"""W805-WWW — Pattern-2 silent-SAFE pin for ``cmd_dark_matter`` on empty corpus.

Seventy-sixth-in-batch W805 sweep. ``cmd_dark_matter.py`` is an
aggregator-shape detector (hidden co-change coupling). Modified-but-
uncommitted in git status. The uncommitted W641-followup-G mods add a
canonical W631 risk-LEVEL projection (``risk_level_canonical`` /
``risk_rank``) to summary + envelope head; they do NOT touch the
Pattern-2 empty-state disclosure.

W978 first-hypothesis re-run BEFORE writing any pin
====================================================

Hypothesis: "0 co-change pairs above threshold => silent SAFE verdict
that doesn't distinguish 'analyzed cleanly, no pairs' from 'co-change
rows exist but ALL below threshold' from 'no co-change history at all'".

The library function ``dark_matter_edges()`` returns ``list[dict]`` filtered
by TWO criteria:

  1. ``cochange_count >= min_cochanges`` (default 3) — pairs that haven't
     moved together at least 3 times are discarded.
  2. ``npmi >= min_npmi`` (default 0.3) — pairs that did but with weak
     statistical correlation are also discarded.

The W805-followup-D guard (already in cmd_dark_matter:389-393, 521-526)
ONLY checks ``COUNT(*) FROM git_cochange == 0`` to disclose
``state="no_cochange"`` / ``partial_success=True``. That misses the
common case where ``git_cochange`` rows EXIST but none pass the
``min_cochanges`` threshold.

Empirical probe (3-file corpus with single init commit):

    git_cochange rows           = 6  (all cochange_count=1)
    git_commits rows            = 1
    threshold (min_cochanges=3) = nothing passes
    output:
      summary.verdict           = "0 dark-matter couplings found (risk_level low)"
      summary.partial_success   = False                    <-- BUG: silent SAFE
      summary.state             = (absent)                 <-- BUG: no disclosure
      summary.total_dark_matter_edges = 0

The W805-followup-D `state="no_cochange"` is set ONLY when the cochange
TABLE is empty. The much more common "rows exist but all below threshold"
path emits an identical "0 dark-matter couplings found" verdict to a
clean populated repository where the algorithm legitimately found no
hidden coupling. Pattern-1-V-D (silent success on degraded resolution):
the detector RAN, but ran on a degenerate input where the gate
``cochange_count >= 3`` excluded every candidate before NPMI evaluation.

W978 hypothesis CONFIRMED on the threshold-not-met axis (not the
no-cochange axis which is already W805-followup-D-fixed).

W907 verify-cycle check
========================

cmd_dark_matter has no defensive "would create cycle" docstrings. The
local import ``from roam.graph.dark_matter import ...`` at line 309 is
legitimate lazy-import (defer engine + regex compilation to first use),
not a cycle hedge. PASS — no false hedges.

Bug class: Pattern-1-V-D silent success on degraded resolution +
Pattern-2 silent fallback. The library applied two filter gates
(``min_cochanges`` + ``min_npmi``) and the command reports SAFE
without disclosing that the gates excluded every candidate.

Source-of-truth lines:
  src/roam/commands/cmd_dark_matter.py:389-393   (state guard ONLY checks
                                                  empty table, misses
                                                  threshold-not-met)
  src/roam/commands/cmd_dark_matter.py:432       ("0 dark-matter couplings
                                                  found" — same string in
                                                  two distinct conditions)
  src/roam/graph/dark_matter.py:50-53            (the ``cochange_count >=
                                                  min_cochanges`` filter
                                                  whose lineage is lost)

Fix template (NOT applied — pinned via xfail only):

  threshold_excluded = (cochange_rows_total > 0) AND (pairs == [])
  if threshold_excluded:
      summary.state = "no_pairs_above_threshold"
      summary.partial_success = True
      verdict = f"0 pairs above min_cochanges={min_cochanges} (raw rows: {N})"

Pinned via ``xfail(strict=True)`` so the future fix flips xpass and the
gate fails loudly. Positive companion tests assert wrapper crash-free
+ already-fixed no-cochange-state axis stays fixed.

Run isolation:
  python -m pytest tests/test_w805_www_cmd_dark_matter_empty_corpus.py -x -n 0
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

_CMD_DARK_MATTER_SPEC = importlib.util.find_spec("roam.commands.cmd_dark_matter")


def test_command_exists_or_skip():
    """W978/W907 existence gate: cmd_dark_matter must be importable."""
    if _CMD_DARK_MATTER_SPEC is None:
        pytest.skip("roam.commands.cmd_dark_matter not installed in this environment")
    assert _CMD_DARK_MATTER_SPEC is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def no_git_project(tmp_path, monkeypatch):
    """Indexed project with NO git history at all.

    Exercises the W805-followup-D guard path: ``git_cochange`` is empty,
    ``state="no_cochange"`` should be stamped.
    """
    proj = tmp_path / "no_git_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "a.py").write_text("x=1\n")
    # NO git_init — leaves git_commits / git_cochange tables empty.
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on no-git corpus: {out}"
    return proj


@pytest.fixture
def threshold_not_met_project(tmp_path, monkeypatch):
    """Indexed project with cochange rows BUT all below min_cochanges=3.

    A single init commit touches all files together once, producing
    cochange rows with ``cochange_count=1``. The default
    ``--min-cochanges=3`` filter excludes every candidate before NPMI
    even runs. This is the Pattern-1-V-D axis the pin targets.
    """
    proj = tmp_path / "low_signal_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "a.py").write_text("x=1\n")
    (proj / "b.py").write_text("y=2\n")
    (proj / "README.md").write_text("# r\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on threshold-not-met corpus: {out}"
    return proj


# ---------------------------------------------------------------------------
# Invoke helper
# ---------------------------------------------------------------------------


def _invoke(runner, project_path, json_mode=False, extra_args=()):
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("dark-matter")
    args.extend(extra_args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result):
    assert result.exit_code == 0, f"dark-matter exit={result.exit_code}:\n{result.output}"
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON: {e}\nOutput head:\n{result.output[:500]}")


# ---------------------------------------------------------------------------
# Positive shape tests — envelope must be parseable + crash-free
# (Pattern-1 variant C — never emit empty stdout)
# ---------------------------------------------------------------------------


class TestEmptyCorpusEnvelopeShape:
    """Empty-corpus invariants that should already hold today."""

    def test_empty_corpus_emits_envelope(self, no_git_project, cli_runner):
        """No-git corpus: dark-matter must not crash and emits real JSON."""
        result = _invoke(cli_runner, no_git_project, json_mode=True)
        assert result.exit_code == 0, f"dark-matter crashed on no-git corpus (Pattern-1-V-C):\n{result.output}"
        data = _parse_json(result)
        assert data.get("command") == "dark-matter"
        assert "summary" in data
        assert "verdict" in data["summary"]
        assert isinstance(data["summary"]["verdict"], str)
        assert data["summary"]["verdict"]

    def test_no_git_history_disclosed_via_state(self, no_git_project, cli_runner):
        """W805-followup-D guard: empty git_cochange table must disclose state."""
        result = _invoke(cli_runner, no_git_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        # W805-followup-D already lands this — assert it stays landed.
        assert summary.get("state") == "no_cochange", (
            f"W805-followup-D guard regression: expected state='no_cochange', "
            f"got {summary.get('state')!r}; summary={summary!r}"
        )
        assert summary.get("partial_success") is True, (
            f"W805-followup-D guard regression: expected partial_success=True, got {summary.get('partial_success')!r}"
        )

    def test_no_git_history_verdict_loud(self, no_git_project, cli_runner):
        """Verdict must NAME the empty-cochange state — LAW 6 standalone-parse."""
        result = _invoke(cli_runner, no_git_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "").lower()
        # Should contain a recognizable disclosure token.
        markers = ("no co-change history", "0 cochange records", "no_cochange")
        offenders = [m for m in markers if m in verdict]
        assert offenders, f"LAW 6 violation — no-cochange verdict lacks disclosure: {verdict!r}"

    def test_law6_verdict_standalone(self, no_git_project, cli_runner):
        """LAW 6 — verdict must be self-contained (no 'see X' indirections)."""
        result = _invoke(cli_runner, no_git_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "").lower()
        assert "see " not in verdict and "details" not in verdict, f"LAW 6 violation — verdict indirects: {verdict!r}"

    def test_no_co_change_distinct_from_no_commits_verdict_text(
        self,
        no_git_project,
        threshold_not_met_project,
        cli_runner,
    ):
        """Verdict text differs between no-git and threshold-not-met paths.

        Already holds today (no-git path emits the W805-followup-D
        disclosure; threshold-not-met path emits the buggy generic SAFE).
        Pin it positive so a future refactor that collapses both back
        into the same string regresses loudly.
        """
        no_git_data = _parse_json(_invoke(cli_runner, no_git_project, json_mode=True))
        threshold_data = _parse_json(_invoke(cli_runner, threshold_not_met_project, json_mode=True))
        no_git_verdict = no_git_data["summary"]["verdict"]
        threshold_verdict = threshold_data["summary"]["verdict"]
        assert no_git_verdict != threshold_verdict, (
            f"Two distinct empty-state axes collapsed to the same verdict.\n"
            f"  no-git:    {no_git_verdict!r}\n"
            f"  threshold: {threshold_verdict!r}"
        )

    def test_findings_registry_no_phantom_emissions(self, no_git_project, cli_runner, threshold_not_met_project):
        """No-pairs + no --persist: no phantom findings rows.

        With ``--persist`` absent AND pairs empty, no row should be
        written to the findings registry. We approximate by asserting
        that the persist branch is gated on ``pairs`` truthiness
        (line 334: ``if persist and pairs:``).
        """
        # Just invoke without --persist; verify it doesn't crash, doesn't
        # silently scribble. We can't directly assert "no row written"
        # without a registry probe but the structural guard is the
        # ``if persist and pairs:`` gate at cmd_dark_matter:334.
        result = _invoke(cli_runner, no_git_project, json_mode=True)
        assert result.exit_code == 0
        data = _parse_json(result)
        # The envelope shape stays clean — no spurious "persisted N findings"
        # field, no error key.
        assert data.get("dark_matter_pairs") == [], (
            f"no-pairs path emitted phantom pairs: {data.get('dark_matter_pairs')!r}"
        )


# ---------------------------------------------------------------------------
# REAL BUG — Pattern-1-V-D silent success on threshold-excluded resolution
# Pinned xfail(strict=True): a fix flips these to xpass -> test failure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-WWW Pattern-1-V-D / Pattern-2 bug: cmd_dark_matter emits "
        "verdict='0 dark-matter couplings found' + partial_success=False "
        "+ NO state stamp when the corpus HAS co-change rows but all are "
        "below min_cochanges threshold (default 3). The W805-followup-D "
        "guard at cmd_dark_matter.py:389-393 only checks "
        "COUNT(*) FROM git_cochange == 0; it misses the much more common "
        "'rows exist but threshold filter excluded all'. Two distinct "
        "input shapes (clean populated graph w/ no hidden coupling vs "
        "low-signal corpus where filter ate every candidate) collapse to "
        "the same SAFE verdict. Fix: when "
        "(cochange_rows_total > 0 AND pairs == []), stamp "
        "state='no_pairs_above_threshold' + partial_success=True + name "
        "the threshold in the verdict. See CLAUDE.md 'Six systemic "
        "anti-patterns' section 1 variant D + section 2."
    ),
)
class TestThresholdNotMetPattern1VD:
    def test_partial_success_when_threshold_not_met(self, threshold_not_met_project, cli_runner):
        """Threshold-excluded corpus must NOT report silent SAFE."""
        result = _invoke(cli_runner, threshold_not_met_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        # The git_cochange table HAS rows (init commit touches every file),
        # but no row clears min_cochanges=3. Today the command claims clean
        # SAFE; the bug is the absent partial_success disclosure.
        assert summary.get("partial_success") is True, (
            f"Pattern-1-V-D: cochange rows exist but threshold excluded "
            f"every candidate; expected partial_success=True, "
            f"got {summary.get('partial_success')!r}. summary={summary!r}"
        )

    def test_state_distinguishes_threshold_from_no_cochange(self, threshold_not_met_project, cli_runner):
        """State must distinguish 'no cochange' from 'no pair above threshold'."""
        result = _invoke(cli_runner, threshold_not_met_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        state = summary.get("state")
        # The expected closed-enum vocabulary:
        # - no_cochange: zero git_cochange rows (already shipped)
        # - no_pairs_above_threshold: rows exist but filter excluded all (this pin)
        assert state == "no_pairs_above_threshold", (
            f"Pattern-1-V-D: expected state='no_pairs_above_threshold' "
            f"(threshold filter excluded every candidate), "
            f"got state={state!r}. summary={summary!r}"
        )

    def test_verdict_names_the_threshold(self, threshold_not_met_project, cli_runner):
        """LAW 6 + LAW 4: verdict must name the threshold token, not generic SAFE."""
        result = _invoke(cli_runner, threshold_not_met_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "").lower()
        # Should reference the threshold somehow: "min_cochanges", "below
        # threshold", "raw rows", or similar disclosure.
        markers = (
            "min_cochanges",
            "min-cochanges",
            "below threshold",
            "no_pairs_above_threshold",
            "raw cochange rows",
            "filtered out",
        )
        hits = [m for m in markers if m in verdict]
        assert hits, (
            f"LAW 4/6 violation: threshold-excluded verdict lacks named "
            f"disclosure of the threshold filter. verdict={verdict!r}. "
            f"Expected one of: {markers}"
        )


# ---------------------------------------------------------------------------
# Positive control — threshold_not_met_project still emits a real envelope
# (guards against an over-eager fix that breaks the structural shape)
# ---------------------------------------------------------------------------


def test_threshold_not_met_envelope_well_formed(threshold_not_met_project, cli_runner):
    """Even on the buggy path, the envelope shape is well-formed."""
    result = _invoke(cli_runner, threshold_not_met_project, json_mode=True)
    data = _parse_json(result)
    assert data.get("command") == "dark-matter"
    summary = data.get("summary", {})
    assert "verdict" in summary
    assert summary.get("total_dark_matter_edges") == 0
    # The canonical W631 risk-level field MUST exist (W641-followup-G mods,
    # uncommitted) even on the silent-SAFE path.
    assert summary.get("risk_level_canonical") in (
        "low",
        "medium",
        "high",
        "critical",
    ), f"missing canonical risk_level_canonical: {summary!r}"
    assert isinstance(summary.get("risk_rank"), int)
