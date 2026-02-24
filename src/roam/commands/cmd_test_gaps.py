"""Find changed symbols that lack test coverage."""

from __future__ import annotations

import os
from collections import deque

import click

from roam.coverage_reports import load_symbol_coverage_map
from roam.db.connection import open_db, find_project_root, batched_in
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import (
    get_changed_files,
    resolve_changed_to_db,
    is_test_file,
)


_MAX_HOPS = 10


# ---------------------------------------------------------------------------
# Reverse-edge BFS to find test-file callers
# ---------------------------------------------------------------------------

def _bfs_to_test_files(conn, start_ids):
    """Walk reverse edges from *start_ids* up to _MAX_HOPS.

    Returns a dict ``{start_id: [(test_file_path, test_symbol_name, hops)]}``
    mapping each starting symbol to the test files that (transitively) call it.
    """
    # BFS state
    visited = {}  # symbol_id -> (hops, set_of_origin_start_ids)
    queue = deque()  # (symbol_id, hops, origin_start_id)

    for sid in start_ids:
        visited[sid] = (0, {sid})
        queue.append((sid, 0, sid))

    while queue:
        current_id, hops, origin = queue.popleft()
        if hops >= _MAX_HOPS:
            continue

        callers = conn.execute(
            "SELECT e.source_id, s.name, s.file_id "
            "FROM edges e "
            "JOIN symbols s ON e.source_id = s.id "
            "WHERE e.target_id = ?",
            (current_id,),
        ).fetchall()

        for row in callers:
            caller_id = row["source_id"]
            new_hops = hops + 1

            if caller_id not in visited:
                visited[caller_id] = (new_hops, {origin})
                queue.append((caller_id, new_hops, origin))
            else:
                old_hops, origins = visited[caller_id]
                if origin not in origins:
                    origins.add(origin)
                    # Re-enqueue only if we haven't explored deeper from here
                    if new_hops <= old_hops:
                        queue.append((caller_id, new_hops, origin))

    return visited


def _find_test_coverage(conn, symbol_ids):
    """For each symbol in *symbol_ids*, find test files that reference it.

    Returns:
        covered: dict  {symbol_id: [test_file_path, ...]}
        all callers reachable: the BFS visited dict
    """
    if not symbol_ids:
        return {}

    reachable = _bfs_to_test_files(conn, symbol_ids)

    # Collect all reachable symbol IDs (excluding the starting set)
    caller_ids = [sid for sid in reachable if sid not in symbol_ids]
    if not caller_ids:
        return {}

    # Batch-fetch file paths for all callers
    caller_file_map = {}  # symbol_id -> file_path
    if caller_ids:
        rows = batched_in(
            conn,
            "SELECT s.id, f.path as file_path "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.id IN ({ph})",
            caller_ids,
        )
        for r in rows:
            caller_file_map[r["id"]] = r["file_path"]

    # Build coverage map: symbol_id -> set of test file paths
    coverage = {}  # symbol_id -> set(test_file_path)
    for caller_id, (hops, origins) in reachable.items():
        if caller_id in symbol_ids:
            continue
        fpath = caller_file_map.get(caller_id)
        if fpath and is_test_file(fpath):
            for origin_id in origins:
                if origin_id in symbol_ids:
                    coverage.setdefault(origin_id, set()).add(fpath)

    return coverage


# ---------------------------------------------------------------------------
# Stale test detection
# ---------------------------------------------------------------------------

def _detect_stale_tests(conn, symbol_ids, coverage_map):
    """Detect symbols whose test files have not been updated since the symbol changed.

    A test is considered "stale" when the source file's mtime is newer than
    the test file's mtime in the DB (indicating the source was modified more
    recently than the test).

    Returns a list of dicts with stale test info.
    """
    stale = []

    for sid, test_files in coverage_map.items():
        # Get the source file mtime for this symbol
        row = conn.execute(
            "SELECT f.path, f.mtime FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.id = ?",
            (sid,),
        ).fetchone()
        if not row or row["mtime"] is None:
            continue

        source_mtime = row["mtime"]
        source_path = row["path"]

        for tf in test_files:
            tf_row = conn.execute(
                "SELECT mtime FROM files WHERE path = ?", (tf,)
            ).fetchone()
            if tf_row and tf_row["mtime"] is not None:
                if source_mtime > tf_row["mtime"]:
                    stale.append({
                        "symbol_id": sid,
                        "source_file": source_path,
                        "test_file": tf,
                    })

    return stale


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _classify_severity(symbol_row, pagerank):
    """Classify a gap's severity based on visibility and PageRank.

    Returns 'high', 'medium', or 'low'.
    """
    is_private = (
        symbol_row["visibility"] == "private"
        or symbol_row["name"].startswith("_")
    )

    if is_private:
        return "low"

    # High: public symbol with notable PageRank
    if pagerank is not None and pagerank >= 0.005:
        return "high"

    return "medium"


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("test-gaps")
@click.argument("files", nargs=-1, required=False)
@click.option("--changed", is_flag=True,
              help="Use `git diff --name-only` to get changed files")
@click.option("--severity", "min_severity", default="medium",
              type=click.Choice(["high", "medium", "low"], case_sensitive=False),
              help="Minimum severity to report (default: medium)")
@click.pass_context
def test_gaps(ctx, files, changed, min_severity):
    """Map changed symbols to missing test coverage.

    Identifies which changed symbols lack test coverage by checking
    reverse dependency edges to test files.  Use --changed to analyze
    the current git diff, or pass specific file paths.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # Resolve target files
    target_paths = list(files) if files else []

    if changed:
        root = find_project_root()
        diff_files = get_changed_files(root)
        target_paths.extend(diff_files)

    if not target_paths:
        if json_mode:
            click.echo(to_json(json_envelope("test-gaps",
                summary={
                    "verdict": "No changed files to analyze",
                    "total_gaps": 0,
                },
                high_gaps=[],
                medium_gaps=[],
                low_gaps=[],
                stale_tests=[],
                recommendations=[],
            )))
        else:
            click.echo("VERDICT: No changed files to analyze")
            click.echo()
            click.echo("Provide file paths or use --changed to analyze git diff.")
        return

    min_sev_idx = _SEVERITY_ORDER.get(min_severity.lower(), 1)

    with open_db(readonly=True) as conn:
        # Resolve paths to DB file IDs
        file_map = resolve_changed_to_db(conn, target_paths)

        if not file_map:
            if json_mode:
                click.echo(to_json(json_envelope("test-gaps",
                    summary={
                        "verdict": "Changed files not found in index",
                        "total_gaps": 0,
                    },
                    high_gaps=[],
                    medium_gaps=[],
                    low_gaps=[],
                    stale_tests=[],
                    recommendations=[],
                )))
            else:
                click.echo("VERDICT: Changed files not found in index")
                click.echo()
                click.echo("Try `roam index` first.")
            return

        # Filter out test files from analysis targets
        source_file_map = {
            p: fid for p, fid in file_map.items()
            if not is_test_file(p)
        }

        if not source_file_map:
            if json_mode:
                click.echo(to_json(json_envelope("test-gaps",
                    summary={
                        "verdict": "All changed files are test files",
                        "total_gaps": 0,
                    },
                    high_gaps=[],
                    medium_gaps=[],
                    low_gaps=[],
                    stale_tests=[],
                    recommendations=[],
                )))
            else:
                click.echo("VERDICT: All changed files are test files")
            return

        # Get all symbols in the changed source files
        all_symbols = []
        for path, fid in source_file_map.items():
            rows = conn.execute(
                "SELECT s.id, s.name, s.kind, s.line_start, s.visibility, "
                "s.is_exported, s.parent_id, f.path as file_path "
                "FROM symbols s "
                "JOIN files f ON s.file_id = f.id "
                "WHERE s.file_id = ?",
                (fid,),
            ).fetchall()
            all_symbols.extend(rows)

        if not all_symbols:
            if json_mode:
                click.echo(to_json(json_envelope("test-gaps",
                    summary={
                        "verdict": "No symbols found in changed files",
                        "total_gaps": 0,
                    },
                    high_gaps=[],
                    medium_gaps=[],
                    low_gaps=[],
                    stale_tests=[],
                    recommendations=[],
                )))
            else:
                click.echo("VERDICT: No symbols found in changed files")
            return

        symbol_ids = {s["id"] for s in all_symbols}

        # Find test coverage via reverse-edge walk
        coverage_map = _find_test_coverage(conn, symbol_ids)
        imported_cov_map = load_symbol_coverage_map(conn, symbol_ids)
        imported_symbols = sum(
            1 for data in imported_cov_map.values()
            if (data.get("coverable_lines") or 0) > 0
        )

        # Fetch PageRank for all symbols (for severity classification)
        pagerank_map = {}
        sym_id_list = list(symbol_ids)
        if sym_id_list:
            pr_rows = batched_in(
                conn,
                "SELECT symbol_id, pagerank FROM graph_metrics "
                "WHERE symbol_id IN ({ph})",
                sym_id_list,
            )
            for r in pr_rows:
                pagerank_map[r["symbol_id"]] = r["pagerank"]

        # Detect stale tests
        stale_entries = _detect_stale_tests(conn, symbol_ids, coverage_map)

        # Build stale lookup: symbol_id -> set of stale test files
        stale_lookup = {}
        for entry in stale_entries:
            stale_lookup.setdefault(entry["symbol_id"], set()).add(entry["test_file"])

        # Classify gaps
        high_gaps = []
        medium_gaps = []
        low_gaps = []
        stale_tests = []
        actual_only = []
        predicted_only = []
        predicted_covered_symbols = 0
        actual_covered_symbols = 0

        for sym in all_symbols:
            sid = sym["id"]
            # Skip non-function/class/method kinds — we only care about callable symbols
            if sym["kind"] not in ("function", "method", "class"):
                continue

            test_files = coverage_map.get(sid, set())
            pr = pagerank_map.get(sid)
            imported = imported_cov_map.get(sid) or {}
            actual_cov_pct = imported.get("coverage_pct")
            actual_covered = imported.get("covered_lines") or 0
            actual_coverable = imported.get("coverable_lines") or 0
            has_actual_data = actual_coverable > 0
            has_actual_coverage = has_actual_data and actual_covered > 0

            if test_files:
                predicted_covered_symbols += 1
            if has_actual_coverage:
                actual_covered_symbols += 1

            # Coverage source disagreement tracking (for enrichment diagnostics)
            if test_files and has_actual_data and not has_actual_coverage:
                predicted_only.append({
                    "name": sym["name"],
                    "kind": sym["kind"],
                    "file": sym["file_path"],
                    "line": sym["line_start"],
                    "actual_coverage_pct": actual_cov_pct or 0.0,
                })
            elif (not test_files) and has_actual_coverage:
                actual_only.append({
                    "name": sym["name"],
                    "kind": sym["kind"],
                    "file": sym["file_path"],
                    "line": sym["line_start"],
                    "actual_coverage_pct": actual_cov_pct,
                })

            gap_required = (
                (not test_files and not has_actual_coverage)
                or (test_files and has_actual_data and not has_actual_coverage)
            )
            if gap_required:
                severity = _classify_severity(sym, pr)
                reason = (
                    "no tests found"
                    if not test_files
                    else "tests found in graph, but imported coverage is 0%"
                )
                gap_entry = {
                    "name": sym["name"],
                    "kind": sym["kind"],
                    "file": sym["file_path"],
                    "line": sym["line_start"],
                    "pagerank": round(pr, 4) if pr is not None else None,
                    "severity": severity,
                    "reason": reason,
                    "actual_coverage_pct": actual_cov_pct,
                }

                if severity == "high":
                    high_gaps.append(gap_entry)
                elif severity == "medium":
                    medium_gaps.append(gap_entry)
                else:
                    low_gaps.append(gap_entry)
                continue

            # Covered (predicted and/or actual) — stale test checks only
            if test_files:
                stale_files = stale_lookup.get(sid, set())
                for tf in stale_files:
                    stale_tests.append({
                        "name": sym["name"],
                        "kind": sym["kind"],
                        "file": sym["file_path"],
                        "line": sym["line_start"],
                        "test_file": tf,
                    })

        # Sort gaps by PageRank (descending), then name
        def _gap_sort_key(g):
            return (-(g["pagerank"] or 0), g["name"])

        high_gaps.sort(key=_gap_sort_key)
        medium_gaps.sort(key=_gap_sort_key)
        low_gaps.sort(key=_gap_sort_key)
        stale_tests.sort(key=lambda s: (s["file"], s["name"]))

        # Apply severity filter — include severities at or above the minimum.
        # Severity indices: high=0, medium=1, low=2.  A gap is included
        # when its severity index <= min_sev_idx.
        filtered_high = high_gaps  # high (0) is always <= any min
        filtered_medium = medium_gaps if min_sev_idx >= 1 else []
        filtered_low = low_gaps if min_sev_idx >= 2 else []
        total_gaps = len(filtered_high) + len(filtered_medium) + len(filtered_low)

        # Build recommendations
        recommendations = []
        if high_gaps:
            recommendations.append(
                f"Add tests for {len(high_gaps)} high-impact public symbols"
            )
        if medium_gaps:
            recommendations.append(
                f"Add tests for {len(medium_gaps)} public symbols"
            )
        if stale_tests:
            recommendations.append(
                f"Update {len(stale_tests)} stale test file(s)"
            )
        if predicted_only:
            recommendations.append(
                f"Investigate {len(predicted_only)} symbols with graph-predicted tests but 0% imported coverage"
            )
        if actual_only:
            recommendations.append(
                f"Review {len(actual_only)} symbols covered in imported reports but missing graph test links"
            )

        n_changed = len(source_file_map)
        actionable = len(high_gaps) + len(medium_gaps)
        verdict = (
            f"{total_gaps} test gaps found in {n_changed} changed file(s)"
        )

        # --- JSON output ---
        if json_mode:
            click.echo(to_json(json_envelope("test-gaps",
                summary={
                    "verdict": verdict,
                    "total_gaps": total_gaps,
                    "high": len(high_gaps),
                    "medium": len(medium_gaps),
                    "low": len(low_gaps),
                    "stale": len(stale_tests),
                    "files_analyzed": n_changed,
                    "predicted_covered_symbols": predicted_covered_symbols,
                    "actual_covered_symbols": actual_covered_symbols,
                    "imported_coverage_symbols": imported_symbols,
                    "actual_only_count": len(actual_only),
                    "predicted_only_count": len(predicted_only),
                },
                budget=token_budget,
                high_gaps=filtered_high,
                medium_gaps=filtered_medium,
                low_gaps=filtered_low,
                stale_tests=stale_tests,
                actual_only_covered=actual_only,
                predicted_only_covered=predicted_only,
                recommendations=recommendations,
            )))
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        if imported_symbols:
            click.echo(
                f"Coverage source blend: graph={predicted_covered_symbols} symbols, "
                f"imported={actual_covered_symbols} symbols (across {imported_symbols} symbols with line data)"
            )
        click.echo()

        if filtered_high:
            click.echo(f"HIGH (public + high-impact):")
            for g in filtered_high:
                pr_str = f" (PageRank {g['pagerank']:.4f})" if g["pagerank"] else ""
                click.echo(
                    f"  {abbrev_kind(g['kind'])} {g['name']} at "
                    f"{loc(g['file'], g['line'])} "
                    f"-- {g['reason']}{pr_str}"
                )
            click.echo()

        if filtered_medium:
            click.echo(f"MEDIUM (public):")
            for g in filtered_medium:
                click.echo(
                    f"  {abbrev_kind(g['kind'])} {g['name']} at "
                    f"{loc(g['file'], g['line'])} "
                    f"-- {g['reason']}"
                )
            click.echo()

        if filtered_low:
            click.echo(f"LOW (internal):")
            for g in filtered_low:
                click.echo(
                    f"  {abbrev_kind(g['kind'])} {g['name']} at "
                    f"{loc(g['file'], g['line'])} "
                    f"-- {g['reason']}"
                )
            click.echo()

        if stale_tests:
            click.echo(f"COVERED (but stale):")
            for s in stale_tests:
                click.echo(
                    f"  {abbrev_kind(s['kind'])} {s['name']} at "
                    f"{loc(s['file'], s['line'])} "
                    f"-- tested in {os.path.basename(s['test_file'])} "
                    f"but not updated since change"
                )
            click.echo()

        if predicted_only:
            click.echo(
                f"NOTE: {len(predicted_only)} symbol(s) have graph-predicted tests but 0% imported line coverage."
            )
        if actual_only:
            click.echo(
                f"NOTE: {len(actual_only)} symbol(s) are covered in imported reports but have no graph-linked tests."
            )

        click.echo(
            f"SUMMARY: {len(high_gaps)} high, {len(medium_gaps)} medium, "
            f"{len(low_gaps)} low, {len(stale_tests)} stale "
            f"-- consider adding {actionable} tests"
        )
