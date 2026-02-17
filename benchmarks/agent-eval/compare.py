#!/usr/bin/env python3
"""
Compare evaluation results across agents, modes, and tasks.

Usage:
    python compare.py results/          # compare all results in directory
    python compare.py results/ --html   # generate HTML report
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


AGENTS = ["claude-code", "claude-code-sonnet", "codex", "gemini-cli"]
MODES = ["vanilla", "roam-cli", "roam-mcp"]
TASKS = ["react-todo", "astro-landing", "python-crawler", "cpp-calculator", "go-loganalyzer"]

SCORE_COLUMNS = [
    ("health", "Health", "{}", True),              # higher is better
    ("dead_symbols", "Dead", "{}", False),          # lower is better
    ("avg_complexity", "AvgCx", "{:.1f}", False),   # lower is better
    ("p90_complexity", "P90Cx", "{:.1f}", False),   # lower is better
    ("high_complexity_count", "HiCx", "{}", False), # lower is better
    ("tangle_ratio", "Tangle", "{:.2f}", False),    # lower is better
    ("hidden_coupling", "HidCoup", "{}", False),    # lower is better
    ("critical_issues", "Crit", "{}", False),       # lower is better
    ("warning_issues", "Warn", "{}", False),        # lower is better
]


def load_results(results_dir: Path) -> list[dict]:
    """Load all result JSON files from a directory."""
    results = []
    for f in sorted(results_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            results.append(data)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: skipping {f}: {e}", file=sys.stderr)
    return results


def build_lookup(results: list[dict]) -> dict:
    """Build a {(agent, mode, task): scores} lookup."""
    lookup = {}
    for r in results:
        key = (r.get("agent", "?"), r.get("mode", "?"), r.get("task", "?"))
        lookup[key] = r.get("scores", {})
    return lookup


def build_signature_lookup(results: list[dict]) -> dict:
    """Build a {agent: signature} lookup from results."""
    sigs = {}
    for r in results:
        agent = r.get("agent")
        sig = r.get("signature")
        if agent and sig:
            sigs[agent] = sig
    return sigs


def print_task_table(task: str, lookup: dict):
    """Print comparison table for one task."""
    print(f"\n{'=' * 80}")
    print(f"  TASK: {task}")
    print(f"{'=' * 80}")

    # Header
    header = f"{'Agent':<14} {'Mode':<10}"
    for _, label, _, _ in SCORE_COLUMNS:
        header += f" {label:>8}"
    print(header)
    print("-" * len(header))

    for agent in AGENTS:
        for mode in MODES:
            key = (agent, mode, task)
            scores = lookup.get(key)
            if not scores:
                continue

            row = f"{agent:<14} {mode:<10}"
            for field, _, fmt, _ in SCORE_COLUMNS:
                val = scores.get(field)
                if val is None:
                    row += f" {'N/A':>8}"
                elif isinstance(val, bool):
                    row += f" {'PASS' if val else 'FAIL':>8}"
                else:
                    try:
                        row += f" {fmt.format(val):>8}"
                    except (ValueError, TypeError):
                        row += f" {str(val)[:8]:>8}"
            print(row)


def print_agent_summary(agent: str, lookup: dict):
    """Print aggregate scores for one agent across all tasks."""
    print(f"\n--- {agent} ---")
    for mode in MODES:
        health_scores = []
        dead_total = 0
        complexities = []
        cycles_total = 0
        gate_passes = 0
        task_count = 0

        for task in TASKS:
            scores = lookup.get((agent, mode, task))
            if not scores:
                continue
            task_count += 1
            if scores.get("health") is not None:
                health_scores.append(scores["health"])
            if scores.get("dead_symbols") is not None:
                dead_total += scores["dead_symbols"]
            if scores.get("avg_complexity") is not None:
                complexities.append(scores["avg_complexity"])
            if scores.get("cycle_count") is not None:
                cycles_total += scores["cycle_count"]
            if scores.get("gate_passed"):
                gate_passes += 1

        if task_count == 0:
            continue

        avg_health = sum(health_scores) / len(health_scores) if health_scores else None
        avg_cx = sum(complexities) / len(complexities) if complexities else None

        print(f"  {mode:<10}  "
              f"avg_health={avg_health or 'N/A':>5}  "
              f"dead={dead_total:>3}  "
              f"avg_cx={f'{avg_cx:.1f}' if avg_cx else 'N/A':>5}  "
              f"cycles={cycles_total:>2}  "
              f"gates={gate_passes}/{task_count}")


def print_mode_comparison(lookup: dict):
    """Show how roam modes compare to vanilla for each agent."""
    print(f"\n{'=' * 80}")
    print("  MODE IMPACT — Health score delta (roam mode - vanilla)")
    print(f"{'=' * 80}")

    header = f"{'Agent':<14}"
    for task in TASKS:
        header += f" {task[:10]:>12}"
    header += f" {'AVG':>8}"
    print(header)
    print("-" * len(header))

    for agent in AGENTS:
        for mode in ["roam-cli", "roam-mcp"]:
            row = f"{agent:<14}"
            deltas = []
            for task in TASKS:
                vanilla = lookup.get((agent, "vanilla", task), {}).get("health")
                enhanced = lookup.get((agent, mode, task), {}).get("health")
                if vanilla is not None and enhanced is not None:
                    delta = enhanced - vanilla
                    deltas.append(delta)
                    sign = "+" if delta > 0 else ""
                    row += f" {sign}{delta:>10}"
                else:
                    row += f" {'N/A':>12}"
            avg_delta = sum(deltas) / len(deltas) if deltas else None
            if avg_delta is not None:
                sign = "+" if avg_delta > 0 else ""
                row += f" {sign}{avg_delta:>6.1f}"
            else:
                row += f" {'N/A':>8}"
            print(f"{row}  ({mode})")


def generate_html_report(lookup: dict, output: Path):
    """Generate an HTML comparison report."""
    html = ["""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Agent Code Quality Evaluation — roam-code</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 1200px; margin: 40px auto; padding: 0 20px;
         background: #f8f9fa; color: #1a1a2e; }
  h1 { text-align: center; margin-bottom: 8px; }
  .subtitle { text-align: center; color: #666; margin-bottom: 40px; }
  table { width: 100%; border-collapse: collapse; margin: 20px 0;
          background: white; border-radius: 8px; overflow: hidden;
          box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  th { background: #1a1a2e; color: white; padding: 12px 16px;
       text-align: left; font-size: 13px; text-transform: uppercase;
       letter-spacing: 0.5px; }
  td { padding: 10px 16px; border-bottom: 1px solid #eee; font-size: 14px; }
  tr:hover td { background: #f0f4ff; }
  .good { color: #2d8a4e; font-weight: 600; }
  .bad { color: #d32f2f; font-weight: 600; }
  .neutral { color: #666; }
  .section { margin-top: 48px; }
  .section h2 { border-bottom: 2px solid #1a1a2e; padding-bottom: 8px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
           font-size: 12px; font-weight: 600; }
  .badge-pass { background: #e6f4ea; color: #2d8a4e; }
  .badge-fail { background: #fce8e6; color: #d32f2f; }
  .delta-pos { color: #2d8a4e; }
  .delta-neg { color: #d32f2f; }
</style></head><body>
<h1>Agent Code Quality Evaluation</h1>
<p class="subtitle">Powered by roam-code | Generated from benchmark results</p>
"""]

    # Per-task tables
    for task in TASKS:
        html.append(f'<div class="section"><h2>{task}</h2>')
        html.append("<table><tr><th>Agent</th><th>Mode</th>")
        for _, label, _, _ in SCORE_COLUMNS:
            html.append(f"<th>{label}</th>")
        html.append("</tr>")

        for agent in AGENTS:
            for mode in MODES:
                scores = lookup.get((agent, mode, task))
                if not scores:
                    continue
                html.append(f"<tr><td>{agent}</td><td>{mode}</td>")
                for field, _, fmt, higher_better in SCORE_COLUMNS:
                    val = scores.get(field)
                    if val is None:
                        html.append('<td class="neutral">N/A</td>')
                    elif isinstance(val, bool):
                        cls = "badge-pass" if val else "badge-fail"
                        txt = "PASS" if val else "FAIL"
                        html.append(f'<td><span class="badge {cls}">{txt}</span></td>')
                    else:
                        try:
                            formatted = fmt.format(val)
                        except (ValueError, TypeError):
                            formatted = str(val)
                        html.append(f"<td>{formatted}</td>")
                html.append("</tr>")
        html.append("</table></div>")

    # Mode impact table
    html.append('<div class="section"><h2>Mode Impact (Health Delta vs Vanilla)</h2>')
    html.append("<table><tr><th>Agent</th><th>Mode</th>")
    for task in TASKS:
        html.append(f"<th>{task[:12]}</th>")
    html.append("<th>AVG</th></tr>")

    for agent in AGENTS:
        for mode in ["roam-cli", "roam-mcp"]:
            html.append(f"<tr><td>{agent}</td><td>{mode}</td>")
            deltas = []
            for task in TASKS:
                vanilla = lookup.get((agent, "vanilla", task), {}).get("health")
                enhanced = lookup.get((agent, mode, task), {}).get("health")
                if vanilla is not None and enhanced is not None:
                    delta = enhanced - vanilla
                    deltas.append(delta)
                    cls = "delta-pos" if delta > 0 else "delta-neg" if delta < 0 else "neutral"
                    sign = "+" if delta > 0 else ""
                    html.append(f'<td class="{cls}">{sign}{delta}</td>')
                else:
                    html.append('<td class="neutral">N/A</td>')
            if deltas:
                avg = sum(deltas) / len(deltas)
                cls = "delta-pos" if avg > 0 else "delta-neg" if avg < 0 else "neutral"
                sign = "+" if avg > 0 else ""
                html.append(f'<td class="{cls}"><strong>{sign}{avg:.1f}</strong></td>')
            else:
                html.append('<td class="neutral">N/A</td>')
            html.append("</tr>")
    html.append("</table></div>")

    html.append("</body></html>")

    output.write_text("\n".join(html), encoding="utf-8")
    print(f"HTML report saved to: {output}")


def main():
    parser = argparse.ArgumentParser(description="Compare agent evaluation results")
    parser.add_argument("results_dir", type=Path, help="Directory with result JSON files")
    parser.add_argument("--html", type=Path, help="Generate HTML report")
    args = parser.parse_args()

    if not args.results_dir.is_dir():
        print(f"Error: {args.results_dir} is not a directory")
        sys.exit(1)

    results = load_results(args.results_dir)
    if not results:
        print("No result files found.")
        sys.exit(1)

    print(f"Loaded {len(results)} result files.\n")
    lookup = build_lookup(results)
    signatures = build_signature_lookup(results)

    # Print agent signatures
    if signatures:
        print(f"{'=' * 80}")
        print("  AGENT SIGNATURES")
        print(f"{'=' * 80}")
        print(f"{'Agent':<20} {'CLI Version':<25} {'Model':<30}")
        print("-" * 80)
        for agent in AGENTS:
            sig = signatures.get(agent)
            if sig:
                print(f"{agent:<20} {sig.get('cli_version', 'N/A'):<25} {sig.get('model', 'N/A'):<30}")
        roam_ver = next((s.get("roam_version") for s in signatures.values() if s.get("roam_version")), None)
        if roam_ver:
            print(f"\nEvaluator: roam-code {roam_ver}")
        print()

    # Print per-task tables
    for task in TASKS:
        if any(k[2] == task for k in lookup):
            print_task_table(task, lookup)

    # Agent summaries
    print(f"\n{'=' * 80}")
    print("  AGENT SUMMARIES")
    print(f"{'=' * 80}")
    for agent in AGENTS:
        if any(k[0] == agent for k in lookup):
            print_agent_summary(agent, lookup)

    # Mode impact
    print_mode_comparison(lookup)

    # HTML report
    if args.html:
        generate_html_report(lookup, args.html)


if __name__ == "__main__":
    main()
