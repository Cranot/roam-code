#!/usr/bin/env python3
"""Audit Claude Code session transcripts for tool-usage patterns.

Measures the dogfood ratio: what fraction of tool calls go to `roam_*`
MCP tools vs always-loaded fallbacks (Bash / Read / Grep / Glob).

Baseline 2026-05-22: 0/218 (0%) across the 6 most-recent transcripts on
this repo.

Usage:
  python3 dev/audit_session_tool_usage.py [--project-dir PATH] [--top N]
  python3 dev/audit_session_tool_usage.py --json

Inputs:
  --project-dir   Repo root the transcripts cover (default: CWD).
                  Used to locate the Claude Code transcripts directory
                  at `/root/.claude/projects/<slug>/*.jsonl`.
  --top N         How many most-recent transcripts to read (default: 6).
  --transcripts-dir PATH
                  Override the transcripts directory directly.
  --json          Emit a machine-readable envelope instead of the
                  human-readable report.

Exit codes:
  0   ran cleanly (whatever the dogfood ratio)
  2   no transcripts found

This script reads user-local transcript files and writes nothing.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys
from typing import Iterable

# Tool name predicates -------------------------------------------------------


def _is_roam_mcp(name: str) -> bool:
    return name.startswith("mcp__roam") or name.startswith("roam_")


# Bash-verb classification ---------------------------------------------------


def _classify_bash(cmd: str) -> str:
    first = cmd.strip().split()[0] if cmd.strip() else ""
    lower = cmd.lower()
    if first in ("grep", "rg", "ripgrep"):
        return "grep-shell"
    if first == "git" and "log" in lower:
        return "git-log"
    if first == "git" and "diff" in lower:
        return "git-diff"
    if first == "git" and "status" in lower:
        return "git-status"
    if first == "git" and ("show" in lower or "blame" in lower):
        return "git-show-blame"
    if first == "git":
        return "git-other"
    if first in ("find", "ls", "tree"):
        return "fs-discovery"
    if first in ("pytest", "python3", "python", "ruff"):
        return "test-lint"
    if first in ("cat", "head", "tail", "less"):
        return "file-read"
    if first in ("awk", "sed"):
        return "text-extract"
    if first in ("ps", "kill", "which", "whoami", "id", "type"):
        return "process-env"
    if first == "roam":
        return "roam-CLI"
    return f"other-{first}" if first else "other"


# Code-relevance classification ---------------------------------------------


def _is_code_relevant(name: str, inp: dict) -> bool:
    """Whether a tool call is in the surface roam_* could substitute for.

    Code-relevant = symbol/code-shaped queries where roam_* tools are
    designed to win. Excludes WebSearch/WebFetch (external research),
    TodoWrite, Glob (filesystem discovery), Write/Edit (production),
    Bash for non-grep verbs, and Read of non-code files.

    Use the code-relevant ratio when grading sessions whose dominant
    work is code-comprehension. The whole-session ratio dilutes the
    signal when research/design dominates the call mix.
    """
    if _is_roam_mcp(name):
        return True
    if name == "Grep":
        pattern = inp.get("pattern") or ""
        if not pattern:
            return False
        if any(c in pattern for c in r".*+?[](){}^$|\\"):
            return False
        return True
    if name == "Bash":
        cmd = inp.get("command") or ""
        return _classify_bash(cmd) == "grep-shell"
    if name == "Read":
        path = inp.get("file_path") or ""
        code_exts = (
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".go",
            ".rs",
            ".java",
            ".kt",
            ".rb",
            ".cpp",
            ".c",
            ".cs",
            ".php",
            ".scala",
            ".swift",
        )
        return any(path.endswith(ext) for ext in code_exts)
    return False


# Project-dir → transcripts-dir mapping --------------------------------------


def _transcripts_dir_for(project_dir: str) -> str:
    """Claude Code maps a repo path like /home/alice/work/myrepo
    to a transcripts directory of dashes: /root/.claude/projects/-home-alice-work-myrepo."""
    slug = "-" + project_dir.lstrip("/").replace("/", "-")
    return f"/root/.claude/projects/{slug}"


# Core extraction ------------------------------------------------------------


def _iter_tool_uses(transcript_path: str) -> Iterable[tuple[str, dict]]:
    with open(transcript_path, "r", errors="replace") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            msg = ev.get("message") or {}
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                yield block.get("name", "<unknown>"), block.get("input") or {}


def audit_transcripts(paths: list[str]) -> dict:
    total = collections.Counter()
    roam = collections.Counter()
    bash_classes = collections.Counter()
    grep_texts: list[tuple[str, str]] = []
    per_file: dict[str, int] = {}
    read_targets = collections.Counter()

    code_relevant_total = 0
    code_relevant_roam = 0

    for tp in paths:
        per = 0
        for name, inp in _iter_tool_uses(tp):
            total[name] += 1
            per += 1
            if _is_roam_mcp(name):
                roam[name] += 1
            if _is_code_relevant(name, inp):
                code_relevant_total += 1
                if _is_roam_mcp(name):
                    code_relevant_roam += 1
            if name == "Bash":
                cmd = inp.get("command") or ""
                bash_classes[_classify_bash(cmd)] += 1
                if _classify_bash(cmd) == "grep-shell":
                    grep_texts.append((cmd, inp.get("description") or ""))
            elif name == "Read":
                read_targets[inp.get("file_path") or ""] += 1
        per_file[os.path.basename(tp)] = per

    total_calls = sum(total.values())
    roam_calls = sum(roam.values())
    dogfood_ratio = (roam_calls / total_calls) if total_calls else 0.0
    code_relevant_ratio = (code_relevant_roam / code_relevant_total) if code_relevant_total else 0.0
    return {
        "transcripts": [os.path.basename(p) for p in paths],
        "per_transcript_calls": per_file,
        "total_calls": total_calls,
        "roam_calls": roam_calls,
        "dogfood_ratio": round(dogfood_ratio, 4),
        "code_relevant_total": code_relevant_total,
        "code_relevant_roam": code_relevant_roam,
        "code_relevant_ratio": round(code_relevant_ratio, 4),
        "code_relevant_miss": code_relevant_total - code_relevant_roam,
        "tools_by_count": total.most_common(40),
        "roam_tools_by_count": roam.most_common(),
        "bash_classes_by_count": bash_classes.most_common(),
        "grep_shell_texts": [{"cmd": c[:240], "desc": d[:140]} for c, d in grep_texts],
        "top_read_targets": read_targets.most_common(20),
    }


# Report renderers -----------------------------------------------------------


def _render_human(report: dict) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("Claude Code session tool-usage audit")
    lines.append("=" * 70)
    lines.append(f"transcripts: {len(report['transcripts'])}")
    for t in report["transcripts"]:
        lines.append(f"  - {t}  ({report['per_transcript_calls'][t]} tool calls)")
    lines.append("")
    lines.append(
        f"TOTAL tool calls: {report['total_calls']}"
        f" | roam_* tool calls: {report['roam_calls']}"
        f" | dogfood ratio: {report['dogfood_ratio'] * 100:.1f}%"
    )
    lines.append(
        f"CODE-RELEVANT calls: {report['code_relevant_total']}"
        f" | roam: {report['code_relevant_roam']}"
        f" | code-relevant ratio: {report['code_relevant_ratio'] * 100:.1f}%"
        f" | misses (Grep/Bash:grep/code-Reads not on roam): {report['code_relevant_miss']}"
    )
    lines.append("")
    lines.append("VERDICT: " + _verdict_string(report))
    lines.append("")

    lines.append("Tools by count (top 20)")
    for name, n in report["tools_by_count"][:20]:
        lines.append(f"  {n:>5}  {name}")
    lines.append("")

    lines.append("roam_* tools used")
    if report["roam_tools_by_count"]:
        for name, n in report["roam_tools_by_count"]:
            lines.append(f"  {n:>5}  {name}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Bash usage by intent")
    for cls, n in report["bash_classes_by_count"]:
        lines.append(f"  {n:>5}  {cls}")
    lines.append("")

    lines.append(f"Shell-grep commands ({len(report['grep_shell_texts'])} total)")
    for item in report["grep_shell_texts"][:15]:
        lines.append(f"  - {item['cmd']}")
        if item["desc"]:
            lines.append(f"    -> {item['desc']}")
    if len(report["grep_shell_texts"]) > 15:
        lines.append(f"  ...{len(report['grep_shell_texts']) - 15} more")
    lines.append("")

    lines.append("Top Read targets")
    for path, n in report["top_read_targets"]:
        lines.append(f"  {n:>3}  {path}")
    return "\n".join(lines)


def _verdict_string(report: dict) -> str:
    ratio = report["dogfood_ratio"]
    total = report["total_calls"]
    roam = report["roam_calls"]
    if total == 0:
        return "no tool traffic observed"
    if ratio == 0:
        return f"0/{total} roam_* calls — dogfood broken; wire CLAUDE.md + .claude/agents/ per the overnight memo"
    if ratio < 0.10:
        return f"{roam}/{total} roam_* calls ({ratio * 100:.1f}%) — dogfood low"
    if ratio < 0.40:
        return f"{roam}/{total} roam_* calls ({ratio * 100:.1f}%) — dogfood improving"
    return f"{roam}/{total} roam_* calls ({ratio * 100:.1f}%) — dogfood healthy"


# Entry point ----------------------------------------------------------------


def _all_project_dirs() -> list[str]:
    """Every Claude Code project's transcripts directory under ~/.claude/projects/.

    Each subdirectory there corresponds to one repo's slug
    (e.g. ``-home-alice-projects-my-repo``). Returns sorted absolute paths.
    """
    base = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(base):
        return []
    return sorted(os.path.join(base, name) for name in os.listdir(base) if os.path.isdir(os.path.join(base, name)))


def _render_cross_project(reports: list[tuple[str, dict]]) -> str:
    """Per-project comparison table — both whole-session and code-relevant ratios."""
    lines = []
    lines.append("=" * 80)
    lines.append("Cross-project roam dogfood audit")
    lines.append("=" * 80)
    header = f"{'project':<48} {'calls':>6} {'roam%':>6} {'cr%':>6} {'miss':>5}"
    lines.append(header)
    lines.append("-" * 80)
    for slug, report in reports:
        if not report["total_calls"]:
            continue
        cr = report.get("code_relevant_ratio", 0.0) * 100
        wr = report["dogfood_ratio"] * 100
        miss = report.get("code_relevant_miss", 0)
        lines.append(f"{slug:<48} {report['total_calls']:>6} {wr:>5.1f}% {cr:>5.1f}% {miss:>5}")
    lines.append("")
    lines.append(
        "Legend: roam% = whole-session ratio (diluted by non-substitutable calls);"
        " cr% = code-relevant ratio (only Grep/Bash:grep/code-Reads/roam_*);"
        " miss = code-relevant calls that did NOT go to roam_*."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project-dir", default=os.getcwd())
    parser.add_argument("--top", type=int, default=6)
    parser.add_argument("--transcripts-dir", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="Scan every project dir under ~/.claude/projects/ and report per-project ratios.",
    )
    args = parser.parse_args(argv)

    if args.all_projects:
        reports: list[tuple[str, dict]] = []
        for pdir in _all_project_dirs():
            paths = sorted(
                glob.glob(os.path.join(pdir, "*.jsonl")),
                key=os.path.getmtime,
                reverse=True,
            )[: args.top]
            if not paths:
                continue
            slug = os.path.basename(pdir).lstrip("-")
            reports.append((slug, audit_transcripts(paths)))
        if not reports:
            sys.stderr.write("No transcripts found under ~/.claude/projects/\n")
            return 2
        if args.json:
            payload = {slug: rep for slug, rep in reports}
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(_render_cross_project(reports))
        return 0

    tdir = args.transcripts_dir or _transcripts_dir_for(os.path.abspath(args.project_dir))
    paths = sorted(
        glob.glob(os.path.join(tdir, "*.jsonl")),
        key=os.path.getmtime,
        reverse=True,
    )[: args.top]

    if not paths:
        sys.stderr.write(f"No transcripts found under {tdir}\n")
        return 2

    report = audit_transcripts(paths)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
