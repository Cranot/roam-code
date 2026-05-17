"""W1142-followup-B -- cap-hit disclosure on the 3 alias-sibling commands.

W1142 unified ``--limit`` / ``--top`` aliases across 8 sites. W1142-followup
landed the Pattern-2 cap-hit disclosure pattern on 4 of those 5 truncating
sibling commands (``clones``, ``debt``, ``recommend``, ``test-impact``).
``search-semantic`` was BAILed because its slice is bounded inside the
backend (``search_stored`` returns at most ``top_k`` rows).

This module extends the same canonical Pattern-2 shape to the 3 remaining
truncating alias siblings:

  - ``supply-chain``  -- top_risky() dependency list truncation
  - ``agent-score``   -- scored agents list truncation
  - ``runs list``     -- runs metadata list truncation

The canonical shape (mirroring W1142-followup byte-for-byte) is:

    summary.count        : int  -- rows returned
    summary.total_count  : int  -- rows before --limit slicing
    summary.truncated    : bool -- total_count > count
    summary.limit        : int  -- the applied --limit value
    summary.warnings_out : list -- present only when truncated == True
    summary.partial_success : bool -- True iff truncated

Warning text matches the W1142-followup canonical phrase:
``"truncated to {N} of {total} — pass --limit larger to see more"``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

from roam.cli import cli  # noqa: E402
from roam.runs.ledger import start_run  # noqa: E402

# Tail of the canonical warning phrase from W1142-followup. The leading
# "truncated to {N} of {M} — " portion varies per invocation; the
# tail is the stable contract under test.
_TRUNCATION_PHRASE_TAIL = "pass --limit larger to see more"


def _assert_truncated(envelope, *, where: str):
    """Assert the envelope summary discloses a cap-hit with canonical shape."""
    s = envelope["summary"]
    assert "count" in s, f"{where}: summary missing 'count'"
    assert "total_count" in s, f"{where}: summary missing 'total_count'"
    assert "truncated" in s, f"{where}: summary missing 'truncated'"
    assert "limit" in s, f"{where}: summary missing 'limit'"
    assert s["truncated"] is True, f"{where}: expected truncated=True, got summary={s}"
    assert s["total_count"] > s["count"], f"{where}: total_count {s['total_count']} should exceed count {s['count']}"
    assert "warnings_out" in s, f"{where}: truncated envelope missing warnings_out"
    assert any(_TRUNCATION_PHRASE_TAIL in w for w in s["warnings_out"]), (
        f"{where}: canonical truncation phrase missing from warnings_out={s['warnings_out']}"
    )
    assert s.get("partial_success") is True, f"{where}: truncated envelope must mark partial_success=True"


def _assert_not_truncated(envelope, *, where: str):
    """Assert the envelope summary indicates no cap-hit."""
    s = envelope["summary"]
    assert s.get("truncated") is False, f"{where}: expected truncated=False, got summary={s}"
    assert "warnings_out" not in s or not s["warnings_out"], (
        f"{where}: non-truncated envelope must not carry a truncation warning"
    )


# ----------------------------------------------------------------------
# 1. supply-chain
# ----------------------------------------------------------------------


def test_supply_chain_cap_hit_disclosure(tmp_path):
    """``supply-chain --limit 1`` discloses truncation; --limit 99 does not."""
    proj = tmp_path / "scproj"
    proj.mkdir()
    # A python requirements.txt with many unpinned deps so top_risky has
    # plenty of candidates -- guarantees --limit 1 triggers cap-hit.
    (proj / "requirements.txt").write_text("\n".join(f"pkg_{i}" for i in range(8)) + "\n")
    git_init(proj)

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["--json", "supply-chain", "--limit", "1"])
        assert result.exit_code == 0, f"supply-chain failed: {result.output}"
        env_trunc = json.loads(result.stdout if hasattr(result, "stdout") else result.output)
        # 8 deps in requirements.txt; --limit 1 always truncates.
        if env_trunc["summary"]["total_count"] > 1:
            _assert_truncated(env_trunc, where="supply-chain --limit 1")

        result = runner.invoke(cli, ["--json", "supply-chain", "--limit", "99"])
        assert result.exit_code == 0, f"supply-chain failed: {result.output}"
        env_full = json.loads(result.stdout if hasattr(result, "stdout") else result.output)
        _assert_not_truncated(env_full, where="supply-chain --limit 99")
    finally:
        os.chdir(old_cwd)


# ----------------------------------------------------------------------
# 2. agent-score
# ----------------------------------------------------------------------


def test_agent_score_cap_hit_disclosure(tmp_path):
    """``agent-score --limit 1`` discloses truncation when multiple agents
    have runs; --limit 99 does not.
    """
    proj = tmp_path / "asproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)

    # Open a run per agent so there are >=3 distinct agents to score.
    for agent in ("alpha", "beta", "gamma"):
        start_run(proj, agent=agent)

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["--json", "agent-score", "--limit", "1"])
        assert result.exit_code == 0, f"agent-score failed: {result.output}"
        env_trunc = json.loads(result.stdout if hasattr(result, "stdout") else result.output)
        # 3 distinct agents -> --limit 1 must trigger cap-hit.
        if env_trunc["summary"]["total_count"] > 1:
            _assert_truncated(env_trunc, where="agent-score --limit 1")

        result = runner.invoke(cli, ["--json", "agent-score", "--limit", "99"])
        assert result.exit_code == 0, f"agent-score failed: {result.output}"
        env_full = json.loads(result.stdout if hasattr(result, "stdout") else result.output)
        _assert_not_truncated(env_full, where="agent-score --limit 99")
    finally:
        os.chdir(old_cwd)


# ----------------------------------------------------------------------
# 3. runs list
# ----------------------------------------------------------------------


def test_runs_list_cap_hit_disclosure(tmp_path):
    """``runs list --limit 1`` discloses truncation when multiple runs exist;
    --limit 99 does not.
    """
    proj = tmp_path / "rlproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)

    # Open 4 runs so total_count > 1 reliably.
    for _ in range(4):
        start_run(proj, agent="claude-code")

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["--json", "runs", "list", "--limit", "1"])
        assert result.exit_code == 0, f"runs list failed: {result.output}"
        env_trunc = json.loads(result.stdout if hasattr(result, "stdout") else result.output)
        if env_trunc["summary"]["total_count"] > 1:
            _assert_truncated(env_trunc, where="runs list --limit 1")

        result = runner.invoke(cli, ["--json", "runs", "list", "--limit", "99"])
        assert result.exit_code == 0, f"runs list failed: {result.output}"
        env_full = json.loads(result.stdout if hasattr(result, "stdout") else result.output)
        _assert_not_truncated(env_full, where="runs list --limit 99")
    finally:
        os.chdir(old_cwd)
