#!/usr/bin/env python3
"""
Evaluate a single agent workspace using roam-code.

Usage:
    python evaluate.py <workspace_path> [--output results/result.json]

Runs roam init + all analysis commands, collects scores into a structured JSON result.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def run_roam(workspace: Path, command: str, timeout: int = 120) -> dict | None:
    """Run a roam command with --json and return parsed output."""
    try:
        result = subprocess.run(
            ["roam", "--json", command],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"raw_output": result.stdout.strip(), "parse_error": True}
        return {
            "error": result.stderr.strip() or f"exit code {result.returncode}",
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "timeout_seconds": timeout}
    except FileNotFoundError:
        return {"error": "roam not found in PATH"}


def run_roam_init(workspace: Path, timeout: int = 300) -> dict:
    """Run roam init and return status."""
    try:
        result = subprocess.run(
            ["roam", "init"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip()[-500:],  # last 500 chars
            "stderr": result.stderr.strip()[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout"}
    except FileNotFoundError:
        return {"success": False, "error": "roam not found in PATH"}


def check_git_init(workspace: Path) -> bool:
    """Ensure workspace is a git repo (roam requires it)."""
    git_dir = workspace / ".git"
    if git_dir.exists():
        return True
    # Initialize git if needed
    try:
        subprocess.run(
            ["git", "init"], cwd=str(workspace),
            capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "add", "-A"], cwd=str(workspace),
            capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=str(workspace),
            capture_output=True, timeout=30,
        )
        return True
    except Exception:
        return False


def count_files(workspace: Path) -> dict:
    """Count files by type in workspace."""
    counts = {}
    total_lines = 0
    total_files = 0
    for f in workspace.rglob("*"):
        if f.is_file() and ".git" not in f.parts and "node_modules" not in f.parts:
            ext = f.suffix.lower() or "(no ext)"
            counts[ext] = counts.get(ext, 0) + 1
            total_files += 1
            try:
                total_lines += len(f.read_text(encoding="utf-8", errors="ignore").splitlines())
            except Exception:
                pass
    return {
        "total_files": total_files,
        "total_lines": total_lines,
        "by_extension": dict(sorted(counts.items(), key=lambda x: -x[1])),
    }


def check_tests_exist(workspace: Path) -> dict:
    """Check if test files exist."""
    test_patterns = [
        "**/*test*.*", "**/*spec*.*", "**/test_*.*",
        "**/tests/**/*.*", "**/__tests__/**/*.*",
    ]
    test_files = set()
    for pattern in test_patterns:
        for f in workspace.glob(pattern):
            if f.is_file() and ".git" not in str(f) and "node_modules" not in str(f):
                test_files.add(str(f.relative_to(workspace)))

    return {
        "tests_found": len(test_files) > 0,
        "test_file_count": len(test_files),
        "test_files": sorted(test_files)[:20],  # cap at 20
    }


def check_readme_exists(workspace: Path) -> bool:
    """Check if README exists."""
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        if (workspace / name).exists():
            return True
    return False


def check_build_config(workspace: Path) -> dict:
    """Check for build/project config files."""
    configs = {
        "package.json": "node",
        "pyproject.toml": "python",
        "setup.py": "python",
        "CMakeLists.txt": "cmake",
        "Makefile": "make",
        "go.mod": "go",
        "Cargo.toml": "rust",
        "vite.config.js": "vite",
        "vite.config.ts": "vite",
        "astro.config.mjs": "astro",
    }
    found = {}
    for filename, build_type in configs.items():
        if (workspace / filename).exists():
            found[filename] = build_type
    return {
        "has_build_config": len(found) > 0,
        "configs_found": found,
    }


def evaluate_workspace(workspace: Path) -> dict:
    """Run full evaluation on a workspace. Returns structured results."""
    results = {
        "workspace": str(workspace),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "file_stats": {},
        "structure": {},
        "roam": {},
        "scores": {},
    }

    # --- File stats ---
    results["file_stats"] = count_files(workspace)
    results["structure"]["tests"] = check_tests_exist(workspace)
    results["structure"]["readme"] = check_readme_exists(workspace)
    results["structure"]["build"] = check_build_config(workspace)

    # --- Git init (roam needs it) ---
    if not check_git_init(workspace):
        results["roam"]["error"] = "Could not initialize git"
        results["scores"] = _empty_scores()
        return results

    # --- Roam init ---
    print(f"  Running roam init...")
    init_result = run_roam_init(workspace)
    results["roam"]["init"] = init_result
    if not init_result.get("success"):
        results["roam"]["error"] = "roam init failed"
        results["scores"] = _empty_scores()
        return results

    # --- Roam analysis commands ---
    commands = ["health", "dead", "complexity", "cycles", "coupling", "gate"]
    for cmd in commands:
        print(f"  Running roam {cmd}...")
        results["roam"][cmd] = run_roam(workspace, cmd)

    # --- Extract scores ---
    results["scores"] = extract_scores(results["roam"])

    # --- Composite AQS ---
    from scoring import compute_aqs, format_aqs_report
    aqs = compute_aqs(results)
    results["aqs"] = aqs

    return results


def extract_scores(roam_results: dict) -> dict:
    """Extract numeric scores from roam JSON output."""
    scores = {}

    # Health score (0-100)
    health = roam_results.get("health")
    if health and isinstance(health, dict):
        summary = health.get("summary", {})
        scores["health"] = summary.get("score", summary.get("health_score", None))
        scores["health_verdict"] = summary.get("verdict", None)

    # Dead code count
    dead = roam_results.get("dead")
    if dead and isinstance(dead, dict):
        summary = dead.get("summary", {})
        scores["dead_symbols"] = summary.get("dead_count", summary.get("total", None))

    # Complexity
    complexity = roam_results.get("complexity")
    if complexity and isinstance(complexity, dict):
        summary = complexity.get("summary", {})
        scores["avg_complexity"] = summary.get("average", summary.get("avg_complexity", None))
        scores["max_complexity"] = summary.get("max", summary.get("max_complexity", None))
        scores["high_complexity_count"] = summary.get("high_count",
            summary.get("above_threshold", None))

    # Cycles
    cycles = roam_results.get("cycles")
    if cycles and isinstance(cycles, dict):
        summary = cycles.get("summary", {})
        scores["cycle_count"] = summary.get("scc_count", summary.get("cycle_count", None))
        scores["tangle_ratio"] = summary.get("tangle_ratio", None)

    # Coupling
    coupling = roam_results.get("coupling")
    if coupling and isinstance(coupling, dict):
        summary = coupling.get("summary", {})
        scores["high_coupling_count"] = summary.get("high_coupling_count",
            summary.get("above_threshold", None))

    # Gate
    gate = roam_results.get("gate")
    if gate and isinstance(gate, dict):
        summary = gate.get("summary", {})
        scores["gate_passed"] = summary.get("passed", summary.get("verdict", None))

    return scores


def _empty_scores() -> dict:
    return {
        "health": None,
        "dead_symbols": None,
        "avg_complexity": None,
        "max_complexity": None,
        "high_complexity_count": None,
        "cycle_count": None,
        "tangle_ratio": None,
        "high_coupling_count": None,
        "gate_passed": None,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate agent workspace with roam")
    parser.add_argument("workspace", type=Path, help="Path to agent workspace")
    parser.add_argument("--output", "-o", type=Path, help="Output JSON file")
    parser.add_argument("--agent", type=str, help="Agent name (claude-code, codex, gemini-cli)")
    parser.add_argument("--mode", type=str, help="Mode (vanilla, roam-cli, roam-mcp)")
    parser.add_argument("--task", type=str, help="Task ID (react-todo, etc.)")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        print(f"Error: {workspace} is not a directory")
        sys.exit(1)

    print(f"Evaluating: {workspace}")
    results = evaluate_workspace(workspace)

    # Add metadata
    if args.agent:
        results["agent"] = args.agent
    if args.mode:
        results["mode"] = args.mode
    if args.task:
        results["task"] = args.task

    # Output
    output_json = json.dumps(results, indent=2, default=str)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_json, encoding="utf-8")
        print(f"\nResults saved to: {args.output}")
    else:
        print("\n" + output_json)

    # Print summary
    scores = results.get("scores", {})
    print("\n=== SCORE SUMMARY ===")
    print(f"  Health:           {scores.get('health', 'N/A')}")
    print(f"  Dead symbols:     {scores.get('dead_symbols', 'N/A')}")
    print(f"  Avg complexity:   {scores.get('avg_complexity', 'N/A')}")
    print(f"  Max complexity:   {scores.get('max_complexity', 'N/A')}")
    print(f"  Cycles:           {scores.get('cycle_count', 'N/A')}")
    print(f"  Tangle ratio:     {scores.get('tangle_ratio', 'N/A')}")
    print(f"  High coupling:    {scores.get('high_coupling_count', 'N/A')}")
    print(f"  Gate passed:      {scores.get('gate_passed', 'N/A')}")
    print(f"  Files:            {results['file_stats'].get('total_files', 'N/A')}")
    print(f"  Lines:            {results['file_stats'].get('total_lines', 'N/A')}")
    print(f"  Tests found:      {results['structure']['tests'].get('test_file_count', 0)}")

    # Print AQS
    aqs = results.get("aqs", {})
    if aqs:
        from scoring import format_aqs_report
        print(f"\n=== AGENT QUALITY SCORE ===")
        print(format_aqs_report(aqs))


if __name__ == "__main__":
    main()
