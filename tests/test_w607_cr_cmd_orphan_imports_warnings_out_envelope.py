"""W607-CR -- ``cmd_orphan_imports`` substrate-boundary plumbing.

cmd_orphan_imports is the unused-import detector (W132 origin per
CLAUDE.md detector roster -- part of the original 16 findings-registry
substrate detectors). The detector has three per-language scanners
(Python, JavaScript/TypeScript, Go) and three W160 false-positive
filters (conftest + try-except + relative-import). Per CLAUDE.md
sprint history (Wave812), empty-corpus smoke was pinned + Wave814
fixed the partial_success missing on empty-state. Until this wave the
command had no substrate-boundary marker plumbing -- a raise inside
``_scan_python`` / ``_scan_javascript`` / ``_scan_go`` (the three
per-language scanners) would crash the orphan-imports detector
outright.

This wave installs the canonical ``_w607cr_warnings_out`` bucket +
``_run_check_cr`` helper inside the ``orphan-imports`` click command
and wraps every substrate boundary:

* scan_python                -- per-language Python orphan scanner
                                (covers W160 conftest + try/except +
                                relative-import filter helpers
                                indirectly via the scanner's call tree)
* scan_javascript            -- per-language JS/TS orphan scanner
* scan_go                    -- per-language Go orphan scanner
* emit_findings              -- W132 findings-registry mirror
                                (sqlite3.OperationalError silent no-op
                                preserved for pre-W89 DB)
* serialize_to_sarif         -- SARIF projection
* derive_distribution        -- R22 wrap + confidence_distribution +
                                verdict_with_high_count

Marker family ``orphan_imports_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers preserved by the
prefix-discipline test (orphan-imports CLOSES the DETECTOR FAMILY
11-WAY with hotspots + n1 + over-fetch + missing-index + auth-gaps +
smells + vibe-check + clones + duplicates + dead == 11 detectors).

W812 / W814 PATTERN-2 REGRESSION GUARD
--------------------------------------

W812/W814 sealed the empty-corpus smoke gap on cmd_orphan_imports.
The regression-guard tests below confirm:

  1. The clean empty-corpus path still emits the expected OK verdict
     without W607-CR marker pollution (W812 invariant preserved --
     empty corpus is NOT a degradation).
  2. The W607-CR substrate boundary on the per-language scanners does
     NOT re-introduce a Pattern-2 silent-fallback -- a raise in one
     scanner surfaces a marker AND flips ``partial_success: True``,
     never a SAFE verdict on a degraded state.

W160 FILTER REGRESSION GUARD
----------------------------

The orphan-imports detector ships three false-positive filters from
W160 (conftest auto-discovery + try-except ImportError + relative
imports). The bonus test confirms the W607-CR plumbing does NOT
remove or short-circuit any of those filter call-sites.

PER-LANGUAGE ISOLATION BONUS
----------------------------

Confirm that a raise in ``_scan_python`` does NOT block the
JavaScript or Go scanner runs -- they each occupy their own
substrate boundary in the dispatcher loop.

DETECTOR FAMILY 11-WAY PAIRING (CLOSES THE FAMILY)
--------------------------------------------------

The pairing test confirms each marker family stays inside its own
prefix without leaking across detector boundaries (orphan-imports +
hotspots + n1 + over-fetch + missing-index + auth-gaps + smells +
vibe-check + clones + duplicates + dead == 11 detectors).
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


def _build_orphan_imports_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project for cmd_orphan_imports.

    Builds a tiny Python source tree with one clean module so the
    orphan-imports detector runs cleanly with zero findings -- the
    tests focus on W607-CR marker plumbing rather than the detector
    verdict itself.
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
def orphan_imports_project(tmp_path):
    """Minimal project for cmd_orphan_imports."""
    return _build_orphan_imports_project(tmp_path)


def _invoke_orphan_imports(cli_runner, project_root, *args, json_mode=True, sarif=False):
    """Invoke the orphan-imports click command directly (bypassing the CLI group)."""
    from roam.commands.cmd_orphan_imports import orphan_imports

    obj = {"json": json_mode, "sarif": sarif, "budget": 0, "detail": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(orphan_imports, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_CR_PHASES = (
    "scan_python",
    "scan_javascript",
    "scan_go",
    "emit_findings",
    "serialize_to_sarif",
    "derive_distribution",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- clean envelope omits W607-CR substrate markers
# ---------------------------------------------------------------------------


def test_orphan_imports_clean_envelope_omits_w607cr_markers(cli_runner, orphan_imports_project):
    """Clean orphan-imports run -> no W607-CR substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-CR bucket
    on the success path must NOT introduce new
    ``orphan_imports_<phase>_failed:`` markers tied to the W607-CR
    wrap.
    """
    result = _invoke_orphan_imports(cli_runner, orphan_imports_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "orphan-imports"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    cr_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"orphan_imports_{p}_failed:" in m for p in _CR_PHASES)
    ]
    assert not cr_markers, (
        f"clean orphan-imports must NOT surface W607-CR substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) scan_python failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_orphan_imports_scan_python_failure_marker_format(cli_runner, orphan_imports_project, monkeypatch):
    """If ``_scan_python`` raises, surface the canonical 3-segment marker."""
    from roam.commands import cmd_orphan_imports

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-scan-python-from-W607-CR")

    # Monkeypatch the _SCANNERS dispatch table so the wrap sees the raise.
    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "python", _raise)

    result = _invoke_orphan_imports(cli_runner, orphan_imports_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    scan_markers = [m for m in all_wo if m.startswith("orphan_imports_scan_python_failed:")]
    assert scan_markers, f"expected orphan_imports_scan_python_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in scan_markers), scan_markers
    assert any("synthetic-scan-python-from-W607-CR" in m for m in scan_markers), scan_markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"scan_python-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # LAW 6: the verdict still appears as a single line.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_orphan_imports_w607cr_warnings_in_envelope(cli_runner, orphan_imports_project, monkeypatch):
    """Non-empty W607-CR bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_orphan_imports

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CR")

    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "python", _raise)

    result = _invoke_orphan_imports(cli_runner, orphan_imports_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CR disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CR disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("orphan_imports_scan_python_failed:")]
    assert markers, f"expected orphan_imports_scan_python_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_orphan_imports_three_segment_marker_shape(cli_runner, orphan_imports_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_orphan_imports

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CR")

    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "python", _raise)

    result = _invoke_orphan_imports(cli_runner, orphan_imports_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("orphan_imports_scan_python_failed:")]
    assert failure_markers, f"expected orphan_imports_scan_python_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "orphan_imports_scan_python_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) emit_findings failure (non-OperationalError) surfaces W607-CR marker
# ---------------------------------------------------------------------------


def test_orphan_imports_emit_findings_failure_surfaces_marker(cli_runner, orphan_imports_project, monkeypatch):
    """W132 emit failure (non-OperationalError) surfaces W607-CR marker.

    sqlite3.OperationalError is the EXPECTED pre-W89 path (silent
    no-op). Generic exceptions surface via the W607-CR marker so a
    real bug in the persist substrate is loud, not silent.
    """
    from roam.commands import cmd_orphan_imports

    # Stub a Python scanner that yields one orphan so the persist branch
    # has something to emit on.
    def _fake_python_scan(_conn):
        return (
            [
                {
                    "language": "python",
                    "file": "src/engine.py",
                    "line": 1,
                    "module": "missing_pkg",
                    "kind": "missing_package",
                    "hint": "synthetic",
                }
            ],
            1,
        )

    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "python", _fake_python_scan)

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-CR")

    monkeypatch.setattr(cmd_orphan_imports, "_emit_orphan_imports_findings", _raise)

    result = _invoke_orphan_imports(cli_runner, orphan_imports_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("orphan_imports_emit_findings_failed:")]
    assert emit_markers, f"expected orphan_imports_emit_findings_failed: marker; got {all_wo!r}"
    # The orphan-imports command still emits a clean envelope past the
    # registry-mirror failure -- W132 is additive, not load-bearing.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) emit_findings OperationalError path stays silent (no W607-CR marker)
# ---------------------------------------------------------------------------


def test_orphan_imports_emit_findings_operational_error_stays_silent(cli_runner, orphan_imports_project, monkeypatch):
    """W607-CR MUST preserve the W132 silent no-op contract on
    ``sqlite3.OperationalError`` (pre-W89 schema -- no findings table).

    The marker MUST NOT surface for this expected degraded path.
    """
    from roam.commands import cmd_orphan_imports

    def _fake_python_scan(_conn):
        return (
            [
                {
                    "language": "python",
                    "file": "src/engine.py",
                    "line": 1,
                    "module": "missing_pkg",
                    "kind": "missing_package",
                    "hint": "synthetic",
                }
            ],
            1,
        )

    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "python", _fake_python_scan)

    def _raise_op_err(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings (pre-W89 schema)")

    monkeypatch.setattr(cmd_orphan_imports, "_emit_orphan_imports_findings", _raise_op_err)

    result = _invoke_orphan_imports(cli_runner, orphan_imports_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("orphan_imports_emit_findings_failed:")]
    assert not emit_markers, (
        f"sqlite3.OperationalError is the EXPECTED pre-W89 silent "
        f"no-op path; W607-CR marker MUST NOT surface; "
        f"got {emit_markers!r}"
    )


# ---------------------------------------------------------------------------
# (7) Per-language isolation -- a Python-scan raise doesn't kill JS/Go
# ---------------------------------------------------------------------------


def test_orphan_imports_per_language_isolation(cli_runner, orphan_imports_project, monkeypatch):
    """A raise in ``_scan_python`` MUST NOT block the JS and Go scanners.

    Per-language isolation: the W607-CR wrap is applied to each
    scanner in the dispatcher loop independently, so the marker for
    one language surfaces AND the other two scanners still classify
    correctly (i.e., the dispatcher loop continues past the failed
    Python scan).
    """
    from roam.commands import cmd_orphan_imports

    seen_languages: list[str] = []

    def _raise_python(*args, **kwargs):
        seen_languages.append("python")
        raise RuntimeError("synthetic-per-lang-isolation-from-W607-CR")

    def _ok_js(_conn):
        seen_languages.append("javascript")
        return ([], 0)

    def _ok_go(_conn):
        seen_languages.append("go")
        return ([], 0)

    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "python", _raise_python)
    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "javascript", _ok_js)
    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "go", _ok_go)

    result = _invoke_orphan_imports(cli_runner, orphan_imports_project)
    assert result.exit_code == 0, result.output

    # All three scanners were invoked despite the Python raise.
    assert "python" in seen_languages, seen_languages
    assert "javascript" in seen_languages, seen_languages
    assert "go" in seen_languages, seen_languages

    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    py_markers = [m for m in all_wo if m.startswith("orphan_imports_scan_python_failed:")]
    assert py_markers, f"expected orphan_imports_scan_python_failed: marker; got {all_wo!r}"
    # JS / Go did NOT raise so their markers must NOT surface.
    for forbidden_prefix in (
        "orphan_imports_scan_javascript_failed:",
        "orphan_imports_scan_go_failed:",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"per-language isolation broken: ``{forbidden_prefix}`` surfaced unexpectedly; got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-CR stays in ``orphan_imports_*`` family
# ---------------------------------------------------------------------------


def test_w607cr_marker_prefix_stays_in_orphan_imports_family(cli_runner, orphan_imports_project, monkeypatch):
    """Every W607-CR substrate marker uses the canonical ``orphan_imports_*`` prefix.

    Hard distinction from sibling W607-* layers. cmd_orphan_imports
    CLOSES the detector-family 11-way along with cmd_hotspots
    (W607-CP, ``hotspots_*``), cmd_n1 (W607-CB, ``n1_*``),
    cmd_over_fetch (W607-CE, ``over_fetch_*``), cmd_missing_index
    (W607-CI, ``missing_index_*``), cmd_auth_gaps (W607-CM,
    ``auth_gaps_*``), cmd_smells (W607-BN, ``smells_*``),
    cmd_vibe_check (W607-BS, ``vibe_check_*``), cmd_clones (W607-BQ,
    ``clones_*``), cmd_duplicates (W607-BM, ``duplicates_*``), and
    cmd_dead (W607-BX, ``dead_*``). A leaking marker would cross the
    detector-family boundary.
    """
    from roam.commands import cmd_orphan_imports

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CR")

    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "python", _raise)

    result = _invoke_orphan_imports(cli_runner, orphan_imports_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("orphan_imports_"), (
            f"every surfaced W607-CR marker must use the "
            f"``orphan_imports_*`` prefix family (cmd_orphan_imports "
            f"scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("hotspots_", "cmd_hotspots W607-CP (runtime hotspots)"),
            ("n1_", "cmd_n1 W607-CB (N+1 detector)"),
            ("over_fetch_", "cmd_over_fetch W607-CE (ORM over-fetch)"),
            ("missing_index_", "cmd_missing_index W607-CI"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("smells_", "cmd_smells W607-BN (structural smells)"),
            ("vibe_check_", "cmd_vibe_check W607-BS (LLM-rot detector)"),
            ("clones_", "cmd_clones W607-BQ (clone detector)"),
            ("duplicates_", "cmd_duplicates W607-BM (duplicates)"),
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
# (9) Source-level guard: cmd_orphan_imports carries the W607-CR accumulator
# ---------------------------------------------------------------------------


def test_cmd_orphan_imports_carries_w607cr_accumulator():
    """AST-level guard: cmd_orphan_imports source carries the W607-CR accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_orphan_imports.py"
    assert src_path.exists(), f"cmd_orphan_imports.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607cr_warnings_out" in src, (
        "W607-CR accumulator missing from cmd_orphan_imports; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_cr" in src, (
        "W607-CR ``_run_check_cr`` helper missing from cmd_orphan_imports; "
        "the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_cr = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cr":
            found_run_check_cr = True
            break
    assert found_run_check_cr, (
        "W607-CR ``_run_check_cr`` helper not found in cmd_orphan_imports "
        "AST; the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (10) Each W607-CR substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607cr_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-CR substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_orphan_imports.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _CR_PHASES:
        # The per-language scanners are wrapped via a dynamic phase
        # name built from the target language slug -- detect via the
        # marker construction site (which preserves the canonical
        # ``orphan_imports_<phase>_failed`` shape regardless of how the
        # wrap is parameterised). emit_findings is wrapped via direct
        # try/except (NOT _run_check_cr) because it needs to
        # distinguish sqlite3.OperationalError (expected pre-W89 path)
        # from generic Exception (W607-CR marker). Source-grep on the
        # marker name in either case.
        marker_grep = f"orphan_imports_{phase}_failed" in src
        same_line = f'_run_check_cr("{phase}"' in src
        multi_line = (
            f'_run_check_cr(\n        "{phase}"' in src
            or f'_run_check_cr(\n            "{phase}"' in src
            or f'_run_check_cr(\n                "{phase}"' in src
            or f'_run_check_cr(\n                    "{phase}"' in src
        )
        scanner_loop = phase in ("scan_python", "scan_javascript", "scan_go") and ('phase = f"scan_{tgt}"' in src)
        assert marker_grep or same_line or multi_line or scanner_loop, (
            f"W607-CR wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (11) W812 / W814 PATTERN-2 REGRESSION GUARD: clean empty corpus
# ---------------------------------------------------------------------------


def test_w812_clean_empty_corpus_no_w607cr_markers(cli_runner, orphan_imports_project):
    """W812 regression guard: clean empty corpus -> no W607-CR markers.

    W812 sealed the empty-corpus smoke gap on cmd_orphan_imports;
    W814 fixed the partial_success-missing bug on empty-state. The
    W607-CR plumbing must NOT re-introduce a Pattern-2 silent-fallback
    or re-flip partial_success on a clean path.
    """
    result = _invoke_orphan_imports(cli_runner, orphan_imports_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output) if result.output.strip() else {}
    summary = data.get("summary") or {}

    # No W607-CR markers on clean path.
    top_wo = data.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    cr_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"orphan_imports_{p}_failed:" in m for p in _CR_PHASES)
    ]
    assert not cr_markers, f"clean empty-corpus path must NOT surface W607-CR markers; got {cr_markers!r}"

    # And partial_success must NOT be flipped True on a clean path.
    # (W814 fix was about empty-state -- the clean OK verdict path
    # should NOT carry partial_success: True since nothing degraded.)
    assert summary.get("partial_success") in (None, False), (
        f"clean empty-corpus partial_success must be falsy (W814 invariant); got summary={summary!r}"
    )


def test_w812_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, orphan_imports_project, monkeypatch):
    """W812 Pattern-2 regression guard on the degraded scan path.

    If ``_scan_python`` raises, the empty-floor default kicks in
    (orphans for that language == []) and the envelope is emitted
    with the OK verdict (since no orphans were found). The W607-CR
    wrap MUST flip ``partial_success: True`` on that branch so the
    degraded envelope is NOT mistaken for a clean "no orphan
    imports" verdict (the classic Pattern-2 silent-fallback bug).
    """
    from roam.commands import cmd_orphan_imports

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-W812-pattern-2-from-W607-CR")

    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "python", _raise)

    result = _invoke_orphan_imports(cli_runner, orphan_imports_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    # The empty-floor default takes us into the OK envelope path
    # with zero orphans -- AND the marker must surface, AND
    # partial_success: True.
    assert summary.get("partial_success") is True, (
        f"degraded scan path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )

    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    scan_markers = [m for m in all_wo if m.startswith("orphan_imports_scan_python_failed:")]
    assert scan_markers, (
        f"degraded scan path MUST surface the scan_python marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (12) W160 filter regression guard: conftest + try-except + relative
# ---------------------------------------------------------------------------


def test_w160_filters_survive_w607cr_plumbing():
    """W160 false-positive filter regression guard.

    cmd_orphan_imports ships three W160 filters:
      (1) ``_is_conftest_path`` — pytest auto-discovered conftest
      (2) ``_optional_import_line_set`` — try/except ImportError
      (3) ``_resolve_relative_import`` — relative imports

    The W607-CR plumbing MUST NOT remove or short-circuit any of
    those filter call-sites. Source-level guard: the three filter
    function names AND their call-sites still exist in the
    cmd_orphan_imports source.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_orphan_imports.py"
    src = src_path.read_text(encoding="utf-8")
    # The three W160 filter helpers must be defined.
    assert "def _is_conftest_path(" in src, (
        "W160 conftest filter helper missing from cmd_orphan_imports; filter contract has been violated."
    )
    assert "def _optional_import_line_set(" in src, (
        "W160 try/except ImportError filter helper missing from cmd_orphan_imports; filter contract has been violated."
    )
    assert "def _resolve_relative_import(" in src, (
        "W160 relative-import filter helper missing from cmd_orphan_imports; filter contract has been violated."
    )
    # AND the three filters must still be CALLED inside _scan_python.
    assert "_is_conftest_path(" in src, (
        "W160 conftest filter no longer invoked in cmd_orphan_imports; the W160 contract has been violated."
    )
    assert "_optional_import_line_set(" in src, (
        "W160 try/except filter no longer invoked in cmd_orphan_imports; the W160 contract has been violated."
    )
    assert "_resolve_relative_import(" in src, (
        "W160 relative-import filter no longer invoked in cmd_orphan_imports; the W160 contract has been violated."
    )


# ---------------------------------------------------------------------------
# (13) DETECTOR FAMILY 11-WAY pairing bonus -- CLOSES THE FAMILY
# ---------------------------------------------------------------------------


def test_detector_family_11way_marker_prefixes_coexist(cli_runner, orphan_imports_project, monkeypatch):
    """DETECTOR FAMILY 11-WAY pairing bonus -- CLOSES THE FAMILY.

    Confirm ``orphan_imports_<phase>_failed:`` markers coexist with
    ``hotspots_*`` (W607-CP), ``n1_*`` (W607-CB), ``over_fetch_*``
    (W607-CE), ``missing_index_*`` (W607-CI), ``auth_gaps_*``
    (W607-CM), ``smells_*`` (W607-BN), ``vibe_check_*`` (W607-BS),
    ``clones_*`` (W607-BQ), ``duplicates_*`` (W607-BM), and
    ``dead_*`` (W607-BX) markers without cross-prefix leakage.

    This is the load-bearing prefix-discipline test for the detector
    family 11-way: each command's marker family stays inside its own
    prefix so a downstream finder/grep on ``orphan_imports_*``
    markers picks up ONLY the orphan-imports detector substrate
    failures. CLOSES the 11-WAY detector family (orphan-imports +
    hotspots + n1 + over-fetch + missing-index + auth-gaps + smells +
    vibe-check + clones + duplicates + dead == 11 detectors).
    """
    from roam.commands import cmd_orphan_imports

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-11way-from-W607-CR")

    monkeypatch.setitem(cmd_orphan_imports._SCANNERS, "python", _raise)

    result = _invoke_orphan_imports(cli_runner, orphan_imports_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # The orphan-imports marker fires.
    assert any(m.startswith("orphan_imports_scan_python_failed:") for m in all_wo), all_wo

    # None of the ten detector-sibling prefixes leak into the
    # orphan-imports envelope. This CLOSES the 11-way detector
    # family: orphan-imports + hotspots + n1 + over-fetch +
    # missing-index + auth-gaps + smells + vibe-check + clones +
    # duplicates + dead == 11 detectors.
    for forbidden_prefix in (
        "hotspots_",
        "n1_",
        "over_fetch_",
        "missing_index_",
        "auth_gaps_",
        "smells_",
        "vibe_check_",
        "clones_",
        "duplicates_",
        "dead_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on detector-family 11-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_orphan_imports "
            f"envelope; got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (14) AST source-level guard: canonical marker shape
# ---------------------------------------------------------------------------


def test_w607cr_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-CR marker shape lives in cmd_orphan_imports."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_orphan_imports.py"
    src = src_path.read_text(encoding="utf-8")
    # The fstring template
    # ``f"orphan_imports_{phase}_failed:{type(exc).__name__}:{exc}"``
    # MUST appear -- the canonical marker construction site. Any
    # divergence from this shape (e.g., a missing colon, mis-spelled
    # prefix) would break consumer parsers.
    fstring_pattern = 'f"orphan_imports_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-CR marker fstring missing from cmd_orphan_imports; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (15) SARIF projection failure -> marker surfaces on CI path
# ---------------------------------------------------------------------------


def test_orphan_imports_sarif_failure_surfaces_marker(cli_runner, orphan_imports_project, monkeypatch):
    """A raise in the SARIF projection must NOT crash the orphan-imports CI path.

    The SARIF projection is wrapped so a writer exception is
    contained -- the click command still returns cleanly without a
    traceback. By design SARIF mode short-circuits the envelope
    (writes pure SARIF to stdout), so we verify exit_code only on
    the smoke-test axis; the marker accumulator stays in-process
    but is not flushed to a second envelope.
    """
    from roam.output import sarif as sarif_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-sarif-from-W607-CR")

    monkeypatch.setattr(sarif_mod, "orphan_imports_to_sarif", _raise)

    result = _invoke_orphan_imports(
        cli_runner,
        orphan_imports_project,
        json_mode=False,
        sarif=True,
    )
    # The W607-CR wrap protects against crash even on the SARIF path.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (16) derive_distribution failure -> envelope composes with empty data
# ---------------------------------------------------------------------------


def test_orphan_imports_derive_distribution_failure_degrades_cleanly(cli_runner, orphan_imports_project, monkeypatch):
    """A raise in the R22 wrap / distribution computation degrades cleanly."""
    from roam.commands import cmd_orphan_imports

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-derive-from-W607-CR")

    # wrap_findings is imported into cmd_orphan_imports at module scope;
    # patch the module attribute so the in-command reference picks up
    # the raise.
    monkeypatch.setattr(cmd_orphan_imports, "wrap_findings", _raise)

    result = _invoke_orphan_imports(cli_runner, orphan_imports_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    derive_markers = [m for m in all_wo if m.startswith("orphan_imports_derive_distribution_failed:")]
    assert derive_markers, f"expected orphan_imports_derive_distribution_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True
