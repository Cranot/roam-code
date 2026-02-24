# webcrawler

An async web crawler that extracts structured data from websites and produces reports in JSON, CSV, or HTML format.

## Features

- Async I/O with configurable concurrency (aiohttp + asyncio)
- Respects robots.txt rules
- Configurable crawl delay, depth, and page limits
- Extracts: title, meta description, headings (h1-h6), links, images with alt text
- Detects broken links (4xx/5xx responses)
- Stays within the same domain by default (flag to allow external)
- Handles redirects, timeouts, circular links, and malformed URLs
- Output formats: JSON, CSV, HTML
- Summary statistics: total pages, broken links, average response time, most linked pages

## Installation

```bash
pip install -e .
```

For development (includes pytest and test utilities):

```bash
pip install -e ".[dev]"
```

## Usage

Basic crawl (JSON output to stdout):

```bash
webcrawler https://example.com
```

### Options

| Option | Short | Default | Description |
|---|---|---|---|
| `--max-depth` | `-d` | 2 | Maximum crawl depth |
| `--max-pages` | `-p` | 50 | Maximum pages to crawl |
| `--delay` | | 1.0 | Seconds between requests |
| `--concurrency` | `-c` | 5 | Max concurrent requests |
| `--allow-external` | | false | Follow external domain links |
| `--timeout` | | 10.0 | Request timeout in seconds |
| `--output-format` | `-f` | json | Output format: json, csv, html |
| `--output` | `-o` | stdout | Output file path |
| `--user-agent` | | webcrawler/1.0 | User-Agent string |

### Examples

Crawl with depth 3, save as HTML report:

```bash
webcrawler https://example.com -d 3 -f html -o report.html
```

Quick shallow crawl with CSV output:

```bash
webcrawler https://example.com -d 1 -p 20 -f csv -o pages.csv
```

Fast crawl with higher concurrency and no delay:

```bash
webcrawler https://example.com -c 10 --delay 0 -p 100
```

Allow crawling external domains:

```bash
webcrawler https://example.com --allow-external -d 1
```

## Project Structure

```
src/webcrawler/
    __init__.py     # Package version
    cli.py          # Click CLI entry point
    crawler.py      # Async crawler engine
    models.py       # Data models (PageData, CrawlStats, CrawlResult)
    parser.py       # HTML parser (BeautifulSoup)
    reporter.py     # Output formatters (JSON, CSV, HTML)
tests/
    test_cli.py     # CLI tests
    test_crawler.py # Crawler engine tests
    test_models.py  # Data model tests
    test_parser.py  # Parser tests
    test_reporter.py# Reporter tests
```

## Running Tests

```bash
pytest tests/ -v
```

## Dependencies

- aiohttp >= 3.9 (async HTTP)
- beautifulsoup4 >= 4.12 (HTML parsing)
- click >= 8.0 (CLI framework)
