"""W294 - tests for AuthorityRef.source population.

The W292 directive wired ``AuthorityRef.extra["provenance"]`` with the
W282 channel label but did NOT populate ``AuthorityRef.source``. Every
AuthorityRef therefore fell back to the W211 default
``"inferred_fallback"`` regardless of the authority kind. W294 closes
that gap: ``_build_authority_refs`` now passes ``source=...`` per the
closed-enum mapping:

* ``mode`` AuthorityRef         -> ``source="mode"``
* ``permit`` AuthorityRef       -> ``source="permit"`` (+ ``extra["permit_id"]``
                                    when the producer carries a real id)
* ``policy_rule`` AuthorityRef  -> ``source="rule_config"``
* ``approval`` AuthorityRef     -> ``source="human_approval"``
* ``lease`` AuthorityRef        -> ``source="inferred_fallback"`` (INTENTIONAL
                                    asymmetry - AUTHORITY_SOURCES has no
                                    ``lease`` entry today; provenance stays
                                    precise as ``producer_envelope(lease)``)

These tests pin the mapping AND the source/provenance independence so a
future refactor can't silently collapse the two axes.
"""

from __future__ import annotations

from roam.evidence._vocabulary import AUTHORITY_SOURCES, PROVENANCE_SOURCES
from roam.evidence.collector import _build_authority_refs


# ---------------------------------------------------------------------------
# Per-authority-kind source mapping
# ---------------------------------------------------------------------------


def test_mode_authority_has_source_mode() -> None:
    """Mode AuthorityRef carries source="mode" (NOT the legacy default)."""
    refs = _build_authority_refs(
        pr_bundle_envelope={"mode": "safe_edit"},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    target = next(r for r in refs if r.authority_kind == "mode")
    assert target.source == "mode"
    assert target.source in AUTHORITY_SOURCES


def test_permit_authority_has_source_permit_and_extra_permit_id() -> None:
    """Permit with real permit_id carries source="permit" + extra["permit_id"]."""
    refs = _build_authority_refs(
        pr_bundle_envelope={
            "permits": [{"permit_id": "perm_20260514_abc123"}],
        },
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    target = next(r for r in refs if r.authority_kind == "permit")
    assert target.source == "permit"
    # W294: when the producer carried a real ``permit_id``, the collector
    # stamps it onto ``extra["permit_id"]`` so the row is distinguishable
    # from a W198 facade row (which would auto-stamp ``extra["facade"]``).
    assert target.extra.get("permit_id") == "perm_20260514_abc123"
    # Belt-and-braces: facade flag must NOT be set when a real id is
    # present (the AuthorityRef.__post_init__ only sets facade when
    # source="permit" AND no permit_id in extra).
    assert not target.extra.get("facade")


def test_permit_authority_facade_when_no_real_permit_id() -> None:
    """Permit row with only ``id`` (no ``permit_id``) lands on the W198 facade path.

    Pins the intentional asymmetry: when the envelope carries an entry
    that has ``id`` but no ``permit_id``, the collector does NOT stamp
    ``extra["permit_id"]``. The AuthorityRef.__post_init__ then sees
    source="permit" without a permit_id and auto-stamps
    ``extra["facade"] = True`` per the W198 facade-detection contract.
    """
    refs = _build_authority_refs(
        pr_bundle_envelope={
            # ``id`` only - no ``permit_id`` key. The collector's
            # ``_entry_id`` falls through to ``id`` for the authority_id,
            # but the W294 ``extra["permit_id"]`` stamp only fires when
            # the entry has a real ``permit_id`` key.
            "permits": [{"id": "synthetic_placeholder"}],
        },
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    target = next(r for r in refs if r.authority_kind == "permit")
    assert target.source == "permit"
    assert "permit_id" not in target.extra
    assert target.extra.get("facade") is True


def test_policy_rule_authority_has_source_rule_config() -> None:
    """Policy-rule AuthorityRef carries source="rule_config"."""
    refs = _build_authority_refs(
        pr_bundle_envelope={
            "rules_passed": [{"rule_id": "no-print-statements"}],
        },
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    target = next(r for r in refs if r.authority_kind == "policy_rule")
    assert target.source == "rule_config"
    assert target.source in AUTHORITY_SOURCES


def test_approval_authority_has_source_human_approval() -> None:
    """Approval AuthorityRef carries source="human_approval"."""
    refs = _build_authority_refs(
        pr_bundle_envelope={
            "approvals": [
                {"approval_id": "appr_pr42_review1",
                 "approver": "human:alice@example.com"},
            ],
        },
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    target = next(r for r in refs if r.authority_kind == "approval")
    assert target.source == "human_approval"
    assert target.source in AUTHORITY_SOURCES
    # granted_by still threads through unchanged from W211.
    assert target.granted_by == "human:alice@example.com"


def test_lease_authority_keeps_source_inferred_fallback_but_provenance_is_lease() -> None:
    """Lease AuthorityRef intentionally keeps the default source value.

    The closed AUTHORITY_SOURCES enum has no ``"lease"`` entry; expanding
    it would be a deliberate vocabulary decision for a future wave. The
    asymmetry is intentional and load-bearing: a lease AuthorityRef
    answers the category question with ``"inferred_fallback"`` (we don't
    have a category for it) but answers the channel question precisely
    with ``"producer_envelope(lease)"``.

    This test pins the asymmetry. If a future wave decides to extend
    AUTHORITY_SOURCES, BOTH this test AND the corresponding branch in
    ``_build_authority_refs`` need to be updated together.
    """
    refs = _build_authority_refs(
        pr_bundle_envelope={
            "leases": [{"lease_id": "lease_42"}],
        },
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    target = next(r for r in refs if r.authority_kind == "lease")
    # Category answer: closed-enum has no ``lease`` literal today.
    assert target.source == "inferred_fallback"
    # Channel answer: still precise.
    assert target.extra.get("provenance") == "producer_envelope(lease)"


# ---------------------------------------------------------------------------
# Drift guard: source and extra["provenance"] are independent fields
# ---------------------------------------------------------------------------


def test_source_and_provenance_are_independent_fields() -> None:
    """Drift guard: source and extra["provenance"] are NOT synonyms.

    The two axes answer different questions and must stay independently
    populated. A fan-out across multiple authority kinds proves that for
    every kind, the source literal and the provenance literal come from
    different vocabularies (the small overlap on ``mode`` and ``permit``
    is coincidental string-equality - the enumerations are still
    distinct closed sets).
    """
    refs = _build_authority_refs(
        pr_bundle_envelope={
            "mode": "safe_edit",
            "permits": [{"permit_id": "perm_alpha"}],
            "leases": [{"lease_id": "lease_beta"}],
            "rules_passed": [{"rule_id": "no-debug-prints"}],
            "approvals": [
                {"approval_id": "appr_pr42",
                 "approver": "human:alice@example.com"},
            ],
        },
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )

    by_kind = {r.authority_kind: r for r in refs}
    assert set(by_kind) == {"mode", "permit", "lease", "policy_rule", "approval"}

    # Each AuthorityRef carries a non-empty source literal that belongs
    # to AUTHORITY_SOURCES, and a non-empty provenance label whose base
    # belongs to PROVENANCE_SOURCES.
    for ref in by_kind.values():
        assert ref.source in AUTHORITY_SOURCES, (
            f"source={ref.source!r} for kind={ref.authority_kind!r} not in "
            f"AUTHORITY_SOURCES"
        )
        provenance = ref.extra.get("provenance")
        assert isinstance(provenance, str) and provenance, (
            f"missing extra['provenance'] for kind={ref.authority_kind!r}"
        )
        # Strip the optional ``"(detail)"`` suffix for the membership check.
        prov_base = provenance.split("(", 1)[0]
        assert prov_base in PROVENANCE_SOURCES, (
            f"provenance base {prov_base!r} for kind={ref.authority_kind!r} "
            f"not in PROVENANCE_SOURCES"
        )

    # mode: source="mode" (closed enum literal), provenance="producer_envelope(mode)"
    assert by_kind["mode"].source == "mode"
    assert by_kind["mode"].extra["provenance"] == "producer_envelope(mode)"
    # The two strings happen to share the substring "mode" but the
    # provenance carries a "(mode)" suffix that puts them in different
    # closed enumerations. The drift guard is the AUTHORITY_SOURCES /
    # PROVENANCE_SOURCES membership above.
    assert by_kind["mode"].source != by_kind["mode"].extra["provenance"]

    # permit: source="permit", provenance="producer_envelope(permit)"
    assert by_kind["permit"].source == "permit"
    assert by_kind["permit"].extra["provenance"] == "producer_envelope(permit)"
    assert by_kind["permit"].source != by_kind["permit"].extra["provenance"]

    # lease: source="inferred_fallback" (asymmetric), provenance="producer_envelope(lease)"
    assert by_kind["lease"].source == "inferred_fallback"
    assert by_kind["lease"].extra["provenance"] == "producer_envelope(lease)"

    # policy_rule: source="rule_config", provenance="producer_envelope(rule)"
    assert by_kind["policy_rule"].source == "rule_config"
    assert by_kind["policy_rule"].extra["provenance"] == "producer_envelope(rule)"

    # approval: source="human_approval", provenance="producer_envelope(approval)"
    assert by_kind["approval"].source == "human_approval"
    assert by_kind["approval"].extra["provenance"] == "producer_envelope(approval)"


def test_caller_mode_only_path_still_populates_source_mode() -> None:
    """The caller-kwarg-only path (no envelope) still populates source="mode".

    W292 noted the caller-mode path tags ``producer_envelope(mode)`` on
    provenance. W294 extends this: the source ALSO becomes ``"mode"``
    because the authority kind is unambiguous even when only the caller
    surface knew about it.
    """
    refs = _build_authority_refs(
        pr_bundle_envelope=None,
        caller_mode="safe_edit",
        corroborated_authorities=frozenset(),
    )
    target = next(r for r in refs if r.authority_kind == "mode")
    assert target.source == "mode"
    assert target.extra.get("provenance") == "producer_envelope(mode)"
