"""Separate complete Debt collection from bounded presentation and failed analysis."""

from __future__ import annotations

import json

import pytest

from roam.commands import cmd_debt
from tests.conftest import invoke_cli


@pytest.fixture
def debt_corpus(project_factory):
    # Real fixture-local indexing/SQLite/CLI serialization are intentional:
    # slicing must be tested after actual computation, without network or clocks.
    return project_factory({f"pkg/file{i}.py": f"def function{i}(x):\n    return x + {i}\n" for i in range(4)})


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("limit", [0, 1, 100])
@pytest.mark.parametrize("failed", [False, True])
def test_complete_output_and_partial_analysis(cli_runner, debt_corpus, monkeypatch, grouped, limit, failed):
    if failed:

        def fail(*args, **kwargs):
            raise RuntimeError("controlled suggestion failure")

        monkeypatch.setattr(cmd_debt, "_improvement_suggestions", fail)
    flags = ["--json", "--budget", "0", "debt", "--limit", str(limit)]
    if grouped:
        flags.append("--by-kind")
    result = invoke_cli(cli_runner, flags, cwd=debt_corpus)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    rows = [row for group in report["groups"] for row in group["files"]] if grouped else report["items"]
    summary = report["summary"]
    assert summary["total_count"] >= 4  # denominator is a measured nonempty population
    assert summary["count"] == len(rows)
    assert summary["truncated"] is (limit == 1)
    assert summary["partial_success"] is (failed or limit == 1)
    if limit != 1:
        assert len(rows) == summary["total_count"]
    if failed:
        assert any("debt_improvement_suggestions_failed" in warning for warning in summary["warnings_out"])


@pytest.mark.parametrize("alias", ["--limit", "--top", "-n"])
def test_negative_limit_refused(cli_runner, debt_corpus, alias):
    result = invoke_cli(cli_runner, ["--json", "debt", alias, "-1"], cwd=debt_corpus)
    assert result.exit_code == 2, result.output


@pytest.mark.parametrize("grouped", [False, True])
def test_unlimited_matches_large_limit_and_preserves_roi(cli_runner, debt_corpus, grouped):
    reports = []
    for limit in (0, 100):
        flags = ["--json", "--budget", "0", "debt", "--roi", "--limit", str(limit)]
        result = invoke_cli(cli_runner, [*flags, *(["--by-kind"] if grouped else [])], cwd=debt_corpus)
        assert result.exit_code == 0, result.output
        reports.append(json.loads(result.stdout))
    key = "groups" if grouped else "items"
    assert reports[0][key] == reports[1][key]
    assert reports[0]["roi"] == reports[1]["roi"]
    for field in ("total_debt", "total_remediation_minutes", "total_files", "total_count"):
        assert reports[0]["summary"][field] == reports[1]["summary"][field]


def test_unlimited_does_not_override_output_budget(cli_runner, debt_corpus):
    result = invoke_cli(cli_runner, ["--json", "--budget", "1", "debt", "--limit", "0"], cwd=debt_corpus)
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["partial_success"] is True
    assert summary["truncation_reason"] == "budget"


@pytest.mark.parametrize("phase", ["_compute_file_debt", "_group_by_directory"])
def test_unlimited_preserves_failed_computation(cli_runner, debt_corpus, monkeypatch, phase):
    def fail(*args, **kwargs):
        raise RuntimeError("controlled computation failure")

    monkeypatch.setattr(cmd_debt, phase, fail)
    result = invoke_cli(cli_runner, ["--json", "--budget", "0", "debt", "--limit", "0", "--by-kind"], cwd=debt_corpus)
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["partial_success"] is True
    assert "controlled computation failure" in " ".join(summary["warnings_out"])


def test_grouped_text_names_hidden_files(cli_runner, project_factory):
    project = project_factory({f"pkg/f{i}.py": f"def f{i}():\n    return {i}\n" for i in range(8)})
    result = invoke_cli(cli_runner, ["debt", "--by-kind"], cwd=project)
    assert result.exit_code == 0, result.output
    assert "+3 more files" in result.stdout
    complete = invoke_cli(cli_runner, ["debt", "--by-kind", "--limit", "0"], cwd=project)
    assert complete.exit_code == 0, complete.output
    assert "more files" not in complete.stdout
    for i in range(8):
        assert f"f{i}.py" in complete.stdout
