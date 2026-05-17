"""W1084 — Denominator-clamp probe-breaking sweep.

The W805-followup-A vibe-check migration noted a sibling issue: when a
composite score command uses per-dimension ``total`` values that are
each clamped to ``max(_, 1)`` for division safety, the clamps make
``total == 0`` impossible. Downstream "is the corpus empty?" probes
that read those totals therefore cannot fire and the composite verdict
silently collapses to a healthy-looking score on an empty corpus.

The canonical fix template (W805-followup-A on vibe-check, W834 on
health, W836 on doctor): keep the clamp for division safety, AND add
an explicit ``empty_corpus = symbols_count == 0`` probe via a direct
``COUNT(*) FROM symbols`` query before the verdict is composed.

This module exercises the fix applied to ``cmd_ai_readiness`` —
previously the only PROBE-BREAKING site found above the W1084 cap.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Fixture: zero-byte Python file + roam init. Same canonical "empty
# corpus" used by tests/test_w805_empty_state_pattern_2.py.
# ---------------------------------------------------------------------------


def _make_empty_corpus(tmp: Path) -> None:
    (tmp / "empty.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=tmp,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "init",
            "-q",
        ],
        cwd=tmp,
        check=True,
    )
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(cwd)


def _run_cli_json(tmp_path: Path, *args: str) -> tuple[int, dict]:
    _make_empty_corpus(tmp_path)
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(cli, ["--json", *args], catch_exceptions=False)
    finally:
        os.chdir(cwd)
    payload = json.loads(result.output) if result.output.strip() else {}
    return result.exit_code, payload


# ---------------------------------------------------------------------------
# cmd_ai_readiness — outer composite must disclose empty corpus
# ---------------------------------------------------------------------------


class TestAiReadinessEmptyCorpus:
    """W1084 — ``roam ai-readiness`` on a corpus with zero symbols."""

    def test_partial_success_on_empty_corpus(self, tmp_path):
        """Empty corpus must NOT yield a silent-Healthy composite score.

        Before W1084: per-dimension ``total = max(total, 1)`` clamps in
        ``_score_dead_code`` (line 290) and ``_score_test_signal``
        (line 361) propagate to the composite, producing
        ``score >= 70`` with ``label="GOOD"`` even on a zero-symbol
        corpus. After W1084: the outer command queries
        ``COUNT(*) FROM symbols`` directly and disclosure flips to
        ``partial_success: true`` / ``state: "no_symbols_indexed"``.
        """
        exit_code, payload = _run_cli_json(tmp_path, "ai-readiness")
        assert exit_code == 0, payload
        summary = payload.get("summary", {})

        # Direct probe must surface zero symbols.
        assert summary.get("symbols_count") == 0, summary

        # Pattern 2 disclosure: empty corpus -> partial_success + state.
        assert summary.get("partial_success") is True, summary
        assert summary.get("state") == "no_symbols_indexed", summary

        # LAW 6: verdict line must work standalone — name the absent
        # state, not the silently-fine score.
        verdict = summary.get("verdict", "")
        assert "corpus empty" in verdict or "no files scanned" in verdict, verdict

    def test_verdict_does_not_overclaim_optimized(self, tmp_path):
        """Empty-corpus verdict must not contain a positive label.

        Before W1084 the verdict on an empty corpus was something like
        ``"AI Readiness 86/100 -- OPTIMIZED"`` — indistinguishable
        from a real high-quality codebase. After W1084 the verdict
        must explicitly name the empty state instead.
        """
        _, payload = _run_cli_json(tmp_path, "ai-readiness")
        verdict = payload.get("summary", {}).get("verdict", "")
        # The 5 positive band labels from _readiness_label() —
        # none of them should appear on an empty corpus.
        for forbidden in ("OPTIMIZED", "GOOD", "FAIR", "POOR", "HOSTILE"):
            assert forbidden not in verdict, (forbidden, verdict)
