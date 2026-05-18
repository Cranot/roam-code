"""W805-X — empty-corpus smoke for ``roam layers`` (W805 Pattern-2 sweep).

Twenty-fourth-in-batch of the W805 Pattern-2 audit. ``cmd_layers`` is an
**architectural-query** family command (NOT resolver-bearing): no symbol /
file argument, output is a global topology view derived from
``build_symbol_graph(conn)`` -> ``detect_layers(G)``.

W978 first-hypothesis re-run BEFORE writing any test
============================================================

CLAUDE.md: "Topological layer detection - returns ``{node_id: layer_number}``".
Pattern-2 hypothesis was: empty corpus -> ``detect_layers`` returns ``{}`` ->
``format_layers`` returns ``[]`` -> ``max_layer = 0`` -> the architecture-shape
branch falls through to ``Flat (no layering)`` and verdict reads
``"Flat (no layering) -- 1 layers, 0 violation(s)"`` -- a silent SAFE shape
indistinguishable from a real 1-symbol corpus.

Re-read of ``src/roam/commands/cmd_layers.py:360-392`` shows this branch IS
explicitly guarded by W807:

* ``if not layer_map:`` short-circuits BEFORE the architecture-shape
  computation, emitting an explicit empty-state envelope with
  ``state="no_graph_nodes"``, ``partial_success=True``, and verdict
  ``"No layers detected: 0 graph nodes"`` (LAW 6 standalone-readable,
  LAW 4 anchored on the ``nodes`` concrete-noun terminal).
* Text mode emits a leading ``VERDICT:`` line + a human-readable
  "No layers detected (graph is empty)." second line.
* ``layers=[]`` + ``violations=[]`` are explicit empty arrays, not
  missing keys.

REAL BUGs found in scope: **0**

The W805-X test file therefore captures positive-coverage regression
pins for the already-sealed W807 contract. It serves the W805 sweep
audit trail (architectural-query family shape uniform with cmd_cycles
/ cmd_clusters / cmd_dark_matter) and prevents silent regression of
the empty-corpus disclosure branch.

W978 discipline applied: ran ``roam --json layers`` in-process on a
README-only corpus BEFORE writing assertions; the live output confirms
the W807-tagged branch is the actual code path. No xfail-strict pins
needed -- all assertions pass on HEAD.

Sweep brief: W805-X (Wave805-X, twenty-fourth-in-batch).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 -- relative-to-tests-dir import after sys.path mutation
    git_init,
    index_in_process,
    invoke_cli,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json_any_exit(result, command="layers"):
    """Parse JSON envelope from stdout regardless of exit_code.

    cmd_layers exits 0 on the empty-corpus branch (it's a normal
    informational disclosure, not an error), but use a permissive
    parser to stay robust against future contract refinements.
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

    With zero indexable source files, ``detect_layers`` returns ``{}`` and
    cmd_layers takes the W807 empty-corpus branch (cmd_layers.py:364-392).
    """
    proj = tmp_path / "w805x-empty"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "README.md").write_text("Empty corpus project.\n", encoding="utf-8")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path):
    """Project with one caller + one callee -- exercises the real layers branch.

    ``callee_fn`` is a layer-0 leaf, ``caller_fn`` is layer-1; cmd_layers
    must emit a non-empty ``layers`` array and ``total_layers >= 1``.
    """
    proj = tmp_path / "w805x-clean"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        "def callee_fn():\n    return 42\n\ndef caller_fn():\n    return callee_fn()\n",
        encoding="utf-8",
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash / no empty stdout.
# ---------------------------------------------------------------------------


class TestEmptyCorpusNoCrash:
    """The empty-corpus branch must always emit a structured envelope,
    never crash and never emit empty stdout (Pattern-1 Variant C)."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus, monkeypatch):
        """No exception / non-empty stdout on the empty-corpus path."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["layers"],
            cwd=empty_corpus,
            json_mode=True,
        )
        # The W807 branch is an INFORMATIONAL disclosure (not an error),
        # so exit code is 0. Pin both the exit and the non-empty stdout.
        assert result.exit_code == 0, f"layers must exit 0 on empty corpus; got {result.exit_code}\n{result.output}"
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on empty-corpus path"


# ---------------------------------------------------------------------------
# Pattern-2 / LAW 6 -- explicit empty-state disclosure.
# ---------------------------------------------------------------------------


class TestEmptyCorpusEnvelopeShape:
    """The W807 empty-corpus envelope must carry a loud, machine-readable
    empty-state disclosure (Pattern-2 silent-SAFE prevention)."""

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus, monkeypatch):
        """Envelope carries a non-empty verdict per LAW 6."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["layers"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        assert "summary" in data, f"envelope missing summary: {data}"
        assert "verdict" in data["summary"], f"summary missing verdict: {data['summary']}"
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict.strip(), f"verdict must be a non-empty string; got {verdict!r}"

    def test_empty_corpus_state_explicit(self, cli_runner, empty_corpus, monkeypatch):
        """``summary.state`` discloses the empty-corpus condition (W807).

        Must NOT be "healthy" / "well-layered" / "flat" / missing -- the
        no_graph_nodes branch is structurally distinct from any clean-corpus
        architecture-shape verdict, and Pattern-2 mandates the state name
        the absent input.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["layers"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        state = data.get("summary", {}).get("state")
        assert state, f"summary.state must be set on empty-corpus branch (W807); got {state!r}\nenvelope: {data}"
        # The W807 contract uses 'no_graph_nodes'; accept any state that
        # names the absent input rather than a clean-corpus shape.
        assert state not in {"healthy", "well-layered", "flat", "moderate"}, (
            f"Pattern-2 silent SAFE: state={state!r} masks empty-corpus condition"
        )
        # Be specific about the canonical W807 state name; if a future
        # refactor renames it, this assertion forces an intentional update.
        assert "no" in state.lower() or "empty" in state.lower(), (
            f"state must explicitly name absent input; got {state!r}"
        )

    def test_empty_corpus_partial_success_set(self, cli_runner, empty_corpus, monkeypatch):
        """``summary.partial_success`` is True on the empty branch (W807).

        Pattern-2 mandates partial_success when the underlying check
        couldn't run on real data. Empty corpus = ``layer_map={}`` =
        the architecture-shape computation didn't actually evaluate.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["layers"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        partial = data.get("summary", {}).get("partial_success")
        assert partial is True, (
            f"summary.partial_success must be True on empty corpus; got {partial!r}\nenvelope: {data}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus, monkeypatch):
        """Verdict must convey the empty-state condition WITHOUT any other field.

        LAW 6: "summary.verdict must work without any other field."
        An agent consuming only ``data["summary"]["verdict"]`` must be
        able to tell the corpus was empty.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["layers"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        verdict = data["summary"]["verdict"].lower()
        # Must mention either "no layers" / "empty" / "0 graph nodes" /
        # "no symbols" -- vocabulary roam already uses across the codebase.
        empty_signal = any(
            tok in verdict for tok in ("no layers", "empty", "0 graph nodes", "no symbols", "no graph", "0 layers")
        )
        assert empty_signal, f"LAW 6: verdict must signal empty corpus standalone; got {verdict!r}"

    def test_empty_corpus_layers_and_violations_empty_arrays(self, cli_runner, empty_corpus, monkeypatch):
        """``layers`` + ``violations`` are explicit empty arrays, not missing.

        Pattern-1 Variant C corollary: empty data is signal too, but only
        when surfaced as a concrete empty array. Missing keys collapse
        downstream JSON consumers into ``KeyError`` paths.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["layers"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        assert "layers" in data, f"envelope must include layers key; got {list(data)}"
        assert "violations" in data, f"envelope must include violations key; got {list(data)}"
        assert data["layers"] == [], f"layers must be empty array; got {data['layers']!r}"
        assert data["violations"] == [], f"violations must be empty array; got {data['violations']!r}"


# ---------------------------------------------------------------------------
# Pattern-2 silent-SAFE prevention -- defensive xfail-style pins on the
# WRONG shapes the W807 guard prevents. These currently PASS on HEAD;
# they would BREAK on regression that strips the W807 branch.
# ---------------------------------------------------------------------------


class TestNoSilentWellLayeredOnEmpty:
    """The empty-corpus branch must NOT emit a clean-corpus architecture
    shape verdict (no "Flat" / "Well-layered" / "Moderate" labels)."""

    def test_no_silent_well_layered_on_empty(self, cli_runner, empty_corpus, monkeypatch):
        """Verdict on empty corpus does NOT use clean-corpus shape labels.

        Pre-W807 regression shape: empty corpus fell through to
        ``shape = "Flat (no layering)"`` because ``max_layer = 0 <= 1``,
        producing verdict "Flat (no layering) -- 1 layers, 0 violation(s)"
        -- a silent SAFE indistinguishable from a 1-symbol corpus.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["layers"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        verdict = data["summary"]["verdict"]
        # The pre-W807 shape would emit "1 layers" + "Flat (no layering)".
        # The W807-guarded shape emits "0 graph nodes" with no shape label.
        assert "1 layers" not in verdict, f"Pattern-2 regression: empty corpus emits '1 layers' verdict ({verdict!r})"
        assert "flat (no layering)" not in verdict.lower(), (
            f"Pattern-2 regression: empty corpus emits 'Flat' shape ({verdict!r})"
        )
        assert "well-layered" not in verdict.lower(), (
            f"Pattern-2 regression: empty corpus emits 'Well-layered' ({verdict!r})"
        )

    def test_no_silent_zero_violations_on_empty(self, cli_runner, empty_corpus, monkeypatch):
        """Empty corpus must not silently report 'clean layering' / '0 violations'
        as the headline disclosure.

        ``total_layers`` on the W807 branch is 0, not 1, so the verdict
        cannot be interpreted as "1 trivial layer, 0 violations -- looks
        healthy".
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(cli_runner, ["layers"], cwd=empty_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        total_layers = data["summary"].get("total_layers")
        # W807 pins total_layers to 0; the pre-W807 shape emitted 1.
        assert total_layers == 0, f"empty corpus must report total_layers=0; got {total_layers!r}\nenvelope: {data}"


# ---------------------------------------------------------------------------
# Positive regression -- clean corpus still emits a real architecture view.
# ---------------------------------------------------------------------------


class TestCleanCorpusEmitsRealLayers:
    """Sanity: the W807 guard must not swallow real architectural output."""

    def test_clean_corpus_emits_real_layers(self, cli_runner, clean_corpus, monkeypatch):
        """Clean corpus produces a non-empty layers structure + a real shape verdict.

        Caller -> callee is a 1-edge call chain; ``callee_fn`` is layer 0,
        ``caller_fn`` is layer 1, so ``total_layers >= 1`` and the
        ``list_counts.layers`` count is non-zero. (Without ``--detail`` the
        envelope's per-layer arrays are stripped via ``strip_list_payloads``;
        the canonical count lives in ``list_counts``.)
        """
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(cli_runner, ["layers"], cwd=clean_corpus, json_mode=True)
        data = _parse_json_any_exit(result)
        list_counts = data.get("list_counts", {})
        layer_count = list_counts.get("layers", 0)
        assert layer_count >= 1, (
            f"clean corpus must emit list_counts.layers >= 1; got {layer_count!r}\nenvelope: {data}"
        )
        total_layers = data["summary"].get("total_layers")
        assert isinstance(total_layers, int) and total_layers >= 1, (
            f"clean corpus must report total_layers >= 1; got {total_layers!r}"
        )
        # The clean-corpus verdict carries a real architecture-shape label
        # (Flat / Moderate / Well-layered), NOT the W807 empty-state phrase.
        verdict = data["summary"]["verdict"]
        assert "0 graph nodes" not in verdict, f"clean corpus must not emit empty-state verdict; got {verdict!r}"
        # state should be absent on the clean-corpus branch (W807 is
        # the only branch that sets summary.state). Don't assert absence
        # strictly -- a future refactor may add a shape state -- but
        # whatever's there must not be the empty-state value.
        state = data["summary"].get("state")
        if state is not None:
            assert state != "no_graph_nodes", f"clean corpus must not emit no_graph_nodes state; got {state!r}"
