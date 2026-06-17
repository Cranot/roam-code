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
_TOTAL_PUBLIC_DEFINITION = "public Python functions/methods; test files excluded unless --include-tests is set"
_COVERAGE_PCT_DEFINITION = "(total_public - max(no_return_annotation, untyped_params)) * 100 // total_public"


def _py_types_agent_facts(
    verdict: str,
    total_public: int,
    coverage_pct: int,
    no_return_annotation: int,
    untyped_params: int,
    uses_any: int,
    old_typing: int,
) -> list[str]:
    return [
        verdict,
        f"{total_public} public Python callable symbols",
        f"coverage pct {coverage_pct}",
        f"{no_return_annotation} return-annotation gaps",
        f"{untyped_params} parameter-annotation gaps",
        f"{uses_any} Any-usage symbols",
        f"{old_typing} legacy-typing symbols",
    ]


def _signature_health(signature: str | None) -> dict:
    """Return a per-symbol annotation snapshot.

    The stored signature includes any decorator chain, so we anchor
    the param + return scan on the ``def NAME(`` token rather than the
    first ``(...)`` block — otherwise a ``@register(name="x")``
    decorator's arguments are read as untyped function parameters.
    """
    if not signature:
        return _empty_signature_health()
    params_typed, params_untyped = _signature_param_counts(signature)
    return {
        "has_return": bool(_DEF_RETURN_RE.search(signature)),
        "params_typed": params_typed,
        "params_untyped": params_untyped,
        "uses_any": bool(_ANY_RE.search(signature)),
        "old_typing": bool(_OLD_TYPING_RE.search(signature)),
    }


def _empty_signature_health() -> dict:
    return {"has_return": False, "params_typed": 0, "params_untyped": 0, "uses_any": False, "old_typing": False}


def _is_ignored_param(param: str) -> bool:
    return not param or param in ("self", "cls")


def _param_without_default(param: str) -> str:
    return param.split("=", 1)[0].strip()


def _signature_param_counts(signature: str) -> tuple[int, int]:
    params_typed = 0
    params_untyped = 0
    # Anchor at ``def NAME(...)`` — falls back to the first ``(...)``
    # if the signature has no def keyword (defensive for unusual
    # symbols that ended up in the table but aren't actually fns).
    paren_match = _DEF_PARAM_RE.search(signature) or _PARAM_RE.search(signature)
    if not paren_match:
        return params_typed, params_untyped

    for raw in paren_match.group(1).split(","):
        param = raw.strip()
        if _is_ignored_param(param):
            continue
        if ":" in _param_without_default(param):
            params_typed += 1
        else:
            params_untyped += 1
    return params_typed, params_untyped


def _public_python_rows(conn, include_tests: bool):
    # Default: exclude test files — they dominate the missing-annotation count
    # without representing production coverage. Use ``--include-tests`` to opt back in.
    test_filter = "" if include_tests else "AND COALESCE(f.file_role, '') != 'test'"
    return conn.execute(
        f"""
        SELECT s.name, s.qualified_name, s.signature, s.line_start, f.path
        FROM symbols s JOIN files f ON s.file_id = f.id
        WHERE s.kind IN ('function', 'method')
          AND s.visibility = 'public'
          AND f.language = 'python'
          {test_filter}
        """
    ).fetchall()


def _python_index_counts(conn) -> tuple[int, int]:
    n_py_files = conn.execute("SELECT COUNT(*) FROM files WHERE language = 'python'").fetchone()[0]
    n_total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    return n_py_files, n_total_files


def _type_issue_labels(health: dict) -> list[str]:
    issues = []
    if not health["has_return"]:
        issues.append("no-return")
    if health["params_untyped"] > 0:
        issues.append(f"{health['params_untyped']}-untyped")
    if health["uses_any"]:
        issues.append("uses-Any")
    if health["old_typing"]:
        issues.append("legacy-typing")
    return issues


def _update_by_file(by_file: dict[str, dict], path: str, health: dict) -> None:
    slot = by_file.setdefault(path, {"total": 0, "missing": 0})
    slot["total"] += 1
    if not health["has_return"] or health["params_untyped"] > 0:
        slot["missing"] += 1


def _type_finding(row, issues: list[str]) -> dict:
    return {
        "name": row["qualified_name"] or row["name"],
        "path": row["path"],
        "line": row["line_start"],
        "issues": issues,
    }


def _type_stats(rows) -> dict:
    stats = {
        "total": len(rows),
        "no_return": 0,
        "untyped_params": 0,
        "uses_any": 0,
        "old_typing": 0,
        "by_file": {},
        "findings": [],
    }
    for row in rows:
        health = _signature_health(row["signature"])
        stats["no_return"] += int(not health["has_return"])
        stats["untyped_params"] += int(health["params_untyped"] > 0)
        stats["uses_any"] += int(health["uses_any"])
        stats["old_typing"] += int(health["old_typing"])
        _update_by_file(stats["by_file"], row["path"], health)
        issues = _type_issue_labels(health)
        if issues:
            stats["findings"].append(_type_finding(row, issues))
    return stats


def _coverage_pct(total: int, no_return: int, untyped_params: int) -> int:
    return ((total - max(no_return, untyped_params)) * 100) // total


def _type_verdict(coverage: int, total: int) -> str:
    if coverage >= 80:
        label = "good type coverage"
    elif coverage >= 50:
        label = "fair type coverage"
    else:
        label = "weak type coverage"
    return f"{label} ({coverage}% public symbols fully typed across {total} fn/methods)"


def _ranked_by_file(by_file: dict[str, dict], limit: int | None = None) -> list[tuple[str, dict]]:
    ranked = sorted(by_file.items(), key=lambda kv: -kv[1]["missing"])
    return ranked[:limit] if limit is not None else ranked


def _ranked_findings(findings: list[dict], limit: int) -> list[dict]:
    return sorted(findings, key=lambda finding: (finding["path"], finding["line"]))[: limit * 5]


def _py_types_json_envelope(verdict: str, stats: dict, coverage: int, detail: bool, limit: int) -> dict:
    total = stats["total"]
    no_return = stats["no_return"]
    untyped_params = stats["untyped_params"]
    uses_any = stats["uses_any"]
    old_typing = stats["old_typing"]
    return json_envelope(
        "py-types",
        summary={
            "verdict": verdict,
            "total_public": total,
            "total_public_definition": _TOTAL_PUBLIC_DEFINITION,
            "no_return_annotation": no_return,
            "untyped_params": untyped_params,
            "uses_any": uses_any,
            "old_typing": old_typing,
            "coverage_pct": coverage,
            "coverage_pct_definition": _COVERAGE_PCT_DEFINITION,
        },
        by_file=[
            {"path": path, "total": data["total"], "missing": data["missing"]}
            for path, data in _ranked_by_file(stats["by_file"], limit)
        ],
        findings=_ranked_findings(stats["findings"], limit) if detail else [],
        agent_contract={
            "facts": _py_types_agent_facts(verdict, total, coverage, no_return, untyped_params, uses_any, old_typing)
        },
    )


def _emit_no_public_text(verdict: str, n_py_files: int, n_total_files: int) -> None:
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    if n_py_files == 0:
        click.echo(
            f"  No Python files in the {n_total_files} indexed files. "
            "Is this a non-Python project, or is Python detection failing?"
        )
        click.echo("  Try: roam understand   (to see indexed languages)")
        return
    click.echo(f"  {n_py_files} Python files indexed but no public fn/methods. Coverage stats default-exclude tests.")
    click.echo("  Try: roam py-types --include-tests")


def _py_types_sarif(by_file: dict[str, dict], coverage: int) -> str:
    from roam.output.sarif import py_types_to_sarif, write_sarif

    by_file_list = [
        {"path": path, "total": data["total"], "missing": data["missing"]} for path, data in _ranked_by_file(by_file)
    ]
    return write_sarif(py_types_to_sarif(by_file_list, coverage))


def _pct_label(value: int, total: int) -> str:
    return f"{value * 100 // total}%"


def _file_coverage(data: dict) -> str:
    return f"{((data['total'] - data['missing']) * 100 // (data['total'] or 1))}%"


def _emit_py_types_detail(by_file: dict[str, dict], findings: list[dict], limit: int) -> None:
    if not by_file:
        return

    click.echo()
    click.echo(f"Top {min(limit, len(by_file))} files by missing-annotation count:")
    click.echo(
        format_table(
            ["File", "Total", "Missing", "Coverage"],
            [
                [path, str(data["total"]), str(data["missing"]), _file_coverage(data)]
                for path, data in _ranked_by_file(by_file, limit)
            ],
        )
    )
    _emit_py_types_findings(findings, limit)


def _emit_py_types_findings(findings: list[dict], limit: int) -> None:
    if not findings:
        return

    click.echo()
    click.echo("Sample findings (file:line, name, issues):")
    click.echo(
        format_table(
            ["Location", "Symbol", "Issues"],
            [
                [f"{item['path']}:{item['line']}", item["name"], ", ".join(item["issues"])]
                for item in _ranked_findings(findings, limit)
            ],
        )
    )


def _emit_py_types_text(verdict: str, stats: dict, coverage: int, detail: bool, limit: int) -> None:
    total = stats["total"]
    click.echo(f"VERDICT: {verdict}\n")
    click.echo(f"  public fn/methods:           {total}")
    click.echo(f"  missing return annotation:   {stats['no_return']} ({_pct_label(stats['no_return'], total)})")
    click.echo(
        f"  param without annotation:    {stats['untyped_params']} ({_pct_label(stats['untyped_params'], total)})"
    )
    click.echo(f"  uses ``Any``:                {stats['uses_any']} ({_pct_label(stats['uses_any'], total)})")
    click.echo(
        f"  legacy typing (Optional/Dict/List/Set/Tuple): {stats['old_typing']} ({_pct_label(stats['old_typing'], total)})"
    )

    if detail:
        _emit_py_types_detail(stats["by_file"], stats["findings"], limit)


def _maybe_exit_py_types_gate(ctx, ci_mode: bool, min_coverage: int | None, coverage: int) -> None:
    if ci_mode and min_coverage is not None and coverage < min_coverage:
        click.echo()
        click.echo(f"GATE FAILED: coverage {coverage}% < required {min_coverage}%")
        ctx.exit(5)


def _emit_empty_json(state: str, verdict: str, python_files: int, indexed_files: int) -> None:
    click.echo(
        to_json(
            json_envelope(
                "py-types",
                summary={
                    "verdict": verdict,
                    "state": state,
                    "total_public": 0,
                    "total_public_definition": _TOTAL_PUBLIC_DEFINITION,
                    "no_return_annotation": 0,
                    "untyped_params": 0,
                    "uses_any": 0,
                    "old_typing": 0,
                    "coverage_pct": 0,
                    "coverage_pct_definition": _COVERAGE_PCT_DEFINITION,
                    "python_files": python_files,
                    "indexed_files": indexed_files,
                    "partial_success": False,
                },
                by_file=[],
                findings=[],
                agent_contract={
                    "facts": _py_types_agent_facts(
                        verdict,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                    )
                },
            )
        )
    )


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
    detail = bool(detail or (ctx.obj.get("detail", False) if ctx.obj else False))
    ensure_index()
    with open_db(readonly=True) as conn:
        rows = _public_python_rows(conn, include_tests)
        if rows:
            empty_counts = None
        else:
            empty_counts = _python_index_counts(conn)

    total = len(rows)
    if total == 0:
        n_py_files, n_total_files = empty_counts or (0, 0)
        state = "no_python_files" if n_py_files == 0 else "no_public_python_functions"
        verdict = "no public Python functions/methods indexed"
        if json_mode:
            _emit_empty_json(state, verdict, n_py_files, n_total_files)
            return

        _emit_no_public_text(verdict, n_py_files, n_total_files)
        return

    stats = _type_stats(rows)
    coverage = _coverage_pct(total, stats["no_return"], stats["untyped_params"])
    verdict = _type_verdict(coverage, total)

    if json_mode:
        click.echo(to_json(_py_types_json_envelope(verdict, stats, coverage, detail, limit)))
        return

    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    if sarif_mode:
        click.echo(_py_types_sarif(stats["by_file"], coverage))
        return

    _emit_py_types_text(verdict, stats, coverage, detail, limit)

    # CI gate — exit 5 (mirrors EXIT_GATE_FAILURE used by ``roam rules
    # --ci``) when coverage falls below the requested threshold.
    _maybe_exit_py_types_gate(ctx, ci_mode, min_coverage, coverage)
