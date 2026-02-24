"""Sub-agent preflight bundle for a symbol.

`roam guard` is a compact, CLI-first context packet for sub-agents that
cannot call MCP tools directly. It intentionally focuses on the minimum set
needed before editing:
- definition/signature
- 1-hop callers/callees
- covering test files
- breaking-change risk score
- layer-violation signals
"""

from __future__ import annotations

import click

from roam.db.connection import open_db, batched_in
from roam.output.formatter import abbrev_kind, budget_truncate, json_envelope, loc, to_json
from roam.commands.context_helpers import (
    gather_symbol_context,
    get_affected_tests_bfs,
    get_blast_radius,
    get_graph_metrics,
    get_symbol_metrics,
)
from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found


_DEFAULT_CALLER_CAP = 8
_DEFAULT_CALLEE_CAP = 8
_DEFAULT_TEST_CAP = 8
_DEFAULT_LAYER_CAP = 6

_DETAIL_CALLER_CAP = 15
_DETAIL_CALLEE_CAP = 15
_DETAIL_TEST_CAP = 20
_DETAIL_LAYER_CAP = 20


def _risk_level(score: int) -> str:
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "HIGH"
    if score >= 35:
        return "MEDIUM"
    return "LOW"


def _trim_signature(signature: str | None, max_len: int = 140) -> str:
    if not signature:
        return ""
    sig = str(signature).strip()
    if len(sig) <= max_len:
        return sig
    return sig[: max_len - 3] + "..."


def _to_edge_item(row) -> dict:
    return {
        "name": row["name"],
        "kind": row["kind"],
        "location": loc(row["file_path"], row["line_start"]),
        "edge_kind": row["edge_kind"] or "",
    }


def _summarize_tests(test_hits: list[dict], cap: int) -> tuple[list[dict], int, int]:
    """Collapse per-symbol test hits to file-level coverage hints."""
    by_file: dict[str, dict] = {}

    for hit in test_hits:
        path = hit["file"]
        kind = hit["kind"]
        hops = hit["hops"]
        priority = (0 if kind == "DIRECT" else 1, hops)

        existing = by_file.get(path)
        if existing is None or priority < existing["_priority"]:
            by_file[path] = {
                "file": path,
                "kind": kind,
                "hops": hops,
                "via": hit.get("via"),
                "_priority": priority,
            }

    rows = sorted(
        by_file.values(),
        key=lambda r: (0 if r["kind"] == "DIRECT" else 1, r["hops"], r["file"]),
    )
    for r in rows:
        r.pop("_priority", None)

    direct_files = sum(1 for r in rows if r["kind"] == "DIRECT")
    return rows[:cap], direct_files, len(rows)


def _risk_score(
    blast: dict,
    symbol_metrics: dict | None,
    graph_metrics: dict | None,
    direct_test_files: int,
    total_test_files: int,
    layer_violation_count: int,
    move_sensitive_count: int,
) -> tuple[int, str, dict]:
    """Compute a compact 0-100 breaking-change risk score.

    The score is intentionally conservative and front-loads blast radius and
    missing tests, because those are the highest-value pre-edit signals for
    sub-agent workflows.
    """
    dep_syms = int(blast.get("dependent_symbols", 0) or 0)
    dep_files = int(blast.get("dependent_files", 0) or 0)
    blast_component = min(45.0, dep_syms * 1.2 + dep_files * 2.5)

    cc = float((symbol_metrics or {}).get("cognitive_complexity") or 0.0)
    nesting = float((symbol_metrics or {}).get("nesting_depth") or 0.0)
    complexity_component = min(18.0, (cc * 0.7) + (nesting * 1.8))

    in_deg = float((graph_metrics or {}).get("in_degree") or 0.0)
    out_deg = float((graph_metrics or {}).get("out_degree") or 0.0)
    bw = float((graph_metrics or {}).get("betweenness") or 0.0)
    centrality_component = min(12.0, (in_deg * 0.9) + (out_deg * 0.4) + (bw * 120.0))

    if total_test_files <= 0:
        test_component = 12.0
    elif direct_test_files <= 0:
        test_component = 7.0
    elif total_test_files < 2:
        test_component = 4.0
    else:
        test_component = 0.0

    layer_component = min(13.0, layer_violation_count * 5.0 + move_sensitive_count * 1.5)

    score = int(round(
        blast_component
        + complexity_component
        + centrality_component
        + test_component
        + layer_component
    ))
    score = max(0, min(100, score))

    level = _risk_level(score)
    factors = {
        "blast": round(blast_component, 1),
        "complexity": round(complexity_component, 1),
        "centrality": round(centrality_component, 1),
        "test_gap": round(test_component, 1),
        "layers": round(layer_component, 1),
    }
    return score, level, factors


def _layer_analysis(conn, symbol_id: int, cap: int) -> dict:
    """Collect layer violations and move-sensitive edges for a symbol."""
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers, find_violations
    except ImportError:
        return {
            "current_layer": None,
            "violation_count": 0,
            "violations": [],
            "move_sensitive_count": 0,
            "move_sensitive_edges": [],
        }

    try:
        G = build_symbol_graph(conn)
    except Exception:
        return {
            "current_layer": None,
            "violation_count": 0,
            "violations": [],
            "move_sensitive_count": 0,
            "move_sensitive_edges": [],
        }

    if symbol_id not in G:
        return {
            "current_layer": None,
            "violation_count": 0,
            "violations": [],
            "move_sensitive_count": 0,
            "move_sensitive_edges": [],
        }

    try:
        layers = detect_layers(G)
        violations = find_violations(G, layers)
    except Exception:
        return {
            "current_layer": None,
            "violation_count": 0,
            "violations": [],
            "move_sensitive_count": 0,
            "move_sensitive_edges": [],
        }

    current_layer = layers.get(symbol_id)

    # Existing formal violations involving this symbol.
    relevant_violations = [
        v for v in violations
        if v["source"] == symbol_id or v["target"] == symbol_id
    ]
    lookup = {}
    if relevant_violations:
        ids = sorted({
            v["source"] for v in relevant_violations
        } | {
            v["target"] for v in relevant_violations
        })
        for r in batched_in(
            conn,
            "SELECT s.id, s.name, s.kind, s.line_start, f.path as file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.id IN ({ph})",
            ids,
        ):
            lookup[r["id"]] = dict(r)

    formatted_violations = []
    for v in relevant_violations[:cap]:
        src = lookup.get(v["source"], {})
        tgt = lookup.get(v["target"], {})
        formatted_violations.append({
            "source": src.get("name", f"id={v['source']}"),
            "source_kind": src.get("kind", ""),
            "source_layer": v["source_layer"],
            "target": tgt.get("name", f"id={v['target']}"),
            "target_kind": tgt.get("kind", ""),
            "target_layer": v["target_layer"],
            "layer_distance": v["layer_distance"],
            "location": loc(src.get("file_path", ""), src.get("line_start")),
        })

    # Move-sensitive edges: any edge touching this symbol that crosses >1 layer.
    move_edges = []
    seen = set()
    for src, tgt in list(G.out_edges(symbol_id)) + list(G.in_edges(symbol_id)):
        if (src, tgt) in seen:
            continue
        seen.add((src, tgt))

        src_layer = layers.get(src)
        tgt_layer = layers.get(tgt)
        if src_layer is None or tgt_layer is None:
            continue

        distance = abs(src_layer - tgt_layer)
        if distance <= 1:
            continue

        src_node = G.nodes.get(src, {})
        tgt_node = G.nodes.get(tgt, {})
        edge_kind = (G.edges.get((src, tgt), {}) or {}).get("kind", "")

        move_edges.append({
            "direction": "outgoing" if src == symbol_id else "incoming",
            "source": src_node.get("name", f"id={src}"),
            "source_layer": src_layer,
            "target": tgt_node.get("name", f"id={tgt}"),
            "target_layer": tgt_layer,
            "edge_kind": edge_kind,
            "layer_distance": distance,
        })

    move_edges.sort(
        key=lambda e: (-e["layer_distance"], e["direction"], e["source"], e["target"]),
    )

    return {
        "current_layer": current_layer,
        "violation_count": len(relevant_violations),
        "violations": formatted_violations,
        "move_sensitive_count": len(move_edges),
        "move_sensitive_edges": move_edges[:cap],
    }


@click.command("guard")
@click.argument("name")
@click.pass_context
def guard(ctx, name):
    """Sub-agent preflight bundle for a symbol (~2K-token target)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    detail = ctx.obj.get("detail", False) if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    caller_cap = _DETAIL_CALLER_CAP if detail else _DEFAULT_CALLER_CAP
    callee_cap = _DETAIL_CALLEE_CAP if detail else _DEFAULT_CALLEE_CAP
    test_cap = _DETAIL_TEST_CAP if detail else _DEFAULT_TEST_CAP
    layer_cap = _DETAIL_LAYER_CAP if detail else _DEFAULT_LAYER_CAP

    ensure_index()

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, name)
        if sym is None:
            click.echo(symbol_not_found(conn, name, json_mode=json_mode))
            raise SystemExit(1)

        context = gather_symbol_context(conn, sym, task="review", use_propagation=False)
        callers = [_to_edge_item(r) for r in context["non_test_callers"][:caller_cap]]
        callees = [_to_edge_item(r) for r in context["callees"][:callee_cap]]

        test_hits = get_affected_tests_bfs(conn, sym["id"], max_hops=8)
        tests, direct_test_files, total_test_files = _summarize_tests(test_hits, cap=test_cap)

        blast = get_blast_radius(conn, sym["id"])
        symbol_metrics = get_symbol_metrics(conn, sym["id"])
        graph_metrics = get_graph_metrics(conn, sym["id"])
        layers = _layer_analysis(conn, sym["id"], cap=layer_cap)

        risk_score, risk_level, risk_factors = _risk_score(
            blast=blast,
            symbol_metrics=symbol_metrics,
            graph_metrics=graph_metrics,
            direct_test_files=direct_test_files,
            total_test_files=total_test_files,
            layer_violation_count=layers["violation_count"],
            move_sensitive_count=layers["move_sensitive_count"],
        )

        # Keep deterministic ordering for signal summary.
        major_factors = sorted(
            risk_factors.items(),
            key=lambda item: (-item[1], item[0]),
        )
        signal_summary = [f"{k}={v:.1f}" for k, v in major_factors[:3] if v > 0]
        verdict = (
            f"{risk_level} breaking-change risk ({risk_score}/100)"
            f" for {sym['qualified_name'] or sym['name']}"
        )

        definition = {
            "name": sym["name"],
            "qualified_name": sym["qualified_name"] or sym["name"],
            "kind": sym["kind"],
            "signature": _trim_signature(sym["signature"]),
            "location": loc(sym["file_path"], sym["line_start"]),
            "layer": layers["current_layer"],
        }

        if json_mode:
            payload = json_envelope(
                "guard",
                summary={
                    "verdict": verdict,
                    "risk_score": risk_score,
                    "risk_level": risk_level,
                    "callers": len(context["non_test_callers"]),
                    "callees": len(context["callees"]),
                    "test_files": total_test_files,
                    "layer_violations": layers["violation_count"],
                    "move_sensitive_edges": layers["move_sensitive_count"],
                    "signals": signal_summary,
                },
                budget=token_budget,
                definition=definition,
                callers=callers,
                callees=callees,
                tests=tests,
                blast_radius=blast,
                metrics={
                    "symbol": symbol_metrics or {},
                    "graph": graph_metrics or {},
                },
                risk={
                    "score": risk_score,
                    "level": risk_level,
                    "factors": risk_factors,
                },
                layer_analysis={
                    "current_layer": layers["current_layer"],
                    "violation_count": layers["violation_count"],
                    "violations": layers["violations"],
                    "move_sensitive_count": layers["move_sensitive_count"],
                    "move_sensitive_edges": layers["move_sensitive_edges"],
                },
            )
            click.echo(to_json(payload))
            return

        lines = []
        lines.append(
            f"GUARD: {abbrev_kind(sym['kind'])} {sym['qualified_name'] or sym['name']} "
            f"{loc(sym['file_path'], sym['line_start'])}"
        )
        if definition["signature"]:
            lines.append(f"Signature: {definition['signature']}")
        lines.append(f"Risk: {risk_score}/100 [{risk_level}]")
        lines.append(
            "Signals: "
            f"blast={blast['dependent_symbols']} syms/{blast['dependent_files']} files, "
            f"tests={direct_test_files} direct/{total_test_files} total, "
            f"layers={layers['violation_count']} violations"
        )

        lines.append("")
        lines.append(f"Callers ({len(context['non_test_callers'])}, showing {len(callers)}):")
        if callers:
            for c in callers:
                edge = f" [{c['edge_kind']}]" if c["edge_kind"] else ""
                lines.append(f"  - {abbrev_kind(c['kind'])} {c['name']} {c['location']}{edge}")
        else:
            lines.append("  - (none)")

        lines.append("")
        lines.append(f"Callees ({len(context['callees'])}, showing {len(callees)}):")
        if callees:
            for c in callees:
                edge = f" [{c['edge_kind']}]" if c["edge_kind"] else ""
                lines.append(f"  - {abbrev_kind(c['kind'])} {c['name']} {c['location']}{edge}")
        else:
            lines.append("  - (none)")

        lines.append("")
        lines.append(f"Tests ({total_test_files}, showing {len(tests)}):")
        if tests:
            for t in tests:
                via = f" via {t['via']}" if t.get("via") else ""
                lines.append(f"  - {t['kind']} {t['file']} ({t['hops']} hops{via})")
        else:
            lines.append("  - (none found)")

        lines.append("")
        layer_line = "Layer analysis:"
        if layers["current_layer"] is not None:
            layer_line += f" current=L{layers['current_layer']}"
        layer_line += (
            f", violations={layers['violation_count']}, "
            f"move-sensitive={layers['move_sensitive_count']}"
        )
        lines.append(layer_line)
        for v in layers["violations"]:
            lines.append(
                f"  - violation: {v['source']} L{v['source_layer']} -> "
                f"{v['target']} L{v['target_layer']} (dist={v['layer_distance']})"
            )
        if not layers["violations"]:
            lines.append("  - violations: (none)")

        output = "\n".join(lines)
        click.echo(budget_truncate(output, token_budget))
