"""Graph-Isomorphism Transfer: topology fingerprint for cross-repo comparison.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because fingerprint outputs are topology fingerprint export
documents — not per-location violations. SARIF is reserved for
findings with file:line coordinates; fingerprint's primary deliverable
is the topology fingerprint document. See action.yml _SUPPORTED_SARIF
allowlist + W1175-RESEARCH Bucket C propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import format_table, json_envelope, to_json

# Hard refusal threshold — beyond this, the spectral analysis (Fiedler vector
# via algebraic_connectivity) runs O(n³) without a sparse-eigensolver hookup
# and will exhaust memory or stall. Below this, we just warn.
# Empirical: 13.8k symbols completes in ~11s; 50k symbols in ~5 min on a
# stock laptop. The hard cap is the boundary above which "index a
# subdirectory" really is the right answer.
_HARD_CAP_SYMBOLS = 100_000
# Soft warn threshold — above this we tell the user it'll take a moment but
# we still run the analysis. Pre-v12 this was the hard refusal threshold.
_WARN_THRESHOLD_SYMBOLS = 20_000

# Envelope-size cap on the clusters list. Pattern 6 (response volume):
# on roam-code itself the fingerprint emits ~9300 clusters of which only
# ~110 carry size_pct >= 0.1% — the trailing ~99% are singleton "clusters"
# of one symbol that bloat the JSON envelope to >300K tokens / 2.1MB.
# Text mode already shows top 5 only (line 641 below); JSON mode now
# emits up to this many to keep the envelope under the W333 / Pattern 6
# 50KB handle threshold while preserving every cluster that crossed the
# 0.1%-of-graph bar. compute_fingerprint sorts clusters descending by
# member count, so the kept slice is always the meaningful head.
_CLUSTERS_JSON_TOP_N = 100


# W155 (W93 follow-up): fingerprint is the next detector migrating onto the
# central findings registry (after ``clones`` in W95, ``dead`` in W99,
# ``complexity`` in W102, ``smells`` in W109, ``health`` in W151). Most of
# the fingerprint payload is aggregate metrics (modularity, fiedler,
# tangle_ratio, layers) — those stay in the envelope and do NOT become
# findings. The two surfaces that DO become per-row findings are:
#
# * ``arch.bad_cluster_pattern`` — clusters whose ``_classify_cluster_pattern``
#   label flags an architectural smell (``monolith``: size_pct > 40%, or
#   ``leaky``: conductance > 0.5). These are graph-pattern predicates over
#   the Louvain (or Leiden) community output -> ``structural`` tier.
# * ``arch.cyclic_cluster`` — Tarjan-SCCs that span more than one cluster.
#   These are the cross-cluster cycles legacy ``antipatterns.cyclic_clusters``
#   reported as a bare count; the registry surfaces them one row per SCC
#   so consumers can act per-cycle. Tarjan SCC + cluster-map intersection
#   is fully deterministic -> ``static_analysis`` tier.
#
# Boundary: god-component findings are emitted by ``roam health`` (W151)
# via the canonical ``roam.quality.god_components`` helper. ``fingerprint``
# surfaces the same count under ``antipatterns.god_components`` for its
# envelope but explicitly does NOT mirror them into the registry — the
# health detector owns that kind. See the explicit comment in the persist
# block below.
#
# Bump this when the predicate / claim shape of either kind changes.
FINGERPRINT_DETECTOR_VERSION: str = "1.0.0"


# W155 — per-kind confidence tier mapping. Kept as a module constant so
# tests can assert the mapping without re-deriving it.
_FINGERPRINT_KIND_TO_CONFIDENCE: dict[str, str] = {
    "arch.bad_cluster_pattern": "structural",
    "arch.cyclic_cluster": "static_analysis",
}


# The two cluster pattern labels we treat as architectural smells. The
# label vocabulary is defined by ``roam.graph.fingerprint._classify_cluster_pattern``:
#
#   monolith — size_pct > 40 (one cluster dominates the graph)
#   leaky    — conductance > 0.5 (cluster boundary leaks heavily)
#   island   — conductance < 0.1 (well-isolated -> not a smell)
#   module   — default (-> not a smell)
#
# Only ``monolith`` and ``leaky`` flag as findings. ``island`` and ``module``
# are the desirable outcomes and do not produce registry rows.
_BAD_CLUSTER_PATTERNS = frozenset({"monolith", "leaky"})


def _fingerprint_bad_cluster_finding_id(label: str, pattern: str) -> str:
    """Stable id for an ``arch.bad_cluster_pattern`` finding.

    Cluster ids from Louvain / Leiden are not stable across reruns (the
    integer id is just an enumeration index), so we key the finding id on
    the human-readable cluster ``label`` (e.g. ``"graph/Builder"``) plus
    the pattern label. Same cluster, same pattern -> same id -> upsert.
    A cluster whose composition shifts and gets a different label fires
    a fresh id, which is the desired behaviour (it's structurally a
    different cluster).
    """
    raw = f"{label}|{pattern}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"fingerprint:arch.bad_cluster_pattern:{digest}"


def _fingerprint_cyclic_cluster_finding_id(member_names: list[str]) -> str:
    """Stable id for an ``arch.cyclic_cluster`` finding.

    Mirrors ``cmd_health._health_cycle_finding_id`` — the SORTED member
    names fold into the digest so re-runs on the same SCC upsert
    regardless of in-memory iteration order. A symbol entering or
    leaving the SCC changes the digest, which is correct (the cycle is
    structurally different).
    """
    sorted_names = sorted(member_names)
    raw = "|".join(sorted_names)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"fingerprint:arch.cyclic_cluster:{digest}"


def _emit_fingerprint_findings(
    conn: sqlite3.Connection,
    clusters_data: list[dict],
    cyclic_sccs: list[dict],
    source_version: str,
) -> int:
    """Mirror fingerprint's cluster-level findings into the registry.

    Returns the count of rows written. The caller is responsible for
    opening ``conn`` writable; :func:`emit_finding` does not commit
    (the caller commits once after this returns).

    Parameters
    ----------
    clusters_data
        The full ``fp["clusters"]`` list — each entry has ``label``,
        ``layer``, ``size_pct``, ``conductance``, ``roles`` and
        ``pattern``. Only entries whose ``pattern`` falls in
        :data:`_BAD_CLUSTER_PATTERNS` emit a row.
    cyclic_sccs
        Pre-computed cross-cluster SCCs — each entry has
        ``member_names`` (list[str]), ``member_ids`` (list[int]),
        ``cluster_ids`` (list[int]), ``cluster_labels`` (list[str])
        and ``files`` (list[str]). One row per SCC.
    source_version
        Detector-version stamp; the caller passes
        :data:`FINGERPRINT_DETECTOR_VERSION`.

    Notes
    -----
    god-component rows are NOT emitted from fingerprint. The canonical
    god-component vocabulary lives under ``arch.god_component`` and is
    owned by ``cmd_health`` (W151), which uses
    ``roam.quality.god_components`` as its single source of truth.
    Emitting from both would double-count the registry surface; the
    W151 reconciliation deliberately moved that surface to health.
    """
    from roam.db.findings import FindingRecord, emit_finding

    written = 0

    # --- arch.bad_cluster_pattern ---
    # Cluster-level subject_kind ``cluster`` is the first cluster-scoped
    # entry in the registry's subject vocabulary (after ``symbol``,
    # ``file``, ``edge``, ``commit``). subject_id is NULL because clusters
    # don't have a single ``symbols.id`` anchor — the qualified_name pattern
    # ``cluster:<label>:<pattern>`` carries the human-readable handle.
    for c in clusters_data:
        pattern = c.get("pattern") or ""
        if pattern not in _BAD_CLUSTER_PATTERNS:
            continue
        label = c.get("label") or ""
        size_pct = c.get("size_pct") or 0.0
        conductance = c.get("conductance") or 0.0
        layer = c.get("layer")
        roles = c.get("roles") or {}
        finding_id = _fingerprint_bad_cluster_finding_id(label, pattern)
        evidence = {
            "kind": "arch.bad_cluster_pattern",
            "label": label,
            "pattern": pattern,
            "size_pct": size_pct,
            "conductance": conductance,
            "layer": layer,
            "roles": dict(roles),
            "qualified_name": f"cluster:{label}:{pattern}",
        }
        # Human-actionable claim string: name the cluster, the pattern,
        # and the metric that triggered it. Aligns with LAW 4
        # concrete-noun anchoring (terminal token ``clusters`` is in the
        # canonical anchor set).
        if pattern == "monolith":
            metric_clause = f"size {size_pct:.0f}% of graph"
        elif pattern == "leaky":
            metric_clause = f"conductance {conductance:.2f}"
        else:
            # Defensive — only monolith/leaky are in _BAD_CLUSTER_PATTERNS.
            metric_clause = f"pattern {pattern}"
        claim = f"arch.bad_cluster_pattern: cluster {label!r} flagged as {pattern} ({metric_clause})"
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="cluster",
                subject_id=None,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=_FINGERPRINT_KIND_TO_CONFIDENCE["arch.bad_cluster_pattern"],
                source_detector="fingerprint",
                source_version=source_version,
            ),
        )
        written += 1

    # --- arch.cyclic_cluster ---
    # One row per cross-cluster SCC. subject_kind reuses ``cycle`` so the
    # registry stays consistent with health's arch.cycle vocabulary —
    # both are "SCC-shaped" subjects. subject_id is NULL (SCCs lack a
    # single anchor).
    for scc in cyclic_sccs:
        member_names = scc.get("member_names") or []
        if not member_names:
            continue
        finding_id = _fingerprint_cyclic_cluster_finding_id(member_names)
        cluster_ids = scc.get("cluster_ids") or []
        cluster_labels = scc.get("cluster_labels") or []
        evidence = {
            "kind": "arch.cyclic_cluster",
            "size": len(member_names),
            "member_names": list(member_names),
            "member_ids": list(scc.get("member_ids") or []),
            "cluster_ids": list(cluster_ids),
            "cluster_labels": list(cluster_labels),
            "spanned_cluster_count": len(set(cluster_ids)),
            "files": list(scc.get("files") or []),
        }
        labels_clause = ", ".join(cluster_labels[:3]) or "?"
        if len(cluster_labels) > 3:
            labels_clause += ", ..."
        claim = (
            f"arch.cyclic_cluster: SCC of {len(member_names)} symbols "
            f"spans {len(set(cluster_ids))} clusters ({labels_clause})"
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="cycle",
                subject_id=None,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=_FINGERPRINT_KIND_TO_CONFIDENCE["arch.cyclic_cluster"],
                source_detector="fingerprint",
                source_version=source_version,
            ),
        )
        written += 1

    return written


def _gather_cyclic_sccs(
    conn: sqlite3.Connection, G, cluster_map: dict[int, int], cluster_labels: dict[int, str]
) -> list[dict]:
    """Build the per-SCC list of cross-cluster cycles for emission.

    Mirrors the count logic inside ``compute_fingerprint`` but returns the
    full SCC record (members + cluster spans + files) so the emit helper
    can write one row per SCC. Each entry is keyed on member NAMES so the
    upsert id is stable across rebuilds where SCC member-ids may shift.

    Only SCCs that span MORE than one cluster are returned — those are
    the architectural smell the legacy ``antipatterns.cyclic_clusters``
    count was flagging. Single-cluster SCCs are the ordinary call cycles
    that ``roam health`` already surfaces as ``arch.cycle``.
    """
    from roam.graph.cycles import find_cycles

    sccs = find_cycles(G, min_size=2)
    if not sccs:
        return []

    # Bulk-fetch the (name, file_path) for every node touched by an SCC
    # so we don't issue one query per node.
    touched_ids: set[int] = set()
    for scc in sccs:
        for nid in scc:
            touched_ids.add(int(nid))
    id_to_name: dict[int, str] = {}
    id_to_file: dict[int, str] = {}
    if touched_ids:
        try:
            from roam.db.connection import batched_in

            rows = batched_in(
                conn,
                "SELECT s.id, s.name, f.path FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
                list(touched_ids),
            )
            for r in rows:
                rid = int(r["id"])
                id_to_name[rid] = r["name"] or ""
                id_to_file[rid] = r["path"] or ""
        except sqlite3.OperationalError:
            # Pre-W89 schema or symbols table absent — fall through with
            # empty maps; SCCs without resolvable names just lose their
            # claim text but still upsert by member-id digest.
            pass

    out: list[dict] = []
    for scc in sccs:
        scc_cluster_ids = {cluster_map.get(int(n)) for n in scc if int(n) in cluster_map}
        scc_cluster_ids.discard(None)
        if len(scc_cluster_ids) < 2:
            # Single-cluster SCC -- this is an ordinary call cycle, not
            # a cross-cluster smell. Health emits these under
            # ``arch.cycle``; fingerprint stays out of that namespace.
            continue
        member_ids = [int(n) for n in scc]
        member_names = [id_to_name.get(nid, "") for nid in member_ids]
        # Drop empty names from the digest input so two SCCs that
        # collide on "" don't collapse together.
        named_members = [n for n in member_names if n]
        if not named_members:
            # Can't form a stable id without at least one resolved name.
            continue
        cluster_id_list = sorted({int(cid) for cid in scc_cluster_ids if cid is not None})
        cluster_label_list = [cluster_labels.get(cid, f"cluster-{cid}") for cid in cluster_id_list]
        files = sorted({id_to_file.get(nid, "") for nid in member_ids if id_to_file.get(nid)})
        out.append(
            {
                "member_ids": member_ids,
                "member_names": named_members,
                "cluster_ids": cluster_id_list,
                "cluster_labels": cluster_label_list,
                "files": files,
            }
        )
    return out


def _format_pct_list(pcts: list[float]) -> str:
    """Format a list of percentages into a compact distribution string."""
    return " / ".join(f"{p:.0f}%" for p in pcts)


@roam_capability(
    name="fingerprint",
    category="architecture",
    summary="Topology fingerprint for cross-repo comparison: layers, modularity, PageRank.",
    inputs=["repo_path"],
    outputs=["signature", "verdict"],
    examples=[
        "roam fingerprint",
        "roam fingerprint --export fp.json",
        "roam fingerprint --compare fp.json",
    ],
    tags=["architecture", "comparison"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command()
@click.option("--compact", is_flag=True, help="Single-line summary output")
@click.option(
    "--export",
    "export_path",
    type=click.Path(),
    default=None,
    help="Write fingerprint JSON to file",
)
@click.option(
    "--compare",
    "compare_path",
    type=click.Path(exists=True),
    default=None,
    help="Compare with a saved fingerprint JSON file",
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist cluster-level findings (arch.bad_cluster_pattern, "
        "arch.cyclic_cluster) to the .roam/index.db findings registry "
        "(cross-detector queryable via `roam findings list --detector "
        "fingerprint`). The detector-specific text/JSON output is "
        "unchanged. god-component rows are NOT mirrored here -- the "
        "health detector owns the arch.god_component vocabulary (W151)."
    ),
)
@click.pass_context
def fingerprint(ctx, compact, export_path, compare_path, persist):
    """Topology fingerprint for cross-repo comparison.

    Unlike ``capsule`` (which exports the raw graph as portable JSON),
    this command extracts a computed topology signature for cross-repo
    comparison.

    Extracts a structural signature from the codebase graph: layers,
    modularity, connectivity, clusters, hub/bridge ratio, PageRank
    distribution, and anti-patterns.

    Use --export to save and --compare to diff against another repo.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    # ``--compact`` is also a top-level global flag (``LazyGroup._GLOBAL_FLAGS``).
    # When invoked as ``roam fingerprint --compact`` the parser moves the
    # flag to the group context, leaving this command's local ``compact``
    # parameter False. Honour the global value too. v12.12.
    if not compact and ctx.obj:
        compact = bool(ctx.obj.get("compact"))
    ensure_index()

    # W607-DH -- substrate-boundary plumbing for cmd_fingerprint.
    # ``_run_check_dh`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607dh_warnings_out`` rather than
    # crashing the topology-fingerprint detector outright. cmd_fingerprint
    # is the topology-hash + cross-repo comparison detector (W82 / W82.1
    # sprint origin); the W155 cluster-level findings substrate (W93
    # follow-up) layered the registry-mirror on top of the original
    # graph-topology pass.
    #
    # Marker family ``fingerprint_<phase>_failed:<exc_class>:<detail>``.
    # Substrates wrapped:
    #
    #   * build_symbol_graph         -- DB → networkx graph construction
    #   * compute_fingerprint        -- topology pass (layers / modularity /
    #                                   fiedler / tangle / clusters / hubs)
    #   * compute_god_components     -- W17.2 canonical god-component
    #                                   reconciliation (degree-thresholded,
    #                                   agrees with roam health)
    #   * detect_clusters            -- Louvain community detection over G
    #   * label_clusters             -- {cluster_id: label} naming pass
    #   * gather_cyclic_sccs         -- W155 cross-cluster SCC mining
    #   * emit_fingerprint_findings  -- W155 registry mirror (silent
    #                                   no-op preserved for pre-W89 DB
    #                                   via sqlite3.OperationalError)
    #   * compose_verdict            -- LAW 6 single-line verdict
    #                                   (layers / modularity / fiedler /
    #                                   tangle composition)
    #   * write_export               -- W82.1 file-write pattern (atomic
    #                                   JSON export when --export is set)
    #   * compare_fingerprints       -- drift detection between two
    #                                   topology fingerprints (when
    #                                   --compare is set)
    #   * serialize_envelope         -- JSON envelope projection
    #                                   (Pattern 6 cluster cap +
    #                                   summary composition)
    _w607dh_warnings_out: list[str] = []

    def _run_check_dh(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-DH marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``fingerprint_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607dh_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607dh_warnings_out.append(f"fingerprint_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.fingerprint import compare_fingerprints, compute_fingerprint

        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        # W805-followup-B: empty-corpus short-circuit (Pattern 2 silent-fallback fix).
        # The spectral analysis (Fiedler vector + Louvain clustering)
        # operates on the symbol graph; with zero symbols it would either
        # crash inside build_symbol_graph / compute_fingerprint OR return
        # degenerate metrics (0 layers, 0.000 fiedler) that look like a
        # tidy result. Surface the empty state explicitly via
        # partial_success + closed-enum state. Mirrors W834 / W836 + W805.
        if sym_count == 0:
            verdict = "no symbols indexed (corpus empty — run `roam index --force` to populate)"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "fingerprint",
                            summary={
                                "verdict": verdict,
                                "symbol_count": 0,
                                "partial_success": True,
                                "state": "no_symbols",
                            },
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {verdict}")
            return
        if sym_count > _HARD_CAP_SYMBOLS:
            # W1085 (Pattern-1A): refuse-on-prerequisite must surface
            # partial_success + closed-enum state. The pre-W1085 envelope
            # made "graph too large, refused to analyze" structurally
            # indistinguishable from "analyzed cleanly with no findings"
            # to any consumer that only reads summary fields.
            # LAW 4: terminal token must hit the concrete-noun anchor set
            # (formatter.concrete_plural_terminals -> "symbols"). Put the
            # cap clause first so the sentence ends on "symbols".
            verdict = f"Skipped fingerprint above cap {_HARD_CAP_SYMBOLS:,}: graph has {sym_count:,} symbols"
            hint = (
                "Index a subdirectory with `roam index <path>` to narrow the "
                "analyzed subset, or raise `_HARD_CAP_SYMBOLS` in "
                "src/roam/commands/cmd_fingerprint.py."
            )
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "fingerprint",
                            summary={
                                "verdict": verdict,
                                "symbol_count": sym_count,
                                "hard_cap": _HARD_CAP_SYMBOLS,
                                "partial_success": True,
                                "state": "graph_too_large",
                                "cap_threshold": _HARD_CAP_SYMBOLS,
                                "actual_count": sym_count,
                            },
                            hint=hint,
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {verdict}")
                click.echo(f"HINT: {hint}")
            return
        if sym_count > _WARN_THRESHOLD_SYMBOLS and not json_mode:
            click.echo(
                f"  Note: {sym_count:,} symbols — spectral analysis may take a minute. (threshold {{:,}})".format(
                    _WARN_THRESHOLD_SYMBOLS
                ),
                err=True,
            )

        # W607-DH: ``build_symbol_graph`` substrate -- a raise here
        # (DB schema drift, networkx import failure) degrades to an
        # empty graph stand-in so the downstream substrates can still
        # compose an empty-floor envelope. We use a fresh empty
        # ``networkx.DiGraph`` placeholder rather than ``None`` so
        # ``compute_fingerprint`` doesn't trip on a NoneType.attribute
        # access before its own wrap fires.
        def _empty_graph():
            import networkx as nx

            return nx.DiGraph()

        G = _run_check_dh("build_symbol_graph", build_symbol_graph, conn, default=None)
        if G is None:
            G = _run_check_dh("build_symbol_graph_fallback", _empty_graph, default=None)
            if G is None:
                # networkx import itself failed -- collapse to a tiny
                # duck-typed stand-in so compute_fingerprint's wrap can
                # still surface its own marker without an AttributeError
                # bypass. The substrates below honor ``default={}``.
                G = object()

        # W607-DH: ``compute_fingerprint`` substrate -- the main topology
        # pass. A raise degrades to an empty floor dict; downstream
        # substrates honor the missing keys via ``.get()`` defaults.
        fp = _run_check_dh("compute_fingerprint", compute_fingerprint, conn, G, default={})
        if fp is None:
            fp = {}

        # W17.2 / Pattern 3c: reconcile god-component count with `roam health`.
        # The legacy `god_objects` field uses a statistical (avg_degree*2)
        # algorithm. The canonical metric (degree-thresholded, utility-aware)
        # is owned by `roam.quality.god_components`. We surface both:
        # `god_components` (canonical, agrees with health) and
        # `god_objects` (legacy alias, retained for back-compat).
        # W607-DH: the historical bare ``try/except: pass`` is now a
        # disclosed substrate boundary.
        def _compute_god_components():
            from roam.quality.god_components import (
                definition as _gc_def,
            )
            from roam.quality.god_components import (
                god_components as _gc,
            )

            _gsum = _gc(conn)
            fp.setdefault("antipatterns", {})
            fp["antipatterns"]["god_components"] = _gsum.total
            fp["antipatterns"]["god_components_critical"] = _gsum.critical
            fp["antipatterns"]["god_components_actionable"] = _gsum.actionable
            fp["antipatterns"]["god_components_legacy_god_objects"] = fp["antipatterns"].get("god_objects", 0)
            fp["antipatterns"]["god_components_definition"] = _gc_def()
            return True

        _run_check_dh("compute_god_components", _compute_god_components, default=None)

        # --- W155: mirror cluster-level findings into the registry ---
        # Runs ONLY with --persist. The persisted set is independent of
        # the display slicing (--compact / --compare / top-5 cluster
        # table) — we emit EVERY bad-pattern cluster + EVERY cross-cluster
        # SCC so the registry stays comprehensive. Wrapped in
        # try/except sqlite3.OperationalError so a pre-W89 DB (without
        # the findings table) silently no-ops rather than crashing the
        # standard fingerprint path.
        #
        # Boundary check: we deliberately do NOT emit god_object /
        # god_component rows here. The W151 health migration owns the
        # arch.god_component kind (via the canonical
        # roam.quality.god_components helper). Emitting from both would
        # double-count the registry surface.
        if persist:
            # W607-DH: ``emit_fingerprint_findings`` substrate boundary
            # uses DIRECT try/except (not _run_check_dh) for the
            # sqlite3.OperationalError leg because the pre-W89 schema
            # path (missing ``findings`` table) is the EXPECTED degraded
            # path -- the W155 silent no-op contract for that case must
            # NOT produce a W607-DH marker. Generic exceptions surface
            # via the per-substrate marker. Mirrors the cmd_bus_factor
            # W607-CQ template: OperationalError == silent no-op;
            # generic Exception == W607-DH marker.
            try:
                # Re-derive cluster_map + cluster labels for cross-cluster
                # SCC analysis. compute_fingerprint already ran these,
                # but only returned the per-cluster summary list; we need
                # the raw {node_id: cluster_id} map to intersect with SCCs.
                from roam.graph.clusters import detect_clusters, label_clusters

                # W607-DH: ``detect_clusters`` substrate -- Louvain pass.
                _cluster_map = _run_check_dh("detect_clusters", detect_clusters, G, default={})
                if _cluster_map is None:
                    _cluster_map = {}
                # W607-DH: ``label_clusters`` substrate -- naming pass.
                _cluster_labels = _run_check_dh("label_clusters", label_clusters, _cluster_map, conn, default={})
                if _cluster_labels is None:
                    _cluster_labels = {}
                # W607-DH: ``gather_cyclic_sccs`` substrate -- W155 SCC mine.
                _cyclic_sccs = _run_check_dh(
                    "gather_cyclic_sccs",
                    _gather_cyclic_sccs,
                    conn,
                    G,
                    _cluster_map,
                    _cluster_labels,
                    default=[],
                )
                if _cyclic_sccs is None:
                    _cyclic_sccs = []
                # W607-DH: ``emit_fingerprint_findings`` substrate boundary
                # -- W155 registry mirror with sqlite3.OperationalError
                # silent-no-op preserved. Generic exceptions surface via
                # the marker.
                try:
                    _emit_fingerprint_findings(
                        conn,
                        fp.get("clusters", []) or [],
                        _cyclic_sccs,
                        FINGERPRINT_DETECTOR_VERSION,
                    )
                    conn.commit()
                except sqlite3.OperationalError:
                    # findings table missing (pre-W89 schema) — silently no-op.
                    raise
                except Exception as _emit_exc:  # noqa: BLE001 -- W607-DH disclosure
                    _w607dh_warnings_out.append(
                        f"fingerprint_emit_fingerprint_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}"
                    )
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

        # W607-DH: ``compose_verdict`` substrate -- LAW 6 single-line
        # verdict string. A KeyError on the topology dict (e.g. when
        # ``compute_fingerprint`` raised and ``fp`` collapsed to {})
        # degrades to the explicit no-data floor so the envelope still
        # emits a non-empty verdict. The closure embeds every dict
        # lookup INSIDE the wrapped function (W978 5th discipline:
        # never index a possibly-poisoned dict at the kwarg-bind site).
        def _compose_verdict():
            topo_local = fp["topology"]
            n_layers_local = topo_local["layers"]
            modularity_local = topo_local["modularity"]
            fiedler_local = topo_local["fiedler"]
            tangle_local = topo_local["tangle_ratio"]
            return (
                topo_local,
                n_layers_local,
                modularity_local,
                fiedler_local,
                tangle_local,
                f"{n_layers_local} layers, modularity {modularity_local:.2f}, "
                f"fiedler {fiedler_local:.3f}, tangle {int(tangle_local * 100)}%",
            )

        verdict_bundle = _run_check_dh(
            "compose_verdict",
            _compose_verdict,
            default=({}, 0, 0.0, 0.0, 0.0, "no fingerprint data"),
        )
        if verdict_bundle is None:
            verdict_bundle = ({}, 0, 0.0, 0.0, 0.0, "no fingerprint data")
        topo, n_layers, modularity, fiedler, tangle, verdict = verdict_bundle

        # -- Export --
        if export_path:
            # Atomic write via temp + os.replace so an interrupted write
            # cannot leave a half-truncated JSON fixture in place — the
            # fingerprint export feeds `--compare` round-trips and CI
            # comparison, so partial-file silent corruption would surface
            # downstream as a confusing parse error.
            # W607-DH: ``write_export`` substrate -- W82.1 file-write
            # pattern. A raise (OSError, permissions) degrades to a
            # marker without crashing the rest of the envelope path.
            def _write_export():
                from roam.atomic_io import atomic_write_text

                atomic_write_text(
                    Path(export_path),
                    _json.dumps(fp, indent=2, default=str),
                )
                return True

            _wrote = _run_check_dh("write_export", _write_export, default=None)
            if _wrote and not json_mode and not compact:
                click.echo(f"Fingerprint written to {export_path}")

        # -- Compare --
        comparison = None
        if compare_path:
            # W607-DH: ``compare_fingerprints`` substrate -- drift
            # detection between two topology fingerprints. A raise
            # (malformed JSON, missing fields) degrades to comparison=None
            # so the standard envelope still emits without the comparison
            # section.
            def _compare():
                other_fp = _json.loads(Path(compare_path).read_text(encoding="utf-8"))
                return compare_fingerprints(fp, other_fp)

            comparison = _run_check_dh("compare_fingerprints", _compare, default=None)

        # -- JSON output --
        if json_mode:
            # W607-DH: ``serialize_envelope`` substrate -- JSON envelope
            # construction. A raise inside the Pattern 6 cluster slice,
            # the god-components definition import, or the summary
            # composition degrades to a minimal floor envelope so the
            # command still emits valid JSON. W978 5th discipline:
            # ``len()`` over ``_all_clusters`` lives INSIDE the closure,
            # not at the kwarg-bind site.
            def _build_envelope():
                from roam.quality.god_components import definition as _gc_def_local

                # Pattern 6 (response volume) — cap the clusters list to
                # _CLUSTERS_JSON_TOP_N. compute_fingerprint sorts clusters
                # descending by size so the kept slice is the meaningful
                # head; the trailing tail on roam-code itself is ~9200
                # singleton clusters (one-symbol "islands") that bloat the
                # envelope to >300K tokens. Disclose lineage via
                # ``clusters_total`` / ``clusters_truncated_to`` so a
                # downstream reader can tell "cap hit" apart from "graph
                # actually has 100 clusters". The on-disk export
                # (``--export``) is unaffected — it still emits the full
                # ``fp`` dict above so cross-repo compares stay lossless.
                _all_clusters_local = fp.get("clusters", []) or []
                _clusters_total_local = len(_all_clusters_local)
                _clusters_truncated_local = _clusters_total_local > _CLUSTERS_JSON_TOP_N
                _envelope_fp_local = dict(fp)
                if _clusters_truncated_local:
                    _envelope_fp_local["clusters"] = _all_clusters_local[:_CLUSTERS_JSON_TOP_N]
                    _envelope_fp_local["clusters_total"] = _clusters_total_local
                    _envelope_fp_local["clusters_truncated_to"] = _CLUSTERS_JSON_TOP_N

                summary_local = {
                    "verdict": verdict,
                    "layers": n_layers,
                    "modularity": modularity,
                    "fiedler": fiedler,
                    "tangle_ratio": tangle,
                    "god_components": fp.get("antipatterns", {}).get(
                        "god_components",
                        fp.get("antipatterns", {}).get("god_objects", 0),
                    ),
                    "god_components_definition": _gc_def_local(),
                    "clusters_total": _clusters_total_local,
                    "clusters_emitted": min(_clusters_total_local, _CLUSTERS_JSON_TOP_N),
                    "clusters_truncated": _clusters_truncated_local,
                }
                envelope_local = json_envelope(
                    "fingerprint",
                    summary=summary_local,
                    fingerprint=_envelope_fp_local,
                )
                return envelope_local

            envelope = _run_check_dh("serialize_envelope", _build_envelope, default=None)
            if envelope is None:
                # Floor envelope -- the W607-DH wrap surfaced a marker but
                # we still owe a structurally valid JSON envelope to the
                # caller. Pattern-2 silent-fallback discipline: name the
                # concrete state, not SAFE/completed.
                envelope = json_envelope(
                    "fingerprint",
                    summary={
                        "verdict": "envelope serialization failed",
                        "layers": n_layers,
                        "modularity": modularity,
                        "fiedler": fiedler,
                        "tangle_ratio": tangle,
                        "partial_success": True,
                        "state": "envelope_serialize_failed",
                    },
                )
            if comparison:
                envelope["comparison"] = comparison
                envelope.setdefault("summary", {})["similarity_score"] = comparison.get("similarity")
            # W607-DH: mirror substrate markers into BOTH the top-level
            # envelope ``warnings_out`` AND ``summary.warnings_out`` so
            # MCP consumers see disclosure regardless of which surface
            # they read. Flipping ``partial_success: True`` is the
            # Pattern-2 silent-fallback guard -- a degraded substrate
            # path must NOT be mistaken for a clean topology verdict.
            if _w607dh_warnings_out:
                envelope.setdefault("summary", {})["partial_success"] = True
                envelope.setdefault("summary", {})["warnings_out"] = list(_w607dh_warnings_out)
                envelope["warnings_out"] = list(_w607dh_warnings_out)
            click.echo(to_json(envelope))
            return

        # -- Compact output --
        if compact:
            sim_str = ""
            if comparison:
                sim_str = f"  similarity={comparison['similarity']:.0%}"
            click.echo(
                f"fingerprint  layers={n_layers}  mod={modularity:.3f}  "
                f"fiedler={fiedler:.4f}  tangle={tangle:.2f}  "
                f"gini={fp['pagerank_gini']:.2f}  "
                f"hubs={fp['hub_bridge_ratio']:.2f}"
                f"{sim_str}"
            )
            return

        # -- Full text output --
        click.echo(f"VERDICT: {verdict}")

        # Topology section
        click.echo("\nTOPOLOGY:")
        dist_str = _format_pct_list(topo["layer_distribution"]) if topo["layer_distribution"] else "n/a"
        click.echo(f"  Layers: {n_layers} (distribution: {dist_str})")
        click.echo(f"  Fiedler: {fiedler:.4f}")
        click.echo(f"  Modularity: {modularity:.3f}")
        click.echo(f"  Tangle ratio: {tangle:.2f}")
        click.echo(f"  Dependency direction: {fp['dependency_direction']}")

        # Clusters section (top 5)
        clusters = fp.get("clusters", [])
        if clusters:
            click.echo(f"\nCLUSTERS (top {min(5, len(clusters))}):")
            table_rows = []
            for c in clusters[:5]:
                table_rows.append(
                    [
                        c["label"],
                        f"{c['size_pct']:.0f}%",
                        f"{c['conductance']:.2f}",
                        str(c["layer"]),
                        c["pattern"],
                    ]
                )
            click.echo(
                format_table(
                    ["Label", "Size", "Conductance", "Layer", "Pattern"],
                    table_rows,
                )
            )

        # Signature section
        click.echo("\nSIGNATURE:")
        click.echo(f"  Hub/bridge ratio: {fp['hub_bridge_ratio']:.2f}")
        click.echo(f"  PageRank Gini: {fp['pagerank_gini']:.2f}")
        # Text/JSON parity fix: surface the canonical god_components count
        # (degree-thresholded, utility-aware, agrees with `roam health`).
        # The legacy `god_objects` stat (avg_degree*2) over-reports by ~40x
        # on roam-code (2153 vs 50) — keep it visible but labelled as legacy
        # so readers can spot the divergence rather than silently absorbing
        # the larger number. Mirrors the JSON envelope's summary.god_components.
        _ap = fp.get("antipatterns", {})
        _gc_count = _ap.get("god_components")
        if _gc_count is not None:
            click.echo(f"  God components: {_gc_count} (legacy god_objects: {_ap.get('god_objects', 0)})")
        else:
            click.echo(f"  God objects: {_ap.get('god_objects', 0)}")
        click.echo(f"  Cyclic clusters: {_ap.get('cyclic_clusters', 0)}")

        # Comparison section
        if comparison:
            sim = comparison["similarity"]
            dist = comparison["euclidean_distance"]
            click.echo(f"\nVERDICT: {sim:.0%} similar (topology distance: {dist:.2f})")
            click.echo("\nCOMPARISON:")
            cmp_rows = []
            for name, m in comparison["per_metric"].items():
                delta_str = f"{m['delta']:+.4f}" if isinstance(m["delta"], float) else f"{m['delta']:+d}"
                cmp_rows.append(
                    [
                        name,
                        str(round(m["this"], 4)),
                        str(round(m["other"], 4)),
                        delta_str,
                    ]
                )
            click.echo(
                format_table(
                    ["Metric", "This repo", "Other repo", "Delta"],
                    cmp_rows,
                )
            )
