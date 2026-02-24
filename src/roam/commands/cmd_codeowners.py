"""Analyze CODEOWNERS coverage, ownership distribution, and unowned files."""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# CODEOWNERS locations (checked in order)
# ---------------------------------------------------------------------------

_CODEOWNERS_LOCATIONS = [
    "CODEOWNERS",
    ".github/CODEOWNERS",
    "docs/CODEOWNERS",
    ".gitlab/CODEOWNERS",
]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def find_codeowners(project_root: Path) -> Path | None:
    """Find the CODEOWNERS file in standard locations.

    Returns the first matching path, or None if no file exists.
    """
    for loc in _CODEOWNERS_LOCATIONS:
        candidate = project_root / loc
        if candidate.is_file():
            return candidate
    return None


def parse_codeowners(codeowners_path: str | Path) -> list[tuple[str, list[str]]]:
    """Parse a CODEOWNERS file into (pattern, owners) tuples.

    Format:
    - Lines starting with # are comments
    - Empty lines are ignored
    - Pattern followed by one or more owners: ``*.py @backend-team @alice``
    - Later rules override earlier ones (last match wins)
    """
    path = Path(codeowners_path)
    if not path.is_file():
        return []

    rules: list[tuple[str, list[str]]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    for line in text.splitlines():
        line = line.strip()
        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue
        # Inline comments (# after whitespace)
        if " #" in line:
            line = line[: line.index(" #")].strip()
        parts = line.split()
        if len(parts) < 2:
            # Pattern with no owner = explicitly unowned (clears ownership)
            rules.append((parts[0], []))
            continue
        pattern = parts[0]
        owners = parts[1:]
        rules.append((pattern, owners))

    return rules


# ---------------------------------------------------------------------------
# Pattern matching (gitignore-style)
# ---------------------------------------------------------------------------


def _codeowners_match(pattern: str, filepath: str) -> bool:
    """Match a CODEOWNERS pattern against a file path.

    CODEOWNERS uses gitignore-style patterns:
    - ``*`` matches anything except ``/``
    - ``**`` matches anything including ``/``
    - Leading ``/`` means anchored to repo root
    - Trailing ``/`` means directory match (any file under that dir)
    - No leading ``/`` means match the basename or partial path
    """
    # Normalize path separators
    filepath = filepath.replace("\\", "/")

    # Directory pattern: trailing / matches everything under that dir
    if pattern.endswith("/"):
        dir_pattern = pattern.rstrip("/")
        # Anchored directory
        if dir_pattern.startswith("/"):
            dir_pattern = dir_pattern[1:]
            return filepath.startswith(dir_pattern + "/") or filepath == dir_pattern
        # Unanchored directory
        return (
            filepath.startswith(dir_pattern + "/")
            or ("/" + dir_pattern + "/") in ("/" + filepath)
        )

    # Anchored pattern (starts with /)
    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern[1:]

    # Handle ** patterns
    if "**" in pattern:
        return _match_doublestar(pattern, filepath, anchored)

    if anchored:
        # Match from root
        return fnmatch.fnmatch(filepath, pattern)

    # Unanchored: if pattern contains /, match against full path
    if "/" in pattern:
        return fnmatch.fnmatch(filepath, pattern)

    # No slash in pattern: match against the basename
    basename = PurePosixPath(filepath).name
    return fnmatch.fnmatch(basename, pattern)


def _match_doublestar(pattern: str, filepath: str, anchored: bool) -> bool:
    """Handle ** glob patterns.

    ``**`` matches zero or more path segments (directories).
    When ``**`` is surrounded by ``/`` (e.g. ``src/**/foo``), it can
    match zero segments (``src/foo``) or many (``src/a/b/foo``).
    """
    # Strategy: convert the CODEOWNERS pattern to a regex.
    # ** means "zero or more path segments" — we must absorb adjacent
    # slashes so that a/**/b matches both a/b and a/x/y/b.
    regex = ""
    i = 0
    plen = len(pattern)
    while i < plen:
        if pattern[i:i + 2] == "**":
            # Absorb trailing slash after **: a/**/ -> a/ or a/x/y/
            end = i + 2
            if end < plen and pattern[end] == "/":
                end += 1
            # ** with absorbed trailing / becomes (.*/)? — zero or more
            # path segments ending with /
            regex += "(.*/)?";
            i = end
        elif pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            regex += "[^/]"
            i += 1
        elif pattern[i] == ".":
            regex += r"\."
            i += 1
        else:
            regex += re.escape(pattern[i])
            i += 1

    if anchored:
        regex = "^" + regex + "$"
    else:
        regex = "(^|.*/)" + regex + "$"

    return bool(re.match(regex, filepath))


def resolve_owners(
    rules: list[tuple[str, list[str]]], filepath: str
) -> list[str]:
    """Determine the owner(s) of a file by applying CODEOWNERS rules.

    Last matching rule wins (standard CODEOWNERS semantics).
    Returns an empty list if no rule matches.
    """
    owners: list[str] = []
    for pattern, rule_owners in rules:
        if _codeowners_match(pattern, filepath):
            owners = rule_owners
    return owners


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------


def _key_areas(file_paths: list[str], max_areas: int = 3) -> list[str]:
    """Extract the most common directory prefixes from a list of file paths."""
    dir_counts: dict[str, int] = defaultdict(int)
    for fp in file_paths:
        fp = fp.replace("\\", "/")
        parts = fp.split("/")
        if len(parts) >= 2:
            # Use first two path components as the area
            area = "/".join(parts[:2]) + "/"
        elif len(parts) == 1:
            area = "./"
        else:
            area = "./"
        dir_counts[area] += 1

    sorted_areas = sorted(dir_counts.items(), key=lambda x: x[1], reverse=True)
    return [area for area, _count in sorted_areas[:max_areas]]


def _get_blame_top_contributor(conn, file_id: int) -> str | None:
    """Get the top contributor for a file from git data.

    Uses stored git_file_changes data (fast) rather than running blame.
    Returns the author name or None if no data.
    """
    row = conn.execute(
        """SELECT gc.author, SUM(gfc.lines_added + gfc.lines_removed) AS churn
           FROM git_file_changes gfc
           JOIN git_commits gc ON gfc.commit_id = gc.id
           WHERE gfc.file_id = ?
           GROUP BY gc.author
           ORDER BY churn DESC
           LIMIT 1""",
        (file_id,),
    ).fetchone()
    if row:
        return row["author"]
    return None


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command("codeowners")
@click.option("--unowned", is_flag=True, help="Show only unowned files")
@click.option("--owner", default="", help="Filter by specific owner")
@click.option("--limit", default=30, help="Max items to display")
@click.pass_context
def codeowners(ctx, unowned, owner, limit):
    """Analyze CODEOWNERS coverage and ownership distribution."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = find_project_root()
    co_path = find_codeowners(project_root)

    # --- No CODEOWNERS file ---
    if co_path is None:
        searched = ", ".join(_CODEOWNERS_LOCATIONS)
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "codeowners",
                        budget=budget,
                        summary={
                            "verdict": "No CODEOWNERS file found",
                            "codeowners_found": False,
                            "searched": _CODEOWNERS_LOCATIONS,
                        },
                    )
                )
            )
            return
        click.echo("VERDICT: No CODEOWNERS file found")
        click.echo()
        click.echo(f"  Searched: {searched}")
        click.echo()
        click.echo("  Consider creating a CODEOWNERS file to define code ownership.")
        click.echo(
            "  Run `roam codeowners --unowned` after creating it to find coverage gaps."
        )
        return

    rules = parse_codeowners(co_path)
    co_relpath = str(co_path.relative_to(project_root)).replace("\\", "/")

    with open_db(readonly=True) as conn:
        # Get all indexed files
        all_files = conn.execute(
            "SELECT id, path FROM files ORDER BY path"
        ).fetchall()

        if not all_files:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "codeowners",
                            budget=budget,
                            summary={
                                "verdict": "No files in index",
                                "total_files": 0,
                            },
                        )
                    )
                )
            else:
                click.echo("VERDICT: No files in index")
            return

        # Resolve ownership for each file
        file_ownership: list[dict] = []
        for f in all_files:
            fpath = f["path"].replace("\\", "/")
            owners = resolve_owners(rules, fpath)
            file_ownership.append(
                {
                    "file_id": f["id"],
                    "path": fpath,
                    "owners": owners,
                }
            )

        total_files = len(file_ownership)
        owned_files = [fo for fo in file_ownership if fo["owners"]]
        unowned_files = [fo for fo in file_ownership if not fo["owners"]]
        owned_count = len(owned_files)
        unowned_count = len(unowned_files)
        coverage_pct = round(owned_count * 100 / total_files, 1) if total_files else 0

        # Build owner -> files mapping
        owner_files: dict[str, list[str]] = defaultdict(list)
        for fo in owned_files:
            for o in fo["owners"]:
                owner_files[o].append(fo["path"])

        # PageRank for unowned files (for importance ranking)
        unowned_ids = [fo["file_id"] for fo in unowned_files]
        pagerank_map: dict[int, float] = {}
        dependents_map: dict[int, int] = {}
        if unowned_ids:
            # Get PageRank from graph_metrics via symbols
            for fo in unowned_files:
                fid = fo["file_id"]
                pr_row = conn.execute(
                    """SELECT MAX(gm.pagerank) AS pr
                       FROM graph_metrics gm
                       JOIN symbols s ON s.id = gm.symbol_id
                       WHERE s.file_id = ?""",
                    (fid,),
                ).fetchone()
                pagerank_map[fid] = (pr_row["pr"] or 0.0) if pr_row else 0.0

                dep_row = conn.execute(
                    """SELECT COUNT(DISTINCT e.source_id) AS cnt
                       FROM edges e
                       JOIN symbols s ON s.id = e.target_id
                       WHERE s.file_id = ?""",
                    (fid,),
                ).fetchone()
                dependents_map[fid] = dep_row["cnt"] if dep_row else 0

        # Sort unowned by PageRank descending
        unowned_ranked = sorted(
            unowned_files,
            key=lambda fo: pagerank_map.get(fo["file_id"], 0),
            reverse=True,
        )

        # Build owner distribution table
        owner_dist = []
        for oname in sorted(owner_files.keys(), key=lambda o: len(owner_files[o]), reverse=True):
            files = owner_files[oname]
            areas = _key_areas(files)
            owner_dist.append(
                {
                    "name": oname,
                    "files": len(files),
                    "pct": round(len(files) * 100 / total_files, 1),
                    "key_areas": areas,
                }
            )

        # Ownership drift: compare declared owner vs top git contributor
        drift_files: list[dict] = []
        for fo in owned_files:
            top_contributor = _get_blame_top_contributor(conn, fo["file_id"])
            if top_contributor and fo["owners"]:
                # Check if top contributor is NOT one of the declared owners
                # Normalize: owners might have @ prefix, contributors might not
                declared_names = {
                    o.lstrip("@").lower() for o in fo["owners"]
                }
                contributor_name = top_contributor.lower()
                if contributor_name not in declared_names:
                    drift_files.append(
                        {
                            "path": fo["path"],
                            "declared_owners": fo["owners"],
                            "top_contributor": top_contributor,
                        }
                    )

        # Build verdict
        verdict = f"{coverage_pct}% CODEOWNERS coverage ({owned_count}/{total_files} files owned)"

        # --- Filter modes ---

        if owner:
            # Filter by specific owner
            owner_matched = owner_files.get(owner, [])
            if not owner_matched:
                # Try case-insensitive and with/without @ prefix
                for oname, ofiles in owner_files.items():
                    if oname.lower() == owner.lower() or oname.lstrip("@").lower() == owner.lstrip("@").lower():
                        owner_matched = ofiles
                        owner = oname
                        break

            if json_mode:
                owner_file_details = []
                for fp in owner_matched[:limit]:
                    frow = conn.execute(
                        "SELECT id FROM files WHERE path = ?", (fp,)
                    ).fetchone()
                    pr = 0.0
                    deps = 0
                    if frow:
                        pr_row = conn.execute(
                            """SELECT MAX(gm.pagerank) AS pr
                               FROM graph_metrics gm
                               JOIN symbols s ON s.id = gm.symbol_id
                               WHERE s.file_id = ?""",
                            (frow["id"],),
                        ).fetchone()
                        pr = (pr_row["pr"] or 0.0) if pr_row else 0.0
                        dep_row = conn.execute(
                            """SELECT COUNT(DISTINCT e.source_id) AS cnt
                               FROM edges e
                               JOIN symbols s ON s.id = e.target_id
                               WHERE s.file_id = ?""",
                            (frow["id"],),
                        ).fetchone()
                        deps = dep_row["cnt"] if dep_row else 0
                    owner_file_details.append(
                        {"path": fp, "pagerank": round(pr, 4), "dependents": deps}
                    )

                click.echo(
                    to_json(
                        json_envelope(
                            "codeowners",
                            budget=budget,
                            summary={
                                "verdict": f"{owner}: {len(owner_matched)} files",
                                "owner": owner,
                                "file_count": len(owner_matched),
                            },
                            files=owner_file_details,
                        )
                    )
                )
                return

            click.echo(f"VERDICT: {owner}: {len(owner_matched)} files")
            click.echo()
            if not owner_matched:
                click.echo(f"  No files found for owner: {owner}")
                return
            rows = []
            for fp in owner_matched[:limit]:
                rows.append([fp])
            click.echo(format_table(["File"], rows))
            if len(owner_matched) > limit:
                click.echo(f"  (+{len(owner_matched) - limit} more)")
            return

        if unowned:
            # Show only unowned files
            if json_mode:
                items = [
                    {
                        "path": fo["path"],
                        "pagerank": round(pagerank_map.get(fo["file_id"], 0), 4),
                        "dependents": dependents_map.get(fo["file_id"], 0),
                    }
                    for fo in unowned_ranked[:limit]
                ]
                click.echo(
                    to_json(
                        json_envelope(
                            "codeowners",
                            budget=budget,
                            summary={
                                "verdict": f"{unowned_count} unowned files ({100 - coverage_pct}% of codebase)",
                                "unowned_count": unowned_count,
                                "total_files": total_files,
                            },
                            unowned=items,
                        )
                    )
                )
                return

            click.echo(
                f"VERDICT: {unowned_count} unowned files ({100 - coverage_pct}% of codebase)"
            )
            click.echo()
            if not unowned_ranked:
                click.echo("  All files have declared owners.")
                return
            click.echo("  Unowned Files (by PageRank):")
            rows = []
            for fo in unowned_ranked[:limit]:
                pr = pagerank_map.get(fo["file_id"], 0)
                deps = dependents_map.get(fo["file_id"], 0)
                rows.append(
                    [fo["path"], f"{pr:.4f}", str(deps)]
                )
            click.echo(format_table(["File", "PageRank", "Dependents"], rows))
            if len(unowned_ranked) > limit:
                click.echo(f"  (+{len(unowned_ranked) - limit} more)")
            return

        # --- Full report ---
        if json_mode:
            unowned_items = [
                {
                    "path": fo["path"],
                    "pagerank": round(pagerank_map.get(fo["file_id"], 0), 4),
                    "dependents": dependents_map.get(fo["file_id"], 0),
                }
                for fo in unowned_ranked[:limit]
            ]
            click.echo(
                to_json(
                    json_envelope(
                        "codeowners",
                        budget=budget,
                        summary={
                            "verdict": verdict,
                            "total_files": total_files,
                            "owned_files": owned_count,
                            "coverage_pct": coverage_pct,
                            "total_owners": len(owner_files),
                            "codeowners_path": co_relpath,
                        },
                        owners=owner_dist,
                        unowned=unowned_items,
                        drift=drift_files[:limit],
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        # Ownership distribution
        if owner_dist:
            click.echo("  Ownership Distribution:")
            tbl_rows = []
            for od in owner_dist:
                areas_str = ", ".join(od["key_areas"])
                tbl_rows.append(
                    [od["name"], str(od["files"]), f"{od['pct']}%", areas_str]
                )
            click.echo(
                format_table(["Owner", "Files", "%", "Key Areas"], tbl_rows, budget=limit)
            )
            click.echo()

        # Coverage summary
        click.echo("  Coverage Summary:")
        click.echo(f"    Total files:   {total_files}")
        click.echo(f"    Owned files:   {owned_count} ({coverage_pct}%)")
        click.echo(f"    Unowned files: {unowned_count} ({round(100 - coverage_pct, 1)}%)")
        click.echo()

        # Top unowned files
        if unowned_ranked:
            click.echo("  Top Unowned Files (by PageRank):")
            tbl_rows = []
            for fo in unowned_ranked[:10]:
                pr = pagerank_map.get(fo["file_id"], 0)
                deps = dependents_map.get(fo["file_id"], 0)
                tbl_rows.append(
                    [fo["path"], f"{pr:.4f}", str(deps)]
                )
            click.echo(format_table(["File", "PageRank", "Dependents"], tbl_rows))
            if len(unowned_ranked) > 10:
                click.echo(f"    (+{len(unowned_ranked) - 10} more unowned)")
            click.echo()

        # Ownership concentration
        if owner_dist:
            top_owner_pct = owner_dist[0]["pct"]
            # How many owners to cover owned_count?
            cumulative = 0
            bus_factor_owners = 0
            for od in owner_dist:
                cumulative += od["files"]
                bus_factor_owners += 1
                if cumulative >= owned_count * 0.8:
                    break
            click.echo("  Ownership Concentration:")
            click.echo(f"    Top owner covers: {top_owner_pct}% of files")
            click.echo(
                f"    Bus factor risk: {bus_factor_owners} owners cover 80% of owned files"
            )
            click.echo()

        # Ownership drift
        if drift_files:
            click.echo(f"  Ownership Drift ({len(drift_files)} files):")
            tbl_rows = []
            for df in drift_files[:10]:
                declared = ", ".join(df["declared_owners"])
                tbl_rows.append(
                    [df["path"], declared, df["top_contributor"]]
                )
            click.echo(
                format_table(
                    ["File", "Declared Owner", "Top Contributor"], tbl_rows
                )
            )
            if len(drift_files) > 10:
                click.echo(f"    (+{len(drift_files) - 10} more drift)")
