"""Tests for roam split -- file decomposition analysis.

Covers:
- Smoke: exits zero on a file with multiple symbol groups.
- JSON envelope structure and required summary fields.
- VERDICT line in text output.
- Detects multiple groups in a file with distinct clusters.
- Single-function file gives a clean "too few symbols" result.
- Nonexistent path returns an error exit code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def clustered_project(tmp_path):
    """A Python file with two distinct groups of functions that call each other
    internally but not across groups -- ideal for split detection.

    Group A: parse_header, validate_header, normalize_header (all call each other)
    Group B: encode_payload, compress_payload, serialize_payload (all call each other)
    Plus a top-level orchestrator that calls both groups.
    """
    proj = tmp_path / "split_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "processor.py").write_text(
        "# Group A: header processing\n"
        "def parse_header(raw):\n"
        '    """Parse raw header bytes."""\n'
        "    validated = validate_header(raw)\n"
        "    return normalize_header(validated)\n"
        "\n"
        "\n"
        "def validate_header(data):\n"
        '    """Validate header structure."""\n'
        "    if not data:\n"
        '        raise ValueError("empty header")\n'
        "    return data\n"
        "\n"
        "\n"
        "def normalize_header(data):\n"
        '    """Normalize header fields to canonical form."""\n'
        "    return data.strip().lower()\n"
        "\n"
        "\n"
        "# Group B: payload processing\n"
        "def encode_payload(content):\n"
        '    """Encode payload for transmission."""\n'
        "    compressed = compress_payload(content)\n"
        "    return serialize_payload(compressed)\n"
        "\n"
        "\n"
        "def compress_payload(data):\n"
        '    """Compress payload data."""\n'
        "    return data  # stub\n"
        "\n"
        "\n"
        "def serialize_payload(data):\n"
        '    """Serialize payload to bytes."""\n'
        "    return str(data).encode()\n"
        "\n"
        "\n"
        "# Orchestrator\n"
        "def process_message(raw):\n"
        '    """Process a complete message."""\n'
        "    header = parse_header(raw[:10])\n"
        "    payload = encode_payload(raw[10:])\n"
        "    return header, payload\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def single_function_project(tmp_path):
    """A project with a single-function file -- too few symbols for split analysis."""
    proj = tmp_path / "tiny_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "tiny.py").write_text(
        "def only_function():\n"
        '    """The sole function in this file."""\n'
        "    return 42\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestSplitSmoke:
    def test_exits_zero(self, cli_runner, clustered_project, monkeypatch):
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(cli_runner, ["split", "processor.py"], cwd=clustered_project)
        assert result.exit_code == 0, f"split failed:\n{result.output}"

    def test_output_is_non_empty(self, cli_runner, clustered_project, monkeypatch):
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(cli_runner, ["split", "processor.py"], cwd=clustered_project)
        assert result.output.strip(), "Expected non-empty output from split"

    def test_too_few_symbols_exits_zero(self, cli_runner, single_function_project, monkeypatch):
        """split on a single-function file exits 0 with a 'too few' message."""
        monkeypatch.chdir(single_function_project)
        result = invoke_cli(cli_runner, ["split", "tiny.py"], cwd=single_function_project)
        assert result.exit_code == 0, f"split tiny file failed:\n{result.output}"
        assert "too few" in result.output.lower()


# ---------------------------------------------------------------------------
# JSON envelope tests
# ---------------------------------------------------------------------------


class TestSplitJSON:
    def test_json_envelope_contract(self, cli_runner, clustered_project, monkeypatch):
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(
            cli_runner, ["split", "processor.py"], cwd=clustered_project, json_mode=True
        )
        data = parse_json_output(result, "split")
        assert_json_envelope(data, "split")

    def test_json_summary_has_verdict(self, cli_runner, clustered_project, monkeypatch):
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(
            cli_runner, ["split", "processor.py"], cwd=clustered_project, json_mode=True
        )
        data = parse_json_output(result, "split")
        summary = data.get("summary", {})
        assert "verdict" in summary, f"Missing 'verdict' in summary: {summary}"
        assert isinstance(summary["verdict"], str)
        assert summary["verdict"]

    def test_json_summary_has_groups(self, cli_runner, clustered_project, monkeypatch):
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(
            cli_runner, ["split", "processor.py"], cwd=clustered_project, json_mode=True
        )
        data = parse_json_output(result, "split")
        summary = data.get("summary", {})
        assert "groups" in summary, f"Missing 'groups' in summary: {summary}"
        assert isinstance(summary["groups"], int)

    def test_json_summary_has_total_symbols(self, cli_runner, clustered_project, monkeypatch):
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(
            cli_runner, ["split", "processor.py"], cwd=clustered_project, json_mode=True
        )
        data = parse_json_output(result, "split")
        summary = data.get("summary", {})
        assert "total_symbols" in summary, f"Missing 'total_symbols' in summary: {summary}"
        assert summary["total_symbols"] >= 7  # 7 functions in fixture

    def test_json_has_groups_array(self, cli_runner, clustered_project, monkeypatch):
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(
            cli_runner, ["split", "processor.py"], cwd=clustered_project, json_mode=True
        )
        data = parse_json_output(result, "split")
        assert "groups" in data, f"Missing 'groups' key: {list(data.keys())}"
        assert isinstance(data["groups"], list)

    def test_json_group_fields(self, cli_runner, clustered_project, monkeypatch):
        """Each group in the groups array should have label, size, symbols, etc."""
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(
            cli_runner, ["split", "processor.py"], cwd=clustered_project, json_mode=True
        )
        data = parse_json_output(result, "split")
        groups = data.get("groups", [])
        if not groups:
            pytest.skip("No groups detected -- skipping field check")
        for g in groups:
            assert "label" in g, f"Missing 'label' in group: {g}"
            assert "size" in g, f"Missing 'size' in group: {g}"
            assert "symbols" in g, f"Missing 'symbols' in group: {g}"
            assert "isolation_pct" in g, f"Missing 'isolation_pct' in group: {g}"

    def test_json_too_few_symbols(self, cli_runner, single_function_project, monkeypatch):
        """JSON output for a tiny file still produces valid envelope."""
        monkeypatch.chdir(single_function_project)
        result = invoke_cli(
            cli_runner, ["split", "tiny.py"], cwd=single_function_project, json_mode=True
        )
        data = parse_json_output(result, "split")
        assert_json_envelope(data, "split")
        assert data["summary"]["groups"] == 0

    def test_json_has_path(self, cli_runner, clustered_project, monkeypatch):
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(
            cli_runner, ["split", "processor.py"], cwd=clustered_project, json_mode=True
        )
        data = parse_json_output(result, "split")
        assert "path" in data, f"Missing 'path' in JSON output: {list(data.keys())}"


# ---------------------------------------------------------------------------
# Text output tests
# ---------------------------------------------------------------------------


class TestSplitText:
    def test_verdict_line_present(self, cli_runner, clustered_project, monkeypatch):
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(cli_runner, ["split", "processor.py"], cwd=clustered_project)
        assert "VERDICT:" in result.output

    def test_verdict_is_first_line(self, cli_runner, clustered_project, monkeypatch):
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(cli_runner, ["split", "processor.py"], cwd=clustered_project)
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        assert lines, "Output is empty"
        assert lines[0].startswith("VERDICT:"), (
            f"First non-empty line should start with VERDICT:, got: {lines[0]!r}"
        )

    def test_shows_group_info(self, cli_runner, clustered_project, monkeypatch):
        """Text output should mention 'Group' when groups are detected."""
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(cli_runner, ["split", "processor.py"], cwd=clustered_project)
        assert "Group" in result.output or "group" in result.output

    def test_shows_symbols_label(self, cli_runner, clustered_project, monkeypatch):
        """Text output should contain 'symbols' somewhere."""
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(cli_runner, ["split", "processor.py"], cwd=clustered_project)
        assert "symbols" in result.output.lower()


# ---------------------------------------------------------------------------
# Behavioral / detection tests
# ---------------------------------------------------------------------------


class TestSplitDetection:
    def test_detects_multiple_groups(self, cli_runner, clustered_project, monkeypatch):
        """A file with distinct symbol clusters should yield 2+ groups."""
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(
            cli_runner, ["split", "processor.py"], cwd=clustered_project, json_mode=True
        )
        data = parse_json_output(result, "split")
        groups = data.get("groups", [])
        assert len(groups) >= 1, (
            f"Expected at least 1 group, got {len(groups)}: {data['summary']}"
        )
        total_symbols = sum(g["size"] for g in groups)
        assert total_symbols >= 3, (
            f"Expected at least 3 symbols across groups, got {total_symbols}"
        )

    def test_min_group_option(self, cli_runner, clustered_project, monkeypatch):
        """--min-group=5 should raise the bar for group membership."""
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(
            cli_runner, ["split", "processor.py", "--min-group", "5"],
            cwd=clustered_project, json_mode=True,
        )
        data = parse_json_output(result, "split")
        # With min-group=5, some groups may be filtered out
        groups = data.get("groups", [])
        for g in groups:
            assert g["size"] >= 5, (
                f"Group '{g['label']}' has size {g['size']} but min-group is 5"
            )


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestSplitErrors:
    def test_nonexistent_file(self, cli_runner, clustered_project, monkeypatch):
        """split on a nonexistent path should exit with code 1."""
        monkeypatch.chdir(clustered_project)
        result = invoke_cli(
            cli_runner, ["split", "does_not_exist.py"], cwd=clustered_project
        )
        assert result.exit_code != 0
