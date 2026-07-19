"""Known-answer and admissibility tests for the episode savings ledger."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_savings import savings
from roam.savings import (
    _episode_health_state,
    _interpret_historical_pattern,
    aggregate_savings_result,
    analyze_ledger,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _fixture_rows(
    *,
    count: int = 30,
    terminal: bool = True,
    health_state: str = "verification_passed",
):
    events: list[dict] = []
    compiles: list[dict] = []
    for i in range(count):
        episode_id = f"ep_{i:03d}"
        session_id = f"s_{i // 3}"
        events.append(
            {
                "schema_version": 1,
                "hook_version": 6,
                "evidence_source": "live_hook",
                "event_id": f"start_{i}",
                "episode_id": episode_id,
                "event_type": "prompt_submitted",
                "ts": "2026-01-01T00:00:00Z",
                "session_id": session_id,
                "turn_seq": i + 1,
                "terminal": False,
                "outcome": "pending",
                "compile_expected": True,
                "health_state": health_state,
            }
        )
        if terminal:
            events.append(
                {
                    "schema_version": 1,
                    "hook_version": 6,
                    "evidence_source": "live_hook",
                    "event_id": f"stop_{i}",
                    "episode_id": episode_id,
                    "event_type": "stop_decision",
                    "ts": "2026-01-01T00:00:10Z",
                    "session_id": session_id,
                    "turn_seq": i + 1,
                    "terminal": True,
                    "outcome": "verified_clean" if i % 3 else "no_edit",
                    "duration_ms": 10_000 + i,
                    "changed_files": 1,
                    "diff_sha256": f"{i:064x}",
                    "health_state": health_state,
                }
            )
        compiles.append(
            {
                "ts": "2026-01-01T00:00:01Z",
                "task_hash": "repeated-task",
                "task_prefix": "investigate repeated login latency",
                "procedure": "freeform_explore",
                "classifier_conf": 0.35,
                "art_label": "facts",
                "prefetched_keys": [],
                "envelope_bytes": 247,
                "compile_ms": 4.0,
                "agent_mode": "hook",
                "session_id": session_id,
                "turn_seq": str(i + 1),
                "episode_id": episode_id,
                "compiler_fp": "fixture",
                "injection_advice": "inject",
                "cache_hit": False,
            }
        )
    return events, compiles


def _seed(tmp_path: Path, **kwargs) -> None:
    events, compiles = _fixture_rows(**kwargs)
    _write_jsonl(tmp_path / ".roam" / "episodes.jsonl", events)
    _write_jsonl(tmp_path / ".roam" / "compile-runs.jsonl", compiles)


def test_policy_ready_requires_complete_join_and_health_context(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = analyze_ledger(tmp_path)
    assert result["summary"]["state"] == "policy_ready"
    assert result["summary"]["measurement_admissible"] is True
    assert result["summary"]["policy_admissible"] is True
    assert result["coverage"]["terminal_coverage_pct"] == 100.0
    assert result["coverage"]["episode_join_coverage_pct"] == 100.0
    assert result["coverage"]["compile_identity_coverage_pct"] == 100.0
    assert result["coverage"]["health_context_coverage_pct"] == 100.0
    assert result["repeat_candidates"][0]["episodes"] == 30
    assert result["repeat_candidates"][0]["evidence_status"] == "candidate"


def test_health_unknown_allows_measurement_but_blocks_policy(tmp_path: Path) -> None:
    _seed(tmp_path, health_state="unknown")
    result = analyze_ledger(tmp_path)
    assert result["summary"]["state"] == "measurement_ready"
    assert result["summary"]["measurement_admissible"] is True
    assert result["summary"]["policy_admissible"] is False
    assert result["repeat_candidates"][0]["evidence_status"] == "candidate_only_health_context_missing"


def test_missing_terminal_outcomes_withholds_savings_claims(tmp_path: Path) -> None:
    _seed(tmp_path, terminal=False)
    result = analyze_ledger(tmp_path)
    assert result["summary"]["state"] == "insufficient_evidence"
    assert result["summary"]["partial_success"] is True
    assert result["coverage"]["terminal_coverage_pct"] == 0.0
    assert result["repeat_candidates"] == []


def test_materialization_is_idempotent_and_preserves_identical_compile_calls(tmp_path: Path) -> None:
    _seed(tmp_path)
    first = analyze_ledger(tmp_path)
    second = analyze_ledger(tmp_path)
    assert first["materialization"]["event_records"] == 60
    assert first["materialization"]["compile_records"] == 30
    assert second["materialization"]["event_rows_inserted"] == 0
    assert second["materialization"]["compile_rows_inserted"] == 0
    assert second["materialization"]["compile_records"] == 30


def test_invalid_jsonl_is_disclosed_without_crashing(tmp_path: Path) -> None:
    events, compiles = _fixture_rows()
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    with (roam_dir / "episodes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("not-json\n")
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    result = analyze_ledger(tmp_path)
    assert result["materialization"]["invalid_event_rows"] == 1
    assert result["summary"]["state"] == "insufficient_evidence"
    assert result["summary"]["integrity_clean"] is False
    assert result["repeat_candidates"] == []


def test_cli_not_initialized_is_honest_and_structured(tmp_path: Path) -> None:
    (tmp_path / ".roam").mkdir()
    result = CliRunner().invoke(savings, ["--root", str(tmp_path)], obj={"json": True})
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["state"] == "not_initialized"
    assert payload["summary"]["partial_success"] is True
    assert payload["sensor_canaries"]["state"] == "passed"
    assert payload["repeat_candidates"] == []


def test_savings_aggregate_projects_only_closed_counts_and_static_text() -> None:
    opaque_episode_id = "ep_0123456789abcdef01234567"
    canaries = [
        "TITLE-CANARY-71",
        "PATTERN-CANARY-72",
        "COMMAND-CANARY-73",
        "PROMPT-CANARY-74",
        "RESPONSE-CANARY-75",
        "PATH-CANARY-76",
        "SESSION-CANARY-77",
        opaque_episode_id,
    ]
    aggregate = aggregate_savings_result(
        {
            "summary": {
                "verdict": canaries[3],
                "state": "policy_ready",
                "partial_success": False,
                "measurement_admissible": True,
                "policy_admissible": True,
                "integrity_clean": True,
                "north_star": canaries[4],
            },
            "coverage": {
                "prompt_starts": 12,
                "terminal_outcomes": 11,
                "terminal_coverage_pct": 91.7,
                "private_path": canaries[5],
            },
            "sensor_canaries": {
                "state": "passed",
                "passed": 3,
                "total": 3,
                "failures": [canaries[4]],
            },
            "repeat_candidates": [{"episode_id": opaque_episode_id, "task_prefix": canaries[3]}],
            "historical_candidates": [{"pattern": canaries[1], "command": canaries[2]}],
            "procedure_atlas": {
                "opportunities": [{"title": canaries[0]}],
                "failure_signatures": [{"template": canaries[4]}],
                "recovery_targets": [{"path": canaries[5]}],
                "intervention_mappings": [
                    {"declaration_state": "declared_native", "title": canaries[0]},
                    {"declaration_state": "unclaimed", "command": canaries[2]},
                    {"declaration_state": "private-state", "path": canaries[5]},
                ],
            },
            "intervention_evidence": {
                "assignments": [{"session_id": canaries[6]}],
                "experiments": [
                    {
                        "episode_id": opaque_episode_id,
                        "intervention_id": canaries[6],
                        "assignment_counts": {"control": 2, "exposed": 3, "private-arm": 4},
                    }
                ],
            },
            "materialization": {"database": canaries[5]},
        }
    )

    assert set(aggregate) == {
        "aggregate_schema",
        "aggregate_schema_version",
        "summary",
        "coverage",
        "sensor_canaries",
        "opportunity_counts",
        "intervention_state",
        "privacy",
    }
    assert aggregate["opportunity_counts"] == {
        "repeated_live_candidates": 1,
        "historical_pattern_candidates": 1,
        "ranked_work_opportunities": 1,
        "failure_signatures": 1,
        "recovery_targets": 1,
        "intervention_mappings": 3,
    }
    assert aggregate["intervention_state"] == {
        "declaration_states": {
            "declared_native": 1,
            "declared_partial": 0,
            "unclaimed": 1,
            "unknown": 1,
        },
        "assignments": 9,
        "experiments": 1,
        "assignment_states": {"control": 2, "exposed": 3, "shadow": 0, "unknown": 4},
        "causal_savings_claimed": False,
    }
    assert aggregate["privacy"] == {
        "aggregate_only": True,
        "raw_transcripts_returned": False,
        "prompt_or_response_text_returned": False,
        "shell_command_text_returned": False,
        "source_or_path_text_returned": False,
        "per_episode_data_returned": False,
        "identifiers_returned": False,
    }
    serialized = json.dumps(aggregate, sort_keys=True)
    for canary in canaries:
        assert canary not in serialized
    assert "episode_id" not in serialized
    assert "next_commands" not in serialized


def test_cli_aggregate_is_stoa_compatible_and_private(tmp_path: Path) -> None:
    events, compiles = _fixture_rows()
    opaque_episode_id = "ep_abcdef0123456789abcdef01"
    for event in events:
        if event["episode_id"] == "ep_000":
            event["episode_id"] = opaque_episode_id
    compiles[0]["episode_id"] = opaque_episode_id
    compiles[0]["task_prefix"] = "PROMPT-CANARY-CLI-81"
    compiles[0]["private_path"] = "PATH-CANARY-CLI-82"
    _write_jsonl(tmp_path / ".roam" / "episodes.jsonl", events)
    _write_jsonl(tmp_path / ".roam" / "compile-runs.jsonl", compiles)

    result = CliRunner().invoke(
        savings,
        ["--root", str(tmp_path), "--aggregate"],
        obj={"json": True},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["aggregate_schema"] == "roam.savings.aggregate"
    assert payload["aggregate_schema_version"] == 1
    assert payload["privacy"]["aggregate_only"] is True
    assert payload["privacy"]["identifiers_returned"] is False
    assert payload["opportunity_counts"]["repeated_live_candidates"] == 1
    assert "agent_contract" not in payload
    for private_key in (
        "event_distribution",
        "outcome_distribution",
        "repeat_candidates",
        "historical_candidates",
        "procedure_atlas",
        "intervention_evidence",
        "materialization",
        "thresholds",
    ):
        assert private_key not in payload
    serialized = json.dumps(payload, sort_keys=True)
    for canary in (
        opaque_episode_id,
        "PROMPT-CANARY-CLI-81",
        "PATH-CANARY-CLI-82",
        str(tmp_path),
    ):
        assert canary not in serialized
    assert "episode_id" not in serialized
    assert "next_commands" not in serialized


def test_cli_rejects_aggregate_schema_mixture(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        savings,
        ["--root", str(tmp_path), "--aggregate", "--schema"],
        obj={"json": True},
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_schema_works_without_telemetry(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        savings,
        ["--root", str(tmp_path), "--schema"],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    fields = payload["event_schema"]["fields"]
    assert {"event_id", "episode_id", "event_type", "terminal", "outcome"} <= set(fields)
    assert {
        "intervention_id",
        "intervention_version",
        "eligibility_rule_version",
        "assignment",
        "downstream_transition_count",
    } <= set(fields)


def test_intervention_evidence_requires_assignment_observation_join(
    tmp_path: Path,
) -> None:
    events, compiles = _fixture_rows(count=2)
    for index, assignment in enumerate(("control", "exposed")):
        episode_id = f"ep_{index:03d}"
        common = {
            "schema_version": 1,
            "hook_version": 6,
            "evidence_source": "live_hook",
            "episode_id": episode_id,
            "session_id": f"cluster_{index}",
            "terminal": False,
            "outcome": "intervention_measurement",
            "health_state": "unknown",
            "intervention_id": "repeated_code_slicing",
            "intervention_version": "grep-packets-v1",
        }
        events.append(
            {
                **common,
                "event_id": f"assignment_{index}",
                "event_type": "intervention_assignment",
                "ts": "2026-01-01T00:00:02Z",
                "eligibility_rule_version": "slice-transition-v1",
                "eligible_transition": True,
                "assignment": assignment,
                "assignment_cluster": f"cluster_{index}",
            }
        )
        events.append(
            {
                **common,
                "event_id": f"observation_{index}",
                "event_type": "intervention_observation",
                "ts": "2026-01-01T00:00:09Z",
                "delivered": assignment == "exposed",
                "adopted": assignment == "exposed",
                "downstream_transition_count": index,
            }
        )
    _write_jsonl(tmp_path / ".roam" / "episodes.jsonl", events)
    _write_jsonl(tmp_path / ".roam" / "compile-runs.jsonl", compiles)

    evidence = analyze_ledger(tmp_path)["intervention_evidence"]
    assert evidence["summary"]["state"] == "instrumented"
    assert evidence["summary"]["assignment_events"] == 2
    assert evidence["summary"]["terminal_observation_joins"] == 2
    experiment = evidence["experiments"][0]
    assert experiment["assignment_counts"] == {"control": 1, "exposed": 1}
    assert experiment["observation_join_coverage_pct"] == 100.0
    assert experiment["event_ordering_violations"] == 0
    assert experiment["promotion_readiness"] == "insufficient_sample"
    assert experiment["effectiveness_state"] == "unmeasured"
    assert experiment["causal_savings_claimed"] is False


def test_intervention_evidence_rejects_post_terminal_observation(
    tmp_path: Path,
) -> None:
    events, compiles = _fixture_rows(count=1)
    events.extend(
        [
            {
                "event_id": "assignment",
                "episode_id": "ep_000",
                "event_type": "intervention_assignment",
                "ts": "2026-01-01T00:00:02Z",
                "session_id": "cluster",
                "terminal": False,
                "intervention_id": "repeated_code_slicing",
                "intervention_version": "grep-packets-v1",
                "eligibility_rule_version": "slice-transition-v1",
                "eligible_transition": True,
                "assignment": "exposed",
                "assignment_cluster": "cluster",
            },
            {
                "event_id": "observation",
                "episode_id": "ep_000",
                "event_type": "intervention_observation",
                "ts": "2026-01-01T00:00:11Z",
                "session_id": "cluster",
                "terminal": False,
                "intervention_id": "repeated_code_slicing",
                "intervention_version": "grep-packets-v1",
                "delivered": True,
                "adopted": True,
                "downstream_transition_count": 0,
            },
        ]
    )
    _write_jsonl(tmp_path / ".roam" / "episodes.jsonl", events)
    _write_jsonl(tmp_path / ".roam" / "compile-runs.jsonl", compiles)

    evidence = analyze_ledger(tmp_path)["intervention_evidence"]
    assert evidence["summary"]["terminal_observation_joins"] == 0
    assert evidence["summary"]["event_ordering_violations"] == 1
    experiment = evidence["experiments"][0]
    assert experiment["promotion_readiness"] == "event_ordering_violation"


def test_historical_pattern_interpretation_maps_repetition_to_existing_surfaces() -> None:
    slice_hint = _interpret_historical_pattern(
        "shell_ngram",
        "rg -n <ARG> <PATH> => sed -n <ARG> <PATH>",
    )
    assert slice_hint["pattern_family"] == "search_then_slice"
    assert slice_hint["priority"] == "high"
    assert "roam retrieve" in slice_hint["existing_surface"]

    projection_hint = _interpret_historical_pattern(
        "shell_sequence",
        "roam complexity <PATH> --json | python3 -c <CODE>",
    )
    assert projection_hint["candidate_disposition"] == "projection_gap"


def test_blocked_continuation_preserves_validated_failure_health() -> None:
    start = {"health_state": "unknown"}
    blocked = {"health_state": "verification_failed", "terminal": 0}
    continuation = {"health_state": "continuation_unverified", "terminal": 1}
    assert _episode_health_state([start, blocked, continuation], start, continuation) == "verification_failed"
