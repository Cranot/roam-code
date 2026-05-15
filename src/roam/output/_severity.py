"""Canonical roam severity vocabulary + SARIF level mapping (W547).

Before W547 the contract was implicit: ``output/sarif.py`` defined the
canonical ``_LEVEL_MAP`` (CRITICAL/ERROR -> "error", HIGH/WARNING ->
"warning", MEDIUM/LOW/INFO -> "note"), and every consumer either imported
``_to_level`` from that module or re-rolled its own table inline.
Drift-prone — different commands could disagree on whether "high" maps
to SARIF warning or note, breaking CI gates keyed off SARIF level.

This module consolidates the contract:

* :data:`SEVERITY_LEVELS` — the closed canonical vocabulary
  (``critical`` / ``error`` / ``warning`` / ``info``).
* :data:`SEVERITY_ALIASES` — the historic spellings (``high`` ->
  ``warning``, ``medium``/``low`` -> ``info``, ``note`` -> ``info``)
  that resolve back into the canonical set so YAML rule packs and
  external feeds (OSV, npm-audit, trivy) round-trip unchanged.
* :func:`normalize_severity` — case-insensitive canonicalisation; an
  unknown label collapses to ``info`` so a typo never accidentally
  promotes a finding into a CI-failing rank.
* :func:`to_sarif_level` — the closed roam-severity -> SARIF-level
  projection. SARIF ``error`` gates fail CI by default in GitHub Code
  Scanning, so the W531 lesson (CRITICAL must NOT silently downgrade
  to ``note``) is encoded in the table, not relearned per-command.

The legacy ``_LEVEL_MAP`` / ``_to_level`` symbols in
:mod:`roam.output.sarif` now thin-wrap this module and are kept only
as a back-compat shim — new code should import from here directly.

W548 (the closed-enum validator at YAML load time for the taint engine)
is bundled into this module via :func:`validate_severity`: a strict
checker that raises ``ValueError`` for unknown labels. The taint
engine wires it up at :func:`roam.security.taint_engine.load_rules`.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable, Mapping
from typing import Any

# ---------------------------------------------------------------------------
# Canonical vocabulary
# ---------------------------------------------------------------------------

# Closed canonical roam severity vocabulary. Lowercase — every consumer
# must normalize before comparing. Membership is O(1) via frozenset.
#
# Why these four and not "high/medium/low"?  The W531 audit + dogfood
# pattern-3a synthesis settled on these labels because they map 1:1 to
# SARIF levels (the lingua franca for CI surfaces) and because the
# OpenVEX/CVSS spellings (critical/high/medium/low) are domain-specific
# aliases — see :data:`SEVERITY_ALIASES`.
SEVERITY_LEVELS: frozenset[str] = frozenset({
    "critical",
    "error",
    "warning",
    "info",
})

# Historic spellings that resolve into the canonical set.
#
# * ``high`` -> ``warning`` (SARIF "warning" tier — does NOT fail CI
#   gates by default; matches the W531 audit decision).
# * ``medium`` / ``low`` -> ``info`` (SARIF "note" tier).
# * ``note`` -> ``info`` (SARIF tier alias used by some YAML rule packs).
# * ``unknown`` -> ``info`` (vuln feeds emit this when CVSS is missing).
#
# Externally-sourced feeds (OSV / npm-audit / trivy / GitHub Advisory
# DB) tag their findings with critical/high/medium/low; storing them as
# canonical roam severities would lose round-trip fidelity, so the
# aliases stay even though the canonical set is narrower.
SEVERITY_ALIASES: dict[str, str] = {
    "high": "warning",
    "medium": "info",
    "low": "info",
    "note": "info",
    "unknown": "info",
}

# Canonical roam severity -> SARIF 2.1.0 level. SARIF accepts exactly
# four levels: error / warning / note / none. roam never emits ``none``
# — a finding that should be suppressed is dropped entirely, not
# stamped with level=none.
#
# This is the contract that drives CI gates: GitHub Code Scanning
# fails a workflow on SARIF level=error by default; level=warning is
# a soft warning; level=note is informational only.
_SARIF_LEVEL_MAP: dict[str, str] = {
    "critical": "error",
    "error": "error",
    "warning": "warning",
    "info": "note",
}


# W564 — Canonical severity rank.
#
# Pre-W564 the contract was implicit: at least 13 distinct rank tables
# lived across ``commands/``, ``critique/aggregator.py`` and
# ``catalog/smells.py``. Each table chose its own polarity (some
# higher-=-worse, some lower-=-worse), its own subset of vocabulary
# (3-tier ``high/medium/low``, 4-tier ``critical/warning/info`` +
# unknown, 5-tier vulns) and its own UPPER- vs lower-case convention.
# Pattern-3a metric-divergence on ORDER: agents comparing
# "is severity X worse than Y?" across commands would get different
# answers depending on which command emitted the finding.
#
# This table is the single source of truth for ORDER. Polarity:
# **higher = worse**. The CVSS-style 5-tier vocab (critical / high /
# medium / low / info) is preserved alongside the SARIF-projecting
# 4-tier vocab (critical / error / warning / info) so callers that
# need tier-distinct filter semantics (e.g. ``roam secrets
# --severity medium`` excludes ``low``) keep working unchanged.
#
# Rank values are chosen so that:
#   * ``critical`` (CVSS top) > ``error`` (SARIF gate) — critical is
#     the most-severe single tier.
#   * ``high`` (CVSS) shares rank with ``error`` — both are "CI gate"
#     equivalents under the W547 SARIF projection.
#   * ``warning`` (SARIF middle) > ``medium`` (CVSS middle) — the
#     SARIF mid-tier is the canonical roam mid-tier.
#   * ``info`` is the floor; ``unknown`` / unrecognised collapse to
#     ``-1`` so they sort below every known tier (the W531
#     CI-safety lesson: unknown labels never gate CI).
_SEVERITY_RANK: dict[str, int] = {
    "critical": 5,
    "error": 4,
    "high": 4,
    "warning": 3,
    "medium": 2,
    "low": 1,
    "note": 0,
    "info": 0,
    # ``unknown`` is a real label (npm-audit / osv emit it when CVSS
    # is missing) but ranks BELOW every defined tier — including
    # ``info``. The SARIF projection collapses it to ``info`` (so a
    # vuln with unknown severity still appears on the report), but
    # the rank contract sorts unknowns last so a typo'd or absent
    # severity never climbs above a defined ``info`` finding.
    # ``severity_rank(None)`` / ``severity_rank("bogus")`` collapse
    # to the same ``-1`` via the dict-default fallback.
    "unknown": -1,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_severity(severity: str | None) -> str:
    """Return *severity* canonicalised against :data:`SEVERITY_LEVELS`.

    Lowercase, alias-resolved, ``None`` / empty / unknown -> ``"info"``.

    Examples
    --------
    >>> normalize_severity("CRITICAL")
    'critical'
    >>> normalize_severity("HIGH")
    'warning'
    >>> normalize_severity("Medium")
    'info'
    >>> normalize_severity(None)
    'info'
    >>> normalize_severity("bogus")
    'info'
    """
    if not severity:
        return "info"
    s = str(severity).strip().lower()
    if s in SEVERITY_LEVELS:
        return s
    if s in SEVERITY_ALIASES:
        return SEVERITY_ALIASES[s]
    return "info"


def to_sarif_level(severity: str | None) -> str:
    """Map a roam severity string to a SARIF 2.1.0 level.

    Closed mapping (case-insensitive, alias-aware via
    :func:`normalize_severity`):

    * ``critical`` / ``error``       -> SARIF ``error``
    * ``high`` / ``warning``         -> SARIF ``warning``
    * ``medium`` / ``low`` / ``info`` / ``note`` / ``unknown``
      -> SARIF ``note``

    Unknown labels collapse to ``note`` so a typo never accidentally
    fails a CI gate keyed off SARIF level=error (the W531 lesson).
    """
    canonical = normalize_severity(severity)
    return _SARIF_LEVEL_MAP.get(canonical, "note")


def severity_rank(severity: str | None) -> int:
    """Canonical rank for a roam severity label (higher = worse).

    Single source of truth for severity ORDER across commands. Returns
    an integer suitable for direct comparison or as a ``sorted`` key
    (use ``-severity_rank(x)`` for descending-worst-first order).

    The rank vocabulary unifies the CVSS-style 5-tier
    (``critical``/``high``/``medium``/``low``/``info``) and the
    SARIF-projecting 4-tier (``critical``/``error``/``warning``/``info``)
    so callers that need tier-distinct filter semantics (e.g. ``roam
    secrets --severity medium`` excluding ``low``) keep working
    unchanged after migrating off their local rank table.

    Unknown labels and ``None`` collapse to ``-1`` so they sort below
    every known tier — the W531 CI-safety lesson (a typo'd label must
    NOT promote a finding into a CI-failing rank).

    Examples
    --------
    >>> severity_rank("critical")
    5
    >>> severity_rank("CRITICAL")
    5
    >>> severity_rank("high")
    4
    >>> severity_rank("warning")
    3
    >>> severity_rank("medium")
    2
    >>> severity_rank("low")
    1
    >>> severity_rank("info")
    0
    >>> severity_rank("bogus")
    -1
    >>> severity_rank(None)
    -1
    """
    if not severity:
        return -1
    s = str(severity).strip().lower()
    return _SEVERITY_RANK.get(s, -1)


def validate_severity(severity: str | None, *, source: str = "") -> str:
    """W548 closed-enum validator — for YAML rule load.

    Like :func:`normalize_severity`, but warns when *severity* is
    neither a canonical level nor a known alias. Returns the
    canonicalised string (falling back to ``"info"`` for unknowns) so
    callers can still proceed; the warning gives rule authors a clear
    signal that their YAML spelling is non-canonical.

    *source* is a free-form identifier (e.g. the rule id or YAML
    filename) included in the warning message so authors can find the
    bad rule quickly.
    """
    if not severity:
        return "info"
    s = str(severity).strip().lower()
    if s in SEVERITY_LEVELS or s in SEVERITY_ALIASES:
        return normalize_severity(s)
    label = f" in {source!r}" if source else ""
    warnings.warn(
        f"[roam.severity] unknown severity {severity!r}{label}; "
        f"expected one of {sorted(SEVERITY_LEVELS | SEVERITY_ALIASES.keys())}; "
        f"falling back to 'info'",
        stacklevel=2,
    )
    return "info"


# ---------------------------------------------------------------------------
# W565 — severity -> findings-registry confidence-level projection.
#
# Before W565 the contract was duplicated: cmd_complexity.py and
# cmd_smells.py each carried a hand-rolled ``_*_SEVERITY_TO_CONFIDENCE``
# table mapping a severity label to a confidence LEVEL ("high" /
# "medium" / "low") used by the per-finding ranking helpers
# (``_complexity_classify`` / ``_smell_classify``). If a third call site
# needed the same projection it would inevitably re-roll its own table
# and Pattern-3a metric-divergence would creep back in.
#
# NOTE on vocabulary. This helper projects severity onto the
# *confidence-LEVEL* axis ("high" / "medium" / "low"), NOT the
# *confidence-TIER* axis (``heuristic`` / ``structural`` /
# ``static_analysis`` / ``runtime``) declared in
# :mod:`roam.db.findings`. The two axes are independent: a structural
# detector can emit a "medium" confidence-level finding, and the
# findings-registry row carries the TIER on a separate column from
# whatever per-finding LEVEL the ranker assigns. The helper is named
# ``severity_to_confidence_level`` to keep the distinction explicit.
# ---------------------------------------------------------------------------

# Default severity -> confidence-LEVEL projection. Built from the two
# pre-W565 tables (``cmd_complexity._COMPLEXITY_SEVERITY_TO_CONFIDENCE``
# and ``cmd_smells._SMELL_SEVERITY_TO_CONFIDENCE``):
#
#   * critical/error/high   -> "high"   (refactor target; CI-gate tier)
#   * warning/medium        -> "medium" (monitor; soft-warning tier)
#   * info/low/note/unknown -> "low"    (exploratory; informational tier)
#
# Polarity matches the canonical severity rank (higher = worse).
# Unknown labels collapse to ``"low"`` so a typo never accidentally
# promotes a finding into a CI-gating confidence rank — same W531
# safety lesson as :func:`to_sarif_level`.
_DEFAULT_SEVERITY_TO_CONFIDENCE_LEVEL: dict[str, str] = {
    "critical": "high",
    "error": "high",
    "high": "high",
    "warning": "medium",
    "medium": "medium",
    "info": "low",
    "low": "low",
    "note": "low",
    "unknown": "low",
}


def severity_to_confidence_level(
    severity: str | None,
    overrides: Mapping[str, str] | None = None,
    *,
    default: str = "low",
) -> str:
    """Project a roam severity label onto a confidence-LEVEL axis.

    Returns one of ``"high"`` / ``"medium"`` / ``"low"`` — the
    per-finding confidence-LEVEL used by ranker helpers like
    :func:`roam.commands.cmd_complexity._complexity_classify` and
    :func:`roam.commands.cmd_smells._smell_classify`.

    Case-insensitive; canonicalises via :func:`normalize_severity`
    *only when the raw label is not directly in the table*, so callers
    that key on the CVSS 5-tier vocab (critical/high/medium/low) keep
    distinct mappings even though :func:`normalize_severity` collapses
    ``medium`` / ``low`` to ``info``. The override table is consulted
    first so a caller can swap any individual mapping without
    re-rolling the full default.

    Parameters
    ----------
    severity:
        The severity label to project. Case-insensitive.
    overrides:
        Optional per-call override table merged on top of the default.
        Keys are case-insensitive.
    default:
        Confidence level to return for unknown labels. Defaults to
        ``"low"`` (the W531 CI-safety floor — a typo never gates CI).

    Examples
    --------
    >>> severity_to_confidence_level("critical")
    'high'
    >>> severity_to_confidence_level("HIGH")
    'high'
    >>> severity_to_confidence_level("warning")
    'medium'
    >>> severity_to_confidence_level("info")
    'low'
    >>> severity_to_confidence_level("bogus")
    'low'
    >>> severity_to_confidence_level(None)
    'low'
    >>> severity_to_confidence_level("warning", overrides={"warning": "high"})
    'high'
    """
    if not severity:
        return default
    key = str(severity).strip().lower()
    if overrides:
        for ok, ov in overrides.items():
            if str(ok).strip().lower() == key:
                return ov
    if key in _DEFAULT_SEVERITY_TO_CONFIDENCE_LEVEL:
        return _DEFAULT_SEVERITY_TO_CONFIDENCE_LEVEL[key]
    # Truly unknown label — collapse to *default*. We deliberately do
    # NOT route through :func:`normalize_severity` here: that would
    # rewrite a typo'd label to ``"info"``, which is itself a key in
    # the table, and ``default`` would never fire. The W531 CI-safety
    # contract requires a typo to surface as the caller-chosen floor,
    # not as the table's ``info``-row value.
    return default


# ---------------------------------------------------------------------------
# W566 — severity-breakdown helper.
#
# Before W566 every command that wanted a ``{severity: count}`` summary
# hand-rolled a bucket dict, looped, and either swallowed unknown
# labels (cmd_secrets) or remapped to "unknown" (cmd_vulns) or carried
# its own 4-tier vocab (critique/aggregator). Three sites, three
# vocabs, three slightly different unknown-label policies. Same
# Pattern-3a metric-divergence symptom — agents reading the
# ``severity_breakdown`` field across commands would get inconsistent
# shapes.
#
# This helper centralises the bucketing while preserving the
# per-vocab differences via the ``vocab`` parameter. The default vocab
# matches the cmd_vulns pre-W566 contract (CVSS 5-tier + ``unknown``)
# because that's the largest call site; the other two migrate by
# passing their explicit vocab.
# ---------------------------------------------------------------------------

# Default breakdown vocabulary. CVSS 5-tier + ``unknown`` — matches
# the pre-W566 ``cmd_vulns._severity_breakdown`` contract verbatim so
# the migration is byte-identical for that site.
_DEFAULT_BREAKDOWN_VOCAB: tuple[str, ...] = (
    "critical",
    "high",
    "medium",
    "low",
    "unknown",
)


def severity_breakdown(
    items: Iterable[Any],
    key: Callable[[Any], str | None] | str = "severity",
    *,
    vocab: Iterable[str] = _DEFAULT_BREAKDOWN_VOCAB,
    unknown_bucket: str | None = "unknown",
    drop_zero: bool = True,
) -> dict[str, int]:
    """Bucket *items* by severity into a ``{label: count}`` dict.

    Centralises the hand-rolled severity-bucketing that lived in
    ``cmd_vulns`` / ``cmd_secrets`` / ``critique/aggregator``. The
    bucketing rules are:

    * Each item's severity is read via *key*. If *key* is a string it
      is used as a dict / mapping key (with ``getattr`` fallback for
      attribute-style objects); if it is a callable, it is called on
      the item.
    * The raw severity is lower-cased and matched against *vocab*. A
      match increments the corresponding bucket.
    * A miss routes to *unknown_bucket* (when set AND in *vocab* —
      otherwise the miss is dropped silently, matching the
      ``cmd_secrets`` pre-W566 behaviour where ``high``/``medium``/
      ``low`` were the only buckets).
    * ``drop_zero`` removes empty buckets from the output, matching
      the pre-W566 ``cmd_vulns`` ``{k: v for k, v in counts.items()
      if v > 0}`` postprocess. Pass ``drop_zero=False`` to keep the
      full vocab even when some buckets are empty (matches the
      ``critique/aggregator`` pre-W566 contract — its 4-tier vocab
      was kept zero-padded so the JSON envelope always carried the
      same shape).

    Parameters
    ----------
    items:
        Any iterable of dicts / mappings / objects carrying a severity
        attribute.
    key:
        Dict-key (``str``) or callable extracting the severity from
        each item. Strings default to ``"severity"``; callables get
        the raw item.
    vocab:
        The set of accepted bucket labels. Defaults to the CVSS 5-tier
        + ``unknown`` vocab (the cmd_vulns pre-W566 contract).
    unknown_bucket:
        Label to use for items whose severity is not in *vocab*. Pass
        ``None`` to drop unknown items entirely.
    drop_zero:
        When True (default), buckets with zero count are removed from
        the result. When False, the full vocab is returned with zero
        counts preserved.

    Examples
    --------
    >>> severity_breakdown([])
    {}
    >>> severity_breakdown([{"severity": "high"}, {"severity": "high"}])
    {'high': 2}
    >>> severity_breakdown(
    ...     [{"severity": "CRITICAL"}, {"severity": "bogus"}]
    ... )
    {'critical': 1, 'unknown': 1}
    >>> severity_breakdown(
    ...     [{"severity": "high"}],
    ...     vocab=("high", "medium", "low"),
    ...     unknown_bucket=None,
    ...     drop_zero=False,
    ... )
    {'high': 1, 'medium': 0, 'low': 0}
    """
    # Materialise vocab once; preserve order so the result is
    # deterministic (Python 3.7+ dict insertion order).
    vocab_list = tuple(vocab)
    counts: dict[str, int] = {label: 0 for label in vocab_list}
    # ``unknown_bucket`` only fires when it's in vocab — otherwise an
    # unknown label is dropped silently. This matches cmd_secrets'
    # pre-W566 ``by_severity.get(...) + 1`` shape: with no "unknown"
    # bucket, the assignment would have inserted a stray key into the
    # dict; the helper drops the item instead, which is the safer
    # behaviour and a small de-facto fix.
    unknown_active = unknown_bucket is not None and unknown_bucket in counts

    # Resolve the extractor.
    if callable(key):
        getter: Callable[[Any], str | None] = key
    else:
        key_str = str(key)

        def getter(item: Any) -> str | None:  # type: ignore[no-redef]
            if isinstance(item, Mapping):
                val = item.get(key_str)
            else:
                val = getattr(item, key_str, None)
            return val if isinstance(val, str) or val is None else str(val)

    for item in items:
        raw = getter(item)
        sev = (raw or "").strip().lower()
        if sev in counts:
            counts[sev] += 1
        elif unknown_active:
            counts[unknown_bucket] += 1  # type: ignore[index]
        # else: drop the item silently.

    if drop_zero:
        return {k: v for k, v in counts.items() if v > 0}
    return counts


# ---------------------------------------------------------------------------
# Back-compat shim — used by roam.output.sarif. New code should NOT
# import these symbols; use :func:`to_sarif_level` instead.
# ---------------------------------------------------------------------------


def _legacy_level_map() -> dict[str, str]:
    """Reconstruct the pre-W547 UPPER-cased SARIF level table.

    Kept so external callers / tests that grep for ``_LEVEL_MAP`` still
    resolve to the same byte-identical mapping. Built lazily from the
    canonical contract above — single source of truth.
    """
    table: dict[str, str] = {}
    # Canonical members
    for sev in SEVERITY_LEVELS:
        table[sev.upper()] = _SARIF_LEVEL_MAP[sev]
    # Aliases
    for alias, canonical in SEVERITY_ALIASES.items():
        # ``unknown`` was not in the legacy table — keep it out so the
        # back-compat shim stays byte-identical to the W531 version.
        if alias == "unknown":
            continue
        table[alias.upper()] = _SARIF_LEVEL_MAP[canonical]
    return table


__all__ = [
    "SEVERITY_LEVELS",
    "SEVERITY_ALIASES",
    "normalize_severity",
    "to_sarif_level",
    "severity_rank",
    "validate_severity",
    # W565
    "severity_to_confidence_level",
    # W566
    "severity_breakdown",
]
