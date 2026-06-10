"""`roam guard-diff` — compare two pr-bundles (verdict diff).

SARIF is deliberately NOT emitted: output is a verdict-delta envelope
across two bundles, not per-file findings — no source locations exist
to populate SARIF result[].locations[].

Shows what changed between two bundle snapshots:
  * Verdict promotion/demotion (e.g. blocked → pass)
  * Newly-introduced reasons (regressions)
  * Resolved reasons (fixed)
  * Changed-file delta
  * Required/executed/missing check delta

Two input modes:

  * `roam guard-diff <bundle1.json> <bundle2.json>` — two explicit bundle files
  * `roam guard-diff --from-log` — compare the two most recent log entries
    for the current branch (or pass --branch to pick a branch)

Helpful for the "did my last commit help?" workflow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.guard_enums import VERDICT_PRECEDENCE
from roam.guard_errors import guard_error_envelope
from roam.guard_log import read_log_entries
from roam.output.formatter import json_envelope, to_json
from roam.proof_bundle import compose_agent_change_proof_bundle, load_pr_bundle


def _compose_or_load(spec: dict | str | Path, repo_root: Path) -> dict:
    """Accept a v1 dict OR a pr-bundle path. Return a v1 dict."""
    if isinstance(spec, dict):
        return spec
    bundle = load_pr_bundle(Path(spec))
    return compose_agent_change_proof_bundle(bundle, repo_root=repo_root)


def _diff_verdicts(a: dict, b: dict) -> dict[str, Any]:
    """Compute the structured delta between two v1 bundles."""
    va = (a.get("verdict") or {}).get("value", "pass")
    vb = (b.get("verdict") or {}).get("value", "pass")
    pa = VERDICT_PRECEDENCE.get(va, -1)
    pb = VERDICT_PRECEDENCE.get(vb, -1)
    direction = "improved" if pb < pa else "regressed" if pb > pa else "unchanged"

    reasons_a = {r.get("code"): r for r in (a.get("verdict", {}) or {}).get("reasons", [])}
    reasons_b = {r.get("code"): r for r in (b.get("verdict", {}) or {}).get("reasons", [])}
    new_reasons = sorted(set(reasons_b) - set(reasons_a))
    resolved_reasons = sorted(set(reasons_a) - set(reasons_b))

    files_a = set(a.get("changed_files") or [])
    files_b = set(b.get("changed_files") or [])

    return {
        "verdict": {"from": va, "to": vb, "direction": direction},
        "reasons": {
            "added": new_reasons,
            "resolved": resolved_reasons,
            "shared": sorted(set(reasons_a) & set(reasons_b)),
        },
        "files": {
            "added": sorted(files_b - files_a),
            "removed": sorted(files_a - files_b),
            "shared_count": len(files_a & files_b),
            "delta": len(files_b) - len(files_a),
        },
        "checks": {
            "required": _count_delta(a, b, "verification_contract", "required"),
            "executed": _count_delta(a, b, "executed_checks"),
            "missing": _count_delta(a, b, "missing_checks"),
        },
    }


def _count_delta(a: dict, b: dict, *keys: str) -> dict[str, int]:
    """Return {from, to, delta} for a counted list field."""
    cursor_a: Any = a
    cursor_b: Any = b
    for k in keys:
        cursor_a = (cursor_a or {}).get(k)
        cursor_b = (cursor_b or {}).get(k)
    la = len(cursor_a or [])
    lb = len(cursor_b or [])
    return {"from": la, "to": lb, "delta": lb - la}


def _per_file_annotations(a: dict, b: dict) -> list[dict[str, Any]]:
    """Annotate each file in either bundle with status + reasons that name it.

    Status is one of: added (in b only), removed (in a only), shared (both).
    `reasons` are the verdict-reason codes from bundle B whose `detail` list
    references this file path. Useful for "which files caused the regression?"
    """
    files_a = set(a.get("changed_files") or [])
    files_b = set(b.get("changed_files") or [])
    reasons_b = (b.get("verdict") or {}).get("reasons") or []

    def _reasons_for(path: str) -> list[str]:
        hits: list[str] = []
        for r in reasons_b:
            detail = r.get("detail")
            if isinstance(detail, list) and path in detail:
                hits.append(r.get("code", "?"))
            elif isinstance(detail, str) and detail == path:
                hits.append(r.get("code", "?"))
        return sorted(set(hits))

    all_files = sorted(files_a | files_b)
    out: list[dict[str, Any]] = []
    for f in all_files:
        if f in files_a and f in files_b:
            status = "shared"
        elif f in files_b:
            status = "added"
        else:
            status = "removed"
        out.append(
            {
                "file": f,
                "status": status,
                "reasons": _reasons_for(f),
            }
        )
    return out


def _resolve_log_pair(root: Path, branch: str | None) -> tuple[dict | None, dict | None]:
    """Return the (older, newer) log entries for the given branch, or (None, None)."""
    entries = read_log_entries(root, limit=50)
    filtered = [e for e in entries if branch is None or e.get("branch") == branch]
    if len(filtered) < 2:
        return None, None
    # entries are most-recent-first
    newer, older = filtered[0], filtered[1]
    return older, newer


def _entry_to_v1_shaped(entry: dict) -> dict:
    """Map a verdict-log entry → a minimal v1-shaped dict so the diff
    function works uniformly. (Reasons get one stub entry per code.)"""
    return {
        "verdict": {
            "value": entry.get("verdict", "pass"),
            "reasons": [{"code": r["code"]} for r in (entry.get("reasons") or [])],
        },
        "changed_files": [],
        "verification_contract": {
            "required": [{}] * (entry.get("required") or 0),
            "skipped": [],
        },
        "executed_checks": [{}] * (entry.get("executed") or 0),
        "missing_checks": [{}] * (entry.get("missing") or 0),
    }


@click.command(name="guard-diff")
@click.argument("bundle_a", type=str, required=False)
@click.argument("bundle_b", type=str, required=False)
@click.option(
    "--from-log",
    "from_log",
    is_flag=True,
    default=False,
    help="Compare the two most recent verdict-log entries instead of explicit bundle files.",
)
@click.option(
    "--branch", "branch", type=str, default=None, help="When --from-log: only consider entries for this branch."
)
@click.option(
    "--by-file",
    "by_file",
    is_flag=True,
    default=False,
    help="Annotate each changed file with its added/removed/shared "
    "status + reasons that name it. Answers 'which files "
    "caused the verdict to move?'",
)
@click.pass_context
@roam_capability(
    name="guard-diff",
    category="planning",
    summary="Verdict diff between two pr-bundles (or two log entries)",
    inputs=("bundle_a", "bundle_b"),
    outputs=("verdict_delta",),
    examples=(
        "roam guard-diff bundle1.json bundle2.json",
        "roam guard-diff --from-log",
        "roam guard-diff --from-log --branch feat/auth",
    ),
    tags=("planning", "roam-guard", "diff"),
)
def guard_diff(
    ctx: click.Context, bundle_a: str | None, bundle_b: str | None, from_log: bool, branch: str | None, by_file: bool
) -> None:
    """Show the verdict delta between two bundle snapshots."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = Path(find_project_root() or Path.cwd())

    if from_log:
        older, newer = _resolve_log_pair(root, branch)
        if older is None or newer is None:
            msg = "Need at least 2 verdict-log entries to diff"
            fix = "Run `roam guard-pr` twice (or omit --branch to pool all branches), then re-run with --from-log."
            if json_mode:
                click.echo(
                    to_json(
                        guard_error_envelope(
                            "guard-diff",
                            "missing_required_field",
                            msg,
                            fix=fix,
                            context={"branch": branch},
                        )
                    )
                )
            else:
                click.echo(f"{msg}. {fix}", err=True)
            ctx.exit(2)
            return
        v1_a = _entry_to_v1_shaped(older)
        v1_b = _entry_to_v1_shaped(newer)
        label_a, label_b = older.get("ts", "older"), newer.get("ts", "newer")
    else:
        if not bundle_a or not bundle_b:
            msg = "Pass two bundle paths or use --from-log."
            if json_mode:
                click.echo(
                    to_json(
                        guard_error_envelope(
                            "guard-diff",
                            "missing_required_field",
                            msg,
                        )
                    )
                )
            else:
                click.echo(msg, err=True)
            ctx.exit(2)
            return
        try:
            v1_a = _compose_or_load(bundle_a, root)
            v1_b = _compose_or_load(bundle_b, root)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            if json_mode:
                click.echo(
                    to_json(
                        guard_error_envelope(
                            "guard-diff",
                            "bundle_load_failed",
                            str(e),
                            context={"bundle_a": bundle_a, "bundle_b": bundle_b},
                        )
                    )
                )
            else:
                click.echo(f"Failed to load bundle(s): {e}", err=True)
            ctx.exit(2)
            return
        label_a, label_b = bundle_a, bundle_b

    diff = _diff_verdicts(v1_a, v1_b)
    direction = diff["verdict"]["direction"]
    per_file = _per_file_annotations(v1_a, v1_b) if by_file else None

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "guard-diff",
                    summary={
                        "verdict": f"{diff['verdict']['from']} → {diff['verdict']['to']} ({direction})",
                        "direction": direction,
                        "from": diff["verdict"]["from"],
                        "to": diff["verdict"]["to"],
                        "added_reason_count": len(diff["reasons"]["added"]),
                        "resolved_reason_count": len(diff["reasons"]["resolved"]),
                        "files_delta": diff["files"]["delta"],
                        "partial_success": direction == "regressed",
                    },
                    agent_contract={
                        "facts": [
                            f"verdict {direction}: {diff['verdict']['from']} → {diff['verdict']['to']}",
                            f"{len(diff['reasons']['added'])} new reasons",
                            f"{len(diff['reasons']['resolved'])} resolved reasons",
                        ],
                        "next_commands": ["roam guard-pr --strict"],
                        "risks": [{"code": "regression", "detail": f"verdict moved to {diff['verdict']['to']}"}]
                        if direction == "regressed"
                        else [],
                    },
                    a=label_a,
                    b=label_b,
                    diff=diff,
                    per_file=per_file,
                )
            )
        )
        return

    arrows = {"improved": "→", "regressed": "→", "unchanged": "="}
    click.echo(f"VERDICT: {diff['verdict']['from']} {arrows[direction]} {diff['verdict']['to']} ({direction})")
    click.echo(f"  a: {label_a}")
    click.echo(f"  b: {label_b}")
    click.echo("")
    if diff["reasons"]["added"]:
        click.echo(f"New reasons ({len(diff['reasons']['added'])}):")
        for r in diff["reasons"]["added"]:
            click.echo(f"  + {r}")
    if diff["reasons"]["resolved"]:
        click.echo(f"Resolved reasons ({len(diff['reasons']['resolved'])}):")
        for r in diff["reasons"]["resolved"]:
            click.echo(f"  - {r}")
    if diff["reasons"]["shared"]:
        click.echo(f"Shared reasons ({len(diff['reasons']['shared'])}):")
        for r in diff["reasons"]["shared"][:5]:
            click.echo(f"  · {r}")
    click.echo("")
    fa, fb = diff["files"]["delta"], diff["files"]["shared_count"]
    click.echo(
        f"Files: {len(diff['files']['added'])} added, "
        f"{len(diff['files']['removed'])} removed, "
        f"{fb} shared (net delta {fa:+d})"
    )
    c = diff["checks"]
    click.echo(
        f"Checks: required {c['required']['from']}→{c['required']['to']} ({c['required']['delta']:+d}), "
        f"executed {c['executed']['from']}→{c['executed']['to']} ({c['executed']['delta']:+d}), "
        f"missing {c['missing']['from']}→{c['missing']['to']} ({c['missing']['delta']:+d})"
    )
    if per_file:
        click.echo("")
        click.echo(f"By file ({len(per_file)} total):")
        for entry in per_file[:20]:
            status = entry["status"]
            marker = {"added": "+", "removed": "-", "shared": "·"}[status]
            reasons = entry["reasons"]
            tail = f" → {','.join(reasons)}" if reasons else ""
            click.echo(f"  {marker} [{status:<7}] {entry['file']}{tail}")
        if len(per_file) > 20:
            click.echo(f"  · _… and {len(per_file) - 20} more files_")
