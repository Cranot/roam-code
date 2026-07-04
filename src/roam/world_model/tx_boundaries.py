"""Transaction-boundary detector — classifies functions by transactional safety.

R28 sub-feature 4 of 4 (shipped in W15.4 — final world-model feature). Heuristic
detector — false negatives expected, false positives possible. Reads
function bodies from disk and matches well-known begin / commit /
rollback markers, then cross-references the world-model side-effects
classifier to know whether the function performs mutations.

Classifications
===============

For each function / method / constructor we emit exactly one of:

- ``transactional``         — has a begin marker, all mutations occur
                              inside it, and a commit OR rollback
                              closes it.
- ``partial_transactional`` — has a begin scope but at least one
                              mutation occurs OUTSIDE that scope.
- ``unsafe_mutation``       — performs mutations (io_write / mutation /
                              .commit / write_text / etc.) WITHOUT any
                              transaction wrapper.
- ``unmatched_begin``       — begin marker present but no
                              commit/rollback found (bug — leak).
- ``unmatched_commit``      — commit marker without a preceding begin
                              (bug — likely a stray call).
- ``non_transactional``     — no mutations, no transaction markers —
                              clean / read-only.
- ``unknown``               — body unreadable / file missing.

Composition
===========

We re-use the side-effects classifier (``mutations`` set = symbols whose
kinds include ``io_write`` or ``mutation``) so a function flagged
``unsafe_mutation`` here will also appear in
``roam side-effects --kind io_write``. The transaction-boundary view
adds the missing axis: *is the mutation wrapped?*

Heuristics
==========

We do NOT walk an AST.  We do a line-by-line scan of the source body
with three regex tables (begin / commit / rollback) and track a
running ``in_tx_depth: int``.  Block-exit (dedent below the with-block
indent) decrements depth.  Mutation counters are split into
``mutations_inside`` (depth > 0) and ``mutations_outside`` (depth ==
0).  See :data:`_BEGIN_PATTERNS`, :data:`_COMMIT_PATTERNS`,
:data:`_ROLLBACK_PATTERNS` for the seed catalog (~25+ patterns).

Confidence is calibrated as:

- ``high``    — matches both a begin marker AND a commit/rollback OR
                we observed clear unsafe_mutation with explicit write
                markers
- ``medium``  — one side of the pair matched (begin without commit, or
                commit without begin)
- ``low``     — heuristic was indeterminate; mutations inferred only
                from the side-effects classifier with no per-line
                marker
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from roam.db.connection import find_project_root
from roam.observability import log_swallowed
from roam.world_model.side_effects import (
    SideEffectClassification,
    classify_side_effects,
)

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

TX_CLASSIFICATIONS = (
    "transactional",
    "partial_transactional",
    "unsafe_mutation",
    "unmatched_begin",
    "unmatched_commit",
    "non_transactional",
    "unknown",
)


@dataclass
class TxBoundary:
    """Per-symbol transaction-boundary classification."""

    symbol: str
    file: str
    classification: str = "unknown"
    begin_markers: list[dict] = field(default_factory=list)
    commit_markers: list[dict] = field(default_factory=list)
    rollback_markers: list[dict] = field(default_factory=list)
    mutations_inside: int = 0
    mutations_outside: int = 0
    confidence: str = "low"
    issues: list[str] = field(default_factory=list)
    symbol_id: int = 0
    line_start: int = 0
    line_end: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "file": self.file,
            "classification": self.classification,
            "begin_markers": list(self.begin_markers),
            "commit_markers": list(self.commit_markers),
            "rollback_markers": list(self.rollback_markers),
            "mutations_inside": self.mutations_inside,
            "mutations_outside": self.mutations_outside,
            "confidence": self.confidence,
            "issues": list(self.issues),
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


# ---------------------------------------------------------------------------
# Begin / Commit / Rollback pattern catalog.
#
# Each tuple is ``(regex, label)``.  The regex matches a single line of
# function body source.  Labels are human-readable strings that surface
# in the ``begin_markers`` / ``commit_markers`` / ``rollback_markers``
# arrays so an agent can see which idiom was matched.
# ---------------------------------------------------------------------------

_BEGIN_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # Generic context-manager transactions
    (re.compile(r"\b(async\s+)?with\s+[\w\.\(\)]*\.transaction\s*\("), "with X.transaction()"),
    (re.compile(r"\b(async\s+)?with\s+[\w\.\(\)]*\.begin\s*\("), "with X.begin()"),
    (re.compile(r"\b(async\s+)?with\s+[\w\.]*engine\.begin\s*\("), "with engine.begin()"),
    (re.compile(r"\b(async\s+)?with\s+[\w\.]*Session\.begin\s*\("), "with Session.begin()"),
    # Django ORM
    (re.compile(r"\b(async\s+)?with\s+transaction\.atomic\s*\("), "with transaction.atomic()"),
    (re.compile(r"@\s*transaction\.atomic\b"), "@transaction.atomic"),
    # SQLAlchemy / generic DB cursor
    (re.compile(r"\b(async\s+)?with\s+\w+\.cursor\s*\(\s*\)\s*as\s+\w+\s*:"), "with conn.cursor() as cur"),
    (re.compile(r"\b(async\s+)?with\s+\w+\.connect\s*\(\s*\)"), "with engine.connect()"),
    # Explicit begin calls
    (re.compile(r"\b\w+\.begin_transaction\s*\("), "begin_transaction()"),
    (re.compile(r"\b\w+\.begin_nested\s*\("), "begin_nested()"),
    # Plain `obj.begin()` — tightened to known transaction-y receivers
    # (db / conn / connection / engine / session / trans / tx / cnx)
    # to avoid matching `vector::begin()` / iterator `.begin()` in C++,
    # `re.compile(...).begin()` in regex code, etc.
    (
        re.compile(r"\b(?:db|conn|connection|engine|session|trans|tx|cnx|cur|cursor)\.begin\s*\(\s*\)"),
        "db.begin()",
    ),
    # Raw SQL begin (most engines accept it)
    (
        re.compile(
            r"\.\s*execute(?:many)?\s*\(\s*['\"]\s*(BEGIN|START\s+TRANSACTION|SAVEPOINT)\b",
            re.IGNORECASE,
        ),
        "execute('BEGIN'/'START TRANSACTION')",
    ),
    # ORM-specific
    (re.compile(r"\bdb\.session\.begin\b"), "db.session.begin"),
    (re.compile(r"\bsessionmaker\s*\("), "sessionmaker()"),
    # async PG/MySQL
    (re.compile(r"\basync\s+with\s+\w+\.transaction\s*\("), "async with X.transaction()"),
)

_COMMIT_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # Plain `.commit()` — tightened to known transaction receivers to
    # avoid matching `git commit` strings in subprocess.run() args,
    # method names called `commit_changes`, etc.
    (
        re.compile(r"\b(?:db|conn|connection|engine|session|trans|tx|cnx|cur|cursor)\.commit\s*\(\s*\)"),
        "db.commit()",
    ),
    (
        re.compile(
            r"\.\s*execute(?:many)?\s*\(\s*['\"]\s*COMMIT\b",
            re.IGNORECASE,
        ),
        "execute('COMMIT')",
    ),
    (re.compile(r"\btransaction\.commit\s*\("), "transaction.commit()"),
    (re.compile(r"\bdb\.session\.commit\s*\("), "db.session.commit()"),
    (re.compile(r"\bawait\s+(?:db|conn|engine|session|trans|tx)\.commit\s*\("), "await X.commit()"),
)

_ROLLBACK_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (
        re.compile(r"\b(?:db|conn|connection|engine|session|trans|tx|cnx|cur|cursor)\.rollback\s*\(\s*\)"),
        "db.rollback()",
    ),
    (
        re.compile(
            r"\.\s*execute(?:many)?\s*\(\s*['\"]\s*ROLLBACK\b",
            re.IGNORECASE,
        ),
        "execute('ROLLBACK')",
    ),
    (re.compile(r"\btransaction\.rollback\s*\("), "transaction.rollback()"),
    (re.compile(r"\bdb\.session\.rollback\s*\("), "db.session.rollback()"),
    (re.compile(r"\bawait\s+(?:db|conn|engine|session|trans|tx)\.rollback\s*\("), "await X.rollback()"),
)

# Mutation markers within the body — used for the per-line depth scan so
# we know which lines mutate. Mirrors a subset of the side-effects source
# patterns; we keep it small/cheap because the cross-symbol classifier
# is the source of truth on "does this symbol mutate at all".
_MUTATION_LINE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(
        r"\.\s*execute(?:many)?\s*\(\s*['\"]\s*(INSERT|UPDATE|DELETE|REPLACE|MERGE|DROP|TRUNCATE|ALTER)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\.\s*save\s*\("),
    re.compile(r"\.\s*delete\s*\("),
    re.compile(r"\.\s*insert\s*\("),
    re.compile(r"\.\s*update\s*\("),
    re.compile(r"\.\s*upsert\s*\("),
    re.compile(r"\.\s*write_text\s*\("),
    re.compile(r"\.\s*write_bytes\s*\("),
    re.compile(r"\.\s*writelines\s*\("),
    re.compile(r"\bopen\s*\([^)]*['\"][wax][+ab]*['\"]"),
    re.compile(r"\bos\.(remove|unlink|rename|replace|mkdir|makedirs|rmdir|chmod|chown)\s*\("),
    re.compile(r"\bshutil\.(copy|move|rmtree|copytree)\s*\("),
    re.compile(r"\bPath\s*\([^)]*\)\.(write_text|write_bytes|unlink|rename|replace|mkdir|chmod|touch)"),
    re.compile(r"\brequests\.(post|put|patch|delete)\s*\("),
    re.compile(r"\bhttpx\.(post|put|patch|delete)\s*\("),
)

# Per-LINE substring fast-reject tokens.
#
# Every begin / commit / rollback / mutation regex above can only match a
# line that contains at least one of these literal substrings — the set is
# a strict superset of the literal anchors baked into each pattern:
#
#   begin     → "transaction" "begin" "connect" "cursor" "sessionmaker"
#               "execute" (raw-SQL BEGIN/SAVEPOINT)
#   commit    → "commit" "execute" (raw-SQL COMMIT)
#   rollback  → "rollback" "execute" (raw-SQL ROLLBACK)
#   mutation  → "execute" "save" "delete" "insert" "update" "upsert"
#               "write_text" "write_bytes" "writelines" "open" "os."
#               "shutil." "Path(" "requests." "httpx."
#
# All method-name literals are lowercase in the patterns, so a
# case-sensitive ``substr in line`` test is exact. If a line contains NONE
# of these tokens, no marker/mutation pattern can possibly match it, so the
# ~41-regex per-line scan is skipped entirely. This is output-identical:
# the token set never causes a pattern to miss a line it would have hit; it
# only eliminates wasted work on lines that match nothing (the common case
# — assignments, returns, conditionals). A microbenchmark put
# ``any(tok in line)`` ~20x cheaper than the original 41 small re.search
# calls, and far cheaper than a combined-alternation NFA gate. The drift
# guard in tests/test_tx_boundaries.py pins the superset relationship so a
# future pattern addition that introduces a new literal anchor fails loudly.
_LINE_MARKER_TOKENS: tuple[str, ...] = (
    "transaction",
    "begin",
    "connect",
    "cursor",
    "sessionmaker",
    "commit",
    "rollback",
    "execute",
    "save",
    "delete",
    "insert",
    "update",
    "upsert",
    "write_text",
    "write_bytes",
    "writelines",
    "open",
    "os.",
    "shutil.",
    "Path(",
    "requests.",
    "httpx.",
)


def _line_has_marker_token(line: str) -> bool:
    """True if ``line`` contains any token a marker/mutation pattern needs.

    Cheap fast-reject for :func:`_scan_body` — see :data:`_LINE_MARKER_TOKENS`.
    """
    for tok in _LINE_MARKER_TOKENS:
        if tok in line:
            return True
    return False


# Cheap pre-filter — skip the per-line scan when none of these appear
# anywhere in the body. Plain substring alternation; ANY hit triggers a
# full scan.
_PRE_FILTER_RE = re.compile(
    r"transaction"
    r"|begin"
    r"|commit"
    r"|rollback"
    r"|cursor"
    r"|atomic"
    r"|\.execute"
    r"|\.save"
    r"|\.delete"
    r"|\.insert"
    r"|\.update"
    r"|\.upsert"
    r"|write_text"
    r"|write_bytes"
    r"|writelines"
    r"|requests\.(?:post|put|patch|delete)"
    r"|httpx\.(?:post|put|patch|delete)"
    r"|shutil\."
    r"|os\.(?:remove|unlink|rename|replace|mkdir|makedirs|rmdir|chmod|chown)"
    r"|Path\s*\(",
    re.IGNORECASE,
)


# Derived views of the begin table: the @transaction.atomic decorator is
# matched separately in _scan_body (it opens a whole-function scope rather
# than an indent-bounded one), so split it out once at import time instead
# of label-filtering all begin patterns on every line.
_ATOMIC_LABEL = "@transaction.atomic"
_ATOMIC_DECORATOR_RE: re.Pattern = next(pat for pat, label in _BEGIN_PATTERNS if label == _ATOMIC_LABEL)
_BODY_BEGIN_PATTERNS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (pat, label) for pat, label in _BEGIN_PATTERNS if label != _ATOMIC_LABEL
)


# ---------------------------------------------------------------------------
# Per-symbol classifier
# ---------------------------------------------------------------------------


def _first_match(patterns: tuple[tuple[re.Pattern, str], ...], line: str) -> Optional[str]:
    """Return the label of the first pattern matching ``line``, else None."""
    for pat, label in patterns:
        if pat.search(line):
            return label
    return None


def _line_mutates(line: str) -> bool:
    """True if ``line`` matches any mutation pattern (one hit is enough)."""
    for pat in _MUTATION_LINE_PATTERNS:
        if pat.search(line):
            return True
    return False


def _strip_comment(line: str) -> str:
    """Strip ``# comment`` from a line (handles `' or "` quote nesting
    crudely — we only need to avoid commit() / begin() matches inside
    Python ``#`` comments).
    """
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def _line_indent(line: str) -> int:
    """Count leading-space indentation. Tabs counted as 1 (heuristic — we
    only need RELATIVE indent comparisons within a single body)."""
    n = 0
    for ch in line:
        if ch in (" ", "\t"):
            n += 1
        else:
            return n
    return n


def _scan_body(body_lines: list[str], body_line_start: int) -> dict:
    """Walk the function body line-by-line tracking transaction depth.

    Returns a dict with keys:
        ``begin_markers``, ``commit_markers``, ``rollback_markers``,
        ``mutations_inside``, ``mutations_outside``,
        ``decorator_atomic`` (bool — function decorated with
        @transaction.atomic), ``unmatched_begin_depth`` (int — depth at
        end-of-body, indicates leak).
    """
    begin_markers: list[dict] = []
    commit_markers: list[dict] = []
    rollback_markers: list[dict] = []
    mutations_inside = 0
    mutations_outside = 0
    decorator_atomic = False

    # Stack of (indent_of_with_line, begin_label).
    # When we see a `with X.transaction():` we push its indent. Any line
    # at indent > that is "inside". When we see a line at indent <=,
    # the with-block has closed → pop.
    tx_stack: list[tuple[int, str]] = []

    # We may need to look across the WHOLE body for the @transaction.atomic
    # decorator, which the caller hands us already-included.
    for offset, raw_line in enumerate(body_lines):
        line = _strip_comment(raw_line).rstrip("\n")
        if not line.strip():
            continue
        line_no = body_line_start + offset
        indent = _line_indent(raw_line)

        # Pop tx_stack when the with-block has been left.
        while tx_stack and indent <= tx_stack[-1][0]:
            tx_stack.pop()

        # Per-line fast-reject: if the line contains none of the literal
        # tokens every marker/mutation pattern requires, no pattern below
        # can match — skip all ~41 re.search calls. Output-identical:
        # _LINE_MARKER_TOKENS is a strict superset of every pattern's
        # literal anchors (see its definition). The tx_stack dedent-pop
        # above already ran, so depth tracking stays correct.
        if not _line_has_marker_token(line):
            continue

        # Decorator on the symbol itself. Treat it as a non-stack tx scope
        # (depth always 1 inside): push a sentinel indent so the rest of
        # the body counts as inside.
        if _ATOMIC_DECORATOR_RE.search(line):
            decorator_atomic = True
            begin_markers.append({"line": line_no, "pattern": _ATOMIC_LABEL})
            tx_stack.append((-1, _ATOMIC_LABEL))

        # Begin markers
        begin_hit = _first_match(_BODY_BEGIN_PATTERNS, line)
        if begin_hit:
            begin_markers.append({"line": line_no, "pattern": begin_hit})
            tx_stack.append((indent, begin_hit))

        # Commit markers — only one per line, prefer the most-specific
        commit_hit = _first_match(_COMMIT_PATTERNS, line)
        if commit_hit:
            commit_markers.append({"line": line_no, "pattern": commit_hit})
            if tx_stack:
                tx_stack.pop()

        # Rollback markers
        rollback_hit = _first_match(_ROLLBACK_PATTERNS, line)
        if rollback_hit:
            rollback_markers.append({"line": line_no, "pattern": rollback_hit})
            if tx_stack:
                tx_stack.pop()

        # Mutation markers — one mutation per line max
        if _line_mutates(line):
            if tx_stack:
                mutations_inside += 1
            else:
                mutations_outside += 1

    return {
        "begin_markers": begin_markers,
        "commit_markers": commit_markers,
        "rollback_markers": rollback_markers,
        "mutations_inside": mutations_inside,
        "mutations_outside": mutations_outside,
        "decorator_atomic": decorator_atomic,
        "unmatched_begin_depth": len(tx_stack),
    }


def _classify_one(
    se: SideEffectClassification,
    body_text: str,
    body_line_start: int,
) -> TxBoundary:
    """Classify a single symbol from its side-effects record + source body."""
    kinds = set(se.kinds or [])
    has_mutation_kind = bool(kinds & {"io_write", "mutation"})

    # Fast path: pure or read-only with no body-level transaction markers ⇒
    # non_transactional. Avoids the per-line scan on the (large) majority
    # of pure helpers.
    if kinds <= {"none", "io_read"} and (not body_text or not _PRE_FILTER_RE.search(body_text)):
        return TxBoundary(
            symbol=se.symbol,
            file=se.file,
            classification="non_transactional",
            confidence="high",
            mutations_inside=0,
            mutations_outside=0,
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # Cheap exit: mutation-bearing symbols without any pre-filter hit in
    # the body (resolved from call-edges only) ⇒ unsafe_mutation.
    if not body_text or not _PRE_FILTER_RE.search(body_text):
        cls = "non_transactional" if not has_mutation_kind else "unsafe_mutation"
        return TxBoundary(
            symbol=se.symbol,
            file=se.file,
            classification=cls,
            confidence="high" if not has_mutation_kind else "medium",
            mutations_inside=0,
            mutations_outside=1 if has_mutation_kind else 0,
            issues=(["mutation detected without transaction marker"] if cls == "unsafe_mutation" else []),
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    body_lines = body_text.splitlines(keepends=True)
    scan = _scan_body(body_lines, body_line_start)

    begin_markers = scan["begin_markers"]
    commit_markers = scan["commit_markers"]
    rollback_markers = scan["rollback_markers"]
    mutations_inside = scan["mutations_inside"]
    mutations_outside = scan["mutations_outside"]
    decorator_atomic = scan["decorator_atomic"]
    # unmatched_begin_depth is currently surfaced via scan[...] but not used
    # in the verdict aggregation below. Kept extracted for future diagnostic
    # output; suppress F841 rather than dropping the lookup.
    _unmatched_begin_depth = scan["unmatched_begin_depth"]  # noqa: F841

    n_begin = len(begin_markers)
    n_close = len(commit_markers) + len(rollback_markers)

    # The side-effects classifier may detect a mutation that our cheap
    # per-line scan missed (e.g. ``.save()`` via a call edge the
    # indexer resolved). Reflect that:
    if has_mutation_kind and mutations_inside == 0 and mutations_outside == 0:
        if n_begin > 0:
            mutations_inside = 1  # assume wrapped — conservative
        else:
            mutations_outside = 1

    total_mutations = mutations_inside + mutations_outside

    issues: list[str] = []

    # ----- Decorator path: @transaction.atomic wraps the whole function -----
    if decorator_atomic and total_mutations > 0 and mutations_outside == 0:
        return TxBoundary(
            symbol=se.symbol,
            file=se.file,
            classification="transactional",
            begin_markers=begin_markers,
            commit_markers=commit_markers,
            rollback_markers=rollback_markers,
            mutations_inside=mutations_inside,
            mutations_outside=mutations_outside,
            confidence="high",
            issues=[],
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # ----- Unmatched commit (commit without preceding begin) -----
    if n_close > 0 and n_begin == 0:
        issues.append("commit/rollback without preceding begin")
        return TxBoundary(
            symbol=se.symbol,
            file=se.file,
            classification="unmatched_commit",
            begin_markers=begin_markers,
            commit_markers=commit_markers,
            rollback_markers=rollback_markers,
            mutations_inside=mutations_inside,
            mutations_outside=mutations_outside,
            confidence="medium",
            issues=issues,
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # ----- Unmatched begin (begin without commit/rollback) -----
    # ``unmatched_begin_depth > 0`` means the body ended with a still-open
    # context-manager scope, OR there's an explicit begin() without close.
    if n_begin > 0 and n_close == 0:
        issues.append("begin without commit/rollback (transaction leak)")
        return TxBoundary(
            symbol=se.symbol,
            file=se.file,
            classification="unmatched_begin",
            begin_markers=begin_markers,
            commit_markers=commit_markers,
            rollback_markers=rollback_markers,
            mutations_inside=mutations_inside,
            mutations_outside=mutations_outside,
            confidence="high",
            issues=issues,
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # ----- Has begin AND close -----
    if n_begin > 0 and n_close > 0:
        if mutations_outside > 0 and mutations_inside > 0:
            issues.append("mutations occur both inside and outside the transaction scope")
            return TxBoundary(
                symbol=se.symbol,
                file=se.file,
                classification="partial_transactional",
                begin_markers=begin_markers,
                commit_markers=commit_markers,
                rollback_markers=rollback_markers,
                mutations_inside=mutations_inside,
                mutations_outside=mutations_outside,
                confidence="high",
                issues=issues,
                symbol_id=se.symbol_id,
                line_start=se.line_start,
                line_end=se.line_end,
            )
        if mutations_outside > 0 and mutations_inside == 0:
            issues.append("transaction opened but mutations are outside the scope")
            return TxBoundary(
                symbol=se.symbol,
                file=se.file,
                classification="partial_transactional",
                begin_markers=begin_markers,
                commit_markers=commit_markers,
                rollback_markers=rollback_markers,
                mutations_inside=mutations_inside,
                mutations_outside=mutations_outside,
                confidence="medium",
                issues=issues,
                symbol_id=se.symbol_id,
                line_start=se.line_start,
                line_end=se.line_end,
            )
        # All mutations inside (or no mutations at all): transactional
        return TxBoundary(
            symbol=se.symbol,
            file=se.file,
            classification="transactional",
            begin_markers=begin_markers,
            commit_markers=commit_markers,
            rollback_markers=rollback_markers,
            mutations_inside=mutations_inside,
            mutations_outside=mutations_outside,
            confidence="high",
            issues=[],
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # ----- No begin, no close -----
    if total_mutations == 0:
        return TxBoundary(
            symbol=se.symbol,
            file=se.file,
            classification="non_transactional",
            begin_markers=[],
            commit_markers=[],
            rollback_markers=[],
            mutations_inside=0,
            mutations_outside=0,
            confidence="high",
            issues=[],
            symbol_id=se.symbol_id,
            line_start=se.line_start,
            line_end=se.line_end,
        )

    # Mutations but no transaction markers → unsafe_mutation
    issues.append(f"{mutations_outside or total_mutations} mutation(s) outside any transaction scope")
    return TxBoundary(
        symbol=se.symbol,
        file=se.file,
        classification="unsafe_mutation",
        begin_markers=[],
        commit_markers=[],
        rollback_markers=[],
        mutations_inside=mutations_inside,
        mutations_outside=mutations_outside or total_mutations,
        confidence="high" if mutations_outside else "medium",
        issues=issues,
        symbol_id=se.symbol_id,
        line_start=se.line_start,
        line_end=se.line_end,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _load_body_with_decorators(repo_root: Path, rel_path: str, ls: int, le: int) -> tuple[str, int]:
    """Read lines [ls..le] but rewind a few lines to capture decorators.

    Returns (body_text, effective_start_line_number).
    """
    try:
        p = repo_root / rel_path
        if not p.exists():
            return "", ls
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return "", ls
    if ls <= 0:
        ls = 1
    if le <= 0 or le > len(lines):
        le = len(lines)
    # Rewind up to 5 lines to capture @transaction.atomic / @decorator.
    eff_start = ls
    for j in range(max(1, ls - 5), ls):
        s = lines[j - 1].lstrip()
        if s.startswith("@"):
            eff_start = j
            break
    return "".join(lines[eff_start - 1 : le]), eff_start


def classify_tx_boundaries(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[TxBoundary]:
    """Classify the transactional safety of functions.

    Args:
        conn: Read-only DB connection.
        symbol_name: Restrict to one symbol (matches ``name`` or
            ``qualified_name``).
        limit: Optional cap on scanned symbols.
        side_effects: Optional pre-computed side-effects list (avoids
            re-running the underlying classifier).

    Returns:
        List of :class:`TxBoundary` records.  Order: by file then by
        symbol id (stable, mirrors ``classify_side_effects``).
    """
    if side_effects is None:
        side_effects = classify_side_effects(conn, symbol_name=symbol_name, limit=limit)

    try:
        repo_root = find_project_root()
    except OSError as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # filesystem root-resolution failure downgrades per-symbol body reads to a
        # CWD-relative path; an unreadable body classifies as ``unknown``,
        # so a silent fallback would mask real transactions as unknown.
        # Surface the lineage (mirrors classify_side_effects /
        # classify_idempotency / classify_causal_graph).
        warnings.warn(
            f"find_project_root() failed in classify_tx_boundaries "
            f"({type(exc).__name__}: {exc}); falling back to Path('.') — "
            "function bodies may classify as 'unknown' if CWD isn't the repo root",
            category=RuntimeWarning,
            stacklevel=2,
        )
        repo_root = Path(".")

    # Group SE records by file so we read each file at most once.
    by_file: dict[str, list[SideEffectClassification]] = {}
    for se in side_effects:
        by_file.setdefault(se.file, []).append(se)

    out: list[TxBoundary] = []
    for file_path, items in by_file.items():
        try:
            p = repo_root / file_path
            if p.exists():
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
            else:
                all_lines = []
        except OSError as exc:
            # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — an
            # unreadable body classifies as ``unknown``, which a silent
            # fallback masks as "we read it and found no transaction".
            # Surface the lineage (rate-limited per-scope; visible under
            # ROAM_VERBOSE=1).
            log_swallowed(f"world_model.tx_boundaries:body_read:{file_path}", exc)
            all_lines = []

        for se in items:
            ls = se.line_start or 1
            le = se.line_end or ls
            if not all_lines:
                out.append(
                    TxBoundary(
                        symbol=se.symbol,
                        file=file_path,
                        classification="unknown",
                        confidence="low",
                        issues=["file unreadable"],
                        symbol_id=se.symbol_id,
                        line_start=ls,
                        line_end=le,
                    )
                )
                continue
            # Rewind for decorators (cheap). Bound j-1 by len(all_lines)
            # because indexer line_start can occasionally point past the
            # current file content (stale index, edited file).
            eff_start = ls
            n_lines = len(all_lines)
            scan_lo = max(1, ls - 5)
            scan_hi = min(ls, n_lines + 1)
            for j in range(scan_lo, scan_hi):
                s = all_lines[j - 1].lstrip()
                if s.startswith("@"):
                    eff_start = j
                    break
            if le > n_lines:
                le = n_lines
            if eff_start > n_lines:
                # Stale index — body unreadable; emit unknown.
                out.append(
                    TxBoundary(
                        symbol=se.symbol,
                        file=file_path,
                        classification="unknown",
                        confidence="low",
                        issues=["line range out of bounds (stale index?)"],
                        symbol_id=se.symbol_id,
                        line_start=ls,
                        line_end=le,
                    )
                )
                continue
            body_text = "".join(all_lines[eff_start - 1 : le])
            out.append(_classify_one(se, body_text, eff_start))
    return out


__all__ = [
    "TX_CLASSIFICATIONS",
    "TxBoundary",
    "classify_tx_boundaries",
]
