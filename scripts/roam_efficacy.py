#!/usr/bin/env python3
"""roam_efficacy.py — measure whether roam MCP tools actually WIN in
production, by mining the session JSONL tool-call stream.

For each `mcp__roam-code__*` tool call, classify the agent's NEXT few tool
calls:

  WON       — the agent acted (Edit/Write/MultiEdit) or moved on without
              re-searching the same target. roam answered the question.
  FALLBACK  — within the next 2 calls the agent ran a Grep / Bash-grep / Read
              that overlaps the roam call's target. roam was distrusted or
              insufficient, so the agent searched manually anyway.
  CHAIN     — next call is another roam tool (neutral; composing).
  END       — roam call was the last tool in the turn/session (assume WON).

Aggregate win/fallback rates per roam tool. A high fallback rate means a
trust/quality problem; a high win rate means the bottleneck is pure
adoption (agents that DO reach for roam are well served).

Read-only. Stdlib only.

Usage:
  python3 scripts/roam_efficacy.py [--since YYYY-MM-DD] [--projects-dir DIR]
                                   [--max-files N] [--out PATH]
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import glob
import json
import os
import re

_ROAM_PREFIX = "mcp__roam-code__"
_SEARCH_TOOLS = {"Grep", "Glob"}
_ACT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_GREP_CMD_RE = re.compile(r"\b(grep|rg|ripgrep|ag|git\s+grep)\b")
_LOOKAHEAD = 3  # how many subsequent tool calls to inspect


def _iter_tool_calls(jsonl_path: str):
    """Yield (tool_name, tool_input_dict) in stream order for one session."""
    try:
        if os.path.getsize(jsonl_path) > 50 * 1024 * 1024:
            return
    except OSError:
        return
    try:
        with open(jsonl_path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                msg = e.get("message", {})
                if not isinstance(msg, dict):
                    continue
                if e.get("type") != "assistant":
                    continue
                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        yield (b.get("name") or "", b.get("input") or {})
    except OSError:
        return


def _roam_target(tool_input: dict) -> str:
    """Best-effort extract the symbol/path a roam call is about."""
    for k in ("query", "symbol", "name", "pattern", "path", "file", "file_path", "filename"):
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().strip("\"'`").lower()
    # batch_search uses queries/patterns lists
    for k in ("queries", "patterns"):
        v = tool_input.get(k)
        if isinstance(v, list) and v and isinstance(v[0], str):
            return v[0].strip().strip("\"'`").lower()
    return ""


def _is_fallback_search(name: str, tool_input: dict, target: str) -> bool:
    """True if this call is a manual search that overlaps `target`."""
    if not target:
        # No target to compare → any grep-shaped call counts as a soft fallback.
        target_tokens = set()
    else:
        target_tokens = {t for t in re.split(r"[\W_]+", target) if len(t) >= 3}

    if name == "Grep":
        pat = (tool_input.get("pattern") or "").lower()
        if not target_tokens:
            return True
        return any(t in pat for t in target_tokens)
    if name == "Read":
        # Re-reading the exact file the roam call was about = mild fallback.
        path = (tool_input.get("file_path") or "").lower()
        return bool(target_tokens) and any(t in path for t in target_tokens)
    if name == "Bash":
        cmd = (tool_input.get("command") or "").lower()
        if not _GREP_CMD_RE.search(cmd):
            return False
        if not target_tokens:
            return True
        return any(t in cmd for t in target_tokens)
    return False


def _classify_after(calls: list, idx: int) -> str:
    """Classify the roam call at `calls[idx]` by looking at the next calls."""
    name, tinput = calls[idx]
    target = _roam_target(tinput)
    nxt = calls[idx + 1 : idx + 1 + _LOOKAHEAD]
    if not nxt:
        return "END"
    # Immediate next call.
    n0_name, n0_input = nxt[0]
    if n0_name.startswith(_ROAM_PREFIX):
        return "CHAIN"
    # Fallback if any of the next LOOKAHEAD calls is a manual search on target.
    for nm, ti in nxt:
        if nm.startswith(_ROAM_PREFIX):
            break  # chained into more roam → not a fallback
        if _is_fallback_search(nm, ti, target):
            return "FALLBACK"
        if nm in _ACT_TOOLS:
            return "WON"  # agent acted on the answer
    # No fallback, no clear act → neutral-positive (agent moved on).
    return "WON"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD; default 14 days ago.")
    ap.add_argument("--projects-dir", default="/root/.claude/projects")
    ap.add_argument("--max-files", type=int, default=4000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.since:
        try:
            cutoff = _dt.datetime.fromisoformat(args.since).timestamp()
        except ValueError:
            cutoff = 0.0
    else:
        cutoff = (_dt.datetime.utcnow() - _dt.timedelta(days=14)).timestamp()

    files = []
    for f in glob.glob(os.path.join(args.projects_dir, "**", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(f) >= cutoff:
                files.append(f)
        except OSError:
            continue
    files = files[: args.max_files]

    per_tool = collections.defaultdict(lambda: collections.Counter())
    total = collections.Counter()
    n_sessions_with_roam = 0

    for f in files:
        calls = list(_iter_tool_calls(f))
        if not calls:
            continue
        had_roam = False
        for i, (name, _ti) in enumerate(calls):
            if not name.startswith(_ROAM_PREFIX):
                continue
            had_roam = True
            verdict = _classify_after(calls, i)
            short = name[len(_ROAM_PREFIX) :]
            per_tool[short][verdict] += 1
            total[verdict] += 1
        if had_roam:
            n_sessions_with_roam += 1

    # Report
    lines = []
    grand = sum(total.values())
    won = total["WON"] + total["END"]
    fb = total["FALLBACK"]
    chain = total["CHAIN"]
    lines.append(f"sessions scanned: {len(files)} | with roam calls: {n_sessions_with_roam}")
    lines.append(f"roam tool calls classified: {grand}")
    if grand:
        lines.append(f"  WON (acted/moved-on/end): {won} ({won * 100 // grand}%)")
        lines.append(f"  FALLBACK (manual re-search): {fb} ({fb * 100 // grand}%)")
        lines.append(f"  CHAIN (composed more roam): {chain} ({chain * 100 // grand}%)")
    lines.append("")
    lines.append(f"{'tool':28} {'n':>5} {'won%':>5} {'fallback%':>10} {'chain%':>7}")
    lines.append("-" * 60)
    for tool, c in sorted(per_tool.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        w = c["WON"] + c["END"]
        lines.append(
            f"{tool:28} {n:>5} {w * 100 // n if n else 0:>4}% "
            f"{c['FALLBACK'] * 100 // n if n else 0:>9}% "
            f"{c['CHAIN'] * 100 // n if n else 0:>6}%"
        )

    report = "\n".join(lines)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
