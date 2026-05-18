"""W420 — ``roam surface`` command-count headline is plugin-loading-invariant.

Why this regression test exists
--------------------------------

``src/roam/commands/cmd_surface.py::_build_surface()`` historically read
``roam.cli._COMMANDS`` at runtime to compute the ``command_count`` /
``canonical_count`` / ``category_count`` headlines. Plugin discovery
(``_ensure_plugin_commands_loaded`` at ``cli.py:678``) mutates that dict
in-place the first time the Click group is walked. Result: the headline
bounced between 241 (no plugins loaded yet) and 242 (plugin discovery
fired in the same process) depending on which sibling Click invocation
ran first. Under pytest-xdist's load-balanced workers this surfaced as
flaky CI.

The W420 fix switches ``_build_surface()`` to read the AST source of
truth via :func:`roam.surface_counts.cli_commands`, matching the W1290
discipline already applied to ``mcp_tool_count``. The AST source is
env-independent and reflects exactly what ships with
``pip install roam-code`` — plugin commands surface separately via
``roam plugins list``.

This test pins the W420 contract: invoking the surface command before
and after a Click action that DOES trigger plugin discovery must
produce identical ``command_count`` / ``canonical_count`` /
``category_count`` headlines.

Parametrized trigger coverage
-----------------------------

``_ensure_plugin_commands_loaded`` is called from four LazyGroup sites
in ``src/roam/cli.py``:

* ``LazyGroup.list_commands`` (line 783) — fired by ``--help-all``,
  ``--help``, and tab-completion.
* ``LazyGroup.get_command`` unknown-command fallback (line 795) — fired
  when the requested subcommand isn't in the static ``_COMMANDS`` dict.
* ``LazyGroup.resolve_command`` typo-recovery path (line 847) — fired
  by the did-you-mean machinery on a UsageError.
* ``LazyGroup.format_help`` (line 915) — fired by the short ``--help``
  panel.

The parametrize matrix below exercises each of those entry points so a
future refactor that adds a NEW trigger but bypasses ``--help-all``
cannot silently slip a plugin-loading regression past this guard.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests._helpers.repo_root import repo_root  # noqa: F401 (W588 lint discipline)


def _surface_summary(runner: CliRunner) -> dict:
    """Run ``roam --json surface`` and return the parsed ``summary`` block."""
    result = runner.invoke(cli, ["--json", "surface"])
    assert result.exit_code == 0, f"surface exit={result.exit_code}\n{result.output}"
    out = result.output
    # ``--json`` envelopes are emitted as a single JSON object on stdout;
    # locate the first '{' to skip any pre-envelope warnings that Click
    # may have routed through stdout.
    idx = out.find("{")
    assert idx >= 0, f"no JSON object in output:\n{out!r}"
    data = json.loads(out[idx:])
    assert isinstance(data.get("summary"), dict), f"missing summary block: {data!r}"
    return data["summary"]


# Each row exercises a distinct ``_ensure_plugin_commands_loaded`` entry
# point. The ``id`` keeps the parametrize report readable.
_PLUGIN_TRIGGERS = [
    pytest.param(["--help-all"], id="help_all__list_commands"),
    pytest.param(["--help"], id="help__format_help"),
    pytest.param(
        ["xyz-totally-bogus-command-name-2026"],
        id="unknown_command__resolve_command",
    ),
    pytest.param(["plugins", "list"], id="plugins_list__direct"),
]


@pytest.mark.parametrize("trigger_args", _PLUGIN_TRIGGERS)
def test_surface_command_count_unaffected_by_plugin_trigger(
    trigger_args: list[str],
) -> None:
    """W420: the ``command_count`` headline must be plugin-loading-invariant
    across every documented plugin-discovery trigger.

    Capture the surface headlines, fire the trigger (which walks the
    LazyGroup machinery and gives ``_ensure_plugin_commands_loaded`` a
    chance to mutate ``_COMMANDS``), then capture again. The two
    headline triples must be identical — the AST source of truth does
    not move when plugins are registered into the runtime dict.

    Exit codes from the trigger invocation are intentionally ignored;
    ``xyz-totally-bogus-command-name-2026`` is expected to return
    non-zero, and that's fine — we only care that the SURFACE call on
    either side of the trigger reports the same headline.
    """
    runner = CliRunner()

    before = _surface_summary(runner)

    # Fire the trigger. We don't assert on its output or exit code —
    # only that running it does not destabilise the surface count.
    runner.invoke(cli, trigger_args)

    after = _surface_summary(runner)

    for key in ("command_count", "canonical_count", "category_count"):
        assert before[key] == after[key], (
            f"W420: surface summary {key!r} drifted across plugin-loading "
            f"boundary under trigger {trigger_args!r}: "
            f"before={before[key]} after={after[key]}. The headline must "
            f"be sourced from the AST-parsed ``_COMMANDS`` dict (via "
            f"``roam.surface_counts.cli_commands``), NOT from the runtime "
            f"``roam.cli._COMMANDS`` dict which plugin discovery mutates "
            f"in-place."
        )


def test_surface_command_count_unaffected_by_plugin_loading() -> None:
    """W420 (original case): ``--help-all`` trigger, preserved as a named
    test so existing CI selectors / dashboards keyed on this name keep
    working. The parametrized variant above is the canonical pin.
    """
    runner = CliRunner()

    before = _surface_summary(runner)

    help_all = runner.invoke(cli, ["--help-all"])
    assert help_all.exit_code == 0, f"--help-all exit={help_all.exit_code}\n{help_all.output}"

    after = _surface_summary(runner)

    for key in ("command_count", "canonical_count", "category_count"):
        assert before[key] == after[key], (
            f"W420: surface summary {key!r} drifted across plugin-loading "
            f"boundary: before={before[key]} after={after[key]}. The "
            f"headline must be sourced from the AST-parsed ``_COMMANDS`` "
            f"dict (via ``roam.surface_counts.cli_commands``), NOT from "
            f"the runtime ``roam.cli._COMMANDS`` dict which plugin "
            f"discovery mutates in-place."
        )
