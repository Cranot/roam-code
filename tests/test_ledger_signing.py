"""Tests for the R20 phase 4 run-ledger HMAC signing chain.

Covers:
  1. log_event includes a ``signature`` field on every new event
  2. The first event in a run uses the seed signature (64 zeroes)
  3. Sequential events form a coherent HMAC chain
  4. Tampering with an event's data breaks verification at that seq
  5. Truncating the JSONL is detected (each remaining event still
     verifies; the loss is observable via event_count drift)
  6. Legacy unsigned events pass verification with state=unsigned
  7. The key file is created on first start_run
  8. The key file is mode 0o600 on POSIX
  9. ``runs verify --all`` summarises multiple runs
 10. End-to-end CLI smoke: tampered chain produces exit-code 5
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    assert_json_envelope,
    git_init,
    invoke_cli,
    parse_json_output,
)

from roam.runs.ledger import (  # noqa: E402
    end_run,
    log_event,
    read_run_events,
    read_run_meta,
    run_dir,
    start_run,
)
from roam.runs.signing import (  # noqa: E402
    SEED_SIGNATURE,
    compute_event_signature,
    ensure_ledger_key,
    key_file_mode,
    ledger_key_path,
    verify_chain,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def signed_project(tmp_path):
    """Minimal git-initialised project with no runs yet."""
    proj = tmp_path / "signedproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# 1. signature field is written on every event
# ---------------------------------------------------------------------------


def test_log_event_includes_signature_field(signed_project):
    meta = start_run(signed_project, agent="claude-code")
    log_event(signed_project, meta.run_id, action="preflight", target="useFoo")

    events_path = run_dir(signed_project, meta.run_id) / "events.jsonl"
    lines = [ln for ln in events_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert "signature" in parsed, "every new event must carry a 'signature' field"
    # 64-char lowercase hex digest from SHA-256.
    assert len(parsed["signature"]) == 64
    assert all(c in "0123456789abcdef" for c in parsed["signature"])


# ---------------------------------------------------------------------------
# 2. seed signature math is what the spec says
# ---------------------------------------------------------------------------


def test_first_event_uses_seed_signature(signed_project):
    meta = start_run(signed_project, agent="claude-code")
    log_event(signed_project, meta.run_id, action="preflight", target="useFoo")

    events_path = run_dir(signed_project, meta.run_id) / "events.jsonl"
    parsed = json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])

    # Recompute the expected signature using the seed prev_sig = "0" * 64.
    key = ensure_ledger_key(signed_project)
    expected = compute_event_signature(SEED_SIGNATURE, parsed, key)
    assert parsed["signature"] == expected, (
        f"first event signature should be HMAC over seed; "
        f"got {parsed['signature']}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# 3. chain holds across multiple events
# ---------------------------------------------------------------------------


def test_sequential_events_chain_correctly(signed_project):
    meta = start_run(signed_project, agent="claude-code")
    for i, action in enumerate(("preflight", "diff", "edit", "test", "commit")):
        log_event(signed_project, meta.run_id, action=action, target=f"t{i}")

    events = list(read_run_events(signed_project, meta.run_id))
    assert len(events) == 5

    key = ensure_ledger_key(signed_project)
    result = verify_chain(events, key)
    assert result["state"] == "ok", f"chain should verify, got {result}"
    assert result["events_verified"] == 5
    assert result["first_tamper_at_seq"] is None
    assert result["partial_success"] is False
    assert result["final_signature"] == events[-1]["signature"]


# ---------------------------------------------------------------------------
# 4. tampering breaks verification at the mutated seq
# ---------------------------------------------------------------------------


def test_tampered_event_breaks_verification(signed_project):
    meta = start_run(signed_project, agent="claude-code")
    log_event(signed_project, meta.run_id, action="preflight", target="useFoo")
    log_event(signed_project, meta.run_id, action="diff", target="useFoo")
    log_event(signed_project, meta.run_id, action="commit", target="useFoo")

    events_path = run_dir(signed_project, meta.run_id) / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    # Mutate event seq=2: change ``target`` from "useFoo" to "evilTarget".
    # Keep the original signature so the recomputed one will mismatch.
    parsed = json.loads(lines[1])
    parsed["target"] = "evilTarget"
    lines[1] = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    events = list(read_run_events(signed_project, meta.run_id))
    key = ensure_ledger_key(signed_project)
    result = verify_chain(events, key)
    assert result["state"] == "tampered"
    assert result["first_tamper_at_seq"] == 2
    assert result["events_verified"] == 1  # event 1 verified before break
    assert "seq=2" in result["details"]


# ---------------------------------------------------------------------------
# 5. truncating the JSONL — remaining events still verify, but the chain
#    no longer matches the post-end_run meta.final_signature
# ---------------------------------------------------------------------------


def test_truncated_jsonl_detected(signed_project):
    meta = start_run(signed_project, agent="claude-code")
    log_event(signed_project, meta.run_id, action="preflight", target="useFoo")
    log_event(signed_project, meta.run_id, action="diff", target="useFoo")
    log_event(signed_project, meta.run_id, action="commit", target="useFoo")
    end_run(signed_project, meta.run_id, status="completed")

    # meta.json should now record the final signature + event count.
    closed = read_run_meta(signed_project, meta.run_id)
    assert closed is not None
    assert closed.event_count == 3
    assert closed.final_signature is not None
    pre_truncation_final = closed.final_signature

    # Chop off the last event.
    events_path = run_dir(signed_project, meta.run_id) / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    events_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    # The remaining events still pass a chain check on their own — that's
    # the design (HMAC verifies what's there, not what's missing). But the
    # post-truncation final signature differs from meta.json's stamped one
    # and the event count has dropped — both observable by a caller.
    events = list(read_run_events(signed_project, meta.run_id))
    assert len(events) == 2
    key = ensure_ledger_key(signed_project)
    result = verify_chain(events, key)
    assert result["state"] == "ok", "remaining events still chain"
    assert result["final_signature"] != pre_truncation_final, (
        "truncation must show up as a final-signature mismatch vs meta.json"
    )
    assert result["events_verified"] == 2


# ---------------------------------------------------------------------------
# 6. legacy unsigned events pass advisory (state=unsigned)
# ---------------------------------------------------------------------------


def test_legacy_unsigned_events_pass_advisory(signed_project):
    """Simulate a pre-signing ledger by writing events without signatures
    directly to events.jsonl. The verifier should treat the run as
    ``unsigned`` (not ``tampered``)."""
    meta = start_run(signed_project, agent="claude-code")
    events_path = run_dir(signed_project, meta.run_id) / "events.jsonl"

    # Bypass log_event to skip the signing step entirely.
    legacy_events = [
        {"ts": "2026-01-01T00:00:00Z", "seq": 1, "action": "preflight"},
        {"ts": "2026-01-01T00:00:01Z", "seq": 2, "action": "diff"},
        {"ts": "2026-01-01T00:00:02Z", "seq": 3, "action": "commit"},
    ]
    with events_path.open("w", encoding="utf-8") as fh:
        for ev in legacy_events:
            fh.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")

    events = list(read_run_events(signed_project, meta.run_id))
    key = ensure_ledger_key(signed_project)
    result = verify_chain(events, key)
    assert result["state"] == "unsigned"
    assert result["events_verified"] == 3
    assert result["partial_success"] is True
    assert result["first_tamper_at_seq"] is None


# ---------------------------------------------------------------------------
# 7. key file is created on first run
# ---------------------------------------------------------------------------


def test_key_file_is_created_on_first_run(signed_project):
    key_path = ledger_key_path(signed_project)
    assert not key_path.exists()

    start_run(signed_project, agent="claude-code")

    assert key_path.exists()
    assert key_path.stat().st_size == 32, "HMAC key should be 32 random bytes"


# ---------------------------------------------------------------------------
# 8. key file has safe permissions on POSIX
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode bits don't apply on Windows")
def test_key_file_has_safe_permissions(signed_project):
    start_run(signed_project, agent="claude-code")
    mode = key_file_mode(signed_project)
    assert mode is not None
    # 0o600 = owner read+write only.
    assert mode == (stat.S_IRUSR | stat.S_IWUSR), (
        f"expected 0o600 on POSIX, got {oct(mode) if mode is not None else mode}"
    )


# ---------------------------------------------------------------------------
# 9. runs verify --all summarises multiple runs
# ---------------------------------------------------------------------------


def test_verify_all_summarizes_multiple_runs(cli_runner, signed_project, monkeypatch):
    monkeypatch.chdir(signed_project)

    # Open three runs, log a few events each.
    meta1 = start_run(signed_project, agent="claude-code")
    log_event(signed_project, meta1.run_id, action="preflight", target="a")
    end_run(signed_project, meta1.run_id, status="completed")

    meta2 = start_run(signed_project, agent="cursor")
    log_event(signed_project, meta2.run_id, action="diff", target="b")
    log_event(signed_project, meta2.run_id, action="commit", target="b")

    meta3 = start_run(signed_project, agent="claude-code")
    log_event(signed_project, meta3.run_id, action="edit", target="c")

    # All three are valid chains.
    result = invoke_cli(
        cli_runner, ["runs", "verify", "--all"], cwd=signed_project, json_mode=True
    )
    data = parse_json_output(result, "runs-verify")
    assert_json_envelope(data, "runs-verify")
    summary = data["summary"]
    assert summary["state"] == "ok"
    assert summary["runs_verified"] == 3
    assert summary["runs_ok"] == 3
    assert summary["runs_tampered"] == 0
    assert summary["events_verified"] == 1 + 2 + 1
    assert len(data["runs"]) == 3


# ---------------------------------------------------------------------------
# 10. CLI smoke: tampered chain → state=tampered + exit 5
# ---------------------------------------------------------------------------


def test_cli_verify_detects_tamper_exit_code(cli_runner, signed_project, monkeypatch):
    monkeypatch.chdir(signed_project)

    meta = start_run(signed_project, agent="claude-code")
    log_event(signed_project, meta.run_id, action="preflight", target="useFoo")
    log_event(signed_project, meta.run_id, action="diff", target="useFoo")
    log_event(signed_project, meta.run_id, action="commit", target="useFoo")

    # Tamper with event 2 in-place.
    events_path = run_dir(signed_project, meta.run_id) / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    parsed = json.loads(lines[1])
    parsed["target"] = "evilTarget"
    lines[1] = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = invoke_cli(
        cli_runner,
        ["runs", "verify", meta.run_id],
        cwd=signed_project,
        json_mode=True,
    )
    assert result.exit_code == 5, f"tamper should exit 5, got {result.exit_code}\n{result.output}"
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert_json_envelope(data, "runs-verify")
    assert data["summary"]["state"] == "tampered"
    assert data["summary"]["first_tamper_at_seq"] == 2
    assert "TAMPER DETECTED" in data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# 11. CLI verify on clean chain → state=ok + exit 0
# ---------------------------------------------------------------------------


def test_cli_verify_ok_chain(cli_runner, signed_project, monkeypatch):
    monkeypatch.chdir(signed_project)
    meta = start_run(signed_project, agent="claude-code")
    log_event(signed_project, meta.run_id, action="preflight", target="useFoo")
    log_event(signed_project, meta.run_id, action="commit", target="useFoo")

    result = invoke_cli(
        cli_runner,
        ["runs", "verify", meta.run_id],
        cwd=signed_project,
        json_mode=True,
    )
    data = parse_json_output(result, "runs-verify")
    assert_json_envelope(data, "runs-verify")
    summary = data["summary"]
    assert summary["state"] == "ok"
    assert summary["events_verified"] == 2
    assert summary["first_tamper_at_seq"] is None
    assert summary["partial_success"] is False
    assert "all signatures match" in summary["verdict"]


# ---------------------------------------------------------------------------
# 12. end_run stamps final_signature + event_count into meta.json
# ---------------------------------------------------------------------------


def test_end_run_stamps_final_signature(signed_project):
    meta = start_run(signed_project, agent="claude-code")
    log_event(signed_project, meta.run_id, action="preflight", target="x")
    log_event(signed_project, meta.run_id, action="commit", target="x")
    closed = end_run(signed_project, meta.run_id, status="completed")

    assert closed.event_count == 2
    assert closed.final_signature is not None
    assert len(closed.final_signature) == 64

    # And it's persisted to disk.
    raw = json.loads(
        (run_dir(signed_project, meta.run_id) / "meta.json").read_text(encoding="utf-8")
    )
    assert raw["final_signature"] == closed.final_signature
    assert raw["event_count"] == 2
