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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_FILENAME = "verdict-log.jsonl"

# POSIX guarantees atomic O_APPEND writes <= PIPE_BUF (typically 4096B).
# Beyond this, concurrent appends from parallel guard-pr runs can interleave.
_ATOMIC_APPEND_LIMIT = 4096


def log_path_for(root: Path) -> Path:
    """Return the canonical verdict-log path under .roam/."""
    return root / ".roam" / LOG_FILENAME


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

    Concurrency-safe: uses POSIX `O_APPEND` + a single `os.write()` so
    parallel guard-pr runs from different processes cannot interleave
    each other's lines (atomic up to PIPE_BUF, ~4096 bytes).

    Never raises — log failures are non-fatal for the verdict command itself.
    """
    try:
        path = log_path_for(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8")
        if len(line) > _ATOMIC_APPEND_LIMIT:
            # Oversize line — concurrent writes may interleave. Bail
            # rather than write a record that could corrupt the log.
            return False
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except (OSError, ValueError):
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
    path = log_path_for(root)
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
        try:
            _os.unlink(tmp_path)
        except OSError:
            pass
        out["removed"] = 0  # rollback signal
        out["error"] = f"write_failed: {e}"
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
    path = log_path_for(root)
    result: dict[str, Any] = {
        "entries": [],
        "error": None,
        "file_present": path.is_file(),
        "malformed_lines": 0,
    }
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
