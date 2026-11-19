"""Complete empty answers retain state; review compounds still lack a change to review."""

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.mark.parametrize("command", ["pr-risk", "breaking", "pr-diff"])
def test_clean_diff_is_complete(project_factory, monkeypatch, command):
    """Contract change: missing default comparison is incomplete.

    Old: breaking on one commit reported no_changes / partial_success=False.
    New: git_error / partial_success=True, because HEAD~1 does not exist.
    Other commands still completely assess the valid empty working-tree diff.
    """
    root = project_factory({"core.py": "def calculate():\n    return 1\n"})
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["--json", command])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["state"] == ("git_error" if command == "breaking" else "no_changes")
    assert summary["partial_success"] is (command == "breaking")


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


@pytest.mark.parametrize("command", ["pr-risk", "breaking", "pr-diff"])
def test_real_clean_diff_is_complete(project_factory, monkeypatch, command):
    import subprocess

    root = project_factory({"core.py": "def calculate():\n    return 1\n"})
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "no public API changes"], cwd=root, check=True, capture_output=True
    )
    monkeypatch.chdir(root)
    args = ["--range", "HEAD~1"] if command == "pr-diff" else (["HEAD~1"] if command == "pr-risk" else [])
    result = CliRunner().invoke(cli, ["--json", command, *args])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["state"] == "no_changes"
    assert summary["partial_success"] is False


@pytest.mark.parametrize("command,args", [("breaking", ["missing-ref"]), ("pr-risk", ["missing-ref"])])
def test_git_comparison_failure(project_factory, monkeypatch, command, args):
    """Named contract change: Git kind and diagnostics are separate.

    Old: summary.git_error contained Git stderr, including missing-ref/fatal.
    New: git_error is the shared closed-enum kind; git_error_detail carries stderr.
    Reason: consumers must branch on the same failure vocabulary across commands.
    All prior diagnostic assertions are retained on the detail field.
    """
    root = project_factory({"core.py": "def calculate():\n    return 1\n"})
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["--json", command, *args])
    summary = json.loads(result.stdout)["summary"]
    assert summary["partial_success"] is True
    assert summary["state"] == "git_error"
    assert "could not" in summary["verdict"].lower()
    assert summary["git_error"] == "git_error"
    assert "missing-ref" in summary["git_error_detail"]
    assert "fatal:" in summary["git_error_detail"]


@pytest.mark.parametrize("fmt", ["text", "markdown", "json"])
def test_attest_failed_read(project_factory, monkeypatch, fmt):
    from roam.commands import cmd_attest

    root = project_factory({"core.py": "def calculate():\n    return 1\n"})
    monkeypatch.chdir(root)
    monkeypatch.setattr(cmd_attest, "get_changed_files_status", lambda *a, **kw: ([], "git_error"))
    result = CliRunner().invoke(cli, ["attest", "--format", fmt])
    # Exit 0 follows the repository convention for a degraded git read (W805-OOOO, W607-AD):
    # the failure is disclosed in every format, not through the exit code.
    assert result.exit_code == 0, result.output
    assert "could not read" in result.stdout.lower()
    assert "no changes found" not in result.stdout.lower()
    if fmt == "json":
        summary = json.loads(result.stdout)["summary"]
        assert summary["state"] == "git_error"
        assert summary["partial_success"] is True


def test_preflight_missing_signals(project_factory, monkeypatch):
    root = project_factory({"core.py": "def calculate():\n    return 1\n"})
    monkeypatch.chdir(root)
    (root / ".roam" / "fitness.yaml").write_text(
        "rules:\n  - name: valid names\n    type: naming\n    kind: function\n    pattern: calculate\n"
    )
    result = CliRunner().invoke(cli, ["--json", "preflight", "calculate"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["blast_radius"]["state"] == "no_callers"
    assert data["tests"]["state"] == "no_tests_indexed"
    assert data["coupling"]["state"] == "no_coupling_data"
    assert data["summary"]["partial_success"] is True


@pytest.mark.parametrize("query,fuzzy", [("xa.py", False), ("a.py", True)])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_file_deps_resolution(project_factory, monkeypatch, query, fuzzy, count):
    files = {
        "xa.py": ("from dep import helper\n" if count else "") + ("from other import extra\n" if count == 2 else ""),
        "dep.py": "def helper():\n    return 1\n",
    }
    files["xa.py"] += (
        "def run():\n    return " + ("helper()" if count else "1") + (" + extra()" if count == 2 else "") + "\n"
    )
    if count == 2:
        files["other.py"] = "def extra():\n    return 2\n"
    root = project_factory(files)
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["--json", "file", "--deps-of", query])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    summary = data["summary"]
    if fuzzy:
        assert summary["resolution"] == "fuzzy_substring"
        assert summary["partial_success"] is True
        assert "xa.py" in summary["verdict"]
    else:
        assert summary.get("resolution") != "fuzzy_substring"
        assert summary.get("partial_success", False) is False
    paths = [f["path"] for f in data["files"]] if count else [data["path"]]
    expected = ["dep.py", "other.py", "xa.py"] if count == 2 else (["dep.py", "xa.py"] if count else ["xa.py"])
    assert sorted(paths) == expected


@pytest.mark.parametrize("fmt", ["text", "markdown", "json"])
def test_attest_clean_read_control(project_factory, monkeypatch, fmt):
    root = project_factory({"core.py": "def calculate():\n    return 1\n"})
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["attest", "--format", fmt])
    assert result.exit_code == 0, result.output
    assert "no changes found" in result.stdout.lower()
    assert "could not read" not in result.stdout.lower()
