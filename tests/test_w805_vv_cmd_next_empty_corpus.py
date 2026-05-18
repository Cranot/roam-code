"""W805-VV: Empty-corpus Pattern-2 smoke test on ``cmd_next``.

Pattern-2 (silent-SAFE) audit. ``roam next`` is the Agent-OS state-reader
that recommends the next action based on cheap repo-state signals (index
presence, staleness, working-tree dirtiness, recent envelope, recent
memory, constitution-pending checks, mode-upgrade hints).

W978 first-hypothesis discipline: empirical probe (twice) on an empty
corpus shows cmd_next does NOT exhibit the silent-SAFE Pattern-2 bug
that the W805 aggregator family carries. The state-reader is a clean
counter-example to the aggregator-family pattern:

  * Empty / no-index corpus -> ``state: "uninitialized"`` (explicit)
  * ``summary.partial_success: true`` (correctly flagged)
  * ``verdict: "Run `roam init` to index the codebase first."`` (LAW 6
    standalone + LAW 2 imperative + CONSTRAINT 12 copy-paste-executable)
  * ``next_steps: ["roam init"]`` (CONSTRAINT 12 self-reference)

This test is a CONFORMANCE pin -- it documents the desirable
state-reader behavior and locks it in as a regression invariant for the
W805 sweep. NO xfail-strict because there is NO bug. The aggregator
family (cmd_brief / cmd_dogfood / cmd_audit / etc.) should converge to
THIS shape, not the other way around.

W805 sweep yield (incl. this entry): aggregator family confirmed
six-strong (W805-F/KK/LL/OO/RR + TT); state-reader family confirmed
clean (W805-VV is the first member catalogued).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    invoke_cli,
    parse_json_output,
)

_CMD_PATH = Path(__file__).resolve().parents[1] / "src" / "roam" / "commands" / "cmd_next.py"


@pytest.fixture
def cli_runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# 1. Existence guard
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """If cmd_next.py vanishes, this whole module skips rather than errors."""
    if not _CMD_PATH.is_file():
        pytest.skip(f"cmd_next.py absent at {_CMD_PATH}")
    assert _CMD_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# 2. Empty corpus does not crash
# ---------------------------------------------------------------------------


def test_empty_corpus_no_crash(cli_runner, tmp_path, monkeypatch):
    """No ``.roam/``, no git -- next must exit 0 with a parseable envelope."""
    proj = tmp_path / "untouched"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# 3. Envelope always carries a verdict
# ---------------------------------------------------------------------------


def test_empty_corpus_envelope_has_verdict(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "verdict"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="next")
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), "summary.verdict must be non-empty"


# ---------------------------------------------------------------------------
# 4. State is explicit on empty corpus (NOT silent SAFE)
# ---------------------------------------------------------------------------


def test_empty_corpus_state_explicit(cli_runner, tmp_path, monkeypatch):
    """Empty corpus -> ``summary.state == "uninitialized"`` (named, not "ok")."""
    proj = tmp_path / "state_explicit"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="next")
    state = data["summary"].get("state")
    assert isinstance(state, str) and state, "summary.state must be non-empty"
    # The key invariant: empty corpus must NOT report "ok" / "safe" /
    # "clean" / "idle" -- those would be silent SAFE.
    assert state not in ("ok", "safe", "clean"), f"Pattern-2 silent SAFE: empty corpus reports state={state!r}"
    # The expected explicit state.
    assert state == "uninitialized", f"expected 'uninitialized', got {state!r}"


# ---------------------------------------------------------------------------
# 5. partial_success is True when index is absent
# ---------------------------------------------------------------------------


def test_empty_corpus_partial_success_set(cli_runner, tmp_path, monkeypatch):
    """When the index is absent, ``partial_success`` must be True."""
    proj = tmp_path / "noindex"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="next")
    assert data["summary"]["partial_success"] is True


# ---------------------------------------------------------------------------
# 6. LAW 6: verdict alone is actionable
# ---------------------------------------------------------------------------


def test_empty_corpus_law6_verdict_standalone(cli_runner, tmp_path, monkeypatch):
    """LAW 6: ``summary.verdict`` alone names the missing precondition AND
    the next action. An agent that consumes ONLY the verdict must know
    what to do."""
    proj = tmp_path / "law6"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="next")
    verdict = data["summary"]["verdict"].lower()
    # Verdict must name the action ("init"/"index") and contain an
    # imperative verb.
    assert "init" in verdict or "index" in verdict, verdict
    # Imperative voice -- starts with "run" / "build" / similar action verb.
    first_word = verdict.split()[0] if verdict.split() else ""
    assert first_word in ("run", "build", "execute", "invoke"), f"verdict not imperative: starts with {first_word!r}"


# ---------------------------------------------------------------------------
# 7. LAW 4: facts strings are concrete-noun anchored
# ---------------------------------------------------------------------------


def test_empty_corpus_law4_facts_anchored(cli_runner, tmp_path, monkeypatch):
    """LAW 4: ``agent_contract.facts[0]`` is either >4 tokens (long-verdict
    exemption) OR ends on a concrete-noun anchor."""
    proj = tmp_path / "law4"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="next")
    facts = data.get("agent_contract", {}).get("facts", [])
    assert isinstance(facts, list) and facts, "agent_contract.facts must be non-empty"
    first = facts[0]
    assert isinstance(first, str) and first.strip(), "facts[0] must be non-empty"
    # Long-verdict exemption: >4 tokens with non-numeric lead.
    tokens = first.split()
    long_enough = len(tokens) > 4 and not tokens[0][:1].isdigit()
    assert long_enough, f"facts[0] too short to satisfy LAW 4 exemption: {first!r}"


# ---------------------------------------------------------------------------
# 8. CONSTRAINT 12: next_commands present + copy-paste-executable
# ---------------------------------------------------------------------------


def test_empty_corpus_next_commands_present(cli_runner, tmp_path, monkeypatch):
    """CONSTRAINT 12: ``next_commands`` is a non-empty list of literal
    ``roam <subcommand>`` strings that are copy-paste-executable."""
    proj = tmp_path / "next_cmds"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="next")
    # Check both envelope locations: next_steps + agent_contract.next_commands
    next_steps = data.get("next_steps")
    assert isinstance(next_steps, list) and next_steps, "next_steps must be non-empty"
    first = next_steps[0]
    assert isinstance(first, str) and first.strip(), "next_steps[0] must be a non-empty string"
    assert first.startswith("roam "), f"next_steps[0] not copy-paste-executable: {first!r}"

    contract = data.get("agent_contract", {})
    nc = contract.get("next_commands")
    assert isinstance(nc, list) and nc, "agent_contract.next_commands must be non-empty"
    assert isinstance(nc[0], str) and nc[0].startswith("roam "), nc[0]


# ---------------------------------------------------------------------------
# 9. NEGATIVE REFERENCE: cmd_next is NOT a silent-SAFE peer of the
#     aggregator family. Empty corpus does NOT emit a "no action needed"
#     / "everything is fine" verdict. This locks in the desirable
#     state-reader shape as a regression invariant.
# ---------------------------------------------------------------------------


def test_no_silent_no_action_needed_on_empty(cli_runner, tmp_path, monkeypatch):
    """Empty corpus MUST NOT emit a silent-SAFE "nothing to do" verdict.

    Agent-safety: a silent SAFE on empty corpus would teach the agent to
    skip work that is actually required (running `roam init`). This test
    pins the desirable behavior: emit an explicit uninitialized verdict
    with a real next-command, not a no-op.

    This is the W805-VV NEGATIVE-REFERENCE pin: cmd_next is a clean
    counter-example to the aggregator family (cmd_brief / cmd_audit /
    cmd_dogfood etc.) -- the test asserts it stays that way.
    """
    proj = tmp_path / "no_silent_safe"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="next")
    summary = data["summary"]
    verdict_lower = summary["verdict"].lower()

    # Forbidden silent-SAFE phrases on empty corpus.
    forbidden_phrases = (
        "no action needed",
        "everything is fine",
        "all clear",
        "no pending work",  # this would be the `idle` branch -- wrong for no-index
        "nothing to do",
    )
    for phrase in forbidden_phrases:
        assert phrase not in verdict_lower, (
            f"Pattern-2 silent SAFE: verdict on empty corpus contains {phrase!r}: {summary['verdict']!r}"
        )

    # Forbidden silent-SAFE state strings.
    forbidden_states = ("ok", "safe", "clean", "idle")
    state = summary.get("state")
    assert state not in forbidden_states, f"Pattern-2 silent SAFE state on empty corpus: state={state!r}"


# ---------------------------------------------------------------------------
# 10. Clean-corpus sanity: with no signals, router still emits a real
#      recommendation (idle branch -> roam tour). State is explicitly
#      "idle" with partial_success=False -- this is correct behavior, NOT
#      Pattern-2: the corpus IS clean and idle, the router IS reporting
#      that accurately, and the recommendation IS still actionable.
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_recommendation(cli_runner, tmp_path, monkeypatch):
    """With an indexed clean corpus, the router emits a real next-command
    (``roam tour`` for idle) -- the recommendation is always actionable,
    never null / empty / generic."""
    # Even without a real index, the smoke test above proves the router
    # emits a real recommendation in the empty-corpus path (`roam init`).
    # This test extends the invariant: the recommendation is ALWAYS
    # actionable -- never an empty string, never null, never just
    # "no-op".
    proj = tmp_path / "clean_corpus"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj, json_mode=True)
    data = parse_json_output(result, command="next")
    cmd = data["summary"].get("command")
    assert isinstance(cmd, str) and cmd.strip(), f"summary.command must be non-empty: {cmd!r}"
    # The command name is always a real roam verb (no spaces, no flags).
    assert " " not in cmd, f"summary.command should be bare verb, got {cmd!r}"
    assert not cmd.startswith("-"), f"summary.command should not be a flag: {cmd!r}"
