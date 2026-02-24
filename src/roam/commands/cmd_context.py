"""Get the minimal context needed to safely modify a symbol."""

from __future__ import annotations

from collections import defaultdict

import click

from roam.db.connection import open_db, batched_in
from roam.db.queries import FILE_BY_PATH
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol, symbol_not_found, file_not_found_hint
from roam.commands.changed_files import is_test_file
from roam.commands.context_helpers import (
    get_coupling as _get_coupling,
    gather_task_extras as _gather_task_extras,
    gather_symbol_context as _gather_symbol_context,
    batch_context as _batch_context,
    gather_annotations as _gather_annotations,
)
from roam.commands.next_steps import suggest_next_steps, format_next_steps_text


_TASK_CHOICES = ["refactor", "debug", "extend", "review", "understand"]


# ---------------------------------------------------------------------------
# Task-mode text output helpers
# ---------------------------------------------------------------------------

def _render_complexity_text(metrics):
    if not metrics:
        return
    click.echo("Complexity:")
    click.echo(
        f"  cognitive={metrics['cognitive_complexity']:.0f}  "
        f"nesting={metrics['nesting_depth']}  "
        f"params={metrics['param_count']}  "
        f"lines={metrics['line_count']}  "
        f"returns={metrics['return_count']}  "
        f"bool_ops={metrics['bool_op_count']}  "
        f"callbacks={metrics['callback_depth']}"
    )
    click.echo()


def _render_graph_centrality_text(metrics):
    if not metrics:
        return
    click.echo("Graph centrality:")
    click.echo(
        f"  pagerank={metrics['pagerank']:.6f}  "
        f"in_degree={metrics['in_degree']}  "
        f"out_degree={metrics['out_degree']}  "
        f"betweenness={metrics['betweenness']:.6f}"
    )
    click.echo()


def _render_churn_text(churn):
    if not churn:
        return
    click.echo("Git churn (file):")
    click.echo(
        f"  commits={churn['commit_count']}  "
        f"total_churn={churn['total_churn']}  "
        f"authors={churn['distinct_authors']}"
    )
    click.echo()


def _render_coupling_text(coupling):
    if not coupling:
        return
    click.echo(f"Temporal coupling ({len(coupling)} partners):")
    rows = [
        [c["path"], f"{c['strength']:.0%}", str(c["cochange_count"])]
        for c in coupling[:10]
    ]
    click.echo(format_table(["file", "strength", "co-changes"], rows))
    click.echo()


def _render_affected_tests_text(tests):
    if not tests:
        click.echo("Affected tests: (none found via BFS)")
        click.echo()
        return
    direct = sum(1 for t in tests if t["kind"] == "DIRECT")
    transitive = sum(1 for t in tests if t["kind"] == "TRANSITIVE")
    click.echo(f"Affected tests ({direct} direct, {transitive} transitive):")
    for t in tests[:15]:
        via_str = f" via {t['via']}" if t.get("via") else ""
        hops = t["hops"]
        click.echo(
            f"  {t['kind']:<12s} {t['file']}::{t['symbol']}  "
            f"({hops} hop{'s' if hops != 1 else ''}{via_str})"
        )
    if len(tests) > 15:
        click.echo(f"  (+{len(tests) - 15} more)")
    click.echo()


def _render_blast_radius_text(blast):
    if not blast:
        return
    click.echo("Blast radius:")
    click.echo(
        f"  {blast['dependent_symbols']} dependent symbols in "
        f"{blast['dependent_files']} files"
    )
    click.echo()


def _render_cluster_text(cluster):
    if not cluster:
        return
    click.echo(
        f"Cluster: {cluster['cluster_label']} "
        f"({cluster['cluster_size']} symbols)"
    )
    names = ", ".join(m["name"] for m in cluster["top_members"][:6])
    if cluster["cluster_size"] > 6:
        names += f" +{cluster['cluster_size'] - 6} more"
    click.echo(f"  members: {names}")
    click.echo()


def _render_similar_symbols_text(similar):
    if not similar:
        return
    click.echo(f"Similar symbols ({len(similar)}):")
    rows = [
        [abbrev_kind(s["kind"]), s["name"], s["location"]]
        for s in similar[:10]
    ]
    click.echo(format_table(["kind", "name", "location"], rows))
    click.echo()


def _render_entry_points_text(entries):
    if not entries:
        return
    click.echo(f"Entry points reaching this ({len(entries)}):")
    rows = [
        [abbrev_kind(e["kind"]), e["name"], e["location"]]
        for e in entries
    ]
    click.echo(format_table(["kind", "name", "location"], rows))
    click.echo()


def _render_file_context_text(file_context):
    if not file_context:
        return
    click.echo(f"File context ({len(file_context)} other exports):")
    for fc in file_context[:15]:
        doc = " [documented]" if fc["has_docstring"] else ""
        click.echo(
            f"  {abbrev_kind(fc['kind'])}  {fc['name']}  L{fc['line']}{doc}"
        )
    if len(file_context) > 15:
        click.echo(f"  (+{len(file_context) - 15} more)")
    click.echo()


def _render_annotations_text(annotations):
    if not annotations:
        return
    click.echo(f"Annotations ({len(annotations)}):")
    for a in annotations:
        tag_str = f"[{a['tag']}] " if a.get("tag") else ""
        author_str = f" (by {a['author']})" if a.get("author") else ""
        click.echo(f"  {tag_str}{a['content']}{author_str}")
    click.echo()


# ---------------------------------------------------------------------------
# Task-mode output: text
# ---------------------------------------------------------------------------

def _output_task_single_text(c, task, extras):
    """Render task-mode text output for a single symbol."""
    sym = c["sym"]
    line_start = c["line_start"]
    non_test_callers = c["non_test_callers"]
    callees = c["callees"]
    test_callers = c["test_callers"]
    test_importers = c["test_importers"]
    siblings = c["siblings"]
    files_to_read = c["files_to_read"]
    skipped_callers = c["skipped_callers"]
    skipped_callees = c["skipped_callees"]

    hide_callees = extras.get("_hide_callees", False)
    limit_callers = extras.get("_limit_callers")
    limit_callees = extras.get("_limit_callees")

    sig = sym["signature"] or ""
    click.echo(f"=== Context for: {sym['name']} (task={task}) ===")
    click.echo(
        f"{abbrev_kind(sym['kind'])}  "
        f"{sym['qualified_name'] or sym['name']}"
        f"{'  ' + sig if sig else ''}  "
        f"{loc(sym['file_path'], line_start)}"
    )
    click.echo()

    # Annotations (shown for all task modes)
    _render_annotations_text(extras.get("annotations"))

    # understand: show docstring first
    if task == "understand" and extras.get("docstring"):
        click.echo("Docstring:")
        for line in extras["docstring"].strip().splitlines()[:10]:
            click.echo(f"  {line}")
        click.echo()

    # Callers
    caller_cap = limit_callers or 20
    if non_test_callers:
        click.echo(f"Callers ({len(non_test_callers)}):")
        rows = []
        for cr in non_test_callers[:caller_cap]:
            rows.append([
                abbrev_kind(cr["kind"]), cr["name"],
                loc(cr["file_path"], cr["edge_line"] or cr["line_start"]),
                cr["edge_kind"] or "",
            ])
        click.echo(format_table(["kind", "name", "location", "edge"], rows))
        if len(non_test_callers) > caller_cap:
            click.echo(f"  (+{len(non_test_callers) - caller_cap} more)")
        click.echo()
    else:
        click.echo("Callers: (none)")
        click.echo()

    # Callees (hidden for refactor, limited for understand)
    if not hide_callees:
        callee_cap = limit_callees or 15
        if callees:
            click.echo(f"Callees ({len(callees)}):")
            rows = []
            for ce in callees[:callee_cap]:
                rows.append([
                    abbrev_kind(ce["kind"]), ce["name"],
                    loc(ce["file_path"], ce["line_start"]),
                    ce["edge_kind"] or "",
                ])
            click.echo(format_table(["kind", "name", "location", "edge"], rows))
            if len(callees) > callee_cap:
                click.echo(f"  (+{len(callees) - callee_cap} more)")
            click.echo()
        else:
            click.echo("Callees: (none)")
            click.echo()

    # Tests (default for non-review/debug; those use BFS affected_tests)
    if task not in ("review", "debug"):
        if test_callers or test_importers:
            click.echo(
                f"Tests ({len(test_callers)} direct, "
                f"{len(test_importers)} file-level):"
            )
            for t in test_callers:
                click.echo(
                    f"  {abbrev_kind(t['kind'])}  {t['name']}  "
                    f"{loc(t['file_path'], t['line_start'])}"
                )
            for ti in test_importers:
                click.echo(f"  file  {ti['path']}")
        else:
            click.echo("Tests: (none)")
        click.echo()

    # Siblings (shown for refactor, understand)
    if task in ("refactor", "understand") and siblings:
        click.echo(f"Siblings ({len(siblings)} exports in same file):")
        for s in siblings[:10]:
            click.echo(f"  {abbrev_kind(s['kind'])}  {s['name']}")
        if len(siblings) > 10:
            click.echo(f"  (+{len(siblings) - 10} more)")
        click.echo()

    # Task-specific extra sections
    if task == "refactor":
        _render_complexity_text(extras.get("complexity"))
        _render_graph_centrality_text(extras.get("graph_centrality"))
        _render_coupling_text(extras.get("coupling"))

    elif task == "debug":
        _render_complexity_text(extras.get("complexity"))
        _render_affected_tests_text(extras.get("affected_tests", []))

    elif task == "extend":
        _render_graph_centrality_text(extras.get("graph_centrality"))
        _render_similar_symbols_text(extras.get("similar_symbols", []))
        _render_entry_points_text(extras.get("entry_points_reaching", []))

    elif task == "review":
        _render_complexity_text(extras.get("complexity"))
        _render_churn_text(extras.get("git_churn"))
        _render_affected_tests_text(extras.get("affected_tests", []))
        _render_coupling_text(extras.get("coupling"))
        _render_blast_radius_text(extras.get("blast_radius"))
        _render_graph_centrality_text(extras.get("graph_centrality"))

    elif task == "understand":
        _render_cluster_text(extras.get("cluster"))
        _render_graph_centrality_text(extras.get("graph_centrality"))
        _render_file_context_text(extras.get("file_context", []))

    # Files to read
    skipped_total = skipped_callers + skipped_callees
    extra_label = f", +{skipped_total} more" if skipped_total else ""
    click.echo(f"Files to read ({len(files_to_read)}{extra_label}):")
    for f in files_to_read:
        end_str = f"-{f['end']}" if f["end"] and f["end"] != f["start"] else ""
        lr = f":{f['start']}{end_str}" if f["start"] else ""
        click.echo(f"  {f['path']:<50s} {lr:<12s} ({f['reason']})")


# ---------------------------------------------------------------------------
# Task-mode output: JSON
# ---------------------------------------------------------------------------

def _output_task_single_json(c, task, extras, budget=0):
    """Build and emit JSON output for task-mode single symbol."""
    sym = c["sym"]
    line_start = c["line_start"]
    line_end = c["line_end"]
    non_test_callers = c["non_test_callers"]
    callees = c["callees"]
    test_callers = c["test_callers"]
    test_importers = c["test_importers"]
    siblings = c["siblings"]
    files_to_read = c["files_to_read"]

    hide_callees = extras.get("_hide_callees", False)
    limit_callers = extras.get("_limit_callers")
    limit_callees = extras.get("_limit_callees")

    caller_cap = limit_callers or len(non_test_callers)
    callee_cap = limit_callees or len(callees)

    payload = {
        "task": task,
        "symbol": sym["qualified_name"] or sym["name"],
        "kind": sym["kind"],
        "signature": sym["signature"] or "",
        "location": loc(sym["file_path"], line_start),
        "definition": {
            "file": sym["file_path"],
            "start": line_start, "end": line_end,
        },
        "callers": [
            {"name": cr["name"], "kind": cr["kind"],
             "location": loc(
                 cr["file_path"], cr["edge_line"] or cr["line_start"],
             ),
             "edge_kind": cr["edge_kind"] or ""}
            for cr in non_test_callers[:caller_cap]
        ],
    }

    if not hide_callees:
        payload["callees"] = [
            {"name": ce["name"], "kind": ce["kind"],
             "location": loc(ce["file_path"], ce["line_start"]),
             "edge_kind": ce["edge_kind"] or ""}
            for ce in callees[:callee_cap]
        ]

    if task not in ("review", "debug"):
        payload["tests"] = [
            {"name": t["name"], "kind": t["kind"],
             "location": loc(t["file_path"], t["line_start"]),
             "edge_kind": t["edge_kind"] or ""}
            for t in test_callers
        ]
        payload["test_files"] = [r["path"] for r in test_importers]

    if task in ("refactor", "understand"):
        payload["siblings"] = [
            {"name": s["name"], "kind": s["kind"]}
            for s in siblings[:10]
        ]

    # Annotations
    anns = extras.get("annotations")
    if anns:
        payload["annotations"] = anns

    # Task-specific extras
    for key in ("docstring", "complexity", "graph_centrality", "git_churn",
                "blast_radius", "cluster"):
        val = extras.get(key)
        if val is not None:
            payload[key] = val

    for key in ("coupling", "affected_tests", "similar_symbols",
                "entry_points_reaching", "file_context"):
        val = extras.get(key)
        if val:
            payload[key] = val

    payload["files_to_read"] = [
        {"path": f["path"], "start": f["start"],
         "end": f["end"], "reason": f["reason"],
         "score": f.get("score"), "rank": f.get("rank")}
        for f in files_to_read
    ]

    # Summary
    summary = {"task": task, "callers": len(non_test_callers)}
    if not hide_callees:
        summary["callees"] = len(callees)
    summary["tests"] = len(test_callers)
    summary["files_to_read"] = len(files_to_read)

    if extras.get("blast_radius"):
        summary["blast_radius_symbols"] = extras["blast_radius"]["dependent_symbols"]
        summary["blast_radius_files"] = extras["blast_radius"]["dependent_files"]
    if extras.get("affected_tests") is not None:
        summary["affected_tests_total"] = len(extras["affected_tests"])
    if extras.get("coupling"):
        summary["coupling_partners"] = len(extras["coupling"])

    click.echo(to_json(json_envelope("context", summary=summary, budget=budget, **payload)))


# ---------------------------------------------------------------------------
# File-level context: --for-file
# ---------------------------------------------------------------------------

def _resolve_file(conn, path):
    """Resolve a file path to its DB row, or None."""
    path = path.replace("\\", "/")
    frow = conn.execute(FILE_BY_PATH, (path,)).fetchone()
    if frow is None:
        frow = conn.execute(
            "SELECT * FROM files WHERE path LIKE ? LIMIT 1",
            (f"%{path}",),
        ).fetchone()
    return frow


def _gather_file_level_context(conn, frow):
    """Gather comprehensive file-level context.

    Returns a dict with callers, callees, tests, coupling, and complexity
    aggregated across all symbols in the file.
    """
    file_id = frow["id"]
    file_path = frow["path"]

    # Get all symbols in the file
    symbols = conn.execute(
        "SELECT s.*, f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.file_id = ? ORDER BY s.line_start",
        (file_id,),
    ).fetchall()

    sym_ids = [s["id"] for s in symbols]
    if not sym_ids:
        return {
            "file_path": file_path,
            "symbol_count": 0,
            "callers": [],
            "callees": [],
            "tests": [],
            "coupling": [],
            "complexity": None,
        }

    # --- Callers: symbols in OTHER files that reference symbols in this file ---
    caller_rows = batched_in(
        conn,
        "SELECT e.target_id, s.name as caller_name, s.kind as caller_kind, "
        "f.path as caller_file, s.line_start as caller_line, "
        "ts.name as target_name "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "JOIN symbols ts ON e.target_id = ts.id "
        "WHERE e.target_id IN ({ph}) AND s.file_id != ?",
        sym_ids,
        post=[file_id],
    )

    # Group callers by source file
    callers_by_file = defaultdict(list)
    for r in caller_rows:
        if not is_test_file(r["caller_file"]):
            callers_by_file[r["caller_file"]].append(r["target_name"])

    callers = []
    for cfile, targets in sorted(callers_by_file.items()):
        unique_targets = sorted(set(targets))
        callers.append({
            "file": cfile,
            "symbols": unique_targets,
            "count": len(unique_targets),
        })

    # --- Callees: symbols in OTHER files that this file's symbols reference ---
    callee_rows = batched_in(
        conn,
        "SELECT e.source_id, s.name as callee_name, s.kind as callee_kind, "
        "f.path as callee_file, s.line_start as callee_line "
        "FROM edges e "
        "JOIN symbols s ON e.target_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE e.source_id IN ({ph}) AND s.file_id != ?",
        sym_ids,
        post=[file_id],
    )

    # Group callees by target file
    callees_by_file = defaultdict(list)
    for r in callee_rows:
        callees_by_file[r["callee_file"]].append(r["callee_name"])

    callees = []
    for cfile, names in sorted(callees_by_file.items()):
        unique_names = sorted(set(names))
        callees.append({
            "file": cfile,
            "symbols": unique_names,
            "count": len(unique_names),
        })

    # --- Tests: test files that reference any symbol in this file ---
    test_caller_rows = batched_in(
        conn,
        "SELECT DISTINCT f.path "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE e.target_id IN ({ph}) AND s.file_id != ?",
        sym_ids,
        post=[file_id],
    )

    direct_tests = sorted(set(
        r["path"] for r in test_caller_rows if is_test_file(r["path"])
    ))

    # Also check file_edges for file-level test importers
    test_importers = conn.execute(
        "SELECT f.path FROM file_edges fe "
        "JOIN files f ON fe.source_file_id = f.id "
        "WHERE fe.target_file_id = ?",
        (file_id,),
    ).fetchall()
    file_level_tests = sorted(set(
        r["path"] for r in test_importers if is_test_file(r["path"])
    ))

    # Merge direct + file-level, mark kind
    test_set = set()
    tests = []
    for t in direct_tests:
        test_set.add(t)
        tests.append({"file": t, "kind": "direct"})
    for t in file_level_tests:
        if t not in test_set:
            test_set.add(t)
            tests.append({"file": t, "kind": "file-level"})

    # --- Coupling ---
    coupling = _get_coupling(conn, file_path, limit=10)

    # --- Complexity summary ---
    metrics_rows = batched_in(
        conn,
        "SELECT sm.* FROM symbol_metrics sm "
        "WHERE sm.symbol_id IN ({ph})",
        sym_ids,
    )

    complexity = None
    if metrics_rows:
        cc_values = [r["cognitive_complexity"] for r in metrics_rows]
        threshold = 15
        complexity = {
            "avg": round(sum(cc_values) / len(cc_values), 1),
            "max": max(cc_values),
            "count_above_threshold": sum(1 for v in cc_values if v > threshold),
            "threshold": threshold,
            "measured_symbols": len(cc_values),
        }

    return {
        "file_path": file_path,
        "language": frow["language"],
        "line_count": frow["line_count"],
        "symbol_count": len(symbols),
        "callers": callers,
        "callees": callees,
        "tests": tests,
        "coupling": coupling,
        "complexity": complexity,
    }


def _output_file_context_text(data):
    """Render --for-file context as text."""
    click.echo(
        f"Context for {data['file_path']} "
        f"({data['symbol_count']} symbols):"
    )
    click.echo()

    # Callers
    callers = data["callers"]
    if callers:
        click.echo(f"Callers ({len(callers)} unique files):")
        for c in callers[:20]:
            syms = ", ".join(c["symbols"][:3])
            if len(c["symbols"]) > 3:
                syms += f" +{len(c['symbols']) - 3} more"
            click.echo(f"  {c['file']:<50s} \u2192 {syms}")
        if len(callers) > 20:
            click.echo(f"  (+{len(callers) - 20} more)")
        click.echo()
    else:
        click.echo("Callers: (none)")
        click.echo()

    # Callees
    callees = data["callees"]
    if callees:
        click.echo(f"Callees ({len(callees)} unique files):")
        for c in callees[:20]:
            syms = ", ".join(c["symbols"][:3])
            if len(c["symbols"]) > 3:
                syms += f" +{len(c['symbols']) - 3} more"
            click.echo(f"  {c['file']:<50s} \u2190 {syms}")
        if len(callees) > 20:
            click.echo(f"  (+{len(callees) - 20} more)")
        click.echo()
    else:
        click.echo("Callees: (none)")
        click.echo()

    # Tests
    tests = data["tests"]
    if tests:
        direct = sum(1 for t in tests if t["kind"] == "direct")
        file_lvl = sum(1 for t in tests if t["kind"] == "file-level")
        click.echo(f"Tests ({direct} direct, {file_lvl} file-level):")
        for t in tests:
            click.echo(f"  {t['file']} ({t['kind']})")
        click.echo()
    else:
        click.echo("Tests: (none)")
        click.echo()

    # Coupling
    coupling = data["coupling"]
    if coupling:
        click.echo(f"Coupling ({len(coupling)} partners):")
        rows = [
            [c["path"], str(c["cochange_count"]), f"{c['strength']:.0%}"]
            for c in coupling[:10]
        ]
        click.echo(format_table(["file", "co-changes", "strength"], rows))
        click.echo()

    # Complexity
    cx = data["complexity"]
    if cx:
        click.echo(
            f"Complexity: avg={cx['avg']}, max={cx['max']}, "
            f"{cx['count_above_threshold']} above threshold "
            f"(>{cx['threshold']})"
        )
        click.echo()


def _output_file_context_json(data, budget=0):
    """Render --for-file context as JSON."""
    summary = {
        "symbol_count": data["symbol_count"],
        "caller_files": len(data["callers"]),
        "callee_files": len(data["callees"]),
        "test_files": len(data["tests"]),
        "coupling_partners": len(data["coupling"]),
    }
    if data["complexity"]:
        summary["complexity_avg"] = data["complexity"]["avg"]
        summary["complexity_max"] = data["complexity"]["max"]

    click.echo(to_json(json_envelope("context",
        summary=summary,
        budget=budget,
        mode="file",
        file=data["file_path"],
        language=data.get("language"),
        line_count=data.get("line_count"),
        symbol_count=data["symbol_count"],
        callers=data["callers"],
        callees=data["callees"],
        tests=data["tests"],
        coupling=data["coupling"],
        complexity=data["complexity"],
    )))


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command()
@click.argument('names', nargs=-1)
@click.option(
    '--task', 'task',
    type=click.Choice(_TASK_CHOICES, case_sensitive=False),
    default=None,
    help='Tailor context to a specific task intent: '
         'refactor, debug, extend, review, understand.',
)
@click.option(
    '--for-file', 'for_file', type=str, default=None,
    help='Get aggregated context for an entire file instead of a symbol.',
)
@click.option(
    '--session-hint', 'session_hint', type=str, default="",
    help='Optional conversation hint used to personalize files-to-read ranking.',
)
@click.option(
    '--recent-symbol', 'recent_symbols', multiple=True,
    help='Recently discussed symbol(s) to bias context ranking (repeatable).',
)
@click.option(
    '--no-propagation', 'no_propagation', is_flag=True, default=False,
    help='Disable call-graph propagation ranking (use legacy PageRank-only mode).',
)
@click.pass_context
def context(ctx, names, task, for_file, session_hint, recent_symbols, no_propagation):
    """Get the minimal context needed to safely modify a symbol.

    Returns definition, callers, callees, tests, and the exact files
    to read -- everything an AI agent needs in one shot.

    Pass multiple symbol names for batch mode with shared callers analysis.

    Use --for-file PATH to get file-level context: callers grouped by
    source file, callees grouped by target file, tests, coupling partners,
    and a complexity summary across all symbols in the file.

    Use --session-hint and --recent-symbol to personalize files-to-read
    ranking for long conversations.

    Use --task to tailor the context to a specific agent intent:

    \b
      refactor   - callers, siblings, complexity, coupling (safe modification)
      debug      - callees, callers, affected tests (execution tracing)
      extend     - full graph, similar symbols, conventions (integration)
      review     - complexity, churn, blast radius, coupling (risk assessment)
      understand - docstring, cluster, architecture role (comprehension)
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    token_budget = ctx.obj.get('budget', 0) if ctx.obj else 0
    ensure_index()

    # --- File-level context mode ---
    if for_file:
        with open_db(readonly=True) as conn:
            frow = _resolve_file(conn, for_file)
            if frow is None:
                click.echo(file_not_found_hint(for_file))
                raise SystemExit(1)
            data = _gather_file_level_context(conn, frow)
            if json_mode:
                _output_file_context_json(data, budget=token_budget)
            else:
                _output_file_context_text(data)
        return

    # Require at least one symbol name if --for-file is not used
    if not names:
        click.echo(ctx.get_help())
        return

    with open_db(readonly=True) as conn:
        # Resolve all symbols
        resolved = []
        for name in names:
            sym = find_symbol(conn, name)
            if sym is None:
                click.echo(symbol_not_found(conn, name, json_mode=json_mode))
                raise SystemExit(1)
            resolved.append(sym)

        # Gather context for each
        use_propagation = not no_propagation
        contexts = [
            _gather_symbol_context(
                conn,
                sym,
                task=task,
                session_hint=session_hint,
                recent_symbols=recent_symbols,
                use_propagation=use_propagation,
            )
            for sym in resolved
        ]

        # --- Batch mode (task extras are ignored) ---
        if len(contexts) > 1:
            if task and not json_mode:
                click.echo(
                    "Warning: task-specific extra sections are ignored in batch mode "
                    "(multiple symbols). Ranking still uses task/session hints.",
                    err=True,
                )
            shared_callers, shared_callees, scored_files = _batch_context(
                conn, contexts,
                task=task,
                session_hint=session_hint,
                recent_symbols=recent_symbols,
                use_propagation=use_propagation,
            )

            if json_mode:
                click.echo(to_json(json_envelope("context",
                    budget=token_budget,
                    summary={
                        "symbols": len(contexts),
                        "shared_callers": len(shared_callers),
                        "shared_callees": len(shared_callees),
                        "files_to_read": len(scored_files),
                    },
                    mode="batch",
                    symbols=[
                        {
                            "name": c["sym"]["qualified_name"] or c["sym"]["name"],
                            "kind": c["sym"]["kind"],
                            "location": loc(c["sym"]["file_path"], c["line_start"]),
                            "callers": [
                                {"name": cr["name"], "kind": cr["kind"],
                                 "location": loc(cr["file_path"], cr["edge_line"] or cr["line_start"])}
                                for cr in c["non_test_callers"][:20]
                            ],
                            "callees": [
                                {"name": ce["name"], "kind": ce["kind"],
                                 "location": loc(ce["file_path"], ce["line_start"])}
                                for ce in c["callees"][:15]
                            ],
                            "tests": len(c["test_callers"]),
                        }
                        for c in contexts
                    ],
                    shared_callers=[
                        {"name": c["name"], "kind": c["kind"],
                         "location": loc(c["file_path"], c["line_start"])}
                        for c in shared_callers
                    ],
                    shared_callees=[
                        {"name": c["name"], "kind": c["kind"],
                         "location": loc(c["file_path"], c["line_start"])}
                        for c in shared_callees
                    ],
                    files_to_read=scored_files,
                )))
                return

            # Text batch output
            click.echo(f"=== Batch Context ({len(contexts)} symbols) ===\n")

            for c in contexts:
                s = c["sym"]
                sig = s["signature"] or ""
                click.echo(f"--- {s['name']} ---")
                click.echo(
                    f"  {abbrev_kind(s['kind'])}  "
                    f"{s['qualified_name'] or s['name']}"
                    f"{'  ' + sig if sig else ''}  "
                    f"{loc(s['file_path'], c['line_start'])}"
                )
                click.echo(
                    f"  Callers: {len(c['non_test_callers'])}  "
                    f"Callees: {len(c['callees'])}  "
                    f"Tests: {len(c['test_callers'])}"
                )
                click.echo()

            if shared_callers:
                click.echo(f"Shared callers ({len(shared_callers)}):")
                rows = [[abbrev_kind(c["kind"]), c["name"],
                         loc(c["file_path"], c["line_start"])]
                        for c in shared_callers[:15]]
                click.echo(format_table(["kind", "name", "location"], rows))
                click.echo()

            if shared_callees:
                click.echo(f"Shared callees ({len(shared_callees)}):")
                rows = [[abbrev_kind(c["kind"]), c["name"],
                         loc(c["file_path"], c["line_start"])]
                        for c in shared_callees[:15]]
                click.echo(format_table(["kind", "name", "location"], rows))
                click.echo()

            click.echo(f"Files to read ({len(scored_files)}):")
            for f in scored_files[:25]:
                reasons = ", ".join(f["reasons"])
                rel_str = f"{f['relevance']:.0%}" if f["relevance"] > 0 else ""
                click.echo(
                    f"  {f['path']:<50s} {rel_str:>5s}  ({reasons})"
                )
            if len(scored_files) > 25:
                click.echo(f"  (+{len(scored_files) - 25} more)")
            return

        # --- Single symbol mode ---
        c = contexts[0]
        sym = c["sym"]

        # Task mode: gather extras and render
        if task:
            extras = _gather_task_extras(conn, sym, c, task)
            if json_mode:
                _output_task_single_json(c, task, extras, budget=token_budget)
            else:
                _output_task_single_text(c, task, extras)
            return

        # --- Default single symbol mode (original behavior) ---
        # Inject annotations if any
        default_annotations = _gather_annotations(conn, sym=sym)

        line_start = c["line_start"]
        line_end = c["line_end"]
        non_test_callers = c["non_test_callers"]
        callees = c["callees"]
        test_callers = c["test_callers"]
        test_importers = c["test_importers"]
        siblings = c["siblings"]
        files_to_read = c["files_to_read"]
        skipped_callers = c["skipped_callers"]
        skipped_callees = c["skipped_callees"]

        if json_mode:
            _sym_name = sym["qualified_name"] or sym["name"]
            _next_steps = suggest_next_steps("context", {
                "symbol": _sym_name,
                "callers": len(non_test_callers),
            })
            click.echo(to_json(json_envelope("context",
                budget=token_budget,
                summary={
                    "callers": len(non_test_callers),
                    "callees": len(callees),
                    "tests": len(test_callers),
                    "files_to_read": len(files_to_read),
                },
                symbol=_sym_name,
                kind=sym["kind"],
                signature=sym["signature"] or "",
                location=loc(sym["file_path"], line_start),
                definition={
                    "file": sym["file_path"],
                    "start": line_start, "end": line_end,
                },
                callers=[
                    {"name": cr["name"], "kind": cr["kind"],
                     "location": loc(cr["file_path"], cr["edge_line"] or cr["line_start"]),
                     "edge_kind": cr["edge_kind"] or ""}
                    for cr in non_test_callers
                ],
                callees=[
                    {"name": ce["name"], "kind": ce["kind"],
                     "location": loc(ce["file_path"], ce["line_start"]),
                     "edge_kind": ce["edge_kind"] or ""}
                    for ce in callees
                ],
                tests=[
                    {"name": t["name"], "kind": t["kind"],
                     "location": loc(t["file_path"], t["line_start"]),
                     "edge_kind": t["edge_kind"] or ""}
                    for t in test_callers
                ],
                test_files=[r["path"] for r in test_importers],
                siblings=[
                    {"name": s["name"], "kind": s["kind"]}
                    for s in siblings[:10]
                ],
                annotations=default_annotations if default_annotations else [],
                files_to_read=[
                    {"path": f["path"], "start": f["start"],
                     "end": f["end"], "reason": f["reason"],
                     "score": f.get("score"), "rank": f.get("rank")}
                    for f in files_to_read
                ],
                next_steps=_next_steps,
            )))
            return

        # --- Text output ---
        sig = sym["signature"] or ""
        click.echo(f"=== Context for: {sym['name']} ===")
        click.echo(
            f"{abbrev_kind(sym['kind'])}  "
            f"{sym['qualified_name'] or sym['name']}"
            f"{'  ' + sig if sig else ''}  "
            f"{loc(sym['file_path'], line_start)}"
        )
        click.echo()

        _render_annotations_text(default_annotations)

        if non_test_callers:
            click.echo(f"Callers ({len(non_test_callers)}):")
            rows = []
            for cr in non_test_callers[:20]:
                rows.append([
                    abbrev_kind(cr["kind"]), cr["name"],
                    loc(cr["file_path"], cr["edge_line"] or cr["line_start"]),
                    cr["edge_kind"] or "",
                ])
            click.echo(format_table(["kind", "name", "location", "edge"], rows))
            if len(non_test_callers) > 20:
                click.echo(f"  (+{len(non_test_callers) - 20} more)")
            click.echo()
        else:
            click.echo("Callers: (none)")
            click.echo()

        if callees:
            click.echo(f"Callees ({len(callees)}):")
            rows = []
            for ce in callees[:15]:
                rows.append([
                    abbrev_kind(ce["kind"]), ce["name"],
                    loc(ce["file_path"], ce["line_start"]),
                    ce["edge_kind"] or "",
                ])
            click.echo(format_table(["kind", "name", "location", "edge"], rows))
            if len(callees) > 15:
                click.echo(f"  (+{len(callees) - 15} more)")
            click.echo()
        else:
            click.echo("Callees: (none)")
            click.echo()

        if test_callers or test_importers:
            click.echo(
                f"Tests ({len(test_callers)} direct, "
                f"{len(test_importers)} file-level):"
            )
            for t in test_callers:
                click.echo(
                    f"  {abbrev_kind(t['kind'])}  {t['name']}  "
                    f"{loc(t['file_path'], t['line_start'])}"
                )
            for ti in test_importers:
                click.echo(f"  file  {ti['path']}")
        else:
            click.echo("Tests: (none)")
        click.echo()

        if siblings:
            click.echo(f"Siblings ({len(siblings)} exports in same file):")
            for s in siblings[:10]:
                click.echo(f"  {abbrev_kind(s['kind'])}  {s['name']}")
            if len(siblings) > 10:
                click.echo(f"  (+{len(siblings) - 10} more)")
            click.echo()

        skipped_total = skipped_callers + skipped_callees
        extra = f", +{skipped_total} more" if skipped_total else ""
        click.echo(f"Files to read ({len(files_to_read)}{extra}):")
        for f in files_to_read:
            end_str = (
                f"-{f['end']}"
                if f["end"] and f["end"] != f["start"]
                else ""
            )
            lr = f":{f['start']}{end_str}" if f["start"] else ""
            click.echo(
                f"  {f['path']:<50s} {lr:<12s} ({f['reason']})"
            )

        _sym_name = sym["qualified_name"] or sym["name"]
        _next_steps = suggest_next_steps("context", {
            "symbol": _sym_name,
            "callers": len(non_test_callers),
        })
        _ns_text = format_next_steps_text(_next_steps)
        if _ns_text:
            click.echo(_ns_text)
