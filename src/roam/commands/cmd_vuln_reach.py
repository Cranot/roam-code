"""Query reachability of ingested vulnerabilities through the call graph.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because vuln-reach outputs are invocation-scoped reachability
aggregates (vulnerable paths from entry points to ingested CVE-tagged
symbols, ranked by depth / fan-in) — not per-location code
violations in source-coordinate form. The underlying CVE-to-symbol
mapping comes from ``roam vuln-map`` (the producer); SARIF exposure
for vulnerability findings is reserved for ecosystem scanners
(npm/pip/trivy/osv) whose native output already targets SARIF
consumers. See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH
propagation plan + W1224-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output._severity import severity_rank
from roam.output.formatter import json_envelope, to_json


@roam_capability(
    name="vuln-reach",
    category="reports",
    summary="Query reachability of ingested vulnerabilities through the call graph",
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
@click.command()
@click.option(
    "--from",
    "from_entry",
    default=None,
    help="Check reachability from a specific entry point symbol",
)
@click.option("--cve", "cve_id", default=None, help="Analyze a specific CVE ID")
@click.pass_context
def vuln_reach(ctx, from_entry, cve_id):
    """Query reachability of ingested vulnerabilities through the call graph.

    Analyzes whether vulnerabilities are reachable from entry points (symbols
    with no incoming calls). Unlike ``vulns`` (which lists known vulnerabilities
    with optional ``--reachable-only`` filtering) and ``vuln-map`` (which
    ingests vulnerability data), this command traces call-graph paths from
    vulnerable packages to entry points, showing hop distance and blast radius
    per CVE.

    \b
    Examples:
        roam vuln-reach                          # all vulns with reachability
        roam vuln-reach --from handle_request    # from specific entry point
        roam vuln-reach --cve CVE-2024-1234      # specific vulnerability

    See also ``vuln-map`` (ingest vulnerability data), ``vulns`` (list
    known vulnerabilities), and ``taint`` (source-to-sink reach).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    # W607-AU -- substrate-boundary plumbing for the call-graph reachability
    # projection leg of the W805 cross-artifact-consistency family
    # (cmd_supply_chain W607-AK is the consumer/projection sibling,
    # cmd_sbom W607-AM is the SBOM emit producer sibling, cmd_vulns
    # W607-AQ is the VEX projection / vuln-ingest sibling). Prior to W607-AU
    # a raise inside any of the substrate boundaries -- ensure_vuln_table /
    # build_symbol_graph / query_vuln_count / analyze_reachability /
    # reach_from_entry / reach_for_cve / serialize_envelope -- crashed the
    # whole vuln-reach invocation wholesale. Each is wrapped via
    # ``_run_check_au`` so a raise becomes a structured
    # ``vuln_reach_<phase>_failed:<exc_class>:<detail>`` marker on
    # ``_w607au_warnings_out`` -- the envelope still emits cleanly with
    # whatever signal the remaining substrates produced.
    #
    # Marker prefix discipline: every W607-AU substrate marker uses the
    # canonical ``vuln_reach_<phase>_failed:<exc_class>:<detail>`` shape.
    # cmd_vuln_reach has NO pre-existing warnings_out channel -- W607-AU
    # is FRESH: the accumulator-based markers become the canonical
    # ``summary.warnings_out`` field outright.
    _w607au_warnings_out: list[str] = []

    def _run_check_au(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AU marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``vuln_reach_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607au_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607au_warnings_out.append(f"vuln_reach_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-CL -- ADDITIVE aggregation-phase plumbing on top of the W607-AU
    # substrate-CALL markers. W607-AU already wrapped the 7 substrate-helper
    # boundaries on the build path (ensure_vuln_table / build_symbol_graph /
    # query_vuln_count / analyze_reachability / reach_from_entry /
    # reach_for_cve / serialize_envelope); W607-CL extends marker coverage
    # to the AGGREGATION-PHASE boundaries that W607-AU left unguarded:
    #
    #   - ``compute_predicate``    -- per-field extraction of the metric
    #                                 fields (total / reachable_count /
    #                                 critical_count / partial_success_state)
    #                                 used to compose the verdict string +
    #                                 envelope.
    #   - ``compute_verdict``      -- verdict-string assembly based on the
    #                                 reachable + critical counts. Floor to
    #                                 a literal "vuln-reach completed"
    #                                 string per LAW 6 (standalone-parse)
    #                                 + W978 first-hypothesis discipline
    #                                 (no re-interpolation of the same
    #                                 values that just raised).
    #   - ``build_envelope``       -- ``json_envelope("vuln-reach", ...)``
    #                                 projection (downstream contract
    #                                 changes / shape regressions). Phase
    #                                 name distinct from W607-AU's
    #                                 existing ``serialize_envelope``
    #                                 (which wraps ``to_json`` instead).
    #
    # cmd_vuln_reach is the call-graph reachability projection sibling of
    # cmd_vulns. Closes the SECURITY-REACHABILITY TRIAD at the aggregation-
    # phase layer alongside cmd_vulns (W607-CH) and cmd_taint (W607-CJ,
    # when landed). Per W826 HIGH-SEV bug pin (cmd_taint silent-SAFE on
    # empty corpus -- security-critical Pattern-2): cmd_vuln_reach must
    # NEVER silently emit a SAFE verdict on the aggregation-phase boundary
    # raising; the marker + partial_success disclosure preserves the
    # W823 empty-corpus security-axis discipline.
    #
    # Marker family ``vuln_reach_*`` -- same family as W607-AU (additive,
    # not a separate prefix). Empty bucket -> byte-identical envelope on
    # the success path.
    #
    # No ``auto_log`` phase: cmd_vuln_reach has no active-run ledger write
    # at present, so the W607-BZ 4-phase set drops to 3 phases here
    # (compute_predicate / compute_verdict / build_envelope). Same marker
    # shape contract, narrower phase set.
    _w607cl_warnings_out: list[str] = []

    def _run_check_cl(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CL marker emission.

        Mirror of ``_run_check_au`` shape (same ``vuln_reach_<phase>_failed:``
        marker family) but writes into ``_w607cl_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cl_warnings_out.append(f"vuln_reach_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    from roam.graph.builder import build_symbol_graph
    from roam.security.vuln_reach import (
        analyze_reachability,
        reach_for_cve,
        reach_from_entry,
    )
    from roam.security.vuln_store import ensure_vuln_table

    with open_db(readonly=False) as conn:
        _run_check_au("ensure_vuln_table", ensure_vuln_table, conn, default=None)
        G = _run_check_au("build_symbol_graph", build_symbol_graph, conn, default=None)

        # Check if any vulnerabilities exist
        vuln_count_row = _run_check_au(
            "query_vuln_count",
            lambda c: c.execute("SELECT COUNT(*) FROM vulnerabilities").fetchone(),
            conn,
            default=None,
        )
        vuln_count = vuln_count_row[0] if vuln_count_row else 0
        if vuln_count == 0:
            if json_mode:
                _combined_warnings_out = list(_w607au_warnings_out) + list(_w607cl_warnings_out)
                summary = {
                    "verdict": "No vulnerabilities ingested. Run vuln-map first.",
                    "total_vulns": 0,
                    "reachable_count": 0,
                    "critical_count": 0,
                }
                if _combined_warnings_out:
                    summary["warnings_out"] = list(_combined_warnings_out)
                    summary["partial_success"] = True
                envelope_kwargs: dict = {
                    "summary": summary,
                    "vulnerabilities": [],
                }
                if _combined_warnings_out:
                    envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

                # W607-CL -- build_envelope boundary on the no-vulns
                # short-circuit. Floor to a parseable stub so consumers
                # still see the marker + canonical command name.
                _envelope_floor: dict = {
                    "command": "vuln-reach",
                    "schema_version": "1.0.0",
                    "summary": {
                        "verdict": "vuln-reach completed",
                        "partial_success": True,
                        "warnings_out": list(_combined_warnings_out),
                    },
                    "warnings_out": list(_combined_warnings_out),
                }
                envelope = _run_check_cl(
                    "build_envelope",
                    json_envelope,
                    "vuln-reach",
                    default=_envelope_floor,
                    **envelope_kwargs,
                )
                # W607-CL -- if build_envelope raised AFTER the combined
                # bucket was snapshotted, rebuild the floor stub's
                # warnings_out so the new marker reaches the JSON output.
                if envelope is _envelope_floor and _w607cl_warnings_out:
                    _combined_warnings_out = list(_w607au_warnings_out) + list(_w607cl_warnings_out)
                    _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
                    _envelope_floor["warnings_out"] = list(_combined_warnings_out)
                    envelope = _envelope_floor

                output_text = _run_check_au("serialize_envelope", to_json, envelope, default="{}")
                if output_text is None:
                    output_text = "{}"
                click.echo(output_text)
                return
            click.echo("VERDICT: No vulnerabilities ingested. Run vuln-map first.")
            return

        # Dispatch based on flags
        if cve_id:
            result = _run_check_au("reach_for_cve", reach_for_cve, conn, G, cve_id, default={})
            _output_cve(
                ctx,
                result,
                json_mode,
                _run_check_au=_run_check_au,
                _w607au_warnings_out=_w607au_warnings_out,
                _run_check_cl=_run_check_cl,
                _w607cl_warnings_out=_w607cl_warnings_out,
            )
            return

        if from_entry:
            results = _run_check_au(
                "reach_from_entry",
                reach_from_entry,
                conn,
                G,
                from_entry,
                default=[],
            )
            _output_from_entry(
                ctx,
                results,
                from_entry,
                json_mode,
                _run_check_au=_run_check_au,
                _w607au_warnings_out=_w607au_warnings_out,
                _run_check_cl=_run_check_cl,
                _w607cl_warnings_out=_w607cl_warnings_out,
            )
            return

        # Default: analyze all
        results = _run_check_au(
            "analyze_reachability",
            analyze_reachability,
            conn,
            G,
            default=[],
        )
        _output_all(
            ctx,
            results,
            json_mode,
            _run_check_au=_run_check_au,
            _w607au_warnings_out=_w607au_warnings_out,
            _run_check_cl=_run_check_cl,
            _w607cl_warnings_out=_w607cl_warnings_out,
        )


def _output_all(
    ctx,
    results: list[dict],
    json_mode: bool,
    _run_check_au=None,
    _w607au_warnings_out=None,
    _run_check_cl=None,
    _w607cl_warnings_out=None,
) -> None:
    """Output for full reachability analysis."""
    # Fallback no-op wrap for callers that bypass the W607-AU closure.
    # In _output_all this only protects the final to_json() boundary:
    # expected serialization faults become markers, unrelated bugs propagate.
    if _run_check_au is None or _w607au_warnings_out is None:
        _w607au_warnings_out = []

        def _run_check_au(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (TypeError, ValueError, RecursionError) as exc:
                _w607au_warnings_out.append(f"vuln_reach_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

    # W607-CL fallback no-op wrap when not invoked from the click closure.
    if _run_check_cl is None or _w607cl_warnings_out is None:
        _w607cl_warnings_out = []

        def _run_check_cl(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                _w607cl_warnings_out.append(f"vuln_reach_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

    # W607-CL -- compute_predicate boundary. Wraps the per-field extraction
    # of (total / reachable_count / critical_count) so a future schema
    # refactor (e.g. ``reachable`` no longer being an int) surfaces a
    # marker rather than crashing the verdict assembly.
    def _compute_predicate_fields(results_local: list[dict]) -> dict:
        reachable_local = [r for r in results_local if r["reachable"] == 1]
        critical_local = [r for r in reachable_local if (r.get("severity") or "").lower() == "critical"]
        return {
            "total": len(results_local),
            "reachable_count": len(reachable_local),
            "critical_count": len(critical_local),
        }

    _pred_fields = _run_check_cl(
        "compute_predicate",
        _compute_predicate_fields,
        results,
        default={
            "total": 0,
            "reachable_count": 0,
            "critical_count": 0,
        },
    )

    # W607-CL -- compute_verdict boundary. Wraps the verdict-string
    # assembly so a downstream f-string refactor (e.g. a non-int count
    # raising on f-string interpolation) surfaces a marker rather than
    # crashing the envelope. Floor must NOT re-interpolate the same
    # values that tripped the closure (W978 first-hypothesis discipline:
    # a __format__-raising sentinel under test would re-raise inside the
    # default f-string). Use a literal ``"vuln-reach completed"`` floor
    # instead (LAW 6 still holds: the line works standalone).
    def _build_verdict_str(fields: dict) -> str:
        reachable_count_local = fields["reachable_count"]
        critical_count_local = fields["critical_count"]
        out = f"{reachable_count_local} reachable vulnerabilities"
        if critical_count_local > 0:
            plural = "s" if critical_count_local != 1 else ""
            out += f", {critical_count_local} critical path{plural}"
        return out

    verdict = _run_check_cl(
        "compute_verdict",
        _build_verdict_str,
        _pred_fields,
        default="vuln-reach completed",
    )

    if json_mode:
        vuln_out = []
        for r in results:
            vuln_out.append(
                {
                    "cve": r.get("cve_id"),
                    "package": r.get("package_name"),
                    "severity": r.get("severity"),
                    "reachable": r["reachable"] == 1,
                    "path": r.get("path_names", []),
                    "hops": r.get("hop_count", 0),
                    "blast_radius": r.get("blast_radius", 0),
                }
            )
        _combined_warnings_out = list(_w607au_warnings_out) + list(_w607cl_warnings_out)
        summary = {
            "verdict": verdict,
            "total_vulns": _pred_fields["total"],
            "reachable_count": _pred_fields["reachable_count"],
            "critical_count": _pred_fields["critical_count"],
        }
        if _combined_warnings_out:
            summary["warnings_out"] = list(_combined_warnings_out)
            summary["partial_success"] = True
        envelope_kwargs: dict = {
            "summary": summary,
            "vulnerabilities": vuln_out,
        }
        if _combined_warnings_out:
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

        # W607-CL -- build_envelope boundary. Wraps the
        # ``json_envelope("vuln-reach", ...)`` projection. A downstream
        # schema-shape refactor that breaks the envelope helper would
        # otherwise crash AFTER all substrate + aggregation signals were
        # already gathered. Floor to a minimal envelope stub so consumers
        # still receive a parseable JSON object with the marker attached
        # + the canonical command name. Phase name distinct from W607-AU's
        # existing ``serialize_envelope`` (which wraps ``to_json`` instead).
        _envelope_floor: dict = {
            "command": "vuln-reach",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        envelope = _run_check_cl(
            "build_envelope",
            json_envelope,
            "vuln-reach",
            default=_envelope_floor,
            **envelope_kwargs,
        )
        # W607-CL -- if ``build_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``vuln_reach_build_envelope_failed:`` marker was appended to
        # ``_w607cl_warnings_out`` and the floor stub carries only the
        # pre-raise combined list. Rebuild the floor stub's warnings_out
        # so the new marker reaches the JSON output.
        if envelope is _envelope_floor and _w607cl_warnings_out:
            _combined_warnings_out = list(_w607au_warnings_out) + list(_w607cl_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            envelope = _envelope_floor

        output_text = _run_check_au("serialize_envelope", to_json, envelope, default="{}")
        if output_text is None:
            output_text = "{}"
        click.echo(output_text)
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")

    # Show reachable first, sorted by severity (W564 canonical rank,
    # negated so critical/high/medium sort ahead of low/unknown).
    sorted_results = sorted(
        results,
        key=lambda r: (
            0 if r["reachable"] == 1 else 1,
            -severity_rank(r.get("severity") or "unknown"),
        ),
    )

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


def _output_from_entry(
    ctx,
    results: list[dict],
    entry: str,
    json_mode: bool,
    _run_check_au=None,
    _w607au_warnings_out=None,
    _run_check_cl=None,
    _w607cl_warnings_out=None,
) -> None:
    """Output for --from entry point analysis."""
    if _run_check_au is None or _w607au_warnings_out is None:
        _w607au_warnings_out = []

        def _run_check_au(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                _w607au_warnings_out.append(f"vuln_reach_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

    # W607-CL fallback no-op wrap when not invoked from the click closure.
    if _run_check_cl is None or _w607cl_warnings_out is None:
        _w607cl_warnings_out = []

        def _run_check_cl(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                _w607cl_warnings_out.append(f"vuln_reach_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

    # W607-CL -- compute_predicate boundary on the from-entry path.
    def _compute_predicate_fields(results_local: list[dict], entry_local: str) -> dict:
        total_local = len(results_local)
        critical_local = sum(1 for r in results_local if (r.get("severity") or "").lower() == "critical")
        return {
            "total": total_local,
            "reachable_count": total_local,
            "critical_count": critical_local,
            "entry": entry_local,
        }

    _pred_fields = _run_check_cl(
        "compute_predicate",
        _compute_predicate_fields,
        results,
        entry,
        default={
            "total": 0,
            "reachable_count": 0,
            "critical_count": 0,
            "entry": entry,
        },
    )

    # W607-CL -- compute_verdict boundary on the from-entry path.
    def _build_verdict_str(fields: dict) -> str:
        total_local = fields["total"]
        entry_local = fields["entry"]
        return f"{total_local} vulnerabilities reachable from {entry_local}"

    verdict = _run_check_cl(
        "compute_verdict",
        _build_verdict_str,
        _pred_fields,
        default="vuln-reach completed",
    )

    if json_mode:
        vuln_out = []
        for r in results:
            vuln_out.append(
                {
                    "cve": r.get("cve_id"),
                    "package": r.get("package_name"),
                    "severity": r.get("severity"),
                    "reachable": True,
                    "path": r.get("path_names", []),
                    "hops": r.get("hop_count", 0),
                    "blast_radius": r.get("blast_radius", 0),
                }
            )
        _combined_warnings_out = list(_w607au_warnings_out) + list(_w607cl_warnings_out)
        summary = {
            "verdict": verdict,
            "total_vulns": _pred_fields["total"],
            "reachable_count": _pred_fields["reachable_count"],
            "critical_count": _pred_fields["critical_count"],
        }
        if _combined_warnings_out:
            summary["warnings_out"] = list(_combined_warnings_out)
            summary["partial_success"] = True
        envelope_kwargs: dict = {
            "summary": summary,
            "vulnerabilities": vuln_out,
        }
        if _combined_warnings_out:
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

        # W607-CL -- build_envelope boundary on the from-entry path.
        _envelope_floor: dict = {
            "command": "vuln-reach",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        envelope = _run_check_cl(
            "build_envelope",
            json_envelope,
            "vuln-reach",
            default=_envelope_floor,
            **envelope_kwargs,
        )
        if envelope is _envelope_floor and _w607cl_warnings_out:
            _combined_warnings_out = list(_w607au_warnings_out) + list(_w607cl_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            envelope = _envelope_floor

        output_text = _run_check_au("serialize_envelope", to_json, envelope, default="{}")
        if output_text is None:
            output_text = "{}"
        click.echo(output_text)
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


def _output_cve(
    ctx,
    result: dict,
    json_mode: bool,
    _run_check_au=None,
    _w607au_warnings_out=None,
    _run_check_cl=None,
    _w607cl_warnings_out=None,
) -> None:
    """Output for --cve single CVE analysis."""
    if _run_check_au is None or _w607au_warnings_out is None:
        _w607au_warnings_out = []

        def _run_check_au(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                _w607au_warnings_out.append(f"vuln_reach_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

    # W607-CL fallback no-op wrap when not invoked from the click closure.
    if _run_check_cl is None or _w607cl_warnings_out is None:
        _w607cl_warnings_out = []

        def _run_check_cl(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                _w607cl_warnings_out.append(f"vuln_reach_{phase}_failed:{type(exc).__name__}:{exc}")
                return default

    if "error" in result:
        if json_mode:
            _combined_warnings_out = list(_w607au_warnings_out) + list(_w607cl_warnings_out)
            summary = {
                "verdict": result["error"],
                "total_vulns": 0,
                "reachable_count": 0,
                "critical_count": 0,
            }
            if _combined_warnings_out:
                summary["warnings_out"] = list(_combined_warnings_out)
                summary["partial_success"] = True
            envelope_kwargs: dict = {
                "summary": summary,
                "vulnerabilities": [],
            }
            if _combined_warnings_out:
                envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

            # W607-CL -- build_envelope boundary on the CVE-error path.
            _envelope_floor: dict = {
                "command": "vuln-reach",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": "vuln-reach completed",
                    "partial_success": True,
                    "warnings_out": list(_combined_warnings_out),
                },
                "warnings_out": list(_combined_warnings_out),
            }
            envelope = _run_check_cl(
                "build_envelope",
                json_envelope,
                "vuln-reach",
                default=_envelope_floor,
                **envelope_kwargs,
            )
            if envelope is _envelope_floor and _w607cl_warnings_out:
                _combined_warnings_out = list(_w607au_warnings_out) + list(_w607cl_warnings_out)
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
                _envelope_floor["warnings_out"] = list(_combined_warnings_out)
                envelope = _envelope_floor

            output_text = _run_check_au("serialize_envelope", to_json, envelope, default="{}")
            if output_text is None:
                output_text = "{}"
            click.echo(output_text)
            return
        click.echo(f"VERDICT: {result['error']}")
        return

    reachable = result.get("reachable", False)
    sev = (result.get("severity") or "unknown").upper()
    cve = result.get("cve_id", "?")
    pkg = result.get("package_name", "?")

    # W607-CL -- compute_predicate boundary on the CVE-result path.
    def _compute_predicate_fields(
        reachable_local: bool,
        sev_local: str,
        cve_local: str,
    ) -> dict:
        return {
            "total": 1,
            "reachable_count": 1 if reachable_local else 0,
            "critical_count": 1 if reachable_local and sev_local == "CRITICAL" else 0,
            "cve": cve_local,
            "reachable_bool": bool(reachable_local),
        }

    _pred_fields = _run_check_cl(
        "compute_predicate",
        _compute_predicate_fields,
        reachable,
        sev,
        cve,
        default={
            "total": 1,
            "reachable_count": 0,
            "critical_count": 0,
            "cve": cve,
            "reachable_bool": False,
        },
    )

    # W607-CL -- compute_verdict boundary on the CVE-result path.
    def _build_verdict_str(fields: dict) -> str:
        cve_local = fields["cve"]
        reachable_bool = fields["reachable_bool"]
        state_word = "reachable" if reachable_bool else "not reachable"
        return f"{cve_local}: {state_word}"

    verdict = _run_check_cl(
        "compute_verdict",
        _build_verdict_str,
        _pred_fields,
        default="vuln-reach completed",
    )

    if json_mode:
        _combined_warnings_out = list(_w607au_warnings_out) + list(_w607cl_warnings_out)
        summary = {
            "verdict": verdict,
            "total_vulns": _pred_fields["total"],
            "reachable_count": _pred_fields["reachable_count"],
            "critical_count": _pred_fields["critical_count"],
        }
        if _combined_warnings_out:
            summary["warnings_out"] = list(_combined_warnings_out)
            summary["partial_success"] = True
        envelope_kwargs: dict = {
            "summary": summary,
            "vulnerabilities": [
                {
                    "cve": cve,
                    "package": pkg,
                    "severity": result.get("severity"),
                    "reachable": reachable,
                    "path": result.get("path_names", []),
                    "hops": result.get("hop_count", 0),
                    "blast_radius": result.get("blast_radius", 0),
                }
            ],
        }
        if _combined_warnings_out:
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

        # W607-CL -- build_envelope boundary on the CVE-result path.
        _envelope_floor: dict = {
            "command": "vuln-reach",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        envelope = _run_check_cl(
            "build_envelope",
            json_envelope,
            "vuln-reach",
            default=_envelope_floor,
            **envelope_kwargs,
        )
        if envelope is _envelope_floor and _w607cl_warnings_out:
            _combined_warnings_out = list(_w607au_warnings_out) + list(_w607cl_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            envelope = _envelope_floor

        output_text = _run_check_au("serialize_envelope", to_json, envelope, default="{}")
        if output_text is None:
            output_text = "{}"
        click.echo(output_text)
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
