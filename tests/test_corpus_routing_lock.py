"""Frozen-corpus routing ratchet — the compiler-moat regression lock.

Replays the frozen production corpora (June 2026 telemetry snapshots under
``internal/benchmarks/corpora/``) through the CURRENT classifier and pins:

1. **still-missed ratchet (roam-code corpus)** — the number of historically
   freeform prompts that STILL classify freeform must never RISE above the
   pinned ceiling. Lowering it (new coverage waves) should also lower the
   ceiling here.
2. **routed-drift pin** — prompts that were routed to a specialized procedure
   in production must not silently re-route. The 24-prompt baseline is all
   truncation artifacts (telemetry stores 80-char prefixes); anything above
   it is a real classifier regression.
3. **cross-repo sentinels** — representative prompt families mined from the
   OTHER repos on this VPS (frontend/stoa/home transcripts) keep their
   routing.

The corpora live under ``internal/`` (gitignored), so this lock runs only on
dev machines / dogfood CI — it skips cleanly elsewhere. History: waves
2026-06-09/10 moved still-missed 447→353 with drift pinned at 24; this test
makes that a one-way door.
"""

from __future__ import annotations

import json
import os

import pytest

from roam.plan.compiler import _classify

_CORPUS = "internal/benchmarks/corpora/all-unique-2026-06-09.jsonl"
_CROSS_REPO = "internal/benchmarks/corpora/cross-repo-prompts-2026-06-09.jsonl"

# Ratchet DOWN only. 353 measured 2026-06-10 after the 7-procedure waves.
_STILL_MISSED_CEILING = 353
# 24 pre-existing truncation artifacts (multi-line prompts cut at 80 chars).
_ROUTED_DRIFT_CEILING = 24


def _load(path):
    if not os.path.exists(path):
        pytest.skip(f"frozen corpus not present: {path} (internal/ is gitignored; dogfood-CI-only lock)")
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_still_missed_freeform_never_rises():
    rows = _load(_CORPUS)
    hist_ff = [r["task"] for r in rows if r.get("telemetry_procedure") == "freeform_explore"]
    still = [t for t in hist_ff if _classify(t)[0] == "freeform_explore"]
    assert len(still) <= _STILL_MISSED_CEILING, (
        f"{len(still)} historically-freeform prompts still classify freeform "
        f"(ceiling {_STILL_MISSED_CEILING}) — a coverage wave regressed. "
        f"Sample: {[t[:60] for t in still[:3]]}"
    )


def test_routed_prompts_do_not_drift():
    rows = _load(_CORPUS)
    drift = [
        (r["task"], r["telemetry_procedure"], _classify(r["task"])[0])
        for r in rows
        if r.get("telemetry_procedure") not in (None, "freeform_explore")
        and _classify(r["task"])[0] != r["telemetry_procedure"]
    ]
    assert len(drift) <= _ROUTED_DRIFT_CEILING, (
        f"{len(drift)} routed prompts now classify differently "
        f"(baseline {_ROUTED_DRIFT_CEILING} truncation artifacts). New drift: "
        f"{[(t[:50], o, n) for t, o, n in drift[:5]]}"
    )


@pytest.mark.parametrize(
    "task,expected",
    [
        # one stable sentinel per 2026-06-09/10 procedure family
        ("what changed in src/roam/cli.py recently", "file_history"),
        ("what are the layers of this codebase", "repo_structure"),
        ("what's the entry point for the CLI", "entry_point_where"),
        ("where is the ROAM_GREP_ENGINE env var configured", "config_where"),
        ("explain the compiler architecture", "describe_file"),
        ("ultrathink: lets keep going", "session_meta"),
        # pre-existing families must keep their homes
        ("where is open_db defined", "symbol_defined_where"),
        ("top 5 most-imported files", "top_n_ranking"),
        ("blast radius of compile_plan", "structural_blast"),
    ],
)
def test_procedure_sentinels(task, expected):
    assert _classify(task)[0] == expected


def test_cross_repo_batch_family_stays_captured():
    rows = _load(_CROSS_REPO)
    # The corpus stores 180-char prefixes; the batch fast-path needs >=200
    # chars, so replay can't measure capture directly. Assert the FAMILY
    # leaders still classify correctly when given at full length.
    payload = (
        "You are validating a behavior extraction adversarially.\n\n"
        "Source file: /data/migrations/legacy/report_gen.bas\n"
        "Extraction JSON: /tmp/pipeline/ab_test/n2v.json\n\n"
        "For each behavior, verify against the source and score "
        "CONFIRMED / PARTIAL / WRONG.\nOutput JSON only with a scores array."
    )
    assert _classify(payload)[0] == "self_contained_task"
    # and the corpus itself still loads (provenance intact)
    assert len(rows) > 500
