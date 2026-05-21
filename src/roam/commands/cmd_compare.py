"""roam compare — structural delta between two indices.

Compute symbol / file / metric / cycle deltas between a baseline index
(usually the merge-base or a previous release) and the current one.
Useful for: "did this refactor actually reduce coupling?", "which files
got significantly more complex?", "did we add or remove
dependency cycles?".

Usage:

    roam compare /path/to/baseline.db /path/to/target.db
    roam compare baseline.db target.db --json
    roam compare baseline.db target.db --top 20

When the user passes git refs instead of paths, we expect them to have
indexed each ref into a tagged DB beforehand (a future `roam snapshot
<ref>` command will automate this; for now, document the workflow in
the help text).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because compare outputs are invocation-scoped structural delta
summaries — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json
from roam.output.metric_definitions import COGNITIVE_COMPLEXITY_DEFINITION


@roam_capability(
    name="compare",
    category="workflow",
    summary="Structural diff between two roam indices",
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
@click.command(name="compare")
@click.argument("baseline", type=click.Path(exists=True, dir_okay=False))
@click.argument("target", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--top",
    type=int,
    default=15,
    show_default=True,
    help="Show only the top <N> changes per category.",
)
@click.option(
    "--threshold",
    type=int,
    default=5,
    show_default=True,
    help="Ignore complexity deltas smaller than this (per-file).",
)
@click.pass_context
def compare_cmd(ctx, baseline: str, target: str, top: int, threshold: int) -> None:
    """Structural diff between two roam indices.

    Reports symbols added/removed/moved, per-file complexity deltas
    above the threshold, language counts, and a one-line health verdict
    (improved/regressed/sideways).

    BASELINE and TARGET are paths to two `.roam/index.db` SQLite files —
    typically your previous-release index vs. your current one.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    base = Path(baseline).resolve()
    targ = Path(target).resolve()

    base_state = _load_index_state(base)
    targ_state = _load_index_state(targ)

    delta = _compute_delta(base_state, targ_state, threshold=threshold)

    verdict = _verdict(delta)
    complexity_available = delta.get("complexity_data_available", True)

    if json_mode:
        summary: dict[str, Any] = {
            "verdict": verdict,
            "baseline": str(base),
            "target": str(targ),
            "symbols_added": len(delta["symbols_added"]),
            "symbols_removed": len(delta["symbols_removed"]),
            "symbols_moved": len(delta["symbols_moved"]),
            "complexity_regressions": len(delta["complexity_up"]),
            "complexity_improvements": len(delta["complexity_down"]),
            # W1298 Pattern-3a: complexity_up/_down deltas are
            # per-file sums of cognitive_complexity, not McCabe.
            "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
        }
        envelope_data: dict[str, Any] = {
            "symbols_added": delta["symbols_added"][:top],
            "symbols_removed": delta["symbols_removed"][:top],
            "symbols_moved": delta["symbols_moved"][:top],
            "complexity_up": delta["complexity_up"][:top],
            "complexity_down": delta["complexity_down"][:top],
            "file_count_baseline": base_state["file_count"],
            "file_count_target": targ_state["file_count"],
            "symbol_count_baseline": base_state["symbol_count"],
            "symbol_count_target": targ_state["symbol_count"],
        }
        if not complexity_available:
            # W-Pattern2: disclose the degraded path -- one index predates
            # the math_signals schema, so complexity deltas were suppressed.
            summary["partial_success"] = True
            summary["complexity_data_available"] = False
            envelope_data["complexity_unavailable_reason"] = (
                "an index predates the math_signals schema -- complexity deltas "
                "were suppressed to avoid a fabricated verdict; re-run roam init"
            )
        click.echo(to_json(json_envelope("compare", summary=summary, **envelope_data)))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    click.echo(f"  Baseline: {base.name}  ({base_state['file_count']} files, {base_state['symbol_count']} symbols)")
    click.echo(f"  Target:   {targ.name}  ({targ_state['file_count']} files, {targ_state['symbol_count']} symbols)")
    click.echo("")
    click.echo(f"  Symbols added:    {len(delta['symbols_added'])}")
    click.echo(f"  Symbols removed:  {len(delta['symbols_removed'])}")
    click.echo(f"  Symbols moved:    {len(delta['symbols_moved'])}")
    if complexity_available:
        click.echo(f"  Files got more complex (Δ ≥ {threshold}): {len(delta['complexity_up'])}")
        click.echo(f"  Files got simpler (Δ ≥ {threshold}):       {len(delta['complexity_down'])}")
    else:
        click.echo("  Complexity delta:  UNAVAILABLE -- an index predates the math_signals schema")
        click.echo("                     (re-run `roam init` on that index to enable complexity deltas)")
    click.echo("")

    def _section(title: str, items: list[Any], render) -> None:
        if not items:
            return
        click.echo(f"  ── {title} (top {min(top, len(items))} of {len(items)}) ──")
        for it in items[:top]:
            click.echo(f"    {render(it)}")
        click.echo("")

    _section("Symbols added", delta["symbols_added"], lambda x: f"+ {x['kind']:8} {x['qname']}  in {x['path']}")
    _section("Symbols removed", delta["symbols_removed"], lambda x: f"- {x['kind']:8} {x['qname']}  was in {x['path']}")
    _section("Symbols moved", delta["symbols_moved"], lambda x: f"~ {x['qname']}: {x['old_path']} → {x['new_path']}")
    _section(
        "Files got more complex",
        delta["complexity_up"],
        lambda x: f"↑ {x['path']}  Δ +{x['delta']}  (now {x['target']}, was {x['baseline']})",
    )
    _section(
        "Files got simpler",
        delta["complexity_down"],
        lambda x: f"↓ {x['path']}  Δ -{abs(x['delta'])}  (now {x['target']}, was {x['baseline']})",
    )


def _load_index_state(db_path: Path) -> dict:
    """Load symbol qnames + file complexities from one index."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        symbols = {}
        for r in conn.execute(
            "SELECT s.qualified_name AS qname, s.kind AS kind, f.path AS path "
            "FROM symbols s JOIN files f ON s.file_id = f.id"
        ):
            qname = r["qname"] or ""
            if not qname:
                continue
            symbols[qname] = {"qname": qname, "kind": r["kind"] or "", "path": r["path"] or ""}

        complexities = {}
        complexity_data_available = True
        try:
            for r in conn.execute(
                "SELECT f.path AS path, COALESCE(SUM(ms.cognitive_complexity), 0) AS total "
                "FROM files f LEFT JOIN symbols s ON s.file_id = f.id "
                "LEFT JOIN math_signals ms ON ms.symbol_id = s.id "
                "GROUP BY f.path"
            ):
                complexities[r["path"]] = int(r["total"] or 0)
        except sqlite3.OperationalError:
            # math_signals or column may not exist on older indices.
            # W-Pattern2: do NOT silently treat complexity as 0 -- that
            # would fabricate IMPROVED/REGRESSED deltas. Disclose instead.
            complexity_data_available = False
            complexities = {}

        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        symbol_count = len(symbols)
        return {
            "symbols": symbols,
            "complexities": complexities,
            "complexity_data_available": complexity_data_available,
            "file_count": file_count,
            "symbol_count": symbol_count,
        }
    finally:
        conn.close()


def _compute_delta(base: dict, targ: dict, *, threshold: int) -> dict[str, list]:
    """Walk the two states and produce a delta dict.

    Symbol moves are detected by name-collision: same qualified_name in
    both indices but different file paths.
    """
    base_syms = base["symbols"]
    targ_syms = targ["symbols"]

    added_names = set(targ_syms) - set(base_syms)
    removed_names = set(base_syms) - set(targ_syms)
    common_names = set(targ_syms) & set(base_syms)

    moved = []
    for name in common_names:
        if base_syms[name]["path"] != targ_syms[name]["path"]:
            moved.append(
                {
                    "qname": name,
                    "kind": targ_syms[name]["kind"],
                    "old_path": base_syms[name]["path"],
                    "new_path": targ_syms[name]["path"],
                }
            )

    # W-Pattern2: if EITHER index lacks complexity data (an index that
    # predates the math_signals schema), suppress the complexity-delta
    # section entirely. Computing deltas against a fabricated 0 baseline
    # would emit a phantom IMPROVED/REGRESSED verdict.
    complexity_data_available = base.get("complexity_data_available", True) and targ.get(
        "complexity_data_available", True
    )

    up = []
    down = []
    if complexity_data_available:
        base_cx = base["complexities"]
        targ_cx = targ["complexities"]
        all_files = set(base_cx) | set(targ_cx)
        for f in all_files:
            b = base_cx.get(f, 0)
            t = targ_cx.get(f, 0)
            d = t - b
            if d >= threshold:
                up.append({"path": f, "baseline": b, "target": t, "delta": d})
            elif d <= -threshold:
                down.append({"path": f, "baseline": b, "target": t, "delta": d})

    return {
        "symbols_added": sorted([targ_syms[n] for n in added_names], key=lambda x: x["qname"]),
        "symbols_removed": sorted([base_syms[n] for n in removed_names], key=lambda x: x["qname"]),
        "symbols_moved": sorted(moved, key=lambda x: x["qname"]),
        "complexity_up": sorted(up, key=lambda x: -x["delta"]),
        "complexity_down": sorted(down, key=lambda x: x["delta"]),
        "complexity_data_available": complexity_data_available,
    }


def _verdict(delta: dict) -> str:
    """One-line health verdict from the delta."""
    up = len(delta["complexity_up"])
    down = len(delta["complexity_down"])
    added = len(delta["symbols_added"])
    removed = len(delta["symbols_removed"])
    complexity_available = delta.get("complexity_data_available", True)

    if not complexity_available:
        # W-Pattern2: complexity deltas were suppressed -- the verdict
        # must NOT pretend to be a complexity-driven IMPROVED/REGRESSED.
        # Decide on symbol churn only and disclose the missing dimension.
        if added == 0 and removed == 0:
            base_verdict = "NO CHANGE"
        elif added > removed * 1.5:
            base_verdict = "SYMBOLS ADDED"
        elif removed > added * 1.5:
            base_verdict = "SYMBOLS REMOVED"
        else:
            base_verdict = "SIDEWAYS"
        return (
            f"{base_verdict} -- complexity delta unavailable "
            "(an index predates the math_signals schema; re-run roam init)"
        )

    if up == 0 and down == 0 and added == 0 and removed == 0:
        return "NO CHANGE"
    if down > up * 1.5 and removed >= added * 0.8:
        return "IMPROVED"
    if up > down * 1.5 and added > removed * 1.5:
        return "REGRESSED"
    return "SIDEWAYS"
