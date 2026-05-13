"""``roam laws`` — self-installing constitution.

Four subcommands:

* ``roam laws mine``    -- discover laws from index / tests / git history
* ``roam laws check``   -- enforce laws against a diff
* ``roam laws list``    -- print law id + description for browsing
* ``roam laws explain`` -- show the full evidence for one law

Pairs with R18 (policy DSL) and R24 (Agent Constitution): the
machine-readable ``rule`` dict each law carries is intentionally
shaped for R18 consumption, so an agent can pipe ``roam laws mine
--json`` into the policy engine without an intermediate format.
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.exit_codes import EXIT_GATE_FAILURE
from roam.laws.checker import check_laws, get_diff_text, parse_added
from roam.laws.miner import Law, mine_laws
from roam.laws.serializer import (
    DEFAULT_LOCATIONS,
    dump_laws_yaml,
    find_laws_file,
    load_laws_yaml,
    write_laws_file,
)
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="laws",
    category="workflow",
    summary=(
        "Self-installing constitution: mine a repo's unwritten rules"
        " from code + tests + git history, then enforce them."
    ),
    inputs=[],
    outputs=["laws", "violations"],
    examples=[
        "roam laws mine --top 10",
        "roam laws mine --out roam-laws.yml",
        "roam laws check --laws-file roam-laws.yml",
        "roam laws list",
        "roam laws explain snake_case_functions",
    ],
    tags=["laws", "agent-os", "constitution", "policy"],
    ai_safe=True,
    requires_index=True,
    maturity="beta",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.group("laws")
@click.pass_context
def laws_group(ctx):
    """Self-installing constitution.

    ``roam laws mine`` walks your index + git history and emits a list
    of inferred rules. ``roam laws check`` enforces those rules against
    the current diff (or any saved diff). Output is a
    ``roam-laws.yml`` checked into the repo, so future PRs are gated
    against the same rules.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# laws mine
# ---------------------------------------------------------------------------


@laws_group.command("mine")
@click.option(
    "--top",
    type=int,
    default=None,
    help="Keep only the top N highest-confidence laws.",
)
@click.option(
    "--min-confidence",
    type=click.Choice(["low", "medium", "high"]),
    default=None,
    help="Drop laws below this confidence level.",
)
@click.option(
    "--out",
    "out_path",
    default=None,
    help="Write YAML to this path (default: stdout).",
)
@click.pass_context
def laws_mine(ctx, top, min_confidence, out_path):
    """Discover laws from the indexed codebase + tests + git history.

    Examples:

    \b
      roam laws mine --top 10
      roam laws mine --out roam-laws.yml
      roam laws mine --json | jq .laws
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    ensure_index()
    with open_db(readonly=True) as conn:
        laws = mine_laws(conn, top=top)

    if min_confidence:
        rank = {"low": 1, "medium": 2, "high": 3}
        min_rank = rank.get(min_confidence, 1)
        laws = [law for law in laws if rank.get(law.confidence, 0) >= min_rank]

    yaml_text = dump_laws_yaml(laws)

    if out_path:
        try:
            target = Path(out_path)
            write_laws_file(target, laws)
            out_msg = f"wrote {target}"
        except Exception as exc:
            out_msg = f"error writing {out_path}: {exc}"
    else:
        out_msg = None

    high = sum(1 for law in laws if law.confidence == "high")
    medium = sum(1 for law in laws if law.confidence == "medium")
    low = sum(1 for law in laws if law.confidence == "low")
    verdict = f"Mined {len(laws)} laws ({high} high-confidence)"

    summary = {
        "verdict": verdict,
        "law_count": len(laws),
        "high_confidence": high,
        "medium_confidence": medium,
        "low_confidence": low,
        "partial_success": False,
    }
    if out_msg:
        summary["written_to"] = str(out_path)

    envelope = json_envelope(
        "laws-mine",
        budget=token_budget,
        summary=summary,
        laws=[law.to_dict() for law in laws],
        agent_contract={
            "facts": [verdict] + [
                f"{law.id}: {law.description} (confidence={law.confidence}, n={law.evidence.get('sample_size', 0)})"
                for law in laws[:5]
            ],
            "next_commands": [
                f"roam laws check --laws-file {out_path or 'roam-laws.yml'}",
                "roam laws list",
            ],
        },
    )

    auto_log(envelope, action="laws-mine", target=str(out_path or ""))

    if json_mode:
        click.echo(to_json(envelope))
        return

    if out_path:
        click.echo(f"VERDICT: {verdict} -> {out_path}")
    else:
        click.echo(f"VERDICT: {verdict}")
    click.echo("")
    if not laws:
        click.echo("(no laws met the conformance / sample thresholds)")
        return

    if out_path:
        # Still summarise on stdout when --out is used; the YAML is on disk.
        for law in laws:
            click.echo(
                f"  {law.id}  [{law.kind}/{law.confidence}]"
                f"  {law.description}"
            )
    else:
        click.echo(yaml_text)


# ---------------------------------------------------------------------------
# laws check
# ---------------------------------------------------------------------------


@laws_group.command("check")
@click.option(
    "--laws-file",
    default=None,
    help="Path to a roam-laws.yml. Default: ./roam-laws.yml or ./.roam/laws.yml.",
)
@click.option(
    "--diff-source",
    type=click.Choice(["working", "staged", "head", "pr"]),
    default="working",
    help="Which diff to gate.",
)
@click.option(
    "--diff-file",
    default=None,
    help="Read a saved diff from this path (overrides --diff-source).",
)
@click.option("--base-ref", default="main", help="Base ref for --diff-source pr.")
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit 5 if any blockers are found (CI gate behaviour).",
)
@click.pass_context
def laws_check(ctx, laws_file, diff_source, diff_file, base_ref, strict):
    """Run mined laws against a diff and report violations."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()

    # Load laws.
    laws_path = find_laws_file(root, laws_file)
    if not laws_path:
        verdict = (
            "no roam-laws.yml found — run `roam laws mine --out roam-laws.yml`"
            " to create one"
        )
        envelope = json_envelope(
            "laws-check",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "violations": 0,
                "partial_success": True,
                "state": "not_initialized",
            },
            violations=[],
            agent_contract={
                "facts": [verdict],
                "next_commands": ["roam laws mine --out roam-laws.yml"],
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    laws = load_laws_yaml(laws_path.read_text(encoding="utf-8"))
    if not laws:
        verdict = f"{laws_path} contains no laws"
        envelope = json_envelope(
            "laws-check",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "violations": 0,
                "partial_success": True,
                "state": "empty",
            },
            violations=[],
        )
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    # Resolve diff source.
    actual_source = "file" if diff_file else diff_source
    diff_text = get_diff_text(
        repo_root=root,
        diff_source=actual_source,
        diff_file=diff_file,
        base_ref=base_ref,
    )

    if not diff_text.strip():
        verdict = "no diff content — nothing to check"
        envelope = json_envelope(
            "laws-check",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "violations": 0,
                "law_count": len(laws),
                "diff_source": actual_source,
                "partial_success": False,
            },
            violations=[],
        )
        auto_log(envelope, action="laws-check", target=str(laws_path))
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    parsed = parse_added(diff_text)
    violations = check_laws(laws, parsed=parsed, repo_root=root)

    blockers = sum(1 for v in violations if v.severity == "blocker")
    warnings = sum(1 for v in violations if v.severity == "warning")
    advisories = sum(1 for v in violations if v.severity == "advisory")
    verdict = (
        f"{len(violations)} violations"
        f" ({blockers} blockers, {warnings} warnings, {advisories} advisories)"
    )

    envelope = json_envelope(
        "laws-check",
        budget=token_budget,
        summary={
            "verdict": verdict,
            "violations": len(violations),
            "blockers": blockers,
            "warnings": warnings,
            "advisories": advisories,
            "law_count": len(laws),
            "diff_source": actual_source,
            "partial_success": False,
        },
        violations=[v.to_dict() for v in violations],
        laws_file=str(laws_path),
        agent_contract={
            "facts": [verdict] + [
                f"{v.law_id}: {v.message} ({v.file}:{v.line})"
                for v in violations[:5]
            ],
            "next_commands": [
                "roam laws list",
                f"roam laws explain {violations[0].law_id}" if violations else "roam laws mine",
            ],
        },
    )

    auto_log(envelope, action="laws-check", target=str(laws_path))

    if json_mode:
        click.echo(to_json(envelope))
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo("")
        for v in violations[:50]:
            loc_str = f"{v.file}:{v.line}" if v.line else v.file
            click.echo(f"  [{v.severity}] {v.law_id} -- {v.message} ({loc_str})")
        if len(violations) > 50:
            click.echo(f"  (+ {len(violations) - 50} more)")

    if strict and blockers > 0:
        ctx.exit(EXIT_GATE_FAILURE)


# ---------------------------------------------------------------------------
# laws list
# ---------------------------------------------------------------------------


@laws_group.command("list")
@click.option("--laws-file", default=None, help="Path to a roam-laws.yml.")
@click.pass_context
def laws_list(ctx, laws_file):
    """Dump law id + description for browsing."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    laws_path = find_laws_file(root, laws_file)
    if not laws_path:
        verdict = "no roam-laws.yml found"
        envelope = json_envelope(
            "laws-list",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "law_count": 0,
                "partial_success": True,
                "state": "not_initialized",
            },
            laws=[],
        )
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo("Hint: run `roam laws mine --out roam-laws.yml`.")
        return

    laws = load_laws_yaml(laws_path.read_text(encoding="utf-8"))
    verdict = f"{len(laws)} laws in {laws_path.name}"
    envelope = json_envelope(
        "laws-list",
        budget=token_budget,
        summary={
            "verdict": verdict,
            "law_count": len(laws),
            "partial_success": False,
        },
        laws=[law.to_dict() for law in laws],
        laws_file=str(laws_path),
    )

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    if not laws:
        click.echo("(empty)")
        return
    for law in laws:
        click.echo(
            f"  {law.id}  [{law.kind}/{law.confidence}]  {law.description}"
        )


# ---------------------------------------------------------------------------
# laws explain
# ---------------------------------------------------------------------------


@laws_group.command("explain")
@click.argument("law_id")
@click.option("--laws-file", default=None, help="Path to a roam-laws.yml.")
@click.pass_context
def laws_explain(ctx, law_id, laws_file):
    """Show the full evidence dict for one law."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    laws_path = find_laws_file(root, laws_file)
    if not laws_path:
        verdict = "no roam-laws.yml found"
        envelope = json_envelope(
            "laws-explain",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "law_id": law_id,
                "partial_success": True,
                "state": "not_initialized",
            },
            law=None,
        )
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    laws = load_laws_yaml(laws_path.read_text(encoding="utf-8"))
    match = next((law for law in laws if law.id == law_id), None)
    if match is None:
        verdict = f"no law with id '{law_id}'"
        envelope = json_envelope(
            "laws-explain",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "law_id": law_id,
                "partial_success": True,
                "state": "not_found",
            },
            law=None,
            available_ids=[law.id for law in laws],
        )
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo("Available ids:")
            for law in laws:
                click.echo(f"  {law.id}")
        return

    verdict = f"{match.id} -- {match.description}"
    envelope = json_envelope(
        "laws-explain",
        budget=token_budget,
        summary={
            "verdict": verdict,
            "law_id": match.id,
            "kind": match.kind,
            "confidence": match.confidence,
            "severity": match.severity,
            "partial_success": False,
        },
        law=match.to_dict(),
    )

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    click.echo(f"  id:          {match.id}")
    click.echo(f"  kind:        {match.kind}")
    click.echo(f"  severity:    {match.severity}")
    click.echo(f"  confidence:  {match.confidence}")
    click.echo("")
    click.echo("  Evidence:")
    for k, v in match.evidence.items():
        click.echo(f"    {k}: {v}")
    click.echo("")
    click.echo("  Rule (machine-readable):")
    for k, v in match.rule.items():
        click.echo(f"    {k}: {v}")
