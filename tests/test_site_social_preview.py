"""Bind social metadata to a reviewed bitmap; hash matching is not OCR review."""

from __future__ import annotations

import hashlib
import json
import struct
from urllib.parse import urlsplit

import pytest

from tests._helpers.repo_root import repo_root
from tests.test_homepage_contract import HomepageParser, normalized

SITE = repo_root() / "templates/distribution/landing-page"


def _assert_image_url(manifest):
    assert manifest["file"] == "og-code-analysis.png"
    parsed = urlsplit(manifest["url"])
    assert (parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment) == (
        "https",
        "roam-code.com",
        "/" + manifest["file"],
        "",
        "",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://roam-code.com/missing.png",
        "https://example.com/og-code-analysis.png",
        "http://roam-code.com/og-code-analysis.png",
        "https://roam-code.com/og-code-analysis.png?old=1",
    ],
)
def test_review_record_rejects_urls_that_do_not_identify_its_local_image(url):
    with pytest.raises(AssertionError):
        _assert_image_url({"file": "og-code-analysis.png", "url": url})


def test_shared_preview_matches_reviewed_image_and_visible_homepage():
    manifest = json.loads((SITE / "data/social-preview.json").read_text(encoding="utf-8"))
    _assert_image_url(manifest)
    data = (SITE / manifest["file"]).read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", data[16:24]) == (manifest["width"], manifest["height"])
    assert hashlib.sha256(data).hexdigest() == manifest["sha256"]
    assert len(data) < 5_000_000
    page = HomepageParser((SITE / "index.html").read_text(encoding="utf-8"))
    assert normalized(next(page.root.find("h1")).text()) == manifest["headline"]
    assert manifest["alt"] == f"Roam — {manifest['headline']}"


def test_all_public_pages_use_the_same_current_social_image():
    manifest = json.loads((SITE / "data/social-preview.json").read_text(encoding="utf-8"))
    pages = sorted(SITE.rglob("*.html"))
    assert len(pages) == 32
    for path in pages:
        page = HomepageParser(path.read_text(encoding="utf-8"))
        metadata = {}
        for node in next(page.root.find("head")).find("meta"):
            key = node.attrs.get("property", node.attrs.get("name"))
            if key in metadata:
                assert False, (path, "duplicate metadata key", key)
            metadata[key] = node.attrs.get("content")
        assert metadata["og:image"] == manifest["url"], path
        assert metadata["twitter:image"] == manifest["url"], path
        assert metadata["og:image:alt"] == manifest["alt"], path
        assert metadata["twitter:image:alt"] == manifest["alt"], path
        assert metadata["og:image:width"] == str(manifest["width"]), path
        assert metadata["og:image:height"] == str(manifest["height"]), path


def test_legacy_social_image_url_redirects_to_current_asset():
    manifest = json.loads((SITE / "data/social-preview.json").read_text(encoding="utf-8"))
    lines = (SITE / "_redirects").read_text(encoding="utf-8").splitlines()
    rules = [line.split() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    assert ["/og.png", f"/{manifest['file']}", "301"] in rules
    assert not (SITE / "og.png").exists(), "Keep one current bitmap, not a stale public sibling"


def test_discovery_cards_name_the_current_image_and_its_actual_dimensions():
    manifest = json.loads((SITE / "data/social-preview.json").read_text(encoding="utf-8"))
    cards = [
        repo_root() / "src/roam/mcp-server-card.json",
        SITE / ".well-known/mcp-server-card.json",
        SITE / ".well-known/mcp/server-card.json",
        SITE / ".well-known/mcp-server-card",
    ]
    for path in cards:
        card = json.loads(path.read_text(encoding="utf-8"))
        images = [icon for icon in card["icons"] if icon["type"] == "image/png"]
        assert images == [
            {
                "src": manifest["url"],
                "sizes": f"{manifest['width']}x{manifest['height']}",
                "type": "image/png",
            }
        ], path
