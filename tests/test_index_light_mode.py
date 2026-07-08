"""`Indexer.run(light=True)` — fast post-edit reindex for verify.

Light mode refreshes symbols + edges (parse + resolve) but skips the O(repo)
METRIC phases (graph_metrics, git_analysis, effects/taint, health/load, search).
The structural data verify reads stays correct; the skipped phases are what made
a post-edit reindex cost ~150s on a large tree (effects/taint alone was ~113s in
the measured roam-code phase_timings). These tests pin: (a) light keeps the graph
consistent (symbols + resolved edges), (b) light actually skips the heavy phases,
(c) the default (light=False) path is unchanged.
"""

from __future__ import annotations

import os

from roam.db.connection import open_db
from roam.index.indexer import Indexer

_BASE = "from __future__ import annotations\n\n\ndef helper_one():\n    return 1\n\n\ndef helper_two():\n    return 2\n"


def _full_index(proj):
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        Indexer(project_root=proj).run(force=True, quiet=True, progress_bar=False)
    finally:
        os.chdir(old)


def test_light_run_refreshes_symbols_and_edges_but_skips_metrics(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "lib.py").write_text(_BASE, encoding="utf-8")
    _full_index(proj)

    # Edit: add a function that CALLS an existing one. A resolved edge
    # new_caller -> helper_one proves phase 2 (resolve) ran in light mode, i.e.
    # the graph stays consistent, not just the symbol table.
    (proj / "lib.py").write_text(_BASE + "\n\ndef new_caller():\n    return helper_one()\n", encoding="utf-8")

    old = os.getcwd()
    try:
        os.chdir(str(proj))
        idx = Indexer(project_root=proj)
        idx.run(quiet=True, progress_bar=False, light=True)
    finally:
        os.chdir(old)

    with open_db(project_root=proj, readonly=True) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM symbols")}
        assert "new_caller" in names, "light run must refresh the edited file's symbols"
        edge = conn.execute(
            "SELECT 1 FROM edges e "
            "JOIN symbols s ON e.source_id = s.id "
            "JOIN symbols t ON e.target_id = t.id "
            "WHERE s.name = 'new_caller' AND t.name = 'helper_one'"
        ).fetchone()
        assert edge is not None, "light run must resolve edges (phase 2) so impact/uses/cycles stay correct"

    timings = idx._phase_timer.timings or {}
    assert "parse_extract" in timings and "resolve" in timings, (
        f"light run must still parse + resolve (the cheap structural phases): {timings}"
    )
    assert "effects_taint" not in timings, (
        f"light run must skip effects/taint (the ~113s phase verify never reads): {timings}"
    )
    assert "graph_metrics" not in timings, f"light run must skip graph metrics: {timings}"


def test_light_then_full_index_recomputes_skipped_metrics(tmp_path):
    """Self-heal: after a light run the poisoned hash/mtime force the NEXT full
    incremental `roam index` to re-detect the file and recompute the metrics
    light skipped — so staleness heals on any normal index, not only --force."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "lib.py").write_text(_BASE, encoding="utf-8")
    _full_index(proj)
    (proj / "lib.py").write_text(_BASE + "\n\ndef added_fn():\n    return 3\n", encoding="utf-8")
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        Indexer(project_root=proj).run(quiet=True, progress_bar=False, light=True)
        with open_db(project_root=proj, readonly=True) as conn:
            row = conn.execute("SELECT hash, mtime FROM files WHERE path = 'lib.py'").fetchone()
            assert row[0] == "roam-light-pending" and row[1] == 0, (
                f"light run must poison hash+mtime so the next full index re-detects: {tuple(row)}"
            )
        idx2 = Indexer(project_root=proj)
        idx2.run(quiet=True, progress_bar=False)  # full incremental (light=False)
    finally:
        os.chdir(old)

    assert idx2.summary is not None and idx2.summary["up_to_date"] is False, (
        "poisoned file must force the next full index to reprocess, not short-circuit up_to_date"
    )
    timings = idx2._phase_timer.timings or {}
    assert "effects_taint" in timings, f"the follow-up full index must recompute the metrics light skipped: {timings}"


def test_default_run_still_computes_all_phases(tmp_path):
    """Guard: light defaults to False, so the normal full index is unchanged —
    the heavy metric phases still run."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "lib.py").write_text(_BASE, encoding="utf-8")
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        idx = Indexer(project_root=proj)
        idx.run(force=True, quiet=True, progress_bar=False)
    finally:
        os.chdir(old)
    timings = idx._phase_timer.timings or {}
    assert "effects_taint" in timings and "graph_metrics" in timings, (
        f"default run must compute all metric phases (light=False unchanged): {timings}"
    )
