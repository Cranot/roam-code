"""Compute risk score for pending changes."""

from __future__ import annotations

import hashlib
import json as _json
import math
import sqlite3
import subprocess
import time
from typing import Any

import click

from roam.capability import roam_capability
from roam.commands.changed_files import (
    get_changed_files,
    is_low_risk_file,
    is_test_file,
    resolve_changed_to_db,
)
from roam.commands.cmd_coupling import _compute_surprise
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import format_table, json_envelope, to_json


# W134 — pr-risk is the sixth detector migrating onto the central findings
# registry (after clones W95, dead W99, complexity W102, smells W109,
# bus-factor W115). Unlike those five — which scan workspace state —
# pr-risk is INVOCATION-SCOPED: a run produces findings tied to a specific
# diff (commit range / staged set / unstaged set) at the moment of
# invocation. The diff id is stamped into ``evidence_json`` so a consumer
# can distinguish fresh rows from rows tied to a since-merged PR.
#
# Confidence tiers per kind (see _PR_RISK_KIND_TO_CONFIDENCE below):
# * ``composite-risk-score`` — the headline 0-100 score is a multiplicative
#   blend of eight fuzzy factors; fundamentally heuristic.
# * ``high-blast-radius-symbol-touched`` — derived from reverse-graph
#   descendants over the symbol DAG; deterministic structural signal.
# * ``test-coverage-gap`` — derived from file_edges (test files importing
#   the changed file); a structural graph + file-role pattern.
# * ``author-novelty-flag`` — author-familiarity is a time-decayed churn
#   rollup; heuristic.
#
# Bump this version when the composite weights / thresholds or the kind
# emit rules change meaningfully so registry consumers can spot rows
# produced under an older shape.
PR_RISK_DETECTOR_VERSION: str = "1.0.0"


# W134 — per-kind confidence tier mapping. Mirrors the W109 smells pattern:
# every emitted kind picks a tier from the central CONFIDENCE_* enum so a
# downstream consumer can weight signals without re-deriving the rule.
_PR_RISK_KIND_TO_CONFIDENCE: dict[str, str] = {
    "composite-risk-score": "heuristic",
    "high-blast-radius-symbol-touched": "structural",
    "test-coverage-gap": "structural",
    "author-novelty-flag": "heuristic",
}


def _diff_id(
    *,
    label: str,
    commit_range: str | None,
    staged: bool,
    file_paths: list[str],
) -> str:
    """Stable id for one pr-risk invocation's diff.

    Folds the diff source (commit_range / staged / unstaged), the sorted
    list of changed file paths, and an explicit label into a sha1 prefix.
    Two invocations against the same diff produce the same id (so
    ``--persist`` upserts in place); changing any of the inputs — even
    just adding one more changed file — produces a fresh id so the prior
    finding stays as an audit-trail row.
    """
    raw = (
        f"{label}|range={commit_range or ''}|staged={int(staged)}|"
        f"files={','.join(sorted(file_paths))}"
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _pr_risk_finding_id(kind: str, diff_id: str, suffix: str = "") -> str:
    """Stable, deterministic finding id for one pr-risk row.

    ``kind`` discriminates the four sub-kinds; ``diff_id`` ties the
    finding to a specific invocation's diff so reruns on the same diff
    upsert. ``suffix`` is appended for kinds that emit per-symbol /
    per-file rows (currently unused — the composite, blast, coverage,
    and novelty kinds are all invocation-scoped). Convention:
    ``"pr-risk:<kind>:<diff_id>[:<suffix_digest>]"``.
    """
    if suffix:
        sfx_digest = hashlib.sha1(suffix.encode("utf-8")).hexdigest()[:8]
        return f"pr-risk:{kind}:{diff_id}:{sfx_digest}"
    return f"pr-risk:{kind}:{diff_id}"


# W134 → W242 → W718 — risk-level → severity mapping for the composite row.
# The headline composite kind is invocation-scoped; severity follows
# the bucketed risk level (low/moderate/high/critical → low/medium/
# high/critical). The conditional sub-kinds carry their own severity
# that reflects what triggered the emit (high blast → high; coverage
# gap → medium; novelty → medium).
#
# W718: keys are lowercase to match the canonical roam severity
# vocabulary (W547 ``roam.output._severity``). Pre-W718 callers /
# fixtures that pass UPPER-cased ``level`` strings (``"CRITICAL"``,
# ``"HIGH"``, ``"MODERATE"``, ``"LOW"``) are normalised at the lookup
# boundary via :func:`_normalise_pr_risk_level`. ``moderate`` is a
# pr-risk-domain bucket label (the score window 25 < risk ≤ 50) that
# maps to the canonical SARIF ``medium`` severity tier — kept as a
# distinct level so the human-readable verdict can say "Moderate risk"
# without ambiguity with the SARIF mid-tier.
_PR_RISK_LEVEL_TO_SEVERITY: dict[str, str] = {
    "low": "low",
    "moderate": "medium",
    "high": "high",
    "critical": "critical",
}


# W989 (Pattern 2 — silent fallback): the canonical pr-risk bucket
# vocabulary re-expressed as a closed set. Mirrors W969's
# ``_CANONICAL_LEVELS`` discipline in ``cmd_alerts.py``. ``_coerce_risk_level``
# (below) is the single boundary that accepts canonical lowercase silently,
# coerces UPPER-cased silently (W718 / W649 back-compat), and warns + defaults
# on anything else.
#
# Kept in sync (by hand, like the LAW 4 anchor lists) with the
# ``_PR_RISK_LEVEL_TO_SEVERITY`` keys above — adding a new bucket means
# updating BOTH this frozenset AND the severity mapping AND the bucketing
# logic in :func:`pr_risk` (the risk-score thresholds at ~line 1000). The
# drift-guard test in ``tests/test_w989_pr_risk_pattern2.py`` pins the
# frozenset-vs-mapping equality so a one-sided edit fails at CI time.
_VALID_RISK_LEVELS: frozenset[str] = frozenset(_PR_RISK_LEVEL_TO_SEVERITY)


def _coerce_risk_level(
    value: Any,
    default: str,
    *,
    field_name: str,
    warnings_out: list[str] | None,
) -> str:
    """W989 (Pattern 2 — silent fallback): coerce a pr-risk ``level`` scalar.

    Mirrors W969's ``_coerce_level`` from ``cmd_alerts.py``: validates against
    the canonical pr-risk vocabulary ``{"low", "moderate", "high", "critical"}``
    and surfaces an actionable warning on unknown input.

    - returns the value untouched when it is already canonical lowercase
      (happy path);
    - lowercases + accepts when it is canonical when lowercased (handles
      pre-W718 UPPER-cased fixtures silently — they round-trip to canonical
      lowercase without a warning);
    - appends an actionable warning AND returns *default* for any other shape
      (unknown string, int, list, None, ...). Pattern 2 discipline: name the
      offending field, name the value, name the resolution and the valid
      spellings.

    The W718 CI-safety floor (default to ``"low"`` for unknown / None) is
    preserved — a typo'd label MUST NOT promote a finding into a CI-failing
    rank — but the silent fallback now surfaces as a structured warning so
    consumers can tell a defaulted level apart from a genuinely-low risk.
    """
    if isinstance(value, str):
        if value in _VALID_RISK_LEVELS:
            return value
        lowered = value.strip().lower()
        if lowered in _VALID_RISK_LEVELS:
            return lowered
    if warnings_out is not None:
        warnings_out.append(
            f"Config field {field_name!r} value {value!r} is not a valid "
            f"pr-risk level (must be one of {sorted(_VALID_RISK_LEVELS)}); "
            f"defaulting to {default!r}."
        )
    return default


def _normalise_pr_risk_level(level: str | None) -> str:
    """Canonicalise a pr-risk ``level`` string to lowercase (W718).

    Returns one of ``"low"`` / ``"moderate"`` / ``"high"`` /
    ``"critical"`` when the input matches a known bucket (case-
    insensitive), or ``"low"`` as the CI-safety floor for unknown /
    None inputs (the W531 lesson: a typo'd label must NOT promote a
    finding into a CI-failing rank). Pre-W718 fixtures that pass
    UPPER-cased ``level`` strings keep working unchanged.

    W989: now delegates to :func:`_coerce_risk_level` so the closed-set
    validation has a single source of truth. This wrapper preserves the
    pre-W989 signature (no ``warnings_out``) for back-compat with the
    23 callers in :func:`_build_pr_risk_finding_rows` and elsewhere; new
    call sites that want to surface the silent-fallback signal should
    call ``_coerce_risk_level`` directly with a ``warnings_out`` accumulator.
    """
    return _coerce_risk_level(
        level,
        default="low",
        field_name="level",
        warnings_out=None,
    )


def _build_pr_risk_finding_rows(
    data: dict,
    source_version: str,
    *,
    warnings_out: list[str] | None = None,
) -> list[dict]:
    """Build the W134 finding row dicts from one pr-risk invocation's data.

    Returns a list of dicts in the W134 registry shape — the SAME shape
    that both ``--persist`` writes to the central findings table AND
    that the JSON envelope stamps at ``envelope["findings"]`` (W242).
    Extracted so both paths are pure transcriptions of one source of
    truth: change the row shape here, and both surfaces follow.

    Each row dict carries the canonical W134 fields plus the
    threshold/severity metadata the agent-OS evidence layer expects:

    * ``finding_id_str`` — deterministic id (stable across reruns on
      the same diff so registry upserts in place).
    * ``source_detector`` / ``source_version`` — provenance.
    * ``subject_kind="commit"`` / ``subject_id=None`` — pr-risk is
      invocation-scoped; the diff doesn't map to a ``symbols.id`` row.
    * ``confidence`` — closed-enum tier from ``_PR_RISK_KIND_TO_CONFIDENCE``.
    * ``claim`` — short human-readable verdict.
    * ``kind`` — ``"pr-risk:<bare_kind>"`` namespaced label so a
      cross-detector consumer can filter without parsing
      ``finding_id_str``.
    * ``severity`` — one of ``CLAIM_SEVERITIES``
      (``critical``/``high``/``medium``/``low``/``info``).
    * ``evidence`` — detector-specific payload (mirrors what
      ``--persist`` lands in ``findings.evidence_json``).

    Threshold gating mirrors the W134 emit rules verbatim:
    * composite-risk-score — always emitted.
    * high-blast-radius-symbol-touched — emitted when ``blast_pct >= 20``.
    * test-coverage-gap — emitted when there are source files AND
      ``test_coverage < 0.5``.
    * author-novelty-flag — emitted when an author resolved AND
      familiarity was assessed AND ``familiarity_risk >= 0.10``.

    W989 (Pattern 2 — silent fallback): when *warnings_out* is supplied
    as a ``list[str]``, an unknown ``data["level"]`` value (anything outside
    the canonical ``low/moderate/high/critical`` set) appends an actionable
    warning naming the field, the value, and the valid spellings. The
    severity falls back to ``"info"`` via :data:`_PR_RISK_LEVEL_TO_SEVERITY`
    after :func:`_coerce_risk_level` floors the unknown level to ``"low"``
    (the W718 CI-safety floor). Pre-W989 callers that don't supply
    ``warnings_out`` retain the byte-identical silent-floor behaviour so
    persisted finding row hashes stay stable.
    """
    diff_id = data["diff_id"]
    label = data["label"]
    commit_range = data["commit_range"]
    staged = data["staged"]
    file_list = data["file_list"]
    created_at = int(time.time())

    # Shared invocation metadata — every row carries this so a consumer
    # can group findings by PR / commit / branch without joining back.
    base_evidence = {
        "diff_id": diff_id,
        "label": label,
        "commit_range": commit_range,
        "staged": bool(staged),
        "file_list": file_list,
        "changed_files_count": len(file_list),
        "created_at_epoch": created_at,
    }

    rows: list[dict] = []

    # --- Always-emitted: the composite risk score ---
    composite_evidence = {
        **base_evidence,
        "risk_score": data["risk"],
        "risk_level": data["level"],
        "blast_radius_pct": round(data["blast_pct"], 1),
        "hotspot_score": round(data["hotspot_score"], 3),
        "test_coverage_pct": round(data["test_coverage"] * 100, 1),
        "bus_factor_risk": round(data["bus_factor_risk"], 3),
        "coupling_score": round(data["coupling_score"], 3),
        "novelty_score": data["novelty"],
        "familiarity_risk": round(data["familiarity_risk"], 3),
        "minor_risk": round(data["minor_risk"], 3),
        "reductive_change": bool(data["reductive_change"]),
        "top_driver": data["driver_label"],
        "lines_added": data["total_added"],
        "lines_removed": data["total_removed"],
        # W198 vocabulary drift fix: ``author`` is the git-blame term
        # (kept for back-compat); ``actor`` is the agentic-assurance
        # crosswalk term (W182 ``ActorRef``). Both carry the same value
        # so the ``ChangeEvidence`` collector never sees two synonyms
        # downstream — it picks one canonical key without losing the
        # original.
        "author": data["resolved_author"],
        "actor": data["resolved_author"],
    }
    composite_claim = (
        f"pr-risk: {data['level']} ({data['risk']}/100) on {label}"
        + (f" — driver: {data['driver_label']}" if data["driver_label"] else "")
    )
    # W989: route through _coerce_risk_level so an unknown level surfaces
    # via warnings_out (when supplied) instead of being a silent floor.
    # When warnings_out is None, the helper preserves the pre-W989 silent
    # behaviour byte-for-byte so persisted finding row hashes stay stable.
    _canonical_level = _coerce_risk_level(
        data["level"],
        default="low",
        field_name="level",
        warnings_out=warnings_out,
    )
    rows.append({
        "finding_id_str": _pr_risk_finding_id("composite-risk-score", diff_id),
        "source_detector": "pr-risk",
        "source_version": source_version,
        "subject_kind": "commit",
        "subject_id": None,
        "confidence": _PR_RISK_KIND_TO_CONFIDENCE["composite-risk-score"],
        "claim": composite_claim,
        "kind": "pr-risk:composite-risk-score",
        "severity": _PR_RISK_LEVEL_TO_SEVERITY.get(_canonical_level, "info"),
        "evidence": composite_evidence,
    })

    # --- Conditional: high-blast-radius-symbol-touched ---
    # Threshold matches the multiplicative factor cap in the composite
    # weight (40% of repo symbols affected → factor saturates). Below
    # that the blast signal is too small to surface as its own finding.
    if data["blast_pct"] >= 20.0:
        blast_evidence = {
            **base_evidence,
            "blast_radius_pct": round(data["blast_pct"], 1),
            "affected_symbols": data["affected_count"],
            "total_symbols": data["total_syms_repo"],
            "changed_symbol_ids_count": data["changed_syms_count"],
        }
        blast_claim = (
            f"High blast radius: {data['affected_count']} of "
            f"{data['total_syms_repo']} symbols affected "
            f"({data['blast_pct']:.1f}%) on {label}"
        )
        rows.append({
            "finding_id_str": _pr_risk_finding_id(
                "high-blast-radius-symbol-touched", diff_id
            ),
            "source_detector": "pr-risk",
            "source_version": source_version,
            "subject_kind": "commit",
            "subject_id": None,
            "confidence": _PR_RISK_KIND_TO_CONFIDENCE[
                "high-blast-radius-symbol-touched"
            ],
            "claim": blast_claim,
            "kind": "pr-risk:high-blast-radius-symbol-touched",
            "severity": "high",
            "evidence": blast_evidence,
        })

    # --- Conditional: test-coverage-gap ---
    # Only emit when there were source files to assess AND coverage is
    # below 50% — at 100% coverage there's no gap; with no source files
    # (e.g., docs-only PR) the metric is N/A.
    if data["source_files_count"] > 0 and data["test_coverage"] < 0.5:
        gap_evidence = {
            **base_evidence,
            "test_coverage_pct": round(data["test_coverage"] * 100, 1),
            "covered_files": data["covered_files"],
            "source_files_count": data["source_files_count"],
            "uncovered_files": data["source_files_count"] - data["covered_files"],
        }
        gap_claim = (
            f"Test coverage gap: {data['covered_files']} of "
            f"{data['source_files_count']} changed source files have "
            f"adjacent tests ({data['test_coverage'] * 100:.0f}% covered) on {label}"
        )
        rows.append({
            "finding_id_str": _pr_risk_finding_id("test-coverage-gap", diff_id),
            "source_detector": "pr-risk",
            "source_version": source_version,
            "subject_kind": "commit",
            "subject_id": None,
            "confidence": _PR_RISK_KIND_TO_CONFIDENCE["test-coverage-gap"],
            "claim": gap_claim,
            "kind": "pr-risk:test-coverage-gap",
            "severity": "medium",
            "evidence": gap_evidence,
        })

    # --- Conditional: author-novelty-flag ---
    # Only emit when we have a resolved author AND familiarity was
    # actually assessed AND the risk is meaningful (>= 0.10 on the
    # 0-0.25 scale, i.e. avg_familiarity below ~0.6).
    fam_assessed = (data["familiarity_details"] or {}).get("files_assessed", 0)
    if (
        data["resolved_author"]
        and fam_assessed > 0
        and data["familiarity_risk"] >= 0.10
    ):
        novelty_evidence = {
            **base_evidence,
            "familiarity_risk": round(data["familiarity_risk"], 3),
            "avg_familiarity": (data["familiarity_details"] or {}).get(
                "avg_familiarity"
            ),
            "files_assessed": fam_assessed,
            "files_familiar": (data["familiarity_details"] or {}).get(
                "files_familiar", 0
            ),
            # W198: see composite_evidence for the rationale — author is
            # git-blame vocabulary; actor is the agentic-assurance
            # crosswalk term. Both carry the same value.
            "author": data["resolved_author"],
            "actor": data["resolved_author"],
        }
        novelty_claim = (
            f"Author novelty: {data['resolved_author']} is unfamiliar with "
            f"{fam_assessed - (data['familiarity_details'] or {}).get('files_familiar', 0)}"
            f" of {fam_assessed} changed files on {label}"
        )
        rows.append({
            "finding_id_str": _pr_risk_finding_id("author-novelty-flag", diff_id),
            "source_detector": "pr-risk",
            "source_version": source_version,
            "subject_kind": "commit",
            "subject_id": None,
            "confidence": _PR_RISK_KIND_TO_CONFIDENCE["author-novelty-flag"],
            "claim": novelty_claim,
            "kind": "pr-risk:author-novelty-flag",
            "severity": "medium",
            "evidence": novelty_evidence,
        })

    return rows


def _emit_pr_risk_findings(
    conn: sqlite3.Connection,
    data: dict,
    source_version: str,
) -> int:
    """Mirror pr-risk's invocation result into the central findings registry.

    Returns the count of finding rows written. ``data`` is the dict of
    pre-computed signals built in the main ``pr_risk`` body — it's
    passed in rather than recomputed so the persist path stays a pure
    transcription of what the read path already calculated.

    The registry uses ``subject_kind="commit"`` for every kind: pr-risk
    operates on a changeset (commit range / staged set / unstaged set),
    not on a static workspace symbol. ``subject_id`` stays NULL because
    a diff doesn't map to a ``symbols.id`` row. The ``diff_id`` in
    ``evidence_json`` is what disambiguates one PR from another.

    Caller commits the transaction. emit_finding does not commit on its
    own (matches the W95 / W99 / W102 / W109 / W115 convention).

    Wrapped at the call site in try/except so a pre-W89 DB (no
    ``findings`` table) silently no-ops rather than crashing the
    standard read path.

    W242 single-source refactor: this helper now delegates to
    :func:`_build_pr_risk_finding_rows` for the row dicts and only
    handles the registry-write side (``FindingRecord`` construction +
    ``emit_finding`` upsert). The same row dicts are stamped at
    ``envelope["findings"]`` by the read path — so the envelope and
    the registry can never drift apart.
    """
    from roam.db.findings import FindingRecord, emit_finding

    rows = _build_pr_risk_finding_rows(data, source_version)
    written = 0
    for row in rows:
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=row["finding_id_str"],
                subject_kind=row["subject_kind"],
                subject_id=row["subject_id"],
                claim=row["claim"],
                evidence_json=_json.dumps(row["evidence"], sort_keys=True),
                confidence=row["confidence"],
                source_detector=row["source_detector"],
                source_version=row["source_version"],
            ),
        )
        written += 1

    return written


def _get_file_stat(root, path, *, staged=False, commit_range=None):
    """Get +/- line counts for a file."""
    cmd = ["git", "diff", "--numstat", "--", path]
    if commit_range:
        cmd = ["git", "diff", "--numstat", commit_range, "--", path]
    elif staged:
        cmd = ["git", "diff", "--cached", "--numstat", "--", path]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0, 0
        parts = result.stdout.strip().split("\t")
        if len(parts) >= 2:
            added = int(parts[0]) if parts[0] != "-" else 0
            removed = int(parts[1]) if parts[1] != "-" else 0
            return added, removed
    except Exception:
        pass
    return 0, 0


def _detect_author():
    """Auto-detect author name from git config. Returns None if undetectable."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _percentile(sorted_values, pct):
    """Linear-interpolated percentile from a sorted numeric list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(sorted_values[lo])
    frac = k - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _smoothstep01(x: float) -> float:
    """Smooth interpolation in [0,1] with flatter tails."""
    x = _clamp01(x)
    return x * x * (3.0 - 2.0 * x)


def _calibrated_hotspot_score(avg_changed_churn: float, repo_churn_sorted: list[float]) -> float:
    """Map changed-file churn to [0,1] using repo-relative percentiles.

    - 0.0 at roughly repo median churn
    - 1.0 around repo p90 churn
    - Smooth interpolation between
    """
    if avg_changed_churn <= 0 or not repo_churn_sorted:
        return 0.0

    p50 = _percentile(repo_churn_sorted, 50)
    p90 = _percentile(repo_churn_sorted, 90)

    if p90 <= p50:
        # Degenerate distribution fallback.
        denom = max(1.0, p50 * 3.0)
        return _clamp01(avg_changed_churn / denom)

    if avg_changed_churn <= p50:
        raw = 0.5 * (avg_changed_churn / max(p50, 1.0))
    else:
        raw = 0.5 + 0.5 * ((avg_changed_churn - p50) / (p90 - p50))
    return _smoothstep01(raw)


def _author_count_risk(author_counts: list[int]) -> float:
    """Continuous bus-factor risk from distinct-author counts per file.

    Per-file risk is 1/N authors, then averaged across changed files.
    This preserves intuitive anchors:
      N=1 -> 1.0, N=2 -> 0.5, N=4 -> 0.25.
    """
    if not author_counts:
        return 0.0
    inv_counts = [1.0 / max(c, 1) for c in author_counts]
    return _clamp01(sum(inv_counts) / len(inv_counts))


def _author_familiarity(conn, author, changed_files):
    """Calculate how familiar the author is with each changed file.

    familiarity(author, file) = sum(
        (lines_added + lines_removed) * exp(-0.005 * days_since)
        for each commit by author to file
    )
    normalized = author_familiarity / max(all_authors_familiarity_for_file)
    familiarity_risk = 1.0 - avg(normalized across changed files)

    Half-life: ~139 days (4.6 months).
    Returns: (risk_score 0-0.25, details_dict)
    """
    now = int(time.time())
    decay_rate = 0.005  # per day; half-life ~139 days

    normalized_scores = []
    file_details = []

    for path, fid in changed_files.items():
        if is_test_file(path) or is_low_risk_file(path):
            continue

        # Get all commits touching this file with per-author churn
        rows = conn.execute(
            "SELECT gc.author, gc.timestamp, gfc.lines_added, gfc.lines_removed "
            "FROM git_file_changes gfc "
            "JOIN git_commits gc ON gfc.commit_id = gc.id "
            "WHERE gfc.file_id = ?",
            (fid,),
        ).fetchall()

        if not rows:
            # No git history for this file — treat as unfamiliar
            normalized_scores.append(0.0)
            file_details.append({"file": path, "familiarity": 0.0})
            continue

        # Accumulate time-decayed churn per author
        author_familiarity = {}
        for r in rows:
            a = r["author"] or ""
            days_since = max(0, (now - (r["timestamp"] or 0)) / 86400)
            churn = (r["lines_added"] or 0) + (r["lines_removed"] or 0)
            weight = churn * math.exp(-decay_rate * days_since)
            author_familiarity[a] = author_familiarity.get(a, 0.0) + weight

        max_fam = max(author_familiarity.values()) if author_familiarity else 0.0
        my_fam = author_familiarity.get(author, 0.0)

        if max_fam > 0:
            norm = my_fam / max_fam
        else:
            norm = 0.0

        normalized_scores.append(norm)
        file_details.append(
            {
                "file": path,
                "familiarity": round(norm, 3),
            }
        )

    if not normalized_scores:
        return 0.0, {"avg_familiarity": 1.0, "files_assessed": 0, "files": []}

    avg_norm = sum(normalized_scores) / len(normalized_scores)
    familiar_count = sum(1 for s in normalized_scores if s >= 0.5)
    risk = (1.0 - avg_norm) * 0.25  # scale to 0-0.25

    details = {
        "avg_familiarity": round(avg_norm, 3),
        "files_assessed": len(normalized_scores),
        "files_familiar": familiar_count,
        "files": file_details,
    }
    return risk, details


def _minor_contributor_risk(conn, author, changed_files):
    """Check if author is a minor contributor to each changed file.

    Minor = author's churn < 5% of file's total_churn.
    Fraction of "minor" files * 0.15 = risk contribution.
    Returns: (risk_score 0-0.15, details_dict)
    """
    minor_count = 0
    assessed = 0
    file_details = []

    for path, fid in changed_files.items():
        if is_test_file(path) or is_low_risk_file(path):
            continue

        # Get total churn for this file
        fs_row = conn.execute(
            "SELECT total_churn FROM file_stats WHERE file_id = ?",
            (fid,),
        ).fetchone()
        total_churn = (fs_row["total_churn"] or 0) if fs_row else 0

        if total_churn == 0:
            # No churn data — can't assess, skip
            continue

        # Get author's churn on this file
        author_row = conn.execute(
            "SELECT COALESCE(SUM(gfc.lines_added), 0) + COALESCE(SUM(gfc.lines_removed), 0) AS churn "
            "FROM git_file_changes gfc "
            "JOIN git_commits gc ON gfc.commit_id = gc.id "
            "WHERE gfc.file_id = ? AND gc.author = ?",
            (fid, author),
        ).fetchone()
        author_churn = author_row["churn"] if author_row else 0

        assessed += 1
        is_minor = author_churn < (total_churn * 0.05)
        if is_minor:
            minor_count += 1

        file_details.append(
            {
                "file": path,
                "author_churn": author_churn,
                "total_churn": total_churn,
                "pct": round(author_churn * 100 / total_churn, 1) if total_churn else 0,
                "is_minor": is_minor,
            }
        )

    if assessed == 0:
        return 0.0, {"minor_files": 0, "files_assessed": 0, "files": []}

    minor_frac = minor_count / assessed
    risk = minor_frac * 0.15

    details = {
        "minor_files": minor_count,
        "files_assessed": assessed,
        "minor_fraction": round(minor_frac, 3),
        "files": file_details,
    }
    return risk, details


@roam_capability(
    name="pr-risk",
    category="refactoring",
    summary="Compute risk score for pending changes",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "review"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("pr-risk")
@click.argument("commit_range", required=False, default=None)
@click.option("--staged", is_flag=True, help="Analyze staged changes")
@click.option("--author", default=None, help="Author name (auto-detects via git config if omitted)")
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror this invocation's risk signals into the central findings "
        "registry (queryable via `roam findings list --detector pr-risk`). "
        "INVOCATION-SCOPED: pr-risk runs against a specific diff, so the "
        "persisted rows are tied to the commit range / staged set / "
        "unstaged set at the moment of invocation. The diff identifier is "
        "stamped into ``evidence_json.diff_id`` so consumers can "
        "distinguish fresh rows from rows tied to a since-merged PR. "
        "Reruns on the same diff upsert in place; reruns on a different "
        "diff insert fresh rows (the older rows stay as audit trail)."
    ),
)
@click.pass_context
def pr_risk(ctx, commit_range, staged, author, persist):
    """Compute risk score for pending changes.

    Analyzes blast radius, hotspot churn, bus factor, test coverage,
    coupling, author familiarity, and minor-contributor status to
    produce a single 0-100 risk score.

    Pass a COMMIT_RANGE (e.g. HEAD~3..HEAD) for committed changes,
    or use --staged for staged changes. Default: unstaged changes.

    \b
    Examples:
      roam pr-risk
      roam pr-risk --staged
      roam pr-risk HEAD~3..HEAD
      roam --json pr-risk HEAD~1..HEAD
      roam pr-risk --persist                 # mirror into findings registry

    With ``--persist``, the invocation's risk signals are mirrored into the
    central findings registry (visible via
    ``roam findings list --detector pr-risk``). Because pr-risk is
    invocation-scoped (vs the workspace-scoped clones / dead / smells
    detectors), the persisted rows carry a ``diff_id`` so they can be told
    apart from rows tied to a different PR — including rows for diffs that
    have since merged.

    See also ``preflight`` (pre-change safety), ``critique`` (post-change
    diff review), and ``affected-tests`` (which tests run for the diff).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    changed = get_changed_files(root, staged=staged, commit_range=commit_range)
    if not changed:
        label = commit_range or ("staged" if staged else "unstaged")
        if json_mode:
            # R9 API recheck: every --json exit must go through json_envelope
            # so consumers see schema_version + summary.verdict — not bare dicts.
            click.echo(
                to_json(
                    json_envelope(
                        "pr-risk",
                        summary={"verdict": "no-changes", "risk_score": 0, "label": label},
                        message=f"No changes found for {label}.",
                    )
                )
            )
        else:
            click.echo(f"No changes found for {label}.")
        return

    with open_db(readonly=not persist) as conn:
        # Map changed files to DB
        file_map = resolve_changed_to_db(conn, changed)

        if not file_map:
            if json_mode:
                # R9 API recheck: same as above — wrap with json_envelope.
                click.echo(
                    to_json(
                        json_envelope(
                            "pr-risk",
                            summary={
                                "verdict": "index-stale",
                                "risk_score": 0,
                                "hint": "Changed files not in index — run `roam index`.",
                            },
                            message="Changed files not found in index. Run `roam index` first.",
                        )
                    )
                )
            else:
                click.echo("Changed files not found in index. Run `roam index` first.")
            return

        total_syms_repo = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        diff_stats = {path: _get_file_stat(root, path, staged=staged, commit_range=commit_range) for path in file_map}

        # --- Resolve author for familiarity/minor-contributor factors ---
        resolved_author = author or _detect_author()

        # --- 1. Blast radius ---
        import networkx as nx

        from roam.graph.builder import build_symbol_graph

        G = build_symbol_graph(conn)
        RG = G.reverse()

        all_affected = set()
        changed_sym_ids = set()
        for path, fid in file_map.items():
            syms = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (fid,)).fetchall()
            for s in syms:
                changed_sym_ids.add(s["id"])
                if s["id"] in RG:
                    all_affected.update(nx.descendants(RG, s["id"]))

        blast_pct = len(all_affected) * 100 / total_syms_repo if total_syms_repo else 0

        # --- 2. Hotspot score (file churn) ---
        hotspot_score = 0.0
        churn_data = {}
        for path, fid in file_map.items():
            row = conn.execute("SELECT total_churn, commit_count FROM file_stats WHERE file_id = ?", (fid,)).fetchone()
            if row:
                churn_data[path] = {
                    "churn": row["total_churn"],
                    "commits": row["commit_count"],
                }

        if churn_data:
            # Repo-relative calibration: compare changed churn against
            # code-file churn percentiles (median..p90), not fixed cutoffs.
            code_churn = {p: d for p, d in churn_data.items() if not is_low_risk_file(p)}
            repo_churn_rows = conn.execute(
                "SELECT f.path, fs.total_churn FROM file_stats fs "
                "JOIN files f ON fs.file_id = f.id "
                "WHERE fs.total_churn IS NOT NULL"
            ).fetchall()
            repo_code_churn = sorted(
                float(r["total_churn"] or 0)
                for r in repo_churn_rows
                if (r["total_churn"] or 0) > 0 and not is_low_risk_file(r["path"])
            )
            if repo_code_churn and code_churn:
                avg_changed = sum(d["churn"] for d in code_churn.values()) / len(code_churn)
                hotspot_score = _calibrated_hotspot_score(avg_changed, repo_code_churn)

        # --- 3. Bus factor ---
        bus_factor_risk = 0.0
        min_bf = None
        author_counts = []
        for path, fid in file_map.items():
            if is_test_file(path) or is_low_risk_file(path):
                continue
            authors = conn.execute(
                "SELECT DISTINCT gc.author FROM git_file_changes gfc "
                "JOIN git_commits gc ON gfc.commit_id = gc.id "
                "WHERE gfc.file_id = ?",
                (fid,),
            ).fetchall()
            if authors:
                author_counts.append(len(authors))

        if author_counts:
            min_bf = min(author_counts)
            bus_factor_risk = _author_count_risk(author_counts)

        # --- 4. Test coverage ---
        test_coverage = 0.0
        source_files = [p for p in file_map if not is_test_file(p) and not is_low_risk_file(p)]
        source_added = sum(diff_stats.get(p, (0, 0))[0] for p in source_files)
        source_removed = sum(diff_stats.get(p, (0, 0))[1] for p in source_files)
        total_added = sum(v[0] for v in diff_stats.values())
        total_removed = sum(v[1] for v in diff_stats.values())
        reductive_change = bool(source_files and source_added == 0 and source_removed > 0)
        covered_files = 0
        for path in source_files:
            fid = file_map[path]
            # Check if any test file imports this file
            has_test = any(
                is_test_file(r["path"])
                for r in conn.execute(
                    "SELECT f.path FROM file_edges fe "
                    "JOIN files f ON fe.source_file_id = f.id "
                    "WHERE fe.target_file_id = ?",
                    (fid,),
                ).fetchall()
            )
            if has_test:
                covered_files += 1

        if source_files:
            test_coverage = covered_files / len(source_files)

        # --- 5. Coupling density ---
        coupling_score = 0.0
        if len(file_map) > 1:
            fids = list(file_map.values())
            ph = ",".join("?" for _ in fids)
            cross_edges = conn.execute(
                f"SELECT COUNT(*) FROM file_edges WHERE source_file_id IN ({ph}) AND target_file_id IN ({ph})",
                fids + fids,
            ).fetchone()[0]
            max_possible = len(fids) * (len(fids) - 1)
            if max_possible > 0:
                coupling_score = min(1.0, cross_edges / max_possible)

        # --- 6. Hypergraph novelty ---
        change_fids = list(file_map.values())
        novelty, closest_pattern, closest_sim = _compute_surprise(conn, change_fids)

        # --- 7. Structural spread (cluster + layer) ---
        cluster_ids = set()
        for fid in file_map.values():
            for r in conn.execute(
                "SELECT DISTINCT c.cluster_id FROM clusters c JOIN symbols s ON c.symbol_id = s.id WHERE s.file_id = ?",
                (fid,),
            ).fetchall():
                cluster_ids.add(r["cluster_id"])

        total_clusters = conn.execute("SELECT COUNT(DISTINCT cluster_id) FROM clusters").fetchone()[0] or 1
        cluster_spread = len(cluster_ids) / total_clusters if total_clusters > 1 else 0

        # Layer spread
        from roam.graph.layers import detect_layers

        layer_map = detect_layers(G)
        touched_layers = set()
        if layer_map:
            for sym_id in changed_sym_ids:
                if sym_id in layer_map:
                    touched_layers.add(layer_map[sym_id])
        total_layers = (max(layer_map.values()) + 1) if layer_map else 1
        layer_spread = len(touched_layers) / total_layers if total_layers > 1 else 0

        # --- 8. Dead code check ---
        new_dead = []
        for path, fid in file_map.items():
            if is_test_file(path):
                continue
            exports = conn.execute(
                "SELECT s.name, s.kind FROM symbols s "
                "WHERE s.file_id = ? AND s.is_exported = 1 "
                "AND s.id NOT IN (SELECT target_id FROM edges) "
                "AND s.kind IN ('function', 'class', 'method')",
                (fid,),
            ).fetchall()
            for e in exports:
                new_dead.append({"name": e["name"], "kind": e["kind"], "file": path})

        # --- 9. Author familiarity ---
        familiarity_risk = 0.0
        familiarity_details = {"avg_familiarity": 1.0, "files_assessed": 0, "files": []}
        if resolved_author:
            familiarity_risk, familiarity_details = _author_familiarity(
                conn,
                resolved_author,
                file_map,
            )

        # --- 10. Minor contributor risk ---
        minor_risk = 0.0
        minor_details = {"minor_files": 0, "files_assessed": 0, "files": []}
        if resolved_author:
            minor_risk, minor_details = _minor_contributor_risk(
                conn,
                resolved_author,
                file_map,
            )

        # --- Composite risk score (0-100) ---
        # Multiplicative model: each factor amplifies the base risk.
        # This captures interaction effects — high blast + untested is
        # exponentially worse than either alone, not just linearly worse.
        # log-space combination: risk = 100 * (1 - product(1 - factor_i))
        _factors = [
            min(blast_pct / 100, 0.40),  # blast radius (up to 40%)
            hotspot_score * 0.30,  # hotspot (up to 30%)
            (1 - test_coverage) * 0.30,  # untested (up to 30%)
            bus_factor_risk * 0.20,  # bus factor (up to 20%)
            coupling_score * 0.20,  # coupling (up to 20%)
            novelty * 0.15,  # novelty (up to 15%)
            familiarity_risk,  # author familiarity (up to 25%)
            minor_risk,  # minor contributor (up to 15%)
        ]
        reductive_discount = 0.0
        if reductive_change:
            # Deletion-only source changes can still be risky when they remove
            # public API, but they do not add new execution paths. Dampening
            # social/churn novelty pressure keeps verified dead-code cleanup
            # from looking like a feature change in hot files.
            _factors = [
                _factors[0] * 0.65,  # blast still matters for public removals
                _factors[1] * 0.35,
                _factors[2] * 0.75,
                _factors[3] * 0.50,
                _factors[4] * 0.65,
                _factors[5] * 0.35,
                _factors[6] * 0.50,
                _factors[7] * 0.50,
            ]
            reductive_discount = 1.0
        # Product of (1 - factor): probability of "no risk" from each
        no_risk = 1.0
        for f in _factors:
            no_risk *= 1 - max(0, min(f, 0.99))
        risk = int(min(100, (1 - no_risk) * 100))

        # W718: canonical lowercase severity vocabulary (W547). Was
        # ``LOW``/``MODERATE``/``HIGH``/``CRITICAL`` pre-W718 — the
        # lowercase form is the only spelling that reaches the JSON
        # envelope, the findings registry, and the ``_PR_RISK_LEVEL_TO_SEVERITY``
        # lookup. ``moderate`` stays as a distinct pr-risk bucket label
        # (25 < risk ≤ 50) that projects to canonical ``medium`` severity.
        if risk <= 25:
            level = "low"
        elif risk <= 50:
            level = "moderate"
        elif risk <= 75:
            level = "high"
        else:
            level = "critical"

        # --- Per-file risk breakdown ---
        per_file = []
        for path, fid in file_map.items():
            syms = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (fid,)).fetchall()
            file_affected = set()
            for s in syms:
                if s["id"] in RG:
                    file_affected.update(nx.descendants(RG, s["id"]))
            churn = churn_data.get(path, {})
            per_file.append(
                {
                    "path": path,
                    "symbols": len(syms),
                    "blast": len(file_affected),
                    "churn": churn.get("churn", 0),
                    "lines_added": diff_stats.get(path, (0, 0))[0],
                    "lines_removed": diff_stats.get(path, (0, 0))[1],
                    "is_test": is_test_file(path),
                }
            )
        per_file.sort(key=lambda x: x["blast"], reverse=True)

        # --- Suggested reviewers ---
        author_lines = {}
        for path, fid in file_map.items():
            if is_test_file(path):
                continue
            rows = conn.execute(
                "SELECT gc.author, gfc.lines_added FROM git_file_changes gfc "
                "JOIN git_commits gc ON gfc.commit_id = gc.id "
                "WHERE gfc.file_id = ?",
                (fid,),
            ).fetchall()
            for r in rows:
                author_lines[r["author"]] = author_lines.get(r["author"], 0) + (r["lines_added"] or 0)
        top_authors = sorted(author_lines.items(), key=lambda x: -x[1])[:5]

        label = commit_range or ("staged" if staged else "unstaged")

        # — name the risk driver. The bare verdict said
        # "High risk (60/100) — careful review needed" without
        # telling the user *why*. The largest single factor
        # is the most useful pointer: maps directly to a fix
        # ("test_coverage low" → write tests; "hotspot" → focus
        # review there; "bus_factor" → loop in maintainer).
        _named_factors = [
            ("blast_pct", min(blast_pct / 100, 0.40)),
            ("hotspot_score", hotspot_score * 0.30),
            ("test_coverage_low", (1 - test_coverage) * 0.30),
            ("bus_factor", bus_factor_risk * 0.20),
            ("coupling", coupling_score * 0.20),
            ("novelty", novelty * 0.15),
            ("familiarity", familiarity_risk),
            ("minor_contributor", minor_risk),
        ]
        top_driver = max(_named_factors, key=lambda x: x[1])
        driver_label = top_driver[0] if top_driver[1] > 0.05 else None

        # Verdict
        if level == "low":
            verdict = f"Low risk ({risk}/100) — safe to merge"
        elif level == "moderate":
            verdict = f"Moderate risk ({risk}/100) — review recommended"
        elif level == "high":
            verdict = f"High risk ({risk}/100) — careful review needed"
        else:
            verdict = f"Critical risk ({risk}/100) — significant blast radius, thorough review required"
        if driver_label:
            verdict += f" (driver: {driver_label})"

        # --- W134 / W242: build the shared finding-row payload ---
        # INVOCATION-SCOPED: pr-risk runs against a specific diff, so the
        # finding rows carry a ``diff_id`` (sha1 of label + commit_range
        # + staged-flag + sorted file paths) inside their evidence.
        # The same row list is the SINGLE SOURCE OF TRUTH for both
        # ``--persist`` (mirror to the central findings registry) AND
        # the JSON envelope's top-level ``findings[]`` array (W242).
        # Building it unconditionally keeps the two surfaces in lockstep:
        # the envelope rows match what a subsequent ``--persist`` run
        # would land in ``findings``.
        file_list_for_id = sorted(file_map.keys())
        diff_id_val = _diff_id(
            label=label,
            commit_range=commit_range,
            staged=bool(staged),
            file_paths=file_list_for_id,
        )
        _pr_risk_data = {
            "diff_id": diff_id_val,
            "label": label,
            "commit_range": commit_range,
            "staged": bool(staged),
            "file_list": file_list_for_id,
            "risk": risk,
            "level": level,
            "blast_pct": blast_pct,
            "hotspot_score": hotspot_score,
            "test_coverage": test_coverage,
            "bus_factor_risk": bus_factor_risk,
            "coupling_score": coupling_score,
            "novelty": novelty,
            "familiarity_risk": familiarity_risk,
            "minor_risk": minor_risk,
            "reductive_change": reductive_change,
            "driver_label": driver_label,
            "total_added": total_added,
            "total_removed": total_removed,
            "resolved_author": resolved_author,
            "affected_count": len(all_affected),
            "total_syms_repo": total_syms_repo,
            "changed_syms_count": len(changed_sym_ids),
            "source_files_count": len(source_files),
            "covered_files": covered_files,
            "familiarity_details": familiarity_details,
        }
        # W989: collect Pattern-2 silent-fallback warnings while building
        # the finding rows. ``level`` is computed internally (the bucketing
        # at ~line 1000), so this accumulator should stay empty on the
        # happy path; non-empty means the canonical-level invariant got
        # broken upstream and the envelope MUST flip ``partial_success``.
        _warnings_out: list[str] = []
        finding_rows = _build_pr_risk_finding_rows(
            _pr_risk_data,
            PR_RISK_DETECTOR_VERSION,
            warnings_out=_warnings_out,
        )

        # --- W134: mirror into the central findings registry ---
        # Reruns on the same diff upsert in place; reruns on a different
        # diff insert fresh rows so consumers can tell findings apart from
        # rows tied to a since-merged PR. Wrapped in try/except so a
        # pre-W89 schema (no ``findings`` table) degrades cleanly.
        if persist:
            try:
                _emit_pr_risk_findings(
                    conn,
                    _pr_risk_data,
                    PR_RISK_DETECTOR_VERSION,
                )
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

        if json_mode:
            # W989 (Pattern 2): surface accumulated silent-fallback warnings
            # on the envelope. ``partial_success`` flips True iff any warning
            # fired — mirrors the cmd_alerts ``warnings_out`` discipline.
            _summary: dict[str, Any] = {
                "verdict": verdict,
                "risk_score": risk,
                "risk_level": level,
                "changed_files": len(file_map),
                "change_shape": "reductive" if reductive_change else "mixed",
                "lines_added": total_added,
                "lines_removed": total_removed,
                "findings_count": len(finding_rows),
            }
            if _warnings_out:
                _summary["partial_success"] = True
                _summary["warnings_count"] = len(_warnings_out)
            click.echo(
                to_json(
                    json_envelope(
                        "pr-risk",
                        summary=_summary,
                        # W242: top-level ``findings[]`` carrying the W134
                        # row shape — the same rows ``--persist`` writes to
                        # the central findings registry. Built from a single
                        # source (``_build_pr_risk_finding_rows``) so the
                        # envelope and the registry can never drift. The
                        # collector's ``pr_risk_envelope`` kwarg expects
                        # exactly this key.
                        findings=finding_rows,
                        label=label,
                        risk_score=risk,
                        risk_level=level,
                        changed_files=len(file_map),
                        change_shape="reductive" if reductive_change else "mixed",
                        reductive_change=reductive_change,
                        reductive_discount_applied=bool(reductive_discount),
                        lines_added=total_added,
                        lines_removed=total_removed,
                        blast_radius_pct=round(blast_pct, 1),
                        hotspot_score=round(hotspot_score, 2),
                        test_coverage_pct=round(test_coverage * 100, 1),
                        bus_factor_risk=round(bus_factor_risk, 2),
                        coupling_score=round(coupling_score, 2),
                        novelty_score=novelty,
                        closest_similarity=closest_sim,
                        closest_historical_pattern=closest_pattern,
                        cluster_spread=round(cluster_spread, 2),
                        clusters_touched=len(cluster_ids),
                        total_clusters=total_clusters,
                        layer_spread=round(layer_spread, 2),
                        layers_touched=len(touched_layers),
                        total_layers=total_layers,
                        dead_exports=len(new_dead),
                        familiarity=familiarity_details,
                        minor_risk=minor_details,
                        # W198 vocabulary drift fix: ``author`` is the
                        # git-blame term kept for back-compat; ``actor``
                        # mirrors the W182 ``ActorRef`` crosswalk
                        # vocabulary so ``ChangeEvidence`` collectors
                        # don't carry two synonyms downstream.
                        author=resolved_author,
                        actor=resolved_author,
                        per_file=per_file,
                        # W198: each reviewer row carries both ``author``
                        # (git-blame) and ``actor`` (crosswalk) for the
                        # same identity. See composite envelope above.
                        suggested_reviewers=[
                            {"author": a, "actor": a, "lines": l}
                            for a, l in top_authors
                        ],
                        dead_code=new_dead[:10],
                        # W989 (Pattern 2): structured silent-fallback
                        # warnings. Empty list on the happy path; non-empty
                        # means the canonical-level invariant got broken
                        # upstream (level NOT in
                        # {"low","moderate","high","critical"}) — the row
                        # severity defaulted to "info" and ``summary.
                        # partial_success`` flipped True. Mirrors the
                        # cmd_alerts ``warnings_out`` discipline.
                        warnings_out=list(_warnings_out),
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}\n")
        click.echo(f"=== PR Risk ({label}) ===\n")
        click.echo(f"Risk Score: {risk}/100 ({level})")
        if reductive_change:
            click.echo(
                f"Change shape: deletion-only source change (+{source_added}/-{source_removed}); reductive rubric applied"
            )
        click.echo()

        click.echo("Breakdown:")
        click.echo(f"  Blast radius:  {blast_pct:5.1f}%  (affected {len(all_affected)} of {total_syms_repo} symbols)")
        click.echo(f"  Hotspot score: {hotspot_score * 100:5.1f}%  {'(hot files!)' if hotspot_score > 0.5 else ''}")
        click.echo(
            f"  Test coverage: {test_coverage * 100:5.1f}%  ({covered_files}/{len(source_files)} source files covered)"
        )
        click.echo(
            f"  Bus factor:    {'RISK' if bus_factor_risk >= 0.5 else 'ok':>5s}  "
            f"{'(single-author file!)' if min_bf == 1 else ''}"
        )
        click.echo(f"  Coupling:      {coupling_score * 100:5.1f}%")
        click.echo(
            f"  Novelty:       {novelty * 100:5.1f}%{'  (unfamiliar change combination!)' if novelty > 0.7 else ''}"
        )
        if resolved_author:
            fam_avg = familiarity_details.get("avg_familiarity", 1.0)
            fam_assessed = familiarity_details.get("files_assessed", 0)
            fam_known = familiarity_details.get("files_familiar", 0)
            click.echo(
                f"  Familiarity:   {fam_avg * 100:5.1f}%  (author knows {fam_known}/{fam_assessed} changed files well)"
            )
            minor_files = minor_details.get("minor_files", 0)
            minor_assessed = minor_details.get("files_assessed", 0)
            if minor_files > 0:
                click.echo(
                    f"  Minor risk:    {minor_risk * 100:5.1f}%"
                    f"  (author is minor contributor to {minor_files}/{minor_assessed} files)"
                )
            else:
                click.echo("  Minor risk:      0.0%  (author is major contributor to all files)")
        if total_clusters > 1:
            click.echo(f"  Cluster spread: {len(cluster_ids)}/{total_clusters} clusters touched")
        if total_layers > 1:
            click.echo(f"  Layer spread:   {len(touched_layers)}/{total_layers} layers touched")
        click.echo()

        # Per-file table
        rows = []
        for pf in per_file[:15]:
            flag = "test" if pf["is_test"] else ""
            rows.append(
                [
                    pf["path"],
                    str(pf["symbols"]),
                    str(pf["blast"]),
                    str(pf["churn"]) if pf["churn"] else "",
                    f"+{pf['lines_added']}/-{pf['lines_removed']}",
                    flag,
                ]
            )
        click.echo("Changed files:")
        click.echo(
            format_table(
                ["file", "syms", "blast", "churn", "+/-", ""],
                rows,
            )
        )
        if len(per_file) > 15:
            click.echo(f"  (+{len(per_file) - 15} more)")

        if new_dead:
            click.echo(f"\nNew dead exports ({len(new_dead)}):")
            for d in new_dead[:10]:
                click.echo(f"  {d['kind']:<10s} {d['name']:<30s} {d['file']}")
            if len(new_dead) > 10:
                click.echo(f"  (+{len(new_dead) - 10} more)")

        if top_authors:
            click.echo("\nSuggested reviewers:")
            for rev_author, lines in top_authors:
                click.echo(f"  {rev_author:<30s} ({lines} lines contributed)")

        # — point at the natural next command.
        from roam.commands.next_steps import format_next_steps_text, suggest_next_steps

        _ns = suggest_next_steps(
            "pr-risk",
            {
                "risk_level": level,
                "driver": driver_label or "",
            },
        )
        _ns_text = format_next_steps_text(_ns)
        if _ns_text:
            click.echo(_ns_text)
