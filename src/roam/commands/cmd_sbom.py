"""Generate Software Bill of Materials (SBOM) with call-graph reachability enrichment."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click

from roam import __version__
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


def _compute_reachability(conn, dep_names: list[str]) -> dict[str, dict]:
    """Check which dependencies are referenced in the codebase symbol graph.

    For each dependency, look for import references or qualified-name matches
    in the ``edges`` / ``symbols`` tables.  When a match is found, trace
    entry points (in-degree 0) that can reach the matched symbol.

    Returns ``{dep_name: {"reachable": bool, "entry_points": [str, ...], "matched_symbols": [str, ...]}}``
    """
    import networkx as nx

    from roam.graph.builder import build_symbol_graph

    result: dict[str, dict] = {}

    # Pre-populate with defaults
    for dep in dep_names:
        result[dep] = {"reachable": False, "entry_points": [], "matched_symbols": []}

    if not dep_names:
        return result

    # Build graph once
    try:
        G = build_symbol_graph(conn)
    except Exception:
        return result

    if not G.nodes:
        return result

    # Compute entry points (in-degree 0)
    entries = [n for n in G.nodes() if G.in_degree(n) == 0]

    # Build a lookup: normalized name fragment -> list of node IDs
    # We match dependency names against import targets and qualified names.
    norm_to_dep: dict[str, list[str]] = {}
    for dep in dep_names:
        norm = _normalize_dep_name(dep)
        if norm:
            norm_to_dep.setdefault(norm, []).append(dep)

    # Scan symbols for matches
    for nid, data in G.nodes(data=True):
        qname = (data.get("qualified_name") or "").lower().replace("-", "_").replace(".", "_")
        name_lower = (data.get("name") or "").lower().replace("-", "_").replace(".", "_")
        file_path = (data.get("file_path") or "").lower().replace("-", "_").replace(".", "_")

        for norm, orig_deps in norm_to_dep.items():
            # Match if the normalized dep name appears as a prefix in qualified
            # name, or in the file path (e.g., node_modules/lodash/...).
            matched = False
            if qname and (qname.startswith(norm + "_") or qname.startswith(norm + "/") or qname == norm):
                matched = True
            elif norm in file_path:
                matched = True
            elif name_lower == norm:
                matched = True

            if not matched:
                continue

            display_name = data.get("qualified_name") or data.get("name", str(nid))

            for dep_name in orig_deps:
                info = result[dep_name]
                if display_name not in info["matched_symbols"]:
                    info["matched_symbols"].append(display_name)

                if not info["reachable"]:
                    # Check if any entry point can reach this node
                    for eid in entries:
                        try:
                            if nx.has_path(G, eid, nid):
                                info["reachable"] = True
                                entry_name = G.nodes[eid].get("qualified_name") or G.nodes[eid].get("name", str(eid))
                                if entry_name not in info["entry_points"]:
                                    info["entry_points"].append(entry_name)
                        except (nx.NetworkXError, nx.NodeNotFound):
                            continue

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

            component["properties"].extend(
                [
                    {"name": "roam:reachable", "value": str(is_reachable).lower()},
                    {"name": "roam:entry_points", "value": "; ".join(entry_points[:10]) if entry_points else ""},
                    {"name": "roam:matched_symbols", "value": str(len(matched_syms))},
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
        "specVersion": "1.5",
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

    doc_namespace = f"https://roam-code.dev/spdx/{project_name}/{uuid.uuid4()}"

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

    # Compute document verification code
    pkg_checksums = sorted(hashlib.sha256(p["name"].encode()).hexdigest() for p in packages)
    verification = hashlib.sha256("".join(pkg_checksums).encode()).hexdigest()

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
@click.pass_context
def sbom(ctx, fmt, output_path, no_reachability):
    """Generate a Software Bill of Materials (SBOM) enriched with call-graph reachability.

    Produces CycloneDX 1.5 or SPDX 2.3 JSON output.  Each dependency is
    annotated with ``roam:reachable`` (whether any code path reaches symbols
    from that package) and ``roam:entry_points`` (which entry points reach it).

    This reachability enrichment is unique to roam-code -- it lets you
    distinguish phantom dependencies from those actually exercised at runtime.

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
    reachability: dict[str, dict] | None = None
    if not no_reachability and deps:
        try:
            ensure_index()
            with open_db(readonly=True) as conn:
                dep_names = [d.name for d in deps]
                reachability = _compute_reachability(conn, dep_names)
        except Exception:
            # Gracefully degrade -- produce SBOM without reachability
            reachability = None

    # Generate SBOM
    if fmt.lower() == "spdx":
        sbom_data = _generate_spdx(project_name, deps, reachability)
    else:
        sbom_data = _generate_cyclonedx(project_name, deps, reachability)

    # Build summary for verdict / JSON envelope
    total_deps = len(deps)
    reachable_count = 0
    phantom_count = 0
    if reachability is not None:
        reachable_count = sum(1 for v in reachability.values() if v.get("reachable"))
        phantom_count = total_deps - reachable_count

    if total_deps == 0:
        verdict = "No dependencies found -- empty SBOM generated"
    elif reachability is not None:
        verdict = f"SBOM generated: {total_deps} dependencies, {reachable_count} reachable, {phantom_count} phantom"
    else:
        verdict = f"SBOM generated: {total_deps} dependencies (reachability not computed)"

    # Output
    if json_mode:
        envelope = json_envelope(
            "sbom",
            summary={
                "verdict": verdict,
                "format": fmt.lower(),
                "total_dependencies": total_deps,
                "reachable_count": reachable_count if reachability else None,
                "phantom_count": phantom_count if reachability else None,
                "reachability_computed": reachability is not None,
            },
            budget=token_budget,
            sbom=sbom_data,
        )
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
