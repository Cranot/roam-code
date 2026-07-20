"""Known-answer and admissibility tests for the episode savings ledger."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands.cmd_savings import savings
from roam.savings import (
    SavingsLedgerSafetyError,
    _episode_health_state,
    _interpret_historical_pattern,
    aggregate_savings_result,
    analyze_ledger,
    materialize_ledger,
)

_REPEATED_TASK_FINGERPRINT = "tfp_0123456789abcdef0123456789abcdef"


def _episode_id(index: int) -> str:
    return f"ep_{index:024x}"


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
        episode_id = _episode_id(i)
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
                "ts": "2026-01-01T01:00:00Z",
                "schema_version": 3,
                "task_fingerprint": _REPEATED_TASK_FINGERPRINT,
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
    assert result["coverage"]["repeat_identity_coverage_pct"] == 100.0
    assert result["repeat_candidates"][0]["episodes"] == 30
    assert result["repeat_candidates"][0]["task_fingerprint"] == _REPEATED_TASK_FINGERPRINT
    assert "task_prefix" not in result["repeat_candidates"][0]
    assert result["repeat_candidates"][0]["evidence_status"] == "candidate"


def test_compile_identity_gate_includes_hour_bucket_overlapping_first_prompt(
    tmp_path: Path,
) -> None:
    events, compiles = _fixture_rows()
    for event in events:
        event["ts"] = "2026-01-01T12:34:10Z" if event["event_type"] == "stop_decision" else "2026-01-01T12:34:00Z"
    for row in compiles:
        row["ts"] = "2026-01-01T12:00:00Z"
    compiles.extend(
        {
            "ts": "2026-01-01T12:00:00Z",
            "procedure": "freeform_explore",
            "art_label": "facts",
            "agent_mode": "hook",
        }
        for _ in range(3)
    )
    compiles.append(
        {
            "ts": "2026-01-01T13:00:00Z",
            "procedure": "freeform_explore",
            "art_label": "facts",
            "agent_mode": "hook",
            "episode_id": "ep_ffffffffffffffffffffffff",
        }
    )
    _write_jsonl(tmp_path / ".roam" / "episodes.jsonl", events)
    _write_jsonl(tmp_path / ".roam" / "compile-runs.jsonl", compiles)

    result = analyze_ledger(tmp_path)
    assert result["coverage"]["compile_identity_coverage_pct"] == 2.9
    assert result["summary"]["measurement_admissible"] is False
    assert result["summary"]["policy_admissible"] is False


def test_ambiguous_identified_rows_cannot_promote_compile_identity_coverage(
    tmp_path: Path,
) -> None:
    events, compiles = _fixture_rows()
    for event in events:
        event["ts"] = "2026-01-01T12:34:10Z" if event["event_type"] == "stop_decision" else "2026-01-01T12:34:00Z"
    for row in compiles:
        row["ts"] = "2026-01-01T13:00:00Z"
    compiles.extend(
        {
            "ts": "2026-01-01T12:00:00Z",
            "procedure": "freeform_explore",
            "art_label": "facts",
            "agent_mode": "hook",
            "episode_id": f"ep_ambiguous_{index}",
        }
        for index in range(70)
    )
    compiles.extend(
        {
            "ts": "2026-01-01T13:00:00Z",
            "procedure": "freeform_explore",
            "art_label": "facts",
            "agent_mode": "hook",
        }
        for _ in range(4)
    )
    _write_jsonl(tmp_path / ".roam" / "episodes.jsonl", events)
    _write_jsonl(tmp_path / ".roam" / "compile-runs.jsonl", compiles)

    result = analyze_ledger(tmp_path)
    assert result["coverage"]["compile_identity_coverage_pct"] == 28.8
    assert result["summary"]["measurement_admissible"] is False
    assert result["summary"]["policy_admissible"] is False


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


def test_materialization_redacts_legacy_compile_payload_and_reconciles_retention(tmp_path: Path) -> None:
    from roam.security.owner_only import path_is_owner_only

    events, compiles = _fixture_rows(count=1)
    canary = "PROMPT-PAYLOAD-CANARY-77"
    compiles[0].update(
        {
            "task_hash": "RAW-PROMPT-HASH-CANARY-78",
            "task_prefix": canary,
            "session_id": "SESSION-CANARY-79",
            "unknown_future_prompt": canary,
        }
    )
    events[0]["unknown_prompt_text"] = canary
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)

    first = analyze_ledger(tmp_path)
    database = Path(first["materialization"]["database"])
    assert path_is_owner_only(database)
    assert canary.encode() not in database.read_bytes()
    with contextlib.closing(sqlite3.connect(database)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(compile_records)")}
        assert "task_hash" not in columns
        assert "task_prefix" not in columns
        assert "payload_json" not in columns
        assert conn.execute("SELECT COUNT(*) FROM compile_records").fetchone()[0] == 1

    _write_jsonl(roam_dir / "compile-runs.jsonl", [])
    second = analyze_ledger(tmp_path)
    assert second["materialization"]["compile_records"] == 0
    with contextlib.closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM compile_records").fetchone()[0] == 0
    assert canary.encode() not in database.read_bytes()


def test_materialization_rejects_raw_text_hidden_in_known_event_fields(tmp_path: Path) -> None:
    events, compiles = _fixture_rows(count=3)
    canary = "PRIVATE-TRANSCRIPT-PAYLOAD-CANARY-83"
    for event in events:
        event["event_id"] = canary + str(event["turn_seq"])
        event["session_id"] = canary
        event["project_id"] = canary
        event["intent_archetypes"] = [canary]
        event["intent_simhash64"] = canary
        event["prompt_hmac_sha256"] = canary
        event["intervention_id"] = canary
        event["assignment_cluster"] = canary
        if event["terminal"]:
            event["trajectory_template"] = canary
            event["phase_sequence_template"] = canary
            event["command_sequence_template"] = canary
            event["shell_templates"] = {canary: 9}
            event["shell_ngrams"] = {canary: 9}
            event["tool_ngrams"] = {canary: 9}
            event["phase_ngrams"] = {canary: 9}
            event["friction"] = {canary: 9}
            event["shell_template_outcomes"] = {canary: {"attempts": 9, "failure_classes": {canary: 9}}}
            event["phase_outcomes"] = {canary: {"attempts": 9}}
            event["command_class_outcomes"] = {canary: {"attempts": 9}}
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)

    result = analyze_ledger(tmp_path)
    database = Path(result["materialization"]["database"])

    assert canary.encode() not in database.read_bytes()
    assert canary not in json.dumps(result, sort_keys=True)
    with contextlib.closing(sqlite3.connect(database)) as conn:
        payloads = [json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM episode_events")]
    assert all("trajectory_template" not in payload for payload in payloads)
    assert all("shell_templates" not in payload for payload in payloads)


def test_materialization_rebuild_purges_legacy_database_pages(tmp_path: Path) -> None:
    events, compiles = _fixture_rows(count=1)
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    database = Path(analyze_ledger(tmp_path)["materialization"]["database"])
    canary = "LEGACY-SQLITE-PROMPT-CANARY-80"

    with contextlib.closing(sqlite3.connect(database)) as conn:
        conn.executescript(
            """
            DROP TABLE compile_records;
            CREATE TABLE compile_records (
                record_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO compile_records(record_id, payload_json) VALUES (?, ?)",
            ("legacy", json.dumps({"task_prefix": canary, "session_id": canary})),
        )
        conn.execute("PRAGMA user_version=3")
        conn.commit()
    assert canary.encode() in database.read_bytes()

    result = analyze_ledger(tmp_path)

    assert result["materialization"]["compile_records"] == 1
    assert canary.encode() not in database.read_bytes()


def test_materialization_replace_failure_preserves_prior_database(tmp_path: Path, monkeypatch) -> None:
    events, compiles = _fixture_rows(count=1)
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    database = Path(analyze_ledger(tmp_path)["materialization"]["database"])
    before = database.read_bytes()

    def reject_install(*_args, **_kwargs):
        raise OSError("simulated atomic install failure")

    monkeypatch.setattr("roam.atomic_io._native_conditional_install", reject_install)
    with pytest.raises(OSError, match="simulated atomic install failure"):
        materialize_ledger(tmp_path)

    assert database.read_bytes() == before
    leftovers = list(roam_dir.glob(".episodes.sqlite.*.tmp"))
    if os.name == "nt":
        assert leftovers == []
    else:
        assert len(leftovers) == 1


def test_materialization_restores_destination_swapped_at_native_install_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roam import atomic_io

    events, compiles = _fixture_rows(count=1)
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    database = Path(analyze_ledger(tmp_path)["materialization"]["database"])
    concurrent = roam_dir / "concurrent-ledger-canary.sqlite"
    concurrent_payload = b"CONCURRENT-LEDGER-GENERATION-DO-NOT-CLOBBER"
    concurrent.write_bytes(concurrent_payload)
    if os.name != "nt":
        concurrent.chmod(0o600)

    real_install = atomic_io._native_conditional_install
    injected = False

    def swap_then_install(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            os.replace(concurrent, database)
        return real_install(*args, **kwargs)

    monkeypatch.setattr(atomic_io, "_native_conditional_install", swap_then_install)
    with pytest.raises(FileExistsError, match="destination changed before install"):
        materialize_ledger(tmp_path)

    assert injected is True
    assert database.read_bytes() == concurrent_payload


def test_materialization_rejects_destination_swapped_after_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import roam.savings as savings_module
    from roam.security.owner_only import ensure_owner_only_path

    events, compiles = _fixture_rows(count=1)
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    database = Path(analyze_ledger(tmp_path)["materialization"]["database"])
    concurrent = roam_dir / "post-install-concurrent.sqlite"
    concurrent_payload = b"POST-INSTALL-CONCURRENT-GENERATION"
    concurrent.write_bytes(concurrent_payload)
    assert ensure_owner_only_path(concurrent)
    real_install = savings_module.conditional_install_file

    def install_then_swap(*args, **kwargs) -> None:
        real_install(*args, **kwargs)
        os.replace(concurrent, database)

    monkeypatch.setattr(savings_module, "conditional_install_file", install_then_swap)
    with pytest.raises(SavingsLedgerSafetyError, match="installed savings ledger changed"):
        materialize_ledger(tmp_path)

    assert database.read_bytes() == concurrent_payload


def test_materialization_post_install_fsync_failure_preserves_valid_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import roam.savings as savings_module

    events, compiles = _fixture_rows(count=1)
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    database = Path(analyze_ledger(tmp_path)["materialization"]["database"])

    real_fsync = savings_module.os.fsync
    calls = 0

    def fail_second_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated post-install durability failure")
        real_fsync(descriptor)

    monkeypatch.setattr(savings_module.os, "fsync", fail_second_fsync)
    with pytest.raises(OSError, match="post-install durability failure"):
        materialize_ledger(tmp_path)

    assert database.exists()
    with contextlib.closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM episode_events").fetchone()[0] == 2


def test_materialization_reaps_crash_orphaned_private_tempfile(tmp_path: Path) -> None:
    events, compiles = _fixture_rows(count=1)
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    analyze_ledger(tmp_path)
    orphan = roam_dir / ".episodes.sqlite.0123456789abcdef.tmp"
    orphan.write_bytes(b"ORPHANED-LEDGER-CANARY-81")
    if os.name != "nt":
        orphan.chmod(0o600)
    os.utime(orphan, (1, 1))

    result = analyze_ledger(tmp_path)

    if os.name == "nt":
        assert not orphan.exists()
        assert result["materialization"]["orphan_temps_removed"] == 1
        assert result["materialization"]["orphan_temps_retained"] == 0
    else:
        assert orphan.exists()
        assert result["materialization"]["orphan_temps_removed"] == 0
        assert result["materialization"]["orphan_temps_retained"] == 1
        assert "private orphan tempfiles retained" in result["summary"]["verdict"]


def test_cleanup_preserves_fresh_live_materialization_tempfile(tmp_path: Path) -> None:
    import roam.savings as savings_module

    events, compiles = _fixture_rows(count=1)
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    database = Path(analyze_ledger(tmp_path)["materialization"]["database"])
    descriptor, temporary = savings_module._new_ledger_temp(database)
    try:
        assert savings_module._cleanup_orphaned_ledger_temps(database) == (0, 0)
        assert temporary.exists()
    finally:
        savings_module._unlink_if_same_file(temporary, descriptor)
        os.close(descriptor)


def test_materialization_serializes_cleanup_around_live_tempfile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import roam.savings as savings_module

    events, compiles = _fixture_rows(count=1)
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    analyze_ledger(tmp_path)

    real_exclusive = savings_module._exclusive_ledger_materialization
    real_new_temp = savings_module._new_ledger_temp
    live_created = threading.Event()
    release_first = threading.Event()
    second_attempted = threading.Event()
    second_acquired = threading.Event()
    live_path: list[Path] = []
    calls = 0

    @contextlib.contextmanager
    def observed_exclusive(path: Path):
        second = threading.current_thread().name == "second-materializer"
        if second:
            second_attempted.set()
        with real_exclusive(path):
            if second:
                second_acquired.set()
            yield

    def pausing_new_temp(path: Path) -> tuple[int, Path]:
        nonlocal calls
        result = real_new_temp(path)
        calls += 1
        if calls == 1:
            live_path.append(result[1])
            live_created.set()
            if not release_first.wait(10):
                raise TimeoutError("test did not release first materialization")
        return result

    monkeypatch.setattr(savings_module, "_exclusive_ledger_materialization", observed_exclusive)
    monkeypatch.setattr(savings_module, "_new_ledger_temp", pausing_new_temp)
    errors: list[BaseException] = []

    def run_materialization() -> None:
        try:
            materialize_ledger(tmp_path)
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=run_materialization, name="first-materializer")
    second = threading.Thread(target=run_materialization, name="second-materializer")
    second_started = False
    first.start()
    try:
        assert live_created.wait(10)
        second.start()
        second_started = True
        assert second_attempted.wait(10)
        assert not second_acquired.wait(0.2)
        assert live_path[0].exists()
    finally:
        release_first.set()
        first.join(10)
        if second_started:
            second.join(10)

    assert not first.is_alive()
    assert second_started and not second.is_alive()
    assert second_acquired.is_set()
    assert errors == []


def test_materialization_process_lock_preserves_other_process_tempfile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import roam.savings as savings_module

    events, compiles = _fixture_rows(count=1)
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    database = Path(analyze_ledger(tmp_path)["materialization"]["database"])
    child_code = """
import os
import sys
from pathlib import Path
from roam.savings import _exclusive_ledger_materialization, _new_ledger_temp, _unlink_if_same_file

database = Path(sys.argv[1])
with _exclusive_ledger_materialization(database):
    descriptor, temporary = _new_ledger_temp(database)
    print(temporary, flush=True)
    sys.stdin.readline()
    _unlink_if_same_file(temporary, descriptor)
    os.close(descriptor)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(database)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    live_temp_text = child.stdout.readline().strip()
    if not live_temp_text:
        assert child.stderr is not None
        pytest.fail(f"lock-holder process failed: {child.stderr.read()}")
    live_temp = Path(live_temp_text)

    attempted = threading.Event()
    real_try_lock = savings_module._try_lock_ledger_materialization

    def observed_try_lock(descriptor: int) -> bool:
        attempted.set()
        return real_try_lock(descriptor)

    monkeypatch.setattr(savings_module, "_try_lock_ledger_materialization", observed_try_lock)
    errors: list[BaseException] = []

    def run_materialization() -> None:
        try:
            materialize_ledger(tmp_path)
        except BaseException as exc:
            errors.append(exc)

    materializer = threading.Thread(target=run_materialization, name="cross-process-materializer")
    materializer.start()
    try:
        assert attempted.wait(10)
        assert materializer.is_alive()
        assert live_temp.exists()
    finally:
        assert child.stdin is not None
        child.stdin.write("\n")
        child.stdin.flush()
        child.stdin.close()
        child.wait(timeout=10)
        materializer.join(10)

    assert child.returncode == 0
    assert not materializer.is_alive()
    assert errors == []


@pytest.mark.parametrize("link_kind", ["symbolic", "hard"])
def test_materialization_rejects_linked_orphan_temp_without_touching_canary(
    tmp_path: Path,
    link_kind: str,
) -> None:
    events, compiles = _fixture_rows(count=1)
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)
    analyze_ledger(tmp_path)
    victim = tmp_path / "orphan-canary.txt"
    victim.write_text("PRESERVE-ORPHAN-CANARY-82", encoding="utf-8")
    if os.name != "nt":
        victim.chmod(0o600)
    orphan = roam_dir / ".episodes.sqlite.fedcba9876543210.tmp"
    try:
        if link_kind == "symbolic":
            orphan.symlink_to(victim)
        else:
            os.link(victim, orphan)
    except OSError as exc:
        pytest.skip(f"{link_kind} links are unavailable: {exc}")

    with pytest.raises(SavingsLedgerSafetyError, match="regular owner-only file"):
        materialize_ledger(tmp_path)

    assert victim.read_text(encoding="utf-8") == "PRESERVE-ORPHAN-CANARY-82"
    assert orphan.exists() or orphan.is_symlink()


def test_materialization_rejects_hard_linked_source_logs(tmp_path: Path) -> None:
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    os.link(outside, roam_dir / "episodes.jsonl")

    with pytest.raises(SavingsLedgerSafetyError, match="bounded owner-only regular file"):
        materialize_ledger(tmp_path)


def test_savings_cli_emits_structured_error_for_hard_linked_source(tmp_path: Path) -> None:
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    victim = tmp_path / "outside.jsonl"
    original = b"{}\n"
    victim.write_bytes(original)
    try:
        os.link(victim, roam_dir / "episodes.jsonl")
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    result = CliRunner().invoke(savings, ["--root", str(tmp_path)], obj={"json": True})

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["isError"] is True
    assert payload["error_code"] == "RUN_FAILED"
    assert payload["summary"]["state"] == "materialization_failed"
    assert payload["summary"]["partial_success"] is True
    assert victim.read_bytes() == original


def test_savings_jsonl_same_size_rewrite_rejects_buffered_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import roam.savings as savings_module
    from roam.security.owner_only import ensure_owner_only_path

    state = tmp_path / ".roam"
    state.mkdir()
    path = state / "episodes.jsonl"
    original = b'{"event_type":"prompt_submitted"}\n'
    replacement = b'{"event_type":"prompt_submitteX"}\n'
    assert len(original) == len(replacement)
    path.write_bytes(original)
    assert ensure_owner_only_path(state)
    assert ensure_owner_only_path(path)
    real_loads = savings_module.loads_bounded
    rewritten = False

    def rewrite_during_parse(value, **kwargs):
        nonlocal rewritten
        if not rewritten:
            rewritten = True
            before = path.stat()
            path.write_bytes(replacement)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        return real_loads(value, **kwargs)

    monkeypatch.setattr(savings_module, "loads_bounded", rewrite_during_parse)

    with pytest.raises(SavingsLedgerSafetyError, match="changed while it was read"):
        savings_module._read_jsonl(path, label="episode log", max_bytes=1024)


def test_materialization_rejects_oversized_prior_database_before_sqlite_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import roam.savings as savings_module
    from roam.security.owner_only import ensure_owner_only_path

    state = tmp_path / ".roam"
    state.mkdir()
    database = state / "episodes.sqlite"
    database.write_bytes(b"x" * 128)
    assert ensure_owner_only_path(state)
    assert ensure_owner_only_path(database)
    monkeypatch.setattr(savings_module, "MAX_LEDGER_DB_BYTES", 64)

    with pytest.raises(SavingsLedgerSafetyError, match="bounded regular private file"):
        materialize_ledger(tmp_path)


def test_privacy_v2_without_keyed_repeat_identity_cannot_report_policy_ready(tmp_path: Path) -> None:
    events, compiles = _fixture_rows()
    for row in compiles:
        row["schema_version"] = 2
        row.pop("task_fingerprint", None)
    _write_jsonl(tmp_path / ".roam" / "episodes.jsonl", events)
    _write_jsonl(tmp_path / ".roam" / "compile-runs.jsonl", compiles)

    result = analyze_ledger(tmp_path)

    assert result["summary"]["measurement_admissible"] is True
    assert result["summary"]["policy_admissible"] is False
    assert result["summary"]["state"] == "measurement_ready"
    assert result["coverage"]["repeat_identity_coverage_pct"] == 0.0
    assert result["repeat_candidates"] == []


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


def test_deep_jsonl_is_disclosed_without_decoder_recursion(tmp_path: Path) -> None:
    events, compiles = _fixture_rows()
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    deep = '{"nested":' + "[" * 200 + "0" + "]" * 200 + "}\n"
    with (roam_dir / "episodes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(deep)
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)

    result = analyze_ledger(tmp_path)

    assert result["materialization"]["invalid_event_rows"] == 1
    assert result["summary"]["integrity_clean"] is False
    assert result["summary"]["state"] == "insufficient_evidence"


def test_duplicate_json_keys_are_disclosed_as_invalid_ledger_rows(tmp_path: Path) -> None:
    events, compiles = _fixture_rows()
    roam_dir = tmp_path / ".roam"
    _write_jsonl(roam_dir / "episodes.jsonl", events)
    with (roam_dir / "episodes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"event_type":"prompt_submitted","event_type":"verification_passed"}\n')
    _write_jsonl(roam_dir / "compile-runs.jsonl", compiles)

    result = analyze_ledger(tmp_path)

    assert result["materialization"]["invalid_event_rows"] == 1
    assert result["summary"]["integrity_clean"] is False
    assert result["summary"]["state"] == "insufficient_evidence"


def test_jsonl_row_limit_counts_blank_and_invalid_physical_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import roam.savings as savings_module

    source = tmp_path / "physical-rows.jsonl"
    source.write_bytes(b"\nnot-json\n{}\n")
    monkeypatch.setattr(savings_module, "MAX_JSONL_ROWS", 2)

    with pytest.raises(SavingsLedgerSafetyError, match="exceeds the 2-row limit"):
        savings_module._read_jsonl(source, label="test JSONL", max_bytes=1024)


def test_jsonl_parse_elapsed_limit_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import roam.savings as savings_module

    source = tmp_path / "elapsed.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    clock = iter([100.0, 102.0])
    monkeypatch.setattr(savings_module, "MAX_JSONL_PARSE_SECONDS", 1.0)
    monkeypatch.setattr(savings_module.time, "monotonic", lambda: next(clock))

    with pytest.raises(SavingsLedgerSafetyError, match="exceeds the 1-second parse limit"):
        savings_module._read_jsonl(source, label="test JSONL", max_bytes=1024)


def test_jsonl_parse_deadline_expiring_inside_decoder_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import roam.savings as savings_module

    source = tmp_path / "decoder-elapsed.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    real_loads_bounded = savings_module.loads_bounded

    def delayed_loads_bounded(*args, **kwargs):
        return real_loads_bounded(*args, **kwargs)

    clock = iter([100.0, 100.1, 100.2, 102.0])
    monkeypatch.setattr(savings_module, "MAX_JSONL_PARSE_SECONDS", 1.0)
    monkeypatch.setattr(savings_module, "loads_bounded", delayed_loads_bounded)
    monkeypatch.setattr(savings_module.time, "monotonic", lambda: next(clock))

    with pytest.raises(SavingsLedgerSafetyError, match="exceeds the 1-second parse limit"):
        savings_module._read_jsonl(source, label="test JSONL", max_bytes=1024)


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
        if event["episode_id"] == _episode_id(0):
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
        episode_id = _episode_id(index)
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
                "episode_id": _episode_id(0),
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
                "episode_id": _episode_id(0),
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
