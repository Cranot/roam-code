"""W805-WWWW -- VSA verifier identity-skip probe (hypothesis DISCONFIRMED).

Hundred-and-first-in-batch W805 sweep, third potential verifier-side
member of the lineage-disclosure family alongside:

- W805-PPPP cmd_cga         (predicate.subject[0].name never checked)
- W805-UUUU cmd_audit_trail_verify (actor/repo/git_sha never cross-checked)

Hypothesis from W805-UUUU agent: ``cmd_attest_vsa`` / ``verify_vsa_statement``
might mirror W805-PPPP's verifier-side identity-skip on the VSA axis
(``predicate.verifier.id`` / ``subject.name`` from a foreign repo silently
verifying ``PASSED`` from another repo's working directory).

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

The hypothesis is **DISCONFIRMED** by surface probe. VSA in roam is
**emit-only** -- there is NO verify path inside roam at all.

1. **VSA emit surface probe.** ``src/roam/attest/vsa.py`` ships
   ``build_vsa_statement`` / ``build_vsa_predicate`` (pure producer
   functions) and ``src/roam/attest/emit_vsa.py`` ships
   ``emit_pr_bundle_slsa_l3`` / ``emit_cga_vsa_sibling`` (write-to-disk
   wrappers). No verify primitive.

2. **CLI surface probe.** ``roam pr-bundle emit --slsa-l3`` and
   ``roam cga emit --also-vsa`` both EMIT a VSA next to the parent
   bundle/CGA. There is NO ``roam vsa verify`` subcommand. The CGA
   verify path (``cmd_cga.cga_verify`` / ``verify_cga_statement``) only
   accepts the CodeGraph/v1 + AIBOM/v1 predicate types -- VSA's
   ``https://slsa.dev/verification_summary/v1`` is explicitly NOT in
   the ``accepted_types`` tuple at ``src/roam/attest/cga.py:399``.

3. **Architectural rationale.** The VSA module docstring at
   ``src/roam/attest/vsa.py:1-43`` is explicit: VSA is a thin projection
   so that ``slsa-verifier`` / Sigstore / Rekor consumers can ingest
   roam attestations. Verification is **delegated to external SLSA
   verifiers**, not implemented inside roam. The roam wording-lint at
   ``src/roam/attest/vsa.py:38-42`` reinforces this: "roam emits the
   evidence; the verifier asserts the claim."

4. **No-verifier means no verifier-side bug.** A "verifier-side identity-
   skip" defect requires a verify code path inside roam that walks a
   VSA statement and produces a verdict. No such path exists. The W805-
   WWWW hypothesis is **structurally inapplicable** to VSA.

5. **Distinctness from W805-PPPP / W805-UUUU.**
   - W805-PPPP cmd_cga: roam SHIPS ``verify_cga_statement`` (cga.py:383-458)
     -- bug is on subject.name skip.
   - W805-UUUU cmd_audit_trail_verify: roam SHIPS ``_verify_chain``
     (cmd_audit_trail_verify.py:163-212) -- bug is on actor/repo/git_sha
     skip.
   - W805-WWWW cmd_attest_vsa: roam SHIPS NO VSA verifier. Bug class is
     not reachable on this surface.

W907 verify-cycle check
=======================

``grep -i 'avoid.*cycle|circular import|kept local|would create a cycle|
duplicated.*here'`` on ``src/roam/attest/vsa.py`` +
``src/roam/attest/emit_vsa.py`` == NO MATCHES. The lazy import
``from roam.attest.emit_vsa import emit_pr_bundle_slsa_l3`` at
``cmd_pr_bundle.py:3093`` is a flag-conditional benign deferred import
(slsa-l3 path is opt-in), not a cargo-cult false cycle. W907 clean.

W805 sweep impact
=================

VSA axis disconfirms the family-3-STRONG verifier-side claim. The
lineage-disclosure family stays at:

- 5-STRONG producer-side (W805-BBBB simulate, W805-DDDD orchestrate,
  W805-GGGG capsule, W805-IIII fingerprint, W805-LLLL runs)
- 2-STRONG verifier-side (W805-PPPP cga, W805-UUUU audit-trail-verify)
- = 7-STRONG TOTAL (unchanged from W805-UUUU)

This file pins the **architectural invariant** (VSA emit-only, no
verifier) so a future agent that ADDS a ``verify_vsa_statement``
function in roam will trigger this test and re-open the verifier-side
identity-skip audit at that time.

Run isolation:
    python -m pytest tests/test_w805_wwww_vsa_verifier_identity_skip.py -x -n 0

Regression baseline:
    python -m pytest tests/test_attest_vsa.py tests/test_w1261_vsa_stale_consumer.py -x -n 0

Sister parity:
    python -m pytest tests/test_w805_pppp_cmd_cga_attestation_lineage.py \
        tests/test_w805_uuuu_cmd_audit_trail_verify_identity_skip.py -x -n 0
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 -- verify the surface before testing)
# ---------------------------------------------------------------------------


_VSA_SPEC = importlib.util.find_spec("roam.attest.vsa")
_EMIT_VSA_SPEC = importlib.util.find_spec("roam.attest.emit_vsa")
_CGA_SPEC = importlib.util.find_spec("roam.attest.cga")


def test_w978_vsa_emit_substrate_present():
    """W978 gate: vsa + emit_vsa + cga modules import cleanly."""
    if _VSA_SPEC is None:
        pytest.skip("roam.attest.vsa not installed")
    assert _EMIT_VSA_SPEC is not None, "roam.attest.emit_vsa missing"
    assert _CGA_SPEC is not None, "roam.attest.cga missing"


# ---------------------------------------------------------------------------
# Core W978-DISCONFIRMED pins -- the architectural invariant that flips the
# hypothesis into a non-bug. If a future contributor adds verify_vsa_statement
# these tests will FAIL and reopen the verifier-side identity-skip audit.
# ---------------------------------------------------------------------------


class TestW805WwwwVsaVerifierAbsenceInvariant:
    """VSA is emit-only in roam. No verify path => no verifier-side
    identity-skip bug. These tests pin that invariant explicitly so a
    future agent that adds verify_vsa_statement triggers a re-audit.
    """

    def test_vsa_module_has_no_verify_function(self):
        """``src/roam/attest/vsa.py`` exposes only producer functions.

        Pinned names: ``build_vsa_predicate``, ``build_vsa_statement``,
        ``build_run_ledger_root_predicate``, ``build_run_ledger_root_statement``.
        Forbidden names (would re-open the W805-WWWW audit):
        ``verify_vsa_statement``, ``verify_vsa_predicate``,
        ``check_vsa_identity``, ``cosign_verify_vsa``.
        """
        if _VSA_SPEC is None:
            pytest.skip("roam.attest.vsa not installed")
        vsa = importlib.import_module("roam.attest.vsa")
        public_names = {n for n in dir(vsa) if not n.startswith("_")}
        # Producer-side surface is load-bearing.
        producers = {
            "build_vsa_predicate",
            "build_vsa_statement",
            "build_run_ledger_root_predicate",
            "build_run_ledger_root_statement",
        }
        missing_producers = producers - public_names
        assert not missing_producers, (
            f"W978 baseline broken: VSA producer functions missing: "
            f"{sorted(missing_producers)}. The surface this test pins has "
            f"shifted; rerun the W805-WWWW probe."
        )
        # Verifier-side surface MUST stay absent.
        forbidden = {
            "verify_vsa_statement",
            "verify_vsa_predicate",
            "check_vsa_identity",
            "cosign_verify_vsa",
        }
        present_forbidden = forbidden & public_names
        assert not present_forbidden, (
            f"W805-WWWW re-audit trigger: a VSA verifier path has been "
            f"added to roam.attest.vsa ({sorted(present_forbidden)}). "
            f"The verifier-side identity-skip family-3 hypothesis is "
            f"now reachable and must be re-probed against the new "
            f"surface. See CLAUDE.md Pattern-1-V-D + W805-PPPP / "
            f"W805-UUUU sister pins."
        )

    def test_emit_vsa_module_has_no_verify_function(self):
        """``src/roam/attest/emit_vsa.py`` is the W486 write-to-disk
        wrapper. Same invariant: emit only, no verify."""
        if _EMIT_VSA_SPEC is None:
            pytest.skip("roam.attest.emit_vsa not installed")
        emit_vsa = importlib.import_module("roam.attest.emit_vsa")
        public_names = {n for n in dir(emit_vsa) if not n.startswith("_")}
        forbidden = {
            "verify_vsa_statement",
            "verify_vsa_predicate",
            "verify_pr_bundle_slsa_l3",
            "verify_cga_vsa_sibling",
        }
        present_forbidden = forbidden & public_names
        assert not present_forbidden, (
            f"W805-WWWW re-audit trigger: VSA verifier surface added to "
            f"roam.attest.emit_vsa ({sorted(present_forbidden)}). Re-run "
            f"the verifier-side identity-skip probe against the new path."
        )

    def test_cga_verify_rejects_vsa_predicate_type(self):
        """The CGA verify path explicitly does NOT accept the VSA
        predicate type. This is the load-bearing reason why a "VSA
        verifier-side identity-skip" cannot land on the cga.verify_*
        surface: the function refuses VSA statements entirely.

        Pinned at ``src/roam/attest/cga.py:383-458`` (verify_cga_statement)
        -- ``accepted_types = (PREDICATE_TYPE, PREDICATE_TYPE_AIBOM,
        *_LEGACY_PREDICATE_TYPES)``. SLSA_VSA_PREDICATE_TYPE
        (``https://slsa.dev/verification_summary/v1``) is NOT in that
        tuple. A statement bearing that predicateType produces a
        "predicateType mismatch" error in the verifier output.
        """
        if _CGA_SPEC is None or _VSA_SPEC is None:
            pytest.skip("roam.attest.cga or roam.attest.vsa not installed")
        cga = importlib.import_module("roam.attest.cga")
        vsa = importlib.import_module("roam.attest.vsa")
        accepted = (
            cga.PREDICATE_TYPE,
            cga.PREDICATE_TYPE_AIBOM,
            *cga._LEGACY_PREDICATE_TYPES,
        )
        assert vsa.SLSA_VSA_PREDICATE_TYPE not in accepted, (
            f"W805-WWWW re-audit trigger: cga.verify_cga_statement now "
            f"accepts the SLSA VSA predicate type. The verifier-side "
            f"identity-skip family-3 hypothesis is reachable through "
            f"this surface and must be re-probed. accepted_types now "
            f"include {vsa.SLSA_VSA_PREDICATE_TYPE!r}."
        )

    def test_no_cli_subcommand_named_vsa_verify(self):
        """No ``roam vsa verify`` / ``roam attest vsa verify`` CLI
        subcommand exists. The verification side is delegated to
        external SLSA verifiers per the architectural docstring at
        ``src/roam/attest/vsa.py:1-43``.
        """
        try:
            from roam.cli import _COMMANDS
        except Exception as exc:
            pytest.skip(f"roam.cli not importable: {exc}")
        # Walk the full canonical-name map. None of these should mention
        # vsa-verify or vsa-validate.
        suspect = [
            name
            for name in _COMMANDS
            if "vsa" in name.lower() and ("verify" in name.lower() or "validate" in name.lower())
        ]
        assert not suspect, (
            f"W805-WWWW re-audit trigger: roam CLI now exposes a VSA "
            f"verifier subcommand: {suspect}. Re-run the verifier-side "
            f"identity-skip probe."
        )


# ---------------------------------------------------------------------------
# Producer-side identity surface pin -- documents that VSA verifier_id IS
# carried in the predicate (so external verifiers CAN cross-check). The
# bug, if any, would land in the EXTERNAL verifier; it is not reachable
# inside roam today.
# ---------------------------------------------------------------------------


class TestVsaProducerCarriesIdentityForExternalVerifier:
    """The VSA predicate DOES carry identity lineage (verifier.id,
    resourceUri, subject[0].name) so that external SLSA verifiers can
    do the cross-check roam itself does not perform. Pin the shape so a
    future refactor that drops the identity fields trips the bug pin
    on the *external* contract.
    """

    def test_predicate_carries_verifier_id(self):
        if _VSA_SPEC is None:
            pytest.skip("roam.attest.vsa not installed")
        vsa = importlib.import_module("roam.attest.vsa")
        from roam.evidence.change_evidence import ChangeEvidence

        ce = ChangeEvidence(
            evidence_id="probe-w805-wwww",
            repo_id="https://example.com/alice/repo.git",
            commit_sha="a" * 40,
        )
        pred = vsa.build_vsa_predicate(ce)
        assert "verifier" in pred, pred
        assert pred["verifier"].get("id") == "https://roam-code.com", pred["verifier"]

    def test_predicate_carries_resource_uri(self):
        if _VSA_SPEC is None:
            pytest.skip("roam.attest.vsa not installed")
        vsa = importlib.import_module("roam.attest.vsa")
        from roam.evidence.change_evidence import ChangeEvidence

        ce = ChangeEvidence(
            evidence_id="probe-w805-wwww",
            repo_id="https://example.com/alice/repo.git",
            commit_sha="b" * 40,
        )
        pred = vsa.build_vsa_predicate(ce)
        # External verifier reads resourceUri to confirm the verdict
        # was issued against this code. roam emits it; verification is
        # the external verifier's job.
        assert pred.get("resourceUri", "").startswith("git+https://example.com/alice/repo.git@"), pred.get(
            "resourceUri"
        )


# ---------------------------------------------------------------------------
# Sister-suite invariant cross-checks (must stay green; do NOT re-assert
# the sister files' xfail-strict claims to avoid collision).
# ---------------------------------------------------------------------------


class TestW805PpppInvariantsPreserved:
    """W805-PPPP (cmd_cga verify subject.name skip) sister cross-check.

    Baseline: ``roam cga emit --no-write`` produces a parseable envelope
    with a predicate. We do NOT re-assert W805-PPPP's xfail-strict pin.
    """

    def test_cga_emit_baseline_parseable(self, tmp_path):
        try:
            from roam.cli import cli
            from tests.conftest import index_in_process
        except Exception as exc:
            pytest.skip(f"roam.cli / conftest not importable: {exc}")

        # Build a minimal git repo.
        proj = tmp_path / "wwww_cga_baseline"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
        (proj / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=str(proj), capture_output=True)

        out, rc = index_in_process(proj, "--force")
        assert rc == 0, out
        runner = CliRunner()
        old = os.getcwd()
        try:
            os.chdir(str(proj))
            r = runner.invoke(cli, ["--json", "cga", "emit", "--no-write"], catch_exceptions=False)
        finally:
            os.chdir(old)
        assert r.exit_code == 0, r.output
        raw = (r.output or "").lstrip()
        decoder = json.JSONDecoder()
        data, _end = decoder.raw_decode(raw)
        statement = data.get("statement") or {}
        assert statement.get("predicate"), f"predicate missing in {data}"


class TestW805UuuuInvariantsPreserved:
    """W805-UUUU (cmd_audit_trail_verify identity-skip) sister.

    Baseline: empty corpus -> state=uninitialized + partial_success=True.
    Confirms the verifier-side family member's load-bearing 3-state
    matrix did not regress (the identity-skip xfail-strict pin is owned
    by W805-UUUU's own file).
    """

    def test_audit_trail_verify_uninitialized_baseline(self, tmp_path):
        try:
            from roam.cli import cli
        except Exception as exc:
            pytest.skip(f"roam.cli not importable: {exc}")

        proj = tmp_path / "wwww_atv_baseline"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
        (proj / "app.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=str(proj), capture_output=True)

        runner = CliRunner()
        old = os.getcwd()
        try:
            os.chdir(str(proj))
            r = runner.invoke(cli, ["--json", "audit-trail-verify"], catch_exceptions=False)
        finally:
            os.chdir(old)
        # No --gate -> exit 0 regardless of state.
        assert r.exit_code == 0, r.output
        raw = (r.output or "").lstrip()
        decoder = json.JSONDecoder()
        data, _end = decoder.raw_decode(raw)
        summary = data.get("summary") or {}
        assert summary.get("state") == "uninitialized", summary
        assert summary.get("partial_success") is True, summary
