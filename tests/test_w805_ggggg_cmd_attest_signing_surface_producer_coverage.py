"""W805-GGGGG -- cmd_attest signing-surface producer-coverage flattening.

Hundred-and-eleventh-in-batch W805 sweep. FOURTH member of the
evidence-compiler producer-coverage family (now 4-STRONG), confirming
the family shape established by W805-TTTT (cmd_pr_bundle envelope) +
W805-YYYY (cmd_pr_replay packet) + W805-DDDDD (cmd_evidence_doctor
consumer). This pin extends the family from MINT / REDACT / SURFACE
into SIGN: cmd_attest's ``--sign`` content_hash covers a legacy
7-axis ``evidence`` block (blast_radius / risk / breaking / fitness /
budget / tests / effects) and is structurally divorced from the W174
``ChangeEvidence`` evidence-compiler packet. The signed surface
carries NO Q-axis producer-coverage state, NO ``redactions[]``, NO
``producer_not_available`` marker, NO ``evidence_completeness()``
projection.

Family axis summary (axis 4-STRONG once this pins):

    W805-TTTT  cmd_pr_bundle        producer envelope-summary layer
                                    (no Q1-Q8 coverage matrix at all).
    W805-YYYY  cmd_pr_replay        producer packet redactions layer
                                    (W261 producer_not_available marker
                                    emitted for Q8 ONLY; Q1-Q7 silently
                                    emit empty state on no-producer).
    W805-DDDDD cmd_evidence_doctor  consumer-side reporting layer
                                    (reads the packet, flattens the
                                    Q8-only-marker-coverage state).
    W805-GGGGG cmd_attest           signing-surface layer
                                    (``--sign`` content_hash covers
                                    legacy 7-axis evidence dict; no
                                    Q1-Q8 producer-coverage state,
                                    no redactions[], no link to the
                                    W174 ChangeEvidence packet).

Same root failure shape (Pattern-1 variant D + Pattern-2 silent
fallback on the producer-coverage axis) projected onto a fourth
distinct file. The W978 distinctness check below confirms cmd_attest
SIGNING surface is structurally distinct from the cmd_attest
get_changed_files axis (W805-OOOO) AND the cmd_cga predicate identity
axis (W805-PPPP).

Bug class
---------
cmd_attest's ``--sign`` flag (cmd_attest.py:907-908) sets
``attestation["content_hash"] = _content_hash(evidence)``. The
``_content_hash`` helper (cmd_attest.py:494-497) computes SHA-256
over a canonical JSON dump of the 7-axis evidence dict assembled at
cmd_attest.py:889-897:

    evidence = {
        "blast_radius": blast,        # 1. blast-radius rollup
        "risk": risk,                 # 2. composite risk score
        "breaking_changes": breaking, # 3. breaking-change rollup
        "fitness": fitness,           # 4. fitness violations
        "budget": budget,             # 5. budget rollup
        "tests": tests,               # 6. affected tests
        "effects": effects,           # 7. effects list
    }

This is a 7-AXIS legacy evidence dict. None of the 8 evidence
questions per CLAUDE.md "The eight evidence questions" table are
classified at the signing boundary. Concretely:

  * NO ``actor_refs[]`` (Q1 actor) -- the attestation has a
    ``timestamp`` + ``tool`` + ``git_range`` but no human / agent /
    MCP / CI-runner identity stamp.
  * NO ``authority_refs[]`` (Q2 authority) -- no mode / permit /
    lease / policy-rule / approval / token-scope reference.
  * NO ``context_files[]`` (Q3 context) -- evidence.blast_radius has
    file counts but no list of files actually read.
  * Q4 changes IS covered (``git_range`` + ``blast_radius.changed_files``).
  * Q5 risk IS covered (``evidence.risk``).
  * NO ``policy_decisions[]`` (Q6 policy) -- evidence.fitness has
    violations but no policy-rule provenance.
  * Partial Q7 verify -- evidence.tests carries affected-tests
    selection but no run results / no attestation links.
  * NO ``approvals[]`` / ``accepted_risks[]`` (Q8 accept) -- no
    approval block at all in the signed surface.
  * NO ``redactions[]`` -- so a downstream verifier cannot tell
    whether a producer was attempted-and-empty (W261
    ``producer_not_available``) or never wired.

The signed ``content_hash`` therefore witnesses ONLY the legacy
evidence dict's bytes. Two attestations with identical legacy
evidence but completely different Q-axis producer-coverage states
(one with all 8 producers wired and successful, one with 6 producers
silently not-wired and Q8 ``producer_not_available``) produce
BYTE-IDENTICAL ``content_hash`` values.

Distinct from sibling family members.
-------------------------------------
* **W805-TTTT** lives at the cmd_pr_bundle envelope-summary layer.
  Different file, different command, different state (5-proof
  validation gate, no Q-axis classification).
* **W805-YYYY** lives at the cmd_pr_replay producer packet layer.
  Different file. cmd_pr_replay DOES build a full ChangeEvidence
  packet via the W176 collector; the asymmetry is in WHICH Q axes
  emit the W261 marker (Q8 only). cmd_attest doesn't build a
  ChangeEvidence packet AT ALL -- it skips the W174 substrate
  entirely.
* **W805-DDDDD** lives at the cmd_evidence_doctor consumer layer.
  Different file, consumer role (reads a packet). cmd_attest is a
  PRODUCER of a different artifact class (legacy attestation, not
  ChangeEvidence packet).
* **W805-OOOO** lives at the cmd_attest get_changed_files axis.
  SAME FILE as W805-GGGGG but DIFFERENT axis. W805-OOOO is about the
  bogus-ref silent-SAFE path inherited from a shared helper.
  W805-GGGGG is about the signed-surface content_hash coverage --
  the bug exists whether or not the changeset resolved cleanly.
* **W805-PPPP** lives at the cmd_cga predicate identity axis.
  Different file (cmd_cga.py vs cmd_attest.py), different artifact
  (in-toto Statement vs attestation envelope), different bug
  (verifier doesn't cross-check subject.name across repos).
  cmd_attest is structurally distinct from cmd_cga: cmd_attest
  builds the legacy 7-axis evidence dict; cmd_cga builds a separate
  CodeGraph predicate via the build_cga_statement helper.

Together the four pins form the evidence-compiler producer-coverage
FAMILY as a 4-STRONG axis: a structurally-complete bug class
spanning MINT (W805-TTTT) / REDACT (W805-YYYY) / SURFACE (W805-DDDDD)
/ SIGN (W805-GGGGG) -- all four exhibit Pattern-1 variant D +
Pattern-2 silent fallback on the same underlying state distinction
(producer attempted-and-empty vs producer-not-wired).

W978 first-hypothesis discipline
---------------------------------
Verified BEFORE pinning:

  * Ran ``roam --json attest --sign`` on a fresh fixture with
    uncommitted changes. Confirmed:
      - ``attestation.content_hash`` is computed.
      - The hash recomputes byte-identically from
        ``json.dumps(data['evidence'], sort_keys=True, default=str)``.
      - The signed surface (``attestation`` block) carries no Q-axis
        producer-coverage state.
      - The envelope summary carries NO ``redactions[]``, NO
        ``actor_refs[]``, NO ``authority_refs[]``, NO
        ``producer_not_available_marker``, NO ``q_coverage``.
  * Confirmed the bug is structurally DIFFERENT from W805-OOOO:
    W805-OOOO is the get_changed_files silent-SAFE on a bogus-ref
    path; W805-GGGGG is the signing-surface flattening on a clean
    changeset path. They co-exist in the same file via different
    code paths.
  * Confirmed the bug is structurally DIFFERENT from W805-PPPP:
    cmd_attest builds a 7-axis legacy dict via _content_hash;
    cmd_cga builds a CodeGraph in-toto predicate via
    build_cga_statement. Different substrate, different artifact
    schema, different hash domain.

W907 verify-cycle check
=======================
grep -i 'avoid.*cycle|circular import|kept local|would create a cycle|
duplicated.*here' on cmd_attest.py: NO MATCHES. The function-scoped
imports inside ``_collect_blast_radius`` (lines 119-122),
``_collect_risk`` (lines 166-178), ``_collect_breaking`` (lines
318-326), ``_collect_budget_evidence`` (lines 393-397),
``_collect_affected_tests_evidence`` (line 362),
``_collect_fitness_evidence`` (line 440), and the atomic_io imports
(lines 990, 1002, 1162) are legitimate cost-deferrals (networkx is
the heaviest) or path-conditional. The atomic_io imports are
explicitly documented (W531 / R28 substrate `unsafe_mutation` guard).
W907 clean.

W805 sweep update
=================
W805 sweep yield ~55/55. Evidence-compiler producer-coverage family
elevates from 3-STRONG (W805-TTTT + W805-YYYY + W805-DDDDD) to
4-STRONG with W805-GGGGG (cmd_attest signing surface). The family is
structurally COMPLETE across mint / redact / surface / sign layers.

Run isolation:
    python -m pytest tests/test_w805_ggggg_cmd_attest_signing_surface_producer_coverage.py -x -n 0
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 -- relative-to-tests-dir import after sys.path mutation
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def dirty_indexed_project(tmp_path):
    """Indexed project with uncommitted edits.

    cmd_attest needs at least one uncommitted change to exercise the
    full ``_collect_*`` -> evidence-dict -> ``_content_hash`` -> signed
    attestation pipeline. The clean-tree path takes the early
    ``no_changes`` branch (already pinned by W805-OOOO) and never
    builds the legacy evidence dict at all.
    """
    proj = tmp_path / "dirty-attest-repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def alpha():\n    return 1\n\ndef beta(x):\n    return alpha() + x\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    # Make an uncommitted edit so the attest pipeline has something
    # to assess (forces the post-``not changed`` branch at
    # cmd_attest.py:807+).
    (proj / "app.py").write_text(
        "def alpha():\n    return 1\n\ndef beta(x):\n    return alpha() + x\n\ndef gamma():\n    return 99\n"
    )
    return proj


# ---------------------------------------------------------------------------
# W978 first-hypothesis verification -- cmd_attest signs over a 7-axis
# legacy evidence dict via _content_hash. The W805-GGGGG axis is the
# signing-surface layer (DISTINCT from W805-OOOO's get_changed_files
# axis on the same file). Source-level guard so a refactor that moves
# the hash domain (e.g. starts hashing a ChangeEvidence packet instead)
# graduates the pin via test-failure rather than letting the bug class
# hide behind a stale assertion.
# ---------------------------------------------------------------------------


class TestCmdAttestSigningSurfaceShape:
    """W978 source-level invariant: cmd_attest defines _content_hash
    over the legacy 7-axis evidence dict, and the --sign flag drops
    its output into ``attestation["content_hash"]``. These are the
    structural anchors that distinguish W805-GGGGG from W805-OOOO
    (get_changed_files axis) and W805-PPPP (cmd_cga predicate)."""

    def test_content_hash_helper_exists(self):
        """cmd_attest must define _content_hash at module scope.

        Fails if a refactor renames / removes the helper. At that
        point the W805-GGGGG pin needs re-targeting against the new
        signing primitive."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_attest.py").read_text(
            encoding="utf-8"
        )
        assert "def _content_hash(" in src, (
            "W805-GGGGG W978-precondition: cmd_attest must define "
            "_content_hash at module scope; if this helper moved, "
            "re-audit the signing-surface family membership."
        )

    def test_content_hash_called_under_sign_flag(self):
        """The --sign flag must route through _content_hash(evidence)."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_attest.py").read_text(
            encoding="utf-8"
        )
        assert 'attestation["content_hash"] = _content_hash(evidence)' in src, (
            "W805-GGGGG W978-precondition: cmd_attest --sign must "
            "stamp ``attestation['content_hash'] = _content_hash(evidence)``; "
            "if this binding changed, the signed-surface coverage is "
            "structurally different and the pin needs re-targeting."
        )


# ---------------------------------------------------------------------------
# Pattern-1 Variant C guard -- --sign --json must not crash, must always
# emit a parseable envelope. Mirrors the W805-OOOO no-crash guard.
# ---------------------------------------------------------------------------


class TestAttestSignNoCrash:
    """--sign --json must always produce a structured envelope."""

    def test_sign_json_no_crash(self, cli_runner, dirty_indexed_project, monkeypatch):
        monkeypatch.chdir(dirty_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "--sign"],
            cwd=dirty_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"--sign --json must exit 0; got {result.exit_code}\n{result.output}"
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on --sign"
        data = parse_json_output(result, "attest")
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# POSITIVE shape pins -- the legacy 7-axis evidence dict + signed
# content_hash MUST keep working. These regression-pin the current
# producer-side shape so a future fix is verifiably additive.
# ---------------------------------------------------------------------------


class TestSignedSurfaceCarriesExistingEvidence:
    """cmd_attest already stamps blast_radius / risk / breaking /
    fitness / budget / tests / effects in evidence + a SHA-256
    content_hash. These positive pins prevent a future refactor from
    regressing the producer-side disclosure that already exists."""

    def test_attestation_block_has_content_hash(self, cli_runner, dirty_indexed_project, monkeypatch):
        monkeypatch.chdir(dirty_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "--sign"],
            cwd=dirty_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "attest")
        attestation = data.get("attestation") or {}
        ch = attestation.get("content_hash") or ""
        assert ch.startswith("sha256:"), f"REGRESSION: --sign must stamp sha256: content_hash; got {ch!r}"

    def test_evidence_block_has_seven_legacy_axes(self, cli_runner, dirty_indexed_project, monkeypatch):
        """The legacy 7-axis evidence dict shape must stay; a fix that
        adds Q-axis producer-coverage MUST NOT remove the existing axes."""
        monkeypatch.chdir(dirty_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "--sign"],
            cwd=dirty_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence") or {}
        expected = {
            "blast_radius",
            "risk",
            "breaking_changes",
            "fitness",
            "budget",
            "tests",
            "effects",
        }
        missing = expected - set(evidence.keys())
        assert not missing, (
            f"REGRESSION: legacy 7-axis evidence dict dropped axes "
            f"{sorted(missing)}; evidence keys={sorted(evidence.keys())}"
        )

    def test_content_hash_recomputes_over_evidence(self, cli_runner, dirty_indexed_project, monkeypatch):
        """The claimed content_hash must recompute byte-identically
        from ``json.dumps(evidence, sort_keys=True, default=str)``.
        This pins the hash DOMAIN to the legacy evidence dict --
        a future fix that broadens the hash to cover a ChangeEvidence
        packet will flip this test, which is the correct unwrap signal."""
        monkeypatch.chdir(dirty_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "--sign"],
            cwd=dirty_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "attest")
        evidence = data["evidence"]
        attestation = data["attestation"]
        claimed = attestation["content_hash"]
        canonical = json.dumps(evidence, sort_keys=True, default=str)
        recomputed = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert claimed == recomputed, (
            f"content_hash domain pin: claimed={claimed!r} vs "
            f"recomputed-over-evidence={recomputed!r}. If these differ, "
            f"the hash domain widened (likely a W805-GGGGG fix-forward) "
            f"and this regression-pin must be updated."
        )


# ---------------------------------------------------------------------------
# Sister-suite invariant cross-checks. The W805-OOOO get_changed_files
# axis + W805-PPPP cmd_cga identity axis + W805-DDDDD evidence_doctor
# consumer axis must stay structurally distinct from the W805-GGGGG
# signing-surface axis.
# ---------------------------------------------------------------------------


class TestW805OoooInvariantsPreserved:
    """Sister cross-check: cmd_attest's W805-OOOO axis
    (get_changed_files silent-SAFE) is distinct from W805-GGGGG.

    The W805-OOOO pin is about the bogus-ref path emitting a
    no-changes envelope without distinguishing clean-tree from typo.
    The W805-GGGGG pin is about the signed-surface content_hash on
    the DIRTY path. They co-exist in the same file via different
    code branches."""

    def test_cmd_attest_still_imports_get_changed_files(self):
        """W805-OOOO precondition stays: cmd_attest still consumes the
        shared helper. If a refactor removes this import, BOTH pins
        need re-targeting -- the W805-OOOO bug class would be gone
        and W805-GGGGG's axis-distinctness claim must be re-verified
        against the new resolution path."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_attest.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-GGGGG sister cross-check: W805-OOOO precondition "
            "broken -- cmd_attest must still import from "
            "roam.commands.changed_files."
        )
        assert "get_changed_files(root" in src, (
            "W805-GGGGG sister cross-check: W805-OOOO precondition "
            "broken -- cmd_attest must still call get_changed_files."
        )


class TestW805PpppInvariantsPreserved:
    """Sister cross-check: cmd_cga's W805-PPPP predicate-identity axis
    is structurally distinct from W805-GGGGG.

    cmd_attest builds a 7-axis legacy evidence dict via _content_hash;
    cmd_cga builds an in-toto CodeGraph predicate via
    build_cga_statement. Different files, different substrates,
    different hash domains."""

    def test_cmd_attest_does_not_build_cga_statement(self):
        """cmd_attest must NOT route through build_cga_statement.

        If cmd_attest is refactored to share the cmd_cga substrate,
        the W805-GGGGG axis-distinctness claim breaks and the family
        membership must be re-audited (the bug may disappear via the
        shared substrate carrying Q-axis state, or the family may
        merge with W805-PPPP)."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_attest.py").read_text(
            encoding="utf-8"
        )
        assert "build_cga_statement" not in src, (
            "W805-GGGGG sister cross-check: cmd_attest must NOT "
            "import build_cga_statement; if it does, W805-GGGGG and "
            "W805-PPPP have merged and the family membership needs "
            "re-auditing."
        )


class TestW805DddddInvariantsPreserved:
    """Sister cross-check: cmd_evidence_doctor's W805-DDDDD consumer
    axis is structurally distinct from W805-GGGGG.

    cmd_attest is a PRODUCER of a legacy attestation artifact;
    cmd_evidence_doctor is a CONSUMER that reads a ChangeEvidence
    packet. cmd_attest doesn't build a ChangeEvidence packet at all,
    so the two pins target structurally different boundaries."""

    def test_cmd_attest_does_not_build_change_evidence_packet(self):
        """cmd_attest must NOT route through the W174 ChangeEvidence
        substrate.

        If it does, the W805-GGGGG bug class is structurally gone
        (the signed surface would carry redactions[] + actor_refs[]
        + the Q-axis machinery). At that point the pin must flip to
        xpass -> test failure -> unwrap. Until then, cmd_attest
        SKIPS the ChangeEvidence packet entirely and signs over the
        legacy 7-axis dict."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_attest.py").read_text(
            encoding="utf-8"
        )
        # The collector + dataclass imports the upgrade would require.
        assert "from roam.evidence.collector import" not in src, (
            "W805-GGGGG sister cross-check: cmd_attest must NOT "
            "import the ChangeEvidence collector; if it does, the "
            "W805-GGGGG bug class is structurally fixed and the pin "
            "must unwrap."
        )
        assert "ChangeEvidence(" not in src, (
            "W805-GGGGG sister cross-check: cmd_attest must NOT "
            "construct a ChangeEvidence directly; if it does, the "
            "W805-GGGGG bug class is structurally fixed and the pin "
            "must unwrap."
        )


# ---------------------------------------------------------------------------
# REAL BUGS -- Pattern-1 Variant D + Pattern-2 silent-fallback on the
# producer-coverage axis. Pinned via xfail(strict=True) so a future fix
# is detected (xpass -> test failure -> unwrap and seal).
# ---------------------------------------------------------------------------


class TestSignedSurfaceCarriesPerQProducerState:
    """The signed surface must distinguish "producer attempted and
    came up empty" from "producer never wired" on at least one of
    the eight evidence questions. Today the signed attestation block
    carries NO Q-axis producer-coverage state."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-GGGGG REAL BUG: src/roam/commands/cmd_attest.py:907-908 "
            "(``if sign: attestation['content_hash'] = _content_hash(evidence)``) "
            "signs over the legacy 7-axis evidence dict assembled at "
            "cmd_attest.py:889-897 (blast_radius / risk / breaking_changes / "
            "fitness / budget / tests / effects). The signed surface carries "
            "no Q-axis producer-coverage state -- no actor_refs[] (Q1), no "
            "authority_refs[] (Q2), no context_files[] (Q3), no "
            "policy_decisions[] (Q6), no tests_run links (Q7), no approvals[] / "
            "accepted_risks[] (Q8), no redactions[] (W261 producer_not_available). "
            "Pattern-1 variant D silent-success-on-degraded-resolution + "
            "Pattern-2 silent-fallback: an attestation signed with 6 of 8 "
            "producers silently not-wired is byte-indistinguishable from "
            "one signed with all 8 producers wired and returning empty. "
            "FAMILY 4-STRONG: mint (W805-TTTT) + redact (W805-YYYY) + "
            "surface (W805-DDDDD) + sign (W805-GGGGG). Fix: route cmd_attest "
            "through the W176 collect_change_evidence() helper so the signed "
            "surface includes the ChangeEvidence packet's redactions[] + "
            "actor_refs[] + authority_refs[] axes. Pinned strict; graduates "
            "when the envelope discloses any Q-axis producer-coverage field."
        ),
    )
    def test_signed_envelope_carries_q_axis_producer_coverage(self, cli_runner, dirty_indexed_project, monkeypatch):
        """Signed attestation must disclose at least one Q-axis
        producer-coverage field."""
        monkeypatch.chdir(dirty_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "--sign"],
            cwd=dirty_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "attest")
        # Look across the summary + top-level envelope for ANY of the
        # canonical Q-axis disclosure fields the W174 / W182 / W211 /
        # W261 substrate defines.
        candidate_keys = {
            "actor_refs",
            "authority_refs",
            "environment_refs",
            "policy_decisions",
            "approvals",
            "accepted_risks",
            "redactions",
            "q_coverage",
            "evidence_completeness",
            "questions_answered",
            "producer_not_available_marker",
            "change_evidence",
        }
        summary = data.get("summary") or {}
        attestation = data.get("attestation") or {}
        evidence = data.get("evidence") or {}
        all_keys = set(data.keys()) | set(summary.keys()) | set(attestation.keys()) | set(evidence.keys())
        overlap = candidate_keys & all_keys
        assert overlap, (
            f"W805-GGGGG: --sign attestation discloses NO Q-axis "
            f"producer-coverage field. Looked for one of "
            f"{sorted(candidate_keys)}; envelope had "
            f"{sorted(all_keys)}."
        )


class TestSignedHashAsymmetricOnProducerCoverage:
    """The signed content_hash must change when producer-coverage
    state changes. Today, the hash is computed over a 7-axis legacy
    evidence dict that carries no Q-axis state, so two attestations
    with identical legacy evidence but different producer-coverage
    states (e.g. Q8 producer_not_available vs Q8 producer-never-wired)
    produce byte-identical content_hash values."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-GGGGG REAL BUG (hash-domain axis): the signed "
            "attestation hash domain is exclusively the 7-axis legacy "
            "evidence dict (cmd_attest.py:889-897). It contains no "
            "ChangeEvidence packet, no redactions[], no actor_refs[], "
            "no authority_refs[]. Two attestations with identical "
            "legacy evidence but different producer-coverage states "
            "produce BYTE-IDENTICAL content_hash values, defeating "
            "the tamper-detection purpose of --sign on the producer-"
            "coverage axis. Pinned strict; graduates when the hash "
            "domain widens to cover at least one Q-axis producer-"
            "coverage field (validated by the canonical recompute "
            "in test_content_hash_recomputes_over_evidence flipping)."
        ),
    )
    def test_signed_hash_domain_covers_producer_coverage(self, cli_runner, dirty_indexed_project, monkeypatch):
        """The signed hash MUST cover at least one Q-axis producer-
        coverage field. Asserted by checking the hash does NOT
        recompute byte-identically from just the legacy evidence dict
        (which would prove the domain is too narrow)."""
        monkeypatch.chdir(dirty_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "--sign"],
            cwd=dirty_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "attest")
        evidence = data["evidence"]
        attestation = data["attestation"]
        claimed = attestation["content_hash"]
        canonical = json.dumps(evidence, sort_keys=True, default=str)
        recomputed = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        # The bug: claimed == recomputed-from-legacy-evidence-only,
        # which proves the hash domain is exclusively the legacy
        # evidence dict -- no Q-axis coverage in the signed surface.
        # Fix: hash domain widens to cover ChangeEvidence packet
        # fields, breaking this exact equality.
        assert claimed != recomputed, (
            f"W805-GGGGG: signed content_hash domain is exclusively "
            f"the legacy 7-axis evidence dict (claimed == recomputed "
            f"from evidence alone). Producer-coverage state is not "
            f"in the hash domain; two attestations with different "
            f"Q-axis producer-coverage states but identical legacy "
            f"evidence produce byte-identical content_hash. "
            f"claimed={claimed!r}, recomputed={recomputed!r}."
        )


# ---------------------------------------------------------------------------
# Positive regression -- the clean / dirty discrimination still works.
# Guards against an over-correcting fix-forward that breaks the
# basic --sign content_hash + tool-version-block disclosure.
# ---------------------------------------------------------------------------


class TestSignPositiveRegression:
    """Positive regression: --sign + --json envelope must still
    carry attestation.tool + attestation.tool_version + git_range +
    timestamp. These pre-W805-GGGGG fields must stay even after the
    bug is fixed."""

    def test_attestation_metadata_pinned(self, cli_runner, dirty_indexed_project, monkeypatch):
        monkeypatch.chdir(dirty_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "--sign"],
            cwd=dirty_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "attest")
        attestation = data["attestation"]
        assert attestation.get("tool") == "roam-code", (
            f"REGRESSION: attestation.tool not 'roam-code'; got {attestation.get('tool')!r}"
        )
        assert attestation.get("tool_version"), (
            f"REGRESSION: attestation.tool_version missing/empty; got {attestation.get('tool_version')!r}"
        )
        assert attestation.get("git_range"), (
            f"REGRESSION: attestation.git_range missing/empty; got {attestation.get('git_range')!r}"
        )
        assert attestation.get("timestamp"), (
            f"REGRESSION: attestation.timestamp missing/empty; got {attestation.get('timestamp')!r}"
        )
