"""SARIF 2.1.0 output for GitHub code scanning integration.

Converts roam analysis results into Static Analysis Results Interchange
Format (SARIF) for consumption by GitHub Advanced Security, VS Code SARIF
Viewer, and other SARIF-aware tools.

Usage::

    from roam.output.sarif import dead_to_sarif, write_sarif

    sarif = dead_to_sarif(dead_exports)
    write_sarif(sarif, "roam-dead.sarif")
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
from pathlib import Path
from typing import Any, Callable

from roam.output._severity import _legacy_level_map, to_sarif_level
from roam.output.formatter import WarningsOut

_SARIF_VERSION = "2.1.0"
_SARIF_SCHEMA = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json"
_TOOL_NAME = "roam-code"
_HELP_BASE = "https://github.com/Cranot/roam-code#"


def _get_version() -> str:
    """Return roam-code version string."""
    from roam import __version__

    return __version__


# ── Severity mapping ─────────────────────────────────────────────────
#
# W547: the canonical roam severity vocabulary + SARIF projection now
# lives in :mod:`roam.output._severity`. The symbols below are kept as
# back-compat shims so external callers / older tests still resolve.
# NEW CODE: import :func:`to_sarif_level` from
# :mod:`roam.output._severity` directly.

_LEVEL_MAP = _legacy_level_map()


def _to_level(severity: str) -> str:
    """Back-compat shim — delegates to :func:`roam.output._severity.to_sarif_level`.

    Closed mapping (case-insensitive, alias-aware):

        CRITICAL / ERROR             -> "error"
        HIGH / WARNING               -> "warning"
        MEDIUM / LOW / INFO / NOTE   -> "note"

    Unknown labels default to ``"note"`` so an unrecognised severity never
    accidentally fails a CI gate keyed off SARIF level=error.
    """
    return to_sarif_level(severity)


# ── Location helpers ─────────────────────────────────────────────────


def _physical_location(file_path: str, line: int | None = None) -> dict:
    """Build a SARIF physicalLocation object.

    *file_path* is stored as a forward-slash URI-style path so that
    SARIF viewers can render it correctly on any platform.
    """
    uri = file_path.replace("\\", "/")
    loc: dict = {
        "artifactLocation": {"uri": uri},
    }
    if line is not None and line > 0:
        loc["region"] = {"startLine": line}
    return loc


def _location(file_path: str, line: int | None = None) -> dict:
    """Build a single SARIF location entry."""
    return {"physicalLocation": _physical_location(file_path, line)}


def _parse_loc_string(loc_str: str) -> tuple[str, int | None]:
    """Parse ``"path/to/file.py:42"`` into ``("path/to/file.py", 42)``.

    Returns ``(path, None)`` when no line number is present.
    """
    if ":" in loc_str:
        parts = loc_str.rsplit(":", 1)
        try:
            return parts[0], int(parts[1])
        except (ValueError, IndexError):
            return loc_str, None
    return loc_str, None


# ── Emitter-level rule / result builders (W1178) ─────────────────────
#
# Building-block substrate for the 20-emitter SARIF pipeline. Each
# emitter previously inlined two near-identical dict shapes (a rule
# descriptor + a result row) per finding family. These helpers centralise
# the shape so the per-emitter sites only have to assert the *content*
# (id strings, message text, locations, custom level mapping). Hash
# stability is preserved: the helpers reproduce the historical key order
# (id -> shortDescription -> helpUri -> defaultConfiguration; ruleId ->
# level -> message -> locations -> extras) so json.dumps emits the same
# bytes the inline call-sites previously did.
#
# Adopt-as-you-go: W1178 only refactors three emitters (dead, critique,
# partition). The remaining ~17 adopters land as W1179.


def _rule_entry(
    id: str,
    short_desc: str,
    help_uri: str = "",
    default_level: str = "",
    **extras: Any,
) -> dict:
    """Build a SARIF rule descriptor dict (W1178 substrate).

    Centralises the rule-shape boilerplate previously inlined at every
    ``*_to_sarif`` call site. The emitted dict uses the SAME key shape
    the legacy inline literals used (``id`` / ``shortDescription`` /
    optional ``helpUri`` / optional ``defaultLevel``) so the result is
    byte-identical to the pre-W1178 inline construction when fed through
    :func:`_build_rule` inside :func:`to_sarif`.

    *help_uri* and *default_level* are optional; passing the empty
    string (default) omits the key from the dict — the historical
    behaviour of the inline call sites.

    *extras* (W1186) lets callers attach optional SARIF rule-descriptor
    keys (``properties`` / ``fullDescription`` / ``messageStrings`` /
    ``relationships``). They pass through via ``dict.update`` so
    insertion order matches the caller's kwarg order — preserving
    byte-stability for emitters that already attach these fields in a
    fixed sequence. Mirrors the ``**extras`` pattern on
    :func:`_result_entry`. Example::

        _rule_entry(
            id="taint.SQLI",
            short_desc="Taint: SQLI",
            help_uri=_HELP_BASE + "taint",
            default_level="error",
            properties={"tags": ["security", "taint", "CWE-89"]},
        )
    """
    out: dict[str, Any] = {"id": id, "shortDescription": short_desc}
    if help_uri:
        out["helpUri"] = help_uri
    if default_level:
        out["defaultLevel"] = default_level
    if extras:
        out.update(extras)
    return out


def _result_entry(
    rule_id: str,
    severity: str,
    locations: list[dict],
    message: str,
    level_mapper: Callable[[str], str] = _to_level,
    **extras: Any,
) -> dict:
    """Build a SARIF result dict (W1178 substrate).

    Centralises the result-row shape previously inlined at every
    ``*_to_sarif`` call site. The emitted dict preserves the historical
    key order (``ruleId`` -> ``level`` -> ``message`` -> ``locations``
    -> caller-supplied extras via ``dict.update``) so json.dumps emits
    byte-identical output to the pre-W1178 inline literals.

    *level_mapper* accepts the helper's per-emitter severity translation
    function. Examples in tree:

    - :func:`_to_level` (default — closed-enum severity vocabulary).
    - :func:`_impact_importance_level` (PageRank importance band).
    - :func:`_clones_pair_level` (similarity score band).
    - :func:`_partition_conflict_risk_level` (LOW/MEDIUM/HIGH band).

    *extras* lets callers attach optional SARIF result keys
    (``codeFlows`` / ``properties`` / ``fixes`` / ``fingerprints`` /
    ``partialFingerprints`` / ``relatedLocations``). They pass through
    via ``dict.update`` so insertion order matches the caller's kwarg
    order — preserving byte-stability for emitters that already attach
    these fields in a fixed sequence.
    """
    result: dict[str, Any] = {
        "ruleId": rule_id,
        "level": level_mapper(severity),
        "message": {"text": message},
        "locations": locations,
    }
    if extras:
        result.update(extras)
    return result


# ── Dashboard-filtering tag derivation (W1062) ───────────────────────
#
# SARIF 2.1.0 stores free-form categorisation tags under
# ``result.properties.tags[]`` (and ``rule.properties.tags[]``).
# GitHub Code Scanning, SonarQube, and security-dashboard tools surface
# them as filter chips so a triage user can slice findings by CWE /
# OWASP category / detector family without expanding every result.
#
# Without normalised tags every roam finding looks uniform to the
# dashboard. This helper centralises the projection so every emitter
# produces the same tag vocabulary shape:
#
#   - lowercase, hyphen-separated, URL-safe (CWE-89 -> ``cwe-89``)
#   - one tag per metadata axis (cwe, owasp, severity, family, ...)
#   - free-form ``extra`` tags pass through after normalisation
#   - duplicates collapsed, order preserved
#
# The vocabulary is intentionally narrow: agents and dashboards key off
# stable tokens like ``cwe-89`` / ``owasp-a03`` / ``security`` rather
# than producer-side variants like ``CWE-89`` / ``A03:2021_Injection``.

import re as _re

# Match the OWASP Top 10 category prefix only ("A01" through "A99")
# so we strip the year + descriptive suffix from inputs like
# ``A03:2021_Injection`` -> ``a03`` without dropping the rank token.
_OWASP_CATEGORY_RE = _re.compile(r"^a\d{1,2}", _re.IGNORECASE)


def _normalize_tag(raw: str) -> str:
    """Normalise one tag string to lowercase-hyphen URL-safe shape.

    Conversion rules (closed):

    - lowercase
    - whitespace, underscores, colons, slashes, dots collapse to ``-``
    - leading/trailing hyphens trimmed
    - empty input returns ``""`` (caller drops empties)

    Examples::

        ``CWE-89``                   -> ``cwe-89``
        ``A03:2021_Injection``       -> ``a03-2021-injection``
        ``EU AI Act Article 12``     -> ``eu-ai-act-article-12``
        ``security``                 -> ``security``
    """
    s = (raw or "").strip().lower()
    if not s:
        return ""
    # Collapse common separators to single hyphen.
    s = _re.sub(r"[\s_:/.]+", "-", s)
    # Drop any leftover non [a-z0-9-] characters (parentheses, etc.).
    s = _re.sub(r"[^a-z0-9-]", "", s)
    # Collapse consecutive hyphens then trim edges.
    s = _re.sub(r"-{2,}", "-", s).strip("-")
    return s


def _derive_finding_tags(
    *,
    cwe: str = "",
    owasp_top10: str = "",
    severity: str = "",
    family: str = "",
    extra: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Build a normalised SARIF ``properties.tags[]`` list from one finding.

    Threads the OWASP / CWE metadata roam already attaches to taint
    rules (W492 ``owasp_top10`` field; W374 CWE codes) into the SARIF
    surface so dashboards can filter by CWE / OWASP category. Without
    this, every roam finding looks uniform to the dashboard.

    Parameters
    ----------
    cwe:
        Raw CWE token, e.g. ``"CWE-89"``. Empty string drops the tag.
    owasp_top10:
        Raw OWASP Top 10 string. Accepts the rank-only form ``"A03"``
        or the rank+year+name form ``"A03:2021_Injection"`` (the shape
        rule YAMLs ship today). Only the rank prefix is kept, projected
        to ``owasp-a03``. Empty string drops the tag.
    severity:
        Closed-enum severity vocab (``critical`` / ``warning`` / ``info``
        / ``error`` / ``note`` / ``low`` / ``medium`` / ``high``). Empty
        string drops the tag. Passed through ``_normalize_tag`` so
        producer-side uppercase variants converge.
    family:
        Finding-family / detector-bucket tag, e.g. ``"security"``,
        ``"taint"``, ``"compliance"``. Empty string drops the tag.
    extra:
        Free-form additional tags (e.g. detector-specific tokens).
        Each entry is normalised and emptied entries are dropped.

    Returns
    -------
    list[str]
        Normalised tag list with duplicates collapsed (insertion order
        preserved). Empty list when every input is empty.

    Examples
    --------
    Taint SQLI finding::

        _derive_finding_tags(
            cwe="CWE-89", owasp_top10="A03:2021_Injection",
            severity="error", family="security", extra=["taint"],
        )
        # -> ['security', 'taint', 'cwe-89', 'owasp-a03', 'error']

    Vulnerability finding::

        _derive_finding_tags(cwe="", severity="critical", family="vuln")
        # -> ['vuln', 'critical']

    Audit-trail conformance check::

        _derive_finding_tags(
            family="compliance",
            extra=["eu-ai-act-article-12", "chain_integrity"],
        )
        # -> ['compliance', 'eu-ai-act-article-12', 'chain-integrity']
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        if not token:
            return
        norm = _normalize_tag(token)
        if not norm or norm in seen:
            return
        seen.add(norm)
        out.append(norm)

    # Family / detector-bucket first so dashboards anchor on the broad
    # axis before the specific identifiers. Matches the legacy taint
    # vocabulary (``["security", "taint", ...]``) which is the only
    # pre-W1062 tag shape any external consumer already keys off.
    _add(family)

    # ``extra`` tokens pass through next so per-emitter custom tags
    # (e.g. ``["taint"]`` after ``family="security"``) line up with the
    # legacy taint shape rather than appearing after CWE / OWASP.
    for token in extra:
        _add(token)

    # CWE projection: ``CWE-89`` -> ``cwe-89``. Pure normalisation; the
    # token shape is already URL-safe-ish, this just lowercases.
    if cwe:
        _add(cwe)

    # OWASP projection: keep only the rank prefix so ``A03`` and
    # ``A03:2021_Injection`` collapse to the same ``owasp-a03`` tag.
    # Year + descriptive suffix is noise for dashboard filtering — the
    # rank IS the OWASP Top 10 category identifier.
    if owasp_top10:
        match = _OWASP_CATEGORY_RE.match(owasp_top10.strip())
        if match:
            _add("owasp-" + match.group(0).lower())
        else:
            # Unknown shape: pass through normalised so we don't drop
            # signal silently. A rule author who writes
            # ``owasp_top10: "Mobile-M1"`` still gets ``owasp-mobile-m1``.
            _add("owasp-" + owasp_top10)

    # Severity last — dashboards usually have a dedicated severity
    # facet, but stamping the tag too lets text-search filters work.
    _add(severity)

    return out


# ── Core builder ─────────────────────────────────────────────────────


# ── Runtime-notification descriptor IDs (W1046) ──────────────────────
#
# Closed enumeration of `descriptor.id` strings used on
# `run.invocations[].toolExecutionNotifications[]` entries when
# ``emit_runtime_notifications=True`` is passed to :func:`to_sarif`.
# Keep this enum tight — every entry must map to a real producer call-site
# that captures `warnings_out` for the corresponding SARIF surface.
_RUNTIME_NOTIFICATION_SUPPRESSIONS_MALFORMED = "suppressions.malformed-entry"
# W1060: producer-supplied advisory warnings from a detector's
# ``warnings: list[str]`` accumulator (Pattern 1B / Pattern 2 silent-fallback
# disclosure). Used when ``complexity_to_sarif`` / future ``*_to_sarif``
# helpers pass through their command's warnings accumulator.
_RUNTIME_NOTIFICATION_PRODUCER_ADVISORY = "producer.advisory-warning"


# W1061-followup-2: shared builder for the W1061 / W1061-followup
# override pair. The "build a list of configurationOverride dicts and a
# parallel list of notificationConfigurationOverride dicts" boilerplate
# was duplicated across 4 callers (cmd_smells, cmd_check_rules,
# cmd_taint, cmd_vulns). Each caller composed structurally identical
# entries from a (descriptor_id, properties) pair:
#
#     {"configuration": {"enabled": <bool>},
#      "descriptor": {"id": <str>},
#      "properties": <dict>}
#
# This helper centralises that construction. The two returned lists are
# passed verbatim onto :func:`to_sarif`'s ``configuration_overrides`` /
# ``notification_configuration_overrides`` kwargs.
#
# Semantic contract preserved from the inline builders:
#   - rule-id-level entries always carry ``configuration.enabled: False``
#     (the rule is disabled by a runtime filter — disclosure under
#     SARIF §3.51 ``ruleConfigurationOverrides[]``).
#   - finding-level entries always carry ``configuration.enabled: True``
#     (the filter IS active and operates at finding-evaluation time
#     under SARIF §3.20.4 ``notificationConfigurationOverrides[]``).
#
# Empty input → empty output. Callers gate emission on the returned
# lists being non-empty (preserves byte-stable default-path SARIF
# bytes — see W1061 hash invariants).
def runtime_filter_disclosure(
    *,
    rule_ids_disabled: list[tuple[str, dict]] | None = None,
    finding_level_filters: list[tuple[str, dict]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Build the W1061 / W1061-followup override pair.

    Parameters
    ----------
    rule_ids_disabled:
        Sequence of ``(rule_descriptor_id, properties)`` tuples — each
        becomes a ``configurationOverride`` with
        ``configuration.enabled: False`` and ``descriptor.id =
        rule_descriptor_id``. Caller owns the descriptor-id namespace
        (e.g. ``"smells/<kind>"`` for cmd_smells, ``"rules/<id>"`` for
        cmd_check_rules, bare ``<rule_id>`` for cmd_taint).
    finding_level_filters:
        Sequence of ``(notification_descriptor_id, properties)`` tuples
        — each becomes a ``notificationConfigurationOverride`` with
        ``configuration.enabled: True`` and ``descriptor.id =
        notification_descriptor_id`` (a synthetic descriptor like
        ``"severity-filter"`` / ``"reachable-only-filter"`` /
        ``"rules-dir-filter"`` — NOT a real rule id).

    Returns
    -------
    tuple[list[dict], list[dict]]
        ``(rule_overrides, notification_overrides)`` — pass straight
        through to :func:`to_sarif`'s
        ``configuration_overrides`` / ``notification_configuration_overrides``
        kwargs (with ``emit_configuration_overrides=bool(rule_overrides)``).
        Either list is empty when its input was ``None`` / empty.
    """
    rule_overrides: list[dict] = []
    for descriptor_id, props in rule_ids_disabled or ():
        rule_overrides.append(
            {
                "configuration": {"enabled": False},
                "descriptor": {"id": descriptor_id},
                "properties": dict(props),
            }
        )
    notif_overrides: list[dict] = []
    for descriptor_id, props in finding_level_filters or ():
        notif_overrides.append(
            {
                "configuration": {"enabled": True},
                "descriptor": {"id": descriptor_id},
                "properties": dict(props),
            }
        )
    return rule_overrides, notif_overrides


def to_sarif(
    tool_name: str,
    version: str,
    rules: list[dict],
    results: list[dict],
    *,
    emit_runtime_notifications: bool = False,
    warnings_out: list[str] | None = None,
    emit_configuration_overrides: bool = False,
    configuration_overrides: list[dict] | None = None,
    notification_configuration_overrides: list[dict] | None = None,
) -> dict:
    """Build a complete SARIF 2.1.0 JSON document.

    Parameters
    ----------
    tool_name:
        Display name of the analysis tool (e.g. ``"roam-code"``).
    version:
        Semantic version of the tool.
    rules:
        List of rule definitions.  Each dict must contain:

        - ``id`` (str): unique rule identifier
        - ``shortDescription`` (str): one-line description

        Optional keys:

        - ``helpUri`` (str): URL for more information
        - ``defaultLevel`` (str): SARIF level (``"error"``/``"warning"``/``"note"``)
    results:
        List of result dicts.  Each must contain:

        - ``ruleId`` (str): matches a rule ``id``
        - ``level`` (str): ``"error"``/``"warning"``/``"note"``
        - ``message`` (str): human-readable finding description
        - ``locations`` (list[dict]): SARIF location objects
    emit_runtime_notifications:
        W1046 (opt-in, default ``False`` — preserves byte-identical SARIF
        output for pre-W1046 callers). When ``True``, every silent-fallback
        warning surfaced by the SARIF suppressions loaders (W1042) is
        projected onto a
        SARIF 2.1.0 ``run.invocations[].toolExecutionNotifications[]``
        entry. Each notification carries ``level: "warning"``,
        ``descriptor.id`` from the closed runtime-notification enumeration
        (``"suppressions.malformed-entry"`` for SARIF-loader warnings;
        ``"producer.advisory-warning"`` for caller-supplied detector
        warnings — see *warnings_out* below), and ``message.text`` from
        the underlying warning string. The ``invocations[]`` field is
        always emitted when this flag is True (empty
        ``toolExecutionNotifications: []`` when there are no warnings)
        so consumers can distinguish "opted in + clean run" from "did
        not opt in".
    warnings_out:
        W1060 (opt-in, default ``None``). Caller-supplied list of
        producer-side advisory warning strings (Pattern 1B / Pattern 2
        silent-fallback disclosure from a detector's ``warnings``
        accumulator). When supplied AND ``emit_runtime_notifications=True``,
        each string is projected onto a
        ``toolExecutionNotifications[]`` entry with
        ``descriptor.id: "producer.advisory-warning"``. When supplied but
        ``emit_runtime_notifications=False``, the warnings are silently
        dropped (the opt-in flag is the ONLY gate that can put
        ``invocations[]`` on the SARIF document — preserves byte-stable
        default-path output). Hash invariant: passing ``warnings_out=None``
        or ``warnings_out=[]`` produces SARIF output byte-identical to
        omitting the kwarg.
    emit_configuration_overrides:
        W1061 (opt-in, default ``False`` — preserves byte-identical SARIF
        output for pre-W1061 callers). When ``True`` AND
        ``configuration_overrides`` is a non-empty list, the entries are
        projected onto
        ``run.invocations[0].ruleConfigurationOverrides[]`` per the SARIF
        2.1.0 OASIS spec §3.51 ``configurationOverride`` object — each
        entry MUST carry ``configuration`` (a ``reportingConfiguration``
        per §3.50, accepting ``level`` from the same closed enum as
        ``notification.level``: ``"none"`` / ``"note"`` / ``"warning"`` /
        ``"error"``, plus an ``enabled`` bool) AND ``descriptor`` (a
        ``reportingDescriptorReference`` per §3.51.2, carrying ``id``).
        The invocations entry is created if absent so this flag composes
        cleanly with ``emit_runtime_notifications``. Use when a runtime
        filter (``--min-severity high``, ``--gate-pattern``, ``--kind``)
        means a "no findings" SARIF result should be readable as
        "filtered" rather than "clean codebase". Empty / None list +
        opt-in produces no key on the SARIF document (hash-stable).
    configuration_overrides:
        W1061 (opt-in, default ``None``). List of pre-built SARIF
        configurationOverride dicts. Each dict's shape is forwarded
        verbatim — the caller owns the closed-enum discipline on
        ``configuration.level``. When ``emit_configuration_overrides`` is
        False this kwarg is ignored (silent drop preserves byte-stable
        pre-W1061 output).
    notification_configuration_overrides:
        W1061-followup (opt-in, default ``None``). Sibling field of
        ``ruleConfigurationOverrides`` per SARIF 2.1.0 §3.20.4 — the
        spec slot for FINDING-LEVEL filters that don't map onto
        rule-id-granular disable semantics (e.g. ``--min-severity high``,
        ``--reachable-only`` on cmd_vulns). Entries carry the same shape
        as ``configurationOverride`` (§3.51): ``configuration`` (a
        reportingConfiguration §3.50, e.g. ``{"enabled": False}``) +
        ``descriptor`` (a reportingDescriptorReference §3.51.2 with
        ``id``) + free-form ``properties``. The ``descriptor.id``
        references a synthetic notification descriptor like
        ``reachable-only-filter`` rather than a real rule. Gated on
        non-empty list inside this function — passing ``None`` or
        ``[]`` keeps the SARIF document byte-identical to the
        pre-W1061-followup default path.

    Returns
    -------
    dict
        A complete SARIF 2.1.0 envelope ready for ``json.dumps``.
    """
    driver: dict = {
        "name": tool_name,
        "version": version,
        "informationUri": "https://roam-code.com/",
        "downloadUri": "https://pypi.org/project/roam-code/",
        "organization": "Cranot",
        "rules": [_build_rule(r) for r in rules],
    }

    # Apply suppressions — load .roam/suppressions.json if present and stamp
    # matching results with the SARIF "suppressions" array.
    # W736 (Phase C-1a of W692): prefer the typed loader + applier for the
    # canonical W691 dict shape. SARIF output bytes stay byte-identical to
    # the legacy dict-applier path — the typed surface is a
    # discriminated-union refactor on the in-memory representation, not a
    # behavioural change. Legacy on-disk shapes (top-level list,
    # ``{"suppressions": [...]}`` envelope) pre-date the W691 finding_id
    # discriminator and cannot project onto FindingIdSuppression without
    # invention; those paths fall back to the legacy dict applier so
    # back-compat fixtures (test_sarif_enrichment) keep passing. The
    # legacy _load_suppressions / _apply_suppressions helpers stay in-tree
    # for now; later sub-waves (W737/W738) will purge them once the
    # legacy on-disk shapes are also retired.
    #
    # W1046: when emit_runtime_notifications=True we plumb a warnings_out
    # accumulator into BOTH loader paths and project the captured warnings
    # onto SARIF run.invocations[].toolExecutionNotifications[] below.
    # Default-False keeps the loader calls warnings_out-free so pre-W1046
    # behaviour (silent fallback) stays byte-identical.
    suppressions_warnings: list[str] | None = [] if emit_runtime_notifications else None
    suppressions_typed = _load_suppressions_typed(
        warnings_out=suppressions_warnings,
    )
    if suppressions_typed:
        results = _apply_suppressions_typed(results, suppressions_typed)
    else:
        suppressions = _load_suppressions(warnings_out=suppressions_warnings)
        if suppressions:
            results = _apply_suppressions(results, suppressions)

    run: dict = {
        "tool": {"driver": driver},
        "automationDetails": _automation_details(tool_name, version),
        "results": results,
    }

    vcs = _version_control_provenance()
    if vcs:
        run["versionControlProvenance"] = vcs

    # W1046: project captured warnings onto run.invocations[]. The
    # ``invocations[]`` key is added ONLY when the caller opted in — this
    # preserves the byte-identical pre-W1046 SARIF output on the default
    # path.
    # W1060: ALSO project caller-supplied producer warnings (from a
    # detector's ``warnings`` accumulator) onto the same array, with a
    # distinct ``descriptor.id`` so consumers can tell loader-class
    # advisories apart from producer-class advisories.
    if emit_runtime_notifications:
        notifications: list[dict] = []
        for warning_text in suppressions_warnings or ():
            notifications.append(
                {
                    "level": "warning",
                    "descriptor": {
                        "id": _RUNTIME_NOTIFICATION_SUPPRESSIONS_MALFORMED,
                    },
                    "message": {"text": warning_text},
                }
            )
        for warning_text in warnings_out or ():
            notifications.append(
                {
                    "level": "warning",
                    "descriptor": {
                        "id": _RUNTIME_NOTIFICATION_PRODUCER_ADVISORY,
                    },
                    "message": {"text": warning_text},
                }
            )
        run["invocations"] = [
            {
                "executionSuccessful": True,
                "toolExecutionNotifications": notifications,
            }
        ]

    # W1061: project runtime rule-level configuration overrides onto
    # ``run.invocations[0].ruleConfigurationOverrides[]`` (SARIF 2.1.0
    # §3.20.5 + §3.51). Without this, consumers (GitHub Code Scanning,
    # Sonar) cannot distinguish a filtered "no findings" run (e.g. user
    # passed ``--min-severity high``) from a clean codebase. Opt-in via
    # ``emit_configuration_overrides=True`` AND non-empty
    # ``configuration_overrides``; default-False path stays byte-identical
    # to pre-W1061 SARIF output. Composes with W1046's
    # ``emit_runtime_notifications`` — share the same ``invocations[0]``
    # entry rather than emitting two parallel invocation objects.
    if emit_configuration_overrides and configuration_overrides:
        if "invocations" not in run:
            run["invocations"] = [{"executionSuccessful": True}]
        run["invocations"][0]["ruleConfigurationOverrides"] = list(
            configuration_overrides
        )

    # W1061-followup: finding-level filter disclosure via
    # ``notificationConfigurationOverrides[]`` (SARIF 2.1.0 §3.20.4).
    # Distinct from rule-level overrides because the underlying filter
    # operates at finding-evaluation time (severity floor, reachability)
    # rather than at rule-dispatch time. Composes with both
    # ``emit_runtime_notifications`` and ``emit_configuration_overrides``
    # — share the same ``invocations[0]`` entry. Default ``None`` /
    # empty stays byte-identical to the pre-W1061-followup output.
    if notification_configuration_overrides:
        if "invocations" not in run:
            run["invocations"] = [{"executionSuccessful": True}]
        run["invocations"][0]["notificationConfigurationOverrides"] = list(
            notification_configuration_overrides
        )

    return {
        "$schema": _SARIF_SCHEMA,
        "version": _SARIF_VERSION,
        "runs": [run],
    }


def _automation_details(tool_name: str, version: str) -> dict:
    """Build a SARIF automationDetails block — stable run identifier.

    Lets GitHub Code Scanning correlate re-ingests of the same logical
    run (e.g. nightly scans) instead of treating each as new findings.
    """
    import os
    from datetime import datetime, timezone

    run_guid = f"{tool_name}/{version}/{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    branch = os.environ.get("GITHUB_REF_NAME") or os.environ.get("CI_COMMIT_BRANCH") or "main"
    return {
        "id": f"roam-{tool_name}/{branch}",
        "guid": run_guid,
        "description": {"text": f"{tool_name} v{version} analysis run on {branch}"},
    }


def _version_control_provenance() -> list[dict]:
    """Probe git for the current commit SHA + branch, attach to the run.

    Returns an empty list when git is unavailable so SARIF stays valid.
    """
    import subprocess

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        if not sha:
            return []
        try:
            branch = (
                subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                ).stdout.strip()
                or "main"
            )
        except Exception:
            branch = "main"
        try:
            remote = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        except Exception:
            remote = ""
        entry = {
            "revisionId": sha,
            "branch": branch,
        }
        if remote:
            entry["repositoryUri"] = remote
        return [entry]
    except Exception:
        return []


def _load_suppressions(
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Load .roam/suppressions.json if present (W691: canonical-shape aware).

    Three on-disk shapes are accepted; the SARIF loader normalises them
    all into ``list[dict]`` of ``{rule_id, location, reason, ...}`` rows
    so :func:`_apply_suppressions` does not need to branch.

    1. **Canonical dict shape** (written by ``roam suppress``):
       ``{finding_id_hex: {reason, added_at, source, rule_id?, location?}}``.
       The finding_id is a 16-char sha256 hash — not itself usable for
       SARIF (ruleId, location) matching. Entries that embed ``rule_id``
       and ``location`` ride through; entries that don't are dropped
       silently (we cannot reverse the hash).

    2. **Legacy SARIF list shape**: ``[{rule_id, location, ...}, ...]``
       — historical shape, kept for back-compat.

    3. **Legacy SARIF envelope shape**: ``{"suppressions": [...]}`` —
       same row contents under one extra key.

    The dict-vs-list disambiguation key is the value type of the first
    item: dict entry => canonical; list / row-dict => legacy SARIF.

    W1042 (Pattern 2 — silent fallback, mirror of W1009's
    :func:`finding_suppress._load_per_finding_suppressions`): when
    *warnings_out* is supplied as a ``list[str]``, every silent-fallback
    path (file unreadable / OSError, malformed JSON, non-dict / non-list
    root, malformed entry) appends an actionable warning naming the
    path, the failure shape, and the resolution. Pre-W1042 callers that
    don't supply ``warnings_out`` retain the byte-identical
    silent-empty-list behaviour so existing SARIF output bytes stay
    bit-identical when ``.roam/suppressions.json`` is well-formed.

    The file-read + JSON parse + root-type check live in
    :func:`roam.commands._yaml_loader.load_yaml_with_warnings` with
    ``parse_error_label="JSON"`` (matching the W1019e shape from
    :func:`finding_suppress._load_per_finding_suppressions`); the
    per-entry validation stays inline.
    """
    from roam.commands._yaml_loader import load_yaml_with_warnings

    candidate = Path.cwd() / ".roam" / "suppressions.json"
    path_str = str(candidate)

    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data = load_yaml_with_warnings(
        candidate,
        config_label="sarif-suppressions",
        parse_error_label="JSON",  # .roam/suppressions.json is JSON-shaped
        warnings_out=warnings_out,
        allow_list_root=True,  # legacy list shape is valid
    )
    if data is None:
        # Missing file — default state, no warning emitted by the helper.
        return []
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the failure (read error / malformed
        # JSON / wrong root type). Propagate the empty result without
        # piling on a second warning.
        return []

    # Legacy list shape: top-level array of {rule_id, location, ...}
    if isinstance(data, list):
        rows: list[dict] = []
        for idx, r in enumerate(data, start=1):
            if isinstance(r, dict):
                rows.append(r)
            elif warnings_out is not None:
                warnings_out.append(
                    f"sarif-suppressions: {path_str!r}: entry #{idx} is "
                    f"{type(r).__name__!r}, expected a mapping with "
                    f"`rule_id` + `location` keys. Skipping entry."
                )
        return rows

    if not isinstance(data, dict):
        return []

    # Legacy SARIF envelope shape: {"suppressions": [...]}
    if "suppressions" in data and isinstance(data["suppressions"], list):
        rows = []
        for idx, r in enumerate(data["suppressions"], start=1):
            if isinstance(r, dict):
                rows.append(r)
            elif warnings_out is not None:
                warnings_out.append(
                    f"sarif-suppressions: {path_str!r}: entry #{idx} "
                    f"under 'suppressions:' is {type(r).__name__!r}, "
                    f"expected a mapping with `rule_id` + `location` keys. "
                    f"Skipping entry."
                )
        return rows

    # Canonical dict shape (W691): {finding_id_hex: entry}. Convert each
    # entry to a SARIF-matching row when it embeds rule_id + location.
    # Heuristic for the canonical shape: every value is a dict carrying
    # a ``reason`` or ``added_at`` field (the cmd_suppress writer always
    # stamps ``added_at`` and either ``reason`` or both).
    if data and all(isinstance(v, dict) for v in data.values()):
        rows = []
        for fid, entry in data.items():
            # Per-finding entry must carry the SARIF projection fields
            # (rule_id + location) for the SARIF apply step to bind it.
            # Entries without them are recorded by the finding_suppress
            # layer through finding_id matching; they're correctly
            # invisible to SARIF.
            rule_id = entry.get("rule_id") or entry.get("ruleId")
            location = entry.get("location")
            if not (rule_id and location):
                continue
            rows.append(
                {
                    "rule_id": rule_id,
                    "location": location,
                    "reason": entry.get("reason", ""),
                    "kind": entry.get("kind", "external"),
                    "status": entry.get("status", "accepted"),
                    "finding_id": str(fid),
                }
            )
        return rows

    # Mixed-type values under the dict root (some dict, some scalar) —
    # not the canonical shape, not the envelope shape. Warn loudly and
    # bail to empty so the SARIF apply step is a no-op.
    if warnings_out is not None:
        non_dict = sum(1 for v in data.values() if not isinstance(v, dict))
        warnings_out.append(
            f"sarif-suppressions: {path_str!r}: root mapping has "
            f"{non_dict} non-dict entries; expected the canonical "
            f"{{finding_id_hex: {{...}}}} shape or the legacy "
            f"{{'suppressions': [...]}} envelope. Skipping file."
        )
    return []


def _load_suppressions_typed(
    *,
    warnings_out: WarningsOut = None,
) -> list:
    """Typed counterpart of :func:`_load_suppressions` (W723 Phase B-b).

    Returns the SARIF-visible subset of ``.roam/suppressions.json`` as
    :class:`roam.policy.suppression_v2.FindingIdSuppression` instances —
    i.e. ONLY entries that carry the SARIF projection fields
    (``rule_id`` + ``location``). Hash-only entries are filtered out
    because the SARIF apply step cannot bind them to (ruleId, location)
    tuples without reversing the hash.

    Mirrors the Phase A pattern shipped in
    :func:`roam.commands.suppression.load_suppressions_typed` and the
    Phase B-a pattern shipped in
    :func:`roam.commands.smells_suppress.load_smells_suppressions_typed`.

    The dict-shaped legacy view via :func:`_load_suppressions` stays the
    canonical entry point used by :func:`_apply_suppressions`; this typed
    surface is the bridge new code reaches for (W724 Phase C will
    migrate the applier itself).

    Legacy on-disk shapes (top-level list, ``{"suppressions": [...]}``
    envelope) are NOT projected here — the dataclass discriminator is
    ``finding_id``-keyed, and the legacy shapes pre-date that key. Use
    :func:`_load_suppressions` if you need the legacy projection.

    W1042 (Pattern 2 — silent fallback, mirror of W1017's
    :func:`finding_suppress.load_per_finding_suppressions_typed`): when
    *warnings_out* is supplied as a ``list[str]``, every silent-fallback
    path (file unreadable / OSError, malformed JSON, non-dict root,
    legacy non-finding-id shape) appends an actionable warning. Pre-W1042
    callers that don't supply ``warnings_out`` retain byte-identical
    silent-empty-list behaviour. The typed surface uses the SAME on-disk
    parser (:func:`load_yaml_with_warnings`) as
    :func:`_load_suppressions` so cross-loader warning shapes stay
    consistent.
    """
    # Local import keeps the policy package out of the import chain for
    # callers that only touch the legacy dict surface.
    from roam.commands._yaml_loader import load_yaml_with_warnings
    from roam.policy.suppression_v2 import FindingIdSuppression

    candidate = Path.cwd() / ".roam" / "suppressions.json"
    path_str = str(candidate)

    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data = load_yaml_with_warnings(
        candidate,
        config_label="sarif-suppressions",
        parse_error_label="JSON",  # .roam/suppressions.json is JSON-shaped
        warnings_out=warnings_out,
        allow_list_root=True,  # legacy list shape acknowledged via warning below
    )
    if data is None:
        # Missing file — default state, no warning emitted by the helper.
        return []
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the failure (read error / malformed
        # JSON / wrong root type). Propagate the empty result without
        # piling on a second warning.
        return []

    # Typed surface is canonical-only: dict-keyed by finding_id with
    # rule_id + location embedded. Legacy list / envelope shapes don't
    # have a finding_id discriminator so they cannot be projected onto
    # FindingIdSuppression without invention.
    if isinstance(data, list):
        if warnings_out is not None and data:
            warnings_out.append(
                f"sarif-suppressions: {path_str!r}: legacy top-level "
                f"list shape pre-dates the finding_id discriminator; "
                f"typed surface cannot project these rows onto "
                f"FindingIdSuppression. Migrate to the canonical "
                f"{{finding_id_hex: {{...}}}} shape or use the legacy "
                f"dict loader."
            )
        return []
    if not isinstance(data, dict):
        return []
    if "suppressions" in data:
        if warnings_out is not None:
            warnings_out.append(
                f"sarif-suppressions: {path_str!r}: legacy "
                f"{{'suppressions': [...]}} envelope shape pre-dates "
                f"the finding_id discriminator; typed surface cannot "
                f"project these rows onto FindingIdSuppression. Migrate "
                f"to the canonical {{finding_id_hex: {{...}}}} shape or "
                f"use the legacy dict loader."
            )
        return []

    if not (data and all(isinstance(v, dict) for v in data.values())):
        if warnings_out is not None and data:
            non_dict = sum(1 for v in data.values() if not isinstance(v, dict))
            warnings_out.append(
                f"sarif-suppressions: {path_str!r}: root mapping has "
                f"{non_dict} non-dict entries; expected the canonical "
                f"{{finding_id_hex: {{...}}}} shape. Skipping file."
            )
        return []

    out: list[FindingIdSuppression] = []
    for fid, entry in data.items():
        rule_id = entry.get("rule_id") or entry.get("ruleId")
        location = entry.get("location")
        # Match the visibility contract of the legacy SARIF loader: an
        # entry without rule_id + location is invisible to SARIF.
        if not (rule_id and location):
            continue
        out.append(FindingIdSuppression.from_dict(str(fid), entry))
    return out


def _apply_suppressions(results: list[dict], suppressions: list[dict]) -> list[dict]:
    """Stamp each matching result with the SARIF suppressions array.

    A result matches when (rule_id, primary location) equals an entry in
    the suppressions list.
    """
    suppression_map: dict[tuple[str, str], dict] = {}
    for s in suppressions:
        rule_id = s.get("rule_id") or s.get("ruleId") or ""
        loc = s.get("location") or ""
        if rule_id:
            suppression_map[(rule_id, loc)] = s

    if not suppression_map:
        return results

    for r in results:
        rule_id = r.get("ruleId") or ""
        # Pull the primary location's file:line
        loc_key = ""
        try:
            phys = r["locations"][0]["physicalLocation"]
            uri = phys["artifactLocation"]["uri"]
            line = phys.get("region", {}).get("startLine")
            loc_key = f"{uri}:{line}" if line else uri
        except (KeyError, IndexError, TypeError):
            pass
        match = suppression_map.get((rule_id, loc_key))
        if match:
            r["suppressions"] = [
                {
                    "kind": match.get("kind", "external"),
                    "status": match.get("status", "accepted"),
                    "justification": match.get("reason", ""),
                }
            ]
    return results


def _apply_suppressions_typed(results: list[dict], suppressions: list) -> list[dict]:
    """Typed counterpart of :func:`_apply_suppressions` (W736 Phase C-1a).

    Same matching semantics + same output bytes as the legacy dict
    applier above. The only change is the *input* type: a list of
    :class:`roam.policy.suppression_v2.FindingIdSuppression` dataclasses
    rather than a list of raw dicts. The output bytes are bit-identical
    to the legacy path so the SARIF hash-stability mandate holds.

    Defaults preserve the legacy behaviour:

    * ``kind`` defaults to ``"external"`` (FindingIdSuppression does
      not carry a ``kind`` field; the writer never sets one).
    * ``status`` defaults to ``"accepted"`` when the dataclass coerced
      the on-disk value to ``None`` (i.e. anything outside the closed
      ``{safe, acknowledged, wont-fix}`` enumeration, including the
      legacy unwritten case).
    """
    suppression_map: dict[tuple[str, str], "object"] = {}
    for s in suppressions:
        rule_id = s.rule_id or ""
        loc = s.location or ""
        if rule_id:
            suppression_map[(rule_id, loc)] = s

    if not suppression_map:
        return results

    for r in results:
        rule_id = r.get("ruleId") or ""
        # Pull the primary location's file:line
        loc_key = ""
        try:
            phys = r["locations"][0]["physicalLocation"]
            uri = phys["artifactLocation"]["uri"]
            line = phys.get("region", {}).get("startLine")
            loc_key = f"{uri}:{line}" if line else uri
        except (KeyError, IndexError, TypeError):
            pass
        match = suppression_map.get((rule_id, loc_key))
        if match is not None:
            r["suppressions"] = [
                {
                    "kind": "external",
                    "status": match.status or "accepted",
                    "justification": match.reason or "",
                }
            ]
    return results


def _build_rule(rule: dict) -> dict:
    """Normalise a rule dict into the SARIF rule schema."""
    out: dict = {
        "id": rule["id"],
        "shortDescription": {"text": rule["shortDescription"]},
    }
    if "helpUri" in rule:
        out["helpUri"] = rule["helpUri"]
    if "defaultLevel" in rule:
        out["defaultConfiguration"] = {"level": rule["defaultLevel"]}
    if "properties" in rule:
        out["properties"] = rule["properties"]
    return out


# ── Write / serialise ────────────────────────────────────────────────


def write_sarif(data: dict, output_path: str | Path | None = None) -> str:
    """Serialise *data* to JSON and optionally write it to *output_path*.

    Returns the JSON string in all cases.
    """
    text = _json.dumps(data, indent=2, default=str)
    if output_path is not None:
        Path(output_path).write_text(text, encoding="utf-8")
    return text


# ── Fitness violations ───────────────────────────────────────────────


def fitness_to_sarif(violations: list[dict]) -> dict:
    """Convert fitness-rule violations to SARIF.

    Each *violation* dict is expected to carry:

    - ``rule`` (str): rule name
    - ``type`` (str): ``"dependency"`` / ``"metric"`` / ``"naming"``
    - ``message`` (str): human-readable detail
    - ``source`` (str, optional): ``"path:line"`` location string
    """
    seen_rules: dict[str, dict] = {}
    results: list[dict] = []

    for v in violations:
        rule_id = f"fitness/{v.get('type', 'unknown')}/{_slugify(v.get('rule', 'unnamed'))}"
        if rule_id not in seen_rules:
            seen_rules[rule_id] = _rule_entry(
                id=rule_id,
                short_desc=v.get("rule", "Fitness rule violation"),
                help_uri=_HELP_BASE + "fitness",
                default_level="warning",
            )

        locations = []
        src = v.get("source", "")
        if src:
            fpath, line = _parse_loc_string(src)
            locations.append(_location(fpath, line))

        results.append(
            _result_entry(
                rule_id=rule_id,
                severity="warning",
                locations=locations,
                message=v.get("message", "Fitness rule violation"),
                level_mapper=lambda s: s,
            )
        )

    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        list(seen_rules.values()),
        results,
    )


# ── Dead code ────────────────────────────────────────────────────────


def dead_to_sarif(dead_exports: list[dict]) -> dict:
    """Convert dead-code findings to SARIF.

    Each *dead_export* dict is expected to carry:

    - ``name`` (str): symbol name
    - ``kind`` (str): symbol kind (function, class, ...)
    - ``location`` (str): ``"path:line"`` location string
    - ``action`` (str, optional): ``"SAFE"`` / ``"REVIEW"`` / ``"INTENTIONAL"``

    W1062-followup-2 dashboard-filtering tags
    -----------------------------------------

    Each rule + result carries ``properties.tags[]`` shaped as
    ``["hygiene", "dead-code", "<action-slug>", "<level>"]`` so a
    dashboard (GitHub Code Scanning / SonarQube) can slice the
    dead-code finding stream by family (``hygiene``) / category
    (``dead-code``) / action (``safe`` / ``review``) / SARIF level
    (``warning`` / ``note``). Dead-code findings have no CWE / OWASP
    anchor — that's expected; family + category + action gives triage
    users the chips they need to separate the ``SAFE`` removal
    candidates from the ``REVIEW`` set.
    """
    rule_id = "dead-code/unreferenced-export"
    # W1062-followup-2: rule-level tags carry the family + category
    # axes so dashboards grouping by rule still get the filter chips
    # even before any specific result lands. The action / level axes
    # are per-result and stamped below.
    rule_tags = _derive_finding_tags(family="hygiene", extra=["dead-code"])
    rules = [
        _rule_entry(
            id=rule_id,
            short_desc="Exported symbol has no references",
            help_uri=_HELP_BASE + "dead",
            default_level="warning",
            properties={"tags": list(rule_tags)},
        )
    ]

    results: list[dict] = []
    for item in dead_exports:
        action = item.get("action", "REVIEW")
        if action in ("INTENTIONAL", "INTENTIONAL_SCAFFOLDING"):
            continue

        # Dead-code level is keyed on the action label (SAFE -> warning,
        # everything else -> note), not on a roam severity string, so
        # we pass an identity ``level_mapper`` and pre-resolve the level
        # at the call site.
        level = "warning" if action == "SAFE" else "note"
        fpath, line = _parse_loc_string(item.get("location", ""))

        locations = []
        if fpath:
            locations.append(_location(fpath, line))

        # W1062-followup-2: per-result tags add the action + SARIF level
        # axes. ``action`` is uppercase producer-side (``SAFE`` /
        # ``REVIEW``) — the helper normalises to ``safe`` / ``review``
        # via _normalize_tag so the dashboard chip vocabulary stays
        # lowercase-hyphen.
        result_tags = _derive_finding_tags(
            family="hygiene",
            extra=["dead-code", action],
            severity=level,
        )
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=level,
                locations=locations,
                message=(f"Unreferenced export: {item.get('kind', '?')} '{item.get('name', '?')}' ({action})"),
                level_mapper=lambda s: s,
                properties={"tags": list(result_tags)},
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Stale references ─────────────────────────────────────────────────


def stale_refs_to_sarif(targets: list[dict]) -> dict:
    """Convert ``stale-refs`` findings to SARIF.

    Each *target* dict is expected to carry:

    - ``target`` (str): the missing path the references point at
    - ``ref_count`` (int): how many references point at it
    - ``rename_hint`` (str, optional): basename-match suggestion
    - ``sources`` (list[dict]): per-source records ``{file, line, kind, raw}``

    Each source becomes one SARIF result, ruleId scoped by reference kind
    (``stale-refs/md_inline``, ``stale-refs/backtick``, …) so GitHub Code
    Scanning surfaces them as discrete categories.
    """
    rule_specs = {
        "md_inline": ("Markdown link target missing", "warning"),
        "md_reference": ("Markdown reference-style link target missing", "warning"),
        "html_attr": ("HTML href/src target missing", "warning"),
        "backtick": ("Backtick-wrapped path target missing", "note"),
        "anchor": ("Markdown anchor / fragment missing in target file", "note"),
        "external": ("External http(s) URL unreachable", "warning"),
    }

    seen_rules: dict[str, dict] = {}
    results: list[dict] = []

    for tgt in targets:
        target_path = tgt.get("target", "?")
        rename_hint = tgt.get("rename_hint")
        for src in tgt.get("sources", []):
            kind = src.get("kind", "md_inline")
            spec = rule_specs.get(kind, ("Stale file reference", "warning"))
            rule_id = f"stale-refs/{kind}"
            if rule_id not in seen_rules:
                seen_rules[rule_id] = _rule_entry(
                    id=rule_id,
                    short_desc=spec[0],
                    help_uri=_HELP_BASE + "stale-refs",
                    default_level=spec[1],
                )
            if kind == "anchor":
                anchor = src.get("anchor", "?")
                anchor_file = src.get("anchor_target_file", target_path.split("#", 1)[0])
                message = f"Anchor '#{anchor}' not found in '{anchor_file}' (raw: '{src.get('raw', '?')}')"
            else:
                message = f"Reference points at missing target '{target_path}' (raw: '{src.get('raw', '?')}')"
            if rename_hint and kind != "anchor":
                message += f". Rename hint: {rename_hint}"
            results.append(
                _result_entry(
                    rule_id=rule_id,
                    severity=spec[1],
                    locations=[_location(src.get("file", ""), src.get("line"))],
                    message=message,
                    level_mapper=lambda s: s,
                )
            )

    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        list(seen_rules.values()),
        results,
    )


# ── Complexity ───────────────────────────────────────────────────────


def complexity_to_sarif(
    complex_symbols: list[dict],
    threshold: float = 25,
    *,
    warnings: list[str] | None = None,
) -> dict:
    """Convert complexity findings to SARIF.

    Each *complex_symbol* dict is expected to carry:

    - ``name`` (str): symbol (qualified) name
    - ``kind`` (str): symbol kind
    - ``file`` (str): file path
    - ``line`` (int | None): line number
    - ``cognitive_complexity`` (float): the computed score
    - ``severity`` (str, optional): ``"CRITICAL"`` / ``"HIGH"`` / ``"MEDIUM"`` / ``"LOW"``

    *warnings* (W1060): producer-side advisory warnings (Pattern 1B /
    Pattern 2 silent-fallback disclosures from ``cmd_complexity``'s
    ``warnings`` accumulator — count-probe failure, pre-W89 findings-table
    missing on ``--persist``, ...). When non-empty, they are projected
    onto the SARIF ``run.invocations[].toolExecutionNotifications[]``
    array via :func:`to_sarif`'s W1046 opt-in. Hash invariant: when
    ``warnings`` is ``None`` or empty, the SARIF output is byte-identical
    to pre-W1060 because ``emit_runtime_notifications=bool([]) is False``,
    which prevents :func:`to_sarif` from adding the ``invocations`` key.
    """
    rule_id = "complexity/cognitive-complexity"
    rules = [
        _rule_entry(
            id=rule_id,
            short_desc=f"Cognitive complexity exceeds threshold ({threshold})",
            help_uri=_HELP_BASE + "complexity",
            default_level="warning",
        )
    ]

    results: list[dict] = []
    for sym in complex_symbols:
        score = sym.get("cognitive_complexity", 0)
        if score < threshold:
            continue

        severity = sym.get("severity", "HIGH" if score >= 25 else "MEDIUM")
        fpath = sym.get("file", "")
        line = sym.get("line")

        locations = []
        if fpath:
            locations.append(_location(fpath, line))

        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=severity,
                locations=locations,
                message=(
                    f"{sym.get('kind', '?')} '{sym.get('name', '?')}' "
                    f"has cognitive complexity {score:.0f} "
                    f"(threshold {threshold})"
                ),
            )
        )

    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        rules,
        results,
        emit_runtime_notifications=bool(warnings),
        warnings_out=warnings or [],
    )


# ── Health issues ────────────────────────────────────────────────────


def health_to_sarif(
    issues: dict,
    *,
    emit_runtime_notifications: bool = False,
    warnings_out: list[str] | None = None,
) -> dict:
    """Convert health-check results to SARIF.

    *issues* is expected to carry:

    - ``cycles`` (list[dict]): each with ``size``, ``severity``,
      ``symbols`` (list[str]), ``files`` (list[str])
    - ``god_components`` (list[dict]): each with ``name``, ``kind``,
      ``degree``, ``file``, ``severity``
    - ``bottlenecks`` (list[dict]): each with ``name``, ``kind``,
      ``betweenness``, ``file``, ``severity``
    - ``layer_violations`` (list[dict], optional): each with ``source``,
      ``source_layer``, ``target``, ``target_layer``, ``severity``

    *emit_runtime_notifications* / *warnings_out* (W1084 — mirrors the W1060
    plumb in :func:`complexity_to_sarif`): caller-supplied producer-side
    advisory warnings (Pattern 1B / Pattern 2 silent-fallback disclosures
    from ``cmd_health``'s ``_gate_warnings`` accumulator — malformed
    ``.roam-gates.yml`` shape, missing ``health`` key, ...). When
    ``emit_runtime_notifications=True`` AND ``warnings_out`` is non-empty,
    each string is projected onto
    ``run.invocations[].toolExecutionNotifications[]`` via
    :func:`to_sarif`'s W1046 surface with
    ``descriptor.id: "producer.advisory-warning"``. Hash invariant:
    omitting the kwargs (or passing ``warnings_out=None`` /
    ``warnings_out=[]``) produces SARIF output byte-identical to pre-W1084
    because :func:`to_sarif` only adds the ``invocations`` key when the
    opt-in flag is True.
    """
    rules = [
        _rule_entry(
            id="health/cycle",
            short_desc="Dependency cycle detected",
            help_uri=_HELP_BASE + "health",
            default_level="warning",
        ),
        _rule_entry(
            id="health/god-component",
            short_desc="God component with excessive coupling",
            help_uri=_HELP_BASE + "health",
            default_level="warning",
        ),
        _rule_entry(
            id="health/bottleneck",
            short_desc="High-betweenness bottleneck symbol",
            help_uri=_HELP_BASE + "health",
            default_level="warning",
        ),
        _rule_entry(
            id="health/layer-violation",
            short_desc="Architectural layer violation",
            help_uri=_HELP_BASE + "health",
            default_level="warning",
        ),
    ]

    results: list[dict] = []

    # Cycles
    for cyc in issues.get("cycles", []):
        severity = cyc.get("severity", "WARNING")
        symbols = cyc.get("symbols", [])
        files = cyc.get("files", [])
        symbol_names = ", ".join(symbols[:5])
        if len(symbols) > 5:
            symbol_names += f" (+{len(symbols) - 5} more)"

        # Attach locations for every file in the cycle
        locations = [_location(f, None) for f in files]

        results.append(
            _result_entry(
                rule_id="health/cycle",
                severity=severity,
                locations=locations,
                message=f"Dependency cycle of {cyc.get('size', '?')} symbols: {symbol_names}",
            )
        )

    # God components
    for g in issues.get("god_components", []):
        severity = g.get("severity", "WARNING")
        fpath = g.get("file", "")
        locations = []
        if fpath:
            locations.append(_location(fpath, None))

        results.append(
            _result_entry(
                rule_id="health/god-component",
                severity=severity,
                locations=locations,
                message=(f"God component: {g.get('kind', '?')} '{g.get('name', '?')}' (degree {g.get('degree', '?')})"),
            )
        )

    # Bottlenecks
    for b in issues.get("bottlenecks", []):
        severity = b.get("severity", "WARNING")
        fpath = b.get("file", "")
        locations = []
        if fpath:
            locations.append(_location(fpath, None))

        results.append(
            _result_entry(
                rule_id="health/bottleneck",
                severity=severity,
                locations=locations,
                message=(
                    f"Bottleneck: {b.get('kind', '?')} '{b.get('name', '?')}' (betweenness {b.get('betweenness', '?')})"
                ),
            )
        )

    # Layer violations
    for v in issues.get("layer_violations", []):
        severity = v.get("severity", "WARNING")

        results.append(
            _result_entry(
                rule_id="health/layer-violation",
                severity=severity,
                locations=[],
                message=(
                    f"Layer violation: {v.get('source', '?')} "
                    f"(L{v.get('source_layer', '?')}) -> "
                    f"{v.get('target', '?')} "
                    f"(L{v.get('target_layer', '?')})"
                ),
            )
        )

    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        rules,
        results,
        emit_runtime_notifications=emit_runtime_notifications or bool(warnings_out),
        warnings_out=warnings_out or [],
    )


# ── Rules violations ─────────────────────────────────────────────────


def rules_to_sarif(
    rule_results: list[dict],
    *,
    emit_runtime_notifications: bool = False,
    warnings_out: list[str] | None = None,
    runtime_overrides: list[dict] | None = None,
    runtime_notification_overrides: list[dict] | None = None,
) -> dict:
    """Convert custom governance rule results to SARIF.

    Each *rule_result* dict is expected to carry:

    - ``name`` (str): rule name
    - ``passed`` (bool): whether the rule passed
    - ``severity`` (str): ``"error"`` / ``"warning"`` / ``"info"``
    - ``violations`` (list[dict], optional): each with ``symbol``, ``file``,
      ``line``, ``reason``

    *emit_runtime_notifications* / *warnings_out* (W1114): producer-side
    advisory warnings (Pattern 1B / Pattern 2 silent-fallback disclosures
    from ``cmd_rules`` / ``cmd_check_rules`` YAML-loader accumulators —
    malformed ``.roam-rules.yml`` / ``.roam/rules/*.yml`` files that were
    skipped). When non-empty AND ``emit_runtime_notifications=True``, each
    string is projected onto the SARIF
    ``run.invocations[].toolExecutionNotifications[]`` array via
    :func:`to_sarif`'s W1046 opt-in. Hash invariant: when both kwargs are
    omitted (or ``warnings_out`` is ``None``/empty and the flag stays
    ``False``), the SARIF output is byte-identical to pre-W1114 callers
    because :func:`to_sarif` then suppresses the ``invocations`` key
    entirely.

    W1061-followup — *runtime_overrides* carries pre-built SARIF
    ``configurationOverride`` dicts (§3.51) for rule-id-level filters
    that disabled specific rules at dispatch time (e.g.
    ``--rule R1 --severity error`` on ``cmd_check_rules``). They project
    onto ``run.invocations[0].ruleConfigurationOverrides[]``.
    *runtime_notification_overrides* carries the same shape but for
    FINDING-LEVEL filters (per SARIF 2.1.0 §3.20.4 sibling slot). Default
    ``None`` keeps SARIF bytes byte-identical to the pre-W1061-followup
    callers via the gated emission inside :func:`to_sarif`.
    """
    seen_rules: dict[str, dict] = {}
    results: list[dict] = []

    for r in rule_results:
        if r.get("passed", True):
            continue

        rule_name = r.get("name", "unnamed")
        severity = r.get("severity", "warning")
        rule_id = f"rules/{_slugify(rule_name)}"

        if rule_id not in seen_rules:
            seen_rules[rule_id] = _rule_entry(
                id=rule_id,
                short_desc=rule_name,
                help_uri=_HELP_BASE + "rules",
                default_level=_to_level(severity),
            )

        for v in r.get("violations", []):
            fpath = v.get("file", "")
            line = v.get("line")
            locations = []
            if fpath:
                locations.append(_location(fpath, line))

            symbol = v.get("symbol", "")
            reason = v.get("reason", "")
            msg = f"Rule '{rule_name}'"
            if symbol:
                msg += f": {symbol}"
            if reason:
                msg += f" - {reason}"

            results.append(
                _result_entry(
                    rule_id=rule_id,
                    severity=severity,
                    locations=locations,
                    message=msg,
                )
            )

    # W1061-followup: forward runtime overrides when the caller (typically
    # ``cmd_check_rules``) captured filter state. Each branch is gated on a
    # non-empty list so the default (no overrides) path stays byte-identical
    # to pre-W1061-followup. Composes cleanly with the W1114 runtime
    # notifications opt-in above.
    overrides = list(runtime_overrides or ())
    notif_overrides = list(runtime_notification_overrides or ())
    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        list(seen_rules.values()),
        results,
        emit_runtime_notifications=emit_runtime_notifications,
        warnings_out=warnings_out,
        emit_configuration_overrides=bool(overrides),
        configuration_overrides=overrides if overrides else None,
        notification_configuration_overrides=notif_overrides if notif_overrides else None,
    )


# ── Taint analysis ────────────────────────────────────────────────────


def taint_to_sarif(
    findings: list[dict],
    *,
    runtime_overrides: list[dict] | None = None,
    runtime_notification_overrides: list[dict] | None = None,
) -> dict:
    """SARIF output for ``roam taint``.

    Each finding becomes one result located at its sink, with a
    code-flow describing the source-to-sink path. One SARIF rule per
    distinct ``rule_id`` (e.g. ``python-sqli``, ``js-xss``). Sanitized
    findings are kept and downgraded to ``note`` so a CI gate can still
    surface them as remediated under OpenVEX.

    Each finding dict is the per-finding shape that ``cmd_taint`` builds
    via its ``findings_dump`` list.

    W1061-followup — *runtime_overrides* carries pre-built SARIF
    ``configurationOverride`` dicts (§3.51) when the caller (typically
    ``cmd_taint``) applied a rule-id-level runtime filter that disabled
    rules at dispatch time (``--rule`` / ``--rules-pack`` substring
    match against rule_id). They project onto
    ``run.invocations[0].ruleConfigurationOverrides[]``.
    *runtime_notification_overrides* covers finding-level filters
    (alternate ``--rules-dir``). Default ``None`` keeps SARIF
    byte-identical to pre-W1061-followup output via gated emission in
    :func:`to_sarif`.
    """
    seen_rules: dict[str, dict] = {}
    results: list[dict] = []

    for f in findings:
        rule_id = f.get("rule_id", "taint/unknown")
        severity = f.get("severity", "warning")
        cwe = f.get("cwe") or ""
        owasp = f.get("owasp_top10") or ""
        sanitized = bool(f.get("sanitizer_in_path"))
        # Sanitized findings are downgraded to note so they don't
        # break a CI gate that fails on warnings/errors.
        level = "note" if sanitized else _to_level(severity)

        # W453 + W1062: build the per-finding tags list once and reuse
        # it on both the rule (first time we see it) and the result.
        # SARIF stores tags under `properties.tags` as a list of strings;
        # GitHub Code Scanning + SonarQube + security-dashboard tools
        # surface them in the UI as filter chips so triage users can
        # slice findings by CWE / OWASP category. W1062 routes through
        # the canonical ``_derive_finding_tags`` helper so the raw
        # producer-side ``CWE-89`` / ``A03:2021_Injection`` strings
        # converge on the URL-safe lowercase-hyphen vocabulary
        # (``cwe-89`` / ``owasp-a03``) every emitter agrees on.
        tags = _derive_finding_tags(
            cwe=cwe,
            owasp_top10=owasp,
            family="security",
            extra=["taint"],
        )

        if rule_id not in seen_rules:
            short = f"Taint: {rule_id}"
            if cwe:
                short += f" ({cwe})"
            rule_entry = _rule_entry(
                id=rule_id,
                short_desc=short,
                help_uri=_HELP_BASE + "taint",
                default_level=_to_level(severity),
                properties={"tags": list(tags)},
            )
            seen_rules[rule_id] = rule_entry

        sink = f.get("sink") or {}
        sink_file = sink.get("file") or ""
        sink_line = sink.get("line")
        locations = [_location(sink_file, sink_line)] if sink_file else []

        # Build a SARIF code-flow from the source → sink hops so the
        # GitHub Code Scanning UI shows the actual path.
        thread_locations = []
        for step in f.get("path", []) or []:
            sf = step.get("file") or ""
            sl = step.get("line")
            if not sf:
                continue
            thread_locations.append(
                {
                    "location": _location(sf, sl),
                    "module": step.get("name") or "",
                }
            )

        src = f.get("source") or {}
        sink_name = sink.get("name") or "<sink>"
        src_name = src.get("name") or "<source>"
        msg_parts = [f"Tainted flow: {src_name} → {sink_name}"]
        if sanitized:
            vex = f.get("vex_justification")
            msg_parts.append(f"(sanitized; OpenVEX: {vex})" if vex else "(sanitized)")

        # W453: result.properties.tags[] — the SARIF idiom for free-form
        # categorisation tags. GitHub Code Scanning + VSCode SARIF
        # Viewer render these as filter chips. Always present
        # ("security", "taint" at minimum); CWE / OWASP appended when
        # the rule declares them.
        result = _result_entry(
            rule_id=rule_id,
            severity=level,
            locations=locations,
            message=" ".join(msg_parts),
            level_mapper=lambda s: s,
            properties={"tags": list(tags)},
        )
        if thread_locations:
            result["codeFlows"] = [{"threadFlows": [{"locations": thread_locations}]}]
        results.append(result)

    # W1061-followup: forward runtime overrides when the caller captured
    # filter state. ``--rule`` / ``--rules-pack`` produce rule-id-level
    # disables (``ruleConfigurationOverrides``); ``--rules-dir`` is a
    # finding-level filter (``notificationConfigurationOverrides``). Each
    # branch gated on non-empty so the default path stays byte-identical
    # to pre-W1061-followup SARIF output.
    overrides = list(runtime_overrides or ())
    notif_overrides = list(runtime_notification_overrides or ())
    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        list(seen_rules.values()),
        results,
        emit_configuration_overrides=bool(overrides),
        configuration_overrides=overrides if overrides else None,
        notification_configuration_overrides=notif_overrides if notif_overrides else None,
    )


# ── Secret scanning ──────────────────────────────────────────────────


def py_types_to_sarif(by_file: list[dict], coverage_pct: int) -> dict:
    """SARIF output for ``roam py-types``.

    Each per-file row produces a ``note``-level finding when the file
    has any missing annotations. Single rule ``py-types/coverage``
    so consumers can suppress/configure uniformly.
    """
    rule_id = "py-types/coverage"
    rules = [
        _rule_entry(
            id=rule_id,
            short_desc="Public function/method missing type annotations",
            help_uri="https://github.com/Cranot/roam-code#roam-py-types",
            default_level="note",
        )
    ]
    results = []
    for row in by_file:
        path = row.get("path", "")
        total = row.get("total", 0) or 0
        missing = row.get("missing", 0) or 0
        if missing <= 0:
            continue
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity="note",
                locations=[_location(path, 1)],
                message=(
                    f"{missing}/{total} public fn/methods missing annotations "
                    f"({(missing * 100 // total) if total else 0}% incomplete). "
                    f"Project coverage: {coverage_pct}%."
                ),
                level_mapper=lambda s: s,
            )
        )
    return to_sarif("roam-py-types", "1.0.0", rules, results)


def py_modern_to_sarif(by_file: list[dict], type_modernisation_pct: int) -> dict:
    """SARIF output for ``roam py-modern`` — flags files using legacy
    ``typing.Optional/Dict/List/...`` instead of PEP 585/604.
    """
    rules = [
        _rule_entry(
            id="py-modern/legacy-typing",
            short_desc="File uses legacy typing.Optional/Dict/List instead of PEP 585/604",
            help_uri="https://github.com/Cranot/roam-code#roam-py-modern",
            default_level="note",
        ),
        _rule_entry(
            id="py-modern/dot-format",
            short_desc="File uses ``.format()`` instead of f-strings",
            help_uri="https://github.com/Cranot/roam-code#roam-py-modern",
            default_level="note",
        ),
    ]
    results = []
    for row in by_file:
        path = row.get("path", "")
        if (row.get("legacy_typing") or 0) > 0:
            results.append(
                _result_entry(
                    rule_id="py-modern/legacy-typing",
                    severity="note",
                    locations=[_location(path, 1)],
                    message=(
                        f"{row['legacy_typing']} legacy ``typing.X[]`` usage(s); "
                        f"prefer PEP 585 (``dict[…]``) / PEP 604 (``X | None``). "
                        f"Project type modernisation: {type_modernisation_pct}%."
                    ),
                    level_mapper=lambda s: s,
                )
            )
        if (row.get("dot_format") or 0) > 0:
            results.append(
                _result_entry(
                    rule_id="py-modern/dot-format",
                    severity="note",
                    locations=[_location(path, 1)],
                    message=(f"{row['dot_format']} ``.format(…)`` call(s); prefer f-strings (PEP 498)."),
                    level_mapper=lambda s: s,
                )
            )
    return to_sarif("roam-py-modern", "1.0.0", rules, results)


def secrets_to_sarif(findings: list[dict]) -> dict:
    """Convert secret-scanning findings to SARIF.

    Each *finding* dict is expected to carry:

    - ``file`` (str): relative file path
    - ``line`` (int): line number
    - ``severity`` (str): ``"high"`` / ``"medium"`` / ``"low"``
    - ``pattern_name`` (str): human-readable pattern name
    - ``matched_text`` (str): masked matched text (safe to include)

    W1062 dashboard-filtering tags
    ------------------------------

    Each rule + result carries ``properties.tags[]`` shaped as
    ``["security", "secret", "<pattern-slug>", "<severity>"]`` so a
    security dashboard (GitHub Code Scanning, SonarQube, security
    dashboards) can slice the finding stream by detector family
    (``security``) / category (``secret``) / pattern slug
    (``aws-access-key``) / severity (``high``). Secret findings have
    no CWE / OWASP anchors — that's expected; the family + category +
    pattern axes already give triage users the chips they need.
    """
    seen_rules: dict[str, dict] = {}
    results: list[dict] = []

    for f in findings:
        pattern_name = f.get("pattern_name", f.get("pattern", "unknown"))
        # Pattern slug doubles as the rule-id suffix AND as a W1062
        # dashboard tag, so a triage user filtering by ``aws-access-key``
        # sees the rule AND every result in one chip.
        pattern_slug = _slugify(pattern_name)
        rule_id = f"secrets/{pattern_slug}"
        severity = f.get("severity", "medium")

        # W1062: per-finding tags. ``family="security"`` anchors the
        # dashboard's broad axis; ``extra=["secret", pattern_slug]``
        # adds the detector category + the specific pattern slug;
        # severity passes through ``_normalize_tag`` so producer-side
        # uppercase variants (e.g. ``"HIGH"``) converge on lowercase
        # chips.
        tags = _derive_finding_tags(
            family="security",
            extra=["secret", pattern_slug],
            severity=severity,
        )

        if rule_id not in seen_rules:
            seen_rules[rule_id] = _rule_entry(
                id=rule_id,
                short_desc=f"Hardcoded secret: {pattern_name}",
                help_uri=_HELP_BASE + "secrets",
                default_level=_to_level(severity),
                properties={"tags": list(tags)},
            )

        fpath = f.get("file", "")
        line = f.get("line")
        locations = []
        if fpath:
            locations.append(_location(fpath, line))

        matched = f.get("matched_text", "")
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=severity,
                locations=locations,
                message=f"Hardcoded {pattern_name} detected: {matched}",
                properties={"tags": list(tags)},
            )
        )

    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        list(seen_rules.values()),
        results,
    )


# ── Algorithmic findings ─────────────────────────────────────────────


def algo_to_sarif(
    findings: list[dict],
    detector_metadata: dict[str, dict] | None = None,
) -> dict:
    """Convert ``roam algo`` findings to SARIF."""
    detector_metadata = detector_metadata or {}
    seen_rules: dict[str, dict] = {}
    results: list[dict] = []

    for f in findings:
        task_id = f.get("task_id", "unknown")
        rule_id = f"algo/{_slugify(task_id)}"

        dmeta = detector_metadata.get(task_id, {})
        precision = f.get("precision", dmeta.get("precision", "medium"))
        impact = f.get("impact", dmeta.get("impact", "medium"))
        tags = f.get("tags", dmeta.get("tags", []))

        if rule_id not in seen_rules:
            short_desc = f"Algorithm improvement opportunity: {task_id}"
            if f.get("suggested_way"):
                short_desc = f"Prefer {f.get('suggested_way')} over {f.get('detected_way')}"
            # Rule descriptor: id / shortDescription / helpUri /
            # defaultLevel via _rule_entry; per-rule ``properties``
            # (precision / impact / tags) is attached post-construction
            # because the W1178 helper doesn't model rule-level
            # properties.
            rule = _rule_entry(
                id=rule_id,
                short_desc=short_desc,
                help_uri=_HELP_BASE + "algo",
                default_level=_algo_level(f.get("confidence", "medium")),
            )
            rule["properties"] = {
                "precision": precision,
                "impact": impact,
                "tags": tags,
            }
            seen_rules[rule_id] = rule

        loc_str = f.get("location", "")
        fpath, line = _parse_loc_string(loc_str)
        locations = []
        if fpath:
            locations.append(_location(fpath, line))

        # surface matched_patterns in SARIF properties
        # so CI dashboards (GitHub Code Scanning) can show WHY a finding
        # fired without an extra round-trip to the JSON envelope.
        properties = {
            "task_id": task_id,
            "detected_way": f.get("detected_way", ""),
            "suggested_way": f.get("suggested_way", ""),
            "confidence": f.get("confidence", ""),
            "precision": precision,
            "impact_band": f.get("impact_band", ""),
            "impact_score": f.get("impact_score", 0.0),
        }
        matched_patterns = (f.get("evidence") or {}).get("matched_patterns") or []
        if matched_patterns:
            properties["matched_patterns"] = matched_patterns
        # algo pre-resolves the level via :func:`_algo_level` (confidence-band
        # mapping); pass an identity ``level_mapper`` so the helper doesn't
        # double-translate. ``partialFingerprints`` / ``properties`` flow as
        # **extras with the historical key order
        # (properties -> partialFingerprints).
        result = _result_entry(
            rule_id=rule_id,
            severity=_algo_level(f.get("confidence", "medium")),
            locations=locations,
            message=_algo_message(f),
            level_mapper=lambda s: s,
            properties=properties,
            partialFingerprints={
                "primaryLocationLineHash": _primary_location_line_hash(f),
                "roamFindingFingerprint/v1": _finding_fingerprint(f),
            },
        )

        evidence_path = f.get("evidence_path", [])
        if evidence_path and fpath:
            flow_locations = [
                {
                    "location": _location(fpath, line),
                    "message": {"text": str(step)},
                }
                for step in evidence_path
            ]
            result["codeFlows"] = [
                {
                    "threadFlows": [{"locations": flow_locations}],
                }
            ]

        fix = f.get("fix", "")
        if fix and fpath:
            start_line = line if isinstance(line, int) and line > 0 else 1
            result["fixes"] = [
                {
                    "description": {"text": "Suggested refactor template"},
                    "artifactChanges": [
                        {
                            "artifactLocation": {"uri": fpath.replace("\\", "/")},
                            "replacements": [
                                {
                                    "deletedRegion": {"startLine": start_line},
                                    "insertedContent": {"text": fix},
                                }
                            ],
                        }
                    ],
                }
            ]

        results.append(result)

    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        list(seen_rules.values()),
        results,
    )


# ── Critique (patch review) ─────────────────────────────────────────


def critique_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam critique`` patch-review findings to SARIF.

    Each *finding* dict is the per-check shape produced by
    :mod:`roam.critique.checks`: ``check`` / ``severity`` / ``title`` /
    ``detail`` / ``evidence``. The check vocabulary is closed (three
    kinds, mirrored from ``cmd_critique._CHECK_TO_KIND``):

    - ``clones-not-edited`` -> rule ``critique/clone-not-edited``
      (defaultLevel ``warning``). Anchored on the changed symbol's file
      via ``evidence.changed_symbol.file``; no line number is recorded
      because the diff-side region is "any clone-sibling edit not
      present" — the persisted ``clone_pairs`` table is the
      authoritative source for sibling locations.
    - ``impact`` -> rule ``critique/blast-radius`` (defaultLevel
      ``warning``). Anchored on the changed symbol via ``evidence.file``
      + ``evidence.line``.
    - ``intent`` -> rule ``critique/intent-mismatch`` (defaultLevel
      ``note``). Diff-wide finding: emits an empty ``locations`` list
      per the SARIF 2.1.0 spec (locations is "optional" — a result
      without one signals "applies to the whole artifact set / run").

    Unknown check labels are skipped silently so a future check addition
    in :mod:`roam.critique.checks` cannot crash the SARIF projection;
    extending the SARIF vocabulary is a deliberate edit to this
    function plus a rule entry in the closed-enum block below.
    """
    rules = [
        _rule_entry(
            id="critique/clone-not-edited",
            short_desc=("Clone sibling of a changed symbol did not receive an analogous edit"),
            help_uri=_HELP_BASE + "critique",
            default_level="warning",
        ),
        _rule_entry(
            id="critique/blast-radius",
            short_desc=("Changed symbol has a high direct-caller count (blast radius)"),
            help_uri=_HELP_BASE + "critique",
            default_level="warning",
        ),
        _rule_entry(
            id="critique/intent-mismatch",
            short_desc=("Stated intent (PR title / commit subject) does not align with the diff's semantic shape"),
            help_uri=_HELP_BASE + "critique",
            default_level="note",
        ),
    ]

    _CHECK_TO_RULE = {
        "clones-not-edited": "critique/clone-not-edited",
        "impact": "critique/blast-radius",
        "intent": "critique/intent-mismatch",
    }

    results: list[dict] = []
    for f in findings:
        check = f.get("check", "")
        rule_id = _CHECK_TO_RULE.get(check)
        if rule_id is None:
            # Unknown check — skip rather than mint a rule on the fly
            # (LAW 8 — closed enumeration over free-string composition).
            continue
        severity = f.get("severity", "info")
        level = _to_level(severity)
        evidence = f.get("evidence") or {}

        # Per-check location extraction. The three check kinds carry
        # different evidence shapes (see roam.critique.checks):
        #   - clones-not-edited: evidence.changed_symbol.file (no line —
        #     the diff-side "region" is the *absence* of an analogous
        #     edit; siblings carry their own file/line in the message
        #     body but the SARIF anchor stays on the changed symbol).
        #   - impact: evidence.file + evidence.line (resolved symbol).
        #   - intent: diff-wide — emit an empty ``locations`` list so
        #     SARIF consumers render this as a "whole run" finding,
        #     not pinned to a particular file.
        locations: list[dict] = []
        if check == "clones-not-edited":
            changed = evidence.get("changed_symbol") or {}
            fpath = changed.get("file", "")
            if fpath:
                locations.append(_location(fpath, None))
        elif check == "impact":
            fpath = evidence.get("file", "")
            line = evidence.get("line")
            if fpath:
                locations.append(_location(fpath, line))
        # else: intent — locations stays empty (diff-wide finding).

        title = f.get("title", "")
        message_text = title or f.get("detail", "") or f"critique {check} finding"

        # critique pre-resolves the level via ``_to_level`` on the
        # severity label above; pass an identity mapper so the helper
        # doesn't double-translate.
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=level,
                locations=locations,
                message=message_text,
                level_mapper=lambda s: s,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Impact (blast radius) ───────────────────────────────────────────


def _impact_importance_level(importance: float) -> str:
    """Map PageRank-style importance score (0.0..1.0 or 0..100) to SARIF level.

    Importance is the per-symbol PageRank value rounded to 6 decimals by
    ``cmd_impact``. Typical values on a 20k-symbol graph fall in the 1e-5
    to 1e-3 range, so the thresholds are tuned to that band. The
    affected-file path stores the *max* importance of any dependent
    symbol in the file (see ``cmd_impact.affected_file_dicts``), which
    keeps the high-leverage files in the "error" / "warning" bands.

    Closed enumeration (LAW 6 — compression forces neutrality):

        >= 0.01   -> "error"    (high-leverage symbol — hub of the graph)
        >= 0.001  -> "warning"  (mid-importance — a refactor needs review)
        < 0.001   -> "note"     (low-importance — informational only)
    """
    try:
        score = float(importance)
    except (TypeError, ValueError):
        return "note"
    if score >= 0.01:
        return "error"
    if score >= 0.001:
        return "warning"
    return "note"


def impact_to_sarif(impact_data: dict) -> dict:
    """Convert ``roam impact`` blast-radius output to SARIF.

    *impact_data* is the JSON envelope built by :mod:`roam.commands.cmd_impact`
    (the ``impact_env`` mapping). Four finding families are projected, each
    onto its own closed-enum rule id:

    - ``impact/affected-file`` (defaultLevel ``warning``): one result per
      ``affected_file_list[]`` entry. Importance is read from the
      ``importance`` field and mapped onto a SARIF level via
      :func:`_impact_importance_level`. File-level anchor (no line).
    - ``impact/direct-dependent`` (defaultLevel ``note``): one result per
      direct-dependent symbol under ``direct_dependents[edge_kind][]``.
      Anchored on the dependent's file; no line (the symbol's
      file_path is stored on each by_kind row but the line is not
      surfaced through the envelope — adding it would require a
      schema change on cmd_impact).
    - ``impact/sf-convention-test`` (defaultLevel ``note``): one result
      per Salesforce convention test file (``sf_convention_tests[]``).
      File-level anchor.
    - ``impact/indirect-ref`` (defaultLevel ``note``): one result per
      string-literal reference site found by
      :func:`cmd_impact._find_indirect_refs`. Each carries ``file`` +
      ``line`` so we anchor with a region.

    A short summary message at the top of every result mentions the
    target symbol (``impact_data["symbol"]``) so SARIF consumers
    correlate the per-file findings to the change that triggered them.

    Empty / leaf-symbol impact data produces a valid SARIF envelope with
    zero results (rules catalogue is always emitted).
    """
    rules = [
        _rule_entry(
            id="impact/affected-file",
            short_desc=("File contains a downstream dependent of the changed symbol"),
            help_uri=_HELP_BASE + "impact",
            default_level="warning",
        ),
        _rule_entry(
            id="impact/direct-dependent",
            short_desc=("Symbol directly calls / imports the changed symbol"),
            help_uri=_HELP_BASE + "impact",
            default_level="note",
        ),
        _rule_entry(
            id="impact/sf-convention-test",
            short_desc=("Salesforce convention test file covers the changed class"),
            help_uri=_HELP_BASE + "impact",
            default_level="note",
        ),
        _rule_entry(
            id="impact/indirect-ref",
            short_desc=("String-literal reference (registry / dispatch) to the changed symbol"),
            help_uri=_HELP_BASE + "impact",
            default_level="note",
        ),
    ]

    symbol = str(impact_data.get("symbol") or "<unknown>")
    results: list[dict] = []

    # Affected files: severity scaled by importance via
    # :func:`_impact_importance_level` (PageRank importance band — closed
    # enum). Pass the helper as ``level_mapper`` so the importance float
    # flows through unchanged.
    for entry in impact_data.get("affected_file_list", []) or []:
        if not isinstance(entry, dict):
            continue
        fpath = entry.get("path") or ""
        if not fpath:
            continue
        importance = entry.get("importance", 0.0)
        results.append(
            _result_entry(
                rule_id="impact/affected-file",
                severity=importance,
                locations=[_location(fpath, None)],
                message=(f"Affected file (importance {importance}): contains downstream dependent(s) of '{symbol}'"),
                level_mapper=_impact_importance_level,
            )
        )

    # Direct dependents (one result per dependent symbol; grouped by edge kind).
    direct_dependents = impact_data.get("direct_dependents") or {}
    if isinstance(direct_dependents, dict):
        for edge_kind, items in direct_dependents.items():
            if not isinstance(items, list):
                continue
            for dep in items:
                if not isinstance(dep, dict):
                    continue
                dep_name = dep.get("name", "?")
                dep_kind = dep.get("kind", "?")
                fpath, line = _parse_loc_string(dep.get("file", "") or "")
                locations = []
                if fpath:
                    locations.append(_location(fpath, line))
                # direct-dependent always note — identity mapper.
                results.append(
                    _result_entry(
                        rule_id="impact/direct-dependent",
                        severity="note",
                        locations=locations,
                        message=(f"Direct dependent ({edge_kind}): {dep_kind} '{dep_name}' calls / imports '{symbol}'"),
                        level_mapper=lambda s: s,
                    )
                )

    # SF convention tests (file-level anchor only).
    for tf in impact_data.get("sf_convention_tests", []) or []:
        if not isinstance(tf, str) or not tf:
            continue
        results.append(
            _result_entry(
                rule_id="impact/sf-convention-test",
                severity="note",
                locations=[_location(tf, None)],
                message=(f"Salesforce convention test covers '{symbol}': {tf}"),
                level_mapper=lambda s: s,
            )
        )

    # Indirect string-literal refs (file + line anchor).
    for ref in impact_data.get("indirect_refs", []) or []:
        if not isinstance(ref, dict):
            continue
        fpath = ref.get("file") or ""
        line = ref.get("line")
        if not fpath:
            continue
        match = ref.get("match", "")
        results.append(
            _result_entry(
                rule_id="impact/indirect-ref",
                severity="note",
                locations=[_location(fpath, line)],
                message=(f"Indirect (string-literal) reference to '{symbol}'" + (f": {match}" if match else "")),
                level_mapper=lambda s: s,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Affected tests ───────────────────────────────────────────────────


def affected_tests_to_sarif(data: dict) -> dict:
    """Convert ``roam affected-tests`` output to SARIF.

    *data* is the JSON envelope built by
    :mod:`roam.commands.cmd_affected_tests`. Each ``tests[]`` entry is
    projected onto one of three closed-enum rule ids, with severity
    determined by the entry's ``kind`` field:

    - ``affected-tests/direct`` (defaultLevel ``error``): test directly
      calls a changed symbol (``kind == "DIRECT"``, hops == 1). Highest
      severity — the test exercises the changed code path with no
      indirection.
    - ``affected-tests/transitive`` (defaultLevel ``warning``): test
      reaches the changed symbol through intermediate callers
      (``kind == "TRANSITIVE"``, hops > 1). The ``via`` field surfaces
      in the message so consumers can see the first hop on the path.
    - ``affected-tests/colocated`` (defaultLevel ``note``): test file
      lives in the same directory as a changed source file (filename
      convention; no graph edge). Weakest signal — included so a
      colocated test that wasn't picked up by the call graph still
      shows up in CI.

    Per-finding anchor: ``tests[].file`` (file-level — the envelope
    does not carry line numbers per W1160). The target symbol
    (``summary.target``) appears in the message so SARIF consumers
    correlate findings to the change that triggered them.

    Empty ``tests[]`` produces a valid SARIF envelope with zero results
    (rules catalogue is always emitted).
    """
    rules = [
        _rule_entry(
            id="affected-tests/direct",
            short_desc=("Test directly exercises the changed symbol"),
            help_uri=_HELP_BASE + "affected-tests",
            default_level="error",
        ),
        _rule_entry(
            id="affected-tests/transitive",
            short_desc=("Test reaches the changed symbol through intermediate callers"),
            help_uri=_HELP_BASE + "affected-tests",
            default_level="warning",
        ),
        _rule_entry(
            id="affected-tests/colocated",
            short_desc=("Test file is colocated with a changed source file"),
            help_uri=_HELP_BASE + "affected-tests",
            default_level="note",
        ),
    ]

    target = str(((data.get("summary") or {}).get("target")) or "<unknown>")
    _kind_to_rule = {
        "DIRECT": ("affected-tests/direct", "error"),
        "TRANSITIVE": ("affected-tests/transitive", "warning"),
        "COLOCATED": ("affected-tests/colocated", "note"),
    }

    results: list[dict] = []
    for entry in data.get("tests", []) or []:
        if not isinstance(entry, dict):
            continue
        fpath = entry.get("file") or ""
        if not fpath:
            continue
        kind = entry.get("kind") or ""
        rule_info = _kind_to_rule.get(kind)
        if rule_info is None:
            # Unknown kind — skip rather than crash.  Closed-enum
            # discipline: if a 4th kind ships, both this mapping and
            # the rules catalogue need to grow in lockstep.
            continue
        rule_id, level = rule_info

        symbol = entry.get("symbol")
        hops = entry.get("hops")
        via = entry.get("via")

        if kind == "DIRECT":
            sym_str = f"::{symbol}" if symbol else ""
            text = f"Direct test ({hops} hop) for '{target}': {fpath}{sym_str}"
        elif kind == "TRANSITIVE":
            sym_str = f"::{symbol}" if symbol else ""
            via_str = f" via {via}" if via else ""
            text = f"Transitive test ({hops} hops{via_str}) for '{target}': {fpath}{sym_str}"
        else:  # COLOCATED
            text = f"Colocated test (same directory) for '{target}': {fpath}"

        # affected-tests pre-resolves the level via the closed-enum
        # _kind_to_rule lookup above; pass an identity ``level_mapper``
        # so the helper doesn't translate again.
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=level,
                locations=[_location(fpath, None)],
                message=text,
                level_mapper=lambda s: s,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Test impact ──────────────────────────────────────────────────────


def _test_impact_reach_level(reach_count: int) -> str:
    """Map per-test ``reach_count`` to a SARIF level (closed enum).

    ``reach_count`` is the number of changed symbols that transitively
    reach a given test file via the reverse call graph (see
    :mod:`roam.commands.cmd_test_impact`). It is a *ranking* signal, not
    a gate-failing one — every band is informational. The two-tier
    note/warning split lets CI consumers visually triage which tests
    cover the most-changed surface without escalating to ``error``
    (LAW 6 — verdict-first compression; ``error`` is reserved for
    gate-failing finding families).

    Closed enumeration:

        >= 20   -> "warning"  (high-impact test — covers many changes)
         5..19  -> "note"     (moderate impact)
         < 5    -> "note"     (low impact — informational ranking)
    """
    if reach_count >= 20:
        return "warning"
    return "note"


def test_impact_to_sarif(data: dict) -> dict:
    """Convert ``roam test-impact`` output to SARIF.

    *data* is the JSON envelope built by :mod:`roam.commands.cmd_test_impact`.
    Each ``tests[]`` entry projects onto the single closed-enum rule
    ``test-impact/affected-test`` (defaultLevel ``note``). Severity is
    scaled by the test's ``reach_count`` field via
    :func:`_test_impact_reach_level`:

    - ``reach_count >= 20`` -> SARIF ``warning`` (high-impact test —
      reachable from many changed symbols; reviewers should pay
      attention).
    - ``reach_count < 20`` -> SARIF ``note`` (moderate / low ranking
      band; informational only).

    The rule is a *ranker*, not a gate — no SARIF ``error`` band is
    emitted because test-impact does not surface a failure mode, only a
    coverage-relevance ranking. CI consumers that want a gate should
    use ``affected-tests`` instead (DIRECT findings escalate to
    ``error`` there).

    Per-finding anchor: ``tests[].file`` — file-level only because the
    cmd_test_impact envelope does not carry per-test line numbers. The
    reach count and the number of changed files (from
    ``data["changed_files"]``) appear in the message so SARIF consumers
    correlate findings to the triggering changeset.

    Empty ``tests[]`` produces a valid SARIF envelope with zero results
    (rules catalogue is always emitted).
    """
    rules = [
        _rule_entry(
            id="test-impact/affected-test",
            short_desc=("Test file is transitively reachable from a changed symbol"),
            help_uri=_HELP_BASE + "test-impact",
            default_level="note",
        ),
    ]

    changed_files = data.get("changed_files") or []
    changed_count = len(changed_files) if isinstance(changed_files, list) else 0

    results: list[dict] = []
    for entry in data.get("tests", []) or []:
        if not isinstance(entry, dict):
            continue
        fpath = entry.get("file") or ""
        if not fpath:
            continue
        try:
            reach_count = int(entry.get("reach_count", 0) or 0)
        except (TypeError, ValueError):
            reach_count = 0

        if changed_count:
            text = f"Test reachable from {reach_count} of {changed_count} changed file(s): {fpath}"
        else:
            text = f"Test reachable from {reach_count} changed symbol(s): {fpath}"

        results.append(
            _result_entry(
                rule_id="test-impact/affected-test",
                severity=reach_count,
                locations=[_location(fpath, None)],
                message=text,
                level_mapper=_test_impact_reach_level,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Smells (code-smell detectors) ────────────────────────────────────


def smells_to_sarif(
    findings: list[dict],
    *,
    runtime_overrides: list[dict] | None = None,
) -> dict:
    """Convert ``roam smells`` detector output to SARIF.

    *findings* is the raw smell-finding list produced by
    :mod:`roam.catalog.smells.run_all_detectors` (8-key dicts with
    ``smell_id`` / ``severity`` / ``symbol_name`` / ``kind`` /
    ``location`` / ``metric_value`` / ``threshold`` / ``description``).
    Each finding projects onto a closed-enum rule id of the form
    ``smells/<smell_id>`` (e.g. ``smells/brain-method``,
    ``smells/god-class``, ``smells/temporal-coupling-cluster``).

    The rules catalogue is built from the
    :mod:`roam.catalog.registry` smell-id -> confidence-tier mapping,
    which is the canonical registry that ``cmd_smells`` and the
    suppression layer also key on (W987 closed-set discipline). One
    rule descriptor per registered smell kind, sorted alphabetically by
    id for SARIF-stable output (mirrors the W896 sort in
    :func:`all_detectors`). Each rule gets ``defaultLevel: "warning"``
    — a neutral middle band; the per-result ``level`` is derived from
    each finding's ``severity`` via :func:`_to_level` so per-result
    severity always overrides the rule default.

    Per-finding severity -> SARIF level (closed mapping via
    :func:`roam.output._severity.to_sarif_level`):

        critical  -> "error"
        warning   -> "warning"
        info      -> "note"

    Per-finding anchor: parsed from the ``location`` ``"path:line"``
    string via :func:`_parse_loc_string`. The ``symbol_name`` + smell
    description appear in the message body so SARIF consumers can
    triage without a JSON-envelope round-trip.

    Unknown smell ids (e.g. plugin-registered detectors that haven't
    landed in :mod:`roam.catalog.registry` yet) are skipped silently —
    extending the SARIF rule vocabulary is a deliberate edit to the
    detector registry, not a free-string composition. Empty
    ``findings`` produces a valid SARIF envelope with zero results
    (rules catalogue is always emitted so consumers can introspect the
    full kind vocabulary even on a clean run).

    W1061 — *runtime_overrides* carries pre-built SARIF
    ``configurationOverride`` dicts (§3.51) when the caller (typically
    ``cmd_smells``) applied a runtime filter that disabled rules. They
    project onto ``run.invocations[0].ruleConfigurationOverrides[]`` so
    consumers (GitHub Code Scanning, Sonar) can read a filtered
    zero-finding result as "filtered" rather than "clean". Default
    ``None`` keeps the SARIF output byte-identical to pre-W1061.
    """
    # Lazy import: keeps the SARIF module's cold-import cost off the
    # critical path of callers that never hit smells. The
    # ``roam.catalog.smells`` import is the side-effectful one — it
    # fires the @detector decorators that populate the registry.
    # ``cmd_smells`` already imports that module before reaching here,
    # but tests / direct callers may not, so we import it explicitly
    # to make this function callable in isolation.
    import roam.catalog.smells  # noqa: F401 — side-effect: populates registry
    from roam.catalog.registry import kind_to_confidence

    known_kinds = sorted(kind_to_confidence().keys())

    # W1062-followup-3: rule-level tags carry the family + category +
    # smell-id axes so dashboards grouping by rule still get the filter
    # chips even before any specific result lands. Severity is
    # per-result and stamped below from the finding's ``severity``
    # field. Smell findings have no CWE / OWASP anchors — that's
    # expected; the family + category + smell_id axes already give
    # triage users the chips they need.
    _smells_rule_tags = {
        smell_id: _derive_finding_tags(
            family="hygiene",
            extra=["smells", smell_id],
        )
        for smell_id in known_kinds
    }

    # One rule descriptor per registered smell kind. defaultLevel
    # is "warning" uniformly — per-result level (derived from
    # finding["severity"]) always overrides. This keeps the rules
    # catalogue closed-by-construction without trying to predict the
    # "typical" severity per kind (which varies by metric threshold
    # within a single detector, e.g. large-class emits both
    # ``critical`` and ``warning`` rows).
    rules = [
        _rule_entry(
            id=f"smells/{smell_id}",
            short_desc=f"Structural code-smell detector: {smell_id}",
            help_uri=_HELP_BASE + "smells",
            default_level="warning",
            properties={"tags": list(_smells_rule_tags[smell_id])},
        )
        for smell_id in known_kinds
    ]
    known_rule_ids = {f"smells/{k}" for k in known_kinds}

    results: list[dict] = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        smell_id = f.get("smell_id") or ""
        if not smell_id:
            continue
        rule_id = f"smells/{smell_id}"
        if rule_id not in known_rule_ids:
            # Unknown smell_id — skip rather than mint a rule on the fly
            # (LAW 8 — closed enumeration over free-string composition).
            # A plugin-registered detector lands here once it's wired
            # into roam.catalog.registry via @detector.
            continue

        severity = f.get("severity") or "info"

        location_str = f.get("location") or ""
        fpath, line = _parse_loc_string(location_str)
        locations: list[dict] = []
        if fpath:
            locations.append(_location(fpath, line))

        symbol_name = f.get("symbol_name") or ""
        description = f.get("description") or smell_id
        if symbol_name:
            message_text = f"{smell_id}: {symbol_name} — {description}"
        else:
            message_text = f"{smell_id}: {description}"

        # W1062-followup-3: per-result tags add the resolved SARIF
        # level axis so dashboards can slice on actionable bands
        # (``critical`` -> ``error``) from the result chip set without
        # re-resolving locally. Severity is passed through the helper's
        # ``_normalize_tag`` chokepoint so producer-side casing
        # converges to lowercase.
        smell_result_tags = _derive_finding_tags(
            family="hygiene",
            extra=["smells", smell_id],
            severity=_to_level(severity),
        )
        # smells maps raw severity through the closed-enum
        # ``_to_level`` mapping (critical/warning/info -> error/warning/note);
        # this is the helper's default ``level_mapper`` so we leave it
        # unspecified.
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=severity,
                locations=locations,
                message=message_text,
                properties={"tags": list(smell_result_tags)},
            )
        )

    # W1061: forward runtime configurationOverrides[] when the caller
    # captured filter state. ``emit_configuration_overrides=True`` is
    # gated on a non-empty list inside :func:`to_sarif` so the default
    # (no overrides) path stays byte-identical to pre-W1061.
    overrides = list(runtime_overrides or ())
    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        rules,
        results,
        emit_configuration_overrides=bool(overrides),
        configuration_overrides=overrides if overrides else None,
    )


# ── Partition (multi-agent work zones) ───────────────────────────────


def _partition_conflict_risk_level(conflict_risk: str) -> str:
    """Map ``LOW`` / ``MEDIUM`` / ``HIGH`` to SARIF level (closed enum).

    Mirrors :func:`roam.commands.cmd_partition._classify_conflict_risk` —
    the conflict-risk label drives parallel-vs-serial work order, so
    HIGH is escalated to ``error`` so a CI gate keyed off SARIF
    ``level: error`` can refuse to dispatch a partition that would
    almost certainly collide with another agent's work. Unknown labels
    default to ``note`` (LAW 6 — neutrality on unfamiliar input).
    """
    label = (conflict_risk or "").upper()
    if label == "HIGH":
        return "error"
    if label == "MEDIUM":
        return "warning"
    # LOW or unknown -> "note"
    return "note"


# Limit the number of SECONDARY file locations attached to a single
# partition/conflict-risk result. A partition can span thousands of
# files (the v12 dogfood's 7919-partition pathology); embedding all of
# them inline would inflate the SARIF document beyond what GitHub Code
# Scanning can render. The first file is the PRIMARY anchor; the next
# up to ``_PARTITION_MAX_SECONDARY_LOCS`` are SECONDARY locations.
_PARTITION_MAX_SECONDARY_LOCS = 10


def partition_to_sarif(data: dict) -> dict:
    """Convert ``roam partition`` multi-agent manifest output to SARIF.

    *data* is the JSON envelope built by :mod:`roam.commands.cmd_partition`
    (``compute_partition_manifest`` result wrapped in :func:`json_envelope`).
    Two finding families project onto SARIF, each on its own closed-enum
    rule id:

    - ``partition/conflict-risk`` (defaultLevel ``warning``): one result
      per partition entry under ``partitions[]``. Severity is scaled by
      the ``conflict_risk`` label via
      :func:`_partition_conflict_risk_level` (``HIGH`` -> ``error``,
      ``MEDIUM`` -> ``warning``, ``LOW`` -> ``note``). Anchor: the
      partition's first file is the PRIMARY location; up to
      ``_PARTITION_MAX_SECONDARY_LOCS`` additional files attach as
      SECONDARY locations so a SARIF consumer (GitHub Code Scanning)
      can highlight the full work-zone footprint without inflating
      the document.
    - ``partition/key-symbol`` (defaultLevel ``note``): one result per
      ``key_symbols[]`` entry per partition. Each key symbol is a
      PageRank-ranked anchor inside the partition; surfacing them as
      individual findings lets a SARIF consumer link directly to the
      highest-leverage symbols in each work zone. File-level anchor
      (the partition envelope does not carry per-symbol line numbers).

    The verdict line (``data["summary"]["verdict"]``) and the
    partition's role / agent labels appear in result messages so SARIF
    consumers correlate findings to the fleet-planner output that
    triggered them.

    Empty / no-partition envelopes produce a valid SARIF document with
    zero results (rules catalogue is always emitted).
    """
    rules = [
        _rule_entry(
            id="partition/conflict-risk",
            short_desc=("Partition's cross-partition coupling drives parallel-vs-serial work order"),
            help_uri=_HELP_BASE + "partition",
            default_level="warning",
        ),
        _rule_entry(
            id="partition/key-symbol",
            short_desc=("PageRank-ranked anchor symbol inside a partition"),
            help_uri=_HELP_BASE + "partition",
            default_level="note",
        ),
    ]

    results: list[dict] = []
    partitions = data.get("partitions") or []
    if not isinstance(partitions, list):
        partitions = []

    for p in partitions:
        if not isinstance(p, dict):
            continue
        pid = p.get("id", "?")
        role = p.get("role", "") or ""
        agent = p.get("agent", "") or ""
        conflict_risk = p.get("conflict_risk", "LOW") or "LOW"
        cross_edges = p.get("cross_partition_edges", 0)
        cochange_score = p.get("cochange_score", 0)

        files = p.get("files") or []
        if not isinstance(files, list):
            files = []

        # Build the partition-level conflict-risk finding. PRIMARY
        # location is the first file; up to _PARTITION_MAX_SECONDARY_LOCS
        # additional files attach as secondary locations. A partition
        # with no files (degenerate empty cluster) emits an empty
        # locations list — SARIF 2.1.0 treats that as a "whole run"
        # finding.
        locations: list[dict] = []
        for fpath in files[: _PARTITION_MAX_SECONDARY_LOCS + 1]:
            if not isinstance(fpath, str) or not fpath:
                continue
            locations.append(_location(fpath, None))

        agent_suffix = f" (Agent: {agent})" if agent else ""
        role_label = f"'{role}'" if role else f"#{pid}"
        results.append(
            _result_entry(
                rule_id="partition/conflict-risk",
                severity=conflict_risk,
                locations=locations,
                message=(
                    f"Partition {pid} {role_label}{agent_suffix} — "
                    f"conflict risk {conflict_risk} "
                    f"({cross_edges} cross-partition edges, "
                    f"cochange score {cochange_score})"
                ),
                level_mapper=_partition_conflict_risk_level,
            )
        )

        # Per-key-symbol findings (anchored on the symbol's file).
        key_symbols = p.get("key_symbols") or []
        if not isinstance(key_symbols, list):
            continue
        for sym in key_symbols:
            if not isinstance(sym, dict):
                continue
            sym_name = sym.get("name", "?")
            sym_kind = sym.get("kind", "?")
            sym_pagerank = sym.get("pagerank", 0.0)
            sym_file = sym.get("file") or ""
            if not isinstance(sym_file, str) or not sym_file:
                continue
            # Key-symbol findings are always SARIF "note" — pass an
            # identity mapper with the literal level so the helper
            # doesn't translate.
            results.append(
                _result_entry(
                    rule_id="partition/key-symbol",
                    severity="note",
                    locations=[_location(sym_file, None)],
                    message=(
                        f"Key symbol in partition {pid} {role_label}: {sym_kind} '{sym_name}' (PageRank {sym_pagerank})"
                    ),
                    level_mapper=lambda s: s,
                )
            )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Clones (AST structural clone detection) ──────────────────────────


def _clones_pair_level(similarity: float) -> str:
    """Map a clone-pair similarity score to a SARIF level (closed enum).

    Mirrors the R22 confidence bands used by :func:`cmd_clones._classify_similarity`
    but collapses onto SARIF's 3-level vocabulary (error / warning / note).
    The intent is to surface *near-identical* clones (>= 0.95 — almost
    certainly an unintentional duplicate) as ``warning`` so a CI gate
    keyed off SARIF ``level: warning`` flags them; lower-similarity
    clones (structural skeleton matches) drop to ``note``.

    Pair severity is NEVER escalated to ``error``: even a 100%-similarity
    clone pair is an *opportunity to refactor*, not a defect that should
    block CI. Cluster findings (3+ members at high similarity) carry
    similar reasoning — surface them, do not gate on them.

    Unknown / sub-threshold scores default to ``note`` (LAW 6 —
    neutrality on unfamiliar input).
    """
    sim = float(similarity or 0.0)
    if sim >= 0.95:
        return "warning"
    return "note"


# Limit the number of SECONDARY member locations attached to a single
# clones/cluster result. A clone cluster can span many duplicate
# functions (the v12 dogfood surfaced ``src/roam/languages/*_lang.py``
# clusters with 20+ members — parallel language extractors that share
# structure by design); embedding all of them inline would inflate the
# SARIF document beyond what GitHub Code Scanning can render. The first
# member is the PRIMARY anchor; the next up to
# ``_CLONES_MAX_SECONDARY_LOCS`` are SECONDARY locations.
_CLONES_MAX_SECONDARY_LOCS = 10


def clones_to_sarif(data: dict) -> dict:
    """Convert ``roam clones`` AST-clone-detection output to SARIF.

    *data* is the JSON envelope built by :mod:`roam.commands.cmd_clones`
    (the ``clones`` command's ``json_envelope`` output). Two finding
    families project onto SARIF, each on its own closed-enum rule id:

    - ``clones/pair`` (defaultLevel ``note``): one result per pair of
      structurally similar functions under ``pairs[]``. Severity is
      scaled by the pair's ``similarity`` score via
      :func:`_clones_pair_level` (>= 0.95 -> ``warning``; lower bands
      collapse to ``note``). Two-sided anchor: PRIMARY = the first
      member (``file_a``:``line_a``); SECONDARY = the second member
      (``file_b``:``line_b``) so a SARIF consumer can navigate
      directly from one clone to its sibling.
    - ``clones/cluster`` (defaultLevel ``warning``): one result per
      cluster under ``clusters[]`` (3+ members at high similarity).
      Multi-member anchor: PRIMARY = the first member; up to
      ``_CLONES_MAX_SECONDARY_LOCS`` additional members attach as
      SECONDARY locations so a SARIF consumer can highlight the full
      cluster footprint without inflating the document.

    The cmd_clones JSON envelope wraps each cluster / pair in a
    ``{value, confidence, reason}`` triple via :func:`wrap_findings`.
    This converter unwraps the triples to reach the underlying
    fields; raw (un-wrapped) shapes are also accepted so callers can
    feed minimal test fixtures without round-tripping through
    :func:`wrap_findings`.

    Pair severity NEVER escalates to ``error``: clones are refactor
    opportunities, not defects. The ``role_bucket`` field (production /
    test_intentional / mixed) surfaces in result messages so SARIF
    consumers can correlate findings to the W165 bucket classification
    without re-querying the registry.

    Empty / no-clone envelopes produce a valid SARIF document with zero
    results (rules catalogue is always emitted so consumers can
    introspect the rule vocabulary even on a clean run).
    """
    # W1062-followup-3: rule-level tags carry the family + category +
    # kind axes so dashboards grouping by rule still get the filter
    # chips even before any specific result lands. Severity is
    # per-result and stamped below from the resolved SARIF level
    # (warning / note via :func:`_clones_pair_level`).
    _CLONES_RULE_TAGS: dict[str, list[str]] = {
        "clones/pair": _derive_finding_tags(
            family="hygiene",
            extra=["duplication", "pair"],
        ),
        "clones/cluster": _derive_finding_tags(
            family="hygiene",
            extra=["duplication", "cluster"],
        ),
    }
    rules = [
        _rule_entry(
            id="clones/pair",
            short_desc=("Pair of structurally similar functions (Type-2 AST clone)"),
            help_uri=_HELP_BASE + "clones",
            default_level="note",
            properties={"tags": list(_CLONES_RULE_TAGS["clones/pair"])},
        ),
        _rule_entry(
            id="clones/cluster",
            short_desc=("Cluster of 3+ structurally similar functions sharing an AST skeleton"),
            help_uri=_HELP_BASE + "clones",
            default_level="warning",
            properties={"tags": list(_CLONES_RULE_TAGS["clones/cluster"])},
        ),
    ]

    results: list[dict] = []

    # ---- Clusters -------------------------------------------------------
    clusters = data.get("clusters") or []
    if not isinstance(clusters, list):
        clusters = []

    for entry in clusters:
        if not isinstance(entry, dict):
            continue
        # Unwrap the {value, confidence, reason} triple if present;
        # otherwise treat the entry itself as the cluster data.
        cluster = entry.get("value") if "value" in entry else entry
        if not isinstance(cluster, dict):
            continue

        cluster_id = cluster.get("cluster_id", "?")
        avg_sim = float(cluster.get("avg_similarity", 0.0) or 0.0)
        size = cluster.get("size", 0) or 0
        pattern = cluster.get("pattern", "") or ""
        role_bucket = cluster.get("role_bucket", "") or ""

        members = cluster.get("members") or []
        if not isinstance(members, list):
            members = []

        # Build locations: PRIMARY = first member; SECONDARY = up to 10
        # additional members. Members carry ``file``, ``line_start``,
        # and ``line_end`` from the clone detector.
        locations: list[dict] = []
        for m in members[: _CLONES_MAX_SECONDARY_LOCS + 1]:
            if not isinstance(m, dict):
                continue
            fpath = m.get("file") or ""
            line = m.get("line_start")
            if not fpath:
                continue
            locations.append(_location(fpath, line))

        bucket_suffix = f" [{role_bucket}]" if role_bucket else ""
        pattern_suffix = f" — {pattern}" if pattern else ""
        # W1062-followup-3: per-result tags add the role_bucket + SARIF
        # level axes. Producer-side underscore (``test_intentional``)
        # collapses to the URL-safe hyphen form via ``_normalize_tag``.
        # Cluster findings are always SARIF ``warning`` — pre-resolved
        # here so the tag vocabulary stays lowercase.
        cluster_extra = ["duplication", "cluster"]
        if role_bucket:
            cluster_extra.append(role_bucket)
        cluster_tags = _derive_finding_tags(
            family="hygiene",
            extra=cluster_extra,
            severity="warning",
        )
        # Cluster findings are always SARIF "warning" — pass the literal
        # level via an identity ``level_mapper``.
        results.append(
            _result_entry(
                rule_id="clones/cluster",
                severity="warning",
                locations=locations,
                message=(
                    f"Clone cluster #{cluster_id}{bucket_suffix}: "
                    f"{size} functions at {round(avg_sim * 100)}% "
                    f"avg similarity{pattern_suffix}"
                ),
                level_mapper=lambda s: s,
                properties={"tags": list(cluster_tags)},
            )
        )

    # ---- Pairs ----------------------------------------------------------
    pairs = data.get("pairs") or []
    if not isinstance(pairs, list):
        pairs = []

    for entry in pairs:
        if not isinstance(entry, dict):
            continue
        # Same unwrap-or-raw pattern as clusters.
        pair = entry.get("value") if "value" in entry else entry
        if not isinstance(pair, dict):
            continue

        file_a = pair.get("file_a") or ""
        file_b = pair.get("file_b") or ""
        if not file_a:
            # Without a primary anchor we cannot surface the pair
            # meaningfully — skip rather than emit an anchorless result.
            continue
        func_a = pair.get("func_a") or "?"
        func_b = pair.get("func_b") or "?"
        line_a = pair.get("line_a")
        line_b = pair.get("line_b")
        similarity = float(pair.get("similarity", 0.0) or 0.0)
        role_bucket = pair.get("role_bucket", "") or ""

        # PRIMARY = file_a; SECONDARY = file_b (when present).
        locations = [_location(file_a, line_a)]
        if file_b:
            locations.append(_location(file_b, line_b))

        bucket_suffix = f" [{role_bucket}]" if role_bucket else ""
        # W1062-followup-3: per-result tags add role_bucket + SARIF
        # level axes. Pair level is derived from the similarity score
        # via :func:`_clones_pair_level` (>=0.95 -> warning, else
        # note); pre-resolve here so the chip vocabulary stays
        # lowercase. Producer-side underscores (``test_intentional``)
        # collapse via ``_normalize_tag``.
        pair_extra = ["duplication", "pair"]
        if role_bucket:
            pair_extra.append(role_bucket)
        pair_tags = _derive_finding_tags(
            family="hygiene",
            extra=pair_extra,
            severity=_clones_pair_level(similarity),
        )
        # Pair severity scales with the similarity score via
        # :func:`_clones_pair_level` (closed band — >=0.95 -> warning,
        # else note).
        results.append(
            _result_entry(
                rule_id="clones/pair",
                severity=similarity,
                locations=locations,
                message=(
                    f"Clone pair{bucket_suffix}: '{func_a}' <-> '{func_b}' at {round(similarity * 100)}% similarity"
                ),
                level_mapper=_clones_pair_level,
                properties={"tags": list(pair_tags)},
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Delete-check (deletion-safety gate on a working/staged/PR diff) ───


# Limit the number of SECONDARY survivor locations attached to a single
# delete-check result. A single deleted symbol can have hundreds of
# surviving references (god-class delete with broad use); embedding all
# of them inline would inflate the SARIF document beyond what GitHub
# Code Scanning can render. The PRIMARY anchor is the deletion site
# (from_file:from_line); the next up to
# ``_DELETE_CHECK_MAX_SECONDARY_LOCS`` survivors attach as SECONDARY
# locations so consumers can navigate to surviving call sites without
# overflowing the document.
_DELETE_CHECK_MAX_SECONDARY_LOCS = 10


def delete_check_to_sarif(data: dict) -> dict:
    """Convert ``roam delete-check`` deletion-safety gate output to SARIF.

    *data* is the JSON envelope built by
    :mod:`roam.commands.cmd_delete_check` — one entry per gated
    deletion under ``deletions[]``, each carrying a closed-enum
    ``verdict`` (``SAFE`` / ``LIKELY-SAFE`` / ``BREAK-RISK``),
    ``kind`` (``symbol`` / ``file`` / ``line``), ``name`` (the deleted
    identifier or path), ``from_file`` + ``from_line`` (the deletion
    site), ``reason`` (human-readable explanation), and a nested
    ``survivors[]`` list of surviving references that prevent the
    deletion from being safe.

    Three rule ids project onto SARIF — one per verdict, each with a
    distinct ``defaultLevel`` so a CI gate keyed off SARIF
    ``level: error`` blocks on BREAK-RISK without surfacing the
    advisory bands:

    - ``delete-check/break-risk`` (defaultLevel ``error``): surviving
      reachable code references remain — deleting the target will
      break the build. Mirrors the cmd_delete_check exit-5 gate.
    - ``delete-check/likely-safe`` (defaultLevel ``warning``): only
      test / docs / unreachable references survive — review
      recommended but not blocking.
    - ``delete-check/safe`` (defaultLevel ``note``): no surviving
      references — informational, safe to delete.

    Per-deletion anchor: PRIMARY = ``from_file:from_line`` (the deletion
    site itself). SECONDARY = up to ``_DELETE_CHECK_MAX_SECONDARY_LOCS``
    survivors[] entries (each with ``path`` + ``line``) so SARIF
    consumers can navigate directly from the deletion site to a
    surviving caller. Survivors without a ``path`` field are skipped
    (defensive — the envelope always populates it, but a future
    producer change shouldn't crash the projector).

    Deletions with no ``from_file`` are skipped — without a PRIMARY
    anchor we cannot surface the row meaningfully (matches the
    ``clones_to_sarif`` pair-without-file_a discipline).

    Empty / no-deletion envelopes produce a valid SARIF document with
    zero results (rules catalogue is always emitted so consumers can
    introspect the rule vocabulary even on a clean run).
    """
    rules = [
        _rule_entry(
            id="delete-check/break-risk",
            short_desc=("Deletion target has surviving reachable code references — deleting it will break the build"),
            help_uri=_HELP_BASE + "delete-check",
            default_level="error",
        ),
        _rule_entry(
            id="delete-check/likely-safe",
            short_desc=(
                "Deletion target has surviving references only in tests / docs / unreachable code — review recommended"
            ),
            help_uri=_HELP_BASE + "delete-check",
            default_level="warning",
        ),
        _rule_entry(
            id="delete-check/safe",
            short_desc=("Deletion target has no surviving references — safe to delete"),
            help_uri=_HELP_BASE + "delete-check",
            default_level="note",
        ),
    ]

    _VERDICT_TO_RULE_LEVEL = {
        "BREAK-RISK": ("delete-check/break-risk", "error"),
        "LIKELY-SAFE": ("delete-check/likely-safe", "warning"),
        "SAFE": ("delete-check/safe", "note"),
    }

    results: list[dict] = []

    deletions = data.get("deletions") or []
    if not isinstance(deletions, list):
        deletions = []

    for entry in deletions:
        if not isinstance(entry, dict):
            continue

        verdict = entry.get("verdict") or ""
        rule_level = _VERDICT_TO_RULE_LEVEL.get(verdict)
        if rule_level is None:
            # Unknown verdict — skip rather than mint a rule on the fly
            # (LAW 8 — closed enumeration over free-string composition).
            continue
        rule_id, level = rule_level

        from_file = entry.get("from_file") or ""
        if not from_file:
            # Without a primary anchor we cannot surface the row
            # meaningfully — skip rather than emit an anchorless result.
            continue
        from_line = entry.get("from_line")
        # ``from_line`` is 0 for full-file deletions (no specific line
        # within the file); :func:`_physical_location` drops the
        # ``region`` key when ``line <= 0`` so SARIF stays valid.

        # PRIMARY = the deletion site itself. SECONDARY = up to
        # _DELETE_CHECK_MAX_SECONDARY_LOCS survivors[] (each with
        # path + line).
        locations: list[dict] = [_location(from_file, from_line)]
        survivors = entry.get("survivors") or []
        if not isinstance(survivors, list):
            survivors = []
        for s in survivors[:_DELETE_CHECK_MAX_SECONDARY_LOCS]:
            if not isinstance(s, dict):
                continue
            spath = s.get("path") or ""
            sline = s.get("line")
            if not spath:
                continue
            locations.append(_location(spath, sline))

        kind = entry.get("kind") or "symbol"
        name = entry.get("name") or "?"
        reason = entry.get("reason") or ""
        reason_suffix = f" — {reason}" if reason else ""
        message_text = f"{verdict} {kind} '{name}'{reason_suffix}"

        # delete-check uses an identity ``level_mapper``: the verdict
        # already maps 1:1 to a SARIF level via _VERDICT_TO_RULE_LEVEL,
        # so we pass the literal level through directly.
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=level,
                locations=locations,
                message=message_text,
                level_mapper=lambda s: s,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Auth gaps (PHP / Laravel endpoint authentication & authorization) ─


def _auth_gaps_confidence_tier_level(tier: str) -> str:
    """Map an auth-gaps confidence tier to a SARIF level (closed enum).

    The cmd_auth_gaps detector classifies each finding into one of three
    confidence tiers (see :func:`roam.commands.cmd_auth_gaps._auth_gap_confidence_tier`).
    The SARIF projection mirrors the tier vocabulary 1:1 onto the SARIF
    3-level band so a CI gate keyed off ``level: error`` only blocks on
    deterministic findings, not heuristic name-matching:

        static_analysis  -> "error"    (direct-unauthenticated-handler;
                                        deterministic brace-depth analysis)
        structural       -> "warning"  (helper-indirection; graph-walked
                                        ancestor + same-class resolution)
        heuristic        -> "note"     (name-based; non-auth guard or
                                        read-method action heuristics)

    Unknown tiers default to ``"note"`` (LAW 6 — neutrality on
    unfamiliar input).
    """
    label = (tier or "").lower()
    if label == "static_analysis":
        return "error"
    if label == "structural":
        return "warning"
    # heuristic or unknown -> "note"
    return "note"


def auth_gaps_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam auth-gaps`` PHP / Laravel auth-gap findings to SARIF.

    *findings* is the combined ``route_findings + controller_findings``
    list the detector builds for the JSON envelope (one entry per gap;
    see :mod:`roam.commands.cmd_auth_gaps` for the dict shape). Each
    finding maps onto one of three closed-enum rule ids via
    :func:`roam.commands.cmd_auth_gaps._auth_gap_finding_kind`, with a
    distinct ``defaultLevel`` so a CI gate keyed off SARIF
    ``level: error`` only blocks on deterministic findings:

    - ``auth-gaps/direct-unauthenticated-handler`` (defaultLevel
      ``error``): a route sits outside every auth middleware group AND
      has no inline auth middleware. Confidence tier ``static_analysis``
      — deterministic brace-depth analysis of the routes file.
    - ``auth-gaps/helper-indirection`` (defaultLevel ``warning``): a
      controller method without a literal ``$this->authorize`` call,
      where same-class or ancestor-class helper descent was attempted
      but did NOT clear the gap. Confidence tier ``structural`` — the
      detector ran a graph traversal (class_source_map +
      ``_collect_ancestor_methods``) to land here.
    - ``auth-gaps/name-based`` (defaultLevel ``note``): weaker signals
      — route low-confidence findings gated on non-auth guard naming
      (throttle / signed / verified) and controller read methods
      (action name heuristic) / tenant-scope demotions. Confidence
      tier ``heuristic`` — pattern-on-name only.

    Per-finding anchor: ``file`` + ``line`` (route findings carry the
    route definition line; controller findings carry the method's
    declaration line). The message body surfaces the route verb / path
    or the controller / method, the confidence label, and the fix hint
    so SARIF consumers can triage without a JSON-envelope round-trip.

    Unknown / unrecognised finding shapes (missing ``type`` or empty
    ``file``) are skipped silently — extending the SARIF vocabulary is
    a deliberate edit to this function plus a kind in
    :mod:`roam.commands.cmd_auth_gaps`. Empty ``findings`` produces a
    valid SARIF envelope with zero results (rules catalogue is always
    emitted so consumers can introspect the rule vocabulary even on a
    clean run).
    """
    # Lazy import: avoids a top-of-module cycle between
    # roam.output.sarif and roam.commands.cmd_auth_gaps. The helper
    # functions are pure (no side-effectful imports), so this is cheap.
    from roam.commands.cmd_auth_gaps import (
        _auth_gap_confidence_tier,
        _auth_gap_finding_kind,
    )

    # W1062-followup-2: stamp dashboard-filter tags on every rule descriptor
    # so a SARIF dashboard (GitHub Code Scanning / SonarQube) can slice the
    # auth-gap rule catalogue by family (`security`), category (`auth`),
    # kind (`direct-unauthenticated-handler` / `helper-indirection` /
    # `name-based`), and confidence tier (`static-analysis` / `structural` /
    # `heuristic`). The W1062 helper normalises the underscore-separated
    # confidence tier (`static_analysis`) to the URL-safe `static-analysis`
    # chip so the dashboard vocabulary stays uniform across emitters.
    _RULE_TAG_SPECS = [
        (
            "auth-gaps/direct-unauthenticated-handler",
            "direct-unauthenticated-handler",
            "static_analysis",
            "error",
        ),
        (
            "auth-gaps/helper-indirection",
            "helper-indirection",
            "structural",
            "warning",
        ),
        (
            "auth-gaps/name-based",
            "name-based",
            "heuristic",
            "note",
        ),
    ]
    _RULE_TAGS_BY_ID: dict[str, list[str]] = {
        rule_id: _derive_finding_tags(
            family="security",
            extra=["auth", kind, tier],
        )
        for rule_id, kind, tier, _level in _RULE_TAG_SPECS
    }

    rules = [
        _rule_entry(
            id="auth-gaps/direct-unauthenticated-handler",
            short_desc=("Route handler sits outside every auth middleware group and has no inline auth middleware"),
            help_uri=_HELP_BASE + "auth-gaps",
            default_level="error",
            properties={"tags": list(_RULE_TAGS_BY_ID["auth-gaps/direct-unauthenticated-handler"])},
        ),
        _rule_entry(
            id="auth-gaps/helper-indirection",
            short_desc=(
                "Controller method lacks a literal authorize() call; "
                "helper-descent through same-class and ancestor "
                "helpers did not clear the gap"
            ),
            help_uri=_HELP_BASE + "auth-gaps",
            default_level="warning",
            properties={"tags": list(_RULE_TAGS_BY_ID["auth-gaps/helper-indirection"])},
        ),
        _rule_entry(
            id="auth-gaps/name-based",
            short_desc=(
                "Auth-gap inferred from name / action heuristics "
                "(non-auth guard, read-method action, tenant-scope "
                "demotion)"
            ),
            help_uri=_HELP_BASE + "auth-gaps",
            default_level="note",
            properties={"tags": list(_RULE_TAGS_BY_ID["auth-gaps/name-based"])},
        ),
    ]

    _KIND_TO_RULE = {
        "direct-unauthenticated-handler": "auth-gaps/direct-unauthenticated-handler",
        "helper-indirection": "auth-gaps/helper-indirection",
        "name-based": "auth-gaps/name-based",
    }

    results: list[dict] = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        ftype = f.get("type") or ""
        if ftype not in ("route", "controller"):
            # Closed-enum discipline — unknown finding type means the
            # detector grew a new bucket without updating this
            # projector. Skip rather than mint a rule on the fly.
            continue
        fpath = f.get("file") or ""
        if not fpath:
            # Without an anchor we cannot surface the finding
            # meaningfully — skip rather than emit an anchorless row.
            continue
        line = f.get("line")

        kind = _auth_gap_finding_kind(f)
        rule_id = _KIND_TO_RULE.get(kind)
        if rule_id is None:
            # Future kind label that hasn't landed in the closed
            # enumeration above — skip (matches LAW 8).
            continue
        tier = _auth_gap_confidence_tier(kind)
        confidence = f.get("confidence") or ""

        if ftype == "route":
            verb = f.get("verb") or ""
            path = f.get("path") or ""
            fix = f.get("fix") or ""
            fix_suffix = f" — {fix}" if fix else ""
            message_text = f"Auth gap: {verb} {path} [confidence={confidence}, tier={tier}]{fix_suffix}"
        else:
            controller = f.get("controller") or ""
            method = f.get("method") or ""
            reason = f.get("reason") or ""
            fix = f.get("fix") or ""
            reason_suffix = f" — {reason}" if reason else ""
            fix_suffix = f" — {fix}" if fix else ""
            message_text = (
                f"Auth gap: {controller}::{method} [confidence={confidence}, tier={tier}]{reason_suffix}{fix_suffix}"
            )

        # auth-gaps pre-resolves the level via the closed-enum
        # _auth_gaps_confidence_tier_level mapping; pass the tier
        # through the helper so the helper doesn't double-translate.
        #
        # W1062-followup-2: stamp the per-result tags with the same
        # family / category / kind / tier vocabulary as the rule
        # descriptor, plus the resolved SARIF severity level so a
        # dashboard can slice by `error` / `warning` / `note` from the
        # result chip set without re-resolving the tier locally.
        result_tags = _derive_finding_tags(
            family="security",
            extra=["auth", kind, tier],
            severity=_auth_gaps_confidence_tier_level(tier),
        )
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=tier,
                locations=[_location(fpath, line)],
                message=message_text,
                level_mapper=_auth_gaps_confidence_tier_level,
                properties={"tags": list(result_tags)},
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── N+1 detector (implicit ORM lazy-load patterns) ───────────────────


def _n1_confidence_level(confidence: str) -> str:
    """Map an n1 finding's confidence label to a SARIF level (closed enum).

    cmd_n1 classifies each finding into one of three confidence labels
    based on collection-context evidence (see
    :func:`roam.commands.cmd_n1._n1_classify`). The SARIF projection
    mirrors that 1:1 onto the SARIF 3-level band so a CI gate keyed off
    ``level: error`` only blocks on findings the detector reached
    through strong evidence (collection / pagination context):

        high   -> "error"    (model used in a collection / pagination
                              context; near-certain N+1 on serialization)
        medium -> "warning"  (relationship lazy-load I/O type but no
                              strong collection-context signal)
        low    -> "note"     (heuristic match; manual review needed)

    Unknown labels default to ``"note"`` (LAW 6 — neutrality on
    unfamiliar input).
    """
    label = (confidence or "").lower()
    if label == "high":
        return "error"
    if label == "medium":
        return "warning"
    # low or unknown -> "note"
    return "note"


def n1_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam n1`` implicit N+1 findings to SARIF.

    *findings* is the raw finding list cmd_n1 builds for the JSON
    envelope (one entry per implicit N+1 pattern — see
    :mod:`roam.commands.cmd_n1` for the dict shape). Each finding maps
    onto one of three closed-enum rule ids by confidence label, with a
    distinct ``defaultLevel`` so a CI gate keyed off SARIF
    ``level: error`` only blocks on near-certain findings (the model
    is used in a collection / pagination context):

    - ``n1/high-confidence`` (defaultLevel ``error``): the model is
      used in a collection / pagination context, so the accessor's I/O
      will fire per-item on serialization. Confidence label ``high``
      from :func:`roam.commands.cmd_n1._n1_classify`.
    - ``n1/medium-confidence`` (defaultLevel ``warning``): the
      accessor triggers a relationship lazy-load I/O type but no
      strong collection-context signal was found. Confidence label
      ``medium``.
    - ``n1/low-confidence`` (defaultLevel ``note``): weaker signals —
      heuristic match without supporting collection-context evidence.
      Confidence label ``low``.

    Per-finding anchor: ``accessor_location`` (parsed as ``path:line``
    via :func:`_parse_loc_string`). The accessor is the actual I/O
    site — the model declaration is referenced in the message body but
    the SARIF anchor lands on the line that fires the per-item query.
    The message body surfaces the model / accessor names, the
    appended attribute, the relationship, the I/O type, and the fix
    suggestion so SARIF consumers can triage without a JSON-envelope
    round-trip.

    Findings missing an accessor location are skipped silently — without
    an anchor SARIF consumers cannot surface the row meaningfully. The
    rule catalogue is always emitted (closed enum of 3 rules) so
    consumers can introspect the rule vocabulary even on a clean run.
    Mirrors the closed-enum design from :func:`auth_gaps_to_sarif`
    (W1195) and :func:`smells_to_sarif` (W1171).

    W1062-followup-4 dashboard-filtering tags
    -----------------------------------------

    Each rule + result carries ``properties.tags[]`` shaped as
    ``["performance", "n1-query", <severity>]`` so a dashboard
    (GitHub Code Scanning / SonarQube) can slice the implicit-N+1
    finding stream by family (``performance``) / category
    (``n1-query``) / resolved SARIF level (``error`` / ``warning`` /
    ``note``). N+1 findings have no CWE / OWASP anchor — performance
    bugs aren't covered by the security taxonomies — so family +
    category + severity is the canonical filter-chip shape. Severity
    is per-result; the rule descriptor carries only family + category
    so dashboards grouping by rule still see the chips on a clean run.
    """
    # W1062-followup-4: rule-level tags carry the family + category
    # axes so dashboards grouping by rule still get the filter chips
    # even before any specific result lands. Severity is per-result and
    # stamped below from the resolved SARIF level.
    _N1_RULE_TAGS = _derive_finding_tags(family="performance", extra=["n1-query"])
    rules = [
        _rule_entry(
            id="n1/high-confidence",
            short_desc=(
                "Implicit N+1: model accessor triggers per-item I/O "
                "and the model is used in a collection / pagination "
                "context"
            ),
            help_uri=_HELP_BASE + "n1",
            default_level="error",
            properties={"tags": list(_N1_RULE_TAGS)},
        ),
        _rule_entry(
            id="n1/medium-confidence",
            short_desc=(
                "Implicit N+1: model accessor triggers a relationship "
                "lazy-load with no strong collection-context evidence"
            ),
            help_uri=_HELP_BASE + "n1",
            default_level="warning",
            properties={"tags": list(_N1_RULE_TAGS)},
        ),
        _rule_entry(
            id="n1/low-confidence",
            short_desc=(
                "Implicit N+1: heuristic match — accessor pattern without supporting collection-context evidence"
            ),
            help_uri=_HELP_BASE + "n1",
            default_level="note",
            properties={"tags": list(_N1_RULE_TAGS)},
        ),
    ]

    _CONFIDENCE_TO_RULE = {
        "high": "n1/high-confidence",
        "medium": "n1/medium-confidence",
        "low": "n1/low-confidence",
    }

    results: list[dict] = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        accessor_loc = f.get("accessor_location") or ""
        if not accessor_loc:
            # Without an anchor we cannot surface the finding
            # meaningfully — skip rather than emit an anchorless row.
            continue
        fpath, line = _parse_loc_string(accessor_loc)
        if not fpath:
            continue

        confidence = (f.get("confidence") or "").lower()
        rule_id = _CONFIDENCE_TO_RULE.get(confidence)
        if rule_id is None:
            # Future confidence label that hasn't landed in the closed
            # enumeration above — skip (matches LAW 8 closed-enum
            # discipline).
            continue

        model_name = f.get("model_name") or ""
        accessor_name = f.get("accessor_name") or ""
        appended = f.get("appended_attribute") or ""
        relationship = f.get("relationship") or ""
        io_type = f.get("io_type") or ""
        suggestion = f.get("suggestion") or ""

        # Message body — surface enough context for triage without a
        # JSON-envelope round-trip. Order: subject (model.accessor) ->
        # mechanism (appended attribute -> relationship via io_type) ->
        # fix.
        parts = [f"Implicit N+1: {model_name}.{accessor_name}"]
        if appended:
            parts.append(f"appended via ${appended}")
        if relationship:
            io_suffix = f" ({io_type})" if io_type else ""
            parts.append(f"triggers {relationship}{io_suffix}")
        message_text = " — ".join(parts)
        if suggestion:
            message_text += f" — Fix: {suggestion}"

        # W1062-followup-4: per-result tags add the SARIF-level axis
        # (resolved from confidence via the level-mapper) so dashboards
        # can filter by severity chip without re-running the mapper.
        result_tags = _derive_finding_tags(
            family="performance",
            extra=["n1-query"],
            severity=_n1_confidence_level(confidence),
        )
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=confidence,
                locations=[_location(fpath, line)],
                message=message_text,
                level_mapper=_n1_confidence_level,
                properties={"tags": list(result_tags)},
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Missing-index detector (unindexed query columns) ─────────────────


def _missing_index_confidence_level(confidence: str) -> str:
    """Map a missing-index finding's confidence label to a SARIF level.

    cmd_missing_index labels each finding high / medium / low based on
    pattern unambiguity (see
    :func:`roam.commands.cmd_missing_index._missing_index_classify`).
    The SARIF projection mirrors that 1:1 onto the SARIF 3-level band
    so a CI gate keyed off ``level: error`` only blocks on findings
    the detector reached through strong evidence (paginated query on
    an unindexed column with an unconditional equality predicate):

        high   -> "error"    (paginated query on unindexed column;
                              guaranteed table scan)
        medium -> "warning"  (orderBy on non-indexed column, OR
                              paginated WHERE without composite
                              coverage)
        low    -> "note"     (column has an index but not the optimal
                              composite — orderby_with_where heuristic)

    Unknown labels default to ``"note"`` (LAW 6 — neutrality on
    unfamiliar input).
    """
    label = (confidence or "").lower()
    if label == "high":
        return "error"
    if label == "medium":
        return "warning"
    # low or unknown -> "note"
    return "note"


def missing_index_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam missing-index`` unindexed-query findings to SARIF.

    *findings* is the raw finding list cmd_missing_index builds for
    the JSON envelope (one entry per unindexed-query pattern — see
    :mod:`roam.commands.cmd_missing_index` for the dict shape). Each
    finding maps onto one of three closed-enum rule ids by confidence
    label, with a distinct ``defaultLevel`` so a CI gate keyed off
    SARIF ``level: error`` only blocks on near-certain findings
    (paginated query on an unindexed column):

    - ``missing-index/high-confidence`` (defaultLevel ``error``):
      WHERE on an unindexed column in a paginated query — bounded
      result set means filtering is happening; missing index means a
      guaranteed table scan. Confidence label ``high`` from
      :func:`roam.commands.cmd_missing_index._missing_index_classify`.
    - ``missing-index/medium-confidence`` (defaultLevel ``warning``):
      orderBy on a non-indexed column, OR a paginated WHERE without
      composite coverage. Recognised pattern but slightly weaker
      signal. Confidence label ``medium``.
    - ``missing-index/low-confidence`` (defaultLevel ``note``): the
      column already has an index, just not the optimal composite
      (orderby_with_where heuristic). Purely a "you could do better"
      heuristic. Confidence label ``low``.

    Per-finding anchor: ``query_location`` (parsed as ``path:line``
    via :func:`_parse_loc_string`). The query location is the actual
    WHERE / orderBy call site — the table + column names are surfaced
    in the message body but the SARIF anchor lands on the line that
    runs the unindexed query. The message body surfaces the table +
    column tuple, the pattern_type, the paginate flag, and the fix
    suggestion (composite-index recommendation) so SARIF consumers
    can triage without a JSON-envelope round-trip.

    Findings missing a query_location are skipped silently — without
    an anchor SARIF consumers cannot surface the row meaningfully.
    The rule catalogue is always emitted (closed enum of 3 rules) so
    consumers can introspect the rule vocabulary even on a clean run.
    Mirrors the closed-enum design from :func:`n1_to_sarif` (W1208)
    and :func:`auth_gaps_to_sarif` (W1195).

    W1062-followup-4 dashboard-filtering tags
    -----------------------------------------

    Each rule + result carries ``properties.tags[]`` shaped as
    ``["performance", "missing-index", <severity>]`` so a dashboard
    (GitHub Code Scanning / SonarQube) can slice the missing-index
    finding stream by family (``performance``) / category
    (``missing-index``) / resolved SARIF level (``error`` /
    ``warning`` / ``note``). Missing-index findings have no CWE /
    OWASP anchor — query-planner pathology isn't covered by the
    security taxonomies — so family + category + severity is the
    canonical filter-chip shape. Severity is per-result; the rule
    descriptor carries only family + category so dashboards grouping
    by rule still see the chips on a clean run.
    """
    # W1062-followup-4: rule-level tags carry the family + category
    # axes so dashboards grouping by rule still get the filter chips
    # even before any specific result lands. Severity is per-result and
    # stamped below from the resolved SARIF level.
    _MISSING_INDEX_RULE_TAGS = _derive_finding_tags(
        family="performance",
        extra=["missing-index"],
    )
    rules = [
        _rule_entry(
            id="missing-index/high-confidence",
            short_desc=("Missing index: WHERE on an unindexed column in a paginated query (guaranteed table scan)"),
            help_uri=_HELP_BASE + "missing-index",
            default_level="error",
            properties={"tags": list(_MISSING_INDEX_RULE_TAGS)},
        ),
        _rule_entry(
            id="missing-index/medium-confidence",
            short_desc=(
                "Missing index: orderBy on a non-indexed column, or paginated WHERE without composite coverage"
            ),
            help_uri=_HELP_BASE + "missing-index",
            default_level="warning",
            properties={"tags": list(_MISSING_INDEX_RULE_TAGS)},
        ),
        _rule_entry(
            id="missing-index/low-confidence",
            short_desc=("Sub-optimal index: column has an individual index but no composite covering filter + sort"),
            help_uri=_HELP_BASE + "missing-index",
            default_level="note",
            properties={"tags": list(_MISSING_INDEX_RULE_TAGS)},
        ),
    ]

    _CONFIDENCE_TO_RULE = {
        "high": "missing-index/high-confidence",
        "medium": "missing-index/medium-confidence",
        "low": "missing-index/low-confidence",
    }

    results: list[dict] = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        query_loc = f.get("query_location") or ""
        if not query_loc:
            # Without an anchor we cannot surface the finding
            # meaningfully — skip rather than emit an anchorless row.
            continue
        fpath, line = _parse_loc_string(query_loc)
        if not fpath:
            continue

        confidence = (f.get("confidence") or "").lower()
        rule_id = _CONFIDENCE_TO_RULE.get(confidence)
        if rule_id is None:
            # Future confidence label that hasn't landed in the closed
            # enumeration above — skip (matches LAW 8 closed-enum
            # discipline).
            continue

        table = f.get("table") or "?"
        columns = f.get("columns") or []
        pattern_type = f.get("pattern_type") or ""
        has_paginate = bool(f.get("has_paginate"))
        issue = f.get("issue") or ""
        suggestion = f.get("suggestion") or ""

        cols_part = " + ".join(columns) if columns else "?"

        # Message body — surface enough context for triage without a
        # JSON-envelope round-trip. Order: subject (table.cols) ->
        # mechanism (pattern_type, paginated?) -> issue -> fix.
        parts = [f"Missing index: {table}.{cols_part}"]
        if pattern_type:
            parts.append(f"pattern={pattern_type}")
        if has_paginate:
            parts.append("paginated query")
        message_text = " — ".join(parts)
        if issue:
            message_text += f" — {issue}"
        if suggestion:
            message_text += f" — Fix: {suggestion}"

        # W1062-followup-4: per-result tags add the SARIF-level axis
        # (resolved from confidence via the level-mapper) so dashboards
        # can filter by severity chip without re-running the mapper.
        result_tags = _derive_finding_tags(
            family="performance",
            extra=["missing-index"],
            severity=_missing_index_confidence_level(confidence),
        )
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=confidence,
                locations=[_location(fpath, line)],
                message=message_text,
                level_mapper=_missing_index_confidence_level,
                properties={"tags": list(result_tags)},
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Internal helpers ─────────────────────────────────────────────────


def _algo_level(confidence: str) -> str:
    c = (confidence or "").lower()
    if c == "high":
        return "warning"
    if c == "medium":
        return "note"
    return "note"


def _algo_message(finding: dict) -> str:
    msg = finding.get("reason", "Algorithmic improvement opportunity")
    if finding.get("suggested_way"):
        msg += f" Suggestion: use '{finding.get('suggested_way')}' instead of '{finding.get('detected_way')}'."
    return msg


def _finding_fingerprint(finding: dict) -> str:
    payload = "|".join(
        [
            str(finding.get("task_id", "")),
            str(finding.get("detected_way", "")),
            str(finding.get("suggested_way", "")),
            str(finding.get("symbol_name", "")),
            str(finding.get("location", "")),
        ]
    )
    return _hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _primary_location_line_hash(finding: dict) -> str:
    payload = "|".join(
        [
            str(finding.get("task_id", "")),
            str(finding.get("location", "")),
        ]
    )
    return _hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _slugify(text: str) -> str:
    """Turn a human-readable name into a URL/ID-safe slug."""
    slug = text.lower().strip()
    slug = slug.replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c in ("-", "_"))


# ── Orphan-imports detector (W1218) ──────────────────────────────────


def _orphan_imports_kind_level(kind: str) -> str:
    """Map an orphan-import finding's kind to a SARIF level (closed enum).

    cmd_orphan_imports classifies each finding into one of three closed
    kinds (see :data:`roam.commands.cmd_orphan_imports._ORPHAN_KIND_CONFIDENCE`).
    The SARIF projection mirrors the R22 confidence-derivation rule
    1:1 onto the SARIF 3-level band so a CI gate keyed off
    ``level: error`` only blocks on near-certain orphans (the
    top-level package IS indexed but the dotted submodule is not —
    almost certainly a typo / stale import):

        internal_typo   -> "error"    (Python: top-level package indexed
                                       but full dotted path is not;
                                       deterministic set-membership over
                                       the index — almost surely a typo)
        missing_package -> "warning"  (Python: not in index AND not
                                       importable via importlib; could be
                                       typo OR uninstalled optional dep)
        missing_local   -> "warning"  (JS/Go: relative / path-style
                                       import that didn't resolve to an
                                       indexed file — possible build-tool
                                       resolution)

    Unknown kinds default to ``"note"`` (LAW 6 — neutrality on
    unfamiliar input). Mirrors the closed-enum band design from
    :func:`_n1_confidence_level` (W1208) and
    :func:`_auth_gaps_confidence_tier_level` (W1195).
    """
    label = (kind or "").lower()
    if label == "internal_typo":
        return "error"
    if label in ("missing_package", "missing_local"):
        return "warning"
    return "note"


def orphan_imports_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam orphan-imports`` findings to SARIF.

    *findings* is the raw orphan-import list cmd_orphan_imports builds
    for the JSON envelope (one entry per orphan import — see
    :mod:`roam.commands.cmd_orphan_imports` for the dict shape:
    ``{language, file, line, module, kind, hint}``). Each finding maps
    onto one of three closed-enum rule ids by kind, with a distinct
    ``defaultLevel`` so a CI gate keyed off SARIF ``level: error``
    only blocks on near-certain orphans (the top-level package IS
    indexed but the dotted submodule is not):

    - ``orphan-imports/internal-typo`` (defaultLevel ``error``): Python
      orphan where the top-level package is in the index but the full
      dotted path is not. Confidence label ``high`` per the R22
      classifier — deterministic static-analysis set-membership over
      the index.
    - ``orphan-imports/missing-package`` (defaultLevel ``warning``):
      Python orphan that resolves neither in the index NOR via
      ``importlib.util.find_spec``. Confidence label ``medium`` —
      could be typo or uninstalled optional dependency.
    - ``orphan-imports/missing-local`` (defaultLevel ``warning``):
      JS/Go orphan where a relative / path-style import did not
      resolve to an indexed file / package. Confidence label
      ``medium`` — possible build-tool resolution.

    Per-finding anchor: ``file`` + ``line`` (the import statement
    line). The message body surfaces the orphan module name, the
    language, the kind, and the resolution hint so SARIF consumers
    can triage without a JSON-envelope round-trip.

    Findings missing an anchor (empty ``file``) are skipped silently —
    without an anchor SARIF consumers cannot surface the row
    meaningfully. The rule catalogue is always emitted (closed enum
    of 3 rules) so consumers can introspect the rule vocabulary even
    on a clean run. Mirrors the closed-enum design from
    :func:`n1_to_sarif` (W1208) and :func:`auth_gaps_to_sarif`
    (W1195).

    W1062-followup-4 dashboard-filtering tags
    -----------------------------------------

    Each rule + result carries ``properties.tags[]`` shaped as
    ``["hygiene", "orphan-imports", <kind-slug>, <severity>]`` so a
    dashboard (GitHub Code Scanning / SonarQube) can slice the
    orphan-imports finding stream by family (``hygiene``) / category
    (``orphan-imports``) / kind (``internal-typo`` /
    ``missing-package`` / ``missing-local``) / resolved SARIF level
    (``error`` / ``warning`` / ``note``). Orphan imports are a
    hygiene / dead-edge concern with no CWE / OWASP anchor — family
    + category + kind + severity is the canonical filter-chip shape.
    Producer-side underscores in the kind label
    (``internal_typo`` / ``missing_package`` / ``missing_local``)
    collapse to the URL-safe hyphen form via ``_normalize_tag``.
    """
    # W1062-followup-4: rule-level tags carry the family + category +
    # kind axes so dashboards grouping by rule still get the filter
    # chips even before any specific result lands. Severity is
    # per-result and stamped below.
    _ORPHAN_IMPORTS_RULE_TAGS: dict[str, list[str]] = {
        "orphan-imports/internal-typo": _derive_finding_tags(
            family="hygiene",
            extra=["orphan-imports", "internal-typo"],
        ),
        "orphan-imports/missing-package": _derive_finding_tags(
            family="hygiene",
            extra=["orphan-imports", "missing-package"],
        ),
        "orphan-imports/missing-local": _derive_finding_tags(
            family="hygiene",
            extra=["orphan-imports", "missing-local"],
        ),
    }
    rules = [
        _rule_entry(
            id="orphan-imports/internal-typo",
            short_desc=(
                "Orphan import: top-level package is indexed but the "
                "full dotted path is not — almost certainly a typo or "
                "stale import"
            ),
            help_uri=_HELP_BASE + "orphan-imports",
            default_level="error",
            properties={"tags": list(_ORPHAN_IMPORTS_RULE_TAGS["orphan-imports/internal-typo"])},
        ),
        _rule_entry(
            id="orphan-imports/missing-package",
            short_desc=(
                "Orphan import: module resolves neither in the index "
                "nor via importlib — likely typo or uninstalled "
                "dependency"
            ),
            help_uri=_HELP_BASE + "orphan-imports",
            default_level="warning",
            properties={"tags": list(_ORPHAN_IMPORTS_RULE_TAGS["orphan-imports/missing-package"])},
        ),
        _rule_entry(
            id="orphan-imports/missing-local",
            short_desc=("Orphan import: path-style import did not resolve to an indexed file / package"),
            help_uri=_HELP_BASE + "orphan-imports",
            default_level="warning",
            properties={"tags": list(_ORPHAN_IMPORTS_RULE_TAGS["orphan-imports/missing-local"])},
        ),
    ]

    _KIND_TO_RULE = {
        "internal_typo": "orphan-imports/internal-typo",
        "missing_package": "orphan-imports/missing-package",
        "missing_local": "orphan-imports/missing-local",
    }

    results: list[dict] = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        fpath = f.get("file") or ""
        if not fpath:
            # Without an anchor we cannot surface the finding
            # meaningfully — skip rather than emit an anchorless row.
            continue
        line = f.get("line")
        kind = (f.get("kind") or "").lower()
        rule_id = _KIND_TO_RULE.get(kind)
        if rule_id is None:
            # Future kind label that hasn't landed in the closed
            # enumeration above — skip (matches LAW 8 closed-enum
            # discipline).
            continue

        language = f.get("language") or ""
        module = f.get("module") or ""
        hint = f.get("hint") or ""

        # Message body — surface enough context for triage without a
        # JSON-envelope round-trip. Order: subject (language: module) ->
        # kind -> resolution hint.
        parts = [f"Orphan import: {language} module {module!r}"]
        if hint:
            parts.append(hint)
        message_text = " — ".join(parts)

        # W1062-followup-4: per-result tags add the SARIF-level axis
        # (resolved from kind via the level-mapper) so dashboards can
        # filter by severity chip without re-running the mapper.
        # Producer-side underscore (``internal_typo``) collapses to
        # the URL-safe hyphen form (``internal-typo``) via
        # ``_normalize_tag``.
        result_tags = _derive_finding_tags(
            family="hygiene",
            extra=["orphan-imports", kind],
            severity=_orphan_imports_kind_level(kind),
        )
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=kind,
                locations=[_location(fpath, line)],
                message=message_text,
                level_mapper=_orphan_imports_kind_level,
                properties={"tags": list(result_tags)},
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Over-fetch detector (W1219) ──────────────────────────────────────


def _over_fetch_severity_level(severity: str) -> str:
    """Map an over-fetch severity / confidence label to a SARIF level.

    cmd_over_fetch classifies findings on two parallel axes:

    - Endpoint-level (3-state): ``BARE`` / ``UNGUARDED_RELATION`` carry
      severity ``H``; ``GUARDED_RELATION`` carries severity ``L``.
    - Model-level confidence ladder: ``high`` / ``medium`` / ``low``.

    The SARIF projection collapses both onto the same closed-enum band:

        H / high       -> "warning"  (over-fetch confirmed leak; CI
                                      consumers gate on level=warning
                                      since over-fetch is a bandwidth
                                      / data-exposure concern not a
                                      correctness bug — keeping it
                                      below ``error`` matches the
                                      defaultLevel choice on the rule)
        M / medium     -> "note"     (threshold-only without confirmed
                                      controller-side leak)
        L / low        -> "note"     (already partially guarded OR
                                      weakest threshold signal)

    Unknown labels default to ``"note"`` (LAW 6 — neutrality on
    unfamiliar input).
    """
    label = (severity or "").lower()
    if label in ("h", "high"):
        return "warning"
    # m / medium / l / low / unknown -> "note"
    return "note"


def over_fetch_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam over-fetch`` Laravel/Eloquent over-fetch findings to SARIF.

    cmd_over_fetch emits two parallel finding shapes (also mirrored
    into the central findings registry under detector ``over-fetch``):

    - **Model-level findings** — large ``$fillable`` models without
      ``$hidden`` / ``$visible`` filtering, optionally with confirmed
      controller-side direct returns. One row per model class.
    - **Endpoint-level findings** (3-state classification) — per
      controller method, classified as ``BARE`` / ``UNGUARDED_RELATION``
      (severity ``H``) or ``GUARDED_RELATION`` (severity ``L``, partial
      fix already applied).

    SARIF projection emits a single closed-enum rule
    ``over-fetch/select-star-or-wide-query`` (defaultLevel ``warning``)
    — over-fetch is a single failure mode (returning more columns than
    necessary), surfaced through several detector heuristics. The rule
    catalogue stays tight rather than minting separate ids per
    confidence band; the per-finding ``level`` carries the
    severity/confidence distinction via :func:`_over_fetch_severity_level`.

    Per-finding anchor:

    - Endpoint findings: anchor at ``file:line`` of the controller
      method (the query/load site itself).
    - Model findings: anchor at ``model_location`` (the model class
      declaration line) — the canonical edit site when the fix is to
      add ``$hidden`` / ``$visible`` / an API Resource scaffold.

    Findings missing both a file path and a parseable location are
    skipped silently. The rule catalogue is always emitted (one rule)
    so consumers can introspect even on a clean run. Mirrors the
    closed-enum design from :func:`auth_gaps_to_sarif` (W1195) and
    :func:`n1_to_sarif` (W1208), but with a single rule because
    over-fetch is one failure mode rather than three distinct kinds.

    *findings* parameter accepts a single combined list. Callers pass
    the concatenation of the JSON envelope's ``findings`` (model-level)
    and ``endpoint_findings`` (3-state) lists.
    """
    # W1062-followup-3: rule-level tags carry the family + category
    # axes so dashboards grouping by rule still get the filter chips
    # even before any specific result lands. Severity is per-result and
    # stamped below from the resolved SARIF level. Over-fetch is a
    # performance / data-exposure concern with no CWE / OWASP anchor —
    # family + category + scope + severity is the canonical filter
    # shape.
    _OVER_FETCH_RULE_TAGS = _derive_finding_tags(
        family="performance",
        extra=["over-fetch"],
    )
    rules = [
        _rule_entry(
            id="over-fetch/select-star-or-wide-query",
            short_desc=(
                "Over-fetch: model serializes more fields than "
                "necessary, or query loads columns/relations the "
                "response does not need"
            ),
            help_uri=_HELP_BASE + "over-fetch",
            default_level="warning",
            properties={"tags": list(_OVER_FETCH_RULE_TAGS)},
        ),
    ]

    rule_id = "over-fetch/select-star-or-wide-query"
    results: list[dict] = []

    for f in findings or []:
        if not isinstance(f, dict):
            continue

        # Endpoint findings carry ``file`` + ``line`` + ``state`` +
        # ``severity``; model findings carry ``model_path`` +
        # ``model_location`` + ``confidence``. We branch on the
        # presence of ``state`` (endpoint discriminator) so a future
        # third finding shape doesn't silently fall through.
        state = f.get("state")
        if state:
            # Endpoint-level finding.
            fpath = f.get("file") or ""
            line = f.get("line")
            if not fpath:
                # Without an anchor we cannot surface the finding
                # meaningfully — skip rather than emit an anchorless
                # row (matches Pattern 1 / LAW 6 disclosure rules).
                continue
            severity_label = f.get("severity") or ""
            endpoint = f.get("endpoint") or ""
            evidence_text = f.get("evidence") or ""
            recommendation = f.get("recommendation") or ""

            parts = [f"Over-fetch endpoint: {endpoint} [state={state}]"]
            if evidence_text:
                parts.append(f"Evidence: {evidence_text}")
            if recommendation:
                parts.append(f"Fix: {recommendation}")
            message_text = " — ".join(parts)

            # W1062-followup-3: per-result tags add the scope
            # (``endpoint``) + resolved SARIF-level axes so dashboards
            # can slice on actionable bands without re-running the
            # mapper. Producer-side ``H`` / ``L`` severity letters
            # converge to ``warning`` / ``note`` via the helper's
            # ``_normalize_tag`` chokepoint.
            endpoint_tags = _derive_finding_tags(
                family="performance",
                extra=["over-fetch", "endpoint"],
                severity=_over_fetch_severity_level(severity_label),
            )
            results.append(
                _result_entry(
                    rule_id=rule_id,
                    severity=severity_label,
                    locations=[_location(fpath, line)],
                    message=message_text,
                    level_mapper=_over_fetch_severity_level,
                    properties={"tags": list(endpoint_tags)},
                )
            )
            continue

        # Model-level finding.
        model_path = f.get("model_path") or ""
        # Parse model_location ("path:line") for the line anchor; fall
        # back to the raw model_path when location is malformed.
        model_loc_str = f.get("model_location") or ""
        line_anchor: int | None = None
        if model_loc_str:
            parsed_path, parsed_line = _parse_loc_string(model_loc_str)
            if parsed_path:
                model_path = parsed_path
            line_anchor = parsed_line
        if not model_path:
            continue

        confidence = (f.get("confidence") or "").lower()
        if confidence not in ("high", "medium", "low"):
            # Future confidence label that hasn't landed in the closed
            # enumeration above — skip (matches LAW 8 closed-enum
            # discipline).
            continue

        model_name = f.get("model_name") or ""
        fillable = f.get("fillable_count")
        hidden = f.get("hidden_count")
        exposed = f.get("exposed_count")
        reasons = f.get("reasons") or []

        parts = [f"Over-fetch model: {model_name} [confidence={confidence}]"]
        if fillable is not None:
            parts.append(f"{fillable} fillable, {hidden} hidden, {exposed} exposed")
        if reasons:
            # Cap to first reason — message body stays terse so SARIF
            # consumers don't blow past viewer rendering limits.
            parts.append(str(reasons[0]))
        message_text = " — ".join(str(p) for p in parts)

        # W1062-followup-3: per-result tags add the scope (``model``) +
        # confidence + resolved SARIF-level axes so dashboards can
        # isolate the model-level rows from the endpoint-level rows
        # under the same rule.
        model_tags = _derive_finding_tags(
            family="performance",
            extra=["over-fetch", "model", confidence],
            severity=_over_fetch_severity_level(confidence),
        )
        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=confidence,
                locations=[_location(model_path, line_anchor)],
                message=message_text,
                level_mapper=_over_fetch_severity_level,
                properties={"tags": list(model_tags)},
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Bus factor (knowledge-loss / single-owner risk) ──────────────────


def _bus_factor_risk_level(risk: str) -> str:
    """Map a bus-factor risk label to a SARIF level (closed enum).

    cmd_bus_factor classifies each directory's risk via
    :func:`roam.commands.cmd_bus_factor._risk_label` onto a 3-band
    ladder. The SARIF projection collapses that ladder onto the SARIF
    level vocabulary so a CI gate keyed off ``level: warning`` blocks
    on the actionable bands without surfacing the long advisory tail:

        HIGH    -> "warning"  (single-point-of-failure module —
                               concentrated ownership and/or stale
                               primary author; review-recommended)
        MEDIUM  -> "note"     (elevated risk but bus_factor >= 2 or
                               primary author still active)
        LOW     -> "note"     (well-distributed ownership)

    Bus-factor is a knowledge-loss / staffing signal, not a correctness
    bug — keeping HIGH at ``warning`` (rather than ``error``) matches
    the W1195 auth-gaps "deterministic kinds gate, advisory kinds
    surface" discipline. Unknown labels default to ``"note"``
    (LAW 6 — neutrality on unfamiliar input).
    """
    label = (risk or "").upper()
    if label == "HIGH":
        return "warning"
    # MEDIUM / LOW / unknown -> "note"
    return "note"


def bus_factor_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam bus-factor`` knowledge-loss findings to SARIF.

    cmd_bus_factor ranks every directory by a risk score combining
    Shannon entropy of contribution shares, primary-author churn
    concentration, and primary-author inactivity (staleness factor).
    The detector also persists findings into the central registry under
    detector ``bus-factor`` with three sub-kinds; the SARIF projection
    mirrors that closed enumeration onto three rule ids with distinct
    ``defaultLevel`` so a CI gate keyed off SARIF ``level: warning``
    only blocks on the actionable risk bands:

    - ``bus-factor/author-concentration`` (defaultLevel ``warning``):
      a single author owns >70% of churn for the directory. Confidence
      tier ``heuristic`` — author-count rollups are fuzzy signals.
    - ``bus-factor/stale-ownership`` (defaultLevel ``warning``): the
      primary author has been inactive longer than the configured
      ``--stale-months`` threshold. Confidence tier ``heuristic`` —
      inactivity is a proxy for knowledge loss, not a guarantee.
    - ``bus-factor/solo-author-summary`` (defaultLevel ``note``): the
      repo-level summary finding emitted on solo-author repos (W164
      collapse). Informational by design — "bus factor 1" is the
      baseline on a solo project, not an actionable finding.

    Per-finding anchor: the **directory path** (no line number — the
    risk applies to the directory as a whole, not a specific symbol).
    SARIF supports directory-style ``artifactLocation.uri`` entries
    with no ``region`` key; :func:`_physical_location` already drops
    the region when no line is supplied. For the repo-level summary,
    the anchor is the repo root (``"./"``) since the finding spans
    the entire repository.

    Input shape: callers pass the ``results`` list built by
    :func:`roam.commands.cmd_bus_factor._analyse_bus_factor` (the
    per-directory ranking surfaced via the JSON envelope's
    ``directories[]`` field). Optionally the list may include a
    repo-level summary entry with ``summary_only=True``; that row
    projects onto the ``solo-author-summary`` rule.

    A directory that is BOTH ``concentrated`` AND ``stale_primary``
    emits TWO results (one per kind) so a SARIF consumer filtering by
    rule id can isolate just the stale set when triaging. Directories
    flagged neither concentrated nor stale_primary are skipped — they
    are below the registry-emission threshold and would dilute the
    SARIF surface with non-actionable rows.

    Empty / no-results envelopes produce a valid SARIF document with
    zero results (rules catalogue is always emitted so consumers can
    introspect the rule vocabulary even on a clean run).
    """
    # W1062-followup-3: rule-level tags carry the family + category
    # axes so dashboards grouping by rule still get the filter chips
    # even before any specific result lands. Severity is per-result and
    # stamped below from the actual risk label.
    _BUS_FACTOR_RULE_TAGS: dict[str, list[str]] = {
        "bus-factor/author-concentration": _derive_finding_tags(
            family="governance",
            extra=["bus-factor", "author-concentration"],
        ),
        "bus-factor/stale-ownership": _derive_finding_tags(
            family="governance",
            extra=["bus-factor", "stale-ownership"],
        ),
        "bus-factor/solo-author-summary": _derive_finding_tags(
            family="governance",
            extra=["bus-factor", "solo-author-summary"],
        ),
    }
    rules = [
        _rule_entry(
            id="bus-factor/author-concentration",
            short_desc=(
                "Directory ownership concentrated in a single author "
                "(>70% of churn) — knowledge loss risk if author "
                "departs"
            ),
            help_uri=_HELP_BASE + "bus-factor",
            default_level="warning",
            properties={"tags": list(_BUS_FACTOR_RULE_TAGS["bus-factor/author-concentration"])},
        ),
        _rule_entry(
            id="bus-factor/stale-ownership",
            short_desc=("Directory primary author inactive beyond the stale-months threshold — forgotten module risk"),
            help_uri=_HELP_BASE + "bus-factor",
            default_level="warning",
            properties={"tags": list(_BUS_FACTOR_RULE_TAGS["bus-factor/stale-ownership"])},
        ),
        _rule_entry(
            id="bus-factor/solo-author-summary",
            short_desc=(
                "Repo-level solo-author summary (W164 collapse of "
                "per-directory author-concentration rows on "
                "single-author repos)"
            ),
            help_uri=_HELP_BASE + "bus-factor",
            default_level="note",
            properties={"tags": list(_BUS_FACTOR_RULE_TAGS["bus-factor/solo-author-summary"])},
        ),
    ]

    results: list[dict] = []

    for r in findings or []:
        if not isinstance(r, dict):
            continue

        # Repo-level summary (W164 collapse) — emitted on solo-author
        # repos as a single roll-up row in place of N per-directory
        # author-concentration findings.
        if r.get("summary_only"):
            repo = r.get("repo") or "./"
            unique_authors = r.get("unique_authors_count")
            dominant = r.get("dominant_actor") or r.get("dominant_author") or ""
            dominant_pct = r.get("dominant_author_share_pct")
            total_dirs = r.get("total_directories_analyzed")

            parts = ["Solo-author repo summary"]
            if dominant:
                parts.append(
                    f"{dominant} owns {dominant_pct}% of churn"
                    if dominant_pct is not None
                    else f"primary author: {dominant}"
                )
            if total_dirs is not None:
                parts.append(f"{total_dirs} directories analysed")
            if unique_authors is not None:
                parts.append(f"{unique_authors} unique authors")
            message_text = " — ".join(str(p) for p in parts)

            # W1062-followup-3: per-result tags add the SARIF-level axis
            # (the solo-summary row is always "LOW" risk -> "note" level
            # via _bus_factor_risk_level — pre-resolve here so the tag
            # vocabulary stays lowercase).
            solo_tags = _derive_finding_tags(
                family="governance",
                extra=["bus-factor", "solo-author-summary"],
                severity=_bus_factor_risk_level("LOW"),
            )
            results.append(
                _result_entry(
                    rule_id="bus-factor/solo-author-summary",
                    severity="LOW",
                    locations=[_location(repo, None)],
                    message=message_text,
                    level_mapper=_bus_factor_risk_level,
                    properties={"tags": list(solo_tags)},
                )
            )
            continue

        directory = r.get("directory") or ""
        if not directory:
            # Without a directory anchor we cannot surface the row
            # meaningfully — skip rather than emit an anchorless
            # result (matches Pattern 1 / LAW 6 disclosure rules).
            continue

        concentrated = bool(r.get("concentrated"))
        stale_primary = bool(r.get("stale_primary"))
        if not concentrated and not stale_primary:
            # Below the persist threshold — the long tail of
            # well-distributed directories is not actionable SARIF
            # output. Mirrors the cmd_bus_factor --persist gate.
            continue

        risk = r.get("risk") or ""
        primary_author = r.get("primary_actor") or r.get("primary_author") or "unknown"
        primary_share_pct = r.get("primary_share_pct", 0)
        bus_factor = r.get("bus_factor", 1)
        entropy = r.get("entropy", 0.0)
        staleness_factor = r.get("staleness_factor", 1.0)

        if concentrated:
            # entropy is a float — format defensively in case the
            # producer emits it as a string or None on a degraded
            # path.
            try:
                entropy_str = f"{float(entropy):.2f}"
            except (TypeError, ValueError):
                entropy_str = str(entropy)
            message_text = (
                f"Bus-factor risk: {directory} is "
                f"{primary_share_pct}%-owned by {primary_author} "
                f"({bus_factor} effective contributor"
                f"{'s' if bus_factor != 1 else ''}, "
                f"entropy {entropy_str}) [risk={risk}]"
            )
            # W1062-followup-3: per-result tags add the risk + SARIF
            # level axes so dashboards can slice on actionable bands
            # (HIGH -> warning) from the result chip set without
            # re-resolving locally. Producer-side uppercase risk labels
            # converge to lowercase via the helper's _normalize_tag
            # chokepoint.
            concentration_tags = _derive_finding_tags(
                family="governance",
                extra=["bus-factor", "author-concentration", risk],
                severity=_bus_factor_risk_level(risk),
            )
            results.append(
                _result_entry(
                    rule_id="bus-factor/author-concentration",
                    severity=risk,
                    locations=[_location(directory, None)],
                    message=message_text,
                    level_mapper=_bus_factor_risk_level,
                    properties={"tags": list(concentration_tags)},
                )
            )

        if stale_primary:
            try:
                staleness_str = f"{float(staleness_factor):.2f}"
            except (TypeError, ValueError):
                staleness_str = str(staleness_factor)
            message_text = (
                f"Stale ownership: {directory} primary author "
                f"{primary_author} ({primary_share_pct}% share) "
                f"inactive — staleness factor {staleness_str} "
                f"[risk={risk}]"
            )
            # W1062-followup-3: per-result tags for the stale-ownership
            # row mirror the author-concentration shape — same family,
            # different kind / risk surfaces.
            stale_tags = _derive_finding_tags(
                family="governance",
                extra=["bus-factor", "stale-ownership", risk],
                severity=_bus_factor_risk_level(risk),
            )
            results.append(
                _result_entry(
                    rule_id="bus-factor/stale-ownership",
                    severity=risk,
                    locations=[_location(directory, None)],
                    message=message_text,
                    level_mapper=_bus_factor_risk_level,
                    properties={"tags": list(stale_tags)},
                )
            )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Laws detector (W1216) ────────────────────────────────────────────


def _laws_severity_level(severity: str) -> str:
    """Map a law-violation severity label to a SARIF level (closed enum).

    The laws checker emits violations under three severity bands
    (see :class:`roam.laws.miner.Violation` + the per-kind ``severity``
    field on :class:`roam.laws.miner.Law`):

        blocker  -> "error"    (a violation the constitution treats as
                                CI-blocking — mined laws today never
                                set this, but the policy DSL can raise
                                advisory laws to blocker via the rule
                                ``severity`` override).
        warning  -> "warning"  (intermediate band — flagged for review
                                but not CI-blocking).
        advisory -> "note"     (the default mined-law severity; named
                                "advisory" because mined laws describe
                                emergent conventions, not invariants
                                — false-positive > false-negative when
                                the gate is advisory).

    Unknown labels default to ``"note"`` (LAW 6 — neutrality on
    unfamiliar input). Mirrors the closed-enum band design from
    :func:`_n1_confidence_level` (W1208) and
    :func:`_auth_gaps_confidence_tier_level` (W1195).
    """
    label = (severity or "").lower()
    if label == "blocker":
        return "error"
    if label == "warning":
        return "warning"
    # advisory / unknown -> "note"
    return "note"


# Closed enumeration of law kinds (mirrors
# :data:`roam.commands.cmd_laws._LAW_KIND_TO_CONFIDENCE`). Each kind
# projects onto a distinct SARIF rule id so a CI consumer can filter
# violations by category (e.g., only block on import-layering breaks
# while letting naming drift surface as advisory). Extending this set
# means adding a kind to BOTH the miner (Law.kind) AND this map.
_LAWS_KIND_TO_RULE: dict[str, str] = {
    "naming": "laws/naming",
    "import": "laws/import-layering",
    "testing": "laws/test-coverage",
    "errors": "laws/error-handling",
    "co_change": "laws/co-change",
}


def laws_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam laws check`` violations to SARIF.

    *findings* is the raw violation list ``cmd_laws`` builds for the
    JSON envelope (one entry per :class:`roam.laws.miner.Violation` —
    see :mod:`roam.laws.checker` for the dict shape:
    ``{law_id, kind, severity, confidence, message, file, line,
    evidence}``). Each violation maps onto one of five closed-enum
    rule ids by ``kind``, with a uniform ``defaultLevel`` of
    ``"note"`` reflecting the mined-laws "false-positive > false-
    negative" stance (per-finding level still varies via
    :func:`_laws_severity_level` when a rule override raises a
    violation to ``warning`` / ``blocker``):

    - ``laws/naming`` (defaultLevel ``note``): symbol name violates
      the dominant naming style mined for its kind (e.g., function
      added in PascalCase when the codebase is overwhelmingly
      snake_case).
    - ``laws/import-layering`` (defaultLevel ``note``): import edge
      added that breaks a mined import-layering law (e.g.,
      ``src/handlers`` importing from ``src/db`` when the mined law
      forbids it).
    - ``laws/test-coverage`` (defaultLevel ``note``): new public
      symbol added without a matching test file (e.g., new public
      function without ``test_<name>.py``).
    - ``laws/error-handling`` (defaultLevel ``note``): stub band for
      the future error-handling-pattern law-kind (mined laws return
      ``[]`` for this kind today — rule emitted so SARIF consumers
      can introspect the closed-enum catalogue).
    - ``laws/co-change`` (defaultLevel ``note``): stub band for the
      future co-change law-kind (mined laws return ``[]`` for this
      kind today — rule emitted so SARIF consumers can introspect
      the closed-enum catalogue).

    Per-finding anchor: ``file`` + ``line`` (the diff hunk line that
    introduced the violation). The message body includes the law id
    so SARIF consumers can pivot to ``roam laws explain <id>`` for
    full evidence without a JSON-envelope round-trip.

    Findings missing an anchor (empty ``file``) are skipped silently
    — without an anchor SARIF consumers cannot surface the row
    meaningfully (matches the Pattern 1 / LAW 6 disclosure rules
    from :func:`orphan_imports_to_sarif`). Future kinds that haven't
    landed in the closed enumeration above are also skipped
    (LAW 8 closed-enum discipline). The rule catalogue is always
    emitted (closed enum of 5 rules) so consumers can introspect the
    rule vocabulary even on a clean run.

    Empty / no-results envelopes produce a valid SARIF document with
    zero results — mirrors :func:`bus_factor_to_sarif` (W1215) and
    :func:`orphan_imports_to_sarif` (W1218).
    """
    rules = [
        _rule_entry(
            id="laws/naming",
            short_desc=(
                "Naming-convention violation against a mined law "
                "(symbol name does not follow the dominant style for "
                "its kind)"
            ),
            help_uri=_HELP_BASE + "laws",
            default_level="note",
        ),
        _rule_entry(
            id="laws/import-layering",
            short_desc=(
                "Import-layering violation against a mined law "
                "(directory-to-directory import edge breaks the "
                "discovered architectural rule)"
            ),
            help_uri=_HELP_BASE + "laws",
            default_level="note",
        ),
        _rule_entry(
            id="laws/test-coverage",
            short_desc=(
                "Test-coverage violation against a mined law (new public symbol added without a matching test file)"
            ),
            help_uri=_HELP_BASE + "laws",
            default_level="note",
        ),
        _rule_entry(
            id="laws/error-handling",
            short_desc=(
                "Error-handling-pattern violation against a mined law (stub kind — reserved for future wiring)"
            ),
            help_uri=_HELP_BASE + "laws",
            default_level="note",
        ),
        _rule_entry(
            id="laws/co-change",
            short_desc=("Co-change violation against a mined law (stub kind — reserved for future wiring)"),
            help_uri=_HELP_BASE + "laws",
            default_level="note",
        ),
    ]

    results: list[dict] = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        fpath = f.get("file") or ""
        if not fpath:
            # Without an anchor we cannot surface the finding
            # meaningfully — skip rather than emit an anchorless row.
            continue
        kind = (f.get("kind") or "").lower()
        rule_id = _LAWS_KIND_TO_RULE.get(kind)
        if rule_id is None:
            # Future kind that hasn't landed in the closed enumeration
            # above — skip (LAW 8 closed-enum discipline).
            continue

        line = f.get("line") or None
        # The SARIF region key is dropped when no line is supplied
        # (see :func:`_physical_location`); pass ``None`` rather than
        # the integer 0 so empty-line violations don't anchor to the
        # synthetic ``startLine: 0``.
        if isinstance(line, int) and line <= 0:
            line = None

        law_id = f.get("law_id") or ""
        message = f.get("message") or ""
        severity = f.get("severity") or "advisory"

        # Message body — surface enough context for triage without a
        # JSON-envelope round-trip. Order: law id -> message body ->
        # severity band.
        parts = []
        if law_id:
            parts.append(f"[{law_id}]")
        if message:
            parts.append(message)
        parts.append(f"(severity={severity})")
        message_text = " ".join(parts)

        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=severity,
                locations=[_location(fpath, line)],
                message=message_text,
                level_mapper=_laws_severity_level,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── LLM-API anti-pattern smells (W1207) ──────────────────────────────


# Closed enumeration of llm-smells pattern kinds (mirrors
# :data:`roam.commands.cmd_llm_smells._LLM_SMELLS_KINDS`). Each kind
# projects onto a distinct SARIF rule id under the ``llm-smells/``
# namespace so a CI consumer can filter findings by category
# (e.g., gate on ``llm-smells/direct-user-input-concatenation`` only
# while letting ``llm-smells/temperature-not-set`` surface as advisory).
# Extending this set means adding a kind to BOTH the detector
# (``_DETECTORS`` tuple) AND this map.
#
# The rule id uses the catalog short name (e.g. ``no-model-version-pinning``)
# rather than the registry kind string (``llm_api_no_model_version_pinning``)
# so SARIF consumers see a stable, dash-cased projection that matches the
# pattern catalog at ``(internal memo)``.
_LLM_SMELLS_KIND_TO_RULE: dict[str, str] = {
    "llm_api_no_model_version_pinning": "llm-smells/no-model-version-pinning",
    "llm_api_missing_max_tokens": "llm-smells/missing-max-tokens",
    "llm_api_direct_user_input_concatenation": ("llm-smells/direct-user-input-concatenation"),
    "llm_api_no_structured_output_validation": ("llm-smells/no-structured-output-validation"),
    "llm_api_temperature_not_set": "llm-smells/temperature-not-set",
    "llm_api_missing_timeout": "llm-smells/missing-timeout",
    "llm_api_missing_max_retries": "llm-smells/missing-max-retries",
    "llm_api_no_system_message": "llm-smells/no-system-message",
    "llm_api_no_retry_on_rate_limit": "llm-smells/no-retry-on-rate-limit",
    "llm_api_call_in_loop": "llm-smells/call-in-loop",
}


# Per-rule short descriptions for the SARIF rule catalogue. Mirrors the
# label vocabulary at :data:`roam.commands.cmd_llm_smells._PATTERN_LABELS`
# but phrased in noun-form so each rule reads cleanly as a SARIF
# ``shortDescription`` (LAW 4 concrete-noun anchoring).
_LLM_SMELLS_RULE_DESCRIPTIONS: dict[str, str] = {
    "llm-smells/no-model-version-pinning": (
        "LLM-API smell: model identifier uses a moving alias rather than "
        "an immutable dated snapshot (arXiv:2512.18020 §3.2 NMVP)"
    ),
    "llm-smells/missing-max-tokens": (
        "LLM-API smell: completion call lacks an output-token bound "
        "(max_tokens / max_output_tokens / max_new_tokens — cost surface)"
    ),
    "llm-smells/direct-user-input-concatenation": (
        "LLM-API smell: user-controlled identifier concatenated into a "
        "prompt-shaped string in the same function (OWASP LLM01:2025)"
    ),
    "llm-smells/no-structured-output-validation": (
        "LLM-API smell: json.loads on LLM response content without a surrounding try/except (parse-failure surface)"
    ),
    "llm-smells/temperature-not-set": (
        "LLM-API smell: completion call without an explicit temperature= kwarg (arXiv:2512.18020 §3.5 TNES)"
    ),
    "llm-smells/missing-timeout": (
        "LLM-API smell: LLM client constructed without a timeout= kwarg (requests can hang indefinitely)"
    ),
    "llm-smells/missing-max-retries": (
        "LLM-API smell: LLM client constructed without an explicit max_retries= kwarg (relies on opaque SDK defaults)"
    ),
    "llm-smells/no-system-message": (
        "LLM-API smell: chat-completion call with inline messages=[...] "
        "lacking a role: system entry (arXiv:2512.18020 §3.3 NSM)"
    ),
    "llm-smells/no-retry-on-rate-limit": (
        "LLM-API smell: file-level — LLM-using file contains no retry / "
        "backoff / RateLimitError indicator (operational gap under load)"
    ),
    "llm-smells/call-in-loop": (
        "LLM-API smell: completion call within 30 lines of an unbounded loop header (cost-spiral surface)"
    ),
}


# Per-rule defaultLevel — uses the canonical severity table from
# :data:`roam.commands.cmd_llm_smells._PATTERN_SEVERITY` but projected
# through :func:`_to_level` so the rule catalogue advertises the SARIF
# band each kind typically lands in. Per-finding level still overrides
# via the finding's ``severity`` field (e.g., a future detector
# refinement could downgrade an instance of ``call-in-loop`` to ``info``
# without changing the rule's defaultLevel).
#
# Mapping (severity -> SARIF level via :func:`_to_level`):
#     critical -> "error"  (only ``direct-user-input-concatenation``)
#     warning  -> "warning"
#     info     -> "note"
_LLM_SMELLS_RULE_DEFAULT_LEVELS: dict[str, str] = {
    "llm-smells/no-model-version-pinning": "warning",
    "llm-smells/missing-max-tokens": "warning",
    "llm-smells/direct-user-input-concatenation": "error",
    "llm-smells/no-structured-output-validation": "warning",
    "llm-smells/temperature-not-set": "note",
    "llm-smells/missing-timeout": "warning",
    "llm-smells/missing-max-retries": "note",
    "llm-smells/no-system-message": "warning",
    "llm-smells/no-retry-on-rate-limit": "warning",
    "llm-smells/call-in-loop": "warning",
}


def llm_smells_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam llm-smells`` detector output to SARIF.

    *findings* is the per-occurrence list ``cmd_llm_smells`` builds for
    the JSON envelope (``findings[]``). Each entry has the shape::

        {
            "kind": "<llm_api_*>",       # registry kind string
            "file": "<relative path>",
            "line": <1-based int>,
            "severity": "info" | "warning" | "critical",
            "confidence": "heuristic",   # uniform across the v1.1 catalog
            "snippet": "<<=120 char excerpt>",
        }

    Each finding projects onto one of ten closed-enum rule ids under the
    ``llm-smells/`` namespace (see :data:`_LLM_SMELLS_KIND_TO_RULE`).
    The rule catalogue is always emitted in full (10 rules) so SARIF
    consumers can introspect the kind vocabulary even on a clean run —
    mirrors :func:`laws_to_sarif` (W1216) and :func:`bus_factor_to_sarif`
    (W1215). Per-finding severity drives the SARIF ``level`` (closed
    mapping via :func:`_to_level`):

        critical -> "error"
        warning  -> "warning"
        info     -> "note"

    Per-finding anchor: ``file`` + ``line`` directly from the envelope
    entry. The message body includes the rule short name + a trimmed
    snippet so SARIF consumers can triage critical findings
    (``direct-user-input-concatenation`` — OWASP LLM01:2025) without a
    JSON-envelope round-trip.

    Findings missing an anchor (empty ``file``) are skipped silently —
    without an anchor SARIF consumers cannot surface the row meaningfully
    (matches the Pattern 1 / LAW 6 disclosure rules from
    :func:`orphan_imports_to_sarif`). Unknown kinds are also skipped
    (LAW 8 closed-enum discipline — extending the vocabulary is a
    deliberate edit to the detector registry, not free-string
    composition). Empty ``findings`` produces a valid SARIF envelope
    with zero results (rules catalogue stays populated).

    W1207 — first SARIF projection for the W415 / W415b LLM-API
    anti-pattern catalog. Audience: teams shipping LLM-powered features
    that want pre-prod gating on cost / security / robustness smells via
    GitHub Code Scanning or any SARIF-aware viewer.
    """
    # Rule catalogue — closed enum of 10 rules sorted alphabetically by
    # rule id (matches the W896 SARIF-stable-output convention adopted
    # by smells_to_sarif). Per-rule defaultLevel reflects the canonical
    # severity-band each kind typically lands in; per-finding level
    # always overrides via the closed _to_level mapping.
    rule_ids_sorted = sorted(_LLM_SMELLS_KIND_TO_RULE.values())
    rules = [
        _rule_entry(
            id=rule_id,
            short_desc=_LLM_SMELLS_RULE_DESCRIPTIONS[rule_id],
            help_uri=_HELP_BASE + "llm-smells",
            default_level=_LLM_SMELLS_RULE_DEFAULT_LEVELS[rule_id],
        )
        for rule_id in rule_ids_sorted
    ]
    known_rule_ids = set(rule_ids_sorted)

    results: list[dict] = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        kind = f.get("kind") or ""
        rule_id = _LLM_SMELLS_KIND_TO_RULE.get(kind)
        if rule_id is None:
            # Future kind that hasn't landed in the closed enumeration
            # above — skip (LAW 8 closed-enum discipline). A new
            # llm-smells pattern lands here once it's wired into BOTH
            # the detector registry AND _LLM_SMELLS_KIND_TO_RULE.
            continue
        if rule_id not in known_rule_ids:  # defence-in-depth
            continue

        fpath = f.get("file") or ""
        if not fpath:
            # Without a file anchor the finding cannot be surfaced
            # meaningfully in SARIF — skip rather than emit an
            # anchorless row (matches laws_to_sarif).
            continue

        line = f.get("line") or None
        # Drop the SARIF region key when no positive line is supplied
        # (matches :func:`_physical_location` behaviour).
        if isinstance(line, int) and line <= 0:
            line = None

        severity = f.get("severity") or "info"
        snippet = (f.get("snippet") or "").strip()

        # Message body: rule short name + (optional) trimmed snippet
        # for triage anchor. The short name strips the ``llm-smells/``
        # prefix so the message stays compact in viewers that already
        # show the rule id alongside.
        short_name = rule_id.split("/", 1)[-1]
        if snippet:
            # Snippet is already <= 120 chars per the detector; trim
            # further to 80 here so the SARIF message doesn't dominate
            # the viewer row.
            trimmed = snippet[:80]
            message_text = f"{short_name}: {trimmed}"
        else:
            message_text = short_name

        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=severity,
                locations=[_location(fpath, line)],
                message=message_text,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Fan detector (W1209) ─────────────────────────────────────────────


def _fan_flag_level(flag: str) -> str:
    """Map a fan architectural flag to a SARIF level (closed enum).

    The fan detector emits three cross-file architectural flags (see
    :data:`roam.commands.cmd_fan._FAN_FLAG_TO_KIND`) — the W150 audit
    intentionally froze this set, so the SARIF level mapping is a
    one-to-one closed enum too:

        HIGH-RISK -> "error"    (both fan-in and fan-out cross-file
                                 thresholds breached — the symbol /
                                 file is a hub AND a spreader, so it
                                 amplifies blast radius in both
                                 directions and is the highest
                                 architectural-risk band).
        hub       -> "note"     (high cross-file fan-in — many distinct
                                 files import / call this symbol; it
                                 absorbs change pressure but does not
                                 propagate it outward, so the advisory
                                 band is the right default).
        spreader  -> "warning"  (high cross-file fan-out — this symbol
                                 reaches into many distinct files;
                                 changes here propagate outward, so
                                 the warning band reflects the higher
                                 blast-radius risk relative to a hub).

    Unknown labels default to ``"note"`` (LAW 6 — neutrality on
    unfamiliar input). Mirrors the closed-enum band design from
    :func:`_laws_severity_level` (W1216) and
    :func:`_partition_conflict_risk_level` (W1159).
    """
    if flag == "HIGH-RISK":
        return "error"
    if flag == "spreader":
        return "warning"
    # hub or unknown -> "note"
    return "note"


# Closed enumeration of fan flags -> SARIF rule ids. Mirrors
# :data:`roam.commands.cmd_fan._FAN_FLAG_TO_KIND` (which projects flags
# to findings-registry kinds). Both maps stay in sync — extending the
# flag vocabulary means adding entries to BOTH (and re-running the W150
# audit + the W152 detector-version bump).
_FAN_FLAG_TO_RULE: dict[str, str] = {
    "HIGH-RISK": "fan/high-risk",
    "hub": "fan/hub",
    "spreader": "fan/spreader",
}


def fan_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam fan`` cross-file fan-in/out findings to SARIF.

    *findings* is the unified per-finding list produced by
    :mod:`roam.commands.cmd_fan`. Two detector surfaces feed the same
    SARIF projection (matches the dual ``source_detector`` design from
    the W150 audit + W152 findings-registry mirror):

    - **fan-symbol** rows carry ``mode == "symbol"`` with ``symbol_name``
      / ``file_path`` / ``line_start`` (or a ``location`` string of the
      form ``"path:line"``).
    - **fan-file** rows carry ``mode == "file"`` with ``file_path`` (and
      no line, since the metric applies to the whole file).

    Three closed-enum rule ids project the three architectural flags:

    - ``fan/hub`` (defaultLevel ``note``): high cross-file fan-in —
      many distinct files import / call this symbol. Absorbs change
      pressure but does not propagate it outward, so the advisory band
      is the right default.
    - ``fan/spreader`` (defaultLevel ``warning``): high cross-file
      fan-out — this symbol reaches into many distinct files. Changes
      here propagate outward, so the warning band reflects the higher
      blast-radius risk relative to a hub.
    - ``fan/high-risk`` (defaultLevel ``error``): both directions over
      threshold (hub AND spreader concurrently) — amplifies blast
      radius in both directions, the highest architectural-risk band.

    Per-finding level is derived from the finding's ``flag`` via
    :func:`_fan_flag_level` — same as the rule defaultLevel, since the
    flag IS the severity band (a "hub" finding cannot escalate to
    "error" without becoming a HIGH-RISK row, by definition).

    Per-finding anchor: ``file_path`` + (optional) ``line_start`` for
    symbol-mode findings; ``file_path`` only for file-mode (no line —
    the metric applies to the whole file). The message body includes
    the symbol name (when present), the flag, and the fan_in / fan_out
    numbers so SARIF consumers can triage without a JSON-envelope
    round-trip.

    Local-only flags (``local-hub`` / ``local-spreader``) and rows
    with an empty flag are skipped silently — the W150 audit classifies
    them as non-architectural (single-file by design — one large SFC,
    generated module) so emitting them would bloat SARIF output with
    non-actionable rows. Mirrors the findings-registry filter in
    :func:`roam.commands.cmd_fan._emit_fan_findings`.

    Findings missing an anchor (empty ``file_path`` / unparseable
    ``location``) are skipped silently — without an anchor SARIF
    consumers cannot surface the row meaningfully (matches the
    Pattern 1 / LAW 6 disclosure rules from :func:`laws_to_sarif`).
    Future flag values that haven't landed in the closed enumeration
    above are also skipped (LAW 8 closed-enum discipline).

    Empty / no-results envelopes produce a valid SARIF document with
    zero results — mirrors :func:`laws_to_sarif` (W1216) and
    :func:`bus_factor_to_sarif` (W1215). The rule catalogue is always
    emitted (closed enum of 3 rules) so consumers can introspect the
    rule vocabulary even on a clean run.
    """
    rules = [
        _rule_entry(
            id="fan/hub",
            short_desc=(
                "Architectural hub: high cross-file fan-in (many distinct files import / call this symbol or file)"
            ),
            help_uri=_HELP_BASE + "fan",
            default_level="note",
        ),
        _rule_entry(
            id="fan/spreader",
            short_desc=(
                "Architectural spreader: high cross-file fan-out (this "
                "symbol or file reaches into many distinct files — "
                "changes here propagate outward)"
            ),
            help_uri=_HELP_BASE + "fan",
            default_level="warning",
        ),
        _rule_entry(
            id="fan/high-risk",
            short_desc=(
                "Architectural high-risk: both cross-file fan-in AND "
                "fan-out over threshold (hub AND spreader concurrently — "
                "amplifies blast radius in both directions)"
            ),
            help_uri=_HELP_BASE + "fan",
            default_level="error",
        ),
    ]

    results: list[dict] = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        flag = f.get("flag") or ""
        rule_id = _FAN_FLAG_TO_RULE.get(flag)
        if rule_id is None:
            # Empty / local-hub / local-spreader / future flag — skip
            # (LAW 8 closed-enum discipline; W150 audit non-architectural
            # filter).
            continue

        # Resolve the anchor. Symbol mode prefers explicit
        # file_path / line_start fields but falls back to parsing the
        # "path:line" ``location`` string the JSON envelope emits.
        # File mode carries ``path`` (file-level fan output) OR
        # ``file_path`` (registry-evidence shape).
        file_path = f.get("file_path") or f.get("path") or ""
        line = f.get("line_start")
        if not file_path:
            location_str = f.get("location") or ""
            if location_str:
                file_path, parsed_line = _parse_loc_string(location_str)
                if line is None:
                    line = parsed_line
        if not file_path:
            # Without an anchor we cannot surface the finding
            # meaningfully — skip rather than emit an anchorless row.
            continue
        if isinstance(line, int) and line <= 0:
            line = None

        symbol_name = f.get("symbol_name") or f.get("name") or ""
        fan_in = f.get("fan_in")
        fan_out = f.get("fan_out")

        # Message body — surface the flag, identity, and the raw
        # fan_in / fan_out numbers so SARIF consumers can triage
        # without a JSON-envelope round-trip. Order: flag -> identity
        # -> metrics.
        parts = [f"[{flag}]"]
        if symbol_name:
            parts.append(symbol_name)
        else:
            parts.append(file_path)
        parts.append(f"(fan_in={fan_in}, fan_out={fan_out})")
        message_text = " ".join(parts)

        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=flag,
                locations=[_location(file_path, line)],
                message=message_text,
                level_mapper=_fan_flag_level,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Duplicates (semantic-duplicate function detection) ───────────────


def _duplicates_cluster_level(similarity: float) -> str:
    """Map a duplicate-cluster similarity score to a SARIF level (closed enum).

    Mirrors :func:`_clones_pair_level` — semantic duplicates are
    refactor opportunities, not defects. Near-identical clusters
    (>= 0.95 avg similarity) surface as ``warning`` so a CI gate keyed
    off SARIF ``level: warning`` flags them for review; lower-similarity
    clusters (structural-pattern match without near-identical bodies)
    drop to ``note``.

    Cluster severity is NEVER escalated to ``error``: even a
    100%-similarity duplicate cluster is an *opportunity to refactor*,
    not a defect that should block CI. The cmd_duplicates command emits
    a refactoring ``suggestion`` per cluster — agents and reviewers can
    triage on the suggestion text, not on the SARIF level alone.

    Unknown / sub-threshold scores default to ``note`` (LAW 6 —
    neutrality on unfamiliar input).
    """
    sim = float(similarity or 0.0)
    if sim >= 0.95:
        return "warning"
    return "note"


# Limit the number of SECONDARY member locations attached to a single
# duplicates/cluster result. A duplicate cluster can span many similar
# functions (the v12 dogfood surfaced parametrize-heavy clusters with
# 10+ members); embedding all of them inline would inflate the SARIF
# document beyond what GitHub Code Scanning can render. The first
# member is the PRIMARY anchor; the next up to
# ``_DUPLICATES_MAX_SECONDARY_LOCS`` are SECONDARY locations.
# Mirrors :data:`_CLONES_MAX_SECONDARY_LOCS` (W1172).
_DUPLICATES_MAX_SECONDARY_LOCS = 10


def duplicates_to_sarif(data: dict) -> dict:
    """Convert ``roam duplicates`` semantic-duplicate output to SARIF.

    *data* is the JSON envelope built by
    :mod:`roam.commands.cmd_duplicates` (the ``duplicates`` command's
    ``json_envelope`` output). One finding family projects onto SARIF
    on a single closed-enum rule id:

    - ``duplicates/cluster`` (defaultLevel ``note``): one result per
      cluster under ``clusters[]`` (2+ semantically similar functions
      grouped by union-find on the weighted similarity metric defined
      in :func:`cmd_duplicates._compute_similarity`). Severity scales
      with the cluster's ``similarity`` score (avg pairwise similarity
      within the cluster) via :func:`_duplicates_cluster_level`
      (>= 0.95 -> ``warning``; lower bands -> ``note``). Multi-member
      anchor: PRIMARY = the first member's ``file``:``line``; up to
      ``_DUPLICATES_MAX_SECONDARY_LOCS`` additional members attach as
      SECONDARY locations so a SARIF consumer can highlight the full
      cluster footprint without inflating the document.

    Where ``clones`` compares AST subtree hashes (Type-2 textual
    clones, emitted under ``clones/pair`` + ``clones/cluster``),
    ``duplicates`` clusters functions by *weighted similarity of
    AST-derived metrics* read from the index (``symbol_metrics`` +
    ``math_signals`` + ``graph_metrics``). The two detectors emit on
    distinct rule prefixes so SARIF consumers can tell their findings
    apart and tune filters per detector family.

    Cluster severity NEVER escalates to ``error``: duplicates are
    refactor opportunities, not defects (mirrors the
    :func:`clones_to_sarif` design). The ``role_bucket`` field
    (production / test_intentional / mixed — W165) surfaces in result
    messages so SARIF consumers can correlate findings to the bucket
    classification without re-querying the registry.

    Findings missing a PRIMARY anchor (no member with a ``file``) are
    skipped silently — mirrors the ``clones_to_sarif`` pair-without-
    file_a discipline. Empty / no-duplicate envelopes produce a valid
    SARIF document with zero results (rules catalogue is always
    emitted so consumers can introspect the rule vocabulary even on a
    clean run).
    """
    rules = [
        _rule_entry(
            id="duplicates/cluster",
            short_desc=("Cluster of 2+ semantically similar functions (metric-weighted duplicate)"),
            help_uri=_HELP_BASE + "duplicates",
            default_level="note",
        ),
    ]

    results: list[dict] = []

    clusters = data.get("clusters") or []
    if not isinstance(clusters, list):
        clusters = []

    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue

        size = cluster.get("size", 0) or 0
        similarity = float(cluster.get("similarity", 0.0) or 0.0)
        pattern = cluster.get("pattern", "") or ""
        suggestion = cluster.get("suggestion", "") or ""
        role_bucket = cluster.get("role_bucket", "") or ""

        functions = cluster.get("functions") or []
        if not isinstance(functions, list):
            functions = []

        # Build locations: PRIMARY = first function; SECONDARY = up to
        # ``_DUPLICATES_MAX_SECONDARY_LOCS`` additional members. The
        # cmd_duplicates JSON envelope serialises members under
        # ``functions[]`` with ``file`` + ``line`` fields (see
        # cmd_duplicates.py:903-915).
        locations: list[dict] = []
        anchor_name = ""
        for fn in functions[: _DUPLICATES_MAX_SECONDARY_LOCS + 1]:
            if not isinstance(fn, dict):
                continue
            fpath = fn.get("file") or ""
            line = fn.get("line")
            if not fpath:
                continue
            if not anchor_name:
                anchor_name = fn.get("name") or ""
            locations.append(_location(fpath, line))

        if not locations:
            # Without a PRIMARY anchor we cannot surface the cluster
            # meaningfully — skip rather than emit an anchorless result
            # (matches the ``clones_to_sarif`` pair-without-file_a
            # discipline).
            continue

        # Message body — surface the cluster size, similarity, role
        # bucket, anchor symbol, pattern hint, and refactor suggestion
        # so SARIF consumers can triage without a JSON-envelope
        # round-trip.
        bucket_suffix = f" [{role_bucket}]" if role_bucket else ""
        anchor_suffix = f" anchored at {anchor_name}" if anchor_name else ""
        pattern_suffix = f" — {pattern}" if pattern else ""

        message = (
            f"Duplicate cluster{bucket_suffix}: "
            f"{size} functions at {round(similarity * 100)}% "
            f"avg similarity{anchor_suffix}{pattern_suffix}"
        )
        if suggestion:
            message = f"{message}. Suggestion: {suggestion}"

        results.append(
            _result_entry(
                rule_id="duplicates/cluster",
                severity=similarity,
                locations=locations,
                message=message,
                level_mapper=_duplicates_cluster_level,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Dark-matter (hidden co-change couplings) ─────────────────────────


def _dark_matter_confidence_level(confidence: str) -> str:
    """Map a dark-matter finding's confidence tier to a SARIF level.

    cmd_dark_matter classifies each pair by hypothesis category (see
    :func:`roam.commands.cmd_dark_matter._dark_matter_confidence_for_category`):

    - typed hypothesis (``SHARED_DB`` / ``EVENT_BUS`` / ``SHARED_CONFIG`` /
      ``SHARED_API`` / ``TEXT_SIMILARITY`` / ``COPY_PASTE`` / ``NAMING``) ->
      ``structural`` -> SARIF ``warning`` (the engine resolved a concrete
      cause beyond raw NPMI correlation).
    - ``UNKNOWN`` (engine ran but matched no pattern) -> ``heuristic`` ->
      SARIF ``note`` (pure statistical correlation; higher false-positive
      risk).

    Unknown labels default to ``"note"`` (LAW 6 — neutrality on
    unfamiliar input). Dark-matter findings NEVER escalate to ``error``:
    a hidden coupling is a refactor / observability signal, not a defect
    that should block CI on its own.
    """
    label = (confidence or "").lower()
    if label == "structural":
        return "warning"
    # heuristic or unknown -> "note"
    return "note"


def dark_matter_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam dark-matter`` hidden-coupling output to SARIF.

    *findings* is the list of pair dicts cmd_dark_matter builds for its
    JSON envelope (``dark_matter_pairs[]``). Each pair carries
    ``file_a`` / ``file_b`` (canonicalised lexicographically by
    :func:`cmd_dark_matter._canonical_pair` so ``(A, B)`` and ``(B, A)``
    describe the same coupling under the same id), ``npmi``, ``lift``,
    ``strength``, ``cochange_count``, and an optional ``hypothesis``
    dict with a ``category`` / ``detail`` / ``confidence`` triple.

    A single closed-enum rule projects onto SARIF:

    - ``dark-matter/hidden-coupling`` (defaultLevel ``note``): one
      result per detected pair. Per-pair severity is mapped from the
      W154 confidence tier via :func:`_dark_matter_confidence_level`:
      typed hypotheses (``SHARED_DB`` / ``EVENT_BUS`` / ``SHARED_CONFIG``
      / ``SHARED_API`` / ``TEXT_SIMILARITY`` / ``COPY_PASTE`` /
      ``NAMING``) -> ``structural`` -> ``warning``; ``UNKNOWN`` /
      missing -> ``heuristic`` -> ``note``.

    Per-pair anchor: two-sided. PRIMARY = ``file_a`` (the
    lexicographically-lower path); SECONDARY = ``file_b`` (the
    lexicographically-higher path). No line number — dark-matter is a
    file-pair-level signal (no specific symbol or call site). The
    PRIMARY/SECONDARY ordering matches the canonical pair ordering
    enforced by :func:`cmd_dark_matter._canonical_pair`, so a SARIF
    consumer's identity for a finding is stable across emissions
    regardless of which order the engine surfaced the pair.

    The message body surfaces NPMI / lift / co-change count and the
    hypothesis category + detail (when the engine resolved one) so SARIF
    consumers can triage without a JSON-envelope round-trip.

    Pairs missing ``file_a`` are skipped silently — without a PRIMARY
    anchor SARIF consumers cannot surface the row meaningfully (mirrors
    the ``clones_to_sarif`` pair-without-file_a discipline). Empty /
    no-coupling envelopes produce a valid SARIF document with zero
    results (rules catalogue is always emitted so consumers can
    introspect the rule vocabulary even on a clean run).
    """
    rules = [
        _rule_entry(
            id="dark-matter/hidden-coupling",
            short_desc=(
                "Hidden coupling: file pair co-changes frequently but "
                "has no structural dependency (shared DB / event bus / "
                "config / API / copy-paste / naming)"
            ),
            help_uri=_HELP_BASE + "dark-matter",
            default_level="note",
        ),
    ]

    # Canonical-category vocabulary mirrors cmd_dark_matter's
    # ``_DARK_MATTER_TYPED_CATEGORIES`` — used only to resolve the
    # confidence tier when a producer skipped the explicit classification
    # step (`--persist` always classifies, but text-mode fixtures may
    # omit the hypothesis dict).
    _TYPED_CATEGORIES = {
        "SHARED_DB",
        "EVENT_BUS",
        "SHARED_CONFIG",
        "SHARED_API",
        "TEXT_SIMILARITY",
        "COPY_PASTE",
        "NAMING",
    }

    results: list[dict] = []
    for p in findings or []:
        if not isinstance(p, dict):
            continue
        file_a = p.get("file_a") or p.get("path_a") or ""
        file_b = p.get("file_b") or p.get("path_b") or ""
        if not file_a:
            # Without a PRIMARY anchor we cannot surface the pair
            # meaningfully — skip rather than emit an anchorless result.
            continue

        # PRIMARY = file_a; SECONDARY = file_b (when present). No line —
        # dark-matter is a file-pair-level signal.
        locations: list[dict] = [_location(file_a)]
        if file_b:
            locations.append(_location(file_b))

        npmi = p.get("npmi", 0) or 0
        lift = p.get("lift", 0) or 0
        cochange_count = p.get("cochange_count", 0) or 0
        hyp = p.get("hypothesis") if isinstance(p.get("hypothesis"), dict) else {}
        category = (hyp.get("category") if hyp else None) or "UNKNOWN"
        detail = (hyp.get("detail") if hyp else "") or ""

        # Resolve confidence tier: typed category -> structural,
        # else heuristic. Mirrors cmd_dark_matter's
        # ``_dark_matter_confidence_for_category`` so the SARIF level
        # tracks the registry's confidence tier on each pair.
        if category in _TYPED_CATEGORIES:
            confidence = "structural"
        else:
            confidence = "heuristic"

        # Message body — surface NPMI / lift / co-change count and the
        # hypothesis (when resolved) so consumers can triage without a
        # JSON-envelope round-trip. Floats use 2-decimal precision to
        # match the text-mode output.
        try:
            npmi_str = f"{float(npmi):.2f}"
        except (TypeError, ValueError):
            npmi_str = str(npmi)
        try:
            lift_str = f"{float(lift):.1f}"
        except (TypeError, ValueError):
            lift_str = str(lift)

        message = (
            f"Dark-matter coupling: {file_a} <-> {file_b} "
            f"(NPMI {npmi_str}, lift {lift_str}, "
            f"co-changes {cochange_count})"
        )
        if category and category != "UNKNOWN":
            hyp_suffix = f" — Hypothesis: {category}"
            if detail:
                hyp_suffix += f" ({detail})"
            message += hyp_suffix

        results.append(
            _result_entry(
                rule_id="dark-matter/hidden-coupling",
                severity=confidence,
                locations=locations,
                message=message,
                level_mapper=_dark_matter_confidence_level,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Hotspots (runtime vs static rank-discrepancy detector — W1210) ───


def _hotspots_classification_level(classification: str) -> str:
    """Map a hotspot classification to a SARIF level (closed enum).

    cmd_hotspots emits three classifications via
    :func:`roam.runtime.hotspots.compute_hotspots` — each row carries the
    ``runtime`` confidence tier in the findings registry because all
    three require ingested ``runtime_stats`` rows. The SARIF projection
    splits them onto three distinct levels so a CI gate keyed off SARIF
    ``level: error`` only blocks on the band that has the strongest
    operator signal (CONFIRMED — both rankings agree the symbol is hot):

        CONFIRMED -> "error"   (static + runtime agree on importance —
                                a real, currently-hot symbol; review
                                pressure justified)
        UPGRADE   -> "warning" (runtime-critical but statically safe —
                                hidden hotspot static analysis missed)
        DOWNGRADE -> "note"    (statically risky but low traffic — was
                                hot, no longer; informational)

    Unknown / mis-cased labels default to ``"note"`` (LAW 6 —
    neutrality on unfamiliar input) so an unrecognised classification
    never accidentally trips a CI gate keyed off ``level: error``.
    """
    label = (classification or "").upper()
    if label == "CONFIRMED":
        return "error"
    if label == "UPGRADE":
        return "warning"
    # DOWNGRADE / unknown -> "note"
    return "note"


def hotspots_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam hotspots`` runtime-vs-static rank-discrepancy findings to SARIF.

    cmd_hotspots compares the static importance ranking (PageRank +
    complexity + churn) against the runtime ranking (call_count + p99
    latency + error rate) ingested via ``roam ingest-trace`` and tags
    each symbol with one of three classifications. The detector persists
    findings into the central registry under detector ``hotspots`` with
    confidence tier ``runtime`` — all three classifications require
    ingested ``runtime_stats`` rows; the detector cannot produce
    findings without real trace data. The SARIF projection mirrors that
    closed enumeration onto three rule ids with distinct
    ``defaultLevel`` so a CI gate keyed off SARIF ``level: error`` only
    blocks on the band that has the strongest operator signal:

    - ``hotspots/confirmed`` (defaultLevel ``error``): static + runtime
      agree on importance — confirmed via real trace data. Confidence
      tier ``runtime`` — both rankings observed in production.
    - ``hotspots/upgrade`` (defaultLevel ``warning``): runtime-critical
      but statically safe — hidden hotspot static analysis missed.
      Confidence tier ``runtime`` — the runtime traffic IS the evidence.
    - ``hotspots/downgrade`` (defaultLevel ``note``): statically risky
      but low traffic — was hot, no longer. Confidence tier ``runtime``
      — informational by design (the absence of traffic is itself a
      runtime observation).

    Per-finding anchor: the **file path** the symbol lives in (no line
    number — :func:`roam.runtime.hotspots.compute_hotspots` returns
    ``symbol_name`` + ``file_path`` but not a line, so the symbol is
    located at file granularity). SARIF supports file-level
    ``artifactLocation.uri`` entries with no ``region`` key;
    :func:`_physical_location` already drops the region when no line is
    supplied. Mirrors the file-level anchor discipline from
    :func:`bus_factor_to_sarif` (directory-level there).

    Input shape: callers pass the list returned by
    :func:`roam.runtime.hotspots.compute_hotspots` — each entry carries
    ``symbol_id``, ``symbol_name``, ``file_path``, ``classification``,
    ``static_rank``, ``runtime_rank``, and the nested ``runtime_stats``
    / ``static_stats`` blocks. Entries without an indexed ``symbol_id``
    (trace span didn't resolve to a known symbol) are skipped — there's
    no stable subject to attach the finding to, mirroring the
    ``_emit_hotspots_findings`` discipline at the producer side.

    Empty / no-trace-data envelopes produce a valid SARIF document with
    zero results (rules catalogue is always emitted so consumers can
    introspect the rule vocabulary even on a run without ingested
    traces).
    """
    rules = [
        _rule_entry(
            id="hotspots/confirmed",
            short_desc=(
                "Runtime hotspot confirmed by both static and runtime ranking — symbol is genuinely hot in production"
            ),
            help_uri=_HELP_BASE + "hotspots",
            default_level="error",
        ),
        _rule_entry(
            id="hotspots/upgrade",
            short_desc=(
                "Runtime-critical symbol that static analysis missed — hidden hotspot ranked high by runtime traffic"
            ),
            help_uri=_HELP_BASE + "hotspots",
            default_level="warning",
        ),
        _rule_entry(
            id="hotspots/downgrade",
            short_desc=(
                "Statically risky symbol with low runtime traffic — was "
                "hot historically, no longer load-bearing in production"
            ),
            help_uri=_HELP_BASE + "hotspots",
            default_level="note",
        ),
    ]

    results: list[dict] = []

    for h in findings or []:
        if not isinstance(h, dict):
            continue

        symbol_id = h.get("symbol_id")
        if symbol_id is None:
            # Trace span didn't resolve to an indexed symbol — there's
            # no stable subject to attach to. Skip rather than emit a
            # subject-less SARIF row, matching the producer-side
            # discipline in ``_emit_hotspots_findings``.
            continue

        classification = (h.get("classification") or "").upper()
        if classification not in ("CONFIRMED", "UPGRADE", "DOWNGRADE"):
            # Unknown classification — skip rather than synthesise a
            # bucket the consumer can't reason about (closed-enum
            # discipline per CLAUDE.md Constraint 8).
            continue

        rule_id = f"hotspots/{classification.lower()}"
        file_path = h.get("file_path") or ""
        if not file_path:
            # Without a file anchor we cannot surface the row
            # meaningfully — skip rather than emit an anchorless
            # result (matches the Pattern 1 / LAW 6 disclosure rules).
            continue

        symbol_name = h.get("symbol_name") or "<unknown>"
        runtime_rank = int(h.get("runtime_rank") or 0)
        static_rank = int(h.get("static_rank") or 0)
        runtime_stats = h.get("runtime_stats") or {}
        call_count = runtime_stats.get("call_count") or 0
        p99 = runtime_stats.get("p99_latency_ms")
        error_rate = runtime_stats.get("error_rate") or 0.0

        # Message body — surface the classification, symbol name,
        # static/runtime ranks, and the core runtime signals so SARIF
        # consumers can triage without a JSON-envelope round-trip.
        p99_suffix = f", p99={p99:.0f}ms" if p99 is not None else ""
        err_suffix = f", err={error_rate * 100:.1f}%" if error_rate and error_rate > 0 else ""
        message = (
            f"Runtime hotspot ({classification}): {symbol_name} — "
            f"runtime_rank={runtime_rank}, static_rank={static_rank}, "
            f"calls={call_count}{p99_suffix}{err_suffix}"
        )

        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=classification,
                locations=[_location(file_path, None)],
                message=message,
                level_mapper=_hotspots_classification_level,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Flag-dead (potentially-stale feature flag detector — W1226) ───────


def _flag_dead_staleness_level(staleness: str) -> str:
    """Map a flag-dead staleness classification to a SARIF level (closed enum).

    cmd_flag_dead emits four staleness states via :func:`analyze_flags`
    (closed enum: ``stale`` / ``likely_stale`` / ``suspect`` / ``ok``).
    Only the first three project onto SARIF — ``ok`` rows have no
    staleness indicators and are not actionable, so they are filtered
    upstream before reaching this mapper.

        stale         -> "warning" (known-stale: listed in --config or
                                    confirmed via dashboard cross-check)
        likely_stale  -> "note"    (count == 1: single-reference flag,
                                    advisory band — likely leftover but
                                    not guaranteed dead)
        suspect       -> "warning" (constant default or all references
                                    in a single file — review pressure
                                    justified but no hard verdict)

    Unknown / mis-cased labels default to ``"note"`` (LAW 6 —
    neutrality on unfamiliar input) so an unrecognised classification
    never accidentally trips a CI gate keyed off ``level: error``.
    Flag-dead deliberately does NOT escalate to ``error``: the detector
    is heuristic (regex-based call-site scan + name-pattern matching,
    no dashboard cross-check) so even the strongest signal stays in
    the warning band — mirrors the W1213 ``duplicates`` severity
    ceiling.
    """
    label = (staleness or "").lower()
    if label == "stale":
        return "warning"
    if label == "suspect":
        return "warning"
    # likely_stale / ok / unknown -> "note"
    return "note"


# Limit the number of SECONDARY call-site locations attached to a
# single flag-dead result. A widely-used feature flag can fire dozens
# of call sites (a beta toggle gating multiple controllers); embedding
# all of them inline would inflate the SARIF document beyond what
# GitHub Code Scanning can render. The first site is the PRIMARY
# anchor; the next up to ``_FLAG_DEAD_MAX_SECONDARY_LOCS`` are
# SECONDARY. Mirrors :data:`_CLONES_MAX_SECONDARY_LOCS` (W1172) and
# :data:`_DUPLICATES_MAX_SECONDARY_LOCS` (W1213).
_FLAG_DEAD_MAX_SECONDARY_LOCS = 10


def flag_dead_to_sarif(
    findings: list[dict],
    *,
    emit_runtime_notifications: bool = False,
    warnings_out: list[str] | None = None,
) -> dict:
    """Convert ``roam flag-dead`` per-flag staleness findings to SARIF.

    cmd_flag_dead scans source files for feature flag API calls
    (LaunchDarkly / Unleash / Split / generic / env-var patterns) and
    groups call sites by flag name. Each per-flag summary carries a
    ``staleness`` classification (closed enum: ``stale`` /
    ``likely_stale`` / ``suspect`` / ``ok``), a ``reasons`` list naming
    the indicators that drove the classification, and a ``locations``
    list of file/line anchor pairs (one per call site).

    *emit_runtime_notifications* / *warnings_out* (W1113): producer-side
    advisory warnings (Pattern 1B / Pattern 2 silent-fallback
    disclosures from ``cmd_flag_dead``'s ``_known_stale_warnings``
    accumulator — known-stale config file unreadable, decode failure,
    etc.). When ``emit_runtime_notifications=True`` AND ``warnings_out``
    is non-empty, the warnings are projected onto the SARIF
    ``run.invocations[].toolExecutionNotifications[]`` array via
    :func:`to_sarif`'s W1046 opt-in. Hash invariant: when both kwargs
    are at their defaults (``False`` / ``None``), the SARIF output is
    byte-identical to pre-W1113 because :func:`to_sarif` only adds the
    ``invocations`` key when ``emit_runtime_notifications=True``.
    Mirrors the W1060 ``complexity_to_sarif`` plumbing.

    Three closed-enum rule ids project the three actionable
    classifications:

    - ``flag-staleness`` (defaultLevel ``warning``): flag listed in
      ``--config`` known-stale file — operator has already confirmed
      the flag should be removed.
    - ``flag-single-reference`` (defaultLevel ``note``): flag has a
      single call site — likely leftover code, advisory band only.
    - ``flag-suspect`` (defaultLevel ``warning``): flag is suspect —
      called with the same constant default at every site OR all
      references concentrate in a single file. Both ``suspect`` sub-
      causes share this rule id (named after the envelope's 4-value
      ``staleness`` vocabulary: ``stale`` / ``likely_stale`` /
      ``suspect`` / ``ok``); the message body surfaces the precise
      reason from the producer's ``reasons[]`` list so SARIF consumers
      can triage without a JSON-envelope round-trip.

    Per-finding level is derived from the flag's ``staleness`` via
    :func:`_flag_dead_staleness_level` (stale + suspect -> warning;
    likely_stale -> note). Flag-dead deliberately does NOT escalate
    to ``error``: the detector is heuristic (regex-based call-site
    scan + name-pattern matching, no dashboard cross-check) so even
    the strongest signal stays in the warning band — mirrors the
    W1213 ``duplicates`` severity ceiling.

    Per-flag anchor: PRIMARY = first location's file + line; if the
    flag has additional call sites they surface as SECONDARY locations
    (up to ``_FLAG_DEAD_MAX_SECONDARY_LOCS`` entries) so consumers can
    highlight the full call-site footprint without inflating the SARIF
    document. Mirrors the W1172 ``clones_to_sarif`` /
    W1213 ``duplicates_to_sarif`` SECONDARY-location discipline.

    Message body: includes the flag name (LAW 4 concrete-noun anchor),
    classification, provider, reference count, and the joined
    ``reasons`` list so SARIF consumers can triage without a JSON-
    envelope round-trip.

    Findings without a ``flag_name``, with a non-actionable
    classification (``staleness == "ok"``), or without any usable
    location anchor are skipped silently — without a stable subject
    SARIF consumers cannot surface the row meaningfully (matches the
    Pattern 1 / LAW 6 disclosure rules). Unknown classifications
    outside the closed enumeration also drop (LAW 8 closed-enum
    discipline per CLAUDE.md Constraint 8).

    Empty / no-flags envelopes produce a valid SARIF document with
    zero results — the rule catalogue is always emitted (closed enum
    of 3 rules) so consumers can introspect the rule vocabulary even
    on a clean run without any flag activity.
    """
    rules = [
        _rule_entry(
            id="flag-staleness",
            short_desc=(
                "Known-stale feature flag: listed in --config known-stale file (operator-confirmed for removal)"
            ),
            help_uri=_HELP_BASE + "flag-dead",
            default_level="warning",
        ),
        _rule_entry(
            id="flag-single-reference",
            short_desc=("Feature flag with a single call site: likely leftover code, advisory review band"),
            help_uri=_HELP_BASE + "flag-dead",
            default_level="note",
        ),
        _rule_entry(
            id="flag-suspect",
            short_desc=(
                "Suspect feature flag: same constant default at every "
                "call site OR all references concentrate in a single file"
            ),
            help_uri=_HELP_BASE + "flag-dead",
            default_level="warning",
        ),
    ]

    # Closed-enum staleness -> rule id mapping. Mirrors the producer-
    # side ``analyze_flags`` vocabulary in cmd_flag_dead.py. The ``ok``
    # bucket has no staleness indicators and is not actionable, so it
    # is deliberately not in this map (rows with ``staleness == "ok"``
    # are filtered out upstream of the per-result loop).
    _STALENESS_TO_RULE = {
        "stale": "flag-staleness",
        "likely_stale": "flag-single-reference",
        "suspect": "flag-suspect",
    }

    results: list[dict] = []

    for f in findings or []:
        if not isinstance(f, dict):
            continue

        flag_name = f.get("flag_name") or ""
        if not flag_name:
            # Without a flag-name subject we cannot surface the finding
            # meaningfully — skip rather than emit a subject-less row
            # (Pattern 1 / LAW 6 disclosure discipline).
            continue

        staleness = (f.get("staleness") or "").lower()
        rule_id = _STALENESS_TO_RULE.get(staleness)
        if rule_id is None:
            # ``ok`` / unknown classification — drop. ``ok`` has no
            # staleness indicators (not actionable); unknown labels
            # drop per closed-enum discipline (LAW 8 / CLAUDE.md
            # Constraint 8).
            continue

        locations_raw = f.get("locations") or []
        if not isinstance(locations_raw, list):
            locations_raw = []

        # Build the SARIF locations list. PRIMARY = first call site
        # (file + line); SECONDARY = up to
        # ``_FLAG_DEAD_MAX_SECONDARY_LOCS`` additional sites. Mirrors
        # the W1172 / W1213 SECONDARY-location discipline.
        sarif_locations: list[dict] = []
        for loc in locations_raw[: _FLAG_DEAD_MAX_SECONDARY_LOCS + 1]:
            if not isinstance(loc, dict):
                continue
            loc_file = loc.get("file") or ""
            loc_line = loc.get("line")
            if not loc_file:
                continue
            if isinstance(loc_line, int) and loc_line <= 0:
                loc_line = None
            sarif_locations.append(_location(loc_file, loc_line))

        if not sarif_locations:
            # Without any anchor we cannot surface the finding
            # meaningfully — skip rather than emit an anchorless row.
            continue

        provider = f.get("provider") or "unknown"
        count = f.get("count") or 0
        reasons_raw = f.get("reasons") or []
        reasons_str = "; ".join(str(r) for r in reasons_raw if r) if reasons_raw else ""

        # Message body — flag name first (LAW 4 concrete-noun anchor),
        # then classification, provider, count, and the joined reasons.
        # The reasons text is what distinguishes the two ``suspect``
        # sub-causes (constant default vs all-in-single-file) under the
        # shared ``flag-suspect`` rule id.
        message_parts = [
            f"Feature flag '{flag_name}' ({staleness}):",
            f"provider={provider},",
            f"refs={count}",
        ]
        message = " ".join(message_parts)
        if reasons_str:
            message += f" — {reasons_str}"

        results.append(
            _result_entry(
                rule_id=rule_id,
                severity=staleness,
                locations=sarif_locations,
                message=message,
                level_mapper=_flag_dead_staleness_level,
            )
        )

    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        rules,
        results,
        emit_runtime_notifications=emit_runtime_notifications,
        warnings_out=warnings_out,
    )


# ── Orphan-routes (dead Laravel API endpoint detector — W1227) ───────


def _orphan_routes_confidence_level(confidence: str) -> str:
    """Map an orphan-route confidence band to a SARIF level (closed enum).

    cmd_orphan_routes emits three actionable confidence bands via
    :func:`_determine_confidence` (closed enum: ``high`` / ``medium`` /
    ``low``). The ``used`` bucket has a frontend consumer and is filtered
    upstream — SARIF consumers never see those rows.

        high   -> "warning" (no references anywhere outside route file /
                             controller — strongest dead-endpoint signal)
        medium -> "warning" (referenced only in backend tests / seeders —
                             still no frontend consumer, dead from the
                             API surface perspective)
        low    -> "note"    (referenced only in docs / comments —
                             advisory band only, the doc may still be
                             load-bearing for downstream consumers)

    Unknown / mis-cased labels default to ``"note"`` (LAW 6 —
    neutrality on unfamiliar input) so an unrecognised classification
    never accidentally trips a CI gate keyed off ``level: error``.
    Orphan-routes deliberately does NOT escalate to ``error``: the
    detector is heuristic (path-segment grep + Laravel-route regex
    parse, no full PHP AST analysis) so even the strongest signal stays
    in the warning band — mirrors the W1226 ``flag-dead`` and W1213
    ``duplicates`` severity ceilings.
    """
    label = (confidence or "").lower()
    if label in ("high", "medium"):
        return "warning"
    # low / used / unknown -> "note"
    return "note"


def orphan_routes_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam orphan-routes`` dead-endpoint findings to SARIF.

    cmd_orphan_routes parses Laravel route files (``routes/api.php`` and
    ``routes/web.php``), extracts route definitions (single-action +
    apiResource / resource), and searches the codebase for references to
    each route's path segments. Each orphan finding carries a
    ``confidence`` classification (closed enum: ``high`` / ``medium`` /
    ``low`` — the ``used`` bucket is filtered upstream), the HTTP method,
    path, optional controller / action, and the file:line of the route
    definition.

    Single closed-enum rule projects the three confidence bands:

    - ``orphan-route`` (defaultLevel ``warning``): a Laravel API route
      with no frontend consumer detected. Dead endpoints are real bugs
      (operational cost + attack surface), not just hygiene — the
      defaultLevel is ``warning`` rather than ``note``. Per-result level
      is derived from the finding's ``confidence`` via
      :func:`_orphan_routes_confidence_level` (high + medium -> warning;
      low -> note).

    Per-finding anchor: the route definition's file + line. The message
    body surfaces the HTTP method, path, controller + action (when
    present), and confidence band so SARIF consumers can triage without
    a JSON-envelope round-trip.

    Findings without a ``path`` or ``method`` (the two fields that
    together identify the route) are skipped silently — without a stable
    subject SARIF consumers cannot surface the row meaningfully (matches
    the Pattern 1 / LAW 6 disclosure rules). Findings without a usable
    file anchor (no ``file`` field) are also skipped. Unknown
    confidence labels outside the ``high`` / ``medium`` / ``low`` closed
    enumeration also drop (LAW 8 / CLAUDE.md Constraint 8). Orphan-routes
    deliberately does NOT escalate to ``error``: the detector is
    heuristic (path-segment grep + Laravel-route regex parse, no full
    PHP AST analysis) so even the strongest signal stays in the warning
    band — mirrors the W1226 ``flag-dead`` and W1213 ``duplicates``
    severity ceilings.

    Empty / no-routes envelopes produce a valid SARIF document with
    zero results — the rule catalogue is always emitted (single
    closed-enum rule) so consumers can introspect the rule vocabulary
    even on a clean run without any orphan endpoints.
    """
    rules = [
        _rule_entry(
            id="orphan-route",
            short_desc=(
                "Laravel API route with no frontend consumer detected — "
                "potentially dead endpoint (operational cost + attack "
                "surface)"
            ),
            help_uri=_HELP_BASE + "orphan-routes",
            default_level="warning",
        ),
    ]

    results: list[dict] = []

    for o in findings or []:
        if not isinstance(o, dict):
            continue

        path = o.get("path") or ""
        method = o.get("method") or ""
        if not path or not method:
            # Without the (method, path) subject we cannot surface the
            # finding meaningfully — skip rather than emit a subject-less
            # row (Pattern 1 / LAW 6 disclosure discipline).
            continue

        confidence = (o.get("confidence") or "").lower()
        if confidence not in ("high", "medium", "low"):
            # ``used`` / unknown classification — drop. ``used`` has a
            # frontend consumer (not an orphan); unknown labels drop per
            # closed-enum discipline (LAW 8 / CLAUDE.md Constraint 8).
            continue

        file_path = o.get("file") or ""
        if not file_path:
            # Without a file anchor we cannot surface the row
            # meaningfully — skip rather than emit an anchorless result.
            continue

        line = o.get("line")
        if isinstance(line, int) and line <= 0:
            line = None

        controller = o.get("controller") or ""
        action = o.get("action") or ""

        # Message body — method + path (LAW 4 concrete-noun anchor on
        # the route identifier), confidence band, controller::action
        # when present so consumers can triage without a JSON-envelope
        # round-trip.
        ctrl_suffix = ""
        if controller and action:
            ctrl_suffix = f" — {controller}::{action}"
        elif controller:
            ctrl_suffix = f" — {controller}"
        message = f"Orphan route ({confidence}): {method} {path}{ctrl_suffix}"

        results.append(
            _result_entry(
                rule_id="orphan-route",
                severity=confidence,
                locations=[_location(file_path, line)],
                message=message,
                level_mapper=_orphan_routes_confidence_level,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)


# ── Verify-imports (import hallucination firewall — W1229) ───────────


def _verify_imports_kind_level(kind: str) -> str:
    """Map a verify-imports finding's kind to a SARIF level (closed enum).

    cmd_verify_imports classifies each unresolved import row by whether
    FTS5 surfaced any fuzzy-match suggestion against the indexed symbol
    table (see :func:`roam.commands.cmd_verify_imports._fts_suggestions`).
    The two-rule projection mirrors the producer's signal directly:

        invalid-import       -> "warning" (FTS5 surfaced at least one
                                           candidate — likely a typo /
                                           rename / stale import; a fix
                                           is reachable through the
                                           suggestions list)
        hallucination-import -> "error"   (FTS5 found no candidate at
                                           all — the imported name does
                                           not exist anywhere in the
                                           indexed symbol table; the
                                           strongest "hallucination
                                           firewall" signal for LLM-
                                           generated code and the only
                                           verify-imports rule that
                                           escalates to ``error``)

    Unknown kinds default to ``"note"`` (LAW 6 — neutrality on
    unfamiliar input). Mirrors the closed-enum band design from
    :func:`_orphan_imports_kind_level` (W1218) — both detectors gate
    CI on near-certain hallucinated imports, but verify-imports
    additionally watches non-Python languages and synthetic component
    symbols (Vue / Svelte SFCs).
    """
    label = (kind or "").lower()
    if label == "hallucination-import":
        return "error"
    if label == "invalid-import":
        return "warning"
    return "note"


def verify_imports_to_sarif(findings: list[dict]) -> dict:
    """Convert ``roam verify-imports`` per-import findings to SARIF.

    cmd_verify_imports scans every source file in the index for import /
    require statements and validates each name against the symbol /
    file tables. Each row carries ``file``, ``line``, ``name``, a
    ``status`` (closed enum: ``resolved`` / ``unresolved``), and an
    optional ``suggestions`` list (FTS5 fuzzy matches against the
    indexed symbol table). The SARIF projection ships only the
    ``unresolved`` rows — ``resolved`` carries no actionable signal.

    The two-rule projection splits the unresolved population by whether
    FTS5 surfaced any nearby candidate (the only producer-side signal
    available for distinguishing typo / rename from a name that simply
    isn't in the codebase):

    - ``invalid-import`` (defaultLevel ``warning``): the imported name
      did not resolve, but FTS5 surfaced at least one fuzzy candidate
      — likely a typo, rename, or stale import. The suggestions list
      gives the consumer a remediation path so the finding sits in the
      review band.
    - ``hallucination-import`` (defaultLevel ``error``): the imported
      name did not resolve AND FTS5 found no nearby candidate. The
      symbol genuinely doesn't exist anywhere in the indexed graph —
      the canonical "hallucination firewall" signal for LLM-generated
      code. This is the only verify-imports rule that escalates to
      ``error`` so a CI gate keyed off ``level: error`` blocks only on
      irrecoverable imports.

    Per-finding anchor: ``file`` + ``line`` (the import statement
    site). The message body surfaces the language, the imported name,
    the classification, and the FTS5 suggestions list when present so
    SARIF consumers can triage without a JSON-envelope round-trip.

    Findings missing a stable subject (no ``name`` or no ``file``) or
    carrying a non-actionable classification (``status == "resolved"``
    or any non-``unresolved`` value) are skipped silently — without a
    subject SARIF consumers cannot surface the row meaningfully
    (Pattern 1 / LAW 6 disclosure discipline). Unknown ``status``
    labels outside the closed enumeration also drop (LAW 8 / CLAUDE.md
    Constraint 8).

    Empty / clean envelopes produce a valid SARIF document with zero
    results — the rule catalogue is always emitted (closed enum of 2
    rules) so consumers can introspect the rule vocabulary even on a
    clean run without any unresolved imports.

    Mirrors the closed-enum design from :func:`orphan_imports_to_sarif`
    (W1218) and :func:`flag_dead_to_sarif` (W1226) — three detectors
    that share a "import-shape signal" audience for the same CI-gate
    consumer.
    """
    rules = [
        _rule_entry(
            id="invalid-import",
            short_desc=(
                "Unresolved import with fuzzy-match candidates — likely a "
                "typo, rename, or stale import; suggestions list gives a "
                "remediation path"
            ),
            help_uri=_HELP_BASE + "verify-imports",
            default_level="warning",
        ),
        _rule_entry(
            id="hallucination-import",
            short_desc=(
                "Unresolved import with no fuzzy-match candidates — the "
                "imported name does not exist anywhere in the indexed "
                "symbol table; canonical hallucination-firewall signal "
                "for LLM-generated code"
            ),
            help_uri=_HELP_BASE + "verify-imports",
            default_level="error",
        ),
    ]

    results: list[dict] = []

    for f in findings or []:
        if not isinstance(f, dict):
            continue

        name = f.get("name") or ""
        if not name:
            # Without an imported-name subject we cannot surface the
            # finding meaningfully — skip rather than emit a
            # subject-less row (Pattern 1 / LAW 6 disclosure
            # discipline).
            continue

        status = (f.get("status") or "").lower()
        if status != "unresolved":
            # ``resolved`` carries no actionable signal; unknown
            # ``status`` labels drop per closed-enum discipline
            # (LAW 8 / CLAUDE.md Constraint 8).
            continue

        file_path = f.get("file") or ""
        if not file_path:
            # Without a file anchor we cannot surface the row
            # meaningfully — skip rather than emit an anchorless
            # result.
            continue

        line = f.get("line")
        if isinstance(line, int) and line <= 0:
            line = None

        suggestions_raw = f.get("suggestions") or []
        if not isinstance(suggestions_raw, list):
            suggestions_raw = []
        suggestions: list[str] = [str(s) for s in suggestions_raw if s]

        # Classification: FTS5 found candidates -> invalid-import
        # (typo / rename signal); no candidates -> hallucination-import
        # (the name genuinely isn't in the index).
        kind = "invalid-import" if suggestions else "hallucination-import"

        language = f.get("language") or ""
        # The producer envelope doesn't always stamp ``language`` on
        # the per-import row (cmd_verify_imports keeps it on the file
        # row, not the finding). Surfacing the empty case keeps the
        # message intelligible even when the language column is
        # missing.
        lang_prefix = f"{language}: " if language else ""

        # Message body — language + name (LAW 4 concrete-noun anchor
        # on the imported name), classification rationale, and the
        # joined suggestions list. Order matches the producer-side
        # text output so SARIF consumers and JSON consumers see a
        # parallel triage path.
        rationale = (
            "no nearby symbol in the indexed table — hallucinated import"
            if kind == "hallucination-import"
            else "did-you-mean candidates available in the indexed table"
        )
        message = f"{lang_prefix}{name} ({kind}): {rationale}"
        if suggestions:
            message += f" — suggestions: {', '.join(suggestions)}"

        results.append(
            _result_entry(
                rule_id=kind,
                severity=kind,
                locations=[_location(file_path, line)],
                message=message,
                level_mapper=_verify_imports_kind_level,
            )
        )

    return to_sarif(_TOOL_NAME, _get_version(), rules, results)
