"""Generate a structured execution plan for modifying code.

Composes data from context, preflight, and testmap into a step-by-step
strategy for an AI agent to follow when modifying a symbol or file.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because plan outputs are invocation-scoped work plans (read_order[],
invariants[], safe_points[], touch_carefully[], tests) — task-specific
advice, not per-location violations. See action.yml _SUPPORTED_SARIF
allowlist and W1154 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.cmd_affected_tests import (
    _gather_affected_tests,
    _looks_like_file,
    _resolve_file_symbols,
)
from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import batched_in, find_project_root, open_db
from roam.output.formatter import (
    abbrev_kind,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)

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

    # Collect callee symbols (outgoing edges from all targets) in one query.
    callees = batched_in(
        conn,
        """SELECT e.target_id, s.name, s.kind, s.line_start, s.line_end,
                  f.path as file_path,
                  COALESCE(gm.pagerank, 0) as pagerank
           FROM edges e
           JOIN symbols s ON e.target_id = s.id
           JOIN files f ON s.file_id = f.id
           LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
           WHERE e.source_id IN ({ph})
           ORDER BY gm.pagerank DESC""",
        sym_ids,
    )

    # Collect caller symbols (incoming edges to all targets) in one query.
    callers = batched_in(
        conn,
        """SELECT e.source_id, s.name, s.kind, s.line_start, s.line_end,
                  f.path as file_path,
                  COALESCE(gm.pagerank, 0) as pagerank
           FROM edges e
           JOIN symbols s ON e.source_id = s.id
           JOIN files f ON s.file_id = f.id
           LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
           WHERE e.target_id IN ({ph})
           ORDER BY gm.pagerank DESC""",
        sym_ids,
    )

    # Build target file entries in one batched query.
    target_entries = []
    target_rows = batched_in(
        conn,
        """SELECT f.path, MIN(s.line_start) as line_start, MAX(s.line_end) as line_end,
                  COALESCE(MAX(gm.pagerank), 0) as pagerank
           FROM files f
           JOIN symbols s ON s.file_id = f.id
           LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
           WHERE f.path IN ({ph})
           GROUP BY f.path""",
        file_paths,
    )
    for row in target_rows:
        if row["path"]:
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


def _trim_signature(sig: str) -> str:
    """Strip leading decorator lines from a captured signature.

    Python's symbol extractor stores the signature as the entire span from the
    first decorator through the ``def``/``class`` declaration. For decorated
    functions this can run hundreds of characters (whole ``@roam_capability(...)``
    + ``@click.command(...)`` + option blocks), dwarfing the actual signature
    and bloating ``invariants[].signature`` in the plan envelope (Pattern 6
    response-volume risk). Return only the first non-decorator line(s).
    """
    if not sig:
        return ""
    lines = sig.splitlines()
    # Drop leading decorator lines plus their continuations (paren-balanced).
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        if not stripped.startswith("@"):
            break
        # Walk forward until the decorator's parentheses are balanced.
        depth = 0
        while i < len(lines):
            depth += lines[i].count("(") - lines[i].count(")")
            i += 1
            if depth <= 0:
                break
    return "\n".join(lines[i:]).strip() or sig.strip()


def _build_invariants(conn, sym_ids, task):
    """Gather invariants to preserve: caller signatures + target signatures."""
    invariants = []

    # Add target symbols themselves in a single batched query.
    target_rows = batched_in(
        conn,
        """SELECT s.name, s.kind, s.signature, s.line_start, f.path as file_path,
                  (SELECT COUNT(*) FROM edges WHERE target_id = s.id) as caller_count
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           WHERE s.id IN ({ph})""",
        sym_ids,
    )
    for row in target_rows:
        invariants.append(
            {
                "name": row["name"],
                "kind": row["kind"],
                "signature": _trim_signature(row["signature"] or ""),
                "callers": row["caller_count"],
                "location": loc(row["file_path"], row["line_start"]),
                "role": "target",
            }
        )

    # Add direct callers with their signatures in a single batched query.
    caller_rows = batched_in(
        conn,
        """SELECT s.name, s.kind, s.signature, s.line_start, f.path as file_path,
                  (SELECT COUNT(*) FROM edges WHERE target_id = s.id) as caller_count
           FROM edges e
           JOIN symbols s ON e.source_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE e.target_id IN ({ph})""",
        sym_ids,
    )

    seen_names = {inv["name"] for inv in invariants}
    for row in caller_rows:
        if row["name"] in seen_names:
            continue
        seen_names.add(row["name"])
        invariants.append(
            {
                "name": row["name"],
                "kind": row["kind"],
                "signature": _trim_signature(row["signature"] or ""),
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

    file_rows = batched_in(
        conn,
        "SELECT id, path FROM files WHERE path IN ({ph})",
        file_paths,
    )
    file_id_to_path = {row["id"]: row["path"] for row in file_rows}
    if not file_id_to_path:
        return safe_points, touch_carefully

    symbol_rows = batched_in(
        conn,
        """SELECT s.id, s.name, s.kind, s.line_start, f.path AS file_path,
                  (SELECT COUNT(*) FROM edges WHERE target_id = s.id) AS in_degree
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           WHERE s.file_id IN ({ph})
           ORDER BY s.file_id, s.line_start""",
        file_id_to_path.keys(),
    )

    symbols_by_path: dict[str, list] = {}
    for sym in symbol_rows:
        symbols_by_path.setdefault(sym["file_path"], []).append(sym)

    for fp in file_paths:
        for sym in symbols_by_path.get(fp, []):
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

    Returns ``(sym_ids: set, file_paths: set, label: str, error: str|None,
    resolution_tier: str, resolved_target: str | None)``.

    W1245 Pattern-2 variant-D: ``resolution_tier`` is one of
    ``{"symbol", "file", "file_substring", "fuzzy", "unresolved"}`` when
    a target was requested (via ``--symbol``, ``--path``, or positional
    ``target``). The file branches now consume the
    :func:`roam.commands.resolve.resolve_file_symbols` substrate so a
    LIKE-fallback substring match surfaces as ``"file_substring"`` rather
    than collapsing into the exact-path ``"file"`` tier (Pattern-1
    Variant D Wave B; closes the audit MEDIUM-severity vocab-mismatch
    entry). ``resolved_target`` echoes the qualified name (for symbol
    targets) or canonical file path (for file targets) so callers can
    disclose the divergence between input and resolved value.
    """
    sym_ids = set()
    file_paths = set()
    label = target or symbol_name or file_path or "staged changes"
    error = None
    resolution_tier = "symbol"
    resolved_target: str | None = None

    if staged:
        from roam.commands.changed_files import get_changed_files, resolve_changed_to_db

        changed = get_changed_files(root, staged=True)
        if not changed:
            return (
                sym_ids,
                file_paths,
                "staged (no changes)",
                "No staged changes found",
                resolution_tier,
                resolved_target,
            )
        file_map = resolve_changed_to_db(conn, changed)
        if not file_map:
            return (
                sym_ids,
                file_paths,
                "staged (not indexed)",
                "Staged files not in index",
                resolution_tier,
                resolved_target,
            )
        file_paths.update(file_map.keys())
        # Batch the symbol fetch across ALL staged file_ids in one IN-clause
        # query rather than one query per file (the prior per-file loop scaled
        # subprocess-free but issued N round-trips for N staged files).
        from roam.db.connection import batched_in

        rows = batched_in(
            conn,
            "SELECT id FROM symbols WHERE file_id IN ({ph})",
            list(file_map.values()),
        )
        sym_ids.update(r["id"] for r in rows)
        label = f"staged changes ({len(file_map)} files)"

    # --symbol option
    if symbol_name:
        sym = find_symbol(conn, symbol_name)
        if sym is None:
            return sym_ids, file_paths, symbol_name, f"Symbol not found: {symbol_name}", "unresolved", symbol_name
        sym_ids.add(sym["id"])
        file_paths.add(sym["file_path"])
        label = f"{sym['name']} ({loc(sym['file_path'], sym['line_start'])})"
        resolution_tier = sym.get("_resolution_tier", "symbol")
        resolved_target = sym["qualified_name"] or sym["name"]

    # --path option
    if file_path:
        fp_norm = file_path.replace("\\", "/")
        # Pattern-1 Variant D Wave B: substrate handles the LIKE fallback
        # internally and returns a tier discriminator (``"file"`` for
        # exact-path, ``"file_substring"`` for LIKE %name). The legacy
        # second LIKE block here was redundant on top of the substrate
        # AND silently collapsed both tiers into the same shape — the
        # canonical Variant D failure pattern. Threading ``file_tier``
        # into the disclosure closes the MEDIUM-severity entry from the
        # audit ((internal memo)).
        sids, fpaths, file_tier = _resolve_file_symbols(conn, fp_norm)
        if file_tier is None and not staged:
            return (
                sym_ids,
                file_paths,
                file_path,
                f"File not found in index: {file_path}",
                resolution_tier,
                resolved_target,
            )
        sym_ids.update(sids)
        file_paths.update(fpaths)
        label = fp_norm
        if file_tier is not None:
            resolution_tier = file_tier
            # Echo the resolved canonical path so disclosure.target
            # surfaces the substring drift between input and resolved.
            if fpaths:
                resolved_target = next(iter(fpaths))

    # Positional target argument
    if target and not symbol_name and not file_path:
        target_norm = target.replace("\\", "/")
        if _looks_like_file(target_norm):
            sids, fpaths, file_tier = _resolve_file_symbols(conn, target_norm)
            if file_tier is None:
                return (
                    sym_ids,
                    file_paths,
                    target,
                    f"File not found in index: {target}",
                    resolution_tier,
                    resolved_target,
                )
            sym_ids.update(sids)
            file_paths.update(fpaths)
            label = target_norm
            resolution_tier = file_tier
            if fpaths:
                resolved_target = next(iter(fpaths))
        else:
            sym = find_symbol(conn, target_norm)
            if sym is None:
                return sym_ids, file_paths, target, f"Symbol not found: {target}", "unresolved", target
            sym_ids.add(sym["id"])
            file_paths.add(sym["file_path"])
            label = f"{sym['name']} ({loc(sym['file_path'], sym['line_start'])})"
            resolution_tier = sym.get("_resolution_tier", "symbol")
            resolved_target = sym["qualified_name"] or sym["name"]

    return sym_ids, file_paths, label, error, resolution_tier, resolved_target


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="plan",
    category="workflow",
    summary="Generate a structured execution plan for modifying code",
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
@click.command("plan")
@click.argument("target", required=False, default=None)
@click.option(
    "--task",
    type=click.Choice(["refactor", "debug", "extend", "review", "understand"]),
    default="refactor",
    help="Type of work to plan for",
)
@click.option("--symbol", "symbol_name", default=None, help="Symbol to plan for")
@click.option("--path", "file_path", default=None, help="File path to plan for")
@click.option(
    "--file",
    "file_path",
    default=None,
    hidden=True,
    help="Deprecated alias for --path. Retained for backward compatibility.",
)
@click.option("--staged", is_flag=True, help="Plan for staged changes")
@click.option("--depth", default=2, type=int, help="Call graph depth for read order")
@click.pass_context
def plan(ctx, target, task, symbol_name, file_path, staged, depth):
    """Generate a structured execution plan for modifying code.

    Unlike ``plan-refactor`` (which focuses on refactoring simulation),
    this command generates a general-purpose work plan for any task type:
    refactor, debug, extend, review, or understand.

    TARGET is a symbol name or file path.  Use --symbol / --path for
    explicit disambiguation, or --staged to plan for staged changes.

    \b
    Examples:
      roam plan login_user
      roam plan src/api.py --task refactor
      roam plan --staged --task review
      roam plan parse_amount --task debug --depth 3

    See also ``plan-refactor`` (refactoring-specific simulation),
    ``preflight`` (blast-radius gate), and ``context`` (read-order for
    one symbol).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    if not target and not symbol_name and not file_path and not staged:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "plan",
                        summary={
                            "verdict": "no TARGET symbol/file, --symbol, --path, or --staged provided",
                            "state": "usage_error",
                            "partial_success": True,
                        },
                        status="usage_error",
                        isError=True,
                        error_code="USAGE_ERROR",
                        error="no TARGET symbol/file, --symbol, --path, or --staged provided",
                        hint="Pass a TARGET symbol/file, --symbol, --path, or --staged.",
                    )
                )
            )
        else:
            click.echo("Provide a TARGET symbol/file, --symbol, --path, or --staged.")
        raise SystemExit(1)

    ensure_index()
    root = find_project_root()

    with open_db(readonly=True) as conn:
        (
            sym_ids,
            file_paths,
            label,
            error,
            resolution_tier,
            resolved_target,
        ) = _resolve_plan_targets(conn, target, symbol_name, file_path, staged, root)

        # W1245 Pattern-2 variant-D: precompute the disclosure block once
        # so both error and success envelope branches can merge it.
        disclosure_target = resolved_target or symbol_name or target or label
        resolution_block = resolution_disclosure(resolution_tier, target=disclosure_target)
        # Pattern-1 Variant D Wave B: append a degraded-tier suffix to the
        # verdict so LAW-6 single-line consumers see the disclosure even
        # without parsing the full envelope. ``file_substring`` is distinct
        # from ``fuzzy`` and from the exact-``file`` tier (which doesn't
        # need a suffix — it's a fully-resolved success).
        if resolution_tier == "fuzzy":
            fuzzy_suffix = " [fuzzy resolution]"
        elif resolution_tier == "file_substring":
            fuzzy_suffix = " [file substring match]"
        else:
            fuzzy_suffix = ""

        if error or not sym_ids:
            msg = error or f"No symbols found for: {label}"
            if json_mode:
                # W1245: surface the unresolved-vs-symbol resolver state on
                # the error envelope too so MCP consumers see a uniform
                # variant-D disclosure shape across both branches.
                unresolved_block = (
                    resolution_block
                    if resolution_tier in ("unresolved", "fuzzy")
                    else resolution_disclosure("unresolved", target=disclosure_target)
                )
                click.echo(
                    to_json(
                        json_envelope(
                            "plan",
                            summary={
                                "verdict": f"Cannot plan: {msg}",
                                "task": task,
                                "error": msg,
                                **unresolved_block,
                            },
                            budget=token_budget,
                            **unresolved_block,
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

        verdict = f"Plan for {task} of {label}{fuzzy_suffix}"

        # JSON mode
        if json_mode:
            # W1245 Pattern-2 variant-D: merge resolver disclosure into both
            # the envelope summary and top-level. The summary already has a
            # ``target`` key (label-shaped) so we filter the helper's
            # ``target`` (resolved-name-shaped) out of the summary merge to
            # avoid clobbering -- the resolved name is still exposed via
            # the top-level ``target`` field from ``**resolution_block``.
            summary_block = {k: v for k, v in resolution_block.items() if k != "target"}
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
                            **summary_block,
                        },
                        budget=token_budget,
                        read_order=read_order,
                        invariants=invariants,
                        safe_points=safe_points,
                        touch_carefully=touch_carefully,
                        tests=tests,
                        post_change=post_change,
                        **resolution_block,
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
