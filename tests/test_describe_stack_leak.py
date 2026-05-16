"""Regression test for `roam describe --agent-prompt` Stack: leak.

Pre-fix the agent-prompt section emitted a ``Stack: src`` line (or
``Stack: tests``) on any monorepo where the top-imports were local
files in the same directory. The language list one line above already
covers the same question more accurately, so the Stack line was
removed entirely. See
``internal/dogfood/research/roam-describe-stack-directory-leak-2026-05-12.md``.
"""

from __future__ import annotations

import pytest

from tests.conftest import git_init, index_in_process, invoke_cli


@pytest.fixture
def stack_leak_project(tmp_path):
    """Mini monorepo where top-imports all live under ``src/``.

    This is the exact shape that triggered the leak: ``service.py``
    and ``utils.py`` both import from ``models.py`` so file_edges
    targets ``src/models.py`` whose parts[0] == "src".
    """
    proj = tmp_path / "stack_leak_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "models.py").write_text("class User:\n    def __init__(self, name):\n        self.name = name\n")
    (src / "service.py").write_text("from models import User\n\ndef create_user(name):\n    return User(name)\n")
    (src / "utils.py").write_text("from models import User\n\ndef get_name(u):\n    return u.name\n")
    git_init(proj)
    index_in_process(proj)
    return proj


class TestDescribeNoStackLeak:
    def test_agent_prompt_does_not_emit_stack_line(self, cli_runner, stack_leak_project, monkeypatch):
        """``Stack:`` must not appear anywhere in agent-prompt text.

        Output line ``Stack: src`` was the regression we're guarding
        against — confirm the prefix is gone, not just the specific
        value.
        """
        monkeypatch.chdir(stack_leak_project)
        result = invoke_cli(cli_runner, ["describe", "--agent-prompt"], cwd=stack_leak_project)
        assert result.exit_code == 0, f"describe failed:\n{result.output}"
        for line in result.output.splitlines():
            assert not line.startswith("Stack:"), (
                f"unexpected Stack line in agent-prompt output: {line!r}\nfull output:\n{result.output}"
            )
        # Sanity: the language list (the line that subsumes Stack) is still there.
        assert "Project:" in result.output
