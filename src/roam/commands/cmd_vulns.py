"""Vulnerability scanning — import, inventory, and reachability analysis."""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
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


def _ingest_report(conn: sqlite3.Connection, report_path: str, fmt: str) -> list[dict]:
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

    return ingester(conn, report_path)


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


def _vulns_to_sarif(vulns: list[dict]) -> dict:
    """Convert vulnerability findings to SARIF 2.1.0 format."""
    from roam.output._severity import to_sarif_level
    from roam.output.sarif import _TOOL_NAME, _get_version, _location, _slugify, to_sarif

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

        if rule_id not in seen_rules:
            seen_rules[rule_id] = {
                "id": rule_id,
                "shortDescription": f"Vulnerability: {title}",
                "helpUri": f"https://nvd.nist.gov/vuln/detail/{cve}" if cve.startswith("CVE-") else "",
                "defaultLevel": sarif_level,
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
            }
        )

    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        list(seen_rules.values()),
        results,
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
    side_effect=False,
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
        )
    else:
        _do_inventory(json_mode, sarif_mode, token_budget, reachable_only, persist)


def _do_import(
    import_file,
    fmt,
    json_mode,
    sarif_mode,
    token_budget,
    reachable_only,
    persist,
):
    """Import a vulnerability report and show results."""
    with open_db(readonly=False) as conn:
        ingested = _ingest_report(conn, import_file, fmt)
        conn.commit()

        # Now query the full inventory
        vuln_rows = _query_vulns(conn, reachable_only)

        if persist:
            try:
                _emit_vuln_findings(conn, vuln_rows)
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

    _output_results(
        vuln_rows,
        json_mode,
        sarif_mode,
        token_budget,
        extra_summary={"imported": len(ingested), "import_file": import_file},
    )


def _do_inventory(json_mode, sarif_mode, token_budget, reachable_only, persist):
    """Show current vulnerability inventory from DB."""
    # When --persist is set we need a writable connection so the
    # findings emit can land — otherwise stay readonly to honour the
    # principle that listing inventory is side-effect-free.
    with open_db(readonly=not persist) as conn:
        vuln_rows = _query_vulns(conn, reachable_only)

        if persist:
            try:
                _emit_vuln_findings(conn, vuln_rows)
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

    _output_results(vuln_rows, json_mode, sarif_mode, token_budget)


def _query_vulns(conn: sqlite3.Connection, reachable_only: bool) -> list[dict]:
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
    except Exception:
        return []

    vulns = [dict(r) for r in rows]

    if reachable_only:
        # If we don't have reachability data yet, compute it in-memory
        # (the connection may be readonly, so we cannot write back to DB)
        needs_analysis = any(v["reachable"] == 0 and v["matched_symbol_id"] is not None for v in vulns)
        if needs_analysis:
            try:
                from roam.graph.builder import build_symbol_graph

                G = build_symbol_graph(conn)
                # Find entry points (in-degree 0)
                entries = [n for n in G.nodes() if G.in_degree(n) == 0]
                import networkx as nx

                for v in vulns:
                    sid = v["matched_symbol_id"]
                    if v["reachable"] != 0 or sid is None:
                        continue
                    if sid not in G:
                        v["reachable"] = -1
                        continue
                    # Check reachability from any entry point
                    reached = False
                    for ep in entries:
                        if nx.has_path(G, ep, sid):
                            reached = True
                            break
                    v["reachable"] = 1 if reached else -1
            except Exception:
                pass

        # Filter to reachable only
        vulns = [v for v in vulns if v.get("reachable") == 1]

    return vulns


def _output_results(
    vulns: list[dict],
    json_mode: bool,
    sarif_mode: bool,
    token_budget: int,
    extra_summary: dict | None = None,
):
    """Produce output in text, JSON, or SARIF format."""
    total = len(vulns)
    by_severity = _severity_breakdown(vulns)
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
    state = "scanned"
    partial_success = False

    if total == 0 and not just_imported:
        state = "no_scan"
        partial_success = True
        verdict = (
            "no vulnerability scan available (vulnerabilities table is empty; "
            "run `roam vulns --import-file <report.json>` to ingest npm-audit, "
            "pip-audit, trivy, or osv output)"
        )
    elif total == 0:
        verdict = "No vulnerabilities found"
    else:
        sev_parts = []
        for sev in ("critical", "high", "medium", "low", "unknown"):
            count = by_severity.get(sev, 0)
            if count > 0:
                sev_parts.append(f"{count} {sev}")
        sev_str = ", ".join(sev_parts)
        verdict = f"{total} vulnerabilities ({sev_str})"
        if reachable_count > 0:
            verdict += f", {reachable_count} reachable"

    # --- SARIF output ---
    if sarif_mode:
        from roam.output.sarif import write_sarif

        sarif = _vulns_to_sarif(vulns)
        click.echo(write_sarif(sarif))
        return

    # --- JSON output ---
    if json_mode:
        # R22: wrap each vuln in {value, confidence, reason}. Consumers
        # that previously read `vulnerabilities[i]["cve_id"]` must now
        # read `vulnerabilities[i]["value"]["cve_id"]` plus
        # `vulnerabilities[i]["confidence"]` / `vulnerabilities[i]["reason"]`.
        # We build the raw records first (so the classifier sees source+
        # reachable), then wrap.
        vuln_records = []
        for v in vulns:
            rec: dict = {
                "cve_id": v.get("cve_id"),
                "package": v.get("package_name"),
                "severity": v.get("severity"),
                "title": v.get("title"),
                "source": v.get("source"),
                "matched_file": v.get("matched_file"),
                "reachable": v.get("reachable", 0),
            }
            if v.get("shortest_path"):
                rec["shortest_path"] = v["shortest_path"]
            if v.get("hop_count"):
                rec["hop_count"] = v["hop_count"]
            vuln_records.append(rec)

        vuln_triples = wrap_findings(vuln_records, classifier=_vuln_classify)
        distribution = confidence_distribution(vuln_triples)
        verdict_with_conf = verdict_with_high_count(verdict, distribution)

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

        envelope = json_envelope(
            "vulns",
            summary=summary,
            budget=token_budget,
            vulnerabilities=vuln_triples,
        )
        click.echo(to_json(envelope))
        return

    # --- Text output ---
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
            matched = v.get("matched_file") or "-"
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
