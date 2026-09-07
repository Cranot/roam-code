"""W220 — make the W186 eight-evidence-questions audit executable.

The W186 crosswalk
(``(internal memo)`` §"The eight evidence
questions") names the eight questions every sellable Roam report should
answer:

* Q1 actor      — WHO made the change?
* Q2 authority  — WHO authorised the change?
* Q3 context    — WHAT context did the actor read?
* Q4 changes    — WHAT changed?
* Q5 risk       — WHAT risk did the change introduce?
* Q6 policy     — WHAT policy decisions applied?
* Q7 verify     — HOW was the change verified?
* Q8 accept     — WHO accepted any residual risk?

W210 already shipped :meth:`ChangeEvidence.evidence_completeness` which
scores a packet against the eight questions. This module turns that
scoring into an executable audit so every wave can:

1. Verify that the scoring function correctly flags gaps on a minimal
   packet (regression guard against "complete" creeping into the empty
   default).
2. Verify that a fully-populated packet can earn ``"complete"`` on all
   eight questions (regression guard against the bar moving out of
   reach).
3. Exercise the real ``pr-replay`` producer and serialized evidence with
   an isolated Git history and declared/missing actor controls. Keep the
   six-answer threshold when identity is supplied and require an honest
   Q1 gap when it is absent; no developer config or CI skip supplies proof.

DESIGN NOTE - delegation, not duplication
-----------------------------------------
The directive offered an inline ``score_packet`` helper as a fallback.
Since W210's :meth:`ChangeEvidence.evidence_completeness` is already
landed and is THE canonical scoring function used by the report-honesty
banner, we delegate to it. Duplicating the scoring logic would create
two sources of truth and a vocabulary-drift risk (cf. the CLAUDE.md
Pattern 3 anti-pattern: "Vocabulary mismatch across commands"). The
parametrised tests below pin the eight question keys so any rename in
W210 surfaces here too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.evidence import (
    ActorRef,
    AuthorityRef,
    ChangeEvidence,
    EnvironmentRef,
    EvidenceArtifact,
    EvidenceSubject,
)

# ---------------------------------------------------------------------------
# The eight questions — the canonical Q-id ↔ rule mapping
# ---------------------------------------------------------------------------
#
# Q-ids match :meth:`ChangeEvidence.evidence_completeness`'s key naming
# exactly (``"Q1"`` .. ``"Q8"``). The second tuple slot is the
# human-readable rule that ``evidence_completeness`` implements; we keep
# the descriptions here as a documentation-tax mirror so a reader of the
# test file does not have to open the source.
EIGHT_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("Q1", "actor: actor_refs (complete) | agent_id or human_actor (partial)"),
    ("Q2", "authority: authority_refs (complete) | mode (partial)"),
    ("Q3", "context: context_refs"),
    ("Q4", "changes: changed_subjects"),
    ("Q5", "risk: risk_level (complete) | SAFE+no findings (not_applicable)"),
    ("Q6", "policy: policy_decisions (complete) | authority_refs (partial)"),
    ("Q7", "verify: tests_run or verification artifacts (complete) | tests_required or report artifacts (partial)"),
    ("Q8", "accept: approvals or accepted_risks (complete) | redactions (partial)"),
)


def _score(packet: ChangeEvidence) -> dict[str, str]:
    """Return the per-question score for ``packet``.

    Thin wrapper over :meth:`ChangeEvidence.evidence_completeness` that
    strips the totals (``complete``/``partial``/``missing``/
    ``not_applicable``) so callers iterating over Q-ids don't have to
    filter. The totals are validated separately in
    :func:`test_totals_match_per_question_counts`.
    """
    full = packet.evidence_completeness()
    return {k: v for k, v in full.items() if k.startswith("Q")}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_packet() -> ChangeEvidence:
    """Smallest valid ChangeEvidence — only ``evidence_id`` populated.

    Used as the negative baseline: a packet this empty MUST miss every
    one of the eight questions, otherwise our scoring is letting empty
    defaults pass.
    """
    return ChangeEvidence(evidence_id="ev_test_minimal")


@pytest.fixture
def populated_packet() -> ChangeEvidence:
    """A ChangeEvidence packet that answers ALL eight questions.

    Constructed inline so the test does not depend on a generated
    fixture file. Every field that contributes to a ``"complete"`` score
    is populated with realistic-but-synthetic values. If
    :meth:`evidence_completeness`'s rules tighten in a future wave (e.g.
    Q7 requiring tests_run AND artifacts), this fixture is the place to
    add the new field.
    """
    return ChangeEvidence(
        evidence_id="ev_test_populated",
        repo_id="repo:example",
        git_range="abc1234..def5678",
        commit_sha="def5678",
        diff_hash="0" * 64,
        run_ids=("run_20260514_test",),
        agent_id="agent:test",
        human_actor="human:test@example.com",
        mode="safe_edit",
        verdict="REVIEW",
        risk_level="medium",  # Q5 complete
        # Q3 complete: context_refs
        context_refs=(
            EvidenceArtifact(
                artifact_id="ctx:test_input",
                kind="report",
                content_inline="captured context blob",
            ),
        ),
        # Q4 complete: changed_subjects
        changed_subjects=(EvidenceSubject(kind="file", qualified_name="src/example.py"),),
        findings=({"detector": "test", "severity": "low"},),
        # Q6 complete: policy_decisions
        policy_decisions=({"rule_id": "test-rule-1", "outcome": "pass"},),
        tests_required=("test_example",),
        # Q7 complete: tests_run
        tests_run=({"name": "test_example", "result": "pass"},),
        # Q8 complete: approvals
        approvals=({"reviewer": "human:reviewer@example.com", "at": "2026-05-14"},),
        accepted_risks=({"id": "ar-1", "rationale": "ack"},),
        artifacts=(
            EvidenceArtifact(
                artifact_id="report:test",
                kind="report",
                content_inline="example report body",
            ),
        ),
        # Q1 complete: actor_refs
        actor_refs=(ActorRef(actor_kind="agent", actor_id="agent:test"),),
        # Q2 complete: authority_refs
        authority_refs=(AuthorityRef(authority_kind="mode", authority_id="mode:safe_edit"),),
        environment_refs=(EnvironmentRef(env_kind="workspace", env_id="workspace:test"),),
    )


# ---------------------------------------------------------------------------
# Parametrised: minimal packet misses every question
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("q_id,_rule", EIGHT_QUESTIONS)
def test_minimal_packet_misses_all_8_questions(minimal_packet: ChangeEvidence, q_id: str, _rule: str) -> None:
    """A minimal packet MUST score ``"missing"`` on every Q.

    This is the negative test: it proves the audit can detect empty
    state. If a future change makes one of the eight questions return
    ``"complete"`` / ``"partial"`` on an empty packet, this test fails
    and points at the regression.

    Q5 (risk) is the one Q with a defined ``"not_applicable"`` branch
    (SAFE verdict + no findings). The minimal packet has neither
    ``risk_level`` nor a SAFE/PASS verdict, so the expected score is
    ``"missing"``, NOT ``"not_applicable"``.
    """
    scores = _score(minimal_packet)
    assert scores[q_id] == "missing", f"{q_id}: minimal packet should miss this question; got {scores[q_id]!r}"


# ---------------------------------------------------------------------------
# Parametrised: populated packet answers every question
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("q_id,_rule", EIGHT_QUESTIONS)
def test_populated_packet_answers_all_8_questions(populated_packet: ChangeEvidence, q_id: str, _rule: str) -> None:
    """A fully populated packet MUST score ``"complete"`` on every Q.

    This is the positive test: it proves the audit's ``"complete"``
    branches are reachable AND that the ``populated_packet`` fixture
    stays in sync with the scoring rules. If a future wave tightens
    Q-N's ``"complete"`` rule (e.g. requires both tests_run AND
    artifacts), this test will fail until the fixture is updated.
    """
    scores = _score(populated_packet)
    assert scores[q_id] == "complete", f"{q_id}: populated packet should answer this question; got {scores[q_id]!r}"


# ---------------------------------------------------------------------------
# Cross-checks on the scoring contract
# ---------------------------------------------------------------------------


def test_score_returns_exactly_eight_questions(
    minimal_packet: ChangeEvidence,
) -> None:
    """The scoring function MUST surface exactly Q1..Q8 — no more, no less.

    Drift guard: if a Q9 sneaks in or a Q gets renamed, both the EIGHT_
    QUESTIONS table above and the report-honesty banner go stale. This
    test pins the key set.
    """
    scores = _score(minimal_packet)
    expected = {f"Q{i}" for i in range(1, 9)}
    assert set(scores.keys()) == expected, f"score keys drifted from Q1..Q8: got {sorted(scores.keys())}"


def test_totals_match_per_question_counts(
    populated_packet: ChangeEvidence,
) -> None:
    """``evidence_completeness`` totals MUST equal the per-Q tallies.

    Defends against a refactor that updates the per-Q rules but forgets
    to re-tally the totals. Uses ``populated_packet`` because it is the
    only fixture guaranteed to produce a non-zero ``complete`` count.
    """
    full = populated_packet.evidence_completeness()
    q_values = [full[f"Q{i}"] for i in range(1, 9)]
    assert full["complete"] == sum(1 for v in q_values if v == "complete")
    assert full["partial"] == sum(1 for v in q_values if v == "partial")
    assert full["missing"] == sum(1 for v in q_values if v == "missing")
    assert full["not_applicable"] == sum(1 for v in q_values if v == "not_applicable")


def test_score_values_are_closed_enumeration(
    minimal_packet: ChangeEvidence,
    populated_packet: ChangeEvidence,
) -> None:
    """Every Q value MUST be one of the four named statuses.

    The four statuses (``complete`` / ``partial`` / ``missing`` /
    ``not_applicable``) are the user-facing vocabulary for the report-
    honesty banner. Any new status would need a doc update and a banner
    template change; this test catches an accidental new string.
    """
    allowed = {"complete", "partial", "missing", "not_applicable"}
    for packet in (minimal_packet, populated_packet):
        scores = _score(packet)
        for q_id, status in scores.items():
            assert status in allowed, f"{q_id}={status!r} is not one of {sorted(allowed)}"


# ---------------------------------------------------------------------------
# Integration: score controlled current-producer pr-replay output
# ---------------------------------------------------------------------------
#
# Trajectory: 3 -> 5 -> 6 -> 7 complete answers, lifted across four waves.
#
# W220 captured the initial baseline as 3/8 complete (Q2 authority,
# Q4 changes, Q5 risk via the ``--tier sample`` path).
#
# W223 wired the five new W199 collector kwargs (rules, audit-trail,
# vuln-reach, test-impact, cga) plus the mcp_receipts_dir into the
# PR Replay producer, lifting Q6 when a policy producer was present.
# The detector-scope policy floor now makes that lift deterministic on a
# fresh checkout: every successful replay records which detector policy it
# selected as ``not_evaluated`` (never a fabricated pass/fail decision).
#
# W246 wired the changed-file surface into the collector's
# ``context_files`` channel: ``_gather_context_files`` derives the file
# list from ``git diff --name-only <range>`` and stamps it onto the
# synthetic pr-bundle envelope. The collector turns those entries into
# ``EvidenceArtifact`` rows on ``context_refs``, flipping Q3 from
# ``missing`` to ``complete`` and lifting the aggregate to 6/8.
#
# W258 mirrors the W246 ``context_refs`` reconstruction block for
# ``artifacts`` so the audit scores Q7 against the same artifacts the
# on-disk packet carries (previously a hardcoded ``artifacts=()`` line
# discarded them). NOTE (W263, 2026-06-17): reconstructing the artifacts
# no longer lifts Q7 to ``complete`` on its own — Q7 was retightened to
# require a verification-shaped artifact (or a real ``tests_run`` row),
# and a clean checkout's pr-replay packet carries only report / manifest /
# raw_envelope artifacts. See the ``EXPECTED_COMPLETE_COUNT_TODAY`` note
# below for the full rationale; the reconstruction is still correct (Q7
# WOULD score ``complete`` here if the producer stamped an attestation /
# cga_predicate in this environment).
#
# W261 lifts Q8 (accept) from ``missing`` to ``partial`` by stamping the
# ``producer_not_available`` redaction reason on every pr-replay packet.
# That keeps the report honest about the producer-side gap (PR Replay has
# no acceptance harvester today) without falsely implying acceptance was
# checked. ``partial`` does NOT count toward ``complete``.
#
# W263 (Q7 definition tightened, 2026-06-17) drops the complete count from
# 7 to 6. ``ChangeEvidence.evidence_completeness`` no longer scores Q7
# ``complete`` for ANY artifact — it now requires a real ``tests_run`` row
# OR a verification-shaped artifact (sarif / attestation / cga_predicate /
# bundle / trace / log_excerpt; see ``_VERIFICATION_ARTIFACT_KINDS``).
# Generic report / manifest / raw_envelope / mcp_receipt artifacts are
# partial context, not proof a change was verified. This repo's own
# pr-replay --tier sample packet carries NO verification-shaped artifacts
# in a clean checkout (its test-impact envelope emits ``tests_required``
# only, never ``tests_run``, and the audit-trail row is a ``manifest``),
# so Q7 honestly scores ``partial`` here. The OLD count of 7 was an
# over-generous score under the pre-W263 "any artifact = verified" rule
# — exactly the false positive the tightening removes. The fixture-level
# contract (``populated_packet``, which sets a real ``tests_run`` row)
# still earns Q7 ``complete``; only the live-producer audit moves.
# Ratchet back UP to 7 only when a future wave either wires a real
# pytest-run harvester into the pr-replay path (Q7 -> complete via
# tests_run) OR makes the producer stamp attestations as a verification
# artifact in a clean checkout.
#
# The assertion is asymmetric (``>=``) so:
#
# * Improvements (e.g. wiring an acceptance producer for Q8) cause this
#   test to PASS until somebody bumps the literal intentionally - we
#   don't want a flake when coverage improves.
# * Regressions (a wave that strips a Q's "complete" field) cause this
#   test to FAIL loudly.
#
# The literal lives next to the integration test (not at module top)
# so it's obvious that it's tied to the integration assertion and not
# a global config knob.
EXPECTED_COMPLETE_COUNT_TODAY: int = 6

# W261/W263 — minimum partial count on the current pr-replay output. Two
# Qs are ``partial`` today:
#   * Q8 (accept): the W261 marker declares the producer gap
#     (``producer_not_available``) on a packet that has no real approvals
#     data, lifting Q8 from ``missing`` to ``partial``.
#   * Q7 (verify): W263 tightened the Q7 definition (see the long note on
#     ``EXPECTED_COMPLETE_COUNT_TODAY`` above). The packet has report /
#     manifest / raw_envelope artifacts but no verification-shaped artifact
#     and no ``tests_run`` row, so Q7 sits at ``partial``.
# If a future wave wires an acceptance harvester (Q8 -> complete) or a real
# test-run harvester (Q7 -> complete), the partial count drops and the
# complete count grows. The assertion is asymmetric (``>=``) for the same
# reason ``EXPECTED_COMPLETE_COUNT_TODAY`` is: an UPWARD move in partial is
# fine; a DOWNWARD move means a wave stripped the W261 marker (or another
# partial signal) and the audit fails loudly.
EXPECTED_PARTIAL_COUNT_TODAY: int = 2


def _packet_from_pr_replay_json(payload: dict) -> ChangeEvidence:
    """Reconstruct a :class:`ChangeEvidence` from the pr-replay JSON.

    Mirrors the field-by-field construction so consumers can call
    :meth:`evidence_completeness` on a packet that didn't come through
    the in-memory constructor. The reconstruction does NOT preserve
    rich nested types (findings stay as plain dicts, etc.) because
    ``evidence_completeness`` only cares about emptiness.
    """
    subj = tuple(
        EvidenceSubject(
            kind=s["kind"],
            qualified_name=s["qualified_name"],
            repo_id=s.get("repo_id"),
            extra=s.get("extra", {}),
        )
        for s in payload.get("changed_subjects", ())
    )
    auth = tuple(
        AuthorityRef(
            authority_kind=a["authority_kind"],
            authority_id=a["authority_id"],
            granted_by=a.get("granted_by"),
            source=a.get("source", "inferred_fallback"),
            extra=a.get("extra", {}),
        )
        for a in payload.get("authority_refs", ())
    )
    env = tuple(
        EnvironmentRef(
            env_kind=e["env_kind"],
            env_id=e["env_id"],
            extra=e.get("extra", {}),
        )
        for e in payload.get("environment_refs", ())
    )
    actor = tuple(
        ActorRef(
            actor_kind=a["actor_kind"],
            actor_id=a["actor_id"],
            display_name=a.get("display_name"),
            trust_tier=a.get("trust_tier", "unknown"),
            extra=a.get("extra", {}),
        )
        for a in payload.get("actor_refs", ())
    )

    # W246 - reconstruct context_refs so Q3 scores against the same
    # artifacts the on-disk packet carries. The pr-replay producer
    # populates them from git's changed-file list; the collector turns
    # them into EvidenceArtifact rows with kind="raw_envelope" and either
    # path+content_hash (when a hash is available) or content_inline (the
    # lifeboat path used when content_hash is None, which is the default
    # PR Replay setting per the W246 perf note).
    #
    # W258 - apply the same reconstruction to ``artifacts`` so Q7 scores
    # against the on-disk audit-trail manifest + cga_predicate rows the
    # W199 gatherers stamp onto the packet. Previously a hardcoded
    # ``artifacts=()`` discarded them and suppressed Q7 to ``partial``;
    # mirroring the context_refs block here closes that synthetic-only
    # gap. Same skip-on-failure discipline: a malformed row should not
    # crash the audit, only drop out of the reconstructed packet.
    def _reconstruct_artifacts(
        rows: tuple,
    ) -> list[EvidenceArtifact]:
        out: list[EvidenceArtifact] = []
        for row in rows:
            try:
                out.append(
                    EvidenceArtifact(
                        artifact_id=row["artifact_id"],
                        kind=row["kind"],
                        path=row.get("path"),
                        content_hash=row.get("content_hash"),
                        content_inline=row.get("content_inline"),
                        redactions=tuple(row.get("redactions", ())),
                        extra=row.get("extra", {}),
                    )
                )
            except (KeyError, ValueError):
                # Skip rows that don't round-trip cleanly - the audit
                # is about coverage of the packet, not strict
                # reconstruction.
                continue
        return out

    context_refs_reconstructed = _reconstruct_artifacts(payload.get("context_refs", ()))
    artifacts_reconstructed = _reconstruct_artifacts(payload.get("artifacts", ()))
    return ChangeEvidence(
        evidence_id=payload["evidence_id"],
        schema_version=payload.get("schema_version", "1.0.0"),
        repo_id=payload.get("repo_id"),
        git_range=payload.get("git_range"),
        commit_sha=payload.get("commit_sha"),
        diff_hash=payload.get("diff_hash"),
        run_ids=tuple(payload.get("run_ids", ())),
        agent_id=payload.get("agent_id"),
        human_actor=payload.get("human_actor"),
        mode=payload.get("mode"),
        started_at=payload.get("started_at"),
        completed_at=payload.get("completed_at"),
        verdict=payload.get("verdict"),
        risk_level=payload.get("risk_level"),
        # W246 + W258: context_refs and artifacts are both reconstructed
        # from payload above, so Q3 and Q7 score against the same rows
        # the on-disk packet carries.
        context_refs=tuple(context_refs_reconstructed),
        changed_subjects=subj,
        findings=tuple(payload.get("findings", ())),
        policy_decisions=tuple(payload.get("policy_decisions", ())),
        tests_required=tuple(payload.get("tests_required", ())),
        tests_run=tuple(payload.get("tests_run", ())),
        approvals=tuple(payload.get("approvals", ())),
        accepted_risks=tuple(payload.get("accepted_risks", ())),
        artifacts=tuple(artifacts_reconstructed),
        actor_refs=actor,
        authority_refs=auth,
        environment_refs=env,
        redactions=tuple(payload.get("redactions", ())),
        # content_hash / signature_ref do not influence the score.
        content_hash=payload.get("content_hash"),
        signature_ref=payload.get("signature_ref"),
    )


@pytest.mark.parametrize("has_actor", [True, False], ids=["declared-actor", "missing-actor"])
def test_current_roam_code_assurance_coverage(tmp_path: Path, monkeypatch, has_actor: bool) -> None:
    """Exercise the serialized producer with explicit, isolated actor inputs.

    A developer's git config, run ledger, attestations and history are not test
    fixtures. The six-answer control declares its actor; the paired no-identity
    control must honestly lose Q1 rather than fabricate an actor. Both run in CI
    and shallow source checkouts because the fixture owns its six commits.
    """
    import os
    import subprocess

    from roam.cli import cli

    for key in (
        "ROAM_AGENT_ID",
        "ROAM_HUMAN_ACTOR",
        "ROAM_MCP_CLIENT_ID",
        "ROAM_CI_RUNNER_ID",
        "GITHUB_ACTIONS_RUN_ID",
        "ROAM_RUN_ID",
        "ROAM_DB_DIR",
        "ROAM_PROJECT_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    if has_actor:
        monkeypatch.setenv("ROAM_HUMAN_ACTOR", "fixture@example.invalid")

    repo = tmp_path / "assurance-repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Assurance fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Assurance fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", f"core.hooksPath={os.devnull}", *args],
            cwd=repo,
            env=git_env,
            capture_output=True,
            check=True,
        )

    git("init", "-q")
    for number in range(6):
        (repo / "sample.py").write_text(f"def answer():\n    return {number}\n", encoding="utf-8")
        git("add", ".gitignore", "sample.py")
        git("commit", "-q", "-m", f"Fixture change {number}")

    target = tmp_path / "current-evidence.json"
    result = CliRunner().invoke(
        cli,
        ["pr-replay", "--tier", "sample", "--evidence", str(target)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert target.exists(), "pr-replay reported success but wrote no file"
    payload = json.loads(target.read_text(encoding="utf-8"))
    packet = _packet_from_pr_replay_json(payload)
    full = packet.evidence_completeness()
    assert full["Q1"] == ("complete" if has_actor else "missing"), full
    if has_actor:
        assert any(actor.actor_id == "fixture@example.invalid" for actor in packet.actor_refs)
    else:
        assert not packet.actor_refs
    # Keep the original six-answer threshold for a declared actor. The missing
    # actor control changes only Q1; neither branch supplies verification or
    # human acceptance that did not occur.
    expected_complete = EXPECTED_COMPLETE_COUNT_TODAY - int(not has_actor)
    assert full["complete"] >= expected_complete, full
    assert full["partial"] >= EXPECTED_PARTIAL_COUNT_TODAY, full
    assert full["Q7"] == "partial", full
    assert full["Q8"] == "partial", full
