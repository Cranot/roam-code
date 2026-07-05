"""Deterministic repair-intent scorer for the Sibling Patch Network.

This is the *measured winner* from the fork-B / T-prime experiment, vendored as
a pure, dependency-free reranker:

  * candidate generation is a frozen top-N **lexical** pool (NOT the graph
    stack — W855/856/857 + fingerprints transfer poorly cross-org: recall 0.33
    vs 0.65); and
  * ranking is ``repair_applicability`` (primary) with lexical cosine as the
    deterministic tie-breaker — exactly the T-prime policy that produced the
    +0.089 recall lift on defect-shaped repairs (CI[0.047,0.134], N=118).

Everything here is deterministic (Rule 10): identical (anchor, candidates,
intent) always yields the identical ranking. There are no learned weights, so
there is nothing to poison until/unless weights are ever trained.

Ported verbatim (semantics-preserving) from
``autopilot/repair_siblings_experiment.py`` and ``autopilot/tprime_experiment.py``.
The graph scorers, git/blob loaders, and the ``forkb_eligibility_ours`` symbol
gate are intentionally left behind.
"""
from __future__ import annotations

import collections
import dataclasses
import math
import re
from typing import Any, Sequence

# --- tokenization primitives (fork-B) --------------------------------------
TOKEN_RE = re.compile(
    r"[A-Za-z_$][A-Za-z0-9_$]*|==|!=|<=|>=|=>|[-+*/%]=|&&|\|\||[0-9]+(?:\.[0-9]+)?"
)
CALL_RE = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
STRING_RE = re.compile(r"(['\"])(?:\\.|(?!\1).)*\1|`(?:\\.|[^`])*`")
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
PY_JS_KEYWORDS = {
    "False", "None", "True", "and", "as", "async", "await", "break", "case",
    "catch", "class", "const", "continue", "def", "default", "del", "do",
    "elif", "else", "except", "export", "extends", "finally", "for", "from",
    "function", "if", "import", "in", "let", "new", "not", "null", "of", "or",
    "pass", "raise", "return", "switch", "this", "throw", "try", "var", "while",
    "with", "yield",
}

# Scoped verdict: SPN v1 admits only DEFECT-shaped repairs. Pure additions and
# guard-only additions go structurally null (repair_applicability near-constant),
# so they are advisory no-ops, not transfer detectors.
DEFECT_KINDS = frozenset({"deletion", "replacement"})

POOL_N = 100


def _normalize_changed_line(text: str) -> str:
    """Strip a leading unified-diff marker and surrounding whitespace.

    Stands in for ``forkb_eligibility_ours.normalize_changed_line`` (which is not
    vendored). Diff markers are already stripped by :func:`parse_patch_changes`,
    so this only trims whitespace here, but it stays defensive.
    """
    stripped = text.strip()
    if stripped[:1] in {"+", "-"} and not stripped.startswith(("++", "--")):
        stripped = stripped[1:].strip()
    return stripped


def _canonical_line(text: str) -> str:
    text = _normalize_changed_line(text)
    text = STRING_RE.sub("STR", text)
    text = NUMBER_RE.sub("NUM", text)
    return re.sub(r"\s+", " ", text.strip())


def _tokenize(text: str) -> tuple[str, ...]:
    return tuple(tok.lower() for tok in TOKEN_RE.findall(text))


def _counter_cosine(a: "collections.Counter[str]", b: "collections.Counter[str]") -> float:
    if not a or not b:
        return 0.0
    dot = sum(count * b.get(tok, 0) for tok, count in a.items())
    norm_a = math.sqrt(sum(count * count for count in a.values()))
    norm_b = math.sqrt(sum(count * count for count in b.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _extract_calls(text: str) -> "frozenset[str]":
    return frozenset(
        name
        for name in CALL_RE.findall(text)
        if name not in PY_JS_KEYWORDS and name not in {"if", "for", "while", "switch", "catch"}
    )


@dataclasses.dataclass(frozen=True)
class RepairIntent:
    kind: str
    deleted_patterns: tuple[str, ...]
    added_patterns: tuple[str, ...]
    deleted_callees: "frozenset[str]"
    added_callees: "frozenset[str]"
    changed_callees: "frozenset[str]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "deleted_patterns": list(self.deleted_patterns),
            "added_patterns": list(self.added_patterns),
            "deleted_callees": sorted(self.deleted_callees),
            "added_callees": sorted(self.added_callees),
            "changed_callees": sorted(self.changed_callees),
        }


@dataclasses.dataclass(frozen=True)
class ScorerCandidate:
    """Minimal candidate the repair scorer needs (roam symbol body, decoded)."""

    meta: dict[str, Any]
    normalized_lines: tuple[str, ...]
    call_names: "frozenset[str]"
    token_counts: "collections.Counter[str]"

    @classmethod
    def from_body(cls, meta: dict[str, Any], body: str) -> "ScorerCandidate":
        normalized = tuple(
            line for line in (_canonical_line(raw) for raw in body.splitlines()) if line
        )
        return cls(
            meta=dict(meta),
            normalized_lines=normalized,
            call_names=_extract_calls(body),
            token_counts=collections.Counter(_tokenize(body)),
        )


@dataclasses.dataclass(frozen=True)
class RankedSibling:
    meta: dict[str, Any]
    lexical: float
    repair_score: float

    def to_dict(self, rank: int) -> dict[str, Any]:
        out = dict(self.meta)
        out["rank"] = rank
        out["lexical_score"] = round(self.lexical, 4)
        out["repair_applicability"] = round(self.repair_score, 4)
        return out


def parse_patch_changes(patch_text: str) -> list[str]:
    """Extract the content of added/removed lines from a unified diff.

    Returns entries prefixed with ``+``/``-`` (the shape
    :func:`derive_repair_intent` expects), skipping diff headers and hunk lines.
    """
    changes: list[str] = []
    for raw in patch_text.splitlines():
        if raw.startswith(("+++", "---", "@@", "diff ", "index ", "\\")):
            continue
        if raw.startswith("+"):
            changes.append("+" + raw[1:])
        elif raw.startswith("-"):
            changes.append("-" + raw[1:])
    return changes


def _looks_like_guard(pattern: str) -> bool:
    return bool(re.search(r"\b(if|unless|return|raise|throw|except|catch|guard|assert)\b|^@", pattern))


def derive_repair_intent(changes: Sequence[str]) -> RepairIntent:
    """Fork-B repair-intent derivation over a list of ``+``/``-`` diff lines."""
    deleted: list[str] = []
    added: list[str] = []
    for raw in changes:
        if not isinstance(raw, str) or not raw:
            continue
        if raw.startswith("-"):
            value = _canonical_line(raw[1:])
            if value:
                deleted.append(value)
        elif raw.startswith("+"):
            value = _canonical_line(raw[1:])
            if value:
                added.append(value)
    deleted_callees = frozenset(name for pattern in deleted for name in _extract_calls(pattern))
    added_callees = frozenset(name for pattern in added for name in _extract_calls(pattern))
    changed_callees = frozenset(deleted_callees ^ added_callees)
    if deleted and added:
        kind = "replacement"
    elif deleted:
        kind = "deletion"
    elif any(_looks_like_guard(pattern) for pattern in added):
        kind = "guard_added"
    elif added:
        kind = "addition"
    else:
        kind = "unknown"
    return RepairIntent(
        kind=kind,
        deleted_patterns=tuple(dict.fromkeys(deleted)),
        added_patterns=tuple(dict.fromkeys(added)),
        deleted_callees=deleted_callees,
        added_callees=added_callees,
        changed_callees=changed_callees,
    )


def is_defect_intent(intent: RepairIntent) -> bool:
    """The scoped-verdict gate: only deletion/replacement transfer."""
    return intent.kind in DEFECT_KINDS


def has_deleted_signature(intent: RepairIntent, candidate: "ScorerCandidate") -> bool:
    """Does the candidate carry the shared *deleted-buggy-line* signature?

    The falsifier proved the DEFECT-shaped win comes from matching the shared
    deleted-buggy-line signature (not from recovering a single commit's diff).
    A candidate is only a genuine transfer target if it still contains that
    pre-fix signature: a deleted pattern line, or a deleted callee. This is the
    product's precision gate — the measured scorer stays the ranking key; this
    only decides proposal *eligibility*. Deterministic.
    """
    if any(_line_present(pattern, candidate.normalized_lines) for pattern in intent.deleted_patterns):
        return True
    if intent.deleted_callees & candidate.call_names:
        return True
    return False


def _line_present(pattern: str, lines: Sequence[str]) -> bool:
    if not pattern:
        return False
    for line in lines:
        if pattern == line:
            return True
        if len(pattern) >= 12 and pattern in line:
            return True
        if len(line) >= 12 and line in pattern:
            return True
    return False


def repair_applicability(intent: RepairIntent, candidate: ScorerCandidate) -> float:
    """The measured winner's applicability score (fork-B, kind-weighted)."""
    deleted_score = 0.0
    added_absent_score = 0.0
    callee_score = 0.0
    if intent.deleted_patterns:
        deleted_score = sum(
            1 for pattern in intent.deleted_patterns if _line_present(pattern, candidate.normalized_lines)
        ) / len(intent.deleted_patterns)
    if intent.added_patterns:
        added_absent_score = sum(
            1 for pattern in intent.added_patterns if not _line_present(pattern, candidate.normalized_lines)
        ) / len(intent.added_patterns)
    if intent.changed_callees:
        deleted_hits = len(intent.deleted_callees & candidate.call_names)
        added_missing = len(intent.added_callees - candidate.call_names)
        callee_score = (deleted_hits + added_missing) / max(1, len(intent.changed_callees))
        callee_score = min(callee_score, 1.0)
    if intent.kind == "replacement":
        return min(1.0, 0.55 * deleted_score + 0.35 * added_absent_score + 0.10 * callee_score)
    if intent.kind == "deletion":
        return min(1.0, 0.85 * deleted_score + 0.15 * callee_score)
    if intent.kind in {"guard_added", "addition"}:
        return min(1.0, 0.75 * added_absent_score + 0.25 * callee_score)
    return max(deleted_score, added_absent_score, callee_score)


def _anchor_token_counts(anchor_body: str) -> "collections.Counter[str]":
    return collections.Counter(_tokenize(anchor_body))


def _lexical_sort_key(item: tuple[ScorerCandidate, float]) -> tuple[Any, ...]:
    cand, lexical = item
    meta = cand.meta
    return (
        -lexical,
        str(meta.get("file", "")),
        str(meta.get("symbol", "")),
        int(meta.get("line_start") or 0),
        int(meta.get("id") or 0),
    )


def rerank(
    anchor_body: str,
    candidates: Sequence[ScorerCandidate],
    intent: RepairIntent,
    *,
    pool_n: int = POOL_N,
    min_lexical: float = 0.0,
    repair_floor: float = 0.0,
    require_deleted_signature: bool = True,
) -> list[RankedSibling]:
    """Freeze a top-N lexical pool, then rerank by repair applicability.

    This is the T-prime policy: ``(-repair_score, -lexical, file, symbol, start)``
    with the lexical pool frozen *before* repair scoring so the reranker can only
    reorder — never enlarge — the candidate set. Deterministic throughout.

    ``require_deleted_signature`` (default on) is the product precision gate: a
    candidate is only proposal-eligible if it carries the shared deleted-buggy
    signature (the load-bearing DEFECT-shaped feature). The measured scorer is
    unchanged; this only decides eligibility, keeping proposals precise so the
    (costly) replay-gate runs on real targets.
    """
    anchor_counts = _anchor_token_counts(anchor_body)
    scored_lexical: list[tuple[ScorerCandidate, float]] = []
    for cand in candidates:
        lexical = _counter_cosine(anchor_counts, cand.token_counts)
        if lexical < min_lexical:
            continue
        scored_lexical.append((cand, lexical))
    scored_lexical.sort(key=_lexical_sort_key)
    pool = scored_lexical[:pool_n]

    gate = require_deleted_signature and intent.kind in DEFECT_KINDS
    ranked: list[RankedSibling] = []
    for cand, lexical in pool:
        if gate and not has_deleted_signature(intent, cand):
            continue
        repair_score = repair_applicability(intent, cand)
        if repair_score <= repair_floor:
            continue
        ranked.append(RankedSibling(meta=cand.meta, lexical=lexical, repair_score=repair_score))
    ranked.sort(
        key=lambda item: (
            -item.repair_score,
            -item.lexical,
            str(item.meta.get("file", "")),
            str(item.meta.get("symbol", "")),
            int(item.meta.get("line_start") or 0),
        )
    )
    return ranked
