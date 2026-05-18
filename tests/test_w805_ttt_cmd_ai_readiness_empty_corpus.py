"""W805-TTT — Pattern-2 silent-SAFE pin for ``cmd_ai_readiness`` on empty corpus.

Seventy-second-in-batch W805 sweep. ``cmd_ai_readiness.py`` is a 7-dimension
composite scorer (weighted 0..100) consumed by ``cmd_describe`` +
``cmd_dashboard``; predicted aggregator-family peer of W805-EEE
(cmd_agent_score) and W805-HHH (cmd_capsule) on the vacuous-max axis.

W978 first-hypothesis re-run BEFORE writing any pin
====================================================

Hypothesis: "7-dimension composite scorer + 0 symbols => silent 100/100
SAFE => verdict-band peer of W805-PP / W805-833".

Empirical probe (empty corpus, README.md + .gitignore only):

    summary.verdict           = "no files scanned (corpus empty — run
                                 `roam index --force` to populate)"
    summary.score             = 83
    summary.label             = "OPTIMIZED"
    summary.partial_success   = true                    <-- W1084 guard set
    summary.state             = "no_symbols_indexed"    <-- W1084 guard set
    summary.symbols_count     = 0
    summary.files_scanned     = 2

W1084 ALREADY GUARDED THE TEXT-VERDICT AXIS but the **machine-readable**
``summary.score`` + ``summary.label`` fields still encode vacuous-max.
On a truly degenerate corpus (no README.md, just .gitignore):

    score = 80  label = "GOOD"

5 of 7 dimensions collapse to 100 on a 0-symbol corpus (naming,
coupling, dead_code, navigability, architecture all return their
"empty input" sentinel 100); test_signal returns 50 (also a sentinel,
not a measurement); documentation returns 30 (README present only).
Composite weighted average lands at 80-83 = "GOOD"/"OPTIMIZED" band.

Pattern-2 silent fallback at the structured-field axis: the verdict
*string* names the empty state, but consumers reading ``summary.score``
or ``summary.label`` for routing/gating decisions see a healthy band.
This is the vacuous-max bug pinned across W805-HHH/PP/833.

W978 hypothesis CONFIRMED on the score/label axis (not the verdict-text
axis, which is already W1084-fixed).

Bug class: Pattern-2 silent fallback — vacuous-max composite when 5/7
upstream dimensions return their "empty input" 100 sentinel without
disclosing the input was empty. Companion to W805-HHH cmd_capsule
health_score=100 bug.

Source-of-truth lines:
  src/roam/commands/cmd_ai_readiness.py:162  (naming: 100 on 0 rows)
  src/roam/commands/cmd_ai_readiness.py:225  (coupling: 100 on |G|=0)
  src/roam/commands/cmd_ai_readiness.py:299  (dead: 100 on 0 dead)
  src/roam/commands/cmd_ai_readiness.py:459  (nav: 100 on no files)
  src/roam/commands/cmd_ai_readiness.py:536  (arch: 100 on |G|=0)
  src/roam/commands/cmd_ai_readiness.py:730  (composite over vacuous 100s)

Pinned via ``xfail(strict=True)`` so the future fix flips xpass and the
gate fails loudly. Positive companion tests assert the wrapper is
crash-free + already-fixed text-verdict axis stays fixed.

Run isolation:
  python -m pytest tests/test_w805_ttt_cmd_ai_readiness_empty_corpus.py -x -n 0
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 — verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_AI_READINESS_SPEC = importlib.util.find_spec("roam.commands.cmd_ai_readiness")


def test_command_exists_or_skip():
    """W978/W907 existence gate: cmd_ai_readiness must be importable."""
    if _CMD_AI_READINESS_SPEC is None:
        pytest.skip("roam.commands.cmd_ai_readiness not installed in this environment")
    assert _CMD_AI_READINESS_SPEC is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus_project(tmp_path, monkeypatch):
    """Indexed project containing no source-code symbols.

    A README.md + .gitignore is enough to satisfy ``git init`` and roam's
    index pipeline, but the corpus has 0 symbols and 0 edges (markdown is
    not a symbol-producing language). This is the canonical "degenerate
    corpus" axis the W805 sweep targets.
    """
    proj = tmp_path / "empty_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("# empty repo for W805-TTT probe\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on empty corpus: {out}"
    return proj


@pytest.fixture
def clean_corpus_project(tmp_path, monkeypatch):
    """Indexed project with real Python source + README — positive control."""
    proj = tmp_path / "clean_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("# clean repo\n")
    (proj / "app.py").write_text(
        '"""App module."""\n\n'
        "def hello():\n"
        '    """Return greeting."""\n'
        "    return 'world'\n\n"
        "def greet(name):\n"
        '    """Greet by name."""\n'
        "    return hello() + name\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on clean corpus: {out}"
    return proj


# ---------------------------------------------------------------------------
# Invoke helper
# ---------------------------------------------------------------------------


def _invoke(runner, project_path, json_mode=False):
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("ai-readiness")

    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result):
    assert result.exit_code == 0, f"ai-readiness exit={result.exit_code}:\n{result.output}"
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON: {e}\nOutput head:\n{result.output[:500]}")


# ---------------------------------------------------------------------------
# Positive tests — empty-corpus envelope must remain parseable + crash-free
# (Pattern-1 variant C — never emit empty stdout)
# ---------------------------------------------------------------------------


class TestEmptyCorpusEnvelopeShape:
    """The W1084 text-verdict guard ALREADY shipped; assert it stays shipped."""

    def test_empty_corpus_no_crash(self, empty_corpus_project, cli_runner):
        """ai-readiness on empty corpus must not crash (Pattern-1-V-C)."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        assert result.exit_code == 0, f"ai-readiness crashed on empty corpus (Pattern-1 variant C):\n{result.output}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus_project, cli_runner):
        """JSON envelope must carry a summary.verdict string (LAW 6)."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert "verdict" in summary, f"summary missing 'verdict': {summary}"
        assert isinstance(summary["verdict"], str) and summary["verdict"], "verdict must be a non-empty string"

    def test_empty_corpus_state_explicit(self, empty_corpus_project, cli_runner):
        """W1084-shipped guard: state='no_symbols_indexed' on 0-symbol corpus."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        # W1084 already lands this — assert it stays landed.
        assert summary.get("state") == "no_symbols_indexed", (
            f"W1084 guard regression: expected state='no_symbols_indexed', got {summary.get('state')!r}"
        )

    def test_empty_corpus_partial_success_set(self, empty_corpus_project, cli_runner):
        """W1084-shipped guard: partial_success=True on 0-symbol corpus."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("partial_success") is True, (
            f"W1084 guard regression: expected partial_success=True, got {summary.get('partial_success')!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus_project, cli_runner):
        """LAW 6 — verdict must be self-contained (no 'see X' indirections)."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "")
        lowered = verdict.lower()
        assert "see " not in lowered and "details" not in lowered, f"LAW 6 violation — verdict indirects: {verdict!r}"

    def test_no_silent_ready_on_empty_text_verdict(self, empty_corpus_project, cli_runner):
        """W1084-shipped: text verdict must NOT read as a SAFE 'AI Readiness X/100'."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "").lower()
        forbidden = ("ai readiness ", "optimized", "good", "fair")
        offenders = [p for p in forbidden if p in verdict]
        assert not offenders, (
            f"W1084 guard regression: empty-corpus verdict reads as a "
            f"SAFE readiness band: {verdict!r}; offenders={offenders}"
        )


# ---------------------------------------------------------------------------
# REAL BUG — Pattern-2 silent SAFE on empty corpus
# (vacuous-max composite score + label survive the W1084 text-verdict guard)
# Pinned xfail(strict=True): a fix flips these to xpass -> test failure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-TTT Pattern-2 bug: cmd_ai_readiness emits summary.score=83 + "
        "summary.label='OPTIMIZED' on a 0-symbol corpus because 5/7 "
        "dimensions return their 'empty input' 100 sentinel without "
        "disclosing the degeneracy. The W1084 fix landed the *text-verdict* "
        "axis (verdict reads 'no files scanned…') and the *state* axis "
        "(state='no_symbols_indexed', partial_success=True), but the "
        "machine-readable summary.score + summary.label fields still encode "
        "vacuous-max. Fix: either zero out / null out the score and label "
        "when state='no_symbols_indexed', or compute the composite only "
        "over dimensions that had a non-zero input set. See CLAUDE.md "
        "'Six systemic anti-patterns' section 2; companion pin W805-HHH."
    ),
)
class TestEmptyCorpusPattern2VacuousMax:
    def test_no_vacuous_max_score(self, empty_corpus_project, cli_runner):
        """Vacuous-max axis: empty corpus must not earn a >= 50 composite."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        # 0 symbols cannot earn a 'GOOD' band (>=66). Any band beyond
        # 'POOR' (<=45) is vacuous-max by definition.
        assert summary.get("score", 0) < 50, (
            f"Vacuous-max: 0-symbol corpus produced score "
            f"{summary.get('score')} (>=50). Composite collapses to "
            f"5*100-sentinel dimensions. Either suppress the score "
            f"(emit null) or compute over non-vacuous dimensions only."
        )

    def test_no_vacuous_max_label(self, empty_corpus_project, cli_runner):
        """Vacuous-max axis: empty corpus must not earn a GOOD/OPTIMIZED label."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        label = summary.get("label", "")
        assert label not in ("GOOD", "OPTIMIZED"), (
            f"Vacuous-max: 0-symbol corpus produced label {label!r}. "
            f"Either suppress the label (emit null / 'UNKNOWN') or compute "
            f"over non-vacuous dimensions only."
        )

    def test_machine_readable_consistent_with_verdict(self, empty_corpus_project, cli_runner):
        """Score/label and verdict text must agree on the degraded-corpus signal."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        verdict = summary.get("verdict", "").lower()
        # The verdict already says "corpus empty". The machine-readable
        # band must reflect the same signal — not "OPTIMIZED".
        if "corpus empty" in verdict or "no files scanned" in verdict:
            label = summary.get("label", "")
            assert label in ("", None, "UNKNOWN", "HOSTILE", "POOR"), (
                f"Machine-readable / verdict-text disagreement: verdict says {verdict!r} but label={label!r}."
            )


# ---------------------------------------------------------------------------
# Positive control — clean corpus must still emit a real readiness score
# (guards against an over-eager fix that breaks the healthy path)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_readiness(clean_corpus_project, cli_runner):
    """Clean corpus: real symbols + a real band that is not the empty sentinel."""
    result = _invoke(cli_runner, clean_corpus_project, json_mode=True)
    data = _parse_json(result)
    summary = data.get("summary", {})
    assert summary.get("symbols_count", 0) >= 1, (
        f"clean corpus produced 0 symbols — fixture or indexer regression: {summary!r}"
    )
    # The clean-corpus verdict should reflect AI Readiness scoring, not the
    # empty-corpus disclaimer text.
    verdict = summary.get("verdict", "").lower()
    assert "ai readiness" in verdict, f"clean corpus lost the readiness verdict shape: {verdict!r}"
    # Clean corpus must NOT carry the empty-corpus state.
    assert summary.get("state") != "no_symbols_indexed", f"clean corpus produced empty-corpus state: {summary!r}"
    # partial_success must be False on a fully-resolved clean corpus.
    assert summary.get("partial_success") is not True, f"clean corpus erroneously flagged partial_success: {summary!r}"
