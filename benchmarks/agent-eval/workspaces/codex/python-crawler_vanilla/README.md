# WebCrawler

Async web crawler packaged as a Python project with a command line interface.

## Features

- Async crawling with `aiohttp` + `asyncio`
- Configurable max depth, max pages, concurrency, timeout, and crawl delay
- Robots.txt compliance
- Same-domain crawling by default, optional external crawling
- Page extraction:
  - title
  - meta description
  - headings (`h1` to `h6`)
  - internal and external links
  - images with alt text
- Broken link detection (`4xx`, `5xx`, malformed, and request failures)
- Redirect handling, circular link handling, malformed URL handling
- Reports in JSON, CSV, and HTML
- Summary stats:
  - total pages crawled
  - broken links found
  - average response time
  - most linked pages

## Setup

```bash
python -m venv .venv
. .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

For development/testing:

```bash
pip install -e .[dev]
```

## Usage

```bash
webcrawler https://example.com
```

Full options:

```bash
webcrawler START_URL \
  --max-depth 2 \
  --max-pages 50 \
  --output-format json \
  --output crawl_report.json \
  --crawl-delay 1.0 \
  --concurrency 5 \
  --timeout 10 \
  --allow-external
```

You can also run:

```bash
python -m webcrawler https://example.com --output-format html
```

## Output Formats

- `json`: full structured crawl report
- `csv`: flat page-level table
- `html`: visual report with summary, pages table, and broken links table

## Testing

```bash
pytest
```
