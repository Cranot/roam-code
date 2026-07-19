"""Privacy and known-answer tests for historical transcript backfill."""

from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands.cmd_savings_backfill import savings_backfill
from roam.savings import analyze_ledger
from roam.transcript_backfill import (
    TranscriptBackfillSafetyError,
    _action_outcome_tables,
    _compressed_sequence,
    _failure_class,
    _friction_metrics,
    _intent_archetypes,
    _is_correction,
    _load_or_create_key,
    _project_scope,
    backfill_transcripts,
    sanitize_command_template,
)


def _write_claude_session(path: Path, cwd: Path, session_id: str, command: str) -> None:
    rows = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": str(cwd),
            "sessionId": session_id,
            "message": {"role": "user", "content": "find the repeated customer login failure"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01Z",
            "cwd": str(cwd),
            "sessionId": session_id,
            "message": {
                "role": "assistant",
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"{session_id}-shell",
                        "name": "Bash",
                        "input": {"command": command},
                    },
                    {
                        "type": "tool_use",
                        "id": f"{session_id}-edit",
                        "name": "Edit",
                        "input": {
                            "file_path": "/private/customer/auth.py",
                            "old_string": "secret-old",
                            "new_string": "secret-new",
                        },
                    },
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:02Z",
            "cwd": str(cwd),
            "sessionId": session_id,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"{session_id}-shell",
                        "content": "1 passed",
                        "is_error": False,
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": f"{session_id}-edit",
                        "content": "updated /private/customer/auth.py",
                        "is_error": False,
                    },
                ],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_sanitized_command_retains_shape_and_removes_values() -> None:
    raw = (
        "ROAM_MODE=safe AWS_SECRET_ACCESS_KEY=supersecret "
        'rg -n "customer-login-pattern" /private/repo/src '
        "&& git status --short && python -m pytest /private/repo/tests/test_auth.py -q"
    )
    template = sanitize_command_template(raw)
    assert template == (
        "<ENV>=<VALUE> <ENV>=<VALUE> rg -n <ARG> <PATH> && git status --short && python -m pytest <PATH> -q"
    )
    for secret in ("supersecret", "customer-login-pattern", "/private/repo", "AWS_SECRET"):
        assert secret not in template


def test_sanitizer_respects_quoted_pipes_and_newline_boundaries() -> None:
    template = sanitize_command_template(
        "grep -E 'tests|failed|errors' /private/log\ncd /private/repo\ngit status --short"
    )
    assert template == ("grep -e <ARG> <PATH> ; cd <PATH> ; git status --short")
    assert template.count("|") == 0


def test_trajectory_compression_preserves_late_state_changes() -> None:
    assert _compressed_sequence(["shell"] * 40 + ["edit"] + ["shell"] * 3) == ("shell*40>edit>shell*3")


def test_friction_metrics_count_observed_rework_without_claiming_savings() -> None:
    actions = [
        {"phase": "orient", "template": "git status --short", "failed": False},
        {"phase": "search", "template": "rg -n <ARG> <PATH>", "failed": False},
        {"phase": "inspect", "template": "sed -n <ARG> <PATH>", "failed": False},
        {"phase": "search", "template": "rg -n <ARG> <PATH>", "failed": True},
        {"phase": "search", "template": "rg -n <ARG> <PATH>", "failed": False},
        {"phase": "edit", "template": "", "failed": False},
        {"phase": "inspect", "template": "sed -n <ARG> <PATH>", "failed": False},
        {"phase": "verify", "template": "python -m pytest <PATH> -q", "failed": False},
    ]
    friction = _friction_metrics(actions, verification_attempts=2)
    assert friction["exact_shell_replays"] == 3
    assert friction["failed_action_retries"] == 1
    assert friction["search_inspect_cycles"] == 1
    assert friction["post_edit_context_calls"] == 1
    assert friction["verification_retries"] == 1


def test_action_outcomes_attribute_failures_without_result_content() -> None:
    actions = [
        {
            "phase": "verify",
            "command_class": "verify",
            "template": "python -m pytest <PATH> -q",
            "failed": True,
            "result_size": 9000,
        },
        {
            "phase": "verify",
            "command_class": "verify",
            "template": "python -m pytest <PATH> -q",
            "failed": False,
            "result_size": 1000,
        },
    ]
    outcomes = _action_outcome_tables(actions)
    pytest_outcome = outcomes["shell_template_outcomes"]["python -m pytest <PATH> -q"]
    assert pytest_outcome == {
        "attempts": 2,
        "failures": 1,
        "no_results": 0,
        "retries_after_failure": 1,
        "result_bytes_bucket": 12288,
        "failure_classes": {},
    }
    assert "9000" not in json.dumps(outcomes)


def test_failure_classification_is_closed_and_discards_result_values() -> None:
    assert _failure_class("pytest: 3 failed, 10 passed") == "test_failure"
    assert _failure_class("ModuleNotFoundError: No module named 'private_pkg'") == ("dependency_unavailable")
    assert _failure_class("error: unrecognized argument '--private-flag'") == ("invalid_invocation")
    assert _failure_class("opaque private failure detail") == "unknown"


def test_intent_archetypes_are_closed_labels_without_prompt_text() -> None:
    assert _intent_archetypes("debug the auth failure and add a regression test") == [
        "debug",
        "implement",
        "verify",
        "security",
    ]


def test_project_scope_prefers_nearest_live_git_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    nested = root / "src" / "feature"
    (root / ".git").mkdir(parents=True)
    nested.mkdir(parents=True)
    _project_scope.cache_clear()
    scope, basis = _project_scope(str(nested))
    assert scope == os.path.normcase(os.path.normpath(str(root)))
    assert basis == "git_root"


def test_correction_detection_ignores_host_wrapper_blocks() -> None:
    assert _is_correction("<system-reminder>private host context</system-reminder>\nActually, use the safer path")


def test_backfill_persists_templates_but_no_raw_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    _write_claude_session(
        source / "session.jsonl",
        root,
        "session-private",
        'rg -n "customer-login-token" /private/customer && pytest -q /private/tests',
    )

    result = backfill_transcripts(root, source, source="claude")
    assert result["episodes"] == 1
    assert result["privacy_contract"]["sanitized_command_templates_persisted"] is True
    raw = (root / ".roam" / "transcript-episodes.jsonl").read_text(encoding="utf-8")
    assert "rg -n <SECRET> <PATH> && pytest -q <PATH>" in raw
    for secret in (
        "find the repeated customer login failure",
        "customer-login-token",
        "/private/customer",
        "secret-old",
        "secret-new",
    ):
        assert secret not in raw
    if os.name != "nt":
        assert stat.S_IMODE((root / ".roam" / "savings-backfill.key").stat().st_mode) == 0o600
        assert stat.S_IMODE((root / ".roam" / "transcript-episodes.jsonl").stat().st_mode) == 0o600


def test_backfill_rejects_redirected_private_state_without_touching_target(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    victim = tmp_path / "victim"
    root.mkdir()
    source.mkdir()
    victim.mkdir()
    try:
        (root / ".roam").symlink_to(victim, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    _write_claude_session(source / "session.jsonl", root, "session", "git status --short")

    with pytest.raises(TranscriptBackfillSafetyError, match="must not be redirected"):
        backfill_transcripts(root, source, source="claude")

    assert list(victim.iterdir()) == []


def test_backfill_cli_emits_structured_failure_for_redirected_private_state(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    victim = tmp_path / "victim"
    root.mkdir()
    source.mkdir()
    victim.mkdir()
    try:
        (root / ".roam").symlink_to(victim, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    _write_claude_session(source / "session.jsonl", root, "session", "git status --short")

    result = CliRunner().invoke(
        savings_backfill,
        ["--transcripts-dir", str(source), "--root", str(root), "--source", "claude"],
        obj={"json": True},
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["isError"] is True
    assert payload["error_code"] == "RUN_FAILED"
    assert payload["summary"]["state"] == "unsafe_path"
    assert payload["privacy_contract"]["raw_transcripts_persisted"] is False


@pytest.mark.parametrize("target_name", ["savings-backfill.key", "transcript-episodes.jsonl"])
def test_backfill_rejects_linked_private_files_without_clobbering_target(
    tmp_path: Path,
    target_name: str,
) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    state = root / ".roam"
    state.mkdir(parents=True)
    source.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve me", encoding="utf-8")
    try:
        (state / target_name).symlink_to(victim)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")
    _write_claude_session(source / "session.jsonl", root, "session", "git status --short")

    with pytest.raises(TranscriptBackfillSafetyError, match="regular, non-linked"):
        backfill_transcripts(root, source, source="claude")

    assert victim.read_text(encoding="utf-8") == "preserve me"


def test_backfill_ignores_legacy_predictable_temp_link(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    state = root / ".roam"
    state.mkdir(parents=True)
    source.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve me", encoding="utf-8")
    legacy_temp = state / f"transcript-episodes.jsonl.tmp-{os.getpid()}"
    try:
        legacy_temp.symlink_to(victim)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")
    _write_claude_session(source / "session.jsonl", root, "session", "git status --short")

    result = backfill_transcripts(root, source, source="claude")

    assert result["episodes"] == 1
    assert victim.read_text(encoding="utf-8") == "preserve me"
    assert legacy_temp.is_symlink()


@pytest.mark.parametrize("target_name", ["savings-backfill.key", "transcript-episodes.jsonl"])
def test_backfill_rejects_hardlinked_private_files_without_clobbering_target(
    tmp_path: Path,
    target_name: str,
) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    state = root / ".roam"
    state.mkdir(parents=True)
    source.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve me", encoding="utf-8")
    if os.name != "nt":
        victim.chmod(0o600)
    try:
        os.link(victim, state / target_name)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")
    _write_claude_session(source / "session.jsonl", root, "session", "git status --short")

    with pytest.raises(TranscriptBackfillSafetyError, match="must not be hard-linked"):
        backfill_transcripts(root, source, source="claude")

    assert victim.read_text(encoding="utf-8") == "preserve me"


def test_concurrent_first_run_uses_one_complete_private_key(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _index: _load_or_create_key(root), range(32)))

    assert len(set(keys)) == 1
    assert len(keys[0]) == 32
    assert (root / ".roam" / "savings-backfill.key").read_text(encoding="ascii") == keys[0].hex() + "\n"


def test_backfill_skips_transcript_links_that_escape_source_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    outside = tmp_path / "outside.jsonl"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    _write_claude_session(outside, root, "outside", "git status --short")
    try:
        (source / "linked.jsonl").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")

    result = backfill_transcripts(root, source, source="claude")

    assert result["files_considered"] == 0
    assert result["episodes"] == 0


def test_repeated_shell_patterns_surface_without_unlocking_live_claims(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    command = 'rg -n "login-latency" /private/customer && pytest -q /private/tests'
    for index in range(3):
        _write_claude_session(
            source / f"session-{index}.jsonl",
            root,
            f"session-{index}",
            command,
        )

    backfill_transcripts(root, source, source="claude")
    result = analyze_ledger(root)
    assert result["summary"]["measurement_admissible"] is False
    assert result["coverage"]["prospective_prompt_starts"] == 0
    assert result["coverage"]["historical_prompt_starts"] == 3
    candidates = result["historical_candidates"]
    assert any(
        candidate["kind"] == "shell_template"
        and candidate["episodes"] == 3
        and "rg -n <ARG> <PATH> && pytest -q <PATH>" in candidate["pattern"]
        for candidate in candidates
    )
    assert all("observed_wall_ms" not in candidate for candidate in candidates)
    assert all("associated_episode_wall_ms" in candidate for candidate in candidates)


def test_dry_run_does_not_create_backfill_files(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    root.mkdir()
    source.mkdir()
    _write_claude_session(source / "session.jsonl", root, "session", "git status --short")

    result = backfill_transcripts(root, source, source="claude", dry_run=True)
    assert result["state"] == "dry_run"
    assert not (root / ".roam" / "transcript-episodes.jsonl").exists()
    assert not (root / ".roam" / "savings-backfill.key").exists()


def test_codex_exec_control_code_is_not_mined_as_shell(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    rows = [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "type": "session_meta",
            "payload": {"cwd": str(root)},
        },
        {
            "timestamp": "2026-01-01T00:00:01Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "inspect the repository"},
        },
        {
            "timestamp": "2026-01-01T00:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "control",
                "input": "const secretCustomer = await computer.getForegroundApp();",
            },
        },
        {
            "timestamp": "2026-01-01T00:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "shell",
                "arguments": json.dumps({"cmd": "git status --short"}),
            },
        },
        {
            "timestamp": "2026-01-01T00:00:04Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 80,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 5,
                    }
                },
            },
        },
        {
            "timestamp": "2026-01-01T00:00:05Z",
            "type": "event_msg",
            "payload": {"type": "task_complete", "duration_ms": 3000},
        },
    ]
    (source / "rollout.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    backfill_transcripts(root, source, source="codex")
    raw = (root / ".roam" / "transcript-episodes.jsonl").read_text(encoding="utf-8")
    assert "git status --short" in raw
    assert "secretCustomer" not in raw
    assert "const " not in raw
    terminal = json.loads(raw.splitlines()[-1])
    assert terminal["input_tokens"] == 100
    assert terminal["cached_input_tokens"] == 80
    assert terminal["output_tokens"] == 20
    assert terminal["reasoning_output_tokens"] == 5
    assert terminal["phase_sequence_template"] == "other>orient"
    assert terminal["friction"]["orientation_calls"] == 1
    assert terminal["tool_result_bytes_bucket"] == 0


def test_search_exit_one_is_no_results_not_tool_failure(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    rows = [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "type": "session_meta",
            "payload": {"cwd": str(root)},
        },
        {
            "timestamp": "2026-01-01T00:00:01Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "find a missing symbol"},
        },
        {
            "timestamp": "2026-01-01T00:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "search",
                "arguments": json.dumps({"cmd": "rg -n missing_symbol src"}),
            },
        },
        {
            "timestamp": "2026-01-01T00:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "search",
                "output": "Process exited with code 1\nFinal output:\n",
            },
        },
        {
            "timestamp": "2026-01-01T00:00:04Z",
            "type": "event_msg",
            "payload": {"type": "task_complete", "duration_ms": 3000},
        },
    ]
    (source / "rollout.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    backfill_transcripts(root, source, source="codex")
    terminal = json.loads((root / ".roam" / "transcript-episodes.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    outcome = terminal["shell_template_outcomes"]["rg -n <ARG> <ARG>"]
    assert terminal["tool_errors"] == 0
    assert outcome["failures"] == 0
    assert outcome["no_results"] == 1


def test_multiple_transcript_roots_merge_into_one_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    first = tmp_path / "claude-a"
    second = tmp_path / "claude-b"
    (root / ".roam").mkdir(parents=True)
    first.mkdir()
    second.mkdir()
    _write_claude_session(first / "a.jsonl", root, "a", "git status --short")
    _write_claude_session(second / "b.jsonl", root, "b", "roam verify")

    result = backfill_transcripts(root, [first, second], source="claude")
    assert result["episodes"] == 2
    assert result["files_considered"] == 2
    assert len(result["source_directories"]) == 2
