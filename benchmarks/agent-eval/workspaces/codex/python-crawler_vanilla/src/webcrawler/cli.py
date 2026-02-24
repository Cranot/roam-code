from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .crawler import CrawlConfig, WebCrawler
from .reporter import write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Async web crawler")
    parser.add_argument("start_url", help="Starting URL to crawl")
    parser.add_argument("--max-depth", type=int, default=2, help="Maximum crawl depth")
    parser.add_argument("--max-pages", type=int, default=50, help="Maximum number of pages")
    parser.add_argument(
        "--output-format",
        choices=["json", "csv", "html"],
        default="json",
        help="Output report format",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path (default: crawl_report.<format>)",
    )
    parser.add_argument(
        "--crawl-delay",
        type=float,
        default=1.0,
        help="Delay between requests to same domain in seconds",
    )
    parser.add_argument(
        "--allow-external",
        action="store_true",
        help="Allow crawling external domains",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Maximum concurrent requests",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Request timeout in seconds",
    )
    parser.add_argument(
        "--user-agent",
        type=str,
        default="webcrawler/0.1",
        help="User-Agent header value",
    )
    return parser


async def run_async(args: argparse.Namespace) -> int:
    config = CrawlConfig(
        start_url=args.start_url,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        crawl_delay=args.crawl_delay,
        allow_external=args.allow_external,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout,
        user_agent=args.user_agent,
    )
    crawler = WebCrawler(config=config)
    report = await crawler.crawl()

    output_path = args.output or f"crawl_report.{args.output_format}"
    written = write_report(report, args.output_format, output_path)
    resolved = Path(written).resolve()
    print(f"Report written to: {resolved}")
    print(f"Pages crawled: {report.summary.total_pages_crawled}")
    print(f"Broken links: {report.summary.broken_links_found}")
    print(f"Average response time (ms): {report.summary.average_response_time_ms}")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
