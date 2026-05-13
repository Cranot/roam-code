"""Render a codebase-architecture audit report from roam-code outputs.

Usage:

    python templates/audit-report/render.py \\
        --client "Acme Inc" \\
        --date 2026-05-05 \\
        --repo /path/to/target/repo \\
        --output audit-report.md

The script:
  1. Confirms the ``roam`` CLI is on PATH.
  2. Runs ``roam audit --json``, ``roam describe --agent-prompt --json``,
     ``roam bus-factor --json``, and ``roam map --json`` in --repo.
  3. Substitutes the results into ``audit-report.md.tmpl``.
  4. Writes the filled markdown to ``--output`` (or stdout).

If a roam subcommand fails, that section is replaced with an inline
"_command failed: ..._" block; the rest of the report still emits.

After rendering, the auditor fills the ``<!-- TODO[narrative]: ... -->``
blocks by hand and runs Pandoc to produce a PDF; see this dir's README.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TEMPLATE_PATH = HERE / "audit-report.md.tmpl"


# ---------------------------------------------------------------- helpers ---

def _run_roam(repo: Path, args: list[str]) -> dict:
    """Invoke ``roam --json <args>`` in --repo and return parsed JSON.

    Returns ``{"_error": ...}`` on any failure so the caller can inline
    the failure into the report rather than abort the whole render.
    """
    cmd = ["roam", "--json", *args]
    try:
        proc = subprocess.run(
            cmd, cwd=str(repo), capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        return {"_error": f"roam not on PATH: {exc}"}
    # Exit code 5 == gate failure but JSON still produced.
    if proc.returncode not in (0, 5):
        return {
            "_error": f"exit {proc.returncode}",
            "_stderr_tail": (proc.stderr or "")[-200:],
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {
            "_error": f"non-JSON output: {exc}",
            "_stdout_head": (proc.stdout or "")[:200],
        }


def _format_hours(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    if value < 1:
        return f"{value * 60:.0f} min"
    return f"{value:.1f} h"


def _format_pct(pct: Any) -> str:
    if not isinstance(pct, (int, float)):
        return "n/a"
    return f"{pct:.0f}%"


def _format_severity_counts(d: Any) -> str:
    """Render a {'CRITICAL': 27, 'WARNING': 10, ...} dict as a stable string."""
    if not isinstance(d, dict):
        return str(d)
    order = ["CRITICAL", "WARNING", "INFO", "NOTE"]
    parts = []
    for key in order:
        if key in d:
            parts.append(f"{key}: {d[key]}")
    for key in sorted(d):
        if key not in order:
            parts.append(f"{key}: {d[key]}")
    return ", ".join(parts) if parts else "—"


def _detect_roam_version() -> str:
    try:
        out = subprocess.check_output(["roam", "--version"], text=True).strip()
        return out
    except Exception:
        return "unknown"


# --------------------------------------------------- per-section renderers ---

def _render_overview(describe: dict) -> str:
    if "_error" in describe:
        return f"_describe command failed: {describe['_error']}_\n"
    parts = [
        f"- **Project:** {describe.get('project', 'n/a')}",
        f"- **Files / symbols indexed:** {describe.get('files', 'n/a')} / {describe.get('symbols', 'n/a')}",
        f"- **Languages (top 5):** {describe.get('languages', 'n/a')}",
    ]
    if describe.get("stack"):
        parts.append(f"- **Top dependencies:** {describe['stack']}")
    if describe.get("structure"):
        parts.append(f"- **Top-level structure:** {describe['structure']}")
    if describe.get("conventions"):
        parts.append(f"- **Detected conventions:** {describe['conventions']}")
    if describe.get("test_cmd"):
        parts.append(f"- **Inferred test command:** `{describe['test_cmd']}`")
    return "\n".join(parts) + "\n"


def _render_skeleton(map_data: dict, health: dict) -> str:
    """Combine map's structural skeleton with health's cycle/tangle metrics."""
    if "_error" in map_data:
        return f"_map command failed: {map_data['_error']}_\n"
    s = map_data.get("summary") or {}
    parts = [
        f"- **Map verdict:** {s.get('verdict', 'n/a')}",
    ]
    edges = s.get("edges")
    if edges is not None:
        parts.append(f"- **Inferred edges (call / import):** {edges}")

    entry_points = map_data.get("entry_points") or []
    if entry_points:
        ep_paths = [
            f"`{e.get('path', '?')}`" if isinstance(e, dict) else f"`{e}`"
            for e in entry_points[:6]
        ]
        parts.append(f"- **Entry points (top 6):** {', '.join(ep_paths)}")

    top_symbols = map_data.get("top_symbols") or []
    if top_symbols:
        sym_strs = []
        for sym in top_symbols[:5]:
            if isinstance(sym, dict):
                sym_strs.append(f"`{sym.get('name', '?')}` ({sym.get('kind', 'symbol')})")
            else:
                sym_strs.append(f"`{sym}`")
        parts.append(f"- **Top symbols by PageRank:** {', '.join(sym_strs)}")

    # Cycle / tangle data lives in the health envelope, not in map.
    if health and "_error" not in health:
        h_summary = health.get("summary") or {}
        actionable = h_summary.get("actionable_cycles")
        tangle = h_summary.get("tangle_ratio")
        prop = h_summary.get("propagation_cost")
        bits = []
        if actionable is not None:
            bits.append(f"actionable cycles: **{actionable}**")
        if isinstance(tangle, (int, float)):
            bits.append(f"tangle ratio: **{tangle:.3f}**")
        if isinstance(prop, (int, float)):
            bits.append(f"propagation cost: **{prop:.2f}**")
        if bits:
            parts.append("- **Architectural metrics:** " + " · ".join(bits))

    return "\n".join(parts) + "\n"


def _render_health(health: dict) -> str:
    if "_error" in health:
        return f"_health command failed: {health['_error']}_\n"
    s = health.get("summary") or {}
    parts = [
        f"**Composite health score:** {s.get('health_score', 'n/a')} / 100 — _{s.get('verdict', 'no verdict')}_",
        f"**Issue mix:** {_format_severity_counts(s.get('severity'))} · "
        f"**Issue count:** {s.get('issue_count', 'n/a')} · "
        f"**Actionable cycles:** {s.get('actionable_cycles', 'n/a')}",
        "",
    ]

    cat_severity = s.get("category_severity") or {}
    if cat_severity:
        parts.append("**Category severities:**\n")
        rows = ["| Category | CRITICAL | WARNING | INFO |", "|---|---|---|---|"]
        for cat, sev in sorted(cat_severity.items()):
            if isinstance(sev, dict):
                rows.append(
                    f"| {cat} | {sev.get('CRITICAL', 0)} | "
                    f"{sev.get('WARNING', 0)} | {sev.get('INFO', 0)} |"
                )
            else:
                rows.append(f"| {cat} | {sev} | — | — |")
        parts.append("\n".join(rows))

    # actionable_cycles is a count, not a list; the per-cycle detail
    # lives behind `roam cycles --json` and is not folded into the audit
    # envelope. We point at it from Appendix B for reproduction.

    return "\n".join(parts) + "\n"


def _render_risk(hotspots: dict) -> str:
    if "_error" in hotspots:
        return f"_hotspots --danger command failed: {hotspots['_error']}_\n"
    files = hotspots.get("danger_zone") or []
    if not files:
        return "_No danger-zone files at the configured thresholds._\n"
    rows = [
        "| # | File | Score | Churn | Complexity | Fan-in |",
        "|---|---|---|---|---|---|",
    ]
    for i, f in enumerate(files[:10], 1):
        path = f.get("path") or "?"
        score = f.get("danger_score")
        score_str = f"{score:.2f}" if isinstance(score, (int, float)) else "?"
        churn = f.get("churn", "?")
        complexity = f.get("complexity")
        complexity_str = f"{complexity:.0f}" if isinstance(complexity, (int, float)) else "?"
        fan_in = f.get("max_fan_in", "?")
        rows.append(f"| {i} | `{path}` | {score_str} | {churn} | {complexity_str} | {fan_in} |")
    return "\n".join(rows) + "\n"


def _render_dead(dead: dict) -> str:
    if "_error" in dead:
        return f"_dead command failed: {dead['_error']}_\n"
    s = dead.get("summary") or {}
    safe = s.get("safe", 0)
    review = s.get("review", 0)
    intentional = s.get("intentional", 0)
    scaffolding = s.get("scaffolding", 0)
    test_only = s.get("test_only", 0)
    total_loc = s.get("total_dead_loc", 0)
    effort_h = s.get("total_effort_hours", 0)
    actionable = safe + review

    parts = [
        f"_{s.get('verdict', 'no verdict')}_",
        "",
        "| Bucket | Count |",
        "|---|---|",
        f"| **SAFE** to remove | {safe} |",
        f"| **REVIEW** manually | {review} |",
        f"| **INTENTIONAL** (keep) | {intentional} |",
        f"| Test-only | {test_only} |",
        f"| Scaffolding | {scaffolding} |",
        "",
        f"**Total dead lines of code:** {total_loc:,} · "
        f"**Estimated removal effort:** {effort_h:.0f} h · "
        f"**Auditor-actionable:** {actionable} (SAFE + REVIEW)",
        "",
        "_Run `roam dead --detail --json` against the repo for the full per-symbol "
        "list (it is omitted here for size). The detail envelope is reproducible "
        "from the same git SHA._",
    ]
    return "\n".join(parts) + "\n"


def _render_bus_factor(bf: dict) -> str:
    if "_error" in bf:
        return f"_bus-factor command failed: {bf['_error']}_\n"
    s = bf.get("summary") or {}
    dirs = bf.get("directories") or []

    header = (
        f"_{s.get('verdict', 'no verdict')}_\n\n"
        f"**Team profile:** {s.get('project_team_size', 'n/a')} · "
        f"**Concentrated dirs:** {s.get('concentrated', 0)} / {s.get('directories_analyzed', s.get('directory_count', 0))} · "
        f"**HIGH-risk dirs:** {s.get('high_risk', 0)} · "
        f"**Critical-entropy dirs:** {s.get('critical_entropy', 0)}\n"
    )

    if not dirs:
        return header + "_No per-directory breakdown available._\n"

    rows = [
        "| Directory | Risk | Bus-factor | Primary author | Share | Stale? |",
        "|---|---|---|---|---|---|",
    ]
    for d in dirs[:15]:
        path = d.get("directory") or "?"
        risk = d.get("risk") or d.get("knowledge_risk") or "?"
        bus = d.get("bus_factor", "?")
        author = (d.get("primary_author") or "?")[:25]
        share = d.get("primary_share")
        share_str = f"{share * 100:.0f}%" if isinstance(share, (int, float)) else "?"
        stale = "yes" if d.get("stale_primary") else "no"
        rows.append(f"| `{path}` | {risk} | {bus} | {author} | {share_str} | {stale} |")
    return header + "\n" + "\n".join(rows) + "\n"


def _render_test(audit_sections: dict) -> str:
    pyramid = audit_sections.get("test_pyramid") or {}
    if "_error" in pyramid:
        return f"_test-pyramid command failed: {pyramid['_error']}_\n"
    s = pyramid.get("summary") or {}
    health = audit_sections.get("health") or {}
    h_summary = health.get("summary") or {}
    coverage_pct = h_summary.get("imported_coverage_pct")
    return (
        f"- **Total test files indexed:** {s.get('total', 0)}\n"
        f"- **Unit / integration / e2e / smoke split:** "
        f"{s.get('unit', 0)} / {s.get('integration', 0)} / "
        f"{s.get('e2e', 0)} / {s.get('smoke', 0)}\n"
        f"- **Imported test coverage (heuristic):** "
        f"{_format_pct(coverage_pct)}\n"
        f"- **Verdict:** {s.get('verdict', 'n/a')}\n"
    )


def _render_agent_prompt(describe: dict) -> str:
    if "_error" in describe:
        return "_describe command failed; cannot generate agent prompt._"
    fragments: list[str] = []
    fragments.append(
        f"Project: {describe.get('project', '<project>')} "
        f"({describe.get('files', 0)} files, "
        f"{describe.get('symbols', 0)} symbols, "
        f"{describe.get('languages', 'unknown')})"
    )
    if describe.get("stack"):
        fragments.append(f"Stack: {describe['stack']}")
    if describe.get("conventions"):
        fragments.append(f"Conventions: {describe['conventions']}")
    if describe.get("structure"):
        fragments.append(f"Structure: {describe['structure']}")
    if describe.get("test_cmd"):
        fragments.append(f"Test cmd: {describe['test_cmd']}")
    return "\n".join(fragments)


# -------------------------------------------------------------------- main ---

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--client", required=True, help="Client name (appears in title)")
    p.add_argument("--date", required=True, help="Audit date (ISO 8601, e.g. 2026-05-05)")
    p.add_argument("--repo", required=True, help="Path to the target repo (must already be indexed)")
    p.add_argument("--output", help="Output markdown path; defaults to stdout")
    p.add_argument(
        "--include-raw",
        action="store_true",
        help="Append raw audit JSON to a hidden Appendix C (useful for archival).",
    )
    args = p.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.exists():
        print(f"--repo {repo} does not exist", file=sys.stderr)
        return 2
    if shutil.which("roam") is None:
        print("roam CLI not on PATH; activate the venv or run `pip install -e .`", file=sys.stderr)
        return 2

    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    audit = _run_roam(repo, ["audit"])
    describe = _run_roam(repo, ["describe", "--agent-prompt"])
    bus_factor = _run_roam(repo, ["bus-factor"])
    map_data = _run_roam(repo, ["map"])

    sections = audit.get("sections") or {}
    summary = audit.get("summary") or {}

    # Pull authoritative values from each section's own summary; the audit
    # rolled-up summary doesn't always populate downstream fields cleanly.
    health_section = sections.get("health") or {}
    h_summary = health_section.get("summary") or {}
    debt_section = sections.get("debt") or {}
    d_summary = debt_section.get("summary") or {}
    dead_section = sections.get("dead") or {}
    de_summary = dead_section.get("summary") or {}
    danger_summary = (sections.get("hotspots_danger") or {}).get("summary") or {}
    stats_summary = (sections.get("stats") or {}).get("summary") or {}

    actionable_dead = de_summary.get("safe", 0) + de_summary.get("review", 0)

    substitutions = {
        "{{CLIENT_NAME}}": args.client,
        "{{AUDIT_DATE}}": args.date,
        "{{VERDICT}}": str(summary.get("verdict") or "n/a"),
        "{{HEALTH_SCORE}}": str(h_summary.get("health_score", summary.get("health_score", "n/a"))),
        "{{DEBT_TOTAL_HOURS}}": _format_hours(d_summary.get("total_remediation_hours")),
        "{{DEAD_COUNT}}": str(actionable_dead),
        "{{DANGER_ZONE_COUNT}}": str(danger_summary.get("count", 0)),
        "{{COVERAGE_PCT}}": _format_pct(h_summary.get("imported_coverage_pct")),
        "{{API_SURFACE}}": str(summary.get("api_surface") or audit.get("api_count") or "n/a"),
        "{{FILE_TOTAL}}": str(stats_summary.get("file_total") or summary.get("file_total", "n/a")),
        "{{SYMBOL_TOTAL}}": str(stats_summary.get("symbol_total") or summary.get("symbol_total", "n/a")),
        "{{REPO_OVERVIEW}}": _render_overview(describe),
        "{{ARCHITECTURE_MAP}}": _render_skeleton(map_data, health_section),
        "{{HEALTH_FINDINGS}}": _render_health(health_section),
        "{{RISK_FINDINGS}}": _render_risk(sections.get("hotspots_danger") or {}),
        "{{DEAD_FINDINGS}}": _render_dead(dead_section),
        "{{OWNER_FINDINGS}}": _render_bus_factor(bus_factor),
        "{{TEST_FINDINGS}}": _render_test(sections),
        "{{AGENT_PROMPT_BLOCK}}": _render_agent_prompt(describe),
        "{{ROAM_VERSION}}": _detect_roam_version(),
    }

    rendered = template
    for placeholder, value in substitutions.items():
        rendered = rendered.replace(placeholder, value)

    if args.include_raw:
        rendered += (
            "\n\n# Appendix C — Raw audit JSON\n\n"
            "```json\n" + json.dumps(audit, indent=2) + "\n```\n"
        )

    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
