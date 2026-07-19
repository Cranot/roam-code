"""Persistent verdict log — append-only JSONL of past verdicts.

Every `roam guard-pr` run can append a one-line summary of the verdict
to `.roam/verdict-log.jsonl`. The log:

  * Enables `roam guard-history` to render fast (no re-composing per bundle)
  * Provides an audit trail (who ran what when, what was the verdict)
  * Survives bundle file deletion / rotation

JSONL shape per line:
```json
{"ts": "2026-05-30T01:00:00Z", "branch": "main", "verdict": "blocked",
 "bundle": ".roam/pr-bundles/main.json", "head_sha": "abc...",
 "changed_files": 46, "required": 4, "executed": 0, "missing": 4,
 "reasons": [{"code": "required_checks_not_run", ...}], "intent": "..."}
```

Best-effort: append-only, never raises, log corruption falls back gracefully.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from roam.observability import log_swallowed

LOG_FILENAME = "verdict-log.jsonl"

# Keep each audit record bounded even though append/rotate now share a lock.
# This prevents an untrusted verdict payload from becoming an unbounded write.
_ATOMIC_APPEND_LIMIT = 4096
_LOG_LOCK_TIMEOUT_SECONDS = 10.0
_LOG_LOCK_RETRY_SECONDS = 0.01
_LOG_THREAD_LOCK = threading.RLock()


def log_path_for(root: Path) -> Path:
    """Return the canonical verdict-log path under .roam/."""
    return root / ".roam" / LOG_FILENAME


def _is_reparse_point(path: Path) -> bool:
    """Return True for a symlink or Windows junction/reparse directory."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _validate_existing_regular_file(path: Path, *, label: str) -> None:
    """Reject links, device nodes, and hard-linked control-plane files."""
    if _is_reparse_point(path):
        raise OSError(f"unsafe {label}: links are not accepted")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"unsafe {label}: expected a regular file")
    if info.st_nlink != 1:
        raise OSError(f"unsafe {label}: hard-linked files are not accepted")


def _validated_log_path(root: Path, *, create_parent: bool) -> Path:
    """Return a contained, concrete ``.roam/verdict-log.jsonl`` path."""
    root_path = Path(root)
    resolved_root = root_path.resolve(strict=False)
    control_dir = root_path / ".roam"
    if _is_reparse_point(control_dir):
        raise OSError("unsafe verdict log root: .roam is a link or junction")
    if control_dir.exists() and not control_dir.is_dir():
        raise OSError("unsafe verdict log root: .roam is not a directory")
    if create_parent:
        control_dir.mkdir(parents=True, exist_ok=True)
    resolved_control = control_dir.resolve(strict=False)
    expected_control = resolved_root / ".roam"
    if resolved_control != expected_control:
        raise OSError("unsafe verdict log root: .roam escaped the repository")
    path = control_dir / LOG_FILENAME
    _validate_existing_regular_file(path, label="verdict log")
    return path


def _open_regular_fd(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open one regular, single-link file without following POSIX symlinks."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    binary = getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags | nofollow | binary, mode)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("unsafe control-plane file descriptor")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_lock_file(path: Path):
    """Securely create or open the persistent cross-process lock file."""
    try:
        fd = _open_regular_fd(path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        _validate_existing_regular_file(path, label="verdict log lock")
        fd = _open_regular_fd(path, os.O_RDWR)
    return os.fdopen(fd, "r+b", buffering=0)


def _lock_file_nonblocking(lock_file) -> bool:
    """Try one OS-level exclusive lock acquisition."""
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"\0")
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock_file(lock_file) -> None:
    """Release an OS-level lock; descriptor close remains the final backstop."""
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive_log_lock(root: Path):
    """Serialize append and rotate across threads and independent processes."""
    with _LOG_THREAD_LOCK:
        path = _validated_log_path(root, create_parent=True)
        lock_path = path.with_name(path.name + ".lock")
        _validate_existing_regular_file(lock_path, label="verdict log lock")
        with _open_lock_file(lock_path) as lock_file:
            deadline = time.monotonic() + _LOG_LOCK_TIMEOUT_SECONDS
            while not _lock_file_nonblocking(lock_file):
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out acquiring verdict log lock")
                time.sleep(_LOG_LOCK_RETRY_SECONDS)
            try:
                yield path
            finally:
                try:
                    _unlock_file(lock_file)
                except OSError as exc:
                    # Closing the descriptor releases the OS lock. Preserve the
                    # caller's original result while leaving an opt-in trace.
                    log_swallowed("guard_log._exclusive_log_lock.unlock", exc)


def _append_all(path: Path, line: bytes) -> None:
    """Append all bytes while the caller holds ``_exclusive_log_lock``."""
    try:
        fd = _open_regular_fd(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        _validate_existing_regular_file(path, label="verdict log")
        fd = _open_regular_fd(path, os.O_WRONLY | os.O_APPEND, 0o644)
    try:
        remaining = memoryview(line)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("verdict log append made no progress")
            remaining = remaining[written:]
    finally:
        os.close(fd)


def build_log_entry(
    *,
    v1: dict[str, Any],
    bundle_path: Path,
    branch: str | None = None,
) -> dict[str, Any]:
    """Build one log line from a composed AgentChangeProofBundle v1 dict."""
    verdict = v1.get("verdict") or {}
    contract = v1.get("verification_contract") or {}
    repo = v1.get("repo") or {}
    return {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "branch": branch or bundle_path.stem.replace("__", "/"),
        "bundle": str(bundle_path),
        "verdict": verdict.get("value", "pass"),
        "head_sha": repo.get("head_sha"),
        "intent": (v1.get("run") or {}).get("agent") or _intent_from_bundle(v1),
        "changed_files": len(v1.get("changed_files") or []),
        "required": len(contract.get("required") or []),
        "executed": len(v1.get("executed_checks") or []),
        "missing": len(v1.get("missing_checks") or []),
        "risk_level": (v1.get("risk") or {}).get("level", "low"),
        "reasons": [{"code": r.get("code"), "count": r.get("count", 1)} for r in (verdict.get("reasons") or [])[:10]],
    }


def _intent_from_bundle(v1: dict[str, Any]) -> str | None:
    """Best-effort intent recovery — try a few likely keys."""
    for key in ("intent", "run_intent"):
        val = v1.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def append_log_entry(root: Path, entry: dict[str, Any]) -> bool:
    """Append one line to `.roam/verdict-log.jsonl`. Returns True on success.

    Concurrency-safe on POSIX and Windows: append and rotate share a bounded
    advisory lock, while a process-local lock covers same-process threads.
    The data descriptor is opened no-follow where the OS supports it and the
    writer rejects redirected or hard-linked control-plane files.

    Never raises — log failures are non-fatal for the verdict command itself.
    """
    try:
        line = (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8")
        if len(line) > _ATOMIC_APPEND_LIMIT:
            # Preserve the historical bounded-record contract. Huge audit
            # records are rejected rather than turning the log into an
            # unbounded disk-write surface.
            return False
        with _exclusive_log_lock(root) as path:
            _append_all(path, line)
        return True
    except (OSError, TimeoutError, ValueError):
        return False


def rotate_log(root: Path, keep: int) -> dict[str, Any]:
    """Truncate `.roam/verdict-log.jsonl` to its last `keep` entries.

    Public programmatic API (W26) — the imperative form of `roam guard-clean`.
    Atomic rewrite via temp-file + `os.replace`. Concurrent appenders never
    see a partial file. No-op when the log already has <= `keep` entries.

    Returns: {kept, removed, total_before, error: None|str}. Never raises;
    `error` is non-None when the read/write degraded silently — caller can
    distinguish "log was empty" from "couldn't read it" (W33b H4 fix).
    """
    import os as _os
    import tempfile

    out: dict[str, Any] = {
        "kept": 0,
        "removed": 0,
        "total_before": 0,
        "error": None,
    }
    if keep < 0:
        out["error"] = "invalid_keep"
        return out
    try:
        with _exclusive_log_lock(root) as path:
            if not path.is_file():
                return out
            try:
                raw = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except OSError as e:
                out["error"] = f"read_failed: {e}"
                return out
            out["total_before"] = len(raw)
            to_keep = raw[-keep:] if keep > 0 else []
            out["kept"] = len(to_keep)
            out["removed"] = len(raw) - len(to_keep)
            if out["removed"] == 0:
                return out
            tmp_path: str | None = None
            try:
                fd, tmp_path = tempfile.mkstemp(
                    prefix=path.name + ".",
                    suffix=".tmp",
                    dir=str(path.parent),
                )
                with _os.fdopen(fd, "w", encoding="utf-8") as f:
                    for line in to_keep:
                        f.write(line + "\n")
                _os.replace(tmp_path, str(path))
            except OSError as e:
                if tmp_path is not None:
                    try:
                        _os.unlink(tmp_path)
                    except OSError:
                        pass
                out["removed"] = 0  # rollback signal
                out["error"] = f"write_failed: {e}"
    except (OSError, TimeoutError) as e:
        out["error"] = f"lock_failed: {e}"
    return out


def read_log_entries(root: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Read verdict log entries. Most recent first.

    Returns [] if the log doesn't exist or is unreadable. Skips malformed
    lines silently (best-effort).

    NOTE: For callers that need to distinguish "no log file" from "read
    failed", use `read_log_entries_detail()` (W33b H4).
    """
    return read_log_entries_detail(root, limit=limit)["entries"]


def read_log_entries_detail(root: Path, limit: int | None = None) -> dict[str, Any]:
    """Like `read_log_entries` but returns a detail envelope.

    Returns:
      {entries: [...], error: None|str, file_present: bool, malformed_lines: int}

    Lets the caller signal "couldn't read the log" vs "no log yet" vs
    "log present but contains N malformed lines" — previously all three
    looked identical (an empty list).
    """
    result: dict[str, Any] = {
        "entries": [],
        "error": None,
        "file_present": False,
        "malformed_lines": 0,
    }
    try:
        path = _validated_log_path(root, create_parent=False)
    except OSError as e:
        result["error"] = f"unsafe_path: {e}"
        return result
    result["file_present"] = path.is_file()
    if not result["file_present"]:
        return result
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        result["error"] = f"read_failed: {e}"
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            result["malformed_lines"] += 1
            continue
    out.reverse()
    if limit is not None:
        out = out[:limit]
    result["entries"] = out
    return result
