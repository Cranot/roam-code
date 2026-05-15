"""W182 agentic-assurance ref dataclasses for ``ChangeEvidence``.

Three sibling dataclasses (``ActorRef``, ``AuthorityRef``,
``EnvironmentRef``) realising the three optional ref lists prescribed in
``(internal memo)`` §"Build deltas"
items 1-3. The crosswalk frames Roam's distinguishing claim as
``identity + authority + evidence``; these three dataclasses populate
the first two axes on the evidence packet.

Why one module, not three?

* The shapes are parallel: ``<kind>`` + ``<stable_id>`` + a small
  optional metadata payload + ``extra: Mapping``. Splitting into three
  near-identical 50-line files would obscure the parallelism. Reading
  one module is faster than tabbing through three.
* They all serve the same downstream contract (a list of refs on
  ``ChangeEvidence``) and validate against frozensets in the same
  ``_vocabulary`` module. Co-locating keeps the validation pattern in
  one place.

Determinism contract:

* All three dataclasses are frozen so they're hashable and the
  containing tuple has a stable element identity.
* Each one validates its ``<kind>`` field against the corresponding
  frozenset at construction time. Unknown kinds raise ``ValueError``
  with a clear message naming the rejected literal.
* The ``extra`` mapping uses ``default_factory=dict`` so every instance
  gets its own dict (avoids the classic mutable-default-argument bug).

NON-GOALS:

* No actor authentication. ``ActorRef`` carries identity *claims*
  (with a ``trust_tier`` rating their provenance), never credentials
  to verify them. Authentication is the CI provider's / Git server's
  job; we record what they assert.
* No token storage. ``AuthorityRef(authority_kind="token_scope")``
  carries the sha256 HASH of the scope, never the raw token bytes.
  Same rule applies to any future authority kind that touches secrets.
* No PII enrichment beyond ``display_name``. The dataclasses expose
  a single optional ``display_name`` for human-friendly reports; we
  deliberately do NOT add fields for full name, organisational role,
  contact info, etc. Anything richer goes in a separate consent-gated
  store, not in the evidence packet.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from roam.evidence._vocabulary import (
    ACTOR_KINDS,
    ACTOR_TRUST_TIERS,
    AUTHORITY_KINDS,
    AUTHORITY_SOURCES,
    ENV_KINDS,
)


@dataclasses.dataclass(frozen=True)
class ActorRef:
    """One actor (human / agent / MCP client / tool / CI / external).

    Fields:

    * ``actor_kind`` - one of ``ACTOR_KINDS``. Validated at construction;
      passing an unknown kind raises ``ValueError``.
    * ``actor_id`` - stable identifier string. Convention is
      ``"<scheme>:<value>"`` (e.g. ``"agent:claude-opus-4.7"``,
      ``"human:alice@example.com"``,
      ``"ci_runner:github.com/owner/repo/actions/runs/123"``). Consumers
      must not parse the inside; structure goes in ``extra``.
    * ``display_name`` - optional human-friendly label for reports
      (e.g. ``"Alice"``). Never used for identity matching.
    * ``trust_tier`` - one of ``ACTOR_TRUST_TIERS``. Records *how
      trustworthy this identity is*: a CI provider OIDC token
      (``verified_ci``) is cryptographically attested; a git author
      email (``git_author``) is plain-text metadata. The default is
      :data:`"unknown"` (the most-conservative tier) so unset paths
      are honest about lacking provenance rather than claiming a
      higher tier by accident. W211 directive.
    * ``extra`` - free-form structured detail (model version, session
      id, etc.). Kept tiny (<1 KB) because it serialises into the
      packet content hash when present.
    """

    actor_kind: str
    actor_id: str
    display_name: str | None = None
    trust_tier: str = "unknown"
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.actor_kind not in ACTOR_KINDS:
            raise ValueError(
                f"ActorRef.actor_kind={self.actor_kind!r} is not in "
                f"ACTOR_KINDS"
            )
        if not isinstance(self.actor_id, str) or not self.actor_id:
            raise ValueError(
                "ActorRef.actor_id must be a non-empty string"
            )
        if self.trust_tier not in ACTOR_TRUST_TIERS:
            raise ValueError(
                f"ActorRef.trust_tier={self.trust_tier!r} is not in "
                f"ACTOR_TRUST_TIERS"
            )


@dataclasses.dataclass(frozen=True)
class AuthorityRef:
    """One piece of authority that gated the change.

    Fields:

    * ``authority_kind`` - one of ``AUTHORITY_KINDS``. Validated.
    * ``authority_id`` - stable identifier. Convention is
      ``"<scheme>:<value>"`` (e.g. ``"mode:safe_edit"``,
      ``"permit:perm_20260513_a3f9c2"``,
      ``"approval:pr_42_review_1"``). For ``token_scope`` this is the
      HASH of the scope, never the raw token.

      W198 facade note: when ``authority_kind="permit"``, ``roam permit``
      is currently a verdict-only facade and does NOT persist a
      permit_id to disk. Producers populating a permit AuthorityRef
      from the facade should pair it with ``source="permit"`` and may
      leave ``authority_id`` as a synthetic placeholder (e.g.
      ``"permit:facade"``); the ``__post_init__`` auto-stamps
      ``extra["facade"] = True`` in that case so downstream consumers
      see the signal explicitly. A future ``roam permit --persist``
      will write real permit ids and the facade flag will go away
      naturally.
    * ``granted_by`` - optional identifier of who/what granted this
      authority (e.g. ``"human:alice@example.com"`` for an approval,
      ``"system:rules.yml"`` for a policy rule). Used for audit
      provenance.
    * ``source`` - one of ``AUTHORITY_SOURCES``. Records *where the
      producer learned about this authority* (active mode, explicit
      permit, rules.yml, CI policy, human approval, or collector
      inference). The default is :data:`"inferred_fallback"` because
      the most common populating path today is the W176 collector
      inferring a value when no explicit declaration was found. W211
      directive.
    * ``extra`` - free-form structured detail (timestamps, scope
      details, ...). When ``source="permit"`` and no ``permit_id`` is
      persisted, ``__post_init__`` sets ``extra["facade"] = True`` to
      mark the W198 facade path explicitly.

    The ``token_scope`` kind exists specifically so a packet can record
    "this change ran under a token scoped to X" WITHOUT revealing the
    token bytes. Producers MUST hash the scope (sha256 hex of the
    canonical-JSON scope object) before constructing the ref; raw token
    material has no business inside an evidence packet.
    """

    authority_kind: str
    authority_id: str
    granted_by: str | None = None
    source: str = "inferred_fallback"
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.authority_kind not in AUTHORITY_KINDS:
            raise ValueError(
                f"AuthorityRef.authority_kind={self.authority_kind!r} is "
                f"not in AUTHORITY_KINDS"
            )
        if not isinstance(self.authority_id, str) or not self.authority_id:
            raise ValueError(
                "AuthorityRef.authority_id must be a non-empty string"
            )
        if self.source not in AUTHORITY_SOURCES:
            raise ValueError(
                f"AuthorityRef.source={self.source!r} is not in "
                f"AUTHORITY_SOURCES"
            )
        # W198 facade flag: when the producer says the authority came
        # from the permit facade (source="permit") but no permit_id is
        # persisted in extra, auto-stamp ``extra["facade"] = True`` so
        # downstream consumers see the verdict-only nature of the
        # current permit implementation. The dataclass is frozen, so we
        # rebuild ``extra`` via ``object.__setattr__`` (the same trick
        # ``ChangeEvidence.__post_init__`` uses to coerce tuples).
        if self.source == "permit" and not self.extra.get("permit_id"):
            if not self.extra.get("facade"):
                merged: dict[str, Any] = dict(self.extra)
                merged["facade"] = True
                object.__setattr__(self, "extra", merged)


@dataclasses.dataclass(frozen=True)
class EnvironmentRef:
    """One reference to the execution environment of the change.

    Fields:

    * ``env_kind`` - one of ``ENV_KINDS``. Validated.
    * ``env_id`` - stable identifier. Convention is
      ``"<scheme>:<value>"`` (e.g.
      ``"ci_job:github.com/owner/repo/actions/runs/123"``,
      ``"workspace:/home/alice/repos/example"``,
      ``"branch_range:main:abc1234..def5678"``).
    * ``extra`` - free-form structured detail (provider name, runner
      labels, hostname hash, ...).

    Multiple ``EnvironmentRef`` entries on one packet are expected: a
    change typically has a workspace identifier AND a branch range AND
    (for CI-run changes) a CI job id. Consumers should treat them as
    a set of facts about the environment, not a single canonical one.
    """

    env_kind: str
    env_id: str
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.env_kind not in ENV_KINDS:
            raise ValueError(
                f"EnvironmentRef.env_kind={self.env_kind!r} is not in "
                f"ENV_KINDS"
            )
        if not isinstance(self.env_id, str) or not self.env_id:
            raise ValueError(
                "EnvironmentRef.env_id must be a non-empty string"
            )


__all__ = [
    "ActorRef",
    "AuthorityRef",
    "EnvironmentRef",
]
