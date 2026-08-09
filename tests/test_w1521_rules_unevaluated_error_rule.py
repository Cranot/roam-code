"""W1521 -- `roam rules --ci` exited 0 "all N rules passed" for a rule it never evaluated.

The defect. A ``.roam/rules`` rule with ``severity: error`` and a
``must_not.clones_with`` clause, on a repo where ``roam clones --persist`` has
not been run, printed::

    VERDICT: all 1 rules passed
      [PASS] Payment path must not be cloned
    NOTE: at least one rule could not fully evaluate ...

-- and exited 0 under ``--ci``. The NOTE contradicts the verdict it prints
under: zero rules "DID run", and the verdict claims all 1 passed.

Mechanism. ``graph_clauses.check_clones_with`` returns
``(False, {"status": "not_indexed"})``; the engine sets ``partial=True`` but
``fired = must_not and matches`` is False, so no violation is recorded, and
``_graph_clause_result`` emits ``passed=True`` + ``partial_success=True``.
``cmd_rules`` collected ``partial_success`` for DISPLAY only -- the three
``if ci_mode and failed_errors > 0`` sites never consulted it.

The scope that matters. Only an ERROR-severity rule that could not be
evaluated gates. ``--ci`` is documented as error-severity only, and every
``clones_with`` rule fails to evaluate until ``roam clones --persist`` has run
-- which the R18 comment and the ``rules --init`` template both document as
normal. An advisory rule that legitimately cannot resolve must disclose, not
gate; the negative tests below pin that, and pin that the shipped
``rules --init`` template still exits 0 under ``--ci``.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.conftest import git_init, index_in_process, invoke_cli, parse_json_output

_PAY_SRC = (
    "def charge_card(user, amount):\n"
    '    """Charge a card."""\n'
    "    if not user:\n"
    "        return None\n"
    "    total = amount * 1.0\n"
    "    return {'user': user, 'total': total}\n"
    "\n"
    "\n"
    "def handle_admin(user, amount):\n"
    '    """Admin path."""\n'
    "    if not user:\n"
    "        return None\n"
    "    total = amount * 1.0\n"
    "    return {'user': user, 'total': total}\n"
)


def _rule_yaml(severity: str) -> str:
    return (
        'name: "Payment path must not be cloned"\n'
        'description: "charge_card must not be a clone of handle_admin"\n'
        f"severity: {severity}\n"
        "when:\n"
        "  symbol: charge_card\n"
        "must_not:\n"
        "  clones_with: handle_admin\n"
    )


def _make_repo(tmp_path, severity: str):
    """Repo whose only rule carries an unevaluable clones_with clause.

    `roam clones --persist` is deliberately never run: clone_pairs stays
    empty, which is the documented normal state.
    """
    proj = tmp_path / f"w1521_{severity}"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "pay.py").write_text(_PAY_SRC)
    rules_dir = proj / ".roam" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "no_payment_clone.yaml").write_text(_rule_yaml(severity))
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def error_rule_repo(tmp_path):
    return _make_repo(tmp_path, "error")


@pytest.fixture
def warning_rule_repo(tmp_path):
    return _make_repo(tmp_path, "warning")


# ---------------------------------------------------------------------------
# MUST FIRE -- an error-severity rule that never ran does not authorize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
def test_ci_refuses_on_unevaluated_error_rule(cli_runner, error_rule_repo, monkeypatch, mode_args):
    monkeypatch.chdir(error_rule_repo)
    result = invoke_cli(cli_runner, [*mode_args, "rules", "--ci"], cwd=error_rule_repo)
    assert result.exit_code == 5, (
        f"mode {mode_args or ['text']} exited {result.exit_code}; a rule that could not be "
        f"evaluated has cleared nothing.\n{result.output[:400]}"
    )


def test_verdict_does_not_claim_a_total_it_did_not_measure(cli_runner, error_rule_repo, monkeypatch):
    monkeypatch.chdir(error_rule_repo)
    result = invoke_cli(cli_runner, ["rules", "--ci"], cwd=error_rule_repo)
    assert "all 1 rules passed" not in result.output, result.output
    assert "could not be evaluated" in result.output, result.output
    # And the per-rule line must not read [PASS] for a rule that never ran.
    assert "[UNEVAL]" in result.output, result.output


def test_json_summary_separates_unevaluated_from_passed(cli_runner, error_rule_repo, monkeypatch):
    monkeypatch.chdir(error_rule_repo)
    result = invoke_cli(cli_runner, ["rules", "--ci"], cwd=error_rule_repo, json_mode=True)
    # parse_json_output asserts exit 0; this run legitimately exits 5.
    assert result.exit_code == 5, result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["unevaluated"] == 1, summary
    assert summary["unevaluated_errors"] == 1, summary
    assert summary["scan_incomplete"] is True, summary
    assert summary["partial_success"] is True, summary
    assert "all 1 rules passed" not in summary["verdict"], summary


def test_sarif_carries_the_unevaluated_disclosure(cli_runner, error_rule_repo, monkeypatch):
    """The SARIF channel emitted results: [] and no notifications at all."""
    monkeypatch.chdir(error_rule_repo)
    result = invoke_cli(cli_runner, ["--sarif", "rules", "--ci"], cwd=error_rule_repo)
    doc = json.loads(result.output)
    run = doc["runs"][0]
    notes = [
        n.get("message", {}).get("text", "")
        for inv in run.get("invocations", [])
        for n in inv.get("toolExecutionNotifications", [])
    ]
    assert any("could not be evaluated" in n for n in notes), notes


def test_check_rules_sibling_no_longer_reports_partial_success_false(cli_runner, error_rule_repo, monkeypatch):
    """`_evaluate_custom_rules` dropped `partial_success` when adapting.

    The same rule file, the same repo: `rules` reported partial_success=True
    and `check-rules` reported False. Fixing one and leaving the other is the
    "never generalised to its sibling" shape this batch is about.
    """
    monkeypatch.chdir(error_rule_repo)
    result = invoke_cli(
        cli_runner,
        ["check-rules", "--rule", "Payment path must not be cloned"],
        cwd=error_rule_repo,
        json_mode=True,
    )
    data = parse_json_output(result, "check-rules")
    summary = data["summary"]
    assert summary["partial_success"] is True, summary
    assert summary["unevaluated"] == 1, summary
    assert summary["scan_incomplete"] is True, summary
    assert "could not be evaluated" in summary["verdict"], summary
    assert data["results"][0]["partial_success"] is True, data["results"]


# ---------------------------------------------------------------------------
# MUST NOT FIRE -- advisory rules disclose, they do not gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
def test_unevaluated_warning_rule_does_not_gate(cli_runner, warning_rule_repo, monkeypatch, mode_args):
    """The outage this guards against.

    Setting scan_incomplete from ALL partial rules rather than error-severity
    ones newly fails --ci for any user keeping advisory graph-clause rules
    that legitimately cannot resolve. Every `clones_with` rule is in that
    state until `roam clones --persist` has run, which the R18 comment and the
    `rules --init` template both document as normal.
    """
    monkeypatch.chdir(warning_rule_repo)
    result = invoke_cli(cli_runner, [*mode_args, "rules", "--ci"], cwd=warning_rule_repo)
    assert result.exit_code == 0, f"mode {mode_args or ['text']}: {result.output[:400]}"


def test_unevaluated_warning_rule_is_still_disclosed(cli_runner, warning_rule_repo, monkeypatch):
    """Not gating is not the same as not saying."""
    monkeypatch.chdir(warning_rule_repo)
    result = invoke_cli(cli_runner, ["rules", "--ci"], cwd=warning_rule_repo, json_mode=True)
    summary = parse_json_output(result, "rules")["summary"]
    assert summary["unevaluated"] == 1, summary
    assert summary["unevaluated_errors"] == 0, summary
    assert summary["scan_incomplete"] is False, summary
    assert summary["partial_success"] is True, summary


def test_without_ci_an_unevaluated_error_rule_still_exits_zero(cli_runner, error_rule_repo, monkeypatch):
    """`rules` without `--ci` is reporting, not gating."""
    monkeypatch.chdir(error_rule_repo)
    result = invoke_cli(cli_runner, ["rules"], cwd=error_rule_repo)
    assert result.exit_code == 0, result.output


def test_shipped_init_template_still_passes_ci(tmp_path):
    """`roam rules --init && roam rules --ci` must stay green on a fresh repo.

    Run out-of-process because `--init` writes files and the template is the
    onboarding path; if the shipped example rules ever became unevaluable at
    error severity, this fix would turn first-run onboarding red. Measured
    today: all 5 template rules evaluate, partial_success is False.
    """
    proj = tmp_path / "w1521_init"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "mod.py").write_text('def foo(a):\n    """Doc."""\n    return a\n')
    git_init(proj)
    index_in_process(proj)

    env = {"PYTHONUTF8": "1"}
    import os

    env = {**os.environ, **env}
    init = subprocess.run(
        [sys.executable, "-m", "roam", "rules", "--init"],
        cwd=str(proj),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert init.returncode == 0, init.stdout + init.stderr
    ci = subprocess.run(
        [sys.executable, "-m", "roam", "--json", "rules", "--ci"],
        cwd=str(proj),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert ci.returncode == 0, ci.stdout[-2000:] + ci.stderr[-2000:]
    summary = json.loads(ci.stdout)["summary"]
    assert summary.get("scan_incomplete") is False, summary


def test_ci_gate_uses_exit_code_five_not_one():
    """The help text says 1 and the code returns 5 -- that mismatch is its own
    catalogued finding. Do NOT "fix" it by changing the code: 1 is outside MCP
    `_SUCCESS_EXIT_CODES` and would flip roam_rules to isError:true."""
    from roam.exit_codes import EXIT_GATE_FAILURE
    from roam.mcp_server import _SUCCESS_EXIT_CODES

    assert EXIT_GATE_FAILURE == 5
    assert EXIT_GATE_FAILURE in _SUCCESS_EXIT_CODES
