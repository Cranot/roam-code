"""Tests for the ``--detail`` global flag documentation.

W19.2 flagged that ``roam --detail --help`` did NOT error, yet ``--detail``
appeared nowhere in ``--help`` or ``--help-all`` output. The flag is real
(set on the ``cli`` group in :mod:`roam.cli`, stored as ``ctx.obj["detail"]``,
consumed by ``cmd_clusters``, ``cmd_deps``, ``cmd_dead``, ``cmd_health``,
``cmd_layers``, ``cmd_hotspots``, the api/MCP wrappers, etc.). The fix is
to surface it in the custom ``format_help`` panel and the ``--help-all``
callback, alongside the other previously-undocumented global flags
(``--json``, ``--compact``, ``--agent``, ``--sarif``, ``--budget``,
``--include-excluded``, ``--override-mode``).

These tests pin that documentation in place so a future re-write of the
help banner can't silently drop ``--detail`` again.
"""

from __future__ import annotations

from click.testing import CliRunner

from roam.cli import cli


def test_detail_flag_documented_in_help():
    """``roam --help`` must mention ``--detail`` so the flag is discoverable."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--detail" in result.output, (
        "--detail must appear in `roam --help` output; "
        "W19.2 flagged this as an undocumented but accepted flag."
    )


def test_detail_flag_documented_in_help_all():
    """``roam --help-all`` must also mention ``--detail`` (parity with --help)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help-all"])
    assert result.exit_code == 0, result.output
    assert "--detail" in result.output, "--detail must appear in `roam --help-all`"


def test_detail_flag_help_text_present():
    """The help text describing what ``--detail`` does must appear, not just
    the flag name. Otherwise the entry is a bare token with no semantics."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, result.output
    # The documented help string for --detail mentions "summary" or "detailed".
    lower = result.output.lower()
    assert "detail" in lower
    # Look for either "detailed output" or "compact summary" — the actual
    # phrasing in the panel — to ensure we documented WHAT the flag does.
    assert "detailed output" in lower or "compact summary" in lower, (
        "--detail's help text should describe what it does (full vs summary), "
        "not just list the flag name."
    )


def test_detail_flag_still_accepted_without_error():
    """Documenting the flag must not break invocation. Regression guard:
    ``roam --detail --help`` must still exit 0 with no `No such option` error.
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["--detail", "--help"])
    assert result.exit_code == 0
    assert "no such option" not in (result.output or "").lower()


def test_detail_flag_combined_with_json_still_accepted():
    """Combining --detail with --json must continue to parse cleanly."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "--detail", "--help"])
    assert result.exit_code == 0
    assert "no such option" not in (result.output or "").lower()
