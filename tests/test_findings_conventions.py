"""Tests for the W133 follow-up: conventions detector emits to the central
findings registry.

The conventions detector is the next migrating onto the A4 findings
registry (after ``clones`` in W95, ``dead`` in W99, ``complexity`` in
W102, ``smells`` in W109, and the subsequent W110/W111 emitters). It
continues to return its in-memory list of naming outliers to the caller
and ALSO emits one row per outlier into ``findings`` when invoked with
``--persist``.

Pattern 4 (CLAUDE.md) note: ``roam.commands.conventions_helper.
compute_conventions`` is the canonical detector — ``describe``,
``understand``, ``minimap``, ``preflight``, and the standalone
``conventions`` command all delegate to it. W133 wires ``--persist``
onto the STANDALONE ``conventions`` command only, NOT onto all 5
surfaces (which would re-entrench Pattern 4 at the persistence layer).
These tests assert that scoping.

The fixtures use Python files with deliberately camelCase function
names. Python's community default is ``snake_case`` (see
``_LANGUAGE_KIND_DEFAULTS``), so the camelCase functions are
guaranteed to surface as outliers regardless of empirical majority.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from tests._findings_helpers import assert_detector_visible_in_findings_count
from roam.commands.cmd_conventions import (
    CONVENTIONS_DETECTOR_VERSION,
    _conventions_finding_id,
    _conventions_violation_confidence,
    _emit_conventions_findings,
)
from roam.db.connection import open_db
from tests.conftest import make_src_project as _make_project


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _convention_violating_project(tmp_path):
    """Tiny repo with Python functions that violate the snake_case default.

    Python's ``_LANGUAGE_KIND_DEFAULTS`` declares ``snake_case`` as the
    community-default style for functions. The camelCase / PascalCase
    function names below are therefore guaranteed outliers regardless
    of the empirical majority in the fixture.

    We deliberately mix in a handful of snake_case names too so the
    by_family_group dominant style stays snake_case (the helper picks
    the documented community default first, but the breakdown should
    still reflect the same picture).
    """
    return _make_project(
        tmp_path,
        {
            "shop.py": """
            def goodSnakeCase():  # NOTE: this name is intentionally camelCase
                return 1

            def anotherCamelOne():
                return 2

            def regular_function():
                return 3

            def proper_helper():
                return 4

            def MoreOffenders():  # PascalCase function — also a violation
                return 5
            """,
        },
    )


def _persist_conventions(proj):
    """Index the project and run ``conventions --persist``.

    Returns the CliRunner result so tests can assert on its exit code.
    """
    runner = CliRunner()
    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["conventions", "--persist"])
    assert result.exit_code == 0, result.output
    return result


# ---------------------------------------------------------------------------
# Core migration assertions
# ---------------------------------------------------------------------------


def test_conventions_emits_to_findings_registry(tmp_path):
    """Running conventions --persist on a violating fixture populates findings."""
    proj = _convention_violating_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conventions(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence "
                "FROM findings WHERE source_detector = 'conventions'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one conventions-emitted finding row"
        for r in rows:
            assert r["source_detector"] == "conventions"
            assert r["source_version"] == CONVENTIONS_DETECTOR_VERSION
            assert r["subject_kind"] in ("symbol", "file")
            assert r["confidence"] in ("heuristic", "structural")
            assert r["finding_id_str"].startswith("conventions:naming-outlier:")
    finally:
        os.chdir(old_cwd)


def test_conventions_finding_id_is_deterministic():
    """_conventions_finding_id returns the same id for the same outlier triple."""
    a = _conventions_finding_id("python", "functions", "goodSnakeCase", "src/a.py", 10)
    b = _conventions_finding_id("python", "functions", "goodSnakeCase", "src/a.py", 10)
    assert a == b
    assert a.startswith("conventions:naming-outlier:")
    # Different name → different id.
    assert _conventions_finding_id(
        "python", "functions", "other", "src/a.py", 10
    ) != a
    # Different file → different id.
    assert _conventions_finding_id(
        "python", "functions", "goodSnakeCase", "src/b.py", 10
    ) != a
    # Different line → different id.
    assert _conventions_finding_id(
        "python", "functions", "goodSnakeCase", "src/a.py", 11
    ) != a
    # Different family → different id.
    assert _conventions_finding_id(
        "js", "functions", "goodSnakeCase", "src/a.py", 10
    ) != a
    # Different group → different id.
    assert _conventions_finding_id(
        "python", "classes", "goodSnakeCase", "src/a.py", 10
    ) != a


def test_conventions_rerun_upserts_not_duplicates(tmp_path):
    """Re-running conventions --persist produces the same finding_id_str set."""
    proj = _convention_violating_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conventions(proj)

        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings "
                    "WHERE source_detector = 'conventions'"
                ).fetchall()
            }
            first_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'conventions'"
            ).fetchone()[0]
        assert first_count == len(first_ids), (
            "duplicate finding_id_str rows on first run"
        )

        # Second run — same fixture, same detector predicate → same ids.
        runner = CliRunner()
        result = runner.invoke(cli, ["conventions", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings "
                    "WHERE source_detector = 'conventions'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'conventions'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


def test_conventions_finding_evidence_carries_outlier_fields(tmp_path):
    """The finding's evidence JSON carries the per-outlier convention context."""
    proj = _convention_violating_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conventions(proj)

        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT evidence_json, claim FROM findings "
                "WHERE source_detector = 'conventions' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
        assert row is not None
        evidence = json.loads(row["evidence_json"])
        for k in (
            "name",
            "kind",
            "language_family",
            "kind_group",
            "actual_style",
            "expected_style",
            "expected_source",
            "file_path",
            "line_start",
        ):
            assert k in evidence, f"evidence missing field {k}"
        # The claim must name the outlier.
        assert evidence["name"] in (row["claim"] or "")
        # The actual style must differ from the expected style — every
        # outlier is, by construction, a mismatch.
        assert evidence["actual_style"] != evidence["expected_style"]
    finally:
        os.chdir(old_cwd)


def test_conventions_finding_subject_links_to_symbols_row(tmp_path):
    """subject_id, when populated, resolves to a real symbols row."""
    proj = _convention_violating_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conventions(proj)

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT subject_id FROM findings "
                "WHERE source_detector = 'conventions' AND subject_id IS NOT NULL"
            ).fetchall()
            assert len(rows) >= 1, (
                "expected at least one conventions finding with a resolved "
                "subject_id"
            )
            for r in rows:
                sym = conn.execute(
                    "SELECT id, name FROM symbols WHERE id = ?", (r["subject_id"],)
                ).fetchone()
                assert sym is not None, f"orphan subject_id {r['subject_id']}"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Per-expected-source confidence tier mapping
# ---------------------------------------------------------------------------


def _seed_for_emit_helper(tmp_path):
    """Open a writable connection on a fresh repo with the findings table.

    The detector + indexer aren't needed here — we exercise
    ``_emit_conventions_findings`` directly on synthetic outlier dicts
    so the tier mapping is verified independently of which violations
    the helper happens to surface on a given fixture.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def test_conventions_tier_mapping_community_default_is_structural(tmp_path):
    """Outliers against community-default styles land at structural confidence.

    The Python community default for functions is ``snake_case`` (from
    ``_LANGUAGE_KIND_DEFAULTS``). An outlier whose ``expected_source``
    is ``"community_default"`` is upgraded to ``structural`` — the
    expected style isn't an empirical guess; it's a documented
    language convention.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        outliers = [
            {
                "name": "badName",
                "kind": "function",
                "language_family": "python",
                "actual_style": "camelCase",
                "expected_style": "snake_case",
                "expected_source": "community_default",
                "file": "src/a.py",
                "line": 10,
            },
            {
                "name": "wrongClass",
                "kind": "class",
                "language_family": "python",
                "actual_style": "camelCase",
                "expected_style": "PascalCase",
                "expected_source": "community_default",
                "file": "src/a.py",
                "line": 20,
            },
        ]
        written = _emit_conventions_findings(
            conn, outliers, CONVENTIONS_DETECTOR_VERSION
        )
        assert written == len(outliers)
        rows = conn.execute(
            "SELECT confidence FROM findings "
            "WHERE source_detector = 'conventions'"
        ).fetchall()
        assert len(rows) == len(outliers)
        for r in rows:
            assert r["confidence"] == "structural"


def test_conventions_tier_mapping_empirical_is_heuristic(tmp_path):
    """Outliers against empirical majority styles land at heuristic confidence.

    When the expected style comes from the empirical distribution
    (``expected_source == "empirical"``), there's no documented basis —
    the rule is "the rest of the codebase does it this way". That's
    a heuristic by construction.
    """
    with _seed_for_emit_helper(tmp_path) as conn:
        outliers = [
            {
                "name": "odd_one_out",
                "kind": "function",
                "language_family": "unknown",
                "actual_style": "snake_case",
                "expected_style": "camelCase",
                "expected_source": "empirical",
                "file": "src/a.js",
                "line": 10,
            },
        ]
        written = _emit_conventions_findings(
            conn, outliers, CONVENTIONS_DETECTOR_VERSION
        )
        assert written == 1
        row = conn.execute(
            "SELECT confidence FROM findings "
            "WHERE source_detector = 'conventions'"
        ).fetchone()
        assert row["confidence"] == "heuristic"


def test_conventions_violation_confidence_helper():
    """Direct test of the source → tier mapping function."""
    assert _conventions_violation_confidence("community_default") == "structural"
    assert _conventions_violation_confidence("empirical") == "heuristic"
    # Unknown / missing falls through to the default heuristic tier
    # (e.g. a future ``expected_source`` value we haven't taught the
    # mapper about yet — fail open at the lower confidence tier).
    assert _conventions_violation_confidence(None) == "heuristic"
    assert _conventions_violation_confidence("future-source") == "heuristic"


# ---------------------------------------------------------------------------
# Visibility through the read-side CLI (`roam findings`)
# ---------------------------------------------------------------------------


def test_conventions_findings_visible_via_cmd_findings_list(tmp_path):
    """`roam findings list --detector conventions` returns rows after migration."""
    proj = _convention_violating_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conventions(proj)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "findings", "list", "--detector", "conventions"]
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "findings-list"
        assert envelope["summary"]["state"] == "populated"
        assert envelope["summary"]["total_findings"] >= 1
        assert "conventions" in envelope["summary"]["detectors"]
        assert all(
            r["source_detector"] == "conventions" for r in envelope["findings"]
        )
    finally:
        os.chdir(old_cwd)


def test_conventions_findings_visible_via_cmd_findings_count(tmp_path):
    """`roam findings count` includes a non-zero entry for conventions."""
    proj = _convention_violating_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        _persist_conventions(proj)
    finally:
        os.chdir(old_cwd)
    assert_detector_visible_in_findings_count(proj, "conventions")


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_no_persist_does_not_emit_findings(tmp_path):
    """Without --persist, the standard read path stays side-effect-free.

    The registry mirror lives inside the ``--persist`` branch — running
    ``roam conventions`` without the flag must not write to ``findings``.
    Important for Pattern 4: the helper is called by 5 surfaces
    (``conventions``, ``describe``, ``understand``, ``minimap``,
    ``preflight``) and only the persist branch on the standalone
    ``conventions`` command should write to the registry.
    """
    proj = _convention_violating_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        # No --persist.
        assert runner.invoke(cli, ["conventions"]).exit_code == 0

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings "
                    "WHERE source_detector = 'conventions'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, "non-persist conventions still wrote to findings"
    finally:
        os.chdir(old_cwd)


def test_other_4_surfaces_do_not_persist_conventions(tmp_path):
    """`describe`, `understand`, `minimap`, `preflight` MUST NOT write
    conventions findings even without --persist on their flag list.

    Pattern 4 guard: these 4 commands consume the canonical detector
    via ``compute_conventions`` but should not have a persistence path
    of their own — only the standalone ``conventions --persist`` writes
    to the registry. If any of them ever gain a ``--persist`` that
    also emits conventions rows, this test should be updated to test
    the union (because that would mean a deliberate design change,
    not a regression).
    """
    proj = _convention_violating_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        # Run each of the 4 non-canonical surfaces. They emit text /
        # JSON describing the conventions; none should populate the
        # registry. Use --json so each returns quickly without
        # rendering large text reports.
        for cmd_args in (
            ["--json", "describe"],
            ["--json", "understand"],
            ["--json", "minimap"],
            # preflight wants a symbol argument; pick the one we know
            # exists in the fixture. If preflight fails to resolve it
            # the test still serves its purpose (the conventions
            # registry table must stay empty).
            ["--json", "preflight", "regular_function"],
        ):
            runner.invoke(cli, cmd_args)

        with open_db(readonly=True) as conn:
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM findings "
                    "WHERE source_detector = 'conventions'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
        assert count == 0, (
            "a non-canonical surface (describe/understand/minimap/preflight) "
            "wrote conventions rows — this re-entrenches Pattern 4 at the "
            "persistence layer"
        )
    finally:
        os.chdir(old_cwd)


def test_conventions_persist_no_findings_table_no_crash(tmp_path):
    """``conventions --persist`` degrades cleanly when the findings table is absent.

    Simulates the pre-W89 schema by DROP-ing ``findings`` after init
    but before the persist call. The standard detector-output path
    must keep working — the command exits 0 and writes no registry rows.
    """
    proj = _convention_violating_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        with open_db(readonly=False) as conn:
            conn.execute("DROP TABLE IF EXISTS findings")
            conn.commit()

        result = runner.invoke(cli, ["conventions", "--persist"])
        # Must succeed despite the missing findings table.
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# W162 — false-positive carve-outs
# ---------------------------------------------------------------------------
#
# The W149 dogfood audit found 39 conventions findings on roam-code, 0 of
# which were real. Root causes split three ways:
#
#   * 27 came from ``tests/fixtures/languages/kotlin/*.kt`` — deliberately-
#     malformed fixture files that the detector was reading as if they
#     were real code.
#   * 6 Python PascalCase "variables" that were ``TypeAlias`` /
#     ``NewType`` declarations.
#   * 2 ``VERSION`` "properties" misclassified as variables — they're
#     class-level constants per PEP 8.
#
# These tests pin the carve-outs so the noise doesn't grow back.


def _conventions_violations(runner: CliRunner) -> list[dict]:
    """Return the unwrapped violations list from ``roam --json conventions``.

    R22 wraps each violation as ``{value, confidence, reason}`` — these
    tests want the inner ``value`` (the convention-violation dict the
    helper produced). Returns an empty list when the command emits no
    violations envelope.
    """
    res = runner.invoke(cli, ["--json", "conventions"])
    assert res.exit_code == 0, res.output
    env = json.loads(res.output)
    wrapped = env.get("violations") or []
    return [w.get("value") if isinstance(w, dict) and "value" in w else w for w in wrapped]


def test_conventions_skips_tests_fixtures_directory(tmp_path):
    """Deliberately-malformed fixture files do NOT contribute violations.

    Pattern: detector should not read its own training data. This test
    plants a Python file under ``tests/fixtures/`` whose naming would
    otherwise be flagged (camelCase / PascalCase functions vs the
    Python community-default snake_case) and asserts the violation
    count is zero.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    # Real source file: a single, conventional, snake_case function.
    src = proj / "src"
    src.mkdir()
    (src / "real.py").write_text(
        "def regular_function():\n    return 1\n",
        encoding="utf-8",
    )
    # Fixture file with deliberately-broken naming. If the detector
    # reads this it WILL flag the camelCase / PascalCase functions
    # against Python's snake_case default.
    fixtures = proj / "tests" / "fixtures" / "languages" / "python"
    fixtures.mkdir(parents=True)
    (fixtures / "bad_names.py").write_text(
        "def camelCaseFunction():\n    return 1\n"
        "def AnotherCamelOne():\n    return 2\n"
        "def MoreOffenders():\n    return 3\n",
        encoding="utf-8",
    )
    import subprocess as _subprocess
    _subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    _subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    _subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(proj), capture_output=True,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        violations = _conventions_violations(runner)
        # All violations come from the fixture; with the exclusion in
        # place there must be NONE.
        offending = [v for v in violations if "fixtures" in (v.get("file") or "")]
        assert offending == [], (
            "tests/fixtures/ identifiers leaked into the conventions detector: "
            f"{offending}"
        )
        # And the real source file's snake_case function must not
        # itself be flagged (sanity).
        from_real = [v for v in violations if (v.get("file") or "").endswith("real.py")]
        assert from_real == [], from_real
    finally:
        os.chdir(old_cwd)


def test_conventions_accepts_python_typealias_pascalcase(tmp_path):
    """``PathLike: TypeAlias = Union[str, Path]`` is NOT a naming outlier.

    PEP 484 / PEP 613 say PascalCase is the correct case style for
    type aliases — the python extractor stores them as
    ``kind="variable"`` but they should not be treated as variables for
    convention checking. Covers Union / Optional / Literal / Callable /
    tuple / list / dict RHS shapes plus the ``: TypeAlias`` annotation.
    """
    proj = _make_project(
        tmp_path,
        {
            "aliases.py": """
            from typing import Callable, Literal, Optional, TypeAlias, Union
            from pathlib import Path

            PathLike: TypeAlias = Union[str, Path]
            LockMode = Literal["read", "write", "exclusive"]
            CommandTarget = tuple[str, str]
            Finding = dict[str, int]
            DetectorSpec = tuple[str, str, Callable[[int], list[int]]]
            MaybeName = Optional[str]

            def regular_function() -> int:
                return 1
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        violations = _conventions_violations(runner)
        # The PascalCase type-alias names must NOT appear as outliers.
        alias_names = {"PathLike", "LockMode", "CommandTarget",
                       "Finding", "DetectorSpec", "MaybeName"}
        leaked = [v["name"] for v in violations if v.get("name") in alias_names]
        assert leaked == [], f"type aliases flagged as PascalCase variables: {leaked}"
    finally:
        os.chdir(old_cwd)


def test_conventions_accepts_python_newtype_pascalcase(tmp_path):
    """``LockMode = NewType("LockMode", str)`` is NOT a naming outlier.

    ``NewType`` is the PEP 484 mechanism for declaring a distinct type
    that happens to share a runtime representation. PascalCase is the
    correct style. The python extractor stores it as ``kind=variable``
    with a NewType call on the RHS — the carve-out reads the signature
    and lets it through.
    """
    proj = _make_project(
        tmp_path,
        {
            "newtypes.py": """
            from typing import NewType

            UserId = NewType("UserId", int)
            OrderId = NewType("OrderId", str)

            def regular_function() -> int:
                return 1
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        violations = _conventions_violations(runner)
        leaked = [v["name"] for v in violations if v.get("name") in {"UserId", "OrderId"}]
        assert leaked == [], f"NewType aliases flagged as variables: {leaked}"
    finally:
        os.chdir(old_cwd)


def test_conventions_accepts_upper_snake_module_constants(tmp_path):
    """Module-level ``VERSION = "1.0.0"`` is a constant, not a variable.

    PEP 8 §"Naming Conventions" — "constants are usually defined on a
    module level and written in all capital letters with underscores
    separating words". The detector must route UPPER_SNAKE / single-
    upper-word names into the ``constants`` group regardless of the
    declared extractor kind, so they're matched against the
    ``UPPER_SNAKE`` expectation for that group rather than the
    ``snake_case`` expectation for variables.
    """
    proj = _make_project(
        tmp_path,
        {
            "consts.py": """
            VERSION = "1.0.0"
            MAX_RETRIES = 5
            DEFAULT_TIMEOUT_S = 30

            def regular_function() -> int:
                return 1
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        violations = _conventions_violations(runner)
        const_names = {"VERSION", "MAX_RETRIES", "DEFAULT_TIMEOUT_S"}
        leaked = [v["name"] for v in violations if v.get("name") in const_names]
        assert leaked == [], f"module-level constants flagged as variables: {leaked}"
    finally:
        os.chdir(old_cwd)


def test_conventions_accepts_upper_snake_class_constants(tmp_path):
    """Class-level ``class X: VERSION = "1.0.0"`` is a constant, not a variable.

    Mirror of the module-level case for class bodies. The python
    extractor stores class-level ``VERSION`` as ``kind="property"`` —
    without the carve-out it lands in ``python/variables`` and is
    flagged as a ``snake_case`` outlier (the actual W149 false
    positive on ``LanguageExtractor.VERSION`` and
    ``LanguageBridge.VERSION`` in this repo).
    """
    proj = _make_project(
        tmp_path,
        {
            "klass.py": """
            class Bridge:
                VERSION = "1.0.0"
                MAX_RETRIES = 5

                def regular_method(self) -> int:
                    return 1

            class Extractor:
                VERSION = "2.0.0"

                def language_name(self) -> str:
                    return "py"
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        violations = _conventions_violations(runner)
        const_names = {"VERSION", "MAX_RETRIES"}
        leaked = [v["name"] for v in violations if v.get("name") in const_names]
        assert leaked == [], f"class-level constants flagged as variables: {leaked}"
    finally:
        os.chdir(old_cwd)
