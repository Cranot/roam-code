"""W805-JJJ — Pattern-2 silent-SAFE pin for `cmd_tour` on empty corpus.

Sixty-first-in-batch W805 sweep. ``cmd_tour.py`` (corpus-walk aggregator
per CLAUDE.md ``roam tour``: top symbols + reading order + entry points
+ language breakdown + project stats) was untested for the
verdict-band / degraded-corpus axis.

W978 first-hypothesis check: the agent's drive-by from W805-HHH was
"any aggregator calling ``metrics_history.collect_metrics`` inherits the
vacuous-max root cause." VERIFIED via grep: cmd_tour does NOT call
``metrics_history.collect_metrics`` — so the W805-HHH vacuous-max axis
does NOT propagate by that path. The first hypothesis was WRONG.

Reproducible probe (in-process CliRunner, README-only corpus → 0 symbols):
  summary.verdict          = "tour: 2 files, 0 symbols, 0 layers, start at ? (markdown)"
  summary.partial_success  = false
  summary["state"]         = (absent)
  summary["resolution"]    = (absent)
  statistics.avg_file_health = 10.0   (vacuous-max axis #2 — markdown files only)
  entry_points             = []
  reading_order            = []
  top_symbols              = []

Two distinct REAL bugs surface:

1) **Pattern-2 silent SAFE** — verdict reads as a successful tour
   ("tour: N files, K layers, start at X") even though zero symbols
   exist, no entry points were discovered, and the starting file is
   literally the sentinel "?". A LAW 6 violation: the verdict embeds
   "start at ?" as if "?" were a filename.

2) **Pattern-1 variant D + W805-HHH-style vacuous-max** — when ``order``
   is empty (line 460 ``start_file = "?"``), the fallback resolves
   silently with no ``partial_success: true``, no ``resolution`` field.
   Simultaneously ``avg_file_health: 10.0`` is reported as a 10/10
   quality score on a 0-symbol corpus where the average is computed
   over file_stats rows for non-source files (markdown).

This file pins both bugs via ``xfail(strict=True)`` so a future fix
will flip them to xpass → test failure → unwrap the xfail. Positive
companion tests assert the envelope shape remains parseable.

Run isolation:
  python -m pytest tests/test_w805_jjj_cmd_tour_empty_corpus.py -x -n 0
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

_CMD_TOUR_SPEC = importlib.util.find_spec("roam.commands.cmd_tour")


def test_command_exists_or_skip():
    """W978/W907 existence gate: cmd_tour module must be importable."""
    if _CMD_TOUR_SPEC is None:
        pytest.skip("roam.commands.cmd_tour not installed in this environment")
    assert _CMD_TOUR_SPEC is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus_project(tmp_path, monkeypatch):
    """Indexed project with no source-code symbols.

    A README.md + .gitignore satisfies ``git init`` and roam's index
    pipeline, but the corpus has 0 symbols (markdown is not a
    symbol-producing language). This is the canonical "degenerate
    corpus" axis the W805 sweep targets.
    """
    proj = tmp_path / "empty_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("# empty repo for W805-JJJ probe\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on empty corpus: {out}"
    return proj


@pytest.fixture
def clean_corpus_project(tmp_path, monkeypatch):
    """Indexed project with real Python source — positive control."""
    proj = tmp_path / "clean_repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def hello():\n    return 'world'\n\ndef greet(name):\n    return hello() + name\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed on clean corpus: {out}"
    return proj


# ---------------------------------------------------------------------------
# Invoke helper — invoke the click command directly so ctx.obj is set
# ---------------------------------------------------------------------------


def _invoke_tour(runner, args=None, cwd=None, json_mode=False):
    from roam.commands.cmd_tour import tour

    full_args = list(args or [])
    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(
            tour,
            full_args,
            obj={"json": json_mode},
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result):
    assert result.exit_code == 0, f"tour exit={result.exit_code}:\n{result.output}"
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON: {e}\nOutput head:\n{result.output[:500]}")


# ---------------------------------------------------------------------------
# Positive tests — empty-corpus envelope must remain parseable + crash-free
# (Pattern-1 variant C — never emit empty stdout)
# ---------------------------------------------------------------------------


class TestEmptyCorpusEnvelopeShape:
    def test_empty_corpus_no_crash(self, empty_corpus_project, cli_runner):
        """tour on empty corpus must not crash, regardless of degraded signal."""
        result = _invoke_tour(cli_runner, cwd=empty_corpus_project, json_mode=True)
        assert result.exit_code == 0, f"tour crashed on empty corpus (Pattern-1 variant C):\n{result.output}"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus_project, cli_runner):
        """JSON envelope must carry a summary.verdict string (LAW 6)."""
        result = _invoke_tour(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert "verdict" in summary, f"summary missing 'verdict': {summary}"
        assert isinstance(summary["verdict"], str) and summary["verdict"], "verdict must be a non-empty string"

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus_project, cli_runner):
        """LAW 6 — verdict must be self-contained (no 'see X' indirections)."""
        result = _invoke_tour(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        verdict = data.get("summary", {}).get("verdict", "")
        lowered = verdict.lower()
        assert "see " not in lowered and "details" not in lowered, f"LAW 6 violation — verdict indirects: {verdict!r}"


# ---------------------------------------------------------------------------
# REAL BUG #1 — Pattern-2 silent SAFE on empty corpus
# Pinned xfail(strict=True): a fix will flip these to xpass → test failure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-JJJ Pattern-2 bug: cmd_tour emits "
        "verdict='tour: N files, 0 symbols, 0 layers, start at ? (markdown)' + "
        "partial_success=false + no 'state' field on a degenerate (0-symbol) "
        "corpus. The verdict reads as a successful tour and embeds the "
        "sentinel '?' as if it were a filename. Fix: disclose "
        "state='empty_corpus' (or 'no_source_symbols'), set "
        "partial_success=true, and downgrade the verdict from a SAFE-shaped "
        "string. See CLAUDE.md 'Six systemic anti-patterns' section 2 + "
        "src/roam/commands/cmd_tour.py:460-465."
    ),
)
class TestEmptyCorpusPattern2Bug:
    def test_empty_corpus_state_explicit(self, empty_corpus_project, cli_runner):
        """Pattern-2: empty-corpus envelope must disclose state explicitly."""
        result = _invoke_tour(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        state = summary.get("state") or summary.get("resolution")
        assert state, (
            f"Pattern-2 silent SAFE: empty corpus produced summary without "
            f"'state'/'resolution' disclosure. summary={summary!r}"
        )

    def test_empty_corpus_partial_success_set(self, empty_corpus_project, cli_runner):
        """Pattern-2: empty corpus must flag partial_success=True."""
        result = _invoke_tour(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        assert summary.get("partial_success") is True, (
            f"Pattern-2 silent SAFE: 0 symbols but partial_success={summary.get('partial_success')!r}"
        )

    def test_no_silent_tour_complete_on_empty(self, empty_corpus_project, cli_runner):
        """The verdict must not read like a healthy tour on a 0-symbol corpus.

        Specifically guards two SAFE-shaped failure modes:
          - "tour: N files, K layers, start at <filename>" pattern when
            symbols=0
          - "start at ?" — the literal '?' sentinel leaking into the verdict
        """
        result = _invoke_tour(cli_runner, cwd=empty_corpus_project, json_mode=True)
        data = _parse_json(result)
        summary = data.get("summary", {})
        verdict = summary.get("verdict", "")
        # If 0 symbols, verdict must NOT begin with the SAFE-shaped "tour: N files,"
        # AND must NOT embed the '?' sentinel as a filename.
        if summary.get("symbols", 0) == 0:
            assert "start at ?" not in verdict, (
                f"Pattern-2 silent SAFE: empty-corpus verdict embeds '?' sentinel as a filename: {verdict!r}"
            )
            # The verdict reading as a successful tour on 0 symbols is the
            # core LAW 6 / Pattern-2 failure.
            assert not verdict.lower().startswith("tour:"), (
                f"Pattern-2 silent SAFE: empty-corpus verdict reads as a successful tour: {verdict!r}"
            )


# ---------------------------------------------------------------------------
# REAL BUG #2 — W805-HHH-style vacuous-max axis (statistics.avg_file_health)
# Pinned xfail(strict=True). The W978 drive-by hypothesis ("any aggregator
# inherits metrics_history.collect_metrics vacuous-max") was WRONG for the
# call-path, but the same SHAPE manifests via cmd_tour._patterns(), which
# computes AVG(health_score) FROM file_stats over the 0-symbol corpus.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-JJJ-2 vacuous-max axis: cmd_tour._patterns() emits "
        "statistics.avg_file_health=10.0 on a corpus with 0 source symbols "
        "(only markdown / non-source files). The 10.0 is a degenerate "
        "average — file_stats includes README.md but the user reads it as "
        "'codebase health is 10/10'. Same SHAPE as W805-HHH "
        "metrics_history.collect_metrics vacuous-max, different call path. "
        "Fix: suppress avg_file_health when total source symbols == 0 OR "
        "attach a state='empty_corpus' qualifier so consumers don't read "
        "10.0 as 'healthy'. See src/roam/commands/cmd_tour.py:291-302."
    ),
)
def test_no_vacuous_max_health_on_empty(empty_corpus_project, cli_runner):
    """avg_file_health=10.0 on 0 symbols is a degenerate-denominator artifact."""
    result = _invoke_tour(cli_runner, cwd=empty_corpus_project, json_mode=True)
    data = _parse_json(result)
    stats = data.get("statistics", {})
    if stats.get("symbols", 0) == 0:
        avg = stats.get("avg_file_health")
        # Either suppress (None) or downgrade below the vacuous-max plateau.
        assert avg is None or avg < 10.0, (
            f"Vacuous-max health: 0 symbols cannot produce a true 10.0 file "
            f"health average. Either suppress (set None) or attach "
            f"state='empty_corpus'. avg_file_health={avg!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-1 variant D — degraded resolution disclosure
# tour resolves a "starting file" via order[0] with a '?' sentinel fallback.
# That fallback is silent — exact Pattern-1-V-D shape.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-JJJ-3 Pattern-1 variant D: when reading_order is empty, "
        "cmd_tour falls back to start_file='?' at "
        "src/roam/commands/cmd_tour.py:460 with no resolution disclosure. "
        "The '?' leaks into the verdict but no 'resolution' or 'state' "
        "field on the envelope signals the degraded resolution. Fix: emit "
        "resolution='unresolved' (or 'no_starting_file') + partial_success=true."
    ),
)
def test_missing_target_resolution_disclosed(empty_corpus_project, cli_runner):
    """Pattern-1-V-D: degraded corpus must disclose resolution state."""
    result = _invoke_tour(cli_runner, cwd=empty_corpus_project, json_mode=True)
    data = _parse_json(result)
    summary = data.get("summary", {})
    # Either summary.resolution OR a top-level resolution field would close it.
    has_resolution = "resolution" in summary or "resolution" in data
    assert has_resolution, (
        f"Pattern-1-V-D: empty corpus produced no resolution disclosure. summary keys={list(summary.keys())}"
    )


# ---------------------------------------------------------------------------
# Positive control — clean corpus must still emit a real tour
# (guards against an over-eager fix that breaks the healthy path)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_tour(clean_corpus_project, cli_runner):
    """Clean corpus: real symbols + a verdict that reads as a successful tour."""
    result = _invoke_tour(cli_runner, cwd=clean_corpus_project, json_mode=True)
    data = _parse_json(result)
    summary = data.get("summary", {})
    assert summary.get("symbols", 0) >= 1, (
        f"clean corpus produced 0 symbols — fixture or indexer regression: {summary!r}"
    )
    verdict = summary.get("verdict", "")
    assert verdict.lower().startswith("tour:"), f"clean corpus lost the 'tour:' verdict shape: {verdict!r}"
    # On a clean corpus, the starting file must NOT be the '?' sentinel.
    assert "start at ?" not in verdict, f"clean corpus leaked the '?' sentinel into verdict: {verdict!r}"
