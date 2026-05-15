"""Canonical risk-LEVEL vocabulary + rank helper (W631).

Background — Pattern-3a structural close-out, third axis:

    W547 + W564 canonicalised the **severity** rank table; W596
    canonicalised the **confidence-LEVEL** rank table. A drive-by
    audit during W596 surfaced a third axis still drifting:
    risk-LEVEL ranks owned per-command.

    Confirmed pre-W631 sites:

    * ``cmd_migration_plan.py:107`` — 3-tier lower-case
      ``{"low": 0, "medium": 1, "high": 2}`` (polarity: lower=safer,
      used to gate the plan by ``--max-risk`` and to sort plan steps
      with low-risk first).
    * ``cmd_path_coverage.py:453`` — 4-tier UPPER-cased
      ``{"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}`` (polarity:
      lower=worse, used to sort classified paths so CRITICAL fires
      first).

    Same Pattern-3a metric-divergence symptom as W547 / W564 / W596:
    each command picks its own vocab, polarity, casing, and
    membership — agents comparing "is risk X worse than Y?" across
    commands get inconsistent answers depending on which command
    emitted the row.

This module is the single source of truth for ORDER on the
risk-LEVEL axis. Polarity: **higher = worse** (matches
:func:`roam.output._severity.severity_rank`). Callers that want
the lower=better polarity (e.g. migration_plan's
"low-risk first" sort + ``--max-risk`` gate) pass
``-risk_rank(x)`` as the sort key, exactly like the W596
pattern.

Vocabulary
----------

* :data:`RISK_LEVELS` — closed canonical lowercase 4-tier
  (``critical`` / ``high`` / ``medium`` / ``low``).
* :data:`RISK_ALIASES` — historic spellings that resolve into the
  canonical set. ``moderate`` -> ``medium`` (the pr_risk
  ``LOW/MODERATE/HIGH/CRITICAL`` vocabulary used by the W134
  composite-risk severity mapping); plus UPPER-case ingest is
  handled transparently by :func:`normalize_risk_level` (no entry
  needed in the alias table — :func:`str.lower` covers it).

NOTE on vocabulary axes. This helper operates on the *risk-LEVEL*
axis — what a workflow command says about the risk of a proposed
change or path. It is INDEPENDENT from:

* the severity-LEVEL axis canonicalised by
  :mod:`roam.output._severity` (a finding's intrinsic severity),
* the confidence-LEVEL axis canonicalised by
  :mod:`roam.output.confidence` (how confident the detector is
  in the finding).

The three axes share overlapping vocabulary tokens (``high`` /
``medium`` / ``low``) but rank semantically different concepts.
A medium-severity finding from a high-confidence detector on a
low-risk symbol is a coherent state — three independent labels.

Rank values are chosen so:

* canonical levels map to ``critical=4 / high=3 / medium=2 /
  low=1`` (matches the W596 confidence_level_rank polarity step
  pattern);
* any other label / ``None`` collapses to ``-1`` (the W531
  CI-safety lesson: a typo'd or absent risk label must NOT
  accidentally promote a step into a CI-gating rank, and must
  NOT accidentally pass a ``--max-risk`` filter).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical vocabulary
# ---------------------------------------------------------------------------

# Closed canonical risk-LEVEL vocabulary. Lowercase — every consumer
# must normalise before comparing. Membership is O(1) via frozenset.
RISK_LEVELS: frozenset[str] = frozenset({
    "critical",
    "high",
    "medium",
    "low",
})


# Historic spellings that resolve into the canonical set. UPPER-case
# variants do NOT need entries — :func:`normalize_risk_level` lowercases
# input before lookup.
RISK_ALIASES: dict[str, str] = {
    # pr_risk's W134 composite-risk vocabulary spells the mid-tier
    # ``MODERATE``; resolve into the canonical ``medium``.
    "moderate": "medium",
}


# W631 — Canonical risk-LEVEL rank.
#
# Polarity: **higher = worse**. Sort callers that want
# "low-risk first" (the cmd_migration_plan polarity) pass
# ``-risk_rank(x)`` as the sort key — same recipe as W596.
_RISK_RANK: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_risk_level(level: str | None) -> str | None:
    """Return *level* canonicalised against :data:`RISK_LEVELS`.

    Lowercase + strip + alias-resolve. Returns ``None`` for empty /
    ``None`` / unknown labels — callers that need a non-``None``
    floor pass the returned value through ``or "low"``.

    The ``None``-on-unknown polarity is intentional: it lets
    :func:`risk_rank` distinguish "unknown" (rank -1, CI-safe) from
    a defined level. Consumers that want a forgiving canonicalisation
    pass the result through ``or default``.

    Examples
    --------
    >>> normalize_risk_level("CRITICAL")
    'critical'
    >>> normalize_risk_level("HIGH")
    'high'
    >>> normalize_risk_level("Moderate")
    'medium'
    >>> normalize_risk_level("  Low  ")
    'low'
    >>> normalize_risk_level(None) is None
    True
    >>> normalize_risk_level("bogus") is None
    True
    """
    if not level:
        return None
    s = str(level).strip().lower()
    if s in RISK_LEVELS:
        return s
    if s in RISK_ALIASES:
        return RISK_ALIASES[s]
    return None


def risk_rank(level: str | None) -> int:
    """Canonical rank for a risk-LEVEL label (higher = worse).

    Single source of truth for risk-LEVEL ORDER across commands.
    Returns an integer suitable for direct comparison or as a
    ``sorted`` key — use ``-risk_rank(x)`` for the inverse
    polarity (which cmd_migration_plan + cmd_path_coverage both
    used pre-W631 as "lower = process first / safer / less
    severe").

    The vocabulary is the closed :data:`RISK_LEVELS` 4-tuple
    (``critical`` / ``high`` / ``medium`` / ``low``) plus the
    ``moderate`` alias from :data:`RISK_ALIASES`. Unknown labels
    and ``None`` collapse to ``-1`` so they sort below every
    known level (the W531 CI-safety lesson — a typo'd label
    must NOT promote a step into a CI-gating rank).

    Examples
    --------
    >>> risk_rank("critical")
    4
    >>> risk_rank("CRITICAL")
    4
    >>> risk_rank("high")
    3
    >>> risk_rank("MODERATE")
    2
    >>> risk_rank("medium")
    2
    >>> risk_rank("low")
    1
    >>> risk_rank("bogus")
    -1
    >>> risk_rank(None)
    -1
    """
    canonical = normalize_risk_level(level)
    if canonical is None:
        return -1
    return _RISK_RANK.get(canonical, -1)
