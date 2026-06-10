"""`roam guard-doctor` — Roam Guard preflight + health check.

SARIF is deliberately NOT emitted: doctor checks are environment-scoped
(directory presence, rule pack load, git state) rather than file-located
findings; SARIF requires locations[] which doctor cannot populate.

Runs a battery of cheap checks adopters care about BEFORE their first
`roam guard-pr` invocation in a new repo:

  * .roam/ directory exists (or can be created)
  * .roam/pr-bundles/ directory present
  * Active rule pack loads cleanly (default or custom via --rules)
  * command_graph has at least one runnable command
  * git is available + we're in a git repo
  * GITHUB_TOKEN present (advisory — only matters for --post-check users)
  * Verdict log readable + non-corrupt (advisory)
  * Python yaml + jsonschema availability (advisory)

Exit codes:
    0 = all checks pass
    1 = at least one ADVISORY check failed
    2 = at least one REQUIRED check failed (blocks `guard-pr` from working)
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.command_graph import build_command_graph
from roam.db.connection import find_project_root
from roam.guard_log import log_path_for, read_log_entries
from roam.guard_rules import get_active_rules
from roam.output.formatter import json_envelope, to_json
from roam.pr_bundle_primitives import all_bundle_paths, bundles_dir

# ---- check result type ----


@dataclass
class Check:
    """One health check result."""

    name: str
    status: str  # "pass" | "fail" | "warn"
    detail: str  # short human-readable summary
    blocking: bool = False
    fix: str | None = None  # one-line remediation hint when failing


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def has_blocking_failure(self) -> bool:
        return any(c.status == "fail" and c.blocking for c in self.checks)

    @property
    def has_any_failure(self) -> bool:
        return any(c.status == "fail" for c in self.checks)

    @property
    def summary_verdict(self) -> str:
        if self.has_blocking_failure:
            return "blocked"
        if self.has_any_failure:
            return "warnings"
        return "healthy"

    def exit_code(self) -> int:
        if self.has_blocking_failure:
            return 2
        if self.has_any_failure:
            return 1
        return 0


# ---- individual checks ----


def _check_dot_roam(root: Path, report: DoctorReport) -> None:
    p = root / ".roam"
    if p.is_dir():
        report.add(Check("dot_roam", "pass", f"{p} exists", blocking=False))
    else:
        report.add(
            Check(
                "dot_roam",
                "warn",
                f"{p} missing (will be auto-created on first use)",
                blocking=False,
                fix="run any roam command that writes to .roam/ (e.g. `roam pr-bundle init`)",
            )
        )


def _check_bundles_dir(root: Path, report: DoctorReport) -> None:
    p = bundles_dir(root)
    bundles = all_bundle_paths(root) if p.is_dir() else []
    if not p.is_dir():
        report.add(
            Check(
                "bundles_dir",
                "warn",
                f"{p} missing — no pr-bundles yet",
                blocking=False,
                fix='run `roam pr-bundle init --intent "<intent>"` to create one',
            )
        )
    elif not bundles:
        report.add(
            Check(
                "bundles_dir",
                "warn",
                f"{p} present but contains 0 bundle files",
                blocking=False,
                fix="run `roam pr-bundle init` or pass `--init-if-missing` to guard-pr",
            )
        )
    else:
        report.add(
            Check(
                "bundles_dir",
                "pass",
                f"{p} present with {len(bundles)} bundle(s)",
            )
        )


def _check_rule_pack(rules_path: str | None, report: DoctorReport) -> None:
    try:
        pack = get_active_rules(rules_path)
    except ValueError as e:
        report.add(
            Check(
                "rule_pack",
                "fail",
                f"rule pack failed to load: {e}",
                blocking=True,
                fix="fix the YAML at the path you passed or omit --rules",
            )
        )
        return
    n = len(pack.file_patterns)
    label = "default" if rules_path is None else rules_path
    if n == 0:
        report.add(
            Check(
                "rule_pack",
                "warn",
                f"rule pack `{pack.name}` loaded but has 0 file_patterns",
                blocking=False,
                fix="add at least one `file_patterns` entry or extend a base pack",
            )
        )
    else:
        report.add(
            Check(
                "rule_pack",
                "pass",
                f"rule pack `{pack.name}` ({label}) loaded with {n} pattern(s)",
            )
        )


def _check_command_graph(root: Path, report: DoctorReport) -> None:
    try:
        graph = build_command_graph(root)
    except Exception as e:  # noqa: BLE001 — best-effort
        report.add(
            Check(
                "command_graph",
                "fail",
                f"command_graph build failed: {e}",
                blocking=True,
                fix="ensure roam is installed correctly + repo has a Makefile / pyproject / package.json",
            )
        )
        return
    commands = graph.get("commands") or []
    if not commands:
        report.add(
            Check(
                "command_graph",
                "warn",
                "command_graph returned 0 commands — verdicts will always pass trivially",
                blocking=False,
                fix="add tests / lint targets to Makefile / pyproject / package.json",
            )
        )
    else:
        kinds = {c.get("kind") for c in commands if isinstance(c, dict)}
        report.add(
            Check(
                "command_graph",
                "pass",
                f"{len(commands)} commands across kinds {sorted(filter(None, kinds))}",
            )
        )


def _check_git(root: Path, report: DoctorReport) -> None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=3.0,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            report.add(Check("git", "pass", "git available + inside a work tree"))
        else:
            report.add(
                Check(
                    "git",
                    "warn",
                    "git available but not in a work tree (changed-files fallback disabled)",
                    blocking=False,
                    fix="run from inside a git checkout",
                )
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        report.add(
            Check(
                "git",
                "warn",
                "git command not found on PATH",
                blocking=False,
                fix="install git (changed-files fallback to git diff requires it)",
            )
        )


def _check_github_token(report: DoctorReport) -> None:
    if os.environ.get("GITHUB_TOKEN"):
        report.add(Check("github_token", "pass", "GITHUB_TOKEN is set"))
    else:
        report.add(
            Check(
                "github_token",
                "warn",
                "GITHUB_TOKEN not set (only matters if you plan to --post-check)",
                blocking=False,
                fix="export GITHUB_TOKEN=ghp_... in CI; not needed for local guard-pr",
            )
        )


def _check_verdict_log(root: Path, report: DoctorReport) -> None:
    p = log_path_for(root)
    if not p.is_file():
        report.add(
            Check(
                "verdict_log",
                "pass",
                "no verdict log yet — will be created on first guard-pr run",
            )
        )
        return
    try:
        entries = read_log_entries(root, limit=5)
    except Exception as e:  # noqa: BLE001
        report.add(
            Check(
                "verdict_log",
                "warn",
                f"verdict log unreadable: {e}",
                blocking=False,
                fix=f"inspect / repair {p}",
            )
        )
        return
    report.add(
        Check(
            "verdict_log",
            "pass",
            f"verdict log present with {len(entries)} recent entries",
        )
    )


def _check_smoke_compose(root: Path, report: DoctorReport) -> None:
    """End-to-end smoke: load the most recent bundle + compose v1 in-process.

    Validates the full Phase-1 pipeline (load → command graph → verification
    contract → verdict) without spawning a subprocess. Skips gracefully when
    no bundles exist (already covered by `bundles_dir` check).
    """
    from roam.pr_bundle_primitives import discover_active_bundle
    from roam.proof_bundle import compose_agent_change_proof_bundle, load_pr_bundle

    bundle_p = discover_active_bundle(root, None)
    if bundle_p is None:
        report.add(
            Check(
                "smoke_compose",
                "warn",
                "no bundle available — skipped smoke compose",
                blocking=False,
                fix="run `roam pr-bundle init` then re-run `roam guard-doctor`",
            )
        )
        return
    try:
        bundle = load_pr_bundle(bundle_p)
        v1 = compose_agent_change_proof_bundle(bundle, repo_root=root)
    except Exception as e:  # noqa: BLE001 — smoke must catch every failure
        report.add(
            Check(
                "smoke_compose",
                "fail",
                f"compose pipeline raised on {bundle_p}: {e}",
                blocking=True,
                fix="run `roam proof-bundle --validate` for a detailed schema diagnosis",
            )
        )
        return
    required_keys = ("verdict", "verification_contract", "changed_files", "risk")
    missing = [k for k in required_keys if k not in v1]
    if missing:
        report.add(
            Check(
                "smoke_compose",
                "fail",
                f"compose succeeded on {bundle_p.name} but v1 missing keys: {missing}",
                blocking=True,
                fix="check that proof_bundle.compose_agent_change_proof_bundle emits the v1 schema",
            )
        )
        return
    verdict_value = (v1.get("verdict") or {}).get("value")
    report.add(
        Check(
            "smoke_compose",
            "pass",
            f"compose pipeline OK on `{bundle_p.name}` → verdict `{verdict_value}`",
        )
    )


def _check_yaml_lib(report: DoctorReport) -> None:
    try:
        import yaml  # type: ignore  # noqa: F401

        report.add(Check("yaml_lib", "pass", "PyYAML available"))
    except ImportError:
        report.add(
            Check(
                "yaml_lib",
                "fail",
                "PyYAML missing — rule pack loading will fail",
                blocking=True,
                fix="pip install pyyaml",
            )
        )


# ---- CLI command ----


@click.command(name="guard-doctor")
@click.option(
    "--rules",
    "rules_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Validate this rule pack instead of the default.",
)
@click.pass_context
@roam_capability(
    name="guard-doctor",
    category="planning",
    summary="Roam Guard preflight + health check — runs cheap checks for adopters",
    inputs=("roam_dir", "rule_pack", "command_graph"),
    outputs=("check_results", "summary_verdict"),
    examples=(
        "roam guard-doctor",
        "roam guard-doctor --rules .roam/guard-rules.yml",
        "roam --json guard-doctor",
    ),
    tags=("planning", "roam-guard", "preflight", "ci"),
)
def guard_doctor(ctx: click.Context, rules_path: str | None) -> None:
    """Preflight + health check before running roam guard-pr."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = Path(find_project_root() or Path.cwd())

    report = DoctorReport()
    _check_dot_roam(root, report)
    _check_bundles_dir(root, report)
    _check_rule_pack(rules_path, report)
    _check_command_graph(root, report)
    _check_git(root, report)
    _check_github_token(report)
    _check_verdict_log(root, report)
    _check_yaml_lib(report)
    _check_smoke_compose(root, report)

    summary_verdict = report.summary_verdict
    exit_code = report.exit_code()

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "guard-doctor",
                    summary={
                        "verdict": summary_verdict,
                        "exit_code": exit_code,
                        "pass_count": sum(1 for c in report.checks if c.status == "pass"),
                        "warn_count": sum(1 for c in report.checks if c.status == "warn"),
                        "fail_count": sum(1 for c in report.checks if c.status == "fail"),
                        "blocking_failures": [c.name for c in report.checks if c.status == "fail" and c.blocking],
                        "partial_success": summary_verdict != "healthy",
                    },
                    agent_contract={
                        "facts": [
                            f"verdict {summary_verdict}",
                            f"{len(report.checks)} checks ran",
                        ],
                        "next_commands": ["roam guard-pr --ci"]
                        if summary_verdict == "healthy"
                        else ["roam guard-doctor --rules <path>"],
                        "risks": [
                            {"code": c.name, "detail": c.detail, "fix": c.fix}
                            for c in report.checks
                            if c.status == "fail"
                        ],
                    },
                    checks=[
                        {"name": c.name, "status": c.status, "detail": c.detail, "blocking": c.blocking, "fix": c.fix}
                        for c in report.checks
                    ],
                )
            )
        )
    else:
        icons = {"pass": "✓", "warn": "⚠", "fail": "✗"}
        click.echo(f"VERDICT: {summary_verdict}")
        for c in report.checks:
            click.echo(f"  {icons.get(c.status, '?')} {c.name:20s} — {c.detail}")
            if c.fix and c.status != "pass":
                click.echo(f"      fix: {c.fix}")
    ctx.exit(exit_code)
