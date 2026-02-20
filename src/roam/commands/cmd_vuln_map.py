"""Ingest vulnerability reports and match them to codebase symbols."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.option("--npm-audit", "npm_audit_path", default=None, type=click.Path(exists=True),
              help="Path to npm audit JSON report")
@click.option("--pip-audit", "pip_audit_path", default=None, type=click.Path(exists=True),
              help="Path to pip-audit JSON report")
@click.option("--trivy", "trivy_path", default=None, type=click.Path(exists=True),
              help="Path to Trivy JSON report")
@click.option("--osv", "osv_path", default=None, type=click.Path(exists=True),
              help="Path to OSV scanner JSON report")
@click.option("--generic", "generic_path", default=None, type=click.Path(exists=True),
              help="Path to generic JSON vulnerability list")
@click.pass_context
def vuln_map(ctx, npm_audit_path, pip_audit_path, trivy_path, osv_path, generic_path):
    """Ingest vulnerability scanner reports and match to codebase symbols.

    Reads vulnerability reports from npm-audit, pip-audit, Trivy, OSV, or
    a generic JSON format. For each vulnerability, attempts to match the
    affected package to symbols in the codebase index.

    \b
    Examples:
        roam vuln-map --npm-audit audit.json
        roam vuln-map --pip-audit pip-audit.json
        roam vuln-map --trivy trivy.json
        roam vuln-map --osv osv.json
        roam vuln-map --generic vulns.json
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.security.vuln_store import (
        ingest_npm_audit, ingest_pip_audit, ingest_trivy,
        ingest_osv, ingest_generic,
    )

    all_vulns: list[dict] = []

    with open_db(readonly=False) as conn:
        if npm_audit_path:
            all_vulns.extend(ingest_npm_audit(conn, npm_audit_path))
        if pip_audit_path:
            all_vulns.extend(ingest_pip_audit(conn, pip_audit_path))
        if trivy_path:
            all_vulns.extend(ingest_trivy(conn, trivy_path))
        if osv_path:
            all_vulns.extend(ingest_osv(conn, osv_path))
        if generic_path:
            all_vulns.extend(ingest_generic(conn, generic_path))

    if not all_vulns:
        if json_mode:
            click.echo(to_json(json_envelope("vuln-map",
                summary={"verdict": "No vulnerability reports provided", "total": 0, "matched": 0},
                vulnerabilities=[],
            )))
            return
        click.echo("VERDICT: No vulnerability reports provided")
        click.echo("  Use --npm-audit, --pip-audit, --trivy, --osv, or --generic to supply a report.")
        return

    matched = sum(1 for v in all_vulns if v.get("matched_symbol_id") is not None)
    total = len(all_vulns)

    if json_mode:
        click.echo(to_json(json_envelope("vuln-map",
            summary={
                "verdict": f"{total} vulnerabilities ingested, {matched} matched to symbols",
                "total": total,
                "matched": matched,
            },
            vulnerabilities=all_vulns,
        )))
        return

    click.echo(f"VERDICT: {total} vulnerabilities ingested, {matched} matched to symbols")
    click.echo("")

    for v in all_vulns:
        cve = v.get("cve_id") or "(no CVE)"
        pkg = v.get("package_name", "?")
        sev = (v.get("severity") or "unknown").upper()
        matched_file = v.get("matched_file")
        if matched_file:
            click.echo(f"  {cve:20s} {pkg:30s} {sev:10s} -> matched: {matched_file}")
        else:
            click.echo(f"  {cve:20s} {pkg:30s} {sev:10s} -> no match (not imported)")
