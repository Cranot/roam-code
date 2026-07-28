"""Tests for `roam guard-pr` aggregate command + GitHub Check API payload.

Per project_roam_guard_phase2_complete:
- guard-pr wraps auto-collect → compose → render → exit per verdict.
- github_check.build_check_run_payload maps verdict → conclusion.
- No tests hit the network — post_check_run is tested by mocking urlopen.
"""

from __future__ import annotations

import json
import urllib.error

import click
import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_guard_pr
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


def test_post_check_run_rejects_control_chars_in_env_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "token\nX-Injected: yes")

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("urlopen should not be called with an invalid token")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    result = post_check_run(owner="o", repo="r", payload={})

    assert result == {"ok": False, "status": 0, "error": "invalid_github_token"}


def test_post_check_run_strips_token_before_authorization_header(monkeypatch):
    class FakeResponse:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"id": 123}'

    captured = {}
    monkeypatch.setenv("GITHUB_TOKEN", "  token\n")

    def fake_urlopen(req, **_kwargs):
        captured["authorization"] = req.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = post_check_run(owner="o", repo="r", payload={})

    assert result["ok"] is True
    assert result["status"] == 201
    assert result["body"] == {"id": 123}
    assert captured["authorization"] == "Bearer token"


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
    payload = json.loads(result.stdout)
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
    payload = json.loads(result.stdout)
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
    payload = json.loads(result.stdout)
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
    payload = json.loads(result.stdout)
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
    payload = json.loads(result.stdout)
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
    payload = json.loads(result.stdout)
    # Verdict is still computed.
    assert payload["summary"]["verdict"] in {"pass", "pass_with_warnings", "needs_review", "blocked"}
    # The agent_change_proof_bundle is still in the envelope.
    assert "agent_change_proof_bundle" in payload


# ---- gate-suppression disclosure (silent-pass regression) ----
#
# `guard-pr` is reporting-only without --strict/--ci and exits 0 even when its
# own verdict is `blocked`. That default is deliberate — the shipped CI
# templates run a bare `guard-pr --post-check` reporting step under
# `set -euo pipefail`, and it runs precisely when the verdict is blocked, so
# flipping the default to non-zero would break them. What is NOT acceptable is
# doing it silently: printing `blocked` and handing back success with no signal
# is indistinguishable from a clean run to anyone reading the exit status.
# These tests pin the disclosure on both surfaces (stderr banner + JSON).


def _blocking_bundle_path(tmp_path):
    """A bundle whose verdict is `blocked` (required checks exist, none ran)."""
    bundle_path = tmp_path / "blocking.json"
    bundle_path.write_text(
        json.dumps(
            _make_pr_bundle(
                risks=[{"severity": "high", "paths": ["src/auth/x.py"], "description": "auth"}],
                files=["src/auth/x.py"],
            )
        )
    )
    return bundle_path


def test_guard_pr_non_strict_blocked_exits_0_but_says_so_loudly(tmp_path):
    """The core regression: exit 0 on `blocked` is allowed, exit 0 in SILENCE is not."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["guard-pr", "--bundle", str(_blocking_bundle_path(tmp_path)), "--skip-collect"],
    )

    assert result.exit_code == 0, "non-strict guard-pr must stay reporting-only"
    assert "blocked" in result.output

    stderr = result.stderr
    # Names the state, the flag that changes it, and the exit code it withheld.
    assert "NOT gating" in stderr
    assert "--ci" in stderr
    assert "would exit 5" in stderr
    # Loud, not a single grey line lost in the scrollback.
    assert "!!!!" in stderr


def test_guard_pr_non_strict_blocked_marks_gate_suppressed_in_json(tmp_path):
    """A machine consumer must be able to tell a clean verdict from a withheld gate.

    `exit_code` is 0 in BOTH cases, so it cannot carry that distinction alone.
    """
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "guard-pr", "--bundle", str(_blocking_bundle_path(tmp_path)), "--skip-collect"],
    )

    assert result.exit_code == 0
    # The banner goes to stderr, so stdout is still a clean JSON document.
    summary = json.loads(result.stdout)["summary"]

    assert summary["verdict"] == "blocked"
    assert summary["exit_code"] == 0  # what the process actually returned
    assert summary["verdict_exit_code"] == 5  # what the gate would have returned
    assert summary["gate_enforced"] is False
    assert summary["gate_suppressed"] is True


def test_guard_pr_strict_blocked_gates_and_emits_no_banner(tmp_path):
    """Under --strict the gate is real, so there is nothing to disclose."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "guard-pr", "--bundle", str(_blocking_bundle_path(tmp_path)), "--strict", "--skip-collect"],
    )

    assert result.exit_code == 5
    summary = json.loads(result.stdout)["summary"]
    assert summary["gate_enforced"] is True
    assert summary["gate_suppressed"] is False
    assert summary["exit_code"] == summary["verdict_exit_code"] == 5
    assert "NOT gating" not in result.stderr


def test_guard_pr_ci_preset_gates_and_emits_no_banner(tmp_path):
    """--ci implies --strict, so it must gate rather than warn."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "guard-pr", "--bundle", str(_blocking_bundle_path(tmp_path)), "--ci", "--skip-collect"],
    )

    assert result.exit_code == 5
    assert json.loads(result.stdout)["summary"]["gate_suppressed"] is False
    assert "NOT gating" not in result.stderr


def test_guard_pr_non_blocking_verdict_emits_no_banner(tmp_path, monkeypatch):
    """The banner is keyed off the verdict's exit code, not off `--strict` alone.

    A non-blocking verdict in non-strict mode suppresses nothing, so warning
    about it would be noise — and noise is how loud warnings get ignored.
    """
    monkeypatch.setattr(cmd_guard_pr, "verdict_exit_code", lambda _v: 0)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "guard-pr", "--bundle", str(_blocking_bundle_path(tmp_path)), "--skip-collect"],
    )

    assert result.exit_code == 0
    summary = json.loads(result.stdout)["summary"]
    assert summary["gate_suppressed"] is False
    assert summary["verdict_exit_code"] == 0
    assert "NOT gating" not in result.stderr


def test_guard_pr_suppressed_gate_is_disclosed_in_agent_contract(tmp_path):
    """Agents read agent_contract.facts, not stderr."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "guard-pr", "--bundle", str(_blocking_bundle_path(tmp_path)), "--skip-collect"],
    )
    facts = json.loads(result.stdout)["agent_contract"]["facts"]
    assert any("NOT GATING" in f for f in facts), facts


def test_guard_pr_dry_run_still_discloses_suppressed_gate(tmp_path):
    """--dry-run predicts the verdict, so it inherits the same silent-pass trap."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["guard-pr", "--bundle", str(_blocking_bundle_path(tmp_path)), "--dry-run", "--skip-collect"],
    )
    assert result.exit_code == 0
    assert "NOT gating" in result.stderr


def test_guard_pr_banner_names_the_flag_and_the_withheld_code():
    """Unit-level pin on the banner text: a hint that omits the flag is useless."""
    runner = CliRunner()

    @click.command()
    def _probe():
        cmd_guard_pr._emit_gate_suppressed_banner("needs_review", 4)

    result = runner.invoke(_probe)
    stderr = result.stderr
    assert "needs_review" in stderr
    assert "--ci" in stderr
    assert "--strict" in stderr
    assert "would exit 4" in stderr
