from __future__ import annotations

import csv
import json
from html import escape
from pathlib import Path

from .models import CrawlReport


def write_report(report: CrawlReport, output_format: str, output_path: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    output_format_normalized = output_format.lower()
    if output_format_normalized == "json":
        _write_json(report, path)
    elif output_format_normalized == "csv":
        _write_csv(report, path)
    elif output_format_normalized == "html":
        _write_html(report, path)
    else:
        raise ValueError(f"Unsupported format: {output_format}")
    return path


def _write_json(report: CrawlReport, path: Path) -> None:
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def _write_csv(report: CrawlReport, path: Path) -> None:
    fieldnames = [
        "url",
        "requested_url",
        "depth",
        "status",
        "response_time_ms",
        "title",
        "meta_description",
        "headings",
        "internal_links_count",
        "external_links_count",
        "images_count",
        "error",
        "redirect_chain",
    ]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for page in report.pages:
            writer.writerow(
                {
                    "url": page.url,
                    "requested_url": page.requested_url,
                    "depth": page.depth,
                    "status": page.status,
                    "response_time_ms": page.response_time_ms,
                    "title": page.title,
                    "meta_description": page.meta_description,
                    "headings": " | ".join(
                        f"{heading.level}:{heading.text}" for heading in page.headings
                    ),
                    "internal_links_count": len(page.internal_links),
                    "external_links_count": len(page.external_links),
                    "images_count": len(page.images),
                    "error": page.error or "",
                    "redirect_chain": " -> ".join(page.redirect_chain),
                }
            )


def _write_html(report: CrawlReport, path: Path) -> None:
    top_links = "".join(
        f"<li><code>{escape(url)}</code> ({count} links)</li>"
        for url, count in report.summary.most_linked_pages
    )
    broken_rows = "".join(
        (
            "<tr>"
            f"<td><code>{escape(item.url)}</code></td>"
            f"<td><code>{escape(item.source_page)}</code></td>"
            f"<td>{item.status if item.status is not None else ''}</td>"
            f"<td>{escape(item.error or '')}</td>"
            "</tr>"
        )
        for item in report.broken_links
    )
    page_rows = "".join(
        (
            "<tr>"
            f"<td><code>{escape(page.url)}</code></td>"
            f"<td>{page.status if page.status is not None else ''}</td>"
            f"<td>{page.response_time_ms if page.response_time_ms is not None else ''}</td>"
            f"<td>{escape(page.title)}</td>"
            f"<td>{len(page.internal_links)}</td>"
            f"<td>{len(page.external_links)}</td>"
            f"<td>{len(page.images)}</td>"
            f"<td>{escape(page.error or '')}</td>"
            "</tr>"
        )
        for page in report.pages
    )

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Web Crawler Report</title>
    <style>
      :root {{
        --bg: #f5f7fb;
        --card: #ffffff;
        --ink: #1f2937;
        --accent: #0f766e;
        --muted: #6b7280;
        --line: #d1d5db;
      }}
      body {{
        margin: 0;
        font-family: "Segoe UI", "Helvetica Neue", sans-serif;
        color: var(--ink);
        background: radial-gradient(circle at top, #d1fae5, var(--bg) 55%);
      }}
      main {{
        max-width: 1100px;
        margin: 0 auto;
        padding: 24px;
      }}
      h1 {{
        margin-top: 0;
        color: var(--accent);
      }}
      .panel {{
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 16px;
        box-shadow: 0 6px 18px rgba(15, 118, 110, 0.08);
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
      }}
      th, td {{
        border: 1px solid var(--line);
        text-align: left;
        padding: 8px;
        vertical-align: top;
      }}
      th {{
        background: #ecfdf5;
      }}
      code {{
        color: #0f172a;
      }}
      .muted {{
        color: var(--muted);
      }}
      @media (max-width: 768px) {{
        table {{
          font-size: 12px;
        }}
        th, td {{
          padding: 6px;
        }}
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Web Crawler Report</h1>
      <p class="muted">Start URL: <code>{escape(report.start_url)}</code></p>

      <section class="panel">
        <h2>Summary</h2>
        <ul>
          <li>Total pages crawled: {report.summary.total_pages_crawled}</li>
          <li>Broken links found: {report.summary.broken_links_found}</li>
          <li>Average response time (ms): {report.summary.average_response_time_ms}</li>
        </ul>
        <h3>Most Linked Pages</h3>
        <ul>{top_links or "<li>None</li>"}</ul>
      </section>

      <section class="panel">
        <h2>Pages</h2>
        <table>
          <thead>
            <tr>
              <th>URL</th>
              <th>Status</th>
              <th>Response (ms)</th>
              <th>Title</th>
              <th>Internal Links</th>
              <th>External Links</th>
              <th>Images</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {page_rows}
          </tbody>
        </table>
      </section>

      <section class="panel">
        <h2>Broken Links</h2>
        <table>
          <thead>
            <tr>
              <th>URL</th>
              <th>Source Page</th>
              <th>Status</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {broken_rows}
          </tbody>
        </table>
      </section>
    </main>
  </body>
</html>
"""
    path.write_text(html, encoding="utf-8")
