"""W11/W12/W13 — classifier routing + dispatch-wiring tests.

Each family has:
  - 5 POSITIVE prompts that MUST classify into the new procedure
  - 5 NEGATIVE prompts that must NOT classify into it (precision check)

Plus a per-family dispatch test: invoking compile_plan + _probe_for_procedure
on a positive prompt returns a non-None probe dict (or documented empty).

Precedence regression: "top 5 most-imported files" used to mis-route to
``structural_coupling`` because the structural subtype check ran first.
That regression is pinned by the W12 positives below."""

from __future__ import annotations

import pytest

from roam.plan.compiler import (
    _PROBE_DISPATCH,
    PlanV0,
    _classify,
    _is_cli_verb_why_slow,
    _is_symbol_defined_where,
    _is_top_n_ranking,
    route_for_plan,
)

# ---- W11 — symbol_defined_where ---------------------------------------

_W11_POSITIVE = [
    "where is _evaluate_mcp_mode_policy defined",
    "find where useKiniseisBalance is",
    "where is compile_plan defined",
    "which file defines RoamPluginContext",
    "where is run_produced_test",
    # W11.1 (2026-06-02 broadening) — "locate" verb variants.
    "locate `compile_plan`",
    "locate the function compile_plan",
    "locate run_produced_test",
    # W11.2 — bare backticked "find `sym`" (no "function|method|class|symbol").
    "find `compile_plan`",
    # W11.3 — "what file holds X" and "where does X live".
    "what file holds compile_plan",
    "where does compile_plan live",
    # W11.4 (2026-06-05 telemetry) — noun between verb and symbol:
    # "find where the function X is defined" (Alt 1's lookahead blocked "the").
    "Find where the function _evaluate_mcp_mode_policy is defined",
    "where is the function compile_plan defined",
    "find where the class Indexer lives",
]

_W11_NEGATIVE = [
    # file-path anchored — file-info / explain intent
    "where is the bug in src/roam/cli.py",
    # generic English question — no concrete bareword
    "where is the documentation",
    # W11.5 (2026-06-05 dogfood) — concept searches: a PLAIN noun followed by
    # more words is conceptual, NOT "find symbol X" (was extracting SQL/security
    # /performance/memory as bogus symbols → garbage prefetch).
    "find SQL injection risks",
    "find security issues",
    "find performance problems",
    "find memory leaks",
    "find race conditions",
    # caller-intent verb (handled by structural_callers)
    "who calls _evaluate_mcp_mode_policy",
    # stack-trace shape
    'File "src/roam/cli.py", line 5 — TypeError',
    # ranking shape — must go to W12
    "top 5 most-imported files",
]


@pytest.mark.parametrize("task", _W11_POSITIVE)
def test_w11_positive_classifies_symbol_defined_where(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc == "symbol_defined_where", f"expected symbol_defined_where, got {proc!r} for {task!r}"


@pytest.mark.parametrize("task", _W11_NEGATIVE)
def test_w11_negative_does_not_misroute_to_symbol_defined_where(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc != "symbol_defined_where", f"unexpected symbol_defined_where for {task!r}"


# ---- W12 — top_n_ranking ----------------------------------------------

_W12_POSITIVE = [
    "top 5 most-imported files",
    "top danger zone file",
    "top 10 most-coupled files",
    "biggest churning files",
    "top 3 most-called functions",
    # W12.1 (2026-06-02 broadening) — hyphenated "most-X file" shape.
    "most-imported file in the project",
    # W12.2 — anchor + dimension noun directly (no "files" suffix).
    "biggest cycles",
    "biggest cluster",
    # W12.3 — "top N <noun> by <dim>" shape.
    "top 3 functions by complexity",
    # W12.4 — new "hot"/"slow"/"deep" anchors.
    "hottest files by churn",
    "slowest functions by complexity",
]

_W12_NEGATIVE = [
    # structural intent without ranking dimension
    "what files are coupled to cli.py",
    # caller intent
    "who calls _evaluate_mcp_mode_policy",
    # symbol-define intent (W11)
    "where is run_produced_test defined",
    # plain freeform
    "explain this codebase",
    # CLI-verb perf shape (W13)
    "why is roam index slow",
]


@pytest.mark.parametrize("task", _W12_POSITIVE)
def test_w12_positive_classifies_top_n_ranking(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc == "top_n_ranking", f"expected top_n_ranking, got {proc!r} for {task!r}"


@pytest.mark.parametrize("task", _W12_NEGATIVE)
def test_w12_negative_does_not_misroute_to_top_n_ranking(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc != "top_n_ranking", f"unexpected top_n_ranking for {task!r}"


# ---- W13 — cli_verb_why_slow -------------------------------------------

_W13_POSITIVE = [
    "why is roam index slow",
    "why is roam compile slow",
    "why is roam health slow",
    "roam diff slow",
    "roam dead hangs",
    # W13.1 (2026-06-02 broadening) — "take so long" alt phrasing.
    "why does roam index take so long",
    "why does `roam compile` take so long",
    # W13.2 — "why is `roam X` so slow" (backticked verb).
    "why is `roam health` so slow",
    # W13.3 — "roam X is slow" / declarative shapes.
    "roam index is slow",
    "roam compile is hanging",
    "roam dead stalls",
]

_W13_NEGATIVE = [
    # bare 'why slow' without roam CLI verb
    "why is this function slow",
    # 'roam <unknown>' must NOT classify (resolver gate)
    "why is roam thisisnotacommand slow",
    # symbol-define shape
    "where is compile_plan defined",
    # caller-intent
    "who calls run_produced_test",
    # plain freeform
    "describe the codebase",
]


@pytest.mark.parametrize("task", _W13_POSITIVE)
def test_w13_positive_classifies_cli_verb_why_slow(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc == "cli_verb_why_slow", f"expected cli_verb_why_slow, got {proc!r} for {task!r}"


@pytest.mark.parametrize("task", _W13_NEGATIVE)
def test_w13_negative_does_not_misroute_to_cli_verb_why_slow(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc != "cli_verb_why_slow", f"unexpected cli_verb_why_slow for {task!r}"


@pytest.mark.parametrize(
    ("procedure", "task", "prefetched"),
    [
        (
            "symbol_defined_where",
            "where is compile_plan defined",
            {"symbol_definitions": [{"name": "compile_plan"}]},
        ),
        (
            "top_n_ranking",
            "top 5 most-called functions",
            {"top_n_ranking": [{"name": "compile_plan"}]},
        ),
        (
            "cli_verb_why_slow",
            "why is roam compile slow",
            {"cli_verb_slow_diagnosis": {"entry": "compile"}},
        ),
    ],
)
def test_route_for_plan_l1_for_task_text_probe_families(
    monkeypatch, procedure: str, task: str, prefetched: dict
) -> None:
    plan = PlanV0(
        task=task,
        procedure=procedure,
        likely_files=[],
        required_checks=[],
        forbidden_paths=[],
        plan_quality=0.5,
        model_calls_avoided=[],
        recommended_first_command="",
        classifier_confidence=0.9,
    )

    monkeypatch.setattr(
        PlanV0,
        "to_l1_probe_envelope",
        lambda self, cwd=None: {
            "schema": "roam-plan-v0-l1-probe",
            "plan": {"prefetched_facts": prefetched},
        },
    )

    routing = route_for_plan(plan, cwd="/tmp/repo")

    assert routing["envelope"] == "l1_probe"
    assert routing["contract_id"] == f"{procedure}_3step"


# Dogfood 2026-06-07 — "fix the bug where roam X is slow" mis-routed to
# cli_verb_why_slow (perf DIAGNOSIS) when it is a bug-FIX. Edit intent must
# fall through; the genuine diagnosis shape must still classify.
@pytest.mark.parametrize(
    "task",
    [
        "fix the bug where roam verify --report is slow on large repos",
        "the roam index command is slow, fix it",
        "optimize roam compile which is slow",
        "refactor roam health since it is slow",
    ],
)
def test_w13_edit_intent_does_not_misroute_to_why_slow(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc != "cli_verb_why_slow", f"{task!r} mis-routed to {proc}"
    assert not _is_cli_verb_why_slow(task)


@pytest.mark.parametrize(
    "task",
    [
        "why is roam verify slow",
        "roam compile hangs forever",
        "why is roam dead slow",
    ],
)
def test_w13_legit_why_slow_still_classifies(task: str) -> None:
    assert _classify(task)[0] == "cli_verb_why_slow"


# ---- Dispatch wiring --------------------------------------------------


def test_w11_dispatch_registered() -> None:
    assert "symbol_defined_where" in _PROBE_DISPATCH


def test_w12_dispatch_registered() -> None:
    assert "top_n_ranking" in _PROBE_DISPATCH


def test_w13_dispatch_registered() -> None:
    assert "cli_verb_why_slow" in _PROBE_DISPATCH


# ---- structural_coupling "between A and B" (2026-06-05 telemetry) -----


@pytest.mark.parametrize(
    "task",
    [
        "trace coupling between src/roam/cli.py and src/roam/atomic_io.py",
        "what is the coupling between cli.py and parser.py",
        "show coupling between the indexer and the parser",
    ],
)
def test_coupling_between_routes_to_structural_coupling(task: str) -> None:
    # "coupling between A and B" leaked to freeform (the regex had
    # coupling/to/for/of but not "between").
    assert _classify(task)[0] == "structural_coupling", task


# ---- Precedence regression (the bug that caused this work) ------------


def test_w12_precedence_over_structural_coupling() -> None:
    """``top 5 most-imported files`` ALSO matches the structural_coupling
    keyword regex; W12 must win because the ranking intent is more
    specific. Regression test for the 2026-06-02 routing fix."""
    proc, rejected = _classify("top 5 most-imported files")
    assert proc == "top_n_ranking"
    # The rejected list should mention structural was overridden.
    assert any("structural" in r for r in rejected), f"expected structural override note in rejected={rejected!r}"


# ---- Helper-level sanity ---------------------------------------------


def test_extractor_helpers_consistent_with_classifier() -> None:
    """``_is_top_n_ranking`` / ``_is_symbol_defined_where`` /
    ``_is_cli_verb_why_slow`` must agree with ``_classify`` on the
    positive prompts. This catches drift where a helper changes but
    the classifier doesn't (or vice versa)."""
    for task in _W11_POSITIVE:
        assert _is_symbol_defined_where(task), f"W11 helper missed {task!r}"
    for task in _W12_POSITIVE:
        assert _is_top_n_ranking(task), f"W12 helper missed {task!r}"
    for task in _W13_POSITIVE:
        assert _is_cli_verb_why_slow(task), f"W13 helper missed {task!r}"


# ---- cli-command file resolution (2026-06-05 dogfood) ----


def test_resolve_cli_command_files():
    """`roam <subcommand>` resolves to the subcommand's module file (so command
    tasks get the file to read/edit). No-op for non-commands / outside the repo."""
    import os

    from roam.plan.compiler import _resolve_cli_command_files

    cwd = os.getcwd()
    assert _resolve_cli_command_files("add a flag to roam smells", cwd) == ["src/roam/commands/cmd_smells.py"]
    assert _resolve_cli_command_files("why is roam dead slow", cwd) == ["src/roam/commands/cmd_dead.py"]
    assert _resolve_cli_command_files("roam is a great tool", cwd) == []  # 'is' not a command
    assert _resolve_cli_command_files("no command here", cwd) == []
