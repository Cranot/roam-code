"""Detect dark matter: co-changing files with no structural dependency."""

from __future__ import annotations

import hashlib
import json as _json
import sqlite3
from collections import Counter

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json
from roam.output.risk import normalize_risk_level, risk_rank

# W154 (W93 follow-up): dark-matter is the Nth detector migrating onto the
# central findings registry (after ``clones`` in W95, ``dead`` in W99,
# ``complexity`` in W102, ``smells`` in W109, ``bus-factor`` in W115,
# ``pr-risk`` in W134, etc.). The shape mirrors those — a stable detector
# version stamp and a deterministic ``finding_id_str`` so re-runs upsert
# instead of duplicating rows. Bump this when the engine's category
# vocabulary, NPMI / lift formulas, or hypothesis output shape changes.
DARK_MATTER_DETECTOR_VERSION: str = "1.0.0"


# W154 — category-driven confidence tier mapping.
#
# Dark-matter pairs come in two evidence classes:
#
# * Typed hypotheses (the HypothesisEngine resolved a concrete reason via
#   shared-DB / event-bus / shared-config / shared-API / text-similarity
#   pattern matching — graph history + source-pattern evidence both
#   used) → ``structural``. The pair has a *named* cause beyond the
#   raw correlation.
# * UNKNOWN (the engine ran but didn't match any pattern) → ``heuristic``.
#   No resolved cause; pure statistical correlation (NPMI + co-change
#   count) — higher false-positive risk.
#
# A pair whose hypothesis was never computed (e.g. ``--explain`` not
# passed in the legacy text path) falls back to ``heuristic`` via the
# default below — but the ``--persist`` branch always classifies, so the
# typical write produces a clean structural/heuristic split.
_DARK_MATTER_TYPED_CATEGORIES: frozenset[str] = frozenset(
    {
        "SHARED_DB",
        "EVENT_BUS",
        "SHARED_CONFIG",
        "SHARED_API",
        "TEXT_SIMILARITY",
        # Spec-named categories preserved for forward-compat; the engine
        # doesn't currently emit these but a future hypothesizer could.
        "COPY_PASTE",
        "NAMING",
    }
)
_DARK_MATTER_DEFAULT_CONFIDENCE: str = "heuristic"


def _dark_matter_confidence_for_category(category: str | None) -> str:
    """Map a hypothesis category to a confidence tier.

    ``UNKNOWN`` (or missing) → ``heuristic`` (pure statistical
    correlation). Typed categories → ``structural`` (graph + source
    evidence both used to reach the verdict).
    """
    if not category or category == "UNKNOWN":
        return _DARK_MATTER_DEFAULT_CONFIDENCE
    if category in _DARK_MATTER_TYPED_CATEGORIES:
        return "structural"
    # Unknown new category — treat as heuristic until the mapping is
    # explicitly updated. Mirrors the ``_SMELL_DEFAULT_CONFIDENCE``
    # fallback pattern in W109.
    return _DARK_MATTER_DEFAULT_CONFIDENCE


# ---------------------------------------------------------------------------
# W641-followup-G — canonical risk-LEVEL projection from cochange-strength
# ---------------------------------------------------------------------------
#
# cmd_dark_matter is the seventh emitter joining the W641 risk-axis cluster
# after cmd_pr_risk (W641), cmd_impact (W641-followup-A), cmd_critique
# (W641-followup-B), cmd_pr_bundle (W641-followup-C), cmd_attest
# (W641-followup-D), and cmd_diff (W641-followup-E). The canonical W631
# risk-LEVEL bucket is derived from two complementary cochange-coupling
# signals on the dark-matter rollup:
#
#   * ``total_pairs`` — the count of hidden file-pair couplings detected
#     after NPMI + min-cochange filtering (already capped by ``-n``).
#   * ``max_strength`` — the maximum cochange-strength across pairs (the
#     ratio ``cochanges / avg_commits`` from
#     :func:`roam.graph.dark_matter.dark_matter_edges`). Strength is the
#     "how often does this pair move together vs how often either moves
#     alone" axis; high values mean the hidden coupling is dense.
#
# Thresholds (max-tier wins across the two axes — OR-aggregation mirrors
# the W641-followup-A/E polarity):
#
#   total_pairs >= 20 OR max_strength >= 0.7 -> "high"
#   total_pairs >=  5 OR max_strength >= 0.4 -> "medium"
#   total_pairs >   0 OR max_strength >  0   -> "low"
#   total_pairs ==  0                         -> "low"
#
# Conservative-on-critical: cmd_dark_matter saturates at ``high`` (same as
# cmd_impact / cmd_diff / cmd_critique). ``critical`` is reserved for the
# multi-factor composite-score commands (cmd_attest's ``_collect_risk``).
# The W531 CI-safety lesson: a threshold wobble MUST NOT promote a finding
# into a CI-gating rank — and dark-matter is a single-axis signal (cochange
# correlation), not a multi-factor composite.


def _dark_matter_risk_level(
    total_pairs: int,
    max_strength: float,
    *,
    warnings_out: list[str] | None = None,
) -> str:
    """Project dark-matter rollup metrics onto the canonical W631 risk-LEVEL set.

    Returns a string in :data:`roam.output.risk.RISK_LEVELS`
    (``critical``/``high``/``medium``/``low``). cmd_dark_matter saturates
    at ``high`` (W641-followup-A/B/E discipline — single-axis cochange-
    coupling signal does not justify escalating to ``critical``).

    Safe-floor: any combination producing ``total_pairs == 0`` AND
    ``max_strength == 0.0`` collapses to ``low`` (W531 CI-safety: an empty
    coupling rollup MUST NOT promote into a gating rank).

    Unknown / negative inputs accumulate a marker on *warnings_out* (when
    provided) under ``dark_matter_unknown_severity:<value>`` so Pattern-2
    silent-fallback stays loud — mirrors the W918 alerts / W989 pr-risk /
    W641-followup-B critique / W641-followup-D attest / W641-followup-E
    diff discipline.
    """
    # Guard: non-int total_pairs / non-numeric max_strength should never reach
    # the projection, but stay loud if they do — record a marker + safe-floor.
    if not isinstance(total_pairs, int) or not isinstance(max_strength, (int, float)):
        if warnings_out is not None:
            warnings_out.append(f"dark_matter_unknown_severity:non_numeric({total_pairs!r},{max_strength!r})")
        return "low"
    if total_pairs < 0 or max_strength < 0:
        if warnings_out is not None:
            warnings_out.append(f"dark_matter_unknown_severity:negative({total_pairs},{max_strength})")
        return "low"
    if total_pairs >= 20 or max_strength >= 0.7:
        return "high"
    if total_pairs >= 5 or max_strength >= 0.4:
        return "medium"
    return "low"


def _canonical_pair(path_a: str, path_b: str) -> tuple[str, str]:
    """Return the (lo, hi) lexicographic ordering of a file-pair.

    Dark-matter couplings are undirected — ``(A, B)`` and ``(B, A)``
    describe the same hidden coupling. We canonicalise to lexicographic
    sort so the finding id is independent of the engine's emission
    order.
    """
    return (path_a, path_b) if path_a <= path_b else (path_b, path_a)


def _dark_matter_finding_id(path_a: str, path_b: str) -> str:
    """Stable, deterministic finding id for one dark-matter pair.

    The pair is sorted lexicographically before hashing so ``(A, B)``
    and ``(B, A)`` produce the same id — same hidden coupling, same
    registry row. Format mirrors W95 clones / W109 smells:
    ``"<detector>:<kind>:<sha1[:12]>"``.
    """
    lo, hi = _canonical_pair(path_a, path_b)
    raw = f"{lo}::{hi}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"dark-matter:arch.dark_matter:{digest}"


def _finding_record_for_stable_pair_identity(p: dict, source_version: str, finding_record_cls):
    """Build one registry record while preserving stable pair identity.

    The hidden tradeoff is identity stability vs evidence fidelity: the
    file-pair key is canonicalized for deterministic upserts, while the
    evidence payload preserves the detector's coupling metrics.
    """
    path_a = p.get("path_a") or ""
    path_b = p.get("path_b") or ""
    if not path_a or not path_b:
        # Skip malformed engine output — every legitimate row has both
        # paths populated.
        return None

    lo, hi = _canonical_pair(path_a, path_b)
    hyp = p.get("hypothesis") or {}
    category = hyp.get("category") or "UNKNOWN"
    detail = hyp.get("detail") or ""

    finding_id = _dark_matter_finding_id(path_a, path_b)
    qualified_name = f"{lo}::{hi}"

    evidence = {
        "qualified_name": qualified_name,
        "path_a": lo,
        "path_b": hi,
        "npmi": p.get("npmi"),
        "lift": p.get("lift"),
        "strength": p.get("strength"),
        "cochange_count": p.get("cochange_count"),
        "hypothesis_category": category,
        "hypothesis_detail": detail,
        "hypothesis_confidence": hyp.get("confidence"),
    }
    claim = (
        f"dark-matter coupling: {lo} <-> {hi} "
        f"(NPMI {p.get('npmi', 0)}, co-changes {p.get('cochange_count', 0)}, "
        f"hypothesis {category})"
    )
    confidence = _dark_matter_confidence_for_category(category)
    return finding_record_cls(
        finding_id_str=finding_id,
        subject_kind="file_pair",
        subject_id=None,
        claim=claim,
        evidence_json=_json.dumps(evidence, sort_keys=True),
        confidence=confidence,
        source_detector="dark-matter",
        source_version=source_version,
    )


def _emit_finding_records(conn: sqlite3.Connection, records) -> int:
    """Emit an iterable of FindingRecords into the central findings registry.

    Centralizes the uniform emission mechanics that are duplicated across
    every detector's persist path: local-import ``emit_finding``, skip
    ``None`` values, and count rows written. Each caller is still
    responsible for building its detector-specific ``FindingRecord``;
    this helper owns only the invariant part.
    """
    # Local import keeps the cost out of the read-only path —
    # callers without --persist never reach here.
    from roam.db.findings import emit_finding

    emitted = 0
    for record in records:
        if record is None:
            continue
        emit_finding(conn, record)
        emitted += 1
    return emitted


def _emit_dark_matter_findings(
    conn: sqlite3.Connection,
    pairs: list[dict],
    source_version: str,
) -> int:
    """Mirror each dark-matter pair into the central findings registry.

    Returns the count of finding rows written. Caller is responsible
    for opening ``conn`` writable; ``emit_finding`` does not commit
    (the caller commits once at the end of the persist branch).

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard dark-matter command path.

    ``subject_kind`` is the NEW ``file_pair`` vocabulary (W154) — the
    first file-pair subject on the registry. ``subject_id`` stays NULL
    because file pairs don't map to ``symbols.id``; consumers join on
    ``(subject_kind = 'file_pair' AND finding_id_str = ?)`` instead.
    Mirrors the W134 pr-risk NULL-subject pattern.
    """
    from roam.db.findings import FindingRecord

    return _emit_finding_records(
        conn,
        (_finding_record_for_stable_pair_identity(p, source_version, FindingRecord) for p in pairs),
    )


@roam_capability(
    name="dark-matter",
    category="architecture",
    summary="Detect dark matter: file pairs that co-change but have no structural link",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command(name="dark-matter")
@click.option("-n", "limit", default=30, help="Max pairs to show")
@click.option("--min-npmi", default=0.3, type=float, show_default=True, help="Minimum NPMI threshold")
@click.option("--min-cochanges", default=3, type=int, show_default=True, help="Minimum co-change count")
@click.option("--explain", is_flag=True, help="Add hypothesis for each pair")
@click.option("--category", is_flag=True, help="Group output by hypothesis category")
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist findings to .roam/index.db findings registry "
        "(cross-detector queryable via `roam findings list --detector dark-matter`). "
        "The detector-specific output is unchanged; the registry rows are "
        "the denormalised cross-detector surface. Persisted set always "
        "carries hypothesis categories — the engine is invoked unconditionally "
        "under --persist so the confidence tier is computed for every pair."
    ),
)
@click.pass_context
def hidden_coupling_cmd(ctx, limit, min_npmi, min_cochanges, explain, category, persist):
    """Detect dark matter: file pairs that co-change but have no structural link.

    Unlike ``coupling`` (which measures file-level co-change frequency),
    this command finds file pairs that co-change frequently despite having
    no structural dependency.

    Dark matter couplings indicate hidden dependencies -- shared databases,
    event buses, config keys, or copy-paste patterns. Use --explain to see
    hypothesized reasons for each coupling.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    from roam.graph.dark_matter import HypothesisEngine, dark_matter_edges

    # W607-BK -- substrate-boundary plumbing on the dark-matter
    # hidden-coupling detector. cmd_dark_matter is one of the W805
    # paired-scoring detector family (dark_matter + duplicates + clones +
    # smells); each one detects DRY/architecture debt from a different
    # signal axis (co-change, AST-similarity, token-similarity, smell-
    # patterns) and the same project root can produce non-empty buckets
    # on every axis simultaneously. The substrate boundaries we wrap:
    #
    #   * compute_cochange_pairs     -- the core ``dark_matter_edges`` call
    #                                    (NPMI + cochange-count filter, the
    #                                    primary detector engine).
    #   * hypothesize_pairs          -- HypothesisEngine.classify_all (typed
    #                                    SHARED_DB / EVENT_BUS / SHARED_CONFIG
    #                                    / SHARED_API / TEXT_SIMILARITY
    #                                    pattern matching against source).
    #   * emit_findings              -- registry mirror under --persist
    #                                    (W154 file_pair subject_kind).
    #   * query_cochange_count       -- empty-floor disclosure probe
    #                                    (distinguishes "clean populated
    #                                    graph -> 0 pairs" from "no
    #                                    co-change history at all").
    #   * serialize_to_sarif         -- SARIF projection for CI gates.
    #
    # Marker family ``dark_matter_<phase>_failed:<exc_class>:<detail>``
    # (underscore form -- matches the pre-existing
    # ``dark_matter_unknown_severity:`` marker emitted by
    # ``_dark_matter_risk_level``). Empty bucket -> no field added -> byte-
    # identical envelope on the happy path (W607-A..BG parity discipline).
    # Threads into BOTH the top-level ``warnings_out`` (preserved-list-
    # field discipline) AND ``summary.warnings_out`` +
    # ``summary.partial_success=True``.
    #
    # ADDITIVE pattern: cmd_dark_matter already has a
    # ``dark_matter_unknown_severity:<value>`` marker family (W641-followup-G,
    # emitted by ``_dark_matter_risk_level`` via the ``warnings_out``
    # parameter). W607-BK extends the same prefix family with substrate-
    # call markers (``dark_matter_<phase>_failed:<exc>:<detail>``). Both
    # marker families coexist on the same accumulator.
    _w607bk_warnings_out: list[str] = []

    def _run_check_bk(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BK marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a
        ``dark_matter_<phase>_failed:<exc_class>:<detail>`` marker via
        ``_w607bk_warnings_out`` and return *default* -- the envelope
        still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bk_warnings_out.append(f"dark_matter_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-CZ: aggregation-phase marker plumbing (additive) -----------
    # cmd_dark_matter detects hidden co-change coupling -- the structural-
    # debt paired-scoring family (W805 4-way: clones BQ, duplicates BM,
    # smells BN, dark_matter BK/CZ). W607-BK (above) plumbed the substrate-
    # CALL layer (5 boundaries: compute_cochange_pairs / hypothesize_pairs /
    # emit_findings / query_cochange_count / serialize_to_sarif). W607-CZ
    # adds the AGGREGATION-PHASE layer on top:
    #
    #   score_classify       -- bucket the run into a state label
    #                           (HIDDEN_COUPLINGS_FOUND / NO_COUPLINGS /
    #                            NO_COCHANGE_HISTORY)
    #   compute_predicate    -- extract dark-matter rollup metrics
    #                           (hidden_pair_count / max_coupling /
    #                            total_pairs / by_category)
    #   compute_verdict      -- composite verdict-string assembly
    #   serialize_envelope   -- json_envelope("dark-matter", ...) projection
    #
    # Marker family ``dark_matter_*`` -- SAME family as W607-BK (additive,
    # not a separate prefix). Empty bucket -> byte-identical envelope on
    # the success path. Both buckets are combined at envelope-emit time so
    # consumers see the full degradation lineage in marker-emission order.
    # The additive bucket stays distinguishable via its phase names
    # (``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
    # ``serialize_envelope``).
    #
    # STRUCTURAL-DEBT PAIRED-SCORING 4-WAY pairing analogue -- pattern
    # reused here for the dark-matter co-change axis:
    #   cmd_clones        (W607-BQ substrate)        -- AST-similarity axis
    #   cmd_duplicates    (W607-BM substrate)        -- token-similarity axis
    #   cmd_smells        (W607-BN substrate)        -- smell-pattern axis
    #   cmd_dark_matter   (W607-BK substrate + CZ THIS) -- co-change axis
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` kwarg in a
    # ``_run_check_cz(...)`` call MUST be a literal constant (not a
    # computed expression like ``len(pairs) if ...``). A computed default
    # expression evaluates BEFORE the wrap call, so a raise inside the
    # expression escapes the try-block. cmd_sbom W607-CG sealed this
    # axis. cmd_taint W607-CJ added the 5th discipline (move ``len()``
    # INSIDE the closure, not at the kwarg-bind site). cmd_audit_trail_export
    # W607-CR added the 7th discipline (use bare ``dict[key]`` lookup when
    # the floor dict guarantees the key, NOT
    # ``dict.get(key, expensive_default)`` which evaluates default eagerly).
    #
    # W607-BK/CZ PHASE-NAME COLLISION (W607-CH 4th-discipline): the
    # substrate-CALL layer uses phase names compute_cochange_pairs /
    # hypothesize_pairs / emit_findings / query_cochange_count /
    # serialize_to_sarif. None collide with score_classify /
    # compute_predicate / compute_verdict / serialize_envelope, so no
    # rename is required. ``serialize_to_sarif`` vs ``serialize_envelope``
    # are deliberately distinct phase names so an agent can tell which
    # serialiser raised.
    _w607cz_warnings_out: list[str] = []

    def _run_check_cz(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-CZ marker emission.

        Mirror of ``_run_check_bk`` shape (same
        ``dark_matter_<phase>_failed:`` marker family) but writes into
        ``_w607cz_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cz_warnings_out.append(f"dark_matter_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        pairs = _run_check_bk(
            "compute_cochange_pairs",
            dark_matter_edges,
            conn,
            min_cochanges=min_cochanges,
            min_npmi=min_npmi,
            default=[],
        )
        if pairs is None:
            pairs = []
        pairs = pairs[:limit]

        # Run hypothesis engine when needed.
        # --persist always classifies so the confidence tier is computed
        # for every row written into the registry; otherwise we only pay
        # the classification cost when the human-facing output needs it.
        # ``sarif_mode`` joins the set so the SARIF projection's
        # severity (structural -> warning, heuristic -> note) reflects
        # the engine's typed-vs-untyped split, not the default fallback.
        need_hypotheses = explain or category or json_mode or persist or sarif_mode
        if need_hypotheses and pairs:
            root = find_project_root()
            engine = HypothesisEngine(root)
            _run_check_bk(
                "hypothesize_pairs",
                engine.classify_all,
                pairs,
                default=None,
            )

        # --- W154: mirror into the central findings registry ---
        # Runs ONLY with --persist. The persisted set is independent of
        # any display-time filtering (``--explain`` / ``--category``);
        # we emit every detected pair (already capped by ``-n``) so the
        # registry stays comprehensive regardless of how a particular
        # invocation slices the view.
        #
        # W607-BK: replaces the pre-existing ``try / except
        # sqlite3.OperationalError: pass`` Pattern-2 silent-fallback. The
        # old block silently no-op'd whenever the findings table was
        # missing (pre-W89 schema) OR whenever ANY OperationalError
        # surfaced (locked DB, full disk, etc.). New path surfaces the
        # exception class + detail via a structured marker so the
        # degradation is visible to consumers.
        if persist and pairs:

            def _emit_and_commit():
                _emit_dark_matter_findings(conn, pairs, DARK_MATTER_DETECTOR_VERSION)
                conn.commit()

            _run_check_bk(
                "emit_findings",
                _emit_and_commit,
                default=None,
            )

        # --- SARIF output (W1211) ---
        # Branches BEFORE json/text so the pre-existing paths stay
        # byte-identical to pre-W1211. The SARIF projection mirrors the
        # displayed slice — ``pairs`` here has already been capped by
        # ``-n``, so a CI gate sees the same evidence the human / agent
        # sees. Hypotheses are guaranteed-resolved on this path
        # (``sarif_mode`` joins ``need_hypotheses`` above) so the SARIF
        # severity tracks the W154 confidence tier (structural ->
        # warning; heuristic -> note).
        if sarif_mode:
            from roam.output.sarif import dark_matter_to_sarif, write_sarif

            sarif_findings = [
                {
                    "file_a": p["path_a"],
                    "file_b": p["path_b"],
                    "npmi": p.get("npmi"),
                    "lift": p.get("lift"),
                    "strength": p.get("strength"),
                    "cochange_count": p.get("cochange_count"),
                    "hypothesis": p.get("hypothesis"),
                }
                for p in pairs
            ]
            # W607-BK: wrap the SARIF projection so a raise inside
            # ``dark_matter_to_sarif`` surfaces a structured marker rather
            # than crashing the CI gate. Default is an empty SARIF
            # document shape so ``write_sarif`` still emits valid JSON.
            sarif_doc = _run_check_bk(
                "serialize_to_sarif",
                dark_matter_to_sarif,
                sarif_findings,
                default={"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json", "runs": []},
            )
            click.echo(write_sarif(sarif_doc))
            return

        if json_mode:
            by_cat: dict[str, int] = Counter()
            for p in pairs:
                cat = p.get("hypothesis", {}).get("category", "UNKNOWN")
                by_cat[cat] += 1

            total = len(pairs)
            parts = [f"{v} {k}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1])]

            # W607-CZ -- compute_predicate boundary. Wraps the dark-matter
            # rollup-metrics extraction (hidden pair count + max coupling +
            # total pair count + by-category histogram). A future refactor
            # of the pairs[] schema that drops or renames the strength /
            # npmi / category fields would otherwise crash here. Floor to
            # documented zero counts matching the empty-bucket branch shape
            # so downstream summary fields stay non-null. W978 discipline:
            # ``default=`` is a literal dict, NOT a computed expression
            # over the (potentially poisoned) inputs.
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(pairs)`` lives
            # INSIDE the wrapped closure rather than at the kwarg-bind
            # site. A __len__-poisoned ``pairs`` sentinel would otherwise
            # escape the wrap. cmd_taint W607-CJ 5th-discipline anchor.
            def _compute_predicate_fields(_pairs, _by_cat) -> dict:
                _max_str = 0.0
                for _p in _pairs:
                    _s = _p.get("strength") or 0.0
                    try:
                        _sf = float(_s)
                    except (TypeError, ValueError):
                        _sf = 0.0
                    if _sf > _max_str:
                        _max_str = _sf
                return {
                    "total_pairs": len(_pairs),
                    "hidden_pair_count": len(_pairs),
                    "max_coupling": _max_str,
                    "by_category": dict(_by_cat),
                }

            _pred_fields = _run_check_cz(
                "compute_predicate",
                _compute_predicate_fields,
                pairs,
                by_cat,
                default={
                    "total_pairs": 0,
                    "hidden_pair_count": 0,
                    "max_coupling": 0.0,
                    "by_category": {},
                },
            )
            # W805-followup-D: empty-state disclosure (Pattern 2 silent-
            # fallback fix). Zero dark-matter pairs can mean two things:
            # (a) the co-change graph was analyzed cleanly and produced
            # no hidden couplings (real success — no `state` stamp), OR
            # (b) the corpus had no co-change history to analyze
            # (degraded — `state=no_cochange` + partial_success=True).
            # Distinguish by actually querying git_cochange instead of
            # inferring from the empty pairs result; the previous code
            # always claimed (b) on zero pairs which is itself a silent
            # Pattern-2 — a clean populated graph would be labelled
            # "no co-change history" incorrectly.
            cochange_count: int
            if total == 0:
                # W607-BK: wrap the empty-floor probe -- a corrupt /
                # missing git_cochange table now surfaces a structured
                # marker rather than silently degrading to the "no
                # cochange history" verdict (Pattern-2 silent-fallback
                # elimination -- the pre-W607-BK ``except
                # sqlite3.OperationalError: pass`` could mask a real
                # data-layer failure as the "no cochange history" verdict).
                def _query_cochange_count():
                    return conn.execute("SELECT COUNT(*) FROM git_cochange").fetchone()[0]

                _cc = _run_check_bk(
                    "query_cochange_count",
                    _query_cochange_count,
                    default=0,
                )
                cochange_count = _cc if _cc is not None else 0
            else:
                cochange_count = -1  # unused when total > 0
            # W641-followup-G — canonical W631 risk-LEVEL projection from
            # the dark-matter rollup. Two axes feed ``_dark_matter_risk_level``:
            # the total pair count (already capped by ``-n``) and the maximum
            # cochange-strength across pairs (``cochanges / avg_commits``).
            # Cross-command consumers can compare e.g.
            # ``risk_rank(summary.risk_level_canonical) >= 3`` to gate on
            # dense hidden coupling without re-deriving the threshold table.
            #
            # Empty / no-cochange paths safe-floor to ``low`` via the
            # ``(0, 0.0)`` projection — the canonical fields are emitted
            # unconditionally so agents downstream call ``risk_rank(...)``
            # without None-handling (parity with W641-followup-A/B/D/E).
            # W607-CZ: ``_max_strength`` now lifted from ``_pred_fields`` so
            # the computation is wrapped by the compute_predicate boundary
            # (defensive lift on the floored dict guarantees the key).
            _max_strength = _pred_fields["max_coupling"]
            _dm_warnings_out: list[str] = []
            _dm_domain_level = _dark_matter_risk_level(
                total,
                _max_strength,
                warnings_out=_dm_warnings_out,
            )
            risk_level_canonical = normalize_risk_level(_dm_domain_level) or "low"
            risk_rank_int = risk_rank(risk_level_canonical)

            # W607-CZ -- score_classify boundary. Wraps the run-state
            # bucketing (couplings-found vs cochange-history-missing) into
            # a state label (HIDDEN_COUPLINGS_FOUND / NO_COUPLINGS /
            # NO_COCHANGE_HISTORY) so a downstream refactor of the
            # state-selection logic surfaces a marker rather than crashing.
            # Floor returns documented state matching the no-couplings
            # branch shape so downstream verdict / compute_predicate stay
            # non-null.
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: ``total`` / ``cochange_count``
            # are passed as raw args; the branch logic lives INSIDE the
            # closure. W978 5th-discipline (cmd_taint W607-CJ): no
            # ``len()`` at kwarg-bind site.
            def _score_classify_run(_total, _cochange_count):
                if _total > 0:
                    _state = "HIDDEN_COUPLINGS_FOUND"
                elif _cochange_count == 0:
                    _state = "NO_COCHANGE_HISTORY"
                else:
                    _state = "NO_COUPLINGS"
                return {"state": _state, "scanned": _total}

            _score_dict = _run_check_cz(
                "score_classify",
                _score_classify_run,
                total,
                cochange_count,
                default={"state": "DEGRADED", "scanned": 0},
            )

            # W607-CZ -- compute_verdict boundary. Wraps the verdict-string
            # assembly so a downstream f-string refactor (non-int total
            # from a vocabulary refactor, or a __format__-raising
            # sentinel) surfaces a marker rather than crashing the
            # envelope. Floor must NOT re-interpolate the same values that
            # tripped the closure (W978 first-hypothesis discipline). Use
            # the literal "dark-matter completed" floor (LAW 6 still
            # holds: the line works standalone).
            #
            # W978 KWARG-DEFAULT EAGERNESS TRAP: ``total`` / ``cochange_count``
            # are passed as raw args; conditional branching lives INSIDE
            # the closure (cmd_taint W607-CJ 5th-discipline anchor).
            def _build_verdict_str(_total, _cochange_count):
                if _total > 0:
                    return f"{_total} dark-matter coupling{'s' if _total != 1 else ''} found"
                if _cochange_count == 0:
                    return (
                        "no co-change history to analyze (corpus has 0 "
                        "cochange records — run `roam index --force` to populate)"
                    )
                return "0 dark-matter couplings found"

            verdict_str = _run_check_cz(
                "compute_verdict",
                _build_verdict_str,
                total,
                cochange_count,
                default="dark-matter completed",
            )
            _summary: dict = {
                "verdict": verdict_str,
                "total_dark_matter_edges": total,
                "by_category": dict(by_cat),
                # W641-followup-G — canonical W631 risk-LEVEL + integer rank.
                # Projected from total_pairs + max cochange-strength via
                # ``_dark_matter_risk_level`` (Pattern-3a structural close-out,
                # seventh axis after W641 + followup-A/B/C/D/E).
                "risk_level_canonical": risk_level_canonical,
                "risk_rank": risk_rank_int,
                # W607-CZ: surface score_classify result on the envelope
                # so consumers can read the run state without re-deriving
                # from raw counts. W978 7th-discipline anchor: bare
                # ``_score_dict["state"]`` lookup (floor dict guarantees
                # the key) -- NOT ``.get("state", expensive_default)``.
                "run_state": _score_dict["state"],
            }
            if total == 0 and cochange_count == 0:
                _summary["partial_success"] = True
                _summary["state"] = "no_cochange"
            elif total > 0 and parts:
                _summary["verdict"] += f" ({', '.join(parts)})"

            # Verdict augmentation: append the canonical bucket so LAW 6
            # standalone-parse holds — an agent reading just the verdict line
            # can call ``risk_rank`` on the parenthesised token without
            # consulting any other envelope field. Mirrors the W641-followup-
            # A/B/C/D/E verdict-augmentation contract. The empty no-cochange
            # path also gets the suffix; state="no_cochange" disambiguates.
            _summary["verdict"] = f"{_summary['verdict']} (risk_level {risk_level_canonical})"

            # Surface Pattern-2 silent-fallback markers (unknown / negative
            # inputs from ``_dark_matter_risk_level``) PLUS W607-BK
            # substrate-call markers PLUS W607-CZ aggregation-phase
            # markers. All three marker families share the
            # ``dark_matter_*`` prefix and coexist on the summary's
            # ``warnings_out`` field. Empty combined list omitted to keep
            # the envelope tight (byte-identical happy path).
            _combined_warnings = list(_dm_warnings_out) + list(_w607bk_warnings_out) + list(_w607cz_warnings_out)
            if _combined_warnings:
                _summary["warnings_out"] = list(_combined_warnings)
                _summary["partial_success"] = True

            _envelope_kwargs: dict = {
                "summary": _summary,
                "budget": token_budget,
                # W641-followup-G — top-level mirror of
                # summary.risk_level_canonical / summary.risk_rank so
                # consumers reading the envelope head without
                # descending into ``summary`` see the canonical
                # bucket too (parity with cmd_impact / cmd_critique /
                # cmd_attest / cmd_diff).
                "risk_level_canonical": risk_level_canonical,
                "risk_rank": risk_rank_int,
                "dark_matter_pairs": [
                    {
                        "file_a": p["path_a"],
                        "file_b": p["path_b"],
                        "npmi": p["npmi"],
                        "lift": p["lift"],
                        "strength": p["strength"],
                        "cochange_count": p["cochange_count"],
                        "hypothesis": p.get("hypothesis"),
                        # ADDITIVE tag from roam.graph.coupling_patterns
                        # (expected_locale / expected_doc_hub / None).
                        # Tagged pairs still count toward every summary
                        # metric — annotation only, never subtraction.
                        "expected_pattern": p.get("expected_pattern"),
                    }
                    for p in pairs
                ],
            }
            # W607-BK / W607-CZ: top-level warnings_out mirror (preserved-
            # list-field discipline -- consumers reading the envelope head
            # without descending into ``summary`` see the substrate +
            # aggregation markers too).
            if _combined_warnings:
                _envelope_kwargs["warnings_out"] = list(_combined_warnings)

            # W607-CZ -- serialize_envelope boundary. Wraps the envelope
            # serialization itself. A downstream schema-shape refactor
            # that breaks ``json_envelope("dark-matter", ...)`` would
            # otherwise crash AFTER all substrate + aggregation signals
            # were already gathered. Floor to a minimal envelope stub so
            # consumers still receive a parseable JSON object with the
            # marker attached + the canonical command name. Mirror of
            # cmd_postmortem's W607-CV / cmd_taint's W607-CJ /
            # cmd_audit_trail_export's W607-CR serialize_envelope floor
            # pattern.
            _envelope_floor: dict = {
                "command": "dark-matter",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": verdict_str,
                    "partial_success": True,
                    "warnings_out": list(_combined_warnings),
                },
                "warnings_out": list(_combined_warnings),
            }
            _envelope = _run_check_cz(
                "serialize_envelope",
                json_envelope,
                "dark-matter",
                default=_envelope_floor,
                **_envelope_kwargs,
            )
            # W607-CZ -- if ``serialize_envelope`` raised AFTER the
            # combined bucket was already snapshotted, the new
            # ``dark_matter_serialize_envelope_failed:`` marker was
            # appended to ``_w607cz_warnings_out`` and the floor stub
            # carries only the pre-raise combined list. Rebuild the
            # floor stub's warnings_out so the new marker reaches the
            # JSON output. Clean path -> envelope is the real
            # json_envelope return value, no rebuild needed.
            if _envelope is _envelope_floor and _w607cz_warnings_out:
                _combined_warnings = list(_dm_warnings_out) + list(_w607bk_warnings_out) + list(_w607cz_warnings_out)
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings)
                _envelope_floor["warnings_out"] = list(_combined_warnings)
                _envelope = _envelope_floor

            click.echo(to_json(_envelope))
            return

        total = len(pairs)

        # W641-followup-G — surface canonical W631 risk-LEVEL on the text
        # VERDICT line too (parity with the JSON envelope augmentation +
        # cmd_diff text-mode polarity). Projected from total_pairs + max
        # cochange-strength so reviewers reading the terminal output see
        # the same closed-enum token as the JSON envelope.
        _dm_text_max_strength = 0.0
        for _p in pairs:
            _s = _p.get("strength") or 0.0
            try:
                _sf = float(_s)
            except (TypeError, ValueError):
                _sf = 0.0
            if _sf > _dm_text_max_strength:
                _dm_text_max_strength = _sf
        _dm_text_level = _dark_matter_risk_level(total, _dm_text_max_strength)
        risk_level_canonical_text = normalize_risk_level(_dm_text_level) or "low"

        if not pairs:
            # W805 (Pattern 2 propagation to text branch): the JSON branch
            # above already distinguishes "0 pairs from a populated
            # co-change graph" from "no co-change history to analyze".
            # Mirror that disclosure on the text branch so agents reading
            # the verdict line alone get the same lineage signal.
            #
            # W607-BK: route the probe through the same ``_run_check_bk``
            # accumulator as the JSON branch so any sqlite-layer raise
            # surfaces a marker rather than silently degrading to the
            # "no cochange history" verdict (Pattern-2 fix). Text mode
            # does not surface ``warnings_out`` on stdout, but the
            # accumulator entry is still meaningful when the same command
            # runs through MCP wrappers that re-serialise as JSON.
            def _query_cochange_count_text():
                return conn.execute("SELECT COUNT(*) FROM git_cochange").fetchone()[0]

            _cc_text = _run_check_bk(
                "query_cochange_count",
                _query_cochange_count_text,
                default=0,
            )
            cochange_count = _cc_text if _cc_text is not None else 0
            if cochange_count == 0:
                click.echo(
                    "VERDICT: no co-change history to analyze "
                    "(corpus has 0 cochange records — "
                    "run `roam index --force` to populate) "
                    f"(risk_level {risk_level_canonical_text})"
                )
            else:
                click.echo(f"VERDICT: 0 dark-matter couplings found (risk_level {risk_level_canonical_text})")
            return

        # Build verdict with category breakdown if hypotheses available
        if need_hypotheses:
            by_cat = Counter()
            for p in pairs:
                cat = p.get("hypothesis", {}).get("category", "UNKNOWN")
                by_cat[cat] += 1
            parts = [f"{v} {k}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1])]
            click.echo(
                f"VERDICT: {total} dark-matter coupling{'s' if total != 1 else ''} found "
                f"({', '.join(parts)}) (risk_level {risk_level_canonical_text})"
            )
        else:
            click.echo(
                f"VERDICT: {total} dark-matter coupling{'s' if total != 1 else ''} found "
                f"(risk_level {risk_level_canonical_text})"
            )

        click.echo()

        if category:
            # Group by hypothesis category
            groups: dict[str, list[dict]] = {}
            for p in pairs:
                cat = p.get("hypothesis", {}).get("category", "UNKNOWN")
                groups.setdefault(cat, []).append(p)
            for cat in sorted(groups.keys()):
                click.echo(f"  [{cat}]")
                for p in groups[cat]:
                    detail = p.get("hypothesis", {}).get("detail", "")
                    click.echo(f"    {p['path_a']} <-> {p['path_b']}")
                    click.echo(
                        f"      NPMI: {p['npmi']:.2f} | Lift: {p['lift']:.1f} | Co-changes: {p['cochange_count']}"
                    )
                    if detail:
                        click.echo(f"      Hypothesis: {cat} ({detail})")
                click.echo()
        else:
            for p in pairs:
                click.echo(f"  {p['path_a']} <-> {p['path_b']}")
                click.echo(f"    NPMI: {p['npmi']:.2f} | Lift: {p['lift']:.1f} | Co-changes: {p['cochange_count']}")
                if explain:
                    hyp = p.get("hypothesis", {})
                    cat = hyp.get("category", "UNKNOWN")
                    detail = hyp.get("detail", "")
                    click.echo(f"    Hypothesis: {cat} ({detail})")
                click.echo()


# Back-compat module attribute used by the lazy CLI registry.
dark_matter = hidden_coupling_cmd
