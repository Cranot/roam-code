"""``roam calc-probe`` — empirical rounding differential across live runtimes.

Static semantics labels are approximations: JS ties are decided by the IEEE-754
*representation* (``(2.675).toFixed(2) == "2.67"`` despite the half-up rule), a
variable mode argument is invisible to the AST, and library defaults drift. The
only ground truth for "do these two rounding idioms agree?" is to **execute
them**. This command runs a catalog of rounding idioms on tie-boundary inputs
(the ``.xx5`` values where tie rules and float representation bite) across every
runtime available on PATH (python always; node/php when installed), normalizes
to cents, and reports exactly which inputs make which implementations disagree.

Deterministic by construction: a fixed input set (no clocks, no randomness), a
fixed idiom catalog, seeded nothing. Missing runtimes are skipped and disclosed
(fail-open) — the comparison simply covers fewer columns.

SARIF is deliberately NOT emitted: this is an empirical comparison report, not a
per-violation static finding stream; results ride the JSON envelope.

Given a PATH argument, the catalog is narrowed to the rounding idioms the code
under that path *actually uses* (via the calc-inventory extractor), so the
report answers "do MY implementations agree?", not "do languages differ in
general?". Idioms the catalog cannot express are disclosed as unprobed.

Usage::

    roam calc-probe               # full idiom catalog, all available runtimes
    roam calc-probe src/          # only idioms the code under src/ uses
    roam --json calc-probe        # machine-readable envelope
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

# Tie-boundary inputs: exact .xx5 halves (tie rules differ) + values whose float
# representation sits just below the printed half (representation effects) +
# negative halves (direction rules differ). Fixed list — determinism contract.
_PROBE_INPUTS: tuple[float, ...] = (
    1.005,
    2.675,
    8.835,
    0.125,
    0.135,
    2.5,
    -2.5,
    -1.005,
    0.005,
    1.015,
    2.045,
    10.075,
)

# Idiom catalog. Each entry: id, language (grammar name as calc_extract reports
# it), rounding fn + mode it corresponds to, runtime, and a program template that
# prints one result per input line (normalized downstream).
_IDIOMS: tuple[dict, ...] = (
    {
        "id": "python:round",
        "language": "python",
        "rounding": "round",
        "mode": None,
        "runtime": "python",
        "expr": "round(x, 2)",
    },
    {
        "id": "python:quantize:ROUND_HALF_UP",
        "language": "python",
        "rounding": "quantize",
        "mode": "ROUND_HALF_UP",
        "runtime": "python",
        "expr": "Decimal(str(x)).quantize(Decimal('0.01'), ROUND_HALF_UP)",
    },
    {
        "id": "python:quantize:ROUND_HALF_EVEN",
        "language": "python",
        "rounding": "quantize",
        "mode": "ROUND_HALF_EVEN",
        "runtime": "python",
        "expr": "Decimal(str(x)).quantize(Decimal('0.01'), ROUND_HALF_EVEN)",
    },
    {
        "id": "javascript:round",
        "language": "javascript",
        "rounding": "round",
        "mode": None,
        "runtime": "node",
        "expr": "Math.round(x*100)/100",
    },
    {
        "id": "javascript:tofixed",
        "language": "javascript",
        "rounding": "tofixed",
        "mode": None,
        "runtime": "node",
        "expr": "x.toFixed(2)",
    },
    {
        "id": "javascript:round:epsilon",
        "language": "javascript",
        "rounding": "round",
        "mode": "EPSILON_NUDGE",
        "runtime": "node",
        "expr": "Math.round((x+Number.EPSILON)*100)/100",
    },
    {
        "id": "php:round",
        "language": "php",
        "rounding": "round",
        "mode": None,
        "runtime": "php",
        "expr": "round($x, 2)",
    },
    {
        "id": "php:round:PHP_ROUND_HALF_EVEN",
        "language": "php",
        "rounding": "round",
        "mode": "PHP_ROUND_HALF_EVEN",
        "runtime": "php",
        "expr": "round($x, 2, PHP_ROUND_HALF_EVEN)",
    },
    {
        "id": "php:number_format",
        "language": "php",
        "rounding": "number_format",
        "mode": None,
        "runtime": "php",
        "expr": "number_format($x, 2, '.', '')",
    },
)

# typescript/tsx/vue script blocks execute on the same runtime as javascript
_LANGUAGE_ALIASES = {"typescript": "javascript", "tsx": "javascript", "jsx": "javascript", "vue": "javascript"}


def _runtime_available(runtime: str) -> bool:
    if runtime == "python":
        return True  # we are running on it
    return shutil.which(runtime) is not None


def _run_idioms_for_runtime(runtime: str, idioms: list[dict], inputs: tuple[float, ...]) -> dict[str, list[str]]:
    """Execute all of a runtime's idioms in ONE subprocess; returns id -> raw results.

    One process per runtime (not per input) keeps the probe in tens of
    milliseconds. Any failure returns {} for the runtime (fail-open, disclosed
    by the caller via ``available``).
    """
    xs = ",".join(repr(x) for x in inputs)
    if runtime == "python":
        lines = [
            "from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN",
            f"xs=[{xs}]",
        ]
        for idiom in idioms:
            lines.append(f"print('|'.join(str({idiom['expr']}) for x in xs))")
        argv = [sys.executable, "-c", "\n".join(lines)]
    elif runtime == "node":
        body = f"const xs=[{xs}];"
        for idiom in idioms:
            body += f"console.log(xs.map(x=>String({idiom['expr']})).join('|'));"
        argv = ["node", "-e", body]
    elif runtime == "php":
        body = f"$xs=[{xs}];"
        for idiom in idioms:
            body += f"echo implode('|', array_map(fn($x)=>strval({idiom['expr']}), $xs)), PHP_EOL;"
        argv = ["php", "-r", body]
    else:
        return {}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    rows = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(rows) != len(idioms):
        return {}
    return {idiom["id"]: rows[i].split("|") for i, idiom in enumerate(idioms)}


def _normalize_cents(raw: str) -> str:
    """Normalize a runtime's printed result to a cents string for comparison.

    ``1``, ``1.0``, ``1.00`` all mean the same cents; sign-normalize ``-0.00``.
    Non-numeric output is kept verbatim (still comparable, still honest).
    """
    try:
        value = float(raw)
    except ValueError:
        return raw.strip()
    out = f"{value:.2f}"
    return "0.00" if out == "-0.00" else out


def _used_idiom_keys(path: Path) -> tuple[set[tuple[str, str, str | None]], int]:
    """(language, rounding, mode) triples used by the code under ``path``."""
    from roam.commands.cmd_calc_inventory import _discover_files
    from roam.index.calc_extract import extract_calcs_from_file

    used: set[tuple[str, str, str | None]] = set()
    calc_count = 0
    for f in _discover_files(path):
        for c in extract_calcs_from_file(f):
            calc_count += 1
            if c.rounding:
                lang = _LANGUAGE_ALIASES.get(c.language, c.language)
                used.add((lang, c.rounding, c.rounding_mode))
    return used, calc_count


@roam_capability(
    name="calc-probe",
    category="health",
    summary="Execute rounding idioms on tie-boundary inputs across live runtimes; report empirical divergences",
    inputs=("paths",),
    outputs=("findings_envelope",),
)
@click.command(name="calc-probe")
@click.argument("paths", nargs=-1, type=click.Path(file_okay=True, dir_okay=True))
@click.pass_context
def calc_probe(ctx, paths):
    """Empirically compare rounding implementations on tie-boundary inputs.

    With no PATH: the full idiom catalog. With one PATH: only the idioms that
    code actually uses. With MULTIPLE paths (e.g. a PHP backend and a JS
    frontend checkout), the union of both sides' idioms is probed together —
    the cross-repo faithfulness comparison a single-repo scan cannot see.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    idioms = list(_IDIOMS)
    scoped_note = None
    unprobed: list[str] = []
    if paths:
        used: set[tuple[str, str, str | None]] = set()
        calc_count = 0
        for path in paths:
            root = Path(path)
            if not root.exists():
                verdict = f"path not found: {path}"
                if json_mode:
                    click.echo(
                        to_json(json_envelope("calc-probe", summary={"verdict": verdict}, error="path_not_found"))
                    )
                else:
                    click.echo(f"VERDICT: {verdict}")
                ctx.exit(2)
                return
            part_used, part_count = _used_idiom_keys(root)
            used |= part_used
            calc_count += part_count
        catalog_keys = {(i["language"], i["rounding"], i["mode"]) for i in _IDIOMS}
        # keep every catalog idiom whose (lang, fn) the code uses — mode-specific
        # variants match exactly; a used mode we can't express is disclosed.
        used_fn = {(lang, fn) for lang, fn, _ in used}
        idioms = [
            i
            for i in _IDIOMS
            if (i["language"], i["rounding"]) in used_fn
            and ((i["language"], i["rounding"], i["mode"]) in used or i["mode"] is None)
        ]
        unprobed = sorted(
            f"{lang}:{fn}:{mode}"
            for (lang, fn, mode) in used
            if (lang, fn, mode) not in catalog_keys and (lang, fn, None) not in catalog_keys
        )
        scoped_note = f"scoped to {len(idioms)} idiom(s) used by {calc_count} calculations under {', '.join(paths)}"

    # group by runtime, execute available ones
    by_runtime: dict[str, list[dict]] = {}
    for idiom in idioms:
        by_runtime.setdefault(idiom["runtime"], []).append(idiom)
    results: dict[str, list[str]] = {}
    skipped_runtimes: list[str] = []
    for runtime, group in sorted(by_runtime.items()):
        if not _runtime_available(runtime):
            skipped_runtimes.append(runtime)
            continue
        results.update(_run_idioms_for_runtime(runtime, group, _PROBE_INPUTS))
    ran = [i for i in idioms if i["id"] in results]

    # per-input comparison over normalized cents
    divergences: list[dict] = []
    for pos, x in enumerate(_PROBE_INPUTS):
        values = {i["id"]: _normalize_cents(results[i["id"]][pos]) for i in ran}
        if len(set(values.values())) > 1:
            divergences.append({"input": x, "values": values})

    verdict = (
        f"{len(divergences)}/{len(_PROBE_INPUTS)} tie inputs diverge across {len(ran)} idiom(s), "
        f"{len(by_runtime) - len(skipped_runtimes)} runtime(s)"
    )
    if skipped_runtimes:
        verdict += f"; skipped runtimes: {', '.join(sorted(skipped_runtimes))}"
    facts = [
        f"{len(divergences)} divergent inputs found",
        f"{len(ran)} idioms ran",
        f"{len(_PROBE_INPUTS)} inputs scanned",
    ]
    summary = {
        "verdict": verdict,
        "divergent_inputs": len(divergences),
        "inputs_probed": len(_PROBE_INPUTS),
        "idioms_ran": [i["id"] for i in ran],
        "runtimes_skipped": sorted(skipped_runtimes),
        "unprobed_used_idioms": unprobed,
    }
    if scoped_note:
        summary["scope"] = scoped_note

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "calc-probe",
                    budget=token_budget,
                    summary=summary,
                    inputs=list(_PROBE_INPUTS),
                    divergences=divergences,
                    agent_contract={"facts": facts},
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if scoped_note:
        click.echo(f"  {scoped_note}")
    if unprobed:
        click.echo(f"  unprobed (no catalog entry): {', '.join(unprobed)}")
    if not divergences:
        click.echo("  all probed implementations agree on every tie input")
        return
    click.echo("\nDIVERGENT INPUTS (normalized to cents):")
    for d in divergences:
        click.echo(f"  {d['input']}:")
        by_val: dict[str, list[str]] = {}
        for idiom_id, val in sorted(d["values"].items()):
            by_val.setdefault(val, []).append(idiom_id)
        for val, ids in sorted(by_val.items()):
            click.echo(f"    {val}  <- {', '.join(ids)}")
