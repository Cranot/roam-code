"""YAML rule parser and graph query evaluator for custom governance rules.

Users define architectural rules as YAML files in ``.roam/rules/``.
Roam evaluates them against the indexed graph and reports violations.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from roam.db.connection import find_project_root
from roam.index.parser import detect_language, parse_file
from roam.rules.ast_match import (
    compile_ast_pattern,
    find_ast_matches,
    normalize_language_name,
)
from roam.rules.dataflow import collect_dataflow_findings


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
    for p in sorted(rules_dir.rglob("*")):
        if not p.is_file():
            continue
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


def _table_exists(conn, table_name: str) -> bool:
    """Return True when a table exists in the current SQLite DB."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _table_columns(conn, table_name: str) -> set[str]:
    """Return the set of columns for a table, or empty set if unavailable."""
    try:
        rows = conn.execute("PRAGMA table_info({})".format(table_name)).fetchall()
    except Exception:
        return set()

    cols: set[str] = set()
    for row in rows:
        try:
            cols.add(str(row["name"]))
        except Exception:
            if len(row) > 1:
                cols.add(str(row[1]))
    return cols


def _as_float_or_none(value) -> float | None:
    """Convert value to float when possible, else return None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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

    Supports requirement checks under ``match.require``:
    - ``has_test``: matched symbols must have test coverage
    - ``name_regex``: symbol name must match regex
    - ``max_params`` / ``min_params``
    - ``max_symbol_lines`` / ``min_symbol_lines``
    - ``max_file_lines`` / ``min_file_lines``
    """
    match = rule.get("match", {})
    exempt = rule.get("exempt", {})

    kind_filter = match.get("kind")
    exported_filter = match.get("exported")
    file_glob = match.get("file_glob")
    min_fan_in = match.get("min_fan_in")
    max_fan_in = match.get("max_fan_in")

    require = match.get("require", {})
    require_has_test = bool(require.get("has_test", False))
    require_name_regex = require.get("name_regex")
    require_max_params = _as_float_or_none(require.get("max_params"))
    require_min_params = _as_float_or_none(require.get("min_params"))
    require_max_symbol_lines = _as_float_or_none(require.get("max_symbol_lines"))
    require_min_symbol_lines = _as_float_or_none(require.get("min_symbol_lines"))
    require_max_file_lines = _as_float_or_none(require.get("max_file_lines"))
    require_min_file_lines = _as_float_or_none(require.get("min_file_lines"))

    compiled_name_regex = None
    if isinstance(require_name_regex, str) and require_name_regex.strip():
        try:
            compiled_name_regex = re.compile(require_name_regex)
        except re.error as exc:
            return {
                "name": rule.get("name", "unnamed"),
                "severity": rule.get("severity", "error"),
                "passed": False,
                "violations": [{
                    "symbol": "",
                    "file": rule.get("_file", ""),
                    "line": None,
                    "reason": "invalid require.name_regex: {}".format(exc),
                }],
            }

    # Build the base query
    file_cols = _table_columns(conn, "files")
    symbol_cols = _table_columns(conn, "symbols")
    has_symbol_metrics = _table_exists(conn, "symbol_metrics")

    if "line_count" in file_cols and "loc" in file_cols:
        file_lines_expr = "COALESCE(f.line_count, f.loc)"
    elif "line_count" in file_cols:
        file_lines_expr = "f.line_count"
    elif "loc" in file_cols:
        file_lines_expr = "f.loc"
    else:
        file_lines_expr = "NULL"

    if "file_role" in file_cols:
        file_role_expr = "f.file_role"
    else:
        file_role_expr = "NULL"

    symbol_lines_fallback = (
        "(CASE WHEN s.line_start IS NOT NULL AND s.line_end IS NOT NULL "
        "THEN (s.line_end - s.line_start + 1) ELSE NULL END)"
        if "line_end" in symbol_cols
        else "NULL"
    )

    if has_symbol_metrics:
        param_expr = "sm.param_count"
        symbol_lines_expr = "COALESCE(sm.line_count, {})".format(symbol_lines_fallback)
        symbol_metrics_join = "LEFT JOIN symbol_metrics sm ON s.id = sm.symbol_id"
    else:
        param_expr = "NULL"
        symbol_lines_expr = symbol_lines_fallback
        symbol_metrics_join = ""

    query = """
        SELECT s.id, s.name, s.kind, s.line_start, s.is_exported,
               f.path AS file_path, {file_role_expr} AS file_role,
               COALESCE(gm.in_degree, 0) AS in_degree,
               {param_expr} AS param_count,
               {symbol_lines_expr} AS symbol_line_count,
               {file_lines_expr} AS file_line_count
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
        {symbol_metrics_join}
        WHERE 1=1
    """.format(
        file_role_expr=file_role_expr,
        param_expr=param_expr,
        symbol_lines_expr=symbol_lines_expr,
        file_lines_expr=file_lines_expr,
        symbol_metrics_join=symbol_metrics_join,
    )
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

    has_requirements = any([
        require_has_test,
        compiled_name_regex is not None,
        require_max_params is not None,
        require_min_params is not None,
        require_max_symbol_lines is not None,
        require_min_symbol_lines is not None,
        require_max_file_lines is not None,
        require_min_file_lines is not None,
    ])

    violations: list[dict] = []
    for row in rows:
        file_path = row["file_path"]
        symbol_name = row["name"]
        in_deg = _as_float_or_none(row["in_degree"]) or 0.0
        param_count = _as_float_or_none(row["param_count"])
        symbol_line_count = _as_float_or_none(row["symbol_line_count"])
        file_line_count = _as_float_or_none(row["file_line_count"])

        # File glob filter
        if file_glob and not _matches_glob(file_path, file_glob):
            continue

        # Min fan-in filter
        if min_fan_in is not None and in_deg < float(min_fan_in):
            continue
        if max_fan_in is not None and in_deg > float(max_fan_in):
            continue

        # Exemptions
        if _is_exempt(symbol_name, file_path, exempt):
            continue

        if has_requirements:
            reasons: list[str] = []

            if require_has_test and not _symbol_has_test(conn, row["id"]):
                reasons.append("{} has no test coverage".format(symbol_name))

            if compiled_name_regex and not compiled_name_regex.search(symbol_name):
                reasons.append(
                    "name '{}' does not match {}".format(symbol_name, compiled_name_regex.pattern)
                )

            if require_max_params is not None:
                if param_count is None:
                    reasons.append("parameter count unavailable")
                elif param_count > require_max_params:
                    reasons.append(
                        "parameter count {:.0f} exceeds {:.0f}".format(
                            param_count, require_max_params
                        )
                    )

            if require_min_params is not None:
                if param_count is None:
                    reasons.append("parameter count unavailable")
                elif param_count < require_min_params:
                    reasons.append(
                        "parameter count {:.0f} is below {:.0f}".format(
                            param_count, require_min_params
                        )
                    )

            if require_max_symbol_lines is not None:
                if symbol_line_count is None:
                    reasons.append("symbol line count unavailable")
                elif symbol_line_count > require_max_symbol_lines:
                    reasons.append(
                        "symbol line count {:.0f} exceeds {:.0f}".format(
                            symbol_line_count, require_max_symbol_lines
                        )
                    )

            if require_min_symbol_lines is not None:
                if symbol_line_count is None:
                    reasons.append("symbol line count unavailable")
                elif symbol_line_count < require_min_symbol_lines:
                    reasons.append(
                        "symbol line count {:.0f} is below {:.0f}".format(
                            symbol_line_count, require_min_symbol_lines
                        )
                    )

            if require_max_file_lines is not None:
                if file_line_count is None:
                    reasons.append("file line count unavailable")
                elif file_line_count > require_max_file_lines:
                    reasons.append(
                        "file line count {:.0f} exceeds {:.0f}".format(
                            file_line_count, require_max_file_lines
                        )
                    )

            if require_min_file_lines is not None:
                if file_line_count is None:
                    reasons.append("file line count unavailable")
                elif file_line_count < require_min_file_lines:
                    reasons.append(
                        "file line count {:.0f} is below {:.0f}".format(
                            file_line_count, require_min_file_lines
                        )
                    )

            if not reasons:
                continue

            violations.append({
                "symbol": symbol_name,
                "file": file_path,
                "line": row["line_start"],
                "reason": "; ".join(reasons),
            })
        else:
            # If no requirements are set, the match itself is the violation.
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
# Rule evaluation: ast_match
# ---------------------------------------------------------------------------


def _format_capture_preview(captures: dict[str, str]) -> str:
    """Format captured metavariables for rule output."""
    if not captures:
        return ""
    parts: list[str] = []
    for name in sorted(captures.keys()):
        text = " ".join(captures[name].split())
        if len(text) > 40:
            text = text[:37] + "..."
        parts.append("${}={}".format(name, text))
    return ", ".join(parts)


def _evaluate_ast_match(rule: dict, conn) -> dict:
    """Evaluate an ast_match rule: structural pattern matching with metavars.

    Rule shape:

    type: ast_match
    match:
      ast: "eval($EXPR)"
      language: python
      file_glob: "**/*.py"
      max_matches: 100
    """
    match = rule.get("match", {})
    exempt = rule.get("exempt", {})

    pattern = match.get("ast")
    if pattern is None and rule.get("type") == "ast_match":
        # Compatibility: allow `match.pattern` when type is explicit.
        pattern = match.get("pattern")

    language_filter = normalize_language_name(match.get("language"))
    file_glob = match.get("file_glob")
    max_matches = int(match.get("max_matches", 0) or 0)

    name = rule.get("name", "unnamed")
    severity = rule.get("severity", "error")

    if not isinstance(pattern, str) or not pattern.strip():
        return {
            "name": name,
            "severity": severity,
            "passed": False,
            "violations": [{
                "symbol": "",
                "file": rule.get("_file", ""),
                "line": None,
                "reason": "ast_match rule missing non-empty match.ast pattern",
            }],
        }

    try:
        root = find_project_root()
    except Exception:
        root = Path.cwd()

    rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
    violations: list[dict] = []
    compiled_cache: dict[str, object] = {}

    for row in rows:
        rel_path = row["path"]

        if file_glob and not _matches_glob(rel_path, file_glob):
            continue
        if _is_exempt("", rel_path, exempt):
            continue

        detected_lang = normalize_language_name(detect_language(rel_path))
        if language_filter and detected_lang != language_filter:
            continue
        if detected_lang is None:
            continue

        full_path = root / rel_path
        tree, source, parsed_lang = parse_file(full_path, detected_lang)
        if tree is None or source is None:
            continue

        active_lang = normalize_language_name(parsed_lang or detected_lang or language_filter)
        if active_lang is None:
            continue

        compiled = compiled_cache.get(active_lang)
        if compiled is None:
            try:
                compiled = compile_ast_pattern(pattern, active_lang)
            except Exception as exc:
                return {
                    "name": name,
                    "severity": severity,
                    "passed": False,
                    "violations": [{
                        "symbol": "",
                        "file": rule.get("_file", ""),
                        "line": None,
                        "reason": "AST pattern compile failed: {}".format(exc),
                    }],
                }
            compiled_cache[active_lang] = compiled

        remaining = 0
        if max_matches > 0:
            remaining = max_matches - len(violations)
            if remaining <= 0:
                break

        matches = find_ast_matches(
            tree,
            source,
            compiled,
            max_matches=remaining,
        )
        for m in matches:
            cap_text = _format_capture_preview(m.get("captures", {}))
            reason = "AST pattern matched: {}".format(pattern)
            if cap_text:
                reason += " ({})".format(cap_text)

            violations.append({
                "symbol": "",
                "file": rel_path,
                "line": m.get("line"),
                "reason": reason,
                "captures": m.get("captures", {}),
            })

            if max_matches > 0 and len(violations) >= max_matches:
                break

    return {
        "name": name,
        "severity": severity,
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Rule evaluation: dataflow_match
# ---------------------------------------------------------------------------


def _evaluate_dataflow_match(rule: dict, conn) -> dict:
    """Evaluate a dataflow_match rule using intra-procedural heuristics.

    Rule shape:

    type: dataflow_match
    match:
      patterns: [dead_assignment, unused_param, source_to_sink]
      file_glob: "**/*.py"
      max_matches: 100
      sources: ["input(", "request.args"]
      sinks: ["eval(", "exec("]
    """
    match = rule.get("match", {})
    exempt = rule.get("exempt", {})

    patterns = match.get("patterns")
    if patterns is None:
        # Compatibility aliases:
        # - singular "pattern"
        # - "dataflow" value
        patterns = match.get("pattern", match.get("dataflow"))

    file_glob = match.get("file_glob")
    max_matches = int(match.get("max_matches", 0) or 0)
    sources = match.get("sources")
    sinks = match.get("sinks")

    name = rule.get("name", "unnamed")
    severity = rule.get("severity", "error")

    findings = collect_dataflow_findings(
        conn,
        patterns=patterns,
        file_glob=file_glob,
        max_matches=max_matches if max_matches > 0 else 0,
        sources=sources,
        sinks=sinks,
    )

    violations: list[dict] = []
    for item in findings:
        symbol_name = item.get("symbol", "")
        file_path = item.get("file", "")
        if _is_exempt(symbol_name, file_path, exempt):
            continue
        violation = {
            "symbol": symbol_name,
            "file": file_path,
            "line": item.get("line"),
            "reason": item.get("reason", "dataflow rule matched"),
            "type": item.get("type"),
        }
        if "variable" in item:
            violation["variable"] = item["variable"]
        if "source" in item:
            violation["source"] = item["source"]
        if "sink" in item:
            violation["sink"] = item["sink"]
        violations.append(violation)

    if max_matches > 0:
        violations = violations[:max_matches]

    return {
        "name": name,
        "severity": severity,
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Rule type detection + dispatch
# ---------------------------------------------------------------------------


def _detect_rule_type(rule: dict) -> str:
    """Detect the rule type from its match specification.

    If ``type`` is explicitly set, it wins.
    Otherwise:
    - ``from`` + ``to`` => path_match
    - ``ast``           => ast_match
    - ``dataflow``      => dataflow_match
    - fallback          => symbol_match
    """
    explicit = rule.get("type")
    if isinstance(explicit, str) and explicit:
        return explicit

    match = rule.get("match", {})
    if "from" in match and "to" in match:
        return "path_match"
    if "ast" in match:
        return "ast_match"
    if "dataflow" in match or "patterns" in match and isinstance(match.get("patterns"), list):
        return "dataflow_match"
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

    rule_type = _detect_rule_type(rule)

    if rule_type == "path_match":
        return _evaluate_path_match(rule, conn)
    if rule_type == "ast_match":
        return _evaluate_ast_match(rule, conn)
    if rule_type == "dataflow_match":
        return _evaluate_dataflow_match(rule, conn)
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
