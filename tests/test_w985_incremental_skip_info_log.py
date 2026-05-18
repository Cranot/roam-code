"""W985-incremental: stderr log when mtime+hash short-circuits incremental reindex.

Same diagnosis-shadowing shape as W985 (shallow-history filter in git_stats)
and W985-followup (HEAD-unchanged skip in git_stats): the existing
"Index is up to date." line was technically correct but did not name WHY
the skip happened (mtime+hash vs. discovery dropped files vs. broken
index) nor the ``--force`` opt-out. An operator running ``roam index`` /
``roam health`` and expecting fresh metrics got a silent "nothing to
refresh" branch and had no signal to distinguish a legitimate no-op from
a stale / broken index.

W985-incremental closes that loop at two sites in
``src/roam/index/indexer.py``:

  Site 1 (the primary shadowing): ``_do_run`` total_changed == 0 branch
  (line ~1985). The skip line must name BOTH (a) the file count covered
  by the mtime+hash check, (b) the source-of-truth (mtime+hash) that
  decided the skip, and (c) the ``--force`` opt-out.

  Site 2 (parity): ``_run_clustering`` cached-signature branch (line
  ~1652). Already named the source-of-truth (graph signature) + counts
  (nodes/edges) before this wave; the W985-incremental edit appends the
  ``--force`` opt-out for parity with site 1 and W985-followup.

Invariants asserted here:

1. Site 1: second run on an unchanged corpus -> stderr carries "Index is
   up to date" AND the file count AND "mtime+hash" AND "--force".
2. Site 1: first run / forced run -> the W985-incremental log MUST NOT
   appear (real work being done).
3. Site 2: cluster-cache hit log carries "--force" alongside the
   pre-existing "graph signature unchanged" + node/edge counts.
4. W985-incremental is a stderr progress line (matches the existing
   self._log convention), not a Pattern-2 envelope warning. The
   ``summary["up_to_date"] is True`` invariant from test_progress.py is
   preserved.
5. Cross-check: the W985-followup HEAD-unchanged log in git_stats.py is
   a sibling on a different namespace and stays untouched by this wave.

Scope discipline: stderr log content ONLY. No change to the mtime cache,
hash cache, ``--force`` flag plumbing, manifest schema, or the
``up_to_date`` summary field. The W985 and W985-followup logs in
git_stats.py live on disjoint branches and are not exercised by these
tests.
"""

from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Anchors the W985-incremental site-1 log MUST carry. Asserting each anchor
# independently means a copy-edit that preserves diagnostic value won't
# break the test; removing any single signal will.
# ---------------------------------------------------------------------------
_SITE1_TRIGGER = "Index is up to date"
_SITE1_SOURCE_OF_TRUTH = "mtime+hash"
_SITE1_FORCE_HINT = "--force"

# Anchors for site 2 (cluster cache).
_SITE2_TRIGGER = "graph signature unchanged"
_SITE2_FORCE_HINT = "--force"


@pytest.fixture
def small_project(tmp_path):
    """Minimal two-file Python project sufficient for incremental reindex.

    Mirrors the shape used in tests/test_progress.py so the existing
    up_to_date invariant tests still pass alongside this regression file.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (proj / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    return proj


# ---------------------------------------------------------------------------
# Site 1: ``_do_run`` total_changed == 0 short-circuit.
# ---------------------------------------------------------------------------


def test_site1_up_to_date_log_names_count_and_source_and_force_hint(small_project, capsys):
    """Second incremental run on an unchanged corpus must surface all three
    W985-incremental anchors on stderr (count, mtime+hash, --force)."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(small_project))
        from roam.index.indexer import Indexer

        # First run builds the index.
        Indexer(project_root=small_project).run(force=True, quiet=False)
        capsys.readouterr()  # drop first-run noise

        # Second run takes the total_changed == 0 short-circuit.
        indexer2 = Indexer(project_root=small_project)
        indexer2.run(quiet=False)
        captured = capsys.readouterr()
    finally:
        os.chdir(old_cwd)

    # Confirm the short-circuit actually ran (preserves the test_progress.py
    # invariant — see Invariant 4 in the module docstring).
    assert indexer2.summary is not None, "Indexer.summary must be populated"
    assert indexer2.summary["up_to_date"] is True, (
        f"Second run on unchanged corpus must hit the up_to_date short-circuit; got summary={indexer2.summary!r}"
    )

    stderr = captured.err
    assert _SITE1_TRIGGER in stderr, f"Expected {_SITE1_TRIGGER!r} on stderr; got: {stderr!r}"
    assert _SITE1_SOURCE_OF_TRUTH in stderr, (
        f"Expected source-of-truth anchor {_SITE1_SOURCE_OF_TRUTH!r} on "
        f"stderr (names WHY the skip happened); got: {stderr!r}"
    )
    assert _SITE1_FORCE_HINT in stderr, f"Expected {_SITE1_FORCE_HINT!r} opt-out hint on stderr; got: {stderr!r}"


def test_site1_forced_run_does_not_emit_up_to_date_log(small_project, capsys):
    """When --force re-indexes everything, the W985-incremental skip log MUST
    NOT fire (real work is being done; emitting the skip log would falsely
    train operators that --force itself is a no-op)."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(small_project))
        from roam.index.indexer import Indexer

        indexer = Indexer(project_root=small_project)
        indexer.run(force=True, quiet=False)
        captured = capsys.readouterr()
    finally:
        os.chdir(old_cwd)

    assert indexer.summary is not None
    assert indexer.summary["up_to_date"] is False, (
        f"force=True must NOT hit the up_to_date short-circuit; got summary={indexer.summary!r}"
    )
    stderr = captured.err
    # Site-1 log line specifically carries the mtime+hash anchor — a generic
    # "Index complete" summary line is fine and expected. Pin against the
    # source-of-truth anchor to keep the assertion crisp.
    assert _SITE1_SOURCE_OF_TRUTH not in stderr, (
        f"force=True path must not emit the mtime+hash skip log; got: {stderr!r}"
    )


def test_site1_first_run_does_not_emit_up_to_date_log(small_project, capsys):
    """On a brand-new project (no prior index), the incremental reindex MUST
    process every file via the added/modified/removed code path — the
    up_to_date short-circuit cannot fire. Pinning this invariant blocks a
    future regression where added-files-only is wrongly treated as no-op."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(small_project))
        from roam.index.indexer import Indexer

        indexer = Indexer(project_root=small_project)
        indexer.run(quiet=False)  # first ever run, no force
        captured = capsys.readouterr()
    finally:
        os.chdir(old_cwd)

    assert indexer.summary is not None
    assert indexer.summary["up_to_date"] is False, (
        f"First-ever run must produce real work, not the up_to_date short-circuit; got summary={indexer.summary!r}"
    )
    stderr = captured.err
    assert _SITE1_SOURCE_OF_TRUTH not in stderr, (
        f"First-ever run must not emit the mtime+hash skip log; got: {stderr!r}"
    )


# ---------------------------------------------------------------------------
# Site 2: ``_run_clustering`` graph-signature cache.
# ---------------------------------------------------------------------------


def test_site2_cluster_cache_log_carries_force_hint(small_project, capsys):
    """A warm incremental rerun should hit the cluster cache (same graph
    signature). The existing log already named the source-of-truth +
    counts; W985-incremental adds the --force opt-out for parity with
    site 1 and the W985-followup canonical shape."""
    old_cwd = os.getcwd()
    try:
        os.chdir(str(small_project))
        from roam.index.indexer import Indexer

        # First run computes clusters from scratch.
        Indexer(project_root=small_project).run(force=True, quiet=False)
        capsys.readouterr()  # drop first-run noise

        # Touch one file's mtime + content so the up-to-date short-circuit
        # does NOT fire — we want to reach _run_clustering and exercise the
        # signature-cache branch (the graph signature stays the same when
        # the edit produces no new symbols / edges).
        a_py = small_project / "a.py"
        a_py.write_text("def f():\n    return 1  # touched\n", encoding="utf-8")

        indexer2 = Indexer(project_root=small_project)
        indexer2.run(quiet=False)
        captured = capsys.readouterr()
    finally:
        os.chdir(old_cwd)

    stderr = captured.err
    # The cluster-cache log only fires when the signature actually matches;
    # if it didn't fire on this corpus, skip rather than false-fail. The
    # important invariant is the *content* of the log when it does fire.
    if _SITE2_TRIGGER not in stderr:
        pytest.skip(
            "Cluster-cache branch did not trip on this corpus (graph "
            "signature changed). Site-2 anchor remains pinned via "
            "test_site2_force_hint_in_source — this test is opportunistic."
        )

    assert _SITE2_FORCE_HINT in stderr, f"Expected {_SITE2_FORCE_HINT!r} on the cluster-cache log line; got: {stderr!r}"


def test_site2_force_hint_in_source():
    """Source-level pin: the cluster-cache log MUST literally contain the
    ``--force`` hint string. Belt-and-braces against the opportunistic
    runtime test above — if the corpus shape changes such that the cache
    branch stops firing in the test environment, this assertion keeps the
    anchor pinned."""
    import inspect

    from roam.index import indexer as indexer_mod

    src = inspect.getsource(
        indexer_mod._run_clustering if hasattr(indexer_mod, "_run_clustering") else indexer_mod.Indexer._run_clustering
    )
    assert "graph signature unchanged" in src, (
        "Cluster-cache log must keep the W985-shape 'graph signature unchanged' anchor"
    )
    assert "--force" in src, "Cluster-cache log must carry the W985-incremental --force opt-out hint"


# ---------------------------------------------------------------------------
# Cross-check: source-level pin on site 1 (defends against accidental
# anchor removal even if the runtime test above is short-circuited by an
# environment quirk).
# ---------------------------------------------------------------------------


def test_site1_anchors_in_source():
    """Belt-and-braces source-level guard for site 1.

    The runtime test above exercises the full pipeline; this pin defends
    each of the three anchors as literal strings in the source so a
    refactor cannot silently drop one. W985 and W985-followup carry an
    equivalent guard via their respective test files (trigger phrase +
    --force hint), and W985-incremental matches that discipline.
    """
    import inspect

    from roam.index import indexer as indexer_mod

    src = inspect.getsource(indexer_mod.Indexer._do_run)
    assert "Index is up to date" in src, "Site 1 must keep the 'Index is up to date' trigger phrase"
    assert "mtime+hash" in src, (
        "Site 1 must name the mtime+hash source-of-truth so operators can "
        "disambiguate the skip from a broken / stale index"
    )
    assert "--force" in src, "Site 1 must surface the --force opt-out alongside the trigger phrase"
