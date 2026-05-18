"""W805 - Empty-corpus regression pin for ``roam fan`` (W805 sweep).

Pins the Pattern 2 "always-emit" discipline on ``fan``'s no-rows
branches. The detector queries ``graph_metrics`` (symbol mode) or
``file_edges`` (file mode); with an empty corpus both queries
legitimately return zero rows. The pre-W805 path would have emitted
a misleading "no results" verdict without disclosing the empty state.

Two state branches are pinned here (one per ``mode``):

* symbol mode -> ``state: "no_symbols"`` when no rows survive the
  graph_metrics query.
* file mode   -> ``state: "no_file_edges"`` when ``file_edges`` is
  empty.

Contract asserted for each:
- exit code 0
- stdout parses as a single ``json_envelope`` dict
- ``summary.verdict`` mentions the empty state (corpus / empty / no)
- ``summary.partial_success`` is True (Pattern 2 always-emit)
- ``summary.state`` is the expected closed-enum value
- ``agent_contract.facts`` is a non-empty list
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_corpus_repo(tmp_path, monkeypatch):
    """A git repo with a single committed empty Python file.

    The committed file has no symbols so the corpus is effectively empty
    from the indexer's point of view -- ``symbols``, ``edges``,
    ``graph_metrics``, and ``file_edges`` all end up empty.
    """
    repo = tmp_path / "empty-fan-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )

    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo)
    assert rc == 0, f"roam init failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _invoke(args):
    from roam.cli import cli

    runner = CliRunner()
    return runner.invoke(cli, args, catch_exceptions=False)


def _assert_empty_corpus_envelope(result, *, expected_state, expected_mode):
    """Shared shape assertions for one empty-corpus fan envelope."""
    assert result.exit_code == 0, f"fan exited {result.exit_code}:\n{result.output}"

    raw = getattr(result, "stdout", None) or result.output
    assert raw.strip(), "stdout must NOT be empty in --json mode"
    env = _json.loads(raw)
    assert isinstance(env, dict)
    assert env.get("command") == "fan"

    summary = env.get("summary") or {}

    # Pattern 2 always-emit: partial_success MUST be True on empty-corpus
    # branches. The detector cannot meaningfully execute against zero rows
    # and the verdict must disclose that explicitly.
    assert summary.get("partial_success") is True, (
        f"summary.partial_success must be True on empty corpus, got: {summary.get('partial_success')!r}"
    )

    # State is a closed enum: the symbol / file mode each get a distinct
    # disclosure key so consumers can branch on them.
    assert summary.get("state") == expected_state, (
        f"summary.state must be {expected_state!r}, got: {summary.get('state')!r}"
    )
    assert summary.get("mode") == expected_mode, f"summary.mode must be {expected_mode!r}, got: {summary.get('mode')!r}"

    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got: {verdict!r}"
    verdict_lc = verdict.lower()
    # Empty-state verdict must explicitly disclose the absent corpus
    # rather than reading like a clean "no hotspots found" result.
    empty_markers = ("empty", "no graph", "no file edges", "no symbols")
    assert any(m in verdict_lc for m in empty_markers), f"verdict must mention empty state, got: {verdict!r}"

    # Auto-derived agent_contract.facts must be non-empty.
    contract = env.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) > 0, f"agent_contract.facts must be non-empty, got: {facts!r}"


def test_fan_symbol_mode_empty_corpus_emits_no_symbols_state(empty_corpus_repo):
    """``roam --json fan`` (symbol mode default) on empty corpus must
    surface ``state: "no_symbols"`` + ``partial_success: True``.
    """
    result = _invoke(["--json", "fan"])
    _assert_empty_corpus_envelope(
        result,
        expected_state="no_symbols",
        expected_mode="symbol",
    )


def test_fan_file_mode_empty_corpus_emits_no_file_edges_state(empty_corpus_repo):
    """``roam --json fan file`` on empty corpus must surface
    ``state: "no_file_edges"`` + ``partial_success: True``.

    The two state names are deliberately distinct (vs sharing ``"empty"``):
    a downstream consumer can tell whether the gap is at the symbol layer
    or the file-edge layer.
    """
    result = _invoke(["--json", "fan", "file"])
    _assert_empty_corpus_envelope(
        result,
        expected_state="no_file_edges",
        expected_mode="file",
    )
