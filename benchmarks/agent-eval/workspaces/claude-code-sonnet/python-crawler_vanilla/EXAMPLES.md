# WebCrawler Usage Examples

## Basic Examples

### 1. Simple Crawl with JSON Output

```bash
webcrawler https://example.com
```

This will:
- Crawl up to 50 pages (default)
- Go 2 levels deep (default)
- Wait 1 second between requests (default)
- Output JSON to stdout
- Stay within example.com domain

### 2. Save Report to File

```bash
webcrawler https://example.com --output-file crawl_report.json
```

### 3. Generate HTML Report

```bash
webcrawler https://example.com --output html --output-file report.html
```

Then open `report.html` in your browser to see:
- Summary statistics
- Most linked pages
- Broken links table
- Detailed page information

### 4. Generate CSV for Spreadsheet Analysis

```bash
webcrawler https://example.com --output csv --output-file data.csv
```

Import the CSV into Excel, Google Sheets, or any spreadsheet application.

## Advanced Examples

### 5. Deep Crawl

```bash
webcrawler https://example.com \
  --max-depth 5 \
  --max-pages 500
```

Crawl deeper into the site structure.

### 6. Fast Crawl with High Concurrency

```bash
webcrawler https://example.com \
  --concurrency 20 \
  --crawl-delay 0.5 \
  --max-pages 200
```

⚠️ **Warning**: High concurrency may overwhelm small servers. Use responsibly!

### 7. Crawl External Links

```bash
webcrawler https://example.com \
  --allow-external \
  --max-pages 100
```

This will follow links to other domains from the starting site.

### 8. Custom User Agent

```bash
webcrawler https://example.com \
  --user-agent "MyCompany Bot/1.0 (+https://mycompany.com/bot)"
```

Use a custom user agent string for identification.

### 9. Slow Crawl (Be Polite)

```bash
webcrawler https://example.com \
  --crawl-delay 5.0 \
  --concurrency 1 \
  --max-pages 20
```

Very polite crawling with 5 second delays and no concurrency.

## Programmatic Usage

You can also use the crawler as a Python library:

```python
import asyncio
from webcrawler.crawler import WebCrawler
from webcrawler.reporter import Reporter

async def crawl_site():
    # Create crawler
    crawler = WebCrawler(
        start_url="https://example.com",
        max_depth=2,
        max_pages=50,
        crawl_delay=1.0,
        allow_external=False,
        concurrency_limit=5,
    )

    # Run crawl
    results, stats = await crawler.crawl()

    # Generate report
    reporter = Reporter(results, stats)

    # Get JSON output
    json_report = reporter.to_json()
    print(json_report)

    # Or get HTML output
    html_report = reporter.to_html()
    with open("report.html", "w") as f:
        f.write(html_report)

# Run the crawler
asyncio.run(crawl_site())
```

## Real-World Scenarios

### Website Health Check

Check for broken links on your site:

```bash
webcrawler https://mysite.com \
  --max-depth 3 \
  --max-pages 200 \
  --output html \
  --output-file health_check.html
```

Review the broken links section in the HTML report.

### SEO Audit

Extract titles and meta descriptions:

```bash
webcrawler https://mysite.com \
  --max-depth 2 \
  --output json \
  --output-file seo_data.json
```

Parse the JSON to analyze:
- Missing titles
- Missing meta descriptions
- Duplicate titles
- Title length issues

### Sitemap Generation

Crawl your site to discover all pages:

```bash
webcrawler https://mysite.com \
  --max-depth 10 \
  --max-pages 1000 \
  --output json \
  --output-file sitemap_data.json
```

Use the JSON output to generate a sitemap.xml file.

### Competitor Analysis

Analyze competitor site structure:

```bash
webcrawler https://competitor.com \
  --max-depth 3 \
  --max-pages 100 \
  --output html \
  --output-file competitor_analysis.html
```

Review the most linked pages and site structure.

## Output Format Details

### JSON Structure

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
        "h2": ["Sub 1", "Sub 2"]
      },
      "internal_links": ["..."],
      "external_links": ["..."],
      "images": [
        {"src": "image.jpg", "alt": "Alt text"}
      ]
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

### CSV Columns

- URL
- Status Code
- Response Time (s)
- Is Broken
- Title
- Meta Description
- H1 Count
- H2 Count
- H3 Count
- Internal Links
- External Links
- Images
- Error

## Tips and Best Practices

1. **Start Small**: Test with `--max-pages 10` first
2. **Respect robots.txt**: The crawler automatically respects robots.txt rules
3. **Use Appropriate Delays**: Default 1 second is usually safe
4. **Monitor Server Load**: Watch the target server's response times
5. **Save to Files**: Large crawls produce lots of output - use `--output-file`
6. **Check Broken Links First**: Use low `--max-pages` to quickly find broken links
7. **Increase Timeout for Slow Sites**: Use `--timeout 60` for slow servers
8. **Custom User Agent**: Identify yourself properly with `--user-agent`

## Troubleshooting

### Connection Errors

```bash
# Increase timeout
webcrawler https://slow-site.com --timeout 60
```

### Too Many Pages

```bash
# Reduce depth instead of page count
webcrawler https://example.com --max-depth 1 --max-pages 1000
```

### Memory Issues

```bash
# Crawl in batches
webcrawler https://example.com --max-pages 100 --output-file batch1.json
webcrawler https://example.com --max-pages 100 --max-depth 2 --output-file batch2.json
```

### Rate Limiting

```bash
# Increase delay and reduce concurrency
webcrawler https://example.com \
  --crawl-delay 3.0 \
  --concurrency 2
```
