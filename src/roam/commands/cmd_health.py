"""Detect and report code health issues."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.db.queries import TOP_BY_DEGREE, TOP_BY_BETWEENNESS
from roam.graph.builder import build_symbol_graph
from roam.graph.cycles import find_cycles, find_weakest_edge, format_cycles
from roam.graph.layers import detect_layers, find_violations
from roam.output.formatter import (
    abbrev_kind, loc, section, format_table, truncate_lines, to_json,
    json_envelope,
)
from roam.commands.resolve import ensure_index


_FRAMEWORK_NAMES = frozenset({
    # Python dunders
    "__init__", "__str__", "__repr__", "__new__", "__del__", "__enter__",
    "__exit__", "__getattr__", "__setattr__", "__getitem__", "__setitem__",
    "__len__", "__iter__", "__next__", "__call__", "__hash__", "__eq__",
    # JS/TS generic
    "constructor", "render", "toString", "valueOf", "toJSON",
    "setUp", "tearDown", "setup", "teardown",
    "configure", "register", "bootstrap", "main",
    # Vue
    "computed", "ref", "reactive", "watch", "watchEffect",
    "defineProps", "defineEmits", "defineExpose", "defineSlots",
    "onMounted", "onUnmounted", "onBeforeMount", "onBeforeUnmount",
    "onActivated", "onDeactivated", "onUpdated", "onBeforeUpdate",
    "provide", "inject", "toRef", "toRefs", "unref", "isRef",
    "shallowRef", "shallowReactive", "readonly", "shallowReadonly",
    "nextTick", "h", "resolveComponent", "emit", "emits", "props",
    # React
    "useState", "useEffect", "useCallback", "useMemo", "useRef",
    "useContext", "useReducer", "useLayoutEffect",
    # Angular
    "ngOnInit", "ngOnDestroy", "ngOnChanges", "ngAfterViewInit",
    # Go
    "init", "New", "Close", "String", "Error",
    # Rust
    "new", "default", "fmt", "from", "into", "drop",
})


# ---- Location-aware utility detection ----

_UTILITY_PATH_PATTERNS = (
    "composables/", "utils/", "services/", "lib/", "helpers/",
    "shared/", "config/", "core/", "hooks/", "stores/",
)


def _is_utility_path(file_path):
    """Check if a file is in a utility/infrastructure directory."""
    p = file_path.replace("\\", "/").lower()
    return any(pat in p for pat in _UTILITY_PATH_PATTERNS)


def _percentile(sorted_values, pct):
    """Return the value at the given percentile (0-100) from a sorted list."""
    if not sorted_values:
        return 0
    idx = int(pct / 100 * len(sorted_values))
    return sorted_values[min(idx, len(sorted_values) - 1)]


def _unique_dirs(file_paths):
    """Extract unique parent directory names from a list of file paths."""
    dirs = set()
    for fp in file_paths:
        p = fp.replace("\\", "/")
        last_slash = p.rfind("/")
        if last_slash >= 0:
            dirs.add(p[:last_slash])
        else:
            dirs.add(".")
    return dirs


@click.command()
@click.option('--no-framework', is_flag=True,
              help='Filter out framework/boilerplate symbols from god components and bottlenecks')
@click.pass_context
def health(ctx, no_framework):
    """Show code health: cycles, god components, bottlenecks."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    with open_db(readonly=True) as conn:
        G = build_symbol_graph(conn)

        # --- Cycles ---
        cycles = find_cycles(G)
        formatted_cycles = format_cycles(cycles, conn) if cycles else []

        # --- Cycle break suggestions ---
        break_suggestions: list[dict] = []
        for scc in cycles:
            if len(scc) < 3:
                continue
            result = find_weakest_edge(G, scc)
            if result is None:
                continue
            src_id, tgt_id, reason = result
            src_name = G.nodes[src_id].get("name", "?") if src_id in G else "?"
            tgt_name = G.nodes[tgt_id].get("name", "?") if tgt_id in G else "?"
            break_suggestions.append({
                "source_id": src_id,
                "target_id": tgt_id,
                "source_name": src_name,
                "target_name": tgt_name,
                "reason": reason,
                "scc_size": len(scc),
            })

        # --- God components ---
        degree_rows = conn.execute(TOP_BY_DEGREE, (50,)).fetchall()
        god_items = []
        for r in degree_rows:
            total = (r["in_degree"] or 0) + (r["out_degree"] or 0)
            if total > 20:
                god_items.append({
                    "name": r["name"], "kind": r["kind"],
                    "degree": total, "file": r["file_path"],
                })

        # --- Bottlenecks (percentile-based severity) ---
        # Fetch all non-zero betweenness values to compute percentile thresholds.
        # Raw betweenness is unnormalized (shortest-path counts), so absolute
        # thresholds don't scale across codebase sizes. Percentiles do.
        all_bw = sorted(
            r[0] for r in conn.execute(
                "SELECT betweenness FROM graph_metrics WHERE betweenness > 0"
            ).fetchall()
        )
        bn_p70 = _percentile(all_bw, 70)
        bn_p90 = _percentile(all_bw, 90)

        bw_rows = conn.execute(TOP_BY_BETWEENNESS, (15,)).fetchall()
        bn_items = []
        for r in bw_rows:
            bw = r["betweenness"] or 0
            if bw > 0.5:
                bn_items.append({
                    "name": r["name"], "kind": r["kind"],
                    "betweenness": round(bw, 1), "file": r["file_path"],
                })

        # --- Framework filtering ---
        filtered_count = 0
        if no_framework:
            before = len(god_items) + len(bn_items)
            god_items = [g for g in god_items if g["name"] not in _FRAMEWORK_NAMES]
            bn_items = [b for b in bn_items if b["name"] not in _FRAMEWORK_NAMES]
            filtered_count = before - len(god_items) - len(bn_items)

        # --- Layer violations ---
        layer_map = detect_layers(G)
        violations = find_violations(G, layer_map) if layer_map else []
        v_lookup = {}
        if violations:
            all_ids = {v["source"] for v in violations} | {v["target"] for v in violations}
            ph = ",".join("?" for _ in all_ids)
            for r in conn.execute(
                f"SELECT s.id, s.name, f.path as file_path "
                f"FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
                list(all_ids),
            ).fetchall():
                v_lookup[r["id"]] = r

        # ---- Classify issue severity (location-aware) ----
        sev_counts = {"CRITICAL": 0, "WARNING": 0, "INFO": 0}

        # Cycle severity: directory-aware
        for cyc in formatted_cycles:
            dirs = _unique_dirs(cyc["files"])
            cyc["directories"] = len(dirs)
            if len(dirs) <= 1:
                # All symbols in same directory — cohesive internal pattern
                cyc["severity"] = "INFO"
            elif len(cyc["files"]) > 3:
                cyc["severity"] = "CRITICAL"
            else:
                cyc["severity"] = "WARNING"
            sev_counts[cyc["severity"]] += 1

        # God component severity: location-aware thresholds
        actionable_count = 0
        utility_count = 0
        for g in god_items:
            is_util = _is_utility_path(g["file"])
            g["category"] = "utility" if is_util else "actionable"
            if is_util:
                utility_count += 1
                # Relaxed thresholds for utilities (3x)
                if g["degree"] > 150:
                    g["severity"] = "CRITICAL"
                elif g["degree"] > 90:
                    g["severity"] = "WARNING"
                else:
                    g["severity"] = "INFO"
            else:
                actionable_count += 1
                # Standard thresholds for non-utility code
                if g["degree"] > 50:
                    g["severity"] = "CRITICAL"
                elif g["degree"] > 30:
                    g["severity"] = "WARNING"
                else:
                    g["severity"] = "INFO"
            sev_counts[g["severity"]] += 1

        # Sort: actionable first, then utilities; within each group by degree desc
        god_items.sort(key=lambda g: (
            0 if g["category"] == "actionable" else 1,
            -g["degree"],
        ))

        # Bottleneck severity: percentile-based thresholds.
        # Utilities get 1.5x multiplied thresholds (higher bar for severity).
        _BN_UTIL_MULT = 1.5
        bn_actionable = 0
        bn_utility = 0
        for b in bn_items:
            is_util = _is_utility_path(b["file"])
            b["category"] = "utility" if is_util else "actionable"
            mult = _BN_UTIL_MULT if is_util else 1.0
            if is_util:
                bn_utility += 1
            else:
                bn_actionable += 1
            if b["betweenness"] > bn_p90 * mult:
                b["severity"] = "CRITICAL"
            elif b["betweenness"] > bn_p70 * mult:
                b["severity"] = "WARNING"
            else:
                b["severity"] = "INFO"
            sev_counts[b["severity"]] += 1

        # Sort: actionable first, then utilities; within each group by betweenness desc
        bn_items.sort(key=lambda b: (
            0 if b["category"] == "actionable" else 1,
            -b["betweenness"],
        ))

        for v in violations:
            v["severity"] = "WARNING"
            sev_counts["WARNING"] += 1

        # --- Tangle ratio ---
        total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] or 1
        cycle_symbol_ids = set()
        for scc in cycles:
            cycle_symbol_ids.update(scc)
        tangle_ratio = round(len(cycle_symbol_ids) / total_symbols * 100, 1)

        # --- Composite health score (0-100) ---
        health_score = 100
        health_score -= min(30, tangle_ratio * 1.5)
        god_critical = sum(1 for g in god_items if g.get("severity") == "CRITICAL")
        health_score -= min(20, god_critical * 5 + len(god_items) * 1)
        bn_critical = sum(1 for b in bn_items if b.get("severity") == "CRITICAL")
        health_score -= min(15, bn_critical * 3 + len(bn_items) * 0.5)
        health_score -= min(15, len(violations) * 2)
        try:
            avg_file_health = conn.execute(
                "SELECT AVG(health_score) FROM file_stats WHERE health_score IS NOT NULL"
            ).fetchone()[0]
            if avg_file_health is not None:
                health_score -= max(0, (10 - avg_file_health) * 2)
        except Exception:
            pass
        health_score = max(0, min(100, int(health_score)))

        if json_mode:
            j_issue_count = len(cycles) + len(god_items) + len(bn_items) + len(violations)
            click.echo(to_json(json_envelope("health",
                summary={
                    "health_score": health_score,
                    "tangle_ratio": tangle_ratio,
                    "issue_count": j_issue_count,
                    "severity": sev_counts,
                },
                health_score=health_score,
                tangle_ratio=tangle_ratio,
                issue_count=j_issue_count,
                severity=sev_counts,
                framework_filtered=filtered_count,
                actionable_count=actionable_count,
                utility_count=utility_count,
                cycles=[
                    {"size": c["size"], "severity": c["severity"],
                     "directories": c["directories"],
                     "symbols": [s["name"] for s in c["symbols"]],
                     "files": c["files"]}
                    for c in formatted_cycles
                ],
                cycle_break_suggestions=[
                    {
                        "source": bs["source_name"],
                        "target": bs["target_name"],
                        "reason": bs["reason"],
                        "scc_size": bs["scc_size"],
                    }
                    for bs in break_suggestions
                ],
                god_components=[
                    {**g, "severity": g["severity"], "category": g["category"]}
                    for g in god_items
                ],
                bottleneck_thresholds={
                    "p70": round(bn_p70, 1),
                    "p90": round(bn_p90, 1),
                    "utility_multiplier": _BN_UTIL_MULT,
                    "population": len(all_bw),
                },
                bottlenecks=[
                    {**b, "severity": b["severity"], "category": b["category"]}
                    for b in bn_items
                ],
                layer_violations=[
                    {
                        "severity": "WARNING",
                        "source": v_lookup.get(v["source"], {}).get("name", "?"),
                        "source_layer": v["source_layer"],
                        "target": v_lookup.get(v["target"], {}).get("name", "?"),
                        "target_layer": v["target_layer"],
                    }
                    for v in violations
                ],
            )))
            return

        # --- Text output ---
        issue_count = len(cycles) + len(god_items) + len(bn_items) + len(violations)
        parts = []
        if cycles:
            parts.append(f"{len(cycles)} cycle{'s' if len(cycles) != 1 else ''}")
        if god_items:
            god_detail = f"{len(god_items)} god component{'s' if len(god_items) != 1 else ''}"
            god_detail += f" ({actionable_count} actionable, {utility_count} expected utilities)"
            parts.append(god_detail)
        if bn_items:
            bn_detail = f"{len(bn_items)} bottleneck{'s' if len(bn_items) != 1 else ''}"
            bn_detail += f" ({bn_actionable} actionable, {bn_utility} expected utilities)"
            parts.append(bn_detail)
        if violations:
            parts.append(f"{len(violations)} layer violation{'s' if len(violations) != 1 else ''}")
        click.echo(f"Health Score: {health_score}/100  |  "
                   f"Tangle: {tangle_ratio}% ({len(cycle_symbol_ids)}/{total_symbols} symbols in cycles)")
        click.echo()
        if issue_count == 0:
            click.echo("Issues: None detected")
        else:
            sev_parts = []
            if sev_counts["CRITICAL"]:
                sev_parts.append(f"{sev_counts['CRITICAL']} CRITICAL")
            if sev_counts["WARNING"]:
                sev_parts.append(f"{sev_counts['WARNING']} WARNING")
            if sev_counts["INFO"]:
                sev_parts.append(f"{sev_counts['INFO']} INFO")
            click.echo(f"Health: {issue_count} issue{'s' if issue_count != 1 else ''} "
                        f"— {', '.join(sev_parts)}")
            detail = ', '.join(parts)
            if filtered_count:
                detail += f"; {filtered_count} framework symbols filtered"
            click.echo(f"  ({detail})")
        click.echo()

        click.echo("=== Cycles ===")
        if formatted_cycles:
            for i, cyc in enumerate(formatted_cycles, 1):
                names = [s["name"] for s in cyc["symbols"]]
                sev = cyc["severity"]
                dir_note = f", {cyc['directories']} dir{'s' if cyc['directories'] != 1 else ''}"
                click.echo(f"  [{sev}] cycle {i} ({cyc['size']} symbols{dir_note}): {', '.join(names[:10])}")
                if len(names) > 10:
                    click.echo(f"    (+{len(names) - 10} more)")
                click.echo(f"    files: {', '.join(cyc['files'][:5])}")
            click.echo(f"  total: {len(cycles)} cycle(s)")
            if break_suggestions:
                click.echo()
                click.echo("  Cycle break suggestions:")
                for bs in break_suggestions:
                    click.echo(
                        f"    Break: remove dependency "
                        f"{bs['source_name']} -> {bs['target_name']} "
                        f"({bs['reason']})"
                    )
        else:
            click.echo("  (none)")

        click.echo("\n=== God Components (degree > 20) ===")
        if god_items:
            god_rows = [[g["severity"], g["name"], abbrev_kind(g["kind"]),
                         str(g["degree"]),
                         "util" if g["category"] == "utility" else "act",
                         loc(g["file"])]
                        for g in god_items]
            click.echo(format_table(["Sev", "Name", "Kind", "Degree", "Cat", "File"],
                                    god_rows, budget=20))
        else:
            click.echo("  (none)")

        click.echo("\n=== Bottlenecks (high betweenness) ===")
        if bn_items:
            bn_rows = []
            for b in bn_items:
                bw_str = f"{b['betweenness']:.0f}" if b["betweenness"] >= 10 else f"{b['betweenness']:.1f}"
                bn_rows.append([b["severity"], b["name"], abbrev_kind(b["kind"]),
                                bw_str,
                                "util" if b["category"] == "utility" else "act",
                                loc(b["file"])])
            click.echo(format_table(["Sev", "Name", "Kind", "Betweenness", "Cat", "File"],
                                    bn_rows, budget=15))
        else:
            click.echo("  (none)")

        click.echo(f"\n=== Layer Violations ({len(violations)}) ===")
        if violations:
            v_rows = []
            for v in violations[:20]:
                src = v_lookup.get(v["source"], {})
                tgt = v_lookup.get(v["target"], {})
                v_rows.append([
                    src.get("name", "?"), f"L{v['source_layer']}",
                    tgt.get("name", "?"), f"L{v['target_layer']}",
                ])
            click.echo(format_table(["Source", "Layer", "Target", "Layer"], v_rows, budget=20))
            if len(violations) > 20:
                click.echo(f"  (+{len(violations) - 20} more)")
        elif layer_map:
            click.echo("  (none)")
        else:
            click.echo("  (no layers detected)")
