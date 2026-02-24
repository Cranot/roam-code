"""Command-line interface for the web crawler."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from webcrawler.crawler import Crawler
from webcrawler.reporter import to_csv, to_html, to_json


@click.command()
@click.argument("url")
@click.option(
    "--max-depth", "-d", default=2, show_default=True,
    help="Maximum crawl depth.",
)
@click.option(
    "--max-pages", "-p", default=50, show_default=True,
    help="Maximum number of pages to crawl.",
)
@click.option(
    "--delay", default=1.0, show_default=True,
    help="Delay in seconds between requests.",
)
@click.option(
    "--concurrency", "-c", default=5, show_default=True,
    help="Maximum number of concurrent requests.",
)
@click.option(
    "--allow-external", is_flag=True, default=False,
    help="Follow links to external domains.",
)
@click.option(
    "--timeout", default=10.0, show_default=True,
    help="Request timeout in seconds.",
)
@click.option(
    "--output-format", "-f",
    type=click.Choice(["json", "csv", "html"], case_sensitive=False),
    default="json", show_default=True,
    help="Output format.",
)
@click.option(
    "--output", "-o", type=click.Path(), default=None,
    help="Output file path. Defaults to stdout.",
)
@click.option(
    "--user-agent", default="webcrawler/1.0", show_default=True,
    help="User-Agent string.",
)
def main(
    url: str,
    max_depth: int,
    max_pages: int,
    delay: float,
    concurrency: int,
    allow_external: bool,
    timeout: float,
    output_format: str,
    output: str | None,
    user_agent: str,
) -> None:
    """Crawl a website starting from URL and produce a report."""
    crawler = Crawler(
        start_url=url,
        max_depth=max_depth,
        max_pages=max_pages,
        delay=delay,
        concurrency=concurrency,
        allow_external=allow_external,
        timeout=timeout,
        user_agent=user_agent,
    )

    click.echo(f"Starting crawl of {crawler.start_url}", err=True)
    click.echo(
        f"  max_depth={max_depth}, max_pages={max_pages}, "
        f"concurrency={concurrency}, delay={delay}s",
        err=True,
    )

    result = asyncio.run(crawler.crawl())

    click.echo(
        f"Crawl complete: {result.stats.total_pages} pages, "
        f"{result.stats.broken_links} broken links",
        err=True,
    )

    fmt = output_format.lower()
    if fmt == "json":
        report = to_json(result)
    elif fmt == "csv":
        report = to_csv(result)
    elif fmt == "html":
        report = to_html(result)
    else:
        click.echo(f"Unknown format: {output_format}", err=True)
        sys.exit(1)

    if output:
        Path(output).write_text(report, encoding="utf-8")
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(report)


if __name__ == "__main__":
    main()
