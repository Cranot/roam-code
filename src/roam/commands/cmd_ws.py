"""Workspace commands: multi-repo grouping and cross-repo analysis."""

from __future__ import annotations

import json
import time
from pathlib import Path

import click

from roam.output.formatter import to_json, json_envelope, format_table


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------

@click.group("ws")
@click.pass_context
def ws(ctx):
    """Multi-repo workspace commands."""
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# ws init
# ---------------------------------------------------------------------------

@ws.command("init")
@click.argument("repos", nargs=-1, required=True)
@click.option("--name", default="", help="Workspace name (default: parent dir name)")
@click.pass_context
def ws_init(ctx, repos, name):
    """Initialize a workspace from multiple repo directories.

    REPOS are paths to git repositories (relative or absolute).

    Example:
      roam ws init ../frontend ../backend --name my-platform
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False

    from roam.workspace.config import (
        save_workspace_config, get_repo_paths, get_workspace_db_path,
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

        resolved.append({
            "path": repo_path_str,
            "abs_path": repo_path,
            "name": repo_path.name,
            "db_path": db_path,
            "indexed": indexed,
        })

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
            config["connections"].append({
                "type": "rest-api",
                "frontend": fe["name"],
                "backend": be["name"],
            })

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
        click.echo(to_json(json_envelope("ws-init",
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
        )))
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


# ---------------------------------------------------------------------------
# ws status
# ---------------------------------------------------------------------------

@ws.command("status")
@click.pass_context
def ws_status(ctx):
    """Show workspace status: repos, index ages, cross-repo edges."""
    json_mode = ctx.obj.get('json') if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import open_workspace_db, get_repos, get_cross_edges

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root, readonly=True) as ws_conn:
        ws_repos = get_repos(ws_conn)
        cross_edges = get_cross_edges(ws_conn)

    # Gather per-repo stats
    repo_stats = []
    for info in repo_infos:
        stat = _get_repo_stat(info)
        repo_stats.append(stat)

    if json_mode:
        click.echo(to_json(json_envelope("ws-status",
            summary={
                "workspace": config["workspace"],
                "repos": len(repo_stats),
                "cross_repo_edges": len(cross_edges),
                "verdict": f"{len(repo_stats)} repos, {len(cross_edges)} cross-repo edges",
            },
            repos=repo_stats,
            cross_repo_edges=len(cross_edges),
        )))
        return

    click.echo(f"WORKSPACE: {config['workspace']} ({len(repo_stats)} repos)")
    headers = ["Repo", "Role", "Files", "Symbols", "Indexed"]
    rows = []
    for s in repo_stats:
        age = _format_age(s.get("index_age_s"))
        rows.append([s["name"], s.get("role", ""), str(s["files"]),
                      str(s["symbols"]), age])
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
def ws_resolve(ctx):
    """Detect cross-repo API connections between frontend and backend repos."""
    json_mode = ctx.obj.get('json') if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import (
        open_workspace_db, get_repos, clear_cross_edges, upsert_repo,
    )
    from roam.workspace.api_scanner import (
        scan_frontend_api_calls, scan_backend_routes,
        match_api_endpoints, build_cross_repo_edges,
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

        # Process each connection pair
        for conn_cfg in config.get("connections", []):
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

    if json_mode:
        match_pct = (
            round(100 * total_matched / total_fe_calls)
            if total_fe_calls else 0
        )
        click.echo(to_json(json_envelope("ws-resolve",
            summary={
                "frontend_calls": total_fe_calls,
                "backend_routes": total_be_routes,
                "matched": total_matched,
                "match_pct": match_pct,
                "verdict": (
                    f"{total_matched}/{total_fe_calls} frontend calls matched "
                    f"({match_pct}%)"
                ),
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
        )))
        return

    click.echo()
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


# ---------------------------------------------------------------------------
# ws understand
# ---------------------------------------------------------------------------

@ws.command("understand")
@click.pass_context
def ws_understand(ctx):
    """Full workspace overview: repos, stats, cross-repo connections."""
    json_mode = ctx.obj.get('json') if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import open_workspace_db
    from roam.workspace.aggregator import aggregate_understand

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root, readonly=True) as ws_conn:
        data = aggregate_understand(ws_conn, repo_infos)

    if json_mode:
        click.echo(to_json(json_envelope("ws-understand",
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
        )))
        return

    click.echo(
        f"WORKSPACE: {config['workspace']} "
        f"({len(data['repos'])} repos, {data['total_files']} files, "
        f"{data['total_symbols']} symbols)"
    )
    click.echo()

    for repo in data["repos"]:
        langs = ", ".join(
            f"{l['language']}" for l in repo.get("languages", [])[:3]
        )
        click.echo(f"=== {repo['name']} ({langs}) ===")
        click.echo(
            f"  {repo['files']} files, {repo['symbols']} symbols, "
            f"{repo['edges']} edges"
        )
        if repo.get("key_symbols"):
            keys = ", ".join(s["name"] for s in repo["key_symbols"][:5])
            click.echo(f"  Key: {keys}")
        click.echo()

    if data["cross_repo_connections"]:
        click.echo(f"=== Cross-Repo Connections ({data['cross_repo_edges']} edges) ===")
        for conn_info in data["cross_repo_connections"]:
            click.echo(
                f"  {conn_info['source_repo']} -> {conn_info['target_repo']} "
                f"({conn_info['edge_count']} edges)"
            )
            for sample in conn_info.get("samples", [])[:3]:
                click.echo(
                    f"    {sample.get('http_method', ''):6s} "
                    f"{sample.get('url_pattern', '')}"
                )


# ---------------------------------------------------------------------------
# ws health
# ---------------------------------------------------------------------------

@ws.command("health")
@click.pass_context
def ws_health(ctx):
    """Workspace-wide health report."""
    json_mode = ctx.obj.get('json') if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import open_workspace_db
    from roam.workspace.aggregator import aggregate_health

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root, readonly=True) as ws_conn:
        data = aggregate_health(ws_conn, repo_infos)

    if json_mode:
        click.echo(to_json(json_envelope("ws-health",
            summary={
                "workspace": config["workspace"],
                "workspace_health": data["workspace_health"],
                "cross_repo_edges": data["cross_repo_edges"],
                "coupling_verdict": data["coupling_verdict"],
                "verdict": (
                    f"Health: {data['workspace_health']}/100, "
                    f"coupling: {data['coupling_verdict']}"
                ),
            },
            **data,
        )))
        return

    click.echo(f"VERDICT: Workspace health {data['workspace_health']}/100, "
               f"cross-repo coupling: {data['coupling_verdict']}")
    click.echo()
    headers = ["Repo", "Health", "Files", "Symbols", "Cycles"]
    rows = []
    for r in data["repos"]:
        score = str(r["health_score"]) if r["health_score"] is not None else "?"
        rows.append([r["name"], score, str(r["files"]),
                      str(r["symbols"]), str(r["cycles"])])
    click.echo(format_table(headers, rows))
    click.echo(f"\n  Cross-repo edges: {data['cross_repo_edges']}")


# ---------------------------------------------------------------------------
# ws context
# ---------------------------------------------------------------------------

@ws.command("context")
@click.argument("symbol")
@click.pass_context
def ws_context(ctx, symbol):
    """Cross-repo augmented context for a symbol.

    Searches all repos in the workspace and shows cross-repo callers/callees.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import open_workspace_db
    from roam.workspace.aggregator import cross_repo_context

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root, readonly=True) as ws_conn:
        data = cross_repo_context(ws_conn, symbol, repo_infos)

    if json_mode:
        found_repos = [f["repo"] for f in data["found_in"]]
        click.echo(to_json(json_envelope("ws-context",
            summary={
                "symbol": symbol,
                "found_in_repos": found_repos,
                "cross_repo_edges": len(data["cross_repo_edges"]),
                "verdict": (
                    f"Found in {len(found_repos)} repo(s), "
                    f"{len(data['cross_repo_edges'])} cross-repo edges"
                ),
            },
            **data,
        )))
        return

    if not data["found_in"]:
        click.echo(f"Symbol '{symbol}' not found in any workspace repo.")
        return

    for entry in data["found_in"]:
        click.echo(
            f"[{entry['repo']}] {entry['kind']} {entry['name']}  "
            f"{entry['file_path']}:{entry.get('line_start', '?')}"
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
def ws_trace(ctx, source, target):
    """Trace a path between symbols across repos.

    Shows how SOURCE connects to TARGET, including cross-repo API edges.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False

    ws_root, config = _require_workspace()

    from roam.workspace.config import get_repo_paths
    from roam.workspace.db import open_workspace_db
    from roam.workspace.aggregator import cross_repo_trace

    repo_infos = get_repo_paths(config, ws_root)

    with open_workspace_db(ws_root, readonly=True) as ws_conn:
        data = cross_repo_trace(ws_conn, source, target, repo_infos)

    if json_mode:
        click.echo(to_json(json_envelope("ws-trace",
            summary={
                "source": source,
                "target": target,
                "bridge_edges": len(data["bridge_edges"]),
                "same_repo": data["same_repo"],
                "verdict": data["verdict"],
            },
            **data,
        )))
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
