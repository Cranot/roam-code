"""Regression — preflight Fitness sibling-failure names must agree with rule_details.

Cosmetic-defect fix (dogfood scan): ``roam preflight <sym>`` printed

    Fitness: target passes; 2 rule(s) fail on sibling symbols ()

with a hollow ``()`` where sibling-symbol rule names belong, and the JSON
``fitness.failed_rules`` was ``[]`` even though ``fitness.rules_failed`` was 2
and ``fitness.rule_details`` listed 2 FAIL rows.

Root cause: ``failed_rules`` is target-attributed BY DESIGN (W-dogfood-K) —
it lists only rules the *target* violates (``violations_on_target > 0``), and is
legitimately empty when the target itself is clean. The sibling-only text
branch wrongly reused ``failed_rules`` to name sibling failures, so it rendered
empty parens; the JSON exposed the same empty list against a non-zero count.

Fix: ``_check_fitness`` now also returns ``failed_rules_on_siblings`` (the rule
names of FAIL rows with ``violations_on_target == 0`` and
``violations_on_siblings > 0``). The text branch names sibling failures from
that list and drops the parenthetical entirely when there are no names;
``failed_rules`` stays accurately target-scoped.

These tests pin the four-way consistency:
``rules_failed`` / ``rule_details`` / ``failed_rules`` / ``failed_rules_on_siblings``.
"""

from __future__ import annotations

import pytest

from roam.commands import cmd_preflight


def _fake_violation(source: str) -> dict:
    """A fitness-checker violation dict keyed by its ``source`` path."""
    return {"source": source, "message": "synthetic violation"}


def test_check_fitness_sibling_only_failure_names_match_rule_details(monkeypatch):
    """A rule failing ONLY on sibling files appears in failed_rules_on_siblings.

    ``failed_rules`` must stay empty (target-attributed, target is clean);
    ``failed_rules_on_siblings`` must name the rule; both must agree with the
    FAIL rows in ``rule_details``.
    """
    target_file = "src/target.py"
    sibling_file = "src/sibling.py"

    rules = [
        {"name": "No cycles", "type": "synthetic"},
        {"name": "Health score above 60", "type": "synthetic"},
    ]

    # "No cycles" fails on a sibling file only; the health rule passes.
    def _checker(rule, conn):
        if rule["name"] == "No cycles":
            return [_fake_violation(f"{sibling_file}:10")]
        return []

    monkeypatch.setattr(cmd_preflight, "_load_rules", lambda root: rules)
    monkeypatch.setitem(cmd_preflight._CHECKERS, "synthetic", _checker)

    result = cmd_preflight._check_fitness(conn=None, root=".", target_paths={target_file})

    # Counts: one rule FAILs, attributed entirely to a sibling.
    assert result["rules_failed"] == 1
    assert result["rules_failing_on_target"] == 0
    assert result["rules_failing_on_siblings"] == 1

    # failed_rules is target-attributed — accurately empty, NOT faked.
    assert result["failed_rules"] == []

    # failed_rules_on_siblings carries the sibling-failure name.
    assert result["failed_rules_on_siblings"] == ["No cycles"]

    # The two lists together must reproduce every FAIL row in rule_details.
    fail_names = sorted(r["name"] for r in result["rule_details"] if r["status"] == "FAIL")
    consistency = sorted(result["failed_rules"] + result["failed_rules_on_siblings"])
    assert consistency == fail_names == ["No cycles"]
    # rules_failed count must equal the rule_details FAIL-row count.
    assert result["rules_failed"] == len(fail_names)


def test_check_fitness_target_failure_still_uses_failed_rules(monkeypatch):
    """A rule failing ON the target stays in failed_rules, not the sibling list."""
    target_file = "src/target.py"

    rules = [{"name": "Max function complexity 25", "type": "synthetic"}]

    def _checker(rule, conn):
        return [_fake_violation(f"{target_file}:42")]

    monkeypatch.setattr(cmd_preflight, "_load_rules", lambda root: rules)
    monkeypatch.setitem(cmd_preflight._CHECKERS, "synthetic", _checker)

    result = cmd_preflight._check_fitness(conn=None, root=".", target_paths={target_file})

    assert result["rules_failing_on_target"] == 1
    assert result["rules_failing_on_siblings"] == 0
    assert result["failed_rules"] == ["Max function complexity 25"]
    assert result["failed_rules_on_siblings"] == []

    fail_names = sorted(r["name"] for r in result["rule_details"] if r["status"] == "FAIL")
    consistency = sorted(result["failed_rules"] + result["failed_rules_on_siblings"])
    assert consistency == fail_names


def test_check_fitness_no_rules_emits_empty_sibling_list(monkeypatch):
    """The no-rules early return carries failed_rules_on_siblings for shape parity."""
    monkeypatch.setattr(cmd_preflight, "_load_rules", lambda root: [])

    result = cmd_preflight._check_fitness(conn=None, root=".", target_paths={"src/x.py"})

    assert result["failed_rules"] == []
    assert result["failed_rules_on_siblings"] == []
    assert result["rules_failed"] == 0


def test_check_fitness_reraises_unexpected_checker_errors(monkeypatch):
    """Unexpected checker bugs must reach the outer preflight marker boundary."""
    rules = [{"name": "Synthetic rule", "type": "synthetic"}]

    def _checker(rule, conn):
        raise RuntimeError("synthetic unexpected checker failure")

    monkeypatch.setattr(cmd_preflight, "_load_rules", lambda root: rules)
    monkeypatch.setitem(cmd_preflight._CHECKERS, "synthetic", _checker)

    with pytest.raises(RuntimeError, match="synthetic unexpected checker failure"):
        cmd_preflight._check_fitness(conn=None, root=".", target_paths={"src/x.py"})


def test_fitness_text_branch_never_prints_hollow_parens():
    """The Fitness text line drops the parenthetical when no names are available.

    Mirrors the ``_with_names`` helper inside ``cmd_preflight``: a sibling
    failure with no resolvable names must render WITHOUT a trailing ``()``,
    and with names must render ``(a, b)``.
    """

    def _with_names(text: str, names: list[str]) -> str:
        shown = [n for n in names[:3] if n]
        return f"{text} ({', '.join(shown)})" if shown else text

    # No names -> no parenthetical at all (the defect was a hollow "()").
    assert _with_names("target passes; 2 rule(s) fail on sibling symbols", []) == (
        "target passes; 2 rule(s) fail on sibling symbols"
    )
    assert "()" not in _with_names("2 rule(s) fail", [])
    assert "()" not in _with_names("2 rule(s) fail", ["", ""])

    # With names -> parenthetical lists them, capped at 3.
    rendered = _with_names("2 rule(s) fail on sibling symbols", ["No cycles", "Max complexity 25"])
    assert rendered == "2 rule(s) fail on sibling symbols (No cycles, Max complexity 25)"
    capped = _with_names("fail", ["a", "b", "c", "d"])
    assert capped == "fail (a, b, c)"
