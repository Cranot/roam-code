"""W607-ED -- additive aggregation-phase plumbing for ``cmd_auth_gaps``.

cmd_auth_gaps is the authentication-gap detector (W116 origin per
CLAUDE.md detector roster -- part of the original 16 findings-registry
substrate detectors). The W607-CM wave installed substrate-CALL
plumbing around the 12 substrate helpers (``find_route_files`` /
``find_service_provider_files`` / ``find_controller_files`` /
``analyze_route_file`` / ``analyze_service_provider`` /
``build_class_source_map`` / ``analyze_controller_file`` /
``apply_confidence_filter`` / ``sort_findings`` /
``aggregate_by_confidence`` / ``emit_findings`` /
``serialize_to_sarif``).

This W607-ED wave layers an ADDITIVE aggregation-phase plumbing on top
of that substrate, mirroring the canonical 4-phase shape that
cmd_n1 W607-DQ + cmd_over_fetch W607-DT + cmd_missing_index W607-DX
use:

  - substrate-CALL layer: W607-CM (12 boundaries -- see _CM_PHASES below)
  - aggregation-phase layer: W607-ED (4 boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``auth_gaps_*`` marker family and the
``auth_gaps_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two bucket sources (``_w607cm_warnings_out`` substrate-CALL +
``_w607ed_warnings_out`` aggregation-phase) are merged at envelope-emit
time into ``warnings_out`` so consumers see the full degradation
lineage. The phase names DO NOT collide -- CM substrate phases are
``find_route_files`` / ``analyze_controller_file`` / etc.,
aggregation phases are ``score_classify`` / ``compute_predicate`` /
``compute_verdict`` / ``serialize_envelope``.

W978 7-discipline first-hypothesis check
----------------------------------------

cmd_sbom W607-CG sealed the kwarg-default eagerness trap (computed
defaults eval BEFORE the try-block).
cmd_taint W607-CJ codified the 5th discipline: move ``len()`` INSIDE
the wrapped closure rather than at the kwarg-bind site.
cmd_audit_trail_export W607-CR codified the 7th discipline: use bare
``dict[key]`` lookup when a floor dict guarantees the key, NOT
``dict.get(key, expensive_default)``.

Every W607-ED ``default=`` MUST be a literal constant, AND every
``len()`` / ``sum()`` over the wrapped input MUST live inside the
closure. The AST audit below pins these disciplines at the W607-ED
layer.

W815 + W818 + W36.7 + W36.10 PRESERVATION
-----------------------------------------

W815 sealed the cmd_auth_gaps empty-corpus smoke with the explicit
zero-count verdict. W818 set the partial_success seal. W36.7
introduced helper-indirection auth-gap detection. W36.10 / W147
introduced the depth-2 ancestor descent path. The regression-guard
tests below confirm:

  1. The clean empty-corpus path still emits ``0 auth gap(s) found``
     (the W815 contract) with no W607-ED markers.
  2. The W607-ED aggregation boundary does NOT re-introduce Pattern-2
     silent-fallback -- a raise in ``json_envelope`` still emits a
     non-empty floor stub with a marker AND ``partial_success: True``,
     never a SAFE verdict on a degraded state (W818 preservation).
  3. The W36.7 helper-indirection + W36.10 ancestor-descent constants
     are still present in cmd_auth_gaps source (the additive
     aggregation-layer wrapping must not touch the detector logic).

SECURITY-DETECTOR 3-WAY PAIRING
-------------------------------

cmd_taint W607-AY+CJ (taint detector), cmd_vulns W607-AQ+CH (vuln
detector), and cmd_auth_gaps W607-CM+ED (this wave) are the 3
security-family detectors with BOTH substrate-CALL AND
aggregation-phase plumbing landed. The security-3-way pairing pin
AST-scans all three modules to confirm both layers are present in
each -- a structural invariant that should never silently break.

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
# Canonical W607-ED phase enumeration
# ---------------------------------------------------------------------------


_ED_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)

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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _populated_schema() -> str:
    """Return the canonical schema script for a roam-indexed project."""
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


def _build_auth_gaps_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_auth_gaps.

    Builds a tiny Python fixture (NO PHP, NO routes, NO controllers) so
    the detector runs cleanly with the W815 zero-count envelope -- the
    tests focus on W607-ED marker plumbing rather than the detector
    verdict on real Laravel apps.
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


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-ED aggregation markers
# ---------------------------------------------------------------------------


def test_auth_gaps_happy_path_no_w607ed_markers(cli_runner, auth_gaps_project):
    """Clean auth-gaps run -> no W607-ED aggregation markers."""
    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "auth-gaps"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _ED_PHASES:
        prefix = f"auth_gaps_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean auth-gaps must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive _run_check_ed helper + accumulator
# ---------------------------------------------------------------------------


def test_cmd_auth_gaps_carries_w607ed_accumulator():
    """AST-level guard: cmd_auth_gaps source carries the W607-ED
    anchors AND the pre-existing W607-CM layer.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    assert src_path.exists(), f"cmd_auth_gaps.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    assert "w607ed_warnings_out" in src, (
        "W607-ED accumulator missing from cmd_auth_gaps; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_ed" in src, (
        "W607-ED helper ``_run_check_ed`` missing from cmd_auth_gaps; the additive wrapper has been refactored away."
    )

    tree = ast.parse(src)
    found_run_check_ed = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ed":
            found_run_check_ed = True
            break
    assert found_run_check_ed, (
        "W607-ED ``_run_check_ed`` helper not found in cmd_auth_gaps AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-CM must still be present (additive layer does NOT replace it)
    assert "w607cm_warnings_out" in src, (
        "W607-CM accumulator vanished alongside the W607-ED add; the "
        "additive plumbing must preserve the W607-CM substrate-CALL layer."
    )
    assert "_run_check_cm" in src, "W607-CM helper has been removed."


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_ed():
    """Every aggregation-phase boundary calls ``_run_check_ed(...)`` with
    the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _ED_PHASES:
        same_line = f'_run_check_ed("{phase}"' in src
        multi_line = any(f'_run_check_ed(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"auth_gaps_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-ED wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) Per-phase isolation -- serialize_envelope raise -> marker + floor stub
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, auth_gaps_project, monkeypatch):
    """If ``json_envelope`` raises on the populated path, the wrap floors
    to a parseable envelope stub and surfaces
    ``auth_gaps_serialize_envelope_failed:``.
    """
    from roam.commands import cmd_auth_gaps as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-ED")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    assert data.get("command") == "auth-gaps", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("auth_gaps_serialize_envelope_failed:")]
    assert markers, f"expected ``auth_gaps_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) Per-phase isolation -- compute_verdict floor is a single line
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_a_single_line(cli_runner, auth_gaps_project):
    """Compute-verdict boundary -- the verdict string on the clean path
    MUST be a single line (LAW 6 standalone-parse discipline).
    """
    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"LAW 6: compute_verdict must produce a single line; got {verdict!r}"


# ---------------------------------------------------------------------------
# (6) Per-phase isolation -- score_classify surfaces run_state on summary
# ---------------------------------------------------------------------------


def test_score_classify_surfaces_run_state(cli_runner, auth_gaps_project):
    """Clean run -> the run_state must be present and in the canonical
    closed enumeration.
    """
    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("run_state") in {
        "NO_AUTH_GAPS",
        "LIGHT",
        "MODERATE",
        "HEAVY",
        "DEGRADED",
    }, f"run_state missing/invalid on clean auth-gaps envelope; got {summary.get('run_state')!r}"


# ---------------------------------------------------------------------------
# (7) Per-phase isolation -- compute_predicate surfaces rollup fields
# ---------------------------------------------------------------------------


def test_compute_predicate_surfaces_rollup_fields(cli_runner, auth_gaps_project):
    """Compute-predicate boundary -- happy path surfaces by_kind /
    files_affected / endpoints_affected rollup on the summary.
    """
    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
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
    assert "endpoints_affected" in summary, (
        f"compute_predicate must surface endpoints_affected rollup; got summary keys = {sorted(summary.keys())!r}"
    )
    assert isinstance(summary["endpoints_affected"], int), (
        f"endpoints_affected must be an int; got {type(summary['endpoints_affected']).__name__!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-CM substrate + W607-ED aggregation markers BOTH surface
# ---------------------------------------------------------------------------


def test_w607cm_substrate_and_w607ed_aggregation_coexist(cli_runner, auth_gaps_project, monkeypatch):
    """When BOTH layers fault, BOTH marker prefixes surface.

    Selects a W607-CM substrate name + the W607-ED serialize_envelope
    boundary so both layers produce a marker on the same invocation.
    """
    from roam.commands import cmd_auth_gaps as _mod

    # W607-CM substrate boundary -- _find_route_files raises
    def _raise_routes(*a, **kw):
        raise RuntimeError("synthetic-cm-coexist-route-files")

    # W607-ED aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-ed-coexist-envelope")

    monkeypatch.setattr(_mod, "_find_route_files", _raise_routes)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL marker from W607-CM (find_route_files)
    cm_markers = [m for m in top_wo if m.startswith("auth_gaps_find_route_files_failed:")]
    # Aggregation-phase marker from W607-ED (serialize_envelope)
    ed_markers = [m for m in top_wo if m.startswith("auth_gaps_serialize_envelope_failed:")]

    assert cm_markers, f"W607-CM substrate-CALL marker (auth_gaps_find_route_files_failed) missing; got {top_wo!r}"
    assert ed_markers, f"W607-ED aggregation-phase marker (auth_gaps_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``auth_gaps_*`` family
    assert all(m.startswith("auth_gaps_") for m in (cm_markers + ed_markers)), (
        f"all markers must share the canonical ``auth_gaps_*`` family; got cm = {cm_markers!r}, ed = {ed_markers!r}"
    )


# ---------------------------------------------------------------------------
# (9) W815 empty-corpus regression -- zero-count verdict preserved
# ---------------------------------------------------------------------------


def test_w815_zero_count_verdict_preserved(cli_runner, auth_gaps_project):
    """W815 invariant: empty corpus -> ``0 auth gap(s) found`` verdict on
    the clean path. The W607-ED compute_verdict floor must NOT replace
    the f-string verdict on a clean run (the literal floor
    ``"auth_gaps completed"`` is reserved for the raise path).
    """
    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    verdict = data["summary"]["verdict"]
    assert verdict == "0 auth gap(s) found", (
        f"W815 invariant violated -- clean empty-corpus auth-gaps must "
        f"emit ``0 auth gap(s) found`` verdict; got {verdict!r}"
    )


# ---------------------------------------------------------------------------
# (10) W818 partial_success seal preserved
# ---------------------------------------------------------------------------


def test_w818_partial_success_flips_on_ed_raise(cli_runner, auth_gaps_project, monkeypatch):
    """W818 seal: a degraded path MUST flip partial_success=True. The
    W607-ED aggregation layer must preserve this on the raise path --
    a synthetic ``json_envelope`` raise produces a floor stub with
    ``partial_success: True`` in its summary.
    """
    from roam.commands import cmd_auth_gaps as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-W818-partial-success-from-ED")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("partial_success") is True, (
        f"W818 seal: non-empty W607-ED warnings_out must flip summary.partial_success=True; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (11) W36.7 helper-indirection + W36.10 ancestor-descent preserved
# ---------------------------------------------------------------------------


def test_w36_7_and_w36_10_invariants_preserved_in_source():
    """W36.7 helper-indirection + W36.10 / W147 depth-2 ancestor
    descent invariants must be preserved in cmd_auth_gaps source after
    the additive W607-ED aggregation plumbing. The aggregation-layer
    wrapping must not touch the detector logic.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    src = src_path.read_text(encoding="utf-8")

    # W36.10 helper-descent depth constant is the canonical pin
    assert "_HELPER_DESCENT_MAX_DEPTH" in src, (
        "W36.10 _HELPER_DESCENT_MAX_DEPTH constant has been removed; "
        "the depth-2 ancestor-descent invariant is no longer enforced."
    )
    assert "_HELPER_DESCENT_MAX_DEPTH = 2" in src, (
        "W36.10 _HELPER_DESCENT_MAX_DEPTH must equal 2 (W147 bumped from 1 to 2); current depth has drifted."
    )

    # W36.7 / W140 helper-indirection reference comment is the anchor
    assert "helper indirection" in src.lower() or "W140" in src, (
        "W36.7 / W140 helper-indirection reference vanished from "
        "cmd_auth_gaps; the detector path is no longer documented."
    )


# ---------------------------------------------------------------------------
# (12) Cross-prefix isolation -- W607-ED stays in auth_gaps_* family
# ---------------------------------------------------------------------------


def test_w607ed_cross_prefix_isolation(cli_runner, auth_gaps_project, monkeypatch):
    """W607-ED markers must NOT leak into sibling W607-* prefix families.

    Especially confirms auth_gaps_* does not collide with the adjacent
    security-family ``vulns_*`` / ``taint_*`` markers (the 3-detector
    security cluster) or other detector-family siblings.
    """
    from roam.commands import cmd_auth_gaps as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation-ED")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for cross-prefix check"
    for marker in failure_markers:
        # Every marker must use the canonical auth_gaps_* family.
        assert marker.startswith("auth_gaps_"), (
            f"every surfaced W607-ED marker must use the "
            f"``auth_gaps_*`` prefix family (cmd_auth_gaps scope); "
            f"got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            # Security-detector family siblings -- the critical cluster
            ("vulns_", "cmd_vulns W607-AQ / CH (sibling security detector)"),
            ("taint_", "cmd_taint W607-AY / CJ (sibling security detector)"),
            # ORM-family siblings
            ("n1_", "cmd_n1 W607-CB / DQ"),
            ("over_fetch_", "cmd_over_fetch W607-CE / DT"),
            ("missing_index_", "cmd_missing_index W607-CI / DX"),
            # Detector-family siblings
            ("smells_", "cmd_smells W607-BN / DF"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("clones_", "cmd_clones W607-BQ / DC"),
            ("duplicates_", "cmd_duplicates W607-BM / DD"),
            ("dead_", "cmd_dead W607-BX / DL"),
            # Other adjacent detectors
            ("complexity_", "cmd_complexity W607-BJ"),
            ("dark_matter_", "cmd_dark_matter W607-BK / CZ"),
            ("postmortem_", "cmd_postmortem W607-AN / CV"),
            ("sbom_", "cmd_sbom W607-AM / CG"),
            ("debt_", "cmd_debt W607-BG"),
            ("health_", "cmd_health W607-M / BA"),
            ("findings_", "cmd_findings W607-C"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (13) Phase-name collision check -- ED phases distinct from CM phases
# ---------------------------------------------------------------------------


def test_w607ed_phase_names_dont_collide_with_w607cm():
    """W978 4th-discipline guard: the 4 W607-ED aggregation phase names
    MUST be disjoint from the 12 W607-CM substrate phase names. A
    collision would make the marker prefix ambiguous (an agent reading
    ``auth_gaps_compute_verdict_failed:`` couldn't tell which layer
    raised).
    """
    ed_set = set(_ED_PHASES)
    cm_set = set(_CM_PHASES)
    collisions = ed_set & cm_set
    assert not collisions, (
        f"W607-ED phase names collide with W607-CM: {collisions!r}. "
        f"Rename one set so each marker phase belongs to exactly one "
        f"layer."
    )


# ---------------------------------------------------------------------------
# (14) compute_verdict floor is a literal constant -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """W978 first-hypothesis discipline anchor: compute_verdict floor
    must be a literal string, NOT an f-string re-interpolating the
    same values that just raised.

    Canonical floor for cmd_auth_gaps is ``"auth_gaps completed"``
    (mirror of cmd_missing_index W607-DX's ``"missing_index completed"`` /
    cmd_over_fetch W607-DT's ``"over_fetch completed"`` / cmd_n1
    W607-DQ's ``"n1 completed"`` / cmd_dead W607-DL's ``"dead completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="auth_gaps completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-ED "
        "discipline; the canonical floor literal 'auth_gaps completed' "
        "is missing from cmd_auth_gaps.py"
    )


# ---------------------------------------------------------------------------
# (15) W978 7-discipline AST audit -- default= floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """Every W607-ED ``default=`` must be a literal constant, NOT
    computed from upstream values.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
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
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_ed"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_ed(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_auth_gaps.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ / cmd_audit_trail_export "
        "W607-CR for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (16) W978 5th-discipline -- closures call len() INSIDE, not at kwarg-bind
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_closures_not_at_kwarg_bind_site():
    """Every ``len()`` call on a wrapped input MUST live INSIDE the
    wrapped closure, NOT at the ``_run_check_ed(...)`` call site as a
    positional or keyword argument expression.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_ed"):
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
                        f"_run_check_ed positional-arg site -- W978 "
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
                        f"_run_check_ed kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_auth_gaps.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure. See cmd_taint W607-CJ "
        "for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (17) AST-scan -- BOTH accumulators are pinned in source (CM + ED)
# ---------------------------------------------------------------------------


def test_w607ed_coexists_with_w607cm_in_source():
    """W607-ED is ADDITIVE -- the pre-existing W607-CM substrate-CALL
    family MUST still be present in source alongside W607-ED.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    src = src_path.read_text(encoding="utf-8")

    # W607-CM substrate-CALL family
    assert "w607cm_warnings_out" in src, "W607-CM substrate-CALL accumulator has been removed."
    assert "_run_check_cm" in src, "W607-CM helper has been removed."
    # W607-ED aggregation-phase family (THIS wave)
    assert "w607ed_warnings_out" in src, "W607-ED aggregation-phase accumulator has been removed."
    assert "_run_check_ed" in src, "W607-ED helper has been removed."


# ---------------------------------------------------------------------------
# (18) ANY W607-ED marker flips partial_success on the populated path
# ---------------------------------------------------------------------------


def test_any_ed_marker_flips_partial_success(cli_runner, auth_gaps_project, monkeypatch):
    """ANY W607-ED marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    auth-gaps" from "auth-gaps ran with aggregation degradation"
    via summary.partial_success alone.
    """
    from roam.commands import cmd_auth_gaps as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-ED")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-ED warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (19) warnings_out mirrors -- top-level AND summary BOTH populated
# ---------------------------------------------------------------------------


def test_w607ed_warnings_out_in_both_top_and_summary(cli_runner, auth_gaps_project, monkeypatch):
    """Non-empty W607-ED bucket -> both top-level AND summary.warnings_out
    populated.
    """
    from roam.commands import cmd_auth_gaps as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-ED")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-ED raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-ED raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("auth_gaps_serialize_envelope_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("auth_gaps_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (20) Helper-template ``return default`` verbatim shape -- W607-DW pin
# ---------------------------------------------------------------------------


def test_run_check_ed_helper_returns_default_verbatim():
    """W607-DW regression guard: the ``_run_check_ed`` helper body must
    end with ``return default`` (verbatim) -- NOT
    ``return default if default is not None else {}``.

    The W607-DP/DW finding identified that an "improved" default-coerce
    return shape silently masks the floor literal -- e.g., a ``None``
    floor for a phase that legitimately returns ``None`` on success
    would get coerced to ``{}`` on raise, breaking caller assumptions.
    The verbatim ``return default`` keeps the floor honest.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_helper = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_ed"):
            continue
        found_helper = True
        try_stmt = None
        for stmt in node.body:
            if isinstance(stmt, ast.Try):
                try_stmt = stmt
                break
        assert try_stmt is not None, (
            f"_run_check_ed body must contain a try/except block; got {[type(s).__name__ for s in node.body]!r}"
        )
        assert try_stmt.handlers, "_run_check_ed try-block must have at least one except-handler"
        last_handler = try_stmt.handlers[-1]
        last_stmt = last_handler.body[-1]
        assert isinstance(last_stmt, ast.Return), (
            f"_run_check_ed except-handler must end with a Return statement; got {type(last_stmt).__name__!r}"
        )
        assert isinstance(last_stmt.value, ast.Name), (
            f"_run_check_ed return value must be a bare ``default`` Name "
            f"node (W607-DW verbatim shape); got "
            f"{type(last_stmt.value).__name__!r} -- a conditional/IfExp "
            f"return masks the floor literal and reintroduces the W607-DW "
            f"silent-coerce bug."
        )
        assert last_stmt.value.id == "default", (
            f"_run_check_ed return value must reference the ``default`` parameter; got Name(id={last_stmt.value.id!r})"
        )
        break

    assert found_helper, "_run_check_ed helper not found in cmd_auth_gaps AST"


# ---------------------------------------------------------------------------
# (21) Security-detector 3-way closure pin -- AST-scan cmd_taint +
#       cmd_vulns + cmd_auth_gaps for both substrate + aggregation plumbing
# ---------------------------------------------------------------------------


def test_security_detector_3way_pairing_substrate_plus_aggregation():
    """Structural invariant: the 3 security-family detectors (cmd_taint /
    cmd_vulns / cmd_auth_gaps) all carry BOTH a substrate-CALL
    accumulator AND an aggregation-phase accumulator.

    A regression here means one detector silently lost a layer -- a
    Pattern-2 hazard the surfacing tests can't catch on their own
    because each detector's tests only inspect its own source. This
    wave (W607-ED) closes the 3-way security-detector triad with the
    aggregation-phase layer; cmd_taint + cmd_vulns have been carrying
    both layers since W607-AY+CJ and W607-AQ+CH respectively.
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    # (cmd path, substrate-CALL accumulator, aggregation-phase
    # accumulator, substrate-helper, aggregation-helper)
    triple = [
        ("cmd_taint.py", "w607ay_warnings_out", "w607cj_warnings_out", "_run_check_ay", "_run_check_cj"),
        ("cmd_vulns.py", "w607aq_warnings_out", "w607ch_warnings_out", "_run_check_aq", "_run_check_ch"),
        ("cmd_auth_gaps.py", "w607cm_warnings_out", "w607ed_warnings_out", "_run_check_cm", "_run_check_ed"),
    ]

    for path, sub_acc, agg_acc, sub_helper, agg_helper in triple:
        src_path = src_root / path
        assert src_path.exists(), f"{path} missing at {src_path}"
        src = src_path.read_text(encoding="utf-8")
        assert sub_acc in src, (
            f"security-detector 3-way pairing broken: {path} lost substrate-CALL accumulator {sub_acc!r}"
        )
        assert agg_acc in src, (
            f"security-detector 3-way pairing broken: {path} lost aggregation-phase accumulator {agg_acc!r}"
        )
        assert sub_helper in src, (
            f"security-detector 3-way pairing broken: {path} lost substrate-CALL helper {sub_helper!r}"
        )
        assert agg_helper in src, (
            f"security-detector 3-way pairing broken: {path} lost aggregation-phase helper {agg_helper!r}"
        )


# ---------------------------------------------------------------------------
# (22) score_classify isolation -- clean path surfaces NO_AUTH_GAPS, not DEGRADED
# ---------------------------------------------------------------------------


def test_score_classify_isolation_clean_path_not_degraded(cli_runner, auth_gaps_project):
    """Per-phase isolation guard: a clean empty-corpus run surfaces
    ``run_state=NO_AUTH_GAPS`` (the literal 0-total bucket label),
    NOT the DEGRADED floor. DEGRADED is reserved for the raise path.
    """
    result = _invoke_auth_gaps(cli_runner, auth_gaps_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    # Clean run -> NO_AUTH_GAPS (0 findings on the Python fixture).
    assert summary.get("run_state") == "NO_AUTH_GAPS", (
        f"clean auth-gaps run must surface run_state=NO_AUTH_GAPS; got summary = {summary!r}"
    )
    # DEGRADED is the floor -- it must NOT surface on the clean path.
    assert summary.get("run_state") != "DEGRADED", (
        f"DEGRADED run_state must be reserved for the raise floor; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (23) compute_predicate isolation -- floor dict shape
# ---------------------------------------------------------------------------


def test_compute_predicate_floor_dict_shape():
    """W978 6th-discipline: compute_predicate floor MUST be a concrete
    dict carrying all 4 documented keys (total_count / by_kind /
    files_affected / endpoints_affected), NOT a sentinel that may
    __len__-raise downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_predicate_floor = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_ed"):
            continue
        # First positional arg must be the phase name literal.
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "compute_predicate"):
            continue
        # Find the default= kwarg
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
                "total_count",
                "by_kind",
                "files_affected",
                "endpoints_affected",
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
        "compute_predicate _run_check_ed call site not found in source; "
        "the aggregation boundary has been refactored away."
    )


# ---------------------------------------------------------------------------
# (24) score_classify floor dict shape -- W978 6th-discipline
# ---------------------------------------------------------------------------


def test_score_classify_floor_dict_shape():
    """W978 6th-discipline: score_classify floor MUST be a concrete
    dict carrying ``state: "DEGRADED"`` + ``scanned: 0``, NOT a
    sentinel that may __len__-raise downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_auth_gaps.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_score_floor = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_ed"):
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
            # Confirm DEGRADED is the literal state value
            assert 'default={"state": "DEGRADED"' in src or '"state": "DEGRADED"' in src, (
                "score_classify floor state value must be DEGRADED literal"
            )
            found_score_floor = True
            break

    assert found_score_floor, (
        "score_classify _run_check_ed call site not found in source; the aggregation boundary has been refactored away."
    )
