"""Tests for the canonical exclude-paths set on ``roam bus-factor``.

SYNTHESIS Rank 16: ``.github/``, ``.claude/``, ``docs/`` etc. should NOT
count toward bus-factor / knowledge-loss risk. Without this filter, a
single-author CI workflow ends up dominating the verdict on otherwise
healthy codebases.

These tests pin the default exclusion behaviour and the
``--include-excluded`` escape hatch.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.conftest import (
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


def _git_commit_as(path, author_name, author_email, message):
    """Make a git commit with an explicit author identity."""
    import os

    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    env = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=path,
        capture_output=True,
        env={**os.environ, **env},
    )


@pytest.fixture
def project_with_github(tmp_path):
    """Project containing both source code AND a ``.github/workflows/``
    file. The CI workflow has its own author (``CiBot``) so we can tell
    whether bus-factor surfaced ``.github/`` as a knowledge-loss risk.
    """
    proj = tmp_path / "github_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()
    (src / "lib.py").write_text(
        "def compute(x):\n"
        '    """Compute something."""\n'
        "    return x * 2\n"
        "\n"
        "\n"
        "def helper(y):\n"
        '    """Helper."""\n'
        "    return y + 1\n"
    )

    workflows = proj / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "name: ci\n"
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
    )

    git_init(proj)

    # Alice owns src/lib.py
    (src / "lib.py").write_text(
        "def compute(x):\n"
        '    """Compute something (Alice rev)."""\n'
        "    return x * 3\n"
        "\n"
        "\n"
        "def helper(y):\n"
        '    """Helper (Alice rev)."""\n'
        "    return y + 2\n"
        "\n"
        "\n"
        "def extra(z):\n"
        '    """Extra method."""\n'
        "    return z - 1\n"
    )
    _git_commit_as(proj, "Alice", "alice@example.com", "lib: alice revision")

    # CiBot owns .github/workflows/ci.yml — sole author => "bus factor 1"
    # for that directory if we don't filter it out.
    (workflows / "ci.yml").write_text(
        "name: ci\n"
        "on: [push, pull_request]\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - run: pytest\n"
    )
    _git_commit_as(proj, "CiBot", "ci@example.com", "ci: add pytest step")

    (workflows / "ci.yml").write_text(
        "name: ci\n"
        "on: [push, pull_request]\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - run: pytest\n"
        "      - run: ruff check .\n"
    )
    _git_commit_as(proj, "CiBot", "ci@example.com", "ci: add ruff check")

    index_in_process(proj)
    return proj


def _directories(data):
    return data.get("directories", [])


def _directory_paths(data):
    return [d["directory"] for d in _directories(data)]


def test_bus_factor_excludes_github_workflows_by_default(
    cli_runner, project_with_github, monkeypatch
):
    """Default bus-factor must not surface ``.github/workflows/`` as a risk."""
    monkeypatch.chdir(project_with_github)
    result = invoke_cli(
        cli_runner, ["bus-factor"], cwd=project_with_github, json_mode=True
    )
    data = parse_json_output(result, "bus-factor")

    paths = _directory_paths(data)
    # No directory in the ranking should be under .github/
    github_dirs = [p for p in paths if ".github" in p]
    assert not github_dirs, (
        f"Expected .github/ directories to be excluded by default, got: {github_dirs}\n"
        f"All directories: {paths}"
    )

    # And CiBot should not appear in any top_authors list since the
    # only file they touched lives under .github/workflows/.
    for d in _directories(data):
        names = [a.get("name") for a in d.get("top_authors", [])]
        assert "CiBot" not in names, (
            f"CiBot (CI-only author) leaked into top_authors of "
            f"{d['directory']}: {names}"
        )


def test_bus_factor_includes_excluded_with_flag(
    cli_runner, project_with_github, monkeypatch
):
    """``--include-excluded`` restores legacy scan-everything behaviour."""
    monkeypatch.chdir(project_with_github)
    result = invoke_cli(
        cli_runner,
        ["--include-excluded", "bus-factor"],
        cwd=project_with_github,
        json_mode=True,
    )
    data = parse_json_output(result, "bus-factor")

    paths = _directory_paths(data)
    # With the override, the CI workflow's directory should be visible.
    github_dirs = [p for p in paths if ".github" in p]
    assert github_dirs, (
        f"Expected .github/ directories to appear with --include-excluded, "
        f"all dirs: {paths}"
    )

    # And CiBot should now be a recognised contributor somewhere.
    found_cibot = False
    for d in _directories(data):
        names = [a.get("name") for a in d.get("top_authors", [])]
        if "CiBot" in names:
            found_cibot = True
            break
    assert found_cibot, "CiBot should appear as an author with --include-excluded"

    # Envelope reports the override too — empty list means "nothing filtered".
    summary = data.get("summary", {})
    assert summary.get("exclude_prefixes_active") == [], (
        f"--include-excluded should clear exclude_prefixes_active, got: "
        f"{summary.get('exclude_prefixes_active')}"
    )
    assert summary.get("excluded_files_count", 0) == 0, (
        f"--include-excluded should report 0 excluded files, got: "
        f"{summary.get('excluded_files_count')}"
    )


def test_bus_factor_envelope_reports_excluded_count(
    cli_runner, project_with_github, monkeypatch
):
    """Default envelope exposes how many files were filtered and which
    prefixes were active so agents can see the gate's effect.
    """
    monkeypatch.chdir(project_with_github)
    result = invoke_cli(
        cli_runner, ["bus-factor"], cwd=project_with_github, json_mode=True
    )
    data = parse_json_output(result, "bus-factor")
    summary = data.get("summary", {})

    assert "excluded_files_count" in summary, (
        f"summary missing excluded_files_count: keys={list(summary.keys())}"
    )
    assert "exclude_prefixes_active" in summary, (
        f"summary missing exclude_prefixes_active: keys={list(summary.keys())}"
    )

    excluded = summary["excluded_files_count"]
    assert isinstance(excluded, int)
    assert excluded > 0, (
        f"Expected at least one excluded file (.github/workflows/ci.yml), "
        f"got excluded_files_count={excluded}"
    )

    active = summary["exclude_prefixes_active"]
    assert isinstance(active, list)
    assert ".github/" in active, (
        f"Expected '.github/' in exclude_prefixes_active, got: {active}"
    )
