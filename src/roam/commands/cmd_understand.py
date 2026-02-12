"""Single-call codebase comprehension — everything an AI agent needs in one shot."""

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import is_test_file


# ---------------------------------------------------------------------------
# Framework / build-tool detection
# ---------------------------------------------------------------------------

_FRAMEWORK_PATTERNS = {
    # JS/TS frameworks
    "vue": (["vue", "@vue"], ["*.vue"]),
    "react": (["react", "react-dom", "@react"], []),
    "angular": (["@angular/core", "@angular"], []),
    "svelte": (["svelte", "@sveltejs"], ["*.svelte"]),
    "next.js": (["next"], ["next.config.*"]),
    "nuxt": (["nuxt", "@nuxt"], ["nuxt.config.*"]),
    # State management
    "pinia": (["pinia"], []),
    "vuex": (["vuex"], []),
    "redux": (["redux", "@reduxjs/toolkit"], []),
    # CSS
    "tailwind": (["tailwindcss"], ["tailwind.config.*"]),
    # Python
    "django": (["django"], []),
    "flask": (["flask"], []),
    "fastapi": (["fastapi"], []),
    # Go
    "gin": (["github.com/gin-gonic/gin"], []),
    "fiber": (["github.com/gofiber/fiber"], []),
    # Rust
    "actix": (["actix-web"], []),
    "axum": (["axum"], []),
}

_BUILD_PATTERNS = {
    "vite": ["vite.config.*"],
    "webpack": ["webpack.config.*"],
    "rollup": ["rollup.config.*"],
    "esbuild": ["esbuild.*"],
    "turbopack": ["turbo.json"],
    "cargo": ["Cargo.toml"],
    "go": ["go.mod"],
    "maven": ["pom.xml"],
    "gradle": ["build.gradle*"],
    "pip": ["pyproject.toml", "setup.py", "setup.cfg"],
    "composer": ["composer.json"],
}


def _detect_frameworks(conn):
    """Detect frameworks by scanning edge targets and file names."""
    # Collect all unique edge target names (imports/references)
    import_targets = set()
    for r in conn.execute(
        "SELECT DISTINCT s.name FROM symbols s "
        "JOIN edges e ON e.target_id = s.id"
    ).fetchall():
        import_targets.add(r["name"].lower())

    # Also collect file paths for pattern matching
    file_paths = set()
    for r in conn.execute("SELECT path FROM files").fetchall():
        file_paths.add(r["path"].replace("\\", "/").lower())

    detected = []
    for name, (import_pats, file_pats) in _FRAMEWORK_PATTERNS.items():
        found = False
        for pat in import_pats:
            if any(pat.lower() in t for t in import_targets):
                found = True
                break
        if not found:
            for pat in file_pats:
                import fnmatch
                if any(fnmatch.fnmatch(fp.split("/")[-1], pat.lower()) for fp in file_paths):
                    found = True
                    break
        if found:
            detected.append(name)

    return detected


def _detect_build(conn):
    """Detect build tool from file names."""
    file_names = set()
    for r in conn.execute("SELECT path FROM files").fetchall():
        name = r["path"].replace("\\", "/").split("/")[-1].lower()
        file_names.add(name)

    for tool, patterns in _BUILD_PATTERNS.items():
        for pat in patterns:
            import fnmatch
            if any(fnmatch.fnmatch(fn, pat.lower()) for fn in file_names):
                return tool
    return None


# ---------------------------------------------------------------------------
# Key abstractions: top symbols by PageRank + fan analysis
# ---------------------------------------------------------------------------

def _key_abstractions(conn, limit=15):
    """Find the most important symbols by PageRank with fan analysis."""
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

        # Why is this important?
        if fan_in > 20:
            why = f"highly imported ({fan_in} dependents)"
        elif fan_in > 10:
            why = f"widely used ({fan_in} dependents)"
        elif r["kind"] == "class":
            why = "core class"
        else:
            why = "high PageRank"

        results.append({
            "name": r["qualified_name"] or r["name"],
            "kind": r["kind"],
            "location": loc(r["file_path"], r["line_start"]),
            "pagerank": round(r["pagerank"] or 0, 4),
            "fan_in": fan_in,
            "fan_out": fan_out,
            "why": why,
        })

    return results


# ---------------------------------------------------------------------------
# Entry points: files with no importers + high PageRank
# ---------------------------------------------------------------------------

def _find_entry_points(conn, limit=10):
    """Find likely entry point files (no importers + have symbols)."""
    rows = conn.execute(
        "SELECT f.id, f.path, f.language, COUNT(s.id) as sym_count "
        "FROM files f "
        "JOIN symbols s ON s.file_id = f.id "
        "WHERE f.id NOT IN (SELECT DISTINCT target_file_id FROM file_edges) "
        "GROUP BY f.id "
        "HAVING sym_count > 0 "
        "ORDER BY sym_count DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()

    return [{"path": r["path"], "symbols": r["sym_count"]} for r in rows]


# ---------------------------------------------------------------------------
# Hotspots: churn * coupling
# ---------------------------------------------------------------------------

def _find_hotspots(conn, limit=10):
    """Find files with highest churn, annotated with coupling info."""
    rows = conn.execute(
        "SELECT fs.file_id, f.path, fs.total_churn, fs.commit_count, "
        "fs.distinct_authors "
        "FROM file_stats fs "
        "JOIN files f ON fs.file_id = f.id "
        "WHERE fs.total_churn > 0 "
        "ORDER BY fs.total_churn DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()

    results = []
    for r in rows:
        if is_test_file(r["path"]):
            continue
        # Count coupling partners
        partners = conn.execute(
            "SELECT COUNT(*) FROM git_cochange "
            "WHERE file_id_a = ? OR file_id_b = ?",
            (r["file_id"], r["file_id"]),
        ).fetchone()[0]

        results.append({
            "path": r["path"],
            "churn": r["total_churn"],
            "commits": r["commit_count"],
            "authors": r["distinct_authors"],
            "coupling_partners": partners,
        })

    return results[:limit]


# ---------------------------------------------------------------------------
# Suggested reading order for AI agents
# ---------------------------------------------------------------------------

def _suggest_reading_order(conn, entry_points, key_abstractions, hotspots):
    """Build a prioritized reading order for an AI agent exploring the codebase."""
    order = []
    seen = set()
    priority = 1

    # 1. Entry points first
    for ep in entry_points[:3]:
        if ep["path"] not in seen:
            seen.add(ep["path"])
            order.append({
                "path": ep["path"],
                "reason": "entry point",
                "priority": priority,
            })
            priority += 1

    # 2. Files with key abstractions
    for ka in key_abstractions[:5]:
        path = ka["location"].rsplit(":", 1)[0]
        if path not in seen:
            seen.add(path)
            order.append({
                "path": path,
                "reason": f"key abstraction ({ka['name']})",
                "priority": priority,
            })
            priority += 1

    # 3. Hotspots
    for hs in hotspots[:3]:
        if hs["path"] not in seen:
            seen.add(hs["path"])
            order.append({
                "path": hs["path"],
                "reason": "active hotspot",
                "priority": priority,
            })
            priority += 1

    return order


# ---------------------------------------------------------------------------
# Conventions summary (lightweight inline detection)
# ---------------------------------------------------------------------------

def _detect_conventions(conn):
    """Detect dominant naming conventions per symbol kind."""
    import re
    _SNAKE = re.compile(r'^[a-z_][a-z0-9_]*$')
    _CAMEL = re.compile(r'^[a-z][a-zA-Z0-9]*$')
    _PASCAL = re.compile(r'^[A-Z][a-zA-Z0-9]*$')
    _UPPER = re.compile(r'^[A-Z_][A-Z0-9_]*$')

    result = {}
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
            result[kind] = {"style": dominant, "pct": pct, "total": total}

    return result


# ---------------------------------------------------------------------------
# Complexity overview
# ---------------------------------------------------------------------------

def _complexity_overview(conn):
    """Get aggregate complexity stats from symbol_metrics."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "AVG(cognitive_complexity) as avg_cc, "
            "MAX(cognitive_complexity) as max_cc "
            "FROM symbol_metrics"
        ).fetchone()
        if not row or row["total"] == 0:
            return None

        critical = conn.execute(
            "SELECT COUNT(*) FROM symbol_metrics WHERE cognitive_complexity >= 25"
        ).fetchone()[0]
        high = conn.execute(
            "SELECT COUNT(*) FROM symbol_metrics WHERE cognitive_complexity >= 15 AND cognitive_complexity < 25"
        ).fetchone()[0]

        # Top 3 worst
        worst = conn.execute(
            "SELECT s.name, sm.cognitive_complexity, f.path "
            "FROM symbol_metrics sm "
            "JOIN symbols s ON sm.symbol_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            "ORDER BY sm.cognitive_complexity DESC LIMIT 3"
        ).fetchall()

        return {
            "total_analyzed": row["total"],
            "avg": round(row["avg_cc"] or 0, 1),
            "max": round(row["max_cc"] or 0, 0),
            "critical": critical,
            "high": high,
            "worst": [
                {"name": w["name"], "cc": round(w["cognitive_complexity"]), "file": w["path"]}
                for w in worst
            ],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pattern summary
# ---------------------------------------------------------------------------

def _detect_patterns_summary(conn):
    """Quick lightweight pattern detection (strategy, factory)."""
    patterns = []

    # Strategy: classes sharing a parent
    try:
        rows = conn.execute(
            "SELECT p.name as parent, COUNT(*) as impl_count "
            "FROM symbols s "
            "JOIN edges e ON e.source_id = s.id "
            "JOIN symbols p ON e.target_id = p.id "
            "WHERE e.kind = 'inherits' AND p.kind IN ('class', 'interface') "
            "GROUP BY p.name "
            "HAVING COUNT(*) >= 3 "
            "ORDER BY COUNT(*) DESC LIMIT 5"
        ).fetchall()
        for r in rows:
            patterns.append({
                "type": "strategy/hierarchy",
                "name": r["parent"],
                "count": r["impl_count"],
            })
    except Exception:
        pass

    # Factory: functions named create_*/build_*/make_*
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM symbols "
            "WHERE kind = 'function' AND "
            "(name LIKE 'create_%' OR name LIKE 'build_%' OR name LIKE 'make_%' OR name LIKE '%Factory%')"
        ).fetchone()[0]
        if count > 0:
            patterns.append({"type": "factory", "name": "factory functions", "count": count})
    except Exception:
        pass

    return patterns


# ---------------------------------------------------------------------------
# Debt hotspots
# ---------------------------------------------------------------------------

def _top_debt(conn, limit=5):
    """Compute top debt files (simplified hotspot-weighted)."""
    try:
        rows = conn.execute(
            "SELECT f.path, fs.complexity, fs.total_churn "
            "FROM file_stats fs "
            "JOIN files f ON fs.file_id = f.id "
            "WHERE fs.total_churn > 0 AND fs.complexity > 0 "
            "ORDER BY fs.complexity * fs.total_churn DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "path": r["path"],
                "complexity": round(r["complexity"] or 0, 1),
                "churn": r["total_churn"] or 0,
                "debt_score": round((r["complexity"] or 0) * (r["total_churn"] or 0) / 100, 1),
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--full", is_flag=True, help="Show all clusters and hotspots, not just top-N")
@click.pass_context
def understand(ctx, full):
    """Single-call codebase comprehension — everything in one shot.

    Returns project structure, tech stack, architecture, health, hotspots,
    and a suggested reading order. Designed for AI agents.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    root = find_project_root()

    with open_db(readonly=True) as conn:
        # --- Basic stats ---
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        # --- Languages ---
        lang_rows = conn.execute(
            "SELECT language, COUNT(*) as cnt FROM files "
            "WHERE language IS NOT NULL "
            "GROUP BY language ORDER BY cnt DESC"
        ).fetchall()
        languages = []
        for r in lang_rows:
            pct = round(r["cnt"] * 100 / file_count, 1) if file_count else 0
            languages.append({
                "name": r["language"],
                "files": r["cnt"],
                "pct": pct,
            })

        # --- Tech stack ---
        frameworks = _detect_frameworks(conn)
        build_tool = _detect_build(conn)

        # --- Architecture ---
        try:
            from roam.graph.builder import build_symbol_graph
            from roam.graph.layers import detect_layers
            G = build_symbol_graph(conn)
            layer_map = detect_layers(G)
            layers = sorted(set(layer_map.values())) if layer_map else []
        except Exception:
            layers = []

        entry_points = _find_entry_points(conn)
        key_abs = _key_abstractions(conn, limit=15 if full else 10)

        # Clusters
        cluster_rows = conn.execute(
            "SELECT cluster_id, cluster_label, COUNT(*) as size "
            "FROM clusters GROUP BY cluster_id ORDER BY size DESC"
        ).fetchall()
        clusters_data = []
        for cr in cluster_rows[:20 if full else 8]:
            top_syms = conn.execute(
                "SELECT s.name, s.kind FROM clusters c "
                "JOIN symbols s ON c.symbol_id = s.id "
                "WHERE c.cluster_id = ? "
                "ORDER BY s.name LIMIT 5",
                (cr["cluster_id"],),
            ).fetchall()
            clusters_data.append({
                "id": cr["cluster_id"],
                "label": cr["cluster_label"] or f"cluster-{cr['cluster_id']}",
                "size": cr["size"],
                "top_symbols": [s["name"] for s in top_syms],
            })

        # --- Health ---
        from roam.commands.metrics_history import collect_metrics
        health = collect_metrics(conn)

        # Worst issues
        worst = []
        if health["cycles"] > 0:
            worst.append(f"{health['cycles']} cycle(s)")
        if health["god_components"] > 0:
            worst.append(f"{health['god_components']} god component(s)")
        if health["dead_exports"] > 20:
            worst.append(f"{health['dead_exports']} dead exports")

        # --- Hotspots ---
        hotspots = _find_hotspots(conn, limit=20 if full else 10)

        # --- Conventions ---
        conventions_summary = _detect_conventions(conn)

        # --- Complexity overview ---
        complexity_summary = _complexity_overview(conn)

        # --- Patterns ---
        patterns_detected = _detect_patterns_summary(conn)

        # --- Debt hotspots ---
        debt_hotspots = _top_debt(conn, limit=5)

        # --- Reading order ---
        reading_order = _suggest_reading_order(conn, entry_points, key_abs, hotspots)

        # --- JSON output ---
        if json_mode:
            click.echo(to_json(json_envelope("understand",
                summary={
                    "files": file_count,
                    "symbols": sym_count,
                    "health_score": health["health_score"],
                    "languages": len(languages),
                },
                project={
                    "name": root.name,
                    "root": str(root),
                    "files": file_count,
                    "symbols": sym_count,
                    "edges": edge_count,
                },
                tech_stack={
                    "languages": languages,
                    "frameworks": frameworks,
                    "build": build_tool,
                },
                architecture={
                    "layers": layers,
                    "layer_count": len(layers),
                    "entry_points": entry_points,
                    "key_abstractions": key_abs,
                    "clusters": clusters_data,
                },
                health_summary={
                    "score": health["health_score"],
                    "cycles": health["cycles"],
                    "god_components": health["god_components"],
                    "bottlenecks": health["bottlenecks"],
                    "dead_exports": health["dead_exports"],
                    "layer_violations": health["layer_violations"],
                    "worst_issues": worst,
                },
                conventions=conventions_summary,
                complexity=complexity_summary,
                patterns=patterns_detected,
                debt_hotspots=debt_hotspots,
                hotspots=hotspots,
                suggested_reading_order=reading_order,
            )))
            return

        # --- Compact text output ---
        # Language summary
        lang_str = ", ".join(f"{l['name']} ({l['files']})" for l in languages[:5])
        if len(languages) > 5:
            lang_str += f" +{len(languages) - 5} more"

        fw_str = ", ".join(frameworks) if frameworks else "none detected"
        build_str = build_tool or "unknown"

        click.echo(f"=== {root.name} ===\n")
        click.echo(f"Project: {file_count} files, {sym_count} symbols, {edge_count} edges")
        click.echo(f"Languages: {lang_str}")
        click.echo(f"Stack: {fw_str} | Build: {build_str}")
        click.echo(f"Architecture: {len(layers)} layers, {len(clusters_data)} clusters")
        click.echo(f"Health: {health['health_score']}/100"
                    f" — {', '.join(worst) if worst else 'no critical issues'}")
        click.echo()

        # Key abstractions
        click.echo(f"Key abstractions ({len(key_abs)}):")
        for ka in key_abs[:10]:
            click.echo(f"  {abbrev_kind(ka['kind'])}  {ka['name']:<40s}  "
                        f"fan_in={ka['fan_in']:<3d}  {ka['location']}")
        if len(key_abs) > 10:
            click.echo(f"  (+{len(key_abs) - 10} more)")
        click.echo()

        # Entry points
        if entry_points:
            click.echo(f"Entry points ({len(entry_points)}):")
            for ep in entry_points[:5]:
                click.echo(f"  {ep['path']:<50s}  ({ep['symbols']} syms)")
            click.echo()

        # Clusters
        if clusters_data:
            click.echo(f"Clusters ({len(clusters_data)}):")
            for cl in clusters_data[:8]:
                syms = ", ".join(cl["top_symbols"][:4])
                more = f" +{cl['size'] - 4}" if cl["size"] > 4 else ""
                click.echo(f"  {cl['label']:<30s}  {cl['size']:>3d} syms  [{syms}{more}]")
            if len(clusters_data) > 8:
                click.echo(f"  (+{len(clusters_data) - 8} more)")
            click.echo()

        # Hotspots
        if hotspots:
            click.echo(f"Hotspots ({len(hotspots)}):")
            for hs in hotspots[:5]:
                click.echo(f"  {hs['path']:<50s}  churn={hs['churn']:<5d}  "
                            f"authors={hs['authors']}  coupling={hs['coupling_partners']}")
            click.echo()

        # Conventions
        if conventions_summary:
            parts = []
            for kind, info in conventions_summary.items():
                parts.append(f"{kind}: {info['style']} ({info['pct']:.0f}%)")
            click.echo(f"Conventions: {', '.join(parts)}")
            click.echo()

        # Complexity
        if complexity_summary:
            click.echo(
                f"Complexity: {complexity_summary['total_analyzed']} functions, "
                f"avg={complexity_summary['avg']}, "
                f"{complexity_summary['critical']} critical, "
                f"{complexity_summary['high']} high"
            )
            if complexity_summary["worst"]:
                worst_names = ", ".join(
                    f"{w['name']}({w['cc']})" for w in complexity_summary["worst"][:3]
                )
                click.echo(f"  Worst: {worst_names}")
            click.echo()

        # Patterns
        if patterns_detected:
            pat_str = ", ".join(
                f"{p['type']}: {p['name']} ({p['count']})" for p in patterns_detected
            )
            click.echo(f"Patterns: {pat_str}")
            click.echo()

        # Debt
        if debt_hotspots:
            click.echo(f"Debt hotspots:")
            for d in debt_hotspots:
                click.echo(
                    f"  {d['path']:<50s}  "
                    f"complexity={d['complexity']:<6}  churn={d['churn']}"
                )
            click.echo()

        # Reading order
        click.echo(f"Suggested reading order:")
        for ro in reading_order:
            click.echo(f"  {ro['priority']:>2d}. {ro['path']:<50s}  ({ro['reason']})")
