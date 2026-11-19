"""Complete empty answers retain state; review compounds still lack a change to review."""

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.mark.parametrize("command", ["pr-risk", "breaking", "pr-diff"])
def test_clean_diff_is_complete(project_factory, monkeypatch, command):
    root = project_factory({"core.py": "def calculate():\n    return 1\n"})
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["--json", command])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["state"] == "no_changes"
    assert summary["partial_success"] is False


@pytest.mark.parametrize("command,partial", [("review-change", True), ("for-refactor", False)])
def test_compound_interprets_clean_child_state(command, partial):
    from roam.mcp_server import _compound_envelope

    result = _compound_envelope(command, [("pr_risk", {"summary": {"state": "no_changes", "partial_success": False}})])
    assert result["summary"]["partial_success"] is partial
    if partial:
        assert result["summary"]["state"] == "no_changes"


@pytest.mark.parametrize("value", [3, 10])
def test_native_top_and_limit_stay_independent(value):
    from roam.mcp_server import _normalize_aliases

    kwargs, warnings = _normalize_aliases("native", {"top": value, "limit": 20}, {"top", "limit"})
    assert kwargs == {"top": value, "limit": 20}
    assert warnings == []
