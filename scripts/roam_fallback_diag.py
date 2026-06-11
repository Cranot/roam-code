#!/usr/bin/env python3
"""roam_fallback_diag.py — for ANY roam MCP tool, diagnose WHY agents fall
back to manual search after calling it.

Generalizes the Loop-2 (2026-06-02) diagnostic. For each call to the target
tool followed (within 3 calls) by a Grep / Bash-grep / Read overlapping the
tool's target, classify the fallback:

  SAME_TARGET_regrep  — re-grep the exact target (tool MISSED / under-returned)
  CONTEXT_read_file   — Read the file the tool was about (wanted the body)
  RELATED_grep        — grep a related/family token (exploring around it)

Prints counts + samples so you can see exactly what the tool failed to give.
Read-only. Stdlib only.

Usage:
  python3 scripts/roam_fallback_diag.py roam_file_info [--since YYYY-MM-DD]
  python3 scripts/roam_fallback_diag.py roam_uses
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import glob
import json
import os
import re

_PREFIX = "mcp__roam-code__"
_GREP_RE = re.compile(r"\b(grep|rg|ripgrep|git\s+grep)\b")


def _tool_calls(path: str):
    try:
        if os.path.getsize(path) > 50 * 1024 * 1024:
            return
    except OSError:
        return
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("type") != "assistant":
                    continue
                m = e.get("message", {})
                if not isinstance(m, dict):
                    continue
                for b in m.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        yield (b.get("name") or "", b.get("input") or {})
    except OSError:
        return


def _target(ti: dict) -> str:
    for k in ("query", "symbol", "name", "pattern", "path", "file", "file_path", "filename", "filepath"):
        v = ti.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().strip("\"'`").lower()
    for k in ("queries", "patterns", "paths"):
        v = ti.get(k)
        if isinstance(v, list) and v and isinstance(v[0], str):
            return v[0].strip().strip("\"'`").lower()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tool", help="bare tool name, e.g. roam_file_info")
    ap.add_argument("--since", default=None)
    ap.add_argument("--projects-dir", default="/root/.claude/projects")
    ap.add_argument("--max-files", type=int, default=4000)
    args = ap.parse_args()

    target_tool = _PREFIX + args.tool if not args.tool.startswith(_PREFIX) else args.tool
    if args.since:
        try:
            cutoff = _dt.datetime.fromisoformat(args.since).timestamp()
        except ValueError:
            cutoff = 0.0
    else:
        cutoff = (_dt.datetime.utcnow() - _dt.timedelta(days=14)).timestamp()

    files = [
        f
        for f in glob.glob(os.path.join(args.projects_dir, "**", "*.jsonl"), recursive=True)
        if os.path.exists(f) and os.path.getmtime(f) >= cutoff
    ][: args.max_files]

    cases = collections.Counter()
    samples = collections.defaultdict(list)
    n_calls = n_fallbacks = 0

    for f in files:
        calls = list(_tool_calls(f))
        for i, (name, ti) in enumerate(calls):
            if name != target_tool:
                continue
            n_calls += 1
            tgt = _target(ti)
            if not tgt:
                continue
            tgt_tokens = {t for t in re.split(r"[\W_]+", tgt) if len(t) >= 3}
            if not tgt_tokens:
                continue
            for nm, nti in calls[i + 1 : i + 4]:
                if nm.startswith(_PREFIX):
                    break  # chained into more roam → not a fallback
                txt = ""
                if nm == "Grep":
                    txt = (nti.get("pattern") or "").lower()
                elif nm == "Bash":
                    c = (nti.get("command") or "").lower()
                    if _GREP_RE.search(c):
                        txt = c
                elif nm == "Read":
                    txt = (nti.get("file_path") or "").lower()
                if not txt or not any(t in txt for t in tgt_tokens):
                    continue
                n_fallbacks += 1
                if nm == "Read":
                    key = "CONTEXT_read_file"
                elif tgt in txt or all(t in txt for t in tgt_tokens):
                    key = "SAME_TARGET_regrep"
                else:
                    key = "RELATED_grep"
                cases[key] += 1
                if len(samples[key]) < 6:
                    samples[key].append((tgt[:34], txt[:56]))
                break

    print(
        f"=== {args.tool}: {n_calls} calls, {n_fallbacks} fallbacks "
        f"({n_fallbacks * 100 // n_calls if n_calls else 0}%) ===\n"
    )
    for k, v in cases.most_common():
        print(f"  {k}: {v} ({v * 100 // n_fallbacks if n_fallbacks else 0}%)")
    print()
    for k, ss in samples.items():
        print(f"--- {k} ---")
        for tgt, txt in ss:
            print(f"   tgt={tgt!r:36} then={txt!r}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
