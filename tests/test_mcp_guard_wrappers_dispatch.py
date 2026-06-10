"""Wave 18: smoke-test every Roam Guard MCP wrapper dispatches end-to-end.

Wave 10 registered 8 `roam_guard_*` / `roam_proof_bundle` / `roam_verdict`
/ `roam_verification_contract` wrappers. The metadata-level lints already
check they exist and have schemas; this file ensures each one actually
runs and returns a structured envelope when invoked.

We call the wrapper functions directly (not through the MCP transport) —
they internally use `_run_roam` which dispatches into the click CLI
in-process, so this exercises the real code path.
"""

from __future__ import annotations

import json

import pytest

from roam import mcp_server
from tests.helpers import make_pr_bundle


@pytest.fixture
def repo_with_bundle(tmp_path, monkeypatch):
    """Seed a repo with `.roam/pr-bundles/main.json` for wrappers that need one."""
    monkeypatch.chdir(tmp_path)
    bundles = tmp_path / ".roam" / "pr-bundles"
    bundles.mkdir(parents=True)
    bundle = make_pr_bundle(intent="wave 18 smoke")
    (bundles / "main.json").write_text(json.dumps(bundle))
    return tmp_path


def _assert_envelope(result, tool_name: str) -> None:
    """Every wrapper must return a JSON-envelope-shaped dict."""
    assert isinstance(result, dict), f"{tool_name} did not return dict"
    # Either a normal envelope (has `summary`) or a compound/error envelope.
    assert "summary" in result or "command" in result or "error" in result, (
        f"{tool_name} envelope missing required keys: {list(result)[:10]}"
    )


def test_roam_guard_doctor_dispatches(repo_with_bundle):
    out = mcp_server.roam_guard_doctor(root=str(repo_with_bundle))
    _assert_envelope(out, "roam_guard_doctor")
    # Command is either the underlying CLI name or the MCP wrapper name when
    # a w296 cold-start envelope intercepts.
    assert out.get("command") in ("guard-doctor", "roam_guard_doctor")


def test_roam_guard_rules_show_dispatches(repo_with_bundle):
    out = mcp_server.roam_guard_rules(subcommand="show", root=str(repo_with_bundle))
    _assert_envelope(out, "roam_guard_rules")


def test_roam_guard_history_dispatches(repo_with_bundle):
    out = mcp_server.roam_guard_history(root=str(repo_with_bundle))
    _assert_envelope(out, "roam_guard_history")
    assert out.get("command") in ("guard-history", "roam_guard_history")


def test_roam_proof_bundle_dispatches(repo_with_bundle):
    out = mcp_server.roam_proof_bundle(root=str(repo_with_bundle))
    _assert_envelope(out, "roam_proof_bundle")
    # Either the v1 envelope (verdict key) or the MCP cold-start envelope.
    assert "verdict" in out or out.get("command") in ("proof-bundle", "roam_proof_bundle")


def test_roam_verification_contract_dispatches(repo_with_bundle):
    out = mcp_server.roam_verification_contract(root=str(repo_with_bundle))
    _assert_envelope(out, "roam_verification_contract")


def test_roam_verdict_dispatches(repo_with_bundle):
    out = mcp_server.roam_verdict(root=str(repo_with_bundle))
    _assert_envelope(out, "roam_verdict")


def test_roam_guard_pr_dry_run_dispatches(repo_with_bundle):
    """Always pass dry_run=True so the wrapper never writes log / posts GH."""
    out = mcp_server.roam_guard_pr(
        dry_run=True,
        fmt="json",
        root=str(repo_with_bundle),
    )
    _assert_envelope(out, "roam_guard_pr")


def test_roam_guard_diff_from_log_with_no_entries_returns_error_envelope(repo_with_bundle):
    """guard-diff --from-log with empty log → structured error envelope, not crash."""
    out = mcp_server.roam_guard_diff(
        from_log=True,
        root=str(repo_with_bundle),
    )
    _assert_envelope(out, "roam_guard_diff")
    # Error is structured (missing_required_field), not a crash.
    summary = out.get("summary") or {}
    assert summary.get("error_code") in ("missing_required_field", None)


def test_all_guard_mcp_wrappers_registered():
    """Wave 10 lock: ensure none of the 8 wrappers regress out of registry."""
    expected = {
        "roam_guard_pr",
        "roam_guard_doctor",
        "roam_guard_rules",
        "roam_guard_history",
        "roam_guard_diff",
        "roam_proof_bundle",
        "roam_verification_contract",
        "roam_verdict",
    }
    missing = expected - set(mcp_server._TOOL_METADATA)
    assert not missing, f"MCP wrappers missing from registry: {missing}"
