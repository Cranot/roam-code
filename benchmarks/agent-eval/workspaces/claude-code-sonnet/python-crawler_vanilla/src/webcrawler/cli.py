"""Command-line interface for web crawler."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from webcrawler.crawler import WebCrawler
from webcrawler.reporter import Reporter


@click.command()
@click.argument('url')
@click.option(
    '--max-depth',
    default=2,
    type=int,
    help='Maximum crawl depth (default: 2)'
)
@click.option(
    '--max-pages',
    default=50,
    type=int,
    help='Maximum number of pages to crawl (default: 50)'
)
@click.option(
    '--crawl-delay',
    default=1.0,
    type=float,
    help='Delay between requests in seconds (default: 1.0)'
)
@click.option(
    '--output',
    type=click.Choice(['json', 'csv', 'html'], case_sensitive=False),
    default='json',
    help='Output format (default: json)'
)
@click.option(
    '--output-file',
    type=click.Path(),
    help='Output file path (prints to stdout if not specified)'
)
@click.option(
    '--allow-external',
    is_flag=True,
    help='Allow crawling external domains'
)
@click.option(
    '--concurrency',
    default=5,
    type=int,
    help='Maximum concurrent requests (default: 5)'
)
@click.option(
    '--user-agent',
    default='WebCrawler/1.0',
    help='User agent string (default: WebCrawler/1.0)'
)
@click.option(
    '--timeout',
    default=30,
    type=int,
    help='Request timeout in seconds (default: 30)'
)
def main(
    url: str,
    max_depth: int,
    max_pages: int,
    crawl_delay: float,
    output: str,
    output_file: str | None,
    allow_external: bool,
    concurrency: int,
    user_agent: str,
    timeout: int,
) -> None:
    """
    Web crawler that respects robots.txt and extracts page content.

    URL: Starting URL for crawling (must include http:// or https://)

    Example usage:

        webcrawler https://example.com --max-depth 3 --max-pages 100

        webcrawler https://example.com --output html --output-file report.html

        webcrawler https://example.com --allow-external --concurrency 10
    """
    # Validate URL
    if not url.startswith(('http://', 'https://')):
        click.echo("Error: URL must start with http:// or https://", err=True)
        sys.exit(1)

    # Display crawl configuration
    click.echo(f"Starting crawl of: {url}")
    click.echo(f"Max depth: {max_depth}, Max pages: {max_pages}")
    click.echo(f"Crawl delay: {crawl_delay}s, Concurrency: {concurrency}")
    click.echo(f"Allow external: {allow_external}")
    click.echo("---")

    # Initialize crawler
    crawler = WebCrawler(
        start_url=url,
        max_depth=max_depth,
        max_pages=max_pages,
        crawl_delay=crawl_delay,
        allow_external=allow_external,
        concurrency_limit=concurrency,
        user_agent=user_agent,
        timeout=timeout,
    )

    # Run crawler
    try:
        results, stats = asyncio.run(crawler.crawl())
    except KeyboardInterrupt:
        click.echo("\nCrawl interrupted by user", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\nCrawl failed: {e}", err=True)
        sys.exit(1)

    # Display quick summary
    click.echo(f"\nCrawl complete!")
    click.echo(f"Pages crawled: {stats.total_pages_crawled}")
    click.echo(f"Broken links: {stats.total_broken_links}")
    click.echo(f"Average response time: {stats.average_response_time:.3f}s")
    click.echo(f"Duration: {stats.crawl_duration:.2f}s")
    click.echo("---")

    # Generate report
    reporter = Reporter(results, stats)

    if output == 'json':
        report_content = reporter.to_json()
    elif output == 'csv':
        report_content = reporter.to_csv()
    else:  # html
        report_content = reporter.to_html()

    # Output report
    if output_file:
        output_path = Path(output_file)
        try:
            output_path.write_text(report_content, encoding='utf-8')
            click.echo(f"Report saved to: {output_file}")
        except Exception as e:
            click.echo(f"Error writing to file: {e}", err=True)
            sys.exit(1)
    else:
        click.echo(report_content)


if __name__ == '__main__':
    main()
