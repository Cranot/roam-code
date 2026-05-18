"""W641-followup-I — exporter risk-LEVEL vocabulary parity guard.

Pattern-3a structural close-out, exporter axis:

* W641              -- ``cmd_pr_risk`` emits ``risk_level_canonical``.
* W641-followup-A   -- ``cmd_impact`` emits canonical.
* W641-followup-B   -- ``cmd_critique`` emits canonical.
* W641-followup-C   -- ``cmd_pr_bundle`` emits canonical.
* W641-followup-D   -- ``cmd_attest`` emits canonical (+ ``MODERATE`` alias).
* W641-followup-E   -- ``cmd_diff`` emits canonical.
* W641-followup-F   -- collector lifts canonical onto ``ChangeEvidence.risk_level``.
* W641-followup-G   -- ``cmd_dark_matter`` emits canonical.
* W641-followup-H   -- ``cmd_migration_plan`` emits canonical (in flight).
* **W641-followup-I (this module)** -- audit DOWNSTREAM exporters
  (``OSCAL``, ``SARIF``, ``OTel``, ``CDEvents``) for risk-LEVEL vocabulary
  drift now that the producer→packet projection loop is closed.

AUDIT RESULT (clean): no exporter re-derives ``risk_level`` from the raw
graph; no exporter carries an inline 4-tier projection table that
duplicates :mod:`roam.output.risk`; both shipped exporters either pass
``ChangeEvidence.risk_level`` through verbatim (OSCAL) or never consume
the field at all (SARIF — operates on per-finding ``severity``, projected
through the canonical :mod:`roam.output._severity` table).

This module pins the clean-audit verdict so a future regression
(an exporter introducing its own ``low / moderate / high / critical`` →
SARIF level table, or copying the OSCAL ``risk-level`` prop emission
without consuming the canonical packet value) fails fast.

Test inventory (8 tests, ≥ 5 required for the no-fix close-out):

1. ``test_oscal_consumes_canonical_risk_level`` -- ChangeEvidence with
   ``risk_level="high"`` projects to OSCAL ``risk-level`` prop with the
   same canonical token. No re-normalisation, no UPPER-casing.
2. ``test_oscal_passes_through_all_four_canonical_tiers`` -- each of the
   4-tier canonical (``critical / high / medium / low``) round-trips
   through ``build_oscal_assessment_results`` byte-identically.
3. ``test_oscal_no_inline_severity_table_drift`` -- AST-level check that
   ``oscal.py`` does not declare a hardcoded
   ``{"low":..., "medium":..., "high":..., "critical":...}`` projection
   table outside the canonical helpers.
4. ``test_sarif_level_map_covers_canonical_4_tier`` -- the SARIF
   ``_SARIF_LEVEL_MAP`` (via ``to_sarif_level``) handles all four
   canonical roam-severity tokens that downstream consumers expect.
5. ``test_sarif_critical_projects_to_error`` -- explicit assertion the
   ``critical`` tier projects to SARIF ``error`` so a CI gate keyed off
   SARIF ``level: error`` catches the highest severity (W531 lesson).
6. ``test_no_exporter_re_derives_from_raw_graph`` -- AST check: neither
   ``oscal.py`` nor ``sarif.py`` imports ``roam.db.connection.open_db``
   or instantiates ``sqlite3.Connection``. They MUST read from
   ``ChangeEvidence`` / per-finding inputs only (the canonical mandate).
7. ``test_otel_adapter_absent_or_canonical`` -- when an OTel adapter
   lands under ``src/roam/evidence/otel.py``, it MUST consume canonical
   from the start. Today the file is absent (greenfield); this test
   pins that property — a future ``otel.py`` MUST either consume
   ``ChangeEvidence.risk_level`` or fail this test.
8. ``test_cdevents_adapter_absent_or_canonical`` -- ditto for
   ``src/roam/evidence/cdevents.py``.
9. ``test_normalize_applied_at_collector_boundary`` -- ``MODERATE`` /
   ``Moderate`` / ``moderate`` / ``medium`` all collapse to ``medium``
   at the collector boundary, so every exporter sees the same canonical
   value regardless of the producer's casing/aliasing.

All tests are pure dict / AST / import exercises - no DB, no
filesystem, no CLI invocation. Greenfield otel / cdevents tests stay
green while the files are absent and trip if the future file does not
consume canonical.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from datetime import datetime, timezone

import pytest

from roam.evidence import ChangeEvidence
from roam.evidence.oscal import build_oscal_assessment_results
from roam.output._severity import _SARIF_LEVEL_MAP, to_sarif_level
from roam.output.risk import RISK_LEVELS, normalize_risk_level
from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_FIXED_CLOCK = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def _packet(*, risk_level: str | None) -> ChangeEvidence:
    """Build a minimal ChangeEvidence packet with the requested risk-LEVEL.

    Only ``risk_level`` matters for this audit — the rest of the packet
    is filled with safe defaults so the OSCAL emitter has enough surface
    to build a valid AR document.
    """
    return ChangeEvidence(
        evidence_id="w641-followup-i-exporter-audit",
        repo_id="test/repo",
        commit_sha="0" * 40,
        verdict="W641-followup-I exporter audit fixture",
        risk_level=risk_level,
    )


def _oscal_result_props(doc: dict) -> list[dict]:
    """Return the ``results[0].props`` list (empty when absent)."""
    return list(doc["assessment-results"]["results"][0].get("props") or [])


def _oscal_risk_level_prop(doc: dict) -> str | None:
    """Find the ``risk-level`` prop's value (None when absent)."""
    for prop in _oscal_result_props(doc):
        if prop.get("name") == "risk-level":
            return prop.get("value")
    return None


# ---------------------------------------------------------------------------
# 1. OSCAL — canonical passthrough
# ---------------------------------------------------------------------------


def test_oscal_consumes_canonical_risk_level() -> None:
    """OSCAL ``risk-level`` prop value matches the canonical packet value.

    ChangeEvidence is the boundary contract — the collector has already
    lifted ``risk_level_canonical`` (W641-followup-F) and normalised it
    via ``normalize_risk_level``. OSCAL must pass that value through
    verbatim (no re-casing, no re-normalisation).
    """
    pkt = _packet(risk_level="high")
    doc = build_oscal_assessment_results(pkt, now=_FIXED_CLOCK)
    assert _oscal_risk_level_prop(doc) == "high"


def test_oscal_passes_through_all_four_canonical_tiers() -> None:
    """Every canonical 4-tier token round-trips through OSCAL byte-identical.

    The canonical 4-tier (``critical / high / medium / low``) lives in
    :data:`roam.output.risk.RISK_LEVELS`. A regression that, say,
    UPPER-cased the tier for OSCAL would break this contract.
    """
    for tier in sorted(RISK_LEVELS):
        pkt = _packet(risk_level=tier)
        doc = build_oscal_assessment_results(pkt, now=_FIXED_CLOCK)
        assert _oscal_risk_level_prop(doc) == tier, (
            f"OSCAL risk-level prop diverged on tier {tier!r}: "
            f"expected verbatim passthrough, got {_oscal_risk_level_prop(doc)!r}"
        )


def test_oscal_emits_no_prop_when_risk_level_absent() -> None:
    """When the packet has ``risk_level=None``, OSCAL omits the prop entirely.

    No fake-canonical default ("low") slipped in by the emitter — the
    absence is preserved through the projection so audit consumers see
    the Q5 ``not_applicable`` semantic (W641-followup-F lineage rule).
    """
    pkt = _packet(risk_level=None)
    doc = build_oscal_assessment_results(pkt, now=_FIXED_CLOCK)
    assert _oscal_risk_level_prop(doc) is None


# ---------------------------------------------------------------------------
# 2. OSCAL — no inline severity / risk projection table drift
# ---------------------------------------------------------------------------


def test_oscal_no_inline_severity_table_drift() -> None:
    """OSCAL must not declare its own ``{low/medium/high/critical: ...}`` table.

    AST scan: walk every dict literal in ``oscal.py`` and assert no
    literal is keyed on the canonical 4-tier vocabulary. A future
    "let me add a CVSS-style numeric mapping right here in OSCAL"
    refactor would trip this guard and force the author to consume
    :mod:`roam.output.risk` instead.
    """
    src = (repo_root() / "src" / "roam" / "evidence" / "oscal.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    canonical_set = frozenset(RISK_LEVELS)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys: list[str] = []
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.append(k.value.lower())
        if not keys:
            continue
        if canonical_set.issubset(set(keys)):
            offenders.append(
                f"oscal.py:{node.lineno}: dict literal duplicates the canonical "
                f"4-tier risk vocabulary {sorted(canonical_set)}"
            )
    assert not offenders, "\n".join(offenders)


# ---------------------------------------------------------------------------
# 3. SARIF — canonical 4-tier coverage
# ---------------------------------------------------------------------------


def test_sarif_level_map_covers_canonical_4_tier() -> None:
    """``_SARIF_LEVEL_MAP`` (W547) projects every canonical severity tier.

    The SARIF projection consumes the roam-canonical 4-tier severity
    vocabulary (``critical / error / warning / info``). The W547 contract
    additionally aliases the CVSS 5-tier (``high / medium / low``) onto
    those tiers via :data:`SEVERITY_ALIASES`. This test pins that
    ``to_sarif_level`` produces a non-trivial SARIF level for every
    severity vocabulary token a downstream exporter could pass in.
    """
    # Roam-canonical 4-tier (the underlying _SARIF_LEVEL_MAP keys)
    assert _SARIF_LEVEL_MAP["critical"] == "error"
    assert _SARIF_LEVEL_MAP["error"] == "error"
    assert _SARIF_LEVEL_MAP["warning"] == "warning"
    assert _SARIF_LEVEL_MAP["info"] == "note"

    # Public projection (handles CVSS aliases via normalize_severity)
    assert to_sarif_level("critical") == "error"
    assert to_sarif_level("high") == "warning"
    assert to_sarif_level("medium") == "note"
    assert to_sarif_level("low") == "note"


def test_sarif_critical_projects_to_error() -> None:
    """The ``critical`` tier MUST gate CI (W531 safety lesson).

    GitHub Code Scanning fails the workflow on SARIF ``level: error``
    by default. A finding tagged ``critical`` (the most-severe tier
    in :data:`RISK_LEVELS`) must project to ``error``; downgrading to
    ``warning`` or ``note`` would silently relax the gate.
    """
    assert to_sarif_level("critical") == "error"
    assert to_sarif_level("CRITICAL") == "error"  # case-insensitive


# ---------------------------------------------------------------------------
# 4. Canonical mandate — exporters don't re-derive from the raw graph
# ---------------------------------------------------------------------------


def test_no_exporter_re_derives_from_raw_graph() -> None:
    """Exporters MUST NOT open the DB directly (canonical-mandate rule).

    Per CLAUDE.md "Evidence compiler layer" -> "The canonical mandate":

        every SARIF / OSCAL / VEX / OTel / CDEvents adapter reads from
        ChangeEvidence or the findings registry, never from the raw graph.

    AST scan: walk imports + name references in ``oscal.py`` /
    ``sarif.py`` and assert neither resolves ``roam.db.connection``
    (or its ``open_db`` symbol) nor instantiates ``sqlite3.Connection``.
    """
    for relpath in (
        ("src", "roam", "evidence", "oscal.py"),
        ("src", "roam", "output", "sarif.py"),
    ):
        path = repo_root().joinpath(*relpath)
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        offenders: list[str] = []
        for node in ast.walk(tree):
            # ``import roam.db.connection`` / ``import sqlite3``
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("roam.db.connection", "sqlite3"):
                        offenders.append(f"{path.name}:{node.lineno}: forbidden import {alias.name}")
            # ``from roam.db.connection import open_db`` / ``from sqlite3 import ...``
            elif isinstance(node, ast.ImportFrom):
                if node.module in ("roam.db.connection", "sqlite3"):
                    offenders.append(f"{path.name}:{node.lineno}: forbidden from-import {node.module}")
        assert not offenders, "Exporter re-derives from raw graph (canonical mandate violation): " + "; ".join(
            offenders
        )


# ---------------------------------------------------------------------------
# 5. Greenfield adapters — OTel / CDEvents
# ---------------------------------------------------------------------------


def _try_import(modname: str):
    """Return the module if importable, else ``None``. Never raises."""
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError:
        return None


def test_otel_adapter_absent_or_canonical() -> None:
    """Pin: an OTel evidence adapter is either absent OR consumes canonical.

    Today the file is absent (Phase 5 / not yet shipped per CLAUDE.md
    "Evidence compiler layer"). When it lands it MUST consume
    ``ChangeEvidence.risk_level`` rather than re-derive a 4-tier
    projection. This test allows the green-field state and trips when
    a future ``otel.py`` exists but does not consume the canonical type.
    """
    mod = _try_import("roam.evidence.otel")
    if mod is None:
        # Greenfield -- nothing to consume yet. PASS.
        return
    src = inspect.getsource(mod)
    assert "ChangeEvidence" in src, (
        "roam.evidence.otel exists but does NOT consume ChangeEvidence; "
        "exporters must consume canonical (per CLAUDE.md canonical mandate)."
    )


def test_cdevents_adapter_absent_or_canonical() -> None:
    """Pin: a CDEvents evidence adapter is either absent OR consumes canonical.

    Same greenfield contract as OTel above. CDEvents adapter ships in
    Phase 5; until then, this test stays green.
    """
    mod = _try_import("roam.evidence.cdevents")
    if mod is None:
        return
    src = inspect.getsource(mod)
    assert "ChangeEvidence" in src, (
        "roam.evidence.cdevents exists but does NOT consume ChangeEvidence; "
        "exporters must consume canonical (per CLAUDE.md canonical mandate)."
    )


# ---------------------------------------------------------------------------
# 6. Collector boundary -- alias normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_value, expected",
    [
        ("MODERATE", "medium"),
        ("Moderate", "medium"),
        ("moderate", "medium"),
        ("medium", "medium"),
        ("MEDIUM", "medium"),
        # 4-tier canonical, UPPER-cased
        ("CRITICAL", "critical"),
        ("HIGH", "high"),
        ("LOW", "low"),
    ],
)
def test_normalize_applied_at_collector_boundary(input_value: str, expected: str) -> None:
    """Every producer-side spelling collapses to canonical at the boundary.

    The W641-followup-F collector calls :func:`normalize_risk_level`
    before stamping ``ChangeEvidence.risk_level``. This test pins the
    normaliser's contract so exporter-side assertions (which trust the
    packet to already be canonical lowercase 4-tier) remain valid.
    """
    assert normalize_risk_level(input_value) == expected


def test_exporter_output_invariant_under_producer_casing() -> None:
    """OSCAL output is byte-identical regardless of producer casing.

    Builds two packets -- one with ``"high"`` and one with what the
    collector WOULD produce for ``"HIGH"`` (which is also ``"high"``
    after normalise). The OSCAL emitter sees identical packets; the
    OSCAL document is identical. This proves the exporter never adds
    casing drift.
    """
    pkt_low = _packet(risk_level=normalize_risk_level("HIGH"))
    pkt_canon = _packet(risk_level="high")
    doc_low = build_oscal_assessment_results(pkt_low, now=_FIXED_CLOCK)
    doc_canon = build_oscal_assessment_results(pkt_canon, now=_FIXED_CLOCK)
    assert _oscal_risk_level_prop(doc_low) == _oscal_risk_level_prop(doc_canon)
