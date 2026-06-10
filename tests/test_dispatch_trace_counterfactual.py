"""Tests for `roam dispatch-trace --counterfactual`.

The counterfactual block mutates the input prompt 5 ways and re-classifies
each variant, surfacing how nearby phrasings route differently. These
tests pin:

  - the 5 mutation labels appear in deterministic order
  - at least one applicable mutation produces an alternative route for a
    well-chosen seed prompt
  - schema-level guarantees (`alternative_routes`, `counterfactuals`
    fields present, summary verdict in "Routed to X; M of N" shape)
  - LAW-4 anchor compliance on the new fact string
  - existing dispatch-trace behaviour is unchanged when --counterfactual
    is omitted

Invokes the click command directly so we don't depend on `roam.cli`
registration.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from roam.commands.cmd_dispatch_trace import (
    _apply_counterfactual_mutations,
    _build_counterfactual_block,
    _select_mutations_for_prompt,
    dispatch_trace,
)

_EXPECTED_LABELS = (
    "add_definition_verb",
    "add_top_prefix",
    "add_cli_perf_frame",
    "drop_structural_noun",
    "anchor_file",
)


# Shape-adaptive labels (W7 — per-shape mutation families).
_SHAPE_A_LABELS = {"frame_as_why_slow", "frame_as_recently_changed"}
_SHAPE_B_LABELS = {"frame_as_coupling", "frame_as_dependents"}
_SHAPE_C_LABELS = {"frame_as_trace", "frame_as_top_n"}
_SHAPE_D_LABELS = {"frame_as_where_defined", "frame_as_callers"}
_GENERIC_FLOOR_LABELS = {"add_top_prefix", "add_definition_verb"}


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner, *args, json_mode=True):
    obj = {"json": json_mode}
    return runner.invoke(
        dispatch_trace,
        list(args),
        obj=obj,
        catch_exceptions=False,
    )


# --------------------------------------------------------------------------
# Mutation primitive — independent of click / classifier
# --------------------------------------------------------------------------


def test_mutations_emit_all_five_labels_in_order():
    mutations = _apply_counterfactual_mutations("imports of cli.py")
    labels = [m[0] for m in mutations]
    assert tuple(labels) == _EXPECTED_LABELS


def test_add_definition_verb_skipped_when_w11_verb_present():
    mutations = dict((m[0], (m[1], m[2])) for m in _apply_counterfactual_mutations("where is foo defined"))
    mutated, applied = mutations["add_definition_verb"]
    assert applied is False, "W11-shape verb present — mutation must skip"


def test_add_definition_verb_applies_on_bare_phrase():
    mutations = dict((m[0], (m[1], m[2])) for m in _apply_counterfactual_mutations("imports of cli.py"))
    mutated, applied = mutations["add_definition_verb"]
    assert applied is True
    assert mutated.startswith("where is ")


def test_add_top_prefix_skipped_when_top_n_present():
    mutations = dict((m[0], (m[1], m[2])) for m in _apply_counterfactual_mutations("top 5 hot files"))
    mutated, applied = mutations["add_top_prefix"]
    assert applied is False


def test_add_top_prefix_applies_otherwise():
    mutations = dict((m[0], (m[1], m[2])) for m in _apply_counterfactual_mutations("trace the login flow"))
    mutated, applied = mutations["add_top_prefix"]
    assert applied is True
    assert mutated.startswith("top 5 ")


def test_drop_structural_noun_removes_first_match():
    mutations = dict((m[0], (m[1], m[2])) for m in _apply_counterfactual_mutations("files coupled to handlers"))
    mutated, applied = mutations["drop_structural_noun"]
    assert applied is True
    assert "coupled" not in mutated.lower()


def test_anchor_file_skipped_when_path_present():
    mutations = dict((m[0], (m[1], m[2])) for m in _apply_counterfactual_mutations("imports of src/roam/cli.py"))
    mutated, applied = mutations["anchor_file"]
    assert applied is False


def test_anchor_file_appends_default_path():
    mutations = dict((m[0], (m[1], m[2])) for m in _apply_counterfactual_mutations("trace login"))
    mutated, applied = mutations["anchor_file"]
    assert applied is True
    assert mutated.endswith("in src/roam/cli.py")


def test_cli_perf_frame_wraps_first_bareword():
    mutations = dict((m[0], (m[1], m[2])) for m in _apply_counterfactual_mutations("imports of cli"))
    mutated, applied = mutations["add_cli_perf_frame"]
    assert applied is True
    assert "why is `roam " in mutated and "` slow" in mutated


# --------------------------------------------------------------------------
# Counterfactual block — N>=2 distinct routes
# --------------------------------------------------------------------------


def test_build_block_yields_alternative_routes_for_seed_prompt():
    """A backticked-symbol seed should route to N>=2 procedures across mutations.

    "what does `compile_plan` do" matches Shape D (backticked symbol)
    under the shape-adaptive selector. The where_defined + callers
    frames intentionally route to distinct procedures, so distinct>=1
    is guaranteed by design.
    """
    from roam.plan.compiler import _classify

    seed = "what does `compile_plan` do"
    baseline_proc, _ = _classify(seed)
    records, alt_routes, distinct = _build_counterfactual_block(seed, baseline_proc)
    # Schema-stable: 2..5 records, deterministic order from the selector.
    assert 2 <= len(records) <= 5
    labels = [r["label"] for r in records]
    # Shape D fires for this seed.
    assert "frame_as_where_defined" in labels
    assert "frame_as_callers" in labels
    # Distinct-route count > 0 — at least one rephrase routes differently.
    assert distinct >= 1, f"expected >=1 alternative route for backticked-symbol seed; got 0 (records={records})"
    # Aggregation map is consistent with distinct count.
    assert sum(alt_routes.values()) == distinct


# --------------------------------------------------------------------------
# Click command — full JSON envelope
# --------------------------------------------------------------------------


def test_counterfactual_flag_emits_block_in_envelope(runner, tmp_path):
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        # Shape D seed — backticked symbol yields N>=2 distinct routes.
        result = _invoke(
            runner,
            "what does `compile_plan` do",
            "--root",
            str(tmp_path),
            "--counterfactual",
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)

        # Structural fields present.
        assert "counterfactuals" in env
        assert "alternative_routes" in env
        cfs = env["counterfactuals"]
        # Shape-adaptive: 2..5 records, all applied (no skip-state under new selector).
        assert 2 <= len(cfs) <= 5
        # Shape D (backticked symbol) fires for this seed.
        labels = [c["label"] for c in cfs]
        assert "frame_as_where_defined" in labels
        assert "frame_as_callers" in labels
        for c in cfs:
            assert set(c.keys()) >= {"label", "mutated_prompt", "procedure", "confidence", "applied"}
            assert isinstance(c["applied"], bool)

        # Verdict shape — "Routed to X; M of N rephrases route differently".
        verdict = env["summary"]["verdict"]
        assert verdict.startswith("Routed to ")
        assert "rephrases route differently" in verdict

        # Summary counterfactual counters present.
        assert "counterfactual_distinct_routes" in env["summary"]
        assert "counterfactual_applied" in env["summary"]

        # At least one alternative route for this seed.
        assert env["summary"]["counterfactual_distinct_routes"] >= 1
        assert sum(env["alternative_routes"].values()) >= 1
    finally:
        os.chdir(old_cwd)


def test_counterfactual_facts_anchor_on_concrete_plural_terminals(runner, tmp_path):
    """LAW 4: the new counterfactual fact must end on a concrete-noun anchor.

    Mirror of the formatter's anchor set (kept narrow — the counterfactual
    fact only needs the "routes" terminal). Any new fact terminal must be
    added BOTH here AND to `roam.output.formatter:concrete_plural_terminals`
    per AGENTS.md (LAW 4).
    """
    anchors = {
        # Subset sufficient for dispatch-trace facts.
        "alternatives",
        "matches",
        "families",
        "bytes",
        "routes",
        # Already in the formatter set:
        "files",
        "symbols",
        "edges",
        "nodes",
        "cycles",
        "clusters",
        "layers",
        "smells",
        "findings",
        "warnings",
        "errors",
        "lines",
        "tokens",
        "items",
        "entries",
        "records",
        "fields",
        "callers",
        "callees",
        "imports",
        "patterns",
        "alerts",
        "issues",
        "violations",
        "risks",
    }
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        result = _invoke(
            runner,
            "what does `compile_plan` do",
            "--root",
            str(tmp_path),
            "--counterfactual",
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        facts = env["agent_contract"]["facts"]
        # Sanity: the counterfactual fact is present.
        assert any("rephrases" in f for f in facts), (
            f"expected a counterfactual fact mentioning 'rephrases'; got {facts}"
        )
        for fact in facts:
            terminal = fact.rstrip(".?!,;:").split()[-1].lower()
            assert terminal in anchors, f"fact terminal {terminal!r} not in LAW-4 anchor set (fact={fact!r})"
    finally:
        os.chdir(old_cwd)


def test_without_counterfactual_flag_block_absent(runner, tmp_path):
    """Default behaviour unchanged when --counterfactual is omitted."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        result = _invoke(
            runner,
            "imports of cli.py",
            "--root",
            str(tmp_path),
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert "counterfactuals" not in env
        assert "alternative_routes" not in env
        # Existing verdict shape preserved.
        assert "Classified as" in env["summary"]["verdict"]
        assert "probes fired" in env["summary"]["verdict"]
        assert "counterfactual_distinct_routes" not in env["summary"]
    finally:
        os.chdir(old_cwd)


def test_counterfactual_text_mode_renders_table(runner, tmp_path):
    """Text mode should include a 'counterfactual rephrases' header."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        result = _invoke(
            runner,
            "imports of cli.py",
            "--root",
            str(tmp_path),
            "--counterfactual",
            json_mode=False,
        )
        assert result.exit_code == 0, result.output
        assert "counterfactual rephrases" in result.output
        # Header verdict line uses "Routed to" framing.
        assert "VERDICT: Routed to " in result.output
    finally:
        os.chdir(old_cwd)


# --------------------------------------------------------------------------
# Shape-adaptive selector (W7) — one positive prompt per shape
# --------------------------------------------------------------------------


def test_shape_a_cli_verb_emits_perf_and_recency_frames():
    """Prompt with `roam <subcmd>` triggers Shape A: why-slow + recently-changed."""
    selected = _select_mutations_for_prompt("roam health is weird")
    labels = {label for label, _ in selected}
    assert _SHAPE_A_LABELS.issubset(labels), f"expected Shape A labels {_SHAPE_A_LABELS} in {labels}"
    # Mutated prompts wrap the original task text.
    by_label = dict(selected)
    assert by_label["frame_as_why_slow"] == "why is roam health is weird slow"
    assert by_label["frame_as_recently_changed"] == "what changed in roam health is weird"


def test_shape_b_file_path_emits_coupling_and_dependents_frames():
    """Prompt with a file extension triggers Shape B: coupling + dependents."""
    selected = _select_mutations_for_prompt("changes in src/roam/cli.py")
    labels = {label for label, _ in selected}
    assert _SHAPE_B_LABELS.issubset(labels), f"expected Shape B labels {_SHAPE_B_LABELS} in {labels}"
    by_label = dict(selected)
    # Path is captured and re-anchored.
    assert "src/roam/cli.py" in by_label["frame_as_coupling"]
    assert "src/roam/cli.py" in by_label["frame_as_dependents"]


def test_shape_c_vague_about_emits_trace_and_top_n_frames():
    """Prompt with "tell me about / explain / describe" triggers Shape C."""
    selected = _select_mutations_for_prompt("tell me about login")
    labels = {label for label, _ in selected}
    assert _SHAPE_C_LABELS.issubset(labels), f"expected Shape C labels {_SHAPE_C_LABELS} in {labels}"
    by_label = dict(selected)
    assert by_label["frame_as_trace"].startswith("trace ")
    assert by_label["frame_as_top_n"].startswith("top 5 most-relevant files for ")


def test_shape_d_backticked_symbol_emits_where_defined_and_callers_frames():
    """Prompt with a backticked identifier triggers Shape D."""
    selected = _select_mutations_for_prompt("what does `compile_plan` do")
    labels = {label for label, _ in selected}
    assert _SHAPE_D_LABELS.issubset(labels), f"expected Shape D labels {_SHAPE_D_LABELS} in {labels}"
    by_label = dict(selected)
    # Symbol is unwrapped from backticks.
    assert by_label["frame_as_where_defined"] == "where is compile_plan defined"
    assert by_label["frame_as_callers"] == "who calls compile_plan"


def test_shape_fallback_when_no_shape_matches_emits_generic_floor():
    """Prompts that match no shape should fall back to the legacy floor."""
    # No CLI verb, no file path, no "about", no backticked symbol.
    selected = _select_mutations_for_prompt("find duplicated logic")
    labels = {label for label, _ in selected}
    assert _GENERIC_FLOOR_LABELS.issubset(labels), f"expected generic floor labels {_GENERIC_FLOOR_LABELS} in {labels}"


def test_shape_selector_caps_at_five():
    """Selector must never return more than 5 mutations even when many shapes fire."""
    # Stack Shape A (roam <verb>), B (.py path), C (about), D (backticked sym).
    selected = _select_mutations_for_prompt("tell me about `handleSave` in src/foo.py vs roam health")
    assert len(selected) <= 5


def test_shape_a_and_b_produce_different_mutations_than_single_shape():
    """Smoke: a CLI-verb prompt and a vague prompt yield disjoint mutation sets."""
    a = {label for label, _ in _select_mutations_for_prompt("roam compile is slow")}
    c = {label for label, _ in _select_mutations_for_prompt("tell me about login")}
    # Shape A labels appear in `a` but not in `c`.
    assert _SHAPE_A_LABELS & a
    assert not (_SHAPE_A_LABELS & c)
    # Shape C labels appear in `c` but not in `a`.
    assert _SHAPE_C_LABELS & c
    assert not (_SHAPE_C_LABELS & a)
