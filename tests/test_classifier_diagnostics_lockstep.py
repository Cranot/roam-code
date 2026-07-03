"""Regression guard: the shared classifier diagnostic registry must enumerate
EVERY procedure `_classify` can return as a winner.

Before the shared `_CLASSIFIER_DIAGNOSTICS` registry, `roam compile --explain`
reported only the synthesis/structural regexes, so a helper-routed winner
(`refactor_move`, `stack_trace_fix`, `session_meta`, `self_contained_task`,
`top_n_ranking`, `compare_x_vs_y`, `describe_file`, ...) could WIN in `_classify`
while no diagnostic key matched — the "← winner" marker never rendered.

The fix replaced the local explain-pattern tuple with the shared registry, but
the "keep this list in lockstep with the `_classify` chain" promise was only a
code comment. This test turns that comment into a guard: it AST-scans `_classify`
for every returned procedure literal (plus the structural sub-types, which
`_classify` returns via the `sub` variable from `_STRUCTURAL_SUBTYPE_REGEXES`)
and asserts each one is a key in `_CLASSIFIER_DIAGNOSTICS`. A new procedure added
to `_classify` without a matching diagnostic probe now fails CI instead of
silently degrading the explain dump.
"""

from __future__ import annotations

import ast
import inspect

from roam.plan.compiler import (
    _CLASSIFIER_DIAGNOSTICS,
    _STRUCTURAL_SUBTYPE_REGEXES,
    _arbitrate_structural,
    _classify,
    _explain_classifier,
)

# `freeform_explore` is the catch-all fallback — it has no regex/helper to
# probe, so it is intentionally absent from the diagnostic registry.
_FALLBACK_PROCEDURES = {"freeform_explore"}

# `structural_general` is a DIAGNOSTIC-ONLY signal, not a `_classify` winner.
# `_STRUCTURAL_RE` is the broad alias built FROM every structural sub-type
# pattern, so its probe reports "this task looks structural" in the explain
# dump — but `_classify` never *returns* `structural_general`: the only
# structural winners are the six specific sub-types in
# `_STRUCTURAL_SUBTYPE_REGEXES`, and an otherwise-unmatched structural-ish task
# falls through to `freeform_explore`. It is allowlisted so the reverse-direction
# guard below treats it as intentional rather than stale.
_DIAGNOSTIC_ONLY_SIGNALS = {"structural_general"}


def _classify_return_literals() -> set[str]:
    """Every string literal `_classify` can `return` as the procedure name.

    Structural sub-types are returned via the `sub` variable (not a literal),
    so they are folded in separately from `_STRUCTURAL_SUBTYPE_REGEXES`.

    The `top_n_ranking` / `compare_x_vs_y` winners are returned by the
    `_arbitrate_structural` helper (which `_classify` delegates the
    top_n/compare/structural-subtype arbitration to), so its source is scanned
    alongside `_classify` — the helper's returns ARE `_classify` winners.
    """
    literals: set[str] = set()
    for fn in (_classify, _arbitrate_structural):
        tree = ast.parse(inspect.getsource(fn))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            value = node.value
            # `return "procedure", rejected` — the procedure is the first element.
            if isinstance(value, ast.Tuple) and value.elts:
                value = value.elts[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                literals.add(value.value)
    return literals


def test_every_classify_winner_has_a_diagnostic_probe():
    registry_keys = {name for name, _ in _CLASSIFIER_DIAGNOSTICS}

    expected = _classify_return_literals() - _FALLBACK_PROCEDURES
    # Structural sub-types reach the caller via the `sub` variable, not a
    # literal return, so add them from the shared precedence tuple.
    expected |= {subtype for subtype, _ in _STRUCTURAL_SUBTYPE_REGEXES}

    missing = sorted(expected - registry_keys)
    assert not missing, (
        "These procedures can win in _classify but have no probe in "
        f"_CLASSIFIER_DIAGNOSTICS: {missing}. Add a ("
        "name, _diag_*) entry so `roam compile --explain` can mark the winner."
    )


def test_no_stale_diagnostic_probes():
    """Reverse-direction lockstep: every registry key must be EITHER a real
    `_classify` winner (literal return or structural sub-type) OR an explicitly
    allowlisted diagnostic-only signal.

    The forward guard (`test_every_classify_winner_has_a_diagnostic_probe`)
    catches a NEW procedure added to `_classify` without a probe. This catches
    the opposite drift: a probe left behind in `_CLASSIFIER_DIAGNOSTICS` after
    its procedure was removed from `_classify`, which would make
    `roam compile --explain` advertise a key that can never become the winner.
    Together they make the "keep this list in lockstep" comment a two-way guard.
    """
    registry_keys = {name for name, _ in _CLASSIFIER_DIAGNOSTICS}

    allowed = _classify_return_literals() - _FALLBACK_PROCEDURES
    allowed |= {subtype for subtype, _ in _STRUCTURAL_SUBTYPE_REGEXES}
    allowed |= _DIAGNOSTIC_ONLY_SIGNALS

    stale = sorted(registry_keys - allowed)
    assert not stale, (
        "These _CLASSIFIER_DIAGNOSTICS keys are neither a _classify winner nor "
        f"an allowlisted diagnostic-only signal: {stale}. Remove the stale probe "
        "(its procedure left _classify) or, if it is an intentional non-winner "
        "signal, add it to _DIAGNOSTIC_ONLY_SIGNALS with a rationale."
    )


def test_registry_keys_are_unique():
    keys = [name for name, _ in _CLASSIFIER_DIAGNOSTICS]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"Duplicate diagnostic keys in _CLASSIFIER_DIAGNOSTICS: {dupes}"


def test_diagnostic_probes_are_callable_and_return_lists():
    # Every probe must accept a task string and return a list (empty on no
    # match) — the explain dump iterates `probe(task)` and tests truthiness.
    for name, probe in _CLASSIFIER_DIAGNOSTICS:
        result = probe("")
        assert isinstance(result, list), f"probe {name!r} did not return a list on empty input"


# ---- tiebreak_rules-vs-_classify lockstep ------------------------------------
# `_explain_classifier` ships a human-readable `tiebreak_rules` list that
# "Mirrors the actual arbitration order in `_classify`". Rule 5 (structural
# sub-type order) is mechanically derived from `_STRUCTURAL_SUBTYPE_REGEXES`,
# but rules 1-4/6 are hand-prose whose ordering claims (`trace > structural`,
# `refactor_move > synthesis`, `synthesis > structural`,
# `top_n_ranking / compare_x_vs_y > structural sub-types`) were only enforced by
# a `# Keep in sync` comment. These guards turn each prose claim into an
# executable assertion: a representative prompt that triggers BOTH competing
# procedures must route to the procedure the prose says wins. A future `_classify`
# reorder that contradicts the displayed rules now fails CI instead of silently
# shipping a misleading explain dump.


def test_tiebreak_rule5_matches_structural_subtype_registry():
    # Rule 5's displayed order must equal the live precedence tuple — this pins
    # `coupling` before `blast`/`callers` (the ordering the backlog item flagged)
    # to the single source of truth, so the text cannot drift on a reorder.
    rules = _explain_classifier("x")["tiebreak_rules"]
    rule5 = next(r for r in rules if r.startswith("5."))
    derived = ", ".join(name.removeprefix("structural_") for name, _ in _STRUCTURAL_SUBTYPE_REGEXES)
    assert rule5.endswith(derived), (
        f"tiebreak rule 5 ({rule5!r}) does not list the live _STRUCTURAL_SUBTYPE_REGEXES order ({derived!r})."
    )
    # Guard the specific inversion the backlog item described.
    assert derived.index("coupling") < derived.index("blast") < derived.index("callers")


def test_tiebreak_prose_ordering_claims_match_classify():
    # Each prompt fires BOTH competing procedures; the value is the winner the
    # prose claims. If `_classify`'s arbitration order ever contradicts a
    # displayed rule, the corresponding case fails.
    claims = {
        "1. trace > structural": ("trace the import cycle through the auth module", "trace_query"),
        "2. refactor_move > synthesis": (
            "extract validate_token from auth.py into a new helper module",
            "refactor_move",
        ),
        "3. synthesis > structural": (
            "write a test that exercises the coupling between modules",
            "synthesis_query",
        ),
        "4a. top_n_ranking > structural": ("top 5 most coupled files", "top_n_ranking"),
        "4b. compare_x_vs_y > structural": ("compare cli.py vs mcp_server.py", "compare_x_vs_y"),
        "6. freeform fallback": ("tell me a joke about cats", "freeform_explore"),
    }
    mismatches = {
        rule: (prompt, expected, _classify(prompt)[0])
        for rule, (prompt, expected) in claims.items()
        if _classify(prompt)[0] != expected
    }
    assert not mismatches, "tiebreak_rules prose contradicts _classify routing: " + "; ".join(
        f"{rule}: {prompt!r} -> got {got!r}, prose claims {exp!r}" for rule, (prompt, exp, got) in mismatches.items()
    )
