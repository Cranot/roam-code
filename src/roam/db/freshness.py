"""Freshness of the index generation used by a query."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

import click
from click.testing import CliRunner

from roam.git_utils import GIT_STATUS_WITH_HEAD, git_status_head_and_paths, project_relative_git_paths, worktree_git_env
from roam.observability import log_swallowed

_BUDGET_ENV = "ROAM_INDEX_FRESHNESS_BUDGET_S"
# Keep configured timeouts representable by subprocess's signed millisecond poll.
_MAX_BUDGET_S = 2_147_483
_BUDGET_EXCEEDED = "freshness check exceeded its time budget"
_NO_GIT_WORKTREE = "project has no Git worktree"
_INDEX_UNAVAILABLE = "the index is unavailable"
_QUERY_FRESHNESS_KEY = "roam_query_index_freshness"
_QUERY_INDEX_READ_KEY = "roam_query_index_read"
_MAINTENANCE_COMMANDS = frozenset({"clean", "doctor", "index", "init", "reset", "watch"})


def _check_budget_seconds() -> float:
    default = 3.0 if sys.platform == "win32" else 1.5
    try:
        seconds = float(os.environ.get(_BUDGET_ENV, default))
    except ValueError:
        return default
    return seconds if 0 < seconds <= _MAX_BUDGET_S else default


class _CheckDeadline:
    def __init__(self) -> None:
        self.budget = _check_budget_seconds()
        self.end = time.monotonic() + self.budget

    def remaining(self) -> float:
        # Floating-point subtraction can round up, even on the first call.
        return min(self.budget, self.end - time.monotonic())


def _latest_index_manifest(conn: sqlite3.Connection) -> sqlite3.Row | tuple | None:
    return conn.execute(
        "SELECT indexed_at, git_head, git_dirty_hash, notes, config_hash "
        "FROM index_manifest ORDER BY indexed_at DESC, id DESC LIMIT 1"
    ).fetchone()


def _manifest_age(indexed_at: int | None) -> float | None:
    return max(0.0, time.time() - indexed_at) if isinstance(indexed_at, int) and indexed_at > 0 else None


def _open_readonly(db_path: Path, *, timeout: float) -> sqlite3.Connection:
    return sqlite3.connect(db_path.absolute().as_uri() + "?mode=ro", uri=True, timeout=timeout)


def index_age_seconds(db_path: Path) -> float | None:
    """Age of the latest recorded index run, if available."""
    try:
        if db_path.is_file():
            with closing(_open_readonly(db_path, timeout=0.3)) as conn:
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


def _run_git(
    root: Path, command: list[str], deadline: _CheckDeadline, *, data: bytes | None = None
) -> tuple[bytes | None, str | None]:
    env = worktree_git_env(root)
    if command[1] == "status":
        # A timed-out status must not leave Git's optional index write locked.
        env = {**(env if env is not None else os.environ), "GIT_OPTIONAL_LOCKS": "0"}
    remaining = deadline.remaining()
    if remaining <= 0:
        return None, _BUDGET_EXCEEDED
    try:
        result = subprocess.run(
            command, cwd=root, capture_output=True, env=env, timeout=remaining, check=False, input=data
        )
    except FileNotFoundError as exc:
        log_swallowed("index_freshness:git", exc)
        return None, "Git executable unavailable"
    except subprocess.TimeoutExpired as exc:
        log_swallowed("index_freshness:git", exc)
        return None, _BUDGET_EXCEEDED
    except OSError as exc:
        log_swallowed("index_freshness:git", exc)
        return None, f"git {command[1]} could not run ({type(exc).__name__})"
    if deadline.remaining() <= 0:
        return None, _BUDGET_EXCEEDED
    no_ignored_paths = command[1] == "check-ignore" and result.returncode == 1 and not result.stdout
    if result.returncode and not no_ignored_paths:
        return None, f"git {command[1]} failed (exit {result.returncode})"
    if result.stderr.strip() or (command[1] == "check-ignore" and not no_ignored_paths and not result.stdout):
        return None, f"git {command[1]} reported an incomplete result"
    return result.stdout, None


def _manifest_notes(notes: str | None) -> dict:
    try:
        manifest = json.loads(notes) if notes else None
    except (ValueError, TypeError) as exc:
        log_swallowed("index_freshness:notes", exc)
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _manifest_dirty_paths(dirty_hash: str | None, metadata: dict) -> list[str] | None:
    paths = metadata.get("git_dirty_paths", None if dirty_hash else [])
    if metadata.get("git_build_head_changed") or not isinstance(paths, list):
        return None
    if any(not isinstance(path, str) or not path or "\0" in path for path in paths):
        return None
    return paths


def _git_paths(output: bytes, *, name_status: bool = False) -> set[str]:
    records = output.decode("utf-8", errors="replace").split("\0")
    if name_status:
        # With rename detection disabled, each status field has one path.
        records = records[1::2]
    return {path.replace("\\", "/") for path in records if path}


def _has_gitignore(paths: set[str]) -> bool:
    return any(path.rsplit("/", 1)[-1] == ".gitignore" for path in paths)


def _indexed_rows(conn: sqlite3.Connection, paths: set[str], *, compare_all: bool):
    select = "SELECT path, hash FROM files"
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
    ignored_paths: set[str],
    include_excluded: bool,
    config_changed: bool,
    deadline: _CheckDeadline,
) -> tuple[set[str] | None, str | None]:
    """Filter Git candidates against discovery and the indexed snapshot."""
    if not paths and not compare_all:
        return set(), None

    from roam.index.discovery import MAX_FILE_SIZE, _filter_files, _is_skippable, load_exclude_patterns
    from roam.index.gitignore import compile_exclude_patterns
    from roam.index.incremental import file_hash

    root_path = os.fspath(root)
    changed_paths: set[str] = set()
    try:
        exclude = None if include_excluded else compile_exclude_patterns(load_exclude_patterns(root))
        for path, digest in _indexed_rows(conn, paths, compare_all=compare_all):
            if time.monotonic() >= deadline.end:
                return None, _BUDGET_EXCEEDED
            listed = path in paths and path not in ignored_paths
            paths.discard(path)
            # Git ignore changes can drop a listed path. An indexed path
            # only needs its Roam exclusion rules reapplied if they changed.
            if not listed or _is_skippable(path) or (config_changed and exclude is not None and exclude(path)):
                changed_paths.add(path)
                continue
            try:
                full_path = os.path.join(root_path, path)
                current = os.stat(full_path)
                changed = not stat.S_ISREG(current.st_mode) or current.st_size > MAX_FILE_SIZE
                if not changed:
                    changed = file_hash(full_path) != digest
            except (FileNotFoundError, NotADirectoryError):
                changed = True
            if changed:
                changed_paths.add(path)
        for path in paths - ignored_paths:
            if time.monotonic() >= deadline.end:
                return None, _BUDGET_EXCEEDED
            # Git can list a submodule directory. The index needs a file it can read.
            if (root / path).is_file() and _filter_files([path], root, exclude, include_excluded):
                changed_paths.add(path)
    except (OSError, sqlite3.Error) as exc:
        log_swallowed("index_freshness:files", exc)
        return None, f"could not compare indexed files ({type(exc).__name__})"
    return changed_paths, None


def _listed_git_paths(root: Path, command: list[str], deadline: _CheckDeadline) -> tuple[set[str] | None, str | None]:
    output, reason = _run_git(root, command, deadline)
    if output is None:
        return None, reason
    if output and not output.endswith(b"\0"):
        return None, "git ls-files reported an incomplete result"
    return _git_paths(output), None


def _recorded_paths_ignored(
    root: Path, paths: set[str], deadline: _CheckDeadline
) -> tuple[set[str] | None, str | None]:
    if not paths:
        return set(), None
    # Git check-ignore skips tracked paths. Prefix relative paths to keep
    # a leading colon literal.
    names = {os.fsencode("./" + path): path for path in paths}
    data = b"\0".join(sorted(names)) + b"\0"
    ignored, reason = _run_git(root, ["git", "check-ignore", "--stdin", "-z"], deadline, data=data)
    if ignored is None:
        return None, reason
    if not ignored:
        return set(), None
    records = set(ignored[:-1].split(b"\0"))
    if not ignored.endswith(b"\0") or not records <= names.keys():
        return None, "git check-ignore reported an incomplete result"
    return {names[record] for record in records}, None


def _git_diff_paths(
    git_root: Path, indexed_head: str, head: str, deadline: _CheckDeadline
) -> tuple[set[str] | None, set[str], str | None]:
    if indexed_head == head:
        return set(), set(), None
    diff, reason = _run_git(
        git_root,
        ["git", "diff", "--name-status", "-z", "--no-renames", "--no-ext-diff", indexed_head, head, "--"],
        deadline,
    )
    if diff is None:
        return None, set(), reason
    fields = diff.split(b"\0")
    changes = list(zip(fields[:-1:2], fields[1::2]))
    if (
        fields[-1]
        or len(fields) % 2 != 1
        or any(kind not in (b"A", b"B", b"D", b"M", b"T", b"U", b"X") or not path for kind, path in changes)
    ):
        return None, set(), "git diff reported an incomplete result"
    deleted = _git_paths(b"\0".join(path for kind, path in changes if kind == b"D"))
    return _git_paths(diff, name_status=True), deleted, None


def _candidate_paths(
    root: Path,
    git_root: Path,
    indexed_head: str,
    head: str,
    status: bytes,
    status_paths: set[str],
    config_changed: bool,
    dirty_paths: list[str] | None,
    deadline: _CheckDeadline,
) -> tuple[set[str] | None, bool, set[str], str | None]:
    diff_paths, committed_deletions, reason = _git_diff_paths(git_root, indexed_head, head, deadline)
    if diff_paths is None:
        return None, False, set(), reason

    prior = set(dirty_paths or ())
    # Ignore rules can affect unchanged paths. A recorded dirty path that
    # disappears from status can also have fallen out of Git's untracked
    # discovery scope via info/exclude or a configured excludes file.
    if dirty_paths is not None and not config_changed and not _has_gitignore(status_paths | diff_paths | prior):
        records = status.split(b"\0")
        staged_deletions = _git_paths(
            b"\0".join(record.split(b" ", 8)[-1] for record in records if record.startswith(b"1 D"))
        )
        untracked = _git_paths(b"\0".join(record[2:] for record in records if record.startswith(b"? ")))
        ignored, reason = _recorded_paths_ignored(root, project_relative_git_paths(prior, root, git_root), deadline)
        if ignored is None:
            return None, False, set(), reason
        # A deletion can leave an eligible untracked file, or a later stage can add it back.
        missing = (committed_deletions - status_paths) | (staged_deletions - untracked)
        ignored.update(project_relative_git_paths(missing, root, git_root))
        current = project_relative_git_paths(status_paths | diff_paths | prior, root, git_root)
        return current, False, ignored, None

    from roam.index.discovery import GIT_LS_FILES

    listing, reason = _listed_git_paths(root, list(GIT_LS_FILES), deadline)
    return listing, True, set(), reason


@dataclass(frozen=True)
class QueryIndexFreshness:
    age_s: float | None
    changed_files: int | None
    reason: str | None = None
    skipped: bool = False
    indexed_commit: str | None = None
    head_commit: str | None = None
    changed_paths: frozenset[str] = frozenset()
    git_history_relation: str | None = None

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
        return self.git_history_relation == "behind"

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


def _is_git_oid(value: object) -> bool:
    return isinstance(value, str) and len(value) in (40, 64) and all(c in "0123456789abcdef" for c in value)


def _read_freshness(conn: sqlite3.Connection, root: Path, deadline: _CheckDeadline) -> QueryIndexFreshness:
    from roam.db.connection import find_project_root
    from roam.index.manifest import _config_hash

    row = _latest_index_manifest(conn)
    age_s = _manifest_age(row[0]) if row else None
    root = root.resolve()
    git_root = find_project_root(root)
    if not (git_root / ".git").exists():
        return QueryIndexFreshness(age_s, None, _NO_GIT_WORKTREE, skipped=True)
    if row is None:
        return QueryIndexFreshness(None, None, "no index manifest recorded")
    _, indexed_head, dirty_hash, notes, config_hash = row
    if not indexed_head:
        return QueryIndexFreshness(age_s, None, "the index manifest has no Git commit")
    if not _is_git_oid(indexed_head):
        return QueryIndexFreshness(age_s, None, "the index manifest has an invalid Git commit")
    status, reason = _run_git(git_root, [*GIT_STATUS_WITH_HEAD, "--untracked-files=all"], deadline)
    if status is None:
        # A failed worktree scan can still leave HEAD readable.
        raw_head = None
        if reason and reason.startswith("git status"):
            raw_head, _ = _run_git(git_root, ["git", "rev-parse", "HEAD"], deadline)
        head = os.fsdecode(raw_head).strip() if raw_head else None
        if not _is_git_oid(head):
            head = None
        relation = (
            _git_history_relation(git_root, indexed_head, head, deadline) if head and head != indexed_head else None
        )
        return QueryIndexFreshness(
            age_s, None, reason, indexed_commit=indexed_head, head_commit=head, git_history_relation=relation
        )
    head, status_paths = git_status_head_and_paths(status)
    if not head or not _is_git_oid(head):
        return QueryIndexFreshness(age_s, None, "git status did not report a HEAD commit", indexed_commit=indexed_head)
    if not status.endswith(b"\0"):
        return QueryIndexFreshness(age_s, None, "git status reported an incomplete result", indexed_commit=indexed_head)
    path_offsets = {b"1 ": 8, b"u ": 10, b"? ": 1}
    for record in status[:-1].split(b"\0"):
        if record.startswith(b"# "):
            continue
        offset = path_offsets.get(record[:2])
        fields = record.split(b" ", offset) if offset is not None else []
        if offset is None or len(fields) != offset + 1 or not fields[-1]:
            return QueryIndexFreshness(
                age_s, None, "git status reported a malformed path record", indexed_commit=indexed_head
            )
    metadata = _manifest_notes(notes)
    options = metadata.get("index_options")
    include_excluded = isinstance(options, dict) and options.get("include_excluded") is True
    recorded_paths = _manifest_dirty_paths(dirty_hash, metadata)
    config_changed = not include_excluded and _config_hash(root) != config_hash
    paths, compare_all, ignored_paths, reason = _candidate_paths(
        root,
        git_root,
        indexed_head,
        head,
        status,
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
            ignored_paths=ignored_paths,
            include_excluded=include_excluded,
            config_changed=config_changed,
            deadline=deadline,
        )
    if time.monotonic() >= deadline.end:
        changed_paths, reason = None, _BUDGET_EXCEEDED
    return QueryIndexFreshness(
        age_s,
        len(changed_paths) if changed_paths is not None else None,
        reason,
        indexed_commit=indexed_head,
        head_commit=head,
        changed_paths=frozenset(changed_paths or ()),
        git_history_relation=_git_history_relation(git_root, indexed_head, head, deadline)
        if indexed_head != head
        else None,
    )


def read_query_index_freshness(conn: sqlite3.Connection, root: Path) -> QueryIndexFreshness:
    deadline = _CheckDeadline()
    busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.execute("PRAGMA busy_timeout=0")
    try:
        return _read_freshness(conn, root, deadline)
    except (OSError, sqlite3.Error) as exc:
        log_swallowed("index_freshness:manifest", exc)
        reason = _BUDGET_EXCEEDED if time.monotonic() >= deadline.end else "could not read the index manifest"
        return QueryIndexFreshness(None, None, reason)
    finally:
        conn.execute(f"PRAGMA busy_timeout={busy_timeout}")


def probe_index_freshness(db_path: Path, root: Path) -> QueryIndexFreshness:
    """Use the query check with a read-only connection for diagnostics."""
    try:
        with closing(_open_readonly(db_path, timeout=0)) as conn:
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


def query_read_index() -> bool:
    """Distinguish CLI index reads from an optional freshness advisory."""
    ctx = click.get_current_context(silent=True)
    # Direct MCP readers and cache refreshes already have a query result.
    return ctx is None or ctx.meta.get(_QUERY_INDEX_READ_KEY, False)


def _invoked_command(ctx: click.Context) -> str:
    parent = ctx.find_root()
    return parent.invoked_subcommand or ctx.command.name or ""


@dataclass
class _QueryIndexObservations:
    # The envelope describes this context's project. Siblings can reuse
    # observations from another project without changing that selection.
    root: Path
    by_root: dict[Path, QueryIndexFreshness | None] = field(default_factory=dict)


def _query_index_observations(ctx: click.Context, root: Path | None = None) -> _QueryIndexObservations:
    observations = ctx.meta.get(_QUERY_FRESHNESS_KEY)
    if observations is None:
        if root is None:
            from roam.db.connection import find_project_root

            root = find_project_root()
        observations = ctx.meta[_QUERY_FRESHNESS_KEY] = _QueryIndexObservations(root)
    return observations


def project_index_freshness(root: Path, *, required: bool = False) -> QueryIndexFreshness | None:
    """Observe an existing index and reuse its result within this invocation."""
    from roam.db.connection import StaleDbDirError, get_db_path

    ctx = click.get_current_context(silent=True)
    if ctx is not None:
        observations = _query_index_observations(ctx, root)
        if freshness := _reuse_query_index(ctx, root, required=required):
            return freshness
        if not required and root in observations.by_root:
            return None

    try:
        db_path = get_db_path(root, create=False)
        freshness = probe_index_freshness(db_path, root) if db_path.is_file() else None
    except (OSError, StaleDbDirError) as exc:
        log_swallowed("index_freshness:db_path", exc)
        freshness = QueryIndexFreshness(None, None, "could not locate the index")
    if freshness is None and required:
        freshness = QueryIndexFreshness(None, None, _INDEX_UNAVAILABLE)
    if ctx is not None:
        if freshness is None:
            observations.by_root[root] = None
        else:
            _record_query_index(ctx, root, freshness)
    return freshness


def query_index_freshness() -> QueryIndexFreshness | None:
    """Use the invocation's observation, or disclose an existing index for metadata commands."""
    ctx = click.get_current_context(silent=True)
    if ctx is None or not isinstance(ctx.obj, dict):
        return None
    observations = ctx.meta.get(_QUERY_FRESHNESS_KEY)
    if observations is not None and observations.root in observations.by_root:
        return observations.by_root[observations.root]
    command = _invoked_command(ctx)
    if command in _MAINTENANCE_COMMANDS or command_is_stale_sensitive(command):
        return None
    return project_index_freshness(_query_index_observations(ctx).root)


def _git_history_relation(root: Path, indexed: str, head: str, deadline: _CheckDeadline) -> str:
    env = worktree_git_env(root)
    for ancestor, descendant, relation in ((indexed, head, "behind"), (head, indexed, "ahead")):
        remaining = deadline.remaining()
        if remaining <= 0:
            return "unknown"
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
            return "unknown"
        if deadline.remaining() <= 0 or result.stdout:
            return "unknown"
        if result.returncode == 0 and not result.stderr:
            return relation
        if result.returncode == 128:
            # Distinguish a missing object from other merge-base failures.
            for commit in (indexed, head):
                output, reason = _run_git(
                    root, ["git", "cat-file", "--batch-check"], deadline, data=f"{commit}\n".encode()
                )
                if reason is None and output.splitlines() == [f"{commit} missing".encode()]:
                    return "unreachable"
            return "unknown"
        if result.returncode != 1 or result.stderr:
            return "unknown"
    shallow, _ = _run_git(root, ["git", "rev-parse", "--is-shallow-repository"], deadline)
    return "diverged" if shallow and shallow.strip() == b"false" else "unknown"


def git_history_hint() -> str | None:
    """Describe how the indexed commit relates to the observed HEAD."""
    freshness = query_index_freshness()
    if freshness and freshness.git_history_relation and not os.environ.get("ROAM_NO_STALENESS_HINT"):
        relation = {
            "behind": "is behind HEAD",
            "ahead": "is ahead of HEAD",
            "diverged": "has diverged from HEAD in the available history",
            "unreachable": "is unreachable in the local Git repository",
            "unknown": "relationship to HEAD is unknown",
        }[freshness.git_history_relation]
        indexed = freshness.indexed_commit[:12]
        head = freshness.head_commit[:12]
        return f"Indexed Git history {relation} ({indexed} → {head}). Run `roam index` to refresh."
    return None


def refuse_stale_index() -> bool:
    return os.environ.get("ROAM_REFUSE_STALE_INDEX") == "1"


class StaleQueryIndexError(click.ClickException):
    """Opt-in refusal of an index with source-file changes."""

    def __init__(self, message: str, *, json_output: bool):
        super().__init__(message)
        self.json_output = json_output


_composite_contexts: ContextVar[tuple[click.Context, ...]] = ContextVar("index_composite_contexts", default=())
_FRESHNESS_PRIORITY = {"fresh": 0, "unknown": 1, "stale": 2}


def freshness_priority(state: str, *, skipped: bool = False) -> int:
    """Prefer evidence of staleness, then unavailable checks, over a fresh read."""
    return _FRESHNESS_PRIORITY.get(state, -1) if not skipped else -1


def valid_index_freshness_metadata(meta: object) -> bool:
    """Check the state and count before reusing a serialized observation."""
    if not isinstance(meta, dict) or "index_changed_files" not in meta:
        return False
    state, count = meta.get("index_state"), meta["index_changed_files"]
    if state == "unknown":
        reason = meta.get("index_state_reason")
        return count is None and isinstance(reason, str) and bool(reason)
    return type(count) is int and ((state == "fresh" and count == 0) or (state == "stale" and count > 0))


def strongest_index_metadata(results: list[dict]) -> tuple[dict, bool]:
    """Copy the strongest index observation and indicate whether it was degraded."""
    selected: dict = {}
    best = -2
    for result in results:
        meta = result.get("_meta") if isinstance(result, dict) else None
        if not isinstance(meta, dict) or not {"index_state", "index_changed_files", "stale_index"} & meta.keys():
            continue
        if not valid_index_freshness_metadata(meta):
            meta = {
                "index_state": "unknown",
                "index_changed_files": None,
                "index_state_reason": "a child reported invalid index freshness metadata",
            }
        skipped = meta["index_state"] == "unknown" and meta.get("index_state_reason") == _NO_GIT_WORKTREE
        rank = freshness_priority(meta["index_state"], skipped=skipped)
        if rank > best:
            selected = meta
            best = rank
    return {
        key: value
        for key, value in selected.items()
        if key.startswith(("index_", "git_history_")) or key == "stale_index"
    }, best > 0


def inside_index_composite() -> bool:
    """True for a CLI child whose caller reports freshness at the composite level."""
    return bool(_composite_contexts.get())


class IndexCompositeRunner(CliRunner):
    """Run a CLI child with the calling command's index observation."""

    def invoke(self, cli, args=None, **kwargs):
        parent = click.get_current_context(silent=True)
        if parent is None or not isinstance(parent.obj, dict):
            return super().invoke(cli, args, **kwargs)

        observations = _query_index_observations(parent)
        before = observations.by_root.get(observations.root)
        ancestors = _composite_contexts.get()
        token = _composite_contexts.set((*ancestors, parent))
        try:
            result = super().invoke(cli, args, **kwargs)
        finally:
            _composite_contexts.reset(token)

        after = observations.by_root.get(observations.root)
        if after is not None:
            check_query_index_policy(parent, after, warn=not ancestors and after is not before)
        return result


def _reuse_query_index(ctx: click.Context, root: Path, *, required: bool) -> QueryIndexFreshness | None:
    """Copy the first observation for this project into the reading context."""
    for query in (ctx, *_composite_contexts.get()):
        observations = query.meta.get(_QUERY_FRESHNESS_KEY)
        recorded = observations.by_root.get(root) if observations is not None else None
        if recorded is not None and (not required or recorded.reason != _INDEX_UNAVAILABLE):
            _record_query_index(ctx, root, recorded)
            return recorded
    return None


def _record_query_index(ctx: click.Context, root: Path, freshness: QueryIndexFreshness) -> None:
    _query_index_observations(ctx, root).by_root[root] = freshness
    rank = freshness_priority(freshness.state, skipped=freshness.skipped)
    for parent in _composite_contexts.get():
        observations = _query_index_observations(parent)
        previous = observations.by_root.get(root)
        if previous is not None and (previous.reason != _INDEX_UNAVAILABLE or freshness.reason == _INDEX_UNAVAILABLE):
            if freshness_priority(previous.state, skipped=previous.skipped) >= rank:
                continue
        observations.by_root[root] = freshness


def check_query_index_policy(ctx: click.Context, freshness: QueryIndexFreshness, *, warn: bool) -> None:
    if not freshness.degraded:
        return
    output_format = ctx.params.get("output_format") or ctx.params.get("fmt")
    json_output = bool(
        ctx.obj.get("json") or ctx.params.get("json_mode") or ctx.params.get("json_output") or output_format == "json"
    )
    if freshness.stale and command_is_stale_sensitive(_invoked_command(ctx)) and refuse_stale_index():
        raise StaleQueryIndexError(freshness.message(), json_output=json_output)
    if warn and not (json_output or ctx.obj.get("sarif") or output_format == "sarif"):
        if hint := freshness.text_hint():
            click.echo(f"Warning: {hint}", err=True)


def observe_query_index(conn: sqlite3.Connection, root: Path) -> None:
    """Check the first index read in a command and share it with its envelopes."""
    ctx = click.get_current_context(silent=True)
    if ctx is None or not isinstance(ctx.obj, dict):
        observations = _direct_observations.get()
        if observations is not None and root not in observations:
            observations[root] = read_query_index_freshness(conn, root)
        return
    if _invoked_command(ctx) in _MAINTENANCE_COMMANDS:
        return
    observations = _query_index_observations(ctx, root)
    if observations.root == root:
        ctx.meta[_QUERY_INDEX_READ_KEY] = True
    parents = tuple(parent for parent in _composite_contexts.get() if _query_index_observations(parent).root == root)
    for parent in parents:
        parent.meta[_QUERY_INDEX_READ_KEY] = True
    recorded = observations.by_root.get(root)
    if recorded is not None and recorded.reason != _INDEX_UNAVAILABLE:
        return
    freshness = _reuse_query_index(ctx, root, required=True)
    if freshness is None:
        freshness = read_query_index_freshness(conn, root)
        _record_query_index(ctx, root, freshness)
    # Refuse before any child consumes stale data, even if only its
    # enclosing command is stale-sensitive.
    if freshness.stale and parents and refuse_stale_index():
        if any(command_is_stale_sensitive(_invoked_command(parent)) for parent in parents):
            raise StaleQueryIndexError(freshness.message(), json_output=True)
    check_query_index_policy(ctx, freshness, warn=not inside_index_composite())
