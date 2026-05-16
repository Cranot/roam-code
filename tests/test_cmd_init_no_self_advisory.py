"""W1291 regression: ``roam init`` must not self-recommend itself.

Smoke Bug #4 from ``docs/fresh-install-smoke.md``: cmd_init calls
``ensure_index()`` which historically printed::

    No roam index found. Run `roam init` to create one.

before building. The advisory is useful when a command that CONSUMES an
index hits a cold-start, but it's confusing UX when the user just typed
``roam init`` -- the tool tells them to run the command they're already
running.

Fix: ``ensure_index(..., suppress_cold_start_advisory=True)`` from
cmd_init's call site only. The advisory continues to fire as designed
for every other command that consumes the index.
"""

from __future__ import annotations

import pytest

from tests.conftest import git_init, invoke_cli


@pytest.fixture
def fresh_project(tmp_path):
    """A git-init'd, NOT-yet-indexed project (init will build the index)."""
    proj = tmp_path / "init_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def main():\n"
        '    """Entry point."""\n'
        '    print("hello")\n'
    )
    git_init(proj)
    return proj


# Match the exact advisory string fragment from
# src/roam/commands/resolve.py:ensure_index. We assert the self-recommend
# substring rather than the whole banner so the test stays stable if the
# tip / doctor lines get reworded.
_SELF_RECOMMEND_FRAGMENT = "Run `roam init` to create one"


class TestInitNoSelfAdvisory:
    def test_text_mode_does_not_self_recommend(self, cli_runner, fresh_project, monkeypatch):
        """`roam init` text output must not tell the user to run `roam init`."""
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0, f"init exited non-zero:\n{result.output}"
        assert _SELF_RECOMMEND_FRAGMENT not in result.output, (
            f"`roam init` self-recommended `roam init`. Full output:\n{result.output}"
        )

    def test_json_mode_does_not_self_recommend(self, cli_runner, fresh_project, monkeypatch):
        """JSON-mode init must also stay silent on the cold-start advisory."""
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project, json_mode=True)
        assert result.exit_code == 0, f"init --json exited non-zero:\n{result.output}"
        assert _SELF_RECOMMEND_FRAGMENT not in result.output, (
            f"`roam init --json` self-recommended `roam init`. Full output:\n{result.output}"
        )

    def test_other_commands_still_emit_advisory(self, cli_runner, tmp_path, monkeypatch):
        """Negative control: cold-start advisory still fires for non-init commands.

        Confirms the suppression is scoped to cmd_init -- we did not silently
        kill the advisory for every consumer-of-index command.
        """
        proj = tmp_path / "consumer_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "app.py").write_text("def main():\n    return 1\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        # ``roam health`` consumes the index and goes through ensure_index.
        result = invoke_cli(cli_runner, ["health"], cwd=proj)
        # The advisory is informational, not fatal -- health auto-builds.
        # We only assert the cold-start text appears in the (combined) output.
        assert _SELF_RECOMMEND_FRAGMENT in result.output, (
            f"Expected cold-start advisory on `roam health` against an unindexed "
            f"project, got:\n{result.output}"
        )
