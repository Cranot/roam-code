"""Tests for the async crawler engine."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webcrawler.crawler import Crawler


SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Test Page</title>
  <meta name="description" content="A test page">
</head>
<body>
  <h1>Welcome</h1>
  <a href="/about">About</a>
  <a href="/contact">Contact</a>
  <a href="https://external.com">External</a>
  <img src="/img/photo.jpg" alt="Photo">
</body>
</html>"""

ABOUT_HTML = """<!DOCTYPE html>
<html>
<head><title>About Page</title></head>
<body>
  <h1>About Us</h1>
  <a href="/">Home</a>
</body>
</html>"""

ROBOTS_TXT = """User-agent: *
Disallow: /secret/
Allow: /
"""


def _make_response(
    status: int = 200,
    html: str = SAMPLE_HTML,
    content_type: str = "text/html",
    url: str = "https://example.com",
    history: list | None = None,
) -> MagicMock:
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.content_type = content_type
    resp.url = url
    resp.history = history or []
    resp.text = AsyncMock(return_value=html)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


class TestCrawlerInit:
    def test_normalize_url_adds_scheme(self) -> None:
        c = Crawler("example.com")
        assert c.start_url == "https://example.com"

    def test_normalize_url_strips_trailing_slash(self) -> None:
        c = Crawler("https://example.com/")
        assert c.start_url == "https://example.com"

    def test_base_domain_extracted(self) -> None:
        c = Crawler("https://www.example.com/path")
        assert c.base_domain == "www.example.com"

    def test_defaults(self) -> None:
        c = Crawler("https://example.com")
        assert c.max_depth == 2
        assert c.max_pages == 50
        assert c.delay == 1.0
        assert c.concurrency == 5
        assert c.allow_external is False


class TestCrawlerCrawl:
    @pytest.mark.asyncio
    async def test_single_page_crawl(self) -> None:
        """Crawl a single page with no follow links."""
        pages: dict[str, MagicMock] = {
            "https://example.com": _make_response(url="https://example.com"),
            "https://example.com/robots.txt": _make_response(status=404, html="", url="https://example.com/robots.txt"),
            "https://example.com/about": _make_response(html=ABOUT_HTML, url="https://example.com/about"),
            "https://example.com/contact": _make_response(status=404, html="", content_type="text/html", url="https://example.com/contact"),
        }

        def mock_get(url, **kwargs):
            resp = pages.get(url)
            if resp is None:
                return _make_response(status=404, html="", url=url)
            return resp

        crawler = Crawler(
            "https://example.com",
            max_depth=1,
            max_pages=10,
            delay=0,
            concurrency=2,
        )

        with patch("webcrawler.crawler.aiohttp.ClientSession") as mock_session_cls:
            session_inst = AsyncMock()
            session_inst.get = MagicMock(side_effect=mock_get)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_inst)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await crawler.crawl()

        assert result.stats.total_pages >= 1
        urls_crawled = {p.url for p in result.pages + result.broken}
        assert "https://example.com" in urls_crawled

    @pytest.mark.asyncio
    async def test_max_pages_limit(self) -> None:
        """Crawler should stop when max_pages is reached."""
        def mock_get(url, **kwargs):
            return _make_response(url=url)

        crawler = Crawler(
            "https://example.com",
            max_depth=5,
            max_pages=1,
            delay=0,
        )

        with patch("webcrawler.crawler.aiohttp.ClientSession") as mock_session_cls:
            session_inst = AsyncMock()
            session_inst.get = MagicMock(side_effect=mock_get)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_inst)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await crawler.crawl()

        assert result.stats.total_pages <= 2  # start page + possibly robots.txt fetch

    @pytest.mark.asyncio
    async def test_broken_link_detection(self) -> None:
        """Pages returning 4xx/5xx are recorded as broken."""
        pages = {
            "https://example.com": _make_response(url="https://example.com", html='<html><body><a href="/broken">Link</a></body></html>'),
            "https://example.com/robots.txt": _make_response(status=404, html="", url="https://example.com/robots.txt"),
            "https://example.com/broken": _make_response(status=500, html="", content_type="text/html", url="https://example.com/broken"),
        }

        def mock_get(url, **kwargs):
            return pages.get(url, _make_response(status=404, html="", url=url))

        crawler = Crawler(
            "https://example.com",
            max_depth=1,
            max_pages=10,
            delay=0,
        )

        with patch("webcrawler.crawler.aiohttp.ClientSession") as mock_session_cls:
            session_inst = AsyncMock()
            session_inst.get = MagicMock(side_effect=mock_get)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_inst)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await crawler.crawl()

        assert result.stats.broken_links >= 1
        broken_urls = {p.url for p in result.broken}
        assert "https://example.com/broken" in broken_urls

    @pytest.mark.asyncio
    async def test_circular_link_detection(self) -> None:
        """Crawler should not revisit already-visited URLs."""
        circular_html = '<html><body><a href="/">Home</a><a href="/a">A</a></body></html>'
        pages = {
            "https://example.com": _make_response(url="https://example.com", html=circular_html),
            "https://example.com/robots.txt": _make_response(status=404, html="", url="https://example.com/robots.txt"),
            "https://example.com/a": _make_response(url="https://example.com/a", html=circular_html),
        }

        def mock_get(url, **kwargs):
            return pages.get(url, _make_response(status=404, html="", url=url))

        crawler = Crawler(
            "https://example.com",
            max_depth=10,
            max_pages=50,
            delay=0,
        )

        with patch("webcrawler.crawler.aiohttp.ClientSession") as mock_session_cls:
            session_inst = AsyncMock()
            session_inst.get = MagicMock(side_effect=mock_get)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_inst)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await crawler.crawl()

        # Should crawl exactly 2 unique pages (root + /a), not loop forever
        assert result.stats.total_pages == 2

    @pytest.mark.asyncio
    async def test_robots_txt_respected(self) -> None:
        """URLs disallowed by robots.txt should be skipped."""
        html_with_secret = '<html><body><a href="/secret/page">Secret</a></body></html>'
        pages = {
            "https://example.com": _make_response(url="https://example.com", html=html_with_secret),
            "https://example.com/robots.txt": _make_response(html=ROBOTS_TXT, content_type="text/plain", url="https://example.com/robots.txt"),
            "https://example.com/secret/page": _make_response(url="https://example.com/secret/page"),
        }

        def mock_get(url, **kwargs):
            return pages.get(url, _make_response(status=404, html="", url=url))

        crawler = Crawler(
            "https://example.com",
            max_depth=2,
            max_pages=10,
            delay=0,
        )

        with patch("webcrawler.crawler.aiohttp.ClientSession") as mock_session_cls:
            session_inst = AsyncMock()
            session_inst.get = MagicMock(side_effect=mock_get)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_inst)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await crawler.crawl()

        crawled_urls = {p.url for p in result.pages + result.broken}
        assert "https://example.com/secret/page" not in crawled_urls

    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """Timeout errors should be caught and recorded."""
        pages = {
            "https://example.com/robots.txt": _make_response(status=404, html="", url="https://example.com/robots.txt"),
        }

        def mock_get(url, **kwargs):
            if url == "https://example.com/robots.txt":
                return pages[url]
            raise asyncio.TimeoutError()

        crawler = Crawler(
            "https://example.com",
            max_depth=0,
            max_pages=5,
            delay=0,
        )

        with patch("webcrawler.crawler.aiohttp.ClientSession") as mock_session_cls:
            session_inst = AsyncMock()
            session_inst.get = MagicMock(side_effect=mock_get)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_inst)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await crawler.crawl()

        # The start URL timed out — recorded with error
        all_pages = result.pages + result.broken
        assert len(all_pages) == 1
        assert all_pages[0].error == "Timeout"

    @pytest.mark.asyncio
    async def test_domain_filtering(self) -> None:
        """Crawler should stay on same domain by default."""
        html = '<html><body><a href="https://other.com/page">Other</a></body></html>'
        pages = {
            "https://example.com": _make_response(url="https://example.com", html=html),
            "https://example.com/robots.txt": _make_response(status=404, html="", url="https://example.com/robots.txt"),
        }

        def mock_get(url, **kwargs):
            return pages.get(url, _make_response(status=404, html="", url=url))

        crawler = Crawler(
            "https://example.com",
            max_depth=2,
            max_pages=10,
            delay=0,
            allow_external=False,
        )

        with patch("webcrawler.crawler.aiohttp.ClientSession") as mock_session_cls:
            session_inst = AsyncMock()
            session_inst.get = MagicMock(side_effect=mock_get)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_inst)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await crawler.crawl()

        crawled_urls = {p.url for p in result.pages + result.broken}
        assert "https://other.com/page" not in crawled_urls

    @pytest.mark.asyncio
    async def test_redirect_tracking(self) -> None:
        """Redirected responses should record the final URL."""
        redirect_resp = _make_response(
            url="https://example.com/new-page",
            history=[MagicMock()],
        )
        pages = {
            "https://example.com": redirect_resp,
            "https://example.com/robots.txt": _make_response(status=404, html="", url="https://example.com/robots.txt"),
        }

        def mock_get(url, **kwargs):
            return pages.get(url, _make_response(status=404, html="", url=url))

        crawler = Crawler(
            "https://example.com",
            max_depth=0,
            max_pages=5,
            delay=0,
        )

        with patch("webcrawler.crawler.aiohttp.ClientSession") as mock_session_cls:
            session_inst = AsyncMock()
            session_inst.get = MagicMock(side_effect=mock_get)
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_inst)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await crawler.crawl()

        assert result.pages[0].redirect_url == "https://example.com/new-page"
