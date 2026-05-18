"""W641-followup-F — collector prefers canonical risk-LEVEL over verdict-grep.

Pattern-3a structural close-out (final axis in the W641 cluster):

* W641              — ``cmd_pr_risk`` emits ``risk_level_canonical`` (third axis).
* W641-followup-A   — ``cmd_impact`` emits ``risk_level_canonical`` (fourth axis).
* W641-followup-B   — ``cmd_critique`` emits ``risk_level_canonical`` (fifth axis).
* W641-followup-C   — ``cmd_pr_bundle`` emits ``risk_level_canonical`` (sixth axis).
* W641-followup-D   — ``cmd_attest`` emits ``risk_level_canonical`` (seventh axis).
* W641-followup-F   — collector prefers the canonical axis when building
  :attr:`ChangeEvidence.risk_level`, closing the producer→packet projection
  loop. Without this wave, every W641 cluster producer emitted the canonical
  field but the collector silently re-derived ``risk_level`` from the legacy
  ``envelope["risk_level"]`` synthesis path, defeating the close-out.

This module pins the W641-followup-F collector contract:

* **Priority 1 (canonical, top-level).** ``envelope["risk_level_canonical"]``
  wins over the legacy ``envelope["risk_level"]`` synthesis.
* **Priority 2 (canonical, summary-nested).** ``envelope["summary"]["risk_level_canonical"]``
  wins when no top-level canonical mirror is present.
* **Priority 3 (legacy).** Falls back to ``envelope["risk_level"]`` /
  ``envelope["summary"]["risk_level"]`` for backward-compat with pre-W641
  envelopes.
* **Priority 4 (missing).** Neither source present → ``ChangeEvidence.risk_level
  is None`` (Q5 ``not_applicable`` semantic per
  :meth:`ChangeEvidence.evidence_completeness`).
* **Lineage disclosure.** The closed-enum lineage token (``canonical`` /
  ``verdict_text_legacy`` / ``missing``) is observable via
  :func:`roam.evidence.collector.resolve_risk_level_with_lineage`.
* **Cross-source divergence.** When canonical + legacy disagree, the
  collector emits a stable ``risk_level_divergence:<canonical>:<legacy>``
  warning so producer drift surfaces.
* **Normalization.** Every lane runs the resolved value through
  :func:`roam.output.risk.normalize_risk_level` (handles UPPER-case +
  ``MODERATE`` alias).

All tests are pure dict exercises — no DB, no filesystem, no CLI invocation.
"""

from __future__ import annotations

import pytest

from roam.evidence import ChangeEvidence, collect_change_evidence
from roam.evidence.collector import (
    RISK_LEVEL_LINEAGE_SOURCES,
    resolve_risk_level_with_lineage,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bundle_with(**fields) -> dict:
    """A minimal pr-bundle envelope; caller-passed kwargs override defaults."""
    env: dict = {
        "command": "pr-bundle",
        "schema": "roam-envelope-v1",
        "summary": {"verdict": "PR proof bundle complete"},
        "verdict": "SAFE",
    }
    # Lift caller fields onto the top-level envelope. ``summary`` is
    # deep-merged so callers can target ``summary.risk_level_canonical``
    # via ``summary={"risk_level_canonical": "high"}``.
    for k, v in fields.items():
        if k == "summary" and isinstance(v, dict):
            env["summary"] = {**env["summary"], **v}
        else:
            env[k] = v
    return env


# ---------------------------------------------------------------------------
# 1. Closed-enum lineage vocabulary
# ---------------------------------------------------------------------------


def test_lineage_vocabulary_is_closed_three_value_enum() -> None:
    """The lineage vocab must be the exact closed 3-value enum."""
    assert RISK_LEVEL_LINEAGE_SOURCES == frozenset({"canonical", "verdict_text_legacy", "missing"})


# ---------------------------------------------------------------------------
# 2. Priority 1 — canonical at top-level wins over legacy synthesis
# ---------------------------------------------------------------------------


def test_canonical_field_preferred_over_verdict_grep() -> None:
    """``risk_level_canonical="high"`` wins over legacy ``risk_level="low"``.

    The producer→packet projection loop: producers emit canonical, collector
    must lift canonical, not re-derive from legacy synthesis.
    """
    bundle = _bundle_with(
        risk_level_canonical="high",
        risk_level="low",  # legacy — should be SUPERSEDED
        verdict="SAFE TO MERGE",  # verdict text says safe but canonical says high
    )
    packet, _warnings = collect_change_evidence(pr_bundle_envelope=bundle)
    assert packet.risk_level == "high"


# ---------------------------------------------------------------------------
# 3. Priority 2 — ``summary.risk_level_canonical`` wins when only nested
# ---------------------------------------------------------------------------


def test_priority_2_summary_risk_level_canonical() -> None:
    """``summary.risk_level_canonical`` wins when no top-level mirror."""
    bundle = _bundle_with(
        # No top-level risk_level_canonical
        summary={"risk_level_canonical": "critical"},
        # Legacy fallback also present — should be SUPERSEDED.
        risk_level="medium",
    )
    packet, _warnings = collect_change_evidence(pr_bundle_envelope=bundle)
    assert packet.risk_level == "critical"


# ---------------------------------------------------------------------------
# 4. Priority 3 — legacy synthesis path preserved for backward-compat
# ---------------------------------------------------------------------------


def test_falls_back_to_verdict_grep_when_canonical_absent() -> None:
    """No canonical field → fall back to legacy ``risk_level`` field."""
    bundle = _bundle_with(
        risk_level="HIGH",  # UPPER-case to also exercise normalize
        verdict="NOT safe to merge (risk: HIGH)",
    )
    packet, _warnings = collect_change_evidence(pr_bundle_envelope=bundle)
    # Normalized to canonical lowercase.
    assert packet.risk_level == "high"


# ---------------------------------------------------------------------------
# 5. Priority 4 — neither source present → None
# ---------------------------------------------------------------------------


def test_safe_floor_no_canonical_no_verdict_text() -> None:
    """Missing both canonical AND legacy → ``packet.risk_level is None``.

    Q5 semantic: ``not_applicable`` (when verdict is SAFE + no findings)
    or ``missing`` (otherwise) — both surface as ``None`` on the packet.
    """
    bundle = _bundle_with()  # no risk_level fields at all
    packet, _warnings = collect_change_evidence(pr_bundle_envelope=bundle)
    assert packet.risk_level is None


# ---------------------------------------------------------------------------
# 6. Lineage marker — canonical source disclosure (helper-level)
# ---------------------------------------------------------------------------


def test_lineage_marker_canonical() -> None:
    """The public lineage helper reports ``canonical`` on Priority 1 / 2."""
    bundle = _bundle_with(risk_level_canonical="high", risk_level="low")
    value, source, divergence = resolve_risk_level_with_lineage(bundle)
    assert value == "high"
    assert source == "canonical"
    # Divergence warning fires because canonical and legacy disagree.
    assert divergence == "risk_level_divergence:high:low"

    # Priority 2 path — only summary nested.
    bundle2 = _bundle_with(summary={"risk_level_canonical": "medium"})
    value2, source2, divergence2 = resolve_risk_level_with_lineage(bundle2)
    assert value2 == "medium"
    assert source2 == "canonical"
    assert divergence2 is None  # no legacy to disagree with


def test_lineage_marker_legacy_verdict() -> None:
    """The lineage helper reports ``verdict_text_legacy`` on Priority 3."""
    bundle = _bundle_with(risk_level="medium")
    value, source, divergence = resolve_risk_level_with_lineage(bundle)
    assert value == "medium"
    assert source == "verdict_text_legacy"
    assert divergence is None


def test_lineage_marker_missing() -> None:
    """The lineage helper reports ``missing`` when neither source present."""
    bundle = _bundle_with()
    value, source, divergence = resolve_risk_level_with_lineage(bundle)
    assert value is None
    assert source == "missing"
    assert divergence is None


# ---------------------------------------------------------------------------
# 7. Divergence warning — surfaces producer drift loudly
# ---------------------------------------------------------------------------


def test_divergence_warning() -> None:
    """Canonical ``low`` + legacy ``HIGH`` → warning ``risk_level_divergence:low:high``.

    The "Make fallback chains loud" rule (CP45 / CP46) requires producer
    drift to be visible to consumers. The collector emits a stable
    string token in ``warnings`` so downstream tools can grep for it.
    """
    bundle = _bundle_with(risk_level_canonical="low", risk_level="HIGH")
    packet, warnings = collect_change_evidence(pr_bundle_envelope=bundle)
    # Canonical wins on the packet field.
    assert packet.risk_level == "low"
    # And the divergence warning fires.
    assert "risk_level_divergence:low:high" in warnings


def test_no_divergence_warning_when_canonical_legacy_agree() -> None:
    """Canonical + legacy AGREE → no divergence warning (silent happy path)."""
    bundle = _bundle_with(risk_level_canonical="medium", risk_level="medium")
    packet, warnings = collect_change_evidence(pr_bundle_envelope=bundle)
    assert packet.risk_level == "medium"
    # No divergence — happy path stays silent so legacy collector tests
    # asserting ``warnings == []`` keep passing.
    assert not any(w.startswith("risk_level_divergence:") for w in warnings)


# ---------------------------------------------------------------------------
# 8. Normalization — UPPER / mixed / MODERATE alias all normalize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HIGH", "high"),
        ("High", "high"),
        ("MODERATE", "medium"),  # W631 alias resolution
        ("Moderate", "medium"),
        ("  Low  ", "low"),
        ("CRITICAL", "critical"),
    ],
)
def test_normalize_applied(raw: str, expected: str) -> None:
    """All canonical-field inputs flow through ``normalize_risk_level``."""
    bundle = _bundle_with(risk_level_canonical=raw)
    packet, _warnings = collect_change_evidence(pr_bundle_envelope=bundle)
    assert packet.risk_level == expected


def test_normalize_applied_legacy_lane() -> None:
    """Legacy-lane resolves through ``normalize_risk_level`` too."""
    bundle = _bundle_with(risk_level="MODERATE")
    packet, _warnings = collect_change_evidence(pr_bundle_envelope=bundle)
    assert packet.risk_level == "medium"


# ---------------------------------------------------------------------------
# 9. Invariant — risk_level is always populated-or-None, never missing
# ---------------------------------------------------------------------------


def test_invariant_field_present() -> None:
    """``packet.risk_level`` is either a canonical string OR explicitly None.

    No bundle shape should produce an attribute-missing or attribute-error
    on ``ChangeEvidence.risk_level`` — the field is a typed ``str | None``.
    """
    for bundle in (
        _bundle_with(risk_level_canonical="high"),
        _bundle_with(risk_level="low"),
        _bundle_with(),  # neither
        _bundle_with(risk_level_canonical="bogus"),  # unknown canonical
        _bundle_with(risk_level="bogus"),  # unknown legacy
    ):
        packet, _warnings = collect_change_evidence(pr_bundle_envelope=bundle)
        assert hasattr(packet, "risk_level")
        # str OR None — both valid.
        assert packet.risk_level is None or isinstance(packet.risk_level, str)


# ---------------------------------------------------------------------------
# 10. Q5 completeness — evidence_completeness logic still works post-rewire
# ---------------------------------------------------------------------------


def test_q5_completeness_unchanged() -> None:
    """``evidence_completeness()`` Q5 still maps risk_level correctly.

    The collector rewrite must not perturb the W210 ``evidence_completeness``
    classifier — Q5 is ``complete`` when ``risk_level`` is populated,
    ``not_applicable`` when verdict is SAFE/PASS + no findings, ``missing``
    otherwise.
    """
    # Q5 complete: canonical risk-LEVEL populated.
    bundle_complete = _bundle_with(risk_level_canonical="high", verdict="NEEDS REVIEW")
    packet_complete, _ = collect_change_evidence(pr_bundle_envelope=bundle_complete)
    assert packet_complete.risk_level == "high"
    completeness = packet_complete.evidence_completeness()
    assert completeness["Q5"] == "complete"

    # Q5 not_applicable: no risk_level but SAFE verdict + no findings.
    bundle_na = _bundle_with(verdict="SAFE")
    packet_na, _ = collect_change_evidence(pr_bundle_envelope=bundle_na)
    assert packet_na.risk_level is None
    assert packet_na.evidence_completeness()["Q5"] == "not_applicable"


# ---------------------------------------------------------------------------
# 11. cmd_attest envelope: canonical-emitting producer flows through cleanly
# ---------------------------------------------------------------------------


def test_no_change_on_attest_envelope() -> None:
    """An attest-shaped envelope carrying ``risk_level_canonical`` flows through.

    cmd_attest (W641-followup-D) emits ``risk_level_canonical`` at the
    top-level. The collector should lift it via the canonical lane and
    leave the rest of the envelope untouched.
    """
    # Attest emits flat top-level fields (no nested summary block for
    # risk_level_canonical — the producer mirrors it at top level).
    bundle = _bundle_with(
        risk_level_canonical="high",
        risk_level="high",  # agreeing legacy mirror (no divergence)
        verdict="ATTESTED",
    )
    packet, warnings = collect_change_evidence(pr_bundle_envelope=bundle)
    assert packet.risk_level == "high"
    # No divergence warning when canonical + legacy agree (the standard
    # cmd_attest emission shape).
    assert not any(w.startswith("risk_level_divergence:") for w in warnings)


# ---------------------------------------------------------------------------
# 12. Helper isolation — pure-function shape
# ---------------------------------------------------------------------------


def test_helper_is_pure_function() -> None:
    """``resolve_risk_level_with_lineage`` does not mutate its input."""
    bundle = _bundle_with(risk_level_canonical="high", risk_level="low")
    snapshot = {k: (v if not isinstance(v, dict) else dict(v)) for k, v in bundle.items()}
    resolve_risk_level_with_lineage(bundle)
    # Top-level keys preserved.
    assert set(bundle.keys()) == set(snapshot.keys())
    # No mutation on any non-dict scalar.
    for k, v in snapshot.items():
        if not isinstance(v, dict):
            assert bundle[k] == v


# ---------------------------------------------------------------------------
# 13. Empty-string / bogus-string defenses
# ---------------------------------------------------------------------------


def test_empty_string_canonical_falls_through_to_legacy() -> None:
    """An empty ``risk_level_canonical=""`` should NOT mask the legacy field.

    ``_coalesce`` treats empty-string as not-provided; the legacy lane
    then fires.
    """
    bundle = _bundle_with(risk_level_canonical="", risk_level="medium")
    packet, _ = collect_change_evidence(pr_bundle_envelope=bundle)
    assert packet.risk_level == "medium"


def test_bogus_canonical_falls_through_to_legacy() -> None:
    """A canonical field carrying an unknown label normalizes to None and
    the legacy lane fires (defense against pre-W631 producer drift)."""
    bundle = _bundle_with(risk_level_canonical="bogus_label", risk_level="HIGH")
    packet, _ = collect_change_evidence(pr_bundle_envelope=bundle)
    # canonical normalizes to None → legacy lane → "high"
    assert packet.risk_level == "high"


# ---------------------------------------------------------------------------
# 14. Sanity: packet still constructs as ChangeEvidence
# ---------------------------------------------------------------------------


def test_packet_is_change_evidence() -> None:
    """The collector still returns a typed ChangeEvidence — no regression."""
    bundle = _bundle_with(risk_level_canonical="high")
    packet, _ = collect_change_evidence(pr_bundle_envelope=bundle)
    assert isinstance(packet, ChangeEvidence)
