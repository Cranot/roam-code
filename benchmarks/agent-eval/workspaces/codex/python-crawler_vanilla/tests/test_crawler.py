from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from webcrawler.crawler import CrawlConfig, RobotsPolicy, WebCrawler
from webcrawler.parser import normalize_url


@dataclass
class FakeResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: str

    async def text(self, errors: str = "ignore") -> str:
        _ = errors
        return self.body

    def release(self) -> None:
        return None


def _build_request_mock(routes: dict[tuple[str, str], dict[str, Any]]):
    async def _request_once(
        self: WebCrawler,  # noqa: ARG001
        session: Any,  # noqa: ARG001
        method: str,
        url: str,
        allow_redirects: bool,  # noqa: ARG001
    ) -> tuple[FakeResponse | None, float, str | None]:
        normalized = normalize_url(url)
        if not normalized:
            return None, 1.0, "Malformed URL"
        payload = routes.get((method, normalized))
        if payload is None:
            return None, 1.0, f"Unhandled route: {method} {normalized}"
        if payload.get("error"):
            return None, payload.get("elapsed_ms", 1.0), payload["error"]
        response = FakeResponse(
            url=payload.get("url", normalized),
            status=payload["status"],
            headers=payload.get("headers", {}),
            body=payload.get("body", ""),
        )
        return response, payload.get("elapsed_ms", 1.0), None

    return _request_once


@pytest.mark.asyncio
async def test_crawler_collects_content_and_broken_links(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {
        ("GET", "https://example.com/"): {
            "status": 200,
            "headers": {"Content-Type": "text/html"},
            "body": """
              <html>
                <head>
                  <title>Home</title>
                  <meta name="description" content="Homepage" />
                </head>
                <body>
                  <h1>Welcome</h1>
                  <h2>Overview</h2>
                  <a href="/about">About</a>
                  <a href="/broken">Broken</a>
                  <a href="/">Loop</a>
                  <a href="ht!tp://bad-url">Bad</a>
                  <a href="https://external.test/page">External</a>
                  <img src="/logo.png" alt="logo" />
                </body>
              </html>
            """,
            "elapsed_ms": 10.0,
        },
        ("GET", "https://example.com/about"): {
            "status": 200,
            "headers": {"Content-Type": "text/html"},
            "body": """
              <html><head><title>About</title></head>
              <body><h1>About</h1><a href="/">Home</a></body></html>
            """,
            "elapsed_ms": 20.0,
        },
        ("GET", "https://example.com/broken"): {
            "status": 404,
            "headers": {"Content-Type": "text/html"},
            "body": "<html><head><title>Not Found</title></head><body>missing</body></html>",
            "elapsed_ms": 8.0,
        },
        ("HEAD", "https://example.com/about"): {"status": 200, "headers": {}, "elapsed_ms": 2.0},
        ("HEAD", "https://example.com/broken"): {"status": 404, "headers": {}, "elapsed_ms": 2.0},
        ("HEAD", "https://example.com/"): {"status": 200, "headers": {}, "elapsed_ms": 2.0},
        ("HEAD", "https://external.test/page"): {"status": 200, "headers": {}, "elapsed_ms": 2.0},
    }

    async def always_allowed(self: RobotsPolicy, url: str) -> bool:  # noqa: ARG001
        return True

    monkeypatch.setattr(RobotsPolicy, "is_allowed", always_allowed)
    monkeypatch.setattr(WebCrawler, "_request_once", _build_request_mock(routes))

    crawler = WebCrawler(
        CrawlConfig(
            start_url="https://example.com",
            max_depth=2,
            max_pages=10,
            crawl_delay=0,
            concurrency=3,
            allow_external=False,
        )
    )
    report = await crawler.crawl()

    assert report.summary.total_pages_crawled == 3
    home_page = next(page for page in report.pages if page.url == "https://example.com/")
    assert home_page.title == "Home"
    assert home_page.meta_description == "Homepage"
    assert [heading.level for heading in home_page.headings] == ["h1", "h2"]
    assert home_page.images[0].alt == "logo"
    assert all("external.test" not in page.url for page in report.pages)
    assert report.summary.average_response_time_ms == pytest.approx(12.67, rel=1e-2)

    broken_urls = {item.url for item in report.broken_links}
    assert "https://example.com/broken" in broken_urls
    assert "ht!tp://bad-url" in broken_urls


@pytest.mark.asyncio
async def test_crawler_allow_external_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {
        ("GET", "https://example.com/"): {
            "status": 200,
            "headers": {"Content-Type": "text/html"},
            "body": '<a href="https://external.test/page">External</a>',
        },
        ("GET", "https://external.test/page"): {
            "status": 200,
            "headers": {"Content-Type": "text/html"},
            "body": "<title>External</title>",
        },
        ("HEAD", "https://external.test/page"): {"status": 200, "headers": {}},
    }

    async def always_allowed(self: RobotsPolicy, url: str) -> bool:  # noqa: ARG001
        return True

    monkeypatch.setattr(RobotsPolicy, "is_allowed", always_allowed)
    monkeypatch.setattr(WebCrawler, "_request_once", _build_request_mock(routes))

    crawler = WebCrawler(
        CrawlConfig(
            start_url="https://example.com",
            max_depth=1,
            max_pages=10,
            crawl_delay=0,
            concurrency=2,
            allow_external=True,
        )
    )
    report = await crawler.crawl()
    assert any(page.url == "https://external.test/page" for page in report.pages)


@pytest.mark.asyncio
async def test_crawler_robots_and_timeout_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {
        ("GET", "https://example.com/private"): {
            "status": 200,
            "headers": {"Content-Type": "text/html"},
            "body": "<html><title>Private</title></html>",
        },
        ("GET", "https://example.com/timeout"): {
            "error": "Request timed out",
            "elapsed_ms": 100.0,
        },
    }

    async def robots_block_private(self: RobotsPolicy, url: str) -> bool:
        return not url.endswith("/private")

    monkeypatch.setattr(RobotsPolicy, "is_allowed", robots_block_private)
    monkeypatch.setattr(WebCrawler, "_request_once", _build_request_mock(routes))

    blocked_crawler = WebCrawler(
        CrawlConfig(
            start_url="https://example.com/private",
            max_depth=0,
            max_pages=5,
            crawl_delay=0,
            concurrency=1,
        )
    )
    blocked_report = await blocked_crawler.crawl()
    assert blocked_report.summary.total_pages_crawled == 1
    assert blocked_report.broken_links[0].error == "Blocked by robots.txt"

    timeout_crawler = WebCrawler(
        CrawlConfig(
            start_url="https://example.com/timeout",
            max_depth=0,
            max_pages=5,
            crawl_delay=0,
            concurrency=1,
        )
    )
    timeout_report = await timeout_crawler.crawl()
    assert timeout_report.broken_links[0].error == "Request timed out"
