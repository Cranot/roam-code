"""proof_bundle — AgentChangeProofBundle v1 composer.

Reads a legacy `pr-bundle` JSON (at `.roam/pr-bundles/<branch>.json`) and
produces the AgentChangeProofBundle v1 dict per the schema spec.

Wires together the three already-shipped modules:
  * command_graph (G2 — what CAN be run)
  * verification_contract (G3 — what MUST run)
  * verdict (closed-enum verdict engine)

Per the pivot memo, this is the Item-3 deliverable for Roam Guard MVP
Phase 1 — keeps the existing `roam pr-bundle emit` untouched (which carries
years of W-series audits) and ships the v1 schema as a sibling artifact.

Caller responsibilities:
  * Pass in repo_root for command_graph + git head_sha resolution.
  * Pass in policy_profile / mode if not already on the bundle.

Output is a dict matching the v1 schema; serialize with json.dumps directly.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from roam.command_graph import build_command_graph
from roam.guard_enums import (
    CHECK_STATUSES as _V1_CHECK_STATUSES,
)
from roam.guard_enums import (
    MODES as _V1_MODES,
)
from roam.guard_enums import (
    POLICY_PROFILES as _V1_POLICY_PROFILES,
)
from roam.guard_enums import (
    RISK_LEVELS as _V1_RISK_LEVELS,
)
from roam.guard_enums import (
    VERDICTS as _V1_VERDICTS,
)
from roam.guard_rules import RulePack
from roam.output._severity import severity_rank
from roam.verdict import compute_verdict
from roam.verification_contract import build_verification_contract

PROOF_BUNDLE_SCHEMA = "agent_change_proof_bundle"
PROOF_BUNDLE_SCHEMA_VERSION = "1.0"

_SCHEMA_PATH = Path(__file__).parent / "schemas" / "agent_change_proof_bundle.v1.json"


def get_v1_schema() -> dict[str, Any]:
    """Return the AgentChangeProofBundle v1 JSON Schema as a dict."""
    return json.loads(_SCHEMA_PATH.read_text())


# Top-level required fields per the v1 schema.
_REQUIRED_V1_FIELDS = (
    "schema",
    "schema_version",
    "changed_files",
    "verification_contract",
    "executed_checks",
    "missing_checks",
    "verdict",
)

# Closed-enum values now imported from guard_enums (single source of truth).
# The aliases (`_V1_VERDICTS`, etc.) keep the validator code unchanged below.


def validate_v1(v1: dict[str, Any]) -> list[str]:
    """Validate v1 bundle against the schema's required fields + closed enums.

    Returns a list of error strings. Empty list = valid. Best-effort: this
    is NOT a full JSON Schema Draft 2020-12 validator (no extra deps); it
    enforces the load-bearing constraints — required fields + closed enums.
    Consumers needing full validation can use `jsonschema` against the
    schema returned by `get_v1_schema()`.
    """
    errors: list[str] = []
    if not isinstance(v1, dict):
        return [f"v1 must be an object, got {type(v1).__name__}"]
    for f in _REQUIRED_V1_FIELDS:
        if f not in v1:
            errors.append(f"missing required field: {f}")
    if v1.get("schema") not in (PROOF_BUNDLE_SCHEMA, None):
        errors.append(f"schema must be '{PROOF_BUNDLE_SCHEMA}', got {v1.get('schema')!r}")
    if "mode" in v1 and v1["mode"] not in _V1_MODES:
        errors.append(f"mode must be one of {_V1_MODES}, got {v1['mode']!r}")
    if "policy_profile" in v1 and v1["policy_profile"] not in _V1_POLICY_PROFILES:
        errors.append(f"policy_profile must be one of {_V1_POLICY_PROFILES}, got {v1['policy_profile']!r}")
    verdict = v1.get("verdict")
    if isinstance(verdict, dict):
        if verdict.get("value") not in _V1_VERDICTS:
            errors.append(f"verdict.value must be one of {_V1_VERDICTS}, got {verdict.get('value')!r}")
        reasons = verdict.get("reasons")
        if not isinstance(reasons, list):
            errors.append("verdict.reasons must be an array")
        else:
            for i, r in enumerate(reasons):
                if not isinstance(r, dict) or "code" not in r:
                    errors.append(f"verdict.reasons[{i}] missing required field 'code'")
    elif "verdict" in v1:
        errors.append("verdict must be an object")
    risk = v1.get("risk")
    if isinstance(risk, dict) and "level" in risk and risk["level"] not in _V1_RISK_LEVELS:
        errors.append(f"risk.level must be one of {_V1_RISK_LEVELS}, got {risk['level']!r}")
    executed = v1.get("executed_checks", [])
    if isinstance(executed, list):
        for i, c in enumerate(executed):
            if isinstance(c, dict) and "status" in c and c["status"] not in _V1_CHECK_STATUSES:
                errors.append(f"executed_checks[{i}].status must be one of {_V1_CHECK_STATUSES}, got {c['status']!r}")
    return errors


def _extract_changed_files(bundle: dict[str, Any]) -> list[str]:
    """Pull unique file paths from affected_symbols + tests_required + context."""
    files: list[str] = []
    seen = set()
    for sym in bundle.get("affected_symbols") or []:
        f = sym.get("file") or sym.get("path")
        if f and f not in seen:
            seen.add(f)
            files.append(f)
    # Some bundles also list files directly in context_read.
    ctx = bundle.get("context_read") or {}
    for f in ctx.get("files_inspected") or []:
        if isinstance(f, str) and f not in seen:
            seen.add(f)
            files.append(f)
    # Allow explicit override key.
    for f in bundle.get("changed_files") or []:
        if f not in seen:
            seen.add(f)
            files.append(f)
    return files


def _git_changed_files(root: Path) -> list[str]:
    """Fallback: enumerate changed files from git working tree + index.

    Used when the bundle has no affected_symbols and no explicit changed_files.
    Real-world dogfood: many agent workflows skip the `pr-bundle add affected`
    step and only call `pr-bundle init` + `emit`. We don't want the verdict
    to silently bypass scope simply because the bundle didn't populate.
    """
    try:
        # Files modified vs HEAD (staged + unstaged).
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=5.0,
        )
        if result.returncode != 0:
            return []
        files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        # Plus untracked (new) files.
        result2 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=5.0,
        )
        if result2.returncode == 0:
            for line in result2.stdout.splitlines():
                line = line.strip()
                if line and line not in files:
                    files.append(line)
        return files
    except (subprocess.TimeoutExpired, OSError):
        return []


def _extract_risk(bundle: dict[str, Any]) -> dict[str, Any]:
    """Aggregate the bundle's risk records into the verdict-engine shape."""
    risks = bundle.get("risks") or []
    if not risks:
        return {"level": "low", "reasons": [], "paths": []}
    # Bundle risks have varying shapes — gather levels + paths defensively.
    levels = [r.get("severity") or r.get("level") for r in risks if isinstance(r, dict)]
    paths: list[str] = []
    reasons: list[str] = []
    for r in risks:
        if not isinstance(r, dict):
            continue
        for p in r.get("paths") or ([r.get("path")] if r.get("path") else []):
            if p:
                paths.append(p)
        desc = r.get("description") or r.get("reason") or r.get("kind")
        if desc:
            reasons.append(str(desc))
    chosen = "low"
    chosen_rank = severity_rank(chosen)
    for lvl in levels:
        if not isinstance(lvl, str) or lvl not in _V1_RISK_LEVELS:
            continue
        rank = severity_rank(lvl)
        if rank > chosen_rank:
            chosen = lvl
            chosen_rank = rank
    return {"level": chosen, "paths": list(dict.fromkeys(paths)), "reasons": list(dict.fromkeys(reasons))}


def _extract_executed_checks(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Map bundle's tests_run records into executed_checks shape."""
    out: list[dict[str, Any]] = []
    for t in bundle.get("tests_run") or []:
        if not isinstance(t, dict):
            continue
        out.append(
            {
                "command": t.get("command") or t.get("name") or t.get("test"),
                "status": t.get("status") or t.get("result") or "pass",
                "evidence": t.get("output") or t.get("evidence") or t.get("log"),
            }
        )
    return out


def _extract_findings(bundle: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Pull a findings list from bundle by key, tolerant of missing keys."""
    val = bundle.get(key)
    if isinstance(val, list):
        return [v for v in val if isinstance(v, dict)]
    return []


def _git_head_sha(root: Path) -> str | None:
    """Try `git rev-parse HEAD` — return None on any failure (non-git repo)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=3.0,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def compose_agent_change_proof_bundle(
    bundle: dict[str, Any],
    *,
    repo_root: Path,
    mode: str | None = None,
    policy_profile: str = "startup",
    rule_pack: RulePack | None = None,
) -> dict[str, Any]:
    """Compose the AgentChangeProofBundle v1 schema from a pr-bundle dict.

    Args:
      bundle: the parsed pr-bundle JSON (from .roam/pr-bundles/<branch>.json).
      repo_root: repository root for command_graph + git resolution.
      mode: optional override; defaults to bundle's mode field or "safe_edit".
      policy_profile: which policy floor applies. Default "startup".

    Returns:
      Dict matching v1 schema (top-level keys: schema, schema_version, repo,
      run, mode, policy_profile, changed_files, affected, risk,
      command_graph_snapshot, verification_contract, executed_checks,
      missing_checks, optimizer_findings, scope_findings, mcp_tool_findings,
      ledger, verdict).
    """
    mode = mode or bundle.get("mode") or "safe_edit"

    changed_files = _extract_changed_files(bundle)
    # Real-world dogfood fallback: if the bundle has no explicit files but
    # there ARE files changed in the working tree, fall back to git. Keeps
    # the verdict honest (a PR with changes can't be "0 changed files").
    if not changed_files:
        changed_files = _git_changed_files(repo_root)
    risk = _extract_risk(bundle)
    executed_checks = _extract_executed_checks(bundle)
    optimizer_findings = _extract_findings(bundle, "optimizer_findings")
    scope_findings = _extract_findings(bundle, "scope_findings")
    mcp_tool_findings = _extract_findings(bundle, "mcp_tool_findings")

    command_graph = build_command_graph(repo_root)
    contract = build_verification_contract(
        changed_files=changed_files,
        command_graph=command_graph,
        risk=risk,
        mode=mode,
        policy_profile=policy_profile,
        rule_pack=rule_pack,
    )

    # missing_checks = required ∩ {not in executed_checks}
    executed_names = {c["command"] for c in executed_checks if c.get("command")}
    missing_checks = [
        {"command": r["command"], "reason": "required_but_not_run", "detail": r.get("reason")}
        for r in contract["required"]
        if r["command"] not in executed_names
    ]

    ledger = bundle.get("ledger") or {}
    verdict = compute_verdict(
        verification_contract=contract,
        executed_checks=executed_checks,
        missing_checks=missing_checks,
        optimizer_findings=optimizer_findings,
        scope_findings=scope_findings,
        mcp_tool_findings=mcp_tool_findings,
        risk=risk,
        ledger=ledger,
    )

    return {
        "schema": PROOF_BUNDLE_SCHEMA,
        "schema_version": PROOF_BUNDLE_SCHEMA_VERSION,
        "repo": {
            "name": repo_root.name,
            "head_sha": _git_head_sha(repo_root),
            "fingerprint": bundle.get("fingerprint"),
        },
        "run": {
            "run_id": bundle.get("run_id"),
            "agent": (bundle.get("actor") or {}).get("agent_id") or bundle.get("agent"),
            "started": bundle.get("created_at"),
            "ended": bundle.get("updated_at"),
        },
        "mode": mode,
        "policy_profile": policy_profile,
        "changed_files": changed_files,
        "affected": {
            "areas": [],
            "symbols": [s.get("name") for s in bundle.get("affected_symbols") or [] if isinstance(s, dict)],
            "downstream": [],
        },
        "risk": risk,
        "command_graph_snapshot": command_graph,
        "verification_contract": contract,
        "executed_checks": executed_checks,
        "missing_checks": missing_checks,
        "optimizer_findings": optimizer_findings,
        "scope_findings": scope_findings,
        "mcp_tool_findings": mcp_tool_findings,
        "ledger": ledger,
        "verdict": verdict,
    }


def load_pr_bundle(path: Path) -> dict[str, Any]:
    """Load a pr-bundle JSON file (tolerant to None content)."""
    text = path.read_text()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"pr-bundle at {path} is not a JSON object")
    return data


# ---- rendering — moved to proof_bundle_render.py (Wave 15) ----
#
# Markdown + SARIF emission lives in `proof_bundle_render` so this module
# stays focused on construction + validation. Re-exported here so existing
# `from roam.proof_bundle import render_markdown, verdict_to_sarif` callers
# don't break.

from roam.proof_bundle_render import (  # noqa: E402, F401  (intentional re-export for external callers)
    _DIRECTORY_GROUPING_THRESHOLD,
    _VERDICT_ICONS,
    _format_reason_md,
    _md_checks_table,
    _md_files_block,
    _md_findings_blocks,
    _md_headline,
    _md_provenance_footer,
    _md_reasons,
    _md_risk_block,
    render_markdown,
    verdict_to_sarif,
)

__all__ = [
    "compose_agent_change_proof_bundle",
    "load_pr_bundle",
    "get_v1_schema",
    "validate_v1",
    "render_markdown",
    "verdict_to_sarif",
]
