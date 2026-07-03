"""Repo-local agent memory store.

Append-only JSONL at ``.roam/memory.jsonl``. One entry per line so
streaming reads / writes are crash-safe and concurrent-append friendly
on POSIX + Windows (Path.open("a") + a single write() per entry keeps
each line atomic for sub-PIPE_BUF writes; entries here are well under
that threshold).

The substrate is intentionally minimal — schema + I/O + a simple
relevance ranker. Higher-level orchestration (run ledger, constitution
violations, agent-export merging) lives in commands that build on top.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEMORY_DIR_NAME = ".roam"
MEMORY_FILE_NAME = "memory.jsonl"

VALID_KINDS = {"fact", "convention", "warning", "decision", "context"}
VALID_CONFIDENCES = {"low", "medium", "high"}

# Token splitter: alnum + underscore + dot/slash (paths & symbol names).
# We keep separators because "auth/login.py" should still score against
# tags like "auth" or files like "login.py" — done by also splitting on
# them at lookup time.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """A single repo-local memory entry.

    All fields except ``id`` and ``ts`` are caller-supplied. ``id`` is
    auto-generated from a hash of (ts, kind, subject, body) for stable
    de-duplication; ``ts`` defaults to UTC now in ISO-8601.
    """

    kind: str
    subject: str
    body: str
    agent: str = "human"
    confidence: str = "medium"
    tags: list[str] = field(default_factory=list)
    relevance_signals: dict = field(default_factory=lambda: {"symbols": [], "files": [], "topics": []})
    id: str = ""
    ts: str = ""

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if not self.id:
            self.id = _make_id(self)
        if self.kind not in VALID_KINDS:
            raise ValueError(f"invalid kind {self.kind!r}; expected one of {sorted(VALID_KINDS)}")
        if self.confidence not in VALID_CONFIDENCES:
            raise ValueError(f"invalid confidence {self.confidence!r}; expected one of {sorted(VALID_CONFIDENCES)}")
        if not isinstance(self.tags, list):
            raise ValueError("tags must be a list of strings")
        # Normalise relevance_signals shape so consumers don't have to
        # KeyError-guard every read.
        rs = self.relevance_signals or {}
        self.relevance_signals = {
            "symbols": list(rs.get("symbols", []) or []),
            "files": list(rs.get("files", []) or []),
            "topics": list(rs.get("topics", []) or []),
        }

    def to_dict(self) -> dict:
        """Plain-dict wire shape for memory JSONL and JSON envelopes.

        Referenced by the in-file JSONL writer and by ``cmd_memory.py`` JSON
        envelopes. Kept as the one-call serializer for embedders so callers do
        not reimplement the dataclass wire shape — reviewed 2026-07-02.
        """
        return asdict(self)


def _make_id(entry: MemoryEntry) -> str:
    """Deterministic id derived from entry content + timestamp.

    Using a hash gives:
      - de-dup on identical (ts, kind, subject, body) entry fields
      - URL-safe / shell-safe (no special chars)
      - short enough to print inline (12 hex chars ≈ 48 bits, plenty for
        per-repo memory volumes — collision probability negligible)
    """
    payload = f"{entry.ts}|{entry.kind}|{entry.subject}|{entry.body}".encode("utf-8")
    digest = hashlib.sha1(payload).hexdigest()[:12]
    # Prefix with "mem_" so the id is self-describing in logs.
    return f"mem_{digest}"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def memory_path(repo_root: Path) -> Path:
    """Resolve the memory JSONL path for *repo_root*.

    Does NOT create the file or its parent directory — that's a write-
    time concern. Read-side callers can use ``.exists()`` to detect the
    no-memory state.
    """
    return Path(repo_root) / MEMORY_DIR_NAME / MEMORY_FILE_NAME


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _warn_skipped_memory_line(reason: str, exc: Exception) -> None:
    sys.stderr.write(f"[memory] skipped malformed memory.jsonl line ({reason}): {exc}\n")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def add_memory(repo_root: Path, entry: MemoryEntry) -> str:
    """Append *entry* to ``.roam/memory.jsonl``. Returns the entry id.

    The append is a single ``write()`` of one JSON line. On both POSIX
    and Windows that is atomic for the sizes we expect (memory bodies
    are short prose, never multi-MB blobs).
    """
    path = memory_path(repo_root)
    _ensure_parent(path)
    line = json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return entry.id


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _parse_line(line: str) -> MemoryEntry | None:
    """Parse a JSONL line into a MemoryEntry, returning None on error.

    Tolerant by design: a corrupted line shouldn't kill the stream.
    """
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        _warn_skipped_memory_line("invalid JSON", exc)
        return None
    if not isinstance(raw, dict):
        return None
    try:
        # Allow forward-compat fields we don't know about by stripping
        # to the known dataclass fields.
        known = {"id", "ts", "kind", "subject", "body", "agent", "confidence", "tags", "relevance_signals"}
        kwargs = {k: v for k, v in raw.items() if k in known}
        return MemoryEntry(**kwargs)
    except (TypeError, ValueError) as exc:
        _warn_skipped_memory_line("invalid entry", exc)
        return None


def _entry_is_visible_to_memory_query(entry: MemoryEntry, *, since: str | None, kind: str | None) -> bool:
    """Keep list_memory's filtering contract out of the streaming loop."""
    if kind is not None and entry.kind != kind:
        return False
    if since is not None and entry.ts < since:
        return False
    return True


def list_memory(
    repo_root: Path,
    since: str | None = None,
    kind: str | None = None,
) -> Iterator[MemoryEntry]:
    """Stream entries from the memory JSONL.

    Filters:
      - ``since``: ISO-8601 string; entries with ``ts >= since`` are returned
      - ``kind``: only entries with this kind

    Yields nothing if the file is missing — callers must handle the
    "no memory yet" state explicitly. Corrupt lines are skipped.
    """
    path = memory_path(repo_root)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            entry = _parse_line(line)
            if entry is None:
                continue
            if not _entry_is_visible_to_memory_query(entry, since=since, kind=kind):
                continue
            yield entry


# ---------------------------------------------------------------------------
# Relevance ranker
# ---------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    """Lowercase alnum token bag for set-overlap scoring."""
    if not text:
        return set()
    return {tok.lower() for tok in _TOKEN_RE.findall(text)}


def _path_tokens(path: str) -> set[str]:
    """Tokenise a file path into its components (sans separators)."""
    if not path:
        return set()
    # Split on / \ . so "auth/login.py" → {auth, login, py}
    parts = re.split(r"[\\/.]+", path.lower())
    return {p for p in parts if p}


def _entry_tokens(entry: MemoryEntry) -> set[str]:
    """All searchable tokens for an entry: subject + body + tags + signals."""
    bag: set[str] = set()
    bag |= _tokens(entry.subject)
    bag |= _path_tokens(entry.subject)
    bag |= _tokens(entry.body)
    for tag in entry.tags:
        bag |= _tokens(tag)
    rs = entry.relevance_signals or {}
    for sym in rs.get("symbols", []) or []:
        bag |= _tokens(sym)
    for fp in rs.get("files", []) or []:
        bag |= _tokens(fp)
        bag |= _path_tokens(fp)
    for topic in rs.get("topics", []) or []:
        bag |= _tokens(topic)
    return bag


def _score(entry: MemoryEntry, query_tokens: set[str], symbol_tokens: set[str], file_tokens: set[str]) -> float:
    """Set-overlap score with mild weighting for explicit signals.

    Weights are tuned for the substrate; the wider system can pull this
    out into a config knob later if needed. Anchor design choices:
      - free-text query tokens count 1.0
      - explicit ``symbols=`` matches count 2.0 (caller knows the symbol)
      - explicit ``files=`` matches count 1.5
      - confidence boosts the final score multiplicatively
    """
    ebag = _entry_tokens(entry)
    if not ebag:
        return 0.0
    score = 0.0
    if query_tokens:
        score += 1.0 * len(query_tokens & ebag)
    if symbol_tokens:
        # Compare against the entry's symbol-bias bag (signals + subject).
        sig_bag: set[str] = set()
        for sym in (entry.relevance_signals or {}).get("symbols", []) or []:
            sig_bag |= _tokens(sym)
        sig_bag |= _tokens(entry.subject)
        score += 2.0 * len(symbol_tokens & sig_bag)
    if file_tokens:
        file_bag: set[str] = set()
        for fp in (entry.relevance_signals or {}).get("files", []) or []:
            file_bag |= _path_tokens(fp)
        file_bag |= _path_tokens(entry.subject)
        score += 1.5 * len(file_tokens & file_bag)
    if score == 0.0:
        return 0.0
    confidence_boost = {"low": 0.8, "medium": 1.0, "high": 1.2}.get(entry.confidence, 1.0)
    return score * confidence_boost


def relevant_memory(
    repo_root: Path,
    query: str = "",
    symbols: list[str] | None = None,
    files: list[str] | None = None,
    top: int = 10,
) -> list[tuple[MemoryEntry, float]]:
    """Return up to *top* (entry, score) pairs ranked by relevance.

    Pure set-overlap — no embeddings, no TF-IDF. Sufficient for the
    R19 substrate; relevance quality is a separate downstream concern.
    Ties broken by recency (later ``ts`` wins).
    """
    symbols = symbols or []
    files = files or []
    q_tokens = _tokens(query)
    s_tokens: set[str] = set()
    for s in symbols:
        s_tokens |= _tokens(s)
    f_tokens: set[str] = set()
    for fp in files:
        f_tokens |= _path_tokens(fp)
        f_tokens |= _tokens(fp)

    scored: list[tuple[MemoryEntry, float]] = []
    for entry in list_memory(repo_root):
        s = _score(entry, q_tokens, s_tokens, f_tokens)
        if s > 0.0:
            scored.append((entry, s))

    # Sort by (score desc, ts desc) — recent ties first.
    scored.sort(key=lambda pair: (pair[1], pair[0].ts), reverse=True)
    return scored[: max(0, int(top))]
