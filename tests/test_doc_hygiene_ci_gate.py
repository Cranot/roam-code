"""W250 / Wave30.1 -- doc-hygiene CI gate is wired in BOTH surfaces.

Companion to ``tests/test_count_drift_hook.py`` (which only checked
``build_readme_counts.py`` was in some workflow). This module enforces the
W250 ask:

1. A GitHub Actions workflow runs ``dev/build_readme_counts.py --check``
   AND ``scripts/sync_surface_counts.py`` on every push / PR.
2. ``.githooks/pre-commit`` exists, is executable, and invokes the same
   two scripts so contributors catch drift locally before CI.
3. ``build_readme_counts.py --check`` is invocable end-to-end (the
   script imports cleanly and at minimum prints its help).
4. CONTRIBUTING.md (or README) documents ``git config core.hooksPath
   .githooks`` so contributors know how to opt in.

Why both gates: the GitHub Actions job is the hard merge gate; the
pre-commit hook is the fast local feedback loop. A drop on either
surface re-opens the bug class W250 was designed to close.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
PRE_COMMIT_HOOK = REPO_ROOT / ".githooks" / "pre-commit"

BUILD_README_REL = "dev/build_readme_counts.py"
SYNC_SURFACE_REL = "scripts/sync_surface_counts.py"


# ---------------------------------------------------------------------------
# GitHub Actions surface
# ---------------------------------------------------------------------------


def _workflow_texts() -> dict[Path, str]:
    """Return ``{workflow_path: text}`` for every yaml file under .github/workflows."""
    if not WORKFLOWS_DIR.is_dir():
        return {}
    out: dict[Path, str] = {}
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        out[path] = path.read_text(encoding="utf-8")
    for path in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        out[path] = path.read_text(encoding="utf-8")
    return out


def test_doc_hygiene_workflow_file_exists() -> None:
    """Some workflow must run BOTH count-drift scripts on push / PR.

    The W250 spec accepts either a dedicated ``doc-hygiene.yml`` workflow
    OR a doc-hygiene job inside an existing workflow (the current shape:
    ``roam-ci.yml`` has a ``doc-hygiene`` job). Both are valid.
    """
    workflows = _workflow_texts()
    assert workflows, (
        f"Expected at least one GitHub Actions workflow under {WORKFLOWS_DIR}; "
        "the doc-hygiene CI gate (W250) relies on Actions to block merges on drift."
    )

    runs_build_readme = [p.name for p, text in workflows.items() if f"{BUILD_README_REL} --check" in text]
    runs_sync_surface = [p.name for p, text in workflows.items() if SYNC_SURFACE_REL in text]

    assert runs_build_readme, (
        f"No workflow in {WORKFLOWS_DIR} runs `python {BUILD_README_REL} --check`. "
        "Add it (or its containing job) so README/CLAUDE/llms-install count drift fails CI."
    )
    assert runs_sync_surface, (
        f"No workflow in {WORKFLOWS_DIR} runs `python {SYNC_SURFACE_REL}`. "
        "Add it so free-form surface (landing page, server.json, llms.txt) drift fails CI."
    )


def test_doc_hygiene_workflow_triggers_on_pr_and_push() -> None:
    """The workflow carrying the drift gate must fire on PRs (and ideally push to main).

    A drift gate that only runs on manual ``workflow_dispatch`` is not a
    merge gate. At minimum it has to run on ``pull_request``.
    """
    workflows = _workflow_texts()
    gate_workflows = [
        (path, text) for path, text in workflows.items() if f"{BUILD_README_REL} --check" in text
    ]
    assert gate_workflows, "no workflow contains the README count-drift gate"

    for path, text in gate_workflows:
        # Be lenient on yaml shape; just require that 'pull_request' appears in the file.
        assert "pull_request" in text, (
            f"{path.name} contains the doc-hygiene gate but does not run on pull_request. "
            "Add `on: [pull_request]` (or extend the existing trigger list) so the gate fires on PRs."
        )


# ---------------------------------------------------------------------------
# Pre-commit hook surface
# ---------------------------------------------------------------------------


def test_pre_commit_hook_exists() -> None:
    """``.githooks/pre-commit`` is present so ``core.hooksPath`` users get the gate locally."""
    assert PRE_COMMIT_HOOK.is_file(), (
        f"Expected {PRE_COMMIT_HOOK} to exist. Without it, contributors who run "
        "`git config core.hooksPath .githooks` get the commit-msg gate but not "
        "the doc-hygiene drift gate locally. Add the hook (W250)."
    )


def test_pre_commit_hook_is_executable() -> None:
    """The hook must have the executable bit set on POSIX (Windows ignores this)."""
    if not PRE_COMMIT_HOOK.is_file():
        pytest.skip("pre-commit hook missing (handled by sibling test)")
    if os.name == "nt":
        # Windows filesystems do not carry the executable bit; git stores
        # the mode in the index. CI runs on ubuntu-latest and will catch
        # any drop of the +x bit at commit time.
        pytest.skip("executable-bit check is POSIX-only")
    mode = PRE_COMMIT_HOOK.stat().st_mode
    assert mode & stat.S_IXUSR, (
        f"{PRE_COMMIT_HOOK} is not executable. Run `chmod +x {PRE_COMMIT_HOOK}` "
        "and commit. Git will record the +x bit in the index."
    )


def test_pre_commit_hook_invokes_both_drift_scripts() -> None:
    """The hook must run BOTH ``build_readme_counts.py --check`` and ``sync_surface_counts.py``.

    Mirrors the two-step shape of the ``doc-hygiene`` CI job so local and
    CI checks fail on the same conditions.
    """
    if not PRE_COMMIT_HOOK.is_file():
        pytest.skip("pre-commit hook missing (handled by sibling test)")
    text = PRE_COMMIT_HOOK.read_text(encoding="utf-8")
    assert BUILD_README_REL in text, (
        f"{PRE_COMMIT_HOOK} does not invoke {BUILD_README_REL}. "
        "Add a `python dev/build_readme_counts.py --check` step."
    )
    assert "--check" in text, (
        f"{PRE_COMMIT_HOOK} mentions {BUILD_README_REL} but not `--check`. "
        "The hook must call it in --check mode (apply mode would rewrite files mid-commit)."
    )
    assert SYNC_SURFACE_REL in text, (
        f"{PRE_COMMIT_HOOK} does not invoke {SYNC_SURFACE_REL}. "
        "Add a `python scripts/sync_surface_counts.py` step (dry-run is the default)."
    )


# ---------------------------------------------------------------------------
# Script invocability
# ---------------------------------------------------------------------------


def test_build_readme_counts_check_invocable() -> None:
    """``python dev/build_readme_counts.py --check`` runs without crashing.

    The exit code is informational only -- 0 means counts are in sync, 1
    means drift was detected (also valid in a hostile working tree). What
    we DO require: the script imports cleanly, the --check flag is
    recognised, and the process exits with a real status code rather than
    crashing with an ImportError or argparse failure.
    """
    script = REPO_ROOT / BUILD_README_REL
    assert script.is_file(), f"{script} missing"

    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Any return code is acceptable EXCEPT the codes Python uses for
    # uncaught exceptions / argparse errors (2 from argparse, anything
    # >2 here would indicate the script crashed before doing its work).
    # We allow 0 (clean) and 1 (drift); we reject 2 (argparse) and >2.
    assert result.returncode in (0, 1), (
        f"`python {BUILD_README_REL} --check` returned {result.returncode}; "
        f"expected 0 (in sync) or 1 (drift detected). "
        f"stderr: {result.stderr[:500]!r}"
    )


# ---------------------------------------------------------------------------
# Documentation surface
# ---------------------------------------------------------------------------


def test_pre_commit_hook_documented() -> None:
    """CONTRIBUTING.md or README documents `git config core.hooksPath .githooks`.

    Without this one-liner, contributors who clone the repo run no
    hooks. We do not require any specific section heading; we just
    require the literal command appears somewhere a contributor can
    find it.
    """
    needle = "core.hooksPath .githooks"
    candidates = [REPO_ROOT / "CONTRIBUTING.md", REPO_ROOT / "README.md"]
    for path in candidates:
        if path.is_file() and needle in path.read_text(encoding="utf-8"):
            return
    raise AssertionError(
        f"Neither CONTRIBUTING.md nor README.md mentions `git config {needle}`. "
        "Document the one-liner so contributors know how to opt into the local "
        "doc-hygiene + commit-msg hooks."
    )
