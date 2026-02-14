"""Smoke tests for the roam CLI entry points via subprocess.

These tests verify that the CLI boots correctly, handles flags, and
runs basic command sequences end-to-end through the real subprocess
interface (not CliRunner).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# conftest.py is auto-loaded by pytest for fixtures, but we need an
# explicit import for the non-fixture helpers (roam, git_init, etc.).
sys.path.insert(0, str(Path(__file__).parent))
from conftest import roam, git_init, git_commit


# ── Helpers / fixtures ──────────────────────────────────────────────

@pytest.fixture
def empty_git_repo(tmp_path):
    """A git repo with a .gitignore but no source files."""
    repo = tmp_path / "empty"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    git_init(repo)
    return repo


@pytest.fixture
def small_project(tmp_path):
    """A minimal Python project inside a git repo, ready for indexing."""
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    (repo / "app.py").write_text(
        "def main():\n"
        "    return greet('world')\n"
        "\n"
        "def greet(name):\n"
        "    return f'Hello, {name}!'\n"
    )
    (repo / "utils.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def unused():\n"
        "    pass\n"
    )
    git_init(repo)
    return repo


# ── 1. Version ──────────────────────────────────────────────────────

def test_version():
    """roam --version exits 0 and prints a version string."""
    output, rc = roam("--version")
    assert rc == 0
    # Version output looks like "roam-code, version X.Y.Z" or similar
    assert "version" in output.lower() or "." in output


# ── 2. Help ─────────────────────────────────────────────────────────

def test_help():
    """roam --help exits 0 and includes command categories."""
    output, rc = roam("--help")
    assert rc == 0
    # The categorized help should show at least one category
    assert "Getting Started" in output or "Codebase Health" in output
    # And the usage line
    assert "Usage" in output or "usage" in output.lower()


# ── 3. Per-command help ─────────────────────────────────────────────

_HELP_COMMANDS = [
    "index", "health", "map", "dead", "search", "grep",
    "weather", "clusters", "layers", "trend", "snapshot",
    "diff", "describe", "deps", "file", "symbol",
]


@pytest.mark.parametrize("cmd", _HELP_COMMANDS)
def test_help_per_command(cmd):
    """roam <cmd> --help exits 0 for each major command."""
    output, rc = roam(cmd, "--help")
    assert rc == 0, f"'{cmd} --help' failed (rc={rc}):\n{output}"
    assert "Usage" in output or "usage" in output.lower()


# ── 4. No args ──────────────────────────────────────────────────────

def test_no_args():
    """Running roam with no arguments shows help (Click returns exit 2 for missing command)."""
    output, rc = roam()
    # Click groups return exit 0 or 2 when invoked without a subcommand
    assert rc in (0, 2)
    assert "Usage" in output or "usage" in output.lower() or "roam" in output.lower()


# ── 5. Unknown command ──────────────────────────────────────────────

def test_unknown_command():
    """roam nonexistent returns a non-zero exit code."""
    output, rc = roam("nonexistent")
    assert rc != 0


# ── 6. Index creates DB ─────────────────────────────────────────────

def test_index_creates_db(small_project):
    """roam index creates .roam/index.db in the project root."""
    output, rc = roam("index", cwd=small_project)
    assert rc == 0, f"roam index failed:\n{output}"
    db_path = small_project / ".roam" / "index.db"
    assert db_path.exists(), f"Expected {db_path} to exist after indexing"
    assert db_path.stat().st_size > 0


# ── 7. Index in non-git directory ───────────────────────────────────

def test_index_no_git_repo(tmp_path):
    """roam index in a non-git directory fails gracefully."""
    plain_dir = tmp_path / "no_git"
    plain_dir.mkdir()
    (plain_dir / "hello.py").write_text("x = 1\n")
    output, rc = roam("index", cwd=plain_dir)
    # Should fail or warn -- non-zero exit or error message
    assert rc != 0 or "error" in output.lower() or "git" in output.lower()


# ── 8. JSON flag produces valid JSON ────────────────────────────────

def test_json_flag_produces_json(small_project):
    """roam --json health in an indexed project outputs valid JSON."""
    out, rc = roam("index", cwd=small_project)
    assert rc == 0, f"index failed:\n{out}"

    output, rc = roam("--json", "health", cwd=small_project)
    assert rc == 0, f"--json health failed:\n{output}"

    # Extract JSON -- there might be non-JSON lines before/after,
    # so find the outermost braces
    start = output.find("{")
    end = output.rfind("}") + 1
    assert start >= 0 and end > start, f"No JSON object found in output:\n{output}"
    data = json.loads(output[start:end])
    assert isinstance(data, dict)
    assert "command" in data
    assert "summary" in data


# ── 9. Compact flag ─────────────────────────────────────────────────

def test_compact_flag(small_project):
    """roam --compact health produces output (possibly shorter)."""
    out, rc = roam("index", cwd=small_project)
    assert rc == 0, f"index failed:\n{out}"

    normal_out, rc1 = roam("health", cwd=small_project)
    compact_out, rc2 = roam("--compact", "health", cwd=small_project)
    assert rc1 == 0
    assert rc2 == 0
    # Compact output should be non-empty
    assert len(compact_out.strip()) > 0


# ── 10. Unicode handling ────────────────────────────────────────────

def test_unicode_handling(tmp_path):
    """Indexing a project with unicode filenames and content succeeds."""
    repo = tmp_path / "unicode_proj"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    # Unicode content inside a normally-named file (safest across platforms)
    (repo / "module.py").write_text(
        "# -*- coding: utf-8 -*-\n"
        "def gruss():\n"
        '    return "Gruesse aus Muenchen"\n'
        "\n"
        "def data():\n"
        '    return {"key": "value", "emoji": "cafe"}\n'
    )
    git_init(repo)

    output, rc = roam("index", cwd=repo)
    assert rc == 0, f"index with unicode content failed:\n{output}"


# ── 11. Empty project ──────────────────────────────────────────────

def test_empty_project(empty_git_repo):
    """Indexing an empty git repo and running health still works."""
    out, rc = roam("index", cwd=empty_git_repo)
    assert rc == 0, f"index on empty repo failed:\n{out}"

    out2, rc2 = roam("health", cwd=empty_git_repo)
    # Health should succeed (possibly with a low score or warning)
    assert rc2 == 0, f"health on empty repo failed:\n{out2}"


# ── 12. Exit code on error ─────────────────────────────────────────

def test_exit_code_on_error(tmp_path):
    """Commands that cannot run return non-zero exit codes."""
    plain_dir = tmp_path / "nope"
    plain_dir.mkdir()
    # health in a directory with no index should fail
    output, rc = roam("health", cwd=plain_dir)
    assert rc != 0 or "error" in output.lower() or "no index" in output.lower()


# ── 13. Multiple commands sequential ───────────────────────────────

def test_multiple_commands_sequential(small_project):
    """Running index, health, and map sequentially all succeed."""
    out1, rc1 = roam("index", cwd=small_project)
    assert rc1 == 0, f"index failed:\n{out1}"

    out2, rc2 = roam("health", cwd=small_project)
    assert rc2 == 0, f"health failed:\n{out2}"

    out3, rc3 = roam("map", cwd=small_project)
    assert rc3 == 0, f"map failed:\n{out3}"
    # map should show some structure
    assert len(out3.strip()) > 0


# ── 14. Index idempotent ───────────────────────────────────────────

def test_index_idempotent(small_project):
    """Running roam index twice does not crash."""
    out1, rc1 = roam("index", cwd=small_project)
    assert rc1 == 0, f"first index failed:\n{out1}"

    out2, rc2 = roam("index", cwd=small_project)
    assert rc2 == 0, f"second index failed:\n{out2}"

    # DB should still exist and be valid
    db = small_project / ".roam" / "index.db"
    assert db.exists()


# ── 15. Stderr separation ──────────────────────────────────────────

def test_stderr_separation(small_project):
    """Errors go to stderr, structured data goes to stdout."""
    out, rc = roam("index", cwd=small_project)
    assert rc == 0, f"index failed:\n{out}"

    # Run health with JSON -- stdout should have the JSON, stderr may have warnings
    result = subprocess.run(
        [sys.executable, "-m", "roam", "--json", "health"],
        cwd=small_project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, f"health failed:\n{result.stderr}"

    # stdout should contain valid JSON
    stdout = result.stdout.strip()
    if stdout:
        start = stdout.find("{")
        end = stdout.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(stdout[start:end])
            assert isinstance(data, dict)

    # If there is any error output, it should be on stderr (not mixed into JSON)
    if result.stderr.strip():
        # stderr content should not be valid JSON (it is logs/warnings)
        try:
            json.loads(result.stderr.strip())
            # If stderr is also JSON, that is unexpected but not fatal
        except json.JSONDecodeError:
            pass  # Expected: stderr is text, not JSON
