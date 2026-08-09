"""Tests for GitHub Action quality gate evaluator (trend-aware gates).

W1515 -- a gate that could not be evaluated is not a gate that passed.
``evaluate_gate`` computed ``passed`` from ``failures`` alone and never
consulted ``checked_expressions``, which the same dict already carried. An
expression naming a metric no payload produced therefore contributed zero
failures, ``passed`` stayed ``True``, and ``main()`` printed ``true`` -- which
``action.yml`` publishes as ``gate-passed=true`` and ``pr-comment.js`` renders
as "### Quality Gate: PASSED" beside the very expression nobody checked.

The partial case was worse than the total one: with a one-element history --
a repository's FIRST CI run -- ``direction()`` and ``delta()`` are both
unevaluable while ``latest()`` is not, so one expression of three was checked
and ``passed: true`` was published as the verdict for all three.

The old ``test_unknown_metric_warns_but_does_not_fail`` enshrined exactly that
behaviour (``passed is True`` with ``checked_expressions == 0``) and was
rewritten as part of the fix. Its replacement, and every test below it,
asserts the third state.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys

import pytest
import yaml

from tests._helpers.repo_root import repo_root


def _load_gate_eval_module():
    """Load .github/scripts/gate_eval.py as a Python module."""
    root = repo_root()
    script = root / ".github" / "scripts" / "gate_eval.py"
    spec = importlib.util.spec_from_file_location("gate_eval", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scalar_gate_pass():
    mod = _load_gate_eval_module()
    results = {"health": {"summary": {"health_score": 82}}}
    report = mod.evaluate_gate("health_score>=70", results)
    assert report["passed"] is True
    assert report["checked_expressions"] == 1
    assert report["failures"] == []


def test_scalar_gate_fail():
    mod = _load_gate_eval_module()
    results = {"health": {"summary": {"health_score": 55}}}
    report = mod.evaluate_gate("health_score>=70", results)
    assert report["passed"] is False
    assert report["checked_expressions"] == 1
    assert report["failures"]
    assert "health_score" in report["failures"][0]


def test_trend_latest_and_delta():
    mod = _load_gate_eval_module()
    results = {
        "trends": {
            "metrics": [
                {"name": "cycle_count", "history": [1, 2, 3, 5], "latest": 5, "change": 4},
            ],
        },
    }
    report = mod.evaluate_gate("latest(cycle_count)>=5,delta(cycle_count)>=4", results)
    assert report["passed"] is True
    assert report["checked_expressions"] == 2


def test_velocity_gate_detects_worsening():
    mod = _load_gate_eval_module()
    results = {
        "trends": {
            "metrics": [
                {"name": "cycle_count", "history": [1, 2, 4, 7]},
            ],
        },
    }
    # cycle_count increasing => positive worsening velocity, should fail <=0 gate
    report = mod.evaluate_gate("velocity(cycle_count)<=0", results)
    assert report["passed"] is False
    assert report["failures"]


def test_direction_gate_uses_metric_polarity():
    mod = _load_gate_eval_module()
    results = {
        "trends": {
            "metrics": [
                {"name": "health_score", "history": [90, 86, 82, 80]},
            ],
        },
    }
    report = mod.evaluate_gate("direction(health_score)=worsening", results)
    assert report["passed"] is True
    assert report["checked_expressions"] == 1


def test_unknown_metric_is_unevaluated_not_passed():
    """THE DEFECT (was ``test_unknown_metric_warns_but_does_not_fail``).

    Nothing was checked, so there is nothing to pass. ``passed`` keeps its
    historical "no expression FAILED" meaning for backward compatibility, but
    it can no longer be read as a verdict on its own.
    """
    mod = _load_gate_eval_module()
    results = {"health": {"summary": {"health_score": 80}}}
    report = mod.evaluate_gate("latest(nonexistent)>=1", results)
    assert report["state"] == "unevaluated"
    assert report["checked_expressions"] == 0
    assert report["unchecked_expressions"] == 1
    assert report["warnings"]
    assert report["failures"] == []


def test_partially_evaluated_gate_is_unevaluated_not_passed():
    """A repository's first CI run: one snapshot, so two of three are blind.

    ``_compute_slope`` needs len(history) >= 2 and ``_metric_delta`` needs the
    same, so ``direction()`` and ``delta()`` cannot be computed. Publishing
    ``true`` here reports a verdict on three expressions having checked one.
    """
    mod = _load_gate_eval_module()
    results = {"trends": {"metrics": [{"name": "health_score", "history": [82], "latest": 82}]}}
    report = mod.evaluate_gate(
        "direction(health_score)!=worsening,delta(health_score)>=-5,latest(health_score)>=70",
        results,
    )
    assert report["checked_expressions"] == 1
    assert report["unchecked_expressions"] == 2
    assert report["state"] == "unevaluated"
    assert report["failures"] == []


def test_a_real_failure_outranks_an_unevaluated_expression():
    """NEGATIVE CONTROL -- the third state must not mask a genuine failure.

    If ``unevaluated`` won over ``failed``, a gate could be downgraded from
    red to advisory by adding one expression nothing can evaluate.
    """
    mod = _load_gate_eval_module()
    results = {"health": {"summary": {"health_score": 55}}}
    report = mod.evaluate_gate("health_score>=70,latest(nonexistent)>=1", results)
    assert report["state"] == "failed"
    assert report["failures"]
    assert report["unchecked_expressions"] == 1


def test_fully_evaluated_gates_keep_their_two_states():
    """NEGATIVE CONTROL -- no new state on a gate that really was evaluated."""
    mod = _load_gate_eval_module()
    good = mod.evaluate_gate("health_score>=70", {"health": {"summary": {"health_score": 82}}})
    assert good["state"] == "passed"
    assert good["passed"] is True
    assert good["unchecked_expressions"] == 0

    bad = mod.evaluate_gate("health_score>=70", {"health": {"summary": {"health_score": 55}}})
    assert bad["state"] == "failed"
    assert bad["passed"] is False
    assert bad["unchecked_expressions"] == 0


def test_empty_gate_expression_is_not_unevaluated():
    """NEGATIVE CONTROL -- asking for nothing is not the same as being blind.

    The action only runs this step when the gate input is non-empty, but the
    function is public and must not report a caller's empty string as a
    broken gate.
    """
    mod = _load_gate_eval_module()
    report = mod.evaluate_gate("", {"health": {"summary": {"health_score": 82}}})
    assert report["state"] == "passed"
    assert report["unchecked_expressions"] == 0


def _run_main(tmp_path, expr: str, payloads: dict[str, dict]):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (results_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    script = repo_root() / ".github" / "scripts" / "gate_eval.py"
    return subprocess.run(
        [sys.executable, str(script), "--expr", expr, "--results-dir", str(results_dir)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "expr,payloads,token",
    [
        ("health_score>=70", {"health": {"summary": {"health_score": 82}}}, "true"),
        ("health_score>=70", {"health": {"summary": {"health_score": 55}}}, "false"),
        ("health_score>=70", {"health": {"summary": {"verdict": "Healthy"}}}, "unknown"),
    ],
)
def test_main_publishes_one_of_three_tokens(tmp_path, expr, payloads, token):
    """stdout IS the published value -- action.yml captures it verbatim."""
    proc = _run_main(tmp_path / token, expr, payloads)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == token, proc.stderr


def test_main_reports_an_unevaluable_expression_as_an_error(tmp_path):
    """The user asked for that gate and did not get it -- ::error::, not ::warning::.

    The per-payload "metric not available" line stays a warning: it fires once
    per payload and is normal noise when several commands ran.
    """
    proc = _run_main(tmp_path, "health_score>=70", {"health": {"summary": {"verdict": "Healthy"}}})
    assert "::error::no compatible payload found for `health_score>=70`" in proc.stderr
    assert "::warning::health: metric not available" in proc.stderr


def test_main_still_exits_zero_on_an_unevaluated_gate(tmp_path):
    """NEGATIVE CONTROL -- refusal is the ACTION's decision, not the script's.

    The composite action turns `unknown` into an ::error:: and, only under
    `gate-strict: true`, exit 5. Making the script itself exit non-zero would
    take that choice away and break every adopter's first trend-gated run.
    """
    proc = _run_main(tmp_path, "health_score>=70", {"health": {"summary": {"verdict": "Healthy"}}})
    assert proc.returncode == 0


# ---------------------------------------------------------------------------
# The script and its two consumers must not drift apart
# ---------------------------------------------------------------------------


def _quality_gate_step() -> dict:
    payload = yaml.safe_load((repo_root() / "action.yml").read_text(encoding="utf-8"))
    steps = payload["runs"]["steps"]
    matches = [s for s in steps if s.get("id") == "quality-gate"]
    assert len(matches) == 1, f"expected one quality-gate step, got {len(matches)}"
    return matches[0]


def test_action_accepts_every_token_the_script_can_print():
    """A token the script prints and the action rejects fails the job at
    ``Gate evaluator returned an unexpected result`` -- a hard break with no
    signal in this repository. Derive the assertion from the script's own
    table rather than a literal."""
    mod = _load_gate_eval_module()
    script = _quality_gate_step()["run"]
    guard = re.search(r"\^\(([a-z|]+)\)\$", script)
    assert guard is not None, "the action no longer validates the gate token"
    accepted = set(guard.group(1).split("|"))
    for state in mod.GATE_STATES:
        assert mod._STATE_TOKENS[state] in accepted, (
            f"the script can print {mod._STATE_TOKENS[state]!r} for state {state!r}, "
            f"but action.yml only accepts {sorted(accepted)}"
        )


def test_action_publishes_the_three_state_word_and_gates_it_behind_an_input():
    step = _quality_gate_step()
    script = step["run"]
    assert "gate-state=${GATE_STATE}" in script
    assert "unevaluated" in script
    assert "Quality gate could not be evaluated" in script
    # The strict escalation must be opt-in, and must be reached only for the
    # unknown branch -- never for a gate that was evaluated and passed.
    assert 'if [ "${GATE_STRICT}" = "true" ]; then' in script
    # The flag reaches the step through the validator, never raw: the
    # validate-inputs step is the single boundary where `inputs.*` is read,
    # and it closes gate-strict to the boolean vocabulary before the gate
    # step compares it. See test_composite_action_security.
    assert step["env"]["GATE_STRICT"] == "${{ steps.validate-inputs.outputs.gate-strict }}"

    action = yaml.safe_load((repo_root() / "action.yml").read_text(encoding="utf-8"))
    assert action["inputs"]["gate-strict"]["default"] == "false", (
        "an unevaluable gate must not block by default -- a repository's first "
        "run has one snapshot and cannot evaluate a trend gate"
    )
    assert "gate-state" in action["outputs"]


def test_pr_comment_never_renders_an_unknown_gate_as_passed():
    """`unknown` AND the empty string (skipped/crashed step) must both read
    as NOT EVALUATED. The old two-way ternary rendered PASSED for both."""
    js = (repo_root() / ".github" / "scripts" / "pr-comment.js").read_text(encoding="utf-8")
    assert "NOT EVALUATED" in js
    assert "GATE_LABELS" in js
    assert "gatePassed === 'false' ? 'FAILED' : 'PASSED'" not in js, (
        "the two-way ternary is back: it reports PASSED for every value that is not 'false'"
    )
