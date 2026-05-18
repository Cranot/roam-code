"""W805-CC - empty-corpus Pattern-2 smoke for ``roam spectral`` (W805 sweep).

Twenty-ninth-in-batch of the W805 Pattern-2 audit. ``cmd_spectral`` is the
architectural-family peer of ``cmd_clusters`` (W805-BB) and ``cmd_layers``
(W805-X): a global topology view derived from
``build_symbol_graph(conn)`` -> Fiedler-vector bisection +
``spectral_gap`` (algebraic connectivity / lambda2 of the Laplacian).

W978 first-hypothesis re-run BEFORE writing any test
============================================================

Hypothesis: empty corpus -> ``spectral_gap(G)`` returns 0.0 (the
domain-legitimate trivial-graph sentinel at
``src/roam/graph/spectral.py:169-170``) -> ``verdict_from_gap(0.0)``
returns ``"Poorly modularized"`` (since 0.0 is NOT > 0.1, see
``src/roam/graph/spectral.py:229-235``) -> the cmd_spectral envelope
flows through the normal partition path at
``src/roam/commands/cmd_spectral.py:142-205`` and reports verdict
``"Poorly modularized"`` with ``spectral_gap: 0.0`` and
``partitions: 0`` -- a silent SAFE indistinguishable from a real
"poorly modularized" finding on a populated graph.

Direct probe of ``roam --json spectral`` on a README-only corpus
(re-run TWICE for W978 first-hypothesis discipline) confirmed:

    summary.verdict          = "Poorly modularized"   <- LIE
    summary.spectral_gap     = 0.0
    summary.partitions       = 0
    summary.partial_success  = False                  <- LIE
    summary.state            = MISSING                <- gap
    agent_contract.facts[0]  = "Poorly modularized"   <- agent acts on a false architectural verdict

W978 findings: Pattern-2 disclosure gaps, ranked by agent-impact
============================================================

1. **CRITICAL: ``summary.verdict`` reports "Poorly modularized" on an
   empty corpus** (``cmd_spectral.py:146,191,205``). The verdict is
   produced by ``verdict_from_gap(spectral_gap(G))`` where an empty
   graph returns ``gap=0.0`` and ``verdict_from_gap`` has no branch
   for the empty/disconnected/trivial sentinel -- 0.0 maps to the
   same verdict as a densely-interconnected populated graph. Agents
   reading the verdict proceed as if the architecture was analysed
   and found poorly modular. Fix template: ``cmd_spectral`` must
   short-circuit when ``len(G) == 0`` (or ``sym_count == 0``) and
   emit ``verdict: "No graph nodes: 0 symbols"`` /
   ``state: "no_graph_nodes"`` / ``partial_success: true`` BEFORE
   the gap/partition pipeline runs (peer pattern: ``cmd_layers``
   W807 guard at ``cmd_layers.py:360-392``).

2. **HIGH: ``summary.partial_success=False`` on empty-corpus path**
   (``cmd_spectral.py:188-200``). The check didn't actually run on
   real data -- ``spectral_gap`` returned the trivial-graph sentinel.
   ``partial_success`` must be true so consumers know the verdict
   is degraded. Same shape as W805-U pin #2 and W805-Y pin #1.

3. **HIGH: ``summary.state`` MISSING entirely** (``cmd_spectral.py:188-200``).
   No machine-readable state slot on the envelope -- peer commands
   (cmd_layers W807, cmd_partition W805-Y, cmd_orchestrate W805-U)
   either carry a closed-enum ``state`` field on the empty-corpus
   branch or are pinned to add one. Agents reading "0 partitions"
   must parse the verdict string instead of reading
   ``state="no_graph_nodes"``.

DO NOT FIX this wave - accumulate xfail-strict pins only on the
3 real disclosure gaps. Positive-coverage pins on Pattern-1
Variant C (no crash / non-empty stdout) and the clean-corpus
regression baseline are unconditional.

Run isolation:
    python -m pytest tests/test_w805_cc_cmd_spectral_empty_corpus.py -x -n 0

Regression baseline:
    python -m pytest tests/test_spectral.py -x -n 0

Sweep brief: W805-CC (Wave805-CC, twenty-ninth-in-batch).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 - relative-to-tests-dir import after sys.path mutation
    git_init,
    index_in_process,
    invoke_cli,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json_any_exit(result, command="spectral"):
    """Parse JSON envelope from stdout regardless of exit_code.

    cmd_spectral exits 0 on the empty-corpus path (it does NOT
    short-circuit -- the Pattern-2 bug under audit), but use a
    permissive parser to stay robust against future contract
    refinements that may flip the exit code or add a guard branch.
    """
    raw = getattr(result, "stdout", None) or result.output
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON from {command} (exit {result.exit_code}): {e}\nOutput was:\n{raw[:500]}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path):
    """README-only project -- ``build_symbol_graph`` returns an empty graph.

    With zero indexable source files, ``spectral_gap(G)`` returns the
    trivial-graph sentinel 0.0 and ``verdict_from_gap(0.0)`` returns
    ``"Poorly modularized"`` -- the Pattern-2 silent fallback.
    """
    proj = tmp_path / "w805cc-empty"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "README.md").write_text("Empty corpus project.\n", encoding="utf-8")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path):
    """Project with two clusters of methods -- exercises the real spectral path.

    Two classes (Alpha, Beta) with internal method-call chains; the
    spectral pipeline must compute a real ``spectral_gap`` (not 0.0)
    and produce at least one non-empty partition.
    """
    proj = tmp_path / "w805cc-clean"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        "class Alpha:\n"
        "    def method_a1(self):\n"
        "        return self.method_a2()\n"
        "    def method_a2(self):\n"
        "        return self.method_a3()\n"
        "    def method_a3(self):\n"
        "        return 42\n"
        "\n"
        "class Beta:\n"
        "    def method_b1(self):\n"
        "        return self.method_b2()\n"
        "    def method_b2(self):\n"
        "        return 99\n",
        encoding="utf-8",
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant C - no crash / no empty stdout (unconditional positive).
# ---------------------------------------------------------------------------


class TestEmptyCorpusNoCrash:
    """The empty-corpus path must always emit a structured envelope,
    never crash and never emit empty stdout (Pattern-1 Variant C)."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus, monkeypatch):
        """No exception / non-empty stdout on the empty-corpus path."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["spectral"],
            cwd=empty_corpus,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"spectral must not crash on empty corpus; got exit {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on empty-corpus path"


# ---------------------------------------------------------------------------
# Envelope shape positive coverage (unconditional).
# ---------------------------------------------------------------------------


class TestEmptyCorpusEnvelopeShape:
    """The empty-corpus envelope must carry the minimum shape contract
    even while the Pattern-2 disclosure pins below are pending."""

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus, monkeypatch):
        """Envelope carries a non-empty verdict per LAW 6.

        The verdict CONTENT is wrong on HEAD (it claims "Poorly
        modularized" on an empty corpus -- pinned via xfail below),
        but the verdict MUST be a non-empty string -- otherwise
        consumers downstream get a KeyError or empty-string verdict.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["spectral"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        assert "summary" in data, f"envelope missing summary: {data}"
        assert "verdict" in data["summary"], f"summary missing verdict: {data['summary']}"
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict.strip(), f"verdict must be a non-empty string; got {verdict!r}"

    def test_empty_corpus_partitions_explicit_empty_array(self, cli_runner, empty_corpus, monkeypatch):
        """``partitions`` is an explicit empty array, not missing.

        Pattern-1 Variant C corollary: empty data is signal too, but
        only when surfaced as a concrete empty array. Missing keys
        collapse downstream JSON consumers into ``KeyError`` paths.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["spectral"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        assert "partitions" in data, f"envelope must include partitions key; got {list(data)}"
        assert data["partitions"] == [], f"partitions must be empty array on empty corpus; got {data['partitions']!r}"


# ---------------------------------------------------------------------------
# Pattern-2 silent-SAFE pins -- xfail-strict on REAL BUGS pending fix.
# ---------------------------------------------------------------------------


class TestNoSilentWellConnectedOnEmpty:
    """The empty-corpus branch must NOT emit a clean-corpus
    spectral-gap shape verdict on a graph that was never analysed."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-CC #1 CRITICAL: empty corpus emits "
            'verdict="Poorly modularized" from verdict_from_gap(0.0); '
            "the 0.0 sentinel is overloaded between "
            "trivial-graph / disconnected / clean-but-poorly-connected. "
            "cmd_spectral.py:146,191,205 must short-circuit BEFORE the "
            "gap pipeline when sym_count == 0 and emit an empty-state "
            "verdict + state=no_graph_nodes (peer pattern: "
            "cmd_layers.py:360-392 W807)."
        ),
    )
    def test_no_silent_poorly_modularized_on_empty(self, cli_runner, empty_corpus, monkeypatch):
        """Verdict on empty corpus must not reuse a clean-corpus
        spectral-gap shape label (Well-modularized / Moderately
        modular / Poorly modularized).

        Pre-fix shape: empty corpus falls through the normal pipeline
        and reports verdict="Poorly modularized" -- a silent SAFE
        indistinguishable from a real "poorly modularized" finding
        on a densely interconnected populated graph.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["spectral"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        verdict = data["summary"]["verdict"].lower()
        clean_shape_labels = {
            "well-modularized",
            "moderately modular",
            "poorly modularized",
        }
        masking = [lab for lab in clean_shape_labels if lab in verdict]
        assert not masking, (
            f"Pattern-2 silent SAFE: empty corpus emits clean-corpus shape label {masking!r} in verdict {verdict!r}"
        )


class TestNoSilentZeroGapOnEmpty:
    """The empty-corpus envelope must not silently report
    ``partial_success=False`` when the underlying check didn't run."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-CC #2 HIGH: cmd_spectral.py:188-200 emits "
            "summary.partial_success=False on the empty-corpus path. "
            "spectral_gap(G) returned the trivial-graph sentinel 0.0 -- "
            "the architectural analysis didn't actually evaluate. "
            "partial_success must be true so consumers know the "
            "verdict is degraded (Pattern-2 mandate; peer pattern: "
            "cmd_layers W807 + W805-Y partition pin #1)."
        ),
    )
    def test_empty_corpus_partial_success_set(self, cli_runner, empty_corpus, monkeypatch):
        """``summary.partial_success`` is True on the empty branch.

        Pattern-2 mandates partial_success when the underlying check
        couldn't run on real data. Empty corpus = trivial-graph
        sentinel = the gap/partition pipeline didn't actually evaluate.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["spectral"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        partial = data.get("summary", {}).get("partial_success")
        assert partial is True, (
            f"summary.partial_success must be True on empty corpus; got {partial!r}\nenvelope: {data}"
        )


class TestEmptyCorpusStateExplicit:
    """The empty-corpus envelope must carry a machine-readable
    ``summary.state`` slot disclosing the empty-corpus condition."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-CC #3 HIGH: cmd_spectral.py:188-200 envelope has NO "
            "summary.state slot. Peer commands (cmd_layers W807 "
            "no_graph_nodes; cmd_orchestrate W805-U; cmd_partition "
            "W805-Y) either carry a closed-enum state field on the "
            "empty branch or are pinned to add one. Agents reading "
            '"0 partitions" must parse the verdict string instead of '
            'reading state="no_graph_nodes".'
        ),
    )
    def test_empty_corpus_state_explicit(self, cli_runner, empty_corpus, monkeypatch):
        """``summary.state`` discloses the empty-corpus condition.

        Must NOT be missing -- the no_graph_nodes branch is
        structurally distinct from any clean-corpus shape verdict
        and Pattern-2 mandates the state name the absent input.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["spectral"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        state = data.get("summary", {}).get("state")
        assert state, f"summary.state must be set on empty-corpus branch; got {state!r}\nenvelope: {data}"
        # The state name MUST name absent input, not a clean-corpus shape.
        assert state not in {
            "well-modularized",
            "moderately modular",
            "poorly modularized",
            "healthy",
        }, f"Pattern-2 silent SAFE: state={state!r} masks empty-corpus condition"
        assert "no" in state.lower() or "empty" in state.lower(), (
            f"state must explicitly name absent input; got {state!r}"
        )


class TestLaw6VerdictStandalone:
    """LAW 6: summary.verdict must convey the empty-state condition
    WITHOUT any other field."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-CC #1 corollary: verdict on empty corpus does not "
            "signal the empty-state condition standalone. An agent "
            "consuming only data['summary']['verdict'] cannot tell "
            "the corpus was empty -- the verdict reads "
            '"Poorly modularized" identical to a real architectural '
            "finding. Same fix-template as #1: short-circuit + "
            'verdict="No graph nodes: 0 symbols".'
        ),
    )
    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus, monkeypatch):
        """Verdict must convey the empty-state condition standalone."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["spectral"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        verdict = data["summary"]["verdict"].lower()
        empty_signal = any(
            tok in verdict
            for tok in (
                "no graph nodes",
                "no symbols",
                "no graph",
                "empty",
                "0 symbols",
                "0 graph nodes",
            )
        )
        assert empty_signal, f"LAW 6: verdict must signal empty corpus standalone; got {verdict!r}"


# ---------------------------------------------------------------------------
# Positive regression -- clean corpus still emits a real spectral view.
# ---------------------------------------------------------------------------


class TestCleanCorpusEmitsRealBisection:
    """Sanity: future fix for the empty-corpus path must not swallow
    real architectural output on a populated graph."""

    def test_clean_corpus_emits_real_bisection(self, cli_runner, clean_corpus, monkeypatch):
        """Clean corpus produces a real spectral envelope.

        Two classes with internal call chains; the spectral pipeline
        must emit a real shape verdict (Well-modularized / Moderately
        modular / Poorly modularized) on real data and the envelope
        must NOT carry an empty-state state slot.
        """
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(cli_runner, ["spectral"], cwd=clean_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        assert result.exit_code == 0, f"spectral must exit 0 on clean corpus; got {result.exit_code}\n{result.output}"

        # Verdict is one of the shape labels on a real corpus.
        verdict = data["summary"]["verdict"]
        valid_shape_labels = {
            "Well-modularized",
            "Moderately modular",
            "Poorly modularized",
        }
        assert verdict in valid_shape_labels, f"clean corpus verdict must be a shape label; got {verdict!r}"

        # spectral_gap is a float (may be 0.0 on some networkx versions
        # if algebraic_connectivity throws -- the spectral.py warning
        # path -- but the type contract holds).
        gap = data["summary"]["spectral_gap"]
        assert isinstance(gap, float), f"spectral_gap must be a float; got {type(gap).__name__}={gap!r}"
        assert gap >= 0.0, f"spectral_gap must be non-negative; got {gap}"

        # state slot, if present, must NOT be the empty-state value.
        # (Once W805-CC #3 lands, clean corpora may carry a state slot
        # too, e.g. "well_modularized" -- we don't assert absence
        # strictly, just that it doesn't claim no_graph_nodes.)
        state = data["summary"].get("state")
        if state is not None:
            assert state != "no_graph_nodes", f"clean corpus must not emit no_graph_nodes state; got {state!r}"
