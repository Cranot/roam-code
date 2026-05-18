"""W805-EE - empty-corpus Pattern-2 smoke for ``roam simulate`` (W805 sweep).

Thirty-first-in-batch of the W805 sweep. cmd_simulate is the named sibling of
cmd_orchestrate (W805-U, 4 REAL BUGS), cmd_partition (W805-Y, 3 REAL BUGS),
cmd_spectral (W805-CC, 4 REAL BUGS) - graph-mutation family: build a graph,
clone it, apply a transform, recompute graph-derived metrics, emit before/after
deltas. cmd_simulate per CLAUDE.md: "counterfactual architecture simulation
(graph cloning + transforms)".

W978 first-hypothesis re-run BEFORE writing any test
============================================================
Direct probe of ``roam --json simulate <subcommand> ...`` on:

1. An empty-file corpus (``empty.py`` with no symbols) + ``simulate move
   nonexistent foo.py``.
2. An empty-file corpus + ``simulate merge a.py b.py`` (no resolve, but
   merge has its own "no symbols in <file>" early-return).
3. A clean 2-symbol corpus (``helper`` + ``caller`` with 1 edge) + ``simulate
   move helper a.py`` (no-op self-move).
4. Clean corpus regression baseline (move helper to newfile.py).

W978 re-run: probed twice — same envelope shape both times. Hypothesis holds.

Empty-corpus probe (move nonexistent on 0-symbol graph)
-------------------------------------------------------
- ``_run_simulation`` (cmd_simulate.py L25-222) computes ``before =
  compute_graph_metrics(G)`` on the EMPTY graph BEFORE the resolve check.
- ``_approx_health`` (graph/simulate.py L38-54) on (tangle=0.0, god=0, bn=0,
  lv=0) returns ``100`` (exp(-0/scale) = 1 for every signal -> raw=100).
- ``resolve_target`` returns ``[]`` -> ``do_op`` returns ``("", "symbol
  not found: ...")`` -> error path at L60-83 emits envelope with
  ``health_before=100``, ``health_after=100``, ``verdict="symbol not found:
  ..."``, ``partial_success=False``, NO ``state`` field.

Empty-corpus probe (merge a.py b.py on 0-symbol graph)
-------------------------------------------------------
- ``simulate_merge`` does an own "no symbols found in: b.py" check (L319-322).
- Hits the same L60-83 error envelope: ``health_before=100``,
  ``health_after=100``, ``partial_success=False``, NO ``state``.

No-op transform (move helper -> a.py where helper already lives)
----------------------------------------------------------------
- ``resolve_target`` succeeds, ``apply_move`` runs but sets
  ``data["file_path"] = "a.py"`` (it was already ``a.py``). before == after.
- Verdict: ``"health unchanged at 100, modularity unchanged, 0 new cycles"`` -
  Pattern-2 silent SAFE: indistinguishable from a real "this transform is
  safe to apply" envelope, despite being a no-op tautology.

W978 findings: Pattern-2 disclosure gaps, ranked by agent-impact
============================================================

1. **CRITICAL: health_score=100 on empty graph** (graph/simulate.py L38-54
   ``_approx_health``). On a 0-node graph, every input signal is 0, every
   ``_hf()`` decay returns 1, log-sum = 0, raw = 100. The envelope reports
   ``health_before=100``, ``health_after=100`` -> fabricated metric
   indistinguishable from a real healthy codebase. Same family as W805-CC
   fiedler-on-empty fabrication. Fix template: when ``len(G) == 0`` in
   ``compute_graph_metrics``, return ``health_score=None`` or ``health_score
   = "no_signal_data"`` rather than 100.

2. **HIGH: partial_success=False on error envelope** (cmd_simulate.py
   L60-83). When ``resolve_target`` returned ``[]`` or merge found no
   symbols, the simulation aborted - yet the envelope ships
   ``partial_success=False``. Same shape as W805-U #2 / W805-Y #1 / W805-L
   #4. Fix template: error path should explicitly set
   ``partial_success=True`` since the transform did not actually run.

3. **HIGH: summary.state MISSING entirely** (cmd_simulate.py L60-83 + L122-141).
   Both error envelope AND success envelope build summary with
   ``{verdict, operation, health_delta, health_before, health_after,
   improved_metrics, degraded_metrics}`` - NO ``state`` field. Same gap as
   W805-Y #2 / W805-U #3. Agents have no machine-readable way to
   distinguish ``"no_signal_data"`` / ``"symbol_not_found"`` / ``"no_op"`` /
   ``"transform_applied"``. Fix template: add closed-enum
   ``summary.state`` ∈ {``transform_applied``, ``no_op_transform``,
   ``symbol_not_found``, ``no_data_in_corpus``}.

4. **HIGH: no-op transform silently emits SAFE verdict** (cmd_simulate.py
   L115-120). Moving ``helper`` to its own existing file mutates a node
   attribute then sets it back to the same value (``data["file_path"] =
   "a.py"`` where it already was ``a.py``). before == after, verdict reads
   ``"health unchanged at 100, modularity unchanged, 0 new cycles"`` -
   indistinguishable from a real "no risk" delta. Same Pattern-2 axis as
   W805-Y #3 stub-padding. Fix template: detect no-op transforms by
   comparing source file == target file in ``simulate_move``; surface
   ``state="no_op_transform"`` + ``partial_success=True`` + verdict
   ``"no-op: symbol already in target file"``.

W978 status: CONFIRMED (3rd-run probe identical to 1st-run probe).

DO NOT FIX this wave - accumulate xfail-strict pins only.

Run isolation:
    python -m pytest tests/test_w805_ee_cmd_simulate_empty_corpus.py -x -n 0

Regression baseline:
    python -m pytest tests/test_simulate.py -x -n 0
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_init_with_baseline(proj: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=proj,
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=proj,
        check=True,
    )


def _invoke_simulate(runner: CliRunner, cwd: Path, *extra, json_mode: bool = True):
    """Invoke ``roam simulate <subcommand> ...`` via the Click group."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("simulate")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_envelope(result) -> dict:
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Indexed project with a single empty .py file - 0 symbols, 0 edges."""
    proj = tmp_path / "empty_sim_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    _git_init_with_baseline(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed project with 2 symbols + 1 edge - regression baseline."""
    proj = tmp_path / "clean_sim_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "a.py").write_text(
        "def helper():\n    return 1\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )
    _git_init_with_baseline(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Sealed-today contracts (always-on smoke)
# ---------------------------------------------------------------------------


class TestSimulateEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_simulate envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``simulate move`` on empty corpus exits 0, no crash."""
        result = _invoke_simulate(cli_runner, empty_corpus, "move", "nonexistent", "foo.py", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Envelope carries ``command=simulate`` + non-empty verdict."""
        result = _invoke_simulate(cli_runner, empty_corpus, "move", "nonexistent", "foo.py", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "simulate"
        verdict = envelope.get("summary", {}).get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: verdict line stands alone (single line, non-placeholder)."""
        result = _invoke_simulate(cli_runner, empty_corpus, "move", "nonexistent", "foo.py", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok"), f"verdict is a placeholder: {verdict!r}"

    def test_empty_corpus_missing_target_disclosure(self, cli_runner, empty_corpus):
        """Pattern-1-V-D good citizen: error verdict explicitly names the missing target.

        cmd_simulate at least gets THIS right: error path emits ``verdict:
        "symbol not found: <name>"``. Lock in as sealed-today good behaviour.
        """
        result = _invoke_simulate(cli_runner, empty_corpus, "move", "ghost_sym", "foo.py", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"].lower()
        assert "not found" in verdict or "ghost_sym" in verdict, (
            f"empty-corpus + missing-symbol verdict must name the failure; got {envelope['summary']['verdict']!r}"
        )

    def test_clean_corpus_emits_real_diff(self, cli_runner, clean_corpus):
        """Happy-path positive coverage: real move on populated corpus produces real envelope."""
        result = _invoke_simulate(cli_runner, clean_corpus, "move", "helper", "newfile.py", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        # Canonical summary fields all present.
        for field in (
            "verdict",
            "operation",
            "health_delta",
            "health_before",
            "health_after",
            "improved_metrics",
            "degraded_metrics",
        ):
            assert field in summary, f"clean envelope missing summary field {field!r}; got {sorted(summary.keys())}"
        # The operation block must be populated with the move details.
        op = envelope["operation"]
        assert op.get("operation") == "move"
        assert op.get("symbol") == "helper"
        assert op.get("to_file") == "newfile.py"


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #1 - CRITICAL: fabricated health=100 on empty graph
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-EE REAL BUG (CRITICAL): graph/simulate.py L38-54 _approx_health "
        "on a 0-node graph returns 100. The cascade: compute_graph_metrics "
        "on len(G)==0 -> tangle=0.0, god_count=0, bn_count=0, lv_count=0 -> "
        "every _hf() decay = exp(-0/scale) = 1 -> weighted log-sum = 0 -> "
        "raw = 100 * exp(0) = 100. Envelope reports health_before=100 + "
        "health_after=100 on the EMPTY corpus, indistinguishable from a "
        "real perfectly-healthy codebase. Same fabricated-metric family as "
        "W805-CC fiedler-on-empty. Agents reading 'health_before=100' on "
        "their actual project will assume the index is healthy when in "
        "fact 0 symbols indexed. Fix template: when len(G)==0 in "
        "compute_graph_metrics, return health_score=None (or omit the field) "
        "so the empty-graph case is distinguishable from a real 100. "
        "Separate fix wave."
    ),
)
def test_no_fabricated_health_on_empty_graph(cli_runner, empty_corpus):
    """Pin: health_score on empty graph MUST be None / N/A, not 100.

    The empty corpus has 0 symbols and 0 edges. health_score=100 here is
    a fabrication - no signal exists from which to compute it.
    """
    result = _invoke_simulate(cli_runner, empty_corpus, "move", "ghost", "foo.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    health_before = summary["health_before"]
    # The fix should disclose empty-graph health as None / null / 0 / a string
    # sentinel — NOT 100 (which means "perfectly healthy" on a real graph).
    assert health_before in (None, 0, "no_signal_data", "n/a"), (
        f"empty corpus has 0 symbols + 0 edges; health_before MUST NOT "
        f"be 100 (which means 'perfectly healthy' on a real graph). "
        f"Got summary.health_before={health_before!r}. This is a "
        f"fabricated metric — _approx_health(0,0,0,0)=100 by default."
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #2 - HIGH: partial_success=False on error envelope
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-EE REAL BUG (HIGH): cmd_simulate.py L60-83 builds the error "
        "envelope summary without partial_success - json_envelope auto-derive "
        "defaults it to False. When resolve_target returned [] (symbol not "
        "found) or simulate_merge found no symbols in file_b, the simulation "
        "DID NOT RUN - yet partial_success=False signals 'success'. Same "
        "Pattern-2 axis as W805-U #2 (orchestrate), W805-Y #1 (partition), "
        "W805-L #4 (preflight), W805-T #2 (uses). Fix template: error path "
        "MUST set partial_success=True since the transform aborted. "
        "Separate fix wave."
    ),
)
def test_empty_corpus_partial_success_set(cli_runner, empty_corpus):
    """Pin: error envelope on missing target MUST have partial_success=True.

    The transform did not actually run. partial_success=False here is the
    canonical Pattern-2 silent SAFE.
    """
    result = _invoke_simulate(cli_runner, empty_corpus, "move", "ghost", "foo.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    assert summary.get("partial_success") is True, (
        f"empty corpus + missing target: simulation aborted, but envelope "
        f"reports partial_success=False. Got summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #3 - HIGH: summary.state MISSING from cmd_simulate envelope
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-EE REAL BUG (HIGH): cmd_simulate.py L60-83 (error envelope) "
        "AND L122-141 (success envelope) build summary={verdict, operation, "
        "health_delta, health_before, health_after, improved_metrics, "
        "degraded_metrics} - NO state field. Agents have no machine-readable "
        "way to distinguish 'symbol_not_found' / 'no_data_in_corpus' / "
        "'no_op_transform' / 'transform_applied'. Same gap as W805-Y #2 / "
        "W805-U #3. Fix template: add closed-enum summary.state ∈ "
        "{transform_applied, no_op_transform, symbol_not_found, "
        "no_data_in_corpus}. Separate fix wave."
    ),
)
def test_empty_corpus_explicit_state(cli_runner, empty_corpus):
    """Pin: summary.state must distinguish error/no-op/real-transform."""
    result = _invoke_simulate(cli_runner, empty_corpus, "move", "ghost", "foo.py", json_mode=True)
    envelope = _parse_envelope(result)
    state = envelope["summary"].get("state")
    assert state in {
        "symbol_not_found",
        "no_data_in_corpus",
        "empty_input",
        "missing_target",
        "no_signal_data",
        "unresolved_target",
    }, (
        f"empty corpus + missing target: summary.state must disclose the "
        f"resolution-failure state; got {state!r}. cmd_simulate has no "
        f"state field at all today — agents reading 'symbol not found' "
        f"must parse the verdict string instead of reading state."
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #4 - HIGH: no-op transform silently emits SAFE verdict
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-EE REAL BUG (HIGH): cmd_simulate.py L115-120 emits "
        "'health unchanged at 100, modularity unchanged, 0 new cycles' when "
        "the move is a no-op (symbol already in target file). apply_move "
        "(graph/simulate.py L177-189) blindly sets data['file_path'] = "
        "target_file without checking if old_file == target_file. before == "
        "after, the verdict is structurally indistinguishable from a real "
        "'this transform is safe' result. Agents reading the verdict will "
        "treat the no-op as a green-light to apply the transform — when in "
        "fact the transform would do nothing. Same Pattern-2 axis as W805-Y "
        "#3 stub-padding silent overprovisioning. Fix template: detect "
        "no-op transforms in simulate_move (compare resolved source file == "
        "target_file) and surface state='no_op_transform' + "
        "partial_success=True + verdict='no-op: <symbol> already in "
        "<target>'. Separate fix wave."
    ),
)
def test_no_op_transform_explicit_disclosure(cli_runner, clean_corpus):
    """Pin: no-op move (helper -> a.py where helper already lives) must NOT
    emit a SAFE-looking 'health unchanged' verdict.

    The fix should disclose the no-op state via:
      - verdict mentions 'no-op' / 'already' / 'same file' / 'no change'
      - summary.state == 'no_op_transform' (or similar)
      - summary.partial_success=True
    """
    # helper lives in a.py - move it to a.py is a no-op
    result = _invoke_simulate(cli_runner, clean_corpus, "move", "helper", "a.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    verdict = summary["verdict"].lower()
    state = str(summary.get("state", "")).lower()
    op = envelope.get("operation", {})
    from_file = (op.get("from_file") or "").replace("\\", "/")
    to_file = (op.get("to_file") or "").replace("\\", "/")

    # Sanity: verify the cascade shape (from_file == to_file).
    assert from_file == to_file, (
        f"PROOF-OF-CASCADE: this test expects apply_move to set the same "
        f"file_path it started with. Got from_file={from_file!r}, "
        f"to_file={to_file!r}. If this changed, the cascade may have been "
        f"fixed upstream."
    )

    discloses_noop = (
        any(
            m in verdict
            for m in (
                "no-op",
                "noop",
                "no op",
                "already",
                "same file",
                "no change",
                "tautolog",
            )
        )
        or any(
            m in state
            for m in (
                "no_op",
                "noop",
                "unchanged_target",
                "tautology",
            )
        )
        or summary.get("partial_success") is True
    )
    assert discloses_noop, (
        f"no-op move (helper -> a.py where helper already lives) emits a "
        f"SAFE-looking 'health unchanged' verdict indistinguishable from "
        f"a real successful no-risk transform. verdict={summary['verdict']!r}, "
        f"state={summary.get('state')!r}, "
        f"partial_success={summary.get('partial_success')!r}. Agents will "
        f"treat the no-op as green-light when in fact the transform would "
        f"do nothing."
    )
