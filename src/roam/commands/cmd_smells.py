"""Detect and report code smells across the codebase."""

from __future__ import annotations

import json as _json
import sqlite3
from collections import Counter

import click

from roam.capability import roam_capability

# W941 -- import roam.catalog.smells at module load so its @detector
# decorators populate the registry BEFORE we call
# ``_registry_kind_to_confidence()`` to derive ``_SMELL_KIND_TO_CONFIDENCE``
# below. The import is side-effect-only (only the decorators matter here);
# ``run_all_detectors`` is still re-imported lazily inside the command body
# to preserve the existing cold-import shape.
from roam.catalog import smells as _smells_module_for_registry  # noqa: F401
from roam.catalog.registry import kind_to_confidence as _registry_kind_to_confidence
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output._severity import severity_rank, severity_to_confidence_level
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import (
    format_table,
    json_envelope,
    strip_list_payloads,
    to_json,
)

# W109 (W93 follow-up): smells is the fourth detector migrating onto the
# central findings registry (after ``clones`` in W95, ``dead`` in W99,
# ``complexity`` in W102). The shape mirrors those three — a stable
# detector version stamp and a deterministic ``finding_id_str`` so
# re-runs upsert instead of duplicating rows. Bump this when any
# detector in roam.catalog.smells changes its predicate / threshold /
# claim shape meaningfully.
#
# 1.3.0 -> 1.4.0 (W647): detect_temporal_coupling now ALSO emits
# ``temporal-coupling-cluster`` findings rolled up by symbol. The pair
# findings are unchanged; the new cluster rows are additive.
# 1.4.0 -> 1.5.0 (W1280): detect_feature_envy predicate tightened
# (test/orchestrator exclusion + single-dominant-foreign-file
# concentration gate). Far fewer rows; claim shape narrowed.
# 1.5.0 -> 1.6.0 (W1287): detect_shotgun_surgery re-implemented from a pure
# in_degree>7 predicate (~100% FP — measured inbound-popularity, the wrong
# axis) onto a conservative distinct-non-test-caller-FILE scatter metric
# (>=12 caller files; Fowler's file-scatter axis). Predicate + claim shape
# changed; ~1472 -> ~27 rows on roam-code.
SMELLS_DETECTOR_VERSION: str = "1.6.0"

# W370c: per-smell-kind version stamps for the 2 detectors landing in this
# wave. The composite SMELLS_DETECTOR_VERSION bumps 1.0.0 -> 1.1.0 to mark
# that two previously-stub kinds now emit. Individual stamps live alongside
# the composite per the CLAUDE.md "Version stamps" rule (call-site, not
# src/roam/catalog/versions.py which is task_id-keyed).
REFUSED_BEQUEST_DETECTOR_VERSION: str = "1.0.0"
PRIMITIVE_OBSESSION_DETECTOR_VERSION: str = "1.0.0"

# W603 / W604: per-kind version stamps for the two new AST-based detectors.
# magic-numbers + boolean-parameter walk every Python file's AST rather than
# querying the DB; same call-site discipline as the W370c stamps above.
MAGIC_NUMBERS_DETECTOR_VERSION: str = "1.0.0"
BOOLEAN_PARAMETER_DETECTOR_VERSION: str = "1.0.0"

# W601 / W602: per-kind version stamps for the second-wave detectors.
# switch-statement is AST-walk Python-only; temporal-coupling is a pure
# SQL join over git_cochange + edges. Same call-site discipline.
SWITCH_STATEMENT_DETECTOR_VERSION: str = "1.0.0"
# W647: rollup adds ``temporal-coupling-cluster`` findings on top of the
# pair findings. Claim shape extended -- bump from 1.0.0 to 1.1.0.
TEMPORAL_COUPLING_DETECTOR_VERSION: str = "1.1.0"

# W605: comment-density (TODO/FIXME/XXX/HACK marker rate per file). Pure
# line-scan over indexed source files. Same call-site discipline as the
# W601/W602/W603/W604 stamps above.
COMMENT_DENSITY_DETECTOR_VERSION: str = "1.0.0"

# W1280: feature-envy predicate tightened from a pure cross-file edge ratio
# to test/orchestrator exclusion + single-dominant-foreign-file concentration
# (~88% FP measured pre-fix). Predicate changed -> bump 1.0.0 -> 1.1.0,
# mirroring the dangerous-eval 1.1.0 precedent. Same call-site discipline.
FEATURE_ENVY_DETECTOR_VERSION: str = "1.1.0"

# W1287: shotgun-surgery re-implemented from in_degree>7 (inbound popularity,
# ~100% FP, the wrong axis) onto a conservative distinct-non-test-caller-FILE
# scatter metric (Fowler's file-scatter axis). Predicate + metric semantics
# changed -> bump 1.0.0 -> 1.1.0. Same call-site discipline as W1280.
SHOTGUN_SURGERY_DETECTOR_VERSION: str = "1.1.0"


# W1269 (sibling of W1256): lookup table consumed by
# ``_emit_smells_findings`` so each finding row stamps its smell kind's own
# version. Unknown kinds (e.g. brain-method, deep-nesting, long-params —
# the detectors without a per-id constant declared above) fall back to the
# composite ``SMELLS_DETECTOR_VERSION`` passed in by the caller. The
# fallback path keeps the W870 parity-lint contract: every registered
# ``smell_id`` always has *some* version source.
_SMELL_KIND_TO_VERSION: dict[str, str] = {
    "refused-bequest": REFUSED_BEQUEST_DETECTOR_VERSION,
    "primitive-obsession": PRIMITIVE_OBSESSION_DETECTOR_VERSION,
    "magic-numbers": MAGIC_NUMBERS_DETECTOR_VERSION,
    "boolean-parameter": BOOLEAN_PARAMETER_DETECTOR_VERSION,
    "switch-statement": SWITCH_STATEMENT_DETECTOR_VERSION,
    "temporal-coupling": TEMPORAL_COUPLING_DETECTOR_VERSION,
    "comment-density": COMMENT_DENSITY_DETECTOR_VERSION,
    "feature-envy": FEATURE_ENVY_DETECTOR_VERSION,
    "shotgun-surgery": SHOTGUN_SURGERY_DETECTOR_VERSION,
}


# W109 — per-smell-kind confidence tier mapping.
#
# W941 + W948 -- derived view: the single source of truth is the @detector
# decorator in ``roam.catalog.smells`` (each call carries the
# ``confidence=`` kwarg AND any ``rollup_kinds={...}`` mapping). The per-
# tier "WHY this tier" rationale also lives inline at each registration
# site, so adding/changing a tier is a one-place edit. See the
# registry-pattern memo ``(internal memo)`` for
# the broader registry story. Tier semantics: see ``roam.db.findings`` for
# the canonical confidence constants (``static_analysis`` / ``structural`` /
# ``heuristic`` / ``runtime``).
_SMELL_KIND_TO_CONFIDENCE: dict[str, str] = _registry_kind_to_confidence()
_SMELL_DEFAULT_CONFIDENCE: str = "heuristic"


def _smell_finding_id(smell_id: str, file_path: str, symbol_name: str, line_start: int | None) -> str:
    """Stable, deterministic finding id for one smell hit.

    The (smell_id, file_path, symbol_name, line_start) tuple re-identifies
    the same hit across runs. We fold file_path + line_start into the
    digest so a renamed symbol at the same source location upserts
    rather than duplicates — and a relocated symbol with the same name
    correctly gets a fresh id (so an obsolete prior row stays in place
    until the next persist clears it via upsert miss).
    """
    from roam.db.findings import make_finding_id

    return make_finding_id("smells", smell_id, smell_id, file_path, symbol_name, int(line_start or 0))


def _resolve_smell_subject_id(
    conn: sqlite3.Connection,
    file_path: str,
    symbol_name: str,
    line_start: int | None,
) -> int | None:
    """Best-effort lookup of ``symbols.id`` for a (file, name, line) triple.

    Mirrors ``clone_detect._resolve_symbol_id`` — same fallback ladder
    (exact match by line_start, then nearest-line by name). Returns
    ``None`` when nothing matches; the findings registry permits NULL
    subject_id by design (file/edge/commit findings).
    """
    try:
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.name = ? AND s.line_start = ? "
            "LIMIT 1",
            (file_path, symbol_name, line_start),
        ).fetchone()
        if row is not None:
            return int(row[0])
        # Fallback: name-only, nearest line. Tree-sitter occasionally
        # disagrees with the indexer about the exact line_start of a
        # decorated function — accept the closest match before
        # giving up.
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.name = ? "
            "ORDER BY ABS(COALESCE(s.line_start, 0) - ?) "
            "LIMIT 1",
            (file_path, symbol_name, line_start or 0),
        ).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.OperationalError:
        # Pre-W89 schema or symbols table absent — fall back to NULL.
        return None


def _emit_smells_findings(
    conn: sqlite3.Connection,
    findings_data: list[dict],
    source_version: str,
) -> int:
    """Mirror each smell finding into the central findings registry.

    Returns the count of finding rows written. Caller is responsible
    for opening ``conn`` writable; emit_finding does not commit
    (the caller commits once at the end of the persist branch).

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard smells command path.

    W1269 (sibling of W1256): each finding row stamps the per-kind
    detector version (``_SMELL_KIND_TO_VERSION[smell_id]``) rather than
    the composite. The ``source_version`` parameter is retained as the
    fallback for kinds without a per-id constant (brain-method,
    deep-nesting, long-params, etc.) so future detectors land here
    cleanly without a parallel edit to the lookup table.
    """
    # Local import keeps the cost out of the read-only path —
    # callers without --persist never reach here.
    from roam.db.findings import FindingRecord, emit_finding

    written = 0
    for f in findings_data:
        smell_id = f.get("smell_id") or "unknown"
        symbol_name = f.get("symbol_name") or ""
        kind = f.get("kind") or ""
        location = f.get("location") or ""
        file_path = location.split(":", 1)[0] if location else ""
        # `location` is `path:line`. Re-derive line_start from there
        # because the smells finding dict doesn't carry a separate
        # line_start field (only the formatted string).
        line_start: int | None = None
        if location and ":" in location:
            try:
                line_start = int(location.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                line_start = None

        subject_id = _resolve_smell_subject_id(conn, file_path, symbol_name, line_start)
        finding_id = _smell_finding_id(smell_id, file_path, symbol_name, line_start)
        evidence = {
            "smell_id": smell_id,
            "severity": f.get("severity"),
            "symbol_name": symbol_name,
            "kind": kind,
            "file_path": file_path,
            "line_start": line_start,
            "location": location,
            "metric_value": f.get("metric_value"),
            "threshold": f.get("threshold"),
            "description": f.get("description"),
        }
        claim = f"{smell_id}: {symbol_name} ({location}) — {f.get('description') or smell_id}"
        confidence = _SMELL_KIND_TO_CONFIDENCE.get(smell_id, _SMELL_DEFAULT_CONFIDENCE)
        # W1269: per-kind version stamp; falls back to the composite
        # ``source_version`` for any kind not yet in _SMELL_KIND_TO_VERSION
        # (defensive forward-compat — same shape as cmd_vibe_check W1256).
        kind_version = _SMELL_KIND_TO_VERSION.get(smell_id, source_version)
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="symbol" if subject_id is not None else "file",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=confidence,
                source_detector="smells",
                source_version=kind_version,
            ),
        )
        written += 1
    return written


# R22 — confidence-derivation rule for smells:
#   critical-severity smells (brain-method, god-class, large-class) have
#     low FP rate and are structural → "high".
#   warning-severity smells (deep-nesting, long-params, feature-envy,
#     shotgun-surgery, low-cohesion) are heuristic but well-validated →
#     "medium".
#   info-severity smells (data-clumps, dead-params, message-chain,
#     placeholders) are exploratory and prone to FP → "low".
#
# W565 — projection moved to ``severity_to_confidence_level``. The
# canonical default table maps critical -> "high", warning -> "medium",
# info -> "low" — byte-identical to the pre-W565 table here. Other
# severity labels (which smells never emits today, but which the
# defensive ``.get(..., "low")`` covered) collapse to "low" via the
# helper's CI-safe default.


def _smell_classify(finding: dict) -> tuple[str, str]:
    """Map a smell finding to a (confidence, reason) tuple.

    Reason names the signal used: severity label + metric vs threshold.
    """
    sev = (finding.get("severity") or "info").lower()
    conf = severity_to_confidence_level(sev)
    metric = finding.get("metric_value")
    threshold = finding.get("threshold")
    smell_id = finding.get("smell_id", "unknown")
    if metric is not None and threshold is not None:
        reason = f"{smell_id}: {sev} severity; metric={metric} vs threshold={threshold}"
    else:
        reason = f"{smell_id}: {sev} severity"
    return conf, reason


# W564 + W1005: severity ordering sourced from roam.output._severity.severity_rank
# (canonical, higher = worse). Local ``_VALID_SEVERITIES`` enumeration is
# the closed 3-tier vocab this detector EMITS. The ``--min-severity`` CLI
# Choice (above) is intentionally WIDER — it accepts the full W547 canonical
# 5-tier (critical/error/high/warning/medium/low/info) so agents can pass
# any of those tokens and have severity_rank() do the canonical comparison.
# A user-passed ``--min-severity high`` will accept only ``critical`` findings
# because the emitted set tops out at ``critical`` (rank 5) and ``high`` ranks 4.
_VALID_SEVERITIES = frozenset({"critical", "warning", "info"})


# W987 (Pattern 2 four-anchor — closed-set vocabulary):
# the ``--kind`` filter on this command and the ``kind:`` field in
# ``.roam/smells.suppress.yml`` BOTH key on the same closed set of
# registered smell ids. This helper returns the canonical set so both
# call sites validate against one source of truth. The set is derived
# lazily from the registry on each call (no module-level cache) so
# plugin-registered detectors land here without a stale snapshot.
def _registered_smell_kinds() -> frozenset[str]:
    """Return the closed set of registered smell ids + rollup ids (W987).

    Lazy derivation from ``roam.catalog.registry.kind_to_confidence`` so
    plugin-registered detectors land in the closed set without requiring
    a cache invalidation. Covers both top-level detector ids AND rollup
    ids (e.g. ``temporal-coupling-cluster``) — anything a finding's
    ``smell_id`` can legitimately be.
    """
    return frozenset(_SMELL_KIND_TO_CONFIDENCE.keys())


def _file_role_lookup(conn) -> dict[str, str]:
    """Return a {path: file_role} map for tooling-exclusion filtering.

    Uses the canonical ``files.file_role`` column populated at index
    time. Falls back to an empty dict if the schema doesn't have that
    column (very old indexes pre-v9).
    """
    try:
        rows = conn.execute("SELECT path, file_role FROM files").fetchall()
    except Exception:
        return {}
    return {
        (r["path"] if hasattr(r, "keys") else r[0]): (r["file_role"] if hasattr(r, "keys") else r[1]) or ""
        for r in rows
    }


def _short_loc(location: str) -> str:
    """Render a location string compact: ``last/two/segments.py:line``.

    Empty input → empty output. Lines without ``:line`` survive."""
    if not location:
        return ""
    norm = location.replace("\\", "/")
    parts = norm.split("/")
    tail = "/".join(parts[-2:]) if len(parts) >= 2 else norm
    return tail


@roam_capability(
    name="smells",
    category="health",
    summary="Detect code smells: brain methods, god classes, deep nesting.",
    inputs=["repo_path"],
    outputs=["smells", "verdict"],
    examples=[
        "roam smells",
        "roam smells --min-severity warning",
        "roam smells --file src/auth.py",
    ],
    tags=["health", "smells"],
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
    "--file",
    "file_path",
    default=None,
    type=click.Path(),
    help="Filter smells to a specific file path",
)
@click.option(
    "--min-severity",
    default=None,
    type=click.Choice(
        # W1005: widened from 3-tier {critical, warning, info} to W547 canonical
        # 5-tier so agents can pass any of {critical, error, high, warning,
        # medium, low, info} and have it compared via ``severity_rank()`` from
        # ``roam.output._severity``. The smell detectors currently emit only
        # {critical, warning, info} (the SARIF-aligned mid-vocab), but the W547
        # rank table accepts CVSS terms (``high``, ``medium``, ``low``) as
        # equivalents under the canonical ordering (higher = worse). Aliases
        # like ``note`` / ``unknown`` are intentionally NOT in the Choice — they
        # collapse to ``info`` / sort-below-info via ``severity_rank``, so a
        # user-facing filter on them would be confusing.
        ["critical", "error", "high", "warning", "medium", "low", "info"],
        case_sensitive=False,
    ),
    help=(
        "Minimum severity to include. Uses the canonical W547 5-tier ordering "
        "(critical > error == high > warning > medium > low > info). Detectors "
        "emit critical/warning/info today; CVSS aliases (high/medium/low) and "
        "SARIF error rank via the same severity_rank() comparator."
    ),
)
@click.option(
    "--include-tooling",
    is_flag=True,
    default=False,
    help=(
        "Include CI scripts, build scripts, dev tooling, and generated "
        "files in the smell count. Excluded by default because high "
        "complexity in one-shot scripts and codegen output is expected "
        "and uninteresting — surfacing them dominates the headline number."
    ),
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist findings to .roam/index.db findings registry "
        "(cross-detector queryable via `roam findings list --detector smells`). "
        "The detector-specific output is unchanged; the registry rows are "
        "the denormalised cross-detector surface. Persisted set ignores "
        "--file / --min-severity filters — every detector hit on the full "
        "codebase is mirrored so a downstream filter doesn't truncate the "
        "registry."
    ),
)
@click.option(
    "--no-suppress",
    is_flag=True,
    default=False,
    help=(
        "Ignore .roam/smells.suppress.yml entries — emit every detector hit "
        "unfiltered. Useful for auditing whether a previously-suppressed "
        "design choice still holds."
    ),
)
# W996: --kind vs --min-severity validation divergence (intentional).
# --kind uses run-time validation against roam.catalog.registry — the smell-id
# set is registry-derived and grows as detectors land between releases, so a
# click.Choice([...]) would break forward-compat with CI scripts whenever a
# new detector ships. Unknown --kind values therefore warn into
# summary.warnings_out and are skipped (graceful, exit 0).
# --min-severity uses click.Choice([...]) — a fixed closed enum widened in
# W1005 to the W547 canonical 5-tier vocab (critical / error / high /
# warning / medium / low / info). Unknown --min-severity raises a click
# usage error (exit 2). Same command, two different "unknown value"
# semantics by design: registry-derived vocabularies warn; fixed enums
# hard-fail at parse.
@click.option(
    "--kind",
    "kind_filter",
    multiple=True,
    default=(),
    metavar="SMELL_ID",
    help=(
        "Filter findings to one or more smell ids (repeat the flag for "
        "multiple). Unknown ids warn (LAW 2 imperative) and are skipped "
        "rather than raising — backward compat with existing CI scripts. "
        "Validated against the registered smell-detector set in "
        "roam.catalog.registry."
    ),
)
# W1294 (perf pushdown — second-cheapest win from PERF-CHARACTERIZATION-
# 2026-05-16): --only restricts the detector dispatch loop to the named
# smell_id set BEFORE running each detector's SQL/AST pass. Distinct from
# --kind which post-filters the finding list AFTER every detector ran
# (preserves --kind's registry-forward-compat warn semantics for CI
# scripts). --only is the work-skipping fast path: a typo here means we
# do zero work, so unknown ids hard-error at parse time (Constraint 8 —
# fixed-enum boundary at the dispatch layer). On roam-code itself
# `roam smells --only clones` is ~10x faster than full `roam smells`.
@click.option(
    "--only",
    "only_kinds",
    multiple=True,
    default=(),
    metavar="SMELL_ID",
    help=(
        "Run ONLY these smell detectors (repeat the flag for multiple). "
        "Skips the SQL/AST pass for every other detector. Unknown ids "
        "raise a usage error with the registered set listed. Differs from "
        "--kind, which post-filters findings after all detectors ran."
    ),
)
@click.pass_context
def smells(ctx, file_path, min_severity, include_tooling, persist, no_suppress, kind_filter, only_kinds):
    """Detect code smells: brain methods, god classes, deep nesting, and more.

    Unlike ``vibe-check`` (which detects AI-generated code anti-patterns via
    source-file regex) and ``health`` (which gives an aggregate codebase
    score), this command runs 24 deterministic DB-query-based structural smell
    detectors: brain methods, god classes, deep nesting, shotgun surgery,
    excessive parameters, switch statements, temporal coupling, and more.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    detail = ctx.obj.get("detail", False) if ctx.obj else False
    ensure_index()

    # W607-BN -- substrate-CALL marker plumbing on the 24-detector smells
    # aggregator. cmd_smells is the highest-impact detector surface
    # (3047 findings rows on roam-code per W607-BJ note) and a key
    # Pattern-2 case-study target (W987 + W1063 follow-ups).
    #
    # Substrate boundaries wrapped:
    #
    #   * load_suppress_rules        -- .roam/smells.suppress.yml loader
    #   * query_findings_corpus      -- run_all_detectors dispatch loop
    #   * apply_suppressions         -- typed-suppression applier
    #   * apply_kind_filter          -- --kind closed-set filter (W987 Pattern-2)
    #   * apply_min_severity_filter  -- W547/W1005 5-tier severity rank filter
    #   * apply_tooling_filter       -- file_role + path-hint tooling exclusion
    #   * aggregate_by_kind          -- Counter aggregation over 24 detectors
    #   * classify_severity          -- wrap_findings + confidence_distribution
    #   * serialize_to_sarif         -- centralized SARIF projection
    #   * emit_findings              -- W109 findings-registry mirror
    #
    # Marker family ``smells_<phase>_failed:<exc_class>:<detail>``.
    # Empty bucket -> byte-identical envelope on the happy path. Threads
    # into the existing W987 ``warnings_list`` accumulator (preserved-
    # list-field discipline) AND ``summary.partial_success=True`` on the
    # degraded path. Coexists with the prior W987 Pattern-2 + W1063
    # Pattern-1D plumbing -- the W607-BN bucket is purely additive.
    _w607bn_warnings_out: list[str] = []

    def _run_check_bn(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BN marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``smells_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607bn_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bn_warnings_out.append(f"smells_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- W607-DF: aggregation-phase marker plumbing (additive) -----------
    # cmd_smells is the smell-pattern axis of the structural-debt paired-
    # scoring 4-way (W805: clones BQ/DC, duplicates BM/DD, smells BN/DF,
    # dark_matter BK/CZ). W607-BN (above) plumbed the substrate-CALL layer
    # (10 boundaries: load_suppress_rules / query_findings_corpus /
    # apply_suppressions / apply_kind_filter / apply_min_severity_filter /
    # apply_tooling_filter / aggregate_by_kind / classify_severity /
    # serialize_to_sarif / emit_findings). W607-DF adds the
    # AGGREGATION-PHASE layer on top:
    #
    #   score_classify       -- bucket the run state (CLEAN / NEEDS_REFACTORING
    #                            / FAIR / GOOD) from total + severity_counts
    #                            so consumers can read the run classification
    #                            without re-deriving from raw counts.
    #   compute_predicate    -- extract smells rollup metrics
    #                            (critical_count / warning_count / info_count /
    #                            files_affected / smell_types_count).
    #   compute_verdict      -- composite verdict-string assembly with the
    #                            confidence-distribution high count suffix.
    #   serialize_envelope   -- json_envelope("smells", ...) projection.
    #
    # Marker family ``smells_*`` -- SAME family as W607-BN (additive, not a
    # separate prefix). Empty bucket -> byte-identical envelope on the
    # success path. Two buckets (``_w607bn_warnings_out`` substrate-CALL +
    # ``_w607df_warnings_out`` aggregation-phase) are combined with the
    # pre-existing W987 ``warnings_list`` at envelope-emit time so
    # consumers see the full degradation lineage in marker-emission order.
    # The additive bucket stays distinguishable via its phase names
    # (``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
    # ``serialize_envelope``).
    #
    # STRUCTURAL-DEBT PAIRED-SCORING 4-WAY pairing analogue -- pattern
    # reused here for the smell-pattern axis. After W607-DF lands, ALL
    # FOUR members of the 4-way carry an aggregation-phase layer
    # (closing the structural-debt 4-way at the agg-layer):
    #   cmd_clones        (W607-BQ substrate + DC aggregation) -- AST-similarity axis
    #   cmd_duplicates    (W607-BM substrate + DD aggregation) -- token-similarity axis
    #   cmd_smells        (W607-BN substrate + DF THIS)        -- smell-pattern axis
    #   cmd_dark_matter   (W607-BK substrate + CZ aggregation) -- co-change axis
    #
    # W978 7-DISCIPLINE: every ``default=`` kwarg in a ``_run_check_df(...)``
    # call MUST be a literal constant (not a computed expression like
    # ``len(findings) if ...``). cmd_sbom W607-CG sealed this axis;
    # cmd_taint W607-CJ added the 5th discipline (move ``len()`` INSIDE
    # the closure, not at the kwarg-bind site); cmd_audit_trail_export
    # W607-CR added the 7th discipline (use bare ``dict[key]`` lookup when
    # the floor dict guarantees the key, NOT
    # ``dict.get(key, expensive_default)`` which evaluates default eagerly).
    #
    # W607-BN/DF PHASE-NAME COLLISION (W607-CH 4th-discipline): the
    # substrate-CALL layer uses phase names load_suppress_rules /
    # query_findings_corpus / apply_suppressions / apply_kind_filter /
    # apply_min_severity_filter / apply_tooling_filter / aggregate_by_kind
    # / classify_severity / serialize_to_sarif / emit_findings. None
    # collide with score_classify / compute_predicate / compute_verdict /
    # serialize_envelope, so no rename is required. ``serialize_to_sarif``
    # vs ``serialize_envelope`` are deliberately distinct phase names so
    # an agent can tell which serialiser raised.
    _w607df_warnings_out: list[str] = []

    def _run_check_df(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-DF marker emission.

        Mirror of ``_run_check_bn`` shape (same ``smells_<phase>_failed:``
        marker family) but writes into ``_w607df_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607df_warnings_out.append(f"smells_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    from roam.catalog.smells import ALL_DETECTORS, run_all_detectors

    # W1294 (perf pushdown): validate --only against the dispatchable
    # detector set (the smell_ids backed by a detect_fn in ALL_DETECTORS).
    # Rollup smell_ids like ``temporal-coupling-cluster`` are emitted as a
    # side-effect of their parent detector and therefore not directly
    # dispatchable — they correctly fall outside this closed enum. Unknown
    # ids hard-error (Constraint 8 fixed-enum boundary) because --only
    # gates which work runs; a typo means we did zero work.
    if only_kinds:
        dispatchable_kinds = frozenset(smell_id for smell_id, _fn in ALL_DETECTORS)
        unknown_only = sorted({k for k in only_kinds if k and k not in dispatchable_kinds})
        if unknown_only:
            valid_listing = ", ".join(sorted(dispatchable_kinds))
            raise click.UsageError(
                f"--only: unknown smell id(s): {', '.join(unknown_only)}. Valid ids: {valid_listing}"
            )
        only_dispatch: frozenset[str] | None = frozenset(k for k in only_kinds if k)
    else:
        only_dispatch = None

    # W658: load the smells suppression allowlist exactly once per run.
    # The substrate keys on (smell_id + symbol_name) so deliberate fan-in
    # hubs (e.g. registry dispatchers) can be allowlisted without
    # polluting the headline number. --no-suppress bypasses the file.
    #
    # W737 (Phase C-1b of W692): use the typed loader + typed applier so
    # the in-memory representation is the canonical
    # :class:`KindSymbolSuppression` dataclass rather than a raw dict.
    # Envelope bytes stay byte-identical to the dict-applier path — this
    # is a representation refactor, not a behavioural change. The legacy
    # dict-shaped helpers stay in-tree for back-compat tests (W724
    # Phase C-2 territory).
    from roam.commands.smells_suppress import (
        apply_suppressions_typed,
        load_smells_suppressions_typed,
    )
    from roam.db.connection import find_project_root

    try:
        project_root = find_project_root()
    except Exception:
        project_root = None

    # W987 (Pattern 1 — warnings_out plumb-through): single accumulator
    # threaded through every silent-fallback path. Surfaced on the envelope
    # under ``warnings_out`` and flips ``summary.partial_success=True`` when
    # non-empty. Default ``[]`` keeps the accumulator-always-on discipline
    # — every silent path the CLI reaches has somewhere to surface.
    warnings_list: list[str] = []

    # W607-BN: load_suppress_rules substrate boundary. A YAML parse error
    # or filesystem failure here should not collapse the smells run --
    # surface the marker and proceed with an empty suppression set so
    # detectors still emit their full findings.
    smells_suppressions = (
        []
        if no_suppress or project_root is None
        else _run_check_bn(
            "load_suppress_rules",
            load_smells_suppressions_typed,
            project_root,
            warnings_out=warnings_list,
            default=[],
        )
    )
    if smells_suppressions is None:
        smells_suppressions = []

    # W987 (Pattern 2 — closed-set vocabulary): validate --kind against
    # the registered smell-detector set. Unknown ids surface as an
    # actionable warning AND are dropped from the filter set rather than
    # raising. If every requested kind is invalid, the filter resolves to
    # zero findings rather than widening to "all smells" — a typo must not
    # explode the result set.
    #
    # W1083-followup-3: partition + per-unknown ``did_you_mean`` +
    # warnings-string formatting is delegated to the shared
    # ``structured_unknown_filter_many`` helper. The local partition loop
    # is absorbed into the helper; the callsite now consumes
    # ``frag["valid_kinds"]`` (-> sanitised_kinds) and
    # ``frag["warnings_text"]`` (-> warnings_list.extend).
    known_kinds = _registered_smell_kinds()
    sanitised_kinds: set[str] = set()
    kind_filter_requested = any(bool(k) for k in kind_filter or ())
    _kind_frag: dict | None = None
    if kind_filter:
        from roam.output.structured_unknowns import (
            structured_unknown_filter_many,
            to_summary_payload_many,
        )

        _kind_frag = structured_unknown_filter_many(
            list(kind_filter),
            known_kinds,
            field_name="kind",
            fact_anchor="kinds",
            state="unknown_kinds",
        )
        sanitised_kinds = set(_kind_frag["valid_kinds"])
        # The helper emits a generic "Drop {k!r}: unknown <field> matches 0
        # entries" warning shape. We rewrite it to the W1066-canonical
        # "Drop --kind {k!r}: unknown smell id matches 0 detectors; pick
        # one of the {N} registered kinds" form so the migration is wire-
        # compatible with existing CI scripts that grep this string.
        if _kind_frag["unknown_kinds"]:
            import difflib

            known_kinds_sorted = sorted(known_kinds)
            for k in _kind_frag["unknown_kinds"]:
                base_msg = (
                    f"Drop --kind {k!r}: unknown smell id matches 0 detectors; "
                    f"pick one of the {len(known_kinds)} registered kinds"
                )
                close_matches = difflib.get_close_matches(k, known_kinds_sorted, n=2, cutoff=0.6)
                if close_matches:
                    quoted = " or ".join(f"'{m}'" for m in close_matches)
                    base_msg = f"{base_msg}. Did you mean: {quoted}?"
                warnings_list.append(base_msg)

    with open_db(readonly=not persist) as conn:
        # W607-BN: query_findings_corpus substrate boundary. A raise in
        # any of the 24 detector dispatches collapses run_all_detectors;
        # surface the marker and proceed with an empty corpus so the
        # envelope still composes cleanly with verdict + warnings_out.
        findings = _run_check_bn(
            "query_findings_corpus",
            run_all_detectors,
            conn,
            only=only_dispatch,
            default=[],
        )
        if findings is None:
            findings = []

        # W658: apply the suppress.yml allowlist BEFORE persist so
        # suppressed findings never enter the findings registry. The
        # suppressed list is held aside for envelope-level disclosure
        # (summary.suppressed_count + suppressed_smells[] in detail
        # mode) — Pattern-2 always-emit discipline says don't drop
        # signal silently.
        #
        # W607-BN: apply_suppressions substrate boundary.
        suppressed_smells: list[dict] = []
        if smells_suppressions:
            _suppress_result = _run_check_bn(
                "apply_suppressions",
                apply_suppressions_typed,
                findings,
                smells_suppressions,
                default=None,
            )
            if _suppress_result is not None:
                findings, suppressed_smells = _suppress_result

        # --- W109: mirror into the central findings registry ---
        # Runs ONLY with --persist. The persisted set is independent of the
        # --file / --min-severity display filters — we emit every detector
        # hit on the full codebase so the registry stays comprehensive
        # regardless of how a particular invocation slices the view. The
        # tooling-exclusion check below the persist block applies only to
        # the headline number; the registry is the cross-detector surface
        # and should carry the unfiltered set.
        if persist:
            # W607-BN: emit_findings substrate boundary. The pre-W89
            # schema path (sqlite3.OperationalError on missing
            # ``findings`` table) is the EXPECTED degraded path -- the
            # try/except below maintains the W109 silent no-op contract
            # for that case. Generic exceptions surface via the
            # ``smells_emit_findings_failed:<exc>:<detail>`` marker.
            try:
                _emit_smells_findings(conn, findings, SMELLS_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass
            except Exception as _emit_exc:  # noqa: BLE001 -- W607-BN disclosure
                _w607bn_warnings_out.append(f"smells_emit_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}")

        # Default: exclude tooling, generated, examples, vendor, workspaces,
        # docs. The headline number is dominated by paths the user didn't
        # write or doesn't want to refactor (``dev/``, ``.github/scripts/``,
        # ``examples/``, vendored packages, codegen output). The shared
        # path-hint set lives in ``roam.output.file_role_hints`` so all
        # headline commands stay in sync. ``--include-tooling`` opts back
        # into the full set.
        from roam.output.file_role_hints import is_excluded_path

        excluded_tooling = 0

        def _apply_tooling_filter_pass(items: list[dict]) -> tuple[list[dict], int]:
            """W607-BN extracted helper: tooling-exclusion pass.

            Returns ``(kept_findings, excluded_count)``. Pulled out so the
            ``apply_tooling_filter`` substrate-CALL boundary can surface
            a single ``smells_apply_tooling_filter_failed:<exc>`` marker
            when either ``_file_role_lookup`` or ``is_excluded_path``
            raises (degrades to the unfiltered list so the headline
            number still emits).
            """
            tooling_roles = {"ci", "scripts", "build", "generated"}
            tooling_roles_per_file = _file_role_lookup(conn)
            kept_local: list[dict] = []
            excluded = 0
            for ff in items:
                loc = (ff.get("location") or "").replace("\\", "/")
                file_path_only = loc.split(":", 1)[0] if loc else ""
                role = tooling_roles_per_file.get(file_path_only)
                if role in tooling_roles:
                    excluded += 1
                    continue
                if is_excluded_path(file_path_only):
                    excluded += 1
                    continue
                kept_local.append(ff)
            return kept_local, excluded

        if not include_tooling:
            _tooling_result = _run_check_bn(
                "apply_tooling_filter",
                _apply_tooling_filter_pass,
                findings,
                default=None,
            )
            if _tooling_result is not None:
                findings, excluded_tooling = _tooling_result

        # Filter by file
        if file_path:
            norm = file_path.replace("\\", "/")
            findings = [f for f in findings if norm in f.get("location", "").replace("\\", "/")]

        # W987/W1035 — filter by smell kind (closed-set validated above).
        # Known ids narrow the result set. Unknown-only filters resolve to
        # zero findings with a warning, instead of silently widening to all
        # findings.
        #
        # W607-BN + W1063 (Pattern-1D): apply_kind_filter substrate boundary.
        # A raise here would silently widen the result set on filter failure
        # (LAW 11: user intent > inference); surface the marker and degrade
        # to an empty set so the consumer sees the disclosure rather than a
        # full unfiltered result that looks like the filter "worked".
        def _apply_kind_filter_pass(items: list[dict]) -> list[dict]:
            """W607-BN extracted helper: --kind closed-set filter."""
            if sanitised_kinds:
                return [f for f in items if f.get("smell_id") in sanitised_kinds]
            if kind_filter_requested:
                return []
            return items

        if sanitised_kinds or kind_filter_requested:
            _kind_result = _run_check_bn(
                "apply_kind_filter",
                _apply_kind_filter_pass,
                findings,
                default=None,
            )
            # On failure, degrade to empty (NOT silent widen): preserves the
            # closed-set vocabulary semantic from W987/W1063 -- the consumer
            # sees the marker AND the empty set rather than an unexpectedly
            # full list.
            findings = [] if _kind_result is None else _kind_result

        # Filter by minimum severity (W564: canonical higher = worse).
        # W607-BN: apply_min_severity_filter substrate boundary.
        if min_severity:
            min_sev = min_severity.lower()

            def _apply_min_severity_pass(items: list[dict]) -> list[dict]:
                """W607-BN extracted helper: severity-rank filter."""
                floor = severity_rank(min_sev)
                return [f for f in items if severity_rank(f.get("severity", "info")) >= floor]

            _sev_result = _run_check_bn(
                "apply_min_severity_filter",
                _apply_min_severity_pass,
                findings,
                default=None,
            )
            if _sev_result is not None:
                findings = _sev_result

        # Compute summary stats
        # W607-BN: aggregate_by_kind substrate boundary -- the 24-detector
        # rollup. Counter() over the findings list is trivially safe, but
        # the substrate boundary discipline keeps the marker family aligned
        # with the canonical phase set.
        def _aggregate_by_kind_pass(items: list[dict]) -> tuple[int, Counter, Counter, int]:
            """W607-BN extracted helper: 24-detector summary aggregation."""
            total = len(items)
            sev_counts = Counter(f.get("severity", "info") for f in items)
            sk_types = Counter(f.get("smell_id", "unknown") for f in items)
            f_affected = len(set(f.get("location", "").split(":")[0] for f in items if f.get("location")))
            return total, sev_counts, sk_types, f_affected

        _agg_result = _run_check_bn(
            "aggregate_by_kind",
            _aggregate_by_kind_pass,
            findings,
            default=None,
        )
        if _agg_result is None:
            # Empty-floor fallback: Counter-like dict that survives even if
            # ``Counter`` itself is the substrate that raised (e.g. a
            # monkeypatched aggregator under test, or a future numpy/native
            # accelerator that replaces collections.Counter at import). The
            # downstream consumer reads via ``.get(k, default)`` which dicts
            # honour identically.
            total_smells = 0
            severity_counts = {}
            smell_types = {}
            files_affected = 0
        else:
            total_smells, severity_counts, smell_types, files_affected = _agg_result

        # W607-DF -- score_classify boundary. Wraps the run-state
        # bucketing (CLEAN / NEEDS_REFACTORING / FAIR / GOOD) into a
        # single state label so a downstream refactor of the state-
        # selection logic surfaces a marker rather than crashing. The
        # state label is surfaced on the envelope so consumers can
        # read it without re-deriving from raw counts. W978 5th-
        # discipline: ``len()`` lives INSIDE the closure (cmd_taint
        # W607-CJ anchor); raw counts are passed as positional args.
        def _score_classify_run(_total, _critical, _warning):
            if _total == 0:
                _state = "CLEAN"
            elif _critical > 0:
                _state = "NEEDS_REFACTORING"
            elif _warning > 0:
                _state = "FAIR"
            else:
                _state = "GOOD"
            return {"state": _state, "scanned": _total}

        critical = severity_counts.get("critical", 0)
        warning = severity_counts.get("warning", 0)

        _score_dict = _run_check_df(
            "score_classify",
            _score_classify_run,
            total_smells,
            critical,
            warning,
            default={"state": "DEGRADED", "scanned": 0},
        )

        # W607-DF -- compute_predicate boundary. Wraps the rollup-metrics
        # extraction (critical_count / warning_count / info_count /
        # files_affected / smell_types_count). A future refactor of the
        # severity_counts / smell_types shape would otherwise crash here.
        # Floor to documented zero counts matching the empty-bucket
        # branch shape so downstream verdict / serialize stay non-null.
        # W978 5th-discipline: ``len()`` lives INSIDE the closure.
        def _compute_predicate_fields(_sev_counts, _smell_types, _files_affected) -> dict:
            return {
                "critical_count": _sev_counts.get("critical", 0),
                "warning_count": _sev_counts.get("warning", 0),
                "info_count": _sev_counts.get("info", 0),
                "files_affected": _files_affected,
                "smell_types_count": len(_smell_types),
            }

        _pred_fields = _run_check_df(
            "compute_predicate",
            _compute_predicate_fields,
            severity_counts,
            smell_types,
            files_affected,
            default={
                "critical_count": 0,
                "warning_count": 0,
                "info_count": 0,
                "files_affected": 0,
                "smell_types_count": 0,
            },
        )

        # W607-DF -- compute_verdict boundary. Wraps the verdict-string
        # assembly so a downstream f-string refactor (non-int totals from
        # a vocabulary refactor, or a __format__-raising sentinel) surfaces
        # a marker rather than crashing the envelope. Floor must NOT
        # re-interpolate the same values that tripped the closure (W978
        # first-hypothesis discipline). Use the literal "smells completed"
        # floor (LAW 6 still holds: the line works standalone). Mirror of
        # cmd_dark_matter W607-CZ "dark-matter completed" anchor.
        #
        # W978 5th-discipline: ``total_smells`` / ``critical`` / ``warning`` /
        # ``files_affected`` passed as raw args; branching lives INSIDE.
        def _build_verdict_str(_total, _critical, _warning, _files_affected):
            if _total == 0:
                return "Clean: no code smells detected"
            if _critical > 0:
                return (
                    f"Needs refactoring: {_total} smell"
                    f"{'s' if _total != 1 else ''} "
                    f"({_critical} critical, {_warning} warning) "
                    f"in {_files_affected} file{'s' if _files_affected != 1 else ''}"
                )
            if _warning > 0:
                return (
                    f"Fair: {_total} smell"
                    f"{'s' if _total != 1 else ''} "
                    f"({_warning} warning) "
                    f"in {_files_affected} file{'s' if _files_affected != 1 else ''}"
                )
            return (
                f"Good: {_total} minor smell"
                f"{'s' if _total != 1 else ''} "
                f"in {_files_affected} file{'s' if _files_affected != 1 else ''}"
            )

        verdict = _run_check_df(
            "compute_verdict",
            _build_verdict_str,
            total_smells,
            critical,
            warning,
            files_affected,
            default="smells completed",
        )

        # SARIF output (W1171): projection for CI / GitHub Code Scanning.
        # Branches BEFORE json/text so the pre-existing paths stay
        # byte-identical to pre-W1171. The rules catalogue is derived
        # from roam.catalog.registry — closed-by-construction over the
        # registered smell-kind vocabulary; per-finding severity drives
        # the SARIF level (critical -> error, warning -> warning,
        # info -> note).
        # W607-BN / W607-DF: merge top-level warnings_list (W987) with
        # the W607-BN substrate-CALL bucket AND the W607-DF aggregation-
        # phase bucket. Empty buckets -> no change -> byte-identical
        # envelope on the happy path. The two W607 buckets share the
        # canonical ``smells_*`` marker family; phase-name disambiguates
        # which layer raised (substrate vs aggregation).
        def _merged_warnings() -> list[str]:
            """Compose W987 ``warnings_list`` ++ ``_w607bn_warnings_out`` ++ ``_w607df_warnings_out``."""
            return list(warnings_list) + list(_w607bn_warnings_out) + list(_w607df_warnings_out)

        if sarif_mode:
            from roam.output.sarif import (
                runtime_filter_disclosure,
                smells_to_sarif,
                write_sarif,
            )

            # W1061-followup-2: delegate the rule-level configurationOverride
            # boilerplate to the shared :func:`runtime_filter_disclosure`
            # helper. W1061 semantics preserved — each rule NOT in the
            # active filter set surfaces with ``configuration.enabled:
            # false`` so a CI consumer reads a filtered "no findings"
            # run as FILTERED rather than CLEAN. ``--min-severity`` is
            # still a deliberate BAIL because the disable semantic is
            # finding-level not rule-level (rules emit mixed severities).
            rule_disabled: list[tuple[str, dict]] = []
            kinds_active = sanitised_kinds or (set(only_dispatch) if only_dispatch else set())
            if kinds_active:
                from roam.catalog.registry import kind_to_confidence

                all_kinds = sorted(kind_to_confidence().keys())
                disabled_kinds = [k for k in all_kinds if k not in kinds_active]
                filter_source = "--only" if (only_dispatch and not sanitised_kinds) else "--kind"
                for k in disabled_kinds:
                    rule_disabled.append(
                        (
                            f"smells/{k}",
                            {
                                "disabled_by": filter_source,
                                "filter_value": sorted(kinds_active),
                            },
                        )
                    )
            sarif_overrides, _ = runtime_filter_disclosure(
                rule_ids_disabled=rule_disabled,
            )

            # W607-BN: serialize_to_sarif substrate boundary. A raise in
            # the SARIF projection collapses to a degraded JSON envelope
            # so the consumer still sees the smells corpus + the marker.
            _sarif_payload = _run_check_bn(
                "serialize_to_sarif",
                lambda: write_sarif(smells_to_sarif(findings, runtime_overrides=sarif_overrides or None)),
                default=None,
            )
            if _sarif_payload is not None:
                click.echo(_sarif_payload)
            else:
                _all_w = _merged_warnings()
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "smells",
                                summary={
                                    "verdict": "SARIF projection failed; falling back to JSON envelope",
                                    "total_smells": total_smells,
                                    "partial_success": True,
                                    "warnings_out": list(_all_w),
                                },
                                smells=[],
                                warnings_out=list(_all_w),
                            )
                        )
                    )
            return

        if json_mode:
            # R22: wrap each smell in {value, confidence, reason} so
            # consumers can weight signals. Consumers that previously
            # read `smells[i]["symbol_name"]` must now read
            # `smells[i]["value"]["symbol_name"]` and may inspect
            # `smells[i]["confidence"]` ("high"|"medium"|"low") and
            # `smells[i]["reason"]`.
            smell_values = [
                {
                    "smell_id": f["smell_id"],
                    "severity": f["severity"],
                    "symbol_name": f["symbol_name"],
                    "kind": f["kind"],
                    "location": f["location"],
                    "metric_value": f["metric_value"],
                    "threshold": f["threshold"],
                    "description": f["description"],
                }
                for f in findings
            ]
            # W607-BN: classify_severity substrate boundary -- wrap_findings +
            # confidence_distribution. A raise here is a substrate failure:
            # emit the marker and fall back to bare values without the
            # {value, confidence, reason} triples.
            _classified = _run_check_bn(
                "classify_severity",
                lambda: (wrap_findings(smell_values, classifier=_smell_classify),),
                default=None,
            )
            if _classified is not None:
                smell_triples = _classified[0]
                distribution = confidence_distribution(smell_triples)
            else:
                smell_triples = smell_values
                distribution = {}
            verdict_with_conf = verdict_with_high_count(verdict, distribution)
            summary: dict = {
                "verdict": verdict_with_conf,
                "total_smells": total_smells,
                "severity": dict(severity_counts),
                "smell_types": dict(smell_types),
                "files_affected": files_affected,
                "findings_confidence_distribution": distribution,
                "suppressed_count": len(suppressed_smells),
                # W607-DF: surface score_classify result on the envelope
                # so consumers can read the run state without re-deriving
                # from raw counts. W978 7th-discipline anchor: bare
                # ``_score_dict["state"]`` lookup (floor dict guarantees
                # the key) -- NOT ``.get("state", expensive_default)``.
                "run_state": _score_dict["state"],
            }
            # W987 (Pattern 1 — surface silent fallbacks on the envelope):
            # any unknown ``--kind`` value or unknown ``kind:`` in the
            # suppression YAML appended to ``warnings_list``. Surface
            # both on the top-level ``warnings_out`` field AND flip
            # ``partial_success=True`` so a consumer reading only the
            # summary still sees the silent-state disclosure.
            #
            # W607-BN / W607-DF: the marker buckets _w607bn_warnings_out
            # and _w607df_warnings_out are sub-streams of the same
            # warnings_out field. Merge before stamping partial_success
            # so a BN-only or DF-only failure still flips the bit.
            _all_w = _merged_warnings()
            if _all_w:
                summary["partial_success"] = True
                # W607-BN / W607-DF: mirror the merged marker stream onto
                # summary.warnings_out so consumers reading the summary
                # block alone see the degraded substrates + aggregation
                # phases (paired with the top-level mirror).
                summary["warnings_out"] = list(_all_w)
            # W1083-followup-3: when --kind had any unknown values, splice
            # the multi-value helper fragment so the JSON consumer can see
            # the partition + per-unknown ``did_you_mean`` map without
            # parsing the warnings_out strings.
            if _kind_frag is not None and _kind_frag.get("unknown_kinds"):
                summary.update(to_summary_payload_many(_kind_frag, include_known=False))

            # W607-DF -- serialize_envelope boundary. Wraps the envelope
            # serialization itself. A downstream schema-shape refactor
            # that breaks ``json_envelope("smells", ...)`` would
            # otherwise crash AFTER all substrate + aggregation signals
            # were already gathered. Floor to a minimal envelope stub so
            # consumers still receive a parseable JSON object with the
            # marker attached + the canonical command name. Mirror of
            # cmd_dark_matter's W607-CZ / cmd_postmortem's W607-CV /
            # cmd_taint's W607-CJ / cmd_audit_trail_export's W607-CR
            # serialize_envelope floor pattern.
            _envelope_floor: dict = {
                "command": "smells",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": verdict,
                    "partial_success": True,
                    "warnings_out": list(_all_w),
                },
                "warnings_out": list(_all_w),
            }
            envelope = _run_check_df(
                "serialize_envelope",
                json_envelope,
                "smells",
                budget=token_budget,
                summary=summary,
                smells=smell_triples,
                suppressed_smells=suppressed_smells if detail else [],
                warnings_out=list(_all_w),
                default=_envelope_floor,
            )
            # W607-DF -- if ``serialize_envelope`` raised AFTER the merged
            # bucket was already snapshotted, the new
            # ``smells_serialize_envelope_failed:`` marker was appended
            # to ``_w607df_warnings_out`` and the floor stub carries only
            # the pre-raise merged list. Rebuild the floor stub's
            # warnings_out so the new marker reaches the JSON output.
            # Clean path -> envelope is the real json_envelope return
            # value, no rebuild needed.
            if envelope is _envelope_floor and _w607df_warnings_out:
                _all_w = _merged_warnings()
                _envelope_floor["summary"]["warnings_out"] = list(_all_w)
                _envelope_floor["warnings_out"] = list(_all_w)
                envelope = _envelope_floor

            if not detail:
                envelope = strip_list_payloads(envelope)
            click.echo(to_json(envelope))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}\n")

        # W987 + W607-BN: surface every accumulated warning prominently --
        # before the smell list so the user sees the silent-state disclosure
        # even when stdout is piped to ``head``. Mirrors the alerts
        # discipline (cmd_alerts._emit_alerts_text) from W918. Includes the
        # W607-BN substrate-CALL markers via _merged_warnings().
        _all_w_text = _merged_warnings()
        if _all_w_text:
            click.echo(f"Warnings ({len(_all_w_text)}):")
            for w in _all_w_text:
                click.echo(f"  - {w}")
            click.echo()

        if total_smells == 0:
            return

        # Summary line
        sev_parts = []
        for sev in ("critical", "warning", "info"):
            count = severity_counts.get(sev, 0)
            if count:
                sev_parts.append(f"{count} {sev.upper()}")
        click.echo(f"Smells: {total_smells} total -- {', '.join(sev_parts)}")
        click.echo(f"Files affected: {files_affected}")
        click.echo()

        if not detail:
            # Show top 5 with truncated location so the user can jump
            # straight to the offender.
            # bare symbol names ("main", "buildComment") were
            # ambiguous when the same name lived in multiple files.
            top = findings[:5]
            if top:
                rows = [
                    [
                        f["severity"].upper(),
                        f["smell_id"],
                        f["symbol_name"],
                        _short_loc(f.get("location") or ""),
                        f["description"],
                    ]
                    for f in top
                ]
                click.echo(
                    format_table(
                        ["Sev", "Smell", "Symbol", "Where", "Description"],
                        rows,
                    )
                )
                if total_smells > 5:
                    click.echo(f"\n(+{total_smells - 5} more, run `roam --detail smells` for the full list)")
            if not include_tooling and excluded_tooling:
                click.echo(
                    f"\n(excluded {excluded_tooling} smell(s) in tooling/scripts/ci/generated; "
                    f"pass --include-tooling to surface them)"
                )
            if suppressed_smells:
                click.echo(
                    f"\n(suppressed {len(suppressed_smells)} smell(s) via "
                    f".roam/smells.suppress.yml; pass --no-suppress to surface them)"
                )
            return

        # Full detail mode
        rows = [
            [
                f["severity"].upper(),
                f["smell_id"],
                f["symbol_name"],
                str(f["metric_value"]),
                str(f["threshold"]),
                f["location"],
                f["description"],
            ]
            for f in findings
        ]
        click.echo(
            format_table(
                ["Sev", "Smell", "Symbol", "Value", "Threshold", "Location", "Description"],
                rows,
            )
        )
