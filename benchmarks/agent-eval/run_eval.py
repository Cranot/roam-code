#!/usr/bin/env python3
"""
Run evaluation for all completed workspaces and generate comparison report.

Usage:
    python run_eval.py                    # evaluate all workspaces, generate report
    python run_eval.py --list             # list all expected workspaces and their status
    python run_eval.py --export-prompts   # export all prompts to prompts/ directory
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from prompts import TASKS, get_prompt, get_all_combinations


AGENTS = ["claude-code", "claude-code-sonnet", "codex", "gemini-cli"]
MODES = ["vanilla", "roam-cli", "roam-mcp"]

BASE_DIR = Path(__file__).parent
WORKSPACES_DIR = BASE_DIR / "workspaces"
RESULTS_DIR = BASE_DIR / "results"
PROMPTS_DIR = BASE_DIR / "prompts"


def workspace_path(agent: str, task_id: str, mode: str) -> Path:
    return WORKSPACES_DIR / agent / f"{task_id}_{mode}"


def result_path(agent: str, task_id: str, mode: str) -> Path:
    return RESULTS_DIR / f"{agent}_{task_id}_{mode}.json"


def list_status():
    """List all expected workspaces and their status."""
    print(f"{'Agent':<14} {'Task':<18} {'Mode':<10} {'Workspace':<8} {'Evaluated':<10}")
    print("-" * 66)

    total = 0
    ready = 0
    done = 0

    for agent in AGENTS:
        for task_id in TASKS:
            for mode in MODES:
                total += 1
                ws = workspace_path(agent, task_id, mode)
                rs = result_path(agent, task_id, mode)

                ws_status = "YES" if ws.is_dir() else "no"
                rs_status = "YES" if rs.is_file() else "no"

                if ws.is_dir():
                    ready += 1
                if rs.is_file():
                    done += 1

                print(f"{agent:<14} {task_id:<18} {mode:<10} {ws_status:<8} {rs_status:<10}")

    print(f"\nTotal: {total} | Workspaces ready: {ready} | Evaluated: {done}")


def export_prompts():
    """Export all prompts to text files."""
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    for task_id in TASKS:
        for mode in MODES:
            prompt = get_prompt(task_id, mode)
            filename = f"{task_id}_{mode}.txt"
            (PROMPTS_DIR / filename).write_text(prompt, encoding="utf-8")

    # Also export a master file with all prompts
    master = []
    for task_id, task in TASKS.items():
        master.append(f"{'=' * 80}")
        master.append(f"TASK: {task['name']} ({task_id})")
        master.append(f"Language: {task['language']}")
        master.append(f"{'=' * 80}\n")
        for mode in MODES:
            master.append(f"--- MODE: {mode} ---\n")
            master.append(get_prompt(task_id, mode))
            master.append("")

    (PROMPTS_DIR / "_all_prompts.txt").write_text("\n".join(master), encoding="utf-8")

    count = len(TASKS) * len(MODES)
    print(f"Exported {count} prompts to {PROMPTS_DIR}/")
    print(f"Master file: {PROMPTS_DIR / '_all_prompts.txt'}")


def evaluate_all(force: bool = False):
    """Evaluate all workspaces that exist."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    evaluated = 0
    skipped = 0

    for agent in AGENTS:
        for task_id in TASKS:
            for mode in MODES:
                ws = workspace_path(agent, task_id, mode)
                rs = result_path(agent, task_id, mode)

                if not ws.is_dir():
                    continue

                if rs.is_file() and not force:
                    skipped += 1
                    continue

                print(f"\n{'=' * 60}")
                print(f"Evaluating: {agent} / {task_id} / {mode}")
                print(f"{'=' * 60}")

                try:
                    result = subprocess.run(
                        [
                            sys.executable, str(BASE_DIR / "evaluate.py"),
                            str(ws),
                            "--agent", agent,
                            "--mode", mode,
                            "--task", task_id,
                            "--output", str(rs),
                        ],
                        timeout=600,
                    )
                    if result.returncode == 0:
                        evaluated += 1
                    else:
                        print(f"  FAILED (exit code {result.returncode})")
                except subprocess.TimeoutExpired:
                    print(f"  TIMEOUT")

    print(f"\nDone. Evaluated: {evaluated}, Skipped (already done): {skipped}")

    # Generate report
    if any(RESULTS_DIR.glob("*.json")):
        print("\nGenerating comparison report...")
        subprocess.run([
            sys.executable, str(BASE_DIR / "compare.py"),
            str(RESULTS_DIR),
            "--html", str(RESULTS_DIR / "report.html"),
        ])


def main():
    parser = argparse.ArgumentParser(description="Run agent evaluation benchmark")
    parser.add_argument("--list", action="store_true", help="List workspace status")
    parser.add_argument("--export-prompts", action="store_true", help="Export prompts to files")
    parser.add_argument("--force", action="store_true", help="Re-evaluate even if results exist")
    args = parser.parse_args()

    if args.list:
        list_status()
    elif args.export_prompts:
        export_prompts()
    else:
        evaluate_all(force=args.force)


if __name__ == "__main__":
    main()
