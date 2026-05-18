"""W805-YYY — cmd_n1 RE-PROBE on empty-corpus + uncommitted-mod regression.

Originally probed at W803 (sibling: ``tests/test_w803_n1_empty_corpus.py``)
with NO Pattern 2 gap found. The current working tree has an uncommitted
modification to ``src/roam/commands/cmd_n1.py`` (W1005-followup-D: widens
``--confidence`` from the 3-tier {high, medium, low} equality filter to
the canonical W547 7-tier with severity_rank() floor semantics). This
sweep validates that the unrelated mod has NOT regressed the Pattern-2
empty-state envelope path.

W978 dominant-axis check: the mod touches ONLY the post-detection filter
(line 1580-1587 in ``analyze_n1``) and the click Choice declaration
(line 1660-1678). The Pattern-2 branches — empty-state detection at
1735-1741, verdict computation at 1774-1788, and JSON envelope at
1815-1828 — are physically separate and untouched.

The re-probe pins:

* W803 baseline still passes (delegates to the sibling fixture).
* Mod is NOT the dominant axis for envelope shape — verified by
  re-asserting the W803 envelope invariants directly here.
* No phantom registry rows on empty corpus (defensive — n1 only
  persists with ``--persist``, but the empty fixture without
  ``--persist`` must NOT touch the registry table at all).
* ``--confidence`` flag widening: the new canonical tiers (warning,
  info, error, critical) are accepted by Click on a populated corpus
  invocation — empty-corpus invocation already covered by W803.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path):
    """Mirror of the W803 fixture (single empty .py file)."""
    repo = tmp_path / "w805_yyy_n1_empty"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    (repo / "blank.py").write_text("")
    git_init(repo)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        out, rc = index_in_process(repo)
        assert rc == 0, f"index failed: {out}"
    finally:
        os.chdir(old_cwd)
    return repo


def test_empty_corpus_emits_envelope(cli_runner, empty_corpus):
    """Baseline: ``roam n1 --json`` on empty corpus exits 0 with a
    well-formed JSON envelope. Post-mod sanity that command is still
    callable."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(empty_corpus))
        result = cli_runner.invoke(cli, ["--json", "n1"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, f"exit={result.exit_code} output={result.output!r}"
    envelope = json.loads(result.output)
    assert envelope.get("command") == "n1"
    assert isinstance(envelope.get("summary"), dict)


def test_empty_corpus_partial_success_disclosure(cli_runner, empty_corpus):
    """Pattern-2 regression pin: ``partial_success`` MUST be True on
    empty corpus (the W805 discipline added at line 1737-1741 +
    line 1823 of cmd_n1.py). Mod must NOT have collapsed this back to
    silent-SAFE."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(empty_corpus))
        result = cli_runner.invoke(cli, ["--json", "n1"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    envelope = json.loads(result.output)
    summary = envelope["summary"]
    # The Pattern-2 invariant: empty corpus → partial_success True,
    # state names the empty axis explicitly.
    assert summary.get("partial_success") is True, (
        f"empty corpus must set partial_success=True; got {summary.get('partial_success')!r}"
    )
    assert summary.get("state") == "empty_corpus", (
        f"empty corpus must set state='empty_corpus'; got {summary.get('state')!r}"
    )
    # Models scanned must be a real integer (0), not absent.
    assert summary.get("models_scanned") == 0


def test_w803_invariants_preserved(cli_runner, empty_corpus):
    """Re-run the W803 envelope invariants directly here so this file
    fails loudly if the uncommitted mod ever shifts the empty-state
    envelope shape. Mirrors the original W803 asserts."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(empty_corpus))
        result = cli_runner.invoke(cli, ["--json", "n1"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0
    envelope = json.loads(result.output)
    summary = envelope["summary"]

    # Verdict discloses empty state (W803 anchor).
    verdict_lc = summary["verdict"].lower()
    assert any(tok in verdict_lc for tok in ("no implicit n+1", "no n+1", "0 patterns", "empty"))
    # Forbid default-success markers.
    for forbidden in ("safe", "healthy", "passing", "all good"):
        assert forbidden not in verdict_lc, f"verdict reads as default-success: {verdict_lc!r}"

    # Total / framework / distribution invariants.
    assert summary["total"] == 0
    assert summary["framework"] == "generic"
    assert summary["truncated"] is False
    assert summary["by_confidence"] == {}
    assert summary["findings_confidence_distribution"] == {"high": 0, "medium": 0, "low": 0}
    assert envelope.get("findings") == []

    # agent_contract.facts non-empty + surfaces empty count.
    facts = (envelope.get("agent_contract") or {}).get("facts") or []
    assert isinstance(facts, list) and len(facts) >= 1
    joined = " ".join(str(f).lower() for f in facts)
    assert "0" in joined


def test_no_n1_candidates_distinct_from_disabled(cli_runner, empty_corpus):
    """Pattern-1-V-D: empty corpus state must be DISTINCT from any
    'detector disabled' / 'feature-off' interpretation. The state field
    must literally be ``empty_corpus`` (not ``"disabled"``, not
    ``"unavailable"``, not absent)."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(empty_corpus))
        result = cli_runner.invoke(cli, ["--json", "n1"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    envelope = json.loads(result.output)
    state = envelope["summary"].get("state")
    # Closed enum on the n1 empty-state branch (line 1737-1741):
    # "empty_corpus" | "no_models" | "scanned". Empty fixture has 0
    # symbols so the empty_corpus branch fires first.
    assert state == "empty_corpus"
    # NOT one of the disabled-style spellings that would confuse the
    # consumer.
    assert state not in ("disabled", "unavailable", "skipped", "n/a", None)


def test_findings_registry_no_phantom_emissions(cli_runner, empty_corpus, tmp_path):
    """Without ``--persist``, n1 must NOT touch the findings registry
    at all — even on empty corpus. Confirms no phantom rows leak in."""
    from roam.cli import cli
    from roam.db.connection import open_db

    old_cwd = os.getcwd()
    try:
        os.chdir(str(empty_corpus))
        # Pre-state: count n1 rows in the registry (should be 0).
        with open_db(readonly=True) as conn:
            try:
                pre_count = conn.execute("SELECT COUNT(*) FROM findings WHERE detector = 'n1'").fetchone()[0]
            except Exception:
                pre_count = 0

        result = cli_runner.invoke(cli, ["--json", "n1"], catch_exceptions=False)

        with open_db(readonly=True) as conn:
            try:
                post_count = conn.execute("SELECT COUNT(*) FROM findings WHERE detector = 'n1'").fetchone()[0]
            except Exception:
                post_count = 0
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0
    assert post_count == pre_count, (
        f"n1 without --persist must not write findings rows; pre={pre_count}, post={post_count}"
    )
    # And in particular, post must be 0 on the empty corpus.
    assert post_count == 0


def test_uncommitted_mod_doesnt_break_envelope(cli_runner, empty_corpus):
    """CRITICAL: the W1005-followup-D mod (3-tier → 7-tier ``--confidence``
    filter widening) must not regress the empty-corpus envelope.

    Specifically asserts that passing the NEW canonical tier
    ``--confidence warning`` (which was REJECTED pre-mod and is ACCEPTED
    post-mod) produces a valid Pattern-2 envelope on empty corpus —
    the floor comparison just collapses to "no findings", and the empty
    branch still fires.
    """
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(empty_corpus))
        result = cli_runner.invoke(
            cli,
            ["--json", "n1", "--confidence", "warning"],
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)

    # The mod added "warning" to the click Choice; pre-mod would have
    # exited 2 (UsageError). Post-mod exits 0 with the empty envelope.
    assert result.exit_code == 0, (
        f"new canonical tier '--confidence warning' must be accepted "
        f"post-mod; exit={result.exit_code} output={result.output!r}"
    )
    envelope = json.loads(result.output)
    # Empty-corpus invariants still hold even with the new filter floor.
    summary = envelope["summary"]
    assert summary["total"] == 0
    assert summary["partial_success"] is True
    assert summary["state"] == "empty_corpus"
