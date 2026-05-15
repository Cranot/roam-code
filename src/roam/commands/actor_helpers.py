"""W260 - shared W189 actor-block resolution for producer-side envelopes.

The W189 actor block is the six-field identity surface every pr-bundle-
shaped envelope stamps onto its top-level ``actor`` key so the W176
collector can fold it into ``ChangeEvidence.actor_refs`` /
``ChangeEvidence.agent_id`` / ``ChangeEvidence.human_actor``.

Before W260, the resolver lived inside ``cmd_pr_bundle.py``. That worked
for the real ``pr-bundle emit`` producer but left ``pr-replay`` to
synthesise its own actor-free envelope, which meant downstream consumers
that read the synthetic envelope directly (not via the collector) saw
no actor identity at all — even when ``ROAM_AGENT_ID`` was set.

W260 extracts the resolver into this module so both producers share the
same priority chain (CLI flag > env > git config > active run-ledger
agent) and emit the same six-key shape. ``cmd_pr_bundle.py`` keeps thin
re-exports for back-compat with existing tests / call sites; new
producers should call :func:`resolve_actor_block` directly from here.

The resolver does NOT scrub secrets — that responsibility lives one
layer up at each producer boundary. See ``cmd_pr_bundle._scrub_actor_block``
(producer side) and ``roam.evidence.collector._scrub_actor_block``
(W249 layer-2). Producers wire the scrub in immediately after calling
:func:`resolve_actor_block`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Priority chain (first hit wins per field, LAW 11 - user intent over
# inference):
#   1. CLI flag (``--agent-id`` / ``--human-actor`` on the producer).
#   2. Environment variables (``ROAM_AGENT_ID`` / ``ROAM_HUMAN_ACTOR`` /
#      ``ROAM_MCP_CLIENT_ID`` / ``ROAM_CI_RUNNER_ID`` /
#      ``GITHUB_ACTIONS_RUN_ID``).
#   3. Git config ``user.email`` (human actor only).
#   4. Active run-ledger ``RunMeta.agent`` (agent id only).
#
# ``actor_kind`` is derived from which fields end up populated.
# Producers for ``mcp_client_id`` / ``tool_id`` are intentionally env-
# only (or NULL); ``tool_id`` is reserved for the W196 follow-up that
# adds per-tool-call MCP receipts.
# ---------------------------------------------------------------------------


def resolve_actor_block(
    *,
    agent_id_override: Optional[str] = None,
    human_actor_override: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> dict:
    """Return the six-key W189 ``actor`` dict for a producer envelope.

    Walks the priority chain documented above and returns a dict with
    six keys (``agent_id`` / ``human_actor`` / ``mcp_client_id`` /
    ``tool_id`` / ``ci_runner_id`` / ``actor_kind``). Fields with no
    resolved value are ``None`` so consumers (including the collector
    at ``collector.py:551-669``) can tell "not set" apart from a
    sentinel string. Never raises - every fallback is best-effort and a
    missing git binary / run-ledger directory degrades to ``None``.

    W290 provenance wiring: the returned dict ALSO carries per-field
    ``provenance_<field>`` sub-keys whose values are the W282
    :func:`provenance_label` string naming WHICH resolution channel won
    the priority race for that field. Sub-keys are emitted only when
    the corresponding field resolved to a non-None value (Pattern 2
    always-emit applies to the actor field itself, not to the
    provenance metadata - silent absence of a provenance sub-key
    matches the silent absence of the field it describes). Downstream
    consumers (e.g. the W176 collector at
    :func:`roam.evidence.collector._build_actor_refs`) lift the
    sub-keys into ``ActorRef.extra["provenance"]`` so each identity
    claim carries its origin trail.
    """
    env = os.environ

    # W290 - track which channel won for each resolved field. ``None``
    # entries mean the field itself did not resolve. The collector
    # mirrors these as ``ActorRef.extra["provenance"]`` via
    # :func:`provenance_label`.
    provenance: dict[str, tuple[str, Optional[str]]] = {}

    # 1. agent_id: flag > ROAM_AGENT_ID > active-run agent (when meaningful)
    agent_id: Optional[str] = None
    if agent_id_override:
        candidate = agent_id_override.strip()
        if candidate:
            agent_id = candidate
            provenance["agent_id"] = ("cli_flag", None)
    if agent_id is None:
        env_agent = env.get("ROAM_AGENT_ID", "").strip()
        if env_agent:
            agent_id = env_agent
            provenance["agent_id"] = ("env_var", "ROAM_AGENT_ID")
    if agent_id is None and repo_root is not None:
        try:
            from roam.runs.ledger import latest_in_progress_run

            meta = latest_in_progress_run(Path(repo_root))
        except Exception:
            meta = None
        # Only treat the run-ledger agent as an agent-id when it looks
        # like one. The ledger accepts ANY non-empty string as ``agent``
        # (see ``start_run`` in ``ledger.py``); a human-driven ``runs
        # start --agent alice@example.com`` should NOT populate
        # ``actor.agent_id`` - that goes in ``human_actor``.
        if meta and isinstance(meta.agent, str) and meta.agent.strip():
            candidate = meta.agent.strip()
            if "@" not in candidate:
                agent_id = candidate
                provenance["agent_id"] = ("run_ledger", None)

    # 2. human_actor: flag > ROAM_HUMAN_ACTOR > git config user.email
    human_actor: Optional[str] = None
    if human_actor_override:
        candidate = human_actor_override.strip()
        if candidate:
            human_actor = candidate
            provenance["human_actor"] = ("cli_flag", None)
    if human_actor is None:
        env_human = env.get("ROAM_HUMAN_ACTOR", "").strip()
        if env_human:
            human_actor = env_human
            provenance["human_actor"] = ("env_var", "ROAM_HUMAN_ACTOR")
    if human_actor is None:
        try:
            from roam.commands.git_helpers import git_actor

            git_who = git_actor()
        except Exception:
            git_who = ""
        # ``git_actor`` returns the sentinel ``"<unknown>"`` when neither
        # ``user.email`` nor ``user.name`` is set; treat it as absence so
        # the actor block stays clean.
        if git_who and git_who != "<unknown>":
            human_actor = git_who
            provenance["human_actor"] = ("git_config", "user.email")

    # 3. mcp_client_id: env-only.
    mcp_client_id: Optional[str] = None
    env_mcp = env.get("ROAM_MCP_CLIENT_ID", "").strip()
    if env_mcp:
        mcp_client_id = env_mcp
        provenance["mcp_client_id"] = ("env_var", "ROAM_MCP_CLIENT_ID")

    # 4. ci_runner_id: ROAM_CI_RUNNER_ID > GITHUB_ACTIONS_RUN_ID
    #    (GitHub Actions auto-detect; other CI providers add an env
    #    variable here when they become a real use case).
    #
    # W290 - ROAM_CI_RUNNER_ID is a Roam-namespaced env so it counts as
    # ``env_var`` (locally exported), whereas GITHUB_ACTIONS_RUN_ID is
    # a CI-provider variable so it counts as ``ci_env_var``. Per the
    # W282 vocabulary docstring: the tiers differ on audit weight.
    ci_runner_id: Optional[str] = None
    env_ci = env.get("ROAM_CI_RUNNER_ID", "").strip()
    if env_ci:
        ci_runner_id = env_ci
        provenance["ci_runner_id"] = ("env_var", "ROAM_CI_RUNNER_ID")
    if ci_runner_id is None:
        env_gha = env.get("GITHUB_ACTIONS_RUN_ID", "").strip()
        if env_gha:
            ci_runner_id = env_gha
            provenance["ci_runner_id"] = (
                "ci_env_var",
                "GITHUB_ACTIONS_RUN_ID",
            )

    actor = {
        "agent_id": agent_id,
        "human_actor": human_actor,
        "mcp_client_id": mcp_client_id,
        # ``tool_id`` is reserved for the W196 follow-up that emits a
        # receipt per MCP tool call. Kept in the shape today (explicit
        # absence, Pattern 2) so collectors don't have to defend against
        # a missing key.
        "tool_id": None,
        "ci_runner_id": ci_runner_id,
        "actor_kind": "external",
    }
    actor["actor_kind"] = resolve_actor_kind(actor)

    # W290 - stamp provenance sub-keys onto the dict so downstream
    # consumers (collector / evidence packet) can carry origin
    # attribution forward. Imported lazily to keep this module's
    # import cost flat for the legacy callers that ignore provenance.
    if provenance:
        from roam.evidence.provenance import provenance_label

        for field, (source, detail) in provenance.items():
            actor[f"provenance_{field}"] = provenance_label(
                source, detail=detail
            )

    return actor


def resolve_actor_kind(actor: dict) -> str:
    """Pick the dominant ``actor_kind`` from the resolved actor dict.

    Priority order matches the agent-OS thesis: when an AI agent is
    identified, that's the load-bearing identity for the change. CI
    runner > MCP client > tool > human reflects how an auto-pipelined
    PR sits relative to a human-driven one. ``external`` is the escape
    hatch when no identity resolves.

    The kind is always one of ``ACTOR_KINDS`` (validated by the
    ``ActorRef`` dataclass at ``src/roam/evidence/refs.py``): ``agent``,
    ``ci_runner``, ``mcp_client``, ``tool``, ``human``, ``external``.
    """
    if actor.get("agent_id"):
        return "agent"
    if actor.get("ci_runner_id"):
        return "ci_runner"
    if actor.get("mcp_client_id"):
        return "mcp_client"
    if actor.get("tool_id"):
        return "tool"
    if actor.get("human_actor"):
        return "human"
    return "external"


__all__ = ("resolve_actor_block", "resolve_actor_kind")
