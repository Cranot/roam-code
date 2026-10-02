"""Reference absence, graph membership and deletion authority stay distinct.

Intentional integration I/O: isolated Git trees, the real index/search and CLI
launcher, and MCP stdio verify boundaries that mocking those components would
erase. All fixture writes are under tmp_path; no network or project Make recipe
is invoked. Timeouts bound stuck processes, not performance expectations.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from roam.commands.grep_helpers import SearchEngineError
from tests.conftest import index_in_process, invoke_cli, parse_json_output

LAUNCHER = Path(sys.executable).with_name("roam.exe" if os.name == "nt" else "roam")


@pytest.fixture
def reference_project(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("ROAM_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_GREP_ENGINE", "git")
    (tmp_path / ".gitignore").write_text(".roam/\n*.log\n", encoding="utf-8")
    (tmp_path / ".roamignore").write_text("excluded.c\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        'def active():\n    return "KNOWN_ONLY KNOWN_SHARED"\n\n'
        "def main():\n    return active()\n\n"
        'def orphan():\n    return "ORPHAN_TOKEN"\n\n'
        'print("TOP_LEVEL_TOKEN")\n',
        encoding="utf-8",
    )
    (tmp_path / "excluded.c").write_text(
        'void wrapper(void) { consume("EXCLUDED_TOKEN KNOWN_SHARED"); }\n', encoding="utf-8"
    )
    # Actual default discovery-size refusal, not a stubbed graph. The padding
    # is inert source text; the entire source is still searched by Git.
    (tmp_path / "large.c").write_text(
        "/*" + "x" * 1_000_001 + '*/\nvoid large(void) { consume("LARGE_TOKEN"); }\n',
        encoding="utf-8",
    )
    (tmp_path / "shader.metal").write_text("// UNSUPPORTED_TOKEN\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("DOC_ONLY_TOKEN\n", encoding="utf-8")
    for args in (
        ["init"],
        ["config", "user.email", "fixture@example.invalid"],
        ["config", "user.name", "Reference fixture"],
        ["add", "."],
        ["-c", "core.hooksPath=", "-c", "commit.gpgsign=false", "commit", "-m", "fixture"],
    ):
        # Real local Git setup is required to verify the indexed/searchable
        # population split; mocking it would remove the defect under test.
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, timeout=20, check=True)
    output, code = index_in_process(tmp_path)
    assert code == 0, output
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        paths = {row["path"] for row in conn.execute("SELECT path FROM files")}
        assert "app.py" in paths and "excluded.c" not in paths and "large.c" not in paths
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] > 0
    return tmp_path


def _read(cli_runner, project, *args):
    result = invoke_cli(cli_runner, ["--budget", "0", "refs-text", *args], cwd=project, json_mode=True)
    assert result.exit_code == 0, result.output
    return parse_json_output(result, "refs-text")


def _absence_can_advance(payload):
    """Example acting consumer: continue review, never authorize deletion.

    This deliberately checks the legacy verdict first: the historical envelope
    would advance even when every code hit was outside the indexed graph.
    """
    return (
        payload["summary"].get("partial_success") is not True
        and bool(payload["results"])
        and all(row["verdict"] == "SAFE-TO-REMOVE" for row in payload["results"])
    )


@pytest.mark.parametrize("target", ["EXCLUDED_TOKEN", "LARGE_TOKEN", "TOP_LEVEL_TOKEN", "UNSUPPORTED_TOKEN"])
@pytest.mark.parametrize("detail", [False, True])
def test_unresolved_reference_never_clears_removal(cli_runner, reference_project, target, detail):
    payload = _read(cli_runner, reference_project, target, *(["--per-match-detail"] if detail else []))
    row = payload["results"][0]
    assert row["verdict"] == "REVIEW", row
    assert not _absence_can_advance(payload)
    assert payload["summary"]["partial_success"] is True
    assert row["partial_success"] is True
    assert row["resolution"] == "unresolved"
    assert row["unresolved_code_references"] >= 1
    assert payload["summary"]["unresolved_code_references"] == row["unresolved_code_references"]
    assert "unknown" in row["reason"].lower()
    assert "unresolved" in payload["summary"]["verdict"].lower()
    assert "unresolved" in payload["agent_contract"]["facts"][0].lower()
    if detail:
        matches = row["matches_by_surface"]["code"]
        assert matches
        assert all("reachable" in match and match["reachable"] is None for match in matches)
        assert all(match["resolution"] == "unresolved" for match in matches)
        assert all(match.get("pagerank") is None for match in matches)


@pytest.mark.parametrize("target", ["EXCLUDED_TOKEN", "LARGE_TOKEN", "TOP_LEVEL_TOKEN", "UNSUPPORTED_TOKEN"])
def test_text_does_not_call_unknown_references_unreachable(cli_runner, reference_project, target):
    result = invoke_cli(cli_runner, ["refs-text", target], cwd=reference_project)
    assert result.exit_code == 0, result.output
    assert "REVIEW" in result.output
    assert "SAFE-TO-REMOVE" not in result.output
    assert "[reachability unknown]" in result.output
    assert "[unreachable]" not in result.output


def test_positive_fact_survives_unrelated_missing_coverage(cli_runner, reference_project):
    payload = _read(cli_runner, reference_project, "KNOWN_ONLY", "--per-match-detail")
    row = payload["results"][0]
    assert row["verdict"] == "LOAD-BEARING", row
    assert payload["summary"]["partial_success"] is False
    assert row["partial_success"] is False
    assert row["resolution"] == "resolved"
    assert row["matches_by_surface"]["code"][0]["reachable"] is True


def test_positive_fact_survives_related_unknown_reference(cli_runner, reference_project):
    payload = _read(cli_runner, reference_project, "KNOWN_SHARED", "--per-match-detail")
    row = payload["results"][0]
    assert row["verdict"] == "LOAD-BEARING", row
    assert row["partial_success"] is True
    assert row["resolution"] == "partial"
    assert row["unresolved_code_references"] == 1
    assert payload["summary"]["partial_success"] is True
    assert not _absence_can_advance(payload)


@pytest.mark.parametrize("target", ["NO_SUCH_LITERAL_7492", "DOC_ONLY_TOKEN"])
def test_completed_negative_stays_scoped_not_blanket_deletion_approval(cli_runner, reference_project, target):
    payload = _read(cli_runner, reference_project, target)
    assert _absence_can_advance(payload)
    assert payload["summary"]["partial_success"] is False
    assert payload["results"][0]["resolution"] == "not_applicable"
    assert payload["results"][0]["unresolved_code_references"] == 0
    assert payload["summary"]["search_scope"]["population"] == "git_tracked_nonbinary_files"
    assert payload["summary"]["search_scope"]["complete"] is True
    assert "not deletion approval" in payload["summary"]["verdict_definition"]
    assert "selected search scope" in payload["results"][0]["reason"]


def test_known_static_unreachability_is_not_no_source_references(cli_runner, reference_project):
    payload = _read(cli_runner, reference_project, "ORPHAN_TOKEN", "--per-match-detail")
    row = payload["results"][0]
    assert row["by_surface"]["dead"] == 1
    assert row["verdict"] == "REVIEW", row
    assert row["partial_success"] is False  # completed graph observation, not a failed check
    assert row["resolution"] == "resolved"
    assert "indexed graph" in row["reason"]
    assert row["matches_by_surface"]["dead"][0]["reachable"] is False
    assert not _absence_can_advance(payload)


def test_unknown_in_one_target_does_not_relabel_other_target(cli_runner, reference_project):
    payload = _read(cli_runner, reference_project, "EXCLUDED_TOKEN", "NO_SUCH_LITERAL_7492", "KNOWN_ONLY")
    rows = {row["string"]: row for row in payload["results"]}
    assert rows["EXCLUDED_TOKEN"]["verdict"] == "REVIEW"
    assert rows["NO_SUCH_LITERAL_7492"]["verdict"] == "SAFE-TO-REMOVE"
    assert rows["NO_SUCH_LITERAL_7492"]["partial_success"] is False
    assert rows["KNOWN_ONLY"]["verdict"] == "LOAD-BEARING"
    assert rows["KNOWN_ONLY"]["partial_success"] is False
    assert payload["summary"]["partial_success"] is True


@pytest.mark.parametrize("positive", [False, True])
def test_unreadable_search_region_never_becomes_confirmed_absence(cli_runner, reference_project, monkeypatch, positive):
    from roam.commands import cmd_refs_text

    real_search = cmd_refs_text.run_search

    def unreadable(**kwargs):
        # Fault at the reader boundary; retain real observed matches. Permission
        # bits are not a portable unreadability fixture on privileged Windows.
        raise SearchEngineError("unreadable search region", real_search(**kwargs))

    monkeypatch.setattr(cmd_refs_text, "run_search", unreadable)
    payload = _read(cli_runner, reference_project, "KNOWN_ONLY" if positive else "NO_SUCH_LITERAL_7492")
    row = payload["results"][0]
    assert row["verdict"] == ("LOAD-BEARING" if positive else "REVIEW")
    assert row["partial_success"] is True
    assert payload["summary"]["search_scope"]["complete"] is False
    assert not _absence_can_advance(payload)


@pytest.mark.parametrize("budget", [0, 1])
def test_actual_venv_cli_keeps_unknown_in_protected_summary(reference_project, budget):
    assert LAUNCHER.is_file(), f"test requires installed venv launcher: {LAUNCHER}"
    # Exercise real process serialization with isolated cwd and bounded lifetime.
    result = subprocess.run(
        [str(LAUNCHER), "--json", "--budget", str(budget), "refs-text", "EXCLUDED_TOKEN", "--per-match-detail"],
        cwd=reference_project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["partial_success"] is True
    assert payload["summary"]["unresolved_code_references"] == 1
    assert "unresolved" in payload["agent_contract"]["facts"][0].lower()
    assert not _absence_can_advance(payload)


@pytest.mark.parametrize("mode", ["--compact", "--agent"])
def test_actual_compact_modes_preserve_reference_uncertainty(reference_project, mode):
    # Exercise the installed CLI's presentation switches, not a mocked formatter.
    result = subprocess.run(
        [str(LAUNCHER), "--json", "--budget", "0", mode, "refs-text", "EXCLUDED_TOKEN"],
        cwd=reference_project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["partial_success"] is True
    assert payload["summary"]["unresolved_code_references"] == 1
    assert "unresolved" in payload["summary"]["verdict"]
    assert "not deletion approval" in payload["summary"]["verdict_definition"]


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("entry", ["main", "not_an_indexed_entry_943"])
def test_empty_search_resolves_requested_entry(cli_runner, reference_project, entry, json_mode):
    result = invoke_cli(
        cli_runner,
        ["refs-text", "NO_SUCH_LITERAL_7492", "--reachable-from", entry],
        cwd=reference_project,
        json_mode=json_mode,
    )
    if entry == "main":
        assert result.exit_code == 0, result.output
        if json_mode:
            payload = parse_json_output(result, "refs-text")
            assert payload["summary"]["reachable_from"] == entry
            assert _absence_can_advance(payload)
        else:
            assert "reachability anchored at entry: main" in result.output
    else:
        assert result.exit_code == 1, result.output
        if json_mode:
            payload = json.loads(result.stdout)
            assert payload["summary"]["state"] == "unresolved_entry"
            assert payload["summary"]["partial_success"] is True
            assert not _absence_can_advance(payload)
        else:
            assert "not found in index" in result.output
            assert "SAFE-TO-REMOVE" not in result.output


@pytest.mark.parametrize("regex", [False, True])
def test_cli_regex_and_literal_answer_different_questions(cli_runner, reference_project, regex):
    payload = _read(cli_runner, reference_project, "KNOWN_ONLY|EXCLUDED_TOKEN", *(["-E"] if regex else []))
    assert payload["results"][0]["verdict"] == ("LOAD-BEARING" if regex else "SAFE-TO-REMOVE")
    assert payload["summary"]["partial_success"] is regex


def test_more_missing_graph_membership_cannot_improve_clearance(cli_runner, reference_project):
    from roam.db.connection import open_db

    before = _read(cli_runner, reference_project, "KNOWN_SHARED", "--per-match-detail")
    assert before["results"][0]["verdict"] == "LOAD-BEARING"
    with open_db() as conn:
        # Controlled omission of already indexed symbol spans; no source change.
        conn.execute("UPDATE symbols SET line_start = NULL, line_end = NULL")
        conn.commit()
    after = _read(cli_runner, reference_project, "KNOWN_SHARED", "--per-match-detail")
    assert after["results"][0]["verdict"] == "REVIEW"
    assert after["results"][0]["unresolved_code_references"] > before["results"][0]["unresolved_code_references"]
    assert not _absence_can_advance(before) and not _absence_can_advance(after)


def test_actual_mcp_stdio_preserves_unknown_positive_and_empty(reference_project):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def observe():
        env = dict(os.environ, ROAM_MCP_PRESET="full", ROAM_MCP_WATCH="0", ROAM_MCP_HANDLE_KB="1")
        params = StdioServerParameters(
            command=str(LAUNCHER), args=["mcp", "--no-auto-index"], cwd=reference_project, env=env
        )
        with (reference_project / "server.log").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                    await session.initialize()
                    for target, expected, partial, literal_mode in (
                        ("EXCLUDED_TOKEN", "REVIEW", True, True),
                        ("KNOWN_ONLY", "LOAD-BEARING", False, True),
                        ("NO_SUCH_LITERAL_7492", "SAFE-TO-REMOVE", False, True),
                        ("KNOWN_ONLY|EXCLUDED_TOKEN", "LOAD-BEARING", True, False),
                        ("KNOWN_ONLY|EXCLUDED_TOKEN", "SAFE-TO-REMOVE", False, True),
                    ):
                        result = await session.call_tool(
                            "roam_refs_text",
                            {
                                "strings": target,
                                "root": str(reference_project),
                                "per_match_detail": True,
                                "fixed": literal_mode,
                            },
                        )
                        assert not result.isError, result
                        payload = json.loads(next(item.text for item in result.content if item.type == "text"))
                        # Force real handle delivery, then fetch the stored artifact.
                        assert payload["is_handle"] is True, payload
                        fetched = await session.call_tool("roam_fetch_handle", {"handle": payload["handle"]})
                        assert not fetched.isError, fetched
                        report = json.loads(next(item.text for item in fetched.content if item.type == "text"))
                        assert report["has_more"] is False, report
                        full = report["parsed"]
                        assert full["results"][0]["verdict"] == expected, full["results"][0]
                        assert payload["summary"]["partial_success"] is partial
                        assert full["summary"]["partial_success"] is partial
                        assert _absence_can_advance(full) is (expected == "SAFE-TO-REMOVE")
                        if target == "EXCLUDED_TOKEN":
                            assert full["results"][0]["matches_by_surface"]["code"][0]["reachable"] is None

    asyncio.run(asyncio.wait_for(observe(), timeout=120))
