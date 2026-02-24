"""Tests for the reporter module (JSON, CSV, HTML output)."""

from __future__ import annotations

import csv
import io
import json

from webcrawler.models import CrawlResult, CrawlStats, PageData
from webcrawler.reporter import to_csv, to_html, to_json


def _sample_result() -> CrawlResult:
    """Build a sample CrawlResult for testing."""
    page1 = PageData(
        url="https://example.com",
        status_code=200,
        response_time=0.25,
        title="Example Home",
        meta_description="An example site",
        headings={"h1": ["Welcome"]},
        internal_links=["https://example.com/about", "https://example.com/contact"],
        external_links=["https://other.com"],
        images=[{"src": "https://example.com/img.png", "alt": "Logo"}],
        depth=0,
    )
    page2 = PageData(
        url="https://example.com/about",
        status_code=200,
        response_time=0.15,
        title="About",
        depth=1,
    )
    broken = PageData(
        url="https://example.com/missing",
        status_code=404,
        response_time=0.05,
        error="Not Found",
        depth=1,
    )

    stats = CrawlStats(
        total_pages=3,
        broken_links=1,
        total_response_time=0.45,
        link_counts={
            "https://example.com/about": 5,
            "https://example.com/contact": 2,
            "https://other.com": 1,
        },
    )

    return CrawlResult(
        start_url="https://example.com",
        pages=[page1, page2],
        broken=[broken],
        stats=stats,
    )


class TestJsonReporter:
    def test_valid_json(self) -> None:
        result = _sample_result()
        output = to_json(result)
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_summary_fields(self) -> None:
        result = _sample_result()
        data = json.loads(to_json(result))
        summary = data["summary"]
        assert summary["total_pages"] == 3
        assert summary["broken_links"] == 1
        assert summary["average_response_time"] == 0.15

    def test_most_linked_pages(self) -> None:
        result = _sample_result()
        data = json.loads(to_json(result))
        most_linked = data["summary"]["most_linked_pages"]
        assert most_linked[0]["url"] == "https://example.com/about"
        assert most_linked[0]["inbound_links"] == 5

    def test_pages_included(self) -> None:
        result = _sample_result()
        data = json.loads(to_json(result))
        assert len(data["pages"]) == 2
        assert len(data["broken"]) == 1

    def test_page_data_fields(self) -> None:
        result = _sample_result()
        data = json.loads(to_json(result))
        page = data["pages"][0]
        assert page["url"] == "https://example.com"
        assert page["title"] == "Example Home"
        assert page["meta_description"] == "An example site"
        assert "h1" in page["headings"]
        assert len(page["internal_links"]) == 2
        assert len(page["images"]) == 1

    def test_broken_page_has_error(self) -> None:
        result = _sample_result()
        data = json.loads(to_json(result))
        broken = data["broken"][0]
        assert broken["status_code"] == 404
        assert broken["error"] == "Not Found"

    def test_empty_result(self) -> None:
        result = CrawlResult(start_url="https://empty.com")
        data = json.loads(to_json(result))
        assert data["summary"]["total_pages"] == 0
        assert data["pages"] == []
        assert data["broken"] == []


class TestCsvReporter:
    def test_valid_csv(self) -> None:
        result = _sample_result()
        output = to_csv(result)
        reader = csv.reader(io.StringIO(output))
        rows = list(reader)
        # Header + 3 data rows (2 pages + 1 broken)
        assert len(rows) == 4

    def test_header_columns(self) -> None:
        result = _sample_result()
        output = to_csv(result)
        reader = csv.reader(io.StringIO(output))
        header = next(reader)
        assert "url" in header
        assert "status_code" in header
        assert "title" in header
        assert "error" in header

    def test_data_values(self) -> None:
        result = _sample_result()
        output = to_csv(result)
        reader = csv.reader(io.StringIO(output))
        next(reader)  # skip header
        first_row = next(reader)
        assert first_row[0] == "https://example.com"
        assert first_row[1] == "200"

    def test_empty_result(self) -> None:
        result = CrawlResult(start_url="https://empty.com")
        output = to_csv(result)
        reader = csv.reader(io.StringIO(output))
        rows = list(reader)
        assert len(rows) == 1  # header only


class TestHtmlReporter:
    def test_contains_doctype(self) -> None:
        result = _sample_result()
        output = to_html(result)
        assert "<!DOCTYPE html>" in output

    def test_contains_title(self) -> None:
        result = _sample_result()
        output = to_html(result)
        assert "Crawl Report" in output

    def test_contains_start_url(self) -> None:
        result = _sample_result()
        output = to_html(result)
        assert "https://example.com" in output

    def test_contains_summary_stats(self) -> None:
        result = _sample_result()
        output = to_html(result)
        assert "Total Pages Crawled" in output
        assert "Broken Links" in output
        assert "Avg Response Time" in output

    def test_contains_page_urls(self) -> None:
        result = _sample_result()
        output = to_html(result)
        assert "https://example.com/about" in output
        assert "https://example.com/missing" in output

    def test_no_broken_message(self) -> None:
        result = CrawlResult(start_url="https://example.com")
        output = to_html(result)
        assert "No broken links found" in output

    def test_html_escaping(self) -> None:
        page = PageData(
            url="https://example.com/page?a=1&b=2",
            status_code=200,
            response_time=0.1,
            title="Page <script>alert('xss')</script>",
            depth=0,
        )
        result = CrawlResult(
            start_url="https://example.com",
            pages=[page],
            stats=CrawlStats(total_pages=1, total_response_time=0.1),
        )
        output = to_html(result)
        assert "<script>" not in output
        assert "&lt;script&gt;" in output
