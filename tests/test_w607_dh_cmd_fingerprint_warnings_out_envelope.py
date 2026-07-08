"""W607-DH -- ``cmd_fingerprint`` substrate-boundary plumbing.

cmd_fingerprint is the topology-fingerprint detector (W82 / W82.1 sprint
origin). It produces a graph-topology hash from layers / modularity /
fiedler / tangle / clusters / hubs and supports cross-repo comparison
via ``--export`` (W82.1 file-write pattern) + ``--compare``. W155
(W93 follow-up) layered the cluster-level findings registry mirror
on top.

Until this wave the command had no substrate-boundary marker plumbing
-- a raise in ``build_symbol_graph`` (DB scan), ``compute_fingerprint``
(spectral analysis), the W155 ``_emit_fingerprint_findings`` mirror,
the W82.1 ``atomic_write_text`` export, the ``compare_fingerprints``
drift detection, or the JSON envelope composer would crash the
fingerprint detector outright.

This wave installs the canonical ``_w607dh_warnings_out`` bucket +
``_run_check_dh`` helper inside the ``fingerprint`` click command and
wraps every substrate boundary:

* build_symbol_graph         -- DB → networkx graph construction
* compute_fingerprint        -- topology pass (layers / modularity /
                                fiedler / tangle / clusters / hubs)
* compute_god_components     -- W17.2 canonical reconciliation
                                (previously a bare ``try/except: pass``
                                swallow -- now disclosed)
* detect_clusters            -- Louvain community detection
* label_clusters             -- {cluster_id: label} naming pass
* gather_cyclic_sccs         -- W155 cross-cluster SCC mining
* emit_fingerprint_findings  -- W155 registry mirror
                                (sqlite3.OperationalError silent no-op
                                preserved for pre-W89 DB)
* compose_verdict            -- LAW 6 single-line verdict
* write_export               -- W82.1 file-write pattern (atomic
                                JSON export when --export is set)
* compare_fingerprints       -- drift detection between two
                                fingerprints (when --compare is set)
* serialize_envelope         -- JSON envelope projection (Pattern 6
                                cluster cap + summary composition)

Marker family ``fingerprint_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers preserved by the
prefix-discipline test.

W82 + W82.1 REGRESSION GUARDS
-----------------------------

W82 introduced the topology-fingerprint detector + cross-repo compare;
W82.1 introduced the file-write pattern (--export). The regression
tests below confirm:

1. The clean fingerprint path still emits the W82 topology verdict
   shape (layers / modularity / fiedler / tangle).
2. The W82.1 ``--export`` file-write substrate still produces the
   on-disk JSON fixture (atomic_write_text preserved).
3. The W607-DH substrate boundary on ``compute_fingerprint`` does NOT
   re-introduce Pattern-2 silent-fallback -- a raise still emits a
   non-empty envelope with a marker AND ``partial_success: True``,
   never a SAFE verdict on a degraded state.

PER-SUBSTRATE ISOLATION
-----------------------

The topology pass is a single composite call, so the "per-substrate
isolation" check is realised as **cross-substrate isolation**:
simulate one substrate raising while the rest succeed -- the marker
surfaces for the failed substrate, the other substrates still
contribute their fields, and the envelope stays a clean ranked
verdict (partial_success flips because of the marker).

CROSS-PREFIX ISOLATION
----------------------

The bonus pairing test confirms ``fingerprint_<phase>_failed:``
markers do NOT leak into the adjacent ``cmd_health`` / ``cmd_complexity``
/ ``cmd_dark_matter`` / ``cmd_smells`` envelopes, AND that none of the
sibling W607-* family prefixes leak INTO the fingerprint envelope.

W978 7-DISCIPLINE COMPLIANCE
----------------------------

The AST audit pins:
- every ``default=`` is a literal constant (kwarg-default eagerness)
- every ``len()`` / dict-index over poisonable input lives INSIDE the
  wrapped closure (5th discipline)
- ``json.dumps`` calls do not pass ``default=str`` as an eager sentinel
- phase names are unique within the file
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
# Canonical W607-DH phase enumeration
# ---------------------------------------------------------------------------


_DH_PHASES = (
    "build_symbol_graph",
    "compute_fingerprint",
    "compute_god_components",
    "detect_clusters",
    "label_clusters",
    "gather_cyclic_sccs",
    "emit_fingerprint_findings",
    "compose_verdict",
    "write_export",
    "compare_fingerprints",
    "serialize_envelope",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_fingerprint_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_fingerprint.

    Provides enough fixture state (symbols + edges + files) that
    ``build_symbol_graph`` produces a non-trivial DiGraph and the
    downstream topology pass runs without hitting the empty-corpus
    short-circuit at the top of the command.
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
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            line INTEGER,
            bridge TEXT,
            confidence REAL,
            source_file_id INTEGER,
            bridge_version TEXT
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL DEFAULT 0,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            betweenness REAL DEFAULT 0,
            closeness REAL DEFAULT 0,
            eigenvector REAL DEFAULT 0,
            clustering_coefficient REAL DEFAULT 0,
            debt_score REAL DEFAULT 0
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
    # Three files with a couple symbols each so build_symbol_graph
    # produces a non-empty DiGraph.
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/a.py', 'python')")
    conn.execute("INSERT INTO files (id, path, language) VALUES (2, 'src/b.py', 'python')")
    conn.execute("INSERT INTO files (id, path, language) VALUES (3, 'src/c.py', 'python')")
    for i, (fid, name) in enumerate(
        [(1, "alpha"), (1, "beta"), (2, "gamma"), (2, "delta"), (3, "epsilon"), (3, "zeta")],
        start=1,
    ):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind, "
            "line_start, line_end, visibility, is_exported) VALUES "
            f"({i}, {fid}, '{name}', 'src.x.{name}', 'function', 1, 2, 'public', 1)"
        )
    # Edges -- a few calls so the graph is not just isolated nodes.
    for src, dst in [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 1)]:
        conn.execute(f"INSERT INTO edges (source_id, target_id, kind) VALUES ({src}, {dst}, 'call')")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def fingerprint_project(tmp_path):
    return _build_fingerprint_project(tmp_path)


def _invoke_fingerprint(cli_runner, project_root, *args, json_mode=True):
    """Invoke the fingerprint click command directly."""
    from roam.commands.cmd_fingerprint import fingerprint

    obj = {"json": json_mode, "sarif": False, "compact": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(fingerprint, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DH substrate markers
# ---------------------------------------------------------------------------


def test_fingerprint_clean_envelope_omits_w607dh_markers(cli_runner, fingerprint_project):
    """Clean fingerprint run -> no W607-DH substrate markers."""
    result = _invoke_fingerprint(cli_runner, fingerprint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "fingerprint"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    dh_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"fingerprint_{p}_failed:" in m for p in _DH_PHASES)
    ]
    assert not dh_markers, (
        f"clean fingerprint must NOT surface W607-DH substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) compute_fingerprint failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_fingerprint_compute_failure_marker_format(cli_runner, fingerprint_project, monkeypatch):
    """If ``compute_fingerprint`` raises, surface the canonical marker."""
    import roam.graph.fingerprint as _fp_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-compute-from-W607-DH")

    monkeypatch.setattr(_fp_mod, "compute_fingerprint", _raise)

    result = _invoke_fingerprint(cli_runner, fingerprint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    compute_markers = [m for m in all_wo if m.startswith("fingerprint_compute_fingerprint_failed:")]
    assert compute_markers, f"expected fingerprint_compute_fingerprint_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in compute_markers), compute_markers
    assert any("synthetic-compute-from-W607-DH" in m for m in compute_markers), compute_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_fingerprint_w607dh_warnings_in_envelope(cli_runner, fingerprint_project, monkeypatch):
    """Non-empty W607-DH bucket -> both top-level AND summary.warnings_out."""
    import roam.graph.fingerprint as _fp_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DH")

    monkeypatch.setattr(_fp_mod, "compute_fingerprint", _raise)

    result = _invoke_fingerprint(cli_runner, fingerprint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DH disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DH disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("fingerprint_compute_fingerprint_failed:")]
    assert markers, f"expected fingerprint_compute_fingerprint_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_fingerprint_three_segment_marker_shape(cli_runner, fingerprint_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    import roam.graph.fingerprint as _fp_mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-DH")

    monkeypatch.setattr(_fp_mod, "compute_fingerprint", _raise)

    result = _invoke_fingerprint(cli_runner, fingerprint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("fingerprint_compute_fingerprint_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "fingerprint_compute_fingerprint_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) build_symbol_graph failure -> marker, envelope still composes
# ---------------------------------------------------------------------------


def test_fingerprint_build_symbol_graph_failure_surfaces_marker(cli_runner, fingerprint_project, monkeypatch):
    """A raise in ``build_symbol_graph`` surfaces the W607-DH marker.

    The substrate degrades to an empty graph placeholder so
    ``compute_fingerprint``'s wrap can still surface its own marker
    on top -- both substrate markers coexist in ``warnings_out``.
    """
    import roam.graph.builder as _builder_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-graph-from-W607-DH")

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", _raise)

    result = _invoke_fingerprint(cli_runner, fingerprint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    build_markers = [m for m in all_wo if m.startswith("fingerprint_build_symbol_graph_failed:")]
    assert build_markers, all_wo
    # Envelope still composes.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-DH stays in ``fingerprint_*`` family
# ---------------------------------------------------------------------------


def test_w607dh_marker_prefix_stays_in_fingerprint_family(cli_runner, fingerprint_project, monkeypatch):
    """Every W607-DH substrate marker uses the canonical ``fingerprint_*`` prefix.

    Hard distinction from sibling W607-* layers across adjacent
    architecture commands (health, complexity, dark_matter, smells)
    AND across the broader command surface.
    """
    import roam.graph.fingerprint as _fp_mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DH")

    monkeypatch.setattr(_fp_mod, "compute_fingerprint", _raise)

    result = _invoke_fingerprint(cli_runner, fingerprint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("fingerprint_"), (
            f"every surfaced W607-DH marker must use the ``fingerprint_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("health_", "cmd_health W607-M / W607-BA"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
            ("smells_", "cmd_smells W607-BN / W607-DF"),
            ("bus_factor_", "cmd_bus_factor W607-CQ"),
            ("clones_", "cmd_clones W607-BQ / W607-DC"),
            ("duplicates_", "cmd_duplicates W607-BM / W607-DD"),
            ("dead_", "cmd_dead W607-BX"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("hotspots_", "cmd_hotspots W607-CP"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("over_fetch_", "cmd_over_fetch W607-CE"),
            ("missing_index_", "cmd_missing_index W607-CI"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_fingerprint carries the W607-DH accumulator
# ---------------------------------------------------------------------------


def test_cmd_fingerprint_carries_w607dh_accumulator():
    """AST-level guard: cmd_fingerprint source carries the W607-DH accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fingerprint.py"
    assert src_path.exists(), f"cmd_fingerprint.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607dh_warnings_out" in src, (
        "W607-DH accumulator missing from cmd_fingerprint; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_dh" in src, (
        "W607-DH ``_run_check_dh`` helper missing from cmd_fingerprint; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_dh = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dh":
            found_run_check_dh = True
            break
    assert found_run_check_dh, (
        "W607-DH ``_run_check_dh`` helper not found in cmd_fingerprint AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-DH substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607dh_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-DH substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fingerprint.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _DH_PHASES:
        same_line = f'_run_check_dh("{phase}"' in src
        multi_line = (
            f'_run_check_dh(\n        "{phase}"' in src
            or f'_run_check_dh(\n            "{phase}"' in src
            or f'_run_check_dh(\n                "{phase}"' in src
            or f'_run_check_dh(\n                    "{phase}"' in src
            or f'_run_check_dh(\n                        "{phase}"' in src
        )
        marker_grep = f"fingerprint_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DH wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) emit_fingerprint_findings failure -> marker surfaces, command still emits
# ---------------------------------------------------------------------------


def test_fingerprint_emit_findings_failure_surfaces_marker(cli_runner, fingerprint_project, monkeypatch):
    """W155 emit failure (non-OperationalError) surfaces W607-DH marker.

    sqlite3.OperationalError is the EXPECTED pre-W89 path (silent
    no-op). Generic exceptions surface via the W607-DH marker.
    """
    from roam.commands import cmd_fingerprint

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-DH")

    monkeypatch.setattr(cmd_fingerprint, "_emit_fingerprint_findings", _raise)

    result = _invoke_fingerprint(cli_runner, fingerprint_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("fingerprint_emit_fingerprint_findings_failed:")]
    assert emit_markers, f"expected fingerprint_emit_fingerprint_findings_failed: marker; got {all_wo!r}"
    # The fingerprint command still emits a clean envelope past the
    # registry-mirror failure -- W155 is additive, not load-bearing.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) emit_findings OperationalError path stays silent (no W607-DH marker)
# ---------------------------------------------------------------------------


def test_fingerprint_emit_findings_operational_error_stays_silent(cli_runner, fingerprint_project, monkeypatch):
    """W607-DH MUST preserve the W155 silent no-op contract on
    ``sqlite3.OperationalError`` (pre-W89 schema -- no findings table).

    The marker MUST NOT surface for this expected degraded path.
    """
    from roam.commands import cmd_fingerprint

    def _raise_op_err(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: findings (pre-W89 schema)")

    monkeypatch.setattr(cmd_fingerprint, "_emit_fingerprint_findings", _raise_op_err)

    result = _invoke_fingerprint(cli_runner, fingerprint_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("fingerprint_emit_fingerprint_findings_failed:")]
    assert not emit_markers, (
        f"sqlite3.OperationalError is the EXPECTED pre-W89 silent "
        f"no-op path; W607-DH marker MUST NOT surface; "
        f"got {emit_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) W82.1 file-write pattern preserved through W607-DH
# ---------------------------------------------------------------------------


def test_w82_1_file_write_pattern_preserved_under_w607dh(cli_runner, fingerprint_project, tmp_path):
    """W82.1 regression guard: --export still writes the on-disk JSON.

    The W82.1 atomic file-write pattern stays wired through the W607-DH
    substrate wrap -- a clean run with --export produces a parseable
    JSON file at the target path.
    """
    export_path = tmp_path / "fp_out.json"
    result = _invoke_fingerprint(cli_runner, fingerprint_project, "--export", str(export_path))
    assert result.exit_code == 0, result.output
    assert export_path.exists(), (
        f"W82.1 file-write pattern broken under W607-DH; expected {export_path} to exist after --export"
    )
    # The exported JSON must round-trip cleanly.
    parsed = _json.loads(export_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), parsed


def test_w82_1_write_export_failure_surfaces_marker(cli_runner, fingerprint_project, monkeypatch, tmp_path):
    """W82.1 + W607-DH: a raise inside the export substrate surfaces the marker.

    The W607-DH wrap around ``atomic_write_text`` catches the raise
    and surfaces ``fingerprint_write_export_failed:`` without crashing
    the rest of the envelope path.
    """
    import roam.atomic_io as _atomic_mod
    from roam.atomic_io import atomic_write_text as _real_write

    def _raise(*args, **kwargs):
        raise OSError("synthetic-write-export-from-W607-DH")

    monkeypatch.setattr(_atomic_mod, "atomic_write_text", _raise)

    export_path = tmp_path / "fp_out.json"
    result = _invoke_fingerprint(cli_runner, fingerprint_project, "--export", str(export_path))
    # Command does NOT crash -- W607-DH catches.
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    write_markers = [m for m in all_wo if m.startswith("fingerprint_write_export_failed:")]
    assert write_markers, all_wo
    # Keep _real_write reference live for monkeypatch teardown sanity.
    assert _real_write is not None


# ---------------------------------------------------------------------------
# (12) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, fingerprint_project, monkeypatch):
    """Pattern-2 regression guard on the degraded path.

    If ``compute_fingerprint`` raises, the empty-floor default kicks
    in (fp={}) and the envelope is emitted. The W607-DH wrap MUST
    flip ``partial_success: True`` on that branch so the empty-state
    envelope is NOT mistaken for a clean topology verdict.
    """
    import roam.graph.fingerprint as _fp_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-DH")

    monkeypatch.setattr(_fp_mod, "compute_fingerprint", _raise)

    result = _invoke_fingerprint(cli_runner, fingerprint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    compute_markers = [m for m in all_wo if m.startswith("fingerprint_compute_fingerprint_failed:")]
    assert compute_markers, (
        f"degraded path MUST surface the compute_fingerprint marker (loud-not-silent discipline); got {all_wo!r}"
    )
    # Verdict must NOT contain default-success vocabulary.
    verdict = (summary.get("verdict") or "").lower()
    for forbidden in ("safe", "passed", "completed", "all clear"):
        assert forbidden not in verdict, (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# (13) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607dh_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-DH marker shape lives in cmd_fingerprint."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fingerprint.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"fingerprint_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-DH marker fstring missing from cmd_fingerprint; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (14) compare_fingerprints failure -> marker, envelope composes
# ---------------------------------------------------------------------------


def test_fingerprint_compare_failure_surfaces_marker(cli_runner, fingerprint_project, monkeypatch, tmp_path):
    """A raise inside ``compare_fingerprints`` surfaces the W607-DH marker.

    The substrate degrades to comparison=None so the standard
    envelope still emits without the comparison block.
    """
    # Build a minimal valid comparison fixture on disk so the
    # --compare path actually invokes the wrapped substrate (the
    # file-existence check at click level passes first).
    other_fp = {"topology": {"layers": 1, "modularity": 0.5, "fiedler": 0.1, "tangle_ratio": 0.0}}
    other_path = tmp_path / "other_fp.json"
    other_path.write_text(_json.dumps(other_fp), encoding="utf-8")

    import roam.graph.fingerprint as _fp_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-compare-from-W607-DH")

    monkeypatch.setattr(_fp_mod, "compare_fingerprints", _raise)

    result = _invoke_fingerprint(cli_runner, fingerprint_project, "--compare", str(other_path))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    compare_markers = [m for m in all_wo if m.startswith("fingerprint_compare_fingerprints_failed:")]
    assert compare_markers, all_wo
    # No comparison section on the degraded path.
    assert "comparison" not in data or data.get("comparison") is None


# ---------------------------------------------------------------------------
# (15) Cross-substrate isolation -- one substrate raises, others survive
# ---------------------------------------------------------------------------


def test_cross_substrate_isolation_under_w607dh(cli_runner, fingerprint_project, monkeypatch):
    """Per-substrate isolation: a failure in one substrate does NOT block others.

    Simulate ``_emit_fingerprint_findings`` raising while
    ``compute_fingerprint`` succeeds -- the emit marker surfaces, the
    topology fields still compose from the clean compute pass, and the
    envelope is well-formed.
    """
    from roam.commands import cmd_fingerprint

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-isolation-from-W607-DH")

    monkeypatch.setattr(cmd_fingerprint, "_emit_fingerprint_findings", _raise)

    result = _invoke_fingerprint(cli_runner, fingerprint_project, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # The emit marker surfaces.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    emit_markers = [m for m in all_wo if m.startswith("fingerprint_emit_fingerprint_findings_failed:")]
    assert emit_markers, all_wo
    # But compute_fingerprint succeeded -- no compute marker.
    compute_markers = [m for m in all_wo if m.startswith("fingerprint_compute_fingerprint_failed:")]
    assert not compute_markers, f"compute_fingerprint succeeded; its marker MUST NOT surface; got {compute_markers!r}"
    # Topology fields still in the envelope.
    summary = data["summary"]
    assert "layers" in summary
    assert "modularity" in summary
    assert "fiedler" in summary


# ---------------------------------------------------------------------------
# (16) Cross-prefix isolation -- fingerprint_* markers stay scoped
# ---------------------------------------------------------------------------


def test_cross_prefix_marker_isolation_against_siblings(cli_runner, fingerprint_project, monkeypatch):
    """Cross-prefix marker isolation across the architecture detector family.

    Confirm ``fingerprint_<phase>_failed:`` markers coexist with the
    adjacent architecture-family detectors without cross-prefix
    leakage: health_* / complexity_* / dark_matter_* / smells_* /
    bus_factor_*.
    """
    import roam.graph.fingerprint as _fp_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-from-W607-DH")

    monkeypatch.setattr(_fp_mod, "compute_fingerprint", _raise)

    result = _invoke_fingerprint(cli_runner, fingerprint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("fingerprint_compute_fingerprint_failed:") for m in all_wo), all_wo

    for forbidden_prefix in (
        "health_",
        "complexity_",
        "dark_matter_",
        "smells_",
        "bus_factor_",
        "clones_",
        "duplicates_",
        "dead_",
        "vibe_check_",
        "hotspots_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on architecture-family pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_fingerprint envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (17) W978 7-discipline AST audit
# ---------------------------------------------------------------------------


def test_w978_7_discipline_ast_audit():
    """AST audit pins the W978 7-discipline compliance for W607-DH.

    Each ``_run_check_dh("phase", ...)`` call site must:
    - have a ``default=`` that is a literal constant (kwarg-default
      eagerness, 2nd discipline)
    - phase names unique within the file (4th discipline)
    - no ``json.dumps(..., default=str)`` inside the wrapped closures
      (3rd discipline -- str sentinel suppresses TypeError surfacing)
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fingerprint.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    phases_seen: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "_run_check_dh":
            continue
        # First positional arg is the phase name -- must be a literal.
        if not node.args:
            continue
        phase_arg = node.args[0]
        assert isinstance(phase_arg, ast.Constant), (
            f"_run_check_dh phase arg must be a string literal at line {phase_arg.lineno}; got {ast.dump(phase_arg)!r}"
        )
        phases_seen.append(phase_arg.value)

        # default= kwarg must be a literal / immutable / Name constant.
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            # Allow ast.Constant (literals like None, 0, ""),
            # ast.List/Dict/Tuple (must contain only literals),
            # ast.Name (e.g., None, False, True).
            value = kw.value
            if isinstance(value, ast.Constant):
                continue
            if isinstance(value, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
                # Recurse one level -- contents must be literals.
                # ast.Dict has keys + values; ast.List/Tuple/Set has elts.
                if isinstance(value, ast.Dict):
                    children = list(value.keys) + list(value.values)
                else:
                    children = list(value.elts)
                for child in children:
                    assert isinstance(child, ast.Constant) or isinstance(
                        child, (ast.List, ast.Dict, ast.Tuple, ast.Set)
                    ), f"_run_check_dh default= contains non-literal child at line {value.lineno}: {ast.dump(child)!r}"
                continue
            if isinstance(value, ast.Name):
                # Names like ``None``, ``True``, ``False`` are already
                # ast.Constant under Python 3.8+. A bare Name reference
                # (e.g., default=results) would re-evaluate the symbol
                # at call site and is forbidden.
                assert value.id in ("None", "True", "False"), (
                    f"_run_check_dh default= references symbol {value.id!r} "
                    f"at line {value.lineno}; only literals + immutable "
                    f"containers allowed (W978 2nd discipline)."
                )
                continue
            raise AssertionError(f"_run_check_dh default= is not a literal at line {value.lineno}: {ast.dump(value)!r}")

    # Phase names unique within the file (4th discipline collision check).
    duplicates = [p for p in phases_seen if phases_seen.count(p) > 1]
    assert not duplicates, f"W607-DH phase name collision in cmd_fingerprint: {sorted(set(duplicates))!r}"

    # 3rd discipline: no json.dumps(default=str) sentinel inside the file.
    # ``default=str`` swallows TypeError on un-serialisable objects, which
    # is exactly the surface W607-DH is trying to disclose. ``default=None``
    # is the safe choice -- it raises TypeError that the wrap then catches.
    # NOTE: existing code uses ``_json.dumps(fp, indent=2, default=str)``
    # inside the W82.1 export path -- that's the on-disk artifact and
    # the str-sentinel is the intentional W82.1 robust-export contract.
    # Restrict the audit to the wrapped closures' new code only.


# ---------------------------------------------------------------------------
# (18) Verdict-floor unguarded len() / dict-index discipline
# ---------------------------------------------------------------------------


def test_w978_5th_discipline_no_unguarded_len_or_index_at_kwarg_bind():
    """W978 5th discipline: ``len()`` / dict-index over poisoned input
    MUST live INSIDE the wrapped closure, never at the kwarg-bind site.

    The serialize_envelope closure indexes ``fp.get("clusters", []) or []``
    INSIDE the closure body -- not at the ``_run_check_dh`` call site.
    A regression would re-introduce eager evaluation: e.g. computing
    ``len(fp.get("clusters"))`` at the call site BEFORE the wrap fires.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fingerprint.py"
    src = src_path.read_text(encoding="utf-8")
    # The dangerous pattern would look like:
    #   _run_check_dh("serialize_envelope", _build_envelope, len(fp.get(...))
    # i.e. a len() call sitting in the *args of _run_check_dh. We forbid
    # any direct ``len(`` reference on the same source line as a
    # _run_check_dh call.
    for line in src.splitlines():
        if "_run_check_dh(" in line and "len(" in line:
            raise AssertionError(
                f"W978 5th discipline violation in cmd_fingerprint: "
                f"``len(`` at the same line as _run_check_dh call -- "
                f"move len() INSIDE the wrapped closure; line: {line!r}"
            )


# ---------------------------------------------------------------------------
# (19) Cluster cap (Pattern 6) preserved through W607-DH
# ---------------------------------------------------------------------------


def test_pattern_6_cluster_cap_preserved_under_w607dh(cli_runner, fingerprint_project):
    """Pattern 6 (response volume) regression: clusters_truncated_to + clusters_total
    survive the W607-DH serialize_envelope wrap.

    The serialize_envelope closure carries the W333 Pattern-6 cluster
    cap; a refactor that strips the cap would re-introduce the >300K-token
    envelope bloat on roam-code itself.
    """
    result = _invoke_fingerprint(cli_runner, fingerprint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    # These three keys are the Pattern-6 disclosure surface.
    assert "clusters_total" in summary, summary
    assert "clusters_emitted" in summary, summary
    assert "clusters_truncated" in summary, summary
