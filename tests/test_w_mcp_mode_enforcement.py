"""MCP-P0.2 — 4-mode policy enforcement at the MCP boundary.

MCP-P0.2 motivation: the 4-mode substrate
(`read_only` / `safe_edit` / `migration` / `autonomous_pr`) is wired into
``mcp_server.py`` so the MCP boundary actually gates destructive tool
calls instead of unconditionally hard-coding ``policy_decision="allow"``.

This test pins:

1. Under ``ROAM_MODE_ENFORCEMENT=1`` + ``read_only`` mode, calling a
   destructive tool returns a Pattern-1 ``MODE_BLOCKED`` envelope (NOT
   the tool's normal output) AND the underlying tool function is never
   invoked.
2. Under ``ROAM_MODE_ENFORCEMENT=0`` (default), calling a destructive
   tool DOES invoke the tool, but the receipt records
   ``policy_decision="deny"`` — advisory-shadow audit trail.
3. Under ``safe_edit`` mode + enforcement, write tools (read_only=False,
   destructive=False) are allowed; destructive tools are still blocked
   AND the block envelope names ``migration`` as the required mode.
4. The receipt's ``required_mode`` field reflects the tool's
   ``_TOOL_METADATA`` side-effect flags (destructive→migration,
   write→safe_edit, read_only+idempotent→read_only) — NOT the
   ``task_mode`` axis (which used to wrongly populate it).
"""

from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# 1. Enforcement ON + read_only mode → destructive tool blocked
# ---------------------------------------------------------------------------


def test_mode_enforcement_blocks_destructive_tool_in_read_only_mode(isolated_repo, monkeypatch) -> None:
    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")

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
# 2. Enforcement OFF (default) → tool runs, receipt records deny (advisory)
# ---------------------------------------------------------------------------


def test_mode_advisory_records_deny_without_blocking_in_read_only_mode(isolated_repo, monkeypatch) -> None:
    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    # ROAM_MODE_ENFORCEMENT intentionally NOT set — advisory shadow.

    raw = {"command": "stub_destroy", "summary": {"verdict": "ran anyway"}}
    wrapped, call_count = _register_destructive_tool(
        monkeypatch, "roam_destroy_stub_advisory", return_value=raw, backing_cli="mutate"
    )

    result = wrapped(symbol="foo")

    # Tool function DID run.
    assert call_count["n"] == 1, "advisory shadow must not block the call"

    # Result is the tool's normal output, NOT a MODE_BLOCKED envelope.
    assert result.get("error_code") != "MODE_BLOCKED"
    assert result["summary"]["verdict"] == "ran anyway"

    # Receipt records the deny decision so an auditor can see what WOULD
    # have been blocked under enforcement.
    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["policy_decision"] == "deny"
    assert r["required_mode"] == "migration"


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
