"""Tests for ``roam replay <run_id>`` (R20 phase 3).

The replay command consumes ``.roam/runs/<run_id>/`` -- it never indexes
or otherwise touches the SQLite DB. So we hand-craft fake ledger
directories (meta.json + events.jsonl) and assert on the rendered
output.

Coverage:
  1. missing run_id -> clean envelope (state=missing_run), never crash
  2. text mode emits a VERDICT line + event count in narrative
  3. JSON envelope has events / summary / stats / agent_contract
  4. --execute without --dry-run / --no-dry-run is refused
  5. --execute --dry-run shows reconstructed commands without running them
  6. in-progress run -> state=incomplete_run
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, invoke_cli, parse_json_output  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def replay_project(tmp_path):
    """Bare git-initialised project; tests seed .roam/runs/ by hand.

    We deliberately do NOT call ``roam runs start`` here -- the test
    surface is the replay command, and the substrate already has
    coverage for ``start_run``. Hand-crafting the directories makes the
    test setup easy to read and decouples replay from any ledger
    bug-of-the-day.
    """
    proj = tmp_path / "replayproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


def _seed_run(
    project: Path,
    run_id: str,
    agent: str = "claude-code",
    events: list[dict] | None = None,
    status: str = "completed",
    started_at: str = "2026-05-13T08:14:33Z",
    ended_at: str | None = "2026-05-13T08:15:03Z",
) -> Path:
    """Hand-write ``.roam/runs/<run_id>/`` with meta.json + events.jsonl."""
    rdir = project / ".roam" / "runs" / run_id
    rdir.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": run_id,
        "agent": agent,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": status,
    }
    (rdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    events_text = ""
    for i, ev in enumerate(events or [], start=1):
        merged = dict(ev)
        merged.setdefault("seq", i)
        merged.setdefault("ts", "2026-05-13T08:14:34Z")
        events_text += json.dumps(merged, sort_keys=True) + "\n"
    (rdir / "events.jsonl").write_text(events_text, encoding="utf-8")
    return rdir


# ---------------------------------------------------------------------------
# 1. Missing run id -> clean envelope
# ---------------------------------------------------------------------------


def test_replay_missing_run_id_returns_clean_envelope(replay_project, cli_runner):
    """A nonexistent run_id should return a structured envelope, not a crash.

    The envelope must say ``state=missing_run`` and ``partial_success=True``
    so consumers can branch on it.
    """
    result = invoke_cli(
        cli_runner,
        ["replay", "run_20260513_doesnotexist"],
        cwd=replay_project,
        json_mode=True,
    )
    # We exit 2 on missing run, but the envelope must still be valid JSON.
    assert result.exit_code == 2, f"expected exit 2, got {result.exit_code}\n{result.output}"
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert data["command"] == "replay"
    assert data["summary"]["state"] == "missing_run"
    assert data["summary"]["partial_success"] is True
    assert data["events_count"] == 0
    assert data["events"] == []


# ---------------------------------------------------------------------------
# 2. Text narrative
# ---------------------------------------------------------------------------


def test_replay_emits_narrative_text(replay_project, cli_runner):
    """Text mode should print a VERDICT line and one row per event."""
    run_id = "run_20260513_abc111"
    _seed_run(
        replay_project,
        run_id,
        events=[
            {"action": "preflight", "target": "useThemeClasses", "summary_verdict": "5 callers, complexity 3"},
            {"action": "diff", "target": "", "summary_verdict": "3 files changed"},
            {"action": "critique", "target": "", "summary_verdict": "SAFE: 0 high-severity"},
        ],
    )
    result = invoke_cli(cli_runner, ["replay", run_id], cwd=replay_project)
    assert result.exit_code == 0, f"failed: {result.output}"
    out = result.output
    assert "VERDICT:" in out
    assert run_id in out
    # All three actions surface in the timeline.
    assert "preflight" in out
    assert "diff" in out
    assert "critique" in out
    # SAFE verdict propagated to the overall verdict.
    assert "SAFE" in out


# ---------------------------------------------------------------------------
# 3. JSON envelope shape
# ---------------------------------------------------------------------------


def test_replay_json_envelope_shape(replay_project, cli_runner):
    """JSON envelope must carry events[], summary, stats, agent_contract."""
    run_id = "run_20260513_abc222"
    _seed_run(
        replay_project,
        run_id,
        events=[
            {"action": "preflight", "target": "foo", "summary_verdict": "ok"},
            {"action": "impact", "target": "foo", "summary_verdict": "100 callers", "partial_success": True},
        ],
    )
    result = invoke_cli(cli_runner, ["replay", run_id], cwd=replay_project, json_mode=True)
    data = parse_json_output(result, command="replay")
    # Required keys.
    assert data["command"] == "replay"
    assert data["run_id"] == run_id
    assert data["events_count"] == 2
    assert isinstance(data["events"], list) and len(data["events"]) == 2
    assert "summary" in data and "verdict" in data["summary"]
    assert data["summary"]["state"] == "ok"
    # Stats sub-dict.
    stats = data["stats"]
    assert "unique_actions" in stats
    assert sorted(stats["unique_actions"]) == ["impact", "preflight"]
    assert stats["partial_success_count"] == 1
    # Agent contract has positive, imperative facts/next_commands.
    contract = data["agent_contract"]
    assert contract["facts"]
    assert contract["next_commands"]
    assert any(nc.startswith("roam runs show") for nc in contract["next_commands"])


# ---------------------------------------------------------------------------
# 4. --execute requires --dry-run first
# ---------------------------------------------------------------------------


def test_replay_execute_requires_dry_run_first(replay_project, cli_runner):
    """Bare ``--execute`` must be refused -- the safety gate."""
    run_id = "run_20260513_abc333"
    _seed_run(
        replay_project,
        run_id,
        events=[{"action": "preflight", "target": "foo", "summary_verdict": "ok"}],
    )
    # Text mode.
    result = invoke_cli(cli_runner, ["replay", run_id, "--execute"], cwd=replay_project)
    assert result.exit_code == 2, "expected exit 2 -- refused without --dry-run"
    assert "refusing to --execute" in result.output
    # JSON mode -- same gate, structured envelope.
    result = invoke_cli(
        cli_runner,
        ["replay", run_id, "--execute"],
        cwd=replay_project,
        json_mode=True,
    )
    assert result.exit_code == 2
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert data["summary"]["state"] == "execute_requires_dry_run"
    assert data["summary"]["partial_success"] is True


# ---------------------------------------------------------------------------
# 5. --execute --dry-run shows commands without running them
# ---------------------------------------------------------------------------


def test_replay_dry_run_shows_commands(replay_project, cli_runner):
    """Dry-run must print the reconstructed argvs but never shell out.

    We seed two events whose actions ARE in the roam registry
    (``preflight``, ``diff``) and one ``end`` event whose action is NOT
    a real subcommand. The dry-run preview should reconstruct the first
    two and skip the third.
    """
    run_id = "run_20260513_abc444"
    _seed_run(
        replay_project,
        run_id,
        events=[
            {"action": "preflight", "target": "useThemeClasses", "summary_verdict": "ok"},
            {"action": "diff", "target": "", "summary_verdict": "clean"},
            {"action": "end", "target": "", "summary_verdict": ""},  # unknown action
        ],
    )
    result = invoke_cli(
        cli_runner,
        ["replay", run_id, "--execute", "--dry-run"],
        cwd=replay_project,
        json_mode=True,
    )
    assert result.exit_code == 0, f"failed: {result.output}"
    data = parse_json_output(result, command="replay")
    assert "execute" in data, "envelope missing 'execute' block in dry-run mode"
    ex = data["execute"]
    assert ex["dry_run"] is True
    # Only the 2 known actions should be reconstructed.
    assert ex["would_run_count"] == 2
    actions = [item["argv"][1] for item in ex["commands"]]
    assert "preflight" in actions
    assert "diff" in actions
    # ``end`` is not a roam subcommand -> surfaced in unknown_actions.
    assert "end" in ex["unknown_actions"]
    # Dry-run must NOT have populated a results[] array.
    assert ex["results"] == []


# ---------------------------------------------------------------------------
# 6. In-progress run -> state=incomplete_run
# ---------------------------------------------------------------------------


def test_replay_handles_in_progress_run(replay_project, cli_runner):
    """A run with no ended_at + status=in_progress should surface cleanly."""
    run_id = "run_20260513_abc555"
    _seed_run(
        replay_project,
        run_id,
        status="in_progress",
        ended_at=None,
        events=[{"action": "preflight", "target": "foo", "summary_verdict": "ok"}],
    )
    result = invoke_cli(cli_runner, ["replay", run_id], cwd=replay_project, json_mode=True)
    data = parse_json_output(result, command="replay")
    assert data["summary"]["state"] == "incomplete_run"
    assert data["summary"]["partial_success"] is True
    # Suggested next_command should include closing the run.
    next_cmds = data["agent_contract"]["next_commands"]
    assert any("runs end" in nc for nc in next_cmds)
