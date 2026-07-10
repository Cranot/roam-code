"""Tests for the precision-first redundant-boolean-return detector."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output


def _classify(symbol):
    from roam.db.connection import open_db
    from roam.world_model.redundant_boolean_return import classify_redundant_boolean_return

    with open_db(readonly=True) as conn:
        return classify_redundant_boolean_return(conn, symbol_name=symbol)


def test_redundant_boolean_return_flags_if_else(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def f(a):\n    if a:\n        return True\n    else:\n        return False\n"})
    monkeypatch.chdir(proj)

    findings = _classify("f")

    assert len(findings) == 1
    assert findings[0].kind == "redundant_boolean_return"
    assert findings[0].form == "if_else"


def test_redundant_boolean_return_flags_swapped_if_else(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def g(a):\n    if a:\n        return False\n    else:\n        return True\n"})
    monkeypatch.chdir(proj)

    assert len(_classify("g")) == 1


def test_redundant_boolean_return_rejects_side_effect_branch(project_factory, monkeypatch):
    proj = project_factory(
        {"src/m.py": "def h(a):\n    if a:\n        do_work()\n        return True\n    else:\n        return False\n"}
    )
    monkeypatch.chdir(proj)

    assert _classify("h") == []


def test_redundant_boolean_return_rejects_integer_literals(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def k(a):\n    if a:\n        return 1\n    else:\n        return 0\n"})
    monkeypatch.chdir(proj)

    assert _classify("k") == []


def test_redundant_boolean_return_rejects_elif(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/m.py": (
                "def m(a, b):\n"
                "    if a:\n"
                "        return True\n"
                "    elif b:\n"
                "        return False\n"
                "    else:\n"
                "        return True\n"
            )
        }
    )
    monkeypatch.chdir(proj)

    assert _classify("m") == []


def test_redundant_boolean_return_is_opt_in_and_reachable_through_verify(
    project_factory,
    cli_runner,
    monkeypatch,
):
    proj = project_factory({"src/m.py": "def f(a):\n    if a:\n        return True\n    else:\n        return False\n"})
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["verify", "--checks", "redundant_boolean_return", "src/m.py"],
        cwd=proj,
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result)
    assert data["summary"]["checks_run"] == ["redundant_boolean_return"]
    assert len(data["violations"]) == 1
    assert data["violations"][0]["symbol"] == "f"

    from roam.commands.cmd_verify import _ALL_CHECKS, _DEFAULT_CHECKS

    assert "redundant_boolean_return" in _ALL_CHECKS
    assert "redundant_boolean_return" not in _DEFAULT_CHECKS
