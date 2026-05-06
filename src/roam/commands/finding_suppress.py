"""Suppression mechanism for math / over-fetch / missing-index / auth-gaps findings.

Three paths, layered most-specific-first:

1. **Inline annotation** — comment on the symbol's line containing
   ``roam: ignore-math[task-id]`` (or ``ignore-over-fetch[...]``,
   ``ignore-missing-index[...]``, ``ignore-auth-gaps[...]``). Per-line,
   per-task-id. Survives reindex.

2. **`.roamignore-findings`** — repo-level YAML/JSON-ish config with
   ``rules:`` blocks that match by task_id + path glob. Useful for
   project-wide carve-outs (e.g. "every Vue 3 composable can have
   queryClient.getQueryData inside loops").

3. **`roam suppress`** — CLI command that records a one-off suppression
   in ``.roam/suppressions.json`` keyed by ``finding_id`` (deterministic
   hash of task_id + location + symbol_name). Use when a single finding
   needs an audit-trail-friendly carve-out with a reason.

Resolved suppressions surface in the envelope under
``summary.suppressed_count`` + each suppressed finding gets
``finding["suppressed"] = {"source": ..., "reason": ...}`` instead of
being dropped silently — so verify can catch over-suppression.
"""

from __future__ import annotations

import hashlib
import json as _json
import re
from pathlib import Path

DEFAULT_SUPPRESSIONS_PATH = Path(".roam") / "suppressions.json"
DEFAULT_IGNORE_FINDINGS_PATH = Path(".roamignore-findings")

# Recognised inline annotation forms. The task-id list is comma-separated.
# All four detector commands share the prefix `ignore-` for symmetry.
_INLINE_ANNOTATION_RE = re.compile(
    r"roam\s*:\s*ignore-(?P<command>math|over-fetch|missing-index|auth-gaps)"
    r"(?:\[(?P<ids>[^\]]+)\])?",
    re.IGNORECASE,
)


def finding_id(task_id: str, location: str, symbol_name: str) -> str:
    """Deterministic short ID for one finding.

    SHA-256 of (task_id|location|symbol_name) truncated to 16 chars —
    stable across runs as long as the location and symbol don't move.
    Used by ``roam suppress`` to point at a specific finding.
    """
    blob = f"{task_id}|{location}|{symbol_name}".encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _line_at(path: Path, line: int) -> str:
    """Read line ``line`` (1-indexed) from ``path``; '' on any error."""
    if line < 1:
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, content in enumerate(f, 1):
                if i == line:
                    return content
                if i > line:
                    break
    except OSError:
        return ""
    return ""


def _inline_match(line_text: str, command: str, task_id: str) -> bool:
    """Does the line carry an inline `roam: ignore-<command>[task_id]` annotation?

    Bare `ignore-<command>` (no `[task_id]`) suppresses all task-ids for that
    command on that line. Otherwise the task-id must appear in the comma-list.
    """
    if not line_text:
        return False
    cmd = command.lower()
    tid = task_id.lower()
    for m in _INLINE_ANNOTATION_RE.finditer(line_text):
        if m.group("command").lower() != cmd:
            continue
        ids_blob = (m.group("ids") or "").strip().lower()
        if not ids_blob:
            return True  # bare ignore-<command> covers all task ids on this line
        ids = {part.strip() for part in ids_blob.split(",") if part.strip()}
        if tid in ids or "*" in ids:
            return True
    return False


def _load_ignore_findings_file(path: Path) -> list[dict]:
    """Load `.roamignore-findings` from ``path``. Returns ``[]`` on any error.

    Format (YAML if PyYAML present, else json-shaped fallback):

    ```yaml
    rules:
      - task_id: io-in-loop
        path_glob: "src/composables/**/*.ts"
        reason: "TanStack Query factories use queryClient.getQueryData in loops"
      - task_id: branching-recursion
        path_glob: "src/utils/object-diff.ts"
    ```
    """
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        import yaml

        data = yaml.safe_load(text) or {}
    except ImportError:
        # No PyYAML: assume strict JSON
        try:
            data = _json.loads(text)
        except _json.JSONDecodeError:
            return []
    except Exception:  # noqa: BLE001 — malformed YAML never crashes the analyser
        return []
    rules = data.get("rules") if isinstance(data, dict) else []
    if not isinstance(rules, list):
        return []
    return [r for r in rules if isinstance(r, dict)]


def _path_matches_glob(file_path: str, glob: str) -> bool:
    """fnmatch-based glob match. Tolerates Windows backslashes."""
    import fnmatch as _fn

    norm = file_path.replace("\\", "/")
    return _fn.fnmatch(norm, glob)


def _load_per_finding_suppressions(path: Path) -> dict[str, dict]:
    """Load `.roam/suppressions.json` keyed by finding_id."""
    if not path.exists():
        return {}
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for fid, entry in data.items():
        if isinstance(entry, dict):
            out[str(fid)] = entry
    return out


def annotate_with_suppression(
    findings: list[dict],
    *,
    command: str,
    project_root: Path | None = None,
    ignore_findings_path: Path | None = None,
    suppressions_path: Path | None = None,
) -> tuple[list[dict], int]:
    """Mark findings that match a suppression rule. Returns ``(annotated, suppressed_count)``.

    Suppressed findings stay in the list with ``finding["suppressed"] = {...}``
    so consumers can choose to filter them out in text output but keep them
    in JSON for verification / audit.

    Decision: don't drop silently — that loses the signal that a previously-
    surfaced finding was suppressed by a (now-stale) rule.
    """
    project_root = project_root or Path(".")
    ignore_path = ignore_findings_path or (project_root / DEFAULT_IGNORE_FINDINGS_PATH.name)
    suppress_path = suppressions_path or (project_root / DEFAULT_SUPPRESSIONS_PATH)

    file_rules = _load_ignore_findings_file(ignore_path)
    per_finding = _load_per_finding_suppressions(suppress_path)

    suppressed = 0
    out: list[dict] = []
    for f in findings:
        # Skip if already annotated (idempotent — multiple commands may share
        # the same envelope path).
        if f.get("suppressed"):
            out.append(f)
            continue

        location = f.get("location", "")
        task_id = f.get("task_id", "")
        symbol_name = f.get("symbol_name", "") or f.get("symbol_id", "")
        file_path = location.split(":", 1)[0] if ":" in location else location

        # Stamp a deterministic finding_id so `roam suppress` can target it.
        f["finding_id"] = finding_id(task_id, location, str(symbol_name))

        # 1. Per-finding suppression (most specific)
        if f["finding_id"] in per_finding:
            entry = per_finding[f["finding_id"]]
            f["suppressed"] = {
                "source": "suppressions.json",
                "reason": entry.get("reason", ""),
                "added_at": entry.get("added_at"),
            }
            suppressed += 1
            out.append(f)
            continue

        # 2. .roamignore-findings file rules
        matched_rule = None
        for rule in file_rules:
            if rule.get("task_id") and rule["task_id"] != task_id:
                continue
            glob = rule.get("path_glob")
            if glob and not _path_matches_glob(file_path, glob):
                continue
            matched_rule = rule
            break
        if matched_rule:
            f["suppressed"] = {
                "source": ".roamignore-findings",
                "reason": matched_rule.get("reason", ""),
                "rule_path_glob": matched_rule.get("path_glob"),
            }
            suppressed += 1
            out.append(f)
            continue

        # 3. Inline annotation on the line
        try:
            line_no = int(location.rsplit(":", 1)[1]) if ":" in location else 0
        except (ValueError, IndexError):
            line_no = 0
        if line_no > 0 and file_path:
            line_text = _line_at(project_root / file_path, line_no)
            # Also check the symbol-line if available (annotations may live
            # one line above the actual match).
            sym_line_text = ""
            sym_line = f.get("symbol_line")
            if sym_line and sym_line != line_no:
                sym_line_text = _line_at(project_root / file_path, int(sym_line))
            for candidate in (line_text, sym_line_text):
                if _inline_match(candidate, command, task_id):
                    f["suppressed"] = {
                        "source": "inline-annotation",
                        "reason": candidate.strip()[:200],
                    }
                    suppressed += 1
                    break
        out.append(f)

    return out, suppressed


def filter_suppressed(findings: list[dict]) -> list[dict]:
    """Drop suppressed findings. For consumers that don't want to see them."""
    return [f for f in findings if not f.get("suppressed")]
