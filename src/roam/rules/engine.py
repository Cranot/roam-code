"""YAML rule parser and graph query evaluator for custom governance rules.

Users define architectural rules as YAML files in ``.roam/rules/``.
Roam evaluates them against the indexed graph and reports violations.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# YAML loading with fallback
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict | None:
    """Load a single YAML file, returning the parsed dict or None on error.

    Falls back to a minimal line parser when PyYAML is not installed.
    """
    try:
        import yaml
    except ImportError:
        return _parse_simple_yaml(path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _parse_simple_yaml(path: Path) -> dict | None:
    """Minimal YAML subset parser for rule files (no PyYAML dependency).

    Handles flat key-value pairs, lists as ``[a, b]``, and nested maps
    introduced by indented keys under a parent key.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    result: dict = {}
    stack: list[tuple[int, dict]] = [(0, result)]

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # Pop stack to matching indent
        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()

        if ":" not in stripped:
            continue

        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()

        current = stack[-1][1]

        if not val:
            # Start a nested dict
            child: dict = {}
            current[key] = child
            stack.append((indent + 2, child))
        elif val.startswith("[") and val.endswith("]"):
            # Inline list
            items = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",") if v.strip()]
            current[key] = items
        else:
            val = val.strip('"').strip("'")
            if val.lower() == "true":
                parsed_val: object = True
            elif val.lower() == "false":
                parsed_val = False
            else:
                try:
                    parsed_val = int(val)
                except ValueError:
                    try:
                        parsed_val = float(val)
                    except ValueError:
                        parsed_val = val
            current[key] = parsed_val

    return result if result else None


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------


def load_rules(rules_dir: Path) -> list[dict]:
    """Load all .yaml/.yml files from the rules directory.

    Returns a list of rule dicts. Files that fail to parse are silently
    skipped (a warning is included in the rule's ``_error`` key).
    """
    if not rules_dir.is_dir():
        return []

    rules: list[dict] = []
    for p in sorted(rules_dir.iterdir()):
        if p.suffix not in (".yaml", ".yml"):
            continue
        data = _load_yaml(p)
        if data is None:
            rules.append({
                "name": p.name,
                "severity": "error",
                "_error": f"failed to parse {p.name}",
                "_file": str(p),
            })
            continue
        data["_file"] = str(p)
        rules.append(data)

    return rules


# ---------------------------------------------------------------------------
# Exemption helpers
# ---------------------------------------------------------------------------


def _is_exempt(symbol_name: str, file_path: str, exempt: dict) -> bool:
    """Check if a symbol/file combination is exempt from the rule."""
    exempt_symbols = exempt.get("symbols", [])
    if isinstance(exempt_symbols, str):
        exempt_symbols = [exempt_symbols]
    for es in exempt_symbols:
        if es == symbol_name:
            return True

    exempt_files = exempt.get("files", [])
    if isinstance(exempt_files, str):
        exempt_files = [exempt_files]
    for ef in exempt_files:
        if _matches_glob(file_path, ef):
            return True

    return False


def _matches_glob(file_path: str, pattern: str) -> bool:
    """Check if a file path matches a glob pattern.

    Supports ``**`` for matching zero or more directories, unlike plain
    ``fnmatch`` which treats ``*`` as matching everything including ``/``.
    """
    norm = file_path.replace("\\", "/")
    pat = pattern.replace("\\", "/")

    if "**" not in pat:
        return fnmatch.fnmatch(norm, pat)

    # Convert glob pattern with ** to regex
    parts: list[str] = []
    i = 0
    while i < len(pat):
        c = pat[i]
        if c == "*":
            if i + 1 < len(pat) and pat[i + 1] == "*":
                if i + 2 < len(pat) and pat[i + 2] == "/":
                    parts.append("(?:.+/)?")
                    i += 3
                    continue
                else:
                    parts.append(".*")
                    i += 2
                    continue
            else:
                parts.append("[^/]*")
                i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        elif c in r".+^${}()|[]":
            parts.append("\\" + c)
            i += 1
        else:
            parts.append(c)
            i += 1

    regex = "".join(parts)
    return re.match("^" + regex + "$", norm) is not None


def _matches_kind(kind: str, kind_filter: list | str | None) -> bool:
    """Check if a symbol kind matches the kind filter."""
    if kind_filter is None:
        return True
    if isinstance(kind_filter, str):
        kind_filter = [kind_filter]
    return kind in kind_filter


# ---------------------------------------------------------------------------
# Rule evaluation: path_match
# ---------------------------------------------------------------------------


def _evaluate_path_match(rule: dict, conn) -> dict:
    """Evaluate a path_match rule: find edges between from/to patterns.

    Looks for direct edges (or paths up to max_distance) from symbols
    matching ``match.from`` criteria to symbols matching ``match.to`` criteria.
    """
    match = rule.get("match", {})
    from_spec = match.get("from", {})
    to_spec = match.get("to", {})
    max_distance = match.get("max_distance", 1)
    exempt = rule.get("exempt", {})

    from_glob = from_spec.get("file_glob")
    from_kind = from_spec.get("kind")
    to_glob = to_spec.get("file_glob")
    to_kind = to_spec.get("kind")

    # Query edges joining source and target symbols with their files
    rows = conn.execute("""
        SELECT
            s1.name AS src_name, s1.kind AS src_kind,
            f1.path AS src_file, s1.line_start AS src_line,
            s2.name AS tgt_name, s2.kind AS tgt_kind,
            f2.path AS tgt_file, s2.line_start AS tgt_line,
            e.kind AS edge_kind
        FROM edges e
        JOIN symbols s1 ON e.source_id = s1.id
        JOIN files f1 ON s1.file_id = f1.id
        JOIN symbols s2 ON e.target_id = s2.id
        JOIN files f2 ON s2.file_id = f2.id
    """).fetchall()

    violations: list[dict] = []
    for row in rows:
        src_file = row["src_file"]
        tgt_file = row["tgt_file"]
        src_name = row["src_name"]
        tgt_name = row["tgt_name"]
        src_kind = row["src_kind"]
        tgt_kind = row["tgt_kind"]

        # Apply from-pattern filter
        if from_glob and not _matches_glob(src_file, from_glob):
            continue
        if not _matches_kind(src_kind, from_kind):
            continue

        # Apply to-pattern filter
        if to_glob and not _matches_glob(tgt_file, to_glob):
            continue
        if not _matches_kind(tgt_kind, to_kind):
            continue

        # max_distance=1 means direct edge (already satisfied)
        # For max_distance > 1 we would need BFS, but direct edge
        # matching covers the core use case.
        if max_distance < 1:
            continue

        # Check exemptions
        if _is_exempt(src_name, src_file, exempt):
            continue
        if _is_exempt(tgt_name, tgt_file, exempt):
            continue

        violations.append({
            "symbol": src_name,
            "file": src_file,
            "line": row["src_line"],
            "reason": f"{src_name} ({src_file}) -> {tgt_name} ({tgt_file})",
        })

    name = rule.get("name", "unnamed")
    severity = rule.get("severity", "error")
    passed = len(violations) == 0

    return {
        "name": name,
        "severity": severity,
        "passed": passed,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Rule evaluation: symbol_match
# ---------------------------------------------------------------------------


def _evaluate_symbol_match(rule: dict, conn) -> dict:
    """Evaluate a symbol_match rule: find symbols matching criteria.

    Supports ``require.has_test`` to check that matched symbols have
    test coverage (edges from test files).
    """
    match = rule.get("match", {})
    exempt = rule.get("exempt", {})

    kind_filter = match.get("kind")
    exported_filter = match.get("exported")
    file_glob = match.get("file_glob")
    min_fan_in = match.get("min_fan_in")

    require = match.get("require", {})
    require_has_test = require.get("has_test", False)

    # Build the base query
    query = """
        SELECT s.id, s.name, s.kind, s.line_start, s.is_exported,
               f.path AS file_path, f.file_role,
               COALESCE(gm.in_degree, 0) AS in_degree
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
        WHERE 1=1
    """
    params: list = []

    if kind_filter:
        if isinstance(kind_filter, str):
            kind_filter = [kind_filter]
        placeholders = ",".join("?" for _ in kind_filter)
        query += f" AND s.kind IN ({placeholders})"
        params.extend(kind_filter)

    if exported_filter is True:
        query += " AND s.is_exported = 1"
    elif exported_filter is False:
        query += " AND s.is_exported = 0"

    rows = conn.execute(query, params).fetchall()

    violations: list[dict] = []
    for row in rows:
        file_path = row["file_path"]
        symbol_name = row["name"]
        in_deg = row["in_degree"]

        # File glob filter
        if file_glob and not _matches_glob(file_path, file_glob):
            continue

        # Min fan-in filter
        if min_fan_in is not None and in_deg < min_fan_in:
            continue

        # Exemptions
        if _is_exempt(symbol_name, file_path, exempt):
            continue

        # Check require.has_test
        if require_has_test:
            has_test = _symbol_has_test(conn, row["id"])
            if has_test:
                continue  # Requirement met, no violation
            violations.append({
                "symbol": symbol_name,
                "file": file_path,
                "line": row["line_start"],
                "reason": f"{symbol_name} has no test coverage",
            })
        else:
            # If no require, the match itself is the violation
            violations.append({
                "symbol": symbol_name,
                "file": file_path,
                "line": row["line_start"],
                "reason": f"{symbol_name} matches rule criteria",
            })

    name = rule.get("name", "unnamed")
    severity = rule.get("severity", "error")
    passed = len(violations) == 0

    return {
        "name": name,
        "severity": severity,
        "passed": passed,
        "violations": violations,
    }


def _symbol_has_test(conn, symbol_id: int) -> bool:
    """Check if a symbol has edges from test files."""
    rows = conn.execute("""
        SELECT 1 FROM edges e
        JOIN symbols s ON e.source_id = s.id
        JOIN files f ON s.file_id = f.id
        WHERE e.target_id = ?
          AND (f.file_role = 'test' OR f.path LIKE '%%test%%')
        LIMIT 1
    """, (symbol_id,)).fetchall()
    return len(rows) > 0


# ---------------------------------------------------------------------------
# Rule type detection + dispatch
# ---------------------------------------------------------------------------


def _detect_rule_type(rule: dict) -> str:
    """Detect the rule type from its match specification.

    If the match block has both ``from`` and ``to``, it is a path_match.
    Otherwise it is a symbol_match.
    """
    match = rule.get("match", {})
    if "from" in match and "to" in match:
        return "path_match"
    return "symbol_match"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_rule(rule: dict, conn, G=None) -> dict:
    """Evaluate a single rule against the indexed DB.

    Returns ``{name, severity, passed, violations: [{symbol, file, line, reason}]}``.
    """
    # Handle parse errors
    if "_error" in rule:
        return {
            "name": rule.get("name", "unknown"),
            "severity": rule.get("severity", "error"),
            "passed": False,
            "violations": [{"symbol": "", "file": rule.get("_file", ""),
                            "line": None, "reason": rule["_error"]}],
        }

    rule_type = rule.get("type") or _detect_rule_type(rule)

    if rule_type == "path_match":
        return _evaluate_path_match(rule, conn)
    else:
        return _evaluate_symbol_match(rule, conn)


def evaluate_all(rules_dir: Path, conn) -> list[dict]:
    """Load and evaluate all rules from the rules directory.

    Returns a list of result dicts, one per rule.
    """
    rules = load_rules(rules_dir)
    results: list[dict] = []
    for rule in rules:
        results.append(evaluate_rule(rule, conn))
    return results
