"""W607-DQ -- additive aggregation-phase plumbing for ``cmd_n1``.

cmd_n1 is the implicit N+1 query detector (W110 origin per CLAUDE.md
detector roster). The W607-CB wave installed substrate-CALL plumbing
around the 9 substrate helpers (``analyze_n1`` / ``_find_model_classes``
/ ``symbol_count_query`` / ``_emit_n1_findings`` / SARIF projection /
sort / aggregate / derive / group). This W607-DQ wave layers an
ADDITIVE aggregation-phase plumbing on top of that substrate,
mirroring the canonical 4-phase shape that cmd_dead W607-DL,
cmd_smells W607-DF, cmd_dark_matter W607-CZ, cmd_clones W607-DC, and
cmd_duplicates W607-DD use:

  - substrate-CALL layer: W607-CB (9 boundaries -- see _CB_PHASES below)
  - aggregation-phase layer: W607-DQ (4 boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``n1_*`` marker family and the
``n1_<phase>_failed:<exc_class>:<detail>`` shape contract. The two
bucket sources (``_w607cb_warnings_out`` substrate-CALL +
``_w607dq_warnings_out`` aggregation-phase) are merged at envelope-emit
time into ``warnings_out`` so consumers see the full degradation
lineage. The phase names DO NOT collide -- substrate phases are
``analyze_n1`` / ``serialize_to_sarif`` / etc., aggregation phases are
``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
``serialize_envelope``.

W978 7-discipline first-hypothesis check
----------------------------------------

cmd_sbom W607-CG sealed the kwarg-default eagerness trap (computed
defaults eval BEFORE the try-block).
cmd_taint W607-CJ codified the 5th discipline: move ``len()`` INSIDE
the wrapped closure rather than at the kwarg-bind site.
cmd_audit_trail_export W607-CR codified the 7th discipline: use bare
``dict[key]`` lookup when a floor dict guarantees the key, NOT
``dict.get(key, expensive_default)`` -- ``.get`` evaluates default
eagerly at call site, re-raising on a poisoned upstream input.

Every W607-DQ ``default=`` MUST be a literal constant, AND every
``len()`` / ``sum()`` over the wrapped input MUST live inside the
closure. The AST audit below pins these disciplines at the W607-DQ
layer.

W803 / W805 EMPTY-STATE PRESERVATION
------------------------------------

W803 confirmed the cmd_n1 empty-corpus smoke had no Pattern-2 gap;
W805 sealed the named-empty-state envelopes (``empty_corpus`` /
``no_models``). The regression-guard tests below confirm:

  1. The clean empty corpus path still emits ``partial_success: True``
     with ``state: empty_corpus`` (W805 invariant preserved).
  2. The W607-DQ aggregation boundary on the verdict / envelope
     serializer does NOT re-introduce Pattern-2 silent-fallback -- a
     raise in ``json_envelope`` still emits a non-empty floor stub
     with a marker AND ``partial_success: True``, never a SAFE
     verdict on a degraded state.

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
# Canonical W607-DQ phase enumeration
# ---------------------------------------------------------------------------


_DQ_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)

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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_n1_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_n1.

    Mirrors test_w607_cb._build_n1_project: a single python source file
    + one symbol so the corpus is non-empty and the detector runs
    through all 4 aggregation phases. The detector itself will return
    zero findings (no ORM models present), which exercises the
    ``state: no_models`` empty-state branch. The W607-DQ phases run
    BEFORE the empty-state envelope branches off, so the aggregation
    boundaries get exercised on every populated-corpus run.
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


def _build_empty_n1_project(tmp_path: Path) -> Path:
    """Build an EMPTY-corpus project (no symbols / no files in the index).

    Used to trigger the ``state: empty_corpus`` branch + verify the
    W803/W805 empty-state Pattern-2 invariants are preserved.
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
    (tmp_path / "README.md").write_text("# empty fixture\n")
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
            source_file_id INTEGER
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
    # No files, no symbols -> empty corpus.
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def n1_project(tmp_path):
    return _build_n1_project(tmp_path)


@pytest.fixture
def empty_n1_project(tmp_path):
    return _build_empty_n1_project(tmp_path)


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


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DQ aggregation markers
# ---------------------------------------------------------------------------


def test_n1_happy_path_no_w607dq_markers(cli_runner, n1_project):
    """Clean n1 on a populated corpus -> no W607-DQ aggregation markers."""
    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "n1"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _DQ_PHASES:
        prefix = f"n1_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean n1 must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive _run_check_dq helper + accumulator
# ---------------------------------------------------------------------------


def test_cmd_n1_carries_w607dq_accumulator():
    """AST-level guard: cmd_n1 source carries the W607-DQ anchors AND
    the pre-existing W607-CB layer.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_n1.py"
    assert src_path.exists(), f"cmd_n1.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    assert "_w607dq_warnings_out" in src, (
        "W607-DQ accumulator missing from cmd_n1; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_dq" in src, (
        "W607-DQ helper ``_run_check_dq`` missing from cmd_n1; the additive wrapper has been refactored away."
    )

    tree = ast.parse(src)
    found_run_check_dq = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dq":
            found_run_check_dq = True
            break
    assert found_run_check_dq, (
        "W607-DQ ``_run_check_dq`` helper not found in cmd_n1 AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-CB must still be present (additive layer does NOT replace it)
    assert "_w607cb_warnings_out" in src, (
        "W607-CB accumulator vanished alongside the W607-DQ add; the "
        "additive plumbing must preserve the W607-CB substrate-CALL layer."
    )
    assert "_run_check_cb" in src, "W607-CB helper has been removed."


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_dq():
    """Every aggregation-phase boundary calls ``_run_check_dq(...)`` with
    the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_n1.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _DQ_PHASES:
        same_line = f'_run_check_dq("{phase}"' in src
        multi_line = any(f'_run_check_dq(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"n1_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DQ wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) Per-phase isolation: serialize_envelope raise -> marker + floor stub
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, n1_project, monkeypatch):
    """If ``json_envelope`` raises on the populated path, the wrap floors
    to a parseable envelope stub and surfaces
    ``n1_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_n1 as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DQ")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "n1", f"envelope stub must carry the canonical command name on raise; got {data!r}"
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("n1_serialize_envelope_failed:")]
    assert markers, f"expected ``n1_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) Per-phase isolation: compute_verdict floor is a single line
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_a_single_line(cli_runner, n1_project):
    """Compute-verdict boundary -- the verdict string on the clean path
    MUST be a single line (LAW 6 standalone-parse discipline).
    """
    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"LAW 6: compute_verdict must produce a single line; got {verdict!r}"


# ---------------------------------------------------------------------------
# (6) Per-phase isolation: score_classify surfaces run_state on summary
# ---------------------------------------------------------------------------


def test_score_classify_surfaces_run_state(cli_runner, n1_project):
    """Clean run -> the run_state must be present and in the canonical
    closed enumeration.
    """
    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("run_state") in {
        "NO_N1",
        "N1_LIGHT",
        "N1_MODERATE",
        "N1_HEAVY",
        "DEGRADED",
    }, f"run_state missing/invalid on clean n1 envelope; got {summary.get('run_state')!r}"


# ---------------------------------------------------------------------------
# (7) Per-phase isolation: compute_predicate surfaces rollup fields
# ---------------------------------------------------------------------------


def test_compute_predicate_surfaces_rollup_fields(cli_runner, n1_project):
    """Compute-predicate boundary -- happy path surfaces by_kind /
    files_affected rollup on the summary.
    """
    result = _invoke_n1(cli_runner, n1_project)
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
# (8) W607-CB substrate + W607-DQ aggregation markers BOTH surface
# ---------------------------------------------------------------------------


def test_w607cb_substrate_and_w607dq_aggregation_coexist(cli_runner, n1_project, monkeypatch):
    """When BOTH layers fault, BOTH marker prefixes surface."""
    from roam.commands import cmd_n1 as _mod

    # W607-CB substrate boundary -- analyze_n1 raises
    def _raise_analyze(*a, **kw):
        raise RuntimeError("synthetic-cb-coexist-analyze")

    # W607-DQ aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-dq-coexist-envelope")

    monkeypatch.setattr(_mod, "analyze_n1", _raise_analyze)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL marker from W607-CB (analyze_n1)
    cb_markers = [m for m in top_wo if m.startswith("n1_analyze_n1_failed:")]
    # Aggregation-phase marker from W607-DQ (serialize_envelope)
    dq_markers = [m for m in top_wo if m.startswith("n1_serialize_envelope_failed:")]

    assert cb_markers, f"W607-CB substrate-CALL marker (n1_analyze_n1_failed) missing; got {top_wo!r}"
    assert dq_markers, f"W607-DQ aggregation-phase marker (n1_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``n1_*`` family
    assert all(m.startswith("n1_") for m in (cb_markers + dq_markers)), (
        f"all markers must share the canonical ``n1_*`` family; got cb = {cb_markers!r}, dq = {dq_markers!r}"
    )


# ---------------------------------------------------------------------------
# (9) W803 / W805 preservation -- empty-corpus still flips partial_success
# ---------------------------------------------------------------------------


def test_w805_empty_state_partial_success_preserved_on_clean_empty(cli_runner, empty_n1_project):
    """W803/W805 invariant: empty corpus -> partial_success=True with
    ``state: empty_corpus`` (named-empty-state preservation).
    """
    result = _invoke_n1(cli_runner, empty_n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # W805 contract: empty corpus -> partial_success=True + named state.
    assert summary.get("partial_success") is True, (
        f"W805 invariant violated -- empty corpus must flip partial_success=True; got summary = {summary!r}"
    )
    assert summary.get("state") == "empty_corpus", (
        f"W805 invariant violated -- empty corpus must name state as ``empty_corpus``; got summary = {summary!r}"
    )


def test_w805_empty_state_partial_success_flips_on_dq_raise(cli_runner, empty_n1_project, monkeypatch):
    """W805 extension: empty corpus + a W607-DQ aggregation marker ->
    partial_success=True still (the empty-state envelope MUST surface
    the DQ bucket alongside its named state).
    """
    from roam.commands import cmd_n1 as _mod

    # Force a substrate-CALL marker via find_model_classes; the empty-
    # corpus path STILL runs through the W607-DQ aggregation phases on
    # the way to envelope emission. Verifies the combined-bucket path
    # still flips partial_success on the empty branch.
    def _raise_models(*a, **kw):
        raise RuntimeError("synthetic-empty-state-cb-marker")

    monkeypatch.setattr(_mod, "_find_model_classes", _raise_models)

    result = _invoke_n1(cli_runner, empty_n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # Non-empty marker bucket -> partial_success flip
    assert summary.get("partial_success") is True, (
        f"W805 empty-state must flip partial_success on non-empty warnings bucket; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (10) Cross-prefix isolation -- W607-DQ stays in n1_* family
# ---------------------------------------------------------------------------


def test_w607dq_cross_prefix_isolation(cli_runner, n1_project, monkeypatch):
    """W607-DQ markers must NOT leak into sibling W607-* prefix families."""
    from roam.commands import cmd_n1 as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation-DQ")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for cross-prefix check"
    for marker in failure_markers:
        # Every marker must use the canonical n1_* family.
        assert marker.startswith("n1_"), (
            f"every surfaced W607-DQ marker must use the ``n1_*`` prefix family (cmd_n1 scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("smells_", "cmd_smells W607-BN / DF (detector sibling)"),
            ("vibe_check_", "cmd_vibe_check W607-BS (LLM-rot detector)"),
            ("clones_", "cmd_clones W607-BQ / DC (clone detector)"),
            ("duplicates_", "cmd_duplicates W607-BM / DD"),
            ("dead_", "cmd_dead W607-BX / DL (dead-code detector)"),
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
# (11) Phase-name collision check -- DQ phases distinct from CB phases
# ---------------------------------------------------------------------------


def test_w607dq_phase_names_dont_collide_with_w607cb():
    """W978 4th-discipline guard: the 4 W607-DQ aggregation phase names
    MUST be disjoint from the 9 W607-CB substrate phase names. A
    collision would make the marker prefix ambiguous (an agent reading
    ``n1_serialize_envelope_failed:`` couldn't tell which layer raised).
    """
    dq_set = set(_DQ_PHASES)
    cb_set = set(_CB_PHASES)
    collisions = dq_set & cb_set
    assert not collisions, (
        f"W607-DQ phase names collide with W607-CB: {collisions!r}. "
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

    Canonical floor for cmd_n1 is ``"n1 completed"`` (mirror of
    cmd_dead W607-DL's ``"dead completed"`` and cmd_dark_matter
    W607-CZ's ``"dark-matter completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_n1.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="n1 completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-DQ "
        "discipline; the canonical floor literal 'n1 completed' is "
        "missing from cmd_n1.py"
    )


# ---------------------------------------------------------------------------
# (13) W978 7-discipline AST audit -- default= floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """Every W607-DQ ``default=`` must be a literal constant, NOT
    computed from upstream values.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_n1.py"
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
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dq"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_dq(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_n1.py:\n"
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
    wrapped closure, NOT at the ``_run_check_dq(...)`` call site as a
    positional or keyword argument expression.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_n1.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dq"):
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
                        f"_run_check_dq positional-arg site -- W978 "
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
                        f"_run_check_dq kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_n1.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure. See cmd_taint W607-CJ "
        "for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (15) AST-scan -- BOTH accumulators are pinned in source
# ---------------------------------------------------------------------------


def test_w607dq_coexists_with_w607cb_in_source():
    """W607-DQ is ADDITIVE -- the pre-existing W607-CB substrate-CALL
    family MUST still be present in source alongside W607-DQ.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_n1.py"
    src = src_path.read_text(encoding="utf-8")

    # W607-CB substrate-CALL family
    assert "_w607cb_warnings_out" in src, "W607-CB substrate-CALL accumulator has been removed."
    assert "_run_check_cb" in src, "W607-CB helper has been removed."
    # W607-DQ aggregation-phase family (THIS wave)
    assert "_w607dq_warnings_out" in src, "W607-DQ aggregation-phase accumulator has been removed."
    assert "_run_check_dq" in src, "W607-DQ helper has been removed."


# ---------------------------------------------------------------------------
# (16) ANY W607-DQ marker flips partial_success on the populated path
# ---------------------------------------------------------------------------


def test_any_dq_marker_flips_partial_success(cli_runner, n1_project, monkeypatch):
    """ANY W607-DQ marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    n1" from "n1 ran with aggregation degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_n1 as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-DQ")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-DQ warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (17) warnings_out mirrors -- top-level AND summary BOTH populated
# ---------------------------------------------------------------------------


def test_w607dq_warnings_out_in_both_top_and_summary(cli_runner, n1_project, monkeypatch):
    """Non-empty W607-DQ bucket -> both top-level AND summary.warnings_out
    populated.
    """
    from roam.commands import cmd_n1 as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DQ")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_n1(cli_runner, n1_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DQ raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DQ raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("n1_serialize_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("n1_serialize_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )
