from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "benchmarks" / "oss-eval" / "run_oss_bench.py"

spec = importlib.util.spec_from_file_location("run_oss_bench", MODULE_PATH)
oss_bench = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
sys.modules[spec.name] = oss_bench
spec.loader.exec_module(oss_bench)


def test_extract_metrics_handles_missing_optional_payloads():
    metrics = oss_bench._extract_metrics(
        health={"health_score": 91, "severity": {"CRITICAL": 0, "WARNING": 2}, "tangle_ratio": 0.04},
        dead=None,
        complexity=None,
        coupling=None,
    )
    assert metrics["health_score"] == 91
    assert metrics["dead_symbols"] is None
    assert metrics["avg_complexity"] is None
    assert metrics["hidden_coupling"] is None


def test_markdown_renderer_outputs_na_for_missing_metrics():
    summary = {
        "generated_at": "2026-02-24T00:00:00+00:00",
        "manifest_path": "benchmarks/oss-eval/targets.json",
        "counts": {
            "targets_total": 1,
            "evaluated": 1,
            "evaluated_full": 0,
            "evaluated_partial": 1,
            "major_total": 1,
            "major_evaluated": 1,
            "major_missing": [],
        },
        "aggregate": {
            "avg_health_score": 91.0,
            "avg_dead_symbols": None,
            "avg_hidden_coupling": None,
        },
        "results": [
            {
                "id": "fastapi",
                "tier": "major",
                "status": "ok_partial",
                "elapsed_s": 3.25,
                "metrics": {
                    "health_score": 91,
                    "dead_symbols": None,
                    "avg_complexity": None,
                    "p90_complexity": None,
                    "hidden_coupling": None,
                },
            }
        ],
    }
    md = oss_bench._render_markdown(summary)
    assert "N/A" in md
    assert "| fastapi | major | ok_partial | 91 | N/A | N/A | N/A | N/A | 3.25 |" in md
