"""Detect near-duplicate code via AST structural hashing.

Unlike ``duplicates`` (which uses metric-based similarity from the DB),
this command re-parses source files and compares actual AST subtree
structures.  Detects Type-2 clones: identical control flow with different
identifiers or literals.

Related commands: ``duplicates`` (metric-based), ``suggest-refactoring``,
``split`` (extract responsibilities).
"""

from __future__ import annotations

import json as _json
import re as _re
import sqlite3 as _sqlite3

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.index.file_roles import is_test as _is_test
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import (
    json_envelope,
    loc,
    to_json,
)

# W165 — Test / fixture / production bucketing for clones findings.
#
# The 212-eval dogfood corpus surfaced that ~250+ of the 584 clone findings
# on roam-code's own repo were in ``src/roam/languages/*_lang.py`` (parallel
# language extractors that *should* mirror each other) and ~130 more were
# in test fixtures (intentional pytest parametrize patterns). Real refactor
# candidates drown in noise.
#
# Fix: classify each clone pair into one of three buckets
#   - "production":         both sides in src/ (genuine refactor candidate)
#   - "test_intentional":   both sides in tests/ (likely pytest parametrize)
#   - "mixed":              one side src/, one side tests/ (interesting —
#                           could be test leakage or test importing src as
#                           fixture; surfaced so agents can audit)
# Then let agents filter via ``--exclude-tests`` / ``--exclude-fixtures``
# AND via the persisted ``role_bucket`` evidence field on each finding row
# (``roam findings list --detector clones`` consumers can post-filter).
#
# Mixed bucket is deliberately preserved through ``--exclude-tests`` — the
# warning value (production-test entanglement) outweighs the noise cost. The
# user-asks-X / engine-does-X mapping is documented in the W165 task notes
# ("opinionated decision: keep mixed with warning surfaced via bucket label").

_FIXTURE_PATTERN = _re.compile(r"(^|/)(fixtures?|test_fixtures|testdata|test_data)(/|$)")


def _normalise_for_role(path: str) -> str:
    """Lower-cost path normalisation just for bucket classification."""
    return path.replace("\\", "/") if path else ""


def _is_fixture_path(path: str) -> bool:
    """True if the path looks like a test fixture / sample-data directory.

    Recognises ``fixtures/``, ``test_fixtures/``, ``testdata/``,
    ``test_data/`` anywhere in the path. Fixture detection is path-only
    (no I/O); the bigger noise source on roam-code is ``tests/fixtures/``
    which is also caught by ``_is_test`` — but downstream consumers may
    want to filter fixtures distinctly from real tests, so we expose both
    flags.
    """
    if not path:
        return False
    return _FIXTURE_PATTERN.search(_normalise_for_role(path)) is not None


def _role_bucket_for_pair(file_a: str, file_b: str) -> str:
    """Classify a two-sided finding (clone pair) into a role bucket.

    Returns one of "production", "test_intentional", or "mixed". A side
    counts as "test" if either ``is_test`` or ``_is_fixture_path``
    matches — the union surfaces the broadest noise class. Both sides
    test ⇒ test_intentional; one side test ⇒ mixed (potential leakage);
    neither ⇒ production.
    """
    a_test = _is_test(file_a) or _is_fixture_path(file_a)
    b_test = _is_test(file_b) or _is_fixture_path(file_b)
    if a_test and b_test:
        return "test_intentional"
    if a_test or b_test:
        return "mixed"
    return "production"


def _role_bucket_for_cluster(files: list[str]) -> str:
    """Classify a multi-member cluster by the role mix of its files.

    All-test (union of is_test + fixture) ⇒ test_intentional; all-source
    ⇒ production; mixed ⇒ mixed. Empty input is treated as production
    (caller already filtered empty clusters out).
    """
    if not files:
        return "production"
    test_sides = [bool(f and (_is_test(f) or _is_fixture_path(f))) for f in files]
    if all(test_sides):
        return "test_intentional"
    if any(test_sides):
        return "mixed"
    return "production"


def _enrich_clones_findings_with_role_bucket(conn) -> int:
    """Inject ``role_bucket`` into the evidence_json of every clones row.

    The clones detector emits its registry rows from
    ``roam.graph.clone_detect._emit_clone_findings`` (a module the W165
    fix is forbidden from touching). To still surface the bucket on
    every persisted finding, we post-process the registry rows here,
    parsing evidence_json, computing the bucket from the (file_a,
    file_b) pair the detector already stored, and writing the row back
    with the new field. Idempotent — re-running upserts the same value.

    Returns the count of finding rows updated. Defensive against
    pre-W89 schemas (no ``findings`` table) — silently returns 0.
    """
    try:
        rows = conn.execute("SELECT id, evidence_json FROM findings WHERE source_detector = 'clones'").fetchall()
    except _sqlite3.OperationalError:
        # No findings table — caller still wants the clone_pairs path to
        # work, which it does. Nothing to enrich.
        return 0

    updated = 0
    for r in rows:
        try:
            evidence = _json.loads(r["evidence_json"] or "{}")
        except (TypeError, ValueError):
            # Corrupt evidence_json — skip rather than mask the underlying
            # data quality issue.
            continue
        file_a = evidence.get("file_a") or ""
        file_b = evidence.get("file_b") or ""
        bucket = _role_bucket_for_pair(file_a, file_b)
        if evidence.get("role_bucket") == bucket:
            # Already enriched (re-run upsert) — skip the write.
            continue
        evidence["role_bucket"] = bucket
        conn.execute(
            "UPDATE findings SET evidence_json = ? WHERE id = ?",
            (_json.dumps(evidence, sort_keys=True), r["id"]),
        )
        updated += 1
    return updated


# R22 — confidence-derivation rule for clone clusters and pairs:
#   similarity >= 0.90 → "high"  (near-identical, almost certainly a clone)
#   similarity in [0.70, 0.90) → "medium"
#   similarity < 0.70 → "low"  (structural skeleton match only; high FP)
def _classify_similarity(sim: float) -> tuple[str, str]:
    """Map a similarity score to a (confidence, reason) tuple."""
    if sim >= 0.90:
        return "high", f"similarity {sim:.2f} ≥ 0.90 — near-identical clone"
    if sim >= 0.70:
        return "medium", f"similarity {sim:.2f} in [0.70, 0.90) — likely clone"
    return "low", f"similarity {sim:.2f} < 0.70 — structural skeleton only"


def _cluster_classify(cluster: dict) -> tuple[str, str]:
    sim = float(cluster.get("avg_similarity", 0.0) or 0.0)
    return _classify_similarity(sim)


def _pair_classify(pair: dict) -> tuple[str, str]:
    sim = float(pair.get("similarity", 0.0) or 0.0)
    return _classify_similarity(sim)


@roam_capability(
    name="clones",
    category="health",
    summary="Detect near-duplicate code via AST structural hashing (Type-2 clones).",
    inputs=["repo_path"],
    outputs=["clusters", "verdict"],
    examples=[
        "roam clones",
        "roam clones --threshold 0.85 --min-lines 8",
        "roam clones --persist",
    ],
    tags=["health", "duplication"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command()
@click.option(
    "--threshold",
    default=0.70,
    show_default=True,
    type=click.FloatRange(0.0, 1.0),
    help="Minimum Jaccard similarity (0.0-1.0)",
)
@click.option(
    "--min-lines",
    default=5,
    show_default=True,
    type=int,
    help="Skip functions shorter than N lines",
)
@click.option("--scope", default=None, type=str, help="Limit to files under this path prefix")
@click.option("--top", default=0, type=int, help="Show only top N clusters (0=all)")
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help="Write results to clone_pairs and clone_clusters tables for downstream consumers (roam critique, roam retrieve).",
)
@click.option(
    "--by-file",
    "by_file",
    is_flag=True,
    default=False,
    help="aggregate clone pairs into file-pair coupling, surface the top-coupled file pairs.",
)
@click.option(
    "--exclude-tests",
    is_flag=True,
    default=False,
    help=(
        "Drop clone pairs/clusters where ALL participating files are tests "
        "(role_bucket=test_intentional — usually pytest parametrize patterns). "
        "Mixed-bucket findings (one side src, one side test) survive — they "
        "surface possible test-leakage and are worth keeping."
    ),
)
@click.option(
    "--exclude-fixtures",
    is_flag=True,
    default=False,
    help=(
        "Drop clone pairs/clusters where any participating file lives under "
        "a fixtures/ / testdata/ / test_data/ directory. Useful when the "
        "noise comes from intentional fixture mirroring rather than tests."
    ),
)
@click.pass_context
def clones(ctx, threshold, min_lines, scope, top, persist, by_file, exclude_tests, exclude_fixtures):
    """Detect near-duplicate code via AST structural hashing.

    Re-parses source files and compares function AST structures via subtree
    hashing.  Finds Type-2 clones: identical control flow with different
    identifiers or literals.

    Unlike ``duplicates`` (metric-based), this uses actual tree-sitter AST
    comparison for higher precision.

    \b
    Examples:
      roam clones
      roam clones --threshold 0.85 --min-lines 8
      roam clones --persist
      roam clones --by-file --top 30

    See also ``duplicates`` (metric-based dup detection), ``critique``
    (clones-not-edited check on a diff), and ``debt`` (refactoring
    backlog).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    from roam.graph.clone_detect import detect_clones, store_clones

    with open_db(readonly=not persist) as conn:
        pairs, clusters = detect_clones(
            conn,
            min_similarity=threshold,
            min_lines=min_lines,
            scope=scope,
        )

        # W165 — bucket-aware filtering. We compute role buckets BEFORE
        # ``store_clones`` so the persisted clone_pairs / clone_clusters
        # tables and the registry mirror both reflect the user's filter
        # intent. ``--exclude-tests`` drops only ``test_intentional`` —
        # mixed (one side src, one side test) survives so the registry
        # keeps surfacing potential test-leakage. ``--exclude-fixtures``
        # drops any pair touching a fixtures/ / testdata/ path.
        if exclude_tests or exclude_fixtures:
            kept_pairs = []
            for p in pairs:
                if exclude_fixtures and (_is_fixture_path(p.file_a) or _is_fixture_path(p.file_b)):
                    continue
                if exclude_tests:
                    bucket = _role_bucket_for_pair(p.file_a, p.file_b)
                    if bucket == "test_intentional":
                        continue
                kept_pairs.append(p)

            kept_pair_keys = {tuple(sorted((p.qname_a, p.qname_b))) for p in kept_pairs}

            # Rebuild clusters: keep only members that participate in at
            # least one surviving pair (size>=2 after the filter).
            kept_clusters = []
            for c in clusters:
                member_files = [m.get("file") or "" for m in c.members]
                if exclude_fixtures and any(_is_fixture_path(f) for f in member_files):
                    continue
                if exclude_tests:
                    bucket = _role_bucket_for_cluster(member_files)
                    if bucket == "test_intentional":
                        continue
                kept_clusters.append(c)

            pairs = kept_pairs
            clusters = kept_clusters
            _ = kept_pair_keys  # reserved for future per-pair pruning

        if persist:
            store_clones(conn, pairs, clusters)
            # W165 — after store_clones writes the findings registry rows
            # (via ``clone_detect._emit_clone_findings``), enrich each row
            # with a ``role_bucket`` evidence field so consumers of
            # ``roam findings list --detector clones`` can post-filter by
            # production vs test_intentional vs mixed.
            _enrich_clones_findings_with_role_bucket(conn)
            conn.commit()

        if top > 0:
            clusters = clusters[:top]

        # Summary stats
        total_functions = sum(len(c.members) for c in clusters)
        total_pairs = len(pairs)
        avg_sim = sum(c.avg_similarity for c in clusters) / len(clusters) if clusters else 0.0

        # Estimate reducible lines
        reducible_lines = 0
        for c in clusters:
            lines = sorted(m["line_end"] - m["line_start"] + 1 for m in c.members)
            if len(lines) > 1:
                reducible_lines += sum(lines[:-1])

        # W165 — per-bucket cluster counts surfaced in the verdict line
        # (Pattern-3 vocabulary improvement from internal/dogfood/
        # SYNTHESIS-2026-05-12.md: separate buckets exposed at the
        # surface, not hidden in JSON).
        bucket_counts = {"production": 0, "test_intentional": 0, "mixed": 0}
        for c in clusters:
            files = [m.get("file") or "" for m in c.members]
            bucket_counts[_role_bucket_for_cluster(files)] += 1

        if clusters:
            verdict = (
                f"{len(clusters)} clone cluster{'s' if len(clusters) != 1 else ''} "
                f"found ({total_functions} functions, {round(avg_sim * 100)}% avg similarity) "
                f"({bucket_counts['production']} production"
                f" · {bucket_counts['test_intentional']} test_intentional"
                f" · {bucket_counts['mixed']} mixed)"
            )
        else:
            verdict = "No structural clones detected"

        # -- SARIF format -----------------------------------------------------
        # W1172: SARIF projection for CI / GitHub Code Scanning integration.
        # Branches BEFORE by_file / json / text paths so those legacy paths
        # stay byte-identical to pre-W1172 output. ``--sarif --by-file`` and
        # ``--sarif --json`` both resolve to SARIF (CI-format consumers want
        # the SARIF document; the by-file aggregation is a human-readable
        # view that does not project onto SARIF's per-finding model).
        if sarif_mode:
            from roam.output.sarif import clones_to_sarif, write_sarif

            cluster_values = [
                {
                    "cluster_id": c.cluster_id,
                    "avg_similarity": c.avg_similarity,
                    "size": len(c.members),
                    "members": c.members,
                    "pattern": c.pattern,
                    "suggestion": c.suggestion,
                    "role_bucket": _role_bucket_for_cluster([m.get("file") or "" for m in c.members]),
                }
                for c in clusters
            ]
            # Match the JSON-mode pair cap (50) so the SARIF document stays
            # bounded on pathological clone-heavy repos. The cap is
            # deterministic across modes.
            pair_values = [
                {
                    "file_a": p.file_a,
                    "func_a": p.func_a,
                    "line_a": p.line_a,
                    "file_b": p.file_b,
                    "func_b": p.func_b,
                    "line_b": p.line_b,
                    "similarity": p.similarity,
                    "role_bucket": _role_bucket_for_pair(p.file_a, p.file_b),
                }
                for p in pairs[:50]
            ]
            envelope = json_envelope(
                "clones",
                summary={
                    "verdict": verdict,
                    "clusters": len(clusters),
                    "clone_pairs": total_pairs,
                },
                clusters=cluster_values,
                pairs=pair_values,
            )
            click.echo(write_sarif(clones_to_sarif(envelope)))
            return

        # aggregate clone pairs into (file_a, file_b) coupling.
        if by_file:
            file_pair_counts: dict[tuple[str, str], int] = {}
            for p in pairs:
                key = tuple(sorted((p.file_a, p.file_b)))
                file_pair_counts[key] = file_pair_counts.get(key, 0) + 1
            file_pairs = [
                {"file_a": a, "file_b": b, "clone_pairs": n}
                for (a, b), n in sorted(file_pair_counts.items(), key=lambda x: -x[1])
            ]
            file_pairs_top = file_pairs[: max(1, top or 25)]
            verdict = f"{len(file_pairs)} clone-coupled file pair(s) (top {len(file_pairs_top)} shown)"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "clones",
                            summary={"verdict": verdict, "file_pairs_total": len(file_pairs)},
                            file_pairs=file_pairs_top,
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {verdict}")
            if not file_pairs:
                return
            click.echo()
            click.echo(f"{'Pairs':>5}  File A  ↔  File B")
            click.echo(f"{'-' * 5}  {'-' * 60}")
            for fp in file_pairs_top:
                click.echo(f"{fp['clone_pairs']:>5}  {fp['file_a']}  ↔  {fp['file_b']}")
            return

        if json_mode:
            # R22: wrap each cluster and pair in {value, confidence,
            # reason} so consumers can weight signals. Consumers that
            # previously read `clusters[i]["avg_similarity"]` must now
            # read `clusters[i]["value"]["avg_similarity"]` plus
            # `clusters[i]["confidence"]` / `clusters[i]["reason"]`.
            cluster_values = [
                {
                    "cluster_id": c.cluster_id,
                    "avg_similarity": c.avg_similarity,
                    "size": len(c.members),
                    "members": c.members,
                    "pattern": c.pattern,
                    "suggestion": c.suggestion,
                    # W165 — surface role_bucket on the JSON cluster too,
                    # not just on persisted findings rows. Agents that
                    # consume the live envelope can filter without going
                    # through the registry.
                    "role_bucket": _role_bucket_for_cluster([m.get("file") or "" for m in c.members]),
                }
                for c in clusters
            ]
            cluster_triples = wrap_findings(cluster_values, classifier=_cluster_classify)

            pair_values = [
                {
                    "file_a": p.file_a,
                    "func_a": p.func_a,
                    "line_a": p.line_a,
                    "file_b": p.file_b,
                    "func_b": p.func_b,
                    "line_b": p.line_b,
                    "similarity": p.similarity,
                    "role_bucket": _role_bucket_for_pair(p.file_a, p.file_b),
                }
                for p in pairs[:50]  # Cap pair output
            ]
            pair_triples = wrap_findings(pair_values, classifier=_pair_classify)

            # Combined distribution for the summary field (clusters +
            # pairs counted together — both are "findings").
            combined = cluster_triples + pair_triples
            distribution = confidence_distribution(combined)
            verdict_with_conf = verdict_with_high_count(verdict, distribution)

            click.echo(
                to_json(
                    json_envelope(
                        "clones",
                        summary={
                            "verdict": verdict_with_conf,
                            "clusters": len(clusters),
                            "clone_pairs": total_pairs,
                            "total_functions": total_functions,
                            "avg_similarity": round(avg_sim, 3),
                            "estimated_reducible_lines": reducible_lines,
                            "findings_confidence_distribution": distribution,
                            # W165 — per-bucket cluster counts surfaced in
                            # the summary so agent contracts can filter
                            # without re-walking clusters[].
                            "role_buckets": bucket_counts,
                        },
                        budget=token_budget,
                        clusters=cluster_triples,
                        pairs=pair_triples,
                    )
                )
            )
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")

        if not clusters:
            return

        click.echo()
        for c in clusters:
            sim_pct = round(c.avg_similarity * 100)
            files = [m.get("file") or "" for m in c.members]
            cluster_bucket = _role_bucket_for_cluster(files)
            click.echo(
                f"CLUSTER {c.cluster_id} -- {sim_pct}% similarity, {len(c.members)} functions [{cluster_bucket}]:"
            )
            for m in c.members:
                lines = m["line_end"] - m["line_start"] + 1
                click.echo(
                    f"  {m['function']:<40s} "
                    f"{loc(m['file'], m['line_start'])}"
                    f"  ({lines} lines, {m['ast_nodes']} AST nodes)"
                )
            click.echo(f"  Pattern: {c.pattern}")
            click.echo(f"  Suggestion: {c.suggestion}")
            click.echo()

        click.echo(
            f"SUMMARY: {len(clusters)} clusters, "
            f"{total_functions} functions, "
            f"~{reducible_lines} lines of reducible duplication"
        )
