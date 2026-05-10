"""Trace from a changed symbol or file to test files that exercise it."""

from __future__ import annotations

import os
from collections import deque

import click

from roam.commands.changed_files import (
    get_changed_files,
    is_test_file,
    resolve_changed_to_db,
)
from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import batched_in, find_project_root, open_db
from roam.index.test_conventions import find_test_candidates
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json

_MAX_HOPS = 10


# ---------------------------------------------------------------------------
# BFS reverse-edge walker
# ---------------------------------------------------------------------------


def _bfs_reverse_callers(conn, start_ids):
    """Walk reverse edges (callers) via BFS up to _MAX_HOPS.

    *start_ids* is a set of symbol IDs to begin from.

    Returns a dict ``{symbol_id: (hop_count, via_name)}`` for every
    reachable caller.  *via_name* is the name of the first symbol on
    the path from the start set that led us here (useful for the
    "via" label in transitive results).
    """
    visited = {}  # symbol_id -> (hops, via_name)
    queue = deque()  # (symbol_id, hops, via_name)

    for sid in start_ids:
        visited[sid] = (0, None)
        queue.append((sid, 0, None))

    while queue:
        current_id, hops, via = queue.popleft()
        if hops >= _MAX_HOPS:
            continue

        callers = conn.execute(
            "SELECT e.source_id, s.name FROM edges e JOIN symbols s ON e.source_id = s.id WHERE e.target_id = ?",
            (current_id,),
        ).fetchall()

        for row in callers:
            caller_id = row["source_id"]
            caller_name = row["name"]
            new_hops = hops + 1
            # The "via" label is the name of the node at hop 1 that started
            # this path (i.e. the direct caller of the target).
            new_via = via if via else caller_name

            if caller_id not in visited or visited[caller_id][0] > new_hops:
                visited[caller_id] = (new_hops, new_via)
                queue.append((caller_id, new_hops, new_via))

    return visited


# ---------------------------------------------------------------------------
# Colocated test detection
# ---------------------------------------------------------------------------


def _find_colocated_tests(conn, file_paths):
    """Find test files colocated with the given source files.

    Uses two mechanisms:
    1. Colocated tests in the same directory (e.g., test_*.py / *_test.py)
    2. Convention-based test discovery (e.g., separate test projects for C#)
    """
    # Pre-fetch the entire (path, language) map once. Replaces three
    # nested N+1 queries (per-dir LIKE, per-file language lookup,
    # per-candidate existence check) with a single SELECT and
    # in-memory dict / set lookups.
    path_to_language: dict[str, str | None] = {}
    for r in conn.execute("SELECT path, language FROM files").fetchall():
        path_to_language[r["path"]] = r["language"]
    all_paths = set(path_to_language)
    file_paths_set = set(file_paths)

    # mechanism 1: colocated tests within the same directory subtree.
    # Original code issued one ``WHERE path LIKE 'dir/%'`` per unique
    # input directory — recursive prefix match. Replaced with a single
    # in-memory scan over the pre-fetched all_paths set.
    dirs = set()
    for fp in file_paths:
        d = os.path.dirname(fp.replace("\\", "/"))
        if d:
            dirs.add(d)

    # Pre-classify each path once: which input dirs contain it as a
    # subtree descendant? For each path p, find the dirs in ``dirs``
    # that p starts with (followed by ``/``). Total work is
    # O(len(all_paths) * len(dirs)) but with zero DB round-trips.
    colocated = []
    for p in all_paths:
        if not is_test_file(p) or p in file_paths_set:
            continue
        p_norm = p.replace("\\", "/")
        for d in dirs:
            if p_norm.startswith(d + "/"):
                colocated.append(p)
                break

    # mechanism 2: convention-based test discovery.
    # Use the pre-fetched dict for both the per-file language lookup
    # and the per-candidate existence check — both were N+1 before.
    convention_tests = []
    for fp in file_paths:
        language = path_to_language.get(fp)
        if not language:
            continue
        candidates = find_test_candidates(fp, language=language)
        for candidate in candidates:
            if candidate in all_paths and is_test_file(candidate) and candidate not in file_paths_set:
                convention_tests.append(candidate)

    return sorted(set(colocated + convention_tests))


# ---------------------------------------------------------------------------
# Core: gather affected tests for a set of symbol IDs
# ---------------------------------------------------------------------------


def _gather_affected_tests(conn, target_sym_ids, target_file_paths):
    """Return a sorted list of affected test entries.

    Each entry is a dict with keys:
        file, symbol (optional), kind (DIRECT|TRANSITIVE|COLOCATED),
        hops, via (optional).
    """
    # BFS from all target symbols
    reachable = _bfs_reverse_callers(conn, target_sym_ids)

    # Collect caller symbols that live in test files
    test_entries = {}  # keyed by (file, symbol_name) to dedupe

    if reachable:
        caller_ids = [sid for sid in reachable if sid not in target_sym_ids]
        if caller_ids:
            rows = batched_in(
                conn,
                "SELECT s.id, s.name, s.kind, f.path as file_path "
                "FROM symbols s "
                "JOIN files f ON s.file_id = f.id "
                "WHERE s.id IN ({ph})",
                caller_ids,
            )

            for r in rows:
                if not is_test_file(r["file_path"]):
                    continue
                hops, via = reachable[r["id"]]
                key = (r["file_path"], r["name"])
                kind = "DIRECT" if hops == 1 else "TRANSITIVE"

                # Keep the shortest path if we see a duplicate
                if key in test_entries and test_entries[key]["hops"] <= hops:
                    continue

                test_entries[key] = {
                    "file": r["file_path"],
                    "symbol": r["name"],
                    "symbol_kind": r["kind"],
                    "kind": kind,
                    "hops": hops,
                    "via": via if hops > 1 else None,
                }

    # Colocated tests (filename-pattern match, not in call graph)
    colocated_files = _find_colocated_tests(conn, set(target_file_paths))
    seen_files = {e["file"] for e in test_entries.values()}

    for cf in colocated_files:
        if cf in seen_files:
            continue
        # Use file path as key (no specific symbol)
        key = (cf, None)
        if key not in test_entries:
            test_entries[key] = {
                "file": cf,
                "symbol": None,
                "symbol_kind": None,
                "kind": "COLOCATED",
                "hops": None,
                "via": None,
            }

    # Sort: DIRECT first, then TRANSITIVE by hop count, then COLOCATED
    kind_order = {"DIRECT": 0, "TRANSITIVE": 1, "COLOCATED": 2}
    results = sorted(
        test_entries.values(),
        key=lambda e: (kind_order.get(e["kind"], 9), e["hops"] or 999, e["file"]),
    )

    return results


# ---------------------------------------------------------------------------
# Resolve targets -> (symbol_ids, file_paths)
# ---------------------------------------------------------------------------


def _resolve_file_symbols(conn, path):
    """Return all symbol IDs and the canonical path for a file."""
    frow = conn.execute("SELECT id, path FROM files WHERE path = ?", (path,)).fetchone()
    if frow is None:
        frow = conn.execute(
            "SELECT id, path FROM files WHERE path LIKE ? LIMIT 1",
            (f"%{path}",),
        ).fetchone()
    if frow is None:
        return set(), set()

    syms = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (frow["id"],)).fetchall()
    return {s["id"] for s in syms}, {frow["path"]}


def _looks_like_file(target):
    """Heuristic: does the target string look like a file path?"""
    return "/" in target or "\\" in target or target.endswith(".py")


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("affected-tests")
@click.argument("target", required=False, default=None)
@click.option("--staged", is_flag=True, help="Find tests for staged changes")
@click.option(
    "--command",
    "show_command",
    is_flag=True,
    help="Output a runnable pytest command",
)
@click.pass_context
def affected_tests(ctx, target, staged, show_command):
    """Trace from a changed symbol or file to test files that exercise it.

    Unlike ``test-map`` (which maps test topology for a specific symbol),
    this command finds all tests affected by staged or specified changes.

    TARGET is a symbol name or file path.  Use --staged to automatically
    find tests for all staged changes.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    if not target and not staged:
        click.echo("Provide a TARGET symbol/file or use --staged.")
        raise SystemExit(1)

    with open_db(readonly=True) as conn:
        all_sym_ids = set()
        all_file_paths = set()
        target_label = target or "staged changes"

        # --staged mode: resolve changed files to symbols
        if staged:
            root = find_project_root()
            changed = get_changed_files(root, staged=True)
            if not changed:
                click.echo("No staged changes found.")
                return
            file_map = resolve_changed_to_db(conn, changed)
            if not file_map:
                click.echo("Staged files not found in index. Try `roam index` first.")
                return
            for path, fid in file_map.items():
                all_file_paths.add(path)
                syms = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (fid,)).fetchall()
                all_sym_ids.update(s["id"] for s in syms)
            target_label = f"staged changes ({len(file_map)} files)"

        # Explicit target (may combine with --staged)
        if target:
            target_norm = target.replace("\\", "/")
            if _looks_like_file(target_norm):
                sym_ids, fpaths = _resolve_file_symbols(conn, target_norm)
                if not sym_ids:
                    click.echo(f"File not found in index: {target}")
                    raise SystemExit(1)
                all_sym_ids.update(sym_ids)
                all_file_paths.update(fpaths)
            else:
                sym = find_symbol(conn, target)
                if sym is None:
                    click.echo(f"Symbol not found: {target}")
                    raise SystemExit(1)
                all_sym_ids.add(sym["id"])
                all_file_paths.add(sym["file_path"])
                target_label = f"{sym['name']} ({abbrev_kind(sym['kind'])}, {loc(sym['file_path'], sym['line_start'])})"

        # Gather affected tests
        results = _gather_affected_tests(conn, all_sym_ids, all_file_paths)

        # Unique test files for the pytest command
        seen_order = []
        seen_set = set()
        for r in results:
            if r["file"] not in seen_set:
                seen_set.add(r["file"])
                seen_order.append(r["file"])

        pytest_cmd = "pytest " + " ".join(seen_order) if seen_order else ""

        # --command mode: just print the command
        if show_command:
            if pytest_cmd:
                click.echo(pytest_cmd)
            else:
                click.echo("# No affected tests found.")
            return

        # JSON output
        if json_mode:
            direct_count = sum(1 for r in results if r["kind"] == "DIRECT")
            transitive_count = sum(1 for r in results if r["kind"] == "TRANSITIVE")
            colocated_count = sum(1 for r in results if r["kind"] == "COLOCATED")

            if results:
                verdict = f"{len(results)} tests affected ({len(seen_order)} files) for {target_label}"
            else:
                verdict = f"no tests affected for {target_label}"

            click.echo(
                to_json(
                    json_envelope(
                        "affected-tests",
                        summary={
                            "verdict": verdict,
                            "target": target_label,
                            "total_tests": len(results),
                            "direct": direct_count,
                            "transitive": transitive_count,
                            "colocated": colocated_count,
                            "test_files": len(seen_order),
                        },
                        budget=token_budget,
                        tests=[
                            {
                                "file": r["file"],
                                "symbol": r["symbol"],
                                "kind": r["kind"],
                                "hops": r["hops"],
                                "via": r["via"],
                            }
                            for r in results
                        ],
                        pytest_command=pytest_cmd,
                        test_files=seen_order,
                    )
                )
            )
            return

        # Text output
        if not results:
            click.echo(f"VERDICT: no tests affected for {target_label}.")
            return

        verdict = f"{len(results)} tests affected ({len(seen_order)} files) for {target_label}"
        click.echo(f"VERDICT: {verdict}\n")
        click.echo(f"Affected tests for {target_label}:\n")

        for r in results:
            kind_tag = f"{r['kind']:<12s}"

            if r["symbol"]:
                label = f"{r['file']}::{r['symbol']}"
            else:
                label = r["file"]

            if r["kind"] == "DIRECT":
                detail = f"({r['hops']} hop)"
            elif r["kind"] == "TRANSITIVE":
                via_str = f" via {r['via']}" if r["via"] else ""
                detail = f"({r['hops']} hops{via_str})"
            else:
                detail = "(same directory)"

            click.echo(f"  {kind_tag} {label:<55s} {detail}")

        click.echo()
        if pytest_cmd:
            click.echo(f"Run: {pytest_cmd}")
