"""Propose pre-change known-answer cases from input partitions or helper calls.

Displaces manually reading branch comparisons, multiplying input values, and
searching tests/callers with grep. Unlike invariants (implicit contracts),
path-coverage (graph paths), test-gaps (symbol priorities), and calc-golden
(recorded value replay), this command proposes an input table for review.
SARIF is deliberately NOT emitted: proposed cases are not code violations.
"""

from __future__ import annotations

import json
import shlex
from collections import Counter

import click

from roam.capability import roam_capability
from roam.commands.case_table_analysis import (
    UNKNOWN,
    bind_call,
    combinations,
    dimensions,
    js_model,
    outcome,
    python_model,
)
from roam.commands.changed_files import is_test_file
from roam.commands.resolve import ensure_index, find_symbol_with_alternatives
from roam.db.connection import find_project_root, open_db
from roam.db.edge_kinds import CALL_EDGE_KINDS
from roam.output.formatter import json_envelope, to_json

SUPPORTED = {"python", "javascript", "typescript", "tsx"}


def _model(source, line, language):
    return python_model(source, line) if language == "python" else js_model(source, line, language)


def build_table(conn, sym, root, cap, pairwise, call_sites):
    file = conn.execute("SELECT language FROM files WHERE id = ?", (sym["file_id"],)).fetchone()
    language = file["language"]
    if language not in SUPPORTED:
        return {"status": "unsupported_language", "language": language, "dimensions": [], "rows": [], "coverage": {}}
    try:
        source = (root / sym["file_path"]).read_text(encoding="utf-8")
        model = _model(source, sym["line_start"], language)
    except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
        return {"status": "source_unavailable", "reason": str(exc), "dimensions": [], "rows": [], "coverage": {}}
    callers = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, f.path, f.language, e.line "
        "FROM edges e JOIN symbols s ON s.id=e.source_id JOIN files f ON f.id=s.file_id "
        "WHERE e.target_id=? AND e.kind IN (?, ?, ?, ?) ORDER BY f.path, e.line, s.id",
        (sym["id"], *CALL_EDGE_KINDS, "tested_by", "tested-by"),
    ).fetchall()
    observed, sites, unparsed = [], [], 0
    cache = {}
    seen = set()
    for caller in callers:
        key = caller["id"]
        if key in seen:
            continue
        seen.add(key)
        test = is_test_file(caller["path"])
        site = {
            "caller": caller["name"],
            "caller_kind": "test" if test else caller["kind"],
            "file": caller["path"],
            "line": caller["line"],
            "coverage": "test_call_observed" if test else "no_test_observed",
            "inputs": UNKNOWN,
        }
        try:
            path = caller["path"]
            cache_key = (path, caller["line_start"])
            if cache_key not in cache:
                text = (root / path).read_text(encoding="utf-8")
                cache[cache_key] = _model(text, caller["line_start"], caller["language"]).calls
            calls = cache[cache_key]
            candidates = [c for c in calls if c[0] == sym["name"]]
            anchors = {c[4] for c in candidates if c[1] == caller["line"]}
            if not anchors and len({c[4] for c in candidates}) == 1:
                anchors = {c[4] for c in candidates}
            if not anchors:
                # An indexed alias can be recovered when its call line has a
                # single callee. Multiple receivers on one line remain unknown.
                anchors = {c[4] for c in calls if c[1] == caller["line"]}
            if len(anchors) != 1:
                unparsed += 1
                sites.append(site)
                continue
            matches = sorted((c for c in calls if c[4] in anchors), key=lambda c: c[1])
            for _, line, positional, keywords, _ in matches:
                call_site = dict(site, line=line, inputs=bind_call(model, positional, keywords))
                sites.append(call_site)
                if test:
                    observed.append((call_site["inputs"], {"file": path, "line": line, "name": caller["name"]}))
            if matches:
                continue
        except (OSError, UnicodeError, ValueError, SyntaxError):
            unparsed += 1
        sites.append(site)
    dims = dimensions(model)
    if call_sites:
        rows = sites[:cap]
        for row in rows:
            row["outcome"] = outcome(model.body, row["inputs"]) if row["inputs"] != UNKNOWN else {"kind": "unknown"}
            row["expected_answer"] = None
        total = len(sites)
        complete = None
        dims = [
            {
                "name": "caller_kind",
                "values": sorted({s["caller_kind"] for s in sites}),
                "source": "indexed_incoming_call_edges",
                "exhaustive": False,
            }
        ]
    else:
        indices, total, complete = combinations(dims, cap, pairwise)
        rows = []
        for index in indices:
            inputs = {d["name"]: d["values"][v] for d, v in zip(dims, index)}
            tests = [
                test
                for args, test in observed
                if json.dumps(args, sort_keys=True) == json.dumps(inputs, sort_keys=True)
                and all(not isinstance(v, dict) or v.get("kind") == "undefined" for v in args.values())
            ]
            rows.append(
                {
                    "inputs": inputs,
                    "coverage": "test_call_observed" if tests else "no_test_observed",
                    "tests": tests,
                    "outcome": outcome(model.body, inputs),
                    "expected_answer": None,
                }
            )
    covered = sum(r["coverage"] == "test_call_observed" for r in rows)
    truncated = total > len(rows) if not pairwise or call_sites or complete is None else complete is False
    return {
        "status": "ok",
        "language": language,
        "dimensions": dims,
        "rows": rows,
        "coverage": {
            "total_rows": total,
            "emitted_rows": len(rows),
            "test_call_observed": covered,
            "no_test_observed": len(rows) - covered,
            "truncated": truncated,
            "pairwise_complete": complete,
            "reduction": "pairwise" if pairwise and not call_sites else "none",
            "definition": "Static literal call arguments in indexed direct tests; not runtime branch coverage",
            "unparsed_callers": unparsed,
        },
        "caller_groups": dict(Counter(s["caller_kind"] for s in sites)),
        "caller_metric_definition": "Source call occurrences plus unresolved indexed caller placeholders, grouped by caller kind",
        "test_map": {
            "direct_tests": sorted({s["file"] for s in sites if s["caller_kind"] == "test"}),
            "definition": "Incoming call edges from test files, matching test-map direct-test semantics",
        },
        "limitations": [
            "Review and fill expected_answer before changing code; current outcomes are not an independent oracle.",
            "OTHER and unknown partitions require concrete representatives; computed arguments and indirect tests stay unproven.",
            "Local annotations and comparisons supply candidate domains; imported enums, loops, switch/match, assignments and effects may remain unknown.",
        ],
    }


@roam_capability(
    name="case-table",
    category="reports",
    summary="Propose pre-change input cases and static test-call evidence",
    displaces=("manual_branch_enumeration", "grep_test_calls"),
    ai_safe=True,
    requires_index=True,
    mcp_expose=True,
    mcp_preset=("full",),
    side_effect=False,
    stale_sensitive=True,
    inputs=("symbol",),
    outputs=("dimensions", "rows", "coverage", "next_commands"),
)
@click.command("case-table")
@click.argument("symbol")
@click.option("--max-rows", type=click.IntRange(1, 256), default=64, show_default=True, help="Cap emitted rows.")
@click.option("--pairwise", is_flag=True, help="Cover input-value pairs without expanding the full product.")
@click.option("--call-sites", is_flag=True, help="Propose one property-check row per indexed helper call site.")
@click.pass_context
def case_table(ctx, symbol, max_rows, pairwise, call_sites):
    """Propose a known-answer table for SYMBOL before editing code.

    Fill expected answers independently; static current returns are observations.
    Use --call-sites for a property required at every call of a helper.
    """
    ensure_index()
    with open_db(readonly=True) as conn:
        sym, alternatives = find_symbol_with_alternatives(conn, symbol)
        if sym is None:
            data = {"status": "symbol_not_found", "dimensions": [], "rows": [], "coverage": {}}
        else:
            data = build_table(conn, sym, find_project_root(), max_rows, pairwise, call_sites)
            data["symbol"] = {"name": sym["name"], "file": sym["file_path"], "line": sym["line_start"]}
            data["resolution"] = sym.get("_resolution_tier", "symbol")
            data["alternatives"] = [{"name": s["qualified_name"], "file": s["file_path"]} for s in alternatives]
    status = data.pop("status")
    coverage = data["coverage"]
    name = sym["name"] if sym else symbol
    degraded = bool(alternatives) if sym else False
    degraded = degraded or data.get("resolution") == "fuzzy"
    if status == "ok":
        verdict = f"{name}: {len(data['rows'])} proposed cases; {coverage['no_test_observed']} lack observed test calls"
        if coverage["truncated"]:
            verdict += "; row cap reached"
        if coverage.get("unparsed_callers"):
            verdict += f"; {coverage['unparsed_callers']} caller sources unresolved"
        if degraded:
            verdict += "; review symbol resolution"
    else:
        verdict = f"{name}: {status.replace('_', ' ')}; no case table generated"
    quoted = shlex.quote(symbol)
    next_commands = [f"roam test-map {quoted}", f"roam context {quoted}"]
    data["next_commands"] = next_commands
    summary = {
        "verdict": verdict,
        "status": status,
        "partial_success": status != "ok" or degraded or bool(coverage.get("unparsed_callers")),
        "mode": "call_sites" if call_sites else "input_dimensions",
        "next_commands": next_commands,
        "resolution": data.get("resolution", "unresolved"),
    }
    obj = ctx.obj or {}
    if obj.get("json"):
        click.echo(to_json(json_envelope("case-table", summary=summary, budget=obj.get("budget", 0), **data)))
        return
    click.echo(f"VERDICT: {verdict}")
    for dim in data["dimensions"]:
        click.echo(f"DIMENSION {dim['name']}: {json.dumps(dim['values'])}")
    for i, row in enumerate(data["rows"], 1):
        label = "TEST CALL" if row["coverage"] == "test_call_observed" else "NO TEST OBSERVED"
        context = f" {row['caller_kind']} {row['caller']} ({row['file']}:{row['line']})" if call_sites else ""
        click.echo(
            f"{i}. [{label}]{context} {json.dumps(row['inputs'])} -> {json.dumps(row['outcome'])}; expected: REVIEW"
        )
    click.echo("Coverage: static direct test calls; runtime execution and expected answers require review.")
    for command in next_commands:
        click.echo(f"Next: Run {command}")
