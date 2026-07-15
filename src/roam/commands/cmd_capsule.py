"""Export the structural graph as a JSON capsule (no function bodies).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because capsule outputs are JSON graph capsules — not per-location
violations. SARIF is reserved for findings with file:line coordinates;
capsule's primary deliverable is the JSON graph capsule. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket C propagation plan +
W1148 audit memo.
"""

from __future__ import annotations

import hashlib
import json as _json
from datetime import datetime, timezone
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json
from roam.output.metric_definitions import COGNITIVE_COMPLEXITY_DEFINITION

# ---------------------------------------------------------------------------
# Path redaction helper
# ---------------------------------------------------------------------------


def _redact_path(path: str) -> str:
    """Hash each path component to anonymize file paths.

    The same path always maps to the same redacted name so graph edges
    remain consistent within a single capsule.
    """
    parts = path.replace("\\", "/").split("/")
    return "/".join(hashlib.sha256(p.encode()).hexdigest()[:6] for p in parts)


# ---------------------------------------------------------------------------
# Data-gathering helpers
# ---------------------------------------------------------------------------


def _gather_topology(conn) -> dict:
    """Return counts and language list for the topology section."""
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    lang_rows = conn.execute(
        "SELECT DISTINCT language FROM files WHERE language IS NOT NULL ORDER BY language"
    ).fetchall()
    languages = [r[0] for r in lang_rows if r[0]]

    return {
        "files": files,
        "symbols": symbols,
        "edges": edges,
        "languages": languages,
    }


def _gather_symbols(conn, redact_paths: bool, no_signatures: bool) -> list[dict]:
    """Return symbol list with optional path redaction and signature omission."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path, s.line_start, "
        "s.signature, s.visibility, s.is_exported "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "ORDER BY f.path, s.line_start"
    ).fetchall()

    # Build fan-in / fan-out lookup in one pass each to avoid N+1 queries
    fan_in_rows = conn.execute("SELECT target_id, COUNT(*) as cnt FROM edges GROUP BY target_id").fetchall()
    fan_in = {r[0]: r[1] for r in fan_in_rows}

    fan_out_rows = conn.execute("SELECT source_id, COUNT(*) as cnt FROM edges GROUP BY source_id").fetchall()
    fan_out = {r[0]: r[1] for r in fan_out_rows}

    # Build complexity lookup
    metric_rows = conn.execute("SELECT symbol_id, cognitive_complexity, halstead_volume FROM symbol_metrics").fetchall()
    metrics_map = {r[0]: r for r in metric_rows}

    result = []
    for r in rows:
        sid = r[0]  # r["id"] — use positional access since column names may vary

        file_path = r[4]  # f.path
        if redact_paths:
            file_path = _redact_path(file_path)

        # Signature
        sig = r[6]  # s.signature
        if no_signatures:
            sig = None

        # Metrics
        m = metrics_map.get(sid)
        metrics_dict: dict = {
            "cognitive_complexity": (m[1] if m else None),
            "fan_in": fan_in.get(sid, 0),
            "fan_out": fan_out.get(sid, 0),
        }
        if m and m[2] is not None:
            metrics_dict["halstead_volume"] = m[2]

        entry: dict = {
            "id": sid,
            "name": r[1],  # s.name
            "kind": r[3],  # s.kind
            "file": file_path,
            "line": r[5],  # s.line_start
            "metrics": metrics_dict,
        }
        if sig is not None:
            entry["signature"] = sig

        result.append(entry)

    return result


def _gather_edges(conn) -> list[dict]:
    """Return all symbol-level edges."""
    rows = conn.execute("SELECT source_id, target_id, kind FROM edges ORDER BY source_id").fetchall()
    return [{"source": r[0], "target": r[1], "kind": r[2]} for r in rows]


def _gather_clusters(conn, redact_paths: bool = False) -> list[dict]:
    """Return clusters with id, label and member count.

    Cluster labels are derived from a file/directory path. Under
    ``--redact-paths`` they MUST be hashed with the same per-component scheme
    as symbol ``file`` fields — otherwise the "zero-source" capsule leaks the
    directory tree verbatim via ``clusters[].label`` while every other path is
    redacted.
    """
    rows = conn.execute(
        "SELECT cluster_id, cluster_label, COUNT(*) as size "
        "FROM clusters GROUP BY cluster_id, cluster_label "
        "ORDER BY cluster_id"
    ).fetchall()
    return [
        {
            "id": r[0],
            "label": _redact_path(r[1]) if (redact_paths and r[1]) else r[1],
            "size": r[2],
        }
        for r in rows
    ]


def _gather_health(conn) -> dict:
    """Collect health metrics via metrics_history.collect_metrics."""
    from roam.commands.metrics_history import collect_metrics

    m = collect_metrics(conn)
    return {
        "score": m.get("health_score", 0),
        "cycles": m.get("cycles", 0),
        "god_components": m.get("god_components", 0),
        "layer_violations": m.get("layer_violations", 0),
        "bottlenecks": m.get("bottlenecks", 0),
        "dead_exports": m.get("dead_exports", 0),
        "tangle_ratio": m.get("tangle_ratio", 0.0),
        "avg_complexity": m.get("avg_complexity", 0.0),
    }


# ---------------------------------------------------------------------------
# Capsule builder
# ---------------------------------------------------------------------------


def _build_capsule(
    conn,
    redact_paths: bool,
    no_signatures: bool,
    *,
    run_check=None,
) -> dict:
    """Assemble the full capsule dict from DB data.

    ``run_check`` is the W607-BD substrate wrapper injected by the click
    handler so each individual gather call (topology / symbols / edges /
    clusters / health) emits a marker on raise rather than crashing the
    whole capsule build. When ``None`` (e.g. unit tests that call this
    builder directly), helpers run unwrapped — the historical contract.
    """
    from roam import __version__

    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if run_check is None:
        topology = _gather_topology(conn)
        symbols = _gather_symbols(conn, redact_paths=redact_paths, no_signatures=no_signatures)
        edges = _gather_edges(conn)
        clusters = _gather_clusters(conn, redact_paths=redact_paths)
        health = _gather_health(conn)
    else:
        topology = run_check(
            "gather_topology",
            _gather_topology,
            conn,
            default={"files": 0, "symbols": 0, "edges": 0, "languages": []},
        )
        symbols = run_check(
            "gather_symbols",
            _gather_symbols,
            conn,
            redact_paths,
            no_signatures,
            default=[],
        )
        edges = run_check(
            "gather_edges",
            _gather_edges,
            conn,
            default=[],
        )
        clusters = run_check(
            "gather_clusters",
            _gather_clusters,
            conn,
            redact_paths,
            default=[],
        )
        health = run_check(
            "gather_health",
            _gather_health,
            conn,
            default={
                "score": 0,
                "cycles": 0,
                "god_components": 0,
                "layer_violations": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "tangle_ratio": 0.0,
                "avg_complexity": 0.0,
            },
        )

    return {
        "capsule": {
            "version": "1.0",
            "generated": ts,
            "tool_version": __version__,
            "redacted": redact_paths,
            "no_signatures": no_signatures,
        },
        "topology": topology,
        "symbols": symbols,
        "edges": edges,
        "clusters": clusters,
        "health": health,
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="capsule",
    category="reports",
    summary="Export the structural graph as a portable JSON capsule",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("capsule")
@click.option(
    "--redact-paths",
    is_flag=True,
    default=False,
    help="Anonymize file paths by hashing each path component.",
)
@click.option(
    "--no-signatures",
    is_flag=True,
    default=False,
    help="Omit parameter signatures from symbol entries.",
)
@click.option(
    "--output",
    default=None,
    metavar="FILE",
    help="Write the full JSON capsule to <FILE> instead of stdout.",
)
@click.pass_context
def capsule(ctx, redact_paths, no_signatures, output):
    """Export the structural graph as a portable JSON capsule.

    Unlike ``context`` (which provides targeted context for one symbol),
    this command exports the entire structural graph as a portable JSON
    document.

    The capsule contains symbol signatures, call edges, cluster assignments,
    and health metrics — but never function bodies. Useful for external
    architectural review without sharing source code.

    When --output is given, the full capsule JSON is always written to the
    file regardless of --json mode, and a summary is printed to stdout.

    \b
    Examples:
      roam capsule
      roam capsule --output graph.json
      roam capsule --redact-paths --no-signatures
      roam --json capsule

    See also ``context`` (per-symbol read order), ``graph-export``
    (graph-export to GraphML/DOT/JSON), and ``attest`` (signed
    in-toto attestation of the capsule contents).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    # W607-BD -- substrate-boundary plumbing for the graph-capsule
    # exporter. Prior to W607-BD a raise inside any substrate helper
    # (gather_topology, gather_symbols, gather_edges, gather_clusters,
    # gather_health, atomic_write_capsule, serialize_envelope) crashed
    # the whole capsule invocation wholesale. Each is wrapped via
    # ``_run_check_bd`` so a raise becomes a structured
    # ``capsule_<phase>_failed:<exc_class>:<detail>`` marker on
    # ``_w607bd_warnings_out`` -- the envelope still emits cleanly with
    # whatever signal the remaining substrates produced.
    #
    # Marker prefix discipline: every W607-BD substrate marker uses the
    # canonical ``capsule_<phase>_failed:<exc_class>:<detail>`` shape.
    # cmd_capsule has NO pre-existing warnings_out channel -- W607-BD is
    # FRESH: the accumulator-based markers become the canonical
    # ``summary.warnings_out`` field outright.
    _w607bd_warnings_out: list[str] = []

    def _run_check_bd(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BD marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``capsule_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607bd_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bd_warnings_out.append(f"capsule_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-DK -- substrate-CALL-layer plumbing for cmd_capsule.
    # cmd_capsule is the graph-EXPORT companion to cmd_fingerprint
    # (topology-HASH); together they close the architecture-export
    # 2-way at the substrate-CALL layer (cmd_fingerprint = W607-DH,
    # cmd_capsule = W607-DK). The W607-BD wave wrapped the INNER
    # gather helpers passed into ``_build_capsule(run_check=...)``;
    # W607-DK wraps the OUTER call-layer substrates the BD wave did
    # not cover:
    #
    #   * build_capsule_payload   -- the outer ``_build_capsule`` call
    #                                composition (a raise in the
    #                                assembly logic outside the gather
    #                                helpers).
    #   * compose_verdict         -- LAW 6 single-line verdict
    #                                composition (topology + health
    #                                dict lookups composing the f-string).
    #   * write_capsule_file      -- W82.1 atomic file-write at the
    #                                call-layer (call-site wrap; the
    #                                inner BD wrap still catches inside
    #                                ``_serialize_and_write``).
    #   * serialize_to_json       -- json_envelope + to_json composition
    #                                at the call layer.
    #
    # Marker family ``capsule_<phase>_failed:<exc_class>:<detail>``
    # (same canonical shape as W607-BD -- one capsule_* marker family,
    # multiple wave-layered accumulators). W607-DK markers mirror into
    # ``_w607dk_warnings_out`` and surface via ``summary.warnings_out``
    # + top-level ``warnings_out``. Non-empty bucket flips
    # ``partial_success: True`` so the Pattern-2 silent-fallback guard
    # holds on degraded paths.
    _w607dk_warnings_out: list[str] = []

    def _run_check_dk(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-DK marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``capsule_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607dk_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607dk_warnings_out.append(f"capsule_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:
        # W607-BD: the capsule builder composes five distinct gather
        # boundaries; each individual gather is wrapped via the injected
        # ``_run_check_bd`` so a raise inside any one boundary degrades
        # that section to its empty-floor default rather than crashing
        # the whole capsule.
        # W607-DK: wrap the OUTER ``_build_capsule`` call itself so a
        # raise in the assembly logic (outside the inner gather helpers)
        # also degrades to an empty-floor capsule rather than crashing.
        def _call_build_capsule():
            return _build_capsule(
                conn,
                redact_paths=redact_paths,
                no_signatures=no_signatures,
                run_check=_run_check_bd,
            )

        # W978 2nd discipline: ``default=`` MUST be a literal-only tree.
        # The ``redacted`` / ``no_signatures`` flag values come from the
        # click options (Names); they're stamped onto the floor capsule
        # AFTER the wrap returns so the kwarg-bind stays a pure literal.
        capsule_data = _run_check_dk(
            "build_capsule_payload",
            _call_build_capsule,
            default={
                "capsule": {
                    "version": "1.0",
                    "generated": "",
                    "tool_version": "",
                    "redacted": False,
                    "no_signatures": False,
                },
                "topology": {"files": 0, "symbols": 0, "edges": 0, "languages": []},
                "symbols": [],
                "edges": [],
                "clusters": [],
                "health": {
                    "score": 0,
                    "cycles": 0,
                    "god_components": 0,
                    "layer_violations": 0,
                    "bottlenecks": 0,
                    "dead_exports": 0,
                    "tangle_ratio": 0.0,
                    "avg_complexity": 0.0,
                },
            },
        )
        # Stamp the user-provided flags onto the floor capsule AFTER the
        # wrap returns so the kwarg-bind stays literal-only.
        if isinstance(capsule_data, dict) and isinstance(capsule_data.get("capsule"), dict):
            capsule_data["capsule"]["redacted"] = redact_paths
            capsule_data["capsule"]["no_signatures"] = no_signatures
        if capsule_data is None:
            capsule_data = {
                "topology": {"files": 0, "symbols": 0, "edges": 0, "languages": []},
                "health": {"score": 0, "cycles": 0, "god_components": 0},
            }

    # W607-DK: ``compose_verdict`` substrate -- LAW 6 single-line verdict.
    # The closure embeds every dict lookup INSIDE the wrapped function
    # (W978 5th discipline: never index a possibly-poisoned dict at the
    # kwarg-bind site). A raise (KeyError on a corrupted capsule_data)
    # degrades to the explicit no-data floor so the envelope still
    # emits a non-empty verdict.
    def _compose_verdict():
        topology_local = capsule_data["topology"]
        health_local = capsule_data["health"]
        files_local = topology_local.get("files", 0)
        symbols_local = topology_local.get("symbols", 0)
        edges_local = topology_local.get("edges", 0)
        score_local = health_local.get("score", 0)
        cycles_local = health_local.get("cycles", 0)
        god_local = health_local.get("god_components", 0)
        langs_local = topology_local.get("languages", []) or []
        langs_str_local = ", ".join(langs_local) if langs_local else "(none)"
        verdict_local = f"capsule exported ({files_local} files, {symbols_local} symbols, {edges_local} edges)"
        return (
            files_local,
            symbols_local,
            edges_local,
            score_local,
            cycles_local,
            god_local,
            langs_str_local,
            verdict_local,
        )

    verdict_bundle = _run_check_dk(
        "compose_verdict",
        _compose_verdict,
        default=(0, 0, 0, 0, 0, 0, "(none)", "capsule export degraded (no topology data)"),
    )
    if verdict_bundle is None:
        verdict_bundle = (0, 0, 0, 0, 0, 0, "(none)", "capsule export degraded (no topology data)")
    files_n, symbols_n, edges_n, score, cycles, god, langs_str, verdict = verdict_bundle

    # Write to file if requested — atomic so a mid-write crash does not
    # land a torn capsule that downstream `roam capsule` / replay consumers
    # then json.loads and crash on (Pattern 1 variant C).
    # W607-BD: wrap the atomic write so a disk-full / permission /
    # encoding raise surfaces a marker rather than crashing the command;
    # the stdout summary still emits with the in-memory capsule_data.
    # W607-DK: also wrap the outer call-layer composition (the import +
    # path construction + closure assembly) so a raise BEFORE
    # ``_serialize_and_write`` runs (e.g., import failure) also surfaces.
    if output:

        def _call_write_capsule_file():
            from roam.atomic_io import atomic_write_text

            out_path = Path(output)

            def _serialize_and_write():
                atomic_write_text(out_path, _json.dumps(capsule_data, indent=2, default=str))
                return True

            return _run_check_bd(
                "atomic_write_capsule",
                _serialize_and_write,
                default=False,
            )

        _run_check_dk(
            "write_capsule_file",
            _call_write_capsule_file,
            default=False,
        )

    # JSON mode without --output: emit full capsule in envelope
    if json_mode and not output:
        # W607-BD: stamp substrate markers onto BOTH ``summary.warnings_out``
        # and the top-level ``warnings_out`` (matches the W607-AY mirror
        # discipline). Non-empty bucket flips partial_success.
        # W607-DK: union of BOTH wave-layered accumulators is what
        # surfaces; degraded paths from either wave flip partial_success.
        def _build_envelope_dk():
            _summary: dict = {
                "verdict": verdict,
                "files": files_n,
                "symbols": symbols_n,
                "edges": edges_n,
                "health_score": score,
                # W1298 Pattern-3a: per-symbol metrics in the capsule
                # carry ``cognitive_complexity`` direct from
                # symbol_metrics — disclose the scorer so importers
                # cannot misread it as cyclomatic.
                "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
            }
            _envelope_extra = dict(capsule_data)
            # Union both wave-layered accumulators so callers see every
            # surfaced marker regardless of which wrap caught it.
            _all_markers = list(_w607bd_warnings_out) + list(_w607dk_warnings_out)
            if _all_markers:
                _summary["partial_success"] = True
                _summary["warnings_out"] = list(_all_markers)
                _envelope_extra["warnings_out"] = list(_all_markers)
            return json_envelope(
                "capsule",
                summary=_summary,
                budget=token_budget,
                **_envelope_extra,
            )

        # W607-DK: wrap the json_envelope composition itself so a
        # circular-ref / hostile field in capsule_data surfaces a
        # marker rather than crashing before to_json runs.
        _envelope = _run_check_dk(
            "serialize_to_json",
            _build_envelope_dk,
            default=None,
        )
        if _envelope is None:
            # Floor envelope -- the W607-DK wrap surfaced a marker but
            # we still owe a structurally valid JSON envelope to the
            # caller. Pattern-2 silent-fallback discipline: name the
            # concrete state, not SAFE/completed.
            _all_markers = list(_w607bd_warnings_out) + list(_w607dk_warnings_out)
            _envelope = json_envelope(
                "capsule",
                summary={
                    "verdict": "capsule envelope serialization failed",
                    "files": files_n,
                    "symbols": symbols_n,
                    "edges": edges_n,
                    "partial_success": True,
                    "state": "envelope_serialize_failed",
                    "warnings_out": list(_all_markers),
                },
                warnings_out=list(_all_markers),
            )
        # W607-BD: wrap the to_json call so a circular-ref bug or
        # hostile field surfaces a marker rather than crashing.
        _output_text = _run_check_bd(
            "serialize_envelope",
            to_json,
            _envelope,
            default="{}",
        )
        # If serialize_envelope or serialize_to_json failed, the BD/DK
        # markers may have landed AFTER _envelope was composed. Re-pack
        # if needed so callers see the freshest marker set.
        if _w607bd_warnings_out or _w607dk_warnings_out:
            # Re-emit envelope with fresh marker union so post-compose
            # markers (e.g., a to_json TypeError after envelope built)
            # also surface.
            _all_markers = list(_w607bd_warnings_out) + list(_w607dk_warnings_out)
            if _output_text and _output_text != "{}":
                try:
                    _reload = _json.loads(_output_text)
                    _reload.setdefault("summary", {})["partial_success"] = True
                    _reload.setdefault("summary", {})["warnings_out"] = list(_all_markers)
                    _reload["warnings_out"] = list(_all_markers)
                    _output_text = _json.dumps(_reload)
                except (ValueError, TypeError) as _exc:
                    from roam.observability import log_swallowed

                    log_swallowed("cmd_capsule:warnings_repack", _exc)
        click.echo(_output_text if _output_text is not None else "{}")
        return

    # Text summary (always shown when --output is used; default mode otherwise)
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo("Topology:")
    click.echo(f"  Files:     {files_n}")
    click.echo(f"  Symbols:   {symbols_n}")
    click.echo(f"  Edges:     {edges_n}")
    click.echo(f"  Languages: {langs_str}")
    click.echo()
    click.echo("Health:")
    click.echo(f"  Score: {score}/100")
    click.echo(f"  Cycles: {cycles}")
    click.echo(f"  God components: {god}")

    if output:
        click.echo()
        click.echo(f"Capsule written to: {output}")
