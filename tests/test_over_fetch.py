"""Tests for roam over-fetch -- Laravel model over-serialization detection."""

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
def overfetch_project(tmp_path):
    """Laravel project with a model that has many fillable fields and no $hidden."""
    proj = tmp_path / "overfetch_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    models = proj / "app" / "Models"
    models.mkdir(parents=True)
    # Model with many fillable fields, no $hidden
    (models / "Order.php").write_text(
        "<?php\nnamespace App\\Models;\n\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n\n"
        "class Order extends Model {\n"
        "    protected $fillable = [\n"
        "        'user_id', 'product_id', 'quantity', 'price',\n"
        "        'discount', 'tax', 'total', 'status',\n"
        "        'shipping_address', 'billing_address',\n"
        "        'payment_method', 'payment_status',\n"
        "        'tracking_number', 'notes', 'internal_notes',\n"
        "        'created_by', 'updated_by', 'deleted_by',\n"
        "        'ip_address', 'user_agent', 'session_id',\n"
        "        'referral_code', 'coupon_code', 'gift_message',\n"
        "        'priority', 'weight', 'dimensions',\n"
        "        'warehouse_id', 'shelf_location', 'batch_number',\n"
        "        'customs_value', 'hs_code',\n"
        "    ];\n"
        "}\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def no_models_project(tmp_path):
    proj = tmp_path / "no_models"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("x = 1\n")
    git_init(proj)
    index_in_process(proj)
    return proj


class TestOverFetchSmoke:
    def test_exits_zero(self, cli_runner, overfetch_project, monkeypatch):
        monkeypatch.chdir(overfetch_project)
        result = invoke_cli(cli_runner, ["over-fetch"], cwd=overfetch_project)
        assert result.exit_code == 0

    def test_no_models_exits_zero(self, cli_runner, no_models_project, monkeypatch):
        monkeypatch.chdir(no_models_project)
        result = invoke_cli(cli_runner, ["over-fetch"], cwd=no_models_project)
        assert result.exit_code == 0


class TestOverFetchJSON:
    def test_json_envelope(self, cli_runner, overfetch_project, monkeypatch):
        monkeypatch.chdir(overfetch_project)
        result = invoke_cli(cli_runner, ["over-fetch"], cwd=overfetch_project, json_mode=True)
        data = parse_json_output(result, "over-fetch")
        assert_json_envelope(data, "over-fetch")

    def test_json_summary_has_verdict(self, cli_runner, overfetch_project, monkeypatch):
        monkeypatch.chdir(overfetch_project)
        result = invoke_cli(cli_runner, ["over-fetch"], cwd=overfetch_project, json_mode=True)
        data = parse_json_output(result, "over-fetch")
        assert "verdict" in data["summary"]


class TestOverFetchText:
    def test_verdict_line(self, cli_runner, overfetch_project, monkeypatch):
        monkeypatch.chdir(overfetch_project)
        result = invoke_cli(cli_runner, ["over-fetch"], cwd=overfetch_project)
        assert "VERDICT:" in result.output
