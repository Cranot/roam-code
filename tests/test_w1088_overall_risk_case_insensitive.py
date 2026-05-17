"""W1088 — ``_overall_risk`` must resolve case-mixed severity inputs.

W759 lowercased four envelope-slot severities in ``cmd_preflight``
(``"low"`` / ``"warning"``). The rank-table ``_SEVERITY_ORDER`` was
UPPER-keyed, so the W759 lowercase values silently missed the lookup
and collapsed to the dict-default 0. The collapse was functionally
equivalent at the MEDIUM threshold but a Pattern-2 silent-fallback —
this regression-pin proves both case forms resolve identically and an
unknown token still hits the default.
"""

from __future__ import annotations

from roam.commands.cmd_preflight import _SEVERITY_ORDER, _overall_risk


def test_w1088_lowercase_and_upper_resolve_identically() -> None:
    """W759 lowercase + W847 UPPER inputs hit the same rank."""
    assert _overall_risk("low") == _overall_risk("LOW") == "LOW"
    assert _overall_risk("warning") == _overall_risk("WARNING") == "MEDIUM"
    assert _overall_risk("high") == _overall_risk("HIGH") == "HIGH"
    assert _overall_risk("critical") == _overall_risk("CRITICAL") == "CRITICAL"
    assert _overall_risk("medium") == _overall_risk("MEDIUM") == "MEDIUM"


def test_w1088_unknown_token_preserves_default_floor() -> None:
    """Bogus labels still resolve to rank 0 (verdict ``LOW``)."""
    assert _overall_risk("bogus") == "LOW"
    assert _SEVERITY_ORDER.get("bogus", 0) == 0


def test_w1088_mixed_case_inputs_pick_worst_rank() -> None:
    """Worst severity wins even when inputs span case forms."""
    assert _overall_risk("low", "HIGH", "warning") == "HIGH"
    assert _overall_risk("LOW", "critical", "medium") == "CRITICAL"
