"""Show fan-in/fan-out metrics for symbols or files."""

from __future__ import annotations

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, open_db
from roam.output.file_role_hints import is_excluded_path
from roam.output.formatter import abbrev_kind, format_table, json_envelope, loc, to_json
from roam.output.framework_filter import FRAMEWORK_PRIMITIVE_NAMES as _FRAMEWORK_NAMES


def _filter_tooling_rows(rows):
    """Filter out rows whose ``file_path`` is in a default-excluded
    location (tooling, generated, examples, vendor, workspaces, etc.).

    Uses the shared ``output.file_role_hints`` set so all headline
    commands stay in sync.
    """
    return [r for r in rows if not is_excluded_path(r["file_path"] or "")]


_CROSS_FILE_HUB_THRESHOLD = 3


def _file_scope_metrics(conn, symbol_ids):
    """Return per-symbol intra/inter-file edge breakdowns.

    Splits each symbol's incoming and outgoing edges by whether the other
    side lives in the same file. Reports distinct file counts so callers
    can decide whether ``hub``/``spreader`` is architectural (many files)
    or just an intra-file convention (one large SFC, generated module).
    """
    if not symbol_ids:
        return {}

    meta = {
        sid: {
            "fan_in_intra": 0,
            "fan_in_inter": 0,
            "fan_in_files": 0,
            "fan_out_intra": 0,
            "fan_out_inter": 0,
            "fan_out_files": 0,
        }
        for sid in symbol_ids
    }

    # Incoming edges grouped by target_id with src.file_id distinct count.
    incoming = batched_in(
        conn,
        "SELECT e.target_id AS sid, src.file_id AS other_file, tgt.file_id AS self_file "
        "FROM edges e "
        "JOIN symbols src ON e.source_id = src.id "
        "JOIN symbols tgt ON e.target_id = tgt.id "
        "WHERE e.target_id IN ({ph})",
        list(symbol_ids),
    )
    in_files: dict[int, set[int]] = {sid: set() for sid in symbol_ids}
    for row in incoming:
        sid = row["sid"]
        bucket = meta[sid]
        if row["other_file"] == row["self_file"]:
            bucket["fan_in_intra"] += 1
        else:
            bucket["fan_in_inter"] += 1
        in_files[sid].add(row["other_file"])

    # Outgoing edges grouped by source_id with tgt.file_id distinct count.
    outgoing = batched_in(
        conn,
        "SELECT e.source_id AS sid, tgt.file_id AS other_file, src.file_id AS self_file "
        "FROM edges e "
        "JOIN symbols src ON e.source_id = src.id "
        "JOIN symbols tgt ON e.target_id = tgt.id "
        "WHERE e.source_id IN ({ph})",
        list(symbol_ids),
    )
    out_files: dict[int, set[int]] = {sid: set() for sid in symbol_ids}
    for row in outgoing:
        sid = row["sid"]
        bucket = meta[sid]
        if row["other_file"] == row["self_file"]:
            bucket["fan_out_intra"] += 1
        else:
            bucket["fan_out_inter"] += 1
        out_files[sid].add(row["other_file"])

    for sid in symbol_ids:
        # Subtract self-file from outbound to keep "files this depends on"
        # comparable to inbound (consumers always live in another file).
        meta[sid]["fan_in_files"] = len(in_files.get(sid, set()))
        meta[sid]["fan_out_files"] = len(out_files.get(sid, set()))

    return meta


def _scope_flag(meta_entry, in_deg, out_deg):
    """Pick the hub/spreader label based on cross-file reach.

    The historic flag fired on raw edge counts, which over-marked symbols
    confined to one large SFC. Cross-file reach (``fan_*_files``) is a
    better signal of architectural pressure — a const used 342 times
    inside its own file is not a spreader.
    """
    in_files = meta_entry.get("fan_in_files", 0)
    out_files = meta_entry.get("fan_out_files", 0)
    cross_in = in_files >= _CROSS_FILE_HUB_THRESHOLD and in_deg > 10
    cross_out = out_files >= _CROSS_FILE_HUB_THRESHOLD and out_deg > 10
    if cross_in and cross_out:
        return "HIGH-RISK"
    if cross_in:
        return "hub"
    if cross_out:
        return "spreader"
    if in_deg > 10 and in_files <= 1:
        return "local-hub"
    if out_deg > 10 and out_files <= 1:
        return "local-spreader"
    return ""


@click.command()
@click.argument("mode", default="symbol", type=click.Choice(["symbol", "file"]))
@click.option("-n", "count", default=20, help="Number of items to show")
@click.option("--no-framework", is_flag=True, help="Filter out framework/boilerplate symbols")
@click.option(
    "--include-tooling",
    is_flag=True,
    default=False,
    help=(
        "Include CI scripts, dev tooling, build, and generated files. "
        "Excluded by default — high fan-in in dev/.github/benchmarks "
        "is expected and dominates the headline."
    ),
)
@click.pass_context
def fan(ctx, mode, count, no_framework, include_tooling):
    """Show fan-in/fan-out: most connected symbols or files.

    Unlike ``coupling`` (which measures temporal co-change frequency), this
    command measures structural connectivity (import/call edges) and flags
    hub/spreader hotspots.

    \b
    Examples:
      roam fan
      roam fan --mode file
      roam fan --count 30 --no-framework

    See also ``coupling`` (co-change frequency), ``deps`` (dependency
    graph), and ``hotspots`` (runtime hotspots).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        # Pull more rows than ``count`` when filtering, so the displayed
        # top-N still has ``count`` entries after exclusions. 5x is a
        # comfortable safety factor for typical tooling shares.
        fetch_limit = count * 5 if not include_tooling else count
        if mode == "symbol":
            rows = conn.execute(
                """
                SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start,
                       gm.in_degree, gm.out_degree,
                       (gm.in_degree + gm.out_degree) as total,
                       gm.betweenness, gm.pagerank
                FROM graph_metrics gm
                JOIN symbols s ON gm.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE gm.in_degree + gm.out_degree > 0
                ORDER BY total DESC
                LIMIT ?
            """,
                (fetch_limit,),
            ).fetchall()

            if not include_tooling:
                rows = _filter_tooling_rows(rows)
            rows = rows[:count]

            if no_framework:
                rows = [r for r in rows if r["name"] not in _FRAMEWORK_NAMES]

            scope_meta = _file_scope_metrics(conn, [r["id"] for r in rows])

            if not rows:
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "fan",
                                budget=token_budget,
                                summary={
                                    "verdict": "no graph metrics available",
                                    "mode": mode,
                                    "items": 0,
                                },
                                mode=mode,
                                items=[],
                            )
                        )
                    )
                else:
                    click.echo("No graph metrics available. Run `roam index` first.")
                return

            if json_mode:
                _top_in = max(rows, key=lambda r: r["in_degree"] or 0)
                _top_out = max(rows, key=lambda r: r["out_degree"] or 0)
                _verdict = (
                    f"top fan-in: {_top_in['name']}({_top_in['in_degree'] or 0}), "
                    f"top fan-out: {_top_out['name']}({_top_out['out_degree'] or 0})"
                )
                click.echo(
                    to_json(
                        json_envelope(
                            "fan",
                            budget=token_budget,
                            summary={"verdict": _verdict, "mode": mode, "items": len(rows)},
                            mode=mode,
                            items=[
                                {
                                    "name": r["name"],
                                    "kind": r["kind"],
                                    "fan_in": r["in_degree"] or 0,
                                    "fan_out": r["out_degree"] or 0,
                                    "total": (r["in_degree"] or 0) + (r["out_degree"] or 0),
                                    "betweenness": round(r["betweenness"] or 0, 1),
                                    "pagerank": round(r["pagerank"] or 0, 4),
                                    "location": loc(r["file_path"], r["line_start"]),
                                    "fan_in_intra": scope_meta.get(r["id"], {}).get("fan_in_intra", 0),
                                    "fan_in_inter": scope_meta.get(r["id"], {}).get("fan_in_inter", 0),
                                    "fan_in_files": scope_meta.get(r["id"], {}).get("fan_in_files", 0),
                                    "fan_out_intra": scope_meta.get(r["id"], {}).get("fan_out_intra", 0),
                                    "fan_out_inter": scope_meta.get(r["id"], {}).get("fan_out_inter", 0),
                                    "fan_out_files": scope_meta.get(r["id"], {}).get("fan_out_files", 0),
                                    "flag": _scope_flag(
                                        scope_meta.get(r["id"], {}),
                                        r["in_degree"] or 0,
                                        r["out_degree"] or 0,
                                    ),
                                }
                                for r in rows
                            ],
                        )
                    )
                )
                return

            table_rows = []
            for r in rows:
                in_deg = r["in_degree"] or 0
                out_deg = r["out_degree"] or 0
                total = in_deg + out_deg
                flag = _scope_flag(scope_meta.get(r["id"], {}), in_deg, out_deg)
                bw = r["betweenness"] or 0
                bw_str = f"{bw:.0f}" if bw >= 10 else (f"{bw:.1f}" if bw > 0.5 else "")
                pr = r["pagerank"] or 0
                pr_str = f"{pr:.4f}" if pr > 0 else ""

                table_rows.append(
                    [
                        abbrev_kind(r["kind"]),
                        r["name"],
                        str(in_deg),
                        str(out_deg),
                        str(total),
                        bw_str,
                        pr_str,
                        flag,
                        loc(r["file_path"], r["line_start"]),
                    ]
                )

            _top_in_r = max(rows, key=lambda r: r["in_degree"] or 0)
            _top_out_r = max(rows, key=lambda r: r["out_degree"] or 0)
            _verdict = (
                f"top fan-in: {_top_in_r['name']}({_top_in_r['in_degree'] or 0}), "
                f"top fan-out: {_top_out_r['name']}({_top_out_r['out_degree'] or 0})"
            )
            click.echo(f"VERDICT: {_verdict}\n")
            click.echo("=== Fan-in/Fan-out (symbol level) ===")
            click.echo(
                format_table(
                    [
                        "kind",
                        "name",
                        "fan-in",
                        "fan-out",
                        "total",
                        "btwn",
                        "PR",
                        "flag",
                        "location",
                    ],
                    table_rows,
                )
            )

        else:  # file mode
            rows = conn.execute(
                """
                SELECT f.path,
                       COUNT(DISTINCT CASE WHEN fe_in.target_file_id = f.id THEN fe_in.source_file_id END) as fan_in,
                       COUNT(DISTINCT CASE WHEN fe_out.source_file_id = f.id THEN fe_out.target_file_id END) as fan_out
                FROM files f
                LEFT JOIN file_edges fe_in ON fe_in.target_file_id = f.id
                LEFT JOIN file_edges fe_out ON fe_out.source_file_id = f.id
                GROUP BY f.id
                HAVING fan_in + fan_out > 0
                ORDER BY fan_in + fan_out DESC
                LIMIT ?
            """,
                (count,),
            ).fetchall()

            if not rows:
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "fan",
                                budget=token_budget,
                                summary={
                                    "verdict": "no file edges available",
                                    "mode": mode,
                                    "items": 0,
                                },
                                mode=mode,
                                items=[],
                            )
                        )
                    )
                else:
                    click.echo("No file edges available. Run `roam index` first.")
                return

            if json_mode:
                _top_in_r = max(rows, key=lambda r: r["fan_in"])
                _top_out_r = max(rows, key=lambda r: r["fan_out"])
                _top_in_name = _top_in_r["path"].split("/")[-1]
                _top_out_name = _top_out_r["path"].split("/")[-1]
                _verdict = (
                    f"top fan-in: {_top_in_name}({_top_in_r['fan_in']}), "
                    f"top fan-out: {_top_out_name}({_top_out_r['fan_out']})"
                )
                click.echo(
                    to_json(
                        json_envelope(
                            "fan",
                            budget=token_budget,
                            summary={"verdict": _verdict, "mode": mode, "items": len(rows)},
                            mode=mode,
                            items=[
                                {
                                    "path": r["path"],
                                    "fan_in": r["fan_in"],
                                    "fan_out": r["fan_out"],
                                    "total": r["fan_in"] + r["fan_out"],
                                }
                                for r in rows
                            ],
                        )
                    )
                )
                return

            table_rows = []
            for r in rows:
                total = r["fan_in"] + r["fan_out"]
                flag = ""
                if r["fan_in"] > 5 and r["fan_out"] > 5:
                    flag = "HIGH-RISK"
                elif r["fan_in"] > 5:
                    flag = "hub"
                elif r["fan_out"] > 5:
                    flag = "spreader"

                table_rows.append(
                    [
                        r["path"],
                        str(r["fan_in"]),
                        str(r["fan_out"]),
                        str(total),
                        flag,
                    ]
                )

            _top_in_r = max(rows, key=lambda r: r["fan_in"])
            _top_out_r = max(rows, key=lambda r: r["fan_out"])
            _top_in_name = _top_in_r["path"].split("/")[-1]
            _top_out_name = _top_out_r["path"].split("/")[-1]
            _verdict = (
                f"top fan-in: {_top_in_name}({_top_in_r['fan_in']}), "
                f"top fan-out: {_top_out_name}({_top_out_r['fan_out']})"
            )
            click.echo(f"VERDICT: {_verdict}\n")
            click.echo("=== Fan-in/Fan-out (file level) ===")
            click.echo(
                format_table(
                    ["path", "fan-in", "fan-out", "total", "flag"],
                    table_rows,
                )
            )
