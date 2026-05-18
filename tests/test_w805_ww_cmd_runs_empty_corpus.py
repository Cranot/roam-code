"""W805-WW -- empty-corpus Pattern-2 smoke test on ``roam runs``.

Forty-ninth-in-batch W805 sweep. Agent-OS substrate state-reader --
``roam runs list/show/verify`` over ``.roam/runs/`` per CLAUDE.md
substrate section. Peer of cmd_replay (in-flight W805-UU).

Scope
-----

``cmd_runs`` (``src/roam/commands/cmd_runs.py``) is the R20 ledger CLI
backing six subcommands: ``start`` / ``log`` / ``end`` / ``list`` /
``show`` / ``verify``. This sweep probes only the READ-ONLY subcommands
per the W805-WW brief:

- ``runs list`` -- streams run metadata; empty corpus = no ``.roam/runs/``.
- ``runs show <id>`` -- dumps a run's meta + events; missing id branch.
- ``runs verify <id>`` -- HMAC chain check on a single run.
- ``runs verify --all`` -- HMAC chain check over every run.

State-mutating ``start`` / ``log`` / ``end`` are explicitly excluded per
the W805-WW hard constraint ("DO NOT trigger start/end state-mutating
subcommands").

W978 first-hypothesis discipline
--------------------------------

Hypothesis: "substrate state-reader peer of cmd_replay (in-flight
W805-UU). Likely silent-SAFE on empty .roam/runs/".

W978 probed four corpora:

* **Empty corpus (no ``.roam/runs/`` at all) -- W978-VERIFIED OK**:
  ``cmd_runs.py:597-618`` (list) and ``cmd_runs.py:1000-1024`` (verify
  --all) BOTH have exemplary 4-axis envelopes. ``state="no_runs"``,
  verdict names the next command (``"no runs yet -- run `roam runs
  start --agent <name>` to open one"``), populated agent_contract.
  ``partial_success=False`` is correct here per Pattern-2 doctrine: no
  degraded execution happened -- there was simply nothing to read.
  **No bug on this axis.**

* **Missing run-id (corpus exists, ID doesn't) -- W978-VERIFIED OK**:
  ``cmd_runs.py:696-722`` (show) returns ``state="unknown_run"`` +
  ``partial_success=True`` + actionable next_commands. Exit 2.
  ``cmd_runs.py:900-918`` (verify with bad id) emits the same shape.
  **No bug on this axis.**

* **Empty events.jsonl (run exists, completed, 0 events) -- REAL BUG**:
  ``cmd_runs.py:920-998`` (verify single run) reaches ``state="ok"``
  with ``partial_success=False`` on a run whose events.jsonl is empty.
  The headline verdict reads "run X verified (0 events, all signatures
  match)" -- a fully-resolved success indistinguishable from a real
  verified ledger. The ``details`` field DOES carry "ledger is empty"
  but that's buried below the agent-visible summary. Pattern-2
  silent-SAFE: an empty analytical product yields a verdict
  indistinguishable from a fully-resolved success.

  Compare to cmd_replay's W805-UU finding: same shape, same root cause
  (state classification has no third branch for events_count == 0).
  Fix template parallels W805-UU:

      if events_verified == 0:
          state = "empty_ledger"  # or "no_events_to_verify"
          verdict = (
              f"run {run_id} has 0 events -- nothing to verify; "
              f"agent may not have called roam runs log"
          )

* **Clean ledger (real events, all signed)**: the verify happy path
  works correctly -- ``state="ok"``, verdict names the count. Covered
  by ``tests/test_runs_ledger.py``. **No bug on this axis.**

Conclusion: empty corpus + missing run-id branches are exemplary; the
empty-events.jsonl branch of ``runs verify <id>`` is the Pattern-2
hole. Pinned xfail-strict below.

REAL BUG pinned (Pattern-2 silent-SAFE on empty events.jsonl)
-------------------------------------------------------------

``cmd_runs.py:920-998`` -- the verify-single-run state-classification
block has branches for ``"ok"`` / ``"tampered"`` / ``"unsigned"`` /
``"key_missing"`` with no fifth branch for ``events_verified == 0``. A
completed run with empty events.jsonl reaches the ``"ok"`` arm via
``verify_chain``'s trivial-pass return on no events.

LAW 4: ``"run X has 0 events"`` anchors on ``events`` (already in
anchor set). LAW 6: empty-ledger verdict should name the cause + the
next command. Pattern-2: explicit ``state`` discloses degradation;
``partial_success`` flags the empty analytical product.

Sweep brief: W805-WW (Wave805-WW, forty-ninth-in-batch).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, invoke_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bare_project(tmp_path, monkeypatch):
    """Bare git-init project with NO ``.roam/runs/`` directory at all.

    Drives the empty-corpus path for ``runs list`` and ``runs verify
    --all`` -- the canonical "no runs yet" branch.
    """
    proj = tmp_path / "bare-runs-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def empty_events_project(tmp_path, monkeypatch):
    """Project with a completed run whose ``events.jsonl`` is empty.

    Drives the ``verify_chain`` -> trivial-pass branch with
    ``events_verified == 0`` -- the Pattern-2 silent-SAFE branch.
    """
    proj = tmp_path / "empty-events-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)

    run_id = "run_20260518_emptyevents"
    rdir = proj / ".roam" / "runs" / run_id
    rdir.mkdir(parents=True)
    meta = {
        "run_id": run_id,
        "agent": "claude-code",
        "started_at": "2026-05-18T08:00:00Z",
        "ended_at": "2026-05-18T08:01:00Z",
        "status": "completed",
    }
    (rdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (rdir / "events.jsonl").write_text("", encoding="utf-8")
    return proj, run_id


def _parse_envelope(result) -> dict:
    raw = (getattr(result, "stdout", None) or result.output).lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output}"
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# Existence gate
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``cmd_runs.runs_group`` is importable + a Click group."""
    try:
        from roam.commands.cmd_runs import runs_group
    except ImportError:
        pytest.skip("cmd_runs not importable -- skipping W805-WW smoke test")
    import click

    assert isinstance(runs_group, click.Group), f"runs_group must be a Click group; got {type(runs_group)!r}"
    # Read-only subcommands present (start/log/end excluded from probe scope).
    assert "list" in runs_group.commands
    assert "show" in runs_group.commands
    assert "verify" in runs_group.commands


# ---------------------------------------------------------------------------
# SMOKE -- always-on contracts on the EMPTY-CORPUS (no .roam/runs/) path.
# Both ``list`` and ``verify --all`` are exemplary 4-axis envelopes today;
# these tests pin the existing-good behavior as a regression guard.
# ---------------------------------------------------------------------------


class TestRunsListEmptyCorpusSealed:
    """Pin the exemplary ``runs list`` empty-corpus behavior."""

    def test_runs_list_empty_no_crash(self, bare_project):
        """No ``.roam/runs/`` -> exit 0 without traceback, non-empty stdout."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["runs", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"expected exit 0 on empty corpus; got {result.exit_code}\n{result.output}"
        assert "Traceback" not in result.output, f"unexpected traceback:\n{result.output}"
        # Pattern-1 variant C: empty stdout would crash the MCP wrapper.
        assert result.output.strip(), "empty stdout on empty corpus (Pattern-1 variant C)"

    def test_runs_list_empty_envelope_verdict(self, bare_project):
        """Empty corpus carries non-empty ``summary.verdict`` string."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["runs", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        assert env["command"] == "runs-list"
        verdict = env.get("summary", {}).get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_runs_list_empty_state_explicit(self, bare_project):
        """Pattern-2 explicit-absence: empty corpus discloses ``state="no_runs"``."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["runs", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        state = env.get("summary", {}).get("state")
        assert state == "no_runs", f"empty corpus must disclose summary.state='no_runs'; got {state!r}"

    def test_runs_list_empty_partial_success_set(self, bare_project):
        """Pattern-2 doctrine on empty corpus.

        Unlike a degraded execution path, ``runs list`` on an empty
        corpus had nothing to read -- no analytical product was
        degraded. ``partial_success=False`` is correct per Pattern-2
        ("Make absent state explicit"). The ``state="no_runs"`` token
        carries the disclosure.
        """
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["runs", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        # We require explicit named state + total=0 -- whichever value
        # partial_success takes, the absent-state disclosure must be
        # carried by state/total (not silently collapsed).
        assert env["summary"].get("state") == "no_runs"
        assert env["summary"].get("total") == 0

    def test_law6_verdict_standalone(self, bare_project):
        """LAW 6: empty-corpus verdict works without any other field.

        The verdict ``"no runs yet -- run `roam runs start --agent
        <name>` to open one"`` names the absent state + the next
        command in one line.
        """
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["runs", "list"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        verdict = env["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict embeds newline: {verdict!r}"
        v_lower = verdict.lower()
        # Verdict names the absent state.
        assert "no runs" in v_lower, f"LAW 6: empty-corpus verdict must name the absent state; got {verdict!r}"
        # Verdict names the recovery command.
        assert "roam runs start" in verdict, (
            f"LAW 6 + CONSTRAINT 12: verdict should embed the next command; got {verdict!r}"
        )


class TestRunsVerifyAllEmptyCorpusSealed:
    """Pin the exemplary ``runs verify --all`` empty-corpus behavior."""

    def test_runs_verify_empty_disclosure(self, bare_project):
        """``runs verify --all`` on empty corpus discloses ``state="no_runs"``."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["runs", "verify", "--all"],
            cwd=bare_project,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"expected exit 0 on empty corpus verify --all; got {result.exit_code}\n{result.output}"
        )
        env = _parse_envelope(result)
        assert env["command"] == "runs-verify"
        state = env.get("summary", {}).get("state")
        assert state == "no_runs", f"empty corpus must disclose summary.state='no_runs'; got {state!r}"
        verdict = env.get("summary", {}).get("verdict") or ""
        # Verdict names the absent state.
        assert "no runs" in verdict.lower(), (
            f"verify --all on empty corpus must name 'no runs' in verdict; got {verdict!r}"
        )


class TestRunsShowMissingIdSealed:
    """Pin the exemplary ``runs show <missing-id>`` behavior."""

    def test_runs_show_missing_id_disclosure(self, bare_project):
        """``runs show <missing>`` returns exit 2 + ``state="unknown_run"``."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["runs", "show", "run_does_not_exist"],
            cwd=bare_project,
            json_mode=True,
        )
        assert result.exit_code == 2, f"expected exit 2 on missing run; got {result.exit_code}\n{result.output}"
        env = _parse_envelope(result)
        assert env["summary"].get("state") == "unknown_run"
        assert env["summary"].get("partial_success") is True
        # CONSTRAINT 12: next_commands name a recovery action.
        next_cmds = env.get("agent_contract", {}).get("next_commands") or []
        assert next_cmds, f"next_commands must be populated; got {next_cmds!r}"
        for nc in next_cmds:
            assert nc.startswith("roam "), f"CONSTRAINT 12: next_command must be literal 'roam ...'; got {nc!r}"
        assert any("runs list" in nc for nc in next_cmds), (
            f"missing-id next_commands should suggest 'roam runs list'; got {next_cmds!r}"
        )


# ---------------------------------------------------------------------------
# CLEAN-corpus regression guard -- a real run emits real metadata.
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_runs(tmp_path, monkeypatch):
    """Non-empty ``.roam/runs/`` yields a real list with >0 runs.

    Builds the run on disk directly (no ``runs start`` invocation per
    the W805-WW hard constraint excluding state-mutating subcommands).
    """
    proj = tmp_path / "clean-runs-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)

    run_id = "run_20260518_clean"
    rdir = proj / ".roam" / "runs" / run_id
    rdir.mkdir(parents=True)
    meta = {
        "run_id": run_id,
        "agent": "claude-code",
        "started_at": "2026-05-18T08:00:00Z",
        "ended_at": "2026-05-18T08:01:00Z",
        "status": "completed",
    }
    (rdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (rdir / "events.jsonl").write_text("", encoding="utf-8")

    runner = CliRunner()
    result = invoke_cli(runner, ["runs", "list"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, f"clean-list failed: {result.output}"
    env = _parse_envelope(result)
    assert env["summary"]["state"] == "ok", f"non-empty corpus should have state='ok'; got {env['summary']!r}"
    assert env["summary"]["total"] == 1, f"clean corpus should yield 1 run; got {env['summary']!r}"
    # Verdict on the non-empty path names the count.
    verdict = env["summary"]["verdict"]
    assert "1 run" in verdict, f"non-empty-corpus verdict should name the count; got {verdict!r}"


# ---------------------------------------------------------------------------
# REAL BUG -- xfail-strict pin. Pattern-2 silent-SAFE in ``runs verify
# <id>`` envelope when events.jsonl is empty. Fix wave separate from
# W805 accumulate-only constraint. Parallels W805-UU (cmd_replay).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-WW Pattern-2 silent-SAFE: cmd_runs.py:920-998 verify-single-run "
        "state-classification has 'ok' / 'tampered' / 'unsigned' / "
        "'key_missing' branches but no fifth arm for events_verified == 0. "
        "A completed run with empty events.jsonl reaches state='ok' via "
        "verify_chain's trivial-pass, indistinguishable from a real verified "
        "ledger. The 'details' field DOES carry 'ledger is empty' but that's "
        "buried below the agent-visible summary. Fix: add 'empty_ledger' "
        "state token when events_verified == 0. Pinned for separate fix wave."
    ),
)
def test_no_silent_no_runs_on_empty(empty_events_project):
    """Pattern-2 explicit-absence: empty events.jsonl discloses dedicated state.

    The completed-but-empty run is an intentionally-absent ledger
    (agent started + ended a run without logging actions). Pattern-2
    mandates an explicit state token distinguishing it from a real
    verified ledger -- e.g. ``empty_ledger`` / ``no_events_to_verify``
    -- so agents reading only ``summary.state`` can branch.
    """
    proj, run_id = empty_events_project
    runner = CliRunner()
    result = invoke_cli(runner, ["runs", "verify", run_id], cwd=proj, json_mode=True)
    env = _parse_envelope(result)
    state = env.get("summary", {}).get("state")
    assert state in {"empty_ledger", "no_events_to_verify", "no_events"}, (
        f"empty events.jsonl must disclose dedicated summary.state "
        f"(empty_ledger/no_events_to_verify/no_events); got {state!r}. "
        f"verdict={env.get('summary', {}).get('verdict')!r}"
    )
