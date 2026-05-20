"""W837 - Empty-corpus regression pin for ``roam forecast`` (Pattern-2 sweep).

Extends the W802-W836 empty-corpus silent-success campaign. ``forecast``
draws on three data layers — snapshot history (Theil-Sen aggregate trends),
per-symbol complexity/churn (at-risk ranking), and the current file graph
(one-shot spectral instability). On a freshly-indexed but symbol-less corpus
all three are vacuous:

* zero snapshots usable for aggregate trends (< 3 needed),
* zero symbols → no per-symbol risk,
* a degenerate 2-node file graph whose 0.0 spectral gap
  ``spectral_instability`` flags as ``is_failed`` (gap < failure band).

Pre-W837 the verdict read like a real architectural finding
("...spectral gap 0.000 in the failure band across 2 nodes") while
``summary.partial_success`` stayed ``False`` and no ``summary.state`` was
emitted — the canonical Pattern-2 silent success (an agent reading
``partial_success`` saw a clean run when there was nothing to forecast).

Contract asserted on an empty corpus:
- exit code 0
- stdout parses as a single ``json_envelope`` dict for ``command == "forecast"``
- ``summary.partial_success`` is True
- ``summary.state`` == ``"no_data"``
- ``summary.verdict`` names the empty corpus (not a "failure band" reading)
- ``agent_contract.facts`` is non-empty
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


@pytest.fixture
def empty_corpus_repo(tmp_path, monkeypatch):
    """A git repo with a single committed empty Python file (no symbols)."""
    repo = tmp_path / "empty-forecast-repo"
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


def _invoke(args):
    from roam.cli import cli

    runner = CliRunner()
    return runner.invoke(cli, args, catch_exceptions=False)


def test_forecast_empty_corpus_emits_no_data_state(empty_corpus_repo):
    """``roam --json forecast`` on an empty corpus must surface
    ``state: "no_data"`` + ``partial_success: True`` with an empty-state
    verdict — not a spectral "failure band" reading.
    """
    result = _invoke(["--json", "forecast"])
    assert result.exit_code == 0, f"forecast exited {result.exit_code}:\n{result.output}"

    raw = getattr(result, "stdout", None) or result.output
    assert raw.strip(), "stdout must NOT be empty in --json mode"
    env = _json.loads(raw)
    assert isinstance(env, dict)
    assert env.get("command") == "forecast"

    summary = env.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"partial_success must be True on empty corpus, got: {summary.get('partial_success')!r}"
    )
    assert summary.get("state") == "no_data", f"state must be 'no_data', got: {summary.get('state')!r}"

    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict
    verdict_lc = verdict.lower()
    assert any(m in verdict_lc for m in ("empty", "no data", "0 symbols")), (
        f"verdict must name the empty corpus, got: {verdict!r}"
    )
    # The misleading spectral "failure band" clause must NOT leak into the
    # empty-corpus verdict.
    assert "failure band" not in verdict_lc, (
        f"empty-corpus verdict must not read like a failure-band finding: {verdict!r}"
    )

    facts = (env.get("agent_contract") or {}).get("facts") or []
    assert isinstance(facts, list) and len(facts) > 0, f"agent_contract.facts must be non-empty, got: {facts!r}"
