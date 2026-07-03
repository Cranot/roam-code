"""Static HTML quality dashboard joining 3-way bench + tool-call + mode-usage + regen telemetry.

Reads four optional TSV inputs from /var/log/roam-dogfood (overridable for tests)
and emits one self-contained HTML page summarising the last N days of per-mode
quality signals. Stdlib-only, no jinja/flask/pandas.

Schemas (header rows expected):
  ab-bench.tsv:       date  project  task  variant  calls  seconds  output_tokens  roam_calls
  all-tool-calls.tsv: date  time_utc  session_id  cwd  tool_name  input_chars  output_chars  is_error  agent_mode
  mode-usage.tsv:     date  session_id  agent_mode  total_tokens  cost_usd
  regen-signal.tsv:   date  session_id  agent_mode  regen_count

Missing files render a "no data" placeholder rather than crashing.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import os
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

DEFAULT_BENCH_TSV = "/var/log/roam-dogfood/ab-bench.tsv"
DEFAULT_TOOL_CALLS_TSV = "/var/log/roam-dogfood/all-tool-calls.tsv"
DEFAULT_MODE_USAGE_TSV = "/var/log/roam-dogfood/mode-usage.tsv"
DEFAULT_REGEN_TSV = "/var/log/roam-dogfood/regen-signal.tsv"
DEFAULT_OUT = "/var/log/roam-dogfood/dashboard.html"

CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       max-width: 1100px; margin: 24px auto; padding: 0 16px; color: #1f2328; }
h1 { font-size: 22px; border-bottom: 1px solid #d0d7de; padding-bottom: 8px; }
h2 { font-size: 17px; margin-top: 28px; border-bottom: 1px solid #eaecef; padding-bottom: 4px; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; }
th, td { border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; font-weight: 600; }
td.num, th.num { text-align: right; font-family: ui-monospace, "SF Mono", Menlo, monospace; }
.missing { color: #6e7781; font-style: italic; }
.ok { color: #1a7f37; }
.bad { color: #d1242f; }
footer { margin-top: 32px; color: #6e7781; font-size: 12px; border-top: 1px solid #eaecef; padding-top: 12px; }
code { background: #f6f8fa; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
"""


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _read_tsv(path: str) -> tuple[list[dict[str, str]], bool]:
    """Return (rows, present). Missing files return ([], False)."""
    if not path or not os.path.exists(path):
        return [], False
    rows: list[dict[str, str]] = []
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                rows.append(row)
    except OSError:
        return [], False
    return rows, True


def _parse_date(s: str) -> dt.date | None:
    if not s:
        return None
    # Accept ISO date or full ISO timestamp.
    head = s.split("T", 1)[0].split(" ", 1)[0]
    try:
        return dt.date.fromisoformat(head)
    except ValueError:
        return None


def _filter_since(rows: Iterable[dict[str, str]], since: dt.date, date_key: str = "date") -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in rows:
        d = _parse_date(r.get(date_key, ""))
        if d is None or d >= since:
            out.append(r)
    return out


def _to_float(s: str) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _to_int(s: str) -> int | None:
    v = _to_float(s)
    return int(v) if v is not None else None


def _median_or_zero(xs: list[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def _mean_or_zero(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def render_header(generated_at: dt.datetime, since: dt.date, file_status: dict[str, tuple[str, bool]]) -> str:
    rows = []
    for label, (path, present) in file_status.items():
        cls = "ok" if present else "bad"
        word = "present" if present else "MISSING"
        rows.append(
            f"<tr><td>{html.escape(label)}</td>"
            f"<td><code>{html.escape(path)}</code></td>"
            f"<td class='{cls}'>{word}</td></tr>"
        )
    body = "".join(rows)
    return (
        "<h1>roam-code quality dashboard</h1>"
        f"<p>generated at <code>{generated_at.isoformat(timespec='seconds')}</code> · "
        f"since <code>{since.isoformat()}</code></p>"
        "<table><thead><tr><th>input</th><th>path</th><th>status</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def render_per_mode_summary(
    mode_usage_rows: list[dict[str, str]],
    regen_rows: list[dict[str, str]],
    mode_usage_path: str,
    regen_path: str,
    mode_usage_present: bool,
    regen_present: bool,
) -> str:
    title = "<h2>Per-mode last-7d summary</h2>"
    if not mode_usage_present and not regen_present:
        msg = (
            f"<p class='missing'>no data — files missing: "
            f"<code>{html.escape(mode_usage_path)}</code>, "
            f"<code>{html.escape(regen_path)}</code></p>"
        )
        return title + msg

    # Group usage by (mode, session_id).
    sessions_by_mode: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for r in mode_usage_rows:
        mode = r.get("agent_mode", "") or "unknown"
        sid = r.get("session_id", "")
        if not sid:
            continue
        sess = sessions_by_mode[mode].setdefault(sid, {"tokens": 0.0, "cost": 0.0})
        tokens = _to_float(r.get("total_tokens", "")) or 0.0
        cost = _to_float(r.get("cost_usd", "")) or 0.0
        sess["tokens"] += tokens
        sess["cost"] += cost

    # Regen lookup: (mode, sid) -> regen_count.
    regen_by_session: dict[tuple[str, str], float] = {}
    for r in regen_rows:
        mode = r.get("agent_mode", "") or "unknown"
        sid = r.get("session_id", "")
        if not sid:
            continue
        rc = _to_float(r.get("regen_count", "")) or 0.0
        regen_by_session[(mode, sid)] = regen_by_session.get((mode, sid), 0.0) + rc

    # Pre-compute per-mode regen lists once; summary loop then uses O(1) lookups.
    regen_by_mode: dict[str, list[float]] = defaultdict(list)
    for (mode, _sid), count in regen_by_session.items():
        regen_by_mode[mode].append(count)

    # We don't have explicit "turns per session" in mode-usage; use total_tokens
    # as a proxy *only* if no other signal — but here we treat token totals
    # as the comparable per-session quantity and report median.
    rows_html: list[str] = []
    all_modes = sorted(set(sessions_by_mode.keys()) | {m for (m, _) in regen_by_session.keys()})
    for mode in all_modes:
        sess_map = sessions_by_mode.get(mode, {})
        n = len(sess_map)
        token_totals = [v["tokens"] for v in sess_map.values()]
        cost_totals = [v["cost"] for v in sess_map.values()]
        median_tokens = _median_or_zero(token_totals)
        mean_cost = _mean_or_zero(cost_totals)
        mode_regens = regen_by_mode.get(mode, [])
        mean_regen = _mean_or_zero(mode_regens)
        rows_html.append(
            f"<tr><td>{html.escape(mode)}</td>"
            f"<td class='num'>{n}</td>"
            f"<td class='num'>{median_tokens:,.0f}</td>"
            f"<td class='num'>${mean_cost:,.4f}</td>"
            f"<td class='num'>{mean_regen:,.2f}</td></tr>"
        )

    body = "".join(rows_html) if rows_html else "<tr><td colspan='5' class='missing'>no rows in window</td></tr>"
    return (
        title
        + "<table><thead><tr>"
        + "<th>mode</th><th class='num'>n sessions</th>"
        + "<th class='num'>median tokens/session</th>"
        + "<th class='num'>mean cost/session</th>"
        + "<th class='num'>mean regen/session</th>"
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table>"
    )


def render_per_task_variant(
    bench_rows: list[dict[str, str]],
    bench_path: str,
    bench_present: bool,
) -> str:
    title = "<h2>Per-task variant comparison</h2>"
    if not bench_present:
        return title + f"<p class='missing'>no data — file missing: <code>{html.escape(bench_path)}</code></p>"

    # Aggregate per (task, variant): turns (calls), statuses.
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"turns": [], "statuses": defaultdict(int)})
    for r in bench_rows:
        task = r.get("task", "") or "?"
        variant = r.get("variant", "") or "?"
        # Prefer canonical 'turns' if present, else 'calls' (real schema).
        turns = _to_float(r.get("turns", "")) or _to_float(r.get("calls", ""))
        if turns is not None:
            grouped[(task, variant)]["turns"].append(turns)
        status = r.get("status", "") or "ok"
        grouped[(task, variant)]["statuses"][status] += 1

    tasks = sorted({t for (t, _) in grouped.keys()})
    variants = ["compile", "roam", "vanilla"]

    head = "<tr><th>task</th>" + "".join(f"<th>{v}</th>" for v in variants) + "</tr>"
    body_rows: list[str] = []
    for task in tasks:
        cells = [f"<td>{html.escape(task)}</td>"]
        for v in variants:
            cell = grouped.get((task, v))
            if not cell:
                cells.append("<td class='missing'>—</td>")
                continue
            median_turns = _median_or_zero(cell["turns"])
            status_bits = ", ".join(f"{k}={n}" for k, n in sorted(cell["statuses"].items()))
            cells.append(f"<td>median turns <b>{median_turns:.1f}</b><br>{html.escape(status_bits)}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    body = "".join(body_rows) if body_rows else "<tr><td colspan='4' class='missing'>no rows in window</td></tr>"
    return title + "<table><thead>" + head + "</thead><tbody>" + body + "</tbody></table>"


def render_mode_usage_topn(
    mode_usage_rows: list[dict[str, str]],
    mode_usage_path: str,
    mode_usage_present: bool,
    top_n: int = 10,
) -> str:
    title = f"<h2>Mode usage Top {top_n} sessions</h2>"
    if not mode_usage_present:
        return title + f"<p class='missing'>no data — file missing: <code>{html.escape(mode_usage_path)}</code></p>"

    # Aggregate by session.
    by_session: dict[str, dict[str, Any]] = {}
    for r in mode_usage_rows:
        sid = r.get("session_id", "")
        if not sid:
            continue
        mode = r.get("agent_mode", "") or "unknown"
        tokens = _to_float(r.get("total_tokens", "")) or 0.0
        cost = _to_float(r.get("cost_usd", "")) or 0.0
        cur = by_session.setdefault(sid, {"mode": mode, "tokens": 0.0, "cost": 0.0})
        cur["tokens"] += tokens
        cur["cost"] += cost
        # If multiple modes per session, keep the highest-tokens one.
        cur["mode"] = mode

    ranked = sorted(by_session.items(), key=lambda kv: kv[1]["tokens"], reverse=True)[:top_n]
    if not ranked:
        body = "<tr><td colspan='4' class='missing'>no rows in window</td></tr>"
    else:
        body = "".join(
            f"<tr><td><code>{html.escape(sid)}</code></td>"
            f"<td>{html.escape(v['mode'])}</td>"
            f"<td class='num'>{v['tokens']:,.0f}</td>"
            f"<td class='num'>${v['cost']:,.4f}</td></tr>"
            for sid, v in ranked
        )
    return (
        title
        + "<table><thead><tr>"
        + "<th>session_id</th><th>mode</th>"
        + "<th class='num'>total tokens</th><th class='num'>total cost</th>"
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table>"
    )


def render_footer(paths: dict[str, str]) -> str:
    items = "".join(f"<li>{html.escape(name)}: <code>{html.escape(p)}</code></li>" for name, p in paths.items())
    return f"<footer><b>source TSVs</b><ul>{items}</ul></footer>"


# ---------------------------------------------------------------------------
# Top-level build
# ---------------------------------------------------------------------------


def build_html(
    bench_tsv: str = DEFAULT_BENCH_TSV,
    tool_calls_tsv: str = DEFAULT_TOOL_CALLS_TSV,
    mode_usage_tsv: str = DEFAULT_MODE_USAGE_TSV,
    regen_tsv: str = DEFAULT_REGEN_TSV,
    since: dt.date | None = None,
    generated_at: dt.datetime | None = None,
) -> str:
    if since is None:
        since = dt.date.today() - dt.timedelta(days=7)
    if generated_at is None:
        generated_at = dt.datetime.now(dt.timezone.utc)

    bench_rows, bench_present = _read_tsv(bench_tsv)
    _tool_calls_rows, tool_calls_present = _read_tsv(tool_calls_tsv)
    mode_usage_rows, mode_usage_present = _read_tsv(mode_usage_tsv)
    regen_rows, regen_present = _read_tsv(regen_tsv)

    bench_rows = _filter_since(bench_rows, since)
    mode_usage_rows = _filter_since(mode_usage_rows, since)
    regen_rows = _filter_since(regen_rows, since)

    file_status = {
        "bench": (bench_tsv, bench_present),
        "tool-calls": (tool_calls_tsv, tool_calls_present),
        "mode-usage": (mode_usage_tsv, mode_usage_present),
        "regen-signal": (regen_tsv, regen_present),
    }

    parts = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        "<title>roam-code quality dashboard</title>",
        f"<style>{CSS}</style>",
        "</head><body>",
        render_header(generated_at, since, file_status),
        render_per_mode_summary(
            mode_usage_rows,
            regen_rows,
            mode_usage_tsv,
            regen_tsv,
            mode_usage_present,
            regen_present,
        ),
        render_per_task_variant(bench_rows, bench_tsv, bench_present),
        render_mode_usage_topn(mode_usage_rows, mode_usage_tsv, mode_usage_present),
        render_footer(
            {
                "bench": bench_tsv,
                "tool-calls": tool_calls_tsv,
                "mode-usage": mode_usage_tsv,
                "regen-signal": regen_tsv,
            }
        ),
        "</body></html>",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Static HTML quality dashboard for roam-code 3-way telemetry.")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--since", default=None, help="YYYY-MM-DD, default 7d ago")
    p.add_argument("--bench-tsv", default=DEFAULT_BENCH_TSV)
    p.add_argument("--tool-calls-tsv", default=DEFAULT_TOOL_CALLS_TSV)
    p.add_argument("--mode-usage-tsv", default=DEFAULT_MODE_USAGE_TSV)
    p.add_argument("--regen-tsv", default=DEFAULT_REGEN_TSV)
    args = p.parse_args(argv)

    since: dt.date | None = None
    if args.since:
        since = dt.date.fromisoformat(args.since)

    html_doc = build_html(
        bench_tsv=args.bench_tsv,
        tool_calls_tsv=args.tool_calls_tsv,
        mode_usage_tsv=args.mode_usage_tsv,
        regen_tsv=args.regen_tsv,
        since=since,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    print(f"wrote {out} ({len(html_doc):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
