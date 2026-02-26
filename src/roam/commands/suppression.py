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

from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Valid status values
# ---------------------------------------------------------------------------

VALID_STATUSES = frozenset({"safe", "acknowledged", "wont-fix"})

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
        for key in ("rule", "file", "line", "reason", "status", "author", "date"):
            val = sup.get(key)
            if val is None:
                continue
            if first:
                lines.append(f"  - {key}: {val}")
                first = True  # only the first field gets the dash
                first = False
            else:
                lines.append(f"    {key}: {val}")

    # Trailing newline
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_suppressions(project_root: str | Path) -> list[dict]:
    """Load suppressions from ``.roam-suppressions.yml`` in *project_root*.

    Returns an empty list if the file does not exist or cannot be parsed.
    Each suppression dict has at least ``rule`` and ``file`` keys, and
    optionally ``line``, ``reason``, ``status``, ``author``, ``date``.
    """
    root = Path(project_root)
    config_path = root / ".roam-suppressions.yml"

    if not config_path.is_file():
        return []

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return []

    return _parse_suppressions_yaml(text)


def is_suppressed(
    suppressions: list[dict],
    rule: str,
    file: str,
    line: int | None = None,
) -> bool:
    """Check if a finding is suppressed.

    Matching logic:
    - ``rule`` and ``file`` must match exactly.
    - If the suppression specifies a ``line``, the finding's line must also
      match.  If the suppression has no ``line``, it suppresses all lines
      in that file for that rule.

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

        # If the suppression specifies a line, require an exact match
        sup_line = sup.get("line")
        if sup_line is not None:
            if line is not None and int(sup_line) != int(line):
                continue

        return True

    return False


def save_suppression(
    project_root: str | Path,
    rule: str,
    file: str,
    reason: str,
    status: str,
    line: int | None = None,
    author: str | None = None,
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
    """
    if status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
        )

    root = Path(project_root)
    existing = load_suppressions(root)

    # Check for duplicates
    norm_file = file.replace("\\", "/")
    for sup in existing:
        if (
            sup.get("rule") == rule
            and sup.get("file", "").replace("\\", "/") == norm_file
            and sup.get("line") == line
        ):
            # Already suppressed -- nothing to do
            return

    # Build the new entry
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry: dict = {
        "rule": rule,
        "file": norm_file,
    }
    if line is not None:
        entry["line"] = int(line)
    entry["reason"] = reason
    entry["status"] = status
    if author:
        entry["author"] = author
    entry["date"] = today

    existing.append(entry)

    config_path = root / ".roam-suppressions.yml"
    config_path.write_text(_serialize_suppressions(existing), encoding="utf-8")


def suppression_stats(suppressions: list[dict]) -> dict:
    """Compute summary statistics over a list of suppressions.

    Returns a dict with keys:

    - ``total`` -- total number of suppressions
    - ``by_status`` -- dict mapping status to count
    - ``by_rule`` -- dict mapping rule to count
    - ``by_file`` -- dict mapping file to count
    """
    by_status: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    by_file: dict[str, int] = {}

    for sup in suppressions:
        st = sup.get("status", "unknown")
        by_status[st] = by_status.get(st, 0) + 1

        rl = sup.get("rule", "unknown")
        by_rule[rl] = by_rule.get(rl, 0) + 1

        fl = sup.get("file", "unknown")
        by_file[fl] = by_file.get(fl, 0) + 1

    return {
        "total": len(suppressions),
        "by_status": by_status,
        "by_rule": by_rule,
        "by_file": by_file,
    }
