"""W1261 — VSA consumer wire-up for the W210/W1254 ``evidence_stale`` axis.

W1254 augmented :meth:`ChangeEvidence.assurance_floor` to return
``stale`` + ``stale_reasons`` alongside ``passes`` + ``missing``.
:mod:`roam.attest.vsa` was the canonical downstream reader called out
by name in the W1254 docstring as the "already-consumes-only-passes-
and-missing" target — i.e. the place the additive shape had to keep
green. W1261 closes the consumer loop: ``_verification_result`` now
gates PASSED on BOTH coverage (``passes``) AND freshness
(``stale != True``).

Pattern-2 discipline ("absence beats silent success"): a stale-but-
MVA-complete packet had been silently attesting as PASSED via
``build_vsa_predicate(...).verificationResult``. After W1261 the same
packet attests FAILED, mirroring the existing high-risk downgrade.

Hash-stability invariant: this change is read-only inside
``_verification_result``. The VSA predicate's ``verificationResult``
field is downstream of the ChangeEvidence packet, so the packet's
canonical JSON + content hash are untouched. Two W210 timestamp /
stale fields on the dataclass already participate in
``_W210_OMIT_WHEN_DEFAULT_FIELDS``, so a packet that doesn't populate
them still serialises byte-identically to a pre-W210 packet. The
attestation predicate built from such a packet is itself byte-stable
on every field other than ``verificationResult`` when staleness flips.
"""

from __future__ import annotations

import dataclasses

from roam.attest.vsa import build_vsa_predicate
from roam.evidence.change_evidence import ChangeEvidence
from roam.evidence.refs import ActorRef, AuthorityRef
from roam.evidence.subject import EvidenceSubject


def _full_packet(**overrides) -> ChangeEvidence:
    """Build an MVA-complete ChangeEvidence (all six floor axes populated).

    Mirrors the ``_minimal_evidence`` fixture in ``test_attest_vsa.py``
    but kept local so the W1261 test file is self-contained.
    """
    kwargs: dict = {
        "evidence_id": "ev_w1261_full",
        "repo_id": "https://github.com/example/example",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "diff_hash": "deadbeef" * 8,
        "verdict": "PASS",
        "risk_level": "low",
        "agent_id": "claude",
        "actor_refs": (
            ActorRef(
                actor_kind="agent",
                actor_id="claude",
                trust_tier="self_reported_agent",
            ),
        ),
        "authority_refs": (AuthorityRef(authority_kind="mode", authority_id="safe_edit"),),
        "changed_subjects": (EvidenceSubject(kind="file", qualified_name="src/foo.py"),),
        "findings": ({"rule_id": "test-rule", "severity": "low", "subject_kind": "file"},),
        "tests_run": ({"test_id": "test_foo", "passed": True},),
        "policy_decisions": ({"rule_id": "policy-x", "decision": "pass"},),
    }
    kwargs.update(overrides)
    return ChangeEvidence(**kwargs).with_content_hash()


# ---------------------------------------------------------------------------
# W1261 - the four canonical cells of the (stale x floor) truth table.
# ---------------------------------------------------------------------------


def test_non_stale_passing_floor_attests_passed() -> None:
    """Baseline: non-stale + passing-floor -> PASSED.

    Pins the pre-W1261 behaviour on a fresh-and-complete packet so a
    future refactor that over-applies the stale gate trips here.
    """
    ev = _full_packet()
    floor = ev.assurance_floor()
    assert floor["passes"] is True
    assert floor["stale"] is False

    pred = build_vsa_predicate(ev)
    assert pred["verificationResult"] == "PASSED"


def test_stale_passing_floor_attests_failed() -> None:
    """The W1261 invariant: stale + passing-floor -> FAILED.

    The single load-bearing test. Before W1261 this returned PASSED
    because ``_verification_result`` only read ``passes``. After W1261
    the stale axis pulls the verdict to FAILED, mirroring the existing
    risk_level high/critical downgrade pattern.
    """
    fresh = _full_packet()
    stale = dataclasses.replace(
        fresh,
        evidence_stale=True,
        stale_reasons=("context_read_at (2026-05-16T10:00:00Z) >= edits_started_at (2026-05-16T09:30:00Z)",),
    )
    # Sanity: floor still passes coverage-wise; the only change is stale.
    floor = stale.assurance_floor()
    assert floor["passes"] is True
    assert floor["stale"] is True

    pred = build_vsa_predicate(stale)
    assert pred["verificationResult"] == "FAILED", (
        "W1261: stale evidence MUST attest FAILED even when the MVA floor passes; pre-W1261 silently emitted PASSED."
    )


def test_stale_failing_floor_attests_failed() -> None:
    """Stale + failing-floor -> FAILED. Stale-precedence sanity.

    Either signal alone is sufficient for FAILED; both together must
    not somehow cancel. Pins the precedence ordering inside
    ``_verification_result`` (stale checked before passes).
    """
    bare = ChangeEvidence(
        evidence_id="ev_w1261_stale_bare",
        evidence_stale=True,
        stale_reasons=("preflight_older_than_edits",),
    ).with_content_hash()
    floor = bare.assurance_floor()
    assert floor["passes"] is False
    assert floor["stale"] is True

    pred = build_vsa_predicate(bare)
    assert pred["verificationResult"] == "FAILED"


def test_non_stale_failing_floor_attests_failed() -> None:
    """Non-stale + failing-floor -> FAILED. Pre-W1261 behaviour preserved.

    Regression guard: the existing floor-not-met path still trips when
    staleness is absent.
    """
    bare = ChangeEvidence(
        evidence_id="ev_w1261_fresh_bare",
    ).with_content_hash()
    floor = bare.assurance_floor()
    assert floor["passes"] is False
    assert floor["stale"] is False

    pred = build_vsa_predicate(bare)
    assert pred["verificationResult"] == "FAILED"


# ---------------------------------------------------------------------------
# W1261 - high-risk + stale interaction (defence-in-depth).
# ---------------------------------------------------------------------------


def test_high_risk_stale_packet_attests_failed() -> None:
    """High risk AND stale -> FAILED. Either signal alone is sufficient.

    Documents the precedence in ``_verification_result``: the
    ``risk_level`` check runs first, so a high-risk packet is FAILED
    independent of the stale axis. This test pins that the two gates
    do not interfere — neither cancels the other.
    """
    fresh = _full_packet(risk_level="critical")
    stale_critical = dataclasses.replace(
        fresh,
        evidence_stale=True,
        stale_reasons=("preflight_older_than_edits",),
    )
    pred = build_vsa_predicate(stale_critical)
    assert pred["verificationResult"] == "FAILED"
