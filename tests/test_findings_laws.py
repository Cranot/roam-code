"""Tests for the W119 follow-up: laws detector emits to the central
findings registry.

The laws miner is the fifth detector migrating onto the A4 findings
registry (after ``clones`` in W95, ``dead`` in W99, ``complexity`` in
W102, and ``smells`` in W109). It continues to return its list of
:class:`roam.laws.miner.Law` dataclasses (and the YAML round-trip)
and ALSO emits one row per mined law into ``findings`` when invoked
with ``--persist``. These tests cover that additive emit and the
end-to-end visibility through ``roam findings`` for an agent.

The fixture exercises the two mining strategies that fire reliably on a
tiny synthetic repo:

* ``naming`` laws — derived from :mod:`roam.commands.conventions_helper`.
  Snake-case functions dominate → ``structural`` confidence tier.
* ``testing`` laws — public symbols matched against ``test_<name>.py``
  test-file basenames → ``heuristic`` confidence tier.

``import`` laws need a cross-directory edge graph that's awkward in a
single-file fixture, so the per-kind tier mapping for ``import`` and the
stub kinds is verified directly via ``_emit_laws_findings`` on synthetic
:class:`Law` dataclasses rather than via the end-to-end indexer + miner
path.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_laws import (
    _LAW_KIND_TO_CONFIDENCE,
    LAWS_DETECTOR_VERSION,
    _emit_laws_findings,
    _law_finding_id,
)
from roam.db.connection import open_db
from roam.laws.miner import Law
from tests.conftest import make_src_project as _make_project


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _lawful_project(tmp_path):
    """Tiny repo with enough symbols and tests to trigger naming + testing laws.

    Twelve snake_case public functions (well above the ``_MIN_SAMPLE_SIZE``
    threshold of 5 in :mod:`roam.laws.miner`) — the dominant style is
    100% snake_case so the naming-law conformance lands at "high".

    Six test files whose basenames mention the public-function names —
    above the ``_MIN_CONFORMANCE_PCT`` threshold of 70% so a
    ``public_functions_must_be_tested`` law fires.

    Keeping the fixture deliberately small so the indexer runs in well
    under a second on every host.
    """
    files: dict[str, str] = {}
    # 12 snake_case public functions across two modules.
    files["mod_a.py"] = (
        "def compute_alpha(x):\n    return x + 1\n\n"
        "def compute_beta(x):\n    return x + 2\n\n"
        "def compute_gamma(x):\n    return x + 3\n\n"
        "def compute_delta(x):\n    return x + 4\n\n"
        "def compute_epsilon(x):\n    return x + 5\n\n"
        "def compute_zeta(x):\n    return x + 6\n"
    )
    files["mod_b.py"] = (
        "def run_eta(x):\n    return x * 2\n\n"
        "def run_theta(x):\n    return x * 3\n\n"
        "def run_iota(x):\n    return x * 4\n\n"
        "def run_kappa(x):\n    return x * 5\n\n"
        "def run_lambda_(x):\n    return x * 6\n\n"
        "def run_mu(x):\n    return x * 7\n"
    )
    # Matching test files for most of the public functions. Land far above
    # the 70% conformance threshold so a testing-law fires.
    for fname in (
        "test_compute_alpha.py",
        "test_compute_beta.py",
        "test_compute_gamma.py",
        "test_compute_delta.py",
        "test_compute_epsilon.py",
        "test_compute_zeta.py",
        "test_run_eta.py",
        "test_run_theta.py",
        "test_run_iota.py",
        "test_run_kappa.py",
    ):
        files[fname] = "def test_it():\n    assert True\n"
    return _make_project(tmp_path, files)


def _persist_laws(proj):
    """Index the project and run ``laws mine --persist``.

    Returns the CliRunner result so tests can assert on its exit code if
    they care about the persist path itself.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["laws", "mine", "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_laws_emits_to_findings_registry(tmp_path):
    """Running ``laws mine --persist`` on a lawful fixture populates findings."""
    proj = _lawful_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_laws(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, subject_id, confidence "
                "FROM findings WHERE source_detector = 'laws'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one laws-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "laws"
            assert r["source_version"] == LAWS_DETECTOR_VERSION
            # Laws are repo-level invariants — subject_kind is "file" by
            # design (no per-symbol grounding), subject_id stays NULL.
            assert r["subject_kind"] == "file"
            assert r["subject_id"] is None
            assert r["confidence"] in (
                "static_analysis",
                "structural",
                "heuristic",
            )
            assert r["finding_id_str"].startswith("laws:")
    finally:
        os.chdir(old_cwd)


def test_law_finding_id_is_deterministic():
    """_law_finding_id returns the same id for the same (kind, id) pair."""
    law_a = Law(id="snake_case_functions", kind="naming", description="d")
    law_b = Law(id="snake_case_functions", kind="naming", description="d")
    assert _law_finding_id(law_a) == _law_finding_id(law_b)
    assert _law_finding_id(law_a).startswith("laws:naming:")
    # Different kind → different id (even with the same slug).
    law_c = Law(id="snake_case_functions", kind="import", description="d")
    assert _law_finding_id(law_c) != _law_finding_id(law_a)
    # Different slug → different id.
    law_d = Law(id="camel_case_functions", kind="naming", description="d")
    assert _law_finding_id(law_d) != _law_finding_id(law_a)


def test_laws_rerun_upserts_not_duplicates(tmp_path):
    """Re-running ``laws mine --persist`` produces the same finding_id_str set."""
    proj = _lawful_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_laws(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'laws'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'laws'"
            ).fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"

        # Second run — same fixture, same miner predicates → same ids.
        runner = CliRunner()
        result = runner.invoke(cli, ["laws", "mine", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'laws'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'laws'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_laws_finding_evidence_carries_law_fields(tmp_path):
    """The finding's evidence JSON carries the per-law context."""
    proj = _lawful_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_laws(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings "
                "WHERE source_detector = 'laws' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None
        evidence = json.loads(row["evidence_json"])
        for k in (
            "law_id",
            "kind",
            "description",
            "severity",
            "confidence_label",
            "rule",
            "evidence",
        ):
            assert k in evidence, f"evidence missing field {k}"
        # The claim must name the law id and the kind.
        assert evidence["law_id"] in (row["claim"] or "")
        assert evidence["kind"] in (row["claim"] or "")
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Per-kind confidence tier mapping
# ---------------------------------------------------------------------------


def _seed_for_emit_helper(tmp_path):
    """Open a writable connection on a fresh repo with the findings table.

    The miner isn't needed here — we exercise ``_emit_laws_findings``
    directly on synthetic :class:`Law` dataclasses so the per-kind tier
    mapping is verified independently of which strategies the miner
    triggers on a given fixture.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def test_law_kind_tier_mapping_structural(tmp_path):
    """Graph / AST-derived law kinds land at structural confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        laws = [
            Law(
                id="snake_case_functions",
                kind="naming",
                description="Functions must be snake_case",
                evidence={"sample_size": 50, "conformance_pct": 96.0},
                confidence="high",
                rule={"kind": "naming", "symbol_kind": "function", "style": "snake_case"},
            ),
            Law(
                id="imports_src_handlers_to_src_db",
                kind="import",
                description="Files in src/handlers/ import from src/db/",
                evidence={"sample_size": 40, "conformance_pct": 95.0},
                confidence="high",
                rule={"kind": "import", "from_dir": "src/handlers", "to_dir": "src/db"},
            ),
        ]
        written = _emit_laws_findings(conn, laws, LAWS_DETECTOR_VERSION)
        assert written == len(laws)
        rows = conn.execute(
            "SELECT evidence_json, confidence FROM findings "
            "WHERE source_detector = 'laws'"
        ).fetchall()
        assert len(rows) == len(laws)
        for r in rows:
            ev = json.loads(r["evidence_json"])
            assert r["confidence"] == "structural", (
                f"law kind {ev['kind']!r} expected structural, "
                f"got {r['confidence']!r}"
            )


def test_law_kind_tier_mapping_heuristic(tmp_path):
    """Name-pattern law kinds land at heuristic confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        laws = [
            Law(
                id="public_functions_must_be_tested",
                kind="testing",
                description="Public functions should have a matching test file",
                evidence={"sample_size": 30, "conformance_pct": 80.0},
                confidence="medium",
                rule={
                    "kind": "testing",
                    "symbol_kind": "function",
                    "test_pattern": "test_*",
                },
            ),
        ]
        written = _emit_laws_findings(conn, laws, LAWS_DETECTOR_VERSION)
        assert written == 1
        row = conn.execute(
            "SELECT confidence FROM findings WHERE source_detector = 'laws'"
        ).fetchone()
        assert row["confidence"] == "heuristic"


def test_law_kind_mapping_covers_all_miner_strategies():
    """The per-kind tier table covers every kind emitted by :func:`mine_laws`.

    Drift guard: if a new mining strategy lands in roam.laws.miner without
    a matching entry here, the emit helper falls back to the default
    ``structural`` tier silently. Surface the omission loudly so the
    tier choice is intentional.
    """
    # The 5 documented strategies in roam.laws.miner (Strategies A-E):
    # naming, import, testing, errors, co_change.
    expected_kinds = {"naming", "import", "testing", "errors", "co_change"}
    mapped_kinds = set(_LAW_KIND_TO_CONFIDENCE.keys())
    missing = expected_kinds - mapped_kinds
    assert not missing, (
        f"law kinds documented in roam.laws.miner but missing from "
        f"_LAW_KIND_TO_CONFIDENCE: {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_laws_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector laws` returns rows after migration."""
    proj = _lawful_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_laws(proj)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "laws"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "laws" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "laws" for r in envelope["findings"]
        )
    finally:
        os.chdir(old_cwd)


def test_laws_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for laws."""
    proj = _lawful_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_laws(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "laws")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard read path stays side-effect-free.

    The registry mirror lives inside the ``--persist`` branch — running
    ``roam laws mine`` without the flag must not write to ``findings``.
    """
    proj = _lawful_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["laws", "mine"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'laws'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist laws still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_laws_persist_no_findings_table_no_crash(tmp_path):
    """``laws mine --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before the persist call. The standard miner output path (text / JSON /
    YAML) which legacy consumers depend on must keep working — the command
    exits 0 and writes no registry rows.
    """
    proj = _lawful_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["laws", "mine", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)
