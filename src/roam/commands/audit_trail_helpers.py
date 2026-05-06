"""Shared helpers for audit-trail commands.

Extracted from cmd_audit_trail_export.py + cmd_audit_trail_conformance.py
in the Phase 14 fresh-eyes sweep — both implemented identical
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
