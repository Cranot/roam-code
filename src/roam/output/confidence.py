"""Shared low-confidence verdict helpers for ranked-result commands.

Background — redacted finding #7:

    `roam ask` correctly says "VERDICT: no confident recipe match" when
    the top recipe scores below ~0.15. `roam retrieve` had no equivalent
    signal until iter-5 — agents would chase the top result of a
    foreign-concept query, wasting turns on a red herring. Each command
    invented its own threshold and verdict format. Same pattern, three
    inconsistent shapes.

This module centralises the pattern so future commands (oracles,
diagnose, semantic-search) inherit one consistent low-confidence
verdict line. The helpers are intentionally small — each command keeps
its own scoring logic, but the *output shape* lives here.

Public API:

* :func:`verdict_prefix` — prepend ``"low confidence — "`` to a base
  verdict when the result is low-confidence. Used by ``roam retrieve``.
* :func:`format_no_match` — emit a full ``"VERDICT: no confident X match"``
  + closest-matches block. Used by ``roam ask``.

Both helpers accept the same threshold parameter so commands can tune
the floor independently. The default 0.15 was empirically validated
in the v12.3 dogfood loop (see :mod:`roam.commands.cmd_ask`).
"""

from __future__ import annotations

DEFAULT_CONFIDENCE_THRESHOLD = 0.15


def is_low_confidence(top_score: float, threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> bool:
    """Return True when *top_score* indicates the answer is junk.

    Score-only check. Commands that need richer signals (token coverage,
    score gap, multi-token spread) should layer those in their own
    classifier and call this function as a final cross-check.
    """
    return top_score < threshold


def verdict_prefix(base_verdict: str, low_confidence: bool, *, label: str = "low confidence") -> str:
    """Prepend a confidence tag to *base_verdict*.

    >>> verdict_prefix("20 spans", True)
    'low confidence — 20 spans'
    >>> verdict_prefix("20 spans", False)
    '20 spans'
    """
    if not low_confidence:
        return base_verdict
    return f"{label} — {base_verdict}"


def format_no_match(
    kind: str,
    candidates: list[tuple[str, float, str]] | None = None,
    *,
    limit: int = 3,
    hint_template: str = "try `--{flag} <name>` to force one",
    flag: str = "recipe",
) -> str:
    """Format a "no confident X match" block for text output.

    *candidates* is a list of ``(name, score, intent)`` tuples sorted
    descending by score. Returns a multi-line string ready for
    ``click.echo``. If *candidates* is empty/None the block is just the
    verdict line.

    >>> print(format_no_match("recipe", [("verify-patch", 0.07, "Audit a patch")]))
    VERDICT: no confident recipe match
    <BLANKLINE>
    Closest matches (try `--recipe <name>` to force one):
      [0.07] verify-patch — Audit a patch
    """
    lines = [f"VERDICT: no confident {kind} match"]
    if not candidates:
        return "\n".join(lines)
    lines.append("")
    lines.append(f"Closest matches ({hint_template.format(flag=flag)}):")
    for name, score, intent in candidates[:limit]:
        lines.append(f"  [{score:.2f}] {name} — {intent}")
    return "\n".join(lines)
