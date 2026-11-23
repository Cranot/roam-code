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

from roam.git_utils import GIT_STATUS_WITH_HEAD, git_status_head_and_paths, worktree_git_env
from roam.observability import log_swallowed

_CHECK_BUDGET_S = 0.3
_BUDGET_EXCEEDED = "freshness check exceeded its time budget"
_QUERY_FRESHNESS_KEY = "roam_query_index_freshness"
_MAINTENANCE_COMMANDS = frozenset({"clean", "doctor", "index", "init", "reset", "watch"})


def _latest_index_manifest(conn: sqlite3.Connection) -> sqlite3.Row | tuple | None:
    return conn.execute(
        "SELECT indexed_at, git_head, git_dirty_hash, notes, config_hash "
        "FROM index_manifest ORDER BY indexed_at DESC, id DESC LIMIT 1"
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
    env = worktree_git_env(root)
    if command[1] == "status":
        # A timed-out status must not leave Git's optional index write locked.
        env = {**(env if env is not None else os.environ), "GIT_OPTIONAL_LOCKS": "0"}
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


def _manifest_notes(notes: str | None) -> dict:
    try:
        manifest = json.loads(notes) if notes else None
    except (ValueError, TypeError) as exc:
        log_swallowed("index_freshness:notes", exc)
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _git_paths(output: bytes, *, name_status: bool = False) -> set[str]:
    records = output.decode("utf-8", errors="replace").split("\0")
    if name_status:
        # With rename detection disabled, each status field has one path.
        records = records[1::2]
    return {path.replace("\\", "/") for path in records if path}


def _has_gitignore(paths: set[str]) -> bool:
    return any(path.rsplit("/", 1)[-1] == ".gitignore" for path in paths)


def _indexed_rows(conn: sqlite3.Connection, paths: set[str], *, compare_all: bool):
    select = "SELECT path, language, mtime, hash FROM files"
    if compare_all:
        yield from conn.execute(select)
        return
    candidates = tuple(paths)
    # Keep each lookup below SQLite's legacy host-parameter limit.
    for start in range(0, len(candidates), 500):
        batch = candidates[start : start + 500]
        placeholders = ",".join("?" for _ in batch)
        yield from conn.execute(f"{select} WHERE path IN ({placeholders})", batch)


def _compare_indexed_files(
    conn: sqlite3.Connection,
    root: Path,
    paths: set[str],
    *,
    compare_all: bool,
    dirty: bool,
    include_excluded: bool,
    config_changed: bool,
    deadline: float,
) -> tuple[set[str] | None, str | None]:
    """Filter Git candidates against discovery and the indexed snapshot."""
    if not paths and not compare_all:
        return set(), None

    from roam.index.discovery import MAX_FILE_SIZE, _filter_files, _is_skippable, load_exclude_patterns
    from roam.index.gitignore import compile_exclude_patterns
    from roam.index.incremental import file_hash
    from roam.index.parser import detect_language

    root_path = os.fspath(root)
    changed_paths: set[str] = set()
    try:
        exclude = None if include_excluded else compile_exclude_patterns(load_exclude_patterns(root))
        for path, language, mtime, digest in _indexed_rows(conn, paths, compare_all=compare_all):
            if time.monotonic() >= deadline:
                return None, _BUDGET_EXCEEDED
            listed = path in paths
            paths.discard(path)
            if not language and not detect_language(path):
                continue
            if not dirty and not compare_all:
                # Git identified a change to a path already accepted by discovery.
                changed_paths.add(path)
                continue
            # Git ignore changes can drop a listed path. An indexed path
            # only needs its Roam exclusion rules reapplied if they changed.
            if not listed or _is_skippable(path) or (config_changed and exclude is not None and exclude(path)):
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
            if detect_language(path) and _filter_files([path], root, exclude, include_excluded):
                changed_paths.add(path)
    except (OSError, sqlite3.Error) as exc:
        log_swallowed("index_freshness:files", exc)
        return None, f"could not compare indexed files ({type(exc).__name__})"
    return changed_paths, None


def _candidate_paths(
    root: Path,
    git_root: Path,
    indexed_head: str,
    head: str,
    paths: set[str],
    config_changed: bool,
    dirty_paths: list[str] | None,
    deadline: float,
) -> tuple[set[str] | None, bool, str | None]:
    if indexed_head != head:
        diff, reason = _run_git(
            git_root,
            ["git", "diff", "--name-status", "-z", "--no-renames", "--no-ext-diff", indexed_head, head, "--"],
            deadline,
        )
        if diff is None:
            return None, False, reason
        paths.update(_git_paths(diff, name_status=True))

    if dirty_paths is not None:
        paths.update(dirty_paths)
    # Legacy dirty manifests and builds spanning a HEAD change need a full
    # comparison. Ignore rules can also affect otherwise unchanged files.
    if dirty_paths is not None and not config_changed and not _has_gitignore(paths):
        untracked_ancestor = False
        if root != git_root:
            # Porcelain, diffs and recorded dirty paths are relative to
            # Git's root. Check ancestor ignore changes before rebasing.
            prefix = root.relative_to(git_root).as_posix() + "/"
            untracked_ancestor = any(path.endswith("/") and prefix.startswith(path) for path in paths)
            paths = {path[len(prefix) :] for path in paths if path != prefix and path.startswith(prefix)}

        if not untracked_ancestor and not any(path.endswith("/") for path in paths):
            return paths, False, None
        # Normal porcelain collapses untracked directories. Expanding them
        # can uncover a .gitignore that changes the indexed scope.
        listing, reason = _run_git(root, ["git", "ls-files", "-z", "--others", "--exclude-standard"], deadline)
        if listing is None:
            return None, False, reason
        untracked = _git_paths(listing)
        if not _has_gitignore(untracked):
            paths = {path for path in paths if not path.endswith("/")}
            return paths | untracked, False, None

    from roam.index.discovery import GIT_LS_FILES

    listing, reason = _run_git(root, list(GIT_LS_FILES), deadline)
    return (_git_paths(listing) if listing is not None else None), True, reason


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

    @property
    def git_history_behind(self) -> bool:
        return (
            self.indexed_commit is not None and self.head_commit is not None and self.indexed_commit != self.head_commit
        )

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
    from roam.db.connection import find_project_root
    from roam.index.manifest import _config_hash

    row = _latest_index_manifest(conn)
    age_s = _manifest_age(row[0]) if row else None
    root = root.resolve()
    git_root = find_project_root(root)
    if not (git_root / ".git").exists():
        return QueryIndexFreshness(age_s, None, "project has no Git worktree", skipped=True)
    if row is None:
        return QueryIndexFreshness(None, None, "no index manifest recorded")
    _, indexed_head, dirty_hash, notes, config_hash = row
    if not indexed_head:
        return QueryIndexFreshness(age_s, None, "the index manifest has no Git commit")
    if len(indexed_head) not in (40, 64) or any(char not in "0123456789abcdef" for char in indexed_head):
        return QueryIndexFreshness(age_s, None, "the index manifest has an invalid Git commit")
    status, reason = _run_git(git_root, [*GIT_STATUS_WITH_HEAD, "--untracked-files=normal"], deadline)
    if status is None:
        # A failed worktree scan can still leave HEAD readable.
        raw_head = None
        if reason and reason.startswith("git status"):
            raw_head, _ = _run_git(git_root, ["git", "rev-parse", "HEAD"], deadline)
        head = os.fsdecode(raw_head).strip() if raw_head else None
        return QueryIndexFreshness(age_s, None, reason, indexed_commit=indexed_head, head_commit=head)
    head, status_paths = git_status_head_and_paths(status)
    if head is None:
        return QueryIndexFreshness(age_s, None, "git status did not report a HEAD commit", indexed_commit=indexed_head)
    metadata = _manifest_notes(notes)
    options = metadata.get("index_options")
    include_excluded = isinstance(options, dict) and options.get("include_excluded") is True
    recorded_paths = metadata.get("git_dirty_paths", None if dirty_hash else [])
    if (
        metadata.get("git_build_head_changed")
        or not isinstance(recorded_paths, list)
        or any(not isinstance(path, str) for path in recorded_paths)
    ):
        recorded_paths = None
    config_changed = not include_excluded and _config_hash(root) != config_hash
    paths, compare_all, reason = _candidate_paths(
        root,
        git_root,
        indexed_head,
        head,
        status_paths,
        config_changed,
        recorded_paths,
        deadline,
    )
    changed_paths = None
    if paths is not None:
        changed_paths, reason = _compare_indexed_files(
            conn,
            root,
            paths,
            compare_all=compare_all,
            dirty=bool(dirty_hash),
            include_excluded=include_excluded,
            config_changed=config_changed,
            deadline=deadline,
        )
    if time.monotonic() >= deadline:
        changed_paths, reason = None, _BUDGET_EXCEEDED
    return QueryIndexFreshness(
        age_s,
        len(changed_paths) if changed_paths is not None else None,
        reason,
        indexed_commit=indexed_head,
        head_commit=head,
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


def _git_history_relation(root: Path, indexed: str, head: str) -> str:
    deadline = time.monotonic() + _CHECK_BUDGET_S
    env = worktree_git_env(root)
    for ancestor, descendant, relation in ((indexed, head, "is behind HEAD"), (head, indexed, "is ahead of HEAD")):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "relationship to HEAD is unknown"
        try:
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", ancestor, descendant],
                cwd=root,
                capture_output=True,
                env=env,
                timeout=remaining,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log_swallowed("index_freshness:history", exc)
            return "relationship to HEAD is unknown"
        if result.returncode == 0:
            return relation
        if result.returncode == 128:
            return "is unreachable in the local Git repository"
        if result.returncode != 1:
            return "relationship to HEAD is unknown"
    return "has diverged from HEAD in the available history"


def git_history_hint() -> str | None:
    """Describe how the indexed commit relates to the observed HEAD."""
    freshness = query_index_freshness()
    if freshness and not os.environ.get("ROAM_NO_STALENESS_HINT"):
        indexed, head = freshness.indexed_commit, freshness.head_commit
        if indexed and head and freshness.git_history_behind:
            root, _ = click.get_current_context().meta[_QUERY_FRESHNESS_KEY]
            relation = _git_history_relation(root, indexed, head)
            return f"Indexed Git history {relation} ({indexed[:12]} → {head[:12]}). Run `roam index` to refresh."
    return None


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
