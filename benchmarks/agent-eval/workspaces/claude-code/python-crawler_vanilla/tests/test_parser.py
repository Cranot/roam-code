"""Tests for the HTML parser module."""

from __future__ import annotations

from webcrawler.parser import parse_page, _resolve_url


class TestExtractTitle:
    def test_basic_title(self) -> None:
        html = "<html><head><title>Hello World</title></head></html>"
        result = parse_page(html, "https://example.com", "example.com")
        assert result["title"] == "Hello World"

    def test_title_with_whitespace(self) -> None:
        html = "<html><head><title>  Spaced Title  </title></head></html>"
        result = parse_page(html, "https://example.com", "example.com")
        assert result["title"] == "Spaced Title"

    def test_no_title(self) -> None:
        html = "<html><head></head></html>"
        result = parse_page(html, "https://example.com", "example.com")
        assert result["title"] is None


class TestExtractMetaDescription:
    def test_basic_meta(self) -> None:
        html = '<html><head><meta name="description" content="A test page"></head></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert result["meta_description"] == "A test page"

    def test_no_meta(self) -> None:
        html = "<html><head></head></html>"
        result = parse_page(html, "https://example.com", "example.com")
        assert result["meta_description"] is None

    def test_empty_meta(self) -> None:
        html = '<html><head><meta name="description" content=""></head></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert result["meta_description"] is None


class TestExtractHeadings:
    def test_all_heading_levels(self) -> None:
        html = """<html><body>
        <h1>Main Title</h1>
        <h2>Subtitle</h2>
        <h3>Section</h3>
        <h4>Subsection</h4>
        <h5>Minor</h5>
        <h6>Tiny</h6>
        </body></html>"""
        result = parse_page(html, "https://example.com", "example.com")
        headings = result["headings"]
        assert headings["h1"] == ["Main Title"]
        assert headings["h2"] == ["Subtitle"]
        assert headings["h3"] == ["Section"]
        assert headings["h4"] == ["Subsection"]
        assert headings["h5"] == ["Minor"]
        assert headings["h6"] == ["Tiny"]

    def test_multiple_same_level(self) -> None:
        html = "<html><body><h2>First</h2><h2>Second</h2></body></html>"
        result = parse_page(html, "https://example.com", "example.com")
        assert result["headings"]["h2"] == ["First", "Second"]

    def test_no_headings(self) -> None:
        html = "<html><body><p>No headings</p></body></html>"
        result = parse_page(html, "https://example.com", "example.com")
        assert result["headings"] == {}


class TestExtractLinks:
    def test_internal_links(self) -> None:
        html = '<html><body><a href="/about">About</a></body></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert "https://example.com/about" in result["internal_links"]
        assert result["external_links"] == []

    def test_external_links(self) -> None:
        html = '<html><body><a href="https://other.com/page">Other</a></body></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert result["internal_links"] == []
        assert "https://other.com/page" in result["external_links"]

    def test_mixed_links(self) -> None:
        html = """<html><body>
        <a href="/local">Local</a>
        <a href="https://external.com">External</a>
        </body></html>"""
        result = parse_page(html, "https://example.com", "example.com")
        assert len(result["internal_links"]) == 1
        assert len(result["external_links"]) == 1

    def test_mailto_links_ignored(self) -> None:
        html = '<html><body><a href="mailto:test@example.com">Email</a></body></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert result["internal_links"] == []
        assert result["external_links"] == []

    def test_javascript_links_ignored(self) -> None:
        html = '<html><body><a href="javascript:void(0)">Click</a></body></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert result["internal_links"] == []
        assert result["external_links"] == []

    def test_fragment_links_stripped(self) -> None:
        html = '<html><body><a href="/page#section">Section</a></body></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert "https://example.com/page" in result["internal_links"]

    def test_relative_links(self) -> None:
        html = '<html><body><a href="subpage">Sub</a></body></html>'
        result = parse_page(html, "https://example.com/dir/", "example.com")
        assert "https://example.com/dir/subpage" in result["internal_links"]


class TestExtractImages:
    def test_basic_image(self) -> None:
        html = '<html><body><img src="/img/photo.jpg" alt="A photo"></body></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert len(result["images"]) == 1
        assert result["images"][0]["src"] == "https://example.com/img/photo.jpg"
        assert result["images"][0]["alt"] == "A photo"

    def test_image_no_alt(self) -> None:
        html = '<html><body><img src="/img/photo.jpg"></body></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert len(result["images"]) == 1
        assert result["images"][0]["alt"] == ""

    def test_absolute_image_url(self) -> None:
        html = '<html><body><img src="https://cdn.example.com/img.png" alt="CDN"></body></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert result["images"][0]["src"] == "https://cdn.example.com/img.png"

    def test_no_src_image_ignored(self) -> None:
        html = '<html><body><img alt="No source"></body></html>'
        result = parse_page(html, "https://example.com", "example.com")
        assert result["images"] == []


class TestResolveUrl:
    def test_absolute_url(self) -> None:
        assert _resolve_url("https://other.com/page", "https://example.com") == "https://other.com/page"

    def test_relative_url(self) -> None:
        assert _resolve_url("/about", "https://example.com") == "https://example.com/about"

    def test_fragment_only(self) -> None:
        assert _resolve_url("#top", "https://example.com") is None

    def test_mailto(self) -> None:
        assert _resolve_url("mailto:a@b.com", "https://example.com") is None

    def test_empty_string(self) -> None:
        assert _resolve_url("", "https://example.com") is None

    def test_tel_scheme(self) -> None:
        assert _resolve_url("tel:+1234567890", "https://example.com") is None

    def test_fragment_stripped_from_resolved(self) -> None:
        result = _resolve_url("/page#section", "https://example.com")
        assert result == "https://example.com/page"
