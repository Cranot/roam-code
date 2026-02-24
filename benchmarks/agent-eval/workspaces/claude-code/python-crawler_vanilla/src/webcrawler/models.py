"""Data models for crawl results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PageData:
    """Data extracted from a single crawled page."""

    url: str
    status_code: int
    response_time: float
    title: Optional[str] = None
    meta_description: Optional[str] = None
    headings: Dict[str, List[str]] = field(default_factory=dict)
    internal_links: List[str] = field(default_factory=list)
    external_links: List[str] = field(default_factory=list)
    images: List[Dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None
    content_type: Optional[str] = None
    depth: int = 0
    redirect_url: Optional[str] = None


@dataclass
class CrawlStats:
    """Summary statistics for a crawl run."""

    total_pages: int = 0
    broken_links: int = 0
    total_response_time: float = 0.0
    link_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def average_response_time(self) -> float:
        if self.total_pages == 0:
            return 0.0
        return self.total_response_time / self.total_pages

    def most_linked_pages(self, top_n: int = 10) -> List[tuple[str, int]]:
        sorted_links = sorted(
            self.link_counts.items(), key=lambda x: x[1], reverse=True
        )
        return sorted_links[:top_n]


@dataclass
class CrawlResult:
    """Complete result of a crawl run."""

    start_url: str
    pages: List[PageData] = field(default_factory=list)
    broken: List[PageData] = field(default_factory=list)
    stats: CrawlStats = field(default_factory=CrawlStats)
