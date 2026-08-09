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


# ---------------------------------------------------------------------------
# A tracked YAML file the gate cannot read must make it REFUSE, not pass.
#
# Measured on a byte-identical copy of the script: with two tracked workflows
# both carrying floating refs, deleting the alphabetically-FIRST one made the
# gate print nothing and exit 0 -- the later file's real `@v3` was never
# examined. Deleting the LAST one reported the first and exited 1. The right
# answer was reachable only by luck of ordering.
#
# Mechanism: `set -euo pipefail` plus `done < "$file"`. A redirect failure
# returns non-zero, `set -e` kills the process-substitution SUBSHELL, and every
# later file is skipped. The consumer `while ... done < <(scan_refs)` never
# inspects the substitution's exit status, so it sees EOF, `unpinned` stays 0,
# and `exit "$unpinned"` exits 0. The only signal was a bash diagnostic on
# stderr; the gate's own published claim is the exit code.
# ---------------------------------------------------------------------------


def _two_file_repo(tmp_path: Path, name: str) -> tuple[Path, Path]:
    repo = tmp_path / name
    script = repo / "dev" / "pin_github_actions.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(PINNER, script)

    (repo / "aaa.yml").write_text("jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n")
    (repo / "zzz.yml").write_text("jobs:\n  z:\n    steps:\n      - uses: actions/setup-python@v3\n")

    _git(repo, "init", "-q")
    _git(repo, "add", "dev/pin_github_actions.sh", "aaa.yml", "zzz.yml")
    return repo, script


def _check(repo: Path, script: Path):
    return subprocess.run(["bash", str(script), "--check"], cwd=repo, capture_output=True, text=True, check=False)


def test_baseline_reports_both_floating_refs(tmp_path: Path) -> None:
    repo, script = _two_file_repo(tmp_path, "baseline")
    result = _check(repo, script)
    assert result.returncode != 0
    assert "aaa.yml:4: uses: actions/checkout@v4" in result.stdout
    assert "zzz.yml:4: uses: actions/setup-python@v3" in result.stdout


def test_unreadable_first_file_does_not_hide_the_later_violation(tmp_path: Path) -> None:
    """THE defect: the alphabetically-first file aborted the whole scan."""
    repo, script = _two_file_repo(tmp_path, "first_missing")
    (repo / "aaa.yml").unlink()

    result = _check(repo, script)

    # Pre-fix: returncode 0 and stdout empty.
    assert result.returncode != 0, result.stdout + result.stderr
    assert "zzz.yml:4: uses: actions/setup-python@v3" in result.stdout
    assert "aaa.yml" in result.stderr


def test_unreadable_last_file_is_also_refused(tmp_path: Path) -> None:
    """Both orderings, because the defect was visible in only one of them."""
    repo, script = _two_file_repo(tmp_path, "last_missing")
    (repo / "zzz.yml").unlink()

    result = _check(repo, script)

    assert result.returncode != 0
    assert "aaa.yml:4: uses: actions/checkout@v4" in result.stdout
    assert "zzz.yml" in result.stderr


def test_an_unreadable_file_alone_is_enough_to_refuse(tmp_path: Path) -> None:
    """No violations found, but part of the input was never read = UNANALYZABLE.

    Without this the gate would exit 0 on a scan it could not complete --
    exactly the shape the repo's own exit_codes doctrine refuses (CLEAN /
    VIOLATION / UNANALYZABLE, and only CLEAN authorizes).
    """
    repo = tmp_path / "unreadable_only"
    script = repo / "dev" / "pin_github_actions.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(PINNER, script)
    pinned = "actions/checkout@" + "1" * 40
    (repo / "a.yml").write_text(f"jobs:\n  a:\n    steps:\n      - uses: {pinned}\n")
    (repo / "b.yml").write_text(f"jobs:\n  b:\n    steps:\n      - uses: {pinned}\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "dev/pin_github_actions.sh", "a.yml", "b.yml")
    (repo / "b.yml").unlink()

    result = _check(repo, script)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "UNANALYZABLE" in result.stderr
    assert "b.yml" in result.stderr


def test_the_scan_publishes_its_own_denominator(tmp_path: Path) -> None:
    repo, script = _two_file_repo(tmp_path, "denominator")
    assert "scanned 2/2 tracked YAML files" in _check(repo, script).stdout

    (repo / "aaa.yml").unlink()
    assert "scanned 1/2 tracked YAML files" in _check(repo, script).stdout


# ---- MUST NOT FIRE -------------------------------------------------------


def test_a_fully_pinned_readable_repo_still_exits_zero(tmp_path: Path) -> None:
    """The guard must not turn a clean repo red."""
    repo = tmp_path / "clean"
    script = repo / "dev" / "pin_github_actions.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(PINNER, script)
    pinned = "actions/checkout@" + "a" * 40
    (repo / "a.yml").write_text(f"jobs:\n  a:\n    steps:\n      - uses: {pinned}\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "dev/pin_github_actions.sh", "a.yml")

    result = _check(repo, script)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "scanned 1/1 tracked YAML files" in result.stdout
    assert "UNANALYZABLE" not in result.stderr


def test_the_index_scope_decision_survives_the_fix(tmp_path: Path) -> None:
    """REGRESSION GUARD: enumeration must stay `git ls-files`, not `find`.

    The script's own comment records the measured outcome: `find` descends
    into bench-repos/ third-party checkouts carrying hundreds of deliberately
    unpinned workflows and left the gate permanently red.
    """
    repo = tmp_path / "scope"
    script = repo / "dev" / "pin_github_actions.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(PINNER, script)
    (repo / ".gitignore").write_text("bench-repos/\n")
    vendored = repo / "bench-repos" / "third-party" / ".github" / "workflows" / "ci.yml"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("steps:\n  - uses: actions/checkout@v4\n")
    pinned = "actions/checkout@" + "b" * 40
    (repo / "a.yml").write_text(f"jobs:\n  a:\n    steps:\n      - uses: {pinned}\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "dev/pin_github_actions.sh", ".gitignore", "a.yml")

    result = _check(repo, script)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "bench-repos" not in result.stdout + result.stderr
