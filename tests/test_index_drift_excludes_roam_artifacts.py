"""Roam output stays outside source freshness and ignore rules affect coverage."""

from __future__ import annotations

import json

import pytest

from roam.cli import cli

pytestmark = pytest.mark.usefixtures("pinned_index_freshness_budget")


def test_roam_artifacts_do_not_count_as_source_drift(project_factory, cli_runner, monkeypatch):
    project = project_factory({"app.py": "def helper(): pass\n"})
    monkeypatch.chdir(project)
    (project / ".roam" / "generated.py").write_text("def generated(): pass\n")

    result = cli_runner.invoke(cli, ["--json", "health"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["_meta"]["index_changed_files"] == 0

    (project / ".roamignore").write_text("# project ignore rules\n")
    (project / ".roamignore-findings").write_text("# no excluded findings\n")
    result = cli_runner.invoke(cli, ["--json", "health"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["_meta"]["index_changed_files"] == 2

    (project / ".roamignore").write_text("app.py\n")
    result = cli_runner.invoke(cli, ["--json", "health"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["_meta"]["index_changed_files"] == 3
