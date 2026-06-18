"""Load and manage .roam-suppressions.yml files for finding triage.

A suppression marks a specific finding (rule + file + optional line) as
reviewed.  Status values:

- ``safe``         -- false positive, no action needed
- ``acknowledged`` -- accepted risk, tracked intentionally
- ``wont-fix``     -- known issue, deferred indefinitely

The YAML parser is intentionally minimal (no PyYAML dependency) and
handles the structured list format used by ``.roam-suppressions.yml``.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from roam.output.formatter import WarningsOut
from roam.policy.suppression_v2 import (
    VALID_STATUSES,  # re-export for back-compat
    RuleFileSuppression,
)

# Re-exported for code that imports VALID_STATUSES from this module.
__all__ = [
    "VALID_STATUSES",
    "find_suppression",
    "is_suppressed",
    "load_suppressions",
    "load_suppressions_typed",
    "save_suppression",
    "suppression_stats",
]

# ---------------------------------------------------------------------------
# Simple YAML parser for suppression files (no PyYAML dependency)
# ---------------------------------------------------------------------------


def _parse_suppressions_yaml(text: str) -> list[dict]:
    """Parse a .roam-suppressions.yml file into a list of suppression dicts.

    Expected format::

        suppressions:
          - rule: secret-detection
            file: tests/fixtures/fake_secrets.py
            reason: Test fixtures with fake credentials
            status: safe
            author: dev@example.com
            date: 2026-02-25
          - rule: complexity-high
            file: src/roam/index/indexer.py
            line: 142
            reason: Intentionally complex pipeline
            status: acknowledged

    Returns a list of dicts, each with at least ``rule`` and ``file`` keys.
    Malformed entries (missing required fields) are silently skipped.
    """
    suppressions: list[dict] = []
    current: dict | None = None
    in_suppressions_block = False

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()

        # Skip blank lines and comments
        if not stripped or stripped.startswith("#"):
            continue

        # Detect the top-level "suppressions:" key
        if stripped == "suppressions:":
            in_suppressions_block = True
            continue

        if not in_suppressions_block:
            continue

        # A new list item starts with "- "
        if stripped.startswith("- "):
            # Save previous entry
            if current is not None:
                if "rule" in current and "file" in current:
                    suppressions.append(current)
            current = {}
            # Parse the key: value on the same line as the dash
            rest = stripped[2:].strip()
            if ":" in rest:
                key, _, val = rest.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val:
                    current[key] = _coerce_value(key, val)
        elif current is not None and ":" in stripped:
            # Continuation key: value under the current list item
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val:
                current[key] = _coerce_value(key, val)

    # Don't forget the last entry
    if current is not None and "rule" in current and "file" in current:
        suppressions.append(current)

    return suppressions


def _parse_suppressions_yaml_root_dict(text: str) -> dict:
    """Tiny-parser wrapper: text -> ``{"suppressions": [rows]}`` root dict.

    The W1032 tiny_parser for :func:`load_yaml_with_warnings`. Pure
    structural parse — no validation; the required-field check
    (``rule`` + ``file``) runs in :func:`_validate_suppression_rows`
    after the helper returns, mirroring the W1019b pattern used by
    :mod:`roam.commands.smells_suppress`.
    """
    rows: list[dict] = []
    current: dict | None = None
    in_block = False

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        if stripped == "suppressions:":
            in_block = True
            continue

        if not in_block:
            continue

        if stripped.startswith("- "):
            if current is not None:
                rows.append(current)
            current = {}
            rest = stripped[2:].strip()
            if ":" in rest:
                key, _, val = rest.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val:
                    current[key] = _coerce_value(key, val)
        elif current is not None and ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val:
                current[key] = _coerce_value(key, val)

    if current is not None:
        rows.append(current)

    return {"suppressions": rows}


def _validate_suppression_rows(
    rows: list,
    *,
    warnings_out: WarningsOut = None,
    source_path: str | None = None,
) -> list[dict]:
    """Apply required-field validation to a list of parsed suppression rows.

    Each row must declare both ``rule`` and ``file`` keys; rows that miss
    either are dropped (matching the pre-W1032 silent-skip behaviour).
    When *warnings_out* is supplied, every dropped row appends a structured
    warning naming the 1-based row index + the missing field so an agent
    can fix the file (W1032 — Pattern 2 silent-fallback).

    The validator mirrors the W995 vocabulary used by
    :mod:`roam.commands.smells_suppress` so the cross-loader warning
    shape stays consistent.
    """
    suppressions: list[dict] = []
    dropped: list[tuple[int, str]] = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            dropped.append((idx, "rule"))
            continue
        if "rule" not in row or "file" not in row:
            missing = "rule" if "rule" not in row else "file"
            dropped.append((idx, missing))
            continue
        suppressions.append(row)

    if warnings_out is not None and dropped:
        loc = source_path or ".roam-suppressions.yml"
        for idx, missing in dropped:
            warnings_out.append(
                f"suppressions: {loc!r}: entry #{idx} dropped — missing "
                f"required field {missing!r}; each row must declare both "
                f"'rule' and 'file' fields."
            )
        if len(dropped) > 1:
            warnings_out.append(
                f"suppressions: {loc!r}: dropped {len(dropped)} malformed "
                f"suppression entries total; fix the listed rows to restore "
                f"them."
            )

    return suppressions


def _coerce_value(key: str, val: str) -> object:
    """Coerce a string value to the appropriate Python type.

    The ``line`` field is parsed as an integer.  All other fields remain
    strings.
    """
    if key == "line":
        try:
            return int(val)
        except ValueError:
            return val
    return val


# ---------------------------------------------------------------------------
# Serialiser -- write suppressions back to YAML-like format
# ---------------------------------------------------------------------------


def _serialize_suppressions(suppressions: list[dict]) -> str:
    """Serialize a list of suppression dicts to .roam-suppressions.yml format."""
    lines = ["# Suppressed findings", "# Managed by: roam triage", "suppressions:"]

    for sup in suppressions:
        first = True
        # Emit fields in a stable, readable order
        for key in ("rule", "file", "symbol", "line", "reason", "status", "author", "date"):
            val = sup.get(key)
            if val is None:
                continue
            if first:
                # Only the first field of each entry gets the leading dash;
                # subsequent fields are indented continuation lines.
                lines.append(f"  - {key}: {val}")
                first = False
            else:
                lines.append(f"    {key}: {val}")

    # Trailing newline
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_suppressions(
    project_root: str | Path,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Load suppressions from ``.roam-suppressions.yml`` in *project_root*.

    Returns an empty list if the file does not exist or cannot be parsed.
    Each suppression dict has at least ``rule`` and ``file`` keys, and
    optionally ``line``, ``reason``, ``status``, ``author``, ``date``.

    W1032 (Pattern 2 — silent fallback, mirror of W706's
    :func:`finding_suppress._load_ignore_findings_file` and W1019b's
    :func:`smells_suppress.load_smells_suppressions`): when *warnings_out*
    is supplied as a ``list[str]``, every silent-fallback path (file
    unreadable / OSError, malformed YAML/JSON, non-dict root, missing or
    non-list ``suppressions:`` key, malformed entry without required
    ``rule`` / ``file``) appends an actionable warning naming the path,
    the failure shape, and the resolution. Pre-W1032 callers that don't
    supply ``warnings_out`` retain byte-identical silent-empty-list
    behaviour so existing happy-path consumers (``cmd_triage``,
    ``save_suppression`` dedup) keep emitting byte-identical envelopes
    when ``.roam-suppressions.yml`` is well-formed.

    The file-read + YAML parse + no-PyYAML fallback + root-type check
    live in :func:`roam.commands._yaml_loader.load_yaml_with_warnings`;
    the per-entry validation (required ``rule`` + ``file``) stays here
    via :func:`_validate_suppression_rows`. Mirrors the W1019b pattern
    from :mod:`roam.commands.smells_suppress`.
    """
    from roam.commands._yaml_loader import load_yaml_with_warnings

    root = Path(project_root)
    config_path = root / ".roam-suppressions.yml"

    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data = load_yaml_with_warnings(
        config_path,
        tiny_parser=_parse_suppressions_yaml_root_dict,
        config_label="suppressions",
        warnings_out=warnings_out,
    )
    if data is None:
        # Missing file — default state, no warning emitted by the helper.
        return []
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the failure (read error / malformed
        # YAML / wrong root type / tiny-parser fallback). Propagate the
        # empty result without piling on a second "no `suppressions:` key"
        # warning that would just confuse the caller.
        return []
    # ``data`` is a Mapping when ``allow_list_root`` is left at False
    # (the default we want for ``.roam-suppressions.yml``). The helper's
    # root-type check guarantees this; the assert keeps the type checker
    # happy on the post-helper rows-extraction logic.
    assert isinstance(data, dict)
    rows = data.get("suppressions")
    if not isinstance(rows, list):
        # No `suppressions:` key or wrong type — treat as empty. The
        # tiny_parser always emits a list (possibly empty) so this branch
        # is only reached when PyYAML parsed a file with no
        # `suppressions:` key. Silent-empty matches pre-W1032 behaviour.
        return []

    return _validate_suppression_rows(rows, warnings_out=warnings_out, source_path=str(config_path))


def load_suppressions_typed(
    project_root: str | Path,
    *,
    warnings_out: WarningsOut = None,
) -> list[RuleFileSuppression]:
    """Typed counterpart of :func:`load_suppressions` (W692).

    Returns the same on-disk rows as :class:`RuleFileSuppression` instances
    instead of raw dicts. The legacy ``load_suppressions`` stays the
    canonical entry point until every caller migrates — this function is the
    bridge new code should reach for.

    W1032 (Pattern 2 — silent fallback, mirror of W1017's
    :func:`finding_suppress.load_per_finding_suppressions_typed`): when
    *warnings_out* is supplied as a ``list[str]``, it is threaded through
    to :func:`load_suppressions` so the typed surface receives the same
    actionable warnings on malformed input that the dict-shaped surface
    does. Pre-W1032 callers that don't supply ``warnings_out`` retain
    byte-identical silent-empty-list behaviour.
    """
    return [
        RuleFileSuppression.from_dict(d, warnings_out=warnings_out)
        for d in load_suppressions(project_root, warnings_out=warnings_out)
    ]


# A line-keyed suppression still matches when the finding has drifted by up
# to this many lines: any edit ABOVE the suppressed symbol shifts it, and
# exact-line matching meant the same false positive re-fired after every
# refactor while the suppression file accreted dead entries (2026-06-10
# dogfood: suppressed findings re-fired at 76→74 and 99→102).
LINE_MATCH_TOLERANCE = 3


def find_suppression(
    suppressions: list[dict],
    rule: str,
    file: str,
    line: int | None = None,
    symbol: str | None = None,
) -> dict | None:
    """Return the matching suppression entry if a finding is suppressed.

    Matching logic:
    - ``rule`` and ``file`` must match exactly.
    - If the suppression specifies a ``symbol``, it matches the finding's
      symbol (function/class name) — refactor-proof, lines are ignored.
    - Else if it specifies a ``line``, the finding's line must match within
      ``LINE_MATCH_TOLERANCE`` lines.
    - With neither, it suppresses the whole file for that rule.

    Parameters
    ----------
    suppressions:
        List of suppression dicts (from :func:`load_suppressions`).
    rule:
        The rule identifier to check.
    file:
        The file path (relative to project root, forward slashes).
    line:
        Optional line number of the finding.
    symbol:
        Optional symbol name (function/class) the finding is attached to.
    Returns ``None`` when no entry matches. This mirrors
    :func:`roam.commands.smells_suppress.is_suppressed`, whose callers need
    the matched entry for audit output instead of only a boolean.
    """
    # Normalise path separators for comparison
    norm_file = file.replace("\\", "/")

    for sup in suppressions:
        sup_rule = sup.get("rule", "")
        sup_file = sup.get("file", "").replace("\\", "/")

        if sup_rule != rule:
            continue
        if sup_file != norm_file:
            continue

        # Symbol-keyed suppression: match on the symbol name, ignore lines.
        sup_symbol = sup.get("symbol")
        if sup_symbol is not None:
            if symbol is not None and str(sup_symbol) == symbol:
                return sup
            continue

        # Line-keyed: match within tolerance so edits above the symbol
        # don't invalidate the suppression.
        sup_line = sup.get("line")
        if sup_line is not None:
            if line is not None:
                try:
                    if abs(int(sup_line) - int(line)) > LINE_MATCH_TOLERANCE:
                        continue
                except (TypeError, ValueError):
                    continue

        return sup

    return None


def _rule_file_entry_applies(
    suppressions: list[dict],
    rule: str,
    file: str,
    line: int | None = None,
    symbol: str | None = None,
) -> bool:
    """Check if a rule/file finding is suppressed."""
    return find_suppression(suppressions, rule, file, line=line, symbol=symbol) is not None


# Back-compat alias for existing callers. Keep the function definition under a
# rule/file-specific name so it does not collide with smells_suppress.is_suppressed.
is_suppressed = _rule_file_entry_applies


def save_suppression(
    project_root: str | Path,
    rule: str,
    file: str,
    reason: str,
    status: str,
    line: int | None = None,
    author: str | None = None,
    symbol: str | None = None,
) -> None:
    """Append a new suppression to ``.roam-suppressions.yml``.

    Creates the file if it does not exist.  Validates the status value.
    Avoids adding duplicate suppressions (same rule + file + line).

    Parameters
    ----------
    project_root:
        Path to the project root directory.
    rule:
        The rule identifier.
    file:
        The file path (relative to project root).
    reason:
        Human-readable justification for the suppression.
    status:
        One of ``safe``, ``acknowledged``, ``wont-fix``.
    line:
        Optional line number to narrow the suppression.
    author:
        Optional author identifier (e.g. email).
    symbol:
        Optional symbol name (function/class). Symbol-keyed suppressions
        survive refactors that shift line numbers — prefer them over
        ``line`` for symbol-attached findings (naming, complexity).
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}")

    root = Path(project_root)
    # Dedup check only — the parsed view is NEVER written back. Rewriting the
    # file from the typed view silently dropped every row the coercion
    # couldn't parse (hand-edited entries, foreign keys, unquoted reasons),
    # and a fully-unparseable file loaded as [] and was REPLACED by the one
    # new entry. Save must append, not normalise.
    existing_typed = load_suppressions_typed(root)

    # Check for duplicates against the typed view (match keys: rule, file, line).
    norm_file = file.replace("\\", "/")
    for sup in existing_typed:
        if sup.rule == rule and sup.file == norm_file and sup.line == line:
            # Already suppressed -- nothing to do
            return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry: dict = {
        "rule": rule,
        "file": norm_file,
    }
    if symbol:
        entry["symbol"] = symbol
    if line is not None:
        entry["line"] = int(line)
    entry["reason"] = reason
    entry["status"] = status
    if author:
        entry["author"] = author
    entry["date"] = today

    config_path = root / ".roam-suppressions.yml"
    if not config_path.exists():
        config_path.write_text(_serialize_suppressions([entry]), encoding="utf-8")
        return

    # Append-only: preserve the existing file byte-for-byte and add the new
    # entry at the end of the `suppressions:` list. Entries the loader warned
    # about (and therefore can't match) stay on disk for the human to fix.
    text = config_path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        text += "\n"
    if "suppressions:" not in text:
        # Root key absent (empty or comment-only file) — add it so the
        # appended list item parses.
        text += "suppressions:\n"
    entry_lines = _serialize_suppressions([entry]).splitlines()
    # _serialize_suppressions emits two comment lines + the root key before
    # the entry; keep only the entry body for the append.
    body = "\n".join(line for line in entry_lines if line.startswith(("  - ", "    ")))
    config_path.write_text(text + body + "\n", encoding="utf-8")


def suppression_stats(suppressions: list[dict]) -> dict:
    """Compute summary statistics over a list of suppressions.

    Returns a dict with keys:

    - ``total`` -- total number of suppressions
    - ``by_status`` -- dict mapping status to count
    - ``by_rule`` -- dict mapping rule to count
    - ``by_file`` -- dict mapping file to count
    """
    by_status = Counter(sup.get("status", "unknown") for sup in suppressions)
    by_rule = Counter(sup.get("rule", "unknown") for sup in suppressions)
    by_file = Counter(sup.get("file", "unknown") for sup in suppressions)

    return {
        "total": len(suppressions),
        "by_status": dict(by_status),
        "by_rule": dict(by_rule),
        "by_file": dict(by_file),
    }
