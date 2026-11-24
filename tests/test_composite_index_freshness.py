"""Composite analysis reports the freshness of the index behind its verdict."""

import asyncio
import json
import os
import subprocess
import sys

import click
import pytest
from click.testing import CliRunner
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from roam.cli import cli

pytestmark = pytest.mark.usefixtures("pinned_index_freshness_budget")


@pytest.fixture(autouse=True)
def _isolated_freshness_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "git config"))
    monkeypatch.delenv("ROAM_DB_DIR", raising=False)
    monkeypatch.delenv("ROAM_REFUSE_STALE_INDEX", raising=False)


@pytest.fixture
def edited_project(project_factory, monkeypatch):
    project = project_factory({"app.py": "def indexed_helper():\n    return 1\n"})
    monkeypatch.chdir(project)
    mode = CliRunner().invoke(cli, ["mode", "autonomous_pr"])
    assert mode.exit_code == 0, mode.output
    (project / "app.py").write_bytes(b"def indexed_helper():\r\n    return 200\r\n")
    return project


_COMPOSITES = {
    "audit": [],
    "pr-prep": [],
    "pr-analyze": [],
    "dogfood": ["--no-audit-trail"],
}


def _assert_stale(payload, command):
    assert payload.get("_meta", {}).get("index_state") == "stale", (command, payload.get("summary"))
    assert payload["_meta"]["index_changed_files"] == 1
    summary = payload["summary"]
    assert summary["partial_success"] is True
    if command == "audit":
        # An independently incomplete check must still have its original reason.
        assert {key for key, item in summary["child_evidence"].items() if item["state"] == "incomplete"} == {
            "test_pyramid"
        }
        assert summary["child_evidence"]["test_pyramid"]["summary"]["state"] == "no_test_files"
    elif command == "dogfood":
        assert summary["failed_sections"] == ["audit"]
        assert "pr_analyze" not in summary["incomplete_sections"]
        assert payload["sections"]["pr_analyze"]["_meta"]["index_state"] == "stale"
    else:
        assert not summary.get("failed_subcommands"), summary


@pytest.mark.parametrize("command", _COMPOSITES)
def test_composite_reports_stale_index_without_failing_its_checks(edited_project, cli_runner, monkeypatch, command):
    args = [command, *_COMPOSITES[command]]
    result = cli_runner.invoke(cli, ["--json", *args])
    assert result.exit_code == 0, result.output
    _assert_stale(json.loads(result.stdout), command)
    assert "Index is stale" not in result.stderr

    result = cli_runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert result.stderr.count("Warning: Index is stale") == 1
    assert "Index is stale" not in result.stdout

    monkeypatch.setenv("ROAM_REFUSE_STALE_INDEX", "1")
    result = cli_runner.invoke(cli, ["--json", *args])
    assert result.exit_code != 0
    refusal = json.loads(result.stdout)
    assert refusal["error_code"] == "INDEX_STALE"
    assert refusal["summary"]["state"] == "stale_index"
    assert refusal["_meta"]["index_state"] == "stale"
    assert not refusal.get("sections")


def test_composite_discloses_interrupted_freshness_check(edited_project, cli_runner, monkeypatch):
    run = subprocess.run

    def timeout(command, *args, **kwargs):
        if command[:2] == ["git", "status"] and "--porcelain=v2" in command:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", timeout)
    result = cli_runner.invoke(cli, ["--json", "audit"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_meta"]["index_state"] == "unknown"
    assert "time budget" in payload["_meta"]["index_state_reason"]
    assert payload["summary"]["partial_success"] is True
    assert payload["summary"]["child_evidence"]["test_pyramid"]["summary"]["state"] == "no_test_files"


@pytest.mark.parametrize("command", _COMPOSITES)
def test_composite_shares_its_first_freshness_result(edited_project, cli_runner, monkeypatch, command):
    run = subprocess.run
    statuses = []

    def interrupt_first_check(args, *positional, **kwargs):
        if args[:2] == ["git", "status"] and "--porcelain=v2" in args:
            statuses.append(args)
            if len(statuses) == 1:
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return run(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, "run", interrupt_first_check)
    result = cli_runner.invoke(cli, ["--json", command, *_COMPOSITES[command]])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_meta"]["index_state"] == "unknown"
    assert payload["_meta"]["index_changed_files"] is None
    assert payload["_meta"]["index_state_reason"] == "freshness check exceeded its time budget"
    assert payload["summary"]["partial_success"] is True
    if command == "dogfood":
        assert payload["sections"]["pr_analyze"]["_meta"]["index_state"] == "unknown"
    assert len(statuses) == 1


def test_cached_pr_analysis_checks_index_freshness(edited_project, cli_runner, monkeypatch):
    args = ["--json", "pr-analyze", "--cache"]
    assert cli_runner.invoke(cli, ["index"]).exit_code == 0
    first = cli_runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    assert not json.loads(first.stdout).get("cache_hit")

    # The uncommitted diff (cache key) stays the same while a newly indexed
    # untracked path goes out of scope.
    (edited_project / "scratch.py").write_text("def scratch(): pass\n", encoding="utf-8")
    result = cli_runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    cached = json.loads(result.stdout)
    assert cached["cache_hit"] is True
    assert cached["_meta"]["index_state"] == "stale"
    assert cached["summary"]["partial_success"] is True
    monkeypatch.setenv("ROAM_REFUSE_STALE_INDEX", "1")
    refused = cli_runner.invoke(cli, args)
    assert json.loads(refused.stdout)["error_code"] == "INDEX_STALE"


@pytest.mark.parametrize("rebuild", [False, True], ids=["restore-working-tree", "rebuild-index"])
def test_pr_analysis_recomputes_after_index_becomes_fresh(edited_project, cli_runner, rebuild):
    # Keep the analyzed diff fixed when the checkout/index changes.
    diff = subprocess.check_output(["git", "diff", "HEAD", "--", "app.py"])
    diff_path = edited_project / ".roam" / "review.diff"
    diff_path.write_bytes(diff)
    assert cli_runner.invoke(cli, ["index"]).exit_code == 0
    scratch = edited_project / "scratch.py"
    scratch.write_bytes(b"def scratch(): pass\n")

    def analyze():
        result = cli_runner.invoke(cli, ["--json", "pr-analyze", "--cache", "--input", str(diff_path)])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    stale = analyze()
    assert stale["_meta"]["index_state"] == "stale"
    assert stale["summary"]["partial_success"] is True
    assert analyze()["cache_hit"] is True
    if rebuild:
        assert cli_runner.invoke(cli, ["index"]).exit_code == 0
    else:
        scratch.unlink()
    fresh = analyze()
    assert fresh["_meta"]["index_state"] == "fresh"
    assert fresh["pr_prep"]["_meta"]["index_state"] == "fresh"
    assert not fresh.get("cache_hit")
    assert analyze()["cache_hit"] is True


def test_pr_analysis_cache_key_tracks_index_publication(edited_project, cli_runner):
    assert cli_runner.invoke(cli, ["index"]).exit_code == 0
    args = ["--json", "pr-analyze", "--cache"]

    def analyze():
        result = cli_runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["_meta"]["index_state"] == "fresh"
        return payload

    assert not analyze().get("cache_hit")
    before = analyze()
    assert before["cache_hit"] is True
    assert cli_runner.invoke(cli, ["index", "--force"]).exit_code == 0
    assert not analyze().get("cache_hit")
    after = analyze()
    assert after["cache_hit"] is True
    assert before["cache_key"] != after["cache_key"]


def test_mcp_doctor_reobserves_index_on_stdio_with_omitted_and_empty_arguments(edited_project, cli_runner):
    assert cli_runner.invoke(cli, ["index"]).exit_code == 0

    async def check():
        env = dict(os.environ, ROAM_MCP_PRESET="full", ROAM_MCP_WATCH="0", ROAM_MCP_HANDLE_KB="0")
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "roam", "mcp", "--no-auto-index"], cwd=edited_project, env=env
        )
        with (edited_project / ".roam" / "server.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    async def freshness(args):
                        reply = await session.call_tool("roam_doctor", args)
                        payload = json.loads(next(item.text for item in reply.content if item.type == "text"))
                        return next(item for item in payload["checks"] if item["name"] == "Index freshness")

                    assert (await freshness(None))["state"] == "fresh"
                    (edited_project / "app.py").write_bytes(b"def after_index():\r\n    return 42\r\n")
                    after = await freshness({})
                    assert after["state"] == "stale"
                    assert after["index_changed_files"] == 1

    asyncio.run(check())


@pytest.mark.parametrize("explicit", [False, True], ids=["missing-arguments", "empty-options"])
def test_mcp_composites_disclose_stale_index_over_stdio(edited_project, explicit):
    async def check():
        env = dict(os.environ, ROAM_MCP_PRESET="full", ROAM_MCP_WATCH="0", ROAM_MCP_HANDLE_KB="0")
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "roam", "mcp", "--no-auto-index"], cwd=edited_project, env=env
        )
        with (edited_project / ".roam" / "server.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for command in _COMPOSITES:
                        args = None
                        if explicit:
                            args = {"root": str(edited_project)}
                            if command in {"pr-analyze", "pr-prep"}:
                                args["commit_range"] = ""
                            if command in {"pr-analyze", "dogfood"}:
                                args["diff_path"] = ""
                        reply = await session.call_tool("roam_" + command.replace("-", "_"), args)
                        assert not reply.isError, reply
                        payload = json.loads(next(item.text for item in reply.content if item.type == "text"))
                        _assert_stale(payload, command)
                    for command in ("stats", "diff"):
                        args = None
                        if explicit:
                            args = {"commit_range": ""} if command == "diff" else {}
                        reply = await session.call_tool(f"roam_{command}", args)
                        assert not reply.isError, reply
                        payload = json.loads(next(item.text for item in reply.content if item.type == "text"))
                        _assert_stale(payload, command)

    asyncio.run(check())


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("explore", {"symbol": "indexed_helper"}),
        ("prepare_change", {"symbol": "indexed_helper"}),
        ("review_change", {}),
        ("diagnose_issue", {"symbol": "indexed_helper"}),
        ("for_new_feature", {"area": "indexed_helper"}),
        ("for_bug_fix", {"symbol": "indexed_helper"}),
        ("for_refactor", {"symbol": "indexed_helper"}),
        ("for_security_review", {}),
    ],
)
def test_mcp_recipes_disclose_staleness_at_the_parent(edited_project, tool, kwargs):
    from roam import mcp_server

    output = getattr(mcp_server, tool)(**kwargs)
    if asyncio.iscoroutine(output):
        output = asyncio.run(output)
    assert output.get("_meta", {}).get("index_state") == "stale", output.get("summary")
    assert output["_meta"]["index_changed_files"] == 1
    assert output["summary"]["partial_success"] is True


def test_mcp_recipe_preserves_strict_stale_refusal(edited_project, monkeypatch):
    from roam import mcp_server

    monkeypatch.setenv("ROAM_REFUSE_STALE_INDEX", "1")
    output = mcp_server.for_bug_fix("indexed_helper")
    assert output.get("error_code") == "INDEX_STALE", output
    assert output["isError"] is True
    assert output["_meta"]["index_state"] == "stale"
    assert output["summary"]["state"] == "stale_index"


def test_mcp_audit_handle_keeps_freshness_disclosure(edited_project, monkeypatch):
    from roam import mcp_server

    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "1")
    output = mcp_server._maybe_handle_off(mcp_server.roam_audit(), tool_name="roam_audit")
    assert output["is_handle"] is True
    assert output["_meta"]["index_state"] == "stale"
    assert output["_meta"]["index_changed_files"] == 1
    assert output["summary"]["partial_success"] is True


def test_watch_keeps_freshness_warning_off_stdout(edited_project, cli_runner, monkeypatch):
    import time

    def stop_after_one_update(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", stop_after_one_update)
    result = cli_runner.invoke(cli, ["pr-analyze", "--watch", "1"])
    assert result.exit_code == 0, result.output
    assert "Warning: Index is stale" in result.stderr
    assert "Index is stale" not in result.stdout


@pytest.mark.parametrize("stream", [False, True], ids=["json", "jsonl"])
def test_batch_reports_index_freshness(edited_project, cli_runner, stream):
    diff_dir = edited_project / ".roam" / "diffs"
    diff_dir.mkdir()
    (diff_dir / "change.diff").write_bytes(subprocess.check_output(["git", "diff"], cwd=edited_project))
    args = ["--json", "pr-analyze", "--batch", str(diff_dir)]
    if stream:
        args.append("--stream-jsonl")
    result = cli_runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    if stream:
        row, payload = [json.loads(line) for line in result.stdout.splitlines()]
        summary = payload["_summary"]
    else:
        payload = json.loads(result.stdout)
        summary = payload["summary"]
        row = payload["files"][0]
    assert payload["_meta"]["index_state"] == "stale"
    assert summary["partial_success"] is True
    assert row["_meta"]["index_changed_files"] == 1


def test_composite_without_git_discloses_why_without_a_warning(tmp_path, cli_runner, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("def helper(): pass\n")
    (tmp_path / "test_app.py").write_text("def test_helper(): pass\n")
    assert cli_runner.invoke(cli, ["index"]).exit_code == 0
    result = cli_runner.invoke(cli, ["--json", "audit"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["_meta"]["index_state"] == "unknown"
    assert payload["_meta"]["index_state_reason"] == "project has no Git worktree"
    assert payload["summary"]["partial_success"] is False
    assert "Could not check index freshness" not in cli_runner.invoke(cli, ["audit"]).stderr


def test_composite_keeps_a_stale_observation_when_reindexed_during_composition(edited_project, cli_runner, monkeypatch):
    from roam.commands import cmd_audit
    from roam.index.indexer import Indexer

    capture = cmd_audit._capture

    def rebuild_after_health(args):
        payload = capture(args)
        if args[0] == "health":
            assert Indexer(edited_project).run(quiet=True)
        return payload

    monkeypatch.setattr(cmd_audit, "_capture", rebuild_after_health)
    result = cli_runner.invoke(cli, ["--json", "audit"])
    assert result.exit_code == 0, result.output
    _assert_stale(json.loads(result.stdout), "audit")
    subsequent = cli_runner.invoke(cli, ["--json", "audit"])
    assert json.loads(subsequent.stdout)["_meta"]["index_state"] == "fresh"


def test_mcp_compound_explains_inconsistent_freshness_metadata():
    from roam.mcp_server import _compound_envelope

    payload = _compound_envelope(
        "explore",
        [
            ("understand", {"summary": {"verdict": "overview"}, "_meta": {"index_state": "fresh"}}),
            (
                "context",
                {"summary": {"verdict": "found symbol"}, "_meta": {"index_state": "fresh", "index_changed_files": 0}},
            ),
        ],
    )
    assert payload["_meta"]["index_state"] == "unknown"
    assert "invalid index freshness metadata" in payload["_meta"]["index_state_reason"]
    assert payload["summary"]["partial_success"] is True
    assert not payload["summary"]["failed_subcommands"]


def test_next_composite_observes_an_edit_between_child_reads(edited_project, cli_runner, monkeypatch):
    from roam.commands import cmd_audit

    assert cli_runner.invoke(cli, ["index"]).exit_code == 0
    capture = cmd_audit._capture

    def edit_after_health(args):
        payload = capture(args)
        if args[0] == "health":
            assert payload["_meta"]["index_state"] == "fresh"
            (edited_project / "app.py").write_bytes(b"def indexed_helper():\n    return 300\n")
        return payload

    with monkeypatch.context() as patch:
        patch.setattr(cmd_audit, "_capture", edit_after_health)
        result = cli_runner.invoke(cli, ["--json", "audit"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["_meta"]["index_state"] == "fresh"

    result = cli_runner.invoke(cli, ["--json", "audit"])
    assert result.exit_code == 0, result.output
    _assert_stale(json.loads(result.stdout), "audit")


@pytest.mark.parametrize("observe_parent_first", [True, False], ids=["parent-first", "child-first"])
def test_composite_reports_its_own_project_freshness(project_factory, monkeypatch, observe_parent_first):
    from roam.db import freshness
    from roam.output.formatter import json_envelope

    parent_root = project_factory({"app.py": "def parent_helper(): pass\n"})
    other_root = project_factory({"app.py": "def original(): pass\n"})
    (other_root / "app.py").write_text("def child_edit(): pass\n")
    monkeypatch.chdir(parent_root)
    probe = freshness.probe_index_freshness
    roots_checked = []

    def record_probe(db_path, root):
        roots_checked.append(root)
        return probe(db_path, root)

    monkeypatch.setattr(freshness, "probe_index_freshness", record_probe)

    @click.command()
    @click.argument("project")
    def child(project):
        root = other_root if project == "other" else parent_root
        observed = freshness.project_index_freshness(root, required=True)
        click.echo(observed.state)

    with click.Context(click.Command("audit"), obj={"json": True}):
        if observe_parent_first:
            assert freshness.project_index_freshness(parent_root, required=True).state == "fresh"
        runner = freshness.IndexCompositeRunner()
        assert runner.invoke(child, ["other"]).output == "stale\n"
        if not observe_parent_first:
            assert freshness.query_index_freshness() is None
        assert runner.invoke(child, ["parent"]).output == "fresh\n"
        assert runner.invoke(child, ["other"]).output == "stale\n"
        payload = json_envelope("audit", persist_response=False)
        assert payload["_meta"]["index_state"] == "fresh"
        assert payload["_meta"]["index_changed_files"] == 0
    assert roots_checked == ([parent_root, other_root] if observe_parent_first else [other_root, parent_root])


@pytest.mark.parametrize("in_composite", [False, True], ids=["same-context", "composite-child"])
def test_required_freshness_rechecks_after_index_is_created(project_factory, monkeypatch, tmp_path, in_composite):
    from roam.db import freshness
    from roam.index.indexer import Indexer
    from roam.output.formatter import json_envelope

    root = project_factory({"app.py": "def helper(): pass\n"}, index=False)
    monkeypatch.chdir(root)
    monkeypatch.setenv("ROAM_DB_DIR", str(tmp_path / "external index"))

    @click.command()
    def child():
        freshness.project_index_freshness(root, required=True)
        click.echo(json.dumps(json_envelope("search", persist_response=False)["_meta"]))

    with click.Context(click.Command("audit"), obj={"json": True}):
        absent = freshness.project_index_freshness(root, required=True)
        assert absent.state == "unknown"
        assert absent.reason == "the index is unavailable"
        assert Indexer(root).run(quiet=True)
        if in_composite:
            result = freshness.IndexCompositeRunner().invoke(child, [], obj={"json": True})
            assert result.exit_code == 0, result.output
            assert json.loads(result.output)["index_state"] == "fresh"
        else:
            assert freshness.project_index_freshness(root, required=True).state == "fresh"
        meta = json_envelope("audit", persist_response=False)["_meta"]
        assert meta["index_state"] == "fresh"
        assert meta["index_changed_files"] == 0
        assert "index_state_reason" not in meta
