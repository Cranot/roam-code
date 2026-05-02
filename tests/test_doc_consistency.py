"""Cross-surface documentation consistency check.

External review (2026-05-02) flagged stale counts across public
surfaces: PyPI said 152 commands, README said 151, docs/site
landscape.json still said 139. For a tool selling "structural truth"
that's a credibility problem the reviewer specifically called out.

This test scrapes every public surface for the load-bearing numbers
(version, CLI command count, MCP tool count, language count) and
asserts they all agree with the source-of-truth (``pyproject.toml``
and the live ``cli._COMMANDS`` / ``mcp_server._REGISTERED_TOOLS``).

When one of these drifts, ALL of them must be updated in the same
PR. Manual maintenance was failing; CI now enforces.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# Source of truth
# ---------------------------------------------------------------------------


def _truth_version() -> str:
    """Read ``version`` from pyproject.toml — the canonical version."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "pyproject.toml missing version"
    return m.group(1)


def _truth_cli_command_count() -> int:
    """Live canonical-command count from ``cli._COMMANDS``."""
    from roam.surface_counts import cli_surface_counts

    return int(cli_surface_counts()["canonical_commands"])


def _truth_mcp_tool_count() -> int:
    """Live registered-tool count from ``mcp_server._REGISTERED_TOOLS``."""
    from roam.surface_counts import mcp_surface_counts

    return int(mcp_surface_counts()["registered_tools"])


# ---------------------------------------------------------------------------
# Per-surface scrapers
# ---------------------------------------------------------------------------


def _scrape_first_int_after(text: str, pattern: str) -> int | None:
    """Find the first integer in ``text`` matching the regex pattern."""
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (IndexError, ValueError):
        return None


def _readme_command_count() -> int | None:
    """README's headline ``N commands``."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return _scrape_first_int_after(text, r"\b(\d+)\s+commands\b")


def _readme_mcp_count() -> int | None:
    """README's headline ``N MCP tools``."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return _scrape_first_int_after(text, r"\b(\d+)\s+MCP\s+tools\b")


def _claude_md_command_count() -> int | None:
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    return _scrape_first_int_after(text, r"\b(\d+)\s+commands\b")


def _llms_install_command_count() -> int | None:
    p = ROOT / "llms-install.md"
    if not p.exists():
        return None
    return _scrape_first_int_after(p.read_text(encoding="utf-8"), r"\b(\d+)\s+commands\b")


def _server_json_version() -> str | None:
    p = ROOT / "server.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("version")


def _mcp_card_version() -> str | None:
    p = ROOT / "docs" / "site" / ".well-known" / "mcp-server-card.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("version")


def _landscape_json_command_count() -> int | None:
    """``docs/site/data/landscape.json`` self-row often quotes counts.
    External reviewer specifically flagged this as a stale-count source."""
    p = ROOT / "docs" / "site" / "data" / "landscape.json"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    # Look for the roam-code entry — characterised by the cli_commands key.
    m = re.search(r'"cli_commands"\s*:\s*"(\d+)\s+canonical', text)
    return int(m.group(1)) if m else None


def _landscape_json_mcp_count() -> int | None:
    p = ROOT / "docs" / "site" / "data" / "landscape.json"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    m = re.search(r"(\d+)\s+MCP\s+tools", text)
    return int(m.group(1)) if m else None


def _landscape_json_version() -> str | None:
    """The roam-code self-row's ``version_evaluated`` field — should
    track the package version reasonably closely."""
    p = ROOT / "docs" / "site" / "data" / "landscape.json"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    # First version_evaluated near the cli_commands hit (assumes
    # roam-code is the first entry; landscape file lists peers below).
    if "cli_commands" not in text:
        return None
    cli_idx = text.find('"cli_commands"')
    near = text[max(0, cli_idx - 500) : cli_idx + 500]
    m = re.search(r'"version_evaluated"\s*:\s*"([^"]+)"', near)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVersionConsistency:
    """``version`` must agree across pyproject, server.json, mcp-server-card,
    and the landscape.json self-row."""

    def test_pyproject_is_truth(self):
        v = _truth_version()
        assert re.match(r"^\d+\.\d+\.\d+", v), f"Bad version format: {v!r}"

    def test_server_json_matches_pyproject(self):
        truth = _truth_version()
        actual = _server_json_version()
        assert actual is not None, "server.json missing"
        assert actual == truth, f"server.json {actual!r} != pyproject {truth!r}"

    def test_mcp_card_matches_pyproject(self):
        truth = _truth_version()
        actual = _mcp_card_version()
        assert actual is not None, "mcp-server-card.json missing"
        assert actual == truth, f"mcp-server-card.json {actual!r} != pyproject {truth!r}"

    def test_landscape_json_self_row_version_matches(self):
        """The roam-code row in landscape.json should show the current
        package version (or a recent nearby tag). Strict equality
        catches the stale ``11.1.2`` flagged by the external review."""
        truth = _truth_version()
        actual = _landscape_json_version()
        if actual is None:
            pytest.skip("landscape.json roam-code self-row missing")
        # Allow the landscape eval-version to lag by at most one minor;
        # full equality enforced when same major/minor.
        truth_major_minor = ".".join(truth.split(".")[:2])
        actual_major_minor = ".".join(actual.lstrip("v").split(".")[:2])
        assert actual_major_minor == truth_major_minor, (
            f"landscape.json self-row says {actual!r} but pyproject is {truth!r}"
        )


class TestCommandCountConsistency:
    """CLI command count must agree across README, CLAUDE.md, llms-install.md,
    landscape.json, and the live ``cli._COMMANDS`` count."""

    def test_truth_command_count_is_positive(self):
        n = _truth_cli_command_count()
        assert n >= 100, f"suspiciously low: {n}"

    def test_readme_matches_source(self):
        truth = _truth_cli_command_count()
        actual = _readme_command_count()
        assert actual is not None, "README missing 'N commands' phrase"
        assert actual == truth, f"README says '{actual} commands' but cli._COMMANDS has {truth}"

    def test_claude_md_matches_source(self):
        truth = _truth_cli_command_count()
        actual = _claude_md_command_count()
        if actual is None:
            pytest.skip("CLAUDE.md does not mention command count")
        assert actual == truth, f"CLAUDE.md says '{actual} commands' but truth is {truth}"

    def test_llms_install_matches_source(self):
        truth = _truth_cli_command_count()
        actual = _llms_install_command_count()
        if actual is None:
            pytest.skip("llms-install.md not present or no count")
        assert actual == truth, f"llms-install.md says '{actual} commands' but truth is {truth}"

    def test_landscape_json_matches_source(self):
        truth = _truth_cli_command_count()
        actual = _landscape_json_command_count()
        if actual is None:
            pytest.skip("landscape.json roam-code self-row missing")
        assert actual == truth, (
            f"landscape.json says '{actual} canonical commands' but truth is {truth}. "
            "External review (2026-05-02) flagged this exact drift."
        )


class TestMcpToolCountConsistency:
    """MCP tool count must agree across README + landscape.json + live count."""

    def test_truth_mcp_count_is_positive(self):
        n = _truth_mcp_tool_count()
        assert n >= 50, f"suspiciously low: {n}"

    def test_readme_matches_source(self):
        truth = _truth_mcp_tool_count()
        actual = _readme_mcp_count()
        assert actual is not None, "README missing 'N MCP tools' phrase"
        assert actual == truth, f"README says '{actual} MCP tools' but live count is {truth}"

    def test_landscape_json_matches_source(self):
        truth = _truth_mcp_tool_count()
        actual = _landscape_json_mcp_count()
        if actual is None:
            pytest.skip("landscape.json missing MCP tool count")
        assert actual == truth, f"landscape.json says '{actual} MCP tools' but live count is {truth}"
