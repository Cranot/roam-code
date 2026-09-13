"""Explicit committed-diff input must reach Dogfood's real PR consumer."""

from __future__ import annotations

import json
import subprocess

from click.testing import CliRunner

from roam.cli import cli
from tests.test_dogfood import tiny_indexed  # noqa: F401


def test_dogfood_forwards_explicit_input_without_losing_rules(tmp_path, monkeypatch):
    from roam.commands import cmd_dogfood

    calls = []
    diff = tmp_path / "change.diff"
    diff.write_text("diff --git a/main.py b/main.py\n", encoding="utf-8")
    monkeypatch.setattr(cmd_dogfood, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_dogfood, "git_metadata", lambda: {})

    def child(args):
        calls.append(args)
        return {"summary": {"verdict": "checked", "partial_success": False}}

    monkeypatch.setattr(cmd_dogfood, "_run_subcommand", child)
    result = CliRunner().invoke(
        cli, ["--json", "dogfood", "--no-audit", "--no-audit-trail", "--input", str(diff), "--rules", "rules.yml"]
    )
    assert result.exit_code == 0, result.output
    assert calls == [["--json", "pr-analyze", "--input", str(diff), "--rules", "rules.yml"]]
    assert json.loads(result.stdout)["summary"]["partial_success"] is False


def test_missing_diff_input_is_refused(tmp_path):
    result = CliRunner().invoke(cli, ["--json", "dogfood", "--input", str(tmp_path / "missing.diff")])
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_explicit_input_cannot_be_silently_ignored(tmp_path):
    diff = tmp_path / "change.diff"
    diff.write_text("diff", encoding="utf-8")
    result = CliRunner().invoke(cli, ["--json", "dogfood", "--input", str(diff), "--no-pr-analyze"])
    assert result.exit_code != 0
    assert "--pr-analyze" in result.output


def test_mcp_diff_path_preserves_existing_rules_parameter(monkeypatch):
    import roam.mcp_server as mcp

    function = mcp.dogfood
    while hasattr(function, "fn") or hasattr(function, "__wrapped__"):
        function = function.fn if hasattr(function, "fn") else function.__wrapped__
    calls = []
    monkeypatch.setattr(mcp, "_run_roam", lambda args, root: calls.append((args, root)) or {"ok": True})
    assert function(input_path="rules.yml", diff_path="change.diff", root="repo") == {"ok": True}
    assert calls == [(["dogfood", "--rules", "rules.yml", "--input", "change.diff"], "repo")]


def test_clean_checkout_uses_real_saved_diff(request, tmp_path, cli_runner):
    """Real local Git and CLI in a disposable indexed repo; no network."""
    project = request.getfixturevalue("tiny_indexed")
    source = project / "src/main.py"
    original = source.read_bytes()
    source.write_bytes(original.replace(b"a + b", b"a - b"))
    diff_bytes = subprocess.check_output(["git", "diff", "--no-ext-diff", "--no-textconv"], cwd=project, timeout=15)
    source.write_bytes(original)
    diff = tmp_path / "saved.diff"
    diff.write_bytes(diff_bytes)
    result = cli_runner.invoke(
        cli, ["--json", "--budget", "0", "dogfood", "--no-audit", "--no-audit-trail", "--input", str(diff)]
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["sections"]["pr_analyze"]["summary"].get("state") != "no_changes"
    assert envelope["summary"]["partial_success"] is False


def test_empty_saved_diff_remains_incomplete(request, tmp_path, cli_runner):
    """Exercise the real local consumer: an empty file is not patch evidence."""
    request.getfixturevalue("tiny_indexed")
    diff = tmp_path / "empty.diff"
    diff.write_text("", encoding="utf-8")
    result = cli_runner.invoke(
        cli, ["--json", "--budget", "0", "dogfood", "--no-audit", "--no-audit-trail", "--input", str(diff)]
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["sections"]["pr_analyze"]["summary"]["state"] == "no_changes"
    assert envelope["summary"]["partial_success"] is True
    assert "pr_analyze" in envelope["summary"]["incomplete_sections"]
