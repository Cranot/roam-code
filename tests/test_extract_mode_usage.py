from __future__ import annotations

from tests._helpers.repo_root import repo_root

"""Tests for scripts/extract_mode_usage.py.

Uses synthetic JSONL fixtures only; never touches the real
/root/.claude/projects directory.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = repo_root()
_SCRIPT = _REPO_ROOT / "scripts" / "extract_mode_usage.py"


def _load_module():
    """Load the script as a module without requiring scripts/ to be a package."""
    spec = importlib.util.spec_from_file_location("extract_mode_usage", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_mode_usage"] = mod
    spec.loader.exec_module(mod)
    return mod


emu = _load_module()


def _make_assistant_turn(ts: str, usage: dict) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": "ignored-here",
        "cwd": "/work/repo",
        "message": {
            "role": "assistant",
            "model": "test-model",
            "usage": usage,
        },
    }


def _make_user_turn(ts: str) -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "sessionId": "ignored-here",
        "cwd": "/work/repo",
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


@pytest.fixture
def fixtures(tmp_path: Path):
    projects = tmp_path / "projects" / "-work-repo"
    projects.mkdir(parents=True)
    modes = tmp_path / "modes"
    modes.mkdir()

    sid_a = "aaaaaaaa-1111-2222-3333-444444444444"
    file_a = projects / f"{sid_a}.jsonl"
    _write_jsonl(
        file_a,
        [
            _make_user_turn("2026-05-30T12:00:00.000Z"),
            _make_assistant_turn(
                "2026-05-30T12:00:02.000Z",
                {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 1000,
                    "cache_creation_input_tokens": 200,
                },
            ),
            _make_user_turn("2026-05-30T12:00:10.000Z"),
            _make_assistant_turn(
                "2026-05-30T12:00:14.000Z",
                {
                    "input_tokens": 30,
                    "output_tokens": 70,
                    "cache_read_input_tokens": 1500,
                    "cache_creation_input_tokens": 0,
                },
            ),
            # malformed line should be tolerated
        ],
    )
    # Append a junk line + a record without usage.
    with file_a.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write(json.dumps({"type": "system", "timestamp": "2026-05-30T12:00:20.000Z"}) + "\n")

    (modes / f"{sid_a[:8]}.txt").write_text("compile\n", encoding="utf-8")

    # Session B — no sidecar → mode=unknown
    sid_b = "bbbbbbbb-1111-2222-3333-444444444444"
    file_b = projects / f"{sid_b}.jsonl"
    _write_jsonl(
        file_b,
        [
            _make_assistant_turn(
                "2026-05-30T13:00:00.000Z",
                {
                    "input_tokens": 7,
                    "output_tokens": 11,
                    "cache_read_input_tokens": 13,
                    "cache_creation_input_tokens": 17,
                },
            ),
        ],
    )

    return {
        "tmp": tmp_path,
        "projects_dir": tmp_path / "projects",
        "modes_dir": modes,
        "sid_a": sid_a,
        "sid_b": sid_b,
        "file_a": file_a,
        "file_b": file_b,
    }


def test_aggregate_session_token_sums(fixtures):
    agg = emu.aggregate_session(fixtures["file_a"], session_id=fixtures["sid_a"])
    assert agg is not None
    assert agg["n_turns"] == 2
    assert agg["total_input_tokens"] == 130
    assert agg["total_output_tokens"] == 120
    assert agg["total_cache_read_tokens"] == 2500
    assert agg["total_cache_creation_tokens"] == 200
    assert agg["cwd"] == "/work/repo"
    # 14s span from 12:00:00 → 12:00:14 (junk + system at :20 stretches to 20s)
    assert agg["total_duration_ms"] >= 14_000


def test_aggregate_session_ttft_from_user_assistant_gap(fixtures):
    agg = emu.aggregate_session(fixtures["file_a"], session_id=fixtures["sid_a"])
    # Two gaps: 2000ms and 4000ms → median 3000.
    assert agg["ttft_ms_median"] == 3000


def test_mode_join_compile(fixtures):
    mode = emu.read_mode(fixtures["modes_dir"], fixtures["sid_a"])
    assert mode == "compile"


def test_mode_join_missing_sidecar_returns_unknown(fixtures):
    mode = emu.read_mode(fixtures["modes_dir"], fixtures["sid_b"])
    assert mode == "unknown"


def test_run_writes_tsv_with_both_sessions(fixtures, tmp_path):
    out = tmp_path / "out.tsv"
    since = emu.parse_since("2026-05-01")
    n = emu.run(fixtures["projects_dir"], fixtures["modes_dir"], out, since)
    assert n == 2

    lines = out.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    assert header == emu.TSV_HEADER

    rows = [dict(zip(header, line.split("\t"))) for line in lines[1:]]
    by_sid = {r["session_id"]: r for r in rows}
    a = by_sid[fixtures["sid_a"]]
    b = by_sid[fixtures["sid_b"]]
    assert a["mode"] == "compile"
    assert a["total_input_tokens"] == "130"
    assert a["total_output_tokens"] == "120"
    assert b["mode"] == "unknown"
    assert b["n_turns"] == "1"


def test_oversized_file_is_skipped(fixtures, monkeypatch):
    monkeypatch.setattr(emu, "MAX_FILE_BYTES", 10)
    assert emu.aggregate_session(fixtures["file_a"], session_id=fixtures["sid_a"]) is None


def test_since_filter_excludes_old_sessions(fixtures, tmp_path):
    out = tmp_path / "out.tsv"
    # Sessions are dated 2026-05-30; filter at 2026-06-15 → exclude all.
    since = emu.parse_since("2026-06-15")
    n = emu.run(fixtures["projects_dir"], fixtures["modes_dir"], out, since)
    assert n == 0


def test_empty_sidecar_returns_unknown(tmp_path, fixtures):
    sidecar = fixtures["modes_dir"] / f"{fixtures['sid_b'][:8]}.txt"
    sidecar.write_text("", encoding="utf-8")
    assert emu.read_mode(fixtures["modes_dir"], fixtures["sid_b"]) == "unknown"
