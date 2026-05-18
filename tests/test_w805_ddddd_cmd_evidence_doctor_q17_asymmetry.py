"""W805-DDDDD — cmd_evidence_doctor Q1-Q7 vs Q8 producer-coverage-asymmetry
consumer-side flattening.

Hundred-and-eighth-in-batch W805 sweep. THIRD member of the evidence-
compiler producer-coverage family (now 3-STRONG), confirming the family
shape established by W805-TTTT (cmd_pr_bundle envelope) and W805-YYYY
(cmd_pr_replay packet). This pin extends the family from PRODUCER-side
into CONSUMER-side: cmd_evidence_doctor reads a packet emitted by the
W805-YYYY producer and is expected to surface the Q1-Q7-vs-Q8 marker-
emission asymmetry to operators. Today it silently flattens it.

Family axis summary (axis 3-STRONG once this pins):

    W805-TTTT  cmd_pr_bundle      producer envelope-summary layer
                                  (no Q1-Q8 coverage matrix at all).
    W805-YYYY  cmd_pr_replay      producer packet redactions layer
                                  (W261 producer_not_available marker
                                  emitted for Q8 ONLY; Q1-Q7 silently
                                  emit empty state on no-producer).
    W805-DDDDD cmd_evidence_doctor consumer-side reporting layer
                                  (reads the packet, flattens the
                                  Q8-only-marker-coverage state).

Same root failure shape (Pattern-1 variant D + Pattern-2 silent
fallback on the producer-coverage axis) projected onto a third distinct
file. The W978 distinctness check below confirms cmd_evidence_doctor
is the consumer side, NOT the producer side.

Bug class
---------
The doctor classifies each Q via :func:`classify_completeness` (a thin
read-only wrapper around :meth:`ChangeEvidence.evidence_completeness`)
and emits a per-Q table on the JSON envelope. The asymmetry between
the W261 marker emitter at change_evidence.py:946-951 (Q8) and the
silent-missing emitters at change_evidence.py:920-934 (Q6 / Q7) is
LOST at the consumer boundary in three concrete shapes:

  1. ``summary.verdict`` is the same single-line string
     (``"WARN: INSUFFICIENT evidence; do not publish as governance
     evidence"``) whether the packet carries
     ``redactions=["producer_not_available"]`` (Q8 producer attempted,
     came up empty) OR carries ``redactions=[]`` (Q8 producer never
     attempted). Both packets land at the same verdict line at
     :func:`_build_verdict` (cmd_evidence_doctor.py:520-523).

  2. ``summary.producer_not_available_marker`` is a TOP-LEVEL BOOLEAN
     (cmd_evidence_doctor.py:759, 791) with no Q-axis attribution.
     An operator reading the envelope cannot tell from the doctor
     output WHICH Q axis the marker covers — only Q8 today, but a
     symmetric producer-coverage scheme (per W805-YYYY) would emit it
     across multiple axes. The doctor flattens the per-axis structure
     into a single boolean.

  3. ``next_steps`` Q7 (missing-no-producer) and Q8 (missing-no-
     producer-marker-already-emitted) emit prose hints from the same
     hint table (cmd_evidence_doctor.py:127-136). The hints differ in
     wording but carry no STRUCTURED ``producer_state`` /
     ``why_missing`` field that an operator can route on. So a
     consumer parsing the JSON envelope cannot distinguish "this
     producer was checked + came up empty" from "this producer was
     never wired".

Distinct from sibling family members.
-------------------------------------
* **W805-TTTT** lives at the cmd_pr_bundle envelope-summary layer
  (no Q1-Q8 coverage matrix at all). Different file (cmd_pr_bundle.py
  vs cmd_evidence_doctor.py), different layer (envelope vs consumer
  reporting). The doctor DOES classify Q1-Q8 — the bug is in HOW it
  surfaces the upstream marker-coverage asymmetry, not in whether the
  classification happens.
* **W805-YYYY** lives at the cmd_pr_replay producer packet layer
  (W261 ``producer_not_available`` marker emitted for Q8 ONLY).
  Different file (cmd_pr_replay.py vs cmd_evidence_doctor.py),
  different role (producer that mints the marker vs consumer that
  reads it). The W805-DDDDD bug only exists BECAUSE the W805-YYYY
  bug exists — the doctor faithfully reflects the producer's
  flattened marker emission instead of surfacing the asymmetry to
  the operator.

Together the three pins form the evidence-compiler producer-coverage
FAMILY as a 3-STRONG axis: a structurally-complete bug class spanning
producer-envelope (W805-TTTT), producer-packet (W805-YYYY), and
consumer-report (W805-DDDDD) — all three exhibit Pattern-1 variant D
+ Pattern-2 silent fallback on the same underlying state distinction
(producer attempted-and-empty vs producer-not-wired).

W978 first-hypothesis discipline
---------------------------------
First hypothesis: "the doctor's verdict line should distinguish
producer-attempted from producer-not-wired."

Probed BEFORE pinning:

  * Synthesized two packets — Case A with
    ``redactions=["producer_not_available"]`` (Q8 producer attempted)
    and Case B with ``redactions=[]`` (no producer at all). Confirmed
    ``classify_completeness`` correctly distinguishes them at the
    per-Q level: Case A lands Q8 -> partial; Case B lands Q8 ->
    missing. The asymmetry IS structurally captured by the upstream
    classifier.
  * Ran ``roam --json evidence-doctor`` on both packets. Confirmed
    ``summary.verdict`` is IDENTICAL ("WARN: INSUFFICIENT evidence")
    on both packets; the consumer-facing single-line verdict
    flattens the per-Q difference.
  * Confirmed ``summary.producer_not_available_marker`` is a top-
    level boolean (True for Case A, False for Case B) — useful but
    NOT Q-axis-attributed. A consumer reading the envelope can tell
    "some Q has the marker" but not WHICH Q.
  * Confirmed the ``next_steps`` array carries free-form prose hints
    (``"attach approvals[] or accepted_risks[] via roam pr-bundle
    add-approval (real producer needed)"``) but NO structured
    ``producer_state`` field that an automated consumer can route on.
  * Confirmed cmd_evidence_doctor is the CONSUMER side of the family
    — it reads from
    :func:`classify_completeness` (no DB query, no graph walk, no
    new analysis); the bug is in HOW it surfaces upstream state, not
    in HOW it derives state. This is distinct from W805-TTTT (a
    producer that mints state) and W805-YYYY (a producer that mints
    markers).

W907 verify-cycle
-----------------
Searched cmd_evidence_doctor.py for the W880 false-cycle hedging
pattern ("duplicated here to avoid X" / "kept local to avoid
circular import"). No matches. The lazy imports at lines 177
(``_vocabulary``), 283 (``hashlib``), 289 (``change_evidence``
omission rules), 410 (``AUTHORITY_KINDS``), 607 (``PACKET_SIZE_
BUDGET_BYTES``), 747 (``classify_packet_budget`` /
``packet_size_bytes``) are all genuine deferred imports for hot-path
or substrate-vs-consumer reasons, each with explicit comments
documenting the deferral motive. Clean — no false cycle hedges.

Security severity
-----------------
MEDIUM. cmd_evidence_doctor is the diagnostic surface a buyer /
auditor / human reviewer runs against a delivered evidence packet
BEFORE running it through a heavyweight CI gate. The whole point of
the verdict line is to be the canonical one-line answer to "is this
evidence packet trustworthy?". When the verdict line silently
collapses producer-attempted-and-empty (Case A) into the same
"WARN: INSUFFICIENT evidence" string as producer-never-wired
(Case B), an auditor reading the verdict cannot tell:

  * which Q axis has the W261 marker covering it (only Q8 today),
  * whether the other axes have been honestly disclosed as
    producer-not-wired OR silently emitted empty state with no
    disclosure,
  * which subset of Q1-Q8 the agentic-assurance-frame consider
    "compiled trustworthy evidence" for.

The downstream attestation step (CGA / SLSA VSA) signs over the
content hash; the producer-coverage gap is observable in the
``redactions`` field but the doctor — the surface a non-expert
auditor consults — surfaces only a boolean and a free-form prose
hint. Not HIGH — a sophisticated auditor CAN drill into the per-Q
table + redactions array + honesty section to reconstruct the
asymmetry; the gap is at the operator-facing reporting layer, not
the underlying classification.

Pinning style: xfail(strict=True)
---------------------------------
xfail-strict so the moment cmd_evidence_doctor grows ANY of:

  * a ``summary.producer_attribution`` dict mapping Q-axis to the
    marker that covers it (e.g.
    ``{"Q8": "producer_not_available"}``),
  * a per-Q ``why_missing`` / ``producer_state`` field on
    ``next_steps`` entries (closed-enum:
    ``not_wired`` / ``attempted_empty`` / ``unknown``),
  * a verdict line that distinguishes "INSUFFICIENT - all producers
    silent" from "INSUFFICIENT - Q8 disclosed empty + Q1-Q7 silent"
    (per-axis disclosure of which markers were respected),

the xfail flips to XPASS and forces removal of the pin (and
re-examination of W805-YYYY at the producer layer + W805-TTTT at
the envelope layer — the shared substrate fix may land at the
classifier level and obviate all three).

Sister-suite parity
-------------------
``TestEvidenceCompilerFamilyParity`` re-runs the W805-TTTT and
W805-YYYY axis-distinct invariants inline so a regression on either
sibling pin is observable from this file. This keeps the producer-
coverage FAMILY discoverable from any member.
"""

from __future__ import annotations

import hashlib
import json as _json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_packet(payload: dict) -> str:
    """Recompute the content_hash for a packet payload exactly the way
    the dataclass + doctor recomputation do (so synthetic packets pass
    the doctor's hash check and we observe the per-Q surface
    asymmetry, not a hash FAIL)."""
    from roam.evidence.change_evidence import (
        _W182_OMIT_WHEN_EMPTY_FIELDS,
        _W210_OMIT_WHEN_DEFAULT_FIELDS,
    )

    stripped = dict(payload)
    stripped["content_hash"] = None
    for k in _W182_OMIT_WHEN_EMPTY_FIELDS:
        if stripped.get(k) == []:
            stripped.pop(k, None)
    for k, default in _W210_OMIT_WHEN_DEFAULT_FIELDS.items():
        if k in stripped and stripped[k] == default:
            stripped.pop(k, None)
    canonical = _json.dumps(stripped, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bare_packet(*, redactions: list[str] | None = None) -> dict:
    """Synthesize a packet where Q1-Q7 producers were never attempted.

    The packet shape mirrors what cmd_pr_replay would emit on a bare
    repo with no harvesters wired (W805-YYYY's canonical fixture).
    Caller controls the ``redactions`` array to toggle Case A
    (``["producer_not_available"]``, Q8 producer attempted-but-empty)
    vs Case B (``[]``, no producer attempted at all).
    """
    if redactions is None:
        redactions = []
    p: dict = {
        "evidence_id": "ev_w805_ddddd",
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "git_range": "abc..def",
        "commit_sha": "d" * 40,
        "diff_hash": "h" * 64,
        "run_ids": ["run_1"],
        "agent_id": None,
        "human_actor": None,
        "mode": None,
        "started_at": None,
        "completed_at": None,
        "verdict": "REVIEW",
        "risk_level": None,
        "context_refs": [],
        "changed_subjects": [],
        "findings": [],
        "policy_decisions": [],
        "tests_required": [],
        "tests_run": [],
        "approvals": [],
        "accepted_risks": [],
        "artifacts": [],
        "redactions": list(redactions),
        "actor_refs": [],
        "authority_refs": [],
        "environment_refs": [],
        "signature_ref": None,
        "content_hash": None,
    }
    p["content_hash"] = _hash_packet(p)
    return p


def _run_doctor(tmp_path: Path, payload: dict) -> dict:
    """Write the packet to tmp_path and invoke evidence-doctor --json."""
    from roam.cli import cli

    path = tmp_path / f"{payload['evidence_id']}.json"
    path.write_text(_json.dumps(payload), encoding="utf-8")
    runner = CliRunner()
    res = runner.invoke(
        cli,
        ["--json", "evidence-doctor", str(path)],
        catch_exceptions=False,
    )
    assert res.exit_code in (0, 2), res.output
    return _json.loads(res.output)


# ---------------------------------------------------------------------------
# W978 prerequisite: confirm the asymmetry axis is distinct from siblings.
# ---------------------------------------------------------------------------


class TestW805DDDDDAxisDistinct:
    def test_consumer_role_not_producer_role(self, tmp_path):
        """cmd_evidence_doctor is the CONSUMER side: it never mints state,
        it only READS state from the packet via classify_completeness.
        Confirm the command queries no DB, no graph, no producer
        harvesters — distinct from W805-TTTT (producer) and W805-YYYY
        (producer).
        """
        from roam.commands import cmd_evidence_doctor as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        # Doctor must never call out to a producer / harvester / graph.
        # If any of these strings appear, the consumer-side framing is
        # broken AND the W805-DDDDD axis would need re-classification.
        forbidden = (
            "open_db(",
            "ensure_index(",
            "_gather_test_impact_envelopes",
            "_gather_context_files",
            "_collect_change_evidence",
            "collect_change_evidence(",
        )
        leaks = [s for s in forbidden if s in src]
        assert not leaks, (
            f"axis bleed: cmd_evidence_doctor must stay consumer-side, "
            f"found producer-side calls {leaks!r}. If the doctor grew "
            "a producer call, W805-DDDDD's CONSUMER-side framing is "
            "wrong and the family axis classification needs revisit."
        )

    def test_doctor_never_emits_packets(self, tmp_path):
        """cmd_evidence_doctor is read-only. Confirm it does NOT carry
        any of the producer-side fingerprints from W805-TTTT
        (envelope mint) / W805-YYYY (packet mint). Different role,
        different layer, different file.
        """
        from roam.commands import cmd_evidence_doctor as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        # Doctor must NOT mint a ChangeEvidence packet or pr-bundle
        # envelope. Read-only / diagnostic-only is the framing
        # documented at cmd_evidence_doctor.py:25-27 ("This is a
        # DIAGNOSTIC command. It reports findings; it never mutates
        # the packet, never writes to disk, and never calls a
        # producer.").
        forbidden_producers = (
            "ChangeEvidence(",
            "_build_envelope",
            "_build_pr_bundle",
            "to_canonical_json()",
        )
        leaks = [s for s in forbidden_producers if s in src]
        assert not leaks, (
            f"axis bleed: cmd_evidence_doctor must stay diagnostic, "
            f"found producer-mint calls {leaks!r}. The W805-DDDDD pin "
            "targets consumer-side flattening; producer-side mints would "
            "make this a W805-TTTT/YYYY duplicate."
        )


# ---------------------------------------------------------------------------
# The W805-DDDDD pins — Pattern-1 variant D + Pattern-2 silent fallback
# on the producer-coverage asymmetry surface at the cmd_evidence_doctor
# consumer boundary.
# ---------------------------------------------------------------------------


class TestEvidenceDoctorQ17VsQ8AsymmetrySurface:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-DDDDD: cmd_evidence_doctor.summary.verdict at "
            "cmd_evidence_doctor.py:520-523 emits the SAME single-line "
            "string ('WARN: INSUFFICIENT evidence; do not publish as "
            "governance evidence') whether the packet carries "
            "redactions=['producer_not_available'] (Q8 producer "
            "attempted-and-empty, the W261 honest-disclosure case) OR "
            "carries redactions=[] (no producer ever wired). The per-Q "
            "table DOES distinguish them (Q8 partial vs missing) but the "
            "consumer-facing verdict line — the canonical one-line "
            "answer an auditor consults — silently flattens the "
            "asymmetry. Pattern-1 variant D + Pattern-2 silent fallback "
            "on the consumer-side reporting axis."
        ),
    )
    def test_verdict_line_distinguishes_attempted_vs_not_wired(self, tmp_path):
        """Verdict line must encode the Q8 producer-state distinction.

        Case A: ``redactions=['producer_not_available']`` — the W261
        marker that the sibling cmd_pr_replay emits when its Q8
        approvals harvester checks and finds nothing. Q8 lands at
        ``partial``.
        Case B: ``redactions=[]`` — no producer attempted. Q8 lands
        at ``missing``.

        Today both packets emit the IDENTICAL verdict line:
        ``WARN: INSUFFICIENT evidence; do not publish as governance
        evidence``. A consumer reading only the verdict cannot tell
        which case they're in.

        Expected on fix: ONE of:
          * verdict strings differ for the two cases (e.g. Case A
            mentions ``"producer attempted"`` / ``"with disclosure"``;
            Case B mentions ``"no producer"`` / ``"undisclosed gap"``),
          * a per-axis disclosure phrase appears in the verdict line,
          * a structured ``summary.producer_coverage_state`` field
            with a closed enum (``all_attempted`` / ``mixed`` /
            ``none_attempted``) appears.
        """
        env_a = _run_doctor(tmp_path, _bare_packet(redactions=["producer_not_available"]))
        env_b = _run_doctor(tmp_path, _bare_packet(redactions=[]))

        v_a = env_a["summary"]["verdict"]
        v_b = env_b["summary"]["verdict"]

        # Sanity: per-Q classification DOES differ (so the asymmetry
        # is present at the data layer; the consumer-surface flattens
        # it). If this sanity fails the bug shape itself is different.
        per_q_a = env_a["evidence_completeness"]["per_question"]
        per_q_b = env_b["evidence_completeness"]["per_question"]
        assert per_q_a["Q8"] == "partial", (
            f"fixture sanity: Case A Q8 should be partial (W261 marker lifts to partial), got {per_q_a['Q8']!r}"
        )
        assert per_q_b["Q8"] == "missing", (
            f"fixture sanity: Case B Q8 should be missing (no marker), got {per_q_b['Q8']!r}"
        )

        # The pin: verdict lines must encode the difference.
        assert v_a != v_b, (
            "Pattern-1 variant D + Pattern-2: cmd_evidence_doctor "
            "verdict line is bit-identical between Q8-producer-"
            "attempted (W261 marker emitted) and Q8-producer-not-"
            "wired (no marker). Both cases yield "
            f"verdict={v_a!r}. The per-Q table at "
            f"per_question correctly distinguishes them (Case A Q8={per_q_a['Q8']!r}, "
            f"Case B Q8={per_q_b['Q8']!r}) but the consumer-facing "
            "verdict — the canonical single-line answer — flattens "
            "the asymmetry. An auditor reading only the verdict line "
            "cannot tell honest-disclosure from silent-omission."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-DDDDD-B: summary.producer_not_available_marker at "
            "cmd_evidence_doctor.py:759, 791 is a top-level BOOLEAN "
            "with no Q-axis attribution. A consumer reading the "
            "envelope sees the marker is present (True) but cannot "
            "tell WHICH Q axis it covers — only Q8 today per "
            "cmd_pr_replay.py:1853-1889, but a symmetric W805-YYYY "
            "fix would emit it across multiple axes. The doctor "
            "flattens per-axis structure into a single boolean. "
            "Pattern-1 variant D on consumer-side aggregation."
        ),
    )
    def test_producer_not_available_marker_carries_axis_attribution(self, tmp_path):
        """The marker must be Q-axis-attributed when reported by doctor.

        Today: ``summary.producer_not_available_marker: bool``
        — a single boolean.

        Expected on fix: ONE of:
          * ``summary.producer_coverage`` dict mapping Q -> marker
            kind (e.g. ``{"Q8": "producer_not_available"}``),
          * ``summary.producer_attributed_axes`` list of Q-ids,
          * ``honesty.producer_coverage`` block with per-axis state.

        The doctor today disclosees only that SOME marker is present;
        a consumer cannot route on WHICH axis is honestly disclosed.
        """
        env = _run_doctor(tmp_path, _bare_packet(redactions=["producer_not_available"]))

        summary = env.get("summary") or {}
        honesty = env.get("honesty") or {}

        # The marker IS present at the boolean level — sanity check.
        assert summary.get("producer_not_available_marker") is True, (
            f"fixture sanity: marker should be True, got {summary!r}"
        )

        # The pin: there must be a Q-axis-attributed shape on the
        # envelope so consumers can tell which Q the marker covers.
        candidates = {
            "producer_coverage",
            "producer_attributed_axes",
            "marker_axes",
            "producer_axes",
            "covered_axes",
        }
        has_attribution_in_summary = any(k in summary for k in candidates)
        has_attribution_in_honesty = any(k in honesty for k in candidates)
        has_attribution_in_env = any(k in env for k in candidates)

        # Also accept a per-Q named field
        per_q_attribution = any(
            k in summary or k in honesty or k in env
            for k in (
                "q8_producer_state",
                "q7_producer_state",
                "q6_producer_state",
            )
        )

        assert (
            has_attribution_in_summary or has_attribution_in_honesty or has_attribution_in_env or per_q_attribution
        ), (
            "Pattern-1 variant D: cmd_evidence_doctor surfaces the "
            "producer_not_available marker as a single boolean with "
            "no Q-axis attribution. Consumers can tell SOME marker is "
            "present but not WHICH Q it covers. "
            f"summary keys={sorted(summary.keys())!r}; "
            f"honesty keys={sorted(honesty.keys())!r}; "
            f"envelope keys={sorted(env.keys())!r}. Symmetric design "
            "would attribute markers per axis."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-DDDDD-C: next_steps entries at "
            "cmd_evidence_doctor.py:422-441 carry only a free-form "
            "prose 'action' hint, no structured producer_state field. "
            "Q7 (verify: missing because no test-impact producer ran) "
            "and Q8 (accept: missing because no approvals producer "
            "ran but the W261 marker is emitted) share the same "
            "underlying root cause (producer-not-wired) but their "
            "next_steps entries differ only in prose wording. A "
            "consumer cannot programmatically route on the "
            "producer-state axis. Pattern-1 variant D on the "
            "next-steps schema."
        ),
    )
    def test_next_steps_carry_structured_producer_state(self, tmp_path):
        """Each next_step entry must disclose the producer-state cause.

        Today: ``next_steps[*] = {'q', 'state', 'action'}`` where
        ``state`` is the Q completeness state (missing / partial)
        and ``action`` is a free-form prose hint. There is no
        ``producer_state`` / ``why_missing`` / ``cause`` field.

        Expected on fix: each entry gains a closed-enum
        ``producer_state`` field (one of ``not_wired`` /
        ``attempted_empty`` / ``unknown``) so a consumer can route
        on the producer-state axis without parsing prose.
        """
        env = _run_doctor(tmp_path, _bare_packet(redactions=["producer_not_available"]))
        steps = env.get("next_steps") or []
        assert steps, "fixture sanity: next_steps should be non-empty on partial packet"

        # The pin: at least ONE entry must carry structured
        # producer-state attribution.
        structured_keys = {
            "producer_state",
            "why_missing",
            "cause",
            "missing_reason",
            "disclosure_state",
        }
        has_structured = any(any(k in step for k in structured_keys) for step in steps)

        assert has_structured, (
            "Pattern-1 variant D: cmd_evidence_doctor next_steps "
            "entries carry only free-form prose hints, no structured "
            "producer-state field. A consumer cannot programmatically "
            "distinguish 'producer not wired' from 'producer attempted "
            "but empty' without parsing the action prose. "
            f"Sample next_step keys: {sorted(steps[0].keys())!r}. "
            "Schema should carry an explicit producer_state field with "
            "a closed enum (not_wired / attempted_empty / unknown)."
        )


# ---------------------------------------------------------------------------
# Sister-suite parity invariants — these MUST pass today (existing pins
# from W805-TTTT + W805-YYYY must remain green after this pin lands).
# ---------------------------------------------------------------------------


class TestEvidenceCompilerFamilyParity:
    """Re-run subsets of W805-TTTT and W805-YYYY invariants so the
    producer-coverage family is discoverable from this file. The full
    pin suites stay in their own files — these parity checks only
    re-assert the "axis still distinct" invariants so a regression in
    either sibling fails this suite too.
    """

    def test_w805_tttt_invariants_preserved(self, tmp_path):
        """W805-TTTT pins the envelope-summary-layer Q1-Q8 absence on
        cmd_pr_bundle. Re-confirm a thin probe that cmd_pr_bundle's
        envelope still lacks any q-coverage signal.
        """
        import os
        import subprocess

        from click.testing import CliRunner

        from roam.cli import cli

        proj = tmp_path / "bundle_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
        (proj / "main.py").write_text(
            "def hello():\n    return 'hi'\n",
            encoding="utf-8",
        )
        # Inline git init to avoid conftest import drift.
        for args in (
            ["git", "init"],
            ["git", "config", "user.email", "t@t"],
            ["git", "config", "user.name", "t"],
            ["git", "add", "-A"],
            ["git", "commit", "-m", "init", "--no-verify"],
            ["git", "checkout", "-B", "test-branch"],
        ):
            subprocess.run(args, cwd=proj, capture_output=True)

        runner = CliRunner()
        old = os.getcwd()
        try:
            os.chdir(str(proj))
            runner.invoke(
                cli,
                ["--json", "pr-bundle", "init", "--intent", "X"],
                catch_exceptions=False,
            )
            res = runner.invoke(
                cli,
                ["--json", "pr-bundle", "emit"],
                catch_exceptions=False,
            )
            assert res.exit_code in (0, 5), res.output
            env = _json.loads(res.output)
        finally:
            os.chdir(old)

        summary = env.get("summary") or {}
        q_keys = {
            "evidence_completeness",
            "questions_answered",
            "q_coverage",
            "coverage_matrix",
            "evidence_questions",
            "evidence_coverage",
            "q1_state",
            "q2_state",
            "q3_state",
            "q4_state",
            "q5_state",
            "q6_state",
            "q7_state",
            "q8_state",
        }
        present = q_keys & (set(summary.keys()) | set(env.keys()))
        assert not present, (
            f"W805-TTTT family-parity: cmd_pr_bundle envelope grew a "
            f"q-coverage signal {sorted(present)!r}; W805-TTTT xfail "
            "should XPASS and W805-DDDDD needs re-examination — the "
            "shared substrate fix may obviate all 3 family pins."
        )

    def test_w805_yyyy_invariants_preserved(self, tmp_path):
        """W805-YYYY pins the cmd_pr_replay packet-redactions layer
        Q1-Q7 asymmetric-marker absence. Re-confirm a thin probe
        that cmd_pr_replay's bare-fixture packet still carries the
        Q8-only marker shape (asymmetric coverage still present).
        """
        import subprocess

        proj = tmp_path / "replay_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
        (proj / "a.py").write_text(
            "def x():\n    return 1\n",
            encoding="utf-8",
        )
        for args in (
            ["git", "init"],
            ["git", "config", "user.email", "t@t"],
            ["git", "config", "user.name", "t"],
            ["git", "add", "-A"],
            ["git", "commit", "-m", "init", "--no-verify"],
        ):
            subprocess.run(args, cwd=proj, capture_output=True)
        (proj / "b.py").write_text(
            "def y():\n    return 2\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=proj, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add b", "--no-verify"],
            cwd=proj,
            capture_output=True,
        )

        evidence_path = proj / "ev.json"
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "roam",
                "--json",
                "pr-replay",
                "--tier",
                "sample",
                "--range",
                "HEAD~1..HEAD",
                "--evidence",
                str(evidence_path),
            ],
            cwd=proj,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 or not evidence_path.exists():
            pytest.skip(
                "pr-replay subprocess could not produce packet "
                f"(stderr tail: {r.stderr[-200:]!r}); W805-YYYY parity probe skipped"
            )

        from roam.evidence import ChangeEvidence

        packet = ChangeEvidence.from_canonical_json(evidence_path.read_text(encoding="utf-8"))
        redactions = list(packet.redactions or ())
        pna = [r_ for r_ in redactions if r_.startswith("producer_not_available")]
        # Today: exactly 1 producer_not_available entry, non-namespaced.
        # If this assertion fails, W805-YYYY has been fixed (symmetric
        # marker emission) and W805-DDDDD's premise needs revisit
        # because the consumer-flattening bug only matters while the
        # producer asymmetry exists.
        assert len(pna) == 1 and ":" not in pna[0], (
            f"W805-YYYY family-parity: cmd_pr_replay marker emission "
            f"changed (got pna={pna!r}); W805-YYYY xfail should XPASS "
            "and W805-DDDDD needs re-examination — if producer-side "
            "asymmetry is fixed, consumer-side flattening may be "
            "obviated."
        )
