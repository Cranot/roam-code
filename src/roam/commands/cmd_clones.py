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
from collections import Counter

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
    help="Skip functions shorter than <N> lines",  # W1117-followup-4
)
@click.option("--scope", default=None, type=str, help="Limit to files under this path prefix")
@click.option(
    "--top", "--limit", "top", default=0, type=int, help="Show only top <N> clusters (0=all)"
)  # W1142: --limit alias; W1117-followup-4
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

    # W607-BQ -- substrate-boundary plumbing on the clones AST-clone detector.
    # cmd_clones completes the W805 paired-scoring family (dark_matter +
    # duplicates + clones + smells) -- all four detect DRY/architecture debt
    # from different signal axes (co-change vs structural-similarity vs
    # AST-subtree vs anti-pattern). cmd_clones overlaps cmd_duplicates on the
    # DRY axis (W136/W821), and overlaps cmd_smells on the structural-debt
    # axis (W855 rename-invariant + W856 cross-layer clone subkinds).
    #
    # The substrate boundaries we wrap:
    #
    #   * query_candidates             -- the AST-subtree-hash detect_clones
    #                                     call that produces (pairs, clusters)
    #                                     from the indexed corpus.
    #   * apply_test_prod_separation   -- W165 test/prod/mixed filtering on
    #                                     the pair + cluster lists when
    #                                     ``--exclude-tests`` /
    #                                     ``--exclude-fixtures`` are set.
    #   * classify_role_buckets        -- W856 cross-layer clone bucket
    #                                     classification per cluster (the
    #                                     verdict-line ``role_buckets`` count
    #                                     pass).
    #   * emit_findings                -- ``--persist`` registry mirror
    #                                     (``store_clones`` +
    #                                     ``_enrich_clones_findings_with_role_bucket``
    #                                     + commit). Pattern-2 elimination
    #                                     target: replaces the implicit
    #                                     "no error handling around the
    #                                     persist write" path.
    #   * serialize_to_sarif           -- ``clones_to_sarif`` projection for
    #                                     CI gates.
    #
    # Marker family ``clones_<phase>_failed:<exc_class>:<detail>``
    # (underscore form -- matches the W805 paired sibling cmd_dark_matter
    # (W607-BK) and cmd_duplicates (W607-BM) marker discipline). Empty
    # bucket -> no field added -> byte-identical envelope on the happy path
    # (W607-A..BM parity).
    # Threads into BOTH the top-level ``warnings_out`` (preserved-list-field
    # discipline) AND ``summary.warnings_out`` + ``summary.partial_success
    # = True``. Composes with the pre-existing ``_warnings_out`` cap-hit
    # disclosure (W1142-followup) -- both bins flush into the same
    # envelope fields so a single consumer sees the union.
    _w607bq_warnings_out: list[str] = []

    def _run_check_bq(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BQ marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``clones_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607bq_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bq_warnings_out.append(f"clones_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-DC: aggregation-phase marker plumbing (additive) -----------
    # cmd_clones detects literal AST-clone classes -- the structural-debt
    # paired-scoring family (W805 4-way: clones BQ/DC, duplicates BM,
    # smells BN, dark_matter BK/CZ). W607-BQ (above) plumbed the substrate-
    # CALL layer (5 boundaries: query_candidates / apply_test_prod_separation
    # / classify_role_buckets / emit_findings / serialize_to_sarif).
    # W607-DC adds the AGGREGATION-PHASE layer on top:
    #
    #   score_classify       -- bucket the run state (CLONES_FOUND /
    #                            NO_CLONES) so consumers can read the run
    #                            classification without re-deriving from
    #                            raw counts.
    #   compute_predicate    -- extract clone-class rollup metrics
    #                            (cluster_count / pair_count /
    #                            total_functions / avg_similarity /
    #                            reducible_lines / distribution / role_buckets).
    #   compute_verdict      -- composite verdict-string assembly with
    #                            high-confidence count.
    #   serialize_envelope   -- json_envelope("clones", ...) projection.
    #
    # Marker family ``clones_*`` -- SAME family as W607-BQ (additive, not a
    # separate prefix). Empty bucket -> byte-identical envelope on the
    # success path. Three buckets (W607-BQ substrate + W607-DC aggregation
    # + W1142-followup cap-hit) are combined at envelope-emit time so
    # consumers see the full degradation lineage in marker-emission order.
    # The additive bucket stays distinguishable via its phase names
    # (``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
    # ``serialize_envelope``).
    #
    # STRUCTURAL-DEBT PAIRED-SCORING 4-WAY pairing analogue -- pattern
    # reused here for the AST-similarity axis. After W607-DC lands,
    # cmd_clones becomes the SECOND member of the 4-way to ALSO carry
    # an aggregation-phase layer (after cmd_dark_matter W607-CZ):
    #   cmd_clones        (W607-BQ substrate + DC THIS) -- AST-similarity axis
    #   cmd_duplicates    (W607-BM substrate)           -- token-similarity axis
    #   cmd_smells        (W607-BN substrate)           -- smell-pattern axis
    #   cmd_dark_matter   (W607-BK substrate + CZ)      -- co-change axis
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` kwarg in a
    # ``_run_check_dc(...)`` call MUST be a literal constant (not a
    # computed expression like ``len(clusters) if ...``). cmd_sbom W607-CG
    # sealed this axis; cmd_taint W607-CJ added the 5th discipline
    # (move ``len()`` INSIDE the closure); cmd_audit_trail_export
    # W607-CR added the 7th discipline (use bare ``dict[key]`` lookup
    # when the floor dict guarantees the key).
    #
    # W607-BQ/DC PHASE-NAME COLLISION (W607-CH 4th-discipline): the
    # substrate-CALL layer uses phase names query_candidates /
    # apply_test_prod_separation / classify_role_buckets / emit_findings /
    # serialize_to_sarif. None collide with score_classify /
    # compute_predicate / compute_verdict / serialize_envelope, so no
    # rename is required. ``serialize_to_sarif`` vs ``serialize_envelope``
    # are deliberately distinct phase names so an agent can tell which
    # serialiser raised.
    _w607dc_warnings_out: list[str] = []

    def _run_check_dc(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-DC marker emission.

        Mirror of ``_run_check_bq`` shape (same ``clones_<phase>_failed:``
        marker family) but writes into ``_w607dc_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607dc_warnings_out.append(f"clones_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        # W607-BQ: wrap the AST-subtree-hash detection. Default is an empty
        # ([], []) tuple so the downstream zero-cluster path still emits a
        # well-formed envelope.
        _detected = _run_check_bq(
            "query_candidates",
            detect_clones,
            conn,
            min_similarity=threshold,
            min_lines=min_lines,
            scope=scope,
            default=([], []),
        )
        pairs, clusters = _detected if _detected is not None else ([], [])

        # W165 — bucket-aware filtering. We compute role buckets BEFORE
        # ``store_clones`` so the persisted clone_pairs / clone_clusters
        # tables and the registry mirror both reflect the user's filter
        # intent. ``--exclude-tests`` drops only ``test_intentional`` —
        # mixed (one side src, one side test) survives so the registry
        # keeps surfacing potential test-leakage. ``--exclude-fixtures``
        # drops any pair touching a fixtures/ / testdata/ path.
        #
        # W607-BQ: wrap the test/prod separation pass so a raise inside
        # ``_role_bucket_for_pair`` / ``_role_bucket_for_cluster`` (e.g.
        # on a malformed file path) surfaces a structured marker rather
        # than crashing the command. The filter degrades to a no-op
        # (original pairs + clusters preserved) on the marker path so
        # the downstream verdict / JSON / SARIF paths still emit cleanly.
        if exclude_tests or exclude_fixtures:

            def _apply_test_prod_separation():
                kept_pairs = []
                for p in pairs:
                    if exclude_fixtures and (_is_fixture_path(p.file_a) or _is_fixture_path(p.file_b)):
                        continue
                    if exclude_tests:
                        bucket = _role_bucket_for_pair(p.file_a, p.file_b)
                        if bucket == "test_intentional":
                            continue
                    kept_pairs.append(p)

                # Rebuild clusters: keep only members that participate in
                # at least one surviving pair (size>=2 after the filter).
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
                return kept_pairs, kept_clusters

            _filtered = _run_check_bq(
                "apply_test_prod_separation",
                _apply_test_prod_separation,
                default=None,
            )
            if _filtered is not None:
                pairs, clusters = _filtered

        if persist:
            # W607-BQ: wrap the registry write path. Default is None --
            # the writes degrade to no-op on a raise while the read-side
            # ``pairs`` / ``clusters`` still surface in the envelope.
            # This replaces the implicit "no error handling around the
            # persist write" Pattern-2 silent fallback: the pre-W607-BQ
            # code path would have unwound the entire CLI on any
            # sqlite3.OperationalError surfacing from ``store_clones``
            # (locked DB, full disk, missing column on a stale schema).
            def _emit_findings():
                store_clones(conn, pairs, clusters)
                # W165 — after store_clones writes the findings registry rows
                # (via ``clone_detect._emit_clone_findings``), enrich each row
                # with a ``role_bucket`` evidence field so consumers of
                # ``roam findings list --detector clones`` can post-filter by
                # production vs test_intentional vs mixed.
                _enrich_clones_findings_with_role_bucket(conn)
                conn.commit()

            _run_check_bq(
                "emit_findings",
                _emit_findings,
                default=None,
            )

        # W1142-followup: cap-hit disclosure. Record the full pre-truncation
        # cluster count so the envelope can distinguish "N total clusters"
        # from "N of M, truncated by --limit".
        total_clusters_full = len(clusters)
        if top > 0:
            clusters = clusters[:top]
        # W1142-followup: pair count is already pre-truncation (`total_pairs`
        # is `len(pairs)` below); record the truncation flag derived from
        # the cluster slicing above.
        clusters_truncated = top > 0 and total_clusters_full > len(clusters)

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
        # the dogfood synthesis notes: separate buckets exposed at the
        # surface, not hidden in JSON).
        # W607-BQ: wrap the bucket-classification pass. A raise inside
        # ``_role_bucket_for_cluster`` (e.g. on a malformed file path)
        # now surfaces a structured marker; the bucket counts safe-floor
        # to zeros so the downstream verdict / JSON still emit cleanly.
        bucket_counts: dict[str, int] = {
            "production": 0,
            "test_intentional": 0,
            "mixed": 0,
        }

        def _classify_role_buckets():
            for c in clusters:
                files = [m.get("file") or "" for m in c.members]
                bucket_counts[_role_bucket_for_cluster(files)] += 1

        _run_check_bq(
            "classify_role_buckets",
            _classify_role_buckets,
            default=None,
        )

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
            # W607-BQ: wrap the SARIF projection so a raise inside
            # ``clones_to_sarif`` surfaces a structured marker rather
            # than crashing the CI gate. Default is an empty SARIF
            # document shape so ``write_sarif`` still emits valid JSON.
            sarif_doc = _run_check_bq(
                "serialize_to_sarif",
                clones_to_sarif,
                envelope,
                default={
                    "version": "2.1.0",
                    "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
                    "runs": [],
                },
            )
            click.echo(write_sarif(sarif_doc))
            return

        # aggregate clone pairs into (file_a, file_b) coupling.
        if by_file:
            file_pair_counts: Counter[tuple[str, str]] = Counter(tuple(sorted((p.file_a, p.file_b))) for p in pairs)
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

            # W607-DC -- compute_predicate boundary. Wraps the clones
            # rollup-metrics extraction (cluster_count + pair_count +
            # total_functions + avg_similarity + reducible_lines +
            # distribution + role_buckets). A future refactor of the
            # cluster/pair triples that drops or renames a field would
            # otherwise crash here. Floor to documented zero counts +
            # empty distribution so downstream summary fields stay
            # non-null. W978 discipline: ``default=`` is a literal dict,
            # NOT a computed expression over the (potentially poisoned)
            # inputs.
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(clusters)`` lives
            # INSIDE the wrapped closure rather than at the kwarg-bind
            # site. A __len__-poisoned ``clusters`` sentinel would
            # otherwise escape the wrap. cmd_taint W607-CJ
            # 5th-discipline anchor.
            def _compute_predicate_fields(_cluster_triples, _pair_triples, _bucket_counts) -> dict:
                _combined = list(_cluster_triples) + list(_pair_triples)
                _dist = confidence_distribution(_combined)
                return {
                    "cluster_count": len(_cluster_triples),
                    "pair_count": len(_pair_triples),
                    "distribution": _dist,
                    "role_buckets": dict(_bucket_counts),
                }

            _pred_fields = _run_check_dc(
                "compute_predicate",
                _compute_predicate_fields,
                cluster_triples,
                pair_triples,
                bucket_counts,
                default={
                    "cluster_count": 0,
                    "pair_count": 0,
                    "distribution": {},
                    "role_buckets": {
                        "production": 0,
                        "test_intentional": 0,
                        "mixed": 0,
                    },
                },
            )
            distribution = _pred_fields["distribution"]

            # W607-DC -- compute_verdict boundary. Wraps the verdict-
            # string assembly (verdict_with_high_count). A
            # ``__format__``-raising sentinel or a vocabulary refactor
            # could otherwise crash the envelope at the verdict line.
            # Floor must NOT re-interpolate the same values that tripped
            # the closure (W978 first-hypothesis discipline). Use the
            # literal "clones completed" floor (LAW 6 still holds:
            # the line works standalone).
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: ``verdict`` /
            # ``distribution`` are passed as raw args; the wrap-then-
            # format logic lives INSIDE the closure (cmd_taint W607-CJ
            # 5th-discipline anchor).
            def _build_verdict_str(_verdict, _distribution):
                return verdict_with_high_count(_verdict, _distribution)

            verdict_with_conf = _run_check_dc(
                "compute_verdict",
                _build_verdict_str,
                verdict,
                distribution,
                default="clones completed",
            )

            # W1142-followup: cap-hit disclosure on the canonical JSON
            # envelope. ``count``/``total_count``/``truncated``/``limit``
            # surface whether the agent's --limit collapsed signal.
            _cap_summary = {
                "count": len(clusters),
                "total_count": total_clusters_full,
                "truncated": clusters_truncated,
                "limit": top,
            }
            _warnings_out: list[str] = []
            if clusters_truncated:
                _warnings_out.append(
                    f"truncated to {len(clusters)} of {total_clusters_full} — pass --limit larger to see more"
                )

            # W607-DC -- score_classify boundary. Wraps the run-state
            # bucketing into a state label (CLONES_FOUND / NO_CLONES) so
            # a downstream refactor of the state-selection logic surfaces
            # a marker rather than crashing. Floor returns documented
            # state matching the no-clones branch shape so downstream
            # verdict / compute_predicate stay non-null.
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: ``clusters`` is passed
            # as raw arg; the branch logic + len() lives INSIDE the
            # closure. W978 5th-discipline (cmd_taint W607-CJ): no
            # ``len()`` at kwarg-bind site.
            def _score_classify_run(_clusters):
                _n = len(_clusters)
                if _n > 0:
                    _state = "CLONES_FOUND"
                else:
                    _state = "NO_CLONES"
                return {"state": _state, "scanned": _n}

            _score_dict = _run_check_dc(
                "score_classify",
                _score_classify_run,
                clusters,
                default={"state": "DEGRADED", "scanned": 0},
            )

            summary_payload = {
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
                # W607-DC: surface score_classify result on the envelope
                # so consumers can read the run state without
                # re-deriving from raw counts. W978 7th-discipline
                # anchor: bare ``_score_dict["state"]`` lookup (floor
                # dict guarantees the key) -- NOT
                # ``.get("state", expensive_default)``.
                "run_state": _score_dict["state"],
                **_cap_summary,
            }
            # W607-BQ + DC: union the cap-hit disclosure list with the
            # substrate-CALL marker list AND the aggregation-phase
            # marker list. All three bins flush into the same
            # ``warnings_out`` envelope fields so a single consumer
            # sees the full disclosure surface. Order: cap-hit first
            # (UI-relevant), substrate markers second (debug-relevant),
            # aggregation markers third (debug-relevant). Empty union
            # -> no field added -> byte-identical envelope on the
            # happy path.
            _combined_warnings = list(_warnings_out) + list(_w607bq_warnings_out) + list(_w607dc_warnings_out)

            _envelope_kwargs: dict = {
                "summary": summary_payload,
                "budget": token_budget,
                "clusters": cluster_triples,
                "pairs": pair_triples,
            }
            if _combined_warnings:
                summary_payload["warnings_out"] = list(_combined_warnings)
                summary_payload["partial_success"] = True
                _envelope_kwargs["warnings_out"] = list(_combined_warnings)

            # W607-DC -- serialize_envelope boundary. Wraps the envelope
            # serialization itself. A downstream schema-shape refactor
            # that breaks ``json_envelope("clones", ...)`` would
            # otherwise crash AFTER all substrate + aggregation signals
            # were already gathered. Floor to a minimal envelope stub so
            # consumers still receive a parseable JSON object with the
            # marker attached + the canonical command name. Mirror of
            # cmd_dark_matter's W607-CZ / cmd_postmortem's W607-CV /
            # cmd_taint's W607-CJ / cmd_audit_trail_export's W607-CR
            # serialize_envelope floor pattern.
            _envelope_floor: dict = {
                "command": "clones",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": verdict_with_conf,
                    "partial_success": True,
                    "warnings_out": list(_combined_warnings),
                },
                "warnings_out": list(_combined_warnings),
            }
            _envelope = _run_check_dc(
                "serialize_envelope",
                json_envelope,
                "clones",
                default=_envelope_floor,
                **_envelope_kwargs,
            )
            # W607-DC -- if ``serialize_envelope`` raised AFTER the
            # combined bucket was already snapshotted, the new
            # ``clones_serialize_envelope_failed:`` marker was appended
            # to ``_w607dc_warnings_out`` and the floor stub carries
            # only the pre-raise combined list. Rebuild the floor
            # stub's warnings_out so the new marker reaches the JSON
            # output. Clean path -> envelope is the real json_envelope
            # return value, no rebuild needed.
            if _envelope is _envelope_floor and _w607dc_warnings_out:
                _combined_warnings = list(_warnings_out) + list(_w607bq_warnings_out) + list(_w607dc_warnings_out)
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings)
                _envelope_floor["warnings_out"] = list(_combined_warnings)
                _envelope = _envelope_floor

            click.echo(to_json(_envelope))
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
