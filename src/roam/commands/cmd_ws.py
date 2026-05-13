"""Workspace commands: multi-repo grouping and cross-repo analysis."""

from __future__ import annotations

import json
import time
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import format_table, json_envelope, to_json

# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="ws",
    category="getting-started",
    summary="Manage multi-repo workspaces with cross-repo dependency tracking",
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
@click.group("ws")
@click.pass_context
def ws(ctx) -> None:
    """Manage multi-repo workspaces with cross-repo dependency tracking.

    Unlike single-repo commands (``understand``, ``health``, ``context``), the ws
    subcommands aggregate across multiple indexed repositories and detect cross-repo
    API connections.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# ws init
# ---------------------------------------------------------------------------


@ws.command("init")
@click.argument("repos", nargs=-1, required=True)
@click.option("--name", default="", help="Workspace name (default: parent dir name)")
@click.pass_context
def ws_init(ctx, repos: tuple, name: str) -> None:
    """Initialize a workspace from multiple repo directories.

    REPOS are paths to git repositories (relative or absolute).

    Example:
      roam ws init ../frontend ../backend --name my-platform
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    from roam.workspace.config import (
        save_workspace_config,
    )
    from roam.workspace.db import open_workspace_db, upsert_repo

    # Resolve repo paths
    cwd = Path.cwd()
    resolved = []
    errors = []
    for repo_path_str in repos:
        repo_path = Path(repo_path_str).resolve()
        if not repo_path.exists():
            # Try relative to cwd
            repo_path = (cwd / repo_path_str).resolve()
        if not repo_path.exists():
            errors.append(f"Path not found: {repo_path_str}")
            continue
        if not (repo_path / ".git").exists():
            errors.append(f"Not a git repo: {repo_path_str}")
            continue

        db_path = repo_path / ".roam" / "index.db"
        indexed = db_path.exists() and db_path.stat().st_size > 0

        resolved.append(
            {
                "path": repo_path_str,
                "abs_path": repo_path,
                "name": repo_path.name,
                "db_path": db_path,
                "indexed": indexed,
            }
        )

    if errors:
        for err in errors:
            click.echo(f"ERROR: {err}", err=True)
        if not resolved:
            raise SystemExit(1)

    # Determine workspace root (common parent of all repos)
    all_parents = [r["abs_path"].parent for r in resolved]
    ws_root = all_parents[0]
    for p in all_parents[1:]:
        # Find common ancestor
        while not str(p).startswith(str(ws_root)):
            ws_root = ws_root.parent
            if ws_root == ws_root.parent:
                break

    ws_name = name or ws_root.name

    # Detect roles from language content
    for r in resolved:
        r["role"] = _detect_role(r["abs_path"])

    # Build config
    config = {
        "workspace": ws_name,
        "repos": [
            {
                "path": r["path"],
                "name": r["name"],
                "role": r["role"],
            }
            for r in resolved
        ],
        "connections": [],
    }

    # Detect REST API connections between frontend/backend pairs
    frontend_repos = [r for r in resolved if r["role"] == "frontend"]
    backend_repos = [r for r in resolved if r["role"] == "backend"]
    for fe in frontend_repos:
        for be in backend_repos:
            config["connections"].append(
                {
                    "type": "rest-api",
                    "frontend": fe["name"],
                    "backend": be["name"],
                }
            )

    # Write config
    config_path = save_workspace_config(ws_root, config)

    # Create workspace DB and register repos
    with open_workspace_db(ws_root) as ws_conn:
        for r in resolved:
            last_indexed = None
            if r["indexed"]:
                last_indexed = r["db_path"].stat().st_mtime
            upsert_repo(
                ws_conn,
                name=r["name"],
                path=str(r["abs_path"]),
                role=r["role"],
                db_path=str(r["db_path"]),
                last_indexed=last_indexed,
            )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "ws-init",
                    summary={
                        "workspace": ws_name,
                        "repos": len(resolved),
                        "config_path": str(config_path),
                    },
                    repos=[
                        {
                            "name": r["name"],
                            "path": r["path"],
                            "role": r["role"],
                            "indexed": r["indexed"],
                        }
                        for r in resolved
                    ],
                    errors=errors,
                )
            )
        )
        return

    # Text output
    click.echo(f"WORKSPACE: {ws_name} ({len(resolved)} repos)")
    click.echo(f"  Config: {config_path}")
    click.echo()
    for r in resolved:
        idx_status = "indexed" if r["indexed"] else "NOT INDEXED (run `roam index` in that repo)"
        click.echo(f"  {r['name']:30s} {r['role']:12s} {idx_status}")
    if errors:
        click.echo()
        for err in errors:
            click.echo(f"  WARNING: {err}")

    not_indexed = [r for r in resolved if not r["indexed"]]
    if not_indexed:
        click.echo()
        click.echo("Next steps:")
        for r in not_indexed:
            click.echo(f"  cd {r['abs_path']} && roam index")
    click.echo()
    click.echo("Run `roam ws resolve` to detect cross-repo API connections.")
    # Issue #18: roles drive the auto-derivation of `connections`. Spell
    # this out so users don't edit roles afterwards and end up with
    # `connections: []` resolving to zero edges.
    untagged = [r for r in resolved if r["role"] not in ("frontend", "backend")]
    if untagged:
        click.echo(
            "  Tip: tag each repo as `role: frontend` or `role: backend` in "
            ".roam-workspace.json so `ws resolve` can pair them automatically. "
            f"Currently untagged: {', '.join(r['name'] for r in untagged[:5])}" + ("..." if len(untagged) > 5 else "")
        )


# ---------------------------------------------------------------------------
# ws status
# ---------------------------------------------------------------------------


@ws.command("status")
@click.pass_context
def ws_status(ctx) -> None:
    """Show workspace status: repos, index ages, cross-repo edges."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import get_cross_edges, open_workspace_db

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root, readonly=True) as ws_conn:
        cross_edges = get_cross_edges(ws_conn)

    # Gather per-repo stats
    repo_stats = []
    for info in repo_infos:
        stat = _get_repo_stat(info)
        repo_stats.append(stat)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "ws-status",
                    summary={
                        "workspace": config["workspace"],
                        "repos": len(repo_stats),
                        "cross_repo_edges": len(cross_edges),
                        "verdict": f"{len(repo_stats)} repos, {len(cross_edges)} cross-repo edges",
                    },
                    repos=repo_stats,
                    cross_repo_edges=len(cross_edges),
                )
            )
        )
        return

    click.echo(f"WORKSPACE: {config['workspace']} ({len(repo_stats)} repos)")
    headers = ["Repo", "Role", "Files", "Symbols", "Indexed"]
    rows = []
    for s in repo_stats:
        age = _format_age(s.get("index_age_s"))
        rows.append([s["name"], s.get("role", ""), str(s["files"]), str(s["symbols"]), age])
    click.echo(format_table(headers, rows))
    click.echo(f"  Cross-repo edges: {len(cross_edges)}", nl=False)
    if not cross_edges:
        click.echo("  (run `roam ws resolve` to detect)")
    else:
        click.echo()


# ---------------------------------------------------------------------------
# ws resolve
# ---------------------------------------------------------------------------


@ws.command("resolve")
@click.pass_context
def ws_resolve(ctx) -> None:
    """Detect cross-repo API connections between frontend and backend repos."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.api_scanner import (
        build_cross_repo_edges,
        find_unmatched_calls,
        match_api_endpoints,
        scan_backend_routes,
        scan_frontend_api_calls,
    )
    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import (
        clear_cross_edges,
        open_workspace_db,
        upsert_repo,
    )

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root) as ws_conn:
        # Clear existing edges before re-resolve
        clear_cross_edges(ws_conn)

        # Ensure repos are registered
        repo_id_map = {}
        for info in repo_infos:
            last_indexed = None
            if info["db_path"].exists():
                last_indexed = info["db_path"].stat().st_mtime
            rid = upsert_repo(
                ws_conn,
                name=info["name"],
                path=str(info["path"]),
                role=info.get("role", ""),
                db_path=str(info["db_path"]),
                last_indexed=last_indexed,
            )
            repo_id_map[info["name"]] = rid

        total_fe_calls = 0
        total_be_routes = 0
        total_matched = 0
        all_matches = []
        all_unmatched: list[dict] = []

        # Issue #18 guard: when `connections: []` is empty (the default
        # after `ws init`, or after the user edits roles by hand without
        # re-running init), auto-derive pairs from `role: frontend` β†”
        # `role: backend` tags so `ws resolve` does *something* useful.
        # The on-disk config is left alone β€” populate in-memory only.
        connections = list(config.get("connections", []))
        if not connections:
            fe_repos = [r for r in repo_infos if r.get("role") == "frontend"]
            be_repos = [r for r in repo_infos if r.get("role") == "backend"]
            if fe_repos and be_repos:
                connections = [
                    {"type": "rest-api", "frontend": fe["name"], "backend": be["name"]}
                    for fe in fe_repos
                    for be in be_repos
                ]
                if not json_mode:
                    pairs = ", ".join(f"{c['frontend']} -> {c['backend']}" for c in connections)
                    click.echo(
                        f"  Note: connections array empty; auto-derived "
                        f"{len(connections)} pair(s) from role tags: {pairs}",
                        err=True,
                    )
                    click.echo(
                        "  (edit `.roam-workspace.json` to override; re-run `roam ws init` to persist)",
                        err=True,
                    )
            elif not json_mode:
                missing = []
                if not fe_repos:
                    missing.append("a frontend role")
                if not be_repos:
                    missing.append("a backend role")
                click.echo(
                    f"  Warning: connections array is empty and no auto-derivation "
                    f"possible (missing: {', '.join(missing)}). "
                    f"Set `role: frontend` / `role: backend` on repos in "
                    f".roam-workspace.json, then re-run `roam ws init` "
                    f"or add `connections` entries manually.",
                    err=True,
                )

        # Process each connection pair
        for conn_cfg in connections:
            if conn_cfg.get("type") != "rest-api":
                continue

            fe_name = conn_cfg.get("frontend", "")
            be_name = conn_cfg.get("backend", "")

            fe_info = next((i for i in repo_infos if i["name"] == fe_name), None)
            be_info = next((i for i in repo_infos if i["name"] == be_name), None)

            if not fe_info or not be_info:
                continue

            if not json_mode:
                click.echo(f"Scanning {fe_name} for API calls...", nl=False)

            fe_calls = scan_frontend_api_calls(fe_info["db_path"], fe_info["path"])
            total_fe_calls += len(fe_calls)
            if not json_mode:
                click.echo(f" {len(fe_calls)} found")

            if not json_mode:
                click.echo(f"Scanning {be_name} for routes...", nl=False)

            be_routes = scan_backend_routes(be_info["db_path"], be_info["path"])
            total_be_routes += len(be_routes)
            if not json_mode:
                click.echo(f" {len(be_routes)} found")

            if not json_mode:
                click.echo("Matching endpoints...", nl=False)

            matched = match_api_endpoints(fe_calls, be_routes)
            total_matched += len(matched)
            if not json_mode:
                click.echo(f" {len(matched)}/{len(fe_calls)} matched")

            if matched:
                fe_repo_id = repo_id_map.get(fe_name, 0)
                be_repo_id = repo_id_map.get(be_name, 0)
                build_cross_repo_edges(ws_conn, fe_repo_id, be_repo_id, matched)

            all_matches.extend(matched)

            # Compute unmatched calls for this pair (potential 404s).
            unmatched = find_unmatched_calls(fe_calls, be_routes, matched)
            all_unmatched.extend(unmatched)

    total_unmatched = len(all_unmatched)
    match_pct = round(100 * total_matched / total_fe_calls) if total_fe_calls else 0
    match_rate = (total_matched / total_fe_calls) if total_fe_calls else 0.0

    # Pattern 2: explicit state when any unmatched URLs survive — never
    # silently SAFE. Verdict standalone-readable per LAW 6.
    if total_fe_calls == 0:
        state = "no_frontend_calls"
        verdict = "0 frontend calls discovered; nothing to resolve"
        partial_success = False
    elif total_unmatched == 0:
        state = "ok"
        verdict = f"{total_matched}/{total_fe_calls} frontend URLs match (100%); 0 unmatched"
        partial_success = False
    else:
        state = "partial_match"
        verdict = (
            f"{total_matched} of {total_fe_calls} frontend URLs match "
            f"({match_pct}%); {total_unmatched} unmatched POTENTIAL 404s"
        )
        partial_success = True

    # Agent contract facts — flat, imperative, concrete.
    facts: list[str] = []
    if total_unmatched > 0:
        facts.append(
            f"{total_unmatched} frontend URLs do not match any backend route"
        )
        # Top reason breakdown (concrete noun anchor).
        reason_counts: dict[str, int] = {}
        for u in all_unmatched:
            reason_counts[u["reason"]] = reason_counts.get(u["reason"], 0) + 1
        top_reasons = sorted(reason_counts.items(), key=lambda x: -x[1])
        for reason_name, count in top_reasons[:3]:
            facts.append(f"{count} unmatched have reason `{reason_name}`")
        # Top shared prefix among unmatched (LAW 4 — concrete noun).
        prefix_counts: dict[str, int] = {}
        for u in all_unmatched:
            url = u["url"]
            segs = [s for s in url.split("/") if s]
            if len(segs) >= 2:
                prefix = "/" + "/".join(segs[:2])
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
            elif segs:
                prefix = "/" + segs[0]
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        top_prefixes = sorted(prefix_counts.items(), key=lambda x: -x[1])
        if top_prefixes and top_prefixes[0][1] >= 2:
            p, n = top_prefixes[0]
            facts.append(f"{n} unmatched URLs share the prefix `{p}/`")
        first = all_unmatched[0]
        facts.append(
            f"Top unmatched: `{first['url']}` ({first['method'] or '?'})"
        )
    else:
        facts.append(
            f"All {total_fe_calls} frontend URLs match a backend route"
        )

    next_commands: list[str] = []
    if total_unmatched > 0:
        next_commands.append(
            "roam --json ws resolve   # see the full unmatched list"
        )
        next_commands.append(
            "roam endpoints --json    # inspect backend route inventory"
        )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "ws-resolve",
                    summary={
                        "frontend_calls": total_fe_calls,
                        "backend_routes": total_be_routes,
                        "matched": total_matched,
                        "matched_count": total_matched,
                        "unmatched_count": total_unmatched,
                        "match_pct": match_pct,
                        "match_rate": round(match_rate, 4),
                        "state": state,
                        "partial_success": partial_success,
                        "verdict": verdict,
                    },
                    matches=[
                        {
                            "url": m["url_pattern"],
                            "method": m.get("http_method", ""),
                            "frontend_file": m["frontend"]["file_path"],
                            "frontend_symbol": m["frontend"].get("symbol_name", ""),
                            "backend_file": m["backend"]["file_path"],
                            "backend_symbol": m["backend"].get("symbol_name", ""),
                            "score": round(m["score"], 2),
                        }
                        for m in all_matches[:50]
                    ],
                    unmatched=all_unmatched,
                    agent_contract={
                        "facts": facts,
                        "next_commands": next_commands,
                    },
                )
            )
        )
        return

    click.echo()
    click.echo(f"VERDICT: {verdict}")
    click.echo(f"Cross-repo edges: {total_matched} api_call edges stored")
    if all_matches:
        # Show top matches
        for m in all_matches[:10]:
            method = m.get("http_method", "?")
            url = m["url_pattern"]
            be_sym = m["backend"].get("symbol_name", "?")
            click.echo(f"  {url:35s} {method:6s} -> {be_sym}")
        if len(all_matches) > 10:
            click.echo(f"  (+{len(all_matches) - 10} more)")

    if all_unmatched:
        click.echo()
        click.echo(f"Unmatched: {total_unmatched} URLs (potential 404s)")
        click.echo()
        click.echo("Top unmatched URLs:")
        for u in all_unmatched[:10]:
            method = u["method"] or "?"
            url = u["url"]
            fe_file = u["frontend_file"]
            click.echo(f"  {method:6s} {url:30s} -- {fe_file}")
        if total_unmatched > 10:
            click.echo(f"  (+{total_unmatched - 10} more)")
        click.echo()
        click.echo("Run `roam --json ws resolve` for full list.")


# ---------------------------------------------------------------------------
# ws understand
# ---------------------------------------------------------------------------


@ws.command("understand")
@click.pass_context
def ws_understand(ctx) -> None:
    """Full workspace overview: repos, stats, cross-repo connections."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.aggregator import aggregate_understand
    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import open_workspace_db

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root, readonly=True) as ws_conn:
        data = aggregate_understand(ws_conn, repo_infos)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "ws-understand",
                    summary={
                        "workspace": config["workspace"],
                        "repos": len(data["repos"]),
                        "total_files": data["total_files"],
                        "total_symbols": data["total_symbols"],
                        "cross_repo_edges": data["cross_repo_edges"],
                        "verdict": (
                            f"{len(data['repos'])} repos, {data['total_files']} files, "
                            f"{data['total_symbols']} symbols, "
                            f"{data['cross_repo_edges']} cross-repo edges"
                        ),
                    },
                    **data,
                )
            )
        )
        return

    click.echo(
        f"WORKSPACE: {config['workspace']} "
        f"({len(data['repos'])} repos, {data['total_files']} files, "
        f"{data['total_symbols']} symbols)"
    )
    click.echo()

    for repo in data["repos"]:
        langs = ", ".join(f"{l['language']}" for l in repo.get("languages", [])[:3])
        click.echo(f"=== {repo['name']} ({langs}) ===")
        click.echo(f"  {repo['files']} files, {repo['symbols']} symbols, {repo['edges']} edges")
        if repo.get("key_symbols"):
            keys = ", ".join(s["name"] for s in repo["key_symbols"][:5])
            click.echo(f"  Key: {keys}")
        click.echo()

    if data["cross_repo_connections"]:
        click.echo(f"=== Cross-Repo Connections ({data['cross_repo_edges']} edges) ===")
        for conn_info in data["cross_repo_connections"]:
            click.echo(f"  {conn_info['source_repo']} -> {conn_info['target_repo']} ({conn_info['edge_count']} edges)")
            for sample in conn_info.get("samples", [])[:3]:
                click.echo(f"    {sample.get('http_method', ''):6s} {sample.get('url_pattern', '')}")


# ---------------------------------------------------------------------------
# ws health
# ---------------------------------------------------------------------------


@ws.command("health")
@click.pass_context
def ws_health(ctx) -> None:
    """Workspace-wide health report."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.aggregator import aggregate_health
    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import open_workspace_db

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root, readonly=True) as ws_conn:
        data = aggregate_health(ws_conn, repo_infos)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "ws-health",
                    summary={
                        "workspace": config["workspace"],
                        "workspace_health": data["workspace_health"],
                        "cross_repo_edges": data["cross_repo_edges"],
                        "coupling_verdict": data["coupling_verdict"],
                        "verdict": (f"Health: {data['workspace_health']}/100, coupling: {data['coupling_verdict']}"),
                    },
                    **data,
                )
            )
        )
        return

    click.echo(
        f"VERDICT: Workspace health {data['workspace_health']}/100, cross-repo coupling: {data['coupling_verdict']}"
    )
    click.echo()
    headers = ["Repo", "Health", "Files", "Symbols", "Cycles"]
    rows = []
    for r in data["repos"]:
        score = str(r["health_score"]) if r["health_score"] is not None else "?"
        rows.append([r["name"], score, str(r["files"]), str(r["symbols"]), str(r["cycles"])])
    click.echo(format_table(headers, rows))
    click.echo(f"\n  Cross-repo edges: {data['cross_repo_edges']}")


# ---------------------------------------------------------------------------
# ws context
# ---------------------------------------------------------------------------


@ws.command("context")
@click.argument("symbol")
@click.pass_context
def ws_context(ctx, symbol: str) -> None:
    """Cross-repo augmented context for a symbol.

    Searches all repos in the workspace and shows cross-repo callers/callees.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.aggregator import cross_repo_context
    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import open_workspace_db

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root, readonly=True) as ws_conn:
        data = cross_repo_context(ws_conn, symbol, repo_infos)

    if json_mode:
        found_repos = [f["repo"] for f in data["found_in"]]
        click.echo(
            to_json(
                json_envelope(
                    "ws-context",
                    summary={
                        "symbol": symbol,
                        "found_in_repos": found_repos,
                        "cross_repo_edges": len(data["cross_repo_edges"]),
                        "verdict": (
                            f"Found in {len(found_repos)} repo(s), {len(data['cross_repo_edges'])} cross-repo edges"
                        ),
                    },
                    **data,
                )
            )
        )
        return

    if not data["found_in"]:
        click.echo(f"Symbol '{symbol}' not found in any workspace repo.")
        return

    for entry in data["found_in"]:
        click.echo(
            f"[{entry['repo']}] {entry['kind']} {entry['name']}  {entry['file_path']}:{entry.get('line_start', '?')}"
        )
        if entry.get("signature"):
            click.echo(f"  {entry['signature']}")
        if entry["callers"]:
            click.echo("  Callers:")
            for c in entry["callers"][:5]:
                click.echo(f"    {c['name']}  {c['file']}:{c.get('line', '?')}")
        if entry["callees"]:
            click.echo("  Callees:")
            for c in entry["callees"][:5]:
                click.echo(f"    {c['name']}  {c['file']}:{c.get('line', '?')}")
        click.echo()

    if data["cross_repo_edges"]:
        click.echo("Cross-repo connections:")
        for edge in data["cross_repo_edges"]:
            click.echo(
                f"  {edge['source_repo']} -> {edge['target_repo']}  "
                f"{edge.get('http_method', '')} {edge.get('url_pattern', '')}  "
                f"({edge['kind']})"
            )


# ---------------------------------------------------------------------------
# ws trace
# ---------------------------------------------------------------------------


@ws.command("trace")
@click.argument("source")
@click.argument("target")
@click.pass_context
def ws_trace(ctx, source: str, target: str) -> None:
    """Trace a path between symbols across repos.

    Shows how SOURCE connects to TARGET, including cross-repo API edges.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.aggregator import cross_repo_trace
    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import open_workspace_db

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root, readonly=True) as ws_conn:
        data = cross_repo_trace(ws_conn, source, target, repo_infos)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "ws-trace",
                    summary={
                        "source": source,
                        "target": target,
                        "bridge_edges": len(data["bridge_edges"]),
                        "same_repo": data["same_repo"],
                        "verdict": data["verdict"],
                    },
                    **data,
                )
            )
        )
        return

    click.echo(f"VERDICT: {data['verdict']}")
    click.echo()

    if data["source"]["locations"]:
        click.echo(f"Source: {source}")
        for loc in data["source"]["locations"][:3]:
            click.echo(f"  [{loc['repo']}] {loc['kind']} {loc['name']}  {loc['file']}")

    if data["target"]["locations"]:
        click.echo(f"Target: {target}")
        for loc in data["target"]["locations"][:3]:
            click.echo(f"  [{loc['repo']}] {loc['kind']} {loc['name']}  {loc['file']}")

    if data["bridge_edges"]:
        click.echo()
        click.echo("Cross-repo bridges:")
        for b in data["bridge_edges"]:
            click.echo(
                f"  {b['source_repo']} -> {b['target_repo']}  "
                f"{b.get('http_method', '')} {b.get('url_pattern', '')}  "
                f"({b['kind']})"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_workspace():
    """Find and load workspace config, or exit with error.

    Returns (ws_root, config).
    """
    from roam.workspace.config import find_workspace_root, load_workspace_config

    ws_root = find_workspace_root()
    if ws_root is None:
        click.echo(
            "No workspace found. Run `roam ws init <repo1> <repo2> ...` first.",
            err=True,
        )
        raise SystemExit(1)

    config = load_workspace_config(ws_root)
    return ws_root, config


def _detect_role(repo_path: Path) -> str:
    """Try to detect whether a repo is a frontend or backend."""
    # Check for common frontend indicators
    if (repo_path / "package.json").exists():
        try:
            pkg = json.loads((repo_path / "package.json").read_text(encoding="utf-8"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if any(k in deps for k in ("vue", "react", "angular", "svelte", "next", "nuxt")):
                return "frontend"
        except (json.JSONDecodeError, OSError):
            pass

    # Check for common backend indicators
    if (repo_path / "composer.json").exists():
        return "backend"
    if (repo_path / "requirements.txt").exists() or (repo_path / "pyproject.toml").exists():
        # Could be either, but check for framework hints
        for f in ("manage.py", "app.py", "main.py"):
            if (repo_path / f).exists():
                return "backend"
    if (repo_path / "go.mod").exists():
        return "backend"
    if (repo_path / "Gemfile").exists():
        return "backend"
    if (repo_path / "artisan").exists():  # Laravel
        return "backend"

    return ""


def _get_repo_stat(info: dict) -> dict:
    """Get basic stats for a repo."""
    import sqlite3

    result = {
        "name": info["name"],
        "role": info.get("role", ""),
        "files": 0,
        "symbols": 0,
        "indexed": False,
        "index_age_s": None,
    }

    db_path = info["db_path"]
    if not db_path.exists():
        return result

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        result["files"] = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        result["symbols"] = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        result["indexed"] = True
        result["index_age_s"] = int(time.time() - db_path.stat().st_mtime)
    except Exception:
        pass
    finally:
        conn.close()

    return result


def _format_age(seconds: int | None) -> str:
    """Format an age in seconds as a human-readable string."""
    if seconds is None:
        return "not indexed"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"
