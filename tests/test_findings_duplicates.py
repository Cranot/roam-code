"""Tests for the W136 follow-up: duplicates detector emits to the central
findings registry.

The ``duplicates`` detector is the next per-detector migration onto the
A4 findings substrate (after ``clones`` W95, ``dead`` W99, ``complexity``
W102, ``smells`` W109, and ``n1`` W110). It continues to render its own
JSON / text envelopes (authoritative output surface) and ALSO, when
``--persist`` is set, emits one row per duplicate CLUSTER into
``findings``.

Why per-cluster and not per-pair: duplicates uses Union-Find to merge
pairwise above-threshold similarity edges into transitively-connected
clusters, then ranks and reports the clusters. The cluster is the
smallest atomic finding the detector can claim ("these N functions form
one duplicated pattern") — emitting per-pair would multiply rows for
clusters of size > 2 without adding signal.

The duplicates detector and the clones detector are INDEPENDENT and
share no implementation: clones re-parses files and hashes AST subtrees
(Type-2 textual clones); duplicates reads pre-computed AST-derived
metrics from ``symbol_metrics`` + ``math_signals`` + ``graph_metrics``
and clusters by a weighted similarity formula. The two detectors emit
under distinct ``source_detector`` values so the registry can tell
their findings apart — these tests assert that.

W165 extension — role_bucket enrichment + --exclude-tests/--exclude-fixtures:
the trailing test block covers the bucket-aware filtering and the
verdict-line bucket counts the W165 patch added to the duplicates
detector. Background and design rationale match ``test_findings_clones``
— the two patches are intentional symmetric fixes for the
W149-surfaced "tests drown signal" noise problem.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import textwrap

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_duplicates import (
    DUPLICATES_DETECTOR_VERSION,
    _duplicates_cluster_finding_id,
    _emit_duplicates_findings,
    _is_fixture_path,
    _role_bucket_for_files,
)
from roam.db.connection import open_db
from tests._findings_helpers import assert_detector_visible_in_findings_count
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _two_duplicates_project(tmp_path):
    """Tiny repo with two structurally similar Python functions.

    Both functions iterate a sequence, filter via ``is_valid()``,
    transform, append, and return — same control-flow shape, same
    parameter count, same line count, differing only in identifier
    choice. That keeps every AST-derived metric in ``symbol_metrics``
    and ``math_signals`` very close, which is exactly what the
    duplicates detector's weighted-similarity formula rewards.

    Mirrors the clones-fixture so an apples-to-apples comparison between
    the two detectors stays cheap.
    """
    return _make_project(
        tmp_path,
        {
            "a.py": """
            def process_orders(items):
                results = []
                for item in items:
                    if item.is_valid():
                        value = item.calculate()
                        results.append(value)
                return results
        """,
            "b.py": """
            def handle_invoices(entries):
                output = []
                for entry in entries:
                    if entry.is_valid():
                        amount = entry.calculate()
                        output.append(amount)
                return output
        """,
        },
    )


def _persist_duplicates(proj, threshold: float = 0.50):
    """Index the project and run ``duplicates --persist``.

    Returns the CliRunner result so tests can assert on exit code.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["duplicates", "--threshold", str(threshold), "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_duplicates_emits_to_findings_registry(tmp_path):
    """Running duplicates --persist on a fixture populates findings."""
    proj = _two_duplicates_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_duplicates(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'duplicates'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one duplicates-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "duplicates"
            assert r["source_version"] == DUPLICATES_DETECTOR_VERSION
            # subject_kind is "symbol" when the anchor's id resolves,
            # "file" only when it doesn't — both are valid.
            assert r["subject_kind"] in ("symbol", "file")
            # All duplicates findings are structural (deterministic
            # AST-metric comparison, no regex / heuristic).
            assert r["confidence"] == "structural"
            assert r["finding_id_str"].startswith("duplicates:cluster:")
    finally:
        os.chdir(old_cwd)


def test_duplicates_finding_id_str_is_deterministic(tmp_path):
    """Re-running duplicates produces the same finding_id_str (upsert)."""
    proj = _two_duplicates_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_duplicates(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'duplicates'"
                ).fetchall()
            }
            first_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'duplicates'").fetchone()[
                0
            ]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"

        # Second run — same fixture, same threshold, same hash inputs.
        runner = CliRunner()
        result = runner.invoke(cli, ["duplicates", "--threshold", "0.50", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'duplicates'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'duplicates'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_duplicates_cluster_finding_id_is_member_order_invariant():
    """_duplicates_cluster_finding_id returns the same id regardless of order."""
    members_forward = ["src.a:process_orders", "src.b:handle_invoices"]
    members_reverse = list(reversed(members_forward))
    fid_a = _duplicates_cluster_finding_id(members_forward)
    fid_b = _duplicates_cluster_finding_id(members_reverse)
    assert fid_a == fid_b
    assert fid_a.startswith("duplicates:cluster:")


def test_duplicates_finding_evidence_carries_members_and_similarity(tmp_path):
    """The finding's evidence JSON references the cluster members + similarity."""
    proj = _two_duplicates_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_duplicates(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings WHERE source_detector = 'duplicates' LIMIT 1"
            ).fetchone()
            assert row is not None
            evidence = json.loads(row["evidence_json"])
            assert "similarity" in evidence
            assert "size" in evidence
            assert "members" in evidence
            assert isinstance(evidence["members"], list)
            assert len(evidence["members"]) >= 2
            for m in evidence["members"]:
                assert "name" in m
                assert "file" in m
                assert "line_start" in m
            # The claim string should mention the cluster size + similarity.
            assert "Duplicate cluster" in row["claim"]
    finally:
        os.chdir(old_cwd)


def test_duplicates_finding_subject_link_when_resolved(tmp_path):
    """When subject_id is populated, it resolves to a real symbols row."""
    proj = _two_duplicates_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_duplicates(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT subject_id FROM findings WHERE source_detector = 'duplicates'   AND subject_id IS NOT NULL"
            ).fetchall()
            # subject_id population is best-effort (anchor PageRank pick);
            # it should land on at least one row given our fixture.
            assert len(rows) >= 1, (
                "expected at least one duplicates finding with a resolved subject_id (the cluster anchor)"
            )
            for r in rows:
                sym = conn.execute(
                    "SELECT id, name FROM symbols WHERE id = ?",
                    (r["subject_id"],),
                ).fetchone()
                assert sym is not None, f"orphan subject_id {r['subject_id']}"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Independence from the clones detector (W95)
# ---------------------------------------------------------------------------


def test_duplicates_and_clones_emit_under_distinct_source_detectors(tmp_path):
    """Both detectors can write to the registry — registry keeps them apart.

    Confirms the "duplicates and clones are independent commands" branch
    of the W136 investigation: running BOTH ``clones --persist`` and
    ``duplicates --persist`` against the same fixture produces TWO
    distinct sets of findings (different ``source_detector`` values,
    non-overlapping ``finding_id_str`` namespaces).
    """
    proj = _two_duplicates_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        assert runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"]).exit_code == 0
        assert runner.invoke(cli, ["duplicates", "--threshold", "0.50", "--persist"]).exit_code == 0

        with open_db(readonly=True) as conn:
            detectors = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT source_detector FROM findings WHERE source_detector IN ('clones', 'duplicates')"
                ).fetchall()
            }
            # Both detectors must have produced at least one row each.
            assert "clones" in detectors, "clones rows missing"
            assert "duplicates" in detectors, "duplicates rows missing"

            # finding_id_str namespaces must not collide: clones uses
            # ``clones:pair:...`` and duplicates uses
            # ``duplicates:cluster:...``.
            mixed = conn.execute(
                "SELECT finding_id_str, source_detector FROM findings WHERE source_detector IN ('clones', 'duplicates')"
            ).fetchall()
            for r in mixed:
                if r["source_detector"] == "clones":
                    assert r["finding_id_str"].startswith("clones:")
                else:
                    assert r["finding_id_str"].startswith("duplicates:")
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_duplicates_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector duplicates` returns rows after migration."""
    proj = _two_duplicates_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_duplicates(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "duplicates"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "duplicates" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "duplicates" for r in envelope["findings"])
    finally:
        os.chdir(old_cwd)


def test_duplicates_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for duplicates."""
    proj = _two_duplicates_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_duplicates(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "duplicates")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_duplicates_no_findings_table_no_crash(tmp_path):
    """``--persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init
    but before duplicates --persist runs. The detector's primary
    JSON / text output path must keep working regardless.
    """
    proj = _two_duplicates_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["duplicates", "--threshold", "0.50", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


def test_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, no findings rows are written.

    The registry mirror lives inside the ``--persist`` branch — running
    ``roam duplicates`` without the flag must remain side-effect-free.
    """
    proj = _two_duplicates_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["duplicates", "--threshold", "0.50"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'duplicates'").fetchone()[0]
            except sqlite3.OperationalError:
                # findings table may not be present on every test env's
                # schema flavour — that's still a "no findings emitted"
                # outcome from this command path.
                count = 0
        assert count == 0, "non-persist duplicates still wrote to findings"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Direct helper exercise (no full DB indexing needed)
# ---------------------------------------------------------------------------


def test_emit_duplicates_findings_skips_short_clusters():
    """A single-member 'cluster' must not produce a finding row.

    The cluster builder filters size<2 already, but the registry write
    should stay tolerant of degenerate dicts so a future refactor of
    the builder can't silently start polluting the registry.
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(":memory:")
    conn.row_factory = _sqlite3.Row
    conn.execute(
        """
        CREATE TABLE findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            finding_id_str TEXT UNIQUE NOT NULL,
            subject_kind TEXT NOT NULL,
            subject_id INTEGER,
            claim TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            confidence TEXT NOT NULL DEFAULT 'heuristic',
            source_detector TEXT NOT NULL,
            source_version TEXT,
            supersedes_id INTEGER,
            suppressions_json TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Single-member "cluster" — must be skipped.
    short_cluster = {
        "similarity": 0.95,
        "size": 1,
        "functions": [
            {
                "id": 1,
                "name": "only",
                "qualified_name": "src.a:only",
                "kind": "function",
                "file_path": "src/a.py",
                "line_start": 10,
                "line_count": 5,
                "pagerank": 0.1,
            }
        ],
        "pattern": "n/a",
        "suggestion": "n/a",
        "total_pagerank": 0.1,
    }
    written = _emit_duplicates_findings(conn, [short_cluster], DUPLICATES_DETECTOR_VERSION)
    assert written == 0
    rows = conn.execute("SELECT COUNT(*) FROM findings").fetchone()
    assert rows[0] == 0


# ---------------------------------------------------------------------------
# W165 — role_bucket enrichment + --exclude-tests / --exclude-fixtures
# ---------------------------------------------------------------------------
# Symmetric coverage to ``test_findings_clones`` for the duplicates
# detector. Same fixture shape (cross-role project), same bucket
# semantics, same opinionated decision (keep mixed through --exclude-tests).
# The duplicates detector emits per cluster (not per pair), so the
# bucket classifier walks the full cluster member set rather than just
# the two sides of a pair.


def _multi_role_dup_project(tmp_path, *, include_fixture: bool = False):
    """Repo with duplicate-pair functions across src/ and tests/.

    Duplicates is metric-based (reads ``symbol_metrics`` +
    ``math_signals``) rather than AST-hash based, so we need each
    function to be a real indexed Python symbol with comparable
    structural metrics. We use the same iterate-filter-transform-append
    shape across all four functions; identifier renaming changes the
    name-token Jaccard but the body-structure vector stays near-identical,
    which keeps the weighted similarity well above the 0.50 floor.
    """
    body = textwrap.dedent("""
        def {name}(items):
            results = []
            for item in items:
                if item.is_valid():
                    value = item.calculate()
                    results.append(value)
            return results
    """).strip()

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    src = proj / "src"
    src.mkdir()
    (src / "a.py").write_text(body.format(name="process_orders") + "\n", encoding="utf-8")
    (src / "b.py").write_text(body.format(name="handle_invoices") + "\n", encoding="utf-8")

    tests_dir = proj / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_a.py").write_text(body.format(name="process_orders_t") + "\n", encoding="utf-8")
    (tests_dir / "test_b.py").write_text(body.format(name="handle_invoices_t") + "\n", encoding="utf-8")

    if include_fixture:
        fix_dir = tests_dir / "fixtures"
        fix_dir.mkdir()
        (fix_dir / "sample.py").write_text(body.format(name="sample_fixture") + "\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(proj),
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    return proj


def _dup_bucket_counts(rows: list[sqlite3.Row]) -> dict[str, int]:
    counts = {"production": 0, "test_intentional": 0, "mixed": 0}
    for r in rows:
        ev = json.loads(r["evidence_json"])
        bucket = ev.get("role_bucket", "production")
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def test_duplicates_emits_role_bucket_in_evidence(tmp_path):
    """A pure src/-cluster duplicate has ``role_bucket: "production"``."""
    proj = _two_duplicates_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_duplicates(proj)
        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT evidence_json FROM findings WHERE source_detector = 'duplicates'").fetchall()
        assert len(rows) >= 1, "expected at least one duplicates finding row"
        for r in rows:
            ev = json.loads(r["evidence_json"])
            assert "role_bucket" in ev, (
                "role_bucket missing from duplicates evidence — W165 patch didn't reach the emit path"
            )
            assert ev["role_bucket"] == "production", (
                f"src/-only cluster should be production, got {ev['role_bucket']!r}"
            )
    finally:
        os.chdir(old_cwd)


def test_duplicates_test_pair_marked_as_test_intentional(tmp_path):
    """Duplicate cluster confined to tests/ buckets as test_intentional."""
    proj = _multi_role_dup_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["duplicates", "--threshold", "0.50", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT evidence_json FROM findings WHERE source_detector = 'duplicates'").fetchall()
        counts = _dup_bucket_counts(rows)
        # The mixed-cluster Union-Find behaviour may pull every duplicate
        # into ONE big mixed cluster (4 functions across src/+tests/),
        # which would mean test_intentional=0. Accept either: at least
        # one mixed (everything merged) OR at least one test_intentional
        # (test-only sub-cluster preserved). Both are valid evidence of
        # bucketing working — what we MUST never see is test_intentional
        # rows misclassified as production.
        for r in rows:
            ev = json.loads(r["evidence_json"])
            files = [m["file"] for m in ev["members"]]
            all_tests = all("tests/" in (f or "").replace("\\", "/") for f in files)
            if all_tests:
                assert ev["role_bucket"] == "test_intentional", (
                    f"all-test cluster misclassified: {ev['role_bucket']!r} for files {files}"
                )
        # And the broader assertion: at least one row in a "test-touching"
        # bucket (test_intentional ∪ mixed) so the fixture proved out.
        assert (counts["test_intentional"] + counts["mixed"]) >= 1, (
            f"no test-touching cluster surfaced; counts={counts}"
        )
    finally:
        os.chdir(old_cwd)


def test_duplicates_exclude_tests_flag_drops_test_findings(tmp_path):
    """``--exclude-tests`` drops test_intentional clusters; mixed survives."""
    proj = _multi_role_dup_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        # Baseline
        baseline = runner.invoke(cli, ["duplicates", "--threshold", "0.50", "--persist"])
        assert baseline.exit_code == 0, baseline.output
        with open_db(readonly=True) as conn:
            base_rows = conn.execute(
                "SELECT evidence_json FROM findings WHERE source_detector = 'duplicates'"
            ).fetchall()
        baseline_total = len(base_rows)

        # Filtered
        filtered = runner.invoke(
            cli,
            [
                "duplicates",
                "--threshold",
                "0.50",
                "--persist",
                "--exclude-tests",
            ],
        )
        assert filtered.exit_code == 0, filtered.output
        with open_db(readonly=True) as conn:
            filt_rows = conn.execute(
                "SELECT evidence_json FROM findings WHERE source_detector = 'duplicates'"
            ).fetchall()
        filt_counts = _dup_bucket_counts(filt_rows)

        # The flag must zero out test_intentional rows.
        assert filt_counts["test_intentional"] == 0, f"--exclude-tests left test_intentional behind: {filt_counts}"
        # The filtered run should not be larger than baseline (it can be
        # equal when every cluster was mixed or production already).
        assert len(filt_rows) <= baseline_total, "filtered run produced MORE rows than baseline — impossible"
    finally:
        os.chdir(old_cwd)


def test_duplicates_mixed_bucket_flagged_for_test_leakage(tmp_path):
    """A src/ + tests/ cluster gets bucket "mixed" (test-leakage signal).

    Duplicates uses Union-Find, so a single project with structurally
    identical functions in both src/ and tests/ tends to merge into one
    large mixed cluster. We assert at least one mixed cluster survives
    AND that --exclude-tests preserves it.
    """
    proj = _multi_role_dup_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["duplicates", "--threshold", "0.50", "--persist"])
        assert result.exit_code == 0, result.output
        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT evidence_json FROM findings WHERE source_detector = 'duplicates'").fetchall()
        counts = _dup_bucket_counts(rows)
        assert counts["mixed"] >= 1, f"expected at least one mixed-bucket duplicate cluster; counts={counts}"

        # Mixed survives --exclude-tests (opinionated W165 decision).
        filtered = runner.invoke(
            cli,
            [
                "duplicates",
                "--threshold",
                "0.50",
                "--persist",
                "--exclude-tests",
            ],
        )
        assert filtered.exit_code == 0, filtered.output
        with open_db(readonly=True) as conn:
            filt_rows = conn.execute(
                "SELECT evidence_json FROM findings WHERE source_detector = 'duplicates'"
            ).fetchall()
        filt_counts = _dup_bucket_counts(filt_rows)
        assert filt_counts["mixed"] >= 1, f"--exclude-tests dropped the mixed bucket; counts={filt_counts}"
    finally:
        os.chdir(old_cwd)


def test_duplicates_verdict_surfaces_per_bucket_counts(tmp_path):
    """Verdict line carries the per-bucket count breakdown.

    Pattern-3 vocabulary improvement (W165): expose the buckets at the
    surface so agents that consume only the verdict (LAW 6) still see
    the production/test split.
    """
    proj = _multi_role_dup_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(
            cli,
            [
                "--json",
                "duplicates",
                "--threshold",
                "0.50",
                "--persist",
            ],
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        summary = envelope["summary"]
        verdict = summary["verdict"]
        assert "production" in verdict, verdict
        assert "test_intentional" in verdict, verdict
        assert "mixed" in verdict, verdict
        assert "role_buckets" in summary, summary
        assert set(summary["role_buckets"].keys()) == {
            "production",
            "test_intentional",
            "mixed",
        }
    finally:
        os.chdir(old_cwd)


def test_duplicates_exclude_fixtures_drops_fixture_clusters(tmp_path):
    """``--exclude-fixtures`` drops clusters touching fixtures/ paths."""
    proj = _multi_role_dup_project(tmp_path, include_fixture=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        baseline = runner.invoke(cli, ["duplicates", "--threshold", "0.50", "--persist"])
        assert baseline.exit_code == 0, baseline.output
        with open_db(readonly=True) as conn:
            base_rows = conn.execute(
                "SELECT evidence_json FROM findings WHERE source_detector = 'duplicates'"
            ).fetchall()

        # Baseline should include at least one fixture-touching cluster.
        base_fixture_rows = []
        for r in base_rows:
            ev = json.loads(r["evidence_json"])
            files = [m["file"] for m in ev["members"]]
            if any("/fixtures/" in (f or "").replace("\\", "/") for f in files):
                base_fixture_rows.append(r)
        assert len(base_fixture_rows) >= 1, "fixture must yield at least one fixture-touching cluster"

        # Clear findings between runs so the filter behavior is tested in
        # isolation from stale-row carryover (findings upsert by id, so
        # baseline-emitted fixture rows would otherwise linger).
        with open_db(readonly=False) as conn:
            conn.execute("DELETE FROM findings WHERE source_detector = 'duplicates'")
            conn.commit()

        filtered = runner.invoke(
            cli,
            [
                "duplicates",
                "--threshold",
                "0.50",
                "--persist",
                "--exclude-fixtures",
            ],
        )
        assert filtered.exit_code == 0, filtered.output
        with open_db(readonly=True) as conn:
            filt_rows = conn.execute(
                "SELECT evidence_json FROM findings WHERE source_detector = 'duplicates'"
            ).fetchall()
        for r in filt_rows:
            ev = json.loads(r["evidence_json"])
            files = [m["file"] for m in ev["members"]]
            for f in files:
                assert "/fixtures/" not in (f or "").replace("\\", "/"), (
                    f"--exclude-fixtures left a fixture cluster behind: {f}"
                )
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Pure-helper unit tests (no DB / no CLI)
# ---------------------------------------------------------------------------


def test_is_fixture_path_recognises_common_layouts():
    """Helper handles fixtures/, test_fixtures/, testdata/, test_data/."""
    assert _is_fixture_path("tests/fixtures/sample.py")
    assert _is_fixture_path("tests/fixture/sample.py")  # singular
    assert _is_fixture_path("test_fixtures/data.py")
    assert _is_fixture_path("testdata/payload.json")
    assert _is_fixture_path("foo/test_data/bar.py")
    assert not _is_fixture_path("src/roam/cli.py")
    assert not _is_fixture_path("")
    assert not _is_fixture_path("tests/test_basic.py")


def test_role_bucket_for_files_three_buckets():
    """All-test ⇒ test_intentional; all-source ⇒ production; mix ⇒ mixed."""
    assert _role_bucket_for_files(["src/a.py", "src/b.py"]) == "production"
    assert _role_bucket_for_files(["tests/test_a.py", "tests/test_b.py"]) == "test_intentional"
    assert _role_bucket_for_files(["src/a.py", "tests/test_a.py"]) == "mixed"
    # Fixtures count as test-side too.
    assert _role_bucket_for_files(["tests/fixtures/x.py", "tests/fixtures/y.py"]) == "test_intentional"
    # Empty list defaults to production (caller filters empty clusters).
    assert _role_bucket_for_files([]) == "production"
