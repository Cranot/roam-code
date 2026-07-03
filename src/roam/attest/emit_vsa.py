"""W486 — shared VSA emit helpers.

Both ``roam pr-bundle emit --slsa-l3`` and ``roam cga emit --also-vsa``
project a ChangeEvidence packet into a SLSA v1 Verification Summary
Attestation (VSA) statement, write it to disk, and optionally cosign-sign
it. Before this module, each caller carried its own ~80%-shared copy of
that pipeline. Variance lived in three places only:

1. *Envelope source* — pr-bundle feeds the bundle envelope into
   :func:`collect_change_evidence` via ``pr_bundle_envelope=``; cga feeds
   the just-emitted CGA statement via ``cga_envelopes=[...]``.
2. *Output path* — pr-bundle lands the VSA inside
   ``.roam/pr-bundle/slsa-vsa-<evidence_id>.json``; cga lands the VSA next
   to the parent CGA as ``<stem>.vsa.json``.
3. *Extra side-product* — pr-bundle additionally emits a run-ledger
   root statement when ``ROAM_RUN_ID`` is set; cga has no analogue.

This module captures the shared middle (build VSA -> atomic write ->
optional cosign-sign) in :func:`_write_and_optionally_sign` and exposes
two thin callers:

* :func:`emit_pr_bundle_slsa_l3` — pr-bundle VSA + optional run-ledger
  root, both optionally signed. Returns the legacy result shape
  ``{"predicate_type", "vsa_path", "run_ledger_root_path", "signed",
  "signatures": [...], "skipped_reasons": [...]}``.
* :func:`emit_cga_vsa_sibling` — single sibling VSA next to the CGA.
  Returns the legacy result shape ``{"predicate_type", "vsa_path",
  "sign_result", "skipped_reasons": [...]}``.

Hash-stability mandate (W486): the VSA statement bytes produced through
this module MUST be byte-identical to the pre-refactor output. The result
dicts MUST stay schema-stable (additive only). See
``tests/test_evidence_schema_migration.py`` 31/31 + the parity test in
``tests/test_attest_vsa.py``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from roam.atomic_io import atomic_write_text
from roam.attest import cga as _cga
from roam.attest.cga import serialize_statement
from roam.attest.vsa import (
    SLSA_VSA_PREDICATE_TYPE,
    build_run_ledger_root_statement,
    build_vsa_statement,
)
from roam.evidence.collector import collect_change_evidence

# NOTE: ``cosign_sign_statement`` is intentionally accessed via the
# ``_cga`` module reference (not a top-level ``from ... import`` binding)
# so existing tests that ``monkeypatch.setattr(roam.attest.cga,
# "cosign_sign_statement", fake)`` keep working without also patching
# this module. See ``tests/test_attest_vsa.py`` (W451 + W472 keyless-
# sign tests).


__all__ = [
    "emit_pr_bundle_slsa_l3",
    "emit_cga_vsa_sibling",
]


# ---------- shared write + sign core ----------


def _serialize_cosign_result(
    cresult: Any, *, include_target_label: bool = False, target_label: str | None = None
) -> dict[str, Any]:
    """Project a :class:`CosignResult` into the JSON-stable dict shape both
    callers expose on their envelopes.

    The pr-bundle path stamps a ``target`` discriminator (it signs both
    the VSA and the run-ledger root in one pass); the cga path doesn't
    need one (single VSA). The field set is otherwise identical, so we
    centralise it here to keep the result-dict drift impossible.
    """
    entry: dict[str, Any] = {
        "signed": bool(cresult.signed),
        "statement_path": str(cresult.statement_path),
        "bundle_path": str(cresult.bundle_path) if cresult.bundle_path else None,
        "signature_path": str(cresult.signature_path) if cresult.signature_path else None,
        "certificate_path": str(cresult.certificate_path) if cresult.certificate_path else None,
        "skipped_reason": cresult.skipped_reason,
        "cosign_version": cresult.cosign_version,
    }
    if include_target_label:
        # pr-bundle's per-target dict puts ``target`` first for human
        # readability, but JSON dicts are ordered insertion-wise in
        # Python 3.7+ AND ``serialize_statement`` doesn't touch these
        # result dicts (they're envelope-side), so the ordering is purely
        # cosmetic. Match the legacy ordering anyway to keep diff churn
        # to a minimum.
        return {"target": target_label, **entry}
    return entry


def _sign_one(
    path: Path,
    *,
    key_path: str | None,
    keyless: bool,
    target_label: str | None = None,
    include_target_label: bool = False,
) -> dict[str, Any]:
    """Sign *path* with cosign; project the result into the caller's dict
    shape. Crash-safe: subprocess failures land as ``skipped_reason``
    rather than propagating.
    """
    try:
        cresult = _cga.cosign_sign_statement(
            path,
            key_path=Path(key_path) if key_path else None,
            keyless=keyless,
        )
    except Exception as exc:  # pragma: no cover — defensive
        if include_target_label:
            return {
                "target": target_label,
                "signed": False,
                "skipped_reason": f"cosign invocation crashed: {exc}",
            }
        return {
            "signed": False,
            "skipped_reason": f"cosign invocation crashed: {exc}",
        }
    return _serialize_cosign_result(
        cresult,
        include_target_label=include_target_label,
        target_label=target_label,
    )


# ---------- shared identity + hash wire-up ----------


def _git_commit_sha_or_none(root: Path) -> str | None:
    """W509/W520 fallback — resolve commit identity via ``git rev-parse
    HEAD`` when the caller's envelope/subject lacks it. Crash-safe: if
    ``git`` is unavailable / not a repo, return ``None`` so the emit path
    still completes (sha1 will simply be absent — same as before the
    fallback for non-git workspaces).

    The import stays function-local so the lookup resolves at call time
    (tests monkeypatch ``roam.attest.cga._git_commit_sha``).
    """
    from roam.attest.cga import _git_commit_sha

    try:
        return _git_commit_sha(root)
    except (subprocess.SubprocessError, OSError):  # pragma: no cover — defensive
        # Intentional: _git_commit_sha already returns None on these (no git /
        # not a repo / timeout, see cga.py); swallow keeps emit fail-soft (W509/W520).
        return None


def _gather_hash_kwargs_or_empty(root: Path) -> dict[str, Any]:
    """W1279 — lift packet-side hashes from the active run's meta.json
    (when ROAM_RUN_ID is set) and recompute the on-disk hashes so the
    collector's W1253 drift detector can fire. Missing run / missing
    meta gracefully degrades to ``packet_config_hashes=None``; no
    exception is propagated up to abort the VSA emit.
    """
    try:
        from roam.evidence.config_hashes_producer import gather_hash_kwargs

        run_id = os.environ.get("ROAM_RUN_ID", "").strip() or None
        return gather_hash_kwargs(root, run_id)
    except Exception:  # noqa: BLE001 - never break VSA emit on hash wire-up
        return {}


# ---------- pr-bundle emit --slsa-l3 ----------


def _emit_run_ledger_root(root: Path, out_dir: Path, result: dict[str, Any]) -> Path | None:
    """Best-effort run-ledger root attestation (ROAM_RUN_ID drives it).

    Owns the whole stage-3 fault domain: every failure lands as a
    ``skipped_reasons`` entry on *result*, never an exception. Returns
    the written statement path, or ``None`` when nothing was written
    (so the signing stage knows to skip this target).

    Whitespace-only ROAM_RUN_ID normalises to None so a malformed env
    var ("   ") cannot reach ``read_run_meta`` and silently surface as
    a misleading "run-ledger HMAC chain not signed" reason (Pattern-2
    silent fallback). Matches the ``.strip() or None`` discipline in
    :func:`_gather_hash_kwargs_or_empty`.
    """
    run_id = os.environ.get("ROAM_RUN_ID", "").strip() or None
    if not run_id:
        result["skipped_reasons"].append("ROAM_RUN_ID not set; run-ledger root attestation skipped")
        return None
    try:
        run_stmt = build_run_ledger_root_statement(root, run_id)
    except Exception as exc:
        run_stmt = None
        result["skipped_reasons"].append(f"run-ledger root build failed: {exc}")
    if run_stmt is None:
        result["skipped_reasons"].append("run-ledger HMAC chain not signed (no final_signature on meta.json)")
        return None
    run_path = out_dir / f"run-ledger-root-{run_id}.json"
    try:
        atomic_write_text(run_path, serialize_statement(run_stmt) + "\n")
        result["run_ledger_root_path"] = str(run_path)
    except Exception as exc:
        result["skipped_reasons"].append(f"run-ledger root write failed: {exc}")
        return None
    return run_path


def _sign_pr_bundle_outputs(
    result: dict[str, Any],
    *,
    vsa_path: Path,
    run_path: Path | None,
    sign_key: str | None,
    sign_keyless: bool,
) -> None:
    """Cosign-sign the VSA + (if written) run-ledger root, appending
    per-target entries to ``result["signatures"]`` and flipping
    ``result["signed"]`` when any target signs.
    """
    for label, target in (("vsa", vsa_path), ("run_ledger_root", run_path)):
        if target is None:
            continue
        sig_entry = _sign_one(
            target,
            key_path=sign_key,
            keyless=sign_keyless,
            target_label=label,
            include_target_label=True,
        )
        result["signatures"].append(sig_entry)
        if sig_entry.get("signed"):
            result["signed"] = True


def emit_pr_bundle_slsa_l3(
    *,
    root: Path,
    envelope: dict,
    sign: bool,
    sign_key: str | None,
    sign_keyless: bool,
) -> dict[str, Any]:
    """W451 emit path — VSA + optional run-ledger-root attestation.

    Pure side-effect helper. Returns the legacy result-dict shape
    consumed by :func:`roam.commands.cmd_pr_bundle.pr_bundle_emit`.
    No-fail discipline: every error is caught and recorded under
    ``skipped_reasons``; SLSA emission MUST NOT break ``pr-bundle emit``.
    """
    result: dict[str, Any] = {
        "predicate_type": SLSA_VSA_PREDICATE_TYPE,
        "vsa_path": None,
        "run_ledger_root_path": None,
        "signed": False,
        "signatures": [],
        "skipped_reasons": [],
    }

    # 1. Build ChangeEvidence from the pr-bundle envelope.
    #
    # W509: the bundle envelope's ``commit_sha`` field is populated by
    # ``pr-bundle init`` (auto-collect path) but is typically absent on
    # ``--no-auto-collect`` runs that hand-craft a minimal bundle.
    # Without the git fallback the resulting VSA drops
    # ``subject[0].digest.sha1``, breaking the SRC-L3 "commit-anchored
    # provenance" claim downstream verifiers depend on.
    commit_sha = envelope.get("commit_sha") or _git_commit_sha_or_none(root)
    _hash_kwargs = _gather_hash_kwargs_or_empty(root)

    try:
        change_evidence, warnings = collect_change_evidence(
            pr_bundle_envelope=envelope,
            repo_id=envelope.get("repo_id"),
            commit_sha=commit_sha,
            **_hash_kwargs,
        )
        change_evidence = change_evidence.with_content_hash()
    except Exception as exc:
        result["skipped_reasons"].append(f"ChangeEvidence collection failed: {exc}")
        return result
    if warnings:
        result["collector_warnings"] = list(warnings)

    # 2. Build + write the SLSA VSA statement.
    out_dir = root / ".roam" / "pr-bundle"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"slsa-vsa-{change_evidence.evidence_id}"
    vsa_path = out_dir / f"{stem}.json"
    try:
        vsa_statement = build_vsa_statement(change_evidence)
        atomic_write_text(vsa_path, serialize_statement(vsa_statement) + "\n")
        result["vsa_path"] = str(vsa_path)
    except Exception as exc:
        result["skipped_reasons"].append(f"VSA emit failed: {exc}")
        return result

    # 3. Run-ledger root statement (best-effort; ROAM_RUN_ID drives it).
    run_path = _emit_run_ledger_root(root, out_dir, result)

    # 4. Optional cosign signing of both statements.
    if sign:
        _sign_pr_bundle_outputs(
            result,
            vsa_path=vsa_path,
            run_path=run_path,
            sign_key=sign_key,
            sign_keyless=sign_keyless,
        )

    return result


# ---------- cga emit --also-vsa ----------


def _resolve_cga_subject_identity(
    statement: dict,
    project_root: Path,
) -> tuple[str | None, str | None]:
    """Pull commit-anchored identity from the parent CGA subject.

    CGA statements produced by ``roam cga emit`` include
    ``git_commit_sha1`` resolved at build time, but direct API callers
    can hand-craft a subject without it. Downstream verifiers depend on
    commit-anchored provenance, so we normalise ``"unknown"`` to None
    and fall back to ``git rev-parse HEAD`` before giving up.
    """
    subject_list = statement.get("subject") or [{}]
    subject0 = subject_list[0] if subject_list else {}
    digest = subject0.get("digest") or {}
    commit_sha = digest.get("git_commit_sha1")
    if commit_sha == "unknown":
        commit_sha = None
    repo_id = subject0.get("name") or None

    # W520: parallel to the W509 fallback in ``emit_pr_bundle_slsa_l3``.
    if not commit_sha:
        commit_sha = _git_commit_sha_or_none(project_root)

    return repo_id, commit_sha


def _write_vsa_sibling(
    change_evidence: Any,
    written_path: Path,
    statement: dict,
) -> Path:
    """Build and write the VSA statement next to the parent CGA.

    The sibling shares the CGA's ``indexed_at`` timestamp so both
    attestations describe the same emission event, and its filename
    strips the ``.intoto.json`` suffix rather than nesting ``.vsa``
    inside the ``.intoto`` stem.
    """
    # ``written_path`` is e.g. ``.roam/attestations/abc123.intoto.json``;
    # strip the ``.intoto.json`` (two suffixes) so the sibling is
    # ``abc123.vsa.json`` rather than ``abc123.intoto.vsa.json``.
    # Path.with_suffix only strips one suffix at a time.
    if written_path.name.endswith(".intoto.json"):
        stem = written_path.name[: -len(".intoto.json")]
        vsa_path = written_path.with_name(f"{stem}.vsa.json")
    else:
        vsa_path = written_path.with_name(f"{written_path.stem}.vsa.json")

    # One clock for both attestations of the same emission event: the
    # sibling's timeVerified pins to the CGA predicate's indexed_at.
    # Independent now() calls straddle second boundaries on slow
    # runners (W805-KKKKK axis C).
    _cga_indexed_at = (statement.get("predicate") or {}).get("indexed_at")
    vsa_statement = build_vsa_statement(change_evidence, time_verified=_cga_indexed_at)
    atomic_write_text(vsa_path, serialize_statement(vsa_statement) + "\n")
    return vsa_path


def emit_cga_vsa_sibling(
    *,
    statement: dict,
    written_path: Path | None,
    written_to: str | None,
    no_write: bool,
    project_root: Path,
    sign: bool,
    key_path: str | None,
    keyless: bool,
) -> dict:
    """W472 emit path — single VSA next to the just-written CGA.

    Pure side-effect helper. Returns the legacy result-dict shape
    consumed by :func:`roam.commands.cmd_cga.cga_emit`. No-fail
    discipline: every error is caught and recorded; VSA emission MUST
    NOT break ``cga emit``.

    ``project_root`` is used as the working directory for the W520
    ``git rev-parse HEAD`` fallback when the parent CGA's subject digest
    lacks ``git_commit_sha1`` (rare but possible via direct API use that
    hand-crafts a subject without git). Otherwise the path strategy here
    remains sibling-of-CGA rather than repo-root-anchored.
    """

    result: dict = {
        "predicate_type": SLSA_VSA_PREDICATE_TYPE,
        "vsa_path": None,
        "sign_result": None,
        "skipped_reasons": [],
    }

    # 1. Prerequisites: cannot land a sibling file when the parent CGA
    # went to stdout (-) or was suppressed (--no-write).
    if no_write or written_path is None or written_to == "stdout":
        result["skipped_reasons"].append(
            "--also-vsa requires a written CGA statement file (incompatible with --no-write and --output -)"
        )
        return result

    # 2. Identity + hash wire-up.
    repo_id, commit_sha = _resolve_cga_subject_identity(statement, project_root)
    _hash_kwargs = _gather_hash_kwargs_or_empty(project_root)

    # 3. Build the ChangeEvidence packet from the CGA we just emitted.
    try:
        change_evidence, warnings = collect_change_evidence(
            cga_envelopes=[statement],
            repo_id=repo_id,
            commit_sha=commit_sha,
            **_hash_kwargs,
        )
        change_evidence = change_evidence.with_content_hash()
    except Exception as exc:
        result["skipped_reasons"].append(f"ChangeEvidence collection failed: {exc}")
        return result
    if warnings:
        result["collector_warnings"] = list(warnings)

    # 4. Build + write the SLSA VSA statement next to the CGA.
    try:
        vsa_path = _write_vsa_sibling(change_evidence, written_path, statement)
        result["vsa_path"] = str(vsa_path)
    except Exception as exc:
        result["skipped_reasons"].append(f"VSA emit failed: {exc}")
        return result

    # 5. Optional cosign signing of the VSA (mirrors the CGA --sign path).
    if sign:
        result["sign_result"] = _sign_one(
            vsa_path,
            key_path=key_path,
            keyless=keyless,
            include_target_label=False,
        )

    return result
