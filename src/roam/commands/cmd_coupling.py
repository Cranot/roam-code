"""Show temporal coupling: files that change together."""

import click

from roam.db.connection import open_db
from roam.output.formatter import format_table, to_json
from roam.commands.resolve import ensure_index


@click.command()
@click.option('-n', 'count', default=20, help='Number of pairs to show')
@click.pass_context
def coupling(ctx, count):
    """Show temporal coupling: file pairs that change together."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        rows = conn.execute("""
            SELECT fa.path as path_a, fb.path as path_b,
                   gc.cochange_count
            FROM git_cochange gc
            JOIN files fa ON gc.file_id_a = fa.id
            JOIN files fb ON gc.file_id_b = fb.id
            ORDER BY gc.cochange_count DESC
            LIMIT ?
        """, (count,)).fetchall()

        if not rows:
            if json_mode:
                click.echo(to_json({"pairs": []}))
            else:
                click.echo("No co-change data available. Run `roam index` on a git repository.")
            return

        # Check which pairs have structural connections (file_edges)
        file_edge_set = set()
        fe_rows = conn.execute(
            "SELECT source_file_id, target_file_id FROM file_edges WHERE symbol_count >= 2"
        ).fetchall()
        for fe in fe_rows:
            file_edge_set.add((fe["source_file_id"], fe["target_file_id"]))
            file_edge_set.add((fe["target_file_id"], fe["source_file_id"]))

        # Build file path -> id lookup and commit counts for normalization
        path_to_id = {}
        file_commits = {}
        for f in conn.execute("SELECT id, path FROM files").fetchall():
            path_to_id[f["path"]] = f["id"]
        for fs in conn.execute("SELECT file_id, commit_count FROM file_stats").fetchall():
            file_commits[fs["file_id"]] = fs["commit_count"] or 1

        table_rows = []
        for r in rows:
            path_a = r["path_a"]
            path_b = r["path_b"]
            cochange = r["cochange_count"]
            fid_a = path_to_id.get(path_a)
            fid_b = path_to_id.get(path_b)

            has_edge = ""
            if fid_a and fid_b:
                if (fid_a, fid_b) in file_edge_set:
                    has_edge = "yes"
                else:
                    has_edge = "HIDDEN"

            # Temporal coupling strength: normalized by avg commits
            strength = ""
            if fid_a and fid_b:
                avg_commits = (file_commits.get(fid_a, 1) + file_commits.get(fid_b, 1)) / 2
                if avg_commits > 0:
                    ratio = cochange / avg_commits
                    strength = f"{ratio:.0%}"

            table_rows.append([str(cochange), strength, has_edge, path_a, path_b])

        if json_mode:
            pairs = []
            for r in rows:
                pa, pb = r["path_a"], r["path_b"]
                fid_a, fid_b = path_to_id.get(pa), path_to_id.get(pb)
                has_struct = bool(fid_a and fid_b and (fid_a, fid_b) in file_edge_set)
                strength_val = None
                if fid_a and fid_b:
                    avg = (file_commits.get(fid_a, 1) + file_commits.get(fid_b, 1)) / 2
                    if avg > 0:
                        strength_val = round(r["cochange_count"] / avg, 2)
                pairs.append({
                    "file_a": pa, "file_b": pb,
                    "cochange_count": r["cochange_count"],
                    "strength": strength_val,
                    "has_structural_edge": has_struct,
                })
            click.echo(to_json({"pairs": pairs}))
            return

        click.echo("=== Temporal coupling (co-change frequency) ===")
        click.echo(format_table(
            ["co-changes", "strength", "structural?", "file A", "file B"],
            table_rows,
        ))

        hidden_count = sum(1 for r in table_rows if r[2] == "HIDDEN")
        total_pairs = len(table_rows)
        if hidden_count:
            pct = hidden_count * 100 / total_pairs if total_pairs else 0
            click.echo(f"\n{hidden_count}/{total_pairs} pairs ({pct:.0f}%) have NO import edge but co-change frequently (hidden coupling).")
