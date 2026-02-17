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


# Agent CLI version + model signatures
AGENT_SIGNATURES = {
    "claude-code": {
        "cli_cmd": "claude",
        "version_cmd": ["claude", "--version"],
        "model": "claude-opus-4-6",
        "invoke": "claude --model opus --dangerously-skip-permissions -p",
    },
    "claude-code-sonnet": {
        "cli_cmd": "claude",
        "version_cmd": ["claude", "--version"],
        "model": "claude-sonnet-4-5-20250929",
        "invoke": "claude --model sonnet --dangerously-skip-permissions -p",
    },
    "codex": {
        "cli_cmd": "codex",
        "version_cmd": ["codex", "--version"],
        "model": "gpt-5.3-codex",
        "invoke": "codex exec --dangerously-bypass-approvals-and-sandbox",
    },
    "gemini-cli": {
        "cli_cmd": "gemini",
        "version_cmd": ["gemini", "--version"],
        "model": "gemini-3-pro-preview",
        "invoke": "gemini --yolo",
    },
}


def get_agent_signature(agent: str) -> dict:
    """Detect CLI version and return full agent signature."""
    sig = AGENT_SIGNATURES.get(agent, {})
    if not sig:
        return {"agent": agent, "cli_version": "unknown", "model": "unknown"}

    cli_version = "unknown"
    version_cmd = sig.get("version_cmd")
    if version_cmd:
        try:
            result = subprocess.run(
                version_cmd, capture_output=True, text=True, timeout=10,
            )
            cli_version = result.stdout.strip().split("\n")[0]
        except Exception:
            pass

    roam_version = "unknown"
    try:
        result = subprocess.run(
            ["roam", "--version"], capture_output=True, text=True, timeout=10,
        )
        roam_version = result.stdout.strip().split("\n")[0]
    except Exception:
        pass

    return {
        "agent": agent,
        "cli_version": cli_version,
        "model": sig.get("model", "unknown"),
        "invoke_command": sig.get("invoke", "unknown"),
        "roam_version": roam_version,
    }


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
    commands = ["health", "dead", "complexity", "coupling"]
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

    # Health score (0-100) — also includes tangle_ratio
    health = roam_results.get("health")
    if health and isinstance(health, dict):
        scores["health"] = health.get("health_score", health.get("summary", {}).get("health_score"))
        scores["health_verdict"] = health.get("summary", {}).get("verdict")
        scores["tangle_ratio"] = health.get("tangle_ratio", health.get("summary", {}).get("tangle_ratio"))
        scores["propagation_cost"] = health.get("propagation_cost")
        scores["issue_count"] = health.get("issue_count")
        severity = health.get("severity", health.get("summary", {}).get("severity", {}))
        scores["critical_issues"] = severity.get("CRITICAL", 0)
        scores["warning_issues"] = severity.get("WARNING", 0)

    # Dead code count
    dead = roam_results.get("dead")
    if dead and isinstance(dead, dict):
        summary = dead.get("summary", {})
        # sum safe + review counts (intentional are OK)
        scores["dead_symbols"] = summary.get("safe", 0) + summary.get("review", 0)

    # Complexity
    complexity = roam_results.get("complexity")
    if complexity and isinstance(complexity, dict):
        summary = complexity.get("summary", {})
        scores["avg_complexity"] = summary.get("average_complexity")
        scores["p90_complexity"] = summary.get("p90_complexity")
        scores["high_complexity_count"] = summary.get("high_count", 0)
        scores["critical_complexity_count"] = summary.get("critical_count", 0)

    # Coupling
    coupling = roam_results.get("coupling")
    if coupling and isinstance(coupling, dict):
        summary = coupling.get("summary", {})
        scores["coupling_pairs"] = summary.get("pairs", 0)
        scores["hidden_coupling"] = summary.get("hidden_coupling", 0)

    return scores


def _empty_scores() -> dict:
    return {
        "health": None,
        "dead_symbols": None,
        "avg_complexity": None,
        "p90_complexity": None,
        "high_complexity_count": None,
        "critical_complexity_count": None,
        "tangle_ratio": None,
        "propagation_cost": None,
        "coupling_pairs": None,
        "hidden_coupling": None,
        "critical_issues": None,
        "warning_issues": None,
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
        results["signature"] = get_agent_signature(args.agent)
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
    print(f"  P90 complexity:   {scores.get('p90_complexity', 'N/A')}")
    print(f"  High complexity:  {scores.get('high_complexity_count', 'N/A')}")
    print(f"  Tangle ratio:     {scores.get('tangle_ratio', 'N/A')}")
    print(f"  Propagation cost: {scores.get('propagation_cost', 'N/A')}")
    print(f"  Coupling pairs:   {scores.get('coupling_pairs', 'N/A')}")
    print(f"  Hidden coupling:  {scores.get('hidden_coupling', 'N/A')}")
    print(f"  Critical issues:  {scores.get('critical_issues', 'N/A')}")
    print(f"  Warning issues:   {scores.get('warning_issues', 'N/A')}")
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
