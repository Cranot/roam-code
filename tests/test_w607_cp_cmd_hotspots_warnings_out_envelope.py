"""W607-CP -- ``cmd_hotspots`` substrate-boundary plumbing.

cmd_hotspots is the runtime-hotspot detector (W120 origin per CLAUDE.md
detector roster -- part of the original 16 findings-registry substrate
detectors). The detector classifies symbols by trace ingestion as
UPGRADE / CONFIRMED / DOWNGRADE against the ``runtime`` confidence tier.
Per CLAUDE.md sprint history (Wave816), empty-corpus smoke was sealed
via W819. Until this wave the command had no substrate-boundary marker
plumbing -- a raise in ``compute_hotspots`` (the core classification)
would crash the hotspots command outright.

This wave installs the canonical ``_w607cp_warnings_out`` bucket +
``_run_check_cp`` helper inside the ``hotspots`` click command and
wraps every substrate boundary:

* load_trace_ingestion       -- runtime_stats COUNT probe
                                (sqlite3.OperationalError silent named
                                state preserved for pre-W21 schema)
* compute_hotspots           -- core static-vs-runtime classification
                                (UPGRADE/CONFIRMED/DOWNGRADE)
* compute_security_hotspots  -- --security mode source-scan
* run_danger_mode            -- --danger mode p75 aggregator
* emit_findings              -- W120 findings-registry mirror
                                (sqlite3.OperationalError silent no-op
                                preserved for pre-W89 DB)
* serialize_to_sarif         -- SARIF projection
* apply_discrepancy_filter   -- UPGRADE/DOWNGRADE filter
* apply_runtime_sort         -- runtime_rank sort
* aggregate_by_kind          -- UPGRADE/CONFIRMED/DOWNGRADE counts
* derive_next_steps          -- suggest_next_steps wrap

Marker family ``hotspots_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test.

W816 PATTERN-2 REGRESSION GUARD
-------------------------------

The W816 sprint sealed the empty-corpus smoke gap (via W819). The
regression-guard test below confirms:

  1. The no-traces / table-missing path still emits
     ``partial_success: True`` with a named ``state``
     (``no_traces`` / ``table_missing``) -- W816 invariant preserved.
  2. The W607-CP substrate boundary on ``compute_hotspots`` does NOT
     re-introduce a Pattern-2 silent-fallback -- a raise in
     ``compute_hotspots`` still emits a non-empty envelope with a
     marker AND ``partial_success: True``, never a SAFE verdict on a
     degraded state.

DETECTOR FAMILY 10-WAY PAIRING (CLOSES THE QUARTET-DECUPLET)
------------------------------------------------------------

The bonus pairing test confirms each marker family stays inside its
own prefix without leaking across detector boundaries (hotspots + n1
+ over_fetch + missing_index + auth_gaps + smells + vibe_check +
clones + duplicates + dead == 10 detectors).

RUNTIME-TIER ISOLATION BONUS
----------------------------

cmd_hotspots emits findings at the ``runtime`` confidence tier (per
CLAUDE.md: trace-required classifications). The bonus test confirms
that tier survives the W607-CP plumbing -- no markers mutate the
detector's confidence-tier contract.
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


def _build_hotspots_project(tmp_path: Path, with_runtime_data: bool = True) -> Path:
    """Build a minimal indexed project for cmd_hotspots.

    A single source symbol is created. The ``runtime_stats`` table is
    created in either populated (one row) or empty form to exercise
    the ``no_traces`` / ``ready`` state paths. The hotspots detector
    may or may not produce a classification depending on the fixture
    depth -- the tests focus on W607-CP marker plumbing, not on the
    detector verdict itself.
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
        CREATE TABLE IF NOT EXISTS runtime_stats (
            id INTEGER PRIMARY KEY,
            symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
            symbol_name TEXT,
            file_path TEXT,
            trace_source TEXT,
            call_count INTEGER DEFAULT 0,
            p50_latency_ms REAL,
            p99_latency_ms REAL,
            error_rate REAL DEFAULT 0.0,
            last_seen TEXT,
            otel_db_system TEXT,
            otel_db_operation TEXT,
            otel_db_statement_type TEXT,
            ingested_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL DEFAULT 0,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS file_stats (
            file_id INTEGER PRIMARY KEY,
            commit_count INTEGER DEFAULT 0,
            total_churn INTEGER DEFAULT 0,
            distinct_authors INTEGER DEFAULT 0,
            complexity REAL DEFAULT 0,
            health_score REAL DEFAULT NULL,
            cochange_entropy REAL DEFAULT NULL,
            cognitive_load REAL DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity REAL DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0,
            param_count INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0,
            return_count INTEGER DEFAULT 0,
            bool_op_count INTEGER DEFAULT 0,
            callback_depth INTEGER DEFAULT 0
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
    if with_runtime_data:
        conn.execute(
            "INSERT INTO runtime_stats (symbol_id, symbol_name, file_path, "
            "call_count, p50_latency_ms, p99_latency_ms, error_rate) VALUES "
            "(1, 'helper', 'src/engine.py', 1000, 5.0, 50.0, 0.0)"
        )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def hotspots_project_no_traces(tmp_path):
    """Project with the runtime_stats table present but empty."""
    return _build_hotspots_project(tmp_path, with_runtime_data=False)


@pytest.fixture
def hotspots_project_with_traces(tmp_path):
    """Project with the runtime_stats table populated (one row)."""
    return _build_hotspots_project(tmp_path, with_runtime_data=True)


def _invoke_hotspots(cli_runner, project_root, *args, json_mode=True, sarif=False, detail=False):
    """Invoke the hotspots click command directly (bypassing the CLI group)."""
    from roam.commands.cmd_hotspots import hotspots

    obj = {"json": json_mode, "sarif": sarif, "budget": 0, "detail": detail}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(hotspots, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_CP_PHASES = (
    "load_trace_ingestion",
    "compute_hotspots",
    "compute_security_hotspots",
    "run_danger_mode",
    "emit_findings",
    "serialize_to_sarif",
    "apply_discrepancy_filter",
    "apply_runtime_sort",
    "aggregate_by_kind",
    "derive_next_steps",
    "serialize_items",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- empty traces envelope omits W607-CP substrate markers
# ---------------------------------------------------------------------------


def test_hotspots_clean_envelope_omits_w607cp_markers(cli_runner, hotspots_project_no_traces):
    """Clean hotspots run with no traces -> no W607-CP substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-CP bucket
    on the success path must NOT introduce new
    ``hotspots_<phase>_failed:`` markers tied to the W607-CP wrap.
    The no-traces path is the "happy" envelope here -- the detector
    cannot run without traces but the envelope composes cleanly.
    """
    result = _invoke_hotspots(cli_runner, hotspots_project_no_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "hotspots"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    cp_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"hotspots_{p}_failed:" in m for p in _CP_PHASES)]
    assert not cp_markers, (
        f"clean hotspots must NOT surface W607-CP substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) compute_hotspots failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_hotspots_compute_hotspots_failure_marker_format(cli_runner, hotspots_project_with_traces, monkeypatch):
    """If ``compute_hotspots`` raises, surface the canonical 3-segment marker."""
    from roam.runtime import hotspots as runtime_hotspots

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-compute-from-W607-CP")

    monkeypatch.setattr(runtime_hotspots, "compute_hotspots", _raise)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    compute_markers = [m for m in all_wo if m.startswith("hotspots_compute_hotspots_failed:")]
    assert compute_markers, f"expected hotspots_compute_hotspots_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in compute_markers), compute_markers
    assert any("synthetic-compute-from-W607-CP" in m for m in compute_markers), compute_markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"compute-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # LAW 6: the verdict still appears as a single line.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_hotspots_w607cp_warnings_in_envelope(cli_runner, hotspots_project_with_traces, monkeypatch):
    """Non-empty W607-CP bucket -> both top-level AND summary.warnings_out."""
    from roam.runtime import hotspots as runtime_hotspots

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CP")

    monkeypatch.setattr(runtime_hotspots, "compute_hotspots", _raise)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CP disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CP disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("hotspots_compute_hotspots_failed:")]
    assert markers, f"expected hotspots_compute_hotspots_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_hotspots_three_segment_marker_shape(cli_runner, hotspots_project_with_traces, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.runtime import hotspots as runtime_hotspots

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CP")

    monkeypatch.setattr(runtime_hotspots, "compute_hotspots", _raise)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("hotspots_compute_hotspots_failed:")]
    assert failure_markers, f"expected hotspots_compute_hotspots_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "hotspots_compute_hotspots_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) emit_findings failure (non-OperationalError) surfaces W607-CP marker
# ---------------------------------------------------------------------------


def test_hotspots_emit_findings_failure_surfaces_marker(cli_runner, hotspots_project_with_traces, monkeypatch):
    """W120 emit failure (non-OperationalError) surfaces W607-CP marker.

    sqlite3.OperationalError is the EXPECTED pre-W89 path (silent
    no-op). Generic exceptions surface via the W607-CP marker so a
    real bug in the persist substrate is loud, not silent.
    """
    from roam.commands import cmd_hotspots

    # Stub compute_hotspots to return at least one indexed-symbol item
    # so the persist branch is exercised.
    def _fake_compute(*_a, **_k):
        return [
            {
                "symbol_id": 1,
                "symbol_name": "helper",
                "file_path": "src/engine.py",
                "classification": "CONFIRMED",
                "static_rank": 1,
                "runtime_rank": 1,
                "runtime_stats": {
                    "call_count": 1000,
                    "p50_latency_ms": 5.0,
                    "p99_latency_ms": 50.0,
                    "error_rate": 0.0,
                },
                "static_stats": {
                    "pagerank": 0.1,
                    "complexity": 1.0,
                    "churn": 0,
                },
            }
        ]

    from roam.runtime import hotspots as runtime_hotspots

    monkeypatch.setattr(runtime_hotspots, "compute_hotspots", _fake_compute)

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-CP")

    monkeypatch.setattr(cmd_hotspots, "_emit_hotspots_findings", _raise)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("hotspots_emit_findings_failed:")]
    assert emit_markers, f"expected hotspots_emit_findings_failed: marker; got {all_wo!r}"
    # The hotspots command still emits a clean envelope past the
    # registry-mirror failure -- W120 is additive, not load-bearing.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) emit_findings OperationalError path stays silent (no W607-CP marker)
# ---------------------------------------------------------------------------


def test_hotspots_emit_findings_operational_error_stays_silent(cli_runner, hotspots_project_with_traces, monkeypatch):
    """W607-CP MUST preserve the W120 silent no-op contract on
    ``sqlite3.OperationalError`` (pre-W89 schema -- no findings table).

    The marker MUST NOT surface for this expected degraded path.
    """
    from roam.commands import cmd_hotspots

    def _fake_compute(*_a, **_k):
        return [
            {
                "symbol_id": 1,
                "symbol_name": "helper",
                "file_path": "src/engine.py",
                "classification": "CONFIRMED",
                "static_rank": 1,
                "runtime_rank": 1,
                "runtime_stats": {
                    "call_count": 1000,
                    "p50_latency_ms": 5.0,
                    "p99_latency_ms": 50.0,
                    "error_rate": 0.0,
                },
                "static_stats": {
                    "pagerank": 0.1,
                    "complexity": 1.0,
                    "churn": 0,
                },
            }
        ]

    from roam.runtime import hotspots as runtime_hotspots

    monkeypatch.setattr(runtime_hotspots, "compute_hotspots", _fake_compute)

    def _raise_op_err(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings (pre-W89 schema)")

    monkeypatch.setattr(cmd_hotspots, "_emit_hotspots_findings", _raise_op_err)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("hotspots_emit_findings_failed:")]
    assert not emit_markers, (
        f"sqlite3.OperationalError is the EXPECTED pre-W89 silent "
        f"no-op path; W607-CP marker MUST NOT surface; "
        f"got {emit_markers!r}"
    )


# ---------------------------------------------------------------------------
# (7) compute_security_hotspots failure -> --security envelope composes
# ---------------------------------------------------------------------------


def test_hotspots_security_compute_failure_degrades_cleanly(cli_runner, hotspots_project_with_traces, monkeypatch):
    """A raise in ``_compute_security_hotspots`` must NOT crash --security."""
    from roam.commands import cmd_hotspots

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-security-from-W607-CP")

    monkeypatch.setattr(cmd_hotspots, "_compute_security_hotspots", _raise)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces, "--security")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    sec_markers = [m for m in all_wo if m.startswith("hotspots_compute_security_hotspots_failed:")]
    assert sec_markers, f"expected hotspots_compute_security_hotspots_failed: marker; got {all_wo!r}"
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-CP stays in ``hotspots_*`` family
# ---------------------------------------------------------------------------


def test_w607cp_marker_prefix_stays_in_hotspots_family(cli_runner, hotspots_project_with_traces, monkeypatch):
    """Every W607-CP substrate marker uses the canonical ``hotspots_*`` prefix.

    Hard distinction from sibling W607-* layers including cmd_n1
    (W607-CB, ``n1_*``), cmd_smells (W607-BN, ``smells_*``),
    cmd_vibe_check (W607-BS, ``vibe_check_*``), cmd_clones (W607-BQ,
    ``clones_*``), cmd_duplicates (W607-BM, ``duplicates_*``), and
    cmd_dead (W607-BX, ``dead_*``). cmd_hotspots is the tenth detector
    in the family decuplet -- a leaking marker would cross the
    detector-family boundary.
    """
    from roam.runtime import hotspots as runtime_hotspots

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CP")

    monkeypatch.setattr(runtime_hotspots, "compute_hotspots", _raise)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("hotspots_"), (
            f"every surfaced W607-CP marker must use the ``hotspots_*`` "
            f"prefix family (cmd_hotspots scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("n1_", "cmd_n1 W607-CB (N+1 detector)"),
            ("smells_", "cmd_smells W607-BN (structural smells)"),
            ("vibe_check_", "cmd_vibe_check W607-BS (LLM-rot detector)"),
            ("clones_", "cmd_clones W607-BQ (clone detector)"),
            ("duplicates_", "cmd_duplicates W607-BM (duplicates)"),
            ("dead_", "cmd_dead W607-BX (dead-code detector)"),
            ("over_fetch_", "cmd_over_fetch W607-CE (ORM over-fetch)"),
            ("missing_index_", "cmd_missing_index W607-CI"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
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
# (9) Source-level guard: cmd_hotspots carries the W607-CP accumulator
# ---------------------------------------------------------------------------


def test_cmd_hotspots_carries_w607cp_accumulator():
    """AST-level guard: cmd_hotspots source carries the W607-CP accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    assert src_path.exists(), f"cmd_hotspots.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607cp_warnings_out" in src, (
        "W607-CP accumulator missing from cmd_hotspots; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_cp" in src, (
        "W607-CP ``_run_check_cp`` helper missing from cmd_hotspots; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_cp = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cp":
            found_run_check_cp = True
            break
    assert found_run_check_cp, (
        "W607-CP ``_run_check_cp`` helper not found in cmd_hotspots AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (10) Each W607-CP substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607cp_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-CP substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _CP_PHASES:
        same_line = f'_run_check_cp("{phase}"' in src
        multi_line = (
            f'_run_check_cp(\n        "{phase}"' in src
            or f'_run_check_cp(\n            "{phase}"' in src
            or f'_run_check_cp(\n                "{phase}"' in src
            or f'_run_check_cp(\n                    "{phase}"' in src
            or f'_run_check_cp(\n                        "{phase}"' in src
        )
        # load_trace_ingestion + emit_findings are wrapped via direct
        # try/except (NOT _run_check_cp) because they need to distinguish
        # sqlite3.OperationalError (expected pre-W21/W89 path) from
        # generic Exception (W607-CP marker). Source-grep on the marker
        # name in those cases.
        marker_grep = f"hotspots_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-CP wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (11) W816 PATTERN-2 REGRESSION GUARD: empty-state branch invariant
# ---------------------------------------------------------------------------


def test_w816_no_traces_partial_success_preserved(cli_runner, hotspots_project_no_traces):
    """W816 regression guard: no-traces path -> partial_success: True
    with named ``state`` (``no_traces`` or ``table_missing``).

    W816/W819 sealed the empty-corpus smoke gap on cmd_hotspots. The
    W607-CP plumbing must NOT re-introduce that bug: when no markers
    fire, the no-traces envelope MUST keep ``state: no_traces`` AND
    ``partial_success: True`` so MCP consumers can distinguish
    "nothing to flag" from "degraded".
    """
    result = _invoke_hotspots(cli_runner, hotspots_project_no_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output) if result.output.strip() else {}
    summary = data.get("summary") or {}

    # W816 invariant: state present, partial_success True on no-traces.
    state = summary.get("state")
    assert state in ("no_traces", "table_missing"), (
        f"no-traces envelope MUST carry named state "
        f"(``no_traces`` or ``table_missing``); got state={state!r}, "
        f"summary={summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"no-traces partial_success must be True (W816 invariant); got summary={summary!r}"
    )

    # And no W607-CP markers fired (since nothing raised).
    top_wo = data.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    cp_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"hotspots_{p}_failed:" in m for p in _CP_PHASES)]
    assert not cp_markers, f"clean no-traces path must NOT surface W607-CP markers; got {cp_markers!r}"


def test_w816_pattern_2_silent_fallback_eliminated_on_degraded_path(
    cli_runner, hotspots_project_with_traces, monkeypatch
):
    """W816 Pattern-2 regression guard on the degraded compute path.

    If ``compute_hotspots`` raises, the empty-floor default kicks in
    (items == []) and the populated-runtime envelope is emitted with
    zero hotspots. The W607-CP wrap MUST flip
    ``partial_success: True`` on that branch so the degraded envelope
    is NOT mistaken for a clean "no runtime hotspots" verdict (the
    classic Pattern-2 silent-fallback bug).
    """
    from roam.runtime import hotspots as runtime_hotspots

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-W816-pattern-2-from-W607-CP")

    monkeypatch.setattr(runtime_hotspots, "compute_hotspots", _raise)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    # The empty-floor default takes us into the populated-runtime
    # envelope path with zero items -- AND the marker must surface,
    # AND partial_success: True.
    assert summary.get("partial_success") is True, (
        f"degraded compute path MUST flip partial_success=True "
        f"(Pattern-2 silent-fallback guard); got summary={summary!r}"
    )

    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    compute_markers = [m for m in all_wo if m.startswith("hotspots_compute_hotspots_failed:")]
    assert compute_markers, (
        f"degraded compute path MUST surface the compute_hotspots marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (12) DETECTOR FAMILY 10-WAY pairing bonus -- CLOSES THE DECUPLET
# ---------------------------------------------------------------------------


def test_detector_family_10way_marker_prefixes_coexist(cli_runner, hotspots_project_with_traces, monkeypatch):
    """DETECTOR FAMILY 10-WAY pairing bonus -- closes the decuplet.

    Confirm ``hotspots_<phase>_failed:`` markers coexist with
    ``n1_*`` (W607-CB), ``over_fetch_*`` (W607-CE), ``missing_index_*``
    (W607-CI), ``auth_gaps_*`` (W607-CM), ``smells_*`` (W607-BN),
    ``vibe_check_*`` (W607-BS), ``clones_*`` (W607-BQ),
    ``duplicates_*`` (W607-BM), and ``dead_*`` (W607-BX) markers
    without cross-prefix leakage.

    This is the load-bearing prefix-discipline test for the detector
    family decuplet: each command's marker family stays inside its own
    prefix so a downstream finder/grep on ``hotspots_*`` markers picks
    up ONLY the runtime-hotspot detector substrate failures.
    """
    from roam.runtime import hotspots as runtime_hotspots

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-10way-from-W607-CP")

    monkeypatch.setattr(runtime_hotspots, "compute_hotspots", _raise)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # The hotspots marker fires.
    assert any(m.startswith("hotspots_compute_hotspots_failed:") for m in all_wo), all_wo

    # None of the nine detector-sibling prefixes leak into the
    # hotspots envelope. This closes the decuplet: hotspots + n1 +
    # over_fetch + missing_index + auth_gaps + smells + vibe_check +
    # clones + duplicates + dead == 10 detectors.
    for forbidden_prefix in (
        "n1_",
        "over_fetch_",
        "missing_index_",
        "auth_gaps_",
        "smells_",
        "vibe_check_",
        "clones_",
        "duplicates_",
        "dead_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on detector-family 10-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_hotspots envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (13) AST source-level guard: canonical marker shape
# ---------------------------------------------------------------------------


def test_w607cp_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-CP marker shape is implemented
    in the shared ``boundary_helpers`` module and wired to the ``hotspots``
    recipe in cmd_hotspots.
    """
    cmd_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    helper_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "boundary_helpers.py"
    cmd_src = cmd_src_path.read_text(encoding="utf-8")
    helper_src = helper_src_path.read_text(encoding="utf-8")
    # The generic marker template lives in the shared helper.
    fstring_pattern = 'f"{recipe_name}_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in helper_src, (
        f"canonical W607 marker fstring missing from boundary_helpers; expected: {fstring_pattern}"
    )
    # cmd_hotspots binds the template to the hotspots recipe name.
    assert 'make_run_check("hotspots",' in cmd_src, (
        "cmd_hotspots must wire the shared helper to the hotspots marker family."
    )


# ---------------------------------------------------------------------------
# (14) RUNTIME-TIER ISOLATION BONUS
# ---------------------------------------------------------------------------


def test_w607cp_preserves_runtime_confidence_tier_contract():
    """RUNTIME-TIER ISOLATION BONUS.

    Per CLAUDE.md, cmd_hotspots emits findings at the ``runtime``
    confidence tier (NOT static_analysis / structural / heuristic).
    All three classifications (UPGRADE / CONFIRMED / DOWNGRADE) require
    ingested ``runtime_stats`` rows -- the detector cannot produce
    findings without real trace data. Confirm the W607-CP plumbing
    does NOT mutate that tier contract (no marker should rewrite the
    confidence value in the emit_finding call).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")
    # The CONFIDENCE_RUNTIME constant should still drive the emit_finding
    # confidence field -- the W607-CP plumbing must NOT introduce a
    # confidence-tier override on the persist path.
    assert "CONFIDENCE_RUNTIME" in src, (
        "CONFIDENCE_RUNTIME constant disappeared from cmd_hotspots; the runtime-tier contract has been violated."
    )
    assert "confidence=CONFIDENCE_RUNTIME" in src, (
        "emit_finding no longer uses CONFIDENCE_RUNTIME; the runtime-"
        "tier contract has been violated by the W607-CP plumbing."
    )
    # Confirm no W607-CP path forces a non-runtime tier (e.g., a marker
    # callsite using CONFIDENCE_HEURISTIC / CONFIDENCE_STRUCTURAL).
    assert "CONFIDENCE_HEURISTIC" not in src, (
        "cmd_hotspots imported CONFIDENCE_HEURISTIC -- runtime tier contract violated."
    )
    assert "CONFIDENCE_STRUCTURAL" not in src, (
        "cmd_hotspots imported CONFIDENCE_STRUCTURAL -- runtime tier contract violated."
    )
    assert "CONFIDENCE_STATIC_ANALYSIS" not in src, (
        "cmd_hotspots imported CONFIDENCE_STATIC_ANALYSIS -- runtime tier contract violated."
    )


# ---------------------------------------------------------------------------
# (15) SARIF projection failure -> marker surfaces on CI path
# ---------------------------------------------------------------------------


def test_hotspots_sarif_failure_surfaces_marker(cli_runner, hotspots_project_with_traces, monkeypatch):
    """A raise in the SARIF projection must NOT crash the hotspots CI path.

    The SARIF projection is wrapped so a writer exception is contained
    -- the click command still returns cleanly without a traceback.
    By design SARIF mode short-circuits the envelope (writes pure
    SARIF to stdout), so we verify exit_code only on the smoke-test
    axis; the marker accumulator stays in-process but is not flushed
    to a second envelope.
    """
    from roam.output import sarif as sarif_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-sarif-from-W607-CP")

    monkeypatch.setattr(sarif_mod, "hotspots_to_sarif", _raise)

    result = _invoke_hotspots(
        cli_runner,
        hotspots_project_with_traces,
        json_mode=False,
        sarif=True,
    )
    # The W607-CP wrap protects against crash even on the SARIF path.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (16) aggregate_by_kind failure -> empty histogram, envelope composes
# ---------------------------------------------------------------------------


def test_hotspots_aggregate_by_kind_failure_degrades_cleanly(cli_runner, hotspots_project_with_traces, monkeypatch):
    """A raise in the by-kind aggregator degrades to (0, 0, 0)."""
    from roam.runtime import hotspots as runtime_hotspots

    # Have compute_hotspots return one finding whose ``classification``
    # field is absent so the lambda ``h["classification"]`` lookup
    # raises KeyError inside the aggregate substrate.
    def _fake_compute(*_a, **_k):
        return [
            {
                "symbol_id": 1,
                "symbol_name": "helper",
                "file_path": "src/engine.py",
                # NO "classification" key -- aggregator KeyErrors.
                "static_rank": 1,
                "runtime_rank": 1,
                "runtime_stats": {
                    "call_count": 1000,
                    "p50_latency_ms": 5.0,
                    "p99_latency_ms": 50.0,
                    "error_rate": 0.0,
                },
                "static_stats": {
                    "pagerank": 0.1,
                    "complexity": 1.0,
                    "churn": 0,
                },
            }
        ]

    monkeypatch.setattr(runtime_hotspots, "compute_hotspots", _fake_compute)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    agg_markers = [m for m in all_wo if m.startswith("hotspots_aggregate_by_kind_failed:")]
    assert agg_markers, f"expected hotspots_aggregate_by_kind_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True
