"""Tests for GitHub Action SARIF guardrail helper."""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path


def _load_sarif_guard_module():
    """Load .github/scripts/sarif_guard.py as a Python module."""
    root = Path(__file__).resolve().parents[1]
    script = root / ".github" / "scripts" / "sarif_guard.py"
    spec = importlib.util.spec_from_file_location("sarif_guard", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_sarif(result_count: int, msg_size: int = 20) -> dict:
    msg = "x" * msg_size
    rules = [{"id": "rule/test", "shortDescription": {"text": "test"}}]
    results = [
        {
            "ruleId": "rule/test",
            "level": "warning",
            "message": {"text": f"{msg}-{i}"},
            "locations": [],
        }
        for i in range(result_count)
    ]
    return {
        "$schema": "https://example.test/sarif",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "roam-code", "rules": rules}},
                "results": results,
            },
        ],
    }


def test_merge_sarif_files_skips_invalid(tmp_path):
    mod = _load_sarif_guard_module()
    good = tmp_path / "health.sarif"
    bad = tmp_path / "broken.sarif"
    good.write_text(json.dumps(_make_sarif(1)), encoding="utf-8")
    bad.write_text("{not json", encoding="utf-8")

    merged, skipped = mod.merge_sarif_files([good, bad])
    assert len(merged["runs"]) == 1
    assert "broken.sarif" in skipped
    auto = merged["runs"][0].get("automationDetails", {})
    assert auto.get("id") == "roam/health"


def test_apply_guardrails_run_and_result_caps():
    mod = _load_sarif_guard_module()
    run = _make_sarif(4)["runs"][0]
    data = {
        "$schema": "x",
        "version": "2.1.0",
        "runs": [deepcopy(run), deepcopy(run), deepcopy(run)],
    }

    summary = mod.apply_guardrails(data, max_runs=2, max_results=2, max_bytes=10_000_000)
    assert summary["runs_before"] == 3
    assert summary["runs_after"] == 2
    assert summary["results_before"] == 12
    assert summary["results_after"] == 4
    assert summary["dropped_runs"] == 1
    assert summary["dropped_results_for_run_cap"] == 4
    assert summary["dropped_results_for_result_cap"] == 4
    assert summary["truncated"] is True


def test_apply_guardrails_size_cap_truncates():
    mod = _load_sarif_guard_module()
    data = _make_sarif(30, msg_size=300)

    summary = mod.apply_guardrails(
        data,
        max_runs=20,
        max_results=25_000,
        max_bytes=2_000,
    )
    assert summary["results_before"] == 30
    assert summary["results_after"] < 30
    assert summary["dropped_results_for_size_cap"] > 0
    assert summary["bytes_after"] <= 2_000


def test_main_writes_output_and_summary(tmp_path, monkeypatch):
    mod = _load_sarif_guard_module()
    in1 = tmp_path / "health.sarif"
    in2 = tmp_path / "dead.sarif"
    out = tmp_path / "merged.sarif"
    summary = tmp_path / "summary.json"
    in1.write_text(json.dumps(_make_sarif(3)), encoding="utf-8")
    in2.write_text(json.dumps(_make_sarif(2)), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sarif_guard.py",
            "--output",
            str(out),
            "--summary-out",
            str(summary),
            "--max-runs",
            "20",
            "--max-results",
            "25000",
            "--max-bytes",
            "10000000",
            str(in1),
            str(in2),
        ],
    )
    rc = mod.main()
    assert rc == 0
    assert out.exists()
    assert summary.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    info = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["version"] == "2.1.0"
    assert len(payload["runs"]) == 2
    assert info["valid_input_files"] == 2
    assert info["truncated"] is False
