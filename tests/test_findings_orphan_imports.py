"""Tests for the W132 follow-up: orphan-imports detector emits to the
central findings registry.

The orphan-imports detector is the fifth detector migrating onto the A4
findings registry (after ``clones`` in W95, ``dead`` in W99,
``complexity`` in W102, and ``smells`` in W109). It continues to return
its in-memory list of orphan dicts to the caller and ALSO emits one row
per orphan into ``findings`` when invoked with ``--persist``. These
tests cover that additive emit and the end-to-end visibility through
``roam findings`` for an agent.

Fixtures lean on two reliably-triggerable orphan kinds:

* ``internal_typo`` — Python: a top-level package that IS indexed plus
  a dotted submodule that is NOT. (We index a tiny ``pkg/`` and then
  write a consumer that imports ``pkg.does_not_exist``.) ``static_analysis``.
* ``missing_package`` — Python: a dotted path that resolves neither in
  the index nor via ``importlib.util.find_spec``. ``static_analysis``.

The ``missing_local`` (JS/Go) tier is verified directly via the emit
helper on synthetic finding dicts so the per-kind tier mapping stays
single-sourced.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_orphan_imports import (
    _ORPHAN_KIND_TO_CONFIDENCE,
    ORPHAN_IMPORTS_DETECTOR_VERSION,
    _emit_orphan_imports_findings,
    _orphan_finding_id,
)
from roam.db.connection import open_db
from tests._findings_helpers import assert_detector_visible_in_findings_count
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _orphan_project(tmp_path):
    """Tiny repo with two Python files that trigger at least one orphan each.

    * ``pkg/__init__.py`` makes ``pkg`` a known indexed package.
    * ``consumer.py`` imports ``pkg.does_not_exist`` → internal_typo
      (top-level ``pkg`` is indexed, submodule is not).
    * ``consumer.py`` also imports ``totally_made_up_pkg_xyzzy`` →
      missing_package (resolves nowhere).

    Keeping the fixture deliberately small so the indexer runs in well
    under a second on every host.
    """
    return _make_project(
        tmp_path,
        {
            "pkg/__init__.py": """
            # marker so the package is discoverable
            VALUE = 1
            """,
            "pkg/real_mod.py": """
            def real_fn():
                return 1
            """,
            "consumer.py": """
            import pkg.does_not_exist
            from totally_made_up_pkg_xyzzy import something

            def consume():
                return something
            """,
        },
    )


def _persist_orphan_imports(proj):
    """Index the project and run ``orphan-imports --persist``.

    Returns the CliRunner result so tests can assert on its exit code if
    they care about the persist path itself.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["orphan-imports", "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_orphan_imports_emits_to_findings_registry(tmp_path):
    """Running orphan-imports --persist populates the findings table."""
    proj = _orphan_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_orphan_imports(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'orphan-imports'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one orphan-imports-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "orphan-imports"
            assert r["source_version"] == ORPHAN_IMPORTS_DETECTOR_VERSION
            assert r["subject_kind"] in ("file", "module")
            # All current kinds map to static_analysis; the per-kind
            # mapping is exercised independently below.
            assert r["confidence"] in (
                "static_analysis",
                "structural",
                "heuristic",
            )
            assert r["finding_id_str"].startswith("orphan-imports:")
    finally:
        os.chdir(old_cwd)


def test_orphan_finding_id_is_deterministic():
    """_orphan_finding_id returns the same id for the same (lang, file, module, line)."""
    a = _orphan_finding_id("python", "src/a.py", "pkg.missing", 10)
    b = _orphan_finding_id("python", "src/a.py", "pkg.missing", 10)
    assert a == b
    assert a.startswith("orphan-imports:python:")
    # Different language → different id.
    assert _orphan_finding_id("javascript", "src/a.py", "pkg.missing", 10) != a
    # Different file → different id.
    assert _orphan_finding_id("python", "src/b.py", "pkg.missing", 10) != a
    # Different module → different id.
    assert _orphan_finding_id("python", "src/a.py", "pkg.other", 10) != a
    # Different line → different id.
    assert _orphan_finding_id("python", "src/a.py", "pkg.missing", 11) != a


def test_orphan_imports_rerun_upserts_not_duplicates(tmp_path):
    """Re-running orphan-imports --persist produces the same id set."""
    proj = _orphan_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_orphan_imports(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'orphan-imports'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'orphan-imports'"
            ).fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"

        # Second run — same fixture, same detector predicates → same ids.
        runner = CliRunner()
        result = runner.invoke(cli, ["orphan-imports", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'orphan-imports'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'orphan-imports'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_orphan_imports_finding_evidence_carries_per_orphan_fields(tmp_path):
    """The finding's evidence JSON carries the per-orphan context."""
    proj = _orphan_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_orphan_imports(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings "
                "WHERE source_detector = 'orphan-imports' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None
        evidence = json.loads(row["evidence_json"])
        for k in ("language", "file", "line", "module", "kind", "hint"):
            assert k in evidence, f"evidence missing field {k}"
        # The claim must name the orphan kind.
        assert evidence["kind"] in (row["claim"] or "")
        # The claim must name the offending module.
        assert evidence["module"] in (row["claim"] or "")
    finally:
        os.chdir(old_cwd)


def test_orphan_imports_finding_subject_links_to_files_row(tmp_path):
    """subject_id, when populated, resolves to a real files row."""
    proj = _orphan_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_orphan_imports(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT subject_id, subject_kind FROM findings "
                "WHERE source_detector = 'orphan-imports' "
                "  AND subject_id IS NOT NULL"
            ).fetchall()
            # Every populated subject_id is subject_kind='file' and
            # points at a real files row.
            for r in rows:
                assert r["subject_kind"] == "file"
                f = conn.execute(
                    "SELECT id, path FROM files WHERE id = ?",
                    (r["subject_id"],),
                ).fetchone()
                assert f is not None, f"orphan subject_id {r['subject_id']}"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Per-kind confidence tier mapping
# ---------------------------------------------------------------------------


def _seed_for_emit_helper(tmp_path):
    """Open a writable connection on a fresh repo with the findings table.

    The detector + indexer aren't needed here — we exercise
    ``_emit_orphan_imports_findings`` directly on synthetic orphan dicts
    so the per-kind tier mapping is verified independently of which
    orphans the scanners happen to surface on a given fixture.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def test_orphan_kind_tier_mapping_static_analysis(tmp_path):
    """All current orphan kinds land at static_analysis confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        orphans = [
            {
                "language": "python",
                "file": "src/a.py",
                "line": 10,
                "module": "pkg.does_not_exist",
                "kind": "internal_typo",
                "hint": "top-level 'pkg' is indexed but full path is not",
            },
            {
                "language": "python",
                "file": "src/a.py",
                "line": 11,
                "module": "totally_made_up_pkg_xyzzy",
                "kind": "missing_package",
                "hint": "neither indexed nor importable",
            },
            {
                "language": "javascript",
                "file": "src/b.js",
                "line": 5,
                "module": "./missing_local_file",
                "kind": "missing_local",
                "hint": "resolved path not in indexed JS files",
            },
            {
                "language": "go",
                "file": "src/c.go",
                "line": 3,
                "module": "myrepo/missing/pkg",
                "kind": "missing_local",
                "hint": "Go import path not in indexed packages",
            },
        ]
        written = _emit_orphan_imports_findings(conn, orphans, ORPHAN_IMPORTS_DETECTOR_VERSION)
        assert written == len(orphans)
        rows = conn.execute(
            "SELECT evidence_json, confidence FROM findings WHERE source_detector = 'orphan-imports'"
        ).fetchall()
        assert len(rows) == len(orphans)
        for r in rows:
            ev = json.loads(r["evidence_json"])
            assert r["confidence"] == "static_analysis", (
                f"kind {ev['kind']!r} expected static_analysis, got {r['confidence']!r}"
            )


def test_orphan_kind_tier_fallback_is_heuristic(tmp_path):
    """An unknown future kind falls back to ``heuristic``.

    Drift guard — if someone adds a name-based / pattern-based kind to
    the scanner but forgets to update ``_ORPHAN_KIND_TO_CONFIDENCE``,
    the emit helper still classifies it conservatively rather than
    over-claiming ``static_analysis``.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        written = _emit_orphan_imports_findings(
            conn,
            [
                {
                    "language": "python",
                    "file": "src/a.py",
                    "line": 1,
                    "module": "weirdly_named_import",
                    "kind": "speculative_future_kind",
                    "hint": "looks unused but may be a side-effect import",
                }
            ],
            ORPHAN_IMPORTS_DETECTOR_VERSION,
        )
        assert written == 1
        row = conn.execute("SELECT confidence FROM findings WHERE source_detector = 'orphan-imports'").fetchone()
        assert row["confidence"] == "heuristic"


def test_orphan_kind_mapping_covers_all_current_kinds():
    """The per-kind tier table covers every kind the scanners emit.

    Drift guard: if a new orphan kind is added to ``_scan_python``,
    ``_scan_javascript``, or ``_scan_go`` without a matching entry
    here, the emit helper falls back to the default ``heuristic`` tier
    silently. Surface the omission loudly so the tier choice is
    intentional.
    """
    # Source-of-truth set — also referenced by the R22 classifier in
    # ``_ORPHAN_KIND_CONFIDENCE`` for display-side confidence labels.
    from roam.commands.cmd_orphan_imports import _ORPHAN_KIND_CONFIDENCE

    classifier_kinds = set(_ORPHAN_KIND_CONFIDENCE.keys())
    registry_kinds = set(_ORPHAN_KIND_TO_CONFIDENCE.keys())
    missing = classifier_kinds - registry_kinds
    assert not missing, (
        f"orphan kinds present in _ORPHAN_KIND_CONFIDENCE but missing "
        f"from _ORPHAN_KIND_TO_CONFIDENCE: {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_orphan_imports_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector orphan-imports` returns rows after migration."""
    proj = _orphan_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_orphan_imports(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "orphan-imports"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "orphan-imports" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "orphan-imports" for r in envelope["findings"])
    finally:
        os.chdir(old_cwd)


def test_orphan_imports_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for orphan-imports."""
    proj = _orphan_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_orphan_imports(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "orphan-imports")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard read path stays side-effect-free.

    The registry mirror lives inside the ``--persist`` branch — running
    ``roam orphan-imports`` without the flag must not write to ``findings``.
    """
    proj = _orphan_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["orphan-imports"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'orphan-imports'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist orphan-imports still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_orphan_imports_persist_no_findings_table_no_crash(tmp_path):
    """``orphan-imports --persist`` degrades cleanly when findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before the persist call. The standard detector-output path (text /
    JSON) which legacy consumers depend on must keep working — the
    command exits 0 and writes no registry rows.
    """
    proj = _orphan_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["orphan-imports", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)
