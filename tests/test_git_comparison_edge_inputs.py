"""Regression and valid-case controls for the Git comparison CLI."""

import json
import os
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands import changed_files
from tests._helpers.repo_root import repo_root

SOURCE = "def calculate(value):\n    return value\n"
KEEP = "".join(f"def keep_{i}(value):\n    return value + {i}\n\n" for i in range(12))


@pytest.fixture(autouse=True)
def isolate_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.org", *args],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


def invoke(*args, expected_exit=0):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == expected_exit, (result.output, result.exception)
    return json.loads(result.stdout)


def index():
    result = CliRunner().invoke(cli, ["index", "--force"])
    assert result.exit_code == 0, (result.output, result.exception)


@pytest.fixture
def project(project_factory, monkeypatch):
    root = project_factory({"core.py": SOURCE + KEEP})
    monkeypatch.chdir(root)
    return root


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("breaking", ["HEAD~1..HEAD"]),
        ("api-changes", ["--base", "HEAD~1"]),
        ("attest", ["HEAD~1..HEAD"]),
        ("pr-risk", ["HEAD~1..HEAD"]),
    ],
)
@pytest.mark.parametrize("rename", [True, False], ids=["regression", "control"])
def test_removed_file_path(project, monkeypatch, command, args, rename):
    from roam.commands import cmd_pr_risk

    resolved_paths = []
    original = cmd_pr_risk.resolve_changed_to_db

    def resolve(conn, paths):
        resolved_paths.extend(paths)
        return original(conn, paths)

    monkeypatch.setattr(cmd_pr_risk, "resolve_changed_to_db", resolve)
    git(project, "config", "diff.renames", "true")
    path = "core.py"
    if rename:
        git(project, "mv", "core.py", "api.py")
        path = "api.py"
    (project / path).write_bytes(b"def keep_0" + (project / path).read_bytes().split(b"def keep_0", 1)[1])
    git(project, "add", "--", path)
    git(project, "commit", "-m", "remove calculate")
    index()

    data = invoke("--json", command, *args)
    if command == "pr-risk":
        assert data["summary"]["changed_files"] == 1
        assert resolved_paths == (["api.py", "core.py"] if rename else ["core.py"])
        return
    if command == "breaking":
        entries = data["removed"]
    elif command == "api-changes":
        assert any(entry["symbol_name"] == "calculate" and entry["file"] == "core.py" for entry in data["changes"])
        return
    else:
        entries = data["evidence"]["breaking_changes"]["removed"]
    assert any(entry["name"] == "calculate" and entry["file"] == "core.py" for entry in entries)


@pytest.mark.parametrize(
    "args",
    [["attest"], ["pr-risk"], ["breaking"], ["pr-diff", "--range"]],
)
def test_empty_ref_is_usage_error(project, args):
    result = CliRunner().invoke(cli, [*args, ""])
    assert result.exit_code == 2
    assert "empty" in result.output and "Invalid value" in result.output


@pytest.mark.parametrize("args", [["attest"], ["pr-risk"], ["breaking"], ["pr-diff", "--range"]])
def test_control_valid_ref(project, args):
    summary = invoke("--json", *args, "HEAD")["summary"]
    assert summary["state"] == "no_changes"
    assert not summary.get("git_error")


@pytest.mark.parametrize("unborn", [True, False], ids=["regression", "control"])
def test_verify_breaking_baseline(tmp_path, monkeypatch, unborn):
    git(tmp_path, "init")
    (tmp_path / ".gitignore").write_bytes(b".roam/\n")
    (tmp_path / "core.py").write_bytes(SOURCE.encode())
    if not unborn:
        git(tmp_path, "add", ".")
        git(tmp_path, "commit", "-m", "initial")
    monkeypatch.chdir(tmp_path)
    index()
    result = CliRunner().invoke(cli, ["--json", "verify", "--checks", "breaking", "core.py"])
    data = json.loads(result.stdout)
    category = data["categories"]["breaking"]
    assert result.exit_code == 0, category
    assert data["summary"]["verification_complete"] is True
    if unborn:
        assert category["baseline_state"] == "no_commits"
        text = CliRunner().invoke(cli, ["verify", "--checks", "breaking", "core.py"])
        assert "no commits" in text.stdout
        compact = CliRunner().invoke(cli, ["verify", "--summary", "--checks", "breaking", "core.py"])
        assert "no commits" in compact.stdout
        assert data["summary"]["breaking_baseline_state"] == "no_commits"
    else:
        assert category["score"] == 100
        assert "baseline_state" not in category


def test_tree_at_base_is_absent(project_factory, monkeypatch):
    root = project_factory({"pkg/util.py/a.py": SOURCE}, index=False)
    git(root, "rm", "pkg/util.py/a.py")
    (root / "pkg").mkdir(exist_ok=True)
    (root / "pkg/util.py").write_bytes(b"def replacement():\n    return 1\n")
    git(root, "add", "pkg/util.py")
    git(root, "commit", "-m", "replace directory with file")
    monkeypatch.chdir(root)
    index()
    assert changed_files.git_show_at_ref(root, "HEAD~1", "pkg/util.py", strict=True) is None
    data = invoke("--json", "api-changes", "--base", "HEAD~1")
    assert not data["summary"].get("git_error")
    assert any(entry["symbol_name"] == "calculate" and entry["file"] == "pkg/util.py/a.py" for entry in data["changes"])


def test_control_blob_at_base(project):
    assert changed_files.git_show_at_ref(project, "HEAD", "core.py", strict=True) == (project / "core.py").read_bytes()


@pytest.mark.parametrize("version", ["2.23.9", "2.24.0.windows.1"], ids=["regression", "control"])
def test_doctor_reports_git_floor(project, monkeypatch, version):
    from roam.commands import cmd_doctor

    which = cmd_doctor.shutil.which
    monkeypatch.setattr(cmd_doctor.shutil, "which", lambda name: "/mock/git" if name == "git" else which(name))
    original = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd == ["git", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, f"git version {version}\n", "")
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    result = CliRunner().invoke(cli, ["--json", "doctor"])
    checks = json.loads(result.stdout)["checks"]
    if version.startswith("2.23"):
        check = next((check for check in checks if "2.24" in check["detail"]), {})
        assert check.get("passed") is False
        assert check["name"] in cmd_doctor._ADVISORY_CHECK_NAMES
    else:
        assert any(check["passed"] and check["detail"] == f"git {version}" for check in checks)


@pytest.mark.parametrize("stderr", [b"error: invalid option: --end-of-options\n", b"usage: git diff [<options>]\n"])
def test_unsupported_git_option_diagnostic(project, monkeypatch, stderr):
    original = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return subprocess.CompletedProcess(cmd, 129, b"", stderr)
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    summary = invoke("--json", "breaking", "HEAD")["summary"]
    assert summary["git_error"] == "git_error"
    assert "Git 2.24" in summary["git_error_detail"]
    assert stderr.decode().strip() in summary["git_error_detail"]


def test_documented_floor():
    for path in ("README.md", "llms-install.md", "CONTRIBUTING.md"):
        assert "Git 2.24" in (repo_root() / path).read_text(encoding="utf-8")


@pytest.mark.parametrize("raw", [b"caf\xe9.py", "café.py".encode()], ids=["regression", "control"])
def test_decode_paths_losslessly(monkeypatch, raw):
    # Exercise a strict filesystem codec even on hosts where fsdecode accepts invalid UTF-8.
    monkeypatch.setattr(os, "fsdecode", lambda value: value.decode("utf-8", errors="surrogatepass"))
    decode = getattr(changed_files, "decode_git_path", os.fsdecode)
    assert decode(raw).encode("utf-8", errors="surrogateescape") == raw


def test_unrepresentable_path_is_disclosed(project, monkeypatch):
    original = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return subprocess.CompletedProcess(cmd, 0, b"caf\xe9.py\0", b"")
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    summary = invoke("--json", "breaking", "HEAD")["summary"]
    assert summary["partial_success"] is True
    assert summary["git_error"] == "git_error"
    assert r"caf\udce9.py" in summary["git_error_detail"]


@pytest.mark.parametrize("dots", ["...", ".."], ids=["regression", "control"])
def test_pr_diff_symbol_base(project, dots):
    fork = git(project, "rev-parse", "HEAD")
    git(project, "checkout", "-b", "peer")
    with (project / "core.py").open("ab") as source:
        source.write(b"\ndef peer_only():\n    return 2\n")
    git(project, "commit", "-am", "peer export")
    git(project, "checkout", "-b", "feature", fork)
    with (project / "core.py").open("ab") as source:
        source.write(b"\ndef feature_only():\n    return 3\n")
    git(project, "commit", "-am", "feature export")
    index()
    changes = invoke("--json", "pr-diff", "--range", f"peer{dots}feature")["symbol_changes"]
    assert [entry["name"] for entry in changes["added"]] == ["feature_only"]
    assert [entry["name"] for entry in changes["removed"]] == ([] if dots == "..." else ["peer_only"])


@pytest.mark.parametrize("timeout", [True, False], ids=["regression", "control"])
def test_attest_unresolved_breaking(project, monkeypatch, timeout):
    git(project, "rm", "core.py")
    git(project, "commit", "-m", "delete API file")
    index()
    original = subprocess.run

    def run(cmd, *args, **kwargs):
        if timeout and cmd[:2] == ["git", "cat-file"] and any(arg.endswith(":core.py") for arg in cmd):
            raise subprocess.TimeoutExpired(cmd, 10)
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    args = ["attest", "HEAD~1..HEAD"]
    summary = invoke("--json", *args)["summary"]
    assert summary["resolution"] == "unresolved"
    assert summary["safe_to_merge"] is False
    if timeout:
        assert summary["git_error"] == "git_timeout"
        assert "git_timeout" in summary["verdict"]
        assert "None" not in summary["verdict"]
        result = CliRunner().invoke(cli, args)
        assert "Breaking comparison could not run: git_timeout" in result.stdout
    else:
        assert not summary.get("git_error")
        assert "(risk_level low)" in summary["verdict"]
