"""Vulnerability scanning — import, inventory, and reachability analysis."""

from __future__ import annotations

import json as _json
import os
import sqlite3
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


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
        ingest_npm_audit, ingest_pip_audit, ingest_trivy,
        ingest_osv, ingest_generic,
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

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}


def _severity_rank(sev: str) -> int:
    return _SEVERITY_ORDER.get((sev or "unknown").lower(), 0)


def _severity_breakdown(vulns: list[dict], key: str = "severity") -> dict[str, int]:
    """Compute a severity breakdown dict from a list of vuln dicts."""
    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    for v in vulns:
        sev = (v.get(key) or "unknown").lower()
        if sev in counts:
            counts[sev] += 1
        else:
            counts["unknown"] += 1
    # Remove zero-count entries for cleaner output
    return {k: v for k, v in counts.items() if v > 0}


# ---------------------------------------------------------------------------
# SARIF conversion
# ---------------------------------------------------------------------------

def _vulns_to_sarif(vulns: list[dict]) -> dict:
    """Convert vulnerability findings to SARIF 2.1.0 format."""
    from roam.output.sarif import to_sarif, _to_level, _location, _slugify, _get_version, _TOOL_NAME

    seen_rules: dict[str, dict] = {}
    results: list[dict] = []

    for v in vulns:
        cve = v.get("cve_id") or v.get("cve") or "unknown"
        pkg = v.get("package_name") or v.get("package") or "unknown"
        severity = (v.get("severity") or "unknown").lower()
        title = v.get("title") or f"Vulnerability in {pkg}"
        matched_file = v.get("matched_file") or ""

        rule_id = f"vuln/{_slugify(cve)}" if cve != "unknown" else f"vuln/{_slugify(pkg)}"

        if rule_id not in seen_rules:
            seen_rules[rule_id] = {
                "id": rule_id,
                "shortDescription": f"Vulnerability: {title}",
                "helpUri": f"https://nvd.nist.gov/vuln/detail/{cve}" if cve.startswith("CVE-") else "",
                "defaultLevel": _to_level(severity.upper()),
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

        results.append({
            "ruleId": rule_id,
            "level": _to_level(severity.upper()),
            "message": {"text": " | ".join(msg_parts)},
            "locations": locations,
        })

    return to_sarif(
        _TOOL_NAME,
        _get_version(),
        list(seen_rules.values()),
        results,
    )


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("vulns")
@click.option("--import-file", "import_file", default=None, type=click.Path(exists=True),
              help="Import a vulnerability report (auto-detects format).")
@click.option("--format", "fmt", default="auto",
              type=click.Choice(_FORMAT_CHOICES, case_sensitive=False),
              help="Report format (default: auto-detect).")
@click.option("--reachable-only", is_flag=True, default=False,
              help="Only show vulnerabilities reachable from entry points.")
@click.pass_context
def vulns(ctx, import_file, fmt, reachable_only):
    """Scan and manage vulnerability inventory.

    Import vulnerability reports from npm-audit, pip-audit, trivy, osv, or
    generic JSON formats. Show current vulnerability inventory with severity
    breakdown and optional reachability filtering.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # If importing, we need write access
    if import_file:
        _do_import(import_file, fmt, json_mode, sarif_mode, token_budget, reachable_only)
    else:
        _do_inventory(json_mode, sarif_mode, token_budget, reachable_only)


def _do_import(import_file, fmt, json_mode, sarif_mode, token_budget, reachable_only):
    """Import a vulnerability report and show results."""
    with open_db(readonly=False) as conn:
        ingested = _ingest_report(conn, import_file, fmt)
        conn.commit()

        # Now query the full inventory
        vuln_rows = _query_vulns(conn, reachable_only)

    _output_results(
        vuln_rows, json_mode, sarif_mode, token_budget,
        extra_summary={"imported": len(ingested), "import_file": import_file},
    )


def _do_inventory(json_mode, sarif_mode, token_budget, reachable_only):
    """Show current vulnerability inventory from DB."""
    with open_db(readonly=True) as conn:
        vuln_rows = _query_vulns(conn, reachable_only)

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
        # If we don't have reachability data yet, compute it
        needs_analysis = any(v["reachable"] == 0 and v["matched_symbol_id"] is not None for v in vulns)
        if needs_analysis:
            try:
                from roam.graph.builder import build_symbol_graph
                from roam.security.vuln_reach import analyze_reachability
                G = build_symbol_graph(conn)
                analyzed = analyze_reachability(conn, G)
                # Refresh from DB
                rows = conn.execute(
                    "SELECT id, cve_id, package_name, severity, title, source, "
                    "matched_symbol_id, matched_file, reachable, shortest_path, hop_count "
                    "FROM vulnerabilities ORDER BY id"
                ).fetchall()
                vulns = [dict(r) for r in rows]
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

    if total == 0:
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
        summary: dict = {
            "verdict": verdict,
            "total": total,
            "by_severity": by_severity,
            "reachable_count": reachable_count,
        }
        if extra_summary:
            summary.update(extra_summary)

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

        envelope = json_envelope(
            "vulns",
            summary=summary,
            budget=token_budget,
            vulnerabilities=vuln_records,
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

        click.echo(format_table(
            ["CVE", "Package", "Severity", "Title", "Reachable", "File"],
            rows,
        ))
        click.echo()

        # Summary line
        summary_parts = [f"{total} vulnerabilities"]
        if reachable_count > 0:
            summary_parts.append(f"{reachable_count} reachable from entry points")
        click.echo(f"  {', '.join(summary_parts)}")

        if extra_summary and extra_summary.get("imported"):
            click.echo(f"  Imported {extra_summary['imported']} from {extra_summary['import_file']}")
    else:
        click.echo("  No vulnerabilities in the database.")
        click.echo("  Import a report: roam vulns --import-file <report.json>")
