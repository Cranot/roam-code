"""Show unreferenced exported symbols (dead code)."""

from __future__ import annotations

import math
import os
import time as _time
from collections import defaultdict
from statistics import median

import click

from roam.db.connection import open_db, find_project_root, batched_in, batched_count
from roam.db.queries import UNREFERENCED_EXPORTS
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


_ENTRY_NAMES = {
    # Generic entry points
    "main", "app", "serve", "server", "setup", "run", "cli",
    "handler", "middleware", "route", "index", "init",
    "register", "boot", "start", "execute", "configure",
    "command", "worker", "job", "task", "listener",
    # Vue lifecycle hooks
    "mounted", "created", "beforeMount", "beforeDestroy",
    "beforeCreate", "activated", "deactivated",
    "onMounted", "onUnmounted", "onBeforeMount", "onBeforeUnmount",
    "onActivated", "onDeactivated", "onUpdated", "onBeforeUpdate",
    # React lifecycle
    "componentDidMount", "componentWillUnmount", "componentDidUpdate",
    # Angular lifecycle
    "ngOnInit", "ngOnDestroy", "ngOnChanges", "ngAfterViewInit",
    # Test lifecycle
    "setUp", "tearDown", "beforeEach", "afterEach", "beforeAll", "afterAll",
}
_ENTRY_FILE_BASES = {"server", "app", "main", "cli", "index", "manage",
                      "boot", "bootstrap", "start", "entry", "worker"}
_API_PREFIXES = ("get", "use", "create", "validate", "fetch", "update",
                 "delete", "find", "check", "make", "build", "parse")


def _dead_action(r, file_imported):
    """Compute actionable verdict and confidence % for a dead symbol.

    Uses tiered confidence scoring (inspired by Vulture and Meta's dead
    code system, 2023):
      100% — unreachable code, unused imports, no dynamic usage possible
       90% — unused functions/classes with no string-based references
       80% — unused but in imported file (could be consumed externally)
       70% — API-prefix naming (get*, create*, etc.) or barrel files
       60% — entry-point/lifecycle hooks (frameworks may invoke implicitly)

    Returns (action_string, confidence_pct).
    """
    name = r["name"]
    name_lower = name.lower()
    base = os.path.basename(r["file_path"]).lower()
    name_no_ext = os.path.splitext(base)[0]
    try:
        kind = r["kind"]
    except (KeyError, IndexError):
        kind = ""

    # Entry point / lifecycle hooks (check original case for camelCase hooks)
    if name in _ENTRY_NAMES or name_lower in _ENTRY_NAMES:
        return "INTENTIONAL", 60

    # Python dunders — always intentional
    if name.startswith("__") and name.endswith("__"):
        return "INTENTIONAL", 60

    # File is an entry point and not imported — symbols here are likely intentional
    if not file_imported and name_no_ext in _ENTRY_FILE_BASES:
        return "INTENTIONAL", 60

    # API naming → review before deleting
    if any(name_lower.startswith(p) for p in _API_PREFIXES):
        return "REVIEW", 70

    # Barrel/index file → likely re-exported for public API
    if base.startswith("index.") or base == "__init__.py":
        return "REVIEW", 70

    # Imported file but symbol unused — could be externally consumed
    if file_imported:
        return "SAFE", 80

    # Private naming conventions (_, single underscore prefix) = higher confidence
    if name.startswith("_") and not name.startswith("__"):
        return "SAFE", 95

    # Functions/methods without callers — high confidence
    if kind in ("function", "method", "constructor"):
        return "SAFE", 90

    # Default: classes, variables, etc.
    return "SAFE", 90


# ---------------------------------------------------------------------------
# Dead cluster detection
# ---------------------------------------------------------------------------

def _find_dead_clusters(conn, dead_ids):
    """Find connected components of dead-only symbols.

    Given a set of dead symbol IDs, build a subgraph of edges where both
    endpoints are dead, then find connected components of size >= 2.
    """
    if not dead_ids:
        return []

    dead_set = set(dead_ids)

    # Edges where both source and target are dead — fetch by source, filter by target
    all_edges = batched_in(
        conn,
        "SELECT source_id, target_id FROM edges WHERE source_id IN ({ph})",
        list(dead_set),
    )
    edges = [e for e in all_edges if e["target_id"] in dead_set]

    # Build adjacency (undirected for component finding)
    adj = defaultdict(set)
    for e in edges:
        adj[e["source_id"]].add(e["target_id"])
        adj[e["target_id"]].add(e["source_id"])

    # BFS to find components
    visited = set()
    clusters = []
    for node in adj:
        if node in visited:
            continue
        component = set()
        queue = [node]
        while queue:
            n = queue.pop()
            if n in visited:
                continue
            visited.add(n)
            component.add(n)
            for nb in adj[n]:
                if nb not in visited:
                    queue.append(nb)
        if len(component) >= 2:
            clusters.append(component)

    clusters.sort(key=lambda c: -len(c))
    return clusters


# ---------------------------------------------------------------------------
# Extinction prediction
# ---------------------------------------------------------------------------

def _predict_extinction(conn, target_name):
    """Predict what becomes dead if symbol X is deleted.

    Algorithm:
    1. Find symbol X's ID
    2. Find all symbols that call/reference X (callers of X)
    3. For each caller: check if X is their ONLY callee. If so, they become orphaned.
    4. Recursively propagate: if removing X orphans Y, check Y's callers too.
    5. Return the full cascade.
    """
    from roam.commands.resolve import find_symbol

    sym = find_symbol(conn, target_name)
    if sym is None:
        return None, []

    target_id = sym["id"]

    # Pre-compute out-degree (callee count) for all symbols
    out_deg = {}
    for r in conn.execute("SELECT source_id, COUNT(*) as cnt FROM edges GROUP BY source_id").fetchall():
        out_deg[r["source_id"]] = r["cnt"]

    # Pre-compute callers for any symbol
    def get_callers(sid):
        return [r["source_id"] for r in conn.execute(
            "SELECT source_id FROM edges WHERE target_id = ?", (sid,)
        ).fetchall()]

    # BFS cascade
    cascade = []
    removed = {target_id}
    queue = [target_id]

    while queue:
        current = queue.pop(0)
        callers = get_callers(current)
        for caller_id in callers:
            if caller_id in removed:
                continue
            # Check remaining out-degree after removing all removed targets
            remaining = batched_count(
                conn,
                "SELECT COUNT(*) FROM edges WHERE source_id = ? AND target_id NOT IN ({ph})",
                list(removed),
                pre=[caller_id],
            )
            if remaining == 0:
                # This caller has no remaining callees → orphaned
                removed.add(caller_id)
                queue.append(caller_id)
                # Get name info for the cascade item
                info = conn.execute(
                    "SELECT s.name, s.kind, f.path as file_path, s.line_start "
                    "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
                    (caller_id,),
                ).fetchone()
                if info:
                    cascade.append({
                        "name": info["name"],
                        "kind": info["kind"],
                        "location": loc(info["file_path"], info["line_start"]),
                        "reason": f"only callees removed",
                    })

    return sym, cascade


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------

def _group_dead(dead_items, by):
    """Group dead items by directory or kind."""
    groups = defaultdict(list)
    for item in dead_items:
        if by == "directory":
            key = os.path.dirname(item["file_path"]).replace("\\", "/") or "."
        elif by == "kind":
            key = item["kind"]
        else:
            key = "all"
        groups[key].append(item)
    return sorted(groups.items(), key=lambda x: -len(x[1]))


# ---------------------------------------------------------------------------
# Core dead code analysis (shared between modes)
# ---------------------------------------------------------------------------

def _analyze_dead(conn):
    """Run the full dead code analysis.

    Returns (high, low, imported_files) where high/low are lists of Row objects.
    """
    rows = conn.execute(UNREFERENCED_EXPORTS).fetchall()
    if not rows:
        return [], [], set()

    imported_files = set()
    for r in conn.execute(
        "SELECT DISTINCT target_file_id FROM file_edges"
    ).fetchall():
        imported_files.add(r["target_file_id"])

    # Filter transitively alive (barrel re-exports)
    importers_of = {}
    for fe in conn.execute(
        "SELECT source_file_id, target_file_id FROM file_edges"
    ).fetchall():
        importers_of.setdefault(fe["target_file_id"], set()).add(fe["source_file_id"])

    transitively_alive = set()
    for r in rows:
        fid = r["file_id"]
        if fid not in imported_files:
            continue
        downstream = set()
        frontier = {fid}
        for _ in range(3):
            next_hop = set()
            for f in frontier:
                for imp_fid in importers_of.get(f, set()):
                    if imp_fid not in downstream:
                        downstream.add(imp_fid)
                        next_hop.add(imp_fid)
            frontier = next_hop
            if not frontier:
                break
        if not downstream:
            continue
        alive = batched_in(
            conn,
            "SELECT 1 FROM edges e JOIN symbols s ON e.target_id = s.id "
            "WHERE s.name = ? AND s.file_id IN ({ph}) LIMIT 1",
            list(downstream),
            pre=[r["name"]],
        )
        if alive:
            transitively_alive.add(r["id"])

    rows = [r for r in rows if r["id"] not in transitively_alive]

    high = [r for r in rows if r["file_id"] in imported_files]
    low = [r for r in rows if r["file_id"] not in imported_files]
    return high, low, imported_files


# ---------------------------------------------------------------------------
# Dead code aging, decay, and effort estimation
# ---------------------------------------------------------------------------

def _get_blame_ages(conn, dead_symbols):
    """Get age data for dead symbols by batching git blame per file.

    Returns dict mapping symbol_id to {age_days, last_modified_days, author,
    author_active, dead_loc}.

    Uses existing git_stats.get_blame_for_file() when available, falls back
    to the git_commits + git_file_changes tables for file-level timestamps.
    """
    now = int(_time.time())
    ninety_days_ago = now - (90 * 86400)
    result = {}

    if not dead_symbols:
        return result

    # Build set of active authors (commits in last 90 days)
    active_authors = set()
    for r in conn.execute(
        "SELECT DISTINCT author FROM git_commits WHERE timestamp >= ?",
        (ninety_days_ago,),
    ).fetchall():
        active_authors.add(r["author"])

    # Group dead symbols by file_path for batched blame
    by_file = defaultdict(list)
    for sym in dead_symbols:
        by_file[sym["file_path"]].append(sym)

    project_root = find_project_root()

    for file_path, syms in by_file.items():
        # Try git blame for line-level accuracy
        blame_entries = []
        try:
            from roam.index.git_stats import get_blame_for_file
            blame_entries = get_blame_for_file(project_root, file_path)
        except Exception:
            pass

        if blame_entries:
            # We have line-level blame data
            for sym in syms:
                line_start = sym["line_start"] or 1
                line_end = sym["line_end"] or line_start
                dead_loc = max(1, line_end - line_start + 1)

                # Extract blame for symbol's line range (1-indexed)
                relevant = blame_entries[line_start - 1: line_end]
                if not relevant:
                    relevant = blame_entries[:1] if blame_entries else []

                if relevant:
                    timestamps = [e["timestamp"] for e in relevant if e["timestamp"] > 0]
                    authors = [e["author"] for e in relevant]
                    # Primary author = most lines
                    author_counts = defaultdict(int)
                    for a in authors:
                        author_counts[a] += 1
                    primary_author = max(author_counts, key=author_counts.get) if author_counts else ""

                    oldest_ts = min(timestamps) if timestamps else now
                    newest_ts = max(timestamps) if timestamps else now
                    age_days = max(0, (now - oldest_ts) // 86400)
                    last_modified_days = max(0, (now - newest_ts) // 86400)
                else:
                    age_days = 0
                    last_modified_days = 0
                    primary_author = ""
                    dead_loc = max(1, line_end - line_start + 1)

                result[sym["id"]] = {
                    "age_days": age_days,
                    "last_modified_days": last_modified_days,
                    "author": primary_author,
                    "author_active": primary_author in active_authors,
                    "dead_loc": dead_loc,
                }
        else:
            # Fallback: use git_file_changes table for file-level timestamps
            file_id = syms[0]["file_id"] if syms else None
            oldest_ts = now
            newest_ts = now
            primary_author = ""

            if file_id is not None:
                ts_row = conn.execute(
                    "SELECT MIN(gc.timestamp) as oldest, MAX(gc.timestamp) as newest "
                    "FROM git_file_changes gfc "
                    "JOIN git_commits gc ON gfc.commit_id = gc.id "
                    "WHERE gfc.file_id = ?",
                    (file_id,),
                ).fetchone()
                if ts_row and ts_row["oldest"]:
                    oldest_ts = ts_row["oldest"]
                    newest_ts = ts_row["newest"]

                author_row = conn.execute(
                    "SELECT gc.author, COUNT(*) as cnt "
                    "FROM git_file_changes gfc "
                    "JOIN git_commits gc ON gfc.commit_id = gc.id "
                    "WHERE gfc.file_id = ? "
                    "GROUP BY gc.author ORDER BY cnt DESC LIMIT 1",
                    (file_id,),
                ).fetchone()
                if author_row:
                    primary_author = author_row["author"]

            age_days = max(0, (now - oldest_ts) // 86400)
            last_modified_days = max(0, (now - newest_ts) // 86400)

            for sym in syms:
                line_start = sym["line_start"] or 1
                line_end = sym["line_end"] or line_start
                dead_loc = max(1, line_end - line_start + 1)
                result[sym["id"]] = {
                    "age_days": age_days,
                    "last_modified_days": last_modified_days,
                    "author": primary_author,
                    "author_active": primary_author in active_authors,
                    "dead_loc": dead_loc,
                }

    # Fill in any symbols we missed (no git data at all)
    for sym in dead_symbols:
        if sym["id"] not in result:
            line_start = sym["line_start"] or 1
            line_end = sym["line_end"] or line_start
            result[sym["id"]] = {
                "age_days": 0,
                "last_modified_days": 0,
                "author": "",
                "author_active": False,
                "dead_loc": max(1, line_end - line_start + 1),
            }

    return result


def _decay_score(age_days, cognitive_complexity, cluster_size, importing_files,
                 author_active, dead_loc):
    """0-100 decay score. Higher = more decayed, harder to remove.

    Scoring breakdown (max 100):
      age_points      (max 35): 7 * log2(1 + age_days / 90)
      cc_points       (max 25): cognitive_complexity * 1.5
      coupling_points (max 20): importing_files * 2 + cluster_size * 3
      size_points     (max 10): dead_loc / 20
      author_points   (max 10): 0 if author_active else 10
    """
    age_points = min(35, 7 * math.log2(1 + age_days / 90))
    cc_points = min(25, cognitive_complexity * 1.5)
    coupling_points = min(20, importing_files * 2 + cluster_size * 3)
    size_points = min(10, dead_loc / 20)
    author_points = 0 if author_active else 10
    return min(100, int(round(
        age_points + cc_points + coupling_points + size_points + author_points
    )))


def _estimate_removal_minutes(dead_loc, cognitive_complexity, importing_files,
                              cluster_size, age_years, author_active):
    """Estimate minutes to remove a dead symbol.

    Factors:
      base             = dead_loc * 1.0
      complexity_factor = 1.0 + (cognitive_complexity / 20.0)
      coupling_factor   = 1.0 + (0.05 * importing_files) + (0.1 * max(0, cluster_size - 1))
      age_factor        = 1.0 + (0.1 * min(age_years, 10))
      author_factor     = 0.8 if author_active else 1.0
    """
    base = dead_loc * 1.0
    complexity_factor = 1.0 + (cognitive_complexity / 20.0)
    coupling_factor = 1.0 + (0.05 * importing_files) + (0.1 * max(0, cluster_size - 1))
    age_factor = 1.0 + (0.1 * min(age_years, 10))
    author_factor = 0.8 if author_active else 1.0
    return round(base * complexity_factor * coupling_factor * age_factor * author_factor, 1)


def _decay_tier(score):
    """Classify decay score into human-readable tier.

    Fresh (0-25), Stale (26-50), Decayed (51-75), Fossilized (76-100).
    """
    if score <= 25:
        return "Fresh"
    elif score <= 50:
        return "Stale"
    elif score <= 75:
        return "Decayed"
    else:
        return "Fossilized"


def _get_symbol_complexities(conn, symbol_ids):
    """Fetch cognitive_complexity from symbol_metrics for a set of symbol IDs.

    Returns dict mapping symbol_id to cognitive_complexity (float).
    """
    if not symbol_ids:
        return {}
    rows = batched_in(
        conn,
        "SELECT symbol_id, cognitive_complexity FROM symbol_metrics "
        "WHERE symbol_id IN ({ph})",
        list(symbol_ids),
    )
    return {r["symbol_id"]: r["cognitive_complexity"] or 0 for r in rows}


def _get_importing_file_counts(conn, file_ids):
    """Count how many files import each given file_id.

    Returns dict mapping file_id to count of importing files.
    """
    if not file_ids:
        return {}
    rows = batched_in(
        conn,
        "SELECT target_file_id, COUNT(*) as cnt FROM file_edges "
        "WHERE target_file_id IN ({ph}) GROUP BY target_file_id",
        list(file_ids),
    )
    return {r["target_file_id"]: r["cnt"] for r in rows}


def _build_cluster_membership(clusters):
    """Build a dict mapping symbol_id to cluster_size from cluster list.

    Each cluster is a set of symbol IDs. Returns {symbol_id: cluster_size}.
    """
    membership = {}
    for cluster_set in clusters:
        size = len(cluster_set)
        for sid in cluster_set:
            membership[sid] = size
    return membership


def _compute_extended_data(conn, all_items, clusters_for_aging):
    """Compute aging, decay, and effort data for dead symbols.

    Returns dict mapping symbol_id to {aging: {...}, effort: {...}, decay_score: int}.
    """
    if not all_items:
        return {}

    symbol_ids = {r["id"] for r in all_items}
    file_ids = {r["file_id"] for r in all_items}

    # Gather all needed data
    blame_ages = _get_blame_ages(conn, all_items)
    complexities = _get_symbol_complexities(conn, symbol_ids)
    importer_counts = _get_importing_file_counts(conn, file_ids)
    cluster_membership = _build_cluster_membership(clusters_for_aging)

    result = {}
    for r in all_items:
        sid = r["id"]
        aging = blame_ages.get(sid, {
            "age_days": 0, "last_modified_days": 0,
            "author": "", "author_active": False, "dead_loc": 1,
        })
        cc = complexities.get(sid, 0)
        importing_files = importer_counts.get(r["file_id"], 0)
        cluster_size = cluster_membership.get(sid, 1)
        age_days = aging["age_days"]
        dead_loc = aging["dead_loc"]
        author_active = aging["author_active"]

        dscore = _decay_score(
            age_days, cc, cluster_size, importing_files,
            author_active, dead_loc,
        )
        age_years = age_days / 365.25
        removal_min = _estimate_removal_minutes(
            dead_loc, cc, importing_files, cluster_size,
            age_years, author_active,
        )
        complexity_factor = round(1.0 + (cc / 20.0), 2)
        coupling_factor = round(
            1.0 + (0.05 * importing_files) + (0.1 * max(0, cluster_size - 1)), 2
        )

        result[sid] = {
            "aging": {
                "age_days": age_days,
                "last_modified_days": aging["last_modified_days"],
                "author": aging["author"],
                "author_active": author_active,
                "dead_loc": dead_loc,
            },
            "effort": {
                "removal_minutes": removal_min,
                "complexity_factor": complexity_factor,
                "coupling_factor": coupling_factor,
            },
            "decay_score": dscore,
        }
    return result


def _extended_summary(extended_data):
    """Compute aggregate summary stats from extended data.

    Returns dict with total_dead_loc, total_effort_hours, median_age_days,
    and decay_distribution.
    """
    if not extended_data:
        return {
            "total_dead_loc": 0,
            "total_effort_hours": 0.0,
            "median_age_days": 0,
            "decay_distribution": {
                "fresh": 0, "stale": 0, "decayed": 0, "fossilized": 0,
            },
        }

    total_loc = sum(d["aging"]["dead_loc"] for d in extended_data.values())
    total_minutes = sum(d["effort"]["removal_minutes"] for d in extended_data.values())
    ages = [d["aging"]["age_days"] for d in extended_data.values()]
    scores = [d["decay_score"] for d in extended_data.values()]

    dist = {"fresh": 0, "stale": 0, "decayed": 0, "fossilized": 0}
    for s in scores:
        tier = _decay_tier(s).lower()
        dist[tier] = dist.get(tier, 0) + 1

    return {
        "total_dead_loc": total_loc,
        "total_effort_hours": round(total_minutes / 60.0, 1),
        "median_age_days": int(median(ages)) if ages else 0,
        "decay_distribution": dist,
    }


@click.command()
@click.option("--all", "show_all", is_flag=True, help="Include low-confidence results")
@click.option("--by-directory", "by_directory", is_flag=True,
              help="Group dead symbols by parent directory")
@click.option("--by-kind", "by_kind", is_flag=True,
              help="Group dead symbols by symbol kind")
@click.option("--summary", "summary_only", is_flag=True,
              help="Only show aggregate counts, no individual symbols")
@click.option("--clusters", "show_clusters", is_flag=True,
              help="Detect dead subgraphs (groups of dead symbols referencing only each other)")
@click.option("--extinction", "extinction_target", default=None,
              help="Predict what else becomes dead if you delete this symbol")
@click.option("--aging", "show_aging", is_flag=True,
              help="Add age/staleness columns to output")
@click.option("--effort", "show_effort", is_flag=True,
              help="Add effort estimation columns to output")
@click.option("--decay", "show_decay", is_flag=True,
              help="Show decay score and distribution")
@click.option("--sort-by-age", "sort_by_age", is_flag=True,
              help="Sort dead code oldest-first")
@click.option("--sort-by-effort", "sort_by_effort", is_flag=True,
              help="Sort by removal effort (highest first)")
@click.option("--sort-by-decay", "sort_by_decay", is_flag=True,
              help="Sort by decay score (most fossilized first)")
@click.pass_context
def dead(ctx, show_all, by_directory, by_kind, summary_only, show_clusters,
         extinction_target, show_aging, show_effort, show_decay,
         sort_by_age, sort_by_effort, sort_by_decay):
    """Show unreferenced exported symbols (dead code)."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    # Any extended flag implies we need extended data
    need_extended = show_aging or show_effort or show_decay or sort_by_age or sort_by_effort or sort_by_decay

    with open_db(readonly=True) as conn:
        # --- Extinction mode (separate flow) ---
        if extinction_target:
            sym, cascade = _predict_extinction(conn, extinction_target)
            if sym is None:
                if json_mode:
                    click.echo(to_json(json_envelope("dead",
                        summary={"error": f"Symbol not found: {extinction_target}"},
                    )))
                else:
                    click.echo(f"Symbol not found: {extinction_target}")
                return

            if json_mode:
                click.echo(to_json(json_envelope("dead",
                    summary={"extinction_cascade": len(cascade)},
                    mode="extinction",
                    target=extinction_target,
                    extinction_cascade=cascade,
                )))
            else:
                click.echo(f"=== Extinction Cascade for: {extinction_target} ===\n")
                if cascade:
                    click.echo(f"Deleting {extinction_target} would orphan {len(cascade)} symbol(s):\n")
                    table_rows = []
                    for c in cascade:
                        table_rows.append([
                            c["name"], abbrev_kind(c["kind"]),
                            c["location"], c["reason"],
                        ])
                    click.echo(format_table(
                        ["Name", "Kind", "Location", "Reason"],
                        table_rows,
                    ))
                else:
                    click.echo("No additional symbols would become dead.")
            return

        # --- Standard dead code analysis ---
        high, low, imported_files = _analyze_dead(conn)
        all_items = high + low

        if not all_items:
            if json_mode:
                click.echo(to_json(json_envelope("dead",
                    summary={"safe": 0, "review": 0, "intentional": 0},
                    high_confidence=[], low_confidence=[],
                )))
            else:
                click.echo("=== Unreferenced Exports (0) ===")
                click.echo("  (none -- all exports are referenced)")
            return

        # Compute action verdicts
        all_dead = [(r, *_dead_action(r, r["file_id"] in imported_files)) for r in all_items]
        n_safe = sum(1 for _, a, _c in all_dead if a == "SAFE")
        n_review = sum(1 for _, a, _c in all_dead if a == "REVIEW")
        n_intent = sum(1 for _, a, _c in all_dead if a == "INTENTIONAL")

        # --- Cluster detection (also needed for extended data) ---
        clusters_data = []
        raw_clusters = []
        if show_clusters or need_extended:
            dead_ids = {r["id"] for r in all_items}
            raw_clusters = _find_dead_clusters(conn, dead_ids)
            if show_clusters:
                id_to_info = {}
                if raw_clusters:
                    all_cluster_ids = set()
                    for c in raw_clusters:
                        all_cluster_ids.update(c)
                    for r in batched_in(
                        conn,
                        "SELECT s.id, s.name, s.kind, f.path as file_path, s.line_start "
                        "FROM symbols s JOIN files f ON s.file_id = f.id "
                        "WHERE s.id IN ({ph})",
                        list(all_cluster_ids),
                    ):
                        id_to_info[r["id"]] = r

                for cluster_set in raw_clusters:
                    syms = []
                    for sid in sorted(cluster_set):
                        info = id_to_info.get(sid)
                        if info:
                            syms.append({
                                "name": info["name"],
                                "kind": info["kind"],
                                "location": loc(info["file_path"], info["line_start"]),
                            })
                    clusters_data.append({"size": len(cluster_set), "symbols": syms})

        # --- Extended data (aging / effort / decay) ---
        extended_data = {}
        ext_summary = {}
        if need_extended:
            extended_data = _compute_extended_data(conn, all_items, raw_clusters)
            ext_summary = _extended_summary(extended_data)

        # --- Sorting by extended fields ---
        if sort_by_age and extended_data:
            all_items = sorted(
                all_items,
                key=lambda r: extended_data.get(r["id"], {}).get("aging", {}).get("age_days", 0),
                reverse=True,
            )
            high = [r for r in all_items if r["file_id"] in imported_files]
            low = [r for r in all_items if r["file_id"] not in imported_files]
        elif sort_by_effort and extended_data:
            all_items = sorted(
                all_items,
                key=lambda r: extended_data.get(r["id"], {}).get("effort", {}).get("removal_minutes", 0),
                reverse=True,
            )
            high = [r for r in all_items if r["file_id"] in imported_files]
            low = [r for r in all_items if r["file_id"] not in imported_files]
        elif sort_by_decay and extended_data:
            all_items = sorted(
                all_items,
                key=lambda r: extended_data.get(r["id"], {}).get("decay_score", 0),
                reverse=True,
            )
            high = [r for r in all_items if r["file_id"] in imported_files]
            low = [r for r in all_items if r["file_id"] not in imported_files]

        # --- Grouping ---
        group_by = None
        groups_data = []
        if by_directory:
            group_by = "directory"
        elif by_kind:
            group_by = "kind"

        if group_by:
            grouped = _group_dead(all_items, group_by)
            for key, items in grouped:
                verdicts = [_dead_action(r, r["file_id"] in imported_files)[0] for r in items]
                groups_data.append({
                    "key": key,
                    "count": len(items),
                    "safe": sum(1 for v in verdicts if v == "SAFE"),
                    "review": sum(1 for v in verdicts if v == "REVIEW"),
                    "intentional": sum(1 for v in verdicts if v == "INTENTIONAL"),
                })

        # --- JSON output ---
        if json_mode:
            def _build_sym_dict(r, file_imported):
                d = {
                    "name": r["name"], "kind": r["kind"],
                    "location": loc(r["file_path"], r["line_start"]),
                    "action": _dead_action(r, file_imported)[0],
                    "confidence": _dead_action(r, file_imported)[1],
                }
                if need_extended and r["id"] in extended_data:
                    ext = extended_data[r["id"]]
                    d["aging"] = ext["aging"]
                    d["effort"] = ext["effort"]
                    d["decay_score"] = ext["decay_score"]
                return d

            summary = {"safe": n_safe, "review": n_review, "intentional": n_intent}
            if need_extended:
                summary.update(ext_summary)

            envelope = json_envelope("dead",
                summary=summary,
                high_confidence=[_build_sym_dict(r, True) for r in high],
                low_confidence=[_build_sym_dict(r, False) for r in low],
            )
            if group_by:
                envelope["grouping"] = group_by
                envelope["groups"] = groups_data
            if show_clusters:
                envelope["dead_clusters"] = clusters_data
            click.echo(to_json(envelope))
            return

        # --- Text: summary-only mode ---
        if summary_only:
            click.echo(f"Dead exports: {len(all_items)} "
                        f"({n_safe} safe, {n_review} review, {n_intent} intentional)")
            if need_extended and ext_summary:
                click.echo(f"  Total dead LOC: {ext_summary['total_dead_loc']}")
                click.echo(f"  Median age: {ext_summary['median_age_days']} days")
                click.echo(f"  Total removal effort: {ext_summary['total_effort_hours']} hours")
                dist = ext_summary["decay_distribution"]
                click.echo(f"  Decay: {dist['fresh']} fresh, {dist['stale']} stale, "
                            f"{dist['decayed']} decayed, {dist['fossilized']} fossilized")
            if group_by and groups_data:
                click.echo(f"\nBy {group_by}:")
                for g in groups_data[:20]:
                    click.echo(f"  {g['key']:<50s}  {g['count']:>3d}  "
                                f"(safe={g['safe']}, review={g['review']})")
            if show_clusters and clusters_data:
                click.echo(f"\nDead clusters: {len(clusters_data)}")
                for i, cl in enumerate(clusters_data[:10], 1):
                    names = ", ".join(s["name"] for s in cl["symbols"][:5])
                    more = f" +{cl['size'] - 5}" if cl["size"] > 5 else ""
                    click.echo(f"  cluster {i} ({cl['size']} syms): {names}{more}")
            return

        # --- Text: grouped mode ---
        if group_by and groups_data:
            click.echo(f"=== Unreferenced Exports by {group_by} ({len(all_items)} total) ===")
            click.echo(f"  Actions: {n_safe} safe to delete, {n_review} need review, "
                        f"{n_intent} likely intentional\n")
            table_rows = []
            for g in groups_data:
                table_rows.append([
                    g["key"],
                    str(g["count"]),
                    str(g["safe"]),
                    str(g["review"]),
                    str(g["intentional"]),
                ])
            click.echo(format_table(
                [group_by.title(), "Total", "Safe", "Review", "Intentional"],
                table_rows,
                budget=30,
            ))
            if show_clusters and clusters_data:
                click.echo(f"\n=== Dead Clusters ({len(clusters_data)}) ===")
                for i, cl in enumerate(clusters_data[:10], 1):
                    names = ", ".join(s["name"] for s in cl["symbols"][:6])
                    more = f" +{cl['size'] - 6}" if cl["size"] > 6 else ""
                    click.echo(f"  cluster {i} ({cl['size']} syms): {names}{more}")
            return

        # --- Text: standard output ---
        click.echo(f"=== Unreferenced Exports ({len(high)} high confidence, {len(low)} low) ===")
        click.echo(f"  Actions: {n_safe} safe to delete, {n_review} need review, "
                    f"{n_intent} likely intentional")

        # Show extended summary if any extended flag is set
        if need_extended and ext_summary:
            click.echo(f"  Total dead LOC: {ext_summary['total_dead_loc']}  "
                        f"Median age: {ext_summary['median_age_days']}d  "
                        f"Removal effort: {ext_summary['total_effort_hours']}h")
            if show_decay:
                dist = ext_summary["decay_distribution"]
                click.echo(f"  Decay: {dist['fresh']} fresh, {dist['stale']} stale, "
                            f"{dist['decayed']} decayed, {dist['fossilized']} fossilized")
        click.echo()

        # Build imported-by lookup for high-confidence results
        if high:
            high_file_ids = {r["file_id"] for r in high}
            importer_rows = batched_in(
                conn,
                "SELECT fe.target_file_id, f.path "
                "FROM file_edges fe JOIN files f ON fe.source_file_id = f.id "
                "WHERE fe.target_file_id IN ({ph})",
                list(high_file_ids),
            )
            importers_by_file = {}
            for ir in importer_rows:
                importers_by_file.setdefault(ir["target_file_id"], []).append(ir["path"])

            # Count how many other exported symbols in the same file ARE referenced
            referenced_counts = {}
            for fid in high_file_ids:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM symbols s "
                    "WHERE s.file_id = ? AND s.is_exported = 1 "
                    "AND s.id IN (SELECT target_id FROM edges)",
                    (fid,),
                ).fetchone()[0]
                referenced_counts[fid] = cnt

            click.echo(f"-- High confidence ({len(high)}) --")
            click.echo("(file is imported but symbol has no references)")

            # Build table headers and rows based on active flags
            headers = ["Action", "Name", "Kind", "Location", "Reason"]
            if show_aging:
                headers.extend(["Age(d)", "LastMod(d)", "Author"])
            if show_effort:
                headers.extend(["LOC", "Effort(m)"])
            if show_decay:
                headers.extend(["Decay", "Tier"])

            table_rows = []
            for r in high:
                imp_list = importers_by_file.get(r["file_id"], [])
                n_importers = len(imp_list)
                n_siblings = referenced_counts.get(r["file_id"], 0)
                if n_siblings > 0:
                    reason = f"{n_importers} importers use {n_siblings} siblings, skip this"
                else:
                    reason = f"{n_importers} importers, none use any export"
                action, confidence = _dead_action(r, True)
                row = [
                    f"{action} {confidence}%",
                    r["name"],
                    abbrev_kind(r["kind"]),
                    loc(r["file_path"], r["line_start"]),
                    reason,
                ]
                if need_extended:
                    ext = extended_data.get(r["id"], {})
                    aging = ext.get("aging", {})
                    effort = ext.get("effort", {})
                    dscore = ext.get("decay_score", 0)
                    if show_aging:
                        row.extend([
                            str(aging.get("age_days", 0)),
                            str(aging.get("last_modified_days", 0)),
                            aging.get("author", "")[:20],
                        ])
                    if show_effort:
                        row.extend([
                            str(aging.get("dead_loc", 0)),
                            str(effort.get("removal_minutes", 0)),
                        ])
                    if show_decay:
                        row.extend([
                            str(dscore),
                            _decay_tier(dscore),
                        ])
                table_rows.append(row)
            click.echo(format_table(headers, table_rows, budget=50))

        if show_all and low:
            click.echo(f"\n-- Low confidence ({len(low)}) --")
            click.echo("(file has no importers — may be entry point or used by unparsed files)")

            headers = ["Action", "Name", "Kind", "Location"]
            if show_aging:
                headers.extend(["Age(d)", "LastMod(d)", "Author"])
            if show_effort:
                headers.extend(["LOC", "Effort(m)"])
            if show_decay:
                headers.extend(["Decay", "Tier"])

            table_rows = []
            for r in low:
                action, confidence = _dead_action(r, False)
                row = [
                    f"{action} {confidence}%",
                    r["name"],
                    abbrev_kind(r["kind"]),
                    loc(r["file_path"], r["line_start"]),
                ]
                if need_extended:
                    ext = extended_data.get(r["id"], {})
                    aging = ext.get("aging", {})
                    effort = ext.get("effort", {})
                    dscore = ext.get("decay_score", 0)
                    if show_aging:
                        row.extend([
                            str(aging.get("age_days", 0)),
                            str(aging.get("last_modified_days", 0)),
                            aging.get("author", "")[:20],
                        ])
                    if show_effort:
                        row.extend([
                            str(aging.get("dead_loc", 0)),
                            str(effort.get("removal_minutes", 0)),
                        ])
                    if show_decay:
                        row.extend([
                            str(dscore),
                            _decay_tier(dscore),
                        ])
                table_rows.append(row)
            click.echo(format_table(headers, table_rows, budget=50))
        elif low:
            click.echo(f"\n({len(low)} low-confidence results hidden — use --all to show)")

        # Dead clusters
        if show_clusters and clusters_data:
            click.echo(f"\n=== Dead Clusters ({len(clusters_data)}) ===")
            click.echo("(groups of dead symbols that only reference each other)")
            for i, cl in enumerate(clusters_data[:10], 1):
                names = ", ".join(s["name"] for s in cl["symbols"][:6])
                more = f" +{cl['size'] - 6}" if cl["size"] > 6 else ""
                click.echo(f"  cluster {i} ({cl['size']} syms): {names}{more}")
                for s in cl["symbols"][:6]:
                    click.echo(f"    {abbrev_kind(s['kind'])}  {s['name']}  {s['location']}")
            if len(clusters_data) > 10:
                click.echo(f"  (+{len(clusters_data) - 10} more clusters)")

        # Check for files with no extracted symbols
        unparsed = conn.execute(
            "SELECT COUNT(*) FROM files f "
            "WHERE NOT EXISTS (SELECT 1 FROM symbols s WHERE s.file_id = f.id)"
        ).fetchone()[0]
        if unparsed:
            click.echo(f"\nNote: {unparsed} files had no symbols extracted (may cause false positives)")
