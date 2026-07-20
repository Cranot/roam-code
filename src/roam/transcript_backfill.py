"""Privacy-preserving historical episode extraction from agent transcripts.

The extractor reads raw Claude Code or Codex JSONL locally and emits a compact
derived snapshot. Raw prompts, responses, command values, paths, and tool
arguments never enter the snapshot. Sanitized shell templates retain the
closed executable/subcommand/flag categories and control-flow shape. Historical episodes
are discovery evidence only; they cannot satisfy the prospective measurement
gate in :mod:`roam.savings`.
"""

from __future__ import annotations

import hashlib
import heapq
import hmac
import json
import os
import re
import shlex
import stat
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from roam.atomic_io import atomic_write_bytes
from roam.observability import log_swallowed
from roam.security.bounded_json import loads_bounded, strict_json_object_pairs
from roam.security.owner_only import (
    create_owner_only_directory,
    ensure_owner_only_file_descriptor,
    ensure_owner_only_path,
    path_is_owner_only,
    pinned_owner_only_directory,
)

BACKFILL_VERSION = 6
MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024
MAX_TRANSCRIPT_LINE_BYTES = 2 * 1024 * 1024
MAX_TRANSCRIPT_ROWS_PER_FILE = 20_000
MAX_TRANSCRIPT_EVENTS_PER_FILE = 10_000
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_EVENTS = 50_000
DEFAULT_MAX_TRANSCRIPT_FILES = 2_000
MAX_TRANSCRIPT_FILES_PER_SOURCE = 10_000
MAX_TOTAL_TRANSCRIPT_FILES = 10_000
MAX_TRANSCRIPT_SOURCES = 16
MAX_TRANSCRIPT_DIRECTORIES = 100_000
MAX_TRANSCRIPT_DIRECTORY_ENTRIES = 1_000_000
MAX_TRANSCRIPT_AGGREGATE_BYTES = 512 * 1024 * 1024
MAX_TRANSCRIPT_AGGREGATE_ROWS = 1_000_000
MAX_TRANSCRIPT_ELAPSED_SECONDS = 120.0
MAX_TOKEN_COUNT = 1_000_000_000_000
MAX_DURATION_MS = 366 * 24 * 60 * 60 * 1000
OUTPUT_NAME = "transcript-episodes.jsonl"
SALT_NAME = "savings-backfill.key"
_MAX_KEY_BYTES = 129

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./\\:-]+")
_CORRECTION_RE = re.compile(
    r"^\s*(no\b|stop\b|wait\b|actually\b|don'?t\b|instead\b|not that|"
    r"that'?s wrong|revert|undo|why did|you (missed|forgot|broke)|try again)",
    re.IGNORECASE,
)
_SYSTEM_BLOCK_RE = re.compile(
    r"<(system-reminder|local-command-caveat|command-name|command-message)>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_VERIFY_RE = re.compile(
    r"\b(pytest|unittest|jest|vitest|mocha|cargo\s+test|go\s+test|dotnet\s+test|"
    r"mvn\s+test|gradle\s+test|npm\s+(run\s+)?test|pnpm\s+(run\s+)?test|"
    r"yarn\s+test|ruff|eslint|mypy|pyright|tsc|typecheck|lint|check\.py|"
    r"roam\s+verify|prepush|pre-commit)\b",
    re.IGNORECASE,
)
_BUILD_RE = re.compile(
    r"\b(npm|pnpm|yarn)\s+(run\s+)?build\b|\b(cargo|go|mvn|gradle|dotnet)\s+build\b",
    re.IGNORECASE,
)
_SEARCH_RE = re.compile(r"\b(rg|grep|git\s+grep|findstr|select-string)\b", re.IGNORECASE)
_FORMAT_RE = re.compile(r"\b(black|prettier|ruff\s+format|gofmt|rustfmt)\b", re.IGNORECASE)
_INSPECT_RE = re.compile(
    r"\b(sed\s+-n|nl\s+-ba|head\b|tail\b|cat\b|less\b|more\b|"
    r"get-content\b|select-object\s+-(?:first|last|skip))",
    re.IGNORECASE,
)
_ORIENT_RE = re.compile(
    r"\b(pwd|tree|git\s+(?:status|branch|log|rev-parse|remote)|"
    r"get-location|get-childitem|ls|dir)\b",
    re.IGNORECASE,
)
_DIFF_RE = re.compile(r"\b(git\s+diff|roam\s+(?:diff|critique))\b", re.IGNORECASE)
_VCS_WRITE_RE = re.compile(
    r"\bgit\s+(?:add|commit|push|merge|rebase|cherry-pick|tag)\b",
    re.IGNORECASE,
)
_DEPLOY_RE = re.compile(
    r"\b(kubectl|helm|terraform|ansible|systemctl|docker\s+(?:compose|run|build|push)|"
    r"ssh|scp|rsync)\b",
    re.IGNORECASE,
)
_DEPENDENCY_RE = re.compile(
    r"\b(pip|pip3|uv|poetry|npm|pnpm|yarn|cargo|go)\s+"
    r"(?:install|add|remove|update|get|mod|sync)\b",
    re.IGNORECASE,
)
_PROJECTION_RE = re.compile(
    r"(?:\|\s*(?:jq|python(?:3)?|head|tail|sed|awk|cut|select-object)\b|head\s+-c\b)",
    re.IGNORECASE,
)
_STRUCTURED_PROJECTION_RE = re.compile(
    r"(?:^|(?:&&|\|\||;)\s*)roam\b[^;]*"
    r"(?:\|\s*(?:jq|python(?:3)?|head|tail|sed|awk|cut|select-object)\b|head\s+-c\b)",
    re.IGNORECASE,
)
_SLICE_TEMPLATE_RE = re.compile(
    r"\b(?:sed\s+-n|nl\s+-ba|head\b|tail\b|get-content\b|"
    r"select-object\s+-(?:first|last|skip))",
    re.IGNORECASE,
)
_HELP_RE = re.compile(r"(?:^|\s)--help(?:\s|$)|(?:^|\s)-h(?:\s|$)", re.IGNORECASE)
_EXIT_CODE_RE = re.compile(
    r"(?:process exited with code|exit code|return code)\s*[:=]?\s*(-?\d+)",
    re.I,
)
_FAILURE_CLASSIFIERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "invalid_invocation",
        re.compile(
            r"\b(unrecognized arguments?|unknown option|invalid option|no such option|"
            r"unexpected argument|usage error)\b",
            re.I,
        ),
    ),
    (
        "command_unavailable",
        re.compile(
            r"\b(command not found|not recognized as an internal or external command|"
            r"executable file not found|could not find executable)\b",
            re.I,
        ),
    ),
    (
        "dependency_unavailable",
        re.compile(
            r"\b(modulenotfounderror|no module named|cannot find module|"
            r"package .* not found|missing dependency|could not import)\b",
            re.I,
        ),
    ),
    (
        "path_unavailable",
        re.compile(
            r"\b(no such file or directory|file not found|path not found|"
            r"cannot find the (?:file|path)|does not exist)\b",
            re.I,
        ),
    ),
    (
        "permission_or_auth",
        re.compile(
            r"\b(permission denied|access denied|unauthorized|forbidden|"
            r"authentication failed|not authorized)\b",
            re.I,
        ),
    ),
    (
        "timeout",
        re.compile(
            r"\b(timed? out|timeout expired|deadline exceeded)\b",
            re.I,
        ),
    ),
    (
        "network",
        re.compile(
            r"\b(connection refused|network is unreachable|could not resolve|"
            r"name resolution|dns failure|connection reset)\b",
            re.I,
        ),
    ),
    (
        "resource_exhausted",
        re.compile(
            r"\b(no space left|out of memory|memoryerror|too many open files|"
            r"resource temporarily unavailable)\b",
            re.I,
        ),
    ),
    (
        "state_conflict",
        re.compile(
            r"\b(merge conflict|would be overwritten|index\.lock|already exists|"
            r"non-fast-forward|working tree.*dirty)\b",
            re.I,
        ),
    ),
    (
        "syntax_or_compile",
        re.compile(
            r"\b(syntaxerror|syntax error|parse error|compilation failed|"
            r"compile error|typecheck failed|type error)\b",
            re.I,
        ),
    ),
    (
        "test_failure",
        re.compile(
            r"(?:\b(?:assertionerror|assertion failed|tests? failed|"
            r"failures?:\s*[1-9])\b|\b[1-9]\d* failed(?:,|\s|$))",
            re.I,
        ),
    ),
)
_URL_RE = re.compile(r"^(?:https?|ssh|git)://", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?:^|[_-])(token|secret|password|passwd|api[_-]?key|private[_-]?key|authorization)(?:$|[_-])",
    re.IGNORECASE,
)
_SAFE_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.+-]{0,39}$")
_PATHISH_RE = re.compile(
    r"(?:[/\\]|^\.+$|\.(?:py|js|ts|tsx|jsx|go|rs|java|cs|php|rb|sh|ps1|json|ya?ml|toml|md|txt|sql)$)", re.I
)
_KNOWN_SUBCOMMAND_EXECUTABLES = frozenset(
    {
        "git",
        "roam",
        "npm",
        "pnpm",
        "yarn",
        "cargo",
        "go",
        "docker",
        "kubectl",
        "systemctl",
        "journalctl",
        "pip",
        "pip3",
    }
)
_SAFE_EXECUTABLES = frozenset(
    {
        "ansible",
        "awk",
        "black",
        "cargo",
        "cat",
        "cd",
        "cmd",
        "cut",
        "dir",
        "docker",
        "dotnet",
        "find",
        "findstr",
        "get-childitem",
        "get-content",
        "get-location",
        "git",
        "go",
        "gofmt",
        "gradle",
        "grep",
        "head",
        "helm",
        "java",
        "journalctl",
        "jq",
        "kubectl",
        "less",
        "ls",
        "make",
        "mvn",
        "mypy",
        "nl",
        "node",
        "npm",
        "npx",
        "pnpm",
        "poetry",
        "powershell",
        "pwsh",
        "py",
        "pyright",
        "pytest",
        "pip",
        "pip3",
        "python",
        "python3",
        "rg",
        "roam",
        "rsync",
        "ruff",
        "rustfmt",
        "scp",
        "sed",
        "select-object",
        "select-string",
        "ssh",
        "systemctl",
        "tail",
        "terraform",
        "tree",
        "tsc",
        "uv",
        "vitest",
        "where",
        "yarn",
    }
)
_SAFE_SUBCOMMANDS_BY_EXECUTABLE: dict[str, frozenset[str]] = {
    "cargo": frozenset({"build", "check", "clippy", "fmt", "test"}),
    "docker": frozenset({"build", "compose", "inspect", "logs", "ps", "pull", "push", "run"}),
    "dotnet": frozenset({"build", "format", "restore", "test"}),
    "git": frozenset(
        {
            "add",
            "branch",
            "cherry-pick",
            "commit",
            "diff",
            "fetch",
            "grep",
            "log",
            "merge",
            "pull",
            "push",
            "rebase",
            "remote",
            "rev-parse",
            "show",
            "status",
            "tag",
        }
    ),
    "go": frozenset({"build", "fmt", "get", "mod", "test", "vet"}),
    "kubectl": frozenset({"apply", "describe", "diff", "get", "logs", "rollout"}),
    "npm": frozenset({"add", "audit", "ci", "install", "remove", "run", "test", "update"}),
    "pip": frozenset({"check", "install", "list", "show", "uninstall"}),
    "pip3": frozenset({"check", "install", "list", "show", "uninstall"}),
    "pnpm": frozenset({"add", "audit", "install", "remove", "run", "test", "update"}),
    "roam": frozenset(
        {
            "compile",
            "compile-stats",
            "context",
            "critique",
            "diff",
            "health",
            "impact",
            "init",
            "preflight",
            "retrieve",
            "savings",
            "understand",
            "uses",
            "verify",
        }
    ),
    "systemctl": frozenset({"daemon-reload", "disable", "enable", "restart", "start", "status", "stop"}),
    "yarn": frozenset({"add", "audit", "install", "remove", "run", "test", "upgrade"}),
}
_SAFE_FLAGS = frozenset(
    {
        "-a",
        "-c",
        "-e",
        "-f",
        "-h",
        "-i",
        "-l",
        "-m",
        "-n",
        "-o",
        "-p",
        "-q",
        "-r",
        "-s",
        "-v",
        "-x",
        "--all",
        "--cached",
        "--changed",
        "--check",
        "--command",
        "--dry-run",
        "--eval",
        "--exclude",
        "--filter",
        "--format",
        "--glob",
        "--help",
        "--include",
        "--json",
        "--maxdepth",
        "--mindepth",
        "--name",
        "--no-cache",
        "--output",
        "--pattern",
        "--quiet",
        "--recursive",
        "--root",
        "--select",
        "--short",
        "--stat",
        "--verbose",
        "--version",
    }
)
_FLAGS_WITH_VALUES = frozenset(
    {
        "-c",
        "-e",
        "-f",
        "-m",
        "-o",
        "-name",
        "-path",
        "-maxdepth",
        "-mindepth",
        "--command",
        "--eval",
        "--filter",
        "--format",
        "--glob",
        "--include",
        "--name",
        "--output",
        "--pattern",
        "--root",
        "--select",
    }
)

_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"})

_INTENT_ARCHETYPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("debug", re.compile(r"\b(debug|diagnos|root cause|why (?:is|does|did)|broken|failure|error)\b", re.I)),
    ("implement", re.compile(r"\b(implement|build|create|add|ship|develop|make)\b", re.I)),
    ("refactor", re.compile(r"\b(refactor|simplif|clean up|restructur|extract|rename)\b", re.I)),
    ("review", re.compile(r"\b(review|audit|critique|inspect|check the diff|code review)\b", re.I)),
    ("verify", re.compile(r"\b(test|verify|validate|lint|typecheck|prove|regression)\b", re.I)),
    ("performance", re.compile(r"\b(performance|latency|speed|optimi[sz]|memory|cpu|faster)\b", re.I)),
    ("security", re.compile(r"\b(security|vulnerab|secret|auth|permission|threat|exploit)\b", re.I)),
    ("deploy", re.compile(r"\b(deploy|release|production|server|service|container|kubernetes)\b", re.I)),
    ("research", re.compile(r"\b(research|compare|investigate|look into|find out|explore)\b", re.I)),
    ("document", re.compile(r"\b(document|readme|docs|guide|explain|write up)\b", re.I)),
    ("plan", re.compile(r"\b(plan|design|architecture|strategy|roadmap|approach)\b", re.I)),
    ("data", re.compile(r"\b(data|database|sql|query|migration|schema|analytics)\b", re.I)),
    ("ui", re.compile(r"\b(ui|ux|frontend|layout|screen|page|component|style|css)\b", re.I)),
    ("git", re.compile(r"\b(git|commit|branch|merge|rebase|pull request|\bpr\b)\b", re.I)),
)


def _parse_ts(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _iso(value: datetime | None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def _bucket(value: int, width: int) -> int:
    return max(width, ((max(0, value) + width - 1) // width) * width)


class TranscriptBackfillSafetyError(ValueError):
    """A transcript or private-state path failed a containment check."""


def _is_reparse_point(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _same_private_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    return bool(
        (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)
        and left.st_mode == right.st_mode
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and (os.name == "nt" or left.st_ctime_ns == right.st_ctime_ns)
    )


def _private_state_directory(root: Path, *, create: bool) -> Path:
    """Return a concrete, owner-controlled ``.roam`` directory."""

    try:
        root_info = os.lstat(root)
    except OSError as exc:
        raise TranscriptBackfillSafetyError(f"project root is unavailable: {root}: {exc}") from exc
    if not stat.S_ISDIR(root_info.st_mode) or _is_reparse_point(root_info):
        raise TranscriptBackfillSafetyError(f"project root must be a concrete directory: {root}")

    state = root / ".roam"
    try:
        state_info = os.lstat(state)
    except FileNotFoundError:
        if not create:
            return state
        if not create_owner_only_directory(state):
            # A concurrent creator may have won between lstat and creation.
            try:
                state_info = os.lstat(state)
            except OSError as exc:
                raise TranscriptBackfillSafetyError(
                    f"private state directory could not be created owner-only: {state}: {exc}"
                ) from exc
        else:
            try:
                state_info = os.lstat(state)
            except OSError as exc:
                raise TranscriptBackfillSafetyError(
                    f"private state directory disappeared during creation: {state}: {exc}"
                ) from exc
    except OSError as exc:
        raise TranscriptBackfillSafetyError(f"private state directory is unavailable: {state}: {exc}") from exc
    if (
        not stat.S_ISDIR(state_info.st_mode)
        or stat.S_ISLNK(state_info.st_mode)
        or _is_reparse_point(state_info)
        or os.path.normcase(str(state.resolve(strict=True))) != os.path.normcase(str(state))
    ):
        raise TranscriptBackfillSafetyError(f"private state directory must not be redirected: {state}")
    if os.name == "nt":
        # Check pre-existing sensitive children before changing the parent
        # DACL. Installing inheritable ACEs can safely tighten unprotected
        # children, but silently repairing a key or snapshot would hide that
        # it had already been exposed under a broader policy.
        for child, label, max_bytes in (
            (state / SALT_NAME, "savings backfill key", _MAX_KEY_BYTES),
            (state / OUTPUT_NAME, "derived transcript snapshot", MAX_SNAPSHOT_BYTES),
        ):
            if os.path.lexists(child):
                _private_file_state(
                    child,
                    label=label,
                    max_bytes=max_bytes,
                    allow_missing=False,
                )
        if not ensure_owner_only_path(state):
            raise TranscriptBackfillSafetyError(
                f"private state directory could not be restricted to the current user: {state}"
            )
        try:
            secured_state_info = os.lstat(state)
        except OSError as exc:
            raise TranscriptBackfillSafetyError(
                f"private state directory changed while being secured: {state}: {exc}"
            ) from exc
        if (state_info.st_dev, state_info.st_ino) != (
            secured_state_info.st_dev,
            secured_state_info.st_ino,
        ):
            raise TranscriptBackfillSafetyError(f"private state directory changed while being secured: {state}")
    else:
        if state_info.st_uid != os.geteuid():
            raise TranscriptBackfillSafetyError(f"private state directory is not owned by this user: {state}")
        if stat.S_IMODE(state_info.st_mode) & 0o022:
            raise TranscriptBackfillSafetyError(f"private state directory is group/world writable: {state}")
        # ``roam init`` historically created ``.roam`` through an ordinary
        # ``mkdir`` call, so the common umask-derived mode is 0755.  That is
        # safe to tighten when the directory is concrete, current-user-owned,
        # and not writable by anyone else.  Do it before callers pin the
        # directory: the pin deliberately requires an exact owner-only mode.
        if stat.S_IMODE(state_info.st_mode) & 0o077:
            identity = (state_info.st_dev, state_info.st_ino)
            try:
                os.chmod(state, 0o700, follow_symlinks=False)
                secured_state_info = os.lstat(state)
            except OSError as exc:
                raise TranscriptBackfillSafetyError(
                    f"private state directory could not be restricted to the current user: {state}: {exc}"
                ) from exc
            if (
                (secured_state_info.st_dev, secured_state_info.st_ino) != identity
                or not stat.S_ISDIR(secured_state_info.st_mode)
                or stat.S_ISLNK(secured_state_info.st_mode)
                or secured_state_info.st_uid != os.geteuid()
                or stat.S_IMODE(secured_state_info.st_mode) & 0o077
            ):
                raise TranscriptBackfillSafetyError(f"private state directory changed while being secured: {state}")
    return state


def _private_file_state(path: Path, *, label: str, max_bytes: int, allow_missing: bool) -> os.stat_result | None:
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise TranscriptBackfillSafetyError(f"{label} is missing: {path}") from None
    except OSError as exc:
        raise TranscriptBackfillSafetyError(f"cannot inspect {label}: {path}: {exc}") from exc
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode) or _is_reparse_point(value):
        raise TranscriptBackfillSafetyError(f"{label} must be a regular, non-linked file: {path}")
    if value.st_nlink != 1:
        raise TranscriptBackfillSafetyError(f"{label} must not be hard-linked: {path}")
    if value.st_size > max_bytes:
        raise TranscriptBackfillSafetyError(f"{label} exceeds the {max_bytes}-byte limit: {path}")
    if os.name == "nt":
        if not path_is_owner_only(path):
            raise TranscriptBackfillSafetyError(f"{label} is not owner-only: {path}")
    else:
        if value.st_uid != os.geteuid():
            raise TranscriptBackfillSafetyError(f"{label} is not owned by this user: {path}")
        if stat.S_IMODE(value.st_mode) & 0o077:
            raise TranscriptBackfillSafetyError(f"{label} is not owner-only: {path}")
    return value


def _read_private_key(path: Path, *, transient_retries: int = 0) -> bytes | None:
    """Read a stable key, tolerating only a bounded concurrent-create window."""

    for attempt in range(transient_retries + 1):
        try:
            before = _private_file_state(
                path,
                label="savings backfill key",
                max_bytes=_MAX_KEY_BYTES,
                allow_missing=True,
            )
            if before is None:
                return None
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            try:
                descriptor = os.open(path, flags)
            except OSError as exc:
                raise TranscriptBackfillSafetyError(f"cannot open savings backfill key: {path}: {exc}") from exc
            try:
                opened = os.fstat(descriptor)
                if not _same_private_file_state(before, opened):
                    raise TranscriptBackfillSafetyError(f"savings backfill key changed before reading: {path}")
                payload = os.read(descriptor, _MAX_KEY_BYTES + 1)
                opened_after = os.fstat(descriptor)
                after = _private_file_state(
                    path,
                    label="savings backfill key",
                    max_bytes=_MAX_KEY_BYTES,
                    allow_missing=False,
                )
                if not _same_private_file_state(opened, opened_after) or not _same_private_file_state(before, after):
                    raise TranscriptBackfillSafetyError(f"savings backfill key changed while reading: {path}")
            finally:
                os.close(descriptor)
            try:
                key = bytes.fromhex(payload.decode("ascii").strip())
            except (UnicodeDecodeError, ValueError) as exc:
                raise TranscriptBackfillSafetyError(f"savings backfill key is malformed: {path}") from exc
            if len(key) < 16:
                raise TranscriptBackfillSafetyError(f"savings backfill key is too short: {path}")
            return key
        except TranscriptBackfillSafetyError:
            if attempt >= transient_retries:
                raise
            time.sleep(0.01)
    raise AssertionError("bounded key-read loop did not terminate")  # pragma: no cover


def _create_private_key(path: Path) -> bytes:
    key = os.urandom(32)
    payload = key.hex().encode("ascii") + b"\n"

    def prepare_private_temp(descriptor: int, temporary: str) -> None:
        if not ensure_owner_only_file_descriptor(descriptor, temporary):
            raise TranscriptBackfillSafetyError(
                f"savings backfill key tempfile was not created for the current user only: {temporary}"
            )

    try:
        atomic_write_bytes(
            path,
            payload,
            prepare_temp_fd=prepare_private_temp,
            durable=True,
            create_parents=False,
            secure_parent=True,
            require_absent=True,
        )
    except FileExistsError:
        concurrent = _read_private_key(path, transient_retries=20)
        if concurrent is None:  # pragma: no cover - FileExistsError contract
            raise TranscriptBackfillSafetyError(f"savings backfill key disappeared during creation: {path}")
        return concurrent
    except TranscriptBackfillSafetyError:
        raise
    except OSError as exc:
        raise TranscriptBackfillSafetyError(f"cannot create savings backfill key: {path}: {exc}") from exc

    installed = _read_private_key(path, transient_retries=0)
    if installed != key:
        raise TranscriptBackfillSafetyError(f"savings backfill key changed during creation: {path}")
    return key


def _load_or_create_key(root: Path, *, create: bool = True) -> bytes:
    state = _private_state_directory(root, create=create)
    if not create and not state.exists():
        return os.urandom(32)
    with pinned_owner_only_directory(state):
        path = state / SALT_NAME
        key = _read_private_key(path, transient_retries=20)
        if key is not None:
            return key
        if not create:
            return os.urandom(32)
        return _create_private_key(path)


def _keyed_hex(key: bytes, purpose: str, value: str, length: int = 24) -> str:
    digest = hmac.new(key, f"{purpose}\0{value}".encode("utf-8", "replace"), hashlib.sha256)
    return digest.hexdigest()[:length]


def _normalize_prompt(text: str) -> list[str]:
    text = _SYSTEM_BLOCK_RE.sub(" ", text)
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower())[:1024]:
        if len(raw) > 80:
            raw = "<long>"
        elif re.fullmatch(r"[0-9a-f]{12,}", raw):
            raw = "<hex>"
        elif re.fullmatch(r"\d+(?:\.\d+)*", raw):
            raw = "<n>"
        elif "/" in raw or "\\" in raw:
            raw = "<path>"
        tokens.append(raw)
    return tokens


def _intent_fingerprints(text: str, key: bytes) -> tuple[str, str, int]:
    tokens = _normalize_prompt(text)
    normalized = " ".join(tokens)
    exact = _keyed_hex(key, "intent-exact", normalized, 32)
    if not tokens:
        return exact, "0000000000000000", 0
    weights = [0] * 64
    features = tokens + [f"{a}\0{b}" for a, b in zip(tokens, tokens[1:])]
    for feature in features:
        digest = hashlib.blake2b(
            feature.encode("utf-8", "replace"),
            key=key,
            digest_size=8,
            person=b"roam-int",
        ).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            weights[bit] += 1 if value & (1 << bit) else -1
    simhash = sum(1 << bit for bit, weight in enumerate(weights) if weight >= 0)
    return exact, f"{simhash:016x}", len(tokens)


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def _is_correction(text: str) -> bool:
    cleaned = _SYSTEM_BLOCK_RE.sub(" ", text)
    cleaned = re.sub(r"<[^>]+>.*?</[^>]+>", " ", cleaned, flags=re.DOTALL)
    return bool(_CORRECTION_RE.match(cleaned.lstrip()))


def _tool_family(name: str) -> str:
    if name in _EDIT_TOOLS:
        return "edit"
    if name in {"Bash", "exec_command", "write_stdin"}:
        return "shell"
    if name in {"Read"}:
        return "read"
    if name in {"Grep", "Glob", "ToolSearch"}:
        return "search"
    if name.startswith("mcp__roam-code__") or name.startswith("roam_"):
        return "roam"
    if name in {"WebSearch", "WebFetch"} or name.startswith("web_search"):
        return "web"
    if name in {"Agent", "Task", "TaskCreate", "TaskUpdate", "TaskOutput"}:
        return "agent"
    return "other"


def _command_class(command: str) -> str:
    if _VERIFY_RE.search(command):
        return "verify"
    if _BUILD_RE.search(command):
        return "build"
    if _FORMAT_RE.search(command):
        return "format"
    if _DIFF_RE.search(command):
        return "review"
    if _VCS_WRITE_RE.search(command):
        return "vcs_write"
    if _DEPLOY_RE.search(command):
        return "deploy"
    if _DEPENDENCY_RE.search(command):
        return "dependency"
    if _SEARCH_RE.search(command):
        return "search"
    if _INSPECT_RE.search(command):
        return "inspect"
    if _ORIENT_RE.search(command):
        return "orient"
    if re.search(r"\bgit\b", command, re.IGNORECASE):
        return "git"
    return "other"


def _tool_phase(name: str, command: str = "") -> str:
    family = _tool_family(name)
    if family == "shell":
        command_class = _command_class(command)
        return {
            "verify": "verify",
            "build": "verify",
            "format": "format",
            "review": "review",
            "vcs_write": "publish",
            "deploy": "deploy",
            "dependency": "setup",
            "search": "search",
            "inspect": "inspect",
            "orient": "orient",
            "git": "orient",
        }.get(command_class, "shell")
    return {
        "edit": "edit",
        "read": "inspect",
        "search": "search",
        "roam": "intelligence",
        "web": "research",
        "agent": "delegate",
    }.get(family, "other")


def _intent_archetypes(text: str) -> list[str]:
    cleaned = _SYSTEM_BLOCK_RE.sub(" ", text)
    matches = [name for name, pattern in _INTENT_ARCHETYPES if pattern.search(cleaned)]
    return matches[:4] or ["other"]


def _content_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8", "replace"))
    try:
        return len(json.dumps(value, default=str).encode("utf-8", "replace"))
    except (TypeError, ValueError):
        return 0


def _exit_code(value: Any) -> int | None:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=str)
        except (TypeError, ValueError):
            return None
    match = _EXIT_CODE_RE.search(text)
    return int(match.group(1)) if match else None


def _result_text(value: Any) -> str:
    """Flatten transient tool-result text for classification only."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_result_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(
            _result_text(value.get(key))
            for key in ("content", "text", "output", "error", "message", "stderr")
            if key in value
        )
    return ""


def _failure_class(value: Any) -> str:
    """Return one closed failure label without retaining result content."""
    text = _result_text(value)[:200_000]
    for label, pattern in _FAILURE_CLASSIFIERS:
        if pattern.search(text):
            return label
    return "unknown"


def _elapsed_ms(started_at: datetime, observed_at: datetime | None) -> int | None:
    if observed_at is None:
        return None
    return max(0, int((observed_at - started_at).total_seconds() * 1000))


def _normalize_transcript_path(value: str) -> str:
    """Normalize transcript path text without consulting the filesystem."""
    if not value:
        return ""
    return os.path.normcase(os.path.normpath(value))


def _is_lexically_within(candidate: str, root: str) -> bool:
    """Return whether two already-normalized paths have lexical containment."""
    if not candidate or not root:
        return False
    try:
        return os.path.commonpath((candidate, root)) == root
    except ValueError:
        # Different drives, or a relative/absolute mismatch. Both are inert
        # classification failures rather than reasons to resolve either path.
        return False


def _project_scope(cwd: str, trusted_root: str = "") -> tuple[str, str]:
    """Classify transcript-provided CWD text using lexical operations only.

    ``trusted_root`` is the explicit command root after command-boundary
    validation. A transcript CWD lexically contained by that root shares the
    root's project fingerprint. Other CWD values remain isolated workspaces.
    No value originating in transcript data is expanded, resolved, or probed.
    """
    normalized_cwd = _normalize_transcript_path(cwd)
    if not normalized_cwd:
        return "", "missing"
    normalized_root = _normalize_transcript_path(trusted_root)
    if _is_lexically_within(normalized_cwd, normalized_root):
        return normalized_root, "workspace"
    return normalized_cwd, "workspace"


def _friction_metrics(
    actions: list[dict[str, Any]],
    *,
    verification_attempts: int,
) -> dict[str, int]:
    phases = [str(action.get("phase") or "other") for action in actions]
    templates = [str(action.get("template") or "") for action in actions if str(action.get("template") or "")]
    template_counts = Counter(templates)
    exact_shell_replays = sum(max(0, count - 1) for count in template_counts.values())
    adjacent_shell_replays = sum(bool(left and left == right) for left, right in zip(templates, templates[1:]))
    failed_action_retries = 0
    for index, action in enumerate(actions):
        template = str(action.get("template") or "")
        if not action.get("failed") or not template:
            continue
        if any(str(candidate.get("template") or "") == template for candidate in actions[index + 1 : index + 4]):
            failed_action_retries += 1
    first_edit = next((index for index, phase in enumerate(phases) if phase == "edit"), None)
    post_edit_context_calls = 0
    if first_edit is not None:
        for phase in phases[first_edit + 1 :]:
            if phase == "verify":
                break
            if phase in {"orient", "search", "inspect", "intelligence"}:
                post_edit_context_calls += 1
    search_inspect_cycles = sum(
        window
        in {
            ("search", "inspect", "search"),
            ("inspect", "search", "inspect"),
        }
        for window in zip(phases, phases[1:], phases[2:])
    )
    phase_switches = sum(left != right for left, right in zip(phases, phases[1:]))
    return {
        "orientation_calls": phases.count("orient"),
        "search_calls": phases.count("search"),
        "inspection_calls": phases.count("inspect"),
        "slice_calls": sum(bool(_SLICE_TEMPLATE_RE.search(template)) for template in templates),
        "output_postprocess_calls": sum(bool(_PROJECTION_RE.search(template)) for template in templates),
        "structured_output_postprocess_calls": sum(
            bool(_STRUCTURED_PROJECTION_RE.search(template)) for template in templates
        ),
        "help_calls": sum(bool(_HELP_RE.search(template)) for template in templates),
        "exact_shell_replays": exact_shell_replays,
        "adjacent_shell_replays": adjacent_shell_replays,
        "failed_action_retries": failed_action_retries,
        "verification_retries": max(0, verification_attempts - 1),
        "post_edit_context_calls": post_edit_context_calls,
        "search_inspect_cycles": search_inspect_cycles,
        "phase_switches": phase_switches,
    }


def _action_outcome_tables(
    actions: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    shell: dict[str, Counter[str]] = defaultdict(Counter)
    phases: dict[str, Counter[str]] = defaultdict(Counter)
    command_classes: dict[str, Counter[str]] = defaultdict(Counter)
    shell_failure_classes: dict[str, Counter[str]] = defaultdict(Counter)
    phase_failure_classes: dict[str, Counter[str]] = defaultdict(Counter)
    command_failure_classes: dict[str, Counter[str]] = defaultdict(Counter)
    for index, action in enumerate(actions):
        failed = bool(action.get("failed"))
        result_size = _int_nonnegative(action.get("result_size"))
        phase = str(action.get("phase") or "other")
        command_class = str(action.get("command_class") or "")
        template = str(action.get("template") or "")
        for table, key in (
            (phases, phase),
            (command_classes, command_class),
            (shell, template),
        ):
            if not key:
                continue
            table[key]["attempts"] += 1
            table[key]["failures"] += int(failed)
            table[key]["no_results"] += int(str(action.get("result_state") or "") == "no_results")
            table[key]["result_bytes"] += result_size
        failure_class = str(action.get("failure_class") or "")
        if failed and failure_class:
            for table, key in (
                (phase_failure_classes, phase),
                (command_failure_classes, command_class),
                (shell_failure_classes, template),
            ):
                if key:
                    table[key][failure_class] += 1
        if (
            failed
            and template
            and any(str(candidate.get("template") or "") == template for candidate in actions[index + 1 : index + 4])
        ):
            shell[template]["retries_after_failure"] += 1

    def render(
        table: dict[str, Counter[str]],
        failure_classes: dict[str, Counter[str]],
    ) -> dict[str, dict[str, Any]]:
        return {
            key: {
                "attempts": counts["attempts"],
                "failures": counts["failures"],
                "no_results": counts["no_results"],
                "retries_after_failure": counts["retries_after_failure"],
                "result_bytes_bucket": (_bucket(counts["result_bytes"], 4096) if counts["result_bytes"] else 0),
                "failure_classes": dict(sorted(failure_classes.get(key, Counter()).items())),
            }
            for key, counts in sorted(table.items())
        }

    return {
        "shell_template_outcomes": render(shell, shell_failure_classes),
        "phase_outcomes": render(phases, phase_failure_classes),
        "command_class_outcomes": render(
            command_classes,
            command_failure_classes,
        ),
    }


def _bounded_nonnegative_int(value: Any, *, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if number < 0 or number > maximum:
        return None
    return number


def _int_nonnegative(value: Any) -> int:
    number = _bounded_nonnegative_int(value, maximum=MAX_TRANSCRIPT_BYTES)
    return number if number is not None else 0


def _run_length_values(values: list[str]) -> list[tuple[str, int]]:
    if not values:
        return []
    runs: list[tuple[str, int]] = []
    current = values[0]
    count = 1
    for value in values[1:]:
        if value == current:
            count += 1
            continue
        runs.append((current, count))
        current = value
        count = 1
    runs.append((current, count))
    return runs


def _compressed_sequence(values: list[str], *, limit: int = 20) -> str:
    runs = _run_length_values(values)
    rendered = [f"{value}*{count}" if count > 1 else value for value, count in runs[:limit]]
    if len(runs) > limit:
        rendered.append("<MORE>")
    return ">".join(rendered)


def _compressed_command_sequence(values: list[str], *, limit: int = 12) -> str:
    runs = _run_length_values(values)
    rendered = [f"{value} ×{count}" if count > 1 else value for value, count in runs[:limit]]
    if len(runs) > limit:
        rendered.append("<MORE>")
    return " => ".join(rendered)


def _sequence_ngrams(
    values: list[str],
    *,
    minimum: int = 2,
    maximum: int = 4,
) -> Counter[str]:
    out: Counter[str] = Counter()
    for width in range(minimum, maximum + 1):
        for index in range(0, len(values) - width + 1):
            out[" => ".join(values[index : index + width])] += 1
    return out


def _command_from_input(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("command", "cmd"):
            command = value.get(key)
            if isinstance(command, str):
                return command
            if isinstance(command, list):
                return " ".join(str(part) for part in command)
        return ""
    if not isinstance(value, str):
        return ""
    try:
        decoded = loads_bounded(value, object_pairs_hook=strict_json_object_pairs)
    except (TypeError, ValueError):
        return value
    return _command_from_input(decoded)


def _safe_executable(token: str) -> str:
    token = token.strip("\"'`")
    base = re.split(r"[/\\]", token)[-1].lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base if base in _SAFE_EXECUTABLES else "<EXEC>"


def _safe_flag(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in _SAFE_FLAGS else "<FLAG>"


def _safe_argument(token: str, *, executable: str, positional_index: int) -> str:
    raw = token.strip()
    clean = raw.strip("\"'`")
    if not clean:
        return "<ARG>"
    if raw.startswith((">", "2>", "1>", "<")):
        return "<REDIR>"
    if _URL_RE.search(clean):
        return "<URL>"
    if _PATHISH_RE.search(clean):
        return "<PATH>"
    if "=" in clean and not clean.startswith(("==", "!=")):
        name, _value = clean.split("=", 1)
        if name.startswith("-"):
            flag = _safe_flag(name)
            return f"{flag}=<ARG>"
        return "<ENV>=<VALUE>"
    if _SECRET_RE.search(clean) or re.fullmatch(r"[A-Za-z0-9+/=_-]{32,}", clean):
        return "<SECRET>"
    if clean.startswith("-"):
        return _safe_flag(clean)
    if re.fullmatch(r"\d+(?:\.\d+)*", clean):
        return "<N>"
    if positional_index == 0 and executable in _KNOWN_SUBCOMMAND_EXECUTABLES:
        normalized = clean.lower()
        return (
            normalized if normalized in _SAFE_SUBCOMMANDS_BY_EXECUTABLE.get(executable, frozenset()) else "<SUBCOMMAND>"
        )
    if (
        positional_index == 1
        and executable in {"npm", "pnpm", "yarn"}
        and clean.lower() in {"build", "test", "lint", "typecheck", "format"}
    ):
        return clean.lower()
    if executable in {"python", "python3", "py"}:
        return clean.lower() if clean in {"pytest", "unittest", "pip"} else "<MODULE>"
    return "<ARG>"


def _split_shell_control(command: str) -> list[str]:
    """Split control operators outside quotes; normalize newlines to ``;``."""
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    escaped = False
    index = 0

    def flush() -> None:
        value = "".join(buf).strip()
        if value:
            out.append(value)
        buf.clear()

    while index < len(command):
        char = command[index]
        if escaped:
            buf.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            buf.append(char)
            escaped = True
            index += 1
            continue
        if quote:
            buf.append(char)
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            buf.append(char)
            index += 1
            continue
        pair = command[index : index + 2]
        if pair in {"&&", "||"}:
            flush()
            out.append(pair)
            index += 2
            continue
        if char in {"|", ";", "\n"}:
            flush()
            operator = "|" if char == "|" else ";"
            if not out or out[-1] != operator:
                out.append(operator)
            index += 1
            continue
        buf.append(char)
        index += 1
    flush()
    return out


def sanitize_command_template(command: str) -> str:
    """Return a useful shell shape while removing values, paths, and secrets."""
    command = command.replace("\r", "\n").strip()
    if not command:
        return ""
    parts = _split_shell_control(command)
    rendered: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part in {"&&", "||", "|", ";"}:
            rendered.append(part)
            continue
        if "\n" in part and any(marker in part for marker in ("<<", "@'", '@"')):
            head = part.splitlines()[0]
            part = head + " <HEREDOC>"
        try:
            # Transcript parsing must be reproducible across the host that runs
            # the miner. Agent shell records are predominantly POSIX-shaped;
            # using the mining host's ``os.name`` made one corpus produce
            # different templates on Windows and Linux.
            tokens = shlex.split(part, posix=True)
        except ValueError:
            tokens = re.findall(r"""(?:"[^"]*"|'[^']*'|`[^`]*`|\S+)""", part)
        if not tokens:
            continue
        env_prefix: list[str] = []
        token_index = 0
        while token_index < len(tokens) and "=" in tokens[token_index] and not tokens[token_index].startswith("-"):
            env_prefix.append("<ENV>=<VALUE>")
            token_index += 1
        if token_index >= len(tokens):
            rendered.extend(env_prefix or ["<ENV>=<VALUE>"])
            continue
        executable = _safe_executable(tokens[token_index])
        segment = [*env_prefix, executable]
        positional_index = 0
        value_expected = ""
        for token in tokens[token_index + 1 : token_index + 20]:
            if value_expected:
                if (
                    value_expected == "-m"
                    and executable in {"python", "python3", "py"}
                    and token.strip("\"'`").lower()
                    in {"pytest", "unittest", "pip", "py_compile", "compileall", "ruff", "mypy"}
                ):
                    safe = token.strip("\"'`").lower()
                else:
                    safe = "<CODE>" if value_expected in {"-c", "--eval", "--command"} else "<ARG>"
                value_expected = ""
            else:
                safe = _safe_argument(
                    token,
                    executable=executable,
                    positional_index=positional_index,
                )
            segment.append(safe)
            if safe in _FLAGS_WITH_VALUES:
                value_expected = safe
            if not token.startswith("-"):
                positional_index += 1
        if len(tokens) - token_index > 20:
            segment.append("<MORE>")
        rendered.append(" ".join(segment))
    template = " ".join(rendered)
    return template[:320]


def _cwd_matches(cwd: str, root: Path, all_projects: bool) -> bool:
    if all_projects:
        return True
    if not cwd:
        return False
    left = _normalize_transcript_path(cwd)
    right = _normalize_transcript_path(str(root))
    return _is_lexically_within(left, right)


@dataclass
class _Episode:
    source: str
    session_key: str
    turn_seq: int
    started_at: datetime
    prompt: str
    cwd: str
    project_root: str
    key: bytes
    last_at: datetime | None = None
    explicit_duration_ms: int | None = None
    tool_sequence: list[str] = field(default_factory=list)
    tool_counts: Counter[str] = field(default_factory=Counter)
    command_counts: Counter[str] = field(default_factory=Counter)
    command_templates: Counter[str] = field(default_factory=Counter)
    command_sequence: list[str] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, int] = field(default_factory=dict)
    tool_errors: int = 0
    edit_actions: int = 0
    failed_edit_actions: int = 0
    verification_attempts: int = 0
    verification_failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_output_tokens: int = 0
    tool_result_bytes: int = 0
    assistant_messages: int = 0
    first_tool_at: datetime | None = None
    first_edit_at: datetime | None = None
    correction_after: bool = False
    explicit_complete: bool = False

    def add_tool(
        self,
        name: str,
        call_id: str,
        tool_input: Any,
        observed_at: datetime | None = None,
    ) -> None:
        family = _tool_family(name)
        self.tool_sequence.append(family)
        self.tool_counts[family] += 1
        command_class = ""
        raw_command = ""
        template = ""
        if family == "shell":
            raw_command = _command_from_input(tool_input)
            command_class = _command_class(raw_command)
            self.command_counts[command_class] += 1
            template = sanitize_command_template(raw_command)
            if template:
                self.command_templates[template] += 1
                self.command_sequence.append(template)
            if command_class in {"verify", "build"}:
                self.verification_attempts += 1
        if name in _EDIT_TOOLS:
            self.edit_actions += 1
        if self.first_tool_at is None:
            self.first_tool_at = observed_at
        if family == "edit" and self.first_edit_at is None:
            self.first_edit_at = observed_at
        self.actions.append(
            {
                "family": family,
                "phase": _tool_phase(name, raw_command),
                "command_class": command_class,
                "template": template,
                "failed": False,
                "result_state": "unknown",
                "result_size": 0,
            }
        )
        if call_id:
            self.pending[call_id] = len(self.actions) - 1

    def add_result(
        self,
        call_id: str,
        failed: bool,
        result_size: int = 0,
        exit_code: int | None = None,
        result_value: Any = "",
    ) -> None:
        self.tool_result_bytes += max(0, result_size)
        action_index = self.pending.pop(call_id, -1)
        action = self.actions[action_index] if 0 <= action_index < len(self.actions) else {}
        family = str(action.get("family") or "")
        command_class = str(action.get("command_class") or "")
        no_results = bool(failed and command_class == "search" and exit_code == 1)
        effective_failure = bool(failed and not no_results)
        if action:
            action["failed"] = effective_failure
            action["result_state"] = "no_results" if no_results else "failure" if effective_failure else "success"
            action["result_size"] = max(0, result_size)
            action["failure_class"] = _failure_class(result_value) if effective_failure else ""
        if effective_failure:
            self.tool_errors += 1
            if family == "edit":
                self.failed_edit_actions += 1
            if command_class in {"verify", "build"}:
                self.verification_failures += 1

    def finish(self, terminal_at: datetime | None) -> tuple[dict[str, Any], dict[str, Any]]:
        terminal_at = terminal_at or self.last_at or self.started_at
        duration_ms = self.explicit_duration_ms
        if duration_ms is None:
            duration_ms = max(0, int((terminal_at - self.started_at).total_seconds() * 1000))
        exact, simhash, token_count = _intent_fingerprints(self.prompt, self.key)
        episode_id = "hist_" + _keyed_hex(
            self.key,
            "episode",
            f"{self.source}\0{self.session_key}\0{self.turn_seq}",
            24,
        )
        session_id = "hist_" + _keyed_hex(self.key, "session", self.session_key, 16)
        successful_edits = max(0, self.edit_actions - self.failed_edit_actions)
        if successful_edits and self.verification_attempts and not self.verification_failures:
            outcome = "historical_acted_verified_proxy"
            health_state = "proxy_verification_passed"
        elif successful_edits and self.verification_failures:
            outcome = "historical_acted_verification_failed_proxy"
            health_state = "proxy_verification_failed"
        elif successful_edits:
            outcome = "historical_acted_unverified"
            health_state = "proxy_unverified"
        elif self.tool_errors:
            outcome = "historical_no_edit_tool_error"
            health_state = "proxy_tool_error"
        else:
            outcome = "historical_no_edit"
            health_state = "not_applicable"
        trajectory = ",".join(self.tool_sequence)
        trajectory_fingerprint = _keyed_hex(self.key, "trajectory", trajectory, 24)
        command_sequence = "\n".join(self.command_sequence)
        command_sequence_fingerprint = _keyed_hex(self.key, "command-sequence", command_sequence, 24)
        event_base = {
            "schema_version": 1,
            "backfill_version": BACKFILL_VERSION,
            "evidence_source": "transcript_backfill",
            "transcript_source": self.source,
            "episode_id": episode_id,
            "session_id": session_id,
            "turn_seq": self.turn_seq,
        }
        normalized_cwd = _normalize_transcript_path(self.cwd)
        project_scope, project_identity_basis = _project_scope(normalized_cwd, self.project_root)
        project_id = "proj_" + _keyed_hex(self.key, "project", project_scope, 20) if project_scope else ""
        phases = [str(action.get("phase") or "other") for action in self.actions]
        friction = _friction_metrics(
            self.actions,
            verification_attempts=self.verification_attempts,
        )
        outcome_tables = _action_outcome_tables(self.actions)
        start = {
            **event_base,
            "event_id": f"evt_{episode_id[5:]}_start",
            "event_type": "prompt_submitted",
            "ts": _iso(self.started_at),
            "terminal": False,
            "outcome": "pending",
            "compile_expected": False,
            "prompt_hmac_sha256": exact,
            "intent_simhash64": simhash,
            "prompt_chars_bucket": _bucket(len(self.prompt), 100),
            "prompt_tokens_bucket": _bucket(token_count, 25),
            "project_id": project_id,
            "project_identity_basis": project_identity_basis,
            "cwd_hmac_sha256": _keyed_hex(self.key, "cwd", normalized_cwd, 24),
            "intent_archetypes": _intent_archetypes(self.prompt),
            "health_state": "unknown",
        }
        terminal = {
            **event_base,
            "event_id": f"evt_{episode_id[5:]}_terminal",
            "event_type": "transcript_terminal",
            "ts": _iso(terminal_at),
            "terminal": True,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "changed_files": None,
            "health_state": health_state,
            "trajectory_fingerprint": trajectory_fingerprint,
            "trajectory_template": _compressed_sequence(self.tool_sequence),
            "phase_sequence_template": _compressed_sequence(phases),
            "phase_ngrams": dict(_sequence_ngrams(phases, minimum=2, maximum=5).most_common(80)),
            "command_sequence_fingerprint": command_sequence_fingerprint,
            "command_sequence_template": _compressed_command_sequence(self.command_sequence),
            "shell_templates": dict(self.command_templates.most_common(30)),
            "shell_ngrams": dict(_sequence_ngrams(self.command_sequence).most_common(80)),
            "tool_ngrams": dict(_sequence_ngrams(self.tool_sequence, minimum=2, maximum=5).most_common(80)),
            "tool_calls": sum(self.tool_counts.values()),
            "tool_errors": self.tool_errors,
            "failure_classes": dict(
                sorted(
                    Counter(
                        str(action.get("failure_class") or "")
                        for action in self.actions
                        if action.get("failed") and action.get("failure_class")
                    ).items()
                )
            ),
            "tool_result_bytes_bucket": (_bucket(self.tool_result_bytes, 4096) if self.tool_result_bytes else 0),
            "assistant_messages": self.assistant_messages,
            "time_to_first_tool_ms": _elapsed_ms(self.started_at, self.first_tool_at),
            "time_to_first_edit_ms": _elapsed_ms(self.started_at, self.first_edit_at),
            "tool_families": dict(sorted(self.tool_counts.items())),
            "command_classes": dict(sorted(self.command_counts.items())),
            "friction": friction,
            **outcome_tables,
            "edit_actions": successful_edits,
            "verification_attempts": self.verification_attempts,
            "verification_failures": self.verification_failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "correction_after": self.correction_after,
            "explicit_complete": self.explicit_complete,
        }
        return start, terminal


def _iter_jsonl(
    path: Path,
    source_root: Path | None = None,
    diagnostics: dict[str, Any] | None = None,
    *,
    byte_budget: int | None = None,
    row_budget: int | None = None,
    deadline: float | None = None,
) -> Iterable[dict[str, Any]]:
    if diagnostics is not None:
        diagnostics.update(state="ok", invalid_rows=0, oversized_lines=0, bytes_read=0, rows_seen=0)

    def set_state(value: str) -> None:
        if diagnostics is not None:
            diagnostics["state"] = value

    def mark_invalid(*, oversized: bool = False) -> None:
        if diagnostics is not None:
            diagnostics["invalid_rows"] += 1
            diagnostics["oversized_lines"] += int(oversized)

    def mark_read(size: int, *, row: bool = False) -> None:
        if diagnostics is not None:
            diagnostics["bytes_read"] += size
            diagnostics["rows_seen"] += int(row)

    descriptor = -1
    try:
        if deadline is not None and time.monotonic() >= deadline:
            set_state("aggregate_time_limit_reached")
            return
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse_point(before)
            or before.st_nlink != 1
            or before.st_size > MAX_TRANSCRIPT_BYTES
            or (byte_budget is not None and before.st_size > max(0, byte_budget))
        ):
            if before.st_size > MAX_TRANSCRIPT_BYTES:
                set_state("oversized")
            elif byte_budget is not None and before.st_size > max(0, byte_budget):
                set_state("aggregate_byte_limit_reached")
            else:
                set_state("unsafe_path")
            return
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > MAX_TRANSCRIPT_BYTES
        ):
            set_state("changed_or_unsafe_path")
            return
        if source_root is not None:
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(source_root)
                current = os.lstat(path)
            except (OSError, RuntimeError, ValueError):
                set_state("escaped_or_changed_path")
                return
            if (
                (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                or not stat.S_ISREG(current.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or _is_reparse_point(current)
                or current.st_nlink != 1
            ):
                set_state("escaped_or_changed_path")
                return
        fh = os.fdopen(descriptor, "rb")
        descriptor = -1
        rows: list[dict[str, Any]] = []
        effective_row_limit = MAX_TRANSCRIPT_ROWS_PER_FILE
        aggregate_row_limit = False
        if row_budget is not None and row_budget < effective_row_limit:
            effective_row_limit = max(0, row_budget)
            aggregate_row_limit = True
        with fh:
            remaining = opened.st_size
            while remaining:
                if deadline is not None and time.monotonic() >= deadline:
                    set_state("aggregate_time_limit_reached")
                    return
                if diagnostics is not None and diagnostics["rows_seen"] >= effective_row_limit:
                    set_state("aggregate_row_limit_reached" if aggregate_row_limit else "row_limit_reached")
                    return
                raw_line = fh.readline(min(MAX_TRANSCRIPT_LINE_BYTES + 1, remaining))
                if not raw_line:
                    set_state("changed_during_read")
                    return
                remaining -= len(raw_line)
                mark_read(len(raw_line), row=True)
                if len(raw_line) > MAX_TRANSCRIPT_LINE_BYTES:
                    while remaining and not raw_line.endswith(b"\n"):
                        raw_line = fh.readline(min(MAX_TRANSCRIPT_LINE_BYTES + 1, remaining))
                        if not raw_line:
                            set_state("changed_during_read")
                            return
                        remaining -= len(raw_line)
                        mark_read(len(raw_line))
                    mark_invalid(oversized=True)
                    continue
                try:
                    value = loads_bounded(
                        raw_line.decode("utf-8"),
                        object_pairs_hook=strict_json_object_pairs,
                    )
                except (UnicodeDecodeError, TypeError, ValueError):
                    mark_invalid()
                    continue
                if isinstance(value, dict):
                    rows.append(value)
                else:
                    mark_invalid()
            after_opened = os.fstat(fh.fileno())
        try:
            after_path = os.lstat(path)
            if source_root is not None:
                path.resolve(strict=True).relative_to(source_root)
        except (OSError, RuntimeError, ValueError):
            set_state("escaped_or_changed_path")
            return
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns")
        if os.name != "nt":
            stable_fields += ("st_ctime_ns",)
        if (
            not stat.S_ISREG(after_opened.st_mode)
            or after_opened.st_nlink != 1
            or stat.S_ISLNK(after_path.st_mode)
            or _is_reparse_point(after_path)
            or after_path.st_nlink != 1
            or any(getattr(opened, field) != getattr(after_opened, field) for field in stable_fields)
            or any(getattr(after_opened, field) != getattr(after_path, field) for field in stable_fields)
        ):
            set_state("changed_during_read")
            return
        if diagnostics is not None and diagnostics["invalid_rows"]:
            set_state("partial_invalid_rows")
        yield from rows
    except OSError:
        set_state("unavailable")
        return
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError as exc:
                log_swallowed("transcript_backfill._iter_jsonl.fd_close", exc)


def _extend_bounded_events(events: list[dict[str, Any]], produced: tuple[dict[str, Any], dict[str, Any]]) -> None:
    if len(events) + len(produced) > MAX_TRANSCRIPT_EVENTS_PER_FILE:
        raise TranscriptBackfillSafetyError(
            f"one transcript exceeds the {MAX_TRANSCRIPT_EVENTS_PER_FILE}-event per-file limit"
        )
    events.extend(produced)


def _scan_claude(
    path: Path,
    root: Path,
    key: bytes,
    all_projects: bool,
    source_root: Path | None = None,
    parsed_rows: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: _Episode | None = None
    turn_seq = 0
    session_key = path.stem
    session_cwd = ""
    last_ts: datetime | None = None
    for row in parsed_rows if parsed_rows is not None else _iter_jsonl(path, source_root):
        ts = _parse_ts(row.get("timestamp")) or last_ts
        if ts:
            last_ts = ts
        cwd = str(row.get("cwd") or "")
        if cwd:
            session_cwd = cwd
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if row.get("type") == "assistant" and current:
            current.assistant_messages += 1
            usage = message.get("usage")
            if isinstance(usage, dict):
                for attribute, key_name in (
                    ("input_tokens", "input_tokens"),
                    ("output_tokens", "output_tokens"),
                    ("cached_input_tokens", "cache_read_input_tokens"),
                    ("cache_creation_tokens", "cache_creation_input_tokens"),
                ):
                    number = _bounded_nonnegative_int(usage.get(key_name), maximum=MAX_TOKEN_COUNT)
                    if number is not None:
                        setattr(current, attribute, min(MAX_TOKEN_COUNT, getattr(current, attribute) + number))
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    current.add_tool(
                        str(block.get("name") or ""),
                        str(block.get("id") or ""),
                        block.get("input"),
                        ts,
                    )
            current.last_at = ts or current.last_at
            continue
        if row.get("type") != "user":
            continue
        if isinstance(content, list) and current:
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                current.add_result(
                    str(block.get("tool_use_id") or ""),
                    block.get("is_error") is True,
                    _content_size(block.get("content")),
                    _exit_code(block.get("content")),
                    block.get("content"),
                )
            current.last_at = ts or current.last_at
        prompt = _text_content(content)
        if not prompt:
            continue
        if current:
            current.correction_after = _is_correction(prompt)
            _extend_bounded_events(events, current.finish(ts))
        turn_seq += 1
        effective_cwd = cwd or session_cwd
        if not _cwd_matches(effective_cwd, root, all_projects):
            current = None
            continue
        current = _Episode(
            source="claude",
            session_key=session_key,
            turn_seq=turn_seq,
            started_at=ts or datetime.now(timezone.utc),
            prompt=prompt,
            cwd=effective_cwd,
            project_root=str(root),
            key=key,
        )
    if current:
        _extend_bounded_events(events, current.finish(last_ts))
    return events


def _scan_codex(
    path: Path,
    root: Path,
    key: bytes,
    all_projects: bool,
    source_root: Path | None = None,
    parsed_rows: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: _Episode | None = None
    turn_seq = 0
    session_key = path.stem
    cwd = ""
    last_ts: datetime | None = None
    for row in parsed_rows if parsed_rows is not None else _iter_jsonl(path, source_root):
        ts = _parse_ts(row.get("timestamp")) or last_ts
        if ts:
            last_ts = ts
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if not cwd and isinstance(payload.get("cwd"), str):
            cwd = payload["cwd"]
        payload_type = str(payload.get("type") or "")
        if row.get("type") == "event_msg" and payload_type == "user_message":
            prompt = str(payload.get("message") or "")
            if not prompt:
                continue
            if current:
                current.correction_after = _is_correction(prompt)
                _extend_bounded_events(events, current.finish(ts))
            turn_seq += 1
            current = _Episode(
                source="codex",
                session_key=session_key,
                turn_seq=turn_seq,
                started_at=ts or datetime.now(timezone.utc),
                prompt=prompt,
                cwd=cwd,
                project_root=str(root),
                key=key,
            )
            continue
        if not current:
            continue
        if row.get("type") == "response_item" and payload_type in {
            "function_call",
            "custom_tool_call",
        }:
            current.add_tool(
                str(payload.get("name") or ""),
                str(payload.get("call_id") or ""),
                payload.get("arguments") if "arguments" in payload else payload.get("input"),
                ts,
            )
        elif row.get("type") == "response_item" and payload_type in {
            "function_call_output",
            "custom_tool_call_output",
        }:
            output = str(payload.get("output") or "")
            exit_code = _exit_code(output)
            current.add_result(
                str(payload.get("call_id") or ""),
                exit_code not in {None, 0},
                _content_size(output),
                exit_code,
                output,
            )
        elif row.get("type") == "event_msg" and payload_type == "patch_apply_end":
            if payload.get("success") is not True:
                current.tool_errors += 1
                current.failed_edit_actions += 1
        elif row.get("type") == "event_msg" and payload_type == "token_count":
            info = payload.get("info")
            last_usage = info.get("last_token_usage") if isinstance(info, dict) else {}
            if isinstance(last_usage, dict):
                for attribute, key_name in (
                    ("input_tokens", "input_tokens"),
                    ("output_tokens", "output_tokens"),
                    ("cached_input_tokens", "cached_input_tokens"),
                    ("reasoning_output_tokens", "reasoning_output_tokens"),
                ):
                    number = _bounded_nonnegative_int(last_usage.get(key_name), maximum=MAX_TOKEN_COUNT)
                    if number is not None:
                        setattr(current, attribute, number)
        elif row.get("type") == "event_msg" and payload_type == "task_complete":
            current.explicit_complete = True
            duration_ms = _bounded_nonnegative_int(payload.get("duration_ms"), maximum=MAX_DURATION_MS)
            if duration_ms is not None:
                current.explicit_duration_ms = duration_ms
            current.last_at = ts or current.last_at
            _extend_bounded_events(events, current.finish(ts))
            current = None
            continue
        current.last_at = ts or current.last_at
    if current:
        _extend_bounded_events(events, current.finish(last_ts))
    if all_projects or _cwd_matches(cwd, root, False):
        return events
    return []


def _detect_source_from_rows(rows: Iterable[dict[str, Any]]) -> str:
    for row in rows:
        if set(row) == {"timestamp", "type", "payload"}:
            return "codex"
        if "message" in row or "sessionId" in row:
            return "claude"
        break
    return "unknown"


def _detect_source(path: Path, source_root: Path | None = None) -> str:
    return _detect_source_from_rows(_iter_jsonl(path, source_root))


def _candidate_files(
    transcripts_dir: Path,
    *,
    since: datetime | None,
    max_files: int,
    deadline: float | None = None,
    directory_budget: int | None = None,
    entry_budget: int | None = None,
) -> tuple[list[Path], dict[str, int]]:
    candidate_files_seen = 0
    oversized_files = 0
    directory_entries_scanned = 0
    directories_scanned = 0
    traversal_truncated = False
    elapsed_limit_reached = False
    directory_budget_reached = False
    entry_budget_reached = False

    def result(selected: list[Path]) -> tuple[list[Path], dict[str, int]]:
        return selected, {
            "candidate_files_seen": candidate_files_seen,
            "oversized_files": oversized_files,
            "files_truncated": max(0, candidate_files_seen - len(selected)),
            "directories_scanned": directories_scanned,
            "directory_entries_scanned": directory_entries_scanned,
            "traversal_truncated": int(traversal_truncated),
            "elapsed_limit_reached": int(elapsed_limit_reached),
            "directory_budget_reached": int(directory_budget_reached),
            "entry_budget_reached": int(entry_budget_reached),
        }

    effective_directory_budget = max(
        0,
        min(
            MAX_TRANSCRIPT_DIRECTORIES,
            MAX_TRANSCRIPT_DIRECTORIES if directory_budget is None else directory_budget,
        ),
    )
    effective_entry_budget = max(
        0,
        min(
            MAX_TRANSCRIPT_DIRECTORY_ENTRIES,
            MAX_TRANSCRIPT_DIRECTORY_ENTRIES if entry_budget is None else entry_budget,
        ),
    )
    if deadline is not None and time.monotonic() >= deadline:
        elapsed_limit_reached = True
        traversal_truncated = True
        return result([])
    if effective_directory_budget == 0:
        directory_budget_reached = True
        traversal_truncated = True
        return result([])
    if effective_entry_budget == 0:
        entry_budget_reached = True
        traversal_truncated = True
        return result([])
    try:
        source_root = transcripts_dir.resolve(strict=True)
    except OSError:
        return result([])
    limit = max(1, min(max_files, MAX_TRANSCRIPT_FILES_PER_SOURCE))
    newest: list[tuple[float, str]] = []
    stack = [source_root]
    seen_directories: set[tuple[int, int]] = set()
    while stack:
        if deadline is not None and time.monotonic() >= deadline:
            elapsed_limit_reached = True
            traversal_truncated = True
            break
        if directories_scanned >= effective_directory_budget:
            directory_budget_reached = True
            traversal_truncated = True
            break
        base = stack.pop()
        try:
            base_info = base.lstat()
        except OSError:
            continue
        identity = (base_info.st_dev, base_info.st_ino)
        if (
            identity in seen_directories
            or not stat.S_ISDIR(base_info.st_mode)
            or stat.S_ISLNK(base_info.st_mode)
            or _is_reparse_point(base_info)
        ):
            continue
        seen_directories.add(identity)
        directories_scanned += 1
        if deadline is not None and time.monotonic() >= deadline:
            elapsed_limit_reached = True
            traversal_truncated = True
            break
        try:
            entries = os.scandir(base)
        except OSError:
            continue
        with entries:
            while True:
                # Check before asking the iterator for another entry. This
                # prevents one extra directory read after the shared deadline
                # or entry budget has expired.
                if deadline is not None and time.monotonic() >= deadline:
                    elapsed_limit_reached = True
                    traversal_truncated = True
                    stack.clear()
                    break
                if directory_entries_scanned >= effective_entry_budget:
                    entry_budget_reached = True
                    traversal_truncated = True
                    stack.clear()
                    break
                try:
                    entry = next(entries)
                except StopIteration:
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    elapsed_limit_reached = True
                    traversal_truncated = True
                    stack.clear()
                    break
                directory_entries_scanned += 1
                path = Path(entry.path)
                try:
                    # ``DirEntry.stat`` reports zero identity/link fields on
                    # current Windows Python builds; ``Path.lstat`` reaches
                    # the native file-id implementation used by the later
                    # pinned-open checks while remaining non-following.
                    value = path.lstat()
                except OSError:
                    continue
                if stat.S_ISDIR(value.st_mode):
                    if stat.S_ISLNK(value.st_mode) or _is_reparse_point(value):
                        continue
                    if len(stack) + directories_scanned >= effective_directory_budget:
                        directory_budget_reached = True
                        traversal_truncated = True
                        continue
                    stack.append(path)
                    continue
                if not entry.name.endswith(".jsonl"):
                    continue
                unsafe = (
                    not stat.S_ISREG(value.st_mode)
                    or stat.S_ISLNK(value.st_mode)
                    or _is_reparse_point(value)
                    or value.st_nlink != 1
                )
                if unsafe:
                    continue
                if value.st_size > MAX_TRANSCRIPT_BYTES:
                    oversized_files += 1
                    continue
                if since and datetime.fromtimestamp(value.st_mtime, timezone.utc) < since:
                    continue
                candidate_files_seen += 1
                item = (value.st_mtime, str(path))
                if len(newest) < limit:
                    heapq.heappush(newest, item)
                elif item > newest[0]:
                    heapq.heapreplace(newest, item)
    selected = [Path(rendered) for _mtime, rendered in sorted(newest)]
    return result(selected)


def backfill_transcripts(
    root: str | Path,
    transcripts_dir: str | Path | Iterable[str | Path],
    *,
    source: str = "auto",
    since: datetime | None = None,
    max_files: int = DEFAULT_MAX_TRANSCRIPT_FILES,
    all_projects: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    try:
        root_path = Path(root).resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise TranscriptBackfillSafetyError("project root could not be resolved safely") from exc
    if isinstance(transcripts_dir, (str, Path)):
        source_values = [transcripts_dir]
    else:
        source_values = []
        for value in transcripts_dir:
            source_values.append(value)
            if len(source_values) > MAX_TRANSCRIPT_SOURCES:
                break
    if len(source_values) > MAX_TRANSCRIPT_SOURCES:
        raise TranscriptBackfillSafetyError(
            f"transcript source count exceeds the {MAX_TRANSCRIPT_SOURCES}-source limit"
        )
    if isinstance(max_files, bool) or not isinstance(max_files, int):
        raise TranscriptBackfillSafetyError("max_files must be an integer")
    effective_max_files = max_files if max_files > 0 else DEFAULT_MAX_TRANSCRIPT_FILES
    if effective_max_files > MAX_TRANSCRIPT_FILES_PER_SOURCE:
        raise TranscriptBackfillSafetyError(
            f"max_files exceeds the {MAX_TRANSCRIPT_FILES_PER_SOURCE}-file per-source limit"
        )
    source_paths: list[Path] = []
    seen_sources: set[str] = set()
    for value in source_values:
        try:
            path = Path(value).expanduser().resolve()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise TranscriptBackfillSafetyError("transcript source could not be resolved safely") from exc
        identity = os.path.normcase(str(path))
        if identity not in seen_sources:
            seen_sources.add(identity)
            source_paths.append(path)
    # One monotonic budget covers key setup, discovery across every source,
    # candidate consolidation, and transcript processing. Explicit command
    # roots are validated before the timer starts; transcript-derived values
    # are never involved in that validation.
    started_at = time.monotonic()
    deadline = started_at + MAX_TRANSCRIPT_ELAPSED_SECONDS
    aggregate_limit_reached = "none"
    # A preview must be observational. Use an ephemeral fingerprint key so a
    # Windows dry run neither creates nor tightens `.roam` or its children.
    key = os.urandom(32) if dry_run else _load_or_create_key(root_path)
    file_heap: list[tuple[float, str, Path, Path]] = []
    seen_files: set[str] = set()
    candidate_files_seen = 0
    oversized_files = 0
    files_truncated = 0
    global_limit_drops = 0
    directories_scanned = 0
    directory_entries_scanned = 0
    traversal_truncated = 0
    discovery_limit_reached = "none"
    remaining_directories = MAX_TRANSCRIPT_DIRECTORIES
    remaining_directory_entries = MAX_TRANSCRIPT_DIRECTORY_ENTRIES
    for source_index, source_path in enumerate(source_paths):
        if time.monotonic() >= deadline:
            aggregate_limit_reached = "elapsed"
            discovery_limit_reached = "elapsed"
            traversal_truncated += 1
            break
        candidates, candidate_stats = _candidate_files(
            source_path,
            since=since,
            max_files=effective_max_files,
            deadline=deadline,
            directory_budget=remaining_directories,
            entry_budget=remaining_directory_entries,
        )
        candidate_files_seen += candidate_stats["candidate_files_seen"]
        oversized_files += candidate_stats["oversized_files"]
        files_truncated += candidate_stats["files_truncated"]
        directories_scanned += candidate_stats["directories_scanned"]
        directory_entries_scanned += candidate_stats["directory_entries_scanned"]
        traversal_truncated += candidate_stats["traversal_truncated"]
        remaining_directories = max(0, remaining_directories - candidate_stats["directories_scanned"])
        remaining_directory_entries = max(
            0,
            remaining_directory_entries - candidate_stats["directory_entries_scanned"],
        )
        if candidate_stats["elapsed_limit_reached"]:
            aggregate_limit_reached = "elapsed"
            discovery_limit_reached = "elapsed"
            break
        stop_discovery = False
        for path in candidates:
            if time.monotonic() >= deadline:
                aggregate_limit_reached = "elapsed"
                discovery_limit_reached = "elapsed"
                traversal_truncated += int(not candidate_stats["traversal_truncated"])
                stop_discovery = True
                break
            identity = os.path.normcase(str(path.resolve()))
            if identity in seen_files:
                continue
            if time.monotonic() >= deadline:
                aggregate_limit_reached = "elapsed"
                discovery_limit_reached = "elapsed"
                traversal_truncated += int(not candidate_stats["traversal_truncated"])
                stop_discovery = True
                break
            try:
                mtime = path.lstat().st_mtime
            except OSError:
                continue
            if time.monotonic() >= deadline:
                aggregate_limit_reached = "elapsed"
                discovery_limit_reached = "elapsed"
                traversal_truncated += int(not candidate_stats["traversal_truncated"])
                stop_discovery = True
                break
            item = (mtime, identity, path, source_path)
            if len(file_heap) < MAX_TOTAL_TRANSCRIPT_FILES:
                heapq.heappush(file_heap, item)
                seen_files.add(identity)
            elif item > file_heap[0]:
                removed = heapq.heapreplace(file_heap, item)
                seen_files.remove(removed[1])
                seen_files.add(identity)
                global_limit_drops += 1
            else:
                global_limit_drops += 1
        if stop_discovery:
            break
        budget_reason = (
            "entries"
            if candidate_stats["entry_budget_reached"] or remaining_directory_entries == 0
            else "directories"
            if candidate_stats["directory_budget_reached"] or remaining_directories == 0
            else "none"
        )
        if budget_reason != "none" and source_index + 1 < len(source_paths):
            discovery_limit_reached = budget_reason
            traversal_truncated += int(not candidate_stats["traversal_truncated"])
            break
    files_truncated += global_limit_drops
    files = [(item[2], item[3]) for item in sorted(file_heap, key=lambda item: (item[0], item[1]), reverse=True)]
    encoded_events: list[tuple[str, str, bytes]] = []
    event_count = 0
    snapshot_bytes = 0
    source_counts: Counter[str] = Counter()
    transcript_read_states: Counter[str] = Counter()
    invalid_transcript_rows = 0
    oversized_transcript_lines = 0
    skipped_unknown = 0
    aggregate_input_bytes = 0
    aggregate_rows_scanned = 0
    files_processed = 0
    for path, source_root in files:
        if aggregate_limit_reached == "elapsed" or time.monotonic() >= deadline:
            aggregate_limit_reached = "elapsed"
            break
        read_diagnostics: dict[str, Any] = {}
        parsed_rows = list(
            _iter_jsonl(
                path,
                source_root,
                read_diagnostics,
                byte_budget=MAX_TRANSCRIPT_AGGREGATE_BYTES - aggregate_input_bytes,
                row_budget=MAX_TRANSCRIPT_AGGREGATE_ROWS - aggregate_rows_scanned,
                deadline=deadline,
            )
        )
        if time.monotonic() >= deadline:
            read_diagnostics["state"] = "aggregate_time_limit_reached"
        read_state = str(read_diagnostics.get("state") or "unknown")
        transcript_read_states[read_state] += 1
        invalid_transcript_rows += int(read_diagnostics.get("invalid_rows") or 0)
        oversized_transcript_lines += int(read_diagnostics.get("oversized_lines") or 0)
        aggregate_input_bytes += int(read_diagnostics.get("bytes_read") or 0)
        aggregate_rows_scanned += int(read_diagnostics.get("rows_seen") or 0)
        if read_state == "aggregate_byte_limit_reached":
            aggregate_limit_reached = "bytes"
            break
        if read_state == "aggregate_row_limit_reached":
            aggregate_limit_reached = "rows"
            break
        if read_state == "aggregate_time_limit_reached":
            aggregate_limit_reached = "elapsed"
            break
        detected = source if source != "auto" else _detect_source_from_rows(parsed_rows)
        if detected == "claude":
            extracted = _scan_claude(path, root_path, key, all_projects, source_root, parsed_rows)
        elif detected == "codex":
            extracted = _scan_codex(path, root_path, key, all_projects, source_root, parsed_rows)
        else:
            files_processed += 1
            skipped_unknown += 1
            continue
        if time.monotonic() >= deadline:
            aggregate_limit_reached = "elapsed"
            break
        if extracted:
            pending_events: list[tuple[str, str, bytes]] = []
            pending_snapshot_bytes = 0
            encoding_elapsed = False
            for event in extracted:
                if time.monotonic() >= deadline:
                    encoding_elapsed = True
                    break
                if event_count + len(pending_events) + 1 > MAX_SNAPSHOT_EVENTS:
                    raise TranscriptBackfillSafetyError(
                        f"derived transcript snapshot exceeds the {MAX_SNAPSHOT_EVENTS}-event limit"
                    )
                encoded = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                if time.monotonic() >= deadline:
                    encoding_elapsed = True
                    break
                pending_snapshot_bytes += len(encoded)
                if snapshot_bytes + pending_snapshot_bytes > MAX_SNAPSHOT_BYTES:
                    raise TranscriptBackfillSafetyError(
                        f"derived transcript snapshot exceeds the {MAX_SNAPSHOT_BYTES}-byte limit"
                    )
                pending_events.append((str(event.get("ts") or ""), str(event.get("event_id") or ""), encoded))
            if encoding_elapsed:
                aggregate_limit_reached = "elapsed"
                break
            event_count += len(pending_events)
            snapshot_bytes += pending_snapshot_bytes
            if not dry_run:
                encoded_events.extend(pending_events)
            source_counts[detected] += 1
        files_processed += 1
    state_dir = root_path / ".roam" if dry_run else _private_state_directory(root_path, create=True)
    output = state_dir / OUTPUT_NAME
    if not dry_run:
        encoded_events.sort(key=lambda item: (item[0], item[1]))
        payload = b"".join(item[2] for item in encoded_events)

        def prepare_private_temp(descriptor: int, temporary: str) -> None:
            if not ensure_owner_only_file_descriptor(descriptor, temporary):
                raise TranscriptBackfillSafetyError(
                    f"derived transcript snapshot temp could not be restricted to the current user: {temporary}"
                )

        def validate_destination() -> None:
            _private_state_directory(root_path, create=False)
            _private_file_state(
                output,
                label="derived transcript snapshot",
                max_bytes=MAX_SNAPSHOT_BYTES,
                allow_missing=True,
            )

        with pinned_owner_only_directory(state_dir):
            atomic_write_bytes(
                output,
                payload,
                prepare_temp_fd=prepare_private_temp,
                before_replace=validate_destination,
                durable=True,
                create_parents=False,
                secure_parent=True,
            )
            _private_file_state(
                output,
                label="derived transcript snapshot",
                max_bytes=MAX_SNAPSHOT_BYTES,
                allow_missing=False,
            )
    return {
        "state": "dry_run" if dry_run else "written",
        "output": str(output),
        "files_considered": len(files),
        "files_processed": files_processed,
        "input_files_skipped": len(files) - files_processed,
        "candidate_files_seen": candidate_files_seen,
        "files_truncated": files_truncated,
        "oversized_files": oversized_files,
        "directories_scanned": directories_scanned,
        "directory_entries_scanned": directory_entries_scanned,
        "traversal_truncated": traversal_truncated,
        "discovery_limit_reached": discovery_limit_reached,
        "source_directories": [str(path) for path in source_paths],
        "files_with_episodes": sum(source_counts.values()),
        "files_by_source": dict(sorted(source_counts.items())),
        "unknown_format_files": skipped_unknown,
        "transcript_read_states": dict(sorted(transcript_read_states.items())),
        "degraded_transcript_files": sum(count for state, count in transcript_read_states.items() if state != "ok"),
        "invalid_transcript_rows": invalid_transcript_rows,
        "oversized_transcript_lines": oversized_transcript_lines,
        "aggregate_input_bytes": aggregate_input_bytes,
        "aggregate_rows_scanned": aggregate_rows_scanned,
        "aggregate_limit_reached": aggregate_limit_reached,
        "episodes": event_count // 2,
        "events": event_count,
        "snapshot_bytes": snapshot_bytes,
        "resource_limits": {
            "max_sources": MAX_TRANSCRIPT_SOURCES,
            "max_files_per_source": effective_max_files,
            "max_total_files": MAX_TOTAL_TRANSCRIPT_FILES,
            "max_directories": MAX_TRANSCRIPT_DIRECTORIES,
            "max_directories_global": MAX_TRANSCRIPT_DIRECTORIES,
            "max_directory_entries_per_source": MAX_TRANSCRIPT_DIRECTORY_ENTRIES,
            "max_directory_entries_global": MAX_TRANSCRIPT_DIRECTORY_ENTRIES,
            "max_aggregate_input_bytes": MAX_TRANSCRIPT_AGGREGATE_BYTES,
            "max_aggregate_rows": MAX_TRANSCRIPT_AGGREGATE_ROWS,
            "max_elapsed_seconds": MAX_TRANSCRIPT_ELAPSED_SECONDS,
            "max_transcript_bytes": MAX_TRANSCRIPT_BYTES,
            "max_transcript_line_bytes": MAX_TRANSCRIPT_LINE_BYTES,
            "max_rows_per_file": MAX_TRANSCRIPT_ROWS_PER_FILE,
            "max_snapshot_bytes": MAX_SNAPSHOT_BYTES,
            "max_snapshot_events": MAX_SNAPSHOT_EVENTS,
            "max_events_per_file": MAX_TRANSCRIPT_EVENTS_PER_FILE,
        },
        "privacy_contract": {
            "prompt_text_persisted": False,
            "assistant_text_persisted": False,
            "raw_commands_persisted": False,
            "sanitized_command_templates_persisted": True,
            "paths_persisted": False,
            "tool_arguments_persisted": False,
            "keyed_fingerprints": True,
            "closed_intent_archetypes_persisted": True,
            "phase_and_friction_counts_persisted": True,
            "sanitized_template_outcomes_persisted": True,
            "search_no_results_separated_from_failures": True,
            "closed_failure_classes_persisted": True,
            "closed_shell_identifier_vocabularies": True,
            "resource_bounds_disclosed": True,
            "tool_result_content_persisted": False,
        },
    }
