"""``roam service-report`` — one-command service-engagement deliverables.

Turns Roam's four services-report *templates* into filled, buyer-facing
deliverables, exactly mirroring how ``roam pr-replay`` productises
``roam postmortem``. Each ``--type`` runs the right existing Roam
primitives against the repo, aggregates their JSON envelopes, and emits
a narrative Markdown report ready to hand to a client.

Four report types, all share the same engine:

* ``--type due-diligence`` — codebase-health / M&A technical diligence.
  Runs ``health``, ``bus-factor``, ``complexity``, ``dead``, ``clones``,
  ``smells``, ``test-pyramid``, ``sbom``, ``supply-chain``, ``vulns``,
  ``architecture-drift``.
* ``--type ai-readiness`` — AI adoption readiness. Runs ``ai-readiness``,
  ``ai-ratio``, ``agent-score``, ``mode``.
* ``--type reachability-triage`` — the security wedge: reachable-vs-noise.
  Runs ``sbom``, ``supply-chain``, ``vulns``, ``vuln-reach``, ``taint``,
  ``secrets``.
* ``--type post-incident`` — replay a commit/incident range with
  ``postmortem`` + ``audit-trail-verify`` audit-trail framing.

Usage::

    # Codebase due-diligence report to stdout
    roam service-report --type due-diligence

    # Client-branded reachability triage written to a file + PDF
    roam service-report --type reachability-triage --client "Acme Inc" \
        --output acme-triage.md --pdf acme-triage.pdf

    # Post-incident replay over an explicit incident window
    roam service-report --type post-incident --range v1.0..main --output incident.md

Output formats: Markdown by default; ``roam --json service-report``
returns the full envelope (summary + sections + report_markdown).
SARIF is deliberately NOT emitted — service-report outputs are
invocation-scoped buyer-facing report envelopes composed from the
individual commands' aggregations, not per-location violations. The composed
subcommands emit their own ``--sarif`` when applicable; this command
rolls them up into a narrative report (same rationale as
``cmd_pr_replay``).

Reuses ``cmd_pr_replay``'s render/output/PDF/ledger infrastructure where
it makes sense (``_render_pdf``, ``_git_head_sha``, ``_is_safe_commit_range``,
``_run_postmortem``) — the two commands are siblings in the paid-audit
family.

Wording discipline (W184 / W203): every report says "maps to / supports
evidence for" and never "certifies / guaranteed / compliant" (the
disclaimer "does not certify" is the one allowed negation). See
``tests/_helpers/wording_lint.py``.
"""

from __future__ import annotations

import json as _json
import os as _os
import subprocess as _subprocess
import sys as _sys
import time as _time
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import click

from roam.capability import roam_capability

# Reuse pr-replay's render/output infrastructure — genuine sibling reuse,
# not duplication. ``_render_pdf`` is a generic markdown→PDF renderer;
# ``_git_head_sha`` / ``_is_safe_commit_range`` / ``_run_postmortem`` are
# the same helpers the paid-audit family already relies on.
from roam.commands.cmd_pr_replay import (
    _git_head_sha,
    _is_safe_commit_range,
    _render_pdf,
    _run_postmortem,
)
from roam.commands.resolve import ensure_index
from roam.exit_codes import EXIT_SUCCESS
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# Report-type registry — single source of truth for what each type means.
# ---------------------------------------------------------------------------

_REPORT_TYPES: dict[str, dict] = {
    "due-diligence": {
        "label": "Codebase Due Diligence",
        "title": "Codebase Due Diligence Report",
        "purpose_line": (
            "Technical due-diligence pass over the target codebase: health, "
            "key-person risk, complexity, dead code, duplication, test signal, "
            "architecture drift, and security / supply-chain posture — the "
            "engineering evidence an acquirer or investor needs before signing."
        ),
        "engagement_price": "$3,000–$7,500",
        "lead_commands": [
            "health",
            "bus-factor",
            "complexity",
            "dead",
            "clones",
            "smells",
            "test-pyramid",
            "sbom",
            "supply-chain",
            "vulns",
            "architecture-drift",
        ],
    },
    "ai-readiness": {
        "label": "AI Adoption Readiness Audit",
        "title": "AI Adoption Readiness Audit",
        "purpose_line": (
            "Pre-rollout readiness review: how ready is this codebase for "
            "agent-driven and AI-assisted development? Scores structural "
            "readiness dimensions, measures the existing AI footprint, and "
            "reports the governance gates that should be in place before "
            "agents touch production code."
        ),
        "engagement_price": "$1,500–$4,000",
        "lead_commands": ["ai-readiness", "ai-ratio", "agent-score", "mode"],
    },
    "reachability-triage": {
        "label": "Security Reachability Triage",
        "title": "Security Reachability Triage",
        "purpose_line": (
            "Scanner-noise reduction sweep: of everything the scanners flag, "
            "what is actually reachable from a production entry point? "
            "Reachability analysis against the call graph separates the "
            "findings that warrant fix work this sprint from the noise."
        ),
        "engagement_price": "$2,500–$6,000",
        "lead_commands": [
            "sbom",
            "supply-chain",
            "vulns",
            "vuln-reach",
            "taint",
            "secrets",
        ],
    },
    "post-incident": {
        "label": "Post-Incident Replay",
        "title": "Post-Incident Replay Report",
        "purpose_line": (
            "Replay a suspected incident window with the current detector set "
            "and the signed audit trail: which findings would have surfaced "
            "pre-merge, and does the change history verify end-to-end? Turns a "
            "postmortem into a durable prevention artifact."
        ),
        "engagement_price": "$1,500–$4,000",
        "lead_commands": ["postmortem", "audit-trail-verify"],
    },
}


# ---------------------------------------------------------------------------
# Primitive invocation — run ``roam --json <cmd>`` in an isolated child,
# return the parsed envelope. Commands such as ``clones`` create their own
# process pools; invoking them through Click's in-process ``CliRunner`` can
# deadlock at the multiprocessing spawn boundary (observed on Windows) and
# also retains command-global caches across an 11-component report. Literal
# subprocess argv keeps every component independent and lets the parent bound
# time, output, and process-tree cleanup.
# ---------------------------------------------------------------------------


_COMPONENT_TIMEOUT_SECONDS = 180
_COMPONENT_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_COMPONENT_MAX_WORKERS = 3
_DUE_DILIGENCE_BUDGET_SECONDS = 240
_DEADLINE_CLEANUP_RESERVE_SECONDS = 15


def _component_failure(command: str, state: str, detail: str) -> dict:
    """Return a structured absent-component envelope without raw payloads."""
    return {
        "command": command,
        "status": "hard_failure",
        "isError": True,
        "summary": {
            "verdict": f"{command} evidence unavailable: {detail}",
            "state": state,
            "partial_success": True,
        },
        "error_code": "COMMAND_FAILED",
        "error": detail,
    }


def _strict_json_object_pairs(pairs):
    """Reject ambiguous duplicate keys in a component envelope."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _terminate_component_process_tree(proc: _subprocess.Popen) -> bool:
    """Terminate a timed-out component and every worker it spawned."""
    from roam.sibling_patch.replay_gate import _terminate_process_tree

    return _terminate_process_tree(proc)


def _component_popen_kwargs() -> dict:
    """Return cross-platform process-group isolation for component commands."""
    if _os.name == "nt":
        return {
            "creationflags": (
                getattr(_subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                | getattr(_subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
        }
    return {"start_new_session": True}


def _run_roam_json(args: list[str], *, deadline: float | None = None) -> dict:
    """Invoke ``roam --json <args>`` in an isolated, bounded child process.

    Never raises for an expected component failure. Progress / auto-index
    chrome before the JSON payload is tolerated by locating the first ``{``;
    duplicate keys, empty/malformed output, oversized output, launch errors,
    and timeouts become explicit failure envelopes. A valid non-zero command
    envelope is preserved because gate exits can carry useful report evidence.
    """
    command = args[0] if args else "component"
    timeout_seconds: float = _COMPONENT_TIMEOUT_SECONDS
    deadline_limited = False
    if deadline is not None:
        remaining = deadline - _time.monotonic() - _DEADLINE_CLEANUP_RESERVE_SECONDS
        if remaining <= 0:
            return _component_failure(
                command,
                "report_deadline_exhausted",
                "report time budget exhausted before component launch",
            )
        timeout_seconds = min(timeout_seconds, remaining)
        deadline_limited = timeout_seconds < _COMPONENT_TIMEOUT_SECONDS
    argv = [_os.path.realpath(_sys.executable), "-m", "roam", "--json", *args]
    child_env = dict(_os.environ)
    child_env["PYTHONUTF8"] = "1"
    try:
        proc = _subprocess.Popen(
            argv,
            cwd=str(Path.cwd()),
            env=child_env,
            shell=False,
            stdin=_subprocess.DEVNULL,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            close_fds=True,
            **_component_popen_kwargs(),
        )
    except OSError as exc:
        return _component_failure(command, "component_unavailable", f"runtime launch failed ({type(exc).__name__})")

    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except _subprocess.TimeoutExpired:
        tree_terminated = _terminate_component_process_tree(proc)
        try:
            proc.communicate(timeout=5)
        except (OSError, _subprocess.TimeoutExpired):
            pass
        cleanup = "process tree terminated" if tree_terminated else "process-tree cleanup incomplete"
        state = "report_deadline_exhausted" if deadline_limited else "component_timeout"
        detail = (
            "report time budget exhausted" if deadline_limited else f"timed out after {_COMPONENT_TIMEOUT_SECONDS}s"
        )
        return _component_failure(command, state, f"{detail}; {cleanup}")

    if len(stdout) + len(stderr) > _COMPONENT_MAX_OUTPUT_BYTES:
        return _component_failure(
            command,
            "component_output_oversized",
            f"output exceeded {_COMPONENT_MAX_OUTPUT_BYTES} bytes",
        )
    text = stdout.decode("utf-8", "replace")
    brace = text.find("{")
    if brace < 0:
        return _component_failure(command, "component_empty_output", "command emitted no JSON envelope")
    try:
        parsed = _json.loads(text[brace:], object_pairs_hook=_strict_json_object_pairs)
    except (_json.JSONDecodeError, ValueError):
        return _component_failure(command, "component_malformed_output", "command emitted invalid JSON")
    if not isinstance(parsed, dict):
        return _component_failure(command, "component_malformed_output", "command emitted a non-object envelope")
    if proc.returncode:
        meta = parsed.get("_meta") if isinstance(parsed.get("_meta"), dict) else {}
        parsed["_meta"] = {**meta, "service_report_component_exit_code": proc.returncode}
    return parsed


def _summary(env: dict) -> dict:
    """Return the ``summary`` sub-dict of an envelope (or ``{}``)."""
    s = env.get("summary") if isinstance(env, dict) else None
    return s if isinstance(s, dict) else {}


def _verdict(env: dict) -> str:
    """Return an envelope's one-line ``summary.verdict`` (or a placeholder)."""
    return str(_summary(env).get("verdict") or "not available")


def _g(env: dict, key: str, default=None):
    """Safe ``summary[key]`` lookup with a default."""
    return _summary(env).get(key, default)


def _cell(value) -> str:
    """Render a scalar for a Markdown table cell (escape the pipe)."""
    if value is None:
        return "—"
    return str(value).replace("|", "/")


def _pct(part, whole) -> str:
    """Format ``part/whole`` as an integer percentage string, guarding /0."""
    try:
        part = float(part)
        whole = float(whole)
    except (TypeError, ValueError):
        return "—"
    if whole <= 0:
        return "—"
    return f"{part * 100 / whole:.0f}%"


# ---------------------------------------------------------------------------
# Shared report chrome — header, disclaimer banner, "not covered", footer.
# Every renderer reuses these so the banner and wording discipline stay
# single-sourced (W184 / W203 clean).
# ---------------------------------------------------------------------------

# The disclaimer banner. "does not certify" is the one allowed negation
# (the wording lint permits a forbidden stem inside a negation window).
_DISCLAIMER_BANNER = (
    "> **Engineering evidence, not an attestation.** This report maps to / "
    "supports evidence for the engineering review below. It does not certify "
    "compliance, replace a professional audit, and its findings depend on "
    "call-graph quality and the declared entry-point inventory. Numbers are "
    "generated from the repository at the index SHA above; review with the "
    "relevant team before acting on them."
)


def _header(
    *,
    type_meta: dict,
    report_type: str,
    client: str | None,
    index_sha: str | None,
    generated_at: str,
    subject: str,
    component_failures: tuple[str, ...] = (),
    component_degraded: tuple[str, ...] = (),
) -> list[str]:
    """Build the shared report header block."""
    out: list[str] = []
    if client:
        out.append(f"# {type_meta['title']} — {client}")
    else:
        out.append(f"# {type_meta['title']}")
    out.append("")
    meta_bits = [
        f"**Type:** {type_meta['label']}",
        f"**Subject:** `{subject}`",
        f"**Index SHA:** `{index_sha or 'unknown'}`",
        f"**Generated:** {generated_at}",
    ]
    out.append(" · ".join(meta_bits) + "  ")
    out.append(f"**Tool:** `roam service-report --type {report_type}`")
    out.append("")
    out.append(_DISCLAIMER_BANNER)
    out.append("")
    if component_failures:
        out.append(
            "> **Partial report:** required evidence is unavailable for "
            + ", ".join(f"`{name}`" for name in component_failures)
            + ". Treat affected conclusions as unresolved."
        )
        out.append("")
    elif component_degraded:
        out.append(
            "> **Degraded evidence:** partial results were reported by "
            + ", ".join(f"`{name}`" for name in component_degraded)
            + ". Review those component envelopes before acting."
        )
        out.append("")
    out.append(type_meta["purpose_line"])
    out.append("")
    return out


def _paid_framing(*, type_meta: dict, client: str | None) -> list[str]:
    """Paid-engagement framing block (mirrors pr-replay's tier framing)."""
    out: list[str] = []
    out.append("## About this engagement")
    out.append("")
    who = client or "your team"
    out.append(
        f"This is a **{type_meta['label']}** deliverable prepared for {who}. "
        f"A full paid engagement ({type_meta['engagement_price']}) includes "
        f"founder review of the findings on a call, a written remediation plan, "
        f"and the raw JSON envelopes for every command run. See "
        f"<https://roam-code.com/docs/> or contact services."
    )
    out.append("")
    return out


def _footer(*, report_type: str, generated_at: str, extra_scope: list[str]) -> list[str]:
    """Shared 'what this does not cover' + disclaimer + methodology footer."""
    out: list[str] = []
    out.append("## What this report does not cover")
    out.append("")
    base_scope = [
        "**Semantic correctness** — whether the code does the right thing. "
        "Roam surfaces structural and evidence signals; it does not replace "
        "human or LLM semantic review.",
        "**Legal, financial, or valuation opinion.** This is engineering evidence only.",
    ]
    for item in extra_scope + base_scope:
        out.append(f"- {item}")
    out.append("")
    out.append("## Disclaimer")
    out.append("")
    out.append(
        "Findings are generated by the open-source Roam CLI against the "
        "repository at the index SHA in the header. Reachability and risk "
        "depend on call-graph quality and the declared entry-point inventory; "
        "static analysis can miss dynamically-constructed paths. This report "
        "maps to / supports evidence for an engineering review — it does not "
        "certify compliance and is not a substitute for a professional audit."
    )
    out.append("")
    out.append(
        f"_Generated by `roam service-report --type {report_type}` on "
        f"{generated_at}. Engine: the open-source Roam CLI "
        f"([github.com/Cranot/roam-code](https://github.com/Cranot/roam-code))._"
    )
    out.append("")
    return out


# ---------------------------------------------------------------------------
# Type: due-diligence
# ---------------------------------------------------------------------------


def _gather_components(
    components: tuple[tuple[str, list[str]], ...],
    *,
    max_workers: int = _COMPONENT_MAX_WORKERS,
    deadline: float | None = None,
) -> dict:
    """Run independent read-side components concurrently, preserving order."""
    if not components:
        return {}
    worker_count = min(max(1, max_workers), len(components))
    if worker_count <= 1:
        return {key: _run_roam_json(args, deadline=deadline) for key, args in components}

    results: dict[str, dict] = {}
    with _ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="roam-service-report") as pool:
        jobs = [(key, pool.submit(_run_roam_json, args, deadline=deadline)) for key, args in components]
        for key, future in jobs:
            try:
                results[key] = future.result()
            except Exception as exc:  # noqa: BLE001 — one component must not erase the report
                results[key] = _component_failure(
                    key.replace("_", "-"),
                    "component_internal_failure",
                    f"component orchestration failed ({type(exc).__name__})",
                )
    return results


_DUE_DILIGENCE_COMPONENTS: tuple[tuple[str, list[str]], ...] = (
    ("health", ["health"]),
    ("bus_factor", ["bus-factor"]),
    ("complexity", ["complexity"]),
    ("dead", ["dead"]),
    ("clones", ["clones"]),
    ("smells", ["smells"]),
    ("test_pyramid", ["test-pyramid"]),
    ("sbom", ["sbom"]),
    ("supply_chain", ["supply-chain"]),
    ("vulns", ["vulns"]),
    ("arch_drift", ["architecture-drift"]),
)


def _gather_due_diligence() -> dict:
    """Run the due-diligence primitives, return {command: envelope}."""
    # Cost-aware scheduling matters more than theoretical parallelism here.
    # ``clones`` owns a ProcessPoolExecutor and must run exclusively; placing
    # it beside the source scanners oversubscribes CPUs and measured slower
    # than serial execution. Lightweight DB summaries can overlap, followed
    # by two compatible source scans and two dependency scans. Reconstruct in
    # registry order so report JSON stays deterministic.
    by_key = dict(_DUE_DILIGENCE_COMPONENTS)
    gathered: dict[str, dict] = {}
    deadline = _time.monotonic() + _DUE_DILIGENCE_BUDGET_SECONDS
    gathered.update(
        _gather_components(
            tuple((key, by_key[key]) for key in ("health", "bus_factor", "complexity", "test_pyramid")),
            deadline=deadline,
        )
    )
    gathered.update(_gather_components((("clones", by_key["clones"]),), max_workers=1, deadline=deadline))
    gathered.update(
        _gather_components(
            tuple((key, by_key[key]) for key in ("dead", "smells")),
            max_workers=2,
            deadline=deadline,
        )
    )
    gathered.update(
        _gather_components(
            tuple((key, by_key[key]) for key in ("sbom", "vulns")),
            max_workers=2,
            deadline=deadline,
        )
    )
    gathered.update(
        _gather_components(
            tuple((key, by_key[key]) for key in ("supply_chain", "arch_drift")),
            max_workers=2,
            deadline=deadline,
        )
    )
    return {key: gathered[key] for key, _args in _DUE_DILIGENCE_COMPONENTS}


def _render_due_diligence(*, env: dict, meta: dict) -> str:
    """Render the due-diligence report (pure — no I/O)."""
    health = env.get("health", {})
    bus = env.get("bus_factor", {})
    cx = env.get("complexity", {})
    dead = env.get("dead", {})
    clones = env.get("clones", {})
    smells = env.get("smells", {})
    pyramid = env.get("test_pyramid", {})
    sbom = env.get("sbom", {})
    supply = env.get("supply_chain", {})
    vulns = env.get("vulns", {})
    drift = env.get("arch_drift", {})

    out: list[str] = _header(**meta)

    # Executive summary — synthesize a conservative verdict from health.
    score = _g(health, "health_score")
    out.append("## 1. Executive summary")
    out.append("")
    if isinstance(score, (int, float)):
        if score >= 75:
            band = "STRONG — investable with routine follow-up"
        elif score >= 55:
            band = "CAUTIONARY — investable with remediation"
        else:
            band = "NEEDS REMEDIATION — material engineering risk"
        out.append(f"**Verdict: {band} (health {score}/100).**")
    else:
        out.append("**Verdict: see sections below (health score unavailable).**")
    out.append("")
    out.append(
        "The sections below are generated directly from the repository. Each "
        "cites the Roam command that produced it so every number is reproducible."
    )
    out.append("")
    out.append(f"- Codebase health: {_verdict(health)}")
    out.append(f"- Key-person risk: {_verdict(bus)}")
    out.append(f"- Duplication: {_verdict(clones)}")
    out.append(f"- Dead code: {_verdict(dead)}")
    out.append("")

    # Health
    out.append("## 2. Codebase health (`roam health`)")
    out.append("")
    out.append("| Metric | Value |")
    out.append("|---|---|")
    out.append(f"| Overall health | {_cell(_g(health, 'health_score'))} / 100 |")
    out.append(f"| Total cycles | {_cell(_g(health, 'cycles_total', _g(health, 'total_cycles')))} |")
    out.append(f"| Actionable cycles | {_cell(_g(health, 'cycles_actionable', _g(health, 'actionable_cycles')))} |")
    out.append(f"| God components | {_cell(_g(health, 'god_components'))} |")
    out.append(f"| Tangle ratio | {_cell(_g(health, 'tangle_ratio'))} |")
    out.append("")

    # Bus factor
    out.append("## 3. Key-person / bus-factor risk (`roam bus-factor`)")
    out.append("")
    out.append(f"{_verdict(bus)}")
    out.append("")
    out.append(f"- High-risk modules: **{_cell(_g(bus, 'high_risk'))}**")
    out.append(f"- Single-owner modules: **{_cell(_g(bus, 'solo_authored_count', _g(bus, 'concentrated')))}**")
    out.append(f"- Directories analyzed: {_cell(_g(bus, 'directories_analyzed'))}")
    out.append("")

    # Complexity + smells
    out.append("## 4. Complexity & maintainability (`roam complexity`, `roam smells`)")
    out.append("")
    out.append("| Signal | Value |")
    out.append("|---|---|")
    out.append(f"| Average cognitive complexity | {_cell(_g(cx, 'average_complexity'))} |")
    out.append(f"| P90 complexity | {_cell(_g(cx, 'p90_complexity'))} |")
    out.append(f"| Critical-complexity symbols | {_cell(_g(cx, 'critical_count'))} |")
    out.append(f"| Symbols analyzed | {_cell(_g(cx, 'total_analyzed'))} |")
    out.append(f"| Total code smells | {_cell(_g(smells, 'total_smells'))} |")
    out.append(f"| Files with smells | {_cell(_g(smells, 'files_affected'))} |")
    out.append("")

    # Dead + clones
    out.append("## 5. Dead code & duplication (`roam dead`, `roam clones`)")
    out.append("")
    out.append(f"- Dead code: {_verdict(dead)}")
    out.append(
        f"  - Files affected: {_cell(_g(dead, 'files_affected'))}, "
        f"estimated remediation: {_cell(_g(dead, 'total_effort_hours'))} hours"
    )
    out.append(f"- Duplication: {_verdict(clones)}")
    reducible = _g(clones, "estimated_reducible_lines")
    if reducible is not None:
        out.append(f"  - Estimated reducible lines: **{_cell(reducible)}**")
    out.append("")

    # Test signal
    out.append("## 6. Test signal (`roam test-pyramid`)")
    out.append("")
    out.append(f"{_verdict(pyramid)}")
    out.append("")
    out.append(
        f"- Test files: {_cell(_g(pyramid, 'total'))} "
        f"(unit {_cell(_g(pyramid, 'unit'))}, integration {_cell(_g(pyramid, 'integration'))}, "
        f"e2e {_cell(_g(pyramid, 'e2e'))})"
    )
    out.append("")

    # Architecture drift
    out.append("## 7. Architecture drift (`roam architecture-drift`)")
    out.append("")
    out.append(f"{_verdict(drift)}")
    out.append("")

    # Security & supply chain
    out.append("## 8. Security & supply chain (`roam vulns`, `roam sbom`, `roam supply-chain`)")
    out.append("")
    out.append("| Source | Signal |")
    out.append("|---|---|")
    out.append(f"| `roam vulns` | {_cell(_verdict(vulns))} |")
    out.append(
        f"| `roam sbom` | {_cell(_g(sbom, 'reachable_count'))} reachable of "
        f"{_cell(_g(sbom, 'total_dependencies'))} deps, {_cell(_g(sbom, 'phantom_count'))} phantom |"
    )
    out.append(
        f"| `roam supply-chain` | risk {_cell(_g(supply, 'risk_score'))}/100, "
        f"pin coverage {_cell(_g(supply, 'pin_coverage_pct'))}% |"
    )
    out.append("")

    # Remediation themes
    out.append("## 9. Remediation themes")
    out.append("")
    out.append(
        "The highest-leverage items surface from sections 2–8 above: break the "
        "actionable cycles, address single-owner concentration in the modules "
        "named by `roam bus-factor`, and reduce the duplication `roam clones` "
        "quantifies. A paid engagement turns these into a costed, sequenced "
        "remediation plan."
    )
    out.append("")

    out.extend(_paid_framing(type_meta=meta["type_meta"], client=meta["client"]))
    out.extend(
        _footer(
            report_type="due-diligence",
            generated_at=meta["generated_at"],
            extra_scope=[
                "**Penetration testing.** Section 8 surfaces structural and reachability signals, not exploit paths.",
                "**Runtime performance profiling.** Complexity is static; it is not a benchmark run.",
            ],
        )
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Type: ai-readiness
# ---------------------------------------------------------------------------


def _gather_ai_readiness() -> dict:
    return _gather_components(
        (
            ("readiness", ["ai-readiness"]),
            ("ai_ratio", ["ai-ratio"]),
            ("agent_score", ["agent-score"]),
            ("mode", ["mode"]),
        )
    )


def _render_ai_readiness(*, env: dict, meta: dict) -> str:
    readiness = env.get("readiness", {})
    ratio = env.get("ai_ratio", {})
    agents = env.get("agent_score", {})
    mode = env.get("mode", {})

    out: list[str] = _header(**meta)

    score = _g(readiness, "score")
    label = _g(readiness, "label")
    out.append("## 1. Executive summary")
    out.append("")
    if score is not None:
        out.append(f"**Readiness verdict: {_cell(score)}/100 — {_cell(label)}.**")
    else:
        out.append("**Readiness verdict: see dimensions below (score unavailable).**")
    out.append("")
    out.append(
        "Readiness is scored across structural dimensions that predict how "
        "safely agents can operate in this codebase, alongside the existing AI "
        "footprint and the governance posture already in place."
    )
    out.append("")

    # Readiness dimensions
    out.append("## 2. Readiness dimensions (`roam ai-readiness`)")
    out.append("")
    dims = readiness.get("dimensions") if isinstance(readiness, dict) else None
    if isinstance(dims, list) and dims:
        out.append("| Dimension | Score | Weight | Contribution |")
        out.append("|---|---:|---:|---:|")
        for d in dims:
            if not isinstance(d, dict):
                continue
            out.append(
                f"| {_cell(d.get('label') or d.get('name'))} | {_cell(d.get('score'))} | "
                f"{_cell(d.get('weight'))} | {_cell(d.get('contribution'))} |"
            )
        out.append("")
    else:
        out.append(f"_{_verdict(readiness)}_")
        out.append("")

    # AI footprint
    out.append("## 3. Existing AI footprint (`roam ai-ratio`)")
    out.append("")
    out.append(f"{_verdict(ratio)}")
    out.append("")
    out.append(
        f"- Estimated AI-generated share: **{_pct(_g(ratio, 'ai_ratio'), 1)}** "
        f"(confidence: {_cell(_g(ratio, 'confidence'))}) across "
        f"{_cell(_g(ratio, 'commits_analyzed'))} commits."
    )
    out.append("")

    # Agent activity
    out.append("## 4. Agent activity (`roam agent-score`)")
    out.append("")
    out.append(f"{_verdict(agents)}")
    out.append("")
    out.append(f"- Agents scored: **{_cell(_g(agents, 'agents_scored', _g(agents, 'count')))}**")
    out.append("")

    # Governance posture
    out.append("## 5. Governance posture (`roam mode`)")
    out.append("")
    out.append("| Gate | Status |")
    out.append("|---|---|")
    out.append(f"| Active mode | {_cell(_g(mode, 'active_mode'))} |")
    out.append(f"| Allowed commands | {_cell(_g(mode, 'allowed_count'))} |")
    out.append(f"| Policy source | {_cell(_g(mode, 'policy_source'))} |")
    out.append(f"| Persisted | {_cell(_g(mode, 'persisted'))} |")
    out.append("")

    # Recommendations
    out.append("## 6. Recommendations")
    out.append("")
    recs = readiness.get("recommendations") if isinstance(readiness, dict) else None
    if isinstance(recs, list) and recs:
        for r in recs[:10]:
            out.append(f"- {_cell(r)}")
    else:
        out.append("- No structured recommendations surfaced; see the dimension scores above.")
    out.append("")

    # Phased rollout
    out.append("## 7. Suggested phased rollout")
    out.append("")
    out.append("| Phase | Scope |")
    out.append("|---|---|")
    out.append("| 1 | Declare an active mode (`roam mode safe_edit`); enforce `roam preflight` pre-commit |")
    out.append("| 2 | Agent edits in the lowest-blast-radius, best-tested zones only |")
    out.append("| 3 | Expand to broader zones under senior review as the readiness score improves |")
    out.append("")

    out.extend(_paid_framing(type_meta=meta["type_meta"], client=meta["client"]))
    out.extend(
        _footer(
            report_type="ai-readiness",
            generated_at=meta["generated_at"],
            extra_scope=[
                "**Team practices & risk appetite.** Readiness scores structural "
                "signals; the rollout decision also depends on team maturity.",
            ],
        )
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Type: reachability-triage
# ---------------------------------------------------------------------------


def _gather_reachability_triage() -> dict:
    return _gather_components(
        (
            ("sbom", ["sbom"]),
            ("supply_chain", ["supply-chain"]),
            ("vulns", ["vulns"]),
            ("vuln_reach", ["vuln-reach"]),
            ("taint", ["taint"]),
            ("secrets", ["secrets"]),
        )
    )


def _render_reachability_triage(*, env: dict, meta: dict) -> str:
    sbom = env.get("sbom", {})
    supply = env.get("supply_chain", {})
    vulns = env.get("vulns", {})
    vuln_reach = env.get("vuln_reach", {})
    taint = env.get("taint", {})
    secrets = env.get("secrets", {})

    out: list[str] = _header(**meta)

    # Executive summary — the reachability wedge.
    total_deps = _g(sbom, "total_dependencies")
    reachable_deps = _g(sbom, "reachable_count")
    taint_findings = _g(taint, "findings", 0)
    secret_findings = _g(secrets, "total_findings", 0)
    reach_vulns = _g(vuln_reach, "reachable_count", 0)

    out.append("## 1. Executive summary")
    out.append("")
    out.append(
        "**The wedge: separate what is reachable from scanner noise.** This "
        "sweep runs the scanners, then filters every finding against the call "
        "graph — only findings reachable from a production entry point warrant "
        "fix work this sprint."
    )
    out.append("")
    if isinstance(total_deps, (int, float)) and isinstance(reachable_deps, (int, float)):
        out.append(
            f"- Dependency reachability: **{_cell(reachable_deps)} of "
            f"{_cell(total_deps)}** dependencies reachable "
            f"({_pct(reachable_deps, total_deps)}); the rest are not reachable "
            f"from the analysed entry points."
        )
    out.append(f"- Reachable known vulnerabilities: **{_cell(reach_vulns)}**")
    out.append(f"- Taint flows: **{_cell(taint_findings)}**")
    out.append(f"- Active secrets: **{_cell(secret_findings)}**")
    out.append("")

    # Reachable vulns
    out.append("## 2. Known vulnerabilities (`roam vulns`, `roam vuln-reach`)")
    out.append("")
    out.append(f"- `roam vulns`: {_verdict(vulns)}")
    out.append(f"- `roam vuln-reach`: {_verdict(vuln_reach)}")
    out.append("")
    if not (_g(vulns, "total") or _g(vuln_reach, "total_vulns")):
        out.append(
            "> No scanner report is ingested for this run. Ingest one with "
            "`roam vulns --import-file <report.json>` (npm-audit, pip-audit, "
            "trivy, or osv) then `roam vuln-map` to populate reachability — the "
            "reachable-vs-raw reduction is the headline number for a paid engagement."
        )
        out.append("")

    # Dependency reachability (the SBOM signal)
    out.append("## 3. Dependency reachability (`roam sbom`)")
    out.append("")
    out.append("| Signal | Value |")
    out.append("|---|---|")
    out.append(f"| Total dependencies | {_cell(_g(sbom, 'total_dependencies'))} |")
    out.append(f"| Reachable | {_cell(_g(sbom, 'reachable_count'))} |")
    out.append(f"| Reachable (direct) | {_cell(_g(sbom, 'reachable_direct_count'))} |")
    out.append(f"| Phantom (declared, not imported) | {_cell(_g(sbom, 'phantom_count'))} |")
    out.append("")
    out.append(f"_{_verdict(sbom)}_")
    out.append("")

    # Taint exposure
    out.append("## 4. Taint exposure (`roam taint`)")
    out.append("")
    out.append(f"{_verdict(taint)}")
    out.append("")
    out.append(
        f"- Findings: **{_cell(_g(taint, 'findings'))}** across "
        f"{_cell(_g(taint, 'rules'))} rule(s); risk score {_cell(_g(taint, 'risk_score'))}."
    )
    out.append("")

    # Secrets
    out.append("## 5. Secrets (`roam secrets`)")
    out.append("")
    out.append(f"{_verdict(secrets)}")
    out.append("")
    out.append(f"- Active secret findings: **{_cell(_g(secrets, 'total_findings'))}**")
    out.append("")

    # Supply chain
    out.append("## 6. Supply chain (`roam supply-chain`)")
    out.append("")
    out.append(f"{_verdict(supply)}")
    out.append("")
    out.append(
        f"- Risk score: {_cell(_g(supply, 'risk_score'))}/100; "
        f"pin coverage {_cell(_g(supply, 'pin_coverage_pct'))}%; "
        f"unpinned {_cell(_g(supply, 'unpinned_count'))} of "
        f"{_cell(_g(supply, 'total_dependencies'))}."
    )
    out.append("")

    # Fix order
    out.append("## 7. Recommended fix order")
    out.append("")
    out.append(
        "1. Any reachable known vulnerability (section 2) — patch first.\n"
        "2. Active secrets (section 5) — rotate, then scrub history.\n"
        "3. Reachable taint flows (section 4) — sanitize the source→sink path.\n"
        "4. Supply-chain pinning (section 6) — pin the unpinned direct deps.\n"
        "5. Defer non-reachable findings; document why in the next scanner baseline."
    )
    out.append("")

    out.extend(_paid_framing(type_meta=meta["type_meta"], client=meta["client"]))
    out.extend(
        _footer(
            report_type="reachability-triage",
            generated_at=meta["generated_at"],
            extra_scope=[
                "**A penetration test or threat model.** Non-reachable findings "
                "may still be exploitable via paths the static graph misses — "
                "review with the security team before deprioritizing.",
            ],
        )
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Type: post-incident
# ---------------------------------------------------------------------------


def _gather_post_incident(commit_range: str) -> dict:
    """Replay a range with postmortem + verify the audit trail."""
    postmortem = _run_postmortem(commit_range, limit=100)
    return {
        "postmortem": postmortem if isinstance(postmortem, dict) else {},
        "audit_trail": _run_roam_json(["audit-trail-verify"]),
    }


def _render_post_incident(*, env: dict, meta: dict, commit_range: str) -> str:
    postmortem = env.get("postmortem", {})
    trail = env.get("audit_trail", {})
    pm_summary = postmortem.get("summary") if isinstance(postmortem, dict) else {}
    pm_summary = pm_summary if isinstance(pm_summary, dict) else {}
    commits = postmortem.get("commits") if isinstance(postmortem, dict) else []
    commits = commits if isinstance(commits, list) else []

    out: list[str] = _header(**meta)

    scanned = pm_summary.get("commits_scanned", len(commits))
    with_findings = pm_summary.get("commits_with_findings", 0)

    out.append("## 1. Incident window")
    out.append("")
    out.append(f"- Replayed range: `{commit_range}`")
    out.append(f"- Commits replayed: **{_cell(scanned)}**")
    out.append(f"- Commits that would have surfaced findings pre-merge: **{_cell(with_findings)}**")
    out.append("")

    # Detector replay
    out.append("## 2. Detector replay (`roam postmortem`)")
    out.append("")
    out.append(
        "Each commit's outgoing diff is replayed against the current detector "
        "set, as if it were a pull request — which findings would have "
        "surfaced before the change merged?"
    )
    out.append("")
    flagged = [
        c for c in commits if isinstance(c, dict) and (int(c.get("high", 0) or 0) + int(c.get("medium", 0) or 0)) > 0
    ]
    if flagged:
        out.append("| Date | SHA | Subject | High | Medium | Top hits |")
        out.append("|---|---|---|---:|---:|---|")
        for c in flagged[:20]:
            subject = (str(c.get("subject") or "")).replace("|", "/")[:60]
            kinds = ", ".join(c.get("kinds") or [])
            out.append(
                f"| {_cell(c.get('date'))} | `{_cell(c.get('short_sha'))}` | {subject} | "
                f"{_cell(c.get('high', 0))} | {_cell(c.get('medium', 0))} | {kinds or '-'} |"
            )
        out.append("")
    else:
        out.append(
            "_No commit in this window would have been flagged by the current "
            "detector set. That is a clean-window observation, not proof of "
            "absence — widen the range or confirm the detector covers the "
            "incident class._"
        )
        out.append("")

    # Audit trail
    out.append("## 3. Audit-trail integrity (`roam audit-trail-verify`)")
    out.append("")
    out.append(f"{_verdict(trail)}")
    out.append("")
    out.append("| Signal | Value |")
    out.append("|---|---|")
    out.append(f"| Chain valid | {_cell(_g(trail, 'chain_valid'))} |")
    out.append(f"| Chain tier | {_cell(_g(trail, 'chain_tier'))} |")
    out.append(f"| Records | {_cell(_g(trail, 'total_records'))} |")
    out.append(f"| Unsigned events | {_cell(_g(trail, 'unsigned_events'))} |")
    out.append("")
    out.append(
        "A verified chain means the run ledger for this window has not been "
        "tampered with — the attribution below rests on a signed record. A "
        'commit with no run record is itself a finding ("shipped without '
        'ledger coverage").'
    )
    out.append("")

    # Prevention
    out.append("## 4. Prevention artifact")
    out.append("")
    out.append(
        "For each detector class that surfaced in section 2, author a rule "
        "under `.roam/rules/` that fails on the incident-introducing change if "
        "reapplied, then wire it into `roam preflight` / `roam critique` so the "
        "same class of change is blocked pre-merge. The durable output of a "
        "post-incident engagement is that rule, not just the narrative."
    )
    out.append("")

    out.extend(_paid_framing(type_meta=meta["type_meta"], client=meta["client"]))
    out.extend(
        _footer(
            report_type="post-incident",
            generated_at=meta["generated_at"],
            extra_scope=[
                "**Full root-cause analysis.** Not every cause is a single "
                "commit, and not every prevention is expressible as a static rule.",
                "**Config-only / infra-only / third-party incidents.** This "
                "replay covers code-change causes tracked in git history.",
            ],
        )
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Dispatch table.
# ---------------------------------------------------------------------------

_GATHER = {
    "due-diligence": lambda commit_range: _gather_due_diligence(),
    "ai-readiness": lambda commit_range: _gather_ai_readiness(),
    "reachability-triage": lambda commit_range: _gather_reachability_triage(),
    "post-incident": lambda commit_range: _gather_post_incident(commit_range),
}


def _render(report_type: str, *, env: dict, meta: dict, commit_range: str) -> str:
    if report_type == "due-diligence":
        return _render_due_diligence(env=env, meta=meta)
    if report_type == "ai-readiness":
        return _render_ai_readiness(env=env, meta=meta)
    if report_type == "reachability-triage":
        return _render_reachability_triage(env=env, meta=meta)
    if report_type == "post-incident":
        return _render_post_incident(env=env, meta=meta, commit_range=commit_range)
    raise ValueError(f"unknown report type: {report_type}")


def _headline(report_type: str, env: dict) -> str:
    """One-line headline for the engagement ledger + envelope summary."""
    if report_type == "due-diligence":
        return _verdict(env.get("health", {}))
    if report_type == "ai-readiness":
        return _verdict(env.get("readiness", {}))
    if report_type == "reachability-triage":
        return _verdict(env.get("sbom", {}))
    if report_type == "post-incident":
        pm = env.get("postmortem", {})
        return _verdict(pm)
    return "not available"


def _component_health(env: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return explicit failed/degraded component names for report disclosure."""
    failed: list[str] = []
    degraded: list[str] = []
    for name, envelope in env.items():
        if not isinstance(envelope, dict):
            failed.append(name)
            continue
        summary = envelope.get("summary")
        if not isinstance(summary, dict):
            failed.append(name)
            continue
        status = envelope.get("status")
        if envelope.get("isError") is True or status == "hard_failure":
            failed.append(name)
        elif summary.get("partial_success") is True or status == "soft_failure":
            degraded.append(name)
    return tuple(failed), tuple(degraded)


# ---------------------------------------------------------------------------
# Engagement ledger — append-only JSONL next to .roam/index.db. Same file
# ``cmd_pr_replay`` writes to; the ``kind`` discriminator distinguishes
# service-report rows from pr-replay rows. Flat schema, additive only.
# ---------------------------------------------------------------------------


def _record_engagement(
    *,
    report_type: str,
    client: str | None,
    subject: str,
    headline: str,
    output_path: str,
    generated_at: str,
) -> Path | None:
    """Append one service-report record to ``.roam/engagements.jsonl``.

    Returns the ledger path on success, ``None`` on failure (never raises —
    telemetry must not break a buyer-facing run).
    """
    try:
        ledger_dir = Path(".roam")
        ledger_dir.mkdir(exist_ok=True)
        ledger = ledger_dir / "engagements.jsonl"
        record = {
            "ledger_schema": 1,
            "kind": "service-report",
            "report_type": report_type,
            "client": client,
            "subject": subject,
            "headline": headline,
            "output_path": output_path,
            "generated_at": generated_at,
        }
        with ledger.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(record) + "\n")
        return ledger
    except OSError:
        return None


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------


@roam_capability(
    category="review",
    summary="Generate a one-command service-engagement report (due-diligence, AI-readiness, reachability-triage, post-incident).",
    inputs=["report_type"],
    outputs=["narrative_report", "sections"],
    examples=[
        "roam service-report --type due-diligence",
        "roam service-report --type reachability-triage --client 'Acme Inc' --output triage.md",
        "roam service-report --type post-incident --range v1.0..main --output incident.md",
    ],
    tags=["audit", "review", "services", "demo"],
    ai_safe=True,
    requires_index=True,
    since="13.5",
    side_effect=True,
)
@click.command(name="service-report")
@click.option(
    "--type",
    "report_type",
    type=click.Choice(list(_REPORT_TYPES.keys()), case_sensitive=False),
    required=True,
    help=(
        "Report type. ``due-diligence`` (codebase health / M&A), "
        "``ai-readiness`` (AI adoption readiness), ``reachability-triage`` "
        "(security noise-reduction), or ``post-incident`` (detector + "
        "audit-trail replay of a commit range)."
    ),
)
@click.option(
    "--client",
    default=None,
    help="Client name to inject into the report header (paid framing).",
)
@click.option(
    "--range",
    "commit_range",
    default=None,
    help=(
        "Commit range for ``--type post-incident`` (e.g. ``v1.0..main``, "
        "``HEAD~30..HEAD``). Ignored by the other report types. Defaults to "
        "``HEAD~20..HEAD`` when unset."
    ),
)
@click.option(
    "--output",
    "output_path",
    default=None,
    type=click.Path(dir_okay=False, writable=True),
    help="Write the Markdown report to PATH instead of stdout.",
)
@click.option(
    "--pdf",
    "pdf_path",
    default=None,
    type=click.Path(dir_okay=False, writable=True),
    help=(
        "Also write a PDF render of the report to PATH (requires ``pandoc`` on "
        "PATH, or ``reportlab`` as a fallback). Implies --output if unset; the "
        "Markdown source is written next to the PDF as ``<pdf>.md``."
    ),
)
@click.option(
    "--track-engagement/--no-track-engagement",
    default=True,
    show_default=True,
    help=(
        "When --output is set, append a one-line JSONL record to "
        "``.roam/engagements.jsonl`` (report type, client, subject, headline, "
        "output path, timestamp) so the operator has a single-file ledger of "
        "every delivered report."
    ),
)
@click.pass_context
def service_report_cmd(
    ctx,
    report_type: str,
    client: str | None,
    commit_range: str | None,
    output_path: str | None,
    pdf_path: str | None,
    track_engagement: bool,
):
    """Generate a one-command service-engagement report.

    Runs the right existing Roam primitives for the chosen ``--type``,
    aggregates their JSON envelopes, and emits a buyer-facing narrative
    report — the productised form of the templates under
    ``templates/services-reports/``. Sibling of ``roam pr-replay``.

    \b
    Examples:
      roam service-report --type due-diligence
      roam service-report --type reachability-triage --client "Acme Inc" --output triage.md
      roam service-report --type post-incident --range v1.0..main --output incident.md

    \b
    Output: Markdown by default; ``roam --json service-report`` returns the
    full envelope (summary + sections + report_markdown).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    report_type = report_type.lower()
    type_meta = _REPORT_TYPES[report_type]
    ensure_index()

    # Post-incident is the only type that consumes a commit range. Validate
    # it the same way pr-replay validates --range (reject argv-injection
    # shapes) and default to a recent window.
    if report_type == "post-incident":
        if commit_range is None:
            commit_range = "HEAD~20..HEAD"
        elif not _is_safe_commit_range(commit_range):
            raise click.UsageError(
                f"--range value must not start with '-' (got {commit_range!r}); "
                "use a git revspec like 'HEAD~30..HEAD', 'v1.0..main', or a branch name."
            )
    else:
        commit_range = commit_range or ""

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    index_sha = _git_head_sha()
    subject = client or "target repository"

    # Gather best-effort, but never collapse a failed component into an empty
    # successful-looking report. Each expected failure is already represented
    # by a structured component envelope; this outer guard handles only an
    # unexpected orchestration defect.
    try:
        env = _GATHER[report_type](commit_range)
    except Exception as exc:  # noqa: BLE001 — the report must survive a bad section
        env = {
            report_type: _component_failure(
                report_type,
                "report_gather_failure",
                f"report gathering failed ({type(exc).__name__})",
            )
        }

    component_failures, component_degraded = _component_health(env)

    meta = {
        "type_meta": type_meta,
        "report_type": report_type,
        "client": client,
        "index_sha": index_sha,
        "generated_at": generated_at,
        "subject": subject,
        "component_failures": component_failures,
        "component_degraded": component_degraded,
    }
    report_md = _render(report_type, env=env, meta=meta, commit_range=commit_range)
    headline = _headline(report_type, env)
    if component_failures:
        headline = f"{headline} — partial report: {len(component_failures)} unavailable components"
    elif component_degraded:
        headline = f"{headline} — degraded evidence: {len(component_degraded)} partial components"

    # --pdf without --output writes the markdown sibling next to the PDF.
    if pdf_path and not output_path:
        output_path = str(Path(pdf_path).with_suffix(".md"))

    if output_path:
        Path(output_path).write_text(report_md, encoding="utf-8")
        if not json_mode:
            click.echo(f"Wrote {len(report_md):,} bytes to {output_path}")

    pdf_backend = None
    if pdf_path:
        ok, info = _render_pdf(report_md, Path(pdf_path))
        if ok:
            pdf_backend = info
            if not json_mode:
                click.echo(f"Wrote PDF to {pdf_path} (backend: {info})")
        else:
            click.echo(f"WARNING: PDF render failed — {info}", err=True)

    engagement_record = None
    if track_engagement and output_path:
        engagement_record = _record_engagement(
            report_type=report_type,
            client=client,
            subject=subject,
            headline=headline,
            output_path=output_path,
            generated_at=generated_at,
        )
        if engagement_record and not json_mode:
            click.echo(f"Logged engagement to {engagement_record}")

    if json_mode:
        envelope = json_envelope(
            "service-report",
            summary={
                "verdict": headline,
                "report_type": report_type,
                "client": client,
                "subject": subject,
                "commit_range": commit_range or None,
                "index_sha": index_sha,
                "generated_at": generated_at,
                "output_path": output_path,
                "pdf_path": pdf_path,
                "pdf_backend": pdf_backend,
                "engagement_logged_to": str(engagement_record) if engagement_record else None,
                "sections_present": sorted(k for k, v in env.items() if v),
                "sections_failed": list(component_failures),
                "sections_degraded": list(component_degraded),
                "state": (
                    "component_failure"
                    if component_failures
                    else "component_degraded"
                    if component_degraded
                    else "complete"
                ),
                "partial_success": bool(component_failures or component_degraded),
            },
            report_markdown=report_md,
            sections=env,
        )
        _target = (f"{report_type}:{commit_range}" if commit_range else report_type)[:80]
        try:
            auto_log(envelope, action="service-report", target=_target)
        except Exception as _exc:  # noqa: BLE001 — telemetry must not break the run
            # Telemetry failure must not break the report — surface lineage
            # so a dropped engagement-log record has a traceable cause.
            from roam.observability import log_swallowed

            log_swallowed("cmd_service_report:auto_log", _exc)
        click.echo(to_json(envelope))
        _ = EXIT_SUCCESS
        return

    if not output_path:
        click.echo(report_md)

    _ = EXIT_SUCCESS
    return
