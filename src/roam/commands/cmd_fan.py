"""Show fan-in/fan-out metrics for symbols or files."""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file
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


def _parse_symbol_location(item: dict) -> tuple[str, str, int | None]:
    """Split a symbol item's ``location`` into ``(file_path, name, line)``.

    ``location`` is the ``<file>:<line>`` string the ranked items carry;
    ``line`` parses to ``None`` when absent or malformed. Single-sourced so
    the persist pre-pass and the emit loop key on byte-identical tuples.
    """
    symbol_name = item.get("name") or ""
    location = item.get("location") or ""
    file_path = location.split(":", 1)[0] if location else ""
    line_start: int | None = None
    if location and ":" in location:
        try:
            line_start = int(location.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            line_start = None
    return file_path, symbol_name, line_start


def _batch_resolve_symbol_ids(
    conn: sqlite3.Connection,
    keys: list[tuple[str, str, int | None]],
) -> dict[tuple[str, str, int | None], int]:
    """Pre-resolve ``symbols.id`` for every ``(path, name, line)`` key.

    Collapses the persist path's former per-item lookups — an exact
    ``(path, name, line)`` match plus a nearest-line fallback query, i.e.
    up to ``2*N`` round-trips for ``N`` flagged items — into a single
    ``path IN (...)`` scan. Candidates are grouped by ``(path, name)`` in
    memory so the nearest-line fallback runs Python-side with no second
    round-trip. Returns a map keyed by the same tuples; misses are absent.
    """
    if not keys:
        return {}
    paths = {k[0] for k in keys if k[0]}
    if not paths:
        return {}
    try:
        rows = batched_in(
            conn,
            "SELECT f.path, s.name, s.id, s.line_start "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE f.path IN ({ph})",
            paths,
        )
    except sqlite3.OperationalError:
        return {}

    # Group candidates by (path, name); both the exact and the
    # nearest-line match then resolve in memory.
    by_pair: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for path, name, sid, line_start in rows:
        by_pair.setdefault((path, name), []).append((int(sid), int(line_start or 0)))

    resolved: dict[tuple[str, str, int | None], int] = {}
    for key in keys:
        file_path, symbol_name, line_start = key
        candidates = by_pair.get((file_path, symbol_name))
        if not candidates:
            continue
        # Exact line first (matches the prior `s.line_start = ?` query);
        # only attempted when a line is known, mirroring the old path
        # where a NULL line never satisfied the equality and fell through.
        if line_start is not None:
            exact = next((sid for sid, ls in candidates if ls == line_start), None)
            if exact is not None:
                resolved[key] = exact
                continue
        # Nearest-line fallback (ABS distance to the known line, or 0).
        target = line_start or 0
        resolved[key] = min(candidates, key=lambda c: abs(c[1] - target))[0]
    return resolved


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

    # Pre-resolve every flagged symbol's subject_id in ONE batched query
    # (keyed by file/name/line, with an in-memory nearest-line fallback)
    # instead of the prior two lookups per item inside the loop.
    symbol_id_map: dict[tuple[str, str, int | None], int] = {}
    if mode == "symbol":
        symbol_keys = [
            _parse_symbol_location(item)
            for item in data.get("items", [])
            if (item.get("flag") or "") in _FAN_FLAG_TO_KIND
        ]
        symbol_id_map = _batch_resolve_symbol_ids(conn, symbol_keys)

    written = 0
    for item in data.get("items", []):
        flag = item.get("flag") or ""
        if flag not in _FAN_FLAG_TO_KIND:
            # Skip empty, local-hub, local-spreader — non-architectural.
            continue

        kind_label = _FAN_FLAG_TO_KIND[flag]
        confidence = _FAN_FLAG_TO_CONFIDENCE[flag]

        if mode == "symbol":
            file_path, symbol_name, line_start = _parse_symbol_location(item)
            location = item.get("location") or ""
            # subject_id was pre-resolved in one batched query above
            # (exact (file, name, line) match, else nearest-line fallback,
            # handling decorator / parser line-start drift like smells does).
            subject_id: int | None = symbol_id_map.get((file_path, symbol_name, line_start))

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


# F3 (DOGFOOD-CORE-2026-05-20, MED): test/prod role split mirrored from
# ``cmd_uses``. Both commands now classify each subject's location via the
# canonical ``is_test_file`` helper (``roam.commands.changed_files``) so the
# two stay vocabulary-consistent (Pattern 3a: no new divergence). ``uses``
# labels every consumer with ``scope`` and splits production_consumers /
# test_consumers in its summary; ``fan`` does the same for its ranked items
# (``scope`` field) and, additionally, drops test-role rows from the headline
# ranking by default — without it the #1 fan-in on roam-code is the
# ``invoke_cli`` conftest fixture (2438 refs), pure test noise that crowds out
# real production coupling. ``--include-tests`` opts the rows back in. The
# split is disclosed in the summary (test_items / test_filtered) so the drop
# is loud, never silent (Pattern-1-variant-D / Pattern-2 lineage).
def _row_scope(file_path) -> str:
    """Classify one subject's file path as ``test`` or ``production``.

    Same mechanism as ``cmd_uses._scope`` — delegates to the canonical
    ``is_test_file`` helper so test-path detection is single-sourced.
    """
    return "test" if is_test_file(file_path) else "production"


def _split_test_rows(rows, path_key: str):
    """Partition ``rows`` into (production_rows, test_rows) by file path.

    ``path_key`` is the row column carrying the file path (``file_path`` for
    symbol-mode graph_metrics rows, ``path`` for file-mode rows).
    """
    production = [r for r in rows if _row_scope(r[path_key]) == "production"]
    test = [r for r in rows if _row_scope(r[path_key]) == "test"]
    return production, test


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


def _score_fan_flags_for_mode_parity(
    items_local,
    *,
    include_local_only: bool,
    scanned_count: int,
) -> dict:
    """Keep symbol/file run_state classification in one precedence order."""
    _high_risk = 0
    _hubs = 0
    _spreaders = 0
    _local_only = 0
    _empty = 0
    for _it in items_local:
        _flag = _it["flag"]
        if _flag == "HIGH-RISK":
            _high_risk += 1
        elif _flag == "hub":
            _hubs += 1
        elif _flag == "spreader":
            _spreaders += 1
        elif include_local_only and _flag in ("local-hub", "local-spreader"):
            _local_only += 1
        else:
            _empty += 1
    if _high_risk > 0:
        _state = "HIGH_RISK_DETECTED"
    elif _hubs > 0 and _spreaders > 0:
        _state = "HUBS_AND_SPREADERS_DETECTED"
    elif _hubs > 0:
        _state = "HUBS_DETECTED"
    elif _spreaders > 0:
        _state = "SPREADERS_DETECTED"
    elif include_local_only and _local_only > 0:
        _state = "LOCAL_ONLY"
    else:
        _state = "BALANCED"

    _score = {
        "state": _state,
        "scanned": scanned_count,
        "high_risk": _high_risk,
        "hubs": _hubs,
        "spreaders": _spreaders,
    }
    if include_local_only:
        _score["local_only"] = _local_only
    _score["empty"] = _empty
    return _score


def _run_check_cy(phase, fn, *args, _warnings_out, default=None, **kwargs):
    """Run one aggregation-phase boundary with W607-CY marker emission.

    Mirror of ``_run_check`` shape (same ``fan_<phase>_failed:``
    marker family) but writes into the supplied ``_warnings_out`` list so
    additive buckets stay distinguishable in tests + audits.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- top-level disclosure
        _warnings_out.append(f"fan_{phase}_failed:{type(exc).__name__}:{exc}")
        return default


def _build_fan_envelope_preserving_warning_contract(
    *,
    mode,
    item_count,
    items,
    token_budget,
    summary,
    verdict,
    w607x_warnings_out,
    w607cy_warnings_out,
):
    """Build the JSON envelope while preserving both warning mirrors."""
    _kwargs: dict = {
        "budget": token_budget,
        "summary": summary,
        "mode": mode,
        "items": items,
    }
    _combined_warnings_out: list[str] = list(w607x_warnings_out) + list(w607cy_warnings_out)
    if _combined_warnings_out:
        summary["warnings_out"] = list(_combined_warnings_out)
        summary["partial_success"] = True
        _kwargs["warnings_out"] = list(_combined_warnings_out)
        _kwargs["partial_success"] = True

    _envelope_floor: dict = {
        "command": "fan",
        "schema_version": "1.0.0",
        "summary": {
            "verdict": verdict,
            "mode": mode,
            "items": item_count,
            "partial_success": True,
            "warnings_out": list(_combined_warnings_out),
        },
        "warnings_out": list(_combined_warnings_out),
    }
    fan_envelope = _run_check_cy(
        "serialize_envelope",
        json_envelope,
        "fan",
        _warnings_out=w607cy_warnings_out,
        default=_envelope_floor,
        **_kwargs,
    )
    if fan_envelope is _envelope_floor and w607cy_warnings_out:
        _combined_warnings_out = list(w607x_warnings_out) + list(w607cy_warnings_out)
        _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
        _envelope_floor["warnings_out"] = list(_combined_warnings_out)
        fan_envelope = _envelope_floor

    return fan_envelope


def _symbol_fan_predicate(rows_local):
    """Normalize symbol rows into the shared fan verdict predicate."""
    top_in_local = max(rows_local, key=lambda r: r["in_degree"] or 0)
    top_out_local = max(rows_local, key=lambda r: r["out_degree"] or 0)
    return {
        "top_in_name": top_in_local["name"],
        "top_in_value": top_in_local["in_degree"] or 0,
        "top_out_name": top_out_local["name"],
        "top_out_value": top_out_local["out_degree"] or 0,
        "item_count": len(rows_local),
    }


def _file_fan_predicate(rows_local):
    """Normalize file rows into the shared fan verdict predicate."""
    top_in_local = max(rows_local, key=lambda r: r["fan_in"])
    top_out_local = max(rows_local, key=lambda r: r["fan_out"])
    return {
        "top_in_name": top_in_local["path"].split("/")[-1],
        "top_in_value": top_in_local["fan_in"],
        "top_out_name": top_out_local["path"].split("/")[-1],
        "top_out_value": top_out_local["fan_out"],
        "item_count": len(rows_local),
    }


def _fan_score_default(scanned_count: int, *, include_local_only: bool) -> dict:
    """Preserve degraded score shape for each fan mode."""
    default_score = {
        "state": "DEGRADED",
        "scanned": scanned_count,
        "high_risk": 0,
        "hubs": 0,
        "spreaders": 0,
        "empty": 0,
    }
    if include_local_only:
        default_score["local_only"] = 0
    return default_score


def _build_fan_verdict_from_predicate(pred):
    return (
        f"top fan-in: {pred['top_in_name']}({pred['top_in_value']}), "
        f"top fan-out: {pred['top_out_name']}({pred['top_out_value']})"
    )


def _emit_fan_json_preserving_mode_parity(
    *,
    items,
    rows,
    mode,
    include_tests,
    test_filtered,
    token_budget,
    w607x_warnings_out,
    include_local_only,
    caller_metric_definition,
    predicate_fn,
):
    """Emit fan JSON through one warning and summary contract."""
    w607cy_warnings_out: list[str] = []
    item_count = len(items)

    def _score_classify(items_local):
        return _score_fan_flags_for_mode_parity(
            items_local,
            include_local_only=include_local_only,
            scanned_count=len(items_local),
        )

    # Precompute the fallback score into a var (W978 discipline 2: ``default=``
    # must not be an inline ``ast.Call`` — the eager evaluation defeats the
    # failure-only intent of the wrapper's default). Matches the sibling
    # ``default=_envelope_floor`` shape above.
    _score_floor = _fan_score_default(item_count, include_local_only=include_local_only)
    score_dict = _run_check_cy(
        "score_classify",
        _score_classify,
        items,
        _warnings_out=w607cy_warnings_out,
        default=_score_floor,
    )

    fan_predicate = _run_check_cy(
        "compute_predicate",
        predicate_fn,
        rows,
        _warnings_out=w607cy_warnings_out,
        default={
            "top_in_name": "",
            "top_in_value": 0,
            "top_out_name": "",
            "top_out_value": 0,
            "item_count": len(rows),
        },
    )

    verdict = _run_check_cy(
        "compute_verdict",
        _build_fan_verdict_from_predicate,
        fan_predicate,
        _warnings_out=w607cy_warnings_out,
        default="fan analysis completed",
    )

    summary: dict = {
        "verdict": verdict,
        "mode": mode,
        "items": len(rows),
        "caller_metric_definition": caller_metric_definition,
        "test_split": True,
        "include_tests": include_tests,
        "production_items": sum(1 for it in items if it["scope"] == "production"),
        "test_items": sum(1 for it in items if it["scope"] == "test"),
        "test_filtered": test_filtered,
        "run_state": score_dict["state"],
    }
    return _build_fan_envelope_preserving_warning_contract(
        mode=mode,
        item_count=len(rows),
        items=items,
        token_budget=token_budget,
        summary=summary,
        verdict=verdict,
        w607x_warnings_out=w607x_warnings_out,
        w607cy_warnings_out=w607cy_warnings_out,
    )


def _emit_symbol_json(
    symbol_items,
    rows,
    mode,
    include_tests,
    _test_filtered,
    token_budget,
    _w607x_warnings_out,
):
    """Emit the symbol-mode JSON envelope.

    Isolated from ``fan`` so the command function stays an orchestrator.
    W607-CY aggregation-phase boundaries (score_classify / compute_predicate
    / compute_verdict / serialize_envelope) run inside this helper.
    """
    return _emit_fan_json_preserving_mode_parity(
        items=symbol_items,
        rows=rows,
        mode=mode,
        include_tests=include_tests,
        test_filtered=_test_filtered,
        token_budget=token_budget,
        w607x_warnings_out=_w607x_warnings_out,
        include_local_only=True,
        caller_metric_definition="direct_in_degree",
        predicate_fn=_symbol_fan_predicate,
    )


def _emit_file_json(
    file_items,
    rows,
    mode,
    include_tests,
    _file_test_filtered,
    token_budget,
    _w607x_warnings_out,
):
    """Emit the file-mode JSON envelope.

    Isolated from ``fan`` so the command function stays an orchestrator.
    Same W607-CY aggregation-phase boundaries as the symbol-mode helper,
    adapted for the file-level flag vocabulary (no local-* flags).
    """
    return _emit_fan_json_preserving_mode_parity(
        items=file_items,
        rows=rows,
        mode=mode,
        include_tests=include_tests,
        test_filtered=_file_test_filtered,
        token_budget=token_budget,
        w607x_warnings_out=_w607x_warnings_out,
        include_local_only=False,
        caller_metric_definition="direct_in_degree (file-level: distinct source files)",
        predicate_fn=_file_fan_predicate,
    )


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
    side_effect=True,
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
    "--include-tests",
    is_flag=True,
    default=False,
    help=(
        "Include test-role symbols/files in the headline ranking. Excluded "
        "by default — a conftest fixture (e.g. invoke_cli) routinely tops "
        "fan-in and crowds out real production coupling. Test-role rows are "
        "always classified (scope field) and disclosed in the summary "
        "(test_items / test_filtered) whether shown or not."
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
def fan(ctx, mode, count, no_framework, include_tooling, include_tests, persist):
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

    # W607-X: per-substrate raise -> ``fan_<phase>_failed:<exc_class>:<detail>``
    # marker accumulator. Threaded onto the success-path JSON envelope at the
    # bottom of each mode branch. Empty-bucket -> envelope omits warnings_out
    # (hash-stable on the clean path, mirrors W607-A..W contract).
    _w607x_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-X marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception (the helper itself raised before producing its own
        floor value), surface a ``fan_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607x_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607x_warnings_out.append(f"fan_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        # Pull more rows than ``count`` when filtering, so the displayed
        # top-N still has ``count`` entries after exclusions. 5x is a
        # comfortable safety factor for typical tooling shares.
        fetch_limit = count * 5 if not include_tooling else count
        if mode == "symbol":

            def _fetch_symbol_rows():
                return conn.execute(
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

            rows = _run_check("fetch_symbol_rows", _fetch_symbol_rows, default=[]) or []

            # Pattern 2 silent-fallback fix: track WHY rows can end up empty
            # so the verdict and hint name the actual cause. The pre-fix path
            # emitted ``state: "no_symbols"`` + "corpus empty — run roam index
            # --force" regardless of whether the raw query returned zero rows
            # (genuinely empty corpus) OR whether the tooling/framework
            # filters wiped them all (corpus had rows, all filtered out).
            # The lying hint sent users to reindex when their filters were
            # the issue.
            _raw_row_count = len(rows)
            if not include_tooling:
                rows = _run_check("filter_tooling", _filter_tooling_rows, rows, default=rows) or []
            _after_tooling = len(rows)

            # F3: split test-role rows out of the headline ranking by default.
            # Mirror of cmd_uses' production/test scope split — both use the
            # canonical is_test_file helper. Test-role count is disclosed in
            # the summary (test_filtered) so the drop is loud, not silent.
            _prod_rows, _test_rows = _split_test_rows(rows, "file_path")
            _test_filtered = 0 if include_tests else len(_test_rows)
            if not include_tests:
                rows = _prod_rows
            _after_tests = len(rows)
            rows = rows[:count]

            if no_framework:
                rows = [r for r in rows if r["name"] not in _FRAMEWORK_NAMES]

            scope_meta = (
                _run_check(
                    "file_scope_metrics",
                    _file_scope_metrics,
                    conn,
                    [r["id"] for r in rows],
                    default={},
                )
                or {}
            )

            if not rows:
                # Lineage: classify the empty-state into a closed enum so
                # the verdict/hint reflect the real cause.
                if _raw_row_count == 0:
                    _empty_state = "no_symbols"
                    _empty_verdict = "no graph metrics available (corpus empty — run `roam index --force` to populate)"
                    _empty_hint = "Run `roam index --force` to populate symbols and graph_metrics."
                elif _after_tooling == 0:
                    _empty_state = "all_filtered_tooling"
                    _empty_verdict = (
                        f"no graph metrics survived tooling exclusion ({_raw_row_count} raw rows; "
                        "re-run with --include-tooling to see CI/dev/generated files)"
                    )
                    _empty_hint = "Re-run with `--include-tooling` to include CI/dev/generated files."
                elif _after_tests == 0:
                    # F3: all surviving rows were test-role and got filtered.
                    _empty_state = "all_filtered_tests"
                    _empty_verdict = (
                        f"no production symbols ({_test_filtered} test-role rows filtered; "
                        "re-run with --include-tests to see test fixtures)"
                    )
                    _empty_hint = "Re-run with `--include-tests` to include test-role symbols."
                else:
                    _empty_state = "all_filtered_framework"
                    _empty_verdict = (
                        f"no graph metrics survived framework exclusion ({_after_tooling} rows pre-filter; "
                        "drop --no-framework to see framework primitives)"
                    )
                    _empty_hint = "Drop `--no-framework` to include framework primitive names."

                if sarif_mode:
                    # W1209: SARIF output with empty results (rules catalogue
                    # still emitted so consumers can introspect the closed enum).
                    from roam.output.sarif import fan_to_sarif, write_sarif

                    click.echo(write_sarif(fan_to_sarif([])))
                    return
                if json_mode:
                    # W805-followup-C: empty-state disclosure (Pattern 2
                    # silent-fallback fix). State is now classified — the
                    # pre-fix path collapsed three distinct causes into one
                    # misleading "corpus empty" verdict.
                    click.echo(
                        to_json(
                            json_envelope(
                                "fan",
                                budget=token_budget,
                                summary={
                                    "verdict": _empty_verdict,
                                    "mode": mode,
                                    "items": 0,
                                    "partial_success": True,
                                    "state": _empty_state,
                                },
                                mode=mode,
                                items=[],
                                hint=_empty_hint,
                            )
                        )
                    )
                else:
                    click.echo(f"VERDICT: {_empty_verdict}")
                    click.echo(f"HINT: {_empty_hint}")
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
                    # F3: per-item test/prod role annotation (mirror of
                    # cmd_uses' ``scope`` field). With the default headline
                    # filter every shown row is "production"; under
                    # --include-tests both scopes appear so consumers can
                    # still filter client-side.
                    "scope": _row_scope(r["file_path"]),
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
            #
            # W607-X: pre-W89 schema (no ``findings`` table) is the
            # expected sqlite3.OperationalError -- caught locally so it
            # does NOT surface as a marker (graceful degradation). All
            # OTHER raise classes flow through _run_check and surface as
            # ``fan_emit_findings_symbol_failed:<exc_class>:<detail>``.
            if persist:

                def _persist_symbol():
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
                        return None

                _run_check("emit_findings_symbol", _persist_symbol, default=None)

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
                fan_envelope = _emit_symbol_json(
                    symbol_items,
                    rows,
                    mode,
                    include_tests,
                    _test_filtered,
                    token_budget,
                    _w607x_warnings_out,
                )
                click.echo(to_json(fan_envelope))
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
                        _row_scope(r["file_path"]),
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
            # F3: name the test-role rows dropped from the headline so the
            # split is visible in text mode too (loud lineage).
            if not include_tests and _test_filtered:
                click.echo(
                    f"NOTE: {_test_filtered} test-role symbol(s) excluded from ranking; "
                    "re-run with --include-tests to show them.\n"
                )
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
                        "scope",
                        "location",
                    ],
                    table_rows,
                )
            )

        else:  # file mode

            def _fetch_file_rows():
                # F3: oversample so the headline still has ``count`` entries
                # after test-role files are dropped (mirror of symbol mode's
                # 5x tooling-filter headroom). When tests are included no
                # filter runs, so the bare ``count`` limit is fine.
                _file_limit = count * 5 if not include_tests else count
                return conn.execute(
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
                    (_file_limit,),
                ).fetchall()

            rows = _run_check("fetch_file_rows", _fetch_file_rows, default=[]) or []

            # F3: split test-role files out of the headline ranking by default.
            # Same canonical is_test_file mechanism as symbol mode + cmd_uses.
            _file_prod_rows, _file_test_rows = _split_test_rows(rows, "path")
            _file_test_filtered = 0 if include_tests else len(_file_test_rows)
            if not include_tests:
                rows = _file_prod_rows
            rows = rows[:count]

            if not rows:
                # F3: distinguish "all surviving files were test-role and
                # got filtered" from a genuinely empty file_edges corpus, so
                # the verdict/hint name the real cause (Pattern 2 lineage).
                if not include_tests and _file_test_filtered:
                    _file_empty_state = "all_filtered_tests"
                    _file_empty_verdict = (
                        f"no production files ({_file_test_filtered} test-role files filtered; "
                        "re-run with --include-tests to see test files)"
                    )
                    _file_empty_hint = "Re-run with `--include-tests` to include test-role files."
                else:
                    _file_empty_state = "no_file_edges"
                    _file_empty_verdict = (
                        "no file edges available (corpus empty — run `roam index --force` to populate)"
                    )
                    _file_empty_hint = "Run `roam index` first."
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
                                    "verdict": _file_empty_verdict,
                                    "mode": mode,
                                    "items": 0,
                                    "partial_success": True,
                                    "state": _file_empty_state,
                                },
                                mode=mode,
                                items=[],
                                hint=_file_empty_hint,
                            )
                        )
                    )
                else:
                    click.echo(_file_empty_verdict)
                    click.echo(f"HINT: {_file_empty_hint}")
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
                    # F3: per-item test/prod role annotation (mirror of
                    # cmd_uses' ``scope`` field + symbol-mode above).
                    "scope": _row_scope(r["path"]),
                }
                for r in rows
            ]

            # W152: mirror cross-file architectural flags into the
            # findings registry. Runs ONLY with --persist.
            #
            # W607-X: pre-W89 schema (no ``findings`` table) is the
            # expected sqlite3.OperationalError -- caught locally so it
            # does NOT surface as a marker (graceful degradation). All
            # OTHER raise classes flow through _run_check and surface as
            # ``fan_emit_findings_file_failed:<exc_class>:<detail>``.
            if persist:

                def _persist_file():
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
                        return None

                _run_check("emit_findings_file", _persist_file, default=None)

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
                fan_envelope = _emit_file_json(
                    file_items,
                    rows,
                    mode,
                    include_tests,
                    _file_test_filtered,
                    token_budget,
                    _w607x_warnings_out,
                )
                click.echo(to_json(fan_envelope))
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
                        item["scope"],
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
            # F3: name the test-role files dropped from the headline.
            if not include_tests and _file_test_filtered:
                click.echo(
                    f"NOTE: {_file_test_filtered} test-role file(s) excluded from ranking; "
                    "re-run with --include-tests to show them.\n"
                )
            click.echo("=== Fan-in/Fan-out (file level) ===")
            click.echo(
                format_table(
                    ["path", "fan-in", "fan-out", "total", "flag", "scope"],
                    table_rows,
                )
            )
