"""Adversarial architecture review — challenge your changes.

Acts as a 'Dungeon Master' for code changes: generates targeted architectural
challenges based on graph topology. Composes existing tools (diff, cycles,
clusters, detectors, layers) to find structural issues and frames them as
adversarial questions the developer must address.
"""

from __future__ import annotations

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope, abbrev_kind, loc
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import get_changed_files, resolve_changed_to_db


# ---------------------------------------------------------------------------
# Severity ordering
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "WARNING": 2, "INFO": 1}

_MIN_SEVERITY = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


# ---------------------------------------------------------------------------
# Challenge builder
# ---------------------------------------------------------------------------

def _challenge(ctype, severity, title, description, question, location=None):
    """Build a challenge dict with all required fields."""
    return {
        "type": ctype,
        "severity": severity,
        "title": title,
        "description": description,
        "question": question,
        "location": location or "",
    }


# ---------------------------------------------------------------------------
# Challenge generators
# ---------------------------------------------------------------------------

def _check_new_cycles(conn, changed_sym_ids):
    """Check if changed symbols are part of any SCC (cycle)."""
    challenges = []
    if not changed_sym_ids:
        return challenges
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles
    except ImportError:
        return challenges

    try:
        G = build_symbol_graph(conn)
    except Exception:
        return challenges

    if len(G) == 0:
        return challenges

    try:
        sccs = find_cycles(G, min_size=2)
    except Exception:
        return challenges

    for scc in sccs:
        overlap = set(scc) & changed_sym_ids
        if not overlap:
            continue

        # Gather names for display (limit to 5)
        names = []
        for sid in scc[:5]:
            node = G.nodes.get(sid, {})
            name = node.get("name", f"id={sid}")
            names.append(name)

        location = ""
        first_overlap = list(overlap)[0]
        if first_overlap in G.nodes:
            location = G.nodes[first_overlap].get("file_path", "")

        challenges.append(_challenge(
            "new_cycle",
            "CRITICAL",
            f"Cyclic dependency involving {len(scc)} symbols",
            (
                f"Changed symbols participate in a cycle: "
                f"{' -> '.join(names)}{'...' if len(scc) > 5 else ''}. "
                f"SCC size: {len(scc)} symbols."
            ),
            (
                "With circular dependencies, explain why this won't cause "
                "infinite recursion or initialization ordering issues."
            ),
            location=location,
        ))
    return challenges


def _check_layer_violations(conn, changed_sym_ids):
    """Check if changed symbols violate layer boundaries (gap > 1)."""
    challenges = []
    if not changed_sym_ids:
        return challenges
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers
    except ImportError:
        return challenges

    try:
        G = build_symbol_graph(conn)
    except Exception:
        return challenges

    if len(G) == 0:
        return challenges

    try:
        layers = detect_layers(G)
    except Exception:
        return challenges

    seen = set()
    for sid in changed_sym_ids:
        if sid not in G or sid not in layers:
            continue
        src_layer = layers[sid]
        for _, tgt in G.out_edges(sid):
            if tgt not in layers:
                continue
            tgt_layer = layers[tgt]
            gap = abs(src_layer - tgt_layer)
            if gap <= 1:
                continue

            # Deduplicate by (src, tgt) pair
            edge_key = (sid, tgt)
            if edge_key in seen:
                continue
            seen.add(edge_key)

            src_node = G.nodes[sid]
            tgt_node = G.nodes[tgt]
            src_name = src_node.get("name", f"id={sid}")
            tgt_name = tgt_node.get("name", f"id={tgt}")
            file_path = src_node.get("file_path", "")

            challenges.append(_challenge(
                "layer_violation",
                "HIGH",
                f"Layer skip: L{src_layer} -> L{tgt_layer}",
                (
                    f"{src_name} (layer {src_layer}) calls "
                    f"{tgt_name} (layer {tgt_layer}), "
                    f"skipping {gap - 1} layer{'s' if gap - 1 != 1 else ''}."
                ),
                (
                    "This dependency skips intermediate layers. Justify the "
                    "shortcut or route through proper layer interfaces."
                ),
                location=file_path,
            ))
    return challenges


def _check_anti_patterns(conn, changed_file_ids):
    """Run anti-pattern detectors scoped to changed files."""
    challenges = []
    if not changed_file_ids:
        return challenges
    try:
        from roam.catalog.detectors import run_detectors
    except ImportError:
        return challenges

    try:
        findings = run_detectors(conn)
    except Exception:
        return challenges

    changed_fids = set(changed_file_ids)

    for f in findings:
        sym_id = f.get("symbol_id")
        if not sym_id:
            continue
        try:
            row = conn.execute(
                "SELECT file_id FROM symbols WHERE id = ?", (sym_id,)
            ).fetchone()
        except Exception:
            continue
        if not row or row["file_id"] not in changed_fids:
            continue

        confidence = f.get("confidence", "medium")
        severity = "HIGH" if confidence == "high" else "WARNING"
        detected = f.get("detected_way", "unknown")
        sym_name = f.get("symbol_name", "")
        location = f.get("location", "")
        suggested = f.get("suggested_way", "")

        challenges.append(_challenge(
            "anti_pattern",
            severity,
            f"Anti-pattern: {detected}",
            (
                f"Symbol '{sym_name}' at {location}. "
                f"Confidence: {confidence}."
            ),
            (
                f"Consider: {suggested}."
                if suggested
                else "Review this pattern and consider a better approach."
            ),
            location=location,
        ))
    return challenges


def _check_cross_cluster(conn, changed_sym_ids):
    """Check for cross-cluster edges introduced by changed symbols."""
    challenges = []
    if not changed_sym_ids:
        return challenges
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.clusters import detect_clusters
    except ImportError:
        return challenges

    try:
        G = build_symbol_graph(conn)
    except Exception:
        return challenges

    if len(G) == 0:
        return challenges

    try:
        clusters = detect_clusters(G)
    except Exception:
        return challenges

    if not clusters:
        return challenges

    # Collect cross-cluster edges from changed symbols
    cross_edges = []
    for sid in changed_sym_ids:
        if sid not in G or sid not in clusters:
            continue
        src_cluster = clusters[sid]
        for _, tgt in G.out_edges(sid):
            if tgt not in clusters:
                continue
            tgt_cluster = clusters[tgt]
            if tgt_cluster == src_cluster:
                continue
            src_node = G.nodes[sid]
            tgt_node = G.nodes[tgt]
            cross_edges.append((src_node, tgt_node, src_cluster, tgt_cluster))

    if not cross_edges:
        return challenges

    # Group by cluster pair (use frozenset for unordered pair)
    pairs: dict[tuple, list] = {}
    for src, tgt, sc, tc in cross_edges:
        key = (min(sc, tc), max(sc, tc))
        if key not in pairs:
            pairs[key] = []
        pairs[key].append((src, tgt))

    for (c1, c2), edges in pairs.items():
        edge_descs = [
            f"{e[0].get('name', '')} -> {e[1].get('name', '')}"
            for e in edges[:3]
        ]
        location = edges[0][0].get("file_path", "") if edges else ""

        challenges.append(_challenge(
            "cross_cluster",
            "WARNING",
            f"{len(edges)} cross-cluster edge(s) between cluster {c1} and {c2}",
            (
                f"Changed code adds edges crossing cluster boundaries: "
                f"{'; '.join(edge_descs)}"
                f"{'...' if len(edges) > 3 else ''}."
            ),
            (
                "These clusters were separated by the community detection algorithm. "
                "Justify the new coupling or extract a shared interface."
            ),
            location=location,
        ))
    return challenges


def _check_orphaned_symbols(conn, changed_sym_ids):
    """Check for symbols in changed files with zero incoming edges."""
    challenges = []
    if not changed_sym_ids:
        return challenges

    for sid in changed_sym_ids:
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM edges WHERE target_id = ?", (sid,)
            ).fetchone()
        except Exception:
            continue
        if not row or row["cnt"] != 0:
            continue

        try:
            sym = conn.execute(
                "SELECT s.name, s.kind, f.path as file_path, s.line_start "
                "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
                (sid,),
            ).fetchone()
        except Exception:
            continue
        if not sym:
            continue

        # Only flag substantive symbols
        if sym["kind"] not in ("function", "method", "class"):
            continue

        file_path = (sym["file_path"] or "").replace("\\", "/")
        name = sym["name"] or ""

        # Skip test files and private symbols
        is_test = (
            file_path.startswith("test")
            or "tests/" in file_path
            or "test/" in file_path
            or "spec/" in file_path
        )
        if is_test:
            continue
        if name.startswith("_"):
            continue

        location = loc(file_path, sym["line_start"])
        challenges.append(_challenge(
            "orphaned",
            "INFO",
            f"Orphaned symbol: {name}",
            (
                f"{name} ({abbrev_kind(sym['kind'])}) at {location} "
                f"has no callers."
            ),
            (
                "This symbol is not called by anything in the indexed codebase. "
                "Is it a new entry point, a public API, or was a connection forgotten?"
            ),
            location=location,
        ))
    return challenges


def _check_high_fan_out(conn, changed_sym_ids):
    """Check for changed symbols with unusually high fan-out (>10 outgoing edges)."""
    challenges = []
    if not changed_sym_ids:
        return challenges

    _FAN_OUT_THRESHOLD = 10

    for sid in changed_sym_ids:
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM edges WHERE source_id = ?", (sid,)
            ).fetchone()
        except Exception:
            continue
        if not row or row["cnt"] <= _FAN_OUT_THRESHOLD:
            continue

        fan_out = row["cnt"]
        try:
            sym = conn.execute(
                "SELECT s.name, s.kind, f.path as file_path, s.line_start "
                "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
                (sid,),
            ).fetchone()
        except Exception:
            continue
        if not sym:
            continue

        file_path = (sym["file_path"] or "").replace("\\", "/")
        name = sym["name"] or ""
        location = loc(file_path, sym["line_start"])

        challenges.append(_challenge(
            "high_fan_out",
            "WARNING",
            f"High fan-out: {name} calls {fan_out} dependencies",
            (
                f"{name} ({abbrev_kind(sym['kind'])}) at {location} "
                f"has {fan_out} outgoing edges, exceeding the threshold of "
                f"{_FAN_OUT_THRESHOLD}."
            ),
            (
                "High fan-out increases coupling and makes this symbol a "
                "change magnet. Consider splitting responsibilities or "
                "introducing a facade/coordinator pattern."
            ),
            location=location,
        ))
    return challenges


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _format_text(challenges, verdict, changed_files_count):
    """Produce plain-text challenge output."""
    lines = [f"VERDICT: {verdict}", ""]

    if not challenges:
        lines.append("No architectural challenges found — changes look clean.")
        return "\n".join(lines)

    lines.append(f"Changed files analyzed: {changed_files_count}")
    lines.append(f"Total challenges: {len(challenges)}")
    lines.append("")

    for i, c in enumerate(challenges, 1):
        lines.append(
            f"CHALLENGE {i} [{c['severity']}] -- {c['title']}"
        )
        lines.append(f"  {c['description']}")
        if c["location"]:
            lines.append(f"  Location: {c['location']}")
        lines.append(f"  Question: \"{c['question']}\"")
        lines.append("")

    return "\n".join(lines)


def _format_markdown(challenges, verdict, changed_files_count):
    """Produce GitHub-compatible markdown output."""
    lines = [
        "## Adversarial Architecture Review",
        "",
        f"**Verdict:** {verdict}",
        f"**Changed files:** {changed_files_count}",
        f"**Total challenges:** {len(challenges)}",
        "",
    ]

    if not challenges:
        lines.append(
            "_No architectural challenges found — changes look structurally clean._"
        )
        return "\n".join(lines)

    # Group by severity
    by_sev = {"CRITICAL": [], "HIGH": [], "WARNING": [], "INFO": []}
    for c in challenges:
        by_sev.setdefault(c["severity"], []).append(c)

    sev_labels = {
        "CRITICAL": "Critical",
        "HIGH": "High",
        "WARNING": "Warning",
        "INFO": "Info",
    }

    for sev in ("CRITICAL", "HIGH", "WARNING", "INFO"):
        group = by_sev.get(sev, [])
        if not group:
            continue
        lines.append(f"### {sev_labels[sev]} ({len(group)})")
        lines.append("")
        for c in group:
            lines.append(f"#### {c['title']}")
            lines.append("")
            lines.append(c["description"])
            if c["location"]:
                lines.append(f"- **Location:** `{c['location']}`")
            lines.append(f"- **Type:** `{c['type']}`")
            lines.append("")
            lines.append(f"> {c['question']}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("adversarial")
@click.option("--staged", is_flag=True, help="Review staged changes only")
@click.option(
    "--range", "commit_range", default=None,
    help="Review a commit range (e.g. main..HEAD)",
)
@click.option(
    "--severity",
    type=click.Choice(["low", "medium", "high", "critical"]),
    default="low",
    help="Minimum severity to show (default: low — show all)",
)
@click.option(
    "--fail-on-critical", is_flag=True,
    help="Exit 1 if critical challenges found (CI mode)",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["text", "markdown"]),
    default="text",
    help="Output format (default: text)",
)
@click.pass_context
def adversarial(ctx, staged, commit_range, severity, fail_on_critical, fmt):
    """Adversarial architecture review -- challenge your changes.

    Generates targeted architectural challenges based on graph topology.
    Acts as a 'Dungeon Master' forcing you to defend structural choices.

    \b
    Challenge types:
      CRITICAL  New cyclic dependencies
      HIGH      Layer violations, high-confidence anti-patterns
      WARNING   Cross-cluster coupling, low-confidence anti-patterns, high fan-out
      INFO      Orphaned symbols (no callers)
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    with open_db(readonly=True) as conn:
        # ------------------------------------------------------------------
        # Resolve changed files
        # ------------------------------------------------------------------
        changed = get_changed_files(
            root, staged=staged, commit_range=commit_range
        )

        if not changed:
            verdict = "No changes detected"
            if json_mode:
                click.echo(to_json(json_envelope(
                    "adversarial",
                    summary={
                        "verdict": verdict,
                        "challenges": 0,
                        "critical": 0,
                        "high": 0,
                        "warning": 0,
                        "info": 0,
                        "changed_files": 0,
                    },
                    challenges=[],
                )))
            elif fmt == "markdown":
                click.echo(_format_markdown([], verdict, 0))
            else:
                click.echo(f"VERDICT: {verdict}")
                click.echo("No uncommitted changes found.")
            return

        file_map = resolve_changed_to_db(conn, changed)

        if not file_map:
            verdict = "Changed files not found in index"
            if json_mode:
                click.echo(to_json(json_envelope(
                    "adversarial",
                    summary={
                        "verdict": verdict,
                        "challenges": 0,
                        "critical": 0,
                        "high": 0,
                        "warning": 0,
                        "info": 0,
                        "changed_files": len(changed),
                    },
                    challenges=[],
                )))
            elif fmt == "markdown":
                click.echo(_format_markdown([], verdict, len(changed)))
            else:
                click.echo(f"VERDICT: {verdict}")
                click.echo(
                    f"Changed files not found in index "
                    f"({len(changed)} files changed). "
                    "Try running `roam index` first."
                )
            return

        # ------------------------------------------------------------------
        # Gather symbol IDs and file IDs for changed files
        # ------------------------------------------------------------------
        changed_sym_ids: set[int] = set()
        changed_file_ids: set[int] = set()

        for path, fid in file_map.items():
            changed_file_ids.add(fid)
            try:
                syms = conn.execute(
                    "SELECT id FROM symbols WHERE file_id = ?", (fid,)
                ).fetchall()
                changed_sym_ids.update(s["id"] for s in syms)
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Run all challenge generators
        # ------------------------------------------------------------------
        challenges: list[dict] = []
        challenges.extend(_check_new_cycles(conn, changed_sym_ids))
        challenges.extend(_check_layer_violations(conn, changed_sym_ids))
        challenges.extend(_check_anti_patterns(conn, changed_file_ids))
        challenges.extend(_check_cross_cluster(conn, changed_sym_ids))
        challenges.extend(_check_orphaned_symbols(conn, changed_sym_ids))
        challenges.extend(_check_high_fan_out(conn, changed_sym_ids))

        # ------------------------------------------------------------------
        # Filter by minimum severity
        # ------------------------------------------------------------------
        min_sev = _MIN_SEVERITY.get(severity.lower(), 1)
        challenges = [
            c for c in challenges
            if _SEVERITY_ORDER.get(c["severity"], 0) >= min_sev
        ]

        # ------------------------------------------------------------------
        # Sort: critical first, then high, warning, info
        # ------------------------------------------------------------------
        challenges.sort(key=lambda c: -_SEVERITY_ORDER.get(c["severity"], 0))

        # ------------------------------------------------------------------
        # Compute summary counts
        # ------------------------------------------------------------------
        critical = sum(1 for c in challenges if c["severity"] == "CRITICAL")
        high = sum(1 for c in challenges if c["severity"] == "HIGH")
        warning = sum(1 for c in challenges if c["severity"] == "WARNING")
        info = sum(1 for c in challenges if c["severity"] == "INFO")

        if not challenges:
            verdict = "No architectural challenges found -- changes look clean"
        elif critical > 0:
            verdict = f"{len(challenges)} challenge(s), {critical} critical"
        elif high > 0:
            verdict = f"{len(challenges)} challenge(s), {high} high severity"
        elif warning > 0:
            verdict = f"{len(challenges)} challenge(s), {warning} warning(s)"
        else:
            verdict = f"{len(challenges)} challenge(s), {info} info"

        # ------------------------------------------------------------------
        # Output
        # ------------------------------------------------------------------
        if json_mode:
            click.echo(to_json(json_envelope(
                "adversarial",
                summary={
                    "verdict": verdict,
                    "challenges": len(challenges),
                    "critical": critical,
                    "high": high,
                    "warning": warning,
                    "info": info,
                    "changed_files": len(file_map),
                },
                challenges=challenges,
            )))
            if fail_on_critical and critical > 0:
                from roam.exit_codes import EXIT_GATE_FAILURE
                ctx.exit(EXIT_GATE_FAILURE)
            return

        if fmt == "markdown":
            output = _format_markdown(challenges, verdict, len(file_map))
        else:
            output = _format_text(challenges, verdict, len(file_map))

        click.echo(output)

        if fail_on_critical and critical > 0:
            from roam.exit_codes import EXIT_GATE_FAILURE
            ctx.exit(EXIT_GATE_FAILURE)
