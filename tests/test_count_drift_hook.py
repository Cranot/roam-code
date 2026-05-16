"""Assert that the README/CLAUDE/llms-install count-drift gate is installed.

W21.10 shipped ``dev/build_readme_counts.py --check`` to detect count drift
between the source of truth (``roam.surface_counts`` → AST-parsed
``src/roam/cli.py`` + ``src/roam/mcp_server.py``) and the prose counts in
README.md / CLAUDE.md / llms-install.md / mcp-server-card.json.

This module enforces that the gate is wired into whichever drift-prevention
surface the repo uses:

1. If ``.pre-commit-config.yaml`` exists, the hook must reference
   ``dev/build_readme_counts.py``. (Currently absent — test is a no-op skip;
   exists so that adopting pre-commit later does not silently drop the gate.)
2. ``.github/workflows/roam-ci.yml`` (or any sibling workflow) must run
   ``dev/build_readme_counts.py --check`` on every push / PR.

The pair makes drift impossible to merge: either pre-commit catches it
locally (when adopted), or CI catches it in the PR.
"""

from __future__ import annotations

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()

PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
DRIFT_SCRIPT_REL = "dev/build_readme_counts.py"


def test_pre_commit_config_includes_roam_readme_counts() -> None:
    """If a pre-commit config exists, our hook must be in it.

    The repo does not currently use the pre-commit framework, so this test
    is a no-op skip. It exists to lock in the invariant: if a future change
    introduces ``.pre-commit-config.yaml``, the count-drift hook must be
    one of the configured hooks.
    """
    if not PRE_COMMIT_CONFIG.exists():
        # No pre-commit framework adopted yet — invariant is vacuously satisfied.
        # CI gate (next test) is the active drift defence.
        return

    text = PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
    assert DRIFT_SCRIPT_REL in text, (
        f"{PRE_COMMIT_CONFIG.name} exists but does not reference "
        f"{DRIFT_SCRIPT_REL}. Add a local hook that runs "
        f"`python {DRIFT_SCRIPT_REL} --check` so count drift is caught "
        "before commit, matching the CI gate."
    )


def test_ci_workflow_includes_build_readme_counts_check() -> None:
    """At least one CI workflow must run ``build_readme_counts.py --check``.

    This is the primary drift defence: every push / PR runs the check, and
    CI fails if any quoted count (command total, MCP-tool total, etc.) has
    drifted from the AST-derived source of truth.
    """
    assert WORKFLOWS_DIR.is_dir(), (
        f"Expected CI workflows directory at {WORKFLOWS_DIR}. "
        "The count-drift gate relies on CI to catch contributor drift."
    )

    needle = f"{DRIFT_SCRIPT_REL} --check"
    matching = [path for path in sorted(WORKFLOWS_DIR.glob("*.yml")) if needle in path.read_text(encoding="utf-8")]

    assert matching, (
        f"No workflow in {WORKFLOWS_DIR} invokes `python {needle}`. "
        "Add it as a step in the doc-hygiene job (or equivalent) so count "
        "drift between README / CLAUDE / llms-install and the source of "
        "truth fails CI."
    )
