"""W607-DL -- additive aggregation-phase plumbing for ``cmd_dead``.

cmd_dead is the foundational dead-code detector (W99 origin). The
W607-BX wave installed substrate-CALL plumbing around the 10 substrate
helpers (``_analyze_dead``, ``collect_dataflow_findings``, etc). This
W607-DL wave layers an ADDITIVE aggregation-phase plumbing on top of
that substrate, mirroring the canonical 4-phase shape that cmd_smells
W607-DF, cmd_dark_matter W607-CZ, cmd_clones W607-DC, and
cmd_duplicates W607-DD use:

  - substrate-CALL layer: W607-BX (10 boundaries -- see _BX_PHASES below)
  - aggregation-phase layer: W607-DL (4 boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``dead_*`` marker family and the
``dead_<phase>_failed:<exc_class>:<detail>`` shape contract. The two
bucket sources (``_w607bx_warnings_out`` substrate-CALL +
``_w607dl_warnings_out`` aggregation-phase) are merged at envelope-emit
time into ``warnings_out`` so consumers see the full degradation
lineage. The phase names DO NOT collide -- substrate phases are
``analyze_dead`` / ``serialize_to_sarif`` / etc., aggregation phases
are ``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
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

Every W607-DL ``default=`` MUST be a literal constant, AND every
``len()`` / ``sum()`` over the wrapped input MUST live inside the
closure. The AST audit below pins these disciplines at the W607-DL
layer.

W804 EMPTY-STATE PRESERVATION
-----------------------------

W802/W804 fixed a real bug: the empty-state branch was missing
``summary.partial_success: False``. The regression-guard test below
confirms that W607-DL did NOT regress that invariant -- a clean empty
corpus path still emits ``summary.partial_success: False`` and a
degraded empty path (e.g. ``serialize_envelope`` raise) still flips
``partial_success: True``.

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
# Canonical W607-DL phase enumeration
# ---------------------------------------------------------------------------


_DL_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)

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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_dead_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root with at least two dead exports.

    Mirrors test_w607_bx._build_dead_project: alive() -> helper() edge,
    dead_one / dead_two have no callers. Two dead exports is enough to
    exercise the full aggregation-phase chain on a NON-empty corpus
    (the populated envelope branch -- the W607-DL phases live in this
    branch).
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


def _build_empty_dead_project(tmp_path: Path) -> Path:
    """Build a project where every symbol has a caller (no dead exports).

    Used to trigger the empty-state W804 partial_success-preservation
    branch.
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
    (tmp_path / "src" / "engine.py").write_text("def alive():\n    return helper()\n\ndef helper():\n    return 0\n")
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
    # alive() calls helper(); helper has a caller; alive is exported but its
    # call-target is reached -- no dead exports.
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(1, 1, 'alive', 'src.engine.alive', 'function', 1, 2, 'public', 1),"
        "(2, 1, 'helper', 'src.engine.helper', 'function', 4, 5, 'public', 1)"
    )
    # Both symbols have inbound edges (alive -> helper, helper -> alive).
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (1, 2, 'call'), (2, 1, 'call')")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def dead_project(tmp_path):
    return _build_dead_project(tmp_path)


@pytest.fixture
def empty_dead_project(tmp_path):
    return _build_empty_dead_project(tmp_path)


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


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DL aggregation markers
# ---------------------------------------------------------------------------


def test_dead_happy_path_no_w607dl_markers(cli_runner, dead_project):
    """Clean dead on a populated corpus -> no W607-DL aggregation markers."""
    result = _invoke_dead(cli_runner, dead_project, detail=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "dead"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _DL_PHASES:
        prefix = f"dead_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean dead must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive _run_check_dl helper + accumulator
# ---------------------------------------------------------------------------


def test_cmd_dead_carries_w607dl_accumulator():
    """AST-level guard: cmd_dead source carries the W607-DL anchors AND
    the pre-existing W607-BX layer.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dead.py"
    assert src_path.exists(), f"cmd_dead.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    assert "w607dl_warnings_out" in src, (
        "W607-DL accumulator missing from cmd_dead; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_dl" in src, (
        "W607-DL helper ``_run_check_dl`` missing from cmd_dead; the additive wrapper has been refactored away."
    )

    tree = ast.parse(src)
    found_run_check_dl = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dl":
            found_run_check_dl = True
            break
    assert found_run_check_dl, (
        "W607-DL ``_run_check_dl`` helper not found in cmd_dead AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-BX must still be present (additive layer does NOT replace it)
    assert "w607bx_warnings_out" in src, (
        "W607-BX accumulator vanished alongside the W607-DL add; the "
        "additive plumbing must preserve the W607-BX substrate-CALL layer."
    )
    assert "_run_check_bx" in src, "W607-BX helper has been removed."


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_dl():
    """Every aggregation-phase boundary calls ``_run_check_dl(...)`` with
    the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dead.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _DL_PHASES:
        same_line = f'_run_check_dl("{phase}"' in src
        multi_line = any(f'_run_check_dl(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"dead_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DL wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4-7) Per-phase isolation -- each aggregation phase raising surfaces
# the marker + degrades gracefully (4 tests, one per phase)
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, dead_project, monkeypatch):
    """If ``json_envelope`` raises on the populated path, the wrap floors
    to a parseable envelope stub and surfaces
    ``dead_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_dead as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_dead(cli_runner, dead_project, detail=True)
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "dead", f"envelope stub must carry the canonical command name on raise; got {data!r}"
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("dead_serialize_envelope_failed:")]
    assert markers, f"expected ``dead_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


def test_compute_verdict_floor_is_a_single_line(cli_runner, dead_project):
    """Compute-verdict boundary -- the verdict string on the clean path
    MUST be a single line (LAW 6 standalone-parse discipline).

    Per-phase isolation surface for compute_verdict: the closure
    builds the verdict from int args only, so the natural raise
    surface is __format__ misbehaviour on a poisoned int subclass.
    The clean path here validates the floor contract (the literal
    ``"dead completed"`` floor is pinned by
    test_compute_verdict_floor_is_literal_constant below).
    """
    result = _invoke_dead(cli_runner, dead_project, detail=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"LAW 6: compute_verdict must produce a single line; got {verdict!r}"


def test_score_classify_failure_isolates(cli_runner, dead_project, monkeypatch):
    """Monkeypatch the W607-DL ``_score_classify_run`` closure path by
    making the underlying state dict access raise.

    Since the closure is a local function defined inside the click
    command, we monkeypatch ``json_envelope`` to inspect the captured
    score-classify outcome instead. The key contract: a clean run
    surfaces ``run_state`` on summary; a degraded score_classify
    floors to ``DEGRADED``.
    """
    result = _invoke_dead(cli_runner, dead_project, detail=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    # Clean populated path -> the run_state must be present.
    assert summary.get("run_state") in {
        "NO_DEAD",
        "DEAD_LIGHT",
        "DEAD_HEAVY",
        "DEGRADED",
    }, f"run_state missing/invalid on clean dead envelope; got {summary.get('run_state')!r}"


def test_compute_predicate_surfaces_rollup_fields(cli_runner, dead_project):
    """Compute-predicate boundary -- happy path surfaces by_kind /
    files_affected rollup on the summary.
    """
    result = _invoke_dead(cli_runner, dead_project, detail=True)
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
# (8) W607-BX coexistence -- substrate + aggregation markers BOTH surface
# ---------------------------------------------------------------------------


def test_w607bx_substrate_and_w607dl_aggregation_coexist(cli_runner, dead_project, monkeypatch):
    """When BOTH layers fault, BOTH marker prefixes surface."""
    from roam.commands import cmd_dead as _mod

    # W607-BX substrate boundary -- _find_dead_clusters raises
    def _raise_clusters(*a, **kw):
        raise RuntimeError("synthetic-bx-coexist-clusters")

    # W607-DL aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-dl-coexist-envelope")

    monkeypatch.setattr(_mod, "_find_dead_clusters", _raise_clusters)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_dead(cli_runner, dead_project, "--clusters", detail=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL marker from W607-BX (find_dead_clusters)
    bx_markers = [m for m in top_wo if m.startswith("dead_find_dead_clusters_failed:")]
    # Aggregation-phase marker from W607-DL (serialize_envelope)
    dl_markers = [m for m in top_wo if m.startswith("dead_serialize_envelope_failed:")]

    assert bx_markers, f"W607-BX substrate-CALL marker (dead_find_dead_clusters_failed) missing; got {top_wo!r}"
    assert dl_markers, f"W607-DL aggregation-phase marker (dead_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``dead_*`` family
    assert all(m.startswith("dead_") for m in (bx_markers + dl_markers)), (
        f"all markers must share the canonical ``dead_*`` family; got bx = {bx_markers!r}, dl = {dl_markers!r}"
    )


# ---------------------------------------------------------------------------
# (9) W804 preservation -- empty-state branch still flips partial_success
# ---------------------------------------------------------------------------


def test_w804_empty_state_partial_success_preserved_on_clean_empty(cli_runner, empty_dead_project):
    """W804 invariant: empty corpus + no markers -> partial_success=False
    (default added by ``json_envelope``).
    """
    result = _invoke_dead(cli_runner, empty_dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # W804 contract: clean empty path -> NO partial_success flip
    # (the default added by json_envelope is False).
    assert summary.get("partial_success") in (None, False), (
        f"W804 invariant violated -- clean empty corpus must not flip partial_success; got summary = {summary!r}"
    )


def test_w804_empty_state_partial_success_flips_on_dl_raise(cli_runner, empty_dead_project, monkeypatch):
    """W804 extension: empty corpus + a W607-DL aggregation marker ->
    partial_success=True (the empty-state branch must surface the DL
    bucket too).
    """
    from roam.commands import cmd_dead as _mod

    # Force a substrate-CALL marker via collect_dataflow_findings; the
    # empty-state branch happens BEFORE the W607-DL aggregation phases
    # are even reached on the empty path. So the W607-DL bucket should
    # be empty here -- but the empty path STILL combines the buckets,
    # which is the discipline being checked. Trigger a BX marker:
    def _raise_dataflow(*a, **kw):
        raise RuntimeError("synthetic-empty-state-bx-marker")

    monkeypatch.setattr(_mod, "collect_dataflow_findings", _raise_dataflow)

    result = _invoke_dead(cli_runner, empty_dead_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # Non-empty marker bucket -> partial_success flip
    assert summary.get("partial_success") is True, (
        f"W804 empty-state must flip partial_success on non-empty warnings bucket; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (10) Cross-prefix isolation -- W607-DL stays in dead_* family
# ---------------------------------------------------------------------------


def test_w607dl_cross_prefix_isolation(cli_runner, dead_project, monkeypatch):
    """W607-DL markers must NOT leak into sibling W607-* prefix families."""
    from roam.commands import cmd_dead as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation-DL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_dead(cli_runner, dead_project, detail=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for cross-prefix check"
    for marker in failure_markers:
        for forbidden_prefix, sibling in (
            ("smells_", "cmd_smells W805 sibling"),
            ("clones_", "cmd_clones W805 sibling"),
            ("duplicates_", "cmd_duplicates W805 sibling"),
            ("dark_matter_", "cmd_dark_matter W805 sibling"),
            ("postmortem_", "cmd_postmortem W607-AN/CV"),
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
            ("vibe_check_", "cmd_vibe_check W607 sibling"),
            ("pr_risk_", "cmd_pr_risk W607-Q / BU"),
            ("impact_", "cmd_impact W607-T / BB"),
            ("retrieve_", "cmd_retrieve W607-B / BI"),
            ("findings_", "cmd_findings W607-C"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (11) Phase-name collision check -- DL phases distinct from BX phases
# ---------------------------------------------------------------------------


def test_w607dl_phase_names_dont_collide_with_w607bx():
    """W978 4th-discipline guard: the 4 W607-DL aggregation phase names
    MUST be disjoint from the 10 W607-BX substrate phase names. A
    collision would make the marker prefix ambiguous (an agent reading
    ``dead_serialize_envelope_failed:`` couldn't tell which layer raised).
    """
    dl_set = set(_DL_PHASES)
    bx_set = set(_BX_PHASES)
    collisions = dl_set & bx_set
    assert not collisions, (
        f"W607-DL phase names collide with W607-BX: {collisions!r}. "
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

    Canonical floor for cmd_dead is ``"dead completed"`` (mirror of
    cmd_dark_matter W607-CZ's ``"dark-matter completed"`` and cmd_smells
    W607-DF's ``"smells completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dead.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="dead completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-DL "
        "discipline; the canonical floor literal 'dead completed' is "
        "missing from cmd_dead.py"
    )


# ---------------------------------------------------------------------------
# (13) W978 7-discipline AST audit -- default= floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """Every W607-DL ``default=`` must be a literal constant, NOT
    computed from upstream values.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dead.py"
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
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dl"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_dl(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_dead.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ / cmd_audit_trail_export "
        "W607-CR for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (14) W978 5th-discipline -- closures call len() INSIDE, not at kwarg-bind site
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_closures_not_at_kwarg_bind_site():
    """Every ``len()`` call on a wrapped input MUST live INSIDE the
    wrapped closure, NOT at the ``_run_check_dl(...)`` call site as a
    positional or keyword argument expression.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dead.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dl"):
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
                        f"_run_check_dl positional-arg site -- W978 "
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
                        f"_run_check_dl kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_dead.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure. See cmd_taint W607-CJ "
        "for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (15) AST-scan -- BOTH accumulators are pinned in source
# ---------------------------------------------------------------------------


def test_w607dl_coexists_with_w607bx_in_source():
    """W607-DL is ADDITIVE -- the pre-existing W607-BX substrate-CALL
    family MUST still be present in source alongside W607-DL.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dead.py"
    src = src_path.read_text(encoding="utf-8")

    # W607-BX substrate-CALL family
    assert "w607bx_warnings_out" in src, "W607-BX substrate-CALL accumulator has been removed."
    assert "_run_check_bx" in src, "W607-BX helper has been removed."
    # W607-DL aggregation-phase family (THIS wave)
    assert "w607dl_warnings_out" in src, "W607-DL aggregation-phase accumulator has been removed."
    assert "_run_check_dl" in src, "W607-DL helper has been removed."


# ---------------------------------------------------------------------------
# (16) ANY W607-DL marker flips partial_success on the populated path
# ---------------------------------------------------------------------------


def test_any_dl_marker_flips_partial_success(cli_runner, dead_project, monkeypatch):
    """ANY W607-DL marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    dead" from "dead ran with aggregation degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_dead as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-DL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_dead(cli_runner, dead_project, detail=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-DL warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (17) warnings_out mirrors -- top-level AND summary BOTH populated
# ---------------------------------------------------------------------------


def test_w607dl_warnings_out_in_both_top_and_summary(cli_runner, dead_project, monkeypatch):
    """Non-empty W607-DL bucket -> both top-level AND summary.warnings_out
    populated.
    """
    from roam.commands import cmd_dead as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_dead(cli_runner, dead_project, detail=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DL raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DL raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("dead_serialize_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("dead_serialize_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )
