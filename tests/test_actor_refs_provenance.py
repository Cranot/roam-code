"""W290 - tests for ActorRef provenance wiring.

The W290 directive wires the W282 :func:`provenance_label` helper onto
``ActorRef.extra["provenance"]`` so each identity claim records WHICH
source produced it. Scope: ``actor_refs`` only (``authority_refs``
provenance is W291; ``policy_decisions`` / ``approvals`` provenance is
W292+).

The mapping these tests pin (one row per priority channel):

* ``--agent`` / explicit CLI flag         -> ``cli_flag``
* ``ROAM_AGENT_ID`` env var               -> ``env_var(ROAM_AGENT_ID)``
* CI provider env (``GITHUB_ACTIONS_RUN_ID``) -> ``ci_env_var(...)``
* ``git config user.email``               -> ``git_config(user.email)``
* Active run-ledger entry                 -> ``run_ledger``
* Audit-trail entry                       -> ``audit_trail``
* MCP receipt                             -> ``mcp_receipt``
* Pre-W290 producer envelope              -> ``producer_envelope``
* Anything else / unattributable          -> ``unknown``

Every emitted provenance source value MUST live in the closed
:data:`PROVENANCE_SOURCES` frozenset (the drift guard at the end of
this module pins that contract).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roam.commands.actor_helpers import resolve_actor_block
from roam.evidence import PROVENANCE_SOURCES, ActorRef
from roam.evidence.collector import (
    _build_actor_refs,
    _read_mcp_receipts_dir,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PROVENANCE_ENV_VARS: tuple[str, ...] = (
    "ROAM_AGENT_ID",
    "ROAM_HUMAN_ACTOR",
    "ROAM_MCP_CLIENT_ID",
    "ROAM_CI_RUNNER_ID",
    "GITHUB_ACTIONS_RUN_ID",
    "GITHUB_ACTIONS",
    "GITHUB_ACTOR",
    "GITHUB_RUN_ID",
    "GITLAB_CI",
    "GITLAB_USER_LOGIN",
    "BUILDKITE",
    "CIRCLECI",
    "JENKINS_URL",
    "TF_BUILD",
    "CI",
)


def _clean_provenance_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete every env var the resolver / CI detector reads."""
    for var in _PROVENANCE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _provenance_base(label: str) -> str:
    """Strip the optional ``"(detail)"`` suffix from a provenance label.

    The W282 helper emits either bare ``"git_config"`` or compact
    ``"git_config(user.email)"`` - both forms validate against
    :data:`PROVENANCE_SOURCES`. The drift guard checks the base.
    """
    return label.split("(", 1)[0]


# ---------------------------------------------------------------------------
# Channel-mapping tests (one per priority row)
# ---------------------------------------------------------------------------


def test_actor_ref_from_roam_agent_id_env_has_env_var_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``ROAM_AGENT_ID=agent:foo``, no CI, no flag -> ``env_var(ROAM_AGENT_ID)``."""
    _clean_provenance_env(monkeypatch)
    monkeypatch.setenv("ROAM_AGENT_ID", "agent:roam-agent-foo")

    actor = resolve_actor_block(repo_root=tmp_path)
    assert actor["agent_id"] == "agent:roam-agent-foo"
    assert actor.get("provenance_agent_id") == "env_var(ROAM_AGENT_ID)"

    refs = _build_actor_refs(
        pr_bundle_envelope={"actor": actor},
        run_events=(),
        caller_agent_id=None,
    )
    agent_refs = [r for r in refs if r.actor_kind == "agent"]
    assert agent_refs, "expected at least one agent ActorRef"
    target = agent_refs[0]
    assert target.actor_id == "agent:roam-agent-foo"
    assert target.extra.get("provenance") == "env_var(ROAM_AGENT_ID)"


def test_actor_ref_from_cli_flag_has_cli_flag_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit ``--agent-id`` flag wins over env -> ``cli_flag``."""
    _clean_provenance_env(monkeypatch)
    monkeypatch.setenv("ROAM_AGENT_ID", "env-agent-loses")

    actor = resolve_actor_block(
        agent_id_override="agent:flag-wins",
        repo_root=tmp_path,
    )
    assert actor["agent_id"] == "agent:flag-wins"
    assert actor.get("provenance_agent_id") == "cli_flag"

    refs = _build_actor_refs(
        pr_bundle_envelope={"actor": actor},
        run_events=(),
        caller_agent_id=None,
    )
    target = next(r for r in refs if r.actor_kind == "agent")
    assert target.actor_id == "agent:flag-wins"
    assert target.extra.get("provenance") == "cli_flag"


def test_actor_ref_from_git_config_has_git_config_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No CI / no env / git config user.email set -> ``git_config(user.email)``."""
    _clean_provenance_env(monkeypatch)

    # Stub git_actor() so the test is deterministic across CI hosts.
    from roam.commands import git_helpers

    monkeypatch.setattr(
        git_helpers,
        "git_actor",
        lambda: "alice@example.com",
    )

    actor = resolve_actor_block(repo_root=tmp_path)
    assert actor["human_actor"] == "alice@example.com"
    assert actor.get("provenance_human_actor") == "git_config(user.email)"

    refs = _build_actor_refs(
        pr_bundle_envelope={"actor": actor},
        run_events=(),
        caller_agent_id=None,
    )
    target = next(r for r in refs if r.actor_kind == "human")
    assert target.extra.get("provenance") == "git_config(user.email)"


def test_actor_ref_from_ci_env_has_ci_env_var_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``GITHUB_ACTIONS_RUN_ID`` set, no flag/env override -> ``ci_env_var(...)``."""
    _clean_provenance_env(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS_RUN_ID", "12345")

    actor = resolve_actor_block(repo_root=tmp_path)
    assert actor["ci_runner_id"] == "12345"
    assert (
        actor.get("provenance_ci_runner_id")
        == "ci_env_var(GITHUB_ACTIONS_RUN_ID)"
    )

    refs = _build_actor_refs(
        pr_bundle_envelope={"actor": actor},
        run_events=(),
        caller_agent_id=None,
    )
    target = next(r for r in refs if r.actor_kind == "ci_runner")
    assert target.extra.get("provenance") == "ci_env_var(GITHUB_ACTIONS_RUN_ID)"


def test_actor_ref_from_audit_trail_has_audit_trail_provenance() -> None:
    """Audit-trail-sourced ActorRefs carry ``"audit_trail"`` provenance.

    The collector today doesn't mirror audit-trail entries into
    ActorRefs (audit-trail envelopes produce artifacts +
    policy_decisions, not actor_refs). The W290 contract is that IF a
    future producer mirrors an audit-trail entry into an ActorRef, it
    MUST stamp ``extra["provenance"] = "audit_trail"``. We pin that
    contract by directly constructing an ActorRef with the audit-trail
    provenance label and asserting it round-trips cleanly.
    """
    from roam.evidence.provenance import provenance_label

    ref = ActorRef(
        actor_kind="human",
        actor_id="human:audit-trail-entry-author",
        extra={"provenance": provenance_label("audit_trail")},
    )
    assert ref.extra.get("provenance") == "audit_trail"


def test_actor_ref_from_mcp_receipt_has_mcp_receipt_provenance(
    tmp_path: Path,
) -> None:
    """A parseable MCP receipt mirrors into ActorRefs with mcp_receipt."""
    receipts_dir = tmp_path / "mcp_receipts"
    receipts_dir.mkdir()

    receipt_payload = {
        "tool_call": "call_abc123",
        "client_id": "client:test-mcp-client",
        "tool_name": "roam_preflight",
        "actor_ref_id": None,
        "declared_side_effects": [],
        "required_mode": None,
        "input_hash": None,
        "policy_decision": "not_evaluated",
        "output_ref": None,
        "output_hash": None,
        "run_event_id": None,
        "redactions": [],
        "extra": {},
    }
    (receipts_dir / "receipt_abc.json").write_text(
        json.dumps(receipt_payload), encoding="utf-8"
    )

    warnings: list[str] = []
    _artifacts, refs = _read_mcp_receipts_dir(receipts_dir, warnings)
    assert warnings == [], f"unexpected warnings: {warnings}"
    assert refs, "expected MCP-receipt-derived ActorRefs"

    by_kind = {r.actor_kind: r for r in refs}
    assert "mcp_client" in by_kind
    assert "tool" in by_kind
    for r in refs:
        assert r.extra.get("provenance") == "mcp_receipt", (
            f"ActorRef from MCP receipt missing mcp_receipt provenance: {r!r}"
        )


def test_actor_ref_from_caller_agent_id_kwarg_has_cli_flag_provenance() -> None:
    """``caller_agent_id`` kwarg on the collector -> ``cli_flag``.

    LAW 11 (explicit caller intent > inference). The caller_agent_id
    path is the collector-side equivalent of a producer-side
    ``--agent-id`` flag.
    """
    refs = _build_actor_refs(
        pr_bundle_envelope=None,
        run_events=(),
        caller_agent_id="agent:caller-kwarg",
    )
    target = next(r for r in refs if r.actor_kind == "agent")
    assert target.actor_id == "agent:caller-kwarg"
    assert target.extra.get("provenance") == "cli_flag"


def test_actor_ref_from_run_event_has_run_ledger_provenance() -> None:
    """Run-event-derived ActorRefs carry ``"run_ledger"`` provenance."""
    refs = _build_actor_refs(
        pr_bundle_envelope=None,
        run_events=[{"agent": "agent:from-run-event"}],
        caller_agent_id=None,
    )
    target = next(r for r in refs if r.actor_kind == "agent")
    assert target.actor_id == "agent:from-run-event"
    assert target.extra.get("provenance") == "run_ledger"


def test_actor_ref_from_pre_w290_envelope_has_producer_envelope_provenance() -> None:
    """Pre-W290 envelopes (no provenance_* sub-keys) -> ``producer_envelope``.

    A producer that hasn't been updated to W290 still emits the bare
    actor block. The collector falls back to ``producer_envelope`` so
    every emitted ActorRef carries SOME provenance label (Pattern 2
    always-emit).
    """
    envelope = {
        "actor": {
            "agent_id": "agent:legacy",
            "human_actor": "legacy@example.com",
            "actor_kind": "agent",
            # No provenance_* sub-keys
        }
    }
    refs = _build_actor_refs(
        pr_bundle_envelope=envelope,
        run_events=(),
        caller_agent_id=None,
    )
    for r in refs:
        assert r.extra.get("provenance") == "producer_envelope", (
            f"pre-W290 envelope ref missing producer_envelope fallback: {r!r}"
        )


# ---------------------------------------------------------------------------
# Drift guard - every test's emitted provenance source belongs to the
# closed PROVENANCE_SOURCES vocabulary
# ---------------------------------------------------------------------------


def test_actor_ref_provenance_uses_only_PROVENANCE_SOURCES_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every provenance label this module emits validates against the
    closed :data:`PROVENANCE_SOURCES` frozenset.

    Builds a fanout of ActorRefs covering every priority channel in
    this module, strips the ``"(detail)"`` suffix from each label, and
    asserts the base source is in ``PROVENANCE_SOURCES``. A future
    drift (e.g. someone changes ``"env_var"`` -> ``"environment_var"``
    in :mod:`actor_helpers`) trips this guard immediately.
    """
    # Channel 1 - env var
    _clean_provenance_env(monkeypatch)
    monkeypatch.setenv("ROAM_AGENT_ID", "agent:env")
    actor_env = resolve_actor_block(repo_root=tmp_path)
    refs_env = _build_actor_refs(
        pr_bundle_envelope={"actor": actor_env},
        run_events=(),
        caller_agent_id=None,
    )

    # Channel 2 - cli flag
    _clean_provenance_env(monkeypatch)
    actor_cli = resolve_actor_block(
        agent_id_override="agent:cli", repo_root=tmp_path
    )
    refs_cli = _build_actor_refs(
        pr_bundle_envelope={"actor": actor_cli},
        run_events=(),
        caller_agent_id=None,
    )

    # Channel 3 - ci_env_var
    _clean_provenance_env(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS_RUN_ID", "ci-run-99")
    actor_ci = resolve_actor_block(repo_root=tmp_path)
    refs_ci = _build_actor_refs(
        pr_bundle_envelope={"actor": actor_ci},
        run_events=(),
        caller_agent_id=None,
    )

    # Channel 4 - run-ledger (from run_events list)
    refs_run = _build_actor_refs(
        pr_bundle_envelope=None,
        run_events=[{"agent": "agent:from-run"}],
        caller_agent_id=None,
    )

    # Channel 5 - producer_envelope (pre-W290 fallback)
    refs_prod = _build_actor_refs(
        pr_bundle_envelope={
            "actor": {
                "agent_id": "agent:pre-w290",
                "actor_kind": "agent",
            }
        },
        run_events=(),
        caller_agent_id=None,
    )

    all_refs: list[ActorRef] = []
    all_refs.extend(refs_env)
    all_refs.extend(refs_cli)
    all_refs.extend(refs_ci)
    all_refs.extend(refs_run)
    all_refs.extend(refs_prod)
    assert all_refs, "fanout produced no ActorRefs - test bug"

    for r in all_refs:
        label = r.extra.get("provenance")
        assert isinstance(label, str) and label, (
            f"ActorRef missing extra['provenance']: {r!r}"
        )
        base = _provenance_base(label)
        assert base in PROVENANCE_SOURCES, (
            f"ActorRef provenance base {base!r} (from label {label!r}) "
            f"is not in PROVENANCE_SOURCES"
        )
