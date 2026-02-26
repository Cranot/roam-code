"""Tests for roam tour -- codebase onboarding tour with reading order."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def tour_project(tmp_path):
    """Python project with caller/callee relationships for tour testing."""
    proj = tmp_path / "tour_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Foundation layer: utility/model functions called by others
    (proj / "models.py").write_text(
        "class Order:\n"
        '    """An order entity."""\n'
        "    def __init__(self, order_id, total):\n"
        "        self.order_id = order_id\n"
        "        self.total = total\n"
        "\n"
        "    def is_paid(self):\n"
        "        return self.total > 0\n"
        "\n"
        "\n"
        "class Customer:\n"
        '    """A customer entity."""\n'
        "    def __init__(self, customer_id, name):\n"
        "        self.customer_id = customer_id\n"
        "        self.name = name\n"
    )

    # Middle layer: service functions that import models
    (proj / "order_service.py").write_text(
        "from models import Order, Customer\n"
        "\n"
        "\n"
        "def place_order(customer, total):\n"
        '    """Place a new order for a customer."""\n'
        "    order = Order(order_id=1, total=total)\n"
        "    return order\n"
        "\n"
        "\n"
        "def get_order_summary(order):\n"
        '    """Summarize an order."""\n'
        "    return f'Order {order.order_id}: paid={order.is_paid()}'\n"
        "\n"
        "\n"
        "def cancel_order(order):\n"
        '    """Cancel an order."""\n'
        "    pass\n"
    )

    # Top layer: entry point that calls service
    (proj / "main.py").write_text(
        "from order_service import place_order, get_order_summary\n"
        "from models import Customer\n"
        "\n"
        "\n"
        "def run():\n"
        '    """Main entry point."""\n'
        "    customer = Customer(1, 'Alice')\n"
        "    order = place_order(customer, 99.99)\n"
        "    print(get_order_summary(order))\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def minimal_tour_project(tmp_path):
    """Minimal single-file project to verify tour works on small codebases."""
    proj = tmp_path / "mini_tour_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def main():\n"
        '    """Entry point."""\n'
        "    return greet('world')\n"
        "\n"
        "\n"
        "def greet(name):\n"
        '    """Return a greeting."""\n'
        '    return f"Hello, {name}!"\n'
    )
    git_init(proj)
    index_in_process(proj)
    return proj


class TestTourSmoke:
    def test_exits_zero(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project)
        assert result.exit_code == 0

    def test_minimal_project_exits_zero(self, cli_runner, minimal_tour_project, monkeypatch):
        monkeypatch.chdir(minimal_tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=minimal_tour_project)
        assert result.exit_code == 0

    def test_mermaid_flag_exits_zero(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour", "--mermaid"], cwd=tour_project)
        assert result.exit_code == 0


class TestTourJSON:
    def test_json_envelope(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project, json_mode=True)
        data = parse_json_output(result, "tour")
        assert_json_envelope(data, "tour")

    def test_json_summary_has_verdict(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project, json_mode=True)
        data = parse_json_output(result, "tour")
        assert "verdict" in data["summary"]

    def test_json_summary_has_files_count(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project, json_mode=True)
        data = parse_json_output(result, "tour")
        assert "files" in data["summary"]
        assert data["summary"]["files"] > 0

    def test_json_has_top_symbols(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project, json_mode=True)
        data = parse_json_output(result, "tour")
        assert "top_symbols" in data
        assert isinstance(data["top_symbols"], list)

    def test_json_has_reading_order(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project, json_mode=True)
        data = parse_json_output(result, "tour")
        assert "reading_order" in data
        assert isinstance(data["reading_order"], list)

    def test_json_has_languages(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project, json_mode=True)
        data = parse_json_output(result, "tour")
        assert "languages" in data
        assert isinstance(data["languages"], list)
        assert len(data["languages"]) >= 1

    def test_mermaid_json_has_mermaid_field(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour", "--mermaid"], cwd=tour_project, json_mode=True)
        data = parse_json_output(result, "tour")
        assert "mermaid" in data
        assert isinstance(data["mermaid"], str)


class TestTourText:
    def test_verdict_line(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project)
        assert "VERDICT:" in result.output

    def test_output_includes_codebase_tour_heading(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project)
        assert "Tour" in result.output or "tour" in result.output.lower()

    def test_output_includes_key_symbols_or_reading_order(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project)
        # The tour should present either top symbols or a reading order section
        output_lower = result.output.lower()
        assert "symbol" in output_lower or "reading" in output_lower or "layer" in output_lower

    def test_output_mentions_python_language(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project)
        assert "python" in result.output.lower()

    def test_output_includes_file_count(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        result = invoke_cli(cli_runner, ["tour"], cwd=tour_project)
        # Should mention the number of files in the project
        assert "file" in result.output.lower()

    def test_write_flag_creates_file(self, cli_runner, tour_project, monkeypatch):
        monkeypatch.chdir(tour_project)
        out_file = tour_project / "ONBOARDING.md"
        result = invoke_cli(
            cli_runner,
            ["tour", "--write", str(out_file)],
            cwd=tour_project,
        )
        assert result.exit_code == 0
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert len(content) > 0
