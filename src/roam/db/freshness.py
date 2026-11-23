"""Freshness of the index generation used by a query."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import click

from roam.observability import log_swallowed

_CHECK_BUDGET_S = 2.0
_BUDGET_EXCEEDED = "tracked-tree comparison exceeded its time budget"
_QUERY_FRESHNESS_KEY = "roam_query_index_freshness"
_MAINTENANCE_COMMANDS = frozenset({"clean", "doctor", "index", "init", "reset", "watch"})


def _latest_index_manifest(conn: sqlite3.Connection) -> sqlite3.Row | tuple | None:
    return conn.execute(
        "SELECT indexed_at, git_head, git_dirty_hash FROM index_manifest ORDER BY indexed_at DESC, id DESC LIMIT 1"
    ).fetchone()


def _manifest_age(indexed_at: int | None) -> float | None:
    return max(0.0, time.time() - indexed_at) if isinstance(indexed_at, int) and indexed_at > 0 else None


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path.absolute().as_uri() + "?mode=ro", uri=True, timeout=_CHECK_BUDGET_S)


def index_age_seconds(db_path: Path) -> float | None:
    """Age of the latest recorded index run, if available."""
    try:
        if db_path.is_file():
            with closing(_open_readonly(db_path)) as conn:
                row = _latest_index_manifest(conn)
                return _manifest_age(row[0]) if row else None
    except (OSError, sqlite3.Error) as exc:
        log_swallowed("index_freshness:age", exc)
    return None


def format_index_age(age_s: float) -> str:
    for unit, seconds in (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)):
        if age_s >= seconds or seconds == 1:
            break
    count = int(age_s / seconds)
    return f"{count} {unit}{'s' if count != 1 else ''} ago"


def _git_changed_paths(
    root: Path, head: str, *, additions_only: bool, deadline: float
) -> tuple[list[str] | None, str | None]:
    """Count from Git's tracked path set without walking untracked directories."""
    if len(head) not in (40, 64) or any(char not in "0123456789abcdef" for char in head):
        return None, "the index manifest has an invalid Git commit"
    command = ["git", "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--ignore-submodules=dirty"]
    if additions_only:
        # Added tracked paths are in Git's staging index. The snapshot
        # comparison already checks the working-tree paths we indexed.
        command.extend(("--cached", "--diff-filter=A"))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, _BUDGET_EXCEEDED
    try:
        result = subprocess.run([*command, head, "--"], cwd=root, capture_output=True, timeout=remaining, check=False)
    except FileNotFoundError as exc:
        log_swallowed("index_freshness:git_diff", exc)
        return None, "Git executable unavailable"
    except subprocess.TimeoutExpired as exc:
        log_swallowed("index_freshness:git_diff", exc)
        return None, _BUDGET_EXCEEDED
    except OSError as exc:
        log_swallowed("index_freshness:git_diff", exc)
        return None, f"git diff could not run ({type(exc).__name__})"
    if result.returncode != 0:
        return None, f"git diff failed (exit {result.returncode})"
    return [path for path in os.fsdecode(result.stdout).split("\0") if path], None


def _compare_indexed_files(
    conn: sqlite3.Connection, root: Path, added_paths: list[str], deadline: float
) -> tuple[int | None, str | None]:
    """Compare a dirty build with the working-tree snapshot captured by the indexer."""
    from roam.index.incremental import file_matches_index

    newly_tracked = set(added_paths)
    root_path = os.fspath(root)
    changed_count = 0
    try:
        for path, mtime, digest in conn.execute("SELECT path, mtime, hash FROM files"):
            if time.monotonic() >= deadline:
                return None, _BUDGET_EXCEEDED
            newly_tracked.discard(path)
            try:
                matches = file_matches_index(os.path.join(root_path, path), mtime, digest)
            except (FileNotFoundError, NotADirectoryError):
                matches = False
            changed_count += not matches
        for path in newly_tracked:
            if time.monotonic() >= deadline:
                return None, _BUDGET_EXCEEDED
            try:
                os.lstat(os.path.join(root_path, path))
            except (FileNotFoundError, NotADirectoryError):
                continue
            changed_count += 1
    except (OSError, sqlite3.Error) as exc:
        log_swallowed("index_freshness:files", exc)
        return None, f"could not compare indexed files ({type(exc).__name__})"
    return changed_count, None


@dataclass(frozen=True)
class QueryIndexFreshness:
    age_s: float | None
    changed_files: int | None
    reason: str | None = None

    @property
    def state(self) -> str:
        if self.changed_files is None:
            return "unknown"
        return "stale" if self.changed_files > 0 else "fresh"

    @property
    def stale(self) -> bool:
        return self.state == "stale"

    def message(self) -> str:
        if self.changed_files is None:
            return f"Could not check index freshness ({self.reason})."
        age = f"updated {format_index_age(self.age_s)}" if self.age_s is not None else "index age unknown"
        if not self.stale:
            return f"Index is fresh ({age}, 0 changed files)."
        changes = f"{self.changed_files} changed file{'s' if self.changed_files != 1 else ''}"
        return f"Index is stale ({age}, {changes}). Run `roam index` to refresh."


def read_query_index_freshness(conn: sqlite3.Connection, root: Path) -> QueryIndexFreshness:
    deadline = time.monotonic() + _CHECK_BUDGET_S
    try:
        row = _latest_index_manifest(conn)
    except sqlite3.Error as exc:
        log_swallowed("index_freshness:manifest", exc)
        return QueryIndexFreshness(None, None, "could not read the index manifest")
    if row is None:
        return QueryIndexFreshness(None, None, "no index manifest recorded")
    indexed_at, head, dirty_hash = row
    age_s = _manifest_age(indexed_at)
    if not head:
        return QueryIndexFreshness(age_s, None, "the index manifest has no Git commit")
    paths, reason = _git_changed_paths(root, head, additions_only=bool(dirty_hash), deadline=deadline)
    if paths is None:
        return QueryIndexFreshness(age_s, None, reason)
    count, reason = _compare_indexed_files(conn, root, paths, deadline) if dirty_hash else (len(paths), None)
    if time.monotonic() >= deadline:
        return QueryIndexFreshness(age_s, None, _BUDGET_EXCEEDED)
    return QueryIndexFreshness(age_s, count, reason)


def probe_index_freshness(db_path: Path, root: Path) -> QueryIndexFreshness:
    """Use the query check with a read-only connection for diagnostics."""
    try:
        with closing(_open_readonly(db_path)) as conn:
            return read_query_index_freshness(conn, root)
    except (OSError, sqlite3.Error) as exc:
        log_swallowed("index_freshness:open", exc)
        return QueryIndexFreshness(None, None, "could not open the index")


def query_index_freshness() -> QueryIndexFreshness | None:
    """Return the observation captured by this Click invocation."""
    ctx = click.get_current_context(silent=True)
    return ctx.meta.get(_QUERY_FRESHNESS_KEY) if ctx is not None else None


def refuse_stale_index() -> bool:
    return os.environ.get("ROAM_REFUSE_STALE_INDEX") == "1"


class StaleQueryIndexError(click.ClickException):
    """Opt-in refusal of an index with tracked-tree changes."""

    def __init__(self, message: str, *, json_output: bool):
        super().__init__(message)
        self.json_output = json_output


def observe_query_index(conn: sqlite3.Connection, root: Path) -> None:
    """Check the first index read in a command and share it with its envelopes."""
    ctx = click.get_current_context(silent=True)
    if ctx is None or not isinstance(ctx.obj, dict):
        return
    cli_ctx = ctx.find_root()
    command = cli_ctx.invoked_subcommand or ctx.command.name
    if command in _MAINTENANCE_COMMANDS or _QUERY_FRESHNESS_KEY in ctx.meta:
        return
    freshness = read_query_index_freshness(conn, root)
    ctx.meta[_QUERY_FRESHNESS_KEY] = freshness
    if freshness.state == "fresh":
        return
    output_format = ctx.params.get("output_format") or ctx.params.get("fmt")
    json_output = bool(
        ctx.obj.get("json") or ctx.params.get("json_mode") or ctx.params.get("json_output") or output_format == "json"
    )
    if freshness.stale and refuse_stale_index():
        raise StaleQueryIndexError(freshness.message(), json_output=json_output)
    if not (json_output or ctx.obj.get("sarif") or output_format == "sarif"):
        click.echo(f"Warning: {freshness.message()}", err=True)
