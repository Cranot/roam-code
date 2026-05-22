"""Detect knowledge loss risk per module (bus factor analysis)."""

from __future__ import annotations

import json as _json
import math
import sqlite3
import subprocess
import time

import click

from roam.capability import roam_capability
from roam.commands.conventions_helper import (
    DEFAULT_EXCLUDE_PREFIXES,
    is_excluded_path,
)
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json
from roam.output.metric_definitions import COGNITIVE_COMPLEXITY_DEFINITION

# W115 — bus-factor is the fourth detector migrating onto the central
# findings registry (after clones W95, dead W99, complexity W102). The
# detector stays heuristic by nature — it counts unique authors and
# rolls up commits/churn per directory, then maps thresholds to
# CRITICAL/HIGH/MEDIUM/LOW. The registry confidence tier is therefore
# always ``heuristic`` regardless of sub-kind. Bump this when the
# concentration / staleness thresholds in ``_analyse_bus_factor`` change
# meaningfully so consumers can spot rows produced under an older
# classifier shape.
BUS_FACTOR_DETECTOR_VERSION: str = "1.0.0"


def _contribution_entropy(author_shares):
    """Normalized Shannon entropy of contributions. 0.0=single author, 1.0=perfectly distributed."""
    shares = [s for s in author_shares if s > 0]
    if len(shares) <= 1:
        return 0.0
    entropy = -sum(p * math.log2(p) for p in shares)
    max_entropy = math.log2(len(shares))
    return entropy / max_entropy if max_entropy > 0 else 0.0


def _knowledge_risk_label(entropy: float) -> str:
    """Map entropy to a knowledge-risk label.

    W761/W847: returns UPPER-case INTERNAL VOCABULARY for the
    ``knowledge_risk`` per-directory payload field. This is the
    knowledge-concentration risk tier (entropy-derived); distinct from
    the canonical W547 ``severity`` envelope slot. The bus-factor JSON
    envelope summary intentionally has no ``severity`` slot — its
    rollup is on ``directories_analyzed`` / ``high_risk`` / ``run_state``.
    """
    if entropy < 0.3:
        return "CRITICAL"  # W761/W847 retained UPPER-case for internal vocabulary
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
        return p[: last_slash + 1]
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
    """Map a numeric risk score to a label.

    W761/W847: returns UPPER-case INTERNAL VOCABULARY for the per-directory
    ``risk`` payload field (bus-factor + staleness composite). Distinct
    from the canonical W547 ``severity`` envelope slot.
    """
    if score >= 1.5:
        return "HIGH"  # W761/W847 retained UPPER-case for internal vocabulary
    if score >= 0.7:
        return "MEDIUM"
    return "LOW"


def _analyse_bus_factor(
    conn,
    stale_months: int,
    exclude_prefixes: tuple[str, ...] = DEFAULT_EXCLUDE_PREFIXES,
):
    """Run the bus-factor analysis across all directories.

    Args:
        conn: open SQLite connection.
        stale_months: months of inactivity before flagging a primary
            author as stale.
        exclude_prefixes: tuple of path prefixes to skip. Defaults to
            :data:`DEFAULT_EXCLUDE_PREFIXES` (``.github/``, ``.claude/``,
            ``docs/``, ``dist/``, ``node_modules/`` etc.) so non-source
            paths don't dominate the ranking. Pass ``()`` to disable.

    Returns:
        (results, excluded_files): ``results`` is a list of per-directory
        dicts sorted by risk score descending; ``excluded_files`` is the
        number of distinct file paths that were skipped by the filter.
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
        return [], 0

    # SYNTHESIS Rank 16: drop non-source paths (``.github/``, ``.claude/``,
    # ``docs/``, ``dist/``, ``node_modules/`` ...) before aggregating so
    # bus-factor doesn't surface CI workflows or vendored deps as a
    # knowledge risk. Honored via the global ``--include-excluded`` flag
    # in the click command below.
    excluded_paths: set[str] = set()
    filtered_rows = []
    for r in rows:
        path = r["path"] or ""
        if exclude_prefixes and is_excluded_path(path, exclude_prefixes):
            excluded_paths.add(path)
            continue
        filtered_rows.append(r)

    if not filtered_rows:
        return [], len(excluded_paths)

    # Aggregate by directory
    dir_data = {}  # dir -> { author -> {commits, churn, last_active} }
    for r in filtered_rows:
        d = _extract_directory(r["path"])
        if d not in dir_data:
            dir_data[d] = {}
        author = r["author"]
        if author not in dir_data[d]:
            dir_data[d][author] = {
                "commits": 0,
                "churn": 0,
                "last_active": 0,
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
            authors.items(),
            key=lambda x: x[1]["churn"],
            reverse=True,
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
        primary_share = primary_data["churn"] / total_churn if total_churn else 0

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
            top_authors.append(
                {
                    "name": name,
                    "commits": data["commits"],
                    "churn": data["churn"],
                    "share": round(share, 3),
                    "share_pct": round(share * 100),
                    "last_active": data["last_active"],
                }
            )

        stale_primary = staleness > 1.0

        # Contribution entropy
        author_shares = [data["churn"] / total_churn if total_churn else 0 for _name, data in sorted_authors]
        entropy = round(_contribution_entropy(author_shares), 2)
        knowledge_risk = _knowledge_risk_label(entropy)

        results.append(
            {
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
            }
        )

    # Sort by risk score descending (highest risk first)
    results.sort(key=lambda r: r["risk_score"], reverse=True)
    return results, len(excluded_paths)


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


def _repo_identifier(project_root) -> str:
    """Return a stable string identifying this repo.

    Prefers the ``origin`` remote URL (the canonical online identity)
    and falls back to the absolute project-root path when the repo has
    no remote configured. Either form is stable across runs so the
    summary finding's id can stay deterministic.
    """
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            url = (proc.stdout or "").strip()
            if url:
                return url
    except (OSError, subprocess.TimeoutExpired) as _exc:
        # A remote-URL probe failure falls through to the project-root
        # path fallback — surface lineage so a switched repo identifier
        # has a discoverable cause.
        from roam.observability import log_swallowed

        log_swallowed("cmd_bus_factor:repo_identifier", _exc)
    return str(project_root)


def _repo_summary_finding_id(repo_id: str) -> str:
    """Stable, deterministic id for the solo-author summary finding.

    One row per repo regardless of how many directories the detector
    surveyed — the W164 collapse rolls 65+ per-directory rows into a
    single repo-level summary. Thin wrapper around the W935 canonical
    ``make_finding_id`` so the sha1+truncate+prefix idiom stays in one
    place; preserved as a function (rather than inlined) so existing
    test imports keep working.
    """
    from roam.db.findings import make_finding_id

    return make_finding_id("bus-factor-summary", "solo-author", repo_id)


def _emit_solo_author_summary_finding(
    conn,
    results: list[dict],
    repo_id: str,
    source_version: str,
) -> None:
    """Emit ONE summary finding for a solo-author repo (W164).

    Instead of N per-directory ``author-concentration`` rows that all
    say "single author owns this directory" (unactionable on a
    solo-author repo and pollutes ``roam findings list``), we roll the
    signal up into a single repo-level row carrying the aggregate
    counts. The per-directory detail is preserved in the standard
    detector output — only the registry surface is collapsed.

    Wrapped at the call site in try/except so a pre-W89 DB silently
    no-ops.
    """
    from roam.db.findings import (
        CONFIDENCE_HEURISTIC,
        FindingRecord,
        emit_finding,
    )

    total_directories = len(results)
    # Aggregate the per-directory rollups back into a single
    # repo-wide author distribution. The detector already weights by
    # churn at the directory level, so summing churn per author across
    # directories gives the right global share.
    author_churn: dict[str, int] = {}
    author_commits: dict[str, int] = {}
    for r in results:
        for a in r.get("top_authors") or []:
            name = a.get("name") or ""
            if not name:
                continue
            author_churn[name] = author_churn.get(name, 0) + int(a.get("churn") or 0)
            author_commits[name] = author_commits.get(name, 0) + int(a.get("commits") or 0)

    unique_authors_count = len(author_churn)
    total_churn = sum(author_churn.values())
    dominant_author = ""
    dominant_share = 0.0
    if author_churn:
        dominant_author, dom_churn = max(author_churn.items(), key=lambda kv: kv[1])
        dominant_share = (dom_churn / total_churn) if total_churn else 0.0

    dominant_share_pct = round(dominant_share * 100)

    # W198 vocabulary drift fix: the solo-author summary keeps
    # ``dominant_author`` (git-blame term, back-compat) but adds a
    # ``dominant_actor`` parallel field so a ``ChangeEvidence`` collector
    # reading this evidence packet stays on the W182 ActorRef crosswalk
    # vocabulary without needing to know the git-blame name.
    evidence = {
        "repo": repo_id,
        "total_directories_analyzed": total_directories,
        "unique_authors_count": unique_authors_count,
        "dominant_author": dominant_author,
        "dominant_actor": dominant_author,
        "dominant_author_share": round(dominant_share, 3),
        "dominant_author_share_pct": dominant_share_pct,
        "summary_only": True,
        "collapsed_kind": "author-concentration",
    }
    claim = (
        f"Solo-author repo: {dominant_author or 'single author'} owns "
        f"{dominant_share_pct}% of churn across {total_directories} "
        f"directories ({unique_authors_count} unique author"
        f"{'s' if unique_authors_count != 1 else ''}). "
        f"Per-directory bus-factor rows collapsed into one summary "
        f"finding; re-run with --force-team-mode for the full ranking."
    )

    emit_finding(
        conn,
        FindingRecord(
            finding_id_str=_repo_summary_finding_id(repo_id),
            # NEW vocabulary in W164: first repo-level finding.
            # ``subject_kind`` is a free TEXT column on findings (no
            # CHECK constraint per W89), so this is additive.
            subject_kind="repo",
            subject_id=None,
            claim=claim,
            evidence_json=_json.dumps(evidence, sort_keys=True),
            confidence=CONFIDENCE_HEURISTIC,
            source_detector="bus-factor",
            source_version=source_version,
        ),
    )


def _bus_factor_finding_id(directory: str, kind: str) -> str:
    """Stable, deterministic finding id for one bus-factor risk.

    The (directory, kind) pair is enough to re-identify the same risk
    across runs — directories stay stable as long as the path layout is
    stable, and the kind disambiguates the same directory surfacing
    under both ``author-concentration`` and ``stale-ownership``. The
    sha1 prefix collapses long path strings to a fixed-width slug so
    the ``finding_id_str`` column stays bounded.
    """
    from roam.db.findings import make_finding_id

    return make_finding_id("bus-factor", kind, directory, kind)


def _emit_bus_factor_findings(
    conn,
    results: list[dict],
    source_version: str,
) -> None:
    """Mirror each bus-factor risk row into the findings registry.

    ``results`` is the directory ranking produced by ``_analyse_bus_factor``
    (same shape used by the JSON envelope). We emit one finding per
    surviving risk per directory; a directory that is both
    ``concentrated`` AND ``stale_primary`` produces two rows (one of
    each kind) so a consumer filtering by kind sees the right subset.

    Confidence tier is always ``heuristic`` — author-count rollups and
    inactivity proxies are fuzzy signals, even when the underlying git
    history is precise. Don't over-classify.

    Wrapped at the call site in try/except so a pre-W89 DB (no
    ``findings`` table) silently no-ops rather than crashing the
    standard read path.
    """
    from roam.db.findings import (
        CONFIDENCE_HEURISTIC,
        FindingRecord,
        emit_finding,
    )

    for r in results:
        directory = r.get("directory") or ""
        if not directory:
            continue

        # Two distinct sub-kinds — author-concentration is the pure
        # author-count heuristic; stale-ownership combines concentration
        # signal with the primary author's inactivity (recency proxy).
        # Both are emitted independently so a consumer can filter to
        # just the stale set when triaging.
        kinds: list[tuple[str, str]] = []
        if r.get("concentrated"):
            kinds.append(
                (
                    "author-concentration",
                    (
                        f"Bus-factor risk: {directory} is {r.get('primary_share_pct', 0)}%-"
                        f"owned by {r.get('primary_author', 'unknown')} "
                        f"({r.get('bus_factor', 1)} effective contributor"
                        f"{'s' if r.get('bus_factor', 1) != 1 else ''}, "
                        f"entropy {r.get('entropy', 0):.2f})"
                    ),
                )
            )
        if r.get("stale_primary"):
            kinds.append(
                (
                    "stale-ownership",
                    (
                        f"Stale ownership: {directory} primary author "
                        f"{r.get('primary_author', 'unknown')} "
                        f"({r.get('primary_share_pct', 0)}% share) inactive — "
                        f"staleness factor {r.get('staleness_factor', 1.0):.2f}"
                    ),
                )
            )

        if not kinds:
            continue

        # The evidence payload is shared across kinds — every risk row
        # carries the same underlying churn / author / staleness signals,
        # so consumers can rebuild the full picture without joining back
        # to the per-directory ranking. Top authors are capped at 5 by
        # the upstream aggregator already.
        # W198 vocabulary drift fix: every persisted finding mirrors the
        # JSON envelope — ``primary_author`` (git-blame, back-compat) is
        # paired with ``primary_actor`` (W182 ActorRef crosswalk). Same
        # value, two keys. ``top_authors`` rows additionally carry an
        # ``actor`` alias of ``name`` so a consumer reading
        # ``evidence_json`` doesn't need to know which surface produced
        # the field.
        top_authors_with_actor = [{**a, "actor": a.get("name")} for a in (r.get("top_authors") or [])]
        evidence = {
            "directory": directory,
            "bus_factor": r.get("bus_factor"),
            "entropy": r.get("entropy"),
            "knowledge_risk": r.get("knowledge_risk"),
            "risk": r.get("risk"),
            "risk_score": r.get("risk_score"),
            "total_commits": r.get("total_commits"),
            "total_churn": r.get("total_churn"),
            "author_count": r.get("author_count"),
            "primary_author": r.get("primary_author"),
            "primary_actor": r.get("primary_author"),
            "primary_share": r.get("primary_share"),
            "primary_share_pct": r.get("primary_share_pct"),
            "primary_last_active": r.get("primary_last_active"),
            "concentrated": bool(r.get("concentrated")),
            "stale_primary": bool(r.get("stale_primary")),
            "staleness_factor": r.get("staleness_factor"),
            "dir_last_active": r.get("dir_last_active"),
            "top_authors": top_authors_with_actor,
        }
        evidence_json = _json.dumps(evidence, sort_keys=True)

        for kind, claim in kinds:
            finding_id = _bus_factor_finding_id(directory, kind)
            emit_finding(
                conn,
                FindingRecord(
                    finding_id_str=finding_id,
                    # Directories aren't ``symbols.id`` rows, so we use
                    # a non-symbol subject_kind. Consumers querying by
                    # directory filter on ``subject_kind='directory'``
                    # and read the path out of ``evidence_json``.
                    subject_kind="directory",
                    subject_id=None,
                    claim=claim,
                    evidence_json=evidence_json,
                    # Bus-factor is fundamentally a heuristic detector —
                    # author counts and inactivity proxies are fuzzy
                    # signals. Both sub-kinds carry the same tier.
                    confidence=CONFIDENCE_HEURISTIC,
                    source_detector="bus-factor",
                    source_version=source_version,
                ),
            )


@roam_capability(
    name="bus-factor",
    category="reports",
    summary="Detect knowledge loss risk per module (bus factor analysis)",
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
@click.command()
@click.option("--limit", default=20, help="Number of directories to show")
@click.option("--stale-months", default=6, help="Months of inactivity before flagging stale knowledge")
@click.option("--brain-methods", is_flag=True, help="Show disproportionately complex functions")
@click.option(
    "--force-team-mode",
    "force_team_mode",
    is_flag=True,
    default=False,
    help=(
        "Override single-author auto-detection. Round 4 #13: when one author "
        "owns >80% of commits, the default switches to STALE-only output "
        "since 'bus factor 1' is the baseline, not a finding. Use this flag "
        "to opt back into the full distributed-team rubric."
    ),
)
@click.option(
    "--persist",
    "persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror concentrated / stale-ownership risks into the central "
        "findings registry — visible via "
        "``roam findings list --detector bus-factor``. The detector-specific "
        "output is unchanged; the registry rows are the denormalised "
        "cross-detector surface. Only directories flagged ``concentrated`` "
        "or ``stale_primary`` are persisted — the long tail of low-risk "
        "modules stays out of the registry."
    ),
)
@click.pass_context
def bus_factor(ctx, limit, stale_months, brain_methods, force_team_mode, persist):
    """Detect knowledge loss risk per module (bus factor analysis).

    Unlike ``simulate-departure`` (which models the impact of a specific developer
    leaving) and ``drift`` (which measures ownership divergence from CODEOWNERS),
    this command scans all directories for knowledge concentration using Shannon
    entropy and staleness factors.

    \b
    Examples:
      roam bus-factor
      roam bus-factor --limit 30
      roam bus-factor --stale-months 12
      roam bus-factor --brain-methods
      roam bus-factor --force-team-mode

    By default the scan skips identifiers under ``.github/``, ``.claude/``,
    ``docs/``, ``dist/``, ``build/``, ``node_modules/``, ``vendor/``, and
    ``__pycache__/`` so CI workflows and vendored deps don't dominate the
    ranking. The global ``--include-excluded`` flag restores legacy
    scan-everything behaviour.

    See also ``simulate-departure`` (impact of a specific developer
    leaving), ``drift`` (CODEOWNERS divergence), and ``owner``
    (per-symbol ownership lookup).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    # SYNTHESIS Rank 16: honor the global ``--include-excluded`` flag so
    # users can opt back into the legacy "scan everything" behaviour.
    # Default is to skip ``.github/``, ``.claude/``, ``docs/``, ``dist/``,
    # ``node_modules/``, etc. — see ``conventions_helper`` for the canon.
    include_excluded = ctx.obj.get("include_excluded", False) if ctx.obj else False
    exclude_prefixes: tuple[str, ...] = () if include_excluded else DEFAULT_EXCLUDE_PREFIXES
    ensure_index()

    # W607-CQ -- substrate-boundary plumbing for cmd_bus_factor.
    # ``_run_check_cq`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607cq_warnings_out`` rather than
    # crashing the bus-factor detector outright (W115 origin per CLAUDE.md
    # detector roster -- part of the original 16 findings-registry
    # substrate detectors). W164 (solo-author summary collapse) and
    # W811/W817 (empty-corpus Pattern-2 guard with explicit zero-count
    # verdict) preceded this wave; this wave layers substrate isolation
    # on top so a raise in ``_analyse_bus_factor`` (git-history ingest),
    # ``_emit_solo_author_summary_finding`` (W164 collapse), or the
    # downstream verdict-composer is disclosed rather than fatal.
    # Marker family ``bus_factor_<phase>_failed:<exc_class>:<detail>``.
    # Substrates wrapped:
    #
    #   * analyse_bus_factor         -- git-blame + co-change ingest
    #   * query_brain_methods        -- high-cc rollup (conditional)
    #   * detect_project_shape       -- single-author auto-detection
    #   * apply_solo_author_collapse -- STALE-only filter when solo-author
    #   * emit_solo_author_summary   -- W164 repo-level collapse finding
    #   * emit_bus_factor_findings   -- W115 registry mirror
    #                                   (sqlite3.OperationalError silent
    #                                   no-op preserved for pre-W89 DB)
    #   * aggregate_risk_counts      -- high/medium/concentrated/stale
    #                                   histogram
    #   * compose_verdict            -- LAW 6 single-line verdict string
    #                                   (min_bf, top_dir.directory accesses)
    #   * serialize_to_sarif         -- SARIF projection (CI integration)
    _w607cq_warnings_out: list[str] = []

    def _run_check_cq(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-CQ marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``bus_factor_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607cq_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cq_warnings_out.append(f"bus_factor_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-EH -- AGGREGATION-layer plumbing on top of W607-CQ substrate
    # layer. The two buckets are disjoint and merged at envelope-emit
    # time so consumers see the full degradation lineage. The phase names
    # (score_classify / compute_predicate / compute_verdict /
    # serialize_envelope) are disjoint from the W607-CQ substrate phases
    # above (analyse_bus_factor / query_brain_methods /
    # detect_project_shape / apply_solo_author_collapse /
    # emit_solo_author_summary / emit_bus_factor_findings /
    # aggregate_risk_counts / compose_verdict / serialize_to_sarif /
    # build_envelope_directories). Marker family
    # ``bus_factor_<phase>_failed:<exc_class>:<detail>`` is shared so
    # any consumer regex over the marker family catches both layers.
    #
    # W978 7-DISCIPLINE applies to every ``_run_check_eh(...)`` call:
    #   1. f-string verdict floor: NEVER re-interpolate the same values
    #      that tripped the closure inside the ``default=`` floor.
    #   2. kwarg-default eagerness: ``default=`` must be a literal
    #      constant, never a computed expression.
    #   3. json.dumps(default=str) sentinel: the serialize_envelope
    #      floor must be JSON-serializable with the standard encoder.
    #   4. phase-name collision: verified above against CQ's 10 phases.
    #   5. len() at kwarg-bind: move len() INSIDE the closure, never at
    #      the ``_run_check_eh(...)`` call site.
    #   6. unguarded len()/if on poisoned object: the floor MUST be a
    #      concrete dict/str/None, never a sentinel that may
    #      __len__-raise downstream.
    #   7. dict.get(key, expensive_default): use bare ``dict[key]`` when
    #      the floor guarantees the key.
    #
    # Aggregation phases wrapped (sibling pattern to cmd_auth_gaps's
    # W607-ED + cmd_missing_index's W607-DX):
    #
    #   * score_classify     -- buckets the run by min bus_factor into
    #                          HEALTHY (>=3) / WARN (2) / CRITICAL (1) /
    #                          DEGRADED
    #   * compute_predicate  -- rollup metrics dict (solo_authored_count,
    #                          low_contributor_count, hottest_files)
    #   * compute_verdict    -- single-line verdict string (LAW 6 floor:
    #                          "bus_factor completed")
    #   * serialize_envelope -- json_envelope("bus-factor", ...) projection
    _w607eh_warnings_out: list[str] = []

    def _run_check_eh(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-EH marker emission.

        Mirror of ``_run_check_cq`` shape (same
        ``bus_factor_<phase>_failed:`` marker family) but writes into
        ``_w607eh_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits. W607-DW finding pin: the
        return statement is verbatim ``return default`` (NOT
        ``return default if default is not None else {}``) so the floor
        is a literal pass-through.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607eh_warnings_out.append(f"bus_factor_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        # W607-CQ: ``analyse_bus_factor`` substrate -- the main detector
        # pass. A raise in the git-history aggregation degrades to
        # (results=[], excluded_files_count=0) so the empty-state path
        # composes cleanly.
        analyse_result = _run_check_cq(
            "analyse_bus_factor",
            _analyse_bus_factor,
            conn,
            stale_months,
            exclude_prefixes=exclude_prefixes,
            default=([], 0),
        )
        if analyse_result is None:
            analyse_result = ([], 0)
        results, excluded_files_count = analyse_result

        # W607-CQ: ``query_brain_methods`` substrate -- only invoked
        # when --brain-methods is set. Default [] keeps the envelope
        # well-formed if the SQL query trips.
        if brain_methods:
            brain_list = _run_check_cq(
                "query_brain_methods",
                _query_brain_methods,
                conn,
                default=[],
            )
            if brain_list is None:
                brain_list = []
        else:
            brain_list = []

        # Round 4 #13, Q: detect single-author projects so we don't flood
        # the output with "bus factor 1" warnings. Switch to a focused
        # mode that only surfaces STALE modules (the actually-actionable
        # signal on a solo project). W164 extends this: when persist is
        # set, collapse the per-directory findings into a single
        # repo-level summary row instead of N redundant "single author
        # owns this directory" rows.
        from roam.db.connection import find_project_root
        from roam.output.project_shape import detect_project_shape

        project_root = find_project_root()
        # W607-CQ: ``detect_project_shape`` substrate -- previously a
        # bare ``except Exception: shape = None`` swallow. Now disclosed
        # via the W607-CQ marker so observers know the auto-team-size
        # detection degraded (matters because ``single_author_mode``
        # downstream gates the W164 collapse path).
        shape = _run_check_cq(
            "detect_project_shape",
            detect_project_shape,
            conn,
            project_root,
            default=None,
        )
        single_author_mode = not force_team_mode and shape is not None and shape.team_size == "single-author"

        # W115 + W164 — mirror concentrated / stale-ownership rows into
        # the central findings registry. Independent of the --limit
        # display slice so re-running with a smaller --limit doesn't
        # truncate the registry. Wrapped in try/except so a pre-W89
        # schema (without the ``findings`` table) degrades cleanly.
        #
        # W164: on a solo-author repo (without --force-team-mode), the
        # per-directory rows all say the same thing ("single author
        # owns this directory") and pollute ``roam findings list`` with
        # dozens of unactionable rows. Collapse into ONE repo-level
        # summary finding instead. Stale-ownership rows still emit
        # per-directory since "this module is forgotten" stays
        # actionable even on a solo repo.
        # W607-CQ: ``emit_solo_author_summary`` and ``emit_bus_factor_findings``
        # substrate boundaries use DIRECT try/except (not _run_check_cq)
        # because the pre-W89 schema path (sqlite3.OperationalError on
        # missing ``findings`` table) is the EXPECTED degraded path --
        # the W115/W164 silent no-op contract for that case must NOT
        # produce a W607-CQ marker. Generic exceptions surface via
        # ``bus_factor_emit_solo_author_summary_failed:<exc>:<detail>``
        # and ``bus_factor_emit_bus_factor_findings_failed:<exc>:<detail>``
        # markers. Mirrors the cmd_auth_gaps W607-CM template:
        # OperationalError == silent no-op; generic Exception ==
        # W607-CQ marker.
        if persist and results:
            try:
                if single_author_mode:
                    # Collapse author-concentration rows into a single
                    # repo-level summary. Keep stale-ownership rows
                    # per-directory — those name actually-actionable
                    # forgotten modules regardless of team size.
                    repo_id = _repo_identifier(project_root)
                    try:
                        _emit_solo_author_summary_finding(conn, results, repo_id, BUS_FACTOR_DETECTOR_VERSION)
                    except sqlite3.OperationalError:
                        # findings table missing (pre-W89 schema) — silently no-op.
                        raise
                    except Exception as _summary_exc:  # noqa: BLE001 -- W607-CQ disclosure
                        _w607cq_warnings_out.append(
                            f"bus_factor_emit_solo_author_summary_failed:{type(_summary_exc).__name__}:{_summary_exc}"
                        )
                    stale_only = [r for r in results if r.get("stale_primary")]
                    if stale_only:
                        try:
                            _emit_bus_factor_findings(conn, stale_only, BUS_FACTOR_DETECTOR_VERSION)
                        except sqlite3.OperationalError:
                            raise
                        except Exception as _emit_exc:  # noqa: BLE001 -- W607-CQ disclosure
                            _w607cq_warnings_out.append(
                                f"bus_factor_emit_bus_factor_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}"
                            )
                    conn.commit()
                else:
                    persistable = [r for r in results if r.get("concentrated") or r.get("stale_primary")]
                    if persistable:
                        try:
                            _emit_bus_factor_findings(conn, persistable, BUS_FACTOR_DETECTOR_VERSION)
                            conn.commit()
                        except sqlite3.OperationalError:
                            raise
                        except Exception as _emit_exc:  # noqa: BLE001 -- W607-CQ disclosure
                            _w607cq_warnings_out.append(
                                f"bus_factor_emit_bus_factor_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}"
                            )
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — silently no-op.
                pass

        # W607-CQ: ``apply_solo_author_collapse`` substrate -- the
        # single-author-mode filter that demotes pure "bus factor 1"
        # rows out of the headline ranking. A raise here degrades to
        # the unfiltered ``results`` list so the standard ranked
        # envelope still composes.
        def _apply_solo_author_collapse():
            if single_author_mode and results:
                stale_only = [r for r in results if r.get("stale_primary")]
                if stale_only:
                    return stale_only
            return results

        collapsed = _run_check_cq(
            "apply_solo_author_collapse",
            _apply_solo_author_collapse,
            default=results,
        )
        if collapsed is not None:
            results = collapsed

        if not results:
            # SARIF path honors empty input — emit a valid SARIF doc
            # with zero results so a CI gate consumer sees the rules
            # catalogue even on a clean / no-history run. Mirrors the
            # cmd_over_fetch / cmd_auth_gaps empty-list contract.
            if sarif_mode:
                # W607-CQ: ``serialize_to_sarif`` substrate on the
                # no-data branch -- a raise in the SARIF writer used
                # to crash the bus-factor command on the CI integration
                # path; now degrades silently to None with a marker.
                def _emit_no_data_sarif():
                    from roam.output.sarif import bus_factor_to_sarif, write_sarif

                    click.echo(write_sarif(bus_factor_to_sarif([])))

                _run_check_cq("serialize_to_sarif", _emit_no_data_sarif, default=None)
                return
            no_data_verdict = "no git history data available"
            if json_mode:
                no_data_summary: dict = {
                    "verdict": no_data_verdict,
                    # W21.7 LAW 4 rename: ``directory_count: N`` rendered
                    # awkwardly as ``"directory count N"``. The new key
                    # humanizes to ``"N directories analyzed"`` — a clean
                    # concrete-noun anchor.
                    "directories_analyzed": 0,
                    "high_risk": 0,
                    "excluded_files_count": excluded_files_count,
                    "exclude_prefixes_active": list(exclude_prefixes),
                }
                if brain_methods:
                    # W1298 Pattern-3a: brain_methods rows carry raw
                    # cognitive_complexity from symbol_metrics.
                    no_data_summary["complexity_definition"] = COGNITIVE_COMPLEXITY_DEFINITION
                envelope_kwargs = dict(
                    summary=no_data_summary,
                    directories=[],
                )
                if brain_methods:
                    envelope_kwargs["brain_methods"] = brain_list
                # W607-CQ + W607-EH: mirror combined substrate-CALL +
                # aggregation-phase markers into BOTH the top-level
                # envelope ``warnings_out`` AND ``summary.warnings_out``
                # so MCP consumers see disclosure regardless of which
                # surface they read. On the no-data branch the markers
                # ride alongside the explicit zero-count verdict
                # (W811/W817 Pattern-2 guard).
                _no_data_combined = list(_w607cq_warnings_out) + list(_w607eh_warnings_out)
                if _no_data_combined:
                    no_data_summary["partial_success"] = True
                    no_data_summary["warnings_out"] = list(_no_data_combined)
                    envelope_kwargs["warnings_out"] = list(_no_data_combined)
                # W607-EH: serialize_envelope boundary on the no-data
                # path. A raise in ``json_envelope`` here used to crash
                # bus-factor on a clean empty-history repo; now degrades
                # to a parseable floor stub with the canonical command
                # name + the no-data verdict. Mirrors the ranked-branch
                # serialize_envelope wrap so the no-data path also
                # benefits from the W607-EH aggregation layer.
                _no_data_envelope_floor: dict = {
                    "command": "bus-factor",
                    "schema_version": "1.0.0",
                    "summary": {
                        "verdict": "bus_factor completed",
                        "partial_success": True,
                        "warnings_out": list(_no_data_combined),
                    },
                    "warnings_out": list(_no_data_combined),
                }
                _no_data_envelope = _run_check_eh(
                    "serialize_envelope",
                    json_envelope,
                    "bus-factor",
                    default=_no_data_envelope_floor,
                    **envelope_kwargs,
                )
                if _no_data_envelope is _no_data_envelope_floor and _w607eh_warnings_out:
                    _no_data_combined = list(_w607cq_warnings_out) + list(_w607eh_warnings_out)
                    _no_data_envelope_floor["summary"]["warnings_out"] = list(_no_data_combined)
                    _no_data_envelope_floor["warnings_out"] = list(_no_data_combined)
                    _no_data_envelope = _no_data_envelope_floor
                click.echo(to_json(_no_data_envelope))
            else:
                click.echo(f"VERDICT: {no_data_verdict}\n")
                click.echo("No git history data available. Run 'roam index' first.")
                if brain_methods and brain_list:
                    _print_brain_methods(brain_list)
            return

        limited = results[:limit]

        # W607-CQ: ``aggregate_risk_counts`` substrate -- the histogram
        # construction over the ranked rows. A raise (e.g. KeyError on a
        # malformed result dict missing ``risk`` / ``concentrated`` /
        # ``stale_primary`` / ``knowledge_risk``) degrades to the
        # all-zero tuple so the verdict composer still produces a
        # coherent string.
        def _aggregate_risk_counts():
            # W761/W847 retained UPPER-case for internal vocabulary —
            # ``risk`` / ``knowledge_risk`` are per-directory payload
            # fields (NOT envelope-slot severity); the comparison strings
            # mirror the values produced by ``_risk_label()`` and
            # ``_knowledge_risk_label()`` upstream.
            return (
                sum(1 for r in results if r["risk"] == "HIGH"),
                sum(1 for r in results if r["risk"] == "MEDIUM"),
                sum(1 for r in results if r["concentrated"]),
                sum(1 for r in results if r["stale_primary"]),
                sum(1 for r in results if r["knowledge_risk"] == "CRITICAL"),
            )

        agg = _run_check_cq(
            "aggregate_risk_counts",
            _aggregate_risk_counts,
            default=(0, 0, 0, 0, 0),
        )
        if agg is None:
            agg = (0, 0, 0, 0, 0)
        high_risk, medium_risk, concentrated_count, stale_count, critical_entropy_count = agg

        # W607-CQ: ``compose_verdict`` substrate -- LAW 6 single-line
        # verdict string. The ``min(r["bus_factor"] for r in results)``
        # call and the ``top_dir['directory']`` access are KeyError /
        # ValueError prone on malformed result rows; the wrap degrades
        # to the explicit "no data" floor so the envelope still emits
        # a non-empty verdict (W811/W817 Pattern-2 contract).
        def _compose_verdict():
            if results:
                top_dir = results[0]
                min_bf = min(r["bus_factor"] for r in results)
                return (
                    f"bus factor {min_bf} (min), {high_risk} high-risk, "
                    f"{concentrated_count} single-owner modules, top risk: {top_dir['directory']}"
                )
            return "no data"

        bus_verdict = _run_check_cq(
            "compose_verdict",
            _compose_verdict,
            default="no data",
        )
        if bus_verdict is None:
            bus_verdict = "no data"

        # ---------------------------------------------------------------
        # SARIF branch — emits BEFORE json/text so the pre-existing paths
        # stay byte-identical. Surfaces the same filtered set the user
        # sees (post single-author-mode collapse), so a CI gate sees the
        # same rows. On a solo-author repo without --force-team-mode the
        # set is already stale-only; we additionally prepend a synthetic
        # summary_only entry so the SARIF projection can emit the
        # repo-level solo-author summary row alongside the per-directory
        # stale-ownership rows.
        if sarif_mode:
            # W607-CQ: ``serialize_to_sarif`` substrate on the main
            # ranked branch -- a raise in the SARIF writer or in the
            # solo-author summary-entry composition (numeric
            # aggregation over ``top_authors`` rows) used to crash the
            # bus-factor command on the CI integration path; now
            # degrades silently to None with a marker, and the function
            # returns early (matches pre-W607-CQ semantics that SARIF
            # mode short-circuits).
            def _emit_main_sarif():
                from roam.output.sarif import bus_factor_to_sarif, write_sarif

                sarif_findings: list[dict] = list(results)
                if single_author_mode:
                    repo_id = _repo_identifier(project_root)
                    author_churn: dict[str, int] = {}
                    for r in results:
                        for a in r.get("top_authors") or []:
                            name = a.get("name") or ""
                            if not name:
                                continue
                            author_churn[name] = author_churn.get(name, 0) + int(a.get("churn") or 0)
                    unique_authors_count = len(author_churn)
                    total_churn = sum(author_churn.values())
                    dominant_author = ""
                    dominant_share = 0.0
                    if author_churn:
                        dominant_author, dom_churn = max(author_churn.items(), key=lambda kv: kv[1])
                        dominant_share = (dom_churn / total_churn) if total_churn else 0.0
                    summary_entry = {
                        "summary_only": True,
                        "repo": repo_id,
                        "total_directories_analyzed": len(results),
                        "unique_authors_count": unique_authors_count,
                        "dominant_author": dominant_author,
                        "dominant_actor": dominant_author,
                        "dominant_author_share_pct": round(dominant_share * 100),
                    }
                    sarif_findings = [summary_entry] + sarif_findings
                click.echo(write_sarif(bus_factor_to_sarif(sarif_findings)))

            _run_check_cq("serialize_to_sarif", _emit_main_sarif, default=None)
            return

        if json_mode:
            summary = {
                "verdict": bus_verdict,
                # W21.7 LAW 4 rename: ``directory_count`` → ``directories_analyzed``
                # so the auto-derived fact reads ``"N directories analyzed"``
                # instead of ``"directory count N"``.
                "directories_analyzed": len(results),
                "high_risk": high_risk,
                "medium_risk": medium_risk,
                "concentrated": concentrated_count,
                "stale_primary": stale_count,
                "critical_entropy": critical_entropy_count,
                "project_team_size": getattr(shape, "team_size", "unknown") if shape else "unknown",
                "single_author_mode": single_author_mode,
                "excluded_files_count": excluded_files_count,
                "exclude_prefixes_active": list(exclude_prefixes),
            }
            if brain_methods:
                summary["brain_method_count"] = len(brain_list)
                # W1298 Pattern-3a: brain_methods rows carry raw
                # cognitive_complexity from symbol_metrics — disclose the
                # scorer so consumers cannot confuse it with cyclomatic.
                summary["complexity_definition"] = COGNITIVE_COMPLEXITY_DEFINITION

            # W607-CQ: ``build_envelope_directories`` substrate -- the
            # per-directory row construction. A KeyError on a malformed
            # result row (missing ``directory`` / ``risk`` / etc.)
            # degrades to [] so the envelope still composes the verdict
            # and summary even when the upstream substrates raised AFTER
            # producing the empty floor (or before, leaving the malformed
            # rows in ``limited``).
            def _build_envelope_directories():
                return [
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
                        "primary_actor": r["primary_author"],
                        "primary_share": r["primary_share"],
                        "primary_last_active": r["primary_last_active"],
                        "concentrated": r["concentrated"],
                        "stale_primary": r["stale_primary"],
                        "staleness_factor": r["staleness_factor"],
                        "top_authors": [{**a, "actor": a.get("name")} for a in r["top_authors"]],
                    }
                    for r in limited
                ]

            envelope_directories = _run_check_cq(
                "build_envelope_directories",
                _build_envelope_directories,
                default=[],
            )
            if envelope_directories is None:
                envelope_directories = []

            # W607-EH -- score_classify boundary. Buckets the run by the
            # minimum bus_factor across analysed directories:
            #   * HEALTHY   -- min_bus_factor >= 3
            #   * WARN      -- min_bus_factor == 2
            #   * CRITICAL  -- min_bus_factor == 1
            #   * DEGRADED  -- floor on raise (or results empty)
            # W978 5th-discipline: ``results`` passed as a raw arg;
            # min()/iteration lives INSIDE the closure (no len() / no
            # min() at kwarg-bind).
            def _score_classify_run(_results):
                # W761/W847 retained UPPER-case for internal vocabulary —
                # ``state`` is the W607-EH score_classify rollup label
                # (HEALTHY / WARN / CRITICAL / DEGRADED), surfaced as the
                # ``run_state`` summary field. Distinct from the canonical
                # W547 ``severity`` envelope slot (bus-factor's summary
                # has no ``severity`` slot by design — rollup is on
                # ``run_state`` / ``directories_analyzed`` / ``high_risk``).
                if not _results:
                    return {"state": "DEGRADED", "scanned": 0}
                _min_bf = min(r["bus_factor"] for r in _results)
                if _min_bf >= 3:
                    _state_label = "HEALTHY"
                elif _min_bf == 2:
                    _state_label = "WARN"
                else:
                    _state_label = "CRITICAL"
                return {"state": _state_label, "scanned": len(_results)}

            _score_dict = _run_check_eh(
                "score_classify",
                _score_classify_run,
                results,
                default={"state": "DEGRADED", "scanned": 0},
            )

            # W607-EH -- compute_predicate boundary. Rollup metrics dict
            # surfacing aggregate dimensions (solo_authored_count,
            # low_contributor_count, hottest_files) so a downstream refactor
            # of the rollup logic surfaces a marker rather than crashing.
            # W978 5th-discipline: ``results`` passed as a raw arg;
            # counting / iteration lives INSIDE the closure.
            def _compute_predicate_fields(_results):
                _solo = 0
                _low = 0
                _hottest_files: list[dict] = []
                for _r in _results:
                    _bf = _r.get("bus_factor")
                    if _bf == 1:
                        _solo += 1
                    if isinstance(_bf, int) and _bf <= 2:
                        _low += 1
                # hottest_files: top 3 directories by risk_score desc
                _ranked = sorted(
                    _results,
                    key=lambda x: -(x.get("risk_score") or 0),
                )[:3]
                for _r in _ranked:
                    _hottest_files.append(
                        {
                            "directory": _r.get("directory"),
                            "bus_factor": _r.get("bus_factor"),
                            "risk": _r.get("risk"),
                        }
                    )
                return {
                    "solo_authored_count": _solo,
                    "low_contributor_count": _low,
                    "hottest_files": _hottest_files,
                }

            _pred_fields = _run_check_eh(
                "compute_predicate",
                _compute_predicate_fields,
                results,
                default={
                    "solo_authored_count": 0,
                    "low_contributor_count": 0,
                    "hottest_files": [],
                },
            )

            # W607-EH -- compute_verdict boundary. Wraps the verdict
            # string assembly so a downstream f-string refactor surfaces
            # a marker rather than crashing the envelope. Literal
            # "bus_factor completed" floor (LAW 6 still holds: the line
            # works standalone).
            #
            # W978 1st-discipline: the floor MUST NOT re-interpolate the
            # same values that tripped the closure. W978 2nd-discipline:
            # ``default=`` is a literal constant.
            def _build_verdict_str(_verdict_floor):
                return _verdict_floor

            verdict = _run_check_eh(
                "compute_verdict",
                _build_verdict_str,
                bus_verdict,
                default="bus_factor completed",
            )
            # Keep the original key in summary aligned with the wrapped
            # verdict so downstream consumers (and the auto-fact
            # humanizer) read the SAME string both layers produced.
            summary["verdict"] = verdict

            # W607-EH: surface score_classify + compute_predicate results
            # on the envelope so consumers can read run state + rollup
            # dimensions without re-deriving from the raw `directories`
            # list. W978 7th-discipline: bare ``_score_dict["state"]``
            # / ``_pred_fields["..."]`` lookups (floor dicts guarantee
            # the keys) -- NOT ``.get(..., expensive_default)``.
            summary["run_state"] = _score_dict["state"]
            summary["solo_authored_count"] = _pred_fields["solo_authored_count"]
            summary["low_contributor_count"] = _pred_fields["low_contributor_count"]
            summary["hottest_files"] = _pred_fields["hottest_files"]

            envelope_kwargs = dict(
                summary=summary,
                stale_months=stale_months,
                # W198 vocabulary drift fix: every per-directory row now
                # carries both ``primary_author`` (git-blame vocabulary,
                # kept for back-compat) and ``primary_actor`` (W182
                # ActorRef crosswalk vocabulary). Same value, two keys —
                # so a ``ChangeEvidence`` collector reading this envelope
                # picks one canonical name without losing the original.
                # ``top_authors`` entries get the same treatment: each
                # row carries ``name`` (existing) and ``actor`` (new) so
                # the crosswalk surface is consistent across the array.
                directories=envelope_directories,
            )
            if brain_methods:
                envelope_kwargs["brain_methods"] = brain_list
            # W607-CQ + W607-EH: mirror combined substrate-CALL +
            # aggregation-phase markers into BOTH the top-level envelope
            # ``warnings_out`` AND ``summary.warnings_out`` so MCP
            # consumers see disclosure regardless of which surface they
            # read. Flipping ``partial_success: True`` is the Pattern-2
            # silent-fallback guard -- a degraded substrate OR
            # aggregation path must NOT be mistaken for a clean ranked
            # verdict.
            _combined_warnings = list(_w607cq_warnings_out) + list(_w607eh_warnings_out)
            if _combined_warnings:
                summary["partial_success"] = True
                summary["warnings_out"] = list(_combined_warnings)
                envelope_kwargs["warnings_out"] = list(_combined_warnings)

            # W607-EH -- serialize_envelope boundary. Wraps the envelope
            # serialization itself. A downstream schema-shape refactor
            # that breaks ``json_envelope("bus-factor", ...)`` would
            # otherwise crash AFTER all substrate + aggregation signals
            # were already gathered. Floor to a minimal envelope stub so
            # consumers still receive a parseable JSON object with the
            # marker attached + the canonical command name. W978
            # 6th-discipline: floor is a concrete dict, not a sentinel
            # that may __len__-raise downstream.
            _envelope_floor: dict = {
                "command": "bus-factor",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": "bus_factor completed",
                    "partial_success": True,
                    "warnings_out": list(_combined_warnings),
                },
                "warnings_out": list(_combined_warnings),
            }
            envelope = _run_check_eh(
                "serialize_envelope",
                json_envelope,
                "bus-factor",
                default=_envelope_floor,
                **envelope_kwargs,
            )
            # W607-EH -- if ``serialize_envelope`` raised AFTER the
            # combined bucket was already snapshotted, the new
            # ``bus_factor_serialize_envelope_failed:`` marker was
            # appended to ``_w607eh_warnings_out`` and the floor stub
            # carries only the pre-raise combined list. Rebuild the
            # floor stub's warnings_out so the new marker reaches the
            # JSON output. Clean path -> envelope is the real
            # json_envelope return value, no rebuild.
            if envelope is _envelope_floor and _w607eh_warnings_out:
                _combined_warnings = list(_w607cq_warnings_out) + list(_w607eh_warnings_out)
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings)
                _envelope_floor["warnings_out"] = list(_combined_warnings)
                envelope = _envelope_floor
            click.echo(to_json(envelope))
            return

        # --- Text output ---
        click.echo(f"VERDICT: {bus_verdict}\n")
        click.echo("Knowledge risk by module:")
        click.echo(
            f"  ({len(results)} directories analysed, "
            f"{high_risk} HIGH, {medium_risk} MEDIUM, "
            f"{concentrated_count} concentrated, "
            f"{stale_count} stale)"
        )
        if excluded_files_count and not include_excluded:
            click.echo(
                f"  ({excluded_files_count} files excluded by default — use --include-excluded to scan everything)"
            )
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
                f"  {r['directory']:<40s} bus={r['bus_factor']}  entropy={r['entropy']:.2f}  {kr_pad} {author_str}"
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
                click.echo(f"    Top: {', '.join(top_parts)}, last active: {dir_time}")
                if r["stale_primary"]:
                    click.echo(f"    ** STALE: primary author inactive >{stale_months} months **")

            click.echo()

        if len(results) > limit:
            click.echo(f"  (+{len(results) - limit} more directories, use --limit to see more)")

        # --- Summary ---
        click.echo()
        click.echo(f"  Knowledge concentration: {critical_entropy_count} modules with critical entropy (<0.3)")
        if brain_methods:
            click.echo(f"  Brain methods: {len(brain_list)} functions with cc>=25 and 50+ lines")

        # --- Brain methods section ---
        if brain_methods and brain_list:
            _print_brain_methods(brain_list)


def _print_brain_methods(brain_list):
    """Print the brain methods section to text output."""
    click.echo()
    click.echo("Brain Methods (high complexity + large size):")
    for m in brain_list:
        click.echo(f"  {m['name']:<20s} cc={m['cognitive_complexity']:<4d} lines={m['line_count']:<5d} {m['path']}")
