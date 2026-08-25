"""Find critical paths through the call graph that have zero test protection.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because path-coverage outputs are invocation-scoped untested-
path enumerations (call-graph chains from entry points with zero
tested-by edges) — not per-location code violations. The output
describes "absence of test edges" along a path, not a defect at a
source coordinate; SARIF audiences scan for per-finding rule_id +
region rows rather than diff-scoped coverage rollups. See ``cmd_test_gaps``
for the parallel absence-of-edge disclosure pattern (W1230) +
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation
plan + W1224-audit memo.
"""

from __future__ import annotations

import ast
import sqlite3
from collections import Counter, deque
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, open_db
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json
from roam.output.risk import risk_rank

# ---------------------------------------------------------------------------
# Entry point discovery
# ---------------------------------------------------------------------------


def _find_entry_points(conn, from_pattern):
    """Find symbols with outgoing edges but no incoming edges (call graph roots).

    If from_pattern is provided, only symbols whose file path matches the
    gitignore-style glob are included.
    """
    from roam.index.gitignore import matches_gitignore

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
        if from_pattern and not matches_gitignore(r["file_path"], from_pattern):
            continue
        entries.append(
            {
                "id": r["id"],
                "name": r["name"],
                "kind": r["kind"],
                "file": r["file_path"],
                "line": r["line_start"] or 0,
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Sink discovery
# ---------------------------------------------------------------------------


def _find_sinks_from_effects(conn, to_pattern):
    """Find sink symbols from symbol_effects table (writes_db, network, filesystem)."""
    from roam.index.gitignore import matches_gitignore

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
    except sqlite3.OperationalError:
        return {}, {}

    sink_ids = {}
    sink_effects = {}
    for r in rows:
        if to_pattern and not matches_gitignore(r["file_path"], to_pattern):
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
    from roam.index.gitignore import matches_gitignore

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
        if to_pattern and not matches_gitignore(r["file_path"], to_pattern):
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


def _build_adjacency(conn):
    """Pre-build a forward adjacency dict from the edges table."""
    adj: dict[int, list[int]] = {}
    rows = conn.execute("SELECT source_id, target_id FROM edges").fetchall()
    for r in rows:
        adj.setdefault(r["source_id"], []).append(r["target_id"])
    return adj


def _find_paths(adj, entry_id, sink_ids, max_depth):
    """BFS from entry_id for paths (node ID lists) that reach any sink.

    Returns ``(paths, pruned)`` where ``pruned`` is the number of queue
    entries that were actually discarded at the ``max_depth`` bound. A
    non-zero ``pruned`` means the walk stopped with unexplored frontier
    left over, so the returned list is a lower bound on what a deeper
    walk would find — callers must disclose that rather than publish the
    count as a finished scan.

    ``pruned`` is deliberately keyed on a real discard, not on graph
    depth: a graph deeper than ``max_depth`` whose every branch already
    terminated at a sink (or at an already-visited node) inside the bound
    discards nothing and reports ``0``.

    NOTE: ``pruned == 0`` does NOT mean the enumeration is complete.
    ``visited`` is shared across branches of one entry's walk, so a sink
    reachable by several distinct routes is recorded once; this function
    under-counts paths even when unbounded. No caller may turn
    ``pruned == 0`` into a completeness claim.
    """
    paths = []
    pruned = 0
    queue = deque()
    queue.append((entry_id, [entry_id]))
    visited = {entry_id}

    while queue:
        current, path = queue.popleft()
        if len(path) > max_depth:
            pruned += 1
            continue
        if current in sink_ids and len(path) > 1:
            paths.append(path)
            continue  # don't continue traversal past a sink

        for tid in adj.get(current, []):
            if tid not in visited:
                visited.add(tid)
                queue.append((tid, path + [tid]))

    return paths, pruned


# ---------------------------------------------------------------------------
# Test coverage check
# ---------------------------------------------------------------------------


def _build_tested_set(conn):
    """Return a set of symbol IDs that are directly called by test code."""
    tested = set()

    # Symbols that live in test files are inherently test symbols
    test_file_rows = conn.execute("SELECT f.id, f.path FROM files f").fetchall()
    test_file_ids = {r["id"] for r in test_file_rows if is_test_file(r["path"])}

    if not test_file_ids:
        return tested

    test_file_id_list = list(test_file_ids)

    # All symbols that are targets of edges originating from test symbols (batched)
    for r in batched_in(
        conn,
        "SELECT e.target_id FROM edges e JOIN symbols s ON e.source_id = s.id WHERE s.file_id IN ({ph})",
        test_file_id_list,
    ):
        tested.add(r["target_id"])

    # Also include symbols that live inside test files themselves (batched)
    for r in batched_in(
        conn,
        "SELECT id FROM symbols WHERE file_id IN ({ph})",
        test_file_id_list,
    ):
        tested.add(r["id"])

    # The index may retain only the module import edge for a direct assertion
    # such as ``assert helpers._normalise(x) == y``. Parse test calls once and
    # map their qualified attribute names back to production symbols.
    production_by_name: dict[str, set[int]] = {}
    for row in conn.execute("SELECT s.id, s.name FROM symbols s JOIN files f ON s.file_id = f.id"):
        production_by_name.setdefault(row["name"], set()).add(row["id"])
    paths_by_id = {row["id"]: row["path"] for row in test_file_rows}
    for file_id in test_file_ids:
        try:
            tree = ast.parse(Path(paths_by_id[file_id]).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        aliases = {
            alias.asname or alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            func = call.func
            if not isinstance(func, ast.Attribute):
                continue
            root = func.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in aliases:
                tested.update(production_by_name.get(func.attr, ()))

    return tested


# ---------------------------------------------------------------------------
# Path risk classification
# ---------------------------------------------------------------------------

_DESTRUCTIVE_EFFECTS = {"writes_db"}


def _classify_risk(path_ids, tested_set, sink_effects):
    """Return a risk label for a single path.

    W718: risk labels are lowercase canonical roam vocabulary (W547):

    * ``critical``  — zero tested nodes AND sink has destructive
      effect (writes_db).
    * ``high``      — zero tested nodes.
    * ``medium``    — only entry point tested, rest untested.
    * ``low``       — most nodes tested.

    Pre-W718 the labels were UPPER-cased; the W631 ``risk_rank`` sort
    is case-insensitive so both spellings sort identically.
    """
    tested_count = sum(1 for nid in path_ids if nid in tested_set)
    total = len(path_ids)
    sink_id = path_ids[-1]
    sink_effect = sink_effects.get(sink_id, "")

    if tested_count == 0:
        if sink_effect in _DESTRUCTIVE_EFFECTS:
            return "critical"
        return "high"
    if tested_count == 1 and path_ids[0] in tested_set and total > 1:
        return "medium"
    ratio = tested_count / total
    if ratio >= 0.5:
        return "low"
    return "medium"


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
    node_path_count = Counter(nid for path in untested_paths for nid in path if nid not in tested_set)

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


@roam_capability(
    name="path-coverage",
    category="reports",
    summary="Find critical untested paths from entry points to sensitive sinks",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("path-coverage")
@click.option(
    "--from",
    "from_pattern",
    default=None,
    help="Glob to filter entry points by file path (e.g. 'api/*').",
)
@click.option("--to", "to_pattern", default=None, help="Glob to filter sinks by file path (e.g. 'db*').")
@click.option("--max-depth", default=8, show_default=True, help="Maximum BFS depth for path search.")
@click.pass_context
def path_coverage(ctx, from_pattern, to_pattern, max_depth):
    """Find critical untested paths from entry points to sensitive sinks.

    Unlike ``coverage-gaps`` (which finds unprotected entry points), this
    command traces full call paths from entry points to sensitive sinks and
    identifies untested paths.

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
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
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
                token_budget=token_budget,
            )
            return

        sink_id_set = set(sink_info.keys())

        # --- 3. BFS path search ---
        adj = _build_adjacency(conn)
        all_paths = []
        pruned_at_depth = 0
        for entry in entries:
            paths, pruned = _find_paths(adj, entry["id"], sink_id_set, max_depth)
            pruned_at_depth += pruned
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
                token_budget=token_budget,
                pruned_at_depth=pruned_at_depth,
                max_depth=max_depth,
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
        for row in batched_in(
            conn,
            "SELECT s.id, s.name, s.kind, f.path AS file_path, s.line_start "
            "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
            list(all_node_ids),
        ):
            sym_info[row["id"]] = {
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
                nodes.append(
                    {
                        "id": nid,
                        "name": info.get("name", "?"),
                        "kind": info.get("kind", "?"),
                        "file": info.get("file", "?"),
                        "line": info.get("line", 0),
                        "tested": nid in tested_set,
                    }
                )

            classified_paths.append(
                {
                    "risk": risk,
                    "nodes": nodes,
                    "tested_count": tested_count,
                    "total_count": len(path_ids),
                    "sink_effect": sink_effect,
                    "path_ids": path_ids,
                }
            )

            if tested_count == 0:
                untested_paths_for_cover.append(path_ids)

        # Sort: critical first, then high, medium, low (W631 + W718).
        # risk_rank polarity is "higher = worse" (critical=4 ... low=1,
        # unknown=-1). Negate for the pre-W631 "lower = worse" sort key
        # polarity; unknown maps to ``-(-1) = 1`` so it sorts AFTER low,
        # matching the pre-W631 default of 4.
        classified_paths.sort(key=lambda p: -risk_rank(p["risk"]))

        # --- 6. Suggest test insertion points ---
        suggestion_ids = _suggest_test_points(untested_paths_for_cover, tested_set)

        suggestions = []
        for nid, paths_covered in suggestion_ids[:10]:
            info = sym_info.get(nid, {})
            suggestions.append(
                {
                    "symbol": info.get("name", "?"),
                    "file": info.get("file", "?"),
                    "line": info.get("line", 0),
                    "paths_covered": paths_covered,
                }
            )

        # --- Summary counts ---
        # W718: bucket keys are lowercase canonical risk labels (W547).
        # Per-path ``risk`` values are already canonical lowercase
        # (see :func:`_classify_risk`); the ``.lower()`` call is
        # defensive so legacy callers that hand-roll UPPER-cased risk
        # strings still bucket correctly.
        total_paths = len(classified_paths)
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for cp in classified_paths:
            key = str(cp["risk"]).strip().lower()
            # W756: fail-loud on unknown risk labels (Pattern 1 variant D).
            # `_classify_risk()` returns a closed lowercase enum
            # ({critical,high,medium,low}); any other key is a producer-
            # contract violation we want to surface, not silently bucket
            # into a new dict slot that downstream verdict math ignores.
            if key not in counts:
                raise ValueError(f"unknown risk label: {key!r} (expected one of {sorted(counts)})")
            counts[key] += 1

        untested_paths_count = counts["critical"] + counts["high"]

        critical_high = counts["critical"] + counts["high"]
        if counts["critical"] > 0:
            verdict = (
                f"{counts['critical']} critical path{'s' if counts['critical'] != 1 else ''} with zero test coverage"
            )
        elif counts["high"] > 0:
            verdict = f"{counts['high']} high-risk path{'s' if counts['high'] != 1 else ''} with zero test coverage"
        elif critical_high == 0 and total_paths > 0:
            verdict = f"{total_paths} path{'s' if total_paths != 1 else ''} found, all partially tested"
        else:
            verdict = f"{total_paths} path{'s' if total_paths != 1 else ''} found"

        # W1528: the BFS bound is part of the measurement, so it has to be
        # part of the claim. When the walk actually discarded frontier at
        # ``--max-depth`` the published counts are a lower bound, and LAW 6
        # requires the verdict to say so without any other field.
        #
        # W1331: named for the canonical disclosure token rather than for the
        # counter behind it. Both output branches below have to spell this
        # signal the same way, or neither a reader diffing them nor the
        # scanner can tell that the text branch discloses it at all --
        # exactly the ignore-drift shape fixed in c49b1312. The value is the
        # same condition both branches already tested, one via the counter
        # and one via a note string that is non-empty precisely when the
        # counter is non-zero.
        scan_incomplete = bool(pruned_at_depth)
        truncation_note = ""
        if scan_incomplete:
            verdict += (
                f"; {pruned_at_depth} branch{'es' if pruned_at_depth != 1 else ''} pruned at --max-depth {max_depth}"
            )
            truncation_note = (
                f"NOTE: {pruned_at_depth} branch{'es' if pruned_at_depth != 1 else ''} "
                f"pruned at --max-depth {max_depth}; longer entry->sink paths were not "
                f"enumerated. Re-run with a larger --max-depth to search further."
            )

        # --- Output ---
        if json_mode:
            # Strip internal path_ids before serialising
            paths_clean = []
            for cp in classified_paths:
                paths_clean.append({k: v for k, v in cp.items() if k != "path_ids"})

            summary = {
                "verdict": verdict,
                "total_paths": total_paths,
                "untested_paths": untested_paths_count,
                "critical": counts["critical"],
                "high": counts["high"],
                # Echo the effective bound so the counts above are
                # self-describing rather than needing the invocation.
                "max_depth": max_depth,
                "pruned_at_max_depth": pruned_at_depth,
            }
            if scan_incomplete:
                summary["partial_success"] = True
                summary["scan_incomplete"] = True
                summary["incomplete_reasons"] = ["max_depth"]

            click.echo(
                to_json(
                    json_envelope(
                        "path-coverage",
                        summary=summary,
                        budget=token_budget,
                        paths=paths_clean,
                        suggestions=suggestions,
                        entry_points_found=len(entries),
                        sinks_found=len(sink_info),
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        if scan_incomplete:
            click.echo(truncation_note)
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


def _no_paths_output(
    json_mode,
    entry_count,
    sink_count,
    from_pattern,
    to_pattern,
    token_budget=0,
    pruned_at_depth=0,
    max_depth=None,
):
    """Emit a graceful message when no entry→sink paths can be found.

    W807 Pattern-2 empty-corpus disclosure: the verdict line distinguishes
    the degenerate causes (no entries, no sinks, no paths) so machine
    consumers don't confuse a degenerate-graph "0 paths" with a fully-
    scanned "0 critical paths found". ``partial_success`` is True + the
    ``state`` field surfaces a closed-enum disclosure when the degeneracy
    is on the graph side (no entries / no sinks). LAW 6 — verdict reads
    without any other field. LAW 4 anchored on ``paths``/``entries``/
    ``sinks`` concrete-noun terminals.

    W1528 splits the old catch-all ``no_paths_connecting`` in two, because
    a BFS that ran out of depth measured nothing about connectivity:

    * ``no_paths_connecting``     — the walk completed and found nothing
      connecting; ``partial_success`` stays False.
    * ``no_paths_within_max_depth`` — the walk discarded frontier at the
      ``--max-depth`` bound, so "no paths" is unproven;
      ``partial_success`` is True and the note names the escape hatch.
    """
    # Pick a verdict + state that names which degenerate axis fired.
    #
    # W1528: the truncation branch is tested FIRST because it is the only
    # one that says the walk did not finish. "Filter narrowed to 0 paths"
    # and "no paths connecting" are both clean-scan claims; a walk that
    # discarded frontier at the bound has not earned either of them, even
    # when a filter was also in play.
    # W1331: one boolean, spelled as the disclosure token itself, feeding both
    # the text verdict/note below and the JSON summary key. The two branches
    # used to test the raw counter independently, which is how a spelling
    # drift between them goes unnoticed.
    scan_incomplete = bool(pruned_at_depth)

    if scan_incomplete:
        # The walk did not complete — it hit the depth bound and threw
        # frontier away. "No paths connecting" would be a claim the
        # traversal never earned, so this is its own state and it is NOT
        # a clean scan.
        note = (
            f"No paths found within --max-depth {max_depth}, but "
            f"{pruned_at_depth} branch{'es' if pruned_at_depth != 1 else ''} "
            f"were pruned at that bound and never searched. "
            f"Re-run with a larger --max-depth to search further."
        )
        verdict = (
            f"0 paths found within --max-depth {max_depth}; "
            f"{pruned_at_depth} branch{'es' if pruned_at_depth != 1 else ''} pruned unsearched"
        )
        state = "no_paths_within_max_depth"
        partial_success = True
    elif from_pattern or to_pattern:
        # NOT a degenerate corpus — a clean scan with a narrow filter —
        # so it stays partial_success: False.
        note = "No paths found matching the specified filters."
        verdict = "Filter narrowed to 0 paths"
        state = "no_paths_matching_filters"
        partial_success = False
    elif entry_count == 0:
        note = "No entry points found (no functions with outgoing but no incoming edges)."
        verdict = "Corpus empty: 0 entries"
        state = "no_entry_points"
        partial_success = True
    elif sink_count == 0:
        note = "No sinks found (no functions with side effects or leaf nodes)."
        verdict = "Corpus empty: 0 paths"
        state = "no_sinks"
        partial_success = True
    else:
        note = "No paths found between entry points and sinks."
        verdict = f"No paths found between {entry_count} entries and {sink_count} sinks"
        state = "no_paths_connecting"
        partial_success = False

    summary = {
        "verdict": verdict,
        "state": state,
        "partial_success": partial_success,
        "total_paths": 0,
        "untested_paths": 0,
        "critical": 0,
        "high": 0,
        "pruned_at_max_depth": pruned_at_depth,
    }
    if max_depth is not None:
        summary["max_depth"] = max_depth
    if scan_incomplete:
        summary["scan_incomplete"] = True
        summary["incomplete_reasons"] = ["max_depth"]

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "path-coverage",
                    summary=summary,
                    budget=token_budget,
                    paths=[],
                    suggestions=[],
                    entry_points_found=entry_count,
                    sinks_found=sink_count,
                    note=note,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo()
        click.echo(note)
