"""Tests for the world-model self-comparison detector."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


def _classify(symbol):
    from roam.db.connection import open_db
    from roam.world_model.self_comparison import classify_self_comparison

    with open_db(readonly=True) as conn:
        return classify_self_comparison(conn, symbol_name=symbol)


def test_self_comparison_flags_attribute(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def f(a):\n    return a.x == a.x\n"})
    monkeypatch.chdir(proj)

    findings = _classify("f")

    assert len(findings) == 1
    assert findings[0].kind == "self_comparison"
    assert findings[0].operator == "=="
    assert findings[0].operand_text == "a.x"


def test_self_comparison_silent_for_bare_name_nan_idiom(project_factory, monkeypatch):
    proj = project_factory({"src/n.py": "def g(a):\n    return a == a\n"})
    monkeypatch.chdir(proj)

    assert _classify("g") == []


def test_self_comparison_silent_for_different_operands(project_factory, monkeypatch):
    proj = project_factory({"src/o.py": "def h(a, b):\n    return a.x == b.x\n"})
    monkeypatch.chdir(proj)

    assert _classify("h") == []


def test_self_comparison_is_opt_in_and_reachable_through_verify(
    project_factory,
    cli_runner,
    monkeypatch,
):
    proj = project_factory({"src/m.py": "def f(a):\n    return a.x == a.x\n"})
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["verify", "--checks", "self_comparison", "src/m.py"],
        cwd=proj,
        json_mode=True,
    )
    data = parse_json_output(result, "verify")
    assert_json_envelope(data, "verify")
    assert data["summary"]["checks_run"] == ["self_comparison"]
    violations = data["categories"]["self_comparison"]["violations"]
    assert len(violations) == 1
    assert violations[0]["symbol"] == "f"

    from roam.commands.cmd_verify import _ALL_CHECKS, _DEFAULT_CHECKS

    assert "self_comparison" in _ALL_CHECKS
    assert "self_comparison" not in _DEFAULT_CHECKS
