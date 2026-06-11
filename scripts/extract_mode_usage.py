from __future__ import annotations

"""Aggregate per-session token usage + latency per agent mode → TSV.

Reads Claude session JSONLs under `--projects-dir` and joins each session to
the agent mode recorded in a sidecar text file under `--modes-dir` (keyed by
the first 8 chars of the session id). Emits one TSV row per session.

Stdlib only.
"""

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

# Per-file ceiling — skip pathological JSONLs (e.g. multi-GB exports) so a
# single huge file cannot wedge the aggregator.
MAX_FILE_BYTES = 50 * 1024 * 1024

TSV_HEADER = [
    "date",
    "session_id",
    "cwd",
    "mode",
    "n_turns",
    "total_input_tokens",
    "total_output_tokens",
    "total_cache_read_tokens",
    "total_cache_creation_tokens",
    "ttft_ms_median",
    "total_duration_ms",
]


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp. Tolerates `Z` suffix and missing tz."""
    if not isinstance(value, str) or not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def read_mode(modes_dir: Path, session_id: str) -> str:
    """Return the mode for a session via its 8-char short-id sidecar.

    Returns the literal string `"unknown"` when no sidecar exists or it is
    empty / unreadable — callers can distinguish via the column value.
    """
    short = session_id[:8]
    sidecar = modes_dir / f"{short}.txt"
    if not sidecar.exists():
        return "unknown"
    try:
        text = sidecar.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "unknown"
    return text or "unknown"


def iter_jsonl(path: Path) -> Iterable[dict]:
    """Yield decoded JSON dicts from a JSONL file, skipping malformed lines."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _extract_usage(record: dict) -> dict | None:
    """Pull a `usage` dict from an assistant record, if present."""
    if record.get("type") != "assistant":
        return None
    msg = record.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if isinstance(usage, dict):
        return usage
    return None


def aggregate_session(jsonl_path: Path, session_id: str | None = None) -> dict | None:
    """Aggregate one session JSONL into a row-friendly dict.

    Returns None when the file is missing, oversized, or contains no
    assistant turns with usage data. The `session_id` argument overrides
    sniffing from records (used when caller already knows it from filename).
    """
    if not jsonl_path.exists():
        return None
    try:
        size = jsonl_path.stat().st_size
    except OSError:
        return None
    if size > MAX_FILE_BYTES:
        return None

    n_turns = 0
    tot_in = 0
    tot_out = 0
    tot_cache_read = 0
    tot_cache_create = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    ttfts_ms: list[float] = []
    cwd = ""
    sniffed_sid = ""

    prev_user_ts: datetime | None = None

    for rec in iter_jsonl(jsonl_path):
        ts = parse_timestamp(rec.get("timestamp"))
        if ts is not None:
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts

        if not cwd:
            c = rec.get("cwd")
            if isinstance(c, str) and c:
                cwd = c
        if not sniffed_sid:
            s = rec.get("sessionId")
            if isinstance(s, str) and s:
                sniffed_sid = s

        rec_type = rec.get("type")

        if rec_type == "user" and ts is not None:
            prev_user_ts = ts
            continue

        usage = _extract_usage(rec)
        if usage is None:
            continue

        n_turns += 1
        tot_in += int(usage.get("input_tokens") or 0)
        tot_out += int(usage.get("output_tokens") or 0)
        tot_cache_read += int(usage.get("cache_read_input_tokens") or 0)
        tot_cache_create += int(usage.get("cache_creation_input_tokens") or 0)

        # Best-effort TTFT — if the assistant record carries a
        # `firstTokenTimestamp` (or `first_token_timestamp`), prefer it;
        # otherwise approximate as (assistant_ts − prior_user_ts).
        ft_raw = rec.get("firstTokenTimestamp") or rec.get("first_token_timestamp")
        ft_ts = parse_timestamp(ft_raw)
        anchor = prev_user_ts
        if anchor is not None:
            if ft_ts is not None:
                delta = (ft_ts - anchor).total_seconds() * 1000.0
            elif ts is not None:
                delta = (ts - anchor).total_seconds() * 1000.0
            else:
                delta = None
            if delta is not None and delta >= 0:
                ttfts_ms.append(delta)

    if n_turns == 0:
        return None

    sid = session_id or sniffed_sid or jsonl_path.stem
    if first_ts is not None and last_ts is not None:
        duration_ms = int((last_ts - first_ts).total_seconds() * 1000)
    else:
        duration_ms = -1
    ttft_median = int(statistics.median(ttfts_ms)) if ttfts_ms else -1
    date_str = first_ts.date().isoformat() if first_ts else ""

    return {
        "date": date_str,
        "session_id": sid,
        "cwd": cwd,
        "n_turns": n_turns,
        "total_input_tokens": tot_in,
        "total_output_tokens": tot_out,
        "total_cache_read_tokens": tot_cache_read,
        "total_cache_creation_tokens": tot_cache_create,
        "ttft_ms_median": ttft_median,
        "total_duration_ms": duration_ms,
        "_first_ts": first_ts,
    }


def session_id_from_path(path: Path) -> str:
    """Session id is the JSONL filename stem (UUID)."""
    return path.stem


def iter_session_files(projects_dir: Path) -> Iterable[Path]:
    """Yield every `*.jsonl` under the projects tree."""
    if not projects_dir.exists():
        return
    for entry in projects_dir.rglob("*.jsonl"):
        if entry.is_file():
            yield entry


def write_tsv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(TSV_HEADER) + "\n")
        for row in rows:
            fh.write("\t".join(str(row.get(col, "")) if row.get(col, "") != "" else "" for col in TSV_HEADER) + "\n")


def run(
    projects_dir: Path,
    modes_dir: Path,
    out_path: Path,
    since: datetime,
) -> int:
    rows: list[dict] = []
    for jsonl in iter_session_files(projects_dir):
        sid = session_id_from_path(jsonl)
        agg = aggregate_session(jsonl, session_id=sid)
        if agg is None:
            continue
        first_ts = agg.pop("_first_ts", None)
        if first_ts is not None and first_ts < since:
            continue
        agg["mode"] = read_mode(modes_dir, sid)
        rows.append(agg)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("session_id", "")))
    write_tsv(rows, out_path)
    return len(rows)


def parse_since(value: str) -> datetime:
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--since must be YYYY-MM-DD (got {value!r})") from exc
    return dt.replace(tzinfo=timezone.utc)


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Aggregate per-session token usage + latency from Claude project "
            "JSONLs, joined to agent mode sidecars, into a TSV."
        )
    )
    default_since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    p.add_argument("--since", type=parse_since, default=parse_since(default_since))
    p.add_argument("--projects-dir", type=Path, default=Path("/root/.claude/projects"))
    p.add_argument(
        "--modes-dir",
        type=Path,
        default=Path("/var/log/roam-dogfood/session-modes"),
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("/var/log/roam-dogfood/mode-usage.tsv"),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    n = run(args.projects_dir, args.modes_dir, args.out, args.since)
    print(f"wrote {n} rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
