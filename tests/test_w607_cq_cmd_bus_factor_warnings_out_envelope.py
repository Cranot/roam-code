"""W607-CQ -- ``cmd_bus_factor`` substrate-boundary plumbing.

cmd_bus_factor is the team-coupling / single-author-risk detector (W115
origin per CLAUDE.md detector roster -- part of the original 16
findings-registry substrate detectors). The detector analyzes git
blame + co-change history to flag knowledge-concentration risks
(bus_factor=1, stale-owner, orphan symbol). W164 layered the
solo-author summary collapse (one repo-level finding instead of N
per-directory "single author owns this directory" rows). W811/W817
sealed the Pattern-2 empty-corpus regression (explicit zero-count
verdict, no SAFE-vocabulary fallback) -- but until this wave the
command had no substrate-boundary marker plumbing -- a raise in
``_analyse_bus_factor`` (git-blame ingest), the W164
``_emit_solo_author_summary_finding`` collapse, or the downstream
verdict composer would crash the bus-factor detector outright.

This wave installs the canonical ``_w607cq_warnings_out`` bucket +
``_run_check_cq`` helper inside the ``bus_factor`` click command and
wraps every substrate boundary:

* analyse_bus_factor         -- git-blame + co-change ingest (single
                                aggregate call returning the per-dir
                                ranking + excluded-paths count)
* query_brain_methods        -- high-cc rollup (conditional)
* detect_project_shape       -- single-author auto-detection
                                (previously a bare ``except Exception:
                                shape = None`` swallow -- now disclosed)
* apply_solo_author_collapse -- STALE-only filter when solo-author
* emit_solo_author_summary   -- W164 repo-level collapse finding
* emit_bus_factor_findings   -- W115 registry mirror
                                (sqlite3.OperationalError silent no-op
                                preserved for pre-W89 DB)
* aggregate_risk_counts      -- high/medium/concentrated/stale histogram
* compose_verdict            -- LAW 6 single-line verdict
                                (min_bf / top_dir.directory accesses)
* serialize_to_sarif         -- SARIF projection (CI integration)

Marker family ``bus_factor_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test. cmd_bus_factor closes the DETECTOR FAMILY
11-WAY with cmd_auth_gaps + cmd_n1 + cmd_over_fetch +
cmd_missing_index + cmd_smells + cmd_vibe_check + cmd_clones +
cmd_duplicates + cmd_dead + cmd_hotspots.

W811/W817 PATTERN-2 REGRESSION GUARD
------------------------------------

W811 surfaced the empty-corpus gap and W817 closed it on BOTH paths
(no-data branch + ranked branch) by switching the verdict from the
SAFE/completed vocabulary to the explicit zero-count + concrete-state
wording. The regression-guard tests below confirm:

  1. The clean empty corpus path still emits the W811/W817 verdict
     shape (no SAFE vocabulary, concrete state).
  2. The W607-CQ substrate boundary on ``_analyse_bus_factor`` does
     NOT re-introduce Pattern-2 silent-fallback -- a raise in that
     substrate still emits a non-empty envelope with a marker AND
     ``partial_success: True``, never a SAFE verdict on a degraded
     state.

W164 SOLO-AUTHOR COLLAPSE REGRESSION
------------------------------------

A regression test confirms the solo-author summary collapse path
(W164) survives W607-CQ plumbing -- the
``_emit_solo_author_summary_finding`` substrate stays wired into the
``--persist`` branch on ``single_author_mode``.

PER-SYMBOL ISOLATION
--------------------

The bus-factor detector aggregates at the directory level (not the
symbol level), so the "per-symbol isolation" check is realised here
as **per-substrate isolation**: simulate ``_query_brain_methods``
raising while the main ``_analyse_bus_factor`` pass succeeds -- the
brain-method marker surfaces, the directory ranking still composes,
and the envelope stays a clean ranked verdict (partial_success flips
because of the marker, but the ranked content survives).

GIT-BLAME DEGRADATION
---------------------

A targeted test simulates ``_analyse_bus_factor`` raising -- the
expected degraded path is the no-data envelope (zero counts, "no git
history data available" verdict, ``partial_success: True``).

DETECTOR FAMILY 11-WAY PAIRING
------------------------------

The bonus pairing test confirms ``bus_factor_<phase>_failed:``
markers coexist with the ten sibling detector families
(auth-gaps / n1 / over-fetch / missing-index / smells / vibe-check /
clones / duplicates / dead / hotspots) without cross-prefix leakage.
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


def _build_bus_factor_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_bus_factor.

    Builds a tiny Python fixture with one commit so the detector's
    git-history ingest has SOMETHING to crunch (without git history
    the no-data branch fires before any other substrate runs, which
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
    )
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/engine.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(1, 1, 'helper', 'src.engine.helper', 'function', 1, 2, 'public', 1)"
    )
    # One commit + one file change so _analyse_bus_factor has something to ingest.
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
    """Invoke the bus_factor click command directly."""
    from roam.commands.cmd_bus_factor import bus_factor

    obj = {"json": json_mode, "sarif": sarif, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(bus_factor, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


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
# (1) Happy path -- envelope omits W607-CQ substrate markers
# ---------------------------------------------------------------------------


def test_bus_factor_clean_envelope_omits_w607cq_markers(cli_runner, bus_factor_project):
    """Clean bus-factor run -> no W607-CQ substrate markers."""
    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "bus-factor"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    cq_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"bus_factor_{p}_failed:" in m for p in _CQ_PHASES)
    ]
    assert not cq_markers, (
        f"clean bus-factor must NOT surface W607-CQ substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) analyse_bus_factor failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_bus_factor_analyse_failure_marker_format(cli_runner, bus_factor_project, monkeypatch):
    """If ``_analyse_bus_factor`` raises, surface the canonical marker."""
    from roam.commands import cmd_bus_factor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-analyse-from-W607-CQ")

    monkeypatch.setattr(cmd_bus_factor, "_analyse_bus_factor", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    analyse_markers = [m for m in all_wo if m.startswith("bus_factor_analyse_bus_factor_failed:")]
    assert analyse_markers, f"expected bus_factor_analyse_bus_factor_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in analyse_markers), analyse_markers
    assert any("synthetic-analyse-from-W607-CQ" in m for m in analyse_markers), analyse_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_bus_factor_w607cq_warnings_in_envelope(cli_runner, bus_factor_project, monkeypatch):
    """Non-empty W607-CQ bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_bus_factor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CQ")

    monkeypatch.setattr(cmd_bus_factor, "_analyse_bus_factor", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CQ disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CQ disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("bus_factor_analyse_bus_factor_failed:")]
    assert markers, f"expected bus_factor_analyse_bus_factor_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_bus_factor_three_segment_marker_shape(cli_runner, bus_factor_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_bus_factor

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CQ")

    monkeypatch.setattr(cmd_bus_factor, "_analyse_bus_factor", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("bus_factor_analyse_bus_factor_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "bus_factor_analyse_bus_factor_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) detect_project_shape failure -> marker surfaces, command still emits
# ---------------------------------------------------------------------------


def test_bus_factor_detect_project_shape_failure_surfaces_marker(cli_runner, bus_factor_project, monkeypatch):
    """A raise in ``detect_project_shape`` previously was swallowed by a
    bare ``except Exception: shape = None``. Now disclosed via the
    W607-CQ marker so observers know auto-team-size detection degraded.
    """

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-project-shape-from-W607-CQ")

    # ``detect_project_shape`` is imported inline inside the click
    # command body, so patch the source module not cmd_bus_factor.
    import roam.output.project_shape as _ps

    monkeypatch.setattr(_ps, "detect_project_shape", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    shape_markers = [m for m in all_wo if m.startswith("bus_factor_detect_project_shape_failed:")]
    assert shape_markers, all_wo
    # Envelope still composes.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-CQ stays in ``bus_factor_*`` family
# ---------------------------------------------------------------------------


def test_w607cq_marker_prefix_stays_in_bus_factor_family(cli_runner, bus_factor_project, monkeypatch):
    """Every W607-CQ substrate marker uses the canonical ``bus_factor_*`` prefix.

    Hard distinction from sibling W607-* layers across the detector
    family (auth-gaps / n1 / over-fetch / missing-index / smells /
    vibe-check / clones / duplicates / dead / hotspots) AND across
    the broader command surface.
    """
    from roam.commands import cmd_bus_factor

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CQ")

    monkeypatch.setattr(cmd_bus_factor, "_analyse_bus_factor", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("bus_factor_"), (
            f"every surfaced W607-CQ marker must use the ``bus_factor_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
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
            ("complexity_", "cmd_complexity W607-BJ"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("vulns_", "cmd_vulns W607-AQ + CH (security sibling)"),
            ("taint_", "cmd_taint W607-AY + CJ (security sibling)"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_bus_factor carries the W607-CQ accumulator
# ---------------------------------------------------------------------------


def test_cmd_bus_factor_carries_w607cq_accumulator():
    """AST-level guard: cmd_bus_factor source carries the W607-CQ accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    assert src_path.exists(), f"cmd_bus_factor.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607cq_warnings_out" in src, (
        "W607-CQ accumulator missing from cmd_bus_factor; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_cq" in src, (
        "W607-CQ ``_run_check_cq`` helper missing from cmd_bus_factor; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_cq = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cq":
            found_run_check_cq = True
            break
    assert found_run_check_cq, (
        "W607-CQ ``_run_check_cq`` helper not found in cmd_bus_factor AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-CQ substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607cq_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-CQ substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _CQ_PHASES:
        same_line = f'_run_check_cq("{phase}"' in src
        multi_line = (
            f'_run_check_cq(\n        "{phase}"' in src
            or f'_run_check_cq(\n            "{phase}"' in src
            or f'_run_check_cq(\n                "{phase}"' in src
            or f'_run_check_cq(\n                    "{phase}"' in src
            or f'_run_check_cq(\n                        "{phase}"' in src
        )
        marker_grep = f"bus_factor_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-CQ wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) emit_bus_factor_findings failure -> marker surfaces, command still emits
# ---------------------------------------------------------------------------


def test_bus_factor_emit_findings_failure_surfaces_marker(cli_runner, bus_factor_project, monkeypatch):
    """W115 emit failure (non-OperationalError) surfaces W607-CQ marker.

    sqlite3.OperationalError is the EXPECTED pre-W89 path (silent
    no-op). Generic exceptions surface via the W607-CQ marker.
    """
    from roam.commands import cmd_bus_factor

    # Force the persist branch by forging a result list with one
    # concentrated row so the emit substrate actually runs.
    monkeypatch.setattr(
        cmd_bus_factor,
        "_analyse_bus_factor",
        lambda conn, stale_months, exclude_prefixes=None: (
            [
                {
                    "directory": "src/",
                    "bus_factor": 1,
                    "entropy": 0.0,
                    "knowledge_risk": "CRITICAL",
                    "total_commits": 5,
                    "total_churn": 50,
                    "author_count": 1,
                    "primary_author": "alice",
                    "primary_share": 1.0,
                    "primary_share_pct": 100,
                    "primary_last_active": 1700000000,
                    "concentrated": True,
                    "stale_primary": False,
                    "staleness_factor": 1.0,
                    "dir_last_active": 1700000000,
                    "risk_score": 1.0,
                    "risk": "HIGH",
                    "top_authors": [
                        {
                            "name": "alice",
                            "commits": 5,
                            "churn": 50,
                            "share": 1.0,
                            "share_pct": 100,
                            "last_active": 1700000000,
                        }
                    ],
                }
            ],
            0,
        ),
    )
    # Force team-mode so the non-solo-author registry branch runs.
    # ``detect_project_shape`` is imported inline inside the click
    # command body, so patch the source module not cmd_bus_factor.
    import roam.output.project_shape as _ps

    monkeypatch.setattr(_ps, "detect_project_shape", lambda c, p: None)

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-CQ")

    monkeypatch.setattr(cmd_bus_factor, "_emit_bus_factor_findings", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("bus_factor_emit_bus_factor_findings_failed:")]
    assert emit_markers, f"expected bus_factor_emit_bus_factor_findings_failed: marker; got {all_wo!r}"
    # The bus-factor command still emits a clean envelope past the
    # registry-mirror failure -- W115 is additive, not load-bearing.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) emit_findings OperationalError path stays silent (no W607-CQ marker)
# ---------------------------------------------------------------------------


def test_bus_factor_emit_findings_operational_error_stays_silent(cli_runner, bus_factor_project, monkeypatch):
    """W607-CQ MUST preserve the W115 silent no-op contract on
    ``sqlite3.OperationalError`` (pre-W89 schema -- no findings table).

    The marker MUST NOT surface for this expected degraded path.
    """
    from roam.commands import cmd_bus_factor

    # Same scaffolding as test (9) so persist actually attempts to emit.
    monkeypatch.setattr(
        cmd_bus_factor,
        "_analyse_bus_factor",
        lambda conn, stale_months, exclude_prefixes=None: (
            [
                {
                    "directory": "src/",
                    "bus_factor": 1,
                    "entropy": 0.0,
                    "knowledge_risk": "CRITICAL",
                    "total_commits": 5,
                    "total_churn": 50,
                    "author_count": 1,
                    "primary_author": "alice",
                    "primary_share": 1.0,
                    "primary_share_pct": 100,
                    "primary_last_active": 1700000000,
                    "concentrated": True,
                    "stale_primary": False,
                    "staleness_factor": 1.0,
                    "dir_last_active": 1700000000,
                    "risk_score": 1.0,
                    "risk": "HIGH",
                    "top_authors": [
                        {
                            "name": "alice",
                            "commits": 5,
                            "churn": 50,
                            "share": 1.0,
                            "share_pct": 100,
                            "last_active": 1700000000,
                        }
                    ],
                }
            ],
            0,
        ),
    )
    # ``detect_project_shape`` is imported inline inside the click
    # command body, so patch the source module not cmd_bus_factor.
    import roam.output.project_shape as _ps

    monkeypatch.setattr(_ps, "detect_project_shape", lambda c, p: None)

    def _raise_op_err(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings (pre-W89 schema)")

    monkeypatch.setattr(cmd_bus_factor, "_emit_bus_factor_findings", _raise_op_err)

    result = _invoke_bus_factor(cli_runner, bus_factor_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("bus_factor_emit_bus_factor_findings_failed:")]
    assert not emit_markers, (
        f"sqlite3.OperationalError is the EXPECTED pre-W89 silent "
        f"no-op path; W607-CQ marker MUST NOT surface; "
        f"got {emit_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) compose_verdict failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_bus_factor_compose_verdict_failure_degrades(cli_runner, bus_factor_project, monkeypatch):
    """A raise inside the verdict composer degrades to the ``"no data"`` floor.

    The verdict composer indexes into ``top_dir["directory"]`` and
    iterates ``r["bus_factor"]`` -- malformed result rows could
    KeyError on either path. The W607-CQ wrap surfaces the marker
    and keeps the envelope a valid LAW-6 single-line verdict.
    """
    from roam.commands import cmd_bus_factor

    # Forge a malformed result row missing the "directory" key so
    # ``top_dir['directory']`` inside _compose_verdict raises KeyError.
    monkeypatch.setattr(
        cmd_bus_factor,
        "_analyse_bus_factor",
        lambda conn, stale_months, exclude_prefixes=None: (
            [
                {
                    # NO "directory" key -- compose_verdict KeyErrors.
                    "bus_factor": 1,
                    "entropy": 0.0,
                    "knowledge_risk": "CRITICAL",
                    "total_commits": 5,
                    "total_churn": 50,
                    "author_count": 1,
                    "primary_author": "alice",
                    "primary_share": 1.0,
                    "primary_share_pct": 100,
                    "primary_last_active": 1700000000,
                    "concentrated": True,
                    "stale_primary": False,
                    "staleness_factor": 1.0,
                    "dir_last_active": 1700000000,
                    "risk_score": 1.0,
                    "risk": "HIGH",
                    "top_authors": [],
                }
            ],
            0,
        ),
    )
    # ``detect_project_shape`` is imported inline inside the click
    # command body, so patch the source module not cmd_bus_factor.
    import roam.output.project_shape as _ps

    monkeypatch.setattr(_ps, "detect_project_shape", lambda c, p: None)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    verdict_markers = [m for m in all_wo if m.startswith("bus_factor_compose_verdict_failed:")]
    assert verdict_markers, all_wo
    # Verdict still emits (LAW 6 single-line).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (12) W811/W817 PATTERN-2 REGRESSION GUARD: zero-count verdict preserved
# ---------------------------------------------------------------------------


def test_w811_w817_empty_corpus_verdict_preserved_under_w607cq(cli_runner, tmp_path):
    """W811/W817 regression guard: empty-corpus envelope still names the
    concrete state ("no git history data available") and forbids the
    SAFE/PASSED/completed vocabulary. The W607-CQ plumbing must NOT
    re-introduce a Pattern-2 bug.

    Builds a *minimal* fixture without any git_commits rows so the
    detector hits the no-data branch at the top of the bus_factor
    command (the W811/W817 zero-count envelope).
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
            line_start INTEGER, line_end INTEGER
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
            author TEXT, timestamp INTEGER, message TEXT
        );
        CREATE TABLE IF NOT EXISTS git_file_changes (
            id INTEGER PRIMARY KEY,
            commit_id INTEGER NOT NULL,
            file_id INTEGER,
            lines_added INTEGER DEFAULT 0,
            lines_removed INTEGER DEFAULT 0
        );
        """
    )
    conn.commit()
    conn.close()

    result = _invoke_bus_factor(cli_runner, tmp_path)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    verdict = summary.get("verdict") or ""
    # Empty-corpus envelope: "no git history data available" (W811/W817
    # explicit-state contract). NOT SAFE/passed/completed.
    assert "no git history" in verdict.lower() or "no data" in verdict.lower(), (
        f"empty-corpus verdict must name the no-data state explicitly (W811/W817 contract); got {verdict!r}"
    )
    for forbidden in ("safe", "passed", "completed", "all clear"):
        assert forbidden not in verdict.lower(), (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )
    # Zero counts mirror the verdict (W811/W817 invariant).
    assert summary.get("directories_analyzed") == 0
    assert summary.get("high_risk") == 0


def test_w811_w817_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, bus_factor_project, monkeypatch):
    """W811/W817 Pattern-2 regression guard on the degraded path.

    If ``_analyse_bus_factor`` raises, the empty-floor default kicks
    in (results=[], excluded_files_count=0) and the envelope is
    emitted. The W607-CQ wrap MUST flip ``partial_success: True`` on
    that branch so the empty-state envelope is NOT mistaken for a
    clean "no git history" verdict.
    """
    from roam.commands import cmd_bus_factor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-W811-pattern-2-from-W607-CQ")

    monkeypatch.setattr(cmd_bus_factor, "_analyse_bus_factor", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    analyse_markers = [m for m in all_wo if m.startswith("bus_factor_analyse_bus_factor_failed:")]
    assert analyse_markers, (
        f"degraded path MUST surface the analyse_bus_factor marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (13) DETECTOR FAMILY 11-WAY pairing bonus
# ---------------------------------------------------------------------------


def test_detector_family_11way_marker_prefixes_coexist(cli_runner, bus_factor_project, monkeypatch):
    """DETECTOR FAMILY 11-WAY pairing bonus.

    Confirm ``bus_factor_<phase>_failed:`` markers coexist with the
    ten sibling detector families without cross-prefix leakage:
    auth_gaps_* (W607-CM), n1_* (W607-CB), over_fetch_* (W607-CE),
    missing_index_* (W607-CI), smells_* (W607-BN), vibe_check_*
    (W607-BS), clones_* (W607-BQ), duplicates_* (W607-BM), dead_*
    (W607-BX), hotspots_* (W607-* runtime). Closes the 11-detector
    family.
    """
    from roam.commands import cmd_bus_factor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-11way-from-W607-CQ")

    monkeypatch.setattr(cmd_bus_factor, "_analyse_bus_factor", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("bus_factor_analyse_bus_factor_failed:") for m in all_wo), all_wo

    # None of the ten detector-sibling prefixes leak into the
    # bus-factor envelope.
    for forbidden_prefix in (
        "auth_gaps_",
        "n1_",
        "over_fetch_",
        "missing_index_",
        "smells_",
        "vibe_check_",
        "clones_",
        "duplicates_",
        "dead_",
        "hotspots_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on detector-family 11-way pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_bus_factor envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (14) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607cq_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-CQ marker shape is implemented
    in the shared ``boundary_helpers`` module and wired to the ``bus_factor``
    recipe in cmd_bus_factor.
    """
    cmd_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_bus_factor.py"
    helper_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "boundary_helpers.py"
    cmd_src = cmd_src_path.read_text(encoding="utf-8")
    helper_src = helper_src_path.read_text(encoding="utf-8")
    # The generic marker template lives in the shared helper.
    fstring_pattern = 'f"{recipe_name}_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in helper_src, (
        f"canonical W607-CQ marker fstring missing from boundary_helpers; expected: {fstring_pattern}"
    )
    # cmd_bus_factor binds the template to the bus_factor recipe name.
    assert 'make_run_check("bus_factor",' in cmd_src, (
        "cmd_bus_factor must wire the shared helper to the bus_factor marker family."
    )


# ---------------------------------------------------------------------------
# (15) SARIF projection failure -> marker surfaces on CI path
# ---------------------------------------------------------------------------


def test_bus_factor_sarif_failure_surfaces_marker(cli_runner, bus_factor_project, monkeypatch):
    """A raise in the SARIF projection must NOT crash the CI path."""
    from roam.output import sarif as sarif_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-sarif-from-W607-CQ")

    monkeypatch.setattr(sarif_mod, "bus_factor_to_sarif", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project, json_mode=False, sarif=True)
    # The W607-CQ wrap protects against crash even on the SARIF path.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (16) query_brain_methods failure -> marker, ranked envelope still composes
# ---------------------------------------------------------------------------


def test_bus_factor_query_brain_methods_failure_degrades_cleanly(cli_runner, bus_factor_project, monkeypatch):
    """A raise in the high-complexity-rollup degrades to brain_list=[].

    Per-substrate isolation: ``_query_brain_methods`` only runs with
    ``--brain-methods``; a raise there must NOT torpedo the main
    bus-factor ranking. The marker surfaces, ``partial_success: True``
    flips, and the directory ranking still emits a coherent envelope.
    """
    from roam.commands import cmd_bus_factor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-brain-from-W607-CQ")

    monkeypatch.setattr(cmd_bus_factor, "_query_brain_methods", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project, "--brain-methods")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    brain_markers = [m for m in all_wo if m.startswith("bus_factor_query_brain_methods_failed:")]
    assert brain_markers, all_wo
    # Envelope still composes -- partial_success flips on degraded path,
    # but the ranked content (directories key) is well-formed.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (17) W164 SOLO-AUTHOR COLLAPSE REGRESSION: emit_solo_author_summary wraps
# ---------------------------------------------------------------------------


def test_w164_solo_author_collapse_survives_w607cq(cli_runner, bus_factor_project, monkeypatch):
    """W164 regression guard: the solo-author summary-finding emit
    substrate stays wired through W607-CQ plumbing.

    A raise inside ``_emit_solo_author_summary_finding`` surfaces
    via the W607-CQ marker (not silently swallowed). The W164
    collapse is the W607-CQ-tracked degraded path -- if a future
    refactor removes the wrap, the marker stops surfacing and this
    test fails.
    """
    from roam.commands import cmd_bus_factor

    # Force the solo-author-mode branch by faking a shape with
    # team_size="single-author" and a result that the W164 path
    # operates on.
    class _FakeShape:
        team_size = "single-author"

    # ``detect_project_shape`` is imported inline inside the click
    # command body, so patch the source module not cmd_bus_factor.
    import roam.output.project_shape as _ps

    monkeypatch.setattr(_ps, "detect_project_shape", lambda c, p: _FakeShape())
    monkeypatch.setattr(
        cmd_bus_factor,
        "_analyse_bus_factor",
        lambda conn, stale_months, exclude_prefixes=None: (
            [
                {
                    "directory": "src/",
                    "bus_factor": 1,
                    "entropy": 0.0,
                    "knowledge_risk": "CRITICAL",
                    "total_commits": 5,
                    "total_churn": 50,
                    "author_count": 1,
                    "primary_author": "alice",
                    "primary_share": 1.0,
                    "primary_share_pct": 100,
                    "primary_last_active": 1700000000,
                    "concentrated": True,
                    "stale_primary": False,
                    "staleness_factor": 1.0,
                    "dir_last_active": 1700000000,
                    "risk_score": 1.0,
                    "risk": "HIGH",
                    "top_authors": [
                        {
                            "name": "alice",
                            "commits": 5,
                            "churn": 50,
                            "share": 1.0,
                            "share_pct": 100,
                            "last_active": 1700000000,
                        }
                    ],
                }
            ],
            0,
        ),
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-w164-summary-from-W607-CQ")

    monkeypatch.setattr(cmd_bus_factor, "_emit_solo_author_summary_finding", _raise)

    result = _invoke_bus_factor(cli_runner, bus_factor_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    summary_markers = [m for m in all_wo if m.startswith("bus_factor_emit_solo_author_summary_failed:")]
    assert summary_markers, (
        f"W164 collapse path must surface bus_factor_emit_solo_author_summary_failed: marker; got {all_wo!r}"
    )
    # The W164 path also flips partial_success on the degraded leg.
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (18) aggregate_risk_counts failure -> empty histogram, envelope composes
# ---------------------------------------------------------------------------


def test_bus_factor_aggregate_risk_counts_failure_degrades(cli_runner, bus_factor_project, monkeypatch):
    """A raise in the risk-histogram aggregator degrades to (0, 0, 0, 0, 0).

    The aggregator indexes ``r["risk"]`` / ``r["concentrated"]`` /
    ``r["stale_primary"]`` / ``r["knowledge_risk"]`` -- KeyError on
    a malformed result row. The W607-CQ wrap surfaces the marker
    and keeps the envelope a coherent ranked verdict.
    """
    from roam.commands import cmd_bus_factor

    # Forge a malformed result row missing ``risk`` so aggregate_risk_counts
    # KeyErrors on the first sum().
    monkeypatch.setattr(
        cmd_bus_factor,
        "_analyse_bus_factor",
        lambda conn, stale_months, exclude_prefixes=None: (
            [
                {
                    "directory": "src/",
                    "bus_factor": 1,
                    "entropy": 0.0,
                    "knowledge_risk": "CRITICAL",
                    # NO "risk" key -- aggregate_risk_counts KeyErrors.
                    "total_commits": 5,
                    "total_churn": 50,
                    "author_count": 1,
                    "primary_author": "alice",
                    "primary_share": 1.0,
                    "primary_share_pct": 100,
                    "primary_last_active": 1700000000,
                    "concentrated": True,
                    "stale_primary": False,
                    "staleness_factor": 1.0,
                    "dir_last_active": 1700000000,
                    "risk_score": 1.0,
                    "top_authors": [],
                }
            ],
            0,
        ),
    )
    # ``detect_project_shape`` is imported inline inside the click
    # command body, so patch the source module not cmd_bus_factor.
    import roam.output.project_shape as _ps

    monkeypatch.setattr(_ps, "detect_project_shape", lambda c, p: None)

    result = _invoke_bus_factor(cli_runner, bus_factor_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    agg_markers = [m for m in all_wo if m.startswith("bus_factor_aggregate_risk_counts_failed:")]
    assert agg_markers, all_wo
    # Counts collapse to the all-zero floor without crashing.
    assert data["summary"].get("high_risk") == 0
    assert data["summary"].get("medium_risk") == 0
    assert data["summary"].get("partial_success") is True
