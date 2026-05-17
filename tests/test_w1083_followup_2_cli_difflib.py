"""W1083-followup-2 — align ``cli.py`` no-such-command suggestions to canonical ``n=2``.

The LazyGroup ``resolve_command`` override at ``src/roam/cli.py:~848``
emits ``difflib.get_close_matches`` suggestions when an unknown
top-level command is invoked. Prior to W1083-followup-2 it used
``n=3`` — drifting from the 5-callsite canonical (``cmd_math:333``,
``cmd_oracle:55``, ``cmd_smells:579``, plus the
``structured_unknown_filter`` default at ``output/structured_unknowns.py``).
This test pins the cap at 2.

Notes
-----
* cli.py is OUTSIDE ``src/roam/commands/`` so it's NOT a
  ``structured_unknown_filter`` adopter candidate (that helper is for
  command-internal closed-set validation, not the CLI dispatcher
  boundary). The fix is local: align the ``n=`` knob only.
* The cutoff (0.6) was already canonical before this WAVE.
"""

from __future__ import annotations

from click.testing import CliRunner

from roam.cli import cli


def _invoke(bad: str):
    return CliRunner().invoke(cli, [bad])


def test_unknown_command_with_many_near_matches_caps_at_two() -> None:
    """``rean`` has 3 candidates within cutoff=0.6 (trend, clean, breaking).

    Pre-W1083-followup-2 (n=3) emitted all three; post-fix caps at 2.
    """
    result = _invoke("rean")
    assert result.exit_code == 2
    # The suggestion line is comma-separated; count occurrences of "`roam ".
    suggestion_count = result.output.count("`roam ")
    assert suggestion_count <= 2, (
        f"expected at most 2 suggestions, got {suggestion_count}: {result.output!r}"
    )
    assert "No such command: 'rean'" in result.output
    assert "Did you mean" in result.output


def test_unknown_command_with_one_match_still_emits_one() -> None:
    """``critic`` has one strong match (``critique``); n=2 doesn't shrink it."""
    result = _invoke("critic")
    assert result.exit_code == 2
    assert "`roam critique`" in result.output
    # Single-match path: only one backticked suggestion in the message.
    suggestion_count = result.output.count("`roam ")
    assert suggestion_count == 1, (
        f"expected exactly 1 suggestion, got {suggestion_count}: {result.output!r}"
    )


def test_unknown_command_with_zero_matches_omits_suggestion_line() -> None:
    """Random gibberish below cutoff falls through to no ``Did you mean`` line.

    The ``ask`` classifier recipe-hint path may still fire for >=6-char
    inputs; either way, no ``roam <cmd>`` literal-suggestion list is
    emitted.
    """
    result = _invoke("xxxxxxxxxxxxx")
    assert result.exit_code == 2
    # cli.py raises a fresh ``UsageError`` ("No such command: 'X'. ...") when it
    # has a suggestion or recipe-hint to offer; otherwise the original Click
    # error ("No such command 'X'.") bubbles up — either phrasing is valid for
    # the zero-suggestion path.
    assert "xxxxxxxxxxxxx" in result.output
    assert "No such command" in result.output
    # No close-match suggestion list — counts as zero ``Did you mean ` `roam`` pairs.
    # (Recipe-hint path uses ``Try `roam ask "..."``` phrasing, which is fine.)
    assert "Did you mean `roam" not in result.output
