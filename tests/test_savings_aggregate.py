"""Producer-side privacy contract for Prakteon's savings dashboard."""

from __future__ import annotations

import json

from click.testing import CliRunner

import roam.commands.cmd_savings as cmd_savings
from roam.savings import aggregate_savings_payload


def _rich_payload() -> dict:
    return {
        "summary": {
            "verdict": "Savings claims withheld",
            "state": "insufficient_evidence",
            "measurement_admissible": False,
            "north_star": {"private": "nested prompt"},
        },
        "coverage": {
            "prompt_starts": 100,
            "historical_prompt_starts": 90,
        },
        "sensor_canaries": {"state": "passed", "passed": 3, "total": 3},
        "historical_candidates": [{"pattern": "secret shell text", "task_prefix": "private prompt"}],
        "procedure_atlas": {
            "opportunities": [{"title": "private opportunity"}],
            "failure_signatures": [{"template": "private failure"}],
            "recovery_targets": [{"failure_class": "tool_timeout"}],
            "intervention_mappings": [
                {
                    "title": "private intervention",
                    "declaration_state": "unclaimed",
                }
            ],
        },
        "intervention_evidence": {
            "assignments": [
                {
                    "assignment": "shadow",
                    "session_id": "private-session",
                }
            ],
            "experiments": [{"intervention_id": "private-id"}],
        },
    }


def test_aggregate_payload_excludes_transcript_derived_values() -> None:
    aggregate = aggregate_savings_payload(_rich_payload())

    assert aggregate["privacy"]["aggregate_only"] is True
    assert aggregate["summary"]["causal_savings_claimed"] is False
    assert aggregate["opportunity_counts"]["historical_pattern_candidates"] == 1
    assert aggregate["intervention_state"]["declaration_states"] == {"unclaimed": 1}
    serialized = json.dumps(aggregate)
    for forbidden in (
        "secret shell text",
        "private prompt",
        "private opportunity",
        "private failure",
        "private intervention",
        "private-session",
        "private-id",
        "nested prompt",
    ):
        assert forbidden not in serialized


def test_savings_aggregate_flag_emits_only_safe_envelope(monkeypatch) -> None:
    monkeypatch.setattr(cmd_savings, "analyze_ledger", lambda _root: _rich_payload())

    result = CliRunner().invoke(
        cmd_savings.savings,
        ["--aggregate"],
        obj={"json": False},
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["command"] == "savings"
    assert payload["privacy"]["aggregate_only"] is True
    assert "historical_candidates" not in payload
    assert "procedure_atlas" not in payload
    assert "secret shell text" not in result.output
