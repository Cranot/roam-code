"""Freshness of the index generation used by a query."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import click

from roam.observability import log_swallowed

_STALE_AFTER_SECONDS = 24 * 60 * 60
_QUERY_FRESHNESS_KEY = "roam_query_index_freshness"
_MAINTENANCE_COMMANDS = frozenset({"doctor", "index", "init", "watch"})


def index_age_seconds(db_path: Path) -> float | None:
    """Age used by doctor, or None when the database cannot be statted."""
    try:
        return max(0.0, time.time() - db_path.stat().st_mtime)
    except FileNotFoundError:
        return None
    except OSError as exc:
        log_swallowed("index_freshness:stat", exc)
        return None


def index_is_stale(age_s: float) -> bool:
    return age_s > _STALE_AFTER_SECONDS


def format_index_age(age_s: float) -> str:
    for unit, seconds in (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)):
        if age_s >= seconds or seconds == 1:
            break
    count = int(age_s / seconds)
    return f"{count} {unit}{'s' if count != 1 else ''} ago"


def _git_changed_paths(root: Path, head: str, *, additions_only: bool) -> list[str] | None:
    """Count from Git's tracked path set without walking untracked directories."""
    if len(head) not in (40, 64) or any(char not in "0123456789abcdef" for char in head):
        return None
    command = ["git", "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--ignore-submodules=dirty"]
    if additions_only:
        # Added tracked paths are in Git's staging index. The snapshot
        # comparison already checks the working-tree paths we indexed.
        command.extend(("--cached", "--diff-filter=A"))
    try:
        result = subprocess.run([*command, head, "--"], cwd=root, capture_output=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_swallowed("index_freshness:git_diff", exc)
        return None
    if result.returncode != 0:
        return None
    return [path for path in os.fsdecode(result.stdout).split("\0") if path]


def _changed_file_count(conn: sqlite3.Connection, root: Path) -> int | None:
    try:
        row = conn.execute(
            "SELECT git_head, git_dirty_hash FROM index_manifest ORDER BY indexed_at DESC, id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error as exc:
        log_swallowed("index_freshness:manifest", exc)
        row = None
    head, dirty_hash = row if row else (None, None)
    changed = _git_changed_paths(root, head, additions_only=bool(dirty_hash)) if head else None
    if changed is not None and not dirty_hash:
        return len(changed)

    # A dirty build's actual inputs can differ from HEAD. Compare against
    # the recorded file snapshot just as the incremental indexer does.
    from roam.index.incremental import file_matches_index

    newly_tracked = set(changed or ())
    root_path = os.fspath(root)
    changed_count = 0
    try:
        for path, mtime, digest in conn.execute("SELECT path, mtime, hash FROM files"):
            newly_tracked.discard(path)
            try:
                matches = file_matches_index(os.path.join(root_path, path), mtime, digest)
            except OSError:
                matches = False
            changed_count += not matches
    except sqlite3.Error as exc:
        log_swallowed("index_freshness:files", exc)
        return None
    return changed_count + sum(os.path.lexists(os.path.join(root_path, path)) for path in newly_tracked)


@dataclass(frozen=True)
class QueryIndexFreshness:
    age_s: float
    changed_files: int | None

    @property
    def stale(self) -> bool:
        return index_is_stale(self.age_s)

    def message(self) -> str:
        age = format_index_age(self.age_s)
        if self.changed_files is None:
            changes = "changed file count unavailable"
        else:
            changes = f"{self.changed_files} changed file{'s' if self.changed_files != 1 else ''}"
        return f"Index is stale (updated {age}, {changes}). Run `roam index` to refresh."


def read_query_index_freshness(
    conn: sqlite3.Connection, db_path: Path, root: Path, *, include_fresh_count: bool = True
) -> QueryIndexFreshness | None:
    age_s = index_age_seconds(db_path)
    if age_s is None:
        return None
    changed_files = _changed_file_count(conn, root) if include_fresh_count or index_is_stale(age_s) else None
    return QueryIndexFreshness(age_s, changed_files)


def query_index_freshness() -> QueryIndexFreshness | None:
    """Return the observation captured by this Click invocation."""
    ctx = click.get_current_context(silent=True)
    return ctx.meta.get(_QUERY_FRESHNESS_KEY) if ctx is not None else None


def refuse_stale_index() -> bool:
    return os.environ.get("ROAM_REFUSE_STALE_INDEX") == "1"


class StaleQueryIndexError(click.ClickException):
    """Opt-in refusal of an age-stale index."""


def observe_query_index(conn: sqlite3.Connection, db_path: Path, root: Path) -> None:
    """Check the first index read in a command and share it with its envelopes."""
    ctx = click.get_current_context(silent=True)
    if ctx is None or not isinstance(ctx.obj, dict):
        return
    cli_ctx = ctx.find_root()
    command = cli_ctx.invoked_subcommand or ctx.command.name
    if command in _MAINTENANCE_COMMANDS or _QUERY_FRESHNESS_KEY in ctx.meta:
        return
    # Fresh text output has no freshness line.
    freshness = read_query_index_freshness(conn, db_path, root, include_fresh_count=bool(ctx.obj.get("json")))
    ctx.meta[_QUERY_FRESHNESS_KEY] = freshness
    if freshness is None or not freshness.stale:
        return
    if refuse_stale_index():
        raise StaleQueryIndexError(freshness.message())
    if not (ctx.obj.get("json") or ctx.obj.get("sarif")):
        click.echo(f"Warning: {freshness.message()}", err=True)
