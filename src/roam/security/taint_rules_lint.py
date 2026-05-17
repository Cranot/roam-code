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
            kind = (
                "sources"
                if kind_singular == "source"
                else "sinks"
                if kind_singular == "sink"
                else "sanitizers"
                if kind_singular == "sanitizer"
                else kind_singular
            )
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
