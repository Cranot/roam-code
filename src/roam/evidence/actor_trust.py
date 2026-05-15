"""W278 - actor-identity spoofing-detection classifier.

The W211 directive added ``ACTOR_TRUST_TIERS`` (5 closed values) on
``ActorRef`` but no producer actively classifies refs into those tiers
based on real corroborating signals. W278 closes that gap: this module
defines :func:`classify_actor_trust_tier`, a PURE function that takes
an actor's identity claim plus three optional corroborating signals
(active CI provider actor, ``git config user.email``, active run-ledger
agent) and returns the most-credible tier from
``ACTOR_TRUST_TIERS``.

W285 extends the ``local_env`` rule beyond "matches the ACTIVE run's
``agent`` field" to also accept "matches an HMAC-verified run-ledger
event author OR a parseable MCP receipt tool/actor id". That makes
tool pseudo-actors recorded inside the ledger (e.g. ``roam_init``,
``roam_reindex``) eligible for ``local_env`` ONLY when real
corroborating evidence exists. The corroboration is evidence-based,
NEVER name-based: a tool whose id is never mentioned in a verified
run-ledger event or a validated MCP receipt stays ``unknown``.

The thesis: when we record ``actor_refs`` on a ``ChangeEvidence``
packet, we should ALSO record WHY we trust that identity claim. A
local agent can set ``ROAM_AGENT_ID=verified-trusted-agent`` and walk
away with a packet that LOOKS authoritative; without an explicit
provenance check, the actor block is just a self-attested string.

Spoofing signals to detect (ordered most -> least suspicious):

* **CI environment mismatch** - ``ROAM_AGENT_ID`` set in env, but
  no CI env vars detected AND no signed run-ledger entry vouches
  for the actor -> ``self_reported_agent``.
* **Git author mismatch** - ``ROAM_AGENT_ID=agent:foo`` but the git
  commit author email is ``bot@trusted.com``; the agent claim is
  unverifiable -> ``self_reported_agent`` (we trust neither).
* **Active CI provider detected** - ``GITHUB_ACTIONS=true`` (etc.)
  AND the CI provider's actor variable matches the actor ref ->
  ``verified_ci``.
* **Git author match** - actor claim matches ``git config
  user.email`` AND no CI claim -> ``git_author``.
* **Local-only run** - no CI provider, no git author match, but
  actor ref matches (a) the active run-ledger entry's agent OR
  (b) the tool/agent id of an HMAC-verified run-ledger event OR
  (c) the tool/actor id on a parseable MCP receipt -> ``local_env``.
* **Default fallback** - no corroborating signal -> ``unknown``.

Determinism contract:

* The function takes EVERY input it depends on as an explicit
  argument. It never reads from ``os.environ``, the filesystem, or
  any global state. This makes it deterministic, audit-friendly, and
  trivial to unit-test.
* The return is always one of the five literals in
  :data:`ACTOR_TRUST_TIERS`. Other return paths are a bug.
* The classifier validates its own output via an assert (the closed
  enum can drift if a maintainer ever adds a new tier and forgets to
  update this module).
"""

from __future__ import annotations

from roam.evidence._vocabulary import ACTOR_TRUST_TIERS


def classify_actor_trust_tier(
    *,
    actor_id: str,
    actor_kind: str,
    ci_env_detected: bool,
    ci_actor_id: str | None,
    git_email: str | None,
    run_ledger_actor: str | None,
    corroborated_tool_ids: frozenset[str] = frozenset(),
    corroborated_actor_ids: frozenset[str] = frozenset(),
) -> str:
    """Return one of ``ACTOR_TRUST_TIERS`` for the given identity claim.

    Args:
        actor_id: the stable identifier string from the ``ActorRef``.
            Convention is ``"<scheme>:<value>"`` but bare values
            (e.g. an email or git author name) are accepted - the
            classifier compares by string equality, not by structure.
        actor_kind: one of ``ACTOR_KINDS`` (``agent``, ``human``,
            ``mcp_client``, ``tool``, ``ci_runner``, ``external``).
            Used to short-circuit certain checks (e.g. only ``agent``
            actor refs are eligible for the ``self_reported_agent``
            downgrade; a ``human`` ref with no corroborating signal
            falls through to ``unknown``, not to "self_reported_agent").
        ci_env_detected: ``True`` if the producer detected an active
            CI environment via the closed list of provider probes
            (see ``collector._detect_ci_env_id`` for the single source
            of truth).
        ci_actor_id: the CI provider's view of who triggered the run
            (e.g. ``GITHUB_ACTOR`` for GitHub Actions). ``None`` if
            either no CI is active or the provider doesn't expose
            actor identity. Used ONLY when ``ci_env_detected`` is
            ``True``.
        git_email: the value of ``git config user.email`` for the
            workspace, or ``None`` if git config is unavailable /
            unset. The classifier matches by string equality.
        run_ledger_actor: the ``agent`` string of the active run-
            ledger entry (HMAC-signed per the agent-OS substrate
            description in CLAUDE.md). ``None`` if no run is open.
        corroborated_tool_ids: W285 - tool identifiers that appear in
            HMAC-verified run-ledger events OR parseable MCP receipts
            (``tool_name`` / ``tool_id`` fields). Membership promotes
            ``actor_id`` to ``local_env`` regardless of ``actor_kind``.
            Empty by default - producers that don't surface a verified
            corroboration source pass ``frozenset()`` and tool pseudo-
            actors stay ``unknown`` (the honest-noise outcome).
        corroborated_actor_ids: W285 - actor identifiers harvested from
            the same verified sources (run-ledger ``agent`` strings on
            verified events, MCP receipt ``actor_ref_id`` / ``client_id``
            values). Membership promotes ``actor_id`` to ``local_env``.
            Disjoint from ``corroborated_tool_ids`` in spirit: tool
            pseudo-actors land in the former, ``agent`` / ``mcp_client``
            refs land in the latter; the classifier doesn't care which
            set matched, only that the actor_id appears in either.

    Returns:
        One of the five literals in :data:`ACTOR_TRUST_TIERS`:
        ``verified_ci`` / ``git_author`` / ``local_env`` /
        ``self_reported_agent`` / ``unknown``.

    Ordering rationale: stronger evidence wins. CI provider OIDC
    attestation > git config metadata > local run-ledger HMAC entry
    > caller-asserted ``ROAM_AGENT_ID`` > nothing. Within each tier,
    the actor_id must match by EQUALITY against the corroborating
    signal; "actor_id contains git_email" or "git_email is a prefix
    of actor_id" do NOT promote the tier (that would be too permissive
    and lets a hostile actor pick an actor_id that contains a
    legitimate email).

    W285 corroboration discipline: the ``corroborated_*`` sets are
    only ever populated from REAL evidence (HMAC-verified run events
    or parseable MCP receipts). The classifier itself does NOT inspect
    actor_id for "looks internal" name patterns - if the producer can't
    supply corroborating evidence, the tier stays ``unknown``. That
    keeps the spoofing-tier signal honest noise rather than a false
    positive masquerading as ``local_env``.
    """
    # Defensive type-narrowing - the public surface promises strings
    # but we accept None-ish at the boundary and bail to unknown
    # rather than raising. An ActorRef construction would have already
    # failed on an empty actor_id so this is belt-and-braces.
    if not isinstance(actor_id, str) or not actor_id:
        return "unknown"

    # ------------------------------------------------------------------
    # Tier 1 - verified_ci. Strongest signal: an active CI provider
    # AND the provider's actor variable matches the claim. The match
    # is by string equality; the producer is responsible for
    # normalising actor_id and ci_actor_id to a comparable shape (both
    # bare values, or both ``"<scheme>:<value>"``) before calling.
    # ------------------------------------------------------------------
    if ci_env_detected and ci_actor_id:
        if actor_id == ci_actor_id:
            tier = "verified_ci"
            assert tier in ACTOR_TRUST_TIERS
            return tier

    # ------------------------------------------------------------------
    # Tier 2 - git_author. The actor_id matches git config user.email
    # for the workspace. This is plain-text metadata (no crypto verify
    # like a signed commit would give) but it's strictly stronger than
    # an env var because it had to be configured at git-init time.
    # ------------------------------------------------------------------
    if git_email and actor_id == git_email:
        tier = "git_author"
        assert tier in ACTOR_TRUST_TIERS
        return tier

    # ------------------------------------------------------------------
    # Tier 3 - local_env. The actor_id matches a corroborating signal
    # rooted in repo-local cryptographic evidence:
    #
    #   (a) the active run-ledger entry's ``agent`` (the W278 baseline),
    #   (b) an HMAC-verified run-ledger event's tool/agent id surfaced
    #       via ``corroborated_tool_ids`` / ``corroborated_actor_ids``
    #       (W285), or
    #   (c) a parseable MCP receipt's tool_name / actor_ref_id likewise
    #       surfaced via the corroboration sets (W285).
    #
    # The run-ledger is HMAC-signed per the agent-OS substrate (see
    # ``src/roam/runs/signing.py``), so a match anchored to a verified
    # event proves the run-ledger key owner countersigned that tool's
    # presence. MCP receipts are weaker (no HMAC, just JSON-parseable)
    # but still represent real producer evidence rather than free-form
    # name claims. Without ANY of those signals, the actor stays at
    # ``unknown`` (or ``self_reported_agent`` for agent-kind refs) -
    # the W285 guardrail: corroboration is ALWAYS evidence-based, never
    # name-pattern-based.
    # ------------------------------------------------------------------
    if run_ledger_actor and actor_id == run_ledger_actor:
        tier = "local_env"
        assert tier in ACTOR_TRUST_TIERS
        return tier
    if actor_id in corroborated_tool_ids or actor_id in corroborated_actor_ids:
        tier = "local_env"
        assert tier in ACTOR_TRUST_TIERS
        return tier

    # ------------------------------------------------------------------
    # Tier 4 - self_reported_agent. The actor claims agent identity
    # but nothing above corroborated it. We narrow this to
    # ``actor_kind == "agent"`` AND no active CI: the rule's purpose
    # is to flag "an env var set ROAM_AGENT_ID locally but no
    # higher-tier signal vouches for it". A ``human`` actor with no
    # match falls through to ``unknown`` (we don't want to brand a
    # human "self-reported agent" - that's a category error).
    #
    # We also require ``not ci_env_detected``. Reasoning: if CI is
    # active AND the actor_id didn't match the CI actor, the most
    # likely cause is the producer simply didn't expose
    # ``GITHUB_ACTOR`` to ``ci_actor_id`` - falling to
    # ``self_reported_agent`` would be too harsh. ``unknown`` is the
    # right signal there (we couldn't prove anything either way).
    # ------------------------------------------------------------------
    if actor_kind == "agent" and not ci_env_detected:
        tier = "self_reported_agent"
        assert tier in ACTOR_TRUST_TIERS
        return tier

    # ------------------------------------------------------------------
    # Tier 5 - unknown. No corroborating signal. The default and
    # most-conservative tier.
    # ------------------------------------------------------------------
    tier = "unknown"
    assert tier in ACTOR_TRUST_TIERS
    return tier


__all__ = ("classify_actor_trust_tier",)
