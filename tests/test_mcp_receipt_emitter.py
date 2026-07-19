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
import os
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

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
    # These tests exercise receipt persistence/output mechanics with synthetic
    # unclassified write tools. Pin the explicit audited permissive path now
    # that mode enforcement defaults on.
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "0")
    monkeypatch.delenv("ROAM_MODE_DRY_RUN", raising=False)
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
    its security-wrapped callable. It remains receipt-free.
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
    # NOT hard-coded "allow". This synthetic tool has no policy entry; the
    # explicit emergency override is represented as a shadow decision, never
    # as an enforced deny.
    assert r["policy_decision"] == "would_deny_dry_run"
    assert (r.get("extra") or {}).get("shadow_mode") is True
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

    # ``functools.wraps`` preserves the public callable identity even though
    # read-only tools now pass through mode policy and egress redaction.
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
    from roam.runs.ledger import start_run

    run = start_run(isolated_repo, agent="receipt-emitter")
    monkeypatch.setenv("ROAM_RUN_ID", run.run_id)
    wrapped = _stub_sensitive_tool(monkeypatch)
    wrapped(target="foo")

    receipts_root = isolated_repo / ".roam" / "mcp_receipts"
    run_bucket = receipts_root / run.run_id
    assert run_bucket.exists(), "receipt should land in the active run's bucket"

    receipts = _read_receipts(receipts_root, bucket=run.run_id)
    assert len(receipts) == 1
    assert receipts[0]["run_event_id"] == run.run_id


@pytest.mark.parametrize(
    "malicious_run_id",
    [
        "../../mcp_escape",
        r"..\..\mcp_escape",
        "run_20260514_deadbeef/../../mcp_escape",
        r"C:\mcp_escape",
    ],
)
def test_traversal_run_ids_are_rejected_and_receipt_routes_to_no_run(
    isolated_repo,
    monkeypatch,
    malicious_run_id,
) -> None:
    """An environment-controlled run id is never accepted as a path."""
    monkeypatch.setenv("ROAM_RUN_ID", malicious_run_id)
    wrapped = _stub_sensitive_tool(monkeypatch)

    result = wrapped(target="safe")

    assert result["summary"]["verdict"] == "ok"
    receipts_root = isolated_repo / ".roam" / "mcp_receipts"
    assert {child.name for child in receipts_root.iterdir() if child.is_dir()} == {"_no_run"}
    receipts = _read_receipts(receipts_root, bucket="_no_run")
    assert len(receipts) == 1
    assert receipts[0]["run_event_id"] is None


@pytest.mark.parametrize("malicious_run_id", ["../../mcp_escape", r"..\..\mcp_escape"])
def test_ledger_link_rejects_traversal_run_id_before_log_event(
    isolated_repo,
    monkeypatch,
    malicious_run_id,
) -> None:
    import roam.mcp_server as m
    import roam.runs.ledger as ledger

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(ledger, "log_event", lambda *args, **kwargs: calls.append((args, kwargs)))

    m._receipt_link_to_ledger(malicious_run_id, "stub", "stub_call", "{}")

    assert calls == []


def test_receipt_target_rejects_symlinked_bucket_escape(isolated_repo) -> None:
    """Resolved containment rejects a canonical bucket redirected outside."""
    import roam.mcp_server as m

    valid_run_id = "run_20260514_deadbeef"
    receipts_root = isolated_repo / ".roam" / "mcp_receipts"
    receipts_root.mkdir(parents=True)
    outside = isolated_repo.parent / f"{isolated_repo.name}_outside_receipts"
    outside.mkdir()
    bucket = receipts_root / valid_run_id
    try:
        bucket.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="receipt bucket escaped"):
        m._mcp_receipt_target(valid_run_id, "stub_call")


@pytest.mark.parametrize("invalid_root", [42, b"repo", "", "   ", "repo\x00escape"])
def test_invalid_explicit_root_never_falls_back_to_server_cwd(
    isolated_repo,
    monkeypatch,
    invalid_root,
) -> None:
    """Bad root types/bytes fail evidence routing without misattribution."""
    wrapped = _stub_sensitive_tool(monkeypatch, "stub_invalid_evidence_root")

    result = wrapped(root=invalid_root)

    assert result["summary"]["verdict"] == "ok"
    assert not (isolated_repo / ".roam" / "mcp_receipts").exists()


def test_pathlike_explicit_root_routes_receipt_to_selected_repo(isolated_repo, monkeypatch) -> None:
    """String-returning PathLike roots retain the supported public behavior."""
    invocation_repo = isolated_repo.parent / f"{isolated_repo.name}_pathlike"
    invocation_repo.mkdir()
    (invocation_repo / ".git").mkdir()
    wrapped = _stub_sensitive_tool(monkeypatch, "stub_pathlike_evidence_root")

    result = wrapped(root=invocation_repo)

    assert result["summary"]["verdict"] == "ok"
    receipts = _read_receipts(invocation_repo / ".roam" / "mcp_receipts", bucket="_no_run")
    assert len(receipts) == 1
    assert receipts[0]["run_event_id"] is None
    assert not (isolated_repo / ".roam" / "mcp_receipts").exists()


def test_sync_positional_and_async_keyword_roots_share_binding(isolated_repo, monkeypatch) -> None:
    """Both branches of ``_wrap_with_receipt`` bind the invocation root."""
    import roam.mcp_server as m

    invocation_repo = isolated_repo.parent / f"{isolated_repo.name}_wrapper_branches"
    invocation_repo.mkdir()
    (invocation_repo / ".git").mkdir()

    sync_name = "stub_sync_positional_evidence_root"
    _stub_sensitive_tool(monkeypatch, sync_name)

    def _sync_inner(root="."):
        return {"command": sync_name, "summary": {"verdict": "ok"}}

    async_name = "stub_async_keyword_evidence_root"
    _stub_sensitive_tool(monkeypatch, async_name)

    async def _async_inner(root="."):
        return {"command": async_name, "summary": {"verdict": "ok"}}

    sync_result = m._wrap_with_receipt(sync_name, _sync_inner)(str(invocation_repo))
    async_result = asyncio.run(m._wrap_with_receipt(async_name, _async_inner)(root=str(invocation_repo)))

    assert sync_result["summary"]["verdict"] == "ok"
    assert async_result["summary"]["verdict"] == "ok"
    receipts = _read_receipts(invocation_repo / ".roam" / "mcp_receipts", bucket="_no_run")
    assert {receipt["tool_name"] for receipt in receipts} == {sync_name, async_name}
    assert not (isolated_repo / ".roam" / "mcp_receipts").exists()


def test_explicit_root_cannot_redirect_receipt_tree_through_symlink(isolated_repo, monkeypatch) -> None:
    """A repo-controlled receipts symlink cannot redirect MCP evidence."""
    invocation_repo = isolated_repo.parent / f"{isolated_repo.name}_symlink_root"
    invocation_repo.mkdir()
    (invocation_repo / ".git").mkdir()
    receipts_parent = invocation_repo / ".roam"
    receipts_parent.mkdir()
    outside = isolated_repo.parent / f"{isolated_repo.name}_outside_root_binding"
    outside.mkdir()
    try:
        (receipts_parent / "mcp_receipts").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    wrapped = _stub_sensitive_tool(monkeypatch, "stub_symlink_evidence_root")

    result = wrapped(root=str(invocation_repo))

    assert result["summary"]["verdict"] == "ok"
    assert list(outside.iterdir()) == []
    assert not (isolated_repo / ".roam" / "mcp_receipts").exists()


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction semantics are Windows-specific")
def test_receipt_target_rejects_in_tree_ntfs_junction(isolated_repo) -> None:
    """An in-repo junction alias is a redirect even when containment passes."""
    import roam.mcp_server as m

    receipts_root = isolated_repo / ".roam" / "mcp_receipts"
    alias_target = receipts_root / "alias-target"
    alias_target.mkdir(parents=True)
    bucket = receipts_root / "run_20260718_deadbeef"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(bucket), str(alias_target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr or created.stdout}")
    try:
        assert not bucket.is_symlink()
        with pytest.raises(ValueError, match="filesystem redirect"):
            m._mcp_receipt_target("run_20260718_deadbeef", "stub_call")
    finally:
        bucket.rmdir()


def test_existing_receipt_hardlink_is_never_replaced(isolated_repo, monkeypatch) -> None:
    """A predicted receipt name cannot turn replacement into aliased evidence."""
    name = "stub_receipt_hardlink"
    receipts_bucket = isolated_repo / ".roam" / "mcp_receipts" / "_no_run"
    receipts_bucket.mkdir(parents=True)
    outside = isolated_repo.parent / f"{isolated_repo.name}_outside_receipt.json"
    outside.write_bytes(b"sentinel")
    fixed_hex = "a" * 32
    target = receipts_bucket / f"{name}_{fixed_hex[:12]}.json"
    try:
        os.link(outside, target)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    monkeypatch.setattr(uuid, "uuid4", lambda: SimpleNamespace(hex=fixed_hex))

    result = _stub_sensitive_tool(monkeypatch, name)()

    assert result["summary"]["verdict"] == "ok"
    assert outside.read_bytes() == b"sentinel"
    assert target.read_bytes() == b"sentinel"
    assert target.stat().st_nlink == 2


def test_receipt_parent_swap_before_temp_write_is_rejected(isolated_repo, monkeypatch) -> None:
    """A mkdir-to-temp TOCTOU swap emits no bytes into the replacement tree."""
    import roam.atomic_io as atomic_io

    real_atomic_write = atomic_io.atomic_write_bytes
    parked: list[Path] = []

    def race_parent(target, content, **kwargs):
        target_path = Path(target)
        original_parent = target_path.parent
        parked_parent = original_parent.with_name(f"{original_parent.name}-parked")
        original_parent.rename(parked_parent)
        original_parent.mkdir()
        parked.append(parked_parent)
        return real_atomic_write(target_path, content, **kwargs)

    monkeypatch.setattr(atomic_io, "atomic_write_bytes", race_parent)
    result = _stub_sensitive_tool(monkeypatch, "stub_receipt_parent_race")()

    assert result["summary"]["verdict"] == "ok"
    replacement = isolated_repo / ".roam" / "mcp_receipts" / "_no_run"
    assert list(replacement.iterdir()) == []
    assert len(parked) == 1
    assert list(parked[0].iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-handle cleanup is platform-specific")
def test_receipt_parent_swap_after_temp_creation_leaves_no_detached_bytes(isolated_repo, monkeypatch) -> None:
    """A detached parent is cleaned through its pinned directory handle."""
    import roam.atomic_io as atomic_io

    real_atomic_write = atomic_io.atomic_write_bytes
    parked: list[Path] = []

    def race_after_temp_creation(target, content, **kwargs):
        original_prepare = kwargs["prepare_temp_fd"]
        target_path = Path(target)

        def _swap_parent(fd: int, temp_path: str) -> None:
            original_parent = target_path.parent
            parked_parent = original_parent.with_name(f"{original_parent.name}-detached")
            original_parent.rename(parked_parent)
            original_parent.mkdir()
            parked.append(parked_parent)
            original_prepare(fd, temp_path)

        kwargs["prepare_temp_fd"] = _swap_parent
        return real_atomic_write(target_path, content, **kwargs)

    monkeypatch.setattr(atomic_io, "atomic_write_bytes", race_after_temp_creation)
    result = _stub_sensitive_tool(monkeypatch, "stub_receipt_post_temp_race")()

    assert result["summary"]["verdict"] == "ok"
    replacement = isolated_repo / ".roam" / "mcp_receipts" / "_no_run"
    assert list(replacement.iterdir()) == []
    assert len(parked) == 1
    assert list(parked[0].iterdir()) == []


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
    assert result["_meta"]["mcp_receipt"] == {
        "state": "write_failed",
        "error_type": "OSError",
    }

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
