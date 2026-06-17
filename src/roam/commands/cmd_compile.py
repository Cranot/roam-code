"""Compile a freeform task into a structured artifact for an AI agent.

SARIF is deliberately NOT emitted: output is an agent-consumption
envelope (plan + facts), not file-located code findings — no
locations[] coordinates exist to populate.

`roam compile "<task>"` runs the ArtifactSelector: classifies the task by
procedure family (structural/synthesis/trace/freeform), picks the right
envelope shape (facts/lean/full), and emits a deterministic JSON envelope
the agent can consume.

Empirically validated 2026-05-28: FactsEnvelope strictly dominates vanilla
on capable models (Opus 4.8) — 99% of vanilla's quality at 54% of
vanilla's cost.

Output formats: ``--json`` (default), text.

Displaces:
  - Agent guessing structure from raw task text
  - Per-task prompt engineering by the user
  - "let me read every file in the repo" exploration loops
"""

from __future__ import annotations

import os

import click

from roam.capability import roam_capability
from roam.command_advice import validate_command_advice_many
from roam.output.formatter import json_envelope, to_json
from roam.plan.calibration import get_profile
from roam.plan.compiler import (
    compile_for_artifact,
    compile_plan,
    injection_advice,
    route_for_plan,
)


def _build_proof_stub(task: str, plan, env: dict, art_label: str) -> dict:
    """W76 — build a partial AgentChangeProofBundle stub primed with the
    compile envelope's signals. Downstream Guard fills in checks + verdict
    via `roam guard-pr` or `roam proof-bundle`.
    """
    import hashlib
    import time

    plan_obj = env.get("plan") or {}
    return {
        "schema": "roam-agent-change-proof-bundle-stub-v1",
        "intent": task[:240],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task_hash": hashlib.sha256(task.encode("utf-8", "replace")).hexdigest()[:16],
        "compiler_inputs": {
            "procedure": plan.procedure,
            "classifier_confidence": plan.classifier_confidence,
            "art_label": art_label,
            "named_paths": plan_obj.get("named_paths", []),
            "forbidden_paths": plan_obj.get("forbidden_paths", []),
            "repo_head": plan_obj.get("repo_head"),
        },
        # Hand-off contract: Guard reads `pending_checks` to know what to verify.
        "pending_checks": list(plan.required_checks or []),
        "verification_contract": {
            "required": list(plan.required_checks or []),
            "compiler_recommended_first": plan.recommended_first_command,
        },
        "verdict": "PENDING",  # Guard sets to PASS / NEEDS_REVIEW / BLOCKED
        "note": (
            "W76 stub — `roam compile --emit-proof-stub` primed this from "
            "the L1 envelope. Pipe through `roam proof-bundle` to fill "
            "executed_checks + verdict, then `roam guard-pr` for the "
            "GitHub Check Run."
        ),
    }


def _resolve_verify_enabled(cwd: str | None) -> bool:
    """Whether to surface the post-edit `roam verify --auto` hint.

    Resolution order (first decisive wins):
      1. ``ROAM_COMPILE_VERIFY`` env — the per-invocation control a host
         UI toggle sets. ``1``/``true``/``on``/``yes`` → force ON;
         ``0``/``false``/``off``/``no`` → force OFF (overrides the file, so
         the toggle can switch it off even in a repo with verify.yaml enabled).
      2. ``.roam/verify.yaml`` (``enabled:`` key) — the persistent per-repo
         opt-in (`roam verify --on/--off`).
    Never raises — config/IO errors resolve to OFF.
    """
    raw = (os.environ.get("ROAM_COMPILE_VERIFY") or "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    try:
        from pathlib import Path as _VPath

        _vcfg = _VPath(cwd or ".") / ".roam" / "verify.yaml"
        if _vcfg.exists():
            import yaml as _vyaml

            _vd = _vyaml.safe_load(_vcfg.read_text(encoding="utf-8")) or {}
            if isinstance(_vd, dict):
                return bool(_vd.get("enabled", True))
    except Exception:  # noqa: BLE001 — never let config break compile
        return False
    return False


def _emit_explain(task: str, json_mode: bool) -> None:
    from roam.plan.compiler import _explain_classifier

    diag = _explain_classifier(task)
    if json_mode:
        click.echo(to_json({"schema": "roam-compile-explain-v1", **diag}))
        return

    click.echo(f"VERDICT: classifier → {diag['winner']}")
    click.echo(f"task: {task[:200]}")
    click.echo("")
    click.echo("regex matches:")
    if not diag["regex_matches"]:
        click.echo("  (none — fell through to freeform_explore)")
    for name, hits in diag["regex_matches"].items():
        marker = "← winner" if name == diag["winner"] else ""
        click.echo(f"  {name:25s} {hits} {marker}")
    click.echo("")
    click.echo("rejected:")
    for row in diag["rejected"] or ["(none)"]:
        click.echo(f"  - {row}")
    if diag["named_paths_extracted"]:
        click.echo("")
        click.echo(f"named_paths_extracted: {diag['named_paths_extracted']}")
    click.echo("")
    click.echo("tiebreak rules (apply in order):")
    for row in diag["tiebreak_rules"]:
        click.echo(f"  {row}")


def _short_task_message(task: str) -> str:
    stripped = task.strip()
    return (
        f"task too short or unstructured ({len(stripped)} chars). "
        f"Pass a freeform sentence like 'find files coupled to "
        f"src/X.py' or 'write a pytest for handleY'."
    )


def _task_is_too_short(task: str) -> bool:
    stripped = task.strip()
    return len(stripped) < 10 or not any(c.isalpha() for c in stripped)


def _emit_short_task(task: str, json_mode: bool) -> None:
    msg = _short_task_message(task)
    if json_mode:
        click.echo(
            to_json(
                {
                    "schema": "roam-compile-error-v1",
                    "summary": {"verdict": "task_too_short", "partial_success": True, "error": msg},
                }
            )
        )
        return
    click.echo(f"VERDICT: task_too_short\n  {msg}", err=True)


def _emit_brief(task: str, json_mode: bool) -> None:
    from roam.plan.compiler import (
        _RECOMMENDED_FIRST_COMMAND,
        _classifier_confidence,
        _classify,
    )

    proc, _ = _classify(task)
    conf = _classifier_confidence(task, proc)
    rec = _RECOMMENDED_FIRST_COMMAND.get(proc, "")
    if json_mode:
        click.echo(
            to_json(
                {
                    "schema": "roam-compile-brief-v1",
                    "procedure": proc,
                    "classifier_confidence": conf,
                    "recommended_first": rec,
                }
            )
        )
        return
    click.echo(f"{proc} ({conf:.2f}): {rec}")


def _emit_route(task: str, plan, cwd: str, profile: str | None, json_mode: bool) -> None:
    routing = route_for_plan(plan, cwd=cwd, profile_name=profile)
    prof = get_profile(profile)
    if json_mode:
        click.echo(
            to_json(
                {
                    "schema": "roam-compile-route-v1",
                    "task": task[:240],
                    "procedure": plan.procedure,
                    "classifier_confidence": plan.classifier_confidence,
                    "routing": routing,
                    "calibration": {
                        "profile_name": prof.name,
                        "family": prof.family,
                        "measured_at": prof.measured_at,
                        "score_per_dollar_lift_vs_vanilla": prof.score_per_dollar_lift_vs_vanilla,
                        "notes": list(prof.notes),
                    },
                }
            )
        )
        return

    click.echo(f"VERDICT: route → {routing['model']} × {routing['envelope']} × {routing['contract_id']}")
    click.echo(f"task:               {task[:200]}")
    click.echo(f"procedure:          {plan.procedure}")
    click.echo(f"classifier_conf:    {plan.classifier_confidence}")
    click.echo(f"model:              {routing['model']}")
    click.echo(f"envelope:           {routing['envelope']}")
    click.echo(f"contract_id:        {routing['contract_id']}")
    click.echo(f"rationale:          {routing['rationale']}")
    click.echo(f"profile:            {prof.name} (validated {prof.measured_at})")
    click.echo(f"validated_lift:     +{prof.score_per_dollar_lift_vs_vanilla * 100:.0f}% score/$ vs vanilla")


def _artifact_for_request(plan, artifact: str, cwd: str) -> tuple[dict, str]:
    if artifact == "auto":
        return compile_for_artifact(plan, cwd=cwd)
    if artifact == "facts":
        return plan.to_facts_envelope(), "facts"
    if artifact == "lean":
        return plan.to_lean_envelope(), "lean"
    if artifact == "contract":
        return plan.to_facts_contract_envelope(), "contract"
    return plan.to_envelope(), "full"


def _compile_verify_hint(cwd: str) -> str | None:
    if not _resolve_verify_enabled(cwd):
        return None
    return "After editing, run `roam verify --auto` to check the change before finalizing."


def _next_commands_for_compile(verify_hint: str | None) -> list[str]:
    commands = [
        "roam compile <task> --artifact facts",
        "roam preflight <symbol>",
    ]
    if verify_hint:
        commands.append("roam verify --auto")
    return commands


def _command_checks_for_compile(plan, env: dict, next_commands: list[str]) -> list[dict]:
    command_advice_items = [("artifact.plan.recommended_first_command", plan.recommended_first_command)]
    roam_starter = (env.get("plan") or {}).get("roam_starter")
    if isinstance(roam_starter, str) and roam_starter.strip():
        command_advice_items.append(("artifact.plan.roam_starter", roam_starter))
    command_advice_items.extend(
        (f"agent_contract.next_commands[{idx}]", command) for idx, command in enumerate(next_commands)
    )
    return validate_command_advice_many(command_advice_items)


def _build_compile_json_envelope(task: str, plan, env: dict, art_label: str, verify_hint: str | None) -> dict:
    next_commands = _next_commands_for_compile(verify_hint)
    return json_envelope(
        "compile",
        summary={
            "verdict": f"{art_label}_envelope for {plan.procedure}",
            "task": task[:120],
            "procedure": plan.procedure,
            "artifact_type": art_label,
            "named_paths_count": len(plan.likely_files),
            "plan_quality": plan.plan_quality,
            "classifier_confidence": plan.classifier_confidence,
            "model_calls_avoided": plan.model_calls_avoided,
            "injection_advice": injection_advice(plan.procedure, task),
            "partial_success": False,
        },
        agent_contract={
            "facts": [
                f"Procedure classified as {plan.procedure}",
                f"Artifact selected: {art_label} envelope",
                f"{len(plan.likely_files)} likely files identified",
                f"{len(plan.forbidden_paths)} forbidden paths declared",
                f"Plan quality {plan.plan_quality} (heuristic 0-1)",
            ],
            "next_commands": next_commands,
            "command_checks": _command_checks_for_compile(plan, env, next_commands),
            "risks": [],
            "confidence": plan.plan_quality,
        },
        artifact=env,
    )


def _emit_prefetched_list(key: str, values: list) -> None:
    click.echo(f"  {key}: ({len(values)} items)")
    for item in values[:8]:
        click.echo(f"    - {item}")
    if len(values) > 8:
        click.echo(f"    ... and {len(values) - 8} more")


def _emit_prefetched_dict(key: str, values: dict) -> None:
    click.echo(f"  {key}:")
    for child_key, child_value in list(values.items())[:8]:
        click.echo(f"    {child_key}: {child_value}")


def _emit_prefetched_value(key: str, value) -> None:
    if isinstance(value, (str, int)):
        click.echo(f"  {key}: {value}")
    elif isinstance(value, list):
        _emit_prefetched_list(key, value)
    elif isinstance(value, dict):
        _emit_prefetched_dict(key, value)


def _emit_prefetched_answers(prefetched: dict) -> None:
    click.echo("")
    click.echo("PREFETCHED ANSWERS (do not re-run the tools that produced these):")
    for key, value in prefetched.items():
        _emit_prefetched_value(key, value)


def _emit_text_compile(task: str, plan, env: dict, art_label: str, verify_hint: str | None) -> None:
    click.echo(f"VERDICT: {art_label}_envelope for {plan.procedure}")
    click.echo(f"task:              {task[:200]}")
    click.echo(f"procedure:         {plan.procedure}")
    click.echo(f"artifact_type:     {art_label}")
    advice = injection_advice(plan.procedure, task)
    if advice != "inject":
        click.echo(f"injection_advice:  {advice}")
    click.echo(f"plan_quality:      {plan.plan_quality}")
    click.echo(f"classifier_conf:   {plan.classifier_confidence}")
    click.echo(f"named_paths:       {plan.likely_files}")
    if plan.procedure == "synthesis_query":
        click.echo(
            f"forbidden_paths:   {len(plan.forbidden_paths)} declared "
            f"(DO NOT edit files matching these patterns: lockfiles, env, "
            f"migrations, vendored, .git, .roam, internal)"
        )

    prefetched = (env.get("plan") or {}).get("prefetched_facts") if isinstance(env, dict) else None
    if art_label == "l1_probe" and prefetched:
        _emit_prefetched_answers(prefetched)
    else:
        click.echo(f"recommended_first: {plan.recommended_first_command}")
    if verify_hint:
        click.echo(f"post_edit_verify:  {verify_hint}")
    click.echo(f"model_calls_avoided: {plan.model_calls_avoided}")


@click.command()
@click.argument("task", type=str)
@click.option(
    "--artifact",
    type=click.Choice(["auto", "facts", "lean", "full", "contract"]),
    default="auto",
    help="Artifact shape. 'auto' uses the ArtifactSelector policy (recommended). "
    "'contract' = facts envelope + R10 per-procedure answer-shape template.",
)
@click.option(
    "--model-tier",
    type=click.Choice(["weak", "capable", "auto"]),
    default="auto",
    help="Model tier hint. Capable models prefer 'facts'; weak models prefer 'full'. "
    "Empirically locked 2026-05-28: facts dominates on Opus 4.8.",
)
@click.option(
    "--route",
    is_flag=True,
    default=False,
    help="Emit full routing decision (model + envelope + contract). Uses the "
    "empirically validated ALL-LEVERS routing — +220% score/$ vs vanilla "
    "on the 22-task benchmark. The output is consumable as an agent spec.",
)
@click.option(
    "--profile",
    type=str,
    default=None,
    help="Calibration profile name. Default 'claude-2026-05' (validated). "
    "Cross-model exploration: 'gpt-5-2026' (unvalidated placeholder).",
)
@click.option(
    "--brief",
    is_flag=True,
    default=False,
    help="Emit only procedure + classifier_confidence + first-command hint. "
    "Sub-300-char envelope for tight-context integration (e.g. host "
    "system-prompt injection on the first user message).",
)
@click.option(
    "--explain",
    "explain",
    is_flag=True,
    default=False,
    help="Dump the classifier's decision tree: which regexes matched, "
    "which procedures were rejected and why, what tiebreak rules applied. "
    "For debugging surprising routing.",
)
@click.option(
    "--emit-proof-stub",
    "emit_proof_stub",
    is_flag=True,
    default=False,
    help="W76 — emit a partial AgentChangeProofBundle stub alongside "
    "the envelope, primed with task + procedure + classifier_confidence + "
    "named_paths. The downstream Guard verifier can fill in checks + "
    "verdict. One flag = compile→Guard funnel.",
)
@click.option(
    "--probes",
    "probes_only",
    is_flag=True,
    default=False,
    help="W117 — emit ONLY the prefetched_facts dict (no envelope wrapping). "
    "Useful for CI scripts that just want the precomputed data + can "
    "skip the surrounding contract/routing layer.",
)
@click.pass_context
@roam_capability(
    name="compile",
    category="planning",
    summary="Compile a freeform task into a structured envelope for an AI agent",
    inputs=("task",),
    outputs=("artifact_envelope",),
    examples=(
        'roam compile "Find files coupled to src/roam/cli.py"',
        'roam compile "refactor auth module" --artifact facts',
    ),
    tags=("planning", "artifact-selector", "facts-envelope"),
)
def compile_(
    ctx: click.Context,
    task: str,
    artifact: str,
    model_tier: str,
    route: bool,
    profile: str | None,
    brief: bool,
    explain: bool,
    emit_proof_stub: bool,
    probes_only: bool,
) -> None:
    """Compile TASK (freeform string) into an agent-consumable envelope."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    if explain:
        _emit_explain(task, bool(json_mode))
        return

    # W34b (E5): reject obviously-empty/garbage task strings up front so
    # downstream paths don't silently produce a low-quality envelope. The
    # threshold is permissive — single-line tasks under 10 chars OR with no
    # letters at all (e.g. "???" / "...") look like accidents.
    if _task_is_too_short(task):
        _emit_short_task(task, bool(json_mode))
        # W23 regression fix (2026-06-02): exit-0 with partial_success envelope
        # so adversarial robustness holds (W82 baseline: "12/12 routed sensibly,
        # zero crashes"). Callers detect short-task degradation via the
        # `summary.verdict` field, not the exit code. The envelope still
        # carries `partial_success: True` and an actionable error message.
        ctx.exit(0)
        return

    # W33e (M2): brief mode does NOT need likely_files (the costly part of
    # compile_plan). Compute classifier + recommended_first directly so brief
    # is ~5-10ms instead of ~200-500ms when search-semantic would otherwise fire.
    if brief:
        _emit_brief(task, bool(json_mode))
        return

    # W57.5 — pass the working dir explicitly so the W56 envelope cache and
    # W57 plan/symbol-resolution caches actually engage at the CLI. (Without
    # this, the persistent caches are silently bypassed for every `roam
    # compile` invocation — they only worked from bench-compile harnesses
    # that passed cwd themselves.)
    _cwd = os.getcwd()
    plan = compile_plan(task, cwd=_cwd)

    # --route emits the full ALL-LEVERS routing decision (model + envelope +
    # contract). This is the production-grade output.
    if route:
        _emit_route(task, plan, _cwd, profile, bool(json_mode))
        return

    env, art_label = _artifact_for_request(plan, artifact, _cwd)

    # OUTPUT-side wiring (opt-in, NOT forced): Verify is the compiler's
    # post-generation acceptance phase for every procedure family. The user or
    # host can still switch it off, or tone it down via the host's verify scope/depth
    # knobs, but when enabled the envelope should always carry the follow-up.
    verify_hint = _compile_verify_hint(_cwd)

    # Build a roam-envelope-v1 envelope wrapping the artifact
    if json_mode:
        envelope = _build_compile_json_envelope(task, plan, env, art_label, verify_hint)
        # W117 — --probes mode short-circuits: emit just the
        # prefetched_facts dict. Useful for CI scripts.
        if probes_only:
            pf = (env.get("plan") or {}).get("prefetched_facts") or {}
            click.echo(to_json(pf))
            return
        # W76 — attach a proof-stub for compile→Guard integration.
        if emit_proof_stub:
            envelope["proof_stub"] = _build_proof_stub(task, plan, env, art_label)
        click.echo(to_json(envelope))
        return

    # Text mode
    _emit_text_compile(task, plan, env, art_label, verify_hint)
