"""Tests for `roam envelope-diff` baseline / regression-contract mode.

Three cases:
  (a) ``--update-baseline DIR`` writes envelope.json + baseline_meta.json
      under ``DIR/<task_hash>/`` and does nothing else.
  (b) Re-diffing the same prompt against the just-written baseline
      returns ``PASS: no regression`` and exit code 0.
  (c) An artificially-degraded current envelope (forced via monkeypatch
      of `_compile_envelope`) trips the regression rules and exits 5
      under ``--regression``.

The tests monkeypatch ``_compile_envelope`` so they do NOT need a real
git repo / built index in ``tmp_path`` — the compile pipeline is
expensive and unrelated to the baseline-contract being exercised.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands import cmd_envelope_diff as mod
from roam.commands.cmd_envelope_diff import envelope_diff


@pytest.fixture
def runner():
    return CliRunner()


def _json_obj():
    return {"json": True}


def _task_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def _baseline_envelope() -> dict:
    """A representative compile envelope with two non-empty probe families
    and a healthy classifier confidence — anchor for the regression rules.
    """
    return {
        "schema": "roam-plan-v0",
        "summary": {
            "procedure": "structural_coupling",
            "classifier_confidence": 0.92,
            "classifier_version": "v9-2026-05",
        },
        "plan": {
            "procedure": "structural_coupling",
            "classifier_confidence": 0.92,
            "classifier_version": "v9-2026-05",
            "prefetched_facts": {
                "coupling_pairs": [{"a": "x.py", "b": "y.py"}],
                "structural_blast": {"affected": ["x.py", "y.py"]},
            },
        },
    }


def _degraded_envelope() -> dict:
    """Same prompt's "current" envelope after a regression: classifier
    confidence collapsed AND one probe family is gone."""
    return {
        "schema": "roam-plan-v0",
        "summary": {
            "procedure": "structural_coupling",
            "classifier_confidence": 0.40,
            "classifier_version": "v10-2026-06",
        },
        "plan": {
            "procedure": "structural_coupling",
            "classifier_confidence": 0.40,
            "classifier_version": "v10-2026-06",
            "prefetched_facts": {
                # coupling_pairs MISSING → triggers probe_family_missing.
                "structural_blast": {"affected": []},  # empty → drops fire rate.
            },
        },
    }


# ---------------------------------------------------------------------------
# (a) --update-baseline writes envelope.json + baseline_meta.json
# ---------------------------------------------------------------------------


def test_update_baseline_writes_files(runner, tmp_path, monkeypatch):
    """`--update-baseline` recompiles the prompt and writes both files."""
    baseline_dir = tmp_path / "baselines"
    prompt = "find files coupled to src/roam/cli.py"

    baseline_env = _baseline_envelope()
    monkeypatch.setattr(mod, "_compile_envelope", lambda task, cwd: baseline_env)

    result = runner.invoke(
        envelope_diff,
        [prompt, "--update-baseline", str(baseline_dir), "--root", str(tmp_path)],
        obj=_json_obj(),
    )
    assert result.exit_code == 0, result.output

    envelope = json.loads(result.output)
    assert envelope["summary"]["mode"] == "update_baseline"
    assert "baseline updated" in envelope["summary"]["verdict"]
    assert envelope["baseline_meta"] is None  # unset on update mode

    task_dir = baseline_dir / _task_hash(prompt)
    env_path = task_dir / "envelope.json"
    meta_path = task_dir / "baseline_meta.json"
    assert env_path.exists(), "envelope.json was not written"
    assert meta_path.exists(), "baseline_meta.json was not written"

    written = json.loads(env_path.read_text(encoding="utf-8"))
    assert written == baseline_env, "round-trip mismatch"

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert set(meta.keys()) >= {"created_at", "head", "classifier_version", "task_prefix"}
    assert meta["classifier_version"] == "v9-2026-05"
    assert meta["task_prefix"].startswith("find files coupled")


# ---------------------------------------------------------------------------
# (b) Re-diff against same baseline → PASS, exit 0
# ---------------------------------------------------------------------------


def test_rediff_same_baseline_passes(runner, tmp_path, monkeypatch):
    """After --update-baseline, re-running with --baseline --regression
    against the same envelope must report PASS with exit 0."""
    baseline_dir = tmp_path / "baselines"
    prompt = "find files coupled to src/roam/cli.py"
    baseline_env = _baseline_envelope()

    monkeypatch.setattr(mod, "_compile_envelope", lambda task, cwd: baseline_env)

    # Step 1: write the baseline.
    write_result = runner.invoke(
        envelope_diff,
        [prompt, "--update-baseline", str(baseline_dir), "--root", str(tmp_path)],
        obj=_json_obj(),
    )
    assert write_result.exit_code == 0, write_result.output

    # Step 2: re-diff against it (compile returns the SAME envelope).
    diff_result = runner.invoke(
        envelope_diff,
        [prompt, "--baseline", str(baseline_dir), "--regression", "--root", str(tmp_path)],
        obj=_json_obj(),
    )
    assert diff_result.exit_code == 0, diff_result.output

    envelope = json.loads(diff_result.output)
    assert envelope["summary"]["verdict"] == "PASS: no regression"
    assert envelope["summary"]["partial_success"] is False
    assert envelope["summary"]["mode"] == "baseline"
    assert envelope["summary"]["regression_check"] is True
    assert envelope["regression_findings"] == []
    assert envelope["baseline_meta"]["classifier_version"] == "v9-2026-05"
    assert envelope["baseline_path"].endswith("envelope.json")

    facts = envelope["agent_contract"]["facts"]
    assert facts
    # "0 regression findings" must be present.
    assert any("0 regression findings" in f for f in facts)


# ---------------------------------------------------------------------------
# (c) Artificial regression → REGRESSION verdict + exit 5
# ---------------------------------------------------------------------------


def test_artificial_regression_exits_5(runner, tmp_path, monkeypatch):
    """A degraded current envelope (dropped probe family + collapsed
    classifier confidence) trips the regression gate and exits 5."""
    baseline_dir = tmp_path / "baselines"
    prompt = "find files coupled to src/roam/cli.py"

    # Step 1: write baseline with the healthy envelope.
    monkeypatch.setattr(mod, "_compile_envelope", lambda task, cwd: _baseline_envelope())
    write_result = runner.invoke(
        envelope_diff,
        [prompt, "--update-baseline", str(baseline_dir), "--root", str(tmp_path)],
        obj=_json_obj(),
    )
    assert write_result.exit_code == 0, write_result.output

    # Step 2: now compile returns a DEGRADED envelope.
    monkeypatch.setattr(mod, "_compile_envelope", lambda task, cwd: _degraded_envelope())
    diff_result = runner.invoke(
        envelope_diff,
        [prompt, "--baseline", str(baseline_dir), "--regression", "--root", str(tmp_path)],
        obj=_json_obj(),
    )
    assert diff_result.exit_code == 5, diff_result.output

    envelope = json.loads(diff_result.output)
    verdict = envelope["summary"]["verdict"]
    assert verdict.startswith("REGRESSION:"), verdict
    assert envelope["summary"]["partial_success"] is True
    assert envelope["summary"]["regression_finding_count"] >= 2

    findings = envelope["regression_findings"]
    rules = {f["rule"] for f in findings}
    # Both classifier-confidence drop AND missing probe family must fire.
    assert "classifier_confidence_drop" in rules
    assert "probe_family_missing" in rules
    # The missing-families list names coupling_pairs.
    missing_finding = next(f for f in findings if f["rule"] == "probe_family_missing")
    assert "coupling_pairs" in missing_finding["missing_families"]


# ---------------------------------------------------------------------------
# Sanity: baseline-missing emits structured error, exit 2.
# ---------------------------------------------------------------------------


def test_baseline_missing_is_structured_error(runner, tmp_path, monkeypatch):
    """Requesting --baseline against an empty dir surfaces a
    `baseline_missing` verdict with exit code 2."""
    baseline_dir = tmp_path / "baselines_empty"
    baseline_dir.mkdir()
    monkeypatch.setattr(mod, "_compile_envelope", lambda task, cwd: _baseline_envelope())
    result = runner.invoke(
        envelope_diff,
        ["some prompt", "--baseline", str(baseline_dir), "--regression", "--root", str(tmp_path)],
        obj=_json_obj(),
    )
    assert result.exit_code == 2, result.output
    envelope = json.loads(result.output)
    assert envelope["summary"]["verdict"] == "baseline_missing"
    assert envelope["summary"]["partial_success"] is True
    assert Path(envelope["baseline_path"]).name == "envelope.json"
