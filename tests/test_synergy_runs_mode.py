"""W14.2 Synergy 4 — ``roam runs start`` records the active mode.

When a run is opened, ``start_run`` consults
:func:`roam.modes.policy.get_active_mode` and stamps the result into
``meta.json`` (and the in-memory :class:`RunMeta`). ``roam runs start``
and ``roam runs show`` surface the captured mode in their envelopes,
and ``roam replay`` mentions it at the top of the narrative.

Three tests:
  1. start_run + read_run_meta round-trip the mode field
  2. roam runs show envelope surfaces mode in summary + run dict
  3. roam replay narrative mentions mode=<name>
"""

from __future__ import annotations

import json
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

from roam.modes import set_active_mode  # noqa: E402
from roam.runs.ledger import (  # noqa: E402
    log_event,
    read_run_meta,
    start_run,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runs_project(tmp_path, monkeypatch):
    """Minimal git-initialised project with no runs yet, mode-aware."""
    proj = tmp_path / "synergy4_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    # Clear any inherited env mode so .roam/active_mode is the only
    # opinion expressed.
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    return proj


# ---------------------------------------------------------------------------
# 1. start_run stamps active mode into meta.json
# ---------------------------------------------------------------------------


def test_runs_start_records_active_mode_in_meta(runs_project):
    """Persist a mode, open a run, confirm meta.json + RunMeta carry it.

    Round-trip: set ``read_only`` -> start_run -> read_run_meta should
    return mode=read_only. With no active_mode file, start_run leaves
    the field None (and meta.json omits the key — backward compatible).
    """
    set_active_mode(runs_project, "read_only")
    meta = start_run(runs_project, agent="claude-code")
    assert meta.mode == "read_only", meta

    # Round-trip through disk.
    fresh = read_run_meta(runs_project, meta.run_id)
    assert fresh is not None
    assert fresh.mode == "read_only", fresh

    # Raw JSON also carries the field.
    raw_path = runs_project / ".roam" / "runs" / meta.run_id / "meta.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw.get("mode") == "read_only", raw

    # No-mode contract: when active_mode file is absent, mode is None
    # AND meta.json OMITS the field entirely (backward compat).
    (runs_project / ".roam" / "active_mode").unlink()
    meta2 = start_run(runs_project, agent="cursor")
    assert meta2.mode is None, meta2
    raw2_path = runs_project / ".roam" / "runs" / meta2.run_id / "meta.json"
    raw2 = json.loads(raw2_path.read_text(encoding="utf-8"))
    assert "mode" not in raw2, raw2


# ---------------------------------------------------------------------------
# 2. roam runs show envelope surfaces the mode
# ---------------------------------------------------------------------------


def test_runs_show_surfaces_mode_in_envelope(cli_runner, runs_project, monkeypatch):
    """``roam runs show <id> --json`` includes mode in summary + run dict."""
    monkeypatch.chdir(runs_project)
    set_active_mode(runs_project, "safe_edit")
    meta = start_run(runs_project, agent="claude-code")

    result = invoke_cli(
        cli_runner, ["runs", "show", meta.run_id], cwd=runs_project, json_mode=True
    )
    data = parse_json_output(result, "runs-show")
    assert_json_envelope(data, "runs-show")

    # Summary: mode key is present and matches.
    assert data["summary"].get("mode") == "safe_edit", data["summary"]
    # Verdict line names the mode (the human-readable surface).
    assert "safe_edit" in data["summary"]["verdict"], data["summary"]["verdict"]
    # Nested run dict echoes the mode (from RunMeta.to_dict()).
    assert data["run"].get("mode") == "safe_edit", data["run"]


# ---------------------------------------------------------------------------
# 3. roam replay narrative mentions the mode at the top
# ---------------------------------------------------------------------------


def test_replay_narrative_mentions_mode(cli_runner, runs_project, monkeypatch):
    """``roam replay <id>`` (text mode) prints ``mode=<name>`` on RUN line.

    We open a mode-tagged run, log one event, and inspect the text
    narrative. The first line should match::

        RUN <id> (agent=..., mode=safe_edit) started ..., N events, ...
    """
    monkeypatch.chdir(runs_project)
    set_active_mode(runs_project, "safe_edit")

    meta = start_run(runs_project, agent="claude-code")
    log_event(runs_project, meta.run_id, action="preflight", target="useFoo")

    result = invoke_cli(cli_runner, ["replay", meta.run_id], cwd=runs_project)
    assert result.exit_code == 0, result.output
    out = result.output
    # The header line carries the mode tag.
    assert "mode=safe_edit" in out, out
    # And the run id is still surfaced.
    assert meta.run_id in out, out
