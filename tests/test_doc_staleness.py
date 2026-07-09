"""Tests for roam doc-staleness -- stale docstring detection."""

from __future__ import annotations

import ast

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_commit,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


def _function(source):
    return ast.parse(source).body[0]


def test_semantic_drift_flags_phantom_param_from_ast():
    from roam.commands.cmd_doc_staleness import _docstring_facts, _semantic_drift

    node = _function(
        'def greet(name):\n    """\n    Args:\n        missing: no longer accepted\n    """\n    return name\n'
    )
    facts = _docstring_facts(ast.get_docstring(node), None)

    drift = _semantic_drift(facts, node)

    assert drift["phantom_params"] == ["missing"]
    assert drift["has_drift"] is True


def test_semantic_drift_flags_return_documentation_when_body_returns_none():
    from roam.commands.cmd_doc_staleness import _docstring_facts, _semantic_drift

    node = _function('def refresh():\n    """Returns:\n        str: refreshed value\n    """\n    return None\n')
    facts = _docstring_facts(ast.get_docstring(node), None)

    drift = _semantic_drift(facts, node)

    assert drift["return_signature_mismatch"] is True
    assert drift["has_drift"] is True


def test_semantic_drift_ignores_prose_by_default_and_flags_it_with_opt_in(tmp_path):
    from roam.commands.cmd_doc_staleness import _analyze_staleness

    proj = tmp_path / "prose"
    proj.mkdir()
    source = proj / "module.py"
    source.write_text('def poll():\n    """Poll one store."""\n    return 1\n')
    git_init(proj)
    source.write_text('def poll():\n    """Poll one store."""\n    return 2\n')
    git_commit(proj, "change implementation")
    symbols = {
        "module.py": [
            {
                "name": "poll",
                "kind": "function",
                "file_path": "module.py",
                "line_start": 1,
                "line_end": 3,
                "docstring": "Poll one store.",
                "signature": "def poll()",
            }
        ]
    }

    assert _analyze_staleness(symbols, proj, 0) == []
    opt_in = _analyze_staleness(symbols, proj, 0, include_prose_drift=True)
    assert opt_in[0]["reasons"] == ["commit_drift_prose"]


def test_semantic_drift_accepts_accurate_google_docstring():
    from roam.commands.cmd_doc_staleness import _docstring_facts, _semantic_drift

    node = _function(
        "def greet(name):\n"
        '    """\n'
        "    Args:\n"
        "        name: person to greet\n"
        "    Returns:\n"
        "        str: greeting\n"
        '    """\n'
        '    return f"Hello, {name}"\n'
    )
    facts = _docstring_facts(ast.get_docstring(node), None)

    assert _semantic_drift(facts, node)["has_drift"] is False


@pytest.fixture
def staleness_project(tmp_path):
    """Project with a docstring that might go stale."""
    proj = tmp_path / "stale_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "module.py").write_text('def greet(name):\n    """Say hello to the user."""\n    return f"Hello, {name}"\n')
    git_init(proj)
    index_in_process(proj)
    return proj


class TestDocStalenessSmoke:
    def test_exits_zero(self, cli_runner, staleness_project, monkeypatch):
        monkeypatch.chdir(staleness_project)
        result = invoke_cli(cli_runner, ["doc-staleness"], cwd=staleness_project)
        assert result.exit_code == 0

    def test_with_days(self, cli_runner, staleness_project, monkeypatch):
        monkeypatch.chdir(staleness_project)
        result = invoke_cli(cli_runner, ["doc-staleness", "--days", "1"], cwd=staleness_project)
        assert result.exit_code == 0

    def test_empty_project(self, cli_runner, tmp_path, monkeypatch):
        proj = tmp_path / "empty"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "x.py").write_text("x = 1\n")
        git_init(proj)
        index_in_process(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["doc-staleness"], cwd=proj)
        assert result.exit_code == 0


class TestDocStalenessJSON:
    def test_json_envelope(self, cli_runner, staleness_project, monkeypatch):
        monkeypatch.chdir(staleness_project)
        result = invoke_cli(cli_runner, ["doc-staleness"], cwd=staleness_project, json_mode=True)
        data = parse_json_output(result, "doc-staleness")
        assert_json_envelope(data, "doc-staleness")

    def test_json_summary_has_verdict(self, cli_runner, staleness_project, monkeypatch):
        monkeypatch.chdir(staleness_project)
        result = invoke_cli(cli_runner, ["doc-staleness"], cwd=staleness_project, json_mode=True)
        data = parse_json_output(result, "doc-staleness")
        assert "verdict" in data["summary"]


class TestDocStalenessText:
    def test_verdict_line(self, cli_runner, staleness_project, monkeypatch):
        monkeypatch.chdir(staleness_project)
        result = invoke_cli(cli_runner, ["doc-staleness"], cwd=staleness_project)
        assert "VERDICT:" in result.output
