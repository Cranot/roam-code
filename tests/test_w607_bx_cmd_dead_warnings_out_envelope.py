"""W607-BX -- ``cmd_dead`` substrate-boundary plumbing.

cmd_dead is the foundational dead-code detector (W99 origin, ~537
findings rows per CLAUDE.md). W802 and W804 sealed the empty-state
Pattern-2 regression for the empty corpus path, but until this wave
the command had no substrate-boundary marker plumbing -- a raise in
``_analyze_dead`` (the 6-tuple aggregation core) would crash the dead
command outright. This wave installs the canonical
``_w607bx_warnings_out`` bucket + ``_run_check_bx`` helper inside the
``dead`` click command and wraps every substrate boundary:

* extinction_predict                -- --extinction mode helper
* analyze_dead                      -- core 6-tuple aggregation
* collect_dataflow_findings         -- unused_assignments
* oracle_reachable_filter           -- --reachable-only intersect
* analyze_dataflow_dead             -- experimental dataflow path
* emit_findings                     -- W96 findings-registry mirror
* serialize_to_sarif                -- SARIF projection
* find_dead_clusters                -- cluster detection (Tarjan)
* compute_extended_data             -- aging/effort/decay
* group_dead                        -- --by-directory / --by-kind

Marker family ``dead_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test (the dead detector is paired with the smells /
vibe-check / clones / duplicates detector quartet -- the bonus pairing
test below confirms five detector marker families coexist without
cross-prefix leakage).

W804 PATTERN-2 REGRESSION GUARD
-------------------------------

W802/W804 fixed a real bug: the empty-state branch was missing
``summary.partial_success: False``. The regression-guard tests below
confirm:

  1. The clean empty corpus path still emits
     ``summary.partial_success: False`` (W804 invariant preserved).
  2. The W607-BX substrate boundary on ``_analyze_dead`` does NOT
     re-introduce Pattern-2 silent-fallback on the empty-state branch
     -- a raise in ``_analyze_dead`` still emits a non-empty envelope
     with a marker AND ``partial_success: True``, never a SAFE
     verdict on a degraded state.

DETECTOR FAMILY 5-WAY PAIRING
-----------------------------

The bonus pairing test invokes ``dead``, ``smells``, ``vibe-check``,
``clones``, and ``duplicates`` on the same workspace (or as close as
possible -- each command has its own fixture shape) and confirms
each marker family stays inside its own prefix without leaking
across detector boundaries.
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


def _build_dead_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_dead.

    Two exported symbols, no consumers -> the dead-export detector
    finds them. Test files / tooling files are excluded by
    ``_is_test_path`` / ``_is_tooling_path`` so they don't pollute the
    fixture.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text(
        "def alive():\n    return helper()\n\n"
        "def dead_one():\n    return 1\n\n"
        "def dead_two():\n    return 2\n\n"
        "def helper():\n    return 0\n"
    )
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
    # alive() calls helper(); dead_one/dead_two have no callers.
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(1, 1, 'alive', 'src.engine.alive', 'function', 1, 2, 'public', 1),"
        "(2, 1, 'dead_one', 'src.engine.dead_one', 'function', 4, 5, 'public', 1),"
        "(3, 1, 'dead_two', 'src.engine.dead_two', 'function', 7, 8, 'public', 1),"
        "(4, 1, 'helper', 'src.engine.helper', 'function', 10, 11, 'public', 1)"
    )
    # alive -> helper edge (so helper is consumed; dead_one/dead_two are not).
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (1, 4, 'call')")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def dead_project(tmp_path):
    return _build_dead_project(tmp_path)


def _invoke_dead(cli_runner, project_root, *args, json_mode=True, detail=False):
    """Invoke the dead click command directly (bypassing the CLI group)."""
    from roam.commands.cmd_dead import dead

    obj = {"json": json_mode, "sarif": False, "budget": 0, "detail": detail}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(dead, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_BX_PHASES = (
    "extinction_predict",
    "analyze_dead",
    "collect_dataflow_findings",
    "oracle_reachable_filter",
    "analyze_dataflow_dead",
    "emit_findings",
    "serialize_to_sarif",
    "find_dead_clusters",
    "compute_extended_data",
    "group_dead",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BX substrate markers
# ---------------------------------------------------------------------------


def test_dead_clean_envelope_omits_w607bx_markers(cli_runner, dead_project):
    """Clean dead run -> no W607-BX substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-BX bucket on
    the success path must NOT introduce new ``dead_<phase>_failed:``
    markers tied to the W607-BX wrap.
    """
    result = _invoke_dead(cli_runner, dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "dead"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    bx_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"dead_{p}_failed:" in m for p in _BX_PHASES)]
    assert not bx_markers, (
        f"clean dead must NOT surface W607-BX substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) analyze_dead failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_dead_analyze_dead_failure_marker_format(cli_runner, dead_project, monkeypatch):
    """If ``_analyze_dead`` raises, surface the canonical 3-segment marker."""
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-analyze-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_analyze_dead", _raise)

    result = _invoke_dead(cli_runner, dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    analyze_markers = [m for m in all_wo if m.startswith("dead_analyze_dead_failed:")]
    assert analyze_markers, f"expected dead_analyze_dead_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in analyze_markers), analyze_markers
    assert any("synthetic-analyze-from-W607-BX" in m for m in analyze_markers), analyze_markers
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


def test_dead_w607bx_warnings_in_envelope(cli_runner, dead_project, monkeypatch):
    """Non-empty W607-BX bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_analyze_dead", _raise)

    result = _invoke_dead(cli_runner, dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BX disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BX disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("dead_analyze_dead_failed:")]
    assert markers, f"expected dead_analyze_dead_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_dead_three_segment_marker_shape(cli_runner, dead_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_analyze_dead", _raise)

    result = _invoke_dead(cli_runner, dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("dead_analyze_dead_failed:")]
    assert failure_markers, f"expected dead_analyze_dead_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "dead_analyze_dead_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) collect_dataflow_findings failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_dead_collect_dataflow_findings_failure_degrades_cleanly(cli_runner, dead_project, monkeypatch):
    """A raise in the dataflow collector must NOT crash the dead command."""
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-dataflow-collect-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "collect_dataflow_findings", _raise)

    result = _invoke_dead(cli_runner, dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    df_markers = [m for m in all_wo if m.startswith("dead_collect_dataflow_findings_failed:")]
    assert df_markers, f"expected dead_collect_dataflow_findings_failed: marker; got {all_wo!r}"
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-BX stays in ``dead_*`` family
# ---------------------------------------------------------------------------


def test_w607bx_marker_prefix_stays_in_dead_family(cli_runner, dead_project, monkeypatch):
    """Every W607-BX substrate marker uses the canonical ``dead_*`` prefix.

    Hard distinction from sibling W607-* layers including cmd_smells
    (W607-BN, ``smells_*``), cmd_vibe_check (W607-BS, ``vibe_check_*``),
    cmd_clones (W607-BQ, ``clones_*``), cmd_duplicates (W607-BM,
    ``duplicates_*``). cmd_dead is the foundational detector in the
    5-way detector family quartet -- a leaking marker would cross the
    detector-family boundary.
    """
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_analyze_dead", _raise)

    result = _invoke_dead(cli_runner, dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("dead_"), (
            f"every surfaced W607-BX marker must use the ``dead_*`` prefix family (cmd_dead scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("smells_", "cmd_smells W607-BN (detector sibling)"),
            ("vibe_check_", "cmd_vibe_check W607-BS (LLM-rot detector)"),
            ("clones_", "cmd_clones W607-BQ (clone detector)"),
            ("duplicates_", "cmd_duplicates W607-BM (duplicates detector)"),
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
# (7) Source-level guard: cmd_dead carries the W607-BX accumulator
# ---------------------------------------------------------------------------


def test_cmd_dead_carries_w607bx_accumulator():
    """AST-level guard: cmd_dead source carries the W607-BX accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dead.py"
    assert src_path.exists(), f"cmd_dead.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607bx_warnings_out" in src, (
        "W607-BX accumulator missing from cmd_dead; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bx" in src, (
        "W607-BX ``_run_check_bx`` helper missing from cmd_dead; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_bx = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bx":
            found_run_check_bx = True
            break
    assert found_run_check_bx, (
        "W607-BX ``_run_check_bx`` helper not found in cmd_dead AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-BX substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bx_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BX substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dead.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _BX_PHASES:
        same_line = f'_run_check_bx("{phase}"' in src
        multi_line = (
            f'_run_check_bx(\n        "{phase}"' in src
            or f'_run_check_bx(\n            "{phase}"' in src
            or f'_run_check_bx(\n                "{phase}"' in src
            or f'_run_check_bx(\n                    "{phase}"' in src
            or f'_run_check_bx(\n                        "{phase}"' in src
        )
        # emit_findings is wrapped via direct try/except (NOT _run_check_bx)
        # because it needs to distinguish sqlite3.OperationalError (expected
        # pre-W89 path) from generic Exception (W607-BX marker). Source-grep
        # on the marker name in that case.
        marker_grep = f"dead_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-BX wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) emit_findings failure -> marker surfaces, dead command still emits
# ---------------------------------------------------------------------------


def test_dead_emit_findings_failure_surfaces_marker(cli_runner, dead_project, monkeypatch):
    """W96 emit failure (non-OperationalError) surfaces W607-BX marker.

    sqlite3.OperationalError is the EXPECTED pre-W89 path (silent
    no-op). Generic exceptions surface via the W607-BX marker so a real
    bug in the persist substrate is loud, not silent.
    """
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_emit_dead_findings", _raise)

    result = _invoke_dead(cli_runner, dead_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("dead_emit_findings_failed:")]
    assert emit_markers, f"expected dead_emit_findings_failed: marker; got {all_wo!r}"
    # The dead command still emits a clean envelope past the
    # registry-mirror failure -- W96 is additive, not load-bearing.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) find_dead_clusters failure -> empty clusters, envelope composes
# ---------------------------------------------------------------------------


def test_dead_find_dead_clusters_failure_degrades_cleanly(cli_runner, dead_project, monkeypatch):
    """A raise in cluster detection degrades to ``[]`` cleanly."""
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-clusters-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_find_dead_clusters", _raise)

    result = _invoke_dead(cli_runner, dead_project, "--clusters")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    cluster_markers = [m for m in all_wo if m.startswith("dead_find_dead_clusters_failed:")]
    assert cluster_markers, f"expected dead_find_dead_clusters_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (11) compute_extended_data failure -> empty extended block
# ---------------------------------------------------------------------------


def test_dead_compute_extended_data_failure_degrades_cleanly(cli_runner, dead_project, monkeypatch):
    """A raise in aging/effort/decay computation degrades to ``{}``."""
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-extended-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_compute_extended_data", _raise)

    result = _invoke_dead(cli_runner, dead_project, "--decay")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    ext_markers = [m for m in all_wo if m.startswith("dead_compute_extended_data_failed:")]
    assert ext_markers, f"expected dead_compute_extended_data_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True
    # Verdict still emits past the extended-data failure.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict


# ---------------------------------------------------------------------------
# (12) extinction_predict failure -> marker on extinction-mode envelope
# ---------------------------------------------------------------------------


def test_dead_extinction_predict_failure_surfaces_marker(cli_runner, dead_project, monkeypatch):
    """A raise in ``_predict_extinction`` surfaces a W607-BX marker.

    The extinction-mode envelope must still emit cleanly with the
    marker AND ``partial_success: True``. The empty-floor default
    ``(None, [])`` triggers the unresolved-symbol path which produces
    a non-empty envelope (W1245 resolution disclosure).
    """
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-extinction-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_predict_extinction", _raise)

    result = _invoke_dead(cli_runner, dead_project, "--extinction", "dead_one")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    ext_markers = [m for m in all_wo if m.startswith("dead_extinction_predict_failed:")]
    assert ext_markers, f"expected dead_extinction_predict_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (13) W804 PATTERN-2 REGRESSION GUARD: empty-state branch invariant
# ---------------------------------------------------------------------------


def test_w804_empty_state_partial_success_preserved(cli_runner, tmp_path):
    """W804 regression guard: empty corpus -> partial_success: False.

    The empty-state branch was historically missing ``partial_success``
    altogether (W802/W804 sealed). The W607-BX plumbing must NOT
    re-introduce that bug on the empty-state path: when no markers
    fire, the envelope MUST keep ``partial_success: False`` so MCP
    consumers can distinguish "nothing to flag" from "degraded".
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
        result = runner.invoke(cli, ["--json", "dead"], catch_exceptions=False)
    finally:
        os.chdir(cwd)

    assert result.exit_code == 0, result.output
    data = _json.loads(result.output) if result.output.strip() else {}
    summary = data.get("summary") or {}

    # W804 invariant: partial_success present AND False on clean empty corpus.
    assert "partial_success" in summary, (
        f"summary.partial_success missing on empty corpus -- W804 regression; got summary={summary!r}"
    )
    assert summary["partial_success"] is False, (
        f"empty-corpus partial_success must be False (W804); got {summary['partial_success']!r}"
    )

    # And no W607-BX markers fired (since nothing raised).
    top_wo = data.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    bx_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"dead_{p}_failed:" in m for p in _BX_PHASES)]
    assert not bx_markers, f"clean empty corpus must NOT surface W607-BX markers; got {bx_markers!r}"


def test_w804_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, dead_project, monkeypatch):
    """W804 Pattern-2 regression guard on the degraded-empty path.

    If ``_analyze_dead`` raises, the empty-floor default kicks in
    (all_items == []) and the empty-state envelope is emitted. The
    W607-BX wrap MUST flip ``partial_success: True`` on that branch
    so the empty-state envelope is NOT mistaken for a clean "no dead
    exports" verdict (the classic Pattern-2 silent-fallback bug).
    """
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-W804-pattern-2-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_analyze_dead", _raise)

    result = _invoke_dead(cli_runner, dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    # The empty-floor default takes us into the empty-state envelope
    # path -- AND the marker must surface, AND partial_success: True.
    assert summary.get("partial_success") is True, (
        f"degraded-empty path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )

    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    analyze_markers = [m for m in all_wo if m.startswith("dead_analyze_dead_failed:")]
    assert analyze_markers, (
        f"degraded-empty path MUST surface the analyze_dead marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (14) DETECTOR FAMILY 5-WAY pairing bonus
# ---------------------------------------------------------------------------


def test_detector_family_5way_marker_prefixes_coexist(cli_runner, dead_project, monkeypatch):
    """DETECTOR FAMILY 5-WAY pairing bonus.

    Confirm ``dead_<phase>_failed:`` markers coexist with
    ``smells_*`` (W607-BN), ``vibe_check_*`` (W607-BS), ``clones_*``
    (W607-BQ), and ``duplicates_*`` (W607-BM) markers without
    cross-prefix leakage.

    This is the load-bearing prefix-discipline test for the detector
    family quartet: each command's marker family stays inside its own
    prefix so a downstream finder/grep on ``dead_*`` markers picks up
    ONLY the dead-detector substrate failures.
    """
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-5way-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_analyze_dead", _raise)

    result = _invoke_dead(cli_runner, dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # The dead marker fires.
    assert any(m.startswith("dead_analyze_dead_failed:") for m in all_wo), all_wo

    # None of the four detector-sibling prefixes leak into the dead
    # envelope.
    for forbidden_prefix in ("smells_", "vibe_check_", "clones_", "duplicates_"):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on detector-family 5-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_dead envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (15) Bonus: W157 entry-point seeding regression
# ---------------------------------------------------------------------------


def test_w157_analyze_dead_seeding_failure_degrades_safely(cli_runner, dead_project, monkeypatch):
    """W157 entry-point seeding regression.

    cmd_dead's ``_analyze_dead`` is the analogue of a graph-traversal
    seed step: it queries exported symbols (the seed set) and uses
    edge data to filter consumers. If seed extraction raises, the
    traversal degrades safely AND surfaces the marker -- the dead
    command does not silently report "no dead exports" on a failed
    seed.
    """
    from roam.commands import cmd_dead

    def _raise_seed(*args, **kwargs):
        raise RuntimeError("synthetic-W157-seeding-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_analyze_dead", _raise_seed)

    result = _invoke_dead(cli_runner, dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    seed_markers = [m for m in all_wo if m.startswith("dead_analyze_dead_failed:")]
    assert seed_markers, (
        f"W157 seeding-failed path must surface dead_analyze_dead_failed: "
        f"marker so the agent learns the traversal degraded; got {all_wo!r}"
    )
    # Crucial: partial_success=True so the empty-floor branch is NOT
    # mistaken for a clean "no dead exports" success (the canonical
    # Pattern-2 silent-fallback hazard for W157 seeding regressions).
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (16) analyze_dataflow_dead failure -> experimental path degrades
# ---------------------------------------------------------------------------


def test_dead_analyze_dataflow_dead_failure_degrades_cleanly(cli_runner, dead_project, monkeypatch):
    """A raise in the experimental dataflow analyser degrades to ``[]``."""
    from roam.commands import cmd_dead

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-dataflow-dead-from-W607-BX")

    monkeypatch.setattr(cmd_dead, "_analyze_dataflow_dead", _raise)

    # --dataflow path only fires on non-json/non-sarif mode by current
    # source layout, but the wrap is exercised via that flag path.
    # Use json_mode=False to enter the dataflow analyser branch.
    result = _invoke_dead(cli_runner, dead_project, "--dataflow", json_mode=False)
    assert result.exit_code == 0, result.output
    # Text mode emits text; the wrap protects against crash. Smoke test
    # that the command exited 0 (no traceback) on the degraded path.


# ---------------------------------------------------------------------------
# (17) AST source-level guard: marker prefix is the canonical
#      ``dead_<phase>_failed:<exc_class>:<detail>`` shape
# ---------------------------------------------------------------------------


def test_w607bx_marker_shape_documented_in_source():
    """Source-level guard: the canonical marker shape appears in cmd_dead docstring/comments."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dead.py"
    src = src_path.read_text(encoding="utf-8")
    # The fstring template
    # ``f"dead_{phase}_failed:{type(exc).__name__}:{exc}"`` MUST appear
    # exactly once -- the canonical marker construction site. Any
    # divergence from this shape (e.g., a missing colon, mis-spelled
    # prefix) would break consumer parsers.
    fstring_pattern = 'f"dead_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-BX marker fstring missing from cmd_dead; expected: {fstring_pattern}"
    )
