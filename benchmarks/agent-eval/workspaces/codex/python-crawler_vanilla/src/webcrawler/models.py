from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Heading:
    level: str
    text: str


@dataclass(slots=True)
class ImageData:
    src: str
    alt: str


@dataclass(slots=True)
class BrokenLink:
    url: str
    source_page: str
    status: int | None = None
    error: str | None = None


@dataclass(slots=True)
class PageData:
    requested_url: str
    url: str
    depth: int
    status: int | None
    response_time_ms: float | None
    title: str = ""
    meta_description: str = ""
    headings: list[Heading] = field(default_factory=list)
    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    images: list[ImageData] = field(default_factory=list)
    redirect_chain: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class SummaryStats:
    total_pages_crawled: int
    broken_links_found: int
    average_response_time_ms: float
    most_linked_pages: list[tuple[str, int]]


@dataclass(slots=True)
class CrawlReport:
    start_url: str
    pages: list[PageData]
    broken_links: list[BrokenLink]
    summary: SummaryStats

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_url": self.start_url,
            "pages": [asdict(page) for page in self.pages],
            "broken_links": [asdict(link) for link in self.broken_links],
            "summary": {
                "total_pages_crawled": self.summary.total_pages_crawled,
                "broken_links_found": self.summary.broken_links_found,
                "average_response_time_ms": self.summary.average_response_time_ms,
                "most_linked_pages": [
                    {"url": url, "count": count}
                    for url, count in self.summary.most_linked_pages
                ],
            },
        }
