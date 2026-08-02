"""W1450 — a fitness rule whose ``type`` no checker handles must never read as clean.

The defect (MEASURED on pre-fix HEAD)
-------------------------------------
``.roam/fitness.yaml`` with two eligible rules::

    - name: "Handlers must not reach into db"
      type: layer          # the term the module docstring ADVERTISED
    - name: "Services must not import controllers"
      type: dependancy     # a one-letter typo for "dependency"

produced::

    exit_code: 0
    summary: {"verdict": "all 0 fitness rule(s) pass", "rules_checked": 0,
              "passed": 0, "failed": 0, "partial_success": false}
    rules:   []

``_run_fitness_rules`` did ``checker = _CHECKERS.get(rule.get("type", ""))``
followed by ``if checker is None: continue`` — so an unrecognised type
vanished from the numerator AND the denominator (``rules_checked =
len(rule_results)``). The loader validated only that a rule was a mapping;
``type`` was never checked against the dispatchable set, so the two halves
were free to disagree. The user wrote two rules, the tool enforced none,
and CI exited 0 on a green verdict.

The fix, and why it fails CLOSED
--------------------------------
An unknown ``type`` is almost always a typo, and a typo that silently
disables enforcement is the worst available outcome: the gate the user
believes is on is off, and nothing anywhere says so. W1332 doctrine
("an uncomputable signal must not be floored to a clean-looking value")
plus AGENTS.md Pattern-2 ("never emit a clean verdict when the underlying
check didn't run") both point the same way — but disclosure ALONE is not
enough here, because the load-bearing signal for a CI gate is the exit
code. So W1450 does both:

1. the rule is counted (``rules_checked`` accounts for every rule the
   user wrote) and reported ``status: "ERROR"`` — never ``PASS``,
   and never ``FAIL`` either, which would claim it ran and was violated;
2. the run exits 1, ahead of the baseline branches: a config error is not
   accepted architectural debt, so ``--write-baseline`` cannot bless a
   typo into permanent silence.

Structural anti-drift
---------------------
The loader and the dispatcher now decide "known type?" through the SAME
function (``_resolve_checker``), reading the SAME registry (``_CHECKERS``).
There is deliberately no second hardcoded list of type names. The
``test_single_source_of_truth_*`` tests prove that behaviourally by
mutating ``_CHECKERS`` and observing BOTH halves follow.

NEGATIVE CONTROL: ``test_known_rule_types_still_pass_and_still_count`` and
``test_mixed_config_still_evaluates_the_valid_rule`` — a fix that simply
made everything loud cannot pass this file.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands import cmd_fitness
from tests.conftest import git_init, index_in_process, invoke_cli

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_UNKNOWN_RULES_YAML = """rules:
  - name: "Handlers must not reach into db"
    type: layer
    from: "src/handlers/**"
    to: "src/db/**"
    allow: false

  - name: "Services must not import controllers"
    type: dependancy
    from: "**/services/**"
    to: "**/controllers/**"
    allow: false
"""

# Two rules a one-file project satisfies — the negative control.
_KNOWN_RULES_YAML = """rules:
  - name: "No cycles"
    type: metric
    metric: cycles
    max: 0

  - name: "Health score above zero"
    type: metric
    metric: health_score
    min: 0
"""

_MIXED_RULES_YAML = """rules:
  - name: "No cycles"
    type: metric
    metric: cycles
    max: 0

  - name: "Services must not import controllers"
    type: dependancy
    from: "**/services/**"
    to: "**/controllers/**"
    allow: false
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """A minimal indexed project; each test rewrites ``.roam/fitness.yaml``."""
    proj = tmp_path_factory.mktemp("w1450_fitness")
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text('def main():\n    """Entry point."""\n    return 0\n')
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


def _write_rules(proj: Path, body: str) -> None:
    cfg_dir = proj / ".roam"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "fitness.yaml").write_text(body, encoding="utf-8")
    baseline = cfg_dir / "fitness-baseline.json"
    if baseline.exists():
        baseline.unlink()


def _extract_envelope(output: str) -> dict:
    """Pull the trailing JSON envelope out of mixed stdout."""
    lines = output.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].startswith("{"):
            return _json.loads("\n".join(lines[idx:]))
    raise AssertionError(f"no JSON envelope found in output:\n{output}")


def _run_fitness(proj: Path, *args: str, json_mode: bool = True):
    result = invoke_cli(CliRunner(), ["fitness", *args], cwd=proj, json_mode=json_mode)
    return result


# ---------------------------------------------------------------------------
# 1. The defect: an unrecognised ``type`` must not read as clean.
# ---------------------------------------------------------------------------


def test_unknown_rule_type_never_verdicts_as_a_pass(project: Path) -> None:
    """Two unenforceable rules must NOT produce "all 0 fitness rule(s) pass"."""
    _write_rules(project, _UNKNOWN_RULES_YAML)
    result = _run_fitness(project)
    summary = _extract_envelope(result.output)["summary"]

    verdict = summary["verdict"]
    assert "all 0 fitness rule(s) pass" not in verdict, (
        f"pre-W1450 false-clean: two rules the tool cannot enforce verdicted as a pass: {verdict!r}"
    )
    assert "pass" not in verdict.split(";")[0], (
        f"the leading clause of the verdict must state the unevaluated rules, got {verdict!r}"
    )
    assert "NOT evaluated" in verdict, f"verdict must name the unevaluated rules, got {verdict!r}"


def test_unknown_rule_type_counts_in_the_denominator(project: Path) -> None:
    """``rules_checked`` must account for EVERY rule the user wrote."""
    _write_rules(project, _UNKNOWN_RULES_YAML)
    summary = _extract_envelope(_run_fitness(project).output)["summary"]

    assert summary["rules_checked"] == 2, (
        f"the user wrote 2 rules; rules_checked={summary['rules_checked']} — "
        f"an unenforceable rule vanished from the denominator"
    )
    assert summary["rules_unevaluated"] == 2
    assert summary["unknown_rule_types"] == ["dependancy", "layer"]


def test_unknown_rule_type_exits_non_zero(project: Path) -> None:
    """CI must fail: the gate the user believes is on is off."""
    _write_rules(project, _UNKNOWN_RULES_YAML)
    result = _run_fitness(project)
    assert result.exit_code == 1, (
        f"fitness exited {result.exit_code} with 0 of 2 rules enforced — CI would report green"
    )


def test_unknown_rule_type_sets_the_machine_gate(project: Path) -> None:
    """The envelope must carry isError/status so agents cannot branch "clean"."""
    _write_rules(project, _UNKNOWN_RULES_YAML)
    envelope = _extract_envelope(_run_fitness(project).output)

    assert envelope.get("isError") is True
    assert envelope.get("status") == "partial_failure"
    assert envelope.get("error_code") == "PARTIAL_FAILURE"
    assert envelope["summary"]["partial_success"] is True


def test_unknown_rule_type_rows_are_error_not_pass(project: Path) -> None:
    """Each unenforceable rule appears as its own ERROR row with an explanation."""
    _write_rules(project, _UNKNOWN_RULES_YAML)
    rules = _extract_envelope(_run_fitness(project).output)["rules"]

    assert len(rules) == 2, f"both rules must appear in the rules array, got {rules!r}"
    assert {r["status"] for r in rules} == {"ERROR"}
    assert {r["name"] for r in rules} == {
        "Handlers must not reach into db",
        "Services must not import controllers",
    }
    typo_row = next(r for r in rules if r["type"] == "dependancy")
    assert "dependency" in typo_row["error"], (
        f"the message must name the closed type set (and ideally the near-miss), got {typo_row['error']!r}"
    )


def test_unknown_rule_type_warns_from_the_loader(project: Path) -> None:
    """The loader disclosure (W1051 carrier) names the file, the rule and the type."""
    _write_rules(project, _UNKNOWN_RULES_YAML)
    summary = _extract_envelope(_run_fitness(project).output)["summary"]

    warnings = summary.get("warnings_out", [])
    assert len(warnings) == 2, f"one warning per unenforceable rule expected, got {warnings!r}"
    joined = " ".join(warnings)
    assert "fitness.yaml" in joined
    assert "'layer'" in joined and "'dependancy'" in joined
    assert "dependency, metric, naming, trend" in joined, (
        f"the warning must enumerate the known types, got {warnings!r}"
    )


def test_text_mode_agrees_with_json_mode(project: Path) -> None:
    """Text mode must not print a green verdict the JSON mode calls an error."""
    _write_rules(project, _UNKNOWN_RULES_YAML)
    result = _run_fitness(project, json_mode=False)

    assert result.exit_code == 1
    assert "all 0 fitness rule(s) pass" not in result.output
    assert "NOT evaluated" in result.output
    assert result.output.count("[ERROR]") == 2
    assert "Fitness check: 2 rules" in result.output


# ---------------------------------------------------------------------------
# 2. NEGATIVE CONTROLS — a fix that just makes everything loud must fail here.
# ---------------------------------------------------------------------------


def test_known_rule_types_still_pass_and_still_count(project: Path) -> None:
    """NEGATIVE CONTROL: valid rules are still checked, still pass, still exit 0."""
    _write_rules(project, _KNOWN_RULES_YAML)
    result = _run_fitness(project)
    summary = _extract_envelope(result.output)["summary"]

    assert result.exit_code == 0, f"a clean config must still exit 0:\n{result.output}"
    assert summary["verdict"].startswith("all 2 fitness rule(s) pass")
    assert summary["rules_checked"] == 2
    assert summary["passed"] == 2
    assert summary["failed"] == 0
    # The unevaluated fields must be ABSENT on a clean run — no envelope churn.
    assert "rules_unevaluated" not in summary
    assert "unknown_rule_types" not in summary
    assert not summary.get("warnings_out")


def test_known_rule_types_carry_no_machine_gate(project: Path) -> None:
    """NEGATIVE CONTROL: a clean run must NOT set isError/status/partial_success."""
    _write_rules(project, _KNOWN_RULES_YAML)
    envelope = _extract_envelope(_run_fitness(project).output)

    assert envelope.get("isError") in (None, False)
    assert envelope.get("status") in (None, "success", "ok")
    assert envelope["summary"].get("partial_success") in (None, False)
    assert {r["status"] for r in envelope["rules"]} == {"PASS"}


def test_mixed_config_still_evaluates_the_valid_rule(project: Path) -> None:
    """NEGATIVE CONTROL: one typo must not disable the rules that DO work."""
    _write_rules(project, _MIXED_RULES_YAML)
    result = _run_fitness(project)
    envelope = _extract_envelope(result.output)
    summary = envelope["summary"]

    assert result.exit_code == 1
    assert summary["rules_checked"] == 2
    assert summary["passed"] == 1, "the valid `metric` rule must still be evaluated and still pass"
    assert summary["rules_unevaluated"] == 1
    by_name = {r["name"]: r for r in envelope["rules"]}
    assert by_name["No cycles"]["status"] == "PASS"
    assert by_name["Services must not import controllers"]["status"] == "ERROR"


# ---------------------------------------------------------------------------
# 3. The denominator invariant + the unit-level dispatch contract.
# ---------------------------------------------------------------------------


def test_every_rule_lands_in_exactly_one_bucket() -> None:
    """``passed + failed + errored == rules_checked == len(rules)``. Always."""
    rules = [
        {"name": "typo", "type": "dependancy"},
        {"name": "advertised-but-absent", "type": "layer"},
        {"name": "no type at all"},
        {"name": "non-string type", "type": 7},
    ]
    rule_results, violations = cmd_fitness._run_fitness_rules(None, rules)
    passed, failed, errored = cmd_fitness._rule_counts(rule_results)

    assert len(rule_results) == len(rules), (
        f"{len(rules)} rules in, {len(rule_results)} rule_results out — a rule was dropped"
    )
    assert passed + failed + errored == len(rule_results)
    assert (passed, failed, errored) == (0, 0, 4)
    # An unevaluable rule is NOT a violation: routing it through
    # all_violations would let --write-baseline bless the typo.
    assert violations == []


def test_missing_type_is_disclosed_as_missing() -> None:
    """A rule with no ``type`` key at all is equally unenforceable."""
    rule_results, _ = cmd_fitness._run_fitness_rules(None, [{"name": "typeless"}])
    assert rule_results[0]["status"] == "ERROR"
    assert "no `type`" in rule_results[0]["error"]
    assert cmd_fitness._unevaluable_rule_types(rule_results) == ["<missing>"]


def test_reason_and_link_survive_on_an_error_row() -> None:
    """Documentation fields must not be lost just because the type is unknown."""
    rule = {"name": "r", "type": "nope", "reason": "because", "link": "https://example.test"}
    rule_results, _ = cmd_fitness._run_fitness_rules(None, [rule])
    assert rule_results[0]["reason"] == "because"
    assert rule_results[0]["link"] == "https://example.test"


# ---------------------------------------------------------------------------
# 4. Structural anti-drift: loader and dispatcher read ONE registry.
# ---------------------------------------------------------------------------


def test_single_source_of_truth_registering_a_checker_teaches_the_loader(tmp_path, monkeypatch) -> None:
    """A type added to ``_CHECKERS`` is accepted by the LOADER with no second list to update."""
    monkeypatch.setitem(cmd_fitness._CHECKERS, "synthetic", lambda rule, conn: [])
    (tmp_path / ".roam").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".roam" / "fitness.yaml").write_text(
        'rules:\n  - name: "Synthetic"\n    type: synthetic\n',
        encoding="utf-8",
    )

    warnings_out: list[str] = []
    rules = cmd_fitness._load_rules(tmp_path, warnings_out=warnings_out)

    assert warnings_out == [], (
        f"the loader rejected a type the dispatcher CAN run — it is reading a second list: {warnings_out!r}"
    )
    rule_results, _ = cmd_fitness._run_fitness_rules(None, rules)
    assert [r["status"] for r in rule_results] == ["PASS"]


def test_single_source_of_truth_removing_a_checker_teaches_the_loader(tmp_path, monkeypatch) -> None:
    """Drop a checker and the LOADER must reject that type too — no stale allowlist."""
    monkeypatch.delitem(cmd_fitness._CHECKERS, "trend")
    (tmp_path / ".roam").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".roam" / "fitness.yaml").write_text(
        'rules:\n  - name: "Health must not regress"\n    type: trend\n    metric: health_score\n',
        encoding="utf-8",
    )

    warnings_out: list[str] = []
    rules = cmd_fitness._load_rules(tmp_path, warnings_out=warnings_out)

    assert warnings_out, "the loader still accepted a type no checker handles — it is reading a second list"
    assert "'trend'" in warnings_out[0]
    rule_results, _ = cmd_fitness._run_fitness_rules(None, rules)
    assert [r["status"] for r in rule_results] == ["ERROR"]


def test_loader_and_dispatcher_share_the_membership_test() -> None:
    """Both halves route through ``_resolve_checker`` over ``_CHECKERS``."""
    for rule_type in cmd_fitness._CHECKERS:
        rule = {"name": rule_type, "type": rule_type}
        assert cmd_fitness._unknown_rule_type(rule) is None
        assert cmd_fitness._resolve_checker(rule) is cmd_fitness._CHECKERS[rule_type]
    assert cmd_fitness._known_rule_types() == tuple(sorted(cmd_fitness._CHECKERS))
    assert cmd_fitness._unknown_rule_type({"type": "layer"}) == "layer"


# ---------------------------------------------------------------------------
# 5. Fail-closed: baseline mode cannot bless a config typo into silence.
# ---------------------------------------------------------------------------


def test_write_baseline_cannot_silence_an_unknown_type(project: Path) -> None:
    """``--write-baseline`` normally exits 0; a config error still exits 1."""
    _write_rules(project, _UNKNOWN_RULES_YAML)
    result = _run_fitness(project, "--write-baseline")
    assert result.exit_code == 1, (
        "a typo'd rule type was baselined away — the gate stays off and CI stays green:\n" + result.output
    )


def test_baseline_comparison_cannot_silence_an_unknown_type(project: Path) -> None:
    """A ``--baseline`` run cannot be trusted while part of the config never ran."""
    _write_rules(project, _KNOWN_RULES_YAML)
    assert _run_fitness(project, "--write-baseline").exit_code == 0
    baseline = project / ".roam" / "fitness-baseline.json"
    assert baseline.exists()

    cfg = project / ".roam" / "fitness.yaml"
    cfg.write_text(_UNKNOWN_RULES_YAML, encoding="utf-8")
    result = _run_fitness(project, "--baseline", str(baseline))
    assert result.exit_code == 1, "an unevaluated rule must fail closed ahead of the baseline branch"
    baseline.unlink()
