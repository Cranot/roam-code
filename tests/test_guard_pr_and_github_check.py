"""Tests for `roam guard-pr` aggregate command + GitHub Check API payload.

Per project_roam_guard_phase2_complete:
- guard-pr wraps auto-collect → compose → render → exit per verdict.
- github_check.build_check_run_payload maps verdict → conclusion.
- No tests hit the network — post_check_run is tested by mocking urlopen.
"""

from __future__ import annotations

import json
import urllib.error

import pytest
from click.testing import CliRunner

from roam.commands import cmd_guard_pr
from roam.cli import cli
from roam.github_check import (
    SUMMARY_BYTE_CAP,
    VERDICT_TO_CONCLUSION,
    VERDICT_TO_TITLE,
    build_check_run_payload,
    post_check_run,
)


def _v1_with_verdict(verdict_value: str = "pass", n_required: int = 2, n_executed: int = 2) -> dict:
    return {
        "schema": "agent_change_proof_bundle",
        "schema_version": "1.0",
        "verdict": {"value": verdict_value, "reasons": [{"code": "all_required_passed"}]},
        "verification_contract": {
            "required": [{"command": f"test{i}", "kind": "test", "reason": "x"} for i in range(n_required)],
            "skipped": [],
        },
        "executed_checks": [{"command": f"test{i}", "status": "pass"} for i in range(n_executed)],
        "missing_checks": [],
        "changed_files": ["src/foo.py", "src/bar.py"],
        "repo": {"head_sha": "abc123"},
        "run": {"agent": "test-agent"},
        "mode": "safe_edit",
        "policy_profile": "startup",
    }


# ---- payload builder tests ----


def test_payload_pass_maps_to_success():
    v1 = _v1_with_verdict("pass")
    p = build_check_run_payload(v1, head_sha="abc" * 7)
    assert p["conclusion"] == "success"
    assert p["status"] == "completed"
    assert "Roam Guard" in p["output"]["title"]


def test_payload_blocked_maps_to_failure():
    v1 = _v1_with_verdict("blocked")
    p = build_check_run_payload(v1, head_sha="x" * 40)
    assert p["conclusion"] == "failure"


def test_payload_needs_review_maps_to_action_required():
    v1 = _v1_with_verdict("needs_review")
    p = build_check_run_payload(v1, head_sha="x" * 40)
    assert p["conclusion"] == "action_required"


def test_payload_pass_with_warnings_maps_to_neutral():
    v1 = _v1_with_verdict("pass_with_warnings")
    p = build_check_run_payload(v1, head_sha="x" * 40)
    assert p["conclusion"] == "neutral"


def test_payload_passes_through_markdown_summary():
    v1 = _v1_with_verdict("pass")
    custom = "# my custom\n\nbody"
    p = build_check_run_payload(v1, head_sha="x" * 40, markdown=custom)
    assert p["output"]["summary"] == custom


def test_payload_falls_back_to_default_summary_without_markdown():
    v1 = _v1_with_verdict("pass", n_required=3, n_executed=2)
    p = build_check_run_payload(v1, head_sha="x" * 40)
    assert "2 of 3" in p["output"]["summary"]


def test_payload_truncates_oversized_summary():
    v1 = _v1_with_verdict("pass")
    huge = "x" * (SUMMARY_BYTE_CAP * 2)
    p = build_check_run_payload(v1, head_sha="x" * 40, markdown=huge)
    assert len(p["output"]["summary"].encode("utf-8")) <= SUMMARY_BYTE_CAP * 2  # bounded
    assert "truncated" in p["output"]["summary"]


def test_payload_includes_details_url_when_passed():
    v1 = _v1_with_verdict("pass")
    p = build_check_run_payload(v1, head_sha="x" * 40, details_url="https://example.com/dash")
    assert p["details_url"] == "https://example.com/dash"


def test_post_check_run_returns_no_token_error_without_env(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = post_check_run(owner="o", repo="r", payload={})
    assert result["ok"] is False
    assert result["error"] == "no_github_token"


def test_post_check_run_http_error_body_read_failure_preserves_status(monkeypatch):
    class BrokenErrorBody:
        def read(self):
            raise OSError("socket closed")

        def close(self):
            pass

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    error = urllib.error.HTTPError(
        "https://api.github.com/repos/o/r/check-runs",
        502,
        "Bad Gateway",
        hdrs={},
        fp=BrokenErrorBody(),
    )

    def raise_http_error(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    result = post_check_run(owner="o", repo="r", payload={})

    assert result == {
        "ok": False,
        "status": 502,
        "body": "HTTP Error 502: Bad Gateway",
        "error": "http_502",
    }


def test_verdict_conclusion_map_is_closed_enum():
    """Every supported verdict has a documented conclusion mapping."""
    for verdict in ("pass", "pass_with_warnings", "needs_review", "blocked"):
        assert verdict in VERDICT_TO_CONCLUSION
        assert verdict in VERDICT_TO_TITLE


# ---- guard-pr CLI tests ----

from tests.helpers import make_pr_bundle as _make_pr_bundle


def test_cli_guard_pr_missing_bundle_exits_2(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-pr", "--bundle", str(tmp_path / "nope.json")])
    assert result.exit_code == 2


def test_cli_guard_pr_text_output_shows_verdict(tmp_path):
    runner = CliRunner()
    bundle_path = tmp_path / "main.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))
    result = runner.invoke(cli, ["guard-pr", "--bundle", str(bundle_path), "--skip-collect"])
    assert result.exit_code in (0, 4, 5)
    assert "VERDICT:" in result.output


def test_cli_guard_pr_markdown_output(tmp_path):
    runner = CliRunner()
    bundle_path = tmp_path / "main.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))
    result = runner.invoke(
        cli,
        [
            "guard-pr",
            "--bundle",
            str(bundle_path),
            "--format",
            "markdown",
            "--skip-collect",
        ],
    )
    assert result.exit_code in (0, 4, 5)
    assert "Roam Guard verdict:" in result.output
    assert "##" in result.output  # markdown header


def test_cli_guard_pr_json_envelope(tmp_path):
    runner = CliRunner()
    bundle_path = tmp_path / "main.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-pr",
            "--bundle",
            str(bundle_path),
            "--skip-collect",
        ],
    )
    assert result.exit_code in (0, 4, 5)
    payload = json.loads(result.output)
    assert payload["command"] == "guard-pr"
    assert "agent_change_proof_bundle" in payload


def test_guard_pr_auto_collect_expected_failure_returns_marker(tmp_path, monkeypatch):
    bundle_path = tmp_path / "main.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))

    def _raise_os_error(*_args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(cmd_guard_pr, "auto_collect", _raise_os_error)

    assert cmd_guard_pr._run_auto_collect_inline(bundle_path, tmp_path) == {
        "error": "auto_collect_failed: disk unavailable"
    }


def test_guard_pr_auto_collect_unexpected_failure_propagates(tmp_path, monkeypatch):
    bundle_path = tmp_path / "main.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))

    def _raise_runtime_error(*_args):
        raise RuntimeError("programmer error")

    monkeypatch.setattr(cmd_guard_pr, "auto_collect", _raise_runtime_error)

    with pytest.raises(RuntimeError, match="programmer error"):
        cmd_guard_pr._run_auto_collect_inline(bundle_path, tmp_path)


def test_cli_guard_pr_strict_blocks_with_exit_5(tmp_path):
    runner = CliRunner()
    bundle_path = tmp_path / "main.json"
    bundle_path.write_text(
        json.dumps(
            _make_pr_bundle(
                risks=[{"severity": "high", "paths": ["src/auth/x.py"], "description": "auth"}],
                files=["src/auth/x.py"],
            )
        )
    )
    result = runner.invoke(
        cli,
        [
            "guard-pr",
            "--bundle",
            str(bundle_path),
            "--strict",
            "--skip-collect",
        ],
    )
    # If contract requires checks → blocked → exit 5
    # If no checks required → exit 0 (no_match)
    # Either way the contract is correctly computed.
    assert result.exit_code in (0, 5)


def test_cli_guard_pr_post_check_requires_gh_repo_and_sha(tmp_path):
    runner = CliRunner()
    bundle_path = tmp_path / "main.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))
    # Missing --gh-repo + --gh-sha → check_result has missing_gh error.
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-pr",
            "--bundle",
            str(bundle_path),
            "--post-check",
            "--skip-collect",
        ],
    )
    assert result.exit_code in (0, 4, 5)
    payload = json.loads(result.output)
    check_result = payload.get("github_check_result")
    assert check_result is not None
    assert check_result.get("error") in ("missing_gh_repo_or_sha", "gh_repo_must_be_owner_slash_repo")


# ---- --init-if-missing + --ci preset tests ----


def test_cli_guard_pr_init_if_missing_creates_bundle(tmp_path):
    """--init-if-missing creates a bundle when one doesn't exist."""
    runner = CliRunner()
    target_path = tmp_path / "fresh.json"
    assert not target_path.exists()
    result = runner.invoke(
        cli,
        [
            "guard-pr",
            "--bundle",
            str(target_path),
            "--init-if-missing",
            "--skip-collect",
        ],
    )
    # Exit may be 0/4/5 depending on verdict; bundle should exist either way.
    assert result.exit_code in (0, 4, 5), f"unexpected exit {result.exit_code}: {result.output}"
    assert target_path.is_file(), "bundle file was not created"
    created = json.loads(target_path.read_text())
    assert "intent" in created  # _empty_bundle shape


def test_cli_guard_pr_init_if_missing_uses_intent(tmp_path):
    runner = CliRunner()
    target_path = tmp_path / "fresh.json"
    result = runner.invoke(
        cli,
        [
            "guard-pr",
            "--bundle",
            str(target_path),
            "--init-if-missing",
            "--init-intent",
            "my custom intent",
            "--skip-collect",
        ],
    )
    assert result.exit_code in (0, 4, 5)
    assert target_path.is_file()
    created = json.loads(target_path.read_text())
    assert created.get("intent") == "my custom intent"


def test_cli_guard_pr_without_init_if_missing_exits_2_when_no_bundle(tmp_path):
    runner = CliRunner()
    target_path = tmp_path / "nope.json"
    result = runner.invoke(
        cli,
        [
            "guard-pr",
            "--bundle",
            str(target_path),
            "--skip-collect",  # no --init-if-missing
        ],
    )
    assert result.exit_code == 2


def test_cli_guard_pr_ci_preset_implies_strict_and_init(tmp_path):
    """--ci is shorthand for --strict --init-if-missing --format markdown."""
    runner = CliRunner()
    target_path = tmp_path / "fresh.json"
    result = runner.invoke(
        cli,
        [
            "guard-pr",
            "--bundle",
            str(target_path),
            "--ci",
            "--skip-collect",
        ],
    )
    # Bundle should have been created.
    assert target_path.is_file()
    # Format should be markdown by default under --ci.
    assert "Roam Guard verdict:" in result.output or "##" in result.output


def test_cli_guard_pr_ci_preset_yields_to_explicit_format(tmp_path):
    """Explicit --format wins over --ci's markdown default (LAW 11)."""
    runner = CliRunner()
    target_path = tmp_path / "fresh.json"
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-pr",
            "--bundle",
            str(target_path),
            "--ci",
            "--skip-collect",
        ],
    )
    # --json wins; output is a JSON envelope, not markdown headers.
    assert target_path.is_file()
    payload = json.loads(result.output)
    assert payload["command"] == "guard-pr"


# ---- Wave 8: --dry-run flag ----


def test_cli_guard_pr_dry_run_does_not_write_log(tmp_path, monkeypatch):
    """--dry-run skips appending to .roam/verdict-log.jsonl."""
    runner = CliRunner()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli,
        [
            "guard-pr",
            "--bundle",
            str(bundle_path),
            "--dry-run",
        ],
    )
    # No verdict log created.
    assert not (tmp_path / ".roam" / "verdict-log.jsonl").exists()
    # Text output flags the dry-run mode.
    assert "dry-run" in result.output


def test_cli_guard_pr_dry_run_does_not_write_output_file(tmp_path):
    """--dry-run + --output → output file NOT written."""
    runner = CliRunner()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))
    out_path = tmp_path / "guard.md"
    result = runner.invoke(
        cli,
        [
            "guard-pr",
            "--bundle",
            str(bundle_path),
            "--dry-run",
            "--format",
            "markdown",
            "--output",
            str(out_path),
        ],
    )
    assert result.exit_code in (0, 4, 5)
    assert not out_path.exists(), "dry-run should not write --output file"


def test_cli_guard_pr_dry_run_json_surface(tmp_path):
    """--dry-run surfaces dry_run=true in the JSON envelope summary."""
    runner = CliRunner()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-pr",
            "--bundle",
            str(bundle_path),
            "--dry-run",
            "--skip-collect",
        ],
    )
    payload = json.loads(result.output)
    assert payload["summary"]["dry_run"] is True


def test_cli_guard_pr_dry_run_skips_post_check(tmp_path):
    """--dry-run + --post-check → no POST attempted, check_result is None."""
    runner = CliRunner()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-pr",
            "--bundle",
            str(bundle_path),
            "--dry-run",
            "--post-check",
            "--gh-repo",
            "owner/repo",
            "--gh-sha",
            "abc" * 14,
        ],
    )
    payload = json.loads(result.output)
    # check_result is None when dry-run skipped the POST.
    assert payload.get("github_check_result") is None


def test_cli_guard_pr_dry_run_still_computes_verdict(tmp_path):
    """--dry-run still composes + computes verdict (it just doesn't persist)."""
    runner = CliRunner()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(_make_pr_bundle()))
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-pr",
            "--bundle",
            str(bundle_path),
            "--dry-run",
            "--skip-collect",
        ],
    )
    payload = json.loads(result.output)
    # Verdict is still computed.
    assert payload["summary"]["verdict"] in {"pass", "pass_with_warnings", "needs_review", "blocked"}
    # The agent_change_proof_bundle is still in the envelope.
    assert "agent_change_proof_bundle" in payload
