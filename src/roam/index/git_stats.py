"""Git statistics collection: commits, churn, co-change, blame, complexity."""

from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
import subprocess
import time as _time
from collections import defaultdict
from itertools import combinations
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def collect_git_stats(conn: sqlite3.Connection, project_root: Path):
    """Collect all git statistics and store them in the database.

    Silently skips if *project_root* is not inside a git repository.
    """
    project_root = Path(project_root).resolve()

    if not _is_git_repo(project_root):
        log.info("Not a git repository — skipping git stats")
        return

    commits = parse_git_log(project_root)
    if not commits:
        log.info("No git commits found")
        return

    log.info("Parsed %d commits from git log", len(commits))

    store_commits(conn, commits)
    compute_cochange(conn)
    compute_file_stats(conn)
    compute_complexity(conn, project_root)


# ---------------------------------------------------------------------------
# Git log parsing
# ---------------------------------------------------------------------------

_COMMIT_SEP = "COMMIT:"


def parse_git_log(project_root: Path, max_commits: int = 5000) -> list[dict]:
    """Parse ``git log --numstat`` into a list of commit dicts.

    Each dict contains::

        {
            "hash": str,
            "author": str,
            "timestamp": int,
            "message": str,
            "files": [{"path": str, "lines_added": int, "lines_removed": int}, ...]
        }
    """
    result = _run_git(
        [
            "git", "log",
            "--numstat",
            "--pretty=format:COMMIT:%H|%an|%at|%s",
            "--no-merges",
            "-n", str(max_commits),
        ],
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

            parts = line[len(_COMMIT_SEP):].split("|", 3)
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
            current["files"].append({
                "path": path,
                "lines_added": lines_added,
                "lines_removed": lines_removed,
            })

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
        inner = raw[brace_start + 1:brace_end]
        prefix = raw[:brace_start]
        suffix = raw[brace_end + 1:]
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
                "INSERT OR IGNORE INTO git_commits (hash, author, timestamp, message) "
                "VALUES (?, ?, ?, ?)",
                (commit["hash"], commit["author"], commit["timestamp"], commit["message"]),
            )

            # Retrieve the commit ID (may have existed already)
            row = conn.execute(
                "SELECT id FROM git_commits WHERE hash = ?", (commit["hash"],)
            ).fetchone()
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
    rows = conn.execute(
        "SELECT commit_id, file_id FROM git_file_changes WHERE file_id IS NOT NULL"
    ).fetchall()

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
                    "INSERT INTO git_cochange (file_id_a, file_id_b, cochange_count) "
                    "VALUES (?, ?, ?)",
                    batch,
                )
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO git_cochange (file_id_a, file_id_b, cochange_count) "
                "VALUES (?, ?, ?)",
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
    """Compute Shannon entropy of co-change distribution per file.

    High entropy = file changes with many different partners (shotgun surgery).
    Low entropy = file changes with a consistent set of partners (focused).
    Stored as normalized entropy [0, 1] in file_stats.cochange_entropy.
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
        entropy = 0.0
        for count in partners.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
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
            sig = hashlib.sha256(
                "|".join(str(fid) for fid in sorted_ids).encode()
            ).hexdigest()[:16]

            edge_id += 1
            pair_count = n * (n - 1) // 2
            edge_batch.append((edge_id, commit_id, n, sig))

            for ordinal, fid in enumerate(sorted_ids):
                member_batch.append((edge_id, fid, ordinal))

            if len(edge_batch) >= 500:
                conn.executemany(
                    "INSERT INTO git_hyperedges (id, commit_id, file_count, sig_hash) "
                    "VALUES (?, ?, ?, ?)",
                    edge_batch,
                )
                conn.executemany(
                    "INSERT INTO git_hyperedge_members (hyperedge_id, file_id, ordinal) "
                    "VALUES (?, ?, ?)",
                    member_batch,
                )
                edge_batch.clear()
                member_batch.clear()

        if edge_batch:
            conn.executemany(
                "INSERT INTO git_hyperedges (id, commit_id, file_count, sig_hash) "
                "VALUES (?, ?, ?, ?)",
                edge_batch,
            )
        if member_batch:
            conn.executemany(
                "INSERT INTO git_hyperedge_members (hyperedge_id, file_id, ordinal) "
                "VALUES (?, ?, ?)",
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
            if conn.execute(
                "SELECT 1 FROM file_stats WHERE file_id = ?", (fid,)
            ).fetchone() is None:
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


def get_blame_for_file(
    project_root: Path, file_path: str
) -> list[dict]:
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
            current_author = line[len("author "):]
        elif line.startswith("author-time "):
            try:
                current_ts = int(line[len("author-time "):])
            except ValueError:
                current_ts = 0
        elif line.startswith("\t"):
            # The actual source line (tab-prefixed)
            entries.append({
                "author": current_author,
                "timestamp": current_ts,
                "line": line[1:],  # strip leading tab
                "commit_hash": current_hash,
            })

    return entries


def get_symbol_blame(
    conn: sqlite3.Connection, project_root: Path, symbol_id: int
) -> dict:
    """Get aggregated blame info for a symbol's line range.

    Returns a dict keyed by author::

        {
            "author_name": {
                "lines": int,
                "commits": set_count,
                "first_date": int (epoch),
                "last_date": int (epoch),
            }
        }
    """
    row = conn.execute(
        "SELECT s.line_start, s.line_end, f.path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.id = ?",
        (symbol_id,),
    ).fetchone()
    if row is None:
        return {}

    line_start = row[0] if not isinstance(row, sqlite3.Row) else row["line_start"]
    line_end = row[1] if not isinstance(row, sqlite3.Row) else row["line_end"]
    file_path = row[2] if not isinstance(row, sqlite3.Row) else row["path"]

    if line_start is None or line_end is None:
        return {}

    blame = get_blame_for_file(project_root, file_path)
    if not blame:
        return {}

    # Filter to the symbol's line range (1-indexed)
    relevant = blame[line_start - 1: line_end]

    authors: dict[str, dict] = {}
    for entry in relevant:
        author = entry["author"]
        if author not in authors:
            authors[author] = {
                "lines": 0,
                "commits": set(),
                "first_date": entry["timestamp"],
                "last_date": entry["timestamp"],
            }
        info = authors[author]
        info["lines"] += 1
        info["commits"].add(entry["commit_hash"])
        if entry["timestamp"] < info["first_date"]:
            info["first_date"] = entry["timestamp"]
        if entry["timestamp"] > info["last_date"]:
            info["last_date"] = entry["timestamp"]

    # Convert commit sets to counts for JSON-friendliness
    for info in authors.values():
        info["commits"] = len(info["commits"])

    return authors


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


def _run_git(
    cmd: list[str], *, cwd: Path, timeout: int = 120
) -> subprocess.CompletedProcess | None:
    """Run a git command, returning *None* on failure."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("git command failed: %s", exc)
        return None

    if result.returncode != 0:
        log.debug("git %s returned %d: %s", cmd[1], result.returncode, result.stderr.strip())
        return None

    return result
