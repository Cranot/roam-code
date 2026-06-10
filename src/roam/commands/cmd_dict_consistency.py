"""W210 — `roam dict-consistency` — would have prevented W181.

SARIF is deliberately NOT emitted: returns a registry-consistency audit envelope; missing-key reports are structural, not source-line findings.

Scan a Python file for top-level dict assignments whose keys are
string literals, then optionally report which dicts are MISSING keys
that other matched dicts have. Catches the W181-class bug instantly
where a new procedure name is added to one registry dict but not to
the parallel ones.

Usage:
    roam dict-consistency src/roam/plan/compiler.py --prefix _PROCEDURE
    roam dict-consistency src/roam/plan/compiler.py --prefix _RECOMMENDED \\
        --check-consistency
"""

from __future__ import annotations

import ast
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json


def _extract_string_dicts(tree: ast.AST) -> dict[str, list[str]]:
    """Return {dict_name: [string_keys, ...]} for every top-level dict
    assignment (incl. annotated assigns) in `tree` whose keys are string
    literals. Handles both `X = {...}` and `X: dict = {...}`."""
    dicts: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        target_name = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name):
                target_name = t.id
                value = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                target_name = node.target.id
                value = node.value
        if target_name is None or not isinstance(value, ast.Dict):
            continue
        keys = []
        for k in value.keys:
            if k is None:
                continue
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.append(k.value)
        if keys:
            dicts[target_name] = keys
    return dicts


@roam_capability(
    name="dict-consistency",
    category="planning",
    summary="Scan a Python module for dict-key string mismatches (find typos in _COMMANDS-style registries)",
    inputs=("path", "--prefix", "--contains", "--check-consistency"),
    outputs=("findings_envelope",),
)
@click.command(name="dict-consistency")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--prefix", default=None, help="Only include dicts whose name starts with this prefix.")
@click.option("--contains", default=None, help="Only include dicts whose name contains this substring.")
@click.option(
    "--check-consistency",
    is_flag=True,
    help="Report missing keys (keys present in any one matched dict but absent from another).",
)
@click.pass_context
def dict_consistency(ctx, path, prefix, contains, check_consistency):
    """Audit string-keyed dicts in a Python file for cross-dict
    consistency. Would have caught W181 (refactor_move missing from
    4 parallel registries) in one call."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    src = Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as exc:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "dict-consistency",
                        summary={"verdict": f"parse-failed: {exc}", "partial_success": False},
                        file_path=path,
                        error=str(exc),
                    )
                )
            )
        else:
            click.echo(f"VERDICT: parse-failed — {exc}")
        ctx.exit(2)
        return

    dicts = _extract_string_dicts(tree)
    if prefix:
        dicts = {k: v for k, v in dicts.items() if k.startswith(prefix)}
    if contains:
        dicts = {k: v for k, v in dicts.items() if contains in k}

    if not dicts:
        verdict = "no matching dicts found"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "dict-consistency",
                        summary={"verdict": verdict, "matched_dicts": 0},
                        file_path=path,
                        prefix=prefix,
                        contains=contains,
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    consistency: dict | None = None
    if check_consistency:
        all_keys: set[str] = set()
        for ks in dicts.values():
            all_keys.update(ks)
        per_dict_missing = {name: sorted(all_keys - set(ks)) for name, ks in dicts.items()}
        any_missing = any(per_dict_missing.values())
        consistency = {
            "all_keys_union": sorted(all_keys),
            "union_size": len(all_keys),
            "per_dict_missing": per_dict_missing,
            "is_consistent": not any_missing,
        }

    summary = {
        "verdict": (
            f"{len(dicts)} dict(s) matched"
            + (
                f"; {sum(1 for v in (consistency or {}).get('per_dict_missing', {}).values() if v)} have missing keys"
                if check_consistency
                else ""
            )
        ),
        "matched_dicts": len(dicts),
    }
    if consistency:
        summary["is_consistent"] = consistency["is_consistent"]
        summary["union_key_count"] = consistency["union_size"]

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "dict-consistency",
                    summary=summary,
                    file_path=path,
                    prefix=prefix,
                    contains=contains,
                    dicts={name: sorted(ks) for name, ks in dicts.items()},
                    consistency=consistency,
                )
            )
        )
        return

    # Text output
    click.echo(f"VERDICT: {summary['verdict']}")
    click.echo(f"file:    {path}")
    if prefix:
        click.echo(f"prefix:  {prefix}")
    if contains:
        click.echo(f"filter:  contains={contains!r}")
    click.echo("")
    for name in sorted(dicts):
        ks = dicts[name]
        first = ", ".join(ks[:5])
        more = f" (+{len(ks) - 5} more)" if len(ks) > 5 else ""
        click.echo(f"  {name}: {len(ks)} keys — {first}{more}")
    if consistency:
        click.echo("")
        click.echo(f"CONSISTENCY: {'OK' if consistency['is_consistent'] else 'MISMATCH'}")
        click.echo(f"  union has {consistency['union_size']} unique keys across {len(dicts)} dicts")
        for name, missing in consistency["per_dict_missing"].items():
            if missing:
                click.echo(f"  {name} MISSING: {', '.join(missing)}")
