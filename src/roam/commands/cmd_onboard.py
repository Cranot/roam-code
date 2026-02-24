"""Generate a structured new-developer onboarding guide for a codebase.

Combines architecture overview, key entry points, critical paths,
hotspots, bus factor risks, and suggested first-read files into a
single, comprehensive guide.  Always current because it is computed
from the index, not hand-written documentation.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

import click

from roam.db.connection import open_db, find_project_root, batched_in
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import is_test_file


# ---------------------------------------------------------------------------
# Detail levels
# ---------------------------------------------------------------------------

_LIMITS = {
    "brief":  {"entry_points": 5,  "critical": 5,  "risk": 3,  "reading": 5,  "clusters": 5},
    "normal": {"entry_points": 10, "critical": 8,  "risk": 5,  "reading": 10, "clusters": 8},
    "full":   {"entry_points": 15, "critical": 15, "risk": 10, "reading": 20, "clusters": 15},
}


# ---------------------------------------------------------------------------
# Section: Project overview
# ---------------------------------------------------------------------------

def _project_overview(conn):
    """Gather top-level project statistics."""
    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

    # File breakdown by role
    role_rows = conn.execute(
        "SELECT file_role, COUNT(*) as cnt FROM files GROUP BY file_role"
    ).fetchall()
    role_counts = {r["file_role"]: r["cnt"] for r in role_rows}
    source_count = role_counts.get("source", 0)
    test_count = role_counts.get("test", 0)
    config_count = role_counts.get("config", 0) + role_counts.get("build", 0)
    docs_count = role_counts.get("docs", 0)

    # Languages
    lang_rows = conn.execute(
        "SELECT language, COUNT(*) as cnt FROM files "
        "WHERE language IS NOT NULL "
        "GROUP BY language ORDER BY cnt DESC"
    ).fetchall()
    languages = []
    for r in lang_rows:
        pct = round(r["cnt"] * 100 / total_files, 0) if total_files else 0
        languages.append({
            "name": r["language"],
            "files": r["cnt"],
            "pct": int(pct),
        })

    primary_language = languages[0]["name"] if languages else "unknown"

    # Test coverage presence
    has_tests = test_count > 0 or conn.execute(
        "SELECT COUNT(*) FROM files "
        "WHERE path LIKE '%test%' OR path LIKE '%spec%'"
    ).fetchone()[0] > 0

    return {
        "total_files": total_files,
        "source_files": source_count,
        "test_files": test_count,
        "config_files": config_count,
        "docs_files": docs_count,
        "total_symbols": total_symbols,
        "languages": languages,
        "primary_language": primary_language,
        "has_tests": has_tests,
    }


# ---------------------------------------------------------------------------
# Section: Architecture overview
# ---------------------------------------------------------------------------

def _architecture_overview(conn, limit_clusters):
    """Analyze layers and clusters from the graph."""
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers

        G = build_symbol_graph(conn)
        layer_map = detect_layers(G)
    except Exception:
        layer_map = {}

    # Compute per-layer file breakdown
    layer_count = 0
    layer_descriptions = []
    if layer_map:
        max_layer = max(layer_map.values())
        layer_count = max_layer + 1

        # Group symbols by layer
        layer_groups = defaultdict(list)
        for node_id, layer_num in layer_map.items():
            layer_groups[layer_num].append(node_id)

        # For each layer, find the dominant directories
        for layer_num in sorted(layer_groups.keys()):
            sym_ids = layer_groups[layer_num][:500]
            if not sym_ids:
                continue
            rows = batched_in(
                conn,
                "SELECT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.id IN ({ph})",
                sym_ids,
            )
            dirs = [os.path.dirname(r["path"]).replace("\\", "/") for r in rows]
            dir_counts = Counter(dirs)
            top_dirs = [d.rstrip("/").rsplit("/", 1)[-1] or "."
                        for d, _ in dir_counts.most_common(3)]
            dirs_str = ", ".join(d for d in top_dirs if d)

            # Role label
            if layer_num == 0:
                role = "foundation/core"
            elif layer_num == max_layer:
                role = "interface/entry"
            else:
                role = "logic/middleware"

            layer_descriptions.append({
                "layer": layer_num,
                "role": role,
                "directories": dirs_str,
                "symbol_count": len(layer_groups[layer_num]),
            })

    # Clusters
    cluster_rows = conn.execute(
        "SELECT cluster_id, cluster_label, COUNT(*) as size "
        "FROM clusters GROUP BY cluster_id ORDER BY size DESC"
    ).fetchall()
    clusters = []
    for cr in cluster_rows[:limit_clusters]:
        top_syms = conn.execute(
            "SELECT s.name FROM clusters c "
            "JOIN symbols s ON c.symbol_id = s.id "
            "WHERE c.cluster_id = ? "
            "ORDER BY s.name LIMIT 4",
            (cr["cluster_id"],),
        ).fetchall()
        clusters.append({
            "id": cr["cluster_id"],
            "label": cr["cluster_label"] or f"cluster-{cr['cluster_id']}",
            "size": cr["size"],
            "top_symbols": [s["name"] for s in top_syms],
        })

    return {
        "layer_count": layer_count,
        "layers": layer_descriptions,
        "cluster_count": len(cluster_rows),
        "clusters": clusters,
    }


# ---------------------------------------------------------------------------
# Section: Entry points (top PageRank symbols)
# ---------------------------------------------------------------------------

def _entry_points(conn, limit):
    """Find highest PageRank symbols as main entry points."""
    rows = conn.execute(
        "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, gm.pagerank, gm.in_degree, gm.out_degree "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN graph_metrics gm ON s.id = gm.symbol_id "
        "WHERE s.kind IN ('function', 'class', 'method', 'interface') "
        "AND s.is_exported = 1 "
        "ORDER BY gm.pagerank DESC LIMIT ?",
        (limit,),
    ).fetchall()

    results = []
    for r in rows:
        fan_in = r["in_degree"] or 0
        fan_out = r["out_degree"] or 0

        if fan_in > 20:
            why = f"highly imported ({fan_in} dependents)"
        elif fan_in > 10:
            why = f"widely used ({fan_in} dependents)"
        elif r["kind"] == "class":
            why = "core class"
        elif fan_out > 10:
            why = f"orchestrator ({fan_out} calls)"
        else:
            why = "high PageRank"

        results.append({
            "name": r["qualified_name"] or r["name"],
            "kind": r["kind"],
            "file": r["file_path"],
            "line": r["line_start"],
            "location": loc(r["file_path"], r["line_start"]),
            "pagerank": round(r["pagerank"] or 0, 4),
            "fan_in": fan_in,
            "fan_out": fan_out,
            "why": why,
        })

    return results


# ---------------------------------------------------------------------------
# Section: Critical paths (backbone files)
# ---------------------------------------------------------------------------

def _critical_paths(conn, limit):
    """Identify files that are critical to understand: high PageRank + high churn."""
    # Files with highest aggregate PageRank (architecture backbone)
    pr_rows = conn.execute(
        "SELECT f.path, SUM(gm.pagerank) as total_pr, COUNT(gm.symbol_id) as sym_count "
        "FROM graph_metrics gm "
        "JOIN symbols s ON gm.symbol_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "GROUP BY f.path "
        "ORDER BY total_pr DESC "
        "LIMIT ?",
        (limit * 3,),
    ).fetchall()

    # Files with highest churn (most actively developed)
    churn_rows = conn.execute(
        "SELECT f.path, fs.total_churn, fs.commit_count "
        "FROM file_stats fs "
        "JOIN files f ON fs.file_id = f.id "
        "WHERE fs.total_churn > 0 "
        "ORDER BY fs.total_churn DESC "
        "LIMIT ?",
        (limit * 3,),
    ).fetchall()

    pr_map = {r["path"]: round(r["total_pr"] or 0, 4) for r in pr_rows}
    churn_map = {r["path"]: r["total_churn"] or 0 for r in churn_rows}
    commit_map = {r["path"]: r["commit_count"] or 0 for r in churn_rows}
    all_paths = set(pr_map.keys()) | set(churn_map.keys())

    results = []
    for path in all_paths:
        if is_test_file(path):
            continue
        pr = pr_map.get(path, 0)
        churn = churn_map.get(path, 0)
        commits = commit_map.get(path, 0)
        # Combined importance score
        importance = pr * 1000 + churn
        in_both = path in pr_map and path in churn_map

        reason_parts = []
        if path in pr_map:
            reason_parts.append("high PageRank")
        if path in churn_map:
            reason_parts.append("high churn")
        if in_both:
            reason_parts = ["backbone + active development"]

        results.append({
            "path": path,
            "pagerank": pr,
            "churn": churn,
            "commits": commits,
            "importance": round(importance, 2),
            "must_understand": in_both,
            "reason": ", ".join(reason_parts),
        })

    results.sort(key=lambda r: (-int(r["must_understand"]), -r["importance"]))
    return results[:limit]


# ---------------------------------------------------------------------------
# Section: Risk areas
# ---------------------------------------------------------------------------

def _risk_areas(conn, limit):
    """Identify bus factor = 1, high complexity, and hotspot files."""
    risks = []

    # Bus factor = 1 (single contributor files)
    bf_rows = conn.execute(
        "SELECT f.path, fs.distinct_authors, fs.commit_count "
        "FROM file_stats fs "
        "JOIN files f ON fs.file_id = f.id "
        "WHERE fs.distinct_authors = 1 AND fs.commit_count >= 3 "
        "ORDER BY fs.total_churn DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    for r in bf_rows:
        if is_test_file(r["path"]):
            continue
        risks.append({
            "path": r["path"],
            "type": "bus_factor",
            "detail": f"single contributor, {r['commit_count']} commits",
            "severity": "HIGH",
        })

    # High complexity files
    cc_rows = conn.execute(
        "SELECT f.path, MAX(sm.cognitive_complexity) as max_cc, "
        "AVG(sm.cognitive_complexity) as avg_cc "
        "FROM symbol_metrics sm "
        "JOIN symbols s ON sm.symbol_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "GROUP BY f.path "
        "HAVING max_cc >= 15 "
        "ORDER BY max_cc DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    for r in cc_rows:
        if is_test_file(r["path"]):
            continue
        max_cc = round(r["max_cc"] or 0)
        severity = "CRITICAL" if max_cc >= 25 else "HIGH"
        risks.append({
            "path": r["path"],
            "type": "high_complexity",
            "detail": f"max CC={max_cc}",
            "severity": severity,
        })

    # Hotspots (high churn + high complexity)
    hotspot_rows = conn.execute(
        "SELECT f.path, fs.total_churn, fs.complexity "
        "FROM file_stats fs "
        "JOIN files f ON fs.file_id = f.id "
        "WHERE fs.total_churn > 0 AND fs.complexity > 5 "
        "ORDER BY fs.complexity * fs.total_churn DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    for r in hotspot_rows:
        if is_test_file(r["path"]):
            continue
        risks.append({
            "path": r["path"],
            "type": "hotspot",
            "detail": f"churn={r['total_churn']}, complexity={round(r['complexity'] or 0, 1)}",
            "severity": "MEDIUM",
        })

    # Deduplicate by path, keeping highest severity
    severity_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    seen = {}
    for r in risks:
        key = r["path"]
        if key not in seen or severity_rank.get(r["severity"], 9) < severity_rank.get(seen[key]["severity"], 9):
            seen[key] = r

    deduped = sorted(seen.values(), key=lambda r: severity_rank.get(r["severity"], 9))
    return deduped[:limit]


# ---------------------------------------------------------------------------
# Section: Suggested reading order
# ---------------------------------------------------------------------------

def _suggested_reading_order(conn, entry_points, critical_paths, architecture, limit):
    """Build a prioritized reading order for new developers."""
    order = []
    seen = set()
    priority = 1

    # 1. Top entry point files (start here)
    for ep in entry_points[:3]:
        path = ep["file"]
        if path not in seen:
            seen.add(path)
            order.append({
                "priority": priority,
                "path": path,
                "reason": "entry point",
            })
            priority += 1

    # 2. Layer-by-layer from top (interface) to bottom (core)
    layers = architecture.get("layers", [])
    # Sort layers in reverse order (highest layer = interface first)
    for layer_desc in sorted(layers, key=lambda l: -l["layer"]):
        dirs_str = layer_desc.get("directories", "")
        if not dirs_str:
            continue
        # Find top files in this layer's directories
        for dir_name in dirs_str.split(", ")[:2]:
            if not dir_name or dir_name == ".":
                continue
            pattern = f"%{dir_name}%"
            rows = conn.execute(
                "SELECT f.path, COALESCE(SUM(gm.pagerank), 0) as pr "
                "FROM files f "
                "LEFT JOIN symbols s ON s.file_id = f.id "
                "LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id "
                "WHERE f.path LIKE ? AND f.language IS NOT NULL "
                "GROUP BY f.path ORDER BY pr DESC LIMIT 2",
                (pattern,),
            ).fetchall()
            for r in rows:
                if r["path"] not in seen:
                    seen.add(r["path"])
                    order.append({
                        "priority": priority,
                        "path": r["path"],
                        "reason": f"layer {layer_desc['layer']} ({layer_desc['role']})",
                    })
                    priority += 1

    # 3. Must-understand files from critical paths
    for cp in critical_paths[:5]:
        if cp["path"] not in seen:
            seen.add(cp["path"])
            order.append({
                "priority": priority,
                "path": cp["path"],
                "reason": cp["reason"],
            })
            priority += 1

    # 4. Test files for critical modules
    for ep in entry_points[:3]:
        file_path = ep["file"]
        frow = conn.execute(
            "SELECT id FROM files WHERE path = ?", (file_path,)
        ).fetchone()
        if not frow:
            continue
        test_rows = conn.execute(
            "SELECT f.path FROM file_edges fe "
            "JOIN files f ON fe.source_file_id = f.id "
            "WHERE fe.target_file_id = ? LIMIT 3",
            (frow["id"],),
        ).fetchall()
        for tr in test_rows:
            if is_test_file(tr["path"]) and tr["path"] not in seen:
                seen.add(tr["path"])
                order.append({
                    "priority": priority,
                    "path": tr["path"],
                    "reason": f"tests for {os.path.basename(file_path)}",
                })
                priority += 1

    return order[:limit]


# ---------------------------------------------------------------------------
# Section: Key conventions
# ---------------------------------------------------------------------------

def _key_conventions(conn):
    """Detect naming conventions and patterns from the indexed symbols."""
    _SNAKE = re.compile(r'^[a-z_][a-z0-9_]*$')
    _CAMEL = re.compile(r'^[a-z][a-zA-Z0-9]*$')
    _PASCAL = re.compile(r'^[A-Z][a-zA-Z0-9]*$')
    _UPPER = re.compile(r'^[A-Z_][A-Z0-9_]*$')

    conventions = {}
    for kind in ("function", "class", "method", "variable"):
        rows = conn.execute(
            "SELECT name FROM symbols WHERE kind = ?", (kind,)
        ).fetchall()
        if not rows:
            continue
        names = [r["name"] for r in rows]
        counts = {"snake_case": 0, "camelCase": 0, "PascalCase": 0, "UPPER_SNAKE": 0}
        for n in names:
            if _UPPER.match(n) and "_" in n:
                counts["UPPER_SNAKE"] += 1
            elif _PASCAL.match(n):
                counts["PascalCase"] += 1
            elif _SNAKE.match(n):
                counts["snake_case"] += 1
            elif _CAMEL.match(n):
                counts["camelCase"] += 1

        if counts:
            dominant = max(counts, key=counts.get)
            total = len(names)
            pct = round(counts[dominant] * 100 / total, 0) if total else 0
            conventions[kind] = {"style": dominant, "pct": int(pct), "total": total}

    # Test file patterns
    test_files = conn.execute(
        "SELECT path FROM files WHERE file_role = 'test'"
    ).fetchall()
    test_patterns = Counter()
    for r in test_files:
        bn = os.path.basename(r["path"])
        if bn.startswith("test_"):
            test_patterns["test_*.py"] += 1
        elif bn.endswith("_test.py"):
            test_patterns["*_test.py"] += 1
        elif bn.endswith(".test.js") or bn.endswith(".test.ts"):
            test_patterns["*.test.{js,ts}"] += 1
        elif bn.endswith(".spec.js") or bn.endswith(".spec.ts"):
            test_patterns["*.spec.{js,ts}"] += 1
        elif bn.endswith("_test.go"):
            test_patterns["*_test.go"] += 1

    dominant_test_pattern = test_patterns.most_common(1)[0][0] if test_patterns else None

    # Import patterns (absolute vs relative)
    edge_rows = conn.execute(
        "SELECT COUNT(*) as cnt FROM edges WHERE kind = 'imports'"
    ).fetchone()
    import_count = edge_rows["cnt"] if edge_rows else 0

    return {
        "naming": conventions,
        "test_pattern": dominant_test_pattern,
        "test_file_count": len(test_files),
        "import_edge_count": import_count,
    }


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

def _emit_text(detail, overview, architecture, entry_points,
               critical_paths, risks, reading_order, conventions):
    """Render the onboarding guide as plain text."""
    click.echo("=== ONBOARDING GUIDE ===\n")

    # --- Project Overview ---
    lang_parts = []
    for l in overview["languages"][:5]:
        lang_parts.append(f"{l['name']} ({l['pct']}%)")
    lang_str = ", ".join(lang_parts)
    if len(overview["languages"]) > 5:
        lang_str += f" +{len(overview['languages']) - 5} more"

    role_parts = []
    if overview["source_files"]:
        role_parts.append(f"{overview['source_files']} source")
    if overview["test_files"]:
        role_parts.append(f"{overview['test_files']} test")
    if overview["config_files"]:
        role_parts.append(f"{overview['config_files']} config")
    if overview["docs_files"]:
        role_parts.append(f"{overview['docs_files']} docs")
    role_str = " (" + ", ".join(role_parts) + ")" if role_parts else ""

    click.echo("PROJECT OVERVIEW")
    click.echo(f"  Files: {overview['total_files']}{role_str}")
    click.echo(f"  Symbols: {overview['total_symbols']:,}")
    click.echo(f"  Languages: {lang_str}")
    if overview["has_tests"]:
        click.echo(f"  Tests: present ({overview['test_files']} test files)")
    else:
        click.echo("  Tests: none detected")
    click.echo()

    # --- Architecture Overview ---
    click.echo(f"ARCHITECTURE ({architecture['layer_count']} layers, {architecture['cluster_count']} modules)")
    if architecture["layers"]:
        for ld in architecture["layers"]:
            click.echo(f"  Layer {ld['layer']} ({ld['role']}): {ld['directories']}"
                        f" -- {ld['symbol_count']} symbols")
    if architecture["clusters"] and detail != "brief":
        click.echo()
        click.echo(f"  Modules ({len(architecture['clusters'])}):")
        for cl in architecture["clusters"]:
            syms = ", ".join(cl["top_symbols"][:3])
            more = f" +{cl['size'] - 3}" if cl["size"] > 3 else ""
            click.echo(f"    {cl['label']:<30s}  {cl['size']:>3d} syms  [{syms}{more}]")
    click.echo()

    # --- Entry Points ---
    if entry_points:
        click.echo(f"ENTRY POINTS (start here)")
        for i, ep in enumerate(entry_points, 1):
            click.echo(f"  {i:>2d}. {abbrev_kind(ep['kind'])} {ep['name']:<40s}  "
                        f"at {ep['location']}  (PageRank {ep['pagerank']:.3f})")
        click.echo()

    # --- Critical Paths ---
    if critical_paths:
        click.echo("CRITICAL PATHS (must understand)")
        for cp in critical_paths:
            must = " *" if cp["must_understand"] else ""
            click.echo(f"  {cp['path']:<55s}  {cp['reason']}{must}")
        click.echo()

    # --- Risk Areas ---
    if risks:
        click.echo("RISK AREAS")
        for r in risks:
            click.echo(f"  [{r['severity']:<8s}] {r['path']:<50s}  {r['type']}: {r['detail']}")
        click.echo()

    # --- Suggested Reading Order ---
    if reading_order:
        click.echo("SUGGESTED READING ORDER")
        for ro in reading_order:
            click.echo(f"  {ro['priority']:>2d}. {ro['path']:<55s}  ({ro['reason']})")
        click.echo()

    # --- Conventions ---
    if conventions["naming"]:
        click.echo("CONVENTIONS")
        for kind, info in conventions["naming"].items():
            click.echo(f"  {kind + ':':<12s} {info['style']} ({info['pct']}%)")
        if conventions["test_pattern"]:
            click.echo(f"  {'test files:':<12s} {conventions['test_pattern']} ({conventions['test_file_count']} files)")
        click.echo()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--detail", type=click.Choice(["brief", "normal", "full"]),
              default="normal", help="Level of detail: brief, normal, or full")
@click.pass_context
def onboard(ctx, detail):
    """Generate a new-developer onboarding guide for the codebase.

    Combines architecture overview, key entry points, critical paths,
    risk areas, bus factor, and a suggested file reading order into a
    single comprehensive guide.  Computed from the index, always current.

    Use --detail to control verbosity:

        roam onboard                # normal (default)
        roam onboard --detail brief # quick overview
        roam onboard --detail full  # everything
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    limits = _LIMITS[detail]

    with open_db(readonly=True) as conn:
        overview = _project_overview(conn)
        architecture = _architecture_overview(conn, limits["clusters"])
        entry_pts = _entry_points(conn, limits["entry_points"])
        critical = _critical_paths(conn, limits["critical"])
        risks = _risk_areas(conn, limits["risk"])
        reading = _suggested_reading_order(
            conn, entry_pts, critical, architecture, limits["reading"],
        )
        conventions = _key_conventions(conn)

        # Build verdict
        risk_count = len(risks)
        if risk_count == 0:
            verdict = "clean codebase, low onboarding friction"
        elif risk_count <= 3:
            verdict = "moderate onboarding complexity, a few risk areas"
        else:
            verdict = f"complex codebase, {risk_count} risk areas to review"

        if json_mode:
            click.echo(to_json(json_envelope("onboard",
                summary={
                    "verdict": verdict,
                    "files": overview["total_files"],
                    "symbols": overview["total_symbols"],
                    "languages": len(overview["languages"]),
                    "layers": architecture["layer_count"],
                    "modules": architecture["cluster_count"],
                    "entry_points": len(entry_pts),
                    "risk_areas": risk_count,
                    "detail": detail,
                },
                overview=overview,
                architecture=architecture,
                entry_points=entry_pts,
                critical_paths=critical,
                risk_areas=risks,
                reading_order=reading,
                conventions=conventions,
            )))
            return

        _emit_text(
            detail, overview, architecture, entry_pts,
            critical, risks, reading, conventions,
        )
