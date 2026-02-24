"""Multi-agent partition manifest: structured work partitioning with conflict analysis."""

from __future__ import annotations

import os
import sqlite3
from collections import Counter, defaultdict

import click

from roam.db.connection import open_db, batched_in
from roam.output.formatter import to_json, json_envelope, abbrev_kind
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Role classification heuristics
# ---------------------------------------------------------------------------

# Map dominant file extensions / directory patterns to human-readable roles.
_EXTENSION_ROLES: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "React/TypeScript",
    ".jsx": "React/JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".cpp": "C++",
    ".cs": "C#",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".sql": "SQL/Database",
    ".html": "Template/HTML",
    ".css": "Styling",
    ".scss": "Styling",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

_DIR_ROLES: list[tuple[str, str]] = [
    ("api", "API Layer"),
    ("routes", "API Layer"),
    ("handler", "API Layer"),
    ("controller", "Controller Layer"),
    ("view", "View/UI Layer"),
    ("component", "UI Component Layer"),
    ("model", "Data Model Layer"),
    ("schema", "Data Schema Layer"),
    ("db", "Database Layer"),
    ("database", "Database Layer"),
    ("migration", "Migration Layer"),
    ("service", "Service Layer"),
    ("domain", "Domain Logic"),
    ("core", "Core Logic"),
    ("util", "Utility Layer"),
    ("helper", "Utility Layer"),
    ("lib", "Library Layer"),
    ("middleware", "Middleware Layer"),
    ("auth", "Auth Layer"),
    ("security", "Security Layer"),
    ("test", "Test Layer"),
    ("tests", "Test Layer"),
    ("spec", "Test Layer"),
    ("specs", "Test Layer"),
    ("config", "Configuration"),
    ("infra", "Infrastructure"),
    ("deploy", "Infrastructure"),
    ("cli", "CLI Layer"),
    ("cmd", "CLI Layer"),
    ("graph", "Graph/Analysis Layer"),
    ("index", "Indexing Layer"),
    ("search", "Search Layer"),
    ("output", "Output/Formatting Layer"),
]


def _suggest_role(files: list[str], language_counts: Counter) -> str:
    """Suggest a human-readable role label from file paths and language mix."""
    # Try directory-based role first (most descriptive)
    dir_parts: list[str] = []
    for f in files:
        parts = f.replace("\\", "/").split("/")
        dir_parts.extend(p.lower() for p in parts[:-1])

    dir_counter = Counter(dir_parts)
    for dir_name, role in _DIR_ROLES:
        if dir_counter.get(dir_name, 0) > 0:
            return role

    # Fall back to dominant language
    if language_counts:
        top_lang = language_counts.most_common(1)[0][0]
        if top_lang:
            return f"{top_lang} Module"

    return "General Module"


# ---------------------------------------------------------------------------
# Difficulty scoring
# ---------------------------------------------------------------------------


def _difficulty_label(score: float) -> str:
    """Map a 0-100 difficulty score to a human-readable label."""
    if score >= 75:
        return "Critical"
    if score >= 50:
        return "Hard"
    if score >= 25:
        return "Medium"
    return "Easy"


def compute_difficulty_score(
    partitions: list[dict],
    *,
    complexity_weight: float = 0.3,
    coupling_weight: float = 0.25,
    churn_weight: float = 0.25,
    size_weight: float = 0.2,
) -> list[dict]:
    """Compute a composite difficulty score for each partition.

    Each metric is normalized to 0-100 relative to the max across all
    partitions before applying weights.  The result is a 0-100 score
    with a human-readable label.

    Parameters
    ----------
    partitions:
        List of partition dicts (must already contain ``complexity``,
        ``cross_partition_edges``, ``churn``, and ``symbol_count``).
    complexity_weight, coupling_weight, churn_weight, size_weight:
        Relative importance of each factor (should sum to 1.0).

    Returns
    -------
    The same list with ``difficulty_score`` (float 0-100) and
    ``difficulty_label`` (str) added to each dict.
    """
    if not partitions:
        return partitions

    # Collect raw values
    complexities = [p.get("complexity", 0) for p in partitions]
    cross_edges = [p.get("cross_partition_edges", 0) for p in partitions]
    churns = [p.get("churn", 0) for p in partitions]
    sizes = [p.get("symbol_count", 0) for p in partitions]

    max_complexity = max(complexities) or 1
    max_cross = max(cross_edges) or 1
    max_churn = max(churns) or 1
    max_size = max(sizes) or 1

    for i, p in enumerate(partitions):
        norm_complexity = (complexities[i] / max_complexity) * 100
        norm_cross = (cross_edges[i] / max_cross) * 100
        norm_churn = (churns[i] / max_churn) * 100
        norm_size = (sizes[i] / max_size) * 100

        score = (
            complexity_weight * norm_complexity
            + coupling_weight * norm_cross
            + churn_weight * norm_churn
            + size_weight * norm_size
        )

        p["difficulty_score"] = round(score, 1)
        p["difficulty_label"] = _difficulty_label(score)

    return partitions


# ---------------------------------------------------------------------------
# Partition analysis engine
# ---------------------------------------------------------------------------


def compute_partition_manifest(
    conn: sqlite3.Connection,
    n_agents: int | None = None,
) -> dict:
    """Build a detailed partition manifest from the symbol graph.

    Parameters
    ----------
    conn:
        Open (readonly) connection to the roam index DB.
    n_agents:
        Number of agents.  ``None`` means auto-detect from cluster count.

    Returns
    -------
    dict with keys: partitions, dependencies, conflict_hotspots,
    overall_conflict_probability, verdict.
    """
    from roam.graph.builder import build_symbol_graph
    from roam.graph.clusters import detect_clusters
    from roam.graph.partition import (
        _adjust_cluster_count,
        compute_conflict_probability,
        compute_merge_order,
    )

    G = build_symbol_graph(conn)
    if len(G) == 0:
        return _empty_manifest(n_agents or 2)

    # -- 1. Detect communities -----------------------------------------------
    cluster_map = detect_clusters(G)
    if not cluster_map:
        cluster_map = {n: 0 for n in G.nodes}

    groups: dict[int, set[int]] = defaultdict(set)
    for node_id, cid in cluster_map.items():
        groups[cid].add(node_id)

    # Auto-detect agent count from natural cluster count
    if n_agents is None:
        n_agents = max(2, len(groups))

    partitions = _adjust_cluster_count(G, groups, n_agents)

    # -- 2. Gather node metadata in bulk -------------------------------------
    all_node_ids = list(set().union(*(p["nodes"] for p in partitions)))
    node_meta: dict[int, dict] = {}
    if all_node_ids:
        rows = batched_in(
            conn,
            "SELECT s.id, s.name, s.kind, s.qualified_name, f.path, f.language, "
            "f.file_role, COALESCE(gm.pagerank, 0) AS pagerank, "
            "COALESCE(sm.cognitive_complexity, 0) AS complexity "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
            "LEFT JOIN symbol_metrics sm ON s.id = sm.symbol_id "
            "WHERE s.id IN ({ph})",
            all_node_ids,
        )
        for r in rows:
            node_meta[r["id"]] = {
                "name": r["name"],
                "kind": r["kind"],
                "qualified_name": r["qualified_name"],
                "path": r["path"].replace("\\", "/"),
                "language": r["language"],
                "file_role": r["file_role"],
                "pagerank": r["pagerank"],
                "complexity": r["complexity"],
            }

    # -- 3. Build node → partition index -------------------------------------
    node_part: dict[int, int] = {}
    for idx, p in enumerate(partitions):
        for n in p["nodes"]:
            node_part[n] = idx

    # -- 4. Co-change data for conflict analysis -----------------------------
    # Build file_path → file_id mapping
    file_path_to_id: dict[str, int] = {}
    file_rows = conn.execute("SELECT id, path FROM files").fetchall()
    for fr in file_rows:
        file_path_to_id[fr["path"].replace("\\", "/")] = fr["id"]

    cochange_map: dict[tuple[int, int], int] = {}
    cochange_rows = conn.execute(
        "SELECT file_id_a, file_id_b, cochange_count FROM git_cochange"
    ).fetchall()
    for cr in cochange_rows:
        cochange_map[(cr["file_id_a"], cr["file_id_b"])] = cr["cochange_count"]
        cochange_map[(cr["file_id_b"], cr["file_id_a"])] = cr["cochange_count"]

    # -- 5. Build per-partition descriptors ----------------------------------
    result_partitions = []
    all_partition_files: list[set[str]] = []

    for idx, p in enumerate(partitions):
        nodes = p["nodes"]
        files_set: set[str] = set()
        language_counter: Counter = Counter()
        total_complexity = 0.0
        test_file_count = 0
        symbols_with_tests = 0
        key_symbols = []

        for n in nodes:
            meta = node_meta.get(n)
            if not meta:
                continue
            files_set.add(meta["path"])
            if meta["language"]:
                lang_label = _EXTENSION_ROLES.get(
                    "." + (meta["language"] or "").lower().split("/")[-1],
                    meta["language"],
                )
                language_counter[lang_label] += 1
            total_complexity += meta["complexity"]
            if meta["file_role"] == "test":
                test_file_count += 1

        # Key symbols: top-5 by PageRank
        ranked_nodes = sorted(
            [n for n in nodes if n in node_meta],
            key=lambda n: node_meta[n]["pagerank"],
            reverse=True,
        )
        for n in ranked_nodes[:5]:
            m = node_meta[n]
            key_symbols.append({
                "name": m["name"],
                "kind": abbrev_kind(m["kind"]),
                "pagerank": round(m["pagerank"], 4),
                "file": m["path"],
            })

        # Test coverage: ratio of symbols that have a test-role file in this partition
        # or whose name appears in a test file within the partition
        total_source_symbols = sum(
            1 for n in nodes
            if n in node_meta and node_meta[n]["file_role"] != "test"
        )
        test_names = set()
        for n in nodes:
            meta = node_meta.get(n)
            if meta and meta["file_role"] == "test":
                test_names.add(meta["name"].lower())

        if total_source_symbols > 0 and test_names:
            for n in nodes:
                meta = node_meta.get(n)
                if meta and meta["file_role"] != "test":
                    # Check if any test symbol references this name
                    if meta["name"].lower() in test_names:
                        symbols_with_tests += 1
                    elif f"test_{meta['name'].lower()}" in test_names:
                        symbols_with_tests += 1

        test_coverage = (
            round(symbols_with_tests / total_source_symbols, 2)
            if total_source_symbols > 0
            else 0.0
        )

        # Cross-partition edge count for this partition
        cross_edges = 0
        for n in nodes:
            for succ in G.successors(n):
                if succ in node_part and node_part[succ] != idx:
                    cross_edges += 1
            for pred in G.predecessors(n):
                if pred in node_part and node_part[pred] != idx:
                    cross_edges += 1

        # Co-change conflict score: files in this partition that co-change
        # with files in OTHER partitions
        cochange_score = 0
        partition_file_ids = {
            file_path_to_id[f] for f in files_set if f in file_path_to_id
        }
        for fid_a in partition_file_ids:
            for fid_b, count in cochange_map.items():
                if fid_b[0] == fid_a:
                    other_fid = fid_b[1] if len(fid_b) > 1 else fid_b
                    # This is handled differently — iterate cochange pairs
                    pass
        # Simpler approach: iterate known cochange pairs
        for (fid_a, fid_b), count in cochange_map.items():
            if fid_a in partition_file_ids and fid_b not in partition_file_ids:
                cochange_score += count

        # Conflict risk label
        if cross_edges <= 3 and cochange_score <= 2:
            conflict_risk = "LOW"
        elif cross_edges <= 10 or cochange_score <= 5:
            conflict_risk = "MEDIUM"
        else:
            conflict_risk = "HIGH"

        # Per-partition churn: sum of churn across files in this partition
        partition_churn = 0
        if partition_file_ids:
            for pfid in partition_file_ids:
                try:
                    churn_row = conn.execute(
                        "SELECT total_churn FROM file_stats WHERE file_id = ?",
                        (pfid,),
                    ).fetchone()
                    if churn_row and churn_row["total_churn"]:
                        partition_churn += churn_row["total_churn"]
                except Exception:
                    pass

        # Suggest role
        role = _suggest_role(sorted(files_set), language_counter)

        # Cluster label from directory majority
        dirs = [os.path.dirname(f).replace("\\", "/") for f in files_set if f]
        dir_counts = Counter(dirs) if dirs else Counter()
        if dir_counts:
            label = dir_counts.most_common(1)[0][0]
            label = label.rstrip("/").rsplit("/", 1)[-1] if label else f"partition-{idx + 1}"
            if not label:
                label = f"root-{idx + 1}"
        else:
            label = f"partition-{idx + 1}"

        all_partition_files.append(files_set)

        result_partitions.append({
            "id": idx + 1,
            "label": label,
            "role": role,
            "files": sorted(files_set),
            "file_count": len(files_set),
            "symbol_count": len(nodes),
            "key_symbols": key_symbols,
            "complexity": round(total_complexity, 1),
            "churn": partition_churn,
            "test_coverage": test_coverage,
            "conflict_risk": conflict_risk,
            "cross_partition_edges": cross_edges,
            "cochange_score": cochange_score,
        })

    # -- 6. Balance partitions by complexity → assign to agents ---------------
    # Sort partitions by complexity descending, assign round-robin to balance
    sorted_parts = sorted(
        range(len(result_partitions)),
        key=lambda i: result_partitions[i]["complexity"],
        reverse=True,
    )
    agent_loads = [0.0] * n_agents
    for pi in sorted_parts:
        # Assign to the agent with the least load
        min_agent = min(range(n_agents), key=lambda a: agent_loads[a])
        result_partitions[pi]["agent"] = f"Worker-{min_agent + 1}"
        agent_loads[min_agent] += result_partitions[pi]["complexity"]

    # -- 6b. Compute composite difficulty scores ------------------------------
    compute_difficulty_score(result_partitions)

    # -- 7. Cross-partition dependencies -------------------------------------
    dep_counter: dict[tuple[int, int], list[str]] = defaultdict(list)
    for u, v in G.edges:
        pu = node_part.get(u)
        pv = node_part.get(v)
        if pu is not None and pv is not None and pu != pv:
            u_meta = node_meta.get(u, {})
            v_meta = node_meta.get(v, {})
            u_file = u_meta.get("path", "?")
            v_file = v_meta.get("path", "?")
            edge_desc = f"{u_file} -> {v_file}"
            dep_counter[(pu + 1, pv + 1)].append(edge_desc)

    dependencies = []
    for (from_id, to_id), edge_descs in sorted(dep_counter.items()):
        # Find shared files
        shared = all_partition_files[from_id - 1] & all_partition_files[to_id - 1]
        dependencies.append({
            "from": from_id,
            "to": to_id,
            "edge_count": len(edge_descs),
            "sample_edges": edge_descs[:5],
            "shared_files": sorted(shared),
        })

    # -- 8. Conflict hotspots ------------------------------------------------
    # Files referenced by symbols in multiple partitions
    file_partitions: dict[str, set[int]] = defaultdict(set)
    for n, pidx in node_part.items():
        meta = node_meta.get(n)
        if meta:
            file_partitions[meta["path"]].add(pidx + 1)

    conflict_hotspots = []
    for fpath, part_ids in sorted(file_partitions.items()):
        if len(part_ids) >= 2:
            conflict_hotspots.append({
                "file": fpath,
                "partition_count": len(part_ids),
                "partitions": sorted(part_ids),
            })
    conflict_hotspots.sort(key=lambda h: -h["partition_count"])
    conflict_hotspots = conflict_hotspots[:20]  # cap

    # -- 9. Overall conflict probability -------------------------------------
    overall_cp = compute_conflict_probability(G, partitions)

    # -- 10. Merge order -----------------------------------------------------
    merge_order = compute_merge_order(G, partitions)

    verdict = (
        f"{len(result_partitions)} partitions for {n_agents} agents, "
        f"conflict probability {int(overall_cp * 100)}%"
    )

    return {
        "verdict": verdict,
        "total_partitions": len(result_partitions),
        "n_agents": n_agents,
        "overall_conflict_probability": round(overall_cp, 4),
        "merge_order": merge_order,
        "partitions": result_partitions,
        "dependencies": dependencies,
        "conflict_hotspots": conflict_hotspots,
    }


def _empty_manifest(n_agents: int) -> dict:
    """Return a valid but empty manifest."""
    return {
        "verdict": f"0 partitions for {n_agents} agents, conflict probability 0%",
        "total_partitions": 0,
        "n_agents": n_agents,
        "overall_conflict_probability": 0.0,
        "merge_order": [],
        "partitions": [],
        "dependencies": [],
        "conflict_hotspots": [],
    }


# ---------------------------------------------------------------------------
# claude-teams format helpers
# ---------------------------------------------------------------------------


def _to_claude_teams(manifest: dict) -> dict:
    """Convert the manifest into a format optimized for Claude Agent Teams SDK.

    Produces a structure that can be directly fed into a multi-agent
    orchestrator:
      - agents: list of agent configs with scope and constraints
      - coordination: shared interfaces and merge strategy
    """
    agents = []
    for p in manifest["partitions"]:
        agents.append({
            "agent_id": p.get("agent", f"Worker-{p['id']}"),
            "role": p["role"],
            "scope": {
                "write_files": p["files"],
                "read_only_deps": [],  # populated below
            },
            "constraints": {
                "conflict_risk": p["conflict_risk"],
                "estimated_complexity": p["complexity"],
                "test_coverage": p["test_coverage"],
            },
        })

    # Fill read-only deps from dependencies
    part_id_to_idx = {p["id"]: i for i, p in enumerate(manifest["partitions"])}
    for dep in manifest["dependencies"]:
        from_idx = part_id_to_idx.get(dep["from"])
        if from_idx is not None and from_idx < len(agents):
            # The "from" partition depends on files in the "to" partition
            to_idx = part_id_to_idx.get(dep["to"])
            if to_idx is not None and to_idx < len(manifest["partitions"]):
                to_files = manifest["partitions"][to_idx]["files"]
                existing = set(agents[from_idx]["scope"]["read_only_deps"])
                for f in to_files:
                    if f not in existing:
                        agents[from_idx]["scope"]["read_only_deps"].append(f)

    coordination = {
        "merge_order": manifest["merge_order"],
        "conflict_hotspots": [h["file"] for h in manifest["conflict_hotspots"]],
        "overall_conflict_probability": manifest["overall_conflict_probability"],
    }

    return {
        "agents": agents,
        "coordination": coordination,
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("partition")
@click.option(
    "--agents", "n_agents", type=int, default=None,
    help="Number of agents (default: auto-detect from cluster count)",
)
@click.option(
    "--format", "output_format", type=click.Choice(["plain", "json", "claude-teams"]),
    default="plain",
    help="Output format: plain (human readable), json, claude-teams",
)
@click.pass_context
def partition(ctx, n_agents, output_format):
    """Generate a multi-agent partition manifest with conflict analysis.

    Partitions the codebase into non-overlapping work zones using community
    detection, then enriches each partition with conflict probability,
    test coverage, estimated complexity, and a suggested agent role.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        manifest = compute_partition_manifest(conn, n_agents)

    # -- claude-teams format -------------------------------------------------
    if output_format == "claude-teams":
        teams_data = _to_claude_teams(manifest)
        if json_mode:
            click.echo(to_json(json_envelope("partition",
                summary={
                    "verdict": manifest["verdict"],
                    "total_partitions": manifest["total_partitions"],
                    "overall_conflict_probability": manifest["overall_conflict_probability"],
                },
                format="claude-teams",
                **teams_data,
            )))
        else:
            click.echo(to_json(teams_data))
        return

    # -- JSON format ---------------------------------------------------------
    if json_mode or output_format == "json":
        click.echo(to_json(json_envelope("partition",
            summary={
                "verdict": manifest["verdict"],
                "total_partitions": manifest["total_partitions"],
                "n_agents": manifest["n_agents"],
                "overall_conflict_probability": manifest["overall_conflict_probability"],
            },
            partitions=manifest["partitions"],
            dependencies=manifest["dependencies"],
            conflict_hotspots=manifest["conflict_hotspots"],
            merge_order=manifest["merge_order"],
        )))
        return

    # -- Plain text format ---------------------------------------------------
    click.echo(f"VERDICT: {manifest['verdict']}")
    click.echo()

    for p in manifest["partitions"]:
        agent_label = p.get("agent", f"Worker-{p['id']}")
        click.echo(
            f'PARTITION {p["id"]} -- "{p["role"]}" (Agent: {agent_label})'
        )
        diff_label = p.get("difficulty_label", "?")
        diff_score = p.get("difficulty_score", 0)
        click.echo(
            f"  Files: {p['file_count']} | "
            f"Symbols: {p['symbol_count']} | "
            f"Complexity: {p['complexity']} | "
            f"Churn: {p.get('churn', 0)} | "
            f"Difficulty: {diff_label} ({diff_score})"
        )
        click.echo(
            f"  Test coverage: {int(p['test_coverage'] * 100)}%"
        )
        # Key files (top 3)
        if p["files"]:
            top_files = p["files"][:3]
            files_str = ", ".join(top_files)
            if len(p["files"]) > 3:
                files_str += f" (+{len(p['files']) - 3} more)"
            click.echo(f"  Key files: {files_str}")
        # Key symbols
        if p["key_symbols"]:
            sym_strs = []
            for s in p["key_symbols"][:3]:
                sym_strs.append(
                    f"{s['kind']} {s['name']} (PageRank {s['pagerank']:.4f})"
                )
            click.echo(f"  Key symbols: {', '.join(sym_strs)}")
        click.echo(
            f"  Conflict risk: {p['conflict_risk']} "
            f"({p['cross_partition_edges']} cross-partition edges)"
        )
        click.echo()

    # Cross-partition dependencies
    if manifest["dependencies"]:
        click.echo("CROSS-PARTITION DEPENDENCIES:")
        for dep in manifest["dependencies"]:
            sample = ""
            if dep["sample_edges"]:
                sample = f": {dep['sample_edges'][0]}"
                if len(dep["sample_edges"]) > 1:
                    sample += ", ..."
            click.echo(
                f"  Partition {dep['from']} -> Partition {dep['to']} "
                f"({dep['edge_count']} edges{sample})"
            )
        click.echo()

    # Conflict hotspots
    if manifest["conflict_hotspots"]:
        click.echo("CONFLICT HOTSPOTS:")
        for h in manifest["conflict_hotspots"][:10]:
            click.echo(
                f"  {h['file']} -- referenced by "
                f"{h['partition_count']} partitions "
                f"(assign to Coordinator)"
            )
        click.echo()

    # Merge order
    if manifest["merge_order"]:
        order_str = " -> ".join(f"Partition {pid}" for pid in manifest["merge_order"])
        click.echo(f"Merge order: {order_str}")
