"""W837 - Empty-corpus regression pin for ``roam coverage-gaps`` (Pattern-2 sweep).

Extends the W802-W836 empty-corpus silent-success campaign.
``coverage-gaps`` walks the call graph to verify that every entry point can
reach a required gate symbol. It requires a ``--gate`` / ``--gate-pattern``
filter; on an empty corpus (or any filter that matches nothing) ``_find_gates``
returns no gate symbols.

Pre-W837 the no-gates branch emitted a verdict-LESS envelope —
``summary == {"error": "No gate symbols found"}`` with empty
``agent_contract.facts`` and no ``verdict`` (LAW 6 violation) and no
``partial_success`` / ``state`` flag. A consumer reading ``summary.verdict``
saw ``None``, indistinguishable from a malformed/empty envelope (Pattern-2:
the absent-state was never disclosed). The fix stamps a standalone verdict +
``partial_success: True`` + ``state: "no_gates"``.

Contract asserted on an empty corpus with a gate that matches no symbols:
- exit code 0
- stdout parses as a single ``json_envelope`` dict for ``command == "coverage-gaps"``
- ``summary.verdict`` is a non-empty standalone line (LAW 6)
- ``summary.partial_success`` is True
- ``summary.state`` == ``"no_gates"``
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
    repo = tmp_path / "empty-covgaps-repo"
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


def test_coverage_gaps_no_gates_empty_corpus_emits_state(empty_corpus_repo):
    """``roam --json coverage-gaps --gate <unmatched>`` on an empty corpus must
    surface ``state: "no_gates"`` + ``partial_success: True`` + a standalone
    verdict, not a verdict-less ``{"error": ...}`` envelope.
    """
    # ``--gate empty.py`` matches no symbol (the file has none).
    result = _invoke(["--json", "coverage-gaps", "--gate", "nonexistent_gate_symbol"])
    assert result.exit_code == 0, f"coverage-gaps exited {result.exit_code}:\n{result.output}"

    raw = getattr(result, "stdout", None) or result.output
    assert raw.strip(), "stdout must NOT be empty in --json mode"
    env = _json.loads(raw)
    assert isinstance(env, dict)
    assert env.get("command") == "coverage-gaps"

    summary = env.get("summary") or {}
    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, (
        f"summary.verdict must be a non-empty standalone line, got: {verdict!r}"
    )

    assert summary.get("partial_success") is True, (
        f"partial_success must be True on no-gates, got: {summary.get('partial_success')!r}"
    )
    assert summary.get("state") == "no_gates", f"state must be 'no_gates', got: {summary.get('state')!r}"
    # The error sidecar is preserved for backward compatibility.
    assert summary.get("error") == "No gate symbols found"

    verdict_lc = verdict.lower()
    assert any(m in verdict_lc for m in ("no gate", "cannot be computed", "empty")), (
        f"verdict must disclose the absent gate, got: {verdict!r}"
    )

    facts = (env.get("agent_contract") or {}).get("facts") or []
    assert isinstance(facts, list) and len(facts) > 0, f"agent_contract.facts must be non-empty, got: {facts!r}"
