"""Compute risk score for pending changes."""

from __future__ import annotations

import math
import subprocess
import time

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import (
    get_changed_files, resolve_changed_to_db, is_test_file, is_low_risk_file,
)
from roam.commands.cmd_coupling import _compute_surprise


def _get_file_stat(root, path):
    """Get +/- line counts for a file."""
    cmd = ["git", "diff", "--numstat", "--", path]
    try:
        result = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True,
            timeout=10, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0, 0
        parts = result.stdout.strip().split("\t")
        if len(parts) >= 2:
            added = int(parts[0]) if parts[0] != "-" else 0
            removed = int(parts[1]) if parts[1] != "-" else 0
            return added, removed
    except Exception:
        pass
    return 0, 0


def _detect_author():
    """Auto-detect author name from git config. Returns None if undetectable."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _author_familiarity(conn, author, changed_files):
    """Calculate how familiar the author is with each changed file.

    familiarity(author, file) = sum(
        (lines_added + lines_removed) * exp(-0.005 * days_since)
        for each commit by author to file
    )
    normalized = author_familiarity / max(all_authors_familiarity_for_file)
    familiarity_risk = 1.0 - avg(normalized across changed files)

    Half-life: ~139 days (4.6 months).
    Returns: (risk_score 0-0.25, details_dict)
    """
    now = int(time.time())
    decay_rate = 0.005  # per day; half-life ~139 days

    normalized_scores = []
    file_details = []

    for path, fid in changed_files.items():
        if is_test_file(path) or is_low_risk_file(path):
            continue

        # Get all commits touching this file with per-author churn
        rows = conn.execute(
            "SELECT gc.author, gc.timestamp, gfc.lines_added, gfc.lines_removed "
            "FROM git_file_changes gfc "
            "JOIN git_commits gc ON gfc.commit_id = gc.id "
            "WHERE gfc.file_id = ?",
            (fid,),
        ).fetchall()

        if not rows:
            # No git history for this file — treat as unfamiliar
            normalized_scores.append(0.0)
            file_details.append({"file": path, "familiarity": 0.0})
            continue

        # Accumulate time-decayed churn per author
        author_familiarity = {}
        for r in rows:
            a = r["author"] or ""
            days_since = max(0, (now - (r["timestamp"] or 0)) / 86400)
            churn = (r["lines_added"] or 0) + (r["lines_removed"] or 0)
            weight = churn * math.exp(-decay_rate * days_since)
            author_familiarity[a] = author_familiarity.get(a, 0.0) + weight

        max_fam = max(author_familiarity.values()) if author_familiarity else 0.0
        my_fam = author_familiarity.get(author, 0.0)

        if max_fam > 0:
            norm = my_fam / max_fam
        else:
            norm = 0.0

        normalized_scores.append(norm)
        file_details.append({
            "file": path,
            "familiarity": round(norm, 3),
        })

    if not normalized_scores:
        return 0.0, {"avg_familiarity": 1.0, "files_assessed": 0, "files": []}

    avg_norm = sum(normalized_scores) / len(normalized_scores)
    familiar_count = sum(1 for s in normalized_scores if s >= 0.5)
    risk = (1.0 - avg_norm) * 0.25  # scale to 0-0.25

    details = {
        "avg_familiarity": round(avg_norm, 3),
        "files_assessed": len(normalized_scores),
        "files_familiar": familiar_count,
        "files": file_details,
    }
    return risk, details


def _minor_contributor_risk(conn, author, changed_files):
    """Check if author is a minor contributor to each changed file.

    Minor = author's churn < 5% of file's total_churn.
    Fraction of "minor" files * 0.15 = risk contribution.
    Returns: (risk_score 0-0.15, details_dict)
    """
    minor_count = 0
    assessed = 0
    file_details = []

    for path, fid in changed_files.items():
        if is_test_file(path) or is_low_risk_file(path):
            continue

        # Get total churn for this file
        fs_row = conn.execute(
            "SELECT total_churn FROM file_stats WHERE file_id = ?", (fid,),
        ).fetchone()
        total_churn = (fs_row["total_churn"] or 0) if fs_row else 0

        if total_churn == 0:
            # No churn data — can't assess, skip
            continue

        # Get author's churn on this file
        author_row = conn.execute(
            "SELECT COALESCE(SUM(gfc.lines_added), 0) + COALESCE(SUM(gfc.lines_removed), 0) AS churn "
            "FROM git_file_changes gfc "
            "JOIN git_commits gc ON gfc.commit_id = gc.id "
            "WHERE gfc.file_id = ? AND gc.author = ?",
            (fid, author),
        ).fetchone()
        author_churn = author_row["churn"] if author_row else 0

        assessed += 1
        is_minor = author_churn < (total_churn * 0.05)
        if is_minor:
            minor_count += 1

        file_details.append({
            "file": path,
            "author_churn": author_churn,
            "total_churn": total_churn,
            "pct": round(author_churn * 100 / total_churn, 1) if total_churn else 0,
            "is_minor": is_minor,
        })

    if assessed == 0:
        return 0.0, {"minor_files": 0, "files_assessed": 0, "files": []}

    minor_frac = minor_count / assessed
    risk = minor_frac * 0.15

    details = {
        "minor_files": minor_count,
        "files_assessed": assessed,
        "minor_fraction": round(minor_frac, 3),
        "files": file_details,
    }
    return risk, details


@click.command("pr-risk")
@click.argument('commit_range', required=False, default=None)
@click.option('--staged', is_flag=True, help='Analyze staged changes')
@click.option('--author', default=None, help='Author name (auto-detects via git config if omitted)')
@click.pass_context
def pr_risk(ctx, commit_range, staged, author):
    """Compute risk score for pending changes.

    Analyzes blast radius, hotspot churn, bus factor, test coverage,
    coupling, author familiarity, and minor-contributor status to
    produce a single 0-100 risk score.

    Pass a COMMIT_RANGE (e.g. HEAD~3..HEAD) for committed changes,
    or use --staged for staged changes. Default: unstaged changes.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    root = find_project_root()

    changed = get_changed_files(root, staged=staged, commit_range=commit_range)
    if not changed:
        label = commit_range or ("staged" if staged else "unstaged")
        if json_mode:
            click.echo(to_json({"label": label, "risk_score": 0,
                                "message": "No changes found"}))
        else:
            click.echo(f"No changes found for {label}.")
        return

    with open_db(readonly=True) as conn:
        # Map changed files to DB
        file_map = resolve_changed_to_db(conn, changed)

        if not file_map:
            if json_mode:
                click.echo(to_json({"risk_score": 0,
                                    "message": "Changed files not in index"}))
            else:
                click.echo("Changed files not found in index. Run `roam index` first.")
            return

        total_syms_repo = conn.execute(
            "SELECT COUNT(*) FROM symbols"
        ).fetchone()[0]

        # --- Resolve author for familiarity/minor-contributor factors ---
        resolved_author = author or _detect_author()

        # --- 1. Blast radius ---
        from roam.graph.builder import build_symbol_graph
        import networkx as nx

        G = build_symbol_graph(conn)
        RG = G.reverse()

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

        blast_pct = len(all_affected) * 100 / total_syms_repo if total_syms_repo else 0

        # --- 2. Hotspot score (file churn) ---
        hotspot_score = 0.0
        churn_data = {}
        for path, fid in file_map.items():
            row = conn.execute(
                "SELECT total_churn, commit_count FROM file_stats "
                "WHERE file_id = ?", (fid,)
            ).fetchone()
            if row:
                churn_data[path] = {
                    "churn": row["total_churn"],
                    "commits": row["commit_count"],
                }

        if churn_data:
            # Compare against repo median churn — exclude docs/config files
            code_churn = {p: d for p, d in churn_data.items() if not is_low_risk_file(p)}
            all_churn = conn.execute(
                "SELECT total_churn FROM file_stats ORDER BY total_churn"
            ).fetchall()
            if all_churn and code_churn:
                median_churn = all_churn[len(all_churn) // 2]["total_churn"]
                if median_churn > 0:
                    avg_changed = sum(d["churn"] for d in code_churn.values()) / len(code_churn)
                    hotspot_score = min(1.0, avg_changed / (median_churn * 3))

        # --- 3. Bus factor ---
        bus_factor_risk = 0.0
        bus_factors = []
        for path, fid in file_map.items():
            if is_test_file(path) or is_low_risk_file(path):
                continue
            authors = conn.execute(
                "SELECT DISTINCT gc.author FROM git_file_changes gfc "
                "JOIN git_commits gc ON gfc.commit_id = gc.id "
                "WHERE gfc.file_id = ?", (fid,)
            ).fetchall()
            if authors:
                bus_factors.append(len(authors))

        if bus_factors:
            min_bf = min(bus_factors)
            if min_bf == 1:
                bus_factor_risk = 1.0
            elif min_bf == 2:
                bus_factor_risk = 0.5
            else:
                bus_factor_risk = 0.0

        # --- 4. Test coverage ---
        test_coverage = 0.0
        source_files = [p for p in file_map
                        if not is_test_file(p) and not is_low_risk_file(p)]
        covered_files = 0
        for path in source_files:
            fid = file_map[path]
            # Check if any test file imports this file
            test_importer = conn.execute(
                "SELECT 1 FROM file_edges fe "
                "JOIN files f ON fe.source_file_id = f.id "
                "WHERE fe.target_file_id = ?",
                (fid,),
            ).fetchall()
            has_test = any(is_test_file(r["path"]) for r in conn.execute(
                "SELECT f.path FROM file_edges fe "
                "JOIN files f ON fe.source_file_id = f.id "
                "WHERE fe.target_file_id = ?", (fid,),
            ).fetchall())
            if has_test:
                covered_files += 1

        if source_files:
            test_coverage = covered_files / len(source_files)

        # --- 5. Coupling density ---
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

        # --- 6. Hypergraph novelty ---
        change_fids = list(file_map.values())
        novelty, closest_pattern, closest_sim = _compute_surprise(conn, change_fids)

        # --- 7. Structural spread (cluster + layer) ---
        cluster_ids = set()
        for fid in file_map.values():
            for r in conn.execute(
                "SELECT DISTINCT c.cluster_id FROM clusters c "
                "JOIN symbols s ON c.symbol_id = s.id "
                "WHERE s.file_id = ?", (fid,),
            ).fetchall():
                cluster_ids.add(r["cluster_id"])

        total_clusters = conn.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM clusters"
        ).fetchone()[0] or 1
        cluster_spread = len(cluster_ids) / total_clusters if total_clusters > 1 else 0

        # Layer spread
        from roam.graph.layers import detect_layers
        layer_map = detect_layers(G)
        touched_layers = set()
        if layer_map:
            for sym_id in changed_sym_ids:
                if sym_id in layer_map:
                    touched_layers.add(layer_map[sym_id])
        total_layers = (max(layer_map.values()) + 1) if layer_map else 1
        layer_spread = len(touched_layers) / total_layers if total_layers > 1 else 0

        # --- 8. Dead code check ---
        new_dead = []
        for path, fid in file_map.items():
            if is_test_file(path):
                continue
            exports = conn.execute(
                "SELECT s.name, s.kind FROM symbols s "
                "WHERE s.file_id = ? AND s.is_exported = 1 "
                "AND s.id NOT IN (SELECT target_id FROM edges) "
                "AND s.kind IN ('function', 'class', 'method')",
                (fid,),
            ).fetchall()
            for e in exports:
                new_dead.append({"name": e["name"], "kind": e["kind"], "file": path})

        # --- 9. Author familiarity ---
        familiarity_risk = 0.0
        familiarity_details = {"avg_familiarity": 1.0, "files_assessed": 0, "files": []}
        if resolved_author:
            familiarity_risk, familiarity_details = _author_familiarity(
                conn, resolved_author, file_map,
            )

        # --- 10. Minor contributor risk ---
        minor_risk = 0.0
        minor_details = {"minor_files": 0, "files_assessed": 0, "files": []}
        if resolved_author:
            minor_risk, minor_details = _minor_contributor_risk(
                conn, resolved_author, file_map,
            )

        # --- Composite risk score (0-100) ---
        # Multiplicative model: each factor amplifies the base risk.
        # This captures interaction effects — high blast + untested is
        # exponentially worse than either alone, not just linearly worse.
        # log-space combination: risk = 100 * (1 - product(1 - factor_i))
        _factors = [
            min(blast_pct / 100, 0.40),          # blast radius (up to 40%)
            hotspot_score * 0.30,                  # hotspot (up to 30%)
            (1 - test_coverage) * 0.30,            # untested (up to 30%)
            bus_factor_risk * 0.20,                # bus factor (up to 20%)
            coupling_score * 0.20,                 # coupling (up to 20%)
            novelty * 0.15,                        # novelty (up to 15%)
            familiarity_risk,                      # author familiarity (up to 25%)
            minor_risk,                            # minor contributor (up to 15%)
        ]
        # Product of (1 - factor): probability of "no risk" from each
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

        # --- Per-file risk breakdown ---
        per_file = []
        for path, fid in file_map.items():
            syms = conn.execute(
                "SELECT id FROM symbols WHERE file_id = ?", (fid,)
            ).fetchall()
            file_affected = set()
            for s in syms:
                if s["id"] in RG:
                    file_affected.update(nx.descendants(RG, s["id"]))
            churn = churn_data.get(path, {})
            per_file.append({
                "path": path,
                "symbols": len(syms),
                "blast": len(file_affected),
                "churn": churn.get("churn", 0),
                "is_test": is_test_file(path),
            })
        per_file.sort(key=lambda x: x["blast"], reverse=True)

        # --- Suggested reviewers ---
        author_lines = {}
        for path, fid in file_map.items():
            if is_test_file(path):
                continue
            rows = conn.execute(
                "SELECT gc.author, gfc.lines_added FROM git_file_changes gfc "
                "JOIN git_commits gc ON gfc.commit_id = gc.id "
                "WHERE gfc.file_id = ?", (fid,),
            ).fetchall()
            for r in rows:
                author_lines[r["author"]] = author_lines.get(r["author"], 0) + (r["lines_added"] or 0)
        top_authors = sorted(author_lines.items(), key=lambda x: -x[1])[:5]

        label = commit_range or ("staged" if staged else "unstaged")

        # Verdict
        if level == "LOW":
            verdict = f"Low risk ({risk}/100) — safe to merge"
        elif level == "MODERATE":
            verdict = f"Moderate risk ({risk}/100) — review recommended"
        elif level == "HIGH":
            verdict = f"High risk ({risk}/100) — careful review needed"
        else:
            verdict = f"Critical risk ({risk}/100) — significant blast radius, thorough review required"

        if json_mode:
            click.echo(to_json(json_envelope("pr-risk",
                summary={
                    "verdict": verdict,
                    "risk_score": risk,
                    "risk_level": level,
                    "changed_files": len(file_map),
                },
                label=label,
                risk_score=risk,
                risk_level=level,
                changed_files=len(file_map),
                blast_radius_pct=round(blast_pct, 1),
                hotspot_score=round(hotspot_score, 2),
                test_coverage_pct=round(test_coverage * 100, 1),
                bus_factor_risk=round(bus_factor_risk, 2),
                coupling_score=round(coupling_score, 2),
                novelty_score=novelty,
                closest_similarity=closest_sim,
                closest_historical_pattern=closest_pattern,
                cluster_spread=round(cluster_spread, 2),
                clusters_touched=len(cluster_ids),
                total_clusters=total_clusters,
                layer_spread=round(layer_spread, 2),
                layers_touched=len(touched_layers),
                total_layers=total_layers,
                dead_exports=len(new_dead),
                familiarity=familiarity_details,
                minor_risk=minor_details,
                author=resolved_author,
                per_file=per_file,
                suggested_reviewers=[
                    {"author": a, "lines": l} for a, l in top_authors
                ],
                dead_code=new_dead[:10],
            )))
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}\n")
        click.echo(f"=== PR Risk ({label}) ===\n")
        click.echo(f"Risk Score: {risk}/100 ({level})")
        click.echo()

        click.echo("Breakdown:")
        click.echo(f"  Blast radius:  {blast_pct:5.1f}%  (affected {len(all_affected)} of {total_syms_repo} symbols)")
        click.echo(f"  Hotspot score: {hotspot_score * 100:5.1f}%  {'(hot files!)' if hotspot_score > 0.5 else ''}")
        click.echo(f"  Test coverage: {test_coverage * 100:5.1f}%  ({covered_files}/{len(source_files)} source files covered)")
        click.echo(f"  Bus factor:    {'RISK' if bus_factor_risk >= 0.5 else 'ok':>5s}  "
                    f"{'(single-author file!)' if bus_factor_risk >= 1.0 else ''}")
        click.echo(f"  Coupling:      {coupling_score * 100:5.1f}%")
        click.echo(f"  Novelty:       {novelty * 100:5.1f}%"
                    f"{'  (unfamiliar change combination!)' if novelty > 0.7 else ''}")
        if resolved_author:
            fam_avg = familiarity_details.get("avg_familiarity", 1.0)
            fam_assessed = familiarity_details.get("files_assessed", 0)
            fam_known = familiarity_details.get("files_familiar", 0)
            click.echo(f"  Familiarity:   {fam_avg * 100:5.1f}%"
                        f"  (author knows {fam_known}/{fam_assessed} changed files well)")
            minor_files = minor_details.get("minor_files", 0)
            minor_assessed = minor_details.get("files_assessed", 0)
            if minor_files > 0:
                click.echo(f"  Minor risk:    {minor_risk * 100:5.1f}%"
                            f"  (author is minor contributor to {minor_files}/{minor_assessed} files)")
            else:
                click.echo(f"  Minor risk:      0.0%"
                            f"  (author is major contributor to all files)")
        if total_clusters > 1:
            click.echo(f"  Cluster spread: {len(cluster_ids)}/{total_clusters} clusters touched")
        if total_layers > 1:
            click.echo(f"  Layer spread:   {len(touched_layers)}/{total_layers} layers touched")
        click.echo()

        # Per-file table
        rows = []
        for pf in per_file[:15]:
            flag = "test" if pf["is_test"] else ""
            rows.append([
                pf["path"],
                str(pf["symbols"]),
                str(pf["blast"]),
                str(pf["churn"]) if pf["churn"] else "",
                flag,
            ])
        click.echo("Changed files:")
        click.echo(format_table(
            ["file", "syms", "blast", "churn", ""],
            rows,
        ))
        if len(per_file) > 15:
            click.echo(f"  (+{len(per_file) - 15} more)")

        if new_dead:
            click.echo(f"\nNew dead exports ({len(new_dead)}):")
            for d in new_dead[:10]:
                click.echo(f"  {d['kind']:<10s} {d['name']:<30s} {d['file']}")
            if len(new_dead) > 10:
                click.echo(f"  (+{len(new_dead) - 10} more)")

        if top_authors:
            click.echo(f"\nSuggested reviewers:")
            for rev_author, lines in top_authors:
                click.echo(f"  {rev_author:<30s} ({lines} lines contributed)")
