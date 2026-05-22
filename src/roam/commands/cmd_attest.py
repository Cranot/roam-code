"""Proof-carrying PR attestation — bundle all evidence into one artifact.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because attest outputs are in-toto attestations — not per-location
violations. SARIF is reserved for findings with file:line coordinates;
attest's primary deliverable is the in-toto attestation. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket C propagation plan +
W1148 audit memo.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.changed_files import (
    get_changed_files,
    is_test_file,
    resolve_changed_to_db,
)
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, find_project_root, open_db
from roam.output.formatter import abbrev_kind, json_envelope, to_json
from roam.output.risk import normalize_risk_level, risk_rank
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# W641-followup-D — canonical risk-LEVEL projection (cluster closer)
# ---------------------------------------------------------------------------
#
# Pattern-3a structural close-out (cluster-closer, fifth+ axis after W641 +
# followup-A/B/C):
#
# W641 shipped canonical risk-LEVEL emission on ``cmd_pr_risk`` (third axis
# after W547 severity + W596 confidence). Follow-ups extended the discipline
# to ``cmd_impact`` (W641-followup-A), ``cmd_critique`` (W641-followup-B),
# and ``cmd_pr_bundle`` (W641-followup-C). ``cmd_attest`` is the natural
# cluster closer: it aggregates risk + breaking + budget + fitness signals
# into a single proof-carrying artifact and emits its own internal risk
# level (``LOW``/``MODERATE``/``HIGH``/``CRITICAL`` from ``_collect_risk``),
# but never projected onto the canonical W631 set so agents comparing
# attest's worst-case against pr-risk / impact / critique on a single floor
# had to re-derive the rank.
#
# The attest internal vocabulary is already a near-mirror of W631's
# 4-tier set — the only domain alias is ``MODERATE`` → canonical
# ``medium`` (resolved via ``RISK_ALIASES``).
#
# Conservative-on-critical: ``_collect_risk`` IS allowed to emit
# ``CRITICAL`` (the composite-risk score's >75/100 tier) — unlike
# critique / impact, attest's composite is a multi-factor blend that
# can legitimately reach the critical threshold. We preserve that
# escalation through the projection (do NOT saturate at ``high``).
#
# Safe-floor: missing risk (``_collect_risk`` returned None because
# networkx isn't installed, or a degraded-resolution path produced an
# empty bundle) collapses to ``low`` (W531 CI-safety floor: a
# typo'd / absent label MUST NOT promote into a CI-gating rank).
# Emitted unconditionally — agents can call
# ``risk_rank(summary["risk_level_canonical"])`` without None-handling.


def _attest_risk_level(
    risk: dict | None,
    *,
    warnings_out: list[str] | None = None,
) -> str:
    """Project attest's internal risk dict onto the canonical W631 risk-LEVEL set.

    Returns a string in :data:`roam.output.risk.RISK_LEVELS`
    (``critical``/``high``/``medium``/``low``). Missing / unknown
    inputs safe-floor to ``low`` (the W531 CI-safety lesson: a typo'd
    or absent label MUST NOT promote a finding into a CI-failing rank).

    Unknown ``risk.level`` strings accumulate a marker on *warnings_out*
    (when provided) under the ``attest_unknown_status:<value>`` key so
    Pattern-2 silent-fallback discipline stays loud — the projection
    safe-floors to ``low`` but the marker disambiguates a real-low
    finding from an unrecognised-label drop (mirrors W641-followup-B
    critique + W989 pr-risk + W918 alerts).

    Conservative-on-critical: unlike critique / impact which saturate
    at ``high``, the attest composite-risk score IS allowed to reach
    ``critical`` (the >75/100 tier of ``_collect_risk``). We preserve
    that escalation through the projection.
    """
    if risk is None:
        # Missing risk evidence — networkx import failed OR degraded
        # path. Safe-floor to ``low``; never None.
        return "low"
    level_raw = risk.get("level") if isinstance(risk, dict) else None
    if not level_raw:
        return "low"
    canonical = normalize_risk_level(level_raw)
    if canonical is None:
        # Unknown label — safe-floor + record marker so the silent
        # fallback stays loud.
        if warnings_out is not None:
            warnings_out.append(f"attest_unknown_status:{level_raw}")
        return "low"
    return canonical


# ---------------------------------------------------------------------------
# Evidence collectors
# ---------------------------------------------------------------------------


def _collect_blast_radius(conn, file_map):
    """Compute blast radius for changed files.

    Returns {changed_files, affected_symbols, affected_files, per_file}.
    """
    try:
        import networkx as nx

        from roam.graph.builder import build_symbol_graph
    except ImportError:
        return {
            "changed_files": len(file_map),
            "affected_symbols": 0,
            "affected_files": 0,
            "per_file": [],
        }

    G = build_symbol_graph(conn)
    RG = G.reverse()

    all_affected_syms = set()
    all_affected_files = set()
    sym_by_file = {}

    for path, fid in file_map.items():
        syms = conn.execute("SELECT id, name, kind FROM symbols WHERE file_id = ?", (fid,)).fetchall()
        sym_by_file[path] = syms

        for s in syms:
            sid = s["id"]
            if sid in RG:
                deps = nx.descendants(RG, sid)
                all_affected_syms.update(deps)
                for d in deps:
                    node = G.nodes.get(d, {})
                    fp = node.get("file_path")
                    if fp and fp != path:
                        all_affected_files.add(fp)

    return {
        "changed_files": len(file_map),
        "affected_symbols": len(all_affected_syms),
        "affected_files": len(all_affected_files),
        "sym_by_file": sym_by_file,
    }


def _collect_risk(conn, root, file_map, staged, commit_range):
    """Compute risk score.

    Returns {score, level, verdict} or None on failure.
    """
    try:
        import networkx as nx

        from roam.commands.changed_files import is_low_risk_file
        from roam.commands.cmd_coupling import _compute_surprise
        from roam.commands.cmd_pr_risk import (
            _author_count_risk,
            _author_familiarity,
            _calibrated_hotspot_score,
            _detect_author,
        )
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers  # noqa: F401
    except ImportError:
        return None

    total_syms = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    author = _detect_author()

    G = build_symbol_graph(conn)
    RG = G.reverse()

    # Blast radius factor
    all_affected = set()
    changed_sym_ids = set()
    for path, fid in file_map.items():
        syms = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (fid,)).fetchall()
        for s in syms:
            changed_sym_ids.add(s["id"])
            if s["id"] in RG:
                all_affected.update(nx.descendants(RG, s["id"]))

    blast_pct = len(all_affected) * 100 / total_syms if total_syms else 0

    # Hotspot factor
    hotspot_score = 0.0
    churn_data = {}
    for path, fid in file_map.items():
        row = conn.execute(
            "SELECT total_churn, commit_count FROM file_stats WHERE file_id = ?",
            (fid,),
        ).fetchone()
        if row:
            churn_data[path] = {"churn": row["total_churn"], "commits": row["commit_count"]}

    if churn_data:
        code_churn = {p: d for p, d in churn_data.items() if not is_low_risk_file(p)}
        repo_churn_rows = conn.execute(
            "SELECT f.path, fs.total_churn FROM file_stats fs "
            "JOIN files f ON fs.file_id = f.id "
            "WHERE fs.total_churn IS NOT NULL"
        ).fetchall()
        repo_code_churn = sorted(
            float(r["total_churn"] or 0)
            for r in repo_churn_rows
            if (r["total_churn"] or 0) > 0 and not is_low_risk_file(r["path"])
        )
        if repo_code_churn and code_churn:
            avg_changed = sum(d["churn"] for d in code_churn.values()) / len(code_churn)
            hotspot_score = _calibrated_hotspot_score(avg_changed, repo_code_churn)

    # Bus factor
    author_counts = []
    for path, fid in file_map.items():
        if is_test_file(path) or is_low_risk_file(path):
            continue
        authors = conn.execute(
            "SELECT DISTINCT gc.author FROM git_file_changes gfc "
            "JOIN git_commits gc ON gfc.commit_id = gc.id "
            "WHERE gfc.file_id = ?",
            (fid,),
        ).fetchall()
        if authors:
            author_counts.append(len(authors))
    bus_factor_risk = _author_count_risk(author_counts) if author_counts else 0.0

    # Test coverage — bulk-fetch incoming source paths for all source files
    # in one query (was N+1: one SELECT per source file).
    source_files = [p for p in file_map if not is_test_file(p) and not is_low_risk_file(p)]
    covered_files = 0
    if source_files:
        source_fids = [file_map[p] for p in source_files]
        rows = batched_in(
            conn,
            "SELECT fe.target_file_id, f.path FROM file_edges fe "
            "JOIN files f ON fe.source_file_id = f.id "
            "WHERE fe.target_file_id IN ({ph})",
            source_fids,
        )
        incoming_by_fid: dict = {}
        for r in rows:
            incoming_by_fid.setdefault(r["target_file_id"], []).append(r["path"])
        for path in source_files:
            fid = file_map[path]
            if any(is_test_file(p) for p in incoming_by_fid.get(fid, ())):
                covered_files += 1
    test_coverage = covered_files / len(source_files) if source_files else 0.0

    # Coupling
    coupling_score = 0.0
    if len(file_map) > 1:
        fids = list(file_map.values())
        ph = ",".join("?" for _ in fids)
        cross_edges = conn.execute(
            f"SELECT COUNT(*) FROM file_edges WHERE source_file_id IN ({ph}) AND target_file_id IN ({ph})",
            fids + fids,
        ).fetchone()[0]
        max_possible = len(fids) * (len(fids) - 1)
        if max_possible > 0:
            coupling_score = min(1.0, cross_edges / max_possible)

    # Novelty
    change_fids = list(file_map.values())
    novelty, _, _ = _compute_surprise(conn, change_fids)

    # Author familiarity
    familiarity_risk = 0.0
    if author:
        familiarity_risk, _ = _author_familiarity(conn, author, file_map)

    # Composite risk score
    _factors = [
        min(blast_pct / 100, 0.40),
        hotspot_score * 0.30,
        (1 - test_coverage) * 0.30,
        bus_factor_risk * 0.20,
        coupling_score * 0.20,
        novelty * 0.15,
        familiarity_risk,
    ]
    no_risk = 1.0
    for f in _factors:
        no_risk *= 1 - max(0, min(f, 0.99))
    risk = int(min(100, (1 - no_risk) * 100))

    if risk <= 25:
        level = "LOW"
    elif risk <= 50:
        level = "MODERATE"
    elif risk <= 75:
        level = "HIGH"
    else:
        level = "CRITICAL"

    return {"score": risk, "level": level}


def _collect_breaking(conn, root, base_ref):
    """Detect breaking changes vs base_ref.

    Returns {removed, signature_changed, renamed} lists.
    """
    try:
        from roam.commands.cmd_breaking import (
            _compare_file,
            _extract_old_symbols,
            _get_current_symbols,
            _git_changed_files,
            _git_show,
        )
    except ImportError:
        return {"removed": [], "signature_changed": [], "renamed": []}

    changed = _git_changed_files(root, base_ref)
    if not changed:
        return {"removed": [], "signature_changed": [], "renamed": []}

    all_removed = []
    all_sig_changed = []
    all_renamed = []

    for fpath in changed:
        old_source = _git_show(root, base_ref, fpath)
        if old_source is None:
            continue
        old_symbols = _extract_old_symbols(old_source, fpath)
        if not old_symbols:
            continue
        new_symbols = _get_current_symbols(conn, fpath)
        removed, sig_changed, renamed = _compare_file(fpath, old_symbols, new_symbols)
        all_removed.extend(removed)
        all_sig_changed.extend(sig_changed)
        all_renamed.extend(renamed)

    return {
        "removed": all_removed,
        "signature_changed": all_sig_changed,
        "renamed": all_renamed,
    }


def _collect_affected_tests_evidence(conn, sym_by_file):
    """Gather affected tests.

    Returns {selected, direct, transitive, colocated, command, tests}.
    """
    from roam.commands.cmd_diff import _collect_affected_tests

    test_results, pytest_cmd = _collect_affected_tests(conn, sym_by_file)

    direct = sum(1 for t in test_results if t["kind"] == "DIRECT")
    transitive = sum(1 for t in test_results if t["kind"] == "TRANSITIVE")
    colocated = sum(1 for t in test_results if t["kind"] == "COLOCATED")

    return {
        "selected": len(test_results),
        "direct": direct,
        "transitive": transitive,
        "colocated": colocated,
        "command": pytest_cmd,
        "tests": [
            {
                "file": t["file"],
                "symbol": t.get("symbol"),
                "kind": t["kind"],
                "hops": t.get("hops", 0),
            }
            for t in test_results[:50]
        ],
    }


def _collect_budget_evidence(conn, root):
    """Evaluate budget rules.

    Returns {rules_checked, passed, failed, skipped, rules}.
    """
    try:
        from roam.commands.cmd_budget import _DEFAULT_BUDGETS, _evaluate_rule, _load_budgets
        from roam.commands.metrics_history import collect_metrics
        from roam.graph.diff import find_before_snapshot
    except ImportError:
        return {"rules_checked": 0, "passed": 0, "failed": 0, "skipped": 0, "rules": []}

    cfg = root / ".roam" / "budget.yaml"
    if not cfg.exists():
        cfg = root / ".roam" / "budget.yml"

    budgets = _load_budgets(cfg) if cfg.exists() else []
    if not budgets:
        budgets = list(_DEFAULT_BUDGETS)

    before = find_before_snapshot(conn, root)
    after = collect_metrics(conn)

    if not before:
        return {
            "rules_checked": len(budgets),
            "passed": 0,
            "failed": 0,
            "skipped": len(budgets),
            "rules": [{"name": b.get("name", "unnamed"), "status": "SKIP"} for b in budgets],
        }

    results = [_evaluate_rule(b, before, after) for b in budgets]
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")

    return {
        "rules_checked": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "rules": results,
    }


def _collect_fitness_evidence(conn, file_map, root):
    """Evaluate fitness rules scoped to changed files.

    Returns {rules, violations}.
    """
    try:
        from roam.commands.cmd_diff import _collect_fitness_violations

        rule_results, violations = _collect_fitness_violations(conn, file_map, root)
        return {"rules": rule_results, "violations": violations[:50]}
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_attest:fitness_violations", _exc)
        return {"rules": [], "violations": []}


def _collect_effects_evidence(conn, file_map):
    """Gather effects for symbols in changed files.

    Returns list of {symbol, file, direct_effects, transitive_effects}.
    """
    effects_list = []
    try:
        for path, fid in file_map.items():
            rows = conn.execute(
                "SELECT s.name, s.kind, se.effect_type, se.source "
                "FROM symbol_effects se "
                "JOIN symbols s ON se.symbol_id = s.id "
                "WHERE s.file_id = ? "
                "ORDER BY s.name, se.effect_type",
                (fid,),
            ).fetchall()

            # Group by symbol
            sym_effects: dict[str, dict] = {}
            for r in rows:
                name = r["name"]
                if name not in sym_effects:
                    sym_effects[name] = {
                        "symbol": name,
                        "kind": r["kind"],
                        "file": path,
                        "direct_effects": [],
                        "transitive_effects": [],
                    }
                if r["source"] == "direct":
                    sym_effects[name]["direct_effects"].append(r["effect_type"])
                else:
                    sym_effects[name]["transitive_effects"].append(r["effect_type"])

            effects_list.extend(sym_effects.values())
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_attest:effects_query", _exc)

    return effects_list


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------


def _content_hash(evidence: dict) -> str:
    """Compute SHA-256 of the evidence payload for tamper detection."""
    canonical = json.dumps(evidence, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Git range helpers
# ---------------------------------------------------------------------------


def _resolve_git_range(root, commit_range):
    """Parse a commit range like 'main..HEAD' into (base_ref, head_ref).

    Returns (base_ref, head_ref) tuple. For single refs, returns (ref, 'HEAD').
    """
    if commit_range and ".." in commit_range:
        parts = commit_range.split("..", 1)
        return parts[0], parts[1]
    return commit_range or "HEAD", "HEAD"


def _get_git_hashes(root, base_ref, head_ref):
    """Resolve refs to short hashes."""
    hashes = {}
    for label, ref in [("base", base_ref), ("head", head_ref)]:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", ref],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0:
                hashes[label] = result.stdout.strip()
        except Exception:
            pass
    return hashes


# ---------------------------------------------------------------------------
# Verdict computation
# ---------------------------------------------------------------------------


def _compute_verdict(risk, breaking_data, fitness_data, budget_data):
    """Determine safe_to_merge, conditions, and warnings.

    Returns {safe_to_merge, conditions, warnings}.
    """
    conditions = []
    warnings = []
    safe = True

    # Breaking changes
    total_breaking = (
        len(breaking_data.get("removed", []))
        + len(breaking_data.get("signature_changed", []))
        + len(breaking_data.get("renamed", []))
    )
    if total_breaking > 0:
        warnings.append(f"{total_breaking} breaking changes detected")

    # Risk level
    if risk and risk.get("level") in ("HIGH", "CRITICAL"):
        warnings.append(f"risk level is {risk['level']} ({risk['score']}/100)")

    # Budget violations
    budget_failed = budget_data.get("failed", 0)
    if budget_failed > 0:
        safe = False
        conditions.append(f"{budget_failed} budget(s) exceeded")

    # Fitness violations
    fitness_violations = len(fitness_data.get("violations", []))
    if fitness_violations > 0:
        warnings.append(f"{fitness_violations} fitness violations in changed files")

    # Tests needed
    conditions.append("run selected tests")

    return {
        "safe_to_merge": safe,
        "conditions": conditions,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------


def _format_markdown(attestation, evidence, verdict):
    """Format attestation as GitHub/GitLab compatible markdown."""
    lines = []
    lines.append("## Roam Attestation")
    lines.append("")

    # Verdict banner
    safe_icon = "PASS" if verdict["safe_to_merge"] else "FAIL"
    lines.append(f"**Verdict: {safe_icon}**")
    if verdict["conditions"]:
        lines.append("")
        lines.append("Conditions:")
        for c in verdict["conditions"]:
            lines.append(f"- {c}")
    if verdict["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for w in verdict["warnings"]:
            lines.append(f"- {w}")

    lines.append("")

    # Risk
    risk = evidence.get("risk")
    if risk:
        lines.append(f"### Risk: {risk['level']} ({risk['score']}/100)")
        lines.append("")

    # Blast radius
    br = evidence.get("blast_radius", {})
    lines.append("### Blast Radius")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Changed files | {br.get('changed_files', 0)} |")
    lines.append(f"| Affected symbols | {br.get('affected_symbols', 0)} |")
    lines.append(f"| Affected files | {br.get('affected_files', 0)} |")
    lines.append("")

    # Breaking changes
    bc = evidence.get("breaking_changes", {})
    removed = len(bc.get("removed", []))
    sig_changed = len(bc.get("signature_changed", []))
    renamed = len(bc.get("renamed", []))
    total_bc = removed + sig_changed + renamed
    if total_bc > 0:
        lines.append(f"### Breaking Changes ({total_bc})")
        lines.append("")
        if removed:
            lines.append(f"- {removed} removed")
        if sig_changed:
            lines.append(f"- {sig_changed} signature changed")
        if renamed:
            lines.append(f"- {renamed} renamed")
        lines.append("")
    else:
        lines.append("### Breaking Changes: None")
        lines.append("")

    # Budget
    bg = evidence.get("budget", {})
    if bg.get("rules_checked", 0) > 0:
        lines.append(f"### Budget ({bg['passed']} passed, {bg['failed']} failed, {bg['skipped']} skipped)")
        lines.append("")
        for r in bg.get("rules", []):
            status = r.get("status", "?")
            name = r.get("name", "unnamed")
            lines.append(f"- [{status}] {name}")
        lines.append("")

    # Tests
    tests = evidence.get("tests", {})
    if tests.get("selected", 0) > 0:
        lines.append(f"### Affected Tests ({tests['selected']})")
        lines.append("")
        lines.append(
            f"- {tests.get('direct', 0)} direct, {tests.get('transitive', 0)} transitive, {tests.get('colocated', 0)} colocated"
        )
        cmd = tests.get("command", "")
        if cmd:
            lines.append(f"- `{cmd}`")
        lines.append("")

    # Effects
    effects = evidence.get("effects", [])
    if effects:
        lines.append(f"### Effects ({len(effects)} symbols)")
        lines.append("")
        for e in effects[:20]:
            all_eff = e.get("direct_effects", []) + e.get("transitive_effects", [])
            lines.append(f"- {e['symbol']}: {', '.join(all_eff)}")
        lines.append("")

    # Attestation metadata
    lines.append("---")
    lines.append(
        f"*Generated by roam-code v{attestation.get('tool_version', '?')} at {attestation.get('timestamp', '?')}*"
    )
    if attestation.get("content_hash"):
        lines.append(f"*Hash: `{attestation['content_hash']}`*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="attest",
    category="workflow",
    summary="Generate a proof-carrying PR attestation",
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core", "compliance"),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=False,
    requires_index=True,
)
@click.command("attest")
@click.argument("commit_range", required=False, default=None)
@click.option("--staged", is_flag=True, help="Attest staged changes only.")
@click.option(
    "--format",
    "output_format",
    default="text",
    type=click.Choice(["text", "markdown", "json"]),
    help="Output format (default: text).",
)
@click.option("--sign", is_flag=True, help="Include SHA-256 content hash for tamper detection.")
@click.option("--output", "output_file", default=None, help="Write attestation to file.")
@click.pass_context
def attest(ctx, commit_range, staged, output_format, sign, output_file):
    """Generate a proof-carrying PR attestation.

    Bundles blast radius, risk score, breaking changes, fitness violations,
    budget consumed, affected tests, and effects into a single verifiable
    evidence artifact. Use --format markdown for PR comments.

    Unlike ``pr-risk`` (which produces a single composite risk score),
    this command assembles multiple independent evidence dimensions into
    one auditable artifact.

    The JSON envelope emits ``summary.risk_level_canonical`` +
    ``summary.risk_rank`` on the canonical W631 risk-LEVEL axis
    (``critical``/``high``/``medium``/``low``) so cross-command floor
    comparators against ``pr-risk`` / ``impact`` / ``critique`` /
    ``pr-bundle`` work on a single floor. Missing risk safe-floors to
    ``low`` (the W531 CI-safety lesson); the verdict line terminates on
    ``(risk_level <canonical>)`` per LAW 6 (standalone-parseable).

    \b
    Examples:
      roam attest                          # attest uncommitted changes
      roam attest --staged                 # attest staged changes
      roam attest main..HEAD               # attest full branch
      roam attest --format markdown        # PR comment format
      roam attest --sign --output a.json   # signed JSON artifact

    See also ``cga`` (CodeGraph attestation), ``pr-risk`` (single
    composite risk score), and ``audit-trail-verify`` (verify a
    previously-signed artifact).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    # --format json is equivalent to --json
    if output_format == "json":
        json_mode = True
    ensure_index()
    root = find_project_root()

    # ── W607-AD substrate-boundary plumbing ──────────────────────────
    #
    # cmd_attest is the proof-carrying PR attestation aggregator: it
    # composes blast-radius / risk / breaking / fitness / budget / tests
    # / effects evidence into a single auditable artifact. Each collector
    # is a substrate boundary that can raise -- e.g. a malformed graph
    # row inside ``_collect_blast_radius`` or a missing optional import
    # propagating through ``_collect_risk`` previously crashed the whole
    # attestation build. W607-AD wraps each collector + the
    # ``get_changed_files`` shared-helper boundary with ``_run_check_ad``
    # so a raise becomes a structured
    # ``attest_<phase>_failed:<exc_class>:<detail>`` marker on
    # ``_w607ad_warnings_out`` -- the envelope still emits cleanly with
    # whatever signal the remaining substrates produced.
    #
    # cmd_attest sits on the W805 cross-artifact consistency family
    # (CGA / VSA / Rekor pipeline): the W607-AD markers fire AT RUNTIME
    # when an emission boundary raises, complementing the W805 xfail-
    # strict pins that catch structural inconsistency at the dataclass
    # level.
    #
    # Marker prefix discipline: every W607-AD substrate marker uses the
    # canonical ``attest_<phase>_failed:<exc_class>:<detail>`` shape.
    # The accumulator is intentionally distinct from the pre-existing
    # ``_attest_warnings_out`` bucket (W641-followup-D unknown-status
    # tracking) so the two axes don't entangle: unknown-status is a
    # data-shape disclosure (a risk.level couldn't be mapped to the
    # canonical W631 set), while W607-AD is a substrate-CALL disclosure
    # (a helper raised before producing its floor value). Both feed the
    # same envelope ``warnings_out`` field on emission; ``partial_success``
    # flips when EITHER bucket is non-empty.
    _w607ad_warnings_out: list[str] = []

    def _run_check_ad(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AD marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an ``attest_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607ad_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ad_warnings_out.append(f"attest_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-BT -- ADDITIVE aggregation-phase plumbing on top of the
    # W607-AD substrate-CALL markers. W607-AD already wrapped the 11
    # substrate-helper boundaries (get_changed_files / resolve_changed_to_db /
    # collect_blast_radius / collect_risk / collect_breaking /
    # collect_fitness / collect_budget / collect_tests / collect_effects /
    # content_hash / compute_verdict); W607-BT extends marker coverage to
    # the AGGREGATION-PHASE boundaries that W607-AD left unguarded:
    #
    #   - ``score_classify``       -- per-factor classification of the
    #                                 internal attest risk-LEVEL set
    #                                 (``LOW``/``MODERATE``/``HIGH``/
    #                                 ``CRITICAL``) via ``_attest_risk_level``
    #                                 -- mirror of cmd_diff's W607-BP
    #                                 severity_classify pattern with
    #                                 default=None driving the
    #                                 score_classification "unknown" sentinel
    #   - ``severity_normalize``   -- canonical W631 risk-LEVEL projection
    #                                 (``normalize_risk_level`` + ``risk_rank``)
    #                                 -- CRITICAL-PATH instrumentation: this
    #                                 is the only edit-loop command where
    #                                 the projection legitimately reaches
    #                                 ``critical`` (the composite-risk score
    #                                 >75/100 tier). Mirror of cmd_diff
    #                                 W607-BP / cmd_critique W607-BL but
    #                                 without saturation-at-high
    #   - ``compute_verdict``      -- augmented_verdict text build with the
    #                                 canonical risk_level suffix (LAW 6
    #                                 standalone-parse) via ``_make_verdict_str``
    #   - ``auto_log``             -- active-run ledger write (silent no-op
    #                                 if no run is active, but the underlying
    #                                 ``auto_log`` can still raise on HMAC
    #                                 chain misshape or filesystem failures)
    #   - ``serialize_envelope``   -- ``json_envelope("attest", ...)`` projection
    #                                 (downstream contract changes / shape
    #                                 regressions)
    #
    # cmd_attest is the proof-carrying PR attestation aggregator: it
    # composes blast-radius / risk / breaking / fitness / budget / tests /
    # effects evidence into a single auditable artifact. With W607-BT
    # landed, the full W631 risk-LEVEL vocabulary range is now dual-bucket
    # plumbed -- cmd_attest is the only command in the W607-* family that
    # legitimately reaches ``risk_level "critical"`` (the >75/100 tier of
    # ``_collect_risk``).
    #
    # Marker family ``attest_*`` -- same family as W607-AD (additive,
    # not a separate prefix). Empty bucket -> byte-identical envelope.
    _w607bt_warnings_out: list[str] = []

    def _run_check_bt(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-BT marker emission.

        Mirror of ``_run_check_ad`` shape (same ``attest_<phase>_failed:``
        marker family) but writes into ``_w607bt_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bt_warnings_out.append(f"attest_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # Resolve git range
    base_ref, head_ref = _resolve_git_range(root, commit_range)
    hashes = _get_git_hashes(root, base_ref, head_ref)

    def _changed_files_for_attest():
        return get_changed_files(root, staged=staged, commit_range=commit_range)

    changed = (
        _run_check_ad(
            "get_changed_files",
            _changed_files_for_attest,
            default=[],
        )
        or []
    )
    if not changed:
        # Pattern 1D / Pattern 2: no-changes is a degraded-resolution path,
        # NOT a fully-assessed "safe to merge" verdict. An agent reading
        # ``summary.safe_to_merge`` must not see ``True`` when the underlying
        # check never ran (there was nothing to assess). Disclose state +
        # partial_success explicitly so the verdict and the field agree.
        label = commit_range or ("staged" if staged else "uncommitted")
        # W641-followup-D — degraded-resolution path still emits the
        # canonical risk-LEVEL fields unconditionally. Empty changeset →
        # canonical ``low`` floor (W531 CI-safety: a no-changes path
        # must NOT promote into a CI-failing rank). Agents downstream
        # can call ``risk_rank(summary["risk_level_canonical"])`` without
        # None-handling.
        _empty_risk_level_canonical = "low"
        _empty_risk_rank = risk_rank(_empty_risk_level_canonical)
        _attest_empty_envelope = json_envelope(
            "attest",
            summary={
                "verdict": (f"no changes found for {label} (risk_level {_empty_risk_level_canonical})"),
                "state": "no_changes",
                "partial_success": True,
                "safe_to_merge": None,
                "risk_level_canonical": _empty_risk_level_canonical,
                "risk_rank": _empty_risk_rank,
            },
            risk_level_canonical=_empty_risk_level_canonical,
            risk_rank=_empty_risk_rank,
        )
        auto_log(_attest_empty_envelope, action="attest", target=label, repo_root=root)
        if json_mode or output_format == "json":
            click.echo(to_json(_attest_empty_envelope))
        else:
            click.echo(f"No changes found for {label}.")
        return

    with open_db(readonly=True) as conn:
        file_map = _run_check_ad("resolve_changed_to_db", resolve_changed_to_db, conn, changed, default={}) or {}

        if not file_map:
            # Pattern-1C / Pattern-1D: emit a structured envelope on the
            # degraded-resolution path so JSON consumers see a non-empty
            # response (not empty stdout) and the verdict discloses that
            # none of the changed files resolved against the index. Mirrors
            # the empty-changeset envelope above at L686-694.
            label = commit_range or ("staged" if staged else "uncommitted")
            # W641-followup-D — unresolved path still emits canonical
            # risk-LEVEL fields. The W531 CI-safety floor applies even
            # on a degraded-resolution branch: a typo'd / absent label
            # must NOT promote a finding into a CI-failing rank. The
            # underlying risk was never assessed (the resolver failed),
            # so the floor lands on ``low`` — the verdict + safe_to_merge
            # carry the actionable disclosure.
            _unresolved_risk_level_canonical = "low"
            _unresolved_risk_rank = risk_rank(_unresolved_risk_level_canonical)
            _attest_unresolved_envelope = json_envelope(
                "attest",
                summary={
                    "verdict": (
                        f"{len(changed)} changed files unresolved against the index "
                        f"({label}); run `roam index` then re-attest "
                        f"(risk_level {_unresolved_risk_level_canonical})"
                    ),
                    "safe_to_merge": False,
                    "partial_success": True,
                    "resolution": "unresolved",
                    "unresolved_file_count": len(changed),
                    "risk_level_canonical": _unresolved_risk_level_canonical,
                    "risk_rank": _unresolved_risk_rank,
                },
                risk_level_canonical=_unresolved_risk_level_canonical,
                risk_rank=_unresolved_risk_rank,
                unresolved_files=sorted(changed),
            )
            auto_log(_attest_unresolved_envelope, action="attest", target=label, repo_root=root)
            if json_mode or output_format == "json":
                click.echo(to_json(_attest_unresolved_envelope))
            else:
                click.echo(f"{len(changed)} changed files not found in index ({label}). Run `roam index` first.")
            return

        # ── Collect all evidence ──────────────────────────────────────
        # W607-AD wraps every collector. Each collector's documented
        # empty-floor (matching its happy-path return shape) is passed
        # as ``default=`` so a raise degrades cleanly and the envelope
        # still emits whatever signal the remaining collectors produced.

        # 1. Blast radius
        blast = _run_check_ad(
            "collect_blast_radius",
            _collect_blast_radius,
            conn,
            file_map,
            default={
                "changed_files": len(file_map),
                "affected_symbols": 0,
                "affected_files": 0,
                "per_file": [],
            },
        )
        sym_by_file = blast.pop("sym_by_file", {})

        # 2. Risk score
        risk = _run_check_ad(
            "collect_risk",
            _collect_risk,
            conn,
            root,
            file_map,
            staged,
            commit_range,
            default=None,
        )

        # 3. Breaking changes
        breaking_ref = base_ref if commit_range else "HEAD"
        breaking = _run_check_ad(
            "collect_breaking",
            _collect_breaking,
            conn,
            root,
            breaking_ref,
            default={"removed": [], "signature_changed": [], "renamed": []},
        )

        # 4. Fitness violations
        fitness = _run_check_ad(
            "collect_fitness",
            _collect_fitness_evidence,
            conn,
            file_map,
            root,
            default={"rules": [], "violations": []},
        )

        # 5. Budget consumed
        budget = _run_check_ad(
            "collect_budget",
            _collect_budget_evidence,
            conn,
            root,
            default={"rules_checked": 0, "passed": 0, "failed": 0, "skipped": 0, "rules": []},
        )

        # 6. Affected tests
        tests = _run_check_ad(
            "collect_tests",
            _collect_affected_tests_evidence,
            conn,
            sym_by_file,
            default={"selected": 0, "direct": 0, "transitive": 0, "colocated": 0, "command": "", "tests": []},
        )

        # 7. Effects
        effects = _run_check_ad(
            "collect_effects",
            _collect_effects_evidence,
            conn,
            file_map,
            default=[],
        )

        # ── Build attestation ─────────────────────────────────────────

        # Tool version
        try:
            import roam

            tool_version = roam.__version__
        except Exception:
            tool_version = "unknown"

        now = datetime.now(timezone.utc)

        evidence = {
            "blast_radius": blast,
            "risk": risk,
            "breaking_changes": breaking,
            "fitness": fitness,
            "budget": budget,
            "tests": tests,
            "effects": effects,
        }

        attestation = {
            "version": "1.0",
            "tool": "roam-code",
            "tool_version": tool_version,
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_range": f"{hashes.get('base', base_ref)}..{hashes.get('head', head_ref)}",
        }

        if sign:
            # W607-AD: signing boundary -- the CRYPTOGRAPHIC core. A
            # raise here previously crashed the whole attestation build
            # (e.g. unserialisable evidence dict).
            def _stamp_content_hash() -> None:
                attestation["content_hash"] = _content_hash(evidence)

            _run_check_ad("content_hash", _stamp_content_hash, default=None)

        verdict = _run_check_ad(
            "compute_verdict",
            _compute_verdict,
            risk,
            breaking,
            fitness,
            budget,
            default={"safe_to_merge": False, "conditions": [], "warnings": []},
        )

        # W641-followup-D — canonical W631 risk-LEVEL projection from the
        # internal attest risk dict. Conservative-on-critical: unlike
        # critique / impact which saturate at ``high``, attest's
        # composite-risk score IS allowed to reach ``critical`` (the
        # >75/100 tier of ``_collect_risk``). Missing risk (networkx
        # import failed OR degraded path) safe-floors to ``low``.
        # Unknown ``risk.level`` strings accumulate a marker on
        # ``_attest_warnings_out`` so Pattern-2 silent-fallback stays
        # loud (mirrors W641-followup-B critique).
        _attest_warnings_out: list[str] = []
        # W607-BT -- score_classify boundary. Wraps the ``_attest_risk_level``
        # projection so a future closed-enum vocabulary refactor surfaces a
        # marker rather than crashing the envelope. Floors to ``None`` so
        # the score_classification "unknown" sentinel disambiguates a
        # degraded outcome from a real ``"low"`` classification (mirror of
        # cmd_diff W607-BP severity_classify pattern).
        _bt_score_probe = _run_check_bt(
            "score_classify",
            _attest_risk_level,
            risk,
            warnings_out=_attest_warnings_out,
            default=None,
        )
        # When the BT probe raised (None floor), mark classification unknown.
        # Clean path -> classification is "classified". This sentinel rides
        # the summary block below.
        _score_classification_state = "unknown" if _bt_score_probe is None else "classified"
        # When the W607-BT probe returned a clean tier we reuse it. When it
        # raised (None floor), do NOT re-call ``_attest_risk_level`` -- the
        # same call would re-raise (W978 first-hypothesis check: don't
        # re-trip the same boundary that just raised). Safe-floor to "low"
        # directly, since the W641-followup-D unknown-status bucket couldn't
        # be populated by the helper on a raise anyway.
        _attest_domain_level = _bt_score_probe if _bt_score_probe is not None else "low"
        # W607-BT -- severity_normalize boundary. Wraps the canonical W631
        # ``normalize_risk_level`` + ``risk_rank`` projections so a future
        # signature change / closed-enum vocabulary drift surfaces a marker
        # rather than crashing the envelope. CRITICAL-PATH instrumentation:
        # this is the only edit-loop command where the projection
        # legitimately reaches ``critical`` (the composite-risk score
        # >75/100 tier). Floors to ``"low"`` / rank ``1`` so downstream
        # comparators stay non-null.
        risk_level_canonical = _run_check_bt(
            "severity_normalize",
            lambda level: normalize_risk_level(level) or "low",
            _attest_domain_level,
            default="low",
        )
        risk_rank_int = _run_check_bt(
            "severity_normalize",
            risk_rank,
            risk_level_canonical,
            default=1,
        )

        # W607-BT -- compute_verdict boundary. Wraps the canonical
        # ``_make_verdict_str`` build so a future format-spec regression on
        # the components (e.g. non-string risk_level_canonical from a
        # vocabulary refactor) surfaces a marker rather than crashing the
        # envelope. Floor must NOT re-format ``risk_level_canonical`` --
        # the same value that tripped the closure (e.g. a __format__-
        # raising sentinel under test) would re-raise inside the default
        # f-string. Use a literal "low" floor instead (LAW 6 still holds:
        # the line works standalone; the W631 floor is "low"). Mirror of
        # cmd_diff W607-BP compute_verdict discipline (W978 first-hypothesis
        # check: the canonical floor MUST NOT re-interpolate the same value
        # that raised on the BadLevel sentinel test).
        _attest_verdict_str = _run_check_bt(
            "compute_verdict",
            _make_verdict_str,
            verdict,
            risk,
            risk_level_canonical,
            default="attest completed (risk_level low)",
        )

        summary_block = {
            "verdict": _attest_verdict_str,
            "safe_to_merge": verdict["safe_to_merge"],
            "risk_score": risk["score"] if risk else None,
            "risk_level": risk["level"] if risk else None,
            # W641-followup-D — canonical W631 risk-LEVEL projection +
            # integer rank. The internal ``risk_level`` field above is
            # the domain-specific 4-tier (``LOW``/``MODERATE``/``HIGH``/
            # ``CRITICAL``); ``risk_level_canonical`` is the W631 closed-
            # enum mirror so cross-command floor comparators
            # (``risk_rank(summary.risk_level_canonical) >= 3`` to gate
            # on high-or-worse) work without re-deriving the rank
            # vocabulary at the call site (Pattern-3a).
            "risk_level_canonical": risk_level_canonical,
            "risk_rank": risk_rank_int,
            # W607-BT -- SCORE-CLASSIFY DEGRADATION sentinel. When the
            # ``score_classify`` boundary raises (and the classify result
            # floors to ``None``), surface
            # ``score_classification: "unknown"`` so the agent sees the
            # degraded outcome alongside the canonical floor (``"low"``)
            # rather than mistaking the floor for a real classification.
            # Clean path -> ``"classified"``. Mirror of cmd_diff's
            # ``severity_classification`` / cmd_impact's
            # ``risk_classification`` sentinel.
            "score_classification": _score_classification_state,
            "breaking_changes": (
                len(breaking.get("removed", []))
                + len(breaking.get("signature_changed", []))
                + len(breaking.get("renamed", []))
            ),
            "budget_failed": budget.get("failed", 0),
            "affected_tests": tests.get("selected", 0),
            "effects_count": len(effects),
        }
        # W641-followup-D — record any unknown-status drops on the
        # summary so Pattern-2 silent-fallback stays visible. Non-empty
        # ``warnings_out`` flips ``partial_success`` (mirrors W989 pr-
        # risk + W918 alerts + W641-followup-B critique discipline) so
        # a downstream consumer reading ``partial_success`` alone sees
        # the degradation.
        #
        # W607-AD — substrate-CALL markers ride the same ``warnings_out``
        # channel but accumulate in a DIFFERENT bucket
        # (``_w607ad_warnings_out``) so the two axes (unknown-status data
        # shape vs. helper-raised substrate boundary) don't conflate at
        # the call site. They merge into a single ``warnings_out`` list
        # on emission; the marker PREFIX disambiguates them downstream
        # (``attest_unknown_status:*`` vs. ``attest_<phase>_failed:*``).
        # ``partial_success`` flips when EITHER bucket is non-empty --
        # consumers reading ``partial_success`` alone need not
        # distinguish the two flavours.
        #
        # W607-BT -- ADDITIVE aggregation-phase markers join the same
        # combined channel: ``_attest_warnings_out`` (unknown-status) +
        # ``_w607ad_warnings_out`` (substrate-CALL) +
        # ``_w607bt_warnings_out`` (aggregation-phase). All three share
        # the ``attest_*`` family per the marker-prefix discipline test;
        # the additive bucket stays distinguishable in tests + audits via
        # its phase names (``score_classify`` / ``severity_normalize`` /
        # ``compute_verdict`` / ``auto_log`` / ``serialize_envelope``).
        _combined_warnings_out: list[str] = (
            list(_attest_warnings_out) + list(_w607ad_warnings_out) + list(_w607bt_warnings_out)
        )
        if _combined_warnings_out:
            summary_block["warnings_out"] = list(_combined_warnings_out)
            summary_block["partial_success"] = True

        # ── Envelope (built unconditionally so we can auto-log it) ───
        _envelope_kwargs: dict = dict(
            summary=summary_block,
            attestation=attestation,
            evidence=evidence,
            verdict=verdict,
            # W641-followup-D — top-level mirrors of
            # summary.risk_level_canonical / summary.risk_rank so
            # consumers that read the top-level envelope head (without
            # descending into ``summary``) see the canonical bucket.
            # Mirror of the W641-followup-A cmd_impact + W641-followup-B
            # cmd_critique contract.
            risk_level_canonical=risk_level_canonical,
            risk_rank=risk_rank_int,
        )
        # W607-AD / W607-BT — top-level mirror of summary.warnings_out so
        # consumers that read the top-level envelope directly (without
        # descending into ``summary``) see the marker channel. Mirror
        # parity with W607-Y cmd_critique (and the rest of the W607 family).
        if _combined_warnings_out:
            _envelope_kwargs["warnings_out"] = list(_combined_warnings_out)
            _envelope_kwargs["partial_success"] = True

        # W607-BT -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("attest", ...)`` would otherwise crash
        # AFTER all substrate + aggregation signals were already gathered.
        # Floor to a minimal envelope stub so consumers still receive a
        # parseable JSON object with the marker attached + the canonical
        # command name. Mirror of cmd_diff's W607-BP serialize_envelope
        # floor pattern.
        _envelope_floor: dict = {
            "command": "attest",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": _attest_verdict_str,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        attest_envelope = _run_check_bt(
            "serialize_envelope",
            json_envelope,
            "attest",
            default=_envelope_floor,
            **_envelope_kwargs,
        )
        # W607-BT -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``attest_serialize_envelope_failed:`` marker was appended to
        # ``_w607bt_warnings_out`` and the floor stub carries only the old
        # combined list. Rebuild the floor stub's warnings_out so the new
        # marker reaches the JSON output. Clean path -> envelope is the
        # real json_envelope return value, no rebuild needed.
        if attest_envelope is _envelope_floor and _w607bt_warnings_out:
            _combined_warnings_out = (
                list(_attest_warnings_out) + list(_w607ad_warnings_out) + list(_w607bt_warnings_out)
            )
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            attest_envelope = _envelope_floor

        _attest_target = commit_range or ("staged" if staged else "uncommitted")
        # W607-BT -- auto_log boundary. Silent no-op if no active run; the
        # wrap surfaces HMAC chain-misshape / filesystem failures as
        # ``attest_auto_log_failed:...`` markers instead of crashing the
        # envelope after it was already built. Mirror of cmd_diff's
        # W607-BP auto_log pattern.
        _run_check_bt(
            "auto_log",
            auto_log,
            attest_envelope,
            action="attest",
            target=_attest_target,
            repo_root=root,
            default=None,
        )
        # W607-BT -- if ``auto_log`` raised, rebuild the envelope so the
        # marker reaches the JSON output. Empty bucket (clean auto_log)
        # -> envelope stays byte-identical to the version already built
        # above.
        if _w607bt_warnings_out and not any(
            m.startswith("attest_auto_log_failed:") for m in (summary_block.get("warnings_out") or [])
        ):
            _combined_warnings_out = (
                list(_attest_warnings_out) + list(_w607ad_warnings_out) + list(_w607bt_warnings_out)
            )
            summary_block["warnings_out"] = list(_combined_warnings_out)
            summary_block["partial_success"] = True
            _envelope_kwargs["summary"] = summary_block
            _envelope_kwargs["warnings_out"] = list(_combined_warnings_out)
            _envelope_kwargs["partial_success"] = True
            attest_envelope = _run_check_bt(
                "serialize_envelope",
                json_envelope,
                "attest",
                default=_envelope_floor,
                **_envelope_kwargs,
            )

        # ── Output ────────────────────────────────────────────────────

        if output_format == "markdown":
            output = _format_markdown(attestation, evidence, verdict)
            if output_file:
                # Atomic write: attestations are auditable artifacts —
                # a torn file mid-write defeats downstream verification.
                # Mirrors the `atomic_write_text` discipline in cmd_cga.py
                # (the R28 substrate's `unsafe_mutation` guard).
                from roam.atomic_io import atomic_write_text

                atomic_write_text(Path(output_file), output)
                click.echo(f"Attestation written to {output_file}")
            else:
                click.echo(output)
            return

        if json_mode or output_format == "json":
            output = to_json(attest_envelope)
            if output_file:
                # Atomic write — see markdown branch above.
                from roam.atomic_io import atomic_write_text

                atomic_write_text(Path(output_file), output)
                click.echo(f"Attestation written to {output_file}")
            else:
                click.echo(output)
            return

        # ── Text output ───────────────────────────────────────────────
        _emit_text(
            attestation,
            evidence,
            verdict,
            risk,
            blast,
            breaking,
            fitness,
            budget,
            tests,
            effects,
            output_file,
        )


# ---------------------------------------------------------------------------
# Text output helpers
# ---------------------------------------------------------------------------


def _make_verdict_str(verdict, risk, risk_level_canonical: str = "low"):
    """Build a short verdict string.

    LAW 6 (compression forces domain neutrality): the verdict line must
    work without any other envelope field. W641-followup-D augments the
    string with a closed-enum ``(risk_level <canonical>)`` parenthesis
    so a consumer parsing only the verdict string sees the canonical
    W631 risk_level directly. *risk_level_canonical* must already be a
    member of :data:`roam.output.risk.RISK_LEVELS`.
    """
    safe = "safe to merge" if verdict["safe_to_merge"] else "NOT safe to merge"
    risk_str = f" (risk: {risk['level']} {risk['score']}/100)" if risk else ""
    return f"{safe}{risk_str} (risk_level {risk_level_canonical})"


def _emit_text(
    attestation,
    evidence,
    verdict,
    risk,
    blast,
    breaking,
    fitness,
    budget,
    tests,
    effects,
    output_file,
):
    """Emit plain text attestation."""
    lines = []

    # Verdict line
    safe = "SAFE TO MERGE" if verdict["safe_to_merge"] else "NOT SAFE TO MERGE"
    lines.append(f"VERDICT: {safe}")
    if verdict["conditions"]:
        lines.append(f"  Conditions: {'; '.join(verdict['conditions'])}")
    if verdict["warnings"]:
        lines.append(f"  Warnings: {'; '.join(verdict['warnings'])}")
    lines.append("")

    # Risk
    if risk:
        lines.append(f"RISK: {risk['level']} ({risk['score']}/100)")
        lines.append("")

    # Blast radius
    lines.append("BLAST RADIUS:")
    lines.append(f"  Changed files:    {blast.get('changed_files', 0)}")
    lines.append(f"  Affected symbols: {blast.get('affected_symbols', 0)}")
    lines.append(f"  Affected files:   {blast.get('affected_files', 0)}")
    lines.append("")

    # Breaking changes
    removed = len(breaking.get("removed", []))
    sig_changed = len(breaking.get("signature_changed", []))
    renamed = len(breaking.get("renamed", []))
    total_bc = removed + sig_changed + renamed
    if total_bc > 0:
        lines.append(f"BREAKING CHANGES ({total_bc}):")
        if removed:
            lines.append(f"  {removed} removed")
            for r in breaking["removed"][:5]:
                lines.append(f"    {abbrev_kind(r.get('kind', ''))} {r['name']}  {r.get('file', '')}")
        if sig_changed:
            lines.append(f"  {sig_changed} signature changed")
        if renamed:
            lines.append(f"  {renamed} renamed")
        lines.append("")
    else:
        lines.append("BREAKING CHANGES: none")
        lines.append("")

    # Budget
    if budget.get("rules_checked", 0) > 0:
        lines.append(f"BUDGET ({budget['passed']} pass, {budget['failed']} fail, {budget['skipped']} skip):")
        for r in budget.get("rules", []):
            status = r.get("status", "?")
            name = r.get("name", "unnamed")
            detail = ""
            if r.get("delta") is not None:
                detail = f"  (delta: {r['delta']}, {r.get('budget', '')})"
            lines.append(f"  [{status}] {name}{detail}")
        lines.append("")

    # Fitness
    fitness_v = fitness.get("violations", [])
    if fitness_v:
        lines.append(f"FITNESS VIOLATIONS ({len(fitness_v)}):")
        for v in fitness_v[:10]:
            lines.append(f"  {v.get('rule', '?')}: {v.get('message', '')}")
        lines.append("")

    # Affected tests
    if tests.get("selected", 0) > 0:
        lines.append(
            f"AFFECTED TESTS ({tests['selected']}: "
            f"{tests.get('direct', 0)} direct, "
            f"{tests.get('transitive', 0)} transitive, "
            f"{tests.get('colocated', 0)} colocated):"
        )
        cmd = tests.get("command", "")
        if cmd:
            lines.append(f"  Run: {cmd}")
        lines.append("")
    else:
        lines.append("AFFECTED TESTS: none found")
        lines.append("")

    # Effects
    if effects:
        lines.append(f"EFFECTS ({len(effects)} symbols):")
        for e in effects[:15]:
            all_eff = e.get("direct_effects", []) + e.get("transitive_effects", [])
            lines.append(f"  {e.get('symbol', '?')}: {', '.join(all_eff)}")
        lines.append("")

    # Attestation metadata
    lines.append("---")
    lines.append(
        f"Attested by roam-code v{attestation.get('tool_version', '?')} at {attestation.get('timestamp', '?')}"
    )
    if attestation.get("content_hash"):
        lines.append(f"Hash: {attestation['content_hash']}")
    git_range = attestation.get("git_range", "?")
    lines.append(f"Range: {git_range}")

    text = "\n".join(lines)

    if output_file:
        # Atomic write — see other attest output branches. Attestation
        # files are auditable artifacts; never leave a torn file behind.
        from roam.atomic_io import atomic_write_text

        atomic_write_text(Path(output_file), text)
        click.echo(f"Attestation written to {output_file}")
    else:
        click.echo(text)
