"""``roam audit-trail-export`` — export the EU AI Act audit trail for procurement.

Reads ``.roam/audit-trail.jsonl`` and emits a procurement-friendly
markdown table, JSON array, or CSV. Supports optional date-range
filtering and verdict filtering.
"""

from __future__ import annotations

import csv
import io
import json as _json
from pathlib import Path

import click

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


def _build_integrity_summary(records: list[dict], path: Path) -> dict:
    """Compose the closing AuditIntegritySummary record.

    Format inspired by the SHA-256 chained log forensic-format conventions
    documented in https://dev.to/veritaschain/building-a-tamper-evident-audit-log-with-sha-256-hash-chains-zero-dependencies-h0b
    — a closing record that locks in the chain head + count + algorithm so
    a downstream consumer can verify "this is the trail at this moment in time".
    """
    import datetime as _dt
    import hashlib as _h

    chain_head = ""
    if path.exists():
        try:
            with path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 8192))
                tail = f.read().decode("utf-8", errors="replace")
            for line in tail.strip().split("\n"):
                if line.strip():
                    chain_head = _h.sha256(line.strip().encode("utf-8")).hexdigest()
        except OSError:
            pass

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
    help="Render only the top N actors ranked by BLOCK count (and total). Procurement-friendly hot list.",
)
@click.pass_context
def audit_trail_export(
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

    # --finalize is a side-effect operation: append the integrity summary
    # to the trail BEFORE rendering, so the rendered output reflects it too.
    if finalize and path.exists():
        existing = _load_records(path)
        summary_record = _build_integrity_summary(existing, path)
        with path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(summary_record, separators=(",", ":"), sort_keys=True) + "\n")

    records = _load_records(path)
    filtered = _filter_records(records, since=since, until=until, verdict_filter=verdict_filter)

    aggregate_data: dict | None = None
    top_actors_data: list[dict] | None = None
    if top_actors > 0:
        top_actors_data = _build_top_actors(filtered, top_actors)
        if fmt.lower() == "md":
            rendered = _render_top_actors_markdown(top_actors_data, path)
        elif fmt.lower() == "csv":
            rendered = _render_top_actors_csv(top_actors_data)
        else:
            rendered = _json.dumps(top_actors_data, indent=2, default=str)
    elif aggregate:
        aggregate_data = _aggregate_records(filtered)
        if fmt.lower() == "md":
            rendered = _render_aggregate_markdown(aggregate_data, path)
        elif fmt.lower() == "csv":
            rendered = _render_aggregate_csv(aggregate_data)
        else:
            rendered = _json.dumps(aggregate_data, indent=2, default=str)
    else:
        if fmt.lower() == "md":
            rendered = _render_markdown(filtered, path)
        elif fmt.lower() == "csv":
            rendered = _render_csv(filtered)
        else:
            rendered = _render_json(filtered)

    if top_actors > 0:
        verdict_text = f"top {len(top_actors_data or [])} actor(s) by BLOCK count"
    elif aggregate:
        verdict_text = f"aggregate over {len(filtered)} record(s)"
    else:
        verdict_text = f"{len(filtered)} record(s) exported"

    summary = {
        "verdict": verdict_text,
        "format": fmt.lower(),
        "aggregate": aggregate,
        "top_actors_limit": top_actors if top_actors > 0 else None,
        "input": str(path),
        "total_records": len(records),
        "filtered_records": len(filtered),
        "verdict_filter": verdict_filter,
        "since": since,
        "until": until,
    }

    if output_path:
        Path(output_path).write_text(rendered, encoding="utf-8")
        summary["output"] = output_path

    if json_mode:
        envelope_payload = {"summary": summary, "content": rendered}
        if aggregate_data is not None:
            envelope_payload["aggregate"] = aggregate_data
        if top_actors_data is not None:
            envelope_payload["top_actors"] = top_actors_data
        click.echo(to_json(json_envelope("audit-trail-export", **envelope_payload)))
    elif output_path:
        click.echo(f"VERDICT: {summary['verdict']} -> {output_path}")
    else:
        click.echo(rendered, nl=False)
