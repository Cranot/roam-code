"""Show function-level temporal coupling: symbols that change together across files."""

from collections import defaultdict

import click

from roam.db.connection import open_db
from roam.output.formatter import format_table, to_json, json_envelope, loc
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def _build_symbol_cochange(conn):
    """Build a cross-file symbol co-change matrix from git history.

    For each commit, find which files changed.  Every symbol in a changed
    file is considered "changed" for that commit.  For every pair of symbols
    that belong to *different* files and co-changed in the same commit,
    increment the pair counter.

    Returns dict[(sym_id_lo, sym_id_hi)] -> count
    """
    # Step 1: gather commit -> list of changed file_ids
    rows = conn.execute("""
        SELECT commit_id, file_id
        FROM git_file_changes
        WHERE file_id IS NOT NULL
        ORDER BY commit_id
    """).fetchall()

    commit_files = defaultdict(set)
    for r in rows:
        commit_files[r["commit_id"]].add(r["file_id"])

    # Step 2: pre-load symbol -> file mapping (only symbols with line info)
    sym_rows = conn.execute("""
        SELECT id, file_id FROM symbols
        WHERE line_start IS NOT NULL
    """).fetchall()

    file_to_syms = defaultdict(list)
    sym_file = {}
    for s in sym_rows:
        file_to_syms[s["file_id"]].append(s["id"])
        sym_file[s["id"]] = s["file_id"]

    # Step 3: for each commit, compute cross-file symbol pairs
    pair_count = defaultdict(int)

    for _cid, fids in commit_files.items():
        # Skip mega-commits (likely merges / reformats)
        if len(fids) > 30:
            continue

        # Collect all symbols that changed, grouped by file
        per_file_syms = []
        for fid in fids:
            syms = file_to_syms.get(fid)
            if syms:
                per_file_syms.append((fid, syms))

        # Cross-file pairs only
        n = len(per_file_syms)
        for i in range(n):
            fid_i, syms_i = per_file_syms[i]
            for j in range(i + 1, n):
                fid_j, syms_j = per_file_syms[j]
                # fid_i != fid_j guaranteed since they come from different entries
                for si in syms_i:
                    for sj in syms_j:
                        key = (min(si, sj), max(si, sj))
                        pair_count[key] += 1

    return pair_count


def _get_direct_edge_set(conn):
    """Return a set of (sym_lo, sym_hi) for all direct edges."""
    rows = conn.execute("SELECT source_id, target_id FROM edges").fetchall()
    edge_set = set()
    for r in rows:
        lo = min(r["source_id"], r["target_id"])
        hi = max(r["source_id"], r["target_id"])
        edge_set.add((lo, hi))
    return edge_set


def _load_symbol_info(conn, sym_ids):
    """Load symbol metadata for a set of IDs.

    Returns dict[sym_id] -> {name, kind, file_path, line_start, qualified_name}
    """
    if not sym_ids:
        return {}

    ph = ",".join("?" for _ in sym_ids)
    rows = conn.execute(f"""
        SELECT s.id, s.name, s.kind, s.qualified_name,
               s.line_start, f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.id IN ({ph})
    """, list(sym_ids)).fetchall()

    info = {}
    for r in rows:
        info[r["id"]] = {
            "name": r["name"],
            "kind": r["kind"],
            "qualified_name": r["qualified_name"],
            "line_start": r["line_start"],
            "file_path": r["file_path"],
        }
    return info


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command("fn-coupling")
@click.option('--min-count', default=3, type=int, show_default=True,
              help='Minimum co-change count to report')
@click.option('--limit', '-n', default=20, type=int, show_default=True,
              help='Maximum number of pairs to show')
@click.option('--include-connected', is_flag=True, default=False,
              help='Also show pairs that have a direct edge')
@click.pass_context
def fn_coupling(ctx, min_count, limit, include_connected):
    """Show function-level temporal coupling (hidden dependencies).

    Finds pairs of symbols in different files that frequently change
    together in commits but have NO direct edge (import/call) between them.
    These represent hidden dependencies that should either be made explicit
    or decoupled.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # Build the co-change matrix
        pair_counts = _build_symbol_cochange(conn)

        if not pair_counts:
            if json_mode:
                click.echo(to_json(json_envelope("fn-coupling",
                    summary={"pairs": 0, "error": "No git co-change data"},
                )))
            else:
                click.echo("No git co-change data available. "
                           "Run `roam index` on a git repository.")
            return

        # Filter by minimum count
        filtered = {k: v for k, v in pair_counts.items() if v >= min_count}

        if not filtered:
            if json_mode:
                click.echo(to_json(json_envelope("fn-coupling",
                    summary={"pairs": 0,
                             "note": f"No pairs with >= {min_count} co-changes"},
                )))
            else:
                click.echo(f"No symbol pairs with >= {min_count} co-changes "
                           f"across files. Try --min-count 2.")
            return

        # Get direct edges to separate hidden from connected
        edge_set = _get_direct_edge_set(conn)

        # Split into hidden vs connected
        hidden = []
        connected = []
        for (sa, sb), count in filtered.items():
            has_edge = (sa, sb) in edge_set
            entry = (sa, sb, count, has_edge)
            if has_edge:
                connected.append(entry)
            else:
                hidden.append(entry)

        # Sort both by count descending
        hidden.sort(key=lambda x: -x[2])
        connected.sort(key=lambda x: -x[2])

        # Build the results list
        if include_connected:
            results = hidden + connected
            results.sort(key=lambda x: -x[2])
        else:
            results = hidden

        results = results[:limit]

        if not results:
            if json_mode:
                click.echo(to_json(json_envelope("fn-coupling",
                    summary={"pairs": 0, "hidden": 0, "connected": len(connected)},
                )))
            else:
                click.echo(f"No hidden coupling found (all {len(connected)} "
                           f"co-changing pairs have direct edges).")
            return

        # Load symbol info for all referenced symbols
        all_ids = set()
        for sa, sb, _cnt, _edge in results:
            all_ids.add(sa)
            all_ids.add(sb)
        sym_info = _load_symbol_info(conn, all_ids)

        # --- JSON output ---
        if json_mode:
            pairs = []
            for sa, sb, count, has_edge in results:
                ia = sym_info.get(sa, {})
                ib = sym_info.get(sb, {})
                pairs.append({
                    "symbol_a": ia.get("qualified_name") or ia.get("name", f"sym_{sa}"),
                    "symbol_b": ib.get("qualified_name") or ib.get("name", f"sym_{sb}"),
                    "file_a": ia.get("file_path", ""),
                    "file_b": ib.get("file_path", ""),
                    "line_a": ia.get("line_start"),
                    "line_b": ib.get("line_start"),
                    "kind_a": ia.get("kind", ""),
                    "kind_b": ib.get("kind", ""),
                    "cochange_count": count,
                    "has_direct_edge": has_edge,
                })

            hidden_count = sum(1 for p in pairs if not p["has_direct_edge"])
            click.echo(to_json(json_envelope("fn-coupling",
                summary={
                    "pairs": len(pairs),
                    "hidden": hidden_count,
                    "connected": len(pairs) - hidden_count,
                    "min_count": min_count,
                },
                pairs=pairs,
            )))
            return

        # --- Text output ---
        click.echo("Function-level temporal coupling (hidden dependencies):\n")

        for sa, sb, count, has_edge in results:
            ia = sym_info.get(sa, {})
            ib = sym_info.get(sb, {})
            name_a = ia.get("name", f"sym_{sa}")
            name_b = ib.get("name", f"sym_{sb}")
            edge_label = "" if has_edge else " (NO direct edge)"

            click.echo(f"  {name_a} <-> {name_b}"
                       f"    co-changed {count} times{edge_label}")

            loc_a = loc(ia.get("file_path", "?"), ia.get("line_start"))
            loc_b = loc(ib.get("file_path", "?"), ib.get("line_start"))
            click.echo(f"    {loc_a}    {loc_b}")
            click.echo()

        total_hidden = len(hidden)
        total_connected = len(connected)
        shown = len(results)
        click.echo(f"Showing {shown} pairs"
                   f" | {total_hidden} hidden, {total_connected} connected"
                   f" (min co-changes: {min_count})")
