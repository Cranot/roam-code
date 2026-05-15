"""``roam py-types`` — type-annotation health for Python projects.

Surfaces (Python pivot v12.4-iter, exploratory):

* % of public functions with full annotations (params + return)
* count of ``Any`` usage in signatures
* count of legacy ``typing.Optional/Dict/List/Set/Tuple`` (PEP 585/604
  modernisation candidates)
* per-file annotation coverage with worst offenders

Reads from ``symbols.signature`` only — no source-text scan needed
since the extractor preserves the full function signature including
annotations.
"""

from __future__ import annotations

import re

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import format_table, json_envelope, to_json

# Pre-compiled patterns for legacy typing constructs that PEP 585/604
# modernised. Order matters: more specific first.
_OLD_TYPING_RE = re.compile(r"\b(Optional|Dict|List|Set|Tuple|FrozenSet)\[")
_ANY_RE = re.compile(r"\bAny\b")
_PARAM_RE = re.compile(r"\(([^)]*)\)")
# The signature column stores decorators + def line (e.g.
# ``@_tool(name=..., output_schema=...)\ndef foo(x: int) -> dict``).
# Anchor param extraction at ``def NAME(`` so decorator arguments
# don't masquerade as untyped params. ``async def`` covered via
# the optional ``async`` group.
_DEF_PARAM_RE = re.compile(r"(?:async\s+)?def\s+\w+\s*\(([^)]*)\)")
# ``->`` only counts when it's the def's return type, not in a
# decorator's lambda or in a string default value. Anchor on the
# closing paren of the def line.
_DEF_RETURN_RE = re.compile(r"(?:async\s+)?def\s+\w+\s*\([^)]*\)\s*->")


def _signature_health(signature: str | None) -> dict:
    """Return a per-symbol annotation snapshot.

    The stored signature includes any decorator chain, so we anchor
    the param + return scan on the ``def NAME(`` token rather than the
    first ``(...)`` block — otherwise a ``@register(name="x")``
    decorator's arguments are read as untyped function parameters.
    """
    if not signature:
        return {"has_return": False, "params_typed": 0, "params_untyped": 0, "uses_any": False, "old_typing": False}
    has_return = bool(_DEF_RETURN_RE.search(signature))
    uses_any = bool(_ANY_RE.search(signature))
    old_typing = bool(_OLD_TYPING_RE.search(signature))
    params_typed = 0
    params_untyped = 0
    # Anchor at ``def NAME(...)`` — falls back to the first ``(...)``
    # if the signature has no def keyword (defensive for unusual
    # symbols that ended up in the table but aren't actually fns).
    paren_match = _DEF_PARAM_RE.search(signature) or _PARAM_RE.search(signature)
    if paren_match:
        param_text = paren_match.group(1)
        for raw in param_text.split(","):
            p = raw.strip()
            if not p or p in ("self", "cls"):
                continue
            # Strip default value: foo: int = 1 → foo: int
            p_no_default = p.split("=", 1)[0].strip()
            if ":" in p_no_default:
                params_typed += 1
            else:
                params_untyped += 1
    return {
        "has_return": has_return,
        "params_typed": params_typed,
        "params_untyped": params_untyped,
        "uses_any": uses_any,
        "old_typing": old_typing,
    }


@roam_capability(
    name="py-types",
    category="health",
    summary="Show Python type-annotation health for the indexed project",
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
@click.command("py-types")
@click.option("--detail", is_flag=True, help="Show per-file table of worst offenders")
@click.option("--top", "limit", default=10, type=int, help="Number of worst-offending files to show")
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help=(
        "Include test files (file_role='test') in the coverage stats. "
        "Excluded by default — test functions rarely have type annotations "
        "and dominate the missing-annotation count, drowning the production "
        "signal (Python pivot v12.4-iter dogfood)."
    ),
)
@click.option(
    "--min-coverage",
    type=int,
    default=None,
    help=(
        "CI gate: exit 5 (EXIT_GATE_FAILURE) when type coverage is below "
        "this percentage. Pair with ``--ci`` for gate semantics. Skipped "
        "when no coverage data."
    ),
)
@click.option(
    "--ci",
    "ci_mode",
    is_flag=True,
    default=False,
    help="CI mode: exit 5 if --min-coverage threshold not met (no-op without --min-coverage).",
)
@click.pass_context
def py_types(ctx, detail, limit, include_tests, min_coverage, ci_mode):
    """Show Python type-annotation health for the indexed project.

    Counts public functions/methods that:
    * lack a return annotation (``-> ...``)
    * have any parameter without a type annotation
    * use ``Any`` (escape hatch — often signals lazy typing)
    * use legacy ``typing.Optional/Dict/List/Set/Tuple`` instead of
      the PEP 604 (``X | None``) / PEP 585 (``dict[str, int]``)
      modern forms.

    Use this to direct typing-fix sprints to the highest-leverage
    files, or as a CI gate (pair ``--ci`` with ``--min-coverage``).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    with open_db(readonly=True) as conn:
        # Default: exclude test files — they dominate the missing-
        # annotation count without representing production coverage.
        # Use ``--include-tests`` to opt back in.
        test_filter = "" if include_tests else "AND COALESCE(f.file_role, '') != 'test'"
        rows = conn.execute(
            f"""
            SELECT s.name, s.qualified_name, s.signature, s.line_start, f.path
            FROM symbols s JOIN files f ON s.file_id = f.id
            WHERE s.kind IN ('function', 'method')
              AND s.visibility = 'public'
              AND f.language = 'python'
              {test_filter}
            """
        ).fetchall()

    total = len(rows)
    if total == 0:
        click.echo("VERDICT: no public Python functions/methods indexed")
        click.echo()
        # Diagnose why
        with open_db(readonly=True) as conn2:
            n_py_files = conn2.execute("SELECT COUNT(*) FROM files WHERE language = 'python'").fetchone()[0]
            n_total_files = conn2.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        if n_py_files == 0:
            click.echo(
                f"  No Python files in the {n_total_files} indexed files. "
                "Is this a non-Python project, or is Python detection failing?"
            )
            click.echo("  Try: roam understand   (to see indexed languages)")
        else:
            click.echo(
                f"  {n_py_files} Python files indexed but no public fn/methods. Coverage stats default-exclude tests."
            )
            click.echo("  Try: roam py-types --include-tests")
        return

    no_return = 0
    untyped_params = 0
    uses_any = 0
    old_typing = 0
    by_file: dict[str, dict] = {}
    findings: list[dict] = []

    for r in rows:
        h = _signature_health(r["signature"])
        if not h["has_return"]:
            no_return += 1
        if h["params_untyped"] > 0:
            untyped_params += 1
        if h["uses_any"]:
            uses_any += 1
        if h["old_typing"]:
            old_typing += 1
        slot = by_file.setdefault(r["path"], {"total": 0, "missing": 0})
        slot["total"] += 1
        if not h["has_return"] or h["params_untyped"] > 0 or h["uses_any"] or h["old_typing"]:
            issues = []
            if not h["has_return"]:
                issues.append("no-return")
            if h["params_untyped"] > 0:
                issues.append(f"{h['params_untyped']}-untyped")
            if h["uses_any"]:
                issues.append("uses-Any")
            if h["old_typing"]:
                issues.append("legacy-typing")
            findings.append(
                {
                    "name": r["qualified_name"] or r["name"],
                    "path": r["path"],
                    "line": r["line_start"],
                    "issues": issues,
                }
            )
        if not h["has_return"] or h["params_untyped"] > 0:
            slot["missing"] += 1

    pct = lambda x: f"{x * 100 // total}%"  # noqa: E731
    coverage = ((total - max(no_return, untyped_params)) * 100) // total
    if coverage >= 80:
        verdict = f"good type coverage ({coverage}% public symbols fully typed across {total} fn/methods)"
    elif coverage >= 50:
        verdict = f"fair type coverage ({coverage}% public symbols fully typed across {total} fn/methods)"
    else:
        verdict = f"weak type coverage ({coverage}% public symbols fully typed across {total} fn/methods)"

    if json_mode:
        # Limit findings to the same top-N as the by_file rollup for the
        # JSON envelope so consumers don't get a full per-symbol dump on
        # large projects. Detail mode (when stable) adds file:line.
        ranked_findings = sorted(findings, key=lambda f: (f["path"], f["line"]))
        click.echo(
            to_json(
                json_envelope(
                    "py-types",
                    summary={
                        "verdict": verdict,
                        "total_public": total,
                        "no_return_annotation": no_return,
                        "untyped_params": untyped_params,
                        "uses_any": uses_any,
                        "old_typing": old_typing,
                        "coverage_pct": coverage,
                    },
                    by_file=[
                        {"path": p, "total": d["total"], "missing": d["missing"]}
                        for p, d in sorted(by_file.items(), key=lambda kv: -kv[1]["missing"])[:limit]
                    ],
                    findings=ranked_findings[: limit * 5] if detail else [],
                )
            )
        )
        return

    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    if sarif_mode:
        from roam.output.sarif import py_types_to_sarif, write_sarif

        by_file_list = [
            {"path": p, "total": d["total"], "missing": d["missing"]}
            for p, d in sorted(by_file.items(), key=lambda kv: -kv[1]["missing"])
        ]
        click.echo(write_sarif(py_types_to_sarif(by_file_list, coverage)))
        return

    click.echo(f"VERDICT: {verdict}\n")
    click.echo(f"  public fn/methods:           {total}")
    click.echo(f"  missing return annotation:   {no_return} ({pct(no_return)})")
    click.echo(f"  param without annotation:    {untyped_params} ({pct(untyped_params)})")
    click.echo(f"  uses ``Any``:                {uses_any} ({pct(uses_any)})")
    click.echo(f"  legacy typing (Optional/Dict/List/Set/Tuple): {old_typing} ({pct(old_typing)})")

    if detail and by_file:
        click.echo()
        click.echo(f"Top {min(limit, len(by_file))} files by missing-annotation count:")
        rows_table = sorted(
            by_file.items(),
            key=lambda kv: -kv[1]["missing"],
        )[:limit]
        click.echo(
            format_table(
                ["File", "Total", "Missing", "Coverage"],
                [
                    [
                        p,
                        str(d["total"]),
                        str(d["missing"]),
                        f"{((d['total'] - d['missing']) * 100 // (d['total'] or 1))}%",
                    ]
                    for p, d in rows_table
                ],
            )
        )
        if findings:
            click.echo()
            sample = sorted(findings, key=lambda f: (f["path"], f["line"]))[: limit * 5]
            click.echo("Sample findings (file:line, name, issues):")
            click.echo(
                format_table(
                    ["Location", "Symbol", "Issues"],
                    [[f"{f['path']}:{f['line']}", f["name"], ", ".join(f["issues"])] for f in sample],
                )
            )

    # CI gate — exit 5 (mirrors EXIT_GATE_FAILURE used by ``roam rules
    # --ci``) when coverage falls below the requested threshold.
    if ci_mode and min_coverage is not None and coverage < min_coverage:
        click.echo()
        click.echo(f"GATE FAILED: coverage {coverage}% < required {min_coverage}%")
        ctx.exit(5)
