"""Sibling Patch Network v1 — ``roam sibling-patch apply <claim>``.

A replay-certified defect-transfer command (design: ``fable-packets/SPN_V1_DESIGN.md``).
A producer who fixed a defect emits one proof-carrying ``RepairTransferClaim``;
a consumer runs it against *their own* repo and gets, PROPOSE-ONLY:

  (a) a lexical candidate pool over their own code (roam's index);
  (b) a rerank by mined repair-intent — the measured winner
      (:mod:`roam.sibling_patch.repair_scorer`, the fork-B / T-prime scorer;
      NOT the graph stack, which transfers poorly cross-org);
  (c) an optional replay of the candidate patch in a throwaway worktree against
      the consumer's OWN ``--validation-command`` (fire pre-patch / clear
      post-patch / localize); and
  (d) proposals only — no push, no write, no commit. Trust never travels — the
      experiment does.

Gate: set ``ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1`` to expose the command. It is
default-off and intentionally not part of the static command surface. Scoped
(falsifier verdict) to DEFECT-shaped repairs (deletion/replacement); pure
additions are a structural no-op and are reported as out-of-scope.
"""
from __future__ import annotations

import json
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.cmd_repair_siblings import (
    SymbolBody,
    _load_candidate_symbols,
)
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.knowledge.knowledge_claim import (
    KnowledgeClaim,
    PatchFusionError,
    RepairTransferError,
)
from roam.output.formatter import format_table, json_envelope, to_json
from roam.sibling_patch import repair_scorer
from roam.sibling_patch.replay_gate import run_replay_gate

_FLAG_ENV = "ROAM_EXPERIMENTAL_REPAIR_SIBLINGS"
_FRAMING = (
    "experimental; propose-only; validated internal cross-org WIN on defect-shaped "
    "repairs (recall +0.089 CI[0.047,0.134]); reranker is deterministic; scoped to "
    "deletion/replacement; NOT a defect detector and NOT an auto-fixer"
)


def _preimage_from_patch(patch_text: str) -> str:
    """Reconstruct the pre-fix code body (context + deleted lines) from a diff.

    This is the org-independent lexical anchor: the buggy code the producer
    fixed, used to find lexically-similar siblings in the consumer's own repo.
    """
    lines: list[str] = []
    for raw in patch_text.splitlines():
        if raw.startswith(("+++", "---", "@@", "diff ", "index ", "\\")):
            continue
        if raw.startswith("+"):
            continue
        if raw.startswith("-"):
            lines.append(raw[1:])
        elif raw.startswith(" "):
            lines.append(raw[1:])
        else:
            lines.append(raw)
    return "\n".join(lines)


def _synthetic_anchor(anchor_meta: dict) -> SymbolBody:
    kind = str(anchor_meta.get("kind") or "function")
    return SymbolBody(
        id=-1,
        file_path=str(anchor_meta.get("file") or "<producer-anchor>"),
        name=str(anchor_meta.get("symbol") or "anchor"),
        qualified_name=str(anchor_meta.get("symbol") or "anchor"),
        kind=kind,
        line_start=None,
        line_end=None,
        body="",
    )


def _load_claim(claim_path: str) -> KnowledgeClaim:
    path = Path(claim_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise click.ClickException(f"claim file not found: {claim_path}") from exc
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"claim file is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise click.ClickException("claim file must contain a single JSON object")
    try:
        # from_dict enforces the WRITE-TIME PATCH-FUSION INVARIANT: a
        # sibling-detector claim is inadmissible without candidate_patch + a
        # green producer-side fusion_attestation.
        return KnowledgeClaim.from_dict(raw)
    except PatchFusionError as exc:
        raise click.ClickException(f"patch-fusion invariant rejected this claim: {exc}") from exc
    except (RepairTransferError, ValueError) as exc:
        raise click.ClickException(f"invalid claim: {exc}") from exc


def _replay_rows(results: list[dict]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in results:
        rows.append(
            [
                str(item["rank"]),
                f"{item['repair_applicability']:.2f}",
                f"{item['lexical_score']:.2f}",
                f"{item['file']}:{item.get('line_start') or '?'}",
                str(item["symbol"]),
                str(item.get("replay_status", "-")),
            ]
        )
    return rows


@roam_capability(
    name="sibling-patch",
    category="refactoring",
    summary="Experimental replay-certified defect-transfer proposer (propose-only)",
    inputs=("repair_transfer_claim", "validation_command"),
    outputs=("ranked_siblings", "fusion_attestations"),
    examples=(
        "ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1 roam sibling-patch apply claim.json",
        "ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1 roam sibling-patch apply claim.json "
        "--validation-command 'pytest -q tests/test_x.py'",
    ),
    tags=("experimental", "repair-intent", "replay-gate", "propose-only"),
    maturity="experimental",
    mcp_expose=False,
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.group("sibling-patch")
def sibling_patch_cmd() -> None:
    """Experimental replay-certified defect transfer (propose-only).

    Enable with ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1. Consumes a proof-carrying
    RepairTransferClaim and proposes replay-certified sibling fixes in your own
    repo. It never writes, commits, or pushes.
    """


@sibling_patch_cmd.command("apply")
@click.argument("claim_path", type=str)
@click.option(
    "--validation-command",
    "validation_command",
    default=None,
    help="YOUR OWN command that fails (non-zero) when the defect is present. "
    "Required to certify; without it the command proposes without replay.",
)
@click.option("--top-n", default=10, show_default=True, type=click.IntRange(1, 100), help="Ranked siblings to show.")
@click.option(
    "--candidate-limit",
    default=100,
    show_default=True,
    type=click.IntRange(1, 1000),
    help="Top lexical candidates to freeze before repair-intent reranking.",
)
@click.option(
    "--min-lexical",
    default=0.05,
    show_default=True,
    type=click.FloatRange(0.0, 1.0),
    help="Minimum lexical cosine for the candidate pool.",
)
@click.option(
    "--max-replays",
    default=3,
    show_default=True,
    type=click.IntRange(0, 25),
    help="How many top siblings to replay-certify (0 disables replay).",
)
@click.option(
    "--replay-timeout",
    default=600,
    show_default=True,
    type=click.IntRange(1, 7200),
    help="Per-run timeout (seconds) for the validation command.",
)
@click.pass_context
def apply_cmd(
    ctx,
    claim_path,
    validation_command,
    top_n,
    candidate_limit,
    min_lexical,
    max_replays,
    replay_timeout,
):
    """Propose replay-certified sibling fixes for a RepairTransferClaim.

    CLAIM_PATH is a JSON RepairTransferClaim. It must carry a candidate_patch
    and a green producer-side fusion_attestation (patch-fusion invariant), or it
    is rejected before anything runs.
    """
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    claim = _load_claim(claim_path)
    rt = claim.repair_transfer
    if rt is None:
        raise click.ClickException(
            "claim has no repair_transfer payload; this command consumes RepairTransferClaims only"
        )

    candidate_patch = str(rt.get("candidate_patch") or "")
    anchor_meta = rt.get("anchor") if isinstance(rt.get("anchor"), dict) else {}

    changes = repair_scorer.parse_patch_changes(candidate_patch)
    intent = repair_scorer.derive_repair_intent(changes)
    in_scope = repair_scorer.is_defect_intent(intent)

    verdict_scope = (
        "in-scope (defect-shaped)"
        if in_scope
        else f"OUT-OF-SCOPE (kind={intent.kind}); SPN v1 admits only {sorted(repair_scorer.DEFECT_KINDS)}"
    )

    ranked: list[repair_scorer.RankedSibling] = []
    lexical_pool_size = 0
    if in_scope:
        ensure_index()
        root = find_project_root()
        anchor = _synthetic_anchor(anchor_meta)
        with open_db(readonly=True) as conn:
            raw_candidates = _load_candidate_symbols(conn, root, anchor)
        scorer_candidates = [
            repair_scorer.ScorerCandidate.from_body(
                {
                    "id": cand.id,
                    "file": cand.file_path,
                    "symbol": cand.label,
                    "kind": cand.kind,
                    "line_start": cand.line_start,
                    "line_end": cand.line_end,
                },
                cand.body,
            )
            for cand in raw_candidates
        ]
        lexical_pool_size = len(scorer_candidates)
        anchor_body = _preimage_from_patch(candidate_patch)
        ranked = repair_scorer.rerank(
            anchor_body,
            scorer_candidates,
            intent,
            pool_n=candidate_limit,
            min_lexical=min_lexical,
            repair_floor=0.0,
        )

    shown = ranked[:top_n]
    result_dicts = [item.to_dict(rank) for rank, item in enumerate(shown, start=1)]

    # --- (c) replay-gate: certify with the CONSUMER's own validation command ---
    certified_green = 0
    replay_ran = bool(validation_command) and max_replays > 0 and in_scope
    if replay_ran:
        root = find_project_root()
        for entry in result_dicts[:max_replays]:
            attestation = run_replay_gate(
                root,
                candidate_patch,
                validation_command,
                retarget_file=entry["file"],
                timeout=replay_timeout,
            )
            entry["replay"] = attestation.to_dict()
            entry["replay_status"] = attestation.status
            if attestation.is_green():
                certified_green += 1
    else:
        for entry in result_dicts:
            entry["replay_status"] = "not_run"

    verdict = (
        f"sibling-patch proposed {len(result_dicts)} candidate(s); "
        f"{certified_green} replay-certified green; "
        f"{'replay ran' if replay_ran else 'replay not run (propose-only)'}; "
        f"scope: {verdict_scope}"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "sibling-patch",
                    summary={
                        "verdict": verdict,
                        "experimental": True,
                        "propose_only": True,
                        "default_off_flag": f"{_FLAG_ENV}=1",
                        "in_scope": in_scope,
                        "scope_note": verdict_scope,
                        "candidate_count": len(result_dicts),
                        "lexical_pool_size": lexical_pool_size,
                        "replay_ran": replay_ran,
                        "certified_green": certified_green,
                        "framing": _FRAMING,
                    },
                    claim={
                        "claim_id": claim.claim_id,
                        "scope": claim.scope,
                        "evidence_type": claim.evidence_type,
                        "sibling_detector": rt.get("sibling_detector"),
                        "candidate_gen": rt.get("candidate_gen"),
                        "replay_predicate": rt.get("replay_predicate"),
                    },
                    repair_intent=intent.to_dict(),
                    anchor=anchor_meta,
                    candidates=result_dicts,
                    agent_contract={
                        "facts": [
                            f"{len(result_dicts)} proposed sibling candidates",
                            f"{certified_green} replay-certified green",
                            "propose-only: no writes, no commits, no pushes",
                        ],
                        "next_commands": ["roam sibling-patch apply --help"],
                    },
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"Frame: {_FRAMING}")
    click.echo(f"Flag: {_FLAG_ENV}=1")
    click.echo(f"Claim: {claim.claim_id} scope={claim.scope} detector={rt.get('sibling_detector')}")
    click.echo(
        "Intent: "
        f"kind={intent.kind}; "
        f"deleted={list(intent.deleted_patterns)[:1] or '-'}; "
        f"added={list(intent.added_patterns)[:1] or '-'}"
    )
    if not in_scope:
        click.echo(f"SCOPE: {verdict_scope} — no proposals (structural no-op).")
        return
    click.echo()
    if result_dicts:
        click.echo(
            format_table(
                ["rank", "applic", "lexical", "location", "symbol", "replay"],
                _replay_rows(result_dicts),
            )
        )
        if not replay_ran:
            click.echo()
            click.echo(
                "Replay not run. Pass --validation-command '<your test>' to certify "
                "(fire pre-patch / clear post-patch) in a throwaway worktree."
            )
    else:
        click.echo("(no repair-applicable siblings in the lexical pool)")
