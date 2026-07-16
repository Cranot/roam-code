"""Universal JSON projection tests for the global --select option."""

from __future__ import annotations

import json

import click
from click.testing import CliRunner

from roam.cli import cli
from roam.output.formatter import json_envelope, to_json
from roam.output.projection import apply_projection, project_cli_output


def test_projection_subset_supports_fields_indices_and_slices() -> None:
    payload = {
        "summary": {"verdict": "ok"},
        "symbols": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
    }
    assert apply_projection(payload, ".summary.verdict") == ("ok", None)
    assert apply_projection(payload, ".symbols[1].name") == ("b", None)
    assert apply_projection(payload, ".symbols[:2]") == (
        [{"name": "a"}, {"name": "b"}],
        None,
    )
    assert apply_projection(payload, ".symbols[-1].name") == ("c", None)


def test_projection_failure_is_structured() -> None:
    projected = project_cli_output(
        {"command": "fixture", "summary": {"verdict": "ok"}},
        (".missing",),
    )
    assert projected["summary"]["state"] == "usage_error"
    assert projected["isError"] is True
    assert projected["error_code"] == "USAGE_ERROR"


def test_global_select_implies_json_and_projects_command_output(tmp_path) -> None:
    (tmp_path / ".roam").mkdir()
    result = CliRunner().invoke(
        cli,
        ["--select", ".summary.state", "savings", "--root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "savings"
    assert payload["projection"] == ".summary.state"
    assert payload["data"] == "not_initialized"


def test_select_after_subcommand_is_normalized_and_repeatable(tmp_path) -> None:
    (tmp_path / ".roam").mkdir()
    result = CliRunner().invoke(
        cli,
        [
            "savings",
            "--root",
            str(tmp_path),
            "--select",
            ".summary.state",
            "--select",
            ".sensor_canaries.state",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["projection_count"] == 2
    assert payload["projections"] == [
        {"expression": ".summary.state", "value": "not_initialized"},
        {"expression": ".sensor_canaries.state", "value": "passed"},
    ]


def test_select_rejects_sarif_combination() -> None:
    result = CliRunner().invoke(
        cli,
        ["--sarif", "--select", ".summary", "health"],
    )
    assert result.exit_code == 2
    assert "--select cannot be combined with --sarif" in result.output


def test_projection_happens_before_json_budget_truncation() -> None:
    @click.command()
    @click.pass_context
    def fixture(ctx):
        click.echo(
            to_json(
                json_envelope(
                    "fixture",
                    summary={"verdict": "large fixture"},
                    budget=100,
                    items=list(range(100)),
                )
            )
        )

    result = CliRunner().invoke(
        fixture,
        [],
        obj={"select": (".items[-1]",)},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"] == 99
