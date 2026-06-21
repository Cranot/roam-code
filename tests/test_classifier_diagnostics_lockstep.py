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
    _classify,
)

# `freeform_explore` is the catch-all fallback — it has no regex/helper to
# probe, so it is intentionally absent from the diagnostic registry.
_FALLBACK_PROCEDURES = {"freeform_explore"}


def _classify_return_literals() -> set[str]:
    """Every string literal `_classify` can `return` as the procedure name.

    Structural sub-types are returned via the `sub` variable (not a literal),
    so they are folded in separately from `_STRUCTURAL_SUBTYPE_REGEXES`.
    """
    tree = ast.parse(inspect.getsource(_classify))
    literals: set[str] = set()
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
