"""MCP-P1.1 — shadow-mode (``ROAM_MODE_DRY_RUN``) preview of policy enforcement.

MCP-P1.1 motivation: gateway operators previewing the 4-mode
enforcement policy in production need a way to see WHAT the gate would
block without actually blocking it. The ``ROAM_MODE_DRY_RUN`` env flag
flips the deny branch of ``_wrap_with_receipt`` from "build a
MODE_BLOCKED envelope" to "log + stamp the receipt + let the call
proceed".

This test pins:

1. Dry-run OFF + deny scenario → ``_build_mode_blocked_envelope`` fires
   and the tool body never runs (existing MCP-P0.2 behavior; this is the
   hash-stability regression guard).
2. Dry-run ON + deny scenario → the tool DOES execute, the receipt
   carries ``policy_decision="would_deny_dry_run"`` +
   ``extra["shadow_mode"] = True`` + ``extra["would_deny_reason"]``.
3. Dry-run ON + allow scenario → receipt unchanged from baseline (no
   shadow marker; allow is the steady state).
4. Dry-run OFF + allow scenario → byte-identical to pre-P1.1 (hash-
   stable regression check).
5. ``ROAM_MODE_DRY_RUN`` parsing accepts the documented truthy set
   (case-insensitive, surrounding whitespace stripped).
6. A single WARN log line is emitted per dry-run-blocked call.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers (mirrors test_w_mcp_mode_enforcement.py)
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


@pytest.fixture(autouse=True)
def _reset_mcp_module_state():
    """Reset the module-level error-storm counter before AND after each test.

    ``_structured_error`` keeps a process-wide ``_ERROR_STORM_STATE`` that
    trims envelope fields after the 3rd consecutive same-code error.
    Without this reset, several MODE_BLOCKED denials from this file leak
    into sibling test files (e.g. ``test_w_mcp_mode_enforcement.py``)
    where a later test's deny envelope gets the trimmed shape and loses
    its ``summary`` key. Standard pattern across the suite — see
    ``tests/test_mcp_server.py`` and ``tests/test_first_error_message_preserved.py``.
    """
    from roam.mcp_server import _reset_error_storm

    _reset_error_storm()
    yield
    _reset_error_storm()


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """Tmp git-shaped dir with every roam env var cleared."""
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    for var in (
        "ROAM_RUN_ID",
        "ROAM_AGENT_ID",
        "ROAM_MCP_CLIENT_ID",
        "ROAM_AGENT_MODE",
        "ROAM_MODE_ENFORCEMENT",
        "ROAM_MODE_DRY_RUN",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _register_destructive_tool(
    monkeypatch,
    name: str,
    *,
    return_value,
    backing_cli: str | None = None,
):
    """Register a synthetic destructive @_tool — mirrors P0.2 test harness."""
    import roam.mcp_server as m

    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "title": name,
            "description": "synthetic destructive shadow-mode fixture",
            "core": False,
            "read_only": False,
            "destructive": True,
            "idempotent": False,
            "task_mode": "optional",
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


def _register_write_tool(
    monkeypatch,
    name: str,
    *,
    return_value,
    backing_cli: str | None = None,
):
    """Register a synthetic NON-destructive write @_tool."""
    import roam.mcp_server as m

    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "title": name,
            "description": "synthetic write-but-not-destructive shadow-mode fixture",
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
# 0. Closed-enum drift: ``would_deny_dry_run`` is in both vocabulary layers
# ---------------------------------------------------------------------------


def test_would_deny_dry_run_is_in_canonical_policy_decisions() -> None:
    """The new closed-enum value must appear in both the canonical
    POLICY_DECISIONS frozenset AND in the MCP-receipt authority-gate
    subset ``_POLICY_DECISIONS``.
    """
    from roam.evidence._vocabulary import POLICY_DECISIONS
    from roam.evidence.mcp_receipt import _POLICY_DECISIONS

    assert "would_deny_dry_run" in POLICY_DECISIONS, (
        "MCP-P1.1: would_deny_dry_run missing from canonical POLICY_DECISIONS"
    )
    assert "would_deny_dry_run" in _POLICY_DECISIONS, (
        "MCP-P1.1: would_deny_dry_run missing from receipt-layer _POLICY_DECISIONS subset"
    )


def test_would_deny_dry_run_constructs_a_receipt() -> None:
    """The receipt dataclass must accept the new closed-enum verdict."""
    from roam.evidence.mcp_receipt import McpDecisionReceipt

    r = McpDecisionReceipt(
        tool_call="x",
        client_id="y",
        tool_name="roam_foo",
        policy_decision="would_deny_dry_run",
    )
    assert r.policy_decision == "would_deny_dry_run"


# ---------------------------------------------------------------------------
# 1. Dry-run OFF + deny → MODE_BLOCKED envelope (P0.2 regression)
# ---------------------------------------------------------------------------


def test_dry_run_off_deny_still_blocks(isolated_repo, monkeypatch) -> None:
    """Without ``ROAM_MODE_DRY_RUN``, the P0.2 enforcement path is
    unchanged — the deny envelope fires and the tool body never runs.
    """
    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")
    # ROAM_MODE_DRY_RUN intentionally NOT set.

    raw = {"command": "shadow_off_deny", "summary": {"verdict": "should not run"}}
    wrapped, call_count = _register_destructive_tool(
        monkeypatch,
        "roam_shadow_off_deny",
        return_value=raw,
        backing_cli="mutate",
    )

    result = wrapped(symbol="foo")

    assert call_count["n"] == 0, "P0.2 regression: tool ran despite mode block"
    assert result.get("error_code") == "MODE_BLOCKED"

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    # Steady-state advisory verdict, NOT the shadow marker.
    assert r["policy_decision"] == "deny"
    assert "shadow_mode" not in (r.get("extra") or {})
    assert "would_deny_reason" not in (r.get("extra") or {})


# ---------------------------------------------------------------------------
# 2. Dry-run ON + deny → tool proceeds; receipt carries shadow markers
# ---------------------------------------------------------------------------


def test_dry_run_on_deny_lets_tool_proceed(isolated_repo, monkeypatch, caplog) -> None:
    """``ROAM_MODE_DRY_RUN=1`` short-circuits the deny branch. The tool
    body runs; the receipt records ``would_deny_dry_run`` +
    ``extra["shadow_mode"] = True`` + ``extra["would_deny_reason"]``.
    A single WARN line is emitted via ``logging`` so operators can grep.
    """
    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")
    monkeypatch.setenv("ROAM_MODE_DRY_RUN", "1")

    raw = {"command": "shadow_on_deny", "summary": {"verdict": "ran in shadow"}}
    wrapped, call_count = _register_destructive_tool(
        monkeypatch,
        "roam_shadow_on_deny",
        return_value=raw,
        backing_cli="mutate",
    )

    with caplog.at_level(logging.WARNING, logger="roam.mcp_server"):
        result = wrapped(symbol="foo")

    # Tool DID run.
    assert call_count["n"] == 1
    assert result.get("error_code") != "MODE_BLOCKED"
    assert result["summary"]["verdict"] == "ran in shadow"

    # Receipt carries the shadow markers.
    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["policy_decision"] == "would_deny_dry_run"
    extra = r.get("extra") or {}
    assert extra.get("shadow_mode") is True
    # Reason carries the same human-readable explanation the deny envelope
    # would have used. We don't pin the literal substring (gate text may
    # vary by environment) — we pin that it's a non-empty string.
    assert isinstance(extra.get("would_deny_reason"), str)
    assert extra["would_deny_reason"], "would_deny_reason must not be empty"
    # required_mode stays populated (migration for destructive=True).
    assert r["required_mode"] == "migration"

    # Exactly one WARN line, matching the documented format.
    warn_records = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "mcp.mode_policy.dry_run" in rec.getMessage()
    ]
    assert len(warn_records) == 1, (
        f"expected exactly one dry-run WARN line, got {len(warn_records)} "
        f"(messages: {[r.getMessage() for r in warn_records]})"
    )
    msg = warn_records[0].getMessage()
    assert "tool=roam_shadow_on_deny" in msg
    assert "would_deny" in msg
    assert "reason=" in msg


# ---------------------------------------------------------------------------
# 3. Dry-run ON + allow → no shadow marker, no extra log
# ---------------------------------------------------------------------------


def test_dry_run_on_allow_is_unchanged(isolated_repo, monkeypatch, caplog) -> None:
    """On the allow path, dry-run is a no-op: the receipt records the
    normal ``allow`` decision and no WARN line is emitted. Allow is the
    steady state — operators only care about what WOULD have been
    blocked.
    """
    monkeypatch.setenv("ROAM_AGENT_MODE", "safe_edit")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")
    monkeypatch.setenv("ROAM_MODE_DRY_RUN", "1")

    raw = {"command": "shadow_on_allow", "summary": {"verdict": "allowed"}}
    wrapped, call_count = _register_write_tool(
        monkeypatch,
        "roam_shadow_on_allow",
        return_value=raw,
        backing_cli="critique",
    )

    with caplog.at_level(logging.WARNING, logger="roam.mcp_server"):
        result = wrapped()

    assert call_count["n"] == 1
    assert result["summary"]["verdict"] == "allowed"

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["policy_decision"] == "allow"
    extra = r.get("extra") or {}
    assert "shadow_mode" not in extra
    assert "would_deny_reason" not in extra

    # No dry-run WARN should fire on the allow path.
    dry_run_lines = [rec for rec in caplog.records if "mcp.mode_policy.dry_run" in rec.getMessage()]
    assert dry_run_lines == [], (
        f"dry-run is a no-op on allow paths, got logs: {[r.getMessage() for r in dry_run_lines]}"
    )


# ---------------------------------------------------------------------------
# 4. Hash-stability: dry-run OFF + allow → byte-identical to pre-P1.1
# ---------------------------------------------------------------------------


def test_hash_stability_dry_run_off_allow(isolated_repo, monkeypatch) -> None:
    """A receipt produced with ``ROAM_MODE_DRY_RUN`` unset must have the
    SAME canonical-JSON shape it had before MCP-P1.1: no ``shadow_mode``
    key, no ``would_deny_reason`` key. The W210 omit-when-default
    discipline must hold at the ``extra`` dict layer too.
    """
    monkeypatch.setenv("ROAM_AGENT_MODE", "migration")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "1")
    # ROAM_MODE_DRY_RUN intentionally NOT set.

    raw = {"command": "shadow_off_allow", "summary": {"verdict": "ok"}}
    wrapped, _call_count = _register_destructive_tool(
        monkeypatch,
        "roam_shadow_off_allow",
        return_value=raw,
        backing_cli="mutate",
    )
    wrapped()

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["policy_decision"] == "allow"
    extra = r.get("extra") or {}
    # Pre-P1.1 shape: nothing shadow-related rides in extra.
    assert "shadow_mode" not in extra
    assert "would_deny_reason" not in extra


# ---------------------------------------------------------------------------
# 5. Env-var parsing: truthy values + case + whitespace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "1",
        "true",
        "yes",
        "on",
        "TRUE",
        "Yes",
        "ON",
        " 1 ",
        "  true  ",
        "\ttrue\n",
    ],
)
def test_dry_run_env_truthy_values(monkeypatch, value) -> None:
    """Documented truthy values: ``1`` / ``true`` / ``yes`` / ``on``,
    case-insensitive, surrounding whitespace stripped.
    """
    import roam.mcp_server as m

    monkeypatch.setenv("ROAM_MODE_DRY_RUN", value)
    assert m._is_mode_dry_run() is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0",
        "false",
        "no",
        "off",
        "FALSE",
        "anything-else",
        "2",
        " ",
    ],
)
def test_dry_run_env_falsy_values(monkeypatch, value) -> None:
    """Anything outside the truthy set — including empty / 0 / false —
    leaves dry-run OFF.
    """
    import roam.mcp_server as m

    monkeypatch.setenv("ROAM_MODE_DRY_RUN", value)
    assert m._is_mode_dry_run() is False


def test_dry_run_env_unset_is_off(monkeypatch) -> None:
    """An unset env var is treated identically to falsy."""
    import roam.mcp_server as m

    monkeypatch.delenv("ROAM_MODE_DRY_RUN", raising=False)
    assert m._is_mode_dry_run() is False


# ---------------------------------------------------------------------------
# 6. Dry-run + enforcement OFF → advisory path (deny) unchanged
# ---------------------------------------------------------------------------


def test_dry_run_with_enforcement_off_is_a_noop(isolated_repo, monkeypatch) -> None:
    """``ROAM_MODE_DRY_RUN`` only matters when enforcement is ON. With
    enforcement OFF (the default advisory-shadow path), dry-run is a no-op
    — the receipt still records the steady-state ``deny`` verdict so
    auditors see what WOULD have been blocked under enforcement.
    """
    monkeypatch.setenv("ROAM_AGENT_MODE", "read_only")
    monkeypatch.setenv("ROAM_MODE_DRY_RUN", "1")
    # ROAM_MODE_ENFORCEMENT intentionally NOT set.

    raw = {"command": "shadow_no_enforce", "summary": {"verdict": "ran anyway"}}
    wrapped, call_count = _register_destructive_tool(
        monkeypatch,
        "roam_shadow_no_enforce",
        return_value=raw,
        backing_cli="mutate",
    )
    result = wrapped()

    assert call_count["n"] == 1
    assert result["summary"]["verdict"] == "ran anyway"

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    # Advisory path: classic ``deny``, NOT the shadow-mode verdict.
    assert r["policy_decision"] == "deny"
    extra = r.get("extra") or {}
    assert "shadow_mode" not in extra
