"""Shared low-confidence verdict helpers for ranked-result commands +
R22 ``{value, confidence, reason}`` finding-triple helpers.

Background — finding #7 (low-confidence verdicts):

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

Low-confidence-verdict API:

* :func:`verdict_prefix` — prepend ``"low confidence — "`` to a base
  verdict when the result is low-confidence. Used by ``roam retrieve``.
* :func:`format_no_match` — emit a full ``"VERDICT: no confident X match"``
  + closest-matches block. Used by ``roam ask``.

Both helpers accept the same threshold parameter so commands can tune
the floor independently. The default 0.15 was empirically validated
in the v12.3 dogfood loop (see :mod:`roam.commands.cmd_ask`).

----------------------------------------------------------------------

R22 finding-triple API (NEW):

    Wrap every list-of-findings entry in a ``{value, confidence,
    reason}`` triple so agents can weight signals at consumption time
    rather than having to re-derive a confidence label from raw
    metrics. Pilot scope: five commands (`smells`, `clones`, `vulns`,
    `orphan-imports`, `complexity`); full sweep follows.

Shape::

    {
      "findings": [
        {
          "value": { ...existing flat finding dict... },
          "confidence": "high" | "medium" | "low",
          "reason": "528 callers indexed; threshold 100"
        },
        ...
      ]
    }

Public helpers:

* :func:`triple` — build a single triple dict.
* :func:`wrap_findings` — wrap a flat list using a per-finding
  ``classifier(finding) -> (confidence, reason)`` callable, or fall
  back to a constant.
* :func:`confidence_distribution` — return ``{"high": N, "medium": M,
  "low": K}`` from a list of triples; for the ``summary.findings_
  confidence_distribution`` envelope field.

Migration recipe — apply the following 4 steps to a target command:

    1. Build each finding dict as you did before, then call
       ``wrap_findings(flat, classifier=my_classifier)`` to produce
       triples; replace the flat list in the envelope payload with
       the triples list.
    2. Add a ``summary.findings_confidence_distribution`` field
       computed via :func:`confidence_distribution`.
    3. Update the verdict line to mention the high-confidence count
       (e.g. ``"23 findings (12 high-confidence)"``).
    4. Update any existing test that reads ``findings[0]["symbol"]``
       to read ``findings[0]["value"]["symbol"]`` and assert that
       ``findings[0]["confidence"]`` is one of ``{high, medium, low}``.

Consumer migration — readers of the new shape:

    OLD: finding["symbol"], finding["severity"]
    NEW: finding["value"]["symbol"], finding["value"]["severity"]
         + finding["confidence"]   ("high" | "medium" | "low")
         + finding["reason"]       (one-line human-readable string)

Why we did this — R22 motivation:

    Agents consuming roam output today see a flat list of findings
    with no signal weighting. A 528-caller-blast-radius finding is
    treated the same as a stale-references-might-be-dead finding,
    even though one is high-signal and the other is heuristic. The
    triple lets agents skip or caveat low-confidence rows without
    having to reproduce roam's internal scoring logic.

    The pilot also surfaces — per-command — the *confidence
    derivation rule* (in the classifier callable's docstring), which
    becomes documentation for future maintainers and a contract for
    consumers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

DEFAULT_CONFIDENCE_THRESHOLD = 0.15

# Valid confidence levels (closed enumeration — agents can rely on this).
CONFIDENCE_LEVELS: tuple[str, str, str] = ("high", "medium", "low")


# ---------------------------------------------------------------------------
# W596 — Canonical confidence-LEVEL rank.
#
# Pre-W596 the contract was implicit: 14 distinct rank tables hand-rolled
# across commands/, world_model/causal_graph and laws/miner. Two polarity
# camps: 7 sites with ``{high:3, medium:2, low:1}`` (higher=more-confident
# sort keys), 7 sites with ``{high:0, medium:1, low:2}`` (lower=better
# filter-order). Same Pattern-3a metric-divergence symptom as W547 / W564
# but on the confidence-LEVEL axis instead of severity.
#
# This is the single source of truth for ORDER on the confidence-LEVEL
# axis. Polarity: **higher = more confident** (matches the natural
# "high > medium > low" reading; sort callers that want lower=better
# pass ``-confidence_level_rank(x)`` as the sort key).
#
# NOTE on vocabulary. This helper operates on the *confidence-LEVEL*
# axis (``high``/``medium``/``low``) NOT the *confidence-TIER* axis
# (``heuristic``/``structural``/``static_analysis``/``runtime``)
# declared in :mod:`roam.db.findings`. See the long-form note in
# :mod:`roam.output._severity` (the W565 section) for why these are
# independent axes — a structural-TIER detector can emit a medium-LEVEL
# finding, and the findings-registry row carries TIER on a separate
# column from whatever per-finding LEVEL the ranker assigns.
#
# Rank values are chosen so:
#   * canonical CONFIDENCE_LEVELS map to ``high=3 / medium=2 / low=1``;
#   * ``unknown`` (the cmd_pr_bundle alias for "missing confidence
#     signal") collapses to ``0`` so it sorts below every defined
#     level — same W531 CI-safety lesson as severity_rank;
#   * any other label / ``None`` collapses to ``-1`` so a typo can
#     never accidentally promote a finding into a CI-gating rank.
_CONFIDENCE_LEVEL_RANK: dict[str, int] = {
    "high": 3,
    "medium": 2,
    "low": 1,
    # ``unknown`` is a real label — cmd_pr_bundle emits it when the
    # world-model classifier returns no signal — but ranks BELOW every
    # defined level. NOT ``-1``: pr_bundle's pre-W596 table mapped it
    # to ``0`` to keep it inside the sortable range, and downstream
    # consumers depend on that polarity (``min(...)`` over a confidence
    # tuple).
    "unknown": 0,
}


def confidence_level_rank(level: str | None, *, fallback: int | None = None) -> int:
    """Canonical rank for a confidence-LEVEL label (higher = more confident).

    Single source of truth for confidence-LEVEL ORDER across commands.
    Returns an integer suitable for direct comparison or as a ``sorted``
    key — use ``-confidence_level_rank(x)`` for the inverse polarity
    (which several pre-W596 sites used as a "high-first" filter order).

    The vocabulary is the closed :data:`CONFIDENCE_LEVELS` triple
    (``high`` / ``medium`` / ``low``) plus the pr_bundle ``unknown``
    sentinel that the world-model classifier emits when no signal is
    available.

    W634 — fail-loud by default. Unknown labels and ``None`` raise
    :class:`ValueError` so a programmer-class typo (``"hihg"``,
    ``"HIGH-CONFIDENCE"``) surfaces at the call site rather than
    silently bucketing into a default rank that could promote or demote
    a finding through a CI gate. Data-path callers that genuinely want
    silent bucketing on classifier-drift / unknown labels MUST opt in
    explicitly via ``fallback=<int>``; the canonical opt-in value is
    ``fallback=-1`` (matches the pre-W634 silent default), which sorts
    below every known level (the W531 CI-safety lesson).

    Examples
    --------
    >>> confidence_level_rank("high")
    3
    >>> confidence_level_rank("HIGH")
    3
    >>> confidence_level_rank("medium")
    2
    >>> confidence_level_rank("low")
    1
    >>> confidence_level_rank("unknown")
    0
    >>> confidence_level_rank("bogus", fallback=-1)
    -1
    >>> confidence_level_rank(None, fallback=-1)
    -1
    >>> confidence_level_rank("bogus")
    Traceback (most recent call last):
        ...
    ValueError: unknown confidence level: 'bogus'
    """
    if not level:
        if fallback is None:
            raise ValueError(f"unknown confidence level: {level!r}")
        return fallback
    s = str(level).strip().lower()
    rank = _CONFIDENCE_LEVEL_RANK.get(s)
    if rank is None:
        if fallback is None:
            raise ValueError(f"unknown confidence level: {level!r}")
        return fallback
    return rank


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


# ----------------------------------------------------------------------------
# R22: {value, confidence, reason} finding-triple helpers
# ----------------------------------------------------------------------------


def triple(value: Any, confidence: str, reason: str) -> dict:
    """Build a single ``{value, confidence, reason}`` finding triple.

    Parameters
    ----------
    value:
        The original flat finding dict (or any JSON-serialisable
        payload). Consumers access fields via ``triple["value"]["..."]``.
    confidence:
        One of :data:`CONFIDENCE_LEVELS`. Anything else is coerced to
        ``"medium"`` so a bad classifier can never break the schema.
    reason:
        Short human-readable explanation of why this confidence was
        assigned. Should reference the specific signal/threshold used
        (e.g. ``"528 callers indexed; threshold 100"``). Empty string
        is allowed but discouraged.
    """
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "medium"
    return {"value": value, "confidence": confidence, "reason": reason or ""}


def wrap_findings(
    findings: list[Any],
    *,
    classifier: Callable[[Any], tuple[str, str]] | None = None,
    default_confidence: str = "medium",
    default_reason: str = "",
) -> list[dict]:
    """Wrap a flat list of findings in the triple format.

    Parameters
    ----------
    findings:
        Flat list — typically each entry is a dict, but any
        JSON-serialisable value works.
    classifier:
        Optional ``finding -> (confidence, reason)`` callable. When
        provided, each finding is fed through it to derive its
        confidence label and reason string. When omitted, every
        finding gets ``default_confidence`` / ``default_reason``.
        Classifiers that raise are caught and the finding falls back
        to ``(default_confidence, default_reason)`` — better degraded
        output than a 500.
    default_confidence:
        Used when *classifier* is None or raises. Must be one of
        :data:`CONFIDENCE_LEVELS`.
    default_reason:
        Reason text paired with the default confidence fallback.

    Returns
    -------
    list[dict]
        One ``{value, confidence, reason}`` triple per input finding,
        same order as the input.
    """
    out: list[dict] = []
    for f in findings:
        if classifier is None:
            out.append(triple(f, default_confidence, default_reason))
            continue
        try:
            conf, reason = classifier(f)
        except Exception:
            conf, reason = default_confidence, default_reason
        out.append(triple(f, conf, reason))
    return out


def confidence_distribution(triples: list[dict]) -> dict[str, int]:
    """Count triples by confidence level.

    Always returns a dict with all three keys present (so consumers
    don't have to check ``.get(..., 0)`` for missing buckets). Triples
    with an unexpected confidence value are bucketed as ``"medium"``,
    matching the coercion in :func:`triple`.
    """
    buckets: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for t in triples:
        c = t.get("confidence") if isinstance(t, dict) else None
        if c in buckets:
            buckets[c] += 1
        else:
            buckets["medium"] += 1
    return buckets


def verdict_with_high_count(base: str, distribution: dict[str, int]) -> str:
    """Append a ``"(N high-confidence)"`` suffix when N>0.

    Idempotent and noisy-only-when-useful: when the high bucket is
    zero we return *base* unchanged so commands with no findings or
    only low-confidence findings keep their original verdict line.
    """
    high = distribution.get("high", 0) if distribution else 0
    if high <= 0:
        return base
    return f"{base} ({high} high-confidence)"
