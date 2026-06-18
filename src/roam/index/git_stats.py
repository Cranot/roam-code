"""Git statistics collection: commits, churn, co-change, blame, complexity."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import sqlite3
import subprocess
from collections import defaultdict
from itertools import combinations
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shallow-history default (W405)
# ---------------------------------------------------------------------------
#
# On FIRST index, roam pulls full git history (up to ``max_commits=5000``).
# For most agent-use cases — last-30-days churn, recent blame, co-change
# weighting — a ~365-day window carries the signal at a fraction of the
# wallclock cost. ``parse_git_log`` now accepts an optional ``since=``
# parameter; ``collect_git_stats`` resolves the default window from
# ``ROAM_GIT_SINCE`` + the "is this the first index?" check before
# delegating.
#
# Opt-out paths (in order of precedence; first non-empty wins):
#   1. Explicit ``since=`` arg to ``parse_git_log`` — wins over env.
#   2. ``ROAM_GIT_SINCE`` env var:
#        - ``"0"`` / ``"off"`` / ``"none"`` / ``"full"`` / empty   → full history
#        - ``"365d"`` / ``"12m"`` / ``"2y"`` / ``"2025-01-01"``     → that window
#   3. First-index default → ``"365d"`` (12-month shallow).
#   4. Warm index (manifest exists with prior git_head) → full history
#      already cached; the heavy pass is already skipped by the B5
#      ``_head_unchanged_since_last_run`` check above, so this rule is
#      only reached when HEAD moved.  In that case we still want the
#      since-window default so a long-lived index doesn't keep paying
#      the full-history tax on every HEAD bump.
#
# Migration safety: ``store_commits`` uses ``INSERT OR IGNORE``, so
# existing commits in the DB are preserved across re-runs with a tighter
# window. Switching the default tomorrow can only ADD commits (when
# users opt back in to full history); it never deletes recorded history.

_DEFAULT_SINCE = "365d"

# Sentinels that mean "do not pass --since": user wants the full history.
_FULL_HISTORY_SENTINELS = frozenset({"", "0", "off", "none", "false", "no", "full"})

# Tokens we accept in ``ROAM_GIT_SINCE``. Anything that doesn't match
# this regex is forwarded verbatim to git (which accepts ISO dates,
# relative phrases like ``"2 weeks ago"``, etc.).
_SHORTHAND_RE = re.compile(r"^(\d+)([dwmy])$", re.IGNORECASE)


def _normalize_since(raw: str | None) -> str | None:
    """Normalize a shorthand window into something ``git log --since=`` accepts.

    Returns ``None`` when *raw* is a full-history sentinel (caller should
    skip the ``--since`` flag entirely).  Otherwise returns the git-ready
    argument value.
    """
    if raw is None:
        return None
    token = raw.strip().lower()
    if token in _FULL_HISTORY_SENTINELS:
        return None
    m = _SHORTHAND_RE.match(token)
    if not m:
        # Forward verbatim — git accepts ISO dates + relative English.
        return raw.strip()
    n, unit = m.group(1), m.group(2).lower()
    unit_word = {"d": "days", "w": "weeks", "m": "months", "y": "years"}[unit]
    return f"{n} {unit_word} ago"


def _first_index(conn: sqlite3.Connection) -> bool:
    """Return True when the ``git_commits`` table is empty.

    Used to decide whether the shallow-history default applies.
    Existing indexes that already captured deeper history keep that
    history on subsequent runs (we never delete commits — see migration
    safety note above), so the shallow default only fires when there's
    nothing to preserve.
    """
    try:
        row = conn.execute("SELECT 1 FROM git_commits LIMIT 1").fetchone()
    except sqlite3.Error:
        # Table missing or other read error — treat as first run.
        return True
    return row is None


def _resolve_default_since(conn: sqlite3.Connection) -> str | None:
    """Resolve the effective ``--since`` window from env + state.

    Returns ``None`` when the caller should fetch full history.
    """
    raw = os.environ.get("ROAM_GIT_SINCE")
    if raw is not None:
        # Explicit env-var wins, even when empty (= full history).
        return _normalize_since(raw)
    if _first_index(conn):
        return _normalize_since(_DEFAULT_SINCE)
    # Warm index with HEAD that moved: keep paying the shallow tax
    # rather than re-fetching all 5000 commits.  Users who want the
    # full backfill set ``ROAM_GIT_SINCE=0`` once.
    return _normalize_since(_DEFAULT_SINCE)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def collect_git_stats(conn: sqlite3.Connection, project_root: Path):
    """Collect all git statistics and store them in the database.

    Silently skips if *project_root* is not inside a git repository.
    Skip-the-pass optimisation (audit B5): if the manifest's recorded
    HEAD matches the live HEAD, the commits / cochange / file_stats
    / complexity tables are already current — re-running parse_git_log
    + the four downstream passes is wasted work. Saves 1-10s per warm
    ``roam index`` run on big-history repos.

    Shallow-history default (W405): on the first index we restrict
    ``git log`` to a ~365-day window unless ``ROAM_GIT_SINCE`` says
    otherwise. Existing commits in the DB are preserved across runs
    (``INSERT OR IGNORE``), so this is a purely additive optimisation.
    """
    project_root = Path(project_root).resolve()

    if not _is_git_repo(project_root):
        log.info("Not a git repository — skipping git stats")
        return

    if _head_unchanged_since_last_run(conn, project_root):
        # W985-followup: surface BOTH the recorded HEAD AND the --force opt-out
        # so an operator running `roam health` / `roam index` and expecting
        # fresh metrics can disambiguate "nothing to do" from "broken / stale
        # index" without re-reading the manifest table. Same diagnosis-
        # shadowing shape as the W985 shallow-history filter: the existing
        # "skipping git stats pass" line was technically correct but did not
        # name the previous index's HEAD nor the opt-out, so readers had to
        # cross-reference the manifest to confirm the skip was legitimate.
        recorded_head = _recorded_head_for_log(conn)
        log.info(
            "git HEAD unchanged since last index (last: %s) — skipping git stats pass; pass --force to re-run anyway",
            recorded_head,
        )
        return

    since = _resolve_default_since(conn)
    commits = parse_git_log(project_root, since=since)
    if not commits:
        if since:
            # W985: surface the shallow-history filter as the likely cause
            # when the corpus is empty. W978 BAIL discovery showed that the
            # first hypothesis on empty-git results is usually "no commits",
            # when in fact the W405 365-day default is shadowing older
            # history. Naming the shadowing window collapses the diagnosis
            # gap from a multi-hop investigation to one log line.
            raw_env = os.environ.get("ROAM_GIT_SINCE")
            effective = raw_env if raw_env is not None else _DEFAULT_SINCE
            log.info(
                "parse_git_log returned 0 commits — ROAM_GIT_SINCE=%s may be "
                "shadowing history; set ROAM_GIT_SINCE=0 to disable shallow truncation",
                effective,
            )
        else:
            log.info("No git commits found")
        return

    if since:
        log.info("Parsed %d commits from git log (since=%s)", len(commits), since)
    else:
        log.info("Parsed %d commits from git log (full history)", len(commits))

    store_commits(conn, commits)
    compute_cochange(conn)
    compute_file_stats(conn)
    compute_complexity(conn, project_root)


def _recorded_head_for_log(conn: sqlite3.Connection) -> str:
    """Return the 7-char short SHA of the latest manifest's ``git_head``.

    W985-followup helper for the "HEAD unchanged" skip log. Returns the
    truncated SHA when the manifest is readable AND has a recorded HEAD;
    returns ``"unknown"`` otherwise. Defensive: SQLite read failures collapse
    to ``"unknown"`` rather than raising — this is a diagnostic log call,
    not a control-flow check, and the caller has already confirmed the
    skip is legitimate via ``_head_unchanged_since_last_run``.
    """
    from roam.index.manifest import latest_manifest

    try:
        prev = latest_manifest(conn)
    except sqlite3.Error:
        return "unknown"
    if not prev:
        return "unknown"
    recorded = prev.get("git_head") or ""
    if not recorded:
        return "unknown"
    return recorded[:7]


def _head_unchanged_since_last_run(conn: sqlite3.Connection, project_root: Path) -> bool:
    """Return True when the latest manifest's git_head matches live HEAD.

    Returns False (forces a re-run) when:
      * the manifest table is missing or empty (first run)
      * the manifest helper cannot be imported or the manifest read hits
        a SQLite failure (defensive: don't skip on uncertainty —
        re-running is at most a few seconds wasted, but missing a real
        change would silently stale the data)
      * the live HEAD can't be resolved (non-git or detached)
      * the recorded HEAD differs from the live HEAD
    """
    try:
        from roam.index.manifest import latest_manifest
    except ImportError:
        return False

    try:
        prev = latest_manifest(conn)
    except sqlite3.DatabaseError:
        return False
    if not prev:
        return False

    recorded_head = prev.get("git_head")
    if not recorded_head:
        return False

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.SubprocessError, OSError):
        return False
    if result.returncode != 0:
        return False
    live_head = result.stdout.strip()
    if not live_head:
        return False

    return live_head == recorded_head


# ---------------------------------------------------------------------------
# Git log parsing
# ---------------------------------------------------------------------------

_COMMIT_SEP = "COMMIT:"


def parse_git_log(
    project_root: Path,
    max_commits: int = 5000,
    since: str | None = None,
) -> list[dict]:
    """Parse ``git log --numstat`` into a list of commit dicts.

    Each dict contains::

        {
            "hash": str,
            "author": str,
            "timestamp": int,
            "message": str,
            "files": [{"path": str, "lines_added": int, "lines_removed": int}, ...]
        }

    Args:
        project_root: Repo root to run ``git log`` in.
        max_commits: Hard cap on commit count (default 5000).
        since: Optional ``--since=`` window (e.g. ``"365 days ago"``,
            ``"2025-01-01"``).  When ``None`` (or empty after stripping)
            the full history up to ``max_commits`` is fetched.  Callers
            inside the indexer pass the result of ``_resolve_default_since``;
            other callers (tests, dev scripts) can pass ``None`` for the
            legacy full-history behaviour or any git-compatible date
            phrase to opt in to a shallow fetch.
    """
    git_cmd = [
        "git",
        "log",
        "--numstat",
        "--pretty=format:COMMIT:%H|%an|%at|%s",
        "--no-merges",
        "-n",
        str(max_commits),
    ]
    if since and since.strip():
        git_cmd.append(f"--since={since}")
    result = _run_git(
        git_cmd,
        cwd=project_root,
    )
    if result is None:
        return []

    commits: list[dict] = []
    current: dict | None = None

    for line in result.stdout.splitlines():
        line = line.rstrip()

        if line.startswith(_COMMIT_SEP):
            # Flush previous commit
            if current is not None:
                commits.append(current)

            parts = line[len(_COMMIT_SEP) :].split("|", 3)
            if len(parts) < 4:
                current = None
                continue

            commit_hash, author, ts_str, message = parts
            try:
                timestamp = int(ts_str)
            except ValueError:
                timestamp = 0

            current = {
                "hash": commit_hash,
                "author": author,
                "timestamp": timestamp,
                "message": message,
                "files": [],
            }
            continue

        # numstat lines: <added>\t<removed>\t<path>
        if current is None or not line or line.isspace():
            continue

        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue

        added_str, removed_str, path = parts

        # Binary files show "-" for both columns
        try:
            lines_added = int(added_str)
        except ValueError:
            lines_added = 0
        try:
            lines_removed = int(removed_str)
        except ValueError:
            lines_removed = 0

        # Normalize path separators and handle renames ("{old => new}"")
        path = _normalize_numstat_path(path)
        if path:
            current["files"].append(
                {
                    "path": path,
                    "lines_added": lines_added,
                    "lines_removed": lines_removed,
                }
            )

    # Flush last commit
    if current is not None:
        commits.append(current)

    return commits


def _normalize_numstat_path(raw: str) -> str:
    """Normalize a numstat path, handling rename notation.

    Git uses ``{old => new}`` inside the path for renames, e.g.
    ``src/{old.py => new.py}`` or ``{a => b}/file.py``.
    We keep only the *new* side.
    """
    raw = raw.strip()
    if "{" in raw and " => " in raw:
        # Extract prefix, old=>new, suffix
        brace_start = raw.index("{")
        brace_end = raw.index("}")
        inner = raw[brace_start + 1 : brace_end]
        prefix = raw[:brace_start]
        suffix = raw[brace_end + 1 :]
        _old, new = inner.split(" => ", 1)
        raw = prefix + new + suffix
    return raw.replace("\\", "/")


# ---------------------------------------------------------------------------
# Storing commits and file changes
# ---------------------------------------------------------------------------


def store_commits(conn: sqlite3.Connection, commits: list[dict]):
    """Insert commits and their file changes into the database."""
    # Build a lookup of path -> file_id from the files table
    path_to_id = {}
    cursor = conn.execute("SELECT id, path FROM files")
    for row in cursor:
        fid = row[0] if not isinstance(row, sqlite3.Row) else row["id"]
        fpath = row[1] if not isinstance(row, sqlite3.Row) else row["path"]
        path_to_id[fpath] = fid

    with conn:
        for commit in commits:
            conn.execute(
                "INSERT OR IGNORE INTO git_commits (hash, author, timestamp, message) VALUES (?, ?, ?, ?)",
                (commit["hash"], commit["author"], commit["timestamp"], commit["message"]),
            )

            # Retrieve the commit ID (may have existed already)
            row = conn.execute("SELECT id FROM git_commits WHERE hash = ?", (commit["hash"],)).fetchone()
            if row is None:
                continue
            commit_id = row[0] if not isinstance(row, sqlite3.Row) else row["id"]

            for fc in commit["files"]:
                file_id = path_to_id.get(fc["path"])
                conn.execute(
                    "INSERT INTO git_file_changes "
                    "(commit_id, file_id, path, lines_added, lines_removed) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (commit_id, file_id, fc["path"], fc["lines_added"], fc["lines_removed"]),
                )


# ---------------------------------------------------------------------------
# Co-change matrix
# ---------------------------------------------------------------------------

_COCHANGE_CHUNK = 500  # process commits in chunks to limit memory


def compute_cochange(conn: sqlite3.Connection):
    """Build a co-change matrix from commits.

    For every commit, find all changed files that have a file_id in the files
    table, then for each ordered pair ``(a, b)`` where ``a < b``, increment
    the co-change count.
    """
    # Gather commit_id -> set of file_ids
    rows = conn.execute("SELECT commit_id, file_id FROM git_file_changes WHERE file_id IS NOT NULL").fetchall()

    commit_files: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        cid = row[0] if not isinstance(row, sqlite3.Row) else row["commit_id"]
        fid = row[1] if not isinstance(row, sqlite3.Row) else row["file_id"]
        commit_files[cid].add(fid)

    # Accumulate pair counts
    pair_counts: dict[tuple[int, int], int] = defaultdict(int)
    for file_ids in commit_files.values():
        if len(file_ids) < 2 or len(file_ids) > 100:
            # Skip trivially small or very large commits (likely bulk reformats)
            continue
        for a, b in combinations(sorted(file_ids), 2):
            pair_counts[(a, b)] += 1

    # Write in chunks
    with conn:
        conn.execute("DELETE FROM git_cochange")
        batch: list[tuple[int, int, int]] = []
        for (a, b), count in pair_counts.items():
            batch.append((a, b, count))
            if len(batch) >= _COCHANGE_CHUNK:
                conn.executemany(
                    "INSERT INTO git_cochange (file_id_a, file_id_b, cochange_count) VALUES (?, ?, ?)",
                    batch,
                )
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO git_cochange (file_id_a, file_id_b, cochange_count) VALUES (?, ?, ?)",
                batch,
            )

    log.info("Computed co-change for %d file pairs", len(pair_counts))

    # --- Co-change entropy per file ---
    _compute_cochange_entropy(conn, pair_counts)

    # --- Hypergraph: store full commit-level file sets ---
    _populate_hyperedges(conn, commit_files)


def _compute_cochange_entropy(
    conn: sqlite3.Connection,
    pair_counts: dict[tuple[int, int], int],
):
    """Compute Renyi entropy (order 2) of co-change distribution per file.

    Uses Renyi entropy H2 = -log2(sum(p_i^2)) instead of Shannon entropy.
    Renyi-2 is more robust to outlier partners (one-off co-changes) and
    gives more weight to the dominant co-change pattern, making it a better
    discriminator for "shotgun surgery" detection.

    High entropy = file changes with many different partners (shotgun surgery).
    Low entropy = file changes with a consistent set of partners (focused).
    Stored as normalized entropy [0, 1] in file_stats.cochange_entropy.

    Reference: Renyi (1961), "On Measures of Entropy and Information."
    """
    # Aggregate: for each file, sum co-change counts per partner
    file_partners: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for (a, b), count in pair_counts.items():
        file_partners[a][b] += count
        file_partners[b][a] += count

    updates: list[tuple[float, int]] = []
    for fid, partners in file_partners.items():
        total = sum(partners.values())
        if total == 0 or len(partners) <= 1:
            updates.append((0.0, fid))
            continue
        # Renyi entropy of order 2: H2 = -log2(sum(p_i^2))
        sum_p_sq = 0.0
        for count in partners.values():
            p = count / total
            sum_p_sq += p * p
        entropy = -math.log2(sum_p_sq) if sum_p_sq > 0 else 0.0
        # Normalize: max Renyi-2 entropy = log2(N) (uniform distribution)
        max_entropy = math.log2(len(partners))
        norm_entropy = entropy / max_entropy if max_entropy > 0 else 0.0
        updates.append((round(norm_entropy, 4), fid))

    with conn:
        for entropy_val, fid in updates:
            conn.execute(
                "UPDATE file_stats SET cochange_entropy = ? WHERE file_id = ?",
                (entropy_val, fid),
            )

    log.info("Computed co-change entropy for %d files", len(updates))


def _populate_hyperedges(
    conn: sqlite3.Connection,
    commit_files: dict[int, set[int]],
):
    """Store n-ary commit patterns as hyperedges.

    Each qualifying commit (2-100 files) produces one ``git_hyperedges`` row
    and N ``git_hyperedge_members`` rows.  A ``sig_hash`` (truncated SHA-256
    of sorted file IDs) enables O(1) pattern matching later.
    """
    with conn:
        conn.execute("DELETE FROM git_hyperedge_members")
        conn.execute("DELETE FROM git_hyperedges")

        edge_batch: list[tuple] = []
        member_batch: list[tuple] = []
        edge_id = 0

        for commit_id, file_ids in commit_files.items():
            n = len(file_ids)
            if n < 2 or n > 100:
                continue

            sorted_ids = sorted(file_ids)
            sig = hashlib.sha256("|".join(str(fid) for fid in sorted_ids).encode()).hexdigest()[:16]

            edge_id += 1
            edge_batch.append((edge_id, commit_id, n, sig))

            for ordinal, fid in enumerate(sorted_ids):
                member_batch.append((edge_id, fid, ordinal))

            if len(edge_batch) >= 500:
                conn.executemany(
                    "INSERT INTO git_hyperedges (id, commit_id, file_count, sig_hash) VALUES (?, ?, ?, ?)",
                    edge_batch,
                )
                conn.executemany(
                    "INSERT INTO git_hyperedge_members (hyperedge_id, file_id, ordinal) VALUES (?, ?, ?)",
                    member_batch,
                )
                edge_batch.clear()
                member_batch.clear()

        if edge_batch:
            conn.executemany(
                "INSERT INTO git_hyperedges (id, commit_id, file_count, sig_hash) VALUES (?, ?, ?, ?)",
                edge_batch,
            )
        if member_batch:
            conn.executemany(
                "INSERT INTO git_hyperedge_members (hyperedge_id, file_id, ordinal) VALUES (?, ?, ?)",
                member_batch,
            )

    log.info("Stored %d hyperedges", edge_id)


# ---------------------------------------------------------------------------
# Per-file stats
# ---------------------------------------------------------------------------


def compute_file_stats(conn: sqlite3.Connection):
    """Compute per-file aggregate statistics from git data.

    Populates *file_stats* with commit_count, total_churn, and distinct_authors
    for every file that has at least one recorded change.
    """
    rows = conn.execute(
        """
        SELECT
            gfc.file_id,
            COUNT(DISTINCT gfc.commit_id)  AS commit_count,
            SUM(gfc.lines_added + gfc.lines_removed) AS total_churn,
            COUNT(DISTINCT gc.author) AS distinct_authors
        FROM git_file_changes gfc
        JOIN git_commits gc ON gfc.commit_id = gc.id
        WHERE gfc.file_id IS NOT NULL
        GROUP BY gfc.file_id
        """
    ).fetchall()

    with conn:
        for row in rows:
            fid = row[0]
            commit_count = row[1]
            total_churn = row[2] or 0
            distinct_authors = row[3]
            conn.execute(
                "INSERT OR REPLACE INTO file_stats "
                "(file_id, commit_count, total_churn, distinct_authors, complexity) "
                "VALUES (?, ?, ?, ?, COALESCE("
                "  (SELECT complexity FROM file_stats WHERE file_id = ?), 0))",
                (fid, commit_count, total_churn, distinct_authors, fid),
            )

    log.info("Computed file stats for %d files", len(rows))


# ---------------------------------------------------------------------------
# Indentation-based complexity
# ---------------------------------------------------------------------------


def compute_complexity(conn: sqlite3.Connection, project_root: Path):
    """Compute indentation-based complexity for every indexed file.

    Complexity is defined as ``avg_indent * max_indent`` (both measured in
    units of 4 spaces), giving a rough proxy for nesting depth.
    """
    project_root = Path(project_root).resolve()
    cursor = conn.execute("SELECT id, path FROM files")
    files = cursor.fetchall()

    updates: list[tuple[float, int]] = []
    for row in files:
        fid = row[0] if not isinstance(row, sqlite3.Row) else row["id"]
        fpath = row[1] if not isinstance(row, sqlite3.Row) else row["path"]
        full_path = project_root / fpath

        complexity = _measure_indent_complexity(full_path)
        if complexity is not None:
            updates.append((complexity, fid))

    with conn:
        for complexity, fid in updates:
            conn.execute(
                "UPDATE file_stats SET complexity = ? WHERE file_id = ?",
                (complexity, fid),
            )
            # If the row doesn't exist yet, create it
            if conn.execute("SELECT 1 FROM file_stats WHERE file_id = ?", (fid,)).fetchone() is None:
                conn.execute(
                    "INSERT INTO file_stats (file_id, complexity) VALUES (?, ?)",
                    (fid, complexity),
                )

    log.info("Computed complexity for %d files", len(updates))


def _measure_indent_complexity(path: Path) -> float | None:
    """Measure indentation complexity of a single file.

    Returns ``avg_indent * max_indent`` normalized to units of 4 spaces,
    or *None* if the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None

    indents: list[float] = []
    for line in text.splitlines():
        if not line or line.isspace():
            continue
        stripped = line.lstrip()
        if not stripped:
            continue
        leading = len(line) - len(stripped)
        # Normalize tabs to 4 spaces
        tab_count = line[:leading].count("\t")
        space_count = leading - tab_count
        indent_units = tab_count + space_count / 4.0
        indents.append(indent_units)

    if not indents:
        return 0.0

    avg_indent = sum(indents) / len(indents)
    max_indent = max(indents)
    return round(avg_indent * max_indent, 2)


# ---------------------------------------------------------------------------
# On-demand blame
# ---------------------------------------------------------------------------


def get_blame_for_file(project_root: Path, file_path: str) -> list[dict]:
    """Run ``git blame --line-porcelain`` on a file and parse the output.

    Returns a list of dicts, one per source line::

        {"author": str, "timestamp": int, "line": str, "commit_hash": str}
    """
    project_root = Path(project_root).resolve()
    result = _run_git(
        ["git", "blame", "--line-porcelain", file_path],
        cwd=project_root,
    )
    if result is None:
        return []

    entries: list[dict] = []
    current_hash: str = ""
    current_author: str = ""
    current_ts: int = 0

    for line in result.stdout.splitlines():
        # A new blame chunk starts with a 40-hex commit hash
        if len(line) >= 40 and line[0] in "0123456789abcdef" and " " in line:
            token = line.split()[0]
            if len(token) == 40:
                current_hash = token

        if line.startswith("author "):
            current_author = line[len("author ") :]
        elif line.startswith("author-time "):
            try:
                current_ts = int(line[len("author-time ") :])
            except ValueError:
                current_ts = 0
        elif line.startswith("\t"):
            # The actual source line (tab-prefixed)
            entries.append(
                {
                    "author": current_author,
                    "timestamp": current_ts,
                    "line": line[1:],  # strip leading tab
                    "commit_hash": current_hash,
                }
            )

    return entries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_git_repo(path: Path) -> bool:
    """Check whether *path* is inside a git work tree."""
    result = _run_git(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
    )
    return result is not None and result.stdout.strip() == "true"


def _run_git(cmd: list[str], *, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess | None:
    """Run a git command, returning *None* on failure.

    Uses ``worktree_git_env`` so parallel agents in sibling worktrees don't
    contend on ``.git/index.lock``.
    """
    from roam.git_utils import worktree_git_env

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=worktree_git_env(cwd),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("git command failed: %s", exc)
        return None

    if result.returncode != 0:
        log.debug("git %s returned %d: %s", cmd[1], result.returncode, result.stderr.strip())
        return None

    return result
