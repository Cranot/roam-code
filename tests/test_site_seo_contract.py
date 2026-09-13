"""Source SEO contracts; these do not establish indexing or rich-result display."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import pytest

from tests.test_homepage_contract import SITE, HomepageParser


def _schema_nodes(value):
    """Read semantic entities from single nodes, arrays, and nested graphs."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _schema_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _schema_nodes(child)


def _entities(page, schema_type):
    for script in page.root.find("script"):
        if script.attrs.get("type") != "application/ld+json":
            continue
        for node in _schema_nodes(json.loads(script.text())):
            types = node.get("@type", [])
            if schema_type in ([types] if isinstance(types, str) else types):
                yield node


@pytest.fixture(scope="module")
def site_indexing_intent():
    """Derive indexing intent from actual head tags, not arbitrary body text."""
    paths = sorted(SITE.rglob("*.html"))
    assert len(paths) >= 31, "A reduced HTML corpus must not silently clear the sitemap"
    pages = {}
    for path in paths:
        page = HomepageParser(path.read_text(encoding="utf-8"))
        head = next(page.root.find("head"))
        canonicals = [
            node.attrs.get("href")
            for node in head.find("link")
            if "canonical" in node.attrs.get("rel", "").lower().split()
        ]
        assert len(canonicals) == 1 and canonicals[0], path
        canonical = canonicals[0]
        parsed = urlparse(canonical)
        assert (parsed.scheme, parsed.netloc) == ("https", "roam-code.com"), path
        assert not parsed.query and not parsed.fragment, path
        assert canonical not in pages, f"Duplicate canonical URL: {canonical}"
        directives = {
            directive
            for node in head.find("meta")
            if node.attrs.get("name", "").lower() in {"robots", "googlebot"}
            for directive in re.split(r"[,\s]+", node.attrs.get("content", "").lower())
        }
        pages[canonical] = not directives.intersection({"noindex", "none"})
    return pages


@pytest.fixture(scope="module")
def sitemap_urls():
    sitemap = ET.fromstring((SITE / "sitemap.xml").read_text(encoding="utf-8"))
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [node.text for node in sitemap.findall("s:url/s:loc", namespace)]
    assert urls and all(urls), "The sitemap must contain nonempty locations"
    assert len(urls) == len(set(urls)), "The sitemap must not duplicate URLs"
    return set(urls)


def test_sitemap_exactly_matches_source_indexable_canonical_pages(site_indexing_intent, sitemap_urls):
    indexable = {url for url, is_indexable in site_indexing_intent.items() if is_indexable}
    assert len(indexable) >= 29, "Indexing exclusions must not hide a reduced page corpus"
    assert sitemap_urls == indexable, {
        "missing": sorted(indexable - sitemap_urls),
        "unexpected": sorted(sitemap_urls - indexable),
    }


def test_sitemap_excludes_pages_with_noindex_directives(site_indexing_intent, sitemap_urls):
    excluded = {url for url, is_indexable in site_indexing_intent.items() if not is_indexable}
    assert {"https://roam-code.com/thank-you", "https://roam-code.com/404"} <= excluded
    assert not sitemap_urls.intersection(excluded), "A sitemap must not advertise noindex pages"


def test_changelog_omits_modification_date_without_a_maintained_owner():
    page = HomepageParser((SITE / "changelog.html").read_text(encoding="utf-8"))
    entities = list(_entities(page, "WebPage"))
    assert len(entities) == 1
    assert entities[0]["url"] == "https://roam-code.com/changelog"
    # Body regeneration does not own a page-modification date. Restore this
    # optional field only alongside a maintained date source and its contract.
    assert "dateModified" not in entities[0], "An unmaintained date invents page freshness"


def test_homepage_declares_one_consistent_website_name():
    page = HomepageParser((SITE / "index.html").read_text(encoding="utf-8"))
    websites = list(_entities(page, "WebSite"))
    assert len(websites) == 1, "Declare one domain-homepage WebSite entity"
    website = websites[0]
    assert website["name"] == "Roam"
    alternatives = website.get("alternateName", [])
    assert "roam-code" in ([alternatives] if isinstance(alternatives, str) else alternatives)
    assert website["url"] == "https://roam-code.com/"
    assert website["@id"] == website["url"] + "#website"
    head = next(page.root.find("head"))
    site_names = [
        node.attrs.get("content") for node in head.find("meta") if node.attrs.get("property") == "og:site_name"
    ]
    assert site_names == [website["name"]]
