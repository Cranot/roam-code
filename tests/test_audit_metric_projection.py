"""Audit metrics retain producer units, zero values and unknown states."""

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

import roam.cli as cli_module
from roam.commands import cmd_audit


@pytest.mark.parametrize("brief", [False, True])
@pytest.mark.parametrize("case", ["positive", "zero", "unknown"])
def test_serialized_metric_projection(monkeypatch, brief, case):
    debt = {"total_debt": 99.5, "total_remediation_minutes": 120}
    dead = {"safe": 2, "review": 3, "intentional": 4, "unused_assignments": 800, "dataflow_dead": 900}
    if case == "zero":
        debt["total_remediation_minutes"] = 0
        dead.update(safe=0, review=0, intentional=0)
    elif case == "unknown":
        debt = {"total_debt": 99.5, "total": 999}
        dead = {"safe": 2, "review": 3}  # incomplete buckets cannot establish a total

    @click.command(context_settings={"ignore_unknown_options": True})
    @click.argument("args", nargs=-1)
    def producer(args):
        summary = debt if "debt" in args else dead if "dead" in args else {}
        click.echo(json.dumps({"summary": summary}))

    monkeypatch.setattr(cli_module, "cli", producer)
    monkeypatch.setattr(cmd_audit, "ensure_index", lambda: None)
    result = CliRunner().invoke(cmd_audit.audit, ["--brief"] if brief else [], obj={"json": True})
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["debt_total"] == {"positive": 120, "zero": 0, "unknown": None}[case]
    assert summary["debt_score_total"] == 99.5
    assert summary["debt_total_definition"] == "estimated_remediation_minutes"
    assert summary["dead_count"] == {"positive": 9, "zero": 0, "unknown": None}[case]
    assert summary["dead_count_definition"] == "dead_export_action_buckets_safe_review_intentional"
