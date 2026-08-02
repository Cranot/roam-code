"""W1449 — a fitness checker that RAISED must never render as PASS in preflight.

Sibling of W1332 (uncomputable signal floored to a clean-looking value), same
doctrine, a new site: ``cmd_preflight._check_fitness`` ran every configured
rule inside

    try:
        violations = checker(rule, conn)
    except (ImportError, re.error, sqlite3.DatabaseError):
        violations = []

and then derived ``status = "PASS" if not violations else "FAIL"``. A checker
that blew up produced the same empty list as a checker that ran and found
nothing, so the rule landed in ``rule_details`` as PASS, was still counted in
``rules_checked``, and the text renderer printed ``all 1 rules pass  [OK]``.

MEASURED divergence that proves it is a fail-open rather than a design choice —
one rule, ``{"type": "naming", "kind": "function", "pattern": "handle_(["}``,
whose ``re.compile`` is unguarded in ``cmd_fitness._check_naming_rule``:

* ``roam fitness``   -> raises ``re.error("unterminated character set ...")``
* ``roam preflight`` -> ``rule_details=[{'status': 'PASS', 'violations': 0}]``,
  ``rules_checked=1``, ``rules_failed=0``, ``severity="OK"``,
  ``partial_success=False``, no ``warnings_out``

Same rule file, same checker, opposite verdicts.

The fix keeps the ``except`` (one broken rule must not take the other N-1 down)
but reports instead of flooring: ``status="ERROR"`` with ``violations=None``
(no measurement, so no floored zero — mirrors ``cmd_db_check``'s ``count:
None``), a ``preflight_fitness_rule_failed:`` marker in the caller's
``warnings_out`` bucket, ``summary.partial_success=True``, a section severity
floored off ``OK``, and a text line that names the unevaluated rules.

Every assertion here has a NEGATIVE CONTROL on the same code path with a
healthy rule, so a "fix" that merely makes everything loud cannot pass.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from pathlib import Path

import pytest

from roam.commands import cmd_preflight
from tests.conftest import index_in_process, invoke_cli

# ---------------------------------------------------------------------------
# Fixtures: a two-symbol project plus the broken / healthy rule files.
# ---------------------------------------------------------------------------

_HANDLERS_SRC = "def handle_request(payload):\n    return payload\n"

_BROKEN_PATTERN = "handle_(["
_HEALTHY_PATTERN = "handle_"

_RULE_NAME = "handlers must be prefixed"


def _fitness_yaml(pattern: str) -> str:
    return f'rules:\n  - name: "{_RULE_NAME}"\n    type: naming\n    kind: function\n    pattern: "{pattern}"\n'


@pytest.fixture
def preflight_project(tmp_path):
    """An indexed one-file project; the caller writes the rule file."""
    proj = tmp_path / "w1449_proj"
    (proj / "app").mkdir(parents=True)
    (proj / "app" / "handlers.py").write_text(_HANDLERS_SRC, encoding="utf-8")
    index_in_process(proj)
    (proj / ".roam").mkdir(exist_ok=True)
    return proj


def _write_rules(proj: Path, pattern: str) -> None:
    (proj / ".roam" / "fitness.yaml").write_text(_fitness_yaml(pattern), encoding="utf-8")


def _preflight_json(cli_runner, proj: Path) -> dict:
    result = invoke_cli(cli_runner, ["preflight", "handle_request"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# 0. The divergence itself — the two commands must not disagree.
# ---------------------------------------------------------------------------


def test_the_same_pattern_that_raises_in_fitness_is_not_clean_in_preflight():
    """``_check_naming_rule`` raises on this pattern; preflight must not say PASS.

    Asserted at the CHECKER level rather than through ``roam fitness`` so this
    pins the divergence without coupling to the fitness command's envelope.
    """
    with pytest.raises(re.error):
        # No conn needed: re.compile blows up before the query.
        cmd_preflight._CHECKERS["naming"](
            {"name": _RULE_NAME, "type": "naming", "kind": "function", "pattern": _BROKEN_PATTERN},
            None,
        )


# ---------------------------------------------------------------------------
# 1. _check_fitness unit level — an ERROR row is not a PASS row.
# ---------------------------------------------------------------------------


def _install_rule(monkeypatch, checker) -> None:
    monkeypatch.setattr(cmd_preflight, "_load_rules", lambda root: [{"name": _RULE_NAME, "type": "synthetic"}])
    monkeypatch.setitem(cmd_preflight._CHECKERS, "synthetic", checker)


def _raise_re_error(rule, conn):
    raise re.error("unterminated character set at position 8")


def _raise_db_error(rule, conn):
    raise sqlite3.DatabaseError("no such table: symbols")


def _raise_import_error(rule, conn):
    raise ImportError("No module named 'networkx'")


def _clean(rule, conn):
    return []


def _violating(rule, conn):
    return [{"source": "app/handlers.py:1", "message": "synthetic violation"}]


@pytest.mark.parametrize("checker", [_raise_re_error, _raise_db_error, _raise_import_error])
def test_raising_checker_is_reported_not_passed(monkeypatch, checker):
    """All three caught exception types produce an ERROR row, never PASS."""
    _install_rule(monkeypatch, checker)
    markers: list[str] = []

    result = cmd_preflight._check_fitness(conn=None, root=".", target_paths={"app/handlers.py"}, warnings_out=markers)

    (detail,) = result["rule_details"]
    assert detail["status"] == "ERROR"
    assert detail["status"] != "PASS"
    # No measurement was produced — a floored 0 would read as "ran, found none".
    assert detail["violations"] is None
    assert detail["error"]

    assert result["rules_errored"] == 1
    assert result["errored_rules"] == [_RULE_NAME]
    assert result["rules_evaluated"] == 0
    # An uncomputable signal is not a clean one.
    assert result["severity"] != "OK"
    # The failure reaches the caller's disclosure bucket.
    assert len(markers) == 1
    assert markers[0].startswith("preflight_fitness_rule_failed:")
    assert _RULE_NAME in markers[0]


def test_a_raising_rule_does_not_take_the_other_rules_down(monkeypatch):
    """The ``except`` stays: one broken rule must not lose the healthy verdicts."""
    monkeypatch.setattr(
        cmd_preflight,
        "_load_rules",
        lambda root: [
            {"name": "broken", "type": "boom"},
            {"name": "healthy-fail", "type": "fails"},
            {"name": "healthy-pass", "type": "clean"},
        ],
    )
    monkeypatch.setitem(cmd_preflight._CHECKERS, "boom", _raise_re_error)
    monkeypatch.setitem(cmd_preflight._CHECKERS, "fails", _violating)
    monkeypatch.setitem(cmd_preflight._CHECKERS, "clean", _clean)
    markers: list[str] = []

    result = cmd_preflight._check_fitness(conn=None, root=".", target_paths={"app/handlers.py"}, warnings_out=markers)

    statuses = {d["name"]: d["status"] for d in result["rule_details"]}
    assert statuses == {"broken": "ERROR", "healthy-fail": "FAIL", "healthy-pass": "PASS"}
    assert result["rules_checked"] == 3, "rules_checked counts rules ATTEMPTED"
    assert result["rules_evaluated"] == 2
    assert result["rules_errored"] == 1
    assert result["rules_failed"] == 1, "an ERROR row must not inflate the FAIL count"
    assert result["failed_rules"] == ["healthy-fail"]
    assert len(markers) == 1


def test_errored_rule_is_absent_from_both_failure_name_lists(monkeypatch):
    """``failed_rules`` / ``failed_rules_on_siblings`` stay FAIL-only."""
    _install_rule(monkeypatch, _raise_re_error)

    result = cmd_preflight._check_fitness(conn=None, root=".", target_paths={"app/handlers.py"})

    assert result["failed_rules"] == []
    assert result["failed_rules_on_siblings"] == []
    assert result["errored_rules"] == [_RULE_NAME]
    # The four-way consistency W-dogfood-K pinned still holds: every FAIL row
    # in rule_details is named by exactly one of the two failure lists.
    fail_names = sorted(d["name"] for d in result["rule_details"] if d["status"] == "FAIL")
    assert sorted(result["failed_rules"] + result["failed_rules_on_siblings"]) == fail_names


def test_warnings_out_is_optional(monkeypatch):
    """Callers that pass no bucket still get the ERROR row, and no crash."""
    _install_rule(monkeypatch, _raise_re_error)

    result = cmd_preflight._check_fitness(conn=None, root=".", target_paths={"app/handlers.py"})

    assert result["rules_errored"] == 1
    assert result["rule_details"][0]["status"] == "ERROR"


# --- negative controls: healthy rules keep their honest verdicts ------------


def test_negative_control_clean_checker_still_passes(monkeypatch):
    """NEGATIVE CONTROL — a rule that genuinely passes still reports PASS."""
    _install_rule(monkeypatch, _clean)
    markers: list[str] = []

    result = cmd_preflight._check_fitness(conn=None, root=".", target_paths={"app/handlers.py"}, warnings_out=markers)

    (detail,) = result["rule_details"]
    assert detail["status"] == "PASS"
    assert detail["violations"] == 0, "a rule that RAN and found nothing reports a measured zero"
    assert result["rules_errored"] == 0
    assert result["errored_rules"] == []
    assert result["rules_evaluated"] == 1
    assert result["severity"] == "OK"
    assert markers == [], "a clean run must not emit a degradation marker"


def test_negative_control_violating_checker_still_fails(monkeypatch):
    """NEGATIVE CONTROL — a genuine violation is still a FAIL, not an ERROR."""
    _install_rule(monkeypatch, _violating)

    result = cmd_preflight._check_fitness(conn=None, root=".", target_paths={"app/handlers.py"})

    (detail,) = result["rule_details"]
    assert detail["status"] == "FAIL"
    assert detail["violations"] == 1
    assert result["rules_errored"] == 0
    assert result["severity"] == "WARNING"


def test_negative_control_no_rules_configured_stays_ok(monkeypatch):
    """NEGATIVE CONTROL — the zero-rule state was already disclosed; keep it."""
    monkeypatch.setattr(cmd_preflight, "_load_rules", lambda root: [])

    result = cmd_preflight._check_fitness(conn=None, root=".", target_paths={"app/handlers.py"})

    assert result["rules_checked"] == 0
    assert result["rules_errored"] == 0
    assert result["rules_evaluated"] == 0
    assert result["severity"] == "OK"


# ---------------------------------------------------------------------------
# 2. End-to-end CLI — the shipped JSON envelope and the shipped text.
# ---------------------------------------------------------------------------


def test_broken_rule_json_envelope_discloses_instead_of_passing(cli_runner, preflight_project):
    _write_rules(preflight_project, _BROKEN_PATTERN)

    env = _preflight_json(cli_runner, preflight_project)
    fitness = env["fitness"]

    assert fitness["rules_errored"] == 1
    assert fitness["errored_rules"] == [_RULE_NAME]
    assert fitness["rules_evaluated"] == 0
    assert fitness["severity"] != "OK"
    assert [d["status"] for d in fitness["rule_details"]] == ["ERROR"]
    assert "PASS" not in [d["status"] for d in fitness["rule_details"]]
    assert fitness["rule_details"][0]["violations"] is None

    markers = env["summary"]["warnings_out"]
    assert any(m.startswith("preflight_fitness_rule_failed:") for m in markers)
    assert env["summary"]["partial_success"] is True
    # Mirrored at the top level, like every other preflight marker bucket.
    assert env["warnings_out"] == markers


def test_broken_rule_text_never_claims_all_rules_pass(cli_runner, preflight_project):
    _write_rules(preflight_project, _BROKEN_PATTERN)

    result = invoke_cli(cli_runner, ["preflight", "handle_request"], cwd=preflight_project)
    assert result.exit_code == 0, result.output

    fitness_line = next(line for line in result.stdout.splitlines() if line.strip().startswith("Fitness:"))
    assert "all 1 rules pass" not in fitness_line
    assert "[OK]" not in fitness_line
    assert "could not be evaluated" in fitness_line
    assert _RULE_NAME in fitness_line
    # W1331 — the marker a JSON reader gets must reach the human too (stderr,
    # so stdout stays byte-identical).
    assert "preflight_fitness_rule_failed:" in result.stderr


def test_negative_control_healthy_rule_json_still_passes(cli_runner, preflight_project):
    """NEGATIVE CONTROL — the same project with a valid regex is untouched."""
    _write_rules(preflight_project, _HEALTHY_PATTERN)

    env = _preflight_json(cli_runner, preflight_project)
    fitness = env["fitness"]

    assert fitness["rules_checked"] == 1
    assert fitness["rules_evaluated"] == 1
    assert fitness["rules_errored"] == 0
    assert fitness["errored_rules"] == []
    assert [d["status"] for d in fitness["rule_details"]] == ["PASS"]
    assert fitness["rule_details"][0]["violations"] == 0
    assert fitness["severity"] == "OK"
    assert env["summary"].get("partial_success") is False
    assert "warnings_out" not in env


def test_negative_control_healthy_rule_text_still_says_all_pass(cli_runner, preflight_project):
    """NEGATIVE CONTROL — the healthy text line is byte-identical to pre-fix."""
    _write_rules(preflight_project, _HEALTHY_PATTERN)

    result = invoke_cli(cli_runner, ["preflight", "handle_request"], cwd=preflight_project)
    assert result.exit_code == 0, result.output

    fitness_line = next(line for line in result.stdout.splitlines() if line.strip().startswith("Fitness:"))
    assert fitness_line == f"  Fitness:          {'all 1 rules pass':<40s} [OK]"
    assert "could not be evaluated" not in result.output
    assert "# warning:" not in result.output


# ---------------------------------------------------------------------------
# 3. Structural guard — the floor must not come back.
# ---------------------------------------------------------------------------


def test_checker_exception_handler_never_floors_violations_to_empty():
    """``except (...): violations = []`` inside ``_check_fitness`` is the defect.

    Pinned structurally (the W1332 detectors guard's shape) because the floor
    is a one-line edit away and reads as harmless.
    """
    source = Path("src/roam/commands/cmd_preflight.py").read_text(encoding="utf-8")
    func = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "_check_fitness"
    )

    offenders = []
    for handler in (n for n in ast.walk(func) if isinstance(n, ast.ExceptHandler)):
        for stmt in ast.walk(handler):
            if not isinstance(stmt, ast.Assign):
                continue
            if not isinstance(stmt.value, (ast.List, ast.Tuple)) or stmt.value.elts:
                continue
            names = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            if "violations" in names:
                offenders.append(stmt.lineno)

    assert offenders == [], f"a raising checker is floored to an empty result at cmd_preflight.py lines {offenders}"
