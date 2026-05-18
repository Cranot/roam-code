"""W805-UU -- empty-corpus Pattern-2 smoke test on ``roam replay``.

Forty-seventh-in-batch W805 sweep. Agent-OS daily-flow surface --
``roam replay <run-id>`` narrates ledger events from ``.roam/runs/``.

Scope
-----

``cmd_replay`` (``src/roam/commands/cmd_replay.py``) is the R20-phase-3
substrate consumer that re-narrates a past agent run. It reads
``.roam/runs/<run_id>/meta.json`` + ``events.jsonl`` and emits either a
numbered text timeline or a structured JSON envelope. ``--execute``
optionally reruns the logged commands as a drift check.

W978 first-hypothesis discipline
--------------------------------

Hypothesis: "agent-OS daily-flow surface consumes ledger events from
``.roam/runs/`` -- empty-ledger / missing-run-id paths likely silent
Pattern-2 SAFE per W805 sweep trend".

W978 probed three corpora:

* **Missing run-id (W978-VERIFIED OK)**: ``cmd_replay.py:210-244`` already
  has an exemplary 4-axis envelope on missing-run: ``state="missing_run"``,
  ``partial_success=True``, verdict ``"run X not found under .roam/runs/"``
  (names the issue + the directory), ``next_commands=["roam runs list",
  "roam runs start --agent <name>"]``. CONSTRAINT 12 satisfied with literal
  ``roam`` strings. **No bug on this axis.**

* **Empty ledger (run exists, 0 events) -- REAL BUG**: ``cmd_replay.py:260-282``
  treats a completed-but-empty run as a fully-resolved success:

  - ``summary.state = "ok"`` despite ZERO events being logged. Pattern-2
    explicit-absence violation: the empty ledger is intentional absence
    (run started + ended without logging actions) but the envelope says
    "ok" indistinguishably from a normal completed run.
  - ``summary.partial_success = False`` -- but the run produced no
    analytical product (0 actions, 0 gate commands, 0 verdicts). Pattern-2
    silent-SAFE: a verdict indistinguishable from a fully-resolved success
    on a degraded outcome.
  - ``summary.verdict = "agent X ran 0 action(s)"`` -- analytical verb
    ``ran`` auto-anchors per LAW 4, but the verdict does NOT name the
    next action (LAW 6 borderline; the verdict works standalone but
    leaves the agent with no copy-pasteable next step embedded in the
    verdict itself).

  Compare to the missing-run path's verdict
  (``"run X not found under .roam/runs/"``) which names the failure
  state + location -- the LAW-6-correct shape for the empty-ledger
  branch would be e.g. ``"replay found 0 logged events in run X --
  agent may not have called 'roam runs log'"``.

* **In-progress run** (existing ``test_replay.py:257-273`` covers this):
  ``state="incomplete_run"``, ``partial_success=True``, ``next_commands``
  include ``"roam runs end --run-id X"``. **No bug on this axis.**

Conclusion: missing-run + in-progress branches are exemplary; the
empty-ledger branch is the Pattern-2 hole. Pinned xfail-strict below.

REAL BUG pinned (Pattern-2 silent-SAFE on empty ledger)
-------------------------------------------------------

``cmd_replay.py:260-282`` -- the state-classification block decides
between ``"incomplete_run"`` (no ``ended_at``) and ``"ok"`` (default), with
no third branch for ``events_count == 0``. A completed run with an empty
ledger reaches the ``"ok"`` arm and emits a fully-resolved success.

Fix template:

    if meta.status == "in_progress" or not meta.ended_at:
        state = "incomplete_run"
    elif events_count == 0:
        state = "empty_ledger"  # or "no_events_logged"
    else:
        state = "ok"

    if state == "empty_ledger":
        verdict = (
            f"replay found 0 events in {run_id} -- agent ran but never "
            f"called roam runs log; run roam runs list to confirm"
        )
        # partial_success=True derived from state in summary block

LAW 4: ``"replay found 0 events"`` anchors on ``events`` (already in
anchor set). LAW 6: verdict names the next command + the cause.
Pattern-2: explicit ``state`` discloses degradation;
``partial_success`` flags the empty analytical product.

Sweep brief: W805-UU (Wave805-UU, forty-seventh-in-batch).
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

    Drives the ``read_run_meta`` -> ``None`` branch for any run_id query --
    the canonical "missing run" path.
    """
    proj = tmp_path / "bare-replay-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def empty_ledger_project(tmp_path, monkeypatch):
    """Project with a completed run whose ``events.jsonl`` is empty.

    Drives the ``state="ok"`` path with ``events_count == 0`` -- the
    Pattern-2 silent-SAFE branch.
    """
    proj = tmp_path / "empty-ledger-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)

    run_id = "run_20260518_emptyledger"
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


@pytest.fixture
def clean_ledger_project(tmp_path, monkeypatch):
    """Project with a completed run containing real logged events."""
    proj = tmp_path / "clean-ledger-proj"
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
    events = [
        {
            "seq": 1,
            "ts": "2026-05-18T08:00:10Z",
            "action": "preflight",
            "target": "foo",
            "summary_verdict": "SAFE: 0 high-severity",
        },
        {
            "seq": 2,
            "ts": "2026-05-18T08:00:20Z",
            "action": "diff",
            "target": "",
            "summary_verdict": "3 files changed",
        },
    ]
    (rdir / "events.jsonl").write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in events) + "\n",
        encoding="utf-8",
    )
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
    """``cmd_replay.replay_cmd`` is importable + a Click command."""
    try:
        from roam.commands.cmd_replay import replay_cmd
    except ImportError:
        pytest.skip("cmd_replay not importable -- skipping W805-UU smoke test")
    import click

    assert isinstance(replay_cmd, click.Command), f"replay_cmd must be a Click command; got {type(replay_cmd)!r}"


# ---------------------------------------------------------------------------
# SMOKE -- always-on contracts. The MISSING-RUN-ID path is fully correct
# today (4-axis lens passes); these tests pin the existing-good behavior
# as a regression guard.
# ---------------------------------------------------------------------------


class TestReplayMissingRunIdSealed:
    """Properties already satisfied by the missing-run-id envelope today.

    ``cmd_replay.py:210-244`` is the exemplary 4-axis branch -- explicit
    state + partial_success + LAW-6 verdict + populated next_commands.
    These tests guard against regression.
    """

    def test_empty_corpus_no_crash(self, bare_project):
        """No ``.roam/runs/`` + arbitrary run-id -> exit 2 without traceback."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["replay", "run_does_not_exist"],
            cwd=bare_project,
            json_mode=True,
        )
        # Missing-run is a structured failure (exit 2), not a crash.
        assert result.exit_code == 2, f"expected exit 2 on missing run; got {result.exit_code}\n{result.output}"
        assert "Traceback" not in result.output, f"unexpected traceback in missing-run output:\n{result.output}"

    def test_empty_corpus_envelope_has_verdict(self, bare_project):
        """Missing-run envelope carries non-empty ``summary.verdict`` string."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["replay", "run_does_not_exist"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        assert env["command"] == "replay"
        verdict = env.get("summary", {}).get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_state_explicit(self, bare_project):
        """Pattern-2 explicit-absence: missing-run discloses ``state="missing_run"``."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["replay", "run_does_not_exist"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        state = env.get("summary", {}).get("state")
        assert state == "missing_run", f"missing-run must disclose summary.state='missing_run'; got {state!r}"

    def test_empty_corpus_partial_success_set(self, bare_project):
        """Pattern-2 silent-SAFE: missing-run sets ``partial_success=True``."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["replay", "run_does_not_exist"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        assert env["summary"].get("partial_success") is True, (
            f"missing-run must set summary.partial_success=True; got summary={env['summary']!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, bare_project):
        """LAW 6: missing-run verdict works without any other field.

        The verdict ``"run X not found under .roam/runs/"`` names the
        failure subject + the directory -- an agent reading only the
        verdict can act on it.
        """
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["replay", "run_does_not_exist"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        verdict = env["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict embeds newline: {verdict!r}"
        # Verdict names the subject + the directory it looked in.
        v_lower = verdict.lower()
        assert "not found" in v_lower or "missing" in v_lower or "does not" in v_lower, (
            f"LAW 6: missing-run verdict must name the failure mode; got {verdict!r}"
        )

    def test_empty_corpus_law4_facts_anchored(self, bare_project):
        """LAW 4: ``agent_contract.facts`` terminals are concrete-noun anchored.

        ``cmd_replay`` emits ``facts_extra`` like ``"run X does not exist on
        disk"`` -- terminal ``disk`` is anchored via the analytical verb
        ``does not exist`` substring.
        """
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["replay", "run_does_not_exist"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        facts = env.get("agent_contract", {}).get("facts") or []
        assert facts, f"agent_contract.facts must be non-empty; got {facts!r}"
        # At least one fact non-empty string.
        assert any(isinstance(f, str) and f.strip() for f in facts), (
            f"agent_contract.facts must contain non-empty strings; got {facts!r}"
        )

    def test_empty_corpus_next_commands_present(self, bare_project):
        """CONSTRAINT 12: missing-run names a copy-pasteable ``roam ...`` next step."""
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["replay", "run_does_not_exist"],
            cwd=bare_project,
            json_mode=True,
        )
        env = _parse_envelope(result)
        next_cmds = env.get("agent_contract", {}).get("next_commands") or []
        assert next_cmds, f"agent_contract.next_commands must be populated on missing-run; got {next_cmds!r}"
        # All entries are literal "roam <subcommand>" strings (CONSTRAINT 12).
        for nc in next_cmds:
            assert nc.startswith("roam "), f"CONSTRAINT 12: next_command must be literal 'roam ...'; got {nc!r}"
        # Specifically: the missing-run path should suggest 'roam runs list'.
        assert any("runs list" in nc for nc in next_cmds), (
            f"missing-run next_commands should include 'roam runs list'; got {next_cmds!r}"
        )


# ---------------------------------------------------------------------------
# CLEAN-corpus regression guard -- a real ledger emits a real replay.
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_replay(clean_ledger_project):
    """Non-empty ledger emits a real replay with >0 events + analytical verdict."""
    proj, run_id = clean_ledger_project
    runner = CliRunner()
    result = invoke_cli(runner, ["replay", run_id], cwd=proj, json_mode=True)
    assert result.exit_code == 0, f"clean-ledger failed: {result.output}"
    env = _parse_envelope(result)
    assert env["events_count"] == 2, f"clean ledger should yield 2 events; got {env['events_count']}"
    assert env["summary"]["state"] == "ok", f"completed run with events should have state='ok'; got {env['summary']!r}"
    # Verdict on the non-empty path names the analytical work done.
    verdict = env["summary"]["verdict"]
    assert "gate command" in verdict or "action" in verdict, (
        f"non-empty-ledger verdict should name the work done; got {verdict!r}"
    )
    # SAFE verdict from the preflight event should propagate.
    assert "SAFE" in verdict, f"SAFE verdict from preflight should propagate; got {verdict!r}"


# ---------------------------------------------------------------------------
# REAL BUG -- xfail-strict pins. Pattern-2 silent-SAFE in empty-ledger
# envelope (run exists + completed, but events.jsonl is empty).
# Fix wave separate from W805 accumulate-only constraint.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-UU Pattern-2 explicit-absence: cmd_replay.py:260-282 state-"
        "classification has only 'incomplete_run' / 'ok' branches with no "
        "third arm for events_count == 0. A completed run with empty "
        "events.jsonl reaches state='ok' indistinguishable from a run "
        "with logged work. Fix: add 'empty_ledger' (or 'no_events_logged') "
        "branch when events_count == 0. Pinned for separate fix wave."
    ),
)
def test_empty_ledger_state_explicit(empty_ledger_project):
    """Pattern-2 explicit-absence: empty ledger discloses dedicated state.

    The completed-but-empty run is an intentionally-absent ledger (agent
    started + ended a run without logging actions). Pattern-2 mandates an
    explicit state token distinguishing it from a real completed run --
    e.g. ``empty_ledger`` / ``no_events_logged`` -- so agents can branch.
    """
    proj, run_id = empty_ledger_project
    runner = CliRunner()
    result = invoke_cli(runner, ["replay", run_id], cwd=proj, json_mode=True)
    env = _parse_envelope(result)
    state = env.get("summary", {}).get("state")
    assert state in {"empty_ledger", "no_events_logged", "no_events"}, (
        f"empty ledger must disclose dedicated summary.state; got {state!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-UU Pattern-2 silent-SAFE: cmd_replay.py:260-282 reports "
        "partial_success=False on a completed run with 0 events. The run "
        "produced no analytical product (0 actions, 0 gate commands, 0 "
        "verdicts) -- Pattern-2 mandates partial_success=True on degraded "
        "output. Pinned for separate fix wave."
    ),
)
def test_empty_ledger_partial_success_set(empty_ledger_project):
    """Pattern-2 silent-SAFE: empty ledger sets ``partial_success=True``."""
    proj, run_id = empty_ledger_project
    runner = CliRunner()
    result = invoke_cli(runner, ["replay", run_id], cwd=proj, json_mode=True)
    env = _parse_envelope(result)
    assert env["summary"].get("partial_success") is True, (
        f"empty ledger must set summary.partial_success=True (Pattern-2); got summary={env['summary']!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-UU LAW 6 + Pattern-2: cmd_replay.py:271-282 verdict on empty "
        "ledger is 'agent X ran 0 action(s)' -- works standalone but does "
        "NOT name the next action. LAW 6 mandates the verdict carry an "
        "actionable next step. Fix: verdict='replay found 0 events in <id> "
        "-- agent ran but never called roam runs log; run roam runs list'. "
        "Pinned for separate fix wave."
    ),
)
def test_empty_ledger_law6_verdict_actionable(empty_ledger_project):
    """LAW 6: empty-ledger verdict names the next action.

    Compare to the missing-run verdict
    (``"run X not found under .roam/runs/"``) which names location +
    failure mode. The empty-ledger verdict should similarly name the
    cause (agent never logged) + the next command (``roam runs list`` or
    similar).
    """
    proj, run_id = empty_ledger_project
    runner = CliRunner()
    result = invoke_cli(runner, ["replay", run_id], cwd=proj, json_mode=True)
    env = _parse_envelope(result)
    verdict = env["summary"]["verdict"]
    v_lower = verdict.lower()
    # LAW 6: the verdict names a follow-up command or the cause.
    assert "roam " in v_lower or "no events" in v_lower or "empty" in v_lower, (
        f"LAW 6: empty-ledger verdict must name the cause or next command; got {verdict!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-UU CONSTRAINT 12: cmd_replay.py:381-388 next_commands on the "
        "empty-ledger path are ['roam runs show <id>', 'roam agent-score'] "
        "-- both literal 'roam ...' strings (CONSTRAINT 12 satisfied) but "
        "neither helps an agent recover from the empty ledger. The "
        "actionable next step is 'roam runs list' (to confirm) or 'roam "
        "runs start' (to begin a new run with proper logging). Pinned "
        "for separate fix wave."
    ),
)
def test_no_silent_replay_complete_on_empty(empty_ledger_project):
    """Empty-ledger ``next_commands`` includes a recovery action.

    ``roam runs show <id>`` repeats the same empty information; ``roam
    agent-score`` is unrelated. On an empty ledger the agent needs to
    either (a) verify the directory state via ``roam runs list`` or
    (b) start a fresh logged run.
    """
    proj, run_id = empty_ledger_project
    runner = CliRunner()
    result = invoke_cli(runner, ["replay", run_id], cwd=proj, json_mode=True)
    env = _parse_envelope(result)
    next_cmds = env.get("agent_contract", {}).get("next_commands") or []
    assert next_cmds, f"next_commands must be populated; got {next_cmds!r}"
    # All literal 'roam ...' (CONSTRAINT 12 -- shape check).
    for nc in next_cmds:
        assert nc.startswith("roam "), f"CONSTRAINT 12: next_command must be literal 'roam ...'; got {nc!r}"
    # Executability check: a recovery action is named (not just show/score).
    joined = " ".join(next_cmds).lower()
    assert "runs list" in joined or "runs start" in joined or "runs log" in joined, (
        f"empty-ledger next_commands must name a recovery action (runs list / runs start / runs log); got {next_cmds!r}"
    )
