"""Offline contracts for downloadable identity and the labelled report excerpt."""

from __future__ import annotations

import hashlib
import re
import struct
from xml.etree import ElementTree as ET

import pytest

from tests._helpers.repo_root import repo_root

SITE = repo_root() / "templates/distribution/landing-page"
NAMES = ("roam-logo", "roam-logo-mono", "roam-logo-white", "roam-mark")
NS = "{http://www.w3.org/2000/svg}"


@pytest.mark.parametrize("name", NAMES)
def test_brand_assets_are_self_contained_and_png_exports_have_alpha(name):
    source = (SITE / f"brand/{name}.svg").read_text(encoding="utf-8")
    svg = ET.fromstring(source)
    assert svg.find(NS + "title") is not None
    assert svg.findall(".//" + NS + "circle")
    assert not re.search(r"<(?:text|script|image|foreignObject)\b|(?:href|onload)=|url\(", source)
    if name != "roam-mark":
        assert len(svg.findall(".//" + NS + "path")) == 5
        font_hash = hashlib.sha256((SITE / "fonts/space-grotesk-variable.woff2").read_bytes()).hexdigest()
        assert f"font-sha256: {font_hash}" in source
    data = (SITE / f"brand/{name}.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    width, height, depth, color = struct.unpack(">IIBB", data[16:26])
    assert width == (512 if name == "roam-mark" else 1600)
    assert depth == 8 and color == 6, "PNG must preserve RGBA transparency"
    _, _, vw, vh = map(float, svg.attrib["viewBox"].split())
    assert abs(height - width * vh / vw) <= 1


def test_downloads_match_real_dimensions_and_preserve_existing_mark():
    press = (SITE / "press.html").read_text(encoding="utf-8")
    assert "not yet packaged" not in press
    for name in NAMES:
        for suffix in ("svg", "png"):
            assert f'href="/brand/{name}.{suffix}" download' in press
        tag = re.search(rf'<img src="/brand/{name}\.svg"[^>]+>', press)[0]
        width, height = (int(re.search(rf'{key}="(\d+)"', tag)[1]) for key in ("width", "height"))
        svg = ET.parse(SITE / f"brand/{name}.svg").getroot()
        _, _, vw, vh = map(float, svg.attrib["viewBox"].split())
        assert width / height == vw / vh
    nav = (SITE / "index.html").read_text(encoding="utf-8")
    original = ET.fromstring(re.search(r'<svg class="site-logo-mark".*?</svg>', nav, re.S)[0])
    for name in NAMES:
        exported = ET.parse(SITE / f"brand/{name}.svg").getroot().find(NS + "g")
        assert [(node.tag.removeprefix(NS), node.attrib) for node in exported] == [
            (node.tag, node.attrib) for node in original
        ]


def test_report_preview_quotes_the_example_without_inventing_results():
    page = (SITE / "audit.html").read_text(encoding="utf-8")
    report = (SITE / "examples/team-replay-report.md").read_text(encoding="utf-8")
    preview = re.search(r'<figure class="report-preview".*?</figure>', page, re.S)[0]
    excerpt = re.search(r"<blockquote>(.*?)</blockquote>", preview, re.S)[1]
    assert " ".join(excerpt.split()) in " ".join(report.split())
    for disclosure in ("Synthetic example", "Not a customer report", "Not performed", "Not run", "Fictional scenarios"):
        assert disclosure in preview
    assert 'href="/examples/team-replay-report.md"' in preview
    assert "<script" not in preview and "<img" not in preview
    assert "not an executed engagement or a PDF delivery" in " ".join(preview.split())
