"""Tests for the W109 follow-up: smells detector emits to the central
findings registry.

The smells detector is the fourth detector migrating onto the A4 findings
registry (after ``clones`` in W95, ``dead`` in W99, and ``complexity`` in
W102). It continues to return its in-memory list of smell findings to the
caller and ALSO emits one row per finding into ``findings`` when invoked
with ``--persist``. These tests cover that additive emit and the
end-to-end visibility through ``roam findings`` for an agent.

The fixtures lean on two reliably-triggerable smell kinds:

* ``deep-nesting`` (a function with a 5+-level conditional pyramid) —
  ``static_analysis`` confidence tier.
* ``long-params`` (a function with > 5 parameters) — ``static_analysis``
  confidence tier.

Structural-tier kinds (``shotgun-surgery``, ``feature-envy``, …)
require cross-file edge graphs that are awkward to set up in a tiny
fixture, so the per-kind tier mapping is verified directly via
``_emit_smells_findings`` on a synthetic finding list rather than via
the end-to-end indexer + detector path.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_smells import (
    _SMELL_KIND_TO_CONFIDENCE,
    SMELLS_DETECTOR_VERSION,
    _emit_smells_findings,
    _smell_finding_id,
)
from roam.db.connection import open_db
from tests._findings_helpers import assert_detector_visible_in_findings_count
from tests.conftest import make_src_project as _make_project


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _smelly_project(tmp_path):
    """Tiny repo with two functions that trigger at least one smell each.

    * ``deeply_nested`` has 5+ levels of nested conditionals — triggers
      ``deep-nesting`` (threshold: nesting_depth > 4).
    * ``too_many_params`` declares > 5 parameters — triggers
      ``long-params`` (threshold: > 5 params, excluding self/cls).

    Keeping the fixture deliberately small so the indexer runs in well
    under a second on every host.
    """
    return _make_project(
        tmp_path,
        {
            "smelly.py": """
            def deeply_nested(items, mode):
                results = []
                for item in items:
                    if item:
                        if mode == "a":
                            if item.value:
                                if item.value > 0:
                                    if item.tag:
                                        if item.tag != "skip":
                                            results.append(item)
                return results

            def too_many_params(alpha, beta, gamma, delta, epsilon, zeta, eta):
                return alpha + beta + gamma + delta + epsilon + zeta + eta
        """,
        },
    )


def _persist_smells(proj):
    """Index the project and run ``smells --persist``.

    Returns the CliRunner result so tests can assert on its exit code if
    they care about the persist path itself.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["smells", "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_smells_emits_to_findings_registry(tmp_path):
    """Running smells --persist on a smelly fixture populates findings."""
    proj = _smelly_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_smells(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'smells'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one smells-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "smells"
            assert r["source_version"] == SMELLS_DETECTOR_VERSION
            assert r["subject_kind"] in ("symbol", "file")
            # The tiers actually populated by the fixture above are
            # both static_analysis; the rest of the mapping is
            # exercised by ``test_smell_kind_tier_mapping`` below.
            assert r["confidence"] in (
                "static_analysis",
                "structural",
                "heuristic",
            )
            assert r["finding_id_str"].startswith("smells:")
    finally:
        os.chdir(old_cwd)


def test_smell_finding_id_is_deterministic():
    """_smell_finding_id returns the same id for the same (kind, file, name, line)."""
    a = _smell_finding_id("deep-nesting", "src/a.py", "foo", 10)
    b = _smell_finding_id("deep-nesting", "src/a.py", "foo", 10)
    assert a == b
    assert a.startswith("smells:deep-nesting:")
    # Different smell kind → different id.
    assert _smell_finding_id("long-params", "src/a.py", "foo", 10) != a
    # Different file → different id.
    assert _smell_finding_id("deep-nesting", "src/b.py", "foo", 10) != a
    # Different line → different id.
    assert _smell_finding_id("deep-nesting", "src/a.py", "foo", 11) != a
    # Different symbol name → different id.
    assert _smell_finding_id("deep-nesting", "src/a.py", "bar", 10) != a


def test_smells_rerun_upserts_not_duplicates(tmp_path):
    """Re-running smells --persist produces the same finding_id_str set."""
    proj = _smelly_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_smells(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'smells'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'smells'"
            ).fetchone()[0]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"

        # Second run — same fixture, same detector predicates → same ids.
        runner = CliRunner()
        result = runner.invoke(cli, ["smells", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'smells'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'smells'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_smells_finding_evidence_carries_smell_fields(tmp_path):
    """The finding's evidence JSON carries the per-finding smell context."""
    proj = _smelly_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_smells(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings "
                "WHERE source_detector = 'smells' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None
        evidence = json.loads(row["evidence_json"])
        for k in (
            "smell_id",
            "severity",
            "symbol_name",
            "kind",
            "file_path",
            "metric_value",
            "threshold",
            "description",
        ):
            assert k in evidence, f"evidence missing field {k}"
        # The claim must name the smell id.
        assert evidence["smell_id"] in (row["claim"] or "")
    finally:
        os.chdir(old_cwd)


def test_smells_finding_subject_links_to_symbols_row(tmp_path):
    """subject_id, when populated, resolves to a real symbols row."""
    proj = _smelly_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_smells(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT subject_id FROM findings "
                "WHERE source_detector = 'smells' AND subject_id IS NOT NULL"
            ).fetchall()
            assert len(rows) >= 1, (
                "expected at least one smells finding with a resolved subject_id"
            )
            for r in rows:
                sym = conn.execute(
                    "SELECT id, name FROM symbols WHERE id = ?", (r["subject_id"],)
                ).fetchone()
                assert sym is not None, f"orphan subject_id {r['subject_id']}"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Per-kind confidence tier mapping
# ---------------------------------------------------------------------------


def _seed_for_emit_helper(tmp_path):
    """Open a writable connection on a fresh repo with the findings table.

    The detector + indexer aren't needed here — we exercise
    ``_emit_smells_findings`` directly on synthetic finding dicts so the
    per-kind tier mapping is verified independently of which smells the
    DB-backed detectors happen to trigger on a given fixture.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def test_smell_kind_tier_mapping_static_analysis(tmp_path):
    """AST-metric smells land at static_analysis confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        findings = [
            {
                "smell_id": "brain-method",
                "severity": "critical",
                "symbol_name": "messy",
                "kind": "function",
                "location": "src/a.py:10",
                "metric_value": 80,
                "threshold": 60,
                "description": "Brain method: complexity 80",
            },
            {
                "smell_id": "deep-nesting",
                "severity": "warning",
                "symbol_name": "deep",
                "kind": "function",
                "location": "src/a.py:20",
                "metric_value": 6,
                "threshold": 4,
                "description": "Deep nesting: depth 6",
            },
            {
                "smell_id": "long-params",
                "severity": "warning",
                "symbol_name": "many_params",
                "kind": "function",
                "location": "src/a.py:30",
                "metric_value": 8,
                "threshold": 5,
                "description": "Long parameter list: 8 params",
            },
            {
                "smell_id": "large-class",
                "severity": "critical",
                "symbol_name": "Big",
                "kind": "class",
                "location": "src/a.py:40",
                "metric_value": 600,
                "threshold": 500,
                "description": "Large class: 600 LOC",
            },
            {
                "smell_id": "dead-params",
                "severity": "info",
                "symbol_name": "stub",
                "kind": "function",
                "location": "src/a.py:50",
                "metric_value": 5,
                "threshold": 4,
                "description": "Dead params: 5 params but complexity 0",
            },
        ]
        written = _emit_smells_findings(conn, findings, SMELLS_DETECTOR_VERSION)
        assert written == len(findings)
        rows = conn.execute(
            "SELECT evidence_json, confidence FROM findings "
            "WHERE source_detector = 'smells'"
        ).fetchall()
        assert len(rows) == len(findings)
        for r in rows:
            ev = json.loads(r["evidence_json"])
            assert r["confidence"] == "static_analysis", (
                f"smell_id {ev['smell_id']!r} expected static_analysis, "
                f"got {r['confidence']!r}"
            )


def test_smell_kind_tier_mapping_structural(tmp_path):
    """Graph-edge-based smells land at structural confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        findings = [
            {
                "smell_id": "god-class",
                "severity": "critical",
                "symbol_name": "GodClass",
                "kind": "class",
                "location": "src/g.py:1",
                "metric_value": 35,
                "threshold": 30,
                "description": "God class: 35 methods",
            },
            {
                "smell_id": "feature-envy",
                "severity": "warning",
                "symbol_name": "envious",
                "kind": "function",
                "location": "src/g.py:50",
                "metric_value": 80,
                "threshold": 50,
                "description": "Feature envy: 8/10 refs (80%) to other files",
            },
            {
                "smell_id": "shotgun-surgery",
                "severity": "warning",
                "symbol_name": "popular",
                "kind": "function",
                "location": "src/g.py:100",
                "metric_value": 15,
                "threshold": 7,
                "description": "Shotgun surgery: 15 incoming dependencies",
            },
            {
                "smell_id": "low-cohesion",
                "severity": "warning",
                "symbol_name": "Loose",
                "kind": "class",
                "location": "src/g.py:150",
                "metric_value": 1,
                "threshold": 3,
                "description": "Low cohesion: 6 methods but 1 internal edge",
            },
            {
                "smell_id": "message-chain",
                "severity": "info",
                "symbol_name": "chatter",
                "kind": "function",
                "location": "src/g.py:200",
                "metric_value": 15,
                "threshold": 10,
                "description": "Message chain: 15 outgoing calls",
            },
        ]
        written = _emit_smells_findings(conn, findings, SMELLS_DETECTOR_VERSION)
        assert written == len(findings)
        rows = conn.execute(
            "SELECT evidence_json, confidence FROM findings "
            "WHERE source_detector = 'smells'"
        ).fetchall()
        assert len(rows) == len(findings)
        for r in rows:
            ev = json.loads(r["evidence_json"])
            assert r["confidence"] == "structural", (
                f"smell_id {ev['smell_id']!r} expected structural, "
                f"got {r['confidence']!r}"
            )


def test_smell_kind_tier_mapping_heuristic(tmp_path):
    """Pattern-based smells land at heuristic confidence."""
    with _seed_for_emit_helper(tmp_path) as conn:
        findings = [
            {
                "smell_id": "data-clumps",
                "severity": "info",
                "symbol_name": "first_clump_fn",
                "kind": "function",
                "location": "src/d.py:10",
                "metric_value": 4,
                "threshold": 3,
                "description": "Data clump: params (name,user_id,email) repeated in 4 functions",
            },
        ]
        written = _emit_smells_findings(conn, findings, SMELLS_DETECTOR_VERSION)
        assert written == 1
        row = conn.execute(
            "SELECT confidence FROM findings WHERE source_detector = 'smells'"
        ).fetchone()
        assert row["confidence"] == "heuristic"


def test_smell_kind_mapping_covers_all_detectors():
    """The per-kind tier table covers every detector in roam.catalog.smells.

    Drift guard: if a new detector is added to the registry without a
    matching entry here, the emit helper falls back to the default
    ``heuristic`` tier silently. Surface the omission loudly so the
    tier choice is intentional.
    """
    from roam.catalog.smells import ALL_DETECTORS

    registry_ids = {smell_id for smell_id, _ in ALL_DETECTORS}
    mapped_ids = set(_SMELL_KIND_TO_CONFIDENCE.keys())
    missing = registry_ids - mapped_ids
    assert not missing, (
        f"smell kinds present in roam.catalog.smells.ALL_DETECTORS but "
        f"missing from _SMELL_KIND_TO_CONFIDENCE: {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_smells_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector smells` returns rows after migration."""
    proj = _smelly_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_smells(proj)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "smells"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "smells" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "smells" for r in envelope["findings"]
        )
    finally:
        os.chdir(old_cwd)


def test_smells_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for smells."""
    proj = _smelly_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_smells(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "smells")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard read path stays side-effect-free.

    The registry mirror lives inside the ``--persist`` branch — running
    ``roam smells`` without the flag must not write to ``findings``.
    """
    proj = _smelly_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["smells"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings WHERE source_detector = 'smells'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist smells still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_smells_persist_no_findings_table_no_crash(tmp_path):
    """``smells --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before the persist call. The standard detector-output path (text /
    JSON / SARIF) which legacy consumers depend on must keep working —
    the command exits 0 and writes no registry rows.
    """
    proj = _smelly_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["smells", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# W163 — dead-params constructor / dataclass / lifecycle exemptions
# ---------------------------------------------------------------------------
#
# Pre-W163, ``detect_dead_params`` flagged any function/method with 4+
# parameters and cognitive complexity <= 1. That predicate
# misclassifies constructors (``__init__`` stores params to self by
# definition), dataclass-generated methods (auto-emitted, body is
# empty by construction), and pytest/unittest lifecycle hooks
# (``setUp`` / ``setup_method`` …). The W149 dogfood audit found
# ~40 % of dead-params findings were these false positives.
#
# These tests pin the exemption: synthetic fixtures for each of the
# four exempt shapes assert NO dead-params finding, plus a regression
# guard asserting a *real* dead-param function (4+ unused params,
# complexity <= 1, ordinary name) still trips the detector.

def _run_dead_params_on_proj(proj):
    """Index ``proj``, then call ``detect_dead_params`` directly.

    Returns the list of finding dicts the catalog detector emitted.
    Bypassing the CLI (with its tooling-exclusion + min-severity
    filters) keeps these assertions about the detector's predicate
    itself, not the wrapper's display filters.
    """
    from roam.catalog.smells import detect_dead_params

    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    with open_db(readonly=True) as conn:
        return detect_dead_params(conn)


def test_init_method_not_flagged_as_dead_params(tmp_path):
    """A bog-standard ``__init__`` that just stores params to self is exempt."""
    proj = _make_project(
        tmp_path,
        {
            "init_only.py": """
            class Widget:
                def __init__(self, a, b, c, d, e, f, g, h):
                    self.a = a
                    self.b = b
                    self.c = c
                    self.d = d
                    self.e = e
                    self.f = f
                    self.g = g
                    self.h = h
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        findings = _run_dead_params_on_proj(proj)
        offenders = [f for f in findings if f["symbol_name"] == "__init__"]
        assert not offenders, (
            f"__init__ should be exempt from dead-params, got: {offenders}"
        )
    finally:
        os.chdir(old_cwd)


def test_dataclass_init_not_flagged(tmp_path):
    """A ``@dataclass`` class's auto-generated __init__ is exempt.

    The detector reads the parent class's ``decorators`` column. The
    fixture also adds an explicit ``__post_init__`` to verify the
    second arm of the rule (dataclass post-init is also exempt
    regardless of decorator detection).
    """
    proj = _make_project(
        tmp_path,
        {
            "dc.py": """
            from dataclasses import dataclass

            @dataclass
            class Big:
                a: int
                b: int
                c: int
                d: int
                e: int
                f: int
                g: int
                h: int

                def __post_init__(self):
                    self._total = self.a + self.b + self.c + self.d
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        findings = _run_dead_params_on_proj(proj)
        # No dead-params from either the dataclass class itself or its
        # __post_init__.
        offenders = [
            f
            for f in findings
            if f["symbol_name"] in ("__init__", "__post_init__", "Big")
        ]
        assert not offenders, (
            f"@dataclass methods should be exempt from dead-params, got: {offenders}"
        )
    finally:
        os.chdir(old_cwd)


def test_real_dead_params_still_flagged(tmp_path):
    """Regression guard: an ordinary function with unused params still trips.

    Body is a single ``return 42`` — cognitive complexity 0, 8 params,
    none used. This is the exact shape the detector was built for and
    must keep flagging after the exemption was added.
    """
    proj = _make_project(
        tmp_path,
        {
            "real.py": """
            def actually_dead(a, b, c, d, e, f, g, h):
                return 42
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        findings = _run_dead_params_on_proj(proj)
        hit = [f for f in findings if f["symbol_name"] == "actually_dead"]
        assert hit, (
            "real dead-params function should still be flagged after "
            f"the W163 exemption — findings: {findings}"
        )
        assert hit[0]["smell_id"] == "dead-params"
    finally:
        os.chdir(old_cwd)


def test_setup_method_not_flagged(tmp_path):
    """pytest/unittest lifecycle hooks are exempt.

    Covers both:
      * ``setUp`` (unittest naming convention; exact-match exemption).
      * ``setup_method`` (pytest naming convention; prefix-match
        exemption via ``setup_*``).
    """
    proj = _make_project(
        tmp_path,
        {
            "test_fixtures.py": """
            class TestThing:
                def setUp(self, a, b, c, d, e, f, g):
                    self.a = a
                    self.b = b
                    self.c = c
                    self.d = d
                    self.e = e
                    self.f = f
                    self.g = g

                def setup_method(self, a, b, c, d, e, f, g):
                    self.a = a
                    self.b = b
                    self.c = c
                    self.d = d
                    self.e = e
                    self.f = f
                    self.g = g

                def tearDown(self, a, b, c, d, e, f, g):
                    self.a = a
                    self.b = b
                    self.c = c
                    self.d = d
                    self.e = e
                    self.f = f
                    self.g = g
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        findings = _run_dead_params_on_proj(proj)
        offenders = [
            f
            for f in findings
            if f["symbol_name"] in ("setUp", "setup_method", "tearDown")
        ]
        assert not offenders, (
            f"setUp / setup_* / tearDown should be exempt from dead-params, "
            f"got: {offenders}"
        )
    finally:
        os.chdir(old_cwd)
