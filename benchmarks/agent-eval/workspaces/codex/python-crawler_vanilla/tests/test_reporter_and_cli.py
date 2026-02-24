from __future__ import annotations

import json

from webcrawler.cli import build_parser
from webcrawler.models import (
    BrokenLink,
    CrawlReport,
    Heading,
    ImageData,
    PageData,
    SummaryStats,
)
from webcrawler.reporter import write_report


def _sample_report() -> CrawlReport:
    return CrawlReport(
        start_url="https://example.com/",
        pages=[
            PageData(
                requested_url="https://example.com/",
                url="https://example.com/",
                depth=0,
                status=200,
                response_time_ms=15.2,
                title="Home",
                meta_description="Homepage",
                headings=[Heading(level="h1", text="Main")],
                internal_links=["https://example.com/about"],
                external_links=["https://external.test/"],
                images=[ImageData(src="https://example.com/logo.png", alt="logo")],
            )
        ],
        broken_links=[
            BrokenLink(
                url="https://example.com/broken",
                source_page="https://example.com/",
                status=404,
                error="HTTP error response",
            )
        ],
        summary=SummaryStats(
            total_pages_crawled=1,
            broken_links_found=1,
            average_response_time_ms=15.2,
            most_linked_pages=[("https://example.com/about", 3)],
        ),
    )


def test_reporter_writes_json_csv_html(tmp_path) -> None:
    report = _sample_report()

    json_path = write_report(report, "json", str(tmp_path / "report.json"))
    csv_path = write_report(report, "csv", str(tmp_path / "report.csv"))
    html_path = write_report(report, "html", str(tmp_path / "report.html"))

    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["summary"]["total_pages_crawled"] == 1
    assert "https://example.com/" in csv_path.read_text(encoding="utf-8")
    html_text = html_path.read_text(encoding="utf-8")
    assert "Web Crawler Report" in html_text
    assert "Broken Links" in html_text


def test_cli_parser_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["https://example.com"])
    assert args.max_depth == 2
    assert args.max_pages == 50
    assert args.output_format == "json"
    assert args.crawl_delay == 1.0
    assert args.allow_external is False
