"""L1-rate floor — the compiler's answer-rate cannot silently sink.

53-57% of real prompts route at L1 (the envelope carries the literal
answer). That rate IS the product: a classifier or artifact-policy change
that demotes L1 answers to bare facts envelopes erodes the measured wins
without failing any routing lock (routing checks WHERE, not WHETHER the
answer shipped). This floor replays a deterministic 60-prompt sample of
the frozen corpus through the full compile (probes included) and fails
below 45% — generous against repo drift, fatal to a real demotion.

Marked slow (~30s warm) and skipped without the dogfood corpus/index.
"""

from __future__ import annotations

import json
import os

import pytest

from tests._helpers.repo_root import repo_root

_CORPUS = "internal/benchmarks/corpora/all-unique-2026-06-09.jsonl"
_FLOOR_PCT = 45.0
_SAMPLE_STRIDE = 12
_SAMPLE_CAP = 60


@pytest.mark.slow
def test_l1_rate_stays_above_floor(monkeypatch):
    root = repo_root()
    corpus = root / _CORPUS
    if not corpus.exists() or not (root / ".roam" / "index.db").exists():
        pytest.skip("dogfood corpus/index absent (public CI)")
    monkeypatch.chdir(root)
    # measurement integrity: this test runs 60 real compiles that append to the
    # repo's own .roam/compile-runs.jsonl. Stamp them 'test' so they never land
    # in the production L1-rate/latency KPIs (compile-stats default-excludes it).
    from roam.plan.agent_mode import ENV_VAR, MODE_TEST

    monkeypatch.setenv(ENV_VAR, MODE_TEST)

    from roam.plan.compiler import compile_for_artifact, compile_plan

    rows = [json.loads(line) for line in corpus.read_text().splitlines() if line.strip()]
    sample = [(r.get("task") or r.get("prompt") or "") for i, r in enumerate(rows) if i % _SAMPLE_STRIDE == 0][
        :_SAMPLE_CAP
    ]
    sample = [t for t in sample if t]

    l1 = total = 0
    for task in sample:
        try:
            plan = compile_plan(task, cwd=os.getcwd())
            _env, label = compile_for_artifact(plan, cwd=os.getcwd())
        except Exception:  # noqa: BLE001 — a crash counts as a non-L1 outcome
            total += 1
            continue
        total += 1
        if label == "l1_probe":
            l1 += 1

    pct = 100.0 * l1 / max(1, total)
    assert pct >= _FLOOR_PCT, (
        f"L1 rate sank to {pct:.1f}% on the {total}-prompt sample "
        f"(floor {_FLOOR_PCT}%, was 56.7% at introduction) — a classifier or "
        f"artifact-policy change is demoting answer-bearing envelopes."
    )
