"""W805-TT: Empty-corpus Pattern-2 smoke test on ``cmd_brief``.

Pattern-2 (silent-SAFE) audit. ``roam brief`` aggregates five
sections (next / highlights / pr-bundle / mode / runs). On a freshly
indexed but otherwise empty corpus, four of those five sections
report degraded states (``empty`` / ``no_runs`` / ``no_active_bundle``
/ ``idle``), yet ``summary.state`` reports ``"ok"`` and
``summary.partial_success`` is ``False``.

Bug location: ``src/roam/commands/cmd_brief.py:815`` --
``partial_success`` is true only when a section *raised* OR the index
is missing. Degraded-but-non-exception section states are silently
treated as ``ok``.

W978 first-hypothesis discipline: probe was repeated twice; both
runs reproduced the same shape.

These tests are read-only against ``cmd_brief.py``. The fix-forward
test (``test_no_silent_brief_clean_on_empty``) is pinned via
``xfail(strict=True)`` until the bug is repaired -- per W805 sweep
charter "pin via xfail-strict; do not fix".
"""

from __future__ import annotations

import subprocess
import sys
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

_CMD_PATH = Path(__file__).resolve().parents[1] / "src" / "roam" / "commands" / "cmd_brief.py"


@pytest.fixture
def cli_runner():
    return CliRunner()


def _make_empty_indexed_project(tmp_path: Path, name: str = "brief_w805tt") -> Path:
    """Index an essentially empty Python repo: just enough for ``roam init``."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    # Minimal source so the index has at least one parsed file but no
    # findings / runs / bundle / conventions / laws / danger.
    (proj / "noop.py").write_text("def noop():\n    return None\n")
    git_init(proj)
    subprocess.run(
        ["git", "checkout", "-B", "w805tt"],
        cwd=proj,
        capture_output=True,
    )
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# 1. Existence guard
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """If cmd_brief.py vanishes, this whole module skips rather than errors."""
    if not _CMD_PATH.is_file():
        pytest.skip(f"cmd_brief.py absent at {_CMD_PATH}")
    assert _CMD_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# 2. Empty corpus does not crash
# ---------------------------------------------------------------------------


def test_empty_corpus_no_crash(cli_runner, tmp_path, monkeypatch):
    """No ``.roam/``, no git -- brief must exit 0 with a parseable envelope."""
    proj = tmp_path / "untouched"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# 3. Envelope always carries a verdict
# ---------------------------------------------------------------------------


def test_empty_corpus_envelope_has_verdict(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "verdict"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="brief")
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), "summary.verdict must be non-empty"


# ---------------------------------------------------------------------------
# 4. Every section carries an explicit state even when degraded
# ---------------------------------------------------------------------------


def test_empty_corpus_state_explicit(cli_runner, tmp_path, monkeypatch):
    """Every section emits a non-empty ``state`` key."""
    proj = tmp_path / "state_explicit"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="brief")
    for key in ("next", "highlights", "mode", "runs", "pr_bundle"):
        assert key in data, f"envelope missing section {key}"
        section = data[key]
        assert isinstance(section, dict)
        state = section.get("state")
        assert isinstance(state, str) and state, f"section {key} missing/empty state"


# ---------------------------------------------------------------------------
# 5. No-index empty repo flags partial_success
# ---------------------------------------------------------------------------


def test_empty_corpus_partial_success_set(cli_runner, tmp_path, monkeypatch):
    """When the index is absent, ``partial_success`` must be True."""
    proj = tmp_path / "noindex"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="brief")
    assert data["summary"]["index_present"] is False
    assert data["summary"]["partial_success"] is True


# ---------------------------------------------------------------------------
# 6. LAW 6: verdict alone names the degraded state
# ---------------------------------------------------------------------------


def test_empty_corpus_law6_verdict_standalone(cli_runner, tmp_path, monkeypatch):
    """LAW 6: ``summary.verdict`` must work alone -- name the mode, next,
    and absent runs/bundle."""
    proj = tmp_path / "law6"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="brief")
    verdict = data["summary"]["verdict"].lower()
    # Verdict must name the active mode, "no runs", and "no pr-bundle".
    assert "mode=" in verdict, verdict
    assert "no runs" in verdict, verdict
    assert "no pr-bundle" in verdict, verdict


# ---------------------------------------------------------------------------
# 7. Indexed-but-empty corpus: silent-SAFE bug pin (Pattern 2)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-TT Pattern-2 bug pin: cmd_brief.py:815 sets "
        "summary.state='ok' + partial_success=False whenever no section "
        "raised AND the index exists, even when 4/5 sections are in "
        "degraded states (highlights=empty, runs=no_runs, "
        "pr_bundle=no_active_bundle, next=idle). Should report a "
        "non-ok aggregate state or partial_success=True."
    ),
)
def test_no_silent_brief_clean_on_empty(cli_runner, tmp_path, monkeypatch):
    """Indexed corpus with no findings/runs/bundle must NOT report ``state: 'ok'``
    with ``partial_success: false``.

    This is the canonical Pattern-2 silent-SAFE shape: four sections
    explicitly report empty/no-data, but the aggregate summary says
    everything is fine.
    """
    proj = _make_empty_indexed_project(tmp_path)
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="brief")

    summary = data["summary"]
    highlights_state = data["highlights"]["state"]
    runs_state = data["runs"]["state"]
    bundle_state = data["pr_bundle"]["state"]

    # Confirm the precondition: this is actually a degraded corpus.
    degraded = (
        highlights_state in ("empty", "no_index", "unavailable")
        or runs_state in ("no_runs", "unavailable")
        or bundle_state in ("no_active_bundle", "unavailable")
    )
    assert degraded, (
        "test precondition failed -- corpus not degraded "
        f"(hl={highlights_state}, runs={runs_state}, bundle={bundle_state})"
    )

    # When degraded, the aggregate must not silently report a clean
    # state. Either partial_success is True OR summary.state names the
    # degradation (e.g., "partial" / "empty" / "degraded").
    is_silent_safe = summary.get("partial_success") is False and summary.get("state") == "ok"
    assert not is_silent_safe, (
        "Pattern-2 silent-SAFE: summary.state='ok' + "
        "partial_success=False on a corpus where "
        f"highlights={highlights_state}, runs={runs_state}, "
        f"pr_bundle={bundle_state}."
    )


# ---------------------------------------------------------------------------
# 8. Clean corpus: real brief sanity check (non-empty sections render)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_brief(cli_runner, tmp_path, monkeypatch):
    """An indexed corpus -- whatever the aggregate state field decides --
    still emits structured ``next``/``mode``/``highlights``/``runs``/``pr_bundle``
    sections with non-empty state strings and the agent_contract block."""
    proj = _make_empty_indexed_project(tmp_path, name="clean_brief")
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["brief"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="brief")

    # Every promised section is present and structured.
    for key in ("next", "highlights", "mode", "runs", "pr_bundle"):
        assert isinstance(data.get(key), dict), f"{key} not dict"
        assert data[key].get("state"), f"{key}.state empty"

    # agent_contract is present with at least one fact + next_command.
    contract = data.get("agent_contract") or {}
    facts = contract.get("facts") or []
    next_cmds = contract.get("next_commands") or []
    assert isinstance(facts, list) and facts, "agent_contract.facts missing"
    assert isinstance(next_cmds, list) and next_cmds, "agent_contract.next_commands missing"
    assert all(isinstance(c, str) and c.strip() for c in next_cmds)
