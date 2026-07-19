"""Vulnerability scanning — import, inventory, and reachability analysis."""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output._severity import (
    severity_breakdown as _canonical_severity_breakdown,
)
from roam.output._severity import severity_rank as _canonical_severity_rank
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import format_table, json_envelope, to_json

# W117 — vulns is the fourth detector migrating onto the central findings
# registry (after clones in W95, dead in W99, complexity in W102). The
# shape mirrors those three: a stable detector version stamp and a
# deterministic ``finding_id_str`` so re-runs upsert instead of
# duplicating rows. Bump this when the source-to-confidence map or the
# reachability tier encoding in ``_emit_vuln_findings`` changes
# meaningfully — those values drive what the registry row's
# ``confidence`` and ``evidence_json.reachability`` shape look like.
VULNS_DETECTOR_VERSION: str = "1.0.0"


# W117 — confidence-tier mapping for vulnerability findings.
#
# The choice depends on TWO signals: ingestion source and reachability.
#
# * ``static_analysis``: a curated CVE database (npm-audit / pip-audit /
#   trivy / osv) reported a deterministic match against a dependency in
#   the project's manifest. This is the strongest signal the detector
#   can produce — the scanner did the version-comparison work upstream.
# * ``heuristic``: source == "generic" (raw user-supplied JSON, no
#   curated CVE DB validated this) OR source is unknown. We don't know
#   whether the version match is exact or fuzzy, so treat as heuristic.
#
# Reachability is captured as a separate dimension in ``evidence_json``
# (``reachability: reachable | unreachable | unknown``) rather than
# collapsing into the confidence tier — downstream consumers can
# deprioritise unreachable findings without losing the underlying
# static-analysis grade. This mirrors the wave-117 brief:
# "unreachable-vuln stays static_analysis but flag in evidence_json".
_CURATED_VULN_SOURCES = {
    "npm-audit",
    "npm_audit",
    "pip-audit",
    "pip_audit",
    "trivy",
    "osv",
}


def _vuln_confidence_tier(source: str | None) -> str:
    """Map a vuln ingestion source to a registry confidence tier."""
    from roam.db.findings import CONFIDENCE_HEURISTIC, CONFIDENCE_STATIC_ANALYSIS

    src = (source or "generic").lower()
    if src in _CURATED_VULN_SOURCES:
        return CONFIDENCE_STATIC_ANALYSIS
    return CONFIDENCE_HEURISTIC


def _vuln_reachability_tag(reachable: int | None) -> str:
    """Encode the integer reachability code as a stable evidence tag."""
    if reachable == 1:
        return "reachable"
    if reachable == -1:
        return "unreachable"
    return "unknown"


def _vuln_finding_id(cve_id: str | None, package_name: str | None) -> str:
    """Stable, deterministic finding id for one vulnerability.

    The (cve_id, package_name) pair re-identifies the same vuln across
    runs: cve_id is the canonical CVE/GHSA identifier from the scanner,
    package_name is the affected dependency. When cve_id is absent
    (some generic / user JSON shapes lack it) we fall back to the
    package name alone — re-running with the same input upserts the
    existing row rather than duplicating.
    """
    cve = (cve_id or "").strip() or "no-cve"
    pkg = (package_name or "").strip() or "unknown-pkg"
    raw = f"{cve}:{pkg}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"vulns:cve:{digest}"


def _emit_vuln_findings(
    conn: sqlite3.Connection,
    vuln_rows: list[dict],
    source_version: str = VULNS_DETECTOR_VERSION,
) -> None:
    """Mirror each vulnerability row into the central findings registry.

    ``vuln_rows`` is the list of dicts produced by ``_query_vulns`` (the
    same shape the JSON envelope renders). Each row maps to ONE finding:

    * ``subject_kind`` is ``"symbol"`` when ``matched_symbol_id`` is
      populated (the vuln has been resolved to a concrete call-site in
      this codebase), otherwise ``"package"`` (the vuln lives in a
      dependency we haven't located a call-site for — still actionable,
      but at the manifest level rather than the call-graph level).
    * ``confidence`` is derived from the ingestion source (curated CVE
      DB = ``static_analysis``, generic / unknown = ``heuristic``).
    * Reachability is captured separately in ``evidence_json`` so
      consumers can deprioritise unreachable findings without losing the
      ``static_analysis`` grade.

    Wrapped at the call site in try/except so a pre-W89 DB (no
    ``findings`` table) silently no-ops rather than crashing the
    standard vulns read/import path.
    """
    from roam.db.findings import FindingRecord, emit_finding

    for v in vuln_rows:
        cve_id = v.get("cve_id")
        package_name = v.get("package_name")
        severity = (v.get("severity") or "unknown").lower()
        title = v.get("title") or ""
        source = v.get("source") or "generic"
        matched_symbol_id = v.get("matched_symbol_id")
        matched_file = v.get("matched_file")
        reachable = v.get("reachable", 0)

        finding_id = _vuln_finding_id(cve_id, package_name)
        reachability = _vuln_reachability_tag(reachable)
        # subject_kind: "symbol" when we've resolved a call-site,
        # otherwise "package" (the affected dependency in the manifest).
        # Pick "package" over "dependency" so the term lines up with the
        # ecosystem vocabulary (npm/pip/trivy/osv all call them packages).
        subject_kind = "symbol" if matched_symbol_id is not None else "package"
        subject_id = int(matched_symbol_id) if matched_symbol_id is not None else None

        evidence = {
            "cve_id": cve_id,
            "package_name": package_name,
            "severity": severity,
            "title": title,
            "source": source,
            "matched_symbol_id": matched_symbol_id,
            "matched_file": matched_file,
            "reachability": reachability,
            "reachable_int": reachable,
            "hop_count": v.get("hop_count"),
            "shortest_path": v.get("shortest_path"),
        }
        cve_display = cve_id or "(no CVE)"
        pkg_display = package_name or "(unknown)"
        claim = (
            f"Vulnerability {cve_display} in {pkg_display} "
            f"(severity={severity}, source={source}, reachability={reachability})"
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=_vuln_confidence_tier(source),
                source_detector="vulns",
                source_version=source_version,
            ),
        )


# R22 — confidence-derivation rule for vulnerabilities:
#   Two signals combined — ingestion source (curated CVE DB vs generic
#   user-supplied JSON) and reachability (does an entry point reach
#   the matched symbol?).
#
#   source ∈ {npm-audit, pip-audit} → "high"   (curated, authoritative)
#   source ∈ {trivy, osv}            → "medium" (broad-scanner, some FP)
#   source ∈ {generic, unknown}      → "low"    (raw user JSON; no curation)
#
#   Reachability tightens or loosens the source-based label:
#   - reachable == 1  → keep or upgrade (medium → high)
#   - reachable == -1 → downgrade one level (high → medium, medium → low)
#   - reachable == 0  → no analysis ran; keep source-based label
_SOURCE_CONFIDENCE = {
    "npm-audit": "high",
    "npm_audit": "high",
    "pip-audit": "high",
    "pip_audit": "high",
    "trivy": "medium",
    "osv": "medium",
    "generic": "low",
}
_LEVEL_ORDER = ["low", "medium", "high"]


def _adjust_level(level: str, delta: int) -> str:
    try:
        idx = _LEVEL_ORDER.index(level)
    except ValueError:
        idx = 1  # medium
    new_idx = max(0, min(2, idx + delta))
    return _LEVEL_ORDER[new_idx]


def _vuln_classify(v: dict) -> tuple[str, str]:
    """Map a vuln finding to a (confidence, reason) tuple."""
    source = (v.get("source") or "generic").lower()
    base = _SOURCE_CONFIDENCE.get(source, "low")
    reach = v.get("reachable", 0)
    if reach == 1:
        final = _adjust_level(base, +1)
        reason = f"source={source} + reachable from entry point"
    elif reach == -1:
        final = _adjust_level(base, -1)
        reason = f"source={source} but unreachable from any entry point"
    else:
        final = base
        reason = f"source={source}; reachability not analyzed"
    return final, reason


# ---------------------------------------------------------------------------
# Format auto-detection
# ---------------------------------------------------------------------------

_FORMAT_CHOICES = ("auto", "npm-audit", "pip-audit", "trivy", "osv", "generic")


def _detect_format(data: object) -> str:
    """Auto-detect vulnerability report format from parsed JSON structure.

    Returns one of: npm-audit, pip-audit, trivy, osv, generic.
    Raises ValueError if the format cannot be determined.
    """
    if isinstance(data, list):
        # pip-audit: list of {"name": ..., "vulns": [...]}
        if data and isinstance(data[0], dict):
            if "vulns" in data[0] and "name" in data[0]:
                return "pip-audit"
            # OSV flat list: [{"id": "GHSA-...", "affected": [...]}]
            if "id" in data[0] and ("affected" in data[0] or "aliases" in data[0]):
                return "osv"
            # generic: [{"cve": ..., "package": ...}]
            if "package" in data[0] or "cve" in data[0]:
                return "generic"
        return "generic"

    if isinstance(data, dict):
        # npm audit v2: {"vulnerabilities": {...}}
        if "vulnerabilities" in data and isinstance(data["vulnerabilities"], dict):
            return "npm-audit"
        # npm audit v1: {"advisories": {...}}
        if "advisories" in data and isinstance(data["advisories"], dict):
            return "npm-audit"
        # trivy: {"Results": [...]}
        if "Results" in data:
            return "trivy"
        # osv scanner: {"results": [...]}
        if "results" in data:
            return "osv"
        # pip-audit wrapped: {"dependencies": [...]}
        if "dependencies" in data:
            return "pip-audit"

    raise ValueError(
        "Cannot auto-detect vulnerability report format. "
        "Use --format to specify one of: npm-audit, pip-audit, trivy, osv, generic"
    )


def _ingest_report(
    conn: sqlite3.Connection,
    report_path: str,
    fmt: str,
    project_root: str | Path | None = None,
) -> list[dict]:
    """Ingest a vulnerability report using the appropriate parser.

    Parameters
    ----------
    conn:
        Writable DB connection.
    report_path:
        Path to the JSON report file.
    fmt:
        One of: npm-audit, pip-audit, trivy, osv, generic, auto.
        If 'auto', the format is detected from the JSON structure.

    Returns
    -------
    List of ingested vulnerability dicts.
    """
    from roam.security.vuln_store import (
        ingest_generic,
        ingest_npm_audit,
        ingest_osv,
        ingest_pip_audit,
        ingest_trivy,
    )

    if fmt == "auto":
        raw = _json.loads(Path(report_path).read_text(encoding="utf-8"))
        fmt = _detect_format(raw)

    dispatch = {
        "npm-audit": ingest_npm_audit,
        "pip-audit": ingest_pip_audit,
        "trivy": ingest_trivy,
        "osv": ingest_osv,
        "generic": ingest_generic,
    }

    ingester = dispatch.get(fmt)
    if ingester is None:
        raise ValueError(f"Unknown format: {fmt}")

    return ingester(conn, report_path, project_root=project_root)


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------


# W564: severity ordering now sourced from
# roam.output._severity.severity_rank — canonical, shared across all
# roam commands. The thin ``_severity_rank`` wrapper is kept so the
# 5-tier vuln vocab (critical/high/medium/low/unknown) collapses to a
# single sort key the rest of this module can use unchanged. The
# canonical rank assigns ``unknown`` to -1 (vs. the legacy 0); we
# remap to 0 here so the local floor semantics around ``--severity
# low`` (rank=1) stay byte-identical.
def _severity_rank(sev: str) -> int:
    canonical = _canonical_severity_rank(sev or "unknown")
    return canonical if canonical >= 0 else 0


def _severity_breakdown(vulns: list[dict], key: str = "severity") -> dict[str, int]:
    """Compute a severity breakdown dict from a list of vuln dicts.

    W566 — delegates to the canonical
    :func:`roam.output._severity.severity_breakdown` helper. Vocab is
    the CVSS 5-tier + ``unknown`` (the default), unknown labels route
    to the ``unknown`` bucket, and zero-count buckets are dropped —
    byte-identical to the pre-W566 contract here. Kept as a private
    thin wrapper so the call sites elsewhere in this module stay
    unchanged.
    """
    return _canonical_severity_breakdown(vulns, key=key)


# ---------------------------------------------------------------------------
# SARIF conversion
# ---------------------------------------------------------------------------


def _vulns_to_sarif(
    vulns: list[dict],
    *,
    runtime_overrides: list[dict] | None = None,
    runtime_notification_overrides: list[dict] | None = None,
) -> dict:
    """Convert vulnerability findings to SARIF 2.1.0 format.

    W1061-followup: ``runtime_overrides`` / ``runtime_notification_overrides``
    project onto SARIF ``ruleConfigurationOverrides`` /
    ``notificationConfigurationOverrides`` per OASIS §3.51 / §3.20.4. The
    cmd_vulns ``--reachable-only`` filter is finding-level (operates on
    each finding's ``reachable`` field, not on rule-id granularity), so
    the caller passes it via ``runtime_notification_overrides``. Empty /
    ``None`` keeps SARIF byte-identical to pre-W1061-followup output via
    gated emission in :func:`to_sarif`.
    """
    from roam.output._severity import to_sarif_level
    from roam.output.sarif import (
        _TOOL_NAME,
        _derive_finding_tags,
        _get_version,
        _location,
        _slugify,
        to_sarif,
    )

    seen_rules: dict[str, dict] = {}
    results: list[dict] = []

    for v in vulns:
        cve = v.get("cve_id") or v.get("cve") or "unknown"
        pkg = v.get("package_name") or v.get("package") or "unknown"
        severity = (v.get("severity") or "unknown").lower()
        title = v.get("title") or f"Vulnerability in {pkg}"
        matched_file = v.get("matched_file") or ""

        rule_id = f"vuln/{_slugify(cve)}" if cve != "unknown" else f"vuln/{_slugify(pkg)}"

        # W547: canonical roam->SARIF severity contract lives in
        # roam.output._severity. Single source of truth for every CI gate.
        sarif_level = to_sarif_level(severity)

        # W1062: derive normalised dashboard tags so GitHub Code
        # Scanning / SonarQube / security-dashboard tools can filter
        # vulnerabilities by family / CVE / severity. ``extra`` carries
        # the CVE id (when present) so dashboards keyed on the CVE
        # string still work without parsing the rule_id.
        extra: list[str] = []
        if cve.startswith("CVE-"):
            extra.append(cve)
        if v.get("reachable") == 1:
            extra.append("reachable")
        elif v.get("reachable") == -1:
            extra.append("not-reachable")
        tags = _derive_finding_tags(
            severity=severity,
            family="vuln",
            extra=extra,
        )

        if rule_id not in seen_rules:
            seen_rules[rule_id] = {
                "id": rule_id,
                "shortDescription": f"Vulnerability: {title}",
                "helpUri": f"https://nvd.nist.gov/vuln/detail/{cve}" if cve.startswith("CVE-") else "",
                "defaultLevel": sarif_level,
                "properties": {"tags": list(tags)},
            }

        locations = []
        if matched_file:
            locations.append(_location(matched_file, None))

        msg_parts = [f"{cve}: {title}"]
        if pkg != "unknown":
            msg_parts.append(f"Package: {pkg}")
        if v.get("reachable") == 1:
            msg_parts.append("REACHABLE from entry points")
        elif v.get("reachable") == -1:
            msg_parts.append("NOT reachable from entry points")

        results.append(
            {
                "ruleId": rule_id,
                "level": sarif_level,
                "message": {"text": " | ".join(msg_parts)},
                "locations": locations,
                "properties": {"tags": list(tags)},
            }
        )

    # W1061-followup: forward runtime overrides. Each branch gated on
    # non-empty list so default path stays byte-identical to
    # pre-W1061-followup. The cmd_vulns ``--reachable-only`` filter
    # populates ``runtime_notification_overrides`` because it operates at
    # finding-evaluation time, not rule-id dispatch time.
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


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="vulns",
    category="reports",
    summary="Scan and manage vulnerability inventory",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "compliance"),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("vulns")
@click.option(
    "--import-file",
    "import_file",
    default=None,
    type=click.Path(exists=True),
    help="Import a vulnerability report (auto-detects format).",
)
@click.option(
    "--format",
    "fmt",
    default="auto",
    type=click.Choice(_FORMAT_CHOICES, case_sensitive=False),
    help="Report format (default: auto-detect).",
)
@click.option(
    "--reachable-only",
    is_flag=True,
    default=False,
    help="Only show vulnerabilities reachable from entry points.",
)
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror vulnerabilities into the central findings registry — "
        "visible via ``roam findings list --detector vulns``. The "
        "detector-specific output (text / JSON / SARIF) is unchanged; "
        "the registry rows are the denormalised cross-detector surface. "
        "Confidence tier is derived from the ingestion source (curated "
        "scanners -> static_analysis, generic JSON -> heuristic); "
        "reachability is captured separately in evidence_json."
    ),
)
@click.pass_context
def vulns(ctx, import_file, fmt, reachable_only, persist):
    """Scan and manage vulnerability inventory.

    Import vulnerability reports from npm-audit, pip-audit, trivy, osv, or
    generic JSON formats. Show current vulnerability inventory with severity
    breakdown and optional reachability filtering.

    Unlike ``vuln-map`` (which ingests vulnerability reports from specific scanner
    formats) and ``vuln-reach`` (which traces call-graph paths from vulnerabilities
    to entry points), this command provides unified vulnerability inventory with
    auto-format detection, ``--import-file`` ingestion, and ``--reachable-only``
    filtering.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # W607-AQ -- substrate-boundary plumbing for the VEX projection /
    # vuln-ingest leg of the W805 cross-artifact-consistency family
    # (cmd_supply_chain W607-AK is the consumer/projection sibling,
    # cmd_sbom W607-AM is the SBOM emit producer sibling). Prior to W607-AQ
    # a raise inside any of the substrate boundaries -- detect_format /
    # load_npm_audit / load_pip_audit / load_trivy / load_osv / load_generic /
    # ingest_report / query_vulns / compute_reachability / classify_findings /
    # emit_vuln_findings / vulns_to_sarif / serialize_envelope -- crashed the
    # whole vulns invocation wholesale. Each is wrapped via ``_run_check_aq``
    # so a raise becomes a structured
    # ``vulns_<phase>_failed:<exc_class>:<detail>`` marker on
    # ``_w607aq_warnings_out`` -- the envelope still emits cleanly with
    # whatever signal the remaining substrates produced.
    #
    # Marker prefix discipline: every W607-AQ substrate marker uses the
    # canonical ``vulns_<phase>_failed:<exc_class>:<detail>`` shape. cmd_vulns
    # has NO pre-existing warnings_out channel -- W607-AQ is FRESH: the
    # accumulator-based markers become the canonical ``summary.warnings_out``
    # field outright.
    _w607aq_warnings_out: list[str] = []

    def _run_check_aq(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AQ marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``vulns_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607aq_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607aq_warnings_out.append(f"vulns_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-CH -- ADDITIVE aggregation-phase plumbing on top of the W607-AQ
    # substrate-CALL markers. W607-AQ already wrapped the 11 substrate-helper
    # boundaries on the build path (detect_format / load_<5 ingest formats> /
    # ingest_report / query_vulns / emit_vuln_findings / classify_findings /
    # confidence_distribution / verdict_with_high_count / severity_breakdown /
    # vulns_to_sarif / write_sarif / serialize_envelope); W607-CH extends
    # marker coverage to the AGGREGATION-PHASE boundaries that W607-AQ left
    # unguarded:
    #
    #   - ``compute_predicate``    -- per-field extraction of the metric
    #                                 fields (total / by_severity /
    #                                 reachable_count / state /
    #                                 just_imported) used to compose the
    #                                 verdict string + envelope. A future
    #                                 ``_severity_breakdown`` schema
    #                                 refactor that returns a non-dict
    #                                 would otherwise crash the envelope
    #                                 post-build.
    #   - ``compute_verdict``      -- verdict-string assembly based on the
    #                                 vuln-count + severity-breakdown.
    #                                 Floor to a literal "Vulnerability
    #                                 scan completed" string per LAW 6
    #                                 (standalone-parse) + W978 first-
    #                                 hypothesis discipline (no re-
    #                                 interpolation of the same values
    #                                 that just raised).
    #   - ``build_envelope``       -- ``json_envelope("vulns", ...)``
    #                                 projection (downstream contract
    #                                 changes / shape regressions). Phase
    #                                 name distinct from W607-AQ's
    #                                 existing ``serialize_envelope``
    #                                 (which wraps ``to_json`` instead).
    #
    # cmd_vulns is the vulnerability scanner -- W117 origin, original 16
    # findings-registry detectors. Per W826 HIGH-SEV bug pin (cmd_taint
    # silent-SAFE on empty corpus -- security-critical Pattern-2):
    # cmd_vulns must NEVER silently emit a SAFE verdict on the
    # aggregation-phase boundary raising; the marker + partial_success
    # disclosure preserves the W823 empty-corpus security-axis discipline.
    #
    # Closes the SECURITY-REACHABILITY TRIAD at the substrate level
    # alongside cmd_taint (W607-AY) and cmd_vuln_reach (W607-AU).
    #
    # Marker family ``vulns_*`` -- same family as W607-AQ (additive, not a
    # separate prefix). Empty bucket -> byte-identical envelope on the
    # success path.
    #
    # No ``auto_log`` phase: cmd_vulns has no active-run ledger write at
    # present, so the W607-BZ 4-phase set drops to 3 phases here
    # (compute_predicate / compute_verdict / build_envelope). Same marker
    # shape contract, narrower phase set.
    _w607ch_warnings_out: list[str] = []

    def _run_check_ch(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CH marker emission.

        Mirror of ``_run_check_aq`` shape (same ``vulns_<phase>_failed:``
        marker family) but writes into ``_w607ch_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ch_warnings_out.append(f"vulns_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # If importing, we need write access
    if import_file:
        _do_import(
            import_file,
            fmt,
            json_mode,
            sarif_mode,
            token_budget,
            reachable_only,
            persist,
            _run_check_aq,
            _w607aq_warnings_out,
            _run_check_ch,
            _w607ch_warnings_out,
        )
    else:
        _do_inventory(
            json_mode,
            sarif_mode,
            token_budget,
            reachable_only,
            persist,
            _run_check_aq,
            _w607aq_warnings_out,
            _run_check_ch,
            _w607ch_warnings_out,
        )


def _do_import(
    import_file,
    fmt,
    json_mode,
    sarif_mode,
    token_budget,
    reachable_only,
    persist,
    _run_check_aq=None,
    _w607aq_warnings_out=None,
    _run_check_ch=None,
    _w607ch_warnings_out=None,
):
    """Import a vulnerability report and show results."""
    # Fallback no-op wrap for callers that bypass the W607-AQ closure --
    # preserves the pre-W607-AQ behaviour byte-for-byte when no accumulator
    # is wired (e.g. unit tests that import _do_import directly).
    if _run_check_aq is None or _w607aq_warnings_out is None:
        _w607aq_warnings_out = []

        def _run_check_aq(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                _w607aq_warnings_out.append(f"vulns_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

    # W607-CH fallback no-op wrap for callers that bypass the click closure.
    if _run_check_ch is None or _w607ch_warnings_out is None:
        _w607ch_warnings_out = []

        def _run_check_ch(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                _w607ch_warnings_out.append(f"vulns_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

    project_root = find_project_root()
    with open_db(readonly=False) as conn:
        # Dispatch through the format dispatcher; each ingest-format
        # boundary (npm-audit / pip-audit / trivy / osv / generic)
        # is its own substrate phase so a raise in one format does not
        # crash the vulns invocation wholesale.
        if fmt == "auto":
            try:
                raw = _run_check_aq(
                    "detect_format_load",
                    lambda p: _json.loads(Path(p).read_text(encoding="utf-8")),
                    import_file,
                    default=None,
                )
                if raw is None:
                    detected_fmt = "generic"
                else:
                    detected_fmt = _run_check_aq("detect_format", _detect_format, raw, default="generic")
            except (_json.JSONDecodeError, OSError, ValueError):
                detected_fmt = "generic"
        else:
            detected_fmt = fmt

        # Per-format ingest boundary -- each format gets its own marker
        # family so multi-ingest-format coverage is one assertion per
        # format. Source-level guard pins literal phase names so a
        # future refactor that drops a format from the dispatch fails
        # the guard rather than silently regressing.
        if detected_fmt == "npm-audit":
            ingested = _run_check_aq(
                "load_npm_audit",
                _ingest_report,
                conn,
                import_file,
                detected_fmt,
                project_root=project_root,
                default=[],
            )
        elif detected_fmt == "pip-audit":
            ingested = _run_check_aq(
                "load_pip_audit",
                _ingest_report,
                conn,
                import_file,
                detected_fmt,
                project_root=project_root,
                default=[],
            )
        elif detected_fmt == "trivy":
            ingested = _run_check_aq(
                "load_trivy",
                _ingest_report,
                conn,
                import_file,
                detected_fmt,
                project_root=project_root,
                default=[],
            )
        elif detected_fmt == "osv":
            ingested = _run_check_aq(
                "load_osv",
                _ingest_report,
                conn,
                import_file,
                detected_fmt,
                project_root=project_root,
                default=[],
            )
        else:
            ingested = _run_check_aq(
                "load_generic",
                _ingest_report,
                conn,
                import_file,
                detected_fmt,
                project_root=project_root,
                default=[],
            )
        if ingested is None:
            ingested = []
        _run_check_aq("commit_ingest", conn.commit, default=None)

        # Now query the full inventory
        vuln_rows = _run_check_aq(
            "query_vulns",
            _query_vulns,
            conn,
            reachable_only,
            project_root=project_root,
            default=[],
        )
        if vuln_rows is None:
            vuln_rows = []

        if persist:
            try:
                _run_check_aq(
                    "emit_vuln_findings",
                    _emit_vuln_findings,
                    conn,
                    vuln_rows,
                    default=None,
                )
                _run_check_aq("commit_findings", conn.commit, default=None)
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

    _output_results(
        vuln_rows,
        json_mode,
        sarif_mode,
        token_budget,
        extra_summary={"imported": len(ingested), "import_file": import_file},
        reachable_only=reachable_only,
        _run_check_aq=_run_check_aq,
        _w607aq_warnings_out=_w607aq_warnings_out,
        _run_check_ch=_run_check_ch,
        _w607ch_warnings_out=_w607ch_warnings_out,
    )


def _do_inventory(
    json_mode,
    sarif_mode,
    token_budget,
    reachable_only,
    persist,
    _run_check_aq=None,
    _w607aq_warnings_out=None,
    _run_check_ch=None,
    _w607ch_warnings_out=None,
):
    """Show current vulnerability inventory from DB."""
    # Fallback no-op wrap when not invoked from the click closure.
    if _run_check_aq is None or _w607aq_warnings_out is None:
        _w607aq_warnings_out = []

        def _run_check_aq(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                _w607aq_warnings_out.append(f"vulns_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

    # W607-CH fallback no-op wrap when not invoked from the click closure.
    if _run_check_ch is None or _w607ch_warnings_out is None:
        _w607ch_warnings_out = []

        def _run_check_ch(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                _w607ch_warnings_out.append(f"vulns_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

    project_root = find_project_root()

    # When --persist is set we need a writable connection so the
    # findings emit can land — otherwise stay readonly to honour the
    # principle that listing inventory is side-effect-free.
    with open_db(readonly=not persist) as conn:
        vuln_rows = _run_check_aq(
            "query_vulns",
            _query_vulns,
            conn,
            reachable_only,
            project_root=project_root,
            default=[],
        )
        if vuln_rows is None:
            vuln_rows = []

        if persist:
            try:
                _run_check_aq(
                    "emit_vuln_findings",
                    _emit_vuln_findings,
                    conn,
                    vuln_rows,
                    default=None,
                )
                _run_check_aq("commit_findings", conn.commit, default=None)
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

    _output_results(
        vuln_rows,
        json_mode,
        sarif_mode,
        token_budget,
        reachable_only=reachable_only,
        _run_check_aq=_run_check_aq,
        _w607aq_warnings_out=_w607aq_warnings_out,
        _run_check_ch=_run_check_ch,
        _w607ch_warnings_out=_w607ch_warnings_out,
    )


def _is_reachable_from_entries(G, entries, sid) -> bool:
    """Return True if ``sid`` is reachable from any graph entry point."""
    import networkx as nx

    for ep in entries:
        if nx.has_path(G, ep, sid):
            return True
    return False


def _mark_vulns_reachable_from_entries(vulns: list[dict], G, entries) -> None:
    """Mutate ``vulns`` in place, setting ``reachable`` from graph analysis."""
    for v in vulns:
        sid = v["matched_symbol_id"]
        if v["reachable"] != 0 or sid is None:
            continue
        if sid not in G:
            v["reachable"] = -1
            continue
        reached = _is_reachable_from_entries(G, entries, sid)
        v["reachable"] = 1 if reached else -1


def _compute_reachability_in_memory(vulns: list[dict], conn: sqlite3.Connection) -> None:
    """Compute reachability in-memory when the DB cannot be written to.

    Mutates each vuln dict's ``reachable`` field in place:
    - 1  = reachable from at least one entry point
    - -1 = not reachable or symbol not in graph
    """
    needs_analysis = any(v["reachable"] == 0 and v["matched_symbol_id"] is not None for v in vulns)
    if not needs_analysis:
        return

    try:
        from networkx.exception import NetworkXException

        from roam.graph.builder import build_symbol_graph

        G = build_symbol_graph(conn)
        entries = [n for n in G.nodes() if G.in_degree(n) == 0]
        _mark_vulns_reachable_from_entries(vulns, G, entries)
    except ImportError as _exc:
        from roam.observability import log_swallowed

        log_swallowed("cmd_vulns:reachability", _exc)
    except (sqlite3.Error, NetworkXException) as _exc:
        from roam.observability import log_swallowed

        log_swallowed("cmd_vulns:reachability", _exc)


def _query_vulns(
    conn: sqlite3.Connection,
    reachable_only: bool,
    project_root: str | Path | None = None,
) -> list[dict]:
    """Query vulnerabilities from the DB, optionally filtered by reachability.

    If reachable_only is True and reachability data exists, build the graph
    and run reachability analysis first.
    """
    # Check if vulnerabilities table has data
    try:
        rows = conn.execute(
            "SELECT id, cve_id, package_name, severity, title, source, "
            "matched_symbol_id, matched_file, reachable, shortest_path, hop_count "
            "FROM vulnerabilities ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    vulns = [dict(r) for r in rows]

    if project_root is not None:
        from roam.security.vuln_store import match_vuln_to_symbols

        for vuln in vulns:
            matches = match_vuln_to_symbols(
                conn,
                vuln.get("package_name") or "",
                project_root=project_root,
            )
            import_match = next((match for match in matches if match["match_kind"] == "import_site"), None)
            if import_match is not None:
                vuln["match_kind"] = "import_site"
                # File and line must come from the SAME match: legacy rows can
                # carry a matched_file from a pre-fix symbol-name coincidence,
                # and pairing that file with a fresh scan's line would render
                # false evidence ("<stale-file>:<line> (imported)").
                vuln["matched_file"] = import_match["file_path"]
                vuln["matched_line"] = import_match["line"]
            elif vuln.get("matched_symbol_id") is not None:
                symbol_match = next(
                    (match for match in matches if match["symbol_id"] == vuln["matched_symbol_id"]),
                    None,
                )
                vuln["match_kind"] = symbol_match["match_kind"] if symbol_match else "symbol_name"

    if reachable_only:
        # If we don't have reachability data yet, compute it in-memory
        # (the connection may be readonly, so we cannot write back to DB)
        _compute_reachability_in_memory(vulns, conn)

        # Filter to reachable only
        vulns = [v for v in vulns if v.get("reachable") == 1]

    return vulns


def _make_fallback_run_check(bucket: list[str], prefix: str):
    """Build a no-op W607 accumulator for use outside the click closure."""

    def _run_check(phase, fn, *args, default=None, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            bucket.append(f"{prefix}_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    return _run_check


def _compute_predicate_fields(
    total_local: int,
    by_severity_local: dict,
    reachable_count_local: int,
    just_imported_local: bool,
) -> dict:
    """Assemble the verdict-input predicate fields (state + sev_parts)."""
    sev_parts: list[str] = []
    for sev in ("critical", "high", "medium", "low", "unknown"):
        count = by_severity_local.get(sev, 0)
        if count > 0:
            sev_parts.append(f"{count} {sev}")
    if total_local == 0 and not just_imported_local:
        state_local = "no_scan"
        partial_success_local = True
    else:
        state_local = "scanned"
        partial_success_local = False
    return {
        "total": total_local,
        "sev_parts": sev_parts,
        "reachable_count": reachable_count_local,
        "just_imported": just_imported_local,
        "state": state_local,
        "partial_success": partial_success_local,
    }


def _build_verdict_str(fields: dict) -> str:
    """Render the one-line verdict from predicate fields (LAW 6 compliant)."""
    total_local = fields["total"]
    sev_parts = fields["sev_parts"]
    reachable_count_local = fields["reachable_count"]
    just_imported_local = fields["just_imported"]
    if total_local == 0 and not just_imported_local:
        return (
            "no vulnerability scan available (vulnerabilities table is empty; "
            "run `roam vulns --import-file <report.json>` to ingest npm-audit, "
            "pip-audit, trivy, or osv output)"
        )
    if total_local == 0:
        return "No vulnerabilities found"
    sev_str = ", ".join(sev_parts)
    out = f"{total_local} vulnerabilities ({sev_str})"
    if reachable_count_local > 0:
        out += f", {reachable_count_local} reachable"
    return out


def _render_sarif_output(vulns: list[dict], reachable_only: bool, _run_check_aq) -> None:
    """Emit SARIF 2.1.0 output with finding-level filter disclosure."""
    from roam.output.sarif import runtime_filter_disclosure, write_sarif

    # W1061-followup-2: finding-level filter disclosure delegated to
    # the shared :func:`runtime_filter_disclosure` helper. cmd_vulns
    # has only one such filter today — ``--reachable-only`` — and it
    # operates at finding-evaluation time (filters each row by its
    # ``reachable`` field), NOT at rule-id granularity. Surfaces
    # under a synthetic ``reachable-only-filter`` notification
    # descriptor per SARIF 2.1.0 §3.20.4.
    finding_filters: list[tuple[str, dict]] = []
    if reachable_only:
        finding_filters.append(
            (
                "reachable-only-filter",
                {"filter": "--reachable-only", "filter_value": True},
            )
        )
    _, sarif_notif_overrides = runtime_filter_disclosure(
        finding_level_filters=finding_filters,
    )
    sarif = _run_check_aq(
        "vulns_to_sarif",
        _vulns_to_sarif,
        vulns,
        runtime_notification_overrides=sarif_notif_overrides or None,
        default={},
    )
    if sarif is None:
        sarif = {}
    sarif_text = _run_check_aq(
        "write_sarif",
        write_sarif,
        sarif,
        default="{}",
    )
    if sarif_text is None:
        sarif_text = "{}"
    click.echo(sarif_text)


def _match_evidence(vuln: dict) -> str | None:
    matched_file = vuln.get("matched_file")
    if not matched_file:
        return None
    if vuln.get("match_kind") == "import_site":
        line = vuln.get("matched_line")
        return f"{matched_file}:{line} (imported)" if line else f"{matched_file} (imported)"
    if vuln.get("match_kind") == "symbol_name":
        return f"{matched_file} (symbol-name match)"
    if vuln.get("match_kind") == "import_edge":
        return f"{matched_file} (import-edge match)"
    return str(matched_file)


def _build_vuln_records(vulns: list[dict]) -> list[dict]:
    """Project raw vuln rows into R22 wrap_findings input records."""
    vuln_records: list[dict] = []
    for v in vulns:
        rec: dict = {
            "cve_id": v.get("cve_id"),
            "package": v.get("package_name"),
            "severity": v.get("severity"),
            "title": v.get("title"),
            "source": v.get("source"),
            "matched_file": v.get("matched_file"),
            "matched_line": v.get("matched_line"),
            "match_kind": v.get("match_kind"),
            "match_evidence": _match_evidence(v),
            "reachable": v.get("reachable", 0),
        }
        if v.get("shortest_path"):
            rec["shortest_path"] = v["shortest_path"]
        if v.get("hop_count"):
            rec["hop_count"] = v["hop_count"]
        vuln_records.append(rec)
    return vuln_records


def _compute_json_distribution(
    vuln_records: list[dict],
    verdict: str,
    _run_check_aq,
) -> tuple[list, dict, str]:
    """Wrap findings + compute confidence distribution + verdict_with_high_count."""
    vuln_triples = _run_check_aq(
        "classify_findings",
        wrap_findings,
        vuln_records,
        classifier=_vuln_classify,
        default=[],
    )
    if vuln_triples is None:
        vuln_triples = []
    distribution = _run_check_aq(
        "confidence_distribution",
        confidence_distribution,
        vuln_triples,
        default={},
    )
    if distribution is None:
        distribution = {}
    verdict_with_conf = _run_check_aq(
        "verdict_with_high_count",
        verdict_with_high_count,
        verdict,
        distribution,
        default=verdict,
    )
    if verdict_with_conf is None:
        verdict_with_conf = verdict
    return vuln_triples, distribution, verdict_with_conf


def _build_json_summary(
    verdict_with_conf: str,
    state: str,
    partial_success: bool,
    total: int,
    by_severity: dict,
    reachable_count: int,
    distribution: dict,
    extra_summary: dict | None,
    combined_warnings: list[str],
) -> dict:
    """Build the canonical JSON summary dict; flip partial_success on any warning."""
    summary: dict = {
        "verdict": verdict_with_conf,
        "state": state,
        "partial_success": partial_success,
        "total": total,
        "by_severity": by_severity,
        "reachable_count": reachable_count,
        "findings_confidence_distribution": distribution,
    }
    if extra_summary:
        summary.update(extra_summary)
    # W607-AQ / W607-CH: merge substrate-CALL markers AND aggregation-
    # phase markers into the canonical ``warnings_out`` channel. Both
    # buckets share the canonical ``vulns_*`` family (the W607-CH bucket
    # is ADDITIVE, not a separate prefix). ``partial_success`` flips when
    # ANY bucket is non-empty. W805 invariant: vulns invocation never
    # collapses to a silent SAFE verdict when any of the ingest / reach
    # / VEX-emit substrates or aggregation-phase boundaries raised. W826
    # security-axis: the cmd_taint silent-SAFE Pattern-2 bug must NOT
    # regress here.
    if combined_warnings:
        summary["warnings_out"] = list(combined_warnings)
        summary["partial_success"] = True
    return summary


def _build_envelope_floor(verdict: str, combined_warnings: list[str]) -> dict:
    """Synthesize the W607-CH build_envelope floor stub (minimal parseable shape)."""
    return {
        "command": "vulns",
        "schema_version": "1.0.0",
        "summary": {
            "verdict": verdict,
            "partial_success": True,
            "warnings_out": list(combined_warnings),
        },
        "warnings_out": list(combined_warnings),
    }


def _render_json_output(
    vulns: list[dict],
    verdict: str,
    state: str,
    partial_success: bool,
    total: int,
    by_severity: dict,
    reachable_count: int,
    extra_summary: dict | None,
    token_budget: int,
    _run_check_aq,
    _run_check_ch,
    _w607aq_warnings_out: list[str],
    _w607ch_warnings_out: list[str],
) -> None:
    """Emit the JSON envelope with W607 boundary protection + floor-rebuild safety net."""
    # R22: wrap each vuln in {value, confidence, reason}. Consumers
    # that previously read `vulnerabilities[i]["cve_id"]` must now
    # read `vulnerabilities[i]["value"]["cve_id"]` plus
    # `vulnerabilities[i]["confidence"]` / `vulnerabilities[i]["reason"]`.
    vuln_records = _build_vuln_records(vulns)
    vuln_triples, distribution, verdict_with_conf = _compute_json_distribution(
        vuln_records,
        verdict,
        _run_check_aq,
    )

    _combined_warnings_out = list(_w607aq_warnings_out) + list(_w607ch_warnings_out)
    summary = _build_json_summary(
        verdict_with_conf,
        state,
        partial_success,
        total,
        by_severity,
        reachable_count,
        distribution,
        extra_summary,
        _combined_warnings_out,
    )

    envelope_kwargs: dict = {
        "summary": summary,
        "budget": token_budget,
        "vulnerabilities": vuln_triples,
    }
    if _combined_warnings_out:
        envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

    # W607-CH -- build_envelope boundary. Wraps the
    # ``json_envelope("vulns", ...)`` projection. A downstream schema-
    # shape refactor that breaks the envelope helper would otherwise
    # crash AFTER all substrate + aggregation signals were already
    # gathered. Floor to a minimal envelope stub so consumers still
    # receive a parseable JSON object with the marker attached + the
    # canonical command name. Phase name distinct from W607-AQ's
    # existing ``serialize_envelope`` (which wraps ``to_json`` instead).
    _envelope_floor = _build_envelope_floor(verdict, _combined_warnings_out)
    envelope = _run_check_ch(
        "build_envelope",
        json_envelope,
        "vulns",
        default=_envelope_floor,
        **envelope_kwargs,
    )
    # W607-CH -- if ``build_envelope`` raised AFTER the combined bucket
    # was already snapshotted, the new ``vulns_build_envelope_failed:``
    # marker was appended to ``_w607ch_warnings_out`` and the floor
    # stub carries only the pre-raise combined list. Rebuild the floor
    # stub's warnings_out so the new marker reaches the JSON output.
    # Clean path -> envelope is the real json_envelope return value,
    # no rebuild needed.
    if envelope is _envelope_floor and _w607ch_warnings_out:
        _combined_warnings_out = list(_w607aq_warnings_out) + list(_w607ch_warnings_out)
        _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
        _envelope_floor["warnings_out"] = list(_combined_warnings_out)
        envelope = _envelope_floor

    output_text = _run_check_aq(
        "serialize_envelope",
        to_json,
        envelope,
        default="{}",
    )
    if output_text is None:
        output_text = "{}"
    # W805 / Pattern-1 variant-D safety net: if serialize_envelope
    # raised AFTER envelope build, re-build with the now-disclosed
    # marker on warnings_out so the consumer still sees it.
    if output_text == "{}" and (_w607aq_warnings_out or _w607ch_warnings_out):
        _combined_warnings_out = list(_w607aq_warnings_out) + list(_w607ch_warnings_out)
        envelope_kwargs["warnings_out"] = list(_combined_warnings_out)
        envelope_kwargs["summary"]["warnings_out"] = list(_combined_warnings_out)
        try:
            envelope = json_envelope("vulns", **envelope_kwargs)
            output_text = to_json(envelope)
        except (TypeError, ValueError):
            output_text = "{}"
    click.echo(output_text)


def _render_text_output(
    vulns: list[dict],
    verdict: str,
    total: int,
    reachable_count: int,
    state: str,
    extra_summary: dict | None,
) -> None:
    """Emit the plain-text verdict + severity-sorted table + summary line."""
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if vulns:
        # Sort by severity (critical first)
        sorted_vulns = sorted(vulns, key=lambda v: -_severity_rank(v.get("severity", "unknown")))

        rows = []
        for v in sorted_vulns:
            cve = v.get("cve_id") or "-"
            pkg = v.get("package_name") or "-"
            sev = (v.get("severity") or "unknown").upper()
            title = v.get("title") or "-"
            if len(title) > 50:
                title = title[:47] + "..."
            reach = "yes" if v.get("reachable") == 1 else ("no" if v.get("reachable") == -1 else "?")
            matched = _match_evidence(v) or "-"
            rows.append([cve, pkg, sev, title, reach, matched])

        click.echo(
            format_table(
                ["CVE", "Package", "Severity", "Title", "Reachable", "File"],
                rows,
            )
        )
        click.echo()

        # Summary line
        summary_parts = [f"{total} vulnerabilities"]
        if reachable_count > 0:
            summary_parts.append(f"{reachable_count} reachable from entry points")
        click.echo(f"  {', '.join(summary_parts)}")

        if extra_summary and extra_summary.get("imported"):
            click.echo(f"  Imported {extra_summary['imported']} from {extra_summary['import_file']}")
    else:
        if state == "no_scan":
            click.echo("  No vulnerability scan has been imported yet.")
        else:
            click.echo("  No vulnerabilities in the database.")
        click.echo("  Import a report: roam vulns --import-file <report.json>")


def _output_results(
    vulns: list[dict],
    json_mode: bool,
    sarif_mode: bool,
    token_budget: int,
    extra_summary: dict | None = None,
    *,
    reachable_only: bool = False,
    _run_check_aq=None,
    _w607aq_warnings_out=None,
    _run_check_ch=None,
    _w607ch_warnings_out=None,
):
    """Produce output in text, JSON, or SARIF format."""
    # Fallback no-op accumulators when invoked outside the click closure.
    if _run_check_aq is None or _w607aq_warnings_out is None:
        _w607aq_warnings_out = []
        _run_check_aq = _make_fallback_run_check(_w607aq_warnings_out, "vulns")

    # W607-CH fallback no-op accumulator when invoked outside the click closure.
    if _run_check_ch is None or _w607ch_warnings_out is None:
        _w607ch_warnings_out = []
        _run_check_ch = _make_fallback_run_check(_w607ch_warnings_out, "vulns")

    total = len(vulns)
    by_severity = _run_check_aq(
        "severity_breakdown",
        _severity_breakdown,
        vulns,
        default={},
    )
    if by_severity is None:
        by_severity = {}
    reachable_count = sum(1 for v in vulns if v.get("reachable") == 1)

    # Fix E (Pattern 2: silent fallbacks) — distinguish "scan ran and found
    # 0 vulnerabilities" from "no scan has ever been imported / no
    # vulnerability data exists in the DB". The previous code reported
    # "No vulnerabilities found" in BOTH cases, which silently hid the
    # no-scan scenario from consumers and could be read as "this codebase
    # is safe" when in fact no scanner has touched it.
    #
    # Heuristic: if no records in `vulnerabilities` AND the caller did not
    # just import a report (extra_summary["imported"] is unset), we are in
    # the no-scan state. An import that found zero rows is still a scan.
    just_imported = bool(extra_summary and extra_summary.get("imported") is not None)

    # W607-CH -- compute_predicate boundary. Wraps the per-field extraction
    # of metrics so a future ``_severity_breakdown`` schema refactor that
    # returns a non-dict (or a dict missing the canonical CVSS keys)
    # surfaces a marker rather than crashing the verdict assembly. Floor
    # to a documented empty-shape dict so downstream verdict/summary
    # fields stay non-null.
    _pred_fields = _run_check_ch(
        "compute_predicate",
        _compute_predicate_fields,
        total,
        by_severity,
        reachable_count,
        just_imported,
        default={
            "total": total,
            "sev_parts": [],
            "reachable_count": reachable_count,
            "just_imported": just_imported,
            "state": "scanned",
            "partial_success": False,
        },
    )
    state = _pred_fields["state"]
    partial_success = _pred_fields["partial_success"]

    # W607-CH -- compute_verdict boundary. Wraps the verdict-string assembly
    # so a downstream f-string refactor (e.g. a non-int total or a
    # severity tuple that raises on join) surfaces a marker rather than
    # crashing the envelope. Floor must NOT re-interpolate the same values
    # that tripped the closure (W978 first-hypothesis discipline: a
    # __format__-raising sentinel under test would re-raise inside the
    # default f-string). Use a literal ``"Vulnerability scan completed"``
    # floor instead (LAW 6 still holds: the line works standalone).
    # Mirror of cmd_supply_chain W607-CD / cmd_cga W607-BZ compute_verdict
    # pattern.
    verdict = _run_check_ch(
        "compute_verdict",
        _build_verdict_str,
        _pred_fields,
        default="Vulnerability scan completed",
    )

    if sarif_mode:
        _render_sarif_output(vulns, reachable_only, _run_check_aq)
        return

    if json_mode:
        _render_json_output(
            vulns,
            verdict,
            state,
            partial_success,
            total,
            by_severity,
            reachable_count,
            extra_summary,
            token_budget,
            _run_check_aq,
            _run_check_ch,
            _w607aq_warnings_out,
            _w607ch_warnings_out,
        )
        return

    _render_text_output(vulns, verdict, total, reachable_count, state, extra_summary)
