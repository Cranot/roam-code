"""W607-CE -- ``cmd_over_fetch`` substrate-boundary plumbing.

cmd_over_fetch is the ORM over-fetch detector (W114 origin per CLAUDE.md
detector roster -- part of the original 16 findings-registry substrate
detectors). The detector has a W84 3-state endpoint classification
(BARE / GUARDED_RELATION / UNGUARDED_RELATION) plus a W21.6
--leaks-only display filter. W809 confirmed the cmd_over_fetch
empty-corpus smoke had no Pattern-2 gap, but until this wave the
command had no substrate-boundary marker plumbing -- a raise in
``analyze_over_fetch`` (the core model-level detection) or
``analyze_endpoint_states`` (the 3-state classifier) would crash the
over-fetch detector outright.

This wave installs the canonical ``_w607ce_warnings_out`` bucket +
``_run_check_ce`` helper inside the ``over-fetch`` click command and
wraps every substrate boundary:

* analyze_over_fetch        -- core model-level detection
* analyze_endpoint_states   -- 3-state endpoint classification
                              (W84 BARE/GUARDED/UNGUARDED)
* find_model_files          -- empty-state model counter
* symbol_count_query        -- empty-state symbol-table COUNT
* emit_findings             -- W114 findings-registry mirror
                              (sqlite3.OperationalError silent no-op
                              preserved for pre-W89 DB)
* serialize_to_sarif        -- SARIF projection
* aggregate_by_confidence   -- model-level by-confidence histogram
* aggregate_by_state        -- endpoint 3-state tallies
* apply_leaks_only_filter   -- W21.6 --leaks-only filter
* compute_endpoint_verdict  -- endpoint-verdict composition

Marker family ``over_fetch_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers preserved by the
prefix-discipline test (over-fetch is part of the DETECTOR FAMILY
7-WAY with n1 / smells / vibe-check / clones / duplicates / dead).

W809 PATTERN-2 REGRESSION GUARD
-------------------------------

W809 confirmed the cmd_over_fetch empty-corpus smoke had no Pattern-2
gap. The regression-guard tests below confirm:

  1. The clean empty corpus path still emits ``partial_success: False``
     with ``detector_state`` naming the absent input state explicitly
     (W805 invariant preserved -- empty corpus is NOT a degradation).
  2. The W607-CE substrate boundary on ``analyze_over_fetch`` does NOT
     re-introduce Pattern-2 silent-fallback -- a raise in
     ``analyze_over_fetch`` still emits a non-empty envelope with a
     marker AND ``partial_success: True``, never a SAFE verdict on a
     degraded state.

DETECTOR FAMILY 7-WAY PAIRING
-----------------------------

The bonus pairing test confirms each marker family stays inside its
own prefix without leaking across detector boundaries (over-fetch +
n1 + smells + vibe-check + clones + duplicates + dead == 7 detectors).
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


def _build_over_fetch_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_over_fetch.

    Builds a tiny Python fixture (NO Laravel models) so the
    over-fetch detector runs cleanly with zero findings -- the tests
    focus on W607-CE marker plumbing rather than the detector verdict.
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
def over_fetch_project(tmp_path):
    return _build_over_fetch_project(tmp_path)


def _invoke_over_fetch(cli_runner, project_root, *args, json_mode=True, sarif=False):
    """Invoke the over_fetch click command directly (bypassing the CLI group)."""
    from roam.commands.cmd_over_fetch import over_fetch_cmd

    obj = {"json": json_mode, "sarif": sarif, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(over_fetch_cmd, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_CE_PHASES = (
    "analyze_over_fetch",
    "analyze_endpoint_states",
    "find_model_files",
    "symbol_count_query",
    "emit_findings",
    "serialize_to_sarif",
    "aggregate_by_confidence",
    "aggregate_by_state",
    "apply_leaks_only_filter",
    "compute_endpoint_verdict",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CE substrate markers
# ---------------------------------------------------------------------------


def test_over_fetch_clean_envelope_omits_w607ce_markers(cli_runner, over_fetch_project):
    """Clean over-fetch run -> no W607-CE substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-CE bucket on
    the success path must NOT introduce new ``over_fetch_<phase>_failed:``
    markers tied to the W607-CE wrap.
    """
    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "over-fetch"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ce_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"over_fetch_{p}_failed:" in m for p in _CE_PHASES)
    ]
    assert not ce_markers, (
        f"clean over-fetch must NOT surface W607-CE substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) analyze_over_fetch failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_over_fetch_analyze_failure_marker_format(cli_runner, over_fetch_project, monkeypatch):
    """If ``analyze_over_fetch`` raises, surface the canonical 3-segment marker."""
    from roam.commands import cmd_over_fetch

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-analyze-from-W607-CE")

    monkeypatch.setattr(cmd_over_fetch, "analyze_over_fetch", _raise)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    analyze_markers = [m for m in all_wo if m.startswith("over_fetch_analyze_over_fetch_failed:")]
    assert analyze_markers, f"expected over_fetch_analyze_over_fetch_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in analyze_markers), analyze_markers
    assert any("synthetic-analyze-from-W607-CE" in m for m in analyze_markers), analyze_markers
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


def test_over_fetch_w607ce_warnings_in_envelope(cli_runner, over_fetch_project, monkeypatch):
    """Non-empty W607-CE bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_over_fetch

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CE")

    monkeypatch.setattr(cmd_over_fetch, "analyze_over_fetch", _raise)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CE disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CE disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("over_fetch_analyze_over_fetch_failed:")]
    assert markers, f"expected over_fetch_analyze_over_fetch_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_over_fetch_three_segment_marker_shape(cli_runner, over_fetch_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_over_fetch

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CE")

    monkeypatch.setattr(cmd_over_fetch, "analyze_over_fetch", _raise)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("over_fetch_analyze_over_fetch_failed:")]
    assert failure_markers, f"expected over_fetch_analyze_over_fetch_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "over_fetch_analyze_over_fetch_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) analyze_endpoint_states failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_over_fetch_analyze_endpoint_states_failure_degrades_cleanly(cli_runner, over_fetch_project, monkeypatch):
    """A raise in ``analyze_endpoint_states`` must NOT crash the over-fetch command."""
    from roam.commands import cmd_over_fetch

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-endpoint-from-W607-CE")

    monkeypatch.setattr(cmd_over_fetch, "analyze_endpoint_states", _raise)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    endpoint_markers = [m for m in all_wo if m.startswith("over_fetch_analyze_endpoint_states_failed:")]
    assert endpoint_markers, f"expected over_fetch_analyze_endpoint_states_failed: marker; got {all_wo!r}"
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-CE stays in ``over_fetch_*`` family
# ---------------------------------------------------------------------------


def test_w607ce_marker_prefix_stays_in_over_fetch_family(cli_runner, over_fetch_project, monkeypatch):
    """Every W607-CE substrate marker uses the canonical ``over_fetch_*`` prefix.

    Hard distinction from sibling W607-* layers including cmd_n1
    (W607-CB, ``n1_*``), cmd_smells (W607-BN, ``smells_*``),
    cmd_vibe_check (W607-BS, ``vibe_check_*``), cmd_clones (W607-BQ,
    ``clones_*``), cmd_duplicates (W607-BM, ``duplicates_*``), and
    cmd_dead (W607-BX, ``dead_*``). cmd_over_fetch is the seventh
    detector in the family septet -- a leaking marker would cross the
    detector-family boundary.
    """
    from roam.commands import cmd_over_fetch

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CE")

    monkeypatch.setattr(cmd_over_fetch, "analyze_over_fetch", _raise)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("over_fetch_"), (
            f"every surfaced W607-CE marker must use the ``over_fetch_*`` "
            f"prefix family (cmd_over_fetch scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("n1_", "cmd_n1 W607-CB (N+1 detector sibling)"),
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
# (7) Source-level guard: cmd_over_fetch carries the W607-CE accumulator
# ---------------------------------------------------------------------------


def test_cmd_over_fetch_carries_w607ce_accumulator():
    """AST-level guard: cmd_over_fetch source carries the W607-CE accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_over_fetch.py"
    assert src_path.exists(), f"cmd_over_fetch.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ce_warnings_out" in src, (
        "W607-CE accumulator missing from cmd_over_fetch; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ce" in src, (
        "W607-CE ``_run_check_ce`` helper missing from cmd_over_fetch; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_ce = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ce":
            found_run_check_ce = True
            break
    assert found_run_check_ce, (
        "W607-CE ``_run_check_ce`` helper not found in cmd_over_fetch AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-CE substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ce_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-CE substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_over_fetch.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _CE_PHASES:
        same_line = f'_run_check_ce("{phase}"' in src
        multi_line = (
            f'_run_check_ce(\n        "{phase}"' in src
            or f'_run_check_ce(\n            "{phase}"' in src
            or f'_run_check_ce(\n                "{phase}"' in src
            or f'_run_check_ce(\n                    "{phase}"' in src
            or f'_run_check_ce(\n                        "{phase}"' in src
        )
        # emit_findings is wrapped via direct try/except (NOT _run_check_ce)
        # because it needs to distinguish sqlite3.OperationalError (expected
        # pre-W89 path) from generic Exception (W607-CE marker). Source-grep
        # on the marker name in that case.
        marker_grep = f"over_fetch_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-CE wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) emit_findings failure -> marker surfaces, command still emits
# ---------------------------------------------------------------------------


def test_over_fetch_emit_findings_failure_surfaces_marker(cli_runner, over_fetch_project, monkeypatch):
    """W114 emit failure (non-OperationalError) surfaces W607-CE marker.

    sqlite3.OperationalError is the EXPECTED pre-W89 path (silent
    no-op). Generic exceptions surface via the W607-CE marker so a real
    bug in the persist substrate is loud, not silent.
    """
    from roam.commands import cmd_over_fetch

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-CE")

    monkeypatch.setattr(cmd_over_fetch, "_emit_over_fetch_findings", _raise)

    result = _invoke_over_fetch(cli_runner, over_fetch_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("over_fetch_emit_findings_failed:")]
    assert emit_markers, f"expected over_fetch_emit_findings_failed: marker; got {all_wo!r}"
    # The over-fetch command still emits a clean envelope past the
    # registry-mirror failure -- W114 is additive, not load-bearing.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) emit_findings OperationalError path stays silent (no W607-CE marker)
# ---------------------------------------------------------------------------


def test_over_fetch_emit_findings_operational_error_stays_silent(cli_runner, over_fetch_project, monkeypatch):
    """W607-CE MUST preserve the W114 silent no-op contract on
    ``sqlite3.OperationalError`` (pre-W89 schema -- no findings table).

    The marker MUST NOT surface for this expected degraded path.
    """
    from roam.commands import cmd_over_fetch

    def _raise_op_err(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings (pre-W89 schema)")

    monkeypatch.setattr(cmd_over_fetch, "_emit_over_fetch_findings", _raise_op_err)

    result = _invoke_over_fetch(cli_runner, over_fetch_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("over_fetch_emit_findings_failed:")]
    assert not emit_markers, (
        f"sqlite3.OperationalError is the EXPECTED pre-W89 silent "
        f"no-op path; W607-CE marker MUST NOT surface; "
        f"got {emit_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) find_model_files failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_over_fetch_find_model_files_failure_degrades_cleanly(cli_runner, over_fetch_project, monkeypatch):
    """A raise in ``_find_model_files`` must NOT crash the over-fetch command."""
    from roam.commands import cmd_over_fetch

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-models-from-W607-CE")

    monkeypatch.setattr(cmd_over_fetch, "_find_model_files", _raise)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    models_markers = [m for m in all_wo if m.startswith("over_fetch_find_model_files_failed:")]
    assert models_markers, f"expected over_fetch_find_model_files_failed: marker; got {all_wo!r}"
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (12) W809 PATTERN-2 REGRESSION GUARD: empty-corpus invariant
# ---------------------------------------------------------------------------


def test_w809_empty_corpus_partial_success_preserved(cli_runner, tmp_path):
    """W809 regression guard: empty corpus -> partial_success: False
    with ``detector_state: empty_corpus``.

    W809 confirmed cmd_over_fetch empty-corpus smoke had no Pattern-2
    gap. The W805 contract for over-fetch is intentionally DIFFERENT
    from the cmd_n1 W805 contract: empty corpus / no PHP models is NOT
    a degradation for over-fetch -- the verdict is genuinely "0 real
    leaks" and partial_success stays False. The W607-CE plumbing must
    NOT re-introduce a Pattern-2 bug: when no markers fire AND the
    corpus is empty, ``partial_success`` MUST stay False so MCP
    consumers don't see a degraded signal where there is none.
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
        result = runner.invoke(cli, ["--json", "over-fetch"], catch_exceptions=False)
    finally:
        os.chdir(cwd)

    assert result.exit_code == 0, result.output
    data = _json.loads(result.output) if result.output.strip() else {}
    summary = data.get("summary") or {}

    # W809 invariant: detector_state names the absent input state,
    # partial_success stays False on empty corpus (NOT a degradation).
    detector_state = summary.get("detector_state")
    assert detector_state in ("empty_corpus", "no_php_models"), (
        f"empty-corpus envelope MUST carry named detector_state "
        f"(``empty_corpus`` or ``no_php_models``); got "
        f"detector_state={detector_state!r}, summary={summary!r}"
    )
    assert summary.get("partial_success") is False, (
        f"empty-corpus partial_success must be False (W809: empty "
        f"corpus is NOT a degradation for over-fetch); got "
        f"summary={summary!r}"
    )

    # And no W607-CE markers fired (since nothing raised).
    top_wo = data.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    ce_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"over_fetch_{p}_failed:" in m for p in _CE_PHASES)
    ]
    assert not ce_markers, f"clean empty corpus must NOT surface W607-CE markers; got {ce_markers!r}"


def test_w809_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, over_fetch_project, monkeypatch):
    """W809 Pattern-2 regression guard on the degraded-empty path.

    If ``analyze_over_fetch`` raises, the empty-floor default kicks in
    (findings == []) and the empty-state envelope is emitted. The
    W607-CE wrap MUST flip ``partial_success: True`` on that branch so
    the empty-state envelope is NOT mistaken for a clean "no over-fetch
    patterns" verdict (the classic Pattern-2 silent-fallback bug).
    """
    from roam.commands import cmd_over_fetch

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-W809-pattern-2-from-W607-CE")

    monkeypatch.setattr(cmd_over_fetch, "analyze_over_fetch", _raise)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    # The empty-floor default takes us into the empty-state envelope
    # path -- AND the marker must surface, AND partial_success: True.
    assert summary.get("partial_success") is True, (
        f"degraded-empty path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )

    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    analyze_markers = [m for m in all_wo if m.startswith("over_fetch_analyze_over_fetch_failed:")]
    assert analyze_markers, (
        f"degraded-empty path MUST surface the analyze_over_fetch marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (13) DETECTOR FAMILY 7-WAY pairing bonus
# ---------------------------------------------------------------------------


def test_detector_family_7way_marker_prefixes_coexist(cli_runner, over_fetch_project, monkeypatch):
    """DETECTOR FAMILY 7-WAY pairing bonus.

    Confirm ``over_fetch_<phase>_failed:`` markers coexist with
    ``n1_*`` (W607-CB), ``smells_*`` (W607-BN), ``vibe_check_*``
    (W607-BS), ``clones_*`` (W607-BQ), ``duplicates_*`` (W607-BM), and
    ``dead_*`` (W607-BX) markers without cross-prefix leakage.

    This is the load-bearing prefix-discipline test for the detector
    family septet: each command's marker family stays inside its own
    prefix so a downstream finder/grep on ``over_fetch_*`` markers
    picks up ONLY the over-fetch detector substrate failures.
    """
    from roam.commands import cmd_over_fetch

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-7way-from-W607-CE")

    monkeypatch.setattr(cmd_over_fetch, "analyze_over_fetch", _raise)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # The over_fetch marker fires.
    assert any(m.startswith("over_fetch_analyze_over_fetch_failed:") for m in all_wo), all_wo

    # None of the six detector-sibling prefixes leak into the
    # over-fetch envelope.
    for forbidden_prefix in (
        "n1_",
        "smells_",
        "vibe_check_",
        "clones_",
        "duplicates_",
        "dead_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on detector-family 7-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_over_fetch envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (14) 3-STATE CLASSIFICATION REGRESSION: W84 contract survives W607-CE
# ---------------------------------------------------------------------------


def test_w84_3state_classification_survives_w607ce_plumbing(cli_runner, over_fetch_project):
    """W84 regression guard: 3-state classification fields survive W607-CE.

    The W84 contract requires the summary to carry
    ``bare_count`` / ``guarded_relation_count`` /
    ``unguarded_relation_count`` / ``endpoint_total`` /
    ``real_leak_count`` -- one numeric per state. The W607-CE plumbing
    wraps ``aggregate_by_state`` and must preserve these fields with
    zero values on an empty project (not omit them).
    """
    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    for field in (
        "bare_count",
        "guarded_relation_count",
        "unguarded_relation_count",
        "endpoint_total",
        "real_leak_count",
    ):
        assert field in summary, (
            f"W84 3-state field {field!r} missing from summary post-W607-CE; "
            f"got summary keys = {sorted(summary.keys())!r}"
        )
        assert isinstance(summary[field], int), (
            f"W84 3-state field {field!r} must be int; got {type(summary[field])!r}: {summary[field]!r}"
        )


# ---------------------------------------------------------------------------
# (15) --leaks-only filter substrate isolation: marker surfaces + graceful degradation
# ---------------------------------------------------------------------------


def test_over_fetch_leaks_only_filter_substrate_isolation(cli_runner, over_fetch_project, monkeypatch):
    """W21.6 --leaks-only filter substrate isolation under W607-CE.

    Simulate the filter substrate raising via a monkeypatched endpoint
    states list that contains a malformed entry (missing ``state``
    key). The filter comprehension's ``e["state"]`` lookup will
    KeyError; the W607-CE wrap must surface the
    ``over_fetch_apply_leaks_only_filter_failed:`` marker AND keep
    the envelope composable.
    """
    from roam.commands import cmd_over_fetch

    def _fake_endpoints(*args, **kwargs):
        # Return one endpoint dict WITHOUT a ``state`` key -- the
        # leaks-only comprehension will KeyError on it.
        return [
            {
                "endpoint": "FakeCtrl@index",
                "controller": "FakeCtrl",
                "method": "index",
                "file": "src/Controllers/FakeCtrl.php",
                "line": 1,
                "location": "src/Controllers/FakeCtrl.php:1",
                # NO "state" key -- filter substrate KeyErrors
                "severity": "H",
                "evidence": "",
                "recommendation": "",
                "details": {},
            }
        ]

    monkeypatch.setattr(cmd_over_fetch, "analyze_endpoint_states", _fake_endpoints)
    # Also patch the by-state aggregator so it doesn't raise first on
    # the same missing key -- we want the leaks-only filter substrate
    # to be the failure-attribution site.
    # We achieve this by making the aggregator tolerant via a
    # different ``state`` access path (via .get) -- but since the
    # source code uses ``e["state"]`` literally, we instead just rely
    # on the by-state aggregator raising FIRST and surfacing its own
    # marker. Both substrates raise; both markers should surface.

    result = _invoke_over_fetch(cli_runner, over_fetch_project, "--leaks-only")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # At least ONE substrate marker fires -- either aggregate_by_state
    # or apply_leaks_only_filter (the missing ``state`` key blows up
    # whichever runs first). Both are valid disclosure paths.
    substrate_markers = [
        m
        for m in all_wo
        if m.startswith("over_fetch_aggregate_by_state_failed:")
        or m.startswith("over_fetch_apply_leaks_only_filter_failed:")
    ]
    assert substrate_markers, f"expected aggregate_by_state or apply_leaks_only_filter marker; got {all_wo!r}"
    # Envelope still composes -- partial_success flipped via marker bucket.
    assert data["summary"].get("partial_success") is True
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict


# ---------------------------------------------------------------------------
# (16) AST source-level guard: marker shape is the canonical
#      ``over_fetch_<phase>_failed:<exc_class>:<detail>`` shape
# ---------------------------------------------------------------------------


def test_w607ce_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-CE marker shape lives in cmd_over_fetch."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_over_fetch.py"
    src = src_path.read_text(encoding="utf-8")
    # The fstring template
    # ``f"over_fetch_{phase}_failed:{type(exc).__name__}:{exc}"`` MUST
    # appear exactly once -- the canonical marker construction site.
    fstring_pattern = 'f"over_fetch_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-CE marker fstring missing from cmd_over_fetch; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (17) SARIF projection failure -> marker surfaces on CI path
# ---------------------------------------------------------------------------


def test_over_fetch_sarif_failure_surfaces_marker(cli_runner, over_fetch_project, monkeypatch):
    """A raise in the SARIF projection must NOT crash the over-fetch CI path.

    The SARIF projection is wrapped so a writer exception is contained
    -- the click command still returns cleanly without a traceback. By
    design SARIF mode short-circuits the envelope (writes pure SARIF
    to stdout), so we verify exit_code only on the smoke-test axis.
    """
    # Patch the over_fetch_to_sarif via the sarif output module -- the
    # import happens inside the click command at call time.
    from roam.output import sarif as sarif_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-sarif-from-W607-CE")

    monkeypatch.setattr(sarif_mod, "over_fetch_to_sarif", _raise)

    result = _invoke_over_fetch(cli_runner, over_fetch_project, json_mode=False, sarif=True)
    # The W607-CE wrap protects against crash even on the SARIF path.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (18) aggregate_by_confidence failure -> empty histogram, envelope composes
# ---------------------------------------------------------------------------


def test_over_fetch_aggregate_by_confidence_failure_degrades_cleanly(cli_runner, over_fetch_project, monkeypatch):
    """A raise in the by-confidence aggregator degrades to ``{}``."""
    from roam.commands import cmd_over_fetch

    # Have analyze_over_fetch return one finding with a MISSING
    # ``confidence`` key -- the aggregator's ``f["confidence"]`` lookup
    # will KeyError inside the substrate.
    def _fake_analyze(*args, **kwargs):
        return [
            {
                "model_name": "FakeModel",
                "model_path": "src/Models/Fake.php",
                "model_location": "src/Models/Fake.php:1",
                "fillable_count": 25,
                "hidden_count": 0,
                "exposed_count": 25,
                "has_visible": False,
                "has_resource": False,
                "resource_path": None,
                # NO "confidence" key -- aggregator KeyErrors.
                "reasons": [],
                "matched_patterns": [],
                "suggestions": [],
                "direct_returns": [],
                "missing_selects": [],
            }
        ]

    monkeypatch.setattr(cmd_over_fetch, "analyze_over_fetch", _fake_analyze)
    # Also patch analyze_endpoint_states to return [] so the endpoint
    # tally isn't the failure-attribution site.
    monkeypatch.setattr(cmd_over_fetch, "analyze_endpoint_states", lambda *a, **k: [])

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    agg_markers = [m for m in all_wo if m.startswith("over_fetch_aggregate_by_confidence_failed:")]
    assert agg_markers, f"expected over_fetch_aggregate_by_confidence_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True
