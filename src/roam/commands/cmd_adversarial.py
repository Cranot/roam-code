"""Adversarial architecture review — challenge your changes.

Acts as a 'Dungeon Master' for code changes: generates targeted architectural
challenges based on graph topology. Composes existing tools (diff, cycles,
clusters, detectors, layers) to find structural issues and frames them as
adversarial questions the developer must address.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because adversarial outputs are invocation-scoped architectural
challenges — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.changed_files import get_changed_files, resolve_changed_to_db
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, find_project_root, open_db
from roam.output._severity import severity_rank
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json

# ---------------------------------------------------------------------------
# Severity ordering (W564: ranks via canonical ``severity_rank``)
# ---------------------------------------------------------------------------
# CLI ``--severity`` floor mapping. Each value maps to the canonical
# ``severity_rank`` of the equivalent label so the filter compares
# directly against finding ranks below.
#
# W1005-followup-B: table widened from CVSS-only 4-tier
# {low, medium, high, critical} to the W547 canonical 7-token vocab
# {critical, error, high, warning, medium, low, info} so agents reading
# the canonical ``severity_rank()`` docstring can pass any tier and have
# the filter compare via the canonical rank table. The detectors in this
# command still EMIT only the UPPER 4-tier set {CRITICAL, HIGH, WARNING,
# INFO}; the WIDER filter vocabulary is the contract change. Aliases like
# ``note`` / ``unknown`` are intentionally NOT in the Choice — they
# collapse to ``info`` / sort below ``info`` via ``severity_rank``, so a
# user-facing filter on them would be confusing.
_MIN_SEVERITY = {
    "critical": severity_rank("critical"),
    "error": severity_rank("error"),
    "high": severity_rank("high"),
    "warning": severity_rank("warning"),
    "medium": severity_rank("medium"),
    "low": severity_rank("low"),
    "info": severity_rank("info"),
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


def _check_new_cycles(conn, changed_sym_ids, status=None):
    """Check if changed symbols are part of any SCC (cycle).

    ``status``: optional mutable dict; when provided, the helper records
    its run state as ``status["new_cycles"] = "ran" | "skipped:<reason>"
    | "errored:<ExcClass>"``. This is the Pattern-2 (silent fallback)
    guard at the orchestration boundary — the verdict-builder can then
    refuse to emit ``"changes look clean"`` when a check silently
    degraded. Helpers stay structurally unchanged when ``status`` is
    None so out-of-tree callers keep working.
    """
    challenges = []
    if not changed_sym_ids:
        if status is not None:
            status["new_cycles"] = "skipped:no_changed_symbols"
        return challenges
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles
    except ImportError:
        if status is not None:
            status["new_cycles"] = "skipped:missing_graph_module"
        return challenges

    try:
        G = build_symbol_graph(conn)
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status["new_cycles"] = f"errored:build_symbol_graph:{type(exc).__name__}"
        return challenges

    if len(G) == 0:
        if status is not None:
            status["new_cycles"] = "skipped:empty_graph"
        return challenges

    try:
        sccs = find_cycles(G, min_size=2)
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status["new_cycles"] = f"errored:find_cycles:{type(exc).__name__}"
        return challenges
    if status is not None:
        status["new_cycles"] = "ran"

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

        challenges.append(
            _challenge(
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
            )
        )
    return challenges


def _check_layer_violations(conn, changed_sym_ids, status=None):
    """Check if changed symbols violate layer boundaries (gap > 1).

    See :func:`_check_new_cycles` for ``status`` semantics (Pattern-2
    silent-fallback guard).
    """
    challenges = []
    if not changed_sym_ids:
        if status is not None:
            status["layer_violations"] = "skipped:no_changed_symbols"
        return challenges
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers
    except ImportError:
        if status is not None:
            status["layer_violations"] = "skipped:missing_graph_module"
        return challenges

    try:
        G = build_symbol_graph(conn)
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status["layer_violations"] = f"errored:build_symbol_graph:{type(exc).__name__}"
        return challenges

    if len(G) == 0:
        if status is not None:
            status["layer_violations"] = "skipped:empty_graph"
        return challenges

    try:
        layers = detect_layers(G)
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status["layer_violations"] = f"errored:detect_layers:{type(exc).__name__}"
        return challenges
    if status is not None:
        status["layer_violations"] = "ran"

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

            challenges.append(
                _challenge(
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
                )
            )
    return challenges


def _check_anti_patterns(conn, changed_file_ids, status=None):
    """Run anti-pattern detectors scoped to changed files.

    See :func:`_check_new_cycles` for ``status`` semantics.
    """
    challenges = []
    if not changed_file_ids:
        if status is not None:
            status["anti_patterns"] = "skipped:no_changed_files"
        return challenges
    try:
        from roam.catalog.detectors import run_detectors
    except ImportError:
        if status is not None:
            status["anti_patterns"] = "skipped:missing_detectors_module"
        return challenges

    try:
        findings = run_detectors(conn)
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status["anti_patterns"] = f"errored:run_detectors:{type(exc).__name__}"
        return challenges
    if status is not None:
        status["anti_patterns"] = "ran"

    changed_fids = set(changed_file_ids)

    for f in findings:
        sym_id = f.get("symbol_id")
        if not sym_id:
            continue
        try:
            row = conn.execute("SELECT file_id FROM symbols WHERE id = ?", (sym_id,)).fetchone()
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

        challenges.append(
            _challenge(
                "anti_pattern",
                severity,
                f"Anti-pattern: {detected}",
                (f"Symbol '{sym_name}' at {location}. Confidence: {confidence}."),
                (f"Consider: {suggested}." if suggested else "Review this pattern and consider a better approach."),
                location=location,
            )
        )
    return challenges


def _check_cross_cluster(conn, changed_sym_ids, status=None):
    """Check for cross-cluster edges introduced by changed symbols.

    See :func:`_check_new_cycles` for ``status`` semantics.
    """
    challenges = []
    if not changed_sym_ids:
        if status is not None:
            status["cross_cluster"] = "skipped:no_changed_symbols"
        return challenges
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.clusters import detect_clusters
    except ImportError:
        if status is not None:
            status["cross_cluster"] = "skipped:missing_graph_module"
        return challenges

    try:
        G = build_symbol_graph(conn)
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status["cross_cluster"] = f"errored:build_symbol_graph:{type(exc).__name__}"
        return challenges

    if len(G) == 0:
        if status is not None:
            status["cross_cluster"] = "skipped:empty_graph"
        return challenges

    try:
        clusters = detect_clusters(G)
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status["cross_cluster"] = f"errored:detect_clusters:{type(exc).__name__}"
        return challenges
    if status is not None:
        status["cross_cluster"] = "ran"

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
        edge_descs = [f"{e[0].get('name', '')} -> {e[1].get('name', '')}" for e in edges[:3]]
        location = edges[0][0].get("file_path", "") if edges else ""

        challenges.append(
            _challenge(
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
            )
        )
    return challenges


def _check_orphaned_symbols(conn, changed_sym_ids, status=None):
    """Check for symbols in changed files with zero incoming edges.

    single batched query for in-degree + symbol metadata
    instead of two queries per changed symbol.

    See :func:`_check_new_cycles` for ``status`` semantics.
    """
    challenges = []
    if not changed_sym_ids:
        if status is not None:
            status["orphaned_symbols"] = "skipped:no_changed_symbols"
        return challenges

    sid_list = list(changed_sym_ids)
    # One query per batch instead of two queries per symbol.
    try:
        rows = batched_in(
            conn,
            "SELECT s.id, s.name, s.kind, f.path AS file_path, s.line_start, "
            "       (SELECT COUNT(*) FROM edges WHERE target_id = s.id) AS in_degree "
            "  FROM symbols s JOIN files f ON s.file_id = f.id "
            " WHERE s.id IN ({ph})",
            sid_list,
        )
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status["orphaned_symbols"] = f"errored:batched_in:{type(exc).__name__}"
        return challenges
    if status is not None:
        status["orphaned_symbols"] = "ran"
    sym_by_id = {r["id"]: r for r in rows if r["in_degree"] == 0}

    for sid in changed_sym_ids:
        sym = sym_by_id.get(sid)
        if sym is None:
            continue

        # Only flag substantive symbols
        if sym["kind"] not in ("function", "method", "class"):
            continue

        file_path = (sym["file_path"] or "").replace("\\", "/")
        name = sym["name"] or ""

        # Skip test files and private symbols
        is_test = file_path.startswith("test") or "tests/" in file_path or "test/" in file_path or "spec/" in file_path
        if is_test:
            continue
        if name.startswith("_"):
            continue

        location = loc(file_path, sym["line_start"])
        challenges.append(
            _challenge(
                "orphaned",
                "INFO",
                f"Orphaned symbol: {name}",
                (f"{name} ({abbrev_kind(sym['kind'])}) at {location} has no callers."),
                (
                    "This symbol is not called by anything in the indexed codebase. "
                    "Is it a new entry point, a public API, or was a connection forgotten?"
                ),
                location=location,
            )
        )
    return challenges


def _check_high_fan_out(conn, changed_sym_ids, status=None):
    """Check for changed symbols with unusually high fan-out (>10 outgoing edges).

    See :func:`_check_new_cycles` for ``status`` semantics.
    """
    challenges = []
    if not changed_sym_ids:
        if status is not None:
            status["high_fan_out"] = "skipped:no_changed_symbols"
        return challenges

    _FAN_OUT_THRESHOLD = 10

    # single batched query for fan-out + metadata.
    sid_list = list(changed_sym_ids)
    try:
        rows = batched_in(
            conn,
            "SELECT s.id, s.name, s.kind, f.path AS file_path, s.line_start, "
            "       (SELECT COUNT(*) FROM edges WHERE source_id = s.id) AS fan_out "
            "  FROM symbols s JOIN files f ON s.file_id = f.id "
            " WHERE s.id IN ({ph})",
            sid_list,
        )
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status["high_fan_out"] = f"errored:batched_in:{type(exc).__name__}"
        return challenges
    if status is not None:
        status["high_fan_out"] = "ran"
    sym_by_id = {r["id"]: r for r in rows if r["fan_out"] > _FAN_OUT_THRESHOLD}

    for sid in changed_sym_ids:
        sym = sym_by_id.get(sid)
        if sym is None:
            continue
        fan_out = sym["fan_out"]

        file_path = (sym["file_path"] or "").replace("\\", "/")
        name = sym["name"] or ""
        location = loc(file_path, sym["line_start"])

        challenges.append(
            _challenge(
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
            )
        )
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
        lines.append(f"CHALLENGE {i} [{c['severity']}] -- {c['title']}")
        lines.append(f"  {c['description']}")
        if c["location"]:
            lines.append(f"  Location: {c['location']}")
        lines.append(f'  Question: "{c["question"]}"')
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
        lines.append("_No architectural challenges found — changes look structurally clean._")
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


@roam_capability(
    name="adversarial",
    category="workflow",
    summary="Adversarial architecture review -- challenge your changes",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("adversarial")
@click.option("--staged", is_flag=True, help="Review staged changes only")
@click.option(
    "--range",
    "commit_range",
    default=None,
    help="Review a commit range (e.g. main..HEAD)",
)
@click.option(
    "--severity",
    type=click.Choice(
        # W1005-followup-B: widened from CVSS-only 4-tier
        # {low, medium, high, critical} to W547 canonical 7-token vocab
        # so agents can pass any of {critical, error, high, warning,
        # medium, low, info} and have it compared via ``severity_rank()``
        # from ``roam.output._severity``. The adversarial detectors
        # currently emit only UPPER 4-tier {CRITICAL, HIGH, WARNING, INFO},
        # but the W547 rank table accepts SARIF aliases (``error`` ==
        # ``high`` at rank 4) and CVSS-style mid-tiers (``medium`` /
        # ``low``) under canonical ordering (higher = worse). Aliases like
        # ``note`` / ``unknown`` are intentionally NOT in the Choice —
        # they collapse to ``info`` / sort below ``info`` via
        # ``severity_rank``, so a user-facing filter on them would be
        # confusing.
        ["critical", "error", "high", "warning", "medium", "low", "info"],
        case_sensitive=False,
    ),
    default="low",
    help=(
        "Minimum severity to show (default: low — show all). Uses the "
        "canonical W547 ordering (critical > error == high > warning > "
        "medium > low > info). Detectors emit CRITICAL/HIGH/WARNING/INFO "
        "today; CVSS/SARIF aliases (error/medium/low) rank via the same "
        "severity_rank() comparator."
    ),
)
@click.option(
    "--fail-on-critical",
    is_flag=True,
    help="Exit 1 if critical challenges found (CI mode)",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "markdown"]),
    default="text",
    help="Output format (default: text)",
)
@click.pass_context
def adversarial(ctx, staged, commit_range, severity, fail_on_critical, fmt):
    """Adversarial architecture review -- challenge your changes.

    Unlike ``diff`` (which reports blast radius facts), this command frames
    architectural issues in changed files as challenges that developers
    must address.

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
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()
    root = find_project_root()

    with open_db(readonly=True) as conn:
        # ------------------------------------------------------------------
        # Resolve changed files
        # ------------------------------------------------------------------
        changed = get_changed_files(root, staged=staged, commit_range=commit_range)

        if not changed:
            verdict = "No changes detected"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
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
                            budget=token_budget,
                            challenges=[],
                        )
                    )
                )
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
                click.echo(
                    to_json(
                        json_envelope(
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
                            budget=token_budget,
                            challenges=[],
                        )
                    )
                )
            elif fmt == "markdown":
                click.echo(_format_markdown([], verdict, len(changed)))
            else:
                click.echo(f"VERDICT: {verdict}")
                click.echo(
                    f"Changed files not found in index ({len(changed)} files changed). Try running `roam index` first."
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
                syms = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (fid,)).fetchall()
                changed_sym_ids.update(s["id"] for s in syms)
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Run all challenge generators
        # ------------------------------------------------------------------
        # SYNTHESIS Pattern 2 (silent fallback) — each helper records its
        # outcome in ``check_status``; the verdict-builder refuses to emit
        # "changes look clean" when any check errored. Same shape as the
        # W832 cmd_critique guard and the X4 cmd_pr_prep guard.
        check_status: dict[str, str] = {}
        challenges: list[dict] = []
        challenges.extend(_check_new_cycles(conn, changed_sym_ids, status=check_status))
        challenges.extend(_check_layer_violations(conn, changed_sym_ids, status=check_status))
        challenges.extend(_check_anti_patterns(conn, changed_file_ids, status=check_status))
        challenges.extend(_check_cross_cluster(conn, changed_sym_ids, status=check_status))
        challenges.extend(_check_orphaned_symbols(conn, changed_sym_ids, status=check_status))
        challenges.extend(_check_high_fan_out(conn, changed_sym_ids, status=check_status))

        # ------------------------------------------------------------------
        # Filter by minimum severity
        # ------------------------------------------------------------------
        min_sev = _MIN_SEVERITY.get(severity.lower(), severity_rank("low"))
        challenges = [c for c in challenges if severity_rank(c["severity"]) >= min_sev]

        # ------------------------------------------------------------------
        # Sort: critical first, then high, warning, info
        # ------------------------------------------------------------------
        challenges.sort(key=lambda c: -severity_rank(c["severity"]))

        # ------------------------------------------------------------------
        # Compute summary counts
        # ------------------------------------------------------------------
        critical = sum(1 for c in challenges if c["severity"] == "CRITICAL")
        high = sum(1 for c in challenges if c["severity"] == "HIGH")
        warning = sum(1 for c in challenges if c["severity"] == "WARNING")
        info = sum(1 for c in challenges if c["severity"] == "INFO")

        # SYNTHESIS Pattern 2 (silent fallback) guard — surface any
        # silently-degraded checks BEFORE deciding the verdict. If any
        # check errored, the "clean" verdict is a lie.
        errored_checks = sorted(
            name for name, s in check_status.items() if s.startswith("errored:")
        )
        partial_success = bool(errored_checks)

        if not challenges:
            if partial_success:
                verdict = (
                    f"PARTIAL ({len(errored_checks)} check(s) errored: "
                    f"{', '.join(errored_checks)}) -- adversarial review degraded, "
                    "cannot certify clean"
                )
            else:
                verdict = "No architectural challenges found -- changes look clean"
        elif critical > 0:
            verdict = f"{len(challenges)} challenge(s), {critical} critical"
        elif high > 0:
            verdict = f"{len(challenges)} challenge(s), {high} high severity"
        elif warning > 0:
            verdict = f"{len(challenges)} challenge(s), {warning} warning(s)"
        else:
            verdict = f"{len(challenges)} challenge(s), {info} info"
        if partial_success and challenges:
            # Append partial qualifier so consumers see BOTH the findings
            # count AND the cascade. Matches the W832 cmd_critique shape.
            verdict += (
                f" -- {len(errored_checks)} check(s) errored: "
                f"{', '.join(errored_checks)}"
            )

        # ------------------------------------------------------------------
        # Output
        # ------------------------------------------------------------------
        if json_mode:
            # LAW 4 (CLAUDE.md): supply explicit agent_contract.facts anchored
            # on the concrete subject ("adversarial review") with an
            # analytical verb. Auto-derive would emit "critical: 5",
            # "high: 12" — abstract key:value pairs that fail to activate
            # analytical mode on the consumer.
            facts: list[str] = [verdict]
            if critical:
                facts.append(
                    f"adversarial review flagged {critical} CRITICAL "
                    f"architectural challenges across {len(file_map)} changed files"
                )
            if high:
                facts.append(f"adversarial review flagged {high} HIGH-severity challenges")
            if warning:
                facts.append(f"adversarial review surfaced {warning} warning(s)")
            if challenges:
                top = challenges[0]
                top_title = top.get("title") or top.get("message") or top.get("category") or "?"
                facts.append(f"highest-priority challenge: [{top.get('severity', '?')}] {top_title}")
            next_commands: list[str] = ["roam preflight", "roam critique"]
            if critical:
                next_commands.insert(0, "roam diff")
            click.echo(
                to_json(
                    json_envelope(
                        "adversarial",
                        summary={
                            "verdict": verdict,
                            "challenges": len(challenges),
                            "critical": critical,
                            "high": high,
                            "warning": warning,
                            "info": info,
                            "changed_files": len(file_map),
                            # SYNTHESIS Pattern 2 — disclose silent
                            # check-degradation so the verdict can't be
                            # silently read as a clean pass.
                            "partial_success": partial_success,
                            "failed_checks": errored_checks,
                            "check_status": dict(check_status),
                            "state": (
                                "partial_adversarial"
                                if partial_success
                                else "all_checks_ran"
                            ),
                        },
                        budget=token_budget,
                        challenges=challenges,
                        agent_contract={
                            "facts": facts,
                            "next_commands": next_commands,
                        },
                    )
                )
            )
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
