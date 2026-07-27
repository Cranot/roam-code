"""Shared helpers for audit-trail commands.

Extracted from cmd_audit_trail_export.py + cmd_audit_trail_conformance.py — both implemented identical
``_load_records`` functions for reading EU AI Act audit-trail JSONL.

Also exposes the canonical default path constant + schema name so
future commands can pull a single source of truth.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

DEFAULT_AUDIT_TRAIL_PATH = Path(".roam") / "audit-trail.jsonl"
AUDIT_TRAIL_SCHEMA = "roam-audit-trail-v1"
INTEGRITY_SUMMARY_SCHEMA = "roam-audit-integrity-summary-v1"

# --- Tail-record protection (bug #286) --------------------------------------
#
# ``cmd_audit_trail_verify._verify_chain`` walks a SHA-256
# ``previous_record_hash`` chain: record N's hash is checked by record
# N+1's back-link. That structurally cannot protect the LAST record —
# there is no N+1 to catch a tampered tail, so editing the most recent
# (and most valuable-to-an-attacker) verdict in place was silently
# undetected, contradicting the command's own "tampering with any
# record breaks the chain" claim.
#
# The fix mirrors the ONE mechanism this codebase already ships for
# exactly this shape of problem: ``roam.runs.ledger.end_run`` stamps an
# R20-phase-4 "final_signature" fingerprint into ``meta.json`` —
# a small file SEPARATE from ``events.jsonl`` — precisely so a reader
# can detect a changed-since-close ledger without trusting the ledger
# file alone. ``write_audit_trail_head`` / ``read_audit_trail_head``
# below do the same thing for the audit trail: every append stamps the
# new tail line's own hash into a sibling ``<trail>.head.json`` file.
# ``_verify_chain`` then compares the trail's ACTUAL last-line hash
# against this independently-stored pointer, so tampering the tail
# without also rewriting the head file now breaks verification.
#
# Backward compatible by construction: a trail with no head file (hand
# built, foreign, copied from a pre-13.10 checkout) simply gets no tail
# check — the exact pre-fix behaviour — so older/foreign trails keep
# verifying exactly as before instead of failing closed on a field that
# never existed for them. See cmd_audit_trail_verify's module docstring
# for the residual limitation this does NOT close (an attacker who can
# rewrite the trail AND the head file together is undetectable — this
# is a hash chain, not a keyed signature).
AUDIT_TRAIL_HEAD_SCHEMA = "roam-audit-trail-head-v1"


def audit_trail_head_path(trail_path: Path) -> Path:
    """Sibling head-pointer path for *trail_path*.

    ``.roam/audit-trail.jsonl`` -> ``.roam/audit-trail.head.json``.
    Derived from the trail's own name so a custom ``--input`` path gets
    its own head file rather than colliding with the canonical one.
    """
    return trail_path.with_name(f"{trail_path.stem}.head.json")


def write_audit_trail_head(trail_path: Path, *, tail_hash: str, sequence_number: int) -> None:
    """Stamp the tail-pointer file after appending a record to *trail_path*.

    Best-effort / non-fatal by design, mirroring
    ``roam.runs.signing.sign_event``'s "append-only is the higher
    invariant" rule: a failure to stamp the head-pointer file must
    never block the audit-trail record itself from being written.
    Callers should wrap this in a broad ``except OSError: pass``.
    """
    from roam.atomic_io import atomic_write_json

    atomic_write_json(
        audit_trail_head_path(trail_path),
        {
            "schema": AUDIT_TRAIL_HEAD_SCHEMA,
            "sequence_number": sequence_number,
            "tail_hash": tail_hash,
        },
        sort_keys=True,
    )


def read_audit_trail_head(trail_path: Path) -> dict | None:
    """Read the head-pointer file for *trail_path*.

    Returns ``None`` when the file is absent, unreadable, or not a
    well-formed JSON object — all three collapse to "no tail
    protection available for this trail" at the call site, matching
    the backward-compatibility contract described above.
    """
    head_path = audit_trail_head_path(trail_path)
    if not head_path.exists():
        return None
    try:
        data = _json.loads(head_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def next_sequence_number(path: Path) -> int:
    """Compute the next monotonic sequence number for an audit-trail record.

    Counts existing records (including malformed lines, which still occupy a
    sequence slot for transparency) and returns N+1. Used by ``pr-analyze
    --audit-trail`` so each record carries a stable position-independent ID.

    Returns 1 for a missing or empty trail (genesis).
    """
    if not path.exists():
        return 1
    count = 0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
    except OSError:
        return 1
    return count + 1


def load_records(path: Path) -> list[dict]:
    """Read a JSONL audit trail; skip blank lines + invalid-JSON lines silently.

    Mirrors the contract used by ``cmd_audit_trail_verify._verify_chain``
    for the records portion (verify also surfaces issues; this loader is
    for consumers that don't need integrity reporting — export, conformance,
    aggregate).
    """
    out: list[dict] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                out.append(_json.loads(stripped))
            except _json.JSONDecodeError:
                continue
    return out
