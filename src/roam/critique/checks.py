"""A.2 — individual checks that compose into ``roam critique``.

Each check returns a list of :class:`Finding` records that the
aggregator ranks. Checks are independent and can be run in any order.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

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
    """

    file_path: str
    hunks: tuple[tuple[int, int], ...]  # ((new_start, new_length), ...)
    additions: int = 0
    deletions: int = 0


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
    current_file: str | None = None

    for line in text.splitlines():
        m = _DIFF_FILE_RE.match(line)
        if m:
            path = m.group(1).strip()
            if path == "/dev/null":
                current_file = None
                continue
            current_file = path
            by_file.setdefault(current_file, [])
            counts.setdefault(current_file, [0, 0])
            continue

        m = _DIFF_HUNK_RE.match(line)
        if m and current_file is not None:
            new_start = int(m.group(1))
            new_length = int(m.group(2)) if m.group(2) else 1
            if new_length > 0:
                by_file[current_file].append((new_start, new_length))
            continue

        if current_file is not None and line[:1] == "+" and not line.startswith("+++"):
            counts[current_file][0] += 1
        elif current_file is not None and line[:1] == "-" and not line.startswith("---"):
            counts[current_file][1] += 1

    regions = []
    for path, hunks in by_file.items():
        adds, dels = counts.get(path, [0, 0])
        regions.append(
            ChangedRegion(
                file_path=path,
                hunks=tuple(hunks),
                additions=adds,
                deletions=dels,
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
    """
    if not regions:
        return []

    out: list[ChangedSymbol] = []
    for region in regions:
        path = region.file_path.replace("\\", "/").lstrip("./")
        if not path:
            continue

        file_id = _resolve_file_id(conn, path)
        if file_id is None:
            continue

        candidates = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, "
            "       s.line_start, s.line_end, f.path AS file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.file_id = ? AND s.line_start IS NOT NULL "
            "ORDER BY s.line_start",
            (file_id,),
        ).fetchall()

        for sym in candidates:
            sym_start = int(sym["line_start"])
            sym_end = int(sym["line_end"]) if sym["line_end"] is not None else sym_start
            for hunk_start, hunk_len in region.hunks:
                hunk_end = hunk_start + max(hunk_len - 1, 0)
                if sym_end >= hunk_start and sym_start <= hunk_end:
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
                    break  # one hunk overlap is enough

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

    findings: list[Finding] = []
    for sym in changed:
        caller_rows = conn.execute(
            "SELECT source_id FROM edges WHERE target_id = ? AND kind IN ('calls', 'references')",
            (sym.symbol_id,),
        ).fetchall()
        callers = len(caller_rows)
        if callers >= high_callers:
            severity = "high" if callers >= high_callers * 2 else "medium"
            # Hot-path bump: if any direct caller has high runtime weight,
            # escalate severity by one notch. δ signal — Phase 2 leverage
            # primitive shipped earlier this push.
            caller_ids = [int(row[0]) for row in caller_rows]
            hot_score = runtime_score_max_for_symbols(conn, caller_ids)
            if hot_score >= 0.5 and severity == "medium":
                severity = "high"
            findings.append(
                Finding(
                    check="impact",
                    severity=severity,
                    title=f"{sym.name} has {callers} direct callers",
                    detail=(
                        f"Changing {sym.name} ({sym.kind} at {sym.file_path}:"
                        f"{sym.line_start}) ripples through at least "
                        f"{callers} call sites. "
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
