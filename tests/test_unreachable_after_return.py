"""Tests for the precision unreachable-after-return detector."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


def _classify(symbol):
    from roam.db.connection import open_db
    from roam.world_model.unreachable_after_return import classify_unreachable_after_return

    with open_db(readonly=True) as conn:
        return classify_unreachable_after_return(conn, symbol_name=symbol)


def test_unreachable_after_return_flags_dead_code(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def f(a):\n    return a\n    print('dead')\n"})
    monkeypatch.chdir(proj)

    findings = _classify("f")

    assert len(findings) == 1
    assert findings[0].kind == "unreachable_after_return"
    assert findings[0].terminator == "return"


def test_unreachable_after_return_flags_dead_code_after_raise(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def g(a):\n    raise ValueError('x')\n    cleanup()\n"})
    monkeypatch.chdir(proj)

    findings = _classify("g")

    assert len(findings) == 1
    assert findings[0].terminator == "raise"


def test_unreachable_after_return_is_conservative_for_compound_siblings(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def h(a):\n    if a:\n        return 1\n    do_more()\n"})
    monkeypatch.chdir(proj)

    assert _classify("h") == []


def test_unreachable_after_return_ignores_last_terminator(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def k(a):\n    x = 1\n    return x\n"})
    monkeypatch.chdir(proj)

    assert _classify("k") == []


def test_unreachable_after_return_does_not_reason_across_branches(project_factory, monkeypatch):
    proj = project_factory(
        {"src/m.py": "def m(a):\n    if a:\n        return 1\n    else:\n        return 2\n    tidy()\n"}
    )
    monkeypatch.chdir(proj)

    assert _classify("m") == []


def test_unreachable_after_return_is_opt_in_and_reachable_through_verify(
    project_factory,
    cli_runner,
    monkeypatch,
):
    proj = project_factory({"src/m.py": "def f(a):\n    return a\n    print('dead')\n"})
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["verify", "--checks", "unreachable_after_return", "src/m.py"],
        cwd=proj,
        json_mode=True,
    )
    data = parse_json_output(result, "verify")
    assert_json_envelope(data, "verify")
    assert data["summary"]["checks_run"] == ["unreachable_after_return"]
    violations = data["categories"]["unreachable_after_return"]["violations"]
    assert len(violations) == 1
    assert violations[0]["symbol"] == "f"

    from roam.commands.cmd_verify import _ALL_CHECKS, _DEFAULT_CHECKS

    assert "unreachable_after_return" in _ALL_CHECKS
    assert "unreachable_after_return" not in _DEFAULT_CHECKS
