"""Generate a structured execution plan for modifying code.

Composes data from context, preflight, and testmap into a step-by-step
strategy for an AI agent to follow when modifying a symbol or file.
"""

from __future__ import annotations

import click

from roam.commands.cmd_affected_tests import (
    _gather_affected_tests,
    _looks_like_file,
    _resolve_file_symbols,
)
from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json

# ---------------------------------------------------------------------------
# Post-change verification commands (static, per task type)
# ---------------------------------------------------------------------------

_POST_CHANGE = {
    "refactor": [
        {
            "command": "roam preflight {target}",
            "reason": "Re-check blast radius and tests after refactor",
        },
        {"command": "roam diff --fitness", "reason": "Verify no fitness rules broken"},
        {"command": "roam health", "reason": "Confirm overall health score unchanged"},
    ],
    "debug": [
        {"command": "roam preflight {target}", "reason": "Confirm fix scope and test coverage"},
        {"command": "roam diagnose {target}", "reason": "Verify root cause resolved"},
        {"command": "roam diff", "reason": "Review blast radius of fix"},
    ],
    "extend": [
        {"command": "roam preflight {target}", "reason": "Check new code passes fitness rules"},
        {"command": "roam diff --fitness", "reason": "Verify no regressions introduced"},
        {"command": "roam dead", "reason": "Ensure no dead code left behind"},
    ],
    "review": [
        {"command": "roam diff", "reason": "Summarize all changes in review"},
        {"command": "roam pr-risk", "reason": "Assess overall PR risk score"},
        {"command": "roam affected-tests --staged", "reason": "Verify test coverage for changes"},
    ],
    "understand": [
        {"command": "roam context {target}", "reason": "Get full context for the symbol"},
        {"command": "roam impact {target}", "reason": "See what depends on this symbol"},
        {"command": "roam trace {target}", "reason": "Trace call paths through the symbol"},
    ],
}


# ---------------------------------------------------------------------------
# Read order: gather callees and callers for topological ordering
# ---------------------------------------------------------------------------


def _build_read_order(conn, sym_ids, file_paths, task, depth):
    """Build a ranked read order from the call graph.

    For refactor/extend: callees first (dependencies before target).
    For debug: callers first (understand who is affected).
    For review/understand: mix both directions.

    Returns a list of dicts: {file, line_start, line_end, reason, rank}.
    """
    if not sym_ids:
        return []

    # Collect callee symbols (outgoing edges from target)
    callees = []
    for sid in sym_ids:
        rows = conn.execute(
            """SELECT e.target_id, s.name, s.kind, s.line_start, s.line_end,
                      f.path as file_path,
                      COALESCE(gm.pagerank, 0) as pagerank
               FROM edges e
               JOIN symbols s ON e.target_id = s.id
               JOIN files f ON s.file_id = f.id
               LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
               WHERE e.source_id = ?
               ORDER BY gm.pagerank DESC""",
            (sid,),
        ).fetchall()
        callees.extend(rows)

    # Collect caller symbols (incoming edges to target)
    callers = []
    for sid in sym_ids:
        rows = conn.execute(
            """SELECT e.source_id, s.name, s.kind, s.line_start, s.line_end,
                      f.path as file_path,
                      COALESCE(gm.pagerank, 0) as pagerank
               FROM edges e
               JOIN symbols s ON e.source_id = s.id
               JOIN files f ON s.file_id = f.id
               LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
               WHERE e.target_id = ?
               ORDER BY gm.pagerank DESC""",
            (sid,),
        ).fetchall()
        callers.extend(rows)

    # Build target file entries
    target_entries = []
    for fp in file_paths:
        row = conn.execute(
            """SELECT f.path, MIN(s.line_start) as line_start, MAX(s.line_end) as line_end,
                      COALESCE(MAX(gm.pagerank), 0) as pagerank
               FROM files f
               JOIN symbols s ON s.file_id = f.id
               LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
               WHERE f.path = ?""",
            (fp,),
        ).fetchone()
        if row and row["path"]:
            target_entries.append(
                {
                    "file": row["path"],
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                    "reason": "target",
                    "rank": 999.0,  # Always show target first
                }
            )

    # Group callee / caller entries by file, pick best pagerank per file
    callee_map = {}  # file_path -> {line_start, line_end, pagerank, sym_name}
    for row in callees:
        fp = row["file_path"]
        if fp in file_paths:
            continue  # skip target files
        pr = row["pagerank"] or 0.0
        if fp not in callee_map or pr > callee_map[fp]["pagerank"]:
            callee_map[fp] = {
                "file": fp,
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "pagerank": pr,
                "sym_name": row["name"],
                "reason": f"callee ({row['name']})",
            }

    caller_map = {}  # file_path -> {line_start, line_end, pagerank, sym_name}
    for row in callers:
        fp = row["file_path"]
        if fp in file_paths:
            continue  # skip target files
        pr = row["pagerank"] or 0.0
        if fp not in caller_map or pr > caller_map[fp]["pagerank"]:
            caller_map[fp] = {
                "file": fp,
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "pagerank": pr,
                "sym_name": row["name"],
                "reason": f"caller ({row['name']}) top PageRank",
            }

    limit = depth * 5

    if task in ("refactor", "extend"):
        # Callees first, then callers
        ordered = (
            sorted(callee_map.values(), key=lambda x: -x["pagerank"])[:limit]
            + sorted(caller_map.values(), key=lambda x: -x["pagerank"])[:limit]
        )
    elif task == "debug":
        # Callers first, then callees
        ordered = (
            sorted(caller_map.values(), key=lambda x: -x["pagerank"])[:limit]
            + sorted(callee_map.values(), key=lambda x: -x["pagerank"])[:limit]
        )
    else:
        # review / understand: rank by pagerank across both
        combined = {}
        for entry in list(callee_map.values()) + list(caller_map.values()):
            fp = entry["file"]
            if fp not in combined or entry["pagerank"] > combined[fp]["pagerank"]:
                combined[fp] = entry
        ordered = sorted(combined.values(), key=lambda x: -x["pagerank"])[:limit]

    # Deduplicate by file
    seen = set()
    result = []
    for entry in target_entries:
        if entry["file"] not in seen:
            seen.add(entry["file"])
            result.append(
                {
                    "file": entry["file"],
                    "line_start": entry["line_start"],
                    "line_end": entry["line_end"],
                    "reason": entry["reason"],
                    "rank": entry["rank"],
                }
            )

    for entry in ordered:
        fp = entry["file"]
        if fp not in seen:
            seen.add(fp)
            result.append(
                {
                    "file": fp,
                    "line_start": entry["line_start"],
                    "line_end": entry["line_end"],
                    "reason": entry["reason"],
                    "rank": round(entry["pagerank"], 6),
                }
            )

    return result[: limit + len(file_paths)]


# ---------------------------------------------------------------------------
# Invariants: callers of target with signature info
# ---------------------------------------------------------------------------


def _build_invariants(conn, sym_ids, task):
    """Gather invariants to preserve: caller signatures + target signatures."""
    invariants = []

    # Add target symbols themselves
    for sid in sym_ids:
        row = conn.execute(
            """SELECT s.name, s.kind, s.signature, s.line_start, f.path as file_path,
                      (SELECT COUNT(*) FROM edges WHERE target_id = s.id) as caller_count
               FROM symbols s
               JOIN files f ON s.file_id = f.id
               WHERE s.id = ?""",
            (sid,),
        ).fetchone()
        if row:
            invariants.append(
                {
                    "name": row["name"],
                    "kind": row["kind"],
                    "signature": row["signature"] or "",
                    "callers": row["caller_count"],
                    "location": loc(row["file_path"], row["line_start"]),
                    "role": "target",
                }
            )

    # Add direct callers with their signatures
    caller_rows = []
    for sid in sym_ids:
        rows = conn.execute(
            """SELECT s.name, s.kind, s.signature, s.line_start, f.path as file_path,
                      (SELECT COUNT(*) FROM edges WHERE target_id = s.id) as caller_count
               FROM edges e
               JOIN symbols s ON e.source_id = s.id
               JOIN files f ON s.file_id = f.id
               WHERE e.target_id = ?
               LIMIT 20""",
            (sid,),
        ).fetchall()
        caller_rows.extend(rows)

    seen_names = {inv["name"] for inv in invariants}
    for row in caller_rows:
        if row["name"] in seen_names:
            continue
        seen_names.add(row["name"])
        invariants.append(
            {
                "name": row["name"],
                "kind": row["kind"],
                "signature": row["signature"] or "",
                "callers": row["caller_count"],
                "location": loc(row["file_path"], row["line_start"]),
                "role": "caller",
            }
        )

    return invariants[:15]


# ---------------------------------------------------------------------------
# Safe modification points and "touch carefully" symbols
# ---------------------------------------------------------------------------


def _build_modification_points(conn, file_paths):
    """For each symbol in target files, classify as safe or touch-carefully."""
    safe_points = []
    touch_carefully = []

    for fp in file_paths:
        frow = conn.execute("SELECT id FROM files WHERE path = ?", (fp,)).fetchone()
        if not frow:
            continue

        syms = conn.execute(
            """SELECT s.id, s.name, s.kind, s.line_start,
                      (SELECT COUNT(*) FROM edges WHERE target_id = s.id) as in_degree
               FROM symbols s
               WHERE s.file_id = ?
               ORDER BY s.line_start""",
            (frow["id"],),
        ).fetchall()

        for sym in syms:
            entry = {
                "name": sym["name"],
                "kind": sym["kind"],
                "line": sym["line_start"],
                "incoming_edges": sym["in_degree"],
            }
            if sym["in_degree"] == 0:
                safe_points.append(entry)
            elif sym["in_degree"] >= 3:
                touch_carefully.append({**entry, "reason": f"{sym['in_degree']} callers depend on this"})

    # Sort touch_carefully by descending in-degree
    touch_carefully.sort(key=lambda x: -x["incoming_edges"])

    return safe_points, touch_carefully


# ---------------------------------------------------------------------------
# Test shortlist
# ---------------------------------------------------------------------------


def _build_test_shortlist(conn, sym_ids, file_paths):
    """Gather affected tests ranked by relevance."""
    results = _gather_affected_tests(conn, sym_ids, file_paths)

    seen_files = []
    seen_set = set()
    for r in results:
        if r["file"] not in seen_set:
            seen_set.add(r["file"])
            seen_files.append(r["file"])

    pytest_cmd = "pytest " + " ".join(seen_files) if seen_files else ""

    return {
        "test_files": seen_files,
        "pytest_command": pytest_cmd,
        "count": len(results),
        "tests": [
            {
                "file": r["file"],
                "symbol": r["symbol"],
                "kind": r["kind"],
                "hops": r["hops"],
            }
            for r in results[:20]
        ],
    }


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def _resolve_plan_targets(conn, target, symbol_name, file_path, staged, root):
    """Resolve CLI arguments into (sym_ids, file_paths, label).

    Returns (sym_ids: set, file_paths: set, label: str, error: str|None).
    """
    sym_ids = set()
    file_paths = set()
    label = target or symbol_name or file_path or "staged changes"
    error = None

    if staged:
        from roam.commands.changed_files import get_changed_files, resolve_changed_to_db

        changed = get_changed_files(root, staged=True)
        if not changed:
            return sym_ids, file_paths, "staged (no changes)", "No staged changes found"
        file_map = resolve_changed_to_db(conn, changed)
        if not file_map:
            return sym_ids, file_paths, "staged (not indexed)", "Staged files not in index"
        for path, fid in file_map.items():
            file_paths.add(path)
            syms = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (fid,)).fetchall()
            sym_ids.update(s["id"] for s in syms)
        label = f"staged changes ({len(file_map)} files)"

    # --symbol option
    if symbol_name:
        sym = find_symbol(conn, symbol_name)
        if sym is None:
            return sym_ids, file_paths, symbol_name, f"Symbol not found: {symbol_name}"
        sym_ids.add(sym["id"])
        file_paths.add(sym["file_path"])
        label = f"{sym['name']} ({loc(sym['file_path'], sym['line_start'])})"

    # --file option
    if file_path:
        fp_norm = file_path.replace("\\", "/")
        sids, fpaths = _resolve_file_symbols(conn, fp_norm)
        if not sids:
            # Try LIKE match
            frow = conn.execute(
                "SELECT id, path FROM files WHERE path LIKE ? LIMIT 1",
                (f"%{fp_norm}",),
            ).fetchone()
            if frow:
                sids2 = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (frow["id"],)).fetchall()
                sids = {s["id"] for s in sids2}
                fpaths = {frow["path"]}
        if not sids and not staged:
            return sym_ids, file_paths, file_path, f"File not found in index: {file_path}"
        sym_ids.update(sids)
        file_paths.update(fpaths)
        label = fp_norm

    # Positional target argument
    if target and not symbol_name and not file_path:
        target_norm = target.replace("\\", "/")
        if _looks_like_file(target_norm):
            sids, fpaths = _resolve_file_symbols(conn, target_norm)
            if not sids:
                frow = conn.execute(
                    "SELECT id, path FROM files WHERE path LIKE ? LIMIT 1",
                    (f"%{target_norm}",),
                ).fetchone()
                if frow:
                    sids2 = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (frow["id"],)).fetchall()
                    sids = {s["id"] for s in sids2}
                    fpaths = {frow["path"]}
            if not sids:
                return sym_ids, file_paths, target, f"File not found in index: {target}"
            sym_ids.update(sids)
            file_paths.update(fpaths)
            label = target_norm
        else:
            sym = find_symbol(conn, target_norm)
            if sym is None:
                return sym_ids, file_paths, target, f"Symbol not found: {target}"
            sym_ids.add(sym["id"])
            file_paths.add(sym["file_path"])
            label = f"{sym['name']} ({loc(sym['file_path'], sym['line_start'])})"

    return sym_ids, file_paths, label, error


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("plan")
@click.argument("target", required=False, default=None)
@click.option(
    "--task",
    type=click.Choice(["refactor", "debug", "extend", "review", "understand"]),
    default="refactor",
    help="Type of work to plan for",
)
@click.option("--symbol", "symbol_name", default=None, help="Symbol to plan for")
@click.option("--file", "file_path", default=None, help="File to plan for")
@click.option("--staged", is_flag=True, help="Plan for staged changes")
@click.option("--depth", default=2, type=int, help="Call graph depth for read order")
@click.pass_context
def plan(ctx, target, task, symbol_name, file_path, staged, depth):
    """Generate a structured execution plan for modifying code.

    TARGET is a symbol name or file path.  Use --symbol / --file for
    explicit disambiguation, or --staged to plan for staged changes.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    if not target and not symbol_name and not file_path and not staged:
        click.echo("Provide a TARGET symbol/file, --symbol, --file, or --staged.")
        raise SystemExit(1)

    ensure_index()
    root = find_project_root()

    with open_db(readonly=True) as conn:
        sym_ids, file_paths, label, error = _resolve_plan_targets(conn, target, symbol_name, file_path, staged, root)

        if error or not sym_ids:
            msg = error or f"No symbols found for: {label}"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "plan",
                            summary={
                                "verdict": f"Cannot plan: {msg}",
                                "task": task,
                                "error": msg,
                            },
                        )
                    )
                )
            else:
                click.echo(f"Cannot plan: {msg}")
            return

        # Build all plan sections
        read_order = _build_read_order(conn, sym_ids, file_paths, task, depth)
        invariants = _build_invariants(conn, sym_ids, task)
        safe_points, touch_carefully = _build_modification_points(conn, file_paths)
        tests = _build_test_shortlist(conn, sym_ids, file_paths)

        # Post-change verification commands — substitute target name
        target_name = label.split(" ")[0]  # first word (symbol name or file)
        post_change = [
            {
                "command": p["command"].replace("{target}", target_name),
                "reason": p["reason"],
            }
            for p in _POST_CHANGE.get(task, _POST_CHANGE["refactor"])
        ]

        verdict = f"Plan for {task} of {label}"

        # JSON mode
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "plan",
                        summary={
                            "verdict": verdict,
                            "task": task,
                            "target": label,
                            "read_order_count": len(read_order),
                            "invariant_count": len(invariants),
                            "safe_points": len(safe_points),
                            "touch_carefully": len(touch_carefully),
                            "tests": tests["count"],
                        },
                        read_order=read_order,
                        invariants=invariants,
                        safe_points=safe_points,
                        touch_carefully=touch_carefully,
                        tests=tests,
                        post_change=post_change,
                    )
                )
            )
            return

        # Text output — verdict first
        click.echo(f"VERDICT: {verdict}\n")

        # Section 1: Read order
        direction = "callee-first" if task in ("refactor", "extend") else "caller-first"
        click.echo(f"1. READ ORDER ({len(read_order)} files, {direction}):")
        for i, entry in enumerate(read_order, 1):
            ls = entry.get("line_start") or ""
            le = entry.get("line_end") or ""
            if ls and le:
                file_loc = f"{entry['file']}:{ls}-{le}"
            elif ls:
                file_loc = f"{entry['file']}:{ls}"
            else:
                file_loc = entry["file"]
            click.echo(f"   {i}. {file_loc:<55s} {entry['reason']}")
        click.echo()

        # Section 2: Invariants
        click.echo("2. INVARIANTS TO PRESERVE:")
        if invariants:
            for inv in invariants:
                sig = inv["signature"] or inv["name"]
                callers_str = f"{inv['callers']} callers"
                role_str = f"  ({inv['role']})" if inv["role"] != "target" else ""
                click.echo(f"   - {sig:<50s} -- {callers_str}{role_str}")
        else:
            click.echo("   (no callers found)")
        click.echo()

        # Section 3: Safe modification points
        click.echo("3. SAFE MODIFICATION POINTS:")
        if safe_points:
            for sp in safe_points[:10]:
                click.echo(f"   - {abbrev_kind(sp['kind'])} {sp['name']:<35s} (line {sp['line']})  0 external callers")
        else:
            click.echo("   (all symbols have callers — proceed carefully)")
        click.echo()

        # Section 4: Touch carefully
        click.echo("4. TOUCH CAREFULLY:")
        if touch_carefully:
            for tc in touch_carefully[:10]:
                click.echo(
                    f"   - {abbrev_kind(tc['kind'])} {tc['name']:<35s}"
                    f" (line {tc['line']})  {tc['incoming_edges']} callers"
                )
        else:
            click.echo("   (no high-fan-in symbols found)")
        click.echo()

        # Section 5: Test shortlist
        test_count = tests["count"]
        click.echo(f"5. TEST SHORTLIST ({test_count} tests):")
        if tests["pytest_command"]:
            click.echo(f"   {tests['pytest_command']}")
            for t in tests["tests"][:10]:
                sym_str = f"::{t['symbol']}" if t.get("symbol") else ""
                click.echo(f"   - {t['file']}{sym_str:<50s} {t['kind']}")
        else:
            click.echo("   (no affected tests found)")
        click.echo()

        # Section 6: Post-change verification
        click.echo("6. POST-CHANGE VERIFICATION:")
        for pc in post_change:
            click.echo(f"   - {pc['command']}")
        click.echo()
