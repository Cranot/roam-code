"""W1453 — an unknown fitness rule ``type`` vanished from ``roam preflight``.

Second of the two remaining instances of the W1450 defect class (the first is
``tests/test_w1453_diff_fitness_dispatch.py``). ``cmd_preflight._check_fitness``
kept its own copy of the dispatcher's membership test::

    rtype = rule.get("type", "")
    checker = _CHECKERS.get(rtype)
    if checker is None:
        continue

W1449 closed preflight's *exception* path — a checker that RAISES now reports
``status="ERROR"``, lands in ``errored_rules``, floors the section severity off
``OK`` and flips ``partial_success``. It did NOT close this one. A rule whose
``type`` no checker handles still fell out of the loop entirely: gone from
``rule_details``, gone from ``rules_checked``, ``rules_errored`` still ``0``.

MEASURED — two rules in ``.roam/fitness.yaml``, one healthy ``naming`` rule and
one ``type: dependancy`` typo of a real architectural constraint::

    "rules_checked": 1          <- the user wrote 2
    "rules_errored": 0
    "severity": "OK"
    "partial_success": false
    text: "  Fitness:          all 1 rules pass    [OK]"

``roam fitness`` on the SAME file reports the typo as ERROR and exits 1
(W1450). Preflight said the architecture was fine.

Fix routes the unknown type into the bucket W1449 already built — same ERROR
row shape (via the shared ``cmd_fitness._unevaluable_rule_entry``, difflib
did-you-mean included), same ``errored_rules`` list, same severity floor, same
``partial_success`` flip — rather than inventing a parallel channel. Membership
is decided by ``cmd_fitness._resolve_checker``, the single test W1450 installed,
so registering a checker teaches preflight too.

Exit code deliberately unchanged: ``roam preflight`` exits 0 on a genuinely
FAILING rule (pinned below), so it is advisory, not a gate. Its remedy is the
severity floor + disclosure, exactly as W1449 chose for the sibling path.

NEGATIVE CONTROLS throughout: a healthy rule of a known type must still be
evaluated and still PASS, a violated rule must still FAIL and still drive
severity, and a rule file with no unknown types must produce a byte-identical
``OK`` section.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.commands import cmd_fitness, cmd_preflight
from tests.conftest import index_in_process, invoke_cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MODELS_SRC = "class User:\n    def __init__(self, name):\n        self.name = name\n"
_AUTH_SRC = "from app.models import User\n\n\ndef verify_token(t):\n    return User('test')\n"

_HEALTHY_RULE = "funcs-are-snake-case"
_TYPO_RULE = "no-auth-to-models"
_TARGET = "verify_token"

# The healthy rule PASSES; the typo'd rule is a real architectural constraint
# (app/auth.py -> app/models.py IS a live edge) that `dependancy` disables.
_RULES_TYPO = """rules:
  - name: funcs-are-snake-case
    type: naming
    kind: function
    pattern: "^[a-z_][a-z0-9_]*$"
  - name: no-auth-to-models
    type: dependancy
    from: "app/auth.py"
    to: "app/models.py"
    allow: false
"""

# Same file with the typo corrected — the negative control that proves the
# rule was worth enforcing (it FAILS once spelled right).
_RULES_CORRECT = _RULES_TYPO.replace("type: dependancy", "type: dependency")

_RULES_HEALTHY_ONLY = """rules:
  - name: funcs-are-snake-case
    type: naming
    kind: function
    pattern: "^[a-z_][a-z0-9_]*$"
"""


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def preflight_project(tmp_path, monkeypatch):
    """Indexed two-file project; the caller writes ``.roam/fitness.yaml``."""
    proj = tmp_path / "w1453_preflight_project"
    app = proj / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "models.py").write_text(_MODELS_SRC, encoding="utf-8")
    (app / "auth.py").write_text(_AUTH_SRC, encoding="utf-8")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    (proj / ".roam").mkdir(exist_ok=True)
    return proj


def _write_rules(proj, yaml_text: str) -> None:
    (proj / ".roam" / "fitness.yaml").write_text(yaml_text, encoding="utf-8")


def _preflight_json(cli_runner, proj) -> dict:
    result = invoke_cli(cli_runner, ["preflight", _TARGET], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _fitness_section(cli_runner, proj) -> dict:
    return _preflight_json(cli_runner, proj)["fitness"]


def _rows_by_name(fitness: dict) -> dict[str, dict]:
    return {r["name"]: r for r in fitness["rule_details"]}


# ---------------------------------------------------------------------------
# 0. The rule stops disappearing.
# ---------------------------------------------------------------------------


def test_unknown_rule_type_appears_in_rule_details(cli_runner, preflight_project):
    _write_rules(preflight_project, _RULES_TYPO)
    rows = _rows_by_name(_fitness_section(cli_runner, preflight_project))
    assert _TYPO_RULE in rows, f"the rule the user wrote vanished from rule_details entirely: {sorted(rows)}"
    assert rows[_TYPO_RULE]["status"] == "ERROR", rows[_TYPO_RULE]


def test_rules_checked_counts_every_rule_the_user_wrote(cli_runner, preflight_project):
    _write_rules(preflight_project, _RULES_TYPO)
    fitness = _fitness_section(cli_runner, preflight_project)
    assert fitness["rules_checked"] == 2, f"the unevaluable rule was dropped from the denominator: {fitness}"
    assert len(fitness["rule_details"]) == fitness["rules_checked"], (
        "rules_checked must keep agreeing with len(rule_details)"
    )


def test_preflight_and_fitness_see_the_same_number_of_rules(cli_runner, preflight_project):
    """Agreement assertion — the two commands read one rule file."""
    _write_rules(preflight_project, _RULES_TYPO)
    fitness_payload = json.loads(invoke_cli(cli_runner, ["fitness"], cwd=preflight_project, json_mode=True).output)
    preflight_fitness = _fitness_section(cli_runner, preflight_project)
    assert preflight_fitness["rules_checked"] == fitness_payload["summary"]["rules_checked"], (
        f"preflight counted {preflight_fitness['rules_checked']} rules, "
        f"roam fitness counted {fitness_payload['summary']['rules_checked']}"
    )


# ---------------------------------------------------------------------------
# 1. It is reported as unevaluated, through W1449's existing bucket.
# ---------------------------------------------------------------------------


def test_unknown_rule_type_lands_in_the_errored_bucket(cli_runner, preflight_project):
    _write_rules(preflight_project, _RULES_TYPO)
    fitness = _fitness_section(cli_runner, preflight_project)
    assert fitness["rules_errored"] == 1, fitness
    assert fitness["errored_rules"] == [_TYPO_RULE], fitness
    assert fitness["rules_evaluated"] == 1, f"rules_evaluated must exclude the rule that never ran: {fitness}"


def test_unknown_rule_type_carries_the_shared_did_you_mean(cli_runner, preflight_project):
    """Reuses ``cmd_fitness._unevaluable_rule_entry`` — not a third dialect."""
    _write_rules(preflight_project, _RULES_TYPO)
    row = _rows_by_name(_fitness_section(cli_runner, preflight_project))[_TYPO_RULE]
    assert "Did you mean 'dependency'?" in row["error"], row


def test_unknown_rule_type_row_has_no_floored_zero(cli_runner, preflight_project):
    """W1449 convention: no measurement -> ``violations: None``, never ``0``.

    ``0`` would read as "inspected, found nothing".
    """
    _write_rules(preflight_project, _RULES_TYPO)
    row = _rows_by_name(_fitness_section(cli_runner, preflight_project))[_TYPO_RULE]
    assert row["violations"] is None, row
    assert row["violations_on_target"] is None, row
    assert row["violations_on_siblings"] is None, row


def test_severity_is_floored_off_ok(cli_runner, preflight_project):
    """The headline consequence: the section read ``OK`` over an unrun rule."""
    _write_rules(preflight_project, _RULES_TYPO)
    fitness = _fitness_section(cli_runner, preflight_project)
    assert fitness["rules_failed"] == 0, "precondition: nothing FAILS, so severity would be OK"
    assert fitness["severity"] != "OK", f"a section made partly of unrun rules must not roll up clean: {fitness}"
    assert fitness["severity"] == "WARNING", fitness


def test_partial_success_and_marker_reach_the_envelope(cli_runner, preflight_project):
    _write_rules(preflight_project, _RULES_TYPO)
    payload = _preflight_json(cli_runner, preflight_project)
    assert payload.get("partial_success") is True, (
        f"an uncomputable signal must flip partial_success (W1332): {payload.get('partial_success')}"
    )
    markers = [w for w in (payload.get("warnings_out") or []) if "fitness_rule_unevaluable" in w]
    assert markers, f"no marker on the envelope: {payload.get('warnings_out')}"
    assert markers[0].startswith("preflight_fitness_rule_unevaluable:"), (
        f"marker must stay in the ``preflight_*`` family: {markers[0]!r}"
    )
    assert _TYPO_RULE in markers[0]


def test_text_mode_names_the_unevaluated_rule(cli_runner, preflight_project):
    _write_rules(preflight_project, _RULES_TYPO)
    result = invoke_cli(cli_runner, ["preflight", _TARGET], cwd=preflight_project)
    assert result.exit_code == 0, result.output
    fitness_line = next(line for line in result.output.splitlines() if "Fitness:" in line)
    assert "could not be evaluated" in fitness_line, fitness_line
    assert _TYPO_RULE in fitness_line, fitness_line
    assert "[OK]" not in fitness_line, f"the line still claims a clean fitness section: {fitness_line!r}"


# ---------------------------------------------------------------------------
# 2. NEGATIVE CONTROLS.
# ---------------------------------------------------------------------------


def test_negative_control_healthy_rule_still_evaluates_and_passes(cli_runner, preflight_project):
    """Same run as the typo — the good rule must be unaffected."""
    _write_rules(preflight_project, _RULES_TYPO)
    rows = _rows_by_name(_fitness_section(cli_runner, preflight_project))
    assert rows[_HEALTHY_RULE]["status"] == "PASS", rows[_HEALTHY_RULE]
    assert rows[_HEALTHY_RULE]["violations"] == 0, rows[_HEALTHY_RULE]


def test_negative_control_correct_spelling_actually_fails(cli_runner, preflight_project):
    """Proof the disabled rule was load-bearing, not a no-op.

    The identical rule with ``dependency`` spelled right FAILS — so the pre-fix
    ``severity: "OK"`` was hiding a real architectural violation.
    """
    _write_rules(preflight_project, _RULES_CORRECT)
    fitness = _fitness_section(cli_runner, preflight_project)
    rows = _rows_by_name(fitness)
    assert rows[_TYPO_RULE]["status"] == "FAIL", rows[_TYPO_RULE]
    assert fitness["rules_errored"] == 0, f"a correctly-typed rule must not be errored: {fitness}"
    assert fitness["severity"] != "OK"


def test_negative_control_clean_rule_file_stays_ok(cli_runner, preflight_project):
    """No unknown types -> section identical to pre-W1453."""
    _write_rules(preflight_project, _RULES_HEALTHY_ONLY)
    payload = _preflight_json(cli_runner, preflight_project)
    fitness = payload["fitness"]
    assert fitness["severity"] == "OK", fitness
    assert fitness["rules_errored"] == 0, fitness
    assert fitness["errored_rules"] == [], fitness
    assert fitness["rules_checked"] == 1 == fitness["rules_evaluated"], fitness
    assert not [w for w in (payload.get("warnings_out") or []) if "fitness_rule_unevaluable" in w]


def test_negative_control_a_failing_rule_still_exits_zero(cli_runner, preflight_project):
    """Premise of the disclose-not-fail-closed decision for preflight.

    ``roam preflight`` is advisory. If it ever gates on fitness failures,
    revisit the unevaluable-rule exit code alongside it.
    """
    _write_rules(preflight_project, _RULES_CORRECT)
    result = invoke_cli(cli_runner, ["preflight", _TARGET], cwd=preflight_project, json_mode=True)
    assert json.loads(result.output)["fitness"]["rules_failed"] >= 1, "precondition: a rule genuinely fails"
    assert result.exit_code == 0, "premise broken — preflight now gates on fitness failures"


def test_unevaluable_rule_does_not_change_the_exit_code(cli_runner, preflight_project):
    _write_rules(preflight_project, _RULES_TYPO)
    result = invoke_cli(cli_runner, ["preflight", _TARGET], cwd=preflight_project, json_mode=True)
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# 3. Structural — one registry, both halves.
# ---------------------------------------------------------------------------


def test_registering_a_checker_makes_preflight_evaluate_it(cli_runner, preflight_project, monkeypatch):
    def _synthetic(rule, conn):
        return []

    monkeypatch.setitem(cmd_fitness._CHECKERS, "synthetic", _synthetic)
    _write_rules(preflight_project, "rules:\n  - name: brand-new-rule\n    type: synthetic\n")
    fitness = _fitness_section(cli_runner, preflight_project)
    rows = _rows_by_name(fitness)
    assert rows["brand-new-rule"]["status"] == "PASS", (
        f"registering a checker in _CHECKERS must teach cmd_preflight: {rows}"
    )
    assert fitness["rules_errored"] == 0, fitness


def test_deregistering_a_checker_makes_preflight_report_error(cli_runner, preflight_project, monkeypatch):
    """The other direction — membership really is ``_CHECKERS``, not a literal list."""
    monkeypatch.delitem(cmd_fitness._CHECKERS, "naming")
    _write_rules(preflight_project, _RULES_HEALTHY_ONLY)
    fitness = _fitness_section(cli_runner, preflight_project)
    rows = _rows_by_name(fitness)
    assert rows[_HEALTHY_RULE]["status"] == "ERROR", (
        f"deregistered type must become ERROR, not vanish and not PASS: {rows}"
    )
    assert fitness["rules_checked"] == 1, fitness
    assert fitness["severity"] == "WARNING", fitness


def test_dispatch_has_no_local_membership_test():
    """Regression guard on the SHAPE — the defect class is a drifting second copy.

    Asserted over the AST, not the text, so the explanatory comment quoting the
    old ``_CHECKERS.get(rtype)`` line does not trip it.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(cmd_preflight._check_fitness)))
    local_lookups = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_CHECKERS"
    ]
    assert not local_lookups, (
        "cmd_preflight._check_fitness resolves checkers locally again — membership "
        "must go through cmd_fitness._resolve_checker"
    )
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "_resolve_checker" in called, "the shared membership test is gone from the dispatcher"


def test_preflight_shares_the_registry_object_with_fitness():
    """``monkeypatch.setitem(cmd_preflight._CHECKERS, ...)`` must still be visible.

    Existing suites (test_w1449_*, test_preflight_fitness_sibling_names) patch
    through the preflight name; that only works while it is the same dict.
    """
    assert cmd_preflight._CHECKERS is cmd_fitness._CHECKERS
