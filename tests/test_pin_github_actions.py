"""Regression tests for the repository-wide GitHub Actions pinner."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
PINNER = ROOT / "dev" / "pin_github_actions.sh"


def test_check_finds_unpinned_action_in_external_template(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    script = repo / "dev" / "pin_github_actions.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(PINNER, script)

    template = repo / "templates" / "examples" / "ci.yml"
    template.parent.mkdir(parents=True)
    original = "name: example\nsteps:\n  - uses: actions/checkout@v4\n"
    template.write_text(original)

    composite = repo / "components" / "child" / "action.yaml"
    composite.parent.mkdir(parents=True)
    composite.write_text("name: child\nruns:\n  using: composite\n  steps:\n    - uses: actions/setup-python@v5\n")

    result = subprocess.run(
        ["bash", str(script), "--check"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "templates/examples/ci.yml:3: uses: actions/checkout@v4" in result.stdout
    assert "components/child/action.yaml:5: uses: actions/setup-python@v5" in result.stdout
    assert template.read_text() == original


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def test_check_ignores_vendored_trees_but_still_sees_composite_actions(tmp_path: Path) -> None:
    """Scope is git's index, not a filesystem walk.

    This repo checks out third-party repositories under bench-repos/ as
    benchmark fixtures. They carry hundreds of deliberately unpinned workflows
    that are not part of this repo's supply chain, and a `find`-based walk
    reported every one of them -- which would leave the gate permanently red.
    Enumerating from the index excludes them because they are gitignored, while
    a composite action at the repo root is still covered.
    """
    repo = tmp_path / "repo"
    script = repo / "dev" / "pin_github_actions.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(PINNER, script)

    (repo / ".gitignore").write_text("bench-repos/\n")

    vendored = repo / "bench-repos" / "third-party" / ".github" / "workflows" / "ci.yml"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("steps:\n  - uses: actions/checkout@v4\n")

    composite = repo / "action.yml"
    composite.write_text("name: root\nruns:\n  using: composite\n  steps:\n    - uses: actions/setup-python@v5\n")

    _git(repo, "init", "-q")
    _git(repo, "add", "dev/pin_github_actions.sh", ".gitignore", "action.yml")

    result = subprocess.run(["bash", str(script), "--check"], cwd=repo, capture_output=True, text=True, check=False)

    assert result.returncode != 0, "the tracked composite action is unpinned and must be reported"
    assert "action.yml:5: uses: actions/setup-python@v5" in result.stdout
    assert "bench-repos" not in result.stdout, "gitignored vendored trees are not this repo's supply chain"


def test_check_passes_on_current_tree() -> None:
    result = subprocess.run(
        ["bash", str(PINNER), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
