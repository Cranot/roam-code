"""Guard known comparison/category and atlas identity regressions, not copy quality."""

from __future__ import annotations

import re

from tests._helpers.repo_root import repo_root

SITE = repo_root() / "templates/distribution/landing-page"


def _page(name: str) -> str:
    return (SITE / name).read_text(encoding="utf-8")


def test_comparison_does_not_require_exclusive_review_layers():
    html = _page("compare.html")
    assert "Roam covers two" not in html
    assert "A healthy setup runs all four" not in html
    assert "the bottom two" not in html
    assert "<code>2. Static</code>" not in html
    assert re.search(r"overlap", html, re.I)


def test_comparison_keeps_agent_access_and_category_clear():
    html = _page("compare.html")
    assert "cloud-ide" not in html.lower()
    assert "CLI fallback" not in html
    assert 'id="ide-agents"' in html  # Preserve inbound links.
    assert 'id="three-layers"' in html


def test_compare_opening_offers_free_setup_before_reference_material():
    header = re.search(r'<header class="hero">(.*?)</header>', _page("compare.html"), re.S).group(1)
    assert re.search(r'<a href="/setup" class="btn-primary">[^<]*free', header)
    assert "/setup#first-result" in header
    assert 'id="first-result"' in _page("setup.html")
    assert "definition and references" in _page("setup.html")


def test_atlas_opening_identifies_custom_website_view():
    header = re.search(r'<header class="atlas-heading">(.*?)</header>', _page("explore.html"), re.S).group(1)
    assert "custom website view" in header
    assert "Roam’s own Python" in header
    assert "roam map" in header


def test_dated_comparison_and_existing_terms_remain_explicit():
    html = _page("compare.html")
    assert 'datetime="2026-06-12"' in html
    assert "not a current buying guide" in html
    assert 'id="methodology"' in html
    assert "https://www.coderabbit.ai/pricing" in html
    assert "$2,500" in html and "$6,000" in html
    assert "proposed $99-$1,499/mo" in html
    assert "within 60 days" in html
    assert "not available to subscribe to" in html
