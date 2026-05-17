"""Idempotency detector — composes on top of side-effects classification.

Heuristic detector — false negatives expected, false positives should be rare.

For each symbol, classify whether calling it **twice** is safe:

- ``idempotent``      — pure functions; read-only I/O; write-with-check
                        patterns (``mkdir(exist_ok=True)``, ``INSERT OR
                        IGNORE``, ``UPSERT``, ``if not exists: create``).
- ``non_idempotent``  — naive writes / mutations / appends.
- ``unknown``         — process spawn or anything we can't reason about.

Composes with :func:`roam.world_model.side_effects.classify_side_effects`:
side-effects classification is the **input** — idempotency is one layer
up.  This is by design: the only way to ask "is this symbol safe to
retry?" without already knowing what it does is to re-do the
side-effects analysis, which is wasteful.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from roam.db.connection import find_project_root
from roam.world_model.side_effects import (
    SideEffectClassification,
    classify_side_effects,
)

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

IDEMPOTENCY_KINDS = ("idempotent", "non_idempotent", "unknown")


@dataclass
class IdempotencyClassification:
    """Per-symbol idempotency classification."""

    symbol: str
    file: str
    kind: str = "unknown"
    evidence: dict = field(default_factory=dict)
    confidence: str = "low"
    symbol_id: int = 0
    line_start: int = 0
    line_end: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "file": self.file,
            "kind": self.kind,
            "evidence": dict(self.evidence),
            "confidence": self.confidence,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


# ---------------------------------------------------------------------------
# Idempotency anchors — source-level "I checked before writing" patterns.
# Match these on the function body to override a naive non_idempotent
# classification.
# ---------------------------------------------------------------------------

_CHECK_FIRST_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bexist_ok\s*=\s*True"), "mkdir(exist_ok=True)"),
    (re.compile(r"INSERT\s+OR\s+IGNORE", re.IGNORECASE), "INSERT OR IGNORE"),
    (re.compile(r"INSERT\s+OR\s+REPLACE", re.IGNORECASE), "INSERT OR REPLACE"),
    (re.compile(r"ON\s+CONFLICT\b", re.IGNORECASE), "ON CONFLICT"),
    (re.compile(r"\bUPSERT\b", re.IGNORECASE), "UPSERT"),
    (re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", re.IGNORECASE), "CREATE TABLE IF NOT EXISTS"),
    (re.compile(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS", re.IGNORECASE), "CREATE INDEX IF NOT EXISTS"),
    (re.compile(r"DROP\s+TABLE\s+IF\s+EXISTS", re.IGNORECASE), "DROP TABLE IF EXISTS"),
    (re.compile(r"\bif\s+not\s+.*\.exists\(\)"), "if not Path.exists()"),
    (re.compile(r"\bif\s+not\s+os\.path\.exists\("), "if not os.path.exists()"),
    (re.compile(r"\bif\s+not\s+os\.path\.isfile\("), "if not os.path.isfile()"),
    (re.compile(r"\bif\s+not\s+os\.path\.isdir\("), "if not os.path.isdir()"),
    (re.compile(r"@idempotent\b"), "@idempotent decorator"),
    (re.compile(r"\.setdefault\("), "dict.setdefault"),
)

# Anti-idempotency anchors — patterns that make a symbol clearly NOT safe
# to retry even if it might pattern-match the check-first list above.
_APPEND_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bopen\s*\([^)]*['\"]a[+]?['\"]"), "open(mode='a')"),
    (re.compile(r"\.append\("), ".append("),
    (re.compile(r"INSERT\s+INTO\b(?!\s+\w+\s+ON\s+CONFLICT)", re.IGNORECASE), "INSERT INTO (naive)"),
    (re.compile(r"\.write\(.*\+=.*counter|\bcounter\s*\+=", re.IGNORECASE), "counter+="),
)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def _load_body(repo_root: Path, rel_path: str, ls: int, le: int) -> str:
    """Read lines [ls..le] (1-based inclusive)."""
    try:
        p = repo_root / rel_path
        if not p.exists():
            return ""
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return ""
    if ls <= 0:
        ls = 1
    if le <= 0 or le > len(lines):
        le = len(lines)
    return "".join(lines[ls - 1 : le])


def _classify_one(
    se: SideEffectClassification,
    body_text: str,
) -> IdempotencyClassification:
    """Map a single side-effects classification + source body → idempotency."""
    kinds = set(se.kinds)

    # Pure → idempotent (high confidence).
    if kinds == {"none"}:
        return IdempotencyClassification(
            symbol=se.symbol,
            file=se.file,
            kind="idempotent",
            evidence={"reason": "pure (no side effects)"},
            confidence="high",
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # Process spawn → unknown (depends on the subprocess).
    if "process" in kinds:
        return IdempotencyClassification(
            symbol=se.symbol,
            file=se.file,
            kind="unknown",
            evidence={
                "reason": "spawns subprocess/thread — depends on child behavior",
                "side_effect_kinds": sorted(kinds),
            },
            confidence="medium",
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # Mutation of global / module state → non_idempotent.
    if "mutation" in kinds:
        return IdempotencyClassification(
            symbol=se.symbol,
            file=se.file,
            kind="non_idempotent",
            evidence={
                "reason": "mutates global / module / nonlocal state",
                "side_effect_kinds": sorted(kinds),
            },
            confidence="high",
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # Read-only → idempotent.
    if kinds <= {"io_read", "none"}:
        return IdempotencyClassification(
            symbol=se.symbol,
            file=se.file,
            kind="idempotent",
            evidence={
                "reason": "read-only I/O (no state change)",
                "side_effect_kinds": sorted(kinds),
            },
            confidence="high",
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # io_write: look for check-first patterns to decide idempotent vs not.
    if "io_write" in kinds:
        check_matches: list[str] = []
        if body_text:
            for pat, label in _CHECK_FIRST_PATTERNS:
                if pat.search(body_text):
                    check_matches.append(label)
        append_matches: list[str] = []
        if body_text:
            for pat, label in _APPEND_PATTERNS:
                if pat.search(body_text):
                    append_matches.append(label)

        if check_matches and not append_matches:
            return IdempotencyClassification(
                symbol=se.symbol,
                file=se.file,
                kind="idempotent",
                evidence={
                    "reason": "write-with-check pattern detected",
                    "check_patterns": check_matches[:8],
                    "side_effect_kinds": sorted(kinds),
                },
                confidence="medium",
                symbol_id=se.symbol_id,
                line_start=se.line_start,
                line_end=se.line_end,
            )
        if append_matches:
            return IdempotencyClassification(
                symbol=se.symbol,
                file=se.file,
                kind="non_idempotent",
                evidence={
                    "reason": "append / naive insert pattern detected",
                    "append_patterns": append_matches[:8],
                    "side_effect_kinds": sorted(kinds),
                },
                confidence="high",
                symbol_id=se.symbol_id,
                line_start=se.line_start,
                line_end=se.line_end,
            )
        # io_write with no check pattern → non_idempotent, medium confidence.
        return IdempotencyClassification(
            symbol=se.symbol,
            file=se.file,
            kind="non_idempotent",
            evidence={
                "reason": "io_write without check-first pattern",
                "side_effect_kinds": sorted(kinds),
            },
            confidence="medium",
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # Unknown side-effects → unknown idempotency.
    return IdempotencyClassification(
        symbol=se.symbol,
        file=se.file,
        kind="unknown",
        evidence={
            "reason": "side-effects unknown",
            "side_effect_kinds": sorted(kinds),
        },
        confidence="low",
        symbol_id=se.symbol_id,
        line_start=se.line_start,
        line_end=se.line_end,
    )


def classify_idempotency(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[IdempotencyClassification]:
    """Scan symbols and classify idempotency.

    Args:
        conn: Read-only DB connection.
        symbol_name: If given, only one symbol.
        limit: Optional cap.
        side_effects: Optional pre-computed side-effects list (pass-through
            optimization — avoids re-running the side-effects detector).

    Returns:
        List of :class:`IdempotencyClassification`.
    """
    if side_effects is None:
        side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)

    try:
        repo_root = find_project_root()
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # missing project root downgrades per-symbol source slicing
        # silently; surface it so callers see "could not locate repo"
        # rather than empty-body classifications.
        warnings.warn(
            f"find_project_root() failed in classify_idempotency "
            f"({type(exc).__name__}: {exc}); falling back to Path('.')",
            category=RuntimeWarning,
            stacklevel=2,
        )
        repo_root = Path(".")

    # Group by file for cache-friendly source reads.
    by_file: dict[str, list[SideEffectClassification]] = {}
    for se in side_effects:
        by_file.setdefault(se.file, []).append(se)

    out: list[IdempotencyClassification] = []
    for file_path, items in by_file.items():
        try:
            p = repo_root / file_path
            if p.exists():
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            else:
                lines = []
        except OSError:
            lines = []
        for se in items:
            ls = se.line_start or 1
            le = se.line_end or ls
            if lines:
                body = "".join(lines[max(0, ls - 1) : le])
            else:
                body = ""
            out.append(_classify_one(se, body))
    return out


__all__ = [
    "IDEMPOTENCY_KINDS",
    "IdempotencyClassification",
    "classify_idempotency",
]
