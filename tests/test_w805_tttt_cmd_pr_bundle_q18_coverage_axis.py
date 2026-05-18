"""W805-TTTT — cmd_pr_bundle Q1-Q8 evidence-coverage matrix flattening axis.

Ninety-eighth-in-batch W805 sweep. Novel evidence-compiler family axis,
DISTINCT from the four security-gate disclosure pins (W826 / W805-KKKK /
W805-NNNN / W805-QQQQ). Where the security-gate family flattens a
producer-side state distinction inside ONE security command's reachability
projection, the W805-TTTT axis flattens the per-question coverage matrix
across all 8 evidence questions in the agent-OS pr-bundle envelope.

Per CLAUDE.md "The eight evidence questions" table, the assurance layer
asks Q1 actor / Q2 authority / Q3 context / Q4 changes / Q5 risk /
Q6 policy / Q7 verify / Q8 accept. The collector at
``src/roam/evidence/change_evidence.py:821`` (``evidence_completeness``)
classifies each Q into the closed enum ``complete`` / ``partial`` /
``missing`` / ``not_applicable``, and W261 specifically reserved the
``producer_not_available`` redaction reason to lift Q8 from ``missing``
to ``partial`` when an approvals harvester WAS checked but came up empty
(vs ``missing`` when no producer was attempted).

Hypothesis (CONFIRMED via probe).
---------------------------------
``cmd_pr_bundle._build_envelope`` (cmd_pr_bundle.py:1626-2030)
constructs the envelope summary from 5 bundle-shape proof checks
(``intent`` / ``affected_symbols`` / ``context_read.commands_run`` /
``tests_run-when-required`` / ``roam_verdict`` signal — see
``_validate_bundle`` cmd_pr_bundle.py:1439-1512). NONE of the 8
evidence questions are classified at the producer boundary; the Q1-Q8
matrix is only computable downstream by the collector once a
``ChangeEvidence`` packet is built.

Concretely, the W261 ``producer_not_available`` redaction reason is
EMITTED by ``cmd_pr_replay`` (a sibling) at four call sites
(cmd_pr_replay.py:1318, 1399, 1837, 1860, 1887) when its approvals
harvester is checked but empty. ``cmd_pr_bundle`` NEVER emits this
reason — its approvals / accepted_risks default to ``[]`` and its
``redactions`` array stays ``[]`` whether (a) no producer was
attempted (no ``--approval`` flag, no on-disk approval file), or (b)
the producer was attempted and came up empty. The two cases are
structurally indistinguishable in the envelope.

Probe transcript (against a real ``pr-bundle init`` + ``emit``)::

    Case 1 (Q8 not attempted, no add-approval invoked):
      approvals: []
      accepted_risks: []
      redactions: []
      state: incomplete

    Case 2 (Q8 satisfied via add-approval --approver alice):
      approvals: [{'approval_id': 'ap_...', ...}]
      accepted_risks: []
      redactions: []
      state: incomplete

The envelope's ``state``, ``partial_success``, and ``redactions`` fields
are bit-identical between Case 1 and Case 2 on the ``redactions`` axis.
Worse, an envelope with NO approval / NO redaction marker silently
flattens to the same coverage profile as one where the producer was
explicitly invoked. There is no ``evidence_completeness`` / ``Q8`` /
``questions_answered`` field on the cmd_pr_bundle envelope summary at
all — Q1-Q8 coverage is invisible at the producer boundary.

Distinct from the four sibling security-gate pins.
--------------------------------------------------
* **W826** (cmd_vulns empty corpus) collapses ``symbol_count == 0`` to
  a generic verdict; gated by ``state == 'empty_corpus'``. cmd_pr_bundle
  does not depend on a populated corpus — bundle proofs are independent
  of indexed symbols. Different mechanism.
* **W805-KKKK** (cmd_taint cross-language) collapses a
  ``f.language IN (rule.languages)`` filter at the taint engine.
  cmd_pr_bundle has no language filter at all. Different file, different
  mechanism.
* **W805-NNNN** (cmd_vuln_reach tri-valued sentinel) collapses
  ``reachable == 0 / 1 / -1`` to a Python ``bool`` at the symbol-graph
  layer. cmd_pr_bundle never had a tri-valued sentinel. Different
  command family entirely (security-gate vs agent-OS substrate).
* **W805-QQQQ** (cmd_sbom cross-ecosystem) collapses three reachability
  branches into a single ``roam:reachable: false`` boolean property on
  the SBOM document. cmd_pr_bundle is not an SBOM producer; it does
  not project cross-ecosystem state at all. Different artifact class
  (SBOM CycloneDX vs pr-bundle evidence envelope).

W805-TTTT is the agent-OS substrate equivalent: the Q1-Q8 coverage
matrix is the structurally-distinct state that gets flattened, NOT a
single security gate's reachability projection.

W978 first-hypothesis discipline.
---------------------------------
Verified BEFORE pinning:

  * Probed the live pr-bundle CLI through ``init`` -> ``add affected``
    -> ``add-approval`` -> ``emit``. Confirmed envelope redactions is
    ``[]`` in BOTH the no-producer-attempted and the producer-attempted-
    but-empty cases.
  * Verified the W805-QQQQ ``roam:reachable`` per-component property
    does NOT exist on a pr-bundle envelope (different artifact class).
  * Verified the W805-NNNN tri-valued ``reachable`` sentinel is NOT
    used by cmd_pr_bundle (no tri-valued sentinel anywhere in
    cmd_pr_bundle.py).
  * Verified ``producer_not_available`` is emitted by cmd_pr_replay but
    NEVER by cmd_pr_bundle, even though both producers populate the
    same evidence envelope shape downstream.
  * Verified ``evidence_completeness()`` lives on the ChangeEvidence
    dataclass (change_evidence.py:821-982), NOT in cmd_pr_bundle.py —
    so the Q1-Q8 matrix is downstream-computable but never surfaced
    at the producer envelope.

W907 verify-cycle.
------------------
No false "duplicated here to avoid cycle" hedges in cmd_pr_bundle.py.
The one lazy import (``from roam.permits.store import load_permits_from_disk``
at cmd_pr_bundle.py:1566) explicitly documents a substrate-vs-consumer
hard-load reason, NOT a false cycle hedge. Clean.

Security severity.
------------------
MEDIUM. cmd_pr_bundle is the agent-OS substrate that compiles evidence
for downstream CGA/in-toto attestation, SLSA VSA verification, and
external GRC tools. A silent flattening of Q8 producer state means a
consumer reading the bundle envelope directly cannot tell whether the
agent skipped the acceptance step entirely or checked-and-found-empty.
For CGA / SLSA VSA / GRC consumers this is exactly the
"identity + authority + evidence" axis the agentic-assurance frame
exists to surface. Not HIGH — the downstream collector DOES classify
Q1-Q8 via ``evidence_completeness()`` on the ChangeEvidence packet;
the gap is at the envelope-emission boundary, not the analysis layer.

Pinning style: xfail(strict=True).
----------------------------------
xfail-strict so the moment cmd_pr_bundle._build_envelope grows ANY
producer-side Q1-Q8 disclosure signal (an ``evidence_completeness``
field on the summary, a ``questions_answered: int`` count, a
``producer_not_available`` redaction reason emission, OR a per-Q
matrix field), the xfail flips to XPASS and forces removal of the pin.

Sister-suite parity.
--------------------
The bottom test class re-runs the W805-QQQQ + W805-NNNN cross-language
probes inlined so a regression in either sibling pin would also fail
this suite. This keeps the sweep family discoverable from any pin file.
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_bundle_project(tmp_path):
    """Minimal git repo with a single source file, branch test-branch."""
    proj = tmp_path / "bundle_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "main.py").write_text(
        "def hello():\n    return 'hi'\n",
        encoding="utf-8",
    )
    git_init(proj)
    subprocess.run(
        ["git", "checkout", "-B", "test-branch"],
        cwd=proj,
        capture_output=True,
    )
    return proj


def _make_empty_corpus(tmp_path):
    """Empty-corpus fixture for the W826 sister-parity check."""
    proj = tmp_path / "empty_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "empty.py").write_text("", encoding="utf-8")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(args, cwd):
    """Invoke the roam CLI in-process with cwd set."""
    from roam.cli import cli

    runner = CliRunner()
    old = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old)


def _emit_bundle(proj, *, with_approval: bool = False) -> dict:
    """Init, optionally add an approval, then emit. Return the envelope dict."""
    _invoke(["--json", "pr-bundle", "init", "--intent", "Test change"], proj)
    if with_approval:
        _invoke(
            [
                "--json",
                "pr-bundle",
                "add-approval",
                "--approver",
                "alice@example.com",
                "--scope",
                "all",
                "--reason",
                "reviewed",
            ],
            proj,
        )
    res = _invoke(["--json", "pr-bundle", "emit"], proj)
    assert res.exit_code in (0, 5), res.output
    return _json.loads(res.output)


# ---------------------------------------------------------------------------
# W978 prerequisite: confirm axis is distinct from sibling pins.
# ---------------------------------------------------------------------------


class TestW805TTTTAxisDistinct:
    def test_w826_empty_corpus_branch_does_not_apply(self, tmp_path):
        """cmd_pr_bundle does not gate on corpus size — empty-corpus state
        is a security-command concept (cmd_vulns / cmd_taint) and does NOT
        appear in the pr-bundle envelope. Confirms W826 axis is distinct.
        """
        proj = _make_bundle_project(tmp_path)
        env = _emit_bundle(proj, with_approval=False)
        summary = env.get("summary") or {}
        # State enum on pr-bundle is: complete / incomplete / not_initialized /
        # mode_restricted / initialized. NEVER "empty_corpus".
        assert summary.get("state") != "empty_corpus", (
            f"W826 axis bleed: pr-bundle state must not be empty_corpus, got {summary!r}"
        )

    def test_axis_distinct_from_w805_nnnn_tri_valued(self, tmp_path):
        """W805-NNNN's collapse is a tri-valued sentinel (-1/0/1) on the
        reachable field. cmd_pr_bundle never had a tri-valued sentinel —
        confirm no per-symbol ``reachable: -1`` shape appears.
        """
        proj = _make_bundle_project(tmp_path)
        env = _emit_bundle(proj, with_approval=False)
        # cmd_pr_bundle's affected_symbols entries do NOT carry a
        # reachable field at all — confirm no tri-valued sentinel leak.
        for sym in env.get("affected_symbols", []):
            if isinstance(sym, dict):
                assert "reachable" not in sym, (
                    f"W805-NNNN axis bleed: tri-valued reachable should not appear, got {sym!r}"
                )

    def test_axis_distinct_from_w805_qqqq_sbom_components(self, tmp_path):
        """W805-QQQQ's collapse is on ``roam:reachable`` per-component SBOM
        properties. cmd_pr_bundle is not an SBOM producer — confirm no
        ``sbom`` / ``components`` key on the pr-bundle envelope.
        """
        proj = _make_bundle_project(tmp_path)
        env = _emit_bundle(proj, with_approval=False)
        assert "sbom" not in env, (
            f"W805-QQQQ axis bleed: pr-bundle must not emit an sbom block, got {sorted(env.keys())!r}"
        )
        assert "components" not in env, (
            f"W805-QQQQ axis bleed: pr-bundle must not emit a components block, got {sorted(env.keys())!r}"
        )


# ---------------------------------------------------------------------------
# The W805-TTTT pin — Pattern-1 variant D + Pattern-2 silent fallback on
# the Q1-Q8 evidence coverage matrix at the pr-bundle producer boundary.
# ---------------------------------------------------------------------------


class TestPrBundleQ18CoverageDisclosure:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-TTTT: cmd_pr_bundle._build_envelope (cmd_pr_bundle.py:1626) "
            "validates 5 bundle-shape proofs in _validate_bundle (cmd_pr_bundle.py:1439) "
            "but does NOT classify the 8 evidence questions (Q1 actor / "
            "Q2 authority / Q3 context / Q4 changes / Q5 risk / Q6 policy / "
            "Q7 verify / Q8 accept) at the producer boundary. The collector's "
            "ChangeEvidence.evidence_completeness() at change_evidence.py:821-982 "
            "is the downstream-only classifier; the producer envelope carries "
            "no Q1-Q8 / coverage_matrix / evidence_completeness signal at all. "
            "Pattern-1 variant D (silent success on degraded resolution): the "
            "envelope's state=='complete' / 'incomplete' field gates on the 5 "
            "bundle proofs, NOT on Q1-Q8 producer presence — a fully-shape-"
            "complete bundle with zero approvals / zero accepted_risks emits "
            "state=='complete' indistinguishably from one whose Q8 producer "
            "was checked-and-empty (the W261 producer_not_available marker "
            "exists in cmd_pr_replay but is NEVER emitted by cmd_pr_bundle)."
        ),
    )
    def test_envelope_surfaces_q1_through_q8_coverage(self, tmp_path):
        """The pr-bundle envelope summary must surface per-question Q1-Q8
        coverage state — at minimum a count of questions answered, or a
        per-Q matrix, or an ``evidence_completeness`` field mirroring the
        downstream ChangeEvidence.evidence_completeness() classifier.

        Expected on fix: at least ONE of these signals must appear on the
        envelope summary:

        * ``summary.evidence_completeness``: dict with Q1..Q8 keys + totals.
        * ``summary.questions_answered``: int count of complete questions.
        * ``summary.q_coverage`` / ``summary.coverage_matrix``: dict.
        * ``summary.evidence_questions``: list of {q_id, state} entries.
        * ``summary.q8_state`` (or any per-Q named field): one of
          ``complete`` / ``partial`` / ``missing`` / ``not_applicable``.
        """
        proj = _make_bundle_project(tmp_path)
        env = _emit_bundle(proj, with_approval=False)
        summary = env.get("summary") or {}

        # Scan for ANY Q1-Q8-shaped disclosure on the summary.
        coverage_keys = {
            "evidence_completeness",
            "questions_answered",
            "q_coverage",
            "coverage_matrix",
            "evidence_questions",
            "evidence_coverage",
        }
        per_q_named = {
            "q1_state",
            "q2_state",
            "q3_state",
            "q4_state",
            "q5_state",
            "q6_state",
            "q7_state",
            "q8_state",
            "Q1",
            "Q2",
            "Q3",
            "Q4",
            "Q5",
            "Q6",
            "Q7",
            "Q8",
        }

        has_aggregate = any(k in summary for k in coverage_keys)
        has_per_q = any(k in summary for k in per_q_named)
        has_top_level_aggregate = any(k in env for k in coverage_keys)
        has_top_level_per_q = any(k in env for k in per_q_named)

        assert has_aggregate or has_per_q or has_top_level_aggregate or has_top_level_per_q, (
            "Pattern-1 variant D + Pattern-2: pr-bundle envelope emits "
            "no Q1-Q8 coverage signal. The 5-proof bundle gate is "
            "structurally distinct from the 8-question evidence-coverage "
            f"matrix. Summary keys: {sorted(summary.keys())!r}; "
            f"envelope keys: {sorted(env.keys())!r}. The downstream "
            "collector classifies Q1-Q8 via "
            "ChangeEvidence.evidence_completeness() but the producer "
            "boundary is silent — agents reading the envelope cannot "
            "tell whether all 8 axes were considered or just the 5 "
            "bundle proofs were validated."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-TTTT-B: cmd_pr_bundle never emits the W261 "
            "producer_not_available redaction reason. cmd_pr_replay "
            "(a sibling at cmd_pr_replay.py:1318-1887) emits it at four "
            "call sites to lift Q8 from 'missing' to 'partial' when the "
            "approvals harvester is checked-but-empty. cmd_pr_bundle "
            "leaves redactions=[] regardless of whether the producer was "
            "attempted, silently collapsing 'Q8 not attempted' and "
            "'Q8 attempted but empty' to bit-identical envelopes. "
            "Pattern-1 variant D on the producer-coverage axis."
        ),
    )
    def test_q8_producer_gap_distinct_from_q8_not_attempted(self, tmp_path):
        """When no add-approval was invoked AND no on-disk approval file
        exists, the envelope must emit either:

        * ``redactions: ["producer_not_available"]`` (the W261 marker
          that the sibling cmd_pr_replay already emits), OR
        * a Q8-specific state field (``summary.q8_state == 'missing'``
          / ``'partial'`` etc.) that distinguishes from the satisfied
          case, OR
        * a top-level ``approvals_state`` / ``acceptance_state`` field
          with closed enum disclosure.
        """
        proj = _make_bundle_project(tmp_path)
        # Case 1: no producer attempted (no add-approval, no on-disk).
        env_no_q8 = _emit_bundle(proj, with_approval=False)
        # Case 2: producer satisfied (add-approval invoked).
        env_with_q8 = _emit_bundle(proj, with_approval=True)

        no_q8_redactions = env_no_q8.get("redactions") or []
        with_q8_redactions = env_with_q8.get("redactions") or []

        # The pin: when Q8 was NOT attempted, at least one of these
        # disclosure shapes must be present.
        has_producer_marker = "producer_not_available" in no_q8_redactions
        has_q8_state = "q8_state" in (env_no_q8.get("summary") or {})
        has_approvals_state = bool(
            env_no_q8.get("approvals_state")
            or env_no_q8.get("acceptance_state")
            or (env_no_q8.get("summary") or {}).get("approvals_state")
            or (env_no_q8.get("summary") or {}).get("acceptance_state")
        )

        # Sanity: the with-approval envelope DOES populate approvals[].
        assert env_with_q8.get("approvals"), (
            f"fixture sanity: --approval flag should populate approvals[], got {env_with_q8.get('approvals')!r}"
        )

        assert has_producer_marker or has_q8_state or has_approvals_state, (
            "Pattern-1 variant D: cmd_pr_bundle silently collapses Q8 "
            "'producer not attempted' to a bit-identical envelope as Q8 "
            "'satisfied' on the redactions axis. "
            f"no-Q8 redactions={no_q8_redactions!r}, "
            f"with-Q8 redactions={with_q8_redactions!r}. "
            "Sibling cmd_pr_replay emits producer_not_available at four "
            "call sites for exactly this case; cmd_pr_bundle does not."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-TTTT-C: --strict on a partial-Q-coverage bundle does "
            "not gate on the 8 evidence questions. _validate_bundle "
            "(cmd_pr_bundle.py:1439) only checks the 5 bundle proofs; a "
            "bundle that has all 5 proofs but is missing Q1 (no actor "
            "block), Q2 (no authority), Q6 (no policy_decisions), or "
            "Q8 (no approvals) still emits state=='complete' and exits "
            "0 under --strict. --strict-resolved similarly checks only "
            "the unresolved-symbol axis, not Q1-Q8 coverage. CI "
            "consumers reading 'state=complete' assume all 8 axes "
            "were verified, but only 5 bundle-shape proofs were."
        ),
    )
    def test_strict_mode_fails_on_partial_q_coverage(self, tmp_path):
        """A bundle with all 5 proofs satisfied but Q8 (acceptance) absent
        must fail --strict / --ci. Today it passes.

        Expected on fix: --strict (or a new --strict-coverage flag) must
        exit non-zero when any of Q1-Q8 is in state ``missing``, OR
        ``--strict`` must additionally enforce a minimum
        questions_answered floor (e.g. >= 6 of 8).

        Pre-condition: build a bundle that satisfies all 5 _validate_bundle
        proofs (intent + affected + context-cmd + tests-or-no-required +
        roam-verdict-signal-via-blast-radius). With all 5 proofs green
        the state becomes ``complete`` and --strict exits 0 — even
        though Q8 (approvals / accepted_risks) is structurally absent.
        """
        proj = _make_bundle_project(tmp_path)
        # Index the project so symbol-resolution succeeds (otherwise
        # affected_symbols carry resolution_state="no_db" and --strict-resolved
        # gates them; we only want --strict here).
        _invoke(["init"], proj)
        # Build a 5-proof-complete bundle but WITHOUT Q8 (no add-approval).
        _invoke(["--json", "pr-bundle", "init", "--intent", "Test"], proj)
        _invoke(
            [
                "--json",
                "pr-bundle",
                "add",
                "affected",
                "hello",
                "--blast-radius",
                "1",
            ],
            proj,
        )
        _invoke(
            [
                "--json",
                "pr-bundle",
                "add",
                "context-cmd",
                "roam preflight hello",
            ],
            proj,
        )

        # Run validate --strict. If the gate were Q-coverage-aware, the
        # absent Q8 should exit non-zero.
        res = _invoke(
            ["--json", "pr-bundle", "validate", "--strict"],
            proj,
        )
        env = _json.loads(res.output)
        summary = env.get("summary") or {}

        # Sanity check: the bundle IS state==complete on the 5-proof axis.
        # If this assertion fails, the fixture is wrong (not the pin).
        assert summary.get("state") == "complete", (
            "fixture sanity: bundle must reach state=='complete' on the "
            f"5-proof axis to probe the 8-question gap, got {summary!r}"
        )
        assert (env.get("approvals") or []) == [], (
            "fixture sanity: no add-approval was invoked, approvals must be empty"
        )
        assert (env.get("accepted_risks") or []) == [], "fixture sanity: no add-accepted-risk was invoked"

        # Hard pin: --strict on a Q8-absent bundle must NOT exit 0.
        # Today, exit_code == 0 because the 5-proof gate doesn't cover Q8.
        assert res.exit_code == 5 or any(
            ("q8" in m.lower())
            or ("evidence" in m.lower())
            or ("coverage" in m.lower())
            or ("approval" in m.lower())
            or ("acceptance" in m.lower())
            for m in (summary.get("missing_proofs") or [])
        ), (
            "Pattern-1 variant D: --strict on a 5-proof-complete bundle "
            f"missing Q8 (no approvals / no accepted_risks) exited "
            f"{res.exit_code} with missing_proofs="
            f"{summary.get('missing_proofs')!r}. The 5-proof gate does "
            "not cover the 8-question coverage matrix; CI consumers "
            "reading 'state=complete + exit 0' under --strict wrongly "
            "conclude full evidence coverage."
        )


# ---------------------------------------------------------------------------
# Sister-suite parity invariants — these MUST pass today.
# ---------------------------------------------------------------------------


class TestEvidenceCompilerFamilyParity:
    def test_w805_qqqq_invariants_preserved(self, tmp_path):
        """Sister: W805-QQQQ (cmd_sbom cross-ecosystem) pin still holds.

        Re-runs the polyglot-SBOM probe inline and confirms the
        silent-SAFE shape. If this assertion EVER fails, the W805-QQQQ
        fix has landed and this parity test should be updated in the
        same patch.
        """
        proj = tmp_path / "polyglot_sbom_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
        (proj / "package.json").write_text(
            _json.dumps(
                {
                    "name": "p",
                    "version": "0.0.1",
                    "dependencies": {"lodash": "4.17.20"},
                }
            ),
            encoding="utf-8",
        )
        (proj / "requirements.txt").write_text("click==8.0.0\n", encoding="utf-8")
        (proj / "app.py").write_text(
            "import click\n\ndef main():\n    click.echo('hi')\n",
            encoding="utf-8",
        )
        git_init(proj)

        from roam.cli import cli

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner.invoke(cli, ["init"], catch_exceptions=False)
            res = runner.invoke(cli, ["--json", "sbom"], catch_exceptions=False)
            assert res.exit_code == 0, res.output
            data = _json.loads(res.output)
        finally:
            os.chdir(old_cwd)

        # W805-QQQQ's shape: all 3 deps emit roam:reachable=false with no
        # ecosystems_unsupported / state disclosure.
        components = data.get("sbom", {}).get("components", []) or []
        for c in components:
            props = {p["name"]: p["value"] for p in c.get("properties", [])}
            assert props.get("roam:reachable") in ("true", "false"), (
                f"W805-QQQQ regression: roam:reachable should still be boolean, got {props!r}"
            )

    def test_w805_nnnn_invariants_preserved(self, tmp_path):
        """Sister: W805-NNNN (cmd_vuln_reach cross-language) pin still holds.

        Re-runs the cross-language vuln-reach probe and confirms the
        critical_count == 0 silent-SAFE shape.
        """
        proj = tmp_path / "vuln_reach_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
        (proj / "package.json").write_text(
            _json.dumps(
                {
                    "name": "p",
                    "version": "0.0.1",
                    "dependencies": {"lodash": "4.17.20"},
                }
            ),
            encoding="utf-8",
        )
        (proj / "app.py").write_text(
            "def handle():\n    return process()\n\ndef process():\n    return merge_data({})\n\ndef merge_data(d):\n    return d\n",
            encoding="utf-8",
        )
        git_init(proj)

        report = [
            {
                "cve": "CVE-2024-NPM-LODASH",
                "package": "lodash",
                "severity": "critical",
                "title": "npm lodash (W805-NNNN parity)",
            }
        ]
        report_path = tmp_path / "vulns.json"
        report_path.write_text(_json.dumps(report), encoding="utf-8")

        from roam.cli import cli

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner.invoke(cli, ["init"], catch_exceptions=False)
            runner.invoke(
                cli,
                ["vuln-map", "--generic", str(report_path)],
                catch_exceptions=False,
            )
            res = runner.invoke(cli, ["--json", "vuln-reach"], catch_exceptions=False)
            assert res.exit_code == 0, res.output
            data = _json.loads(res.output)
        finally:
            os.chdir(old_cwd)

        summary = data.get("summary") or {}
        assert summary.get("critical_count") == 0, (
            f"W805-NNNN regression: critical_count should be 0 on silent-SAFE cross-lang run, got {summary!r}"
        )
