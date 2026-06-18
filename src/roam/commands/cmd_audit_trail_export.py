"""``roam audit-trail-export`` — export the EU AI Act audit trail for procurement.

Reads ``.roam/audit-trail.jsonl`` and emits a procurement-friendly
markdown table, JSON array, or CSV. Supports optional date-range
filtering and verdict filtering.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because audit-trail-export outputs are audit trail exports —
not per-location violations. SARIF is reserved for findings with
file:line coordinates; audit-trail-export's primary deliverable is the
procurement-friendly audit trail export document. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket C propagation plan +
W1148 audit memo.
"""

from __future__ import annotations

import csv
import io
import json as _json
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.audit_trail_helpers import (
    AUDIT_TRAIL_SCHEMA,
    DEFAULT_AUDIT_TRAIL_PATH,
    INTEGRITY_SUMMARY_SCHEMA,
)
from roam.commands.audit_trail_helpers import load_records as _load_records
from roam.output.formatter import json_envelope, to_json

_COLUMNS = [
    "timestamp",
    "actor",
    "verdict",
    "blast_radius",
    "ai_likelihood",
    "rule_violations_count",
    "git_sha",
    "diff_sha256",
    "intent_marker",
]


def _filter_records(
    records: list[dict],
    *,
    since: str | None,
    until: str | None,
    verdict_filter: str | None,
) -> list[dict]:
    """Apply ``--since``, ``--until``, and ``--verdict`` filters."""
    out = records
    if since:
        out = [r for r in out if (r.get("timestamp") or "") >= since]
    if until:
        out = [r for r in out if (r.get("timestamp") or "") <= until]
    if verdict_filter:
        wanted = {v.strip().upper() for v in verdict_filter.split(",")}
        out = [r for r in out if (r.get("verdict") or "").upper() in wanted]
    return out


def _render_markdown(records: list[dict], path: Path) -> str:
    if not records:
        return f"_No audit-trail records in `{path}`._\n"
    lines = [
        f"# Roam Audit Trail — {path}",
        "",
        f"**Records:** {len(records)} · "
        f"**First:** {records[0].get('timestamp', '?')} · "
        f"**Last:** {records[-1].get('timestamp', '?')}",
        "",
        "| # | Timestamp | Actor | Verdict | Blast | AI | Rule violations | Git SHA |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(records, 1):
        sha = (r.get("git_sha") or "")[:10]
        lines.append(
            f"| {i} | {r.get('timestamp', '?')} | "
            f"{r.get('actor', '?')} | "
            f"{r.get('verdict', '?')} | "
            f"{r.get('blast_radius', '?')} | "
            f"{r.get('ai_likelihood', '?')} | "
            f"{r.get('rule_violations_count', 0)} | "
            f"`{sha}` |"
        )
    lines.append("")
    lines.append("_Schema: `roam-audit-trail-v1`. SHA-256 chain verifiable with `roam audit-trail-verify`._")
    return "\n".join(lines) + "\n"


def _render_csv(records: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_COLUMNS)
    for r in records:
        writer.writerow([r.get(c, "") for c in _COLUMNS])
    return buf.getvalue()


def _render_json(records: list[dict]) -> str:
    return _json.dumps(records, indent=2, default=str)


def _aggregate_records(records: list[dict]) -> dict:
    """Bucket records by actor, repo, verdict, and year-month.

    Procurement reviewers love these tables — "Q1: 15 BLOCK, 3
    INTENTIONAL bypass" — without having to scan the JSONL by hand.
    Returns a nested dict suitable for both markdown and JSON rendering.
    """
    by_actor: dict[str, dict[str, int]] = {}
    by_repo: dict[str, dict[str, int]] = {}
    by_verdict: dict[str, int] = {}
    by_month: dict[str, dict[str, int]] = {}

    def _bump(bucket: dict, key: str, verdict: str) -> None:
        bucket.setdefault(key, {"INTENTIONAL": 0, "SAFE": 0, "REVIEW": 0, "BLOCK": 0, "OTHER": 0, "_total": 0})
        slot = verdict if verdict in bucket[key] else "OTHER"
        bucket[key][slot] = bucket[key].get(slot, 0) + 1
        bucket[key]["_total"] = bucket[key].get("_total", 0) + 1

    for r in records:
        verdict = (r.get("verdict") or "UNKNOWN").upper()
        actor = r.get("actor") or "<unknown>"
        repo = r.get("repo") or "<unknown>"
        ts = r.get("timestamp") or ""
        # Extract YYYY-MM (first 7 chars of an ISO timestamp).
        month = ts[:7] if len(ts) >= 7 else "<undated>"

        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
        _bump(by_actor, actor, verdict)
        _bump(by_repo, repo, verdict)
        _bump(by_month, month, verdict)

    # C.1.aaa — at-a-glance snapshot fields so procurement consumers can
    # answer "who/what/when triggered most" without parsing the full tables.
    def _top(bucket: dict[str, dict[str, int]]) -> dict | None:
        if not bucket:
            return None
        key, counts = max(bucket.items(), key=lambda kv: kv[1].get("_total", 0))
        return {"key": key, "count": counts.get("_total", 0)}

    snapshot = {
        "top_actor": _top(by_actor),
        "top_repo": _top(by_repo),
        "top_month": _top(by_month),
        "top_verdict": (
            {"key": max(by_verdict.items(), key=lambda kv: kv[1])[0], "count": max(by_verdict.values())}
            if by_verdict
            else None
        ),
    }

    return {
        "total_records": len(records),
        "by_verdict": dict(sorted(by_verdict.items())),
        "by_actor": dict(sorted(by_actor.items())),
        "by_repo": dict(sorted(by_repo.items())),
        "by_month": dict(sorted(by_month.items())),
        "snapshot": snapshot,
    }


def _render_aggregate_markdown(agg: dict, path: Path) -> str:
    """Render an aggregate report as markdown — one table per dimension."""
    lines = [
        f"# Audit Trail Aggregate Report — {path}",
        "",
        f"**Total records:** {agg['total_records']}",
    ]
    snap = agg.get("snapshot") or {}
    snap_bits: list[str] = []
    for label, key in (
        ("top verdict", "top_verdict"),
        ("top actor", "top_actor"),
        ("top month", "top_month"),
        ("top repo", "top_repo"),
    ):
        item = snap.get(key)
        if item:
            snap_bits.append(f"**{label}**: `{item['key']}` ({item['count']})")
    if snap_bits:
        lines.append("")
        lines.append(" · ".join(snap_bits))
    lines.append("")
    lines.append("## By verdict")
    lines.append("")
    lines.append("| Verdict | Count |")
    lines.append("|---|---|")
    for v, c in agg["by_verdict"].items():
        lines.append(f"| {v} | {c} |")

    def _table(title: str, dim_data: dict[str, dict[str, int]]) -> None:
        lines.append("")
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| Key | INTENTIONAL | SAFE | REVIEW | BLOCK | OTHER | Total |")
        lines.append("|---|---|---|---|---|---|---|")
        for key, counts in dim_data.items():
            lines.append(
                f"| {key} | {counts.get('INTENTIONAL', 0)} | {counts.get('SAFE', 0)} | "
                f"{counts.get('REVIEW', 0)} | {counts.get('BLOCK', 0)} | "
                f"{counts.get('OTHER', 0)} | {counts.get('_total', 0)} |"
            )

    _table("By month (year-month bucket)", agg["by_month"])
    _table("By actor", agg["by_actor"])
    _table("By repo", agg["by_repo"])

    lines.append("")
    lines.append("_Schema: `roam-audit-trail-v1`. Generated by `roam audit-trail-export --aggregate`._")
    return "\n".join(lines) + "\n"


def _compute_chain_head(path: Path) -> str:
    """Read the trail tail and compute the SHA-256 of the last non-blank line.

    Extracted from ``_build_integrity_summary`` so W607-AP can wrap this
    I/O boundary as its own substrate phase (``compute_chain_head``).
    Returns "" when the path does not exist or is empty.

    W607-AP Pattern-2 elimination: the previous in-line ``except OSError:
    pass`` is gone; an OSError now propagates to the ``_run_check_ap``
    wrapper, surfacing a structured marker instead of silently degrading
    to an empty chain_head.
    """
    import hashlib as _h

    if not path.exists():
        return ""
    chain_head = ""
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 8192))
        tail = f.read().decode("utf-8", errors="replace")
    for line in tail.strip().split("\n"):
        if line.strip():
            chain_head = _h.sha256(line.strip().encode("utf-8")).hexdigest()
    return chain_head


def _build_integrity_summary(records: list[dict], path: Path, chain_head: str = "") -> dict:
    """Compose the closing AuditIntegritySummary record.

    Format inspired by the SHA-256 chained log forensic-format conventions
    documented in https://dev.to/veritaschain/building-a-tamper-evident-audit-log-with-sha-256-hash-chains-zero-dependencies-h0b
    — a closing record that locks in the chain head + count + algorithm so
    a downstream consumer can verify "this is the trail at this moment in time".

    W607-AP: ``chain_head`` is now computed by the caller via
    ``_compute_chain_head`` (a separate substrate phase). Default "" keeps
    direct callers (tests, future helpers) working without refactoring.
    """
    import datetime as _dt

    return {
        "schema": INTEGRITY_SUMMARY_SCHEMA,
        "record_schema": AUDIT_TRAIL_SCHEMA,
        "hash_algorithm": "sha256",
        "event_count": len(records),
        "first_timestamp": records[0].get("timestamp") if records else None,
        "last_timestamp": records[-1].get("timestamp") if records else None,
        "chain_head": chain_head,
        "summary_emitted_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "summary_note": "Closing integrity summary — appending records after this line restarts the chain section.",
    }


def _build_top_actors(records: list[dict], limit: int) -> list[dict]:
    """Rank actors by BLOCK count first, total verdict count as tiebreaker.

    Returns a list of ``{actor, total, block, review, safe, intentional, other}``
    dicts truncated to ``limit``. Used by `--top-actors N`.
    """
    by_actor: dict[str, dict[str, int]] = {}
    for r in records:
        actor = r.get("actor") or "<unknown>"
        verdict = (r.get("verdict") or "UNKNOWN").upper()
        bucket = by_actor.setdefault(
            actor,
            {"actor": actor, "total": 0, "BLOCK": 0, "REVIEW": 0, "SAFE": 0, "INTENTIONAL": 0, "OTHER": 0},
        )
        slot = verdict if verdict in ("BLOCK", "REVIEW", "SAFE", "INTENTIONAL") else "OTHER"
        bucket[slot] += 1
        bucket["total"] += 1

    ranked = sorted(by_actor.values(), key=lambda b: (-b["BLOCK"], -b["total"]))
    return ranked[:limit] if limit > 0 else ranked


def _render_top_actors_markdown(actors: list[dict], path: Path) -> str:
    """Procurement-friendly hot list: who triggered the most BLOCKs."""
    if not actors:
        return f"_No audit-trail records in `{path}`._\n"
    lines = [
        f"# Top actors by BLOCK count — {path}",
        "",
        f"**Showing top {len(actors)} actor(s).**",
        "",
        "| Rank | Actor | BLOCK | REVIEW | SAFE | INTENTIONAL | Other | Total |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, a in enumerate(actors, 1):
        lines.append(
            f"| {i} | {a['actor']} | {a['BLOCK']} | {a['REVIEW']} | {a['SAFE']} | "
            f"{a['INTENTIONAL']} | {a['OTHER']} | {a['total']} |"
        )
    lines.append("")
    lines.append(
        "_Ranked by BLOCK count first, total record count as tiebreaker. "
        "Actors with no BLOCKs are sorted by total record count._"
    )
    return "\n".join(lines) + "\n"


def _render_top_actors_csv(actors: list[dict]) -> str:
    """Flat CSV: rank, actor, block, review, safe, intentional, other, total."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["rank", "actor", "block", "review", "safe", "intentional", "other", "total"])
    for i, a in enumerate(actors, 1):
        writer.writerow([i, a["actor"], a["BLOCK"], a["REVIEW"], a["SAFE"], a["INTENTIONAL"], a["OTHER"], a["total"]])
    return buf.getvalue()


def _render_aggregate_csv(agg: dict) -> str:
    """Flat CSV: dimension, key, verdict, count."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["dimension", "key", "verdict", "count"])
    for v, c in agg["by_verdict"].items():
        writer.writerow(["verdict", v, "_total", c])
    for dim_name in ("by_month", "by_actor", "by_repo"):
        for key, counts in agg[dim_name].items():
            for verdict, count in counts.items():
                if verdict == "_total" or count == 0:
                    continue
                writer.writerow([dim_name, key, verdict, count])
    return buf.getvalue()


@roam_capability(
    name="audit-trail-export",
    category="workflow",
    summary="Export the audit trail for procurement / compliance review",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "compliance"),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=False,
    requires_index=True,
)
@click.command(name="audit-trail-export")
@click.option(
    "--input",
    "input_path",
    type=click.Path(),
    default=None,
    help=f"Audit trail JSONL path (default: {DEFAULT_AUDIT_TRAIL_PATH}).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["md", "json", "csv"], case_sensitive=False),
    default="md",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--since",
    default=None,
    help="ISO 8601 timestamp lower bound (e.g. 2026-01-01T00:00:00Z).",
)
@click.option(
    "--until",
    default=None,
    help="ISO 8601 timestamp upper bound.",
)
@click.option(
    "--verdict",
    "verdict_filter",
    default=None,
    help="Comma-separated verdicts to keep (e.g. REVIEW,BLOCK).",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(),
    default=None,
    help="Write rendered output to file (default: stdout).",
)
@click.option(
    "--aggregate",
    is_flag=True,
    help="Render aggregate counts (by actor / repo / verdict / month) instead of per-record list.",
)
@click.option(
    "--finalize",
    is_flag=True,
    help="Append a closing AuditIntegritySummary record to the trail (locks the chain head + event count).",
)
@click.option(
    "--top-actors",
    "top_actors",
    type=int,
    default=0,
    help="Render only the top <N> actors ranked by BLOCK count (and total). Procurement-friendly hot list.",  # W1117-followup
)
@click.pass_context
def audit_trail_export_cmd(
    ctx,
    input_path: str | None,
    fmt: str,
    since: str | None,
    until: str | None,
    verdict_filter: str | None,
    output_path: str | None,
    aggregate: bool,
    finalize: bool,
    top_actors: int,
) -> None:
    """Export the audit trail for procurement / compliance review.

    \b
    Examples:
      roam audit-trail-export                              # markdown to stdout
      roam audit-trail-export --format csv -o trail.csv
      roam audit-trail-export --since 2026-01-01 --verdict BLOCK,REVIEW
      roam audit-trail-export --aggregate                  # bucket counts by month/actor/repo
      roam audit-trail-export --aggregate --format csv     # aggregate as CSV
      roam --json audit-trail-export                       # envelope around the JSON

    Use after ``roam audit-trail-verify`` confirms chain integrity.
    The ``--aggregate`` flag is the procurement-friendly summary path:
    rather than the per-record list, it emits "Q1: 15 BLOCK,
    3 INTENTIONAL bypass" tables per month / actor / repo.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    path = Path(input_path) if input_path else DEFAULT_AUDIT_TRAIL_PATH

    # --- W607-AP: substrate-CALL marker plumbing -------------------------
    # cmd_audit_trail_export is the EXPORT leg of the audit-trail family
    # quartet (W607-AD attest produces, W607-AI audit_trail_verify checks
    # chain integrity, W607-AL audit_trail_conformance scores Article-12
    # conformance, W607-AP audit_trail_export -- THIS wave -- projects
    # the trail to procurement-friendly markdown / JSON / CSV).
    #
    # Substrate boundaries wrapped here:
    #
    #   load_records_finalize                         (read for --finalize)
    #   compute_chain_head                            (tail-read I/O boundary)
    #   build_integrity_summary                       (closing record build)
    #   append_integrity_summary                      (--finalize append I/O)
    #   load_records                                  (JSONL trail-read)
    #   filter_records                                (since/until/verdict)
    #   aggregate_records                             (bucketing computation)
    #   build_top_actors                              (ranking computation)
    #   render_output                                 (projection: md/csv/json)
    #   atomic_write_text                             (I/O boundary)
    #
    # The PRIOR code had ONE Pattern-2 silent-fallback block (``except
    # OSError: pass`` in _build_integrity_summary's tail-read). It is
    # replaced by extracting the tail-read into ``_compute_chain_head``
    # and routing it through ``_run_check_ap("compute_chain_head", ...)``
    # so the disclosure channel names the I/O failure instead of
    # silently degrading to an empty chain_head.
    #
    # Each raise becomes an
    # ``audit_trail_export_<phase>_failed:<exc_class>:<detail>`` marker
    # via ``_w607ap_warnings_out``. partial_success flips on any
    # non-empty bucket. Empty bucket on the clean path keeps the
    # envelope shape byte-identical to the pre-W607-AP command.
    #
    # QUARTET-CLOSURE milestone: with W607-AD (attest, produce),
    # W607-AI (verify), W607-AL (conform), and W607-AP (export -- this
    # wave), the audit-trail family is W607-plumbed end-to-end. A raise
    # anywhere in {produce, verify, conform, export} surfaces a marker
    # rather than crashing.
    _w607ap_warnings_out: list[str] = []

    def _run_check_ap(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AP marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an
        ``audit_trail_export_<phase>_failed:<exc_class>:<detail>`` marker
        via ``_w607ap_warnings_out`` and return *default* -- the envelope
        still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ap_warnings_out.append(f"audit_trail_export_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-CR: aggregation-phase marker plumbing (additive) -----------
    # cmd_audit_trail_export is the EXPORT leg of the AUDIT-TRAIL FAMILY
    # (cmd_audit_trail_verify W607-AI+CN; cmd_audit_trail_conformance
    # W607-AL+CO; cmd_audit_trail_export W607-AP+CR -- this layer).
    # W607-AP plumbed the substrate-CALL layer (10 substrate boundaries:
    # load_records / compute_chain_head / build_integrity_summary /
    # append_integrity_summary / filter_records / aggregate_records /
    # build_top_actors / render_output / atomic_write_text). W607-CR adds
    # the AGGREGATION-PHASE layer on top:
    #
    #   score_classify       -- bucket records (records vs aggregate vs top)
    #                           into RECORDS_EXPORTED / EMPTY / DEGRADED state
    #   compute_predicate    -- per-format record totals (total/filtered)
    #   compute_verdict      -- composite verdict text ("N record(s) exported"
    #                           / "aggregate over N record(s)" / "top N actor(s)")
    #   serialize_envelope   -- json_envelope("audit-trail-export", ...) projection
    #
    # Marker family ``audit_trail_export_*`` -- same family as W607-AP
    # (additive, not a separate prefix). Empty bucket -> byte-identical
    # envelope on the success path. Both buckets are combined at envelope-
    # emit time so consumers see the full degradation lineage in marker-
    # emission order. The additive bucket stays distinguishable via its
    # phase names (``score_classify`` / ``compute_predicate`` /
    # ``compute_verdict`` / ``serialize_envelope``).
    #
    # AUDIT-TRAIL FAMILY 3-WAY pairing -- closes the family at aggregation
    # layer:
    #   cmd_audit_trail_verify       (W607-AI substrate + W607-CN aggregation)
    #   cmd_audit_trail_conformance  (W607-AL substrate + W607-CO aggregation)
    #   cmd_audit_trail_export       (W607-AP substrate + W607-CR THIS)
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` kwarg in a
    # ``_run_check_cr(...)`` call MUST be a literal constant (not a
    # computed expression like ``len(filtered) if ...``). A computed
    # default expression evaluates BEFORE the wrap call, so a raise
    # inside the expression escapes the try-block. cmd_sbom's W607-CG
    # sealed this axis. cmd_taint's W607-CJ added the 5th discipline
    # (move ``len()`` INSIDE the closure, not at the kwarg-bind site).
    #
    # W607-AP/CR PHASE-NAME COLLISION (W607-CH): the substrate-CALL layer
    # has NO ``serialize_envelope`` phase (W607-AP wraps ``render_output``
    # and ``atomic_write_text`` for I/O but NOT json_envelope). So no
    # rename is required. If a future W607-AP revision adds a
    # ``serialize_envelope`` phase, rename W607-CR's to ``build_envelope``
    # to avoid collision.
    #
    # MULTI-FORMAT NOTE: cmd_audit_trail_export has 3 emit paths
    # (CSV / JSON / markdown via ``--format``). W607-AP wraps the
    # rendering phase via ``render_output`` / ``aggregate_records`` /
    # ``build_top_actors``. W607-CR's aggregation phases live in the
    # POST-rendering envelope-build flow and are format-agnostic (they
    # operate on ``filtered`` counts + ``rendered`` content), so the
    # marker family stays clean across all 3 formats by construction.
    _w607cr_warnings_out: list[str] = []

    def _run_check_cr(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CR marker emission.

        Mirror of ``_run_check_ap`` shape (same
        ``audit_trail_export_<phase>_failed:`` marker family) but writes
        into ``_w607cr_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cr_warnings_out.append(f"audit_trail_export_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --finalize is a side-effect operation: append the integrity summary
    # to the trail BEFORE rendering, so the rendered output reflects it too.
    if finalize and path.exists():
        existing = _run_check_ap("load_records_finalize", _load_records, path, default=[])
        chain_head = _run_check_ap("compute_chain_head", _compute_chain_head, path, default="")
        summary_record = _run_check_ap(
            "build_integrity_summary",
            _build_integrity_summary,
            existing,
            path,
            chain_head,
            default=None,
        )
        if summary_record is not None:

            def _append_summary():
                with path.open("a", encoding="utf-8") as f:
                    f.write(_json.dumps(summary_record, separators=(",", ":"), sort_keys=True) + "\n")
                return True

            _run_check_ap("append_integrity_summary", _append_summary, default=False)

    records = _run_check_ap("load_records", _load_records, path, default=[])
    filtered = _run_check_ap(
        "filter_records",
        _filter_records,
        records,
        since=since,
        until=until,
        verdict_filter=verdict_filter,
        default=list(records),
    )

    aggregate_data: dict | None = None
    top_actors_data: list[dict] | None = None

    def _render_top_actors_branch():
        data = _build_top_actors(filtered, top_actors)
        if fmt.lower() == "md":
            return data, _render_top_actors_markdown(data, path)
        elif fmt.lower() == "csv":
            return data, _render_top_actors_csv(data)
        else:
            return data, _json.dumps(data, indent=2, default=str)

    def _render_aggregate_branch():
        data = _aggregate_records(filtered)
        if fmt.lower() == "md":
            return data, _render_aggregate_markdown(data, path)
        elif fmt.lower() == "csv":
            return data, _render_aggregate_csv(data)
        else:
            return data, _json.dumps(data, indent=2, default=str)

    def _render_records_branch():
        if fmt.lower() == "md":
            return _render_markdown(filtered, path)
        elif fmt.lower() == "csv":
            return _render_csv(filtered)
        else:
            return _render_json(filtered)

    if top_actors > 0:
        # W607-AP: combined ranking + projection -- a raise inside either
        # _build_top_actors or _render_top_actors_* surfaces one marker.
        result = _run_check_ap(
            "build_top_actors",
            _render_top_actors_branch,
            default=([], ""),
        )
        top_actors_data, rendered = result
    elif aggregate:
        result = _run_check_ap(
            "aggregate_records",
            _render_aggregate_branch,
            default=({}, ""),
        )
        aggregate_data, rendered = result
    else:
        rendered = _run_check_ap("render_output", _render_records_branch, default="")

    # W607-CR -- score_classify boundary. Wraps the export-mode bucketing
    # (records-list / aggregate / top-actors) into a state label
    # (RECORDS_EXPORTED / EMPTY / DEGRADED) so a downstream refactor of
    # the mode-selection logic surfaces a marker rather than crashing.
    # Floor returns documented zero counts matching the empty-trail
    # branch shape so downstream verdict / compute_predicate stay
    # non-null.
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(filtered)`` /
    # ``len(top_actors_data or [])`` are computed INSIDE the wrapped
    # closure rather than at the call site -- a _BadList whose
    # ``__len__`` or ``__iter__`` raises would otherwise escape the
    # try-block at kwarg-bind time. W978 5th-discipline (cmd_taint
    # W607-CJ): move ``len()`` INSIDE the closure.
    def _score_classify_export(_filtered, _top_actors_data, _aggregate_data, _top_actors_limit, _aggregate_mode):
        _filtered_n = len(_filtered) if _filtered is not None else 0
        if _top_actors_limit > 0:
            _top_n = len(_top_actors_data) if _top_actors_data is not None else 0
            _mode = "top_actors"
            _state = "RECORDS_EXPORTED" if _top_n else "EMPTY"
        elif _aggregate_mode:
            _mode = "aggregate"
            _state = "RECORDS_EXPORTED" if _filtered_n else "EMPTY"
        else:
            _mode = "records"
            _state = "RECORDS_EXPORTED" if _filtered_n else "EMPTY"
        return {"mode": _mode, "state": _state, "filtered_n": _filtered_n}

    _score_dict = _run_check_cr(
        "score_classify",
        _score_classify_export,
        filtered,
        top_actors_data,
        aggregate_data,
        top_actors,
        aggregate,
        default={"mode": "records", "state": "DEGRADED", "filtered_n": 0},
    )

    # W607-CR -- compute_verdict boundary. Wraps the verdict-string
    # assembly so a downstream f-string refactor (non-int counts from a
    # vocabulary refactor, or a __format__-raising sentinel) surfaces a
    # marker rather than crashing the envelope. Floor must NOT re-
    # interpolate the same values that tripped the closure (W978 first-
    # hypothesis). Use the literal "audit-trail-export completed" floor
    # (LAW 6 still holds: the line works standalone).
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: ``top_actors_data`` /
    # ``filtered`` / ``top_actors`` / ``aggregate`` are passed as raw
    # args; ``len()`` lives INSIDE the closure (cmd_taint W607-CJ
    # 5th-discipline anchor).
    def _build_verdict_str(_top_actors_data, _filtered, _top_actors_limit, _aggregate_mode):
        if _top_actors_limit > 0:
            _n = len(_top_actors_data) if _top_actors_data is not None else 0
            return f"top {_n} actor(s) by BLOCK count"
        if _aggregate_mode:
            return f"aggregate over {len(_filtered)} record(s)"
        return f"{len(_filtered)} record(s) exported"

    verdict_text = _run_check_cr(
        "compute_verdict",
        _build_verdict_str,
        top_actors_data,
        filtered,
        top_actors,
        aggregate,
        default="audit-trail-export completed",
    )

    # W607-CR -- compute_predicate boundary. Wraps the per-format record
    # totals extraction so a future ``records[]`` / ``filtered[]`` schema
    # refactor that drops or renames count fields surfaces a marker
    # rather than crashing the envelope. Floor to documented zero-counts
    # matching the empty-trail branch shape so downstream summary fields
    # stay non-null. W978 discipline: ``default=`` is a literal dict, NOT
    # a computed expression over the (potentially poisoned) inputs.
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(records)`` / ``len(filtered)``
    # are computed INSIDE the wrapped closure -- passing the raw lists
    # keeps the kwarg-bind step pure (no ``__len__`` call until we're
    # inside the try-block). cmd_taint W607-CJ 5th-discipline anchor.
    def _compute_predicate_fields(_records, _filtered) -> dict:
        return {
            "total_records": len(_records),
            "filtered_records": len(_filtered),
        }

    _pred_fields = _run_check_cr(
        "compute_predicate",
        _compute_predicate_fields,
        records,
        filtered,
        default={"total_records": 0, "filtered_records": 0},
    )

    # W978 KWARG-DEFAULT EAGERNESS NOTE: do NOT use
    # ``_pred_fields.get("filtered_records", len(filtered))`` -- the
    # second arg evaluates EAGERLY (Python evaluates .get's defaults at
    # the call site), which would re-raise on a __len__-poisoned
    # ``filtered`` sentinel. _pred_fields ALWAYS carries the keys
    # (either real value or floor 0), so a bare lookup is correct.
    summary = {
        "verdict": verdict_text,
        "format": fmt.lower(),
        "aggregate": aggregate,
        "top_actors_limit": top_actors if top_actors > 0 else None,
        "input": str(path),
        "total_records": _pred_fields["total_records"],
        "filtered_records": _pred_fields["filtered_records"],
        "verdict_filter": verdict_filter,
        "since": since,
        "until": until,
        # W607-CR: surface the score_classify result on the envelope so
        # consumers can read the export mode + state without re-deriving
        # from raw filtered/records counts.
        "export_mode": _score_dict.get("mode", "records"),
        "export_state": _score_dict.get("state", "RECORDS_EXPORTED"),
    }

    if output_path:
        # Atomic write — the audit-trail export is an evidence artifact
        # consumed by downstream compliance tooling (`audit-trail-verify`,
        # OSCAL projection). A torn write would silently corrupt the
        # exported chain. Route through atomic_io (W880-shaped fix).
        from roam.atomic_io import atomic_write_text

        _run_check_ap(
            "atomic_write_text",
            atomic_write_text,
            Path(output_path),
            rendered,
            default=None,
        )
        summary["output"] = output_path

    # W607-AP / W607-CR: thread substrate-CALL markers AND aggregation-
    # phase markers onto BOTH summary.warnings_out AND top-level
    # envelope.warnings_out so consumers reading either surface see the
    # full disclosure lineage. Both buckets share the canonical
    # ``audit_trail_export_*`` marker family (W607-CR is additive, not a
    # separate prefix); the additive bucket stays distinguishable via
    # its phase names (``score_classify`` / ``compute_predicate`` /
    # ``compute_verdict`` / ``serialize_envelope``). Non-empty combined
    # bucket flips partial_success. Empty combined bucket on the clean
    # path keeps the envelope byte-identical to the pre-W607-AP/CR
    # command (hash-stable happy path).
    _combined_warnings_out = list(_w607ap_warnings_out) + list(_w607cr_warnings_out)
    if _combined_warnings_out:
        summary["warnings_out"] = list(_combined_warnings_out)
        summary["partial_success"] = True

    if json_mode:
        envelope_payload = {"summary": summary, "content": rendered}
        if aggregate_data is not None:
            envelope_payload["aggregate"] = aggregate_data
        if top_actors_data is not None:
            envelope_payload["top_actors"] = top_actors_data
        # W607-AP / W607-CR: mirror BOTH substrate-CALL markers AND
        # aggregation-phase markers at the top level too so a consumer
        # reading envelope.warnings_out (rather than
        # envelope.summary.warnings_out) sees the same disclosure.
        if _combined_warnings_out:
            envelope_payload["warnings_out"] = list(_combined_warnings_out)

        # W607-CR -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("audit-trail-export", ...)`` would
        # otherwise crash AFTER all substrate + aggregation signals were
        # already gathered. Floor to a minimal envelope stub so consumers
        # still receive a parseable JSON object with the marker attached
        # + the canonical command name. Mirror of cmd_taint's W607-CJ /
        # cmd_audit_trail_conformance's W607-CO serialize_envelope floor
        # pattern.
        _envelope_floor: dict = {
            "command": "audit-trail-export",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict_text,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        _envelope = _run_check_cr(
            "serialize_envelope",
            json_envelope,
            "audit-trail-export",
            default=_envelope_floor,
            **envelope_payload,
        )
        # W607-CR -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``audit_trail_export_serialize_envelope_failed:`` marker was
        # appended to ``_w607cr_warnings_out`` and the floor stub carries
        # only the pre-raise combined list. Rebuild the floor stub's
        # warnings_out so the new marker reaches the JSON output. Clean
        # path -> envelope is the real json_envelope return value, no
        # rebuild needed.
        if _envelope is _envelope_floor and _w607cr_warnings_out:
            _combined_warnings_out = list(_w607ap_warnings_out) + list(_w607cr_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            _envelope = _envelope_floor

        click.echo(to_json(_envelope))
    elif output_path:
        click.echo(f"VERDICT: {summary['verdict']} -> {output_path}")
    else:
        click.echo(rendered, nl=False)
