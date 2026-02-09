"""Show fan-in/fan-out metrics for symbols or files."""

import click

from roam.db.connection import open_db, db_exists
from roam.output.formatter import abbrev_kind, loc, format_table


def _ensure_index():
    if not db_exists():
        click.echo("No index found. Building...")
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command()
@click.argument('mode', default='symbol', type=click.Choice(['symbol', 'file']))
@click.option('-n', 'count', default=20, help='Number of items to show')
def fan(mode, count):
    """Show fan-in/fan-out: most connected symbols or files."""
    _ensure_index()

    with open_db(readonly=True) as conn:
        if mode == 'symbol':
            rows = conn.execute("""
                SELECT s.name, s.kind, f.path as file_path, s.line_start,
                       gm.in_degree, gm.out_degree,
                       (gm.in_degree + gm.out_degree) as total,
                       gm.betweenness, gm.pagerank
                FROM graph_metrics gm
                JOIN symbols s ON gm.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE gm.in_degree + gm.out_degree > 0
                ORDER BY total DESC
                LIMIT ?
            """, (count,)).fetchall()

            if not rows:
                click.echo("No graph metrics available. Run `roam index` first.")
                return

            table_rows = []
            for r in rows:
                flag = ""
                if r["in_degree"] > 10 and r["out_degree"] > 10:
                    flag = "HIGH-RISK"
                elif r["in_degree"] > 10:
                    flag = "hub"
                elif r["out_degree"] > 10:
                    flag = "spreader"

                bw = r["betweenness"] or 0
                bw_str = f"{bw:.0f}" if bw >= 10 else (f"{bw:.1f}" if bw > 0.5 else "")
                pr = r["pagerank"] or 0
                pr_str = f"{pr:.4f}" if pr > 0 else ""

                table_rows.append([
                    abbrev_kind(r["kind"]),
                    r["name"],
                    str(r["in_degree"]),
                    str(r["out_degree"]),
                    str(r["total"]),
                    bw_str,
                    pr_str,
                    flag,
                    loc(r["file_path"], r["line_start"]),
                ])

            click.echo("=== Fan-in/Fan-out (symbol level) ===")
            click.echo(format_table(
                ["kind", "name", "fan-in", "fan-out", "total", "btwn", "PR", "flag", "location"],
                table_rows,
            ))

        else:  # file mode
            rows = conn.execute("""
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
            """, (count,)).fetchall()

            if not rows:
                click.echo("No file edges available. Run `roam index` first.")
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

                table_rows.append([
                    r["path"],
                    str(r["fan_in"]),
                    str(r["fan_out"]),
                    str(total),
                    flag,
                ])

            click.echo("=== Fan-in/Fan-out (file level) ===")
            click.echo(format_table(
                ["path", "fan-in", "fan-out", "total", "flag"],
                table_rows,
            ))
