"""Cross-surface documentation consistency check.

The same load-bearing numbers (package version, CLI command count, MCP
tool count) appear across many surfaces — pyproject.toml, server.json,
the MCP server card, the README, the docs-site landscape entry — and
they have a habit of drifting out of sync because a release bump only
touches some of them.

This test scrapes every public surface for those numbers and asserts
they all agree with the source-of-truth (``pyproject.toml`` and the
live ``cli._COMMANDS`` / ``mcp_server._REGISTERED_TOOLS`` counters).
When one of them drifts, all of them must be updated in the same PR.
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
    """Live public-command count from ``cli._COMMANDS`` (counts aliases).

    Uses ``command_names`` (not ``canonical_commands``) because that's what
    a user sees when running ``roam --help`` and what the README headline
    advertises. Aliases like ``algo``/``math`` are real commands a user
    can invoke.
    """
    from roam.surface_counts import cli_surface_counts

    return int(cli_surface_counts()["command_names"])


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


# GitHub Pages was disabled on 2026-05-08; the canonical public
# mcp-server-card.json moved to the Cloudflare-served landing-page tree.
# The bundled wheel copy lives under ``src/roam/`` for ``roam mcp --card``.
_PUBLIC_MCP_CARD = ROOT / "templates" / "distribution" / "landing-page" / ".well-known" / "mcp-server-card.json"


def _mcp_card_version() -> str | None:
    p = _PUBLIC_MCP_CARD
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("version")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVersionConsistency:
    """``version`` must agree across pyproject, server.json, mcp-server-card,
    and the landscape.json self-row."""

    def test_pyproject_is_truth(self):
        v = _truth_version()
        # Accept both 2-segment (12.12) and 3-segment (12.12.0) forms.
        # The project switched to 2-segment versions in v12.11; the
        # release commit explicitly noted "skipping the third version
        # component going forward". Tests guard the *consistency*
        # across pyproject/server.json/mcp-card, not the segment count.
        assert re.match(r"^\d+\.\d+(\.\d+)?$", v), f"Bad version format: {v!r}"

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

    def test_bundled_card_matches_public_card(self):
        """The wheel ships ``src/roam/mcp-server-card.json`` so
        ``roam mcp --card`` works post-install without a source
        checkout. The Cloudflare-served public copy is canonical for
        the hosted ``/.well-known`` URL. Both must match byte-for-byte
        so they don't drift across releases.
        """
        bundled = ROOT / "src" / "roam" / "mcp-server-card.json"
        canonical = _PUBLIC_MCP_CARD
        if not bundled.exists() or not canonical.exists():
            pytest.skip("card files not both present")
        a = bundled.read_text(encoding="utf-8")
        b = canonical.read_text(encoding="utf-8")
        assert a == b, (
            "src/roam/mcp-server-card.json drifted from "
            f"{canonical.relative_to(ROOT).as_posix()} — "
            "re-copy after editing either file."
        )

    def test_card_tool_count_matches_live_count(self):
        """The card's ``capabilities.tools.total`` must match the live
        MCP tool count from ``surface_counts``."""
        try:
            from roam.surface_counts import collect_surface_counts
        except ImportError:
            pytest.skip("surface_counts unavailable")
        live = collect_surface_counts()
        card = json.loads(_PUBLIC_MCP_CARD.read_text(encoding="utf-8"))
        live_total = live["mcp"]["registered_tools"]
        live_core = live["mcp"]["core_tools"]
        card_total = card["capabilities"]["tools"]["total"]
        card_core = card["capabilities"]["tools"]["presets"]["core"]
        card_full = card["capabilities"]["tools"]["presets"]["full"]
        assert card_total == live_total, (
            f"card capabilities.tools.total = {card_total} but live MCP tool count = {live_total}"
        )
        assert card_core == live_core, (
            f"card capabilities.tools.presets.core = {card_core} but live core preset count = {live_core}"
        )
        assert card_full == live_total, (
            f"card capabilities.tools.presets.full = {card_full} but live total = {live_total}"
        )

    # ``test_landscape_json_self_row_version_matches`` removed
    # 2026-05-08: ``docs/site/data/landscape.json`` was deleted when GH
    # Pages was disabled. The roam-code self-row data still lives in
    # ``src/roam/competitor_site_data.py`` and the gitignored internal
    # tracker; neither needs a public-version-stamp consistency check.


class TestCommandCountConsistency:
    """CLI command count must agree across README, llms-install.md,
    and the live ``cli._COMMANDS`` count. (``landscape.json`` consistency
    check removed when GH Pages was disabled — file no longer exists.)"""

    def test_truth_command_count_is_positive(self):
        n = _truth_cli_command_count()
        assert n >= 100, f"suspiciously low: {n}"

    def test_readme_matches_source(self):
        truth = _truth_cli_command_count()
        actual = _readme_command_count()
        assert actual is not None, "README missing 'N commands' phrase"
        assert actual == truth, f"README says '{actual} commands' but cli._COMMANDS has {truth}"

    def test_llms_install_matches_source(self):
        truth = _truth_cli_command_count()
        actual = _llms_install_command_count()
        if actual is None:
            pytest.skip("llms-install.md not present or no count")
        assert actual == truth, f"llms-install.md says '{actual} commands' but truth is {truth}"


class TestMcpToolCountConsistency:
    """MCP tool count must agree across README + the live count.
    (``landscape.json`` consistency check removed; see above.)"""

    def test_truth_mcp_count_is_positive(self):
        n = _truth_mcp_tool_count()
        assert n >= 50, f"suspiciously low: {n}"

    def test_readme_matches_source(self):
        truth = _truth_mcp_tool_count()
        actual = _readme_mcp_count()
        assert actual is not None, "README missing 'N MCP tools' phrase"
        assert actual == truth, f"README says '{actual} MCP tools' but live count is {truth}"


# ---------------------------------------------------------------------------
# Internal-docs link audit
# ---------------------------------------------------------------------------

# Historically README and CHANGELOG linked out to ``docs/site/*.html``
# and a few sibling files. After GitHub Pages was disabled on
# 2026-05-08 and ``docs/site/`` was deleted, the docs live entirely at
# https://roam-code.com/docs/. New markdown link references to
# ``docs/site/*`` are now leaks pointing at deleted paths — catch them.

_DOC_LINK_RE = re.compile(r"\(docs/site/([^)#?]+\.(?:html|md))\)")


def _scrape_doc_links(text: str) -> set[str]:
    """All ``docs/site/*.{html,md}`` markdown links referenced by ``text``."""
    return {f"docs/site/{m}" for m in _DOC_LINK_RE.findall(text)}


class TestInternalDocLinks:
    """No markdown link in README or CHANGELOG should reference the
    deleted ``docs/site/*`` tree. New references are leaks."""

    def test_readme_does_not_link_to_deleted_docs_site(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        links = _scrape_doc_links(text)
        assert not links, (
            "README links to deleted docs/site/* paths "
            "(GH Pages was disabled 2026-05-08; canonical docs live at "
            f"https://roam-code.com/docs/): {sorted(links)}"
        )

    def test_changelog_does_not_link_to_deleted_docs_site(self):
        cl = ROOT / "CHANGELOG.md"
        if not cl.exists():
            pytest.skip("CHANGELOG.md missing")
        text = cl.read_text(encoding="utf-8")
        links = _scrape_doc_links(text)
        # NEW link references must be zero; historical entries that just
        # mention paths in prose aren't matched by the link regex.
        assert not links, (
            "CHANGELOG has markdown links to deleted docs/site/* paths "
            "(GH Pages was disabled 2026-05-08; canonical docs live at "
            f"https://roam-code.com/docs/): {sorted(links)}"
        )
