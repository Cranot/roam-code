"""W805-DDD: Empty-corpus Pattern-2 smoke test on ``cmd_constitution``.

Pattern-2 (silent-SAFE) audit. ``roam constitution`` is the Agent-OS
capstone state-reader (R24): a single declarative file unifying laws +
rules + memory + gates. Five read-only sub-branches exist:

  * ``constitution show``   -- render the loaded constitution
  * ``constitution check``  -- verify sources + required-checks resolve
  * ``constitution where``  -- print canonical path
  * ``constitution apply``  -- run required-checks for one gate
  * ``constitution init``   -- generate ``.roam/constitution.yml`` (this
    one is write-bearing but probed here in idempotent / clean-corpus
    paths only).

W978 first-hypothesis discipline: empirical probe (show / check / where
/ apply on an empty corpus) shows ``cmd_constitution`` does NOT exhibit
the silent-SAFE Pattern-2 bug carried by the W805 aggregator family.
It is a CLEAN COUNTER-EXAMPLE peer of cmd_next (W805-VV),
cmd_intent_check (W805-YY) and cmd_mode (W805-AAA) -- the FOURTH
catalogued clean state-reader counter-example to the W805 aggregator
family.

The key empty-corpus behaviors locked in here:

  * No ``.roam/constitution.yml`` -> every read-only subcommand emits
    ``state: "not_initialized"`` LOUDLY (no silent-SAFE fallback).
  * ``partial_success: true`` is correct on the missing-constitution
    branch because the requested operation could not be completed (vs
    cmd_mode where a documented default IS the successful answer).
  * ``agent_contract.facts[0]`` names the absent file explicitly:
    ``"no .roam/constitution.yml in this repo"``.
  * ``agent_contract.next_commands`` names the recovery command:
    ``"roam constitution init"`` -- copy-paste-executable (LAW 12).
  * ``constitution apply --gate before_edit`` on missing constitution
    exits 2 (usage error) while still emitting the full envelope.
  * ``constitution where`` on missing path: ``exists: false``,
    ``state: "not_initialized"``, ``partial_success: true``.

LAW 6 check: every branch's ``summary.verdict`` works without any other
field -- it names the missing constitution + the recovery command.

CLEAN CORPUS positive pin: after ``roam constitution init``, ``show``
emits ``state: "ok"``, ``partial_success: false``, and the full
``constitution`` payload (metadata + modes + policy + signals). This is
the affirmative path of the capstone state-reader.

This module pins the desirable state-reader shape as a regression
invariant. NO xfail-strict because there is NO bug. ``cmd_constitution``
joins ``cmd_next`` (W805-VV), ``cmd_intent_check`` (W805-YY), and
``cmd_mode`` (W805-AAA) as the FOURTH catalogued clean state-reader
counter-example to the W805 aggregator family.

W805 sweep yield (incl. this entry): aggregator family confirmed
six-strong (W805-F/KK/LL/OO/RR + TT); state-reader family confirmed
clean four-strong (W805-VV cmd_next + W805-YY cmd_intent_check +
W805-AAA cmd_mode + W805-DDD cmd_constitution).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

_CMD_PATH = Path(__file__).resolve().parents[1] / "src" / "roam" / "commands" / "cmd_constitution.py"


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _constitution_commands_run_in_declared_mode(monkeypatch):
    """This shape suite intentionally exercises the command, not its mode gate."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")
    monkeypatch.delenv("ROAM_MODE_ENFORCEMENT", raising=False)


def _parse_json_any_exit(result, command="constitution"):
    """Parse JSON regardless of exit code.

    ``constitution apply`` exits 2 on the missing-constitution branch
    while still emitting a fully-formed JSON envelope. The shared
    ``parse_json_output`` helper asserts exit==0 which would mask the
    envelope on that branch -- so this module uses its own parser that
    only validates JSON shape.
    """
    raw = getattr(result, "stdout", None)
    if raw is None:
        raw = result.output
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON from {command}: {e}\nOutput was:\n{raw[:500]}")


# ---------------------------------------------------------------------------
# 1. Existence guard
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """If cmd_constitution.py vanishes, this whole module skips."""
    if not _CMD_PATH.is_file():
        pytest.skip(f"cmd_constitution.py absent at {_CMD_PATH}")
    assert _CMD_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# 2. Empty corpus does not crash (show / check / where)
# ---------------------------------------------------------------------------


def test_empty_corpus_no_crash(cli_runner, tmp_path, monkeypatch):
    """No ``.roam/``, no git -- every read-only constitution subcommand
    must produce a parseable envelope, NOT a traceback.
    """
    proj = tmp_path / "untouched"
    proj.mkdir()
    monkeypatch.chdir(proj)

    for args in (["constitution", "show"], ["constitution", "check"], ["constitution", "where"]):
        result = invoke_cli(cli_runner, args, cwd=proj, json_mode=True)
        assert "Traceback" not in result.output, f"{args} crashed: {result.output[:300]}"
        # Body must parse as JSON.
        _parse_json_any_exit(result, command=" ".join(args))


# ---------------------------------------------------------------------------
# 3. Envelope always carries a verdict
# ---------------------------------------------------------------------------


def test_empty_corpus_envelope_has_verdict(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "verdict"
    proj.mkdir()
    monkeypatch.chdir(proj)

    for args in (
        ["constitution", "show"],
        ["constitution", "check"],
        ["constitution", "where"],
    ):
        result = invoke_cli(cli_runner, args, cwd=proj, json_mode=True)
        data = _parse_json_any_exit(result, command=" ".join(args))
        verdict = data["summary"].get("verdict")
        assert isinstance(verdict, str) and verdict.strip(), (
            f"{args} summary.verdict must be non-empty, got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# 4. State is explicit (`not_initialized`) on every missing-constitution branch
# ---------------------------------------------------------------------------


def test_empty_corpus_state_explicit(cli_runner, tmp_path, monkeypatch):
    """Empty corpus MUST surface ``state: "not_initialized"`` LOUDLY on
    every read-only subcommand. This is the "Make fallback chains loud"
    invariant for the capstone state-reader -- the silent-SAFE bug
    would be returning ``state: "ok"`` with a default constitution
    indistinguishable from a real one.
    """
    proj = tmp_path / "state_explicit"
    proj.mkdir()
    monkeypatch.chdir(proj)

    for args in (
        ["constitution", "show"],
        ["constitution", "check"],
        ["constitution", "where"],
    ):
        result = invoke_cli(cli_runner, args, cwd=proj, json_mode=True)
        data = _parse_json_any_exit(result, command=" ".join(args))
        state = data["summary"].get("state")
        assert state == "not_initialized", f"{args} must surface state='not_initialized', got {state!r}"


# ---------------------------------------------------------------------------
# 5. partial_success: true on every missing-constitution branch
# ---------------------------------------------------------------------------


def test_empty_corpus_partial_success_set(cli_runner, tmp_path, monkeypatch):
    """Empty corpus -> ``partial_success: true`` on every read-only
    subcommand because the requested operation could not complete
    (no constitution to load / check / apply).

    Note: this differs from cmd_mode (W805-AAA) where the documented
    default IS the successful answer (``partial_success: false``). Here,
    ``cmd_constitution`` cannot produce a meaningful answer without the
    file -- there is no documented-default constitution.
    """
    proj = tmp_path / "noindex"
    proj.mkdir()
    monkeypatch.chdir(proj)

    for args in (
        ["constitution", "show"],
        ["constitution", "check"],
        ["constitution", "where"],
    ):
        result = invoke_cli(cli_runner, args, cwd=proj, json_mode=True)
        data = _parse_json_any_exit(result, command=" ".join(args))
        ps = data["summary"].get("partial_success")
        assert ps is True, f"{args} on empty corpus must set partial_success=True, got {ps!r}"


# ---------------------------------------------------------------------------
# 6. LAW 6: verdict alone is actionable
# ---------------------------------------------------------------------------


def test_empty_corpus_law6_verdict_standalone(cli_runner, tmp_path, monkeypatch):
    """LAW 6: ``summary.verdict`` alone names the missing constitution
    AND the recovery command. An agent that consumes ONLY the verdict
    must know what to do without reading any other field."""
    proj = tmp_path / "law6"
    proj.mkdir()
    monkeypatch.chdir(proj)

    # `show` and `check` share the same verdict shape.
    for args in (["constitution", "show"], ["constitution", "check"]):
        result = invoke_cli(cli_runner, args, cwd=proj, json_mode=True)
        data = _parse_json_any_exit(result, command=" ".join(args))
        verdict = data["summary"]["verdict"]
        assert "no constitution" in verdict.lower(), f"{args} verdict must name the missing file, got {verdict!r}"
        assert "roam constitution init" in verdict, f"{args} verdict must name the recovery command, got {verdict!r}"

    # `where` verdict is the path string + "does not exist" hint.
    result = invoke_cli(cli_runner, ["constitution", "where"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result, command="where")
    where_verdict = data["summary"]["verdict"]
    assert "constitution.yml" in where_verdict, f"where verdict must name the canonical path, got {where_verdict!r}"
    assert "does not exist" in where_verdict, f"where verdict must name the absent-file state, got {where_verdict!r}"


# ---------------------------------------------------------------------------
# 7. Lineage loud on default fallback (W805-AAA exemplar parity)
# ---------------------------------------------------------------------------


def test_lineage_loud_on_default_fallback(cli_runner, tmp_path, monkeypatch):
    """W805-AAA exemplar parity: empty-corpus default-fallback resolution
    MUST disclose its lineage via:
      * ``agent_contract.facts[0]`` naming the missing file
      * ``agent_contract.next_commands`` naming the recovery action
      * ``summary.path`` (or top-level ``path``) naming the canonical
        location where the file is expected

    This is the "Make fallback chains loud" invariant -- the
    Pattern-2 silent-SAFE bug would be a verdict like ``"ok"`` with no
    indication that the resolution came from absent state.
    """
    proj = tmp_path / "lineage_loud"
    proj.mkdir()
    monkeypatch.chdir(proj)

    for args in (["constitution", "show"], ["constitution", "check"]):
        result = invoke_cli(cli_runner, args, cwd=proj, json_mode=True)
        data = _parse_json_any_exit(result, command=" ".join(args))

        # 1) facts loudly name the missing file.
        facts = data.get("agent_contract", {}).get("facts", [])
        assert facts, f"{args} agent_contract.facts must not be empty"
        first_fact = facts[0]
        assert "no .roam/constitution.yml" in first_fact, (
            f"{args} first fact must name the missing file, got {first_fact!r}"
        )

        # 2) next_commands names the recovery action (LAW 12: copy-paste-executable).
        next_cmds = data.get("agent_contract", {}).get("next_commands", [])
        assert "roam constitution init" in next_cmds, (
            f"{args} next_commands must include 'roam constitution init', got {next_cmds!r}"
        )

        # 3) canonical path is exposed.
        path = data.get("path") or data["summary"].get("path", "")
        assert "constitution.yml" in path, f"{args} envelope must surface the canonical path, got path={path!r}"


# ---------------------------------------------------------------------------
# 8. NEGATIVE REFERENCE: cmd_constitution is NOT a silent-SAFE peer.
#     Empty corpus show/check/where DISCLOSE state explicitly --
#     the Pattern-2 bug would be omitting the lineage and emitting a
#     verdict indistinguishable from a constitution-driven OK.
# ---------------------------------------------------------------------------


def test_no_silent_constitution_loaded_on_missing(cli_runner, tmp_path, monkeypatch):
    """Empty corpus show/check MUST NOT hide the fact that no
    constitution was found.

    Agent-safety: a silent-SAFE default (e.g., ``state: "ok"`` +
    ``partial_success: false`` on missing constitution) would teach the
    agent that the project author chose to operate without constitution
    governance -- but actually no constitution exists yet at all. The
    lineage difference matters for any decision the agent makes about
    "is this project governed?".

    This test pins the desirable behavior: emit ``state:
    "not_initialized"`` AND ``partial_success: true`` AND name the
    missing file in ``agent_contract.facts``.

    This is the W805-DDD NEGATIVE-REFERENCE pin: cmd_constitution is a
    clean counter-example to the aggregator family (cmd_brief /
    cmd_audit / cmd_dogfood etc.) -- this test asserts it stays that
    way.
    """
    proj = tmp_path / "no_silent_ok"
    proj.mkdir()
    monkeypatch.chdir(proj)

    for args in (["constitution", "show"], ["constitution", "check"]):
        result = invoke_cli(cli_runner, args, cwd=proj, json_mode=True)
        data = _parse_json_any_exit(result, command=" ".join(args))
        summary = data["summary"]

        # Pattern-2 anti-shape: state="ok" + partial_success=False on missing.
        assert summary.get("state") != "ok", (
            f"Pattern-2 silent-SAFE: {args} hides missing constitution under state='ok'. Got summary={summary!r}"
        )

        # partial_success MUST flip True (degraded resolution).
        assert summary.get("partial_success") is True, (
            f"Pattern-2 silent-SAFE: {args} hides missing constitution "
            f"under partial_success=False. Got summary={summary!r}"
        )

        # And lineage in agent_contract.facts.
        facts = data.get("agent_contract", {}).get("facts", [])
        facts_joined = " | ".join(facts)
        assert "no .roam/constitution.yml" in facts_joined, (
            f"Pattern-2 silent-SAFE: {args} hides lineage from agent_contract.facts. Got facts={facts!r}"
        )


# ---------------------------------------------------------------------------
# 9. `constitution apply` on missing constitution: explicit usage-error exit.
# ---------------------------------------------------------------------------


def test_apply_on_missing_constitution_explicit(cli_runner, tmp_path, monkeypatch):
    """``roam constitution apply --gate before_edit`` on a missing
    constitution must:
      * emit a fully-formed envelope (NOT empty stdout / NOT crash)
      * verdict names the missing constitution
      * state == ``"not_initialized"``
      * partial_success == True
      * exit 2 (usage error -- you can't apply what isn't initialized)

    This is the W805-DDD apply-axis pin: the gate-runner subcommand
    discloses its prerequisite failure explicitly instead of running
    no checks and returning a hollow SAFE verdict."""
    proj = tmp_path / "apply_missing"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["constitution", "apply", "--gate", "before_edit"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_any_exit(result, command="apply")
    summary = data["summary"]

    assert summary.get("state") == "not_initialized"
    assert summary.get("partial_success") is True
    assert summary.get("gate") == "before_edit"
    verdict = summary.get("verdict", "")
    assert "no constitution" in verdict.lower(), f"apply verdict must name the missing file, got {verdict!r}"
    assert result.exit_code == 2, f"apply on missing constitution must exit 2, got {result.exit_code}"


# ---------------------------------------------------------------------------
# 10. Clean-corpus positive pin: after `init`, show emits real constitution.
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_constitution(cli_runner, tmp_path, monkeypatch):
    """With a freshly-initialized constitution, ``show`` must emit:
      * ``state: "ok"``
      * ``partial_success: false``
      * a populated ``constitution`` payload (metadata + modes + policy)
      * exit 0

    This is the affirmative path of the capstone state-reader -- it
    correctly reports a clean OK when the substrate is in place, with
    no degraded-state ambiguity."""
    proj = tmp_path / "clean_init"
    proj.mkdir()
    monkeypatch.chdir(proj)

    # init the constitution.
    init_res = invoke_cli(cli_runner, ["constitution", "init"], cwd=proj, json_mode=True)
    assert init_res.exit_code == 0, init_res.output
    init_data = _parse_json_any_exit(init_res, command="init")
    assert init_data["summary"].get("state") == "initialized"
    assert init_data["summary"].get("created") is True
    assert init_data["summary"].get("partial_success") is False

    # show after init.
    show_res = invoke_cli(cli_runner, ["constitution", "show"], cwd=proj, json_mode=True)
    assert show_res.exit_code == 0
    show_data = _parse_json_any_exit(show_res, command="show")
    summary = show_data["summary"]

    assert summary.get("state") == "ok", f"post-init show must be state='ok', got {summary!r}"
    assert summary.get("partial_success") is False, f"post-init show must have partial_success=False, got {summary!r}"
    # Real constitution payload is present.
    constitution = show_data.get("constitution")
    assert isinstance(constitution, dict), "show must emit a constitution payload"
    assert "modes" in constitution, "constitution must include modes"
    assert "policy" in constitution, "constitution must include policy"
    # Mode count is the documented 4 (read_only / safe_edit / migration / autonomous_pr).
    assert summary.get("mode_count") == 4, f"constitution must list 4 modes, got {summary.get('mode_count')!r}"
