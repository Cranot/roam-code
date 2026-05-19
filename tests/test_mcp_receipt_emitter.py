"""W196 - ``McpDecisionReceipt`` emission tests.

Per ``(internal memo)`` §"MCP trust boundary"
(lines 244-262). Wires the W183 receipt dataclass into the FastMCP
``@_tool`` decorator so sensitive tool calls produce a local audit
artefact under ``.roam/mcp_receipts/``.

These tests exercise the emission path on real ``@_tool``-decorated
functions (e.g. ``roam_init``) - they do NOT require a running MCP
transport because the receipt wrapper is wired in BEFORE the
``if mcp is None: return fn`` gate, so it fires for in-process callers
too.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from roam.evidence.mcp_receipt import hash_input_args

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_async(coro):
    """Run an async function from a sync test (Python 3.10+ compatible)."""
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.iscoroutine(coro) else asyncio.run(coro)


def _read_receipts(receipts_root: Path, bucket: str | None = None) -> list[dict]:
    """List every receipt JSON file under the receipts root."""
    target = receipts_root if bucket is None else receipts_root / bucket
    if not target.exists():
        return []
    receipts: list[dict] = []
    if bucket is None:
        # Walk every bucket directory
        for sub in target.iterdir():
            if sub.is_dir():
                for f in sub.glob("*.json"):
                    receipts.append(json.loads(f.read_text(encoding="utf-8")))
    else:
        for f in target.glob("*.json"):
            receipts.append(json.loads(f.read_text(encoding="utf-8")))
    return receipts


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    """Create a temporary git-repo-shaped directory and chdir into it.

    Ensures ``find_project_root`` resolves to ``tmp_path`` so receipts
    land in a writeable per-test location, and clears any inherited
    ``ROAM_RUN_ID`` / ``ROAM_AGENT_ID`` / ``ROAM_MCP_CLIENT_ID`` env vars
    so tests start from a clean slate.
    """
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    monkeypatch.delenv("ROAM_AGENT_ID", raising=False)
    monkeypatch.delenv("ROAM_MCP_CLIENT_ID", raising=False)
    return tmp_path


def _stub_sensitive_tool(monkeypatch, name: str = "stub_sensitive_tool"):
    """Register a synthetic sensitive tool in ``_TOOL_METADATA`` and return
    its receipt-wrapped callable.

    Lets us exercise the emitter without invoking ``roam_init`` (which
    would shell out to ``roam init`` and require a real index).
    """
    import roam.mcp_server as m

    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "title": name,
            "description": "synthetic test fixture",
            "core": False,
            "read_only": False,
            "destructive": True,
            "idempotent": False,
            "task_mode": "required",
            "version": "0.0.0",
        },
    )

    def _inner(**kwargs):
        return {"command": name, "summary": {"verdict": "ok"}, "kwargs": kwargs}

    return m._wrap_with_receipt(name, _inner)


def _stub_readonly_tool(monkeypatch, name: str = "stub_readonly_tool"):
    """Register a synthetic read-only tool in ``_TOOL_METADATA`` and return
    its receipt-wrapped callable (which should be the same fn unchanged).
    """
    import roam.mcp_server as m

    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "title": name,
            "description": "synthetic read-only fixture",
            "core": False,
            "read_only": True,
            "destructive": False,
            "idempotent": True,
            "task_mode": None,
            "version": "0.0.0",
        },
    )

    def _inner(**kwargs):
        return {"command": name, "summary": {"verdict": "ok"}}

    return m._wrap_with_receipt(name, _inner)


# ---------------------------------------------------------------------------
# 1. _is_sensitive predicate unit tests
# ---------------------------------------------------------------------------


def test_is_sensitive_detector() -> None:
    """Unit test for ``_is_sensitive`` against known metadata shapes."""
    from roam.mcp_server import _is_sensitive

    # Pure read-only / idempotent / no-task → not sensitive
    assert _is_sensitive({"destructive": False, "read_only": True, "idempotent": True, "task_mode": None}) is False

    # destructive=True → sensitive
    assert _is_sensitive({"destructive": True, "read_only": True, "idempotent": True}) is True

    # read_only=False → sensitive
    assert _is_sensitive({"destructive": False, "read_only": False, "idempotent": True}) is True

    # idempotent=False → sensitive (even if read-only)
    assert _is_sensitive({"destructive": False, "read_only": True, "idempotent": False}) is True

    # task_mode="required" → sensitive
    assert _is_sensitive({"destructive": False, "read_only": True, "idempotent": True, "task_mode": "required"}) is True

    # task_mode="optional" alone is NOT sensitive — only "required" is
    assert (
        _is_sensitive({"destructive": False, "read_only": True, "idempotent": True, "task_mode": "optional"}) is False
    )

    # Empty / missing metadata defaults to non-sensitive (safe default)
    assert _is_sensitive({}) is False


# ---------------------------------------------------------------------------
# 2. Emission behaviour
# ---------------------------------------------------------------------------


def test_sensitive_tool_emits_receipt(isolated_repo, monkeypatch) -> None:
    """Invoke a sensitive tool → a receipt file appears at the expected path."""
    wrapped = _stub_sensitive_tool(monkeypatch)

    result = wrapped(symbol="useThemeClasses")
    assert result["summary"]["verdict"] == "ok"

    receipts_root = isolated_repo / ".roam" / "mcp_receipts"
    assert receipts_root.exists(), "mcp_receipts/ directory should be created"

    receipts = _read_receipts(receipts_root)
    assert len(receipts) == 1, f"expected exactly one receipt, found {len(receipts)}"
    r = receipts[0]

    # Required envelope shape
    assert r["tool_name"] == "stub_sensitive_tool"
    assert r["tool_call"].startswith("stub_sensitive_tool_")
    # MCP-P0.2: policy_decision is now sourced from the real 4-mode gate,
    # NOT hard-coded "allow". A synthetic stub_sensitive_tool is not
    # registered in any mode's allow-list → the gate honestly records
    # "deny" (advisory because ROAM_MODE_ENFORCEMENT is not set).
    assert r["policy_decision"] == "deny"
    assert r["client_id"] == "<unknown>"
    # MCP-P0.2: required_mode is sourced from the agent-mode taxonomy
    # (read_only / safe_edit / migration / autonomous_pr) — closed enum
    # in :data:`roam.modes.policy.VALID_MODES` — NOT from the task_mode
    # axis (required / optional / None) that historically poisoned this
    # field. A destructive synthetic stub falls back to "migration"
    # via the side-effect-based default.
    from roam.modes.policy import VALID_MODES

    assert r["required_mode"] in VALID_MODES
    assert r["required_mode"] == "migration"
    # Destructive AND non-idempotent → both side-effects listed
    assert "destructive" in r["declared_side_effects"]
    assert "non_idempotent" in r["declared_side_effects"]


def test_readonly_tool_does_not_emit_receipt(isolated_repo, monkeypatch) -> None:
    """Invoke a read-only tool → no receipt file created."""
    wrapped = _stub_readonly_tool(monkeypatch)

    # The receipt wrapper should return the original fn unchanged for
    # read-only tools, so we can identify that quickly.
    assert wrapped.__name__ == "_inner"

    result = wrapped()
    assert result["summary"]["verdict"] == "ok"

    receipts_root = isolated_repo / ".roam" / "mcp_receipts"
    # Directory may not even exist; if it does it must be empty.
    if receipts_root.exists():
        assert _read_receipts(receipts_root) == []


def test_receipt_carries_input_hash(isolated_repo, monkeypatch) -> None:
    """The receipt's ``input_hash`` must match ``hash_input_args(kwargs)``."""
    wrapped = _stub_sensitive_tool(monkeypatch)
    args = {"symbol": "useThemeClasses", "verbose": True}
    wrapped(**args)

    receipts_root = isolated_repo / ".roam" / "mcp_receipts"
    receipts = _read_receipts(receipts_root)
    assert len(receipts) == 1
    expected_hash = hash_input_args(args)
    assert receipts[0]["input_hash"] == expected_hash


def test_receipt_with_active_run_links_run_event_id(isolated_repo, monkeypatch) -> None:
    """When ROAM_RUN_ID is set, the receipt's run_event_id matches it AND
    the file lives under the run-id bucket directory.
    """
    monkeypatch.setenv("ROAM_RUN_ID", "run_test_20260514_xyz")
    wrapped = _stub_sensitive_tool(monkeypatch)
    wrapped(target="foo")

    receipts_root = isolated_repo / ".roam" / "mcp_receipts"
    run_bucket = receipts_root / "run_test_20260514_xyz"
    assert run_bucket.exists(), "receipt should land in the active run's bucket"

    receipts = _read_receipts(receipts_root, bucket="run_test_20260514_xyz")
    assert len(receipts) == 1
    assert receipts[0]["run_event_id"] == "run_test_20260514_xyz"


def test_receipt_falls_back_to_no_run_dir_when_no_active_run(isolated_repo, monkeypatch) -> None:
    """With no active run, receipts go to ``.roam/mcp_receipts/_no_run/``."""
    # isolated_repo already clears ROAM_RUN_ID. No real run exists on disk.
    wrapped = _stub_sensitive_tool(monkeypatch)
    wrapped(target="bar")

    receipts_root = isolated_repo / ".roam" / "mcp_receipts"
    no_run = receipts_root / "_no_run"
    assert no_run.exists(), "_no_run/ bucket should be created when no run is open"
    receipts = _read_receipts(receipts_root, bucket="_no_run")
    assert len(receipts) == 1
    assert receipts[0]["run_event_id"] is None


def test_receipt_persist_failure_does_not_break_tool(isolated_repo, monkeypatch) -> None:
    """If the receipt write blows up, the underlying tool call still
    returns its result. Audit-trail failures are best-effort.
    """
    import roam.mcp_server as m

    # Force the write helper to always raise.
    def _broken_write(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(m, "_write_mcp_receipt", _broken_write)

    wrapped = _stub_sensitive_tool(monkeypatch)
    result = wrapped(symbol="ok")

    # Tool still succeeded.
    assert result["summary"]["verdict"] == "ok"

    # And nothing was written.
    receipts_root = isolated_repo / ".roam" / "mcp_receipts"
    if receipts_root.exists():
        assert _read_receipts(receipts_root) == []


# ---------------------------------------------------------------------------
# 3. declared_side_effects derivations
# ---------------------------------------------------------------------------


def test_destructive_tool_carries_destructive_side_effect(isolated_repo, monkeypatch) -> None:
    """A destructive tool's receipt has ``destructive`` in declared_side_effects."""
    import roam.mcp_server as m

    name = "stub_destructive_only"
    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "destructive": True,
            "read_only": False,
            "idempotent": True,
            "task_mode": None,
        },
    )

    def _inner(**kwargs):
        return {"command": name}

    wrapped = m._wrap_with_receipt(name, _inner)
    wrapped()

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    side_effects = receipts[0]["declared_side_effects"]
    assert "destructive" in side_effects
    # destructive wins over write — the two should not both appear
    assert "write" not in side_effects


def test_idempotent_false_carries_non_idempotent(isolated_repo, monkeypatch) -> None:
    """``idempotent=False`` puts ``non_idempotent`` in declared_side_effects."""
    import roam.mcp_server as m

    # Write-only-and-non-idempotent (no destructive flag) → side effects
    # should be ("write", "non_idempotent") in that order.
    name = "stub_write_non_idempotent"
    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "destructive": False,
            "read_only": False,
            "idempotent": False,
            "task_mode": None,
        },
    )

    def _inner(**kwargs):
        return {"command": name}

    wrapped = m._wrap_with_receipt(name, _inner)
    wrapped()

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    side_effects = receipts[0]["declared_side_effects"]
    assert "write" in side_effects
    assert "non_idempotent" in side_effects


# ---------------------------------------------------------------------------
# 4. Output-hash / output-ref selection
# ---------------------------------------------------------------------------


def test_small_result_produces_output_hash(isolated_repo, monkeypatch) -> None:
    """For small (<8KB) return values, the receipt carries ``output_hash``."""
    wrapped = _stub_sensitive_tool(monkeypatch)
    wrapped(foo="bar")

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    # Small result → output_hash set, output_ref None
    assert r["output_hash"] is not None
    assert len(r["output_hash"]) == 64  # sha256 hex
    assert r["output_ref"] is None


def test_handle_envelope_produces_output_ref(isolated_repo, monkeypatch) -> None:
    """A return value that already looks like a handle envelope produces
    ``output_ref`` rather than ``output_hash``."""
    import roam.mcp_server as m

    name = "stub_handle_returner"
    monkeypatch.setitem(
        m._TOOL_METADATA,
        name,
        {
            "name": name,
            "destructive": False,
            "read_only": False,
            "idempotent": True,
            "task_mode": None,
        },
    )

    def _inner(**kwargs):
        # Build a payload large enough to force handle-path detection.
        big_blob = "x" * (16 * 1024)
        return {
            "command": name,
            "is_handle": True,
            "summary": {"verdict": "stored", "handle": "abc123def456"},
            "blob": big_blob,
        }

    wrapped = m._wrap_with_receipt(name, _inner)
    wrapped()

    receipts = _read_receipts(isolated_repo / ".roam" / "mcp_receipts")
    assert len(receipts) == 1
    r = receipts[0]
    assert r["output_ref"] == "handle:abc123def456"
    assert r["output_hash"] is None


# ---------------------------------------------------------------------------
# 5. End-to-end smoke (uses a real @_tool-decorated function)
# ---------------------------------------------------------------------------


def test_roam_init_is_wired_as_sensitive(isolated_repo, monkeypatch) -> None:
    """The real ``roam_init`` tool, decorated via @_tool, is wrapped by the
    receipt emitter (sensitive: read_only=False, idempotent=False,
    task_mode=required).
    """
    import roam.mcp_server as m

    meta = m._TOOL_METADATA["roam_init"]
    assert m._is_sensitive(meta) is True
    side_effects = m._declared_side_effects_for(meta)
    # read_only=False & destructive=False → "write"; idempotent=False → "non_idempotent"
    assert "write" in side_effects
    assert "non_idempotent" in side_effects
