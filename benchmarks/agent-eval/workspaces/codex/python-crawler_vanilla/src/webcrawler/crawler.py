from __future__ import annotations

import asyncio
from collections import Counter, deque
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib import robotparser
from urllib.parse import urlsplit

import aiohttp

from .models import BrokenLink, CrawlReport, PageData, SummaryStats
from .parser import ParsedPage, get_domain, normalize_url, parse_html


@dataclass(slots=True)
class CrawlConfig:
    start_url: str
    max_depth: int = 2
    max_pages: int = 50
    crawl_delay: float = 1.0
    allow_external: bool = False
    concurrency: int = 5
    timeout_seconds: float = 10.0
    user_agent: str = "webcrawler/0.1"
    max_redirects: int = 10


@dataclass(slots=True)
class _FetchResult:
    requested_url: str
    final_url: str
    status: int | None
    headers: dict[str, str]
    text: str
    response_time_ms: float | None
    redirect_chain: list[str]
    error: str | None = None


@dataclass(slots=True)
class _CrawlResult:
    page: PageData
    links_for_queue: list[str]
    links_for_counting: list[str]
    broken_links: list[BrokenLink]


class DelayManager:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self._last_request_by_domain: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, domain: str) -> None:
        if self.delay_seconds <= 0:
            return
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            now = monotonic()
            last = self._last_request_by_domain.get(domain)
            if last is not None:
                remaining = self.delay_seconds - (now - last)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request_by_domain[domain] = monotonic()


class RobotsPolicy:
    def __init__(self, session: aiohttp.ClientSession, user_agent: str) -> None:
        self._session = session
        self._user_agent = user_agent
        self._parsers: dict[str, robotparser.RobotFileParser | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def is_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        domain = parts.netloc.lower()
        parser = await self._load_parser(parts.scheme, domain)
        if parser is None:
            return True
        return parser.can_fetch(self._user_agent, url)

    async def _load_parser(
        self, scheme: str, domain: str
    ) -> robotparser.RobotFileParser | None:
        if domain in self._parsers:
            return self._parsers[domain]

        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            if domain in self._parsers:
                return self._parsers[domain]
            robots_url = f"{scheme}://{domain}/robots.txt"
            parser = robotparser.RobotFileParser()
            parser.set_url(robots_url)
            try:
                async with self._session.get(robots_url) as response:
                    if response.status >= 400:
                        self._parsers[domain] = None
                        return None
                    body = await response.text(errors="ignore")
            except aiohttp.ClientError:
                self._parsers[domain] = None
                return None
            except asyncio.TimeoutError:
                self._parsers[domain] = None
                return None

            parser.parse(body.splitlines())
            self._parsers[domain] = parser
            return parser


class WebCrawler:
    def __init__(self, config: CrawlConfig) -> None:
        normalized_start = normalize_url(config.start_url)
        if not normalized_start:
            raise ValueError(f"Invalid starting URL: {config.start_url}")

        self.config = config
        self.start_url = normalized_start
        self.start_domain = get_domain(self.start_url)
        self._delay_manager = DelayManager(self.config.crawl_delay)
        self._request_semaphore = asyncio.Semaphore(max(1, self.config.concurrency))
        self._link_health_cache: dict[str, tuple[int | None, str | None]] = {}
        self._link_health_tasks: dict[str, asyncio.Task[tuple[int | None, str | None]]] = {}
        self._link_health_lock = asyncio.Lock()

    async def crawl(self) -> CrawlReport:
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        connector = aiohttp.TCPConnector(limit=max(1, self.config.concurrency))
        headers = {"User-Agent": self.config.user_agent}

        pages: dict[str, PageData] = {}
        broken_links: list[BrokenLink] = []
        inbound_counts: Counter[str] = Counter()
        response_times_ms: list[float] = []

        queue: deque[tuple[str, int, str]] = deque([(self.start_url, 0, self.start_url)])
        queued: set[str] = {self.start_url}
        visited: set[str] = set()

        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, headers=headers
        ) as session:
            self._robots_policy = RobotsPolicy(session, user_agent=self.config.user_agent)
            while queue and len(visited) < self.config.max_pages:
                batch: list[tuple[str, int, str]] = []
                while (
                    queue
                    and len(batch) < self.config.concurrency
                    and (len(visited) + len(batch)) < self.config.max_pages
                ):
                    url, depth, source_page = queue.popleft()
                    queued.discard(url)
                    if url in visited:
                        continue
                    visited.add(url)
                    batch.append((url, depth, source_page))

                if not batch:
                    continue

                results = await asyncio.gather(
                    *(self._crawl_single(session, url, depth, source) for url, depth, source in batch)
                )

                for result in results:
                    page = result.page
                    if page.url not in pages:
                        pages[page.url] = page
                        if page.response_time_ms is not None:
                            response_times_ms.append(page.response_time_ms)

                    for link in result.links_for_counting:
                        inbound_counts[link] += 1
                    broken_links.extend(result.broken_links)

                    for link in result.links_for_queue:
                        if link in visited or link in queued:
                            continue
                        if len(visited) + len(queued) >= self.config.max_pages:
                            break
                        queue.append((link, page.depth + 1, page.url))
                        queued.add(link)

        unique_broken: dict[tuple[str, str], BrokenLink] = {}
        for broken in broken_links:
            key = (broken.url, broken.source_page)
            if key not in unique_broken:
                unique_broken[key] = broken

        avg_response = (
            round(sum(response_times_ms) / len(response_times_ms), 2)
            if response_times_ms
            else 0.0
        )
        most_linked = inbound_counts.most_common(5)
        summary = SummaryStats(
            total_pages_crawled=len(pages),
            broken_links_found=len(unique_broken),
            average_response_time_ms=avg_response,
            most_linked_pages=most_linked,
        )
        return CrawlReport(
            start_url=self.start_url,
            pages=list(pages.values()),
            broken_links=list(unique_broken.values()),
            summary=summary,
        )

    async def _crawl_single(
        self,
        session: aiohttp.ClientSession,
        url: str,
        depth: int,
        source_page: str,
    ) -> _CrawlResult:
        fetch_result = await self._fetch_url(session, url)
        page = PageData(
            requested_url=url,
            url=fetch_result.final_url,
            depth=depth,
            status=fetch_result.status,
            response_time_ms=fetch_result.response_time_ms,
            redirect_chain=fetch_result.redirect_chain,
            error=fetch_result.error,
        )
        broken_links: list[BrokenLink] = []

        if fetch_result.error:
            broken_links.append(
                BrokenLink(
                    url=url,
                    source_page=source_page,
                    status=fetch_result.status,
                    error=fetch_result.error,
                )
            )
            return _CrawlResult(page=page, links_for_queue=[], links_for_counting=[], broken_links=broken_links)

        if fetch_result.status is not None and fetch_result.status >= 400:
            broken_links.append(
                BrokenLink(
                    url=fetch_result.final_url,
                    source_page=source_page,
                    status=fetch_result.status,
                    error="HTTP error response",
                )
            )
            return _CrawlResult(page=page, links_for_queue=[], links_for_counting=[], broken_links=broken_links)

        content_type = fetch_result.headers.get("Content-Type", "")
        parsed: ParsedPage | None = None
        if "text/html" in content_type.lower():
            parsed = parse_html(
                html=fetch_result.text,
                base_url=fetch_result.final_url,
                base_domain=self.start_domain,
            )
            page.title = parsed.title
            page.meta_description = parsed.meta_description
            page.headings = parsed.headings
            page.internal_links = parsed.internal_links
            page.external_links = parsed.external_links
            page.images = parsed.images

            for malformed in parsed.malformed_links:
                broken_links.append(
                    BrokenLink(
                        url=malformed,
                        source_page=page.url,
                        status=None,
                        error="Malformed URL",
                    )
                )

            checked_broken = await self._check_links(
                session=session, links=parsed.all_links, source_page=page.url
            )
            broken_links.extend(checked_broken)

        queue_candidates: list[str] = []
        if parsed and depth < self.config.max_depth:
            queue_candidates.extend(parsed.internal_links)
            if self.config.allow_external:
                queue_candidates.extend(parsed.external_links)

        return _CrawlResult(
            page=page,
            links_for_queue=queue_candidates,
            links_for_counting=(parsed.all_links if parsed else []),
            broken_links=broken_links,
        )

    async def _fetch_url(self, session: aiohttp.ClientSession, requested_url: str) -> _FetchResult:
        current_url = requested_url
        redirect_chain: list[str] = []
        seen_redirect_targets: set[str] = set()
        total_elapsed_ms = 0.0

        for _ in range(self.config.max_redirects + 1):
            if not self.config.allow_external and get_domain(current_url) != self.start_domain:
                return _FetchResult(
                    requested_url=requested_url,
                    final_url=current_url,
                    status=None,
                    headers={},
                    text="",
                    response_time_ms=total_elapsed_ms or None,
                    redirect_chain=redirect_chain,
                    error="Redirected to external domain",
                )

            if not await self._robots_policy.is_allowed(current_url):
                return _FetchResult(
                    requested_url=requested_url,
                    final_url=current_url,
                    status=None,
                    headers={},
                    text="",
                    response_time_ms=total_elapsed_ms or None,
                    redirect_chain=redirect_chain,
                    error="Blocked by robots.txt",
                )

            domain = get_domain(current_url)
            await self._delay_manager.wait(domain)
            response, elapsed_ms, error = await self._request_once(
                session=session, method="GET", url=current_url, allow_redirects=False
            )
            total_elapsed_ms += elapsed_ms
            if error:
                return _FetchResult(
                    requested_url=requested_url,
                    final_url=current_url,
                    status=None,
                    headers={},
                    text="",
                    response_time_ms=total_elapsed_ms or None,
                    redirect_chain=redirect_chain,
                    error=error,
                )
            if response is None:
                return _FetchResult(
                    requested_url=requested_url,
                    final_url=current_url,
                    status=None,
                    headers={},
                    text="",
                    response_time_ms=total_elapsed_ms or None,
                    redirect_chain=redirect_chain,
                    error="No response received",
                )

            status = response.status
            headers = dict(response.headers)

            if 300 <= status < 400:
                location = response.headers.get("Location")
                if not location:
                    response.release()
                    return _FetchResult(
                        requested_url=requested_url,
                        final_url=current_url,
                        status=status,
                        headers=headers,
                        text="",
                        response_time_ms=total_elapsed_ms,
                        redirect_chain=redirect_chain,
                        error="Redirect response missing Location header",
                    )
                next_url = normalize_url(location, base_url=current_url)
                if not next_url:
                    response.release()
                    return _FetchResult(
                        requested_url=requested_url,
                        final_url=current_url,
                        status=status,
                        headers=headers,
                        text="",
                        response_time_ms=total_elapsed_ms,
                        redirect_chain=redirect_chain,
                        error="Malformed redirect URL",
                    )
                if next_url in seen_redirect_targets:
                    response.release()
                    return _FetchResult(
                        requested_url=requested_url,
                        final_url=next_url,
                        status=status,
                        headers=headers,
                        text="",
                        response_time_ms=total_elapsed_ms,
                        redirect_chain=redirect_chain,
                        error="Circular redirect detected",
                    )
                seen_redirect_targets.add(next_url)
                redirect_chain.append(current_url)
                response.release()
                current_url = next_url
                continue

            response_text = ""
            try:
                response_text = await response.text(errors="ignore")
            except UnicodeDecodeError:
                response_text = ""

            final_url = normalize_url(str(response.url)) or current_url
            return _FetchResult(
                requested_url=requested_url,
                final_url=final_url,
                status=status,
                headers=headers,
                text=response_text,
                response_time_ms=total_elapsed_ms,
                redirect_chain=redirect_chain,
                error=None,
            )

        return _FetchResult(
            requested_url=requested_url,
            final_url=current_url,
            status=None,
            headers={},
            text="",
            response_time_ms=total_elapsed_ms or None,
            redirect_chain=redirect_chain,
            error=f"Exceeded redirect limit ({self.config.max_redirects})",
        )

    async def _check_links(
        self,
        session: aiohttp.ClientSession,
        links: list[str],
        source_page: str,
    ) -> list[BrokenLink]:
        unique_links = list(dict.fromkeys(links))
        health_results = await asyncio.gather(
            *(self._get_link_health(session=session, url=link) for link in unique_links)
        )
        broken: list[BrokenLink] = []
        for link, (status, error) in zip(unique_links, health_results):
            if error is None and (status is None or status < 400):
                continue
            if status is not None and status < 400:
                continue
            broken.append(BrokenLink(url=link, source_page=source_page, status=status, error=error))
        return broken

    async def _get_link_health(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> tuple[int | None, str | None]:
        async with self._link_health_lock:
            if url in self._link_health_cache:
                return self._link_health_cache[url]
            existing_task = self._link_health_tasks.get(url)
            if existing_task is None:
                existing_task = asyncio.create_task(self._probe_link(session=session, url=url))
                self._link_health_tasks[url] = existing_task

        result = await existing_task
        async with self._link_health_lock:
            self._link_health_cache[url] = result
            self._link_health_tasks.pop(url, None)
        return result

    async def _probe_link(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> tuple[int | None, str | None]:
        if not await self._robots_policy.is_allowed(url):
            return None, None
        domain = get_domain(url)
        await self._delay_manager.wait(domain)
        response, _, error = await self._request_once(
            session=session, method="HEAD", url=url, allow_redirects=True
        )
        if error:
            return None, error
        if response is None:
            return None, "No response received"
        if response.status in {405, 501}:
            response.release()
            response, _, error = await self._request_once(
                session=session, method="GET", url=url, allow_redirects=True
            )
            if error:
                return None, error
            if response is None:
                return None, "No response received"
        status = response.status
        response.release()
        return status, None

    async def _request_once(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        allow_redirects: bool,
    ) -> tuple[aiohttp.ClientResponse | None, float, str | None]:
        start = monotonic()
        try:
            async with self._request_semaphore:
                response = await session.request(
                    method=method,
                    url=url,
                    allow_redirects=allow_redirects,
                )
            elapsed_ms = (monotonic() - start) * 1000
            return response, elapsed_ms, None
        except asyncio.TimeoutError:
            elapsed_ms = (monotonic() - start) * 1000
            return None, elapsed_ms, "Request timed out"
        except aiohttp.InvalidURL:
            elapsed_ms = (monotonic() - start) * 1000
            return None, elapsed_ms, "Malformed URL"
        except aiohttp.ClientError as exc:
            elapsed_ms = (monotonic() - start) * 1000
            return None, elapsed_ms, f"HTTP client error: {exc}"
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (monotonic() - start) * 1000
            return None, elapsed_ms, f"Unexpected error: {exc}"
