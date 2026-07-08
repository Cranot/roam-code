"""W607-CW -- ``cmd_conventions`` substrate-boundary plumbing.

cmd_conventions is the project-convention detector (W133 origin per
CLAUDE.md detector roster -- part of the original 16
findings-registry substrate detectors). The detector applies the
case-classification primitives at ``cmd_conventions`` against the
canonical aggregator (``conventions_helper.compute_conventions`` -- the
Fix G delegation point) so this command's verdicts agree with
``roam describe``, ``roam understand``, ``roam minimap``, and
``roam preflight`` (Pattern 4 of the dogfood corpus: five surfaces
historically computed conventions differently; Fix G consolidated them
onto the helper).

W162 layered test/fixture path exclusion + TypeAlias detection so the
detector stops emitting ``.github/workflows/setup-node``-style false
positives (51%-of-identifiers volume regression). W988 layered the
Pattern-2 empty-state playbook so an empty corpus emits the explicit
``"no symbols analyzed (corpus empty …)"`` verdict + ``partial_success``
flip rather than a silent SAFE/PASSED vocabulary fallback. Until this
wave the command had no substrate-boundary marker plumbing -- a raise
in the Fix G ``compute_conventions`` call, any per-axis analysis helper
(files / imports / errors / exports), the W133 registry mirror, or the
downstream verdict / R22 wrap composer would crash the conventions
detector outright.

This wave installs the canonical ``_w607cw_warnings_out`` bucket +
``_run_check_cw`` helper inside the ``conventions`` click command and
wraps every substrate boundary:

* analyse_naming             -- Fix G ``compute_conventions``
                                delegation (the canonical
                                per-(family, group) aggregator)
* query_files                -- ``SELECT path FROM files`` row fetch
* analyse_files              -- file-organization summarizer
                                (test patterns / barrel files / top dirs)
* analyse_imports            -- import-edge style detector
                                (absolute vs relative)
* analyse_error_handling     -- error-symbol roll-up
* analyse_exports            -- is_exported distribution + JS/TS
                                default-vs-named style
* emit_findings              -- W133 registry mirror (DIRECT try/except;
                                sqlite3.OperationalError silent no-op
                                preserved for pre-W89 DB)
* compose_verdict            -- LAW 6 single-line verdict composition
* build_naming_violations    -- per-outlier dict assembly with
                                group_dominant_pct annotation
* wrap_findings_classify     -- R22 wrap_findings +
                                confidence_distribution +
                                verdict_with_high_count composition

Marker family ``conventions_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers preserved by the
prefix-discipline test. cmd_conventions CLOSES the DETECTOR FAMILY
12-WAY with cmd_bus_factor + cmd_hotspots + cmd_auth_gaps + cmd_n1 +
cmd_over_fetch + cmd_missing_index + cmd_smells + cmd_vibe_check +
cmd_clones + cmd_duplicates + cmd_dead + cmd_orphan_imports.

W988 PATTERN-2 EMPTY-CORPUS REGRESSION GUARD
--------------------------------------------

W988 surfaced the empty-corpus gap on cmd_conventions and applied the
Pattern-2 empty-state playbook: when ``naming_summary`` is empty, the
verdict is the explicit
``"no symbols analyzed (corpus empty — run `roam index --force` to populate)"``
state + ``partial_success: True`` + ``state: "no_symbols_analyzed"``.
The regression-guard test below confirms the W607-CW plumbing does not
re-introduce Pattern-2 silent-fallback on the empty-corpus branch.

W162 + FIX G REGRESSION GUARDS
------------------------------

W162 (test/fixture exclusion + TypeAlias detection) and Fix G
(``compute_conventions`` canonical delegation) are explicitly probed
with two targeted regression tests:

1. The Fix G delegation: ``_analyze_naming`` calls
   ``conventions_helper.compute_conventions`` (NOT a local
   re-implementation). A raise in the helper surfaces via the W607-CW
   ``analyse_naming`` marker (not a silently-swallowed empty floor).
2. The W162 ``exclude_paths`` plumbing: the global ``--include-excluded``
   flag toggles between ``exclude_paths=()`` (legacy scan-everything)
   and ``exclude_paths=None`` (default exclusion list). The W607-CW
   wrap forwards the kwarg untouched.

PER-SUBSTRATE ISOLATION
-----------------------

The conventions detector aggregates per axis (naming / files /
imports / errors / exports), so the "per-symbol isolation" check is
realised here as **per-substrate isolation**: simulate
``_analyze_imports`` raising while the main ``compute_conventions``
pass succeeds -- the imports marker surfaces, ``import_info`` falls
to the ``style="unknown"`` floor, and the envelope's naming + files +
errors + exports content survives.

DETECTOR FAMILY 12-WAY PAIRING
------------------------------

The bonus pairing test confirms ``conventions_<phase>_failed:``
markers coexist with the eleven sibling detector families
(bus_factor / hotspots / auth_gaps / n1 / over_fetch / missing_index /
smells / vibe_check / clones / duplicates / dead / orphan_imports)
without cross-prefix leakage.
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


def _build_conventions_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_conventions.

    Builds a tiny Python fixture with one source symbol so the
    detector's naming / imports / exports / error-handling rollups
    have SOMETHING to crunch (without symbols the empty-corpus
    branch fires before any per-axis substrate runs, which
    short-circuits past the substrates the tests want to exercise).
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
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS file_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file_id INTEGER NOT NULL,
            target_file_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'imports',
            symbol_count INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS file_stats (
            file_id INTEGER PRIMARY KEY,
            commit_count INTEGER DEFAULT 0,
            total_churn INTEGER DEFAULT 0,
            distinct_authors INTEGER DEFAULT 0,
            complexity REAL DEFAULT 0
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
def conventions_project(tmp_path):
    return _build_conventions_project(tmp_path)


def _invoke_conventions(cli_runner, project_root, *args, json_mode=True, include_excluded=False):
    """Invoke the conventions click command directly (bypassing the CLI group)."""
    from roam.commands.cmd_conventions import conventions

    obj = {
        "json": json_mode,
        "sarif": False,
        "budget": 0,
        "include_excluded": include_excluded,
    }
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(conventions, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_CW_PHASES = (
    "analyse_naming",
    "query_files",
    "analyse_files",
    "analyse_imports",
    "analyse_error_handling",
    "analyse_exports",
    "emit_findings",
    "compose_verdict",
    "build_naming_violations",
    "wrap_findings_classify",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CW substrate markers
# ---------------------------------------------------------------------------


def test_conventions_clean_envelope_omits_w607cw_markers(cli_runner, conventions_project):
    """Clean conventions run -> no W607-CW substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-CW bucket
    on the success path must NOT introduce new
    ``conventions_<phase>_failed:`` markers tied to the W607-CW wrap.
    """
    result = _invoke_conventions(cli_runner, conventions_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "conventions"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    cw_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"conventions_{p}_failed:" in m for p in _CW_PHASES)
    ]
    assert not cw_markers, (
        f"clean conventions must NOT surface W607-CW substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) analyse_naming failure -> canonical 3-segment marker + partial_success
# ---------------------------------------------------------------------------


def test_conventions_analyse_naming_failure_marker_format(cli_runner, conventions_project, monkeypatch):
    """If ``_analyze_naming`` raises, surface the canonical 3-segment marker.

    Fix G regression guard: the substrate boundary here is the
    ``conventions_helper.compute_conventions`` delegation; the
    W607-CW wrap surfaces a clean marker rather than letting the
    raise propagate.
    """
    from roam.commands import cmd_conventions

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-analyse-naming-from-W607-CW")

    monkeypatch.setattr(cmd_conventions, "_analyze_naming", _raise)

    result = _invoke_conventions(cli_runner, conventions_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    naming_markers = [m for m in all_wo if m.startswith("conventions_analyse_naming_failed:")]
    assert naming_markers, f"expected conventions_analyse_naming_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in naming_markers), naming_markers
    assert any("synthetic-analyse-naming-from-W607-CW" in m for m in naming_markers), naming_markers

    # 3-segment marker shape check (prefix:exc_class:detail).
    marker = naming_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "conventions_analyse_naming_failed", parts
    assert parts[1] == "RuntimeError", parts
    assert parts[2], parts

    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"analyse_naming-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # LAW 6: verdict still emits as a single line.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out dual-mirror (top-level + summary.warnings_out)
# ---------------------------------------------------------------------------


def test_conventions_w607cw_warnings_dual_mirror(cli_runner, conventions_project, monkeypatch):
    """Non-empty W607-CW bucket -> both top-level AND summary.warnings_out.

    MCP consumers may read either surface, so the marker must land in
    BOTH locations on a disclosure path (dual-mirror invariant).
    """
    from roam.commands import cmd_conventions

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CW")

    monkeypatch.setattr(cmd_conventions, "_analyze_naming", _raise)

    result = _invoke_conventions(cli_runner, conventions_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CW disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CW disclosure path; got summary = {data['summary']!r}"
    )
    # Same marker appears in both surfaces.
    top_markers = [m for m in data["warnings_out"] if m.startswith("conventions_analyse_naming_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("conventions_analyse_naming_failed:")]
    assert top_markers, data["warnings_out"]
    assert summary_markers, data["summary"]["warnings_out"]


# ---------------------------------------------------------------------------
# (4) Per-substrate isolation: analyse_imports failure
# ---------------------------------------------------------------------------


def test_conventions_analyse_imports_failure_degrades_cleanly(cli_runner, conventions_project, monkeypatch):
    """A raise in ``_analyze_imports`` degrades to the ``style="unknown"`` floor.

    Per-substrate isolation: a raise in the imports axis must NOT
    torpedo the naming / files / errors / exports content. The marker
    surfaces, ``partial_success: True`` flips, and the envelope's other
    axes survive.
    """
    from roam.commands import cmd_conventions

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-imports-from-W607-CW")

    monkeypatch.setattr(cmd_conventions, "_analyze_imports", _raise)

    result = _invoke_conventions(cli_runner, conventions_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    imports_markers = [m for m in all_wo if m.startswith("conventions_analyse_imports_failed:")]
    assert imports_markers, all_wo

    # The imports axis falls to the empty floor.
    assert data["summary"].get("import_style") == "unknown"
    # Other axes survive -- naming summary / files key are present.
    assert "naming" in data, sorted(data.keys())
    assert "files" in data, sorted(data.keys())
    # LAW 6 verdict still emits.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (5) analyse_files failure -> marker + envelope composes
# ---------------------------------------------------------------------------


def test_conventions_analyse_files_failure_degrades_cleanly(cli_runner, conventions_project, monkeypatch):
    """A raise in ``_analyze_files`` degrades to the empty file-info floor."""
    from roam.commands import cmd_conventions

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-files-from-W607-CW")

    monkeypatch.setattr(cmd_conventions, "_analyze_files", _raise)

    result = _invoke_conventions(cli_runner, conventions_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    files_markers = [m for m in all_wo if m.startswith("conventions_analyse_files_failed:")]
    assert files_markers, all_wo
    assert data["summary"].get("total_files") == 0
    assert data["summary"].get("test_files") == 0
    assert data["summary"].get("barrel_files") == 0
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) emit_findings failure -> non-OperationalError surfaces marker
# ---------------------------------------------------------------------------


def test_conventions_emit_findings_generic_failure_surfaces_marker(cli_runner, conventions_project, monkeypatch):
    """W133 emit failure (non-OperationalError) surfaces W607-CW marker.

    The W133 registry mirror uses a DIRECT try/except (not the
    ``_run_check_cw`` helper) because the pre-W89 schema path
    (``sqlite3.OperationalError`` on missing ``findings`` table) is
    the EXPECTED degraded path -- the W133 silent no-op contract for
    that case must NOT produce a W607-CW marker. Generic exceptions
    DO surface via
    ``conventions_emit_findings_failed:<exc_class>:<detail>``.
    """
    from roam.commands import cmd_conventions

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-CW")

    monkeypatch.setattr(cmd_conventions, "_emit_conventions_findings", _raise)

    result = _invoke_conventions(cli_runner, conventions_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("conventions_emit_findings_failed:")]
    assert emit_markers, f"expected conventions_emit_findings_failed: marker on generic exception; got {all_wo!r}"
    assert any("RuntimeError" in m for m in emit_markers), emit_markers
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (7) emit_findings OperationalError path stays silent (no W607-CW marker)
# ---------------------------------------------------------------------------


def test_conventions_emit_findings_operational_error_stays_silent(cli_runner, conventions_project, monkeypatch):
    """W607-CW MUST preserve the W133 silent no-op contract on
    ``sqlite3.OperationalError`` (pre-W89 schema -- no findings table).

    The marker MUST NOT surface for this expected degraded path.
    """
    from roam.commands import cmd_conventions

    def _raise_op_err(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings (pre-W89 schema)")

    monkeypatch.setattr(cmd_conventions, "_emit_conventions_findings", _raise_op_err)

    result = _invoke_conventions(cli_runner, conventions_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("conventions_emit_findings_failed:")]
    assert not emit_markers, (
        f"sqlite3.OperationalError is the EXPECTED pre-W89 silent "
        f"no-op path; W607-CW marker MUST NOT surface; "
        f"got {emit_markers!r}"
    )


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-CW stays in ``conventions_*`` family
# ---------------------------------------------------------------------------


def test_w607cw_marker_prefix_stays_in_conventions_family(cli_runner, conventions_project, monkeypatch):
    """Every W607-CW substrate marker uses the canonical ``conventions_*`` prefix.

    Hard distinction from sibling W607-* layers across the broader
    command surface.
    """
    from roam.commands import cmd_conventions

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CW")

    monkeypatch.setattr(cmd_conventions, "_analyze_naming", _raise)

    result = _invoke_conventions(cli_runner, conventions_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("conventions_"), (
            f"every surfaced W607-CW marker must use the ``conventions_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("bus_factor_", "cmd_bus_factor W607-CQ"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("over_fetch_", "cmd_over_fetch W607-CE"),
            ("missing_index_", "cmd_missing_index W607-CI"),
            ("smells_", "cmd_smells W607-BN"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("clones_", "cmd_clones W607-BQ"),
            ("duplicates_", "cmd_duplicates W607-BM"),
            ("dead_", "cmd_dead W607-BX"),
            ("hotspots_", "cmd_hotspots W607-* (runtime)"),
            ("orphan_imports_", "cmd_orphan_imports W607-CR"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("health_", "cmd_health W607-M / W607-BA"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) AST source-level guard: cmd_conventions carries the W607-CW accumulator
# ---------------------------------------------------------------------------


def test_cmd_conventions_carries_w607cw_accumulator():
    """AST-level guard: cmd_conventions source carries the W607-CW accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_conventions.py"
    assert src_path.exists(), f"cmd_conventions.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607cw_warnings_out" in src, (
        "W607-CW accumulator missing from cmd_conventions; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_cw" in src, (
        "W607-CW ``_run_check_cw`` helper missing from cmd_conventions; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_cw = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cw":
            found_run_check_cw = True
            break
    assert found_run_check_cw, (
        "W607-CW ``_run_check_cw`` helper not found in cmd_conventions AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (10) Every W607-CW substrate phase is wrapped (source-level grep)
# ---------------------------------------------------------------------------


def test_all_w607cw_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-CW substrate boundary is wrapped.

    Each phase name must appear either as a ``_run_check_cw("phase"...)``
    call (same-line or multi-line) OR carry the explicit marker fstring
    ``conventions_<phase>_failed`` (the DIRECT try/except pattern used
    for the ``emit_findings`` substrate, where the OperationalError
    silent no-op must coexist with generic-exception disclosure).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_conventions.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _CW_PHASES:
        same_line = f'_run_check_cw("{phase}"' in src
        multi_line = (
            f'_run_check_cw(\n        "{phase}"' in src
            or f'_run_check_cw(\n            "{phase}"' in src
            or f'_run_check_cw(\n                "{phase}"' in src
            or f'_run_check_cw(\n                    "{phase}"' in src
            or f'_run_check_cw(\n                        "{phase}"' in src
        )
        marker_grep = f"conventions_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-CW wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (11) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607cw_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-CW marker shape lives in cmd_conventions."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_conventions.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"conventions_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-CW marker fstring missing from cmd_conventions; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (12) Fix G regression: _analyze_naming delegates to conventions_helper
# ---------------------------------------------------------------------------


def test_fix_g_compute_conventions_delegation_preserved_under_w607cw():
    """Fix G regression guard: ``_analyze_naming`` delegates to
    ``conventions_helper.compute_conventions`` (NOT a local re-implementation).

    Pattern 4 of the dogfood synthesis (CLAUDE.md): five surfaces
    (describe, understand, minimap, preflight, conventions standalone)
    historically each computed conventions differently. Fix G
    consolidated them onto ``conventions_helper.compute_conventions``.
    The W607-CW wrap MUST preserve that delegation -- a refactor that
    re-introduces a local re-implementation would silently re-fork the
    five-way Pattern-4 divergence.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_conventions.py"
    src = src_path.read_text(encoding="utf-8")
    # The Fix G delegation marker.
    assert "from roam.commands.conventions_helper import compute_conventions" in src, (
        "Fix G delegation missing: cmd_conventions no longer imports "
        "compute_conventions from conventions_helper. Pattern 4 fork "
        "regression -- describe/understand/minimap/preflight/conventions "
        "must all agree on the same canonical computation."
    )
    assert "compute_conventions(conn" in src, (
        "Fix G call site missing: cmd_conventions no longer calls "
        "compute_conventions(conn, …). Pattern 4 fork regression."
    )


# ---------------------------------------------------------------------------
# (13) W162 regression: exclude_paths plumbing preserved under W607-CW
# ---------------------------------------------------------------------------


def test_w162_exclude_paths_plumbing_preserved_under_w607cw(cli_runner, conventions_project, monkeypatch):
    """W162 regression guard: the ``exclude_paths`` kwarg is forwarded
    through the W607-CW wrap to ``_analyze_naming``.

    W162 layered test/fixture path exclusion + TypeAlias detection so
    the detector stops emitting ``.github/workflows/setup-node``-style
    false positives (51%-of-identifiers volume regression). The W607-CW
    wrap MUST forward the ``exclude_paths`` kwarg untouched; a refactor
    that drops the kwarg would re-introduce the W162 volume regression.
    """
    from roam.commands import cmd_conventions

    captured: list = []

    def _capture(conn, exclude_paths=None):
        captured.append(exclude_paths)
        # Return an empty-floor tuple so the rest of the envelope composes.
        return [], {}, [], {"prefixes": [], "suffixes": []}

    monkeypatch.setattr(cmd_conventions, "_analyze_naming", _capture)

    # Default invocation: include_excluded=False -> exclude_paths=None
    # (helper applies its default exclusion list).
    result = _invoke_conventions(cli_runner, conventions_project)
    assert result.exit_code == 0, result.output
    assert captured, "expected _analyze_naming to be called via _run_check_cw"
    assert captured[-1] is None, (
        f"W162 default invocation must pass exclude_paths=None so "
        f"conventions_helper applies its default exclusion list; "
        f"got {captured[-1]!r}"
    )

    # include_excluded=True -> exclude_paths=() (legacy scan-everything).
    captured.clear()
    result = _invoke_conventions(cli_runner, conventions_project, include_excluded=True)
    assert result.exit_code == 0, result.output
    assert captured, "expected _analyze_naming to be called via _run_check_cw"
    assert captured[-1] == (), (
        f"--include-excluded must pass exclude_paths=() (legacy scan-everything); got {captured[-1]!r}"
    )


# ---------------------------------------------------------------------------
# (14) W988 PATTERN-2 EMPTY-CORPUS REGRESSION GUARD
# ---------------------------------------------------------------------------


def test_w988_empty_corpus_verdict_preserved_under_w607cw(cli_runner, tmp_path):
    """W988 Pattern-2 empty-corpus regression guard.

    When ``naming_summary`` is empty, the verdict is the explicit
    no-symbols state, ``partial_success: True`` flips, and
    ``state: "no_symbols_analyzed"`` is stamped. The W607-CW plumbing
    must NOT re-introduce Pattern-2 silent-fallback (SAFE/PASSED/
    completed vocabulary on a degraded state).
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
            line_start INTEGER, line_end INTEGER,
            visibility TEXT DEFAULT 'public', is_exported INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS file_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file_id INTEGER NOT NULL,
            target_file_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'imports',
            symbol_count INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS file_stats (
            file_id INTEGER PRIMARY KEY,
            commit_count INTEGER DEFAULT 0,
            total_churn INTEGER DEFAULT 0,
            distinct_authors INTEGER DEFAULT 0,
            complexity REAL DEFAULT 0
        );
        """
    )
    conn.commit()
    conn.close()

    result = _invoke_conventions(cli_runner, tmp_path)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = (summary.get("verdict") or "").lower()

    # W988 explicit-state verdict.
    assert "no symbols" in verdict or "corpus empty" in verdict or "no data" in verdict, (
        f"empty-corpus verdict must name the no-data state explicitly (W988 Pattern-2 contract); got {verdict!r}"
    )
    # Pattern-2 forbidden vocabulary stays out.
    for forbidden in ("safe", "passed", "completed", "all clear"):
        assert forbidden not in verdict, (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )
    # The W988 state stamp + partial_success flip.
    assert summary.get("partial_success") is True
    assert summary.get("state") == "no_symbols_analyzed", summary


# ---------------------------------------------------------------------------
# (15) compose_verdict failure -> "no data" floor + envelope composes
# ---------------------------------------------------------------------------


def test_conventions_compose_verdict_failure_degrades(cli_runner, conventions_project, monkeypatch):
    """A raise inside the verdict composer degrades to the ``"no data"`` floor.

    The composer accesses ``biggest_group["dominant_style"]`` -- a
    malformed ``naming_summary`` row could KeyError. The W607-CW wrap
    surfaces the marker and keeps the envelope a valid LAW-6
    single-line verdict.
    """
    from roam.commands import cmd_conventions

    # Forge a malformed naming_summary missing 'dominant_style' so
    # _compose_verdict KeyErrors on biggest_group['dominant_style'].
    def _bad_naming(conn, exclude_paths=None):
        bad_summary = {
            "python/functions": {
                # NO "dominant_style" key -- compose_verdict KeyErrors.
                "total": 5,
                "percent": 80,
                "breakdown": {},
            }
        }
        return [], bad_summary, [], {"prefixes": [], "suffixes": []}

    monkeypatch.setattr(cmd_conventions, "_analyze_naming", _bad_naming)

    result = _invoke_conventions(cli_runner, conventions_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    verdict_markers = [m for m in all_wo if m.startswith("conventions_compose_verdict_failed:")]
    assert verdict_markers, all_wo
    # Verdict still emits (LAW 6 single-line).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (16) DETECTOR FAMILY 12-WAY pairing bonus
# ---------------------------------------------------------------------------


def test_detector_family_12way_marker_prefixes_coexist(cli_runner, conventions_project, monkeypatch):
    """DETECTOR FAMILY 12-WAY pairing bonus.

    Confirm ``conventions_<phase>_failed:`` markers coexist with the
    eleven sibling detector families without cross-prefix leakage:
    bus_factor_* (W607-CQ), hotspots_* (W607-* runtime),
    auth_gaps_* (W607-CM), n1_* (W607-CB), over_fetch_* (W607-CE),
    missing_index_* (W607-CI), smells_* (W607-BN), vibe_check_*
    (W607-BS), clones_* (W607-BQ), duplicates_* (W607-BM), dead_*
    (W607-BX), orphan_imports_* (W607-CR). CLOSES the 12-detector
    family.
    """
    from roam.commands import cmd_conventions

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-12way-from-W607-CW")

    monkeypatch.setattr(cmd_conventions, "_analyze_naming", _raise)

    result = _invoke_conventions(cli_runner, conventions_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("conventions_analyse_naming_failed:") for m in all_wo), all_wo

    # None of the eleven sibling detector prefixes leak into the
    # conventions envelope. This is the 12-way pairing closer:
    # bus_factor / hotspots / auth_gaps / n1 / over_fetch /
    # missing_index / smells / vibe_check / clones / duplicates /
    # dead / orphan_imports.
    for forbidden_prefix in (
        "bus_factor_",
        "hotspots_",
        "auth_gaps_",
        "n1_",
        "over_fetch_",
        "missing_index_",
        "smells_",
        "vibe_check_",
        "clones_",
        "duplicates_",
        "dead_",
        "orphan_imports_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on detector-family 12-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_conventions envelope; "
            f"got {leaked!r}"
        )
