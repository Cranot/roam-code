"""W805-EEE: Empty-corpus Pattern-2 smoke test on ``cmd_agent_score``.

Fifty-seventh-in-batch W805 Pattern-2 audit sweep. ``roam agent-score``
is the R20-phase-3 composite scorer that aggregates per-agent runs from
``.roam/runs/`` into a 0..100 score with three components: completion
rate (70 pts), clean-signal rate (20 pts), breadth factor (10 pts).

Per CLAUDE.md ("score the agent on 0..100 composite") this command is
shaped like the W805 aggregator-family candidates (cmd_health,
cmd_doctor, cmd_dashboard) -- a multi-signal composite scorer that
synthesises several upstream signals into a single numeric verdict band.
The natural Pattern-2 prediction is silent ``100/100`` on an empty
corpus, mirroring the verdict-band silent-SAFE shape pinned by W805-PP
(cmd_dashboard) and W805-833 (cmd_health).

W978 first-hypothesis re-run BEFORE writing any test
============================================================

Hypothesis: "composite 0..100 scorer + missing inputs = silent 100/100
SAFE -- verdict-band axis peer of W805-PP / W805-833".

Empirical probe (empty corpus, no ``.roam/runs/`` directory):

    $ mkdir empty-score-test && cd empty-score-test
    $ git init && touch app.py && git add . && git commit -m init
    $ python -m roam --json agent-score
    {
      "summary": {
        "verdict": "no runs yet -- run 'roam runs start --agent NAME' to open one",
        "partial_success": false,
        "state": "no_data",
        "agents_scored": 0,
        "next_commands": ["roam runs start --agent <name>"]
      },
      "agents": [],
      "facts_extra": ["no .roam/runs/ directory exists yet"],
      ...
    }

**Hypothesis disconfirmed.** ``cmd_agent_score`` is a CLEAN
state-reader counter-example to the W805 aggregator family. The W978
re-run shows the explicit empty-state branch at
``cmd_agent_score.py:174-196`` already names the absent-data axis loudly:

  * ``state: "no_data"`` explicit (not "ok", not absent)
  * ``verdict: "no runs yet -- run 'roam runs start --agent NAME' to
    open one"`` -- names the absent state AND embeds the literal
    next-action command per CONSTRAINT 12
  * ``agents_scored: 0`` quantifies the absence
  * No silent ``100/100`` score band -- the verdict-band lookup is
    SHORT-CIRCUITED before any scoring math runs
  * ``next_commands: ["roam runs start --agent <name>"]`` literal
    copy-pasteable per CONSTRAINT 12
  * ``facts_extra: ["no .roam/runs/ directory exists yet"]`` -- names
    the missing producer (not the consumer) per LAW-4 anchor

The second empty-state branch at ``cmd_agent_score.py:200-222`` covers
the "runs dir exists but no matching runs" axis with the same shape
(``state: "no_data"`` + verdict "no runs match the given filters" +
``next_commands: ["roam runs list"]``).

**Why ``partial_success: false`` is correct here:**

Per CLAUDE.md "Make fallback chains loud" + "Distinguish intentional
absence from broken absence":

  * ``state: "no_data"`` already names the absent-data axis explicitly
  * No degraded computation occurred -- the scoring math never runs
    because the input set is empty
  * No fallback chain was invoked -- the early return is the documented
    happy path for the empty-input branch
  * Compare to cmd_dashboard (W805-PP): there the scoring math DOES
    run on the empty graph (collect_metrics returns 100 because no
    cycles can exist on 0 nodes), and the verdict-band lookup proceeds
    to emit "HEALTHY 100/100" -- THAT is the Pattern-2 bug. cmd_agent_score
    short-circuits BEFORE the score is computed, so there is no silent
    SAFE-band to disclose.

**Why this is NOT a verdict-band axis peer of W805-PP / W805-833:**

cmd_dashboard / cmd_health compose their verdict from a numeric
band-lookup on the health score: ``_health_label(100) -> "HEALTHY"``.
That lookup is band-driven and silent-on-empty -- the bug class.

cmd_agent_score never reaches its verdict-band logic on empty input:
the early return at line 174 fires BEFORE any score is computed. The
scored verdict (line 338-352) only runs when ``agents_scored >= 1``.
The empty-corpus verdict is a hand-written string naming the absent
state, not a band-lookup.

**Why this is NOT an aggregator-family peer of cmd_compound recipes:**

cmd_agent_score does NOT delegate to ``_compound_envelope`` (the MCP
aggregator behind ``for_bug_fix``/``for_refactor``/``pr_prep``). It
reads ``.roam/runs/`` directly via ``list_runs`` + ``read_run_events``
from ``roam.runs.ledger``. It is a single-process direct CLI command
that composes its own envelope, not a multi-subcommand orchestrator.

LAW 6 check
-----------

Both empty-state verdicts work without any other field:

  * "no runs yet -- run 'roam runs start --agent NAME' to open one"
  * "no runs match the given filters"

Single-line, standalone-readable, names the absent state explicitly,
and the first one embeds the next-action command literally.

Conclusion: cmd_agent_score is the FOURTH catalogued clean state-reader
counter-example to the W805 aggregator family. NO xfail-strict pin
because there is NO bug. This module pins the desirable state-reader
shape as a regression invariant.

W805 sweep yield (incl. this entry):

  * Aggregator-family bugs confirmed: SIX (W805-F/KK/LL/OO/RR/TT share
    ``_compound_envelope`` root) + standalone aggregators (cmd_dogfood,
    cmd_audit, cmd_brief, cmd_dashboard, cmd_health, cmd_doctor).
  * State-reader clean counter-examples: FOUR
    (cmd_next/W805-VV + cmd_intent_check/W805-YY + cmd_mode/W805-AAA +
    cmd_agent_score/W805-EEE).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, invoke_cli, parse_json_output  # noqa: E402

# ---------------------------------------------------------------------------
# Existence guard (BAIL-if-absent shape per W978 + W907)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``cmd_agent_score`` module + ``agent_score_cmd`` Click command resolve."""
    try:
        from roam.commands import cmd_agent_score
    except ImportError as exc:  # pragma: no cover - guarded environments only
        pytest.skip(f"roam.commands.cmd_agent_score import failed: {exc!r}")
    assert hasattr(cmd_agent_score, "agent_score_cmd"), "roam.commands.cmd_agent_score.agent_score_cmd missing"
    assert callable(cmd_agent_score.agent_score_cmd), "agent_score_cmd is not a callable"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_runs_project(tmp_path):
    """Bare git project with NO ``.roam/runs/`` directory.

    Triggers the line-174 early-return branch in cmd_agent_score where
    ``rroot.exists()`` is False -- the cleanest empty-corpus axis.
    """
    proj = tmp_path / "empty-score-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    git_init(proj)
    return proj


@pytest.fixture
def runs_dir_exists_but_empty(tmp_path):
    """Project with ``.roam/runs/`` directory but no run sub-folders.

    Triggers the line-200 early-return branch where ``rroot.exists()`` is
    True but ``list_runs`` yields nothing -- the second empty-corpus axis.
    """
    proj = tmp_path / "empty-runs-dir-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (proj / ".roam" / "runs").mkdir(parents=True)
    git_init(proj)
    return proj


@pytest.fixture
def clean_runs_corpus(tmp_path):
    """Project with one hand-seeded completed run.

    W978 negative-control: confirms the empty-corpus state guards are
    empty-corpus-specific, NOT class-wide cmd_agent_score defects. A real
    run on disk should produce a real numeric score in the envelope.
    """
    proj = tmp_path / "clean-score-proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    rdir = proj / ".roam" / "runs" / "run-abc123"
    rdir.mkdir(parents=True)
    (rdir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": "run-abc123",
                "agent": "claude-code",
                "started_at": "2026-05-13T08:14:33Z",
                "ended_at": "2026-05-13T08:15:03Z",
                "status": "completed",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    events = [
        {"seq": 1, "ts": "2026-05-13T08:14:34Z", "action": "preflight"},
        {"seq": 2, "ts": "2026-05-13T08:14:40Z", "action": "impact"},
        {"seq": 3, "ts": "2026-05-13T08:14:50Z", "action": "diff"},
    ]
    events_text = "".join(json.dumps(ev, sort_keys=True) + "\n" for ev in events)
    (rdir / "events.jsonl").write_text(events_text, encoding="utf-8")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# SMOKE (always-on) -- Pattern-1A regression baseline + state-reader shape
# ---------------------------------------------------------------------------


class TestAgentScoreEmptyCorpusSmoke:
    """Pattern-1A always-emit envelope + LAW-6 standalone verdict on the
    no-runs-directory axis (line-174 early-return branch).
    """

    def test_empty_corpus_no_crash(self, empty_runs_project, cli_runner):
        """agent-score must return a structured envelope on no-runs-dir corpus."""
        result = invoke_cli(cli_runner, ["agent-score"], cwd=empty_runs_project, json_mode=True)
        assert result.exit_code == 0, f"failed: {result.output}"
        data = parse_json_output(result, command="agent-score")
        assert isinstance(data, dict), f"expected dict, got {type(data).__name__}"

    def test_empty_corpus_envelope_has_verdict(self, empty_runs_project, cli_runner):
        """``summary.verdict`` is a non-empty string."""
        result = invoke_cli(cli_runner, ["agent-score"], cwd=empty_runs_project, json_mode=True)
        data = parse_json_output(result, command="agent-score")
        summary = data.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty string; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_runs_project, cli_runner):
        """Envelope identifies itself as ``agent-score``."""
        result = invoke_cli(cli_runner, ["agent-score"], cwd=empty_runs_project, json_mode=True)
        data = parse_json_output(result, command="agent-score")
        assert data.get("command") == "agent-score", data.get("command")

    def test_empty_corpus_state_explicit(self, empty_runs_project, cli_runner):
        """``summary.state`` names the empty-corpus axis explicitly.

        cmd_agent_score sets ``state: "no_data"`` on the line-174 branch;
        this regression-pins that disclosure rather than letting it drift
        to ``"ok"`` (which would be the silent-SAFE Pattern-2 shape).
        """
        result = invoke_cli(cli_runner, ["agent-score"], cwd=empty_runs_project, json_mode=True)
        data = parse_json_output(result, command="agent-score")
        s = data["summary"]
        state = s.get("state")
        assert state is not None, (
            f"summary.state missing on empty corpus; expected explicit 'no_data' state. keys={list(s.keys())}"
        )
        assert isinstance(state, str) and state, f"state={state!r} not a string"
        lowered = state.lower()
        assert any(token in lowered for token in ("no_data", "no data", "empty", "not_initialized", "uninitialized")), (
            f"summary.state={state!r} does not name the empty-corpus axis"
        )

    def test_empty_corpus_partial_success_set(self, empty_runs_project, cli_runner):
        """``summary.partial_success`` is present + boolean on empty corpus.

        cmd_agent_score sets ``partial_success: False`` here -- which is
        CORRECT per CLAUDE.md "Distinguish intentional absence from
        broken absence" because ``state: "no_data"`` already names the
        absent-data axis explicitly + no degraded computation ran. We
        regression-pin presence + bool-ness, not the False value, so a
        future tightening to True (if rationale emerges) does not break.
        """
        result = invoke_cli(cli_runner, ["agent-score"], cwd=empty_runs_project, json_mode=True)
        data = parse_json_output(result, command="agent-score")
        s = data["summary"]
        assert "partial_success" in s, f"summary.partial_success key missing on empty corpus; keys={list(s.keys())}"
        assert isinstance(s["partial_success"], bool), f"partial_success={s['partial_success']!r} is not bool"

    def test_empty_corpus_law6_verdict_standalone(self, empty_runs_project, cli_runner):
        """LAW 6: verdict is single-line, standalone-readable.

        Empty-corpus verdict must work without any other field.
        cmd_agent_score emits "no runs yet -- run 'roam runs start
        --agent NAME' to open one" which names the absent state AND
        embeds the next-action command literally.
        """
        result = invoke_cli(cli_runner, ["agent-score"], cwd=empty_runs_project, json_mode=True)
        data = parse_json_output(result, command="agent-score")
        verdict = data["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert len(verdict) > 10, f"verdict too short to be informative: {verdict!r}"
        # Names the absent state explicitly: rejects silent SAFE/100 verdict.
        lowered = verdict.lower()
        assert any(token in lowered for token in ("no runs", "no data", "no agent", "empty", "not_initialized")), (
            f"verdict={verdict!r} does not name the empty-state axis -- possible silent-SAFE Pattern-2 regression"
        )

    def test_empty_corpus_agents_scored_zero(self, empty_runs_project, cli_runner):
        """``summary.agents_scored`` quantifies the absence as 0.

        Regression-pins the numeric disclosure of the empty-corpus axis
        -- a value of 0 means the consumer can branch on the count
        without parsing the verdict string.
        """
        result = invoke_cli(cli_runner, ["agent-score"], cwd=empty_runs_project, json_mode=True)
        data = parse_json_output(result, command="agent-score")
        s = data["summary"]
        assert s.get("agents_scored") == 0, f"expected agents_scored=0 on empty corpus; got {s.get('agents_scored')!r}"
        # And the agents[] array is empty (machine-readable disclosure axis).
        assert data.get("agents") == [], f"expected agents=[] on empty corpus; got {data.get('agents')!r}"


# ---------------------------------------------------------------------------
# No-matching-runs branch (line 200) -- runs dir exists but is empty
# ---------------------------------------------------------------------------


class TestAgentScoreNoMatchingRuns:
    """Pin the SECOND empty-state branch in cmd_agent_score (line 200)
    where ``.roam/runs/`` exists but ``list_runs`` returns nothing.
    """

    def test_runs_dir_empty_emits_no_data(self, runs_dir_exists_but_empty, cli_runner):
        """Empty runs directory -> same ``state: "no_data"`` disclosure."""
        result = invoke_cli(
            cli_runner,
            ["agent-score"],
            cwd=runs_dir_exists_but_empty,
            json_mode=True,
        )
        data = parse_json_output(result, command="agent-score")
        s = data["summary"]
        state = (s.get("state") or "").lower()
        assert "no_data" in state or "no data" in state or "empty" in state, (
            f"summary.state={s.get('state')!r} does not name the empty-state axis"
        )
        assert s.get("agents_scored") == 0

    def test_runs_dir_empty_verdict_names_absence(self, runs_dir_exists_but_empty, cli_runner):
        """Verdict for runs-dir-empty branch still names the absent state."""
        result = invoke_cli(
            cli_runner,
            ["agent-score"],
            cwd=runs_dir_exists_but_empty,
            json_mode=True,
        )
        data = parse_json_output(result, command="agent-score")
        verdict = data["summary"]["verdict"].lower()
        assert any(token in verdict for token in ("no runs", "no data", "empty", "match")), (
            f"verdict does not name absent state: {data['summary']['verdict']!r}"
        )


# ---------------------------------------------------------------------------
# Anti-aggregator-family checks: confirms cmd_agent_score is NOT a peer
# of cmd_dashboard / cmd_health / _compound_envelope
# ---------------------------------------------------------------------------


class TestAgentScoreNotVerdictBandPeer:
    """Confirms cmd_agent_score is NOT a verdict-band silent-SAFE peer.

    Unlike W805-PP (cmd_dashboard) which emits "HEALTHY 100/100" on
    empty corpus, cmd_agent_score MUST NOT emit a silent "100/100" or
    "SAFE" verdict-band response on empty input -- the early return at
    line 174 should fire before any score is computed.
    """

    def test_no_silent_full_score_on_empty(self, empty_runs_project, cli_runner):
        """Empty corpus verdict MUST NOT silently read "100/100" / "SAFE".

        This is the verdict-band axis peer test of W805-PP / W805-833.
        cmd_agent_score is CLEAN here because the early return fires
        before any score is computed -- the verdict instead names the
        absent state explicitly.
        """
        result = invoke_cli(cli_runner, ["agent-score"], cwd=empty_runs_project, json_mode=True)
        data = parse_json_output(result, command="agent-score")
        verdict = data["summary"]["verdict"]
        # The silent-SAFE shape we are guarding against: a numeric score
        # band (100/100 / 100.0/100 / HEALTHY / SAFE) on a corpus with
        # no input data.
        lowered = verdict.lower()
        silent_safe_tokens = ("100/100", "100.0/100", "healthy", "safe", "all green")
        # The empty-corpus verdict explicitly mentions "no runs" / "no data"
        # / "empty" -- so even if a future verdict embedded a score-shaped
        # phrase, the absent-state phrase must also be present.
        has_absent_state_phrase = any(token in lowered for token in ("no runs", "no data", "empty", "no agent"))
        if any(token in lowered for token in silent_safe_tokens):
            assert has_absent_state_phrase, (
                f"verdict={verdict!r} contains silent-SAFE phrase without "
                f"naming the absent-state axis -- possible Pattern-2 "
                f"verdict-band silent-SAFE regression"
            )

    def test_no_score_band_when_no_agents_scored(self, empty_runs_project, cli_runner):
        """``summary`` MUST NOT include a numeric score when nothing was scored.

        Pinning the "no scoring math ran" invariant -- the early return
        at line 174 should keep the score-band fields out of the
        summary on empty corpus.
        """
        result = invoke_cli(cli_runner, ["agent-score"], cwd=empty_runs_project, json_mode=True)
        data = parse_json_output(result, command="agent-score")
        s = data["summary"]
        # ``agents_scored`` is the count, that's allowed. But there is
        # no aggregate "score" summary field, no "completion_rate" /
        # "clean_signal_rate" / "breadth_factor" at summary-level on
        # empty corpus -- those only appear per-agent inside agents[].
        for forbidden in ("score", "overall_score", "completion_rate", "clean_signal_rate"):
            assert forbidden not in s, (
                f"summary contains {forbidden!r} on empty corpus; early-return branch should not emit score-band fields"
            )

    def test_aggregator_propagates_partial_success(self, runs_dir_exists_but_empty, cli_runner):
        """If cmd_agent_score WERE a _compound_envelope peer, ANY
        subcommand failure would set partial_success=True.

        cmd_agent_score is NOT a _compound_envelope peer -- it composes
        its own envelope directly. This test pins the standalone
        envelope shape: on a degraded path (runs dir empty), the
        envelope keys are the direct fields (state, agents_scored,
        verdict), NOT a compound subcommand failure list.
        """
        result = invoke_cli(
            cli_runner,
            ["agent-score"],
            cwd=runs_dir_exists_but_empty,
            json_mode=True,
        )
        data = parse_json_output(result, command="agent-score")
        # cmd_agent_score is a direct CLI scorer, not a compound -- so
        # ``failed_subcommands`` / ``compound_subcommand_results`` MUST
        # NOT be in the envelope.
        for compound_field in (
            "failed_subcommands",
            "compound_subcommand_results",
            "subcommand_results",
        ):
            assert compound_field not in data, (
                f"envelope contains {compound_field!r}; cmd_agent_score should not delegate to _compound_envelope"
            )
            assert compound_field not in (data.get("summary") or {}), (
                f"summary contains {compound_field!r}; cmd_agent_score should not delegate to _compound_envelope"
            )


# ---------------------------------------------------------------------------
# Clean-corpus positive baseline (W978 negative control)
# ---------------------------------------------------------------------------


class TestAgentScoreCleanCorpusBaseline:
    """Real runs on disk -> real numeric score in envelope.

    W978 negative control: confirms the empty-corpus pins above are
    empty-corpus-specific, NOT class-wide cmd_agent_score defects.
    """

    def test_clean_corpus_emits_real_score(self, clean_runs_corpus, cli_runner):
        """Real seeded run -> real envelope with scored agent + numeric score."""
        result = invoke_cli(cli_runner, ["agent-score"], cwd=clean_runs_corpus, json_mode=True)
        data = parse_json_output(result, command="agent-score")
        assert data.get("command") == "agent-score"
        s = data["summary"]
        # State should be "ok" (or at least not "no_data") since we
        # have a real run on disk.
        assert s.get("state") != "no_data", f"clean corpus state={s.get('state')!r}; expected non-no_data"
        assert s.get("agents_scored") == 1, f"clean corpus agents_scored={s.get('agents_scored')!r}; expected 1"
        agents = data.get("agents") or []
        assert len(agents) == 1, f"expected 1 agent in agents[]; got {len(agents)}"
        agent = agents[0]
        assert agent.get("agent") == "claude-code", agent
        # Single completed run with no partials -> score >= 70 (completion-only).
        assert isinstance(agent.get("score"), (int, float)), f"score={agent.get('score')!r} is not numeric"
        assert agent["score"] >= 70, f"score={agent['score']}; expected >= 70"
        # The score-band fields appear per-agent (NOT at summary-level).
        assert "score_components" in agent, agent
        assert "completion_rate" in agent["score_components"], agent["score_components"]

    def test_clean_corpus_verdict_names_agent_and_score(self, clean_runs_corpus, cli_runner):
        """Verdict on a single-agent clean corpus names agent + score band."""
        result = invoke_cli(cli_runner, ["agent-score"], cwd=clean_runs_corpus, json_mode=True)
        data = parse_json_output(result, command="agent-score")
        verdict = data["summary"]["verdict"]
        assert "claude-code" in verdict, f"verdict does not name the agent: {verdict!r}"
        assert "/100" in verdict, f"verdict does not name the score band: {verdict!r}"
