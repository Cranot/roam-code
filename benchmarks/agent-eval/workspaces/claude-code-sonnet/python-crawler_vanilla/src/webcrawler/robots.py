"""robots.txt parser and URL permission checker."""

from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import aiohttp


class RobotsChecker:
    """Async robots.txt checker with caching."""

    def __init__(self, user_agent: str = "WebCrawler/1.0"):
        """
        Initialize robots checker.

        Args:
            user_agent: User agent string for crawler
        """
        self.user_agent = user_agent
        self._parsers: dict[str, Optional[RobotFileParser]] = {}
        self._lock = asyncio.Lock()

    async def can_fetch(self, url: str, session: aiohttp.ClientSession) -> bool:
        """
        Check if URL can be fetched according to robots.txt.

        Args:
            url: URL to check
            session: aiohttp session for fetching robots.txt

        Returns:
            True if allowed, False if disallowed
        """
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        async with self._lock:
            if base_url not in self._parsers:
                await self._load_robots(base_url, session)

        parser = self._parsers.get(base_url)
        if parser is None:
            # If robots.txt failed to load or doesn't exist, allow crawling
            return True

        return parser.can_fetch(self.user_agent, url)

    async def _load_robots(self, base_url: str, session: aiohttp.ClientSession) -> None:
        """
        Load and parse robots.txt for a domain.

        Args:
            base_url: Base URL of the domain
            session: aiohttp session for fetching
        """
        robots_url = f"{base_url}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)

        try:
            async with session.get(robots_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    content = await response.text()
                    # RobotFileParser expects lines as a list
                    parser.parse(content.splitlines())
                    self._parsers[base_url] = parser
                else:
                    # No robots.txt or error - allow all
                    self._parsers[base_url] = None
        except Exception:
            # Network error or timeout - allow all
            self._parsers[base_url] = None

    def get_crawl_delay(self, url: str) -> Optional[float]:
        """
        Get crawl delay from robots.txt if specified.

        Args:
            url: URL to check crawl delay for

        Returns:
            Crawl delay in seconds, or None if not specified
        """
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        parser = self._parsers.get(base_url)
        if parser is None:
            return None

        delay = parser.crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None
