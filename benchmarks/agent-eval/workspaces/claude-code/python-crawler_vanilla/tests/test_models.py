"""Tests for data models."""

from __future__ import annotations

from webcrawler.models import CrawlResult, CrawlStats, PageData


class TestPageData:
    def test_defaults(self) -> None:
        page = PageData(url="https://example.com", status_code=200, response_time=0.5)
        assert page.title is None
        assert page.meta_description is None
        assert page.headings == {}
        assert page.internal_links == []
        assert page.external_links == []
        assert page.images == []
        assert page.error is None
        assert page.depth == 0

    def test_with_data(self) -> None:
        page = PageData(
            url="https://example.com",
            status_code=200,
            response_time=0.123,
            title="Test",
            headings={"h1": ["Title"]},
            internal_links=["https://example.com/about"],
            depth=1,
        )
        assert page.title == "Test"
        assert page.headings == {"h1": ["Title"]}
        assert len(page.internal_links) == 1
        assert page.depth == 1


class TestCrawlStats:
    def test_average_response_time(self) -> None:
        stats = CrawlStats(total_pages=4, total_response_time=2.0)
        assert stats.average_response_time == 0.5

    def test_average_response_time_zero_pages(self) -> None:
        stats = CrawlStats(total_pages=0, total_response_time=0.0)
        assert stats.average_response_time == 0.0

    def test_most_linked_pages(self) -> None:
        stats = CrawlStats(
            link_counts={
                "https://example.com/a": 10,
                "https://example.com/b": 5,
                "https://example.com/c": 20,
            }
        )
        top = stats.most_linked_pages(2)
        assert top == [
            ("https://example.com/c", 20),
            ("https://example.com/a", 10),
        ]

    def test_most_linked_pages_empty(self) -> None:
        stats = CrawlStats()
        assert stats.most_linked_pages() == []


class TestCrawlResult:
    def test_defaults(self) -> None:
        result = CrawlResult(start_url="https://example.com")
        assert result.pages == []
        assert result.broken == []
        assert result.stats.total_pages == 0
