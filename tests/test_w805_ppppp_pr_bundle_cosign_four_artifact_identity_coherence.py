"""W805-PPPPP -- 4-artifact identity-coherence pin for ``pr-bundle emit
--slsa-l3 --sign --keyless``.

Hundred-and-twentieth-in-batch W805 sweep. Extends the W805-OOOOO
"3-artifact cross-artifact consistency" axis (bundle envelope + SLSA VSA
+ run-ledger-root statement) to the FOUR-artifact case introduced by
``--sign --keyless``: when cosign is requested, a per-statement
signature triplet (``.sig`` / ``.cert`` / ``.bundle``) joins each
in-toto statement on disk. The W805-PPPPP axis is: does the cosign
signature artifact's roam-emit metadata agree with the underlying
VSA / run-ledger-root statement's subject / payloadType / dirty-tree
disclosure -- and does cosign act as the cross-artifact correlation
point that W805-OOOOO axis B pinned as MISSING between VSA and
run-ledger-root?

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **Producer inventory.** ``roam pr-bundle emit --slsa-l3 --sign --keyless``
   yields FOUR artifact kinds in one shot:

   * **Artifact 1 -- the pr-bundle envelope.** Same as W805-OOOOO: top-level
     ``commit_sha`` + ``bundle_meta.git`` block; no ``slsa_l3.signatures[]``
     pre-sign.
   * **Artifact 2 -- the SLSA VSA statement.** Same as W805-OOOOO:
     ``subject[0].digest = {sha1: commit, sha256: content_hash}``,
     ``predicateType = SLSA_VSA_PREDICATE_TYPE``.
   * **Artifact 3 -- the run-ledger-root statement.** Same as W805-OOOOO:
     ``subject[0].name = "urn:roam:run:<run_id>"``,
     ``subject[0].digest = {sha256: <final_signature>}``,
     ``predicateType = RUN_LEDGER_ROOT_PREDICATE_TYPE``.
   * **Artifact 4 -- the cosign signature triplet.** Per-statement
     ``.sig`` / ``.cert`` / ``.bundle`` written by
     ``src/roam/attest/cga.py:cosign_sign_statement`` (subprocess wrapper
     around ``cosign sign-blob --yes <stmt> --output-signature <sig>
     --bundle <bundle>``, with ``--output-certificate <cert>`` added on
     the keyless path). The result is projected into
     ``slsa_l3.signatures[]`` by
     ``src/roam/attest/emit_vsa.py:_serialize_cosign_result``, one entry
     per signed target (``target="vsa"`` and ``target="run_ledger_root"``).

2. **The signature-entry shape (live probe).** Per the projection at
   ``emit_vsa.py:70-98`` and the dataclass at ``cga.py:480-491``, each
   ``slsa_l3.signatures[i]`` entry carries:

   * ``target`` (label: ``"vsa"`` OR ``"run_ledger_root"``)
   * ``signed`` (bool)
   * ``statement_path`` (the in-toto file that was signed)
   * ``bundle_path`` / ``signature_path`` / ``certificate_path`` (paths
     to the cosign outputs)
   * ``skipped_reason`` (str, empty on success)
   * ``cosign_version`` (str)

   What's NOT there: ``payload_subject``, ``payload_subject_digest``,
   ``payload_predicate_type``, ``payloadType``, ``subject_digest``,
   ``related_signature``, ``git_dirty_hash``, or any field that
   correlates the signature artifact back to the underlying statement's
   identity claims. The signature entry is a bag of file paths + a
   version string. A downstream consumer that ingests only the cosign
   signature output (the standard sigstore consumption pattern: verify
   the bytes you have, no need to fetch the source statement back)
   cannot tell:

   * which commit_sha the underlying statement covered (Axis A);
   * which in-toto ``predicateType`` was signed (Axis B);
   * which other roam artifact this signature pairs with -- the two
     signature entries for ``vsa`` and ``run_ledger_root`` sit in the
     same list but neither carries an explicit cross-reference (Axis C,
     the strongest gap -- this is the slot W805-OOOOO axis B's
     "MISSING correlation link" between VSA and run-ledger-root could
     naturally live in, but doesn't);
   * whether the tree was dirty at sign time (Axis D, sibling to
     W805-KKKKK axis B and W805-OOOOO axis D, now propagated through
     a fourth artifact kind).

3. **Distinct from W805-OOOOO.** W805-OOOOO probed cross-artifact
   identity coherence on the THREE structured-JSON artifacts
   (envelope + VSA + run-ledger-root). W805-PPPPP probes the FOURTH
   artifact kind (cosign signature output) and the wrapper metadata
   roam emits about it. The bug shape is the same family
   (cross-artifact consistency + Pattern-2 silent fallback) but the
   surface is genuinely new: the signature entry shape is roam-owned,
   not in-toto-spec-defined, so the fix lives entirely in
   ``_serialize_cosign_result`` / ``_sign_one`` rather than in any
   external spec.

W978 axis-distinctness
======================

W805-PPPPP is **structurally distinct** from its W805 siblings:

* **W805-KKKKK** (CGA<->sibling-VSA): TWO artifacts, both JSON
  attestations, written by ``cga emit --also-vsa``. No signing.
* **W805-OOOOO** (this's predecessor): THREE artifacts, all
  JSON, written by ``pr-bundle emit --slsa-l3``. No signing.
* **W805-PPPPP** (this): FOUR artifacts of THREE kinds (envelope
  JSON + two in-toto statements + cosign-signed binary triplet),
  written by ``pr-bundle emit --slsa-l3 --sign --keyless``. The
  fourth artifact kind is roam-OWNED at the metadata layer (the
  signature-entry projection in ``slsa_l3.signatures[]``); the bytes
  on disk are cosign-defined but the metadata that names them is
  roam-defined and currently carries zero cross-artifact correlation.

Pinned via ``xfail(strict=True)`` on each drift axis; a future fix
flips xpass -> failure -> unwrap and seal.

W907 verify-cycle check
=======================

``grep -i 'avoid.*cycle|circular import|kept local|would create a cycle|
duplicated.*here'`` over ``src/roam/attest/cga.py`` +
``src/roam/attest/emit_vsa.py`` + ``src/roam/attest/vsa.py`` yields
ZERO false cycle hedges. W907 clean. ``_cga`` is imported as a module
alias in ``emit_vsa.py`` (not via ``from ... import``) for monkeypatch
compatibility -- that's a genuine binding-pattern choice, not a false
cycle hedge.

Run isolation
=============

    python -m pytest tests/test_w805_ppppp_pr_bundle_cosign_four_artifact_identity_coherence.py -x -n 0

Sister parity
=============

    python -m pytest tests/test_w805_ooooo_pr_bundle_slsa_l3_three_artifact_identity_coherence.py \
        tests/test_w805_kkkkk_cga_vsa_sibling_consistency.py \
        tests/test_attest_vsa.py -x -n 0
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 -- verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_PR_BUNDLE_SPEC = importlib.util.find_spec("roam.commands.cmd_pr_bundle")
_EMIT_VSA_SPEC = importlib.util.find_spec("roam.attest.emit_vsa")
_VSA_SPEC = importlib.util.find_spec("roam.attest.vsa")
_CGA_SPEC = importlib.util.find_spec("roam.attest.cga")
_RUNS_LEDGER_SPEC = importlib.util.find_spec("roam.runs.ledger")


def test_substrate_modules_present():
    """W978/W907 gate: pr_bundle + emit_vsa + vsa + cga + runs.ledger import."""
    if _CMD_PR_BUNDLE_SPEC is None:
        pytest.skip("roam.commands.cmd_pr_bundle not installed")
    assert _EMIT_VSA_SPEC is not None, "roam.attest.emit_vsa missing"
    assert _VSA_SPEC is not None, "roam.attest.vsa missing"
    assert _CGA_SPEC is not None, "roam.attest.cga missing"
    assert _RUNS_LEDGER_SPEC is not None, "roam.runs.ledger missing"


def test_cosign_signing_path_exists():
    """W978: confirm the ``--sign --keyless`` path is wired in
    ``pr-bundle emit --slsa-l3`` BEFORE pinning any drift on it.

    If cosign integration were absent (e.g. only VSA + run-ledger-root
    emit, no signing wrapper), the W805-PPPPP probe would be N/A and
    the whole file would skip. The presence-check here makes that
    skip explicit rather than producing misleading xfails on a missing
    surface.
    """
    from roam.attest.cga import (
        CosignResult,
        cosign_available,
        cosign_sign_statement,
    )

    # Wrapper API exists (callable + dataclass).
    assert callable(cosign_sign_statement)
    assert callable(cosign_available)
    assert hasattr(CosignResult, "__dataclass_fields__")
    # The result fields confirm the integration model.
    assert {
        "signed",
        "statement_path",
        "signature_path",
        "certificate_path",
        "bundle_path",
        "skipped_reason",
        "cosign_version",
    }.issubset(set(CosignResult.__dataclass_fields__.keys()))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _git(args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo_with_bundle(tmp_path):
    """Initialise a tiny git repo + hand-craft a minimal pr-bundle on disk.

    Byte-identical to the W805-OOOOO fixture so the FOUR-artifact probe
    extends the THREE-artifact probe by adding ``--sign --keyless`` to the
    same invocation.
    """
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "test"], tmp_path)
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], tmp_path).stdout.strip()
    bundle_dir = tmp_path / ".roam" / "pr-bundles"
    bundle_dir.mkdir(parents=True)
    bundle = {
        "intent": "demo intent",
        "context_cmd": "roam preflight f",
        "affected_symbols": [{"symbol": "f", "kind": "function", "file": "a.py"}],
        "tests": [{"test_id": "t1", "passed": True}],
        "verdict": "PASS",
        "risk_level": "low",
    }
    (bundle_dir / f"{branch}.json").write_text(json.dumps(bundle), encoding="utf-8")
    return tmp_path


def _emit_quadruple(runner, repo: Path, monkeypatch, *, run_id: str = "r_ppppp_demo"):
    """Drive ``pr-bundle emit --slsa-l3 --sign --keyless`` with a stubbed
    cosign so ALL FOUR artifacts (envelope + VSA + run-ledger-root +
    cosign signature entries) materialise in one invocation.

    Cosign is stubbed -- the test environment is not guaranteed to have
    cosign installed (or be willing to do an OIDC keyless flow). The
    stub returns a fake CosignResult so the emit path believes signing
    succeeded and populates ``slsa_l3.signatures[]`` with both entries
    (``target="vsa"`` and ``target="run_ledger_root"``). This is the
    same stub pattern ``test_attest_vsa.py::test_emit_signs_when_sign_flag_set``
    uses -- the W805-PPPPP axes pin metadata on the entries themselves,
    not the cryptographic content of the signatures.

    Returns ``(envelope, vsa, run_root, signatures)`` where ``signatures``
    is ``slsa_l3.signatures``: a list of two dicts indexed by ``target``.
    """
    from roam.attest import cga as cga_mod

    # The signature/cert/bundle paths the fake reports; they don't need
    # to exist on disk (the emit path doesn't read them back).
    sig_dir = repo / ".roam" / "pr-bundle"

    class _FakeCosignResult:
        def __init__(self, statement_path: Path):
            self.signed = True
            self.statement_path = statement_path
            stem = statement_path.stem
            self.signature_path = sig_dir / f"{stem}.sig"
            self.certificate_path = sig_dir / f"{stem}.cert"
            self.bundle_path = sig_dir / f"{stem}.bundle"
            self.skipped_reason = ""
            self.cosign_version = "v2.4.0-mock"

    monkeypatch.setattr(
        cga_mod,
        "cosign_sign_statement",
        lambda statement_path, **kw: _FakeCosignResult(statement_path),
    )

    # Stub the run-ledger meta read so the run-ledger-root statement
    # gets emitted (same pattern as W805-OOOOO).
    class _StubMeta:
        run_id = "r_ppppp_demo"
        agent = "claude"
        started_at = "2026-05-18T00:00:00+00:00"
        ended_at = "2026-05-18T00:01:00+00:00"
        status = "completed"
        final_signature = "ef" * 32
        event_count = 3

    monkeypatch.setattr(
        "roam.runs.ledger.read_run_meta",
        lambda root, run_id_arg: _StubMeta() if run_id_arg == run_id else None,
    )
    monkeypatch.setenv("ROAM_RUN_ID", run_id)
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")
    monkeypatch.chdir(repo)

    from roam.cli import cli

    result = runner.invoke(
        cli,
        [
            "--json",
            "pr-bundle",
            "emit",
            "--no-auto-collect",
            "--slsa-l3",
            "--sign",
            "--keyless",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    slsa = envelope.get("slsa_l3") or {}
    vsa_path_str = slsa.get("vsa_path")
    run_path_str = slsa.get("run_ledger_root_path")
    signatures = slsa.get("signatures") or []
    assert vsa_path_str, f"VSA path missing: {slsa}"
    assert run_path_str, f"run-ledger-root path missing: {slsa}"
    assert signatures, f"signatures[] empty: {slsa}"
    vsa = json.loads(Path(vsa_path_str).read_text(encoding="utf-8"))
    run_root = json.loads(Path(run_path_str).read_text(encoding="utf-8"))
    return envelope, vsa, run_root, signatures


def _by_target(signatures: list[dict], target: str) -> dict:
    """Pick the signature entry for a given target (``"vsa"`` /
    ``"run_ledger_root"``)."""
    for sig in signatures:
        if sig.get("target") == target:
            return sig
    raise AssertionError(f"no signature entry for target={target!r}; got {signatures!r}")


# ---------------------------------------------------------------------------
# Setup invariant -- BOTH signature entries are produced in one invocation.
# ---------------------------------------------------------------------------


class TestBothSignatureEntriesEmitted:
    """Sanity: ``pr-bundle emit --slsa-l3 --sign --keyless`` produces
    TWO signature entries (one per signed statement). This is the
    pre-condition for every W805-PPPPP axis -- if signing yielded only
    one entry, the cross-artifact correlation discussion would be
    void.
    """

    def test_both_targets_present_and_signed(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_quadruple(cli_runner, repo_with_bundle, monkeypatch)
        targets = sorted(sig.get("target") for sig in signatures)
        assert targets == ["run_ledger_root", "vsa"], (
            f"Expected one signature entry per signed statement; got {targets}"
        )
        for sig in signatures:
            assert sig.get("signed") is True, sig
            assert sig.get("cosign_version") == "v2.4.0-mock"


# ---------------------------------------------------------------------------
# REAL BUG axis A -- cosign signature entry carries NO ``commit_sha`` /
# subject-digest mirror from the underlying statement.
#
# A downstream consumer reading ``slsa_l3.signatures[i]`` cannot tell
# which commit the signed statement covered without going back to disk
# to re-read the source file. Pattern-2 silent fallback: the signature
# metadata claims provenance without naming what it covers.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-PPPPP axis A -- cosign signature entry carries NO mirror "
        "of the underlying statement's subject.digest. "
        "``src/roam/attest/emit_vsa.py:70-98`` "
        "(``_serialize_cosign_result``) projects the cosign result into "
        "{signed, statement_path, signature_path, certificate_path, "
        "bundle_path, skipped_reason, cosign_version}. There is no "
        "``payload_subject_digest`` field. A downstream consumer "
        "ingesting only ``slsa_l3.signatures[i]`` (the common sigstore "
        "consumption pattern: verify the signature output, no need to "
        "re-fetch the source statement) cannot tell which commit_sha "
        "the VSA signature covered. The VSA's own "
        "``subject[0].digest.sha1`` carries the commit, but the "
        "signature entry that names the VSA on disk does NOT mirror it. "
        "Fix template: have ``_serialize_cosign_result`` lift the "
        "underlying statement's ``subject[0].digest`` into a "
        "``payload_subject_digest`` field on the signature entry so the "
        "cosign artifact is self-describing without a back-fetch. "
        "Family: cross-artifact consistency + Pattern-2 silent fallback."
    ),
)
class TestVsaSignatureEntryMirrorsCommitSha:
    def test_vsa_signature_carries_payload_subject_digest(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, vsa, _run, signatures = _emit_quadruple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")
        # The VSA itself carries commit_sha as subject[0].digest.sha1.
        vsa_sha1 = (vsa.get("subject") or [{}])[0].get("digest", {}).get("sha1")
        assert vsa_sha1, f"setup invariant: VSA missing subject sha1: {vsa.get('subject')}"
        # Required: the signature entry mirrors that digest so the cosign
        # output is self-describing.
        payload_digest = (
            vsa_sig.get("payload_subject_digest")
            or (vsa_sig.get("payload_subject") or {}).get("digest")
            or vsa_sig.get("subject_digest")
        )
        assert payload_digest, (
            "Cross-artifact: VSA signature entry MUST carry a mirror of "
            "the signed statement's subject digest so consumers of "
            f"slsa_l3.signatures[vsa] are self-describing. entry={vsa_sig!r}"
        )
        # And the mirror must equal the underlying sha1.
        mirrored = payload_digest.get("sha1") if isinstance(payload_digest, dict) else payload_digest
        assert mirrored == vsa_sha1, (
            "Cross-artifact: VSA signature entry's payload_subject_digest "
            f"must equal VSA subject sha1; entry={mirrored!r} vsa={vsa_sha1!r}"
        )


# ---------------------------------------------------------------------------
# REAL BUG axis B -- cosign signature entry carries NO ``payloadType`` /
# ``predicateType`` mirror.
#
# A consumer that ingests only ``slsa_l3.signatures[i]`` cannot tell
# whether the signed bytes were a SLSA VSA statement or a run-ledger-root
# statement (the two have different predicate types). The ``target``
# label is roam-internal vocabulary, not the in-toto spec's
# ``predicateType`` URI.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-PPPPP axis B -- cosign signature entry carries NO mirror "
        "of the underlying statement's ``predicateType``. The roam "
        '``target`` label (``"vsa"`` / ``"run_ledger_root"``) is '
        "roam-internal vocabulary, not the in-toto predicateType URI "
        "(``https://slsa.dev/verification_summary/v1`` for VSA, "
        "``https://roam.dev/attestations/run-ledger-root/v1`` for the "
        "run root). A downstream verifier handed only "
        "``slsa_l3.signatures[i]`` cannot tell which in-toto statement "
        "schema was signed without re-reading the source file. The "
        "schema discriminator lives EXCLUSIVELY in the source file's "
        "``predicateType`` field; the signature entry doesn't surface "
        "it. Fix template: have ``_serialize_cosign_result`` lift the "
        "underlying statement's ``predicateType`` into a "
        "``payload_predicate_type`` field on the signature entry. "
        "Family: cross-artifact consistency."
    ),
)
class TestSignatureEntryMirrorsPayloadType:
    def test_vsa_and_run_signatures_carry_payload_predicate_type(self, repo_with_bundle, cli_runner, monkeypatch):
        from roam.attest.vsa import (
            RUN_LEDGER_ROOT_PREDICATE_TYPE,
            SLSA_VSA_PREDICATE_TYPE,
        )

        _env, vsa, run_root, signatures = _emit_quadruple(cli_runner, repo_with_bundle, monkeypatch)
        # Setup invariant: source files carry distinct predicate types.
        assert vsa.get("predicateType") == SLSA_VSA_PREDICATE_TYPE
        assert run_root.get("predicateType") == RUN_LEDGER_ROOT_PREDICATE_TYPE

        for target, expected_pt in (
            ("vsa", SLSA_VSA_PREDICATE_TYPE),
            ("run_ledger_root", RUN_LEDGER_ROOT_PREDICATE_TYPE),
        ):
            entry = _by_target(signatures, target)
            mirrored = entry.get("payload_predicate_type") or entry.get("predicateType") or entry.get("payloadType")
            assert mirrored == expected_pt, (
                f"Cross-artifact: {target!r} signature entry MUST carry "
                f"payload_predicate_type={expected_pt!r}; entry={entry!r}"
            )


# ---------------------------------------------------------------------------
# REAL BUG axis C -- THE STRONGEST GAP. The two signature entries
# (``target="vsa"`` + ``target="run_ledger_root"``) sit in the SAME list
# but carry NO cross-reference to each other.
#
# W805-OOOOO axis B pinned that the VSA and run-ledger-root statements
# themselves carry no cross-reference. The natural slot for the
# correlation link is the cosign signature layer -- the two signatures
# ARE produced in one invocation, against the same ChangeEvidence
# context, by the same emit path. Yet the signature entries don't
# reference each other either. The correlation gap survives the fourth
# artifact.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-PPPPP axis C (STRONGEST GAP) -- the two cosign signature "
        "entries produced by ``pr-bundle emit --slsa-l3 --sign --keyless`` "
        '(``target="vsa"`` + ``target="run_ledger_root"``) sit in the '
        "SAME ``slsa_l3.signatures[]`` list but carry NO explicit "
        "cross-reference to each other. W805-OOOOO axis B pinned that "
        "the underlying VSA and run-ledger-root statements themselves "
        "carry no ``related_attestation`` link. The natural place to "
        "close that gap is the cosign signature layer: BOTH signatures "
        "are produced in one invocation, against the same ChangeEvidence "
        "context, against the same emit path, with the same cosign "
        "configuration. A verifier that fetches the VSA signature from "
        "a Rekor transparency log MUST be able to find the paired "
        "run-ledger-root signature without out-of-band coordination. "
        "Today, the signature entries are isolated: each carries only "
        "its own statement_path / signature_path / certificate_path / "
        "bundle_path. There is no ``related_signature`` / "
        "``co_signed_with`` / ``signature_set_id`` field that would "
        "let a verifier walk from one signature to the other. Fix "
        "template: stamp a shared ``signature_set_id`` "
        "(e.g. the ChangeEvidence content_hash) on BOTH entries so a "
        "verifier can group co-signed artifacts. Family: cross-artifact "
        "consistency + Pattern-2 silent fallback."
    ),
)
class TestSignatureEntriesCarryCrossReference:
    def test_vsa_and_run_signatures_share_correlation_id(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_quadruple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")
        run_sig = _by_target(signatures, "run_ledger_root")

        # The pair MUST share a correlation identifier so a verifier
        # ingesting one entry can find the other.
        vsa_set_id = (
            vsa_sig.get("signature_set_id") or vsa_sig.get("co_signed_with") or vsa_sig.get("related_signature")
        )
        run_set_id = (
            run_sig.get("signature_set_id") or run_sig.get("co_signed_with") or run_sig.get("related_signature")
        )
        assert vsa_set_id, (
            f"Cross-artifact: VSA signature entry MUST carry a signature_set_id / co_signed_with. entry={vsa_sig!r}"
        )
        assert run_set_id, (
            "Cross-artifact: run-ledger-root signature entry MUST carry "
            f"a signature_set_id / co_signed_with. entry={run_sig!r}"
        )
        # Symmetric reference: either same set id, or each names the
        # other's statement_path.
        symmetric = (
            vsa_set_id == run_set_id
            or vsa_set_id == run_sig.get("statement_path")
            or run_set_id == vsa_sig.get("statement_path")
        )
        assert symmetric, (
            "Cross-artifact: the VSA + run-ledger-root signature entries "
            "MUST be symmetrically correlated (shared set id OR mutual "
            f"statement_path reference). vsa={vsa_set_id!r} "
            f"run={run_set_id!r}"
        )


# ---------------------------------------------------------------------------
# REAL BUG axis D -- dirty-tree disclosure across FOUR artifacts.
#
# Sister to W805-KKKKK axis B (CGA<->VSA pair) and W805-OOOOO axis D
# (3-artifact). Here the cosign signature entry is the FOURTH place
# the dirty-tree signal could live and doesn't. A consumer that ingests
# only ``slsa_l3.signatures[i]`` cannot audit clean-tree state at
# sign time -- the signature metadata is dirty-tree-blind.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-PPPPP axis D -- dirty-tree disclosure asymmetric across "
        "FOUR artifacts: the bundle envelope's bundle_meta.git block "
        "records the porcelain hash, but the SLSA VSA (W805-KKKKK axis "
        "B), the run-ledger-root statement (W805-OOOOO axis D), AND "
        "the cosign signature entries (W805-PPPPP, this axis) all drop "
        "the dirty-tree signal. A downstream consumer ingesting only "
        "``slsa_l3.signatures[i]`` cannot tell whether the tree was "
        "clean or dirty when cosign signed the statement -- and "
        "sign-time clean-tree state is precisely what an attestation "
        "verifier needs to audit. Fix template: thread git_dirty_hash "
        "from ChangeEvidence into ``_serialize_cosign_result`` so each "
        "signature entry carries the dirty-tree disclosure that was "
        "true at sign time. Family: cross-artifact consistency + "
        "Pattern-2 silent fallback."
    ),
)
class TestSignatureEntriesCarryDirtyTreeDisclosure:
    def test_dirty_signal_present_on_signature_entries(self, repo_with_bundle, cli_runner, monkeypatch):
        # Dirty the tree before emit so the bundle producer records a
        # porcelain hash (matches W805-OOOOO axis D setup).
        (repo_with_bundle / "a.py").write_text("def f():\n    return 2\n# uncommitted edit\n", encoding="utf-8")
        envelope, _vsa, _run, signatures = _emit_quadruple(cli_runner, repo_with_bundle, monkeypatch)

        # Setup invariant: the envelope MUST disclose dirty state
        # (the bundle producer is supposed to record it on
        # bundle_meta.git after an uncommitted edit).
        git_block = (envelope.get("bundle_meta") or {}).get("git") or {}
        env_dirty = (
            git_block.get("status_porcelain_hash") or git_block.get("is_dirty") or envelope.get("git_dirty_hash")
        )
        assert env_dirty, (
            "Setup invariant: bundle envelope should record some "
            f"dirty-tree signal after uncommitted edit; got git_block={git_block!r}"
        )

        # The signature entries MUST carry the same signal.
        for target in ("vsa", "run_ledger_root"):
            sig = _by_target(signatures, target)
            sig_dirty = (
                sig.get("git_dirty_hash") or sig.get("status_porcelain_hash") or sig.get("payload_git_dirty_hash")
            )
            assert sig_dirty, (
                f"Cross-artifact: {target!r} signature entry MUST carry "
                "the dirty-tree disclosure that was true at sign time. "
                f"entry={sig!r}"
            )


# ---------------------------------------------------------------------------
# POSITIVE pin -- the signature entry DOES carry ``statement_path``, so
# a consumer COULD back-fetch the source file. Not a drift axis -- just
# confirms the existing weak correlation that the fixes above strengthen.
# ---------------------------------------------------------------------------


class TestSignatureEntriesCarryStatementPath:
    """Confirms the existing weak correlation: each signature entry
    points back at its source statement via ``statement_path``. This
    is what consumers must rely on TODAY to learn anything about the
    signed payload -- the W805-PPPPP fixes would replace this
    back-fetch with self-describing metadata.
    """

    def test_each_entry_carries_back_pointer_to_source_statement(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_quadruple(cli_runner, repo_with_bundle, monkeypatch)
        for sig in signatures:
            stmt_path = sig.get("statement_path")
            assert stmt_path, f"signature entry missing statement_path: {sig}"
            # statement_path is a string POSIX-or-Windows path; just
            # confirm it's a non-empty string -- the W805-PPPPP probe
            # doesn't depend on the file actually existing on disk
            # (cosign was stubbed).
            assert isinstance(stmt_path, str) and stmt_path.strip(), sig
