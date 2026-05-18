"""W805-ZZZ — cmd_duplicates re-probe + Pattern-1-V-D below-threshold pin.

Seventy-ninth-in-batch W805 sweep. Mirror of W805-WWW (cmd_dark_matter
below-threshold collapse). cmd_duplicates was originally probed at W821
(empty-corpus envelope). This re-probe validates the W821 invariants
under the current working tree AND extends the surface to catch the
W805-WWW bug class on a different detector.

W978 first-hypothesis re-run BEFORE writing any pin
====================================================

Hypothesis: "0 clusters above the (threshold × pre-filter) gates emits a
silent SAFE verdict that doesn't distinguish 'analyzed cleanly with no
duplicates' from 'candidates EXISTED but the bucket pre-filter excluded
every pair' from 'pairs survived bucket filter but no similarity score
cleared threshold'".

The early-return guard at ``cmd_duplicates.py:612-664`` carries strong
Pattern-2 disclosure for the ``len(candidates) < 2`` axis — three closed-
enum states (``empty_corpus`` / ``no_candidates`` / ``insufficient_
candidates``) with ``partial_success=True`` and named verdicts. THAT axis
is sealed.

But the much more common downstream collapse is the bucket pre-filter at
``cmd_duplicates.py:734`` (``abs(pa - pb) <= 1``) and the threshold gate
at ``cmd_duplicates.py:782`` (``if sim >= threshold``). When either gate
silently drops every candidate pair, ``cluster_list == []`` AND
``partial_success`` stays False AND no ``state`` key is stamped. The
verdict collapses to ``"No semantic duplicates detected"``, which is
byte-identical to a populated graph where the detector legitimately
found no duplicates.

Empirical probe (2-function corpus, ``param_count`` mismatch):

    candidates (>= min_lines=5)  = 2
    pre-filter (|param diff|<=1) = 0 pairs survive
    pair_scores                  = {}
    cluster_list                 = []
    output:
      summary.verdict            = "No semantic duplicates detected"
      summary.partial_success    = False                    <-- BUG: silent SAFE
      summary.state              = (absent)                 <-- BUG: no disclosure
      summary.candidate_count    = 2                        <-- only signal!

The ``candidate_count`` field is the only hint that anything entered the
pipeline; the verdict and ``state`` fields are byte-identical to a clean
populated graph.

W978 hypothesis CONFIRMED on the post-candidate-collapse axis. The
``len(candidates) < 2`` axis is already W805-sealed; this pin targets
the orthogonal ``candidates >= 2 but pre-filter / threshold excluded all
pairs`` axis.

W907 verify-cycle check
========================

cmd_duplicates has no defensive "would create cycle" docstrings. Module
docstrings at lines 19-30 (W165 buckets), 66-79 (W136 findings registry),
and 367-368 (W89 schema-tolerance comment) are all legitimate explainer
notes, not false hedges. The local import ``from roam.db.findings
import ...`` at line 371 is the documented "keeps cost out of the
read-only path" pattern — legitimate lazy import, not a cycle hedge.
PASS — no false hedges.

Pattern recognition (mission-task 2)
=====================================

cmd_duplicates.py and tests/test_duplicates.py are NOT modified in the
current working tree (the mission-prompt git_status snapshot was stale).
The seventy-eighth-in-batch sweep (W805-YYY cmd_n1) intervening commit
likely cleaned them. Nothing to bisect against an uncommitted diff.

The pre-existing W821 baseline (4 tests) passes. The pre-existing
``test_duplicates.py`` regression suite (27 tests) passes. The bug
pinned below is PRE-EXISTING, not regression-introduced.

Bug class: Pattern-1-V-D silent success on degraded resolution +
Pattern-2 silent fallback. The detector applies two upstream gates
(bucket pre-filter + similarity threshold) and the command reports
clean SAFE without disclosing that the gates excluded every candidate
pair.

Source-of-truth lines:
  src/roam/commands/cmd_duplicates.py:612-664   (early-return state guard
                                                 — already W805-sealed for
                                                 the < 2 candidates axis)
  src/roam/commands/cmd_duplicates.py:734       (bucket pre-filter — the
                                                 silent gate)
  src/roam/commands/cmd_duplicates.py:782       (similarity threshold —
                                                 the second silent gate)
  src/roam/commands/cmd_duplicates.py:909-910   ("No semantic duplicates
                                                 detected" — same string
                                                 in two distinct conditions)

Fix template (NOT applied — pinned via xfail only):

  pairs_considered = len(pairs_to_check)
  pairs_above_thresh = len(pair_scores)
  if not cluster_list and original_candidate_count >= 2:
      if pairs_considered == 0:
          summary.state = "no_pairs_after_prefilter"
          summary.partial_success = True
          verdict = f"0 pairs survived shape pre-filter (had {original_candidate_count} candidates)"
      elif pairs_above_thresh == 0:
          summary.state = "no_pairs_above_threshold"
          summary.partial_success = True
          verdict = f"0 pairs above threshold={threshold} (considered {pairs_considered})"

Pinned via ``xfail(strict=True)`` so the future fix flips xpass and the
gate fails loudly. Positive companion tests assert wrapper crash-free +
already-fixed empty-corpus state axis stays fixed.

Run isolation:
  python -m pytest tests/test_w805_zzz_cmd_duplicates_post_mod_empty_corpus.py -x -n 0
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
# Module existence gate (W978 + W907 — verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_DUPLICATES_SPEC = importlib.util.find_spec("roam.commands.cmd_duplicates")


def test_command_exists_or_skip():
    """W978/W907 existence gate: cmd_duplicates must be importable."""
    if _CMD_DUPLICATES_SPEC is None:
        pytest.skip("roam.commands.cmd_duplicates not installed in this environment")
    assert _CMD_DUPLICATES_SPEC is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus_project(tmp_path, monkeypatch):
    """Indexed project containing a single empty .py file.

    Re-mirrors the W821 baseline so we can re-prove the early-return
    state guard at cmd_duplicates.py:612-664 (already W805-sealed) is
    still landed. No functions/methods => ``state="empty_corpus"``.
    """
    proj = tmp_path / "empty_corpus_zzz"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on empty-corpus fixture: {out}"
    return proj


@pytest.fixture
def below_threshold_project(tmp_path, monkeypatch):
    """Indexed project with 2 candidates that the bucket pre-filter EXCLUDES.

    Two functions both clear ``min_lines=5`` (default), so they survive
    the early-return guard at line 612 (``len(candidates) < 2`` is
    False). But their ``param_count`` differs by 4 (2 vs 6), so the
    bucket pre-filter at line 734 (``abs(pa - pb) <= 1``) drops the
    pair, and ``cluster_list`` ends up empty.

    This is the W805-WWW echo axis: candidates entered, pipeline ran,
    no clusters formed — same SAFE verdict as a populated graph with
    legitimately no duplicates.
    """
    proj = tmp_path / "below_threshold_zzz"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # 2 params, 9 lines.
    (proj / "a.py").write_text(
        "def alpha_processor(data, config):\n"
        '    """Process alpha data with config."""\n'
        "    if not data:\n"
        "        return None\n"
        "    result = []\n"
        "    for item in data:\n"
        "        if item.kind == 'alpha':\n"
        "            result.append(item.value * 2)\n"
        "    return result\n"
    )
    # 6 params, 13 lines — wildly different shape, bucket pre-filter drops it.
    (proj / "b.py").write_text(
        "def render_html_template(template_str, ctx_a, ctx_b, ctx_c, ctx_d, ctx_e):\n"
        '    """Render HTML by deeply nested loops and many params."""\n'
        "    out = []\n"
        "    for line in template_str.split('\\n'):\n"
        "        for key in ctx_a:\n"
        "            for sub in ctx_b:\n"
        "                while sub:\n"
        "                    try:\n"
        "                        out.append(line.format(k=key, s=sub, c=ctx_c, d=ctx_d, e=ctx_e))\n"
        "                    except KeyError:\n"
        "                        out.append('')\n"
        "                    sub = None\n"
        "    return '\\n'.join(out)\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on below-threshold fixture: {out}"
    return proj


# ---------------------------------------------------------------------------
# Invoke helper
# ---------------------------------------------------------------------------


def _invoke(runner, project_path, json_mode=False, extra_args=()):
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("duplicates")
    args.extend(extra_args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result):
    assert result.exit_code == 0, f"duplicates exit={result.exit_code}:\n{result.output}"
    try:
        return _json.loads(result.output)
    except _json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON: {e}\nOutput head:\n{result.output[:500]}")


# ---------------------------------------------------------------------------
# W821 invariants — already-sealed empty-corpus state guard stays landed
# ---------------------------------------------------------------------------


class TestW821EmptyCorpusInvariantsPreserved:
    """Re-prove that the W805-sealed early-return state guard still holds."""

    def test_empty_corpus_emits_envelope(self, empty_corpus_project, cli_runner):
        """Empty corpus must emit a parseable JSON envelope (Pattern-1-V-C)."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        assert data.get("command") == "duplicates"
        assert "summary" in data
        assert isinstance(data["summary"].get("verdict"), str)
        assert data["summary"]["verdict"]

    def test_empty_corpus_state_disclosed(self, empty_corpus_project, cli_runner):
        """Empty-corpus path stamps state='empty_corpus' (W805-sealed)."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("state") == "empty_corpus", (
            f"W805 early-return guard regression: expected state='empty_corpus', "
            f"got {summary.get('state')!r}; summary keys: {sorted(summary.keys())}"
        )
        assert summary.get("partial_success") is True, (
            f"Empty corpus must disclose partial_success=True; got {summary.get('partial_success')!r}"
        )

    def test_empty_corpus_verdict_law6(self, empty_corpus_project, cli_runner):
        """LAW 6: verdict must be self-contained — names the empty state."""
        result = _invoke(cli_runner, empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "").lower()
        markers = ("no symbols", "0 functions", "empty", "corpus has 0")
        hits = [m for m in markers if m in verdict]
        assert hits, f"empty-corpus verdict lacks disclosure: {verdict!r}"


# ---------------------------------------------------------------------------
# Positive shape tests on the below-threshold corpus
# (envelope still well-formed even on the buggy SAFE path)
# ---------------------------------------------------------------------------


class TestBelowThresholdEnvelopeShape:
    def test_below_threshold_emits_envelope(self, below_threshold_project, cli_runner):
        """Below-threshold corpus must still emit a parseable envelope."""
        result = _invoke(cli_runner, below_threshold_project, json_mode=True)
        data = _parse_json(result)
        assert data.get("command") == "duplicates"
        assert "summary" in data
        assert isinstance(data["summary"].get("verdict"), str)
        assert data["summary"]["verdict"]
        assert data.get("clusters") == []
        assert data["summary"].get("total_clusters") == 0

    def test_below_threshold_candidate_count_nonzero(self, below_threshold_project, cli_runner):
        """``candidate_count`` reveals candidates DID enter the pipeline.

        On the below-threshold path, the only signal that anything entered
        the algorithm is ``summary.candidate_count >= 2``. The verdict and
        ``state`` fields are byte-identical to a clean populated graph.
        Pin the candidate_count signal positive so a future shape change
        that drops it regresses loudly.
        """
        result = _invoke(cli_runner, below_threshold_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("candidate_count", 0) >= 2, (
            f"below-threshold corpus must surface candidate_count >= 2 "
            f"(the only hint that anything entered the pipeline); "
            f"got {summary.get('candidate_count')!r}. summary={summary!r}"
        )


# ---------------------------------------------------------------------------
# Findings-registry no-phantom emissions (--persist OFF)
# ---------------------------------------------------------------------------


def test_below_threshold_findings_registry_no_phantom_emissions(below_threshold_project, cli_runner):
    """Without --persist, the registry must not gain rows on a no-clusters run.

    The structural guard is the ``if persist:`` gate at
    cmd_duplicates.py:875. Probe behaviorally: invoke duplicates without
    --persist, then count findings rows for ``source_detector='duplicates'``.
    """
    import sqlite3 as _sqlite3

    result = _invoke(cli_runner, below_threshold_project, json_mode=True)
    assert result.exit_code == 0

    db_path = below_threshold_project / ".roam" / "index.db"
    if not db_path.exists():
        pytest.skip(".roam/index.db missing — index pipeline did not write expected path")

    conn = _sqlite3.connect(db_path)
    try:
        # Tolerate pre-W89 schema (no findings table) — the test is about
        # phantom emissions, not registry-presence assertions.
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "findings" not in tables:
            pytest.skip("findings table absent — pre-W89 schema, no registry to probe")
        rows = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'duplicates'").fetchone()[0]
    finally:
        conn.close()
    assert rows == 0, (
        f"Phantom registry emission on no-clusters/--persist=off path: "
        f"got {rows} findings rows for source_detector='duplicates'"
    )


# ---------------------------------------------------------------------------
# REAL BUG — Pattern-1-V-D silent SAFE on below-threshold collapse
# Pinned xfail(strict=True): a fix flips these to xpass -> test failure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-ZZZ Pattern-1-V-D / Pattern-2 bug: cmd_duplicates emits "
        "verdict='No semantic duplicates detected' + partial_success=False "
        "+ NO state stamp when the corpus HAS >=2 function candidates but "
        "the bucket pre-filter at cmd_duplicates.py:734 (or the threshold "
        "gate at line 782) silently excludes every candidate pair. The "
        "W805-sealed early-return at cmd_duplicates.py:612-664 only "
        "discloses the ``len(candidates) < 2`` axis; it misses the "
        "downstream gate-collapse path where candidates entered but no "
        "pair survived to scoring. Three distinct input shapes (clean "
        "populated graph with no duplicates / candidates dropped by "
        "shape pre-filter / candidates scored but all below threshold) "
        "collapse to the same SAFE verdict. Fix: when "
        "(original_candidate_count >= 2 AND cluster_list == []), stamp "
        "state in {'no_pairs_after_prefilter','no_pairs_above_threshold'} "
        "+ partial_success=True + name the gate in the verdict. See "
        "CLAUDE.md 'Six systemic anti-patterns' section 1 variant D + "
        "section 2. Mirror of W805-WWW on cmd_dark_matter."
    ),
)
class TestBelowThresholdPattern1VD:
    def test_partial_success_when_prefilter_excludes_all(self, below_threshold_project, cli_runner):
        """Pre-filter excluded every pair => MUST NOT report silent SAFE."""
        result = _invoke(cli_runner, below_threshold_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("partial_success") is True, (
            f"Pattern-1-V-D: candidates entered but pre-filter excluded "
            f"every pair; expected partial_success=True, "
            f"got {summary.get('partial_success')!r}. summary={summary!r}"
        )

    def test_state_distinguishes_below_threshold_from_no_duplicates(self, below_threshold_project, cli_runner):
        """State must name the gate-collapse path, not absent."""
        result = _invoke(cli_runner, below_threshold_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        state = summary.get("state")
        # The expected closed-enum vocabulary mirrors the W805-WWW pin:
        #   - empty_corpus / no_candidates / insufficient_candidates  (already sealed)
        #   - no_pairs_after_prefilter / no_pairs_above_threshold     (THIS pin)
        #   - (absent or "no_duplicates") on truly clean populated graphs
        assert state in ("no_pairs_after_prefilter", "no_pairs_above_threshold"), (
            f"Pattern-1-V-D: expected state in {{'no_pairs_after_prefilter',"
            f"'no_pairs_above_threshold'}} (gate collapsed every candidate pair), "
            f"got state={state!r}. summary={summary!r}"
        )

    def test_verdict_names_the_gate(self, below_threshold_project, cli_runner):
        """LAW 6 + LAW 4: verdict must name the excluding gate, not generic SAFE."""
        result = _invoke(cli_runner, below_threshold_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "").lower()
        # Should reference the gate somehow: "pre-filter", "threshold",
        # "shape", "below", "param", or similar disclosure.
        markers = (
            "pre-filter",
            "prefilter",
            "shape",
            "below threshold",
            "below the threshold",
            "param mismatch",
            "no_pairs_after_prefilter",
            "no_pairs_above_threshold",
            "filtered out",
            "excluded",
        )
        hits = [m for m in markers if m in verdict]
        assert hits, (
            f"LAW 4/6 violation: below-threshold verdict lacks named "
            f"disclosure of the gate that excluded every candidate pair. "
            f"verdict={verdict!r}. Expected one of: {markers}"
        )


# ---------------------------------------------------------------------------
# Distinct-verdict invariant (positive control — pins the bug for now)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-ZZZ: today the below-threshold path emits the same SAFE "
        "verdict as a truly-empty no-functions corpus would IF the early-"
        "return guard didn't fire. After the fix, the two paths MUST "
        "emit distinct verdicts. Pinned xfail-strict so the fix flips it."
    ),
)
def test_below_threshold_verdict_distinct_from_no_duplicates_legitimate(below_threshold_project, cli_runner):
    """The verdict on the below-threshold (buggy) path MUST become distinct
    from the legitimate 'no semantic duplicates' string after the fix."""
    result = _invoke(cli_runner, below_threshold_project, json_mode=True)
    data = _parse_json(result)
    verdict = (data.get("summary", {}).get("verdict") or "").lower()
    assert verdict != "no semantic duplicates detected", (
        f"Below-threshold path still emits the SAFE verdict byte-identical "
        f"to a populated graph with truly no duplicates: {verdict!r}"
    )
