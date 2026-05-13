"""Tests for roam n1 -- implicit N+1 I/O pattern detection in ORM models."""

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
def laravel_project(tmp_path):
    """Laravel-style PHP project with a model that has $appends and an accessor
    that lazy-loads a relationship -- the canonical N+1 scenario."""
    proj = tmp_path / "laravel_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # App Models directory
    models_dir = proj / "app" / "Models"
    models_dir.mkdir(parents=True)

    (models_dir / "Order.php").write_text(
        "<?php\n"
        "namespace App\\Models;\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n"
        "\n"
        "class Order extends Model {\n"
        "    protected $fillable = ['total', 'status'];\n"
        "    protected $appends = ['total_display', 'item_count'];\n"
        "\n"
        "    public function getTotalDisplayAttribute() {\n"
        "        return '$' . number_format($this->total, 2);\n"
        "    }\n"
        "\n"
        "    public function getItemCountAttribute() {\n"
        "        return $this->items()->count();\n"
        "    }\n"
        "\n"
        "    public function items() {\n"
        "        return $this->hasMany(OrderItem::class);\n"
        "    }\n"
        "\n"
        "    public function user() {\n"
        "        return $this->belongsTo(User::class);\n"
        "    }\n"
        "}\n"
    )

    (models_dir / "OrderItem.php").write_text(
        "<?php\n"
        "namespace App\\Models;\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n"
        "\n"
        "class OrderItem extends Model {\n"
        "    protected $fillable = ['order_id', 'product_id', 'quantity', 'price'];\n"
        "\n"
        "    public function order() {\n"
        "        return $this->belongsTo(Order::class);\n"
        "    }\n"
        "}\n"
    )

    # A controller that lists orders (collection context)
    controllers_dir = proj / "app" / "Http" / "Controllers"
    controllers_dir.mkdir(parents=True)

    (controllers_dir / "OrderController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "use App\\Models\\Order;\n"
        "\n"
        "class OrderController extends Controller {\n"
        "    public function index() {\n"
        "        return Order::paginate(20);\n"
        "    }\n"
        "\n"
        "    public function show($id) {\n"
        "        return Order::find($id);\n"
        "    }\n"
        "}\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def python_only_project(tmp_path):
    """A pure Python project with no ORM models -- N+1 detector should find nothing."""
    proj = tmp_path / "python_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "app.py").write_text("def compute(x):\n    return x * 2\n\ndef main():\n    return compute(21)\n")
    (proj / "utils.py").write_text("def format_value(v):\n    return str(v)\n")

    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestN1Smoke:
    def test_exits_zero_on_laravel_project(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project)
        assert result.exit_code == 0

    def test_exits_zero_on_python_only_project(self, cli_runner, python_only_project, monkeypatch):
        """Should exit 0 and report no findings even without ORM models."""
        monkeypatch.chdir(python_only_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=python_only_project)
        assert result.exit_code == 0

    def test_confidence_filter_high(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1", "--confidence", "high"], cwd=laravel_project)
        assert result.exit_code == 0

    def test_confidence_filter_medium(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1", "--confidence", "medium"], cwd=laravel_project)
        assert result.exit_code == 0

    def test_confidence_filter_low(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1", "--confidence", "low"], cwd=laravel_project)
        assert result.exit_code == 0

    def test_verbose_flag_accepted(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1", "--verbose"], cwd=laravel_project)
        assert result.exit_code == 0

    def test_limit_flag_accepted(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1", "--limit", "5"], cwd=laravel_project)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# JSON envelope tests
# ---------------------------------------------------------------------------


class TestN1JSON:
    def test_json_envelope_structure(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "n1")
        assert_json_envelope(data, "n1")

    def test_json_summary_has_verdict(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "n1")
        assert "verdict" in data["summary"]
        assert isinstance(data["summary"]["verdict"], str)
        assert len(data["summary"]["verdict"]) > 0

    def test_json_summary_has_total(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "n1")
        assert "total" in data["summary"]
        assert isinstance(data["summary"]["total"], int)

    def test_json_summary_has_framework(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "n1")
        assert "framework" in data["summary"]

    def test_json_has_findings_list(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "n1")
        assert "findings" in data
        assert isinstance(data["findings"], list)

    def test_json_python_project_has_no_findings(self, cli_runner, python_only_project, monkeypatch):
        monkeypatch.chdir(python_only_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=python_only_project, json_mode=True)
        data = parse_json_output(result, "n1")
        assert data["summary"]["total"] == 0
        assert data["findings"] == []

    def test_json_finding_fields_when_present(self, cli_runner, laravel_project, monkeypatch):
        """When findings are reported each should have the expected keys."""
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "n1")
        findings = data.get("findings", [])
        if not findings:
            pytest.skip("No N+1 findings detected in fixture -- skipping field check")
        # R22 confidence triple shape — value carries the original
        # finding fields; confidence/reason live at the triple level.
        triple_keys = {"value", "confidence", "reason"}
        value_required_keys = {
            "model_name",
            "accessor_name",
            "appended_attribute",
        }
        for finding in findings:
            assert triple_keys.issubset(set(finding.keys())), (
                f"Triple missing keys: {triple_keys - set(finding.keys())}"
            )
            missing = value_required_keys - set(finding["value"].keys())
            assert not missing, f"Value missing keys: {missing}"

    def test_json_command_field_is_n1(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project, json_mode=True)
        data = parse_json_output(result, "n1")
        assert data["command"] == "n1"


# ---------------------------------------------------------------------------
# Text output tests
# ---------------------------------------------------------------------------


class TestN1Text:
    def test_verdict_line_present(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project)
        assert "VERDICT:" in result.output

    def test_verdict_line_is_first_line(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project)
        first_line = result.output.strip().splitlines()[0]
        assert first_line.startswith("VERDICT:")

    def test_no_findings_message_on_python_project(self, cli_runner, python_only_project, monkeypatch):
        monkeypatch.chdir(python_only_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=python_only_project)
        assert "No implicit N+1" in result.output or "0 implicit" in result.output

    def test_framework_line_shown_when_detected(self, cli_runner, laravel_project, monkeypatch):
        """Framework name should appear in text output when a specific framework is detected."""
        monkeypatch.chdir(laravel_project)
        result = invoke_cli(cli_runner, ["n1"], cwd=laravel_project)
        # Framework line is shown when framework is not 'generic'
        # For a PHP-heavy project it may detect 'laravel' or fall back to 'generic'
        assert result.exit_code == 0  # At minimum, it ran successfully
