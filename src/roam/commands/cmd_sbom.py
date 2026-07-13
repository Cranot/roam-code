"""Generate Software Bill of Materials (SBOM) with call-graph reachability enrichment.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because sbom outputs are Software Bill of Materials documents
(CycloneDX/SPDX) — not per-location violations. SARIF is reserved for
findings with file:line coordinates; sbom's primary deliverable is the
CycloneDX/SPDX SBOM document. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket C propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click

from roam import __version__
from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import StaleDbDirError, find_project_root, open_db
from roam.output.formatter import json_envelope, to_json

_SBOM_BOUNDARY_EXCEPTIONS = (
    click.ClickException,
    sqlite3.DatabaseError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

# ---------------------------------------------------------------------------
# Ecosystem -> Package URL (purl) type mapping
# ---------------------------------------------------------------------------
_ECOSYSTEM_PURL_TYPE: dict[str, str] = {
    "python": "pypi",
    "javascript": "npm",
    "go": "golang",
    "rust": "cargo",
    "java": "maven",
    "ruby": "gem",
    "php": "composer",
}


# ---------------------------------------------------------------------------
# Reachability helpers
# ---------------------------------------------------------------------------


def _normalize_dep_name(name: str) -> str:
    """Normalize a dependency name for fuzzy matching against symbol references.

    Handles common conventions: underscores/hyphens, case differences,
    namespace prefixes (e.g., ``@scope/pkg`` -> ``pkg``).
    """
    # Strip npm scopes
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    # Strip Maven group IDs
    if ":" in name:
        name = name.rsplit(":", 1)[-1]
    return name.lower().replace("-", "_").replace(".", "_")


def _node_match_keys(data) -> tuple[str, str, str]:
    """pre-normalise the three node fields used for dep matching."""
    qname = (data.get("qualified_name") or "").lower().replace("-", "_").replace(".", "_")
    name_lower = (data.get("name") or "").lower().replace("-", "_").replace(".", "_")
    file_path = (data.get("file_path") or "").lower().replace("-", "_").replace(".", "_")
    return qname, name_lower, file_path


def _matches_dep(qname: str, name_lower: str, file_path: str, norm: str) -> bool:
    """predicate version of the inner dep-match check."""
    if qname and (qname.startswith(norm + "_") or qname.startswith(norm + "/") or qname == norm):
        return True
    if norm in file_path:
        return True
    if name_lower == norm:
        return True
    return False


def _entry_ancestors(G, nid, entry_set: set) -> set:
    """Return the entry-point node IDs (subset of ``entry_set``) that can reach ``nid``.

    A node ``eid`` can reach ``nid`` iff ``eid`` is an ancestor of ``nid``
    (or ``eid == nid``). Computed via a SINGLE reverse traversal (BFS over
    predecessors) seeded at ``nid`` — O(V+E) per matched node — instead of
    the historical per-(entry, node) ``nx.has_path`` probe which was
    O(entries x (V+E)) per node. The reverse-reachable closure is intersected
    with the entry set to recover the reaching entries.

    The result is set-valued; callers iterate the canonical entry order to
    preserve deterministic ``entry_points`` ordering.
    """
    if nid not in G:
        return set()
    # Reverse BFS: walk predecessors from nid to find all ancestors. The
    # closure includes nid itself, so an entry that *is* the matched node
    # (trivially reachable from itself, like the old has_path with eid==nid)
    # is captured too.
    visited = {nid}
    stack = [nid]
    while stack:
        cur = stack.pop()
        for pred in G.predecessors(cur):
            if pred not in visited:
                visited.add(pred)
                stack.append(pred)
    return visited & entry_set


def _trace_entry_reach(G, entries, nid):
    """Return the entry-point node IDs that can reach ``nid``, in ``entries`` order.

    ``entries`` is the canonical ordered list of in-degree-0 nodes. Membership
    is resolved via a single reverse traversal (see ``_entry_ancestors``) and
    filtered back through ``entries`` so ordering matches the historical
    per-entry ``nx.has_path`` scan exactly.
    """
    reaching = _entry_ancestors(G, nid, set(entries))
    return [eid for eid in entries if eid in reaching]


def _build_norm_lookup(dep_names: list[str]) -> dict[str, list[str]]:
    """group orig dep names by their normalised key."""
    norm_to_dep: dict[str, list[str]] = {}
    for dep in dep_names:
        norm = _normalize_dep_name(dep)
        if norm:
            norm_to_dep.setdefault(norm, []).append(dep)
    return norm_to_dep


def _record_match(info: dict, display_name: str, G, entries, nid, entry_set: set | None = None) -> None:
    """update a single dep's reachability record.

    ``entry_set`` is the precomputed in-degree-0 node set (passed by the
    orchestrator to avoid rebuilding it per matched node). When omitted it is
    derived from ``entries`` so the helper stays callable standalone.
    """
    if display_name not in info["matched_symbols"]:
        info["matched_symbols"].append(display_name)
    if info["reachable"]:
        return
    if entry_set is None:
        entry_set = set(entries)
    reaching = _entry_ancestors(G, nid, entry_set)
    for eid in entries:
        if eid not in reaching:
            continue
        info["reachable"] = True
        entry_name = G.nodes[eid].get("qualified_name") or G.nodes[eid].get("name", str(eid))
        if entry_name not in info["entry_points"]:
            info["entry_points"].append(entry_name)


def _compute_reachability(conn, dep_names: list[str]) -> dict[str, dict]:
    """Check which dependencies are referenced in the codebase symbol graph.

    For each dependency, look for import references or qualified-name
    matches in the ``edges`` / ``symbols`` tables. When a match is
    found, trace entry points (in-degree 0) that can reach the matched
    symbol.

    Returns ``{dep_name: {"reachable": bool, "entry_points": [...],
    "matched_symbols": [...]}}``.

    orchestrator only. this function had cc=150
    and nesting depth 8 (the deepest in the repo). Per-symbol logic now
    lives in ``_node_match_keys``, ``_matches_dep``,
    ``_entry_ancestors`` / ``_trace_entry_reach``, ``_build_norm_lookup``,
    ``_record_match``.

    Reachability is computed via a per-matched-node reverse traversal
    (``_entry_ancestors``) rather than a per-(entry, node) ``nx.has_path``
    probe: the old O(entries x matched x (V+E)) loop went quadratic on
    large repos (thousands of in-degree-0 entries). The reverse BFS is
    O(V+E) per matched node and yields an identical reaching-entry set.
    """
    from roam.graph.builder import build_symbol_graph

    result: dict[str, dict] = {
        dep: {"reachable": False, "entry_points": [], "matched_symbols": []} for dep in dep_names
    }
    if not dep_names:
        return result
    try:
        G = build_symbol_graph(conn)
    except sqlite3.Error:
        return result
    if not G.nodes:
        return result

    entries = [n for n in G.nodes() if G.in_degree(n) == 0]
    entry_set = set(entries)
    norm_to_dep = _build_norm_lookup(dep_names)

    for nid, data in G.nodes(data=True):
        qname, name_lower, file_path = _node_match_keys(data)
        for norm, orig_deps in norm_to_dep.items():
            if not _matches_dep(qname, name_lower, file_path, norm):
                continue
            display_name = data.get("qualified_name") or data.get("name", str(nid))
            for dep_name in orig_deps:
                _record_match(result[dep_name], display_name, G, entries, nid, entry_set)
    return result


# ---------------------------------------------------------------------------
# PURL generation
# ---------------------------------------------------------------------------


def _make_purl(ecosystem: str, name: str, version: str) -> str:
    """Build a Package URL (purl) string per the purl spec."""
    purl_type = _ECOSYSTEM_PURL_TYPE.get(ecosystem, ecosystem)
    # Maven uses group:artifact -- encode as namespace/name
    if ecosystem == "java" and ":" in name:
        group, artifact = name.split(":", 1)
        return f"pkg:{purl_type}/{group}/{artifact}" + (f"@{version}" if version else "")
    safe_name = name.replace("/", "%2F") if ecosystem != "javascript" else name
    return f"pkg:{purl_type}/{safe_name}" + (f"@{version}" if version else "")


def _version_from_spec(spec: str) -> str:
    """Extract a concrete version from a version specifier.

    Best-effort: strips comparison operators and returns the first
    version-like string.  Returns empty string if no version can be
    extracted.
    """
    if not spec:
        return ""
    # Strip common prefixes
    cleaned = re.sub(r"^[~^>=<!]+", "", spec.strip())
    # Take only the first version token
    m = re.match(r"([0-9][0-9a-zA-Z._\-]*)", cleaned)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# CycloneDX 1.5 generation
# ---------------------------------------------------------------------------


def _generate_cyclonedx(
    project_name: str,
    deps: list,
    reachability: dict[str, dict] | None,
) -> dict:
    """Generate a CycloneDX 1.5 JSON SBOM.

    Parameters
    ----------
    project_name:
        Name of the project being analyzed.
    deps:
        List of Dependency namedtuples from the supply-chain module.
    reachability:
        Optional dict mapping dep name -> reachability info.  ``None``
        means reachability analysis was skipped.
    """
    serial = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # Metadata
    metadata = {
        "timestamp": ts,
        "tools": {
            "components": [
                {
                    "type": "application",
                    "name": "roam-code",
                    "version": __version__,
                    "description": "Codebase comprehension tool for AI coding assistants",
                    "externalReferences": [
                        {
                            "type": "website",
                            "url": "https://github.com/AbanteAI/roam-code",
                        }
                    ],
                }
            ]
        },
        "component": {
            "type": "application",
            "name": project_name,
            "bom-ref": f"pkg:generic/{project_name}",
        },
    }

    # Components
    components: list[dict] = []
    dep_tree: list[dict] = []

    # Root dependency entry
    root_ref = f"pkg:generic/{project_name}"
    root_depends: list[str] = []

    for dep in deps:
        version = _version_from_spec(dep.version_spec)
        purl = _make_purl(dep.ecosystem, dep.name, version)
        bom_ref = purl

        component: dict = {
            "type": "library",
            "name": dep.name,
            "version": version,
            "purl": purl,
            "bom-ref": bom_ref,
            "scope": "optional" if dep.is_dev else "required",
        }

        # Add ecosystem as external reference
        # W1075: stamp the original version_spec so SBOM consumers can
        # recover the full constraint (e.g. ">=0.6,<1.6.3"). Without this,
        # _version_from_spec strips comparison operators and emits only the
        # first numeric token, silently dropping upper bounds that exclude
        # known-broken releases — a supply-chain correctness gap.
        component["properties"] = [
            {"name": "roam:ecosystem", "value": dep.ecosystem},
            {"name": "roam:pin_status", "value": dep.pin_status},
            {"name": "roam:risk_level", "value": dep.risk_level},
            {"name": "roam:source_file", "value": dep.source_file},
            {"name": "roam:version_spec", "value": dep.version_spec or ""},
        ]

        # Reachability enrichment
        if reachability is not None:
            reach_info = reachability.get(dep.name, {})
            is_reachable = reach_info.get("reachable", False)
            entry_points = reach_info.get("entry_points", [])
            matched_syms = reach_info.get("matched_symbols", [])
            sources = reach_info.get("sources", [])
            confidence = reach_info.get("confidence", "indirect")

            component["properties"].extend(
                [
                    {"name": "roam:reachable", "value": str(is_reachable).lower()},
                    {"name": "roam:entry_points", "value": "; ".join(entry_points[:10]) if entry_points else ""},
                    {"name": "roam:matched_symbols", "value": str(len(matched_syms))},
                    {"name": "roam:reach_confidence", "value": confidence},
                    {"name": "roam:reach_sources", "value": "; ".join(sources[:5]) if sources else ""},
                ]
            )

        components.append(component)
        root_depends.append(bom_ref)

        # Each dep entry in the dependency tree (leaf -- no transitive deps known)
        dep_tree.append({"ref": bom_ref, "dependsOn": []})

    # Root dependency entry
    dep_tree.insert(0, {"ref": root_ref, "dependsOn": root_depends})

    bom: dict = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "serialNumber": f"urn:uuid:{serial}",
        "metadata": metadata,
        "components": components,
        "dependencies": dep_tree,
    }

    return bom


# ---------------------------------------------------------------------------
# SPDX 2.3 generation
# ---------------------------------------------------------------------------


def _generate_spdx(
    project_name: str,
    deps: list,
    reachability: dict[str, dict] | None,
) -> dict:
    """Generate a basic SPDX 2.3 JSON SBOM.

    Parameters
    ----------
    project_name:
        Name of the project being analyzed.
    deps:
        List of Dependency namedtuples from the supply-chain module.
    reachability:
        Optional dict mapping dep name -> reachability info.
    """
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    doc_namespace = f"https://roam-code.com/spdx/{project_name}/{uuid.uuid4()}"

    packages: list[dict] = []
    relationships: list[dict] = []

    # Root package
    root_spdx_id = "SPDXRef-RootPackage"
    packages.append(
        {
            "SPDXID": root_spdx_id,
            "name": project_name,
            "versionInfo": "",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
        }
    )
    relationships.append(
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": root_spdx_id,
        }
    )

    for i, dep in enumerate(deps):
        spdx_id = f"SPDXRef-Package-{i}"
        version = _version_from_spec(dep.version_spec)
        purl = _make_purl(dep.ecosystem, dep.name, version)

        pkg: dict = {
            "SPDXID": spdx_id,
            "name": dep.name,
            "versionInfo": version or "NOASSERTION",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": purl,
                }
            ],
        }

        # W1075: stamp the full version_spec on the SPDX comment so
        # downstream tooling can recover constraints like ">=0.6,<1.6.3"
        # that versionInfo (lower bound only) silently drops.
        comment_parts: list[str] = []
        if dep.version_spec and dep.version_spec != version:
            comment_parts.append(f"roam:version_spec={dep.version_spec}")
        # Reachability as annotation
        if reachability is not None:
            reach_info = reachability.get(dep.name, {})
            is_reachable = reach_info.get("reachable", False)
            entry_points = reach_info.get("entry_points", [])
            comment_parts.append(f"roam:reachable={str(is_reachable).lower()}")
            if entry_points:
                comment_parts.append(f"roam:entry_points={';'.join(entry_points[:10])}")
        if comment_parts:
            pkg["comment"] = " ".join(comment_parts)

        packages.append(pkg)
        relationships.append(
            {
                "spdxElementId": root_spdx_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": spdx_id,
            }
        )

    spdx: dict = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{project_name}-sbom",
        "documentNamespace": doc_namespace,
        "creationInfo": {
            "created": ts,
            "creators": [f"Tool: roam-code-{__version__}"],
            "licenseListVersion": "3.19",
        },
        "documentDescribes": [root_spdx_id],
        "packages": packages,
        "relationships": relationships,
    }

    return spdx


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="sbom",
    category="reports",
    summary="Generate a Software Bill of Materials (SBOM) enriched with call-graph reachability",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "compliance"),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("sbom")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["cyclonedx", "spdx"], case_sensitive=False),
    default="cyclonedx",
    show_default=True,
    help="SBOM output format",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default=None,
    type=click.Path(),
    help="Write SBOM to file instead of stdout",
)
@click.option(
    "--no-reachability",
    is_flag=True,
    default=False,
    help="Skip call-graph reachability analysis (faster)",
)
@click.option(
    "--aibom",
    is_flag=True,
    default=False,
    help=(
        "Embed the AIBOM extension (CycloneDX only) — bind AI-authored "
        "commits (mined via committer email + Co-Authored-By trailers + "
        "AI-keyword scan) to the indexed symbols they touched. Required "
        "for EU AI Act Art. 50 disclosure (effective 2026-08-02)."
    ),
)
@click.pass_context
def sbom_cmd(ctx, fmt, output_path, no_reachability, aibom):
    """Generate a Software Bill of Materials (SBOM) enriched with call-graph reachability.

    Produces CycloneDX 1.5 or SPDX 2.3 JSON output.  Each dependency is
    annotated with ``roam:reachable`` (whether any code path reaches symbols
    from that package) and ``roam:entry_points`` (which entry points reach it).

    This reachability enrichment is unique to roam-code -- it lets you
    distinguish phantom dependencies from those actually exercised at runtime.

    Unlike ``supply-chain`` (which shows a developer-facing risk dashboard),
    this command produces machine-readable artifacts for external tools like
    Dependency-Track, FOSSA, and GitHub Dependency Review.

    \b
    Examples:
        roam sbom                                 # CycloneDX to stdout
        roam sbom --format spdx -o sbom.json      # SPDX to file
        roam sbom --no-reachability                # skip call-graph analysis
        roam --json sbom                           # wrapped in roam JSON envelope
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    # W607-AM -- substrate-boundary plumbing for the SBOM EMIT producer leg
    # of the W805 cross-artifact-consistency family. Prior to W607-AM,
    # expected runtime failures in find_project_root / discover_and_parse /
    # compute_graph_reachability / compute_filesystem_reachability /
    # merge_reachability / generate_cyclonedx / generate_spdx /
    # build_aibom_block / serialize_sbom / write_sbom crashed the whole
    # SBOM emit wholesale. Each is wrapped via ``_run_check_am`` so a raise
    # becomes a structured ``sbom_<phase>_failed:<exc_class>:<detail>``
    # marker on ``_w607am_warnings_out`` -- the envelope still emits cleanly
    # with whatever signal the remaining substrates produced.
    #
    # cmd_sbom is the SBOM EMIT producer on the W805 cross-artifact family
    # (sibling of cmd_supply_chain W607-AK which is the consumer/projection
    # side). cmd_sbom produces the CycloneDX/SPDX artifact downstream
    # consumers use; the W805 6-artifact identity-coherence story would
    # naturally extend to a 7th SBOM-with-content-hash-binding artifact and
    # W607-AM gives the runtime-raise complement to that future pin.
    #
    # Marker prefix discipline: every W607-AM substrate marker uses the
    # canonical ``sbom_<phase>_failed:<exc_class>:<detail>`` shape. cmd_sbom
    # has NO pre-existing warnings_out channel -- W607-AM is FRESH: the
    # accumulator-based markers become the canonical ``summary.warnings_out``
    # field outright.
    _w607am_warnings_out: list[str] = []

    def _run_check_am(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AM marker emission.

        On a clean call the result is returned as-is. On an expected
        boundary failure, surface a ``sbom_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607am_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except _SBOM_BOUNDARY_EXCEPTIONS as exc:
            _w607am_warnings_out.append(f"sbom_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-CG -- ADDITIVE aggregation-phase plumbing on top of the W607-AM
    # substrate-CALL markers. W607-AM already wrapped the substrate-helper
    # boundaries on the EMIT path (find_project_root / discover_and_parse /
    # compute_graph_reachability / compute_filesystem_reachability /
    # merge_reachability / generate_cyclonedx / generate_spdx /
    # build_aibom_block / serialize_sbom_json / write_sbom_to_disk);
    # W607-CG extends marker coverage to the AGGREGATION-PHASE
    # boundaries that W607-AM left unguarded:
    #
    #   - ``compute_predicate``    -- per-field extraction of the
    #                                 reachability metric counts
    #                                 (total_deps / reachable_count /
    #                                 phantom_count / reachable_direct_count
    #                                 / reachable_heuristic_count) used to
    #                                 compose the verdict string + envelope.
    #                                 A future ``reachability`` schema
    #                                 refactor that drops/renames one of
    #                                 these per-dep ``confidence`` /
    #                                 ``reachable`` keys would otherwise
    #                                 crash the envelope post-build.
    #   - ``compute_verdict``      -- verdict string assembly based on
    #                                 total_deps + reachability presence
    #                                 (empty-deps / reachability-ran /
    #                                 no-reachability branches). Floor to a
    #                                 literal "SBOM analysis completed"
    #                                 string per LAW 6 (standalone-parse)
    #                                 + W978 first-hypothesis discipline
    #                                 (no re-interpolation of the same
    #                                 values that just raised).
    #   - ``serialize_envelope``   -- ``json_envelope("sbom", ...)``
    #                                 projection (downstream contract
    #                                 changes / shape regressions). Mirror
    #                                 of cmd_supply_chain W607-CD
    #                                 serialize_envelope floor pattern.
    #
    # cmd_sbom is the SBOM EMIT producer leg of the W805 cross-artifact-
    # consistency family. Closes the SBOM/VEX PROJECTION chain alongside
    # the now-complete attestation quartet (cmd_attest W607-AD/BT,
    # cmd_pr_bundle W607-AE/BW, cmd_cga W607-AF/BZ, cmd_supply_chain
    # W607-AK/CD). The W607-CG markers fire AT RUNTIME when an
    # aggregation-phase boundary raises, complementing the W805
    # xfail-strict pins that catch structural inconsistency at the
    # dataclass level.
    #
    # Marker family ``sbom_*`` -- same family as W607-AM (additive, not
    # a separate prefix). Empty bucket -> byte-identical envelope on
    # the success path.
    #
    # No ``auto_log`` phase: cmd_sbom has no active-run ledger write at
    # present, so the W607-BZ 4-phase set drops to 3 phases here
    # (compute_predicate / compute_verdict / serialize_envelope). Same
    # marker shape contract, narrower phase set.
    _w607cg_warnings_out: list[str] = []

    def _run_check_cg(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CG marker emission.

        Mirror of ``_run_check_am`` shape (same ``sbom_<phase>_failed:``
        marker family) but writes into ``_w607cg_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except _SBOM_BOUNDARY_EXCEPTIONS as exc:
            _w607cg_warnings_out.append(f"sbom_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    project_root = _run_check_am("find_project_root", find_project_root, default=None)
    if project_root is None:
        project_root = Path.cwd()

    project_name = project_root.name

    # Import supply-chain discovery (same data source as `roam supply-chain`)
    from roam.commands.cmd_supply_chain import discover_and_parse

    deps = _run_check_am("discover_and_parse", discover_and_parse, project_root, default=[])

    # Reachability analysis
    # Graph-based check (symbol graph reachability) AND filesystem-based
    # heuristics (CSS @import, dynamic import(), config files, package.json
    # scripts, known TS loaders). The filesystem layer closes 5 systematic
    # false-positive categories the graph misses.
    reachability: dict[str, dict] | None = None
    if not no_reachability and deps:
        from roam.security.sbom_reachability import (
            compute_filesystem_reachability,
            merge_reachability,
        )

        dep_names = [d.name for d in deps]

        # Graph-based reachability (may fail if index is unavailable).
        # W607-AM wraps the inner _compute_reachability call so a raise
        # there becomes a structured marker rather than crashing the SBOM
        # build. ensure_index() and open_db() stay outside the wrap to
        # preserve the existing degrade-on-no-index behaviour.
        graph_reach: dict[str, dict] = {}
        try:
            ensure_index()
            with open_db(readonly=True) as conn:
                graph_reach = _run_check_am(
                    "compute_graph_reachability",
                    _compute_reachability,
                    conn,
                    dep_names,
                    default={},
                )
        except (click.ClickException, sqlite3.DatabaseError, OSError, StaleDbDirError):
            graph_reach = {}

        # Filesystem-based reachability (cheap, independent of index).
        # ``fs_scan_meta`` carries scan-completeness out-of-band (never as a
        # pseudo-dep inside the {dep: info} result); a truncated scan means
        # absence-of-import evidence is incomplete, so disclose it via the
        # established W607-AM warnings channel (Pattern-2: no silent caps).
        fs_scan_meta: dict = {}
        fs_reach = _run_check_am(
            "compute_filesystem_reachability",
            compute_filesystem_reachability,
            project_root,
            dep_names,
            default={},
            meta_out=fs_scan_meta,
        )
        if fs_scan_meta.get("truncated"):
            _w607am_warnings_out.append(
                "reachability_scan_truncated:caps_hit="
                + ",".join(str(c) for c in fs_scan_meta.get("caps_hit", []))
                + " — file cap reached; unimported/phantom verdicts may under-report"
            )

        reachability = _run_check_am(
            "merge_reachability",
            merge_reachability,
            graph_reach,
            fs_reach,
            default={},
        )
        # If both layers returned empty (or merge raised), fall back to None
        # so callers can tell reachability wasn't actually computed.
        if not reachability:
            reachability = None

    # Generate SBOM
    if fmt.lower() == "spdx":
        sbom_data = _run_check_am(
            "generate_spdx",
            _generate_spdx,
            project_name,
            deps,
            reachability,
            default=None,
        )
    else:
        sbom_data = _run_check_am(
            "generate_cyclonedx",
            _generate_cyclonedx,
            project_name,
            deps,
            reachability,
            default=None,
        )

    # AIBOM extension (CycloneDX 1.7 only) — bind AI-authored commits to
    # indexed symbols. Required for EU AI Act Art. 50 disclosure.
    if aibom and fmt.lower() == "cyclonedx" and sbom_data is not None:
        from roam.security.aibom_extension import build_aibom_block

        def _build_aibom_block(_project_root):
            ensure_index()
            with open_db(readonly=True) as conn:
                return build_aibom_block(_project_root, conn)

        aibom_block = _run_check_am(
            "build_aibom_block",
            _build_aibom_block,
            project_root,
            default=None,
        )
        if aibom_block is not None:
            sbom_data["aibom"] = aibom_block
        else:
            # Preserve pre-W607-AM error disclosure on the SBOM artifact
            # (in addition to the W607-AM marker on warnings_out) so SBOM
            # consumers that don't read warnings_out still see the gap.
            sbom_data["aibom"] = {"error": "build_aibom_block_failed", "version": "0.1"}

    # Build summary for verdict / JSON envelope.
    #
    # W18.2 LAW 12 — confidence bucketing. The reachability dict tags every
    # match with a 6-level confidence label (``direct`` > ``config_import`` >
    # ``script_consumer`` > ``loader`` > ``css_import`` > ``dynamic_import``
    # > ``indirect``). Collapse to 2 macro-buckets in the verdict so an
    # agent can tell a graph-traced hit apart from a filesystem deduction:
    #
    # * ``direct`` — graph-traced reach (the symbol graph had a path)
    # * ``heuristic`` — everything else (config / script / loader / css /
    #   dynamic-import deductions)
    #
    # ``reachable_count`` / ``phantom_count`` stay so pre-W18.2 consumers
    # keep working; the two new counts are additive.
    #
    # W607-CG -- compute_predicate boundary. Wraps the per-field extraction
    # of the reachability metric counts so a future ``reachability`` schema
    # refactor that renames the per-dep ``confidence`` / ``reachable`` keys
    # surfaces a marker rather than crashing the envelope. Floor to
    # documented empty-shape ints matching the happy-path shape so
    # downstream verdict/summary fields stay non-null. Mirror of
    # cmd_supply_chain W607-CD compute_predicate pattern.
    def _compute_predicate_fields(_deps, _reachability) -> dict:
        _total = len(_deps)
        _reach = 0
        _phantom = 0
        _direct = 0
        _heuristic = 0
        if _reachability is not None:
            for v in _reachability.values():
                if v.get("reachable"):
                    _reach += 1
                    if v.get("confidence") == "direct":
                        _direct += 1
                    else:
                        _heuristic += 1
            _phantom = _total - _reach
        return {
            "total_deps": _total,
            "reachable_count": _reach,
            "phantom_count": _phantom,
            "reachable_direct_count": _direct,
            "reachable_heuristic_count": _heuristic,
        }

    _pred_fields = _run_check_cg(
        "compute_predicate",
        _compute_predicate_fields,
        deps,
        reachability,
        default={
            "total_deps": 0,
            "reachable_count": 0,
            "phantom_count": 0,
            "reachable_direct_count": 0,
            "reachable_heuristic_count": 0,
        },
    )
    total_deps = _pred_fields["total_deps"]
    reachable_count = _pred_fields["reachable_count"]
    phantom_count = _pred_fields["phantom_count"]
    reachable_direct_count = _pred_fields["reachable_direct_count"]
    reachable_heuristic_count = _pred_fields["reachable_heuristic_count"]

    # W607-CG -- compute_verdict boundary. Wraps the verdict-string assembly
    # so a downstream f-string refactor (e.g. a __format__-raising sentinel
    # injected into one of the count fields) surfaces a marker rather than
    # crashing the envelope. Floor must NOT re-interpolate the same values
    # that tripped the closure (W978 first-hypothesis discipline: an
    # __index__-raising sentinel under test would re-raise inside the
    # default f-string). Use a literal ``"SBOM analysis completed"`` floor
    # instead (LAW 6 still holds: the line works standalone). Mirror of
    # cmd_supply_chain W607-CD compute_verdict pattern.
    def _build_verdict_str(fields: dict, reachability_present: bool) -> str:
        _total = fields["total_deps"]
        if _total == 0:
            return "No dependencies found -- empty SBOM generated"
        if reachability_present:
            _r = fields["reachable_count"]
            _d = fields["reachable_direct_count"]
            _h = fields["reachable_heuristic_count"]
            _p = fields["phantom_count"]
            return f"{_r} reachable ({_d} direct, {_h} heuristic), {_p} phantom"
        return f"SBOM generated: {_total} dependencies (reachability not computed)"

    verdict = _run_check_cg(
        "compute_verdict",
        _build_verdict_str,
        _pred_fields,
        reachability is not None,
        default="SBOM analysis completed",
    )

    # Concrete-noun facts (LAW 4): each fact names the analytical subject
    # in the body so an agent reading only the facts list knows what the
    # count refers to. Built only when reachability ran — when it didn't,
    # the auto-derived facts cover the no-data branch.
    explicit_facts: list[str] | None = None
    if reachability is not None and total_deps > 0:
        explicit_facts = [
            verdict,
            f"{reachable_direct_count} packages directly imported from source",
            f"{reachable_heuristic_count} packages reached via heuristic (config files, scripts, loaders)",
            f"{phantom_count} phantom packages (deps in package.json with no consumer)",
        ]

    # Output
    if json_mode:
        _summary: dict = {
            "verdict": verdict,
            "format": fmt.lower(),
            "total_dependencies": total_deps,
            "reachable_count": reachable_count if reachability else None,
            "phantom_count": phantom_count if reachability else None,
            "reachable_direct_count": (reachable_direct_count if reachability else None),
            "reachable_heuristic_count": (reachable_heuristic_count if reachability else None),
            "reachability_computed": reachability is not None,
        }
        # W607-AM / W607-CG: surface substrate-CALL markers AND aggregation-
        # phase markers on BOTH the canonical ``summary.warnings_out`` and
        # the top-level ``warnings_out`` so an agent reading only one of the
        # two fields still sees the failure. ``partial_success`` flips
        # whenever ANY bucket is non-empty -- W805 invariant: SBOM emit
        # never collapses to a silent SAFE verdict when any of the EMIT-side
        # substrates raised. Both buckets share the canonical ``sbom_*``
        # marker family; the additive W607-CG bucket stays distinguishable
        # via its phase names (``compute_predicate`` / ``compute_verdict`` /
        # ``serialize_envelope``).
        _combined_warnings_out = list(_w607am_warnings_out) + list(_w607cg_warnings_out)
        if _combined_warnings_out:
            _summary["warnings_out"] = list(_combined_warnings_out)
            _summary["partial_success"] = True
        envelope_kwargs: dict = {
            "summary": _summary,
            "budget": token_budget,
            "sbom": sbom_data,
        }
        if explicit_facts is not None:
            envelope_kwargs["agent_contract"] = {"facts": explicit_facts}
        if _combined_warnings_out:
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)

        # W607-CG -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("sbom", ...)`` would otherwise crash AFTER
        # all substrate + aggregation signals were already gathered. Floor
        # to a minimal envelope stub so consumers still receive a parseable
        # JSON object with the marker attached + the canonical command
        # name. Mirror of cmd_supply_chain's W607-CD serialize_envelope
        # floor pattern.
        _envelope_floor: dict = {
            "command": "sbom",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": verdict,
                "partial_success": True,
                "warnings_out": list(_combined_warnings_out),
            },
            "warnings_out": list(_combined_warnings_out),
        }
        envelope = _run_check_cg(
            "serialize_envelope",
            json_envelope,
            "sbom",
            default=_envelope_floor,
            **envelope_kwargs,
        )
        # W607-CG -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``sbom_serialize_envelope_failed:`` marker was appended to
        # ``_w607cg_warnings_out`` and the floor stub carries only the
        # pre-raise combined list. Rebuild the floor stub's warnings_out
        # so the new marker reaches the JSON output. Clean path ->
        # envelope is the real json_envelope return value, no rebuild
        # needed.
        if envelope is _envelope_floor and _w607cg_warnings_out:
            _combined_warnings_out = list(_w607am_warnings_out) + list(_w607cg_warnings_out)
            _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings_out)
            _envelope_floor["warnings_out"] = list(_combined_warnings_out)
            envelope = _envelope_floor

        output_text = _run_check_am(
            "serialize_sbom_json",
            to_json,
            envelope,
            default="{}",
        )
        # If serialize_sbom_json raised AFTER envelope build, the bucket gets
        # a marker but the envelope itself was already produced. Re-serialize
        # one more time so the disclosed marker rides on the output. This is
        # the W805 / Pattern-1 variant-D safety net: the envelope's marker
        # disclosure must reach the consumer rather than be swallowed by the
        # serializer.
        if output_text == "{}" and (_w607am_warnings_out or _w607cg_warnings_out):
            _combined_warnings_out = list(_w607am_warnings_out) + list(_w607cg_warnings_out)
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)
            envelope_kwargs["summary"]["warnings_out"] = list(_combined_warnings_out)
            envelope = json_envelope("sbom", **envelope_kwargs)
            try:
                output_text = to_json(envelope)
            except (TypeError, ValueError):
                output_text = "{}"
    else:
        output_text = _run_check_am(
            "serialize_sbom_json",
            to_json,
            sbom_data if sbom_data is not None else {},
            default="{}",
        )

    if output_path:
        out = Path(output_path)
        _run_check_am(
            "write_sbom_to_disk",
            out.write_text,
            output_text,
            encoding="utf-8",
        )
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"Written to {out}")
    else:
        if json_mode:
            click.echo(output_text)
        else:
            # Text mode with no output path: print verdict header + SBOM
            click.echo(f"VERDICT: {verdict}")
            click.echo()
            click.echo(output_text)


# Backwards-compatible import name for tests and external callers that invoke
# the Click command object directly.
sbom = sbom_cmd
