"""W607-CB -- ``cmd_n1`` substrate-boundary plumbing.

cmd_n1 is the implicit N+1 query detector (W110 origin per CLAUDE.md
detector roster -- part of the original 16 findings-registry
substrate detectors). The detector has multiple layered substrates:
B2 controller-file cache (W80), B3 bulk-fetch helpers (W86), and B3.5
candidate-filter (W91). W805 sealed the Pattern-2 empty-state regression
(``empty_corpus`` / ``no_models`` named states) but until this wave the
command had no substrate-boundary marker plumbing -- a raise in
``analyze_n1`` (the core 6-tuple aggregation) would crash the n1
command outright.

This wave installs the canonical ``_w607cb_warnings_out`` bucket +
``_run_check_cb`` helper inside the ``n1`` click command and wraps
every substrate boundary:

* analyze_n1                -- core 6-tuple aggregation (analogue of
                              _analyze_dead from cmd_dead BX)
* find_model_classes        -- empty-state model counter
* symbol_count_query        -- empty-state symbol-table COUNT
* emit_findings             -- W110 findings-registry mirror
                              (sqlite3.OperationalError silent no-op
                              preserved for pre-W89 DB)
* serialize_to_sarif        -- SARIF projection
* sort_findings             -- confidence-rank sort
* aggregate_by_confidence   -- by-confidence histogram
* derive_distribution       -- R22 wrap_findings + distribution
* group_by_model            -- text-mode grouping

Marker family ``n1_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test (n1 is part of the DETECTOR FAMILY 6-WAY with
smells / vibe-check / clones / duplicates / dead).

W805 PATTERN-2 REGRESSION GUARD
-------------------------------

W803 confirmed the cmd_n1 empty-corpus smoke had no Pattern-2 gap;
W805 sealed the named-empty-state envelopes (``empty_corpus`` /
``no_models``). The regression-guard tests below confirm:

  1. The clean empty corpus path still emits ``partial_success: True``
     with ``state: empty_corpus`` (W805 invariant preserved).
  2. The W607-CB substrate boundary on ``analyze_n1`` does NOT
     re-introduce Pattern-2 silent-fallback -- a raise in
     ``analyze_n1`` still emits a non-empty envelope with a marker
     AND ``partial_success: True``, never a SAFE verdict on a
     degraded state.

DETECTOR FAMILY 6-WAY PAIRING
-----------------------------

The bonus pairing test confirms each marker family stays inside its
own prefix without leaking across detector boundaries (n1 + smells +
vibe-check + clones + duplicates + dead == 6 detectors).
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


def _build_n1_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_n1.

    Builds a Laravel-flavoured fixture with a model class + appended
    accessor so analyze_n1 has something to look at. The detector may or
    may not find a high-confidence N+1 finding depending on bulk-fetch
    coverage -- the tests below tolerate either outcome and focus on
    the W607-CB marker plumbing rather than the detector verdict.
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
        CREATE TABLE IF NOT EXISTS git_commits (
            id INTEGER PRIMARY KEY, hash TEXT NOT NULL UNIQUE,
            author TEXT, timestamp INTEGER, message TEXT
        );
        CREATE TABLE IF NOT EXISTS git_file_changes (
            id INTEGER PRIMARY KEY, commit_id INTEGER NOT NULL,
            file_id INTEGER, path TEXT NOT NULL,
            lines_added INTEGER DEFAULT 0, lines_removed INTEGER DEFAULT 0
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
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def n1_project(tmp_path):
    return _build_n1_project(tmp_path)


def _invoke_n1(cli_runner, project_root, *args, json_mode=True, sarif=False):
    """Invoke the n1 click command directly (bypassing the CLI group)."""
    from roam.commands.cmd_n1 import n1_cmd

    obj = {"json": json_mode, "sarif": sarif, "budget": 0}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(n1_cmd, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_CB_PHASES = (
    "analyze_n1",
    "find_model_classes",
    "symbol_count_query",
    "emit_findings",
    "serialize_to_sarif",
    "sort_findings",
    "aggregate_by_confidence",
    "derive_distribution",
    "group_by_model",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CB substrate markers
# ---------------------------------------------------------------------------


def test_n1_clean_envelope_omits_w607cb_markers(cli_runner, n1_project):
    """Clean n1 run -> no W607-CB substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-CB bucket on
    the success path must NOT introduce new ``n1_<phase>_failed:``
    markers tied to the W607-CB wrap.
    """
    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "n1"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    cb_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"n1_{p}_failed:" in m for p in _CB_PHASES)]
    assert not cb_markers, (
        f"clean n1 must NOT surface W607-CB substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) analyze_n1 failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_n1_analyze_failure_marker_format(cli_runner, n1_project, monkeypatch):
    """If ``analyze_n1`` raises, surface the canonical 3-segment marker."""
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-analyze-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "analyze_n1", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    analyze_markers = [m for m in all_wo if m.startswith("n1_analyze_n1_failed:")]
    assert analyze_markers, f"expected n1_analyze_n1_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in analyze_markers), analyze_markers
    assert any("synthetic-analyze-from-W607-CB" in m for m in analyze_markers), analyze_markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"analyze-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # LAW 6: the verdict still appears as a single line.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_n1_w607cb_warnings_in_envelope(cli_runner, n1_project, monkeypatch):
    """Non-empty W607-CB bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "analyze_n1", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CB disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CB disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("n1_analyze_n1_failed:")]
    assert markers, f"expected n1_analyze_n1_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_n1_three_segment_marker_shape(cli_runner, n1_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "analyze_n1", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("n1_analyze_n1_failed:")]
    assert failure_markers, f"expected n1_analyze_n1_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "n1_analyze_n1_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) find_model_classes failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_n1_find_model_classes_failure_degrades_cleanly(cli_runner, n1_project, monkeypatch):
    """A raise in ``_find_model_classes`` must NOT crash the n1 command."""
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-models-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "_find_model_classes", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    models_markers = [m for m in all_wo if m.startswith("n1_find_model_classes_failed:")]
    assert models_markers, f"expected n1_find_model_classes_failed: marker; got {all_wo!r}"
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-CB stays in ``n1_*`` family
# ---------------------------------------------------------------------------


def test_w607cb_marker_prefix_stays_in_n1_family(cli_runner, n1_project, monkeypatch):
    """Every W607-CB substrate marker uses the canonical ``n1_*`` prefix.

    Hard distinction from sibling W607-* layers including cmd_smells
    (W607-BN, ``smells_*``), cmd_vibe_check (W607-BS, ``vibe_check_*``),
    cmd_clones (W607-BQ, ``clones_*``), cmd_duplicates (W607-BM,
    ``duplicates_*``), and cmd_dead (W607-BX, ``dead_*``). cmd_n1 is
    the sixth detector in the family quartet -- a leaking marker would
    cross the detector-family boundary.
    """
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "analyze_n1", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("n1_"), (
            f"every surfaced W607-CB marker must use the ``n1_*`` prefix family (cmd_n1 scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("smells_", "cmd_smells W607-BN (detector sibling)"),
            ("vibe_check_", "cmd_vibe_check W607-BS (LLM-rot detector)"),
            ("clones_", "cmd_clones W607-BQ (clone detector)"),
            ("duplicates_", "cmd_duplicates W607-BM (duplicates detector)"),
            ("dead_", "cmd_dead W607-BX (dead-code detector)"),
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
# (7) Source-level guard: cmd_n1 carries the W607-CB accumulator
# ---------------------------------------------------------------------------


def test_cmd_n1_carries_w607cb_accumulator():
    """AST-level guard: cmd_n1 source carries the W607-CB accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_n1.py"
    assert src_path.exists(), f"cmd_n1.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607cb_warnings_out" in src, (
        "W607-CB accumulator missing from cmd_n1; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_cb" in src, (
        "W607-CB ``_run_check_cb`` helper missing from cmd_n1; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_cb = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cb":
            found_run_check_cb = True
            break
    assert found_run_check_cb, (
        "W607-CB ``_run_check_cb`` helper not found in cmd_n1 AST; the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-CB substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607cb_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-CB substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_n1.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _CB_PHASES:
        same_line = f'_run_check_cb("{phase}"' in src
        multi_line = (
            f'_run_check_cb(\n        "{phase}"' in src
            or f'_run_check_cb(\n            "{phase}"' in src
            or f'_run_check_cb(\n                "{phase}"' in src
            or f'_run_check_cb(\n                    "{phase}"' in src
            or f'_run_check_cb(\n                        "{phase}"' in src
        )
        # emit_findings is wrapped via direct try/except (NOT _run_check_cb)
        # because it needs to distinguish sqlite3.OperationalError (expected
        # pre-W89 path) from generic Exception (W607-CB marker). Source-grep
        # on the marker name in that case.
        marker_grep = f"n1_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-CB wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) emit_findings failure -> marker surfaces, n1 command still emits
# ---------------------------------------------------------------------------


def test_n1_emit_findings_failure_surfaces_marker(cli_runner, n1_project, monkeypatch):
    """W110 emit failure (non-OperationalError) surfaces W607-CB marker.

    sqlite3.OperationalError is the EXPECTED pre-W89 path (silent
    no-op). Generic exceptions surface via the W607-CB marker so a real
    bug in the persist substrate is loud, not silent.
    """
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "_emit_n1_findings", _raise)

    result = _invoke_n1(cli_runner, n1_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("n1_emit_findings_failed:")]
    assert emit_markers, f"expected n1_emit_findings_failed: marker; got {all_wo!r}"
    # The n1 command still emits a clean envelope past the
    # registry-mirror failure -- W110 is additive, not load-bearing.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) emit_findings OperationalError path stays silent (no W607-CB marker)
# ---------------------------------------------------------------------------


def test_n1_emit_findings_operational_error_stays_silent(cli_runner, n1_project, monkeypatch):
    """W607-CB MUST preserve the W110 silent no-op contract on
    ``sqlite3.OperationalError`` (pre-W89 schema -- no findings table).

    The marker MUST NOT surface for this expected degraded path.
    """
    from roam.commands import cmd_n1

    def _raise_op_err(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings (pre-W89 schema)")

    monkeypatch.setattr(cmd_n1, "_emit_n1_findings", _raise_op_err)

    result = _invoke_n1(cli_runner, n1_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("n1_emit_findings_failed:")]
    assert not emit_markers, (
        f"sqlite3.OperationalError is the EXPECTED pre-W89 silent "
        f"no-op path; W607-CB marker MUST NOT surface; "
        f"got {emit_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) derive_distribution failure -> empty R22 wrap, envelope composes
# ---------------------------------------------------------------------------


def test_n1_derive_distribution_failure_degrades_cleanly(cli_runner, n1_project, monkeypatch):
    """A raise in R22 wrap_findings degrades to empty triples + empty dist."""
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-distribution-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "wrap_findings", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    dist_markers = [m for m in all_wo if m.startswith("n1_derive_distribution_failed:")]
    assert dist_markers, f"expected n1_derive_distribution_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (12) W805 PATTERN-2 REGRESSION GUARD: empty-state branch invariant
# ---------------------------------------------------------------------------


def test_w805_empty_state_partial_success_preserved(cli_runner, tmp_path):
    """W805 regression guard: empty corpus -> partial_success: True
    with ``state: empty_corpus``.

    W803 confirmed cmd_n1 empty-corpus smoke had no Pattern-2 gap;
    W805 sealed the named-empty-state envelopes. The W607-CB plumbing
    must NOT re-introduce that bug: when no markers fire, the empty-
    corpus envelope MUST keep ``state: empty_corpus`` AND
    ``partial_success: True`` so MCP consumers can distinguish
    "nothing to flag" from "degraded".
    """
    # Build empty corpus (no symbols at all).
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "init",
            "-q",
        ],
        cwd=tmp_path,
        capture_output=True,
    )

    from roam.cli import cli

    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        init_result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert init_result.exit_code == 0, init_result.output
        result = runner.invoke(cli, ["--json", "n1"], catch_exceptions=False)
    finally:
        os.chdir(cwd)

    assert result.exit_code == 0, result.output
    data = _json.loads(result.output) if result.output.strip() else {}
    summary = data.get("summary") or {}

    # W805 invariant: state present, partial_success True on empty corpus.
    state = summary.get("state")
    assert state in ("empty_corpus", "no_models"), (
        f"empty-corpus envelope MUST carry named state "
        f"(``empty_corpus`` or ``no_models``); got state={state!r}, "
        f"summary={summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"empty-corpus partial_success must be True (W805); got summary={summary!r}"
    )

    # And no W607-CB markers fired (since nothing raised).
    top_wo = data.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    cb_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"n1_{p}_failed:" in m for p in _CB_PHASES)]
    assert not cb_markers, f"clean empty corpus must NOT surface W607-CB markers; got {cb_markers!r}"


def test_w805_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, n1_project, monkeypatch):
    """W805 Pattern-2 regression guard on the degraded-empty path.

    If ``analyze_n1`` raises, the empty-floor default kicks in
    (findings == [], framework == "generic") and the empty-state
    envelope is emitted. The W607-CB wrap MUST flip
    ``partial_success: True`` on that branch so the empty-state
    envelope is NOT mistaken for a clean "no implicit N+1 patterns"
    verdict (the classic Pattern-2 silent-fallback bug).
    """
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-W805-pattern-2-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "analyze_n1", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    # The empty-floor default takes us into the empty-state envelope
    # path -- AND the marker must surface, AND partial_success: True.
    assert summary.get("partial_success") is True, (
        f"degraded-empty path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )

    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    analyze_markers = [m for m in all_wo if m.startswith("n1_analyze_n1_failed:")]
    assert analyze_markers, (
        f"degraded-empty path MUST surface the analyze_n1 marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (13) DETECTOR FAMILY 6-WAY pairing bonus
# ---------------------------------------------------------------------------


def test_detector_family_6way_marker_prefixes_coexist(cli_runner, n1_project, monkeypatch):
    """DETECTOR FAMILY 6-WAY pairing bonus.

    Confirm ``n1_<phase>_failed:`` markers coexist with
    ``smells_*`` (W607-BN), ``vibe_check_*`` (W607-BS), ``clones_*``
    (W607-BQ), ``duplicates_*`` (W607-BM), and ``dead_*`` (W607-BX)
    markers without cross-prefix leakage.

    This is the load-bearing prefix-discipline test for the detector
    family sextet: each command's marker family stays inside its own
    prefix so a downstream finder/grep on ``n1_*`` markers picks up
    ONLY the N+1-detector substrate failures.
    """
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-6way-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "analyze_n1", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # The n1 marker fires.
    assert any(m.startswith("n1_analyze_n1_failed:") for m in all_wo), all_wo

    # None of the five detector-sibling prefixes leak into the n1
    # envelope.
    for forbidden_prefix in (
        "smells_",
        "vibe_check_",
        "clones_",
        "duplicates_",
        "dead_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on detector-family 6-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_n1 envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (14) Sort failure -> unsorted findings, envelope composes
# ---------------------------------------------------------------------------


def test_n1_sort_findings_failure_degrades_cleanly(cli_runner, n1_project, monkeypatch):
    """A raise inside the sort comparator must NOT crash the n1 command.

    Simulates a malformed confidence field by monkeypatching
    ``confidence_level_rank`` to raise. The findings stay unsorted on
    the degraded path, but the envelope still emits with a marker.
    """
    from roam.commands import cmd_n1

    # Have analyze_n1 return one finding so the sort phase actually
    # executes -- the empty-list path skips the comparator and would
    # not exercise the wrap.
    def _fake_analyze(*args, **kwargs):
        return (
            [
                {
                    "model_name": "FakeModel",
                    "model_location": "src/m.py:1",
                    "accessor_name": "fake",
                    "accessor_location": "src/m.py:2",
                    "appended_attribute": "fake_attr",
                    "relationship": "fake_rel",
                    "io_type": "db",
                    "eager_loaded": False,
                    "confidence": "high",
                    "severity": "per-item query on serialization",
                    "collection_contexts": [],
                    "suggestion": "fix it",
                }
            ],
            "generic",
        )

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-sort-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "analyze_n1", _fake_analyze)
    monkeypatch.setattr(cmd_n1, "confidence_level_rank", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    sort_markers = [m for m in all_wo if m.startswith("n1_sort_findings_failed:")]
    assert sort_markers, f"expected n1_sort_findings_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (15) AST source-level guard: marker prefix is the canonical
#      ``n1_<phase>_failed:<exc_class>:<detail>`` shape
# ---------------------------------------------------------------------------


def test_w607cb_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-CB marker shape lives in cmd_n1."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_n1.py"
    src = src_path.read_text(encoding="utf-8")
    # The fstring template
    # ``f"n1_{phase}_failed:{type(exc).__name__}:{exc}"`` MUST appear
    # exactly once -- the canonical marker construction site. Any
    # divergence from this shape (e.g., a missing colon, mis-spelled
    # prefix) would break consumer parsers.
    fstring_pattern = 'f"n1_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, f"canonical W607-CB marker fstring missing from cmd_n1; expected: {fstring_pattern}"


# ---------------------------------------------------------------------------
# (16) SARIF projection failure -> marker surfaces on CI path
# ---------------------------------------------------------------------------


def test_n1_sarif_failure_surfaces_marker(cli_runner, n1_project, monkeypatch):
    """A raise in the SARIF projection must NOT crash the n1 CI path.

    The SARIF projection is wrapped so a writer exception is contained
    -- the click command still returns cleanly without a traceback. By
    design SARIF mode short-circuits the envelope (writes pure SARIF
    to stdout), so we verify exit_code only on the smoke-test axis;
    the marker accumulator stays in-process but is not flushed to a
    second envelope.
    """

    # Patch the n1_to_sarif import target -- the import happens inside
    # the click command at call time, so we patch the module attribute
    # via the sarif output module.
    from roam.output import sarif as sarif_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-sarif-from-W607-CB")

    monkeypatch.setattr(sarif_mod, "n1_to_sarif", _raise)

    result = _invoke_n1(cli_runner, n1_project, json_mode=False, sarif=True)
    # The W607-CB wrap protects against crash even on the SARIF path.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (17) aggregate_by_confidence failure -> empty histogram, envelope composes
# ---------------------------------------------------------------------------


def test_n1_aggregate_by_confidence_failure_degrades_cleanly(cli_runner, n1_project, monkeypatch):
    """A raise in the by-confidence aggregator degrades to ``{}``."""
    from roam.commands import cmd_n1

    # Have analyze_n1 return one finding with a malformed confidence
    # value -- the aggregator's ``f["confidence"]`` lookup will succeed
    # but a downstream defaultdict failure won't happen organically;
    # we instead patch defaultdict at the module level to raise on
    # __setitem__ so the histogram comprehension fails.
    def _fake_analyze(*args, **kwargs):
        # Provide a finding whose ``confidence`` key is *missing*
        # entirely -- the lambda ``f["confidence"]`` lookup will raise
        # KeyError inside the aggregate substrate.
        return (
            [
                {
                    "model_name": "FakeModel",
                    "model_location": "src/m.py:1",
                    "accessor_name": "fake",
                    "accessor_location": "src/m.py:2",
                    "appended_attribute": "fake_attr",
                    "relationship": "fake_rel",
                    "io_type": "db",
                    "eager_loaded": False,
                    # NO "confidence" key -- aggregator KeyErrors.
                    "severity": "per-item query on serialization",
                    "collection_contexts": [],
                    "suggestion": "fix it",
                }
            ],
            "generic",
        )

    monkeypatch.setattr(cmd_n1, "analyze_n1", _fake_analyze)
    # Skip the sort step -- it would also raise on missing confidence
    # and steal the failure-attribution. Patch confidence_level_rank
    # to a no-op so sort succeeds (returns same order).
    monkeypatch.setattr(cmd_n1, "confidence_level_rank", lambda *_a, **_k: 0)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    agg_markers = [m for m in all_wo if m.startswith("n1_aggregate_by_confidence_failed:")]
    assert agg_markers, f"expected n1_aggregate_by_confidence_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (18) Per-language isolation bonus: analyze_n1 fails on one framework,
#      empty-floor degrades and envelope composes cleanly
# ---------------------------------------------------------------------------


def test_n1_per_framework_isolation_on_analyze_failure(cli_runner, n1_project, monkeypatch):
    """Per-framework isolation: analyze_n1 raise -> empty floor + marker.

    Simulates the analyze_n1 substrate raising as if one framework's
    ORM call detection blew up. The W607-CB wrap returns the empty
    floor ``([], "generic")``, the envelope STILL emits with the
    ``no_models`` empty state (because models_scanned probes
    independently), the marker surfaces, and partial_success flips.

    The key invariant: a raise in one substrate does NOT pollute other
    substrate outputs -- models_scanned still queries cleanly, the
    empty-state classifier still names the absent state, and the
    envelope stays load-bearing.
    """
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-per-framework-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "analyze_n1", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    # analyze_n1 marker fires.
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    assert any(m.startswith("n1_analyze_n1_failed:") for m in all_wo), (
        f"expected n1_analyze_n1_failed marker; got {all_wo!r}"
    )

    # Other substrates still produced clean output -- the empty-state
    # classifier still ran and named the absent state.
    assert summary.get("state") in ("empty_corpus", "no_models", "scanned"), (
        f"empty-state classifier should still run cleanly past an "
        f"analyze_n1 failure; got state={summary.get('state')!r}"
    )
    assert summary.get("partial_success") is True

    # No cross-substrate marker pollution -- only the analyze_n1
    # marker fires, not find_model_classes / symbol_count_query.
    other_substrate_markers = [
        m
        for m in all_wo
        if m.startswith("n1_find_model_classes_failed:") or m.startswith("n1_symbol_count_query_failed:")
    ]
    assert not other_substrate_markers, (
        f"analyze_n1 substrate failure must NOT pollute other substrate markers; got {other_substrate_markers!r}"
    )


# ---------------------------------------------------------------------------
# (19) B3 bulk-fetch substrate bonus: find_model_classes raise
#      degrades gracefully and surfaces marker
# ---------------------------------------------------------------------------


def test_n1_b3_bulk_fetch_fallback_surfaces_marker(cli_runner, n1_project, monkeypatch):
    """B3 bulk-fetch fallback regression guard.

    cmd_n1 has multi-layered bulk-fetch substrates (W86 B3 +
    W91 B3.5). The outermost ``_find_model_classes`` call is the
    canonical bulk-fetch entry point -- a raise here used to crash
    the n1 command. The W607-CB wrap degrades to ``{}`` (no models),
    the empty-state classifier names ``no_models`` (or
    ``empty_corpus`` depending on the symbol count probe), and the
    marker surfaces so the agent learns the bulk-fetch substrate
    failed.

    Hard distinction from cmd_dead's W157 seeding-failed test: the
    n1 bulk-fetch fallback degrades to an EMPTY model dict (no
    seeds = no analysis), and the empty-state envelope makes that
    visible via the named ``no_models`` state.
    """
    from roam.commands import cmd_n1

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-W86-B3-from-W607-CB")

    monkeypatch.setattr(cmd_n1, "_find_model_classes", _raise)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    bulk_markers = [m for m in all_wo if m.startswith("n1_find_model_classes_failed:")]
    assert bulk_markers, f"expected n1_find_model_classes_failed: marker; got {all_wo!r}"
    # Crucial: partial_success=True so the empty-floor branch is NOT
    # mistaken for a clean "no implicit N+1 patterns" success (the
    # canonical Pattern-2 silent-fallback hazard for B3 bulk-fetch
    # regressions). models_scanned probes the EMPTY floor (W607-CB
    # default is {}).
    assert summary.get("partial_success") is True
    assert summary.get("models_scanned") == 0
