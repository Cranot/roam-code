"""Tests for the CLI interface."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from webcrawler.cli import main
from webcrawler.models import CrawlResult, CrawlStats, PageData


def _mock_result() -> CrawlResult:
    page = PageData(
        url="https://example.com",
        status_code=200,
        response_time=0.2,
        title="Example",
        depth=0,
    )
    return CrawlResult(
        start_url="https://example.com",
        pages=[page],
        stats=CrawlStats(total_pages=1, total_response_time=0.2),
    )


class TestCli:
    def test_json_output(self) -> None:
        runner = CliRunner()
        with patch("webcrawler.cli.asyncio.run", return_value=_mock_result()):
            result = runner.invoke(main, [
                "https://example.com", "-f", "json", "-d", "0",
            ])
        assert result.exit_code == 0
        # CliRunner mixes stderr into output; extract the JSON portion
        json_start = result.output.index("{")
        data = json.loads(result.output[json_start:])
        assert data["summary"]["total_pages"] == 1

    def test_csv_output(self) -> None:
        runner = CliRunner()
        with patch("webcrawler.cli.asyncio.run", return_value=_mock_result()):
            result = runner.invoke(main, [
                "https://example.com", "-f", "csv", "-d", "0",
            ])
        assert result.exit_code == 0
        assert "url,status_code" in result.output

    def test_html_output(self) -> None:
        runner = CliRunner()
        with patch("webcrawler.cli.asyncio.run", return_value=_mock_result()):
            result = runner.invoke(main, [
                "https://example.com", "-f", "html", "-d", "0",
            ])
        assert result.exit_code == 0
        assert "<!DOCTYPE html>" in result.output

    def test_output_to_file(self, tmp_path) -> None:
        runner = CliRunner()
        outfile = str(tmp_path / "report.json")
        with patch("webcrawler.cli.asyncio.run", return_value=_mock_result()):
            result = runner.invoke(main, [
                "https://example.com", "-f", "json", "-o", outfile, "-d", "0",
            ])
        assert result.exit_code == 0
        assert "Report written to" in result.output  # stderr message captured by runner
        with open(outfile) as f:
            data = json.loads(f.read())
        assert data["summary"]["total_pages"] == 1

    def test_stderr_messages(self) -> None:
        runner = CliRunner()
        with patch("webcrawler.cli.asyncio.run", return_value=_mock_result()):
            result = runner.invoke(main, [
                "https://example.com", "-f", "json", "-d", "0",
            ])
        assert result.exit_code == 0
        # CliRunner captures stderr mixed into output
        assert "Starting crawl" in result.output
        assert "Crawl complete" in result.output

    def test_default_options(self) -> None:
        runner = CliRunner()
        with patch("webcrawler.cli.asyncio.run", return_value=_mock_result()) as mock_run:
            with patch("webcrawler.cli.Crawler") as mock_crawler_cls:
                mock_crawler_cls.return_value.start_url = "https://example.com"
                mock_crawler_cls.return_value.crawl = AsyncMock(return_value=_mock_result())
                mock_run.return_value = _mock_result()
                result = runner.invoke(main, ["https://example.com"])
        assert result.exit_code == 0

    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Crawl a website" in result.output
        assert "--max-depth" in result.output
        assert "--max-pages" in result.output
        assert "--output-format" in result.output
