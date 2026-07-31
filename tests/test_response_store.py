"""Regression tests for the indexed ``.roam/responses`` store."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from roam.response_store import newest_response, response_candidates, store_response_text


def _seed(root: Path, name: str, mtime: float, payload: dict | None = None) -> Path:
    responses = root / ".roam" / "responses"
    responses.mkdir(parents=True, exist_ok=True)
    target = responses / name
    target.write_text(json.dumps(payload or {"name": name}), encoding="utf-8")
    os.utime(target, (mtime, mtime))
    return target


def test_latest_lookup_bootstraps_once_then_never_scans(tmp_path, monkeypatch):
    """The steady-state one-envelope read must not enumerate the corpus."""
    import roam.response_store as store

    now = time.time()
    _seed(tmp_path, "old.json", now - 10)
    latest = _seed(tmp_path, "latest.json", now)

    assert newest_response(tmp_path) == latest  # one-time legacy bootstrap

    def forbidden_scan(_responses_dir):
        raise AssertionError("steady-state lookup scanned the response directory")

    monkeypatch.setattr(store, "_scan_response_entries", forbidden_scan)
    assert newest_response(tmp_path) == latest


def test_latest_lookup_returns_none_when_newest_is_older_than_cutoff(tmp_path):
    stale = time.time() - 120
    _seed(tmp_path, "stale.json", stale)

    assert newest_response(tmp_path, max_age_seconds=60) is None
    assert newest_response(tmp_path, max_age_seconds=0) is not None


def test_external_file_creation_invalidates_and_rebuilds_index(tmp_path):
    now = time.time()
    old = _seed(tmp_path, "old.json", now - 10)
    assert newest_response(tmp_path) == old

    external = _seed(tmp_path, "external.json", now + 10)
    assert newest_response(tmp_path) == external


def test_narrow_window_query_uses_index_without_full_scan(tmp_path, monkeypatch):
    import roam.response_store as store

    now = time.time()
    _seed(tmp_path, "outside.json", now - 3600)
    inside = _seed(tmp_path, "inside.json", now)
    assert response_candidates(tmp_path, since_epoch=now - 30) == [inside]

    def forbidden_scan(_responses_dir):
        raise AssertionError("indexed narrow-window query scanned the directory")

    monkeypatch.setattr(store, "_scan_response_entries", forbidden_scan)
    assert response_candidates(tmp_path, since_epoch=now - 30) == [inside]


def test_next_command_reader_is_wired_to_indexed_lookup(tmp_path, monkeypatch):
    import roam.response_store as store
    from roam.commands.cmd_next import _read_recent_envelope_next_command

    envelope = {"agent_contract": {"next_commands": ["roam impact target"]}}
    store_response_text(tmp_path, "latest.json", json.dumps(envelope))
    assert _read_recent_envelope_next_command(tmp_path) == ("roam impact target", "latest.json")

    def forbidden_scan(_responses_dir):
        raise AssertionError("next command reader bypassed the response index")

    monkeypatch.setattr(store, "_scan_response_entries", forbidden_scan)
    assert _read_recent_envelope_next_command(tmp_path) == ("roam impact target", "latest.json")


def test_pr_bundle_reader_is_wired_to_indexed_window(tmp_path, monkeypatch):
    import roam.response_store as store
    from roam.commands.cmd_pr_bundle import _candidate_responses

    now = time.time()
    inside = _seed(tmp_path, "inside.json", now)
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 30))
    assert _candidate_responses(tmp_path, since) == [inside]

    def forbidden_scan(_responses_dir):
        raise AssertionError("PR bundle reader bypassed the response index")

    monkeypatch.setattr(store, "_scan_response_entries", forbidden_scan)
    assert _candidate_responses(tmp_path, since) == [inside]


def test_concurrent_writers_publish_complete_indexed_envelopes(tmp_path):
    """Concurrent payload+index commits must lose no files or expose partial JSON."""
    payloads = {
        f"command_{i:02d}.json": json.dumps({"command": f"command-{i}", "items": list(range(50))}) for i in range(24)
    }

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(store_response_text, tmp_path, name, text) for name, text in payloads.items()]
        for future in futures:
            future.result()

    candidates = response_candidates(tmp_path)
    assert {path.name for path in candidates} == set(payloads)
    for path in candidates:
        assert json.loads(path.read_text(encoding="utf-8"))["command"].startswith("command-")


def test_content_addressed_reuse_does_not_refresh_newest_order(tmp_path):
    first = store_response_text(tmp_path, "first.json", "{}", overwrite=False)
    time.sleep(0.01)
    second = store_response_text(tmp_path, "second.json", "{}", overwrite=False)
    assert newest_response(tmp_path) == second

    store_response_text(tmp_path, "first.json", "{}", overwrite=False)
    assert newest_response(tmp_path) == second
    assert first.read_text(encoding="utf-8") == "{}"


def test_response_cap_is_byte_bounded_disclosed_and_schema_preserving():
    from roam.output.formatter import _cap_response_store_envelope

    envelope = {
        "schema": "roam-envelope-v1",
        "schema_version": "1.1.0",
        "command": "dead",
        "version": "13.10.0",
        "project": "fixture",
        "summary": {"verdict": "many findings", "partial_success": False},
        "bridges": [{"name": f"bridge-{i}-" + ("b" * 30)} for i in range(40)],
        "links": [{"source": f"source-{i}", "target": f"target-{i}"} for i in range(20)],
        "agent_contract": {"facts": ["100 findings"], "risks": [], "next_commands": []},
        "_meta": {"timestamp": "2026-07-31T00:00:00Z"},
        "findings": [{"name": f"finding-{i}", "body": "x" * 400} for i in range(200)],
    }
    required = {
        "schema",
        "schema_version",
        "command",
        "version",
        "project",
        "summary",
        "bridges",
        "links",
        "agent_contract",
        "_meta",
    }
    full_bytes = len(json.dumps(envelope, indent=2).encode("utf-8"))
    assert full_bytes > 8_192, "fixture must cross the cap or the test proves nothing"

    capped, text = _cap_response_store_envelope(envelope, 8_192)

    assert len(text.encode("utf-8")) <= 8_192
    assert required <= capped.keys()
    assert capped["bridges"] == envelope["bridges"]
    assert capped["links"] == envelope["links"]
    assert capped["summary"]["truncated"] is True
    assert capped["summary"]["truncation_reason"] == "budget"
    assert capped["summary"]["partial_success"] is True
    disclosure = capped["summary"]["response_store"]
    assert disclosure == {
        "payload_truncated": True,
        "max_bytes": 8_192,
        "original_bytes": full_bytes,
        "stored_bytes": len(text.encode("utf-8")),
    }
    assert "truncated" not in envelope["summary"], "storage cap mutated the command response"
