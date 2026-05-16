"""Tests for the W125 follow-up: vibe-check detector emits to the central
findings registry.

The vibe-check detector is the third detector migration onto the A4
findings table (after W95's clones and W96's dead). It continues to
render its own JSON / text envelopes (authoritative output surface) and
ALSO, when ``--persist`` is set, emits one row per anti-pattern finding
into ``findings``.

The 8 sub-patterns ride distinct confidence tiers:

* ``dead_exports`` → structural (graph reachability)
* ``hallucinated_imports`` → structural (graph queries)
* ``copy_paste`` → structural (deterministic block-hash equality)
* ``short_churn`` → heuristic (commit-count threshold)
* ``empty_handlers`` → heuristic (regex on raw source)
* ``abandoned_stubs`` → heuristic (regex on raw source)
* ``error_inconsistency`` → heuristic (regex pattern count threshold)
* ``comment_anomalies`` → heuristic (z-score statistical outlier)

These tests cover that additive emit and the end-to-end visibility
through ``roam findings`` for an agent.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_vibe_check import (
    VIBE_CHECK_DETECTOR_VERSION,
    _vibe_check_tier,
    _vibe_finding_id,
)
from roam.db.connection import open_db
from roam.db.findings import (
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_STRUCTURAL,
)
from tests._findings_helpers import assert_detector_visible_in_findings_count
from tests.conftest import make_src_project as _make_project


def _rot_project(tmp_path):
    """Tiny repo with several anti-patterns vibe-check should flag.

    Designed to hit at least three distinct kinds with a single index +
    persist call so the test doesn't depend on git churn or runtime
    fingerprints:

    * ``orphan_export`` — exported but uncalled → ``dead_exports``.
    * ``stub_one``/``stub_two``/``stub_three`` — three ``pass`` stubs →
      ``abandoned_stubs``.
    * ``catch_empty.py`` — three near-identical functions sharing the
      same normalised body → ``copy_paste`` (group of >=3).
    * Each of those functions also wraps an ``except: pass`` block →
      ``empty_handlers``.
    """
    return _make_project(
        tmp_path,
        {
            "lib.py": """
            def used_helper(value):
                return value * 2

            def orphan_export(items):
                results = []
                for item in items:
                    results.append(item)
                return results

            def stub_one():
                pass

            def stub_two():
                pass

            def stub_three():
                pass
            """,
            "catch_empty.py": """
            def process_alpha(data):
                try:
                    return data["value"]
                except Exception:
                    pass

            def process_beta(data):
                try:
                    return data["value"]
                except Exception:
                    pass

            def process_gamma(data):
                try:
                    return data["value"]
                except Exception:
                    pass
            """,
            "main.py": """
            from .lib import used_helper

            def main():
                return used_helper(5)
            """,
        },
    )


def _run_vibe_check_persist(proj):
    """Index the project and run ``vibe-check --persist``.

    Returns the CliRunner result so tests can assert on its exit code.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["vibe-check", "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_vibe_check_emits_to_findings_registry(tmp_path):
    """Running vibe-check --persist populates findings rows."""
    proj = _rot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'vibe-check'"
            ).fetchall()
        assert len(rows) >= 1, (
            "expected at least one vibe-check-emitted finding row "
            "(dead_exports + abandoned_stubs alone should produce several)"
        )
        for r in rows:
            assert r["source_detector"] == "vibe-check"
            assert r["source_version"] == VIBE_CHECK_DETECTOR_VERSION
            assert r["subject_kind"] in ("symbol", "file")
            # Only structural / heuristic for this detector.
            assert r["confidence"] in ("structural", "heuristic")
            assert r["finding_id_str"].startswith("vibe-check:")
    finally:
        os.chdir(old_cwd)


def test_vibe_check_finding_id_str_is_deterministic(tmp_path):
    """Re-running vibe-check produces the same id set (upsert, not duplicate)."""
    proj = _rot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'vibe-check'"
                ).fetchall()
            }
            first_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'vibe-check'").fetchone()[
                0
            ]
        assert first_count == len(first_ids), "duplicate finding_id_str rows on first run"

        # Second run — same fixture, same detectors, same hash inputs.
        runner = CliRunner()
        result = runner.invoke(cli, ["vibe-check", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'vibe-check'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'vibe-check'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_vibe_finding_id_encodes_kind():
    """_vibe_finding_id returns kind-prefixed ids for upsert routing.

    The format is ``vibe-check:<kind>:<digest>`` — consumers filtering
    by kind can do a prefix match without parsing JSON evidence.
    """
    assert _vibe_finding_id("dead_exports", "a/b.py:foo", 1).startswith("vibe-check:dead_exports:")
    assert _vibe_finding_id("copy_paste", "a/b.py:foo:hash", 5).startswith("vibe-check:copy_paste:")
    assert _vibe_finding_id("short_churn", "a/b.py", None).startswith("vibe-check:short_churn:")
    # Stable under repeated calls.
    assert _vibe_finding_id("stubs", "x", 1) == _vibe_finding_id("stubs", "x", 1)


def test_vibe_check_tier_mapping():
    """Confidence tier mapping matches the W125 decision per kind."""
    assert _vibe_check_tier("dead_exports") == CONFIDENCE_STRUCTURAL
    assert _vibe_check_tier("hallucinated_imports") == CONFIDENCE_STRUCTURAL
    assert _vibe_check_tier("copy_paste") == CONFIDENCE_STRUCTURAL

    assert _vibe_check_tier("short_churn") == CONFIDENCE_HEURISTIC
    assert _vibe_check_tier("empty_handlers") == CONFIDENCE_HEURISTIC
    assert _vibe_check_tier("abandoned_stubs") == CONFIDENCE_HEURISTIC
    assert _vibe_check_tier("error_inconsistency") == CONFIDENCE_HEURISTIC
    assert _vibe_check_tier("comment_anomalies") == CONFIDENCE_HEURISTIC

    # Unknown kind defaults to heuristic (no crash).
    assert _vibe_check_tier("unknown_pattern") == CONFIDENCE_HEURISTIC


def test_vibe_check_finding_evidence_carries_pattern_key(tmp_path):
    """Every finding's evidence_json names the pattern kind explicitly."""
    proj = _rot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT evidence_json FROM findings WHERE source_detector = 'vibe-check'").fetchall()
        assert len(rows) >= 1
        kinds_seen: set[str] = set()
        for r in rows:
            evidence = json.loads(r["evidence_json"])
            assert "pattern" in evidence, "each vibe-check finding must label its pattern in evidence"
            kinds_seen.add(evidence["pattern"])
        # At least one of the patterns we engineered into the fixture
        # should fire. dead_exports is the most reliable across CI hosts.
        assert kinds_seen, "expected at least one pattern key in evidence"
    finally:
        os.chdir(old_cwd)


def test_vibe_check_dead_export_finding_links_to_symbol(tmp_path):
    """dead_exports findings carry subject_kind=symbol with a real subject_id."""
    proj = _rot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT subject_id, evidence_json FROM findings "
                "WHERE source_detector = 'vibe-check' "
                "AND subject_kind = 'symbol' "
                "AND finding_id_str LIKE 'vibe-check:dead_exports:%'"
            ).fetchall()
            # The fixture defines orphan_export + stub_one/two/three; all
            # are exported and uncalled, so at least one should land.
            assert len(rows) >= 1, "no dead_exports vibe findings emitted"
            for r in rows:
                assert r["subject_id"] is not None, "dead_exports findings must carry a resolved subject_id"
                sym = conn.execute(
                    "SELECT id, name FROM symbols WHERE id = ?",
                    (r["subject_id"],),
                ).fetchone()
                assert sym is not None, f"orphan subject_id {r['subject_id']}"
                evidence = json.loads(r["evidence_json"])
                assert evidence["pattern"] == "dead_exports"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_vibe_check_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector vibe-check` returns rows after migration."""
    proj = _rot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "list", "--detector", "vibe-check"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "vibe-check" in envelope["summary"]["detectors"]
        assert all(r["source_detector"] == "vibe-check" for r in envelope["findings"])
    finally:
        os.chdir(old_cwd)


def test_vibe_check_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for vibe-check."""
    proj = _rot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "vibe-check")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_vibe_check_no_findings_table_no_crash(tmp_path):
    """vibe-check --persist degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init but
    before vibe-check --persist runs. The detector's standard text/JSON
    output (which existing consumers depend on) must keep working.
    """
    proj = _rot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["vibe-check", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
        # The standard text output still names the verdict.
        assert "AI rot score" in result.output
    finally:
        os.chdir(old_cwd)


def test_vibe_check_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, no findings rows are written.

    Mirror-into-registry must remain gated on --persist so the read-only
    invocation stays side-effect-free.
    """
    proj = _rot_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["vibe-check"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'vibe-check'").fetchone()[0]
            except sqlite3.OperationalError:
                # findings table may not be present on every schema flavour
                # — that's still a "no findings emitted" outcome.
                count = 0
        assert count == 0, "non-persist vibe-check still wrote to findings"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# W371 — Modular Mirage + Boilerplate Inflation
#
# Two new patterns added to vibe-check per the May 2026 arxiv research
# (2605.02741 + 2512.18020). The detectors emit into the central findings
# registry alongside the existing 8 score-bearing patterns but DO NOT
# alter the canonical AI rot score (they sit at weight=0).
# ---------------------------------------------------------------------------


def _modular_mirage_project(tmp_path):
    """Fixture with one single-caller exported helper + one many-callers.

    ``helper_used_once`` lives in ``lib_helper.py`` and is called exactly
    once from ``main.py``: the modular-mirage signal. ``shared_util``
    lives in ``lib_shared.py`` and is called from THREE separate
    consumers — that's a real abstraction and must NOT be flagged.
    """
    return _make_project(
        tmp_path,
        {
            "lib_helper.py": '''
            def helper_used_once(value):
                """Apply the special doubling rule."""
                return value * 2
            ''',
            "lib_shared.py": '''
            def shared_util(value):
                """Real reusable helper — used from multiple sites."""
                return value + 1
            ''',
            "main.py": """
            from .lib_helper import helper_used_once
            from .lib_shared import shared_util

            def main():
                return helper_used_once(5)

            def consumer_a():
                return shared_util(1)

            def consumer_b():
                return shared_util(2)

            def consumer_c():
                return shared_util(3)
            """,
        },
    )


def _polymorphism_project(tmp_path):
    """Fixture with real polymorphism — abstract class + 3 subclasses.

    Vibe-check must NOT flag the abstract method as a modular-mirage
    just because each subclass overrides it. The signal is exactly-one-
    caller, not exactly-one-implementation. (We also keep the call
    sites cross-file so the same-file dedup doesn't accidentally hide
    the test.)
    """
    return _make_project(
        tmp_path,
        {
            "base.py": '''
            class Animal:
                def speak(self):
                    """Override in subclasses."""
                    raise NotImplementedError
            ''',
            "dog.py": """
            from .base import Animal

            class Dog(Animal):
                def speak(self):
                    return "woof"
            """,
            "cat.py": """
            from .base import Animal

            class Cat(Animal):
                def speak(self):
                    return "meow"
            """,
            "cow.py": """
            from .base import Animal

            class Cow(Animal):
                def speak(self):
                    return "moo"
            """,
            "main.py": """
            from .dog import Dog
            from .cat import Cat
            from .cow import Cow

            def main():
                animals = [Dog(), Cat(), Cow()]
                return [a.speak() for a in animals]
            """,
        },
    )


def _boilerplate_project(tmp_path):
    """Fixture with both boilerplate-inflation sub-heuristics.

    ``inflated.py`` has THREE comment-restates-code occurrences and ONE
    shallow-wrapper. ``clean.py`` is the negative control — its comments
    explain WHY, not WHAT.
    """
    return _make_project(
        tmp_path,
        {
            "inflated.py": '''
            def thin_wrapper(value):
                """Call the underlying compute_total helper with value."""
                return compute_total(value)

            def compute_total(value):
                # set counter to value plus one
                counter = value + 1
                # set result to counter times two
                result = counter * 2
                # return result
                return result
            ''',
            "clean.py": """
            def real_function(value):
                # Edge case discovered in prod 2026-03-12 — empty payloads.
                if not value:
                    return None
                # Reuses the legacy seven-day rollup window agreed with finance.
                window = 7
                return value * window
            """,
            "main.py": """
            from .inflated import thin_wrapper, compute_total
            from .clean import real_function

            def main():
                return [thin_wrapper(1), compute_total(2), real_function(3)]
            """,
        },
    )


def test_modular_mirage_single_caller_export_flagged(tmp_path):
    """Single-caller exported helper produces a modular_mirage finding."""
    proj = _modular_mirage_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT subject_id, claim, evidence_json, confidence "
                "FROM findings "
                "WHERE source_detector = 'vibe-check' "
                "AND finding_id_str LIKE 'vibe-check:modular_mirage:%'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one modular_mirage finding for helper_used_once"
        mirage_names = []
        for r in rows:
            evidence = json.loads(r["evidence_json"])
            assert evidence["pattern"] == "modular_mirage"
            assert evidence["caller_count"] == 1
            assert evidence["research"] == "arxiv:2605.02741"
            assert r["confidence"] == "structural"
            mirage_names.append(evidence["name"])
        assert "helper_used_once" in mirage_names, f"expected helper_used_once in flagged names, got {mirage_names}"
        # The shared_util has THREE callers — must NOT be flagged.
        assert "shared_util" not in mirage_names, "shared_util has 3 callers; flagging it would be a false positive"
    finally:
        os.chdir(old_cwd)


def test_modular_mirage_no_false_positive_on_real_polymorphism(tmp_path):
    """Abstract method + 3 subclass overrides → NO modular_mirage flag.

    Each subclass ``.speak()`` may be a single-caller call site, but the
    polymorphic dispatch makes the abstract method a real reuse point.
    The detector deliberately skips class symbols and only flags
    function/method exports — so the only risk would be the abstract
    base. With 3 callers across the dispatch list, the count rules it
    out.
    """
    proj = _polymorphism_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'vibe-check' "
                "AND finding_id_str LIKE 'vibe-check:modular_mirage:%'"
            ).fetchall()
        flagged = [json.loads(r["evidence_json"])["name"] for r in rows]
        # ``speak`` is the polymorphic method — must not be the sole
        # signal. Even if individual subclass overrides land here, the
        # base abstract method must not (it has multiple inbound callers
        # via the dispatch list).
        assert "Animal.speak" not in flagged
    finally:
        os.chdir(old_cwd)


def test_boilerplate_inflation_comment_restating_code_flagged(tmp_path):
    """Comment-restates-code occurrences emit boilerplate_inflation findings."""
    proj = _boilerplate_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json, confidence FROM findings "
                "WHERE source_detector = 'vibe-check' "
                "AND finding_id_str LIKE 'vibe-check:boilerplate_inflation:%'"
            ).fetchall()
        subkinds_seen: set[str] = set()
        inflated_files: set[str] = set()
        for r in rows:
            evidence = json.loads(r["evidence_json"])
            assert evidence["pattern"] == "boilerplate_inflation"
            assert evidence["research"] == "arxiv:2605.02741+2512.18020"
            assert r["confidence"] == "heuristic"
            subkinds_seen.add(evidence["subkind"])
            inflated_files.add(evidence["file_path"])
        assert "comment_restates_code" in subkinds_seen, f"expected comment_restates_code, got {subkinds_seen}"
        # The negative-control file (clean.py) explains intent rather
        # than restating code, so it must not appear.
        assert not any("clean.py" in p for p in inflated_files), f"clean.py should not be flagged; got {inflated_files}"
    finally:
        os.chdir(old_cwd)


def test_boilerplate_inflation_shallow_wrapper_flagged(tmp_path):
    """Shallow Python wrapper (docstring + single call) is flagged."""
    proj = _boilerplate_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'vibe-check' "
                "AND finding_id_str LIKE 'vibe-check:boilerplate_inflation:%'"
            ).fetchall()
        wrapper_hits = [
            json.loads(r["evidence_json"])
            for r in rows
            if json.loads(r["evidence_json"])["subkind"] == "shallow_wrapper"
        ]
        assert wrapper_hits, "expected at least one shallow_wrapper finding for thin_wrapper"
        names = {hit.get("snippet", "") for hit in wrapper_hits}
        # The wrapper is recognisably ``thin_wrapper`` — its name should
        # appear in the snippet payload.
        assert any("thin_wrapper" in s for s in names), f"expected thin_wrapper in snippets, got {names}"
    finally:
        os.chdir(old_cwd)


def test_modular_mirage_and_boilerplate_inflation_in_findings_count(tmp_path):
    """`roam findings count` surfaces non-zero modular_mirage entries.

    The count command bucketises by detector, not by pattern kind, so
    both new patterns count under the ``vibe-check`` row. This test
    asserts the overall count rises after running the W371 detectors —
    sanity check that the persist path wires the new kinds through.
    """
    proj = _modular_mirage_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _run_vibe_check_persist(proj)

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "findings", "count"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["counts"].get("vibe-check", 0) >= 1
    finally:
        os.chdir(old_cwd)


def test_w371_patterns_do_not_alter_ai_rot_score(tmp_path):
    """W371 informational patterns do NOT contribute to the AI rot score.

    The canonical score is the weighted average of the 8 score-bearing
    detectors. Adding modular_mirage / boilerplate_inflation findings
    must NOT change the headline ``score`` value — they're surfaced for
    visibility only.
    """
    proj = _boilerplate_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["--json", "vibe-check"])
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        # Find the 8 score-bearing rows.
        score_bearing = [p for p in envelope["patterns"] if not p.get("informational")]
        informational = [p for p in envelope["patterns"] if p.get("informational")]
        assert len(score_bearing) == 8
        assert len(informational) == 2
        info_names = {p["name"] for p in informational}
        assert info_names == {"modular_mirage", "boilerplate_inflation"}
        for p in informational:
            assert p["weight"] == 0
    finally:
        os.chdir(old_cwd)
