from __future__ import annotations

"""Walk recent Claude Code session JSONLs and emit a TSV of regeneration /
dissatisfaction signal counts per session, joinable with mode telemetry.

A regen signal is any user message (after the first user turn in the
session) whose normalized text opens with — or contains as an opening
clause — a paraphrase / "try again" marker.

Output TSV columns:
    date  session_id  cwd  user_msg_count  assistant_msg_count  regen_signals  mode

The mode is joined from a per-session sidecar:
    <modes-dir>/<first-8-chars-of-session-id>.txt   (plain text)

Designed to be importable for tests (process_session_file, etc.).
"""

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator


_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB hard skip

# Regen / paraphrase markers. We normalize the message to lowercase, strip
# leading whitespace + common punctuation, and check both startswith and
# "opening clause" (marker appears within the first ~40 chars before the
# first sentence terminator).
_REGEN_MARKERS: tuple[str, ...] = (
    "actually",
    "wait",
    "no, ",
    "no - ",
    "no -",
    "no wait",
    "no but",
    "try again",
    "regenerate",
    "redo",
    "i meant",
    "what i meant",
    "instead",
    "not quite",
    "not what i wanted",
    "do it differently",
    "different approach",
)


# Strip surrounding markdown/quote noise from the head of the message before
# matching markers.
_LEADING_NOISE_RE = re.compile(r"^[\s>*_`'\"\-]+")


def _normalize_head(text: str, head_chars: int = 80) -> str:
    """Return a lowercase, leading-noise-stripped head of the message."""
    if not text:
        return ""
    head = text[: head_chars * 4]  # generous slice in case of unicode
    head = _LEADING_NOISE_RE.sub("", head)
    head = head.lower()
    return head[:head_chars]


def message_is_regen(text: str) -> bool:
    """Return True if `text` looks like a paraphrase / try-again signal."""
    head = _normalize_head(text)
    if not head:
        return False
    # Split on common opening-clause terminators to bound "opening clause".
    opening = re.split(r"[.!?\n]", head, maxsplit=1)[0]
    for marker in _REGEN_MARKERS:
        if opening.startswith(marker):
            return True
        # also accept a marker as the first clause separated by comma
        if marker in opening.split(",")[0]:
            return True
    return False


def _extract_text(message_content: object) -> str:
    """Pull a flat text representation out of a Claude message `content`.

    `content` may be a string or a list of typed blocks. We concatenate any
    `text`-bearing blocks so markers in any of them are detected.
    """
    if isinstance(message_content, str):
        return message_content
    if isinstance(message_content, list):
        parts: list[str] = []
        for block in message_content:
            if isinstance(block, dict):
                t = block.get("type")
                if t == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif t == "tool_result":
                    # tool results are NOT user-typed regen content
                    continue
                elif "text" in block and isinstance(block["text"], str):
                    parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects from a JSONL file, skipping malformed lines."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError as exc:
        print(f"[extract_regen_signal] IOError on {path}: {exc}", file=sys.stderr)
        return


def process_session_file(path: Path) -> dict | None:
    """Compute counts for a single session JSONL.

    Returns dict with keys: session_id, cwd, date, user_msg_count,
    assistant_msg_count, regen_signals — or None if file unreadable / empty.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        print(f"[extract_regen_signal] stat failed on {path}: {exc}", file=sys.stderr)
        return None
    if size > _MAX_FILE_BYTES:
        print(
            f"[extract_regen_signal] skip large file ({size} bytes): {path}",
            file=sys.stderr,
        )
        return None

    session_id = path.stem
    cwd = ""
    earliest_ts = ""
    user_count = 0
    assistant_count = 0
    regen = 0
    seen_first_user = False

    for obj in _iter_jsonl(path):
        otype = obj.get("type")
        if otype not in ("user", "assistant"):
            # session_id / cwd may still appear here; capture opportunistically
            if not cwd and isinstance(obj.get("cwd"), str):
                cwd = obj["cwd"]
            continue

        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")

        ts = obj.get("timestamp")
        if isinstance(ts, str) and (not earliest_ts or ts < earliest_ts):
            earliest_ts = ts
        if not cwd and isinstance(obj.get("cwd"), str):
            cwd = obj["cwd"]

        if role == "assistant":
            assistant_count += 1
            continue
        if role == "user":
            text = _extract_text(msg.get("content"))
            # Skip pure tool_result echoes that have no user text.
            if not text.strip():
                continue
            user_count += 1
            if seen_first_user and message_is_regen(text):
                regen += 1
            seen_first_user = True

    date = earliest_ts[:10] if earliest_ts else ""
    return {
        "session_id": session_id,
        "cwd": cwd,
        "date": date,
        "user_msg_count": user_count,
        "assistant_msg_count": assistant_count,
        "regen_signals": regen,
    }


def load_mode(modes_dir: Path, session_id: str) -> str:
    """Read the per-session mode sidecar (`<short-sid>.txt`)."""
    short = session_id[:8].lower()
    sidecar = modes_dir / f"{short}.txt"
    try:
        if sidecar.is_file():
            return sidecar.read_text(encoding="utf-8", errors="replace").strip() or "unknown"
    except OSError as exc:
        print(f"[extract_regen_signal] sidecar read failed {sidecar}: {exc}", file=sys.stderr)
    return "unknown"


def iter_session_files(projects_dir: Path, since: _dt.date) -> Iterator[Path]:
    """Yield JSONL files modified on or after `since`."""
    if not projects_dir.is_dir():
        return
    cutoff_ts = _dt.datetime(since.year, since.month, since.day).timestamp()
    for root, _dirs, files in os.walk(projects_dir):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            p = Path(root) / name
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff_ts:
                yield p


def run(
    since: _dt.date,
    projects_dir: Path,
    modes_dir: Path,
    out_path: Path,
) -> int:
    """Main entry. Returns count of sessions written."""
    rows: list[dict] = []
    for jsonl in iter_session_files(projects_dir, since):
        rec = process_session_file(jsonl)
        if rec is None:
            continue
        rec["mode"] = load_mode(modes_dir, rec["session_id"])
        rows.append(rec)

    # Write TSV (header + rows).
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "date",
        "session_id",
        "cwd",
        "user_msg_count",
        "assistant_msg_count",
        "regen_signals",
        "mode",
    )
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(header) + "\n")
        for r in rows:
            fh.write(
                "\t".join(
                    [
                        str(r.get("date", "")),
                        str(r.get("session_id", "")),
                        str(r.get("cwd", "")),
                        str(r.get("user_msg_count", 0)),
                        str(r.get("assistant_msg_count", 0)),
                        str(r.get("regen_signals", 0)),
                        str(r.get("mode", "unknown")),
                    ]
                )
                + "\n"
            )
    return len(rows)


def _default_since() -> _dt.date:
    # datetime.utcnow() per spec (deprecated in 3.12+ but still functional);
    # fall back to datetime.now(timezone.utc) when unavailable.
    try:
        return (_dt.datetime.utcnow() - _dt.timedelta(days=7)).date()
    except AttributeError:  # pragma: no cover
        return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)).date()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract regen / dissatisfaction signal counts per Claude Code session."
    )
    p.add_argument(
        "--since",
        type=lambda s: _dt.datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
        help="Earliest session-file mtime to include (YYYY-MM-DD). Default: 7 days ago (UTC).",
    )
    p.add_argument(
        "--projects-dir",
        type=Path,
        default=Path("/root/.claude/projects"),
        help="Root of per-cwd session JSONL trees.",
    )
    p.add_argument(
        "--modes-dir",
        type=Path,
        default=Path("/var/log/roam-dogfood/session-modes"),
        help="Directory of per-session mode sidecars (<short-sid>.txt).",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("/var/log/roam-dogfood/regen-signal.tsv"),
        help="Output TSV path.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    since = args.since or _default_since()
    n = run(
        since=since,
        projects_dir=args.projects_dir,
        modes_dir=args.modes_dir,
        out_path=args.out,
    )
    print(f"[extract_regen_signal] wrote {n} sessions to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
