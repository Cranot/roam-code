"""Tests for `roam guard-history` (Phase 8 dashboard MVP)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.helpers import make_pr_bundle


@pytest.fixture
def fake_repo_with_bundles(tmp_path, monkeypatch):
    """Create a fake project root with .roam/pr-bundles/ containing 3 bundles."""
    bundles_dir = tmp_path / ".roam" / "pr-bundles"
    bundles_dir.mkdir(parents=True)

    # Bundle A — auth file → blocked verdict expected
    (bundles_dir / "main.json").write_text(
        json.dumps(
            make_pr_bundle(
                intent="auth refactor",
            )
        )
    )

    # Bundle B — empty bundle, no affected → pass
    bundle_b = make_pr_bundle(intent="docs only")
    bundle_b["affected_symbols"] = []
    (bundles_dir / "feat__docs.json").write_text(json.dumps(bundle_b))

    # Bundle C — empty intent
    (bundles_dir / "old.json").write_text(json.dumps(make_pr_bundle(intent="")))

    monkeypatch.chdir(tmp_path)
    # find_project_root looks for git or .roam — make .roam suffice
    return tmp_path


def test_guard_history_text_lists_bundles(fake_repo_with_bundles):
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-history"])
    assert result.exit_code == 0
    assert "VERDICT:" in result.output
    # All three bundles surface
    assert "auth refactor" in result.output or "docs only" in result.output


def test_guard_history_json_envelope(fake_repo_with_bundles):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-history"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["command"] == "guard-history"
    assert "rows" in payload
    assert len(payload["rows"]) >= 1
    row = payload["rows"][0]
    # Schema each row must carry
    for k in ("path", "branch", "verdict", "changed_files", "required_checks"):
        assert k in row


def test_guard_history_no_bundles_dir():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["guard-history"])
        assert result.exit_code == 0
        assert "No pr-bundles found" in result.output


def test_guard_history_limit_caps_rows(fake_repo_with_bundles):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-history", "--limit", "1"])
    payload = json.loads(result.output)
    assert len(payload["rows"]) == 1


def test_guard_history_verdict_filter(fake_repo_with_bundles):
    runner = CliRunner()
    # Filter to blocked — depending on rules, may be empty.
    result = runner.invoke(cli, ["--json", "guard-history", "--verdict", "blocked"])
    payload = json.loads(result.output)
    # Every returned row should have verdict == blocked
    for row in payload["rows"]:
        assert row["verdict"] == "blocked"


def test_guard_history_branch_recovered_from_filename(fake_repo_with_bundles):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-history"])
    payload = json.loads(result.output)
    branches = {r["branch"] for r in payload["rows"]}
    # `feat__docs.json` → branch `feat/docs`
    assert any("/" in b for b in branches) or "main" in branches


# ---- Wave 1: verdict-log fast-path ----


@pytest.fixture
def fake_repo_with_log(tmp_path, monkeypatch):
    """A repo with a verdict log already populated. No pr-bundles."""
    from roam.guard_log import append_log_entry

    for verdict in ("pass", "blocked", "pass_with_warnings"):
        append_log_entry(
            tmp_path,
            {
                "ts": "2026-05-30T00:00:00Z",
                "branch": f"branch-{verdict}",
                "bundle": str(tmp_path / ".roam/pr-bundles" / f"{verdict}.json"),
                "verdict": verdict,
                "changed_files": 1,
                "required": 1,
                "executed": 1 if verdict == "pass" else 0,
                "missing": 0 if verdict == "pass" else 1,
                "intent": f"intent-{verdict}",
            },
        )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_guard_history_uses_log_when_auto_and_log_present(fake_repo_with_log):
    """auto + log present → reads from log without re-composing."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-history"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["source"] == "log"
    assert payload["summary"]["log_available"] is True
    # Every row should be tagged source=log
    for row in payload["rows"]:
        assert row.get("source") == "log"


def test_guard_history_source_compose_forces_compose(fake_repo_with_bundles):
    """--source compose ignores log even if present."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-history", "--source", "compose"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["source"] == "compose"


def test_guard_history_rebuild_flag_overrides_log(fake_repo_with_log):
    """--rebuild forces compose. With no bundles on disk, returns empty rows."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-history", "--rebuild"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["source"] == "compose"
    # No bundles on disk + compose → empty
    assert len(payload["rows"]) == 0


def test_guard_history_log_path_respects_verdict_filter(fake_repo_with_log):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-history",
            "--source",
            "log",
            "--verdict",
            "blocked",
        ],
    )
    payload = json.loads(result.output)
    for row in payload["rows"]:
        assert row["verdict"] == "blocked"


def test_guard_history_falls_back_to_compose_when_no_log(fake_repo_with_bundles):
    """auto + no log → compose path."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-history"])
    payload = json.loads(result.output)
    assert payload["summary"]["source"] == "compose"
    assert payload["summary"]["log_available"] is False


def test_guard_history_branch_filter_via_log(fake_repo_with_log):
    """`--branch <name>` narrows the log path to entries for that branch."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-history",
            "--branch",
            "branch-blocked",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["branch_filter"] == "branch-blocked"
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["branch"] == "branch-blocked"


def test_guard_history_branch_filter_no_match(fake_repo_with_log):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "guard-history",
            "--branch",
            "does-not-exist",
        ],
    )
    payload = json.loads(result.output)
    assert len(payload["rows"]) == 0
