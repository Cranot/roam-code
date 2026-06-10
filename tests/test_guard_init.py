"""Tests for `roam guard-init` bootstrap command."""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def test_guard_init_creates_dot_roam(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-init"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["created_count"] >= 2
    assert (tmp_path / ".roam").is_dir()
    assert (tmp_path / ".roam" / "pr-bundles").is_dir()


def test_guard_init_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ["guard-init"])
    result = runner.invoke(cli, ["--json", "guard-init"])
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "already initialized"
    assert payload["summary"]["existing_count"] >= 2
    assert payload["summary"]["created_count"] == 0


def test_guard_init_writes_rules_stub(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-init", "--with-rules-stub"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["rules_stub"] == ".roam-guard-rules.yml"
    stub = tmp_path / ".roam-guard-rules.yml"
    assert stub.is_file()
    assert "extends: default" in stub.read_text()


def test_guard_init_preserves_existing_stub_without_force(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stub = tmp_path / ".roam-guard-rules.yml"
    stub.write_text("# my custom pack\nextends: default\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-init", "--with-rules-stub"])
    payload = json.loads(result.output)
    assert payload["rules_stub"] is None
    assert "my custom pack" in stub.read_text()


def test_guard_init_force_overwrites_stub(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stub = tmp_path / ".roam-guard-rules.yml"
    stub.write_text("# old\n")
    runner = CliRunner()
    runner.invoke(cli, ["guard-init", "--with-rules-stub", "--force"])
    assert "extends: default" in stub.read_text()


def test_guard_init_text_mode_prints_next_steps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-init"])
    assert result.exit_code == 0
    assert "Next steps:" in result.output
    assert "roam guard-pr --dry-run" in result.output
