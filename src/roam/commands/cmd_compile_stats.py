"""Analyze the `.roam/compile-runs.jsonl` telemetry written by W39 D1.

Reports production-grade distributions across all `roam compile` calls:
  - procedure-class distribution
  - L1-probe route rate (compile's headline KPI)
  - probe-fire rate by family
  - classifier-confidence histogram
  - envelope-size + compile-latency p50/p95/p99

Different from `internal/benchmarks/compile_readiness.py` (synthetic
19-task scorecard) — this command reads REAL workloads, so the
distributions reflect actual usage patterns.

SARIF is deliberately NOT emitted: output is aggregate statistics over
telemetry rows, not file-located findings.

Output formats: text (default), --json.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_CACHE_WARMER_AGENT_MODES = frozenset({"compile_cache_build"})


def _read_telemetry(root: str) -> list[dict]:
    """Read .roam/compile-runs.jsonl rows. Returns [] if the log doesn't exist."""
    log_path = Path(root) / ".roam" / "compile-runs.jsonl"
    if not log_path.exists():
        return []
    rows: list[dict] = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate corrupted lines
    return rows


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * pct / 100
    f = int(k)
    c = min(f + 1, len(sorted_v) - 1)
    if f == c:
        return sorted_v[f]
    return sorted_v[f] + (sorted_v[c] - sorted_v[f]) * (k - f)


def _summarize(rows: list[dict]) -> dict:
    if not rows:
        return {
            "verdict": "no telemetry yet — run `roam compile <task>` to populate",
            "row_count": 0,
            "partial_success": True,
        }
    n = len(rows)
    proc_counts = Counter(r.get("procedure") for r in rows)
    art_counts = Counter(r.get("art_label") for r in rows)
    confs = [r.get("classifier_conf") for r in rows if isinstance(r.get("classifier_conf"), (int, float))]
    sizes = [
        r.get("envelope_bytes")
        for r in rows
        if isinstance(r.get("envelope_bytes"), (int, float)) and r["envelope_bytes"] > 0
    ]
    latencies = [r.get("compile_ms") for r in rows if isinstance(r.get("compile_ms"), (int, float))]
    probe_keys_per_row = [len(r.get("prefetched_keys") or []) for r in rows]

    l1_count = art_counts.get("l1_probe", 0)
    l1_pct = l1_count * 100 // n if n else 0
    # W58 — cache-hit telemetry
    hits = sum(1 for r in rows if r.get("cache_hit") is True)
    hit_pct = hits * 100 // n if n else 0

    # Probe-key family histogram — which probes actually fire in the wild.
    key_counts: Counter = Counter()
    for r in rows:
        for k in r.get("prefetched_keys") or []:
            key_counts[k] += 1

    return {
        "verdict": f"L1-route rate {l1_pct}% over {n} compile calls",
        "row_count": n,
        "first_ts": rows[0].get("ts"),
        "last_ts": rows[-1].get("ts"),
        "procedure_distribution": dict(proc_counts.most_common()),
        "artifact_distribution": dict(art_counts.most_common()),
        "l1_probe_count": l1_count,
        "l1_probe_pct": l1_pct,
        "cache_hits": hits,
        "cache_hit_pct": hit_pct,
        "classifier_confidence": {
            "mean": round(statistics.mean(confs), 3) if confs else None,
            "median": round(statistics.median(confs), 3) if confs else None,
            "low_conf_count": sum(1 for c in confs if c < 0.6),
            "low_conf_pct": (sum(1 for c in confs if c < 0.6) * 100 // len(confs)) if confs else 0,
        },
        "envelope_size_bytes": {
            "mean": int(statistics.mean(sizes)) if sizes else None,
            "p50": int(_percentile(sizes, 50)) if sizes else None,
            "p95": int(_percentile(sizes, 95)) if sizes else None,
            "p99": int(_percentile(sizes, 99)) if sizes else None,
            "max": max(sizes) if sizes else None,
        },
        "compile_latency_ms": {
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
            "p50": round(_percentile(latencies, 50), 1) if latencies else None,
            "p95": round(_percentile(latencies, 95), 1) if latencies else None,
            "p99": round(_percentile(latencies, 99), 1) if latencies else None,
            "max": round(max(latencies), 1) if latencies else None,
        },
        "probe_keys_per_call": {
            "mean": round(statistics.mean(probe_keys_per_row), 2) if probe_keys_per_row else None,
            "median": statistics.median(probe_keys_per_row) if probe_keys_per_row else None,
        },
        "top_probe_keys": dict(key_counts.most_common(15)),
        "partial_success": False,
    }


def _new_cache_task_stat(task_hash: str, row: dict) -> dict:
    return {
        "task_hash": task_hash,
        "task_prefix": row.get("task_prefix") or "",
        "miss_count": 0,
        "hit_count": 0,
        "total_count": 0,
        "last_seen_index": -1,
        "last_cache_hit": False,
    }


def _update_cache_task_stat(stat: dict, row: dict, idx: int) -> None:
    if not stat["task_prefix"]:
        stat["task_prefix"] = row.get("task_prefix") or ""
    cache_hit = row.get("cache_hit") is True
    stat["total_count"] += 1
    stat["hit_count"] += 1 if cache_hit else 0
    stat["miss_count"] += 0 if cache_hit else 1
    stat["last_seen_index"] = idx
    stat["last_cache_hit"] = cache_hit


def _cache_miss_record(stat: dict) -> dict | None:
    if stat["miss_count"] <= 0:
        return None
    total = stat["total_count"] or 1
    return {
        "task_hash": stat["task_hash"],
        "miss_count": stat["miss_count"],
        "hit_count": stat["hit_count"],
        "total_count": stat["total_count"],
        "miss_rate_pct": stat["miss_count"] * 100 // total,
        "last_cache_hit": stat["last_cache_hit"],
        "active_miss": not stat["last_cache_hit"],
        "task_prefix": stat["task_prefix"],
    }


def _cache_miss_sort_key(entry: dict) -> tuple:
    return (
        not entry["active_miss"],
        -entry["miss_count"],
        -entry["miss_rate_pct"],
        entry["task_hash"],
    )


def _is_cache_warmer_row(row: dict) -> bool:
    return (row.get("agent_mode") or "unknown") in _CACHE_WARMER_AGENT_MODES


def _top_cache_misses(rows: list[dict], limit: int = 10) -> list[dict]:
    """Rank repeated cache misses without hiding whether they are still active.

    Older output counted misses only, so a task with 100 historical misses and
    later steady hits still looked like a prebuild candidate. Include hit/miss
    rates and latest state so callers can prioritize current misses first.
    """
    tasks: dict[str, dict] = {}
    for idx, r in enumerate(rows):
        if _is_cache_warmer_row(r):
            continue
        h = r.get("task_hash") or "?"
        d = tasks.setdefault(h, _new_cache_task_stat(h, r))
        _update_cache_task_stat(d, r, idx)

    out = [record for stat in tasks.values() if (record := _cache_miss_record(stat)) is not None]
    out.sort(key=_cache_miss_sort_key)
    return out[:limit]


@click.command(name="compile-stats")
@click.option("--root", default=".", show_default=True, help="Project root containing .roam/compile-runs.jsonl")
@click.option("--by-procedure", is_flag=True, default=False, help="Group L1-route rate and probe timings by procedure.")
@click.option(
    "--slow-probes",
    is_flag=True,
    default=False,
    help="Show p50/p95/p99 latency per probe section (requires W43 P3 telemetry).",
)
@click.option(
    "--top-misses",
    is_flag=True,
    default=False,
    help="W91 — show top tasks that miss the cache most often. Helpful for `roam compile-cache build --top-misses`.",
)
@click.option(
    "--by-mode",
    is_flag=True,
    default=False,
    help="W5 — group rows by agent_mode "
    "(compile/roam/vanilla/unknown). Rows that pre-date the "
    "agent_mode field count as 'unknown'.",
)
@click.pass_context
@roam_capability(
    name="compile-stats",
    category="planning",
    summary="Analyze .roam/compile-runs.jsonl telemetry for production fire-rate and latency distributions",
    inputs=("--root",),
    outputs=("summary_envelope",),
    examples=(
        "roam compile-stats",
        "roam --json compile-stats --root /path/to/project",
    ),
    tags=("planning", "telemetry", "compiler"),
)
def compile_stats(
    ctx: click.Context, root: str, by_procedure: bool, slow_probes: bool, top_misses: bool, by_mode: bool
) -> None:
    """Show distribution stats over the compile telemetry log."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    rows = _read_telemetry(root)
    summary = _summarize(rows)
    # W52 — per-procedure breakdown
    if by_procedure and rows:
        from collections import defaultdict

        per_proc: dict = defaultdict(lambda: {"n": 0, "l1": 0, "ms_sum": 0.0})
        for r in rows:
            proc = r.get("procedure") or "?"
            per_proc[proc]["n"] += 1
            if r.get("art_label") == "l1_probe":
                per_proc[proc]["l1"] += 1
            per_proc[proc]["ms_sum"] += r.get("compile_ms", 0)
        summary["by_procedure"] = {
            proc: {
                "n": d["n"],
                "l1_pct": d["l1"] * 100 // d["n"] if d["n"] else 0,
                "mean_compile_ms": round(d["ms_sum"] / d["n"], 1) if d["n"] else 0,
            }
            for proc, d in sorted(per_proc.items(), key=lambda kv: -kv[1]["n"])
        }
    # W91 — top tasks that miss the cache. Useful for picking corpus tasks
    # to warm via `roam compile-cache build --top-misses`. A task that misses
    # repeatedly is either churning (its deps change frequently) or unique
    # (one-off; not worth caching).
    if top_misses and rows:
        summary["top_cache_misses"] = _top_cache_misses(rows)
    # W5 — by-mode breakdown. Joins on the `agent_mode` field
    # added to telemetry rows when ROAM_AGENT_MODE env var is set at compile
    # time (the host platform sets this per call). Pre-W5 rows lack the field,
    # so they bucket as 'unknown' — useful baseline.
    if by_mode and rows:
        from collections import defaultdict

        per_mode: dict = defaultdict(lambda: {"n": 0, "l1": 0, "ms_sum": 0.0, "envelope_bytes_sum": 0, "cache_hits": 0})
        for r in rows:
            mode = r.get("agent_mode") or "unknown"
            per_mode[mode]["n"] += 1
            if r.get("art_label") == "l1_probe":
                per_mode[mode]["l1"] += 1
            per_mode[mode]["ms_sum"] += r.get("compile_ms", 0) or 0
            per_mode[mode]["envelope_bytes_sum"] += r.get("envelope_bytes", 0) or 0
            if r.get("cache_hit") is True:
                per_mode[mode]["cache_hits"] += 1
        summary["by_mode"] = {
            mode: {
                "n": d["n"],
                "l1_pct": d["l1"] * 100 // d["n"] if d["n"] else 0,
                "mean_compile_ms": round(d["ms_sum"] / d["n"], 1) if d["n"] else 0,
                "mean_envelope_bytes": d["envelope_bytes_sum"] // d["n"] if d["n"] else 0,
                "cache_hit_pct": d["cache_hits"] * 100 // d["n"] if d["n"] else 0,
            }
            for mode, d in sorted(per_mode.items(), key=lambda kv: -kv[1]["n"])
        }
    # W52 — slow-probes p50/p95/p99 per section
    if slow_probes and rows:
        from collections import defaultdict

        section_times: dict = defaultdict(list)
        for r in rows:
            timings = r.get("probe_timings_ms") or {}
            for label, ms in timings.items():
                if isinstance(ms, (int, float)):
                    section_times[label].append(ms)
        summary["probe_section_latency_ms"] = {
            label: {
                "n": len(vals),
                "p50": round(_percentile(vals, 50), 1),
                "p95": round(_percentile(vals, 95), 1),
                "p99": round(_percentile(vals, 99), 1),
                "max": round(max(vals), 1),
            }
            for label, vals in sorted(section_times.items(), key=lambda kv: -_percentile(kv[1], 95))
        }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "compile-stats",
                    summary=summary,
                    agent_contract={
                        "facts": [
                            f"Telemetry log has {summary['row_count']} rows",
                            f"L1-probe envelope chosen on {summary.get('l1_probe_pct', 0)}% of calls",
                            f"Top procedure: {next(iter((summary.get('procedure_distribution') or {'(none)': 0}).keys()))}",
                        ],
                        "next_commands": [
                            "roam compile <task>          # generate more telemetry",
                        ],
                        "risks": [],
                        "confidence": None,
                    },
                    rows=len(rows),
                )
            )
        )
        return

    if not rows:
        click.echo("VERDICT: no telemetry yet")
        click.echo(f"  (no .roam/compile-runs.jsonl under {root})")
        click.echo("  Run `roam compile <task>` to populate the log.")
        return

    click.echo(f"VERDICT: {summary['verdict']}")
    click.echo(f"first call:        {summary['first_ts']}")
    click.echo(f"last call:         {summary['last_ts']}")
    click.echo(f"total compile calls: {summary['row_count']}")
    click.echo("")
    click.echo("Artifact distribution:")
    for art, count in summary["artifact_distribution"].items():
        pct = count * 100 // summary["row_count"]
        marker = " ← compile's headline KPI" if art == "l1_probe" else ""
        click.echo(f"  {art:<14s} {count:>4d}  ({pct:>3d}%){marker}")
    click.echo("")
    click.echo("Procedure distribution:")
    for proc, count in summary["procedure_distribution"].items():
        pct = count * 100 // summary["row_count"]
        click.echo(f"  {proc:<22s} {count:>4d}  ({pct:>3d}%)")
    click.echo("")
    click.echo(
        f"Cache hits:        {summary.get('cache_hits', 0)}  "
        f"({summary.get('cache_hit_pct', 0)}%)  ← W58 production-cache health"
    )
    click.echo(
        f"Classifier confidence: mean={summary['classifier_confidence']['mean']} "
        f"median={summary['classifier_confidence']['median']} "
        f"low_conf<0.6: {summary['classifier_confidence']['low_conf_pct']}%"
    )
    click.echo(
        f"Envelope size:   p50={summary['envelope_size_bytes']['p50']} "
        f"p95={summary['envelope_size_bytes']['p95']} "
        f"max={summary['envelope_size_bytes']['max']} bytes"
    )
    click.echo(
        f"Compile latency: p50={summary['compile_latency_ms']['p50']}ms "
        f"p95={summary['compile_latency_ms']['p95']}ms "
        f"max={summary['compile_latency_ms']['max']}ms"
    )
    click.echo(f"Probe keys/call: mean={summary['probe_keys_per_call']['mean']}")
    click.echo("")
    click.echo("Top probe keys fired (signal that probes are actually working):")
    for k, count in list(summary["top_probe_keys"].items())[:10]:
        click.echo(f"  {k:<32s} {count:>4d}")

    # W52 — per-procedure breakdown
    if "by_procedure" in summary:
        click.echo("")
        click.echo("Per-procedure L1-route + latency:")
        click.echo(f"  {'procedure':<22s} {'n':>5s} {'l1%':>5s} {'mean_ms':>8s}")
        for proc, d in summary["by_procedure"].items():
            click.echo(f"  {proc:<22s} {d['n']:>5d} {d['l1_pct']:>4d}% {d['mean_compile_ms']:>8.1f}")

    # W91 — top misses text rendering
    if "top_cache_misses" in summary:
        click.echo("")
        click.echo("Top cache misses (W91 — warm these with `compile-cache build --top-misses`):")
        click.echo(f"  {'task_hash':<14s} {'miss':>5s} {'hit':>5s} {'miss%':>6s} {'latest':>7s}  task")
        for entry in summary["top_cache_misses"]:
            latest = "miss" if entry.get("active_miss") else "hit"
            click.echo(
                f"  {entry['task_hash']:<14s} {entry['miss_count']:>5d} "
                f"{entry.get('hit_count', 0):>5d} {entry.get('miss_rate_pct', 0):>5d}% "
                f"{latest:>7s}  {entry['task_prefix'][:70]}"
            )
    # W52 — slow-probes histogram
    if "probe_section_latency_ms" in summary:
        click.echo("")
        click.echo("Probe section latency (slowest first by p95):")
        click.echo(f"  {'section':<22s} {'n':>5s} {'p50':>7s} {'p95':>7s} {'p99':>7s} {'max':>7s}")
        for label, d in summary["probe_section_latency_ms"].items():
            click.echo(
                f"  {label:<22s} {d['n']:>5d} {d['p50']:>7.1f} {d['p95']:>7.1f} {d['p99']:>7.1f} {d['max']:>7.1f}"
            )
