"""W1074 — difflib closest-match for ``cmd_workflow`` and
``cmd_explain_command`` unknown-name UsageErrors.

W1066 cap-deferred two clean-fit sites; W1074 extends the
``difflib.get_close_matches(cutoff=0.6, n=2)`` pattern to both. The
fixes are additive: when no registered name is within cutoff, the
error message stays byte-identical to the pre-W1074 phrasing.

Three scenarios per site:

1. Unknown name with a close match - message includes
   ``"Did you mean: '<match>'?"``.
2. Unknown name with no close match - message is byte-identical to
   the pre-W1074 phrasing (no ``"Did you mean"`` line / fragment).
3. The known-good path (a registered name) succeeds and never reaches
   the unknown-name disclosure code path.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture
def cli_runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# cmd_workflow — unknown recipe name
# ---------------------------------------------------------------------------


def test_workflow_unknown_recipe_close_match_suggests(cli_runner):
    """``roam workflow safe-delet-check`` (one missing char) lands well
    inside the cutoff of ``safe-delete-check`` and surfaces a
    ``Did you mean: 'safe-delete-check'?`` fragment in the UsageError
    text. The structured ``UNKNOWN_RECIPE:`` prefix is preserved."""
    result = cli_runner.invoke(cli, ["workflow", "safe-delet-check"], catch_exceptions=False)
    assert result.exit_code != 0
    # Click writes UsageError text to stderr in 8.2+, but the CliRunner
    # combines streams into ``result.output`` when invoked without
    # ``mix_stderr=False`` — both surfaces are checked.
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "UNKNOWN_RECIPE" in combined
    assert "safe-delet-check" in combined
    # The closest-match suggestion is the W1074 add.
    assert "Did you mean" in combined
    assert "safe-delete-check" in combined


def test_workflow_unknown_recipe_no_close_match_omits_suggestion(cli_runner):
    """``roam workflow zzzzzzzz`` is nowhere near any registered recipe
    name. The error message must NOT include a ``Did you mean`` fragment
    when no candidate is within cutoff 0.6."""
    result = cli_runner.invoke(cli, ["workflow", "zzzzzzzz"], catch_exceptions=False)
    assert result.exit_code != 0
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "UNKNOWN_RECIPE" in combined
    assert "zzzzzzzz" in combined
    # Suppressed cleanly when no candidate clears cutoff 0.6.
    assert "Did you mean" not in combined


def test_workflow_known_recipe_no_suggestion_path(cli_runner):
    """A registered recipe name resolves through ``by_name`` and the
    unknown-name code path is never reached. The verdict text shows the
    recipe name, never a ``Did you mean`` fragment."""
    result = cli_runner.invoke(cli, ["workflow", "safe-delete-check"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "safe-delete-check" in result.output
    assert "Did you mean" not in result.output


# ---------------------------------------------------------------------------
# cmd_explain_command — unknown command name
# ---------------------------------------------------------------------------


def test_explain_command_unknown_close_match_suggests(cli_runner):
    """``roam explain-command healt`` (one missing char) lands within
    cutoff 0.6 of registered ``health``. The hint stream MUST include
    a ``did you mean 'health'`` fragment alongside the pre-W1074
    ``run 'roam surface'`` hint."""
    result = cli_runner.invoke(cli, ["explain-command", "healt"], catch_exceptions=False)
    assert result.exit_code == 2, result.output
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    # Pre-W1074 error line preserved.
    assert "ERROR: unknown command 'healt'" in combined
    # W1074 closest-match line surfaces.
    assert "did you mean" in combined.lower()
    assert "'health'" in combined
    # Pre-W1074 surface-hint line preserved (byte-identical).
    assert "run 'roam surface'" in combined


def test_explain_command_unknown_no_close_match_omits_suggestion(cli_runner):
    """``roam explain-command zzzzzzzz`` is nowhere near any registered
    command. The pre-W1074 error/hint pair stays byte-identical: no
    ``did you mean`` line is injected when no candidate clears cutoff
    0.6."""
    result = cli_runner.invoke(cli, ["explain-command", "zzzzzzzz"], catch_exceptions=False)
    assert result.exit_code == 2, result.output
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "ERROR: unknown command 'zzzzzzzz'" in combined
    assert "run 'roam surface'" in combined
    # Suppressed cleanly when no candidate clears cutoff 0.6.
    assert "did you mean" not in combined.lower()


def test_explain_command_known_no_suggestion_path(cli_runner):
    """A registered canonical command name resolves cleanly through
    ``_build_surface`` and never reaches the unknown-name disclosure.
    No ``did you mean`` fragment in stdout/stderr."""
    result = cli_runner.invoke(cli, ["explain-command", "surface"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "VERDICT:" in combined
    assert "did you mean" not in combined.lower()
