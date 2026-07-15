"""``roam calc-inventory`` — enumerate computed-numeric fields and their formulas.

roam records declarations and references but never the arithmetic *between* them,
so ``$vat = round($base * $rate / 100, 2)`` is invisible to the symbol layer.
This command walks the tree-sitter AST (via :mod:`roam.index.calc_extract`) and
surfaces every calculation: the target field, its formula, operands, numeric
literals, and any rounding function — deterministically, no model calls, in any
tree-sitter language roam supports (PHP, JS/TS, Python, Go, Java, C#, ...).

Three uses:

- **Inventory** (default): "where does this codebase compute money/totals/tax, and
  how?" — an audit + comprehension primitive.
- ``--money``: filter to money/accounting-shaped targets (vat, net, tax, total, ...).
- ``--divergence``: flag fields with the *same name* computed by *different*
  formulas across the codebase — the drift that bites when a value is calculated in
  two places (a backend and a frontend, a service and a worker) and the copies fall
  out of sync (e.g. one rounds half-up, the other truncates).

SARIF is deliberately NOT emitted (advisory heuristic scan; findings ride the JSON
envelope). Output follows the canonical ``json_envelope`` shape.

Usage::

    roam calc-inventory
    roam calc-inventory app/Services/
    roam calc-inventory --money
    roam calc-inventory --divergence
    roam calc-inventory --round-funcs r,round2,money
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.index.calc_extract import (
    Calc,
    extract_calcs_from_file,
    normalize_formula,
    normalize_target,
    rounding_semantic,
)
from roam.output.formatter import json_envelope, to_json

# Languages whose grammars use the node kinds the extractor understands.
_CALC_EXTS: frozenset[str] = frozenset(
    {
        ".php",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".vue",
        ".py",
        ".go",
        ".java",
        ".cs",
        ".rb",
        ".rs",
        ".c",
        ".cc",
        ".cpp",
        ".kt",
    }
)

# Money / accounting-shaped target names (matched against the normalized field
# name). Broad by design — advisory filter, not a gate.
_MONEY_RE = re.compile(
    r"(vat|fpa|tax|net|gross|amount|amt|total|subtotal|sum|price|cost|charge|fee|duty|"
    r"withhold|withheld|balance|discount|payment|due|poso|axia|katharo|synolo|rate|round)",
    re.IGNORECASE,
)

_SKIP_DIR_PARTS = frozenset({"node_modules", "vendor", "dist", "build", ".git", "__pycache__", "tests", "test"})


def _discover_files(root: Path) -> list[Path]:
    """Source files under ``root`` in a calc-bearing language, skipping vendored/test dirs.
    A file ``root`` is scanned regardless of directory filters."""
    if root.is_file():
        return [root] if root.suffix in _CALC_EXTS else []
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in _CALC_EXTS:
            continue
        if any(part in _SKIP_DIR_PARTS for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


def _calc_to_dict(c: Calc) -> dict:
    return {
        "target": c.target,
        "field": normalize_target(c.target),
        "formula": c.formula,
        "operands": list(c.operands),
        "literals": list(c.literals),
        "rounding": c.rounding,
        "file": c.file,
        "line": c.line,
        "language": c.language,
        "kind": c.kind,
    }


def _find_divergences(calcs: list[Calc]) -> list[dict]:
    """Fields (by normalized name) computed by >=2 distinct formula shapes."""
    by_field: dict[str, list[Calc]] = defaultdict(list)
    for c in calcs:
        by_field[normalize_target(c.target)].append(c)
    out: list[dict] = []
    for field_name, group in by_field.items():
        shapes: dict[str, list[Calc]] = defaultdict(list)
        for c in group:
            shapes[normalize_formula(c.formula)].append(c)
        if len(shapes) < 2:
            continue
        rounders = {c.rounding for c in group if c.rounding}
        semantics = {s for c in group if (s := rounding_semantic(c.language, c.rounding))}
        langs = {c.language for c in group if c.language}
        files = {c.file for c in group if c.file}
        out.append(
            {
                "field": field_name,
                "distinct_formulas": len(shapes),
                "languages": sorted(langs),
                "cross_language": len(langs) > 1,
                "cross_file": len(files) > 1,
                "rounding_functions": sorted(rounders),
                "rounding_divergent": len(rounders) > 1,
                "rounding_semantics": sorted(semantics),
                "rounding_semantics_divergent": len(semantics) > 1,
                "variants": [
                    {
                        "formula": group2[0].formula,
                        "rounding": group2[0].rounding,
                        "language": group2[0].language,
                        "sites": [f"{c.file}:{c.line}" for c in sorted(group2, key=lambda x: (x.file, x.line))],
                    }
                    for group2 in sorted(shapes.values(), key=lambda g: -len(g))
                ],
            }
        )
    # Highest signal first: cross-language drift (two implementations), then
    # rounding-divergent, then cross-file, then breadth. Same-file generic
    # accumulators (many `total` shapes in one file) sink to the bottom.
    out.sort(
        key=lambda d: (
            not d["rounding_semantics_divergent"],
            not d["cross_language"],
            not d["rounding_divergent"],
            not d["cross_file"],
            -d["distinct_formulas"],
            d["field"],
        )
    )
    return out


@roam_capability(
    name="calc-inventory",
    category="comprehension",
    summary="Enumerate computed-numeric fields + formulas from source (AST); flag same-name divergent formulas",
    inputs=("path", "--money", "--divergence", "--round-funcs"),
    outputs=("findings_envelope",),
)
@click.command(name="calc-inventory")
@click.argument("path", required=False, default="src/", type=click.Path(file_okay=True, dir_okay=True))
@click.option(
    "--money", "money_only", is_flag=True, help="Only money/accounting-shaped fields (vat, net, tax, total, ...)."
)
@click.option(
    "--divergence", "divergence", is_flag=True, help="Flag fields computed by >=2 different formulas (drift)."
)
@click.option(
    "--round-funcs", default="", help="Comma-list of extra project rounding wrappers to recognize (e.g. r,round2)."
)
@click.option(
    "--fail-on-divergence",
    is_flag=True,
    help="Exit 5 if any field is rounding-semantics-divergent (same name, different tie behavior). Opt-in CI gate.",
)
@click.pass_context
def calc_inventory(ctx, path, money_only, divergence, round_funcs, fail_on_divergence):
    """Enumerate computed-numeric fields + their formulas from the AST."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    divergence = divergence or fail_on_divergence  # the gate needs divergence data
    root = Path(path)

    extra_round_funcs = frozenset(f.strip().lower() for f in round_funcs.split(",") if f.strip())

    if not root.exists():
        verdict = f"path not found: {path}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "calc-inventory",
                        summary={"verdict": verdict, "partial_success": False},
                        path=str(path),
                        error="path_not_found",
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)
        return

    files = _discover_files(root)
    calcs: list[Calc] = []
    for f in files:
        calcs.extend(extract_calcs_from_file(f, extra_round_funcs))

    if money_only:
        calcs = [c for c in calcs if _MONEY_RE.search(normalize_target(c.target))]

    divergences = _find_divergences(calcs) if divergence else None
    rounding_count = sum(1 for c in calcs if c.rounding)
    files_with_calcs = len({c.file for c in calcs})

    if calcs:
        verdict = f"{len(calcs)} calculations across {files_with_calcs} files ({rounding_count} with rounding)"
        if divergences:
            sd = sum(1 for d in divergences if d["rounding_semantics_divergent"])
            rd = sum(1 for d in divergences if d["rounding_divergent"])
            extra = []
            if sd:
                extra.append(f"{sd} rounding-semantics-divergent")
            if rd:
                extra.append(f"{rd} rounding-divergent")
            verdict += f"; {len(divergences)} divergent fields" + (f" ({', '.join(extra)})" if extra else "")
    else:
        verdict = "no calculations found"

    facts = [
        f"{len(calcs)} calculations",
        f"{files_with_calcs} files",
        f"{rounding_count} rounding",
    ]
    if divergences is not None:
        facts.append(f"{len(divergences)} divergent fields")

    gate_failed = (
        fail_on_divergence and bool(divergences) and any(d["rounding_semantics_divergent"] for d in divergences)
    )

    summary = {
        "verdict": verdict,
        "calculations": len(calcs),
        "files_scanned": len(files),
        "files_with_calcs": files_with_calcs,
        "rounding_count": rounding_count,
        "money_only": money_only,
        "gate_failed": gate_failed,
    }

    if json_mode:
        envelope_kwargs: dict = dict(
            summary=summary,
            path=str(path),
            files_scanned=len(files),
            calculations=[_calc_to_dict(c) for c in calcs],
            agent_contract={"facts": facts},
        )
        if divergences is not None:
            envelope_kwargs["divergences"] = divergences
        # budget= must be a literal keyword on json_envelope so the central
        # budget gate (test_budget_coverage_survey) detects the forwarding and
        # the large ``calculations`` list is trimmed to the token cap.
        click.echo(to_json(json_envelope("calc-inventory", budget=token_budget, **envelope_kwargs)))
        ctx.exit(5 if gate_failed else 0)
        return

    click.echo(f"VERDICT: {verdict}")
    if not calcs:
        return
    if divergences:
        click.echo("\nDIVERGENT FIELDS (same name, different formula):")
        for d in divergences[:20]:
            tags = []
            if d["rounding_semantics_divergent"]:
                tags.append("ROUNDING-SEMANTICS-DIVERGENT " + "/".join(d["rounding_semantics"]))
            if d["cross_language"]:
                tags.append("CROSS-LANGUAGE " + "/".join(d["languages"]))
            if d["rounding_divergent"]:
                tags.append("ROUNDING-DIVERGENT")
            tag = ("  [" + ", ".join(tags) + "]") if tags else ""
            click.echo(f"  {d['field']} — {d['distinct_formulas']} formulas{tag}")
            for v in d["variants"]:
                r = f" [{v['rounding']}]" if v["rounding"] else ""
                click.echo(f"      {v['formula'][:70]}{r}")
                click.echo(f"        {', '.join(v['sites'][:4])}")
    else:
        by_file: dict[str, list[Calc]] = defaultdict(list)
        for c in calcs:
            by_file[c.file].append(c)
        for fpath in sorted(by_file)[:40]:
            click.echo(f"\n{fpath}")
            for c in sorted(by_file[fpath], key=lambda x: x.line):
                r = f"  [{c.rounding}]" if c.rounding else ""
                click.echo(f"  L{c.line:<5} {c.target[:26]:26s} = {c.formula[:60]}{r}")

    if gate_failed:
        click.echo("\nVERDICT: FAIL — rounding-semantics-divergent field(s) found (--fail-on-divergence)")
        ctx.exit(5)
