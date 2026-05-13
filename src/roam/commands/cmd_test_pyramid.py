"""``roam test-pyramid`` — count tests per kind, flag inverted pyramids.

Surfaces the ``classify_test_kind`` helper at the CLI level.
A healthy pyramid has many unit tests, fewer integration tests, and a
small e2e cap. When ``e2e + integration > unit``, you're paying for
slow CI. We surface that via VERDICT.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.index.file_roles import ROLE_TEST
from roam.index.test_conventions import classify_test_kind
from roam.output.formatter import json_envelope, to_json


def _verdict(counts: dict) -> str:
    unit = counts["unit"]
    integ = counts["integration"]
    e2e = counts["e2e"]
    smoke = counts["smoke"]
    unknown = counts["unknown"]
    total = unit + integ + e2e + smoke + unknown
    if total == 0:
        return "no test files indexed"
    classified = unit + integ + e2e + smoke
    if classified == 0:
        return f"UNSTRUCTURED — {total} flat test files (no kind hints in paths or names)"
    if unknown >= classified * 4:
        return f"MOSTLY-UNSTRUCTURED — {unknown} of {total} test files have no kind hint (only {classified} classified)"
    inverted_pair = e2e + integ
    if inverted_pair > unit and unit > 0:
        return f"INVERTED — {inverted_pair} integration+e2e vs {unit} unit (slow CI risk)"
    return f"OK — {unit} unit / {integ} integration / {e2e} e2e / {smoke} smoke"


@roam_capability(
    name="test-pyramid",
    category="refactoring",
    summary="Count tests by kind (unit/integration/e2e/smoke), flag inverted pyramids",
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
@click.pass_context
def test_pyramid(ctx) -> None:
    """Count tests by kind (unit/integration/e2e/smoke), flag inverted pyramids."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    counts: dict[str, int] = {"unit": 0, "integration": 0, "e2e": 0, "smoke": 0, "unknown": 0}
    samples: dict[str, list[str]] = {k: [] for k in counts}
    with open_db(readonly=True) as conn:
        rows = conn.execute(
            "SELECT path FROM files WHERE file_role = ? ORDER BY path",
            (ROLE_TEST,),
        ).fetchall()
    for r in rows:
        kind = classify_test_kind(r["path"])
        if kind not in counts:
            kind = "unknown"
        counts[kind] += 1
        if len(samples[kind]) < 3:
            samples[kind].append(r["path"])
    total = sum(counts.values())
    verdict = _verdict(counts)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "test-pyramid",
                    summary={"verdict": verdict, "total": total, **counts},
                    counts=counts,
                    samples=samples,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"Total test files: {total}")
    click.echo()
    click.echo("Kind         Count  Sample")
    click.echo("-----------  -----  -------------------------------------")
    for kind in ("unit", "integration", "e2e", "smoke", "unknown"):
        sample = samples[kind][0] if samples[kind] else "—"
        click.echo(f"{kind:11}  {counts[kind]:5}  {sample}")
