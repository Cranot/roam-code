"""Privacy and retention contracts for compile telemetry persistence."""

from __future__ import annotations

import json
import os
import re
import stat
import time

import pytest

from roam.compile_telemetry import COMPILE_TELEMETRY_SAFE_PROCEDURES
from roam.plan import compiler as compiler
from roam.security.owner_only import path_is_owner_only

_SENSITIVE_FIELDS = {
    "task",
    "task_hash",
    "task_prefix",
    "session_id",
    "turn_seq",
    "compiler_fp",
}


def _plan(prompt: str = "what does src/privacy_sentinel.py do"):
    plan = compiler.compile_plan(prompt)
    object.__setattr__(plan, "model_calls_avoided", ["local-a", "local-b"])
    return plan


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("ROAM_COMPILE_TELEMETRY", "1")
    monkeypatch.delenv("ROAM_TELEMETRY_LOCAL", raising=False)
    monkeypatch.delenv("ROAM_TELEMETRY_OFFTHREAD", raising=False)
    monkeypatch.delenv("ROAM_EPISODE_ID", raising=False)
    compiler._TELEMETRY_FINGERPRINT_KEYS.clear()


def test_compile_telemetry_is_off_without_explicit_consent(tmp_path, monkeypatch):
    monkeypatch.delenv("ROAM_COMPILE_TELEMETRY", raising=False)
    monkeypatch.delenv("ROAM_TELEMETRY_LOCAL", raising=False)
    monkeypatch.setenv("ROAM_TELEMETRY_OFFTHREAD", "1")
    (tmp_path / ".roam").mkdir()

    compiler._maybe_append_compile_telemetry(_plan(), {}, "full", 1.0, str(tmp_path))

    assert not (tmp_path / ".roam" / "compile-runs.jsonl").exists()


def test_compile_telemetry_omits_prompt_secrets_and_stable_identifiers(tmp_path, monkeypatch):
    _enable(monkeypatch)
    (tmp_path / ".roam").mkdir()
    prompt_secret = "PROMPT-SENTINEL-9f45 sk-abcdefghijklmnopqrstuvwxyz123456"
    identifier_secret = "RAW-ID-SENTINEL-77"
    monkeypatch.setenv("ROAM_SESSION_ID", identifier_secret + "-session")
    monkeypatch.setenv("ROAM_EPISODE_ID", identifier_secret + "-episode")
    monkeypatch.setenv("ROAM_TURN_SEQ", identifier_secret + "-turn")
    monkeypatch.setenv("ROAM_AGENT_MODE", identifier_secret + "-mode")
    env = {
        "plan": {
            "prefetched_facts": {
                "file_skeleton": {"raw": prompt_secret},
                "file_skeleton_definition": prompt_secret,
            }
        }
    }

    compiler._maybe_append_compile_telemetry(
        _plan(f"what does src/privacy_sentinel.py do {prompt_secret}"),
        env,
        "l1_probe",
        12.34,
        str(tmp_path),
    )

    log = tmp_path / ".roam" / "compile-runs.jsonl"
    raw = log.read_text(encoding="utf-8")
    entry = json.loads(raw)
    assert prompt_secret not in raw
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in raw
    assert identifier_secret not in raw
    assert _SENSITIVE_FIELDS.isdisjoint(entry)
    assert "episode_id" not in entry
    assert entry["agent_mode"] == "other"
    assert re.fullmatch(r"tfp_[0-9a-f]{32}", entry["task_fingerprint"])
    assert entry["prefetched_keys"] == ["file_skeleton"]
    assert entry["model_calls_avoided_count"] == 2
    assert entry["savings"] == {
        "model_calls_avoided_count": 2,
        "prefetched_fact_count": 1,
        "cache_reuse_count": 0,
    }
    assert entry["ts"].endswith(":00:00Z")
    assert compiler._ensure_owner_only_file(str(log))
    assert compiler._ensure_owner_only_file(str(tmp_path / ".roam" / "savings-backfill.key"))
    if os.name != "nt":
        assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_compile_telemetry_preserves_all_production_compile_modes() -> None:
    for mode in ("compile_codex", "compile_claude", "compile_maestro"):
        row = compiler._sanitize_compile_telemetry_row(
            {
                "ts": "2026-07-19T15:42:17Z",
                "procedure": "freeform_explore",
                "art_label": "facts",
                "agent_mode": mode,
            }
        )
        assert row is not None
        assert row["agent_mode"] == mode


def test_compile_procedure_registry_is_covered_by_closed_telemetry_vocabulary() -> None:
    assert set(compiler._ARTIFACT_POLICY) <= COMPILE_TELEMETRY_SAFE_PROCEDURES


def test_compile_telemetry_uses_stable_keyed_local_repeat_identity(tmp_path, monkeypatch):
    _enable(monkeypatch)
    (tmp_path / ".roam").mkdir()

    compiler._maybe_append_compile_telemetry(_plan("repeat this task"), {}, "full", 1.0, str(tmp_path))
    compiler._maybe_append_compile_telemetry(_plan("repeat this task"), {}, "full", 2.0, str(tmp_path))
    compiler._maybe_append_compile_telemetry(_plan("a different task"), {}, "full", 3.0, str(tmp_path))

    rows = [
        json.loads(line)
        for line in (tmp_path / ".roam" / "compile-runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["task_fingerprint"] == rows[1]["task_fingerprint"]
    assert rows[0]["task_fingerprint"] != rows[2]["task_fingerprint"]
    raw = json.dumps(rows)
    assert "repeat this task" not in raw
    assert "a different task" not in raw


def test_compile_telemetry_rewrites_legacy_rows_and_enforces_retention(tmp_path, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(compiler, "_COMPILE_TELEMETRY_MAX_RECORDS", 3)
    monkeypatch.setattr(compiler, "_COMPILE_TELEMETRY_MAX_BYTES", 4096)
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    log = log_dir / "compile-runs.jsonl"
    current_ts = time.strftime("%Y-%m-%dT%H:00:00Z", time.gmtime())
    legacy_rows = [
        {
            "ts": "2000-01-01T00:00:00Z",
            "task_prefix": "OLD-PROMPT-SENTINEL",
            "session_id": "OLD-SESSION-SENTINEL",
            "procedure": "freeform_explore",
        },
        {
            "ts": current_ts,
            "task_hash": "STABLE-HASH-SENTINEL",
            "episode_id": "EPISODE-SENTINEL",
            "procedure": "freeform_explore",
            "art_label": "full",
        },
    ]
    log.write_text("\n".join(json.dumps(row) for row in legacy_rows) + "\n", encoding="utf-8")

    plan = _plan()
    for compile_ms in range(6):
        compiler._maybe_append_compile_telemetry(plan, {}, "full", compile_ms, str(tmp_path))

    raw = log.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(rows) == 3
    assert [row["compile_ms"] for row in rows] == [3.0, 4.0, 5.0]
    assert "OLD-PROMPT-SENTINEL" not in raw
    assert "OLD-SESSION-SENTINEL" not in raw
    assert "STABLE-HASH-SENTINEL" not in raw
    assert "EPISODE-SENTINEL" not in raw
    assert all(_SENSITIVE_FIELDS.isdisjoint(row) for row in rows)
    assert all("episode_id" not in row for row in rows)
    assert log.stat().st_size <= compiler._COMPILE_TELEMETRY_MAX_BYTES


def test_compile_telemetry_preserves_only_strict_opaque_local_episode_id(tmp_path, monkeypatch):
    _enable(monkeypatch)
    (tmp_path / ".roam").mkdir()
    valid_episode_id = "ep_0123456789abcdef01234567"
    monkeypatch.setenv("ROAM_EPISODE_ID", valid_episode_id)

    compiler._maybe_append_compile_telemetry(_plan(), {}, "full", 1.0, str(tmp_path))

    log = tmp_path / ".roam" / "compile-runs.jsonl"
    first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert first["episode_id"] == valid_episode_id
    assert compiler._ensure_owner_only_file(str(log))

    invalid_ids = (
        "ep_0123456789ABCDEF01234567",
        "ep_f123456789abcdef0123456",
        "ep_0123456789abcdef012345678",
        "ep_../../PRIVATE-EPISODE-CANARY",
    )
    for compile_ms, invalid_id in enumerate(invalid_ids, start=2):
        monkeypatch.setenv("ROAM_EPISODE_ID", invalid_id)
        compiler._maybe_append_compile_telemetry(_plan(), {}, "full", compile_ms, str(tmp_path))

    raw = log.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[0]["episode_id"] == valid_episode_id
    assert all("episode_id" not in row for row in rows[1:])
    for invalid_id in invalid_ids:
        assert invalid_id not in raw


def test_compile_telemetry_enforces_byte_cap(tmp_path, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(compiler, "_COMPILE_TELEMETRY_MAX_RECORDS", 100)
    monkeypatch.setattr(compiler, "_COMPILE_TELEMETRY_MAX_BYTES", 1200)
    (tmp_path / ".roam").mkdir()
    plan = _plan()

    for compile_ms in range(12):
        compiler._maybe_append_compile_telemetry(plan, {}, "full", compile_ms, str(tmp_path))

    log = tmp_path / ".roam" / "compile-runs.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert rows
    assert len(rows) < 12
    assert log.stat().st_size <= 1200


def test_compile_telemetry_post_install_validation_failure_never_deletes_log(
    tmp_path,
    monkeypatch,
):
    from roam.security.owner_only import ensure_owner_only_path

    state = tmp_path / ".roam"
    state.mkdir()
    assert ensure_owner_only_path(state)
    log = state / "compile-runs.jsonl"
    log.write_bytes(b"previous-valid-row\n")
    assert ensure_owner_only_path(log)
    monkeypatch.setattr(compiler, "_owner_only_file_is_safe", lambda _path: False)

    installed = compiler._atomic_write_owner_only(str(log), b"replacement-valid-row\n")

    assert installed is False
    assert log.exists()
    assert log.read_bytes() == b"replacement-valid-row\n"


def test_compile_telemetry_lock_is_os_owned_and_never_stale_unlinked(tmp_path):
    from roam.security.owner_only import ensure_owner_only_path, path_is_owner_only

    state = tmp_path / ".roam"
    state.mkdir()
    assert ensure_owner_only_path(state)
    log = state / "compile-runs.jsonl"

    with compiler._compile_telemetry_interprocess_lock(str(log)) as first:
        assert first is True
        with compiler._compile_telemetry_interprocess_lock(str(log)) as second:
            assert second is False

    lock = state / "compile-runs.jsonl.lock"
    assert lock.exists()
    assert path_is_owner_only(lock)
    with compiler._compile_telemetry_interprocess_lock(str(log)) as reacquired:
        assert reacquired is True


def test_compile_telemetry_categories_are_closed_and_huge_numbers_are_dropped() -> None:
    canary = "customer_alpha_private_probe"
    row = compiler._sanitize_compile_telemetry_row(
        {
            "ts": "2026-07-19T15:42:17Z",
            "procedure": canary,
            "art_label": canary,
            "agent_mode": canary,
            "injection_advice": canary,
            "prefetched_keys": [canary, "file_skeleton"],
            "probe_timings_ms": {canary: 3.0, "inner_probe": 2.0},
            "model_calls_avoided_count": 10**400,
        }
    )

    assert row is not None
    assert row["procedure"] == "other"
    assert row["art_label"] == "other"
    assert row["agent_mode"] == "other"
    assert row["injection_advice"] == "other"
    assert row["prefetched_keys"] == ["file_skeleton", "other"]
    assert row["prefetched_fact_count"] == 2
    assert row["probe_timings_ms"] == {"inner_probe": 2.0, "other": 3.0}
    assert "model_calls_avoided_count" not in row
    assert canary not in repr(row)


def test_compile_telemetry_counts_unique_keys_and_rejects_invalid_ranges_and_dates() -> None:
    row = compiler._sanitize_compile_telemetry_row(
        {
            "ts": "2024-02-29T23:59:59Z",
            "procedure": "freeform_explore",
            "prefetched_keys": ["file_skeleton"] * 128,
            "classifier_conf": -0.01,
        }
    )

    assert row is not None
    assert row["prefetched_keys"] == ["file_skeleton"]
    assert row["prefetched_fact_count"] == 1
    assert row["savings"]["prefetched_fact_count"] == 1
    assert "classifier_conf" not in row
    assert compiler._sanitize_compile_telemetry_row({"ts": "2026-02-30T10:00:00Z"}) is None
    assert compiler._sanitize_compile_telemetry_row({"ts": "2026-01-01T24:00:00Z"}) is None


def test_compile_telemetry_lock_never_initializes_a_hardlinked_victim(tmp_path) -> None:
    from roam.security.owner_only import ensure_owner_only_path

    state = tmp_path / ".roam"
    state.mkdir()
    assert ensure_owner_only_path(state)
    victim = tmp_path / "empty-victim"
    victim.write_bytes(b"")
    lock = state / "compile-runs.jsonl.lock"
    try:
        os.link(victim, lock)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    with compiler._compile_telemetry_interprocess_lock(str(state / "compile-runs.jsonl")) as acquired:
        assert acquired is False

    assert victim.read_bytes() == b""


def test_compile_telemetry_existing_hardlink_is_never_read_or_acl_mutated(tmp_path) -> None:
    from roam.security.owner_only import ensure_owner_only_path

    state = tmp_path / ".roam"
    state.mkdir()
    assert ensure_owner_only_path(state)
    victim = tmp_path / "victim.jsonl"
    original = b'{"ts":"2026-07-19T10:00:00Z","procedure":"freeform_explore"}\n'
    victim.write_bytes(original)
    if os.name != "nt":
        victim.chmod(0o644)
    original_mode = stat.S_IMODE(victim.stat().st_mode)
    original_owner_only = path_is_owner_only(victim)
    log = state / "compile-runs.jsonl"
    try:
        os.link(victim, log)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    compiler._write_compile_telemetry_line(
        str(log),
        '{"ts":"2026-07-19T11:00:00Z","procedure":"freeform_explore"}',
    )

    assert victim.read_bytes() == original
    assert stat.S_IMODE(victim.stat().st_mode) == original_mode
    assert path_is_owner_only(victim) is original_owner_only
