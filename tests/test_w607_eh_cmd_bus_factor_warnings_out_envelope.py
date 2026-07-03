"""W607-EH -- additive aggregation-phase plumbing for ``cmd_bus_factor``.

cmd_bus_factor is the team-coupling / single-author-risk detector (W115
origin per CLAUDE.md detector roster -- part of the original 16
findings-registry substrate detectors). The W607-CQ wave installed
substrate-CALL plumbing around the 10 substrate helpers
(``analyse_bus_factor`` / ``query_brain_methods`` /
``detect_project_shape`` / ``apply_solo_author_collapse`` /
``emit_solo_author_summary`` / ``emit_bus_factor_findings`` /
``aggregate_risk_counts`` / ``compose_verdict`` /
``build_envelope_directories`` / ``serialize_to_sarif``).

This W607-EH wave layers an ADDITIVE aggregation-phase plumbing on top
of that substrate, mirroring the canonical 4-phase shape that
cmd_auth_gaps W607-CM+ED, cmd_n1 W607-DQ, cmd_over_fetch W607-DT, and
cmd_missing_index W607-DX use:

  - substrate-CALL layer: W607-CQ (10 boundaries -- see _CQ_PHASES below)
  - aggregation-phase layer: W607-EH (4 boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``bus_factor_*`` marker family and the
``bus_factor_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two bucket sources (``_w607cq_warnings_out`` substrate-CALL +
``_w607eh_warnings_out`` aggregation-phase) are merged at envelope-emit
time into ``warnings_out`` so consumers see the full degradation
lineage. The phase names DO NOT collide -- CQ substrate phases are
``analyse_bus_factor`` / ``build_envelope_directories`` / etc.,
aggregation phases are ``score_classify`` / ``compute_predicate`` /
``compute_verdict`` / ``serialize_envelope``.

W978 7-discipline first-hypothesis check
----------------------------------------

Every W607-EH ``default=`` MUST be a literal constant, AND every
``len()`` / ``sum()`` over the wrapped input MUST live inside the
closure. The AST audit below pins these disciplines at the W607-EH
layer (mirror of the W607-ED audit on cmd_auth_gaps).

W164 + W817 + W978 PRESERVATION
-------------------------------

W164 layered the solo-author summary collapse. W817 sealed the
Pattern-2 empty-corpus regression (explicit zero-count verdict on
both the no-data branch and the ranked branch). W978 first-hypothesis
discipline was applied at W607-CQ. The regression-guard tests below
confirm:

  1. The clean populated path still emits a single-line verdict
     (the W607-CQ ``_compose_verdict`` substrate result is passed
     through unchanged via the W607-EH ``compute_verdict`` boundary).
  2. The W607-EH aggregation boundary does NOT re-introduce Pattern-2
     silent-fallback -- a raise in ``json_envelope`` still emits a
     non-empty floor stub with a marker AND ``partial_success: True``,
     never a SAFE verdict on a degraded state (W817 preservation).
  3. The W164 ``_emit_solo_author_summary_finding`` reference is
     preserved in source (the additive aggregation-layer wrapping must
     not touch the detector logic).

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
# Canonical W607-EH phase enumeration
# ---------------------------------------------------------------------------


_EH_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)

_CQ_PHASES = (
    "analyse_bus_factor",
    "query_brain_methods",
    "detect_project_shape",
    "apply_solo_author_collapse",
    "emit_solo_author_summary",
    "emit_bus_factor_findings",
    "aggregate_risk_counts",
    "compose_verdict",
    "build_envelope_directories",
    "serialize_to_sarif",
)


# ---------------------------------------------------------------------------
# Fixtures (mirror of the W607-CQ fixture so both files exercise the
# same indexed corpus)
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _populated_schema() -> str:
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
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS git_commits (
            id INTEGER PRIMARY KEY,
            sha TEXT NOT NULL UNIQUE,
            author TEXT,
            timestamp INTEGER,
            message TEXT
        );
        CREATE TABLE IF NOT EXISTS git_file_changes (
            id INTEGER PRIMARY KEY,
            commit_id INTEGER NOT NULL,
            file_id INTEGER,
            lines_added INTEGER DEFAULT 0,
            lines_removed INTEGER DEFAULT 0,
            FOREIGN KEY(commit_id) REFERENCES git_commits(id),
            FOREIGN KEY(file_id) REFERENCES files(id)
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


def _build_bus_factor_project(tmp_path: Path) -> Path:
    """Mirror of the W607-CQ fixture -- one Python file + one commit so
    ``_analyse_bus_factor`` has SOMETHING to crunch.
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
    conn.execute(
        "INSERT INTO git_commits (id, sha, author, timestamp, message) VALUES "
        "(1, 'deadbeef', 'Test <t@t.com>', 1700000000, 'init')"
    )
    conn.execute("INSERT INTO git_file_changes (commit_id, file_id, lines_added, lines_removed) VALUES (1, 1, 2, 0)")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def bus_factor_project(tmp_path):
    return _build_bus_factor_project(tmp_path)


def _invoke_bus_factor(cli_runner, project_root, *args, json_mode=True, sarif=False):
    from roam.commands.cmd_bus_factor import bus_factor

    obj = {"json": json_mode, "sarif": sarif, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(bus_factor, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-EH aggregation markers
# ---------------------------------------------------------------------------


def test_bus_factor_happy_path_no_w607eh_markers(cli_runner, bus_factor_project):
    """Clean bus-factor run -> no W607-EH aggregation markers."""
    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "bus-factor"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _EH_PHASES:
        prefix = f"bus_factor_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean bus-factor must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive _run_check_eh helper + accumulator
# ---------------------------------------------------------------------------


def test_cmd_bus_factor_carries_w607eh_accumulator():
    """AST-level guard: cmd_bus_factor source carries the W607-EH
    anchors AND the pre-existing W607-CQ layer.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    assert src_path.exists(), f"cmd_bus_factor.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    assert "_w607eh_warnings_out" in src, (
        "W607-EH accumulator missing from cmd_bus_factor; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_eh" in src, (
        "W607-EH helper ``_run_check_eh`` missing from cmd_bus_factor; the additive wrapper has been refactored away."
    )

    tree = ast.parse(src)
    found_run_check_eh = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_eh":
            found_run_check_eh = True
            break
    assert found_run_check_eh, (
        "W607-EH ``_run_check_eh`` helper not found in cmd_bus_factor AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-CQ must still be present (additive layer does NOT replace it)
    assert "_w607cq_warnings_out" in src, (
        "W607-CQ accumulator vanished alongside the W607-EH add; the "
        "additive plumbing must preserve the W607-CQ substrate-CALL layer."
    )
    assert "_run_check_cq" in src, "W607-CQ helper has been removed."


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_eh():
    """Every aggregation-phase boundary calls ``_run_check_eh(...)`` with
    the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _EH_PHASES:
        same_line = f'_run_check_eh("{phase}"' in src
        multi_line = any(f'_run_check_eh(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"bus_factor_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-EH wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) Per-phase isolation -- serialize_envelope raise -> marker + floor stub
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, bus_factor_project, monkeypatch):
    """If ``json_envelope`` raises on the populated path, the wrap floors
    to a parseable envelope stub and surfaces
    ``bus_factor_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_bus_factor as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-EH")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "bus-factor", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("bus_factor_serialize_envelope_failed:")]
    assert markers, f"expected ``bus_factor_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) Per-phase isolation -- compute_verdict floor is a single line
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_a_single_line(cli_runner, bus_factor_project):
    """Compute-verdict boundary -- the verdict string on the clean path
    MUST be a single line (LAW 6 standalone-parse discipline).
    """
    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"LAW 6: compute_verdict must produce a single line; got {verdict!r}"


# ---------------------------------------------------------------------------
# (6) Per-phase isolation -- score_classify surfaces run_state on summary
# ---------------------------------------------------------------------------


def test_score_classify_surfaces_run_state(cli_runner, bus_factor_project):
    """Clean run -> the run_state must be present and in the canonical
    closed enumeration.
    """
    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("run_state") in {
        "HEALTHY",
        "WARN",
        "CRITICAL",
        "DEGRADED",
    }, f"run_state missing/invalid on clean bus-factor envelope; got {summary.get('run_state')!r}"


# ---------------------------------------------------------------------------
# (7) Per-phase isolation -- compute_predicate surfaces rollup fields
# ---------------------------------------------------------------------------


def test_compute_predicate_surfaces_rollup_fields(cli_runner, bus_factor_project):
    """Compute-predicate boundary -- happy path surfaces solo_authored_count,
    low_contributor_count, hottest_files rollup on the summary.
    """
    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    assert "solo_authored_count" in summary, (
        f"compute_predicate must surface solo_authored_count; got summary keys = {sorted(summary.keys())!r}"
    )
    assert isinstance(summary["solo_authored_count"], int), (
        f"solo_authored_count must be an int; got {type(summary['solo_authored_count']).__name__!r}"
    )
    assert "low_contributor_count" in summary, (
        f"compute_predicate must surface low_contributor_count; got summary keys = {sorted(summary.keys())!r}"
    )
    assert isinstance(summary["low_contributor_count"], int), (
        f"low_contributor_count must be an int; got {type(summary['low_contributor_count']).__name__!r}"
    )
    assert "hottest_files" in summary, (
        f"compute_predicate must surface hottest_files rollup; got summary keys = {sorted(summary.keys())!r}"
    )
    assert isinstance(summary["hottest_files"], list), (
        f"hottest_files must be a list; got {type(summary['hottest_files']).__name__!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-CQ substrate + W607-EH aggregation markers BOTH surface
# ---------------------------------------------------------------------------


def test_w607cq_substrate_and_w607eh_aggregation_coexist(cli_runner, bus_factor_project, monkeypatch):
    """When BOTH layers fault, BOTH marker prefixes surface.

    Selects a W607-CQ substrate name (analyse_bus_factor) + the W607-EH
    serialize_envelope boundary so both layers produce a marker on the
    same invocation.
    """
    from roam.commands import cmd_bus_factor as _mod

    def _raise_analyse(*a, **kw):
        raise RuntimeError("synthetic-cq-coexist-analyse")

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-eh-coexist-envelope")

    monkeypatch.setattr(_mod, "_analyse_bus_factor", _raise_analyse)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    cq_markers = [m for m in top_wo if m.startswith("bus_factor_analyse_bus_factor_failed:")]
    eh_markers = [m for m in top_wo if m.startswith("bus_factor_serialize_envelope_failed:")]

    assert cq_markers, f"W607-CQ substrate-CALL marker (bus_factor_analyse_bus_factor_failed) missing; got {top_wo!r}"
    assert eh_markers, (
        f"W607-EH aggregation-phase marker (bus_factor_serialize_envelope_failed) missing; got {top_wo!r}"
    )

    # Both share the canonical ``bus_factor_*`` family
    assert all(m.startswith("bus_factor_") for m in (cq_markers + eh_markers)), (
        f"all markers must share the canonical ``bus_factor_*`` family; got cq = {cq_markers!r}, eh = {eh_markers!r}"
    )


# ---------------------------------------------------------------------------
# (9) W817 partial_success seal preserved
# ---------------------------------------------------------------------------


def test_w817_partial_success_flips_on_eh_raise(cli_runner, bus_factor_project, monkeypatch):
    """W817 seal: a degraded path MUST flip partial_success=True. The
    W607-EH aggregation layer must preserve this on the raise path --
    a synthetic ``json_envelope`` raise produces a floor stub with
    ``partial_success: True`` in its summary.
    """
    from roam.commands import cmd_bus_factor as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-W817-partial-success-from-EH")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("partial_success") is True, (
        f"W817 seal: non-empty W607-EH warnings_out must flip summary.partial_success=True; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (10) W164 solo-author collapse reference preserved in source
# ---------------------------------------------------------------------------


def test_w164_solo_author_collapse_invariant_preserved_in_source():
    """W164 solo-author summary collapse path must be preserved in
    cmd_bus_factor source after the additive W607-EH aggregation
    plumbing. The aggregation-layer wrapping must not touch the
    detector logic.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    src = src_path.read_text(encoding="utf-8")

    # W164 collapse helper is the canonical pin
    assert "_emit_solo_author_summary_finding" in src, (
        "W164 _emit_solo_author_summary_finding reference removed; "
        "the solo-author summary collapse path is no longer wired."
    )
    # W164 documentation reference
    assert "W164" in src, (
        "W164 reference comment vanished from cmd_bus_factor; the "
        "solo-author summary collapse rationale is no longer documented."
    )
    # W164 single_author_mode gating is the canonical entry point
    assert "single_author_mode" in src, "W164 single_author_mode gate has been removed from cmd_bus_factor."


# ---------------------------------------------------------------------------
# (11) Cross-prefix isolation -- W607-EH stays in bus_factor_* family
# ---------------------------------------------------------------------------


def test_w607eh_cross_prefix_isolation(cli_runner, bus_factor_project, monkeypatch):
    """W607-EH markers must NOT leak into sibling W607-* prefix families."""
    from roam.commands import cmd_bus_factor as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation-EH")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for cross-prefix check"
    for marker in failure_markers:
        assert marker.startswith("bus_factor_"), (
            f"every surfaced W607-EH marker must use the "
            f"``bus_factor_*`` prefix family (cmd_bus_factor scope); "
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
            ("hotspots_", "cmd_hotspots W607-* sibling detector"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("dark_matter_", "cmd_dark_matter W607-BK / CZ"),
            ("debt_", "cmd_debt W607-BG"),
            ("health_", "cmd_health W607-M / BA"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) Phase-name collision check -- EH phases distinct from CQ phases
# ---------------------------------------------------------------------------


def test_w607eh_phase_names_dont_collide_with_w607cq():
    """W978 4th-discipline guard: the 4 W607-EH aggregation phase names
    MUST be disjoint from the 10 W607-CQ substrate phase names.
    """
    eh_set = set(_EH_PHASES)
    cq_set = set(_CQ_PHASES)
    collisions = eh_set & cq_set
    assert not collisions, (
        f"W607-EH phase names collide with W607-CQ: {collisions!r}. "
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

    Canonical floor for cmd_bus_factor is ``"bus_factor completed"``
    (mirror of cmd_auth_gaps W607-ED ``"auth_gaps completed"`` /
    cmd_missing_index W607-DX ``"missing_index completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="bus_factor completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-EH "
        "discipline; the canonical floor literal 'bus_factor completed' "
        "is missing from cmd_bus_factor.py"
    )


# ---------------------------------------------------------------------------
# (14) W978 7-discipline AST audit -- default= floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """Every W607-EH ``default=`` must be a literal constant, NOT
    computed from upstream values.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
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
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_eh"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_eh(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_bus_factor.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants."
    )


# ---------------------------------------------------------------------------
# (15) W978 5th-discipline -- closures call len() INSIDE, not at kwarg-bind
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_closures_not_at_kwarg_bind_site():
    """Every ``len()`` call on a wrapped input MUST live INSIDE the
    wrapped closure, NOT at the ``_run_check_eh(...)`` call site as a
    positional or keyword argument expression.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_eh"):
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
                        f"_run_check_eh positional-arg site -- W978 "
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
                        f"_run_check_eh kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_bus_factor.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure."
    )


# ---------------------------------------------------------------------------
# (16) AST-scan -- BOTH accumulators are pinned in source (CQ + EH)
# ---------------------------------------------------------------------------


def test_w607eh_coexists_with_w607cq_in_source():
    """W607-EH is ADDITIVE -- the pre-existing W607-CQ substrate-CALL
    family MUST still be present in source alongside W607-EH.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    src = src_path.read_text(encoding="utf-8")

    assert "_w607cq_warnings_out" in src, "W607-CQ substrate-CALL accumulator has been removed."
    assert "_run_check_cq" in src, "W607-CQ helper has been removed."
    assert "_w607eh_warnings_out" in src, "W607-EH aggregation-phase accumulator has been removed."
    assert "_run_check_eh" in src, "W607-EH helper has been removed."


# ---------------------------------------------------------------------------
# (17) ANY W607-EH marker flips partial_success on the populated path
# ---------------------------------------------------------------------------


def test_any_eh_marker_flips_partial_success(cli_runner, bus_factor_project, monkeypatch):
    """ANY W607-EH marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    bus-factor" from "bus-factor ran with aggregation degradation"
    via summary.partial_success alone.
    """
    from roam.commands import cmd_bus_factor as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-EH")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-EH warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (18) warnings_out mirrors -- top-level AND summary BOTH populated
# ---------------------------------------------------------------------------


def test_w607eh_warnings_out_in_both_top_and_summary(cli_runner, bus_factor_project, monkeypatch):
    """Non-empty W607-EH bucket -> both top-level AND summary.warnings_out
    populated.
    """
    from roam.commands import cmd_bus_factor as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-EH")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-EH raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-EH raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("bus_factor_serialize_envelope_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("bus_factor_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (19) Helper-template ``return default`` verbatim shape -- W607-DW pin
# ---------------------------------------------------------------------------


def test_run_check_eh_helper_returns_default_verbatim():
    """W607-DW regression guard: the ``_run_check_eh`` helper body must
    end with ``return default`` (verbatim) -- NOT
    ``return default if default is not None else {}``.

    The W607-DP/DW finding identified that an "improved" default-coerce
    return shape silently masks the floor literal.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_helper = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_eh"):
            continue
        found_helper = True
        try_stmt = None
        for stmt in node.body:
            if isinstance(stmt, ast.Try):
                try_stmt = stmt
                break
        assert try_stmt is not None, (
            f"_run_check_eh body must contain a try/except block; got {[type(s).__name__ for s in node.body]!r}"
        )
        assert try_stmt.handlers, "_run_check_eh try-block must have at least one except-handler"
        last_handler = try_stmt.handlers[-1]
        last_stmt = last_handler.body[-1]
        assert isinstance(last_stmt, ast.Return), (
            f"_run_check_eh except-handler must end with a Return statement; got {type(last_stmt).__name__!r}"
        )
        assert isinstance(last_stmt.value, ast.Name), (
            f"_run_check_eh return value must be a bare ``default`` Name "
            f"node (W607-DW verbatim shape); got "
            f"{type(last_stmt.value).__name__!r}"
        )
        assert last_stmt.value.id == "default", (
            f"_run_check_eh return value must reference the ``default`` parameter; got Name(id={last_stmt.value.id!r})"
        )
        break

    assert found_helper, "_run_check_eh helper not found in cmd_bus_factor AST"


# ---------------------------------------------------------------------------
# (20) score_classify isolation -- clean populated path is not DEGRADED
# ---------------------------------------------------------------------------


def test_score_classify_isolation_clean_path_not_degraded(cli_runner, bus_factor_project):
    """Per-phase isolation guard: a clean populated run (single commit,
    single Python file) surfaces a non-DEGRADED ``run_state`` -- DEGRADED
    is reserved for the raise path / empty-results floor.

    The toy fixture has 1 author + 1 file so the bus_factor==1 path
    lands the CRITICAL bucket on the clean rank path. The point of the
    test is that the floor literal DEGRADED is NOT what we get on a
    happy populated run.
    """
    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    state = summary.get("run_state")
    assert state in {"HEALTHY", "WARN", "CRITICAL"}, (
        f"clean populated bus-factor run must surface one of HEALTHY/WARN/CRITICAL; got run_state={state!r}"
    )
    # DEGRADED is the floor -- it must NOT surface on the clean path.
    assert state != "DEGRADED", f"DEGRADED run_state must be reserved for the raise floor; got summary = {summary!r}"


# ---------------------------------------------------------------------------
# (21) compute_predicate floor dict shape -- W978 6th-discipline
# ---------------------------------------------------------------------------


def test_compute_predicate_floor_dict_shape():
    """W978 6th-discipline: compute_predicate floor MUST be a concrete
    dict carrying all 3 documented keys (solo_authored_count /
    low_contributor_count / hottest_files), NOT a sentinel that may
    __len__-raise downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_predicate_floor = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_eh"):
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
                "solo_authored_count",
                "low_contributor_count",
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
        "compute_predicate _run_check_eh call site not found in source; "
        "the aggregation boundary has been refactored away."
    )


# ---------------------------------------------------------------------------
# (22) score_classify floor dict shape -- W978 6th-discipline
# ---------------------------------------------------------------------------


def test_score_classify_floor_dict_shape():
    """W978 6th-discipline: score_classify floor MUST be a concrete
    dict carrying ``state: "DEGRADED"`` + ``scanned: 0``.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_score_floor = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_eh"):
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
        "score_classify _run_check_eh call site not found in source; the aggregation boundary has been refactored away."
    )


# ---------------------------------------------------------------------------
# (23) Both accumulators pinned in AST (CQ + EH at function-scope level)
# ---------------------------------------------------------------------------


def test_ast_audit_both_accumulators_present_in_bus_factor_function():
    """AST audit: both ``_w607cq_warnings_out`` and ``_w607eh_warnings_out``
    are assigned inside the ``bus_factor`` click command body. A
    regression where one is moved out of scope (or silently dropped)
    would break the two-layer marker plumbing.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    cq_found = False
    eh_found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "bus_factor":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.AnnAssign):
                # Try plain Assign too
                if isinstance(sub, ast.Assign):
                    for tgt in sub.targets:
                        if isinstance(tgt, ast.Name):
                            if tgt.id == "_w607cq_warnings_out":
                                cq_found = True
                            elif tgt.id == "_w607eh_warnings_out":
                                eh_found = True
                continue
            tgt = sub.target
            if isinstance(tgt, ast.Name):
                if tgt.id == "_w607cq_warnings_out":
                    cq_found = True
                elif tgt.id == "_w607eh_warnings_out":
                    eh_found = True

    assert cq_found, (
        "AST audit: ``_w607cq_warnings_out`` accumulator not assigned "
        "inside the bus_factor click command function body."
    )
    assert eh_found, (
        "AST audit: ``_w607eh_warnings_out`` accumulator not assigned "
        "inside the bus_factor click command function body."
    )


# ---------------------------------------------------------------------------
# (24) Helper marker template is shared -- both helpers use bus_factor_* family
# ---------------------------------------------------------------------------


def test_both_helpers_use_bus_factor_marker_family():
    """Source-level audit: both ``_run_check_cq`` and ``_run_check_eh``
    must emit markers under the canonical ``bus_factor_*`` family so a
    consumer regex spans both layers without rework.

    The marker construction now lives in the shared ``boundary_helpers``
    module; each wrapper pins the recipe name (``bus_factor``) at the
    ``make_run_check`` call site.
    """
    cmd_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    helper_src_path = (
        Path(__file__).parent.parent / "src" / "roam" / "commands" / "boundary_helpers.py"
    )
    cmd_src = cmd_src_path.read_text(encoding="utf-8")
    helper_src = helper_src_path.read_text(encoding="utf-8")

    # Generic marker template lives in the shared helper.
    assert 'f"{recipe_name}_{phase}_failed:{type(exc).__name__}:{exc}"' in helper_src, (
        "Canonical W607 marker f-string template must live in boundary_helpers."
    )
    # Both wrappers route through the helper with the bus_factor recipe name.
    assert cmd_src.count('make_run_check("bus_factor",') >= 2, (
        "Both ``_run_check_cq`` and ``_run_check_eh`` must emit markers "
        "under the canonical bus_factor_<phase>_failed:<exc>:<detail> "
        "family; expected both wrappers to call make_run_check(\"bus_factor\", ...)."
    )
