"""Find unprotected entry points — symbols with no path to a required gate."""
from __future__ import annotations

import fnmatch
import os
import re
from collections import defaultdict

import click

from roam.db.connection import open_db, batched_in
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.gate_presets import (
    ALL_PRESETS, GatePreset, GateRule, get_preset, detect_preset, load_gates_config,
)


def _find_gates(conn, gate_names, gate_pattern):
    """Find gate symbol IDs by exact name or regex pattern."""
    gates = set()
    gate_info = {}

    if gate_names:
        names = [n.strip() for n in gate_names.split(",") if n.strip()]
        for name in names:
            rows = conn.execute(
                "SELECT s.id, s.name, f.path as file_path, s.line_start "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name = ?",
                (name,),
            ).fetchall()
            for r in rows:
                gates.add(r["id"])
                gate_info[r["id"]] = r["name"]

    if gate_pattern:
        regex = re.compile(gate_pattern, re.IGNORECASE)
        rows = conn.execute(
            "SELECT s.id, s.name, f.path as file_path, s.line_start "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
        ).fetchall()
        for r in rows:
            if regex.search(r["name"]):
                gates.add(r["id"])
                gate_info[r["id"]] = r["name"]

    return gates, gate_info


def _find_entries(conn, scope, entry_pattern):
    """Find entry point symbols — exported top-level functions, optionally scoped."""
    sql = (
        "SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.is_exported = 1 AND s.kind IN ('function', 'method') "
        "AND s.parent_id IS NULL "
    )
    params = []

    if scope:
        # Convert glob to LIKE pattern
        like = scope.replace("*", "%").replace("?", "_")
        sql += "AND f.path LIKE ? "
        params.append(like)

    sql += "ORDER BY f.path, s.line_start"
    rows = conn.execute(sql, params).fetchall()

    if entry_pattern:
        regex = re.compile(entry_pattern, re.IGNORECASE)
        rows = [r for r in rows if regex.search(r["name"])]

    return rows


def _build_adj(conn):
    """Build adjacency list from edges table (source → [targets])."""
    adj = defaultdict(set)
    for e in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        adj[e["source_id"]].add(e["target_id"])
    return adj


def _bfs_to_gate(adj, start_id, gates, max_depth):
    """BFS from start_id to find shortest path to any gate symbol.

    Returns (gate_name, depth, chain) or (None, None, None) if not found.
    """
    if start_id in gates:
        return start_id, 0, [start_id]

    visited = {start_id}
    # Queue entries: (node_id, depth, path)
    queue = [(start_id, 0, [start_id])]

    while queue:
        current, depth, path = queue.pop(0)
        if depth >= max_depth:
            continue
        for neighbor in adj.get(current, set()):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            new_path = path + [neighbor]
            if neighbor in gates:
                return neighbor, depth + 1, new_path
            queue.append((neighbor, depth + 1, new_path))

    return None, None, None


def _evaluate_gate_rules(conn, rules):
    """Evaluate gate rules against indexed files.

    Returns a list of violation dicts for files that fail a gate rule.
    """
    # Fetch all indexed file paths
    all_files = [r["path"] for r in conn.execute("SELECT path FROM files").fetchall()]

    # Build a set of test-like file paths for quick lookup
    test_patterns = ["test_*", "*_test.*", "*.test.*", "*.spec.*", "*_test.go"]
    test_files = set()
    for fp in all_files:
        basename = os.path.basename(fp)
        if any(fnmatch.fnmatch(basename, tp) for tp in test_patterns):
            test_files.add(fp)

    # Count test functions per file
    test_fn_count = {}
    for fp in test_files:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.kind = 'function' AND s.name LIKE 'test%'",
            (fp,),
        ).fetchone()
        test_fn_count[fp] = row["cnt"] if row else 0

    violations = []
    for rule in rules:
        # Find files matching include patterns
        matched = set()
        for fp in all_files:
            for pat in rule.include_patterns:
                if fnmatch.fnmatch(fp, pat):
                    matched.add(fp)
                    break

        # Remove files matching exclude patterns
        for fp in list(matched):
            for pat in rule.exclude_patterns:
                if fnmatch.fnmatch(fp, pat):
                    matched.discard(fp)
                    break

        # For each matched file, check if a corresponding test file exists
        for fp in sorted(matched):
            basename = os.path.basename(fp)
            stem = os.path.splitext(basename)[0]
            # Look for test_<stem>.py, <stem>_test.py, <stem>.test.js, etc.
            has_test = False
            related_test_count = 0
            for tf in test_files:
                tf_base = os.path.basename(tf)
                if stem in tf_base:
                    has_test = True
                    related_test_count += test_fn_count.get(tf, 0)

            if not has_test or related_test_count < rule.min_test_count:
                violations.append({
                    "rule": rule.name,
                    "severity": rule.severity,
                    "file": fp,
                    "description": rule.description,
                    "test_found": has_test,
                    "test_count": related_test_count,
                    "min_required": rule.min_test_count,
                })

    return violations


@click.command("coverage-gaps")
@click.option("--gate", "gate_names", default=None,
              help="Comma-separated gate symbol names (e.g. 'requireAuth,validateToken')")
@click.option("--gate-pattern", "gate_pattern", default=None,
              help="Regex to match gate symbols by name (e.g. 'auth|permission|guard')")
@click.option("--scope", default=None,
              help="File scope glob (e.g. 'app/routes/**')")
@click.option("--entry-pattern", "entry_pattern", default=None,
              help="Regex to filter entry points by name (e.g. 'handler|controller')")
@click.option("--max-depth", default=8, show_default=True, help="Max BFS depth")
@click.option("--preset", "preset_name", default=None,
              help="Use a built-in gate preset (python, javascript, go, java-maven, rust)")
@click.option("--auto-detect", "auto_detect", is_flag=True, default=False,
              help="Auto-detect framework preset from project files")
@click.option("--config", "config_path", default=None,
              help="Path to .roam-gates.yml config file")
@click.pass_context
def coverage_gaps(ctx, gate_names, gate_pattern, scope, entry_pattern, max_depth,
                  preset_name, auto_detect, config_path):
    """Find entry points with no path to a required gate symbol.

    Use --gate for exact names or --gate-pattern for regex matching.
    Searches the call graph to find which entry points can reach a gate
    and which are unprotected.

    Use --preset or --auto-detect to apply framework-specific gate rules
    that check file-level test coverage requirements.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    # --- Resolve preset / config gate rules ---
    preset_used = None
    gate_rules = []

    if config_path:
        gate_rules = load_gates_config(config_path)
        if not gate_rules:
            msg = f"No rules loaded from {config_path}"
            if json_mode:
                click.echo(to_json(json_envelope("coverage-gaps",
                    summary={"error": msg},
                )))
            else:
                click.echo(msg)
            return

    if preset_name:
        preset_used = get_preset(preset_name)
        if not preset_used:
            available = ", ".join(p.name for p in ALL_PRESETS)
            msg = f"Unknown preset '{preset_name}'. Available: {available}"
            if json_mode:
                click.echo(to_json(json_envelope("coverage-gaps",
                    summary={"error": msg},
                )))
            else:
                click.echo(msg)
            return
        gate_rules = preset_used.rules

    if auto_detect and not preset_used:
        with open_db(readonly=True) as conn:
            file_paths = [r["path"] for r in conn.execute("SELECT path FROM files").fetchall()]
        preset_used = detect_preset(file_paths)
        if preset_used:
            gate_rules = preset_used.rules

    # If preset/config rules are active, evaluate them
    gate_violations = []
    if gate_rules:
        with open_db(readonly=True) as conn:
            gate_violations = _evaluate_gate_rules(conn, gate_rules)

    # If only preset/config mode (no --gate/--gate-pattern), output just the violations
    if not gate_names and not gate_pattern and gate_rules:
        errors = [v for v in gate_violations if v["severity"] == "error"]
        warnings = [v for v in gate_violations if v["severity"] == "warning"]
        preset_info = preset_used.name if preset_used else "custom"

        if json_mode:
            click.echo(to_json(json_envelope("coverage-gaps",
                summary={
                    "verdict": "fail" if errors else "pass",
                    "preset": preset_info,
                    "total_violations": len(gate_violations),
                    "errors": len(errors),
                    "warnings": len(warnings),
                },
                preset=preset_info,
                gate_violations=gate_violations,
            )))
        else:
            click.echo(f"=== Coverage Gaps (preset: {preset_info}) ===\n")
            click.echo(f"Violations: {len(gate_violations)}  "
                        f"Errors: {len(errors)}  Warnings: {len(warnings)}")
            click.echo()
            if gate_violations:
                rows = []
                for v in gate_violations[:40]:
                    rows.append([
                        v["severity"].upper(), v["rule"],
                        v["file"],
                        f"{v['test_count']}/{v['min_required']}",
                        v["description"],
                    ])
                click.echo(format_table(
                    ["Severity", "Rule", "File", "Tests", "Description"],
                    rows,
                    budget=40,
                ))
            else:
                click.echo("All gate rules pass.")
        return

    if not gate_names and not gate_pattern:
        click.echo("Provide --gate <names> or --gate-pattern <regex>")
        raise SystemExit(1)

    with open_db(readonly=True) as conn:
        gates, gate_info = _find_gates(conn, gate_names, gate_pattern)

        if not gates:
            if json_mode:
                click.echo(to_json(json_envelope("coverage-gaps",
                    summary={"error": "No gate symbols found"},
                )))
            else:
                click.echo("No gate symbols found matching the criteria.")
            return

        entries = _find_entries(conn, scope, entry_pattern)
        if not entries:
            if json_mode:
                click.echo(to_json(json_envelope("coverage-gaps",
                    summary={"error": "No entry points found"},
                )))
            else:
                click.echo("No entry points found in scope.")
            return

        adj = _build_adj(conn)

        # Resolve symbol names for chain display
        id_to_name = {}
        all_ids = set()
        for e in entries:
            all_ids.add(e["id"])
        for g in gates:
            all_ids.add(g)
        # Batch fetch names
        if all_ids:
            for r in batched_in(
                conn,
                "SELECT id, name FROM symbols WHERE id IN ({ph})",
                list(all_ids),
            ):
                id_to_name[r["id"]] = r["name"]

        covered = []
        uncovered = []

        for entry in entries:
            gate_id, depth, chain = _bfs_to_gate(adj, entry["id"], gates, max_depth)
            if gate_id is not None:
                # Resolve chain names (lazy — fetch as needed)
                chain_names = []
                for sid in chain:
                    if sid not in id_to_name:
                        r = conn.execute("SELECT name FROM symbols WHERE id = ?", (sid,)).fetchone()
                        id_to_name[sid] = r["name"] if r else "?"
                    chain_names.append(id_to_name[sid])

                covered.append({
                    "name": entry["name"],
                    "kind": entry["kind"],
                    "file": entry["file_path"],
                    "line": entry["line_start"],
                    "gate": gate_info.get(gate_id, "?"),
                    "depth": depth,
                    "chain": chain_names,
                })
            else:
                uncovered.append({
                    "name": entry["name"],
                    "kind": entry["kind"],
                    "file": entry["file_path"],
                    "line": entry["line_start"],
                    "reason": f"no gate in call chain (searched {max_depth} hops)",
                })

        total = len(entries)
        coverage_pct = round(len(covered) * 100 / total, 1) if total else 0

        if json_mode:
            summary = {
                "total_entries": total,
                "covered": len(covered),
                "uncovered": len(uncovered),
                "coverage_pct": coverage_pct,
                "gates_found": sorted(set(gate_info.values())),
            }
            extra = dict(
                gates_found=sorted(set(gate_info.values())),
                uncovered=uncovered,
                covered=covered,
            )
            if preset_used:
                summary["preset"] = preset_used.name
                extra["preset"] = preset_used.name
            if gate_violations:
                summary["gate_violation_count"] = len(gate_violations)
                extra["gate_violations"] = gate_violations
            click.echo(to_json(json_envelope("coverage-gaps",
                summary=summary,
                **extra,
            )))
            return

        # --- Text output ---
        header = "=== Coverage Gaps ==="
        if preset_used:
            header = f"=== Coverage Gaps (preset: {preset_used.name}) ==="
        click.echo(f"{header}\n")
        click.echo(f"Gates: {', '.join(sorted(set(gate_info.values())))}")
        click.echo(f"Entry points: {total}  Covered: {len(covered)}  "
                    f"Uncovered: {len(uncovered)}  Coverage: {coverage_pct}%")
        click.echo()

        if uncovered:
            click.echo(f"-- Uncovered ({len(uncovered)}) --")
            rows = []
            for u in uncovered[:30]:
                rows.append([
                    u["name"], abbrev_kind(u["kind"]),
                    loc(u["file"], u["line"]),
                    u["reason"],
                ])
            click.echo(format_table(
                ["Name", "Kind", "Location", "Reason"],
                rows,
                budget=30,
            ))
            click.echo()

        if covered:
            click.echo(f"-- Covered ({len(covered)}) --")
            rows = []
            for c in covered[:20]:
                chain_str = " -> ".join(c["chain"][:5])
                if len(c["chain"]) > 5:
                    chain_str += f" (+{len(c['chain']) - 5})"
                rows.append([
                    c["name"], abbrev_kind(c["kind"]),
                    loc(c["file"], c["line"]),
                    c["gate"], str(c["depth"]),
                    chain_str,
                ])
            click.echo(format_table(
                ["Name", "Kind", "Location", "Gate", "Depth", "Chain"],
                rows,
                budget=20,
            ))

        if gate_violations:
            click.echo()
            click.echo(f"-- Gate Violations ({len(gate_violations)}) --")
            rows = []
            for v in gate_violations[:30]:
                rows.append([
                    v["severity"].upper(), v["rule"],
                    v["file"],
                    f"{v['test_count']}/{v['min_required']}",
                ])
            click.echo(format_table(
                ["Severity", "Rule", "File", "Tests"],
                rows,
                budget=30,
            ))
