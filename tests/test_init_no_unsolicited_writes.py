"""Tests for `roam init` trust contract: no unsolicited file writes.

Audit R1 + dogfood corpus: ``roam init`` previously dropped a
``.github/workflows/roam.yml`` into the user's repo on first install.
That's trust-damaging for non-GitHub users (and for users who are just
evaluating roam in a private repo). The fix promotes CI generation to
an explicit ``--with-ci=...`` opt-in with default ``none`` and adds an
operator hint pointing at the canonical ``roam ci-setup``.
"""

from __future__ import annotations

import pytest

from tests.conftest import git_init, invoke_cli


@pytest.fixture
def fresh_project(tmp_path):
    """Git-init'd project with a tiny Python module — NOT pre-indexed.

    ``roam init`` runs the indexer itself; pre-indexing here would
    mask the "first run" behaviour we care about.
    """
    proj = tmp_path / "init_no_unsolicited"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text('def main():\n    """Entry point."""\n    print("hi")\n')
    git_init(proj)
    return proj


class TestInitNoUnsolicitedWrites:
    def test_default_init_does_not_create_github_workflow(self, cli_runner, fresh_project, monkeypatch):
        """Default ``roam init`` MUST NOT write any CI workflow file.

        Pre-fix: ``.github/workflows/roam.yml`` was written every time.
        Post-fix: only ``--with-ci=github`` produces it.
        """
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0, f"init failed:\n{result.output}"
        workflow = fresh_project / ".github" / "workflows" / "roam.yml"
        assert not workflow.exists(), f"unsolicited workflow file at {workflow} — default init must not write CI config"
        # The .github directory itself shouldn't be created either.
        gh_dir = fresh_project / ".github"
        assert not gh_dir.exists(), f"unsolicited .github/ directory created at {gh_dir}"

    def test_explicit_with_ci_github_creates_workflow(self, cli_runner, fresh_project, monkeypatch):
        """``--with-ci=github`` is the explicit opt-in — workflow must exist."""
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init", "--with-ci=github"], cwd=fresh_project)
        assert result.exit_code == 0, f"init --with-ci=github failed:\n{result.output}"
        workflow = fresh_project / ".github" / "workflows" / "roam.yml"
        assert workflow.exists(), "--with-ci=github should create .github/workflows/roam.yml"
        # Sanity: file is non-empty and looks like a workflow.
        content = workflow.read_text(encoding="utf-8")
        assert content.strip(), "generated workflow is empty"
        assert "name: Roam" in content or "jobs:" in content

    def test_default_init_prints_ci_setup_hint(self, cli_runner, fresh_project, monkeypatch):
        """Default text output mentions `roam ci-setup` as the next step.

        The hint is what makes the opt-in discoverable. Without it, a
        user who actually wants CI integration has no obvious path
        forward after a default ``roam init``.
        """
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0
        assert "ci-setup" in result.output.lower(), (
            f"expected `roam ci-setup` hint in default init output, got:\n{result.output}"
        )
