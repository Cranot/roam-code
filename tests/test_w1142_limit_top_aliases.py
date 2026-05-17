"""W1142 -- Pattern-3b numeric-truncation flag aliasing.

W1142 closes the numeric-output-cap cluster in the Pattern-3b
vocabulary-mismatch family (precedent: W332 closed the symbol/target/
file/path cluster via wrapper-side ``_PARAM_ALIASES``).

The canonical name is ``--limit`` (35+ command sites). ``--top`` is
the divergent sibling expressing the same concept (15+ sites). Pattern-3b
silent-fail: an agent that passes ``--top 5`` to a ``--limit`` command
(or ``--limit 5`` to a ``--top`` command) gets either:

  - Click usage-error exit (best-case visible failure), or worse
  - Silent default-truncation when the unrecognized flag is somehow
    consumed elsewhere.

W1142 lands dual-flag aliases on 8 sites: 5 DIVERGENT (``--top`` primary,
adds ``--limit`` alias) + 3 MISSING-ALIAS (``--limit`` primary, adds
``--top`` alias). The aliases share a single ``dest`` so downstream
handlers see one canonical variable -- no semantic change, purely
agent-vocabulary tolerance.

These tests pin the click-option parameter structure rather than running
full commands, because:

  - Many target commands require an indexed project (heavy fixture).
  - The contract under test is option-binding, not output semantics.
  - The option-parser layer is exactly where Pattern-3b silent-fail
    would surface, so testing here catches the bug closest to the cause.
"""

from __future__ import annotations

import click
import pytest

from roam.commands import cmd_test_impact as _cmd_test_impact_mod
from roam.commands.cmd_agent_score import agent_score_cmd
from roam.commands.cmd_clones import clones
from roam.commands.cmd_debt import debt
from roam.commands.cmd_recommend import recommend
from roam.commands.cmd_runs import runs_list
from roam.commands.cmd_search_semantic import search_semantic
from roam.commands.cmd_supply_chain import supply_chain

# Pytest collects any module-level name starting with ``test_`` and warns
# when it cannot call it. The ``test_impact`` click.Command is not callable
# as a test function -- bind it under an underscore-prefixed name to keep
# the collector quiet (private names are not collected).
_test_impact_cmd = _cmd_test_impact_mod.test_impact


def _find_option(cmd: click.Command, *flag_names: str) -> click.Option | None:
    """Return the click.Option that registers ALL listed flag names, or None."""
    wanted = set(flag_names)
    for param in cmd.params:
        if isinstance(param, click.Option):
            decls = set(param.opts) | set(param.secondary_opts)
            if wanted.issubset(decls):
                return param
    return None


# W1142 alias matrix: (command_obj, primary_flag, alias_flag, dest_name)
# DIVERGENT (--top primary, --limit alias added):
#   supply_chain, agent_score, runs_list, clones, search_semantic
# MISSING-ALIAS (--limit primary, --top alias added):
#   debt, recommend, test_impact
_W1142_ALIAS_MATRIX = [
    (supply_chain, "--top", "--limit", "top"),
    (agent_score_cmd, "--top", "--limit", "top"),
    (runs_list, "--top", "--limit", "top"),
    (clones, "--top", "--limit", "top"),
    (search_semantic, "--top", "--limit", "top_k"),
    (debt, "--limit", "--top", "limit"),
    (recommend, "--limit", "--top", "limit"),
    (_test_impact_cmd, "--limit", "--top", "limit"),
]


@pytest.mark.parametrize(
    "command, primary, alias, dest",
    _W1142_ALIAS_MATRIX,
    ids=[c.name for c, *_ in _W1142_ALIAS_MATRIX],
)
def test_w1142_dual_flag_alias_registered(
    command: click.Command,
    primary: str,
    alias: str,
    dest: str,
) -> None:
    """Both --limit AND --top must bind to the same click.Option / dest.

    Pattern-3b regression: removing the alias would silently drop one
    flag form. This test fails closed when either flag is unwired.
    """
    option = _find_option(command, primary, alias)
    assert option is not None, (
        f"{command.name}: expected one click.Option declaring both "
        f"{primary!r} and {alias!r}, got params: "
        f"{[(p.name, p.opts) for p in command.params if isinstance(p, click.Option)]}"
    )
    assert option.name == dest, f"{command.name}: dual-flag option must keep dest={dest!r}, got dest={option.name!r}"


@pytest.mark.parametrize(
    "command, primary, alias, dest",
    _W1142_ALIAS_MATRIX,
    ids=[c.name for c, *_ in _W1142_ALIAS_MATRIX],
)
def test_w1142_alias_in_help_text(
    command: click.Command,
    primary: str,
    alias: str,
    dest: str,
) -> None:
    """``roam <cmd> --help`` must surface BOTH flag names.

    Help-text discoverability is the second layer of the W1142 guarantee:
    even if an agent never tries the alias blindly, the help output
    must announce it.
    """
    runner = click.testing.CliRunner()
    result = runner.invoke(command, ["--help"])
    assert result.exit_code == 0, result.output
    assert primary in result.output, f"{command.name} --help missing primary flag {primary!r}"
    assert alias in result.output, f"{command.name} --help missing alias flag {alias!r}"


@pytest.mark.parametrize(
    "command, primary, alias, dest",
    _W1142_ALIAS_MATRIX,
    ids=[c.name for c, *_ in _W1142_ALIAS_MATRIX],
)
def test_w1142_primary_and_alias_parse_identically(
    command: click.Command,
    primary: str,
    alias: str,
    dest: str,
) -> None:
    """Parsing ``cmd <primary> N`` and ``cmd <alias> N`` must yield same dest value.

    This uses click's parser directly so we don't require an indexed
    project or live DB. ``make_context`` runs the parameter parsing
    layer in isolation, which is exactly the layer Pattern-3b targets.
    """
    # Pick a value distinct from the option's default so we can detect
    # silent-default fallback.
    option = _find_option(command, primary, alias)
    assert option is not None
    default = option.default
    test_value = (default + 7) if isinstance(default, int) else 7
    test_str = str(test_value)

    # Some commands require a positional argument (search_semantic,
    # recommend take a symbol/query). Provide a placeholder; option
    # parsing happens before the command body runs so the value doesn't
    # need to exist in any index.
    extras: list[str] = []
    for param in command.params:
        if isinstance(param, click.Argument) and param.required:
            extras.append("PLACEHOLDER")

    ctx_primary = command.make_context(command.name, [primary, test_str, *extras], resilient_parsing=True)
    ctx_alias = command.make_context(command.name, [alias, test_str, *extras], resilient_parsing=True)

    assert ctx_primary.params[dest] == test_value, (
        f"{command.name}: {primary} {test_str} did not bind dest "
        f"{dest!r} to {test_value} (got {ctx_primary.params[dest]!r})"
    )
    assert ctx_alias.params[dest] == test_value, (
        f"{command.name}: {alias} {test_str} did not bind dest "
        f"{dest!r} to {test_value} (got {ctx_alias.params[dest]!r}) -- "
        "Pattern-3b silent-fail regressed."
    )
    assert ctx_primary.params[dest] == ctx_alias.params[dest], (
        f"{command.name}: primary/alias produced divergent dest values"
    )
