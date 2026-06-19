"""``roam dogfood`` — run the v2 stack on the current repo in one shot.

Bundles ``audit`` + ``pr-analyze`` (against uncommitted diff) + audit-trail
emission + ``audit-trail-conformance-check`` into a single envelope. The
"show me everything roam can do for me" command — equally useful as a
dev's local self-check and as a customer's first-touch demo.

Lives at the top of the funnel: a new user runs ``roam dogfood`` once
and sees the full v2 product surface in 30 seconds. No subscription,
no API key, no upload.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because dogfood outputs are invocation-scoped compound audit
envelopes — not per-location violations. ``dogfood`` delegates SARIF
emission to composed subcommands (``audit``, ``pr-analyze``, etc.) when
their own ``--sarif`` flag fires directly. See action.yml
_SUPPORTED_SARIF allowlist + W1145 / W1175-RESEARCH Bucket B
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import json as _json

import click
from click.testing import CliRunner

from roam.capability import roam_capability
from roam.commands.audit_trail_helpers import DEFAULT_AUDIT_TRAIL_PATH
from roam.commands.git_helpers import git_metadata
from roam.commands.resolve import ensure_index
from roam.output.formatter import json_envelope, to_json


def _run_subcommand(args: list[str]) -> dict:
    """Invoke a roam subcommand in-process and return its parsed envelope.

    On JSON-parse failure or non-zero exit, returns a sentinel dict carrying
    ``_subcommand_failed=True`` so the caller's compositor can detect it
    instead of silently treating an error envelope as a success (Pattern 2 —
    silent fallback). The legacy ``error``/``exit_code``/``raw_output_excerpt``
    keys are preserved for callers / tests that pin them.
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, args)
    try:
        return _json.loads(result.output)
    except Exception as exc:  # noqa: BLE001 — defensive
        return {
            "_subcommand_failed": True,
            "error": f"{' '.join(args[:3])} failed to produce JSON: {exc}",
            "exit_code": result.exit_code,
            "raw_output_excerpt": result.output[:300] if result.output else "",
        }


@roam_capability(
    name="dogfood",
    category="health",
    summary="Run the v2 stack on the current repo: audit + pr-analyze + audit-trail.",
    inputs=[],
    outputs=["audit", "pr_analyze", "audit_trail", "verdict"],
    examples=["roam dogfood", "roam --json dogfood", "roam dogfood --no-audit-trail"],
    tags=["health", "demo"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command(name="dogfood")
@click.option(
    "--audit/--no-audit",
    default=True,
    show_default=True,
    help="Include the structured audit (health + debt + dead + danger zones).",
)
@click.option(
    "--pr-analyze/--no-pr-analyze",
    "pr_analyze_on",
    default=True,
    show_default=True,
    help="Include pr-analyze on uncommitted changes (the v2 Agent Review engine).",
)
@click.option(
    "--audit-trail/--no-audit-trail",
    "audit_trail_on",
    default=True,
    show_default=True,
    help="Append a record to .roam/audit-trail.jsonl + check Article 12 conformance.",
)
@click.option(
    "--rules",
    "rules_file",
    type=click.Path(),
    default=None,
    help="Pass-through to pr-analyze (default: auto-detect .roam/rules.yml).",
)
@click.pass_context
def dogfood_cmd(
    ctx,
    audit: bool,
    pr_analyze_on: bool,
    audit_trail_on: bool,
    rules_file: str | None,
) -> None:
    """Run the v2 stack on the current repo and emit one combined envelope.

    \b
    Examples:
      roam dogfood                       # text summary of audit + pr-analyze + conformance
      roam --json dogfood                # full envelope for tooling
      roam dogfood --no-audit-trail      # skip the audit-trail record
      roam dogfood --rules .roam/rules.yml

    Designed as the first-touch experience for new users + as a local
    self-check that surfaces everything Roam can show you in one command.
    Reuses the same engines that power Roam Cloud Lite + Roam Agent Review.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    # W607-D: Pattern-2 consumer-layer wiring — thread a ``warnings_out``
    # bucket through the dogfood aggregation path. cmd_dogfood is a
    # cross-detector aggregator: it invokes subcommands (audit / pr-analyze
    # / audit-trail-conformance-check) via ``_run_subcommand`` and composes
    # their envelopes. ``_run_subcommand`` already returns
    # ``_subcommand_failed`` sentinels for parse failures (Pattern-2 silent
    # fallback disclosure), but exceptions raised during the aggregation
    # itself (git_metadata, summary composition, subcommand invocation)
    # historically bubbled as Click tracebacks. The outer-guard boundary
    # surfaces those via the canonical
    # ``dogfood_aggregation_failed:<exc_class>:<detail>`` marker family,
    # mirroring cmd_findings W607-C ``findings_query_failed:...`` and
    # cmd_retrieve W607-B ``retrieve_pipeline_failed:...`` idioms.
    # Empty bucket -> byte-identical envelope (hash-stable per
    # ``json_envelope`` W817 always-emit discipline).
    warnings_out: list[str] = []

    # W607-AV: per-phase substrate-CALL marker plumbing (additive to W607-D's
    # outer-guard). cmd_dogfood is a high-traffic aggregator invoked in
    # dogfood eval loops where silent helper failures are highest-cost. The
    # outer-guard only catches the aggregation block wholesale; W607-AV adds
    # a per-phase ``_run_check_av`` wrapper that surfaces
    # ``dogfood_<phase>_failed:<exc_class>:<detail>`` markers for each
    # substrate boundary (git_metadata, audit_subcommand,
    # pr_analyze_subcommand, conformance_subcommand, compose_summary,
    # serialize_envelope). A raise in one phase no longer aborts the
    # remaining phases — degraded but consistent envelope emission. Mirrors
    # the canonical W607-AQ ``_run_check_aq`` template (cmd_vulns).
    _w607av_warnings_out: list[str] = []

    def _run_check_av(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AV marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``dogfood_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607av_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607av_warnings_out.append(f"dogfood_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    sections: dict = {}
    git_meta: dict = {}
    try:
        # NOTE: git_metadata is intentionally NOT wrapped in _run_check_av --
        # it is owned by the W607-D outer-guard's
        # ``dogfood_aggregation_failed:`` marker family (preserved for
        # contract parity with cmd_findings W607-C / cmd_retrieve W607-B
        # outer-guard idioms). W607-AV's per-phase plumbing covers the
        # subcommand-dispatch substrates downstream of git_metadata.
        git_meta = git_metadata()

        # 1. roam audit — health / debt / dead / danger zone
        if audit:
            sections["audit"] = _run_check_av(
                "audit_subcommand",
                _run_subcommand,
                ["--json", "audit"],
                default={"_subcommand_failed": True, "error": "audit substrate raised"},
            )

        # 2. roam pr-analyze on uncommitted diff (with audit-trail when requested)
        if pr_analyze_on:
            pr_args = ["--json", "pr-analyze"]
            if rules_file:
                pr_args.extend(["--rules", rules_file])
            if audit_trail_on:
                pr_args.append("--audit-trail")
            sections["pr_analyze"] = _run_check_av(
                "pr_analyze_subcommand",
                _run_subcommand,
                pr_args,
                default={"_subcommand_failed": True, "error": "pr_analyze substrate raised"},
            )

        # 3. audit-trail-conformance-check (only meaningful if a trail exists)
        if audit_trail_on and DEFAULT_AUDIT_TRAIL_PATH.exists():
            sections["conformance"] = _run_check_av(
                "conformance_subcommand",
                _run_subcommand,
                ["--json", "audit-trail-conformance-check"],
                default={"_subcommand_failed": True, "error": "conformance substrate raised"},
            )
    except Exception as exc:  # noqa: BLE001 — W607-D outer-guard
        # W607-D outer-guard: aggregation raised before sections completed
        # (git_metadata failure, subcommand-invocation crash, unexpected I/O
        # error). Disclose loudly via the canonical
        # ``dogfood_aggregation_failed:<exc_class>:<detail>`` marker and fall
        # back to empty git_meta + whatever sections were populated so the
        # envelope still emits a consistent contract.
        warnings_out.append(f"dogfood_aggregation_failed:{type(exc).__name__}:{exc}")
        git_meta = {}

    # ---- Compose the summary line ----
    def _compose_summary():
        """Compose the verdict + summary scaffold from gathered sections.

        Wrapped in ``_run_check_av("compose_summary", ...)`` so a raise here
        (e.g. an unexpected envelope shape that broke `.get` chaining)
        becomes a structured marker instead of a Click traceback.
        """
        audit_summary = (sections.get("audit") or {}).get("summary") or {}
        pr_summary = (sections.get("pr_analyze") or {}).get("summary") or {}
        conf_summary = (sections.get("conformance") or {}).get("summary") or {}

        health_score = audit_summary.get("health_score") or audit_summary.get("score")
        pr_verdict = pr_summary.get("verdict")
        conf_score = conf_summary.get("score")

        failed_sections = sorted(k for k, v in sections.items() if isinstance(v, dict) and v.get("_subcommand_failed"))

        parts = []
        if health_score is not None:
            parts.append(f"health {health_score}")
        if pr_verdict:
            parts.append(f"pr-analyze {pr_verdict}")
        if conf_score is not None:
            parts.append(f"conformance {conf_score}/100")
        if failed_sections:
            parts.append(f"{len(failed_sections)} section(s) failed: {', '.join(failed_sections)}")
        verdict_text = " · ".join(parts) if parts else "no sections enabled"

        summary = {
            "verdict": verdict_text,
            "health_score": health_score,
            "pr_verdict": pr_verdict,
            "conformance_score": conf_score,
            "git_sha": git_meta.get("git_sha"),
            "git_branch": git_meta.get("git_branch"),
            "sections_run": sorted(sections.keys()),
        }
        if failed_sections:
            summary["partial_success"] = True
            summary["failed_sections"] = failed_sections
        return summary, verdict_text, pr_summary, conf_summary, health_score, pr_verdict, conf_score

    composed = _run_check_av(
        "compose_summary",
        _compose_summary,
        default=(
            {"verdict": "compose_summary substrate raised", "sections_run": sorted(sections.keys())},
            "compose_summary substrate raised",
            {},
            {},
            None,
            None,
            None,
        ),
    )
    summary, verdict_text, pr_summary, conf_summary, health_score, pr_verdict, conf_score = composed

    # W607-AV: merge the per-phase marker bucket into the canonical
    # warnings_out channel (alongside any W607-D outer-guard markers).
    combined_warnings = list(warnings_out) + list(_w607av_warnings_out)

    if combined_warnings:
        # W607-D / W607-AV Pattern-2 disclosure: surface markers AND flip
        # partial_success so consumers can distinguish "clean dogfood
        # run" from "dogfood aggregation hit a substrate fault".
        summary["warnings_out"] = list(combined_warnings)
        summary["partial_success"] = True

    if json_mode:
        envelope_text = _run_check_av(
            "serialize_envelope",
            lambda: to_json(
                json_envelope(
                    "dogfood",
                    summary=summary,
                    sections=sections,
                    **({"warnings_out": list(combined_warnings)} if combined_warnings else {}),
                )
            ),
            default=None,
        )
        if envelope_text is None:
            # serialize_envelope raised — re-merge bucket (it may have grown)
            # and produce a minimal manually-crafted envelope so the contract
            # still holds.
            combined_warnings = list(warnings_out) + list(_w607av_warnings_out)
            summary["warnings_out"] = list(combined_warnings)
            summary["partial_success"] = True
            envelope_text = to_json(
                json_envelope(
                    "dogfood",
                    summary=summary,
                    sections={},
                    warnings_out=list(combined_warnings),
                )
            )
        click.echo(envelope_text)
    else:
        click.echo(f"VERDICT: {verdict_text}")
        click.echo()
        if health_score is not None:
            click.echo(f"  audit health:    {health_score}/100")
        if pr_verdict:
            blast = pr_summary.get("blast_radius", "?")
            ai = pr_summary.get("ai_likelihood", "?")
            rv = pr_summary.get("rule_violations", 0)
            click.echo(f"  pr-analyze:      {pr_verdict}  (blast {blast}, ai {ai}, rules {rv})")
        if conf_score is not None:
            passed = conf_summary.get("checks_passed", "?")
            total = conf_summary.get("checks_total", "?")
            click.echo(f"  conformance:     {conf_score}/100  ({passed}/{total} checks passed)")
        click.echo()
        click.echo("Drill in:")
        if audit:
            click.echo("  roam audit                            # full health / debt / dead breakdown")
        if pr_analyze_on:
            click.echo("  roam pr-analyze --explain             # rationale + concerns")
        if audit_trail_on:
            click.echo("  roam audit-trail-export --aggregate   # procurement summary")
            click.echo("  roam audit-trail-conformance-check    # EU AI Act Article 12 score")
