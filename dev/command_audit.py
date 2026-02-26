#!/usr/bin/env python3
"""Run an agent-oriented 20-command operational audit and write a markdown report."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AuditCommand:
    id: int
    title: str
    command: tuple[str, ...]
    timeout_sec: int = 60
    expected_exit_codes: tuple[int, ...] = (0,)


COMMANDS: tuple[AuditCommand, ...] = (
    AuditCommand(1, "Current directory", ("python", "-c", "import os; print(os.getcwd())")),
    AuditCommand(2, "Branch + working tree", ("git", "status", "--short", "--branch")),
    AuditCommand(
        3,
        "Focused hygiene snapshot",
        (
            "python",
            "dev/repo_hygiene.py",
            "--json",
            "--show",
            "0",
            "--focus-path",
            "src",
            "--focus-path",
            "tests",
            "--focus-path",
            "dev",
            "--focus-path",
            "pyproject.toml",
            "--focus-path",
            "Makefile",
            "--threshold-scope",
            "focus",
            "--max-untracked",
            "50",
            "--max-unstaged",
            "30",
            "--max-staged",
            "30",
            "--max-conflicts",
            "0",
            "--debt-baseline",
            "reports/hygiene_debt_baseline.json",
            "--require-debt-baseline",
            "--max-new-untracked",
            "0",
            "--max-new-unstaged",
            "0",
            "--max-new-staged",
            "0",
        ),
        expected_exit_codes=(0, 1),
    ),
    AuditCommand(4, "Unstaged diff stat", ("git", "diff", "--stat")),
    AuditCommand(5, "Staged diff stat", ("git", "diff", "--cached", "--stat")),
    AuditCommand(6, "File index sample", ("rg", "--files")),
    AuditCommand(7, "Function definition scan", ("rg", "-n", "^def ", "src", "-g", "*.py")),
    AuditCommand(8, "TODO policy guard", ("python", "dev/todo_guard.py"), expected_exit_codes=(0, 1)),
    AuditCommand(9, "TODO/FIXME/HACK sample", ("rg", "-n", "TODO|FIXME|HACK", "src", "tests", "-g", "*.py")),
    AuditCommand(10, "Runtime print() scan in src", ("rg", "-n", r"\bprint\(", "src", "-g", "*.py"), expected_exit_codes=(0, 1)),
    AuditCommand(11, "Ruff lint", ("ruff", "check", "--no-cache", "src", "tests", "--output-format", "concise"), timeout_sec=180),
    AuditCommand(12, "Python version", ("python", "--version")),
    AuditCommand(13, "Environment doctor", ("python", "dev/env_doctor.py", "--no-require-venv"), timeout_sec=180, expected_exit_codes=(0, 1, 2)),
    AuditCommand(14, "Test collection", ("pytest", "-q", "tests", "--collect-only"), timeout_sec=240),
    AuditCommand(
        15,
        "Fast smoke tier",
        (
            "pytest",
            "-q",
            "tests/test_exit_codes.py",
            "tests/test_health_gate.py",
            "tests/test_surface_counts.py",
            "tests/test_competitor_site_data.py",
            "--maxfail=1",
        ),
        timeout_sec=240,
    ),
    AuditCommand(
        16,
        "Core tier",
        (
            "pytest",
            "-q",
            "tests/test_basic.py",
            "tests/test_exit_codes.py",
            "tests/test_health_gate.py",
            "tests/test_runtime.py",
            "tests/test_rules.py",
            "tests/test_surface_counts.py",
            "tests/test_competitor_site_data.py",
            "--maxfail=3",
        ),
        timeout_sec=360,
    ),
    AuditCommand(17, "Surface counts", ("python", "src/roam/surface_counts.py")),
    AuditCommand(18, "Recent commit graph", ("git", "log", "--oneline", "--graph", "-10")),
    AuditCommand(
        19,
        "Untracked count",
        (
            "python",
            "-c",
            (
                "import subprocess; "
                "out=subprocess.run(['git','ls-files','--others','--exclude-standard'],"
                "capture_output=True,text=True,check=False).stdout.splitlines(); "
                "print(len(out))"
            ),
        ),
    ),
    AuditCommand(
        20,
        "pyproject header",
        (
            "python",
            "-c",
            "from pathlib import Path; print('\\n'.join(Path('pyproject.toml').read_text(encoding='utf-8').splitlines()[:20]))",
        ),
    ),
)


@dataclass
class CommandResult:
    command: AuditCommand
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_sec: float

    @property
    def unexpected_exit(self) -> bool:
        return self.exit_code not in self.command.expected_exit_codes

    @property
    def execution_failed(self) -> bool:
        return self.timed_out or self.unexpected_exit


def _run(cmd: AuditCommand, cwd: Path) -> CommandResult:
    argv = list(cmd.command)
    if argv and argv[0] == "python":
        argv[0] = sys.executable
    start = time.perf_counter()
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=cmd.timeout_sec,
            check=False,
        )
        duration = time.perf_counter() - start
        return CommandResult(cmd, result.returncode, result.stdout, result.stderr, False, duration)
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or "").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        duration = time.perf_counter() - start
        return CommandResult(cmd, 124, stdout, stderr, True, duration)


def _trim_lines(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text.strip()
    kept = lines[:max_lines]
    kept.append(f"... ({len(lines) - max_lines} more lines truncated)")
    return "\n".join(kept).strip()


def _default_output(repo_root: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return repo_root / "reports" / f"command_audit_{stamp}.md"


def _line_count(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return len([line for line in stripped.splitlines() if line.strip()])


def _extract_collected_tests(text: str) -> int:
    total = 0
    for line in text.splitlines():
        m = re.search(r":\s+(\d+)\s*$", line)
        if m:
            total += int(m.group(1))
    return total


def _parse_hygiene_json(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _insights(results: dict[int, CommandResult]) -> tuple[list[str], list[str]]:
    findings: list[str] = []
    highlights: list[str] = []

    hygiene = _parse_hygiene_json(results[3].stdout.strip()) if 3 in results else None
    if hygiene:
        scope = hygiene.get("threshold_scope", "global")
        focus_counts = hygiene.get("counts_focus") or {}
        global_counts = hygiene.get("counts_global") or {}
        highlights.append(
            "hygiene focus counts: "
            f"staged={focus_counts.get('staged', 0)} "
            f"unstaged={focus_counts.get('unstaged', 0)} "
            f"untracked={focus_counts.get('untracked', 0)} "
            f"conflicts={focus_counts.get('conflicts', 0)} "
            f"(scope={scope})"
        )
        highlights.append(
            "hygiene global counts: "
            f"staged={global_counts.get('staged', 0)} "
            f"unstaged={global_counts.get('unstaged', 0)} "
            f"untracked={global_counts.get('untracked', 0)} "
            f"conflicts={global_counts.get('conflicts', 0)}"
        )
        top_roots = hygiene.get("top_untracked_roots", [])
        if top_roots:
            root_frag = ", ".join(f"{item['root']}={item['count']}" for item in top_roots[:3])
            highlights.append(f"top untracked roots: {root_frag}")
        debt_deltas = hygiene.get("debt_deltas")
        if isinstance(debt_deltas, dict):
            highlights.append(
                "debt deltas: "
                f"staged={debt_deltas.get('staged', 0)} "
                f"unstaged={debt_deltas.get('unstaged', 0)} "
                f"untracked={debt_deltas.get('untracked', 0)} "
                f"conflicts={debt_deltas.get('conflicts', 0)}"
            )
        failed_checks = hygiene.get("failed_checks", [])
        if failed_checks:
            findings.append("focused hygiene thresholds exceeded")
        debt_failed_checks = hygiene.get("debt_failed_checks", [])
        if debt_failed_checks:
            findings.append("new global hygiene debt detected vs baseline")

    todo_guard = results.get(8)
    if todo_guard and todo_guard.exit_code != 0:
        findings.append("TODO policy guard reported violations")

    print_scan = results.get(10)
    if print_scan and print_scan.exit_code == 0:
        print_hits = _line_count(print_scan.stdout)
        findings.append(f"runtime print() usage found in src ({print_hits} matches)")
    if print_scan and print_scan.exit_code == 1:
        highlights.append("runtime print() usage in src: none")

    lint = results.get(11)
    if lint and lint.exit_code != 0:
        findings.append("ruff lint reported issues")
    elif lint:
        highlights.append("ruff lint: pass")

    env = results.get(13)
    if env and env.exit_code != 0:
        findings.append("environment doctor reported venv/dependency issues")
    elif env:
        highlights.append("environment doctor: pass")

    collect = results.get(14)
    if collect:
        collected = _extract_collected_tests(collect.stdout)
        highlights.append(f"collected tests: {collected}")

    smoke = results.get(15)
    core = results.get(16)
    if smoke:
        highlights.append(f"smoke tier duration: {smoke.duration_sec:.2f}s (exit {smoke.exit_code})")
    if core:
        highlights.append(f"core tier duration: {core.duration_sec:.2f}s (exit {core.exit_code})")

    return findings, highlights


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the standard 20-command audit.")
    parser.add_argument("--output", type=Path, default=None, help="Markdown output path.")
    parser.add_argument("--max-output-lines", type=int, default=40, help="Per-command output line cap.")
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Return non-zero when command execution fails unexpectedly (timeout/unexpected exit).",
    )
    parser.add_argument(
        "--fail-on-finding",
        action="store_true",
        help="Return non-zero when diagnostic findings are present.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    output_path = args.output if args.output is not None else _default_output(repo_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sections: list[str] = []
    execution_failures = 0
    by_id: dict[int, CommandResult] = {}
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for command in COMMANDS:
        result = _run(command, repo_root)
        by_id[command.id] = result
        combined = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        trimmed = _trim_lines(combined, max_lines=max(args.max_output_lines, 1))
        if not trimmed:
            trimmed = "<no output>"

        if result.timed_out:
            status = f"timeout ({command.timeout_sec}s)"
        else:
            status = f"exit {result.exit_code}"
        if result.execution_failed:
            execution_failures += 1

        expectations = ", ".join(str(code) for code in command.expected_exit_codes)

        sections.append(
            "\n".join(
                [
                    f"## {command.id}) {command.title}",
                    "",
                    "**Command**",
                    "```bash",
                    " ".join(command.command),
                    "```",
                    f"**Result**: {status}",
                    f"**Expected exits**: {expectations}",
                    f"**Duration**: {result.duration_sec:.2f}s",
                    "```text",
                    trimmed,
                    "```",
                    "",
                ]
            )
        )

    findings, highlights = _insights(by_id)
    header = "\n".join(
        [
            f"# Command Audit ({now})",
            "",
            f"- Repo: `{repo_root}`",
            f"- Commands: `{len(COMMANDS)}`",
            f"- Execution failures: `{execution_failures}`",
            f"- Findings: `{len(findings)}`",
            "",
        ]
    )

    summary_block: list[str] = ["## Agent Insights", ""]
    if highlights:
        summary_block.append("### Highlights")
        summary_block.extend(f"- {line}" for line in highlights)
        summary_block.append("")
    if findings:
        summary_block.append("### Findings")
        summary_block.extend(f"- {line}" for line in findings)
        summary_block.append("")
    else:
        summary_block.extend(["### Findings", "- none", ""])

    summary_block.extend(
        [
            "### Next Actions",
            "- If hygiene remains noisy globally, decide whether large untracked roots should be committed or ignored.",
            "- Run inside a dedicated `.venv` to remove environment drift from diagnostics.",
            "- Keep using smoke/core tiers for fast local feedback before full-suite runs.",
            "",
        ]
    )

    output_path.write_text(header + "\n".join(summary_block) + "\n".join(sections), encoding="utf-8")
    print(f"Wrote {output_path}")
    print(f"Execution failures: {execution_failures}")
    print(f"Findings: {len(findings)}")

    if args.fail_on_error and execution_failures > 0:
        return 1
    if args.fail_on_finding and findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
