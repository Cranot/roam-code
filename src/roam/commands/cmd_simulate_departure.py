"""Simulate what happens when a developer leaves the team.

Identifies files, symbols, and modules that become orphaned or under-owned
when one or more developers depart.  Combines git blame ownership analysis,
CODEOWNERS cross-referencing, PageRank-weighted symbol importance, and
cluster-level impact to produce a comprehensive knowledge-loss risk report.
"""

from __future__ import annotations

import fnmatch
import math
import time
from collections import defaultdict
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root, batched_in
from roam.output.formatter import (
    abbrev_kind,
    to_json,
    json_envelope,
)
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Time-decayed ownership computation
# ---------------------------------------------------------------------------

_HALF_LIFE_DAYS = 365  # weight halves every year


def _decay_weight(epoch: int, now: int) -> float:
    """Exponential time-decay weight for a commit timestamp."""
    if not epoch:
        return 0.0
    age_days = max(0, (now - epoch)) / 86400
    return math.pow(0.5, age_days / _HALF_LIFE_DAYS)


def compute_file_ownership(conn, file_ids: list[int], now: int | None = None):
    """Compute per-file developer ownership with time-decayed blame.

    Returns ``{file_id: {author: ownership_share}}`` where shares sum to ~1.0
    per file.  Uses churn (lines_added + lines_removed) weighted by recency.
    """
    if now is None:
        now = int(time.time())

    if not file_ids:
        return {}

    rows = batched_in(
        conn,
        """
        SELECT gfc.file_id, gc.author,
               gfc.lines_added + gfc.lines_removed AS churn,
               gc.timestamp
        FROM git_file_changes gfc
        JOIN git_commits gc ON gfc.commit_id = gc.id
        WHERE gfc.file_id IN ({ph})
        """,
        file_ids,
    )

    # Accumulate weighted churn per (file, author)
    file_author_weight: dict[int, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for r in rows:
        fid = r["file_id"]
        author = r["author"]
        churn = (r["churn"] or 0)
        w = _decay_weight(r["timestamp"] or 0, now)
        file_author_weight[fid][author] += churn * w

    # Normalise to shares
    result: dict[int, dict[str, float]] = {}
    for fid, authors in file_author_weight.items():
        total = sum(authors.values())
        if total <= 0:
            continue
        result[fid] = {a: v / total for a, v in authors.items()}

    return result


# ---------------------------------------------------------------------------
# CODEOWNERS parsing
# ---------------------------------------------------------------------------


def parse_codeowners(project_root: Path) -> list[tuple[str, list[str]]]:
    """Parse a CODEOWNERS file and return (pattern, [owners]) pairs.

    Searches for CODEOWNERS in standard locations:
    - CODEOWNERS
    - .github/CODEOWNERS
    - docs/CODEOWNERS

    Returns patterns in order (last match wins, as per GitHub convention).
    """
    candidates = [
        project_root / "CODEOWNERS",
        project_root / ".github" / "CODEOWNERS",
        project_root / "docs" / "CODEOWNERS",
    ]
    codeowners_path = None
    for c in candidates:
        if c.exists():
            codeowners_path = c
            break

    if codeowners_path is None:
        return []

    rules: list[tuple[str, list[str]]] = []
    try:
        text = codeowners_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern = parts[0]
        owners = parts[1:]
        rules.append((pattern, owners))

    return rules


def resolve_codeowner(file_path: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    """Resolve CODEOWNERS for a file path.  Last matching rule wins.

    CODEOWNERS pattern semantics:
    - ``*.py``        -- matches any .py file anywhere
    - ``docs/``       -- matches anything under docs/
    - ``/src/core/``  -- matches anything under src/core/ (anchored to root)
    - ``src/app.py``  -- matches src/app.py exactly (or anywhere if no /)
    """
    matched_owners: list[str] = []
    normalised = file_path.replace("\\", "/")

    for pattern, owners in rules:
        raw = pattern

        # Strip leading / (anchors to repo root — but paths are already relative)
        if raw.startswith("/"):
            raw = raw[1:]

        # Directory pattern: "dir/" matches "dir/**"
        if raw.endswith("/"):
            if normalised.startswith(raw) or ("/" + raw) in ("/" + normalised):
                matched_owners = owners
                continue

        # Use fnmatch for glob patterns (*, ?)
        if fnmatch.fnmatch(normalised, raw):
            matched_owners = owners
            continue

        # If pattern has no directory separator, match against basename too
        if "/" not in raw:
            basename = normalised.rsplit("/", 1)[-1]
            if fnmatch.fnmatch(basename, raw):
                matched_owners = owners
                continue

        # Also try matching with any prefix for non-anchored patterns
        if fnmatch.fnmatch(normalised, "*/" + raw):
            matched_owners = owners

    return matched_owners


def _normalise_identity(name: str) -> str:
    """Lowercase + strip whitespace for identity matching."""
    return name.strip().lower()


def _identity_matches(developer: str, candidate: str) -> bool:
    """Check if a developer identifier matches a candidate (name or email).

    Supports partial matching: 'alice' matches 'Alice Smith' and
    'alice@example.com'.
    """
    dev = _normalise_identity(developer)
    cand = _normalise_identity(candidate)
    # Exact match
    if dev == cand:
        return True
    # Substring match (name in full name, or email local part)
    if dev in cand:
        return True
    # CODEOWNERS often use @username
    if cand.startswith("@") and dev == cand[1:]:
        return True
    if dev.startswith("@") and dev[1:] == cand:
        return True
    return False


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------

_HIGH_THRESHOLD = 0.50
_MEDIUM_THRESHOLD = 0.30


def _verdict(critical: int, high: int, medium: int, total: int) -> str:
    """Determine overall verdict string."""
    if critical > 0:
        return f"CRITICAL -- {total} files at risk, {critical} with sole CODEOWNER"
    if high > 0:
        return f"HIGH RISK -- {total} files at risk, {high} with >50% ownership"
    if medium > 0:
        return f"MEDIUM RISK -- {total} files at risk"
    return "LOW RISK -- minimal knowledge concentration"


def _severity_label(critical: int, high: int, medium: int) -> str:
    """Short severity for JSON."""
    if critical > 0:
        return "CRITICAL"
    if high > 0:
        return "HIGH"
    if medium > 0:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------


def _analyse_departure(conn, project_root, developers: list[str]):
    """Run the full departure simulation.

    Returns a dict with all result data ready for output formatting.
    """
    now = int(time.time())

    # 1. Get all files
    all_files = conn.execute(
        "SELECT id, path FROM files ORDER BY path"
    ).fetchall()

    if not all_files:
        return None

    file_ids = [f["id"] for f in all_files]
    file_map = {f["id"]: f["path"] for f in all_files}

    # 2. Compute ownership for all files
    ownership = compute_file_ownership(conn, file_ids, now=now)

    # 3. CODEOWNERS integration
    codeowners_rules = parse_codeowners(project_root)

    # 4. Find files where departing devs have significant ownership
    critical_files = []  # sole CODEOWNER + >50%
    high_risk_files = []  # >50% ownership
    medium_risk_files = []  # >30% ownership

    for fid, path in file_map.items():
        shares = ownership.get(fid, {})
        # Sum ownership across all departing developers
        dev_share = 0.0
        for dev in developers:
            for author, share in shares.items():
                if _identity_matches(dev, author):
                    dev_share += share
                    break

        if dev_share < _MEDIUM_THRESHOLD:
            continue

        # Check CODEOWNERS
        codeowners = resolve_codeowner(path, codeowners_rules)
        is_sole_codeowner = False
        if codeowners:
            # Check if departing dev is the sole codeowner
            non_departing_owners = []
            for co in codeowners:
                is_departing = False
                for dev in developers:
                    if _identity_matches(dev, co):
                        is_departing = True
                        break
                if not is_departing:
                    non_departing_owners.append(co)
            if len(non_departing_owners) == 0:
                is_sole_codeowner = True

        entry = {
            "file_id": fid,
            "path": path,
            "ownership_pct": round(dev_share * 100),
            "ownership_share": round(dev_share, 3),
            "codeowners": codeowners,
            "is_sole_codeowner": is_sole_codeowner,
        }

        if is_sole_codeowner and dev_share >= _HIGH_THRESHOLD:
            critical_files.append(entry)
        elif dev_share >= _HIGH_THRESHOLD:
            high_risk_files.append(entry)
        else:
            medium_risk_files.append(entry)

    # Sort each list by ownership descending
    for lst in (critical_files, high_risk_files, medium_risk_files):
        lst.sort(key=lambda x: x["ownership_share"], reverse=True)

    # 5. Get PageRank data for at-risk files
    at_risk_file_ids = [
        f["file_id"]
        for f in critical_files + high_risk_files + medium_risk_files
    ]

    # Build a mapping of file_id -> list of important symbols
    file_symbols: dict[int, list[dict]] = defaultdict(list)
    if at_risk_file_ids:
        sym_rows = batched_in(
            conn,
            """
            SELECT s.id, s.name, s.kind, s.file_id, s.line_start,
                   COALESCE(gm.pagerank, 0) AS pagerank
            FROM symbols s
            LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
            WHERE s.file_id IN ({ph})
            ORDER BY pagerank DESC
            """,
            at_risk_file_ids,
        )
        for r in sym_rows:
            file_symbols[r["file_id"]].append({
                "name": r["name"],
                "kind": r["kind"],
                "line": r["line_start"],
                "pagerank": round(r["pagerank"], 4) if r["pagerank"] else 0,
            })

    # Annotate file entries with pagerank of top symbol
    for entry in critical_files + high_risk_files + medium_risk_files:
        fid = entry["file_id"]
        syms = file_symbols.get(fid, [])
        entry["top_pagerank"] = syms[0]["pagerank"] if syms else 0

    # 6. Key symbols at risk (highest PageRank across all at-risk files)
    all_syms = []
    for fid in at_risk_file_ids:
        for s in file_symbols.get(fid, []):
            s_copy = dict(s)
            s_copy["path"] = file_map[fid]
            all_syms.append(s_copy)
    all_syms.sort(key=lambda x: x["pagerank"], reverse=True)
    key_symbols = all_syms[:20]

    # 7. Cluster / module impact
    # Count how many clusters lose their primary contributor
    cluster_rows = conn.execute("""
        SELECT c.cluster_id, c.cluster_label, s.file_id
        FROM clusters c
        JOIN symbols s ON c.symbol_id = s.id
    """).fetchall()

    cluster_files: dict[int, set[int]] = defaultdict(set)
    cluster_labels: dict[int, str] = {}
    for r in cluster_rows:
        cid = r["cluster_id"]
        cluster_files[cid].add(r["file_id"])
        if r["cluster_label"]:
            cluster_labels[cid] = r["cluster_label"]

    at_risk_set = set(at_risk_file_ids)
    total_clusters = len(cluster_files)
    affected_clusters = 0
    affected_cluster_list = []
    for cid, fids in cluster_files.items():
        # A cluster is "affected" if >30% of its files are at-risk
        overlap = fids & at_risk_set
        if len(overlap) > 0 and len(overlap) / len(fids) >= 0.3:
            affected_clusters += 1
            affected_cluster_list.append({
                "cluster_id": cid,
                "label": cluster_labels.get(cid, f"cluster-{cid}"),
                "total_files": len(fids),
                "at_risk_files": len(overlap),
            })

    affected_cluster_list.sort(
        key=lambda x: x["at_risk_files"], reverse=True
    )

    # 8. Generate recommendations
    recommendations = _generate_recommendations(
        critical_files, high_risk_files, medium_risk_files, affected_clusters,
    )

    total_at_risk = len(critical_files) + len(high_risk_files) + len(medium_risk_files)
    verdict_str = _verdict(
        len(critical_files), len(high_risk_files),
        len(medium_risk_files), total_at_risk,
    )
    severity = _severity_label(
        len(critical_files), len(high_risk_files), len(medium_risk_files),
    )

    return {
        "developers": developers,
        "verdict": verdict_str,
        "severity": severity,
        "total_files_at_risk": total_at_risk,
        "critical_files": critical_files,
        "high_risk_files": high_risk_files,
        "medium_risk_files": medium_risk_files,
        "key_symbols": key_symbols,
        "total_clusters": total_clusters,
        "affected_clusters": affected_clusters,
        "affected_cluster_list": affected_cluster_list,
        "recommendations": recommendations,
    }


def _generate_recommendations(critical, high, medium, affected_clusters):
    """Generate actionable recommendations based on analysis results."""
    recs = []

    if critical:
        top = critical[0]
        recs.append(
            f"Pair program on {top['path']} (highest risk, "
            f"{top['ownership_pct']}% ownership + sole CODEOWNER)"
        )
        if len(critical) > 1:
            recs.append(
                f"Add backup CODEOWNERS for {len(critical)} critical files"
            )

    if high:
        top = high[0]
        recs.append(
            f"Spread knowledge of {top['path']} ({top['ownership_pct']}% ownership)"
        )
        if len(high) > 3:
            recs.append(
                f"Schedule knowledge-transfer sessions for {len(high)} high-risk files"
            )

    if affected_clusters > 0:
        recs.append(
            f"Document architecture decisions in {affected_clusters} affected modules"
        )

    if medium and not critical and not high:
        recs.append(
            "Low overall risk -- consider gradual knowledge sharing"
        )

    if not critical and not high and not medium:
        recs.append("No significant knowledge concentration detected")

    return recs


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command()
@click.argument("developers", nargs=-1, required=True)
@click.option(
    "--limit",
    default=20,
    help="Maximum files to show per risk category",
)
@click.pass_context
def simulate_departure(ctx, developers, limit):
    """Simulate what happens when a developer leaves the team.

    Identifies files, symbols, and modules at risk of becoming orphaned
    or under-owned.  Accepts one or more developer names or email addresses
    (matched against git blame authors).

    \b
    Examples:
      roam simulate-departure "Alice Smith"
      roam simulate-departure alice@example.com
      roam simulate-departure alice bob
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    project_root = find_project_root()

    with open_db(readonly=True) as conn:
        result = _analyse_departure(conn, project_root, list(developers))

        if result is None:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "simulate-departure",
                            summary={
                                "verdict": "NO DATA -- no files in index",
                                "developer": ", ".join(developers),
                                "total_files_at_risk": 0,
                            },
                        )
                    )
                )
            else:
                click.echo("No files in index. Run 'roam index' first.")
            return

        if json_mode:
            _output_json(result, developers)
        else:
            _output_text(result, developers, limit)


def _output_json(result, developers):
    """Emit JSON envelope output."""
    def _file_entry(f):
        entry = {
            "path": f["path"],
            "ownership_pct": f["ownership_pct"],
            "top_pagerank": f["top_pagerank"],
        }
        if f.get("codeowners"):
            entry["codeowners"] = f["codeowners"]
        return entry

    click.echo(
        to_json(
            json_envelope(
                "simulate-departure",
                summary={
                    "verdict": result["verdict"],
                    "severity": result["severity"],
                    "developer": ", ".join(developers),
                    "total_files_at_risk": result["total_files_at_risk"],
                    "critical_count": len(result["critical_files"]),
                    "high_risk_count": len(result["high_risk_files"]),
                    "medium_risk_count": len(result["medium_risk_files"]),
                    "affected_modules": result["affected_clusters"],
                },
                developer=", ".join(developers),
                total_files_at_risk=result["total_files_at_risk"],
                critical_files=[
                    _file_entry(f) for f in result["critical_files"]
                ],
                high_risk_files=[
                    _file_entry(f) for f in result["high_risk_files"]
                ],
                medium_risk_files=[
                    _file_entry(f) for f in result["medium_risk_files"]
                ],
                key_symbols=[
                    {
                        "name": s["name"],
                        "kind": s["kind"],
                        "file": s["path"],
                        "line": s["line"],
                        "pagerank": s["pagerank"],
                    }
                    for s in result["key_symbols"]
                ],
                affected_modules={
                    "total": result["total_clusters"],
                    "affected": result["affected_clusters"],
                    "details": result["affected_cluster_list"],
                },
                recommendations=result["recommendations"],
            )
        )
    )


def _output_text(result, developers, limit):
    """Emit verdict-first text output."""
    dev_str = ", ".join(developers)
    click.echo(f"VERDICT: {result['verdict']}")
    click.echo()

    if result["critical_files"]:
        click.echo("CRITICAL (sole CODEOWNER + >50% blame):")
        for f in result["critical_files"][:limit]:
            pr_str = f"PageRank {f['top_pagerank']:.4f}" if f["top_pagerank"] else ""
            parts = [f"  {f['path']}  ({f['ownership_pct']}% ownership"]
            if pr_str:
                parts[0] += f", {pr_str}"
            parts[0] += ")"
            click.echo(parts[0])
        if len(result["critical_files"]) > limit:
            click.echo(f"  (+{len(result['critical_files']) - limit} more)")
        click.echo()

    if result["high_risk_files"]:
        click.echo("HIGH RISK (>50% blame):")
        for f in result["high_risk_files"][:limit]:
            click.echo(f"  {f['path']}  ({f['ownership_pct']}% ownership)")
        if len(result["high_risk_files"]) > limit:
            click.echo(f"  (+{len(result['high_risk_files']) - limit} more)")
        click.echo()

    if result["medium_risk_files"]:
        click.echo("MEDIUM RISK (>30% blame):")
        for f in result["medium_risk_files"][:limit]:
            click.echo(f"  {f['path']}  ({f['ownership_pct']}% ownership)")
        if len(result["medium_risk_files"]) > limit:
            click.echo(f"  (+{len(result['medium_risk_files']) - limit} more)")
        click.echo()

    if result["key_symbols"]:
        click.echo("KEY SYMBOLS AT RISK:")
        for s in result["key_symbols"][:15]:
            kind = abbrev_kind(s["kind"])
            loc = f"{s['path']}:{s['line']}" if s["line"] else s["path"]
            pr_str = f"PageRank {s['pagerank']:.4f}" if s["pagerank"] else ""
            parts = [f"  {kind} {s['name']} ({loc}"]
            if pr_str:
                parts[0] += f", {pr_str}"
            parts[0] += ")"
            click.echo(parts[0])
        if len(result["key_symbols"]) > 15:
            click.echo(f"  (+{len(result['key_symbols']) - 15} more)")
        click.echo()

    click.echo(
        f"AFFECTED MODULES: {result['affected_clusters']} of "
        f"{result['total_clusters']} clusters lose primary contributor"
    )

    if result["recommendations"]:
        click.echo("RECOMMENDATIONS:")
        for rec in result["recommendations"]:
            click.echo(f"  - {rec}")
