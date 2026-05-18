"""W805-AAA: Empty-corpus Pattern-2 smoke test on ``cmd_mode``.

Pattern-2 (silent-SAFE) audit. ``roam mode`` is the Agent-OS mode-policy
state-reader (R16): it shows / switches / queries the active agent mode
that gates which roam commands an agent is allowed to invoke. Read-only
sub-branches probed here are the default show, ``--list``, ``--check``,
and the stale-file fallback. The state-mutating ``mode <name>`` switch is
NOT exercised (would write to ``.roam/active_mode``).

W978 first-hypothesis discipline: empirical probe (5-axis: default show,
--list, --check allowed, --check blocked, stale-file) shows ``cmd_mode``
does NOT exhibit the silent-SAFE Pattern-2 bug carried by the W805
aggregator family. It is a CLEAN COUNTER-EXAMPLE peer of cmd_intent_check
(W805-YY) and cmd_next (W805-VV) -- the THIRD catalogued clean
state-reader counter-example to the W805 aggregator family.

The key empty-corpus behavior to lock in:

  * No ``.roam/active_mode`` -> ``policy_source: "default"`` (LOUD
    disclosure of the fallback lineage). ``state: "ok"`` is correct
    because the resolution IS deterministic and DID complete -- the
    default mode (``safe_edit``) is a documented valid resolution, not a
    degraded sentinel.
  * No ``.roam/active_mode`` + default-show -> ``partial_success: false``
    is correct because nothing went wrong: the documented default is the
    successful output of the query. The lineage is exposed via
    ``policy_source: "default"`` and the ``agent_contract.facts[]``
    string ``"policy source: default"``.
  * Stale ``.roam/active_mode`` (unknown name) -> ``partial_success:
    true`` + ``stale_active_mode_file`` field + prepended fact naming
    the stale contents. This is the "Make fallback chains loud"
    invariant applied correctly.
  * ``--check`` on a BLOCKED command -> ``partial_success: true``,
    ``allowed: false``, verdict starts ``"BLOCKED: ..."``, exit 5.
  * ``--check`` on an ALLOWED command -> ``partial_success: false``,
    ``allowed: true``, exit 0.

LAW 6 check: every branch's ``summary.verdict`` works without any other
field -- it always names the active mode, the allowed/blocked verb, or
the BLOCKED + upgrade-mode hint.

This module pins the desirable state-reader shape as a regression
invariant. NO xfail-strict because there is NO bug. ``cmd_mode`` joins
``cmd_next`` (W805-VV) and ``cmd_intent_check`` (W805-YY) as the THIRD
catalogued clean state-reader counter-example to the W805 aggregator
family.

W805 sweep yield (incl. this entry): aggregator family confirmed
six-strong (W805-F/KK/LL/OO/RR + TT); state-reader family confirmed
clean three-strong (W805-VV cmd_next + W805-YY cmd_intent_check +
W805-AAA cmd_mode).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

_CMD_PATH = Path(__file__).resolve().parents[1] / "src" / "roam" / "commands" / "cmd_mode.py"


@pytest.fixture
def cli_runner():
    return CliRunner()


def _parse_json_any_exit(result, command="mode"):
    """Parse JSON regardless of exit code.

    cmd_mode exits 5 on --check BLOCKED and 2 on usage-error, but in
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
    """If cmd_mode.py vanishes, this whole module skips."""
    if not _CMD_PATH.is_file():
        pytest.skip(f"cmd_mode.py absent at {_CMD_PATH}")
    assert _CMD_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# 2. Empty corpus does not crash (default show)
# ---------------------------------------------------------------------------


def test_empty_corpus_no_crash(cli_runner, tmp_path, monkeypatch):
    """No ``.roam/``, no git -- ``roam mode`` (default show) must
    produce a parseable envelope at exit 0, NOT a traceback.
    """
    proj = tmp_path / "untouched"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["mode"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# 3. Envelope always carries a verdict
# ---------------------------------------------------------------------------


def test_empty_corpus_envelope_has_verdict(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "verdict"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["mode"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), "summary.verdict must be non-empty"


# ---------------------------------------------------------------------------
# 4. State is explicit on default-show: policy_source loudly named
# ---------------------------------------------------------------------------


def test_empty_corpus_state_explicit(cli_runner, tmp_path, monkeypatch):
    """Empty corpus default-show MUST expose the resolution-source
    via ``policy_source: "default"`` AND ``agent_contract.facts[]``
    naming it explicitly. This is the "Make fallback chains loud"
    invariant for the state-reader -- the silent-SAFE bug would be
    omitting the lineage entirely.

    Note: ``state: "ok"`` is correct here because the mode-policy
    resolution IS deterministic and DID complete -- the default
    (``safe_edit``) is a documented valid output, not a degraded
    sentinel. The Pattern-2 invariant for mode-readers is on the
    LINEAGE EXPOSURE, not on the ``state`` field.
    """
    proj = tmp_path / "state_explicit"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["mode"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]
    # Resolution completed -- state is "ok".
    assert summary.get("state") == "ok", f"expected state='ok', got {summary.get('state')!r}"
    # Lineage is LOUD -- policy_source names the fallback.
    assert summary.get("policy_source") == "default", (
        f"empty corpus must expose policy_source='default', got {summary.get('policy_source')!r}"
    )
    # active_mode named explicitly.
    assert summary.get("active_mode") == "safe_edit", (
        f"empty corpus must resolve to safe_edit default, got {summary.get('active_mode')!r}"
    )


# ---------------------------------------------------------------------------
# 5. partial_success: false on default-show is CORRECT (clean resolution)
# ---------------------------------------------------------------------------


def test_empty_corpus_partial_success_set(cli_runner, tmp_path, monkeypatch):
    """Empty corpus default-show -> ``partial_success: false`` is CORRECT.
    The documented default mode is the successful output of the query;
    nothing went wrong. Lineage exposure is via ``policy_source`` not
    via ``partial_success``.

    Compare with W805-YY (intent-check): a BLOCKED verdict there does
    flip ``partial_success: true`` because the GATE failed. Here, the
    mode resolution itself succeeded -- the read-only query has no
    failure mode to report.
    """
    proj = tmp_path / "noindex"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["mode"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    # default show + empty corpus = clean resolution, no partial_success.
    assert data["summary"]["partial_success"] is False, (
        "empty corpus default-show must NOT flag partial_success (resolution succeeded via documented default)"
    )


# ---------------------------------------------------------------------------
# 6. LAW 6: verdict alone is actionable
# ---------------------------------------------------------------------------


def test_empty_corpus_law6_verdict_standalone(cli_runner, tmp_path, monkeypatch):
    """LAW 6: ``summary.verdict`` alone names the active mode and the
    allowed-command count. An agent that consumes ONLY the verdict must
    know which mode is active without reading any other field."""
    proj = tmp_path / "law6"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["mode"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    verdict = data["summary"]["verdict"]
    # Verdict names the active mode by name (LAW 6).
    assert "safe_edit" in verdict, f"verdict must name the active mode, got {verdict!r}"
    # And includes the allowed-command count.
    assert "allowed" in verdict.lower(), f"verdict must name the allowed-command axis, got {verdict!r}"


# ---------------------------------------------------------------------------
# 7. NEGATIVE REFERENCE: cmd_mode is NOT a silent-default peer.
#     Empty corpus default-show DISCLOSES policy_source explicitly --
#     the Pattern-2 bug would be omitting the lineage and emitting a
#     verdict indistinguishable from a constitution-driven safe_edit.
# ---------------------------------------------------------------------------


def test_no_silent_default_mode_on_empty(cli_runner, tmp_path, monkeypatch):
    """Empty corpus default-show MUST NOT hide the fact that the
    resolution came from the baked-in default.

    Agent-safety: a silent default would teach the agent that the
    project author chose ``safe_edit`` via constitution -- but actually
    the resolution fell through every priority level (explicit / env /
    file) down to the hardcoded default. The lineage difference matters
    for any decision the agent makes about "what does this project want."

    This test pins the desirable behavior: emit
    ``policy_source: "default"`` AND ``"policy source: default"`` in
    ``agent_contract.facts``.

    This is the W805-AAA NEGATIVE-REFERENCE pin: cmd_mode is a clean
    counter-example to the aggregator family (cmd_brief / cmd_audit /
    cmd_dogfood etc.) -- this test asserts it stays that way.
    """
    proj = tmp_path / "no_silent_default"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["mode"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]

    # LOUD disclosure required in summary.
    assert summary.get("policy_source") == "default", (
        f"Pattern-2 silent default: empty corpus hides policy_source. Got summary={summary!r}"
    )

    # AND in agent_contract.facts (redundant exposure is intentional --
    # an agent that reads only the contract still sees the lineage).
    facts = data.get("agent_contract", {}).get("facts", [])
    facts_joined = " | ".join(facts)
    assert "default" in facts_joined.lower(), (
        f"Pattern-2 silent default: empty corpus hides lineage from agent_contract.facts. Got facts={facts!r}"
    )

    # Exit 0 (success), NOT a degraded-state exit.
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# 8. Clean ALLOWED branch via --check: read-only-safe verb produces
#     ALLOWED verdict with partial_success=False (correctly clean).
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_mode(cli_runner, tmp_path, monkeypatch):
    """With an ALLOWED verb (`preflight` is in safe_edit), --check
    emits an ``allowed: true`` envelope + ``partial_success: false``
    + exit 0. This is the affirmative path of the mode-gate
    state-reader -- it correctly reports a clean pass when one
    exists, with no degraded-state ambiguity."""
    proj = tmp_path / "clean_allowed"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["mode", "--check", "preflight"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]
    assert summary.get("allowed") is True, f"preflight should be ALLOWED, got {summary!r}"
    assert summary.get("partial_success") is False, "ALLOWED verb must have partial_success=False"
    assert summary.get("state") == "ok"
    assert summary.get("active_mode") == "safe_edit"
    assert result.exit_code == 0, f"ALLOWED verb must exit 0, got {result.exit_code}"


# ---------------------------------------------------------------------------
# 9. --check BLOCKED branch: explicit BLOCKED verdict + upgrade hint,
#     NOT silent ALLOWED.
# ---------------------------------------------------------------------------


def test_check_blocked_explicit_not_silent(cli_runner, tmp_path, monkeypatch):
    """--check on a BLOCKED verb (`attest` requires autonomous_pr)
    must emit:
      * verdict starts ``"BLOCKED: ..."``
      * ``allowed: false``
      * ``partial_success: true``
      * exit 5 (gate failure)
      * verdict names the upgrade mode (``autonomous_pr``)

    This is the W805-QQ 4-axis-lens 'blocked-input' axis on a
    mode-gate state-reader."""
    proj = tmp_path / "check_blocked"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["mode", "--check", "attest"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]
    assert summary.get("allowed") is False
    assert summary.get("partial_success") is True
    verdict = summary.get("verdict", "")
    assert verdict.startswith("BLOCKED"), f"verdict must start with BLOCKED, got {verdict!r}"
    assert "autonomous_pr" in verdict, f"verdict must name upgrade mode, got {verdict!r}"
    assert result.exit_code == 5


# ---------------------------------------------------------------------------
# 10. Stale .roam/active_mode -> partial_success=True + loud disclosure.
#      This is the canonical "Make fallback chains loud" applied to a
#      DEGRADED resolution (vs the clean default-show in test_4).
# ---------------------------------------------------------------------------


def test_stale_active_mode_file_loud(cli_runner, tmp_path, monkeypatch):
    """A ``.roam/active_mode`` containing an unknown mode name must:
      * flip ``partial_success: true`` (lineage was degraded)
      * expose ``summary.stale_active_mode_file = <raw contents>``
      * prepend a fact to ``agent_contract.facts`` naming the stale
        contents

    This is the W978-discipline counter-test to test_4: the clean
    default-show case has ``partial_success: false`` because nothing
    went wrong; the stale-file case has ``partial_success: true``
    because something DID. Both expose lineage loudly via distinct
    structured fields."""
    proj = tmp_path / "stale_file"
    proj.mkdir()
    roam_dir = proj / ".roam"
    roam_dir.mkdir()
    (roam_dir / "active_mode").write_text("not_a_real_mode\n", encoding="utf-8")
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["mode"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]

    # Resolution fell through to the default, but lineage was degraded.
    assert summary.get("partial_success") is True, f"stale active_mode must flip partial_success=True, got {summary!r}"
    assert summary.get("stale_active_mode_file") == "not_a_real_mode", (
        f"stale contents must be exposed via summary.stale_active_mode_file, got {summary!r}"
    )
    assert summary.get("active_mode") == "safe_edit", "stale fallback must resolve to the documented default"

    # And the fact list names the stale state.
    facts = data.get("agent_contract", {}).get("facts", [])
    assert facts, "agent_contract.facts must not be empty"
    first_fact = facts[0]
    assert "stale" in first_fact.lower(), f"first fact must name the stale lineage, got {first_fact!r}"
    assert "not_a_real_mode" in first_fact, f"first fact must name the actual stale contents, got {first_fact!r}"


# ---------------------------------------------------------------------------
# 11. --list on empty corpus: enumerates all 4 modes + active_mode named.
# ---------------------------------------------------------------------------


def test_list_modes_on_empty_corpus(cli_runner, tmp_path, monkeypatch):
    """``--list`` on empty corpus must enumerate every VALID_MODE
    and name the active default. State is clean."""
    proj = tmp_path / "list"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["mode", "--list"], cwd=proj, json_mode=True)
    data = _parse_json_any_exit(result)
    summary = data["summary"]
    assert summary.get("modes_total") == 4
    assert summary.get("active_mode") == "safe_edit"
    assert summary.get("state") == "ok"
    assert summary.get("partial_success") is False
    modes_view = data.get("modes", [])
    mode_names = {m["mode"] for m in modes_view}
    assert mode_names == {"read_only", "safe_edit", "migration", "autonomous_pr"}
    assert result.exit_code == 0
