"""Tests for the bounded / sampled variants of ``roam duplicates``.

Covers the post-bailout behaviour added in Fix Rank-19:
  * Large candidate counts (~18K) no longer abort the command — instead
    they trigger a deterministic stride sample with ``partial_success``.
  * ``--max-pairs N`` caps the number of clusters reported and marks the
    envelope as truncated.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands.cmd_duplicates import duplicates
from roam.db.connection import open_db
from tests.conftest import git_init, index_in_process


def _populate_duplicate_candidates(
    db_path: Path,
    total_symbols: int,
    *,
    n_functions: int | None = None,
) -> None:
    """Bulk-insert synthetic symbols into the index DB.

    The DB grows to ``total_symbols`` rows but only ``n_functions`` of
    them (default: 1/6th, mirroring real projects where most symbols are
    variables/classes/fields) carry the ``function`` kind and a metrics
    row.  Functions are spread across many shape buckets
    (param_count × line_count) so the bucketed pair scan stays
    tractable in test-relevant time.
    """
    if n_functions is None:
        n_functions = max(1, total_symbols // 6)
    n_other = max(0, total_symbols - n_functions)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("INSERT OR IGNORE INTO files (id, path) VALUES (9999, 'src/synth_dups.py')")
        # Variables — not candidates for duplicates, just inflate the
        # total symbol count so the fixture matches "18K symbol graph".
        other_rows = [
            (
                500000 + i,
                9999,
                f"var_{i:06d}",
                f"var_{i:06d}",
                "variable",
                "",
                None,
                None,
            )
            for i in range(n_other)
        ]
        # Functions — actual duplicate-detection candidates, spread
        # across 16 line-count bands × 12 param counts = 192 buckets.
        fn_rows = []
        metric_rows = []
        for i in range(n_functions):
            sid = 100000 + i
            band = i % 16
            param_count = (i // 16) % 12
            line_count = 8 + band * 2  # 8..38
            name = f"fn_p{param_count:02d}_l{line_count:02d}_n{i:05d}"
            line_start = 10 + i * (line_count + 5)
            line_end = line_start + line_count - 1
            fn_rows.append((sid, 9999, name, name, "function", "", line_start, line_end))
            metric_rows.append((sid, line_count, param_count, 1, 3, 1, 0, 0))
        if other_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO symbols "
                "(id, file_id, name, qualified_name, kind, signature, line_start, line_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                other_rows,
            )
        conn.executemany(
            "INSERT OR IGNORE INTO symbols "
            "(id, file_id, name, qualified_name, kind, signature, line_start, line_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            fn_rows,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO symbol_metrics "
            "(symbol_id, line_count, param_count, nesting_depth, "
            "cognitive_complexity, return_count, bool_op_count, callback_depth) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            metric_rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def large_dup_project(tmp_path, monkeypatch):
    """Indexed project inflated to ~18K symbols (≈3K function candidates).

    Mirrors a real production codebase where most symbols are
    variables/classes/fields and only a fraction are functions.  Crosses
    the OLD bailout threshold (2000 function candidates) by a wide
    margin without triggering O(n^2) blow-up in the bucketed pair scan.
    """
    proj = tmp_path / "dup_large"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "seed.py").write_text("def seed():\n    return 1\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    db_path = proj / ".roam" / "index.db"
    _populate_duplicate_candidates(db_path, 18000, n_functions=3000)
    with open_db(readonly=True, project_root=proj) as conn:
        total = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        fns = conn.execute("SELECT COUNT(*) FROM symbols WHERE kind IN ('function','method')").fetchone()[0]
    assert total >= 18000, f"expected >=18000 total symbols, got {total}"
    assert fns >= 3000, f"expected >=3000 function candidates, got {fns}"
    return proj


@pytest.fixture
def many_clusters_project(tmp_path, monkeypatch):
    """Project tuned so many small duplicate clusters form (for --max-pairs)."""
    proj = tmp_path / "dup_pairs"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "seed.py").write_text("def seed():\n    return 1\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    db_path = proj / ".roam" / "index.db"
    # 400 candidates spread across 80 families => ~80 clusters of ~5 each
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("INSERT OR IGNORE INTO files (id, path) VALUES (9998, 'src/synth_pairs.py')")
        rows = []
        metric_rows = []
        for i in range(400):
            sid = 200000 + i
            family = i % 80
            # Make names within a family share tokens so similarity is high.
            name = f"family_{family:02d}_op_{i:04d}"
            line_start = 10 + i * 20
            line_end = line_start + 9
            rows.append((sid, 9998, name, name, "function", "", line_start, line_end))
            metric_rows.append((sid, 10, 2 + (family % 2), 1, 2, 1, 0, 0))
        conn.executemany(
            "INSERT OR IGNORE INTO symbols "
            "(id, file_id, name, qualified_name, kind, signature, line_start, line_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO symbol_metrics "
            "(symbol_id, line_count, param_count, nesting_depth, "
            "cognitive_complexity, return_count, bool_op_count, callback_depth) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            metric_rows,
        )
        conn.commit()
    finally:
        conn.close()
    return proj


def _invoke_duplicates(args, cwd, json_mode=True):
    runner = CliRunner()
    import os

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(duplicates, args, obj={"json": json_mode, "budget": 0}, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


class TestDuplicatesBounded:
    """Bounded / sampled behaviour replaces the old hard bailout."""

    def test_duplicates_handles_18k_symbol_graph(self, large_dup_project):
        """At 18K total symbols (≈3K candidates — above the OLD 2000-row
        bailout) the command must not bail out; it should run and emit
        a clean envelope."""
        result = _invoke_duplicates(
            ["--threshold", "0.9", "--min-lines", "1", "--max-pairs", "20"],
            large_dup_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output[:400]}"
        data = json.loads(result.output)
        assert data["command"] == "duplicates"
        summary = data["summary"]
        assert "verdict" in summary
        # The old bailout printed "Too many candidates ... Use --scope".
        # That message must never appear post-fix.
        assert "Too many candidates" not in summary["verdict"]
        # candidate_count is the new always-on field — confirms we
        # crossed the OLD 2000-candidate bailout threshold.
        assert summary["candidate_count"] >= 3000

    def test_duplicates_max_pairs_truncation(self, many_clusters_project):
        """``--max-pairs N`` truncates the cluster list to N and flags it."""
        result = _invoke_duplicates(
            ["--threshold", "0.5", "--min-lines", "1", "--max-pairs", "5"],
            many_clusters_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output[:400]}"
        data = json.loads(result.output)
        summary = data["summary"]
        clusters = data.get("clusters", [])
        assert len(clusters) <= 5
        # When truncation actually kicks in, the envelope must say so.
        if summary.get("truncated"):
            assert summary["partial_success"] is True
            assert summary["max_pairs"] == 5
            assert summary["clusters_total"] >= len(clusters)
            assert "truncated" in summary["verdict"]

    def test_duplicates_sample_flag(self, large_dup_project):
        """``--sample N`` deterministically samples down to N candidates."""
        result = _invoke_duplicates(
            ["--sample", "500", "--threshold", "0.7", "--min-lines", "1"],
            large_dup_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output[:400]}"
        data = json.loads(result.output)
        summary = data["summary"]
        # Sampling must be reflected in the envelope.
        assert summary.get("sampled") is True
        assert summary.get("partial_success") is True
        assert summary.get("sample_size", 0) <= 500
        assert "sampled" in summary["verdict"]
