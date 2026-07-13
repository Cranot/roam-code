"""Deterministic repair-intent scoring for the retrieve reranker.

The applicability formula is the validated T-prime formula from the frozen
1c experiment.  This module only adapts retrieval rows to the scorer's small
candidate protocol; it does not add graph features, learned weights, or
another candidate-generation path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from roam.sibling_patch.repair_scorer import (
    RepairIntent,
    ScorerCandidate,
    derive_repair_intent,
    parse_patch_changes,
    repair_applicability,
)

REPAIR_INTENT_FLAG = "ROAM_EXPERIMENTAL_REPAIR_INTENT"


@dataclass(frozen=True)
class ScoredCandidate:
    """T-prime pool row with the validated repair score attached."""

    candidate: ScorerCandidate
    lexical: float
    clone_score: float = 0.0
    graph_score: float = 0.0
    repair_score: float = 0.0
    cochange_count: int = 0
    tags: tuple[str, ...] = ()
    clone_tags: tuple[str, ...] = ()


def flag_enabled() -> bool:
    """Return whether the experimental retrieve signal is enabled."""
    return os.environ.get(REPAIR_INTENT_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def score_pool_repair_intent(
    lexical_pool: Sequence[ScoredCandidate],
    intent: RepairIntent,
) -> list[ScoredCandidate]:
    """Attach validated repair applicability to every row in *lexical_pool*.

    This is the T-prime ``score_pool_repair_intent`` operation: pool identity
    and all existing score fields are preserved; only ``repair_score`` is
    replaced with ``repair_applicability(intent, candidate)``.
    """
    return [
        ScoredCandidate(
            candidate=item.candidate,
            lexical=item.lexical,
            clone_score=item.clone_score,
            graph_score=item.graph_score,
            repair_score=repair_applicability(intent, item.candidate),
            cochange_count=item.cochange_count,
            tags=item.tags,
            clone_tags=item.clone_tags,
        )
        for item in lexical_pool
    ]


def intent_from_patch(text: str) -> RepairIntent:
    """Derive the validated repair intent from unified-diff text."""
    return derive_repair_intent(parse_patch_changes(text))


def score_retrieval_candidates(
    candidates: Sequence[dict[str, Any]],
    intent: RepairIntent,
    *,
    project_root: Path,
) -> dict[int, float]:
    """Return ``symbol_id -> repair_score`` for retrieval candidates.

    Candidate bodies are read only when the experimental signal is active and
    an intent was supplied. Missing/unreadable files degrade to an empty body,
    which is the scorer's deterministic zero-signal input.
    """
    pool: list[ScoredCandidate] = []
    for row in candidates:
        body = _read_candidate_body(project_root, row)
        candidate = ScorerCandidate.from_body(
            {
                "id": row.get("symbol_id", 0),
                "file": row.get("file_path") or row.get("file") or "",
                "symbol": row.get("qualified_name") or row.get("name") or "",
                "kind": row.get("kind") or "",
                "line_start": row.get("line_start") or 0,
                "line_end": row.get("line_end") or 0,
            },
            body,
        )
        pool.append(ScoredCandidate(candidate=candidate, lexical=float(row.get("fts_score") or 0.0)))

    return {
        int(item.candidate.meta.get("id") or 0): item.repair_score for item in score_pool_repair_intent(pool, intent)
    }


def _read_candidate_body(project_root: Path, row: dict[str, Any]) -> str:
    path_value = row.get("file_path") or row.get("file")
    if not path_value:
        return ""
    path = project_root / str(path_value)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(int(row.get("line_start") or 1) - 1, 0)
    end_value = row.get("line_end")
    end = int(end_value) if end_value is not None else min(len(lines), start + 400)
    return "\n".join(lines[start : max(start, end)])


__all__ = [
    "REPAIR_INTENT_FLAG",
    "RepairIntent",
    "ScoredCandidate",
    "ScorerCandidate",
    "derive_repair_intent",
    "flag_enabled",
    "intent_from_patch",
    "parse_patch_changes",
    "repair_applicability",
    "score_pool_repair_intent",
    "score_retrieval_candidates",
]
