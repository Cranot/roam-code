"""Built-in structural rule pack for roam-code.

Provides 10 out-of-the-box governance rules that can be enabled,
disabled, or overridden via user config (.roam-rules.yml).

Each check function has signature::

    (conn, G, threshold) -> list[Violation]

where Violation is a dict with keys: symbol, file, line, reason
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from typing import Callable

import networkx as nx


def make_violation(symbol="", file="", line=None, reason=""):
    """Return a standard violation dict."""
    return {"symbol": symbol, "file": file, "line": line, "reason": reason}


@dataclass
class BuiltinRule:
    """A single built-in governance rule."""

    id: str
    severity: str
    description: str
    check: str
    threshold: float | None = None
    enabled: bool = True
    _fn: Callable | None = field(default=None, repr=False)

    def evaluate(self, conn: sqlite3.Connection, G: nx.DiGraph | None) -> list[dict]:
        """Run the check and return violations."""
        if self._fn is None:
            return []
        try:
            return self._fn(conn, G, self.threshold)
        except Exception as exc:  # pragma: no cover
            return [make_violation(reason="check error: {}".format(exc))]


def _check_no_circular_imports(conn, G, threshold):
    """Find cycles (SCCs with >= 2 members)."""
    if G is None or len(G) == 0:
        return []
    from roam.graph.cycles import find_cycles, format_cycles
    cycles = find_cycles(G, min_size=2)
    if not cycles:
        return []
    formatted = format_cycles(cycles, conn)
    violations = []
    for cyc in formatted:
        files = cyc.get("files", [])
        names = [s["name"] for s in cyc.get("symbols", [])]
        reason = "cycle of {} symbols: {}".format(cyc["size"], ", ".join(names[:5]))
        if len(names) > 5:
            reason += " (+{} more)".format(len(names) - 5)
        fpath = files[0] if files else ""
        violations.append(make_violation(
            symbol=names[0] if names else "", file=fpath, reason=reason))
    return violations


def _check_max_fan_out(conn, G, threshold):
    """Find symbols with more outgoing edges than threshold."""
    limit = int(threshold) if threshold is not None else 15
    if G is None:
        return []
    violations = []
    for node, out_deg in G.out_degree():
        if out_deg > limit:
            data = G.nodes[node]
            violations.append(make_violation(
                symbol=data.get("name", str(node)),
                file=data.get("file_path", ""),
                line=data.get("line_start"),
                reason="fan-out {} exceeds limit {}".format(out_deg, limit),
            ))
    violations.sort(key=lambda v: v["file"])
    return violations


def _check_max_fan_in(conn, G, threshold):
    """Find symbols with more incoming edges than threshold."""
    limit = int(threshold) if threshold is not None else 30
    if G is None:
        return []
    violations = []
    for node, in_deg in G.in_degree():
        if in_deg > limit:
            data = G.nodes[node]
            violations.append(make_violation(
                symbol=data.get("name", str(node)),
                file=data.get("file_path", ""),
                line=data.get("line_start"),
                reason="fan-in {} exceeds limit {}".format(in_deg, limit),
            ))
    violations.sort(key=lambda v: v["file"])
    return violations


def _check_max_file_complexity(conn, G, threshold):
    """Find files whose total cognitive complexity exceeds threshold."""
    limit = float(threshold) if threshold is not None else 50.0
    try:
        rows = conn.execute(
            """
            SELECT f.path, SUM(s.cognitive_complexity) AS total_cc
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            WHERE s.cognitive_complexity IS NOT NULL
            GROUP BY f.id, f.path
            HAVING total_cc > ?
            ORDER BY total_cc DESC
            """,
            (limit,),
        ).fetchall()
    except Exception:
        return []
    return [
        make_violation(
            file=row[0],
            reason="total cognitive complexity {:.0f} exceeds {:.0f}".format(row[1], limit),
        )
        for row in rows
    ]


def _check_max_file_length(conn, G, threshold):
    """Find files with more lines than threshold."""
    limit = int(threshold) if threshold is not None else 500
    try:
        rows = conn.execute(
            "SELECT path, loc FROM files WHERE loc IS NOT NULL AND loc > ? ORDER BY loc DESC",
            (limit,),
        ).fetchall()
    except Exception:
        return []
    return [
        make_violation(file=row[0], reason="{} lines exceeds limit {}".format(row[1], limit))
        for row in rows
    ]


def _is_test_path(path: str) -> bool:
    """Return True if path looks like a test file."""
    p = path.replace("\\", "/").lower()
    base = os.path.basename(p)
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    return "tests/" in p or "test/" in p or "__tests__/" in p or "spec/" in p


def _check_test_file_exists(conn, G, threshold):
    """Find source files that have no corresponding test file."""
    try:
        file_rows = conn.execute("SELECT id, path FROM files ORDER BY path").fetchall()
    except Exception:
        return []

    all_paths = [(r[0], r[1]) for r in file_rows]
    test_paths = {p for _, p in all_paths if _is_test_path(p)}

    def _stem(path):
        base = os.path.basename(path.replace("\\", "/"))
        s = base.rsplit(".", 1)[0].lower()
        s = s.removeprefix("test_")
        for sfx in ("_test", ".test", ".spec"):
            if s.endswith(sfx):
                s = s[:-len(sfx)]
        return s

    test_stems = {_stem(tp) for tp in test_paths}
    _SOURCE_EXTS = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".go",
        ".java", ".cs", ".rb", ".rs", ".php", ".cpp", ".c",
    }
    _SKIP_SEGS = (
        "migrations/", "docs/", "scripts/", "examples/", ".github/",
        "__pycache__/", "node_modules/", ".roam/", "vendor/",
    )

    violations = []
    for _, path in all_paths:
        if _is_test_path(path):
            continue
        p = path.replace("\\", "/").lower()
        if any(seg in p for seg in _SKIP_SEGS):
            continue
        if os.path.splitext(path)[1].lower() not in _SOURCE_EXTS:
            continue
        if _stem(path) not in test_stems:
            violations.append(make_violation(
                file=path,
                reason="no test file found for {}".format(os.path.basename(path)),
            ))
    violations.sort(key=lambda v: v["file"])
    return violations


def _check_no_god_classes(conn, G, threshold):
    """Find classes with more than threshold methods."""
    limit = int(threshold) if threshold is not None else 20
    _m = chr(39) + "method" + chr(39)
    _c = chr(39) + "class" + chr(39)
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, f.path, s.line_start, COUNT(s2.id) AS method_count "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "JOIN symbols s2 ON s2.parent_id = s.id AND s2.kind = {} "
            "WHERE s.kind = {} "
            "GROUP BY s.id HAVING method_count > ? ORDER BY method_count DESC".format(_m, _c),
            (limit,),
        ).fetchall()
    except Exception:
        return []
    violations = []
    for row in rows:
        violations.append(make_violation(
            symbol=row[1], file=row[2], line=row[3],
            reason="class has {} methods (limit {})".format(row[4], limit),
        ))
    return violations


def _check_no_deep_inheritance(conn, G, threshold):
    """Find classes with inheritance depth > threshold."""
    limit = int(threshold) if threshold is not None else 4
    _kinds = ("extends", "inherits", "implements")
    _ks = ", ".join(chr(39) + k + chr(39) for k in _kinds)
    try:
        edge_rows = conn.execute(
            "SELECT e.source_id, e.target_id FROM edges e WHERE e.kind IN ({})".format(_ks)
        ).fetchall()
    except Exception:
        return []
    if not edge_rows:
        return []

    inherit_graph = __import__("networkx").DiGraph()
    for row in edge_rows:
        inherit_graph.add_edge(row[0], row[1])

    import networkx as nx
    if not nx.is_directed_acyclic_graph(inherit_graph):
        cond = nx.condensation(inherit_graph)
        depths = {}
        for scc_node in nx.topological_sort(cond):
            preds = list(cond.predecessors(scc_node))
            depths[scc_node] = (max(depths[p] for p in preds) + 1) if preds else 0
        mapping = cond.graph["mapping"]
        node_depth = {node: depths[mapping[node]] for node in inherit_graph.nodes()}
    else:
        node_depth = {}
        for node in nx.topological_sort(inherit_graph):
            preds = list(inherit_graph.predecessors(node))
            node_depth[node] = (max(node_depth[p] for p in preds) + 1) if preds else 0

    violations = []
    for node, depth in node_depth.items():
        if depth > limit:
            try:
                row = conn.execute(
                    "SELECT s.name, f.path, s.line_start "
                    "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
                    (node,),
                ).fetchone()
                sym_name, fpath, line = (row[0], row[1], row[2]) if row else (str(node), "", None)
            except Exception:
                sym_name, fpath, line = str(node), "", None
            violations.append(make_violation(
                symbol=sym_name, file=fpath, line=line,
                reason="inheritance depth {} exceeds limit {}".format(depth, limit),
            ))
    violations.sort(key=lambda v: v["file"])
    return violations


def _check_layer_violation(conn, G, threshold):
    """Find edges where a higher layer imports a lower layer."""
    if G is None or len(G) == 0:
        return []
    from roam.graph.layers import detect_layers, find_violations
    from roam.db.connection import batched_in

    layer_map = detect_layers(G)
    if not layer_map:
        return []
    raw_violations = find_violations(G, layer_map)
    if not raw_violations:
        return []

    all_ids = list(
        {v["source"] for v in raw_violations} | {v["target"] for v in raw_violations}
    )
    lookup = {}
    for row in batched_in(
        conn,
        "SELECT s.id, s.name, f.path AS file_path, s.line_start "
        "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
        all_ids,
    ):
        lookup[row[0]] = {"name": row[1], "file": row[2], "line": row[3]}

    violations = []
    for v in raw_violations:
        src = lookup.get(v["source"], {})
        tgt = lookup.get(v["target"], {})
        violations.append(make_violation(
            symbol=src.get("name", "?"),
            file=src.get("file", ""),
            line=src.get("line"),
            reason="{} (L{}) imports {} (L{})".format(
                src.get("name", "?"), v["source_layer"],
                tgt.get("name", "?"), v["target_layer"],
            ),
        ))
    return violations


def _check_no_orphan_symbols(conn, G, threshold):
    """Find symbols with 0 incoming and 0 outgoing edges."""
    if G is None:
        return []
    _ORPHAN_KINDS = {"function", "method", "class", "module"}
    violations = []
    for node in G.nodes():
        if G.in_degree(node) == 0 and G.out_degree(node) == 0:
            data = G.nodes[node]
            if data.get("kind", "") not in _ORPHAN_KINDS:
                continue
            violations.append(make_violation(
                symbol=data.get("name", str(node)),
                file=data.get("file_path", ""),
                line=data.get("line_start"),
                reason="symbol has 0 incoming and 0 outgoing edges",
            ))
    violations.sort(key=lambda v: (v["file"], v.get("line") or 0))
    return violations


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_CHECK_FN_MAP: dict[str, Callable] = {
    "cycles":           _check_no_circular_imports,
    "fan-out":          _check_max_fan_out,
    "fan-in":           _check_max_fan_in,
    "file-complexity":  _check_max_file_complexity,
    "file-length":      _check_max_file_length,
    "test-file-exists": _check_test_file_exists,
    "god-class":        _check_no_god_classes,
    "deep-inheritance": _check_no_deep_inheritance,
    "layer-violation":  _check_layer_violation,
    "orphan-symbols":   _check_no_orphan_symbols,
}

BUILTIN_RULES: list[BuiltinRule] = [
    BuiltinRule(id="no-circular-imports", severity="error",
                description="No circular import chains",
                check="cycles", threshold=0, _fn=_check_no_circular_imports),
    BuiltinRule(id="max-fan-out", severity="warning",
                description="Functions should not call more than 15 other functions",
                check="fan-out", threshold=15, _fn=_check_max_fan_out),
    BuiltinRule(id="max-fan-in", severity="warning",
                description="Symbols should not be called by more than 30 others",
                check="fan-in", threshold=30, _fn=_check_max_fan_in),
    BuiltinRule(id="max-file-complexity", severity="warning",
                description="Max cognitive complexity per file (default 50)",
                check="file-complexity", threshold=50, _fn=_check_max_file_complexity),
    BuiltinRule(id="max-file-length", severity="info",
                description="Max lines per file (default 500)",
                check="file-length", threshold=500, _fn=_check_max_file_length),
    BuiltinRule(id="test-file-exists", severity="info",
                description="Source files should have corresponding test files",
                check="test-file-exists", threshold=None, _fn=_check_test_file_exists),
    BuiltinRule(id="no-god-classes", severity="warning",
                description="Classes with more than 20 methods",
                check="god-class", threshold=20, _fn=_check_no_god_classes),
    BuiltinRule(id="no-deep-inheritance", severity="warning",
                description="Inheritance depth should not exceed 4",
                check="deep-inheritance", threshold=4, _fn=_check_no_deep_inheritance),
    BuiltinRule(id="layer-violation", severity="warning",
                description="Lower layers should not import upper layers",
                check="layer-violation", threshold=None, _fn=_check_layer_violation),
    BuiltinRule(id="no-orphan-symbols", severity="info",
                description="Symbols with 0 incoming and 0 outgoing edges",
                check="orphan-symbols", threshold=None, _fn=_check_no_orphan_symbols),
]

BUILTIN_RULE_MAP: dict[str, BuiltinRule] = {r.id: r for r in BUILTIN_RULES}


def get_builtin_rule(rule_id: str) -> BuiltinRule | None:
    """Return the built-in rule with the given ID, or None."""
    return BUILTIN_RULE_MAP.get(rule_id)


# ---------------------------------------------------------------------------
# Quality rule profiles
# ---------------------------------------------------------------------------

BUILTIN_PROFILES: dict[str, dict] = {
    "default": {
        "description": "Standard quality rules",
        "rules": {
            r.id: {"enabled": True, "threshold": r.threshold}
            for r in BUILTIN_RULES
        },
    },
    "strict-security": {
        "description": "Security-focused rules with tighter thresholds",
        "extends": "default",
        "rules": {
            "max-fan-out": {"threshold": 10},
            "max-file-complexity": {"threshold": 30},
            "no-god-classes": {"threshold": 15},
        },
    },
    "ai-code-review": {
        "description": "Rules tuned for AI-generated code review",
        "extends": "default",
        "rules": {
            "max-file-length": {"threshold": 300},
            "max-fan-out": {"threshold": 10},
            "test-file-exists": {"enabled": True, "severity": "warning"},
        },
    },
    "legacy-maintenance": {
        "description": "Relaxed rules for legacy codebases",
        "extends": "default",
        "rules": {
            "max-file-complexity": {"threshold": 80},
            "max-file-length": {"threshold": 800},
            "no-god-classes": {"threshold": 30},
            "no-deep-inheritance": {"threshold": 6},
        },
    },
    "minimal": {
        "description": "Only critical rules",
        "rules": {
            "no-circular-imports": {"enabled": True},
            "layer-violation": {"enabled": True},
        },
    },
}


def resolve_profile(profile_name: str) -> list[dict]:
    """Resolve a named profile into a list of rule override dicts.

    Handles ``extends:`` inheritance by merging the parent profile's
    rule overrides first, then applying the child's overrides on top.

    Parameters
    ----------
    profile_name:
        Name of the profile (must be a key in ``BUILTIN_PROFILES``).

    Returns
    -------
    A list of rule-override dicts suitable for passing to
    ``_resolve_rules()`` as ``user_overrides``.

    Raises
    ------
    ValueError:
        If the profile name is not found.
    """
    if profile_name not in BUILTIN_PROFILES:
        raise ValueError(
            "Unknown profile: '{}'. Available: {}".format(
                profile_name, ", ".join(sorted(BUILTIN_PROFILES.keys()))
            )
        )

    profile = BUILTIN_PROFILES[profile_name]

    # Resolve parent first (inheritance chain)
    merged_rules: dict[str, dict] = {}
    parent_name = profile.get("extends")
    if parent_name:
        parent_overrides = resolve_profile(parent_name)
        for ov in parent_overrides:
            rid = ov.get("id", "")
            if rid:
                merged_rules[rid] = dict(ov)

    # Apply this profile's rules on top
    profile_rules = profile.get("rules", {})

    if profile_name == "minimal":
        # Minimal profile: only enable listed rules, disable everything else
        # Start by disabling all rules
        for rule in BUILTIN_RULES:
            if rule.id not in merged_rules:
                merged_rules[rule.id] = {"id": rule.id, "enabled": False}
            else:
                merged_rules[rule.id]["enabled"] = False
        # Then enable the ones listed
        for rule_id, overrides in profile_rules.items():
            if rule_id not in merged_rules:
                merged_rules[rule_id] = {"id": rule_id}
            merged_rules[rule_id].update(overrides)
    else:
        for rule_id, overrides in profile_rules.items():
            if rule_id not in merged_rules:
                merged_rules[rule_id] = {"id": rule_id}
            merged_rules[rule_id].update(overrides)

    return list(merged_rules.values())


def list_profiles() -> list[dict]:
    """Return a list of available profile summaries.

    Each dict has keys: name, description, extends, rule_count.
    """
    result = []
    for name, prof in sorted(BUILTIN_PROFILES.items()):
        result.append({
            "name": name,
            "description": prof.get("description", ""),
            "extends": prof.get("extends"),
            "rule_count": len(prof.get("rules", {})),
        })
    return result
