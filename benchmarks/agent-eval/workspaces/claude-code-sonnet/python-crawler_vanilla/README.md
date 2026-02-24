# WebCrawler

A comprehensive async web crawler built with Python that respects robots.txt, handles edge cases gracefully, and provides detailed reports in multiple formats.

## Features

- **Async I/O**: Uses `aiohttp` and `asyncio` for efficient concurrent crawling
- **Robots.txt Support**: Respects robots.txt rules and crawl delays
- **Configurable Limits**: Control max depth, max pages, crawl delay, and concurrency
- **Rich Content Extraction**: Extracts titles, meta descriptions, headings (h1-h6), links, and images
- **Broken Link Detection**: Identifies and reports 4xx/5xx responses
- **Domain Control**: Stay within same domain by default, with option to allow external links
- **Edge Case Handling**: Handles redirects, timeouts, circular links, and malformed URLs
- **Multiple Output Formats**: JSON (structured), CSV (flat table), HTML (visual report)
- **Comprehensive Statistics**: Total pages, broken links, response times, most linked pages

## Installation

### From Source

```bash
# Clone the repository
git clone <repository-url>
cd webcrawler

# Install in development mode
pip install -e .

# Or install with dev dependencies for testing
pip install -e ".[dev]"
```

### From Package

```bash
pip install webcrawler
```

## Usage

### Basic Usage

```bash
# Crawl a website with default settings
webcrawler https://example.com

# Specify max depth and pages
webcrawler https://example.com --max-depth 3 --max-pages 100

# Save output to file
webcrawler https://example.com --output json --output-file report.json
```

### Advanced Options

```bash
# Allow external domain crawling
webcrawler https://example.com --allow-external

# Adjust crawl delay and concurrency
webcrawler https://example.com --crawl-delay 2.0 --concurrency 10

# Generate HTML report
webcrawler https://example.com --output html --output-file report.html

# Custom user agent and timeout
webcrawler https://example.com --user-agent "MyBot/1.0" --timeout 60
```

### Command-Line Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `url` | argument | (required) | Starting URL for crawling |
| `--max-depth` | int | 2 | Maximum crawl depth |
| `--max-pages` | int | 50 | Maximum number of pages to crawl |
| `--crawl-delay` | float | 1.0 | Delay between requests in seconds |
| `--output` | choice | json | Output format: json, csv, or html |
| `--output-file` | path | stdout | Output file path |
| `--allow-external` | flag | False | Allow crawling external domains |
| `--concurrency` | int | 5 | Maximum concurrent requests |
| `--user-agent` | string | WebCrawler/1.0 | User agent string |
| `--timeout` | int | 30 | Request timeout in seconds |

## Examples

### Example 1: Basic Website Crawl

```bash
webcrawler https://example.com --max-depth 2 --max-pages 50
```

Output:
```
Starting crawl of: https://example.com
Max depth: 2, Max pages: 50
Crawl delay: 1.0s, Concurrency: 5
Allow external: False
---
Crawl complete!
Pages crawled: 42
Broken links: 3
Average response time: 0.234s
Duration: 45.67s
---
```

### Example 2: Generate HTML Report

```bash
webcrawler https://example.com --output html --output-file report.html
```

Opens `report.html` in your browser to see a visual report with:
- Summary statistics
- Most linked pages
- Broken links table
- Detailed page information

### Example 3: Deep Crawl with External Links

```bash
webcrawler https://example.com \
  --max-depth 5 \
  --max-pages 500 \
  --allow-external \
  --concurrency 20 \
  --crawl-delay 0.5 \
  --output json \
  --output-file deep_crawl.json
```

## Output Formats

### JSON Format

Structured data with full details:

```json
{
  "summary": {
    "total_pages_crawled": 42,
    "total_pages_attempted": 45,
    "broken_links_count": 3,
    "average_response_time": 0.234,
    "crawl_duration": 45.67
  },
  "most_linked_pages": [
    {"url": "https://example.com/popular", "link_count": 15}
  ],
  "pages": [
    {
      "url": "https://example.com/page1",
      "status_code": 200,
      "title": "Page Title",
      "meta_description": "Description",
      "headings": {
        "h1": ["Main Heading"],
        "h2": ["Subheading 1", "Subheading 2"]
      },
      "internal_links": [...],
      "external_links": [...],
      "images": [...]
    }
  ],
  "broken_links": [
    {
      "url": "https://example.com/broken",
      "status_code": 404,
      "found_on": "https://example.com/page1"
    }
  ]
}
```

### CSV Format

Flat table format suitable for spreadsheet analysis:

```csv
URL,Status Code,Response Time (s),Is Broken,Title,Meta Description,...
https://example.com/,200,0.234,No,Home Page,Welcome to our site,...
https://example.com/broken,404,0.123,Yes,,,,...
```

### HTML Format

Visual report with:
- Summary statistics table
- Most linked pages
- Broken links with sources
- Detailed page information with color-coded status

## Architecture

The package is organized into clean, modular components:

```
src/webcrawler/
├── __init__.py          # Package metadata
├── cli.py               # Click-based CLI interface
├── crawler.py           # Core async crawler engine
├── parser.py            # HTML parsing and extraction
├── reporter.py          # Output formatters (JSON/CSV/HTML)
├── robots.py            # robots.txt checker with caching
└── url_utils.py         # URL validation and normalization
```

### Key Components

- **WebCrawler**: Main crawler class with async/await support
- **HTMLParser**: BeautifulSoup-based content extractor
- **RobotsChecker**: Async robots.txt parser with per-domain caching
- **Reporter**: Multi-format output generator
- **URL Utils**: URL normalization, validation, and resolution

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run with coverage
pytest --cov=webcrawler --cov-report=html

# Run specific test file
pytest tests/test_crawler.py -v

# Run specific test
pytest tests/test_crawler.py::TestWebCrawler::test_basic_crawl -v
```

### Project Structure

```
webcrawler/
├── src/webcrawler/      # Source code
├── tests/               # Unit tests
│   ├── test_url_utils.py
│   ├── test_parser.py
│   ├── test_robots.py
│   ├── test_crawler.py
│   └── test_reporter.py
├── pyproject.toml       # Package configuration
└── README.md           # This file
```

### Type Hints

The entire codebase uses Python type hints for better IDE support and type checking:

```python
from webcrawler.crawler import WebCrawler

crawler: WebCrawler = WebCrawler(
    start_url="https://example.com",
    max_depth=2,
    max_pages=50,
)
```

## Edge Cases Handled

- **Redirects**: Follows HTTP redirects automatically
- **Timeouts**: Configurable timeout with graceful error handling
- **Circular Links**: Tracks visited URLs to avoid infinite loops
- **Malformed URLs**: Validates and normalizes all URLs
- **Network Errors**: Catches and reports connection errors
- **Non-HTML Content**: Only parses text/html content types
- **Missing robots.txt**: Allows crawling when robots.txt is missing
- **Fragment URLs**: Normalizes by removing fragments (#)
- **Relative URLs**: Resolves relative URLs to absolute
- **Duplicate Links**: Deduplicates links before crawling

## Performance

- **Concurrent Crawling**: Multiple pages fetched simultaneously
- **Configurable Concurrency**: Adjust based on target server capacity
- **Async I/O**: Non-blocking I/O for efficient resource usage
- **Robots.txt Caching**: Per-domain caching to avoid repeated fetches
- **Early Termination**: Stops when max pages or depth reached

## Limitations

- Only crawls HTTP/HTTPS URLs
- Requires valid HTML for content extraction
- JavaScript-rendered content not supported (static HTML only)
- No support for authentication (basic auth, cookies, etc.)
- Single-threaded (async, not parallel processing)

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new features
4. Ensure all tests pass
5. Submit a pull request

## License

MIT License - see LICENSE file for details

## Requirements

- Python 3.9+
- aiohttp >= 3.8.0
- beautifulsoup4 >= 4.11.0
- click >= 8.0.0

## Changelog

### Version 1.0.0
- Initial release
- Async crawling with configurable concurrency
- robots.txt support
- Multiple output formats (JSON, CSV, HTML)
- Comprehensive edge case handling
- Full test coverage
