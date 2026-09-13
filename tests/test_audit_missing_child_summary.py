"""Missing child measurements stay unknown across the aggregate boundary."""

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

import roam.cli as cli_module
from roam.commands import cmd_audit, cmd_dogfood


@pytest.mark.parametrize("detail", [False, True])
def test_brief_transport_failure_does_not_invent_available_detail(monkeypatch, detail):
    failed = {"_error": "exit 2", "_command": ["health"], "_output_head": "failed"}
    if detail:
        failed["items"] = [{"name": "retained observation"}]
    monkeypatch.setattr(cmd_audit, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_audit, "_capture", lambda args: failed)
    result = CliRunner().invoke(cmd_audit.audit, ["--brief"], obj={"json": True})
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["partial_success"] is True
    assert bool(summary.get("detail_available")) is detail


@pytest.mark.parametrize("consumer", ["audit", "dogfood"])
@pytest.mark.parametrize("case", ["missing", "null", "empty", "malformed", "root_partial", "declared"])
def test_missing_summary_cannot_be_complete(monkeypatch, consumer, case):
    child = {
        "missing": {},
        "null": {"summary": None},
        "empty": {"summary": {}},
        "malformed": {"summary": ["not a mapping"]},
        "root_partial": {"partial_success": True},
        "declared": {"summary": {"verdict": "observed", "partial_success": False}},
    }[case]
    complete = {"summary": {"verdict": "observed", "partial_success": False, "health_score": 79}}
    if consumer == "audit":
        monkeypatch.setattr(cmd_audit, "ensure_index", lambda: None)
        monkeypatch.setattr(cmd_audit, "_capture", lambda args: child if args[0] == "health" else complete)
        target, args = cmd_audit.audit, ["--brief"]
    else:
        monkeypatch.setattr(cmd_dogfood, "ensure_index", lambda: None)
        monkeypatch.setattr(cmd_dogfood, "git_metadata", lambda: {"git_sha": "fixture-sha"})
        monkeypatch.setattr(cmd_dogfood, "_run_subcommand", lambda args: child if "pr-analyze" in args else complete)
        target, args = cmd_dogfood.dogfood_cmd, ["--no-audit-trail"]
    result = CliRunner().invoke(target, args, obj={"json": True})
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["partial_success"] is (case != "declared")
    if consumer == "dogfood":
        assert summary["health_score"] == 79
        assert summary["git_sha"] == "fixture-sha"
        assert summary["sections_run"] == ["audit", "pr_analyze"]


def test_missing_counts_are_not_zero(monkeypatch):
    monkeypatch.setattr(cmd_audit, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_audit, "_capture", lambda args: {})
    result = CliRunner().invoke(cmd_audit.audit, ["--brief"], obj={"json": True})
    summary = json.loads(result.output)["summary"]
    for key in ("danger_zone_count", "test_count", "api_surface", "file_total", "symbol_total", "stale_ref_count"):
        assert summary[key] is None, key
    assert summary["partial_success"] is True


@pytest.mark.parametrize("consumer", ["audit", "dogfood"])
@pytest.mark.parametrize("flag", ["absent", None, 0, "", [], {}, False])
def test_serialized_child_requires_explicit_false_completion(monkeypatch, consumer, flag):
    @click.command(context_settings={"ignore_unknown_options": True})
    @click.argument("args", nargs=-1)
    def producer(args):
        summary = {"verdict": "observed", "partial_success": False, "health_score": 79}
        if (consumer == "audit" and "health" in args) or (consumer == "dogfood" and "audit" in args):
            if flag == "absent":
                summary.pop("partial_success")
            else:
                summary["partial_success"] = flag
        click.echo(json.dumps({"summary": summary}))

    # Retain the actual JSON capture boundary; only the child producer is replaced.
    monkeypatch.setattr(cli_module, "cli", producer)
    if consumer == "audit":
        monkeypatch.setattr(cmd_audit, "ensure_index", lambda: None)
        target, args = cmd_audit.audit, ["--brief"]
    else:
        monkeypatch.setattr(cmd_dogfood, "ensure_index", lambda: None)
        monkeypatch.setattr(cmd_dogfood, "git_metadata", lambda: {})
        target, args = cmd_dogfood.dogfood_cmd, ["--no-pr-analyze", "--no-audit-trail"]
    result = CliRunner().invoke(target, args, obj={"json": True})
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["partial_success"] is (flag is not False)
    if flag is not False:
        key = "health" if consumer == "audit" else "audit"
        assert key in summary["child_evidence"]
        if consumer == "audit":
            assert summary["child_evidence"][key]["state"] == "incomplete"


@pytest.mark.parametrize("consumer", ["audit", "dogfood"])
@pytest.mark.parametrize("budget", [0, 512, 20000])
def test_parent_budget_reaches_serialized_child(monkeypatch, consumer, budget):
    @click.group()
    @click.option("--json", "json_mode", is_flag=True)
    @click.option("--budget", type=int, default=20000)
    @click.pass_context
    def root(ctx, json_mode, budget):
        ctx.obj = {"budget": budget}

    @root.command()
    @click.pass_context
    def health(ctx):
        click.echo(json.dumps({"summary": {"verdict": "observed", "budget_seen": ctx.obj["budget"]}}))

    monkeypatch.setattr(cli_module, "cli", root)
    with click.Context(click.Command("parent"), obj={"budget": budget, "budget_explicit": True}):
        result = (
            cmd_audit._capture(["health"]) if consumer == "audit" else cmd_dogfood._run_subcommand(["--json", "health"])
        )
    assert result["summary"]["budget_seen"] == budget
