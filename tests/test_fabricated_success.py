"""Tests for the world-model fabricated-success detector."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


def _classify(symbol):
    from roam.db.connection import open_db
    from roam.world_model.fabricated_success import classify_fabricated_success

    with open_db(readonly=True) as conn:
        return classify_fabricated_success(conn, symbol_name=symbol)


def test_fabricated_success_flags_charge_stub(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/payments.py": ('def charge():\n    return {"status": "success"}\n'),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify("charge")

    assert len(findings) == 1
    assert findings[0].kind == "fabricated_success_stub"
    assert findings[0].declared_sink == "payment"
    assert findings[0].success_shape == "status_success"


def test_fabricated_success_silent_when_real_sink_exists(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/payments.py": (
                "import requests\n\n"
                "def charge():\n"
                '    resp = requests.post("https://payments.example/charge")\n'
                '    return {"status": "success"}\n'
            ),
        }
    )
    monkeypatch.chdir(proj)

    assert _classify("charge") == []


def test_fabricated_success_silent_without_sink_declaration(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/helpers.py": "def helper():\n    return True\n",
        }
    )
    monkeypatch.chdir(proj)

    assert _classify("helper") == []


def test_fabricated_success_is_opt_in_and_reachable_through_verify(
    project_factory,
    cli_runner,
    monkeypatch,
):
    proj = project_factory(
        {
            "src/payments.py": ('def charge():\n    return {"status": "success"}\n'),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["verify", "--checks", "fabricated_success", "src/payments.py"],
        cwd=proj,
        json_mode=True,
    )
    data = parse_json_output(result, "verify")
    assert_json_envelope(data, "verify")
    assert data["summary"]["checks_run"] == ["fabricated_success"]
    violations = data["categories"]["fabricated_success"]["violations"]
    assert len(violations) == 1
    assert violations[0]["symbol"] == "charge"

    from roam.commands.cmd_verify import _ALL_CHECKS, _DEFAULT_CHECKS

    assert "fabricated_success" in _ALL_CHECKS
    assert "fabricated_success" not in _DEFAULT_CHECKS
