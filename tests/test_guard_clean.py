"""Tests for `roam guard-clean` log-pruning command."""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli
from roam.guard_log import append_log_entry, log_path_for


def _seed_log(tmp_path, n: int) -> None:
    """Append `n` minimal log entries."""
    for i in range(n):
        append_log_entry(
            tmp_path,
            {
                "ts": f"2026-05-30T00:00:{i:02d}Z",
                "branch": "main",
                "verdict": "pass",
                "changed_files": 0,
                "required": 0,
                "executed": 0,
                "missing": 0,
                "reasons": [],
            },
        )


def test_guard_clean_no_log_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-clean"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "no verdict log present"


def test_guard_clean_already_within_keep_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_log(tmp_path, 5)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-clean", "--keep", "10"])
    payload = json.loads(result.output)
    assert payload["summary"]["removed"] == 0
    assert payload["summary"]["kept"] == 5
    assert log_path_for(tmp_path).read_text(encoding="utf-8").count("\n") == 5


def test_guard_clean_trims_to_last_n(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_log(tmp_path, 20)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-clean", "--keep", "5"])
    payload = json.loads(result.output)
    assert payload["summary"]["removed"] == 15
    assert payload["summary"]["kept"] == 5
    # The 5 surviving entries must be the LAST 5 (ts 15..19).
    lines = log_path_for(tmp_path).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5
    entries = [json.loads(line) for line in lines]
    assert entries[0]["ts"] == "2026-05-30T00:00:15Z"
    assert entries[-1]["ts"] == "2026-05-30T00:00:19Z"


def test_guard_clean_dry_run_does_not_modify_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_log(tmp_path, 10)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-clean", "--keep", "3", "--dry-run"])
    payload = json.loads(result.output)
    assert payload["summary"]["dry_run"] is True
    assert payload["summary"]["removed"] == 7
    # Log itself unchanged.
    lines = log_path_for(tmp_path).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 10


def test_guard_clean_keep_zero_clears_all(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_log(tmp_path, 4)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-clean", "--keep", "0"])
    payload = json.loads(result.output)
    assert payload["summary"]["kept"] == 0
    assert payload["summary"]["removed"] == 4
    # Log present but empty.
    assert log_path_for(tmp_path).read_text(encoding="utf-8") == ""


def test_guard_clean_rejects_negative_keep(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-clean", "--keep", "-1"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["summary"]["error_code"] == "invalid_argument"


def test_guard_clean_text_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_log(tmp_path, 6)
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-clean", "--keep", "2"])
    assert result.exit_code == 0
    assert "removed 4 entries" in result.output
