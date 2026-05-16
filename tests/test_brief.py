"""Tests for ``roam brief`` -- one-page agent briefing (W14.5).

Brief is a META-COMMAND that composes summaries from five existing
subsystems (next, agents-md, pr-bundle, mode, runs). The tests focus
on:

  * Graceful degradation when subsystems are absent (no index / no
    runs dir / no bundle / no constitution).
  * Envelope shape -- every top-level key the spec promises.
  * Text rendering stays under the one-page budget.
  * Performance budget (<500ms target -- generous to avoid flakes).

We do NOT exercise the substrate subsystems themselves; their own test
files own that coverage. We only confirm that brief composes them
correctly.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _make_indexed_project(tmp_path, name="brief_proj"):
    """Tiny indexed Python project with a class + function."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "service.py").write_text(
        "class UserService:\n"
        "    def get_user(self, user_id):\n"
        "        return {'id': user_id}\n"
        "\n"
        "def helper():\n"
        "    return UserService().get_user(1)\n"
    )
    git_init(proj)
    subprocess.run(["git", "checkout", "-B", "brief-test"], cwd=proj, capture_output=True)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# 1. Empty (un-indexed) repo
# ---------------------------------------------------------------------------


def test_brief_empty_repo_returns_clean_envelope(cli_runner, tmp_path, monkeypatch):
    """No ``.roam/`` at all -- brief must produce a valid envelope, not crash.

    Verifies: index_present is False, partial_success is True, every
    section emits a state, no Python traceback in output.
    """
    proj = tmp_path / "empty"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="brief")
    assert data["command"] == "brief"
    assert "summary" in data
    assert data["summary"]["index_present"] is False
    assert data["summary"]["partial_success"] is True
    # Every section should be present in the envelope even if its state
    # indicates "no data". Pattern 2 from CLAUDE.md.
    for key in ("next", "highlights", "mode", "runs", "pr_bundle"):
        assert key in data, f"envelope missing section: {key}"
        assert "state" in data[key], f"section {key} missing state field"


# ---------------------------------------------------------------------------
# 2. Indexed repo populates highlights
# ---------------------------------------------------------------------------


def test_brief_with_index_includes_highlights(cli_runner, tmp_path, monkeypatch):
    """An indexed project surfaces stack + conventions via highlights."""
    proj = _make_indexed_project(tmp_path, "brief_indexed")
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="brief")

    assert data["summary"]["index_present"] is True
    # Highlights section should be present with a stack list.
    hl = data["highlights"]
    assert hl["state"] in ("ok", "empty")
    assert isinstance(hl["stack"], list)
    assert isinstance(hl["danger_zones"], list)
    assert isinstance(hl["laws"], list)
    # Tiny fixtures may not surface danger zones or laws (require churn /
    # mining); we only assert the keys exist + are list-typed.


# ---------------------------------------------------------------------------
# 3. Active run is surfaced
# ---------------------------------------------------------------------------


def test_brief_with_active_run_includes_in_progress(cli_runner, tmp_path, monkeypatch):
    """An in-progress run on disk is surfaced in runs.in_progress."""
    proj = _make_indexed_project(tmp_path, "brief_run")
    monkeypatch.chdir(proj)

    from roam.runs.ledger import start_run

    meta = start_run(proj, agent="claude-code")
    assert meta.status == "in_progress"

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="brief")

    in_progress = data["runs"]["in_progress"]
    assert len(in_progress) == 1
    assert in_progress[0]["run_id"] == meta.run_id
    assert in_progress[0]["agent"] == "claude-code"
    # Verdict should mention active run count.
    assert "active run" in data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# 4. PR bundle is surfaced
# ---------------------------------------------------------------------------


def test_brief_with_pr_bundle_includes_bundle_status(cli_runner, tmp_path, monkeypatch):
    """An initialised PR bundle surfaces with intent + branch."""
    proj = _make_indexed_project(tmp_path, "brief_bundle")
    monkeypatch.chdir(proj)

    result_init = invoke_cli(
        cli_runner,
        ["pr-bundle", "init", "--intent", "Add retry logic"],
        cwd=proj,
    )
    assert result_init.exit_code == 0, result_init.output

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="brief")

    pb = data["pr_bundle"]
    assert pb["state"] == "active"
    assert pb["intent"] == "Add retry logic"
    assert pb["intent_set"] is True
    assert pb["branch"] == "brief-test"
    # Verdict should not say "no pr-bundle".
    assert "no pr-bundle" not in data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# 5. Active mode is surfaced
# ---------------------------------------------------------------------------


def test_brief_with_active_mode_shows_mode(cli_runner, tmp_path, monkeypatch):
    """Persisting an active mode surfaces it in mode.active."""
    proj = _make_indexed_project(tmp_path, "brief_mode")
    monkeypatch.chdir(proj)

    from roam.modes.policy import set_active_mode

    set_active_mode(proj, "read_only")

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="brief")

    assert data["mode"]["active"] == "read_only"
    assert data["mode"]["allowed_count"] > 0
    # The resolution path should record "file" somewhere in source.
    assert "file" in data["mode"]["source"]
    # Verdict should name the mode explicitly.
    assert "mode=read_only" in data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# 6. Text mode stays under one page
# ---------------------------------------------------------------------------


def test_brief_text_mode_under_60_lines(cli_runner, tmp_path, monkeypatch):
    """Text output is concise -- agent reads it in one pass, no scrolling.

    The spec calls for < 60 lines on a populated repo. We measure against
    a fully-populated brief (indexed, with a bundle and a run).
    """
    proj = _make_indexed_project(tmp_path, "brief_text")
    monkeypatch.chdir(proj)

    # Populate every section so the text render exercises full breadth.
    invoke_cli(cli_runner, ["pr-bundle", "init", "--intent", "test"], cwd=proj)
    from roam.runs.ledger import end_run, start_run

    m1 = start_run(proj, agent="claude-code")
    end_run(proj, m1.run_id, status="completed")

    result = invoke_cli(cli_runner, ["brief"], cwd=proj)
    assert result.exit_code == 0, result.output

    line_count = result.output.count("\n")
    assert line_count < 60, f"text too long: {line_count} lines\n{result.output}"
    # ASCII only -- LAW 7 (no emojis, no box-drawing).
    assert "VERDICT:" in result.output
    assert "ROAM BRIEF" in result.output


# ---------------------------------------------------------------------------
# 7. JSON envelope shape
# ---------------------------------------------------------------------------


def test_brief_json_envelope_shape(cli_runner, tmp_path, monkeypatch):
    """All expected top-level keys + summary/agent_contract subkeys present."""
    proj = _make_indexed_project(tmp_path, "brief_shape")
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="brief")

    # Top-level envelope keys.
    expected_top = {
        "command",
        "summary",
        "next",
        "highlights",
        "mode",
        "runs",
        "pr_bundle",
        "agent_contract",
    }
    missing = expected_top - set(data.keys())
    assert not missing, f"envelope missing top-level keys: {missing}"

    # summary structure.
    summary = data["summary"]
    for key in ("verdict", "state", "partial_success", "sections"):
        assert key in summary, f"summary missing {key}"
    assert isinstance(summary["sections"], list)

    # agent_contract structure (imperative + flat).
    contract = data["agent_contract"]
    assert "facts" in contract
    assert "next_commands" in contract
    assert isinstance(contract["facts"], list)
    assert isinstance(contract["next_commands"], list)
    # LAW 2 -- copy-paste-executable.
    for cmd in contract["next_commands"]:
        assert isinstance(cmd, str) and cmd.strip(), f"bad next_command: {cmd!r}"


# ---------------------------------------------------------------------------
# 8. Skip flags work as documented
# ---------------------------------------------------------------------------


def test_brief_skip_flags_exclude_sections(cli_runner, tmp_path, monkeypatch):
    """``--no-pr-bundle`` etc. mark the relevant section state ``skipped``."""
    proj = _make_indexed_project(tmp_path, "brief_skip")
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["brief", "--no-pr-bundle", "--no-runs"],
        cwd=proj,
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="brief")

    # Skipped sections should be marked as such; sections list should not
    # include them.
    assert data["pr_bundle"]["state"] == "skipped"
    assert data["runs"]["state"] == "skipped"
    assert "pr_bundle" not in data["summary"]["sections"]
    assert "runs" not in data["summary"]["sections"]
    # But mode / next / highlights still consulted.
    assert "mode" in data["summary"]["sections"]
    assert "next" in data["summary"]["sections"]
    assert "highlights" in data["summary"]["sections"]


# ---------------------------------------------------------------------------
# 9. Performance budget
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_brief_performance_under_500ms(cli_runner, tmp_path, monkeypatch):
    """Brief must stay under ~500ms on a tiny indexed repo.

    Marked slow because it's a timing-sensitive perf gate. We give it
    generous headroom (1.5s) to avoid flakes on CI shared-runner load
    while still catching any subprocess-shelling regression.
    """
    proj = _make_indexed_project(tmp_path, "brief_perf")
    monkeypatch.chdir(proj)

    # Warm-up call -- pay any first-import cost (lazy-loaded modules)
    # before the timed measurement.
    invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)

    t0 = time.perf_counter()
    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    # 1.5s budget -- much higher than the 500ms target to absorb CI
    # variance; a regression that shells out would still trip this.
    assert elapsed < 1.5, f"brief too slow: {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# 10. W15.2 followup -- public-API helpers are importable
# ---------------------------------------------------------------------------


def test_agents_md_public_section_helpers_importable():
    """W15.2 followup: the section helpers used by brief MUST be importable
    from the ``roam.agents_md`` package, not just from the private
    ``roam.agents_md.generator`` module.

    This pins the contract so a future rename can't silently break brief.
    """
    from roam.agents_md import (
        section_danger_zones,
        section_laws,
        section_stack,
    )

    assert callable(section_stack)
    assert callable(section_danger_zones)
    assert callable(section_laws)

    # Aliases for backward compat — should still resolve.
    from roam.agents_md.generator import (
        _section_danger_zones,
        _section_laws,
        _section_stack,
    )

    assert _section_stack is section_stack
    assert _section_danger_zones is section_danger_zones
    assert _section_laws is section_laws


def test_brief_uses_public_section_api(cli_runner, tmp_path, monkeypatch):
    """W15.2 followup: brief still works after the import path change.

    Smoke test that the renamed-import inside ``cmd_brief._section_highlights``
    resolves and produces a populated highlights section on an indexed repo.
    """
    proj = _make_indexed_project(tmp_path, "brief_public_api")
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="brief")

    # Highlights are present + populated (stack should be non-empty on an
    # indexed Python project).
    hl = data["highlights"]
    assert hl["state"] in ("ok", "empty"), hl
    assert isinstance(hl.get("stack"), list)
    # The fixture is a Python file → stack should surface "python".
    if hl["state"] == "ok":
        langs = [e.get("language", "").lower() for e in hl.get("stack", [])]
        assert any("python" in l for l in langs), hl["stack"]
