"""Exercise serialized child results through audit's real capture boundary."""

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

import roam.cli as cli_module
from roam.commands import cmd_audit, cmd_dogfood


@pytest.mark.parametrize("brief", [False, True])
@pytest.mark.parametrize("outcome", ["failed", "incomplete", "complete", "gate", "shortened", "unknown_shortened"])
def test_child_evidence_survives_summary_consumption(monkeypatch, brief, outcome):
    @click.command(context_settings={"ignore_unknown_options": True})
    @click.argument("args", nargs=-1)
    def producer(args):
        summary = {"verdict": "checked", "partial_success": False}
        if "stale-refs" in args:
            if outcome == "failed":
                raise click.ClickException("controlled child failure")
            if outcome == "incomplete":
                summary.update(partial_success=True, files_scanned=3, files_unreadable=1)
            if outcome == "shortened":
                summary.update(truncated=True, truncation_reason="detail_mode")
            if outcome == "unknown_shortened":
                summary.update(truncated=True, truncation_reason="display limit")
        click.echo(json.dumps({"summary": summary}))
        if "stale-refs" in args and outcome == "gate":
            raise SystemExit(5)

    monkeypatch.setattr(cli_module, "cli", producer)
    monkeypatch.setattr(cmd_audit, "ensure_index", lambda: None)
    result = CliRunner().invoke(cmd_audit.audit, ["--brief"] if brief else [], obj={"json": True})
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    summary = report["summary"]
    if outcome in {"failed", "incomplete", "unknown_shortened"}:
        assert summary["partial_success"] is True
        assert "stale_refs" in summary["verdict"]
        evidence = summary["child_evidence"]["stale_refs"]
        if outcome == "failed":
            assert evidence["state"] == "failed"
            assert "exit 1" in evidence["error"]
        else:
            assert evidence["state"] == "incomplete"
            if outcome == "incomplete":
                assert evidence["summary"]["files_scanned"] == 3
                assert evidence["summary"]["files_unreadable"] == 1
            else:
                assert evidence["summary"]["truncation_reason"] == "display limit"
    else:
        assert not summary.get("partial_success", False)
        if outcome == "shortened":
            assert summary["child_evidence"]["stale_refs"]["state"] == "detail_omitted"

    @click.command(context_settings={"ignore_unknown_options": True})
    @click.argument("args", nargs=-1)
    def audit_producer(args):
        click.echo(json.dumps(report))

    monkeypatch.setattr(cli_module, "cli", audit_producer)
    monkeypatch.setattr(cmd_dogfood, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_dogfood, "git_metadata", lambda: {})
    consumed = CliRunner().invoke(cmd_dogfood.dogfood_cmd, ["--no-pr-analyze", "--no-audit-trail"], obj={"json": True})
    assert consumed.exit_code == 0, consumed.output
    dogfood_summary = json.loads(consumed.output)["summary"]
    assert bool(dogfood_summary.get("partial_success")) == (outcome in {"failed", "incomplete", "unknown_shortened"})
    if outcome in {"failed", "incomplete", "unknown_shortened"}:
        assert dogfood_summary["incomplete_sections"] == ["audit"]


def test_real_indexed_dogfood_discloses_missing_tests(tmp_path, monkeypatch):
    from tests.conftest import git_init, index_in_process

    (tmp_path / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def identity(value):\n    return value\n", encoding="utf-8")
    git_init(tmp_path)
    monkeypatch.chdir(tmp_path)
    output, code = index_in_process(tmp_path)
    assert code == 0, output
    result = CliRunner().invoke(cli_module.cli, ["--json", "dogfood", "--no-pr-analyze", "--no-audit-trail"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    # This is the same summary flag read by the hosted Dogfood workflow.
    assert envelope["summary"]["partial_success"] is True
    evidence = envelope["summary"]["child_evidence"]["audit"]["child_evidence"]["test_pyramid"]
    assert evidence["summary"]["state"] == "no_test_files"
    audit = envelope["sections"]["audit"]
    debt = audit["sections"]["debt"]["summary"]
    dead = audit["sections"]["dead"]["summary"]
    assert audit["summary"]["debt_total"] == debt.get("total_remediation_minutes")
    assert audit["summary"]["debt_score_total"] == debt["total_debt"]
    assert audit["summary"]["dead_count"] == sum(dead[key] for key in ("safe", "review", "intentional"))


@pytest.mark.parametrize("capture", ["audit", "dogfood"])
@pytest.mark.parametrize("exit_code", [0, 5, 6, 2])
@pytest.mark.parametrize("diagnostic", [False, True])
def test_capture_preserves_structured_stdout_and_exit_status(monkeypatch, capture, exit_code, diagnostic):
    @click.command(context_settings={"ignore_unknown_options": True})
    @click.argument("args", nargs=-1)
    def producer(args):
        click.echo(json.dumps({"summary": {"verdict": "observed", "files_scanned": 3}}))
        if diagnostic:
            click.echo("diagnostic on stderr", err=True)
        raise SystemExit(exit_code)

    monkeypatch.setattr(cli_module, "cli", producer)
    payload = cmd_audit._capture(["health"]) if capture == "audit" else cmd_dogfood._run_subcommand(["--json", "audit"])
    assert payload["summary"]["files_scanned"] == 3
    error_key = "_error" if capture == "audit" else "_subcommand_failed"
    assert bool(payload.get(error_key)) == (exit_code not in (0, 5, 6))
    if exit_code == 6:
        assert payload["partial_success"] is True
    if exit_code not in (0, 5):
        assert payload["exit_code"] == exit_code


@pytest.mark.parametrize("brief", [False, True])
def test_unreadable_document_retains_real_scan_counts(tmp_path, monkeypatch, brief):
    from pathlib import Path

    from roam.commands import cmd_stale_refs
    from tests.conftest import git_init, index_in_process

    (tmp_path / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def identity(value):\n    return value\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("def test_identity():\n    assert True\n", encoding="utf-8")
    blocked = tmp_path / "blocked.md"
    blocked.write_text("[Missing](missing.md)\n", encoding="utf-8")
    (tmp_path / "readable.md").write_text("Readable documentation.\n", encoding="utf-8")
    git_init(tmp_path)
    monkeypatch.chdir(tmp_path)
    output, code = index_in_process(tmp_path)
    assert code == 0, output
    real_open = open

    def controlled_open(path, *args, **kwargs):
        if Path(path).resolve() == blocked.resolve():
            raise PermissionError("controlled unreadable document")
        return real_open(path, *args, **kwargs)

    # Fail the actual scanner's file-open boundary, not its emitted envelope.
    monkeypatch.setattr(cmd_stale_refs, "open", controlled_open, raising=False)
    result = CliRunner().invoke(cli_module.cli, ["--json", "audit", *(["--brief"] if brief else [])])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["partial_success"] is True
    child = summary["child_evidence"]["stale_refs"]["summary"]
    assert child["scan_incomplete"] is True
    assert child["files_unreadable"] == 1
    assert child["files_scanned"] >= 1
