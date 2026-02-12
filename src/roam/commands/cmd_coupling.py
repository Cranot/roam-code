"""Show temporal coupling: files that change together."""

import hashlib

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import get_changed_files, resolve_changed_to_db


# ---------------------------------------------------------------------------
# Surprise score: Jaccard similarity against hypergraph patterns
# ---------------------------------------------------------------------------

def _compute_surprise(conn, change_fids):
    """Compute surprise score (0-1) for a set of file IDs.

    0.0 = you always change these files together (seen before).
    1.0 = never seen this combination.
    """
    if not change_fids or len(change_fids) < 2:
        return 0.0, None, 0.0

    change_set = set(change_fids)

    # Get all hyperedge IDs that share at least one member with our change set
    ph = ",".join("?" for _ in change_set)
    candidate_edges = conn.execute(
        f"""SELECT DISTINCT hyperedge_id FROM git_hyperedge_members
            WHERE file_id IN ({ph})""",
        list(change_set),
    ).fetchall()

    if not candidate_edges:
        return 0.5, None, 0.0  # no history → moderate surprise

    max_jaccard = 0.0
    best_pattern = None

    for row in candidate_edges:
        he_id = row["hyperedge_id"]
        members = conn.execute(
            "SELECT file_id FROM git_hyperedge_members WHERE hyperedge_id = ?",
            (he_id,),
        ).fetchall()
        pattern_set = {m["file_id"] for m in members}

        intersection = len(change_set & pattern_set)
        union = len(change_set | pattern_set)
        if union > 0:
            jaccard = intersection / union
            if jaccard > max_jaccard:
                max_jaccard = jaccard
                best_pattern = pattern_set

    # Resolve best pattern paths
    best_paths = None
    if best_pattern:
        ph = ",".join("?" for _ in best_pattern)
        rows = conn.execute(
            f"SELECT path FROM files WHERE id IN ({ph})",
            list(best_pattern),
        ).fetchall()
        best_paths = sorted(r["path"] for r in rows)

    return round(1.0 - max_jaccard, 3), best_paths, round(max_jaccard, 3)


# ---------------------------------------------------------------------------
# Against mode: check coupling for a change set
# ---------------------------------------------------------------------------

def _against_mode(conn, change_fids, file_map, min_strength, min_cochanges):
    """Check which co-change partners are missing from the change set."""
    path_to_id = {}
    file_commits = {}
    for f in conn.execute("SELECT id, path FROM files").fetchall():
        path_to_id[f["path"]] = f["id"]
    id_to_path = {v: k for k, v in path_to_id.items()}
    for fs in conn.execute("SELECT file_id, commit_count FROM file_stats").fetchall():
        file_commits[fs["file_id"]] = fs["commit_count"] or 1

    change_set = set(change_fids)
    missing = []
    included = []

    for path, fid in file_map.items():
        # Get co-change partners
        partners = conn.execute(
            """SELECT file_id_a, file_id_b, cochange_count
               FROM git_cochange
               WHERE file_id_a = ? OR file_id_b = ?""",
            (fid, fid),
        ).fetchall()

        for p in partners:
            partner_fid = p["file_id_b"] if p["file_id_a"] == fid else p["file_id_a"]
            cochanges = p["cochange_count"]
            if cochanges < min_cochanges:
                continue

            avg = (file_commits.get(fid, 1) + file_commits.get(partner_fid, 1)) / 2
            strength = cochanges / avg if avg > 0 else 0
            if strength < min_strength:
                continue

            partner_path = id_to_path.get(partner_fid, f"file_id={partner_fid}")
            entry = {
                "path": partner_path,
                "strength": round(strength, 2),
                "cochanges": cochanges,
                "partner_of": path,
            }

            if partner_fid in change_set:
                included.append(entry)
            else:
                missing.append(entry)

    # Deduplicate missing by path (keep highest strength)
    seen = {}
    for m in missing:
        if m["path"] not in seen or m["strength"] > seen[m["path"]]["strength"]:
            seen[m["path"]] = m
    missing = sorted(seen.values(), key=lambda x: -x["strength"])

    return missing, included


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option('-n', 'count', default=20, help='Number of pairs to show')
@click.option('--staged', is_flag=True, help='Check coupling for staged changes')
@click.option('--against', 'commit_range', default=None,
              help='Check coupling for a commit range (e.g. HEAD~3..HEAD)')
@click.option('--min-strength', default=0.3, type=float, show_default=True,
              help='Minimum coupling strength for against mode')
@click.option('--min-cochanges', default=2, type=int, show_default=True,
              help='Minimum co-change count for against mode')
@click.pass_context
def coupling(ctx, count, staged, commit_range, min_strength, min_cochanges):
    """Show temporal coupling: file pairs that change together.

    Default: show top co-change pairs.
    With --staged or --against: show missing co-change partners for your changes.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # --- Against/staged mode ---
        if staged or commit_range:
            root = find_project_root()
            changed = get_changed_files(root, staged=staged, commit_range=commit_range)
            if not changed:
                label = commit_range or "staged"
                if json_mode:
                    click.echo(to_json(json_envelope("coupling",
                        summary={"error": f"No changes for {label}"},
                    )))
                else:
                    click.echo(f"No changes found for {label}.")
                return

            file_map = resolve_changed_to_db(conn, changed)
            if not file_map:
                if json_mode:
                    click.echo(to_json(json_envelope("coupling",
                        summary={"error": "Changed files not in index"},
                    )))
                else:
                    click.echo("Changed files not found in index.")
                return

            change_fids = list(file_map.values())
            missing, included = _against_mode(
                conn, change_fids, file_map, min_strength, min_cochanges,
            )
            surprise, best_pattern, similarity = _compute_surprise(conn, change_fids)

            if json_mode:
                envelope = json_envelope("coupling",
                    summary={
                        "change_set": len(file_map),
                        "missing": len(missing),
                        "surprise_score": surprise,
                    },
                    mode="against",
                    change_set=sorted(file_map.keys()),
                    surprise_score=surprise,
                    closest_similarity=similarity,
                    closest_historical_pattern=best_pattern,
                    missing_cochanges=missing[:30],
                    included_partners=included[:20],
                )
                click.echo(to_json(envelope))
                return

            label = commit_range or "staged"
            click.echo(f"=== Coupling Check ({label}, {len(file_map)} files) ===\n")
            click.echo(f"Surprise score: {surprise:.0%}"
                        f"{'  (unfamiliar combination!)' if surprise > 0.7 else ''}")
            click.echo()

            if missing:
                click.echo(f"Missing co-change partners ({len(missing)}):")
                click.echo("(files you usually change together but are not in this diff)")
                rows = []
                for m in missing[:20]:
                    rows.append([
                        "MISSING", m["path"],
                        f"{m['strength']:.0%}",
                        str(m["cochanges"]),
                        m["partner_of"],
                    ])
                click.echo(format_table(
                    ["Status", "File", "Strength", "Co-changes", "Partner of"],
                    rows,
                ))
            else:
                click.echo("No missing co-change partners.")

            if included:
                click.echo(f"\nIncluded partners ({len(included)}):")
                rows = []
                for i in included[:10]:
                    rows.append([
                        "OK", i["path"], f"{i['strength']:.0%}",
                        str(i["cochanges"]),
                    ])
                click.echo(format_table(
                    ["Status", "File", "Strength", "Co-changes"],
                    rows,
                ))
            return

        # --- Default mode: top co-change pairs ---
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
                click.echo(to_json(json_envelope("coupling",
                    summary={"pairs": 0},
                    pairs=[],
                )))
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
            hidden_pairs = sum(1 for p in pairs if not p["has_structural_edge"])
            click.echo(to_json(json_envelope("coupling",
                summary={"pairs": len(pairs), "hidden_coupling": hidden_pairs},
                pairs=pairs,
            )))
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
