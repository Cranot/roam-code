"""Core async web crawler engine."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from webcrawler.parser import HTMLParser, PageContent
from webcrawler.robots import RobotsChecker
from webcrawler.url_utils import get_domain, is_same_domain, normalize_url


@dataclass
class CrawlResult:
    """Result of crawling a single page."""

    url: str
    status_code: Optional[int] = None
    content: Optional[PageContent] = None
    error: Optional[str] = None
    response_time: float = 0.0
    is_broken: bool = False


@dataclass
class CrawlStats:
    """Statistics from a crawl session."""

    total_pages_crawled: int = 0
    total_pages_attempted: int = 0
    broken_links: list[tuple[str, int, str]] = field(default_factory=list)  # (url, status, source)
    response_times: list[float] = field(default_factory=list)
    most_linked_pages: dict[str, int] = field(default_factory=dict)  # url -> count
    crawl_start_time: float = field(default_factory=time.time)
    crawl_end_time: Optional[float] = None

    @property
    def average_response_time(self) -> float:
        """Calculate average response time."""
        if not self.response_times:
            return 0.0
        return sum(self.response_times) / len(self.response_times)

    @property
    def total_broken_links(self) -> int:
        """Count of broken links found."""
        return len(self.broken_links)

    @property
    def crawl_duration(self) -> float:
        """Total crawl duration in seconds."""
        end = self.crawl_end_time or time.time()
        return end - self.crawl_start_time


class WebCrawler:
    """Async web crawler with configurable limits and robots.txt support."""

    def __init__(
        self,
        start_url: str,
        max_depth: int = 2,
        max_pages: int = 50,
        crawl_delay: float = 1.0,
        allow_external: bool = False,
        concurrency_limit: int = 5,
        user_agent: str = "WebCrawler/1.0",
        timeout: int = 30,
    ):
        """
        Initialize web crawler.

        Args:
            start_url: Starting URL for crawling
            max_depth: Maximum depth to crawl
            max_pages: Maximum number of pages to crawl
            crawl_delay: Delay between requests in seconds
            allow_external: Allow crawling external domains
            concurrency_limit: Maximum concurrent requests
            user_agent: User agent string
            timeout: Request timeout in seconds
        """
        self.start_url = normalize_url(start_url)
        self.base_domain = f"{get_domain(self.start_url)}"
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.crawl_delay = crawl_delay
        self.allow_external = allow_external
        self.concurrency_limit = concurrency_limit
        self.user_agent = user_agent
        self.timeout = timeout

        # Crawl state
        self.visited: set[str] = set()
        self.results: list[CrawlResult] = []
        self.stats = CrawlStats()
        self.robots_checker = RobotsChecker(user_agent)
        self.parser = HTMLParser(self.base_domain)

        # Concurrency control
        self.semaphore = asyncio.Semaphore(concurrency_limit)
        self.url_queue: asyncio.Queue[tuple[str, int, str]] = asyncio.Queue()  # (url, depth, source_url)

    async def crawl(self) -> tuple[list[CrawlResult], CrawlStats]:
        """
        Start crawling from the start URL.

        Returns:
            Tuple of (results list, statistics)
        """
        self.stats.crawl_start_time = time.time()

        # Create aiohttp session with custom headers
        timeout_config = aiohttp.ClientTimeout(total=self.timeout)
        headers = {'User-Agent': self.user_agent}

        async with aiohttp.ClientSession(timeout=timeout_config, headers=headers) as session:
            # Add start URL to queue
            await self.url_queue.put((self.start_url, 0, "start"))

            # Process URLs from queue
            tasks = []
            while not self.url_queue.empty() or tasks:
                # Start new tasks up to concurrency limit
                while not self.url_queue.empty() and len(tasks) < self.concurrency_limit:
                    if len(self.visited) >= self.max_pages:
                        break

                    try:
                        url, depth, source = await asyncio.wait_for(
                            self.url_queue.get(),
                            timeout=0.1
                        )
                        if url not in self.visited:
                            task = asyncio.create_task(
                                self._crawl_page(url, depth, source, session)
                            )
                            tasks.append(task)
                    except asyncio.TimeoutError:
                        break

                if not tasks:
                    break

                # Wait for at least one task to complete
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

                # Convert pending set back to list
                tasks = list(pending)

                # Process completed tasks
                for task in done:
                    await task  # Ensure exceptions are raised

                # Check if we've reached limits
                if len(self.visited) >= self.max_pages:
                    # Cancel remaining tasks
                    for task in tasks:
                        task.cancel()
                    break

            # Wait for any remaining tasks
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        self.stats.crawl_end_time = time.time()
        return self.results, self.stats

    async def _crawl_page(
        self,
        url: str,
        depth: int,
        source_url: str,
        session: aiohttp.ClientSession
    ) -> None:
        """
        Crawl a single page.

        Args:
            url: URL to crawl
            depth: Current depth level
            source_url: URL that linked to this page
            session: aiohttp session
        """
        async with self.semaphore:
            # Mark as visited
            self.visited.add(url)

            # Check robots.txt
            if not await self.robots_checker.can_fetch(url, session):
                return

            # Respect crawl delay
            if self.crawl_delay > 0:
                await asyncio.sleep(self.crawl_delay)

            # Fetch page
            result = CrawlResult(url=url)
            self.stats.total_pages_attempted += 1

            try:
                start_time = time.time()
                async with session.get(url, allow_redirects=True) as response:
                    response_time = time.time() - start_time
                    result.response_time = response_time
                    result.status_code = response.status
                    self.stats.response_times.append(response_time)

                    # Check for broken links (4xx, 5xx)
                    if response.status >= 400:
                        result.is_broken = True
                        self.stats.broken_links.append((url, response.status, source_url))

                    # Only parse successful HTML responses
                    if response.status == 200:
                        content_type = response.headers.get('Content-Type', '')
                        if 'text/html' in content_type:
                            html = await response.text()
                            content = self.parser.parse(html, url)
                            result.content = content

                            self.stats.total_pages_crawled += 1

                            # Update link statistics
                            for link in content.internal_links + content.external_links:
                                self.stats.most_linked_pages[link] = \
                                    self.stats.most_linked_pages.get(link, 0) + 1

                            # Add new URLs to queue if within depth limit
                            if depth < self.max_depth:
                                await self._queue_links(content, depth, url)

            except asyncio.TimeoutError:
                result.error = "Request timeout"
                result.is_broken = True
                self.stats.broken_links.append((url, 0, source_url))
            except aiohttp.ClientError as e:
                result.error = f"Client error: {str(e)}"
                result.is_broken = True
                self.stats.broken_links.append((url, 0, source_url))
            except Exception as e:
                result.error = f"Unexpected error: {str(e)}"
                result.is_broken = True
                self.stats.broken_links.append((url, 0, source_url))

            self.results.append(result)

    async def _queue_links(self, content: PageContent, current_depth: int, source_url: str) -> None:
        """
        Add discovered links to crawl queue.

        Args:
            content: Parsed page content
            current_depth: Current crawl depth
            source_url: Source URL of the links
        """
        next_depth = current_depth + 1

        # Queue internal links
        for link in content.internal_links:
            if link not in self.visited and len(self.visited) < self.max_pages:
                await self.url_queue.put((link, next_depth, source_url))

        # Queue external links if allowed
        if self.allow_external:
            for link in content.external_links:
                if link not in self.visited and len(self.visited) < self.max_pages:
                    await self.url_queue.put((link, next_depth, source_url))
