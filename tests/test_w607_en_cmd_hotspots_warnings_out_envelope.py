"""W607-EN -- additive aggregation-phase plumbing for ``cmd_hotspots``.

cmd_hotspots is the runtime-hotspot detector (W120 origin per CLAUDE.md
detector roster -- part of the original 16 findings-registry substrate
detectors). The W607-CP wave installed substrate-CALL plumbing around
the 11 substrate helpers (``load_trace_ingestion`` / ``compute_hotspots``
/ ``compute_security_hotspots`` / ``run_danger_mode`` / ``emit_findings``
/ ``serialize_to_sarif`` / ``apply_discrepancy_filter`` /
``apply_runtime_sort`` / ``aggregate_by_kind`` / ``derive_next_steps`` /
``serialize_items``).

This W607-EN wave layers an ADDITIVE aggregation-phase plumbing on top
of that substrate, mirroring the canonical 4-phase shape that
cmd_bus_factor W607-CQ+EH, cmd_auth_gaps W607-CM+ED, cmd_n1 W607-CB+DQ,
cmd_over_fetch W607-CE+DT, and cmd_missing_index W607-CI+DX use:

  - substrate-CALL layer: W607-CP (11 boundaries -- see _CP_PHASES below)
  - aggregation-phase layer: W607-EN (4 boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``hotspots_*`` marker family and the
``hotspots_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two bucket sources (``_w607cp_warnings_out`` substrate-CALL +
``_w607en_warnings_out`` aggregation-phase) are merged at envelope-emit
time into ``warnings_out`` so consumers see the full degradation
lineage. The phase names DO NOT collide -- CP substrate phases are
``compute_hotspots`` / ``serialize_items`` / etc., aggregation phases
are ``score_classify`` / ``compute_predicate`` / ``compute_verdict``
/ ``serialize_envelope``.

W978 7-discipline first-hypothesis check
----------------------------------------

Every W607-EN ``default=`` MUST be a literal constant, AND every
``len()`` / ``sum()`` over the wrapped input MUST live inside the
closure. The AST audit below pins these disciplines at the W607-EN
layer (mirror of the W607-EH audit on cmd_bus_factor).

W120 + W816 + W978 PRESERVATION
-------------------------------

W120 layered the findings-registry mirror. W816 sealed the Pattern-2
empty-corpus regression (explicit zero-count verdict on the no-traces
branch). W978 first-hypothesis discipline was applied at W607-CP. The
regression-guard tests below confirm:

  1. The clean populated path still emits a single-line verdict.
  2. The W607-EN aggregation boundary does NOT re-introduce Pattern-2
     silent-fallback -- a raise in ``json_envelope`` still emits a
     non-empty floor stub with a marker AND ``partial_success: True``,
     never a SAFE verdict on a degraded state (W816 preservation).
  3. The W120 ``_emit_hotspots_findings`` reference is preserved in
     source (the additive aggregation-layer wrapping must not touch the
     detector logic).

DETECTOR-FAMILY 11-WAY PAIRING (CLOSES THE FINAL UNPAIRED)
----------------------------------------------------------

This wave is the LAST UNPAIRED detector in the original 16-detector
findings-registry roster. With cmd_hotspots W607-CP+EN, the 11-way
aggregation-layer closure for the registry-substrate detectors is:

  auth_gaps CM+ED, n1 CB+DQ, over_fetch CE+DT, missing_index CI+DX,
  smells BN+DF, clones BQ+DC, duplicates BM+DD, dead BX+DL,
  bus_factor CQ+EH, taint AY+CJ, vulns AQ+CH, hotspots CP+EN

The cross-detector pin test below AST-scans all 11 commands confirming
each carries BOTH substrate + agg layers in source.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
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
# Canonical phase enumerations
# ---------------------------------------------------------------------------


_EN_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)

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
# Fixtures (mirror the W607-CP fixture so both files exercise the same
# indexed corpus)
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_hotspots_project(tmp_path: Path, with_runtime_data: bool = True) -> Path:
    """Build a minimal indexed project for cmd_hotspots (mirror of W607-CP)."""
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


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-EN aggregation markers
# ---------------------------------------------------------------------------


def test_hotspots_happy_path_no_w607en_markers(cli_runner, hotspots_project_with_traces):
    """Clean hotspots run -> no W607-EN aggregation markers."""
    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "hotspots"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _EN_PHASES:
        prefix = f"hotspots_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean hotspots must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive _run_check_en helper + accumulator
# ---------------------------------------------------------------------------


def test_cmd_hotspots_carries_w607en_accumulator():
    """AST-level guard: cmd_hotspots source carries the W607-EN anchors
    AND the pre-existing W607-CP layer.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    assert src_path.exists(), f"cmd_hotspots.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    assert "w607en_warnings_out" in src, (
        "W607-EN accumulator missing from cmd_hotspots; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_en" in src, (
        "W607-EN helper ``_run_check_en`` missing from cmd_hotspots; the additive wrapper has been refactored away."
    )

    tree = ast.parse(src)
    found_run_check_en = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_en":
            found_run_check_en = True
            break
    assert found_run_check_en, (
        "W607-EN ``_run_check_en`` helper not found in cmd_hotspots AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-CP must still be present (additive layer does NOT replace it)
    assert "w607cp_warnings_out" in src, (
        "W607-CP accumulator vanished alongside the W607-EN add; the "
        "additive plumbing must preserve the W607-CP substrate-CALL layer."
    )
    assert "_run_check_cp" in src, "W607-CP helper has been removed."


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_en():
    """Every aggregation-phase boundary calls ``_run_check_en(...)`` with
    the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _EN_PHASES:
        same_line = f'_run_check_en("{phase}"' in src
        multi_line = any(f'_run_check_en(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"hotspots_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-EN wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) Per-phase isolation -- serialize_envelope raise -> marker + floor stub
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, hotspots_project_with_traces, monkeypatch):
    """If ``json_envelope`` raises on the populated path, the wrap floors
    to a parseable envelope stub and surfaces
    ``hotspots_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_hotspots as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-EN")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "hotspots", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("hotspots_serialize_envelope_failed:")]
    assert markers, f"expected ``hotspots_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) Per-phase isolation -- compute_verdict floor is a single line
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_a_single_line(cli_runner, hotspots_project_with_traces):
    """Compute-verdict boundary -- the verdict string on the clean path
    MUST be a single line (LAW 6 standalone-parse discipline).
    """
    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"LAW 6: compute_verdict must produce a single line; got {verdict!r}"


# ---------------------------------------------------------------------------
# (6) Per-phase isolation -- score_classify surfaces run_state on summary
# ---------------------------------------------------------------------------


def test_score_classify_surfaces_run_state(cli_runner, hotspots_project_with_traces):
    """Clean run -> the run_state must be present and in the canonical
    closed enumeration.
    """
    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("run_state") in {
        "COLD",
        "WARM",
        "HOT",
        "DEGRADED",
    }, f"run_state missing/invalid on clean hotspots envelope; got {summary.get('run_state')!r}"


# ---------------------------------------------------------------------------
# (7) Per-phase isolation -- compute_predicate surfaces rollup fields
# ---------------------------------------------------------------------------


def test_compute_predicate_surfaces_rollup_fields(cli_runner, hotspots_project_with_traces):
    """Compute-predicate boundary -- happy path surfaces heat,
    commit_count, hottest_files rollup on the summary.
    """
    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    assert "heat" in summary, f"compute_predicate must surface heat; got summary keys = {sorted(summary.keys())!r}"
    assert isinstance(summary["heat"], int), f"heat must be an int; got {type(summary['heat']).__name__!r}"
    assert "commit_count" in summary, (
        f"compute_predicate must surface commit_count; got summary keys = {sorted(summary.keys())!r}"
    )
    assert isinstance(summary["commit_count"], int), (
        f"commit_count must be an int; got {type(summary['commit_count']).__name__!r}"
    )
    assert "hottest_files" in summary, (
        f"compute_predicate must surface hottest_files rollup; got summary keys = {sorted(summary.keys())!r}"
    )
    assert isinstance(summary["hottest_files"], list), (
        f"hottest_files must be a list; got {type(summary['hottest_files']).__name__!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-CP substrate + W607-EN aggregation markers BOTH surface
# ---------------------------------------------------------------------------


def test_w607cp_substrate_and_w607en_aggregation_coexist(cli_runner, hotspots_project_with_traces, monkeypatch):
    """When BOTH layers fault, BOTH marker prefixes surface.

    Selects a W607-CP substrate name (serialize_items) + the W607-EN
    serialize_envelope boundary so both layers produce a marker on the
    same invocation.
    """
    from roam.commands import cmd_hotspots as _mod

    # Raise inside serialize_items closure via a poisoned items list
    # cannot easily be triggered externally; instead monkeypatch
    # ``json_envelope`` so the EN serialize_envelope raises, then patch
    # an upstream substrate to also fault.
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-en-coexist-envelope")

    # Patch the CP-wrapped derive_next_steps to raise so a CP marker
    # surfaces alongside the EN serialize_envelope marker.
    def _raise_next_steps(*a, **kw):
        raise RuntimeError("synthetic-cp-coexist-next-steps")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)
    monkeypatch.setattr(_mod, "suggest_next_steps", _raise_next_steps)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    cp_markers = [m for m in top_wo if m.startswith("hotspots_derive_next_steps_failed:")]
    en_markers = [m for m in top_wo if m.startswith("hotspots_serialize_envelope_failed:")]

    assert cp_markers, f"W607-CP substrate-CALL marker (hotspots_derive_next_steps_failed) missing; got {top_wo!r}"
    assert en_markers, f"W607-EN aggregation-phase marker (hotspots_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``hotspots_*`` family
    assert all(m.startswith("hotspots_") for m in (cp_markers + en_markers)), (
        f"all markers must share the canonical ``hotspots_*`` family; got cp = {cp_markers!r}, en = {en_markers!r}"
    )


# ---------------------------------------------------------------------------
# (9) W816 partial_success seal preserved
# ---------------------------------------------------------------------------


def test_w816_partial_success_flips_on_en_raise(cli_runner, hotspots_project_with_traces, monkeypatch):
    """W816 seal: a degraded path MUST flip partial_success=True. The
    W607-EN aggregation layer must preserve this on the raise path --
    a synthetic ``json_envelope`` raise produces a floor stub with
    ``partial_success: True`` in its summary.
    """
    from roam.commands import cmd_hotspots as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-W816-partial-success-from-EN")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("partial_success") is True, (
        f"W816 seal: non-empty W607-EN warnings_out must flip summary.partial_success=True; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (10) W120 findings-registry reference preserved in source
# ---------------------------------------------------------------------------


def test_w120_findings_registry_invariant_preserved_in_source():
    """W120 findings-registry mirror path must be preserved in
    cmd_hotspots source after the additive W607-EN aggregation
    plumbing. The aggregation-layer wrapping must not touch the
    detector logic.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")

    # W120 mirror helper is the canonical pin
    assert "_emit_hotspots_findings" in src, (
        "W120 _emit_hotspots_findings reference removed; the findings-registry mirror path is no longer wired."
    )
    # W120 documentation reference
    assert "W120" in src, (
        "W120 reference comment vanished from cmd_hotspots; the "
        "findings-registry mirror rationale is no longer documented."
    )
    # W120 --persist gate is the canonical entry point
    assert "persist" in src, "W120 --persist gate has been removed from cmd_hotspots."


# ---------------------------------------------------------------------------
# (11) Cross-prefix isolation -- W607-EN stays in hotspots_* family
# ---------------------------------------------------------------------------


def test_w607en_cross_prefix_isolation(cli_runner, hotspots_project_with_traces, monkeypatch):
    """W607-EN markers must NOT leak into sibling W607-* prefix families."""
    from roam.commands import cmd_hotspots as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation-EN")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for cross-prefix check"
    for marker in failure_markers:
        assert marker.startswith("hotspots_"), (
            f"every surfaced W607-EN marker must use the "
            f"``hotspots_*`` prefix family (cmd_hotspots scope); "
            f"got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("auth_gaps_", "cmd_auth_gaps W607-CM / ED"),
            ("vulns_", "cmd_vulns W607-AQ / CH"),
            ("taint_", "cmd_taint W607-AY / CJ"),
            ("n1_", "cmd_n1 W607-CB / DQ"),
            ("over_fetch_", "cmd_over_fetch W607-CE / DT"),
            ("missing_index_", "cmd_missing_index W607-CI / DX"),
            ("smells_", "cmd_smells W607-BN / DF"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("clones_", "cmd_clones W607-BQ / DC"),
            ("duplicates_", "cmd_duplicates W607-BM / DD"),
            ("dead_", "cmd_dead W607-BX / DL"),
            ("bus_factor_", "cmd_bus_factor W607-CQ / EH"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("dark_matter_", "cmd_dark_matter W607-BK / CZ"),
            ("debt_", "cmd_debt W607-BG"),
            ("health_", "cmd_health W607-M / BA"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) Phase-name collision check -- EN phases distinct from CP phases
# ---------------------------------------------------------------------------


def test_w607en_phase_names_dont_collide_with_w607cp():
    """W978 4th-discipline guard: the 4 W607-EN aggregation phase names
    MUST be disjoint from the 11 W607-CP substrate phase names.
    """
    en_set = set(_EN_PHASES)
    cp_set = set(_CP_PHASES)
    collisions = en_set & cp_set
    assert not collisions, (
        f"W607-EN phase names collide with W607-CP: {collisions!r}. "
        f"Rename one set so each marker phase belongs to exactly one "
        f"layer."
    )


# ---------------------------------------------------------------------------
# (13) compute_verdict floor is a literal constant -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """W978 first-hypothesis discipline anchor: compute_verdict floor
    must be a literal string, NOT an f-string re-interpolating the
    same values that just raised.

    Canonical floor for cmd_hotspots is ``"hotspots completed"``
    (mirror of cmd_bus_factor W607-EH ``"bus_factor completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="hotspots completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-EN "
        "discipline; the canonical floor literal 'hotspots completed' "
        "is missing from cmd_hotspots.py"
    )


# ---------------------------------------------------------------------------
# (14) W978 7-discipline AST audit -- default= floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """Every W607-EN ``default=`` must be a literal constant, NOT
    computed from upstream values.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.Dict):
            return all(_is_literal(k) for k in node.keys if k is not None) and all(_is_literal(v) for v in node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_literal(e) for e in node.elts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _is_literal(node.operand)
        return False

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_en"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_en(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_hotspots.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants."
    )


# ---------------------------------------------------------------------------
# (15) W978 5th-discipline -- closures call len() INSIDE, not at kwarg-bind
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_closures_not_at_kwarg_bind_site():
    """Every ``len()`` call on a wrapped input MUST live INSIDE the
    wrapped closure, NOT at the ``_run_check_en(...)`` call site as a
    positional or keyword argument expression.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_en"):
            continue
        for sub in node.args:
            for descendant in ast.walk(sub):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call at "
                        f"_run_check_en positional-arg site -- W978 "
                        f"5th-discipline violation"
                    )
        for kw in node.keywords:
            for descendant in ast.walk(kw.value):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call in "
                        f"_run_check_en kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_hotspots.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure."
    )


# ---------------------------------------------------------------------------
# (16) AST-scan -- BOTH accumulators are pinned in source (CP + EN)
# ---------------------------------------------------------------------------


def test_w607en_coexists_with_w607cp_in_source():
    """W607-EN is ADDITIVE -- the pre-existing W607-CP substrate-CALL
    family MUST still be present in source alongside W607-EN.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")

    assert "w607cp_warnings_out" in src, "W607-CP substrate-CALL accumulator has been removed."
    assert "_run_check_cp" in src, "W607-CP helper has been removed."
    assert "w607en_warnings_out" in src, "W607-EN aggregation-phase accumulator has been removed."
    assert "_run_check_en" in src, "W607-EN helper has been removed."


# ---------------------------------------------------------------------------
# (17) ANY W607-EN marker flips partial_success on the populated path
# ---------------------------------------------------------------------------


def test_any_en_marker_flips_partial_success(cli_runner, hotspots_project_with_traces, monkeypatch):
    """ANY W607-EN marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    hotspots" from "hotspots ran with aggregation degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_hotspots as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-EN")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-EN warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (18) warnings_out mirrors -- top-level AND summary BOTH populated
# ---------------------------------------------------------------------------


def test_w607en_warnings_out_in_both_top_and_summary(cli_runner, hotspots_project_with_traces, monkeypatch):
    """Non-empty W607-EN bucket -> both top-level AND summary.warnings_out
    populated.
    """
    from roam.commands import cmd_hotspots as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-EN")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-EN raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-EN raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("hotspots_serialize_envelope_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("hotspots_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (19) Helper-template ``return default`` verbatim shape -- W607-DW pin
# ---------------------------------------------------------------------------


def test_run_check_en_helper_returns_default_verbatim():
    """W607-DW regression guard: the ``_run_check_en`` helper body must
    end with ``return default`` (verbatim) -- NOT
    ``return default if default is not None else {}``.

    The W607-DP/DW finding identified that an "improved" default-coerce
    return shape silently masks the floor literal.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_helper = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_en"):
            continue
        found_helper = True
        try_stmt = None
        for stmt in node.body:
            if isinstance(stmt, ast.Try):
                try_stmt = stmt
                break
        assert try_stmt is not None, (
            f"_run_check_en body must contain a try/except block; got {[type(s).__name__ for s in node.body]!r}"
        )
        assert try_stmt.handlers, "_run_check_en try-block must have at least one except-handler"
        last_handler = try_stmt.handlers[-1]
        last_stmt = last_handler.body[-1]
        assert isinstance(last_stmt, ast.Return), (
            f"_run_check_en except-handler must end with a Return statement; got {type(last_stmt).__name__!r}"
        )
        assert isinstance(last_stmt.value, ast.Name), (
            f"_run_check_en return value must be a bare ``default`` Name "
            f"node (W607-DW verbatim shape); got "
            f"{type(last_stmt.value).__name__!r}"
        )
        assert last_stmt.value.id == "default", (
            f"_run_check_en return value must reference the ``default`` parameter; got Name(id={last_stmt.value.id!r})"
        )
        break

    assert found_helper, "_run_check_en helper not found in cmd_hotspots AST"


# ---------------------------------------------------------------------------
# (20) score_classify isolation -- clean populated path is not DEGRADED
# ---------------------------------------------------------------------------


def test_score_classify_isolation_clean_path_not_degraded(cli_runner, hotspots_project_with_traces):
    """Per-phase isolation guard: a clean populated run surfaces a
    non-DEGRADED ``run_state`` -- DEGRADED is reserved for the raise
    path / empty-results floor.
    """
    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    state = summary.get("run_state")
    assert state in {"COLD", "WARM", "HOT"}, (
        f"clean populated hotspots run must surface one of COLD/WARM/HOT; got run_state={state!r}"
    )
    # DEGRADED is the floor -- it must NOT surface on the clean path.
    assert state != "DEGRADED", f"DEGRADED run_state must be reserved for the raise floor; got summary = {summary!r}"


# ---------------------------------------------------------------------------
# (21) compute_predicate floor dict shape -- W978 6th-discipline
# ---------------------------------------------------------------------------


def test_compute_predicate_floor_dict_shape():
    """W978 6th-discipline: compute_predicate floor MUST be a concrete
    dict carrying all 3 documented keys (heat / commit_count /
    hottest_files), NOT a sentinel that may __len__-raise downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_predicate_floor = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_en"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "compute_predicate"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            assert isinstance(kw.value, ast.Dict), (
                f"compute_predicate default= must be a literal dict; got {type(kw.value).__name__!r}"
            )
            keys_present = set()
            for k in kw.value.keys:
                if isinstance(k, ast.Constant):
                    keys_present.add(k.value)
            expected_keys = {
                "heat",
                "commit_count",
                "hottest_files",
            }
            missing = expected_keys - keys_present
            assert not missing, (
                f"compute_predicate floor dict missing keys {missing!r}; "
                f"floor shape must mirror the happy-path return so "
                f"downstream consumers see a consistent envelope."
            )
            found_predicate_floor = True
            break

    assert found_predicate_floor, (
        "compute_predicate _run_check_en call site not found in source; "
        "the aggregation boundary has been refactored away."
    )


# ---------------------------------------------------------------------------
# (22) score_classify floor dict shape -- W978 6th-discipline
# ---------------------------------------------------------------------------


def test_score_classify_floor_dict_shape():
    """W978 6th-discipline: score_classify floor MUST be a concrete
    dict carrying ``state: "DEGRADED"`` + ``scanned: 0``.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_score_floor = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_en"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "score_classify"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            assert isinstance(kw.value, ast.Dict), (
                f"score_classify default= must be a literal dict; got {type(kw.value).__name__!r}"
            )
            keys_present = {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
            assert "state" in keys_present, (
                f"score_classify floor must carry ``state`` key; got keys = {keys_present!r}"
            )
            assert '"state": "DEGRADED"' in src, "score_classify floor state value must be DEGRADED literal"
            found_score_floor = True
            break

    assert found_score_floor, (
        "score_classify _run_check_en call site not found in source; the aggregation boundary has been refactored away."
    )


# ---------------------------------------------------------------------------
# (23) Both accumulators pinned in AST (CP + EN at function-scope level)
# ---------------------------------------------------------------------------


def test_ast_audit_both_accumulators_present_in_hotspots_function():
    """AST audit: both ``_w607cp_warnings_out`` and ``_w607en_warnings_out``
    are assigned inside the ``hotspots`` click command body.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    cp_found = False
    en_found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "hotspots":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.AnnAssign):
                # Try plain Assign too
                if isinstance(sub, ast.Assign):
                    for tgt in sub.targets:
                        if isinstance(tgt, ast.Name):
                            if tgt.id == "_w607cp_warnings_out":
                                cp_found = True
                            elif tgt.id == "_w607en_warnings_out":
                                en_found = True
                continue
            tgt = sub.target
            if isinstance(tgt, ast.Name):
                if tgt.id == "_w607cp_warnings_out":
                    cp_found = True
                elif tgt.id == "_w607en_warnings_out":
                    en_found = True

    assert cp_found, (
        "AST audit: ``_w607cp_warnings_out`` accumulator not assigned inside the hotspots click command function body."
    )
    assert en_found, (
        "AST audit: ``_w607en_warnings_out`` accumulator not assigned inside the hotspots click command function body."
    )


# ---------------------------------------------------------------------------
# (24) Helper marker template is shared -- both helpers use hotspots_* family
# ---------------------------------------------------------------------------


def test_both_helpers_use_hotspots_marker_family():
    """Source-level audit: both ``_run_check_cp`` and ``_run_check_en``
    must emit markers under the canonical ``hotspots_*`` family so a
    consumer regex spans both layers without rework.

    The marker construction now lives in the shared ``boundary_helpers``
    module; each wrapper pins the recipe name (``hotspots``) at the
    ``make_run_check`` call site.
    """
    cmd_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_hotspots.py"
    helper_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "boundary_helpers.py"
    cmd_src = cmd_src_path.read_text(encoding="utf-8")
    helper_src = helper_src_path.read_text(encoding="utf-8")

    # Generic marker template lives in the shared helper.
    assert 'f"{recipe_name}_{phase}_failed:{type(exc).__name__}:{exc}"' in helper_src, (
        "Canonical W607 marker f-string template must live in boundary_helpers."
    )
    # Both wrappers route through the helper with the hotspots recipe name.
    assert cmd_src.count('make_run_check("hotspots",') >= 2, (
        "Both ``_run_check_cp`` and ``_run_check_en`` must emit markers "
        "under the canonical hotspots_<phase>_failed:<exc>:<detail> "
        'family; expected both wrappers to call make_run_check("hotspots", ...).'
    )


# ---------------------------------------------------------------------------
# (25) Detector-family 11-way agg-layer closure pin
# ---------------------------------------------------------------------------


def test_detector_family_11_way_agg_layer_closure():
    """Detector-family 11-way pairing pin: AST-scan all 11 detectors
    confirming each carries BOTH a substrate-CALL layer accumulator AND
    an aggregation-layer accumulator (or, where the wave description
    pairs only at one layer, the documented single-layer accumulator).

    Roster (11-way agg-layer closure with cmd_hotspots W607-EN landing):

      auth_gaps      CM + ED
      n1             CB + DQ
      over_fetch     CE + DT
      missing_index  CI + DX
      smells         BN + DF
      clones         BQ + DC
      duplicates     BM + DD
      dead           BX + DL
      bus_factor     CQ + EH
      taint          AY + CJ
      vulns          AQ + CH
      hotspots       CP + EN  (THIS WAVE)
    """
    commands_dir = Path(__file__).parent.parent / "src" / "roam" / "commands"
    pairings: list[tuple[str, str, str]] = [
        ("cmd_auth_gaps.py", "cm", "ed"),
        ("cmd_n1.py", "cb", "dq"),
        ("cmd_over_fetch.py", "ce", "dt"),
        ("cmd_missing_index.py", "ci", "dx"),
        ("cmd_smells.py", "bn", "df"),
        ("cmd_clones.py", "bq", "dc"),
        ("cmd_duplicates.py", "bm", "dd"),
        ("cmd_dead.py", "bx", "dl"),
        ("cmd_bus_factor.py", "cq", "eh"),
        ("cmd_taint.py", "ay", "cj"),
        ("cmd_vulns.py", "aq", "ch"),
        ("cmd_hotspots.py", "cp", "en"),
    ]

    missing: list[str] = []
    for filename, sub_prefix, agg_prefix in pairings:
        path = commands_dir / filename
        assert path.exists(), f"missing source file: {path}"
        src = path.read_text(encoding="utf-8")
        sub_acc = f"_w607{sub_prefix}_warnings_out"
        agg_acc = f"_w607{agg_prefix}_warnings_out"
        if sub_acc not in src:
            missing.append(f"{filename}: substrate layer {sub_acc} missing")
        if agg_acc not in src:
            missing.append(f"{filename}: aggregation layer {agg_acc} missing")

    assert not missing, "Detector-family 11-way agg-layer closure broken:\n" + "\n".join(missing)


# ---------------------------------------------------------------------------
# (26) W120 findings-registry persist still wired (preserved invariant)
# ---------------------------------------------------------------------------


def test_w120_persist_path_preserved_under_w607en_layering(cli_runner, hotspots_project_with_traces, tmp_path):
    """W120 --persist mirrors runtime hotspots into the findings registry.
    The additive W607-EN aggregation-phase plumbing must NOT touch that
    wiring -- a --persist invocation on a populated fixture must still
    produce a coherent envelope (the persist code path itself is gated
    on a runtime-resolved symbol, which the fixture provides).
    """
    result = _invoke_hotspots(cli_runner, hotspots_project_with_traces, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "hotspots"
    # The verdict / summary must still emit
    assert isinstance(data["summary"].get("verdict"), str), data["summary"]


# ---------------------------------------------------------------------------
# (27) W816 empty-corpus preservation -- no-traces still emits explicit verdict
# ---------------------------------------------------------------------------


def test_w816_no_traces_state_preserved_under_w607en(cli_runner, hotspots_project_no_traces):
    """W816 empty-corpus seal: the no-traces path emits ``partial_success:
    True`` with an explicit zero-count verdict + named ``state`` field.
    The W607-EN aggregation layer must not regress this contract.
    """
    result = _invoke_hotspots(cli_runner, hotspots_project_no_traces)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    # Named state must be present (W816 seal)
    assert summary.get("state") in {"no_traces", "table_missing"}, (
        f"W816 seal: no-traces path must surface a named state; got summary={summary!r}"
    )
    # Partial-success guard preserved (Pattern-2 contract)
    assert summary.get("partial_success") is True, (
        f"W816 seal: no-traces path must flip partial_success=True; got summary={summary!r}"
    )
    # Verdict still a single line
    verdict = summary.get("verdict") or ""
    assert "\n" not in verdict, verdict
