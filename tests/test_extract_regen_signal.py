from __future__ import annotations

from tests._helpers.repo_root import repo_root

"""Tests for scripts/extract_regen_signal.py.

Synthetic-only — never touches /root/.claude/projects or /var/log/.
"""

import importlib.util
import json
from pathlib import Path

_REPO_ROOT = repo_root()
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "extract_regen_signal.py"


def _load_module():
    """Load scripts/extract_regen_signal.py as a module without requiring
    `scripts/` to be a package."""
    spec = importlib.util.spec_from_file_location("extract_regen_signal_under_test", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()


def _u(content, ts="2026-05-30T12:00:00.000Z", cwd="/tmp/fake-proj") -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "cwd": cwd,
        "sessionId": "abcdef12-0000-0000-0000-000000000000",
        "message": {"role": "user", "content": content},
    }


def _a(text="ok", ts="2026-05-30T12:00:01.000Z") -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_message_is_regen_markers():
    assert _mod.message_is_regen("Actually, I meant the other file.")
    assert _mod.message_is_regen("wait — that's the wrong symbol")
    assert _mod.message_is_regen("try again with a smaller window")
    assert _mod.message_is_regen("No, I wanted the prior version")
    assert _mod.message_is_regen("I meant the test file, not the source")
    assert _mod.message_is_regen("not quite — closer to what we had before")
    assert _mod.message_is_regen("Different approach please")


def test_message_is_regen_negatives():
    assert not _mod.message_is_regen("Please write a new test for foo()")
    assert not _mod.message_is_regen("Run the suite and report results")
    assert not _mod.message_is_regen("")
    # an "actually" mid-sentence is NOT an opening clause
    assert not _mod.message_is_regen("The function returns a list, but it is actually a tuple internally.")


def test_extract_text_string_and_blocks():
    assert _mod._extract_text("hello") == "hello"
    blocks = [
        {"type": "text", "text": "part one"},
        {"type": "tool_result", "content": "should be ignored"},
        {"type": "text", "text": "part two"},
    ]
    out = _mod._extract_text(blocks)
    assert "part one" in out and "part two" in out
    assert "should be ignored" not in out


def test_process_session_with_regen(tmp_path: Path):
    sid = "abcdef12-1111-2222-3333-444444444444"
    jsonl = tmp_path / f"{sid}.jsonl"
    rows = [
        _u("Please trace how guard-pr posts to GitHub.", ts="2026-05-30T10:00:00Z"),
        _a("Tracing now...", ts="2026-05-30T10:00:01Z"),
        _u("Actually, focus on the verdict mapping.", ts="2026-05-30T10:00:02Z"),
        _a("Got it.", ts="2026-05-30T10:00:03Z"),
        _u("try again but list functions only", ts="2026-05-30T10:00:04Z"),
        _a("ok.", ts="2026-05-30T10:00:05Z"),
        # malformed-style: blank user text (e.g., pure tool_result) should be skipped
    ]
    _write_jsonl(jsonl, rows)
    # append a malformed line manually
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
        fh.write("\n")

    rec = _mod.process_session_file(jsonl)
    assert rec is not None
    assert rec["session_id"] == sid
    assert rec["user_msg_count"] == 3
    assert rec["assistant_msg_count"] == 3
    assert rec["regen_signals"] == 2  # "Actually" + "try again"
    assert rec["date"] == "2026-05-30"
    assert rec["cwd"] == "/tmp/fake-proj"


def test_process_session_no_regen(tmp_path: Path):
    sid = "12345678-aaaa-bbbb-cccc-dddddddddddd"
    jsonl = tmp_path / f"{sid}.jsonl"
    rows = [
        _u("Add a CLI command for foo.", ts="2026-05-30T11:00:00Z"),
        _a("On it.", ts="2026-05-30T11:00:01Z"),
        _u("Also include tests, please.", ts="2026-05-30T11:00:02Z"),
        _a("Done.", ts="2026-05-30T11:00:03Z"),
    ]
    _write_jsonl(jsonl, rows)

    rec = _mod.process_session_file(jsonl)
    assert rec is not None
    assert rec["user_msg_count"] == 2
    assert rec["assistant_msg_count"] == 2
    assert rec["regen_signals"] == 0


def test_load_mode_from_sidecar(tmp_path: Path):
    modes_dir = tmp_path / "modes"
    modes_dir.mkdir()
    sid = "abcdef12-1111-2222-3333-444444444444"
    (modes_dir / "abcdef12.txt").write_text("compile\n", encoding="utf-8")

    assert _mod.load_mode(modes_dir, sid) == "compile"
    # unknown session → "unknown"
    assert _mod.load_mode(modes_dir, "ffffffff-...") == "unknown"


def test_run_end_to_end_joins_mode(tmp_path: Path):
    projects = tmp_path / "projects" / "-tmp-fake"
    projects.mkdir(parents=True)
    modes = tmp_path / "modes"
    modes.mkdir()
    out = tmp_path / "out" / "regen-signal.tsv"

    sid_regen = "11111111-aaaa-bbbb-cccc-dddddddddddd"
    sid_clean = "22222222-aaaa-bbbb-cccc-dddddddddddd"

    _write_jsonl(
        projects / f"{sid_regen}.jsonl",
        [
            _u("Walk me through the indexer.", ts="2026-05-30T09:00:00Z"),
            _a("Sure...", ts="2026-05-30T09:00:01Z"),
            _u("wait, start at parser.py instead", ts="2026-05-30T09:00:02Z"),
            _a("ok.", ts="2026-05-30T09:00:03Z"),
        ],
    )
    _write_jsonl(
        projects / f"{sid_clean}.jsonl",
        [
            _u("Write a unit test for bar().", ts="2026-05-30T09:30:00Z"),
            _a("Done.", ts="2026-05-30T09:30:01Z"),
        ],
    )

    (modes / sid_regen[:8].lower()).with_suffix(".txt").write_text("compile", encoding="utf-8")
    # sid_clean has no sidecar → mode should be "unknown"

    import datetime as _dt

    n = _mod.run(
        since=_dt.date(2020, 1, 1),
        projects_dir=tmp_path / "projects",
        modes_dir=modes,
        out_path=out,
    )
    assert n == 2
    assert out.is_file()

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    header = lines[0].split("\t")
    assert header == [
        "date",
        "session_id",
        "cwd",
        "user_msg_count",
        "assistant_msg_count",
        "regen_signals",
        "mode",
    ]

    by_sid: dict[str, list[str]] = {}
    for line in lines[1:]:
        cols = line.split("\t")
        by_sid[cols[1]] = cols

    assert sid_regen in by_sid
    assert by_sid[sid_regen][5] == "1"  # regen_signals
    assert by_sid[sid_regen][6] == "compile"

    assert sid_clean in by_sid
    assert by_sid[sid_clean][5] == "0"
    assert by_sid[sid_clean][6] == "unknown"


def test_large_file_skipped(tmp_path: Path, monkeypatch):
    sid = "99999999-aaaa-bbbb-cccc-dddddddddddd"
    jsonl = tmp_path / f"{sid}.jsonl"
    _write_jsonl(
        jsonl,
        [_u("hello", ts="2026-05-30T12:00:00Z"), _a("hi", ts="2026-05-30T12:00:01Z")],
    )
    # Lower the cap below the tiny file size to exercise the guard.
    monkeypatch.setattr(_mod, "_MAX_FILE_BYTES", 1)
    rec = _mod.process_session_file(jsonl)
    assert rec is None
