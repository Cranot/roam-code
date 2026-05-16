"""W278 - tests for the actor-identity spoofing-tier classifier.

The classifier (``roam.evidence.actor_trust.classify_actor_trust_tier``)
is a pure function: every input it depends on is an explicit argument.
The tests therefore exercise the classification matrix directly without
any monkeypatching of ``os.environ`` or the filesystem.

The five tiers (closed enum in ``ACTOR_TRUST_TIERS``):

* ``verified_ci``         - active CI provider + actor matches CI actor
* ``git_author``          - actor matches ``git config user.email``
* ``local_env``           - actor matches active run-ledger entry
* ``self_reported_agent`` - ``actor_kind="agent"`` with no other signal
* ``unknown``             - default fallback

Each test pins one row of the classification matrix.
"""

from __future__ import annotations

import pytest

from roam.evidence import ACTOR_TRUST_TIERS
from roam.evidence.actor_trust import classify_actor_trust_tier

# ---------------------------------------------------------------------------
# Tier 1 - verified_ci
# ---------------------------------------------------------------------------


def test_classify_verified_ci() -> None:
    """CI env active + actor matches CI actor -> verified_ci.

    Stronger evidence wins: even when git_email also matches the
    actor_id, the active CI claim outranks. This exercises the
    ordering of the if-chain in :func:`classify_actor_trust_tier`.
    """
    tier = classify_actor_trust_tier(
        actor_id="alice@example.com",
        actor_kind="human",
        ci_env_detected=True,
        ci_actor_id="alice@example.com",
        git_email="alice@example.com",  # also matches, but CI wins
        run_ledger_actor=None,
    )
    assert tier == "verified_ci"
    assert tier in ACTOR_TRUST_TIERS


def test_classify_verified_ci_with_github_actor_string() -> None:
    """``GITHUB_ACTOR`` is a bare username string, not an email."""
    tier = classify_actor_trust_tier(
        actor_id="octocat",
        actor_kind="human",
        ci_env_detected=True,
        ci_actor_id="octocat",
        git_email=None,
        run_ledger_actor=None,
    )
    assert tier == "verified_ci"


def test_classify_verified_ci_does_not_promote_mismatched_actor() -> None:
    """CI active but actor_id != ci_actor_id -> not verified_ci.

    A hostile env could set ``GITHUB_ACTIONS=true`` while also stamping
    a forged actor_id. The classifier compares by equality only; a
    mismatch falls through to lower tiers.
    """
    tier = classify_actor_trust_tier(
        actor_id="agent:claude-opus-4.7",
        actor_kind="agent",
        ci_env_detected=True,
        ci_actor_id="octocat",  # the REAL CI actor doesn't match
        git_email=None,
        run_ledger_actor=None,
    )
    # ci_env_detected=True suppresses the self_reported_agent downgrade
    # (we can't prove it either way), so this falls all the way to unknown.
    assert tier == "unknown"


# ---------------------------------------------------------------------------
# Tier 2 - git_author
# ---------------------------------------------------------------------------


def test_classify_git_author() -> None:
    """No CI + actor matches git_email -> git_author."""
    tier = classify_actor_trust_tier(
        actor_id="alice@example.com",
        actor_kind="human",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email="alice@example.com",
        run_ledger_actor=None,
    )
    assert tier == "git_author"
    assert tier in ACTOR_TRUST_TIERS


def test_classify_git_author_does_not_partial_match() -> None:
    """Substring matches between actor_id and git_email do NOT promote.

    A hostile actor could pick an actor_id like
    ``"alice@example.com.attacker.com"`` and hope a prefix match
    promotes them to git_author. The classifier requires equality.
    """
    tier = classify_actor_trust_tier(
        actor_id="alice@example.com.attacker.com",
        actor_kind="human",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email="alice@example.com",
        run_ledger_actor=None,
    )
    assert tier == "unknown"


# ---------------------------------------------------------------------------
# Tier 3 - local_env
# ---------------------------------------------------------------------------


def test_classify_local_env() -> None:
    """No CI + no git match + actor matches run-ledger -> local_env."""
    tier = classify_actor_trust_tier(
        actor_id="agent:claude-opus-4.7",
        actor_kind="agent",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor="agent:claude-opus-4.7",
    )
    assert tier == "local_env"
    assert tier in ACTOR_TRUST_TIERS


def test_classify_local_env_outranks_self_reported_agent() -> None:
    """Run-ledger match (HMAC-signed) beats raw ``actor_kind="agent"``."""
    tier = classify_actor_trust_tier(
        actor_id="agent:foo",
        actor_kind="agent",  # would otherwise fall to self_reported
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor="agent:foo",  # but we have a ledger match
    )
    assert tier == "local_env"


# ---------------------------------------------------------------------------
# Tier 4 - self_reported_agent
# ---------------------------------------------------------------------------


def test_classify_self_reported_agent() -> None:
    """actor_kind=agent + no CI + no other signal -> self_reported_agent.

    This is the canonical spoofing case: ``ROAM_AGENT_ID=agent:foo``
    set locally, nothing corroborates the claim. The classifier flags
    it explicitly so downstream consumers can downgrade trust.
    """
    tier = classify_actor_trust_tier(
        actor_id="agent:claude-opus-4.7",
        actor_kind="agent",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor=None,
    )
    assert tier == "self_reported_agent"
    assert tier in ACTOR_TRUST_TIERS


def test_classify_self_reported_agent_only_for_agent_kind() -> None:
    """``human`` ref with no signal falls to ``unknown``, not self-reported.

    Branding a human ``self_reported_agent`` would be a category
    error; the tier is reserved for agent claims.
    """
    tier = classify_actor_trust_tier(
        actor_id="human:bob",
        actor_kind="human",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor=None,
    )
    assert tier == "unknown"


def test_classify_self_reported_agent_suppressed_when_ci_active() -> None:
    """CI active but no match -> ``unknown``, not ``self_reported_agent``.

    The most likely cause of "CI active + no match" is the producer
    didn't expose the CI actor variable. Falling to
    ``self_reported_agent`` would over-flag; ``unknown`` is honest.
    """
    tier = classify_actor_trust_tier(
        actor_id="agent:foo",
        actor_kind="agent",
        ci_env_detected=True,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor=None,
    )
    assert tier == "unknown"


# ---------------------------------------------------------------------------
# Tier 5 - unknown (default fallback)
# ---------------------------------------------------------------------------


def test_classify_unknown_fallback() -> None:
    """No signals at all -> unknown."""
    tier = classify_actor_trust_tier(
        actor_id="external:probe",
        actor_kind="external",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor=None,
    )
    assert tier == "unknown"
    assert tier in ACTOR_TRUST_TIERS


def test_classify_unknown_on_empty_actor_id() -> None:
    """Defensive: empty actor_id (which an ActorRef would reject) -> unknown."""
    tier = classify_actor_trust_tier(
        actor_id="",
        actor_kind="agent",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor=None,
    )
    assert tier == "unknown"


# ---------------------------------------------------------------------------
# W285 - corroborated_tool_ids / corroborated_actor_ids
#
# The W278 baseline only promoted to ``local_env`` when actor_id matched
# the ACTIVE run's agent. W285 extends that to "matches any HMAC-verified
# run-ledger event tool/actor id OR any parseable MCP receipt id". The
# guardrail: corroboration is ALWAYS evidence-based, never name-pattern-
# based. A tool whose id is never witnessed by a verified source stays
# ``unknown``, which is the honest-noise outcome.
# ---------------------------------------------------------------------------


def test_classify_local_env_via_corroborated_tool_ids() -> None:
    """Tool pseudo-actor + name in corroborated_tool_ids -> local_env.

    ``roam_init`` appearing in an HMAC-verified run-ledger event (or a
    parseable MCP receipt) is real evidence the tool was actually used.
    The classifier promotes the actor to ``local_env`` regardless of
    ``actor_kind``.
    """
    tier = classify_actor_trust_tier(
        actor_id="roam_init",
        actor_kind="tool",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor=None,
        corroborated_tool_ids=frozenset({"roam_init"}),
    )
    assert tier == "local_env"
    assert tier in ACTOR_TRUST_TIERS


def test_classify_unknown_without_corroboration() -> None:
    """CRITICAL guardrail - same actor_id, empty corroboration -> unknown.

    Proves there is NO name-based shortcut. ``roam_init`` looks like an
    internal tool but without verified evidence the classifier returns
    the honest ``unknown`` rather than over-promoting to ``local_env``.
    Removing the W285 corroboration check would make this case revert
    to ``unknown`` too - but ALSO break the
    test_classify_local_env_via_corroborated_tool_ids case. Both tests
    must hold for the W285 contract.
    """
    tier = classify_actor_trust_tier(
        actor_id="roam_init",
        actor_kind="tool",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor=None,
        corroborated_tool_ids=frozenset(),
        corroborated_actor_ids=frozenset(),
    )
    assert tier == "unknown"


def test_classify_local_env_via_corroborated_actor_ids() -> None:
    """Agent actor + id in corroborated_actor_ids -> local_env.

    Mirrors :func:`test_classify_local_env_via_corroborated_tool_ids`
    but on the actor-id axis. An ``agent:foo`` ref whose id appears in
    a parseable MCP receipt (``actor_ref_id`` / ``client_id``) is real
    evidence and promotes to ``local_env``. Without the W285 actor-id
    set this case would fall to ``self_reported_agent`` (agent_kind
    with no other signal), so the test also pins that ``local_env``
    outranks ``self_reported_agent`` for corroborated agents.
    """
    tier = classify_actor_trust_tier(
        actor_id="agent:foo",
        actor_kind="agent",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor=None,
        corroborated_actor_ids=frozenset({"agent:foo"}),
    )
    assert tier == "local_env"


def test_classify_unknown_for_unfamiliar_tool_id() -> None:
    """Tool actor with empty corroboration sets -> unknown.

    A tool name the producer has never seen (in verified events OR in
    MCP receipts) stays ``unknown`` even when the actor_id looks
    plausibly internal. This is the W285 guardrail in action: the
    classifier does NOT inspect actor_id structure or naming convention.
    """
    tier = classify_actor_trust_tier(
        actor_id="never-seen-tool",
        actor_kind="tool",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor=None,
        corroborated_tool_ids=frozenset(),
        corroborated_actor_ids=frozenset(),
    )
    assert tier == "unknown"


def test_classify_local_env_corroboration_does_not_outrank_ci() -> None:
    """Ordering check - CI verification beats corroborated_tool_ids.

    If the producer somehow surfaced both signals (a tool actor that
    matched the CI actor id AND appeared in corroborated_tool_ids), the
    stronger CI tier wins. Stress-tests the if-chain order: corroboration
    is checked AFTER CI / git_author, so the classifier never demotes
    an actor that earns the stronger tier.
    """
    tier = classify_actor_trust_tier(
        actor_id="roam_init",
        actor_kind="tool",
        ci_env_detected=True,
        ci_actor_id="roam_init",  # implausible in practice but rigorous
        git_email=None,
        run_ledger_actor=None,
        corroborated_tool_ids=frozenset({"roam_init"}),
    )
    assert tier == "verified_ci"


def test_classify_local_env_corroboration_does_not_promote_partial_match() -> None:
    """Substring match between actor_id and corroborated id -> not local_env.

    A hostile producer could populate ``corroborated_tool_ids`` with
    ``"roam_init"`` while the actor_id is the longer ``"roam_init_evil"``.
    The classifier matches by membership only - ``frozenset.__contains__``
    is exact equality, not prefix.
    """
    tier = classify_actor_trust_tier(
        actor_id="roam_init_evil",
        actor_kind="tool",
        ci_env_detected=False,
        ci_actor_id=None,
        git_email=None,
        run_ledger_actor=None,
        corroborated_tool_ids=frozenset({"roam_init"}),
    )
    assert tier == "unknown"


# ---------------------------------------------------------------------------
# Closed enum contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor_kind,ci_env_detected,ci_actor_id,git_email,run_ledger_actor",
    [
        ("agent", True, "actor", "actor", "actor"),
        ("human", False, None, "actor", None),
        ("agent", False, None, None, "actor"),
        ("agent", False, None, None, None),
        ("external", False, None, None, None),
    ],
)
def test_classify_always_returns_member_of_closed_enum(
    actor_kind: str,
    ci_env_detected: bool,
    ci_actor_id: str | None,
    git_email: str | None,
    run_ledger_actor: str | None,
) -> None:
    """Drift guard - every path returns a member of ``ACTOR_TRUST_TIERS``."""
    tier = classify_actor_trust_tier(
        actor_id="actor",
        actor_kind=actor_kind,
        ci_env_detected=ci_env_detected,
        ci_actor_id=ci_actor_id,
        git_email=git_email,
        run_ledger_actor=run_ledger_actor,
    )
    assert tier in ACTOR_TRUST_TIERS
