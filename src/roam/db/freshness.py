"""Freshness of the index generation used by a query."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import click

from roam.observability import log_swallowed

_CHECK_BUDGET_S = 0.3
_BUDGET_EXCEEDED = "freshness check exceeded its time budget"
_QUERY_FRESHNESS_KEY = "roam_query_index_freshness"
_MAINTENANCE_COMMANDS = frozenset({"clean", "doctor", "index", "init", "reset", "watch"})


def _latest_index_manifest(conn: sqlite3.Connection) -> sqlite3.Row | tuple | None:
    return conn.execute(
        "SELECT indexed_at, git_head, notes, config_hash FROM index_manifest ORDER BY indexed_at DESC, id DESC LIMIT 1"
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
                row = conn.execute(
                    "SELECT indexed_at FROM index_manifest ORDER BY indexed_at DESC, id DESC LIMIT 1"
                ).fetchone()
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


def _run_git(root: Path, command: list[str], deadline: float) -> tuple[bytes | None, str | None]:
    from roam.git_utils import worktree_git_env

    env = worktree_git_env(root)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, _BUDGET_EXCEEDED
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, env=env, timeout=remaining, check=False)
    except FileNotFoundError as exc:
        log_swallowed("index_freshness:git", exc)
        return None, "Git executable unavailable"
    except subprocess.TimeoutExpired as exc:
        log_swallowed("index_freshness:git", exc)
        return None, _BUDGET_EXCEEDED
    except OSError as exc:
        log_swallowed("index_freshness:git", exc)
        return None, f"git {command[1]} could not run ({type(exc).__name__})"
    if result.returncode:
        return None, f"git {command[1]} failed (exit {result.returncode})"
    if result.stderr.strip():
        return None, f"git {command[1]} reported an incomplete result"
    return result.stdout, None


def _include_excluded(notes: str | None) -> bool:
    try:
        manifest = json.loads(notes) if notes else None
    except (ValueError, TypeError) as exc:
        log_swallowed("index_freshness:notes", exc)
        return False
    options = manifest.get("index_options") if isinstance(manifest, dict) else None
    return isinstance(options, dict) and options.get("include_excluded") is True


def _compare_indexed_files(
    conn: sqlite3.Connection,
    root: Path,
    listed_paths: bytes,
    include_excluded: bool,
    indexed_config_hash: str | None,
    deadline: float,
) -> tuple[set[str] | None, str | None]:
    """Compare the same indexed source-file snapshot for clean and dirty builds."""
    from roam.index.discovery import (
        MAX_FILE_SIZE,
        _filter_files,
        _is_skippable,
        _matches_exclude,
        load_exclude_patterns,
    )
    from roam.index.incremental import file_hash
    from roam.index.manifest import _config_hash
    from roam.index.parser import detect_language

    paths = {path.replace("\\", "/") for path in listed_paths.decode("utf-8", errors="replace").split("\0") if path}
    root_path = os.fspath(root)
    changed_paths: set[str] = set()
    try:
        exclude_patterns = [] if include_excluded else load_exclude_patterns(root)
        recheck_excludes = exclude_patterns and _config_hash(root) != indexed_config_hash
        for path, language, mtime, digest in conn.execute("SELECT path, language, mtime, hash FROM files"):
            if time.monotonic() >= deadline:
                return None, _BUDGET_EXCEEDED
            listed = path in paths
            paths.discard(path)
            if not (language or detect_language(path)):
                continue
            if not listed or _is_skippable(path) or (recheck_excludes and _matches_exclude(path, exclude_patterns)):
                changed_paths.add(path)
                continue
            try:
                full_path = os.path.join(root_path, path)
                current = os.stat(full_path)
                changed = not stat.S_ISREG(current.st_mode) or current.st_size > MAX_FILE_SIZE
                if not changed and current.st_mtime != mtime:
                    changed = file_hash(full_path) != digest
            except (FileNotFoundError, NotADirectoryError):
                changed = True
            if changed:
                changed_paths.add(path)
        for path in paths:
            if time.monotonic() >= deadline:
                return None, _BUDGET_EXCEEDED
            if detect_language(path) and _filter_files([path], root, exclude_patterns, include_excluded):
                changed_paths.add(path)
    except (OSError, sqlite3.Error) as exc:
        log_swallowed("index_freshness:files", exc)
        return None, f"could not compare indexed files ({type(exc).__name__})"
    return changed_paths, None


@dataclass(frozen=True)
class QueryIndexFreshness:
    age_s: float | None
    changed_files: int | None
    reason: str | None = None
    skipped: bool = False
    indexed_commit: str | None = None
    head_commit: str | None = None
    changed_paths: frozenset[str] = frozenset()

    @property
    def state(self) -> str:
        if self.changed_files is None:
            return "unknown"
        return "stale" if self.changed_files > 0 else "fresh"

    @property
    def stale(self) -> bool:
        return self.state == "stale"

    @property
    def degraded(self) -> bool:
        return self.state != "fresh" and not self.skipped

    def legacy_status(self) -> dict:
        """Compatibility fields from the same source snapshot comparison."""
        return {
            "fresh": None if self.changed_files is None else not self.stale,
            "dirty_files": self.changed_files,
            "indexed_commit": self.indexed_commit[:12] if self.indexed_commit else None,
            "head_commit": self.head_commit[:12] if self.head_commit else None,
            "hint": self.message() if self.degraded else None,
        }

    def text_hint(self) -> str | None:
        return self.message() if self.degraded and not os.environ.get("ROAM_NO_STALENESS_HINT") else None

    def message(self) -> str:
        if self.changed_files is None:
            return f"Could not check index freshness ({self.reason})."
        age = f"updated {format_index_age(self.age_s)}" if self.age_s is not None else "index age unknown"
        if not self.stale:
            return f"Index is fresh ({age}, 0 changed files)."
        changes = f"{self.changed_files} changed file{'s' if self.changed_files != 1 else ''}"
        return f"Index is stale ({age}, {changes}). Run `roam index` to refresh."


def _read_freshness(conn: sqlite3.Connection, root: Path, deadline: float) -> QueryIndexFreshness:
    from roam.index.discovery import GIT_LS_FILES

    row = _latest_index_manifest(conn)
    age_s = _manifest_age(row[0]) if row else None
    if not (root / ".git").exists():
        return QueryIndexFreshness(age_s, None, "project has no Git worktree", skipped=True)
    if row is None:
        return QueryIndexFreshness(None, None, "no index manifest recorded")
    _, indexed_head, notes, config_hash = row
    if not indexed_head:
        return QueryIndexFreshness(age_s, None, "the index manifest has no Git commit")
    paths, reason = _run_git(root, list(GIT_LS_FILES), deadline)
    if paths is None:
        return QueryIndexFreshness(age_s, None, reason, indexed_commit=indexed_head)
    head, reason = _run_git(root, ["git", "rev-parse", "HEAD"], deadline)
    if head is None:
        return QueryIndexFreshness(age_s, None, reason, indexed_commit=indexed_head)
    changed_paths, reason = _compare_indexed_files(conn, root, paths, _include_excluded(notes), config_hash, deadline)
    if time.monotonic() >= deadline:
        changed_paths, reason = None, _BUDGET_EXCEEDED
    return QueryIndexFreshness(
        age_s,
        len(changed_paths) if changed_paths is not None else None,
        reason,
        indexed_commit=indexed_head,
        head_commit=os.fsdecode(head).strip(),
        changed_paths=frozenset(changed_paths or ()),
    )


def read_query_index_freshness(conn: sqlite3.Connection, root: Path) -> QueryIndexFreshness:
    deadline = time.monotonic() + _CHECK_BUDGET_S
    busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.execute("PRAGMA busy_timeout=0")
    try:
        return _read_freshness(conn, root, deadline)
    except (OSError, sqlite3.Error) as exc:
        log_swallowed("index_freshness:manifest", exc)
        reason = _BUDGET_EXCEEDED if time.monotonic() >= deadline else "could not read the index manifest"
        return QueryIndexFreshness(None, None, reason)
    finally:
        conn.execute(f"PRAGMA busy_timeout={busy_timeout}")


def probe_index_freshness(db_path: Path, root: Path) -> QueryIndexFreshness:
    """Use the query check with a read-only connection for diagnostics."""
    try:
        with closing(_open_readonly(db_path)) as conn:
            return read_query_index_freshness(conn, root)
    except (OSError, sqlite3.Error) as exc:
        log_swallowed("index_freshness:open", exc)
        return QueryIndexFreshness(None, None, "could not open the index")


_direct_observations: ContextVar[dict[Path, QueryIndexFreshness] | None] = ContextVar(
    "direct_index_observations", default=None
)


@contextmanager
def capture_direct_index_reads():
    """Capture freshness for an MCP request that opens an index outside Click."""
    observations: dict[Path, QueryIndexFreshness] = {}
    token = _direct_observations.set(observations)
    try:
        yield observations
    finally:
        _direct_observations.reset(token)


def command_is_stale_sensitive(command: str) -> bool:
    from roam.capability import _CAPABILITIES

    capability = _CAPABILITIES.get(command)
    return capability is None or capability.stale_sensitive


def query_is_stale_sensitive(command: str) -> bool:
    ctx = click.get_current_context(silent=True)
    return command_is_stale_sensitive(_invoked_command(ctx) if ctx is not None else command)


def _invoked_command(ctx: click.Context) -> str:
    parent = ctx.find_root()
    return parent.invoked_subcommand or ctx.command.name or ""


def project_index_freshness(root: Path) -> QueryIndexFreshness | None:
    """Observe an existing index and reuse its result within this invocation."""
    from roam.db.connection import StaleDbDirError, get_db_path

    ctx = click.get_current_context(silent=True)
    recorded = ctx.meta.get(_QUERY_FRESHNESS_KEY) if ctx is not None else None
    if recorded is not None and recorded[0] == root:
        return recorded[1]

    try:
        db_path = get_db_path(root, create=False)
        freshness = probe_index_freshness(db_path, root) if db_path.is_file() else None
    except (OSError, StaleDbDirError) as exc:
        log_swallowed("index_freshness:db_path", exc)
        freshness = QueryIndexFreshness(None, None, "could not locate the index")
    if ctx is not None:
        ctx.meta[_QUERY_FRESHNESS_KEY] = (root, freshness)
    return freshness


def query_index_freshness() -> QueryIndexFreshness | None:
    """Use the invocation's observation, or disclose an existing index for metadata commands."""
    ctx = click.get_current_context(silent=True)
    if ctx is None or not isinstance(ctx.obj, dict):
        return None
    recorded = ctx.meta.get(_QUERY_FRESHNESS_KEY)
    if recorded is not None:
        return recorded[1]
    command = _invoked_command(ctx)
    if command in _MAINTENANCE_COMMANDS or command_is_stale_sensitive(command):
        return None
    from roam.db.connection import find_project_root

    return project_index_freshness(find_project_root())


def refuse_stale_index() -> bool:
    return os.environ.get("ROAM_REFUSE_STALE_INDEX") == "1"


class StaleQueryIndexError(click.ClickException):
    """Opt-in refusal of an index with source-file changes."""

    def __init__(self, message: str, *, json_output: bool):
        super().__init__(message)
        self.json_output = json_output


def observe_query_index(conn: sqlite3.Connection, root: Path) -> None:
    """Check the first index read in a command and share it with its envelopes."""
    ctx = click.get_current_context(silent=True)
    if ctx is None or not isinstance(ctx.obj, dict):
        observations = _direct_observations.get()
        if observations is not None and root not in observations:
            observations[root] = read_query_index_freshness(conn, root)
        return
    command = _invoked_command(ctx)
    if command in _MAINTENANCE_COMMANDS or _QUERY_FRESHNESS_KEY in ctx.meta:
        return
    freshness = read_query_index_freshness(conn, root)
    ctx.meta[_QUERY_FRESHNESS_KEY] = (root, freshness)
    if not freshness.degraded:
        return
    output_format = ctx.params.get("output_format") or ctx.params.get("fmt")
    json_output = bool(
        ctx.obj.get("json") or ctx.params.get("json_mode") or ctx.params.get("json_output") or output_format == "json"
    )
    if freshness.stale and command_is_stale_sensitive(command) and refuse_stale_index():
        raise StaleQueryIndexError(freshness.message(), json_output=json_output)
    if not (json_output or ctx.obj.get("sarif") or output_format == "sarif"):
        if hint := freshness.text_hint():
            click.echo(f"Warning: {hint}", err=True)
