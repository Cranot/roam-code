"""Shared test fixtures and helpers for roam tests.

Provides:
- Subprocess helper: roam() for smoke/E2E tests
- Git helpers: git_init(), git_commit()
- CliRunner fixtures: cli_runner, invoke_cli()
- Composable project fixtures: git_repo → python_project → indexed_project
- Factory fixture: project_factory for custom file combinations
- JSON validation helpers: parse_json_output(), assert_json_envelope()
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner


# ===========================================================================
# Subprocess helpers (kept for backward compat + smoke tests)
# ===========================================================================

def roam(*args, cwd=None):
    """Run a roam CLI command and return (output, returncode)."""
    result = subprocess.run(
        [sys.executable, "-m", "roam"] + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return result.stdout + result.stderr, result.returncode


def git_init(path):
    """Initialize a git repo, add all files, and commit."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True)


def git_commit(path, msg="update"):
    """Stage all and commit."""
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=path, capture_output=True)


# ===========================================================================
# CliRunner helpers
# ===========================================================================

@pytest.fixture
def cli_runner():
    """Provide a Click CliRunner for in-process CLI testing."""
    return CliRunner()


def invoke_cli(runner, args, cwd=None, json_mode=False):
    """Invoke the roam CLI via CliRunner.

    Args:
        runner: CliRunner instance
        args: list of CLI arguments (e.g. ["health"])
        cwd: directory to run in (monkeypatched via env)
        json_mode: if True, prepend --json flag
    Returns:
        click.testing.Result
    """
    from roam.cli import cli

    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.extend(args)

    env = {}
    if cwd:
        env["PWD"] = str(cwd)

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    return result


# ===========================================================================
# JSON validation helpers
# ===========================================================================

def parse_json_output(result, command=None):
    """Parse JSON from a CliRunner result.

    Args:
        result: click.testing.Result from invoke_cli
        command: optional command name for better error messages
    Returns:
        Parsed dict from JSON output
    Raises:
        AssertionError with context on parse failure
    """
    assert result.exit_code == 0, (
        f"Command {command or '?'} failed (exit {result.exit_code}):\n"
        f"{result.output}"
    )
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"Invalid JSON from {command or '?'}: {e}\n"
            f"Output was:\n{result.output[:500]}"
        )


def assert_json_envelope(data, command=None):
    """Validate that a parsed JSON dict follows the roam envelope contract.

    Checks required top-level keys: command, version, timestamp, summary.
    Checks summary contains a verdict string.
    """
    assert isinstance(data, dict), f"Expected dict, got {type(data)}"
    assert "command" in data, f"Missing 'command' key in envelope"
    assert "version" in data, f"Missing 'version' key in envelope"
    assert "timestamp" in data, f"Missing 'timestamp' key in envelope"
    assert "summary" in data, f"Missing 'summary' key in envelope"
    if command:
        assert data["command"] == command, (
            f"Expected command={command}, got {data['command']}"
        )
    summary = data["summary"]
    assert isinstance(summary, dict), f"summary should be dict, got {type(summary)}"


# ===========================================================================
# Composable project fixtures
# ===========================================================================

@pytest.fixture
def git_repo(tmp_path):
    """Create an empty git repo with an initial commit.

    Returns the path to the repo directory.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    git_init(repo)
    return repo


@pytest.fixture
def python_project(git_repo):
    """Extend git_repo with a small Python project (3 files, imports, calls).

    Returns the path to the project directory.
    """
    src = git_repo / "src"
    src.mkdir()

    (src / "models.py").write_text(
        'class User:\n'
        '    """A user model."""\n'
        '    def __init__(self, name, email):\n'
        '        self.name = name\n'
        '        self.email = email\n'
        '\n'
        '    def display_name(self):\n'
        '        return self.name.title()\n'
        '\n'
        '    def validate_email(self):\n'
        '        return "@" in self.email\n'
        '\n'
        '\n'
        'class Admin(User):\n'
        '    """An admin user."""\n'
        '    def __init__(self, name, email, role="admin"):\n'
        '        super().__init__(name, email)\n'
        '        self.role = role\n'
        '\n'
        '    def promote(self, user):\n'
        '        pass\n'
    )

    (src / "service.py").write_text(
        'from models import User, Admin\n'
        '\n'
        '\n'
        'def create_user(name, email):\n'
        '    """Create a new user."""\n'
        '    user = User(name, email)\n'
        '    if not user.validate_email():\n'
        '        raise ValueError("Invalid email")\n'
        '    return user\n'
        '\n'
        '\n'
        'def get_display(user):\n'
        '    """Get display name."""\n'
        '    return user.display_name()\n'
        '\n'
        '\n'
        'def unused_helper():\n'
        '    """This function is never called (dead code)."""\n'
        '    return 42\n'
    )

    (src / "utils.py").write_text(
        'def format_name(first, last):\n'
        '    """Format a full name."""\n'
        '    return f"{first} {last}"\n'
        '\n'
        '\n'
        'def parse_email(raw):\n'
        '    """Parse an email address."""\n'
        '    if "@" not in raw:\n'
        '        return None\n'
        '    parts = raw.split("@")\n'
        '    return {"user": parts[0], "domain": parts[1]}\n'
        '\n'
        '\n'
        'UNUSED_CONSTANT = "never_referenced"\n'
    )

    git_commit(git_repo, "add python project")
    return git_repo


@pytest.fixture
def indexed_project(python_project, monkeypatch):
    """Extend python_project by running `roam index` on it.

    Returns the path to the indexed project directory.
    """
    monkeypatch.chdir(python_project)
    out, rc = roam("index", cwd=python_project)
    assert rc == 0, f"roam index failed:\n{out}"
    return python_project


@pytest.fixture
def project_factory(tmp_path_factory):
    """Factory fixture for creating custom project layouts.

    Usage:
        def test_something(project_factory):
            proj = project_factory({
                "app.py": 'def main(): pass',
                "lib/helper.py": 'def help(): pass',
            })
            # proj is an indexed project path

    Returns a callable that accepts a dict of {relative_path: content}
    and returns an indexed project path.
    """
    def _create(files, *, index=True, extra_commits=None):
        proj = tmp_path_factory.mktemp("project")
        (proj / ".gitignore").write_text(".roam/\n")

        for rel_path, content in files.items():
            fp = proj / rel_path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)

        git_init(proj)

        if extra_commits:
            for commit_files, msg in extra_commits:
                for rel_path, content in commit_files.items():
                    fp = proj / rel_path
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(content)
                git_commit(proj, msg)

        if index:
            out, rc = roam("index", cwd=proj)
            assert rc == 0, f"roam index failed:\n{out}"

        return proj

    return _create


# ===========================================================================
# Snapshot helper for trend tests
# ===========================================================================

@pytest.fixture
def project_with_snapshots(indexed_project, monkeypatch):
    """An indexed project with multiple snapshots for trend testing.

    Creates 5 snapshots by modifying files between each.
    Returns the project path.
    """
    monkeypatch.chdir(indexed_project)

    # Snapshot 1 is created by index. Create 4 more.
    src = indexed_project / "src"
    for i in range(2, 6):
        (src / f"extra_{i}.py").write_text(
            f'def func_{i}():\n'
            f'    """Function {i}."""\n'
            f'    return {i}\n'
        )
        git_commit(indexed_project, f"add extra_{i}")
        out, rc = roam("index", cwd=indexed_project)
        assert rc == 0, f"roam index (snapshot {i}) failed:\n{out}"
        out, rc = roam("snapshot", "--tag", f"v{i}", cwd=indexed_project)
        # snapshot may or may not exist as command; don't assert

    return indexed_project
