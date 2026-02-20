"""Query reachability of ingested vulnerabilities through the call graph."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.option("--from", "from_entry", default=None,
              help="Check reachability from a specific entry point symbol")
@click.option("--cve", "cve_id", default=None,
              help="Analyze a specific CVE ID")
@click.pass_context
def vuln_reach(ctx, from_entry, cve_id):
    """Query reachability of ingested vulnerabilities through the call graph.

    Analyzes whether vulnerabilities are reachable from entry points (symbols
    with no incoming calls). Unreachable vulnerabilities can be safely
    deprioritized.

    \b
    Examples:
        roam vuln-reach                          # all vulns with reachability
        roam vuln-reach --from handle_request    # from specific entry point
        roam vuln-reach --cve CVE-2024-1234      # specific vulnerability
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.graph.builder import build_symbol_graph
    from roam.security.vuln_store import ensure_vuln_table
    from roam.security.vuln_reach import (
        analyze_reachability, reach_from_entry, reach_for_cve,
    )

    with open_db(readonly=False) as conn:
        ensure_vuln_table(conn)
        G = build_symbol_graph(conn)

        # Check if any vulnerabilities exist
        vuln_count = conn.execute("SELECT COUNT(*) FROM vulnerabilities").fetchone()[0]
        if vuln_count == 0:
            if json_mode:
                click.echo(to_json(json_envelope("vuln-reach",
                    summary={
                        "verdict": "No vulnerabilities ingested. Run vuln-map first.",
                        "total_vulns": 0,
                        "reachable_count": 0,
                        "critical_count": 0,
                    },
                    vulnerabilities=[],
                )))
                return
            click.echo("VERDICT: No vulnerabilities ingested. Run vuln-map first.")
            return

        # Dispatch based on flags
        if cve_id:
            result = reach_for_cve(conn, G, cve_id)
            _output_cve(ctx, result, json_mode)
            return

        if from_entry:
            results = reach_from_entry(conn, G, from_entry)
            _output_from_entry(ctx, results, from_entry, json_mode)
            return

        # Default: analyze all
        results = analyze_reachability(conn, G)
        _output_all(ctx, results, json_mode)


def _output_all(ctx, results: list[dict], json_mode: bool) -> None:
    """Output for full reachability analysis."""
    reachable = [r for r in results if r["reachable"] == 1]
    critical = [r for r in reachable if (r.get("severity") or "").lower() == "critical"]
    total = len(results)

    verdict = f"{len(reachable)} reachable vulnerabilities"
    if critical:
        verdict += f", {len(critical)} critical path{'s' if len(critical) != 1 else ''}"

    if json_mode:
        vuln_out = []
        for r in results:
            vuln_out.append({
                "cve": r.get("cve_id"),
                "package": r.get("package_name"),
                "severity": r.get("severity"),
                "reachable": r["reachable"] == 1,
                "path": r.get("path_names", []),
                "hops": r.get("hop_count", 0),
                "blast_radius": r.get("blast_radius", 0),
            })
        click.echo(to_json(json_envelope("vuln-reach",
            summary={
                "verdict": verdict,
                "total_vulns": total,
                "reachable_count": len(reachable),
                "critical_count": len(critical),
            },
            vulnerabilities=vuln_out,
        )))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")

    # Show reachable first, sorted by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
    sorted_results = sorted(results,
        key=lambda r: (
            0 if r["reachable"] == 1 else 1,
            severity_order.get((r.get("severity") or "unknown").lower(), 5),
        ))

    for r in sorted_results:
        cve = r.get("cve_id") or r.get("package_name", "?")
        pkg = r.get("package_name", "?")
        title = r.get("title") or ""
        sev = (r.get("severity") or "unknown").upper()

        if r["reachable"] == 1:
            click.echo(f"{cve} ({pkg}" + (f" -- {title}" if title else "") + f") -- {sev}")
            path_names = r.get("path_names", [])
            if path_names:
                click.echo("  Path: " + path_names[0])
                for name in path_names[1:]:
                    click.echo(f"    -> {name}")
            hops = r.get("hop_count", 0)
            br = r.get("blast_radius", 0)
            click.echo(f"  Distance: {hops} hop{'s' if hops != 1 else ''} | Blast radius: {br} symbols")
            click.echo("")

        elif r["reachable"] == -1:
            click.echo(f"{pkg} -- NOT REACHABLE")
            click.echo("  No path from any entry point. Safe to deprioritize.")
            click.echo("")

        else:
            click.echo(f"{pkg} -- UNMATCHED")
            click.echo("  Package not found in codebase symbols.")
            click.echo("")


def _output_from_entry(ctx, results: list[dict], entry: str, json_mode: bool) -> None:
    """Output for --from entry point analysis."""
    if json_mode:
        vuln_out = []
        for r in results:
            vuln_out.append({
                "cve": r.get("cve_id"),
                "package": r.get("package_name"),
                "severity": r.get("severity"),
                "reachable": True,
                "path": r.get("path_names", []),
                "hops": r.get("hop_count", 0),
                "blast_radius": r.get("blast_radius", 0),
            })
        click.echo(to_json(json_envelope("vuln-reach",
            summary={
                "verdict": f"{len(results)} vulnerabilities reachable from {entry}",
                "total_vulns": len(results),
                "reachable_count": len(results),
                "critical_count": sum(1 for r in results if (r.get("severity") or "").lower() == "critical"),
            },
            vulnerabilities=vuln_out,
        )))
        return

    click.echo(f"VERDICT: {len(results)} vulnerabilities reachable from {entry}")
    click.echo("")
    for r in results:
        cve = r.get("cve_id") or r.get("package_name", "?")
        pkg = r.get("package_name", "?")
        sev = (r.get("severity") or "unknown").upper()
        click.echo(f"{cve} ({pkg}) -- {sev}")
        path_names = r.get("path_names", [])
        if path_names:
            click.echo("  Path: " + path_names[0])
            for name in path_names[1:]:
                click.echo(f"    -> {name}")
        hops = r.get("hop_count", 0)
        br = r.get("blast_radius", 0)
        click.echo(f"  Distance: {hops} hop{'s' if hops != 1 else ''} | Blast radius: {br} symbols")
        click.echo("")


def _output_cve(ctx, result: dict, json_mode: bool) -> None:
    """Output for --cve single CVE analysis."""
    if "error" in result:
        if json_mode:
            click.echo(to_json(json_envelope("vuln-reach",
                summary={"verdict": result["error"], "total_vulns": 0,
                          "reachable_count": 0, "critical_count": 0},
                vulnerabilities=[],
            )))
            return
        click.echo(f"VERDICT: {result['error']}")
        return

    reachable = result.get("reachable", False)
    sev = (result.get("severity") or "unknown").upper()
    cve = result.get("cve_id", "?")
    pkg = result.get("package_name", "?")

    if json_mode:
        click.echo(to_json(json_envelope("vuln-reach",
            summary={
                "verdict": f"{cve}: {'reachable' if reachable else 'not reachable'}",
                "total_vulns": 1,
                "reachable_count": 1 if reachable else 0,
                "critical_count": 1 if reachable and sev == "CRITICAL" else 0,
            },
            vulnerabilities=[{
                "cve": cve,
                "package": pkg,
                "severity": result.get("severity"),
                "reachable": reachable,
                "path": result.get("path_names", []),
                "hops": result.get("hop_count", 0),
                "blast_radius": result.get("blast_radius", 0),
            }],
        )))
        return

    click.echo(f"VERDICT: {cve}: {'reachable' if reachable else 'not reachable'}")
    click.echo("")
    click.echo(f"{cve} ({pkg}) -- {sev}")
    if reachable:
        path_names = result.get("path_names", [])
        if path_names:
            click.echo("  Path: " + path_names[0])
            for name in path_names[1:]:
                click.echo(f"    -> {name}")
        hops = result.get("hop_count", 0)
        br = result.get("blast_radius", 0)
        click.echo(f"  Distance: {hops} hop{'s' if hops != 1 else ''} | Blast radius: {br} symbols")
        entries = result.get("entry_points_reaching", [])
        if entries:
            click.echo(f"  Entry points reaching: {', '.join(entries)}")
    else:
        click.echo("  Not reachable from any entry point. Safe to deprioritize.")
