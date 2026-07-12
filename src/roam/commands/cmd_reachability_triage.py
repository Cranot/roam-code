"""``roam reachability-triage`` — zero-egress scanner reachability facts.

This is a thin, versioned wrapper around the existing
``service-report --type reachability-triage`` compose. The compose remains the
single source of truth for invoking ``sbom``, ``supply-chain``, ``vulns``,
``vuln-reach``, ``taint``, and ``secrets``; this command only projects its
results into deterministic reachability facts and an optional baseline gate.

Honesty contract: Non-reachable does not mean safe. This command is a
reachability filter over your scanner output, not a taint-analysis replacement.
Its output maps to / supports evidence for review by a security team.

Output formats: text (default), ``--json``. SARIF is deliberately NOT emitted
because reachability-triage projects reachability FACTS + an optional baseline
gate over other scanners' findings — the per-location violations already live
in those upstream tools' own SARIF; this wrapper adds a reachability filter,
not a new violation stream.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import click

from roam.atomic_io import atomic_write_json
from roam.capability import roam_capability
from roam.commands.changed_files import get_changed_files
from roam.commands.cmd_service_report import _GATHER, _is_safe_commit_range
from roam.commands.cmd_vulns import _vuln_finding_id
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root
from roam.exit_codes import EXIT_GATE_FAILURE
from roam.output.formatter import json_envelope, to_json

REACHABILITY_TRIAGE_WRAPPER_VERSION = "1.0.0"
REACHABILITY_TRIAGE_BASELINE_REL = (".roam", "reachability-triage-baseline.json")
REACHABILITY_TRIAGE_PRIMITIVES = (
    "sbom",
    "supply-chain",
    "vulns",
    "vuln-reach",
    "taint",
    "secrets",
)

_HONESTY_LINES = (
    "Non-reachable does not mean safe.",
    "This is a reachability filter over your scanner output, not a taint-analysis replacement.",
    "This output maps to / supports evidence for review by a security team.",
)


def _summary_value(envelope: dict, key: str, default=0):
    """Read a metric from an envelope summary, then its top level."""
    if not isinstance(envelope, dict):
        return default
    summary = envelope.get("summary")
    if isinstance(summary, dict) and key in summary:
        return summary[key]
    return envelope.get(key, default)


def _compose_metrics(env: dict) -> dict:
    """Project stable figures from the delegated service-report compose."""
    sbom = env.get("sbom", {})
    supply_chain = env.get("supply_chain", {})
    vulns = env.get("vulns", {})
    vuln_reach = env.get("vuln_reach", {})
    taint = env.get("taint", {})
    secrets = env.get("secrets", {})
    return {
        "dependencies": {
            "total": _summary_value(sbom, "total_dependencies"),
            "reachable": _summary_value(sbom, "reachable_count"),
            "reachable_direct": _summary_value(sbom, "reachable_direct_count"),
            "reachable_heuristic": _summary_value(sbom, "reachable_heuristic_count"),
            "phantom": _summary_value(sbom, "phantom_count"),
        },
        "supply_chain": {
            "total_dependencies": _summary_value(supply_chain, "total_dependencies"),
            "risk_score": _summary_value(supply_chain, "risk_score"),
            "unpinned": _summary_value(supply_chain, "unpinned_count"),
        },
        "vulnerabilities": {
            "total": _summary_value(vuln_reach, "total_vulns", _summary_value(vulns, "total")),
            "reachable": _summary_value(vuln_reach, "reachable_count"),
            "critical_reachable": _summary_value(vuln_reach, "critical_count"),
        },
        "taint": {"flows": _summary_value(taint, "findings")},
        "secrets": {"findings": _summary_value(secrets, "total_findings")},
    }


def _unwrap_finding(record: object) -> dict:
    if not isinstance(record, dict):
        return {}
    value = record.get("value")
    return value if isinstance(value, dict) else record


def _normalise_file(value: object) -> str:
    path = str(value or "").replace("\\", "/")
    return path[2:] if path.startswith("./") else path


def _vuln_files_by_identity(env: dict) -> dict[tuple[str, str], set[str]]:
    """Map vuln-reach identities to files disclosed by ``roam vulns``."""
    rows = env.get("vulns", {}).get("vulnerabilities", [])
    mapped: dict[tuple[str, str], set[str]] = {}
    for raw in rows if isinstance(rows, list) else []:
        row = _unwrap_finding(raw)
        key = (str(row.get("cve_id") or row.get("cve") or ""), str(row.get("package_name") or row.get("package") or ""))
        matched_file = _normalise_file(row.get("matched_file"))
        if matched_file:
            mapped.setdefault(key, set()).add(matched_file)
    return mapped


def _project_vulnerability_flows(env: dict, changed_files: set[str] | None = None) -> list[dict]:
    """Project deterministic vulnerability reachability facts.

    IDs reuse :func:`cmd_vulns._vuln_finding_id`, whose identity is the
    scanner's ``(cve_id, package_name)`` pair. Hop distance and blast radius
    come directly from the delegated ``vuln-reach`` result.
    """
    records = env.get("vuln_reach", {}).get("vulnerabilities", [])
    files_by_identity = _vuln_files_by_identity(env)
    flows: list[dict] = []
    for raw in records if isinstance(records, list) else []:
        row = _unwrap_finding(raw)
        cve = str(row.get("cve") or row.get("cve_id") or "")
        package = str(row.get("package") or row.get("package_name") or "")
        files = set(files_by_identity.get((cve, package), set()))
        path = row.get("path")
        if isinstance(path, list):
            for item in path:
                if isinstance(item, dict):
                    file_path = _normalise_file(item.get("file"))
                    if file_path:
                        files.add(file_path)
        if changed_files is not None and not files.intersection(changed_files):
            continue
        reachable = bool(row.get("reachable"))
        flows.append(
            {
                "finding_id": _vuln_finding_id(cve, package),
                "cve": cve or None,
                "package": package or None,
                "reachability": "reachable" if reachable else "not-reachable",
                "hop_distance": int(row.get("hops") or row.get("hop_count") or 0),
                "blast_radius": int(row.get("blast_radius") or 0),
                "files": sorted(files),
            }
        )
    return sorted(flows, key=lambda flow: flow["finding_id"])


def _facts_for_flows(flows: list[dict]) -> list[str]:
    if not flows:
        return ["0 reachable vulnerability paths"]
    return [
        (
            f"{flow['finding_id']} maps to {flow['reachability']} at "
            f"{flow['hop_distance']} hops with blast radius {flow['blast_radius']} symbols"
        )
        for flow in flows
    ]


def _baseline_path(root: Path) -> Path:
    return root.joinpath(*REACHABILITY_TRIAGE_BASELINE_REL)


def _write_baseline(path: Path, reachable_ids: set[str]) -> None:
    atomic_write_json(
        path,
        {
            "schema": "roam-reachability-triage-baseline-v1",
            "wrapper_version": REACHABILITY_TRIAGE_WRAPPER_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reachable_flow_finding_ids": sorted(reachable_ids),
        },
        sort_keys=True,
    )


def _load_baseline(path: Path) -> tuple[set[str] | None, str]:
    """Load baseline IDs, returning a fail-open state on absent/bad input."""
    if not path.exists():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, "unreadable"
    if isinstance(data, list):
        values = data
    elif isinstance(data, dict):
        values = data.get("reachable_flow_finding_ids")
    else:
        values = None
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return None, "unreadable"
    return set(values), "loaded"


def _text_output(
    *,
    observation: str,
    metrics: dict,
    facts: list[str],
    commit_range: str | None,
    gate: dict,
) -> str:
    lines = [f"REACHABILITY: {observation}"]
    if commit_range:
        lines.append(f"Scope: {commit_range}")
    lines.extend(f"- {fact}" for fact in facts)
    lines.extend(
        [
            "",
            (
                f"Figures: {metrics['dependencies']['reachable']}/{metrics['dependencies']['total']} "
                f"dependencies reachable; {metrics['taint']['flows']} taint flows; "
                f"{metrics['secrets']['findings']} secret findings."
            ),
        ]
    )
    if gate["requested"]:
        if gate["evaluated"]:
            lines.append(f"Baseline gate: {len(gate['new_reachable_finding_ids'])} new reachable paths.")
        elif gate.get("baseline_error"):
            lines.append(
                f"Baseline gate: FAIL-CLOSED -- baseline at {gate['baseline_path']} is present but unreadable "
                "(corrupt/tampered); refusing to pass silently. Re-run --write-baseline to re-establish it."
            )
        else:
            lines.append(f"Baseline gate: fail-open ({gate['baseline_state']} baseline).")
    if gate["baseline_state"] == "written":
        lines.append(f"Baseline: wrote {gate['baseline_path']}.")
    lines.extend(["", "Honesty:", *[f"- {line}" for line in _HONESTY_LINES]])
    return "\n".join(lines)


@roam_capability(
    category="review",
    summary="Filter scanner findings through local call-graph reachability.",
    inputs=["commit_range", "baseline"],
    outputs=["reachability_facts", "scanner_metrics", "baseline_diff"],
    examples=[
        "roam reachability-triage",
        "roam reachability-triage --range main..HEAD --json",
        "roam reachability-triage --gate-on-new-reachable",
    ],
    tags=["security", "reachability", "zero-egress", "review"],
    ai_safe=True,
    mcp_expose=True,
    requires_index=True,
    since="13.8",
)
@click.command(name="reachability-triage")
@click.option(
    "--range",
    "commit_range",
    default=None,
    help="Limit vulnerability-flow facts to files changed in a git range such as main..HEAD.",
)
@click.option(
    "--gate-on-new-reachable",
    is_flag=True,
    help="Exit 5 only for reachable-flow finding IDs absent from the .roam baseline; missing baselines fail open.",
)
@click.option(
    "--write-baseline",
    "--baseline-write",
    "write_baseline",
    is_flag=True,
    help="Persist the current reachable-flow finding IDs to .roam/reachability-triage-baseline.json.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit the structured JSON envelope.")
@click.pass_context
def reachability_triage_cmd(
    ctx,
    commit_range: str | None,
    gate_on_new_reachable: bool,
    write_baseline: bool,
    json_output: bool,
) -> None:
    """Emit zero-egress vulnerability reachability facts.

    Delegates to the versioned ``service-report --type
    reachability-triage`` compose. Non-reachable does not mean safe. This is a
    reachability filter over your scanner output, not a taint-analysis
    replacement. The output maps to / supports evidence for security review.
    """
    json_mode = json_output or bool(ctx.obj.get("json") if ctx.obj else False)
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    if commit_range and not _is_safe_commit_range(commit_range):
        raise click.UsageError(
            f"--range value must not start with '-' (got {commit_range!r}); use a git revspec like 'main..HEAD'."
        )

    ensure_index()
    root = find_project_root()
    changed_files = (
        {_normalise_file(path) for path in get_changed_files(root, commit_range=commit_range)} if commit_range else None
    )

    # Do not duplicate or partially reconstruct the six-command compose.
    # Invariant (single reachability source): reachability-triage is a PASS-THROUGH
    # re-projection of `roam vuln-reach`'s output (the compose invokes it
    # in-process) plus a temporal baseline diff + file-scope filter. There is
    # exactly ONE reachability computation in the product. NEVER recompute
    # reachability / hop_distance / blast_radius from the DB here -- a second
    # matcher would let the two commands silently diverge on identical state.
    env = _GATHER["reachability-triage"](commit_range or "")
    metrics = _compose_metrics(env)
    flows = _project_vulnerability_flows(env, changed_files)
    facts = _facts_for_flows(flows)
    reachable_ids = {flow["finding_id"] for flow in flows if flow["reachability"] == "reachable"}

    baseline_path = _baseline_path(root)
    baseline_state = "not-requested"
    baseline_ids: set[str] | None = None
    if write_baseline:
        _write_baseline(baseline_path, reachable_ids)
        baseline_ids = set(reachable_ids)
        baseline_state = "written"
    elif gate_on_new_reachable:
        baseline_ids, baseline_state = _load_baseline(baseline_path)

    gate_evaluated = gate_on_new_reachable and baseline_ids is not None
    # A present-but-corrupt/tampered baseline must NOT silently disarm the gate.
    # `_load_baseline` returns None (fail-open) for BOTH "missing" and
    # "unreadable", which previously collapsed a corrupt baseline into the same
    # silent pass as a legitimately-absent one -- a security gate that can be
    # disarmed by truncating one JSON file. We now split the two: "missing"
    # still fails open (legitimate first run / bootstrap), but "unreadable"
    # (the tamper/corruption case) fails CLOSED so it cannot pass unnoticed.
    baseline_unreadable = gate_on_new_reachable and not write_baseline and baseline_state == "unreadable"
    new_reachable_ids = sorted(reachable_ids - baseline_ids) if gate_evaluated else []
    gate = {
        "requested": gate_on_new_reachable,
        "evaluated": gate_evaluated,
        "baseline_state": baseline_state,
        "baseline_error": baseline_unreadable,
        "baseline_path": str(baseline_path),
        "new_reachable_finding_ids": new_reachable_ids,
    }

    reachable_count = sum(flow["reachability"] == "reachable" for flow in flows)
    not_reachable_count = len(flows) - reachable_count
    observation = f"{reachable_count} reachable paths; {not_reachable_count} not-reachable paths"
    missing_primitives = [
        primitive
        for primitive, key in zip(
            REACHABILITY_TRIAGE_PRIMITIVES,
            ("sbom", "supply_chain", "vulns", "vuln_reach", "taint", "secrets"),
        )
        if not env.get(key)
    ]

    if json_mode:
        envelope = json_envelope(
            "reachability-triage",
            summary={
                "verdict": observation,
                "reachable_paths": reachable_count,
                "not_reachable_paths": not_reachable_count,
                "commit_range": commit_range,
                "changed_files": len(changed_files) if changed_files is not None else None,
                "new_reachable_paths": len(new_reachable_ids),
                "gate_evaluated": gate_evaluated,
                "partial_success": bool(missing_primitives),
            },
            agent_contract={"facts": facts, "risks": list(_HONESTY_LINES), "next_commands": []},
            wrapper_version=REACHABILITY_TRIAGE_WRAPPER_VERSION,
            delegated_compose="service-report:reachability-triage",
            primitives=list(REACHABILITY_TRIAGE_PRIMITIVES),
            metrics=metrics,
            flows=flows,
            gate=gate,
            missing_primitives=missing_primitives,
            honesty=list(_HONESTY_LINES),
            budget=token_budget,
        )
        click.echo(to_json(envelope))
    else:
        click.echo(
            _text_output(
                observation=observation,
                metrics=metrics,
                facts=facts,
                commit_range=commit_range,
                gate=gate,
            )
        )

    if (gate_evaluated and new_reachable_ids) or baseline_unreadable:
        # Fail the gate on EITHER a new reachable flow (the normal block) OR a
        # present-but-corrupt baseline (the tamper case that used to pass open).
        ctx.exit(EXIT_GATE_FAILURE)
