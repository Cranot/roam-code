"""Discover implicit contracts (invariants) for symbols.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because invariants outputs are invocation-scoped implicit-
contract enumerations (return-type stability, null-checks, ordering
constraints inferred from call-graph + usage patterns) — not per-
location code violations. The ``laws`` command ships SARIF when an
invariant is promoted to a checked law and a violation is observed;
the ``invariants`` discovery step itself returns candidate contracts
without source coordinates suitable for SARIF ``locations[]``. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation
plan + W1224-audit memo.
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import open_db
from roam.output.formatter import (
    abbrev_kind,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)
from roam.output.metric_definitions import (
    BREAKING_RISK_DEFINITION,
    CALLER_METRIC_RAW,
    INVARIANTS_DEFINITION,
)


def _discover_invariants(conn, sym_id, sym_info):
    """Discover invariants for a single symbol."""
    name = sym_info["name"]
    kind = sym_info["kind"]
    signature = sym_info.get("signature") or ""
    file_path = sym_info["file_path"]
    line_start = sym_info.get("line_start", 0)

    # 1. Caller analysis
    callers = conn.execute(
        """SELECT s.name, s.kind, f.path as file_path, e.kind as edge_kind
           FROM edges e
           JOIN symbols s ON e.source_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE e.target_id = ?""",
        (sym_id,),
    ).fetchall()

    caller_count = len(callers)
    caller_files = set(c["file_path"] for c in callers)
    file_spread = len(caller_files)

    # 2. Callee analysis (what this symbol depends on)
    callees = conn.execute(
        """SELECT s.name, s.kind, f.path as file_path
           FROM edges e
           JOIN symbols s ON e.target_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE e.source_id = ?""",
        (sym_id,),
    ).fetchall()

    # 3. Complexity metrics
    metrics_row = conn.execute(
        """SELECT cognitive_complexity, param_count, line_count, return_count
           FROM symbol_metrics WHERE symbol_id = ?""",
        (sym_id,),
    ).fetchone()

    param_count = metrics_row["param_count"] if metrics_row else 0

    # 4. Build invariants list
    invariants = []

    # Signature contract
    if signature:
        stability = "HIGH" if caller_count >= 10 else "MEDIUM" if caller_count >= 3 else "LOW"
        invariants.append(
            {
                "type": "SIGNATURE",
                "description": f"Signature: {signature}",
                "stability": stability,
                "detail": f"{caller_count} callers depend on this signature",
            }
        )

    # Parameter count contract
    if param_count > 0:
        invariants.append(
            {
                "type": "PARAMS",
                "description": f"Accepts {param_count} parameter(s)",
                "stability": "HIGH" if caller_count >= 5 else "MEDIUM",
                "detail": f"Changing parameter count would affect {caller_count} call sites",
            }
        )

    # File spread contract
    if file_spread >= 3:
        invariants.append(
            {
                "type": "USAGE_SPREAD",
                "description": f"Used across {file_spread} files",
                "stability": "HIGH",
                "detail": "Wide usage makes this a de-facto public API",
            }
        )

    # Dependency contract (what it calls)
    if len(callees) > 0:
        dep_names = [c["name"] for c in callees[:5]]
        invariants.append(
            {
                "type": "DEPENDENCIES",
                "description": f"Depends on {len(callees)} symbol(s): {', '.join(dep_names)}",
                "stability": "MEDIUM",
                "detail": "Removing a dependency may change behavior",
            }
        )

    # Breaking risk score
    breaking_risk = caller_count * max(file_spread, 1)
    risk_level = (
        "CRITICAL"
        if breaking_risk >= 50
        else "HIGH"
        if breaking_risk >= 20
        else "MEDIUM"
        if breaking_risk >= 5
        else "LOW"
    )

    return {
        "name": name,
        "kind": kind,
        "signature": signature,
        "file": file_path,
        "line": line_start,
        "caller_count": caller_count,
        "file_spread": file_spread,
        "callee_count": len(callees),
        "param_count": param_count,
        "invariants": invariants,
        "breaking_risk": breaking_risk,
        "risk_level": risk_level,
    }


@roam_capability(
    name="invariants",
    category="refactoring",
    summary="Discover implicit contracts for symbols",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("invariants")
@click.argument("target", required=False, default=None)
@click.option("--public-api", is_flag=True, help="Analyze all public/exported symbols")
@click.option("--breaking-risk", is_flag=True, help="Rank by breaking risk")
@click.option("--top", "top_n", default=20, type=int, help="Max symbols to show")
@click.pass_context
def invariants(ctx, target, public_api, breaking_risk, top_n):
    """Discover implicit contracts for symbols.

    Unlike ``check-rules`` (which evaluates explicit governance rules),
    this command auto-discovers implicit contracts from usage patterns and
    call frequency.

    Analyzes caller patterns, signatures, and usage spread to surface
    the invisible rules that must be preserved when modifying code.

    \b
    Invariant types:
      SIGNATURE     Function signature (params, return type)
      PARAMS        Parameter count and ordering
      USAGE_SPREAD  How widely the symbol is used across files
      DEPENDENCIES  What the symbol depends on
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        results = []
        # W1245 Pattern-2 variant-D: track the resolver tier of any
        # find_symbol() callsite reached on the symbol-target branch.
        # The two callsites below (file-fallback symbol lookup AND
        # bare-symbol mode) are mutually exclusive at runtime, so a
        # single tier + resolved_name pair is sufficient. ``symbol``
        # is the default when target is a file (file path resolution
        # doesn't walk the symbol fallback chain) or absent. Only
        # ``--public-api`` / ``--breaking-risk`` batch modes skip
        # disclosure (no per-name input to grade).
        resolution_tier = "symbol"
        resolved_target: str | None = None
        target_unresolved = False

        if target:
            # Detect whether target looks like a file path (has a known extension)
            target_norm = target.replace("\\", "/")
            _known_exts = {
                ".py",
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".go",
                ".java",
                ".rb",
                ".rs",
                ".c",
                ".cpp",
                ".h",
                ".hpp",
                ".php",
                ".cs",
                ".swift",
                ".kt",
                ".scala",
                ".m",
                ".r",
                ".lua",
                ".sh",
                ".sql",
            }
            target_suffix = Path(target_norm).suffix.lower()
            looks_like_file = target_suffix in _known_exts

            if looks_like_file:
                # File mode: try exact match, then suffix/LIKE match
                row = conn.execute("SELECT id FROM files WHERE path = ?", (target_norm,)).fetchone()
                if not row:
                    row = conn.execute("SELECT id FROM files WHERE path LIKE ?", (f"%{target_norm}",)).fetchone()

                if row:
                    syms = conn.execute(
                        """SELECT s.id, s.name, s.kind, s.signature, s.line_start,
                                  f.path as file_path
                           FROM symbols s JOIN files f ON s.file_id = f.id
                           WHERE s.file_id = ?
                           AND s.kind IN ('function', 'method', 'class')
                           ORDER BY s.line_start""",
                        (row["id"],),
                    ).fetchall()
                    for sym in syms:
                        results.append(_discover_invariants(conn, sym["id"], dict(sym)))
                else:
                    # Not found as file, fall back to symbol lookup
                    sym = find_symbol(conn, target)
                    if sym:
                        results.append(_discover_invariants(conn, sym["id"], dict(sym)))
                        resolution_tier = sym.get("_resolution_tier", "symbol")
                        resolved_target = sym.get("qualified_name") or sym["name"]
                    else:
                        target_unresolved = True
                        resolved_target = target
            else:
                # Symbol mode
                sym = find_symbol(conn, target)
                if sym:
                    results.append(_discover_invariants(conn, sym["id"], dict(sym)))
                    resolution_tier = sym.get("_resolution_tier", "symbol")
                    resolved_target = sym.get("qualified_name") or sym["name"]
                else:
                    target_unresolved = True
                    resolved_target = target

        elif public_api:
            # All exported symbols (functions/classes with 0 or more callers)
            syms = conn.execute(
                """SELECT s.id, s.name, s.kind, s.signature, s.line_start,
                          f.path as file_path
                   FROM symbols s JOIN files f ON s.file_id = f.id
                   WHERE s.kind IN ('function', 'method', 'class')
                   AND s.name NOT LIKE '\\_%' ESCAPE '\\'
                   ORDER BY s.name"""
            ).fetchall()
            for sym in syms[: top_n * 2]:  # over-fetch, then sort/trim
                results.append(_discover_invariants(conn, sym["id"], dict(sym)))

        elif breaking_risk:
            # All symbols ranked by breaking risk
            syms = conn.execute(
                """SELECT s.id, s.name, s.kind, s.signature, s.line_start,
                          f.path as file_path
                   FROM symbols s JOIN files f ON s.file_id = f.id
                   WHERE s.kind IN ('function', 'method', 'class')
                   ORDER BY s.name"""
            ).fetchall()
            for sym in syms:
                results.append(_discover_invariants(conn, sym["id"], dict(sym)))

        if not results and not target and not public_api and not breaking_risk:
            click.echo("Provide a TARGET symbol/file, or use --public-api or --breaking-risk.")
            raise SystemExit(1)

        # Sort by breaking risk if requested
        if breaking_risk:
            results.sort(key=lambda r: -r["breaking_risk"])

        results = results[:top_n]

        # Compute summary
        total_invariants = sum(len(r["invariants"]) for r in results)
        high_risk = sum(1 for r in results if r["risk_level"] in ("CRITICAL", "HIGH"))

        # W1245 Pattern-2 variant-D: build the disclosure block. When a
        # symbol-target callsite resolved unresolved, surface that
        # explicitly via ``resolution="unresolved"`` so the success
        # verdict on an empty results list isn't indistinguishable from
        # an exact-but-empty resolution. The batch modes (--public-api,
        # --breaking-risk) leave the disclosure as the no-op ``symbol``
        # default because there's no per-input grading to apply.
        if target_unresolved:
            disclosure_tier = "unresolved"
        else:
            disclosure_tier = resolution_tier
        # Only emit a disclosure when the symbol-target branch actually
        # walked the resolver (or attempted it). The batch modes resolve
        # everything by direct DB query and don't have a tier to report.
        emit_disclosure = bool(target)
        resolution_block = resolution_disclosure(disclosure_tier, target=resolved_target) if emit_disclosure else {}
        fuzzy_suffix = " [fuzzy resolution]" if disclosure_tier == "fuzzy" else ""

        if not results:
            verdict = f"No symbols found for: {target or 'query'}"
        elif len(results) == 1:
            r = results[0]
            verdict = (
                f"{len(r['invariants'])} invariants for {r['name']}"
                f" ({r['caller_count']} callers, risk: {r['risk_level']})"
                f"{fuzzy_suffix}"
            )
        else:
            verdict = (
                f"{total_invariants} invariants across {len(results)} symbols, {high_risk} high-risk{fuzzy_suffix}"
            )

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "invariants",
                        summary={
                            "verdict": verdict,
                            "symbols_analyzed": len(results),
                            "total_invariants": total_invariants,
                            "high_risk_count": high_risk,
                            # W331: name what an "invariant" actually is
                            # here (NOT a verified property; a usage-
                            # derived heuristic contract) and how the
                            # breaking_risk score is computed.
                            "invariants_definition": INVARIANTS_DEFINITION,
                            "breaking_risk_definition": BREAKING_RISK_DEFINITION,
                            # W335: per-symbol payload carries
                            # caller_count = len(callers) — same
                            # raw_edge_rows shape as cmd_uses /
                            # cmd_context. Label it so downstream
                            # consumers know it counts per-file edge
                            # rows (with multiplicity), not distinct
                            # upstream symbols (which would be
                            # direct_in_degree).
                            "caller_metric_definition": CALLER_METRIC_RAW,
                            **resolution_block,
                        },
                        symbols=results,
                        **resolution_block,
                    )
                )
            )
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        for r in results:
            click.echo(f"CONTRACT: {r['name']} ({abbrev_kind(r['kind'])}, {loc(r['file'], r['line'])})")
            if r["signature"]:
                click.echo(f"  Signature: {r['signature']}")
            click.echo(f"  Callers: {r['caller_count']} across {r['file_spread']} files")
            click.echo(f"  Breaking risk: {r['risk_level']} (score: {r['breaking_risk']})")
            click.echo()

            if r["invariants"]:
                click.echo("  INVARIANTS:")
                for i, inv in enumerate(r["invariants"], 1):
                    click.echo(f"    {i}. [{inv['type']}] {inv['description']}")
                    click.echo(f"       Stability: {inv['stability']} -- {inv['detail']}")
                click.echo()
