"""Fulfil the cross-family review obligations (orchestration graph 1b/4b).

The compile envelope tells an agent its plan must survive a critique by a
different model family before it implements, and that "done" needs a
same-shaped verdict on the finished diff (see
``plan.compiler.orchestration_contract_for``). :mod:`roam.review_receipt`
decides whether that happened; :mod:`roam.verdict` blocks when it did not.

This module closes the gap between those two: it makes the obligation
CHEAP TO FULFIL. Without it an agent must hash the artifact by hand,
compose receipt JSON against a schema it cannot see, and drop the file
where the gate happens to look. A gate that costs more to satisfy than to
strip gets stripped -- so ergonomics here is not polish, it is what keeps
the mechanism alive.

Two commands, both deliberately thin over already-verified machinery:

  roam review-request --phase 1b --artifact plan.md
      Emits the reviewer brief: the criteria, the artifact bytes, and the
      exact receipt skeleton to fill. Carries NO builder rationale -- the
      reviewer must attack the artifact, not be anchored by the author's
      defence of it.

  roam review-accept --phase 1b --artifact plan.md \
      --builder-family claude --reviewer-family openai \
      --decision accept [--finding 'title|severity']...
      DERIVES the digest from the artifact's bytes, composes the receipt,
      and refuses to write one that would not verify.

SARIF is deliberately NOT emitted: a review receipt is a claim about an
artifact as a whole (its digest, its reviewer, its decision), not a set of
file-located code findings — there are no locations[] coordinates to
populate.

The trust rule this module obeys: it never accepts a digest, and it never
writes a receipt it has not just run through the verifier. A writer that
can emit records its own reader rejects is a broken pair.

HONEST SCOPE. A receipt's path is keyed on (phase, artifact digest), so a
second review of the SAME bytes overwrites the first -- including
replacing a recorded rejection with an acceptance. That is not a new
weakness: an agent with write access to the repo could always delete the
file. It is the same disclosed limit the whole local-receipt design
carries (occurrence is declared, not attested), and it is why an ordering
ledger was designed and then NOT built -- a mechanism whose own state the
agent can rewrite is disclosure, not enforcement. Making a rejection
un-overwritable requires an authority outside the agent's write boundary
(CI recomputing, or a signing key it cannot read), not more local
bookkeeping.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json
from roam.review_receipt import (
    DIGEST_SCHEME,
    FINDING_SEVERITIES,
    PHASE_CRITERIA_TEMPLATE,
    RECEIPT_SCHEMA,
    REVIEW_DECISIONS,
    REVIEW_FAMILIES,
    canonical_artifact_sha256,
    verify_receipt,
)

# Short aliases so a human types `--phase 1b`, not the wire name.
PHASE_ALIASES: dict[str, str] = {
    "1b": "1b_plan_critique",
    "4b": "4b_done_verdict",
    "1b_plan_critique": "1b_plan_critique",
    "4b_done_verdict": "4b_done_verdict",
}

# Where receipts live. One directory, one file per (phase, digest): a
# receipt is ABOUT specific bytes, so its identity is those bytes -- two
# reviews of two plan revisions never collide, and a receipt cannot be
# silently reused for a different artifact by living at a stable path.
REVIEWS_DIR = ".roam/reviews"

# The criteria each phase's reviewer is held to. These live HERE, in the
# tool, not in the receipt: criteria an agent could edit before the
# reviewer reads them would let the review question itself be forged.
CRITERIA: dict[str, tuple[str, ...]] = {
    "1b_plan_critique": (
        "Attack this plan as a set of UNPROVEN HYPOTHESES. You are a different "
        "model family from its author; your value is the blind spots that family shares.",
        "For each major decision: name the concrete failure sequence (inputs/state -> "
        "wrong outcome), not a general concern.",
        "Name any stance the plan takes where the OPPOSITE is correct, argued at full strength.",
        "Name what to CUT as complexity without proportional value.",
        "Return decision=accept only if you found no blocking defect. Findings with "
        "severity blocker/critical/high are blocking.",
    ),
    "4b_done_verdict": (
        "Review this FINISHED DIFF against the acceptance criteria it claims to meet. "
        "You are a different model family from its author.",
        "Verify the change does what it says on real paths -- not that it compiles or that tests were claimed to pass.",
        "Name anything the diff does BEYOND its stated scope, and anything stated but absent.",
        "Check the diff's own tests: would they fail if the change were reverted?",
        "Return decision=accept only if you found no blocking defect. Findings with "
        "severity blocker/critical/high are blocking.",
    ),
}


def _resolve_phase(phase: str) -> str:
    resolved = PHASE_ALIASES.get(phase.strip().lower())
    if resolved is None:
        raise click.BadParameter(f"phase must be one of {sorted(set(PHASE_ALIASES))}, got {phase!r}")
    return resolved


def _read_artifact(artifact: str) -> tuple[Path, bytes]:
    """Read the artifact as RAW BYTES.

    Binary mode on purpose: the digest must be over what is actually on
    disk, not over what a text decoder reconstructed from it.
    """
    path = Path(artifact)
    if not path.is_file():
        raise click.ClickException(
            f"artifact not found or not a regular file: {artifact}. "
            "The review must be OF something; there is no default."
        )
    return path, path.read_bytes()


def _parse_findings(raw: tuple[str, ...]) -> list[dict]:
    """Parse ``'title|severity'`` pairs into finding records."""
    findings = []
    for item in raw:
        title, _, severity = item.partition("|")
        severity = severity.strip().lower() or "medium"
        if severity not in FINDING_SEVERITIES:
            raise click.BadParameter(f"severity must be one of {sorted(FINDING_SEVERITIES)}, got {severity!r}")
        findings.append({"title": title.strip(), "severity": severity})
    return findings


@roam_capability(
    name="review-request",
    category="workflow",
    summary="Emit the brief for a cross-family review obligation (1b/4b)",
    maturity="experimental",
    mcp_expose=False,
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
@click.command("review-request")
@click.option("--phase", required=True, help="1b (plan critique) or 4b (done verdict).")
@click.option("--artifact", required=True, help="File under review: the plan, or the diff.")
@click.option("--json", "json_mode", is_flag=True, help="Emit the brief as JSON.")
def review_request_cmd(phase: str, artifact: str, json_mode: bool) -> None:
    """Emit the brief to hand a different-family reviewer.

    Carries the criteria and the artifact, and NOT the author's rationale:
    a reviewer given the defence tends to grade the defence.
    """
    resolved = _resolve_phase(phase)
    path, data = _read_artifact(artifact)
    digest = canonical_artifact_sha256(data)
    criteria = CRITERIA[resolved]

    skeleton = {
        "schema": RECEIPT_SCHEMA,
        "phase": resolved,
        "criteria_template": PHASE_CRITERIA_TEMPLATE[resolved],
        "builder_family": "<your family: " + "|".join(sorted(REVIEW_FAMILIES)) + ">",
        "reviewer_family": "<reviewer family, MUST differ from builder>",
        "artifact_sha256": digest,
        "digest_scheme": DIGEST_SCHEME,
        "decision": "<" + "|".join(sorted(REVIEW_DECISIONS)) + ">",
        "findings": [{"title": "...", "severity": "|".join(sorted(FINDING_SEVERITIES))}],
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "review-request",
                    summary={
                        "verdict": f"review brief for {resolved}",
                        "phase": resolved,
                        "artifact": str(path),
                        "artifact_sha256": digest,
                        "digest_scheme": DIGEST_SCHEME,
                        "partial_success": False,
                    },
                    criteria=list(criteria),
                    artifact_bytes=data.decode("utf-8", "replace"),
                    receipt_skeleton=skeleton,
                )
            )
        )
        return

    click.echo(f"REVIEW REQUEST — {resolved}")
    click.echo(f"artifact:       {path}")
    click.echo(f"artifact_sha256: {digest}  ({DIGEST_SCHEME})")
    click.echo("")
    click.echo("Send everything below to a model from a DIFFERENT family than yours.")
    click.echo("Do not include your reasoning for the plan — it anchors the reviewer.")
    click.echo("")
    click.echo("--- criteria ---")
    for line in criteria:
        click.echo(f"  {line}")
    click.echo("")
    click.echo("--- artifact under review ---")
    click.echo(data.decode("utf-8", "replace"))
    click.echo("--- end artifact ---")
    click.echo("")
    click.echo("Record the outcome with:")
    click.echo(
        f"  roam review-accept --phase {phase} --artifact {path} \\\n"
        "      --builder-family <yours> --reviewer-family <theirs> \\\n"
        "      --decision <accept|revise|reject|error> [--finding 'title|severity']..."
    )


@roam_capability(
    name="review-accept",
    category="workflow",
    summary="Record a cross-family review outcome as a verifiable receipt",
    maturity="experimental",
    mcp_expose=False,
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
@click.command("review-accept")
@click.option("--phase", required=True, help="1b (plan critique) or 4b (done verdict).")
@click.option("--artifact", required=True, help="File that was reviewed.")
@click.option("--builder-family", required=True, help="Family that AUTHORED the artifact.")
@click.option("--reviewer-family", required=True, help="Family that REVIEWED it; must differ.")
@click.option("--decision", required=True, help="accept | revise | reject | error.")
@click.option(
    "--finding",
    "findings_raw",
    multiple=True,
    help="Repeatable 'title|severity'. Blocking severities override an accept.",
)
@click.option("--reviewer-model", default=None, help="Optional: the exact model, for provenance.")
@click.option("--json", "json_mode", is_flag=True, help="Emit the result as JSON.")
def review_accept_cmd(
    phase: str,
    artifact: str,
    builder_family: str,
    reviewer_family: str,
    decision: str,
    findings_raw: tuple[str, ...],
    reviewer_model: str | None,
    json_mode: bool,
) -> None:
    """Record a review outcome as a receipt the verdict gate can read.

    The digest is DERIVED here from the artifact's current bytes -- there
    is deliberately no ``--digest`` flag, because a digest supplied
    alongside the receipt would let both sides of the verifier's
    comparison come from the party being judged.

    The receipt is verified BEFORE it is written. A writer whose output
    its own reader rejects is a broken pair, and the failure should
    surface here where it is cheap, not at verdict time where it reads as
    an unexplained block.
    """
    resolved = _resolve_phase(phase)
    path, data = _read_artifact(artifact)
    digest = canonical_artifact_sha256(data)

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "phase": resolved,
        "criteria_template": PHASE_CRITERIA_TEMPLATE[resolved],
        "builder_family": builder_family.strip().lower(),
        "reviewer_family": reviewer_family.strip().lower(),
        "artifact_sha256": digest,
        "digest_scheme": DIGEST_SCHEME,
        "decision": decision.strip().lower(),
        "findings": _parse_findings(findings_raw),
    }
    if reviewer_model:
        receipt["reviewer_model"] = reviewer_model

    repo_root = Path.cwd()
    out_dir = repo_root / REVIEWS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{resolved}-{digest[:16]}.json"

    # Write, then verify what was written, then keep or remove. Verifying
    # the in-memory dict would prove less: the bytes on disk are what the
    # gate reads, and encoding or permission faults live between the two.
    out_path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    result = verify_receipt(
        out_path,
        expected_phase=resolved,
        artifact_bytes=data,
        repo_root=repo_root,
    )
    if result["status"] in ("receipt_malformed", "family_unresolved", "same_family", "wrong_phase"):
        out_path.unlink(missing_ok=True)
        raise click.ClickException(
            f"refusing to write a receipt the verifier rejects: {result['status']} — {result['reason']}"
        )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "review-accept",
                    summary={
                        "verdict": f"{result['status']} for {resolved}",
                        "phase": resolved,
                        "status": result["status"],
                        "receipt": str(out_path.relative_to(repo_root)),
                        "artifact_sha256": digest,
                        "blocking_findings": result["derived"].get("blocking_findings_count", 0),
                        # A recorded negative review is a SUCCESSFUL recording of
                        # a negative outcome, not a failed command.
                        "partial_success": result["status"] != "declared_accepted",
                    },
                    verification=result,
                )
            )
        )
        return

    click.echo(f"receipt: {out_path.relative_to(repo_root)}")
    click.echo(f"status:  {result['status']}")
    click.echo(f"reason:  {result['reason']}")
    if result["status"] != "declared_accepted":
        click.echo("")
        click.echo("This is recorded, not hidden — the verdict gate will read it and block.")
