"""A.2 — individual checks that compose into ``roam critique``.

Each check returns a list of :class:`Finding` records that the
aggregator ranks. Checks are independent and can be run in any order.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from roam.db.edge_kinds import call_or_ref_in_clause
from roam.graph.clone_detect import get_clone_siblings

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangedRegion:
    """A region of a file modified by the diff (hunks aggregated per file).

    Line numbers refer to the **new** side of the diff. Multiple hunks per
    file are collapsed into a list of (start, length) tuples for efficient
    symbol lookup.

    ``changed_lines`` (F1) is the set of new-side line numbers that were
    *actually* touched — added lines plus the new-side anchor(s) of every
    deletion. Unchanged **context** lines inside a hunk are NOT included.
    This is the load-bearing distinction for attribution: a symbol that
    merely shares a hunk with an edit (appearing only as a context line —
    e.g. ``var send = require('send')`` three lines above the real add)
    must not be reported as "changed". Validated against real third-party
    diffs (express ``send``, requests ``httpbin``, zod ``pipe``, fastapi
    ``write_file``): all four were context-line false positives that this
    field removes.
    """

    file_path: str
    hunks: tuple[tuple[int, int], ...]  # ((new_start, new_length), ...)
    additions: int = 0
    deletions: int = 0
    changed_lines: frozenset[int] = frozenset()


@dataclass(frozen=True)
class ChangedSymbol:
    """A symbol whose body overlaps at least one changed hunk."""

    symbol_id: int
    name: str
    qualified_name: str | None
    kind: str
    file_path: str
    line_start: int
    line_end: int


@dataclass
class Finding:
    """One ranked observation produced by a check."""

    check: str  # "clones-not-edited" | "impact" | "assumptions" | "intent"
    severity: str  # "high" | "medium" | "low" | "info"
    title: str
    detail: str
    evidence: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

_DIFF_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.+?)(?:\s|$)")
_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_DIFF_SHAPE_HINT_RE = re.compile(r"^(?:diff --git |index [0-9a-f]+\.\.|---(?: |/)|\+\+\+(?: |/)|@@ )", re.MULTILINE)


def looks_like_unified_diff(text: str) -> bool:
    """Return True when ``text`` carries at least one diff-shape signal.

    Used by ``roam critique`` to surface ``INVALID_DIFF`` instead of the
    silent ``no concerns`` verdict that ambiguous shell substitutions or
    truncated paste-buffers used to produce.
    """
    if not text or not text.strip():
        return False
    return bool(_DIFF_SHAPE_HINT_RE.search(text))


def parse_diff(text: str) -> list[ChangedRegion]:
    """Parse a unified diff into per-file changed regions.

    Tolerant of `git diff` and plain-`diff` headers. Skips renames,
    binary diffs, and ``/dev/null`` targets (deletions). Only the new
    side is captured — that's what symbol lookup needs.
    """
    if not text:
        return []

    by_file: dict[str, list[tuple[int, int]]] = {}
    counts: dict[str, list[int]] = {}  # file → [adds, dels]
    changed_lines: dict[str, set[int]] = {}  # file → {new-side line numbers actually touched}
    current_file: str | None = None
    # ``new_line`` tracks the current new-side line number as we walk a hunk
    # body; ``None`` outside a hunk. ``hunk_new_start`` bounds deletion
    # anchors so a deletion at the very top of a hunk never anchors before
    # the hunk's first line.
    new_line: int | None = None
    hunk_new_start = 0

    for line in text.splitlines():
        m = _DIFF_FILE_RE.match(line)
        if m:
            path = m.group(1).strip()
            new_line = None
            if path == "/dev/null":
                current_file = None
                continue
            current_file = path
            by_file.setdefault(current_file, [])
            counts.setdefault(current_file, [0, 0])
            changed_lines.setdefault(current_file, set())
            continue

        m = _DIFF_HUNK_RE.match(line)
        if m and current_file is not None:
            new_start = int(m.group(1))
            new_length = int(m.group(2)) if m.group(2) else 1
            if new_length > 0:
                by_file[current_file].append((new_start, new_length))
            new_line = new_start
            hunk_new_start = new_start
            continue

        if current_file is None:
            continue

        first = line[:1]
        if first == "+" and not line.startswith("+++"):
            counts[current_file][0] += 1
            if new_line is not None:
                # An added line occupies this new-side position.
                changed_lines[current_file].add(new_line)
                new_line += 1
        elif first == "-" and not line.startswith("---"):
            counts[current_file][1] += 1
            if new_line is not None:
                # A deleted line has no new-side position of its own; anchor
                # it to the new-side line that now sits at the gap and to the
                # line just above (so a deletion inside a symbol body is
                # attributed to that symbol regardless of which boundary the
                # symbol spans). The deletion does NOT advance ``new_line``.
                changed_lines[current_file].add(new_line)
                if new_line - 1 >= hunk_new_start:
                    changed_lines[current_file].add(new_line - 1)
        elif new_line is not None and (first == " " or line == ""):
            # Context line — unchanged; advance the cursor but do NOT mark it.
            new_line += 1
        # Any other line inside a hunk ("\ No newline at end of file", or
        # stray metadata) neither advances nor marks.

    regions = []
    for path, hunks in by_file.items():
        adds, dels = counts.get(path, [0, 0])
        regions.append(
            ChangedRegion(
                file_path=path,
                hunks=tuple(hunks),
                additions=adds,
                deletions=dels,
                changed_lines=frozenset(changed_lines.get(path, set())),
            )
        )
    return regions


# ---------------------------------------------------------------------------
# Symbol lookup
# ---------------------------------------------------------------------------


def find_changed_symbols(
    conn: sqlite3.Connection,
    regions: list[ChangedRegion],
) -> list[ChangedSymbol]:
    """Return DB symbols whose body overlaps any hunk in *regions*.

    Two paths join:

    * Files in the diff are matched against ``files.path`` exactly first,
      falling back to anchored-suffix match (same shape as
      ``_seeds_from_files`` in retrieve).
    * For each matched file, symbols whose [line_start, line_end] window
      intersects at least one hunk are returned.

    Files that do not resolve to any indexed file (untracked, generated,
    ignored) are silently skipped — the caller may treat that as a
    separate finding if desired.

    Query shape: one bulk file-resolve plus one bulk symbols query —
    constant in the number of changed files, instead of the previous
    2 queries per region (the per-region pattern was an N+1 that
    showed up flagged on roam itself).
    """
    if not regions:
        return []

    # Step 1 — normalise paths once. Use a set for the dedup membership
    # check (O(1) lookup) instead of a list (O(n) per check, which would
    # make the loop O(n²) for large diffs).
    seen_paths: set[str] = set()
    norm_paths: list[str] = []
    region_to_path: list[str] = []
    for region in regions:
        path = region.file_path.replace("\\", "/").lstrip("./")
        if path and path not in seen_paths:
            seen_paths.add(path)
            norm_paths.append(path)
        region_to_path.append(path)
    if not norm_paths:
        return []

    # Step 2 — bulk exact-path resolve. One IN query covers every region.
    path_to_fid: dict[str, int] = {}
    from roam.db.connection import batched_in

    rows = batched_in(
        conn,
        "SELECT id, path FROM files WHERE path IN ({ph})",
        norm_paths,
    )
    for row in rows:
        path_to_fid[row["path"]] = int(row["id"])

    # Step 3 — anchored-suffix fallback for paths exact-match didn't
    # catch (e.g. monorepo subroots). Single query with OR-chained LIKEs
    # so the lookup stays constant in fallback count instead of issuing
    # one query per unresolved path. Index unresolved paths by their
    # suffix-key (basename or last-segment) so the result-walk is O(rows)
    # instead of O(rows * unresolved).
    unresolved = [p for p in norm_paths if p not in path_to_fid]
    if unresolved:
        like_clauses = " OR ".join("path LIKE ?" for _ in unresolved)
        like_params = [f"%/{p}" for p in unresolved]
        rows = conn.execute(
            f"SELECT id, path FROM files WHERE {like_clauses} ORDER BY length(path) ASC",
            like_params,
        ).fetchall()
        # Build a suffix lookup so we can match each row to its unresolved
        # path in O(1) instead of scanning the unresolved list per row.
        # Hoist the candidate scan out of the assignment loop: each db_path's
        # candidate suffixes are invariant, so compute them once and reuse.
        unresolved_set = set(unresolved)
        path_candidates: dict[str, list[str]] = {}
        for row in rows:
            db_path = row["path"]
            cands = []
            if db_path in unresolved_set:
                cands.append(db_path)
            suffix_starts = [i + 1 for i, char in enumerate(db_path) if char == "/"]
            for suffix_start in reversed(suffix_starts):
                cand = db_path[suffix_start:]
                if cand in unresolved_set:
                    cands.append(cand)
            path_candidates[db_path] = cands

        # Greedy assignment in row order (shortest DB path first): give each
        # row to the first of its candidates that is still unmapped.
        for row in rows:
            file_id = int(row["id"])
            for cand in path_candidates[row["path"]]:
                if cand not in path_to_fid:
                    path_to_fid[cand] = file_id
                    break

    if not path_to_fid:
        return []

    # Step 4 — bulk symbols-by-file query. One IN over every resolved
    # file_id. Group rows by file_id in Python so the per-region hunk-
    # overlap loop reads from a dict instead of re-querying.
    sym_rows = batched_in(
        conn,
        "SELECT s.id, s.name, s.qualified_name, s.kind, "
        "       s.line_start, s.line_end, s.file_id, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.file_id IN ({ph}) AND s.line_start IS NOT NULL "
        "ORDER BY s.file_id, s.line_start",
        list(set(path_to_fid.values())),
    )
    by_fid: dict[int, list] = {}
    for sym in sym_rows:
        by_fid.setdefault(int(sym["file_id"]), []).append(sym)

    # Step 5 — per-region overlap against ACTUALLY-CHANGED lines (F1).
    #
    # A symbol is "changed" only when at least one added/deleted new-side line
    # falls inside its [line_start, line_end] window. Overlapping the raw hunk
    # *range* (the pre-F1 behaviour) counted context lines and produced the D1
    # false-positive class (a symbol three lines above the real edit reported
    # as changed). We fall back to hunk-range overlap only when a region has no
    # line-level signal (defensive: a real hunk always has ≥1 +/- line, so this
    # keeps behaviour intact for exotic diffs while the common path is exact).
    import bisect

    out: list[ChangedSymbol] = []
    for region, path in zip(regions, region_to_path):
        if not path:
            continue
        fid = path_to_fid.get(path)
        if fid is None:
            continue
        sorted_changed = sorted(region.changed_lines)
        for sym in by_fid.get(fid, ()):
            sym_start = int(sym["line_start"])
            sym_end = int(sym["line_end"]) if sym["line_end"] is not None else sym_start
            hit = False
            if sorted_changed:
                # First changed line >= sym_start; symbol is touched iff that
                # line is also <= sym_end.
                idx = bisect.bisect_left(sorted_changed, sym_start)
                hit = idx < len(sorted_changed) and sorted_changed[idx] <= sym_end
            else:
                for hunk_start, hunk_len in region.hunks:
                    hunk_end = hunk_start + max(hunk_len - 1, 0)
                    if sym_end >= hunk_start and sym_start <= hunk_end:
                        hit = True
                        break
            if hit:
                out.append(
                    ChangedSymbol(
                        symbol_id=int(sym["id"]),
                        name=sym["name"],
                        qualified_name=sym["qualified_name"],
                        kind=sym["kind"],
                        file_path=sym["file_path"],
                        line_start=sym_start,
                        line_end=sym_end,
                    )
                )

    # Deduplicate by symbol_id while preserving order.
    seen: set[int] = set()
    unique: list[ChangedSymbol] = []
    for sym in out:
        if sym.symbol_id not in seen:
            seen.add(sym.symbol_id)
            unique.append(sym)
    return unique


def _resolve_file_id(conn: sqlite3.Connection, path: str) -> int | None:
    """Look up a file id using exact, then anchored-suffix matching."""
    row = conn.execute("SELECT id FROM files WHERE path = ? LIMIT 1", (path,)).fetchone()
    if row is not None:
        return int(row[0])

    suffix = f"%/{path}" if "/" not in path else f"%/{path}"
    row = conn.execute(
        "SELECT id FROM files WHERE path LIKE ? ORDER BY length(path) ASC LIMIT 1",
        (suffix,),
    ).fetchone()
    return int(row[0]) if row is not None else None


# ---------------------------------------------------------------------------
# Check 1 — clones-not-edited (the killer signal, A.0-backed)
# ---------------------------------------------------------------------------


def check_clones_not_edited(
    conn: sqlite3.Connection,
    changed: list[ChangedSymbol],
    regions: list[ChangedRegion],
) -> list[Finding]:
    """Flag clone siblings of changed symbols that did NOT get analogous edits.

    For each changed symbol, look up its persisted clone siblings (via
    the A.0 ``clone_pairs`` table). For every sibling whose file/region
    is NOT also in the diff, emit a *high* severity finding — the same
    bug fix probably needs to ship there too.

    Requires ``roam clones --persist`` to have been run. When the clone
    table is empty, this check returns ``[]`` silently — no false alarms.
    """
    if not changed:
        return []

    # Quick existence check — empty table means the user hasn't persisted
    # clones; emit zero findings rather than nag.
    has_persisted = conn.execute("SELECT 1 FROM clone_pairs LIMIT 1").fetchone()
    if not has_persisted:
        return []

    changed_qnames = {f"{s.file_path}:{s.name}" for s in changed}
    changed_files = {r.file_path.replace("\\", "/").lstrip("./") for r in regions}

    findings: list[Finding] = []
    for sym in changed:
        siblings = get_clone_siblings(conn, sym.file_path, sym.name)
        if not siblings:
            continue

        unedited = [
            s
            for s in siblings
            if s["sibling_qname"] not in changed_qnames
            and (s.get("sibling_file") or "").replace("\\", "/").lstrip("./") not in changed_files
        ]
        if not unedited:
            continue

        # Severity scales with how many siblings we suspect.
        severity = "high" if len(unedited) >= 2 else "medium"
        title = (
            f"{sym.name} has {len(unedited)} clone sibling"
            f"{'s' if len(unedited) != 1 else ''} that may need the same change"
        )
        sibling_locs = [
            f"{s['sibling_file']}:{s['sibling_line']} ({s['sibling_func']}, sim={s['similarity']:.2f})"
            for s in unedited[:5]
        ]
        more = "" if len(unedited) <= 5 else f"\n  ... and {len(unedited) - 5} more"
        findings.append(
            Finding(
                check="clones-not-edited",
                severity=severity,
                title=title,
                detail="Unedited clone siblings:\n  " + "\n  ".join(sibling_locs) + more,
                evidence={
                    "changed_symbol": {
                        "id": sym.symbol_id,
                        "name": sym.name,
                        "file": sym.file_path,
                    },
                    "siblings": list(unedited),
                },
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Check 2 — impact (blast radius, basic version)
# ---------------------------------------------------------------------------


def _load_callers_for_impact_gate(
    conn: sqlite3.Connection,
    symbol_ids: list[int],
) -> dict[int, list[int]]:
    """Return direct caller ids grouped by changed target symbol."""
    if not symbol_ids:
        return {}

    from roam.db.connection import batched_in

    callers_by_symbol: dict[int, list[int]] = {symbol_id: [] for symbol_id in symbol_ids}
    rows = batched_in(
        conn,
        f"SELECT target_id, source_id FROM edges WHERE target_id IN ({{ph}}) AND {call_or_ref_in_clause()}",
        symbol_ids,
    )
    for row in rows:
        callers_by_symbol.setdefault(int(row[0]), []).append(int(row[1]))
    return callers_by_symbol


def check_impact(
    conn: sqlite3.Connection,
    changed: list[ChangedSymbol],
    *,
    high_callers: int = 10,
) -> list[Finding]:
    """Emit a finding for each changed symbol whose direct caller count is high.

    v12.0 ships a minimal version: count first-hop callers and warn when
    above *high_callers*. v12.1 will multiply with hotspots and vuln-reach
    once the daemon caches PageRank.
    """
    if not changed:
        return []

    from roam.runtime.hotspots import runtime_score_max_for_symbols

    # F2 — test-only symbols (fixtures, helpers) get their impact demoted. A
    # test helper's "N callers" are other tests; changing it does not ripple
    # through production. In cross-library validation, write_file / httpbin were
    # 3-line test helpers reported as high-blast — F1 already stops attributing them, and
    # this demotes any test-only symbol that does legitimately change.
    try:
        from roam.index.file_roles import is_test as _is_test_file
    except Exception:  # noqa: BLE001 — degrade gracefully if the helper moves
        _is_test_file = lambda _p: False  # noqa: E731

    _demote = {"high": "medium", "medium": "low", "low": "info", "info": "info"}

    findings: list[Finding] = []
    callers_by_symbol = _load_callers_for_impact_gate(
        conn,
        [sym.symbol_id for sym in changed],
    )
    for sym in changed:
        # W512: edge-kind vocabulary lives in roam.db.edge_kinds. Pre-W499
        # the plural-only filter matched 0 of 14,949 caller edges on roam-code
        # itself, silently no-op'ing the entire impact check.
        caller_ids = callers_by_symbol.get(sym.symbol_id, [])
        callers = len(caller_ids)
        if callers >= high_callers:
            severity = "high" if callers >= high_callers * 2 else "medium"
            # Hot-path bump: if any direct caller has high runtime weight,
            # escalate severity by one notch. δ signal — Phase 2 leverage
            # primitive shipped earlier this push.
            hot_score = runtime_score_max_for_symbols(conn, caller_ids)
            if hot_score >= 0.5 and severity == "medium":
                severity = "high"
            # F2 — demote test-only symbols one severity notch; their callers
            # are the suite, not production.
            test_only = bool(_is_test_file(sym.file_path))
            if test_only:
                severity = _demote.get(severity, severity)
            findings.append(
                Finding(
                    check="impact",
                    severity=severity,
                    title=f"{sym.name} has {callers} direct callers",
                    detail=(
                        f"Changing {sym.name} ({sym.kind} at {sym.file_path}:"
                        f"{sym.line_start}) ripples through at least "
                        f"{callers} call sites. "
                        + ("This is a test-only symbol; its callers are the test suite. " if test_only else "")
                        + (
                            f"At least one caller is on a hot runtime path (runtime_score={hot_score:.2f})."
                            if hot_score >= 0.5
                            else "Consider if any of them need updating too."
                        )
                    ),
                    evidence={
                        "symbol_id": sym.symbol_id,
                        "callers": callers,
                        "file": sym.file_path,
                        "line": sym.line_start,
                        "max_caller_runtime_score": round(hot_score, 4),
                        "test_only": test_only,
                    },
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Check 3 — intent vs semantic-diff (Meta JIT-test framing, 4× lift)
# ---------------------------------------------------------------------------


# Verbs commonly seen in PR titles / commit messages, paired with the
# expected *direction* of change. e.g. "fix" = bug-fix expected; "add" =
# new symbol expected; "remove"/"delete" = symbols expected gone. These
# are the deterministic anchor points for the intent ↔ semantic diff
# comparison; we don't try to NLP the rest.
_INTENT_VERBS: dict[str, set[str]] = {
    "add": {"add", "introduce", "create", "support", "implement", "ship"},
    "remove": {"remove", "delete", "drop", "deprecate", "kill", "retire"},
    "fix": {"fix", "fixes", "fixed", "resolve", "patch", "correct"},
    "rename": {"rename", "renamed"},
    "refactor": {"refactor", "extract", "split", "merge", "reorganize"},
    "perf": {"speed", "optimize", "optimise", "improve performance", "perf"},
    "test": {"test", "tests"},
    "doc": {"doc", "docs", "documentation", "comment", "comments"},
}


def _classify_intent(text: str) -> set[str]:
    """Return the set of intent labels detected in *text*.

    Empty set when no signal is present — the caller treats this as
    "intent unknown, skip the check" rather than as evidence of
    mismatch. Conservative on purpose; false positives are worse than
    no finding.
    """
    if not text:
        return set()
    lower = text.lower()
    found: set[str] = set()
    for label, verbs in _INTENT_VERBS.items():
        for verb in verbs:
            if verb in lower:
                found.add(label)
                break
    return found


def _semantic_summary(
    changed: list[ChangedSymbol],
    regions: list[ChangedRegion],
) -> dict[str, int]:
    """Return crude semantic counts: net adds, deletes, renames hint."""
    additions = sum(r.additions for r in regions)
    deletions = sum(r.deletions for r in regions)
    return {
        "symbols_touched": len(changed),
        "additions": additions,
        "deletions": deletions,
        "files": len({r.file_path for r in regions}),
    }


def check_intent_alignment(
    intent_text: str,
    changed: list[ChangedSymbol],
    regions: list[ChangedRegion],
) -> list[Finding]:
    """Flag obvious mismatches between stated intent and the diff's shape.

    Cheap heuristics — never claims more than the deterministic signal
    supports. Examples:

    * Intent says "add X" but the diff has zero net additions.
    * Intent says "remove X" but the diff has zero deletions.
    * Intent says "fix bug" but the diff is dominated by additions
      (could be legit, but worth a low-severity nudge).
    * Intent says "rename" but more than two symbols are touched and
      none of the file names changed.

    Returns at most one finding per intent class — the goal is a tight
    deterministic signal that pairs with the `clones-not-edited` killer,
    not a noise floor.
    """
    if not intent_text or not changed:
        return []

    labels = _classify_intent(intent_text)
    if not labels:
        return []

    summary = _semantic_summary(changed, regions)
    findings: list[Finding] = []

    if "add" in labels and summary["additions"] == 0:
        findings.append(
            Finding(
                check="intent",
                severity="medium",
                title="PR title says 'add' but the diff has no additions",
                detail=(
                    "The stated intent mentions adding something, but the "
                    "diff has zero net additions across the changed files. "
                    "Either the intent is overstated or the diff is "
                    "deletion-only."
                ),
                evidence={"intent_label": "add", **summary},
            )
        )

    if "remove" in labels and summary["deletions"] == 0:
        findings.append(
            Finding(
                check="intent",
                severity="medium",
                title="PR title says 'remove' but the diff has no deletions",
                detail=(
                    "The stated intent mentions removing something, but no "
                    "lines were deleted. Either the intent is overstated "
                    "or the change is purely additive."
                ),
                evidence={"intent_label": "remove", **summary},
            )
        )

    if "fix" in labels and summary["additions"] >= 5 * max(summary["deletions"], 1):
        findings.append(
            Finding(
                check="intent",
                severity="low",
                title="PR title says 'fix' but the diff is dominated by additions",
                detail=(
                    f"Net additions {summary['additions']} ≫ deletions "
                    f"{summary['deletions']}. Bug-fix patches usually "
                    "rewrite or delete; mostly-additive 'fix' commits are "
                    "occasionally legitimate but worth a quick second look."
                ),
                evidence={"intent_label": "fix", **summary},
            )
        )

    if "rename" in labels and summary["symbols_touched"] >= 3:
        findings.append(
            Finding(
                check="intent",
                severity="low",
                title="PR title says 'rename' but touches several unrelated symbols",
                detail=(
                    f"{summary['symbols_touched']} symbols across "
                    f"{summary['files']} files moved. Pure renames usually "
                    "touch the renamed symbol's definition + its callers; "
                    "wider blast radius suggests the diff combines a rename "
                    "with other changes."
                ),
                evidence={"intent_label": "rename", **summary},
            )
        )

    return findings
