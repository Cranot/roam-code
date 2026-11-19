"""Fail-first controls for range reads, blob-absence checks and shared git classification."""

import ast
import inspect
import json
import os
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli

SOURCE = "def calculate():\n    return 1\n"


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.fixture
def project(project_factory, monkeypatch):
    root = project_factory({"core.py": SOURCE})
    monkeypatch.chdir(root)
    return root


@pytest.mark.parametrize("target", ["HEAD~1..HEAD", "HEAD~1...HEAD", "HEAD~1"])
def test_breaking_range_compares_committed_right_side(project_factory, monkeypatch, target):
    root = project_factory(
        {"core.py": SOURCE},
        extra_commits=[({"core.py": SOURCE.replace("calculate", "other")}, "rename public function")],
    )
    monkeypatch.chdir(root)
    # The range cases still compare the committed rename against a worktree
    # without it. The single-ref case adds a symbol to get past the empty diff.
    current = SOURCE + "\ndef worktree_only():\n    return 3\n" if target == "HEAD~1" else SOURCE
    (root / "core.py").write_text(current, encoding="utf-8")
    index = CliRunner().invoke(cli, ["index", "--force"])
    assert index.exit_code == 0, index.output
    summary = invoke("--json", "breaking", target)["summary"]
    assert summary["renamed"] == (0 if target == "HEAD~1" else 1)
    assert summary["total"] == (0 if target == "HEAD~1" else 1)


@pytest.mark.parametrize("absent", [True, False])
def test_locale_neutral_blob_absence(project_factory, monkeypatch, absent):
    from roam.commands.changed_files import GitComparisonError, git_show_at_ref

    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    project = project_factory({"core.py": SOURCE}, index=False)
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LANGUAGE", "de")

    def git(*args, check=True):
        return subprocess.run(["git", *args], cwd=project, capture_output=True, check=check)

    path = "new.py" if absent else "core.py"
    if not absent:
        # The tree still names the file, but its loose blob is unreadable.
        oid = git("rev-parse", "HEAD:core.py").stdout.decode().strip()
        blob = project / ".git" / "objects" / oid[:2] / oid[2:]
        blob.rename(project / "held-blob")
    assert git("show", f"HEAD:{path}", "--", check=False).returncode != 0
    if absent:
        assert git_show_at_ref(project, "HEAD", "new.py", strict=True) is None
    else:
        with pytest.raises(GitComparisonError):
            git_show_at_ref(project, "HEAD", "core.py", strict=True)


@pytest.mark.parametrize("failed", [True, False])
@pytest.mark.parametrize("command,args", [("api-changes", ["--base", "HEAD"]), ("attest", [])])
def test_ref_comparison_required_blob(project, monkeypatch, command, args, failed):
    (project / "core.py").write_text(SOURCE.replace("return 1", "return 2"))
    original = subprocess.run

    def run(cmd, *a, **kw):
        # Named command-line contract change: blob reads used git show.
        # They now use git cat-file blob to reject nonblobs and path fallback.
        if failed and cmd[0] == "git" and cmd[1] in ("show", "cat-file") and "HEAD:core.py" in cmd:
            raise subprocess.TimeoutExpired(cmd, 10)
        return original(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    summary = invoke("--json", command, *args)["summary"]
    assert summary.get("git_error") == ("git_timeout" if failed else None)
    assert summary.get("partial_success", False) is failed


@pytest.mark.parametrize("kind", ["git_timeout", "git_not_available", "git_error", None])
def test_api_changes_degraded_exit_contract(project, monkeypatch, kind):
    from roam.commands import cmd_api_changes as api
    from roam.commands.changed_files import GitComparisonError

    if kind:

        def fail(*args):
            raise GitComparisonError(kind, "comparison unavailable")

        monkeypatch.setattr(api, "_git_changed_files", fail)
    summary = invoke("--json", "api-changes", "--base", "HEAD")["summary"]
    assert summary.get("git_error") == kind
    assert summary.get("partial_success", False) is bool(kind)


@pytest.mark.parametrize("failed", [True, False])
def test_pr_risk_null_detail_and_no_dead_warning_branch(project, monkeypatch, failed):
    from roam.commands import cmd_pr_risk as risk

    if failed:

        def fail(*args, **kwargs):
            raise RuntimeError("escaped")

        monkeypatch.setattr(risk, "get_changed_files_status", fail)
    summary = invoke("--json", "pr-risk", "HEAD")["summary"]
    if failed:
        assert summary["git_error_detail"] is None
        assert summary["warnings_out"]
    else:
        assert summary["state"] == "no_changes"
        assert "warnings_out" not in summary
    tree = ast.parse(inspect.getsource(risk))
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and ast.unparse(node.test) == "not changed":
            assert "_w607q_warnings_out" not in ast.unparse(node)


@pytest.mark.parametrize("failed", [True, False])
def test_attest_preserves_risk_after_breaking_failure(project, monkeypatch, failed):
    from roam.commands import cmd_attest as attest

    (project / "core.py").write_text(SOURCE.replace("return 1", "return 2"))
    risk = {"score": 17, "level": "LOW"}
    monkeypatch.setattr(attest, "_collect_risk", lambda *a, **kw: risk)
    if failed:
        monkeypatch.setattr(
            attest,
            "_collect_breaking",
            lambda *a: {
                "removed": [],
                "signature_changed": [],
                "renamed": [],
                "git_error": "git_timeout",
                "git_error_detail": "timed out",
                "partial_success": True,
            },
        )
    data = invoke("--json", "attest", "--sign")
    assert data["evidence"]["risk"] == risk
    assert data["attestation"]["content_hash"] == attest._content_hash(data["evidence"])
    if failed:
        assert data["summary"]["git_error"] == "git_timeout"
        assert data["summary"]["safe_to_merge"] is None
        # Named contract change: old rank null on a breaking-only failure.
        # New: rank 1 for the computed LOW risk. The separate merge verdict
        # stays unknown because risk alone cannot certify the breaking check.
        assert data["summary"]["risk_rank"] == 1


@pytest.mark.parametrize("kind", ["git_timeout", "git_not_available", "git_error", None])
def test_attest_stable_git_error_state(project, monkeypatch, kind):
    from roam.commands import cmd_attest as attest

    monkeypatch.setattr(attest, "get_changed_files_status", lambda *a, **kw: ([], kind))
    summary = invoke("--json", "attest")["summary"]
    assert summary["state"] == ("git_error" if kind else "no_changes")
    assert summary.get("git_error") == kind


@pytest.mark.parametrize("kind", ["git_timeout", "git_not_available", "git_error", None])
def test_shared_runner_classifies(project, monkeypatch, kind):
    from roam.commands import changed_files

    def run(cmd, **kwargs):
        if kind == "git_timeout":
            raise subprocess.TimeoutExpired(cmd, 10)
        if kind == "git_not_available":
            raise FileNotFoundError("missing")
        return subprocess.CompletedProcess(cmd, 128 if kind else 0, b"ok", b"failed" if kind else b"")

    monkeypatch.setattr(subprocess, "run", run)
    details = []
    result, error = changed_files.run_git_comparison(project, ["git", "show", "HEAD:core.py"], details)
    assert error == kind
    assert bool(details) is bool(kind)
    assert (result is None) is bool(kind)


@pytest.mark.parametrize("fmt", ["json", "markdown", "text"])
@pytest.mark.parametrize("target", ["HEAD", "missing-ref"])
def test_attest_output_controls(project, tmp_path, fmt, target):
    target_file = tmp_path / "attestation.txt"
    result = CliRunner().invoke(cli, ["attest", target, "--format", fmt, "--output", str(target_file)])
    assert result.exit_code == 0, result.output
    assert target_file.read_text()
    assert f"Attestation written to {target_file}" in result.stdout


def test_verify_breaking_import_and_execution(project, monkeypatch):
    from roam.commands import cmd_breaking as breaking
    from roam.commands.cmd_verify import _check_breaking
    from roam.db.connection import open_db

    (project / "core.py").write_text("def calculate(value):\n    return value\n")
    index = CliRunner().invoke(cli, ["index", "--force"])
    assert index.exit_code == 0, index.output
    called = []
    original = breaking._compare_file

    def compare(*args, **kwargs):
        called.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(breaking, "_compare_file", compare)
    with open_db(readonly=True) as conn:
        result = _check_breaking(conn, [], ["core.py"], project)
    assert called, result
    assert "import failed" not in str(result)


def test_legacy_pr_risk_import_preserved():
    from roam.commands.cmd_pr_risk import get_changed_files

    assert callable(get_changed_files)


def test_shared_runner_preserves_merge_base_fallback(project, monkeypatch):
    from roam.commands.changed_files import GitComparisonError, git_comparison_base

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 128, "", ""))
    with pytest.raises(GitComparisonError, match="^git merge-base failed$"):
        git_comparison_base(project, "HEAD~1...HEAD")
