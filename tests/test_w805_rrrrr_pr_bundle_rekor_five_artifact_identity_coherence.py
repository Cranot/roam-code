"""W805-RRRRR -- 5-artifact identity-coherence pin for ``pr-bundle emit
--slsa-l3 --sign --keyless`` -- the **Rekor transparency-log entry** as
the fifth artifact in the supply-chain chain.

Hundred-and-twenty-first-in-batch W805 sweep. Extends the W805-PPPPP
"4-artifact cross-artifact consistency" axis (bundle envelope + SLSA VSA
+ run-ledger-root statement + cosign signature triplet) to the FIVE-artifact
case introduced by cosign's keyless transparency-log upload.

When ``cosign sign-blob --keyless`` runs, it does NOT merely produce a
``.sig`` / ``.cert`` / ``.bundle`` triplet on disk -- it also uploads the
signature to the **Rekor public transparency log** and receives back a
log entry referenced by a UUID + log-index pair. That Rekor record is
the FIFTH artifact in the supply-chain chain: it lives off the local
machine (rekor.sigstore.dev), it is queryable by any downstream verifier
as proof of inclusion in a tamper-evident append-only log, and it
carries its own ``subjectHash`` field that should match the underlying
statement's payload digest.

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **Producer inventory.** ``cosign sign-blob --keyless`` is invoked by
   ``src/roam/attest/cga.py:cosign_sign_statement`` with argv:

       cosign sign-blob --yes <stmt> --output-signature <sig> --bundle <bundle> --output-certificate <cert>

   Real cosign on the keyless path prints to stderr lines like::

       Retrieving signed certificate ...
       tlog entry created with index: 187452301
       Bundle wrote in the file <bundle>

   And the ``.bundle`` file itself is a JSON document with a
   ``rekorBundle.Payload.logIndex`` + ``rekorBundle.Payload.body``
   field carrying the base64-encoded Rekor entry.

2. **What roam parses.** Probe ``src/roam/attest/cga.py:cosign_sign_statement``
   (lines 510-630) end-to-end:

       proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
       ...
       if proc.returncode != 0: <return skipped>
       sig_present = sig_path.exists()
       bundle_present = bundle_path.exists()
       ...
       return CosignResult(signed=True, ..., bundle_path=..., ...)

   The captured ``proc.stdout`` / ``proc.stderr`` are READ ONLY for the
   error path. On success, NEITHER stdout NOR stderr is parsed -- the
   ``tlog entry created with index: N`` and ``tlog entry uuid: <hex>``
   lines are dropped on the floor. The ``.bundle`` file is referenced
   by path but never opened: the ``rekorBundle.Payload.logIndex`` is
   not lifted into ``CosignResult``.

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

   No ``rekor_log_index``. No ``rekor_uuid``. No ``rekor_entry_url``.
   No ``rekor_subject_hash``. No ``rekor_integrated_time``. The Rekor
   transparency-log integration is INVISIBLE to anything that consumes
   the roam emit-side metadata.

4. **What ``_serialize_cosign_result`` projects.** Per
   ``src/roam/attest/emit_vsa.py:70-98``: the projection mirrors
   ``CosignResult`` exactly (signed/statement_path/bundle_path/
   signature_path/certificate_path/skipped_reason/cosign_version) plus an
   optional ``target`` discriminator. There is no Rekor field to project
   even if it existed. The fifth-artifact gap is structural at the
   source (the dataclass) and propagates through to the envelope.

5. **Why this matters.** The Rekor entry IS the tamper-evident anchor
   the SLSA L3 "transparency log" requirement points to. A downstream
   verifier that wants to:

   * confirm a roam-emitted signature is on Rekor at log-index N
   * fetch the Rekor entry body and verify its ``subjectHash`` matches
     the VSA's ``subject[0].digest.sha256``
   * walk from one signature's Rekor entry to its co-signed partner's
     Rekor entry (the W805-OOOOO/PPPPP correlation axis carried into
     transparency-log space)
   * audit clean-tree state at the moment cosign uploaded to Rekor

   ...cannot do ANY of those things from roam's emit-side envelope
   today. The verifier would have to (a) open the ``.bundle`` file from
   disk, (b) base64-decode the inner Rekor payload, and (c) parse it
   themselves -- the standard sigstore consumption pattern works, but
   roam offers ZERO help. Pattern-2 silent fallback at the
   transparency-log layer: the metadata claims keyless signing
   succeeded without naming WHERE on the public log it landed.

6. **Cosign availability.** The probe is STRUCTURAL, not functional.
   We don't need cosign to actually upload to Rekor; we just need the
   ``CosignResult`` dataclass and the ``_serialize_cosign_result``
   projection. Both are introspectable from Python source without ever
   shelling out to ``cosign``. The test ALSO drives the
   ``--sign --keyless`` emit path with a stubbed cosign (matching the
   W805-PPPPP fixture) so we can assert the projected envelope entry
   shape -- but the dataclass-level assertions stand alone.

W978 axis-distinctness
======================

W805-RRRRR is **structurally distinct** from its W805 siblings:

* **W805-KKKKK** (CGA<->sibling-VSA): TWO artifacts, both JSON
  attestations, written by ``cga emit --also-vsa``. No signing.
* **W805-OOOOO** (3-artifact): THREE JSON artifacts, written by
  ``pr-bundle emit --slsa-l3``. No signing.
* **W805-PPPPP** (4-artifact): FOUR artifacts of THREE kinds (envelope
  JSON + two in-toto statements + cosign signature triplet). The
  fourth artifact kind is roam-OWNED at the metadata layer.
* **W805-RRRRR** (this, 5-artifact): FIVE artifacts of FOUR kinds --
  the four above plus the Rekor transparency-log entry. The fifth
  artifact lives OFF the local machine (on rekor.sigstore.dev), is
  produced as a side-effect of cosign's keyless flow, and is referenced
  by a UUID + log-index pair that roam currently DOES NOT capture.
  The bytes on Rekor are sigstore-defined; the metadata that names
  them on the roam side is what's missing.

Pinned via ``xfail(strict=True)`` on each drift axis; a future fix
flips xpass -> failure -> unwrap and seal.

W907 verify-cycle check
=======================

``grep -i 'avoid.*cycle|circular import|kept local|would create a cycle|
duplicated.*here'`` over ``src/roam/attest/cga.py`` +
``src/roam/attest/emit_vsa.py`` yields ZERO false cycle hedges.
``emit_vsa.py`` uses ``from roam.attest import cga as _cga`` (module
alias, NOT a false cycle hedge -- the inline docstring at lines 53-58
explicitly names monkeypatch compatibility as the reason). W907 clean.

Run isolation
=============

    python -m pytest tests/test_w805_rrrrr_pr_bundle_rekor_five_artifact_identity_coherence.py -x -n 0

Sister parity
=============

    python -m pytest tests/test_w805_ppppp_pr_bundle_cosign_four_artifact_identity_coherence.py \
        tests/test_w805_ooooo_pr_bundle_slsa_l3_three_artifact_identity_coherence.py \
        tests/test_w805_kkkkk_cga_vsa_sibling_consistency.py \
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


def test_substrate_modules_present():
    """W978/W907 gate: pr_bundle + emit_vsa + vsa + cga import."""
    if _CMD_PR_BUNDLE_SPEC is None:
        pytest.skip("roam.commands.cmd_pr_bundle not installed")
    assert _EMIT_VSA_SPEC is not None, "roam.attest.emit_vsa missing"
    assert _VSA_SPEC is not None, "roam.attest.vsa missing"
    assert _CGA_SPEC is not None, "roam.attest.cga missing"


def test_cosign_sign_blob_keyless_path_wired():
    """W978: confirm ``cosign sign-blob --yes ... --bundle ...`` is the
    invocation shape before pinning any Rekor metadata drift on it.

    If the codepath were file-based detached-key signing only (no Rekor
    upload), the entire W805-RRRRR axis would be N/A and this file
    would skip. The presence-check makes that skip explicit rather than
    producing misleading xfails on a non-existent surface.
    """
    cga_source = Path(_CGA_SPEC.origin).read_text(encoding="utf-8")
    # The wrapper builds the cosign argv and includes ``sign-blob`` +
    # ``--bundle``; on the keyless branch it additionally appends
    # ``--output-certificate``. Both are required for a Rekor upload to
    # happen at all (cosign emits the Rekor entry as part of the keyless
    # sign-blob flow -- see https://docs.sigstore.dev/cosign/key_management/signing_with_blobs/).
    assert '"sign-blob"' in cga_source or "'sign-blob'" in cga_source, (
        "cga.py does not appear to shell out to ``cosign sign-blob`` -- the "
        "W805-RRRRR Rekor probe is N/A if the signing path is detached-key only"
    )
    assert '"--bundle"' in cga_source or "'--bundle'" in cga_source, (
        "cga.py ``cosign sign-blob`` argv missing ``--bundle`` -- a Rekor "
        "transparency-log entry is only written when cosign produces a "
        "sigstore bundle"
    )
    assert "keyless" in cga_source, (
        "cga.py has no ``keyless`` codepath -- Rekor uploads are keyless-only "
        "(detached key signing produces no transparency-log entry)"
    )


# ---------------------------------------------------------------------------
# Fixtures (modelled on W805-PPPPP)
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

    Byte-identical to the W805-PPPPP fixture so the FIVE-artifact probe
    extends the FOUR-artifact probe by reading Rekor metadata off the
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


def _emit_quintuple(runner, repo: Path, monkeypatch, *, run_id: str = "r_rrrrr_demo"):
    """Drive ``pr-bundle emit --slsa-l3 --sign --keyless`` with a stubbed
    cosign that simulates a REAL Rekor upload outcome.

    The stub returns a fake CosignResult whose ``cosign_version`` carries
    a known-good string. The W805-RRRRR axes pin metadata that DOES NOT
    exist on the projected envelope today (rekor_log_index, rekor_uuid,
    rekor_entry_url, rekor_subject_hash) -- so the stub doesn't need to
    set those fields; the projection layer wouldn't carry them through
    even if it did. This is structural absence at the dataclass and
    the serializer.

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
        run_id = "r_rrrrr_demo"
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
# STRUCTURAL pin -- CosignResult has ZERO Rekor fields.
#
# Pure source-level introspection. No subprocess, no fixture. This is
# the cleanest possible expression of the W805-RRRRR family gap: the
# dataclass that represents the outcome of a keyless cosign sign-blob
# call carries NONE of the fields cosign actually emits.
# ---------------------------------------------------------------------------


class TestCosignResultDataclassRekorFieldsExist:
    """Pure-Python introspection: does ``CosignResult`` declare ANY
    field that would carry Rekor transparency-log metadata?

    No subprocess. No fixture. Pure structural pin.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-RRRRR axis A -- ``CosignResult`` (the dataclass in "
            "``src/roam/attest/cga.py:480-491``) carries NO Rekor "
            "transparency-log metadata. Fields are: signed, "
            "statement_path, signature_path, certificate_path, "
            "bundle_path, skipped_reason, cosign_version. NONE of "
            "rekor_log_index / rekor_uuid / rekor_entry_url / "
            "rekor_subject_hash / rekor_integrated_time is declared. "
            "Real ``cosign sign-blob --keyless`` writes ``tlog entry "
            "created with index: N`` + ``tlog entry uuid: <hex>`` to "
            "stderr AND embeds ``rekorBundle.Payload.{logIndex,body}`` "
            "in the .bundle file; roam captures NEITHER. Fix template: "
            "add ``rekor_log_index: int | None = None``, ``rekor_uuid: "
            "str | None = None``, ``rekor_entry_url: str | None = "
            "None`` to ``CosignResult``, parse them from "
            "``proc.stderr`` (regex on the tlog lines) + the .bundle "
            "JSON in ``cosign_sign_statement`` post-success. Family: "
            "cross-artifact consistency + Pattern-2 silent fallback at "
            "the transparency-log layer."
        ),
    )
    def test_cosign_result_declares_rekor_log_index_field(self):
        from roam.attest.cga import CosignResult

        fields = set(CosignResult.__dataclass_fields__.keys())
        # At least one Rekor-naming field MUST exist.
        rekor_fields = {
            "rekor_log_index",
            "rekor_uuid",
            "rekor_entry_url",
            "rekor_subject_hash",
            "rekor_integrated_time",
            "log_index",
            "tlog_uuid",
        }
        assert fields & rekor_fields, (
            "CosignResult MUST declare at least one Rekor-naming field "
            "so the keyless transparency-log entry is captured at the "
            f"dataclass layer. Got fields={sorted(fields)!r}"
        )


# ---------------------------------------------------------------------------
# AXIS A -- envelope-side: ``slsa_l3.signatures[i]`` has no rekor_log_index
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RRRRR axis A (envelope projection) -- the signature "
        "entries in ``slsa_l3.signatures[]`` carry NO "
        "``rekor_log_index`` field. ``_serialize_cosign_result`` "
        "(``src/roam/attest/emit_vsa.py:70-98``) projects "
        "{signed, statement_path, signature_path, certificate_path, "
        "bundle_path, skipped_reason, cosign_version} -- the union of "
        "those is the entire schema. The Rekor log index is the "
        "PRIMARY KEY for querying a Rekor record "
        "(``GET /api/v1/log/entries?logIndex=N``); without it, a "
        "downstream verifier cannot locate the transparency-log entry "
        "without parsing the local .bundle file. Fix template: thread "
        "the new ``CosignResult.rekor_log_index`` field through "
        "``_serialize_cosign_result``. Family: cross-artifact "
        "consistency at the transparency-log layer."
    ),
)
class TestSignatureEntryCarriesRekorLogIndex:
    def test_vsa_signature_entry_carries_rekor_log_index(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_quintuple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")
        log_index = (
            vsa_sig.get("rekor_log_index") or vsa_sig.get("log_index") or (vsa_sig.get("rekor") or {}).get("log_index")
        )
        assert log_index is not None, (
            "VSA signature entry MUST carry rekor_log_index so a "
            "downstream verifier can query the transparency log without "
            f"re-parsing the .bundle file. entry={vsa_sig!r}"
        )


# ---------------------------------------------------------------------------
# AXIS B -- envelope-side: ``slsa_l3.signatures[i]`` has no rekor_entry_url
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RRRRR axis B -- the signature entries in "
        "``slsa_l3.signatures[]`` carry NO ``rekor_entry_url`` field. "
        "The canonical Rekor entry URL "
        "(``https://rekor.sigstore.dev/api/v1/log/entries/<uuid>``) is "
        "the queryable artifact a downstream verifier hits to confirm "
        "tamper-evidence. Today roam captures neither the UUID nor the "
        "URL nor enough state to reconstruct either. Fix template: "
        "lift ``rekor_uuid`` off the .bundle JSON, compose the URL via "
        '``f"https://rekor.sigstore.dev/api/v1/log/entries/{uuid}"``, '
        "expose both on ``CosignResult`` + ``_serialize_cosign_result``. "
        "Family: cross-artifact consistency."
    ),
)
class TestSignatureEntryCarriesRekorEntryUrl:
    def test_vsa_signature_entry_carries_rekor_entry_url(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_quintuple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")
        url = (
            vsa_sig.get("rekor_entry_url") or vsa_sig.get("rekor_url") or (vsa_sig.get("rekor") or {}).get("entry_url")
        )
        assert isinstance(url, str) and url, (
            "VSA signature entry MUST carry rekor_entry_url so a "
            "downstream verifier has a queryable URL to confirm "
            f"transparency-log inclusion. entry={vsa_sig!r}"
        )
        assert "rekor" in url.lower(), f"rekor_entry_url MUST reference a rekor host; got {url!r}"


# ---------------------------------------------------------------------------
# AXIS C -- the Rekor entry's subject hash should bind to the VSA payload
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RRRRR axis C -- the signature entry carries NO "
        "``rekor_subject_hash`` mirror of the underlying statement's "
        "payload digest. Real Rekor entries carry a "
        "``spec.data.hash.value`` field (the sha256 of the signed "
        "bytes); a verifier handed only ``slsa_l3.signatures[i]`` "
        "cannot confirm the Rekor entry actually covers THIS VSA's "
        "bytes without fetching the .bundle from disk, base64-decoding "
        "the inner payload, AND re-hashing the source statement. This "
        "is the strongest Rekor-layer cross-artifact gap: the "
        "transparency log proves SOMETHING was signed, but roam's "
        "envelope can't say WHAT. Fix template: lift "
        "``rekorBundle.Payload.body`` JSON's ``spec.data.hash.value`` "
        "into ``rekor_subject_hash`` and assert equality against the "
        "VSA's ``subject[0].digest.sha256`` at emit time. Family: "
        "cross-artifact consistency + Pattern-2 silent fallback."
    ),
)
class TestSignatureEntryRekorSubjectMatchesVsaDigest:
    def test_rekor_subject_hash_equals_vsa_subject_sha256(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, vsa, _run, signatures = _emit_quintuple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")

        # The underlying VSA carries subject[0].digest -- the SLSA VSA
        # spec uses sha256 for the payload digest (sha1 separately for
        # the git commit, per W805-OOOOO axis structure).
        vsa_digest = (vsa.get("subject") or [{}])[0].get("digest", {})
        vsa_sha256 = vsa_digest.get("sha256")
        assert vsa_sha256, f"setup invariant: VSA missing subject sha256 digest: {vsa_digest!r}"

        rekor_subject = (
            vsa_sig.get("rekor_subject_hash")
            or (vsa_sig.get("rekor") or {}).get("subject_hash")
            or (vsa_sig.get("rekor") or {}).get("data_hash")
        )
        assert rekor_subject, (
            "VSA signature entry MUST carry rekor_subject_hash so a "
            "verifier can confirm the Rekor record covers the same "
            f"bytes as the local VSA. entry={vsa_sig!r}"
        )
        assert rekor_subject == vsa_sha256, (
            "rekor_subject_hash MUST equal VSA subject sha256 so the "
            "transparency-log entry is bound to the right payload. "
            f"rekor={rekor_subject!r} vsa={vsa_sha256!r}"
        )


# ---------------------------------------------------------------------------
# AXIS D -- the Rekor entry should carry the same commit_sha as the VSA
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RRRRR axis D -- the signature entry carries NO mirror "
        "of the VSA's ``subject[0].digest.sha1`` (the git commit sha) "
        "alongside the Rekor metadata. W805-PPPPP axis A pinned the "
        "sibling drift on the cosign-side; W805-RRRRR carries the "
        "same identity axis through to the FIFTH artifact (the Rekor "
        "log entry). A verifier fetching only the Rekor entry "
        "(without the .bundle, without the VSA, without the envelope) "
        "should be able to learn the commit_sha from a Rekor-anchored "
        "field on roam's envelope so the chain is self-describing. "
        "Fix template: stamp ``rekor_anchored_commit_sha`` on each "
        "signature entry, sourced from the VSA's subject sha1 at emit "
        "time. Family: cross-artifact consistency carried through "
        "five artifacts."
    ),
)
class TestSignatureEntryRekorCarriesCommitSha:
    def test_rekor_entry_carries_commit_sha_mirror(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, vsa, _run, signatures = _emit_quintuple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")
        vsa_sha1 = (vsa.get("subject") or [{}])[0].get("digest", {}).get("sha1")
        assert vsa_sha1, f"setup invariant: VSA missing subject sha1: {vsa.get('subject')!r}"
        rekor_commit = (
            vsa_sig.get("rekor_anchored_commit_sha")
            or (vsa_sig.get("rekor") or {}).get("commit_sha")
            or (vsa_sig.get("rekor") or {}).get("anchored_commit")
        )
        assert rekor_commit, (
            "VSA signature entry MUST carry rekor_anchored_commit_sha "
            f"so the transparency-log layer is commit-bound. entry={vsa_sig!r}"
        )
        assert rekor_commit == vsa_sha1, (
            f"rekor_anchored_commit_sha MUST equal the VSA's subject sha1. rekor={rekor_commit!r} vsa={vsa_sha1!r}"
        )


# ---------------------------------------------------------------------------
# AXIS E -- W805-PPPPP follow-up: do the TWO Rekor entries cross-reference?
#
# Both signed statements (vsa + run_ledger_root) go to Rekor, producing
# TWO separate log entries with TWO separate log indices. The W805-PPPPP
# axis C cross-reference gap (signatures don't reference each other)
# should be CLOSED at the Rekor layer -- the run-ledger-root signature's
# Rekor entry could store a reference to the VSA's Rekor entry. It
# doesn't. The correlation gap survives into the fifth artifact.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RRRRR axis E (W805-PPPPP follow-up) -- the TWO Rekor "
        "log entries produced by ``pr-bundle emit --slsa-l3 --sign "
        "--keyless`` (one per signed statement: vsa + run_ledger_root) "
        "carry NO cross-reference. W805-OOOOO axis B + W805-PPPPP axis "
        "C pinned the correlation gap at the JSON-artifact and "
        "cosign-signature layers respectively; the natural slot for "
        "the fix moved with each new artifact and it's now at the "
        "Rekor transparency-log layer too. A verifier fetching the VSA "
        "Rekor entry MUST be able to find the paired run-ledger-root "
        "Rekor entry without out-of-band coordination -- the strongest "
        "fix is to stamp a shared ``rekor_signature_set_id`` on BOTH "
        "entries at emit time (e.g. the ChangeEvidence content_hash). "
        "Today: no rekor_log_index, no rekor_signature_set_id, no "
        "rekor cross-reference of any kind. The five-artifact chain "
        "still has the correlation gap. Family: cross-artifact "
        "consistency carried through five artifacts."
    ),
)
class TestRekorEntriesCarryMutualCrossReference:
    def test_both_rekor_entries_share_correlation_id(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_quintuple(cli_runner, repo_with_bundle, monkeypatch)
        vsa_sig = _by_target(signatures, "vsa")
        run_sig = _by_target(signatures, "run_ledger_root")

        vsa_set = (
            vsa_sig.get("rekor_signature_set_id")
            or (vsa_sig.get("rekor") or {}).get("signature_set_id")
            or (vsa_sig.get("rekor") or {}).get("co_signed_with")
        )
        run_set = (
            run_sig.get("rekor_signature_set_id")
            or (run_sig.get("rekor") or {}).get("signature_set_id")
            or (run_sig.get("rekor") or {}).get("co_signed_with")
        )
        assert vsa_set, (
            "VSA signature entry MUST carry rekor_signature_set_id so "
            "a verifier walking from the VSA's Rekor entry can find "
            f"the paired run-ledger-root entry. entry={vsa_sig!r}"
        )
        assert run_set, f"run-ledger-root signature entry MUST carry rekor_signature_set_id. entry={run_sig!r}"
        # Symmetric: shared set id OR mutual log-index reference.
        symmetric = (
            vsa_set == run_set or vsa_set == run_sig.get("rekor_log_index") or run_set == vsa_sig.get("rekor_log_index")
        )
        assert symmetric, (
            "The two Rekor entries MUST be symmetrically correlated "
            "(shared set id OR mutual log-index reference). "
            f"vsa={vsa_set!r} run={run_set!r}"
        )


# ---------------------------------------------------------------------------
# POSITIVE pins -- locks in the four-artifact correlation that DOES exist.
#
# These are NOT drift axes. They confirm the existing-positive ground
# the W805-RRRRR fixes would build on top of: signatures[] is present,
# both targets are signed, and the bundle_path back-pointer exists. A
# regression that drops any of these would surface here, not via xpass.
# ---------------------------------------------------------------------------


class TestExistingPositiveGround:
    """The 4-artifact pre-conditions for the W805-RRRRR Rekor probe."""

    def test_signatures_list_present_with_two_targets(self, repo_with_bundle, cli_runner, monkeypatch):
        _env, _vsa, _run, signatures = _emit_quintuple(cli_runner, repo_with_bundle, monkeypatch)
        targets = sorted(sig.get("target") for sig in signatures)
        assert targets == ["run_ledger_root", "vsa"], f"Expected both signature targets present; got {targets!r}"

    def test_each_signature_entry_points_to_local_bundle_file(self, repo_with_bundle, cli_runner, monkeypatch):
        """Today's weak Rekor-adjacent correlation: each signature
        entry carries ``bundle_path``, and the .bundle file (when
        cosign actually runs) contains the Rekor entry body. The
        W805-RRRRR fixes would lift the .bundle's Rekor payload into
        roam-side metadata so consumers don't have to open the
        .bundle themselves.
        """
        _env, _vsa, _run, signatures = _emit_quintuple(cli_runner, repo_with_bundle, monkeypatch)
        for sig in signatures:
            bp = sig.get("bundle_path")
            assert isinstance(bp, str) and bp.strip(), f"signature entry missing bundle_path: {sig!r}"
            # Sanity: the path ends in .bundle (cosign convention).
            assert bp.endswith(".bundle"), f"bundle_path should end in .bundle: {bp!r}"


# ---------------------------------------------------------------------------
# STRUCTURAL counter-pin -- the cosign argv does NOT request the
# ``--rekor-url`` override.
#
# Cosign defaults to ``https://rekor.sigstore.dev``; this is fine for
# the public path. Pinning the default keeps a future "switch to
# a private Rekor" change visible. Not a drift today -- positive pin.
# ---------------------------------------------------------------------------


class TestCosignArgvUsesDefaultRekor:
    def test_no_rekor_url_override_in_argv(self):
        """Locks in the public-Rekor default. A change to private
        Rekor would need its own dedicated review."""
        cga_source = Path(_CGA_SPEC.origin).read_text(encoding="utf-8")
        # The override flag would be ``--rekor-url`` per cosign docs.
        assert "--rekor-url" not in cga_source, (
            "cga.py argv must NOT pass --rekor-url unless a private "
            "Rekor switch has been explicitly reviewed (env-var only, "
            "no hardcoded private endpoint)"
        )
