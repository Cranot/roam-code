"""Show unreferenced exported symbols (dead code)."""

import os
from collections import defaultdict

import click

from roam.db.connection import open_db
from roam.db.queries import UNREFERENCED_EXPORTS
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


_ENTRY_NAMES = {
    # Generic entry points
    "main", "app", "serve", "server", "setup", "run", "cli",
    "handler", "middleware", "route", "index", "init",
    "register", "boot", "start", "execute", "configure",
    "command", "worker", "job", "task", "listener",
    # Vue lifecycle hooks
    "mounted", "created", "beforeMount", "beforeDestroy",
    "beforeCreate", "activated", "deactivated",
    "onMounted", "onUnmounted", "onBeforeMount", "onBeforeUnmount",
    "onActivated", "onDeactivated", "onUpdated", "onBeforeUpdate",
    # React lifecycle
    "componentDidMount", "componentWillUnmount", "componentDidUpdate",
    # Angular lifecycle
    "ngOnInit", "ngOnDestroy", "ngOnChanges", "ngAfterViewInit",
    # Test lifecycle
    "setUp", "tearDown", "beforeEach", "afterEach", "beforeAll", "afterAll",
}
_ENTRY_FILE_BASES = {"server", "app", "main", "cli", "index", "manage",
                      "boot", "bootstrap", "start", "entry", "worker"}
_API_PREFIXES = ("get", "use", "create", "validate", "fetch", "update",
                 "delete", "find", "check", "make", "build", "parse")


def _dead_action(r, file_imported):
    """Compute actionable verdict for a dead symbol."""
    name = r["name"]
    name_lower = name.lower()
    base = os.path.basename(r["file_path"]).lower()
    name_no_ext = os.path.splitext(base)[0]

    # Entry point / lifecycle hooks (check original case for camelCase hooks)
    if name in _ENTRY_NAMES or name_lower in _ENTRY_NAMES:
        return "INTENTIONAL"

    # Python dunders — always intentional
    if name.startswith("__") and name.endswith("__"):
        return "INTENTIONAL"

    # File is an entry point and not imported — symbols here are likely intentional
    if not file_imported and name_no_ext in _ENTRY_FILE_BASES:
        return "INTENTIONAL"

    # API naming → review before deleting
    if any(name_lower.startswith(p) for p in _API_PREFIXES):
        return "REVIEW"

    # Barrel/index file → likely re-exported for public API
    if base.startswith("index.") or base == "__init__.py":
        return "REVIEW"

    return "SAFE"


# ---------------------------------------------------------------------------
# Dead cluster detection
# ---------------------------------------------------------------------------

def _find_dead_clusters(conn, dead_ids):
    """Find connected components of dead-only symbols.

    Given a set of dead symbol IDs, build a subgraph of edges where both
    endpoints are dead, then find connected components of size >= 2.
    """
    if not dead_ids:
        return []

    dead_set = set(dead_ids)
    ph = ",".join("?" for _ in dead_set)

    # Edges where both source and target are dead
    edges = conn.execute(
        f"""SELECT source_id, target_id FROM edges
            WHERE source_id IN ({ph}) AND target_id IN ({ph})""",
        list(dead_set) + list(dead_set),
    ).fetchall()

    # Build adjacency (undirected for component finding)
    adj = defaultdict(set)
    for e in edges:
        adj[e["source_id"]].add(e["target_id"])
        adj[e["target_id"]].add(e["source_id"])

    # BFS to find components
    visited = set()
    clusters = []
    for node in adj:
        if node in visited:
            continue
        component = set()
        queue = [node]
        while queue:
            n = queue.pop()
            if n in visited:
                continue
            visited.add(n)
            component.add(n)
            for nb in adj[n]:
                if nb not in visited:
                    queue.append(nb)
        if len(component) >= 2:
            clusters.append(component)

    clusters.sort(key=lambda c: -len(c))
    return clusters


# ---------------------------------------------------------------------------
# Extinction prediction
# ---------------------------------------------------------------------------

def _predict_extinction(conn, target_name):
    """Predict what becomes dead if symbol X is deleted.

    Algorithm:
    1. Find symbol X's ID
    2. Find all symbols that call/reference X (callers of X)
    3. For each caller: check if X is their ONLY callee. If so, they become orphaned.
    4. Recursively propagate: if removing X orphans Y, check Y's callers too.
    5. Return the full cascade.
    """
    from roam.commands.resolve import find_symbol

    sym = find_symbol(conn, target_name)
    if sym is None:
        return None, []

    target_id = sym["id"]

    # Pre-compute out-degree (callee count) for all symbols
    out_deg = {}
    for r in conn.execute("SELECT source_id, COUNT(*) as cnt FROM edges GROUP BY source_id").fetchall():
        out_deg[r["source_id"]] = r["cnt"]

    # Pre-compute callers for any symbol
    def get_callers(sid):
        return [r["source_id"] for r in conn.execute(
            "SELECT source_id FROM edges WHERE target_id = ?", (sid,)
        ).fetchall()]

    # BFS cascade
    cascade = []
    removed = {target_id}
    queue = [target_id]

    while queue:
        current = queue.pop(0)
        callers = get_callers(current)
        for caller_id in callers:
            if caller_id in removed:
                continue
            # Check remaining out-degree after removing all removed targets
            remaining = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE source_id = ? AND target_id NOT IN ({})".format(
                    ",".join("?" for _ in removed)
                ),
                [caller_id] + list(removed),
            ).fetchone()[0]
            if remaining == 0:
                # This caller has no remaining callees → orphaned
                removed.add(caller_id)
                queue.append(caller_id)
                # Get name info for the cascade item
                info = conn.execute(
                    "SELECT s.name, s.kind, f.path as file_path, s.line_start "
                    "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
                    (caller_id,),
                ).fetchone()
                if info:
                    cascade.append({
                        "name": info["name"],
                        "kind": info["kind"],
                        "location": loc(info["file_path"], info["line_start"]),
                        "reason": f"only callees removed",
                    })

    return sym, cascade


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------

def _group_dead(dead_items, by):
    """Group dead items by directory or kind."""
    groups = defaultdict(list)
    for item in dead_items:
        if by == "directory":
            key = os.path.dirname(item["file_path"]).replace("\\", "/") or "."
        elif by == "kind":
            key = item["kind"]
        else:
            key = "all"
        groups[key].append(item)
    return sorted(groups.items(), key=lambda x: -len(x[1]))


# ---------------------------------------------------------------------------
# Core dead code analysis (shared between modes)
# ---------------------------------------------------------------------------

def _analyze_dead(conn):
    """Run the full dead code analysis.

    Returns (high, low, imported_files) where high/low are lists of Row objects.
    """
    rows = conn.execute(UNREFERENCED_EXPORTS).fetchall()
    if not rows:
        return [], [], set()

    imported_files = set()
    for r in conn.execute(
        "SELECT DISTINCT target_file_id FROM file_edges"
    ).fetchall():
        imported_files.add(r["target_file_id"])

    # Filter transitively alive (barrel re-exports)
    importers_of = {}
    for fe in conn.execute(
        "SELECT source_file_id, target_file_id FROM file_edges"
    ).fetchall():
        importers_of.setdefault(fe["target_file_id"], set()).add(fe["source_file_id"])

    transitively_alive = set()
    for r in rows:
        fid = r["file_id"]
        if fid not in imported_files:
            continue
        downstream = set()
        frontier = {fid}
        for _ in range(3):
            next_hop = set()
            for f in frontier:
                for imp_fid in importers_of.get(f, set()):
                    if imp_fid not in downstream:
                        downstream.add(imp_fid)
                        next_hop.add(imp_fid)
            frontier = next_hop
            if not frontier:
                break
        if not downstream:
            continue
        ph = ",".join("?" for _ in downstream)
        alive = conn.execute(
            f"""SELECT 1 FROM edges e
                JOIN symbols s ON e.target_id = s.id
                WHERE s.name = ?
                AND s.file_id IN ({ph})
                LIMIT 1""",
            [r["name"]] + list(downstream),
        ).fetchone()
        if alive:
            transitively_alive.add(r["id"])

    rows = [r for r in rows if r["id"] not in transitively_alive]

    high = [r for r in rows if r["file_id"] in imported_files]
    low = [r for r in rows if r["file_id"] not in imported_files]
    return high, low, imported_files


@click.command()
@click.option("--all", "show_all", is_flag=True, help="Include low-confidence results")
@click.option("--by-directory", "by_directory", is_flag=True,
              help="Group dead symbols by parent directory")
@click.option("--by-kind", "by_kind", is_flag=True,
              help="Group dead symbols by symbol kind")
@click.option("--summary", "summary_only", is_flag=True,
              help="Only show aggregate counts, no individual symbols")
@click.option("--clusters", "show_clusters", is_flag=True,
              help="Detect dead subgraphs (groups of dead symbols referencing only each other)")
@click.option("--extinction", "extinction_target", default=None,
              help="Predict what else becomes dead if you delete this symbol")
@click.pass_context
def dead(ctx, show_all, by_directory, by_kind, summary_only, show_clusters, extinction_target):
    """Show unreferenced exported symbols (dead code)."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # --- Extinction mode (separate flow) ---
        if extinction_target:
            sym, cascade = _predict_extinction(conn, extinction_target)
            if sym is None:
                if json_mode:
                    click.echo(to_json(json_envelope("dead",
                        summary={"error": f"Symbol not found: {extinction_target}"},
                    )))
                else:
                    click.echo(f"Symbol not found: {extinction_target}")
                return

            if json_mode:
                click.echo(to_json(json_envelope("dead",
                    summary={"extinction_cascade": len(cascade)},
                    mode="extinction",
                    target=extinction_target,
                    extinction_cascade=cascade,
                )))
            else:
                click.echo(f"=== Extinction Cascade for: {extinction_target} ===\n")
                if cascade:
                    click.echo(f"Deleting {extinction_target} would orphan {len(cascade)} symbol(s):\n")
                    table_rows = []
                    for c in cascade:
                        table_rows.append([
                            c["name"], abbrev_kind(c["kind"]),
                            c["location"], c["reason"],
                        ])
                    click.echo(format_table(
                        ["Name", "Kind", "Location", "Reason"],
                        table_rows,
                    ))
                else:
                    click.echo("No additional symbols would become dead.")
            return

        # --- Standard dead code analysis ---
        high, low, imported_files = _analyze_dead(conn)
        all_items = high + low

        if not all_items:
            if json_mode:
                click.echo(to_json(json_envelope("dead",
                    summary={"safe": 0, "review": 0, "intentional": 0},
                    high_confidence=[], low_confidence=[],
                )))
            else:
                click.echo("=== Unreferenced Exports (0) ===")
                click.echo("  (none -- all exports are referenced)")
            return

        # Compute action verdicts
        all_dead = [(r, _dead_action(r, r["file_id"] in imported_files)) for r in all_items]
        n_safe = sum(1 for _, a in all_dead if a == "SAFE")
        n_review = sum(1 for _, a in all_dead if a == "REVIEW")
        n_intent = sum(1 for _, a in all_dead if a == "INTENTIONAL")

        # --- Cluster detection ---
        clusters_data = []
        if show_clusters:
            dead_ids = {r["id"] for r in all_items}
            raw_clusters = _find_dead_clusters(conn, dead_ids)
            id_to_info = {}
            if raw_clusters:
                all_cluster_ids = set()
                for c in raw_clusters:
                    all_cluster_ids.update(c)
                ph = ",".join("?" for _ in all_cluster_ids)
                for r in conn.execute(
                    f"SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start "
                    f"FROM symbols s JOIN files f ON s.file_id = f.id "
                    f"WHERE s.id IN ({ph})",
                    list(all_cluster_ids),
                ).fetchall():
                    id_to_info[r["id"]] = r

            for cluster_set in raw_clusters:
                syms = []
                for sid in sorted(cluster_set):
                    info = id_to_info.get(sid)
                    if info:
                        syms.append({
                            "name": info["name"],
                            "kind": info["kind"],
                            "location": loc(info["file_path"], info["line_start"]),
                        })
                clusters_data.append({"size": len(cluster_set), "symbols": syms})

        # --- Grouping ---
        group_by = None
        groups_data = []
        if by_directory:
            group_by = "directory"
        elif by_kind:
            group_by = "kind"

        if group_by:
            grouped = _group_dead(all_items, group_by)
            for key, items in grouped:
                verdicts = [_dead_action(r, r["file_id"] in imported_files) for r in items]
                groups_data.append({
                    "key": key,
                    "count": len(items),
                    "safe": sum(1 for v in verdicts if v == "SAFE"),
                    "review": sum(1 for v in verdicts if v == "REVIEW"),
                    "intentional": sum(1 for v in verdicts if v == "INTENTIONAL"),
                })

        # --- JSON output ---
        if json_mode:
            envelope = json_envelope("dead",
                summary={"safe": n_safe, "review": n_review, "intentional": n_intent},
                high_confidence=[
                    {"name": r["name"], "kind": r["kind"],
                     "location": loc(r["file_path"], r["line_start"]),
                     "action": _dead_action(r, True)}
                    for r in high
                ],
                low_confidence=[
                    {"name": r["name"], "kind": r["kind"],
                     "location": loc(r["file_path"], r["line_start"]),
                     "action": _dead_action(r, False)}
                    for r in low
                ],
            )
            if group_by:
                envelope["grouping"] = group_by
                envelope["groups"] = groups_data
            if show_clusters:
                envelope["dead_clusters"] = clusters_data
            click.echo(to_json(envelope))
            return

        # --- Text: summary-only mode ---
        if summary_only:
            click.echo(f"Dead exports: {len(all_items)} "
                        f"({n_safe} safe, {n_review} review, {n_intent} intentional)")
            if group_by and groups_data:
                click.echo(f"\nBy {group_by}:")
                for g in groups_data[:20]:
                    click.echo(f"  {g['key']:<50s}  {g['count']:>3d}  "
                                f"(safe={g['safe']}, review={g['review']})")
            if show_clusters and clusters_data:
                click.echo(f"\nDead clusters: {len(clusters_data)}")
                for i, cl in enumerate(clusters_data[:10], 1):
                    names = ", ".join(s["name"] for s in cl["symbols"][:5])
                    more = f" +{cl['size'] - 5}" if cl["size"] > 5 else ""
                    click.echo(f"  cluster {i} ({cl['size']} syms): {names}{more}")
            return

        # --- Text: grouped mode ---
        if group_by and groups_data:
            click.echo(f"=== Unreferenced Exports by {group_by} ({len(all_items)} total) ===")
            click.echo(f"  Actions: {n_safe} safe to delete, {n_review} need review, "
                        f"{n_intent} likely intentional\n")
            table_rows = []
            for g in groups_data:
                table_rows.append([
                    g["key"],
                    str(g["count"]),
                    str(g["safe"]),
                    str(g["review"]),
                    str(g["intentional"]),
                ])
            click.echo(format_table(
                [group_by.title(), "Total", "Safe", "Review", "Intentional"],
                table_rows,
                budget=30,
            ))
            if show_clusters and clusters_data:
                click.echo(f"\n=== Dead Clusters ({len(clusters_data)}) ===")
                for i, cl in enumerate(clusters_data[:10], 1):
                    names = ", ".join(s["name"] for s in cl["symbols"][:6])
                    more = f" +{cl['size'] - 6}" if cl["size"] > 6 else ""
                    click.echo(f"  cluster {i} ({cl['size']} syms): {names}{more}")
            return

        # --- Text: standard output ---
        click.echo(f"=== Unreferenced Exports ({len(high)} high confidence, {len(low)} low) ===")
        click.echo(f"  Actions: {n_safe} safe to delete, {n_review} need review, "
                    f"{n_intent} likely intentional")
        click.echo()

        # Build imported-by lookup for high-confidence results
        if high:
            high_file_ids = {r["file_id"] for r in high}
            ph = ",".join("?" for _ in high_file_ids)
            importer_rows = conn.execute(
                f"SELECT fe.target_file_id, f.path "
                f"FROM file_edges fe JOIN files f ON fe.source_file_id = f.id "
                f"WHERE fe.target_file_id IN ({ph})",
                list(high_file_ids),
            ).fetchall()
            importers_by_file = {}
            for ir in importer_rows:
                importers_by_file.setdefault(ir["target_file_id"], []).append(ir["path"])

            # Count how many other exported symbols in the same file ARE referenced
            referenced_counts = {}
            for fid in high_file_ids:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM symbols s "
                    "WHERE s.file_id = ? AND s.is_exported = 1 "
                    "AND s.id IN (SELECT target_id FROM edges)",
                    (fid,),
                ).fetchone()[0]
                referenced_counts[fid] = cnt

            click.echo(f"-- High confidence ({len(high)}) --")
            click.echo("(file is imported but symbol has no references)")
            table_rows = []
            for r in high:
                imp_list = importers_by_file.get(r["file_id"], [])
                n_importers = len(imp_list)
                n_siblings = referenced_counts.get(r["file_id"], 0)
                if n_siblings > 0:
                    reason = f"{n_importers} importers use {n_siblings} siblings, skip this"
                else:
                    reason = f"{n_importers} importers, none use any export"
                action = _dead_action(r, True)
                table_rows.append([
                    action,
                    r["name"],
                    abbrev_kind(r["kind"]),
                    loc(r["file_path"], r["line_start"]),
                    reason,
                ])
            click.echo(format_table(
                ["Action", "Name", "Kind", "Location", "Reason"],
                table_rows,
                budget=50,
            ))

        if show_all and low:
            click.echo(f"\n-- Low confidence ({len(low)}) --")
            click.echo("(file has no importers — may be entry point or used by unparsed files)")
            table_rows = []
            for r in low:
                action = _dead_action(r, False)
                table_rows.append([
                    action,
                    r["name"],
                    abbrev_kind(r["kind"]),
                    loc(r["file_path"], r["line_start"]),
                ])
            click.echo(format_table(
                ["Action", "Name", "Kind", "Location"],
                table_rows,
                budget=50,
            ))
        elif low:
            click.echo(f"\n({len(low)} low-confidence results hidden — use --all to show)")

        # Dead clusters
        if show_clusters and clusters_data:
            click.echo(f"\n=== Dead Clusters ({len(clusters_data)}) ===")
            click.echo("(groups of dead symbols that only reference each other)")
            for i, cl in enumerate(clusters_data[:10], 1):
                names = ", ".join(s["name"] for s in cl["symbols"][:6])
                more = f" +{cl['size'] - 6}" if cl["size"] > 6 else ""
                click.echo(f"  cluster {i} ({cl['size']} syms): {names}{more}")
                for s in cl["symbols"][:6]:
                    click.echo(f"    {abbrev_kind(s['kind'])}  {s['name']}  {s['location']}")
            if len(clusters_data) > 10:
                click.echo(f"  (+{len(clusters_data) - 10} more clusters)")

        # Check for files with no extracted symbols
        unparsed = conn.execute(
            "SELECT COUNT(*) FROM files f "
            "WHERE NOT EXISTS (SELECT 1 FROM symbols s WHERE s.file_id = f.id)"
        ).fetchone()[0]
        if unparsed:
            click.echo(f"\nNote: {unparsed} files had no symbols extracted (may cause false positives)")
