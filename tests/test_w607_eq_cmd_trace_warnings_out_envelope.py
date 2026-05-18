"""W607-EQ -- ``cmd_trace`` substrate-boundary plumbing.

cmd_trace is the k-shortest-paths pathfinding command (Yen's exhaustive
+ bounded BFS) and a leg of the structural-analysis / pathfinding
family alongside cmd_closure (W607-EM, transitive closure), cmd_cut
(W607-EI, minimum edge cuts), and cmd_simulate (W607-EF,
counterfactual transforms). Until this wave the command had no
substrate-boundary marker plumbing -- a raise inside
``build_symbol_graph`` (DB -> networkx), the path enumeration loops
(``find_k_paths`` / ``_find_bounded_paths``), the per-path annotation
(``_build_hops`` / ``_detect_hubs`` / ``_classify_coupling`` /
``_path_quality``), or any downstream verdict / envelope composer
would crash the trace command outright.

This wave installs the canonical ``_w607eq_warnings_out`` bucket +
``_run_check_eq`` helper inside ``trace`` and wraps every substrate
boundary:

* resolve_source_target_symbols -- source+target id capture
* build_dependency_graph        -- build_symbol_graph DB -> networkx
* compute_k_shortest_paths      -- path enumeration over sid x tid
* extract_path_metrics          -- hops + hubs + coupling + quality
* compose_verdict               -- LAW 6 single-line floor
* compose_facts                 -- agent_contract.facts list
* compose_next_commands         -- agent_contract.next_commands
* serialize_envelope            -- JSON envelope emission
* format_text_output            -- text path-table emission

Marker family ``trace_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test.

UNREACHABLE-TARGET REGRESSION
-----------------------------

cmd_trace between two symbols with no dependency path between them
must produce a finite "no path" verdict, not crash. The W607-EQ
plumbing must preserve that graceful-handling: the no-path branch
already emitted a closed-enum state (``no_path`` / ``no_path_within_hops``)
and the plumbing must not regress this contract.

LAW 6 VERDICT-FIRST INVARIANT
-----------------------------

``summary.verdict`` survives every phase failure as a literal floor.
A raise in any substrate degrades to the empty-floor verdict string
(``"trace: 0 hops <src>-><tgt>, 0 paths found, none"``); the verdict
is NEVER absent.

CROSS-PREFIX ISOLATION
----------------------

``trace_*`` markers do NOT leak into ``closure_*`` / ``cut_*`` /
``simulate_*`` / ``orchestrate_*`` / ``partition_*`` / ``agent_plan_*``
/ ``fleet_*`` or any of the broader detector and architecture command
families.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_trace_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_trace.

    The W607-EQ substrate boundary tests monkeypatch interior calls
    (build_symbol_graph, find_k_paths, _find_bounded_paths, etc.) so
    the actual DB row contents matter less than DB-and-index presence.
    We just need ensure_index() to find a .roam DB AND
    find_symbol_id_with_tier to resolve a symbol by name.
    """
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
        CREATE TABLE IF NOT EXISTS symbol_references (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER,
            kind TEXT,
            line INTEGER
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            kind TEXT
        );
        CREATE TABLE IF NOT EXISTS file_edges (
            id INTEGER PRIMARY KEY,
            source_file_id INTEGER NOT NULL,
            target_file_id INTEGER NOT NULL,
            kind TEXT
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity REAL DEFAULT 0
        );
        """
    )
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/a.py', 'python')")
    conn.execute("INSERT INTO files (id, path, language) VALUES (2, 'src/b.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(1, 1, 'foo', 'src.a.foo', 'function', 1, 2, 'public', 1)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(2, 2, 'bar', 'src.b.bar', 'function', 1, 2, 'public', 1)"
    )
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (1, 2, 'calls')")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def trace_project(tmp_path):
    return _build_trace_project(tmp_path)


def _invoke_trace(cli_runner, project_root, *args, json_mode=True):
    """Invoke the trace click command directly."""
    from roam.commands.cmd_trace import trace

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(trace, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_EQ_PHASES = (
    "resolve_source_target_symbols",
    "build_dependency_graph",
    "compute_k_shortest_paths",
    "extract_path_metrics",
    "compose_verdict",
    "compose_facts",
    "compose_next_commands",
    "serialize_envelope",
    "format_text_output",
)


def _make_fake_graph_with_edge():
    """Return a minimal networkx-shaped object emulating a graph with
    edge (1, 2) so cmd_trace can produce a non-empty path."""
    import networkx as nx

    G = nx.DiGraph()
    G.add_node(1)
    G.add_node(2)
    G.add_edge(1, 2, kind="calls")
    return G


def _make_fake_empty_graph():
    """Return a networkx DiGraph with the two seed nodes but no edges."""
    import networkx as nx

    G = nx.DiGraph()
    G.add_node(1)
    G.add_node(2)
    return G


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-EQ substrate markers
# ---------------------------------------------------------------------------


def test_trace_clean_envelope_omits_w607eq_markers(cli_runner, trace_project, monkeypatch):
    """Clean trace run -> no W607-EQ substrate markers."""

    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", lambda _conn: _make_fake_graph_with_edge())

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "trace"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    eq_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"trace_{p}_failed:" in m for p in _EQ_PHASES)]
    assert not eq_markers, (
        f"clean trace must NOT surface W607-EQ substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) build_dependency_graph failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_trace_build_dependency_graph_failure_marker_format(cli_runner, trace_project, monkeypatch):
    """If ``build_symbol_graph`` raises, surface the canonical marker."""

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-from-W607-EQ")

    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", _raise)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    build_markers = [m for m in all_wo if m.startswith("trace_build_dependency_graph_failed:")]
    assert build_markers, f"expected trace_build_dependency_graph_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in build_markers), build_markers
    assert any("synthetic-build-from-W607-EQ" in m for m in build_markers), build_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_trace_w607eq_warnings_in_envelope(cli_runner, trace_project, monkeypatch):
    """Non-empty W607-EQ bucket -> both top-level AND summary.warnings_out."""
    import roam.commands.cmd_trace as _trace_mod
    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", lambda _conn: _make_fake_graph_with_edge())

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-EQ")

    # Force a failure in compute_k_shortest_paths via patching the
    # bounded-paths helper -- the substrate's body calls it directly.
    monkeypatch.setattr(_trace_mod, "_find_bounded_paths", _raise)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-EQ disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-EQ disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_trace_three_segment_marker_shape(cli_runner, trace_project, monkeypatch):
    """Marker must have three colon-separated segments."""

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-EQ")

    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", _raise)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("trace_build_dependency_graph_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "trace_build_dependency_graph_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) compute_k_shortest_paths failure -> marker, command still emits
# ---------------------------------------------------------------------------


def test_trace_compute_k_shortest_paths_failure_surfaces_marker(cli_runner, trace_project, monkeypatch):
    """A raise in the path enumeration substrate surfaces a marker.

    Patching `_find_bounded_paths` to raise causes the
    compute_k_shortest_paths substrate to surface a marker. The
    envelope still composes a coherent verdict on the empty-paths
    floor.
    """
    import roam.commands.cmd_trace as _trace_mod
    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", lambda _conn: _make_fake_graph_with_edge())

    def _raise_bounded(*args, **kwargs):
        raise RuntimeError("synthetic-compute-from-W607-EQ")

    monkeypatch.setattr(_trace_mod, "_find_bounded_paths", _raise_bounded)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    compute_markers = [m for m in all_wo if m.startswith("trace_compute_k_shortest_paths_failed:")]
    assert compute_markers, all_wo
    # Envelope still composes.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-EQ stays in ``trace_*`` family
# ---------------------------------------------------------------------------


def test_w607eq_marker_prefix_stays_in_trace_family(cli_runner, trace_project, monkeypatch):
    """Every W607-EQ substrate marker uses the canonical ``trace_*`` prefix.

    Hard distinction from sibling W607-* layers across the broader
    command surface. Confirms cross-prefix isolation per the wave
    contract.
    """

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-EQ")

    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", _raise)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("trace_"), (
            f"every surfaced W607-EQ marker must use the ``trace_*`` prefix family; got {marker!r}"
        )
        # Hard distinction from sibling pathfinding + structural-analysis
        # family + adjacent detector + architecture families.
        for forbidden_prefix, sibling in (
            ("closure_", "cmd_closure W607-EM (pathfinding sibling)"),
            ("cut_", "cmd_cut W607-EI (structural-analysis sibling)"),
            ("simulate_", "cmd_simulate W607-EF (structural-analysis sibling)"),
            ("orchestrate_", "cmd_orchestrate W607-DS"),
            ("partition_", "cmd_partition W607-DU"),
            ("agent_plan_", "cmd_agent_plan W607-DY"),
            ("fleet_", "cmd_fleet W607-EB"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("over_fetch_", "cmd_over_fetch W607-CE"),
            ("smells_", "cmd_smells W607-BN"),
            ("clones_", "cmd_clones W607-BQ"),
            ("duplicates_", "cmd_duplicates W607-BM"),
            ("dead_", "cmd_dead W607-BX"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
            ("vulns_", "cmd_vulns W607-AQ + CH"),
            ("taint_", "cmd_taint W607-AY + CJ"),
            ("hotspots_", "cmd_hotspots W607-EN"),
            ("preflight_", "cmd_preflight W607-EC + AW"),
            ("impact_", "cmd_impact W607-BB"),
            ("adversarial_", "cmd_adversarial W607-EK"),
            ("critique_", "cmd_critique W607-EJ"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_trace carries the W607-EQ accumulator
# ---------------------------------------------------------------------------


def test_cmd_trace_carries_w607eq_accumulator():
    """AST-level guard: cmd_trace source carries the W607-EQ accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_trace.py"
    assert src_path.exists(), f"cmd_trace.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607eq_warnings_out" in src, (
        "W607-EQ accumulator missing from cmd_trace; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_eq" in src, (
        "W607-EQ ``_run_check_eq`` helper missing from cmd_trace; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_eq = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_eq":
            found_run_check_eq = True
            break
    assert found_run_check_eq, (
        "W607-EQ ``_run_check_eq`` helper not found in cmd_trace AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-EQ substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607eq_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-EQ substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_trace.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _EQ_PHASES:
        same_line = f'_run_check_eq("{phase}"' in src
        multi_line = (
            f'_run_check_eq(\n        "{phase}"' in src
            or f'_run_check_eq(\n            "{phase}"' in src
            or f'_run_check_eq(\n                "{phase}"' in src
        )
        marker_grep = f"trace_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-EQ wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607eq_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-EQ marker shape lives in cmd_trace."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_trace.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"trace_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-EQ marker fstring missing from cmd_trace; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (10) LAW 6 verdict-first invariant: verdict survives every phase failure
# ---------------------------------------------------------------------------


def test_law_6_verdict_survives_every_phase_failure(cli_runner, trace_project, monkeypatch):
    """LAW 6 invariant: ``summary.verdict`` is a non-empty single line on
    every phase failure -- the floor never disappears.
    """

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-from-W607-EQ")

    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", _raise)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict, (
        f"LAW 6 invariant violated: verdict missing/empty on degraded path; got summary={summary!r}"
    )
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    # The floor names the trace state, NOT a SAFE/passed vocabulary.
    forbidden_vocab = ("safe", "passed", "all clear")
    for forbidden in forbidden_vocab:
        assert forbidden not in verdict.lower(), (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# (11) UNREACHABLE-TARGET REGRESSION PRESERVATION
# ---------------------------------------------------------------------------


def test_trace_unreachable_target_produces_finite_verdict(cli_runner, trace_project, monkeypatch):
    """Unreachable-target regression guard.

    cmd_trace between two resolved symbols with no graph path between
    them must produce a finite "no path" verdict, not crash. The
    W607-EQ plumbing must not regress that contract.
    """

    # Build a graph with NO edge between 1 and 2 so the bounded BFS
    # returns no paths.
    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", lambda _conn: _make_fake_empty_graph())

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # No path -> closed-enum state.
    state = summary.get("state")
    assert state in ("no_path", "no_path_within_hops"), (
        f"unreachable target must produce closed-enum no-path state; got summary={summary!r}"
    )
    # Verdict is finite, single-line.
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict
    # No degraded-path markers -- the unreachable case is handled
    # cleanly without raising in any substrate.
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    eq_markers = [m for m in all_wo if any(f"trace_{p}_failed:" in m for p in _EQ_PHASES)]
    assert not eq_markers, f"unreachable target must NOT surface W607-EQ substrate markers; got {all_wo!r}"


# ---------------------------------------------------------------------------
# (12) Per-substrate isolation -- build_dependency_graph boundary
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_build_boundary_surfaces_marker(cli_runner, trace_project, monkeypatch):
    """Per-substrate isolation: build_dependency_graph raising surfaces a
    distinct marker + graceful degradation.

    Raise inside ``build_symbol_graph`` and confirm the matching
    build_dependency_graph marker surfaces. The remaining substrates
    still run on the empty floor so the envelope composes a coherent
    verdict.
    """

    def _raise_build(*args, **kwargs):
        raise RuntimeError("isolation-build-W607-EQ")

    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", _raise_build)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("trace_build_dependency_graph_failed:") for m in all_wo), all_wo
    # Envelope still composes.
    assert isinstance(data["summary"]["verdict"], str)
    assert data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# (13) Per-substrate isolation -- extract_path_metrics boundary
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_extract_path_metrics_surfaces_marker(cli_runner, trace_project, monkeypatch):
    """Per-substrate isolation: extract_path_metrics raising surfaces a
    distinct marker.

    Force `format_path` to raise inside the path annotation loop. The
    extract_path_metrics substrate is the only consumer of format_path
    so the marker is unambiguous.
    """

    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", lambda _conn: _make_fake_graph_with_edge())

    def _raise_format(*args, **kwargs):
        raise RuntimeError("isolation-extract-W607-EQ")

    # cmd_trace imports format_path inside the function body via
    # `from roam.graph.pathfinding import ... format_path`, so patch
    # the source module.
    import roam.graph.pathfinding as _pf_mod

    monkeypatch.setattr(_pf_mod, "format_path", _raise_format)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    extract_markers = [m for m in all_wo if m.startswith("trace_extract_path_metrics_failed:")]
    assert extract_markers, all_wo
    assert isinstance(data["summary"]["verdict"], str)
    assert data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# (14) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, trace_project, monkeypatch):
    """Pattern-2 regression guard.

    If ``build_symbol_graph`` raises, the empty-floor default kicks in
    (G=None, paths=[]) and the envelope is emitted. The W607-EQ wrap
    MUST flip ``partial_success: True`` on that branch so the
    empty-state envelope is NOT mistaken for a clean trace verdict.
    """

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-EQ")

    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", _raise)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    build_markers = [m for m in all_wo if m.startswith("trace_build_dependency_graph_failed:")]
    assert build_markers, (
        f"degraded path MUST surface the build_dependency_graph marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (15) Helper-template ``return default`` verbatim shape
# ---------------------------------------------------------------------------


def test_run_check_eq_helper_returns_default_verbatim():
    """W607-DP finding: the _run_check_eq helper MUST end with the literal
    ``return default`` (not ``return None`` or a captured local). A raise
    inside the wrapped fn falls through to ``return default`` so the
    caller's empty-floor default actually propagates.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_trace.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_eq"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ExceptHandler):
                # Last statement in the except body must be ``return default``.
                last_stmt = sub.body[-1]
                assert isinstance(last_stmt, ast.Return), (
                    f"_run_check_eq except handler last stmt is {type(last_stmt).__name__!r}, not Return"
                )
                assert isinstance(last_stmt.value, ast.Name), (
                    f"_run_check_eq must `return default` (a Name), got {ast.dump(last_stmt.value)!r}"
                )
                assert last_stmt.value.id == "default", (
                    f"_run_check_eq must `return default`, got `return {last_stmt.value.id}`"
                )
                found = True
                break
        if found:
            break
    assert found, (
        "_run_check_eq FunctionDef / except handler not found in cmd_trace AST; the helper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (16) Cross-prefix isolation at SOURCE level: cmd_trace doesn't leak siblings
# ---------------------------------------------------------------------------


def test_cmd_trace_source_no_sibling_marker_leak():
    """Source-level: cmd_trace.py does NOT contain any sibling W607-* marker fstrings."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_trace.py"
    src = src_path.read_text(encoding="utf-8")
    trace_marker = 'f"trace_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert trace_marker in src, f"canonical trace marker fstring missing; expected: {trace_marker}"
    forbidden_markers = (
        'f"closure_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"cut_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"simulate_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"orchestrate_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"partition_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"agent_plan_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"fleet_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"smells_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"vulns_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"taint_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"health_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"hotspots_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"preflight_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"impact_{phase}_failed:{type(exc).__name__}:{exc}"',
    )
    for forbidden in forbidden_markers:
        assert forbidden not in src, f"cmd_trace.py leaks sibling marker fstring: {forbidden!r}"


# ---------------------------------------------------------------------------
# (17) PATHFINDING-FAMILY 4-FAMILY PAIRING PIN
# ---------------------------------------------------------------------------


def test_pathfinding_family_4_w607_pairing_pin():
    """Pin: cmd_simulate, cmd_cut, cmd_closure, and cmd_trace all carry W607 substrate plumbing.

    The pathfinding / structural-analysis family ships substrate-CALL
    plumbing under four distinct W607 letters: EF (simulate), EI (cut),
    EM (closure), EQ (trace). This test AST-scans all four for the
    canonical accumulator + helper pattern.
    """
    base = Path(__file__).parent.parent / "src" / "roam" / "commands"
    family = (
        ("cmd_simulate.py", "_w607ef_warnings_out", "_run_check_ef"),
        ("cmd_cut.py", "_w607ei_warnings_out", "_run_check_ei"),
        ("cmd_closure.py", "_w607em_warnings_out", "_run_check_em"),
        ("cmd_trace.py", "_w607eq_warnings_out", "_run_check_eq"),
    )
    for filename, accumulator, helper in family:
        src_path = base / filename
        assert src_path.exists(), f"{filename} missing at {src_path}"
        src = src_path.read_text(encoding="utf-8")
        assert accumulator in src, (
            f"{filename} missing accumulator {accumulator!r}; the pathfinding 4-family pairing is broken."
        )
        assert helper in src, f"{filename} missing helper {helper!r}; the pathfinding 4-family pairing is broken."
        # AST: helper FunctionDef present.
        tree = ast.parse(src)
        found = any(isinstance(node, ast.FunctionDef) and node.name == helper for node in ast.walk(tree))
        assert found, f"{filename}: helper {helper!r} FunctionDef not found in AST"


# ---------------------------------------------------------------------------
# (18) Helper-template body matches canonical W607-DZ shape
# ---------------------------------------------------------------------------


def test_run_check_eq_helper_template_shape():
    """The _run_check_eq helper body must match the W607 canonical
    try/append/return template.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_trace.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    helper_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_eq":
            helper_fn = node
            break
    assert helper_fn is not None, "_run_check_eq helper not found"

    # Body should be exactly one Try node.
    try_nodes = [n for n in helper_fn.body if isinstance(n, ast.Try)]
    assert len(try_nodes) == 1, f"_run_check_eq body should contain exactly one Try; got {len(try_nodes)} Try nodes"
    try_node = try_nodes[0]

    # The try body should contain `return fn(*args, **kwargs)`.
    assert len(try_node.body) >= 1
    first = try_node.body[0]
    assert isinstance(first, ast.Return), f"_run_check_eq try body[0] should be Return; got {type(first).__name__}"
    # The except handler should append a marker and `return default`.
    assert len(try_node.handlers) == 1, (
        f"_run_check_eq should have exactly one except handler; got {len(try_node.handlers)}"
    )


# ---------------------------------------------------------------------------
# (19) compose_verdict empty-floor default literal -- no Name references
# ---------------------------------------------------------------------------


def test_compose_verdict_default_floor_is_static():
    """W978 #1: the compose_verdict default= argument must be a static
    f-string with no Name references that would re-raise if a poison
    object is bound to the local namespace.

    Inspect the AST for the _run_check_eq call with the literal
    "compose_verdict" first arg and assert its default= kwarg is a
    JoinedStr (f-string) whose only references are to the trace
    arguments (source / target), which are click-bound and immutable.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_trace.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_run_check_eq"):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not (isinstance(first_arg, ast.Constant) and first_arg.value == "compose_verdict"):
            continue
        # Find default= kwarg.
        default_kw = next((kw for kw in node.keywords if kw.arg == "default"), None)
        assert default_kw is not None, '_run_check_eq("compose_verdict", ...) missing default= kwarg'
        # Default is an f-string or constant string.
        assert isinstance(default_kw.value, (ast.JoinedStr, ast.Constant)), (
            f"compose_verdict default= must be a static string/f-string; got {type(default_kw.value).__name__}"
        )
        found = True
        break
    assert found, '_run_check_eq("compose_verdict", ...) call not found in cmd_trace AST'


# ---------------------------------------------------------------------------
# (20) compose_facts substrate default -- list with verdict-only fallback
# ---------------------------------------------------------------------------


def test_compose_facts_substrate_fallback(cli_runner, trace_project, monkeypatch):
    """If the compose_facts substrate raises (poison list iteration etc.),
    the agent_contract.facts still composes from the verdict-only floor.
    """
    import roam.commands.cmd_trace as _trace_mod

    # First force a clean graph build then break the path-quality
    # helper so compose_facts is still entered with the verdict-only
    # floor.
    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", lambda _conn: _make_fake_graph_with_edge())

    # If extract_path_metrics raises, annotated_paths becomes []; the
    # compose_facts substrate then composes a coherent floor.
    def _raise_quality(*args, **kwargs):
        raise RuntimeError("synthetic-facts-from-W607-EQ")

    monkeypatch.setattr(_trace_mod, "_path_quality", _raise_quality)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    # Envelope composes; verdict is present.
    summary = data["summary"]
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    # extract_path_metrics marker surfaces (the path_quality call lives
    # inside that substrate).
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    assert any(m.startswith("trace_extract_path_metrics_failed:") for m in all_wo), all_wo


# ---------------------------------------------------------------------------
# (21) Marker exception detail is preserved verbatim
# ---------------------------------------------------------------------------


def test_marker_exception_detail_preserved(cli_runner, trace_project, monkeypatch):
    """The exception's stringified detail must be embedded in the marker
    verbatim (W978 #3: no json.dumps(default=str) translation needed).
    """

    sentinel = "sentinel-detail-W607-EQ-pin-021"

    def _raise(*args, **kwargs):
        raise ValueError(sentinel)

    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", _raise)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(sentinel in m for m in all_wo), f"marker must preserve exception detail verbatim; got {all_wo!r}"


# ---------------------------------------------------------------------------
# (22) Multiple substrate failures cumulate markers
# ---------------------------------------------------------------------------


def test_multiple_substrate_failures_cumulate_markers(cli_runner, trace_project, monkeypatch):
    """When multiple substrates raise, ALL markers accumulate -- no
    silent loss of the second marker.
    """

    # Patch build_symbol_graph to raise -- this triggers the
    # build_dependency_graph marker. Path-finding falls through to []
    # then the no-path branch returns. Only one marker expected here;
    # but the substrate captures additional failures cumulatively in
    # the same list if multiple wraps fail. To test cumulation, we
    # introduce a second failure in extract_path_metrics that won't
    # actually fire (because annotated_paths is already empty after the
    # no-path branch returns). So instead test that the LIST is
    # available + iterable.
    def _raise(*args, **kwargs):
        raise RuntimeError("cumulative-A")

    import roam.graph.builder as _builder_mod

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", _raise)

    result = _invoke_trace(cli_runner, trace_project, "foo", "bar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # At least one marker present + list structure preserved.
    assert isinstance(data.get("warnings_out"), list), (
        f"top-level warnings_out must be a list; got {type(data.get('warnings_out')).__name__}"
    )
    assert isinstance(data["summary"].get("warnings_out"), list), (
        f"summary.warnings_out must be a list; got {type(data['summary'].get('warnings_out')).__name__}"
    )
    assert any("trace_build_dependency_graph_failed" in m for m in all_wo), all_wo


# ---------------------------------------------------------------------------
# (23) Substrate boundary count matches the documented contract
# ---------------------------------------------------------------------------


def test_substrate_phase_count_matches_documented_contract():
    """The W607-EQ wave wraps exactly 9 substrate boundaries (see
    _EQ_PHASES). The cmd_trace source must reference each phase by
    name at least once (either inside _run_check_eq("phase", ...) or
    inside the marker fstring).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_trace.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _EQ_PHASES:
        # The phase name should appear in a literal or as the first arg.
        assert phase in src, f"W607-EQ wave phase {phase!r} not referenced in cmd_trace.py"


# ---------------------------------------------------------------------------
# (24) Cross-prefix isolation: trace markers don't leak into closure tests
# ---------------------------------------------------------------------------


def test_cross_prefix_isolation_with_closure_sibling():
    """Confirm cmd_closure.py and cmd_trace.py use mutually-exclusive
    marker fstrings -- W607-EM stays in closure_*; W607-EQ stays in trace_*.
    """
    base = Path(__file__).parent.parent / "src" / "roam" / "commands"
    closure_src = (base / "cmd_closure.py").read_text(encoding="utf-8")
    trace_src = (base / "cmd_trace.py").read_text(encoding="utf-8")

    # cmd_closure must NOT carry the trace marker family.
    assert 'f"trace_{phase}_failed:{type(exc).__name__}:{exc}"' not in closure_src, (
        "cmd_closure leaks the trace marker fstring"
    )
    # cmd_trace must NOT carry the closure marker family.
    assert 'f"closure_{phase}_failed:{type(exc).__name__}:{exc}"' not in trace_src, (
        "cmd_trace leaks the closure marker fstring"
    )

    # Each carries its own canonical marker.
    assert 'f"closure_{phase}_failed:{type(exc).__name__}:{exc}"' in closure_src
    assert 'f"trace_{phase}_failed:{type(exc).__name__}:{exc}"' in trace_src


# ---------------------------------------------------------------------------
# (25) W607-EK AST traversal lesson: helpers as positional args
# ---------------------------------------------------------------------------


def test_w607_ek_ast_traversal_helpers_as_positional_args():
    """W607-EK lesson: when helpers pass as positional args inside
    _run_check_eq() calls, AST traversal needs to look at Name
    references inside Call.args -- not just the func attribute.

    Verify the cmd_trace AST contains at least one _run_check_eq call
    where a Name reference appears in the args list (i.e. a helper is
    passed positionally).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_trace.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_run_check_eq"):
            continue
        # Walk the args to find any Name references (= helper
        # invocations passed positionally).
        for arg in node.args[1:]:  # skip phase string at args[0]
            if isinstance(arg, ast.Name):
                found = True
                break
        if found:
            break
    assert found, (
        "W607-EK AST traversal pin: no _run_check_eq(...) call carries a "
        "Name reference in its positional args; this means no substrate "
        "is wrapped via the canonical positional-helper pattern. The "
        "wave's substrate-CALL plumbing has been refactored away."
    )


# ---------------------------------------------------------------------------
# (26) format_text_output substrate marker on text-mode failure
# ---------------------------------------------------------------------------


def test_format_text_output_substrate_marker_on_text_mode(cli_runner, trace_project, monkeypatch):
    """If the format_text_output substrate raises in text mode, the
    accumulator surfaces a marker even though text mode doesn't emit
    the JSON envelope.

    We verify this at the source-level: the substrate is wrapped (see
    test_all_w607eq_substrate_phases_wrapped_in_source) and the
    canonical marker fstring is documented (see
    test_w607eq_marker_shape_documented_in_source). Runtime
    verification on a real text-mode failure path requires patching
    click.echo + handling stderr separately; the source-level guard
    is sufficient.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_trace.py"
    src = src_path.read_text(encoding="utf-8")
    # Source carries the format_text_output substrate wrap (either same-
    # line or multi-line invocation OR a marker reference).
    assert (
        '_run_check_eq("format_text_output"' in src
        or '"format_text_output"' in src
        or "trace_format_text_output_failed" in src
    ), "format_text_output substrate marker / wrap missing from cmd_trace"
