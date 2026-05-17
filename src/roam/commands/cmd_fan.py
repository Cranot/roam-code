"""Show fan-in/fan-out metrics for symbols or files."""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, open_db
from roam.output.file_role_hints import is_excluded_path
from roam.output.formatter import abbrev_kind, format_table, json_envelope, loc, to_json
from roam.output.framework_filter import FRAMEWORK_PRIMITIVE_NAMES as _FRAMEWORK_NAMES

# W152: fan is the fifth detector migrating onto the central findings
# registry (after ``clones`` in W95, ``dead`` in W99, ``complexity`` in
# W102, ``smells`` in W109). The shape mirrors those — a stable detector
# version stamp and a deterministic ``finding_id_str`` so re-runs upsert
# instead of duplicating rows. Bump this when the predicate (cross-file
# hub threshold, degree thresholds) or the emitted flag vocabulary
# changes meaningfully.
FAN_DETECTOR_VERSION: str = "1.0.0"


# W152 — per-flag confidence tier mapping.
#
# All three architectural flags ride on graph-edge evidence (the call /
# import graph in ``edges`` + ``file_edges``) rather than on regex or
# runtime signal. Per the W150 audit they all land at ``structural``:
#
# * ``arch.fan_hub`` — cross-file fan-in over threshold (many distinct
#   files import / call this symbol).
# * ``arch.fan_spreader`` — cross-file fan-out over threshold (this
#   symbol reaches into many distinct files).
# * ``arch.fan_high_risk`` — both directions over threshold (hub and
#   spreader concurrently).
#
# ``local-hub`` / ``local-spreader`` are intentionally NOT mirrored: the
# W150 audit classifies them as single-file by design (one large SFC,
# generated module) rather than architectural — emitting them would
# bloat the registry with non-actionable rows.
_FAN_FLAG_TO_KIND: dict[str, str] = {
    "hub": "arch.fan_hub",
    "spreader": "arch.fan_spreader",
    "HIGH-RISK": "arch.fan_high_risk",
}
_FAN_FLAG_TO_CONFIDENCE: dict[str, str] = {
    "hub": "structural",
    "spreader": "structural",
    "HIGH-RISK": "structural",
}


def _fan_finding_id(
    source_detector: str,
    flag: str,
    subject_key: str,
) -> str:
    """Stable, deterministic finding id for one fan hit.

    ``subject_key`` is the natural identifier for the subject:
    ``<file_path>:<symbol_name>:<line_start>`` for symbol mode and
    ``<file_path>`` for file mode. We fold it into a short digest so
    re-runs upsert the same row in place rather than duplicating.

    The ``source_detector`` is part of the id to avoid hash collisions
    across the dual-detector design (``fan-symbol`` vs ``fan-file``):
    a same-name file/symbol pair under each surface gets distinct ids.
    """
    raw = f"{source_detector}:{flag}:{subject_key}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{source_detector}:{flag}:{digest}"


def _resolve_file_id(conn: sqlite3.Connection, file_path: str) -> int | None:
    """Look up ``files.id`` for a path. Returns ``None`` on miss.

    File-mode subjects link via ``subject_kind='file'`` + ``subject_id``
    pointing at ``files.id`` so downstream consumers can JOIN cleanly.
    """
    try:
        row = conn.execute(
            "SELECT id FROM files WHERE path = ? LIMIT 1",
            (file_path,),
        ).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.OperationalError:
        return None


def _emit_fan_findings(
    conn: sqlite3.Connection,
    data: dict,
    mode: str,
    source_version: str,
) -> int:
    """Mirror cross-file fan findings into the central registry.

    Returns the number of finding rows written. Caller is responsible
    for opening ``conn`` writable; emit_finding does not commit
    (the caller commits once at the end of the persist branch).

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard fan command path.

    Dual ``source_detector`` design per the W150 audit:

    * ``mode == "symbol"`` → ``source_detector = "fan-symbol"``,
      ``subject_kind = "symbol"``, ``subject_id`` = ``symbols.id``.
    * ``mode == "file"`` → ``source_detector = "fan-file"``,
      ``subject_kind = "file"``, ``subject_id`` = ``files.id``.

    The dual approach keeps the registry queryable per surface
    (``roam findings list --detector fan-symbol`` vs ``--detector
    fan-file``) instead of forcing consumers to filter on a nested
    ``mode`` field in the evidence JSON.

    Only the three architectural flags (``HIGH-RISK`` / ``hub`` /
    ``spreader``) are mirrored. Rows with empty flag, ``local-hub``, or
    ``local-spreader`` are skipped — see the module-level
    ``_FAN_FLAG_TO_KIND`` comment for the rationale.
    """
    # Local import keeps the cost out of the read-only path —
    # callers without --persist never reach here.
    from roam.db.findings import FindingRecord, emit_finding

    source_detector = "fan-symbol" if mode == "symbol" else "fan-file"
    subject_kind = "symbol" if mode == "symbol" else "file"
    caller_metric_definition = data.get("summary", {}).get("caller_metric_definition")

    written = 0
    for item in data.get("items", []):
        flag = item.get("flag") or ""
        if flag not in _FAN_FLAG_TO_KIND:
            # Skip empty, local-hub, local-spreader — non-architectural.
            continue

        kind_label = _FAN_FLAG_TO_KIND[flag]
        confidence = _FAN_FLAG_TO_CONFIDENCE[flag]

        if mode == "symbol":
            symbol_name = item.get("name") or ""
            location = item.get("location") or ""
            file_path = location.split(":", 1)[0] if location else ""
            line_start: int | None = None
            if location and ":" in location:
                try:
                    line_start = int(location.rsplit(":", 1)[1])
                except (ValueError, IndexError):
                    line_start = None
            # Resolve subject_id back to symbols.id via (file, name, line).
            subject_id: int | None = None
            try:
                row = conn.execute(
                    "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id "
                    "WHERE f.path = ? AND s.name = ? AND s.line_start = ? LIMIT 1",
                    (file_path, symbol_name, line_start),
                ).fetchone()
                if row is not None:
                    subject_id = int(row[0])
                else:
                    # Fallback: nearest-line match by (path, name) — handles
                    # decorator / parser line-start drift the same way smells does.
                    row = conn.execute(
                        "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id "
                        "WHERE f.path = ? AND s.name = ? "
                        "ORDER BY ABS(COALESCE(s.line_start, 0) - ?) LIMIT 1",
                        (file_path, symbol_name, line_start or 0),
                    ).fetchone()
                    subject_id = int(row[0]) if row is not None else None
            except sqlite3.OperationalError:
                subject_id = None

            subject_key = f"{file_path}:{symbol_name}:{int(line_start or 0)}"
            evidence = {
                "mode": "symbol",
                "flag": flag,
                "symbol_name": symbol_name,
                "kind": item.get("kind"),
                "file_path": file_path,
                "line_start": line_start,
                "location": location,
                "fan_in": item.get("fan_in"),
                "fan_out": item.get("fan_out"),
                "total": item.get("total"),
                "fan_in_intra": item.get("fan_in_intra"),
                "fan_in_inter": item.get("fan_in_inter"),
                "fan_in_files": item.get("fan_in_files"),
                "fan_out_intra": item.get("fan_out_intra"),
                "fan_out_inter": item.get("fan_out_inter"),
                "fan_out_files": item.get("fan_out_files"),
                "betweenness": item.get("betweenness"),
                "pagerank": item.get("pagerank"),
                # Pattern 3 (vocabulary discipline): preserve the exact
                # metric definition so downstream consumers can tell
                # this `fan_in` apart from `impact`'s `fan_in` or
                # `cmd_describe`'s caller count.
                "caller_metric_definition": caller_metric_definition,
            }
            claim = (
                f"{kind_label}: {symbol_name} ({location}) — "
                f"fan_in={item.get('fan_in')}, fan_out={item.get('fan_out')}, "
                f"fan_in_files={item.get('fan_in_files')}, "
                f"fan_out_files={item.get('fan_out_files')}"
            )
        else:  # file mode
            file_path = item.get("path") or ""
            subject_id = _resolve_file_id(conn, file_path)
            subject_key = file_path
            evidence = {
                "mode": "file",
                "flag": flag,
                "file_path": file_path,
                "fan_in": item.get("fan_in"),
                "fan_out": item.get("fan_out"),
                "total": item.get("total"),
                "caller_metric_definition": caller_metric_definition,
            }
            claim = f"{kind_label}: {file_path} — fan_in={item.get('fan_in')}, fan_out={item.get('fan_out')}"

        finding_id = _fan_finding_id(source_detector, flag, subject_key)
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=confidence,
                source_detector=source_detector,
                source_version=source_version,
            ),
        )
        written += 1
    return written


def _filter_tooling_rows(rows):
    """Filter out rows whose ``file_path`` is in a default-excluded
    location (tooling, generated, examples, vendor, workspaces, etc.).

    Uses the shared ``output.file_role_hints`` set so all headline
    commands stay in sync.
    """
    return [r for r in rows if not is_excluded_path(r["file_path"])]


_CROSS_FILE_HUB_THRESHOLD = 3


def _file_scope_metrics(conn, symbol_ids):
    """Return per-symbol intra/inter-file edge breakdowns.

    Splits each symbol's incoming and outgoing edges by whether the other
    side lives in the same file. Reports distinct file counts so callers
    can decide whether ``hub``/``spreader`` is architectural (many files)
    or just an intra-file convention (one large SFC, generated module).
    """
    if not symbol_ids:
        return {}

    meta = {
        sid: {
            "fan_in_intra": 0,
            "fan_in_inter": 0,
            "fan_in_files": 0,
            "fan_out_intra": 0,
            "fan_out_inter": 0,
            "fan_out_files": 0,
        }
        for sid in symbol_ids
    }

    # Incoming edges grouped by target_id with src.file_id distinct count.
    incoming = batched_in(
        conn,
        "SELECT e.target_id AS sid, src.file_id AS other_file, tgt.file_id AS self_file "
        "FROM edges e "
        "JOIN symbols src ON e.source_id = src.id "
        "JOIN symbols tgt ON e.target_id = tgt.id "
        "WHERE e.target_id IN ({ph})",
        list(symbol_ids),
    )
    in_files: dict[int, set[int]] = {sid: set() for sid in symbol_ids}
    for row in incoming:
        sid = row["sid"]
        bucket = meta[sid]
        if row["other_file"] == row["self_file"]:
            bucket["fan_in_intra"] += 1
        else:
            bucket["fan_in_inter"] += 1
        in_files[sid].add(row["other_file"])

    # Outgoing edges grouped by source_id with tgt.file_id distinct count.
    outgoing = batched_in(
        conn,
        "SELECT e.source_id AS sid, tgt.file_id AS other_file, src.file_id AS self_file "
        "FROM edges e "
        "JOIN symbols src ON e.source_id = src.id "
        "JOIN symbols tgt ON e.target_id = tgt.id "
        "WHERE e.source_id IN ({ph})",
        list(symbol_ids),
    )
    out_files: dict[int, set[int]] = {sid: set() for sid in symbol_ids}
    for row in outgoing:
        sid = row["sid"]
        bucket = meta[sid]
        if row["other_file"] == row["self_file"]:
            bucket["fan_out_intra"] += 1
        else:
            bucket["fan_out_inter"] += 1
        out_files[sid].add(row["other_file"])

    for sid in symbol_ids:
        # Subtract self-file from outbound to keep "files this depends on"
        # comparable to inbound (consumers always live in another file).
        meta[sid]["fan_in_files"] = len(in_files.get(sid, set()))
        meta[sid]["fan_out_files"] = len(out_files.get(sid, set()))

    return meta


def _scope_flag(meta_entry, in_deg, out_deg):
    """Pick the hub/spreader label based on cross-file reach.

    The historic flag fired on raw edge counts, which over-marked symbols
    confined to one large SFC. Cross-file reach (``fan_*_files``) is a
    better signal of architectural pressure — a const used 342 times
    inside its own file is not a spreader.
    """
    in_files = meta_entry.get("fan_in_files", 0)
    out_files = meta_entry.get("fan_out_files", 0)
    cross_in = in_files >= _CROSS_FILE_HUB_THRESHOLD and in_deg > 10
    cross_out = out_files >= _CROSS_FILE_HUB_THRESHOLD and out_deg > 10
    if cross_in and cross_out:
        return "HIGH-RISK"
    if cross_in:
        return "hub"
    if cross_out:
        return "spreader"
    if in_deg > 10 and in_files <= 1:
        return "local-hub"
    if out_deg > 10 and out_files <= 1:
        return "local-spreader"
    return ""


@roam_capability(
    category="architecture",
    summary="Show fan-in/fan-out metrics ranking symbols or files by coupling.",
    inputs=["mode"],
    outputs=["rankings"],
    examples=[
        "roam fan",
        "roam fan file -n 50",
        "roam fan --no-framework",
    ],
    tags=["architecture", "metrics"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command()
@click.argument("mode", default="symbol", type=click.Choice(["symbol", "file"]))
@click.option("-n", "count", default=20, help="Number of items to show")
@click.option("--no-framework", is_flag=True, help="Filter out framework/boilerplate symbols")
@click.option(
    "--include-tooling",
    is_flag=True,
    default=False,
    help=(
        "Include CI scripts, dev tooling, build, and generated files. "
        "Excluded by default — high fan-in in dev/.github/benchmarks "
        "is expected and dominates the headline."
    ),
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist cross-file architectural fan findings (HIGH-RISK / hub / "
        "spreader) to the .roam/index.db findings registry. "
        "Symbol-mode findings emit under source_detector='fan-symbol'; "
        "file-mode under 'fan-file'. Local-only flags (local-hub, "
        "local-spreader) are skipped as non-architectural. "
        "Query via `roam findings list --detector fan-symbol` or `fan-file`."
    ),
)
@click.pass_context
def fan(ctx, mode, count, no_framework, include_tooling, persist):
    """Show fan-in/fan-out: most connected symbols or files.

    Unlike ``coupling`` (which measures temporal co-change frequency), this
    command measures structural connectivity (import/call edges) and flags
    hub/spreader hotspots.

    \b
    Examples:
      roam fan
      roam fan --mode file
      roam fan --count 30 --no-framework

    See also ``coupling`` (co-change frequency), ``deps`` (dependency
    graph), and ``hotspots`` (runtime hotspots).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=not persist) as conn:
        # Pull more rows than ``count`` when filtering, so the displayed
        # top-N still has ``count`` entries after exclusions. 5x is a
        # comfortable safety factor for typical tooling shares.
        fetch_limit = count * 5 if not include_tooling else count
        if mode == "symbol":
            rows = conn.execute(
                """
                SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start,
                       gm.in_degree, gm.out_degree,
                       (gm.in_degree + gm.out_degree) as total,
                       gm.betweenness, gm.pagerank
                FROM graph_metrics gm
                JOIN symbols s ON gm.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE gm.in_degree + gm.out_degree > 0
                ORDER BY total DESC
                LIMIT ?
            """,
                (fetch_limit,),
            ).fetchall()

            if not include_tooling:
                rows = _filter_tooling_rows(rows)
            rows = rows[:count]

            if no_framework:
                rows = [r for r in rows if r["name"] not in _FRAMEWORK_NAMES]

            scope_meta = _file_scope_metrics(conn, [r["id"] for r in rows])

            if not rows:
                if sarif_mode:
                    # W1209: SARIF output with empty results (rules catalogue
                    # still emitted so consumers can introspect the closed enum).
                    from roam.output.sarif import fan_to_sarif, write_sarif

                    click.echo(write_sarif(fan_to_sarif([])))
                    return
                if json_mode:
                    # W805-followup-C: empty-state disclosure (Pattern 2
                    # silent-fallback fix). Zero rows on a symbol-mode
                    # query means the symbols/degrees corpus is empty —
                    # not a clean run. Surface via partial_success +
                    # closed-enum state.
                    click.echo(
                        to_json(
                            json_envelope(
                                "fan",
                                budget=token_budget,
                                summary={
                                    "verdict": "no graph metrics available (corpus empty — run `roam index --force` to populate)",
                                    "mode": mode,
                                    "items": 0,
                                    "partial_success": True,
                                    "state": "no_symbols",
                                },
                                mode=mode,
                                items=[],
                            )
                        )
                    )
                else:
                    click.echo("No graph metrics available. Run `roam index` first.")
                return

            # Build the symbol-mode items list once — reused by JSON emit,
            # the persist branch, and (indirectly) the text table below.
            symbol_items = [
                {
                    "name": r["name"],
                    "kind": r["kind"],
                    "fan_in": r["in_degree"] or 0,
                    "fan_out": r["out_degree"] or 0,
                    "total": (r["in_degree"] or 0) + (r["out_degree"] or 0),
                    "betweenness": round(r["betweenness"] or 0, 1),
                    "pagerank": round(r["pagerank"] or 0, 4),
                    "location": loc(r["file_path"], r["line_start"]),
                    "fan_in_intra": scope_meta.get(r["id"], {}).get("fan_in_intra", 0),
                    "fan_in_inter": scope_meta.get(r["id"], {}).get("fan_in_inter", 0),
                    "fan_in_files": scope_meta.get(r["id"], {}).get("fan_in_files", 0),
                    "fan_out_intra": scope_meta.get(r["id"], {}).get("fan_out_intra", 0),
                    "fan_out_inter": scope_meta.get(r["id"], {}).get("fan_out_inter", 0),
                    "fan_out_files": scope_meta.get(r["id"], {}).get("fan_out_files", 0),
                    "flag": _scope_flag(
                        scope_meta.get(r["id"], {}),
                        r["in_degree"] or 0,
                        r["out_degree"] or 0,
                    ),
                }
                for r in rows
            ]

            # W152: mirror cross-file architectural flags into the
            # findings registry. Runs ONLY with --persist. Local-only
            # flags (local-hub, local-spreader) are skipped per the
            # W150 audit recommendation.
            if persist:
                try:
                    _emit_fan_findings(
                        conn,
                        {
                            "summary": {"caller_metric_definition": "direct_in_degree"},
                            "items": symbol_items,
                        },
                        mode="symbol",
                        source_version=FAN_DETECTOR_VERSION,
                    )
                    conn.commit()
                except sqlite3.OperationalError:
                    # findings table missing (pre-W89 schema) — degrade gracefully.
                    pass

            # --- W1209: SARIF projection (symbol mode) ---
            # Branches BEFORE json/text so the pre-existing paths stay
            # byte-identical. Only the three cross-file architectural
            # flags (HIGH-RISK / hub / spreader) project to SARIF —
            # local-only flags are skipped per the W150 audit.
            if sarif_mode:
                from roam.output.sarif import fan_to_sarif, write_sarif

                click.echo(write_sarif(fan_to_sarif(symbol_items)))
                return

            if json_mode:
                _top_in = max(rows, key=lambda r: r["in_degree"] or 0)
                _top_out = max(rows, key=lambda r: r["out_degree"] or 0)
                _verdict = (
                    f"top fan-in: {_top_in['name']}({_top_in['in_degree'] or 0}), "
                    f"top fan-out: {_top_out['name']}({_top_out['out_degree'] or 0})"
                )
                click.echo(
                    to_json(
                        json_envelope(
                            "fan",
                            budget=token_budget,
                            summary={
                                "verdict": _verdict,
                                "mode": mode,
                                "items": len(rows),
                                "caller_metric_definition": "direct_in_degree",
                            },
                            mode=mode,
                            items=symbol_items,
                        )
                    )
                )
                return

            table_rows = []
            for r in rows:
                in_deg = r["in_degree"] or 0
                out_deg = r["out_degree"] or 0
                total = in_deg + out_deg
                flag = _scope_flag(scope_meta.get(r["id"], {}), in_deg, out_deg)
                bw = r["betweenness"] or 0
                bw_str = f"{bw:.0f}" if bw >= 10 else (f"{bw:.1f}" if bw > 0.5 else "")
                pr = r["pagerank"] or 0
                pr_str = f"{pr:.4f}" if pr > 0 else ""

                table_rows.append(
                    [
                        abbrev_kind(r["kind"]),
                        r["name"],
                        str(in_deg),
                        str(out_deg),
                        str(total),
                        bw_str,
                        pr_str,
                        flag,
                        loc(r["file_path"], r["line_start"]),
                    ]
                )

            _top_in_r = max(rows, key=lambda r: r["in_degree"] or 0)
            _top_out_r = max(rows, key=lambda r: r["out_degree"] or 0)
            _verdict = (
                f"top fan-in: {_top_in_r['name']}({_top_in_r['in_degree'] or 0}), "
                f"top fan-out: {_top_out_r['name']}({_top_out_r['out_degree'] or 0})"
            )
            click.echo(f"VERDICT: {_verdict}\n")
            click.echo("=== Fan-in/Fan-out (symbol level) ===")
            click.echo(
                format_table(
                    [
                        "kind",
                        "name",
                        "fan-in",
                        "fan-out",
                        "total",
                        "btwn",
                        "PR",
                        "flag",
                        "location",
                    ],
                    table_rows,
                )
            )

        else:  # file mode
            rows = conn.execute(
                """
                SELECT f.path,
                       COUNT(DISTINCT CASE WHEN fe_in.target_file_id = f.id THEN fe_in.source_file_id END) as fan_in,
                       COUNT(DISTINCT CASE WHEN fe_out.source_file_id = f.id THEN fe_out.target_file_id END) as fan_out
                FROM files f
                LEFT JOIN file_edges fe_in ON fe_in.target_file_id = f.id
                LEFT JOIN file_edges fe_out ON fe_out.source_file_id = f.id
                GROUP BY f.id
                HAVING fan_in + fan_out > 0
                ORDER BY fan_in + fan_out DESC
                LIMIT ?
            """,
                (count,),
            ).fetchall()

            if not rows:
                if sarif_mode:
                    # W1209: SARIF output with empty results.
                    from roam.output.sarif import fan_to_sarif, write_sarif

                    click.echo(write_sarif(fan_to_sarif([])))
                    return
                if json_mode:
                    # W805-followup-C: empty-state disclosure (Pattern 2
                    # silent-fallback fix). Zero rows on a file-mode
                    # query means the file_edges corpus is empty — not
                    # a clean run. Surface via partial_success + state.
                    click.echo(
                        to_json(
                            json_envelope(
                                "fan",
                                budget=token_budget,
                                summary={
                                    "verdict": "no file edges available (corpus empty — run `roam index --force` to populate)",
                                    "mode": mode,
                                    "items": 0,
                                    "partial_success": True,
                                    "state": "no_file_edges",
                                },
                                mode=mode,
                                items=[],
                            )
                        )
                    )
                else:
                    click.echo("No file edges available. Run `roam index` first.")
                return

            def _file_flag(fan_in: int, fan_out: int) -> str:
                if fan_in > 5 and fan_out > 5:
                    return "HIGH-RISK"
                if fan_in > 5:
                    return "hub"
                if fan_out > 5:
                    return "spreader"
                return ""

            # Build the file-mode items list once — reused by JSON emit,
            # the persist branch, and the text table below.
            file_items = [
                {
                    "path": r["path"],
                    "fan_in": r["fan_in"],
                    "fan_out": r["fan_out"],
                    "total": r["fan_in"] + r["fan_out"],
                    "flag": _file_flag(r["fan_in"], r["fan_out"]),
                }
                for r in rows
            ]

            # W152: mirror cross-file architectural flags into the
            # findings registry. Runs ONLY with --persist.
            if persist:
                try:
                    _emit_fan_findings(
                        conn,
                        {
                            "summary": {
                                "caller_metric_definition": ("direct_in_degree (file-level: distinct source files)")
                            },
                            "items": file_items,
                        },
                        mode="file",
                        source_version=FAN_DETECTOR_VERSION,
                    )
                    conn.commit()
                except sqlite3.OperationalError:
                    # findings table missing (pre-W89 schema) — degrade gracefully.
                    pass

            # --- W1209: SARIF projection (file mode) ---
            # Branches BEFORE json/text so the pre-existing paths stay
            # byte-identical. fan_to_sarif handles file-mode rows via
            # the ``path`` field (no line — metric applies to the
            # whole file).
            if sarif_mode:
                from roam.output.sarif import fan_to_sarif, write_sarif

                click.echo(write_sarif(fan_to_sarif(file_items)))
                return

            if json_mode:
                _top_in_r = max(rows, key=lambda r: r["fan_in"])
                _top_out_r = max(rows, key=lambda r: r["fan_out"])
                _top_in_name = _top_in_r["path"].split("/")[-1]
                _top_out_name = _top_out_r["path"].split("/")[-1]
                _verdict = (
                    f"top fan-in: {_top_in_name}({_top_in_r['fan_in']}), "
                    f"top fan-out: {_top_out_name}({_top_out_r['fan_out']})"
                )
                click.echo(
                    to_json(
                        json_envelope(
                            "fan",
                            budget=token_budget,
                            summary={
                                "verdict": _verdict,
                                "mode": mode,
                                "items": len(rows),
                                "caller_metric_definition": "direct_in_degree (file-level: distinct source files)",
                            },
                            mode=mode,
                            items=file_items,
                        )
                    )
                )
                return

            table_rows = []
            for item in file_items:
                table_rows.append(
                    [
                        item["path"],
                        str(item["fan_in"]),
                        str(item["fan_out"]),
                        str(item["total"]),
                        item["flag"],
                    ]
                )

            _top_in_r = max(rows, key=lambda r: r["fan_in"])
            _top_out_r = max(rows, key=lambda r: r["fan_out"])
            _top_in_name = _top_in_r["path"].split("/")[-1]
            _top_out_name = _top_out_r["path"].split("/")[-1]
            _verdict = (
                f"top fan-in: {_top_in_name}({_top_in_r['fan_in']}), "
                f"top fan-out: {_top_out_name}({_top_out_r['fan_out']})"
            )
            click.echo(f"VERDICT: {_verdict}\n")
            click.echo("=== Fan-in/Fan-out (file level) ===")
            click.echo(
                format_table(
                    ["path", "fan-in", "fan-out", "total", "flag"],
                    table_rows,
                )
            )
