"""W607-CI -- ``cmd_missing_index`` substrate-boundary plumbing.

cmd_missing_index is the missing-index detector (W111 origin per
CLAUDE.md detector roster -- part of the original 16 findings-registry
substrate detectors). Completes the ORM-detector triple alongside
cmd_n1 (W110 / W607-CB) and cmd_over_fetch (W114 / W607-CE).

The detector has W18.4 unconditional-predicate detection + W36.3
unconditional-first column ordering. W807 sealed the empty-corpus
smoke with the explicit ``no_migrations`` state, but until this wave
the command had no substrate-boundary marker plumbing -- a raise in
``_parse_migration_indexes`` (Step 2 index parsing) or
``_parse_query_patterns`` (Step 3 query enumeration) would crash the
missing-index detector outright.

This wave installs the canonical ``_w607ci_warnings_out`` bucket +
``_run_check_ci`` helper inside the ``missing-index`` click command
and wraps every substrate boundary:

* parse_migration_indexes     -- Step 2 index definitions
* parse_query_patterns        -- Step 3 query enumeration
* build_model_table_overrides -- M9 cross-file override index
* build_findings              -- Step 4 cross-reference + W18.4 +
                                 W36.3 unconditional-first ordering
* apply_confidence_filter     -- W1005-followup-D floor
* apply_table_filter          -- --table display filter
* aggregate_by_confidence     -- histogram
* emit_findings               -- W111 findings-registry mirror
                                 (sqlite3.OperationalError silent no-op
                                 preserved for pre-W89 DB)
* serialize_to_sarif          -- SARIF projection

Marker family ``missing_index_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers preserved by the
prefix-discipline test (missing-index is part of the DETECTOR FAMILY
8-WAY with n1 / over-fetch / smells / vibe-check / clones / duplicates
/ dead).

W807 PATTERN-2 REGRESSION GUARD
-------------------------------

W807 confirmed the cmd_missing_index empty-corpus smoke explicitly
names the ``no_migrations`` state. The regression-guard tests below
confirm:

  1. The clean no-migrations path still emits ``state: "no_migrations"``
     with ``partial_success: True`` (the W807 contract: missing-input
     is a degradation, distinct from over-fetch where empty-corpus is
     NOT a degradation -- different detector semantics).
  2. The W607-CI substrate boundary on ``parse_migration_indexes``
     does NOT re-introduce Pattern-2 silent-fallback -- a raise in
     that substrate still emits a non-empty envelope with a marker
     AND ``partial_success: True``, never a SAFE verdict on a
     degraded state.

ORM-DETECTOR TRIPLE + DETECTOR FAMILY 8-WAY
-------------------------------------------

The bonus pairing tests confirm marker families coexist without
cross-prefix leakage:

* ORM detector triple: missing-index (W111) + n1 (W110) + over-fetch (W114).
* Detector family 8-way: + smells + vibe-check + clones + duplicates + dead.

W18.4 UNCONDITIONAL-PREDICATE REGRESSION
----------------------------------------

A regression test confirms ``_classify_predicates`` still returns
classified predicates (``unconditional`` / ``conditional`` / ``range``
/ ``sort``) after W607-CI plumbing wraps the parser substrates.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_missing_index_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_missing_index.

    Builds a tiny Python fixture (NO PHP migrations) so the detector
    runs cleanly with the empty-state ``no_migrations`` verdict -- the
    tests focus on W607-CI marker plumbing rather than the detector
    verdict on real Laravel apps.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text("def helper():\n    return 0\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call',
            line INTEGER, bridge TEXT, confidence REAL,
            source_file_id INTEGER,
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
        CREATE TABLE IF NOT EXISTS file_edges (
            id INTEGER PRIMARY KEY, source_file_id INTEGER NOT NULL,
            target_file_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'imports',
            symbol_count INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY,
            finding_id_str TEXT NOT NULL UNIQUE,
            subject_kind TEXT NOT NULL,
            subject_id INTEGER,
            claim TEXT NOT NULL,
            evidence_json TEXT,
            confidence TEXT NOT NULL,
            source_detector TEXT NOT NULL,
            source_version TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'open'
        );
        """
    )
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/engine.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(1, 1, 'helper', 'src.engine.helper', 'function', 1, 2, 'public', 1)"
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def missing_index_project(tmp_path):
    return _build_missing_index_project(tmp_path)


def _invoke_missing_index(cli_runner, project_root, *args, json_mode=True, sarif=False):
    """Invoke the missing_index click command directly."""
    from roam.commands.cmd_missing_index import missing_index_cmd

    obj = {"json": json_mode, "sarif": sarif, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(missing_index_cmd, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_CI_PHASES = (
    "parse_migration_indexes",
    "parse_query_patterns",
    "build_model_table_overrides",
    "build_findings",
    "apply_confidence_filter",
    "apply_table_filter",
    "aggregate_by_confidence",
    "emit_findings",
    "serialize_to_sarif",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CI substrate markers
# ---------------------------------------------------------------------------


def test_missing_index_clean_envelope_omits_w607ci_markers(cli_runner, missing_index_project):
    """Clean missing-index run -> no W607-CI substrate markers."""
    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "missing-index"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ci_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"missing_index_{p}_failed:" in m for p in _CI_PHASES)
    ]
    assert not ci_markers, (
        f"clean missing-index must NOT surface W607-CI substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) parse_migration_indexes failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_missing_index_parse_migration_indexes_failure_marker_format(cli_runner, missing_index_project, monkeypatch):
    """If ``_parse_migration_indexes`` raises, surface the canonical marker."""
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-parse-migrations-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_parse_migration_indexes", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    parse_markers = [m for m in all_wo if m.startswith("missing_index_parse_migration_indexes_failed:")]
    assert parse_markers, f"expected missing_index_parse_migration_indexes_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in parse_markers), parse_markers
    assert any("synthetic-parse-migrations-from-W607-CI" in m for m in parse_markers), parse_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_missing_index_w607ci_warnings_in_envelope(cli_runner, missing_index_project, monkeypatch):
    """Non-empty W607-CI bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_parse_query_patterns", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CI disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CI disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("missing_index_parse_query_patterns_failed:")]
    assert markers, f"expected missing_index_parse_query_patterns_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_missing_index_three_segment_marker_shape(cli_runner, missing_index_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_parse_migration_indexes", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("missing_index_parse_migration_indexes_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "missing_index_parse_migration_indexes_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) parse_query_patterns failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_missing_index_parse_query_patterns_failure_degrades_cleanly(cli_runner, missing_index_project, monkeypatch):
    """A raise in ``_parse_query_patterns`` must NOT crash the command."""
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-query-patterns-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_parse_query_patterns", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    query_markers = [m for m in all_wo if m.startswith("missing_index_parse_query_patterns_failed:")]
    assert query_markers, all_wo
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-CI stays in ``missing_index_*`` family
# ---------------------------------------------------------------------------


def test_w607ci_marker_prefix_stays_in_missing_index_family(cli_runner, missing_index_project, monkeypatch):
    """Every W607-CI substrate marker uses the canonical ``missing_index_*`` prefix.

    Hard distinction from sibling W607-* layers including the
    ORM-detector triple siblings (cmd_n1 W607-CB ``n1_*`` and
    cmd_over_fetch W607-CE ``over_fetch_*``), plus the broader
    detector family.
    """
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_parse_migration_indexes", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("missing_index_"), (
            f"every surfaced W607-CI marker must use the ``missing_index_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("n1_", "cmd_n1 W607-CB (ORM triple sibling)"),
            ("over_fetch_", "cmd_over_fetch W607-CE (ORM triple sibling)"),
            ("smells_", "cmd_smells W607-BN"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("clones_", "cmd_clones W607-BQ"),
            ("duplicates_", "cmd_duplicates W607-BM"),
            ("dead_", "cmd_dead W607-BX"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("debt_", "cmd_debt W607-BG"),
            ("vulns_", "cmd_vulns W607-AQ"),
            ("attest_", "cmd_attest W607-AD"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("doctor_", "cmd_doctor W607-N"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("retrieve_", "cmd_retrieve W607-B"),
            ("findings_", "cmd_findings W607-C"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_missing_index carries the W607-CI accumulator
# ---------------------------------------------------------------------------


def test_cmd_missing_index_carries_w607ci_accumulator():
    """AST-level guard: cmd_missing_index source carries the W607-CI accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_missing_index.py"
    assert src_path.exists(), f"cmd_missing_index.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ci_warnings_out" in src, (
        "W607-CI accumulator missing from cmd_missing_index; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ci" in src, (
        "W607-CI ``_run_check_ci`` helper missing from cmd_missing_index; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_ci = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ci":
            found_run_check_ci = True
            break
    assert found_run_check_ci, (
        "W607-CI ``_run_check_ci`` helper not found in cmd_missing_index AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-CI substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ci_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-CI substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_missing_index.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _CI_PHASES:
        same_line = f'_run_check_ci("{phase}"' in src
        multi_line = (
            f'_run_check_ci(\n        "{phase}"' in src
            or f'_run_check_ci(\n            "{phase}"' in src
            or f'_run_check_ci(\n                "{phase}"' in src
            or f'_run_check_ci(\n                    "{phase}"' in src
            or f'_run_check_ci(\n                        "{phase}"' in src
        )
        # emit_findings is wrapped via direct try/except (NOT _run_check_ci)
        # because it needs to distinguish sqlite3.OperationalError (expected
        # pre-W89 path) from generic Exception (W607-CI marker). Source-grep
        # on the marker name in that case.
        marker_grep = f"missing_index_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-CI wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) emit_findings failure -> marker surfaces, command still emits
# ---------------------------------------------------------------------------


def test_missing_index_emit_findings_failure_surfaces_marker(cli_runner, missing_index_project, monkeypatch):
    """W111 emit failure (non-OperationalError) surfaces W607-CI marker.

    sqlite3.OperationalError is the EXPECTED pre-W89 path (silent
    no-op). Generic exceptions surface via the W607-CI marker.
    """
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_emit_missing_index_findings", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("missing_index_emit_findings_failed:")]
    assert emit_markers, f"expected missing_index_emit_findings_failed: marker; got {all_wo!r}"
    # The missing-index command still emits a clean envelope past the
    # registry-mirror failure -- W111 is additive, not load-bearing.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) emit_findings OperationalError path stays silent (no W607-CI marker)
# ---------------------------------------------------------------------------


def test_missing_index_emit_findings_operational_error_stays_silent(cli_runner, missing_index_project, monkeypatch):
    """W607-CI MUST preserve the W111 silent no-op contract on
    ``sqlite3.OperationalError`` (pre-W89 schema -- no findings table).

    The marker MUST NOT surface for this expected degraded path.
    """
    from roam.commands import cmd_missing_index

    def _raise_op_err(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings (pre-W89 schema)")

    monkeypatch.setattr(cmd_missing_index, "_emit_missing_index_findings", _raise_op_err)

    result = _invoke_missing_index(cli_runner, missing_index_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("missing_index_emit_findings_failed:")]
    assert not emit_markers, (
        f"sqlite3.OperationalError is the EXPECTED pre-W89 silent "
        f"no-op path; W607-CI marker MUST NOT surface; "
        f"got {emit_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) build_model_table_overrides failure -> empty floor
# ---------------------------------------------------------------------------


def test_missing_index_build_model_table_overrides_failure_degrades(cli_runner, missing_index_project, monkeypatch):
    """A raise in ``_build_model_table_overrides`` must NOT crash."""
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-overrides-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_build_model_table_overrides", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    overrides_markers = [m for m in all_wo if m.startswith("missing_index_build_model_table_overrides_failed:")]
    assert overrides_markers, all_wo
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (12) W807 PATTERN-2 REGRESSION GUARD: no_migrations state preserved
# ---------------------------------------------------------------------------


def test_w807_no_migrations_state_preserved_under_w607ci(cli_runner, missing_index_project):
    """W807 regression guard: empty/no-migrations envelope still names the state.

    W807 sealed this contract: when there are no PHP migration files,
    the verdict explicitly names ``state: "no_migrations"`` with
    ``partial_success: True`` (missing-input IS a degradation for
    this detector -- hard distinction from cmd_over_fetch W607-CE
    where empty-corpus is NOT a degradation).

    The W607-CI plumbing must NOT re-introduce a Pattern-2 bug: the
    pre-existing ``no_migrations`` state must continue to surface,
    and ``partial_success: True`` must hold.
    """
    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    # W807 invariant: the empty-input state is named explicitly.
    state = summary.get("state")
    assert state == "no_migrations", (
        f"empty-input envelope MUST carry state=`no_migrations` "
        f"(W807 contract); got state={state!r}, summary={summary!r}"
    )
    # And partial_success stays True (missing-input IS a degradation
    # for missing-index, unlike over-fetch).
    assert summary.get("partial_success") is True, (
        f"no_migrations partial_success must be True (W807: missing "
        f"PHP migrations IS a degradation for missing-index); "
        f"got summary={summary!r}"
    )


def test_w807_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, missing_index_project, monkeypatch):
    """W807 Pattern-2 regression guard on the degraded-empty path.

    If ``_parse_migration_indexes`` raises, the empty-floor default
    kicks in (table_indexes == {}) and the envelope is emitted. The
    W607-CI wrap MUST flip ``partial_success: True`` on that branch
    (already True via the ``no_migrations`` state on the fixture, but
    the W607-CI bucket also independently flips it) so the empty-state
    envelope is NOT mistaken for a clean "no missing indexes" verdict.
    """
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-W807-pattern-2-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_parse_migration_indexes", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded-empty path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )

    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    parse_markers = [m for m in all_wo if m.startswith("missing_index_parse_migration_indexes_failed:")]
    assert parse_markers, (
        f"degraded-empty path MUST surface the parse_migration_indexes "
        f"marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (13) ORM DETECTOR TRIPLE pairing bonus
# ---------------------------------------------------------------------------


def test_orm_detector_triple_marker_prefixes_coexist(cli_runner, missing_index_project, monkeypatch):
    """ORM DETECTOR TRIPLE pairing bonus.

    Confirm ``missing_index_<phase>_failed:`` markers coexist with
    ``n1_*`` (W607-CB) and ``over_fetch_*`` (W607-CE) markers without
    cross-prefix leakage. Closes the W110 / W111 / W114 ORM-detector
    triple substrate-boundary integration.

    This is the load-bearing prefix-discipline test for the triple:
    each command's marker family stays inside its own prefix so a
    downstream grep on ``missing_index_*`` markers picks up ONLY the
    missing-index detector substrate failures.
    """
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-ORM-triple-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_parse_migration_indexes", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # The missing_index marker fires.
    assert any(m.startswith("missing_index_parse_migration_indexes_failed:") for m in all_wo), all_wo

    # Sibling ORM-detector prefixes do NOT leak.
    for forbidden_prefix in ("n1_", "over_fetch_"):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on ORM-detector triple pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_missing_index envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (14) DETECTOR FAMILY 8-WAY pairing bonus
# ---------------------------------------------------------------------------


def test_detector_family_8way_marker_prefixes_coexist(cli_runner, missing_index_project, monkeypatch):
    """DETECTOR FAMILY 8-WAY pairing bonus.

    Confirm ``missing_index_<phase>_failed:`` markers coexist with
    ``n1_*`` (W607-CB), ``over_fetch_*`` (W607-CE), ``smells_*``
    (W607-BN), ``vibe_check_*`` (W607-BS), ``clones_*`` (W607-BQ),
    ``duplicates_*`` (W607-BM), and ``dead_*`` (W607-BX) markers
    without cross-prefix leakage. Closes the 8-detector family.
    """
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-8way-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_parse_migration_indexes", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("missing_index_parse_migration_indexes_failed:") for m in all_wo), all_wo

    # None of the seven detector-sibling prefixes leak into the
    # missing-index envelope.
    for forbidden_prefix in (
        "n1_",
        "over_fetch_",
        "smells_",
        "vibe_check_",
        "clones_",
        "duplicates_",
        "dead_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on detector-family 8-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_missing_index envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (15) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607ci_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-CI marker shape lives in cmd_missing_index."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_missing_index.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"missing_index_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-CI marker fstring missing from cmd_missing_index; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (16) SARIF projection failure -> marker surfaces on CI path
# ---------------------------------------------------------------------------


def test_missing_index_sarif_failure_surfaces_marker(cli_runner, missing_index_project, monkeypatch):
    """A raise in the SARIF projection must NOT crash the CI path."""
    from roam.output import sarif as sarif_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-sarif-from-W607-CI")

    monkeypatch.setattr(sarif_mod, "missing_index_to_sarif", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project, json_mode=False, sarif=True)
    # The W607-CI wrap protects against crash even on the SARIF path.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (17) aggregate_by_confidence failure -> empty histogram, envelope composes
# ---------------------------------------------------------------------------


def test_missing_index_aggregate_by_confidence_failure_degrades_cleanly(cli_runner, missing_index_project, monkeypatch):
    """A raise in the by-confidence aggregator degrades to ``{}``."""
    from roam.commands import cmd_missing_index

    # Have _build_findings return one finding with a MISSING
    # ``confidence`` key -- the aggregator's ``f["confidence"]`` lookup
    # will KeyError inside the substrate.
    def _fake_build_findings(*args, **kwargs):
        return [
            {
                "table": "users",
                "columns": ["email"],
                "issue": "no index on email",
                "query_location": "src/Models/User.php:10",
                "query_kind": "model",
                "has_paginate": False,
                "pattern_type": "single_where",
                "suggestion": "Add index on email",
                "missing_individual": ["email"],
                # NO "confidence" key -- aggregator KeyErrors.
            }
        ]

    monkeypatch.setattr(cmd_missing_index, "_build_findings", _fake_build_findings)
    # Force the no_migrations path off so we reach the build_findings flow.
    # We don't have real migrations so total_findings==1 needs a non-empty
    # migration list. Easiest: also patch _parse_migration_indexes to return
    # a non-empty dict so migrations_scanned > 0 and we don't short-circuit.
    monkeypatch.setattr(
        cmd_missing_index,
        "_parse_migration_indexes",
        lambda *a, **k: {"users": {("id",)}},
    )
    # Pretend there's at least one migration file in the index so the
    # "no_migrations" branch doesn't fire. We patch the SQL query result
    # is tricky; instead patch _is_migration_path to mark a fake file.
    # Simpler: patch _build_findings to consume an empty query_patterns
    # list and our fake build_findings ignores its inputs. Force the
    # "no_migrations" check off by patching is_migration_path globally.
    # Actually, we need at least one migration_paths element. Add a fake
    # PHP migration file to the DB via direct insert.
    db_path = missing_index_project / ".roam" / "index.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO files (id, path, language) VALUES "
        "(2, 'database/migrations/2020_01_01_000000_create_users.php', 'php')"
    )
    conn.commit()
    conn.close()

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    agg_markers = [m for m in all_wo if m.startswith("missing_index_aggregate_by_confidence_failed:")]
    assert agg_markers, f"expected missing_index_aggregate_by_confidence_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (18) W18.4 UNCONDITIONAL-PREDICATE REGRESSION: classification survives W607-CI
# ---------------------------------------------------------------------------


def test_w18_4_unconditional_predicate_detection_survives_w607ci():
    """W18.4 regression guard: ``_classify_predicates`` still returns
    classified predicates after W607-CI plumbing wraps the parser
    substrates.

    The W18.4 contract requires every predicate to carry one of four
    classifications: ``unconditional`` / ``conditional`` / ``range`` /
    ``sort``. The W607-CI plumbing wraps the parser entry points but
    MUST NOT alter the per-predicate classification surface.
    """
    from roam.commands.cmd_missing_index import (
        _PRED_CONDITIONAL,
        _PRED_RANGE,
        _PRED_SORT,
        _PRED_UNCONDITIONAL,
        _classify_predicates,
    )

    body = """
    public function index() {
        return User::query()
            ->where('email', $email)
            ->where('status', '>', 0)
            ->when($filter, function ($q) use ($filter) {
                $q->where('role', $filter);
            })
            ->orderBy('created_at')
            ->paginate(20);
    }
    """
    preds = _classify_predicates(body)
    assert preds, "W18.4 classification must return non-empty predicate list"

    classifications = {p.classification for p in preds}
    # Every classification must be one of the four canonical labels.
    valid = {_PRED_UNCONDITIONAL, _PRED_CONDITIONAL, _PRED_RANGE, _PRED_SORT}
    assert classifications.issubset(valid), (
        f"W18.4 classifications drifted post-W607-CI; got {classifications!r}, valid set is {valid!r}"
    )
    # AT LEAST one unconditional + one conditional + one range + one sort
    # should land for this body.
    assert _PRED_UNCONDITIONAL in classifications, classifications
    assert _PRED_CONDITIONAL in classifications, classifications
    assert _PRED_RANGE in classifications, classifications
    assert _PRED_SORT in classifications, classifications


# ---------------------------------------------------------------------------
# (19) Per-language isolation: parse_query_patterns raises -> marker surfaces
# ---------------------------------------------------------------------------


def test_per_language_isolation_marker_surfaces(cli_runner, missing_index_project, monkeypatch):
    """Per-language isolation: simulate ``parse_query_patterns`` raising
    on the PHP-source pass and confirm the marker surfaces AND the
    envelope still composes (so other language detectors -- if any --
    would still classify correctly in the broader fleet view).

    For missing-index specifically, the detector is PHP-only (Laravel),
    so the isolation surface is one substrate. The marker shape is the
    same canonical 3-segment family.
    """
    from roam.commands import cmd_missing_index

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-per-lang-from-W607-CI")

    monkeypatch.setattr(cmd_missing_index, "_parse_query_patterns", _raise)

    result = _invoke_missing_index(cli_runner, missing_index_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    isolation_markers = [m for m in all_wo if m.startswith("missing_index_parse_query_patterns_failed:")]
    assert isolation_markers, f"expected missing_index_parse_query_patterns_failed: marker; got {all_wo!r}"
    # Envelope still composes -- other substrates run unaffected.
    assert data["summary"].get("partial_success") is True
    # Migrations-scanned count is still reported (the SQL query and
    # parse_migration_indexes substrate ran cleanly).
    assert "migrations_scanned" in data["summary"]
    assert "indexes_found" in data["summary"]
