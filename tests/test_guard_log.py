"""Tests for the persistent verdict log (`.roam/verdict-log.jsonl`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from roam.guard_log import (
    LOG_FILENAME,
    append_log_entry,
    build_log_entry,
    log_path_for,
    read_log_entries,
)
from tests._helpers.repo_root import repo_root


def _sample_v1() -> dict:
    return {
        "schema": "agent_change_proof_bundle",
        "schema_version": "1.0",
        "changed_files": ["src/foo.py", "src/bar.py"],
        "verification_contract": {
            "required": [{"command": "pytest", "kind": "test", "reason": "x"}],
            "skipped": [],
        },
        "executed_checks": [{"command": "pytest", "status": "pass"}],
        "missing_checks": [],
        "verdict": {
            "value": "pass",
            "reasons": [{"code": "all_required_passed"}],
        },
        "risk": {"level": "low"},
        "repo": {"head_sha": "abc1234567890def"},
    }


def test_build_log_entry_shape(tmp_path):
    v1 = _sample_v1()
    bundle_path = tmp_path / "main.json"
    entry = build_log_entry(v1=v1, bundle_path=bundle_path)
    # Required keys
    for k in (
        "ts",
        "branch",
        "bundle",
        "verdict",
        "changed_files",
        "required",
        "executed",
        "missing",
        "risk_level",
        "reasons",
    ):
        assert k in entry, f"missing key {k}"
    assert entry["verdict"] == "pass"
    assert entry["changed_files"] == 2
    assert entry["risk_level"] == "low"


def test_build_log_entry_recovers_branch_from_filename(tmp_path):
    v1 = _sample_v1()
    bundle_path = tmp_path / "feat__refactor__retry.json"
    entry = build_log_entry(v1=v1, bundle_path=bundle_path)
    # `__` → `/`
    assert entry["branch"] == "feat/refactor/retry"


def test_append_log_entry_creates_file(tmp_path):
    v1 = _sample_v1()
    entry = build_log_entry(v1=v1, bundle_path=tmp_path / "main.json")
    ok = append_log_entry(tmp_path, entry)
    assert ok
    log = log_path_for(tmp_path)
    assert log.is_file()
    line = log.read_text().strip()
    parsed = json.loads(line)
    assert parsed["verdict"] == "pass"


def test_append_log_entry_appends_multiple(tmp_path):
    v1 = _sample_v1()
    entry = build_log_entry(v1=v1, bundle_path=tmp_path / "main.json")
    for _ in range(3):
        append_log_entry(tmp_path, entry)
    log_text = log_path_for(tmp_path).read_text().strip()
    assert len(log_text.splitlines()) == 3


def test_read_log_entries_returns_most_recent_first(tmp_path):
    # Write 3 entries with different verdicts.
    v1 = _sample_v1()
    for verdict in ("pass", "blocked", "pass_with_warnings"):
        v1["verdict"] = {"value": verdict, "reasons": []}
        append_log_entry(tmp_path, build_log_entry(v1=v1, bundle_path=tmp_path / "main.json"))
    entries = read_log_entries(tmp_path)
    assert len(entries) == 3
    # First in returned list = most recently appended.
    assert entries[0]["verdict"] == "pass_with_warnings"
    assert entries[-1]["verdict"] == "pass"


def test_read_log_entries_limit(tmp_path):
    v1 = _sample_v1()
    for _ in range(5):
        append_log_entry(tmp_path, build_log_entry(v1=v1, bundle_path=tmp_path / "main.json"))
    entries = read_log_entries(tmp_path, limit=2)
    assert len(entries) == 2


def test_read_log_entries_handles_missing_file(tmp_path):
    assert read_log_entries(tmp_path) == []


def test_read_log_entries_skips_malformed_lines(tmp_path):
    log = log_path_for(tmp_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"valid": "json"}\nnot json\n{"also": "valid"}\n')
    entries = read_log_entries(tmp_path)
    # 2 valid lines (malformed line skipped silently).
    assert len(entries) == 2


def test_log_path_for_returns_canonical_location(tmp_path):
    path = log_path_for(tmp_path)
    assert path == tmp_path / ".roam" / LOG_FILENAME


def test_append_log_entry_rejects_oversize_line(tmp_path):
    """Lines beyond PIPE_BUF (~4096B) are rejected, not silently corrupted."""
    huge = {"junk": "x" * 5000}
    ok = append_log_entry(tmp_path, huge)
    assert ok is False
    # File MUST NOT have been created with a partial write.
    assert not log_path_for(tmp_path).is_file() or log_path_for(tmp_path).read_text() == ""


def test_append_log_entry_parallel_no_interleave(tmp_path):
    """Concurrent appends from multiple threads do not interleave or lose lines."""
    import threading

    v1 = _sample_v1()
    entry = build_log_entry(v1=v1, bundle_path=tmp_path / "main.json")
    n_threads = 16
    per_thread = 8

    outcomes = []

    def writer():
        for _ in range(per_thread):
            outcomes.append(append_log_entry(tmp_path, entry))

    threads = [threading.Thread(target=writer) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes == [True] * (n_threads * per_thread)
    lines = log_path_for(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_threads * per_thread
    # Every line must be a complete, parseable JSON record (no torn writes).
    for line in lines:
        parsed = json.loads(line)
        assert parsed["verdict"] == "pass"


def test_append_log_entry_rejects_symlinked_control_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / ".roam").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable on this platform")

    assert append_log_entry(tmp_path, {"verdict": "pass"}) is False
    assert not (outside / LOG_FILENAME).exists()


def test_append_log_entry_parallel_processes_no_loss(tmp_path):
    """The OS lock serializes independent guard-pr processes, not only threads."""
    workers = 4
    per_worker = 16
    source_root = repo_root() / "src"
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in (str(source_root), existing_pythonpath) if part)
    script = (
        "from pathlib import Path\n"
        "import sys\n"
        "from roam.guard_log import append_log_entry\n"
        "root = Path(sys.argv[1])\n"
        "count = int(sys.argv[2])\n"
        "assert all(append_log_entry(root, {'verdict': 'pass'}) for _ in range(count))\n"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), str(per_worker)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(workers)
    ]
    failures = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        if process.returncode != 0:
            failures.append((process.returncode, stdout, stderr))
    assert not failures

    lines = log_path_for(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == workers * per_worker
    assert all(json.loads(line)["verdict"] == "pass" for line in lines)


def test_append_log_entry_rejects_hard_linked_log(tmp_path):
    control_dir = tmp_path / ".roam"
    control_dir.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("sentinel\n", encoding="utf-8")
    try:
        os.link(outside, control_dir / LOG_FILENAME)
    except (OSError, NotImplementedError):
        pytest.skip("hard links are unavailable on this platform")

    assert append_log_entry(tmp_path, {"verdict": "pass"}) is False
    assert outside.read_text(encoding="utf-8") == "sentinel\n"
