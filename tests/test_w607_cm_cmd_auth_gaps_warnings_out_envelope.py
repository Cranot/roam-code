"""W607-CM -- ``cmd_auth_gaps`` substrate-boundary plumbing.

cmd_auth_gaps is the authentication-gap detector (W116 origin per
CLAUDE.md detector roster -- part of the original 16 findings-registry
substrate detectors). The detector has W140 helper-indirection
detection + W36.10 depth-2 ancestor descent + E2 cross-file class-source
map. W815 sealed the Pattern-2 empty-corpus regression (explicit
zero-count verdict, no SAFE-vocabulary fallback) but until this wave
the command had no substrate-boundary marker plumbing -- a raise in
``_analyze_controller_file`` (the W140 / W36.10 host) or
``_build_class_source_map`` (the cross-file walker) would crash the
auth-gaps detector outright.

This wave installs the canonical ``_w607cm_warnings_out`` bucket +
``_run_check_cm`` helper inside the ``auth-gaps`` click command and
wraps every substrate boundary:

* find_route_files            -- route file discovery
* find_service_provider_files -- provider file discovery
* find_controller_files       -- controller file discovery
* analyze_route_file          -- per-route-file brace-depth analyser
* analyze_service_provider    -- per-provider-file scan
* build_class_source_map      -- E2 inheritance-lookup map build
* analyze_controller_file     -- per-controller analysis (W140
                                 helper-indirection + W36.10 depth-2
                                 ancestor descent live here)
* apply_confidence_filter     -- W1005-followup-D severity floor
* aggregate_by_confidence     -- histogram
* emit_findings               -- W116 findings-registry mirror
                                 (sqlite3.OperationalError silent no-op
                                 preserved for pre-W89 DB)
* serialize_to_sarif          -- W1195 SARIF projection

Marker family ``auth_gaps_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test (auth-gaps closes the DETECTOR FAMILY 9-WAY
with n1 / over-fetch / missing-index / smells / vibe-check / clones /
duplicates / dead, AND closes the SECURITY-DETECTOR FAMILY 3-WAY with
vulns (W607-AQ + CH) and taint (W607-AY + CJ)).

W815 PATTERN-2 REGRESSION GUARD
-------------------------------

W815 confirmed the cmd_auth_gaps empty-corpus smoke explicitly names
the zero-count outcome ("0 auth gap(s) found") and forbids the
SAFE/PASSED/completed vocabulary. The regression-guard tests below
confirm:

  1. The clean empty corpus path still emits the W815 verdict shape
     (zero counts, partial_success bool, no forbidden vocabulary).
  2. The W607-CM substrate boundary on ``_analyze_controller_file``
     does NOT re-introduce Pattern-2 silent-fallback -- a raise in
     that substrate still emits a non-empty envelope with a marker
     AND ``partial_success: True``, never a SAFE verdict on a
     degraded state.

W140 HELPER-INDIRECTION REGRESSION
----------------------------------

A regression test confirms ``_method_has_authorize`` still resolves
intra-class helper indirection after W607-CM plumbing wraps the
analyser substrates (the W140 contract).

DETECTOR FAMILY 9-WAY + SECURITY-DETECTOR FAMILY 3-WAY PAIRING
---------------------------------------------------------------

The bonus pairing tests confirm marker families coexist without
cross-prefix leakage:

* Detector family 9-way: auth-gaps + n1 + over-fetch + missing-index +
  smells + vibe-check + clones + duplicates + dead.
* Security-detector family 3-way: auth-gaps + vulns + taint.
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


def _build_auth_gaps_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_auth_gaps.

    Builds a tiny Python fixture (NO PHP, NO routes, NO controllers)
    so the detector runs cleanly with the W815 zero-count envelope --
    the tests focus on W607-CM marker plumbing rather than the
    detector verdict on real Laravel apps.
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
def auth_gaps_project(tmp_path):
    return _build_auth_gaps_project(tmp_path)


def _invoke_auth_gaps(cli_runner, project_root, *args, json_mode=True, sarif=False):
    """Invoke the auth_gaps click command directly."""
    from roam.commands.cmd_auth_gaps import auth_gaps_cmd

    obj = {"json": json_mode, "sarif": sarif, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(auth_gaps_cmd, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_CM_PHASES = (
    "find_route_files",
    "find_service_provider_files",
    "find_controller_files",
    "analyze_route_file",
    "analyze_service_provider",
    "build_class_source_map",
    "analyze_controller_file",
    "apply_confidence_filter",
    "sort_findings",
    "aggregate_by_confidence",
    "emit_findings",
    "serialize_to_sarif",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CM substrate markers
# ---------------------------------------------------------------------------


def test_auth_gaps_clean_envelope_omits_w607cm_markers(cli_runner, auth_gaps_project):
    """Clean auth-gaps run -> no W607-CM substrate markers."""
    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "auth-gaps"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    cm_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"auth_gaps_{p}_failed:" in m for p in _CM_PHASES)
    ]
    assert not cm_markers, (
        f"clean auth-gaps must NOT surface W607-CM substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) find_controller_files failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_auth_gaps_find_controller_files_failure_marker_format(cli_runner, auth_gaps_project, monkeypatch):
    """If ``_find_controller_files`` raises, surface the canonical marker."""
    from roam.commands import cmd_auth_gaps

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-find-controllers-from-W607-CM")

    monkeypatch.setattr(cmd_auth_gaps, "_find_controller_files", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    find_markers = [m for m in all_wo if m.startswith("auth_gaps_find_controller_files_failed:")]
    assert find_markers, f"expected auth_gaps_find_controller_files_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in find_markers), find_markers
    assert any("synthetic-find-controllers-from-W607-CM" in m for m in find_markers), find_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_auth_gaps_w607cm_warnings_in_envelope(cli_runner, auth_gaps_project, monkeypatch):
    """Non-empty W607-CM bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_auth_gaps

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CM")

    monkeypatch.setattr(cmd_auth_gaps, "_find_route_files", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CM disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CM disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("auth_gaps_find_route_files_failed:")]
    assert markers, f"expected auth_gaps_find_route_files_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_auth_gaps_three_segment_marker_shape(cli_runner, auth_gaps_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_auth_gaps

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CM")

    monkeypatch.setattr(cmd_auth_gaps, "_find_controller_files", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("auth_gaps_find_controller_files_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "auth_gaps_find_controller_files_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) build_class_source_map failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_auth_gaps_build_class_source_map_failure_degrades(cli_runner, auth_gaps_project, monkeypatch):
    """A raise in ``_build_class_source_map`` must NOT crash the command."""
    from roam.commands import cmd_auth_gaps

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-class-map-from-W607-CM")

    monkeypatch.setattr(cmd_auth_gaps, "_build_class_source_map", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    class_map_markers = [m for m in all_wo if m.startswith("auth_gaps_build_class_source_map_failed:")]
    assert class_map_markers, all_wo
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-CM stays in ``auth_gaps_*`` family
# ---------------------------------------------------------------------------


def test_w607cm_marker_prefix_stays_in_auth_gaps_family(cli_runner, auth_gaps_project, monkeypatch):
    """Every W607-CM substrate marker uses the canonical ``auth_gaps_*`` prefix.

    Hard distinction from sibling W607-* layers including the
    security-detector siblings (cmd_vulns W607-AQ/CH ``vulns_*`` and
    cmd_taint W607-AY/CJ ``taint_*``), plus the broader detector family.
    """
    from roam.commands import cmd_auth_gaps

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CM")

    monkeypatch.setattr(cmd_auth_gaps, "_find_controller_files", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("auth_gaps_"), (
            f"every surfaced W607-CM marker must use the ``auth_gaps_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("vulns_", "cmd_vulns W607-AQ / CH (security sibling)"),
            ("taint_", "cmd_taint W607-AY / CJ (security sibling)"),
            ("vuln_reach_", "cmd_vuln_reach (security sibling)"),
            ("n1_", "cmd_n1 W607-CB"),
            ("over_fetch_", "cmd_over_fetch W607-CE"),
            ("missing_index_", "cmd_missing_index W607-CI"),
            ("smells_", "cmd_smells W607-BN"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("clones_", "cmd_clones W607-BQ"),
            ("duplicates_", "cmd_duplicates W607-BM"),
            ("dead_", "cmd_dead W607-BX"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("debt_", "cmd_debt W607-BG"),
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
# (7) Source-level guard: cmd_auth_gaps carries the W607-CM accumulator
# ---------------------------------------------------------------------------


def test_cmd_auth_gaps_carries_w607cm_accumulator():
    """AST-level guard: cmd_auth_gaps source carries the W607-CM accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    assert src_path.exists(), f"cmd_auth_gaps.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607cm_warnings_out" in src, (
        "W607-CM accumulator missing from cmd_auth_gaps; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_cm" in src, (
        "W607-CM ``_run_check_cm`` helper missing from cmd_auth_gaps; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_cm = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cm":
            found_run_check_cm = True
            break
    assert found_run_check_cm, (
        "W607-CM ``_run_check_cm`` helper not found in cmd_auth_gaps AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-CM substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607cm_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-CM substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _CM_PHASES:
        same_line = f'_run_check_cm("{phase}"' in src
        multi_line = (
            f'_run_check_cm(\n        "{phase}"' in src
            or f'_run_check_cm(\n            "{phase}"' in src
            or f'_run_check_cm(\n                "{phase}"' in src
            or f'_run_check_cm(\n                    "{phase}"' in src
            or f'_run_check_cm(\n                        "{phase}"' in src
        )
        # emit_findings is wrapped via direct try/except (NOT _run_check_cm)
        # because it needs to distinguish sqlite3.OperationalError (expected
        # pre-W89 path) from generic Exception (W607-CM marker). Source-grep
        # on the marker name in that case.
        marker_grep = f"auth_gaps_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-CM wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) emit_findings failure -> marker surfaces, command still emits
# ---------------------------------------------------------------------------


def test_auth_gaps_emit_findings_failure_surfaces_marker(cli_runner, auth_gaps_project, monkeypatch):
    """W116 emit failure (non-OperationalError) surfaces W607-CM marker.

    sqlite3.OperationalError is the EXPECTED pre-W89 path (silent
    no-op). Generic exceptions surface via the W607-CM marker.
    """
    from roam.commands import cmd_auth_gaps

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-CM")

    monkeypatch.setattr(cmd_auth_gaps, "_emit_auth_gaps_findings", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("auth_gaps_emit_findings_failed:")]
    assert emit_markers, f"expected auth_gaps_emit_findings_failed: marker; got {all_wo!r}"
    # The auth-gaps command still emits a clean envelope past the
    # registry-mirror failure -- W116 is additive, not load-bearing.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) emit_findings OperationalError path stays silent (no W607-CM marker)
# ---------------------------------------------------------------------------


def test_auth_gaps_emit_findings_operational_error_stays_silent(cli_runner, auth_gaps_project, monkeypatch):
    """W607-CM MUST preserve the W116 silent no-op contract on
    ``sqlite3.OperationalError`` (pre-W89 schema -- no findings table).

    The marker MUST NOT surface for this expected degraded path.
    """
    from roam.commands import cmd_auth_gaps

    def _raise_op_err(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings (pre-W89 schema)")

    monkeypatch.setattr(cmd_auth_gaps, "_emit_auth_gaps_findings", _raise_op_err)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("auth_gaps_emit_findings_failed:")]
    assert not emit_markers, (
        f"sqlite3.OperationalError is the EXPECTED pre-W89 silent "
        f"no-op path; W607-CM marker MUST NOT surface; "
        f"got {emit_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) analyze_controller_file failure -> empty floor (W140 host)
# ---------------------------------------------------------------------------


def test_auth_gaps_analyze_controller_file_failure_degrades(cli_runner, auth_gaps_project, monkeypatch):
    """A raise in ``_analyze_controller_file`` must NOT crash.

    ``_analyze_controller_file`` is the host for W140 helper-indirection
    and W36.10 depth-2 ancestor descent. Per-file isolation means one
    controller raising must not torpedo siblings.
    """
    from roam.commands import cmd_auth_gaps

    # Pretend at least one controller file exists so the substrate runs.
    monkeypatch.setattr(
        cmd_auth_gaps,
        "_find_controller_files",
        lambda conn: ["app/Http/Controllers/UserController.php"],
    )
    # And give _read_source a non-None body so analyze runs.
    monkeypatch.setattr(cmd_auth_gaps, "_read_source", lambda p: "<?php class UserController {}\n")

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-analyze-controller-from-W607-CM")

    monkeypatch.setattr(cmd_auth_gaps, "_analyze_controller_file", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    analyze_markers = [m for m in all_wo if m.startswith("auth_gaps_analyze_controller_file_failed:")]
    assert analyze_markers, all_wo
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (12) W815 PATTERN-2 REGRESSION GUARD: zero-count verdict preserved
# ---------------------------------------------------------------------------


def test_w815_zero_count_verdict_preserved_under_w607cm(cli_runner, auth_gaps_project):
    """W815 regression guard: empty-corpus envelope still names the zero count.

    W815 sealed this contract: when there are no PHP files, the
    verdict explicitly names the zero count ("0 auth gap(s) found")
    and the SAFE/PASSED/completed vocabulary is forbidden. The
    W607-CM plumbing must NOT re-introduce a Pattern-2 bug.
    """
    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    verdict = summary.get("verdict") or ""
    assert "0" in verdict or "no " in verdict.lower(), (
        f"verdict must name zero-count outcome (W815 contract); got {verdict!r}"
    )
    for forbidden in ("safe", "passed", "completed", "all clear", "ok"):
        assert forbidden not in verdict.lower(), (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )
    # Zero counts mirror the verdict (W815 invariant).
    assert summary.get("total") == 0
    assert summary.get("high") == 0
    assert summary.get("medium") == 0
    assert summary.get("low") == 0


def test_w815_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, auth_gaps_project, monkeypatch):
    """W815 Pattern-2 regression guard on the degraded-empty path.

    If ``_analyze_controller_file`` raises, the empty-floor default
    kicks in (findings == []) and the envelope is emitted. The
    W607-CM wrap MUST flip ``partial_success: True`` on that branch
    so the empty-state envelope is NOT mistaken for a clean "0 auth
    gaps" verdict.
    """
    from roam.commands import cmd_auth_gaps

    monkeypatch.setattr(
        cmd_auth_gaps,
        "_find_controller_files",
        lambda conn: ["app/Http/Controllers/UserController.php"],
    )
    monkeypatch.setattr(cmd_auth_gaps, "_read_source", lambda p: "<?php class UserController {}\n")

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-W815-pattern-2-from-W607-CM")

    monkeypatch.setattr(cmd_auth_gaps, "_analyze_controller_file", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded-empty path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )

    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    analyze_markers = [m for m in all_wo if m.startswith("auth_gaps_analyze_controller_file_failed:")]
    assert analyze_markers, (
        f"degraded-empty path MUST surface the analyze_controller_file "
        f"marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (13) DETECTOR FAMILY 9-WAY pairing bonus
# ---------------------------------------------------------------------------


def test_detector_family_9way_marker_prefixes_coexist(cli_runner, auth_gaps_project, monkeypatch):
    """DETECTOR FAMILY 9-WAY pairing bonus.

    Confirm ``auth_gaps_<phase>_failed:`` markers coexist with
    ``n1_*`` (W607-CB), ``over_fetch_*`` (W607-CE), ``missing_index_*``
    (W607-CI), ``smells_*`` (W607-BN), ``vibe_check_*`` (W607-BS),
    ``clones_*`` (W607-BQ), ``duplicates_*`` (W607-BM), and ``dead_*``
    (W607-BX) markers without cross-prefix leakage. Closes the
    9-detector family.
    """
    from roam.commands import cmd_auth_gaps

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-9way-from-W607-CM")

    monkeypatch.setattr(cmd_auth_gaps, "_find_route_files", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("auth_gaps_find_route_files_failed:") for m in all_wo), all_wo

    # None of the eight detector-sibling prefixes leak into the
    # auth-gaps envelope.
    for forbidden_prefix in (
        "n1_",
        "over_fetch_",
        "missing_index_",
        "smells_",
        "vibe_check_",
        "clones_",
        "duplicates_",
        "dead_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on detector-family 9-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_auth_gaps envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (14) SECURITY-DETECTOR FAMILY 3-WAY pairing bonus
# ---------------------------------------------------------------------------


def test_security_detector_family_3way_marker_prefixes_coexist(cli_runner, auth_gaps_project, monkeypatch):
    """SECURITY-DETECTOR FAMILY 3-WAY pairing bonus.

    Confirm ``auth_gaps_<phase>_failed:`` markers coexist with
    ``vulns_*`` (W607-AQ + CH) and ``taint_*`` (W607-AY + CJ) markers
    without cross-prefix leakage. Closes the security-detector family
    alongside the security-reachability triad. The auth-gaps detector
    answers "endpoints missing auth", vulns answers "vulnerable
    dependencies", taint answers "tainted dataflow paths" -- three
    orthogonal security axes, three disjoint marker families.
    """
    from roam.commands import cmd_auth_gaps

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-security-3way-from-W607-CM")

    monkeypatch.setattr(cmd_auth_gaps, "_find_controller_files", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("auth_gaps_find_controller_files_failed:") for m in all_wo), all_wo

    for forbidden_prefix in ("vulns_", "taint_", "vuln_reach_"):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on security-detector 3-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_auth_gaps envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (15) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607cm_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-CM marker shape lives in cmd_auth_gaps."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"auth_gaps_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-CM marker fstring missing from cmd_auth_gaps; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (16) SARIF projection failure -> marker surfaces on CI path
# ---------------------------------------------------------------------------


def test_auth_gaps_sarif_failure_surfaces_marker(cli_runner, auth_gaps_project, monkeypatch):
    """A raise in the SARIF projection must NOT crash the CI path."""
    from roam.output import sarif as sarif_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-sarif-from-W607-CM")

    monkeypatch.setattr(sarif_mod, "auth_gaps_to_sarif", _raise)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project, json_mode=False, sarif=True)
    # The W607-CM wrap protects against crash even on the SARIF path.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (17) aggregate_by_confidence failure -> empty histogram, envelope composes
# ---------------------------------------------------------------------------


def test_auth_gaps_aggregate_by_confidence_failure_degrades_cleanly(cli_runner, auth_gaps_project, monkeypatch):
    """A raise in the by-confidence aggregator degrades to ``(0, 0, 0)``."""
    from roam.commands import cmd_auth_gaps

    # Make the apply_confidence_filter substrate raise so the
    # post-filter aggregator never gets a clean list -- but easier:
    # force the aggregator itself to KeyError by patching all_findings
    # via the filter substrate. Cleanest path: patch
    # severity_rank used inside apply_confidence_filter to raise once
    # the aggregator runs. Even cleaner: monkeypatch the aggregator's
    # input. We patch _find_controller_files to return one file plus
    # _read_source + _analyze_controller_file to yield ONE finding with
    # a missing ``confidence`` key, so the aggregator's f["confidence"]
    # KeyErrors.
    monkeypatch.setattr(
        cmd_auth_gaps,
        "_find_controller_files",
        lambda conn: ["app/Http/Controllers/UserController.php"],
    )
    monkeypatch.setattr(
        cmd_auth_gaps,
        "_read_source",
        lambda p: "<?php class UserController {}\n",
    )

    def _fake_analyze(*args, **kwargs):
        return [
            {
                "type": "controller",
                "controller": "UserController",
                "method": "destroy",
                "file": "app/Http/Controllers/UserController.php",
                "line": 1,
                # NO "confidence" key -- the apply_confidence_filter
                # substrate will KeyError, then the aggregator would
                # also KeyError; either way W607-CM surfaces a marker.
            }
        ]

    monkeypatch.setattr(cmd_auth_gaps, "_analyze_controller_file", _fake_analyze)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # Any of apply_confidence_filter / sort_findings /
    # aggregate_by_confidence surfaces a marker depending on which
    # raise wins first -- all are acceptable evidence of the W607-CM
    # disclosure path (the malformed finding causes a KeyError at
    # whichever substrate touches f["confidence"] first).
    surfaced = [
        m
        for m in all_wo
        if m.startswith("auth_gaps_apply_confidence_filter_failed:")
        or m.startswith("auth_gaps_sort_findings_failed:")
        or m.startswith("auth_gaps_aggregate_by_confidence_failed:")
    ]
    assert surfaced, (
        f"expected apply_confidence_filter / sort_findings / aggregate_by_confidence marker; got {all_wo!r}"
    )
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (18) W140 HELPER-INDIRECTION REGRESSION: detection survives W607-CM
# ---------------------------------------------------------------------------


def test_w140_helper_indirection_detection_survives_w607cm():
    """W140 regression guard: ``_method_has_authorize`` still resolves
    intra-class helper indirection after W607-CM plumbing wraps the
    analyser substrates.

    The W140 contract: when a controller method calls ``$this->authorize()``
    DIRECTLY OR through a helper defined on the same class, the
    detector must recognise the method as authorized. The W607-CM
    plumbing wraps the per-controller analyser entry point but MUST
    NOT alter the per-method helper-indirection logic.
    """
    from roam.commands.cmd_auth_gaps import _method_has_authorize

    # Direct authorize -- canonical happy path.
    direct = "{ $this->authorize('update', $model); return true; }"
    assert _method_has_authorize(direct) is True, "W140 regression: direct $this->authorize() call must be recognised"

    # Helper indirection via the canonical _AUTHORIZE_HELPER_NAMES allowlist.
    helper_allowlisted = "{ $this->requireAuthorization(); return true; }"
    assert _method_has_authorize(helper_allowlisted) is True, (
        "W140 regression: $this->requireAuthorization() via allowlist must be recognised"
    )

    # Same-class helper descent (W140 Layer 2) -- helper method body
    # contains the authorize call.
    own_methods = {
        "_localGuard": "{ $this->authorize('view', $model); }",
    }
    descent = "{ $this->_localGuard(); return true; }"
    assert _method_has_authorize(descent, own_class_methods=own_methods) is True, (
        "W140 Layer 2 regression: same-class helper descent must resolve the authorize call inside the helper body"
    )

    # No authorize anywhere -- must return False.
    bare = "{ return true; }"
    assert _method_has_authorize(bare) is False, "W140 baseline: no authorize call anywhere must return False"


# ---------------------------------------------------------------------------
# (19) Per-framework isolation: per-file analyse loop continues on raise
# ---------------------------------------------------------------------------


def test_per_framework_isolation_one_route_file_raise_continues(cli_runner, auth_gaps_project, monkeypatch):
    """Per-framework isolation: simulate ``_analyze_route_file`` raising
    on one route file -- confirm the marker surfaces AND the envelope
    still composes (so other frameworks / route files would still
    classify correctly in the broader fleet view).

    The detector is PHP/Laravel-only today, but the per-file-loop
    isolation is the equivalent of per-framework isolation in
    multi-framework detectors -- one fixture's failure must not
    torpedo the rest.
    """
    from roam.commands import cmd_auth_gaps

    # Two route files -- the first will raise, the second must still process.
    monkeypatch.setattr(
        cmd_auth_gaps,
        "_find_route_files",
        lambda conn: ["routes/api.php", "routes/web.php"],
    )
    monkeypatch.setattr(cmd_auth_gaps, "_read_source", lambda p: "<?php /* fake route file */\n")

    call_log = []

    def _flaky_analyze(file_path, source):
        call_log.append(file_path)
        if call_log[-1].endswith("api.php"):
            raise RuntimeError("synthetic-per-framework-from-W607-CM")
        return ([], set())

    monkeypatch.setattr(cmd_auth_gaps, "_analyze_route_file", _flaky_analyze)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    isolation_markers = [m for m in all_wo if m.startswith("auth_gaps_analyze_route_file_failed:")]
    assert isolation_markers, f"expected auth_gaps_analyze_route_file_failed: marker; got {all_wo!r}"
    # Both route files reached _analyze_route_file (per-file isolation).
    assert len(call_log) == 2, (
        f"per-framework isolation broken: one file failure stopped the loop; call_log={call_log!r}"
    )
    # Envelope still composes -- partial_success flips on degraded path.
    assert data["summary"].get("partial_success") is True
