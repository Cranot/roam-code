"""Privacy and known-answer tests for historical transcript backfill."""

from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from click.testing import CliRunner

import roam.transcript_backfill as transcript_backfill_module
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


def test_sanitizer_collapses_unknown_executables_flags_and_subcommands() -> None:
    canaries = ("ACME_INTERNAL_TOOL", "--customer-alpha", "hunter2", "customer-private-verb")
    template = sanitize_command_template(
        "ACME_INTERNAL_TOOL --customer-alpha hunter2 && git customer-private-verb private-value"
    )

    assert template == "<EXEC> <FLAG> <ARG> && git <SUBCOMMAND> <ARG>"
    assert all(canary.lower() not in template.lower() for canary in canaries)


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


def test_project_scope_uses_explicit_root_lexically_without_filesystem_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = str(tmp_path / "repo")
    nested = str(tmp_path / "repo" / "src" / "feature")

    def forbidden_probe(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("transcript CWD classification attempted a filesystem probe")

    for method in ("resolve", "stat", "lstat", "exists", "is_dir"):
        monkeypatch.setattr(Path, method, forbidden_probe)

    scope, basis = _project_scope(nested, root)
    assert scope == os.path.normcase(os.path.normpath(str(root)))
    assert basis == "workspace"

    hostile = r"\\attacker.invalid\share\repo"
    hostile_scope, hostile_basis = _project_scope(hostile, root)
    assert hostile_scope == os.path.normcase(os.path.normpath(hostile))
    assert hostile_basis == "workspace"


@pytest.mark.parametrize(
    "hostile_cwd",
    (r"\\attacker.invalid\share\repo", "/net/attacker.invalid/automount/repo"),
)
def test_transcript_cwd_is_inert_during_end_to_end_backfill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hostile_cwd: str,
) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    root.mkdir()
    source.mkdir()
    _write_claude_session(source / "session.jsonl", hostile_cwd, "hostile", "git status --short")

    marker = "attacker.invalid"

    def reject_hostile(value: object) -> None:
        if isinstance(value, int):
            return
        try:
            rendered = os.fsdecode(os.fspath(value))
        except TypeError:
            return
        assert marker not in rendered, f"filesystem probe reached transcript CWD: {rendered}"

    for method in ("resolve", "expanduser", "stat", "lstat", "exists", "is_dir"):
        original = getattr(Path, method)

        def guarded_path(
            self: Path,
            *args: object,
            _original: object = original,
            **kwargs: object,
        ) -> object:
            reject_hostile(self)
            return _original(self, *args, **kwargs)  # type: ignore[operator]

        monkeypatch.setattr(Path, method, guarded_path)

    for function_name in ("stat", "lstat", "scandir"):
        original = getattr(os, function_name)

        def guarded_os(
            value: object,
            *args: object,
            _original: object = original,
            **kwargs: object,
        ) -> object:
            reject_hostile(value)
            return _original(value, *args, **kwargs)  # type: ignore[operator]

        monkeypatch.setattr(os, function_name, guarded_os)

    for function_name in ("exists", "isdir"):
        original = getattr(os.path, function_name)

        def guarded_os_path(
            value: object,
            *args: object,
            _original: object = original,
            **kwargs: object,
        ) -> object:
            reject_hostile(value)
            return _original(value, *args, **kwargs)  # type: ignore[operator]

        monkeypatch.setattr(os.path, function_name, guarded_os_path)

    result = backfill_transcripts(
        root,
        source,
        source="claude",
        all_projects=True,
        dry_run=True,
    )

    assert result["episodes"] == 1
    assert result["privacy_contract"]["paths_persisted"] is False


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
    if os.name == "nt":
        from roam.security.owner_only import path_is_owner_only

        assert path_is_owner_only(root / ".roam")
        assert path_is_owner_only(root / ".roam" / "savings-backfill.key")
        assert path_is_owner_only(root / ".roam" / "transcript-episodes.jsonl")
    else:
        assert stat.S_IMODE((root / ".roam" / "savings-backfill.key").stat().st_mode) == 0o600
        assert stat.S_IMODE((root / ".roam" / "transcript-episodes.jsonl").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode tightening regression")
def test_backfill_tightens_normal_umask_created_roam_directory(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    state = root / ".roam"
    state.mkdir(parents=True)
    state.chmod(0o755)
    source.mkdir()
    _write_claude_session(source / "session.jsonl", root, "session", "git status --short")

    result = backfill_transcripts(root, source, source="claude")

    assert result["episodes"] == 1
    assert stat.S_IMODE(state.stat().st_mode) == 0o700


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


def test_private_key_prepare_failure_never_publishes_partial_final_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / ".roam"
    state.mkdir()
    key_path = state / "savings-backfill.key"
    monkeypatch.setattr(
        transcript_backfill_module,
        "ensure_owner_only_file_descriptor",
        lambda _descriptor, _path: False,
    )

    with pytest.raises(TranscriptBackfillSafetyError, match="key tempfile was not created"):
        transcript_backfill_module._create_private_key(key_path)

    assert not key_path.exists()
    retained = list(state.glob(".savings-backfill.key.*.tmp"))
    if os.name == "nt":
        assert retained == []
    else:
        assert len(retained) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL inheritance regression")
def test_owner_only_directory_makes_new_children_private_at_creation(tmp_path: Path) -> None:
    from roam.security.owner_only import (
        create_owner_only_directory,
        open_new_owner_only_file,
        path_is_owner_only,
        pinned_owner_only_directory,
    )

    state = tmp_path / "private"
    assert create_owner_only_directory(state)
    child = state / "before-callback.tmp"
    descriptor = open_new_owner_only_file(child)
    try:
        assert path_is_owner_only(child)
    finally:
        os.close(descriptor)

    moved = tmp_path / "swapped"
    with pytest.raises(PermissionError, match="changed during operation"):
        with pinned_owner_only_directory(state):
            state.rename(moved)
    assert moved.is_dir()


def test_owner_only_descriptor_rejects_a_different_path(tmp_path: Path) -> None:
    from roam.security.owner_only import ensure_owner_only_file_descriptor

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"")
    second.write_bytes(b"")
    descriptor = os.open(first, os.O_RDWR)
    try:
        assert ensure_owner_only_file_descriptor(descriptor, second) is False
    finally:
        os.close(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL regression")
def test_existing_windows_key_must_already_be_current_user_only(tmp_path: Path) -> None:
    from roam.security.owner_only import path_is_owner_only

    root = tmp_path / "repo"
    state = root / ".roam"
    state.mkdir(parents=True)
    key = state / "savings-backfill.key"
    key.write_text("00" * 32 + "\n", encoding="ascii")
    if path_is_owner_only(key):
        pytest.skip("test temp root already creates protected owner-only files")

    with pytest.raises(TranscriptBackfillSafetyError, match="not owner-only"):
        _load_or_create_key(root)


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


@pytest.mark.skipif(os.name != "nt", reason="Windows dry-run DACL regression")
def test_dry_run_does_not_tighten_existing_windows_acls(tmp_path: Path) -> None:
    from roam.security.owner_only import path_is_owner_only

    root = tmp_path / "repo"
    source = tmp_path / "transcripts"
    state = root / ".roam"
    state.mkdir(parents=True)
    source.mkdir()
    index = state / "index.db"
    index.write_bytes(b"public-before-preview")
    before = (path_is_owner_only(state), path_is_owner_only(index))
    if any(before):
        pytest.skip("test temp root already creates owner-only state")
    _write_claude_session(source / "session.jsonl", root, "session", "git status --short")

    result = backfill_transcripts(root, source, source="claude", dry_run=True)

    assert result["state"] == "dry_run"
    assert (path_is_owner_only(state), path_is_owner_only(index)) == before


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


def test_schema_drifted_usage_numbers_do_not_abort_backfill(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    path = source / "drift.jsonl"
    _write_claude_session(path, root, "drift", "git status --short")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[1]["message"]["usage"] = {
        "input_tokens": [1],
        "output_tokens": {"unexpected": 2},
        "cache_read_input_tokens": 10**400,
        "cache_creation_input_tokens": True,
    }
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = backfill_transcripts(root, source, source="claude")
    terminal = json.loads((root / ".roam" / "transcript-episodes.jsonl").read_text(encoding="utf-8").splitlines()[-1])

    assert result["episodes"] == 1
    assert terminal["input_tokens"] == 0
    assert terminal["output_tokens"] == 0
    assert terminal["cached_input_tokens"] == 0
    assert terminal["cache_creation_tokens"] == 0


def test_backfill_discloses_bounded_file_selection(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    for index in range(3):
        path = source / f"{index}.jsonl"
        _write_claude_session(path, root, str(index), "git status --short")
        os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))

    result = backfill_transcripts(root, source, source="claude", max_files=1)

    assert result["candidate_files_seen"] == 3
    assert result["files_considered"] == 1
    assert result["files_truncated"] == 2
    assert result["resource_limits"]["max_files_per_source"] == 1


def test_backfill_caps_directory_enumeration_before_unbounded_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    for index in range(4):
        _write_claude_session(source / f"{index}.jsonl", root, str(index), "git status --short")
    monkeypatch.setattr(transcript_backfill_module, "MAX_TRANSCRIPT_DIRECTORY_ENTRIES", 2)

    result = backfill_transcripts(root, source, source="claude", max_files=10)

    assert result["directory_entries_scanned"] == 2
    assert result["traversal_truncated"] == 1
    assert result["files_considered"] <= 2
    assert result["resource_limits"]["max_directory_entries_per_source"] == 2
    assert result["resource_limits"]["max_directory_entries_global"] == 2


@pytest.mark.parametrize(
    ("limit_name", "expected_reason"),
    (
        ("MAX_TRANSCRIPT_DIRECTORIES", "directories"),
        ("MAX_TRANSCRIPT_DIRECTORY_ENTRIES", "entries"),
    ),
)
def test_backfill_shares_discovery_budgets_across_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    expected_reason: str,
) -> None:
    root = tmp_path / "repo"
    first = tmp_path / "sessions-a"
    second = tmp_path / "sessions-b"
    root.mkdir()
    first.mkdir()
    second.mkdir()
    _write_claude_session(first / "first.jsonl", root, "first", "git status --short")
    _write_claude_session(second / "second.jsonl", root, "second", "git diff --stat")
    monkeypatch.setattr(transcript_backfill_module, limit_name, 1)
    scanned_roots: list[str] = []
    real_scandir = os.scandir

    def tracking_scandir(path: object) -> object:
        scanned_roots.append(os.path.normcase(os.path.normpath(os.fsdecode(os.fspath(path)))))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", tracking_scandir)

    result = backfill_transcripts(
        root,
        [first, second],
        source="claude",
        dry_run=True,
    )

    assert scanned_roots == [os.path.normcase(os.path.normpath(str(first.resolve())))]
    assert result["directories_scanned"] == 1
    assert result["directory_entries_scanned"] == 1
    assert result["traversal_truncated"] == 1
    assert result["discovery_limit_reached"] == expected_reason


def test_backfill_elapsed_budget_starts_before_discovery_and_stops_later_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    first = tmp_path / "sessions-a"
    second = tmp_path / "sessions-b"
    root.mkdir()
    first.mkdir()
    second.mkdir()
    _write_claude_session(first / "first.jsonl", root, "first", "git status --short")
    _write_claude_session(second / "second.jsonl", root, "second", "git diff --stat")

    class FakeClock:
        now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = FakeClock()
    scanned_roots: list[str] = []
    real_scandir = os.scandir

    def expiring_scandir(path: object) -> object:
        scanned_roots.append(os.path.normcase(os.path.normpath(os.fsdecode(os.fspath(path)))))
        iterator = real_scandir(path)
        clock.now = 2.0
        return iterator

    monkeypatch.setattr(transcript_backfill_module, "MAX_TRANSCRIPT_ELAPSED_SECONDS", 1.0)
    monkeypatch.setattr(transcript_backfill_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(os, "scandir", expiring_scandir)

    result = backfill_transcripts(
        root,
        [first, second],
        source="claude",
        dry_run=True,
    )

    assert scanned_roots == [os.path.normcase(os.path.normpath(str(first.resolve())))]
    assert result["directory_entries_scanned"] == 0
    assert result["files_processed"] == 0
    assert result["aggregate_limit_reached"] == "elapsed"
    assert result["discovery_limit_reached"] == "elapsed"
    assert result["traversal_truncated"] == 1


def test_backfill_discloses_per_file_row_limit_without_partial_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    _write_claude_session(source / "many-rows.jsonl", root, "rows", "git status --short")
    monkeypatch.setattr(transcript_backfill_module, "MAX_TRANSCRIPT_ROWS_PER_FILE", 1)

    result = backfill_transcripts(root, source, source="claude")

    assert result["episodes"] == 0
    assert result["degraded_transcript_files"] == 1
    assert result["transcript_read_states"] == {"row_limit_reached": 1}


def test_backfill_stops_before_per_file_event_graph_can_grow_unbounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    _write_claude_session(source / "events.jsonl", root, "events", "git status --short")
    monkeypatch.setattr(transcript_backfill_module, "MAX_TRANSCRIPT_EVENTS_PER_FILE", 1)

    with pytest.raises(TranscriptBackfillSafetyError, match="per-file limit"):
        backfill_transcripts(root, source, source="claude")


def test_deep_json_row_is_skipped_before_decoder_recursion(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    path = source / "deep.jsonl"
    _write_claude_session(path, root, "deep", "git status --short")
    valid = path.read_text(encoding="utf-8")
    deep = '{"nested":' + "[" * 200 + "0" + "]" * 200 + "}\n"
    path.write_text(deep + valid, encoding="utf-8")

    result = backfill_transcripts(root, source, source="auto")

    assert result["episodes"] == 1
    assert result["degraded_transcript_files"] == 1
    assert result["transcript_read_states"] == {"partial_invalid_rows": 1}
    assert result["invalid_transcript_rows"] == 1


def test_duplicate_json_keys_are_skipped_and_disclosed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    path = source / "duplicate.jsonl"
    _write_claude_session(path, root, "duplicate", "git status --short")
    valid = path.read_text(encoding="utf-8")
    duplicate = '{"type":"user","type":"assistant","message":{}}\n'
    path.write_text(duplicate + valid, encoding="utf-8")

    result = backfill_transcripts(root, source, source="auto")

    assert result["episodes"] == 1
    assert result["degraded_transcript_files"] == 1
    assert result["transcript_read_states"] == {"partial_invalid_rows": 1}
    assert result["invalid_transcript_rows"] == 1


def test_invalid_utf8_row_is_skipped_and_disclosed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    path = source / "invalid-utf8.jsonl"
    _write_claude_session(path, root, "invalid-utf8", "git status --short")
    path.write_bytes(b'{"bad":"\xff"}\n' + path.read_bytes())

    result = backfill_transcripts(root, source, source="auto")

    assert result["episodes"] == 1
    assert result["transcript_read_states"] == {"partial_invalid_rows": 1}
    assert result["invalid_transcript_rows"] == 1


def test_same_size_transcript_rewrite_discards_all_buffered_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    root.mkdir()
    source.mkdir()
    path = source / "rewrite.jsonl"
    _write_claude_session(path, root, "rewrite", "git status --short")
    original = path.read_bytes()
    replacement = original.replace(b"git status", b"git statux", 1)
    assert len(replacement) == len(original)
    diagnostics: dict = {}
    real_loads = transcript_backfill_module.loads_bounded
    rewritten = False

    def rewrite_during_parse(value, **kwargs):
        nonlocal rewritten
        if not rewritten:
            rewritten = True
            before = path.stat()
            path.write_bytes(replacement)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        return real_loads(value, **kwargs)

    monkeypatch.setattr(transcript_backfill_module, "loads_bounded", rewrite_during_parse)

    rows = list(transcript_backfill_module._iter_jsonl(path, source.resolve(), diagnostics))

    assert rows == []
    assert diagnostics["state"] == "changed_during_read"
    assert diagnostics["bytes_read"] == len(original)


def test_backfill_caps_aggregate_input_bytes_on_newest_file_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    older = source / "older.jsonl"
    newer = source / "newer.jsonl"
    _write_claude_session(older, root, "older", "git status --short")
    _write_claude_session(newer, root, "newer", "git diff --stat")
    now = newer.stat().st_mtime
    os.utime(older, (now - 10, now - 10))
    os.utime(newer, (now, now))
    monkeypatch.setattr(transcript_backfill_module, "MAX_TRANSCRIPT_AGGREGATE_BYTES", newer.stat().st_size)

    result = backfill_transcripts(root, source, source="claude")

    assert result["episodes"] == 1
    assert result["files_processed"] == 1
    assert result["input_files_skipped"] == 1
    assert result["aggregate_input_bytes"] == newer.stat().st_size
    assert result["aggregate_limit_reached"] == "bytes"
    assert result["resource_limits"]["max_aggregate_input_bytes"] == newer.stat().st_size


def test_backfill_caps_aggregate_rows_without_emitting_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    _write_claude_session(source / "rows.jsonl", root, "rows", "git status --short")
    monkeypatch.setattr(transcript_backfill_module, "MAX_TRANSCRIPT_AGGREGATE_ROWS", 1)

    result = backfill_transcripts(root, source, source="claude")

    assert result["episodes"] == 0
    assert result["files_processed"] == 0
    assert result["input_files_skipped"] == 1
    assert result["aggregate_rows_scanned"] == 1
    assert result["aggregate_limit_reached"] == "rows"

    cli_result = CliRunner().invoke(
        savings_backfill,
        ["--transcripts-dir", str(source), "--root", str(root), "--source", "claude"],
        obj={"json": True},
    )
    assert cli_result.exit_code == 0, cli_result.output
    summary = json.loads(cli_result.output)["summary"]
    assert summary["partial_success"] is True
    assert "incomplete evidence:" in summary["verdict"]
    assert "aggregate_input_truncated" in summary["verdict"]


def test_backfill_caps_elapsed_input_work_before_opening_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    source = tmp_path / "sessions"
    (root / ".roam").mkdir(parents=True)
    source.mkdir()
    _write_claude_session(source / "elapsed.jsonl", root, "elapsed", "git status --short")

    class FakeClock:
        now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = FakeClock()
    real_candidate_files = transcript_backfill_module._candidate_files

    def expiring_discovery(*args: object, **kwargs: object) -> object:
        discovered = real_candidate_files(*args, **kwargs)
        clock.now = 2.0
        return discovered

    monkeypatch.setattr(transcript_backfill_module, "MAX_TRANSCRIPT_ELAPSED_SECONDS", 1.0)
    monkeypatch.setattr(transcript_backfill_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(transcript_backfill_module, "_candidate_files", expiring_discovery)

    result = backfill_transcripts(root, source, source="claude")

    assert result["episodes"] == 0
    assert result["files_processed"] == 0
    assert result["aggregate_input_bytes"] == 0
    assert result["aggregate_limit_reached"] == "elapsed"
    assert result["discovery_limit_reached"] == "elapsed"
