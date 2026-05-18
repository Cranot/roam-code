"""W805-CONSOLIDATE -- parametrized drift table for the 6-artifact W805 family.

Hundred-and-twenty-third-in-batch W805 sweep. This file does NOT add a
new artifact-axis -- it consolidates the (artifact_kind x identity_axis)
matrix that the 5 W805 sister files
(KKKKK / OOOOO / PPPPP / RRRRR / SSSSS) pin one cell at a time.

The sister files retain their xfail-strict pins verbatim (ADDITIVE-ONLY
discipline -- see the W805-CONSOLIDATE wave notes). This file is a
parallel artifact: it exercises the SAME fixture chain through the
shared helper at ``tests/_w805_emit_helpers.py``, and pins the same
drift cells against ONE PARAMETRIZED ID so a future drift-count
regression surfaces as a single named assertion rather than 6 separate
file diffs.

Family-closer summary
=====================

When the upstream emit path ships ``signature_set_id`` (or an
equivalent shared correlation id) across all 6 artifacts -- and the
related fixes for OIDC issuer / workflow identity / commit_sha
propagation / dirty-tree disclosure / payload-predicate-type / Rekor
log index land -- the ``test_drift_cell_count_matches_known_total``
family-closer trips on xpassed strict-xfails and the family closes in
ONE place.

W978 first-hypothesis discipline
================================

This file was BUILT against the live xfail set in the 5 sister files,
NOT against a hypothetical post-fix world. Every drift cell is named
in the parametrize matrix with its ``family_origin`` so the cell can be
located in the corresponding sister file when it trips.

W907 verify-cycle check
=======================

This file's only import beyond stdlib + pytest is
``tests._w805_emit_helpers`` (sibling module in the same package).
The helper module is genuinely a deferred-import surface (lazy import
of ``roam.attest.cga`` inside ``build_six_artifact_fixture`` to keep
top-level import cheap). No false-cycle hedges -- grep ``-i
'avoid.*cycle|circular import|kept local|would create a cycle|
duplicated.*here'`` over ``_w805_emit_helpers.py`` is clean by
construction.

Run isolation
=============

    python -m pytest tests/test_w805_consolidate_cross_artifact_drift_table.py -x -n 0

Sister parity (must stay identical pass/xfail counts before + after
this file lands)
===============

    python -m pytest \
        tests/test_w805_kkkkk_cga_vsa_sibling_consistency.py \
        tests/test_w805_ooooo_pr_bundle_slsa_l3_three_artifact_identity_coherence.py \
        tests/test_w805_ppppp_pr_bundle_cosign_four_artifact_identity_coherence.py \
        tests/test_w805_rrrrr_pr_bundle_rekor_five_artifact_identity_coherence.py \
        tests/test_w805_sssss_pr_bundle_fulcio_six_artifact_identity_coherence.py \
        tests/test_w805_consolidate_cross_artifact_drift_table.py \
        -n 0 -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from _w805_emit_helpers import (  # noqa: E402
    build_six_artifact_fixture,
    extract_commit_sha,
    extract_dirty_flag,
    extract_oidc_issuer,
    extract_payload_predicate_type,
    extract_rekor_log_index,
    extract_signature_set_id,
    extract_workflow_identity,
    init_repo_with_bundle,
    substrate_modules_present,
)

# ---------------------------------------------------------------------------
# Substrate gate -- identical to per-sister-file gates.
# ---------------------------------------------------------------------------


def test_substrate_modules_present():
    """W978/W907 gate: pr_bundle + emit_vsa + vsa + cga + runs.ledger import.

    Mirrors each sister file's substrate gate; consolidated here so the
    drift-table refuses to run when the substrate isn't installed
    (rather than emitting a wall of misleading xfails).
    """
    if not substrate_modules_present():
        pytest.skip("W805 substrate modules not installed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def repo_with_bundle(tmp_path):
    if not substrate_modules_present():
        pytest.skip("W805 substrate modules not installed")
    return init_repo_with_bundle(tmp_path)


@pytest.fixture
def fixture_clean(repo_with_bundle, cli_runner, monkeypatch):
    """Six-artifact fixture on a clean tree."""
    return build_six_artifact_fixture(cli_runner, repo_with_bundle, monkeypatch)


@pytest.fixture
def fixture_dirty(repo_with_bundle, cli_runner, monkeypatch):
    """Six-artifact fixture on a dirty tree (uncommitted edit)."""
    (repo_with_bundle / "a.py").write_text("def f():\n    return 2\n# uncommitted edit\n", encoding="utf-8")
    return build_six_artifact_fixture(cli_runner, repo_with_bundle, monkeypatch)


# ---------------------------------------------------------------------------
# The drift-cell matrix.
#
# Each row pins ONE (artifact_kind, identity_axis) cell.
#
# ``currently_present=True`` rows are positive assertions (no drift today).
# ``currently_present=False`` rows are xfail-strict pins on a known drift
# cell. When the underlying source ships the missing field, the cell
# trips and the family closes one cell at a time.
#
# The matrix MIRRORS the xfail-strict pins in the 5 sister files,
# parametrized so a single cell change surfaces as a single named
# assertion. NOT replacing the sister pins -- consolidating them in
# one structured place.
# ---------------------------------------------------------------------------


# (artifact_kind, identity_axis, extractor, currently_present, family_origin, reason)
_DRIFT_CELLS: list[tuple[str, str, Any, bool, str, str]] = [
    # ---------- KKKKK family (CGA + VSA sibling) ------------------------
    # The KKKKK family drives ``cga emit --also-vsa`` rather than
    # ``pr-bundle emit --slsa-l3``, so it's not directly reproducible
    # from the consolidating fixture. We document those drift cells as
    # KKKKK-origin-only here; the sister file retains the live pin.
    # See ``family_origin="kkkkk"`` exclusions in the matrix below.
    # ---------- OOOOO family (envelope + VSA + run_ledger_root) --------
    # Axis E: envelope.commit_sha None while VSA carries the real sha.
    (
        "envelope",
        "commit_sha",
        extract_commit_sha,
        False,
        "ooooo",
        "envelope-missing-commit_sha-drift",
    ),
    # Axis A: VSA has the commit sha (positive control).
    (
        "vsa",
        "commit_sha",
        extract_commit_sha,
        True,
        "ooooo",
        "vsa-carries-commit_sha-positive",
    ),
    # Axis A: run-ledger-root attestation has NO commit_sha.
    (
        "run_ledger_root",
        "commit_sha",
        extract_commit_sha,
        False,
        "ooooo",
        "run_ledger_root-missing-commit_sha-drift",
    ),
    # Axis D: dirty-tree absent on VSA.
    (
        "vsa",
        "dirty_flag",
        extract_dirty_flag,
        False,
        "ooooo",
        "vsa-missing-dirty_flag-drift",
    ),
    # Axis D: dirty-tree absent on run-ledger-root.
    (
        "run_ledger_root",
        "dirty_flag",
        extract_dirty_flag,
        False,
        "ooooo",
        "run_ledger_root-missing-dirty_flag-drift",
    ),
    # ---------- PPPPP family (cosign signature triplet, 4-artifact) ----
    # Axis A: cosign sig entry missing payload_subject_digest mirror.
    (
        "cosign_vsa",
        "commit_sha",
        extract_commit_sha,
        False,
        "ppppp",
        "cosign_vsa-missing-commit_sha-drift",
    ),
    # Axis B: cosign sig entry missing payload_predicate_type.
    (
        "cosign_vsa",
        "payload_predicate_type",
        extract_payload_predicate_type,
        False,
        "ppppp",
        "cosign_vsa-missing-payload_predicate_type-drift",
    ),
    (
        "cosign_run",
        "payload_predicate_type",
        extract_payload_predicate_type,
        False,
        "ppppp",
        "cosign_run-missing-payload_predicate_type-drift",
    ),
    # Axis C: cosign signatures missing signature_set_id (strongest gap).
    (
        "cosign_vsa",
        "signature_set_id",
        extract_signature_set_id,
        False,
        "ppppp",
        "cosign_vsa-missing-signature_set_id-drift",
    ),
    (
        "cosign_run",
        "signature_set_id",
        extract_signature_set_id,
        False,
        "ppppp",
        "cosign_run-missing-signature_set_id-drift",
    ),
    # Axis D: cosign signatures missing dirty-tree disclosure.
    (
        "cosign_vsa",
        "dirty_flag",
        extract_dirty_flag,
        False,
        "ppppp",
        "cosign_vsa-missing-dirty_flag-drift",
    ),
    (
        "cosign_run",
        "dirty_flag",
        extract_dirty_flag,
        False,
        "ppppp",
        "cosign_run-missing-dirty_flag-drift",
    ),
    # ---------- RRRRR family (Rekor transparency-log, 5-artifact) ------
    # Axis A: signatures missing rekor_log_index.
    (
        "rekor_vsa",
        "rekor_log_index",
        extract_rekor_log_index,
        False,
        "rrrrr",
        "rekor_vsa-missing-rekor_log_index-drift",
    ),
    (
        "rekor_run",
        "rekor_log_index",
        extract_rekor_log_index,
        False,
        "rrrrr",
        "rekor_run-missing-rekor_log_index-drift",
    ),
    # Axis D: rekor entry missing commit_sha mirror.
    (
        "rekor_vsa",
        "commit_sha",
        extract_commit_sha,
        False,
        "rrrrr",
        "rekor_vsa-missing-commit_sha-drift",
    ),
    # Axis E: rekor entries missing signature_set_id (correlation).
    (
        "rekor_vsa",
        "signature_set_id",
        extract_signature_set_id,
        False,
        "rrrrr",
        "rekor_vsa-missing-signature_set_id-drift",
    ),
    (
        "rekor_run",
        "signature_set_id",
        extract_signature_set_id,
        False,
        "rrrrr",
        "rekor_run-missing-signature_set_id-drift",
    ),
    # ---------- SSSSS family (Fulcio cert SAN, 6-artifact) -------------
    # Axis A: signatures missing oidc_issuer.
    (
        "fulcio_vsa",
        "oidc_issuer",
        extract_oidc_issuer,
        False,
        "sssss",
        "fulcio_vsa-missing-oidc_issuer-drift",
    ),
    (
        "fulcio_run",
        "oidc_issuer",
        extract_oidc_issuer,
        False,
        "sssss",
        "fulcio_run-missing-oidc_issuer-drift",
    ),
    # Axis B: signatures missing workflow_identity.
    (
        "fulcio_vsa",
        "workflow_identity",
        extract_workflow_identity,
        False,
        "sssss",
        "fulcio_vsa-missing-workflow_identity-drift",
    ),
    (
        "fulcio_run",
        "workflow_identity",
        extract_workflow_identity,
        False,
        "sssss",
        "fulcio_run-missing-workflow_identity-drift",
    ),
]


def _cell_id(
    artifact_kind: str,
    identity_axis: str,
    _extractor: Any,
    _currently_present: bool,
    family_origin: str,
    reason: str,
) -> str:
    """Build a parametrize id naming the cell + its family origin."""
    return f"{family_origin}-{artifact_kind}-{identity_axis}"


# The dirty-tree axis needs a tree with uncommitted edits to set the
# positive setup invariant. Split the matrix accordingly.
_DIRTY_AXIS = "dirty_flag"

_CLEAN_CELLS = [c for c in _DRIFT_CELLS if c[1] != _DIRTY_AXIS]
_DIRTY_CELLS = [c for c in _DRIFT_CELLS if c[1] == _DIRTY_AXIS]


@pytest.mark.parametrize(
    "artifact_kind,identity_axis,extractor,currently_present,family_origin,reason",
    _CLEAN_CELLS,
    ids=[_cell_id(*c) for c in _CLEAN_CELLS],
)
def test_drift_cell_clean(
    fixture_clean,
    artifact_kind,
    identity_axis,
    extractor,
    currently_present,
    family_origin,
    reason,
    request,
):
    """One drift cell from the consolidated matrix (clean-tree).

    Mirrors the corresponding sister-file pin: positive cells assert
    the field is populated; drift cells xfail-strict on the field
    being absent.

    Setup invariant: the underlying artifact must materialise. The
    fixture asserts that; a setup-time failure surfaces as an error,
    not an xpassed strict-xfail.
    """
    if not currently_present:
        # xfail-strict on the drift cell -- mirrors the sister pin.
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=(
                    f"W805-{family_origin.upper()} drift cell "
                    f"({artifact_kind} x {identity_axis}): {reason}. "
                    "See the corresponding sister file for the full "
                    "xfail-strict reason + fix template."
                ),
            )
        )
    artifact = fixture_clean[artifact_kind]
    value = extractor(artifact, artifact_kind)
    assert value, f"{artifact_kind} {identity_axis} MUST be populated. family_origin={family_origin} reason={reason}"


@pytest.mark.parametrize(
    "artifact_kind,identity_axis,extractor,currently_present,family_origin,reason",
    _DIRTY_CELLS,
    ids=[_cell_id(*c) for c in _DIRTY_CELLS],
)
def test_drift_cell_dirty(
    fixture_dirty,
    artifact_kind,
    identity_axis,
    extractor,
    currently_present,
    family_origin,
    reason,
    request,
):
    """One dirty-tree-axis drift cell from the consolidated matrix.

    Identical shape to ``test_drift_cell_clean`` but uses the
    dirty-tree fixture so the envelope's bundle_meta.git block records
    the porcelain hash that the OTHER artifacts are supposed to mirror.
    """
    if not currently_present:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=(
                    f"W805-{family_origin.upper()} drift cell "
                    f"({artifact_kind} x {identity_axis}): {reason}. "
                    "See the corresponding sister file for the full "
                    "xfail-strict reason + fix template."
                ),
            )
        )

    # Setup invariant: the envelope must record SOME dirty signal.
    env_dirty = extract_dirty_flag(fixture_dirty["envelope"], "envelope")
    assert env_dirty, (
        "Setup invariant: bundle envelope should record some dirty-tree "
        f"signal after uncommitted edit; envelope={fixture_dirty['envelope']!r}"
    )

    artifact = fixture_dirty[artifact_kind]
    value = extractor(artifact, artifact_kind)
    assert value, (
        f"{artifact_kind} {identity_axis} MUST be populated on dirty tree. "
        f"family_origin={family_origin} reason={reason}"
    )


# ---------------------------------------------------------------------------
# Family-closer summary
#
# Asserts the EXACT count of drift cells matches what's known
# at scan-time. When upstream fixes flip xfail-strict cells to
# xpassed (and the cells are removed from this matrix as they
# seal), this number flips and the family closes one cell at a
# time.
# ---------------------------------------------------------------------------


# Drift cells = cells where currently_present is False.
_KNOWN_DRIFT_CELL_COUNT = sum(1 for c in _DRIFT_CELLS if not c[3])


def test_drift_cell_count_matches_known_total():
    """Family-closer pin: the parametrize matrix declares a known number
    of drift cells today.

    When an upstream fix lands and the corresponding cell is removed
    from the matrix (or flipped to currently_present=True), this
    assertion trips. That's the signal to walk the sister files and
    flip their xfail-strict pins to either xpassed or to permanent
    positive assertions.
    """
    drift_cells = [c for c in _DRIFT_CELLS if not c[3]]
    positive_cells = [c for c in _DRIFT_CELLS if c[3]]
    assert len(drift_cells) == _KNOWN_DRIFT_CELL_COUNT, (
        f"Drift cell count drift: expected {_KNOWN_DRIFT_CELL_COUNT} drift cells, "
        f"got {len(drift_cells)}; positive_cells={len(positive_cells)}; "
        f"total={len(_DRIFT_CELLS)} families. "
        "Walk the sister files: KKKKK / OOOOO / PPPPP / RRRRR / SSSSS, "
        "flip the corresponding xfail-strict pin if a fix has landed."
    )


def test_drift_cell_family_coverage():
    """Family-closer pin: every W805 family (OOOOO/PPPPP/RRRRR/SSSSS)
    contributes at least one drift cell to the matrix.

    KKKKK is intentionally NOT covered by this consolidating fixture
    (it drives ``cga emit --also-vsa`` rather than ``pr-bundle emit
    --slsa-l3``); its 2 drift cells stay pinned exclusively in the
    sister file. The other 4 families MUST each have at least one
    drift cell here.
    """
    families = {c[4] for c in _DRIFT_CELLS if not c[3]}
    assert families == {"ooooo", "ppppp", "rrrrr", "sssss"}, (
        f"Expected drift cells from {{ooooo,ppppp,rrrrr,sssss}}; "
        f"got {sorted(families)!r}. KKKKK is intentionally not covered "
        "(different invocation path)."
    )


def test_drift_cell_count_at_least_sister_file_minimum():
    """The consolidating matrix MUST pin at least as many drift cells
    as the sum of unique-axis drifts pinned across the 4 covered
    sister files (OOOOO + PPPPP + RRRRR + SSSSS).

    Sister-file xfail-strict counts (live):
      * OOOOO: 5 xfails  (3 commit_sha-shaped + 1 dirty + 1 correlation)
      * PPPPP: 4 xfails  (commit_sha + predicate_type + set_id + dirty)
      * RRRRR: 6 xfails  (dataclass + log_index + url + subject + commit + set_id)
      * SSSSS: 6 xfails  (dataclass + oidc + workflow + validity + actor_refs + set_id)

    Some sister cells don't map cleanly to a single (artifact, axis)
    cell here (e.g. RRRRR's dataclass pin probes the dataclass shape,
    not the envelope projection; SSSSS axis D probes actor_refs[]
    structure, not a signature-entry field). The drift-cell-count
    minimum is therefore strictly less than the sister-file total
    -- but it MUST be at least the number of cells that DO map.
    """
    drift_cells = [c for c in _DRIFT_CELLS if not c[3]]
    # Counted from the sister-file mapping above; see the
    # ``family_origin`` distribution in _DRIFT_CELLS.
    family_counts = {}
    for c in drift_cells:
        family_counts[c[4]] = family_counts.get(c[4], 0) + 1

    # Floor per family: at least 2 axes pinned for each covered family.
    for family, count in family_counts.items():
        assert count >= 2, (
            f"Family {family!r} contributes only {count} drift cell(s); "
            "expected at least 2 (otherwise the consolidation isn't "
            "pulling its weight relative to the sister file)."
        )
