"""W607-DT -- additive aggregation-phase plumbing for ``cmd_over_fetch``.

cmd_over_fetch is the ORM over-fetch detector (W114 origin per CLAUDE.md
detector roster -- part of the original 16 findings-registry substrate
detectors). The W607-CE wave installed substrate-CALL plumbing around
the 10 substrate helpers (``analyze_over_fetch`` /
``analyze_endpoint_states`` / ``_find_model_files`` /
``symbol_count_query`` / ``_emit_over_fetch_findings`` / SARIF
projection / ``aggregate_by_confidence`` / ``aggregate_by_state`` /
``apply_leaks_only_filter`` / ``compute_endpoint_verdict``).

This W607-DT wave layers an ADDITIVE aggregation-phase plumbing on top
of that substrate, mirroring the canonical 4-phase shape that cmd_dead
W607-DL, cmd_smells W607-DF, cmd_dark_matter W607-CZ, cmd_clones
W607-DC, cmd_duplicates W607-DD, and cmd_n1 W607-DQ use:

  - substrate-CALL layer: W607-CE (10 boundaries -- see _CE_PHASES below)
  - aggregation-phase layer: W607-DT (4 boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``over_fetch_*`` marker family and the
``over_fetch_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two bucket sources (``_w607ce_warnings_out`` substrate-CALL +
``_w607dt_warnings_out`` aggregation-phase) are merged at envelope-emit
time into ``warnings_out`` so consumers see the full degradation
lineage. The phase names DO NOT collide -- CE substrate phases are
``analyze_over_fetch`` / ``compute_endpoint_verdict`` / etc.,
aggregation phases are ``score_classify`` / ``compute_predicate`` /
``compute_verdict`` / ``serialize_envelope``. CE's
``compute_endpoint_verdict`` is deliberately distinct from DT's
``compute_verdict`` so an agent can tell which layer raised.

W978 7-discipline first-hypothesis check
----------------------------------------

cmd_sbom W607-CG sealed the kwarg-default eagerness trap (computed
defaults eval BEFORE the try-block).
cmd_taint W607-CJ codified the 5th discipline: move ``len()`` INSIDE
the wrapped closure rather than at the kwarg-bind site.
cmd_audit_trail_export W607-CR codified the 7th discipline: use bare
``dict[key]`` lookup when a floor dict guarantees the key, NOT
``dict.get(key, expensive_default)``.

Every W607-DT ``default=`` MUST be a literal constant, AND every
``len()`` / ``sum()`` over the wrapped input MUST live inside the
closure. The AST audit below pins these disciplines at the W607-DT
layer.

W809 PATTERN-2 PRESERVATION
---------------------------

W809 confirmed the cmd_over_fetch empty-corpus smoke had no Pattern-2
gap. The regression-guard tests below confirm:

  1. The clean empty corpus path still emits ``detector_state:
     empty_corpus`` (W805 invariant preserved -- empty corpus is NOT a
     degradation; partial_success stays False per W809 contract).
  2. The W607-DT aggregation boundary on the verdict / envelope
     serializer does NOT re-introduce Pattern-2 silent-fallback -- a
     raise in ``json_envelope`` still emits a non-empty floor stub with
     a marker AND ``partial_success: True``, never a SAFE verdict on a
     degraded state.

ORM-FAMILY 2-WAY PAIRING
------------------------

cmd_n1 W607-DQ (N+1 detector, sibling ORM-family) and cmd_over_fetch
W607-DT (over-fetch detector) are the 2 ORM-family aggregation-layer
roll-outs as of this wave. The cross-prefix isolation test confirms
each marker family stays inside its own prefix without leaking across
detector boundaries.

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
# Canonical W607-DT phase enumeration
# ---------------------------------------------------------------------------


_DT_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)

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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _populated_schema() -> str:
    """Return the canonical schema script for a roam-indexed project.

    Mirrors test_w607_ce_cmd_over_fetch_warnings_out_envelope shape.
    """
    return """
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


def _build_over_fetch_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_over_fetch.

    Tiny Python fixture (NO Laravel models) so the detector runs cleanly
    with zero model-level findings -- the W607-DT aggregation phases run
    BEFORE the empty-state envelope branches off and get exercised on
    every populated-corpus run.
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
    conn.executescript(_populated_schema())
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/engine.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(1, 1, 'helper', 'src.engine.helper', 'function', 1, 2, 'public', 1)"
    )
    conn.commit()
    conn.close()
    return tmp_path


def _build_empty_over_fetch_project(tmp_path: Path) -> Path:
    """Build an EMPTY-corpus project (no symbols / no files in the index)."""
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
    (tmp_path / "README.md").write_text("# empty fixture\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_populated_schema())
    # No files / no symbols -> empty corpus.
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def over_fetch_project(tmp_path):
    return _build_over_fetch_project(tmp_path)


@pytest.fixture
def empty_over_fetch_project(tmp_path):
    return _build_empty_over_fetch_project(tmp_path)


def _invoke_over_fetch(cli_runner, project_root, *args, json_mode=True, sarif=False):
    """Invoke the over-fetch click command directly (bypassing CLI group)."""
    from roam.commands.cmd_over_fetch import over_fetch_cmd

    obj = {"json": json_mode, "sarif": sarif, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(over_fetch_cmd, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DT aggregation markers
# ---------------------------------------------------------------------------


def test_over_fetch_happy_path_no_w607dt_markers(cli_runner, over_fetch_project):
    """Clean over-fetch on a populated corpus -> no W607-DT markers."""
    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "over-fetch"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _DT_PHASES:
        prefix = f"over_fetch_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean over-fetch must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive _run_check_dt helper + accumulator
# ---------------------------------------------------------------------------


def test_cmd_over_fetch_carries_w607dt_accumulator():
    """AST-level guard: cmd_over_fetch source carries the W607-DT anchors
    AND the pre-existing W607-CE layer.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_over_fetch.py"
    assert src_path.exists(), f"cmd_over_fetch.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    assert "_w607dt_warnings_out" in src, (
        "W607-DT accumulator missing from cmd_over_fetch; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_dt" in src, (
        "W607-DT helper ``_run_check_dt`` missing from cmd_over_fetch; the additive wrapper has been refactored away."
    )

    tree = ast.parse(src)
    found_run_check_dt = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dt":
            found_run_check_dt = True
            break
    assert found_run_check_dt, (
        "W607-DT ``_run_check_dt`` helper not found in cmd_over_fetch AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-CE must still be present (additive layer does NOT replace it)
    assert "_w607ce_warnings_out" in src, (
        "W607-CE accumulator vanished alongside the W607-DT add; the "
        "additive plumbing must preserve the W607-CE substrate-CALL layer."
    )
    assert "_run_check_ce" in src, "W607-CE helper has been removed."


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_dt():
    """Every aggregation-phase boundary calls ``_run_check_dt(...)`` with
    the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_over_fetch.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _DT_PHASES:
        same_line = f'_run_check_dt("{phase}"' in src
        multi_line = any(f'_run_check_dt(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"over_fetch_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DT wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) Per-phase isolation: serialize_envelope raise -> marker + floor stub
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, over_fetch_project, monkeypatch):
    """If ``json_envelope`` raises on the populated path, the wrap floors
    to a parseable envelope stub and surfaces
    ``over_fetch_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_over_fetch as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DT")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "over-fetch", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("over_fetch_serialize_envelope_failed:")]
    assert markers, f"expected ``over_fetch_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) Per-phase isolation: compute_verdict floor is a single line
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_a_single_line(cli_runner, over_fetch_project):
    """Compute-verdict boundary -- the verdict string on the clean path
    MUST be a single line (LAW 6 standalone-parse discipline).
    """
    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"LAW 6: compute_verdict must produce a single line; got {verdict!r}"


# ---------------------------------------------------------------------------
# (6) Per-phase isolation: score_classify surfaces run_state on summary
# ---------------------------------------------------------------------------


def test_score_classify_surfaces_run_state(cli_runner, over_fetch_project):
    """Clean run -> the run_state must be present and in the canonical
    closed enumeration.
    """
    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("run_state") in {
        "NO_OVER_FETCH",
        "OF_LIGHT",
        "OF_MODERATE",
        "OF_HEAVY",
        "DEGRADED",
    }, f"run_state missing/invalid on clean over-fetch envelope; got {summary.get('run_state')!r}"


# ---------------------------------------------------------------------------
# (7) Per-phase isolation: compute_predicate surfaces rollup fields
# ---------------------------------------------------------------------------


def test_compute_predicate_surfaces_rollup_fields(cli_runner, over_fetch_project):
    """Compute-predicate boundary -- happy path surfaces by_kind /
    files_affected rollup on the summary.
    """
    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    assert "by_kind" in summary, (
        f"compute_predicate must surface by_kind rollup; got summary keys = {sorted(summary.keys())!r}"
    )
    assert isinstance(summary["by_kind"], dict), (
        f"by_kind must be a dict (potentially empty); got {type(summary['by_kind']).__name__!r}"
    )
    assert "files_affected" in summary, (
        f"compute_predicate must surface files_affected rollup; got summary keys = {sorted(summary.keys())!r}"
    )
    assert isinstance(summary["files_affected"], int), (
        f"files_affected must be an int; got {type(summary['files_affected']).__name__!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-CE substrate + W607-DT aggregation markers BOTH surface
# ---------------------------------------------------------------------------


def test_w607ce_substrate_and_w607dt_aggregation_coexist(cli_runner, over_fetch_project, monkeypatch):
    """When BOTH layers fault, BOTH marker prefixes surface."""
    from roam.commands import cmd_over_fetch as _mod

    # W607-CE substrate boundary -- analyze_over_fetch raises
    def _raise_analyze(*a, **kw):
        raise RuntimeError("synthetic-ce-coexist-analyze")

    # W607-DT aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-dt-coexist-envelope")

    monkeypatch.setattr(_mod, "analyze_over_fetch", _raise_analyze)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL marker from W607-CE (analyze_over_fetch)
    ce_markers = [m for m in top_wo if m.startswith("over_fetch_analyze_over_fetch_failed:")]
    # Aggregation-phase marker from W607-DT (serialize_envelope)
    dt_markers = [m for m in top_wo if m.startswith("over_fetch_serialize_envelope_failed:")]

    assert ce_markers, f"W607-CE substrate-CALL marker (over_fetch_analyze_over_fetch_failed) missing; got {top_wo!r}"
    assert dt_markers, (
        f"W607-DT aggregation-phase marker (over_fetch_serialize_envelope_failed) missing; got {top_wo!r}"
    )

    # Both share the canonical ``over_fetch_*`` family
    assert all(m.startswith("over_fetch_") for m in (ce_markers + dt_markers)), (
        f"all markers must share the canonical ``over_fetch_*`` family; got ce = {ce_markers!r}, dt = {dt_markers!r}"
    )


# ---------------------------------------------------------------------------
# (9) W809 / W805 preservation -- empty-corpus stays Pattern-2 clean
# ---------------------------------------------------------------------------


def test_w809_empty_state_pattern2_preserved_on_clean_empty(cli_runner, empty_over_fetch_project):
    """W809 invariant: empty corpus -> detector_state=empty_corpus AND
    partial_success=False (empty corpus is NOT a degradation; the
    detector ran but had no input to analyze).
    """
    result = _invoke_over_fetch(cli_runner, empty_over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # W809 contract: empty corpus -> detector_state names the absent state
    assert summary.get("detector_state") == "empty_corpus", (
        f"W809/W805 invariant violated -- empty corpus must name "
        f"detector_state as ``empty_corpus``; got summary = {summary!r}"
    )
    # W809 contract: empty corpus is clean, NOT degraded
    assert summary.get("partial_success") is False, (
        f"W809 invariant violated -- empty corpus must NOT flip "
        f"partial_success (no leaks + no W607-CE/DT markers => clean); "
        f"got summary = {summary!r}"
    )


def test_w805_empty_state_partial_success_flips_on_dt_raise(cli_runner, empty_over_fetch_project, monkeypatch):
    """W805 extension: empty corpus + a W607-DT aggregation marker ->
    partial_success=True. A degraded aggregation surface MUST flip the
    Pattern-2 signal even on a named-empty state.
    """
    from roam.commands import cmd_over_fetch as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-empty-state-dt-marker")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_over_fetch(cli_runner, empty_over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # Non-empty marker bucket -> partial_success flip
    assert summary.get("partial_success") is True, (
        f"empty-state must flip partial_success on non-empty warnings bucket; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (10) Cross-prefix isolation -- W607-DT stays in over_fetch_* family
# ---------------------------------------------------------------------------


def test_w607dt_cross_prefix_isolation(cli_runner, over_fetch_project, monkeypatch):
    """W607-DT markers must NOT leak into sibling W607-* prefix families.

    Especially confirms over_fetch_* does not collide with adjacent
    ORM-family ``n1_*`` or ``missing_index_*`` markers (the 3-detector
    sibling cluster).
    """
    from roam.commands import cmd_over_fetch as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation-DT")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for cross-prefix check"
    for marker in failure_markers:
        # Every marker must use the canonical over_fetch_* family.
        assert marker.startswith("over_fetch_"), (
            f"every surfaced W607-DT marker must use the ``over_fetch_*`` "
            f"prefix family (cmd_over_fetch scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            # ORM-family siblings -- the critical cluster to keep distinct
            ("n1_", "cmd_n1 W607-CB / DQ (sibling ORM detector)"),
            ("missing_index_", "cmd_missing_index W607-CC (sibling ORM detector)"),
            # Detector-family 7-way siblings
            ("smells_", "cmd_smells W607-BN / DF (detector sibling)"),
            ("vibe_check_", "cmd_vibe_check W607-BS (LLM-rot detector)"),
            ("clones_", "cmd_clones W607-BQ / DC (clone detector)"),
            ("duplicates_", "cmd_duplicates W607-BM / DD"),
            ("dead_", "cmd_dead W607-BX / DL (dead-code detector)"),
            # Other adjacent detectors
            ("complexity_", "cmd_complexity W607-BJ"),
            ("dark_matter_", "cmd_dark_matter W607-BK / CZ"),
            ("postmortem_", "cmd_postmortem W607-AN / CV"),
            ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI"),
            ("audit_trail_conformance_", "cmd_audit_trail_conformance W607-CO"),
            ("audit_trail_export_", "cmd_audit_trail_export W607-CR"),
            ("vulns_", "cmd_vulns W607-AQ / CH"),
            ("taint_", "cmd_taint W607-AY / CJ"),
            ("sbom_", "cmd_sbom W607-AM / CG"),
            ("debt_", "cmd_debt W607-BG"),
            ("health_", "cmd_health W607-M / BA"),
            ("supply_chain_", "cmd_supply_chain W607-AK / CD"),
            ("attest_", "cmd_attest W607-AD / BT"),
            ("diff_", "cmd_diff W607-Z / BP"),
            ("critique_", "cmd_critique W607-Y / BL"),
            ("pr_risk_", "cmd_pr_risk W607-Q / BU"),
            ("impact_", "cmd_impact W607-T / BB"),
            ("retrieve_", "cmd_retrieve W607-B / BI"),
            ("findings_", "cmd_findings W607-C"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (11) Phase-name collision check -- DT phases distinct from CE phases
# ---------------------------------------------------------------------------


def test_w607dt_phase_names_dont_collide_with_w607ce():
    """W978 4th-discipline guard: the 4 W607-DT aggregation phase names
    MUST be disjoint from the 10 W607-CE substrate phase names. A
    collision would make the marker prefix ambiguous (an agent reading
    ``over_fetch_compute_verdict_failed:`` couldn't tell which layer
    raised).
    """
    dt_set = set(_DT_PHASES)
    ce_set = set(_CE_PHASES)
    collisions = dt_set & ce_set
    assert not collisions, (
        f"W607-DT phase names collide with W607-CE: {collisions!r}. "
        f"Rename one set so each marker phase belongs to exactly one "
        f"layer."
    )


# ---------------------------------------------------------------------------
# (12) compute_verdict floor is a literal constant -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """W978 first-hypothesis discipline anchor: compute_verdict floor
    must be a literal string, NOT an f-string re-interpolating the
    same values that just raised.

    Canonical floor for cmd_over_fetch is ``"over_fetch completed"``
    (mirror of cmd_dead W607-DL's ``"dead completed"`` /
    cmd_dark_matter W607-CZ's ``"dark-matter completed"`` /
    cmd_n1 W607-DQ's ``"n1 completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_over_fetch.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="over_fetch completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-DT "
        "discipline; the canonical floor literal 'over_fetch completed' "
        "is missing from cmd_over_fetch.py"
    )


# ---------------------------------------------------------------------------
# (13) W978 7-discipline AST audit -- default= floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """Every W607-DT ``default=`` must be a literal constant, NOT
    computed from upstream values.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_over_fetch.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        """True iff ``node`` is a fully-literal AST subtree."""
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
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dt"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_dt(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_over_fetch.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ / cmd_audit_trail_export "
        "W607-CR for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (14) W978 5th-discipline -- closures call len() INSIDE, not at kwarg-bind
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_closures_not_at_kwarg_bind_site():
    """Every ``len()`` call on a wrapped input MUST live INSIDE the
    wrapped closure, NOT at the ``_run_check_dt(...)`` call site as a
    positional or keyword argument expression.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_over_fetch.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dt"):
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
                        f"_run_check_dt positional-arg site -- W978 "
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
                        f"_run_check_dt kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_over_fetch.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure. See cmd_taint W607-CJ "
        "for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (15) AST-scan -- BOTH accumulators are pinned in source
# ---------------------------------------------------------------------------


def test_w607dt_coexists_with_w607ce_in_source():
    """W607-DT is ADDITIVE -- the pre-existing W607-CE substrate-CALL
    family MUST still be present in source alongside W607-DT.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_over_fetch.py"
    src = src_path.read_text(encoding="utf-8")

    # W607-CE substrate-CALL family
    assert "_w607ce_warnings_out" in src, "W607-CE substrate-CALL accumulator has been removed."
    assert "_run_check_ce" in src, "W607-CE helper has been removed."
    # W607-DT aggregation-phase family (THIS wave)
    assert "_w607dt_warnings_out" in src, "W607-DT aggregation-phase accumulator has been removed."
    assert "_run_check_dt" in src, "W607-DT helper has been removed."


# ---------------------------------------------------------------------------
# (16) ANY W607-DT marker flips partial_success on the populated path
# ---------------------------------------------------------------------------


def test_any_dt_marker_flips_partial_success(cli_runner, over_fetch_project, monkeypatch):
    """ANY W607-DT marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    over-fetch" from "over-fetch ran with aggregation degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_over_fetch as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-DT")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-DT warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (17) warnings_out mirrors -- top-level AND summary BOTH populated
# ---------------------------------------------------------------------------


def test_w607dt_warnings_out_in_both_top_and_summary(cli_runner, over_fetch_project, monkeypatch):
    """Non-empty W607-DT bucket -> both top-level AND summary.warnings_out
    populated.
    """
    from roam.commands import cmd_over_fetch as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DT")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_over_fetch(cli_runner, over_fetch_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DT raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DT raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("over_fetch_serialize_envelope_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("over_fetch_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (18) W21.6 --leaks-only flag preserved through the W607-DT plumbing
# ---------------------------------------------------------------------------


def test_w21_6_leaks_only_flag_preserved(cli_runner, over_fetch_project):
    """The W21.6 --leaks-only flag must survive the W607-DT additive
    layer (presentation filter scopes endpoint findings only; the
    summary tells the truth regardless).
    """
    # --leaks-only is a tri-state Click option; explicit value wins
    # over the --ci inference.
    result = _invoke_over_fetch(cli_runner, over_fetch_project, "--leaks-only")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("leaks_only") is True, (
        f"--leaks-only flag must be reflected on the envelope; got summary = {summary!r}"
    )
