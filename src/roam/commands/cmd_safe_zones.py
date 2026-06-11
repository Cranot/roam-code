"""Identify safe refactoring boundaries for a symbol or file.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because safe-zones is a validator-not-detector: its output is
a single-input boundary verdict (which symbols / regions are safe to
refactor without breaking callers) for one target per invocation, not
a codebase-wide scan. SARIF consumers expect a corpus of per-finding
results at file:line coordinates; safe-zones returns one boundary
decision per invocation. See ``cmd_safe_delete`` / ``cmd_syntax_check``
for the parallel validator-not-detector disclosure pattern (W1192) +
action.yml _SUPPORTED_SARIF allowlist + W1224-audit memo.
"""

from __future__ import annotations

from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.commands.graph_helpers import bfs_nx
from roam.commands.resolve import ensure_index, find_symbol, resolve_file_symbols
from roam.db.connection import batched_in, open_db
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)


def _resolve_file_symbols(conn, target):
    """Return ``(file_id, sym_ids, tier)`` for a file-path-like target.

    Pattern-1 Variant D Wave C shim (audit reference:
    ``(internal memo)``). Delegates to
    :func:`roam.commands.resolve.resolve_file_symbols` so the silent
    LIKE %name substring fallback is surfaced via a tier discriminator
    rather than collapsed into the same shape as an exact-path match.

    Returns a 3-tuple ``(file_id, sym_ids, tier)``:

    - ``file_id``: resolved ``files.id`` (or ``None`` on miss).
    - ``sym_ids``: ``set[int]`` of symbol ids owned by ``file_id``
      (empty on miss OR on a file indexed with zero symbols).
    - ``tier``: ``"file"`` (exact match), ``"file_substring"`` (LIKE
      fallback), or ``None`` (no match). Pass directly to
      :func:`roam.output.formatter.resolution_disclosure` so callers
      flip ``partial_success: true`` on the substring path.

    Pre-Wave-C this helper returned a 2-tuple ``(file_id, sym_ids)``
    and silently collapsed both tiers — the canonical Variant D failure
    shape.
    """
    file_id, sym_ids, _fpath, tier = resolve_file_symbols(conn, target)
    if file_id is None:
        return None, None, None
    return file_id, sym_ids, tier


def _classify_zone(boundary_count):
    """Return (zone_label, zone_description)."""
    if boundary_count == 0:
        return "ISOLATED", "no external connections -- safe to refactor freely"
    if boundary_count <= 5:
        return (
            "CONTAINED",
            f"{boundary_count} boundary symbols -- refactor with minor API contract awareness",
        )
    return "EXPOSED", f"{boundary_count} boundary symbols -- refactor carefully, many consumers"


@roam_capability(
    name="safe-zones",
    category="architecture",
    summary="Identify safe refactoring boundaries for a symbol or file",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("safe-zones")
@click.argument("target")
@click.option(
    "--depth",
    default=5,
    type=int,
    show_default=True,
    help="Max BFS depth for propagation analysis.",
)
@click.pass_context
def safe_zones(ctx, target, depth):
    """Identify safe refactoring boundaries for a symbol or file.

    Answers: if I refactor TARGET, what is the containment boundary?
    How far can changes propagate?

    TARGET is a symbol name (or file:symbol) or a file path.

    Unlike ``impact`` (which traces unlimited reverse dependents for blast radius) and
    ``closure`` (which identifies exact locations needing modification), this command maps
    a bounded containment zone around a symbol, classifying nodes as strictly internal or
    boundary.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # --- Resolve target to seed symbol IDs ---
        seed_ids: set[int] = set()
        target_label = target
        # W1245 Pattern-2 variant-D resolution-state tracking. File-target
        # branch stays implicit ``symbol`` (file-path is the user's
        # intended primary type, not a fallback); symbol branch reads
        # the ``_resolution_tier`` stamp post-W1249.
        resolution_tier: str = "symbol"

        # Try as file first
        file_id, file_syms, file_tier = _resolve_file_symbols(conn, target)
        if file_tier is not None:
            # Pattern-1 Variant D Wave C: ``file_tier`` distinguishes
            # an exact-path resolution (``"file"``) from a degraded
            # LIKE %name substring fallback (``"file_substring"``).
            # An empty ``file_syms`` set on a successful ``file`` tier
            # is a valid resolved-but-empty shape (file indexed with
            # zero symbols); gate this branch on tier rather than
            # truthy-syms so the resolution stays tier-disclosable.
            resolution_tier = file_tier
            seed_ids = file_syms or set()
            # Fetch the canonical path for display
            frow = conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
            if frow:
                target_label = frow["path"]
        else:
            # Try as symbol
            sym = find_symbol(conn, target)
            if sym is None:
                if json_mode:
                    # W1245 Pattern-2 variant-D: structured unresolved
                    # envelope so MCP consumers see the same shape as
                    # the resolved branches.
                    unresolved_disclosure = resolution_disclosure("unresolved", target=target)
                    click.echo(
                        to_json(
                            json_envelope(
                                "safe-zones",
                                summary={
                                    "verdict": f"Symbol or file not found: {target}",
                                    "partial_success": True,
                                    "state": "not_found",
                                    "internal_size": 0,
                                    "boundary_size": 0,
                                    **unresolved_disclosure,
                                },
                                internal_zone=[],
                                boundary=[],
                                **unresolved_disclosure,
                            )
                        )
                    )
                    raise SystemExit(1)
                click.echo(f"Symbol or file not found: {target}")
                raise SystemExit(1)
            # W1245 \ W1249 Pattern-2 variant-D: ``find_symbol`` stamps
            # ``_resolution_tier`` on the returned row.
            resolution_tier = sym.get("_resolution_tier", "symbol")
            seed_ids = {sym["id"]}
            target_label = sym["qualified_name"] or sym["name"]

        # --- Build graph ---
        try:
            from roam.graph.builder import build_symbol_graph
        except ImportError:
            click.echo("Graph module not available. Run `roam index` first.")
            return

        G = build_symbol_graph(conn)

        # Filter seed_ids to nodes present in the graph
        seed_ids = {s for s in seed_ids if s in G}
        if not seed_ids:
            verdict = "Target symbol(s) not found in the dependency graph."
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "safe-zones",
                            summary={"verdict": verdict, "internal_size": 0, "boundary_size": 0},
                            internal_zone=[],
                            boundary=[],
                        )
                    )
                )
            else:
                click.echo(verdict)
            return

        # --- BFS forward (callees / downstream) and backward (callers / upstream) ---
        forward = bfs_nx(G, seed_ids, depth, direction="forward")
        backward = bfs_nx(G, seed_ids, depth, direction="backward")

        # Internal zone = union of forward and backward, including seeds
        internal_ids = set(forward.keys()) | set(backward.keys())

        # --- Identify boundary symbols ---
        # A boundary symbol is an internal node that has at least one
        # neighbor outside the internal zone.
        boundary_ids: set[int] = set()
        external_caller_count: defaultdict[int, int] = defaultdict(int)  # boundary_id -> count of external callers
        external_callee_count: defaultdict[int, int] = defaultdict(int)  # boundary_id -> count of external callees

        for nid in internal_ids:
            if nid not in G:
                continue
            # Check outgoing edges to external nodes
            for succ in G.successors(nid):
                if succ not in internal_ids:
                    boundary_ids.add(nid)
                    external_callee_count[nid] += 1
            # Check incoming edges from external nodes
            for pred in G.predecessors(nid):
                if pred not in internal_ids:
                    boundary_ids.add(nid)
                    external_caller_count[nid] += 1

        # Strictly internal = internal minus boundary
        strictly_internal_ids = internal_ids - boundary_ids

        # --- Classify ---
        zone_label, zone_desc = _classify_zone(len(boundary_ids))

        # --- Gather node details from DB for display ---
        all_display_ids = internal_ids | boundary_ids
        if not all_display_ids:
            click.echo("No reachable symbols found.")
            return

        detail_rows = batched_in(
            conn,
            "SELECT s.id, s.name, s.kind, s.qualified_name, s.line_start, "
            "f.path as file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.id IN ({ph})",
            list(all_display_ids),
        )

        detail_map: dict[int, dict] = {}
        for r in detail_rows:
            detail_map[r["id"]] = {
                "id": r["id"],
                "name": r["name"],
                "kind": r["kind"],
                "qualified_name": r["qualified_name"],
                "line_start": r["line_start"],
                "file_path": r["file_path"],
            }

        # --- Collect affected files ---
        affected_files: set[str] = set()
        for nid in internal_ids:
            info = detail_map.get(nid)
            if info:
                affected_files.add(info["file_path"])

        # --- External ref counts for boundary symbols ---
        # Total external references (callers from outside the zone)
        boundary_external_refs: dict[int, int] = {}
        for bid in boundary_ids:
            ext_callers = external_caller_count.get(bid, 0)
            ext_callees = external_callee_count.get(bid, 0)
            boundary_external_refs[bid] = ext_callers + ext_callees

        # --- Build verdict ---
        verdict = (
            f"{zone_label}: {len(strictly_internal_ids)} internal, {len(boundary_ids)} boundary symbols "
            f"in {len(affected_files)} file{'s' if len(affected_files) != 1 else ''}"
        )
        # W1245 Pattern-2 variant-D: suffix the verdict when the symbol-
        # target resolver succeeded through a degraded tier so LAW-6
        # single-line consumers still see the disclosure. Pattern-1
        # Variant D Wave C adds the ``file_substring`` case: distinct
        # from the exact-``file`` tier so agents can tell a substring
        # LIKE-fallback match from a fully-resolved exact-path success.
        if resolution_tier == "fuzzy":
            verdict = f"{verdict} [fuzzy resolution]"
        elif resolution_tier == "file_substring":
            verdict = f"{verdict} [file substring match]"
        disclosure = resolution_disclosure(resolution_tier, target=target_label)

        # --- JSON output ---
        if json_mode:
            internal_list = []
            for nid in sorted(strictly_internal_ids):
                info = detail_map.get(nid)
                if not info:
                    continue
                internal_list.append(
                    {
                        "name": info["name"],
                        "kind": abbrev_kind(info["kind"]),
                        "file": info["file_path"],
                        "line": info["line_start"],
                    }
                )

            boundary_list = []
            for nid in sorted(boundary_ids, key=lambda b: boundary_external_refs.get(b, 0), reverse=True):
                info = detail_map.get(nid)
                if not info:
                    continue
                boundary_list.append(
                    {
                        "name": info["name"],
                        "kind": abbrev_kind(info["kind"]),
                        "file": info["file_path"],
                        "line": info["line_start"],
                        "external_callers": external_caller_count.get(nid, 0),
                        "external_callees": external_callee_count.get(nid, 0),
                        "external_refs": boundary_external_refs.get(nid, 0),
                    }
                )

            click.echo(
                to_json(
                    json_envelope(
                        "safe-zones",
                        summary={
                            "verdict": verdict,
                            "zone": zone_label,
                            "internal_symbols": len(strictly_internal_ids),
                            "boundary_symbols": len(boundary_ids),
                            "total_symbols": len(internal_ids),
                            "affected_files": len(affected_files),
                            # W1245 Pattern-2 variant-D resolution disclosure.
                            # Filters helper ``target`` to avoid clobbering the
                            # explicit ``target=target_label`` kwarg below.
                            **{k: v for k, v in disclosure.items() if k != "target"},
                        },
                        target=target_label,
                        depth=depth,
                        zone=zone_label,
                        zone_description=zone_desc,
                        internal_symbols=len(strictly_internal_ids),
                        boundary_symbols=len(boundary_ids),
                        total_symbols=len(internal_ids),
                        affected_files=sorted(affected_files),
                        internal=internal_list,
                        boundary=boundary_list,
                        # W1245 Pattern-2 variant-D resolution disclosure at
                        # top level. ``target`` filtered to avoid collision
                        # with the explicit ``target=target_label`` kwarg.
                        **{k: v for k, v in disclosure.items() if k != "target"},
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}\n")
        click.echo(f"Safe zone analysis for `{target_label}`:\n")
        click.echo(f"Zone: {zone_label} ({zone_desc})\n")

        # Internal symbols
        if strictly_internal_ids:
            click.echo("Internal (safe to change freely):")
            int_rows = []
            for nid in sorted(strictly_internal_ids):
                info = detail_map.get(nid)
                if not info:
                    continue
                int_rows.append(
                    [
                        info["name"],
                        abbrev_kind(info["kind"]),
                        loc(info["file_path"], info["line_start"]),
                    ]
                )
            click.echo(format_table(["name", "kind", "location"], int_rows, budget=20))
            click.echo()

        # Boundary symbols
        if boundary_ids:
            click.echo("Boundary (maintain contracts):")
            bnd_rows = []
            for nid in sorted(boundary_ids, key=lambda b: boundary_external_refs.get(b, 0), reverse=True):
                info = detail_map.get(nid)
                if not info:
                    continue
                ext_c = external_caller_count.get(nid, 0)
                ext_e = external_callee_count.get(nid, 0)
                refs_parts = []
                if ext_c:
                    refs_parts.append(f"{ext_c} caller{'s' if ext_c != 1 else ''}")
                if ext_e:
                    refs_parts.append(f"{ext_e} callee{'s' if ext_e != 1 else ''}")
                refs_label = ", ".join(refs_parts) if refs_parts else "0 external refs"
                bnd_rows.append(
                    [
                        info["name"],
                        abbrev_kind(info["kind"]),
                        loc(info["file_path"], info["line_start"]),
                        f"({refs_label})",
                    ]
                )
            click.echo(
                format_table(
                    ["name", "kind", "location", "external refs"],
                    bnd_rows,
                    budget=20,
                )
            )
            click.echo()

        # Blast radius summary
        click.echo(
            f"Blast radius: {len(internal_ids)} symbols in "
            f"{len(affected_files)} file{'s' if len(affected_files) != 1 else ''}"
            f" (contained to {', '.join(sorted(affected_files)[:5])}"
            f"{'...' if len(affected_files) > 5 else ''})"
        )
