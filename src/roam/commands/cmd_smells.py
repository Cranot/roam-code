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
SMELLS_DETECTOR_VERSION: str = "1.4.0"

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
                f"--only: unknown smell id(s): {', '.join(unknown_only)}. "
                f"Valid ids: {valid_listing}"
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

    smells_suppressions = (
        []
        if no_suppress or project_root is None
        else load_smells_suppressions_typed(project_root, warnings_out=warnings_list)
    )

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
        findings = run_all_detectors(conn, only=only_dispatch)

        # W658: apply the suppress.yml allowlist BEFORE persist so
        # suppressed findings never enter the findings registry. The
        # suppressed list is held aside for envelope-level disclosure
        # (summary.suppressed_count + suppressed_smells[] in detail
        # mode) — Pattern-2 always-emit discipline says don't drop
        # signal silently.
        suppressed_smells: list[dict] = []
        if smells_suppressions:
            findings, suppressed_smells = apply_suppressions_typed(findings, smells_suppressions)

        # --- W109: mirror into the central findings registry ---
        # Runs ONLY with --persist. The persisted set is independent of the
        # --file / --min-severity display filters — we emit every detector
        # hit on the full codebase so the registry stays comprehensive
        # regardless of how a particular invocation slices the view. The
        # tooling-exclusion check below the persist block applies only to
        # the headline number; the registry is the cross-detector surface
        # and should carry the unfiltered set.
        if persist:
            try:
                _emit_smells_findings(conn, findings, SMELLS_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

        # Default: exclude tooling, generated, examples, vendor, workspaces,
        # docs. The headline number is dominated by paths the user didn't
        # write or doesn't want to refactor (``dev/``, ``.github/scripts/``,
        # ``examples/``, vendored packages, codegen output). The shared
        # path-hint set lives in ``roam.output.file_role_hints`` so all
        # headline commands stay in sync. ``--include-tooling`` opts back
        # into the full set.
        from roam.output.file_role_hints import is_excluded_path

        excluded_tooling = 0
        if not include_tooling:
            tooling_roles = {"ci", "scripts", "build", "generated"}
            tooling_roles_per_file = _file_role_lookup(conn)
            kept: list[dict] = []
            for f in findings:
                loc = (f.get("location") or "").replace("\\", "/")
                file_path_only = loc.split(":", 1)[0] if loc else ""
                role = tooling_roles_per_file.get(file_path_only)
                if role in tooling_roles:
                    excluded_tooling += 1
                    continue
                if is_excluded_path(file_path_only):
                    excluded_tooling += 1
                    continue
                kept.append(f)
            findings = kept

        # Filter by file
        if file_path:
            norm = file_path.replace("\\", "/")
            findings = [f for f in findings if norm in f.get("location", "").replace("\\", "/")]

        # W987/W1035 — filter by smell kind (closed-set validated above).
        # Known ids narrow the result set. Unknown-only filters resolve to
        # zero findings with a warning, instead of silently widening to all
        # findings.
        if sanitised_kinds:
            findings = [f for f in findings if f.get("smell_id") in sanitised_kinds]
        elif kind_filter_requested:
            findings = []

        # Filter by minimum severity (W564: canonical higher = worse).
        if min_severity:
            min_sev = min_severity.lower()
            floor = severity_rank(min_sev)
            findings = [f for f in findings if severity_rank(f.get("severity", "info")) >= floor]

        # Compute summary stats
        total_smells = len(findings)
        severity_counts = Counter(f.get("severity", "info") for f in findings)
        smell_types = Counter(f.get("smell_id", "unknown") for f in findings)
        files_affected = len(set(f.get("location", "").split(":")[0] for f in findings if f.get("location")))

        # Verdict
        critical = severity_counts.get("critical", 0)
        warning = severity_counts.get("warning", 0)
        if total_smells == 0:
            verdict = "Clean: no code smells detected"
        elif critical > 0:
            verdict = (
                f"Needs refactoring: {total_smells} smell"
                f"{'s' if total_smells != 1 else ''} "
                f"({critical} critical, {warning} warning) "
                f"in {files_affected} file{'s' if files_affected != 1 else ''}"
            )
        elif warning > 0:
            verdict = (
                f"Fair: {total_smells} smell"
                f"{'s' if total_smells != 1 else ''} "
                f"({warning} warning) "
                f"in {files_affected} file{'s' if files_affected != 1 else ''}"
            )
        else:
            verdict = (
                f"Good: {total_smells} minor smell"
                f"{'s' if total_smells != 1 else ''} "
                f"in {files_affected} file{'s' if files_affected != 1 else ''}"
            )

        # SARIF output (W1171): projection for CI / GitHub Code Scanning.
        # Branches BEFORE json/text so the pre-existing paths stay
        # byte-identical to pre-W1171. The rules catalogue is derived
        # from roam.catalog.registry — closed-by-construction over the
        # registered smell-kind vocabulary; per-finding severity drives
        # the SARIF level (critical -> error, warning -> warning,
        # info -> note).
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
            kinds_active = sanitised_kinds or (
                set(only_dispatch) if only_dispatch else set()
            )
            if kinds_active:
                from roam.catalog.registry import kind_to_confidence

                all_kinds = sorted(kind_to_confidence().keys())
                disabled_kinds = [k for k in all_kinds if k not in kinds_active]
                filter_source = (
                    "--only"
                    if (only_dispatch and not sanitised_kinds)
                    else "--kind"
                )
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

            click.echo(
                write_sarif(
                    smells_to_sarif(
                        findings, runtime_overrides=sarif_overrides or None
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
            smell_triples = wrap_findings(smell_values, classifier=_smell_classify)
            distribution = confidence_distribution(smell_triples)
            verdict_with_conf = verdict_with_high_count(verdict, distribution)
            summary: dict = {
                "verdict": verdict_with_conf,
                "total_smells": total_smells,
                "severity": dict(severity_counts),
                "smell_types": dict(smell_types),
                "files_affected": files_affected,
                "findings_confidence_distribution": distribution,
                "suppressed_count": len(suppressed_smells),
            }
            # W987 (Pattern 1 — surface silent fallbacks on the envelope):
            # any unknown ``--kind`` value or unknown ``kind:`` in the
            # suppression YAML appended to ``warnings_list``. Surface
            # both on the top-level ``warnings_out`` field AND flip
            # ``partial_success=True`` so a consumer reading only the
            # summary still sees the silent-state disclosure.
            if warnings_list:
                summary["partial_success"] = True
            # W1083-followup-3: when --kind had any unknown values, splice
            # the multi-value helper fragment so the JSON consumer can see
            # the partition + per-unknown ``did_you_mean`` map without
            # parsing the warnings_out strings.
            if _kind_frag is not None and _kind_frag.get("unknown_kinds"):
                summary.update(
                    to_summary_payload_many(_kind_frag, include_known=False)
                )
            envelope = json_envelope(
                "smells",
                budget=token_budget,
                summary=summary,
                smells=smell_triples,
                suppressed_smells=suppressed_smells if detail else [],
                warnings_out=list(warnings_list),
            )
            if not detail:
                envelope = strip_list_payloads(envelope)
            click.echo(to_json(envelope))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}\n")

        # W987: surface every accumulated warning prominently — before
        # the smell list so the user sees the silent-state disclosure
        # even when stdout is piped to ``head``. Mirrors the alerts
        # discipline (cmd_alerts._emit_alerts_text) from W918.
        if warnings_list:
            click.echo(f"Warnings ({len(warnings_list)}):")
            for w in warnings_list:
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
