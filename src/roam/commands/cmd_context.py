"""Get the minimal context needed to safely modify a symbol.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because context outputs are invocation-scoped read-order advice
(files_to_read[] ranked, caller/callee summaries, test mappings) for
agent orientation — not per-location code findings. Output is advisory
ranking, not defect annotation. See action.yml _SUPPORTED_SARIF allowlist
and W1155 audit memo.
"""

from __future__ import annotations

import os
from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file
from roam.commands.context_helpers import (
    batch_context,
    gather_annotations,
    gather_symbol_context,
    get_affected_tests_bfs,
    get_blast_radius,
    get_cluster_info,
    get_coupling,
    get_entry_points_reaching,
    get_file_churn,
    get_file_context,
    get_graph_metrics,
    get_similar_symbols,
    get_symbol_metrics,
)
from roam.commands.next_steps import format_next_steps_text, suggest_next_steps
from roam.commands.resolve import ensure_index, file_not_found_hint, find_symbol, symbol_not_found
from roam.db.connection import batched_in, open_db
from roam.db.queries import FILE_BY_PATH
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)
from roam.output.metric_definitions import CALLER_METRIC_RAW

_TASK_CHOICES = ["refactor", "debug", "extend", "review", "understand"]


# ---------------------------------------------------------------------------
# Small rendering helpers (text) — kept as-is, already well-factored
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
        f"  commits={churn['commit_count']}  total_churn={churn['total_churn']}  authors={churn['distinct_authors']}"
    )
    click.echo()


def _render_coupling_text(coupling):
    if not coupling:
        return
    click.echo(f"Temporal coupling ({len(coupling)} partners):")
    rows = [[c["path"], f"{c['strength']:.0%}", str(c["cochange_count"])] for c in coupling[:10]]
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
        click.echo(f"  {t['kind']:<12s} {t['file']}::{t['symbol']}  ({hops} hop{'s' if hops != 1 else ''}{via_str})")
    if len(tests) > 15:
        click.echo(f"  (+{len(tests) - 15} more)")
    click.echo()


def _render_blast_radius_text(blast):
    if not blast:
        return
    click.echo("Blast radius:")
    click.echo(f"  {blast['dependent_symbols']} dependent symbols in {blast['dependent_files']} files")
    click.echo()


def _render_cluster_text(cluster):
    if not cluster:
        return
    click.echo(f"Cluster: {cluster['cluster_label']} ({cluster['cluster_size']} symbols)")
    names = ", ".join(m["name"] for m in cluster["top_members"][:6])
    if cluster["cluster_size"] > 6:
        names += f" +{cluster['cluster_size'] - 6} more"
    click.echo(f"  members: {names}")
    click.echo()


def _render_similar_symbols_text(similar):
    if not similar:
        return
    click.echo(f"Similar symbols ({len(similar)}):")
    rows = [[abbrev_kind(s["kind"]), s["name"], s["location"]] for s in similar[:10]]
    click.echo(format_table(["kind", "name", "location"], rows))
    click.echo()


def _render_entry_points_text(entries):
    if not entries:
        return
    click.echo(f"Entry points reaching this ({len(entries)}):")
    rows = [[abbrev_kind(e["kind"]), e["name"], e["location"]] for e in entries]
    click.echo(format_table(["kind", "name", "location"], rows))
    click.echo()


def _render_file_context_text(file_context):
    if not file_context:
        return
    click.echo(f"File context ({len(file_context)} other exports):")
    for fc in file_context[:15]:
        doc = " [documented]" if fc["has_docstring"] else ""
        click.echo(f"  {abbrev_kind(fc['kind'])}  {fc['name']}  L{fc['line']}{doc}")
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
# Gather functions — one per mode, each returns a standardised data dict
# ---------------------------------------------------------------------------


def _gather_single(conn, sym, task, session_hint, recent_symbols, use_propagation):
    """Gather all context for a single symbol.

    Returns a data dict with mode='single', plus all context fields
    including task extras (always gathered, regardless of task value).
    """
    c = gather_symbol_context(
        conn,
        sym,
        task=task,
        session_hint=session_hint,
        recent_symbols=recent_symbols,
        use_propagation=use_propagation,
    )

    sym_id = sym["id"]
    file_path = sym["file_path"]

    # Always gather all extras — task just passes through for ranking/JSON
    annotations = gather_annotations(conn, sym=sym)

    try:
        docstring = sym["docstring"] or None
    except (KeyError, IndexError):
        docstring = None

    try:
        complexity = get_symbol_metrics(conn, sym_id)
    except Exception:
        complexity = None

    try:
        graph_centrality = get_graph_metrics(conn, sym_id)
    except Exception:
        graph_centrality = None

    try:
        git_churn = get_file_churn(conn, file_path)
    except Exception:
        git_churn = None

    try:
        coupling = get_coupling(conn, file_path, limit=10)
    except Exception:
        coupling = []

    try:
        affected_tests = get_affected_tests_bfs(conn, sym_id)
    except Exception:
        affected_tests = []

    try:
        blast_radius = get_blast_radius(conn, sym_id)
    except Exception:
        blast_radius = None

    try:
        cluster = get_cluster_info(conn, sym_id)
    except Exception:
        cluster = None

    try:
        similar_symbols = get_similar_symbols(conn, sym, limit=10)
    except Exception:
        similar_symbols = []

    try:
        entry_points_reaching = get_entry_points_reaching(conn, sym_id, limit=5)
    except Exception:
        entry_points_reaching = []

    try:
        fid = sym["file_id"]
    except (KeyError, IndexError):
        fid = conn.execute("SELECT file_id FROM symbols WHERE id = ?", (sym_id,)).fetchone()[0]

    try:
        file_context_syms = get_file_context(conn, fid, sym_id)
    except Exception:
        file_context_syms = []

    next_steps = suggest_next_steps(
        "context",
        {
            "symbol": sym["qualified_name"] or sym["name"],
            "callers": len(c["non_test_callers"]),
        },
    )

    return {
        "mode": "single",
        "task": task,
        # raw gather_symbol_context fields
        "sym": c["sym"],
        "line_start": c["line_start"],
        "line_end": c["line_end"],
        "callers": c["callers"],
        "callees": c["callees"],
        "non_test_callers": c["non_test_callers"],
        "test_callers": c["test_callers"],
        "test_importers": c["test_importers"],
        "siblings": c["siblings"],
        "files_to_read": c["files_to_read"],
        "skipped_callers": c["skipped_callers"],
        "skipped_callees": c["skipped_callees"],
        # extras — always present
        "annotations": annotations,
        "docstring": docstring,
        "complexity": complexity,
        "graph_centrality": graph_centrality,
        "git_churn": git_churn,
        "coupling": coupling,
        "affected_tests": affected_tests,
        "blast_radius": blast_radius,
        "cluster": cluster,
        "similar_symbols": similar_symbols,
        "entry_points_reaching": entry_points_reaching,
        "file_context_syms": file_context_syms,
        "next_steps": next_steps,
    }


def _gather_file(conn, frow):
    """Gather context for an entire file.

    Returns a data dict with mode='file'.
    """
    file_id = frow["id"]
    file_path = frow["path"]

    symbols = conn.execute(
        "SELECT s.*, f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.file_id = ? ORDER BY s.line_start",
        (file_id,),
    ).fetchall()

    sym_ids = [s["id"] for s in symbols]
    if not sym_ids:
        return {
            "mode": "file",
            "file_path": file_path,
            "language": frow["language"],
            "line_count": frow["line_count"],
            "symbol_count": 0,
            "callers": [],
            "callees": [],
            "tests": [],
            "coupling": [],
            "complexity": None,
        }

    # Callers: symbols in OTHER files that reference symbols in this file
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

    callers_by_file = defaultdict(list)
    for r in caller_rows:
        if not is_test_file(r["caller_file"]):
            callers_by_file[r["caller_file"]].append(r["target_name"])

    callers = []
    for cfile, targets in sorted(callers_by_file.items()):
        unique_targets = sorted(set(targets))
        callers.append({"file": cfile, "symbols": unique_targets, "count": len(unique_targets)})

    # Callees: symbols in OTHER files that this file's symbols reference
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

    callees_by_file = defaultdict(list)
    for r in callee_rows:
        callees_by_file[r["callee_file"]].append(r["callee_name"])

    callees = []
    for cfile, names in sorted(callees_by_file.items()):
        unique_names = sorted(set(names))
        callees.append({"file": cfile, "symbols": unique_names, "count": len(unique_names)})

    # Tests: test files that reference any symbol in this file
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
    direct_tests = sorted(set(r["path"] for r in test_caller_rows if is_test_file(r["path"])))

    test_importers = conn.execute(
        "SELECT f.path FROM file_edges fe JOIN files f ON fe.source_file_id = f.id WHERE fe.target_file_id = ?",
        (file_id,),
    ).fetchall()
    file_level_tests = sorted(set(r["path"] for r in test_importers if is_test_file(r["path"])))

    test_set = set()
    tests = []
    for t in direct_tests:
        test_set.add(t)
        tests.append({"file": t, "kind": "direct"})
    for t in file_level_tests:
        if t not in test_set:
            test_set.add(t)
            tests.append({"file": t, "kind": "file-level"})

    coupling = get_coupling(conn, file_path, limit=10)

    metrics_rows = batched_in(
        conn,
        "SELECT sm.* FROM symbol_metrics sm WHERE sm.symbol_id IN ({ph})",
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
        "mode": "file",
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


def _gather_batch(conn, resolved, task, session_hint, recent_symbols, use_propagation):
    """Gather context for multiple symbols (batch mode).

    Returns a data dict with mode='batch'.
    """
    contexts = [
        gather_symbol_context(
            conn,
            sym,
            task=task,
            session_hint=session_hint,
            recent_symbols=recent_symbols,
            use_propagation=use_propagation,
        )
        for sym in resolved
    ]

    try:
        shared_callers, shared_callees, scored_files = batch_context(
            conn,
            contexts,
            task=task,
            session_hint=session_hint,
            recent_symbols=recent_symbols,
            use_propagation=use_propagation,
        )
    except Exception:
        shared_callers, shared_callees, scored_files = [], [], []
        for c in contexts:
            for f in c["files_to_read"]:
                scored_files.append(f)

    return {
        "mode": "batch",
        "task": task,
        "contexts": contexts,
        "shared_callers": shared_callers,
        "shared_callees": shared_callees,
        "files_to_read": scored_files,
    }


# ---------------------------------------------------------------------------
# Render functions — text and JSON, one per mode
# ---------------------------------------------------------------------------


def _render_async_badge(sym, row_keys) -> None:
    """single-line async badge above the signature."""
    if "is_async" in row_keys and sym["is_async"]:
        click.echo("  [async coroutine]")


def _render_idiom_badge(sym, sym_kind: str, decorators_str: str, row_keys) -> None:
    """surface model-class / fixture / param-test badges."""
    try:
        from roam.catalog.python_idioms import fixture_kind, is_model_class
    except Exception:
        return
    if sym_kind == "class":
        sig_text = sym["signature"] if "signature" in row_keys else ""
        try:
            is_model, kind_label = is_model_class(sig_text, decorators_str)
        except Exception:
            return
        if is_model and kind_label:
            click.echo(f"  [{kind_label} model]")
    elif sym_kind in ("function", "method"):
        try:
            fkind = fixture_kind(decorators_str)
        except Exception:
            return
        if fkind:
            click.echo(f"  [{fkind}]")


def _split_decorators_paren_aware(decorators_str: str) -> list[str]:
    """paren-aware split.

    ``@parametrize("a,b", [...])`` has commas inside its argument; the
    naive ``str.split(",")`` would break it into fragments. Track
    bracket depth so we only split on top-level commas.
    """
    decos: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in decorators_str:
        if ch == "," and depth == 0:
            if current:
                decos.append("".join(current).strip())
                current = []
        else:
            current.append(ch)
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
    if current:
        decos.append("".join(current).strip())
    return decos


def _render_decorators_block(decorators_str: str) -> None:
    """print up to 5 decorator first-lines for the symbol."""
    if not decorators_str:
        return
    for d in _split_decorators_paren_aware(decorators_str)[:5]:
        first_line = d.splitlines()[0] if d else ""
        if len(d.splitlines()) > 1:
            first_line += "..."
        click.echo(f"  {first_line}")


def _render_text(data):
    """Print text output for any mode."""
    mode = data["mode"]

    if data.get("inline_mode"):
        _render_inline_text(data)
        return

    if mode == "file":
        _render_file_text(data)
    elif mode == "batch":
        _render_batch_text(data)
    else:
        _render_single_text(data)


def _render_inline_text(data) -> None:
    """concatenate the recommended files into one paste-ready block.

    Each file is preceded by a header line ``=== <path> ===`` and each
    line is prefixed with a 1-based line number for reference. Files
    that can't be read are skipped with a one-line warning.
    """
    files = data.get("files_to_read") or []
    sym = data.get("sym") or {}
    sym_name = sym["name"] if hasattr(sym, "keys") and "name" in sym.keys() else ""
    click.echo(f"VERDICT: inline context for {sym_name} — {len(files)} file(s)")
    for f in files:
        path = f.get("path") if isinstance(f, dict) else getattr(f, "path", None)
        if not path:
            continue
        click.echo()
        click.echo(f"=== {path} ===")
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError as exc:
            click.echo(f"  [unreadable: {exc}]")
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            click.echo(f"{i:>5}  {line}")


def _render_json(data, budget=0):
    """Print JSON output for any mode."""
    mode = data["mode"]

    if mode == "file":
        _render_file_json(data, budget)
    elif mode == "batch":
        _render_batch_json(data, budget)
    else:
        _render_single_json(data, budget)


# ---------------------------------------------------------------------------
# Single-symbol text + JSON
# ---------------------------------------------------------------------------


def _table_budget(data) -> int:
    """read the threaded ``--budget`` so format_table honors it."""
    try:
        return int(data.get("token_budget") or 0)
    except (TypeError, ValueError):
        return 0


def _render_single_header(data, sym, task_suffix, sig):
    """Header block: VERDICT + signature line + badges + decorators."""
    files_to_read = data["files_to_read"]
    non_test_callers = data["non_test_callers"]
    verdict = f"{len(files_to_read)} files, {len(non_test_callers)} callers for {sym['name']}{task_suffix}"
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"=== Context for: {sym['name']}{task_suffix} ===")
    _row_keys = sym.keys() if hasattr(sym, "keys") else []
    decorators_str = (sym["decorators"] if "decorators" in _row_keys else "") or ""
    sym_kind = sym["kind"] if "kind" in _row_keys else ""
    _render_async_badge(sym, _row_keys)
    _render_idiom_badge(sym, sym_kind, decorators_str, _row_keys)
    _render_decorators_block(decorators_str)
    click.echo(
        f"{abbrev_kind(sym['kind'])}  "
        f"{sym['qualified_name'] or sym['name']}"
        f"{'  ' + sig if sig else ''}  "
        f"{loc(sym['file_path'], data['line_start'])}"
    )
    click.echo()
    return _row_keys, decorators_str, sym_kind


def _render_callers_block(non_test_callers, data):
    """Callers section."""
    if non_test_callers:
        click.echo(f"Callers ({len(non_test_callers)}):")
        rows = [
            [
                abbrev_kind(cr["kind"]),
                cr["name"],
                loc(cr["file_path"], cr["edge_line"] or cr["line_start"]),
                cr["edge_kind"] or "",
            ]
            for cr in non_test_callers[:20]
        ]
        click.echo(format_table(["kind", "name", "location", "edge"], rows, budget=_table_budget(data)))
        if len(non_test_callers) > 20:
            click.echo(f"  (+{len(non_test_callers) - 20} more)")
        click.echo()
    else:
        click.echo("Callers: (none)")
        click.echo()


def _render_callees_block(callees, data):
    """Callees section."""
    if callees:
        click.echo(f"Callees ({len(callees)}):")
        rows = [
            [
                abbrev_kind(ce["kind"]),
                ce["name"],
                loc(ce["file_path"], ce["line_start"]),
                ce["edge_kind"] or "",
            ]
            for ce in callees[:15]
        ]
        click.echo(format_table(["kind", "name", "location", "edge"], rows, budget=_table_budget(data)))
        if len(callees) > 15:
            click.echo(f"  (+{len(callees) - 15} more)")
        click.echo()
    else:
        click.echo("Callees: (none)")
        click.echo()


def _render_tests_block(test_callers, test_importers):
    """Tests section."""
    if test_callers or test_importers:
        click.echo(f"Tests ({len(test_callers)} direct, {len(test_importers)} file-level):")
        for t in test_callers:
            click.echo(f"  {abbrev_kind(t['kind'])}  {t['name']}  {loc(t['file_path'], t['line_start'])}")
        for ti in test_importers:
            click.echo(f"  file  {ti['path']}")
    else:
        click.echo("Tests: (none)")
    click.echo()


def _render_siblings_block(siblings):
    """Siblings section."""
    if not siblings:
        return
    click.echo(f"Siblings ({len(siblings)} exports in same file):")
    for s in siblings[:10]:
        click.echo(f"  {abbrev_kind(s['kind'])}  {s['name']}")
    if len(siblings) > 10:
        click.echo(f"  (+{len(siblings) - 10} more)")
    click.echo()


def _render_model_fields_block(sym, sym_kind, decorators_str, _row_keys):
    """Pydantic / dataclass / attrs / TypedDict / NamedTuple field listing.

    When the symbol is a model class, surface its fields directly so an
    agent immediately sees the shape (not just as siblings). v12.7 pivot.
    """
    if sym_kind != "class":
        return
    try:
        from roam.catalog.python_idioms import is_model_class

        sig_for_model = sym["signature"] if "signature" in _row_keys else ""
        is_model, _label = is_model_class(sig_for_model, decorators_str)
    except Exception:
        is_model = False
    if not is_model:
        return
    from roam.db.connection import open_db as _open_db

    try:
        with _open_db(readonly=True) as _conn:
            field_rows = _conn.execute(
                "SELECT name, default_value FROM symbols WHERE parent_id = ? AND kind = 'property' ORDER BY line_start",
                (sym["id"],),
            ).fetchall()
    except Exception:
        field_rows = []
    if not field_rows:
        return
    click.echo(f"Fields ({len(field_rows)}):")
    for fr in field_rows[:20]:
        fname = fr["name"] if "name" in fr.keys() else fr[0]
        fdef = (fr["default_value"] if "default_value" in fr.keys() else fr[1]) or ""
        badge = f" = {fdef}" if fdef and fdef not in ("None",) else ""
        click.echo(f"  {fname}{badge}")
    if len(field_rows) > 20:
        click.echo(f"  (+{len(field_rows) - 20} more)")
    click.echo()


def _render_files_to_read_block(files_to_read, skipped_callers, skipped_callees):
    """Files-to-read footer."""
    skipped_total = skipped_callers + skipped_callees
    extra_label = f", +{skipped_total} more" if skipped_total else ""
    click.echo(f"Files to read ({len(files_to_read)}{extra_label}):")
    for f in files_to_read:
        end_str = f"-{f['end']}" if f["end"] and f["end"] != f["start"] else ""
        lr = f":{f['start']}{end_str}" if f["start"] else ""
        click.echo(f"  {f['path']:<50s} {lr:<12s} ({f['reason']})")


def _render_extras_block(data):
    """All the non-core analyses (complexity, churn, coupling, etc.).

    Each helper handles its own None/empty input gracefully.
    """
    _render_complexity_text(data.get("complexity"))
    _render_graph_centrality_text(data.get("graph_centrality"))
    _render_churn_text(data.get("git_churn"))
    _render_coupling_text(data.get("coupling"))
    _render_affected_tests_text(data.get("affected_tests") or [])
    _render_blast_radius_text(data.get("blast_radius"))
    _render_cluster_text(data.get("cluster"))
    _render_similar_symbols_text(data.get("similar_symbols") or [])
    _render_entry_points_text(data.get("entry_points_reaching") or [])
    _render_file_context_text(data.get("file_context_syms") or [])


def _render_single_text(data):
    sym = data["sym"]
    task = data["task"]
    sig = sym["signature"] or ""
    task_suffix = f" (task={task})" if task else ""

    _row_keys, decorators_str, sym_kind = _render_single_header(data, sym, task_suffix, sig)

    _render_annotations_text(data.get("annotations"))

    if task == "understand" and data.get("docstring"):
        click.echo("Docstring:")
        for line in data["docstring"].strip().splitlines()[:10]:
            click.echo(f"  {line}")
        click.echo()

    _render_callers_block(data["non_test_callers"], data)
    _render_callees_block(data["callees"], data)
    _render_tests_block(data["test_callers"], data["test_importers"])
    _render_siblings_block(data["siblings"])
    _render_model_fields_block(sym, sym_kind, decorators_str, _row_keys)
    _render_extras_block(data)
    _render_files_to_read_block(data["files_to_read"], data["skipped_callers"], data["skipped_callees"])

    ns_text = format_next_steps_text(data.get("next_steps") or [])
    if ns_text:
        click.echo(ns_text)


def _render_single_json(data, budget=0):
    sym = data["sym"]
    task = data["task"]
    non_test_callers = data["non_test_callers"]
    callees = data["callees"]
    test_callers = data["test_callers"]
    test_importers = data["test_importers"]
    siblings = data["siblings"]
    files_to_read = data["files_to_read"]

    task_suffix = f" (task={task})" if task else ""
    # W1245 Pattern-2 variant-D: pull the single-symbol resolution tier
    # threaded by the CLI entry. Default to ``"symbol"`` so any caller
    # building ``data`` independently keeps backwards compatibility.
    resolutions = data.get("_resolutions") or []
    res0 = resolutions[0] if resolutions else None
    resolution_tier = (res0 or {}).get("tier", "symbol")
    resolved_target = (res0 or {}).get("resolved_name") or (sym["qualified_name"] or sym["name"])
    resolution_block = resolution_disclosure(resolution_tier, target=resolved_target)
    fuzzy_suffix = " [fuzzy resolution]" if resolution_tier != "symbol" else ""
    verdict = (
        f"{len(files_to_read)} files, {len(non_test_callers)} callers for {sym['name']}{task_suffix}{fuzzy_suffix}"
    )

    summary = {
        "verdict": verdict,
        "callers": len(non_test_callers),
        "caller_metric_definition": CALLER_METRIC_RAW,
        "callees": len(callees),
        "tests": len(test_callers),
        "files_to_read": len(files_to_read),
        # W1245 Pattern-2 variant-D resolution disclosure.
        **resolution_block,
    }
    if task:
        summary["task"] = task
    if data.get("blast_radius"):
        summary["blast_radius_symbols"] = data["blast_radius"]["dependent_symbols"]
        summary["blast_radius_files"] = data["blast_radius"]["dependent_files"]
    if data.get("affected_tests") is not None:
        summary["affected_tests_total"] = len(data["affected_tests"])
    if data.get("coupling"):
        summary["coupling_partners"] = len(data["coupling"])

    payload = {
        "symbol": sym["qualified_name"] or sym["name"],
        "kind": sym["kind"],
        "signature": sym["signature"] or "",
        "location": loc(sym["file_path"], data["line_start"]),
        "definition": {
            "file": sym["file_path"],
            "start": data["line_start"],
            "end": data["line_end"],
        },
        "callers": [
            {
                "name": cr["name"],
                "kind": cr["kind"],
                "location": loc(cr["file_path"], cr["edge_line"] or cr["line_start"]),
                "edge_kind": cr["edge_kind"] or "",
            }
            for cr in non_test_callers
        ],
        "callees": [
            {
                "name": ce["name"],
                "kind": ce["kind"],
                "location": loc(ce["file_path"], ce["line_start"]),
                "edge_kind": ce["edge_kind"] or "",
            }
            for ce in callees
        ],
        "tests": [
            {
                "name": t["name"],
                "kind": t["kind"],
                "location": loc(t["file_path"], t["line_start"]),
                "edge_kind": t["edge_kind"] or "",
            }
            for t in test_callers
        ],
        "test_files": [r["path"] for r in test_importers],
        "siblings": [{"name": s["name"], "kind": s["kind"]} for s in siblings[:10]],
        "annotations": data.get("annotations") or [],
        "files_to_read": [
            {
                "path": f["path"],
                "start": f["start"],
                "end": f["end"],
                "reason": f["reason"],
                "score": f.get("score"),
                "rank": f.get("rank"),
            }
            for f in files_to_read
        ],
        "next_steps": data.get("next_steps") or [],
    }

    # Include all extras present in data (omit None / empty)
    for key in (
        "task",
        "docstring",
        "complexity",
        "graph_centrality",
        "git_churn",
        "blast_radius",
        "cluster",
    ):
        val = data.get(key)
        if val is not None:
            payload[key] = val

    for key in (
        "coupling",
        "affected_tests",
        "similar_symbols",
        "entry_points_reaching",
        "file_context_syms",
    ):
        val = data.get(key)
        if val:
            payload[key] = val

    # W1245 Pattern-2 variant-D: mirror the resolution disclosure into the
    # top-level envelope so consumers reading either ``summary`` or the
    # top-level fields get the same signal.
    payload.update(resolution_block)

    click.echo(to_json(json_envelope("context", summary=summary, budget=budget, **payload)))


# ---------------------------------------------------------------------------
# File-level text + JSON
# ---------------------------------------------------------------------------


def _render_file_text(data):
    fname = os.path.basename(data["file_path"])
    verdict = (
        f"{fname}: {data['symbol_count']} symbols, {len(data['callers'])} caller files, {len(data['tests'])} test files"
    )
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"Context for {data['file_path']} ({data['symbol_count']} symbols):")
    click.echo()

    callers = data["callers"]
    if callers:
        click.echo(f"Callers ({len(callers)} unique files):")
        for c in callers[:20]:
            syms = ", ".join(c["symbols"][:3])
            if len(c["symbols"]) > 3:
                syms += f" +{len(c['symbols']) - 3} more"
            click.echo(f"  {c['file']:<50s} -> {syms}")
        if len(callers) > 20:
            click.echo(f"  (+{len(callers) - 20} more)")
        click.echo()
    else:
        click.echo("Callers: (none)")
        click.echo()

    callees = data["callees"]
    if callees:
        click.echo(f"Callees ({len(callees)} unique files):")
        for c in callees[:20]:
            syms = ", ".join(c["symbols"][:3])
            if len(c["symbols"]) > 3:
                syms += f" +{len(c['symbols']) - 3} more"
            click.echo(f"  {c['file']:<50s} <- {syms}")
        if len(callees) > 20:
            click.echo(f"  (+{len(callees) - 20} more)")
        click.echo()
    else:
        click.echo("Callees: (none)")
        click.echo()

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

    coupling = data["coupling"]
    if coupling:
        click.echo(f"Coupling ({len(coupling)} partners):")
        rows = [[c["path"], str(c["cochange_count"]), f"{c['strength']:.0%}"] for c in coupling[:10]]
        click.echo(format_table(["file", "co-changes", "strength"], rows))
        click.echo()

    cx = data["complexity"]
    if cx:
        click.echo(
            f"Complexity: avg={cx['avg']}, max={cx['max']}, "
            f"{cx['count_above_threshold']} above threshold "
            f"(>{cx['threshold']})"
        )
        click.echo()


def _render_file_json(data, budget=0):
    fname = os.path.basename(data["file_path"])
    verdict = (
        f"{fname}: {data['symbol_count']} symbols, {len(data['callers'])} caller files, {len(data['tests'])} test files"
    )
    summary = {
        "verdict": verdict,
        "symbol_count": data["symbol_count"],
        "caller_files": len(data["callers"]),
        "caller_metric_definition": CALLER_METRIC_RAW,
        "callee_files": len(data["callees"]),
        "test_files": len(data["tests"]),
        "coupling_partners": len(data["coupling"]),
    }
    if data["complexity"]:
        summary["complexity_avg"] = data["complexity"]["avg"]
        summary["complexity_max"] = data["complexity"]["max"]

    click.echo(
        to_json(
            json_envelope(
                "context",
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
            )
        )
    )


# ---------------------------------------------------------------------------
# Batch text + JSON
# ---------------------------------------------------------------------------


def _render_batch_text(data):
    contexts = data["contexts"]
    shared_callers = data["shared_callers"]
    shared_callees = data["shared_callees"]
    scored_files = data["files_to_read"]

    click.echo(f"VERDICT: {len(contexts)} symbols, {len(scored_files)} files to read")
    click.echo()
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
            f"  Callers: {len(c['non_test_callers'])}  Callees: {len(c['callees'])}  Tests: {len(c['test_callers'])}"
        )
        click.echo()

    if shared_callers:
        click.echo(f"Shared callers ({len(shared_callers)}):")
        rows = [[abbrev_kind(c["kind"]), c["name"], loc(c["file_path"], c["line_start"])] for c in shared_callers[:15]]
        click.echo(format_table(["kind", "name", "location"], rows))
        click.echo()

    if shared_callees:
        click.echo(f"Shared callees ({len(shared_callees)}):")
        rows = [[abbrev_kind(c["kind"]), c["name"], loc(c["file_path"], c["line_start"])] for c in shared_callees[:15]]
        click.echo(format_table(["kind", "name", "location"], rows))
        click.echo()

    click.echo(f"Files to read ({len(scored_files)}):")
    for f in scored_files[:25]:
        reasons = ", ".join(f["reasons"])
        rel_str = f"{f['relevance']:.0%}" if f.get("relevance", 0) > 0 else ""
        click.echo(f"  {f['path']:<50s} {rel_str:>5s}  ({reasons})")
    if len(scored_files) > 25:
        click.echo(f"  (+{len(scored_files) - 25} more)")


def _render_batch_json(data, budget=0):
    contexts = data["contexts"]
    shared_callers = data["shared_callers"]
    shared_callees = data["shared_callees"]
    scored_files = data["files_to_read"]

    # W1245 Pattern-2 variant-D: per-entry resolver tier so a batch with
    # mixed exact + fuzzy + (post-resolver) "symbol" tiers discloses
    # each entry's degradation. Top-level ``partial_success`` flips
    # whenever ANY entry resolved non-exactly so consumers scanning only
    # the summary still see the degradation signal. Pair by index --
    # ``_gather_batch`` builds ``contexts`` in the same order as
    # ``resolved``, which mirrors the input ``names`` order, so multiple
    # inputs that converge on the same resolved symbol keep distinct
    # tier disclosures.
    resolutions = data.get("_resolutions") or []
    any_degraded = any(r["tier"] != "symbol" for r in resolutions)

    def _entry_resolution(idx: int, c) -> dict:
        sym = c["sym"]
        resolved_name = sym["qualified_name"] or sym["name"]
        rec = resolutions[idx] if idx < len(resolutions) else None
        tier = (rec or {}).get("tier", "symbol")
        return resolution_disclosure(tier, target=resolved_name)

    click.echo(
        to_json(
            json_envelope(
                "context",
                budget=budget,
                summary={
                    "verdict": f"{len(contexts)} symbols, {len(scored_files)} files to read",
                    "symbols": len(contexts),
                    "shared_callers": len(shared_callers),
                    "shared_callees": len(shared_callees),
                    "files_to_read": len(scored_files),
                    "caller_metric_definition": CALLER_METRIC_RAW,
                    # W1245 Pattern-2 variant-D: top-level partial_success
                    # flips on ANY degraded entry across the batch.
                    "partial_success": any_degraded,
                },
                mode="batch",
                symbols=[
                    {
                        "name": c["sym"]["qualified_name"] or c["sym"]["name"],
                        "kind": c["sym"]["kind"],
                        "location": loc(c["sym"]["file_path"], c["line_start"]),
                        "callers": [
                            {
                                "name": cr["name"],
                                "kind": cr["kind"],
                                "location": loc(cr["file_path"], cr["edge_line"] or cr["line_start"]),
                            }
                            for cr in c["non_test_callers"][:20]
                        ],
                        "callees": [
                            {
                                "name": ce["name"],
                                "kind": ce["kind"],
                                "location": loc(ce["file_path"], ce["line_start"]),
                            }
                            for ce in c["callees"][:15]
                        ],
                        "tests": len(c["test_callers"]),
                        # Per-entry resolution disclosure (per W324 cmd_annotate
                        # per-finding template).
                        **_entry_resolution(i, c),
                    }
                    for i, c in enumerate(contexts)
                ],
                shared_callers=[
                    {
                        "name": c["name"],
                        "kind": c["kind"],
                        "location": loc(c["file_path"], c["line_start"]),
                    }
                    for c in shared_callers
                ],
                shared_callees=[
                    {
                        "name": c["name"],
                        "kind": c["kind"],
                        "location": loc(c["file_path"], c["line_start"]),
                    }
                    for c in shared_callees
                ],
                files_to_read=scored_files,
                partial_success=any_degraded,
            )
        )
    )


# ---------------------------------------------------------------------------
# File path resolver helper
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


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="context",
    category="workflow",
    summary="Get the minimal context needed to safely modify a symbol",
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
@click.command()
@click.argument("names", nargs=-1)
@click.option(
    "--task",
    "task",
    type=click.Choice(_TASK_CHOICES, case_sensitive=False),
    default=None,
    help="Tailor context to a specific task intent: refactor, debug, extend, review, understand.",
)
@click.option(
    "--for-file",
    "for_file",
    type=str,
    default=None,
    help="Get aggregated context for an entire file instead of a symbol.",
)
@click.option(
    "--session-hint",
    "session_hint",
    type=str,
    default="",
    help="Optional conversation hint used to personalize files-to-read ranking.",
)
@click.option(
    "--recent-symbol",
    "recent_symbols",
    multiple=True,
    help="Recently discussed symbol(s) to bias context ranking (repeatable).",
)
@click.option(
    "--no-propagation",
    "no_propagation",
    is_flag=True,
    default=False,
    help="Disable call-graph propagation ranking (use legacy PageRank-only mode).",
)
@click.option(
    "--inline",
    "inline_mode",
    is_flag=True,
    default=False,
    help=(
        "emit the recommended file content as one concatenated "
        "string with line markers, ready to paste into a chat agent."
    ),
)
@click.pass_context
def context(ctx, names, task, for_file, session_hint, recent_symbols, no_propagation, inline_mode):
    """Get the minimal context needed to safely modify a symbol.

    Unlike single-purpose commands like ``impact`` or ``uses``, this command
    aggregates data from 15+ subsystems into one AI-agent-ready response.

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

    \b
    Examples:
      roam context handle_login
      roam context User --task refactor
      roam context --for-file src/auth.py
      roam context fn_a fn_b fn_c    # batch with shared callers

    See also ``preflight`` (full safety pre-change check), ``impact``
    (blast radius alone), and ``understand`` (broad orientation).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    use_propagation = not no_propagation

    # --- File-level context mode ---
    if for_file:
        with open_db(readonly=True) as conn:
            frow = _resolve_file(conn, for_file)
            if frow is None:
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "context",
                                summary={
                                    "verdict": f"file not found: '{for_file}'",
                                    "error": "file_not_found",
                                },
                                file=for_file,
                                hint=file_not_found_hint(for_file),
                            )
                        )
                    )
                    raise SystemExit(1)
                click.echo(file_not_found_hint(for_file))
                raise SystemExit(1)
            data = _gather_file(conn, frow)
        _render_json(data, budget=token_budget) if json_mode else _render_text(data)
        return

    # Require at least one symbol name if --for-file is not used
    if not names:
        click.echo(ctx.get_help())
        return

    with open_db(readonly=True) as conn:
        # Resolve all symbols
        resolved = []
        # W1245 Pattern-2 variant-D: record each input's resolver tier
        # alongside the resolved row, so JSON renderers can disclose
        # which calls landed on exact-name matches and which fell back
        # to fuzzy-LIKE matches.
        resolutions: list[dict] = []
        for name in names:
            sym = find_symbol(conn, name)
            if sym is None:
                # W1272 — Pattern-2c Convention (c): unresolved exits 0
                # with a resolution=unresolved + partial_success
                # disclosure. Context gathering for a missing symbol is
                # "I tried and there's nothing to gather" (a valid
                # no-op success), not a tool failure. Keep the FTS
                # suggestion list in text mode.
                unresolved_block = resolution_disclosure("unresolved", target=name or "")
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "context",
                                summary={
                                    "verdict": f"Symbol '{name}' not found",
                                    "partial_success": True,
                                    "state": "not_found",
                                    **unresolved_block,
                                },
                                symbol=name or "",
                                **unresolved_block,
                            )
                        )
                    )
                else:
                    click.echo(symbol_not_found(conn, name, json_mode=False))
                return
            resolved.append(sym)
            resolutions.append(
                {
                    "input": name,
                    "tier": sym.get("_resolution_tier", "symbol"),
                    "resolved_name": sym["qualified_name"] or sym["name"],
                }
            )

        # Batch mode
        if len(resolved) > 1:
            if task and not json_mode:
                click.echo(
                    "Warning: task-specific extra sections are ignored in batch mode "
                    "(multiple symbols). Ranking still uses task/session hints.",
                    err=True,
                )
            data = _gather_batch(conn, resolved, task, session_hint, recent_symbols, use_propagation)
        else:
            # Single symbol mode — always gather everything
            data = _gather_single(conn, resolved[0], task, session_hint, recent_symbols, use_propagation)

    # pass inline-mode flag through to the renderer.
    if inline_mode:
        data["inline_mode"] = True
    # thread the global --budget through to renderers so
    # format_table calls in cmd_context can honor it.
    data["token_budget"] = token_budget
    # W1245 Pattern-2 variant-D resolution disclosure threaded into
    # ``data`` so ``_render_single_json`` / ``_render_batch_json`` can
    # merge the per-call tier into the envelope shape uniformly.
    data["_resolutions"] = resolutions
    _render_json(data, budget=token_budget) if json_mode else _render_text(data)
