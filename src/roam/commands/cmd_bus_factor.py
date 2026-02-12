"""Detect knowledge loss risk per module (bus factor analysis)."""

import math
import time

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


def _contribution_entropy(author_shares):
    """Normalized Shannon entropy of contributions. 0.0=single author, 1.0=perfectly distributed."""
    shares = [s for s in author_shares if s > 0]
    if len(shares) <= 1:
        return 0.0
    entropy = -sum(p * math.log2(p) for p in shares)
    max_entropy = math.log2(len(shares))
    return entropy / max_entropy if max_entropy > 0 else 0.0


def _knowledge_risk_label(entropy: float) -> str:
    """Map entropy to a knowledge-risk label."""
    if entropy < 0.3:
        return "CRITICAL"
    if entropy < 0.5:
        return "HIGH"
    if entropy < 0.7:
        return "MEDIUM"
    return "LOW"


def _format_relative_time(epoch: int) -> str:
    """Format a unix timestamp as a human-readable relative time string."""
    if not epoch:
        return "unknown"
    now = int(time.time())
    diff = now - epoch
    if diff < 0:
        return "just now"
    days = diff // 86400
    if days < 1:
        return "today"
    if days == 1:
        return "1 day ago"
    if days < 30:
        return f"{days} days ago"
    months = days // 30
    if months == 1:
        return "1 month ago"
    if months < 12:
        return f"{months} months ago"
    years = days // 365
    if years == 1:
        return "1 year ago"
    return f"{years} years ago"


def _extract_directory(path: str) -> str:
    """Extract parent directory from a file path."""
    p = path.replace("\\", "/")
    last_slash = p.rfind("/")
    if last_slash >= 0:
        return p[:last_slash + 1]
    return "./"


def _compute_staleness_factor(last_active_epoch: int, stale_months: int) -> float:
    """Compute staleness factor: 1.0 if recent, scales up when stale.

    Returns a multiplier >= 1.0. Once the primary author's last commit
    exceeds *stale_months*, the factor grows linearly (capped at 3.0).
    """
    if not last_active_epoch:
        return 3.0
    now = int(time.time())
    months_ago = (now - last_active_epoch) / (30 * 86400)
    if months_ago <= stale_months:
        return 1.0
    # Linear ramp: 1.0 at threshold, 3.0 at 3x threshold
    extra = (months_ago - stale_months) / stale_months
    return min(1.0 + extra, 3.0)


def _risk_label(score: float) -> str:
    """Map a numeric risk score to a label."""
    if score >= 1.5:
        return "HIGH"
    if score >= 0.7:
        return "MEDIUM"
    return "LOW"


def _analyse_bus_factor(conn, stale_months: int):
    """Run the bus-factor analysis across all directories.

    Returns a list of dicts sorted by risk score descending.
    """
    # Fetch per-file author contribution data with timestamps
    rows = conn.execute("""
        SELECT gfc.file_id, f.path, gc.author,
               COUNT(DISTINCT gfc.commit_id) AS commits,
               SUM(gfc.lines_added + gfc.lines_removed) AS churn,
               MAX(gc.timestamp) AS last_active
        FROM git_file_changes gfc
        JOIN git_commits gc ON gfc.commit_id = gc.id
        JOIN files f ON gfc.file_id = f.id
        WHERE gfc.file_id IS NOT NULL
        GROUP BY gfc.file_id, gc.author
    """).fetchall()

    if not rows:
        return []

    # Aggregate by directory
    dir_data = {}  # dir -> { author -> {commits, churn, last_active} }
    for r in rows:
        d = _extract_directory(r["path"])
        if d not in dir_data:
            dir_data[d] = {}
        author = r["author"]
        if author not in dir_data[d]:
            dir_data[d][author] = {
                "commits": 0, "churn": 0, "last_active": 0,
            }
        entry = dir_data[d][author]
        entry["commits"] += r["commits"]
        entry["churn"] += r["churn"] or 0
        ts = r["last_active"] or 0
        if ts > entry["last_active"]:
            entry["last_active"] = ts

    results = []
    for directory, authors in dir_data.items():
        total_churn = sum(a["churn"] for a in authors.values())
        total_commits = sum(a["commits"] for a in authors.values())

        if total_commits == 0:
            continue

        # Sort authors by churn contribution descending
        sorted_authors = sorted(
            authors.items(), key=lambda x: x[1]["churn"], reverse=True,
        )

        # Bus factor: count of authors contributing >10% of changes
        bus_factor = 0
        for name, data in sorted_authors:
            share = data["churn"] / total_churn if total_churn else 0
            if share > 0.10:
                bus_factor += 1
        bus_factor = max(bus_factor, 1)

        # Primary author
        primary_name = sorted_authors[0][0]
        primary_data = sorted_authors[0][1]
        primary_share = (
            primary_data["churn"] / total_churn if total_churn else 0
        )

        # Knowledge concentration flag
        concentrated = primary_share > 0.70

        # Most recent activity across all authors in this directory
        dir_last_active = max(a["last_active"] for a in authors.values())

        # Primary author's last activity
        primary_last_active = primary_data["last_active"]

        # Staleness factor based on primary author
        staleness = _compute_staleness_factor(primary_last_active, stale_months)

        # Churn weight: log-ish scaling so high-churn dirs rank higher
        # Normalize: low churn (< 50) gets weight ~1, high churn scales up
        churn_weight = 1.0 + math.log1p(total_churn) / 10.0

        # Risk score = (1 / bus_factor) * churn_weight * staleness_factor
        risk_score = (1.0 / bus_factor) * churn_weight * staleness

        # Top authors summary (up to 5)
        top_authors = []
        for name, data in sorted_authors[:5]:
            share = data["churn"] / total_churn if total_churn else 0
            top_authors.append({
                "name": name,
                "commits": data["commits"],
                "churn": data["churn"],
                "share": round(share, 3),
                "share_pct": round(share * 100),
                "last_active": data["last_active"],
            })

        stale_primary = staleness > 1.0

        # Contribution entropy
        author_shares = [
            data["churn"] / total_churn if total_churn else 0
            for _name, data in sorted_authors
        ]
        entropy = round(_contribution_entropy(author_shares), 2)
        knowledge_risk = _knowledge_risk_label(entropy)

        results.append({
            "directory": directory,
            "bus_factor": bus_factor,
            "entropy": entropy,
            "knowledge_risk": knowledge_risk,
            "total_commits": total_commits,
            "total_churn": total_churn,
            "author_count": len(authors),
            "primary_author": primary_name,
            "primary_share": round(primary_share, 3),
            "primary_share_pct": round(primary_share * 100),
            "primary_last_active": primary_last_active,
            "concentrated": concentrated,
            "stale_primary": stale_primary,
            "staleness_factor": round(staleness, 2),
            "dir_last_active": dir_last_active,
            "risk_score": round(risk_score, 3),
            "risk": _risk_label(risk_score),
            "top_authors": top_authors,
        })

    # Sort by risk score descending (highest risk first)
    results.sort(key=lambda r: r["risk_score"], reverse=True)
    return results


def _query_brain_methods(conn):
    """Find disproportionately complex functions (cc>=25 and 50+ lines)."""
    rows = conn.execute("""
        SELECT s.name, s.kind, f.path, sm.cognitive_complexity,
               sm.line_count, sm.nesting_depth
        FROM symbol_metrics sm
        JOIN symbols s ON s.id = sm.symbol_id
        JOIN files f ON f.id = s.file_id
        WHERE sm.cognitive_complexity >= 25 AND sm.line_count >= 50
        ORDER BY sm.cognitive_complexity DESC
    """).fetchall()
    return [
        {
            "name": r["name"],
            "kind": r["kind"],
            "path": r["path"],
            "cognitive_complexity": r["cognitive_complexity"],
            "line_count": r["line_count"],
            "nesting_depth": r["nesting_depth"],
        }
        for r in rows
    ]


@click.command()
@click.option('--limit', default=20, help='Number of directories to show')
@click.option('--stale-months', default=6,
              help='Months of inactivity before flagging stale knowledge')
@click.option('--brain-methods', is_flag=True,
              help='Show disproportionately complex functions')
@click.pass_context
def bus_factor(ctx, limit, stale_months, brain_methods):
    """Detect knowledge loss risk per module (bus factor analysis)."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        results = _analyse_bus_factor(conn, stale_months)
        brain_list = _query_brain_methods(conn) if brain_methods else []

        if not results:
            if json_mode:
                envelope_kwargs = dict(
                    summary={"directory_count": 0, "high_risk": 0},
                    directories=[],
                )
                if brain_methods:
                    envelope_kwargs["brain_methods"] = brain_list
                click.echo(to_json(json_envelope("bus-factor", **envelope_kwargs)))
            else:
                click.echo("No git history data available. Run 'roam index' first.")
                if brain_methods and brain_list:
                    _print_brain_methods(brain_list)
            return

        limited = results[:limit]

        high_risk = sum(1 for r in results if r["risk"] == "HIGH")
        medium_risk = sum(1 for r in results if r["risk"] == "MEDIUM")
        concentrated_count = sum(1 for r in results if r["concentrated"])
        stale_count = sum(1 for r in results if r["stale_primary"])
        critical_entropy_count = sum(
            1 for r in results if r["knowledge_risk"] == "CRITICAL"
        )

        if json_mode:
            summary = {
                "directory_count": len(results),
                "high_risk": high_risk,
                "medium_risk": medium_risk,
                "concentrated": concentrated_count,
                "stale_primary": stale_count,
                "critical_entropy": critical_entropy_count,
            }
            if brain_methods:
                summary["brain_method_count"] = len(brain_list)

            envelope_kwargs = dict(
                summary=summary,
                stale_months=stale_months,
                directories=[
                    {
                        "directory": r["directory"],
                        "bus_factor": r["bus_factor"],
                        "entropy": r["entropy"],
                        "knowledge_risk": r["knowledge_risk"],
                        "risk": r["risk"],
                        "risk_score": r["risk_score"],
                        "total_commits": r["total_commits"],
                        "total_churn": r["total_churn"],
                        "author_count": r["author_count"],
                        "primary_author": r["primary_author"],
                        "primary_share": r["primary_share"],
                        "primary_last_active": r["primary_last_active"],
                        "concentrated": r["concentrated"],
                        "stale_primary": r["stale_primary"],
                        "staleness_factor": r["staleness_factor"],
                        "top_authors": r["top_authors"],
                    }
                    for r in limited
                ],
            )
            if brain_methods:
                envelope_kwargs["brain_methods"] = brain_list
            click.echo(to_json(json_envelope("bus-factor", **envelope_kwargs)))
            return

        # --- Text output ---
        click.echo("Knowledge risk by module:")
        click.echo(f"  ({len(results)} directories analysed, "
                   f"{high_risk} HIGH, {medium_risk} MEDIUM, "
                   f"{concentrated_count} concentrated, "
                   f"{stale_count} stale)")
        click.echo()

        for r in limited:
            # Build author share summary
            author_parts = []
            for a in r["top_authors"][:5]:
                author_parts.append(f"{a['name']}:{a['share_pct']}%")
            author_str = " ".join(author_parts)

            kr = r["knowledge_risk"]
            kr_pad = kr.ljust(8)
            click.echo(
                f"  {r['directory']:<40s} bus={r['bus_factor']}  "
                f"entropy={r['entropy']:.2f}  {kr_pad} {author_str}"
            )

            # Primary author line
            primary_time = _format_relative_time(r["primary_last_active"])
            primary_pct = r["primary_share_pct"]

            if r["concentrated"]:
                # Single-point-of-failure: emphasise primary author
                click.echo(
                    f"    Primary: {r['primary_author']} "
                    f"({primary_pct}% of {r['total_commits']} commits), "
                    f"last active: {primary_time}"
                )
                if r["stale_primary"]:
                    click.echo(f"    ** STALE: primary author inactive >{stale_months} months **")
            else:
                # Multiple contributors: show top authors
                top_parts = []
                for a in r["top_authors"][:3]:
                    top_parts.append(f"{a['name']} ({a['share_pct']}%)")
                dir_time = _format_relative_time(r["dir_last_active"])
                click.echo(
                    f"    Top: {', '.join(top_parts)}, "
                    f"last active: {dir_time}"
                )
                if r["stale_primary"]:
                    click.echo(f"    ** STALE: primary author inactive >{stale_months} months **")

            click.echo()

        if len(results) > limit:
            click.echo(f"  (+{len(results) - limit} more directories, "
                       f"use --limit to see more)")

        # --- Summary ---
        click.echo()
        click.echo(f"  Knowledge concentration: {critical_entropy_count} modules "
                   f"with critical entropy (<0.3)")
        if brain_methods:
            click.echo(f"  Brain methods: {len(brain_list)} functions "
                       f"with cc>=25 and 50+ lines")

        # --- Brain methods section ---
        if brain_methods and brain_list:
            _print_brain_methods(brain_list)


def _print_brain_methods(brain_list):
    """Print the brain methods section to text output."""
    click.echo()
    click.echo("Brain Methods (high complexity + large size):")
    for m in brain_list:
        click.echo(
            f"  {m['name']:<20s} cc={m['cognitive_complexity']:<4d} "
            f"lines={m['line_count']:<5d} {m['path']}"
        )
