"""Root cause analysis for a failing symbol.

Given a symbol suspected to be involved in a bug, ranks likely root
causes by combining four signals no other tool brings together:
(1) call graph proximity, (2) git churn, (3) cognitive complexity,
(4) co-change history with the failing symbol.
"""

from __future__ import annotations

import click
import networkx as nx

from roam.db.connection import open_db
from roam.graph.builder import build_symbol_graph
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol


def _get_symbol_metrics(conn, sym_id):
    """Fetch complexity and churn for a symbol."""
    sm = conn.execute(
        "SELECT cognitive_complexity, nesting_depth, line_count "
        "FROM symbol_metrics WHERE symbol_id = ?",
        (sym_id,),
    ).fetchone()

    gm = conn.execute(
        "SELECT pagerank, in_degree, out_degree, betweenness "
        "FROM graph_metrics WHERE symbol_id = ?",
        (sym_id,),
    ).fetchone()

    file_row = conn.execute(
        "SELECT fs.commit_count, fs.total_churn, fs.cochange_entropy, "
        "       fs.health_score, f.path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "LEFT JOIN file_stats fs ON f.id = fs.file_id "
        "WHERE s.id = ?",
        (sym_id,),
    ).fetchone()

    return {
        "complexity": (sm["cognitive_complexity"] or 0) if sm else 0,
        "nesting": (sm["nesting_depth"] or 0) if sm else 0,
        "line_count": (sm["line_count"] or 0) if sm else 0,
        "pagerank": round((gm["pagerank"] or 0), 4) if gm else 0,
        "in_degree": (gm["in_degree"] or 0) if gm else 0,
        "out_degree": (gm["out_degree"] or 0) if gm else 0,
        "betweenness": round((gm["betweenness"] or 0), 3) if gm else 0,
        "commits": (file_row["commit_count"] or 0) if file_row else 0,
        "churn": (file_row["total_churn"] or 0) if file_row else 0,
        "entropy": round((file_row["cochange_entropy"] or 0), 2) if file_row else 0,
        "health": (file_row["health_score"] or 0) if file_row else 0,
        "file_path": file_row["path"] if file_row else "",
    }


def _risk_score(metrics):
    """Compute a composite risk score for root-cause ranking.

    Higher = more likely to be a root cause.  Combines churn,
    complexity, low health, and entropy.
    """
    churn_norm = min(metrics["commits"] / 50, 1.0)  # 50+ commits = max
    cc_norm = min(metrics["complexity"] / 30, 1.0)   # 30+ cc = max
    health_risk = max(0, (7 - metrics["health"]) / 7) if metrics["health"] else 0.5
    entropy_risk = metrics["entropy"]

    return round(
        churn_norm * 0.30
        + cc_norm * 0.30
        + health_risk * 0.25
        + entropy_risk * 0.15,
        3,
    )


def _cochange_partners(conn, file_id, limit=10):
    """Find files that frequently change together with the given file."""
    rows = conn.execute(
        """SELECT CASE WHEN cc.file_id_a = ? THEN cc.file_id_b ELSE cc.file_id_a END as partner_id,
                  cc.cochange_count, f.path
           FROM git_cochange cc
           JOIN files f ON f.id = CASE WHEN cc.file_id_a = ? THEN cc.file_id_b ELSE cc.file_id_a END
           WHERE cc.file_id_a = ? OR cc.file_id_b = ?
           ORDER BY cc.cochange_count DESC
           LIMIT ?""",
        (file_id, file_id, file_id, file_id, limit),
    ).fetchall()
    return [{"file": r["path"], "cochange_count": r["cochange_count"]} for r in rows]


def _recent_changes(conn, file_id, limit=5):
    """Get recent git commits touching this file."""
    rows = conn.execute(
        """SELECT gc.hash, gc.author, gc.message, gc.timestamp
           FROM git_commits gc
           JOIN git_file_changes gfc ON gc.id = gfc.commit_id
           WHERE gfc.file_id = ?
           ORDER BY gc.timestamp DESC
           LIMIT ?""",
        (file_id, limit),
    ).fetchall()
    return [
        {
            "hash": r["hash"][:8],
            "author": r["author"],
            "message": (r["message"] or "")[:80],
        }
        for r in rows
    ]


@click.command()
@click.argument("name")
@click.option("--depth", default=2, help="How many hops to analyze (default 2)")
@click.pass_context
def diagnose(ctx, name, depth):
    """Root cause analysis for a failing symbol.

    Given a symbol suspected of causing a bug, ranks upstream callers
    and downstream callees by a composite risk score combining:
    git churn, cognitive complexity, file health, and co-change entropy.

    Also shows co-change partners and recent git history for the
    symbol's file.

    Example:

        roam diagnose handle_payment
        roam diagnose UserService.create --depth 3
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, name)
        if sym is None:
            click.echo(f"Symbol not found: {name}")
            raise SystemExit(1)

        sym_id = sym["id"]
        G = build_symbol_graph(conn)

        if sym_id not in G:
            click.echo(f"Symbol not in dependency graph: {name}")
            raise SystemExit(1)

        target_metrics = _get_symbol_metrics(conn, sym_id)

        # Upstream callers (predecessors in call graph) up to depth hops
        upstream_ids = set()
        frontier = {sym_id}
        for _ in range(depth):
            next_frontier = set()
            for nid in frontier:
                if nid in G:
                    for pred in G.predecessors(nid):
                        if pred != sym_id and pred not in upstream_ids:
                            upstream_ids.add(pred)
                            next_frontier.add(pred)
            frontier = next_frontier

        # Downstream callees (successors) up to depth hops
        downstream_ids = set()
        frontier = {sym_id}
        for _ in range(depth):
            next_frontier = set()
            for nid in frontier:
                if nid in G:
                    for succ in G.successors(nid):
                        if succ != sym_id and succ not in downstream_ids:
                            downstream_ids.add(succ)
                            next_frontier.add(succ)
            frontier = next_frontier

        # Rank upstream by risk score
        def _build_ranked(sym_ids, direction):
            ranked = []
            for sid in sym_ids:
                row = conn.execute(
                    "SELECT s.name, s.qualified_name, s.kind, s.line_start, f.path, f.id as file_id "
                    "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
                    (sid,),
                ).fetchone()
                if not row:
                    continue
                metrics = _get_symbol_metrics(conn, sid)
                risk = _risk_score(metrics)
                ranked.append({
                    "name": row["qualified_name"] or row["name"],
                    "kind": abbrev_kind(row["kind"]),
                    "location": loc(row["path"], row["line_start"]),
                    "risk_score": risk,
                    "complexity": metrics["complexity"],
                    "commits": metrics["commits"],
                    "health": metrics["health"],
                    "entropy": metrics["entropy"],
                    "direction": direction,
                })
            ranked.sort(key=lambda x: -x["risk_score"])
            return ranked

        upstream_ranked = _build_ranked(upstream_ids, "upstream")
        downstream_ranked = _build_ranked(downstream_ids, "downstream")

        # Co-change partners
        file_row = conn.execute(
            "SELECT f.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
            (sym_id,),
        ).fetchone()
        cochanges = _cochange_partners(conn, file_row["id"]) if file_row else []
        recent = _recent_changes(conn, file_row["id"]) if file_row else []

        # Build verdict
        all_suspects = upstream_ranked[:5] + downstream_ranked[:5]
        if all_suspects:
            top = all_suspects[0]
            verdict = (
                f"Top suspect: {top['name']} "
                f"(risk={top['risk_score']:.2f}, cc={top['complexity']}, "
                f"commits={top['commits']}, health={top['health']}/10)"
            )
        else:
            verdict = "No upstream/downstream symbols found within depth range."

        if json_mode:
            click.echo(to_json(json_envelope("diagnose",
                summary={
                    "target": sym["qualified_name"] or sym["name"],
                    "verdict": verdict,
                    "upstream_count": len(upstream_ranked),
                    "downstream_count": len(downstream_ranked),
                },
                target_metrics=target_metrics,
                upstream=upstream_ranked[:15],
                downstream=downstream_ranked[:15],
                cochange_partners=cochanges,
                recent_commits=recent,
            )))
            return

        # Text output
        click.echo(f"\nVERDICT: {verdict}\n")
        sym_name = sym["qualified_name"] or sym["name"]
        click.echo(f"Diagnose: {sym_name}")
        click.echo(f"  {loc(target_metrics['file_path'], sym['line_start'])}")
        click.echo(f"  complexity={target_metrics['complexity']}, "
                    f"commits={target_metrics['commits']}, "
                    f"health={target_metrics['health']}/10\n")

        if upstream_ranked:
            click.echo(f"Upstream suspects (callers, ranked by risk):\n")
            rows = [
                [r["name"], r["kind"], f"{r['risk_score']:.2f}",
                 str(r["complexity"]), str(r["commits"]),
                 f"{r['health']}/10", r["location"]]
                for r in upstream_ranked[:10]
            ]
            click.echo(format_table(
                ["Symbol", "Kind", "Risk", "CC", "Commits", "Health", "Location"],
                rows,
            ))

        if downstream_ranked:
            click.echo(f"\nDownstream suspects (callees, ranked by risk):\n")
            rows = [
                [r["name"], r["kind"], f"{r['risk_score']:.2f}",
                 str(r["complexity"]), str(r["commits"]),
                 f"{r['health']}/10", r["location"]]
                for r in downstream_ranked[:10]
            ]
            click.echo(format_table(
                ["Symbol", "Kind", "Risk", "CC", "Commits", "Health", "Location"],
                rows,
            ))

        if cochanges:
            click.echo(f"\nCo-change partners (files that change together):\n")
            for c in cochanges[:8]:
                click.echo(f"  {c['file']}  ({c['cochange_count']} co-changes)")

        if recent:
            click.echo(f"\nRecent commits to {target_metrics['file_path']}:\n")
            for c in recent:
                click.echo(f"  {c['hash']}  {c['author']:<20} {c['message']}")
