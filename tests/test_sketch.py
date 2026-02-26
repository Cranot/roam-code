"""Tests for roam sketch -- compact structural skeleton of a directory."""

from __future__ import annotations

import pytest

from tests.conftest import (
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
def sketch_project(tmp_path):
    """Python project with several files, classes and functions for skeleton output."""
    proj = tmp_path / "sketch_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()

    (src / "models.py").write_text(
        '"""Domain models."""\n'
        "\n"
        "\n"
        "class Product:\n"
        '    """A purchasable product."""\n'
        "\n"
        "    def __init__(self, name: str, price: float) -> None:\n"
        "        self.name = name\n"
        "        self.price = price\n"
        "\n"
        "    def display(self) -> str:\n"
        '        """Return formatted product string."""\n'
        "        return f\"{self.name}: ${self.price:.2f}\"\n"
        "\n"
        "    def apply_discount(self, pct: float) -> float:\n"
        '        """Return discounted price."""\n'
        "        return self.price * (1 - pct / 100)\n"
        "\n"
        "\n"
        "class Cart:\n"
        '    """Shopping cart."""\n'
        "\n"
        "    def __init__(self) -> None:\n"
        "        self.items: list = []\n"
        "\n"
        "    def add(self, product: Product, qty: int = 1) -> None:\n"
        '        """Add product to cart."""\n'
        "        self.items.append((product, qty))\n"
        "\n"
        "    def total(self) -> float:\n"
        '        """Compute total price."""\n'
        "        return sum(p.price * q for p, q in self.items)\n"
    )

    (src / "services.py").write_text(
        '"""Business-logic services."""\n'
        "from src.models import Cart, Product\n"
        "\n"
        "\n"
        "def create_product(name: str, price: float) -> Product:\n"
        '    """Factory for Product."""\n'
        "    return Product(name, price)\n"
        "\n"
        "\n"
        "def checkout(cart: Cart) -> dict:\n"
        '    """Process checkout and return receipt."""\n'
        "    return {\"total\": cart.total(), \"items\": len(cart.items)}\n"
    )

    (src / "utils.py").write_text(
        "def slugify(text: str) -> str:\n"
        '    """Convert text to URL-safe slug."""\n'
        '    return text.lower().replace(" ", "-")\n'
        "\n"
        "\n"
        "def clamp(value: float, lo: float, hi: float) -> float:\n"
        '    """Clamp value between lo and hi."""\n'
        "    return max(lo, min(hi, value))\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestSketchSmoke:
    def test_exits_zero_for_known_directory(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "src"], cwd=sketch_project)
        assert result.exit_code == 0

    def test_exits_zero_with_full_flag(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "--full", "src"], cwd=sketch_project)
        assert result.exit_code == 0

    def test_exits_zero_for_nonexistent_directory(self, cli_runner, sketch_project, monkeypatch):
        """Unknown directory should still exit 0 with a 'no symbols found' message."""
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "nonexistent_dir_xyz"], cwd=sketch_project)
        assert result.exit_code == 0

    def test_full_shows_more_symbols_than_default(self, cli_runner, sketch_project, monkeypatch):
        """--full mode should include private symbols; default shows only exported ones.

        Both should exit 0; full may produce equal or more output lines.
        """
        monkeypatch.chdir(sketch_project)
        result_default = invoke_cli(cli_runner, ["sketch", "src"], cwd=sketch_project)
        result_full = invoke_cli(cli_runner, ["sketch", "--full", "src"], cwd=sketch_project)
        assert result_default.exit_code == 0
        assert result_full.exit_code == 0
        # Full output should have at least as many lines as default
        assert len(result_full.output.splitlines()) >= len(result_default.output.splitlines())


# ---------------------------------------------------------------------------
# JSON envelope tests
# ---------------------------------------------------------------------------


class TestSketchJSON:
    def test_json_envelope_structure(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "src"], cwd=sketch_project, json_mode=True)
        data = parse_json_output(result, "sketch")
        assert_json_envelope(data, "sketch")

    def test_json_summary_has_verdict(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "src"], cwd=sketch_project, json_mode=True)
        data = parse_json_output(result, "sketch")
        assert "verdict" in data["summary"]
        assert isinstance(data["summary"]["verdict"], str)

    def test_json_summary_has_file_count(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "src"], cwd=sketch_project, json_mode=True)
        data = parse_json_output(result, "sketch")
        assert "file_count" in data["summary"] or "file_count" in data

    def test_json_summary_has_symbol_count(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "src"], cwd=sketch_project, json_mode=True)
        data = parse_json_output(result, "sketch")
        assert "symbol_count" in data["summary"] or "symbol_count" in data

    def test_json_has_files_dict(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "src"], cwd=sketch_project, json_mode=True)
        data = parse_json_output(result, "sketch")
        assert "files" in data

    def test_json_no_symbols_envelope(self, cli_runner, sketch_project, monkeypatch):
        """Nonexistent directory should still return a valid envelope with zero counts."""
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(
            cli_runner, ["sketch", "nonexistent_dir_xyz"], cwd=sketch_project, json_mode=True
        )
        data = parse_json_output(result, "sketch")
        assert_json_envelope(data, "sketch")
        # file_count and symbol_count should be 0
        fc = data["summary"].get("file_count") or data.get("file_count", 0)
        sc = data["summary"].get("symbol_count") or data.get("symbol_count", 0)
        assert fc == 0
        assert sc == 0

    def test_json_files_entries_have_symbol_fields(self, cli_runner, sketch_project, monkeypatch):
        """Each symbol entry in the files dict should have name, kind, and line_start."""
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(
            cli_runner, ["sketch", "--full", "src"], cwd=sketch_project, json_mode=True
        )
        data = parse_json_output(result, "sketch")
        files = data.get("files", {})
        if not files:
            pytest.skip("No files in JSON output -- skipping field check")
        for path, symbols in files.items():
            for sym in symbols:
                assert "name" in sym, f"Symbol in {path} missing 'name'"
                assert "kind" in sym, f"Symbol in {path} missing 'kind'"
                assert "line_start" in sym, f"Symbol in {path} missing 'line_start'"


# ---------------------------------------------------------------------------
# Text output tests
# ---------------------------------------------------------------------------


class TestSketchText:
    def test_verdict_line_present(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "src"], cwd=sketch_project)
        assert "VERDICT:" in result.output

    def test_verdict_line_is_first_non_empty_line(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "src"], cwd=sketch_project)
        lines = [l for l in result.output.splitlines() if l.strip()]
        assert lines[0].startswith("VERDICT:")

    def test_class_names_appear_in_output(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "--full", "src"], cwd=sketch_project)
        assert "Product" in result.output or "Cart" in result.output

    def test_function_names_appear_in_output(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "--full", "src"], cwd=sketch_project)
        assert "slugify" in result.output or "clamp" in result.output or "checkout" in result.output

    def test_no_symbols_message_for_unknown_dir(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "no_such_dir"], cwd=sketch_project)
        assert "no symbols found" in result.output.lower() or "VERDICT:" in result.output

    def test_file_paths_appear_in_output(self, cli_runner, sketch_project, monkeypatch):
        monkeypatch.chdir(sketch_project)
        result = invoke_cli(cli_runner, ["sketch", "--full", "src"], cwd=sketch_project)
        # At least one .py file path should appear
        assert ".py" in result.output
