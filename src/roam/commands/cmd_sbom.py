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
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click

from roam import __version__
from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json

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


def _trace_entry_reach(G, entries, nid):
    """return the entry-point node IDs that can reach ``nid``."""
    import networkx as nx

    reach: list = []
    for eid in entries:
        try:
            if nx.has_path(G, eid, nid):
                reach.append(eid)
        except (nx.NetworkXError, nx.NodeNotFound):
            continue
    return reach


def _build_norm_lookup(dep_names: list[str]) -> dict[str, list[str]]:
    """group orig dep names by their normalised key."""
    norm_to_dep: dict[str, list[str]] = {}
    for dep in dep_names:
        norm = _normalize_dep_name(dep)
        if norm:
            norm_to_dep.setdefault(norm, []).append(dep)
    return norm_to_dep


def _record_match(info: dict, display_name: str, G, entries, nid) -> None:
    """update a single dep's reachability record."""
    if display_name not in info["matched_symbols"]:
        info["matched_symbols"].append(display_name)
    if info["reachable"]:
        return
    for eid in _trace_entry_reach(G, entries, nid):
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
    ``_trace_entry_reach``, ``_build_norm_lookup``, ``_record_match``.
    """
    from roam.graph.builder import build_symbol_graph

    result: dict[str, dict] = {
        dep: {"reachable": False, "entry_points": [], "matched_symbols": []} for dep in dep_names
    }
    if not dep_names:
        return result
    try:
        G = build_symbol_graph(conn)
    except Exception:
        return result
    if not G.nodes:
        return result

    entries = [n for n in G.nodes() if G.in_degree(n) == 0]
    norm_to_dep = _build_norm_lookup(dep_names)

    for nid, data in G.nodes(data=True):
        qname, name_lower, file_path = _node_match_keys(data)
        for norm, orig_deps in norm_to_dep.items():
            if not _matches_dep(qname, name_lower, file_path, norm):
                continue
            display_name = data.get("qualified_name") or data.get("name", str(nid))
            for dep_name in orig_deps:
                _record_match(result[dep_name], display_name, G, entries, nid)
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
        component["properties"] = [
            {"name": "roam:ecosystem", "value": dep.ecosystem},
            {"name": "roam:pin_status", "value": dep.pin_status},
            {"name": "roam:risk_level", "value": dep.risk_level},
            {"name": "roam:source_file", "value": dep.source_file},
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

        # Reachability as annotation
        if reachability is not None:
            reach_info = reachability.get(dep.name, {})
            is_reachable = reach_info.get("reachable", False)
            entry_points = reach_info.get("entry_points", [])
            pkg["comment"] = f"roam:reachable={str(is_reachable).lower()}" + (
                f" roam:entry_points={';'.join(entry_points[:10])}" if entry_points else ""
            )

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
def sbom(ctx, fmt, output_path, no_reachability, aibom):
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

    try:
        project_root = find_project_root()
    except Exception:
        project_root = Path.cwd()

    project_name = project_root.name

    # Import supply-chain discovery (same data source as `roam supply-chain`)
    from roam.commands.cmd_supply_chain import discover_and_parse

    deps = discover_and_parse(project_root)

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

        # Graph-based reachability (may fail if index is unavailable)
        graph_reach: dict[str, dict] = {}
        try:
            ensure_index()
            with open_db(readonly=True) as conn:
                graph_reach = _compute_reachability(conn, dep_names)
        except Exception:
            graph_reach = {}

        # Filesystem-based reachability (cheap, independent of index)
        try:
            fs_reach = compute_filesystem_reachability(project_root, dep_names)
        except Exception:
            fs_reach = {}

        reachability = merge_reachability(graph_reach, fs_reach)
        # If both layers returned empty, fall back to None so callers can
        # tell reachability wasn't actually computed.
        if not reachability:
            reachability = None

    # Generate SBOM
    if fmt.lower() == "spdx":
        sbom_data = _generate_spdx(project_name, deps, reachability)
    else:
        sbom_data = _generate_cyclonedx(project_name, deps, reachability)

    # AIBOM extension (CycloneDX 1.7 only) — bind AI-authored commits to
    # indexed symbols. Required for EU AI Act Art. 50 disclosure.
    if aibom and fmt.lower() == "cyclonedx":
        try:
            from roam.security.aibom_extension import build_aibom_block

            ensure_index()
            with open_db(readonly=True) as conn:
                aibom_block = build_aibom_block(project_root, conn)
            sbom_data["aibom"] = aibom_block
        except Exception as exc:
            sbom_data["aibom"] = {"error": str(exc), "version": "0.1"}

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
    total_deps = len(deps)
    reachable_count = 0
    phantom_count = 0
    reachable_direct_count = 0
    reachable_heuristic_count = 0
    if reachability is not None:
        for v in reachability.values():
            if v.get("reachable"):
                reachable_count += 1
                if v.get("confidence") == "direct":
                    reachable_direct_count += 1
                else:
                    reachable_heuristic_count += 1
        phantom_count = total_deps - reachable_count

    if total_deps == 0:
        verdict = "No dependencies found -- empty SBOM generated"
    elif reachability is not None:
        verdict = (
            f"{reachable_count} reachable "
            f"({reachable_direct_count} direct, "
            f"{reachable_heuristic_count} heuristic), "
            f"{phantom_count} phantom"
        )
    else:
        verdict = f"SBOM generated: {total_deps} dependencies (reachability not computed)"

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
        envelope_kwargs: dict = {
            "summary": {
                "verdict": verdict,
                "format": fmt.lower(),
                "total_dependencies": total_deps,
                "reachable_count": reachable_count if reachability else None,
                "phantom_count": phantom_count if reachability else None,
                "reachable_direct_count": (reachable_direct_count if reachability else None),
                "reachable_heuristic_count": (reachable_heuristic_count if reachability else None),
                "reachability_computed": reachability is not None,
            },
            "budget": token_budget,
            "sbom": sbom_data,
        }
        if explicit_facts is not None:
            envelope_kwargs["agent_contract"] = {"facts": explicit_facts}
        envelope = json_envelope("sbom", **envelope_kwargs)
        output_text = to_json(envelope)
    else:
        output_text = to_json(sbom_data)

    if output_path:
        out = Path(output_path)
        out.write_text(output_text, encoding="utf-8")
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
