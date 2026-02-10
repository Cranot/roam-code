"""Show unreferenced exported symbols (dead code)."""

import os

import click

from roam.db.connection import open_db
from roam.db.queries import UNREFERENCED_EXPORTS
from roam.output.formatter import abbrev_kind, loc, format_table, to_json
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


@click.command()
@click.option("--all", "show_all", is_flag=True, help="Include low-confidence results")
@click.pass_context
def dead(ctx, show_all):
    """Show unreferenced exported symbols (dead code)."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    with open_db(readonly=True) as conn:
        rows = conn.execute(UNREFERENCED_EXPORTS).fetchall()

        if not rows:
            if json_mode:
                click.echo(to_json({"high_confidence": [], "low_confidence": []}))
            else:
                click.echo("=== Unreferenced Exports (0) ===")
                click.echo("  (none -- all exports are referenced)")
            return

        # Split by confidence: file is imported (high) vs not imported (low)
        imported_files = set()
        for r in conn.execute(
            "SELECT DISTINCT target_file_id FROM file_edges"
        ).fetchall():
            imported_files.add(r["target_file_id"])

        # Filter out symbols consumed transitively:
        # Build importer graph for multi-hop barrel export resolution.
        # If a same-named symbol in any downstream file (up to 3 hops through
        # file_edges) has incoming edges, the export is alive.
        importers_of: dict = {}  # file_id -> set of file_ids that import from it
        for fe in conn.execute(
            "SELECT source_file_id, target_file_id FROM file_edges"
        ).fetchall():
            importers_of.setdefault(fe["target_file_id"], set()).add(fe["source_file_id"])

        transitively_alive = set()
        for r in rows:
            fid = r["file_id"]
            if fid not in imported_files:
                continue
            # Collect downstream files up to 3 hops (handles barrel re-exports)
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

        # Get file_id for each dead symbol
        high = []
        low = []
        for r in rows:
            file_id = r["file_id"]
            if file_id in imported_files:
                high.append(r)
            else:
                low.append(r)

        # Compute action verdicts for all dead symbols
        all_dead = [(r, _dead_action(r, r["file_id"] in imported_files)) for r in high + low]
        n_safe = sum(1 for _, a in all_dead if a == "SAFE")
        n_review = sum(1 for _, a in all_dead if a == "REVIEW")
        n_intent = sum(1 for _, a in all_dead if a == "INTENTIONAL")

        if json_mode:
            click.echo(to_json({
                "summary": {"safe": n_safe, "review": n_review, "intentional": n_intent},
                "high_confidence": [
                    {"name": r["name"], "kind": r["kind"],
                     "location": loc(r["file_path"], r["line_start"]),
                     "action": _dead_action(r, True)}
                    for r in high
                ],
                "low_confidence": [
                    {"name": r["name"], "kind": r["kind"],
                     "location": loc(r["file_path"], r["line_start"]),
                     "action": _dead_action(r, False)}
                    for r in low
                ],
            }))
            return

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
            importers_by_file: dict = {}
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

        # Check for files with no extracted symbols
        unparsed = conn.execute(
            "SELECT COUNT(*) FROM files f "
            "WHERE NOT EXISTS (SELECT 1 FROM symbols s WHERE s.file_id = f.id)"
        ).fetchone()[0]
        if unparsed:
            click.echo(f"\nNote: {unparsed} files had no symbols extracted (may cause false positives)")
