"""Privacy and retention contracts for compile telemetry persistence."""

from __future__ import annotations

import json
import os
import stat
import time

from roam.plan import compiler as compiler

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
    assert entry["prefetched_keys"] == ["file_skeleton"]
    assert entry["model_calls_avoided_count"] == 2
    assert entry["savings"] == {
        "model_calls_avoided_count": 2,
        "prefetched_fact_count": 1,
        "cache_reuse_count": 0,
    }
    assert entry["ts"].endswith(":00:00Z")
    assert compiler._ensure_owner_only_file(str(log))
    if os.name != "nt":
        assert stat.S_IMODE(log.stat().st_mode) == 0o600


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
