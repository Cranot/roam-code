"""SARIF 2.1.0 output for GitHub code scanning integration.

Converts roam analysis results into Static Analysis Results Interchange
Format (SARIF) for consumption by GitHub Advanced Security, VS Code SARIF
Viewer, and other SARIF-aware tools.

Usage::

    from roam.output.sarif import dead_to_sarif, write_sarif

    sarif = dead_to_sarif(dead_exports)
    write_sarif(sarif, "roam-dead.sarif")
"""

from __future__ import annotations

import json as _json
from pathlib import Path

_SARIF_VERSION = "2.1.0"
_SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/"
    "main/sarif-2.1/schema/sarif-schema-2.1.0.json"
)
_TOOL_NAME = "roam-code"
_HELP_BASE = "https://github.com/AbanteAI/roam-code#"


def _get_version() -> str:
    """Return roam-code version string."""
    try:
        from importlib.metadata import version
        return version("roam-code")
    except Exception:
        return "dev"


# ── Severity mapping ─────────────────────────────────────────────────

_LEVEL_MAP = {
    "CRITICAL": "error",
    "HIGH":     "warning",
    "WARNING":  "warning",
    "MEDIUM":   "note",
    "LOW":      "note",
    "INFO":     "note",
}


def _to_level(severity: str) -> str:
    """Map a roam severity string to a SARIF level."""
    return _LEVEL_MAP.get(severity.upper(), "note")


# ── Location helpers ─────────────────────────────────────────────────

def _physical_location(file_path: str, line: int | None = None) -> dict:
    """Build a SARIF physicalLocation object.

    *file_path* is stored as a forward-slash URI-style path so that
    SARIF viewers can render it correctly on any platform.
    """
    uri = file_path.replace("\\", "/")
    loc: dict = {
        "artifactLocation": {"uri": uri},
    }
    if line is not None and line > 0:
        loc["region"] = {"startLine": line}
    return loc


def _location(file_path: str, line: int | None = None) -> dict:
    """Build a single SARIF location entry."""
    return {"physicalLocation": _physical_location(file_path, line)}


def _parse_loc_string(loc_str: str) -> tuple[str, int | None]:
    """Parse ``"path/to/file.py:42"`` into ``("path/to/file.py", 42)``.

    Returns ``(path, None)`` when no line number is present.
    """
    if ":" in loc_str:
        parts = loc_str.rsplit(":", 1)
        try:
            return parts[0], int(parts[1])
        except (ValueError, IndexError):
            return loc_str, None
    return loc_str, None


# ── Core builder ─────────────────────────────────────────────────────

def to_sarif(
    tool_name: str,
    version: str,
    rules: list[dict],
    results: list[dict],
) -> dict:
    """Build a complete SARIF 2.1.0 JSON document.

    Parameters
    ----------
    tool_name:
        Display name of the analysis tool (e.g. ``"roam-code"``).
    version:
        Semantic version of the tool.
    rules:
        List of rule definitions.  Each dict must contain:

        - ``id`` (str): unique rule identifier
        - ``shortDescription`` (str): one-line description

        Optional keys:

        - ``helpUri`` (str): URL for more information
        - ``defaultLevel`` (str): SARIF level (``"error"``/``"warning"``/``"note"``)
    results:
        List of result dicts.  Each must contain:

        - ``ruleId`` (str): matches a rule ``id``
        - ``level`` (str): ``"error"``/``"warning"``/``"note"``
        - ``message`` (str): human-readable finding description
        - ``locations`` (list[dict]): SARIF location objects

    Returns
    -------
    dict
        A complete SARIF 2.1.0 envelope ready for ``json.dumps``.
    """
    driver: dict = {
        "name": tool_name,
        "version": version,
        "rules": [
            _build_rule(r) for r in rules
        ],
    }

    return {
        "$schema": _SARIF_SCHEMA,
        "version": _SARIF_VERSION,
        "runs": [
            {
                "tool": {"driver": driver},
                "results": results,
            }
        ],
    }


def _build_rule(rule: dict) -> dict:
    """Normalise a rule dict into the SARIF rule schema."""
    out: dict = {
        "id": rule["id"],
        "shortDescription": {"text": rule["shortDescription"]},
    }
    if "helpUri" in rule:
        out["helpUri"] = rule["helpUri"]
    if "defaultLevel" in rule:
        out["defaultConfiguration"] = {"level": rule["defaultLevel"]}
    return out


# ── Write / serialise ────────────────────────────────────────────────

def write_sarif(data: dict, output_path: str | Path | None = None) -> str:
    """Serialise *data* to JSON and optionally write it to *output_path*.

    Returns the JSON string in all cases.
    """
    text = _json.dumps(data, indent=2, default=str)
    if output_path is not None:
        Path(output_path).write_text(text, encoding="utf-8")
    return text


# ── Fitness violations ───────────────────────────────────────────────

def fitness_to_sarif(violations: list[dict]) -> dict:
    """Convert fitness-rule violations to SARIF.

    Each *violation* dict is expected to carry:

    - ``rule`` (str): rule name
    - ``type`` (str): ``"dependency"`` / ``"metric"`` / ``"naming"``
    - ``message`` (str): human-readable detail
    - ``source`` (str, optional): ``"path:line"`` location string
    """
    seen_rules: dict[str, dict] = {}
    results: list[dict] = []

    for v in violations:
        rule_id = f"fitness/{v.get('type', 'unknown')}/{_slugify(v.get('rule', 'unnamed'))}"
        if rule_id not in seen_rules:
            seen_rules[rule_id] = {
                "id": rule_id,
                "shortDescription": v.get("rule", "Fitness rule violation"),
                "helpUri": _HELP_BASE + "fitness",
                "defaultLevel": "warning",
            }

        locations = []
        src = v.get("source", "")
        if src:
            fpath, line = _parse_loc_string(src)
            locations.append(_location(fpath, line))

        results.append({
            "ruleId": rule_id,
            "level": "warning",
            "message": {"text": v.get("message", "Fitness rule violation")},
            "locations": locations,
        })

    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        list(seen_rules.values()),
        results,
    )


# ── Dead code ────────────────────────────────────────────────────────

def dead_to_sarif(dead_exports: list[dict]) -> dict:
    """Convert dead-code findings to SARIF.

    Each *dead_export* dict is expected to carry:

    - ``name`` (str): symbol name
    - ``kind`` (str): symbol kind (function, class, ...)
    - ``location`` (str): ``"path:line"`` location string
    - ``action`` (str, optional): ``"SAFE"`` / ``"REVIEW"`` / ``"INTENTIONAL"``
    """
    rule_id = "dead-code/unreferenced-export"
    rules = [{
        "id": rule_id,
        "shortDescription": "Exported symbol has no references",
        "helpUri": _HELP_BASE + "dead",
        "defaultLevel": "warning",
    }]

    results: list[dict] = []
    for item in dead_exports:
        action = item.get("action", "REVIEW")
        if action == "INTENTIONAL":
            continue

        level = "warning" if action == "SAFE" else "note"
        fpath, line = _parse_loc_string(item.get("location", ""))

        locations = []
        if fpath:
            locations.append(_location(fpath, line))

        results.append({
            "ruleId": rule_id,
            "level": level,
            "message": {
                "text": (
                    f"Unreferenced export: {item.get('kind', '?')} "
                    f"'{item.get('name', '?')}' ({action})"
                ),
            },
            "locations": locations,
        })

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Complexity ───────────────────────────────────────────────────────

def complexity_to_sarif(
    complex_symbols: list[dict],
    threshold: float = 25,
) -> dict:
    """Convert complexity findings to SARIF.

    Each *complex_symbol* dict is expected to carry:

    - ``name`` (str): symbol (qualified) name
    - ``kind`` (str): symbol kind
    - ``file`` (str): file path
    - ``line`` (int | None): line number
    - ``cognitive_complexity`` (float): the computed score
    - ``severity`` (str, optional): ``"CRITICAL"`` / ``"HIGH"`` / ``"MEDIUM"`` / ``"LOW"``
    """
    rule_id = "complexity/cognitive-complexity"
    rules = [{
        "id": rule_id,
        "shortDescription": (
            f"Cognitive complexity exceeds threshold ({threshold})"
        ),
        "helpUri": _HELP_BASE + "complexity",
        "defaultLevel": "warning",
    }]

    results: list[dict] = []
    for sym in complex_symbols:
        score = sym.get("cognitive_complexity", 0)
        if score < threshold:
            continue

        severity = sym.get("severity", "HIGH" if score >= 25 else "MEDIUM")
        level = _to_level(severity)
        fpath = sym.get("file", "")
        line = sym.get("line")

        locations = []
        if fpath:
            locations.append(_location(fpath, line))

        results.append({
            "ruleId": rule_id,
            "level": level,
            "message": {
                "text": (
                    f"{sym.get('kind', '?')} '{sym.get('name', '?')}' "
                    f"has cognitive complexity {score:.0f} "
                    f"(threshold {threshold})"
                ),
            },
            "locations": locations,
        })

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Naming conventions ───────────────────────────────────────────────

def conventions_to_sarif(violations: list[dict]) -> dict:
    """Convert naming-convention violations (outliers) to SARIF.

    Each *violation* dict is expected to carry:

    - ``name`` (str): symbol name
    - ``kind`` (str): symbol kind
    - ``actual_style`` (str): detected case style
    - ``expected_style`` (str): dominant case style for this kind group
    - ``file`` (str): file path
    - ``line`` (int | None): line number
    """
    rule_id = "conventions/naming-style"
    rules = [{
        "id": rule_id,
        "shortDescription": "Symbol name does not match codebase naming convention",
        "helpUri": _HELP_BASE + "conventions",
        "defaultLevel": "note",
    }]

    results: list[dict] = []
    for v in violations:
        fpath = v.get("file", "")
        line = v.get("line")

        locations = []
        if fpath:
            locations.append(_location(fpath, line))

        results.append({
            "ruleId": rule_id,
            "level": "note",
            "message": {
                "text": (
                    f"{v.get('kind', '?')} '{v.get('name', '?')}' "
                    f"uses {v.get('actual_style', '?')} "
                    f"(expected {v.get('expected_style', '?')})"
                ),
            },
            "locations": locations,
        })

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Breaking changes ─────────────────────────────────────────────────

def breaking_to_sarif(changes: dict) -> dict:
    """Convert breaking-change analysis to SARIF.

    *changes* is expected to carry:

    - ``removed`` (list[dict]): each with ``name``, ``kind``, ``file``, ``line``
    - ``signature_changed`` (list[dict]): each with ``name``, ``kind``,
      ``old_signature``, ``new_signature``, ``file``, ``line``
    - ``renamed`` (list[dict]): each with ``old_name``, ``new_name``,
      ``kind``, ``file``, ``line``
    """
    rules = [
        {
            "id": "breaking/removed-export",
            "shortDescription": "Exported symbol was removed",
            "helpUri": _HELP_BASE + "breaking",
            "defaultLevel": "error",
        },
        {
            "id": "breaking/signature-changed",
            "shortDescription": "Exported symbol signature changed",
            "helpUri": _HELP_BASE + "breaking",
            "defaultLevel": "warning",
        },
        {
            "id": "breaking/renamed",
            "shortDescription": "Exported symbol was renamed",
            "helpUri": _HELP_BASE + "breaking",
            "defaultLevel": "warning",
        },
    ]

    results: list[dict] = []

    for item in changes.get("removed", []):
        fpath = item.get("file", "")
        line = item.get("line")
        locations = []
        if fpath:
            locations.append(_location(fpath, line))
        results.append({
            "ruleId": "breaking/removed-export",
            "level": "error",
            "message": {
                "text": (
                    f"Removed exported {item.get('kind', '?')} "
                    f"'{item.get('name', '?')}'"
                ),
            },
            "locations": locations,
        })

    for item in changes.get("signature_changed", []):
        fpath = item.get("file", "")
        line = item.get("line")
        locations = []
        if fpath:
            locations.append(_location(fpath, line))
        results.append({
            "ruleId": "breaking/signature-changed",
            "level": "warning",
            "message": {
                "text": (
                    f"Signature changed for {item.get('kind', '?')} "
                    f"'{item.get('name', '?')}'"
                ),
            },
            "locations": locations,
        })

    for item in changes.get("renamed", []):
        fpath = item.get("file", "")
        line = item.get("line")
        locations = []
        if fpath:
            locations.append(_location(fpath, line))
        results.append({
            "ruleId": "breaking/renamed",
            "level": "warning",
            "message": {
                "text": (
                    f"Renamed {item.get('kind', '?')} "
                    f"'{item.get('old_name', '?')}' -> "
                    f"'{item.get('new_name', '?')}'"
                ),
            },
            "locations": locations,
        })

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Health issues ────────────────────────────────────────────────────

def health_to_sarif(issues: dict) -> dict:
    """Convert health-check results to SARIF.

    *issues* is expected to carry:

    - ``cycles`` (list[dict]): each with ``size``, ``severity``,
      ``symbols`` (list[str]), ``files`` (list[str])
    - ``god_components`` (list[dict]): each with ``name``, ``kind``,
      ``degree``, ``file``, ``severity``
    - ``bottlenecks`` (list[dict]): each with ``name``, ``kind``,
      ``betweenness``, ``file``, ``severity``
    - ``layer_violations`` (list[dict], optional): each with ``source``,
      ``source_layer``, ``target``, ``target_layer``, ``severity``
    """
    rules = [
        {
            "id": "health/cycle",
            "shortDescription": "Dependency cycle detected",
            "helpUri": _HELP_BASE + "health",
            "defaultLevel": "warning",
        },
        {
            "id": "health/god-component",
            "shortDescription": "God component with excessive coupling",
            "helpUri": _HELP_BASE + "health",
            "defaultLevel": "warning",
        },
        {
            "id": "health/bottleneck",
            "shortDescription": "High-betweenness bottleneck symbol",
            "helpUri": _HELP_BASE + "health",
            "defaultLevel": "warning",
        },
        {
            "id": "health/layer-violation",
            "shortDescription": "Architectural layer violation",
            "helpUri": _HELP_BASE + "health",
            "defaultLevel": "warning",
        },
    ]

    results: list[dict] = []

    # Cycles
    for cyc in issues.get("cycles", []):
        severity = cyc.get("severity", "WARNING")
        level = _to_level(severity)
        symbols = cyc.get("symbols", [])
        files = cyc.get("files", [])
        symbol_names = ", ".join(symbols[:5])
        if len(symbols) > 5:
            symbol_names += f" (+{len(symbols) - 5} more)"

        # Attach locations for every file in the cycle
        locations = [_location(f, None) for f in files]

        results.append({
            "ruleId": "health/cycle",
            "level": level,
            "message": {
                "text": (
                    f"Dependency cycle of {cyc.get('size', '?')} symbols: "
                    f"{symbol_names}"
                ),
            },
            "locations": locations,
        })

    # God components
    for g in issues.get("god_components", []):
        severity = g.get("severity", "WARNING")
        level = _to_level(severity)
        fpath = g.get("file", "")
        locations = []
        if fpath:
            locations.append(_location(fpath, None))

        results.append({
            "ruleId": "health/god-component",
            "level": level,
            "message": {
                "text": (
                    f"God component: {g.get('kind', '?')} "
                    f"'{g.get('name', '?')}' "
                    f"(degree {g.get('degree', '?')})"
                ),
            },
            "locations": locations,
        })

    # Bottlenecks
    for b in issues.get("bottlenecks", []):
        severity = b.get("severity", "WARNING")
        level = _to_level(severity)
        fpath = b.get("file", "")
        locations = []
        if fpath:
            locations.append(_location(fpath, None))

        results.append({
            "ruleId": "health/bottleneck",
            "level": level,
            "message": {
                "text": (
                    f"Bottleneck: {b.get('kind', '?')} "
                    f"'{b.get('name', '?')}' "
                    f"(betweenness {b.get('betweenness', '?')})"
                ),
            },
            "locations": locations,
        })

    # Layer violations
    for v in issues.get("layer_violations", []):
        severity = v.get("severity", "WARNING")
        level = _to_level(severity)

        results.append({
            "ruleId": "health/layer-violation",
            "level": level,
            "message": {
                "text": (
                    f"Layer violation: {v.get('source', '?')} "
                    f"(L{v.get('source_layer', '?')}) -> "
                    f"{v.get('target', '?')} "
                    f"(L{v.get('target_layer', '?')})"
                ),
            },
            "locations": [],
        })

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Internal helpers ─────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Turn a human-readable name into a URL/ID-safe slug."""
    slug = text.lower().strip()
    slug = slug.replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c in ("-", "_"))
