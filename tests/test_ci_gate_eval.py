"""Tests for GitHub Action quality gate evaluator (trend-aware gates)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_gate_eval_module():
    """Load .github/scripts/gate_eval.py as a Python module."""
    root = Path(__file__).resolve().parents[1]
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


def test_unknown_metric_warns_but_does_not_fail():
    mod = _load_gate_eval_module()
    results = {"health": {"summary": {"health_score": 80}}}
    report = mod.evaluate_gate("latest(nonexistent)>=1", results)
    assert report["passed"] is True
    assert report["checked_expressions"] == 0
    assert report["warnings"]
