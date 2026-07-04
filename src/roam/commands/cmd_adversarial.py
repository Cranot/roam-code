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
from roam.catalog._shared import is_test_path as _is_test_path
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


def _set_layer_status(status, value):
    if status is not None:
        status["layer_violations"] = value


def _import_layer_contract_tools(status):
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers
    except ImportError:
        _set_layer_status(status, "skipped:missing_graph_module")
        return None
    return build_symbol_graph, detect_layers


def _build_layer_contract_graph(conn, build_symbol_graph, status):
    try:
        G = build_symbol_graph(conn)
    except Exception as exc:  # noqa: BLE001
        _set_layer_status(status, f"errored:build_symbol_graph:{type(exc).__name__}")
        return None

    if len(G) == 0:
        _set_layer_status(status, "skipped:empty_graph")
        return None
    return G


def _detect_layer_contract_map(G, detect_layers, status):
    try:
        layers = detect_layers(G)
    except Exception as exc:  # noqa: BLE001
        _set_layer_status(status, f"errored:detect_layers:{type(exc).__name__}")
        return None

    _set_layer_status(status, "ran")
    return layers


def _load_layer_contract_context(conn, status):
    tools = _import_layer_contract_tools(status)
    if tools is None:
        return None

    build_symbol_graph, detect_layers = tools
    G = _build_layer_contract_graph(conn, build_symbol_graph, status)
    if G is None:
        return None

    layers = _detect_layer_contract_map(G, detect_layers, status)
    if layers is None:
        return None
    return G, layers


def _layer_contract_changed_symbol_index(G, layers, changed_sym_ids):
    changed_ids = tuple(changed_sym_ids)
    eligible_changed_ids = set(changed_ids) & set(G.nodes) & set(layers)
    return changed_ids, eligible_changed_ids


def _iter_layer_contract_candidate_edges(G, changed_ids, eligible_changed_ids):
    for sid in changed_ids:
        if sid not in eligible_changed_ids:
            continue
        for _, tgt in G.out_edges(sid):
            yield sid, tgt


def _layer_contract_skip_or_none(layers, sid, tgt, seen_edges):
    src_layer = layers.get(sid)
    tgt_layer = layers.get(tgt)
    if src_layer is None or tgt_layer is None:
        return None

    gap = abs(src_layer - tgt_layer)
    if gap <= 1:
        return None

    edge_key = (sid, tgt)
    if edge_key in seen_edges:
        return None
    seen_edges.add(edge_key)
    return sid, tgt, src_layer, tgt_layer, gap


def _iter_changed_edges_that_skip_layer_contract(G, layers, changed_sym_ids):
    """Yield deduplicated changed edges whose layer gap needs a challenge."""
    changed_ids, eligible_changed_ids = _layer_contract_changed_symbol_index(G, layers, changed_sym_ids)
    seen_edges = set()

    for sid, tgt in _iter_layer_contract_candidate_edges(G, changed_ids, eligible_changed_ids):
        skip = _layer_contract_skip_or_none(layers, sid, tgt, seen_edges)
        if skip is not None:
            yield skip


def _iter_changed_edges_that_cross_cluster_boundaries(G, clusters, changed_sym_ids):
    """Yield changed outgoing edges whose endpoints belong to different clusters."""
    changed_ids = tuple(changed_sym_ids)
    clustered_graph_nodes = set(G.nodes) & set(clusters)
    eligible_changed_ids = [sid for sid in changed_ids if sid in clustered_graph_nodes]

    for sid in eligible_changed_ids:
        src_cluster = clusters[sid]
        src_node = G.nodes[sid]
        for _, tgt in G.out_edges(sid):
            tgt_cluster = clusters.get(tgt)
            if tgt_cluster is None or tgt_cluster == src_cluster:
                continue
            yield src_node, G.nodes[tgt], src_cluster, tgt_cluster


def _layer_contract_skip_challenge(G, skip):
    sid, tgt, src_layer, tgt_layer, gap = skip
    src_node = G.nodes[sid]
    tgt_node = G.nodes[tgt]
    src_name = src_node.get("name", f"id={sid}")
    tgt_name = tgt_node.get("name", f"id={tgt}")
    file_path = src_node.get("file_path", "")

    return _challenge(
        "layer_violation",
        "HIGH",
        f"Layer skip: L{src_layer} -> L{tgt_layer}",
        (
            f"{src_name} (layer {src_layer}) calls "
            f"{tgt_name} (layer {tgt_layer}), "
            f"skipping {gap - 1} layer{'s' if gap - 1 != 1 else ''}."
        ),
        ("This dependency skips intermediate layers. Justify the shortcut or route through proper layer interfaces."),
        location=file_path,
    )


def _set_cycle_status(status, value):
    if status is not None:
        status["new_cycles"] = value


def _import_cycle_detection_tools(status):
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles
    except ImportError:
        _set_cycle_status(status, "skipped:missing_graph_module")
        return None
    return build_symbol_graph, find_cycles


def _build_cycle_detection_graph(conn, build_symbol_graph, status):
    try:
        G = build_symbol_graph(conn)
    except Exception as exc:  # noqa: BLE001
        _set_cycle_status(status, f"errored:build_symbol_graph:{type(exc).__name__}")
        return None

    if len(G) == 0:
        _set_cycle_status(status, "skipped:empty_graph")
        return None
    return G


def _find_symbol_cycles(G, find_cycles, status):
    try:
        sccs = find_cycles(G, min_size=2)
    except Exception as exc:  # noqa: BLE001
        _set_cycle_status(status, f"errored:find_cycles:{type(exc).__name__}")
        return None

    _set_cycle_status(status, "ran")
    return sccs


def _load_cycle_detection_context(conn, status):
    tools = _import_cycle_detection_tools(status)
    if tools is None:
        return None

    build_symbol_graph, find_cycles = tools
    G = _build_cycle_detection_graph(conn, build_symbol_graph, status)
    if G is None:
        return None

    sccs = _find_symbol_cycles(G, find_cycles, status)
    if sccs is None:
        return None
    return G, sccs


def _iter_cycles_touching_changed_symbols(sccs, changed_sym_ids):
    """Yield SCCs that touch changed symbols using one changed-id lookup set."""
    for scc in sccs:
        overlap = changed_sym_ids.intersection(scc)
        if overlap:
            yield tuple(scc), overlap


def _cycle_display_names(G, cycle_ids):
    """Return the leading symbol names that make a cycle challenge readable."""
    names = []
    for sid in cycle_ids[:5]:
        node = G.nodes.get(sid, {})
        names.append(node.get("name", f"id={sid}"))
    return names


def _cycle_location_that_preserves_changed_scope(G, cycle_ids, overlap):
    """Use a changed symbol location so global SCCs stay scoped to the edit."""
    first_overlap = next((sid for sid in cycle_ids if sid in overlap), None)
    if first_overlap in G.nodes:
        return G.nodes[first_overlap].get("file_path", "")
    return ""


def _new_cycle_challenge(G, cycle_ids, overlap):
    names = _cycle_display_names(G, cycle_ids)
    location = _cycle_location_that_preserves_changed_scope(G, cycle_ids, overlap)

    return _challenge(
        "new_cycle",
        "CRITICAL",
        f"Cyclic dependency involving {len(cycle_ids)} symbols",
        (
            f"Changed symbols participate in a cycle: "
            f"{' -> '.join(names)}{'...' if len(cycle_ids) > 5 else ''}. "
            f"SCC size: {len(cycle_ids)} symbols."
        ),
        (
            "With circular dependencies, explain why this won't cause "
            "infinite recursion or initialization ordering issues."
        ),
        location=location,
    )


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
    changed_ids = set(changed_sym_ids or ())
    if not changed_ids:
        _set_cycle_status(status, "skipped:no_changed_symbols")
        return challenges

    context = _load_cycle_detection_context(conn, status)
    if context is None:
        return challenges

    G, sccs = context
    for cycle_ids, overlap in _iter_cycles_touching_changed_symbols(sccs, changed_ids):
        challenges.append(_new_cycle_challenge(G, cycle_ids, overlap))
    return challenges


def _check_layer_violations(conn, changed_sym_ids, status=None):
    """Check if changed symbols violate layer boundaries (gap > 1).

    See :func:`_check_new_cycles` for ``status`` semantics (Pattern-2
    silent-fallback guard).
    """
    challenges = []
    if not changed_sym_ids:
        _set_layer_status(status, "skipped:no_changed_symbols")
        return challenges

    context = _load_layer_contract_context(conn, status)
    if context is None:
        return challenges

    G, layers = context
    for skip in _iter_changed_edges_that_skip_layer_contract(G, layers, changed_sym_ids):
        challenges.append(_layer_contract_skip_challenge(G, skip))
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
        # Scope the detectors to the changed files. The python-idiom detectors
        # otherwise regex-scan EVERY Python file (measured ~70% of a project-wide
        # run); passing the changed fileset collapses that to the files this
        # adversarial pass actually cares about. The post-filter below is now
        # redundant for symbol-bearing findings but kept as a safety net.
        findings = run_detectors(conn, scope_file_ids=changed_file_ids)
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status["anti_patterns"] = f"errored:run_detectors:{type(exc).__name__}"
        return challenges
    if status is not None:
        status["anti_patterns"] = "ran"

    changed_fids = set(changed_file_ids)

    # W1259 dogfood fix (CHALLENGE 57 HIGH loop-query): the original loop ran
    # one ``SELECT file_id FROM symbols WHERE id = ?`` per finding. On
    # roam-code itself ``run_detectors`` emits ~7900 findings, producing
    # ~7900 SQL round-trips here just to filter to the small set of changed
    # files. Pre-fetch the symbol->file_id map for ALL referenced symbols in
    # one batched query, then filter in Python.
    candidate_sids = {f.get("symbol_id") for f in findings if f.get("symbol_id")}
    sym_to_file: dict[int, int] = {}
    if candidate_sids:
        try:
            rows = batched_in(
                conn,
                "SELECT id, file_id FROM symbols WHERE id IN ({ph})",
                list(candidate_sids),
            )
            sym_to_file = {r["id"]: r["file_id"] for r in rows}
        except Exception as exc:  # noqa: BLE001
            # Lineage: degrade loudly. If the batched lookup fails we
            # cannot safely scope findings to changed files, so mark the
            # check as errored rather than emit a misleading clean result.
            if status is not None:
                status["anti_patterns"] = f"errored:symbol_file_lookup:{type(exc).__name__}"
            return challenges

    for f in findings:
        sym_id = f.get("symbol_id")
        if not sym_id:
            continue
        file_id = sym_to_file.get(sym_id)
        if file_id is None or file_id not in changed_fids:
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

    cross_edges = list(_iter_changed_edges_that_cross_cluster_boundaries(G, clusters, changed_sym_ids))

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


def _load_changed_symbol_counts(conn, changed_sym_ids, status, status_key, direction, alias):
    """Fetch changed symbols plus their edge count in one batched query.

    Shared scaffold for detectors that need a per-symbol edge count
    (in-degree, fan-out, etc.). Handles the empty-input guard, status
    bookkeeping, and batched query error handling.

    Parameters
    ----------
    conn
        SQLite connection.
    changed_sym_ids
        Iterable of symbol ids to analyze.
    status
        Mutable status dict (may be ``None``).
    status_key
        Key to use in ``status`` for skip/error/ran messages.
    direction
        Edge column direction: ``"source"`` or ``"target"``.
    alias
        Result-column alias for the count (e.g. ``"in_degree"``,
        ``"fan_out"``).

    Returns
    -------
    list
        Result rows, or an empty list if the query was skipped or errored.
    """
    if not changed_sym_ids:
        if status is not None:
            status[status_key] = "skipped:no_changed_symbols"
        return []

    sid_list = list(changed_sym_ids)
    try:
        rows = batched_in(
            conn,
            f"SELECT s.id, s.name, s.kind, f.path AS file_path, s.line_start, "
            f"       (SELECT COUNT(*) FROM edges WHERE {direction}_id = s.id) AS {alias} "
            f"  FROM symbols s JOIN files f ON s.file_id = f.id "
            f" WHERE s.id IN ({{ph}})",
            sid_list,
        )
    except Exception as exc:  # noqa: BLE001
        if status is not None:
            status[status_key] = f"errored:batched_in:{type(exc).__name__}"
        return []

    if status is not None:
        status[status_key] = "ran"
    return rows


def _check_orphaned_symbols(conn, changed_sym_ids, status=None):
    """Check for symbols in changed files with zero incoming edges.

    See :func:`_check_new_cycles` for ``status`` semantics.
    """
    challenges = []
    rows = _load_changed_symbol_counts(conn, changed_sym_ids, status, "orphaned_symbols", "target", "in_degree")
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

        # W1259 dogfood fix (W907 cargo-cult guard + parity): the original
        # ad-hoc check (``startswith("test")`` + ``"tests/" in``) missed
        # ``_test.go`` / ``_test.py`` suffix files, ``__tests__/``
        # directories, and camelCase ``UserTest.java`` / ``UserSpec.scala``
        # / ``UserTests.cs`` basenames — all of which the canonical
        # ``roam.catalog._shared.is_test_path`` detects. Delegate to the
        # canonical helper so multi-language repos don't see test
        # symbols flagged as orphans here.
        if _is_test_path(file_path):
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
    _FAN_OUT_THRESHOLD = 10

    rows = _load_changed_symbol_counts(conn, changed_sym_ids, status, "high_fan_out", "source", "fan_out")
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

    # W607-EK -- substrate-boundary plumbing for cmd_adversarial.
    # ``_run_check_ek`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607ek_warnings_out`` rather than
    # crashing the adversarial command outright. cmd_adversarial is a
    # multi-substrate aggregator (W148-doc + W150 detector-candidacy
    # audit) composing cycles + clusters + layers + catalog + dead +
    # complexity on changed files. A raise inside any constituent
    # substrate helper (_check_new_cycles, _check_layer_violations,
    # _check_anti_patterns, _check_cross_cluster, _check_orphaned_symbols,
    # _check_high_fan_out), the changed-file resolver, or any downstream
    # verdict / envelope composer used to crash the adversarial command
    # outright. Marker family
    # ``adversarial_<phase>_failed:<exc_class>:<detail>``. Substrates
    # wrapped:
    #
    #   * resolve_changed_files     -- get_changed_files +
    #                                  resolve_changed_to_db
    #   * lookup_changed_symbols    -- batched_in changed-symbol-id lookup
    #   * compose_cycles_check      -- _check_new_cycles (cycles substrate)
    #   * compose_layers_check      -- _check_layer_violations (layers
    #                                  substrate)
    #   * compose_catalog_check     -- _check_anti_patterns (algo catalog
    #                                  substrate)
    #   * compose_clusters_check    -- _check_cross_cluster (clusters
    #                                  substrate)
    #   * compose_dead_check        -- _check_orphaned_symbols (dead
    #                                  substrate)
    #   * compose_complexity_check  -- _check_high_fan_out (complexity
    #                                  substrate)
    #   * score_classify            -- severity filter + sort + counters
    #   * compose_verdict           -- LAW 6 single-line verdict floor
    #   * serialize_envelope        -- JSON envelope emission
    #
    # W978 7-discipline applied: (1) verdict floor uses literal
    # zero-count text -- no Name references, (2) default values for
    # _run_check_ek are immutable literals or empty lists, (3) no
    # json.dumps(default=str) needed (no datetimes), (4) ``adversarial_*``
    # prefix is unique (collision-checked by cross-prefix-discipline
    # test), (5) len() at kwarg-bind is gated by the envelope fallback,
    # (6) len() / if x: on a poisoned object only runs after the
    # empty-floor guard, (7) no dict.get(key, expensive_default) calls --
    # all defaults are immutable literals.
    _w607ek_warnings_out: list[str] = []

    def _run_check_ek(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-EK marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an
        ``adversarial_<phase>_failed:<exc_class>:<detail>`` marker via
        ``_w607ek_warnings_out`` and return *default* -- the envelope
        still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607ek_warnings_out.append(f"adversarial_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:
        # ------------------------------------------------------------------
        # Resolve changed files (W607-EK: resolve_changed_files substrate)
        # ------------------------------------------------------------------
        changed = _run_check_ek(
            "resolve_changed_files",
            get_changed_files,
            root,
            staged=staged,
            commit_range=commit_range,
            default=[],
        )
        if changed is None:
            changed = []

        if not changed:
            verdict = "No changes detected"
            if json_mode:
                # W607-EK: mirror substrate markers + partial_success
                # into the early-return envelope so a degraded
                # resolve_changed_files raise surfaces here rather
                # than vanishing into the no-changes path.
                _early_summary: dict = {
                    "verdict": verdict,
                    "challenges": 0,
                    "critical": 0,
                    "high": 0,
                    "warning": 0,
                    "info": 0,
                    "changed_files": 0,
                }
                _early_kwargs: dict = dict(
                    summary=_early_summary,
                    budget=token_budget,
                    challenges=[],
                )
                if _w607ek_warnings_out:
                    _early_summary["partial_success"] = True
                    _early_summary["warnings_out"] = list(_w607ek_warnings_out)
                    _early_kwargs["warnings_out"] = list(_w607ek_warnings_out)
                click.echo(to_json(json_envelope("adversarial", **_early_kwargs)))
            elif fmt == "markdown":
                click.echo(_format_markdown([], verdict, 0))
            else:
                click.echo(f"VERDICT: {verdict}")
                click.echo("No uncommitted changes found.")
            return

        # W607-EK: resolve_changed_files substrate (second leg) -- the
        # DB-side resolver. A raise here degrades to an empty file_map
        # so the rest of the envelope still composes.
        file_map = _run_check_ek(
            "resolve_changed_files",
            resolve_changed_to_db,
            conn,
            changed,
            default={},
        )
        if file_map is None:
            file_map = {}

        if not file_map:
            verdict = "Changed files not found in index"
            if json_mode:
                # W607-EK: mirror substrate markers + partial_success
                # into the early-return envelope so a degraded
                # resolve_changed_to_db raise surfaces here.
                _early_summary2: dict = {
                    "verdict": verdict,
                    "challenges": 0,
                    "critical": 0,
                    "high": 0,
                    "warning": 0,
                    "info": 0,
                    "changed_files": len(changed),
                }
                _early_kwargs2: dict = dict(
                    summary=_early_summary2,
                    budget=token_budget,
                    challenges=[],
                )
                if _w607ek_warnings_out:
                    _early_summary2["partial_success"] = True
                    _early_summary2["warnings_out"] = list(_w607ek_warnings_out)
                    _early_kwargs2["warnings_out"] = list(_w607ek_warnings_out)
                click.echo(to_json(json_envelope("adversarial", **_early_kwargs2)))
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
        # W1259 dogfood fix (CHALLENGE 71/77/88 silent-swallow at line 769):
        # the original loop ran one ``SELECT id FROM symbols WHERE file_id =
        # ?`` per changed file and silently swallowed any failure. A SQLite
        # error here would leave ``changed_sym_ids`` partial, making every
        # downstream check (cycles / layers / cross-cluster / orphaned /
        # fan-out) emit degraded results indistinguishable from a clean
        # pass — the canonical Pattern-2 silent-fallback hole. Batch the
        # lookup into one query AND degrade loudly via ``check_status``
        # when it fails.
        changed_sym_ids: set[int] = set()
        changed_file_ids: set[int] = set(file_map.values())
        sym_lookup_status = "ran"

        # W607-EK: lookup_changed_symbols substrate -- batched_in
        # changed-symbol-id lookup. A raise here degrades to an empty
        # changed_sym_ids set so each downstream substrate emits its
        # "no_changed_symbols" skipped state. The existing
        # sym_lookup_status check_status entry preserves the W1259
        # silent-swallow guard.
        def _lookup_changed_symbols():
            if not changed_file_ids:
                return (set(), "ran")
            try:
                rows = batched_in(
                    conn,
                    "SELECT id FROM symbols WHERE file_id IN ({ph})",
                    list(changed_file_ids),
                )
                return ({r["id"] for r in rows}, "ran")
            except Exception as exc:  # noqa: BLE001
                return (set(), f"errored:symbol_lookup:{type(exc).__name__}")

        lookup_result = _run_check_ek(
            "lookup_changed_symbols",
            _lookup_changed_symbols,
            default=(set(), "ran"),
        )
        if lookup_result is None:
            lookup_result = (set(), "ran")
        changed_sym_ids, sym_lookup_status = lookup_result

        # ------------------------------------------------------------------
        # Run all challenge generators
        # ------------------------------------------------------------------
        # SYNTHESIS Pattern 2 (silent fallback) — each helper records its
        # outcome in ``check_status``; the verdict-builder refuses to emit
        # "changes look clean" when any check errored. Same shape as the
        # W832 cmd_critique guard and the X4 cmd_pr_prep guard.
        check_status: dict[str, str] = {}
        # W1259 dogfood: also surface the changed-symbol lookup status so a
        # SQL failure here cannot silently produce empty downstream results.
        if sym_lookup_status != "ran":
            check_status["symbol_lookup"] = sym_lookup_status
        challenges: list[dict] = []

        # W607-EK: each constituent substrate wrapped so an uncaught
        # raise inside any one helper degrades to an empty list +
        # surfaces a marker. The six legs map 1:1 to the substrate
        # boundaries declared in the W148-doc characterization
        # (cycles + clusters + layers + catalog + dead + complexity on
        # changed files).
        cycles_result = _run_check_ek(
            "compose_cycles_check",
            _check_new_cycles,
            conn,
            changed_sym_ids,
            status=check_status,
            default=[],
        )
        if cycles_result is None:
            cycles_result = []
        challenges.extend(cycles_result)

        layers_result = _run_check_ek(
            "compose_layers_check",
            _check_layer_violations,
            conn,
            changed_sym_ids,
            status=check_status,
            default=[],
        )
        if layers_result is None:
            layers_result = []
        challenges.extend(layers_result)

        catalog_result = _run_check_ek(
            "compose_catalog_check",
            _check_anti_patterns,
            conn,
            changed_file_ids,
            status=check_status,
            default=[],
        )
        if catalog_result is None:
            catalog_result = []
        challenges.extend(catalog_result)

        clusters_result = _run_check_ek(
            "compose_clusters_check",
            _check_cross_cluster,
            conn,
            changed_sym_ids,
            status=check_status,
            default=[],
        )
        if clusters_result is None:
            clusters_result = []
        challenges.extend(clusters_result)

        dead_result = _run_check_ek(
            "compose_dead_check",
            _check_orphaned_symbols,
            conn,
            changed_sym_ids,
            status=check_status,
            default=[],
        )
        if dead_result is None:
            dead_result = []
        challenges.extend(dead_result)

        complexity_result = _run_check_ek(
            "compose_complexity_check",
            _check_high_fan_out,
            conn,
            changed_sym_ids,
            status=check_status,
            default=[],
        )
        if complexity_result is None:
            complexity_result = []
        challenges.extend(complexity_result)

        # ------------------------------------------------------------------
        # W607-EK: score_classify substrate -- severity filter + sort +
        # per-bucket counters. A raise inside ``severity_rank`` on a
        # malformed challenge dict degrades to the empty-counts floor
        # so the verdict still emits.
        # ------------------------------------------------------------------
        def _score_classify():
            min_sev_local = _MIN_SEVERITY.get(severity.lower(), severity_rank("low"))
            filtered_local = [c for c in challenges if severity_rank(c["severity"]) >= min_sev_local]
            filtered_local.sort(key=lambda c: -severity_rank(c["severity"]))
            critical_local = sum(1 for c in filtered_local if c["severity"] == "CRITICAL")
            high_local = sum(1 for c in filtered_local if c["severity"] == "HIGH")
            warning_local = sum(1 for c in filtered_local if c["severity"] == "WARNING")
            info_local = sum(1 for c in filtered_local if c["severity"] == "INFO")
            return (filtered_local, critical_local, high_local, warning_local, info_local)

        classified = _run_check_ek(
            "score_classify",
            _score_classify,
            default=([], 0, 0, 0, 0),
        )
        if classified is None:
            classified = ([], 0, 0, 0, 0)
        challenges, critical, high, warning, info = classified

        # SYNTHESIS Pattern 2 (silent fallback) guard — surface any
        # silently-degraded checks BEFORE deciding the verdict. If any
        # check errored, the "clean" verdict is a lie.
        errored_checks = sorted(name for name, s in check_status.items() if s.startswith("errored:"))
        partial_success = bool(errored_checks)

        # W607-EK: compose_verdict substrate -- LAW 6 single-line
        # verdict floor. A raise here degrades to the literal zero-count
        # floor string so the verdict NEVER disappears.
        # W1259 dogfood fix (LAW 4): the verdicts terminate on
        # ``challenges`` (anchored).
        def _compose_verdict():
            if not challenges:
                if partial_success:
                    return (
                        f"PARTIAL ({len(errored_checks)} check(s) errored: "
                        f"{', '.join(errored_checks)}) -- adversarial review degraded, "
                        "cannot certify clean"
                    )
                return "No architectural challenges found -- changes look clean"
            if critical > 0:
                verdict_local = f"{critical} critical of {len(challenges)} challenges"
            elif high > 0:
                verdict_local = f"{high} high-severity of {len(challenges)} challenges"
            elif warning > 0:
                verdict_local = f"{warning} warning(s) across {len(challenges)} challenges"
            else:
                verdict_local = f"{info} info-level of {len(challenges)} challenges"
            if partial_success:
                # Append partial qualifier so consumers see BOTH the
                # findings count AND the cascade. Matches the W832
                # cmd_critique shape.
                verdict_local += f" -- {len(errored_checks)} check(s) errored: {', '.join(errored_checks)}"
            return verdict_local

        verdict = _run_check_ek(
            "compose_verdict",
            _compose_verdict,
            default="0 of 0 challenges",
        )
        if not isinstance(verdict, str) or not verdict:
            verdict = "0 of 0 challenges"

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

            # W607-EK: mirror substrate markers into BOTH the top-level
            # envelope ``warnings_out`` AND ``summary.warnings_out`` so
            # MCP consumers see disclosure regardless of which surface
            # they read. Flipping ``partial_success: True`` is the
            # Pattern-2 silent-fallback guard. The substrate-marker flip
            # is independent of the in-tree ``check_status`` Pattern-2
            # guard -- a substrate boundary raising is a different
            # failure class from a constituent check returning
            # ``errored:*``.
            envelope_summary: dict = {
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
                "state": ("partial_adversarial" if partial_success else "all_checks_ran"),
            }
            envelope_kwargs: dict = dict(
                summary=envelope_summary,
                budget=token_budget,
                challenges=challenges,
                agent_contract={
                    "facts": facts,
                    "next_commands": next_commands,
                },
            )
            if _w607ek_warnings_out:
                envelope_summary["partial_success"] = True
                envelope_summary["warnings_out"] = list(_w607ek_warnings_out)
                envelope_kwargs["warnings_out"] = list(_w607ek_warnings_out)

            # W607-EK: serialize_envelope substrate -- json_envelope
            # construction + click.echo emission. The wrap protects
            # against crashes inside the formatter call so the marker
            # surfaces and the function returns cleanly.
            def _serialize_envelope():
                click.echo(to_json(json_envelope("adversarial", **envelope_kwargs)))

            _run_check_ek("serialize_envelope", _serialize_envelope, default=None)
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
