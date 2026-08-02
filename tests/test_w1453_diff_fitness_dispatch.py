"""W1453 — ``roam diff --fitness`` reported unhandled rule types as PASS.

Third and worst instance of the W1450 defect class (unknown rule ``type``
disabled the rule). ``cmd_fitness`` DROPPED such a rule (W1450 fixed it);
``cmd_preflight`` also dropped it (W1453 sibling file). ``cmd_diff`` did
something strictly worse — it ASSERTED the rule had been checked::

    for rule in rules:
        rtype = rule.get("type", "")
        violations = []                       # <-- the fall-through
        if rtype == "dependency":   ...
        elif rtype == "metric":     ...
        elif rtype == "naming":     ...
        status = "PASS" if not violations else "FAIL"

Two distinct rules fell through that chain, not one:

1. a typo'd / unknown ``type`` — the W1450 shape; and
2. ``trend`` — a **registered checker in ``cmd_fitness._CHECKERS``** that
   this if/elif simply never learned about. A user's trend rule had never
   once been enforced by ``roam diff --fitness``.

MEASURED on one project, one ``.roam/fitness.yaml``, one index — four
rules, ``health_score`` seeded 95/94/96 then 40:

    roam fitness           -> health-must-not-regress  FAIL (1 violation)
                              "health_score dropped by 55.0"
    roam diff --fitness    -> {"name": "health-must-not-regress",
                               "type": "trend", "status": "PASS",
                               "violations": 0}
                              header: "4 rules, 3 passed, 1 failed", exit 0

Same rule, same checker, opposite verdicts — and the wrong one is the
affirmative claim.

Fix is structural, not a fourth ``elif``. ``_SCOPED_CHECKERS`` is a
NARROWING table (a subset of ``_CHECKERS``), and the membership test is
``cmd_fitness._resolve_checker`` — the single one W1450 installed. The
fall-through inverts:

* registered type, no scoped variant -> run the GLOBAL checker
  (``scope: "repository"``). Honest over-report, visible + correctable.
* type no checker handles            -> ``_unevaluable_rule_entry`` ERROR row
  (``scope: "not_evaluated"``) + ``diff_fitness_rule_unevaluable:`` marker
  + ``summary.fitness_rules_unevaluated`` + ``partial_success``.

DISCLOSE, do not fail closed — deliberately unlike W1450's ``roam fitness``.
Pinned by ``test_a_genuinely_failing_rule_still_exits_zero``: ``roam diff``
already exits 0 on a real architectural violation, so exiting 1 on a config
typo would rank the typo ABOVE the violation it was meant to catch. ``diff``
is an informational blast-radius report; ``roam fitness`` is the gate and
fails closed there. ``_collect_fitness_violations`` is additionally a
library function ``cmd_attest._collect_fitness_evidence`` calls behind
``except Exception`` — a ``SystemExit`` (a ``BaseException``) raised inside
it would escape that guard and take ``roam attest`` down.

NEGATIVE CONTROLS carried throughout: a ``dependency`` rule must keep
FAILING and a ``naming`` rule must keep PASSING in every scenario, and
``trend`` must be genuinely CHECKED (a seeded regression must produce a
FAIL with the violation payload) rather than merely not-crashed.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest
from click.testing import CliRunner

from roam.commands import cmd_diff
from tests.conftest import git_init, index_in_process, invoke_cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MODELS_SRC = "class User:\n    def __init__(self, name):\n        self.name = name\n"
_AUTH_SRC = "from src.models import User\n\n\ndef verify_token(t):\n    return User('test')\n"

# Four rules, one file. Two are negative controls with known-good types:
# the dependency rule MUST keep failing (src/auth.py -> src/models.py is a
# real edge in a changed file) and the naming rule MUST keep passing.
_RULES_YAML = """rules:
  - name: no-auth-to-models
    type: dependency
    from: "src/auth.py"
    to: "src/models.py"
    allow: false
  - name: funcs-are-snake-case
    type: naming
    kind: function
    pattern: "^[a-z_][a-z0-9_]*$"
  - name: health-must-not-regress
    type: trend
    metric: health_score
    window: 3
    max_decrease: 5
  - name: typoed-rule
    type: dependancy
    from: "src/**"
    to: "src/**"
    allow: false
"""

_DEP_RULE = "no-auth-to-models"
_NAMING_RULE = "funcs-are-snake-case"
_TREND_RULE = "health-must-not-regress"
_UNKNOWN_RULE = "typoed-rule"


@pytest.fixture
def cli_runner():
    return CliRunner()


def _seed_health_regression(proj) -> None:
    """Three healthy snapshots then a crash — a real, checkable trend violation.

    The trend checker needs >= 2 snapshot rows; the latest must sit more than
    ``max_decrease`` below the average of the preceding ``window``.
    """
    conn = sqlite3.connect(str(proj / ".roam" / "index.db"))
    try:
        now = int(time.time())
        conn.execute("DELETE FROM snapshots")
        for offset, score in ((300, 95), (200, 94), (100, 96), (0, 40)):
            conn.execute(
                "INSERT INTO snapshots (timestamp, source, health_score) VALUES (?, ?, ?)",
                (now - offset, "w1453-test", score),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def diff_project(tmp_path, monkeypatch):
    """Indexed git corpus with an uncommitted edit, rules, and seeded snapshots."""
    proj = tmp_path / "w1453_diff_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "models.py").write_text(_MODELS_SRC, encoding="utf-8")
    (src / "auth.py").write_text(_AUTH_SRC, encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"

    # Uncommitted edit so `roam diff` sees src/auth.py as changed — this is
    # what makes the dependency negative control fire.
    (src / "auth.py").write_text(_AUTH_SRC.replace("return User", "# tweak\n    return User"), encoding="utf-8")

    (proj / ".roam").mkdir(exist_ok=True)
    (proj / ".roam" / "fitness.yaml").write_text(_RULES_YAML, encoding="utf-8")
    _seed_health_regression(proj)
    return proj


def _diff_json(cli_runner, proj) -> dict:
    result = invoke_cli(cli_runner, ["diff", "--fitness"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _rules_by_name(payload: dict) -> dict[str, dict]:
    rows = payload["fitness_violations"]["rules"]
    return {r["name"]: r for r in rows}


# ---------------------------------------------------------------------------
# 0. The divergence itself — diff must not contradict fitness on one rule file.
# ---------------------------------------------------------------------------


def test_trend_rule_verdict_agrees_between_fitness_and_diff(cli_runner, diff_project):
    """The headline bug: ``roam fitness`` said FAIL, ``roam diff`` said PASS.

    Pinned as an AGREEMENT assertion rather than a literal, so a future change
    to either command's trend semantics keeps the two honest with each other.
    """
    fitness_result = invoke_cli(cli_runner, ["fitness"], cwd=diff_project, json_mode=True)
    fitness_rows = {r["name"]: r for r in json.loads(fitness_result.output)["rules"]}

    diff_rows = _rules_by_name(_diff_json(cli_runner, diff_project))

    assert fitness_rows[_TREND_RULE]["status"] == "FAIL", (
        "fixture precondition broken — the seeded health_score regression must "
        f"make `roam fitness` fail the trend rule, got {fitness_rows[_TREND_RULE]}"
    )
    assert diff_rows[_TREND_RULE]["status"] == fitness_rows[_TREND_RULE]["status"], (
        "`roam diff --fitness` and `roam fitness` disagree on the SAME trend rule, "
        f"same rule file, same index: diff={diff_rows[_TREND_RULE]} "
        f"fitness={fitness_rows[_TREND_RULE]}"
    )


# ---------------------------------------------------------------------------
# 1. trend is genuinely CHECKED, not merely not-crashed.
# ---------------------------------------------------------------------------


def test_trend_rule_is_reported_failing_not_passing(cli_runner, diff_project):
    row = _rules_by_name(_diff_json(cli_runner, diff_project))[_TREND_RULE]
    assert row["status"] == "FAIL", f"a registered `trend` checker was reported {row['status']!r}: {row}"
    assert row["violations"] >= 1, f"trend rule claimed FAIL with no violations: {row}"


def test_trend_violation_payload_reaches_the_envelope(cli_runner, diff_project):
    """Not-crashed is not checked. The measured numbers must be present.

    Guards against a "fix" that routes trend through the dispatcher but
    discards its findings.
    """
    payload = _diff_json(cli_runner, diff_project)
    trend_violations = [v for v in payload["fitness_violations"]["violations"] if v.get("type") == "trend"]
    assert trend_violations, (
        f"trend rule reported FAIL but contributed no violation payload: {payload['fitness_violations']}"
    )
    v = trend_violations[0]
    assert v["metric"] == "health_score"
    assert v["latest"] == 40, f"trend checker did not read the seeded snapshots: {v}"
    assert v["delta"] < -5, f"trend delta must breach max_decrease=5, got {v}"
    assert v["rule"] == _TREND_RULE


def test_trend_rule_counts_toward_fitness_rules_failed(cli_runner, diff_project):
    """A false PASS also under-counted the summary the agent branches on."""
    payload = _diff_json(cli_runner, diff_project)
    # dependency rule + trend rule.
    assert payload["summary"]["fitness_rules_failed"] == 2, (
        f"expected the dependency AND trend rules to be counted as failing: {payload['summary']}"
    )


def test_trend_row_discloses_that_it_was_not_diff_scoped(cli_runner, diff_project):
    """A repo-wide FAIL must not read as "this diff caused it".

    ``trend`` has no per-file projection to narrow to, so it runs globally.
    Saying so is the price of enforcing it here.
    """
    row = _rules_by_name(_diff_json(cli_runner, diff_project))[_TREND_RULE]
    assert row.get("scope") == "repository", f"trend row must disclose its evaluation scope: {row}"


# ---------------------------------------------------------------------------
# 2. Unknown rule type — ERROR, never PASS.
# ---------------------------------------------------------------------------


def test_unknown_rule_type_is_error_not_pass(cli_runner, diff_project):
    row = _rules_by_name(_diff_json(cli_runner, diff_project))[_UNKNOWN_RULE]
    assert row["status"] == "ERROR", (
        f"a rule type no checker handles was reported {row['status']!r} — the envelope "
        f"asserted a check that never happened: {row}"
    )


def test_unknown_rule_type_carries_the_shared_did_you_mean(cli_runner, diff_project):
    """Reuses ``cmd_fitness._unevaluable_rule_entry`` — not a third dialect."""
    row = _rules_by_name(_diff_json(cli_runner, diff_project))[_UNKNOWN_RULE]
    assert "dependancy" in row["error"]
    assert "Did you mean 'dependency'?" in row["error"], f"missing the shared difflib hint: {row}"
    assert row.get("scope") == "not_evaluated"


def test_unknown_rule_type_is_disclosed_on_the_summary(cli_runner, diff_project):
    summary = _diff_json(cli_runner, diff_project)["summary"]
    assert summary["fitness_rules_unevaluated"] == 1, summary
    assert summary["fitness_unknown_rule_types"] == ["dependancy"], summary
    assert summary["partial_success"] is True, (
        f"a rule nothing could evaluate must flip partial_success (W1332): {summary}"
    )


def test_unknown_rule_type_emits_a_diff_family_marker(cli_runner, diff_project):
    payload = _diff_json(cli_runner, diff_project)
    markers = [w for w in payload.get("warnings_out", []) if "fitness_rule_unevaluable" in w]
    assert markers, f"no unevaluable marker on the envelope: {payload.get('warnings_out')}"
    assert markers[0].startswith("diff_fitness_rule_unevaluable:"), (
        f"marker must stay in the ``diff_*`` family (marker-prefix discipline): {markers[0]!r}"
    )
    assert _UNKNOWN_RULE in markers[0]


def test_unevaluable_rule_is_not_relabelled_as_a_failure(cli_runner, diff_project):
    """ERROR and FAIL are different claims; collapsing them would lie the other way."""
    summary = _diff_json(cli_runner, diff_project)["summary"]
    # 2 = dependency + trend. The unevaluable rule must NOT be in here.
    assert summary["fitness_rules_failed"] == 2, summary
    assert summary["fitness_rules_unevaluated"] == 1, summary


# ---------------------------------------------------------------------------
# 3. NEGATIVE CONTROLS — a fix that just makes everything loud must fail.
# ---------------------------------------------------------------------------


def test_negative_control_known_type_that_passes_still_passes(cli_runner, diff_project):
    row = _rules_by_name(_diff_json(cli_runner, diff_project))[_NAMING_RULE]
    assert row["status"] == "PASS", f"a healthy naming rule must still PASS: {row}"
    assert row["violations"] == 0
    assert row.get("scope") == "changed_files", f"naming stays diff-narrowed: {row}"


def test_negative_control_known_type_that_fails_still_fails(cli_runner, diff_project):
    row = _rules_by_name(_diff_json(cli_runner, diff_project))[_DEP_RULE]
    assert row["status"] == "FAIL", f"a genuinely violated dependency rule must still FAIL: {row}"
    assert row["violations"] >= 1
    assert row.get("scope") == "changed_files", f"dependency stays diff-narrowed: {row}"


def test_negative_control_scoped_checkers_still_narrow_to_the_diff(cli_runner, tmp_path, monkeypatch):
    """The dependency rule must NOT fire when its source file is unchanged.

    This is the property the scoped variants exist for. Routing dispatch
    through ``_resolve_checker`` must not quietly promote every scoped rule
    to the global checker.
    """
    proj = tmp_path / "w1453_unchanged"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "models.py").write_text(_MODELS_SRC, encoding="utf-8")
    (src / "auth.py").write_text(_AUTH_SRC, encoding="utf-8")
    (src / "unrelated.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, out
    # Change a file that is NOT the dependency rule's source.
    (src / "unrelated.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    (proj / ".roam").mkdir(exist_ok=True)
    (proj / ".roam" / "fitness.yaml").write_text(
        'rules:\n  - name: no-auth-to-models\n    type: dependency\n    from: "src/auth.py"\n'
        '    to: "src/models.py"\n    allow: false\n',
        encoding="utf-8",
    )

    row = _rules_by_name(_diff_json(cli_runner, proj))["no-auth-to-models"]
    assert row["status"] == "PASS", (
        f"src/auth.py is unchanged, so its edge is out of diff scope — the scoped "
        f"dependency checker must still narrow: {row}"
    )


def test_negative_control_all_known_rules_leaves_envelope_unchanged(cli_runner, diff_project):
    """No unevaluable rule -> no new keys, no partial_success flip."""
    (diff_project / ".roam" / "fitness.yaml").write_text(
        "rules:\n  - name: funcs-are-snake-case\n    type: naming\n    kind: function\n"
        '    pattern: "^[a-z_][a-z0-9_]*$"\n',
        encoding="utf-8",
    )
    payload = _diff_json(cli_runner, diff_project)
    summary = payload["summary"]
    assert "fitness_rules_unevaluated" not in summary, f"clean run gained a key: {summary}"
    assert "fitness_unknown_rule_types" not in summary, f"clean run gained a key: {summary}"
    assert not [w for w in payload.get("warnings_out", []) if "fitness_rule_unevaluable" in w]
    assert summary.get("partial_success") is not True, summary


# ---------------------------------------------------------------------------
# 4. Fail-closed vs disclose — the deliberate decision, pinned.
# ---------------------------------------------------------------------------


def test_a_genuinely_failing_rule_still_exits_zero(cli_runner, diff_project):
    """``roam diff`` is informational, not a gate — the premise of the decision.

    If this ever starts exiting 1 on a FAIL, revisit the unevaluable-rule
    exit code with it; until then, exiting 1 on a config typo while exiting 0
    on a real architectural violation would rank the typo higher than the
    violation it was written to catch. ``roam fitness`` is the gate (W1450,
    fails closed).
    """
    result = invoke_cli(cli_runner, ["diff", "--fitness"], cwd=diff_project, json_mode=True)
    payload = json.loads(result.output)
    assert payload["summary"]["fitness_rules_failed"] >= 1, "precondition: a rule genuinely fails"
    assert result.exit_code == 0, "premise broken — diff now gates on fitness failures"


def test_unevaluable_rule_does_not_change_the_exit_code(cli_runner, diff_project):
    result = invoke_cli(cli_runner, ["diff", "--fitness"], cwd=diff_project, json_mode=True)
    assert result.exit_code == 0, (
        "W1453 chose DISCLOSE over fail-closed for diff; the remedy is "
        f"partial_success + ERROR status, not exit 1. Output:\n{result.output}"
    )


def test_collector_never_raises_systemexit(diff_project, monkeypatch):
    """``cmd_attest`` guards this collector with ``except Exception``.

    ``SystemExit`` is a ``BaseException`` — copying ``cmd_fitness``'s
    fail-closed ``raise SystemExit(1)`` into the shared collector would
    escape that guard and take ``roam attest`` down on a config typo.
    """
    from roam.db.connection import open_db

    monkeypatch.chdir(diff_project)
    with open_db(readonly=True) as conn:
        row = conn.execute("SELECT id, path FROM files WHERE path LIKE '%auth.py'").fetchone()
        file_map = {row["path"]: row["id"]}
        # Would propagate rather than be caught if the collector exited.
        rule_results, _violations = cmd_diff._collect_fitness_violations(conn, file_map, diff_project)
    assert any(r["status"] == "ERROR" for r in rule_results), (
        f"collector must still REPORT the unevaluable rule, just not exit: {rule_results}"
    )


# ---------------------------------------------------------------------------
# 5. Structural — registering a checker teaches this consumer.
# ---------------------------------------------------------------------------


def test_registering_a_checker_makes_diff_enforce_it(cli_runner, diff_project, monkeypatch):
    """The whole point: ``_CHECKERS`` is the registry, not a literal list here.

    A brand-new type — one no ``if/elif`` in cmd_diff could possibly know —
    must be enforced the moment it is registered, with no scoped variant.
    """
    from roam.commands import cmd_fitness

    def _synthetic(rule, conn):
        return [{"rule": rule["name"], "type": "synthetic", "message": "synthetic violation"}]

    monkeypatch.setitem(cmd_fitness._CHECKERS, "synthetic", _synthetic)
    (diff_project / ".roam" / "fitness.yaml").write_text(
        "rules:\n  - name: brand-new-rule\n    type: synthetic\n", encoding="utf-8"
    )

    row = _rules_by_name(_diff_json(cli_runner, diff_project))["brand-new-rule"]
    assert row["status"] == "FAIL", (
        f"registering a checker in _CHECKERS must teach cmd_diff — got {row}. "
        "A fourth if/elif branch would not satisfy this."
    )
    assert row["scope"] == "repository", f"no scoped variant -> global evaluation, disclosed: {row}"


def test_deregistering_a_checker_makes_diff_report_error(cli_runner, diff_project, monkeypatch):
    """The other direction — the membership test really is ``_CHECKERS``.

    Removing ``trend`` must turn the trend rule into an ERROR row, not back
    into a silent PASS.
    """
    from roam.commands import cmd_fitness

    monkeypatch.delitem(cmd_fitness._CHECKERS, "trend")
    (diff_project / ".roam" / "fitness.yaml").write_text(
        "rules:\n  - name: health-must-not-regress\n    type: trend\n"
        "    metric: health_score\n    window: 3\n    max_decrease: 5\n",
        encoding="utf-8",
    )

    row = _rules_by_name(_diff_json(cli_runner, diff_project))[_TREND_RULE]
    assert row["status"] == "ERROR", f"deregistered type must become ERROR, not PASS: {row}"


def test_scoped_checkers_is_a_subset_of_the_registry():
    """``_SCOPED_CHECKERS`` narrows; it must never gain a type ``_CHECKERS`` lacks.

    A scoped variant for a type the registry does not know would be
    unreachable (``_resolve_checker`` gates first) — a silent dead branch and
    the seed of a fourth dialect.
    """
    from roam.commands import cmd_fitness

    unknown = set(cmd_diff._SCOPED_CHECKERS) - set(cmd_fitness._CHECKERS)
    assert not unknown, f"_SCOPED_CHECKERS has types absent from _CHECKERS (unreachable): {sorted(unknown)}"


def test_dispatch_has_no_hardcoded_type_names():
    """Regression guard: the fall-through must not come back as an if/elif.

    Pins the SHAPE, not just the behaviour — the defect class is "a second
    hardcoded list of rule types drifts from the registry". Asserted over the
    AST so explanatory comments quoting the old code do not trip it.
    """
    import ast
    import inspect
    import textwrap

    from roam.commands import cmd_fitness

    tree = ast.parse(textwrap.dedent(inspect.getsource(cmd_diff._collect_fitness_violations)))
    known_types = set(cmd_fitness._CHECKERS)
    literal_compares = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for node in node.comparators
        if isinstance(node, ast.Constant) and node.value in known_types
    ]
    assert not literal_compares, (
        "cmd_diff._collect_fitness_violations compares rule types literally again "
        f"({sorted(set(literal_compares))}) — dispatch must go through "
        "cmd_fitness._resolve_checker + _SCOPED_CHECKERS"
    )
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "_resolve_checker" in called, "the shared membership test is gone from the dispatcher"


# ---------------------------------------------------------------------------
# 6. Text mode tells the same story as JSON.
# ---------------------------------------------------------------------------


def test_text_mode_renders_error_and_not_evaluated_tally(cli_runner, diff_project):
    result = invoke_cli(cli_runner, ["diff", "--fitness"], cwd=diff_project)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "1 NOT evaluated" in out, f"header must not fold an unevaluated rule into passed/failed:\n{out}"
    assert f"[ERROR] {_UNKNOWN_RULE}" in out, f"unevaluable rule must render as ERROR:\n{out}"
    assert f"[PASS] {_UNKNOWN_RULE}" not in out
    assert f"[FAIL] {_TREND_RULE}" in out, f"trend must render as FAIL:\n{out}"
    assert "repo-wide, not diff-scoped" in out, f"repo-wide evaluation must be disclosed in text:\n{out}"
    # Negative control survives text mode too.
    assert f"[PASS] {_NAMING_RULE}" in out, f"healthy naming rule must still render PASS:\n{out}"
