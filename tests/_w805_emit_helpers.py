"""W805-CONSOLIDATE -- shared fixture + extractor helpers for the W805
cross-artifact identity-coherence family.

The W805 family (KKKKK, OOOOO, PPPPP, RRRRR, SSSSS) pins ``xfail(strict=True)``
on cross-artifact identity drift across up to six related supply-chain
artifacts. Each sister test file owns a near-byte-identical
``_emit_triple`` / ``_emit_quadruple`` / ``_emit_quintuple`` /
``_emit_sextuple`` fixture builder and a near-byte-identical
``_by_target`` / ``subject.digest`` extractor. This module consolidates
those into one place so a future consolidating drift-table can iterate
all 6 artifact kinds against all identity axes from one fixture.

ADDITIVE-ONLY discipline (accumulate-then-squash). This helper module
does NOT replace any sister file's bespoke fixture; it is consumed by the
consolidating drift-table at
``tests/test_w805_consolidate_cross_artifact_drift_table.py``. Sister
files retain their xfail-strict pins verbatim until a separate,
user-authorised post-squash wave can safely refactor them.

W907 verify-cycle check: this module imports ``roam.attest.cga`` and
``roam.attest.vsa`` ONLY (both via ``importlib.util.find_spec`` at the
gate-test layer, with the actual import deferred inside the fixture
builder). No false-cycle hedges; the lazy-import inside the builder is
specifically to keep the module importable even when those modules are
absent (matches the W978 skip-gate pattern from the sister files).

LAW 4 anchoring: identity-axis terminal tokens (``commit_sha``,
``signature_set_id``, etc.) are concrete field nouns, not narrative
tails -- the consolidating drift-table's parametrize ids stay
LAW-4-compliant.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

# ---------------------------------------------------------------------------
# Module presence gates (W978 + W907 -- verify before hypothesising)
# ---------------------------------------------------------------------------


def substrate_modules_present() -> bool:
    """Return True iff the W805 substrate modules can be imported.

    Mirrors the per-sister-file ``test_substrate_modules_present`` gate;
    the consolidating drift-table calls this to decide whether to skip
    or proceed.
    """
    for mod in (
        "roam.commands.cmd_pr_bundle",
        "roam.attest.emit_vsa",
        "roam.attest.vsa",
        "roam.attest.cga",
        "roam.runs.ledger",
    ):
        if importlib.util.find_spec(mod) is None:
            return False
    return True


# ---------------------------------------------------------------------------
# 6-artifact fixture builder -- byte-equivalent to the sister
# ``_emit_sextuple`` helpers but parameterized.
# ---------------------------------------------------------------------------


def _git(args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def init_repo_with_bundle(tmp_path: Path) -> Path:
    """Initialise a tiny git repo + hand-craft a minimal pr-bundle.

    Byte-identical to the sister W805 fixtures so the consolidating
    probe shares ground truth with each individual artifact-count
    sister.
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


def build_six_artifact_fixture(
    runner,
    repo: Path,
    monkeypatch,
    *,
    run_id: str = "r_consolidate_demo",
) -> dict[str, Any]:
    """Drive ``pr-bundle emit --slsa-l3 --sign --keyless`` with stubs so
    all six artifact surfaces materialise in one invocation:

    * ``envelope`` -- the pr-bundle JSON envelope (artifact 1)
    * ``vsa`` -- the SLSA VSA in-toto statement (artifact 2)
    * ``run_ledger_root`` -- the run-ledger-root in-toto statement (artifact 3)
    * ``cosign_vsa`` / ``cosign_run`` -- the cosign signature entries
      (artifacts 4a/4b -- the "cosign triplet" surface as projected onto
      ``slsa_l3.signatures[]``)
    * ``rekor_vsa`` / ``rekor_run`` -- the Rekor transparency-log entries
      (artifacts 5a/5b -- live on the signature entries via
      ``rekor_log_index`` / ``rekor_entry_url`` projection; absent today)
    * ``fulcio_vsa`` / ``fulcio_run`` -- the Fulcio short-lived cert SAN
      content (artifacts 6a/6b -- live on the signature entries via
      ``oidc_issuer`` / ``workflow_identity`` / ``cert_not_before`` /
      ``cert_not_after`` projection; absent today)

    Rekor + Fulcio extract from the SAME signature entries -- the
    cosign-side dataclass is the only place where transparency-log +
    cert-SAN metadata could land. Today none of those fields are present,
    which is exactly what the consolidating drift-table pins.

    Returns a dict keyed by artifact name, each value a dict-shaped
    artifact (the actor for that artifact's identity axes). For Rekor +
    Fulcio the value points at the same dict as the cosign signature
    entry (since today those fields are projected through the cosign
    metadata layer).
    """
    from roam.attest import cga as cga_mod  # lazy: defer heavy import to first-use

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
        run_id = "r_consolidate_demo"
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

    cosign_vsa = _by_target(signatures, "vsa")
    cosign_run = _by_target(signatures, "run_ledger_root")

    return {
        "envelope": envelope,
        "vsa": vsa,
        "run_ledger_root": run_root,
        # cosign signature entries (artifacts 4a / 4b)
        "cosign_vsa": cosign_vsa,
        "cosign_run": cosign_run,
        # rekor + fulcio project through the SAME signature-entry dict
        # today -- if/when separate Rekor/Fulcio metadata dicts emerge,
        # this is the seam where they'd land
        "rekor_vsa": cosign_vsa,
        "rekor_run": cosign_run,
        "fulcio_vsa": cosign_vsa,
        "fulcio_run": cosign_run,
        # All signature entries together (for axes that probe both pairs)
        "signatures": signatures,
    }


def _by_target(signatures: list[dict], target: str) -> dict:
    """Pick the signature entry for a given target.

    Identical to the sister-file ``_by_target`` helpers; consolidated
    here so the drift-table can iterate without re-defining.
    """
    for sig in signatures:
        if sig.get("target") == target:
            return sig
    raise AssertionError(f"no signature entry for target={target!r}; got {signatures!r}")


# ---------------------------------------------------------------------------
# Per-artifact identity-axis extractors.
#
# Each extractor returns the value the axis names on the given artifact,
# or ``None`` if the field is absent today (which is the drift the
# consolidating table pins).
# ---------------------------------------------------------------------------


def extract_commit_sha(artifact: Mapping[str, Any], kind: str) -> str | None:
    """Pull the artifact's notion of ``commit_sha`` (or return None).

    Knowing about each artifact's distinct field name is the entire
    point of the helper -- the drift the W805 family pins is precisely
    that these fields disagree (or are absent).
    """
    if kind == "envelope":
        return artifact.get("commit_sha")
    if kind in ("vsa", "cga"):
        # VSA: subject[0].digest.sha1 ; CGA: subject[0].digest.git_commit_sha1
        digest = (artifact.get("subject") or [{}])[0].get("digest", {})
        return digest.get("sha1") or digest.get("git_commit_sha1")
    if kind == "run_ledger_root":
        pred = artifact.get("predicate") or {}
        subj = (artifact.get("subject") or [{}])[0]
        digest = subj.get("digest", {})
        return pred.get("commit_sha") or digest.get("sha1")
    if kind in ("cosign_vsa", "cosign_run"):
        return (
            artifact.get("payload_subject_digest")
            or (artifact.get("payload_subject") or {}).get("digest", {}).get("sha1")
            or artifact.get("subject_digest")
        )
    if kind in ("rekor_vsa", "rekor_run"):
        return (
            artifact.get("rekor_anchored_commit_sha")
            or (artifact.get("rekor") or {}).get("commit_sha")
            or (artifact.get("rekor") or {}).get("anchored_commit")
        )
    if kind in ("fulcio_vsa", "fulcio_run"):
        # Fulcio doesn't natively carry commit_sha; this would only be
        # populated if a future fix uplifted it onto the cert metadata.
        return (artifact.get("fulcio") or {}).get("commit_sha")
    return None


def extract_subject_name(artifact: Mapping[str, Any], kind: str) -> str | None:
    if kind == "envelope":
        # No top-level subject name on the envelope; identity is implicit.
        return None
    if kind in ("vsa", "cga", "run_ledger_root"):
        return (artifact.get("subject") or [{}])[0].get("name")
    if kind in ("cosign_vsa", "cosign_run"):
        return artifact.get("target") or artifact.get("statement_path")
    return None


def extract_dirty_flag(artifact: Mapping[str, Any], kind: str) -> str | None:
    if kind == "envelope":
        git_block = (artifact.get("bundle_meta") or {}).get("git") or {}
        return git_block.get("status_porcelain_hash") or git_block.get("is_dirty") or artifact.get("git_dirty_hash")
    if kind == "cga":
        return (artifact.get("predicate") or {}).get("git_dirty_hash")
    if kind in ("vsa", "run_ledger_root"):
        pred = artifact.get("predicate") or {}
        return (
            pred.get("git_dirty_hash")
            or (pred.get("evidenceMetadata") or {}).get("git_dirty_hash")
            or ((pred.get("verifier") or {}).get("evidenceMetadata") or {}).get("git_dirty_hash")
        )
    if kind in ("cosign_vsa", "cosign_run"):
        return (
            artifact.get("git_dirty_hash")
            or artifact.get("status_porcelain_hash")
            or artifact.get("payload_git_dirty_hash")
        )
    return None


def extract_signature_set_id(artifact: Mapping[str, Any], kind: str) -> str | None:
    if kind in ("cosign_vsa", "cosign_run"):
        return (
            artifact.get("signature_set_id")
            or artifact.get("co_signed_with")
            or artifact.get("related_signature")
            or artifact.get("evidence_id")
            or artifact.get("content_hash")
        )
    if kind in ("rekor_vsa", "rekor_run"):
        return (
            artifact.get("rekor_signature_set_id")
            or (artifact.get("rekor") or {}).get("signature_set_id")
            or (artifact.get("rekor") or {}).get("co_signed_with")
        )
    return None


def extract_oidc_issuer(artifact: Mapping[str, Any], kind: str) -> str | None:
    if kind in ("fulcio_vsa", "fulcio_run", "cosign_vsa", "cosign_run"):
        return (
            artifact.get("oidc_issuer")
            or artifact.get("certificate_oidc_issuer")
            or (artifact.get("fulcio") or {}).get("oidc_issuer")
            or (artifact.get("cert") or {}).get("oidc_issuer")
        )
    return None


def extract_workflow_identity(artifact: Mapping[str, Any], kind: str) -> str | None:
    if kind in ("fulcio_vsa", "fulcio_run", "cosign_vsa", "cosign_run"):
        return (
            artifact.get("workflow_identity")
            or artifact.get("certificate_identity")
            or (artifact.get("fulcio") or {}).get("workflow_identity")
            or (artifact.get("cert") or {}).get("subject_alternative_name")
            or (artifact.get("cert") or {}).get("workflow_identity")
        )
    return None


def extract_rekor_log_index(artifact: Mapping[str, Any], kind: str) -> Any:
    if kind in ("rekor_vsa", "rekor_run", "cosign_vsa", "cosign_run"):
        return (
            artifact.get("rekor_log_index")
            or artifact.get("log_index")
            or (artifact.get("rekor") or {}).get("log_index")
        )
    return None


def extract_payload_predicate_type(artifact: Mapping[str, Any], kind: str) -> str | None:
    if kind in ("cosign_vsa", "cosign_run"):
        return artifact.get("payload_predicate_type") or artifact.get("predicateType") or artifact.get("payloadType")
    if kind in ("vsa", "run_ledger_root"):
        return artifact.get("predicateType")
    return None


# ---------------------------------------------------------------------------
# CrossArtifactProbe -- parametrize unit for the consolidating drift table.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrossArtifactProbe:
    """One (artifact_kind, identity_axis, expected_field) cell.

    ``currently_present``: True when this cell is a POSITIVE assertion
    (no drift today); False when it's an xfail-strict drift pin.

    ``family_origin``: which W805 sister file first surfaced the drift
    (kkkkk / ooooo / ppppp / rrrrr / sssss / consolidate).

    ``reason``: short LAW-4-anchored description of the cell, suitable
    for use as the parametrize id.
    """

    artifact_kind: str
    identity_axis: str
    extractor: Callable[[Mapping[str, Any], str], Any]
    currently_present: bool
    family_origin: str
    reason: str


# Canonical extractor table -- maps (artifact_kind, identity_axis) to the
# extractor function. The drift-table parametrizes over a subset of these.
EXTRACTORS: dict[str, Callable[[Mapping[str, Any], str], Any]] = {
    "commit_sha": extract_commit_sha,
    "subject_name": extract_subject_name,
    "dirty_flag": extract_dirty_flag,
    "signature_set_id": extract_signature_set_id,
    "oidc_issuer": extract_oidc_issuer,
    "workflow_identity": extract_workflow_identity,
    "rekor_log_index": extract_rekor_log_index,
    "payload_predicate_type": extract_payload_predicate_type,
}
