"""Real-repository regressions for Git comparison evidence."""

import json
import os
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli

pytestmark = pytest.mark.usefixtures("pinned_index_freshness_budget")


SOURCE = "def calculate():\n    return 1\n"
RENAMED = SOURCE.replace("calculate", "other")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True, text=True).stdout.strip()


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, (result.output, result.exception)
    return json.loads(result.stdout)


@pytest.fixture(autouse=True)
def isolate_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")


@pytest.fixture
def project(project_factory, monkeypatch):
    root = project_factory({"core.py": SOURCE})
    monkeypatch.chdir(root)
    return root


def test_attest_computed_risk_is_visible_when_breaking_fails(project, monkeypatch):
    from roam.commands import cmd_attest

    (project / "core.py").write_text(RENAMED, encoding="utf-8")
    risk = {"score": 67, "level": "HIGH"}
    monkeypatch.setattr(cmd_attest, "_collect_risk", lambda *a: risk)
    original = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[0] == "git" and cmd[1] in ("show", "cat-file") and "HEAD:core.py" in cmd:
            raise subprocess.TimeoutExpired(cmd, 10)
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    data = invoke("--json", "attest", "--sign")
    summary = data["summary"]
    assert summary["safe_to_merge"] is False
    assert data["verdict"]["safe_to_merge"] is False
    assert summary["partial_success"] is True
    assert summary["verdict"].startswith("NOT safe to merge")
    assert summary["git_error"] == "git_timeout"
    assert (summary["risk_score"], summary["risk_level"]) == (67, "HIGH")
    assert (summary["risk_level_canonical"], summary["risk_rank"]) == ("high", 3)
    assert data["evidence"]["risk"] == risk
    assert data["attestation"]["content_hash"] == cmd_attest._content_hash(data["evidence"])


@pytest.mark.parametrize("command", ["semantic-diff", "pr-diff"])
def test_unreadable_existing_blob_is_disclosed(project, monkeypatch, command):
    (project / "core.py").write_text(RENAMED, encoding="utf-8")
    original = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[0] == "git" and cmd[1] in ("show", "cat-file") and any(arg.endswith(":core.py") for arg in cmd[2:]):
            raise subprocess.TimeoutExpired(cmd, 10)
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    args = ["--base", "HEAD"] if command == "semantic-diff" else ["--range", "HEAD"]
    data = invoke("--json", command, *args)
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["git_error"] == "git_timeout"
    assert not (data["symbols_added"] if command == "semantic-diff" else data["symbol_changes"]["added"])


@pytest.mark.parametrize("dots", ["..", "..."])
def test_attest_breaking_uses_both_committed_endpoints(project_factory, monkeypatch, dots):
    root = project_factory(
        {"core.py": SOURCE},
        extra_commits=[({"core.py": RENAMED}, "feature renames API")],
    )
    right = git(root, "rev-parse", "HEAD")
    fork = git(root, "rev-parse", "HEAD~1")
    git(root, "checkout", "-b", "peer", fork)
    (root / "core.py").write_text(SOURCE + "\nclass PeerOnly:\n    pass\n", encoding="utf-8")
    git(root, "add", "core.py")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "peer adds API")
    (root / "core.py").write_text(SOURCE.replace("calculate", "worktree_only"), encoding="utf-8")
    monkeypatch.chdir(root)
    indexed = CliRunner().invoke(cli, ["index", "--force"])
    assert indexed.exit_code == 0, indexed.output

    data = invoke("--json", "attest", f"peer{dots}{right}")
    changes = data["evidence"]["breaking_changes"]
    assert [(entry["old_name"], entry["new_name"]) for entry in changes["renamed"]] == [("calculate", "other")]
    assert [entry["name"] for entry in changes["removed"]] == (["PeerOnly"] if dots == ".." else [])


@pytest.mark.parametrize("command", ["pr-risk", "attest"])
def test_python_failure_has_no_git_kind(project, monkeypatch, command):
    from roam.commands import cmd_attest, cmd_pr_risk

    def fail(*args, **kwargs):
        raise OSError("Roam helper failed")

    monkeypatch.setattr(cmd_attest if command == "attest" else cmd_pr_risk, "get_changed_files_status", fail)
    summary = invoke("--json", command, "HEAD")["summary"]
    assert summary["partial_success"] is True
    assert summary.get("git_error") is None
    assert summary["state"] == "changed_files_unavailable"
    assert any("OSError:Roam helper failed" in warning for warning in summary["warnings_out"])


@pytest.mark.parametrize("failure", [PermissionError("git executable denied"), OSError("git spawn failed")])
def test_git_spawn_failure_has_kind_and_detail(project, monkeypatch, failure):
    original = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[:3] == ["git", "diff", "--name-only"]:
            raise failure
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    summary = invoke("--json", "api-changes", "--base", "HEAD")["summary"]
    assert summary["partial_success"] is True
    assert summary["git_error"] == "git_error"
    assert str(failure) in summary["git_error_detail"]


def test_name_only_keeps_verbatim_paths(project_factory, monkeypatch):
    paths = ["café.py", "with space.py"]
    if os.name != "nt":
        paths += ['"quoted".py', "line\nbreak.py", "back\\slash.py"]
    root = project_factory({path: SOURCE for path in paths}, index=False)
    monkeypatch.chdir(root)
    git(root, "config", "core.quotePath", "true")
    for path in paths:
        (root / path).write_text(RENAMED, encoding="utf-8")
    from roam.commands.changed_files import get_changed_files_status

    assert get_changed_files_status(root, commit_range="HEAD") == (sorted(paths), None)


@pytest.mark.parametrize("command", ["api-changes", "semantic-diff"])
def test_empty_base_is_usage_error(project, command):
    result = CliRunner().invoke(cli, [command, "--base", ""])
    assert result.exit_code == 2
    assert "--base" in result.output and "empty" in result.output


@pytest.mark.parametrize("command", ["breaking", "api-changes", "attest", "semantic-diff", "pr-diff"])
@pytest.mark.parametrize("shared_objects", [False, True])
def test_gitlink_pointer_change_is_disclosed_skip(project, project_factory, command, shared_objects):
    if shared_objects:
        # A submodule commit can also be present in the superproject.
        first = git(project, "rev-parse", "HEAD")
        git(
            project,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "commit",
            "--allow-empty",
            "-m",
            "bump",
        )
        pointers = (first, git(project, "rev-parse", "HEAD"))
    else:
        module = project_factory({"lib.py": SOURCE}, extra_commits=[({"lib.py": RENAMED}, "bump")], index=False)
        pointers = (git(module, "rev-parse", "HEAD~1"), git(module, "rev-parse", "HEAD"))
    for index, pointer in enumerate(pointers):
        git(project, "update-index", "--add", "--cacheinfo", f"160000,{pointer},vendor")
        git(project, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", f"link {index}")
    args = {
        "breaking": ["HEAD~1..HEAD"],
        "api-changes": ["--base", "HEAD~1"],
        "attest": ["HEAD~1..HEAD"],
        "semantic-diff": ["--base", "HEAD~1"],
        "pr-diff": ["--range", "HEAD~1"],
    }
    data = invoke("--json", command, *args[command])
    assert not data["summary"].get("git_error")
    assert {"file": "vendor", "reason": "gitlink"} in data["files_not_analyzed"]


def test_verify_uses_literal_pathspec(project_factory, monkeypatch):
    path = "src/[id].ts"
    root = project_factory({path: "export function fresh() {}\n", "src/i.ts": "export function neighbor() {}\n"})
    monkeypatch.chdir(root)
    for name in (path, "src/i.ts"):
        with (root / name).open("a", encoding="utf-8") as source:
            source.write("// updated\n")
    from roam.commands.cmd_verify import _git_diff_zero_context

    diff = _git_diff_zero_context([path], root)
    assert f"+++ b/{path}" in diff
    assert "+++ b/src/i.ts" not in diff


def test_literal_new_file_beside_glob_match(project_factory, monkeypatch):
    root = project_factory(
        {"src/i.ts": "export function neighbor() {}\n"},
        extra_commits=[({"src/[id].ts": "export function fresh() {}\n"}, "add literal path")],
    )
    monkeypatch.chdir(root)
    from roam.commands.changed_files import git_show_at_ref

    assert git_show_at_ref(root, "HEAD~1", "src/[id].ts", strict=True) is None
    # Compare with the committed bytes: the fixture writer follows the platform line ending.
    assert git_show_at_ref(root, "HEAD~1", "src/i.ts", strict=True) == (root / "src/i.ts").read_bytes()
    data = invoke("--json", "semantic-diff", "--base", "HEAD~1")
    assert [(entry["file"], entry["name"]) for entry in data["symbols_added"]] == [("src/[id].ts", "fresh")]
    assert data["summary"]["partial_success"] is False


def test_expected_new_file_does_not_log_a_failure(project_factory, monkeypatch, capsys):
    from roam import observability
    from roam.commands.changed_files import git_show_at_ref

    project = project_factory({"core.py": SOURCE}, index=False)
    observability.reset()
    monkeypatch.setenv("ROAM_OBSERVABILITY", "1")
    assert git_show_at_ref(project, "HEAD", "added.py", strict=True) is None
    assert "git_comparison" not in capsys.readouterr().err
    assert git_show_at_ref(project, "HEAD", "core.py", strict=True) == (project / "core.py").read_bytes()
    assert git_show_at_ref(project, "missing-ref", "core.py") is None
    assert "git_comparison" in capsys.readouterr().err


def test_verify_breaking_discloses_unavailable_base_blob(project, monkeypatch):
    from roam.commands.cmd_verify import _check_breaking
    from roam.db.connection import open_db

    (project / "core.py").write_text("def calculate(value):\n    return value\n", encoding="utf-8")
    indexed = CliRunner().invoke(cli, ["index", "--force"])
    assert indexed.exit_code == 0, indexed.output
    original = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[0] == "git" and cmd[1] in ("show", "cat-file") and "HEAD:core.py" in cmd:
            raise subprocess.TimeoutExpired(cmd, 10)
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    with open_db(readonly=True) as conn:
        outcome = _check_breaking(conn, [], ["core.py"], project)
        assert outcome["available"] is False
        assert outcome["partial_success"] is True
        assert "core.py" in outcome["unavailable_reason"]
        control = _check_breaking(conn, [], ["new.py"], project)
    assert control == {"score": 100, "violations": []}


def test_attest_breaking_python_failure_cannot_approve_merge(project, monkeypatch):
    from roam.commands import cmd_attest

    (project / "core.py").write_text(RENAMED, encoding="utf-8")

    def fail(*args, **kwargs):
        raise RuntimeError("breaking detector failed")

    monkeypatch.setattr(cmd_attest, "_collect_breaking", fail)
    data = invoke("--json", "attest", "--sign")
    assert data["summary"]["safe_to_merge"] is None
    assert data["summary"]["partial_success"] is True
    assert data["summary"].get("git_error") is None
    assert data["summary"]["breaking_changes"] is None
    assert data["evidence"]["breaking_changes"]["state"] == "breaking_unavailable"


@pytest.mark.parametrize("output", ["", "malformed\n"])
def test_merge_base_requires_an_object_id(project, monkeypatch, output):
    from roam.commands.changed_files import GitComparisonError, git_comparison_base

    original = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(cmd, 0, output, "")
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(GitComparisonError, match="merge-base returned an invalid object ID") as error:
        git_comparison_base(project, "HEAD...HEAD")
    assert error.value.kind == "git_error"
