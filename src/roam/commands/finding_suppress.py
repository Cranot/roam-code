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
import re
from pathlib import Path
from typing import TYPE_CHECKING

from roam.output.formatter import WarningsOut

if TYPE_CHECKING:
    from roam.policy.suppression_v2 import FindingIdSuppression

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


def _parse_simple_ignore_findings_yaml(text: str) -> dict:
    """Minimal YAML parser for .roamignore-findings — no PyYAML required.

    Handles the documented shape only:

        rules:
          - task_id: io-in-loop
            path_glob: "src/composables/**/*.ts"
            reason: "..."
          - task_id: branching-recursion
            path_glob: "src/utils/object-diff.ts"

    Anything more complex (anchors, multi-line strings, nested lists)
    needs real PyYAML. Returns ``{}`` on shapes we can't recognise so
    callers fall through to a clean empty-rules state.
    """
    rules: list[dict] = []
    current: dict | None = None
    in_rules_block = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "rules:" or stripped.startswith("rules:"):
            in_rules_block = True
            continue
        if not in_rules_block:
            continue
        if stripped.startswith("- "):
            if current:
                rules.append(current)
            current = {}
            stripped = stripped[2:].strip()
            # First key on the same line as `-` is the common shape.
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                current[k.strip()] = v.strip().strip('"').strip("'")
        elif current is not None and ":" in stripped:
            k, _, v = stripped.partition(":")
            current[k.strip()] = v.strip().strip('"').strip("'")
    if current:
        rules.append(current)
    return {"rules": rules} if rules else {}


def _load_ignore_findings_file(
    path: Path,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
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

    W706 (Pattern 2 — silent fallback): when *warnings_out* is supplied as
    a ``list[str]``, every silent-fallback path (file unreadable, malformed
    YAML/JSON root, missing ``rules`` key, non-list ``rules``, non-dict
    entries) appends an actionable warning naming the path, the failure
    shape, and the resolution. Pre-W706 callers that don't supply
    ``warnings_out`` retain the byte-identical silent-empty-list behaviour
    so the existing happy-path consumers (cmd_math.py) keep emitting
    byte-identical envelopes when the suppression file is well-formed.

    W1019a (Phase 2 of the YAML-loader consolidation): the file-read +
    YAML parse + no-PyYAML fallback + root-type check now live in
    :func:`roam.commands._yaml_loader.load_yaml_with_warnings`. The
    per-callsite rules-extraction and per-entry validation stays here —
    the helper owns I/O + parser-fallback shape; the schema vocabulary
    stays on the callsite that owns the on-disk format.
    """
    from roam.commands._yaml_loader import load_yaml_with_warnings

    path_str = str(path)
    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data = load_yaml_with_warnings(
        path,
        tiny_parser=_parse_simple_ignore_findings_yaml,
        config_label="ignore-findings",
        warnings_out=warnings_out,
    )
    if data is None:
        # Missing file — default state, no warning emitted by the helper.
        return []
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the failure (read error / malformed
        # YAML / wrong root type / tiny-parser fallback). Propagate the
        # empty result without piling on a second "no `rules:` key"
        # warning that would just confuse the caller.
        return []
    # ``data`` is a Mapping when ``allow_list_root`` is left at False
    # (the default we want for `.roamignore-findings`). The helper's
    # root-type check guarantees this; the assert keeps the type checker
    # happy on the post-helper rules-extraction logic.
    assert isinstance(data, dict)
    if "rules" not in data:
        if warnings_out is not None:
            warnings_out.append(
                f"ignore-findings: {path_str!r} has no `rules:` key. "
                f"Expected shape: `rules:` followed by a list of "
                f"`{{task_id, path_glob, reason}}` entries."
            )
        return []
    rules = data.get("rules")
    if not isinstance(rules, list):
        if warnings_out is not None:
            warnings_out.append(
                f"ignore-findings: {path_str!r} `rules` is "
                f"{type(rules).__name__!r}, expected a list. Treating as "
                f"empty rules."
            )
        return []
    out: list[dict] = []
    for idx, r in enumerate(rules):
        if not isinstance(r, dict):
            if warnings_out is not None:
                warnings_out.append(
                    f"ignore-findings: {path_str!r} rules[{idx}] is "
                    f"{type(r).__name__!r}, expected a mapping with "
                    f"`task_id` / `path_glob` keys. Skipping entry."
                )
            continue
        if not r.get("task_id") and not r.get("path_glob"):
            # An entry that has neither task_id nor path_glob would match
            # every finding silently — surface that as a structured warning
            # so an agent can see the rule is over-broad / malformed before
            # it suppresses signal in bulk.
            if warnings_out is not None:
                warnings_out.append(
                    f"ignore-findings: {path_str!r} rules[{idx}] has neither "
                    f"`task_id` nor `path_glob` and would match every finding. "
                    f"Skipping entry; add at least one filter."
                )
            continue
        out.append(r)
    return out


def _path_matches_glob(file_path: str, glob: str) -> bool:
    """fnmatch-based glob match. Tolerates Windows backslashes."""
    import fnmatch as _fn

    norm = file_path.replace("\\", "/")
    return _fn.fnmatch(norm, glob)


def _load_per_finding_suppressions(
    path: Path,
    *,
    warnings_out: WarningsOut = None,
) -> dict[str, dict]:
    """Load `.roam/suppressions.json` keyed by finding_id.

    W1009 (Pattern 2 — silent fallback, mirror of W706's
    :func:`_load_ignore_findings_file`): when *warnings_out* is supplied
    as a ``list[str]``, every silent-fallback path (file unreadable /
    OSError, malformed JSON, non-dict root, non-dict entry value)
    appends an actionable warning naming the path, the failure shape,
    and the resolution. Pre-W1009 callers that don't supply
    ``warnings_out`` retain the byte-identical silent-empty-dict
    behaviour so existing happy-path consumers keep emitting
    byte-identical envelopes when ``.roam/suppressions.json`` is
    well-formed.

    W1019e (Phase 2 of the YAML-loader consolidation, mirror of
    W1019a's :func:`_load_ignore_findings_file`): the file-read +
    JSON parse + root-type check now live in
    :func:`roam.commands._yaml_loader.load_yaml_with_warnings`, with
    ``parse_error_label="JSON"`` (W1035) so the malformed-input
    warning body says "malformed JSON" — matching the JSON-shaped
    on-disk format. The per-entry validation (non-dict entry value)
    stays inline — the helper owns I/O + parser-fallback shape; the
    schema vocabulary stays on the callsite that owns the on-disk
    format.
    """
    from roam.commands._yaml_loader import load_yaml_with_warnings

    path_str = str(path)
    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data = load_yaml_with_warnings(
        path,
        config_label="per-finding-suppressions",
        parse_error_label="JSON",  # W1035: this file is JSON-shaped, not YAML
        warnings_out=warnings_out,
    )
    if data is None:
        # Missing file — default state, no warning emitted by the helper.
        return {}
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the failure (read error / malformed
        # JSON / wrong root type). Propagate the empty result without
        # piling on a second warning.
        return {}
    # ``data`` is a Mapping when ``allow_list_root`` is left at False
    # (the default we want for ``.roam/suppressions.json``). The
    # helper's root-type check guarantees this; the assert keeps the
    # type checker happy on the post-helper per-entry validation logic.
    assert isinstance(data, dict)
    out: dict[str, dict] = {}
    for fid, entry in data.items():
        if not isinstance(entry, dict):
            if warnings_out is not None:
                warnings_out.append(
                    f"per-finding-suppressions: {path_str!r} entry "
                    f"{str(fid)!r} is {type(entry).__name__!r}, expected a "
                    f"mapping with `reason` / `added_at` keys. Skipping entry."
                )
            continue
        out[str(fid)] = entry
    return out


def load_per_finding_suppressions_typed(
    path: Path,
    *,
    warnings_out: WarningsOut = None,
) -> list[FindingIdSuppression]:
    """Typed counterpart of :func:`_load_per_finding_suppressions` (W723 Phase B-b).

    Returns the same on-disk rows as :class:`FindingIdSuppression` instances
    instead of a raw ``{finding_id: dict}`` mapping. The legacy dict-keyed
    loader stays the canonical entry point until every caller migrates —
    this function is the bridge new code should reach for.

    Mirrors the Phase A pattern shipped in
    :func:`roam.commands.suppression.load_suppressions_typed` and the
    Phase B-a pattern shipped in
    :func:`roam.commands.smells_suppress.load_smells_suppressions_typed`.

    Entries without the SARIF projection fields (``rule_id`` / ``location``)
    are still surfaced — the dict-keyed reader matches them by finding_id
    hash. SARIF's projection-only view lives in
    :func:`roam.output.sarif._load_suppressions_typed`.

    W1017 (Pattern 2 — silent fallback): when *warnings_out* is supplied
    as a ``list[str]``, it is threaded through to
    :func:`_load_per_finding_suppressions` so the typed surface receives
    the same actionable warnings on malformed input that the dict-keyed
    surface does (W1009). Pre-W1017 callers that don't supply
    ``warnings_out`` retain byte-identical silent-empty-list behaviour.
    """
    # Local import keeps the policy package out of the import chain for
    # callers that don't touch the typed surface.
    from roam.policy.suppression_v2 import FindingIdSuppression

    raw = _load_per_finding_suppressions(path, warnings_out=warnings_out)
    return [FindingIdSuppression.from_dict(fid, entry, warnings_out=warnings_out) for fid, entry in raw.items()]


def annotate_with_suppression(
    findings: list[dict],
    *,
    command: str,
    project_root: Path | None = None,
    ignore_findings_path: Path | None = None,
    suppressions_path: Path | None = None,
    warnings_out: WarningsOut = None,
) -> tuple[list[dict], int]:
    """Mark findings that match a suppression rule. Returns ``(annotated, suppressed_count)``.

    Suppressed findings stay in the list with ``finding["suppressed"] = {...}``
    so consumers can choose to filter them out in text output but keep them
    in JSON for verification / audit.

    Decision: don't drop silently — that loses the signal that a previously-
    surfaced finding was suppressed by a (now-stale) rule.

    W706 (Pattern 2 — silent fallback): when *warnings_out* is supplied,
    malformed `.roamignore-findings` entries (missing/typed-wrong keys,
    malformed YAML/JSON root, unreadable file) surface as structured
    warnings instead of silently returning an empty rules list. Callers
    can drain the accumulator into ``summary.warnings_out`` + flip
    ``summary.partial_success`` so the agent sees WHY no rules loaded.

    W1009 extends the same plumb-through to the sibling
    `.roam/suppressions.json` loader so per-finding-id suppression files
    surface their failure shape on the same accumulator.
    """
    project_root = project_root or Path(".")
    ignore_path = ignore_findings_path or (project_root / DEFAULT_IGNORE_FINDINGS_PATH.name)
    suppress_path = suppressions_path or (project_root / DEFAULT_SUPPRESSIONS_PATH)

    file_rules = _load_ignore_findings_file(ignore_path, warnings_out=warnings_out)
    per_finding = _load_per_finding_suppressions(suppress_path, warnings_out=warnings_out)

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
