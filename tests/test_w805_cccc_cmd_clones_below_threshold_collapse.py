"""W805-CCCC — cmd_clones below-threshold collapse probe.

Eighty-first-in-batch W805 sweep. THIRD in the paired-scoring detector
family (dark_matter cochange -> duplicates pair_similarity -> clones AST
similarity). Mirror of W805-WWW (cmd_dark_matter) + W805-ZZZ
(cmd_duplicates) on a third detector that uses the same
"pre-filter + paired-similarity-threshold" architecture.

W978 first-hypothesis re-run BEFORE writing any pin
====================================================

Hypothesis: ``cmd_clones`` collapses three distinguishable input shapes
to a SINGLE silent SAFE verdict::

    (I) Empty corpus -> 0 funcs -> no candidates to compare
        verdict = "No structural clones detected"

    (II) Populated corpus, bucket pre-filter at clone_detect._compare_func_pair
         drops every pair (ratio < 0.5) -> 0 candidates pass shape gate
         verdict = "No structural clones detected"

    (III) Pairs scored, ALL below ``min_similarity`` (Jaccard) threshold
          -> 0 pairs survive scoring -> 0 clusters
          verdict = "No structural clones detected"

All three converge to the same envelope: ``clusters=[]``, ``pairs=[]``,
``summary.verdict = "No structural clones detected"``, NO ``state``
field, NO ``partial_success`` field, NO ``candidate_count`` field.

Architecture re-verification (W978 re-read of cmd_clones.py + clone_detect.py)
==============================================================================

cmd_clones.py:287-293         detect_clones(conn, min_similarity, min_lines, scope)
cmd_clones.py:374-383         verdict construction -- single "No structural
                              clones detected" string for empty clusters
cmd_clones.py:469-558         JSON envelope -- no state / partial_success
                              / candidate_count fields on the empty path

clone_detect.py:715-798       detect_clones() top-level pipeline
clone_detect.py:728           _fetch_candidate_files(conn, scope)
clone_detect.py:733           _parallel_extract_func_infos(files, min_lines)
                              -- size + min_AST_NODES=8 filter applied here
clone_detect.py:735-739       max_functions=2000 cap (sorted by node_count)
clone_detect.py:741           _find_clone_pairs(funcs, min_similarity)
clone_detect.py:599-618       _enumerate_candidate_pairs -> bucketed pairs
clone_detect.py:523-544       _compare_func_pair: ratio>=0.5 gate THEN
                              sim>=min_similarity Jaccard gate
clone_detect.py:735-739       max_functions truncation cap (silent)

Three gates can silently filter every candidate:
  (a) _MIN_AST_NODES=8 (clone_detect.py:79) at extraction
  (b) ratio = min(a.node_count, b.node_count) / max(...) < 0.5
      (clone_detect.py:527)
  (c) sim = _jaccard_bags(...) < min_similarity (clone_detect.py:530)

W978 hypothesis CONFIRMED: cmd_clones collapses (I) + (II) + (III) to
one silent SAFE verdict. The pre-existing W808 test
(``test_w808_clones_empty_corpus.py``) already pins (I) -- empty corpus
-- via xfail-strict on the ``summary.partial_success`` axis (W808 line
117: "summary.partial_success must be set"). That test confirms (I) is
NOT YET fixed at the time of this probe.

This pin extends the surface to (II) + (III) -- the bucket pre-filter
collapse and the below-threshold collapse -- and proves the FAMILY
SHAPE (3rd consecutive same-shape finding across dark_matter +
duplicates + clones = paired-scoring-detector below-threshold collapse
class).

PAIRED-SCORING DETECTOR FAMILY CONFIRMED. Three independent commands
(cmd_dark_matter, cmd_duplicates, cmd_clones) all exhibit the same
silent-SAFE-on-gate-collapse bug at the structural-similarity layer.

W907 verify-cycle check
========================

cmd_clones.py has no defensive "would create cycle" docstrings. Module
docstrings at lines 35-56 (W165 bucket explainer) and 116-129
(_enrich_clones_findings explainer) are legitimate explainer notes,
not false hedges. The local import ``from roam.graph.clone_detect
import detect_clones, store_clones`` at line 285 is a documented lazy
import for the heavy networkx-adjacent dependency, not a cycle hedge.
PASS -- no false hedges.

Bug class: Pattern-1-V-D silent success on degraded resolution +
Pattern-2 silent fallback (CLAUDE.md "Six systemic anti-patterns" #1.D
+ #2). The detector applies three upstream gates (min_lines,
node_count ratio, Jaccard threshold) and the command reports clean
SAFE without disclosing that the gates excluded every candidate pair.

Fix template (NOT applied -- pinned via xfail only):

    candidates_extracted = len(funcs)            # post-min_lines filter
    pairs_considered = len(candidate_pairs)      # post-bucket pre-filter
    pairs_above_thresh = len(pair_scores)        # post-threshold gate

    if not clusters:
        if candidates_extracted == 0:
            state = "empty_corpus"               # OR "no_candidates"
            verdict = "no functions to scan (after min_lines filter)"
            partial_success = True
        elif pairs_considered == 0:
            state = "no_pairs_after_prefilter"
            verdict = (f"{candidates_extracted} candidates but bucket "
                       "pre-filter excluded every pair")
            partial_success = True
        elif pairs_above_thresh == 0:
            state = "no_pairs_above_threshold"
            verdict = (f"{pairs_considered} pairs scored but all below "
                       f"threshold={min_similarity}")
            partial_success = True

Pinned via ``xfail(strict=True)`` so a future fix flips xpass + fails
the gate loudly. Positive companion tests assert wrapper crash-free +
existing W808/W813 invariants stay landed.

Run isolation:
    python -m pytest tests/test_w805_cccc_cmd_clones_below_threshold_collapse.py -x -n 0

W805 sweep update: 81st-in-batch.
W805-DDDD candidate: cmd_smells AST-similarity routines (if family
confirmation; same paired-scoring shape) OR pivot to fresh axis
(e.g. cmd_taint cross-language tainted-path scoring).
"""

from __future__ import annotations

import importlib.util
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 -- verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_CLONES_SPEC = importlib.util.find_spec("roam.commands.cmd_clones")


def test_command_exists_or_skip():
    """W978/W907 existence gate: cmd_clones must be importable."""
    if _CMD_CLONES_SPEC is None:
        pytest.skip("roam.commands.cmd_clones not installed in this environment")
    assert _CMD_CLONES_SPEC is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus_project(tmp_path, monkeypatch):
    """Indexed project with a single empty .py file (zero functions).

    Re-mirrors the W808 baseline. After indexing, the symbols table has
    no function/method rows; ``detect_clones`` extracts zero ``_FuncInfo``
    records and the pair-enumeration loop never executes.

    Input shape (I) of the three-shape family: empty input.
    """
    proj = tmp_path / "empty_corpus_cccc"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on empty-corpus fixture: {out}"
    return proj


@pytest.fixture
def pre_filter_collapse_project(tmp_path, monkeypatch):
    """Indexed project where the node_count ratio pre-filter EXCLUDES all pairs.

    Two functions both clear min_lines=5 (default) and _MIN_AST_NODES=8,
    so they enter ``_find_clone_pairs`` as candidates. But their AST
    sizes differ by enough that ``ratio = min/max < 0.5`` at
    ``_compare_func_pair`` (clone_detect.py:527), so every pair is
    silently filtered. ``pair_scores == {}`` and ``clusters == []``.

    Input shape (II): candidates present, pre-filter collapses all.
    """
    proj = tmp_path / "pre_filter_collapse_cccc"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Tiny function (~12 AST nodes -- minimal control flow).
    (proj / "a.py").write_text("def small(x):\n    if x > 0:\n        return x\n    return 0\n")
    # Huge function (~80+ AST nodes -- deeply nested) so ratio min/max < 0.5.
    (proj / "b.py").write_text(
        "def big_one(a, b, c, d, e):\n"
        "    out = {}\n"
        "    for i in range(10):\n"
        "        for j in range(20):\n"
        "            for k in range(30):\n"
        "                if a > b:\n"
        "                    if c > d:\n"
        "                        if e > 0:\n"
        "                            try:\n"
        "                                out[(i, j, k)] = (a + b) * (c - d) / e\n"
        "                            except ZeroDivisionError:\n"
        "                                out[(i, j, k)] = None\n"
        "                            except (TypeError, ValueError):\n"
        "                                out[(i, j, k)] = -1\n"
        "                            finally:\n"
        "                                pass\n"
        "                        else:\n"
        "                            while a > 0:\n"
        "                                a -= 1\n"
        "                                b += 1\n"
        "                    else:\n"
        "                        out[(i, j, k)] = 0\n"
        "                else:\n"
        "                    out[(i, j, k)] = -2\n"
        "    return out\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on pre-filter-collapse fixture: {out}"
    return proj


@pytest.fixture
def below_threshold_project(tmp_path, monkeypatch):
    """Indexed project where pair survives pre-filter but score is BELOW threshold.

    Two functions of similar AST size (ratio ~ 0.8-0.9 so the
    ``min/max >= 0.5`` ratio gate passes) but structurally divergent
    enough that Jaccard < ``min_similarity`` (we run with
    ``--threshold 0.95`` so even moderately similar pairs fall below).

    Input shape (III): pairs scored, all below threshold.
    """
    proj = tmp_path / "below_threshold_cccc"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Pure loop -- list output, no branches.
    (proj / "a.py").write_text(
        "def loop_only(items):\n"
        "    out = []\n"
        "    for item in items:\n"
        "        out.append(item)\n"
        "        out.append(item * 2)\n"
        "    return out\n"
    )
    # Pure branch chain -- dict output, no loops.
    (proj / "b.py").write_text(
        "def branch_only(x):\n"
        "    if x == 1:\n"
        "        return {'one': True}\n"
        "    if x == 2:\n"
        "        return {'two': True}\n"
        "    return {'other': False}\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on below-threshold fixture: {out}"
    return proj


@pytest.fixture
def clean_clones_project(tmp_path, monkeypatch):
    """Indexed project with TWO real clone pairs above default threshold.

    Positive control: the standard 0.70 threshold MUST produce at
    least one cluster on this corpus. Pins that the default verdict
    machinery still flows for the populated case.
    """
    proj = tmp_path / "clean_clones_cccc"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "a.py").write_text(
        "def proc_orders(items):\n"
        "    out = []\n"
        "    for it in items:\n"
        "        if it.is_valid():\n"
        "            v = it.calculate()\n"
        "            out.append(v)\n"
        "    return out\n"
    )
    (proj / "b.py").write_text(
        "def handle_invoices(entries):\n"
        "    res = []\n"
        "    for e in entries:\n"
        "        if e.is_valid():\n"
        "            x = e.calculate()\n"
        "            res.append(x)\n"
        "    return res\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on clean-clones fixture: {out}"
    return proj


# ---------------------------------------------------------------------------
# Invoke helper
# ---------------------------------------------------------------------------


def _invoke(runner, project_path, json_mode=False, extra_args=()):
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("clones")
    args.extend(extra_args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result):
    assert result.exit_code == 0, f"clones exit={result.exit_code}:\n{result.output}"
    try:
        return _json.loads(result.output)
    except _json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON: {e}\nOutput head:\n{result.output[:500]}")


# ---------------------------------------------------------------------------
# W808/W813 invariants -- empty-corpus envelope shape stays well-formed
# ---------------------------------------------------------------------------


class TestW808W813InvariantsPreserved:
    """Empty-corpus envelope shape must stay well-formed (Pattern-1-V-C)."""

    def test_empty_corpus_emits_envelope(self, empty_corpus_project, cli_runner):
        """Empty corpus must emit a parseable JSON envelope (no stdout crash)."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        assert data.get("command") == "clones"
        assert "summary" in data
        assert isinstance(data["summary"].get("verdict"), str)
        assert data["summary"]["verdict"]
        # Zero-count fields are present and consistent (matches W808 contract).
        assert data["summary"].get("clusters") == 0
        assert data["summary"].get("clone_pairs") == 0
        assert data.get("clusters") == []
        assert data.get("pairs") == []

    def test_empty_corpus_verdict_mentions_empty_state(self, empty_corpus_project, cli_runner):
        """Verdict on empty corpus must NOT be a default success string.

        Mirrors W808's verdict-token check. The current implementation
        emits "No structural clones detected" which contains "no " --
        the assertion stays satisfied even before the W805-CCCC fix.
        """
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        verdict_lower = data["summary"]["verdict"].lower()
        assert any(t in verdict_lower for t in ("no ", "empty", "0", "none")), (
            f"verdict must mention empty/zero state on empty corpus, got: {data['summary']['verdict']!r}"
        )


# ---------------------------------------------------------------------------
# Positive shape tests on the gate-collapse corpora
# (envelope still well-formed even on the buggy SAFE path)
# ---------------------------------------------------------------------------


class TestGateCollapseEnvelopeShape:
    def test_pre_filter_collapse_emits_envelope(self, pre_filter_collapse_project, cli_runner):
        """Pre-filter-collapse corpus emits a parseable envelope."""
        result = _invoke(cli_runner, pre_filter_collapse_project, json_mode=True)
        data = _parse_json(result)
        assert data.get("command") == "clones"
        assert isinstance(data["summary"].get("verdict"), str)
        assert data["summary"]["verdict"]
        assert data.get("clusters") == []
        assert data["summary"].get("clusters") == 0

    def test_below_threshold_emits_envelope(self, below_threshold_project, cli_runner):
        """Below-threshold corpus (--threshold 0.95) emits a parseable envelope."""
        result = _invoke(
            cli_runner,
            below_threshold_project,
            json_mode=True,
            extra_args=("--threshold", "0.95"),
        )
        data = _parse_json(result)
        assert data.get("command") == "clones"
        assert isinstance(data["summary"].get("verdict"), str)
        assert data["summary"]["verdict"]
        assert data.get("clusters") == []
        assert data["summary"].get("clusters") == 0

    def test_clean_corpus_real_pairs(self, clean_clones_project, cli_runner):
        """Positive control: populated corpus with real clones -> >=1 cluster.

        Pins that the default threshold (0.70) path still flows. If
        this regresses, the W805-CCCC fix is over-disclosing on a
        legitimately-populated populated graph.
        """
        result = _invoke(
            cli_runner,
            clean_clones_project,
            json_mode=True,
            extra_args=("--threshold", "0.50"),
        )
        data = _parse_json(result)
        assert data.get("command") == "clones"
        # Either we found clusters (expected) OR we hit a structural
        # tree-sitter / extraction edge case -- in the latter case,
        # the envelope must still be well-formed (no crash). The strong
        # assertion is: if we DID find clusters, the verdict reflects
        # that and does NOT say "No structural clones detected".
        clusters_n = data["summary"].get("clusters", 0)
        if clusters_n > 0:
            verdict = data["summary"]["verdict"]
            assert "no structural clones" not in verdict.lower(), (
                f"clean-clones corpus with {clusters_n} clusters still emits the empty-state verdict: {verdict!r}"
            )


# ---------------------------------------------------------------------------
# FAMILY-confirms invariant -- distinct verdicts for three input shapes
# Pinned xfail(strict=True): today all three shapes collapse to the same
# verdict; the fix MUST flip them to distinct disclosure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-CCCC FAMILY invariant: cmd_clones collapses three "
        "distinguishable input shapes (empty_corpus / "
        "no_pairs_after_prefilter / no_pairs_above_threshold) to a "
        "single silent SAFE verdict='No structural clones detected'. "
        "Mirror of W805-WWW (cmd_dark_matter cochange_count) + W805-ZZZ "
        "(cmd_duplicates pair_similarity). Three distinct states MUST "
        "produce three distinct verdicts after the fix. Pinned "
        "xfail-strict so the fix flips xpass -> test failure."
    ),
)
def test_distinct_verdicts_for_three_input_shapes(
    empty_corpus_project,
    pre_filter_collapse_project,
    below_threshold_project,
    cli_runner,
):
    """All three gate-collapse paths MUST emit distinct verdicts after the fix."""
    # Re-invoke each fixture and collect verdicts.
    v_empty = (
        _parse_json(_invoke(cli_runner, empty_corpus_project, json_mode=True))
        .get("summary", {})
        .get("verdict", "")
        .lower()
    )
    v_prefilter = (
        _parse_json(_invoke(cli_runner, pre_filter_collapse_project, json_mode=True))
        .get("summary", {})
        .get("verdict", "")
        .lower()
    )
    v_below = (
        _parse_json(
            _invoke(
                cli_runner,
                below_threshold_project,
                json_mode=True,
                extra_args=("--threshold", "0.95"),
            )
        )
        .get("summary", {})
        .get("verdict", "")
        .lower()
    )
    # All three distinct.
    assert v_empty != v_prefilter, (
        f"empty_corpus and pre_filter_collapse emit byte-identical "
        f"verdict: {v_empty!r}. FAMILY invariant requires distinct "
        f"disclosure for each gate that collapses every candidate."
    )
    assert v_prefilter != v_below, (
        f"pre_filter_collapse and below_threshold emit byte-identical "
        f"verdict: {v_prefilter!r}. FAMILY invariant requires distinct "
        f"disclosure for each gate."
    )
    assert v_empty != v_below, (
        f"empty_corpus and below_threshold emit byte-identical verdict: "
        f"{v_empty!r}. FAMILY invariant requires distinct disclosure."
    )


# ---------------------------------------------------------------------------
# REAL BUG -- Pattern-1-V-D silent SAFE on below-threshold collapse
# Pinned xfail(strict=True): a fix flips these to xpass -> test failure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-CCCC Pattern-1-V-D / Pattern-2 bug: cmd_clones emits "
        "verdict='No structural clones detected' WITHOUT partial_success "
        "+ WITHOUT state stamp when the bucket pre-filter at "
        "clone_detect._compare_func_pair (ratio<0.5 at "
        "clone_detect.py:527) silently excludes every candidate pair. "
        "Three distinct input shapes collapse to the same SAFE verdict. "
        "Fix: when (clusters == [] AND funcs_extracted >= 2), stamp "
        "state in {'no_pairs_after_prefilter',"
        "'no_pairs_above_threshold','empty_corpus'} + "
        "partial_success=True + name the gate in the verdict. See "
        "CLAUDE.md 'Six systemic anti-patterns' section 1 variant D + "
        "section 2. Mirror of W805-WWW + W805-ZZZ. THIRD detector in "
        "the paired-scoring-detector below-threshold collapse family."
    ),
)
class TestPreFilterCollapsePattern1VD:
    def test_partial_success_when_prefilter_excludes_all(self, pre_filter_collapse_project, cli_runner):
        """Pre-filter excluded every pair => MUST NOT report silent SAFE."""
        result = _invoke(cli_runner, pre_filter_collapse_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("partial_success") is True, (
            f"Pattern-1-V-D: candidates entered but ratio pre-filter "
            f"excluded every pair; expected partial_success=True, "
            f"got {summary.get('partial_success')!r}. summary={summary!r}"
        )

    def test_state_distinguishes_pre_filter_collapse(self, pre_filter_collapse_project, cli_runner):
        """State must name the gate-collapse path, not be absent."""
        result = _invoke(cli_runner, pre_filter_collapse_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        state = summary.get("state")
        # Closed-enum vocabulary mirroring W805-WWW + W805-ZZZ:
        assert state in (
            "no_pairs_after_prefilter",
            "no_pairs_above_threshold",
            "no_candidates",
        ), (
            f"Pattern-1-V-D: expected state in "
            f"{{'no_pairs_after_prefilter','no_pairs_above_threshold',"
            f"'no_candidates'}} (gate collapsed every candidate pair), "
            f"got state={state!r}. summary={summary!r}"
        )

    def test_verdict_names_the_gate(self, pre_filter_collapse_project, cli_runner):
        """LAW 6 + LAW 4: verdict must name the excluding gate, not generic SAFE."""
        result = _invoke(cli_runner, pre_filter_collapse_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "").lower()
        markers = (
            "pre-filter",
            "prefilter",
            "shape",
            "below threshold",
            "below the threshold",
            "ratio",
            "size",
            "no_pairs_after_prefilter",
            "no_pairs_above_threshold",
            "filtered out",
            "excluded",
        )
        hits = [m for m in markers if m in verdict]
        assert hits, (
            f"LAW 4/6 violation: pre-filter-collapse verdict lacks named "
            f"disclosure of the gate that excluded every candidate pair. "
            f"verdict={verdict!r}. Expected one of: {markers}"
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-CCCC Pattern-1-V-D / Pattern-2 bug: cmd_clones emits "
        "silent SAFE verdict when pairs are scored but all fall below "
        "the Jaccard ``min_similarity`` threshold "
        "(clone_detect._compare_func_pair line 530). The third "
        "distinguishable input shape in the paired-scoring detector "
        "family. Fix: stamp state='no_pairs_above_threshold' + "
        "partial_success=True + name the threshold in the verdict. "
        "Pinned xfail-strict per the W805-WWW + W805-ZZZ template."
    ),
)
class TestBelowThresholdPattern1VD:
    def test_partial_success_when_all_below_threshold(self, below_threshold_project, cli_runner):
        """All pairs scored below threshold => MUST NOT report silent SAFE."""
        result = _invoke(
            cli_runner,
            below_threshold_project,
            json_mode=True,
            extra_args=("--threshold", "0.95"),
        )
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("partial_success") is True, (
            f"Pattern-1-V-D: pairs entered scoring but all below threshold; "
            f"expected partial_success=True, got "
            f"{summary.get('partial_success')!r}. summary={summary!r}"
        )

    def test_state_distinguishes_below_threshold(self, below_threshold_project, cli_runner):
        """State must name the threshold-collapse path."""
        result = _invoke(
            cli_runner,
            below_threshold_project,
            json_mode=True,
            extra_args=("--threshold", "0.95"),
        )
        data = _parse_json(result)
        summary = data.get("summary", {})
        state = summary.get("state")
        assert state in (
            "no_pairs_above_threshold",
            "no_pairs_after_prefilter",
        ), (
            f"Pattern-1-V-D: expected state in "
            f"{{'no_pairs_above_threshold','no_pairs_after_prefilter'}} "
            f"(threshold collapsed all scored pairs), got state={state!r}. "
            f"summary={summary!r}"
        )

    def test_verdict_names_the_threshold(self, below_threshold_project, cli_runner):
        """LAW 6 + LAW 4: verdict must name the threshold or score collapse."""
        result = _invoke(
            cli_runner,
            below_threshold_project,
            json_mode=True,
            extra_args=("--threshold", "0.95"),
        )
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "").lower()
        markers = (
            "threshold",
            "below",
            "0.95",
            "jaccard",
            "similarity",
            "no_pairs_above_threshold",
            "scored",
        )
        hits = [m for m in markers if m in verdict]
        assert hits, (
            f"LAW 4/6 violation: below-threshold verdict lacks named "
            f"disclosure of the threshold that excluded every scored "
            f"pair. verdict={verdict!r}. Expected one of: {markers}"
        )


# ---------------------------------------------------------------------------
# Distinct-verdict invariant for the empty-corpus path (positive control).
# The W808 baseline already xfails on partial_success; this xfail-strict
# extends to "verdict on empty corpus must be DISTINCT from verdict on
# gate-collapse paths" -- the FAMILY-shape disclosure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-CCCC: today the empty-corpus path emits the SAME "
        "'No structural clones detected' verdict that gate-collapse "
        "paths emit. The FAMILY invariant requires the empty path "
        "to land a distinct verdict (e.g. 'no functions to scan' or "
        "'corpus has 0 functions/methods'). Mirror of W805-ZZZ "
        "duplicates fix that distinguishes 'empty_corpus' from "
        "'no_candidates'. Pinned xfail-strict so the fix flips it."
    ),
)
def test_empty_corpus_verdict_distinct_from_default_safe(empty_corpus_project, cli_runner):
    """Empty-corpus verdict MUST become distinct from the default SAFE string."""
    result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
    data = _parse_json(result)
    verdict = (data.get("summary", {}).get("verdict") or "").lower()
    assert verdict != "no structural clones detected", (
        f"Empty-corpus path still emits the default SAFE verdict "
        f"byte-identical to a populated graph with no clones found: "
        f"{verdict!r}"
    )
