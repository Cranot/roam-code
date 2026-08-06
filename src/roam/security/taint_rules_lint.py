"""W489-A: shared helper to capture W454/W479 `qualified_only` lint
warnings emitted by :func:`roam.security.taint_engine.load_rules`.

Hoisted out of :mod:`roam.commands.cmd_taint` so commands that load
taint rules out-of-band (e.g. ``roam cga emit --include-taint``) can
mirror the same envelope-stamping discipline without re-implementing
the regex + dedup-bypass logic.

Returns ``(rules, violations)``. Each violation is
``{"rule_id", "kind", "name", "message"}``. The lint warning text is
pinned by ``tests/test_taint_rule_hygiene.py``; the regex matches the
W479-pinned format and falls back to surfacing the raw ``message`` when
the upstream warning shape changes (defence: never silently drop).
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Any

from roam.security.taint_engine import load_rules

# W489-A: parse the W454/W479 `load_rules` lint warning shape into a
# structured per-violation record so envelopes can disclose
# bare-name-under-qualified_only entries without losing the rule_id /
# kind / name fields buried in the human-readable warning string.
# Format pinned by tests/test_taint_rule_hygiene.py:
#   "[taint-engine] rule '{id}': bare {kind} '{name}' is a no-op under
#    qualified_only=true; ..."
_W489_A_LINT_REGEX = re.compile(r"rule '([^']+)': bare (\w+) '([^']+)'")

# The W479 warning emits the singular form (``kind[:-1]`` in
# ``taint_engine._warn_bare_entries_under_qualified_only``); map it back
# to the canonical registry plural for consumers. Kept as a flat table
# (not a nested ternary) so it adds no control-flow nesting here.
_KIND_SINGULAR_TO_PLURAL = {
    "source": "sources",
    "sink": "sinks",
    "sanitizer": "sanitizers",
}


def count_bare_name_entries(rules: list[Any]) -> int:
    """Count dot-less source/sink/sanitizer entries across ALL *rules*.

    R3 — why this exists as a SEPARATE counter from
    :func:`capture_qualified_only_lint`.

    ``qualified_only_violations`` counts bare entries **only in rules
    that set ``qualified_only: true``**, because
    ``taint_engine.load_rules`` only calls
    ``_warn_bare_entries_under_qualified_only`` under that flag, and the
    capture below only records a warning whose text contains
    ``qualified_only=true``. In the shipped 22-rule pack exactly 3 rules
    set the flag, so the stamped ``qualified_only_violations: 0`` means
    "no rule disabled its own bare names" — while a consumer reads it as
    "no rule has bare names". That is the exact inversion: the corpus is
    full of bare tokens (``eval``, ``system``, ``run``, ``execute``) and
    those bare tokens are what the text scanner matches on.

    This counter is unconditional: every rule, every kind, flag or no
    flag. A consumer reading ``bare_name_entries`` is reading something
    true. It counts ENTRIES, not matches — it is a property of the rule
    pack, not of the corpus being scanned.
    """
    total = 0
    for rule in rules:
        for kind in ("sources", "sinks", "sanitizers"):
            for entry in getattr(rule, kind, ()) or ():
                if entry and "." not in str(entry):
                    total += 1
    return total


def capture_qualified_only_lint(
    rules_path: Path,
) -> tuple[list[Any], list[dict]]:
    """Call ``load_rules`` while recording W454/W479 lint warnings.

    Returns ``(rules, violations)``. Each violation is
    ``{"rule_id", "kind", "name", "message"}``. The regex matches the
    W479-pinned warning text; if it doesn't (unexpected upstream change),
    the row still surfaces the raw ``message`` so the disclosure never
    silently drops.
    """
    # W489-A: simplefilter("always") so duplicate-message dedup doesn't
    # hide a violation on second-call paths (registry doesn't matter
    # here — we're inside catch_warnings).
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        rules = load_rules(rules_path)

    violations: list[dict] = []
    for record in captured:
        message = str(record.message)
        if "qualified_only=true" not in message:
            continue
        match = _W489_A_LINT_REGEX.search(message)
        if match:
            rule_id, kind_singular, name = match.groups()
            # The warning uses singular ("source"/"sink"/"sanitizer");
            # canonicalise to the registry kind plural for consumers.
            # Unknown singulars pass through unchanged (defence: never
            # invent a plural if the upstream vocabulary grows).
            kind = _KIND_SINGULAR_TO_PLURAL.get(kind_singular, kind_singular)
            violations.append(
                {
                    "rule_id": rule_id,
                    "kind": kind,
                    "name": name,
                    "message": message,
                }
            )
        else:
            # Defensive fallback — unexpected warning shape; surface the
            # raw message rather than crashing or silently dropping.
            violations.append(
                {
                    "rule_id": None,
                    "kind": None,
                    "name": None,
                    "message": message,
                }
            )
    return rules, violations
