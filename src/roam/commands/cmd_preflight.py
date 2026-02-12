"""Compound pre-change safety check.

Combines blast radius, affected tests, complexity, coupling, conventions,
and fitness violations into a single call -- reducing round-trips for AI
agents from 5-6 calls to 1.
"""

import os
from collections import Counter, defaultdict
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import (
    abbrev_kind, loc, to_json, json_envelope,
)
from roam.commands.resolve import ensure_index, find_symbol
from roam.commands.changed_files import (
    get_changed_files,
    resolve_changed_to_db,
    is_test_file,
)
from roam.commands.cmd_affected_tests import (
    _bfs_reverse_callers,
    _gather_affected_tests,
    _resolve_file_symbols,
    _looks_like_file,
)
from roam.commands.cmd_conventions import (
    classify_case,
    _group_for_kind,
)
from roam.commands.cmd_fitness import _load_rules, _CHECKERS


# ---------------------------------------------------------------------------
# Risk-level helpers
# ---------------------------------------------------------------------------

def _blast_severity(affected_syms: int, affected_files: int) -> str:
    if affected_syms >= 50 or affected_files >= 15:
        return "CRITICAL"
    if affected_syms >= 20 or affected_files >= 8:
        return "HIGH"
    if affected_syms >= 5 or affected_files >= 3:
        return "MEDIUM"
    return "LOW"


def _test_severity(direct: int, transitive: int, colocated: int) -> str:
    total = direct + transitive + colocated
    if total == 0:
        return "WARNING"
    return "OK"


def _complexity_severity(cc: float, nesting: int) -> str:
    if cc >= 25:
        return "CRITICAL"
    if cc >= 15 or nesting >= 5:
        return "HIGH"
    if cc >= 8 or nesting >= 4:
        return "MEDIUM"
    return "LOW"


def _coupling_severity(missing_count: int) -> str:
    if missing_count >= 5:
        return "HIGH"
    if missing_count >= 2:
        return "MEDIUM"
    if missing_count >= 1:
        return "LOW"
    return "OK"


def _convention_severity(violation_count: int) -> str:
    if violation_count >= 5:
        return "HIGH"
    if violation_count >= 1:
        return "WARNING"
    return "OK"


def _fitness_severity(failed_rules: int) -> str:
    if failed_rules >= 3:
        return "CRITICAL"
    if failed_rules >= 1:
        return "WARNING"
    return "OK"


_SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "WARNING": 2, "MEDIUM": 2, "LOW": 1, "OK": 0}


def _overall_risk(*severities: str) -> str:
    """Compute overall risk from individual severity labels."""
    max_val = max(_SEVERITY_ORDER.get(s, 0) for s in severities)
    if max_val >= 4:
        return "CRITICAL"
    if max_val >= 3:
        return "HIGH"
    if max_val >= 2:
        return "MEDIUM"
    return "LOW"


def _severity_tag(sev: str) -> str:
    return f"[{sev}]"


# ---------------------------------------------------------------------------
# 1. Blast radius
# ---------------------------------------------------------------------------

def _check_blast_radius(conn, sym_ids, file_paths):
    """Compute blast radius: affected symbols and files via reverse edges."""
    try:
        from roam.graph.builder import build_symbol_graph
        import networkx as nx
    except ImportError:
        return {
            "affected_symbols": 0,
            "affected_files": 0,
            "affected_file_list": [],
            "severity": "LOW",
        }

    G = build_symbol_graph(conn)
    RG = G.reverse()

    all_affected_syms = set()
    all_affected_files = set()

    for sid in sym_ids:
        if sid in RG:
            deps = nx.descendants(RG, sid)
            all_affected_syms.update(deps)
            for d in deps:
                node = G.nodes.get(d, {})
                fp = node.get("file_path")
                if fp and fp not in file_paths:
                    all_affected_files.add(fp)

    severity = _blast_severity(len(all_affected_syms), len(all_affected_files))

    return {
        "affected_symbols": len(all_affected_syms),
        "affected_files": len(all_affected_files),
        "affected_file_list": sorted(all_affected_files)[:20],
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# 2. Affected tests
# ---------------------------------------------------------------------------

def _check_affected_tests(conn, sym_ids, file_paths):
    """Find tests that need to run."""
    results = _gather_affected_tests(conn, sym_ids, file_paths)

    direct = sum(1 for r in results if r["kind"] == "DIRECT")
    transitive = sum(1 for r in results if r["kind"] == "TRANSITIVE")
    colocated = sum(1 for r in results if r["kind"] == "COLOCATED")

    # Unique test files
    seen = set()
    test_files = []
    for r in results:
        if r["file"] not in seen:
            seen.add(r["file"])
            test_files.append(r["file"])

    pytest_cmd = "pytest " + " ".join(test_files) if test_files else ""
    severity = _test_severity(direct, transitive, colocated)

    return {
        "direct": direct,
        "transitive": transitive,
        "colocated": colocated,
        "total": len(results),
        "test_files": test_files,
        "pytest_command": pytest_cmd,
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# 3. Complexity
# ---------------------------------------------------------------------------

def _check_complexity(conn, sym_ids):
    """Check complexity for target symbols."""
    if not sym_ids:
        return {
            "max_cognitive_complexity": 0,
            "max_nesting_depth": 0,
            "high_complexity_symbols": [],
            "severity": "LOW",
        }

    ph = ",".join("?" for _ in sym_ids)
    rows = conn.execute(
        f"""SELECT sm.cognitive_complexity, sm.nesting_depth,
                   sm.param_count, sm.line_count, sm.return_count,
                   sm.bool_op_count, sm.callback_depth,
                   s.name, s.kind, s.line_start, f.path as file_path
            FROM symbol_metrics sm
            JOIN symbols s ON sm.symbol_id = s.id
            JOIN files f ON s.file_id = f.id
            WHERE sm.symbol_id IN ({ph})
            ORDER BY sm.cognitive_complexity DESC""",
        list(sym_ids),
    ).fetchall()

    if not rows:
        return {
            "max_cognitive_complexity": 0,
            "max_nesting_depth": 0,
            "high_complexity_symbols": [],
            "severity": "LOW",
        }

    max_cc = max(r["cognitive_complexity"] for r in rows)
    max_nest = max(r["nesting_depth"] for r in rows)

    high = [
        {
            "name": r["name"],
            "kind": r["kind"],
            "file": r["file_path"],
            "line": r["line_start"],
            "cognitive_complexity": r["cognitive_complexity"],
            "nesting_depth": r["nesting_depth"],
        }
        for r in rows
        if r["cognitive_complexity"] >= 8
    ]

    severity = _complexity_severity(max_cc, max_nest)

    return {
        "max_cognitive_complexity": round(max_cc, 1),
        "max_nesting_depth": max_nest,
        "high_complexity_symbols": high[:10],
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# 4. Coupling (temporal co-change)
# ---------------------------------------------------------------------------

def _check_coupling(conn, file_ids, file_paths):
    """Find temporally-coupled files that should change together."""
    if not file_ids:
        return {
            "coupled_files": 0,
            "missing_partners": [],
            "severity": "OK",
        }

    change_set = set(file_ids)

    # Build lookups
    id_to_path = {}
    file_commits = {}
    for f in conn.execute("SELECT id, path FROM files").fetchall():
        id_to_path[f["id"]] = f["path"]
    for fs in conn.execute("SELECT file_id, commit_count FROM file_stats").fetchall():
        file_commits[fs["file_id"]] = fs["commit_count"] or 1

    missing = []
    min_strength = 0.3
    min_cochanges = 2

    for fid in file_ids:
        partners = conn.execute(
            """SELECT file_id_a, file_id_b, cochange_count
               FROM git_cochange
               WHERE file_id_a = ? OR file_id_b = ?""",
            (fid, fid),
        ).fetchall()

        for p in partners:
            partner_fid = p["file_id_b"] if p["file_id_a"] == fid else p["file_id_a"]
            cochanges = p["cochange_count"]
            if cochanges < min_cochanges:
                continue

            avg = (file_commits.get(fid, 1) + file_commits.get(partner_fid, 1)) / 2
            strength = cochanges / avg if avg > 0 else 0
            if strength < min_strength:
                continue

            if partner_fid not in change_set:
                partner_path = id_to_path.get(partner_fid, f"file_id={partner_fid}")
                source_path = id_to_path.get(fid, f"file_id={fid}")
                missing.append({
                    "path": partner_path,
                    "strength": round(strength, 2),
                    "cochanges": cochanges,
                    "partner_of": source_path,
                })

    # Deduplicate by path (keep highest strength)
    seen = {}
    for m in missing:
        if m["path"] not in seen or m["strength"] > seen[m["path"]]["strength"]:
            seen[m["path"]] = m
    missing = sorted(seen.values(), key=lambda x: -x["strength"])

    severity = _coupling_severity(len(missing))

    return {
        "coupled_files": len(missing),
        "missing_partners": missing[:10],
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# 5. Convention compliance
# ---------------------------------------------------------------------------

def _check_conventions(conn, sym_ids):
    """Check if target symbols follow codebase naming conventions."""
    if not sym_ids:
        return {
            "violations": [],
            "violation_count": 0,
            "severity": "OK",
        }

    # First, determine dominant naming style per kind-group from whole codebase
    all_symbols = conn.execute("""
        SELECT s.name, s.kind
        FROM symbols s
        WHERE s.kind IN ('function', 'method', 'class', 'interface',
                         'struct', 'trait', 'enum', 'variable',
                         'constant', 'property', 'field', 'type_alias')
    """).fetchall()

    group_cases = defaultdict(Counter)
    for sym in all_symbols:
        group = _group_for_kind(sym["kind"])
        style = classify_case(sym["name"])
        if style:
            group_cases[group][style] += 1

    # Determine dominant style per group
    dominant = {}
    for group, counter in group_cases.items():
        if counter:
            dominant[group] = counter.most_common(1)[0][0]

    # Now check target symbols
    ph = ",".join("?" for _ in sym_ids)
    target_syms = conn.execute(
        f"""SELECT s.name, s.kind, s.line_start, f.path as file_path
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            WHERE s.id IN ({ph})""",
        list(sym_ids),
    ).fetchall()

    violations = []
    for sym in target_syms:
        group = _group_for_kind(sym["kind"])
        style = classify_case(sym["name"])
        expected = dominant.get(group)
        if style and expected and style != expected:
            violations.append({
                "name": sym["name"],
                "kind": sym["kind"],
                "actual_style": style,
                "expected_style": expected,
                "file": sym["file_path"],
                "line": sym["line_start"],
            })

    severity = _convention_severity(len(violations))

    return {
        "violations": violations,
        "violation_count": len(violations),
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# 6. Fitness rule violations
# ---------------------------------------------------------------------------

def _check_fitness(conn, root):
    """Run fitness rules from .roam/fitness.yaml and report failures."""
    rules = _load_rules(root)
    if not rules:
        return {
            "rules_checked": 0,
            "rules_failed": 0,
            "total_violations": 0,
            "failed_rules": [],
            "rule_details": [],
            "severity": "OK",
        }

    all_violations = []
    rule_results = []

    for rule in rules:
        rtype = rule.get("type", "")
        checker = _CHECKERS.get(rtype)
        if checker is None:
            continue

        try:
            violations = checker(rule, conn)
        except Exception:
            violations = []

        status = "PASS" if not violations else "FAIL"
        rule_results.append({
            "name": rule.get("name", "unnamed"),
            "type": rtype,
            "status": status,
            "violations": len(violations),
        })
        all_violations.extend(violations)

    failed = sum(1 for r in rule_results if r["status"] == "FAIL")
    failed_names = [r["name"] for r in rule_results if r["status"] == "FAIL"]
    severity = _fitness_severity(failed)

    return {
        "rules_checked": len(rule_results),
        "rules_failed": failed,
        "total_violations": len(all_violations),
        "failed_rules": failed_names,
        "rule_details": rule_results,
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------

def _resolve_targets(conn, target, staged, root):
    """Resolve CLI arguments into (sym_ids, file_paths, file_ids, label).

    Returns a tuple of:
    - sym_ids: set of symbol IDs
    - file_paths: set of file paths (str)
    - file_ids: list of file IDs (int)
    - label: human-readable label for the target
    """
    sym_ids = set()
    file_paths = set()
    file_ids = []
    label = target or "staged changes"

    if staged:
        changed = get_changed_files(root, staged=True)
        if not changed:
            return sym_ids, file_paths, file_ids, "staged (no changes)"
        file_map = resolve_changed_to_db(conn, changed)
        if not file_map:
            return sym_ids, file_paths, file_ids, "staged (not in index)"
        for path, fid in file_map.items():
            file_paths.add(path)
            file_ids.append(fid)
            syms = conn.execute(
                "SELECT id FROM symbols WHERE file_id = ?", (fid,)
            ).fetchall()
            sym_ids.update(s["id"] for s in syms)
        label = f"staged changes ({len(file_map)} files)"

    if target:
        target_norm = target.replace("\\", "/")
        if _looks_like_file(target_norm):
            sids, fpaths = _resolve_file_symbols(conn, target_norm)
            if not sids:
                return sym_ids, file_paths, file_ids, f"{target} (not found)"
            sym_ids.update(sids)
            file_paths.update(fpaths)
            # Get file IDs for the resolved paths
            for fp in fpaths:
                row = conn.execute(
                    "SELECT id FROM files WHERE path = ?", (fp,)
                ).fetchone()
                if row:
                    file_ids.append(row["id"])
            label = target_norm
        else:
            sym = find_symbol(conn, target)
            if sym is None:
                return sym_ids, file_paths, file_ids, f"{target} (not found)"
            sym_ids.add(sym["id"])
            file_paths.add(sym["file_path"])
            # Get file ID
            row = conn.execute(
                "SELECT id FROM files WHERE path = ?", (sym["file_path"],)
            ).fetchone()
            if row:
                file_ids.append(row["id"])
            label = f"{sym['name']} ({loc(sym['file_path'], sym['line_start'])})"

    return sym_ids, file_paths, file_ids, label


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("preflight")
@click.argument("target", required=False, default=None)
@click.option("--staged", is_flag=True, help="Check staged changes")
@click.pass_context
def preflight(ctx, target, staged):
    """Run a pre-change safety checklist for a symbol, file, or staged changes.

    Combines blast radius, affected tests, complexity, coupling, conventions,
    and fitness checks into a single report. Ideal for AI agents that want
    one-call risk assessment before making changes.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    if not target and not staged:
        click.echo("Provide a TARGET symbol/file or use --staged.")
        raise SystemExit(1)

    ensure_index()
    root = find_project_root()

    with open_db(readonly=True) as conn:
        # Resolve targets
        sym_ids, file_paths, file_ids, label = _resolve_targets(
            conn, target, staged, root,
        )

        if not sym_ids:
            if json_mode:
                click.echo(to_json(json_envelope("preflight",
                    summary={"target": label, "risk_level": "UNKNOWN",
                             "error": "No symbols found"},
                )))
            else:
                click.echo(f"No symbols found for: {label}")
            return

        # Run all checks
        blast = _check_blast_radius(conn, sym_ids, file_paths)
        tests = _check_affected_tests(conn, sym_ids, file_paths)
        compl = _check_complexity(conn, sym_ids)
        coupl = _check_coupling(conn, file_ids, file_paths)
        convs = _check_conventions(conn, sym_ids)
        fitns = _check_fitness(conn, root)

        # Overall risk
        risk = _overall_risk(
            blast["severity"],
            tests["severity"],
            compl["severity"],
            coupl["severity"],
            convs["severity"],
            fitns["severity"],
        )

        # JSON output
        if json_mode:
            click.echo(to_json(json_envelope("preflight",
                summary={
                    "target": label,
                    "risk_level": risk,
                    "symbols_checked": len(sym_ids),
                    "files_checked": len(file_paths),
                },
                blast_radius={
                    "affected_symbols": blast["affected_symbols"],
                    "affected_files": blast["affected_files"],
                    "affected_file_list": blast["affected_file_list"],
                    "severity": blast["severity"],
                },
                tests={
                    "direct": tests["direct"],
                    "transitive": tests["transitive"],
                    "colocated": tests["colocated"],
                    "total": tests["total"],
                    "test_files": tests["test_files"],
                    "pytest_command": tests["pytest_command"],
                    "severity": tests["severity"],
                },
                complexity={
                    "max_cognitive_complexity": compl["max_cognitive_complexity"],
                    "max_nesting_depth": compl["max_nesting_depth"],
                    "high_complexity_symbols": compl["high_complexity_symbols"],
                    "severity": compl["severity"],
                },
                coupling={
                    "coupled_files": coupl["coupled_files"],
                    "missing_partners": coupl["missing_partners"],
                    "severity": coupl["severity"],
                },
                conventions={
                    "violation_count": convs["violation_count"],
                    "violations": convs["violations"],
                    "severity": convs["severity"],
                },
                fitness={
                    "rules_checked": fitns["rules_checked"],
                    "rules_failed": fitns["rules_failed"],
                    "total_violations": fitns["total_violations"],
                    "failed_rules": fitns["failed_rules"],
                    "rule_details": fitns["rule_details"],
                    "severity": fitns["severity"],
                },
            )))
            return

        # Text output
        click.echo(f"Pre-flight check for `{label}`:\n")

        # Blast radius
        blast_desc = (
            f"{blast['affected_symbols']} symbols in "
            f"{blast['affected_files']} files"
        )
        click.echo(
            f"  Blast radius:     {blast_desc:<40s} {_severity_tag(blast['severity'])}"
        )

        # Affected tests
        test_desc = f"{tests['direct']} direct, {tests['transitive']} transitive"
        if tests["colocated"]:
            test_desc += f", {tests['colocated']} colocated"
        click.echo(
            f"  Affected tests:   {test_desc:<40s} {_severity_tag(tests['severity'])}"
        )

        # Complexity
        cc = compl["max_cognitive_complexity"]
        nest = compl["max_nesting_depth"]
        compl_desc = f"cc={cc:.0f}, nest={nest}"
        click.echo(
            f"  Complexity:       {compl_desc:<40s} {_severity_tag(compl['severity'])}"
        )

        # Coupling
        if coupl["coupled_files"] > 0:
            coupl_desc = f"{coupl['coupled_files']} files often change together"
        else:
            coupl_desc = "no missing co-change partners"
        click.echo(
            f"  Coupling:         {coupl_desc:<40s} {_severity_tag(coupl['severity'])}"
        )

        # Conventions
        if convs["violation_count"] > 0:
            conv_desc = f"{convs['violation_count']} naming violations"
        else:
            conv_desc = "no violations"
        click.echo(
            f"  Conventions:      {conv_desc:<40s} {_severity_tag(convs['severity'])}"
        )

        # Fitness
        if fitns["rules_checked"] == 0:
            fit_desc = "no rules configured"
        elif fitns["rules_failed"] > 0:
            rule_names = ", ".join(fitns["failed_rules"][:3])
            fit_desc = f"{fitns['rules_failed']} rules would fail ({rule_names})"
        else:
            fit_desc = f"all {fitns['rules_checked']} rules pass"
        click.echo(
            f"  Fitness:          {fit_desc:<40s} {_severity_tag(fitns['severity'])}"
        )

        # Overall
        click.echo(f"\n  Overall risk: {risk}")

        # Suggested tests
        if tests["pytest_command"]:
            click.echo(f"  Suggested tests: {tests['pytest_command']}")
