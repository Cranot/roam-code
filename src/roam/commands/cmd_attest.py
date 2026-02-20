"""Proof-carrying PR attestation — bundle all evidence into one artifact."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope, abbrev_kind
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import (
    get_changed_files,
    resolve_changed_to_db,
    is_test_file,
)


# ---------------------------------------------------------------------------
# Evidence collectors
# ---------------------------------------------------------------------------


def _collect_blast_radius(conn, file_map):
    """Compute blast radius for changed files.

    Returns {changed_files, affected_symbols, affected_files, per_file}.
    """
    try:
        from roam.graph.builder import build_symbol_graph
        import networkx as nx
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
        syms = conn.execute(
            "SELECT id, name, kind FROM symbols WHERE file_id = ?", (fid,)
        ).fetchall()
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
        from roam.commands.cmd_pr_risk import (
            _detect_author,
            _author_familiarity,
            _calibrated_hotspot_score,
            _author_count_risk,
        )
        from roam.commands.changed_files import is_low_risk_file
        from roam.commands.cmd_coupling import _compute_surprise
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers
        import networkx as nx
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
        syms = conn.execute(
            "SELECT id FROM symbols WHERE file_id = ?", (fid,)
        ).fetchall()
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
            "WHERE gfc.file_id = ?", (fid,),
        ).fetchall()
        if authors:
            author_counts.append(len(authors))
    bus_factor_risk = _author_count_risk(author_counts) if author_counts else 0.0

    # Test coverage
    source_files = [p for p in file_map if not is_test_file(p) and not is_low_risk_file(p)]
    covered_files = 0
    for path in source_files:
        fid = file_map[path]
        has_test = any(
            is_test_file(r["path"])
            for r in conn.execute(
                "SELECT f.path FROM file_edges fe "
                "JOIN files f ON fe.source_file_id = f.id "
                "WHERE fe.target_file_id = ?", (fid,),
            ).fetchall()
        )
        if has_test:
            covered_files += 1
    test_coverage = covered_files / len(source_files) if source_files else 0.0

    # Coupling
    coupling_score = 0.0
    if len(file_map) > 1:
        fids = list(file_map.values())
        ph = ",".join("?" for _ in fids)
        cross_edges = conn.execute(
            f"SELECT COUNT(*) FROM file_edges "
            f"WHERE source_file_id IN ({ph}) AND target_file_id IN ({ph})",
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
    import math
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
        no_risk *= (1 - max(0, min(f, 0.99)))
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
            _git_changed_files,
            _git_show,
            _extract_old_symbols,
            _get_current_symbols,
            _compare_file,
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
            {"file": t["file"], "symbol": t.get("symbol"), "kind": t["kind"],
             "hops": t.get("hops", 0)}
            for t in test_results[:50]
        ],
    }


def _collect_budget_evidence(conn, root):
    """Evaluate budget rules.

    Returns {rules_checked, passed, failed, skipped, rules}.
    """
    try:
        from roam.commands.cmd_budget import _load_budgets, _evaluate_rule, _DEFAULT_BUDGETS
        from roam.graph.diff import find_before_snapshot
        from roam.commands.metrics_history import collect_metrics
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
    except Exception:
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
    except Exception:
        pass

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
                cwd=str(root), capture_output=True, text=True,
                timeout=10, encoding="utf-8", errors="replace",
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
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
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
        lines.append(f"- {tests.get('direct', 0)} direct, {tests.get('transitive', 0)} transitive, {tests.get('colocated', 0)} colocated")
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
    lines.append(f"*Generated by roam-code v{attestation.get('tool_version', '?')} at {attestation.get('timestamp', '?')}*")
    if attestation.get("content_hash"):
        lines.append(f"*Hash: `{attestation['content_hash']}`*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("attest")
@click.argument("commit_range", required=False, default=None)
@click.option("--staged", is_flag=True, help="Attest staged changes only.")
@click.option("--format", "output_format", default="text",
              type=click.Choice(["text", "markdown", "json"]),
              help="Output format (default: text).")
@click.option("--sign", is_flag=True,
              help="Include SHA-256 content hash for tamper detection.")
@click.option("--output", "output_file", default=None,
              help="Write attestation to file.")
@click.pass_context
def attest(ctx, commit_range, staged, output_format, sign, output_file):
    """Generate a proof-carrying PR attestation.

    Bundles blast radius, risk score, breaking changes, fitness violations,
    budget consumed, affected tests, and effects into a single verifiable
    evidence artifact. Use --format markdown for PR comments.

    \\b
    Examples:
      roam attest                          # attest uncommitted changes
      roam attest --staged                 # attest staged changes
      roam attest main..HEAD               # attest full branch
      roam attest --format markdown        # PR comment format
      roam attest --sign --output a.json   # signed JSON artifact
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    # --format json is equivalent to --json
    if output_format == "json":
        json_mode = True
    ensure_index()
    root = find_project_root()

    # Resolve git range
    base_ref, head_ref = _resolve_git_range(root, commit_range)
    hashes = _get_git_hashes(root, base_ref, head_ref)

    changed = get_changed_files(root, staged=staged, commit_range=commit_range)
    if not changed:
        label = commit_range or ("staged" if staged else "uncommitted")
        if json_mode or output_format == "json":
            click.echo(to_json(json_envelope("attest",
                summary={"verdict": f"no changes found for {label}",
                         "safe_to_merge": True},
            )))
        else:
            click.echo(f"No changes found for {label}.")
        return

    with open_db(readonly=True) as conn:
        file_map = resolve_changed_to_db(conn, changed)

        if not file_map:
            click.echo("Changed files not found in index. Run `roam index` first.")
            return

        # ── Collect all evidence ──────────────────────────────────────

        # 1. Blast radius
        blast = _collect_blast_radius(conn, file_map)
        sym_by_file = blast.pop("sym_by_file", {})

        # 2. Risk score
        risk = _collect_risk(conn, root, file_map, staged, commit_range)

        # 3. Breaking changes
        breaking_ref = base_ref if commit_range else "HEAD"
        breaking = _collect_breaking(conn, root, breaking_ref)

        # 4. Fitness violations
        fitness = _collect_fitness_evidence(conn, file_map, root)

        # 5. Budget consumed
        budget = _collect_budget_evidence(conn, root)

        # 6. Affected tests
        tests = _collect_affected_tests_evidence(conn, sym_by_file)

        # 7. Effects
        effects = _collect_effects_evidence(conn, file_map)

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
            attestation["content_hash"] = _content_hash(evidence)

        verdict = _compute_verdict(risk, breaking, fitness, budget)

        # ── Output ────────────────────────────────────────────────────

        if output_format == "markdown":
            output = _format_markdown(attestation, evidence, verdict)
            if output_file:
                Path(output_file).write_text(output, encoding="utf-8")
                click.echo(f"Attestation written to {output_file}")
            else:
                click.echo(output)
            return

        if json_mode or output_format == "json":
            envelope = json_envelope("attest",
                summary={
                    "verdict": _make_verdict_str(verdict, risk),
                    "safe_to_merge": verdict["safe_to_merge"],
                    "risk_score": risk["score"] if risk else None,
                    "risk_level": risk["level"] if risk else None,
                    "breaking_changes": (
                        len(breaking.get("removed", []))
                        + len(breaking.get("signature_changed", []))
                        + len(breaking.get("renamed", []))
                    ),
                    "budget_failed": budget.get("failed", 0),
                    "affected_tests": tests.get("selected", 0),
                    "effects_count": len(effects),
                },
                attestation=attestation,
                evidence=evidence,
                verdict=verdict,
            )
            output = to_json(envelope)
            if output_file:
                Path(output_file).write_text(output, encoding="utf-8")
                click.echo(f"Attestation written to {output_file}")
            else:
                click.echo(output)
            return

        # ── Text output ───────────────────────────────────────────────
        _emit_text(attestation, evidence, verdict, risk, blast, breaking,
                    fitness, budget, tests, effects, output_file)


# ---------------------------------------------------------------------------
# Text output helpers
# ---------------------------------------------------------------------------


def _make_verdict_str(verdict, risk):
    """Build a short verdict string."""
    safe = "safe to merge" if verdict["safe_to_merge"] else "NOT safe to merge"
    risk_str = f" (risk: {risk['level']} {risk['score']}/100)" if risk else ""
    return f"{safe}{risk_str}"


def _emit_text(attestation, evidence, verdict, risk, blast, breaking,
               fitness, budget, tests, effects, output_file):
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
        lines.append(f"AFFECTED TESTS ({tests['selected']}: "
                      f"{tests.get('direct', 0)} direct, "
                      f"{tests.get('transitive', 0)} transitive, "
                      f"{tests.get('colocated', 0)} colocated):")
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
    lines.append(f"Attested by roam-code v{attestation.get('tool_version', '?')} "
                  f"at {attestation.get('timestamp', '?')}")
    if attestation.get("content_hash"):
        lines.append(f"Hash: {attestation['content_hash']}")
    git_range = attestation.get("git_range", "?")
    lines.append(f"Range: {git_range}")

    text = "\n".join(lines)

    if output_file:
        Path(output_file).write_text(text, encoding="utf-8")
        click.echo(f"Attestation written to {output_file}")
    else:
        click.echo(text)
