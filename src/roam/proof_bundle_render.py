"""Rendering for AgentChangeProofBundle v1 — markdown + SARIF.

Lifted out of `proof_bundle.py` so the composer module stays focused on
construction + validation. All public symbols are also re-exported from
`proof_bundle` for backwards compatibility — callers can import either
path.

Layout:

  * `render_markdown(v1)` — top-level reviewer markdown
  * `_md_*` — per-section helpers (testable in isolation)
  * `verdict_to_sarif(v1)` — SARIF 2.1.0 output for CI ingestion
"""

from __future__ import annotations

from typing import Any

from roam.guard_enums import VERDICT_ICONS

_VERDICT_ICONS = VERDICT_ICONS

# Switch the markdown files-block from flat list to grouped-by-directory
# when changed_files exceeds this threshold. Surfaces blast radius without
# burying reviewers in a 200-line file list.
_DIRECTORY_GROUPING_THRESHOLD = 20


def _format_reason_md(r: dict[str, Any]) -> str:
    """Format one verdict reason as a markdown list item.

    Handles both single reasons and aggregated reasons (with `count` + `checks`).
    Aggregated reasons render as a parent line + nested check list.
    """
    code = r.get("code", "?")
    count = r.get("count")
    checks = r.get("checks")
    if isinstance(checks, list) and count:
        context_parts = [f"`{k}={v}`" for k, v in r.items() if k not in ("code", "count", "checks") and v is not None]
        context = ", ".join(context_parts)
        head = f"- `{code}` (×{count})" + (f" — {context}" if context else "")
        show_limit = 3 if len(checks) > 5 else min(10, len(checks))
        nested = []
        for c in checks[:show_limit]:
            check_name = c.get("check") or c.get("command") or "?"
            extra = ", ".join(f"{k}={v}" for k, v in c.items() if k not in ("check", "command") and v is not None)
            nested.append(f"  - `{check_name}`" + (f" ({extra})" if extra else ""))
        if len(checks) > show_limit:
            nested.append(f"  - _… and {len(checks) - show_limit} more_")
        return head + "\n" + "\n".join(nested)
    context = ", ".join(f"`{k}={v}`" for k, v in r.items() if k != "code" and v is not None)
    return f"- `{code}`" + (f" — {context}" if context else "")


def render_markdown(v1: dict[str, Any]) -> str:
    """Render an AgentChangeProofBundle v1 as reviewer-readable markdown.

    Composed from per-section helpers (`_md_headline`, `_md_reasons`,
    `_md_checks_table`, `_md_risk_block`, `_md_findings_blocks`,
    `_md_files_block`, `_md_provenance_footer`). Each helper is testable
    in isolation; reordering or extending is a one-line list edit.
    """
    sections: list[str] = [
        _md_headline(v1),
        _md_reasons(v1),
        _md_checks_table(v1),
        _md_risk_block(v1),
        _md_findings_blocks(v1),
        _md_files_block(v1),
        _md_provenance_footer(v1),
    ]
    return "\n".join(s for s in sections if s)


def _md_headline(v1: dict[str, Any]) -> str:
    """Top of the report: verdict icon + summary one-liner."""
    verdict = v1.get("verdict") or {}
    verdict_val = verdict.get("value", "pass")
    icon = _VERDICT_ICONS.get(verdict_val, "?")
    contract = v1.get("verification_contract") or {}
    required = contract.get("required") or []
    executed = v1.get("executed_checks") or []
    missing = v1.get("missing_checks") or []
    risk_level = (v1.get("risk") or {}).get("level", "low")
    return (
        f"## {icon} Roam Guard verdict: `{verdict_val}`\n"
        f"\n"
        f"> **{len(executed)}** of **{len(required)}** required checks ran. "
        f"**{len(missing)}** missing. Risk: `{risk_level}`."
    )


def _md_reasons(v1: dict[str, Any]) -> str:
    """Verdict reasons block (aggregated form rendered with nested checks)."""
    reasons = (v1.get("verdict") or {}).get("reasons") or []
    if not reasons:
        return ""
    lines = ["### Verdict reasons"]
    for r in reasons[:8]:
        lines.append(_format_reason_md(r))
    if len(reasons) > 8:
        lines.append(f"- _… and {len(reasons) - 8} more_")
    return "\n".join(lines)


def _md_checks_table(v1: dict[str, Any]) -> str:
    """Markdown table: required check × ran/missing × why."""
    contract = v1.get("verification_contract") or {}
    required = contract.get("required") or []
    missing = v1.get("missing_checks") or []
    executed = v1.get("executed_checks") or []
    if not (required or missing):
        return ""
    lines = ["### Verification checks", "| Status | Command | Why |", "|---|---|---|"]
    executed_by_name = {c.get("command"): c for c in executed}
    for r in required:
        cmd = r.get("command", "?")
        why = r.get("reason", "—")
        if cmd in executed_by_name:
            status_icon = {"pass": "✅", "fail": "❌", "error": "⚠️"}.get(
                executed_by_name[cmd].get("status", "pass"), "✅"
            )
            lines.append(f"| {status_icon} ran | `{cmd}` | {why} |")
        else:
            lines.append(f"| 🛑 missing | `{cmd}` | {why} |")
    return "\n".join(lines)


def _md_risk_block(v1: dict[str, Any]) -> str:
    """Risk reasons + paths (only when level != low)."""
    risk = v1.get("risk") or {}
    risk_level = risk.get("level", "low")
    if risk_level == "low":
        return ""
    lines = [f"### Risk: `{risk_level}`"]
    for reason in (risk.get("reasons") or [])[:5]:
        lines.append(f"- {reason}")
    for path in (risk.get("paths") or [])[:10]:
        lines.append(f"- path: `{path}`")
    return "\n".join(lines)


def _md_findings_blocks(v1: dict[str, Any]) -> str:
    """Optimizer / scope / MCP tool findings — each its own section."""
    sections: list[str] = []
    for title, key in (
        ("Optimizer findings", "optimizer_findings"),
        ("Scope findings", "scope_findings"),
        ("MCP tool findings", "mcp_tool_findings"),
    ):
        items = v1.get(key) or []
        if not items:
            continue
        block = [f"### {title} ({len(items)})"]
        for f in items[:6]:
            subj = f.get("subject") or f.get("symbol") or f.get("kind") or "—"
            sev = f.get("severity", "")
            sev_str = f" ({sev})" if sev else ""
            block.append(f"- `{subj}`{sev_str}")
        sections.append("\n".join(block))
    return "\n\n".join(sections)


def _md_files_block(v1: dict[str, Any]) -> str:
    """Files-touched listing with truncation.

    Two render modes:
      * ≤ _DIRECTORY_GROUPING_THRESHOLD files → flat list (first 15 + "and N more")
      * > threshold → grouped by top-level directory with counts.
    """
    files = v1.get("changed_files") or []
    if not files:
        return ""
    if len(files) <= _DIRECTORY_GROUPING_THRESHOLD:
        lines = [f"### Files touched ({len(files)})"]
        for f in files[:15]:
            lines.append(f"- `{f}`")
        if len(files) > 15:
            lines.append(f"- _… and {len(files) - 15} more_")
        return "\n".join(lines)

    by_dir: dict[str, list[str]] = {}
    for f in files:
        top = f.split("/", 1)[0] if "/" in f else "(root)"
        by_dir.setdefault(top, []).append(f)
    sorted_dirs = sorted(by_dir.items(), key=lambda kv: -len(kv[1]))
    lines = [f"### Files touched ({len(files)}, across {len(by_dir)} top-level dir(s))"]
    for top, group in sorted_dirs[:10]:
        lines.append(f"- `{top}/` ({len(group)} files)")
        for f in group[:3]:
            lines.append(f"  - `{f}`")
        if len(group) > 3:
            lines.append(f"  - _… and {len(group) - 3} more in `{top}/`_")
    if len(sorted_dirs) > 10:
        remaining_dirs = len(sorted_dirs) - 10
        remaining_files = sum(len(g) for _, g in sorted_dirs[10:])
        lines.append(f"- _… and {remaining_files} files across {remaining_dirs} more dir(s)_")
    return "\n".join(lines)


def _md_provenance_footer(v1: dict[str, Any]) -> str:
    """Single-line provenance footer below a horizontal rule."""
    repo = v1.get("repo") or {}
    run = v1.get("run") or {}
    head = repo.get("head_sha") or "—"
    head_short = head[:12] if head and head != "—" else "—"
    agent = run.get("agent") or "—"
    mode = v1.get("mode") or "—"
    policy = v1.get("policy_profile") or "—"
    return (
        "---\n"
        f"_Bundle `{v1.get('schema_version', '1.0')}` · "
        f"head `{head_short}` · agent `{agent}` · "
        f"mode `{mode}` · policy `{policy}`_"
    )


# ---- SARIF 2.1.0 output — for GitHub Code Scanning / GitLab SAST / etc. ----

_VERDICT_TO_SARIF_LEVEL = {
    "pass": "note",
    "pass_with_warnings": "warning",
    "needs_review": "warning",
    "blocked": "error",
}

_REASON_TO_SARIF_LEVEL = {
    "required_check_not_run": "error",
    "required_checks_not_run": "error",
    "required_check_failed": "error",
    "required_checks_failed": "error",
    "policy_violation": "error",
    "ledger_integrity_failure": "error",
    "mcp_redaction_required": "error",
    "high_risk_path": "warning",
    "out_of_scope_edit": "warning",
    "missing_test_for_high_risk": "warning",
    "optimizer_warning": "warning",
    "optimizer_warnings": "warning",
    "scope_finding": "warning",
    "scope_findings": "warning",
    "mcp_tool_finding": "warning",
    "all_required_passed": "note",
}


def verdict_to_sarif(v1: dict[str, Any], *, tool_version: str = "1.0") -> dict[str, Any]:
    """Convert an AgentChangeProofBundle v1 into a SARIF 2.1.0 document.

    Each verdict reason becomes one SARIF result. Reasons are mapped to
    SARIF rule IDs (`roam.guard.<code>`). Locations point to the bundle's
    `changed_files` (best-effort — SARIF requires per-result locations).
    """
    from roam.output.sarif import to_sarif

    verdict = v1.get("verdict") or {}
    reasons = verdict.get("reasons") or []
    changed_files = v1.get("changed_files") or []

    seen_codes: set[str] = set()
    rules: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    verdict_value = verdict.get("value", "pass")
    umbrella_rule_id = "roam.guard.verdict"
    rules.append(
        {
            "id": umbrella_rule_id,
            "shortDescription": "Roam Guard PR verdict",
            "defaultLevel": _VERDICT_TO_SARIF_LEVEL.get(verdict_value, "note"),
            "helpUri": "https://roam-code.com/docs/roam-guard",
        }
    )
    location = (
        [{"physicalLocation": {"artifactLocation": {"uri": changed_files[0]}}}]
        if changed_files
        else [{"physicalLocation": {"artifactLocation": {"uri": "."}}}]
    )
    results.append(
        {
            "ruleId": umbrella_rule_id,
            "level": _VERDICT_TO_SARIF_LEVEL.get(verdict_value, "note"),
            "message": f"Roam Guard verdict: {verdict_value}",
            "locations": location,
        }
    )

    for r in reasons:
        code = r.get("code")
        if not code:
            continue
        rule_id = f"roam.guard.{code}"
        if code not in seen_codes:
            rules.append(
                {
                    "id": rule_id,
                    "shortDescription": code.replace("_", " "),
                    "defaultLevel": _REASON_TO_SARIF_LEVEL.get(code, "warning"),
                }
            )
            seen_codes.add(code)
        ctx_pairs = [f"{k}={v}" for k, v in r.items() if k != "code" and v is not None]
        ctx = "; ".join(ctx_pairs) if ctx_pairs else code
        detail = r.get("detail")
        if isinstance(detail, list) and detail and isinstance(detail[0], str):
            res_locations = [{"physicalLocation": {"artifactLocation": {"uri": d}}} for d in detail[:5]]
        else:
            res_locations = location
        results.append(
            {
                "ruleId": rule_id,
                "level": _REASON_TO_SARIF_LEVEL.get(code, "warning"),
                "message": ctx,
                "locations": res_locations,
            }
        )

    return to_sarif(
        tool_name="roam-guard",
        version=tool_version,
        rules=rules,
        results=results,
    )
