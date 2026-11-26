"""Git failure disclosure regressions, with CLI controls on valid refs and exact paths."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli

pytestmark = pytest.mark.usefixtures("pinned_index_freshness_budget")


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exception is None or isinstance(result.exception, SystemExit), result.output
    return result


@pytest.fixture
def project(project_factory, monkeypatch):
    root = project_factory({"core.py": "def calculate():\n    return 1\n"})
    monkeypatch.chdir(root)
    return root


def test_breaking_failure_retains_unknown_counts(project):
    result = invoke("--json", "breaking", "missing-ref")
    summary = json.loads(result.stdout)["summary"]
    for key in ("removed", "signature_changed", "renamed", "total"):
        assert key in summary
        assert summary[key] is None
    assert "breaking" in invoke("breaking", "missing-ref").stdout.lower()


@pytest.mark.parametrize(
    "command,args",
    [
        ("breaking", ["missing-ref"]),
        ("pr-risk", ["missing-ref"]),
        ("pr-diff", ["--range", "missing-ref"]),
        ("attest", ["missing-ref"]),
    ],
)
def test_git_vocabulary(project, command, args):
    summary = json.loads(invoke("--json", command, *args).stdout)["summary"]
    assert summary["git_error"] == "git_error"
    assert "missing-ref" in summary["git_error_detail"]
    assert "fatal:" in summary["git_error_detail"]


@pytest.mark.parametrize("kind", ["git_timeout", "git_not_available", "git_error"])
def test_pr_risk_real_failure_kind(project, monkeypatch, kind):
    from roam.commands import changed_files

    original = changed_files.subprocess.run

    def fail_diff(cmd, *a, **kw):
        if cmd[:3] == ["git", "diff", "--name-only"]:
            if kind == "git_timeout":
                raise subprocess.TimeoutExpired(cmd, 10)
            if kind == "git_not_available":
                raise FileNotFoundError("git missing")
            return subprocess.CompletedProcess(cmd, 128, "", "diff failed")
        return original(cmd, *a, **kw)

    monkeypatch.setattr(changed_files.subprocess, "run", fail_diff)
    summary = json.loads(invoke("--json", "pr-risk", "HEAD").stdout)["summary"]
    assert summary["state"] == summary["git_error"] == kind
    for key in ("risk_score", "risk_level", "risk_level_canonical", "risk_rank"):
        assert key in summary
        assert summary[key] is None
    assert summary["partial_success"] is True


@pytest.mark.parametrize("target", ["HEAD~1", "HEAD~1..HEAD", "HEAD~1...HEAD"])
def test_breaking_range_retains_findings(project_factory, monkeypatch, target):
    root = project_factory(
        {"core.py": "def calculate():\n    return 1\n"},
        extra_commits=[
            ({"core.py": "def other():\n    return 1\n"}, "rename public API"),
        ],
    )
    monkeypatch.chdir(root)
    result = invoke("--json", "breaking", target)
    summary = json.loads(result.stdout)["summary"]
    assert summary["total"] == 1
    assert summary["renamed"] == 1
    assert summary.get("partial_success", False) is False


@pytest.mark.parametrize("query", ["a.py", "xa.py"])
def test_single_file_discloses_mapping(project_factory, monkeypatch, query):
    root = project_factory({"xa.py": "def run():\n    return 1\n"})
    monkeypatch.chdir(root)
    data = json.loads(invoke("--json", "file", "--deps-of", query).stdout)
    assert data["path"] == "xa.py"
    summary = data["summary"]
    if query == "a.py":
        # The parent already emitted resolution and the filename. This pins the
        # NEW disclosure: the requested spelling and its resolved target together.
        assert "fuzzy --deps-of a.py resolved to xa.py" in summary["verdict"]
    else:
        assert "fuzzy" not in summary["verdict"]
        assert summary.get("partial_success", False) is False


@pytest.mark.parametrize("mode", ["text", "json"])
@pytest.mark.parametrize("explicit", [False, True])
def test_file_one_verdict_and_resolved_dedupe(project_factory, monkeypatch, mode, explicit):
    root = project_factory(
        {
            "xa.py": "from dep import helper\ndef run():\n    return helper()\n",
            "dep.py": "def helper():\n    return 1\n",
        }
    )
    monkeypatch.chdir(root)
    args = (["--json"] if mode == "json" else []) + ["file"]
    if explicit:
        args += ["xa.py"]
    result = invoke(*args, "--deps-of", "a.py")
    assert result.exit_code == 0, result.output
    if mode == "json":
        data = json.loads(result.stdout)
        assert sorted(f["path"] for f in data["files"]) == ["dep.py", "xa.py"]
        assert data["summary"]["files"] == 2
    else:
        verdicts = [s for s in result.stdout.splitlines() if s.startswith("VERDICT:")]
        assert len(verdicts) == 1
        assert "2 files" in verdicts[0]
        assert "fuzzy --deps-of a.py resolved to xa.py" in verdicts[0]


@pytest.mark.parametrize("fmt", ["json", "markdown", "text"])
@pytest.mark.parametrize("output", [False, True])
def test_attest_failure_risk_and_output(project, tmp_path, fmt, output):
    # Named contract change: old canonical risk "low" and integer rank 1 on a
    # failed changed-file lookup. New: both fields are null, including their
    # top-level mirrors. Reason: the lookup failed before risk could be assessed.
    path = tmp_path / "attestation.txt"
    args = ["attest", "missing-ref", "--format", fmt] + (["--output", str(path)] if output else [])
    result = invoke(*args)
    assert result.exit_code == 0, result.output
    content = path.read_text() if output else result.stdout
    assert "could not read" in content
    if fmt == "json":
        data = json.loads(content)
        for location in (data, data["summary"]):
            assert location["risk_level_canonical"] is None
            assert location["risk_rank"] is None
        assert data["summary"]["safe_to_merge"] is None


@pytest.mark.parametrize("command", ["api-changes", "semantic-diff"])
@pytest.mark.parametrize("mode", ["text", "json"])
def test_sibling_failure_disclosure(project, command, mode):
    args = (["--json"] if mode == "json" else []) + [command, "--base", "missing-ref"]
    if command == "api-changes":
        args += ["--severity", "breaking"]
    result = invoke(*args)
    if mode == "json":
        summary = json.loads(result.stdout)["summary"]
        assert summary["partial_success"] is True
        assert summary["git_error"] == "git_error"
        assert "missing-ref" in summary["git_error_detail"]
    else:
        assert "could not" in result.stdout.lower()
    if command == "api-changes":
        # Named contract change: old false clean (breaking_count=0, no partial_success).
        # New: unknown counts with partial_success and the Git failure kind.
        # Reason: a failed comparison cannot establish clean results. Both
        # versions exit 0. --severity is an output filter.
        assert result.exit_code == 0


@pytest.mark.parametrize("fmt", ["json", "markdown", "text"])
def test_attest_breaking_failure_reaches_envelope(project, monkeypatch, fmt):
    from roam.commands import changed_files

    original = changed_files.subprocess.run
    (project / "core.py").write_text("def other():\n    return 2\n")

    def fail_breaking(cmd, *a, **kw):
        # Named command-line contract change: HEAD is now followed by --.
        # The separator prevents Git from treating an unknown ref as a path.
        if cmd[:3] == ["git", "diff", "--name-only"] and "HEAD" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "breaking comparison failed")
        return original(cmd, *a, **kw)

    monkeypatch.setattr(changed_files.subprocess, "run", fail_breaking)
    result = invoke("attest", "--format", fmt, "--sign")
    assert result.exit_code == 0, result.output
    if fmt != "json":
        assert "breaking comparison could not run" in result.stdout.lower()
        assert "breaking comparison failed" in result.stdout
        return
    data = json.loads(result.stdout)
    from roam.commands.cmd_attest import _content_hash

    assert data["attestation"]["content_hash"] == _content_hash(data["evidence"])
    summary = data["summary"]
    assert summary["partial_success"] is True
    assert summary["git_error"] == "git_error"
    assert summary["breaking_changes"] is None
    assert summary["safe_to_merge"] is None
    assert "breaking comparison failed" in summary["git_error_detail"]
    assert data["evidence"]["breaking_changes"]["git_error"] == "git_error"


@pytest.mark.parametrize(
    "command,args",
    [
        ("breaking", ["HEAD"]),
        ("pr-risk", ["HEAD"]),
        ("pr-diff", ["--range", "HEAD"]),
        ("api-changes", ["--base", "HEAD", "--severity", "breaking"]),
        ("semantic-diff", ["--base", "HEAD"]),
        ("attest", []),
    ],
)
def test_valid_empty_cli_control(project, command, args):
    result = invoke("--json", command, *args)
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["summary"]
    assert not summary.get("git_error")
    assert "could not" not in summary["verdict"]
    if command != "attest":
        assert summary.get("partial_success", False) is False


def test_three_dot_reads_merge_base(project_factory, monkeypatch):
    root = project_factory(
        {"core.py": "def calculate():\n    return 1\n"},
        extra_commits=[
            ({"core.py": "def other():\n    return 1\n"}, "rename on feature"),
        ],
    )

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)

    git("branch", "feature")
    git("checkout", "-b", "peer", "HEAD~1")
    (root / "core.py").write_text("def calculate():\n    return 1\n\ndef peer_only():\n    return 3\n")
    git("add", "core.py")
    git("commit", "-m", "peer has another API")
    git("checkout", "feature")
    monkeypatch.chdir(root)
    summary = json.loads(invoke("--json", "breaking", "peer...HEAD").stdout)["summary"]
    assert summary["total"] == 1
    assert summary["renamed"] == 1
    assert summary["removed"] == 0


@pytest.mark.parametrize("kind", ["git_timeout", "git_not_available", "git_error"])
def test_failed_old_content_is_not_a_new_file(project_factory, monkeypatch, kind):
    root = project_factory(
        {"core.py": "def calculate():\n    return 1\n"},
        extra_commits=[
            ({"core.py": "def other():\n    return 1\n"}, "rename"),
        ],
    )
    monkeypatch.chdir(root)
    original = subprocess.run

    def fail_show(cmd, *a, **kw):
        # Named command-line contract change: Git blob reads moved from
        # show to cat-file blob so nonblob objects cannot become source.
        if cmd[0] == "git" and cmd[1] in ("show", "cat-file"):
            if kind == "git_timeout":
                raise subprocess.TimeoutExpired(cmd, 10)
            if kind == "git_not_available":
                raise FileNotFoundError("git unavailable")
            return subprocess.CompletedProcess(cmd, 128, b"", b"fatal: corrupt object")
        return original(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", fail_show)
    summary = json.loads(invoke("--json", "breaking", "HEAD~1").stdout)["summary"]
    assert summary["partial_success"] is True
    assert summary["git_error"] == kind
    assert summary["total"] is None


def test_added_file_control(project_factory, monkeypatch):
    root = project_factory(
        {"core.py": "def calculate():\n    return 1\n"},
        extra_commits=[
            ({"new.py": "def fresh():\n    return 1\n"}, "new public API"),
        ],
    )
    monkeypatch.chdir(root)
    summary = json.loads(invoke("--json", "breaking", "HEAD~1..HEAD").stdout)["summary"]
    assert summary["total"] == 0
    assert summary.get("partial_success", False) is False
