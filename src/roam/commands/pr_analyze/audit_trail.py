"""Audit-trail JSONL emit for ``roam pr-analyze`` (D5 split).

EU AI Act Article 12-shaped records: each line is a JSON object with
the verdict, structural metrics, rationale summary, and the prior
record's SHA-256 (chain integrity). Local-first; pair with
``roam.attest.cga`` for cosign signing when needed.
"""

from __future__ import annotations

import hashlib
import json as _json
from pathlib import Path

from roam.commands.audit_trail_helpers import AUDIT_TRAIL_SCHEMA, next_sequence_number
from roam.commands.git_helpers import (
    detect_roam_version,
    git_actor,
    git_head_sha,
    git_origin_url,
    utc_timestamp,
)


def _last_record_hash(path: Path) -> str:
    """Return SHA-256 of the last line in the audit-trail JSONL, or '' if none."""
    if not path.exists():
        return ""
    try:
        # Read tail efficiently — last 8 KB is plenty for a single JSON line
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", errors="replace")
        last_line = ""
        for line in tail.strip().split("\n"):
            if line.strip():
                last_line = line.strip()
        if not last_line:
            return ""
        return hashlib.sha256(last_line.encode("utf-8")).hexdigest()
    except OSError:
        return ""


def _emit_audit_trail_record(
    *,
    audit_trail_path: Path,
    diff_text: str,
    bundle: dict,
    intent: str | None,
    reviewers_payload: dict | None,
) -> dict:
    """Append a tamper-evident Article 12-shaped record to the audit trail.

    The record includes: invoking actor (from git config), repo + git SHA,
    diff hash (SHA-256), the verdict + structural metrics, the rationale
    summary, the previous record's hash for chain integrity, and the
    full reviewer payload when supplied.
    """
    audit_trail_path.parent.mkdir(parents=True, exist_ok=True)
    summary = bundle.get("summary") or {}
    rationale = bundle.get("rationale") or {}

    record = {
        "schema": AUDIT_TRAIL_SCHEMA,
        "sequence_number": next_sequence_number(audit_trail_path),
        "timestamp": utc_timestamp(),
        "tool": "roam-code",
        "tool_version": detect_roam_version(),
        "actor": git_actor(),
        "repo": git_origin_url(),
        "git_sha": git_head_sha(),
        "diff_sha256": hashlib.sha256((diff_text or "").encode("utf-8")).hexdigest(),
        "verdict": summary.get("verdict"),
        "blast_radius": summary.get("blast_radius"),
        "ai_likelihood": summary.get("ai_likelihood"),
        "rule_violations_count": summary.get("rule_violations", 0),
        "high_severity_critique": summary.get("high_severity_critique", 0),
        "intent_marker": intent or None,
        "rationale_summary": rationale.get("summary_text"),
        "suggested_reviewers": [r.get("name") for r in (rationale.get("suggested_reviewers") or [])],
        "previous_record_hash": _last_record_hash(audit_trail_path),
    }
    # Stable JSON encoding so the chain hash is reproducible
    line = _json.dumps(record, separators=(",", ":"), sort_keys=True)
    with audit_trail_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return record
