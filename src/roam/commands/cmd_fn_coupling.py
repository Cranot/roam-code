"""Show function-level temporal coupling: symbols that change together across files.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because fn-coupling outputs are invocation-scoped temporal
co-change rankings (per-symbol-pair lift / support / confidence scores
derived from git history) — not per-location code violations. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation
plan + W1224-audit memo.
"""

from __future__ import annotations

from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, open_db
from roam.output.formatter import json_envelope, loc, to_json

# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------


# Defaults tuned for the round-3 dogfood report which produced 2.26M pairs
# on the a Vue 3 + Laravel codebase project — every commit that touched two large Vue SFCs
# created `len(syms_i) * len(syms_j)` pairs, dominated by the long tail of
# auto-generated props/types. The new caps keep the signal intact while
# trimming the noise floor by ~3 orders of magnitude.
_DEFAULT_MAX_FILES_PER_COMMIT = 5  # was 30 — coordinated edits >5 files are usually merges/reformats
_DEFAULT_MAX_SYMBOLS_PER_FILE = 25  # PageRank-ranked top-N within a file


def _build_symbol_cochange(
    conn,
    *,
    exclude_tests: bool = True,
    max_files_per_commit: int = _DEFAULT_MAX_FILES_PER_COMMIT,
    max_symbols_per_file: int = _DEFAULT_MAX_SYMBOLS_PER_FILE,
    since_commit_id: int | None = None,
) -> tuple[dict, dict]:
    """Build a cross-file symbol co-change matrix from git history.

    Returns ``(pair_counts, suppressions)`` where ``suppressions`` records
    how many entries we filtered (so consumers can surface honest numbers
    via the ``suppressions`` envelope field).

    The algorithm caps symbols per file per commit by PageRank (which
    approximates "the actually-architectural symbols") rather than
    counting every prop/method ever defined in the file. This drops the
    dominant noise source — a single coordinated edit between two
    6000-line SFCs no longer produces ~14k spurious pairs.
    """
    suppressions = {
        "test_files": 0,
        "mega_commits": 0,
        "capped_symbols": 0,
        "since_filtered": 0,
    }

    # Step 1: gather commit -> list of changed file_ids, optionally filtered
    # to a recency window (round 4 / feature C).
    if since_commit_id is not None:
        rows = conn.execute(
            "SELECT commit_id, file_id FROM git_file_changes "
            "WHERE file_id IS NOT NULL AND commit_id >= ? ORDER BY commit_id",
            (since_commit_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT commit_id, file_id FROM git_file_changes WHERE file_id IS NOT NULL ORDER BY commit_id"
        ).fetchall()

    commit_files: dict[int, set[int]] = defaultdict(set)
    for r in rows:
        commit_files[r["commit_id"]].add(r["file_id"])

    # Step 2: pre-load symbol -> file mapping with PageRank for ranking,
    # plus the file path so we can drop tests up front.
    sym_rows = conn.execute(
        "SELECT s.id, s.file_id, COALESCE(gm.pagerank, 0) AS pr, f.path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id "
        "WHERE s.line_start IS NOT NULL"
    ).fetchall()

    file_to_syms: dict[int, list[tuple[int, float]]] = defaultdict(list)
    test_file_ids: set[int] = set()
    for s in sym_rows:
        if exclude_tests and is_test_file(s["path"]):
            test_file_ids.add(s["file_id"])
            continue
        file_to_syms[s["file_id"]].append((s["id"], s["pr"] or 0))

    if exclude_tests:
        suppressions["test_files"] = len(test_file_ids)

    # Step 3: rank symbols per file by PageRank descending and trim. This
    # gives every commit the "architecturally important" symbols only.
    file_top_syms: dict[int, list[int]] = {}
    for fid, syms in file_to_syms.items():
        if len(syms) > max_symbols_per_file:
            suppressions["capped_symbols"] += len(syms) - max_symbols_per_file
            ranked = sorted(syms, key=lambda x: -x[1])[:max_symbols_per_file]
            file_top_syms[fid] = [s[0] for s in ranked]
        else:
            file_top_syms[fid] = [s[0] for s in syms]

    # Step 4: for each commit, compute cross-file symbol pairs
    pair_count: dict[tuple[int, int], int] = defaultdict(int)

    for _cid, fids in commit_files.items():
        if len(fids) > max_files_per_commit:
            suppressions["mega_commits"] += 1
            continue

        per_file_syms: list[tuple[int, list[int]]] = []
        for fid in fids:
            syms = file_top_syms.get(fid)
            if syms:
                per_file_syms.append((fid, syms))

        n = len(per_file_syms)
        for i in range(n):
            _fid_i, syms_i = per_file_syms[i]
            for j in range(i + 1, n):
                _fid_j, syms_j = per_file_syms[j]
                for si in syms_i:
                    for sj in syms_j:
                        key = (min(si, sj), max(si, sj))
                        pair_count[key] += 1

    return pair_count, suppressions


def _get_direct_edge_set(conn):
    """Return a set of (sym_lo, sym_hi) for all direct edges."""
    rows = conn.execute("SELECT source_id, target_id FROM edges").fetchall()
    edge_set = set()
    for r in rows:
        lo = min(r["source_id"], r["target_id"])
        hi = max(r["source_id"], r["target_id"])
        edge_set.add((lo, hi))
    return edge_set


def _load_symbol_info(conn, sym_ids):
    """Load symbol metadata for a set of IDs.

    Returns dict[sym_id] -> {name, kind, file_path, line_start, qualified_name}
    """
    if not sym_ids:
        return {}

    rows = batched_in(
        conn,
        "SELECT s.id, s.name, s.kind, s.qualified_name, "
        "s.line_start, f.path AS file_path "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.id IN ({ph})",
        list(sym_ids),
    )

    info = {}
    for r in rows:
        info[r["id"]] = {
            "name": r["name"],
            "kind": r["kind"],
            "qualified_name": r["qualified_name"],
            "line_start": r["line_start"],
            "file_path": r["file_path"],
        }
    return info


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@roam_capability(
    name="fn-coupling",
    category="refactoring",
    summary="Show function-level temporal coupling (hidden dependencies)",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "refactor", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("fn-coupling")
@click.option("--min-count", default=3, type=int, show_default=True, help="Minimum co-change count to report")
@click.option("--limit", "-n", default=20, type=int, show_default=True, help="Maximum number of pairs to show")
@click.option(
    "--include-connected",
    is_flag=True,
    default=False,
    help="Also show pairs that have a direct edge",
)
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help=(
        "Include test files in the co-change matrix. Off by default — "
        "test fixtures co-change with src files by design; "
        "including them inflates pair counts by orders of magnitude."
    ),
)
@click.option(
    "--max-files-per-commit",
    type=int,
    default=_DEFAULT_MAX_FILES_PER_COMMIT,
    show_default=True,
    help=(
        "Skip commits that touch more than N files (treated as merges/reformats). "
        "Lower = fewer false-coupled pairs from mega commits."
    ),
)
@click.option(
    "--max-symbols-per-file",
    type=int,
    default=_DEFAULT_MAX_SYMBOLS_PER_FILE,
    show_default=True,
    help=(
        "Per commit, only the top-N PageRank symbols of each changed file "
        "contribute pairs. Caps the N×M explosion when two large SFCs co-change."
    ),
)
@click.option(
    "--since",
    "since_ref",
    default=None,
    help=(
        "Only consider commits since this ref (sha or tag). Round 4 / "
        "feature C: 'what new hidden coupling did the last 10 commits "
        "introduce?' is more actionable than the full-history default."
    ),
)
@click.pass_context
def fn_coupling(
    ctx,
    min_count,
    limit,
    include_connected,
    include_tests,
    max_files_per_commit,
    max_symbols_per_file,
    since_ref,
):
    """Show function-level temporal coupling (hidden dependencies).

    Finds pairs of symbols in different files that frequently change
    together in commits but have NO direct edge (import/call) between them.
    These represent hidden dependencies that should either be made explicit
    or decoupled.

    Unlike ``coupling`` (which detects file-level temporal coupling with
    statistical metrics like Lift and NPMI), this command drills down to
    individual functions and classes to pinpoint the exact symbols involved.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # Resolve --since into a commit_id boundary if provided. Anything
        # we can't resolve is silently ignored — co-change is descriptive,
        # not gating, so a bad ref shouldn't fail the command.
        since_commit_id: int | None = None
        if since_ref:
            row = conn.execute(
                "SELECT id FROM git_commits WHERE hash = ? OR hash LIKE ? ORDER BY id DESC LIMIT 1",
                (since_ref, f"{since_ref}%"),
            ).fetchone()
            if row:
                since_commit_id = int(row["id"])

        # Build the co-change matrix
        pair_counts, suppressions = _build_symbol_cochange(
            conn,
            exclude_tests=not include_tests,
            max_files_per_commit=max_files_per_commit,
            max_symbols_per_file=max_symbols_per_file,
            since_commit_id=since_commit_id,
        )

        if not pair_counts:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "fn-coupling",
                            summary={"pairs": 0, "error": "No git co-change data"},
                        )
                    )
                )
            else:
                click.echo("No git co-change data available. Run `roam index` on a git repository.")
            return

        # Filter by minimum count
        filtered = {k: v for k, v in pair_counts.items() if v >= min_count}

        if not filtered:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "fn-coupling",
                            summary={
                                "pairs": 0,
                                "note": f"No pairs with >= {min_count} co-changes",
                            },
                        )
                    )
                )
            else:
                click.echo(f"No symbol pairs with >= {min_count} co-changes across files. Try --min-count 2.")
            return

        # Get direct edges to separate hidden from connected
        edge_set = _get_direct_edge_set(conn)

        # Split into hidden vs connected
        hidden = []
        connected = []
        for (sa, sb), count in filtered.items():
            has_edge = (sa, sb) in edge_set
            entry = (sa, sb, count, has_edge)
            if has_edge:
                connected.append(entry)
            else:
                hidden.append(entry)

        # Sort both by count descending
        hidden.sort(key=lambda x: -x[2])
        connected.sort(key=lambda x: -x[2])

        # Build the results list
        if include_connected:
            results = hidden + connected
            results.sort(key=lambda x: -x[2])
        else:
            results = hidden

        results = results[:limit]

        if not results:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "fn-coupling",
                            summary={"pairs": 0, "hidden": 0, "connected": len(connected)},
                        )
                    )
                )
            else:
                click.echo(f"No hidden coupling found (all {len(connected)} co-changing pairs have direct edges).")
            return

        # Load symbol info for all referenced symbols
        all_ids = set()
        for sa, sb, _cnt, _edge in results:
            all_ids.add(sa)
            all_ids.add(sb)
        sym_info = _load_symbol_info(conn, all_ids)

        # Round 3 #1: collapse pairs that share both leaf names+kinds within
        # the same file pair. The graph indexer stores `id` properties at
        # multiple line offsets (AppItem.id, NavItem.id, ...) which gave the
        # same pair four duplicate rows. Aggregating by name+kind+file gives
        # one canonical row with a summed count and a list of contributing
        # symbol ids — agents see "themeStore <-> id" once, not four times.
        def _pair_key(sa, sb):
            ia = sym_info.get(sa, {})
            ib = sym_info.get(sb, {})
            return (
                (ia.get("name", ""), ia.get("kind", ""), ia.get("file_path", "")),
                (ib.get("name", ""), ib.get("kind", ""), ib.get("file_path", "")),
            )

        merged: dict[tuple, dict] = {}
        for sa, sb, count, has_edge in results:
            key = _pair_key(sa, sb)
            entry = merged.get(key)
            if entry is None:
                merged[key] = {
                    "sa": sa,
                    "sb": sb,
                    "count": count,
                    "has_edge": has_edge,
                    "duplicates": 1,
                }
            else:
                entry["count"] += count
                entry["duplicates"] += 1
                if has_edge:
                    entry["has_edge"] = True
        results = [(e["sa"], e["sb"], e["count"], e["has_edge"], e["duplicates"]) for e in merged.values()]
        results.sort(key=lambda x: -x[2])
        results = results[:limit]

        # --- Build verdict ---
        if results:
            sa0, sb0, cnt0, _e0, _dup0 = results[0]
            ia0 = sym_info.get(sa0, {})
            ib0 = sym_info.get(sb0, {})
            name_a0 = ia0.get("name", f"sym_{sa0}")
            name_b0 = ib0.get("name", f"sym_{sb0}")
            verdict = f"{len(hidden)} coupled function pairs, strongest: {name_a0}+{name_b0} ({cnt0} co-changes)"
        else:
            verdict = "no significant function coupling"

        # --- JSON output ---
        if json_mode:
            pairs = []
            for sa, sb, count, has_edge, duplicates in results:
                ia = sym_info.get(sa, {})
                ib = sym_info.get(sb, {})
                pairs.append(
                    {
                        "symbol_a": ia.get("qualified_name") or ia.get("name", f"sym_{sa}"),
                        "symbol_b": ib.get("qualified_name") or ib.get("name", f"sym_{sb}"),
                        "file_a": ia.get("file_path", ""),
                        "file_b": ib.get("file_path", ""),
                        "line_a": ia.get("line_start"),
                        "line_b": ib.get("line_start"),
                        "kind_a": ia.get("kind", ""),
                        "kind_b": ib.get("kind", ""),
                        "cochange_count": count,
                        "duplicates_collapsed": duplicates,
                        "has_direct_edge": has_edge,
                    }
                )

            hidden_count = sum(1 for p in pairs if not p["has_direct_edge"])
            click.echo(
                to_json(
                    json_envelope(
                        "fn-coupling",
                        summary={
                            "verdict": verdict,
                            "pairs": len(pairs),
                            "hidden": hidden_count,
                            "connected": len(pairs) - hidden_count,
                            "min_count": min_count,
                        },
                        pairs=pairs,
                        suppressions=suppressions,
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}\n")
        click.echo("Function-level temporal coupling (hidden dependencies):\n")

        for sa, sb, count, has_edge, _duplicates in results:
            ia = sym_info.get(sa, {})
            ib = sym_info.get(sb, {})
            name_a = ia.get("name", f"sym_{sa}")
            name_b = ib.get("name", f"sym_{sb}")
            edge_label = "" if has_edge else " (NO direct edge)"

            click.echo(f"  {name_a} <-> {name_b}    co-changed {count} times{edge_label}")

            loc_a = loc(ia.get("file_path", "?"), ia.get("line_start"))
            loc_b = loc(ib.get("file_path", "?"), ib.get("line_start"))
            click.echo(f"    {loc_a}    {loc_b}")
            click.echo()

        total_hidden = len(hidden)
        total_connected = len(connected)
        shown = len(results)
        click.echo(
            f"Showing {shown} pairs | {total_hidden} hidden, {total_connected} connected (min co-changes: {min_count})"
        )
        suppressed_parts = [f"{v} {k}" for k, v in suppressions.items() if v]
        if suppressed_parts:
            click.echo(f"Suppressed: {', '.join(suppressed_parts)} (use --include-tests / raise caps to inspect)")
