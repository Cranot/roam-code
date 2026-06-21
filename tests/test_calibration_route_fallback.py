"""Maintainability guard for the calibration route-tier fallback.

Newer classifier procedures (symbol_defined_where, top_n_ranking,
compare_x_vs_y, file_history, ...) are registered in the compiler's
`_L1_PROBE_ELIGIBLE` / `_ARTIFACT_POLICY` tables but are deliberately ABSENT
from any profile's `procedure_routes`. They therefore inherit the conservative
`DEFAULT_TIER` ("heavy") via `CalibrationProfile.tier_for`.

This is intentional — a procedure with no measured cheap-model route is paid
for at the safe (heavy) tier rather than silently downgraded. These tests pin
that contract so that:

  1. The fallback default stays `heavy` (changing it is a behavior change).
  2. The fallback is applied uniformly through `tier_for`, not a magic literal.
  3. The exact set of procedures relying on the fallback is pinned — adding a
     new classifier procedure trips `test_no_unintended_fallback_procedures`,
     forcing the author to consciously choose a `light` route or acknowledge
     the heavy default by extending the expected set below.

Adding `light` routes would be a separate behavior change, out of scope here.
"""

from __future__ import annotations

from roam.plan.calibration import (
    DEFAULT_TIER,
    CLAUDE_2026_05,
    get_profile,
)
from roam.plan.compiler import _ARTIFACT_POLICY, _L1_PROBE_ELIGIBLE


# Classifier procedures the compiler knows about: every artifact-policy key
# plus every L1-probe-eligible procedure. This is the universe that flows
# through `route_for_plan -> _model -> profile.tier_for`.
_CLASSIFIER_PROCEDURES = frozenset(_ARTIFACT_POLICY) | frozenset(_L1_PROBE_ELIGIBLE)

# Procedures KNOWN to rely on the heavy fallback today (absent from the
# validated profile's procedure_routes). Pinned so a new addition is a
# deliberate edit, not a silent default. To route one cheaply, add it to
# CLAUDE_2026_05.procedure_routes (a measured behavior change) and drop it
# from this set; to keep it heavy on purpose, add it here.
_EXPECTED_FALLBACK_PROCEDURES = frozenset(
    {
        "structural_query",  # legacy fallback
        "refactor_move",
        "describe_file",
        "stack_trace_fix",
        "file_history",
        "repo_structure",
        "entry_point_where",
        "config_where",
        "session_meta",
        "self_contained_task",
    }
)


def test_default_tier_is_heavy() -> None:
    """The conservative fallback must stay heavy — downgrading is a behavior change."""
    assert DEFAULT_TIER == "heavy"


def test_tier_for_unknown_procedure_uses_default() -> None:
    profile = get_profile("claude-2026-05")
    assert profile.tier_for("a_procedure_that_does_not_exist") == DEFAULT_TIER


def test_tier_for_respects_explicit_routes() -> None:
    """Explicitly-routed procedures bypass the fallback."""
    for procedure, tier in CLAUDE_2026_05.procedure_routes.items():
        assert CLAUDE_2026_05.tier_for(procedure) == tier


def test_no_unintended_fallback_procedures() -> None:
    """Lint: every classifier procedure absent from procedure_routes is acknowledged.

    A failure here means a new classifier procedure was added to the compiler
    without a routing decision. Either add a measured route to
    CLAUDE_2026_05.procedure_routes, or add the name to
    _EXPECTED_FALLBACK_PROCEDURES to record that heavy is intentional.
    """
    actual_fallback = _CLASSIFIER_PROCEDURES - set(CLAUDE_2026_05.procedure_routes)
    missing = actual_fallback - _EXPECTED_FALLBACK_PROCEDURES
    stale = _EXPECTED_FALLBACK_PROCEDURES - actual_fallback
    assert not missing, (
        f"New classifier procedures fall through to the heavy default without a "
        f"routing decision: {sorted(missing)}. Add a route to "
        f"CLAUDE_2026_05.procedure_routes or list them in "
        f"_EXPECTED_FALLBACK_PROCEDURES."
    )
    assert not stale, (
        f"These procedures no longer fall through (now routed explicitly); "
        f"drop them from _EXPECTED_FALLBACK_PROCEDURES: {sorted(stale)}."
    )


# The W11/W12/W13/W28 task-text-only probe families. AGENTS.md documents these
# as "route to `l1_probe` with embedded probe answers": when the probe fires,
# the envelope already contains the answer, so the light model suffices to
# render it. Pinned here (not just as values in procedure_routes) so a future
# retune can't silently revert them to the heavy default — the decision is
# probe-answer-shape, not a magnitude guess.
_L1_PROBE_ANSWER_PROCEDURES = frozenset(
    {"symbol_defined_where", "top_n_ranking", "cli_verb_why_slow", "compare_x_vs_y"}
)


def test_l1_probe_answer_procedures_route_light() -> None:
    """Successful probe routes must not pay the heavy model.

    For these four families `route_for_plan` reaches `tier_for` only after
    `_l1_has_procedure_data` is True — the envelope already contains the
    answer. A `heavy` value here means Sonnet is paid to restate a
    probe-computed answer (the regression this pins). The four are also
    L1-probe-eligible; if that ever changes the test fails loudly so the
    light route is re-justified rather than rotting.
    """
    assert _L1_PROBE_ANSWER_PROCEDURES <= set(_L1_PROBE_ELIGIBLE), (
        "An L1 probe-answer procedure is no longer L1-probe-eligible; the "
        "light route needs re-justifying."
    )
    profile = get_profile("claude-2026-05")
    for procedure in _L1_PROBE_ANSWER_PROCEDURES:
        tier = profile.tier_for(procedure)
        assert tier == "light", (
            f"{procedure} is an L1 probe-answer procedure (answer embedded on a "
            f"successful probe) but routes {tier!r}; it should be 'light'. See "
            f"CLAUDE_2026_05.procedure_routes."
        )
