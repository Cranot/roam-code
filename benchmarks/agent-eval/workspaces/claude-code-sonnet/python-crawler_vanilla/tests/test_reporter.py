"""Tests for output reporters."""

from __future__ import annotations

import csv
import json
from io import StringIO

import pytest

from webcrawler.crawler import CrawlResult, CrawlStats
from webcrawler.parser import PageContent
from webcrawler.reporter import Reporter


class TestReporter:
    """Tests for report generation."""

    @pytest.fixture
    def sample_results(self):
        """Create sample crawl results."""
        content1 = PageContent(
            url="https://example.com/page1",
            title="Page 1",
            meta_description="Description 1",
            headings={'h1': ['Heading 1'], 'h2': ['Heading 2a', 'Heading 2b'], 'h3': [], 'h4': [], 'h5': [], 'h6': []},
            internal_links=["https://example.com/page2"],
            external_links=["https://other.com"],
            images=[{'src': 'image1.jpg', 'alt': 'Image 1'}],
        )

        result1 = CrawlResult(
            url="https://example.com/page1",
            status_code=200,
            content=content1,
            response_time=0.5,
        )

        result2 = CrawlResult(
            url="https://example.com/broken",
            status_code=404,
            is_broken=True,
            response_time=0.3,
        )

        return [result1, result2]

    @pytest.fixture
    def sample_stats(self):
        """Create sample statistics."""
        stats = CrawlStats()
        stats.total_pages_crawled = 1
        stats.total_pages_attempted = 2
        stats.broken_links = [("https://example.com/broken", 404, "https://example.com/page1")]
        stats.response_times = [0.5, 0.3]
        stats.most_linked_pages = {
            "https://example.com/page2": 3,
            "https://example.com/page1": 1,
        }
        return stats

    def test_json_output(self, sample_results, sample_stats):
        """Test JSON report generation."""
        reporter = Reporter(sample_results, sample_stats)
        json_output = reporter.to_json()

        data = json.loads(json_output)

        assert 'summary' in data
        assert 'pages' in data
        assert 'broken_links' in data
        assert 'most_linked_pages' in data

        assert data['summary']['total_pages_crawled'] == 1
        assert data['summary']['broken_links_count'] == 1
        assert len(data['pages']) == 2
        assert len(data['broken_links']) == 1

    def test_json_page_details(self, sample_results, sample_stats):
        """Test JSON includes page details."""
        reporter = Reporter(sample_results, sample_stats)
        json_output = reporter.to_json()

        data = json.loads(json_output)
        page1 = data['pages'][0]

        assert page1['url'] == "https://example.com/page1"
        assert page1['title'] == "Page 1"
        assert page1['meta_description'] == "Description 1"
        assert page1['status_code'] == 200
        assert page1['internal_links_count'] == 1
        assert page1['external_links_count'] == 1
        assert page1['images_count'] == 1

    def test_csv_output(self, sample_results, sample_stats):
        """Test CSV report generation."""
        reporter = Reporter(sample_results, sample_stats)
        csv_output = reporter.to_csv()

        reader = csv.reader(StringIO(csv_output))
        rows = list(reader)

        # Should have header + 2 data rows
        assert len(rows) == 3

        # Check header
        assert 'URL' in rows[0]
        assert 'Status Code' in rows[0]
        assert 'Title' in rows[0]

        # Check data row
        assert rows[1][0] == "https://example.com/page1"
        assert rows[1][1] == "200"

    def test_html_output(self, sample_results, sample_stats):
        """Test HTML report generation."""
        reporter = Reporter(sample_results, sample_stats)
        html_output = reporter.to_html()

        assert '<!DOCTYPE html>' in html_output
        assert '<html>' in html_output
        assert 'Crawl Summary' in html_output
        assert 'Most Linked Pages' in html_output
        assert 'Broken Links' in html_output
        assert 'https://example.com/page1' in html_output

    def test_html_includes_styles(self, sample_results, sample_stats):
        """Test HTML report includes CSS styles."""
        reporter = Reporter(sample_results, sample_stats)
        html_output = reporter.to_html()

        assert '<style>' in html_output
        assert 'font-family' in html_output

    def test_most_linked_pages_sorted(self, sample_results, sample_stats):
        """Test most linked pages are sorted by count."""
        reporter = Reporter(sample_results, sample_stats)
        json_output = reporter.to_json()

        data = json.loads(json_output)
        most_linked = data['most_linked_pages']

        # Should be sorted in descending order
        assert most_linked[0]['url'] == "https://example.com/page2"
        assert most_linked[0]['link_count'] == 3

    def test_broken_links_details(self, sample_results, sample_stats):
        """Test broken links include all details."""
        reporter = Reporter(sample_results, sample_stats)
        json_output = reporter.to_json()

        data = json.loads(json_output)
        broken = data['broken_links'][0]

        assert broken['url'] == "https://example.com/broken"
        assert broken['status_code'] == 404
        assert broken['found_on'] == "https://example.com/page1"

    def test_average_response_time(self, sample_results, sample_stats):
        """Test average response time calculation."""
        reporter = Reporter(sample_results, sample_stats)
        json_output = reporter.to_json()

        data = json.loads(json_output)

        # (0.5 + 0.3) / 2 = 0.4
        assert data['summary']['average_response_time'] == 0.4

    def test_empty_results(self):
        """Test reporter handles empty results."""
        reporter = Reporter([], CrawlStats())
        json_output = reporter.to_json()

        data = json.loads(json_output)
        assert data['summary']['total_pages_crawled'] == 0
        assert len(data['pages']) == 0

    def test_csv_escapes_special_characters(self):
        """Test CSV properly escapes special characters."""
        content = PageContent(
            url="https://example.com/page",
            title='Title with "quotes" and, commas',
            meta_description="Description",
        )

        result = CrawlResult(
            url="https://example.com/page",
            status_code=200,
            content=content,
        )

        reporter = Reporter([result], CrawlStats())
        csv_output = reporter.to_csv()

        # CSV should handle quotes and commas properly
        assert 'Title with "quotes"' in csv_output or '"Title with ""quotes"" and, commas"' in csv_output
