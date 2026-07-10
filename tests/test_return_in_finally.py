"""Tests for the precision-first return-in-finally detector."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


def _classify(symbol):
    from roam.db.connection import open_db
    from roam.world_model.return_in_finally import classify_return_in_finally

    with open_db(readonly=True) as conn:
        return classify_return_in_finally(conn, symbol_name=symbol)


def test_return_in_finally_flags_return_once(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "def risky():\n    pass\n\n"
                "def guarded(value):\n"
                "    try:\n        risky()\n"
                "    finally:\n        return (value.attr, (value,))\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify("guarded")

    assert len(findings) == 1
    assert findings[0].kind == "return_in_finally"
    assert findings[0].statement_kind == "return"
    assert findings[0].line_end >= findings[0].line_start


def test_return_in_try_is_silent(project_factory, monkeypatch):
    proj = project_factory(
        {"src/example.py": "def guarded():\n    try:\n        return (1, 2)\n    finally:\n        cleanup.attr()\n"}
    )
    monkeypatch.chdir(proj)

    assert _classify("guarded") == []


def test_break_inside_finally_loop_is_silent(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "def guarded(items):\n"
                "    try:\n        risky()\n"
                "    finally:\n"
                "        for item in items:\n            if item:\n                break\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    assert _classify("guarded") == []


def test_direct_break_and_continue_are_flagged(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "def break_guarded():\n"
                "    while ready():\n"
                "        try:\n            work()\n"
                "        finally:\n            break\n\n"
                "def continue_guarded():\n"
                "    while ready():\n"
                "        try:\n            work()\n"
                "        finally:\n            continue\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    assert _classify("break_guarded")[0].statement_kind == "break"
    assert _classify("continue_guarded")[0].statement_kind == "continue"


def test_nested_scopes_are_silent_and_multiple_offenders_are_deduplicated(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "def guarded():\n"
                "    try:\n        work()\n"
                "    finally:\n"
                "        def nested():\n            return 1\n"
                "        callback = lambda: 2\n"
                "        return (nested, callback)\n"
                "        return 3\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify("guarded")
    assert len(findings) == 1
    assert findings[0].statement_kind == "return"


def test_return_in_finally_is_opt_in_and_reachable_through_verify(
    project_factory,
    cli_runner,
    monkeypatch,
):
    proj = project_factory(
        {
            "src/example.py": ("def guarded():\n    try:\n        risky()\n    finally:\n        return None\n"),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["verify", "--checks", "return_in_finally", "src/example.py"],
        cwd=proj,
        json_mode=True,
    )
    data = parse_json_output(result, "verify")
    assert_json_envelope(data, "verify")
    assert "return_in_finally" in data["summary"]["checks_run"]
    violations = data["categories"]["return_in_finally"]["violations"]
    assert len(violations) == 1
    assert violations[0]["statement_kind"] == "return"

    from roam.commands.cmd_verify import _ALL_CHECKS, _DEFAULT_CHECKS

    assert "return_in_finally" in _ALL_CHECKS
    assert "return_in_finally" not in _DEFAULT_CHECKS
