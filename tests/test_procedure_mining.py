"""Known-answer tests for private transcript opportunity mining."""

from __future__ import annotations

from roam.procedure_mining import (
    build_procedure_atlas,
    declared_displacement_claims,
    normalized_episode_tokens,
)


def _episode(index: int, **overrides):
    row = {
        "episode_id": f"hist_{index}",
        "session_id": f"session_{index // 2}",
        "project_id": f"project_{index % 3}",
        "evidence_source": "transcript_backfill",
        "transcript_source": "codex" if index % 2 else "claude",
        "terminal": True,
        "outcome": "historical_no_edit",
        "duration_ms": 10_000,
        "tool_calls": 10,
        "tool_errors": 0,
        "tool_result_bytes_bucket": 0,
        "edit_actions": 0,
        "input_tokens": 1000,
        "output_tokens": 100,
        "cached_input_tokens": 500,
        "cache_creation_tokens": 0,
        "reasoning_output_tokens": 50,
        "correction_after": False,
        "prompt_hmac_sha256": "same-intent",
        "intent_simhash64": "1234567890abcdef",
        "prompt_tokens_bucket": 50,
        "intent_archetypes": ["debug"],
        "phase_sequence_template": "orient>search>inspect*2",
        "shell_templates": {
            "git status --short": 1,
            "rg -n <ARG> <PATH>": 1,
            "sed -n <ARG> <PATH>": 2,
        },
        "shell_template_outcomes": {
            "python -m pytest <PATH> -q": {
                "attempts": 2,
                "failures": 1,
                "retries_after_failure": 1,
                "result_bytes_bucket": 8192,
                "failure_classes": {"test_failure": 1},
            }
        },
        "friction": {
            "slice_calls": 3,
            "search_inspect_cycles": 1,
            "exact_shell_replays": 2,
            "output_postprocess_calls": 0,
            "orientation_calls": 1,
            "verification_retries": 0,
            "failed_action_retries": 0,
            "post_edit_context_calls": 0,
            "help_calls": 0,
        },
    }
    row.update(overrides)
    return row


def test_atlas_ranks_direct_actions_without_calling_them_savings() -> None:
    atlas = build_procedure_atlas([_episode(index) for index in range(6)])
    assert atlas["summary"]["causal_savings_claimed"] is False
    slicing = next(row for row in atlas["opportunities"] if row["opportunity"] == "repeated_code_slicing")
    assert slicing["episodes"] == 6
    assert slicing["projects"] == 3
    assert slicing["addressable_actions"] == 12
    assert slicing["evidence_status"] == "historical_opportunity_only"
    assert "saved" not in " ".join(slicing).lower()


def test_exact_intent_cluster_requires_multiple_sessions() -> None:
    atlas = build_procedure_atlas([_episode(index) for index in range(6)])
    clusters = atlas["exact_intent_clusters"]
    assert len(clusters) == 1
    assert clusters[0]["episodes"] == 6
    assert clusters[0]["sessions"] == 3
    assert clusters[0]["projects"] == 3
    assert clusters[0]["modal_phase_sequence"] == "orient>search>inspect*2"


def test_live_episodes_never_enter_historical_atlas() -> None:
    atlas = build_procedure_atlas([_episode(index, evidence_source="live_hook") for index in range(6)])
    assert atlas["summary"]["episodes"] == 0
    assert atlas["opportunities"] == []
    assert atlas["exact_intent_clusters"] == []
    assert atlas["near_intent_clusters"] == []


def test_token_normalization_does_not_double_count_codex_cache() -> None:
    codex = _episode(
        1,
        transcript_source="codex",
        input_tokens=1000,
        cached_input_tokens=800,
        output_tokens=100,
        reasoning_output_tokens=50,
    )
    claude = _episode(
        2,
        transcript_source="claude",
        input_tokens=200,
        cached_input_tokens=800,
        cache_creation_tokens=100,
        output_tokens=100,
        reasoning_output_tokens=0,
    )
    assert normalized_episode_tokens(codex) == 1150
    assert normalized_episode_tokens(claude) == 1200


def test_near_intent_cluster_requires_distinct_exact_intents() -> None:
    base = int("1234567890abcdef", 16)
    episodes = [
        _episode(
            index,
            prompt_hmac_sha256=f"intent-{index}",
            intent_simhash64=f"{base ^ (1 << index):016x}",
        )
        for index in range(3)
    ]
    atlas = build_procedure_atlas(episodes)
    assert len(atlas["near_intent_clusters"]) == 1
    cluster = atlas["near_intent_clusters"][0]
    assert cluster["episodes"] == 3
    assert cluster["distinct_exact_intents"] == 3
    assert cluster["sessions"] == 2


def test_scripted_exact_intent_is_disclosed_and_removed_from_organic_support() -> None:
    episodes = [_episode(index, session_id=f"script-session-{index}") for index in range(20)]
    atlas = build_procedure_atlas(episodes)
    assert atlas["summary"]["likely_automated_episodes"] == 20
    slicing = next(row for row in atlas["opportunities"] if row["opportunity"] == "repeated_code_slicing")
    assert slicing["episodes"] == 20
    assert slicing["organic_episode_estimate"] == 0
    assert slicing["likely_automated_episodes"] == 20
    assert slicing["organic_addressable_actions"] == 0


def test_structured_projection_does_not_count_code_slicing_pipeline() -> None:
    episodes = [
        _episode(
            1,
            shell_templates={
                "nl -ba <PATH> | sed -n <ARG>": 8,
                "roam complexity <PATH> --json | python3 -c <CODE>": 2,
            },
            friction={"output_postprocess_calls": 10},
        )
    ]
    atlas = build_procedure_atlas(episodes)
    projection = next(row for row in atlas["opportunities"] if row["opportunity"] == "output_postprocessing")
    assert projection["addressable_actions"] == 2


def test_template_outcome_rankings_attribute_failures_and_result_volume() -> None:
    atlas = build_procedure_atlas([_episode(index) for index in range(6)])
    failure = atlas["failure_signatures"][0]
    assert failure["template"] == "python -m pytest <PATH> -q"
    assert failure["attempts"] == 12
    assert failure["failures"] == 6
    assert failure["retries_after_failure"] == 6
    assert failure["projects"] == 3
    assert failure["failure_classes"] == {"test_failure": 6}
    result = atlas["large_result_producers"][0]
    assert result["associated_bucketed_result_bytes"] == 49152
    assert atlas["recovery_targets"] == [
        {
            "failure_class": "test_failure",
            "failures": 6,
            "templates": 1,
            "projects": 3,
            "evidence_status": "closed_failure_class_only",
            "raw_result_content_persisted": False,
            "classification_status": "heuristic_unvalidated",
            "routing_eligible": False,
        }
    ]


def test_declared_displacements_are_ast_read_without_importing_commands() -> None:
    claims = declared_displacement_claims()
    pairs = {(claim["capability"], claim["opportunity"]) for claim in claims}

    assert ("at", "repeated_code_slicing") in pairs
    assert ("grep", "search_inspect_thrash") in pairs
    assert ("verify", "verification_retry") in pairs
    assert ("global --select", "output_postprocessing") in pairs


def test_intervention_mappings_keep_declarations_prospectively_unmeasured() -> None:
    atlas = build_procedure_atlas(
        [
            _episode(
                index,
                tool_errors=1,
                friction={
                    "slice_calls": 3,
                    "search_inspect_cycles": 1,
                    "exact_shell_replays": 2,
                    "output_postprocess_calls": 0,
                    "orientation_calls": 1,
                    "verification_retries": 0,
                    "failed_action_retries": 0,
                    "post_edit_context_calls": 0,
                    "help_calls": 0,
                },
            )
            for index in range(6)
        ]
    )
    gaps = {row["opportunity"]: row for row in atlas["intervention_mappings"]}

    assert gaps["repeated_code_slicing"]["declaration_state"] == "declared_native"
    assert gaps["search_inspect_thrash"]["declaration_state"] == "declared_native"
    assert gaps["exact_shell_replay"]["declaration_state"] == "declared_partial"
    assert gaps["tool_failure_recovery"]["declaration_state"] == "unclaimed"
    assert all(row["effectiveness_state"] == "unmeasured" for row in gaps.values())
    assert all(row["residual_gap_score"] is None for row in gaps.values())
    assert (
        gaps["repeated_code_slicing"]["research_priority_score"] == gaps["repeated_code_slicing"]["opportunity_score"]
    )
    assert all(row["causal_savings_claimed"] is False for row in gaps.values())

    tests = {row["transition"]: row for row in atlas["intervention_tests"]}
    slicing = tests["repeated_code_slicing"]
    assert slicing["exposure_state"] == "not_instrumented"
    assert slicing["effectiveness_state"] == "unmeasured"
    assert slicing["minimum_promotion_gate"]["require_transition_reduction"] is True
    assert slicing["minimum_promotion_gate"]["require_outcome_non_inferiority"] is True
    assert slicing["minimum_promotion_gate"]["power_analysis_required"] is True
    assert slicing["minimum_promotion_gate"]["transition_effect_interval_must_exclude_zero"] is True
    assert slicing["experimental_design"]["assignment_unit"] == "session_id"
    assert slicing["experimental_design"]["analysis_population"] == "intent_to_treat"
    assert slicing["experimental_design"]["required_event_pair"] == [
        "intervention_assignment",
        "intervention_observation",
    ]


def test_high_tool_no_edit_excludes_passive_and_unknown_intents() -> None:
    episodes = [
        _episode(1, intent_archetypes=["research"]),
        _episode(2, intent_archetypes=["review", "plan"]),
        _episode(3, intent_archetypes=[]),
        _episode(4, intent_archetypes=["implement"]),
        _episode(5, intent_archetypes=["research", "implement"]),
    ]
    atlas = build_procedure_atlas(episodes)
    opportunity = next(row for row in atlas["opportunities"] if row["opportunity"] == "high_tool_no_edit")
    assert opportunity["episodes"] == 2
    assert opportunity["addressable_actions"] == 2
