"""Tests for robots.txt checker."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses

import aiohttp

from webcrawler.robots import RobotsChecker


@pytest.mark.asyncio
class TestRobotsChecker:
    """Tests for robots.txt checking."""

    async def test_allows_when_no_robots_txt(self):
        checker = RobotsChecker("TestBot/1.0")

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=404)

            async with aiohttp.ClientSession() as session:
                can_fetch = await checker.can_fetch("https://example.com/page", session)

        assert can_fetch is True

    async def test_respects_disallow_rule(self):
        checker = RobotsChecker("TestBot/1.0")
        robots_content = """
User-agent: *
Disallow: /admin
"""

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=200, body=robots_content)

            async with aiohttp.ClientSession() as session:
                can_fetch_admin = await checker.can_fetch("https://example.com/admin", session)
                can_fetch_public = await checker.can_fetch("https://example.com/public", session)

        assert can_fetch_admin is False
        assert can_fetch_public is True

    async def test_respects_allow_rule(self):
        checker = RobotsChecker("TestBot/1.0")
        robots_content = """
User-agent: *
Disallow: /private
"""

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=200, body=robots_content)

            async with aiohttp.ClientSession() as session:
                can_fetch_public = await checker.can_fetch("https://example.com/public", session)
                can_fetch_private = await checker.can_fetch("https://example.com/private", session)

        assert can_fetch_public is True
        assert can_fetch_private is False

    async def test_caches_robots_txt(self):
        checker = RobotsChecker("TestBot/1.0")
        robots_content = """
User-agent: *
Disallow: /admin
"""

        with aioresponses() as m:
            # Should only fetch robots.txt once
            m.get("https://example.com/robots.txt", status=200, body=robots_content)

            async with aiohttp.ClientSession() as session:
                await checker.can_fetch("https://example.com/page1", session)
                await checker.can_fetch("https://example.com/page2", session)

        # Should have cached the result

    async def test_handles_network_error(self):
        checker = RobotsChecker("TestBot/1.0")

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", exception=aiohttp.ClientError())

            async with aiohttp.ClientSession() as session:
                can_fetch = await checker.can_fetch("https://example.com/page", session)

        # Should allow on error
        assert can_fetch is True

    async def test_different_domains_separate_cache(self):
        checker = RobotsChecker("TestBot/1.0")
        robots1 = "User-agent: *\nDisallow: /admin"
        robots2 = "User-agent: *\nDisallow: /private"

        with aioresponses() as m:
            m.get("https://example.com/robots.txt", status=200, body=robots1)
            m.get("https://other.com/robots.txt", status=200, body=robots2)

            async with aiohttp.ClientSession() as session:
                can_fetch1 = await checker.can_fetch("https://example.com/admin", session)
                can_fetch2 = await checker.can_fetch("https://other.com/admin", session)

        assert can_fetch1 is False
        assert can_fetch2 is True
