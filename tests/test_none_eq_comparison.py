from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


def _classify(symbol):
    from roam.db.connection import open_db
    from roam.world_model.none_eq_comparison import classify_none_eq_comparison

    with open_db(readonly=True) as conn:
        return classify_none_eq_comparison(conn, symbol_name=symbol)


def test_none_eq_comparison_flags_eq(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def f(a):\n    return a == None\n"})
    monkeypatch.chdir(proj)

    findings = _classify("f")

    assert len(findings) == 1
    assert findings[0].kind == "none_eq_comparison"
    assert findings[0].operator == "=="
    assert findings[0].operand_text == "a"


def test_none_eq_comparison_flags_neq_none_on_left(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def g(a):\n    return None != a.x\n"})
    monkeypatch.chdir(proj)

    findings = _classify("g")

    assert len(findings) == 1
    assert findings[0].operator == "!="
    assert findings[0].operand_text == "a.x"


def test_none_eq_comparison_skips_is_none(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def h(a):\n    return a is None\n"})
    monkeypatch.chdir(proj)

    assert _classify("h") == []


def test_none_eq_comparison_skips_non_none_equality(project_factory, monkeypatch):
    proj = project_factory({"src/m.py": "def k(a, b):\n    return a == b\n"})
    monkeypatch.chdir(proj)

    assert _classify("k") == []


def test_none_eq_comparison_is_opt_in_and_reachable_through_verify(
    project_factory,
    cli_runner,
    monkeypatch,
):
    proj = project_factory({"src/m.py": "def f(a):\n    return a == None\n"})
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["verify", "--checks", "none_eq_comparison", "src/m.py"],
        cwd=proj,
        json_mode=True,
    )
    data = parse_json_output(result, "verify")
    assert_json_envelope(data, "verify")
    assert data["summary"]["checks_run"] == ["none_eq_comparison"]
    violations = data["categories"]["none_eq_comparison"]["violations"]
    assert len(violations) == 1
    assert violations[0]["symbol"] == "f"

    from roam.commands.cmd_verify import _ALL_CHECKS, _DEFAULT_CHECKS

    assert "none_eq_comparison" in _ALL_CHECKS
    assert "none_eq_comparison" not in _DEFAULT_CHECKS
