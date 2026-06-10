"""Tests for ``roam compiler-corpus`` — saved-corpus compiler analysis.

Asserts:
  * 5-line corpus → ``prompts_processed == 5`` and non-empty distributions
  * Blank lines and ``#`` comments are skipped
  * Missing corpus → ``state == "not_initialized"``, no crash
  * Facts list is LAW-4 anchored (terminal token in concrete-noun set)
  * ``--limit`` caps the processed count
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_compiler_corpus import (
    _aggregate,
    _compute_score,
    _load_corpus,
    compiler_corpus,
)

# Terminal-noun anchor set — mirrors the formatter's
# ``concrete_plural_terminals`` plus a few SBOM-style additions. Kept in
# sync with ``tests/test_law4_lint.py`` per the AGENTS.md note. For this
# command we only need the terminals our facts actually use.
_ANCHOR_TOKENS = {
    # Concrete plural nouns (mirrors a subset of
    # ``roam.output.formatter.concrete_plural_terminals``).
    "prompts",
    "entries",
    "bytes",
    "findings",
    "errors",
    "files",
    "items",
    "tokens",
    "records",
    # Time units
    "seconds",
    "milliseconds",
    # Past-participle state qualifiers
    "scanned",
    "checked",
    "skipped",
    "affected",
    "confirmed",
}


def _strip_punct(token: str) -> str:
    return token.rstrip(".,;:!?)\"'")


def _is_law4_anchored(fact: str) -> bool:
    """Terminal-token-anchored if the last non-punct word is in the anchor set."""
    parts = fact.strip().split()
    if not parts:
        return False
    return _strip_punct(parts[-1]) in _ANCHOR_TOKENS


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _seed_corpus(tmp_path: Path, prompts: list[str], with_comments: bool = False) -> Path:
    """Write the given prompts to ``corpus.txt`` and return its path."""
    path = tmp_path / "corpus.txt"
    lines: list[str] = []
    if with_comments:
        lines.append("# header comment")
        lines.append("")
    lines.extend(prompts)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_FIVE_PROMPTS = [
    "what does compile_plan do",
    "trace the request login flow",
    "find callers of compile_for_artifact",
    "list cycles in the dependency graph",
    "where is the n+1 query in catalog/tasks.py",
]


# ----------------------------------------------------------------------
# Loader unit tests
# ----------------------------------------------------------------------


def test_load_corpus_missing_returns_empty(tmp_path):
    assert _load_corpus(tmp_path / "does-not-exist.txt", limit=10) == []


def test_load_corpus_skips_blanks_and_comments(tmp_path):
    path = _seed_corpus(tmp_path, _FIVE_PROMPTS, with_comments=True)
    out = _load_corpus(path, limit=10)
    assert out == _FIVE_PROMPTS


def test_load_corpus_respects_limit(tmp_path):
    path = _seed_corpus(tmp_path, _FIVE_PROMPTS)
    out = _load_corpus(path, limit=3)
    assert len(out) == 3
    assert out == _FIVE_PROMPTS[:3]


# ----------------------------------------------------------------------
# Aggregation unit tests
# ----------------------------------------------------------------------


def test_aggregate_empty_records():
    assert _aggregate([])["state"] == "not_initialized"


def test_aggregate_basic_distribution():
    records = [
        {
            "prompt": "a",
            "procedure": "trace_flow",
            "artifact_label": "l1_probe",
            "envelope_bytes": 500,
            "compile_ms": 100.0,
            "probe_empty": False,
            "error": None,
        },
        {
            "prompt": "b",
            "procedure": "trace_flow",
            "artifact_label": "l1_probe",
            "envelope_bytes": 600,
            "compile_ms": 150.0,
            "probe_empty": False,
            "error": None,
        },
        {
            "prompt": "c",
            "procedure": "freeform_explore",
            "artifact_label": "facts",
            "envelope_bytes": 800,
            "compile_ms": 200.0,
            "probe_empty": False,
            "error": None,
        },
        {
            "prompt": "d",
            "procedure": "trace_flow",
            "artifact_label": "l1_probe",
            "envelope_bytes": 700,
            "compile_ms": 250.0,
            "probe_empty": True,
            "error": None,
        },
    ]
    agg = _aggregate(records)
    assert agg["state"] == "ok"
    assert agg["artifact_distribution"]["l1_probe"] == 3
    assert agg["artifact_distribution"]["facts"] == 1
    assert agg["procedure_distribution"]["trace_flow"] == 3
    # 3 of 4 are l1 → 75%
    assert agg["l1_route_rate_pct"] == 75
    # One probe_empty fact survived to top_misses.
    assert "d" in agg["top_misses"]
    assert agg["envelope_bytes"]["max"] == 800
    assert agg["compile_latency_ms"]["max"] == 250


def test_aggregate_isolates_errors():
    records = [
        {
            "prompt": "good",
            "procedure": "p",
            "artifact_label": "facts",
            "envelope_bytes": 100,
            "compile_ms": 10.0,
            "probe_empty": False,
            "error": None,
        },
        {
            "prompt": "bad",
            "procedure": None,
            "artifact_label": None,
            "envelope_bytes": 0,
            "compile_ms": 5.0,
            "probe_empty": True,
            "error": "ValueError: kaboom",
        },
    ]
    agg = _aggregate(records)
    assert agg["state"] == "ok"
    assert agg["artifact_distribution"] == {"facts": 1}
    assert agg["compile_errors"] and agg["compile_errors"][0]["prompt"] == "bad"


def test_compute_score_full_credit():
    agg = {
        "state": "ok",
        "l1_route_rate_pct": 100,
        "compile_latency_ms": {"p50": 100, "p95": 200, "max": 300},
    }
    # l1 = 60, latency = 40 → 100
    assert _compute_score(agg) == 100


def test_compute_score_no_data():
    assert _compute_score({"state": "not_initialized"}) == 0


# ----------------------------------------------------------------------
# CLI integration — runs the real compiler in-process
# ----------------------------------------------------------------------


def test_cli_missing_corpus_renders_not_initialized(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        compiler_corpus,
        ["--corpus", str(tmp_path / "nope.txt"), "--root", str(tmp_path)],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["summary"]["state"] == "not_initialized"
    assert env["prompts_processed"] == 0
    assert env["artifact_distribution"] == {}
    # LAW-4 anchored facts on the empty path too.
    for fact in env["agent_contract"]["facts"]:
        assert _is_law4_anchored(fact), f"unanchored: {fact!r}"


def test_cli_five_prompt_corpus_populates_envelope(tmp_path):
    path = _seed_corpus(tmp_path, _FIVE_PROMPTS, with_comments=True)
    runner = CliRunner()
    result = runner.invoke(
        compiler_corpus,
        ["--corpus", str(path), "--root", str(tmp_path), "--limit", "10"],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)

    assert env["summary"]["state"] == "ok"
    assert env["prompts_processed"] == 5
    assert env["corpus_path"].endswith("corpus.txt")
    assert isinstance(env["artifact_distribution"], dict)
    assert len(env["artifact_distribution"]) >= 1
    assert isinstance(env["procedure_distribution"], dict)
    assert len(env["procedure_distribution"]) >= 1
    assert 0 <= env["l1_route_rate_pct"] <= 100
    # Percentile structure
    for section in ("envelope_bytes", "compile_latency_ms"):
        assert set(env[section].keys()) == {"p50", "p95", "max"}
    # Score bounded
    assert 0 <= env["summary"]["score"] <= 100
    # Verdict mentions the headline numbers we promised
    verdict = env["summary"]["verdict"]
    assert "5 prompts" in verdict
    assert "% L1" in verdict
    assert "p50=" in verdict
    # LAW-4 anchor compliance on every fact
    for fact in env["agent_contract"]["facts"]:
        assert _is_law4_anchored(fact), f"unanchored: {fact!r}"


def test_cli_respects_limit(tmp_path):
    path = _seed_corpus(tmp_path, _FIVE_PROMPTS)
    runner = CliRunner()
    result = runner.invoke(
        compiler_corpus,
        ["--corpus", str(path), "--root", str(tmp_path), "--limit", "2"],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["prompts_processed"] == 2
