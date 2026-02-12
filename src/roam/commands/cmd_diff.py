"""Show blast radius of uncommitted changes."""

import fnmatch

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import get_changed_files, resolve_changed_to_db


# ---------------------------------------------------------------------------
# Affected tests helper
# ---------------------------------------------------------------------------

def _collect_affected_tests(conn, sym_by_file):
    """Gather affected tests for all symbols across changed files.

    Returns (test_entries, pytest_cmd) where test_entries is the list from
    ``_gather_affected_tests`` and pytest_cmd is a runnable pytest string.
    """
    from roam.commands.cmd_affected_tests import _gather_affected_tests

    all_sym_ids = set()
    all_file_paths = set()
    for path, syms in sym_by_file.items():
        all_file_paths.add(path)
        all_sym_ids.update(s["id"] for s in syms)

    if not all_sym_ids:
        return [], ""

    results = _gather_affected_tests(conn, all_sym_ids, all_file_paths)

    # Build deduplicated ordered file list for pytest command
    seen_order = []
    seen_set = set()
    for r in results:
        if r["file"] not in seen_set:
            seen_set.add(r["file"])
            seen_order.append(r["file"])

    pytest_cmd = "pytest " + " ".join(seen_order) if seen_order else ""
    return results, pytest_cmd


# ---------------------------------------------------------------------------
# Coupling warnings helper
# ---------------------------------------------------------------------------

def _collect_coupling_warnings(conn, file_map, min_cochanges=3):
    """Find temporally-coupled files that are NOT in the changeset.

    Returns a list of dicts with keys: path, cochanges, strength, partner_of.
    """
    change_fids = set(file_map.values())

    # Build lookup tables
    id_to_path = {}
    file_commits = {}
    for f in conn.execute("SELECT id, path FROM files").fetchall():
        id_to_path[f["id"]] = f["path"]
    for fs in conn.execute(
        "SELECT file_id, commit_count FROM file_stats"
    ).fetchall():
        file_commits[fs["file_id"]] = fs["commit_count"] or 1

    warnings = {}  # keyed by path, keep highest cochange

    for path, fid in file_map.items():
        rows = conn.execute(
            """SELECT file_id_a, file_id_b, cochange_count
               FROM git_cochange
               WHERE (file_id_a = ? OR file_id_b = ?)
               AND cochange_count >= ?""",
            (fid, fid, min_cochanges),
        ).fetchall()

        for r in rows:
            partner_fid = (
                r["file_id_b"] if r["file_id_a"] == fid else r["file_id_a"]
            )
            if partner_fid in change_fids:
                continue  # already in the diff, no warning needed

            cochanges = r["cochange_count"]
            avg = (
                file_commits.get(fid, 1) + file_commits.get(partner_fid, 1)
            ) / 2
            strength = cochanges / avg if avg > 0 else 0

            partner_path = id_to_path.get(partner_fid, f"file_id={partner_fid}")
            if (
                partner_path not in warnings
                or cochanges > warnings[partner_path]["cochanges"]
            ):
                warnings[partner_path] = {
                    "path": partner_path,
                    "cochanges": cochanges,
                    "strength": round(strength, 2),
                    "partner_of": path,
                }

    return sorted(warnings.values(), key=lambda x: -x["cochanges"])


# ---------------------------------------------------------------------------
# Fitness check helper (scoped to changed files)
# ---------------------------------------------------------------------------

def _collect_fitness_violations(conn, file_map, root):
    """Run fitness rules scoped to the changed files.

    For dependency rules: only report violations where the source is in the
    changed files (i.e. edges introduced/present in the diff).
    For metric rules on per-symbol metrics: only report symbols in changed files.
    For global metrics and naming rules: run normally (not file-scoped).

    Returns (rule_results, violations) lists.
    """
    from roam.commands.cmd_fitness import _load_rules

    rules = _load_rules(root)
    if not rules:
        return [], []

    changed_paths = set(file_map.keys())
    changed_fids = set(file_map.values())

    all_violations = []
    rule_results = []

    for rule in rules:
        rtype = rule.get("type", "")
        violations = []

        if rtype == "dependency":
            violations = _check_dep_rule_scoped(rule, conn, changed_paths)
        elif rtype == "metric":
            violations = _check_metric_rule_scoped(rule, conn, changed_fids)
        elif rtype == "naming":
            violations = _check_naming_rule_scoped(rule, conn, changed_fids)

        status = "PASS" if not violations else "FAIL"
        rule_results.append({
            "name": rule.get("name", "unnamed"),
            "type": rtype,
            "status": status,
            "violations": len(violations),
        })
        all_violations.extend(violations)

    return rule_results, all_violations


def _check_dep_rule_scoped(rule, conn, changed_paths):
    """Check dependency rule, only reporting edges whose source is in changed files."""
    from_pattern = rule.get("from", "**")
    to_pattern = rule.get("to", "**")
    allow = rule.get("allow", False)

    rows = conn.execute(
        """SELECT e.source_id, e.target_id, e.kind, e.line,
                  sf.path as source_path, tf.path as target_path,
                  ss.name as source_name, ts.name as target_name
           FROM edges e
           JOIN symbols ss ON e.source_id = ss.id
           JOIN symbols ts ON e.target_id = ts.id
           JOIN files sf ON ss.file_id = sf.id
           JOIN files tf ON ts.file_id = tf.id"""
    ).fetchall()

    violations = []
    for r in rows:
        # Only flag edges originating from changed files
        if r["source_path"] not in changed_paths:
            continue
        src_match = fnmatch.fnmatch(r["source_path"], from_pattern)
        tgt_match = fnmatch.fnmatch(r["target_path"], to_pattern)

        if src_match and tgt_match and not allow:
            violations.append({
                "rule": rule["name"],
                "type": "dependency",
                "message": f"{r['source_name']} -> {r['target_name']}",
                "source": f"{r['source_path']}:{r['line'] or '?'}",
                "target": r["target_path"],
                "edge_kind": r["kind"],
            })

    return violations


def _check_metric_rule_scoped(rule, conn, changed_fids):
    """Check metric rules scoped to changed files where applicable."""
    from roam.output.formatter import loc

    metric = rule.get("metric", "")
    max_val = rule.get("max")
    min_val = rule.get("min")
    violations = []

    if metric == "cognitive_complexity" and changed_fids:
        threshold = max_val if max_val is not None else 999
        ph = ",".join("?" for _ in changed_fids)
        rows = conn.execute(
            f"""SELECT sm.cognitive_complexity, s.name, s.kind,
                       s.line_start, f.path
                FROM symbol_metrics sm
                JOIN symbols s ON sm.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE s.file_id IN ({ph})
                AND sm.cognitive_complexity > ?
                ORDER BY sm.cognitive_complexity DESC""",
            list(changed_fids) + [threshold],
        ).fetchall()
        for r in rows:
            violations.append({
                "rule": rule["name"],
                "type": "metric",
                "message": (
                    f"{r['name']} complexity={r['cognitive_complexity']:.0f} "
                    f"(max={threshold})"
                ),
                "source": loc(r["path"], r["line_start"]),
                "metric": "cognitive_complexity",
                "value": r["cognitive_complexity"],
                "threshold": threshold,
            })
    elif metric in ("cycles", "health_score"):
        # Global metrics -- delegate to full checker
        from roam.commands.cmd_fitness import _check_metric_rule
        violations = _check_metric_rule(rule, conn)
    # Other count-based metrics run globally too
    elif metric in ("god_components", "bottlenecks", "dead_exports"):
        from roam.commands.cmd_fitness import _check_metric_rule
        violations = _check_metric_rule(rule, conn)

    return violations


def _check_naming_rule_scoped(rule, conn, changed_fids):
    """Check naming rules scoped to changed files."""
    import re
    from roam.output.formatter import loc

    kind = rule.get("kind", "function")
    pattern = rule.get("pattern", "")
    exclude = rule.get("exclude", "")

    if not pattern or not changed_fids:
        return []

    regex = re.compile(pattern)
    exclude_re = re.compile(exclude) if exclude else None

    ph = ",".join("?" for _ in changed_fids)
    rows = conn.execute(
        f"""SELECT s.name, s.kind, s.line_start, f.path
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            WHERE s.kind = ? AND s.file_id IN ({ph})""",
        [kind] + list(changed_fids),
    ).fetchall()

    violations = []
    for r in rows:
        name = r["name"]
        if exclude_re and exclude_re.match(name):
            continue
        if not regex.match(name):
            violations.append({
                "rule": rule["name"],
                "type": "naming",
                "message": f"{name} does not match {pattern}",
                "source": loc(r["path"], r["line_start"]),
            })

    return violations


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("diff")
@click.argument('commit_range', required=False, default=None)
@click.option('--staged', is_flag=True, help='Analyze staged changes instead of unstaged')
@click.option('--full', is_flag=True,
              help='Show all results without truncation and enable --tests --coupling --fitness')
@click.option('--tests', is_flag=True, help='Show affected test files')
@click.option('--coupling', is_flag=True, help='Warn about missing co-change partners')
@click.option('--fitness', is_flag=True, help='Check fitness rules against changed files')
@click.pass_context
def diff_cmd(ctx, commit_range, staged, full, tests, coupling, fitness):
    """Show blast radius: what code is affected by your changes.

    Optionally pass a COMMIT_RANGE (e.g. HEAD~3..HEAD, abc123, main..feature)
    to analyze committed changes instead of uncommitted ones.

    Use --tests, --coupling, --fitness to add extra analysis sections,
    or --full to enable all three plus untruncated output.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    root = find_project_root()

    # --full implies all three extras
    if full:
        tests = True
        coupling = True
        fitness = True

    changed = get_changed_files(root, staged=staged, commit_range=commit_range)
    if not changed:
        label = commit_range or ("staged" if staged else "unstaged")
        click.echo(f"No changes found for {label}.")
        return

    with open_db(readonly=True) as conn:
        # Map changed files to file IDs
        file_map = resolve_changed_to_db(conn, changed)

        if not file_map:
            click.echo(f"Changed files not found in index ({len(changed)} files changed).")
            click.echo("Try running `roam index` first.")
            return

        # Get symbols in changed files
        sym_by_file = {}
        for path, fid in file_map.items():
            syms = conn.execute(
                "SELECT id, name, kind FROM symbols WHERE file_id = ?", (fid,)
            ).fetchall()
            sym_by_file[path] = syms

        total_syms = sum(len(s) for s in sym_by_file.values())

        # Build graph and compute impact
        try:
            from roam.graph.builder import build_symbol_graph
            import networkx as nx
        except ImportError:
            click.echo("Graph module not available.")
            return

        G = build_symbol_graph(conn)
        RG = G.reverse()

        # Per-file impact analysis
        file_impacts = []
        all_affected_files = set()
        all_affected_syms = set()

        for path, syms in sym_by_file.items():
            file_dependents = set()
            file_affected_files = set()
            for s in syms:
                sid = s["id"]
                if sid in RG:
                    deps = nx.descendants(RG, sid)
                    file_dependents.update(deps)
                    for d in deps:
                        node = G.nodes.get(d, {})
                        fp = node.get("file_path")
                        if fp and fp != path:
                            file_affected_files.add(fp)

            all_affected_syms.update(file_dependents)
            all_affected_files.update(file_affected_files)

            file_impacts.append({
                "path": path,
                "symbols": len(syms),
                "affected_syms": len(file_dependents),
                "affected_files": len(file_affected_files),
            })

        # Sort by blast radius
        file_impacts.sort(key=lambda x: x["affected_syms"], reverse=True)

        # ── Extra analyses ───────────────────────────────────────────

        # Affected tests
        test_results = []
        pytest_cmd = ""
        if tests:
            test_results, pytest_cmd = _collect_affected_tests(conn, sym_by_file)

        # Coupling warnings
        coupling_warnings = []
        if coupling:
            try:
                coupling_warnings = _collect_coupling_warnings(conn, file_map)
            except Exception:
                pass  # table may not exist in older indexes

        # Fitness violations
        fitness_rule_results = []
        fitness_violations = []
        if fitness:
            try:
                fitness_rule_results, fitness_violations = (
                    _collect_fitness_violations(conn, file_map, root)
                )
            except Exception:
                pass  # fitness.yaml may not exist

        # ── JSON output ──────────────────────────────────────────────

        if json_mode:
            envelope_data = dict(
                label=commit_range or ("staged" if staged else "unstaged"),
                changed_files=len(file_map),
                symbols_defined=total_syms,
                affected_symbols=len(all_affected_syms),
                affected_files=len(all_affected_files),
                per_file=file_impacts,
                blast_radius=sorted(all_affected_files),
            )

            summary = {
                "changed_files": len(file_map),
                "affected_symbols": len(all_affected_syms),
                "affected_files": len(all_affected_files),
            }

            if tests:
                direct = sum(1 for t in test_results if t["kind"] == "DIRECT")
                transitive = sum(
                    1 for t in test_results if t["kind"] == "TRANSITIVE"
                )
                colocated = sum(
                    1 for t in test_results if t["kind"] == "COLOCATED"
                )
                test_files = []
                seen = set()
                for t in test_results:
                    if t["file"] not in seen:
                        seen.add(t["file"])
                        test_files.append(t["file"])

                summary["affected_tests"] = len(test_results)
                envelope_data["affected_tests"] = {
                    "total": len(test_results),
                    "direct": direct,
                    "transitive": transitive,
                    "colocated": colocated,
                    "test_files": test_files,
                    "pytest_command": pytest_cmd,
                    "tests": [
                        {
                            "file": t["file"],
                            "symbol": t["symbol"],
                            "kind": t["kind"],
                            "hops": t["hops"],
                            "via": t["via"],
                        }
                        for t in test_results
                    ],
                }

            if coupling:
                summary["coupling_warnings"] = len(coupling_warnings)
                envelope_data["coupling_warnings"] = coupling_warnings

            if fitness:
                failed_count = sum(
                    1 for r in fitness_rule_results if r["status"] == "FAIL"
                )
                summary["fitness_violations"] = len(fitness_violations)
                summary["fitness_rules_failed"] = failed_count
                envelope_data["fitness_violations"] = {
                    "rules": fitness_rule_results,
                    "violations": fitness_violations[:100],
                }

            click.echo(to_json(json_envelope(
                "diff", summary=summary, **envelope_data,
            )))
            return

        # ── Text output ──────────────────────────────────────────────

        if commit_range:
            label = commit_range
        else:
            label = "staged" if staged else "unstaged"
        click.echo(f"=== Blast Radius ({label} changes) ===\n")
        click.echo(f"Changed files: {len(file_map)}  Symbols defined: {total_syms}")
        click.echo(f"Affected symbols: {len(all_affected_syms)}  Affected files: {len(all_affected_files)}")
        click.echo()

        # Per-file breakdown
        rows = []
        display = file_impacts if full else file_impacts[:15]
        for fi in display:
            rows.append([
                fi["path"],
                str(fi["symbols"]),
                str(fi["affected_syms"]),
                str(fi["affected_files"]),
            ])
        click.echo(format_table(
            ["Changed file", "Symbols", "Affected syms", "Affected files"],
            rows,
        ))
        if not full and len(file_impacts) > 15:
            click.echo(f"\n(+{len(file_impacts) - 15} more files)")

        # List affected files
        if all_affected_files:
            click.echo(f"\nFiles in blast radius ({len(all_affected_files)}):")
            sorted_files = sorted(all_affected_files)
            show = sorted_files if full else sorted_files[:20]
            for fp in show:
                click.echo(f"  {fp}")
            if not full and len(sorted_files) > 20:
                click.echo(f"  (+{len(sorted_files) - 20} more)")

        # ── Affected tests section ───────────────────────────────────

        if tests:
            click.echo()
            if not test_results:
                click.echo("=== Affected Tests ===\n")
                click.echo("No affected tests found.")
            else:
                direct = sum(1 for t in test_results if t["kind"] == "DIRECT")
                transitive = sum(
                    1 for t in test_results if t["kind"] == "TRANSITIVE"
                )
                colocated = sum(
                    1 for t in test_results if t["kind"] == "COLOCATED"
                )
                click.echo(
                    f"=== Affected Tests ({len(test_results)}: "
                    f"{direct} direct, {transitive} transitive, "
                    f"{colocated} colocated) ===\n"
                )

                display_tests = (
                    test_results if full else test_results[:20]
                )
                for t in display_tests:
                    kind_tag = f"{t['kind']:<12s}"
                    if t["symbol"]:
                        test_label = f"{t['file']}::{t['symbol']}"
                    else:
                        test_label = t["file"]

                    if t["kind"] == "DIRECT":
                        detail = f"({t['hops']} hop)"
                    elif t["kind"] == "TRANSITIVE":
                        via_str = f" via {t['via']}" if t["via"] else ""
                        detail = f"({t['hops']} hops{via_str})"
                    else:
                        detail = "(same directory)"

                    click.echo(f"  {kind_tag} {test_label:<55s} {detail}")

                if not full and len(test_results) > 20:
                    click.echo(f"  (+{len(test_results) - 20} more)")

                if pytest_cmd:
                    click.echo(f"\nRun: {pytest_cmd}")

        # ── Coupling warnings section ────────────────────────────────

        if coupling:
            click.echo()
            click.echo("=== Coupling Warnings ===\n")
            if not coupling_warnings:
                click.echo("No missing co-change partners.")
            else:
                click.echo(
                    f"Missing co-change partners ({len(coupling_warnings)}):"
                )
                click.echo(
                    "(files you usually change together but are not in this diff)"
                )
                cpl_rows = []
                display_cpl = (
                    coupling_warnings if full else coupling_warnings[:10]
                )
                for w in display_cpl:
                    cpl_rows.append([
                        w["path"],
                        str(w["cochanges"]),
                        f"{w['strength']:.0%}",
                        w["partner_of"],
                    ])
                click.echo(format_table(
                    [
                        "Usually changes with",
                        "Co-changed",
                        "Strength",
                        "Partner of",
                    ],
                    cpl_rows,
                ))
                if not full and len(coupling_warnings) > 10:
                    click.echo(
                        f"\n(+{len(coupling_warnings) - 10} more warnings)"
                    )

        # ── Fitness violations section ───────────────────────────────

        if fitness:
            click.echo()
            if not fitness_rule_results:
                click.echo("=== Fitness Check ===\n")
                click.echo(
                    "No fitness rules found. Create .roam/fitness.yaml "
                    "or run: roam fitness --init"
                )
            else:
                failed = sum(
                    1 for r in fitness_rule_results if r["status"] == "FAIL"
                )
                passed = sum(
                    1 for r in fitness_rule_results if r["status"] == "PASS"
                )
                click.echo(
                    f"=== Fitness Check ({len(fitness_rule_results)} rules, "
                    f"{passed} passed, {failed} failed) ===\n"
                )

                for rr in fitness_rule_results:
                    icon = "PASS" if rr["status"] == "PASS" else "FAIL"
                    detail = (
                        f" ({rr['violations']} violations)"
                        if rr["violations"]
                        else ""
                    )
                    click.echo(f"  [{icon}] {rr['name']}{detail}")

                if fitness_violations:
                    click.echo(
                        f"\nViolations in changed files "
                        f"({len(fitness_violations)}):\n"
                    )
                    display_v = (
                        fitness_violations if full
                        else fitness_violations[:15]
                    )
                    for v in display_v:
                        src = v.get("source", "")
                        click.echo(f"  {v['rule']}: {v['message']}")
                        if src:
                            click.echo(f"    at {src}")
                    if not full and len(fitness_violations) > 15:
                        click.echo(
                            f"\n  (+{len(fitness_violations) - 15} more)"
                        )
