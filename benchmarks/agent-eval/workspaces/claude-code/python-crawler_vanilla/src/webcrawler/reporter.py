"""Output reporters — JSON, CSV, and HTML formats."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from html import escape
from typing import Any, Dict, List

from webcrawler.models import CrawlResult, PageData


def to_json(result: CrawlResult) -> str:
    """Produce a structured JSON report."""
    data = _build_report_dict(result)
    return json.dumps(data, indent=2, default=str)


def to_csv(result: CrawlResult) -> str:
    """Produce a flat CSV table of all crawled pages."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "url",
        "status_code",
        "response_time",
        "title",
        "meta_description",
        "internal_links",
        "external_links",
        "images",
        "depth",
        "error",
    ])

    all_pages = result.pages + result.broken
    for page in all_pages:
        writer.writerow([
            page.url,
            page.status_code,
            f"{page.response_time:.3f}",
            page.title or "",
            page.meta_description or "",
            len(page.internal_links),
            len(page.external_links),
            len(page.images),
            page.depth,
            page.error or "",
        ])

    return output.getvalue()


def to_html(result: CrawlResult) -> str:
    """Produce a visual HTML report with summary statistics."""
    stats = result.stats
    most_linked = stats.most_linked_pages(10)

    pages_rows = ""
    all_pages = result.pages + result.broken
    for page in all_pages:
        status_cls = "ok" if page.status_code and page.status_code < 400 else "error"
        pages_rows += f"""<tr class="{status_cls}">
  <td><a href="{escape(page.url)}">{escape(page.url)}</a></td>
  <td>{page.status_code}</td>
  <td>{page.response_time:.3f}s</td>
  <td>{escape(page.title or "")}</td>
  <td>{len(page.internal_links)}</td>
  <td>{len(page.external_links)}</td>
  <td>{len(page.images)}</td>
  <td>{page.depth}</td>
  <td>{escape(page.error or "")}</td>
</tr>
"""

    broken_rows = ""
    for page in result.broken:
        broken_rows += f"""<tr>
  <td><a href="{escape(page.url)}">{escape(page.url)}</a></td>
  <td>{page.status_code}</td>
  <td>{escape(page.error or "")}</td>
</tr>
"""

    linked_rows = ""
    for url, count in most_linked:
        linked_rows += f"""<tr>
  <td><a href="{escape(url)}">{escape(url)}</a></td>
  <td>{count}</td>
</tr>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Crawl Report - {escape(result.start_url)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 2rem; color: #333; }}
  h1 {{ color: #1a1a2e; }}
  h2 {{ color: #16213e; margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
  th {{ background: #16213e; color: white; }}
  tr.ok td {{ background: #f0fff0; }}
  tr.error td {{ background: #fff0f0; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin: 1rem 0; }}
  .stat-card {{ background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; padding: 1rem; text-align: center; }}
  .stat-card .value {{ font-size: 2rem; font-weight: bold; color: #16213e; }}
  .stat-card .label {{ color: #6c757d; }}
  a {{ color: #0066cc; }}
</style>
</head>
<body>
<h1>Crawl Report</h1>
<p>Start URL: <a href="{escape(result.start_url)}">{escape(result.start_url)}</a></p>

<h2>Summary Statistics</h2>
<div class="stats">
  <div class="stat-card">
    <div class="value">{stats.total_pages}</div>
    <div class="label">Total Pages Crawled</div>
  </div>
  <div class="stat-card">
    <div class="value">{stats.broken_links}</div>
    <div class="label">Broken Links</div>
  </div>
  <div class="stat-card">
    <div class="value">{stats.average_response_time:.3f}s</div>
    <div class="label">Avg Response Time</div>
  </div>
  <div class="stat-card">
    <div class="value">{len(result.pages)}</div>
    <div class="label">Successful Pages</div>
  </div>
</div>

<h2>All Pages</h2>
<table>
<thead>
<tr><th>URL</th><th>Status</th><th>Time</th><th>Title</th><th>Int. Links</th><th>Ext. Links</th><th>Images</th><th>Depth</th><th>Error</th></tr>
</thead>
<tbody>
{pages_rows}</tbody>
</table>

<h2>Broken Links</h2>
{"<p>No broken links found.</p>" if not result.broken else f'''<table>
<thead>
<tr><th>URL</th><th>Status</th><th>Error</th></tr>
</thead>
<tbody>
{broken_rows}</tbody>
</table>'''}

<h2>Most Linked Pages</h2>
{"<p>No link data available.</p>" if not most_linked else f'''<table>
<thead>
<tr><th>URL</th><th>Inbound Links</th></tr>
</thead>
<tbody>
{linked_rows}</tbody>
</table>'''}

</body>
</html>
"""


def _build_report_dict(result: CrawlResult) -> Dict[str, Any]:
    """Build the full report as a dictionary."""
    stats = result.stats
    return {
        "start_url": result.start_url,
        "summary": {
            "total_pages": stats.total_pages,
            "broken_links": stats.broken_links,
            "average_response_time": round(stats.average_response_time, 3),
            "most_linked_pages": [
                {"url": url, "inbound_links": count}
                for url, count in stats.most_linked_pages(10)
            ],
        },
        "pages": [_page_to_dict(p) for p in result.pages],
        "broken": [_page_to_dict(p) for p in result.broken],
    }


def _page_to_dict(page: PageData) -> Dict[str, Any]:
    """Convert a PageData instance to a serializable dict."""
    d: Dict[str, Any] = {
        "url": page.url,
        "status_code": page.status_code,
        "response_time": round(page.response_time, 3),
        "depth": page.depth,
    }
    if page.title:
        d["title"] = page.title
    if page.meta_description:
        d["meta_description"] = page.meta_description
    if page.headings:
        d["headings"] = page.headings
    if page.internal_links:
        d["internal_links"] = page.internal_links
    if page.external_links:
        d["external_links"] = page.external_links
    if page.images:
        d["images"] = page.images
    if page.error:
        d["error"] = page.error
    if page.redirect_url:
        d["redirect_url"] = page.redirect_url
    return d
