"""Code smell detection: query DB signals to find structural anti-patterns.

Each detector has signature ``(conn) -> list[dict]`` and returns findings
with fields: smell_id, severity, symbol_name, kind, location, metric_value,
threshold, description.

Severity levels:
- critical: High-impact structural issue that should be refactored
- warning: Moderate issue worth investigating
- info: Minor concern or code style observation

15 deterministic detectors querying the SQLite index. No heuristics,
no source reading -- pure DB queries for speed and reproducibility.
"""

from __future__ import annotations

import re


def _loc(path: str, line: int | None) -> str:
    if line is not None:
        return f"{path}:{line}"
    return path


def _finding(
    smell_id: str,
    severity: str,
    symbol_name: str,
    kind: str,
    location: str,
    metric_value: float | int,
    threshold: float | int,
    description: str,
) -> dict:
    return {
        "smell_id": smell_id,
        "severity": severity,
        "symbol_name": symbol_name,
        "kind": kind,
        "location": location,
        "metric_value": metric_value,
        "threshold": threshold,
        "description": description,
    }


def _parse_param_count(signature: str | None) -> int:
    """Count parameters from a signature string, excluding self/cls."""
    if not signature:
        return 0
    # Extract content between first pair of parentheses
    m = re.search(r'\(([^)]*)\)', signature)
    if not m:
        return 0
    params_str = m.group(1).strip()
    if not params_str:
        return 0
    # Split by comma, handling nested generics/brackets
    depth = 0
    parts: list[str] = []
    current: list[str] = []
    for ch in params_str:
        if ch in ('(', '[', '<', '{'):
            depth += 1
            current.append(ch)
        elif ch in (')', ']', '>', '}'):
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current).strip())
    # Filter out self/cls and empty parts
    filtered = [
        p for p in parts
        if p and p.split(':')[0].split('=')[0].strip().lower() not in ('self', 'cls', '')
    ]
    return len(filtered)


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------

def detect_brain_method(conn) -> list[dict]:
    """Functions with complexity > 60 AND > 100 LOC."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, s.line_end, f.path as file_path, "
        "sm.cognitive_complexity "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN symbol_metrics sm ON sm.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND sm.cognitive_complexity > 60 "
        "AND (s.line_end - s.line_start) > 100"
    ).fetchall()
    results = []
    for r in rows:
        loc_str = _loc(r["file_path"], r["line_start"])
        line_count = (r["line_end"] or 0) - (r["line_start"] or 0)
        results.append(_finding(
            "brain-method", "critical",
            r["name"], r["kind"], loc_str,
            r["cognitive_complexity"], 60,
            f"Brain method: complexity {r['cognitive_complexity']:.0f}, {line_count} LOC",
        ))
    return results


def detect_deep_nesting(conn) -> list[dict]:
    """Symbols with nesting depth > 4."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, f.path as file_path, "
        "sm.nesting_depth "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN symbol_metrics sm ON sm.symbol_id = s.id "
        "WHERE sm.nesting_depth > 4 "
        "AND s.kind IN ('function', 'method')"
    ).fetchall()
    results = []
    for r in rows:
        loc_str = _loc(r["file_path"], r["line_start"])
        results.append(_finding(
            "deep-nesting", "warning",
            r["name"], r["kind"], loc_str,
            r["nesting_depth"], 4,
            f"Deep nesting: depth {r['nesting_depth']}",
        ))
    return results


def detect_long_params(conn) -> list[dict]:
    """Functions with > 5 parameters (excluding self/cls)."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, s.signature, f.path as file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.signature IS NOT NULL "
        "AND s.signature != ''"
    ).fetchall()
    results = []
    for r in rows:
        count = _parse_param_count(r["signature"])
        if count > 5:
            loc_str = _loc(r["file_path"], r["line_start"])
            results.append(_finding(
                "long-params", "warning",
                r["name"], r["kind"], loc_str,
                count, 5,
                f"Long parameter list: {count} params",
            ))
    return results


def detect_large_class(conn) -> list[dict]:
    """Classes with > 500 LOC AND > 20 methods."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, f.path as file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = 'class' "
        "AND (s.line_end - s.line_start) > 500"
    ).fetchall()
    results = []
    for r in rows:
        method_count = conn.execute(
            "SELECT COUNT(*) FROM symbols "
            "WHERE file_id = (SELECT file_id FROM symbols WHERE id = ?) "
            "AND kind = 'method' "
            "AND line_start >= ? AND line_end <= ?",
            (r["id"], r["line_start"] or 0, r["line_end"] or 0),
        ).fetchone()[0]
        if method_count > 20:
            loc_str = _loc(r["file_path"], r["line_start"])
            line_count = (r["line_end"] or 0) - (r["line_start"] or 0)
            results.append(_finding(
                "large-class", "critical",
                r["name"], r["kind"], loc_str,
                line_count, 500,
                f"Large class: {line_count} LOC, {method_count} methods",
            ))
    return results


def detect_god_class(conn) -> list[dict]:
    """Classes with > 30 methods OR > 1000 LOC."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
        "f.path as file_path, s.file_id "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = 'class'"
    ).fetchall()
    results = []
    for r in rows:
        line_count = (r["line_end"] or 0) - (r["line_start"] or 0)
        method_count = conn.execute(
            "SELECT COUNT(*) FROM symbols "
            "WHERE file_id = ? "
            "AND kind = 'method' "
            "AND line_start >= ? AND line_end <= ?",
            (r["file_id"], r["line_start"] or 0, r["line_end"] or 0),
        ).fetchone()[0]
        if method_count > 30 or line_count > 1000:
            loc_str = _loc(r["file_path"], r["line_start"])
            metric = max(method_count, line_count)
            threshold = 30 if method_count > 30 else 1000
            parts = []
            if method_count > 30:
                parts.append(f"{method_count} methods")
            if line_count > 1000:
                parts.append(f"{line_count} LOC")
            results.append(_finding(
                "god-class", "critical",
                r["name"], r["kind"], loc_str,
                metric, threshold,
                f"God class: {', '.join(parts)}",
            ))
    return results


def detect_feature_envy(conn) -> list[dict]:
    """Functions where > 50% of edge targets are in other files (min 4 refs)."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.file_id, "
        "f.path as file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method')"
    ).fetchall()
    results = []
    for r in rows:
        edges = conn.execute(
            "SELECT e.target_id, t.file_id as target_file_id "
            "FROM edges e "
            "JOIN symbols t ON e.target_id = t.id "
            "WHERE e.source_id = ?",
            (r["id"],),
        ).fetchall()
        total = len(edges)
        if total < 4:
            continue
        external = sum(1 for e in edges if e["target_file_id"] != r["file_id"])
        ratio = external / total
        if ratio > 0.5:
            loc_str = _loc(r["file_path"], r["line_start"])
            results.append(_finding(
                "feature-envy", "warning",
                r["name"], r["kind"], loc_str,
                round(ratio * 100, 1), 50,
                f"Feature envy: {external}/{total} refs ({ratio:.0%}) to other files",
            ))
    return results


def detect_shotgun_surgery(conn) -> list[dict]:
    """Symbols with in_degree > 7 in graph_metrics."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, f.path as file_path, "
        "gm.in_degree "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN graph_metrics gm ON gm.symbol_id = s.id "
        "WHERE gm.in_degree > 7"
    ).fetchall()
    results = []
    for r in rows:
        loc_str = _loc(r["file_path"], r["line_start"])
        results.append(_finding(
            "shotgun-surgery", "warning",
            r["name"], r["kind"], loc_str,
            r["in_degree"], 7,
            f"Shotgun surgery: {r['in_degree']} incoming dependencies",
        ))
    return results


def detect_data_clumps(conn) -> list[dict]:
    """3+ params repeated across 3+ functions (group by sorted first-3 param names)."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, s.signature, f.path as file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.signature IS NOT NULL "
        "AND s.signature != ''"
    ).fetchall()

    # Build param-group map
    from collections import defaultdict
    param_groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        sig = r["signature"]
        m = re.search(r'\(([^)]*)\)', sig or "")
        if not m:
            continue
        params_str = m.group(1).strip()
        if not params_str:
            continue
        # Simple split by comma (top-level only)
        depth = 0
        parts: list[str] = []
        current: list[str] = []
        for ch in params_str:
            if ch in ('(', '[', '<', '{'):
                depth += 1
                current.append(ch)
            elif ch in (')', ']', '>', '}'):
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append(''.join(current).strip())
        # Extract just param names, skip self/cls
        names = []
        for p in parts:
            name = p.split(':')[0].split('=')[0].strip().lower()
            if name and name not in ('self', 'cls', ''):
                names.append(name)
        if len(names) >= 3:
            key = ",".join(sorted(names[:3]))
            param_groups[key].append(r)

    results = []
    seen_groups: set[str] = set()
    for key, funcs in param_groups.items():
        if len(funcs) >= 3 and key not in seen_groups:
            seen_groups.add(key)
            # Report one finding per clump using the first function
            r = funcs[0]
            loc_str = _loc(r["file_path"], r["line_start"])
            func_names = [f["name"] for f in funcs[:5]]
            results.append(_finding(
                "data-clumps", "info",
                r["name"], r["kind"], loc_str,
                len(funcs), 3,
                f"Data clump: params ({key}) repeated in {len(funcs)} functions: "
                f"{', '.join(func_names)}",
            ))
    return results


def detect_dead_params(conn) -> list[dict]:
    """Functions with 4+ params but complexity <= 1 (likely unused params)."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, s.signature, f.path as file_path, "
        "sm.cognitive_complexity "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN symbol_metrics sm ON sm.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND sm.cognitive_complexity <= 1 "
        "AND s.signature IS NOT NULL "
        "AND s.signature != ''"
    ).fetchall()
    results = []
    for r in rows:
        count = _parse_param_count(r["signature"])
        if count >= 4:
            loc_str = _loc(r["file_path"], r["line_start"])
            results.append(_finding(
                "dead-params", "info",
                r["name"], r["kind"], loc_str,
                count, 4,
                f"Dead params: {count} params but complexity {r['cognitive_complexity']:.0f}",
            ))
    return results


def detect_empty_catch(conn) -> list[dict]:
    """Placeholder: empty catch/except blocks. Returns []."""
    return []


def detect_low_cohesion(conn) -> list[dict]:
    """Classes with 5+ methods but fewer than methods/2 internal edges."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
        "f.path as file_path, s.file_id "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = 'class'"
    ).fetchall()
    results = []
    for r in rows:
        # Count methods within the class line range
        methods = conn.execute(
            "SELECT id FROM symbols "
            "WHERE file_id = ? "
            "AND kind = 'method' "
            "AND line_start >= ? AND line_end <= ?",
            (r["file_id"], r["line_start"] or 0, r["line_end"] or 0),
        ).fetchall()
        method_count = len(methods)
        if method_count < 5:
            continue
        method_ids = [m["id"] for m in methods]
        if not method_ids:
            continue
        # Count internal edges between methods of this class
        ph = ",".join("?" for _ in method_ids)
        internal_edges = conn.execute(
            f"SELECT COUNT(*) FROM edges "
            f"WHERE source_id IN ({ph}) AND target_id IN ({ph})",
            method_ids + method_ids,
        ).fetchone()[0]
        threshold = method_count // 2
        if internal_edges < threshold:
            loc_str = _loc(r["file_path"], r["line_start"])
            results.append(_finding(
                "low-cohesion", "warning",
                r["name"], r["kind"], loc_str,
                internal_edges, threshold,
                f"Low cohesion: {method_count} methods but only {internal_edges} internal edges "
                f"(threshold: {threshold})",
            ))
    return results


def detect_message_chain(conn) -> list[dict]:
    """Functions with out_degree > 10 in graph_metrics."""
    rows = conn.execute(
        "SELECT s.name, s.kind, s.line_start, f.path as file_path, "
        "gm.out_degree "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN graph_metrics gm ON gm.symbol_id = s.id "
        "WHERE gm.out_degree > 10 "
        "AND s.kind IN ('function', 'method')"
    ).fetchall()
    results = []
    for r in rows:
        loc_str = _loc(r["file_path"], r["line_start"])
        results.append(_finding(
            "message-chain", "info",
            r["name"], r["kind"], loc_str,
            r["out_degree"], 10,
            f"Message chain: {r['out_degree']} outgoing calls",
        ))
    return results


def detect_refused_bequest(conn) -> list[dict]:
    """Placeholder: classes that override parent methods to do nothing. Returns []."""
    return []


def detect_primitive_obsession(conn) -> list[dict]:
    """Placeholder: excessive use of primitive types. Returns []."""
    return []


def detect_duplicate_conditionals(conn) -> list[dict]:
    """Placeholder: repeated conditional logic. Returns []."""
    return []


# ---------------------------------------------------------------------------
# Detector registry
# ---------------------------------------------------------------------------

ALL_DETECTORS: list[tuple[str, callable]] = [
    ("brain-method", detect_brain_method),
    ("deep-nesting", detect_deep_nesting),
    ("long-params", detect_long_params),
    ("large-class", detect_large_class),
    ("god-class", detect_god_class),
    ("feature-envy", detect_feature_envy),
    ("shotgun-surgery", detect_shotgun_surgery),
    ("data-clumps", detect_data_clumps),
    ("dead-params", detect_dead_params),
    ("empty-catch", detect_empty_catch),
    ("low-cohesion", detect_low_cohesion),
    ("message-chain", detect_message_chain),
    ("refused-bequest", detect_refused_bequest),
    ("primitive-obsession", detect_primitive_obsession),
    ("duplicate-conditionals", detect_duplicate_conditionals),
]

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def run_all_detectors(conn) -> list[dict]:
    """Run all 15 smell detectors and return combined findings.

    Returns list of finding dicts sorted by severity (critical first).
    """
    findings: list[dict] = []
    for _smell_id, detect_fn in ALL_DETECTORS:
        try:
            hits = detect_fn(conn)
        except Exception:
            continue
        findings.extend(hits)
    # Sort: critical first, then warning, then info
    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.get("severity", "info"), 2))
    return findings


def file_health_scores(conn) -> dict[str, float]:
    """Compute per-file health scores from smell findings.

    Returns {file_path: score} where score is 1-10 (10 = healthy).
    Penalties: critical = -3, warning = -1.5, info = -0.5. Min score = 1.
    """
    findings = run_all_detectors(conn)
    penalties: dict[str, float] = {}
    for f in findings:
        loc_str = f.get("location", "")
        # Extract file path from location (path:line or path)
        file_path = loc_str.split(":")[0] if ":" in loc_str else loc_str
        if not file_path:
            continue
        sev = f.get("severity", "info")
        if sev == "critical":
            penalty = 3.0
        elif sev == "warning":
            penalty = 1.5
        else:
            penalty = 0.5
        penalties[file_path] = penalties.get(file_path, 0.0) + penalty

    # Get all indexed files
    files = conn.execute("SELECT path FROM files").fetchall()
    scores: dict[str, float] = {}
    for row in files:
        path = row["path"]
        penalty = penalties.get(path, 0.0)
        score = max(1.0, 10.0 - penalty)
        scores[path] = round(score, 1)
    return scores
