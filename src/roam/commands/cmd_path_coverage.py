"""Find critical paths through the call graph that have zero test protection."""

from __future__ import annotations

import fnmatch
from collections import deque

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import is_test_file


# ---------------------------------------------------------------------------
# Entry point discovery
# ---------------------------------------------------------------------------

def _find_entry_points(conn, from_pattern):
    """Find symbols with outgoing edges but no incoming edges (call graph roots).

    If from_pattern is provided, only symbols whose file path matches the
    fnmatch glob are included.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, f.path AS file_path, s.line_start "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.id IN (SELECT source_id FROM edges) "
        "AND s.id NOT IN (SELECT target_id FROM edges) "
        "ORDER BY f.path, s.line_start"
    ).fetchall()

    entries = []
    for r in rows:
        if from_pattern and not fnmatch.fnmatch(r["file_path"], from_pattern):
            continue
        entries.append({
            "id": r["id"],
            "name": r["name"],
            "kind": r["kind"],
            "file": r["file_path"],
            "line": r["line_start"] or 0,
        })
    return entries


# ---------------------------------------------------------------------------
# Sink discovery
# ---------------------------------------------------------------------------

def _find_sinks_from_effects(conn, to_pattern):
    """Find sink symbols from symbol_effects table (writes_db, network, filesystem)."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT se.symbol_id, s.name, s.kind, f.path AS file_path, "
            "       s.line_start, se.effect_type "
            "FROM symbol_effects se "
            "JOIN symbols s ON se.symbol_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE se.source = 'direct' "
            "AND se.effect_type IN ('writes_db', 'network', 'filesystem')"
        ).fetchall()
    except Exception:
        return {}, {}

    sink_ids = {}
    sink_effects = {}
    for r in rows:
        if to_pattern and not fnmatch.fnmatch(r["file_path"], to_pattern):
            continue
        sid = r["symbol_id"]
        sink_ids[sid] = {
            "id": sid,
            "name": r["name"],
            "kind": r["kind"],
            "file": r["file_path"],
            "line": r["line_start"] or 0,
        }
        # Keep strongest effect type per symbol
        sink_effects[sid] = r["effect_type"]

    return sink_ids, sink_effects


def _find_sinks_fallback(conn, to_pattern):
    """Fallback: leaf nodes with incoming but no outgoing edges."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, f.path AS file_path, s.line_start "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.id IN (SELECT target_id FROM edges) "
        "AND s.id NOT IN (SELECT source_id FROM edges) "
        "ORDER BY f.path, s.line_start"
    ).fetchall()

    sink_ids = {}
    for r in rows:
        if to_pattern and not fnmatch.fnmatch(r["file_path"], to_pattern):
            continue
        sink_ids[r["id"]] = {
            "id": r["id"],
            "name": r["name"],
            "kind": r["kind"],
            "file": r["file_path"],
            "line": r["line_start"] or 0,
        }
    return sink_ids


# ---------------------------------------------------------------------------
# BFS path finding
# ---------------------------------------------------------------------------

def _find_paths(conn, entry_id, sink_ids, max_depth):
    """BFS from entry_id, return list of paths (as node ID lists) that reach any sink."""
    paths = []
    queue = deque()
    queue.append((entry_id, [entry_id]))
    visited = {entry_id}

    while queue:
        current, path = queue.popleft()
        if len(path) > max_depth:
            continue
        if current in sink_ids and len(path) > 1:
            paths.append(path)
            continue  # don't continue traversal past a sink

        callees = conn.execute(
            "SELECT target_id FROM edges WHERE source_id = ?",
            (current,),
        ).fetchall()
        for row in callees:
            tid = row["target_id"]
            if tid not in visited:
                visited.add(tid)
                queue.append((tid, path + [tid]))

    return paths


# ---------------------------------------------------------------------------
# Test coverage check
# ---------------------------------------------------------------------------

def _build_tested_set(conn):
    """Return a set of symbol IDs that are directly called by test code."""
    tested = set()

    # Symbols that live in test files are inherently test symbols
    test_file_rows = conn.execute(
        "SELECT f.id, f.path FROM files f"
    ).fetchall()
    test_file_ids = {
        r["id"] for r in test_file_rows if is_test_file(r["path"])
    }

    if not test_file_ids:
        return tested

    # All symbols that are targets of edges originating from test symbols
    for file_id in test_file_ids:
        rows = conn.execute(
            "SELECT e.target_id "
            "FROM edges e "
            "JOIN symbols s ON e.source_id = s.id "
            "WHERE s.file_id = ?",
            (file_id,),
        ).fetchall()
        for r in rows:
            tested.add(r["target_id"])

    # Also include symbols that live inside test files themselves
    for file_id in test_file_ids:
        rows = conn.execute(
            "SELECT id FROM symbols WHERE file_id = ?",
            (file_id,),
        ).fetchall()
        for r in rows:
            tested.add(r["id"])

    return tested


# ---------------------------------------------------------------------------
# Path risk classification
# ---------------------------------------------------------------------------

_DESTRUCTIVE_EFFECTS = {"writes_db"}


def _classify_risk(path_ids, tested_set, sink_effects):
    """Return a risk label for a single path.

    CRITICAL  — zero tested nodes AND sink has destructive effect (writes_db)
    HIGH      — zero tested nodes
    MEDIUM    — only entry point tested, rest untested
    LOW       — most nodes tested
    """
    tested_count = sum(1 for nid in path_ids if nid in tested_set)
    total = len(path_ids)
    sink_id = path_ids[-1]
    sink_effect = sink_effects.get(sink_id, "")

    if tested_count == 0:
        if sink_effect in _DESTRUCTIVE_EFFECTS:
            return "CRITICAL"
        return "HIGH"
    if tested_count == 1 and path_ids[0] in tested_set and total > 1:
        return "MEDIUM"
    ratio = tested_count / total
    if ratio >= 0.5:
        return "LOW"
    return "MEDIUM"


# ---------------------------------------------------------------------------
# Greedy set cover: optimal test insertion points
# ---------------------------------------------------------------------------

def _suggest_test_points(untested_paths, tested_set):
    """Return an ordered list of symbols to test for maximum path coverage.

    Uses greedy set cover: at each step pick the node that covers the most
    currently-uncovered paths.
    """
    if not untested_paths:
        return []

    # Count how many untested paths each node (that is itself untested) appears in
    node_path_count = {}
    for path in untested_paths:
        for nid in path:
            if nid not in tested_set:
                node_path_count[nid] = node_path_count.get(nid, 0) + 1

    covered = set()
    suggestions = []
    remaining_paths = list(range(len(untested_paths)))

    while remaining_paths and node_path_count:
        # Pick node covering most remaining uncovered paths
        best_node = max(node_path_count, key=lambda n: node_path_count[n])
        best_count = node_path_count[best_node]
        if best_count == 0:
            break

        suggestions.append((best_node, best_count))

        # Remove paths covered by best_node
        new_remaining = []
        newly_covered = set()
        for pi in remaining_paths:
            path = untested_paths[pi]
            if best_node in path:
                newly_covered.add(pi)
                # Also mark all untested nodes in that path as covering fewer paths
            else:
                new_remaining.append(pi)
        remaining_paths = new_remaining

        # Update counts: remove covered paths contribution
        for pi in newly_covered:
            path = untested_paths[pi]
            for nid in path:
                if nid in node_path_count:
                    node_path_count[nid] = max(0, node_path_count[nid] - 1)

        # Remove the chosen node so it isn't selected again
        del node_path_count[best_node]

    return suggestions


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("path-coverage")
@click.option("--from", "from_pattern", default=None,
              help="Glob to filter entry points by file path (e.g. 'api/*').")
@click.option("--to", "to_pattern", default=None,
              help="Glob to filter sinks by file path (e.g. 'db*').")
@click.option("--max-depth", default=8, show_default=True,
              help="Maximum BFS depth for path search.")
@click.pass_context
def path_coverage(ctx, from_pattern, to_pattern, max_depth):
    """Find critical untested paths from entry points to sensitive sinks.

    Traces call graph paths from entry points (functions with no callers) to
    sensitive sinks (functions with side effects like DB writes or network I/O)
    and identifies which paths have zero test coverage.  Suggests the minimal
    set of test insertion points that would cover the most untested paths.

    \b
    Examples:
      roam path-coverage
      roam path-coverage --from "api/*"
      roam path-coverage --to "db*"
      roam path-coverage --max-depth 5
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # --- 1. Entry points ---
        entries = _find_entry_points(conn, from_pattern)

        # --- 2. Sinks ---
        sink_info, sink_effects = _find_sinks_from_effects(conn, to_pattern)
        if not sink_info:
            # Fallback: use leaf nodes
            sink_info = _find_sinks_fallback(conn, to_pattern)
            sink_effects = {}

        if not entries or not sink_info:
            _no_paths_output(
                json_mode,
                len(entries),
                len(sink_info),
                from_pattern,
                to_pattern,
            )
            return

        sink_id_set = set(sink_info.keys())

        # --- 3. BFS path search ---
        all_paths = []
        for entry in entries:
            paths = _find_paths(conn, entry["id"], sink_id_set, max_depth)
            all_paths.extend(paths)

        # Deduplicate paths (same node sequence)
        seen_paths = set()
        unique_paths = []
        for p in all_paths:
            key = tuple(p)
            if key not in seen_paths:
                seen_paths.add(key)
                unique_paths.append(p)

        if not unique_paths:
            _no_paths_output(
                json_mode,
                len(entries),
                len(sink_info),
                from_pattern,
                to_pattern,
            )
            return

        # --- 4. Test coverage check ---
        tested_set = _build_tested_set(conn)

        # --- 5. Score and classify paths ---
        # Build symbol info lookup for all nodes in paths
        all_node_ids = set()
        for path in unique_paths:
            all_node_ids.update(path)

        sym_info = {}
        for nid in all_node_ids:
            row = conn.execute(
                "SELECT s.id, s.name, s.kind, f.path AS file_path, s.line_start "
                "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
                (nid,),
            ).fetchone()
            if row:
                sym_info[nid] = {
                    "id": row["id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "file": row["file_path"],
                    "line": row["line_start"] or 0,
                }

        classified_paths = []
        untested_paths_for_cover = []

        for path_ids in unique_paths:
            risk = _classify_risk(path_ids, tested_set, sink_effects)
            tested_in_path = [nid for nid in path_ids if nid in tested_set]
            tested_count = len(tested_in_path)
            sink_id = path_ids[-1]
            sink_effect = sink_effects.get(sink_id, "")

            nodes = []
            for nid in path_ids:
                info = sym_info.get(nid, {})
                nodes.append({
                    "id": nid,
                    "name": info.get("name", "?"),
                    "kind": info.get("kind", "?"),
                    "file": info.get("file", "?"),
                    "line": info.get("line", 0),
                    "tested": nid in tested_set,
                })

            classified_paths.append({
                "risk": risk,
                "nodes": nodes,
                "tested_count": tested_count,
                "total_count": len(path_ids),
                "sink_effect": sink_effect,
                "path_ids": path_ids,
            })

            if tested_count == 0:
                untested_paths_for_cover.append(path_ids)

        # Sort: CRITICAL first, then HIGH, MEDIUM, LOW
        _RISK_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        classified_paths.sort(key=lambda p: _RISK_ORDER.get(p["risk"], 4))

        # --- 6. Suggest test insertion points ---
        suggestion_ids = _suggest_test_points(untested_paths_for_cover, tested_set)

        suggestions = []
        for nid, paths_covered in suggestion_ids[:10]:
            info = sym_info.get(nid, {})
            suggestions.append({
                "symbol": info.get("name", "?"),
                "file": info.get("file", "?"),
                "line": info.get("line", 0),
                "paths_covered": paths_covered,
            })

        # --- Summary counts ---
        total_paths = len(classified_paths)
        counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for cp in classified_paths:
            counts[cp["risk"]] = counts.get(cp["risk"], 0) + 1

        untested_paths_count = counts["CRITICAL"] + counts["HIGH"]

        critical_high = counts["CRITICAL"] + counts["HIGH"]
        if counts["CRITICAL"] > 0:
            verdict = (
                f"{counts['CRITICAL']} critical path{'s' if counts['CRITICAL'] != 1 else ''} "
                f"with zero test coverage"
            )
        elif counts["HIGH"] > 0:
            verdict = (
                f"{counts['HIGH']} high-risk path{'s' if counts['HIGH'] != 1 else ''} "
                f"with zero test coverage"
            )
        elif critical_high == 0 and total_paths > 0:
            verdict = f"{total_paths} path{'s' if total_paths != 1 else ''} found, all partially tested"
        else:
            verdict = f"{total_paths} path{'s' if total_paths != 1 else ''} found"

        # --- Output ---
        if json_mode:
            # Strip internal path_ids before serialising
            paths_clean = []
            for cp in classified_paths:
                paths_clean.append({k: v for k, v in cp.items() if k != "path_ids"})

            click.echo(to_json(json_envelope(
                "path-coverage",
                summary={
                    "verdict": verdict,
                    "total_paths": total_paths,
                    "untested_paths": untested_paths_count,
                    "critical": counts["CRITICAL"],
                    "high": counts["HIGH"],
                },
                paths=paths_clean,
                suggestions=suggestions,
                entry_points_found=len(entries),
                sinks_found=len(sink_info),
            )))
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        if not classified_paths:
            click.echo("No entry-to-sink paths found in the call graph.")
            return

        display_paths = classified_paths[:20]
        for i, cp in enumerate(display_paths, 1):
            tested_str = f"{cp['tested_count']}/{cp['total_count']} nodes tested"
            click.echo(f"PATH {i} [{cp['risk']}]:")
            for j, node in enumerate(cp["nodes"]):
                tested_label = "TESTED" if node["tested"] else "UNTESTED"
                kind_abbr = abbrev_kind(node["kind"])
                location = loc(node["file"], node["line"])
                sink_label = ""
                if j == len(cp["nodes"]) - 1 and cp["sink_effect"]:
                    sink_label = f", sink: {cp['sink_effect']}"
                if j == 0:
                    click.echo(f"  {kind_abbr} {node['name']}  {location}  ({tested_label}{sink_label})")
                else:
                    click.echo(f"  -> {kind_abbr} {node['name']}  {location}  ({tested_label}{sink_label})")
            click.echo(f"  Risk: {tested_str}")
            click.echo()

        if len(classified_paths) > 20:
            click.echo(f"(+{len(classified_paths) - 20} more paths)")
            click.echo()

        if suggestions:
            click.echo("OPTIMAL TEST POINTS:")
            for rank, s in enumerate(suggestions, 1):
                click.echo(
                    f"  {rank}. {s['symbol']} ({s['file']}:{s['line']}) "
                    f"-- covers {s['paths_covered']} untested path{'s' if s['paths_covered'] != 1 else ''}"
                )
        else:
            click.echo("No test insertion suggestions (all paths have some coverage).")


def _no_paths_output(json_mode, entry_count, sink_count, from_pattern, to_pattern):
    """Emit a graceful message when no entry→sink paths can be found."""
    if from_pattern or to_pattern:
        note = "No paths found matching the specified filters."
    elif entry_count == 0:
        note = "No entry points found (no functions with outgoing but no incoming edges)."
    elif sink_count == 0:
        note = "No sinks found (no functions with side effects or leaf nodes)."
    else:
        note = "No paths found between entry points and sinks."

    verdict = "no critical paths found"

    if json_mode:
        click.echo(to_json(json_envelope(
            "path-coverage",
            summary={
                "verdict": verdict,
                "total_paths": 0,
                "untested_paths": 0,
                "critical": 0,
                "high": 0,
            },
            paths=[],
            suggestions=[],
            entry_points_found=entry_count,
            sinks_found=sink_count,
            note=note,
        )))
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo()
        click.echo(note)
