"""Keep website fallback typography, named controls and Windows export usable."""

from __future__ import annotations

import re

import pytest

from scripts import stage_site
from tests import test_site_staging as staging_tests
from tests._helpers.repo_root import repo_root

SITE = repo_root() / stage_site.SITE


def _page_text(name):
    from tests.test_homepage_contract import HomepageParser, normalized

    return normalized(HomepageParser((SITE / name).read_text(encoding="utf-8")).root.text())


def test_governance_distinguishes_coordination_from_human_approval():
    text = _page_text("governance.html")
    assert "lease record names the human approver" not in text
    assert "Re-runs are blocked until a new lease is claimed" not in text
    assert "a lease is not human approval" in text


def test_governance_attaches_the_network_boundary():
    text = _page_text("governance.html")
    assert "nothing leaves the local environment" not in text
    assert "Connected agents" in text and "online features" in text


def test_accessibility_does_not_promote_historical_checks_to_current_conformance():
    text = _page_text("accessibility.html")
    assert "Current conformance" not in text
    assert "all decorative" not in text
    assert "screen readers announce them correctly" not in text
    assert "earlier seven-page site" in text and "informative" in text
    assert "not a conformance certification" in text


def test_no_cookies_inventory_includes_first_party_atlas():
    text = _page_text("no-cookies.html")
    assert "atlas.mjs" in text and "atlas-model.mjs" in text and "atlas-data.json" in text


@pytest.mark.parametrize("name", ["docs/integration-tutorials.html", "docs/mcp-usage.html"])
def test_reference_links_do_not_promise_unpublished_flag_and_envelope_catalogues(name):
    text = _page_text(name)
    assert "every command, every flag, every JSON envelope" not in text
    assert "command index" in text and "--help" in text


def test_atlas_maintenance_describes_emitted_unresolved_count():
    text = (repo_root() / "docs/website-maintenance.md").read_text(encoding="utf-8")
    assert "unresolved local import locations" not in text
    assert "unresolved local import count" in text


@pytest.fixture
def repo(tmp_path):
    return staging_tests.repo.__wrapped__(tmp_path)


def test_shared_code_font_keeps_a_generic_monospace_fallback():
    css = (SITE / "landing.css").read_text(encoding="utf-8")
    rule = re.search(r"code,\s*pre,\s*kbd,\s*samp\s*\{([^}]+)\}", css)
    assert rule and "font-family: var(--font-mono)" in rule.group(1)
    assert re.search(r"--font-mono:[^;]*,\s*monospace\s*;", css)


def test_map_view_controls_have_a_named_group():
    from tests.test_homepage_contract import HomepageParser

    page = HomepageParser((SITE / "explore.html").read_text(encoding="utf-8"))
    # Parse the actual markup; the same label must name a semantic group.
    groups = [node for node in page.elements if "atlas-modes" in node.attrs.get("class", "").split()]
    assert len(groups) == 1
    assert groups[0].attrs.get("role") == "group"
    assert groups[0].attrs.get("aria-label") == "Map view"


def test_forced_colors_can_override_inline_connection_dimming():
    # Source guard complements browser checks with real forced-colors emulation.
    css = (SITE / "atlas.css").read_text(encoding="utf-8")
    forced = css.split("@media (forced-colors:active)", 1)[1]
    inactive = re.search(r"(?m)^\s*\.atlas-edge\s*\{([^}]+)\}", forced)
    active = re.search(r"\.atlas-edge\.is-active\s*\{([^}]+)\}", forced)
    assert inactive and "opacity:.55!important" in inactive.group(1)
    assert "stroke-dasharray:3 4" in inactive.group(1)
    assert active and "opacity:1!important" in active.group(1)
    assert "stroke-dasharray:none" in active.group(1)


def test_setup_directory_does_not_claim_a_stale_platform_count():
    from tests.test_homepage_contract import HomepageParser, normalized

    page = HomepageParser((SITE / "setup.html").read_text(encoding="utf-8"))
    assert any(normalized(node.text()) == "All platforms" for node in page.root.find("code"))
    assert not re.search(r"All\s+\d+\s+platforms", page.root.text())


def test_sitemap_does_not_publish_unmaintained_modification_dates():
    import xml.etree.ElementTree as ET

    sitemap = ET.fromstring((SITE / "sitemap.xml").read_text(encoding="utf-8"))
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    assert len(sitemap.findall("s:url", ns)) >= 29
    # Dates are optional; restore them only with a maintained per-page source.
    assert not sitemap.findall("s:url/s:lastmod", ns)


def test_confirmation_page_defers_start_dates_to_the_agreed_schedule():
    from tests.test_homepage_contract import HomepageParser, normalized

    text = normalized(HomepageParser((SITE / "thank-you.html").read_text(encoding="utf-8")).root.text())
    assert "Within 24 hours" not in text
    assert "day after countersignature" not in text
    assert "agreed kickoff date" in text
    assert "5 business days" in text and "10 business days" in text


def test_readme_action_and_package_example_use_the_same_published_version():
    text = (repo_root() / "README.md").read_text(encoding="utf-8")
    example = re.search(r"uses: Cranot/roam-code@v([\d.]+)\s+with:\s+version: '([\d.]+)'", text)
    assert example, "The active README action example was not inspected"
    assert example.group(1) == example.group(2)


@pytest.mark.parametrize("autocrlf", ["true", "input", "false"])
def test_committed_export_under_windows_line_ending_settings(repo, autocrlf):
    # Git conversion is the boundary under test. This fixture owns a temporary
    # local repository; it does not change host config or contact a publisher.
    root, sha = repo
    staging_tests.git(root, "config", "core.autocrlf", autocrlf)
    public = stage_site.stage(root, sha)
    blob = staging_tests.git(root, "show", f"{sha}:{stage_site.SITE}/index.html")
    assert (public / "index.html").read_bytes() == blob
    assert staging_tests.git(root, "config", "core.autocrlf").decode().strip() == autocrlf
