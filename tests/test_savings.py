"""Known-answer and admissibility tests for the episode savings ledger."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_savings import savings
from roam.savings import (
    _episode_health_state,
    _interpret_historical_pattern,
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
