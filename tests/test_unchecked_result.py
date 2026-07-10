"""Tests for the opt-in unchecked-result world-model detector."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


def _classify(symbol):
    from roam.db.connection import open_db
    from roam.world_model.unchecked_result import classify_unchecked_result

    with open_db(readonly=True) as conn:
        return classify_unchecked_result(conn, symbol_name=symbol)


@pytest.mark.parametrize(
    ("callee", "expression"),
    [
        ("re.match", "re.match(r'(\\d+)', s).group(1)"),
        ("re.search", "re.search(r'(\\d+)', s)[0]"),
        ("re.fullmatch", "re.fullmatch(r'(\\d+)', s).group(1)"),
    ],
)
def test_regex_optional_returners_fire(project_factory, monkeypatch, callee, expression):
    proj = project_factory({"src/mod.py": f"import re\ndef f(s):\n    return {expression}\n"})
    monkeypatch.chdir(proj)

    findings = _classify("f")

    assert len(findings) == 1
    assert findings[0].callee == callee
    assert findings[0].access_kind in {"attribute", "subscript"}
    assert findings[0].line_start == 3


def test_get_subscript_fires_and_reports_one_finding(project_factory, monkeypatch):
    proj = project_factory({"src/mod.py": "def f(d, k):\n    return d.get(k)[0]\n"})
    monkeypatch.chdir(proj)

    findings = _classify("f")

    assert len(findings) == 1
    assert findings[0].callee == "dict.get"
    assert findings[0].access_kind == "subscript"


@pytest.mark.parametrize(
    "source",
    [
        "import os\ndef f(k):\n    return os.getenv(k).strip()\n",
        "import os\ndef f(k):\n    return os.environ.get(k).strip()\n",
        "import re\ndef f(s):\n    return (re.search(r'x', s).group(1),)\n",
    ],
)
def test_other_supported_shapes_fire(project_factory, monkeypatch, source):
    proj = project_factory({"src/mod.py": source})
    monkeypatch.chdir(proj)

    assert len(_classify("f")) == 1


def test_assigned_and_guarded_result_is_not_inline(project_factory, monkeypatch):
    proj = project_factory(
        {"src/mod.py": "import re\ndef f(s):\n    m = re.match(r'x', s)\n    if m:\n        return m.group(1)\n"}
    )
    monkeypatch.chdir(proj)

    assert _classify("f") == []


def test_get_with_default_is_not_optional(project_factory, monkeypatch):
    proj = project_factory({"src/mod.py": "def f(d, k, DEFAULT):\n    return d.get(k, DEFAULT)[0]\n"})
    monkeypatch.chdir(proj)

    assert _classify("f") == []


def test_nested_function_and_lambda_bodies_are_skipped(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/mod.py": (
                "import re\n"
                "def f(s):\n"
                "    def inner():\n"
                "        return re.match(r'x', s).group(1)\n"
                "    return (lambda: re.search(r'x', s).group(1))\n"
            )
        }
    )
    monkeypatch.chdir(proj)

    assert _classify("f") == []


@pytest.mark.parametrize("expression", ["self.session.get(url).json()", "self.client.get(path).status_code"])
def test_http_receiver_components_are_excluded(project_factory, monkeypatch, expression):
    proj = project_factory({"src/mod.py": f"def f(self, url, path):\n    return {expression}\n"})
    monkeypatch.chdir(proj)

    assert _classify("f") == []


def test_local_dict_literal_get_still_fires(project_factory, monkeypatch):
    proj = project_factory({"src/mod.py": "def f(k):\n    return {}.get(k)[0]\n"})
    monkeypatch.chdir(proj)

    assert _classify("f")[0].callee == "dict.get"


def test_at_most_one_finding_per_symbol_and_duplicate_suppression(project_factory, monkeypatch):
    proj = project_factory(
        {"src/mod.py": ("import re\ndef f(s):\n    return re.match(r'x', s).group(1), re.search(r'x', s)[0]\n")}
    )
    monkeypatch.chdir(proj)

    findings = _classify("f")
    assert len(findings) == 1


def test_unchecked_result_is_opt_in_and_reachable_through_verify(
    project_factory,
    cli_runner,
    monkeypatch,
):
    proj = project_factory({"src/mod.py": "import re\ndef f(s):\n    return re.match(r'x', s).group(1)\n"})
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["verify", "--checks", "unchecked_result", "src/mod.py"],
        cwd=proj,
        json_mode=True,
    )
    data = parse_json_output(result, "verify")
    assert_json_envelope(data, "verify")
    assert "unchecked_result" in data["summary"]["checks_run"]
    violations = data["categories"]["unchecked_result"]["violations"]
    assert len(violations) == 1
    assert violations[0]["callee"] == "re.match"

    from roam.commands.cmd_verify import _ALL_CHECKS, _DEFAULT_CHECKS

    assert "unchecked_result" in _ALL_CHECKS
    assert "unchecked_result" not in _DEFAULT_CHECKS
