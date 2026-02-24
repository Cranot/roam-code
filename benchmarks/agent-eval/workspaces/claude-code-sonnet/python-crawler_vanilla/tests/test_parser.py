"""Tests for HTML parser."""

from __future__ import annotations

import pytest

from webcrawler.parser import HTMLParser


class TestHTMLParser:
    """Tests for HTML parsing and content extraction."""

    def test_extract_title(self):
        html = "<html><head><title>Test Page</title></head><body></body></html>"
        parser = HTMLParser("https://example.com")
        content = parser.parse(html, "https://example.com/page")

        assert content.title == "Test Page"

    def test_extract_meta_description(self):
        html = """
        <html>
            <head>
                <meta name="description" content="This is a test page">
            </head>
            <body></body>
        </html>
        """
        parser = HTMLParser("https://example.com")
        content = parser.parse(html, "https://example.com/page")

        assert content.meta_description == "This is a test page"

    def test_extract_headings(self):
        html = """
        <html><body>
            <h1>Heading 1</h1>
            <h2>Heading 2a</h2>
            <h2>Heading 2b</h2>
            <h3>Heading 3</h3>
        </body></html>
        """
        parser = HTMLParser("https://example.com")
        content = parser.parse(html, "https://example.com/page")

        assert len(content.headings['h1']) == 1
        assert content.headings['h1'][0] == "Heading 1"
        assert len(content.headings['h2']) == 2
        assert len(content.headings['h3']) == 1

    def test_extract_internal_links(self):
        html = """
        <html><body>
            <a href="/page1">Link 1</a>
            <a href="https://example.com/page2">Link 2</a>
            <a href="page3">Link 3</a>
        </body></html>
        """
        parser = HTMLParser("example.com")
        content = parser.parse(html, "https://example.com/base")

        assert len(content.internal_links) == 3
        assert "https://example.com/page1" in content.internal_links
        assert "https://example.com/page2" in content.internal_links

    def test_extract_external_links(self):
        html = """
        <html><body>
            <a href="https://other.com/page1">External Link</a>
            <a href="https://example.com/page2">Internal Link</a>
        </body></html>
        """
        parser = HTMLParser("example.com")
        content = parser.parse(html, "https://example.com/page")

        assert len(content.external_links) == 1
        assert "https://other.com/page1" in content.external_links
        assert len(content.internal_links) == 1

    def test_extract_images(self):
        html = """
        <html><body>
            <img src="/image1.jpg" alt="Image 1">
            <img src="https://example.com/image2.jpg" alt="Image 2">
            <img src="image3.jpg">
        </body></html>
        """
        parser = HTMLParser("https://example.com")
        content = parser.parse(html, "https://example.com/page")

        assert len(content.images) == 3
        assert content.images[0]['alt'] == "Image 1"
        assert content.images[1]['alt'] == "Image 2"
        assert content.images[2]['alt'] == ""

    def test_ignore_invalid_links(self):
        html = """
        <html><body>
            <a href="javascript:void(0)">JS Link</a>
            <a href="mailto:test@example.com">Email</a>
            <a href="https://example.com/valid">Valid Link</a>
        </body></html>
        """
        parser = HTMLParser("example.com")
        content = parser.parse(html, "https://example.com/page")

        assert len(content.internal_links) == 1
        assert "https://example.com/valid" in content.internal_links

    def test_deduplicate_links(self):
        html = """
        <html><body>
            <a href="/page1">Link 1</a>
            <a href="/page1">Link 1 again</a>
            <a href="/page1#section">Link 1 with fragment</a>
        </body></html>
        """
        parser = HTMLParser("example.com")
        content = parser.parse(html, "https://example.com/base")

        # Should deduplicate to single normalized URL
        assert len(content.internal_links) == 1

    def test_empty_html(self):
        html = "<html><body></body></html>"
        parser = HTMLParser("https://example.com")
        content = parser.parse(html, "https://example.com/page")

        assert content.title is None
        assert content.meta_description is None
        assert len(content.internal_links) == 0
        assert len(content.external_links) == 0
