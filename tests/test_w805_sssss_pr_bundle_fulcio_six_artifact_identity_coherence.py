"""W805-SSSSS -- 6-artifact identity-coherence pin for ``pr-bundle emit
--slsa-l3 --sign --keyless`` -- the **Fulcio short-lived X.509 cert**
as the sixth artifact in the supply-chain chain.

Hundred-and-twenty-second-in-batch W805 sweep. Extends the W805-RRRRR
"5-artifact cross-artifact consistency" axis (bundle envelope + SLSA
VSA + run-ledger-root statement + cosign signature triplet + Rekor
transparency-log entry) to the SIX-artifact case introduced by
Fulcio's keyless OIDC certificate.

When ``cosign sign-blob --keyless`` runs, Fulcio issues a short-lived
X.509 certificate (typically valid ~10 minutes) whose Subject
Alternative Name (SAN) extension carries:

* the OIDC **issuer** URL (e.g. ``https://token.actions.githubusercontent.com``)
* the **workflow identity** URI (e.g. ``https://github.com/owner/repo/.github/workflows/release.yml@refs/heads/main``)
* the cert validity window (``notBefore`` / ``notAfter`` timestamps)

This cert lands on disk at ``CosignResult.certificate_path`` via
cosign's ``--output-certificate`` flag. **roam writes the path and
walks away** -- the cert is never opened, the SAN is never parsed,
the OIDC issuer + workflow identity are never lifted onto
``CosignResult`` or the envelope.

The Fulcio cert is structurally the **Q1 actor-identity answer**
from the evidence-compiler 8-question framework: *who signed this?*
Today an evidence-packet consumer handed the cosign triplet (sig +
cert + bundle paths) cannot answer Q1 without (a) opening the cert
file from disk, (b) parsing the X.509 DER/PEM, (c) extracting the
SAN extension OID ``1.3.6.1.4.1.57264.1.1`` (Sigstore issuer),
(d) extracting the SAN URI value (workflow identity). The standard
sigstore consumption pattern works, but roam offers ZERO help.
Pattern-2 silent fallback at the actor-identity layer: the metadata
claims keyless signing succeeded without naming WHO signed it.

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **Producer inventory.** ``cosign sign-blob --keyless`` is invoked
   by ``src/roam/attest/cga.py:cosign_sign_statement``. On the
   keyless branch the argv appends ``--output-certificate
   <cert_path>`` (lines 571-575). Real cosign on the keyless path
   prints to stderr lines like::

       Retrieving signed certificate ...
       **Warning** Using payload from: <stmt>
       tlog entry created with index: 187452301

   And the cert file at ``--output-certificate`` is a PEM-encoded
   X.509 certificate whose SAN extension embeds the OIDC issuer +
   workflow identity.

2. **What roam parses.** Probe ``cosign_sign_statement`` (lines
   510-630) end-to-end. The cert path is built (line 559), passed
   to cosign as ``--output-certificate`` (line 575), checked for
   existence at line 626 (``cert_path.exists()``), and stamped on
   ``CosignResult.certificate_path`` -- and that's it. The cert
   file is NEVER opened. The SAN extension is NEVER parsed. The
   OIDC issuer + workflow identity + validity window are dropped.

3. **The ``CosignResult`` shape (live probe).** Per
   ``src/roam/attest/cga.py:480-491``:

       @dataclass
       class CosignResult:
           signed: bool
           statement_path: Path
           signature_path: Path | None = None
           certificate_path: Path | None = None
           bundle_path: Path | None = None
           skipped_reason: str = ""
           cosign_version: str = ""

   No ``oidc_issuer``. No ``workflow_identity``. No
   ``cert_not_before`` / ``cert_not_after``. No
   ``signature_set_id``. The Fulcio actor-identity axis is
   INVISIBLE to anything that consumes the roam emit-side metadata.

4. **What ``_serialize_cosign_result`` projects.** Per
   ``src/roam/attest/emit_vsa.py:70-98``: the projection mirrors
   ``CosignResult`` exactly. There is no Fulcio field to project
   even if it existed. The sixth-artifact gap is structural at the
   source (the dataclass) and propagates through to the envelope.

5. **Why this matters.** The Fulcio cert IS the answer to Q1
   ("who signed this?") in the evidence-compiler 8-question
   framework. A downstream verifier that wants to:

   * confirm the cert was issued by a trusted Fulcio root
     (sigstore.dev) and not a private Fulcio instance
   * confirm the OIDC issuer is what the policy expects
     (``https://token.actions.githubusercontent.com`` for GHA
     workflows, ``https://accounts.google.com`` for Google OIDC,
     etc.)
   * confirm the workflow identity matches an approved release
     pipeline (``.github/workflows/release.yml@refs/heads/main``,
     not ``.github/workflows/scratch.yml@refs/heads/dev``)
   * uplift ``actor_refs[]`` with a ``kind="ci_runner"`` entry
     SOURCED from the cert's workflow identity (W182 ActorRef
     vocabulary)
   * audit the cert validity window against the run-ledger
     ``started_at``/``ended_at`` for replay-attack analysis
     (cert expired before the run started => replayed signature)

   ...cannot do ANY of those things from roam's emit-side envelope
   today. Pattern-2 silent fallback at the actor-identity layer.

6. **Cosign availability.** The probe is STRUCTURAL, not functional.
   We don't need cosign to actually contact Fulcio; we just need the
   ``CosignResult`` dataclass and the ``_serialize_cosign_result``
   projection. Both are introspectable from Python source without
   ever shelling out to ``cosign``. The test ALSO drives the
   ``--sign --keyless`` emit path with a stubbed cosign (matching
   the W805-PPPPP/RRRRR fixture) so we can assert the projected
   envelope entry shape -- but the dataclass-level assertions stand
   alone.

W978 axis-distinctness
======================

W805-SSSSS is **structurally distinct** from its W805 siblings:

* **W805-KKKKK** (CGA<->sibling-VSA): TWO artifacts, both JSON
  attestations, written by ``cga emit --also-vsa``. No signing.
* **W805-OOOOO** (3-artifact): THREE JSON artifacts, written by
  ``pr-bundle emit --slsa-l3``. No signing.
* **W805-PPPPP** (4-artifact): FOUR artifacts of THREE kinds
  (envelope JSON + two in-toto statements + cosign signature
  triplet). The fourth artifact kind is roam-OWNED at the metadata
  layer.
* **W805-RRRRR** (5-artifact): FIVE artifacts of FOUR kinds -- the
  four above plus the Rekor transparency-log entry. The fifth
  artifact lives OFF the local machine (on rekor.sigstore.dev).
* **W805-SSSSS** (this, 6-artifact): SIX artifacts of FIVE kinds --
  the five above plus the Fulcio short-lived X.509 cert. The sixth
  artifact lives on disk (cosign downloads it) but its
  SEMANTICALLY-INTERESTING content (the SAN extension carrying
  OIDC issuer + workflow identity + validity window) is never
  lifted into roam's emit-side metadata. The bytes are on disk;
  the meaning is missing. The Fulcio cert is the structural answer
  to Q1 (actor identity); roam's envelope today cannot answer Q1
  without re-parsing the X.509.

Pinned via ``xfail(strict=True)`` on each drift axis; a future fix
flips xpass -> failure -> unwrap and seal.

W907 verify-cycle check
=======================

``grep -i 'avoid.*cycle|circular import|kept local|would create a
cycle|duplicated.*here'`` over ``src/roam/attest/cga.py`` +
``src/roam/attest/emit_vsa.py`` yields ZERO false cycle hedges.
``emit_vsa.py`` uses ``from roam.attest import cga as _cga`` (module
alias, NOT a false cycle hedge -- the inline docstring at lines
53-58 explicitly names monkeypatch compatibility as the reason).
W907 clean.

Run isolation
=============

    python -m pytest tests/test_w805_sssss_pr_bundle_fulcio_six_artifact_identity_coherence.py -x -n 0

Sister parity
=============

    python -m pytest \
        tests/test_w805_rrrrr_pr_bundle_rekor_five_artifact_identity_coherence.py \
        tests/test_w805_ppppp_pr_bundle_cosign_four_artifact_identity_coherence.py \
        tests/test_w805_ooooo_pr_bundle_slsa_l3_three_artifact_identity_coherence.py \
        tests/test_w805_kkkkk_cga_vsa_sibling_subject_consistency.py \
        -x -n 0
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
_REFS_SPEC = importlib.util.find_spec("roam.evidence.refs")


def test_substrate_modules_present():
    """W978/W907 gate: pr_bundle + emit_vsa + vsa + cga + refs import."""
    if _CMD_PR_BUNDLE_SPEC is None:
        pytest.skip("roam.commands.cmd_pr_bundle not installed")
    assert _EMIT_VSA_SPEC is not None, "roam.attest.emit_vsa missing"
    assert _VSA_SPEC is not None, "roam.attest.vsa missing"
    assert _CGA_SPEC is not None, "roam.attest.cga missing"
    assert _REFS_SPEC is not None, "roam.evidence.refs (ActorRef) missing"


def test_cosign_sign_blob_keyless_path_emits_certificate():
    """W978: confirm ``--output-certificate <path>`` lands on the argv
    on the keyless branch BEFORE pinning any cert-SAN parsing drift on it.

    If the keyless branch did NOT request the cert (e.g. the impl
    relied on the bundle file alone), the Fulcio-cert probe would be
    N/A and this file would skip. The presence-check makes that skip
    explicit rather than producing misleading xfails on a non-existent
    surface.
    """
    cga_source = Path(_CGA_SPEC.origin).read_text(encoding="utf-8")
    assert '"sign-blob"' in cga_source or "'sign-blob'" in cga_source, (
        "cga.py does not appear to shell out to ``cosign sign-blob`` -- the "
        "W805-SSSSS Fulcio probe is N/A if the signing path is not keyless"
    )
    assert "--output-certificate" in cga_source, (
        "cga.py keyless branch missing ``--output-certificate`` -- a Fulcio "
        "X.509 cert is only landed on disk when cosign is asked to write one"
    )
    assert "keyless" in cga_source, (
        "cga.py has no ``keyless`` codepath -- Fulcio cert issuance is "
        "keyless-only (offline keypair signing involves no Fulcio interaction)"
    )


# ---------------------------------------------------------------------------
# Fixtures (modelled on W805-RRRRR)
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
    """Initialise a tiny git repo + hand-craft a minimal pr-bundle.

    Byte-identical to the W805-RRRRR fixture so the SIX-artifact probe
    extends the FIVE-artifact probe by reading Fulcio metadata off the
    same ``--slsa-l3 --sign --keyless`` invocation.
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


def _emit_sextuple(runner, repo: Path, monkeypatch, *, run_id: str = "r_sssss_demo"):
    """Drive ``pr-bundle emit --slsa-l3 --sign --keyless`` with a stubbed
    cosign that simulates a REAL Fulcio keyless flow outcome.

    The stub returns a fake CosignResult whose ``cosign_version`` carries
    a known-good string and whose certificate_path points at a path the
    test could (in a future fix) open + parse. The W805-SSSSS axes pin
    metadata that DOES NOT exist on the projected envelope today
    (oidc_issuer, workflow_identity, cert_not_before/after,
    signature_set_id) -- so the stub doesn't need to set those fields;
    the projection layer wouldn't carry them through even if it did.
    This is structural absence at the dataclass and the serializer.

    Returns ``(envelope, vsa, run_root, signatures)``.
    """
    from roam.attest import cga as cga_mod

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

    class _StubMeta:
        run_id = "r_sssss_demo"
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
    for sig in signatures:
        if sig.get("target") == target:
            return sig
    raise AssertionError(f"no signature entry for target={target!r}; got {signatures!r}")


# ---------------------------------------------------------------------------
# STRUCTURAL pin -- CosignResult has ZERO Fulcio cert-SAN fields.
#
# Pure source-level introspection. No subprocess, no fixture. This is
# the cleanest possible expression of the W805-SSSSS family gap: the
# dataclass that represents the outcome of a keyless cosign sign-blob
# call carries NONE of the actor-identity fields the cert actually
# embeds.
# ---------------------------------------------------------------------------


class TestCosignResultDataclassFulcioFieldsExist:
    """Pure-Python introspection: does ``CosignResult`` declare ANY
    field that would carry Fulcio cert-SAN actor-identity metadata?

    No subprocess. No fixture. Pure structural pin.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-SSSSS structural pin -- ``CosignResult`` (the "
            "dataclass in ``src/roam/attest/cga.py:480-491``) carries "
            "NO Fulcio cert-SAN actor-identity metadata. Fields are: "
            "signed, statement_path, signature_path, certificate_path, "
            "bundle_path, skipped_reason, cosign_version. NONE of "
            "oidc_issuer / workflow_identity / cert_not_before / "
            "cert_not_after / cert_subject is declared. Real "
            "``cosign sign-blob --keyless`` writes a Fulcio X.509 cert "
            "to ``--output-certificate <cert_path>`` whose SAN "
            "extension carries the OIDC issuer URL + workflow identity "
            "URI + validity window; roam captures NONE of these. Fix "
            "template: add ``oidc_issuer: str | None = None``, "
            "``workflow_identity: str | None = None``, ``cert_not_before: "
            "str | None = None``, ``cert_not_after: str | None = None`` "
            "to ``CosignResult``, parse them from the cert file post-"
            "success (X.509 SAN extension OID 1.3.6.1.4.1.57264.1.1 + "
            "validity dates) in ``cosign_sign_statement``. Family: "
            "cross-artifact consistency + Pattern-2 silent fallback at "
            "the actor-identity layer (Q1 from the evidence-compiler "
            "8-question framework)."
        ),
    )
    def test_cosign_result_declares_fulcio_actor_identity_field(self):
        from roam.attest.cga import CosignResult

        fields = set(CosignResult.__dataclass_fields__.keys())
        # At least one Fulcio-naming field MUST exist.
        fulcio_fields = {
            "oidc_issuer",
            "workflow_identity",
            "cert_not_before",
            "cert_not_after",
            "cert_subject",
            "certificate_oidc_issuer",
            "certificate_identity",
            "fulcio_oidc_issuer",
            "fulcio_workflow_identity",
        }
        assert fields & fulcio_fields, (
            "CosignResult MUST declare at least one Fulcio-naming "
            "field so the keyless cert's OIDC issuer + workflow "
            "identity is captured at the dataclass layer. Got "
            f"fields={sorted(fields)!r}"
        )


# ---------------------------------------------------------------------------
# AXIS A -- envelope-side: ``slsa_l3.signatures[i]`` has no oidc_issuer
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-SSSSS axis A (envelope projection) -- the signature "
        "entries in ``slsa_l3.signatures[]`` carry NO ``oidc_issuer`` "
        "field. ``_serialize_cosign_result`` "
        "(``src/roam/attest/emit_vsa.py:70-98``) projects "
        "{signed, statement_path, signature_path, certificate_path, "
        "bundle_path, skipped_reason, cosign_version} -- the union of "
        "those is the entire schema. The OIDC issuer URL is the "
        "PRIMARY trust anchor for a Fulcio-issued cert (a verifier "
        "must confirm it matches policy: e.g. "
        "``https://token.actions.githubusercontent.com`` for GHA, "
        "``https://accounts.google.com`` for Google OIDC). Without it, "
        "a downstream verifier cannot confirm the cert was issued from "
        "an approved identity provider without re-parsing the .cert "
        "file. Fix template: thread a new "
        "``CosignResult.oidc_issuer`` field through "
        "``_serialize_cosign_result``. Family: cross-artifact "
        "consistency at the actor-identity layer (Q1)."
    ),
)
class TestSignatureEntryCarriesOidcIssuer:
    def test_vsa_signature_entry_carries_oidc_issuer(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_sextuple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")
        issuer = (
            vsa_sig.get("oidc_issuer")
            or vsa_sig.get("certificate_oidc_issuer")
            or (vsa_sig.get("fulcio") or {}).get("oidc_issuer")
            or (vsa_sig.get("cert") or {}).get("oidc_issuer")
        )
        assert isinstance(issuer, str) and issuer, (
            "VSA signature entry MUST carry oidc_issuer so a downstream "
            "verifier can confirm the cert was issued from an approved "
            f"OIDC identity provider. entry={vsa_sig!r}"
        )
        # An OIDC issuer is always a URL (RFC 8414 / OpenID Connect Discovery).
        assert issuer.startswith("https://"), f"oidc_issuer MUST be an https:// URL; got {issuer!r}"


# ---------------------------------------------------------------------------
# AXIS B -- envelope-side: ``slsa_l3.signatures[i]`` has no workflow_identity
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-SSSSS axis B -- the signature entries in "
        "``slsa_l3.signatures[]`` carry NO ``workflow_identity`` "
        "field. The Fulcio SAN extension URI value names the "
        "specific workflow that requested the keyless signature "
        "(e.g. "
        "``https://github.com/owner/repo/.github/workflows/release.yml@refs/heads/main``). "
        "This is the GRANULAR actor identity -- not just 'a GHA "
        "workflow' (that's oidc_issuer) but 'THIS specific workflow "
        "on THIS branch'. A verifier comparing against an allowlist "
        "of approved release pipelines NEEDS this value. Fix template: "
        "lift the SAN URI from the cert (X.509 extension OID "
        "1.3.6.1.4.1.57264.1.1 or the standard SAN URIs), expose "
        "on ``CosignResult.workflow_identity`` + "
        "``_serialize_cosign_result``. Family: cross-artifact "
        "consistency at the actor-identity layer."
    ),
)
class TestSignatureEntryCarriesWorkflowIdentity:
    def test_vsa_signature_entry_carries_workflow_identity(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_sextuple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")
        identity = (
            vsa_sig.get("workflow_identity")
            or vsa_sig.get("certificate_identity")
            or (vsa_sig.get("fulcio") or {}).get("workflow_identity")
            or (vsa_sig.get("cert") or {}).get("subject_alternative_name")
            or (vsa_sig.get("cert") or {}).get("workflow_identity")
        )
        assert isinstance(identity, str) and identity, (
            "VSA signature entry MUST carry workflow_identity so a "
            "verifier can match against an allowlist of approved "
            f"release pipelines. entry={vsa_sig!r}"
        )


# ---------------------------------------------------------------------------
# AXIS C -- envelope-side: no cert validity window (cert_not_before/after)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-SSSSS axis C -- the signature entries in "
        "``slsa_l3.signatures[]`` carry NO ``cert_not_before`` / "
        "``cert_not_after`` validity-window timestamps. Fulcio "
        "certs are SHORT-LIVED (typically ~10 minutes) by design "
        "-- the cert proves identity at the moment of signing AND "
        "ONLY at that moment. The validity window is critical for "
        "replay-attack analysis: a signature with a cert valid "
        "2025-01-01T00:00:00..00:10:00 cannot have signed a "
        "ChangeEvidence whose run started_at is 2025-02-01. "
        "Without these timestamps on the envelope, a verifier "
        "comparing against ``ChangeEvidence.edits_started_at`` / "
        "``ended_at`` (W210 time-aware fields) must re-parse the "
        "cert. Fix template: lift X.509 notBefore + notAfter via "
        "``cryptography.x509.load_pem_x509_certificate`` (already "
        "an indirect dep via sigstore-python), expose on "
        "``CosignResult.cert_not_before`` / ``cert_not_after`` + "
        "``_serialize_cosign_result``. Family: cross-artifact "
        "consistency + replay-attack analysis."
    ),
)
class TestSignatureEntryCarriesCertValidityWindow:
    def test_vsa_signature_entry_carries_cert_validity_window(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_sextuple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")
        not_before = (
            vsa_sig.get("cert_not_before")
            or (vsa_sig.get("cert") or {}).get("not_before")
            or (vsa_sig.get("fulcio") or {}).get("not_before")
        )
        not_after = (
            vsa_sig.get("cert_not_after")
            or (vsa_sig.get("cert") or {}).get("not_after")
            or (vsa_sig.get("fulcio") or {}).get("not_after")
        )
        assert not_before, (
            "VSA signature entry MUST carry cert_not_before so a "
            "verifier can analyse replay-attack risk against the "
            f"run-ledger timestamps. entry={vsa_sig!r}"
        )
        assert not_after, (
            "VSA signature entry MUST carry cert_not_after so a "
            "verifier can confirm the cert was valid at signing "
            f"time. entry={vsa_sig!r}"
        )


# ---------------------------------------------------------------------------
# AXIS D -- envelope.actor_refs[] has NO Fulcio-sourced ci_runner entry
#
# This is the structural-link axis between the supply-chain layer and
# the evidence-compiler 8-question framework. ``actor_refs[]`` (W182
# ActorRef vocabulary, ACTOR_KINDS includes ``ci_runner`` + ``agent``)
# is the Q1 (who acted?) producer surface; the Fulcio cert IS the
# answer to Q1 on the supply-chain side. The two should connect.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-SSSSS axis D (the structural-link axis) -- the "
        "envelope's ``actor_refs[]`` array (W182, ACTOR_KINDS "
        "includes ``ci_runner`` + ``agent``) carries NO entry "
        "SOURCED from the Fulcio cert's workflow identity. The "
        "Fulcio cert IS the Q1 actor-identity answer on the "
        "supply-chain side; ``actor_refs[]`` IS the Q1 actor-"
        "identity producer surface in the evidence-compiler 8-"
        "question framework. The two should connect at emit time: "
        "when ``pr-bundle emit --slsa-l3 --sign --keyless`` "
        "succeeds, the bundle envelope SHOULD have an ``actor_refs[]`` "
        "entry with ``actor_kind=ci_runner`` (or ``agent``) and "
        "``actor_id`` = the workflow identity URI from the cert. "
        "Today: the cert is written to disk, the SAN is never "
        "parsed, ``actor_refs[]`` is never uplifted. The "
        "supply-chain and evidence-compiler axes don't talk. Fix "
        "template: in ``_emit_slsa_l3_attestations`` post-sign, "
        "if ``cresult.workflow_identity`` is set, push an ActorRef "
        "onto the envelope's actor_refs[] with "
        "``provenance_source='producer_envelope'`` and "
        "``trust_tier='verified_ci'``. Family: cross-artifact "
        "consistency + Q1 producer/consumer coupling."
    ),
)
class TestEnvelopeActorRefsUpliftedFromFulcioCert:
    def test_actor_refs_includes_fulcio_sourced_ci_runner(self, repo_with_bundle, cli_runner, monkeypatch):
        envelope, _vsa, _run, _signatures = _emit_sextuple(cli_runner, repo_with_bundle, monkeypatch)
        # The top-level envelope OR the slsa_l3 sub-envelope may carry
        # actor_refs[]; accept either. Accept also a nested location
        # under ``evidence`` (collector output).
        candidates = []
        candidates.extend(envelope.get("actor_refs") or [])
        candidates.extend((envelope.get("slsa_l3") or {}).get("actor_refs") or [])
        candidates.extend((envelope.get("evidence") or {}).get("actor_refs") or [])
        fulcio_sourced = [
            a
            for a in candidates
            if (
                a.get("actor_kind") in ("ci_runner", "agent")
                and (
                    a.get("provenance_source") in ("producer_envelope", "mcp_receipt", "ci_env_var")
                    or "fulcio" in (a.get("source") or "").lower()
                    or "workflow" in (a.get("actor_id") or "").lower()
                )
            )
        ]
        assert fulcio_sourced, (
            "actor_refs[] MUST include a Fulcio-cert-sourced entry "
            "with actor_kind=ci_runner|agent so the supply-chain "
            "layer's Q1 answer flows into the evidence-compiler's "
            f"Q1 surface. candidates={candidates!r}"
        )


# ---------------------------------------------------------------------------
# AXIS E -- family closer: ``signature_set_id`` unifying all 6 artifacts.
#
# The W805-RRRRR sister test recommended this as the "cleanest fix":
# a single ``signature_set_id`` (sourced from ChangeEvidence.content_hash)
# threaded uniformly from the dataclass through every projection. Each
# signature entry should share this id so a verifier can correlate ALL
# six artifacts (envelope, VSA, run-ledger-root, cosign triplet, Rekor
# entry, Fulcio cert) back to a single evidence packet.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-SSSSS axis E (family closer) -- the signature entries "
        "in ``slsa_l3.signatures[]`` carry NO ``signature_set_id`` "
        "field. W805-RRRRR's structural recommendation: a single "
        "``signature_set_id`` (sourced from "
        "``ChangeEvidence.content_hash``) threaded uniformly from "
        "the dataclass through every projection. This is the natural "
        "structural cap on the W805-K/O/P/R/S 6-artifact family: a "
        "verifier handed any ONE of the six artifacts should be "
        "able to discover the other five via a shared id. Today: "
        "each artifact carries its own identity (envelope hash, VSA "
        "subject digest, run-ledger HMAC tip, cosign signature "
        "bytes, [absent] Rekor log index, [absent] Fulcio cert "
        "fingerprint) and NO SHARED CORRELATION ID. Fix template: "
        "stamp ``signature_set_id = change_evidence.content_hash`` "
        "on EVERY signature entry at emit time; mirror on the "
        "envelope's top-level slsa_l3 dict; downstream verifiers "
        "use it as the join key across all six artifacts. Family: "
        "cross-artifact consistency carried through SIX artifacts -- "
        "the family-closer pin."
    ),
)
class TestSignatureEntriesShareSignatureSetId:
    def test_both_signature_entries_share_signature_set_id(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_sextuple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")
        run_sig = _by_target(signatures, "run_ledger_root")

        vsa_set = vsa_sig.get("signature_set_id") or vsa_sig.get("evidence_id") or vsa_sig.get("content_hash")
        run_set = run_sig.get("signature_set_id") or run_sig.get("evidence_id") or run_sig.get("content_hash")
        assert vsa_set, (
            "VSA signature entry MUST carry signature_set_id so a "
            "verifier can correlate it with the run-ledger-root "
            f"signature + the other 4 artifacts. entry={vsa_sig!r}"
        )
        assert run_set, f"run-ledger-root signature entry MUST carry signature_set_id. entry={run_sig!r}"
        assert vsa_set == run_set, (
            f"Both signature entries MUST share the same signature_set_id. vsa={vsa_set!r} run={run_set!r}"
        )


# ---------------------------------------------------------------------------
# POSITIVE pins -- locks in the existing ground the W805-SSSSS fixes
# would build on top of.
#
# These are NOT drift axes. They confirm the existing positive ground:
# certificate_path is captured, the keyless argv requests it, ACTOR_KINDS
# includes ci_runner/agent (the vocab the W805-SSSSS axis D fix would
# emit into).
# ---------------------------------------------------------------------------


class TestExistingPositiveGround:
    """The 5-artifact pre-conditions for the W805-SSSSS Fulcio probe."""

    def test_signatures_list_present_with_two_targets(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_sextuple(cli_runner, repo_with_bundle, monkeypatch)
        targets = sorted(sig.get("target") for sig in signatures)
        assert targets == ["run_ledger_root", "vsa"], f"Expected both signature targets present; got {targets!r}"

    def test_each_signature_entry_points_to_local_certificate_file(self, repo_with_bundle, cli_runner, monkeypatch):
        """Today's weak Fulcio-adjacent correlation: each signature
        entry carries ``certificate_path``, and the .cert file
        (when cosign actually runs) contains the Fulcio cert with
        SAN extension carrying OIDC issuer + workflow identity. The
        W805-SSSSS fixes would lift the SAN into roam-side metadata
        so consumers don't have to open the .cert themselves.
        """
        _env, _vsa, _run, signatures = _emit_sextuple(cli_runner, repo_with_bundle, monkeypatch)
        for sig in signatures:
            cp = sig.get("certificate_path")
            assert isinstance(cp, str) and cp.strip(), f"signature entry missing certificate_path: {sig!r}"
            # Sanity: the path ends in .cert (cosign convention).
            assert cp.endswith(".cert"), f"certificate_path should end in .cert: {cp!r}"

    def test_actor_kinds_vocab_includes_ci_runner_and_agent(self):
        """The ACTOR_KINDS vocabulary already includes the kinds the
        W805-SSSSS axis D fix would emit into. This positive pin
        locks in the producer-surface availability; a regression
        that dropped ``ci_runner`` from ACTOR_KINDS would break the
        Fulcio-uplift fix path.
        """
        from roam.evidence._vocabulary import ACTOR_KINDS

        assert "ci_runner" in ACTOR_KINDS, (
            "ACTOR_KINDS MUST include 'ci_runner' so a Fulcio-cert "
            "uplift can land a typed ActorRef. "
            f"Got ACTOR_KINDS={sorted(ACTOR_KINDS)!r}"
        )
        assert "agent" in ACTOR_KINDS, (
            "ACTOR_KINDS MUST include 'agent' so a Fulcio-cert "
            "uplift can land a typed ActorRef. "
            f"Got ACTOR_KINDS={sorted(ACTOR_KINDS)!r}"
        )


# ---------------------------------------------------------------------------
# STRUCTURAL counter-pin -- the cosign argv does NOT request a
# private Fulcio (``--fulcio-url``) override.
#
# Cosign defaults to ``https://fulcio.sigstore.dev``; this is fine for
# the public path. Pinning the default keeps a future "switch to a
# private Fulcio" change visible. Not a drift today -- positive pin.
# ---------------------------------------------------------------------------


class TestCosignArgvUsesDefaultFulcio:
    def test_no_fulcio_url_override_in_argv(self):
        """Locks in the public-Fulcio default. A change to private
        Fulcio would need its own dedicated review."""
        cga_source = Path(_CGA_SPEC.origin).read_text(encoding="utf-8")
        # The override flag would be ``--fulcio-url`` per cosign docs.
        assert "--fulcio-url" not in cga_source, (
            "cga.py argv must NOT pass --fulcio-url unless a private "
            "Fulcio switch has been explicitly reviewed (env-var only, "
            "no hardcoded private endpoint)"
        )
