"""W607-BS -- ``cmd_vibe_check`` substrate-boundary plumbing.

The 10-detector vibe-check AI-rot pipeline (8 score-bearing + 2 W371
informational) is the LLM-rot sibling of cmd_smells (W607-BN). Per
CLAUDE.md it persists ~831 findings rows on roam-code with confidence
tiers ``static_analysis`` / ``structural`` per detector. This wave
installs the canonical ``_w607bs_warnings_out`` bucket + ``_run_check_bs``
helper inside the ``vibe-check`` click command and wraps every substrate
boundary:

* load_corpus                       -- files/symbols COUNT(*) probes
* detect_dead_exports               -- pattern 1
* detect_short_churn                -- pattern 2
* detect_empty_handlers             -- pattern 3
* detect_stubs                      -- pattern 4 (abandoned_stubs)
* detect_hallucinated_imports       -- pattern 5
* detect_error_inconsistency        -- pattern 6
* detect_comment_anomalies          -- pattern 7
* detect_copy_paste                 -- pattern 8
* detect_modular_mirage             -- W371 informational pattern 9
* detect_boilerplate_inflation      -- W371 informational pattern 10
* aggregate_by_kind                 -- worst-files rollup over the 10
* classify_severity                 -- _compute_score + _severity_label
* emit_findings                     -- W125 findings-registry mirror

Marker family ``vibe_check_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the prefix-discipline
test.

PER-DETECTOR ISOLATION discipline
---------------------------------

A raise in one of the 10 detectors degrades that detector's tuple to its
empty-floor default (``(0, 0)`` for dead-exports, ``(0, 0, [])`` for the
other 9) and surfaces a single marker. The OTHER nine detectors continue
to report their findings -- the envelope composes cleanly with the
remaining 9 patterns populated AND the failing detector's marker on
``warnings_out``. This is the canonical W607 discipline.

SMELLS/VIBE-CHECK 2-WAY pairing
-------------------------------

The bonus integration test invokes both ``smells`` and ``vibe-check`` on
the same workspace and confirms each command's marker family stays inside
its own prefix without leaking across the LLM-rot duo boundary.
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


def _build_vibe_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for vibe-check.

    A real (non-degenerate) corpus is needed so the empty-corpus
    short-circuit does NOT fire and the score/severity classification
    substrate has real work to do. Mirrors the W607-BN smells fixture
    shape with the table additions vibe-check needs (commits, file_hashes,
    body_hashes are intentionally NOT created -- vibe-check tolerates
    missing optional tables via its own per-detector try/except clauses).
    """
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text("def main():\n    return 42\n")
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
        "visibility, is_exported) "
        "VALUES (1, 1, 'main', 'src.engine.main', 'function', 1, 2, 'public', 1)"
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def vibe_project(tmp_path):
    return _build_vibe_project(tmp_path)


def _invoke_vibe_check(cli_runner, project_root, *args, json_mode=True):
    """Invoke the vibe-check click command directly (bypassing the CLI group).

    Mirrors test_w607_bn_cmd_smells_warnings_out_envelope._invoke_smells.
    """
    from roam.commands.cmd_vibe_check import vibe_check

    obj = {"json": json_mode, "sarif": False, "budget": 0}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(vibe_check, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_BS_PHASES = (
    "load_corpus",
    "detect_dead_exports",
    "detect_short_churn",
    "detect_empty_handlers",
    "detect_stubs",
    "detect_hallucinated_imports",
    "detect_error_inconsistency",
    "detect_comment_anomalies",
    "detect_copy_paste",
    "detect_modular_mirage",
    "detect_boilerplate_inflation",
    "aggregate_by_kind",
    "classify_severity",
    "emit_findings",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BS substrate markers
# ---------------------------------------------------------------------------


def test_vibe_check_clean_envelope_omits_w607bs_markers(cli_runner, vibe_project):
    """Clean vibe-check run -> no W607-BS substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-BS bucket on
    the success path must NOT introduce new ``vibe_check_<phase>_failed:``
    markers tied to the W607-BS wrap.
    """
    result = _invoke_vibe_check(cli_runner, vibe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "vibe-check"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    bs_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"vibe_check_{p}_failed:" in m for p in _BS_PHASES)
    ]
    assert not bs_markers, (
        f"clean vibe-check must NOT surface W607-BS substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) classify_severity failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_vibe_check_classify_severity_failure_marker_format(cli_runner, vibe_project, monkeypatch):
    """If ``_compute_score`` raises, surface the canonical 3-segment marker."""
    from roam.commands import cmd_vibe_check

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-classify-from-W607-BS")

    monkeypatch.setattr(cmd_vibe_check, "_compute_score", _raise)

    result = _invoke_vibe_check(cli_runner, vibe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    classify_markers = [m for m in all_wo if m.startswith("vibe_check_classify_severity_failed:")]
    assert classify_markers, f"expected vibe_check_classify_severity_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in classify_markers), classify_markers
    assert any("synthetic-classify-from-W607-BS" in m for m in classify_markers), classify_markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"classify-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # LAW 6: the verdict still appears as a single line.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_vibe_check_w607bs_warnings_in_envelope(cli_runner, vibe_project, monkeypatch):
    """Non-empty W607-BS bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_vibe_check

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BS")

    monkeypatch.setattr(cmd_vibe_check, "_compute_score", _raise)

    result = _invoke_vibe_check(cli_runner, vibe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BS disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BS disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("vibe_check_classify_severity_failed:")]
    assert markers, f"expected vibe_check_classify_severity_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_vibe_check_three_segment_marker_shape(cli_runner, vibe_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_vibe_check

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-BS")

    monkeypatch.setattr(cmd_vibe_check, "_compute_score", _raise)

    result = _invoke_vibe_check(cli_runner, vibe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("vibe_check_classify_severity_failed:")]
    assert failure_markers, f"expected vibe_check_classify_severity_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "vibe_check_classify_severity_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) detect_dead_exports failure -> verdict still emits, partial_success
# ---------------------------------------------------------------------------


def test_vibe_check_detector_failure_degrades_cleanly(cli_runner, vibe_project, monkeypatch):
    """A raise in one detector must NOT crash the vibe-check command.

    Empty-floor fallback kicks in for the failing detector (degraded
    path): the envelope still emits with verdict + partial_success +
    the substrate marker.
    """
    from roam.commands import cmd_vibe_check

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-dead-exports-from-W607-BS")

    monkeypatch.setattr(cmd_vibe_check, "_detect_dead_exports", _raise)

    result = _invoke_vibe_check(cli_runner, vibe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    dead_markers = [m for m in all_wo if m.startswith("vibe_check_detect_dead_exports_failed:")]
    assert dead_markers, f"expected vibe_check_detect_dead_exports_failed: marker; got {all_wo!r}"
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-BS stays in ``vibe_check_*`` family
# ---------------------------------------------------------------------------


def test_w607bs_marker_prefix_stays_in_vibe_check_family(cli_runner, vibe_project, monkeypatch):
    """Every W607-BS substrate marker uses the canonical ``vibe_check_*`` prefix.

    Hard distinction from sibling W607-* layers including cmd_smells
    (W607-BN, ``smells_*``), cmd_complexity (W607-BJ, ``complexity_*``),
    cmd_health (W607-M/BA, ``health_*``), etc. Especially important since
    vibe-check is the LLM-rot SIBLING of smells -- a leaking marker would
    cross the duo boundary.
    """
    from roam.commands import cmd_vibe_check

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BS")

    monkeypatch.setattr(cmd_vibe_check, "_compute_score", _raise)

    result = _invoke_vibe_check(cli_runner, vibe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("vibe_check_"), (
            f"every surfaced W607-BS marker must use the ``vibe_check_*`` "
            f"prefix family (cmd_vibe_check scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("smells_", "cmd_smells W607-BN (LLM-rot sibling)"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("debt_", "cmd_debt W607-BG"),
            ("vulns_", "cmd_vulns W607-AQ"),
            ("sbom_", "cmd_sbom W607-AM"),
            ("supply_chain_", "cmd_supply_chain W607-AK"),
            ("cga_", "cmd_cga W607-AF"),
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
            ("dogfood_", "cmd_dogfood W607-D / W607-AV"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_vibe_check carries the W607-BS accumulator
# ---------------------------------------------------------------------------


def test_cmd_vibe_check_carries_w607bs_accumulator():
    """AST-level guard: cmd_vibe_check source carries the W607-BS accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vibe_check.py"
    assert src_path.exists(), f"cmd_vibe_check.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607bs_warnings_out" in src, (
        "W607-BS accumulator missing from cmd_vibe_check; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bs" in src, (
        "W607-BS ``_run_check_bs`` helper missing from cmd_vibe_check; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_bs = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bs":
            found_run_check_bs = True
            break
    assert found_run_check_bs, (
        "W607-BS ``_run_check_bs`` helper not found in cmd_vibe_check AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-BS substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bs_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BS substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vibe_check.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _BS_PHASES:
        same_line = f'_run_check_bs("{phase}"' in src
        multi_line = (
            f'_run_check_bs(\n        "{phase}"' in src
            or f'_run_check_bs(\n            "{phase}"' in src
            or f'_run_check_bs(\n                "{phase}"' in src
            or f'_run_check_bs(\n                    "{phase}"' in src
            or f'_run_check_bs(\n                        "{phase}"' in src
        )
        # emit_findings is wrapped via direct try/except (NOT _run_check_bs)
        # because it needs to distinguish sqlite3.OperationalError (expected
        # pre-W89 path) from generic Exception (W607-BS marker). Source-grep
        # on the marker name in that case.
        marker_grep = f"vibe_check_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-BS wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) PER-DETECTOR ISOLATION bonus -- 10 patterns, one fails, 9 survive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raise_phase,detector_attr",
    [
        ("detect_dead_exports", "_detect_dead_exports"),
        ("detect_short_churn", "_detect_short_churn"),
        ("detect_empty_handlers", "_detect_empty_handlers"),
        ("detect_stubs", "_detect_stubs"),
        ("detect_hallucinated_imports", "_detect_hallucinated_imports"),
        ("detect_error_inconsistency", "_detect_error_inconsistency"),
        ("detect_comment_anomalies", "_detect_comment_anomalies"),
        ("detect_copy_paste", "_detect_copy_paste"),
        ("detect_modular_mirage", "_detect_modular_mirage"),
        ("detect_boilerplate_inflation", "_detect_boilerplate_inflation"),
    ],
)
def test_w607bs_per_detector_isolation(cli_runner, vibe_project, monkeypatch, raise_phase, detector_attr):
    """PER-DETECTOR ISOLATION bonus.

    Canonical W607 discipline: if 1 of 10 detectors raises, the other 9
    must still report AND the marker for the failing one surfaces in
    warnings_out with partial_success=True.

    This is the load-bearing isolation test for the LLM-rot rollup: a
    bug in one detector (say, ``_detect_hallucinated_imports`` blowing
    up on a malformed edge row) must not collapse the entire vibe-check
    invocation. The remaining 9 patterns continue to report their
    counts in the envelope ``patterns`` array.
    """
    from roam.commands import cmd_vibe_check

    def _raise(*args, **kwargs):
        raise RuntimeError(f"synthetic-isolation-{raise_phase}")

    monkeypatch.setattr(cmd_vibe_check, detector_attr, _raise)

    result = _invoke_vibe_check(cli_runner, vibe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    isolation_markers = [m for m in all_wo if m.startswith(f"vibe_check_{raise_phase}_failed:")]
    assert isolation_markers, f"expected vibe_check_{raise_phase}_failed: marker; got {all_wo!r}"
    # partial_success flipped on the degraded path.
    assert data["summary"].get("partial_success") is True

    # The OTHER nine detectors continue to report (10 rows in patterns[]).
    patterns_field = data.get("patterns") or []
    assert isinstance(patterns_field, list), patterns_field
    assert len(patterns_field) == 10, (
        f"all 10 patterns must still appear in the envelope on per-detector "
        f"isolation; got {len(patterns_field)} rows: "
        f"{[p.get('name') for p in patterns_field]!r}"
    )

    # No marker family leaked across detector boundaries -- the only
    # phase that fires is the one we monkeypatched.
    leaked = [
        m
        for m in all_wo
        if any(
            m.startswith(f"vibe_check_{other}_failed:")
            for other in _BS_PHASES
            if other != raise_phase and other not in ("classify_severity", "aggregate_by_kind", "load_corpus")
        )
    ]
    assert not leaked, f"per-detector isolation leaked across boundaries on raise_phase={raise_phase!r}: got {leaked!r}"


# ---------------------------------------------------------------------------
# (10) aggregate_by_kind failure -> empty worst_files, envelope composes
# ---------------------------------------------------------------------------


def test_vibe_check_aggregate_by_kind_failure_degrades_cleanly(cli_runner, vibe_project, monkeypatch):
    """A raise in worst-files aggregation degrades to ``[]`` cleanly."""
    from roam.commands import cmd_vibe_check

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-aggregate-from-W607-BS")

    monkeypatch.setattr(cmd_vibe_check, "_aggregate_worst_files", _raise)

    result = _invoke_vibe_check(cli_runner, vibe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    agg_markers = [m for m in all_wo if m.startswith("vibe_check_aggregate_by_kind_failed:")]
    assert agg_markers, f"expected vibe_check_aggregate_by_kind_failed: marker; got {all_wo!r}"
    # worst_files field degrades to an empty list (envelope still composes).
    worst_files = data.get("worst_files") or []
    assert worst_files == [], f"aggregate_by_kind failure must degrade worst_files to []; got {worst_files!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (11) load_corpus failure -> empty-corpus disclosure fires + marker
# ---------------------------------------------------------------------------


def test_vibe_check_load_corpus_failure_surfaces_marker(cli_runner, vibe_project, monkeypatch):
    """A raise in the files/symbols COUNT(*) probes surfaces a marker.

    The empty-floor default ``(0, 0)`` triggers the W805-followup-A
    empty-corpus disclosure path (Pattern 2). Both the substrate marker
    AND the empty-corpus state field surface so consumers can tell the
    failure mode from a real empty repo.
    """
    from roam.commands import cmd_vibe_check

    # The corpus probe runs a fresh closure on `conn`. Wrap _run_check_bs
    # itself at the call site by patching find_project_root to a sentinel
    # is not enough -- the easiest hook is to monkeypatch open_db so the
    # returned conn raises on .execute(). Use a thin wrapper.
    real_open_db = cmd_vibe_check.open_db

    class _RaisingExecuteConn:
        def __init__(self, real_conn):
            self._real = real_conn

        def __enter__(self):
            self._real.__enter__()
            return self

        def __exit__(self, *args, **kwargs):
            return self._real.__exit__(*args, **kwargs)

        def execute(self, sql, *args, **kwargs):
            if "COUNT(*)" in sql and ("FROM files" in sql or "FROM symbols" in sql):
                raise RuntimeError("synthetic-load-corpus-from-W607-BS")
            return self._real.execute(sql, *args, **kwargs)

        def commit(self):
            return self._real.commit()

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _wrap_open(readonly=True):
        return _RaisingExecuteConn(real_open_db(readonly=readonly))

    monkeypatch.setattr(cmd_vibe_check, "open_db", _wrap_open)

    result = _invoke_vibe_check(cli_runner, vibe_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    corpus_markers = [m for m in all_wo if m.startswith("vibe_check_load_corpus_failed:")]
    assert corpus_markers, f"expected vibe_check_load_corpus_failed: marker; got {all_wo!r}"
    # The empty-floor (0, 0) triggers the W805-followup-A path.
    assert data["summary"].get("partial_success") is True
    # The empty-corpus closed-enum state surfaces alongside the marker.
    assert data["summary"].get("state") == "no_files_scanned", (
        f"empty-floor corpus path must set state=no_files_scanned; got summary={data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (12) SMELLS/VIBE-CHECK 2-WAY pairing bonus
# ---------------------------------------------------------------------------


def test_smells_and_vibe_check_marker_families_coexist_on_same_workspace(cli_runner, vibe_project, monkeypatch):
    """SMELLS/VIBE-CHECK 2-WAY pairing bonus.

    Invoke both commands back-to-back on the same workspace with a raise
    injected into each command's classify_severity substrate. Confirm:

    * cmd_vibe_check surfaces ``vibe_check_classify_severity_failed:``
    * cmd_smells surfaces ``smells_classify_severity_failed:``
    * neither marker family leaks across the LLM-rot duo boundary.
    """
    from roam.commands import cmd_smells, cmd_vibe_check

    # (a) vibe-check arm of the duo.
    def _raise_vc(*args, **kwargs):
        raise RuntimeError("synthetic-duo-vibe-check")

    monkeypatch.setattr(cmd_vibe_check, "_compute_score", _raise_vc)

    result_vc = _invoke_vibe_check(cli_runner, vibe_project)
    assert result_vc.exit_code == 0, result_vc.output
    data_vc = _json.loads(result_vc.output)
    all_wo_vc = list(data_vc.get("warnings_out") or []) + list(data_vc["summary"].get("warnings_out") or [])
    assert any(m.startswith("vibe_check_classify_severity_failed:") for m in all_wo_vc), all_wo_vc
    # No smells_* leakage in the vibe-check envelope.
    assert not any(m.startswith("smells_") for m in all_wo_vc), all_wo_vc

    # Reset the vibe-check monkeypatch so it doesn't bleed into smells.
    monkeypatch.undo()

    # (b) smells arm of the duo. Use the same project root.
    def _raise_sm(*args, **kwargs):
        raise RuntimeError("synthetic-duo-smells")

    monkeypatch.setattr(cmd_smells, "wrap_findings", _raise_sm)

    obj = {"json": True, "sarif": False, "budget": 0, "detail": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(vibe_project))
        result_sm = cli_runner.invoke(cmd_smells.smells, [], obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert result_sm.exit_code == 0, result_sm.output
    data_sm = _json.loads(result_sm.output)
    all_wo_sm = list(data_sm.get("warnings_out") or []) + list(data_sm["summary"].get("warnings_out") or [])
    assert any(m.startswith("smells_classify_severity_failed:") for m in all_wo_sm), all_wo_sm
    # No vibe_check_* leakage in the smells envelope.
    assert not any(m.startswith("vibe_check_") for m in all_wo_sm), all_wo_sm


# ---------------------------------------------------------------------------
# (13) emit_findings failure -> marker surfaces, vibe-check still emits
# ---------------------------------------------------------------------------


def test_vibe_check_emit_findings_failure_surfaces_marker(cli_runner, vibe_project, monkeypatch):
    """W125 emit failure (non-OperationalError) surfaces W607-BS marker.

    sqlite3.OperationalError is the EXPECTED pre-W89 path (silent
    no-op). Generic exceptions surface via the W607-BS marker so a real
    bug in the persist substrate is loud, not silent.
    """
    from roam.commands import cmd_vibe_check

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-BS")

    monkeypatch.setattr(cmd_vibe_check, "_emit_vibe_check_findings", _raise)

    result = _invoke_vibe_check(cli_runner, vibe_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("vibe_check_emit_findings_failed:")]
    assert emit_markers, f"expected vibe_check_emit_findings_failed: marker; got {all_wo!r}"
    # The vibe-check command still emits a clean envelope past the
    # registry-mirror failure -- W125 is additive, not load-bearing.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert data["summary"].get("partial_success") is True
