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

import hashlib
import io
import os
import stat
import statistics
from collections import Counter
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.compile_telemetry import (
    COMPILE_TELEMETRY_TASK_FINGERPRINT_RE,
    bucket_telemetry_ts,
    sanitize_compile_telemetry_row,
)
from roam.output.formatter import json_envelope, to_json
from roam.security.bounded_json import loads_bounded, strict_json_object_pairs
from roam.security.owner_only import (
    file_descriptor_is_owner_only,
    path_is_owner_only,
    pinned_owner_only_directory,
)

_CACHE_WARMER_AGENT_MODES = frozenset({"compile_cache_build"})
_INVALID_TELEMETRY_TS = "1970-01-01T00:00:00Z"
_LEGACY_TASK_GROUP_FIELD = "_legacy_task_group"
_LEGACY_TASK_GROUP_DOMAIN = b"roam.compile-stats.legacy-task-group\0"
_CANONICAL_NUMERIC_FIELDS = (
    "classifier_conf",
    "envelope_bytes",
    "compile_ms",
    "model_calls_avoided_count",
)
_MAX_TELEMETRY_FILE_BYTES = 2 * 1024 * 1024
_MAX_TELEMETRY_LINE_BYTES = 256 * 1024
_MAX_TELEMETRY_ROWS = 10_000


class _TelemetryRows(list[dict]):
    """List-compatible result carrying an explicit bounded-read state."""

    def __init__(self, values=(), *, read_state: str = "ok", invalid_rows: int = 0) -> None:
        super().__init__(values)
        self.read_state = read_state
        self.invalid_rows = invalid_rows


def _is_reparse_point(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return bool(
        (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)
        and left.st_mode == right.st_mode
        and left.st_nlink == right.st_nlink == 1
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and (os.name == "nt" or left.st_ctime_ns == right.st_ctime_ns)
    )


# Documented privacy-safe v4 row schema for `.roam/compile-runs.jsonl`.
# Insertion order mirrors the sanitizer whitelist in `roam/plan/compiler.py`
# so `--schema` matches what can actually persist after legacy-log rewrites.
_ROW_SCHEMA: dict[str, str] = {
    "schema_version": "privacy-safe compile telemetry schema version",
    "ts": "UTC hour bucket containing the compile call (ISO-8601)",
    "procedure": "closed classifier-selected compile-procedure category; unsupported values collapse to other",
    "classifier_conf": "procedure-classifier confidence 0..1 (plan.classifier_confidence)",
    "art_label": "closed envelope artifact class (e.g. l1_probe, facts) — compile's headline KPI",
    "prefetched_keys": "sorted closed prefetched-fact categories; unsupported keys collapse to other",
    "prefetched_fact_count": "number of privacy-safe prefetched-fact keys",
    "envelope_bytes": "serialized envelope size in bytes (-1 if serialization failed)",
    "compile_ms": "wall-clock compile latency in milliseconds",
    "agent_mode": "closed categorical ROAM_AGENT_MODE value; unknown values collapse to other",
    "episode_id": "OPTIONAL opaque local episode join key; malformed or identifying values are omitted",
    "task_fingerprint": "OPTIONAL keyed repo-local repeat identity; not reversible to prompt text",
    "injection_advice": "closed hook-channel injection-advice category",
    "cache_hit": "whether the envelope cache served this call (W58)",
    "model_calls_avoided_count": "bounded count of local signals that avoided model calls",
    "savings": "aggregate avoided-model-call, prefetched-fact, and cache-reuse counts",
    "probe_timings_ms": "OPTIONAL bounded closed-category probe latency map emitted only on cache misses",
}


def _legacy_task_group(row: dict) -> str | None:
    """Derive a non-reversible in-memory group without retaining prompt text."""

    task_hash = row.get("task_hash")
    task_prefix = row.get("task_prefix")
    if not isinstance(task_hash, str) or not task_hash or not isinstance(task_prefix, str) or not task_prefix:
        return None
    encoded = task_hash.encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(_LEGACY_TASK_GROUP_DOMAIN + encoded).hexdigest()


def _canonical_numeric_input(value):
    """Reject integers that cannot reach the canonical float-based bounds."""

    if isinstance(value, int) and not isinstance(value, bool):
        try:
            float(value)
        except OverflowError:
            return None
    return value


def _sanitize_stats_row(value: dict, *, retain_legacy_task_text: bool) -> dict:
    """Project one raw row through the canonical bounded telemetry schema.

    Historical rows may lack a valid timestamp, but they still count toward
    legacy aggregates. Give the canonical sanitizer a temporary valid bucket,
    then restore the honest unknown timestamp. Legacy prompt text is retained
    only for the explicit local cache-replay compatibility path.
    """

    raw_timestamp = value.get("ts")
    timestamp = bucket_telemetry_ts(raw_timestamp) if isinstance(raw_timestamp, str) else None
    candidate = {**value, "ts": timestamp or _INVALID_TELEMETRY_TS}
    for field in _CANONICAL_NUMERIC_FIELDS:
        candidate[field] = _canonical_numeric_input(candidate.get(field))
    for field in ("episode_id", "task_fingerprint"):
        if not isinstance(candidate.get(field), str):
            candidate[field] = None
    timings = candidate.get("probe_timings_ms")
    if isinstance(timings, dict):
        candidate["probe_timings_ms"] = {
            key: _canonical_numeric_input(number) for key, number in timings.items() if isinstance(key, str)
        }
    row = sanitize_compile_telemetry_row(candidate)
    if row is None:  # The temporary timestamp makes this unreachable for dict input.
        return {}
    if timestamp is None:
        row["ts"] = None

    legacy_group = _legacy_task_group(value)
    if legacy_group is not None:
        if retain_legacy_task_text:
            row["task_hash"] = value["task_hash"]
            row["task_prefix"] = value["task_prefix"]
        else:
            row[_LEGACY_TASK_GROUP_FIELD] = legacy_group
    return row


def _read_telemetry(root: str, *, retain_legacy_task_text: bool = True) -> list[dict]:
    """Read canonical aggregate rows; return [] when the log does not exist.

    ``retain_legacy_task_text`` exists only for the local ``compile-cache``
    replay path. ``compile-stats`` disables it before retaining or aggregating
    rows, while preserving legacy grouping through an internal digest.
    """
    try:
        state_dir = Path(root).resolve() / ".roam"
    except (OSError, RuntimeError):
        return _TelemetryRows(read_state="unavailable")
    log_path = state_dir / "compile-runs.jsonl"
    try:
        state_info = os.lstat(state_dir)
    except FileNotFoundError:
        return _TelemetryRows(read_state="missing")
    except OSError:
        return _TelemetryRows(read_state="unavailable")
    if (
        not stat.S_ISDIR(state_info.st_mode)
        or stat.S_ISLNK(state_info.st_mode)
        or _is_reparse_point(state_info)
        or not path_is_owner_only(state_dir)
    ):
        return _TelemetryRows(read_state="unsafe_state_directory")

    descriptor = -1
    try:
        with pinned_owner_only_directory(state_dir):
            try:
                before = os.lstat(log_path)
            except FileNotFoundError:
                return _TelemetryRows(read_state="missing")
            if (
                not stat.S_ISREG(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or _is_reparse_point(before)
                or before.st_nlink != 1
                or before.st_size > _MAX_TELEMETRY_FILE_BYTES
            ):
                state = "oversized" if before.st_size > _MAX_TELEMETRY_FILE_BYTES else "unsafe_log_path"
                return _TelemetryRows(read_state=state)
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(log_path, flags)
            if not file_descriptor_is_owner_only(descriptor, log_path):
                return _TelemetryRows(read_state="unsafe_log_path")
            opened = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                return _TelemetryRows(read_state="changed_during_read")
            if opened.st_size > _MAX_TELEMETRY_FILE_BYTES:
                return _TelemetryRows(read_state="oversized")
            chunks: list[bytes] = []
            remaining = _MAX_TELEMETRY_FILE_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            after_descriptor = os.fstat(descriptor)
            try:
                after_path = os.lstat(log_path)
            except OSError:
                return _TelemetryRows(read_state="changed_during_read")
            if (
                len(payload) != opened.st_size
                or len(payload) > _MAX_TELEMETRY_FILE_BYTES
                or not _same_file_snapshot(opened, after_descriptor)
                or not _same_file_snapshot(after_descriptor, after_path)
            ):
                return _TelemetryRows(read_state="changed_during_read")
    except (OSError, PermissionError, ValueError):
        return _TelemetryRows(read_state="unavailable")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass

    rows = _TelemetryRows()
    stream = io.BytesIO(payload)
    for raw_line in stream:
        if len(rows) >= _MAX_TELEMETRY_ROWS:
            rows.read_state = "row_limit_reached"
            break
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        if len(raw_line) > _MAX_TELEMETRY_LINE_BYTES:
            rows.invalid_rows += 1
            continue
        try:
            value = loads_bounded(raw_line, object_pairs_hook=strict_json_object_pairs)
        except (UnicodeDecodeError, ValueError, TypeError):
            rows.invalid_rows += 1
            continue  # tolerate bounded corrupted lines
        if isinstance(value, dict):
            rows.append(_sanitize_stats_row(value, retain_legacy_task_text=retain_legacy_task_text))
        else:
            rows.invalid_rows += 1
    if rows.invalid_rows and rows.read_state == "ok":
        rows.read_state = "partial_invalid_rows"
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
    proc_counts = Counter(r.get("procedure") if isinstance(r.get("procedure"), str) else "unknown" for r in rows)
    art_counts = Counter(r.get("art_label") if isinstance(r.get("art_label"), str) else "unknown" for r in rows)
    confs = [
        r.get("classifier_conf")
        for r in rows
        if isinstance(r.get("classifier_conf"), (int, float)) and not isinstance(r.get("classifier_conf"), bool)
    ]
    sizes = [
        r.get("envelope_bytes")
        for r in rows
        if isinstance(r.get("envelope_bytes"), (int, float))
        and not isinstance(r.get("envelope_bytes"), bool)
        and r["envelope_bytes"] > 0
    ]
    latencies = [
        r.get("compile_ms")
        for r in rows
        if isinstance(r.get("compile_ms"), (int, float)) and not isinstance(r.get("compile_ms"), bool)
    ]
    probe_keys_per_row = [
        len(r.get("prefetched_keys")) if isinstance(r.get("prefetched_keys"), list) else 0 for r in rows
    ]

    l1_count = art_counts.get("l1_probe", 0)
    l1_pct = l1_count * 100 // n if n else 0
    # W58 — cache-hit telemetry
    hits = sum(1 for r in rows if r.get("cache_hit") is True)
    hit_pct = hits * 100 // n if n else 0

    # Probe-key family histogram — which probes actually fire in the wild.
    key_counts: Counter = Counter()
    for r in rows:
        keys = r.get("prefetched_keys")
        if isinstance(keys, list):
            for key in keys:
                if isinstance(key, str):
                    key_counts[key] += 1

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


def _cache_identity(row: dict) -> tuple[str, str] | None:
    fingerprint = row.get("task_fingerprint")
    if isinstance(fingerprint, str) and COMPILE_TELEMETRY_TASK_FINGERPRINT_RE.fullmatch(fingerprint):
        return "task_fingerprint", fingerprint
    legacy_group = row.get(_LEGACY_TASK_GROUP_FIELD)
    if isinstance(legacy_group, str) and legacy_group:
        return "legacy", legacy_group
    task_hash = row.get("task_hash")
    task_prefix = row.get("task_prefix")
    if isinstance(task_hash, str) and task_hash and isinstance(task_prefix, str) and task_prefix:
        return "legacy", task_hash
    return None


def _new_cache_task_stat(
    identity_type: str,
    identity: str,
    row: dict,
    *,
    include_sensitive_task_text: bool,
) -> dict:
    legacy_task_prefix = ""
    if identity_type == "legacy" and include_sensitive_task_text:
        prefix = row.get("task_prefix")
        legacy_task_prefix = prefix if isinstance(prefix, str) else ""
    return {
        "identity_type": identity_type,
        "identity": identity,
        "legacy_task_prefix": legacy_task_prefix,
        "miss_count": 0,
        "hit_count": 0,
        "total_count": 0,
        "last_seen_index": -1,
        "last_cache_hit": False,
    }


def _update_cache_task_stat(
    stat: dict,
    row: dict,
    idx: int,
    *,
    include_sensitive_task_text: bool,
) -> None:
    if include_sensitive_task_text and stat["identity_type"] == "legacy" and not stat["legacy_task_prefix"]:
        prefix = row.get("task_prefix")
        stat["legacy_task_prefix"] = prefix if isinstance(prefix, str) else ""
    cache_hit = row.get("cache_hit") is True
    stat["total_count"] += 1
    stat["hit_count"] += 1 if cache_hit else 0
    stat["miss_count"] += 0 if cache_hit else 1
    stat["last_seen_index"] = idx
    stat["last_cache_hit"] = cache_hit


def _cache_miss_record(stat: dict, *, include_sensitive_task_text: bool) -> dict | None:
    if stat["miss_count"] <= 0:
        return None
    total = stat["total_count"] or 1
    record = {
        "identity_type": stat["identity_type"],
        "_identity": stat["identity"],
        "miss_count": stat["miss_count"],
        "hit_count": stat["hit_count"],
        "total_count": stat["total_count"],
        "miss_rate_pct": stat["miss_count"] * 100 // total,
        "last_cache_hit": stat["last_cache_hit"],
        "active_miss": not stat["last_cache_hit"],
    }
    if stat["identity_type"] == "task_fingerprint":
        record["task_fingerprint"] = stat["identity"]
    elif include_sensitive_task_text:
        record["task_hash"] = stat["identity"]
        record["task_prefix"] = stat["legacy_task_prefix"]
    return record


def _cache_miss_sort_key(entry: dict) -> tuple:
    return (
        not entry["active_miss"],
        -entry["miss_count"],
        -entry["miss_rate_pct"],
        entry["_identity"],
    )


def _is_cache_warmer_row(row: dict) -> bool:
    return _safe_agent_mode(row) in _CACHE_WARMER_AGENT_MODES


def _safe_agent_mode(row: dict) -> str:
    value = row.get("agent_mode")
    return value if isinstance(value, str) and value else "unknown"


def _top_cache_misses(
    rows: list[dict],
    limit: int = 10,
    *,
    include_sensitive_task_text: bool = False,
) -> list[dict]:
    """Rank repeated cache misses without hiding whether they are still active.

    Older output counted misses only, so a task with 100 historical misses and
    later steady hits still looked like a prebuild candidate. Include hit/miss
    rates and latest state so callers can prioritize current misses first.
    """
    tasks: dict[tuple[str, str], dict] = {}
    for idx, r in enumerate(rows):
        if _is_cache_warmer_row(r):
            continue
        identity = _cache_identity(r)
        if identity is None:
            continue
        identity_type, identity_value = identity
        d = tasks.setdefault(
            identity,
            _new_cache_task_stat(
                identity_type,
                identity_value,
                r,
                include_sensitive_task_text=include_sensitive_task_text,
            ),
        )
        _update_cache_task_stat(
            d,
            r,
            idx,
            include_sensitive_task_text=include_sensitive_task_text,
        )

    out = [
        record
        for stat in tasks.values()
        if (record := _cache_miss_record(stat, include_sensitive_task_text=include_sensitive_task_text)) is not None
    ]
    out.sort(key=_cache_miss_sort_key)
    selected = out[:limit]
    legacy_number = 0
    for record in selected:
        if record["identity_type"] == "legacy" and not include_sensitive_task_text:
            legacy_number += 1
            record["task_ref"] = f"legacy_task_{legacy_number:03d}"
        record.pop("_identity", None)
    return selected


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
    "--include-bench",
    is_flag=True,
    default=False,
    help="Include non-production rows (bench/corpus/trace/envelope_diff/cache/test) in the KPI "
    "aggregates. Default excludes them so L1-rate and latency reflect real traffic only.",
)
@click.option(
    "--by-mode",
    is_flag=True,
    default=False,
    help="W5 — group rows by agent_mode "
    "(compile/roam/vanilla/unknown). Rows that pre-date the "
    "agent_mode field count as 'unknown'.",
)
@click.option(
    "--schema",
    is_flag=True,
    default=False,
    help="Print the documented compile-runs.jsonl row-field schema and exit (no telemetry read).",
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
    ctx: click.Context,
    root: str,
    by_procedure: bool,
    slow_probes: bool,
    top_misses: bool,
    include_bench: bool,
    by_mode: bool,
    schema: bool,
) -> None:
    """Show distribution stats over the compile telemetry log."""
    from roam.plan.agent_mode import is_non_production

    json_mode = ctx.obj.get("json") if ctx.obj else False
    # `--schema` is static documentation: short-circuit BEFORE reading any
    # telemetry so it works even with no `.roam/compile-runs.jsonl` present.
    if schema:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "compile-stats",
                        summary={
                            "verdict": f"Compile telemetry schema documents {len(_ROW_SCHEMA)} privacy-safe fields",
                            "field_count": len(_ROW_SCHEMA),
                            "partial_success": False,
                        },
                        row_schema={"fields": _ROW_SCHEMA},
                    )
                )
            )
        else:
            click.echo("compile-runs.jsonl row schema:")
            for field, meaning in _ROW_SCHEMA.items():
                click.echo(f"  {field:<18s} {meaning}")
        return
    all_rows = _read_telemetry(root, retain_legacy_task_text=False)
    telemetry_read_state = getattr(all_rows, "read_state", "ok")
    invalid_telemetry_rows = getattr(all_rows, "invalid_rows", 0)

    # KPI aggregates (summary L1-rate/latency, by-procedure, top-misses) use
    # PRODUCTION rows only by default — bench/corpus/trace/diff/cache/test rows
    # would skew every reported number. `--by-mode` keeps the full set (its job
    # is to show the split). `unknown` rows stay in: historically they are a
    # MIXED bucket (all pre-stamp rows), so dropping them would hide real
    # traffic — disclosed below instead.
    def non_production(row: dict) -> bool:
        return is_non_production({"agent_mode": _safe_agent_mode(row)})

    excluded = sum(1 for r in all_rows if non_production(r))
    rows = all_rows if include_bench else [r for r in all_rows if not non_production(r)]
    summary = _summarize(rows)
    summary["telemetry_read_state"] = telemetry_read_state
    summary["invalid_telemetry_rows"] = invalid_telemetry_rows
    if telemetry_read_state not in {"ok", "missing"}:
        summary["partial_success"] = True
        if rows:
            summary["verdict"] += f"; telemetry read degraded: {telemetry_read_state}"
        else:
            summary["verdict"] = f"Compile telemetry read degraded: {telemetry_read_state}"
    if excluded and not include_bench:
        summary["excluded_non_production_rows"] = excluded
    # W52 — per-procedure breakdown
    if by_procedure and rows:
        from collections import defaultdict

        per_proc: dict = defaultdict(lambda: {"n": 0, "l1": 0, "ms_sum": 0.0})
        for r in rows:
            proc = r.get("procedure") if isinstance(r.get("procedure"), str) else "?"
            per_proc[proc]["n"] += 1
            if r.get("art_label") == "l1_probe":
                per_proc[proc]["l1"] += 1
            compile_ms = r.get("compile_ms")
            if isinstance(compile_ms, (int, float)) and not isinstance(compile_ms, bool):
                per_proc[proc]["ms_sum"] += compile_ms
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
        eligible = [row for row in rows if not _is_cache_warmer_row(row)]
        eligible_rows = len(eligible)
        fingerprint_rows = sum(
            isinstance(row.get("task_fingerprint"), str)
            and bool(COMPILE_TELEMETRY_TASK_FINGERPRINT_RE.fullmatch(row["task_fingerprint"]))
            for row in eligible
        )
        legacy_rows = sum(
            (identity := _cache_identity(row)) is not None and identity[0] == "legacy" for row in eligible
        )
        identified_rows = fingerprint_rows + legacy_rows
        unavailable_rows = eligible_rows - identified_rows
        if fingerprint_rows and legacy_rows:
            misses_state = "mixed_privacy_safe_and_legacy_rows"
        elif fingerprint_rows:
            misses_state = "privacy_safe_fingerprint_rows"
        elif legacy_rows:
            misses_state = "legacy_redacted_rows"
        else:
            misses_state = "unavailable_repeat_identity"
        if unavailable_rows and identified_rows:
            misses_state = f"partial_{misses_state}"
        summary["top_cache_misses_state"] = misses_state
        summary["top_cache_misses_fingerprint_rows"] = fingerprint_rows
        summary["top_cache_misses_legacy_rows"] = legacy_rows
        summary["top_cache_misses_unavailable_rows"] = unavailable_rows
        summary["top_cache_misses_definition"] = (
            "privacy-safe rows group by keyed repo-local task_fingerprint; legacy rows group internally "
            "but expose only per-response task_ref labels, never prompt text or raw prompt hashes"
        )
        if unavailable_rows:
            summary["partial_success"] = True
            summary["verdict"] += f"; repeat identity unavailable for {unavailable_rows} telemetry rows"
    # W5 — by-mode breakdown. Joins on the `agent_mode` field
    # added to telemetry rows when ROAM_AGENT_MODE env var is set at compile
    # time (the host platform sets this per call). Pre-W5 rows lack the field,
    # so they bucket as 'unknown' — useful baseline. This breakdown ALWAYS uses
    # the full row set (its whole job is to show the production/non-prod split),
    # regardless of the --include-bench KPI filter.
    if by_mode and all_rows:
        from collections import defaultdict

        per_mode: dict = defaultdict(lambda: {"n": 0, "l1": 0, "ms_sum": 0.0, "envelope_bytes_sum": 0, "cache_hits": 0})
        for r in all_rows:
            mode = _safe_agent_mode(r)
            per_mode[mode]["n"] += 1
            if r.get("art_label") == "l1_probe":
                per_mode[mode]["l1"] += 1
            compile_ms = r.get("compile_ms")
            if isinstance(compile_ms, (int, float)) and not isinstance(compile_ms, bool):
                per_mode[mode]["ms_sum"] += compile_ms
            envelope_bytes = r.get("envelope_bytes")
            if isinstance(envelope_bytes, (int, float)) and not isinstance(envelope_bytes, bool):
                per_mode[mode]["envelope_bytes_sum"] += envelope_bytes
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
            timings = r.get("probe_timings_ms")
            if not isinstance(timings, dict):
                continue
            for label, ms in timings.items():
                if isinstance(label, str) and isinstance(ms, (int, float)) and not isinstance(ms, bool):
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
        if telemetry_read_state not in {"ok", "missing"}:
            click.echo(f"VERDICT: compile telemetry read degraded: {telemetry_read_state}")
        elif excluded and not include_bench:
            # the file EXISTS and has rows — they were all non-production. Say so
            # rather than the misleading "no telemetry / no file" message.
            click.echo("VERDICT: no production telemetry")
            click.echo(f"  ({excluded} row(s) present, all non-production; --include-bench to include them)")
        else:
            click.echo("VERDICT: no telemetry yet")
            click.echo(f"  (no .roam/compile-runs.jsonl under {root})")
            click.echo("  Run `roam compile <task>` to populate the log.")
        return

    click.echo(f"VERDICT: {summary['verdict']}")
    click.echo(f"first call:        {summary['first_ts']}")
    click.echo(f"last call:         {summary['last_ts']}")
    click.echo(f"total compile calls: {summary['row_count']}")
    if excluded and not include_bench:
        click.echo(f"  (excluded {excluded} non-production row(s) from KPIs; --include-bench to keep them)")
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
        if summary.get("top_cache_misses_state") == "unavailable_repeat_identity":
            click.echo("Top cache misses unavailable: these telemetry rows carry no safe repeat identity.")
            click.echo("Use `compile-cache build --corpus <file>` or `--all-files` to warm explicit tasks.")
        else:
            click.echo("Top repeated cache-miss identities (prompt text and raw hashes withheld):")
            click.echo(f"  {'task_ref':<20s} {'miss':>5s} {'hit':>5s} {'miss%':>6s} {'latest':>7s}")
            for entry in summary["top_cache_misses"]:
                latest = "miss" if entry.get("active_miss") else "hit"
                task_ref = entry.get("task_fingerprint") or entry.get("task_ref") or "unavailable"
                click.echo(
                    f"  {task_ref[:20]:<20s} {entry['miss_count']:>5d} "
                    f"{entry.get('hit_count', 0):>5d} {entry.get('miss_rate_pct', 0):>5d}% "
                    f"{latest:>7s}"
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
