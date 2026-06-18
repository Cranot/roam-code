"""Pattern-2 empty-corpus disclosure for ``roam debt`` and ``roam alerts``.

B-cluster completion (2026-06-18). The W805 sweep pinned the empty-corpus
silent-SAFE for the symbol-graph commands (health/cycles/clusters/dashboard/
dead/clones) but never reached ``debt`` (B4) or ``alerts`` (B8) — both report
a clean bill on a 0-symbol corpus:

  * ``debt`` computes file-level debt from ``file_stats``, so on a docs-only
    repo it reports e.g. "low debt — top hotspot: README.md (score 0.0)",
    analyzing a README as if it were code.
  * ``alerts`` runs threshold checks against live metrics that are all
    vacuously healthy (there is no code), so it reports "no alerts — all
    metrics within normal ranges".

Both are the canonical Pattern-2 shape: an agent reading the verdict cannot
tell "clean because well-built" from "clean because there is nothing indexed"
(uncoded / index broken / wrong cwd). The fix (mirroring cmd_health and the
shared ``resolve.empty_corpus_state`` helper) discloses ``state="empty_corpus"``
+ ``partial_success=True`` + a verdict that names the empty corpus when
``COUNT(*) FROM symbols == 0``.

These are live assertions (not xfail pins): the fix ships in the same change.
A ``clean_corpus`` negative control confirms the guard is empty-corpus-specific
and does not perturb a populated repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli, parse_json_output  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Docs-only indexed repo: 1 file, 0 symbols.

    The indexer runs cleanly but extracts zero function/class/method symbols,
    so ``empty_corpus_state(conn)`` fires the disclosure path.
    """
    repo = tmp_path / "empty-bcluster-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "README.md").write_text("# docs only\n\nNo code here.\n", encoding="utf-8")
    git_init(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Populated indexed repo (negative control): real symbols present."""
    repo = tmp_path / "clean-bcluster-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "app.py").write_text(
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )
    git_init(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# debt (B4)
# ---------------------------------------------------------------------------


class TestDebtEmptyCorpus:
    def test_empty_corpus_discloses_state(self, empty_corpus, cli_runner, monkeypatch):
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["debt"], cwd=empty_corpus, json_mode=True)
        assert result.exit_code == 0, result.output
        summary = parse_json_output(result, "debt")["summary"]
        assert summary.get("state") == "empty_corpus", summary
        assert summary.get("partial_success") is True, summary

    def test_empty_corpus_verdict_not_silent_low_debt(self, empty_corpus, cli_runner, monkeypatch):
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["debt"], cwd=empty_corpus, json_mode=True)
        verdict = parse_json_output(result, "debt")["summary"]["verdict"].lower()
        # Must NOT report a clean debt bill or analyze the README as a hotspot.
        assert "low debt" not in verdict, verdict
        assert "readme" not in verdict, verdict
        # Must name the empty corpus.
        assert any(t in verdict for t in ("0 symbols", "no code", "empty")), verdict

    def test_clean_corpus_unaffected(self, clean_corpus, cli_runner, monkeypatch):
        """Negative control: a populated repo does NOT get the empty-corpus state."""
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(cli_runner, ["debt"], cwd=clean_corpus, json_mode=True)
        assert result.exit_code == 0, result.output
        summary = parse_json_output(result, "debt")["summary"]
        assert summary.get("state") != "empty_corpus", summary


# ---------------------------------------------------------------------------
# alerts (B8)
# ---------------------------------------------------------------------------


class TestAlertsEmptyCorpus:
    def test_empty_corpus_discloses_state(self, empty_corpus, cli_runner, monkeypatch):
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["alerts"], cwd=empty_corpus, json_mode=True)
        assert result.exit_code == 0, result.output
        summary = parse_json_output(result, "alerts")["summary"]
        assert summary.get("state") == "empty_corpus", summary
        assert summary.get("partial_success") is True, summary
        # Canonical required fields still present (shape parity with the
        # populated alerts envelope).
        for field in ("total", "critical", "warning", "info"):
            assert field in summary, f"missing {field!r}: {summary}"

    def test_empty_corpus_verdict_names_empty(self, empty_corpus, cli_runner, monkeypatch):
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["alerts"], cwd=empty_corpus, json_mode=True)
        verdict = parse_json_output(result, "alerts")["summary"]["verdict"].lower()
        assert any(t in verdict for t in ("0 symbols", "no code", "empty")), verdict

    def test_clean_corpus_unaffected(self, clean_corpus, cli_runner, monkeypatch):
        """Negative control: a populated repo does NOT get the empty-corpus state."""
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(cli_runner, ["alerts"], cwd=clean_corpus, json_mode=True)
        assert result.exit_code == 0, result.output
        summary = parse_json_output(result, "alerts")["summary"]
        assert summary.get("state") != "empty_corpus", summary
