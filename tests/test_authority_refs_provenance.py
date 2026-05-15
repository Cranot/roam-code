"""W292 - tests for AuthorityRef provenance wiring.

The W292 directive wires the W282 :func:`provenance_label` helper onto
``AuthorityRef.extra["provenance"]`` so each authority claim records
WHICH source produced this specific value. Scope: ``authority_refs``
only. Policy decisions / approvals provenance is W293.

Critical semantic distinction (pinned by
:func:`test_authority_ref_source_field_preserved_alongside_provenance`):

* ``AuthorityRef.source`` is from the W211 ``AUTHORITY_SOURCES`` enum (6
  values). It names the AUTHORITY KIND CATEGORY.
* ``AuthorityRef.extra["provenance"]`` is from the W282
  ``PROVENANCE_SOURCES`` enum (10 values). It names the DATA CHANNEL
  that produced the value.

These two fields are independently load-bearing and must NOT be merged.
A mode authority can have ``source="mode"`` (category) AND
``provenance="run_ledger"`` (channel: HMAC-verified ledger event
recorded the mode).

The deterministic precedence table this module pins:

* HMAC-verified run-ledger entry        -> ``run_ledger`` (WINS)
* Permit from envelope (no ledger)      -> ``producer_envelope(permit)``
* Mode from envelope (no ledger)        -> ``producer_envelope(mode)``
* Lease from envelope (no ledger)       -> ``producer_envelope(lease)``
* Rule from envelope (no ledger)        -> ``producer_envelope(rule)``
* Approval from envelope (no ledger)    -> ``producer_envelope(approval)``
* Nothing / unknown source              -> ``unknown``

Every emitted provenance source value MUST live in the closed
:data:`PROVENANCE_SOURCES` frozenset (drift guard at module end).
"""

from __future__ import annotations

from roam.evidence import PROVENANCE_SOURCES, AuthorityRef
from roam.evidence._vocabulary import AUTHORITY_SOURCES
from roam.evidence.collector import (
    _build_authority_refs,
    _resolve_authority_provenance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provenance_base(label: str) -> str:
    """Strip the optional ``"(detail)"`` suffix from a provenance label.

    The W282 helper emits either bare ``"run_ledger"`` or compact
    ``"producer_envelope(permit)"`` - both forms validate against
    :data:`PROVENANCE_SOURCES`. The drift guard checks the base.
    """
    return label.split("(", 1)[0]


# ---------------------------------------------------------------------------
# Channel-mapping tests (one per priority row)
# ---------------------------------------------------------------------------


def test_mode_authority_has_producer_envelope_provenance() -> None:
    """Mode from envelope, no run-ledger -> ``producer_envelope(mode)``."""
    refs = _build_authority_refs(
        pr_bundle_envelope={"mode": "safe_edit"},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    mode_refs = [r for r in refs if r.authority_kind == "mode"]
    assert mode_refs, "expected at least one mode AuthorityRef"
    target = mode_refs[0]
    assert target.authority_id == "safe_edit"
    assert target.extra.get("provenance") == "producer_envelope(mode)"


def test_mode_authority_with_run_ledger_evidence_promotes_to_run_ledger() -> None:
    """Mode in envelope AND in verified ledger event -> ``run_ledger`` wins."""
    refs = _build_authority_refs(
        pr_bundle_envelope={"mode": "safe_edit"},
        caller_mode=None,
        corroborated_authorities=frozenset({("mode", "safe_edit")}),
    )
    target = next(r for r in refs if r.authority_kind == "mode")
    assert target.authority_id == "safe_edit"
    # run_ledger beats producer_envelope per the W292 precedence table.
    assert target.extra.get("provenance") == "run_ledger"


def test_permit_authority_has_producer_envelope_provenance() -> None:
    """Permit from disk-loaded envelope -> ``producer_envelope(permit)``."""
    refs = _build_authority_refs(
        pr_bundle_envelope={
            "permits": [{"permit_id": "perm_20260514_abc123"}],
        },
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    permit_refs = [r for r in refs if r.authority_kind == "permit"]
    assert permit_refs, "expected at least one permit AuthorityRef"
    target = permit_refs[0]
    assert target.authority_id == "perm_20260514_abc123"
    assert target.extra.get("provenance") == "producer_envelope(permit)"


def test_permit_authority_with_run_ledger_promotes_to_run_ledger() -> None:
    """Permit in envelope + verified ledger entry -> ``run_ledger``."""
    refs = _build_authority_refs(
        pr_bundle_envelope={
            "permits": [{"permit_id": "perm_xyz"}],
        },
        caller_mode=None,
        corroborated_authorities=frozenset({("permit", "perm_xyz")}),
    )
    target = next(r for r in refs if r.authority_kind == "permit")
    assert target.authority_id == "perm_xyz"
    assert target.extra.get("provenance") == "run_ledger"


def test_lease_authority_has_producer_envelope_provenance() -> None:
    """Lease from envelope, no ledger -> ``producer_envelope(lease)``."""
    refs = _build_authority_refs(
        pr_bundle_envelope={
            "leases": [{"lease_id": "lease_42"}],
        },
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    target = next(r for r in refs if r.authority_kind == "lease")
    assert target.authority_id == "lease_42"
    assert target.extra.get("provenance") == "producer_envelope(lease)"


def test_lease_authority_with_run_ledger_promotes_to_run_ledger() -> None:
    """Lease in envelope + verified ledger -> ``run_ledger``."""
    refs = _build_authority_refs(
        pr_bundle_envelope={
            "leases": [{"lease_id": "lease_99"}],
        },
        caller_mode=None,
        corroborated_authorities=frozenset({("lease", "lease_99")}),
    )
    target = next(r for r in refs if r.authority_kind == "lease")
    assert target.extra.get("provenance") == "run_ledger"


def test_rule_authority_has_producer_envelope_provenance() -> None:
    """Rule from envelope, no ledger -> ``producer_envelope(rule)``."""
    refs = _build_authority_refs(
        pr_bundle_envelope={
            "rules_passed": [{"rule_id": "no-print-statements"}],
        },
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    target = next(r for r in refs if r.authority_kind == "policy_rule")
    assert target.authority_id == "no-print-statements"
    assert target.extra.get("provenance") == "producer_envelope(rule)"


def test_approval_authority_has_producer_envelope_provenance() -> None:
    """Approval from envelope, no ledger -> ``producer_envelope(approval)``."""
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
    assert target.authority_id == "appr_pr42_review1"
    assert target.extra.get("provenance") == "producer_envelope(approval)"
    # granted_by also threads through, unchanged from W211.
    assert target.granted_by == "human:alice@example.com"


def test_caller_mode_only_has_producer_envelope_provenance() -> None:
    """Caller kwarg-only mode (no envelope row) still tags ``producer_envelope(mode)``.

    The W292 envelope_source attribution treats caller_mode the same
    as an envelope mode row - the caller is the surface that knew
    about the mode, so ``producer_envelope(mode)`` is the honest tag.
    The alternative (``inferred``) would underclaim the signal.
    """
    refs = _build_authority_refs(
        pr_bundle_envelope=None,
        caller_mode="safe_edit",
        corroborated_authorities=frozenset(),
    )
    target = next(r for r in refs if r.authority_kind == "mode")
    assert target.authority_id == "safe_edit"
    assert target.extra.get("provenance") == "producer_envelope(mode)"


def test_authority_ref_source_field_preserved_alongside_provenance() -> None:
    """Drift guard: ``source`` and ``extra["provenance"]`` are DISTINCT fields.

    ``AuthorityRef.source`` (W211 AUTHORITY_SOURCES) names the
    AUTHORITY KIND CATEGORY. ``extra["provenance"]`` (W282
    PROVENANCE_SOURCES) names the DATA CHANNEL. They are not
    synonyms; merging them would erase information.

    W294 update: ``_build_authority_refs`` now populates ``source``
    distinctly per the authority-kind mapping
    (mode -> ``"mode"``, permit -> ``"permit"``,
    policy_rule -> ``"rule_config"``, approval -> ``"human_approval"``).
    The mode test below pins the new source value while still proving
    source != provenance.
    """
    refs = _build_authority_refs(
        pr_bundle_envelope={"mode": "safe_edit"},
        caller_mode=None,
        corroborated_authorities=frozenset({("mode", "safe_edit")}),
    )
    target = next(r for r in refs if r.authority_kind == "mode")

    # W294: mode AuthorityRef carries source="mode" per the closed
    # AUTHORITY_SOURCES vocabulary (NOT the legacy ``inferred_fallback``
    # default).
    assert target.source in AUTHORITY_SOURCES
    assert target.source == "mode"

    # provenance carries the W282 channel label - a DIFFERENT value
    # from source. Both are populated and complementary.
    provenance = target.extra.get("provenance")
    assert provenance == "run_ledger"
    assert _provenance_base(provenance) in PROVENANCE_SOURCES

    # Crucially: the two values answer different questions. The source
    # field doesn't tell you the value came from the ledger; the
    # provenance field doesn't tell you it's a mode-kind authority.
    assert target.source != provenance


def test_duplicate_authority_precedence_is_deterministic() -> None:
    """Same authority via multiple channels - precedence stays consistent.

    The dedup pass keeps the first sighting (mode emission), so an
    authority that ALSO appears in the verified ledger picks up
    ``run_ledger`` provenance. The result must be byte-identical
    across multiple invocations (no dict-iteration-order flakiness).
    """
    envelope = {
        "mode": "safe_edit",
        "permits": [{"permit_id": "perm_alpha"}],
        "leases": [{"lease_id": "lease_beta"}],
    }
    corroborated = frozenset({
        ("mode", "safe_edit"),
        ("permit", "perm_alpha"),
        # lease_beta NOT in ledger -> stays at producer_envelope(lease)
    })

    # Run the resolver three times and assert byte-identical output.
    runs = []
    for _ in range(3):
        refs = _build_authority_refs(
            pr_bundle_envelope=envelope,
            caller_mode=None,
            corroborated_authorities=corroborated,
        )
        runs.append([
            (r.authority_kind, r.authority_id, r.extra.get("provenance"))
            for r in refs
        ])

    # Determinism: every run produces the same sequence.
    assert runs[0] == runs[1] == runs[2]

    by_kind = {r.authority_kind: r for r in refs}
    assert by_kind["mode"].extra.get("provenance") == "run_ledger"
    assert by_kind["permit"].extra.get("provenance") == "run_ledger"
    assert by_kind["lease"].extra.get("provenance") == "producer_envelope(lease)"


def test_resolver_run_ledger_wins_over_envelope() -> None:
    """Unit test of the resolver: corroboration short-circuits envelope tags."""
    # mode + envelope + ledger -> run_ledger (corroboration wins)
    p = _resolve_authority_provenance(
        authority_kind="mode",
        authority_id="safe_edit",
        envelope_source="mode",
        corroborated_in_run_ledger=True,
    )
    assert p == "run_ledger"

    # Same kind/id without ledger -> producer_envelope(mode)
    p = _resolve_authority_provenance(
        authority_kind="mode",
        authority_id="safe_edit",
        envelope_source="mode",
        corroborated_in_run_ledger=False,
    )
    assert p == "producer_envelope(mode)"

    # No envelope, no ledger -> unknown
    p = _resolve_authority_provenance(
        authority_kind="mode",
        authority_id=None,
        envelope_source=None,
        corroborated_in_run_ledger=False,
    )
    assert p == "unknown"


def test_resolver_emits_only_PROVENANCE_SOURCES_values() -> None:
    """Resolver output must always parse against the closed enum."""
    cases = [
        # (envelope_source, corroborated, expected base)
        (None, True, "run_ledger"),
        ("mode", True, "run_ledger"),
        ("permit", True, "run_ledger"),
        ("audit_trail", False, "audit_trail"),
        ("mcp_receipt", False, "mcp_receipt"),
        ("mode", False, "producer_envelope"),
        ("permit", False, "producer_envelope"),
        ("rule", False, "producer_envelope"),
        ("lease", False, "producer_envelope"),
        ("approval", False, "producer_envelope"),
        ("producer_envelope", False, "producer_envelope"),
        ("inferred", False, "inferred"),
        (None, False, "unknown"),
        ("not-a-real-channel", False, "unknown"),
    ]
    for src, corrob, expected_base in cases:
        label = _resolve_authority_provenance(
            authority_kind="mode",
            authority_id="x",
            envelope_source=src,
            corroborated_in_run_ledger=corrob,
        )
        base = _provenance_base(label)
        assert base in PROVENANCE_SOURCES, (
            f"resolver emitted {label!r} (base {base!r}) not in "
            f"PROVENANCE_SOURCES"
        )
        assert base == expected_base, (
            f"resolver({src!r}, corrob={corrob}) -> {label!r}, "
            f"expected base {expected_base!r}"
        )


# ---------------------------------------------------------------------------
# Drift guard - every test's emitted provenance source belongs to the
# closed PROVENANCE_SOURCES vocabulary
# ---------------------------------------------------------------------------


def test_authority_provenance_uses_only_PROVENANCE_SOURCES_values() -> None:
    """Every AuthorityRef provenance label validates against the closed enum.

    Builds a fanout of AuthorityRefs covering every priority channel
    in this module, strips the ``"(detail)"`` suffix from each label,
    and asserts the base source is in ``PROVENANCE_SOURCES``. A
    future drift (e.g. someone changes ``"producer_envelope"`` ->
    ``"producer_env"``) trips this guard immediately.
    """
    # Channel 1 - run_ledger (mode in envelope AND ledger)
    refs_ledger = _build_authority_refs(
        pr_bundle_envelope={"mode": "safe_edit"},
        caller_mode=None,
        corroborated_authorities=frozenset({("mode", "safe_edit")}),
    )

    # Channel 2 - producer_envelope(mode)
    refs_mode = _build_authority_refs(
        pr_bundle_envelope={"mode": "read_only"},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )

    # Channel 3 - producer_envelope(permit)
    refs_permit = _build_authority_refs(
        pr_bundle_envelope={"permits": [{"permit_id": "perm_x"}]},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )

    # Channel 4 - producer_envelope(lease)
    refs_lease = _build_authority_refs(
        pr_bundle_envelope={"leases": [{"lease_id": "lease_y"}]},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )

    # Channel 5 - producer_envelope(rule)
    refs_rule = _build_authority_refs(
        pr_bundle_envelope={"rules_passed": [{"rule_id": "rule_z"}]},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )

    # Channel 6 - producer_envelope(approval)
    refs_appr = _build_authority_refs(
        pr_bundle_envelope={
            "approvals": [{"approval_id": "appr_q"}],
        },
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )

    all_refs: list[AuthorityRef] = []
    all_refs.extend(refs_ledger)
    all_refs.extend(refs_mode)
    all_refs.extend(refs_permit)
    all_refs.extend(refs_lease)
    all_refs.extend(refs_rule)
    all_refs.extend(refs_appr)
    assert all_refs, "fanout produced no AuthorityRefs - test bug"

    for r in all_refs:
        label = r.extra.get("provenance")
        assert isinstance(label, str) and label, (
            f"AuthorityRef missing extra['provenance']: {r!r}"
        )
        base = _provenance_base(label)
        assert base in PROVENANCE_SOURCES, (
            f"AuthorityRef provenance base {base!r} (from label {label!r}) "
            f"is not in PROVENANCE_SOURCES"
        )
