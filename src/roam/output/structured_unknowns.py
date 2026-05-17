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

W1083 Phase 3 ergonomic refinement adds the sibling function
``to_summary_payload(fragment)`` that returns the summary-ready subset
of fields (``state``, ``partial_success``, the dynamic requested/known
field pair, and optionally ``did_you_mean``). The 5 callsites
previously hand-stamped these 4-5 keys into their summary literal; the
helper now centralises that splice so a new field can be added in one
place. The helper preserves the fragment's insertion order so JSON
output stays byte-stable.
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


def to_summary_payload(
    fragment: dict[str, Any],
    *,
    include_did_you_mean: bool = True,
) -> dict[str, Any]:
    """Return the envelope-``summary``-ready subset of a fragment.

    W1083 ergonomic refinement. The 5 callsites that adopted
    ``structured_unknown_filter`` in W1080/W1082 each hand-stamped the
    same 4-5 keys into their ``summary={...}`` literal:

    * ``partial_success`` (always ``True`` on a fragment)
    * ``state``
    * the caller-named ``<requested_field>`` (e.g. ``requested_detector``)
    * the caller-named ``<known_field>`` (e.g. ``known_detectors``)
    * optionally ``did_you_mean`` (3 of 5 callsites: ``cmd_findings``,
      ``cmd_endpoints`` x2)

    This helper extracts that subset so callers can splice it into
    their summary via ``summary.update(to_summary_payload(frag))`` or
    ``summary = {"verdict": ..., **to_summary_payload(frag), ...}``.
    The dynamic field names are preserved verbatim — the helper
    discovers them by exclusion (every key that is NOT one of the
    well-known fixed fields) so callers do NOT have to repeat the
    ``requested_field`` / ``known_field`` argument from the
    ``structured_unknown_filter`` call.

    Parameters
    ----------
    fragment
        A non-``None`` return value from ``structured_unknown_filter``.
        Passing ``None`` raises ``TypeError`` at the membership check —
        callsites already guard ``if frag is not None:`` so the type-
        check here is a safety net, not a hot path.
    include_did_you_mean
        When ``True`` (default), the ``did_you_mean`` field is carried
        over IFF it is present in the fragment (the
        ``did_you_mean_omit_when_empty=True`` W1081 kwarg can omit it).
        When ``False``, the field is dropped from the payload
        unconditionally — matches the ``cmd_search`` and
        ``cmd_test_scaffold`` choice of carrying close-matches only in
        the verdict suffix, not in the structured summary.

    Returns
    -------
    ``dict`` with the same key order as the fragment (Python 3.7+
    dict insertion-order guarantee) so JSON serialization stays
    byte-stable across the migration.
    """
    # Fixed fields the helper itself owns — every OTHER key in the
    # fragment is one of the two caller-named dynamic fields
    # (``<requested_field>`` + ``<known_field>``). Discovering them by
    # exclusion lets the consumer avoid repeating the field-name args.
    _FIXED_NON_SUMMARY = {"facts", "verdict_suffix"}
    payload: dict[str, Any] = {}
    for key, value in fragment.items():
        if key in _FIXED_NON_SUMMARY:
            continue
        if key == "did_you_mean" and not include_did_you_mean:
            continue
        payload[key] = value
    return payload


def structured_unknown_filter_many(
    requested: list[str] | tuple[str, ...],
    known: Iterable[str],
    *,
    field_name: str,
    fact_anchor: str,
    state: str,
    n_suggestions: int = 2,
    cutoff: float = 0.6,
    drop_empty: bool = True,
) -> dict[str, Any]:
    """W1083-followup-3 — multi-value sibling of
    ``structured_unknown_filter``.

    Validates a LIST of user-supplied values against a closed vocabulary,
    partitions into valid / unknown, and emits per-unknown ``difflib``
    closest-match suggestions. Unlike the single-value helper, this one
    ALWAYS returns a dict (never ``None``) — the caller usually wants the
    valid/unknown partition even on the happy path so a local partition
    loop can be removed.

    The Pattern-1D disclosure fields (``state``, ``partial_success``,
    ``did_you_mean``, ``verdict_suffix``, ``warnings_text``) are
    populated conditionally — they appear iff the unknown subset is
    non-empty. Callers can splice the fragment into their envelope via
    ``to_summary_payload_many`` (which excludes presentation-only fields
    like ``facts``, ``verdict_suffix``, ``warnings_text``, ``valid_*``)
    and ``warnings_list.extend(frag["warnings_text"])``.

    Parameters
    ----------
    requested
        The user-supplied list of filter values (from a click
        ``multiple=True`` option). May be a list or tuple.
    known
        Closed vocabulary to validate against. Iterated once; the helper
        sorts + dedupes internally.
    field_name
        Singular field-name root used to derive ``requested_<field>s``,
        ``known_<field>s``, ``valid_<field>s``, ``unknown_<field>s``
        summary keys (e.g. ``"detector"`` -> ``requested_detectors`` /
        ``known_detectors`` / ``valid_detectors`` / ``unknown_detectors``).
    fact_anchor
        LAW 4 plural concrete-noun terminal used in ``facts`` strings
        (e.g. ``"detectors"``, ``"kinds"``).
    state
        Closed-enum disclosure state stamped when the unknown subset is
        non-empty (e.g. ``"unknown_detectors"``).
    n_suggestions
        ``difflib.get_close_matches`` ``n=`` (default 2 — canonical
        across both BAILed callsites per W1083-RESEARCH §3).
    cutoff
        ``difflib.get_close_matches`` cutoff (default 0.6 — canonical).
    drop_empty
        When ``True`` (default), empty-string / falsy entries in
        ``requested`` are skipped before partitioning. Mirrors the
        cmd_smells preflight (``for k in kind_filter: if not k: continue``)
        and is a safe no-op for callsites that already pre-filter.

    Returns
    -------
    ``dict`` with the documented shape (see W1083-RESEARCH-multi-value
    §3.2). Always non-``None`` — the empty-happy-path shape contains
    ``requested_*``, ``known_*``, ``valid_*``, ``unknown_*`` (empty),
    ``facts`` (the LAW 4 anchored "all valid" line), and an empty
    ``warnings_text``.
    """
    if drop_empty:
        requested_clean = [r for r in requested if r]
    else:
        requested_clean = list(requested)

    known_sorted = sorted(set(known))
    known_set = set(known_sorted)

    valid: list[str] = [r for r in requested_clean if r in known_set]
    unknown: list[str] = [r for r in requested_clean if r not in known_set]

    # Preserve insertion order (request-order), dedupe while preserving
    # first-seen order so consumers reading the lists see a stable order.
    def _dedupe_preserve(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    valid = _dedupe_preserve(valid)
    unknown = _dedupe_preserve(unknown)

    requested_key = f"requested_{field_name}s"
    known_key = f"known_{field_name}s"
    valid_key = f"valid_{field_name}s"
    unknown_key = f"unknown_{field_name}s"

    # Build per-unknown suggestions dict (only for entries with hits).
    did_you_mean: dict[str, list[str]] = {}
    for u in unknown:
        matches = difflib.get_close_matches(u, known_sorted, n=n_suggestions, cutoff=cutoff)
        if matches:
            did_you_mean[u] = matches

    # LAW 4 concrete-noun-anchored facts. The fact-anchor MUST be a
    # plural concrete noun in the formatter's concrete_plural_terminals
    # set AND the test lint's _CONCRETE_NOUN_ANCHORS set.
    facts: list[str] = []
    if requested_clean:
        facts.append(f"{len(valid)} of {len(requested_clean)} valid {fact_anchor}")
    if unknown:
        facts.append(f"{len(unknown)} unknown {fact_anchor}")
        for u in unknown:
            if u in did_you_mean:
                quoted = " or ".join(f"'{m}'" for m in did_you_mean[u])
                facts.append(f"closest match for {u!r}: {quoted}")

    # Pre-formatted verdict suffix + warning strings for splicing.
    verdict_suffix = ""
    warnings_text: list[str] = []
    if unknown:
        # Verdict suffix: aggregate, short. Lists the unknowns and any
        # per-unknown suggestions inline (single line, no newlines —
        # the verdict is one line per LAW 6).
        unknown_quoted = ", ".join(f"'{u}'" for u in unknown)
        verdict_suffix = f" Unknown: {unknown_quoted}."
        if did_you_mean:
            per_name_hints: list[str] = []
            for u in unknown:
                if u in did_you_mean:
                    quoted = " or ".join(f"'{m}'" for m in did_you_mean[u])
                    per_name_hints.append(f"{u!r} -> {quoted}")
            if per_name_hints:
                verdict_suffix += f" Did you mean: {'; '.join(per_name_hints)}?"

        # Per-unknown warning strings ready for warnings_out (the
        # caller does warnings_list.extend(frag["warnings_text"])).
        for u in unknown:
            base = (
                f"Drop {u!r}: unknown {field_name} matches 0 entries; "
                f"pick one of the {len(known_sorted)} registered {fact_anchor}"
            )
            if u in did_you_mean:
                quoted = " or ".join(f"'{m}'" for m in did_you_mean[u])
                base = f"{base}. Did you mean: {quoted}?"
            warnings_text.append(base)

    # Assemble fragment with insertion-order preservation. The
    # Pattern-1D disclosure fields (state / partial_success / did_you_mean)
    # are conditionally present iff unknown is non-empty.
    fragment: dict[str, Any] = {}
    if unknown:
        fragment["state"] = state
        fragment["partial_success"] = True
    fragment[requested_key] = list(requested_clean)
    fragment[known_key] = known_sorted
    fragment[valid_key] = valid
    fragment[unknown_key] = unknown
    if did_you_mean:
        fragment["did_you_mean"] = did_you_mean
    fragment["facts"] = facts
    fragment["verdict_suffix"] = verdict_suffix
    fragment["warnings_text"] = warnings_text
    return fragment


def to_summary_payload_many(
    fragment: dict[str, Any],
    *,
    include_did_you_mean: bool = True,
    include_known: bool = True,
    include_valid: bool = False,
) -> dict[str, Any]:
    """W1083-followup-3 — multi-value sibling of ``to_summary_payload``.

    Returns the envelope-``summary``-ready subset of a multi-value
    fragment. Excludes presentation-only fields (``facts``,
    ``verdict_suffix``, ``warnings_text``) that belong on
    ``agent_contract``, the verdict, and ``warnings_out`` respectively.

    Parameters
    ----------
    fragment
        Return value of ``structured_unknown_filter_many``. Always a
        dict (the multi-value helper never returns ``None``).
    include_did_you_mean
        When ``True`` (default) carries the per-unknown suggestions map
        iff it is present in the fragment.
    include_known
        When ``True`` (default) carries the sorted closed-set
        ``known_<field>s``. Pass ``False`` for callsites that already
        document the closed set elsewhere and want a lighter summary
        (e.g. cmd_math which doesn't currently surface ``known_detectors``
        on the summary — the existing W1057 envelope only carried
        ``only_unknown`` / ``exclude_unknown``).
    include_valid
        When ``True`` carries the ``valid_<field>s`` list. Default
        ``False`` matches both BAILed callsites — neither cmd_math nor
        cmd_smells surfaces a ``valid_*`` field today.

    Returns
    -------
    ``dict`` with the same insertion order as the fragment for byte-
    stable JSON serialization.
    """
    _FIXED_NON_SUMMARY = {"facts", "verdict_suffix", "warnings_text"}
    payload: dict[str, Any] = {}
    for key, value in fragment.items():
        if key in _FIXED_NON_SUMMARY:
            continue
        if key == "did_you_mean" and not include_did_you_mean:
            continue
        if key.startswith("known_") and not include_known:
            continue
        if key.startswith("valid_") and not include_valid:
            continue
        payload[key] = value
    return payload


__all__ = [
    "structured_unknown_filter",
    "to_summary_payload",
    "structured_unknown_filter_many",
    "to_summary_payload_many",
]
