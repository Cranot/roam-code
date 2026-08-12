"""W1462 — `-h` must be help on every command, and must never be claimed for anything else.

Issue #100: an external user's agents failed on the first or second roam call.
Measured root cause on 2026-08-12: `roam <cmd> -h` exited 2 with
``Error: No such option: -h`` on all 285 top-level commands — every command in a
CLI whose stated primary caller is an agent.

The fix is one setting on the root group (``CLI_CONTEXT_SETTINGS`` in
``roam/cli.py``), inherited by every subcommand through Click's Context chain.
That inheritance is exactly what makes it fragile in one direction: a future
command that declares its own ``-h`` would SHADOW the alias and silently change
meaning for that command only. The shadowing test below is the guard for that,
and it is written to fail loudly rather than skip.
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from roam.cli import CLI_CONTEXT_SETTINGS
from roam.cli import cli as root_cli


def _walk(cmd: click.Command, ctx: click.Context, path: list[str], out: list[tuple[str, click.Command]]) -> None:
    if path:
        out.append((" ".join(path), cmd))
    if isinstance(cmd, click.Group):
        for name in cmd.list_commands(ctx):
            sub = cmd.get_command(ctx, name)
            if sub is None:
                continue
            _walk(sub, click.Context(sub, info_name=name, parent=ctx), [*path, name], out)


def _all_commands() -> list[tuple[str, click.Command]]:
    out: list[tuple[str, click.Command]] = []
    _walk(root_cli, click.Context(root_cli, info_name="roam"), [], out)
    return out


ALL_COMMANDS = _all_commands()


def test_command_surface_is_not_empty():
    """A registry that failed to load would make every other test here vacuous."""
    top = [name for name, _ in ALL_COMMANDS if " " not in name]
    assert len(top) >= 285, f"expected the full top-level surface, got {len(top)}"


def test_root_group_declares_the_alias():
    assert CLI_CONTEXT_SETTINGS["help_option_names"] == ["-h", "--help"]
    assert root_cli.context_settings.get("help_option_names") == ["-h", "--help"]


def dash_h_offenders(commands: list[tuple[str, click.Command]]) -> list[str]:
    """Commands whose own parameters claim ``-h``, shadowing the help alias."""
    offenders = []
    for name, cmd in commands:
        for param in cmd.params:
            opts = tuple(param.opts or ()) + tuple(param.secondary_opts or ())
            if "-h" in opts:
                offenders.append(f"{name} (--{param.name} declares -h)")
    return offenders


def test_no_command_claims_dash_h_for_itself():
    """Shadowing guard.

    ``-h`` is resolved from the Context, so a parameter that declares ``-h``
    wins over the inherited help alias for that one command — an agent typing
    ``-h`` would get a wrong answer with exit 0 instead of help. If a command
    genuinely needs ``-h``, that is a deliberate decision: exempt it here with a
    comment saying why, do not delete this test.
    """
    offenders = dash_h_offenders(ALL_COMMANDS)
    assert not offenders, "these commands shadow the -h help alias:\n  " + "\n  ".join(offenders)


def test_shadowing_detector_catches_a_planted_conflict():
    """NEGATIVE CONTROL for the test above.

    ``test_no_command_claims_dash_h_for_itself`` passes on an empty list, which
    is also what it would report if the detector were broken. Plant a real
    conflicting ``-h`` on a real roam command and require the detector to name
    it, so a green result means "measured clean", not "measured nothing".
    """
    ctx = click.Context(root_cli, info_name="roam")
    victim = root_cli.get_command(ctx, "health")
    assert victim is not None

    planted = click.Option(["-h", "--host"], help="planted conflict — not a real roam flag")
    victim.params.append(planted)
    # Click caches the resolved help option on the Command the first time it is
    # asked for one (``Command._help_option``, added in 8.1.8). Clear it so the
    # plant is resolved from scratch, and clear it again on the way out so the
    # mutation cannot leak into other tests in the same session.
    victim._help_option = None
    try:
        offenders = dash_h_offenders([("health", victim)])
        assert offenders == ["health (--host declares -h)"], offenders

        # The shadowing is real, not theoretical. ``get_help_option_names``
        # subtracts every opt the command already declares, so help silently
        # loses ``-h`` on this one command and the planted option takes it.
        result = CliRunner().invoke(root_cli, ["health", "-h"], catch_exceptions=True)
        assert result.exit_code != 0, "planted -h should have stolen the help option"
        assert "requires an argument" in result.output.lower(), result.output
        assert victim._help_option.opts == ["--help"], victim._help_option.opts
    finally:
        victim.params.remove(planted)
        victim._help_option = None

    assert dash_h_offenders([("health", victim)]) == []
    restored = CliRunner().invoke(root_cli, ["health", "-h"], catch_exceptions=True)
    assert restored.exit_code == 0 and "Usage:" in restored.output


def test_no_command_swallows_unknown_options():
    """``ignore_unknown_options`` would route ``-h`` to the command as a value.

    None do today. If one is added, ``-h`` stops being help there without any
    parameter declaring it, so the shadowing test above would not catch it.

    Deliberately NOT also asserting ``allow_extra_args``: every ``click.Group``
    sets it True so it can forward argv to a subcommand, and a leaf command with
    it set still rejects unknown *options*. Asserting it flagged all 23 groups
    while all 23 answer ``-h`` correctly — a guard that fires on non-defects
    gets deleted, which is how a real one goes missing.
    """
    offenders = [name for name, cmd in ALL_COMMANDS if cmd.context_settings.get("ignore_unknown_options")]
    assert not offenders, f"these commands would swallow -h instead of showing help: {offenders}"


@pytest.mark.parametrize("name", [n for n, _ in ALL_COMMANDS])
def test_dash_h_prints_help_and_exits_zero(name):
    """Before this fix every one of these exited 2 with 'No such option: -h'."""
    result = CliRunner().invoke(root_cli, [*name.split(" "), "-h"], catch_exceptions=True)
    assert result.exit_code == 0, f"roam {name} -h exited {result.exit_code}:\n{result.output}"
    assert "Usage:" in result.output, f"roam {name} -h printed no usage block:\n{result.output}"


def test_dash_h_does_not_run_the_command_body():
    """Help is eager: it must short-circuit BEFORE required-positional validation.

    ``roam symbol`` needs a SYMBOL argument. If ``-h`` were parsed after
    argument validation, agents would still get exit 2 on exactly the commands
    where they most need the help text.
    """
    result = CliRunner().invoke(root_cli, ["symbol", "-h"], catch_exceptions=True)
    assert result.exit_code == 0, result.output
    assert "Missing" not in result.output, result.output
