"""Central findings registry — the denormalised cross-detector surface.

Detectors continue to write to their own tables (``math_signals``,
``taint_findings``, ``clone_pairs``, etc.) AND emit a row to the
``findings`` table. Consumers (``roam findings list/show/count``, central
SARIF emit, the suppression CLI) query this table directly instead of
joining ~20 detector-specific tables.

Substrate population as of W146: ~20 detectors persist findings (clones,
dead, complexity, smells, n1, missing-index, over-fetch, bus-factor,
auth-gaps, vulns, invariants, hotspots, taint, vibe-check, orphan-imports,
conventions, pr-risk, duplicates, audit-trail-conformance,
audit-trail-verify) — ~7900+ rows on roam-code itself. The historical
"deferred to follow-up waves" framing is retired; the per-detector
catalog is enumerated in the ``source_detector`` field comment below.

Core API:

* ``emit_finding`` upserts on ``finding_id_str``; re-running a detector
  refreshes evidence in place without duplicating rows.
* ``list_findings`` is the read-side query helper backing
  ``roam findings list --filter``.
* ``supersede_finding`` lets a detector mark a prior finding obsolete
  while preserving the audit trail (``supersedes_id`` chain).
* ``make_finding_id`` produces the canonical
  ``"<prefix>:<subject>:<digest12>"`` id used across every detector.

See CLAUDE.md §"Agent OS substrate" for the wider registry framing and
the confidence-tier / subject-kind vocabulary discipline.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

# Confidence vocabulary — kept as plain strings for now to avoid an
# enum import dance at hot-path emit sites. The values below are the
# accepted enumeration; new detectors should reuse one of these rather
# than minting their own. A future wave can promote to an Enum once
# the consumer surface stabilises.
#
# CONFIDENCE_* — registry confidence-tier vocabulary (4-tier:
# heuristic / structural / static_analysis / runtime).
# Names the DETECTION METHOD a detector used to surface this finding.
#
# NOT the same as evidence-level CLAIM_CONFIDENCES (direct / derived /
# inferred / legacy_fallback in roam.evidence._vocabulary), which
# names the PRODUCER/COLLECTOR LEVEL the evidence packet derived from.
# Both 4-tier axes share the "confidence" name but are ORTHOGONAL.
# See CLAUDE.md §"Confidence-tier vocabulary" for the canonical split.
CONFIDENCE_HEURISTIC = "heuristic"  # pattern-match / regex / signal threshold
CONFIDENCE_STRUCTURAL = "structural"  # AST / graph evidence
CONFIDENCE_STATIC_ANALYSIS = "static_analysis"  # taint / dataflow / type analysis
CONFIDENCE_RUNTIME = "runtime"  # observed at runtime (OTel / coverage)


# Finding ID derivation -----------------------------------------------------
#
# The canonical id shape across every detector is
# ``"<prefix>:<subject>:<digest12>"`` where ``digest12`` is the first 12 hex
# chars of ``sha1(":".join(raw_parts))``. Each detector chooses its own
# ``prefix`` (the detector's stable namespace, e.g. ``"smells"``,
# ``"bus-factor"``, ``"dead"``) and ``subject`` (a human-readable mid-segment
# disambiguating per-kind / per-language / per-check rows under one detector).
#
# The helper exists so the 6+ detector modules that emit findings stop
# re-implementing the same sha1 + truncate + join boilerplate (W855 cluster,
# sim=1.000). Call-sites read like
# ``make_finding_id("smells", smell_id, smell_id, file_path, name, int(line or 0))``
# — the second positional ``subject`` is also the first ``raw_part`` for the
# detectors whose pre-helper bodies happened to include the subject in their
# raw tuple; that overlap is fine and is required to reproduce byte-identical
# hashes against rows already persisted in the findings registry.
#
# Hash stability is a hard contract: changing the digest would orphan every
# existing finding row. The unit test ``test_findings_make_finding_id_*``
# pins the format against the original per-detector helpers.
def make_finding_id(prefix: str, subject: str, *raw_parts: object) -> str:
    """Canonical finding-id helper — produces ``"<prefix>:<subject>:<digest>"``.

    ``raw_parts`` are str()ed in order and joined by ``":"`` before sha1.
    Caller is responsible for null-coercion (e.g. ``int(line or 0)``) so the
    digest stays stable across ``None`` / ``0`` / missing inputs — the helper
    intentionally does NOT mask Nones because different detectors have
    different "absent" sentinels and hiding that here would silently change
    one of the persisted hashes.
    """
    raw = ":".join(str(part) for part in raw_parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{subject}:{digest}"


# The findings registry intentionally has NO severity column. Severity
# vocabulary lives in evidence_json per-detector — the canonical 5-tier
# alphabet is roam.evidence._vocabulary.CLAIM_SEVERITIES (critical /
# high / medium / low / info). See output/_severity.py for the
# SARIF-projection 4-tier (W547/W564 canonical).
@dataclass(frozen=True)
class FindingRecord:
    """One detector finding, denormalised for cross-detector queries.

    ``finding_id_str`` should be deterministic — a detector that re-runs
    on the same input MUST produce the same id, so the UNIQUE upsert
    refreshes the row in place. Convention: ``"<detector>:<subject>:<hash>"``.

    ``subject_id`` is nullable because not every subject_kind maps to a
    ``symbols.id`` (file-level, edge-level, commit-level findings exist).
    Consumers querying by symbol JOIN on ``(subject_kind='symbol' AND subject_id=?)``.

    ``evidence_json`` is the detector-specific payload — schema is owned
    by the detector, not by this registry. Keep it small (< 4 KB) and
    intern long strings via shared keys when possible. The < 4 KB target
    is GUIDANCE, not enforced: there is no current lint or schema check
    that fails an over-budget row. A future closure could add one as
    ``tests/test_findings_evidence_size_lint.py`` (per-detector p95 size
    + hard ceiling); leaving unwritten until a real regression motivates it.

    ``source_version`` is the stamp reserved by W81 / ROADMAP A6. Detectors
    populate it from their own ``VERSION`` class attribute so a consumer
    can spot rows produced under a stale detector shape. Convention: each
    detector owns a ``<DETECTOR>_DETECTOR_VERSION`` module constant at its
    call-site (see the ``source_detector`` field comment below) — NOT in
    ``src/roam/catalog/versions.py`` which is reserved for the
    task_id-keyed algorithm-catalog registry.
    """

    finding_id_str: str
    # subject_kind — narrower than evidence-level SUBJECT_KINDS by design.
    # Registry rows must map to a concrete graph identity (symbols.id or NULL),
    # so this column excludes the 7 evidence-only kinds: rule, control, run,
    # bundle, finding, test, artifact. Those live ONLY in ChangeEvidence
    # packets, not in the findings table. See roam.evidence._vocabulary.SUBJECT_KINDS
    # for the full 20-kind vocabulary + CLAUDE.md §"Evidence compiler layer"
    # for the layered design rationale.
    subject_kind: str
    claim: str
    # source_detector — free-form string today; the canonical persisting
    # detectors (~20 as of W146 per CLAUDE.md) are: clones (W95), dead (W99),
    # complexity (W102), smells (W109), n1 (W110), missing-index (W111),
    # over-fetch (W114), bus-factor (W115), auth-gaps (W116), vulns (W117),
    # invariants (W119), hotspots (W120), taint (W122), vibe-check (W125),
    # orphan-imports (W132), conventions (W133), pr-risk (W134),
    # duplicates (W136), audit-trail-conformance (W145), audit-trail-verify
    # (W146). Closed-set validation is intentionally NOT enforced here —
    # new detectors must extend by adding a `<DETECTOR>_DETECTOR_VERSION`
    # module constant at their call-site per W81.
    source_detector: str
    subject_id: Optional[int] = None
    evidence_json: str = "{}"
    confidence: str = CONFIDENCE_HEURISTIC
    source_version: Optional[str] = None
    supersedes_id: Optional[int] = None
    # suppressions_json — JSON array of suppression-id strings that gag this
    # finding for downstream consumers (SARIF emit, ``roam findings list``,
    # CI gate). Default ``"[]"`` means no suppressions. Suppression id format
    # and lifecycle are documented in ``src/roam/commands/suppression.py``;
    # this column is intentionally append-only at the registry layer — the
    # suppression CLI is the only writer that adds/removes entries.
    suppressions_json: str = "[]"


def emit_finding(conn: sqlite3.Connection, record: FindingRecord) -> int:
    """Insert (or upsert on ``finding_id_str``) a finding row.

    Returns the assigned ``id``. On conflict, the evidence / confidence /
    source_version columns are refreshed but the row id is preserved so
    downstream supersedes chains stay intact.

    Caller is responsible for transaction management — emit_finding
    issues a single INSERT and does NOT commit.
    """
    cur = conn.execute(
        """
        INSERT INTO findings (
            finding_id_str, subject_kind, subject_id, claim,
            evidence_json, confidence, source_detector, source_version,
            supersedes_id, suppressions_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(finding_id_str) DO UPDATE SET
            evidence_json = excluded.evidence_json,
            confidence = excluded.confidence,
            source_version = excluded.source_version,
            claim = excluded.claim
        """,
        (
            record.finding_id_str,
            record.subject_kind,
            record.subject_id,
            record.claim,
            record.evidence_json,
            record.confidence,
            record.source_detector,
            record.source_version,
            record.supersedes_id,
            record.suppressions_json,
        ),
    )
    # On INSERT lastrowid is the new id; on UPDATE it's the existing row's
    # id (SQLite preserves rowid through ON CONFLICT DO UPDATE).
    rowid = cur.lastrowid
    if rowid:
        return int(rowid)
    # Fallback: lookup by the unique key. Only reached if a driver
    # quirk loses lastrowid on the UPDATE branch.
    row = conn.execute(
        "SELECT id FROM findings WHERE finding_id_str = ?",
        (record.finding_id_str,),
    ).fetchone()
    return int(row[0]) if row else 0


def get_finding(conn: sqlite3.Connection, finding_id_str: str) -> Optional[dict[str, Any]]:
    """Fetch a single finding by its stable string id. Returns None if absent."""
    row = conn.execute(
        """
        SELECT id, finding_id_str, subject_kind, subject_id, claim,
               evidence_json, confidence, source_detector, source_version,
               supersedes_id, suppressions_json, created_at
        FROM findings WHERE finding_id_str = ?
        """,
        (finding_id_str,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_findings(
    conn: sqlite3.Connection,
    *,
    detector: Optional[str] = None,
    subject_kind: Optional[str] = None,
    subject_id: Optional[int] = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Query findings with optional filters.

    Used by the eventual ``roam findings --filter`` command. All filters
    are optional and AND-composed. ``limit`` is enforced at the SQL
    layer to keep the response bounded — callers that need pagination
    should add an offset parameter in a follow-up wave.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if detector is not None:
        clauses.append("source_detector = ?")
        params.append(detector)
    if subject_kind is not None:
        clauses.append("subject_kind = ?")
        params.append(subject_kind)
    if subject_id is not None:
        clauses.append("subject_id = ?")
        params.append(subject_id)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    rows = conn.execute(
        "SELECT id, finding_id_str, subject_kind, subject_id, claim, "
        "evidence_json, confidence, source_detector, source_version, "
        "supersedes_id, suppressions_json, created_at "
        f"FROM findings{where_sql} "
        "ORDER BY id DESC LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_by_detector(conn: sqlite3.Connection) -> dict[str, int]:
    """Aggregate row counts grouped by ``source_detector``.

    Used for the ``roam findings --list`` overview ("which detectors
    have produced findings + how many"). Returns an empty dict when
    the table is empty.
    """
    rows = conn.execute("SELECT source_detector, COUNT(*) FROM findings GROUP BY source_detector").fetchall()
    return {str(name): int(count) for name, count in rows}


def supersede_finding(
    conn: sqlite3.Connection,
    old_finding_id_str: str,
    new_record: FindingRecord,
) -> int:
    """Mark ``old_finding_id_str`` as superseded, emit ``new_record``.

    The new record's ``supersedes_id`` is overwritten with the old row's
    numeric id. The old row stays in place — superseding is additive,
    not destructive (preserves the audit trail).

    Returns the new finding's row id. Raises ``ValueError`` when the
    old finding doesn't exist.
    """
    old_row = conn.execute(
        "SELECT id FROM findings WHERE finding_id_str = ?",
        (old_finding_id_str,),
    ).fetchone()
    if old_row is None:
        raise ValueError(f"supersede_finding: no existing finding with id {old_finding_id_str!r}")
    old_id = int(old_row[0])
    # Rebuild the record with supersedes_id pointing at the old row.
    # dataclass is frozen, so use a fresh instance instead of mutating.
    successor = FindingRecord(
        finding_id_str=new_record.finding_id_str,
        subject_kind=new_record.subject_kind,
        subject_id=new_record.subject_id,
        claim=new_record.claim,
        evidence_json=new_record.evidence_json,
        confidence=new_record.confidence,
        source_detector=new_record.source_detector,
        source_version=new_record.source_version,
        supersedes_id=old_id,
        suppressions_json=new_record.suppressions_json,
    )
    return emit_finding(conn, successor)


def _row_to_dict(row: tuple) -> dict[str, Any]:
    """Convert a fetched row to a stable dict shape.

    Tuple order MUST match the SELECT column order used by the readers
    above. Centralising this conversion makes the row layout single-
    sourced — adding a column later only requires updating this helper
    and the SELECTs above.
    """
    return {
        "id": int(row[0]),
        "finding_id_str": row[1],
        "subject_kind": row[2],
        "subject_id": int(row[3]) if row[3] is not None else None,
        "claim": row[4],
        "evidence_json": row[5],
        "confidence": row[6],
        "source_detector": row[7],
        "source_version": row[8],
        "supersedes_id": int(row[9]) if row[9] is not None else None,
        "suppressions_json": row[10],
        "created_at": row[11],
    }
