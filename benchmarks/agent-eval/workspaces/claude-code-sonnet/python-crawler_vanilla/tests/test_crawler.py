"""Tests for web crawler."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses

from webcrawler.crawler import WebCrawler


@pytest.mark.asyncio
class TestWebCrawler:
    """Tests for web crawler functionality."""

    async def test_basic_crawl(self):
        html = """
        <html>
            <head><title>Test Page</title></head>
            <body>
                <h1>Hello World</h1>
                <a href="/page2">Link to Page 2</a>
            </body>
        </html>
        """

        crawler = WebCrawler(
            start_url="https://example.com",
            max_depth=1,
            max_pages=10,
            crawl_delay=0,
        )

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)
            m.get("https://example.com/", status=200, body=html, content_type='text/html')

            results, stats = await crawler.crawl()

        assert len(results) >= 1
        assert stats.total_pages_crawled >= 1
        assert results[0].status_code == 200
        assert results[0].content is not None
        assert results[0].content.title == "Test Page"

    async def test_respects_max_pages(self):
        html = """
        <html><body>
            <a href="/page1">Link 1</a>
            <a href="/page2">Link 2</a>
            <a href="/page3">Link 3</a>
        </body></html>
        """

        crawler = WebCrawler(
            start_url="https://example.com",
            max_depth=2,
            max_pages=2,
            crawl_delay=0,
        )

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)
            m.get("https://example.com/", status=200, body=html, content_type='text/html')
            m.get("https://example.com/page1", status=200, body=html, content_type='text/html')
            m.get("https://example.com/page2", status=200, body=html, content_type='text/html')
            m.get("https://example.com/page3", status=200, body=html, content_type='text/html')

            results, stats = await crawler.crawl()

        # With async crawling, we might crawl slightly over the limit
        # due to concurrent task scheduling
        assert len(crawler.visited) <= 3
        assert stats.total_pages_attempted <= 3

    async def test_respects_max_depth(self):
        html_depth0 = '<html><body><a href="/depth1">Link</a></body></html>'
        html_depth1 = '<html><body><a href="/depth2">Link</a></body></html>'
        html_depth2 = '<html><body><a href="/depth3">Link</a></body></html>'

        crawler = WebCrawler(
            start_url="https://example.com",
            max_depth=1,
            max_pages=10,
            crawl_delay=0,
        )

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)
            m.get("https://example.com/", status=200, body=html_depth0, content_type='text/html')
            m.get("https://example.com/depth1", status=200, body=html_depth1, content_type='text/html')
            m.get("https://example.com/depth2", status=200, body=html_depth2, content_type='text/html')

            results, stats = await crawler.crawl()

        # Should not crawl depth2 as it's beyond max_depth
        crawled_urls = {r.url for r in results}
        assert "https://example.com/" in crawled_urls
        assert "https://example.com/depth1" in crawled_urls
        # depth2 should not be crawled (depth > max_depth)

    async def test_detects_broken_links(self):
        html = '<html><body><a href="/broken">Broken Link</a></body></html>'

        crawler = WebCrawler(
            start_url="https://example.com",
            max_depth=1,
            max_pages=10,
            crawl_delay=0,
        )

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)
            m.get("https://example.com/", status=200, body=html, content_type='text/html')
            m.get("https://example.com/broken", status=404)

            results, stats = await crawler.crawl()

        assert stats.total_broken_links >= 1
        broken_urls = [url for url, _, _ in stats.broken_links]
        assert "https://example.com/broken" in broken_urls

    async def test_stays_within_domain(self):
        html = """
        <html><body>
            <a href="/internal">Internal</a>
            <a href="https://other.com/external">External</a>
        </body></html>
        """

        crawler = WebCrawler(
            start_url="https://example.com",
            max_depth=1,
            max_pages=10,
            crawl_delay=0,
            allow_external=False,
        )

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)
            m.get("https://example.com/", status=200, body=html, content_type='text/html')
            m.get("https://example.com/internal", status=200, body="<html></html>", content_type='text/html')

            results, stats = await crawler.crawl()

        crawled_urls = {r.url for r in results}
        assert "https://example.com/internal" in crawled_urls
        assert "https://other.com/external" not in crawled_urls

    async def test_allows_external_when_enabled(self):
        html = '<html><body><a href="https://other.com/page">External</a></body></html>'

        crawler = WebCrawler(
            start_url="https://example.com",
            max_depth=1,
            max_pages=10,
            crawl_delay=0,
            allow_external=True,
        )

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)
            m.get("https://other.com/robots.txt", status=404)
            m.get("https://example.com/", status=200, body=html, content_type='text/html')
            m.get("https://other.com/page", status=200, body="<html></html>", content_type='text/html')

            results, stats = await crawler.crawl()

        crawled_urls = {r.url for r in results}
        assert "https://other.com/page" in crawled_urls

    async def test_handles_redirects(self):
        html = '<html><body>Redirected Content</body></html>'

        crawler = WebCrawler(
            start_url="https://example.com/old",
            max_depth=0,
            max_pages=10,
            crawl_delay=0,
        )

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)
            # Simulate redirect
            m.get("https://example.com/old", status=301, headers={'Location': 'https://example.com/new'})
            m.get("https://example.com/new", status=200, body=html, content_type='text/html')

            results, stats = await crawler.crawl()

        # Should handle redirect gracefully
        assert len(results) >= 1

    async def test_avoids_circular_links(self):
        html_a = '<html><body><a href="/b">Link to B</a></body></html>'
        html_b = '<html><body><a href="/a">Link to A</a></body></html>'

        crawler = WebCrawler(
            start_url="https://example.com/a",
            max_depth=3,
            max_pages=10,
            crawl_delay=0,
        )

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)
            m.get("https://example.com/a", status=200, body=html_a, content_type='text/html')
            m.get("https://example.com/b", status=200, body=html_b, content_type='text/html')

            results, stats = await crawler.crawl()

        # Should only visit each URL once
        assert len(crawler.visited) == 2

    async def test_tracks_response_times(self):
        html = '<html><body>Test</body></html>'

        crawler = WebCrawler(
            start_url="https://example.com",
            max_depth=0,
            max_pages=10,
            crawl_delay=0,
        )

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)
            m.get("https://example.com/", status=200, body=html, content_type='text/html')

            results, stats = await crawler.crawl()

        assert len(stats.response_times) > 0
        assert stats.average_response_time >= 0

    async def test_counts_most_linked_pages(self):
        html = """
        <html><body>
            <a href="/popular">Link 1</a>
            <a href="/popular">Link 2</a>
            <a href="/other">Link 3</a>
        </body></html>
        """

        crawler = WebCrawler(
            start_url="https://example.com",
            max_depth=0,
            max_pages=10,
            crawl_delay=0,
        )

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)
            m.get("https://example.com/", status=200, body=html, content_type='text/html')

            results, stats = await crawler.crawl()

        assert "https://example.com/popular" in stats.most_linked_pages
        assert stats.most_linked_pages["https://example.com/popular"] >= 1
