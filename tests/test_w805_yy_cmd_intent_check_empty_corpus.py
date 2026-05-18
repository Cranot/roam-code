"""W805-YY: Empty-corpus Pattern-2 smoke test on ``cmd_intent_check``.

Pattern-2 (silent-SAFE) audit. ``roam intent-check`` is the Agent-OS
mode-gate state-reader that checks whether an intended command is
allowed under the currently-active mode (R16 policy gate). It is a
no-side-effect query -- the intended command is NEVER executed; only
its allow/block verdict is computed.

W978 first-hypothesis discipline: empirical probe (triple-probe on
allowed / blocked / unknown-command + no-arg) shows cmd_intent_check
does NOT exhibit the silent-SAFE Pattern-2 bug that the W805
aggregator family carries. The state-reader is a CLEAN COUNTER-EXAMPLE
peer of cmd_next (W805-VV):

  * BLOCKED corpus -> verdict starts ``BLOCKED --`` + explicit reason +
    copy-paste-executable mode upgrade hint
  * ALLOWED corpus -> ``partial_success: false`` (correctly clean)
  * BLOCKED corpus -> ``partial_success: true`` (correctly flagged)
  * Unknown command -> explicit ``not in any mode's allow-list`` reason,
    BLOCKED verdict (NOT silent SAFE)
  * No-arg -> ``state: "error"`` + ``partial_success: true`` + exit 2
    (distinct usage-error path, NOT silent SAFE)

This test is a CONFORMANCE pin -- it documents the desirable
mode-gate state-reader behavior and locks it in as a regression
invariant for the W805 sweep. NO xfail-strict because there is NO
bug. cmd_intent_check joins cmd_next (W805-VV) as the SECOND
catalogued clean state-reader counter-example to the W805
aggregator family.

Note on ``state`` field semantics for mode-gate readers: cmd_next
emits ``state: "uninitialized"`` on empty corpus because the index
IS a missing precondition. cmd_intent_check emits ``state: "ok"``
on a BLOCKED verdict because the mode-policy check IS deterministic
and DID complete -- the BLOCKED verdict is the SUCCESSFUL OUTPUT of
a query, not a degraded-execution sentinel. The Pattern-2
anti-pattern would be ``partial_success: false`` + ``allowed: false``
(silent SAFE on a real block). cmd_intent_check correctly stamps
``partial_success: true`` whenever ``allowed: false``.

W805 sweep yield (incl. this entry): aggregator family confirmed
six-strong (W805-F/KK/LL/OO/RR + TT); state-reader family confirmed
clean two-strong (W805-VV cmd_next + W805-YY cmd_intent_check).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

_CMD_PATH = Path(__file__).resolve().parents[1] / "src" / "roam" / "commands" / "cmd_intent_check.py"


@pytest.fixture
def cli_runner():
    return CliRunner()


def _parse_json_any_exit(result, command="intent-check"):
    """Parse JSON regardless of exit code.

    cmd_intent_check exits 5 on BLOCKED and 2 on usage-error, but in
    both cases emits a fully-formed JSON envelope. The shared
    ``parse_json_output`` helper asserts exit==0 which would mask the
    envelope on BLOCKED -- so this module uses its own parser that
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
    """If cmd_intent_check.py vanishes, this whole module skips."""
    if not _CMD_PATH.is_file():
        pytest.skip(f"cmd_intent_check.py absent at {_CMD_PATH}")
    assert _CMD_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# 2. Empty corpus does not crash
# ---------------------------------------------------------------------------


def test_empty_corpus_no_crash(cli_runner, tmp_path, monkeypatch):
    """No ``.roam/``, no git -- intent-check must produce a parseable
    envelope (exit 5 on BLOCKED, NOT a traceback)."""
    proj = tmp_path / "untouched"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["intent-check", "attest"], cwd=proj, json_mode=True)
    # Exit 5 = BLOCKED (gate failure) -- expected for 'attest' in safe_edit.
    assert result.exit_code in (0, 5), result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# 3. Envelope always carries a verdict
# ---------------------------------------------------------------------------


def test_empty_corpus_envelope_has_verdict(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "verdict"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["intent-check", "attest"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), "summary.verdict must be non-empty"


# ---------------------------------------------------------------------------
# 4. State is explicit on BLOCKED verdict (NOT silent SAFE)
# ---------------------------------------------------------------------------


def test_empty_corpus_state_explicit(cli_runner, tmp_path, monkeypatch):
    """Empty corpus + blocked verb -> ``summary.allowed: false`` AND
    ``partial_success: true``. The mode-policy check is deterministic
    so ``state`` is correctly ``"ok"`` (the query completed); the
    silent-SAFE invariant for mode-gate readers is on ``allowed`` /
    ``partial_success``, NOT on ``state``."""
    proj = tmp_path / "state_explicit"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["intent-check", "attest"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]
    # The query completed -- state is "ok".
    assert summary.get("state") == "ok", f"expected state='ok', got {summary.get('state')!r}"
    # The verdict is BLOCKED -- allowed must be False.
    assert summary.get("allowed") is False, "BLOCKED verdict must have allowed=False"
    # The verdict prefix is explicit, NOT silent SAFE.
    verdict = summary.get("verdict", "")
    assert verdict.startswith("BLOCKED"), f"verdict must start with BLOCKED, got {verdict!r}"


# ---------------------------------------------------------------------------
# 5. partial_success is True on BLOCKED
# ---------------------------------------------------------------------------


def test_empty_corpus_partial_success_set(cli_runner, tmp_path, monkeypatch):
    """When the verb is BLOCKED, ``partial_success`` must be True
    (the gate failed, this is the Pattern-2 invariant)."""
    proj = tmp_path / "noindex"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["intent-check", "attest"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    assert data["summary"]["partial_success"] is True


# ---------------------------------------------------------------------------
# 6. LAW 6: verdict alone is actionable
# ---------------------------------------------------------------------------


def test_empty_corpus_law6_verdict_standalone(cli_runner, tmp_path, monkeypatch):
    """LAW 6: ``summary.verdict`` alone names the blocked command AND
    the upgrade path. An agent that consumes ONLY the verdict must
    know what to do."""
    proj = tmp_path / "law6"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["intent-check", "attest"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    verdict = data["summary"]["verdict"]
    # Verdict names the blocked command + the upgrade mode (LAW 6).
    assert "attest" in verdict, verdict
    # And includes a copy-paste-executable hint (CONSTRAINT 12).
    assert "roam mode" in verdict, f"verdict missing upgrade hint: {verdict!r}"


# ---------------------------------------------------------------------------
# 7. CONSTRAINT 12: next_commands present + copy-paste-executable
# ---------------------------------------------------------------------------


def test_empty_corpus_next_commands_present(cli_runner, tmp_path, monkeypatch):
    """CONSTRAINT 12: ``agent_contract.next_commands`` is a non-empty
    list of literal ``roam <subcommand>`` strings on a BLOCKED verb
    with an upgrade path."""
    proj = tmp_path / "next_cmds"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["intent-check", "attest"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    contract = data.get("agent_contract", {})
    nc = contract.get("next_commands")
    assert isinstance(nc, list) and nc, "agent_contract.next_commands must be non-empty on BLOCKED w/ upgrade"
    assert isinstance(nc[0], str) and nc[0].startswith("roam "), nc[0]


# ---------------------------------------------------------------------------
# 8. NEGATIVE REFERENCE: cmd_intent_check is NOT a silent-SAFE peer.
#     Empty corpus + BLOCKED verb does NOT emit a silent ALLOWED /
#     "safe to proceed" verdict. This locks in the desirable
#     mode-gate state-reader shape as a regression invariant.
# ---------------------------------------------------------------------------


def test_no_silent_allowed_on_empty(cli_runner, tmp_path, monkeypatch):
    """Empty corpus + BLOCKED verb MUST NOT emit a silent-SAFE
    ALLOWED verdict.

    Agent-safety: a silent ALLOWED on a mode-gated command would
    teach the agent that the gate is permissive, allowing it to
    proceed with an action the active mode explicitly forbids. This
    test pins the desirable behavior: emit explicit BLOCKED with a
    real upgrade-mode next-command.

    This is the W805-YY NEGATIVE-REFERENCE pin: cmd_intent_check is
    a clean counter-example to the aggregator family (cmd_brief /
    cmd_audit / cmd_dogfood etc.) -- this test asserts it stays that
    way.
    """
    proj = tmp_path / "no_silent_allowed"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["intent-check", "attest"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]
    verdict_lower = summary["verdict"].lower()

    # The verdict is "BLOCKED -- 'attest' not allowed in safe_edit mode..."
    # so 'allowed' appears as part of "not allowed". Check the verdict
    # does NOT start with ALLOWED (silent-SAFE prefix). Forbidden silent-SAFE
    # phrases on a BLOCKED verb would be "allowed" / "ok" / "safe" /
    # "no action needed" / "everything is fine" / "all clear"; the
    # startswith("allowed") guard below catches the leading-SAFE shape.
    assert not verdict_lower.startswith("allowed"), (
        f"Pattern-2 silent SAFE: BLOCKED verb produced ALLOWED verdict: {summary['verdict']!r}"
    )
    # And the structured allowed flag is False.
    assert summary.get("allowed") is False, "BLOCKED verb must have allowed=False"
    # Exit code is 5 (gate failure), not 0 (silent SAFE).
    assert result.exit_code == 5, f"BLOCKED verb must exit 5, got {result.exit_code}"


# ---------------------------------------------------------------------------
# 9. Clean ALLOWED branch: read-only-safe verb produces ALLOWED
#     verdict with partial_success=False (correctly clean).
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_intent_check(cli_runner, tmp_path, monkeypatch):
    """With an ALLOWED verb (`preflight` is read-only-safe), the gate
    emits ALLOWED + partial_success=False + exit 0. This is the
    affirmative path of the mode-gate state-reader -- it correctly
    reports a clean pass when one exists."""
    proj = tmp_path / "clean_allowed"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["intent-check", "preflight"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]
    assert summary.get("allowed") is True, f"preflight should be ALLOWED, got {summary!r}"
    assert summary.get("partial_success") is False, "ALLOWED verb must have partial_success=False"
    verdict = summary.get("verdict", "")
    assert verdict.startswith("ALLOWED"), f"verdict must start with ALLOWED, got {verdict!r}"
    assert result.exit_code == 0, f"ALLOWED verb must exit 0, got {result.exit_code}"


# ---------------------------------------------------------------------------
# 10. Unknown-command branch: explicit BLOCKED with "not in any mode's
#      allow-list" reason, NOT silent SAFE.
# ---------------------------------------------------------------------------


def test_unknown_command_explicit_not_silent(cli_runner, tmp_path, monkeypatch):
    """A typo / unknown command is BLOCKED with an explicit
    'not in any mode's allow-list' reason, NOT silently ALLOWED.
    This is the W805-QQ 4-axis-lens 'unknown-input' axis."""
    proj = tmp_path / "unknown"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["intent-check", "no-such-command"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]
    assert summary.get("allowed") is False
    assert summary.get("partial_success") is True
    reason = summary.get("reason", "")
    assert "not in any mode" in reason or "allow-list" in reason, (
        f"unknown-command reason must be explicit, got {reason!r}"
    )
    assert result.exit_code == 5


# ---------------------------------------------------------------------------
# 11. No-arg usage-error branch: distinct ``state: "error"`` + exit 2
#      (NOT collapsed into a silent SAFE).
# ---------------------------------------------------------------------------


def test_no_arg_usage_error_distinct(cli_runner, tmp_path, monkeypatch):
    """No INTENDED_COMMAND -> distinct usage-error envelope:
    state='error', partial_success=True, exit 2. This is NOT a
    silent SAFE -- the missing-arg state is explicit."""
    proj = tmp_path / "no_arg"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["intent-check"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]
    assert summary.get("state") == "error", f"no-arg must have state='error', got {summary.get('state')!r}"
    assert summary.get("partial_success") is True
    assert summary.get("allowed") is False
    assert result.exit_code == 2
