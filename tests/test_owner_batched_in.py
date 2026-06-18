"""Regression test for P0-A: ``roam owner <large-dir>`` must not crash on
directories with more files than SQLITE_MAX_VARIABLE_NUMBER (default 999).

History: ``cmd_owner._show_dir_owner`` and the JSON dir path bound the full
directory file-id set into a raw ``WHERE file_id IN ({placeholders})`` clause
(one ``?`` per id). At >999 files SQLite raises ``OperationalError: too many
SQL variables`` and the command crashes outright — trivially reached on a
monorepo top-level directory. The fix routes both queries through
``batched_in`` and re-aggregates in Python.

These tests exercise the two extracted helpers directly against a synthetic
DB so we can construct the exact >999-file + cross-batch-commit scenario that
the real repo (single author, modest tree) cannot reproduce. They pin BOTH
properties the batching must preserve:

  1. No crash above the SQLite variable cliff.
  2. Exact aggregation: distinct-commit counting must dedup a commit that
     touches files in two different batches (a naive per-batch sum would
     double-count it), and the top-churned-files merge must return the GLOBAL
     top-N, not a per-batch top-N.
"""

from __future__ import annotations

import sqlite3

from roam.commands.cmd_owner import _dir_author_churn, _dir_top_churned_files
from roam.db.connection import _BATCH_SIZE, ensure_schema

# Comfortably past the 999-variable cliff and spanning >2 batches.
N_FILES = _BATCH_SIZE * 2 + 50  # 1050 with default _BATCH_SIZE=500


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _seed(conn: sqlite3.Connection) -> list[int]:
    """Seed N_FILES files, one per-file commit by author 'A', plus one
    cross-batch commit by author 'B'. Returns the ordered file-id list."""
    file_ids: list[int] = []
    for i in range(N_FILES):
        cur = conn.execute("INSERT INTO files (path) VALUES (?)", (f"dir/f{i:05d}.py",))
        file_ids.append(cur.lastrowid)

    # Author A: one distinct commit per file, churn 10 each.
    for i, fid in enumerate(file_ids):
        cur = conn.execute(
            "INSERT INTO git_commits (hash, author, timestamp, message) VALUES (?,?,?,?)",
            (f"a{i:05d}", "A", 1_000 + i, "edit"),
        )
        cid = cur.lastrowid
        conn.execute(
            "INSERT INTO git_file_changes (commit_id, file_id, path, lines_added, lines_removed) VALUES (?,?,?,?,?)",
            (cid, fid, f"dir/f{i:05d}.py", 6, 4),
        )

    # Author B: ONE commit touching a file in batch 0 AND a file in batch 1
    # (indices 0 and 600 fall in different 500-wide batches). A correct
    # distinct-commit count for B is 1; a per-batch sum would report 2.
    cur = conn.execute(
        "INSERT INTO git_commits (hash, author, timestamp, message) VALUES (?,?,?,?)",
        ("bbbbb", "B", 9_999, "cross-batch"),
    )
    bcid = cur.lastrowid
    for idx in (0, 600):
        conn.execute(
            "INSERT INTO git_file_changes (commit_id, file_id, path, lines_added, lines_removed) VALUES (?,?,?,?,?)",
            (bcid, file_ids[idx], f"dir/f{idx:05d}.py", 3, 2),
        )
    conn.commit()
    return file_ids


def test_dir_author_churn_no_crash_and_exact_across_batches():
    conn = _conn()
    file_ids = _seed(conn)
    assert len(file_ids) > 999  # past the raw-IN cliff

    rows = _dir_author_churn(conn, file_ids)
    by_author = {r["author"]: r for r in rows}

    # Author A: one commit + 10 churn per file, all files touched.
    a = by_author["A"]
    assert a["commits"] == N_FILES
    assert a["files_touched"] == N_FILES
    assert a["churn"] == 10 * N_FILES

    # Author B: the single cross-batch commit must be counted ONCE, not twice.
    b = by_author["B"]
    assert b["commits"] == 1, "cross-batch commit double-counted — batching is not dedup-exact"
    assert b["files_touched"] == 2
    assert b["churn"] == 2 * 5

    # Sorted by churn DESC (matches the legacy SQL ORDER BY churn DESC).
    assert [r["churn"] for r in rows] == sorted((r["churn"] for r in rows), reverse=True)


def test_dir_top_churned_files_returns_global_topN_across_batches():
    conn = _conn()
    file_ids = _seed(conn)

    # Scatter total_churn so the true global top-10 are spread over multiple
    # batches; a per-batch top-N bug would miss high values outside batch 0.
    # `i * 7 % 10_000 + i` is strictly increasing in i (the +i breaks ties),
    # so every churn is unique and the expected top-10 is unambiguous.
    expected_pairs = []
    for i, fid in enumerate(file_ids):
        churn = i * 7 % 10_000 + i
        conn.execute(
            "INSERT INTO file_stats (file_id, commit_count, total_churn, distinct_authors) VALUES (?,?,?,?)",
            (fid, 1, churn, 1),
        )
        expected_pairs.append((f"dir/f{i:05d}.py", churn))
    conn.commit()

    top = _dir_top_churned_files(conn, file_ids, limit=10)
    got = [(r["path"], r["total_churn"]) for r in top]

    expected = sorted(expected_pairs, key=lambda p: p[1], reverse=True)[:10]
    assert got == expected, "top-churned merge did not return the GLOBAL top-10 across batches"
