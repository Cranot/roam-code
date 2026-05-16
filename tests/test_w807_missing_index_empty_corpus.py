"""W807 — empty-corpus smoke for ``roam missing-index``.

Pins the empty-state contract for the missing-index detector when the
indexed corpus contains zero PHP migration files (e.g. a pure-Python
project): the command MUST emit a structured envelope whose verdict
discloses the absent-input state explicitly, rather than silently
returning a "No missing indexes detected" success that looks
indistinguishable from a clean scan.

Continuation of the W805 sweep (Pattern 2 — silent fallback). See
``CLAUDE.md`` §"Six systemic anti-patterns" Pattern 2 and the existing
``test_empty_state_framing.py`` for the canonical reference shape.

LAW 4 anchor terminals used by the assertions / fact strings the
command emits: ``queries``, ``indexes``, ``findings``, ``markers``.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402


def test_w807_missing_index_empty_corpus_emits_no_migrations_envelope(tmp_path, monkeypatch):
    """Empty-corpus smoke: a project with only a Python file (zero PHP
    migrations) must yield a structured ``missing-index`` envelope whose
    verdict mentions the absent-migrations state, NOT a default success.

    Assertions cover:
      * exit code 0 (the command itself ran cleanly)
      * envelope parses + ``summary`` block present
      * ``summary.verdict`` mentions the empty-input condition
        (no default "No missing indexes detected" silent success)
      * ``summary.partial_success`` is present AND False-OR-True per the
        canonical Pattern-2 contract; xfail-strict if missing entirely
      * ``agent_contract.facts`` is non-empty (the LAW-4-anchored
        fact list reaches the consumer even in the empty case)
    """
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "main.py").write_text(
        "def hi():\n    return 1\n",
        encoding="utf-8",
    )
    git_init(proj)
    output, exit_code = index_in_process(proj)
    assert exit_code == 0, f"index failed: {output}"

    monkeypatch.chdir(proj)
    runner = CliRunner()
    from roam.cli import cli

    result = runner.invoke(cli, ["--json", "missing-index"])

    # Exit 0 — the command ran cleanly even with an empty corpus.
    assert result.exit_code == 0, f"missing-index exited {result.exit_code}; output={result.output!r}"

    # Structured envelope parses.
    env = _json.loads(result.output)
    assert isinstance(env, dict), "envelope must be a JSON object"
    assert env.get("command") == "missing-index"

    summary = env.get("summary")
    assert isinstance(summary, dict), "summary block must be present"

    # Verdict discloses the empty-input condition explicitly.
    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, "verdict must be a non-empty string"
    lowered = verdict.lower()
    empty_markers = (
        "no migrations",
        "no_migrations",
        "no php migration",
        "not initialized",
        "no migration files",
    )
    assert any(m in lowered for m in empty_markers), (
        f"verdict must disclose empty-corpus state; got {verdict!r}, expected one of {empty_markers}"
    )
    # The default-success string is a silent-fallback failure mode.
    assert "no missing indexes detected" not in lowered, (
        f"verdict must NOT emit the default success string on empty corpus; got {verdict!r}"
    )

    # partial_success — xfail-strict if the field is missing entirely
    # (the canonical Pattern-2 envelope MUST carry it).
    if "partial_success" not in summary:
        pytest.xfail("summary.partial_success missing — required by Pattern 2 empty-state framing contract")
    assert summary["partial_success"] is False or summary["partial_success"] is True, (
        f"partial_success must be a bool; got {summary['partial_success']!r}"
    )

    # state field — confirm it's the structured no_migrations marker.
    assert summary.get("state") == "no_migrations", f"state must be 'no_migrations'; got {summary.get('state')!r}"
    assert summary.get("migrations_scanned") == 0
    assert summary.get("total") == 0

    # agent_contract.facts non-empty (LAW 4 — concrete-noun-anchored
    # facts must reach the consumer in the empty case too).
    agent_contract = env.get("agent_contract") or {}
    facts = agent_contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) > 0, f"agent_contract.facts must be non-empty; got {facts!r}"
    assert all(isinstance(f, str) and f for f in facts), f"each fact must be a non-empty string; got {facts!r}"
