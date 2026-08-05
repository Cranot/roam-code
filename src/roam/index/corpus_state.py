"""Three-valued verdict on what an index run actually produced.

Why this module exists
----------------------
``roam init`` used to print ``Roam is ready: 2 files, 0 symbols, 0 edges``
and exit 0 for at least six structurally different situations, four of
which are outright broken. An agent (or a human) reading that transcript
has no way to tell "this repo has no code in it" from "the indexer
understood nothing and every downstream command will now return a
vacuously clean answer". That is the estate's signature defect —
an ABSENT measurement rendered as a definite success value — sitting in
the first command a new user runs.

The states
----------
``indexed``
    At least one symbol was extracted. Success, exit 0.

``no_indexable_content``
    A *legitimate* zero. The tree genuinely holds nothing roam can index
    (empty repo, docs-only repo, or every source file deliberately
    filtered by ``.gitignore`` / ``.roamignore`` / the size cap). Exit 0
    is correct, but the report must SAY the repo has no indexable content
    and name the reason — a docs repo and a repo whose ``.roamignore``
    accidentally swallowed ``src/`` look identical otherwise.

``indexing_failed``
    Refusal. Files in languages roam claims to support were indexed and
    produced zero symbols, or discovery found work the index does not
    contain. The measurement is UNKNOWN, never a benign default; callers
    must exit non-zero and name the cause.

Cost
----
The disk census only runs on the zero-symbol path, so a healthy index
pays one extra ``COUNT(*)`` and nothing else. It is deliberately built
out of ``roam.index.discovery``'s own predicates rather than a
reimplementation: ``discover_files`` stays the single source of truth
for what *should* be indexed, and this module only *explains* the
difference between that set and the files present on disk.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

STATE_INDEXED = "indexed"
STATE_NO_CONTENT = "no_indexable_content"
STATE_FAILED = "indexing_failed"

# Reason codes. Stable strings — CI, MCP wrappers and agents branch on these.
REASON_NONE = ""
REASON_EMPTY_TREE = "empty_tree"
REASON_NO_SOURCE_FILES = "no_source_files"
REASON_ALL_SOURCE_FILTERED = "all_source_filtered"
REASON_PARSERS_EXTRACTED_NOTHING = "parsers_extracted_nothing"
REASON_DISCOVERED_FILES_NOT_INDEXED = "discovered_files_not_indexed"
REASON_INDEX_UNREADABLE = "index_unreadable"

# Attribution codes for files that exist on disk but never reached the index.
FILTER_GITIGNORED = "untracked_or_gitignored"
FILTER_OVER_SIZE_CAP = "over_size_cap"
FILTER_UNREADABLE = "unreadable"
FILTER_ROAMIGNORE = "roamignore_or_config_exclude"
FILTER_GENERATED = "generated_file_heuristic"
FILTER_UNEXPLAINED = "unexplained"

# Cap the explanatory walk so a pathological tree cannot turn a
# diagnostic into a hang. Only reached when the corpus is empty anyway.
_MAX_CENSUS_FILES = 50_000
_MAX_SAMPLE = 5


@dataclass
class CorpusVerdict:
    """What the index holds, and whether that is a result or a failure."""

    state: str
    files: int = 0
    symbols: int = 0
    edges: int = 0
    parseable_files: int = 0
    parsed_files: int = 0
    reason: str = REASON_NONE
    detail: str = ""
    remediation: str = ""
    candidates_on_disk: int = 0
    filtered_by: dict[str, int] = field(default_factory=dict)
    sample: list[str] = field(default_factory=list)
    # False when the on-disk census could not run. The state is still
    # correct; the *cause* is then unconfirmed and must say so rather than
    # letting "0 candidates found" stand in for "0 candidates exist".
    census_ok: bool = True

    @property
    def ok(self) -> bool:
        """True when exit 0 is defensible (indexed, or a legitimate zero)."""
        return self.state != STATE_FAILED

    @property
    def is_empty(self) -> bool:
        """True when zero symbols were indexed, for whatever reason."""
        return self.state != STATE_INDEXED

    def as_dict(self) -> dict:
        """Envelope-ready payload. Empty explanatory fields are dropped."""
        out: dict = {
            "state": self.state,
            "files": self.files,
            "symbols": self.symbols,
            "edges": self.edges,
            "parseable_files": self.parseable_files,
            "parsed_files": self.parsed_files,
        }
        if self.reason:
            out["reason"] = self.reason
        if self.detail:
            out["detail"] = self.detail
        if self.remediation:
            out["remediation"] = self.remediation
        if self.state != STATE_INDEXED:
            out["candidates_on_disk"] = self.candidates_on_disk
            out["filtered_by"] = dict(self.filtered_by)
            if self.sample:
                out["sample"] = list(self.sample)
            if not self.census_ok:
                out["census"] = "unavailable"
        return out

    def text_lines(self) -> list[str]:
        """Human-facing disclosure block. Empty for a healthy index."""
        if self.state == STATE_INDEXED:
            return []
        lines = [self.detail]
        if self.filtered_by:
            breakdown = ", ".join(f"{k}={v}" for k, v in sorted(self.filtered_by.items()))
            lines.append(f"  Source files on disk that never reached the index: {breakdown}")
        for item in self.sample:
            lines.append(f"    {item}")
        if self.remediation:
            lines.append(f"  {self.remediation}")
        return lines


@dataclass
class _Census:
    total_files: int = 0
    candidates: int = 0
    expected_files: int = 0
    # Subset of ``expected_files`` in a language roam can extract symbols
    # from. The plain file count is NOT usable as a "discovery found work"
    # signal: ``roam init`` writes ``.roamignore`` *after* it indexes, so
    # discovery legitimately sees one more (non-source) file than the index
    # holds on a repo that had nothing to index.
    expected_source: int = 0
    filtered: dict[str, int] = field(default_factory=dict)
    sample: list[str] = field(default_factory=list)
    failed: bool = False


def _counts(conn: sqlite3.Connection) -> tuple[int, int, int, int, int] | None:
    """Return (files, symbols, edges, parseable_files, parsed_files) or None."""
    from roam.languages.registry import _SUPPORTED_LANGUAGES

    try:
        files = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] or 0)
        symbols = int(conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] or 0)
        edges = int(conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] or 0)
        langs = tuple(sorted(_SUPPORTED_LANGUAGES))
        holes = ",".join("?" * len(langs))
        parseable = int(
            conn.execute(f"SELECT COUNT(*) FROM files WHERE language IN ({holes})", langs).fetchone()[0] or 0
        )
        parsed = int(
            conn.execute(
                "SELECT COUNT(DISTINCT f.id) FROM files f "
                "JOIN symbols s ON s.file_id = f.id "
                f"WHERE f.language IN ({holes})",
                langs,
            ).fetchone()[0]
            or 0
        )
    except (sqlite3.Error, TypeError, IndexError):
        return None
    return files, symbols, edges, parseable, parsed


def _attribute(root: Path, rel: str, patterns, tracked: set[str] | None) -> str:
    """Name the filter that kept ``rel`` out of the index.

    Ordered to mirror the real pipeline: git listing first (``discover_files``
    prefers ``git ls-files``), then the ``_filter_files`` sequence.
    """
    from roam.index.discovery import (
        MAX_FILE_SIZE,
        _is_generated_content,
        _matches_exclude,
    )

    if tracked is not None and rel not in tracked:
        return FILTER_GITIGNORED
    full = root / rel
    try:
        if full.stat().st_size > MAX_FILE_SIZE:
            return FILTER_OVER_SIZE_CAP
    except OSError:
        return FILTER_UNREADABLE
    if patterns and _matches_exclude(rel, patterns):
        return FILTER_ROAMIGNORE
    try:
        if _is_generated_content(full):
            return FILTER_GENERATED
    except OSError:
        return FILTER_UNREADABLE
    return FILTER_UNEXPLAINED


def _census_disk(root: Path) -> _Census:
    """Count source files present on disk and explain the ones that were dropped.

    Only called when the corpus holds zero symbols. Any failure marks the
    census ``failed`` rather than raising — a diagnostic must never be the
    thing that breaks ``roam init``.
    """
    census = _Census()
    try:
        from roam.index.discovery import (
            _git_ls_files,
            _is_skippable,
            _walk_files,
            discover_files,
            load_exclude_patterns,
        )
        from roam.languages.registry import _is_supported_language, get_language_for_file

        walked = _walk_files(root)
        census.total_files = len(walked)
        if len(walked) > _MAX_CENSUS_FILES:
            walked = walked[:_MAX_CENSUS_FILES]

        candidates = []
        for rel in walked:
            if _is_skippable(rel):
                continue
            lang = get_language_for_file(rel)
            if lang and _is_supported_language(lang):
                candidates.append(rel)
        census.candidates = len(candidates)

        expected = set(discover_files(root))
        census.expected_files = len(expected)
        census.expected_source = sum(
            1 for rel in expected if (lang := get_language_for_file(rel)) and _is_supported_language(lang)
        )
        if not candidates:
            return census

        patterns = load_exclude_patterns(root)
        tracked_list = _git_ls_files(root)
        tracked = set(tracked_list) if tracked_list is not None else None

        for rel in candidates:
            if rel in expected:
                continue
            why = _attribute(root, rel, patterns, tracked)
            census.filtered[why] = census.filtered.get(why, 0) + 1
            if len(census.sample) < _MAX_SAMPLE:
                census.sample.append(f"{rel}  [{why}]")
    except Exception as exc:  # noqa: BLE001 — a diagnostic must not break init
        from roam.observability import log_swallowed

        log_swallowed("index.corpus_state:_census_disk", exc)
        census.failed = True
    return census


def classify(conn: sqlite3.Connection, project_root: Path | str | None = None) -> CorpusVerdict:
    """Return the three-valued verdict for the index behind ``conn``.

    ``project_root`` is only consulted on the zero-symbol path, to explain
    *why* nothing was indexed. Pass it whenever it is known; without it the
    verdict is still correct, just less specific about the cause.
    """
    counts = _counts(conn)
    if counts is None:
        return CorpusVerdict(
            state=STATE_FAILED,
            reason=REASON_INDEX_UNREADABLE,
            detail=(
                "the index database could not be queried for its contents "
                "(files/symbols/edges tables missing or unreadable) — the "
                "index build did not complete"
            ),
            remediation="Run `roam index --force` and report the error if it persists.",
        )

    files, symbols, edges, parseable, parsed = counts

    if symbols > 0:
        return CorpusVerdict(
            state=STATE_INDEXED,
            files=files,
            symbols=symbols,
            edges=edges,
            parseable_files=parseable,
            parsed_files=parsed,
            detail=f"{files} files, {symbols} symbols, {edges} edges",
        )

    # ---- zero symbols. Which of the two zeros is this? --------------------

    if parseable > 0:
        # Roam claimed it understands these languages and then extracted
        # nothing from a single one of them. Never a legitimate result:
        # every symbol-, call-graph- and dependency-based command would now
        # return a vacuously clean answer.
        return CorpusVerdict(
            state=STATE_FAILED,
            files=files,
            symbols=symbols,
            edges=edges,
            parseable_files=parseable,
            parsed_files=parsed,
            reason=REASON_PARSERS_EXTRACTED_NOTHING,
            detail=(
                f"indexing failed: {parseable} file(s) in languages roam supports "
                f"were indexed, but zero symbols were extracted from any of them. "
                f"The index is unusable — symbol, call-graph and dependency queries "
                f"would all return empty results that look clean."
            ),
            remediation=(
                "Run `roam index --force --verbose` to see the per-file parse errors, "
                "then `roam doctor` to check the grammar install."
            ),
        )

    root = Path(project_root).resolve() if project_root is not None else None
    census = _census_disk(root) if root is not None else _Census(failed=True)

    if census.expected_source > 0:
        # Discovery would hand the indexer real source files, in languages
        # roam supports, and the index holds none of them. The files were
        # dropped between discovery and write — a failure, not a filter.
        return CorpusVerdict(
            state=STATE_FAILED,
            files=files,
            symbols=symbols,
            edges=edges,
            parseable_files=parseable,
            parsed_files=parsed,
            reason=REASON_DISCOVERED_FILES_NOT_INDEXED,
            detail=(
                f"indexing failed: discovery found {census.expected_source} source file(s) to "
                f"index but the index contains none of them — they were dropped between "
                f"discovery and write."
            ),
            remediation="Check ROAM_DB_DIR / disk permissions, then run `roam index --force`.",
            candidates_on_disk=census.candidates,
            filtered_by=census.filtered,
            sample=census.sample,
        )

    # A legitimate zero — but say which kind, and why.
    if census.candidates > 0:
        reason = REASON_ALL_SOURCE_FILTERED
        detail = (
            f"no indexable content: {census.candidates} source file(s) exist on disk but "
            f"every one of them was filtered out before indexing, so this index holds "
            f"0 symbols. Nothing that depends on symbols can answer anything about this repo."
        )
        remediation = (
            "If that is unintended, check `.roamignore`, `.gitignore` and the 1MB "
            "file-size cap; `roam --include-excluded index --force` re-indexes without "
            "the .roamignore/config filters."
        )
    elif census.total_files == 0 and not census.failed:
        reason = REASON_EMPTY_TREE
        detail = "no indexable content: the project tree is empty — there are no files to index."
        remediation = ""
    else:
        reason = REASON_NO_SOURCE_FILES
        detail = (
            f"no indexable content: {files} file(s) were indexed but none are in a language "
            f"roam can extract symbols from, so this index holds 0 symbols. Nothing that "
            f"depends on symbols can answer anything about this repo."
        )
        remediation = (
            "This is expected for a docs-only or config-only repo. `roam doctor` reports which grammars are installed."
        )

    if census.failed:
        # Never let a failed census read as "nothing was there". The state
        # is still right; the cause is UNKNOWN and says so.
        detail += " (the on-disk source census could not be run, so the cause is unconfirmed)"

    return CorpusVerdict(
        state=STATE_NO_CONTENT,
        files=files,
        symbols=symbols,
        edges=edges,
        parseable_files=parseable,
        parsed_files=parsed,
        reason=reason,
        detail=detail,
        remediation=remediation,
        candidates_on_disk=census.candidates,
        filtered_by=census.filtered,
        sample=census.sample,
        census_ok=not census.failed,
    )


def classify_project(project_root: Path | str | None = None) -> CorpusVerdict:
    """``classify`` for callers that do not already hold a connection."""
    from roam.db.connection import db_exists, find_project_root, open_db

    root = Path(project_root).resolve() if project_root is not None else find_project_root(".")
    if not db_exists(root):
        return CorpusVerdict(
            state=STATE_FAILED,
            reason=REASON_INDEX_UNREADABLE,
            detail="indexing failed: no index database exists after the index run",
            remediation="Check ROAM_DB_DIR / disk permissions, then run `roam index --force`.",
        )
    with open_db(readonly=True, project_root=root) as conn:
        return classify(conn, root)
