"""Auto-detect implicit codebase conventions and patterns.

This module hosts the case-classification primitives
(``classify_case``, ``_group_for_kind``, ``_LANGUAGE_KIND_DEFAULTS``,
etc.) used everywhere in roam. The **canonical aggregator** that
applies them and produces per-kind percentages lives in
``roam.commands.conventions_helper`` — the standalone ``conventions``
command, ``roam describe``, ``roam understand``, ``roam minimap``, and
``roam preflight`` all delegate there so they agree on the same
codebase.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because conventions outputs are invocation-scoped
convention-classification percentages — not per-location violations.
See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import hashlib
import json as _json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.languages import JS_FAMILY_LANGUAGES
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    to_json,
)

# W133 (W93 follow-up): conventions is the next detector migrating onto
# the central findings registry (after ``clones`` in W95, ``dead`` in
# W99, ``complexity`` in W102, ``smells`` in W109, and the subsequent
# W110-W111 emitters). The shape mirrors those — a stable detector
# version stamp and a deterministic ``finding_id_str`` so re-runs upsert
# instead of duplicating rows.
#
# **Anti-pattern Pattern 4 note.** Per ``CLAUDE.md`` Pattern 4 and the
# 212-eval dogfood synthesis, five surfaces (``describe``,
# ``understand``, ``minimap``, ``preflight``, and the standalone
# ``conventions`` command) historically each computed conventions
# differently. Fix G consolidated them onto
# ``roam.commands.conventions_helper.compute_conventions`` — that helper
# is *the* canonical detector. W133 deliberately wires ``--persist``
# onto the STANDALONE ``conventions`` command only, because:
#
# * the helper computes the data but doesn't aggregate-then-persist
#   anywhere (only ``conventions`` builds the violation envelope today),
# * the other four surfaces emit summaries, not violation lists, so
#   their ``--persist`` would either redundantly mirror the same rows or
#   re-derive violations under different filters (re-entrenching
#   Pattern 4 at the persistence layer).
#
# Bump CONVENTIONS_DETECTOR_VERSION when the violation predicate (the
# (language_family, kind_group) majority calculation in
# ``conventions_helper._find_outliers``) or the registry-row shape
# changes meaningfully.
CONVENTIONS_DETECTOR_VERSION: str = "1.0.0"


# Per-kind confidence tier mapping for conventions findings.
#
# Conventions themselves are *inferred from majority patterns* — they
# are heuristics by construction (the prompt's instruction:
# "conventions are themselves heuristics inferred from majority
# patterns"). The default tier is therefore ``heuristic``. Where a
# specific subkind has a deterministic basis (file-extension family
# defaults from ``_LANGUAGE_KIND_DEFAULTS`` — e.g. "python functions
# should be snake_case") we upgrade to ``structural``: those rules
# come from the language community default, not from the empirical
# distribution in this repo, so they're a documented expectation
# rather than a freshly-inferred guess.
_CONVENTIONS_VIOLATION_KIND: str = "naming-outlier"
_CONVENTIONS_DEFAULT_CONFIDENCE: str = "heuristic"


def _conventions_violation_confidence(expected_source: str | None) -> str:
    """Map an outlier's ``expected_source`` to a confidence tier.

    ``compute_conventions`` records ``expected_source`` as either
    ``"community_default"`` (the documented language convention from
    ``_LANGUAGE_KIND_DEFAULTS``) or ``"empirical"`` (the codebase's own
    majority style). Community-default violations are upgraded to
    ``structural`` because the expected style is documented language
    convention rather than a freshly-inferred majority. Empirical
    violations stay at ``heuristic``.
    """
    if expected_source == "community_default":
        return "structural"
    return _CONVENTIONS_DEFAULT_CONFIDENCE


def _conventions_finding_id(
    family: str,
    group: str,
    name: str,
    file_path: str,
    line_start: int | None,
) -> str:
    """Stable, deterministic finding id for one convention violation.

    The (language_family, kind_group, name, file_path, line_start)
    tuple re-identifies the same outlier across runs. We fold the
    (family, group) into the digest because the same symbol name in
    two different family/group contexts is a different finding (e.g.
    a Python ``foo`` flagged as a function-style outlier vs the same
    symbol re-flagged as a property-style outlier).
    """
    raw = f"{family}|{group}|{name}|{file_path}|{int(line_start or 0)}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"conventions:{_CONVENTIONS_VIOLATION_KIND}:{digest}"


def _resolve_convention_subject_id(
    conn: sqlite3.Connection,
    file_path: str,
    symbol_name: str,
    line_start: int | None,
) -> int | None:
    """Best-effort lookup of ``symbols.id`` for one outlier triple.

    Mirrors ``cmd_smells._resolve_smell_subject_id`` — exact match on
    (path, name, line_start) first, then nearest-line by name.
    Returns ``None`` when nothing matches; the findings registry
    permits NULL subject_id by design.
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
        return None


def _emit_records_to_findings_registry(
    conn: sqlite3.Connection,
    records: list[dict],
    source_detector: str,
    source_version: str,
    *,
    build_finding: Callable[[dict, sqlite3.Connection], "FindingRecord | None"],
) -> int:
    """Shared registry-mirror loop for detector-specific records.

    Encodes the uniform emission mechanics (local import, JSON evidence,
    ``emit_finding`` call, count) while letting each detector supply the
    domain-specific mapping from its raw record to a ``FindingRecord``.
    Records whose builder returns ``None`` are skipped silently.
    """
    # Local import keeps the cost out of the read-only path — callers
    # without --persist never reach here.
    from roam.db.findings import FindingRecord, emit_finding

    written = 0
    for rec in records:
        finding = build_finding(rec, conn)
        if finding is None:
            continue
        emit_finding(conn, finding)
        written += 1
    return written


def _emit_conventions_findings(
    conn: sqlite3.Connection,
    outliers: list[dict],
    source_version: str,
) -> int:
    """Mirror each convention-violation outlier into the findings registry.

    Returns the count of rows written. The caller is responsible for
    opening ``conn`` writable; ``emit_finding`` does not commit (the
    caller commits once at the end of the persist branch).

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard conventions command path.

    Only convention **violations** (outliers) are emitted — the
    detected conventions themselves are *state* (codebase inventory)
    not findings, and stay in the existing per-kind / per-(family,
    group) summary surfaces.
    """
    from roam.db.findings import FindingRecord

    def _build_convention_finding(o: dict, _conn: sqlite3.Connection) -> FindingRecord | None:
        name = o.get("name") or ""
        kind = o.get("kind") or ""
        file_path = o.get("file") or ""
        line_start = o.get("line")
        try:
            line_start_int: int | None = int(line_start) if line_start is not None else None
        except (TypeError, ValueError):
            line_start_int = None
        family = o.get("language_family") or "unknown"
        group = _group_for_kind(kind)
        actual_style = o.get("actual_style") or "?"
        expected_style = o.get("expected_style") or "?"
        expected_source = o.get("expected_source")

        subject_id = _resolve_convention_subject_id(_conn, file_path, name, line_start_int)
        finding_id = _conventions_finding_id(family, group, name, file_path, line_start_int)
        evidence = {
            "name": name,
            "kind": kind,
            "language_family": family,
            "kind_group": group,
            "actual_style": actual_style,
            "expected_style": expected_style,
            "expected_source": expected_source,
            "file_path": file_path,
            "line_start": line_start_int,
        }
        location = f"{file_path}:{line_start_int}" if line_start_int is not None else file_path
        claim = (
            f"naming-outlier: {name} ({kind}) is {actual_style}, "
            f"expected {expected_style} for {family}/{group} at {location}"
        )
        confidence = _conventions_violation_confidence(expected_source)
        return FindingRecord(
            finding_id_str=finding_id,
            subject_kind="symbol" if subject_id is not None else "file",
            subject_id=subject_id,
            claim=claim,
            evidence_json=_json.dumps(evidence, sort_keys=True),
            confidence=confidence,
            source_detector="conventions",
            source_version=source_version,
        )

    return _emit_records_to_findings_registry(
        conn,
        outliers,
        "conventions",
        source_version,
        build_finding=_build_convention_finding,
    )


# R22 — confidence classifier for convention-violation findings.
#
# Each outlier carries the (family, group) it violated. The confidence
# reflects how dominant the convention is in that group:
#   high   — the convention is dominant in ≥90% of the group; this
#            symbol is genuinely an outlier.
#   medium — 70–89% dominance; convention is real but with notable
#            minority styles, so the call is softer.
#   low    — 50–69% dominance; the project doesn't have a clear
#            convention, so flagging the symbol is mostly noise.
#
# We capture the group percent at envelope-build time and stash it on
# each outlier so the classifier can read it without needing the
# naming_summary dict.
_CONVENTION_HIGH_PCT = 90.0
_CONVENTION_MEDIUM_PCT = 70.0


def _convention_classify(violation: dict) -> tuple[str, str]:
    """Map a convention-violation finding to a (confidence, reason) tuple."""
    pct = violation.get("group_dominant_pct")
    expected = violation.get("expected_style") or "?"
    actual = violation.get("actual_style") or "?"
    try:
        pct_f = float(pct) if pct is not None else 0.0
    except (TypeError, ValueError):
        pct_f = 0.0
    if pct_f >= _CONVENTION_HIGH_PCT:
        return "high", (f"{expected} is dominant in {pct_f:.0f}% of its group; this {actual} symbol is a clear outlier")
    if pct_f >= _CONVENTION_MEDIUM_PCT:
        return "medium", (f"{expected} dominant in {pct_f:.0f}% of its group; convention real but not unanimous")
    return "low", (f"{expected} only {pct_f:.0f}% dominant; convention is weak in this group")


# ---------------------------------------------------------------------------
# Case-style detection primitives
# ---------------------------------------------------------------------------
#
# The case classifiers, skip / non-code-language sets, type-alias and
# constant detectors, and per-language convention-default tables were
# extracted into the leaf module ``conventions_primitives`` so that
# ``conventions_helper`` can import them WITHOUT importing this command
# module (which would form a top-level import cycle:
# ``conventions_helper -> cmd_conventions -> conventions_helper``). They are
# re-exported here unchanged so existing references
# (``cmd_conventions.classify_case`` / ``cmd_conventions._SKIP_NAMES`` /
# ``cmd_conventions._LANGUAGE_KIND_DEFAULTS`` in ``cmd_verify``,
# ``cmd_preflight``, ``laws.checker``, and the test suite) keep resolving.
from roam.commands.conventions_primitives import (  # noqa: E402,F401
    _CASE_PATTERNS,
    _KIND_GROUPS,
    _LANGUAGE_FAMILIES,
    _LANGUAGE_KIND_DEFAULTS,
    _MIN_NAME_LEN,
    _SINGLE_LOWER,
    _SINGLE_PASCAL,
    _SINGLE_UPPER,
    _SKIP_NAMES,
    _TYPE_ALIAS_CONTAINERS,
    NON_CODE_CONVENTION_LANGUAGES,
    _detect_affixes,
    _group_for_kind,
    _language_family,
    classify_case,
    is_python_type_alias_signature,
    is_upper_snake_constant_name,
)

# ---------------------------------------------------------------------------
# File organization detection
# ---------------------------------------------------------------------------

_TEST_PATTERNS = [
    ("test_*.py", re.compile(r"(^|/)test_[^/]+\.py$")),
    ("*_test.py", re.compile(r"(^|/)[^/]+_test\.py$")),
    ("*.test.ts", re.compile(r"(^|/)[^/]+\.test\.ts$")),
    ("*.test.tsx", re.compile(r"(^|/)[^/]+\.test\.tsx$")),
    ("*.test.js", re.compile(r"(^|/)[^/]+\.test\.js$")),
    ("*.test.jsx", re.compile(r"(^|/)[^/]+\.test\.jsx$")),
    ("*.spec.ts", re.compile(r"(^|/)[^/]+\.spec\.ts$")),
    ("*.spec.tsx", re.compile(r"(^|/)[^/]+\.spec\.tsx$")),
    ("*.spec.js", re.compile(r"(^|/)[^/]+\.spec\.js$")),
    ("*.spec.jsx", re.compile(r"(^|/)[^/]+\.spec\.jsx$")),
    ("*_test.go", re.compile(r"(^|/)[^/]+_test\.go$")),
    ("*_test.rs", re.compile(r"(^|/)[^/]+_test\.rs$")),
    ("Test*.java", re.compile(r"(^|/)Test[^/]+\.java$")),
    ("*Test.java", re.compile(r"(^|/)[^/]+Test\.java$")),
]

_BARREL_NAMES = frozenset(
    {
        "index.ts",
        "index.js",
        "index.tsx",
        "index.jsx",
        "index.mjs",
        "index.cjs",
        "__init__.py",
    }
)


def _analyze_files(paths: list[str]) -> dict:
    """Analyze file paths for directory structure and test conventions."""
    normalized = [p.replace("\\", "/") for p in paths]

    # Top-level directory counts
    dir_counts: Counter = Counter()
    for p in normalized:
        parts = p.split("/")
        if len(parts) > 1:
            dir_counts[parts[0] + "/"] += 1

    top_dirs = [{"dir": d, "count": c} for d, c in dir_counts.most_common(15) if c >= 2]

    # Test file patterns
    test_pattern_counts: Counter = Counter()
    test_dir_counts: Counter = Counter()
    total_test_files = 0

    for p in normalized:
        for pattern_name, regex in _TEST_PATTERNS:
            if regex.search(p):
                test_pattern_counts[pattern_name] += 1
                total_test_files += 1
                # Track which directories contain tests
                parts = p.split("/")
                if len(parts) > 1:
                    test_dir_counts[parts[0] + "/"] += 1
                break

    test_patterns = [{"pattern": pat, "count": c} for pat, c in test_pattern_counts.most_common(5) if c >= 1]

    test_dirs = [{"dir": d, "count": c} for d, c in test_dir_counts.most_common(5) if c >= 1]

    # Barrel files
    barrel_count = 0
    for p in normalized:
        basename = p.rsplit("/", 1)[-1] if "/" in p else p
        if basename in _BARREL_NAMES:
            barrel_count += 1

    return {
        "total_files": len(paths),
        "top_dirs": top_dirs,
        "test_patterns": test_patterns,
        "test_dirs": test_dirs,
        "test_file_count": total_test_files,
        "barrel_files": barrel_count,
        "has_barrels": barrel_count > 0,
    }


# ---------------------------------------------------------------------------
# Import pattern detection
# ---------------------------------------------------------------------------


def _analyze_imports(conn) -> dict:
    """Analyze import edges for absolute vs relative and grouping patterns."""
    # Get edges with kind='imports' joining file paths
    rows = conn.execute("""
        SELECT fe.source_file_id, fe.target_file_id, fe.symbol_count,
               sf.path as source_path, tf.path as target_path
        FROM file_edges fe
        JOIN files sf ON fe.source_file_id = sf.id
        JOIN files tf ON fe.target_file_id = tf.id
        WHERE fe.kind = 'imports'
    """).fetchall()

    if not rows:
        return {
            "total_import_edges": 0,
            "absolute_imports": 0,
            "relative_imports": 0,
            "absolute_pct": 0,
            "style": "unknown",
        }

    total = len(rows)
    relative = 0
    absolute = 0

    for r in rows:
        src = r["source_path"].replace("\\", "/")
        tgt = r["target_path"].replace("\\", "/")

        # Heuristic: if source and target share a common prefix directory,
        # and the target is within 2 levels, it's likely a relative import.
        src_parts = src.rsplit("/", 1)
        tgt_parts = tgt.rsplit("/", 1)

        src_dir = src_parts[0] if len(src_parts) > 1 else ""
        tgt_dir = tgt_parts[0] if len(tgt_parts) > 1 else ""

        if (
            src_dir
            and tgt_dir
            and (src_dir == tgt_dir or src_dir.startswith(tgt_dir + "/") or tgt_dir.startswith(src_dir + "/"))
        ):
            relative += 1
        else:
            absolute += 1

    abs_pct = round(100 * absolute / total, 1) if total else 0
    style = "absolute" if abs_pct >= 60 else "relative" if abs_pct <= 40 else "mixed"

    return {
        "total_import_edges": total,
        "absolute_imports": absolute,
        "relative_imports": relative,
        "absolute_pct": abs_pct,
        "relative_pct": round(100 * relative / total, 1) if total else 0,
        "style": style,
    }


# ---------------------------------------------------------------------------
# Export pattern detection
# ---------------------------------------------------------------------------


def _analyze_exports(conn) -> dict:
    """Analyze is_exported flag distribution across symbols."""
    row = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_exported = 1 THEN 1 ELSE 0 END) as exported,
            SUM(CASE WHEN is_exported = 0 THEN 1 ELSE 0 END) as private
        FROM symbols
        WHERE kind IN ('function', 'class', 'method', 'variable', 'constant',
                        'interface', 'struct', 'enum', 'type_alias')
    """).fetchone()

    total = row["total"] or 0
    exported = row["exported"] or 0
    private = row["private"] or 0
    exported_pct = round(100 * exported / total, 1) if total else 0

    # Per-kind breakdown
    kind_rows = conn.execute("""
        SELECT kind,
               COUNT(*) as total,
               SUM(CASE WHEN is_exported = 1 THEN 1 ELSE 0 END) as exported
        FROM symbols
        WHERE kind IN ('function', 'class', 'method', 'variable', 'constant',
                        'interface', 'struct', 'enum', 'type_alias')
        GROUP BY kind
        ORDER BY total DESC
    """).fetchall()

    by_kind = []
    for kr in kind_rows:
        kt = kr["total"] or 0
        ke = kr["exported"] or 0
        by_kind.append(
            {
                "kind": kr["kind"],
                "total": kt,
                "exported": ke,
                "exported_pct": round(100 * ke / kt, 1) if kt else 0,
            }
        )

    # Detect default-export vs named-export preference for JS/TS
    # Check if files have exactly one exported symbol (likely default export).
    # Vue / Svelte SFCs participate in the same import graph as .ts files
    # (their ``<script>`` blocks compile down to ESM modules) so they're
    # counted here — see ``roam.languages.JS_FAMILY_LANGUAGES``.
    js_ph = ",".join("?" * len(JS_FAMILY_LANGUAGES))
    default_style_rows = conn.execute(
        f"""
        SELECT f.id, f.path, COUNT(*) as exported_count
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.is_exported = 1
          AND f.language IN ({js_ph})
        GROUP BY f.id
    """,
        JS_FAMILY_LANGUAGES,
    ).fetchall()

    single_export_files = sum(1 for r in default_style_rows if r["exported_count"] == 1)
    multi_export_files = sum(1 for r in default_style_rows if r["exported_count"] > 1)
    js_ts_total = single_export_files + multi_export_files

    export_style = "unknown"
    if js_ts_total > 0:
        if single_export_files > multi_export_files:
            export_style = "default-export preferred"
        else:
            export_style = "named-exports preferred"

    return {
        "total_symbols": total,
        "exported": exported,
        "private": private,
        "exported_pct": exported_pct,
        "by_kind": by_kind,
        "js_ts_export_style": export_style,
        "js_ts_single_export_files": single_export_files,
        "js_ts_multi_export_files": multi_export_files,
    }


# ---------------------------------------------------------------------------
# Error handling detection
# ---------------------------------------------------------------------------

_ERROR_NAME_RE = re.compile(r"(Error|Exception|Err|Fault|Failure|Panic)$", re.IGNORECASE)


def _analyze_error_handling(conn) -> dict:
    """Detect error/exception patterns from symbols and file complexity."""
    # Count error-related symbols.
    # Use a broad query then filter in Python to avoid LIKE false positives
    # (e.g., DEFAULT matching %Fault%).
    error_candidates = conn.execute("""
        SELECT s.name, s.kind, f.path as file_path, s.line_start
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.name LIKE '%Error%'
           OR s.name LIKE '%Exception%'
           OR s.name LIKE '%Failure%'
    """).fetchall()
    error_symbols = [
        r
        for r in error_candidates
        if _ERROR_NAME_RE.search(r["name"])
        or "Error" in r["name"]
        or "Exception" in r["name"]
        or "Failure" in r["name"]
    ]

    error_classes = [r for r in error_symbols if r["kind"] in ("class", "struct", "interface")]
    error_functions = [r for r in error_symbols if r["kind"] in ("function", "method")]

    # Complexity as proxy for error handling density
    complexity_rows = conn.execute("""
        SELECT AVG(complexity) as avg_complexity,
               MAX(complexity) as max_complexity,
               COUNT(*) as file_count
        FROM file_stats
        WHERE complexity > 0
    """).fetchone()

    return {
        "error_symbol_count": len(error_symbols),
        "error_classes": len(error_classes),
        "error_functions": len(error_functions),
        "error_symbols": [
            {"name": r["name"], "kind": r["kind"], "file": r["file_path"], "line": r["line_start"]}
            for r in error_symbols[:20]
        ],
        "avg_complexity": round(complexity_rows["avg_complexity"] or 0, 1),
        "max_complexity": round(complexity_rows["max_complexity"] or 0, 1),
        "files_with_complexity": complexity_rows["file_count"] or 0,
    }


def _analyze_naming(conn, exclude_paths=None) -> tuple[list, dict, list, dict]:
    """Discover dominant naming style per (language_family, kind_group) and
    surface symbols that violate it.

    Delegates to ``roam.commands.conventions_helper.compute_conventions``
    so the standalone ``conventions`` command agrees with every other
    roam command that mentions conventions. Excludes non-source-code
    paths (``.github/``, ``.claude/``, ``docs/``, ``dist/``, …) by
    default — Pattern 4 of the dogfood corpus showed standalone
    conventions emitting 9014 outliers (51% of identifiers!) including
    ``.github/workflows/setup-node``-style false positives.

    Returns ``(all_symbols, naming_summary, outliers, affixes)`` to
    preserve the historic call shape for backwards compatibility with
    the JSON envelope.
    """
    # Inline import to avoid circular dependency at module load.
    from roam.commands.conventions_helper import compute_conventions

    result = compute_conventions(conn, exclude_paths=exclude_paths)

    # all_symbols is returned for callers that needed the raw row list.
    # The helper doesn't expose that anymore (since exclude filtering
    # happens inside it), so we synthesise a compatible row count from
    # the totals. Existing callers (this module's own ``conventions``
    # command) only use ``naming_summary``, ``outliers``, and
    # ``affixes`` — the raw list is kept as an empty placeholder.
    all_symbols: list = []
    naming_summary = result["by_family_group"]
    outliers = result["outliers"]
    affixes = result["affixes"]
    return all_symbols, naming_summary, outliers, affixes


def _build_naming_summary(group_cases: dict[tuple[str, str], Counter]) -> dict[str, dict]:
    """Pick the dominant style per (family, group). Documented community
    defaults beat the empirical mode so a SQL-heavy project's bad habits
    don't get treated as "the convention"."""
    summary: dict[str, dict] = {}
    for (family, group), counter in sorted(group_cases.items()):
        total = sum(counter.values())
        empirical_style, empirical_count = counter.most_common(1)[0]
        documented = _LANGUAGE_KIND_DEFAULTS.get((family, group))
        dominant_style = documented or empirical_style
        dominant_count = counter.get(dominant_style, empirical_count)
        pct = round(100 * dominant_count / total, 1) if total else 0
        key = f"{family}/{group}" if family != "unknown" else group
        summary[key] = {
            "dominant_style": dominant_style,
            "expected_source": "community_default" if documented else "empirical",
            "dominant_count": dominant_count,
            "total": total,
            "percent": pct,
            "language_family": family,
            "kind_group": group,
            "breakdown": dict(counter.most_common()),
        }
    return summary


def _find_naming_outliers(symbol_details: list[dict], naming_summary: dict[str, dict]) -> list[dict]:
    """Symbols whose case style doesn't match the dominant style for their
    (family, group)."""
    outliers: list[dict] = []
    for det in symbol_details:
        family = det["language_family"]
        group = det["group"]
        key = f"{family}/{group}" if family != "unknown" else group
        grp_info = naming_summary.get(key)
        if grp_info and det["style"] != grp_info["dominant_style"]:
            outliers.append(
                {
                    "name": det["name"],
                    "kind": det["kind"],
                    "language_family": family,
                    "actual_style": det["style"],
                    "expected_style": grp_info["dominant_style"],
                    "expected_source": grp_info["expected_source"],
                    "file": det["file"],
                    "line": det["line"],
                }
            )
    return outliers


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


@roam_capability(
    name="conventions",
    category="refactoring",
    summary="Auto-detect codebase naming, file, import, and export conventions",
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
@click.option("-n", "max_outliers", default=10, help="Maximum outliers to display per category")
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist convention-violation findings (naming outliers) to "
        ".roam/index.db findings registry (cross-detector queryable via "
        "`roam findings list --detector conventions`). The detector-"
        "specific output is unchanged; the registry rows are the "
        "denormalised cross-detector surface. Detected conventions "
        "themselves stay as inventory in the standard envelope — only "
        "violations are emitted as findings."
    ),
)
@click.pass_context
def conventions(ctx, max_outliers, persist):
    """Auto-detect codebase naming, file, import, and export conventions.

    Unlike ``verify`` (which enforces conventions on changed files) and
    ``check-rules`` (which evaluates governance rules), this command
    discovers what conventions the codebase actually follows.

    Naming detection delegates to the canonical helper at
    ``roam.commands.conventions_helper`` so this command's verdicts
    agree with ``roam describe``, ``roam understand``, ``roam minimap``,
    and ``roam preflight``.

    By default the helper skips identifiers under ``.github/``,
    ``.claude/``, ``docs/``, ``dist/``, ``build/``, ``node_modules/``,
    ``vendor/``, and ``__pycache__/``. The global ``--include-excluded``
    flag restores legacy scan-everything behaviour for users who need it.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    include_excluded = ctx.obj.get("include_excluded") if ctx.obj else False
    ensure_index()

    # W607-CW -- substrate-boundary plumbing for cmd_conventions.
    # ``_run_check_cw`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607cw_warnings_out`` rather than
    # crashing the conventions detector outright. cmd_conventions is
    # the project-convention detector (W133 origin per CLAUDE.md
    # detector roster -- part of the original 16 findings-registry
    # substrate detectors). Per W162 (test/fixture path exclusion +
    # TypeAlias detection) + W988 (Pattern-2 empty-state playbook
    # applied) + Fix G (conventions_helper.compute_conventions
    # canonical aggregator delegation) the substrate boundaries here
    # are: the conventions_helper delegation (Fix G), the per-axis
    # analysis helpers (files/imports/errors/exports), the registry
    # mirror (W133 --persist), and the verdict / wrap_findings
    # composition. Marker family
    # ``conventions_<phase>_failed:<exc_class>:<detail>``. Closes the
    # 12-WAY detector family with cmd_orphan_imports + cmd_bus_factor +
    # cmd_hotspots + cmd_auth_gaps + cmd_n1 + cmd_over_fetch +
    # cmd_missing_index + cmd_smells + cmd_vibe_check + cmd_clones +
    # cmd_duplicates + cmd_dead.
    #
    # Substrates wrapped:
    #
    #   * analyse_naming             -- Fix G conventions_helper
    #                                   delegation (the canonical
    #                                   per-(family, group) aggregator)
    #   * query_files                -- ``SELECT path FROM files`` row
    #                                   fetch (DB read)
    #   * analyse_files              -- file-organization summarizer
    #                                   (test patterns / barrel files
    #                                   / top dirs)
    #   * analyse_imports            -- import-edge style detector
    #                                   (absolute vs relative)
    #   * analyse_error_handling     -- error-symbol roll-up
    #   * analyse_exports            -- is_exported distribution +
    #                                   JS/TS default-vs-named style
    #   * emit_findings              -- W133 registry mirror
    #                                   (sqlite3.OperationalError silent
    #                                   no-op preserved for pre-W89 DB)
    #   * build_naming_violations    -- per-outlier dict assembly with
    #                                   group_dominant_pct annotation
    #   * wrap_findings_classify     -- R22 wrap_findings +
    #                                   confidence_distribution +
    #                                   verdict_with_high_count
    #   * compose_verdict            -- LAW 6 single-line verdict
    _w607cw_warnings_out: list[str] = []

    def _run_check_cw(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-CW marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``conventions_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607cw_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cw_warnings_out.append(f"conventions_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        project = find_project_root().name

        # ---- 1. Naming conventions ----
        # ``exclude_paths=()`` disables the default exclusion list so
        # the legacy scan-everything behaviour is still available via
        # the global ``--include-excluded`` flag.
        exclude_paths = () if include_excluded else None
        # W607-CW: ``analyse_naming`` substrate -- the Fix G canonical
        # delegation to conventions_helper.compute_conventions. A raise
        # in the helper (e.g. on a malformed symbols table) degrades to
        # the empty-floor tuple so the envelope's per-axis composition
        # still runs.
        analyse_naming_result = _run_check_cw(
            "analyse_naming",
            _analyze_naming,
            conn,
            exclude_paths=exclude_paths,
            default=([], {}, [], {"prefixes": [], "suffixes": []}),
        )
        if analyse_naming_result is None:
            analyse_naming_result = ([], {}, [], {"prefixes": [], "suffixes": []})
        all_symbols, naming_summary, outliers, affixes = analyse_naming_result

        # --- W133: mirror outliers into the central findings registry ---
        # Runs ONLY with --persist. We emit one row per convention
        # violation; the conventions themselves stay as inventory in
        # the standard envelope (the per-(family, group) summary).
        # Wrapped defensively so a pre-W89 DB (no ``findings`` table)
        # degrades cleanly without breaking the standard output path.
        # W607-CW: ``emit_findings`` substrate boundary uses DIRECT
        # try/except (not _run_check_cw) because the pre-W89 schema
        # path (sqlite3.OperationalError on missing ``findings`` table)
        # is the EXPECTED degraded path -- the W133 silent no-op
        # contract for that case must NOT produce a W607-CW marker.
        # Generic exceptions surface via
        # ``conventions_emit_findings_failed:<exc_class>:<detail>``
        # marker. Mirrors the cmd_bus_factor W607-CQ template:
        # OperationalError == silent no-op; generic Exception ==
        # W607-CW marker.
        if persist:
            try:
                _emit_conventions_findings(conn, outliers, CONVENTIONS_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError:
                pass
            except Exception as _emit_exc:  # noqa: BLE001 -- W607-CW disclosure
                _w607cw_warnings_out.append(f"conventions_emit_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}")

        # ---- 2. File organization ----
        # W607-CW: ``query_files`` substrate -- the SELECT path FROM
        # files row fetch. A raise (e.g. OperationalError on missing
        # files table) degrades to [] so the per-file analysis runs
        # against the empty floor.
        file_paths = _run_check_cw(
            "query_files",
            lambda: [r["path"] for r in conn.execute("SELECT path FROM files ORDER BY path").fetchall()],
            default=[],
        )
        if file_paths is None:
            file_paths = []
        # W607-CW: ``analyse_files`` substrate -- file-organization
        # summarizer (test patterns / barrel files / top dirs).
        file_info = _run_check_cw(
            "analyse_files",
            _analyze_files,
            file_paths,
            default={
                "total_files": 0,
                "top_dirs": [],
                "test_patterns": [],
                "test_dirs": [],
                "test_file_count": 0,
                "barrel_files": 0,
                "has_barrels": False,
            },
        )
        if file_info is None:
            file_info = {
                "total_files": 0,
                "top_dirs": [],
                "test_patterns": [],
                "test_dirs": [],
                "test_file_count": 0,
                "barrel_files": 0,
                "has_barrels": False,
            }

        # ---- 3. Import patterns ----
        # W607-CW: ``analyse_imports`` substrate -- import-edge style
        # detector. A raise in the SQL ingest degrades to the empty
        # 'unknown' floor.
        import_info = _run_check_cw(
            "analyse_imports",
            _analyze_imports,
            conn,
            default={
                "total_import_edges": 0,
                "absolute_imports": 0,
                "relative_imports": 0,
                "absolute_pct": 0,
                "relative_pct": 0,
                "style": "unknown",
            },
        )
        if import_info is None:
            import_info = {
                "total_import_edges": 0,
                "absolute_imports": 0,
                "relative_imports": 0,
                "absolute_pct": 0,
                "relative_pct": 0,
                "style": "unknown",
            }

        # ---- 4. Error handling ----
        # W607-CW: ``analyse_error_handling`` substrate -- error-symbol
        # roll-up. A raise (e.g. file_stats query trips) degrades to
        # the zero-count floor.
        error_info = _run_check_cw(
            "analyse_error_handling",
            _analyze_error_handling,
            conn,
            default={
                "error_symbol_count": 0,
                "error_classes": 0,
                "error_functions": 0,
                "error_symbols": [],
                "avg_complexity": 0,
                "max_complexity": 0,
                "files_with_complexity": 0,
            },
        )
        if error_info is None:
            error_info = {
                "error_symbol_count": 0,
                "error_classes": 0,
                "error_functions": 0,
                "error_symbols": [],
                "avg_complexity": 0,
                "max_complexity": 0,
                "files_with_complexity": 0,
            }

        # ---- 5. Export patterns ----
        # W607-CW: ``analyse_exports`` substrate -- is_exported
        # distribution + JS/TS default-vs-named style. A raise degrades
        # to the zero-count floor with style="unknown".
        export_info = _run_check_cw(
            "analyse_exports",
            _analyze_exports,
            conn,
            default={
                "total_symbols": 0,
                "exported": 0,
                "private": 0,
                "exported_pct": 0,
                "by_kind": [],
                "js_ts_export_style": "unknown",
                "js_ts_single_export_files": 0,
                "js_ts_multi_export_files": 0,
            },
        )
        if export_info is None:
            export_info = {
                "total_symbols": 0,
                "exported": 0,
                "private": 0,
                "exported_pct": 0,
                "by_kind": [],
                "js_ts_export_style": "unknown",
                "js_ts_single_export_files": 0,
                "js_ts_multi_export_files": 0,
            }

        # ---- Build verdict ----
        # W607-CW: ``compose_verdict`` substrate -- LAW 6 single-line
        # verdict string. The ``max(naming_summary.values(), key=...)``
        # call and the ``biggest_group['dominant_style']`` access are
        # KeyError / ValueError prone on a degraded naming_summary
        # (e.g. if analyse_naming raised earlier and returned the {}
        # empty floor). The wrap degrades to the explicit "no data"
        # floor so the envelope still emits a non-empty verdict
        # (W811/W817-style Pattern-2 contract).
        def _compose_verdict():
            dominant_desc = ""
            if naming_summary:
                # Pick the group with the most symbols to represent overall style
                biggest_group = max(naming_summary.values(), key=lambda g: g["total"])
                dominant_desc = f"{biggest_group['dominant_style']} ({biggest_group['percent']}%)"
            test_desc = (
                f"{file_info['test_file_count']} test files" if file_info["test_file_count"] else "no test files"
            )
            outlier_desc = f"{len(outliers)} naming outliers" if outliers else "consistent naming"
            return f"{outlier_desc}, {dominant_desc}, {test_desc}"

        verdict = _run_check_cw("compose_verdict", _compose_verdict, default="no data")
        if verdict is None:
            verdict = "no data"

        # ---- JSON output ----
        if json_mode:
            # W607-CW: ``build_naming_violations`` substrate -- per-outlier
            # dict assembly with group_dominant_pct annotation. A raise
            # (e.g. KeyError on a malformed outlier missing ``name``)
            # degrades to [] so the envelope's violations array stays
            # well-formed.
            def _build_naming_violations():
                violation_list = []
                for o in outliers:
                    family = o.get("language_family", "unknown")
                    # Recover group by looking at the kind (we don't store
                    # group on the outlier directly).
                    kind = o.get("kind", "")
                    group = _group_for_kind(kind)
                    key = f"{family}/{group}" if family != "unknown" else group
                    grp_info = naming_summary.get(key) or {}
                    violation_list.append(
                        {
                            "name": o["name"],
                            "kind": o["kind"],
                            "actual_style": o["actual_style"],
                            "expected_style": o["expected_style"],
                            "file": o["file"],
                            "line": o["line"],
                            "group_dominant_pct": grp_info.get("percent"),
                            "group_dominant_style": grp_info.get("dominant_style"),
                            "naming_group": key,
                        }
                    )
                return violation_list

            violation_list = _run_check_cw(
                "build_naming_violations",
                _build_naming_violations,
                default=[],
            )
            if violation_list is None:
                violation_list = []

            # R22: wrap each violation in {value, confidence, reason}.
            # Consumers that previously read violations[i]["name"] must
            # now read violations[i]["value"]["name"] plus
            # violations[i]["confidence"] / violations[i]["reason"].
            # W607-CW: ``wrap_findings_classify`` substrate -- R22
            # wrap_findings + confidence_distribution +
            # verdict_with_high_count composition. A raise in the
            # classifier or the distribution rollup degrades to the
            # unwrapped verdict + empty distribution.
            def _wrap_findings_classify():
                triples = wrap_findings(violation_list, classifier=_convention_classify)
                dist = confidence_distribution(triples)
                return triples, dist, verdict_with_high_count(verdict, dist)

            wfc_result = _run_check_cw(
                "wrap_findings_classify",
                _wrap_findings_classify,
                default=([], {}, verdict),
            )
            if wfc_result is None:
                wfc_result = ([], {}, verdict)
            violation_triples, distribution, wrapped_verdict = wfc_result
            # W805-followup-E: empty-state disclosure (Pattern 2 silent-
            # fallback fix). When naming_summary is empty the per-group
            # symbol analysis ran against zero symbols; "consistent
            # naming, no test files" is indistinguishable from "we
            # analyzed nothing." Surface the degraded state explicitly.
            empty_naming = not naming_summary
            summary = {
                "verdict": (
                    "no symbols analyzed (corpus empty — run `roam index --force` to populate)"
                    if empty_naming
                    else wrapped_verdict
                ),
                "total_symbols_analyzed": sum(g["total"] for g in naming_summary.values()),
                "naming_groups": len(naming_summary),
                "outlier_count": len(outliers),
                "total_files": file_info["total_files"],
                "test_files": file_info["test_file_count"],
                "barrel_files": file_info["barrel_files"],
                "import_style": import_info["style"],
                "exported_pct": export_info["exported_pct"],
                "findings_confidence_distribution": distribution,
            }
            if empty_naming:
                summary["partial_success"] = True
                summary["state"] = "no_symbols_analyzed"
            # W607-CW: mirror substrate markers into BOTH the top-level
            # envelope ``warnings_out`` AND ``summary.warnings_out`` so
            # MCP consumers see disclosure regardless of which surface
            # they read. Flipping ``partial_success: True`` is the
            # Pattern-2 silent-fallback guard -- a degraded substrate
            # path must NOT be mistaken for a clean conventions verdict.
            envelope_kwargs = dict(
                summary=summary,
                budget=token_budget,
                naming=naming_summary,
                affixes=affixes,
                files=file_info,
                imports=import_info,
                exports=export_info,
                errors=error_info,
                violations=violation_triples,
            )
            if _w607cw_warnings_out:
                summary["partial_success"] = True
                summary["warnings_out"] = list(_w607cw_warnings_out)
                envelope_kwargs["warnings_out"] = list(_w607cw_warnings_out)
            click.echo(to_json(json_envelope("conventions", **envelope_kwargs)))
            return

        # ---- Text output ----
        click.echo(f"VERDICT: {verdict}\n")
        click.echo(f"Conventions detected in {project}:\n")

        # -- Naming --
        click.echo("=== Naming ===")
        if naming_summary:
            for group, info in sorted(naming_summary.items()):
                click.echo(
                    f"  {group.capitalize()}: {info['dominant_style']} ({info['percent']}% of {info['total']} {group})"
                )
                # Show minority styles if present
                for style, count in info["breakdown"].items():
                    if style != info["dominant_style"] and count >= 2:
                        pct = round(100 * count / info["total"], 1)
                        click.echo(f"    also: {style} ({pct}%, {count})")
        else:
            click.echo("  (no classifiable symbols found)")

        if outliers:
            click.echo(f"\n  Outliers ({len(outliers)} total):")
            for o in outliers[:max_outliers]:
                click.echo(
                    f"    {o['name']} ({o['actual_style']}, "
                    f"expected {o['expected_style']}) "
                    f"at {loc(o['file'], o['line'])}"
                )
            if len(outliers) > max_outliers:
                click.echo(f"    (+{len(outliers) - max_outliers} more)")

        if affixes["prefixes"] or affixes["suffixes"]:
            click.echo("\n  Common affixes:")
            for p in affixes["prefixes"][:5]:
                click.echo(f"    prefix {p['affix']}  ({p['count']} symbols, {p['percent']}%)")
            for s in affixes["suffixes"][:5]:
                click.echo(f"    suffix {s['affix']}  ({s['count']} symbols, {s['percent']}%)")

        # -- File organization --
        click.echo(f"\n=== File Organization ({file_info['total_files']} files) ===")
        if file_info["top_dirs"]:
            dir_rows = [[d["dir"], str(d["count"])] for d in file_info["top_dirs"]]
            click.echo(format_table(["Directory", "Files"], dir_rows))
        if file_info["test_patterns"]:
            click.echo(f"\n  Test files: {file_info['test_file_count']} detected")
            for tp in file_info["test_patterns"]:
                click.echo(f"    {tp['pattern']} ({tp['count']} files)")
            if file_info["test_dirs"]:
                dirs = ", ".join(d["dir"] for d in file_info["test_dirs"])
                click.echo(f"    in: {dirs}")
        else:
            click.echo("  Tests: (no standard test file patterns detected)")
        if file_info["has_barrels"]:
            click.echo(f"  Barrel/index files: {file_info['barrel_files']}")

        # -- Import style --
        click.echo(f"\n=== Import Style ({import_info['total_import_edges']} import edges) ===")
        if import_info["total_import_edges"] > 0:
            click.echo(
                f"  {import_info['style'].capitalize()} imports preferred "
                f"({import_info['absolute_pct']}% cross-directory, "
                f"{import_info['relative_pct']}% same-directory)"
            )
        else:
            click.echo("  (no import edges found)")

        # -- Error handling --
        click.echo("\n=== Error Handling ===")
        if error_info["error_symbol_count"] > 0:
            click.echo(
                f"  {error_info['error_symbol_count']} error-related symbols "
                f"({error_info['error_classes']} classes, "
                f"{error_info['error_functions']} functions)"
            )
            for es in error_info["error_symbols"][:5]:
                click.echo(f"    {es['name']} ({abbrev_kind(es['kind'])}) at {loc(es['file'], es['line'])}")
            if len(error_info["error_symbols"]) > 5:
                click.echo(f"    (+{len(error_info['error_symbols']) - 5} more)")
        else:
            click.echo("  (no error/exception symbols detected)")
        if error_info["files_with_complexity"] > 0:
            click.echo(f"  Avg file complexity: {error_info['avg_complexity']} (max {error_info['max_complexity']})")

        # -- Export pattern --
        click.echo(f"\n=== Export Pattern ({export_info['total_symbols']} symbols) ===")
        if export_info["total_symbols"] > 0:
            click.echo(f"  Exported: {export_info['exported']} ({export_info['exported_pct']}%)")
            click.echo(
                f"  Private:  {export_info['private']} "
                f"({round(100 * export_info['private'] / export_info['total_symbols'], 1)}%)"
            )
            if export_info["by_kind"]:
                ek_rows = [
                    [
                        abbrev_kind(k["kind"]),
                        str(k["total"]),
                        str(k["exported"]),
                        f"{k['exported_pct']}%",
                    ]
                    for k in export_info["by_kind"]
                ]
                click.echo(format_table(["Kind", "Total", "Exported", "Rate"], ek_rows))
            if export_info["js_ts_export_style"] != "unknown":
                click.echo(f"  JS/TS: {export_info['js_ts_export_style']}")
        else:
            click.echo("  (no symbols found)")
