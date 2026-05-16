"""Tests for ``roam agent-score`` (R20 phase 3).

Like ``replay``, agent-score is a pure read of ``.roam/runs/``. We
hand-craft fake ledger directories and assert on the rendered envelope
+ score components.

Coverage:
  1. empty .roam/runs/ -> state=no_data, never crash
  2. single completed run, no partials -> score >= 70 (completion-only)
  3. multiple agents -> one entry per agent in agents[]
  4. partial-success on one agent lowers its score vs a clean agent
  5. confidence=low when an agent has <2 runs (and verdict says so)
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
def score_project(tmp_path):
    """Bare git-initialised project; tests seed .roam/runs/ as needed."""
    proj = tmp_path / "scoreproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


def _seed_run(
    project: Path,
    run_id: str,
    agent: str,
    events: list[dict] | None = None,
    status: str = "completed",
    started_at: str = "2026-05-13T08:14:33Z",
    ended_at: str | None = "2026-05-13T08:15:03Z",
) -> Path:
    """Hand-write ``.roam/runs/<run_id>/`` for the test."""
    rdir = project / ".roam" / "runs" / run_id
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "agent": agent,
                "started_at": started_at,
                "ended_at": ended_at,
                "status": status,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    events_text = ""
    for i, ev in enumerate(events or [], start=1):
        merged = dict(ev)
        merged.setdefault("seq", i)
        merged.setdefault("ts", "2026-05-13T08:14:34Z")
        events_text += json.dumps(merged, sort_keys=True) + "\n"
    (rdir / "events.jsonl").write_text(events_text, encoding="utf-8")
    return rdir


def _agents_map(data: dict) -> dict[str, dict]:
    """Index ``agents[]`` by agent name for easy assertions."""
    return {a["agent"]: a for a in data.get("agents", [])}


# ---------------------------------------------------------------------------
# 1. No runs at all -> state=no_data
# ---------------------------------------------------------------------------


def test_agent_score_no_runs_returns_no_data(score_project, cli_runner):
    """Empty ``.roam/runs/`` must not crash; must emit a clean envelope."""
    result = invoke_cli(cli_runner, ["agent-score"], cwd=score_project, json_mode=True)
    assert result.exit_code == 0, f"failed: {result.output}"
    data = parse_json_output(result, command="agent-score")
    assert data["summary"]["state"] == "no_data"
    assert data["summary"]["agents_scored"] == 0
    assert data["agents"] == []


# ---------------------------------------------------------------------------
# 2. Single clean completed run -> score >= 70
# ---------------------------------------------------------------------------


def test_agent_score_single_completed_run(score_project, cli_runner):
    """One completed run with no partials should clear the 70-point floor.

    completion=1.0 (1 run completed of 1 total)  -> 70
    clean_rate=1.0 (0 partials of N events)      -> 20
    breadth: 5 unique actions cap at 1.0         -> 10
    Total: 100. We assert >= 70 to leave room for breadth shortfalls.
    """
    _seed_run(
        score_project,
        "run_20260513_solo01",
        agent="claude-code",
        events=[
            {"action": "preflight", "target": "foo", "summary_verdict": "ok"},
            {"action": "diff", "target": "", "summary_verdict": "clean"},
            {"action": "critique", "target": "", "summary_verdict": "SAFE"},
            {"action": "verify", "target": "", "summary_verdict": "ok"},
            {"action": "attest", "target": "", "summary_verdict": "signed"},
        ],
    )
    result = invoke_cli(cli_runner, ["agent-score"], cwd=score_project, json_mode=True)
    data = parse_json_output(result, command="agent-score")
    assert data["summary"]["agents_scored"] == 1
    agents = _agents_map(data)
    assert "claude-code" in agents
    a = agents["claude-code"]
    assert a["score"] >= 70.0, f"expected score >= 70, got {a['score']}"
    # Score components are surfaced.
    comps = a["score_components"]
    assert comps["completion_rate"] == 1.0
    assert comps["clean_signal_rate"] == 1.0


# ---------------------------------------------------------------------------
# 3. Multiple agents -> one row per agent
# ---------------------------------------------------------------------------


def test_agent_score_multiple_agents(score_project, cli_runner):
    """3 runs spread across 2 agents -> envelope has 2 agent entries."""
    _seed_run(
        score_project,
        "run_20260513_aaa001",
        agent="claude-code",
        events=[{"action": "preflight", "target": "x", "summary_verdict": "ok"}],
    )
    _seed_run(
        score_project,
        "run_20260513_aaa002",
        agent="claude-code",
        events=[{"action": "diff", "target": "", "summary_verdict": "ok"}],
    )
    _seed_run(
        score_project,
        "run_20260513_bbb001",
        agent="cursor",
        events=[{"action": "preflight", "target": "y", "summary_verdict": "ok"}],
    )
    result = invoke_cli(cli_runner, ["agent-score"], cwd=score_project, json_mode=True)
    data = parse_json_output(result, command="agent-score")
    assert data["summary"]["agents_scored"] == 2
    agents = _agents_map(data)
    assert "claude-code" in agents and "cursor" in agents
    assert agents["claude-code"]["runs_total"] == 2
    assert agents["cursor"]["runs_total"] == 1
    # Confidence: claude-code has 2 runs (ok), cursor has 1 (low).
    assert agents["claude-code"]["confidence"] == "ok"
    assert agents["cursor"]["confidence"] == "low"


# ---------------------------------------------------------------------------
# 4. Partial-success hits the score
# ---------------------------------------------------------------------------


def test_agent_score_partial_success_lowers_score(score_project, cli_runner):
    """Agent with partial_success on every event scores below a clean agent.

    Both agents complete their single run, so completion_rate is 1.0
    for both. The dirty agent's clean_signal_rate drops to 0, costing
    20 points; the clean agent keeps them.
    """
    # Clean agent.
    _seed_run(
        score_project,
        "run_20260513_clean1",
        agent="clean-agent",
        events=[
            {"action": "preflight", "target": "x", "summary_verdict": "ok"},
            {"action": "diff", "target": "", "summary_verdict": "clean"},
            {"action": "verify", "target": "", "summary_verdict": "ok"},
        ],
    )
    # Dirty agent: every event marked partial.
    _seed_run(
        score_project,
        "run_20260513_dirty1",
        agent="dirty-agent",
        events=[
            {"action": "preflight", "target": "x", "summary_verdict": "ok", "partial_success": True},
            {"action": "diff", "target": "", "summary_verdict": "ok", "partial_success": True},
            {"action": "verify", "target": "", "summary_verdict": "ok", "partial_success": True},
        ],
    )
    result = invoke_cli(cli_runner, ["agent-score"], cwd=score_project, json_mode=True)
    data = parse_json_output(result, command="agent-score")
    agents = _agents_map(data)
    clean_score = agents["clean-agent"]["score"]
    dirty_score = agents["dirty-agent"]["score"]
    assert clean_score > dirty_score, f"expected clean ({clean_score}) > dirty ({dirty_score})"
    # The gap should be roughly the 20-point clean_signal weight.
    assert (clean_score - dirty_score) >= 15.0


# ---------------------------------------------------------------------------
# 5. Confidence=low with a single run
# ---------------------------------------------------------------------------


def test_agent_score_confidence_low_when_few_runs(score_project, cli_runner):
    """Single-run agent -> confidence=low, verdict mentions it explicitly."""
    _seed_run(
        score_project,
        "run_20260513_only01",
        agent="claude-code",
        events=[{"action": "preflight", "target": "foo", "summary_verdict": "ok"}],
    )
    result = invoke_cli(
        cli_runner,
        ["agent-score", "--agent", "claude-code"],
        cwd=score_project,
        json_mode=True,
    )
    data = parse_json_output(result, command="agent-score")
    agents = _agents_map(data)
    assert agents["claude-code"]["confidence"] == "low"
    # Verdict should say "low confidence" out loud.
    assert "low confidence" in data["summary"]["verdict"].lower()
