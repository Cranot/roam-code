"""Async web crawler engine."""

from __future__ import annotations

import asyncio
import time
from typing import Optional, Set
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import aiohttp

from webcrawler.models import CrawlResult, CrawlStats, PageData
from webcrawler.parser import parse_page


class Crawler:
    """Async web crawler with configurable depth, concurrency, and domain filtering."""

    def __init__(
        self,
        start_url: str,
        max_depth: int = 2,
        max_pages: int = 50,
        delay: float = 1.0,
        concurrency: int = 5,
        allow_external: bool = False,
        timeout: float = 10.0,
        user_agent: str = "webcrawler/1.0",
    ) -> None:
        self.start_url = self._normalize_url(start_url)
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.delay = delay
        self.concurrency = concurrency
        self.allow_external = allow_external
        self.timeout = timeout
        self.user_agent = user_agent

        parsed = urlparse(self.start_url)
        self.base_domain: str = parsed.hostname or ""
        self.base_scheme: str = parsed.scheme or "https"

        self._visited: Set[str] = set()
        self._robots_cache: dict[str, Optional[RobotFileParser]] = {}
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._page_count = 0

    @staticmethod
    def _normalize_url(url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url.rstrip("/")

    async def crawl(self) -> CrawlResult:
        """Run the crawl and return results."""
        self._semaphore = asyncio.Semaphore(self.concurrency)
        result = CrawlResult(start_url=self.start_url)
        result.stats = CrawlStats()

        connector = aiohttp.TCPConnector(limit=self.concurrency)
        client_timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=client_timeout,
            headers={"User-Agent": self.user_agent},
        ) as session:
            await self._crawl_url(session, self.start_url, 0, result)

        return result

    async def _crawl_url(
        self,
        session: aiohttp.ClientSession,
        url: str,
        depth: int,
        result: CrawlResult,
    ) -> None:
        """Crawl a single URL and recurse into discovered links."""
        normalized = url.rstrip("/")
        if normalized in self._visited:
            return
        if self._page_count >= self.max_pages:
            return
        if depth > self.max_depth:
            return

        self._visited.add(normalized)
        self._page_count += 1

        page = await self._fetch_page(session, url, depth)
        if page is None:
            return

        if page.status_code and page.status_code >= 400:
            result.broken.append(page)
            result.stats.broken_links += 1
        else:
            result.pages.append(page)

        result.stats.total_pages += 1
        result.stats.total_response_time += page.response_time

        # Count inbound links
        for link in page.internal_links + page.external_links:
            link_norm = link.rstrip("/")
            result.stats.link_counts[link_norm] = (
                result.stats.link_counts.get(link_norm, 0) + 1
            )

        # Recurse into internal links
        if depth < self.max_depth:
            child_urls = page.internal_links
            if self.allow_external:
                child_urls = child_urls + page.external_links

            tasks = []
            for link in child_urls:
                if self._page_count >= self.max_pages:
                    break
                link_norm = link.rstrip("/")
                if link_norm in self._visited:
                    continue
                tasks.append(
                    self._crawl_url(session, link, depth + 1, result)
                )

            if tasks:
                await asyncio.gather(*tasks)

    async def _fetch_page(
        self,
        session: aiohttp.ClientSession,
        url: str,
        depth: int,
    ) -> Optional[PageData]:
        """Fetch and parse a single page."""
        assert self._semaphore is not None

        # Check robots.txt
        if not await self._check_robots(session, url):
            return None

        async with self._semaphore:
            # Respect crawl delay
            if self.delay > 0:
                await asyncio.sleep(self.delay)

            start_time = time.monotonic()
            try:
                async with session.get(
                    url, allow_redirects=True, max_redirects=5
                ) as response:
                    elapsed = time.monotonic() - start_time
                    content_type = response.content_type or ""

                    page = PageData(
                        url=url,
                        status_code=response.status,
                        response_time=elapsed,
                        content_type=content_type,
                        depth=depth,
                    )

                    # Track redirects
                    if response.history:
                        page.redirect_url = str(response.url)

                    # Only parse HTML pages
                    if response.status < 400 and "html" in content_type:
                        html = await response.text(errors="replace")
                        parsed = parse_page(html, str(response.url), self.base_domain)
                        page.title = parsed["title"]
                        page.meta_description = parsed["meta_description"]
                        page.headings = parsed["headings"]
                        page.internal_links = parsed["internal_links"]
                        page.external_links = parsed["external_links"]
                        page.images = parsed["images"]

                    return page

            except asyncio.TimeoutError:
                elapsed = time.monotonic() - start_time
                return PageData(
                    url=url,
                    status_code=0,
                    response_time=elapsed,
                    error="Timeout",
                    depth=depth,
                )
            except aiohttp.ClientError as exc:
                elapsed = time.monotonic() - start_time
                return PageData(
                    url=url,
                    status_code=0,
                    response_time=elapsed,
                    error=str(exc),
                    depth=depth,
                )

    async def _check_robots(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> bool:
        """Check if the URL is allowed by robots.txt."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.hostname}"

        if origin not in self._robots_cache:
            rp = RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            try:
                async with session.get(robots_url) as resp:
                    if resp.status == 200:
                        text = await resp.text(errors="replace")
                        rp.parse(text.splitlines())
                    else:
                        # No robots.txt — allow everything
                        rp.parse([])
            except (aiohttp.ClientError, asyncio.TimeoutError):
                rp.parse([])

            self._robots_cache[origin] = rp

        rp = self._robots_cache[origin]
        if rp is None:
            return True
        return rp.can_fetch(self.user_agent, url)
