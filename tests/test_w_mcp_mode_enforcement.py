"""MCP-P0.2 — 4-mode policy enforcement at the MCP boundary.

MCP-P0.2 motivation: the 4-mode substrate
(`read_only` / `safe_edit` / `migration` / `autonomous_pr`) is wired into
``mcp_server.py`` so the MCP boundary actually gates destructive tool
calls instead of unconditionally hard-coding ``policy_decision="allow"``.

This test pins:

1. Under default enforcement + ``read_only`` mode, calling a
   destructive tool returns a Pattern-1 ``MODE_BLOCKED`` envelope (NOT
   the tool's normal output) AND the underlying tool function is never
   invoked.
2. Under explicit emergency override ``ROAM_MODE_ENFORCEMENT=0``, calling a
   destructive tool invokes it with a warning and the receipt records
   ``policy_decision="would_deny_dry_run"`` — never an unenforced deny.
3. Under ``safe_edit`` mode + enforcement, write tools (read_only=False,
   destructive=False) are allowed; destructive tools are still blocked
   AND the block envelope names ``migration`` as the required mode.
4. The receipt's ``required_mode`` field reflects the tool's
   ``_TOOL_METADATA`` side-effect flags (destructive→migration,
   write→safe_edit, read_only+idempotent→read_only) — NOT the
   ``task_mode`` axis (which used to wrongly populate it).
5. Policy import/resolution failures block writes but allow explicitly
   read-only diagnostics.
6. Verify's index/evidence maintenance writes require safe_edit, not
   autonomous_pr, and remain blocked in read_only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_mcp_module_state():
    """Reset the module-level error-storm counter before AND after each test.

    ``_structured_error`` keeps a process-wide ``_ERROR_STORM_STATE`` that
    trims envelope fields (including ``summary``) after the 3rd consecutive
    same-code error. The 7 ``MODE_BLOCKED`` assertions in this file share that
    error_code with sibling files (``test_w_mcp_security_pipeline_e2e.py`` /
    ``test_w_mcp_shadow_mode.py``); under ``pytest -n auto`` a worker that
    already saw >=3 ``MODE_BLOCKED`` errors would trim this file's deny
    envelope and drop the ``summary`` key (KeyError). Standard pattern across
    the suite — see ``tests/test_w_mcp_security_pipeline_e2e.py`` (fdd2d3be)
    and ``tests/test_w_mcp_shadow_mode.py``.
    """
    from roam.mcp_server import _reset_error_storm

    _reset_error_storm()
    yield
    _reset_error_storm()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_receipts(receipts_root: Path) -> list[dict]:
    """Walk every bucket under ``mcp_receipts/`` and load JSON receipts."""
    if not receipts_root.exists():
        return []
    receipts: list[dict] = []
    for sub in receipts_root.iterdir():
        if sub.is_dir():
            for f in sub.glob("*.json"):
                receipts.append(json.loads(f.read_text(encoding="utf-8")))
    return receipts


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """Tmp git-shaped dir, with all roam env vars cleared."""
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    monkeypatch.delenv("ROAM_AGENT_ID", raising=False)
    monkeypatch.delenv("ROAM_MCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    monkeypatch.delenv("ROAM_MODE_ENFORCEMENT", raising=False)
    monkeypatch.delenv("ROAM_MODE_DRY_RUN", raising=False)
    return tmp_path


def _register_destructive_tool(monkeypatch, name: str, *, return_value, backing_cli: str | None = None):
    """Register a synthetic destructive @_tool with the given backing CLI name.

    If *backing_cli* is supplied, ALSO patch ``_MCP_TO_CLI_RENAME_ALIAS`` so
    the gate maps the synthetic MCP name onto a real CLI command (e.g.
    ``"mutate"`` which sits at migration tier). When *backing_cli* is None
    the default name-translation rule applies (strip ``roam_`` + swap
    underscores for dashes).
    """
    import roam.mcp_server as m

    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "title": name,
            "description": "synthetic destructive test fixture",
            "core": False,
            "read_only": False,
            "destructive": True,
            "idempotent": False,
            "task_mode": "optional",  # WRONG axis — required_mode must NOT pull from here
            "version": "0.0.0",
        },
    )
    if backing_cli is not None:
        monkeypatch.setitem(m._MCP_TO_CLI_RENAME_ALIAS, name, backing_cli)

    call_count = {"n": 0}

    def _inner(**kwargs):
        call_count["n"] += 1
        return return_value

    wrapped = m._wrap_with_receipt(name, _inner)
    return wrapped, call_count


def _register_write_tool(monkeypatch, name: str, *, return_value, backing_cli: str | None = None):
    """Register a synthetic NON-destructive write @_tool (read_only=False, destructive=False)."""
    import roam.mcp_server as m

    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "title": name,
            "description": "synthetic write-but-not-destructive test fixture",
            "core": False,
            "read_only": False,
            "destructive": False,
            "idempotent": True,
            "task_mode": None,
            "version": "0.0.0",
        },
    )
    if backing_cli is not None:
        monkeypatch.setitem(m._MCP_TO_CLI_RENAME_ALIAS, name, backing_cli)

    call_count = {"n": 0}

    def _inner(**kwargs):
        call_count["n"] += 1
        return return_value

    wrapped = m._wrap_with_receipt(name, _inner)
    return wrapped, call_count


def _register_read_only_task_tool(monkeypatch, name: str, *, return_value):
    """Register a receipt-bearing diagnostic with no declared writes."""
    import roam.mcp_server as m

    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "title": name,
            "description": "synthetic read-only diagnostic fixture",
            "core": False,
            "read_only": True,
            "destructive": False,
            "idempotent": True,
            "task_mode": "required",
            "version": "0.0.0",
        },
    )
    call_count = {"n": 0}

    def _inner(**kwargs):
        call_count["n"] += 1
        return return_value

    return m._wrap_with_receipt(name, _inner), call_count


def _register_plain_read_only_tool(monkeypatch, name: str, *, return_value):
    """Register a read-only/idempotent tool that intentionally emits no receipt."""
    import roam.mcp_server as m

    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "title": name,
            "description": "synthetic ordinary read-only fixture",
            "core": False,
            "read_only": True,
            "destructive": False,
            "idempotent": True,
            "task_mode": None,
            "version": "0.0.0",
        },
    )
    call_count = {"n": 0}

    def _inner(**kwargs):
        call_count["n"] += 1
        return return_value

    return m._wrap_with_receipt(name, _inner), call_count


# ---------------------------------------------------------------------------
# 1. Enforcement ON + read_only mode → destructive tool blocked
# ---------------------------------------------------------------------------


def test_mode_policy_blocks_plain_readonly_tool_when_policy_denies_without_receipt(
    isolated_repo,
    monkeypatch,
) -> None:
    """Receipt classification cannot bypass the mode-policy boundary."""
    import roam.mcp_server as m

    raw = {"command": "stub_plain_readonly", "summary": {"verdict": "must not run"}}
    wrapped, call_count = _register_plain_read_only_tool(
        monkeypatch,
        "roam_plain_readonly_policy_denied",
        return_value=raw,
    )
    policy_calls = {"n": 0}

    def _deny(_tool_name, *_args, **_kwargs):
        policy_calls["n"] += 1
        return {
            "decision": "deny",
            "enforcement": True,
            "active_mode": "read_only",
            "required_mode": "read_only",
            "reason": "synthetic policy denial",
        }

    monkeypatch.setattr(m, "_evaluate_mcp_mode_policy", _deny)
    result = wrapped()

    assert policy_calls["n"] == 1
    assert call_count["n"] == 0
    assert result["isError"] is True
    assert result["error_code"] == "MODE_BLOCKED"
    assert _read_receipts(isolated_repo / ".roam" / "mcp_receipts") == []


def test_mode_enforcement_blocks_destructive_tool_in_read_only_mode(isolated_repo, monkeypatch) -> None:
    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    # Enforcement is default-on; no enable flag is required.

    raw = {"command": "stub_destroy", "summary": {"verdict": "destroyed everything"}}
    # ``mutate`` is the canonical migration-tier destructive command.
    wrapped, call_count = _register_destructive_tool(
        monkeypatch, "roam_destroy_stub", return_value=raw, backing_cli="mutate"
    )

    result = wrapped(symbol="foo")

    # Tool function MUST NOT have run.
    assert call_count["n"] == 0, "destructive tool ran despite mode block"

    # Result is a Pattern-1 MODE_BLOCKED envelope.
    assert isinstance(result, dict)
    assert result.get("isError") is True
    assert result.get("error_code") == "MODE_BLOCKED"
    summary = result.get("summary", {})
    assert summary.get("state") == "mode_blocked"
    assert summary.get("partial_success") is False
    assert "next_command" in result and result["next_command"].startswith("roam mode ")
    # The verdict must work without other fields (LAW 6).
    verdict = summary.get("verdict", "")
    assert "BLOCKED" in verdict and "mutate" in verdict

    # The receipt must record the deny decision.
    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["policy_decision"] == "deny"
    assert r["required_mode"] == "migration", f"required_mode must reflect side-effect tier, got {r['required_mode']!r}"


# ---------------------------------------------------------------------------
# 2. Explicit emergency override → tool runs with visible/auditable evidence
# ---------------------------------------------------------------------------


def test_mode_emergency_override_records_effective_decision(isolated_repo, monkeypatch, caplog) -> None:
    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "0")

    raw = {"command": "stub_destroy", "summary": {"verdict": "ran anyway"}}
    wrapped, call_count = _register_destructive_tool(
        monkeypatch, "roam_destroy_stub_advisory", return_value=raw, backing_cli="mutate"
    )

    with caplog.at_level(logging.WARNING, logger="roam.mcp_server"):
        result = wrapped(symbol="foo")

    # Tool function DID run.
    assert call_count["n"] == 1, "emergency override must permit the call"

    # Result is the tool's normal output, NOT a MODE_BLOCKED envelope.
    assert result.get("error_code") != "MODE_BLOCKED"
    assert result["summary"]["verdict"] == "ran anyway"

    # The receipt describes what was actually enforced: the deny was reduced
    # to a visible shadow decision, not recorded as an enforced deny.
    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["policy_decision"] == "would_deny_dry_run"
    assert r["required_mode"] == "migration"
    extra = r.get("extra") or {}
    assert extra.get("shadow_mode") is True
    assert "ROAM_MODE_ENFORCEMENT=0 emergency override" in extra.get("would_deny_reason", "")
    assert any("mcp.mode_policy.override" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# 3. safe_edit mode + enforcement → write tools allowed, destructive blocked
# ---------------------------------------------------------------------------


def test_safe_edit_mode_allows_write_tool_under_enforcement(isolated_repo, monkeypatch) -> None:
    monkeypatch.setenv("ROAM_AGENT_MODE", "safe_edit")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")

    raw = {"command": "stub_write", "summary": {"verdict": "wrote a thing"}}
    # ``critique`` lives at safe_edit tier (added by _MODE_EXTRAS["safe_edit"]).
    wrapped, call_count = _register_write_tool(monkeypatch, "roam_write_stub", return_value=raw, backing_cli="critique")

    result = wrapped()

    assert call_count["n"] == 1, "safe_edit write tool was wrongly blocked"
    assert result.get("error_code") != "MODE_BLOCKED"
    assert result["summary"]["verdict"] == "wrote a thing"

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["policy_decision"] == "allow"
    # required_mode for a (read_only=False, destructive=False, idempotent=True)
    # tool backed by ``critique`` (a safe_edit command) resolves via the
    # policy walk to safe_edit.
    assert r["required_mode"] == "safe_edit"


def test_safe_edit_mode_still_blocks_destructive_tool_under_enforcement(isolated_repo, monkeypatch) -> None:
    monkeypatch.setenv("ROAM_AGENT_MODE", "safe_edit")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")

    raw = {"command": "stub_destroy", "summary": {"verdict": "should not run"}}
    wrapped, call_count = _register_destructive_tool(
        monkeypatch, "roam_destroy_stub_safe_edit", return_value=raw, backing_cli="mutate"
    )

    result = wrapped()

    assert call_count["n"] == 0
    assert result.get("error_code") == "MODE_BLOCKED"
    # The block envelope names migration (the lowest mode that allows mutate).
    assert "migration" in result["summary"]["verdict"]
    assert result["next_command"] == "roam mode migration"


def test_tool_effects_cannot_be_weakened_by_read_only_command_tier(isolated_repo, monkeypatch) -> None:
    """The effective tier is max(command policy, MCP wrapper effects)."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    raw = {"command": "stub_write", "summary": {"verdict": "must not run"}}
    wrapped, call_count = _register_write_tool(
        monkeypatch,
        "roam_policy_metadata_max_stub",
        return_value=raw,
        backing_cli="health",
    )

    result = wrapped()

    assert call_count["n"] == 0
    assert result.get("error_code") == "MODE_BLOCKED"
    assert result["next_command"] == "roam mode safe_edit"
    receipt = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")[0]
    assert receipt["required_mode"] == "safe_edit"
    assert receipt["policy_decision"] == "deny"


def test_real_option_dependent_writer_uses_concrete_invocation_effects(isolated_repo, monkeypatch) -> None:
    """Fan's query stays read-only while ``persist=True`` requires a write tier."""
    import roam.mcp_server as m

    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    query_decision = m._evaluate_mcp_mode_policy("roam_fan", {"persist": False})
    write_decision = m._evaluate_mcp_mode_policy("roam_fan", {"persist": True})

    assert query_decision["decision"] == "allow"
    assert query_decision["active_mode"] == "read_only"
    assert query_decision["required_mode"] == "read_only"
    assert write_decision["decision"] == "deny"
    assert write_decision["active_mode"] == "read_only"
    assert write_decision["required_mode"] == "safe_edit"
    assert "MCP tool effects require safe_edit" in write_decision["reason"]


def test_real_option_dependent_wrapper_blocks_only_persist_form(isolated_repo, monkeypatch) -> None:
    """The dispatch wrapper and receipt use the same invocation classification."""
    import roam.mcp_server as m

    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    monkeypatch.setenv("ROAM_MCP_DISABLE_COLD_START_GUARD", "1")
    calls: list[tuple[list[str], str]] = []

    def _run(args, root):
        calls.append((args, root))
        return {"command": "fan", "summary": {"verdict": "query completed"}}

    monkeypatch.setattr(m, "_run_roam", _run)

    query_result = m.roam_fan()
    blocked_result = m.roam_fan(persist=True)

    assert query_result["summary"]["verdict"] == "query completed"
    assert calls == [(["fan", "symbol", "-n", "20"], ".")]
    assert blocked_result["error_code"] == "MODE_BLOCKED"
    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    assert receipts[0]["declared_side_effects"] == ["write"]
    assert receipts[0]["required_mode"] == "safe_edit"
    assert receipts[0]["policy_decision"] == "deny"


# ---------------------------------------------------------------------------
# 4. required_mode reflects metadata, not task_mode
# ---------------------------------------------------------------------------


def test_required_mode_reflects_metadata_not_task_mode(isolated_repo, monkeypatch) -> None:
    """The receipt's ``required_mode`` is sourced from the mode policy
    walk over ``_TOOL_METADATA`` side-effects, NOT from
    ``meta["task_mode"]`` (which is the task-mode taxonomy:
    required/optional/None — wrong axis).

    The synthetic stub sets ``task_mode="optional"``. If the legacy bug
    were still live, the receipt's ``required_mode`` would be
    ``"optional"`` — a string that is NOT in
    ``VALID_MODES = {read_only, safe_edit, migration, autonomous_pr}``.
    """
    from roam.modes.policy import VALID_MODES

    monkeypatch.setenv("ROAM_AGENT_MODE", "migration")
    # Enforcement off — we just want the receipt's required_mode to be
    # populated from the right axis.

    raw = {"command": "stub_destroy", "summary": {"verdict": "ok"}}
    wrapped, _call_count = _register_destructive_tool(
        monkeypatch, "roam_destroy_axis_check", return_value=raw, backing_cli="mutate"
    )
    wrapped()

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["required_mode"] in VALID_MODES, (
        f"required_mode must be a member of VALID_MODES {sorted(VALID_MODES)}, got {r['required_mode']!r}"
    )
    # And specifically it must be ``migration`` for a destructive tool
    # backed by ``mutate``.
    assert r["required_mode"] == "migration"
    # Belt-and-braces: the legacy task_mode string must NOT have leaked
    # into the required_mode slot.
    assert r["required_mode"] != "optional"
    assert r["required_mode"] != "required"


# ---------------------------------------------------------------------------
# 5. Bonus: side-effect-based fallback when CLI name is not in any mode
# ---------------------------------------------------------------------------


def test_required_mode_fallback_when_cli_command_unknown(isolated_repo, monkeypatch) -> None:
    """When the synthetic tool's CLI name is not in any policy allow-list
    (e.g. a tool whose backing CLI command was renamed without updating
    the policy YAML), the receipt's ``required_mode`` falls back to the
    side-effect-based default — never empty, never invalid.
    """
    from roam.modes.policy import VALID_MODES

    # No ``backing_cli`` override → the alias table fallback computes
    # ``destroy-fallback-stub`` which is NOT a real CLI command.
    raw = {"command": "stub_destroy", "summary": {"verdict": "ok"}}
    wrapped, _call_count = _register_destructive_tool(
        monkeypatch, "roam_destroy_fallback_stub", return_value=raw, backing_cli=None
    )
    wrapped()

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    # Side-effect fallback for destructive=True is migration.
    assert r["required_mode"] == "migration"
    assert r["required_mode"] in VALID_MODES


# ---------------------------------------------------------------------------
# 6. Allow path does not regress P0.1 redaction
# ---------------------------------------------------------------------------


def test_allow_path_still_redacts_egress_secrets(isolated_repo, monkeypatch) -> None:
    """P0.1 invariant: on the allow path (the gate did NOT deny), the
    egress redaction still scrubs secret-shaped strings from the
    response. This is the contract MCP-P0.2 must not undo.
    """
    monkeypatch.setenv("ROAM_AGENT_MODE", "migration")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")

    secret = "sk-test-1234567890abcdef1234567890"
    raw = {"command": "stub_leaky_p02", "summary": {"verdict": f"token {secret}"}}
    wrapped, call_count = _register_destructive_tool(
        monkeypatch, "roam_leaky_p02", return_value=raw, backing_cli="mutate"
    )

    result = wrapped()
    assert call_count["n"] == 1, "tool must run under migration mode"
    flat = json.dumps(result)
    assert secret not in flat, "P0.1 egress redaction must still fire on the allow path"
    assert "[REDACTED]" in flat

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["policy_decision"] == "allow"
    assert "secret" in r["redactions"]


def test_policy_import_failure_blocks_write_and_receipt_records_deny(isolated_repo, monkeypatch) -> None:
    import roam.mcp_server as m

    def _broken_dependencies():
        raise ImportError("synthetic missing policy")

    monkeypatch.setattr(m, "_mcp_mode_policy_dependencies", _broken_dependencies)
    raw = {"command": "stub_write", "summary": {"verdict": "must not run"}}
    wrapped, call_count = _register_write_tool(monkeypatch, "roam_policy_failure_write", return_value=raw)
    result = wrapped()

    assert call_count["n"] == 0
    assert result.get("error_code") == "MODE_BLOCKED"
    assert "policy unavailable" in result.get("error", "")
    receipt = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")[0]
    assert receipt["policy_decision"] == "deny"
    assert receipt["required_mode"] == "safe_edit"


def test_policy_import_failure_allows_read_only_diagnostic(isolated_repo, monkeypatch) -> None:
    import roam.mcp_server as m

    def _broken_dependencies():
        raise ImportError("synthetic missing policy")

    monkeypatch.setattr(m, "_mcp_mode_policy_dependencies", _broken_dependencies)
    raw = {"command": "stub_diagnostic", "summary": {"verdict": "diagnosed"}}
    wrapped, call_count = _register_read_only_task_tool(
        monkeypatch,
        "roam_policy_failure_diagnostic",
        return_value=raw,
    )
    result = wrapped()

    assert call_count["n"] == 1
    assert result["summary"]["verdict"] == "diagnosed"
    receipt = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")[0]
    assert receipt["policy_decision"] == "allow"
    assert receipt["required_mode"] == "read_only"


def test_policy_resolution_failure_blocks_write_and_receipt_records_deny(isolated_repo, monkeypatch) -> None:
    import roam.mcp_server as m
    from roam.modes.policy import VALID_MODES

    def _broken_resolve(_repo_root):
        raise RuntimeError("synthetic active-mode resolution failure")

    monkeypatch.setattr(
        m,
        "_mcp_mode_policy_dependencies",
        lambda: (lambda: isolated_repo, lambda *_args: (True, "unexpected"), _broken_resolve, VALID_MODES),
    )
    raw = {"command": "stub_write", "summary": {"verdict": "must not run"}}
    wrapped, call_count = _register_write_tool(
        monkeypatch,
        "roam_policy_resolution_failure_write",
        return_value=raw,
    )
    result = wrapped()

    assert call_count["n"] == 0
    assert result.get("error_code") == "MODE_BLOCKED"
    receipt = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")[0]
    assert receipt["policy_decision"] == "deny"
    assert receipt["required_mode"] == "safe_edit"


def test_omitted_root_discovery_failure_does_not_turn_cwd_fallback_into_allow(
    isolated_repo,
    monkeypatch,
) -> None:
    """CWD may host the denial receipt, but it is never trusted as policy resolution."""
    import roam.db.connection as connection

    def _broken_find_project_root(*_args, **_kwargs):
        raise OSError("synthetic root discovery failure")

    monkeypatch.setattr(connection, "find_project_root", _broken_find_project_root)
    raw = {"command": "stub_write", "summary": {"verdict": "must not run"}}
    wrapped, call_count = _register_write_tool(
        monkeypatch,
        "roam_root_discovery_failure_write",
        return_value=raw,
    )

    result = wrapped()

    assert call_count["n"] == 0
    assert result["error_code"] == "MODE_BLOCKED"
    assert "project-root resolution" in result["error"]
    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    assert receipts[0]["run_event_id"] is None
    assert receipts[0]["policy_decision"] == "deny"


def test_sensitive_wrapper_reuses_one_root_binding_for_policy_and_receipt(tmp_path, monkeypatch) -> None:
    """Policy cannot re-resolve a different repository after evidence binding."""
    import roam.db.connection as connection

    server_root = tmp_path / "server-repo"
    invocation_root = tmp_path / "invocation-repo"
    for root in (server_root, invocation_root):
        root.mkdir()
        (root / ".git").mkdir()
    monkeypatch.chdir(server_root)
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "0")
    calls: list[Path] = []

    def _single_use_resolver(start="."):
        calls.append(Path(start).resolve())
        if len(calls) > 1:
            raise AssertionError("mode policy attempted an independent root resolution")
        return invocation_root.resolve()

    monkeypatch.setattr(connection, "find_project_root", _single_use_resolver)
    raw = {"command": "stub_write", "summary": {"verdict": "completed"}}
    wrapped, call_count = _register_write_tool(
        monkeypatch,
        "roam_single_root_binding_write",
        return_value=raw,
    )

    result = wrapped(root=str(invocation_root))

    assert result["summary"]["verdict"] == "completed"
    assert call_count["n"] == 1
    assert calls == [invocation_root.resolve()]
    receipts = _read_receipts(invocation_root / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    assert not (server_root / ".roam" / "mcp_receipts").exists()


def test_mode_policy_resolves_the_invocation_root_instead_of_server_cwd(tmp_path, monkeypatch) -> None:
    """A multi-repo MCP server must gate the repo the tool will inspect."""
    import roam.mcp_server as m
    from roam.modes.policy import VALID_MODES

    server_root = tmp_path / "server-repo"
    invocation_root = tmp_path / "invocation-repo"
    for root in (server_root, invocation_root):
        root.mkdir()
        (root / ".git").mkdir()
    monkeypatch.chdir(server_root)

    seen: dict[str, Path] = {}

    class _ReadOnlyMode:
        name = "read_only"

    def _find_project_root(start="."):
        seen["find_start"] = Path(start).resolve()
        return Path(start).resolve()

    def _resolve_mode(root):
        seen["mode_root"] = Path(root).resolve()
        return _ReadOnlyMode()

    def _check_command_allowed(root, _command):
        seen["policy_root"] = Path(root).resolve()
        return True, ""

    monkeypatch.setattr(
        m,
        "_mcp_mode_policy_dependencies",
        lambda: (_find_project_root, _check_command_allowed, _resolve_mode, VALID_MODES),
    )
    result = m._evaluate_mcp_mode_policy(
        "roam_health",
        {"root": str(invocation_root)},
        {"read_only": True, "destructive": False, "idempotent": True},
    )

    expected = invocation_root.resolve()
    assert result["decision"] == "allow"
    assert seen == {
        "find_start": expected,
        "mode_root": expected,
        "policy_root": expected,
    }


@pytest.mark.parametrize("invalid_root", [42, b"repo", "", "  ", "repo\x00escape"])
def test_mode_policy_invalid_explicit_root_fails_closed_for_write(
    isolated_repo,
    invalid_root,
) -> None:
    """Root validation happens before policy/filesystem use and denies writes."""
    import roam.mcp_server as m

    decision = m._evaluate_mcp_mode_policy(
        "roam_mutate",
        {"root": invalid_root},
        {"read_only": False, "destructive": True, "idempotent": False},
    )

    assert decision["decision"] == "deny"
    assert decision["enforcement"] is True
    assert decision["active_mode"] == ""
    assert decision["required_mode"] == "migration"
    assert "project-root resolution" in decision["reason"]


def test_sensitive_mcp_tools_map_to_explicit_policy_or_bootstrap() -> None:
    """Every receipt-bearing side effect has an intentional policy target."""
    import roam.mcp_server as m
    from roam.modes.policy import _MODE_EXTRAS

    classified = set().union(*_MODE_EXTRAS.values())
    unclassified: list[tuple[str, str]] = []
    for tool_name, metadata in sorted(m._TOOL_METADATA.items()):
        sensitive = (
            metadata.get("read_only") is not True
            or metadata.get("destructive") is True
            or metadata.get("idempotent") is not True
        )
        if not sensitive:
            continue
        cli_name = m._mcp_tool_to_cli_command(tool_name)
        if cli_name not in classified and cli_name not in m._MCP_MODE_BOOTSTRAP_CLI:
            unclassified.append((tool_name, cli_name))

    assert not unclassified, f"sensitive MCP tools lack mode classifications: {unclassified}"


def test_native_read_only_surface_is_closed_and_fully_classified() -> None:
    """Every wrapper without a backing CLI verb needs an explicit audit entry."""
    import roam.mcp_server as m
    from roam import cli

    cli_commands = set(cli._COMMANDS) | set(cli._DEPRECATED_COMMANDS)
    native_tools = {
        tool_name for tool_name in m._TOOL_METADATA if m._mcp_tool_to_cli_command(tool_name) not in cli_commands
    }
    assert native_tools == m._MCP_NATIVE_READ_ONLY_TOOLS
    for tool_name in native_tools:
        metadata = m._TOOL_METADATA[tool_name]
        assert metadata.get("read_only") is True, tool_name
        assert metadata.get("destructive") is False, tool_name
        assert metadata.get("idempotent") is True, tool_name


@pytest.mark.parametrize("active_mode", ["read_only", "safe_edit", "migration", "autonomous_pr"])
def test_native_read_only_tool_is_monotonic_across_modes(isolated_repo, monkeypatch, active_mode) -> None:
    import roam.mcp_server as m

    monkeypatch.setenv("ROAM_AGENT_MODE", active_mode)
    decision = m._evaluate_mcp_mode_policy("roam_expand_toolset")
    assert decision == {
        "decision": "allow",
        "enforcement": True,
        "active_mode": active_mode,
        "required_mode": "read_only",
        "reason": "",
    }


@pytest.mark.parametrize("tool_name", ["roam_init", "roam_reindex"])
def test_mcp_bootstrap_tools_remain_reachable_in_read_only(isolated_repo, monkeypatch, tool_name) -> None:
    import roam.mcp_server as m

    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    decision = m._evaluate_mcp_mode_policy(tool_name)
    assert decision["decision"] == "allow"
    assert decision["active_mode"] == "read_only"
    assert decision["required_mode"] == "read_only"


@pytest.mark.parametrize(
    ("active_mode", "expected_calls", "expected_decision"),
    [
        ("safe_edit", 1, "allow"),
        ("read_only", 0, "deny"),
    ],
)
def test_verify_safe_maintenance_boundary(
    isolated_repo,
    monkeypatch,
    active_mode,
    expected_calls,
    expected_decision,
) -> None:
    monkeypatch.setenv("ROAM_AGENT_MODE", active_mode)
    raw = {"command": "stub_verify", "summary": {"verdict": "verified"}}
    wrapped, call_count = _register_write_tool(
        monkeypatch,
        f"roam_verify_maintenance_{active_mode}",
        return_value=raw,
        backing_cli="verify",
    )
    result = wrapped()

    assert call_count["n"] == expected_calls
    if expected_calls:
        assert result["summary"]["verdict"] == "verified"
    else:
        assert result.get("error_code") == "MODE_BLOCKED"
        assert "safe_edit" in result["summary"]["verdict"]
    receipt = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")[0]
    assert receipt["policy_decision"] == expected_decision
    assert receipt["required_mode"] == "safe_edit"
