"""W1077 — shared ``structured_unknown_filter`` helper.

The "validate against closed vocabulary + difflib closest-match + emit
unknown_X envelope" pattern has landed 5+ times in the same shape across
W1063 (``cmd_findings --detector``), W1068 (``cmd_search --kind``),
W1069 (``cmd_endpoints --framework``), W1070 (``cmd_test_scaffold
--framework``), and W1075 (``cmd_endpoints --method``). Each callsite is
~50 LOC of nearly-identical scaffolding.

This module centralizes the pure-logic core. The helper returns an
envelope-fragment ``dict`` (NOT a full ``json_envelope`` payload) when
the requested value is not in the known set, else ``None``. Callers
splice the fragment into their site-specific ``summary`` + ``agent_contract``
structures so per-callsite specialization (extra summary fields, custom
verdict prose, next_commands) stays at the callsite.

The fragment carries:

* ``state``                       — closed-enum disclosure state (e.g. ``unknown_detector``)
* ``partial_success``             — always ``True`` here (Pattern-1D disclosure)
* ``<requested_field>``           — echo of the user-supplied value
* ``<known_field>``               — the sorted closed set the caller validated against
* ``did_you_mean``                — difflib closest-match suggestions (possibly empty list).
                                    With ``did_you_mean_omit_when_empty=True`` (W1081)
                                    the field is OMITTED when no matches were found.
* ``facts``                       — LAW 4 concrete-noun-anchored fact strings
* ``verdict_suffix``              — pre-formatted " Did you mean: 'x'?" tail (empty when no match)

The caller composes the final verdict, calls ``json_envelope``, and emits.
Phase 1 (W1077) ships the helper UNUSED. Phase 2 (W1079) migrates the 5
existing callsites. W1081 refinement adds three kwargs
(``did_you_mean_omit_when_empty``, ``requested_disclosure_verb``,
``known_disclosure_label``) so callers can avoid post-helper patching.
"""

from __future__ import annotations

import difflib
from typing import Any, Iterable


def structured_unknown_filter(
    *,
    requested: str,
    known: Iterable[str],
    state: str,
    requested_field: str,
    known_field: str,
    fact_anchor: str,
    cutoff: float = 0.6,
    n_suggestions: int = 2,
    did_you_mean_omit_when_empty: bool = False,
    requested_disclosure_verb: str = "not in known",
    known_disclosure_label: str | None = None,
) -> dict[str, Any] | None:
    """Return an envelope-fragment ``dict`` when ``requested`` is not in
    ``known``; otherwise ``None``.

    Parameters
    ----------
    requested
        The user-supplied filter value (e.g. ``"garblargle"``).
    known
        The closed vocabulary to validate against. Iterated once; the
        helper sorts + dedupes internally so callers may pass a ``set``,
        ``list``, or generator.
    state
        Closed-enum disclosure state (e.g. ``"unknown_detector"``).
    requested_field
        Summary-field name that echoes the user input (e.g.
        ``"requested_detector"``).
    known_field
        Summary-field name listing the sorted closed set (e.g.
        ``"known_detectors"``).
    fact_anchor
        The LAW 4 concrete-noun terminal used in the ``facts`` list (e.g.
        ``"detectors"``, ``"kinds"``, ``"frameworks"``, ``"methods"``).
        Must be in the formatter's ``concrete_plural_terminals`` set AND
        the test lint's ``_CONCRETE_NOUN_ANCHORS`` set — see CLAUDE.md
        LAW 4.
    cutoff
        ``difflib.get_close_matches`` cutoff (default 0.6 — mirrors the
        cmd_math precedent and all 5 existing callsites).
    n_suggestions
        ``difflib.get_close_matches`` ``n=`` parameter (default 2).
    did_you_mean_omit_when_empty
        W1081: when ``True`` AND no close matches were found, OMIT the
        ``did_you_mean`` field from the returned fragment entirely
        (instead of returning an empty list). Lets callers splice the
        whole fragment into their summary without conditional logic.
        Default ``False`` preserves byte-identical pre-W1081 behavior.
    requested_disclosure_verb
        W1081: overrides the connector phrase used in the second
        LAW-4-anchored fact. Default ``"not in known"`` yields the
        pre-W1081 ``"'X' not in known Y"``. Pass e.g. ``"substring not
        in observed"`` to disclose substring-match semantics natively
        (``"'flask' substring not in observed frameworks"``).
    known_disclosure_label
        W1081: overrides the label tail (the ``"known {fact_anchor}"``
        suffix) in the second fact. Defaults to ``None`` which means
        ``"known {fact_anchor}"``. Pass e.g. ``"observed frameworks"``
        to render ``"'flask' substring not in observed frameworks"``.
        The terminal token of the override MUST stay a concrete-noun
        anchor (LAW 4) — callers are responsible.

    Returns
    -------
    ``dict`` envelope-fragment when the value is unknown, else ``None``.

    Notes
    -----
    Membership is checked against the sorted-deduped set; callers that
    accept BOTH a full-name and an abbreviation (e.g. ``cmd_search``
    accepts both ``"function"`` and ``"fn"``) should pass the UNION of
    both alphabets as ``known``.
    """
    known_sorted = sorted(set(known))
    if requested in known_sorted:
        return None

    matches = difflib.get_close_matches(requested, known_sorted, n=n_suggestions, cutoff=cutoff)

    # LAW 4 concrete-noun-anchored facts. Both base facts terminate on
    # ``fact_anchor`` (the caller's plural concrete noun, unless the
    # caller overrides the second-fact tail via ``known_disclosure_label``
    # — in which case the override's terminal MUST stay a concrete-noun
    # anchor too). The optional close-match fact is parenthesised-tail-
    # style so the terminal stays predictable.
    #
    # Default (no overrides):  "'X' not in known {fact_anchor}"
    # Verb override only:      "'X' {verb} {fact_anchor}"
    # Label override only:     "'X' not in known {label}"
    # Both overrides:          "'X' {verb} {label}"
    if known_disclosure_label is not None:
        second_fact = f"{requested!r} {requested_disclosure_verb} {known_disclosure_label}"
    else:
        second_fact = f"{requested!r} {requested_disclosure_verb} {fact_anchor}"
    facts: list[str] = [
        f"0 matching {fact_anchor}",
        second_fact,
    ]
    if matches:
        quoted = " or ".join(f"'{m}'" for m in matches)
        facts.append(f"closest match: {quoted}")

    verdict_suffix = ""
    if matches:
        quoted = " or ".join(f"'{m}'" for m in matches)
        verdict_suffix = f" Did you mean: {quoted}?"

    fragment: dict[str, Any] = {
        "state": state,
        "partial_success": True,
        requested_field: requested,
        known_field: known_sorted,
    }
    # W1081: when ``did_you_mean_omit_when_empty=True`` AND no matches were
    # found, omit the field entirely so callers can splice without
    # conditional logic. Default (False) preserves byte-identical
    # pre-W1081 behavior — the ``did_you_mean`` key is always present in
    # the same insertion-order slot (between ``known_field`` and
    # ``facts``) with a possibly-empty list value.
    if not (did_you_mean_omit_when_empty and not matches):
        fragment["did_you_mean"] = matches
    fragment["facts"] = facts
    fragment["verdict_suffix"] = verdict_suffix
    return fragment


__all__ = ["structured_unknown_filter"]
