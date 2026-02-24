"""Output formatters for crawl results."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Any

from webcrawler.crawler import CrawlResult, CrawlStats


class Reporter:
    """Generate reports in various formats from crawl results."""

    def __init__(self, results: list[CrawlResult], stats: CrawlStats):
        """
        Initialize reporter.

        Args:
            results: List of crawl results
            stats: Crawl statistics
        """
        self.results = results
        self.stats = stats

    def to_json(self) -> str:
        """
        Generate JSON report.

        Returns:
            JSON string with structured report
        """
        # Build summary
        summary = {
            'total_pages_crawled': self.stats.total_pages_crawled,
            'total_pages_attempted': self.stats.total_pages_attempted,
            'broken_links_count': self.stats.total_broken_links,
            'average_response_time': round(self.stats.average_response_time, 3),
            'crawl_duration': round(self.stats.crawl_duration, 2),
        }

        # Get top 10 most linked pages
        sorted_pages = sorted(
            self.stats.most_linked_pages.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]
        most_linked = [
            {'url': url, 'link_count': count}
            for url, count in sorted_pages
        ]

        # Build pages data
        pages = []
        for result in self.results:
            page_data: dict[str, Any] = {
                'url': result.url,
                'status_code': result.status_code,
                'response_time': round(result.response_time, 3) if result.response_time else None,
                'is_broken': result.is_broken,
                'error': result.error,
            }

            if result.content:
                page_data['title'] = result.content.title
                page_data['meta_description'] = result.content.meta_description
                page_data['headings'] = result.content.headings
                page_data['internal_links_count'] = len(result.content.internal_links)
                page_data['external_links_count'] = len(result.content.external_links)
                page_data['images_count'] = len(result.content.images)
                page_data['internal_links'] = result.content.internal_links
                page_data['external_links'] = result.content.external_links
                page_data['images'] = result.content.images

            pages.append(page_data)

        # Build broken links data
        broken_links = [
            {
                'url': url,
                'status_code': status,
                'found_on': source
            }
            for url, status, source in self.stats.broken_links
        ]

        report = {
            'summary': summary,
            'most_linked_pages': most_linked,
            'pages': pages,
            'broken_links': broken_links,
        }

        return json.dumps(report, indent=2)

    def to_csv(self) -> str:
        """
        Generate CSV report.

        Returns:
            CSV string with flat table of results
        """
        output = StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow([
            'URL',
            'Status Code',
            'Response Time (s)',
            'Is Broken',
            'Title',
            'Meta Description',
            'H1 Count',
            'H2 Count',
            'H3 Count',
            'Internal Links',
            'External Links',
            'Images',
            'Error',
        ])

        # Write data rows
        for result in self.results:
            row = [
                result.url,
                result.status_code or '',
                round(result.response_time, 3) if result.response_time else '',
                'Yes' if result.is_broken else 'No',
            ]

            if result.content:
                row.extend([
                    result.content.title or '',
                    result.content.meta_description or '',
                    len(result.content.headings.get('h1', [])),
                    len(result.content.headings.get('h2', [])),
                    len(result.content.headings.get('h3', [])),
                    len(result.content.internal_links),
                    len(result.content.external_links),
                    len(result.content.images),
                ])
            else:
                row.extend(['', '', 0, 0, 0, 0, 0, 0])

            row.append(result.error or '')
            writer.writerow(row)

        return output.getvalue()

    def to_html(self) -> str:
        """
        Generate HTML report with visual formatting.

        Returns:
            HTML string with formatted report
        """
        # Summary statistics
        summary_html = f"""
        <div class="summary">
            <h2>Crawl Summary</h2>
            <table>
                <tr><th>Total Pages Crawled</th><td>{self.stats.total_pages_crawled}</td></tr>
                <tr><th>Total Pages Attempted</th><td>{self.stats.total_pages_attempted}</td></tr>
                <tr><th>Broken Links Found</th><td>{self.stats.total_broken_links}</td></tr>
                <tr><th>Average Response Time</th><td>{self.stats.average_response_time:.3f}s</td></tr>
                <tr><th>Total Crawl Duration</th><td>{self.stats.crawl_duration:.2f}s</td></tr>
            </table>
        </div>
        """

        # Most linked pages
        sorted_pages = sorted(
            self.stats.most_linked_pages.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]

        most_linked_html = """
        <div class="most-linked">
            <h2>Most Linked Pages</h2>
            <table>
                <tr><th>URL</th><th>Link Count</th></tr>
        """
        for url, count in sorted_pages:
            most_linked_html += f"<tr><td>{url}</td><td>{count}</td></tr>\n"
        most_linked_html += "</table></div>"

        # Broken links
        broken_html = """
        <div class="broken-links">
            <h2>Broken Links</h2>
            <table>
                <tr><th>URL</th><th>Status</th><th>Found On</th></tr>
        """
        for url, status, source in self.stats.broken_links:
            broken_html += f"<tr><td>{url}</td><td>{status or 'Error'}</td><td>{source}</td></tr>\n"
        broken_html += "</table></div>"

        # Pages details
        pages_html = """
        <div class="pages">
            <h2>Crawled Pages</h2>
        """
        for result in self.results:
            status_class = 'broken' if result.is_broken else 'success'
            pages_html += f"""
            <div class="page {status_class}">
                <h3>{result.url}</h3>
                <p><strong>Status:</strong> {result.status_code or 'Error'} |
                   <strong>Response Time:</strong> {result.response_time:.3f}s</p>
            """

            if result.content:
                pages_html += f"""
                <p><strong>Title:</strong> {result.content.title or 'N/A'}</p>
                <p><strong>Description:</strong> {result.content.meta_description or 'N/A'}</p>
                <p><strong>Headings:</strong>
                   H1: {len(result.content.headings.get('h1', []))},
                   H2: {len(result.content.headings.get('h2', []))},
                   H3: {len(result.content.headings.get('h3', []))}</p>
                <p><strong>Links:</strong>
                   Internal: {len(result.content.internal_links)},
                   External: {len(result.content.external_links)}</p>
                <p><strong>Images:</strong> {len(result.content.images)}</p>
                """

            if result.error:
                pages_html += f"<p class='error'><strong>Error:</strong> {result.error}</p>"

            pages_html += "</div>\n"

        pages_html += "</div>"

        # Complete HTML document
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Web Crawler Report</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        h1 {{
            color: #333;
            border-bottom: 3px solid #4CAF50;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #555;
            margin-top: 30px;
        }}
        .summary, .most-linked, .broken-links, .pages {{
            background: white;
            padding: 20px;
            margin: 20px 0;
            border-radius: 5px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        th, td {{
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background-color: #4CAF50;
            color: white;
        }}
        .page {{
            margin: 15px 0;
            padding: 15px;
            border-left: 4px solid #4CAF50;
            background: #f9f9f9;
        }}
        .page.broken {{
            border-left-color: #f44336;
        }}
        .page h3 {{
            margin-top: 0;
            color: #333;
            word-break: break-all;
        }}
        .error {{
            color: #f44336;
        }}
        .success {{
            color: #4CAF50;
        }}
    </style>
</head>
<body>
    <h1>Web Crawler Report</h1>
    {summary_html}
    {most_linked_html}
    {broken_html}
    {pages_html}
</body>
</html>"""

        return html
