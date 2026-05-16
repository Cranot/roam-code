"""Tests for the R20 per-agent-run event ledger substrate.

Covers:
  - start_run creates .roam/runs/<run_id>/{meta.json, events.jsonl}
  - log_event appends one line with monotonic seq
  - end_run stamps ended_at + status into meta.json
  - list_runs streams meta records (newest first), empty -> no_runs state
  - read_run_events returns events in seq order
  - run_id format matches the contract
  - log without --run-id targets the latest in-progress run
"""

from __future__ import annotations

import json
import re
import sys
import time
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
    RUN_ID_RE,
    end_run,
    list_runs,
    log_event,
    read_run_events,
    read_run_meta,
    run_dir,
    start_run,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runs_project(tmp_path):
    """A minimal git-initialised project with no runs yet."""
    proj = tmp_path / "runproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# 1. start_run creates the run directory
# ---------------------------------------------------------------------------


def test_start_run_creates_directory(runs_project):
    meta = start_run(runs_project, agent="claude-code")
    rdir = run_dir(runs_project, meta.run_id)
    assert rdir.exists() and rdir.is_dir()
    assert (rdir / "meta.json").exists(), "meta.json was not created"
    assert (rdir / "events.jsonl").exists(), "events.jsonl was not created"

    # meta.json has the expected fields
    raw = json.loads((rdir / "meta.json").read_text(encoding="utf-8"))
    assert raw["run_id"] == meta.run_id
    assert raw["agent"] == "claude-code"
    assert raw["status"] == "in_progress"
    assert raw["started_at"]
    assert raw["ended_at"] is None


# ---------------------------------------------------------------------------
# 2. log_event appends one line with monotonic seq
# ---------------------------------------------------------------------------


def test_log_event_appends_line(runs_project):
    meta = start_run(runs_project, agent="claude-code")
    s1 = log_event(runs_project, meta.run_id, action="preflight", target="useFoo")
    s2 = log_event(runs_project, meta.run_id, action="diff", target="")
    s3 = log_event(runs_project, meta.run_id, action="commit", target="abc123")
    assert (s1, s2, s3) == (1, 2, 3), f"seqs should be 1,2,3 got {s1},{s2},{s3}"

    path = run_dir(runs_project, meta.run_id) / "events.jsonl"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3
    parsed = [json.loads(ln) for ln in lines]
    assert [p["seq"] for p in parsed] == [1, 2, 3]
    assert parsed[0]["action"] == "preflight"
    assert parsed[0]["target"] == "useFoo"
    # ts present and ISO-8601 Z-suffix
    for p in parsed:
        assert p["ts"].endswith("Z"), f"expected ISO-8601 Z timestamp, got {p['ts']}"


# ---------------------------------------------------------------------------
# 3. end_run updates meta.json
# ---------------------------------------------------------------------------


def test_end_run_updates_meta(runs_project):
    meta = start_run(runs_project, agent="claude-code")
    assert meta.status == "in_progress"
    assert meta.ended_at is None

    # Sleep a hair to ensure ended_at > started_at on coarse-grained clocks.
    time.sleep(0.01)
    ended = end_run(runs_project, meta.run_id, status="completed")
    assert ended.status == "completed"
    assert ended.ended_at is not None
    assert ended.ended_at >= ended.started_at

    # Re-read from disk to confirm persistence.
    fresh = read_run_meta(runs_project, meta.run_id)
    assert fresh is not None
    assert fresh.status == "completed"
    assert fresh.ended_at == ended.ended_at


# ---------------------------------------------------------------------------
# 4. list_runs on empty repo returns no_runs state (CLI envelope)
# ---------------------------------------------------------------------------


def test_list_runs_empty_returns_no_runs(cli_runner, runs_project, monkeypatch):
    monkeypatch.chdir(runs_project)
    result = invoke_cli(cli_runner, ["runs", "list"], cwd=runs_project, json_mode=True)
    data = parse_json_output(result, "runs-list")
    assert_json_envelope(data, "runs-list")
    assert data["summary"]["state"] == "no_runs"
    assert data["summary"]["partial_success"] is False
    assert data["summary"]["total"] == 0
    assert data["runs"] == []

    # And direct API: list_runs yields nothing on a fresh repo
    assert list(list_runs(runs_project)) == []


# ---------------------------------------------------------------------------
# 5. list_runs streams meta records
# ---------------------------------------------------------------------------


def test_list_runs_streams_meta(runs_project):
    metas = []
    for agent in ("claude-code", "cursor", "claude-code"):
        m = start_run(runs_project, agent=agent)
        metas.append(m)
        # Force distinct started_at across runs so the deterministic run_id
        # hash + listing order is stable.
        time.sleep(0.01)

    listed = list(list_runs(runs_project))
    assert len(listed) == 3, f"expected 3 runs, got {len(listed)}"
    listed_ids = {m.run_id for m in listed}
    assert listed_ids == {m.run_id for m in metas}

    # Filter by agent
    cc = list(list_runs(runs_project, agent="claude-code"))
    assert len(cc) == 2
    assert all(m.agent == "claude-code" for m in cc)


# ---------------------------------------------------------------------------
# 6. show reads all events in seq order
# ---------------------------------------------------------------------------


def test_show_run_reads_all_events(cli_runner, runs_project, monkeypatch):
    monkeypatch.chdir(runs_project)

    meta = start_run(runs_project, agent="claude-code")
    for i, action in enumerate(("preflight", "diff", "edit", "test", "commit")):
        log_event(
            runs_project,
            meta.run_id,
            action=action,
            target=f"target_{i}",
            summary_verdict=f"verdict {i}",
        )
    end_run(runs_project, meta.run_id, status="completed")

    # Direct API
    events = list(read_run_events(runs_project, meta.run_id))
    assert len(events) == 5
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]
    assert [e["action"] for e in events] == ["preflight", "diff", "edit", "test", "commit"]

    # CLI show
    result = invoke_cli(cli_runner, ["runs", "show", meta.run_id], cwd=runs_project, json_mode=True)
    data = parse_json_output(result, "runs-show")
    assert_json_envelope(data, "runs-show")
    assert data["summary"]["total"] == 5
    assert data["summary"]["state"] == "completed"
    assert len(data["events"]) == 5
    assert [e["seq"] for e in data["events"]] == [1, 2, 3, 4, 5]
    assert data["run"]["status"] == "completed"
    assert data["run"]["ended_at"]


# ---------------------------------------------------------------------------
# 7. run_id format matches the contract
# ---------------------------------------------------------------------------


def test_run_id_format(runs_project):
    meta = start_run(runs_project, agent="claude-code")
    assert RUN_ID_RE.match(meta.run_id), f"run_id {meta.run_id!r} doesn't match contract"
    # Hand-rolled regex for the parts the contract names explicitly.
    assert re.match(r"^run_\d{8}_[0-9a-f]+$", meta.run_id)
    parts = meta.run_id.split("_")
    assert len(parts) == 3, f"expected run_<date>_<hash>, got {meta.run_id}"
    assert parts[0] == "run"
    assert len(parts[1]) == 8 and parts[1].isdigit()
    assert len(parts[2]) >= 6 and all(c in "0123456789abcdef" for c in parts[2])


# ---------------------------------------------------------------------------
# 8. log without --run-id uses latest in-progress run
# ---------------------------------------------------------------------------


def test_log_without_run_id_uses_latest_in_progress(cli_runner, runs_project, monkeypatch):
    monkeypatch.chdir(runs_project)

    # Open two runs; the second one is the most-recent in-progress one.
    start_run(runs_project, agent="claude-code")
    time.sleep(0.01)
    active = start_run(runs_project, agent="cursor")

    # Now log without --run-id via the CLI.
    result = invoke_cli(
        cli_runner,
        ["runs", "log", "--action", "preflight", "--target", "useFoo", "--verdict", "5 callers"],
        cwd=runs_project,
        json_mode=True,
    )
    data = parse_json_output(result, "runs-log")
    assert_json_envelope(data, "runs-log")
    assert data["summary"]["logged"] is True
    assert data["summary"]["run_id"] == active.run_id
    assert data["summary"]["seq"] == 1

    # Confirm the event landed in the active run, not the older one.
    events = list(read_run_events(runs_project, active.run_id))
    assert len(events) == 1
    assert events[0]["action"] == "preflight"
    assert events[0]["target"] == "useFoo"
    assert events[0]["summary_verdict"] == "5 callers"


# ---------------------------------------------------------------------------
# 9. CLI envelope shape across all subcommands (extra safety net)
# ---------------------------------------------------------------------------


def test_runs_json_envelope_shape(cli_runner, runs_project, monkeypatch):
    monkeypatch.chdir(runs_project)

    # start
    r = invoke_cli(cli_runner, ["runs", "start", "--agent", "claude-code"], cwd=runs_project, json_mode=True)
    data = parse_json_output(r, "runs-start")
    assert_json_envelope(data, "runs-start")
    assert data["schema"] == "roam-envelope-v1"
    assert data["summary"]["started"] is True
    run_id = data["summary"]["run_id"]

    # log
    r = invoke_cli(
        cli_runner,
        ["runs", "log", "--action", "diff", "--target", "x"],
        cwd=runs_project,
        json_mode=True,
    )
    data = parse_json_output(r, "runs-log")
    assert_json_envelope(data, "runs-log")
    assert data["summary"]["logged"] is True

    # list
    r = invoke_cli(cli_runner, ["runs", "list"], cwd=runs_project, json_mode=True)
    data = parse_json_output(r, "runs-list")
    assert_json_envelope(data, "runs-list")
    assert data["summary"]["total"] >= 1

    # end
    r = invoke_cli(cli_runner, ["runs", "end", "--run-id", run_id], cwd=runs_project, json_mode=True)
    data = parse_json_output(r, "runs-end")
    assert_json_envelope(data, "runs-end")
    assert data["summary"]["ended"] is True
    assert data["summary"]["state"] == "completed"

    # show
    r = invoke_cli(cli_runner, ["runs", "show", run_id], cwd=runs_project, json_mode=True)
    data = parse_json_output(r, "runs-show")
    assert_json_envelope(data, "runs-show")
    assert data["summary"]["total"] >= 1


# ---------------------------------------------------------------------------
# 10. log against unknown run_id surfaces clean error envelope
# ---------------------------------------------------------------------------


def test_log_unknown_run_id_returns_clean_error(cli_runner, runs_project, monkeypatch):
    monkeypatch.chdir(runs_project)
    result = invoke_cli(
        cli_runner,
        ["runs", "log", "--run-id", "run_19990101_deadbe", "--action", "noop"],
        cwd=runs_project,
        json_mode=True,
    )
    # exit_code 2 (we deliberately exit on error), so use a direct parse.
    assert result.exit_code == 2, result.output
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert_json_envelope(data, "runs-log")
    assert data["summary"]["logged"] is False
    assert data["summary"]["state"] == "unknown_run"
    assert data["summary"]["partial_success"] is True
