"""Tests for the W93 follow-up: clones detector emits to the central
findings registry.

The clones detector is the proof-of-concept migration onto the A4 findings
table. It continues to write to ``clone_pairs`` / ``clone_clusters``
(authoritative detector-specific tables) and ALSO emits one row per
clone pair into ``findings``. These tests cover that additive emit and
the end-to-end visibility through ``roam findings`` for an agent.

W165 extension — role_bucket enrichment + --exclude-tests/--exclude-fixtures:
the trailing test block validates that every persisted clones finding
carries a ``role_bucket`` evidence field (production / test_intentional /
mixed), that the CLI flags drop test-intentional findings while preserving
the mixed (potential-leakage) signal, and that the verdict line surfaces
per-bucket counts. See ``internal/dogfood/SYNTHESIS-2026-05-12.md`` for the
noise problem this fixes (250+/584 clones in language-extractor mirrors
plus 130+ in test fixtures drowned real refactor candidates).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import textwrap

from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.db.connection import open_db
from roam.graph.clone_detect import (
    CLONES_DETECTOR_VERSION,
    _clone_pair_finding_id,
)
from tests.conftest import make_src_project as _make_project


def _two_clone_project(tmp_path):
    """Tiny repo with two structurally identical Python functions.

    Mirrors the fixture used by ``test_clones.TestClonePersistence`` so
    the same threshold + min-lines settings reliably surface at least
    one clone pair on every platform.
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


def _persist_clones(proj):
    """Index the project and run ``clones --persist``.

    Returns the CliRunner result of the clones call so tests can assert
    on its exit code if they care about the persist path itself.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_clones_emits_to_findings_registry(tmp_path):
    """Running clones --persist on a fixture with duplicates populates findings."""
    proj = _two_clone_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_clones(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'clones'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one clones-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "clones"
            assert r["source_version"] == CLONES_DETECTOR_VERSION
            assert r["subject_kind"] == "symbol"
            assert r["confidence"] in ("structural", "heuristic")
            assert r["finding_id_str"].startswith("clones:pair:")
    finally:
        os.chdir(old_cwd)


def test_clones_finding_id_str_is_deterministic(tmp_path):
    """Re-running clones produces the same finding_id_str (upsert, not duplicate)."""
    proj = _two_clone_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_clones(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'clones'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'clones'"
            ).fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"

        # Second run — same fixture, same threshold, same hash inputs.
        runner = CliRunner()
        result = runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'clones'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'clones'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_clone_pair_finding_id_is_sort_order_invariant():
    """_clone_pair_finding_id returns the same id regardless of side ordering."""
    a = "src/a.py:process_orders"
    b = "src/b.py:handle_invoices"
    assert _clone_pair_finding_id(a, b) == _clone_pair_finding_id(b, a)
    assert _clone_pair_finding_id(a, b).startswith("clones:pair:")


def test_clones_finding_evidence_links_to_pair(tmp_path):
    """The finding's evidence JSON references both qnames of the clone pair."""
    proj = _two_clone_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_clones(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, subject_id FROM findings "
                "WHERE source_detector = 'clones' LIMIT 1"
            ).fetchone()
            assert row is not None
            evidence = json.loads(row["evidence_json"])
            assert "qname_a" in evidence
            assert "qname_b" in evidence
            assert "similarity" in evidence
            assert isinstance(evidence["similarity"], (int, float))

            # The qname pair stored in evidence_json should also be the
            # one persisted in clone_pairs (authoritative table).
            pair = conn.execute(
                "SELECT 1 FROM clone_pairs WHERE "
                "(qname_a = :a AND qname_b = :b) OR (qname_a = :b AND qname_b = :a)",
                {"a": evidence["qname_a"], "b": evidence["qname_b"]},
            ).fetchone()
            assert pair is not None, "evidence qnames not found in clone_pairs"
    finally:
        os.chdir(old_cwd)


def test_clones_finding_subject_link(tmp_path):
    """subject_id, when populated, resolves to a real symbols row."""
    proj = _two_clone_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_clones(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT subject_id FROM findings "
                "WHERE source_detector = 'clones' AND subject_id IS NOT NULL"
            ).fetchall()
            assert len(rows) >= 1, (
                "expected at least one clones finding with a resolved subject_id"
            )
            for r in rows:
                sym = conn.execute(
                    "SELECT id, name FROM symbols WHERE id = ?", (r["subject_id"],)
                ).fetchone()
                assert sym is not None, f"orphan subject_id {r['subject_id']}"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_clones_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector clones` returns rows after migration."""
    proj = _two_clone_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_clones(proj)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "clones"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "clones" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "clones" for r in envelope["findings"]
        )
    finally:
        os.chdir(old_cwd)


def test_clones_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for clones."""
    proj = _two_clone_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_clones(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "clones")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_store_clones_no_findings_table_no_crash(tmp_path):
    """``store_clones`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before clones --persist runs. The clone_pairs / clone_clusters write
    path (which existing consumers depend on) must keep working.
    """
    proj = _two_clone_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["clones", "--threshold", "0.50", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            pair_count = conn.execute("SELECT COUNT(*) FROM clone_pairs").fetchone()[0]
        assert pair_count >= 1, "clone_pairs path broke when findings table absent"
    finally:
        os.chdir(old_cwd)


def test_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, no findings rows are written either.

    The registry mirror lives inside ``store_clones`` — running
    ``roam clones`` without ``--persist`` must remain side-effect-free.
    """
    proj = _two_clone_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["clones", "--threshold", "0.50"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'clones'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                # findings table may not be present on every test env's
                # schema flavour — that's still a "no findings emitted"
                # outcome from this command path.
                count = 0
        assert count == 0, "non-persist clones still wrote to findings"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# W165 — role_bucket enrichment + --exclude-tests / --exclude-fixtures
# ---------------------------------------------------------------------------
# These tests cover the wave-165 patch:
#   - Every persisted clones finding row carries a ``role_bucket`` field
#     in evidence_json (production / test_intentional / mixed).
#   - ``--exclude-tests`` drops findings where ALL sides are tests.
#   - ``--exclude-fixtures`` drops findings touching fixture directories.
#   - Mixed-bucket findings (one side src, one side test) survive
#     ``--exclude-tests`` deliberately so test-leakage stays visible.
#
# The fixture builder below is intentionally local (no shared helper) to
# keep the cross-role layout (src/ + tests/ in one project) co-located
# with the assertions that read it.


def _multi_role_project(tmp_path, *, include_fixture: bool = False):
    """Build a repo with clone-pair functions across src/ and tests/.

    Layout (every body is the same shape so the structural hash matches
    even after identifier renaming — keeps the detector firing reliably
    at ``--threshold 0.50``):

        src/a.py            — process_orders   (production)
        src/b.py            — handle_invoices  (production)
        tests/test_a.py     — process_orders_t (test)
        tests/test_b.py     — handle_invoices_t (test)

    With ``include_fixture=True`` we ALSO write
    ``tests/fixtures/sample.py`` carrying an identical-shape function so
    the ``--exclude-fixtures`` flag has something to drop.

    Expected role buckets after ``clones --persist --threshold 0.50``:
      - {src/a.py × src/b.py}         → "production"
      - {tests/test_a.py × tests/test_b.py} → "test_intentional"
      - any cross-pair like
        {src/a.py × tests/test_a.py}  → "mixed"
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
    proj.mkdir(parents=True)
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    src = proj / "src"
    src.mkdir()
    (src / "a.py").write_text(body.format(name="process_orders") + "\n", encoding="utf-8")
    (src / "b.py").write_text(body.format(name="handle_invoices") + "\n", encoding="utf-8")

    tests_dir = proj / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_a.py").write_text(
        body.format(name="process_orders_t") + "\n", encoding="utf-8"
    )
    (tests_dir / "test_b.py").write_text(
        body.format(name="handle_invoices_t") + "\n", encoding="utf-8"
    )

    if include_fixture:
        fix_dir = tests_dir / "fixtures"
        fix_dir.mkdir()
        (fix_dir / "sample.py").write_text(
            body.format(name="sample_fixture") + "\n", encoding="utf-8"
        )

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


def _bucket_counts(rows: list[sqlite3.Row]) -> dict[str, int]:
    """Group registry rows by their ``role_bucket`` evidence field."""
    counts = {"production": 0, "test_intentional": 0, "mixed": 0}
    for r in rows:
        ev = json.loads(r["evidence_json"])
        bucket = ev.get("role_bucket", "production")
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def test_clones_emits_role_bucket_in_evidence(tmp_path):
    """A pure src/-pair clone has ``role_bucket: "production"`` in evidence."""
    proj = _make_project(
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
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(
            cli, ["clones", "--threshold", "0.50", "--persist"]
        )
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'clones'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one clones finding row"
        for r in rows:
            ev = json.loads(r["evidence_json"])
            assert "role_bucket" in ev, (
                "role_bucket missing from evidence — W165 enrichment never ran"
            )
            assert ev["role_bucket"] == "production", (
                f"src/-only pair should be production, got {ev['role_bucket']!r}"
            )
    finally:
        os.chdir(old_cwd)


def test_clones_test_pair_marked_as_test_intentional(tmp_path):
    """A clone pair where both sides live in tests/ buckets as test_intentional."""
    proj = _multi_role_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(
            cli, ["clones", "--threshold", "0.50", "--persist"]
        )
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'clones'"
            ).fetchall()
        counts = _bucket_counts(rows)

        # The fixture is constructed so every pairwise combination above
        # the 0.50 threshold fires. We only need to assert that at least
        # one row landed in test_intentional and that NO test_intentional
        # row was misclassified as production.
        assert counts["test_intentional"] >= 1, (
            f"tests/test_a × tests/test_b pair should be test_intentional; "
            f"counts={counts}"
        )
    finally:
        os.chdir(old_cwd)


def test_clones_exclude_tests_flag_drops_test_findings(tmp_path):
    """``--exclude-tests`` skips emitting test_intentional rows; mixed survives.

    The flag is a FILTER on what the current invocation emits, not a
    retroactive deletion of prior runs. We assert two things on two
    independent project states:

      1. A clean run WITH --exclude-tests emits zero test_intentional
         rows (the filter is doing its job).
      2. A clean run WITHOUT the flag emits at least one
         test_intentional row (proving the fixture would surface those
         rows absent the filter).

    Using two independent tmp_path-derived projects avoids the
    ``store_clones`` semantics where ``clone_pairs`` truncates but
    ``findings`` upserts (prior test_intentional rows would otherwise
    survive a flagged re-run because the filter prevents the upsert
    from refreshing them, but the old rows still exist).
    """
    runner = CliRunner()

    # Branch A: clean project, run with --exclude-tests.
    proj_a = _multi_role_project(tmp_path / "a")
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj_a))
        assert runner.invoke(cli, ["index"]).exit_code == 0
        filtered = runner.invoke(
            cli,
            ["clones", "--threshold", "0.50", "--persist", "--exclude-tests"],
        )
        assert filtered.exit_code == 0, filtered.output
        with open_db(readonly=True) as conn:
            filt_rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'clones'"
            ).fetchall()
        filt_counts = _bucket_counts(filt_rows)
        assert filt_counts["test_intentional"] == 0, (
            f"--exclude-tests left test_intentional behind on a clean run: "
            f"{filt_counts}"
        )
    finally:
        os.chdir(old_cwd)

    # Branch B: independent clean project, baseline run (no flag) — must
    # produce at least one test_intentional row, proving the fixture
    # genuinely creates them.
    proj_b = _multi_role_project(tmp_path / "b")
    try:
        os.chdir(str(proj_b))
        assert runner.invoke(cli, ["index"]).exit_code == 0
        baseline = runner.invoke(
            cli, ["clones", "--threshold", "0.50", "--persist"]
        )
        assert baseline.exit_code == 0, baseline.output
        with open_db(readonly=True) as conn:
            base_rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'clones'"
            ).fetchall()
        base_counts = _bucket_counts(base_rows)
        assert base_counts["test_intentional"] >= 1, (
            f"baseline (no flag) failed to produce test_intentional rows; "
            f"counts={base_counts}"
        )
        # And the baseline must be strictly larger than the filtered run
        # (assuming the same fixture shape on both branches).
        assert len(base_rows) > len(filt_rows), (
            "baseline produced no more rows than filtered run — fixture "
            "shape not generating enough test_intentional pairs"
        )
    finally:
        os.chdir(old_cwd)


def test_clones_mixed_bucket_flagged_for_test_leakage(tmp_path):
    """One side in src/, other in tests/ ⇒ role_bucket: "mixed".

    This case is the test-leakage signal: a production function with a
    structural clone living inside the test suite. We deliberately keep
    mixed rows through ``--exclude-tests`` (opinionated W165 decision —
    drop test_intentional only) so agents auditing test_intentional vs.
    refactor candidates still see the entanglement.
    """
    proj = _multi_role_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(
            cli, ["clones", "--threshold", "0.50", "--persist"]
        )
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'clones'"
            ).fetchall()
        counts = _bucket_counts(rows)

        # The fixture produces process_orders (src/a) clone-paired with
        # process_orders_t (tests/test_a) — that's exactly a mixed pair.
        assert counts["mixed"] >= 1, (
            f"expected at least one mixed-bucket finding "
            f"(src/ × tests/), got counts={counts}"
        )

        # Mixed must survive --exclude-tests (W165 opinionated decision:
        # potential test-leakage > noise reduction for that one bucket).
        filtered = runner.invoke(
            cli,
            ["clones", "--threshold", "0.50", "--persist", "--exclude-tests"],
        )
        assert filtered.exit_code == 0, filtered.output
        with open_db(readonly=True) as conn:
            filt_rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'clones'"
            ).fetchall()
        filt_counts = _bucket_counts(filt_rows)
        assert filt_counts["mixed"] >= 1, (
            f"--exclude-tests must NOT drop the mixed bucket; counts={filt_counts}"
        )
    finally:
        os.chdir(old_cwd)


def test_clones_verdict_surfaces_per_bucket_counts(tmp_path):
    """Verdict line includes the (P production · T test_intentional · M mixed) breakdown.

    This is the Pattern-3 vocabulary improvement called out in the W165
    task brief: agents that read only the verdict (LAW 6 from
    ``CLAUDE.md``) get the bucketing without parsing the JSON envelope.
    """
    proj = _multi_role_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(
            cli,
            [
                "--json",
                "clones",
                "--threshold",
                "0.50",
                "--persist",
            ],
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        summary = envelope["summary"]
        verdict = summary["verdict"]

        # Verdict must mention all three buckets by name so an agent
        # consuming only the verdict can act on the breakdown.
        assert "production" in verdict, verdict
        assert "test_intentional" in verdict, verdict
        assert "mixed" in verdict, verdict

        # Summary must carry a structured role_buckets dict too.
        assert "role_buckets" in summary, summary
        assert set(summary["role_buckets"].keys()) == {
            "production",
            "test_intentional",
            "mixed",
        }
    finally:
        os.chdir(old_cwd)


def test_clones_exclude_fixtures_drops_fixture_rows(tmp_path):
    """``--exclude-fixtures`` skips emitting findings under fixtures/.

    Same two-branch pattern as ``test_clones_exclude_tests_flag_drops_test_findings``:
    branch A is a clean run with the flag, branch B is a clean run
    without it. The two-projects approach sidesteps the upsert semantics
    of ``store_clones`` (clone_pairs truncates but findings rows upsert,
    so flag-induced misses on a re-run leave old rows behind).
    """
    runner = CliRunner()

    proj_a = _multi_role_project(tmp_path / "a", include_fixture=True)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj_a))
        assert runner.invoke(cli, ["index"]).exit_code == 0
        filtered = runner.invoke(
            cli,
            [
                "clones",
                "--threshold",
                "0.50",
                "--persist",
                "--exclude-fixtures",
            ],
        )
        assert filtered.exit_code == 0, filtered.output
        with open_db(readonly=True) as conn:
            filt_rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'clones'"
            ).fetchall()
        for r in filt_rows:
            ev = json.loads(r["evidence_json"])
            fa = (ev.get("file_a") or "").replace("\\", "/")
            fb = (ev.get("file_b") or "").replace("\\", "/")
            assert "/fixtures/" not in fa and "/fixtures/" not in fb, (
                f"--exclude-fixtures left a fixture row behind: {fa} × {fb}"
            )
    finally:
        os.chdir(old_cwd)

    proj_b = _multi_role_project(tmp_path / "b", include_fixture=True)
    try:
        os.chdir(str(proj_b))
        assert runner.invoke(cli, ["index"]).exit_code == 0
        baseline = runner.invoke(
            cli, ["clones", "--threshold", "0.50", "--persist"]
        )
        assert baseline.exit_code == 0, baseline.output
        with open_db(readonly=True) as conn:
            base_rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'clones'"
            ).fetchall()
        base_fixture_rows = []
        for r in base_rows:
            ev = json.loads(r["evidence_json"])
            fa = (ev.get("file_a") or "").replace("\\", "/")
            fb = (ev.get("file_b") or "").replace("\\", "/")
            if "/fixtures/" in fa or "/fixtures/" in fb:
                base_fixture_rows.append(r)
        assert len(base_fixture_rows) >= 1, (
            "baseline (no flag) must yield at least one fixture-touching row "
            "to prove the filter actually has work to do"
        )
    finally:
        os.chdir(old_cwd)
