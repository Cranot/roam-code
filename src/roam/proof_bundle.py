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
from roam.proof_input import parse_proof_json, validate_proof_input
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


def _git_changed_files_with_provenance(root: Path) -> tuple[list[str], str | None]:
    """Enumerate changed files from git, and say when git could not answer.

    Returns ``(files, unanalyzable_reason)``. ``unanalyzable_reason`` is
    ``None`` only when git ACTUALLY ANSWERED; then an empty list means "this
    tree has no changes", which is a measurement.

    The second element exists because the two states used to be one. The
    fallback returned ``[]`` for "git says nothing changed" AND for "git
    refused to speak", and every caller downstream read the empty list as the
    former. In a worktree whose ``.git`` cannot be read, that produced an
    empty verification contract, which made "all required checks passed"
    vacuously true, which printed a green verdict and exited 0 over a repo
    the process had not been able to open. An absent measurement is UNKNOWN,
    never a benign CLEAN.
    """
    files: list[str] = []
    try:
        # Files modified vs HEAD (staged + unstaged).
        result = subprocess.run(
            ["git", "diff", "--name-only", "-z", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            cwd=str(root),
            timeout=5.0,
        )
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            return [], (
                f"git could not enumerate the change set (`git diff --name-only HEAD` "
                f"exited {result.returncode}" + (f": {detail[0]}" if detail else "") + ")"
            )
        # Git's line-delimited output quotes non-ASCII names and cannot
        # represent embedded newlines. NUL records preserve the actual path,
        # including meaningful leading/trailing whitespace.
        files = list(dict.fromkeys(path for path in result.stdout.split("\0") if path))
        # Plus untracked (new) files.
        result2 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            cwd=str(root),
            timeout=5.0,
        )
        if result2.returncode != 0:
            return files, f"git could not enumerate untracked files (`git ls-files` exited {result2.returncode})"
        files = list(dict.fromkeys([*files, *(path for path in result2.stdout.split("\0") if path)]))
        return files, None
    except subprocess.TimeoutExpired:
        return files, "git did not answer within 5s while enumerating the change set"
    except OSError as e:
        return files, f"git could not be run to enumerate the change set: {e}"


def _git_changed_files(root: Path) -> list[str]:
    """Back-compat shim: the file list only, with the provenance discarded.

    Callers that need to tell "no changes" from "could not ask" must use
    :func:`_git_changed_files_with_provenance` instead.
    """
    return _git_changed_files_with_provenance(root)[0]


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
                # W1441 — a record with no status field is a bare claim,
                # not evidence. It used to default to "pass", which let a
                # bundle read green without any recorded outcome
                # (fail-open). "unverified" never satisfies a required
                # check; see verdict._collect_blockers_that_invalidate_proof.
                "status": t.get("status") or t.get("result") or "unverified",
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
    # Reject malformed evidence before collection or normalization can erase it.
    validate_proof_input(bundle)
    mode = mode or bundle.get("mode") or "safe_edit"

    changed_files = _extract_changed_files(bundle)
    # Real-world dogfood fallback: if the bundle has no explicit files but
    # there ARE files changed in the working tree, fall back to git. Keeps
    # the verdict honest (a PR with changes can't be "0 changed files").
    #
    # When the bundle declared nothing AND git could not answer either, the
    # change set is UNKNOWN rather than empty, and every downstream artefact
    # -- the verification contract, `missing_checks`, the verdict -- is being
    # computed over a set nobody measured. That is a hard gate, not a pass.
    change_set_unanalyzable: str | None = None
    if not changed_files:
        changed_files, change_set_unanalyzable = _git_changed_files_with_provenance(repo_root)
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
    # W1443 — the review gate keys on what the WORK declared, not on what
    # this call chose to pass: an orchestration_contract recorded in the
    # bundle (put there by the compile envelope) means 1b/4b obligations
    # apply, so missing review evidence blocks rather than passing quietly.
    orchestration_contract = bundle.get("orchestration_contract")
    orchestration_contract = orchestration_contract if isinstance(orchestration_contract, dict) else None
    review_evidence = bundle.get("review_evidence")
    review_evidence = review_evidence if isinstance(review_evidence, dict) else None
    verdict = compute_verdict(
        verification_contract=contract,
        executed_checks=executed_checks,
        missing_checks=missing_checks,
        optimizer_findings=optimizer_findings,
        scope_findings=scope_findings,
        mcp_tool_findings=mcp_tool_findings,
        risk=risk,
        ledger=ledger,
        review_evidence=review_evidence,
        orchestration_contract=orchestration_contract,
        change_set_unanalyzable=change_set_unanalyzable,
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
        # The PROVENANCE of `changed_files`, persisted so a later reader can
        # tell "git answered, nothing changed" from "git could not answer".
        # Without it, `roam verdict` reading this file back had no way to
        # reach the unanalyzable-change-set blocker at all -- it defaulted to
        # None and the gate was unreachable from the CI-facing entry point.
        "change_set_unanalyzable": change_set_unanalyzable,
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
        # Persist the exact inputs used above. Otherwise `roam verdict`
        # reading this artifact silently loses review blockers and warnings.
        # None (legacy no-review path) must remain distinct from {} (opt-in).
        "review_evidence": review_evidence,
        "orchestration_contract": orchestration_contract,
        "verdict": verdict,
    }


def load_pr_bundle(path: Path) -> dict[str, Any]:
    """Load an unambiguous UTF-8 proof input before collection or composition."""
    return parse_proof_json(path.read_text(encoding="utf-8"))


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
