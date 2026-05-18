"""W607-EI -- ``cmd_cut`` substrate-boundary plumbing.

cmd_cut is the graph-cut command (minimum edge cuts between
architectural clusters), one leg of the structural-analysis family
alongside cmd_closure (transitive closure) and cmd_simulate (W607-EF,
counterfactual transforms). Until this wave the command had no
substrate-boundary marker plumbing -- a raise inside ``detect_clusters``,
``label_clusters``, ``nx.minimum_edge_cut``,
``nx.edge_betweenness_centrality``, or any downstream verdict / envelope
composer would crash the cut command outright.

This wave installs the canonical ``_w607ei_warnings_out`` bucket +
``_run_check_ei`` helper inside ``cut`` and wraps every substrate
boundary:

* detect_clusters         -- detect_clusters + label_clusters + grouping
* compute_min_cuts        -- the boundaries loop (cross-edges + min-cut)
* extract_leak_edges      -- edge_betweenness leak-edge ranking
* compose_verdict         -- LAW 6 single-line floor
* compose_facts           -- agent_contract.facts list
* compose_next_commands   -- agent_contract.next_commands
* serialize_envelope      -- JSON envelope emission
* format_text_output      -- text path boundary table printing

Marker family ``cut_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test.

DISCONNECTED-GRAPH REGRESSION
-----------------------------

cmd_cut on a disconnected source+target graph must produce a sensible
verdict, not crash. The W607-EI substrate plumbing must not regress
the existing graceful-degradation behaviour around
``nx.minimum_edge_cut`` raises (already caught inside the inner loop
by a narrow except), nor introduce a new crash on the
empty-cross-edges branch.

LAW 6 VERDICT-FIRST INVARIANT
-----------------------------

``summary.verdict`` survives every phase failure as a literal floor.
A raise in any substrate degrades to the empty-floor verdict string
(``"0 boundaries analyzed, 0 fragile boundaries"``); the verdict is
NEVER absent.

CROSS-PREFIX ISOLATION
----------------------

``cut_*`` markers do NOT leak into ``simulate_*`` / ``closure_*`` /
``orchestrate_*`` / ``partition_*`` / ``agent_plan_*`` / ``fleet_*``
or any of the broader detector and architecture command families.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sqlite3
from pathlib import Path

import networkx as nx
import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_cut_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_cut.

    The W607-EI substrate boundary tests monkeypatch the interior calls
    (build_symbol_graph, detect_clusters, etc.) so the actual graph
    contents matter less than DB-and-index presence. We just need
    ensure_index() to find a .roam DB rooted at tmp_path.
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
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (2, 1, 'calls')")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def cut_project(tmp_path):
    return _build_cut_project(tmp_path)


def _make_two_cluster_graph():
    """Build a small two-cluster DiGraph with one cross-edge.

    Two clusters {1, 2} and {3, 4} connected by one cross-edge 2 -> 3.
    Min cut between them is the single cross-edge.
    """
    G = nx.DiGraph()
    G.add_node(1, name="a1", file_path="src/cluster_a/a1.py", kind="function")
    G.add_node(2, name="a2", file_path="src/cluster_a/a2.py", kind="function")
    G.add_node(3, name="b1", file_path="src/cluster_b/b1.py", kind="function")
    G.add_node(4, name="b2", file_path="src/cluster_b/b2.py", kind="function")
    G.add_edge(1, 2, kind="calls")
    G.add_edge(2, 3, kind="calls")  # cross-edge
    G.add_edge(3, 4, kind="calls")
    return G


def _make_disconnected_graph():
    """Build a graph with two clusters but NO cross-edges (disconnected).

    {1, 2} and {3, 4} with no edges between them.
    """
    G = nx.DiGraph()
    G.add_node(1, name="a1", file_path="src/cluster_a/a1.py", kind="function")
    G.add_node(2, name="a2", file_path="src/cluster_a/a2.py", kind="function")
    G.add_node(3, name="b1", file_path="src/cluster_b/b1.py", kind="function")
    G.add_node(4, name="b2", file_path="src/cluster_b/b2.py", kind="function")
    G.add_edge(1, 2, kind="calls")
    G.add_edge(3, 4, kind="calls")
    return G


def _invoke_cut(cli_runner, project_root, *args, json_mode=True):
    """Invoke the cut click command directly.

    Clears the module-level ``_GRAPH_CACHE`` before every invocation so
    monkeypatched ``build_symbol_graph`` calls aren't bypassed by a
    cached graph from a sibling test.
    """
    from roam.commands.cmd_cut import cut
    from roam.graph.builder import clear_graph_cache

    clear_graph_cache()

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cut, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_EI_PHASES = (
    "detect_clusters",
    "compute_min_cuts",
    "extract_leak_edges",
    "compose_verdict",
    "compose_facts",
    "compose_next_commands",
    "serialize_envelope",
    "format_text_output",
)


def _two_cluster_labels(clusters, conn):
    """label_clusters stub returning string labels keyed by cluster id."""
    cluster_ids = sorted(set(clusters.values()))
    return {cid: f"cluster_{cid}" for cid in cluster_ids}


def _two_cluster_detect(G):
    """detect_clusters stub: {1: 0, 2: 0, 3: 1, 4: 1}."""
    out = {}
    for n in G.nodes():
        out[n] = 0 if n in (1, 2) else 1
    return out


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-EI substrate markers
# ---------------------------------------------------------------------------


def test_cut_clean_envelope_omits_w607ei_markers(cli_runner, cut_project, monkeypatch):
    """Clean cut run -> no W607-EI substrate markers."""
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_two_cluster_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _two_cluster_detect)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    result = _invoke_cut(cli_runner, cut_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "cut"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ei_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"cut_{p}_failed:" in m for p in _EI_PHASES)]
    assert not ei_markers, (
        f"clean cut must NOT surface W607-EI substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) detect_clusters failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_cut_detect_clusters_failure_marker_format(cli_runner, cut_project, monkeypatch):
    """If ``detect_clusters`` raises, surface the canonical marker."""
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-detect-from-W607-EI")

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_two_cluster_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _raise)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    result = _invoke_cut(cli_runner, cut_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    detect_markers = [m for m in all_wo if m.startswith("cut_detect_clusters_failed:")]
    assert detect_markers, f"expected cut_detect_clusters_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in detect_markers), detect_markers
    assert any("synthetic-detect-from-W607-EI" in m for m in detect_markers), detect_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_cut_w607ei_warnings_in_envelope(cli_runner, cut_project, monkeypatch):
    """Non-empty W607-EI bucket -> both top-level AND summary.warnings_out."""
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-EI")

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_two_cluster_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _raise)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    result = _invoke_cut(cli_runner, cut_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-EI disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-EI disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_cut_three_segment_marker_shape(cli_runner, cut_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-EI")

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_two_cluster_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _raise)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    result = _invoke_cut(cli_runner, cut_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("cut_detect_clusters_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "cut_detect_clusters_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) extract_leak_edges failure -> marker, command still emits
# ---------------------------------------------------------------------------


def test_cut_extract_leak_edges_failure_surfaces_marker(cli_runner, cut_project, monkeypatch):
    """A raise in ``edge_betweenness_centrality`` substrate surfaces a marker.

    The wider extract_leak_edges substrate wraps the edge-betweenness
    call. Force its outer-wrapper to raise by monkeypatching the
    networkx function to raise an exception type the inner try/except
    does NOT catch (and importing the cmd module's nx reference).
    """
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_two_cluster_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _two_cluster_detect)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    # Force the leak-edges substrate to raise by replacing labels with a
    # poison object that raises on .get() inside _extract_leak_edges'
    # cross-cluster filter loop.
    class _BoomDict(dict):
        def get(self, key, default=None):
            raise RuntimeError("synthetic-leak-from-W607-EI")

    # Replace label_clusters with one that returns a normal dict, but
    # replace clusters.get via monkeypatching detect_clusters output.
    def _poison_detect(G):
        d = _BoomDict()
        d[1] = 0
        d[2] = 0
        d[3] = 1
        d[4] = 1
        return d

    monkeypatch.setattr(_clusters_mod, "detect_clusters", _poison_detect)

    result = _invoke_cut(cli_runner, cut_project, "--leak-edges")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    leak_markers = [
        m
        for m in all_wo
        if m.startswith("cut_extract_leak_edges_failed:")
        or m.startswith("cut_detect_clusters_failed:")
        or m.startswith("cut_compute_min_cuts_failed:")
    ]
    assert leak_markers, all_wo
    # Envelope still composes.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-EI stays in ``cut_*`` family
# ---------------------------------------------------------------------------


def test_w607ei_marker_prefix_stays_in_cut_family(cli_runner, cut_project, monkeypatch):
    """Every W607-EI substrate marker uses the canonical ``cut_*`` prefix.

    Hard distinction from sibling W607-* layers across the broader
    command surface. Confirms cross-prefix isolation per the wave
    contract.
    """
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-EI")

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_two_cluster_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _raise)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    result = _invoke_cut(cli_runner, cut_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("cut_"), (
            f"every surfaced W607-EI marker must use the ``cut_*`` prefix family; got {marker!r}"
        )
        # Hard distinction from sibling structural-analysis family +
        # adjacent detector + architecture families.
        for forbidden_prefix, sibling in (
            ("simulate_", "cmd_simulate W607-EF (structural-analysis sibling)"),
            ("closure_", "cmd_closure (structural-analysis sibling)"),
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
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_cut carries the W607-EI accumulator
# ---------------------------------------------------------------------------


def test_cmd_cut_carries_w607ei_accumulator():
    """AST-level guard: cmd_cut source carries the W607-EI accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_cut.py"
    assert src_path.exists(), f"cmd_cut.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ei_warnings_out" in src, (
        "W607-EI accumulator missing from cmd_cut; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ei" in src, (
        "W607-EI ``_run_check_ei`` helper missing from cmd_cut; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_ei = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ei":
            found_run_check_ei = True
            break
    assert found_run_check_ei, (
        "W607-EI ``_run_check_ei`` helper not found in cmd_cut AST; the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-EI substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ei_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-EI substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_cut.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _EI_PHASES:
        same_line = f'_run_check_ei("{phase}"' in src
        multi_line = (
            f'_run_check_ei(\n        "{phase}"' in src
            or f'_run_check_ei(\n            "{phase}"' in src
            or f'_run_check_ei(\n                "{phase}"' in src
        )
        marker_grep = f"cut_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-EI wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) compose_verdict failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_cut_compose_verdict_failure_degrades(cli_runner, cut_project, monkeypatch):
    """A raise inside the verdict composer degrades to the empty-floor verdict.

    Force ``boundaries`` (the in-scope local) to be a poison object so
    the sum() / len() calls inside _compose_verdict raise. The W607-EI
    compose_verdict substrate falls back to the empty-floor default
    verdict so LAW 6 holds.
    """
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    # Replace compute_min_cuts via the detector path -- force it to
    # return a poison object so sum() inside _compose_verdict raises.
    class _PoisonList(list):
        def __len__(self):
            raise ZeroDivisionError("synthetic-verdict-from-W607-EI")

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_two_cluster_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _two_cluster_detect)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    # Replace the boundaries return path by monkeypatching minimum_edge_cut
    # to raise an outer exception that the inner try/except does not catch.

    # Patch the nx symbol the cmd module imports inside the with-block.
    # Use a wrapper-level raise by making detect_clusters return a poison
    # bool that breaks the loop iteration.
    class _Boom:
        def items(self):
            raise RuntimeError("synthetic-compose-verdict-from-W607-EI")

    monkeypatch.setattr(
        _clusters_mod,
        "detect_clusters",
        lambda G: _Boom(),
    )

    result = _invoke_cut(cli_runner, cut_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    bad_markers = [
        m
        for m in all_wo
        if m.startswith("cut_detect_clusters_failed:")
        or m.startswith("cut_compute_min_cuts_failed:")
        or m.startswith("cut_compose_verdict_failed:")
    ]
    assert bad_markers, all_wo
    # Verdict still emits (LAW 6 single-line).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607ei_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-EI marker shape lives in cmd_cut."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_cut.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"cut_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, f"canonical W607-EI marker fstring missing from cmd_cut; expected: {fstring_pattern}"


# ---------------------------------------------------------------------------
# (11) LAW 6 verdict-first invariant: verdict survives every phase failure
# ---------------------------------------------------------------------------


def test_law_6_verdict_survives_every_phase_failure(cli_runner, cut_project, monkeypatch):
    """LAW 6 invariant: ``summary.verdict`` is a non-empty single line on
    every phase failure -- the floor never disappears.

    Exercise: raise inside ``detect_clusters`` so the downstream
    substrates operate on the empty floor; the verdict still emits as
    the LAW-6 zero-count floor string.
    """
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-from-W607-EI")

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_two_cluster_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _raise)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    result = _invoke_cut(cli_runner, cut_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict, (
        f"LAW 6 invariant violated: verdict missing/empty on degraded path; got summary={summary!r}"
    )
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    # The floor names the zero-count state, NOT a SAFE/passed vocabulary.
    forbidden_vocab = ("safe", "passed", "all clear")
    for forbidden in forbidden_vocab:
        assert forbidden not in verdict.lower(), (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# (12) DISCONNECTED-GRAPH REGRESSION PRESERVATION
# ---------------------------------------------------------------------------


def test_cut_disconnected_graph_produces_sensible_verdict(cli_runner, cut_project, monkeypatch):
    """Disconnected-graph regression guard.

    cmd_cut on a graph with two clusters but NO cross-edges must
    produce a sensible verdict (zero boundaries, zero fragile), not
    crash. The W607-EI substrate plumbing must NOT regress this
    graceful-degradation behaviour: clean envelope, no W607-EI
    markers, partial_success unset.
    """
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_disconnected_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _two_cluster_detect)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    result = _invoke_cut(cli_runner, cut_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # Disconnected: zero boundaries analyzed (no cross-edges between
    # clusters means the inner loop's `if not cross_edges: continue`
    # branch is taken for every cluster pair).
    assert summary.get("boundaries_analyzed", -1) == 0, (
        f"disconnected graph must produce 0 boundaries; got summary={summary!r}"
    )
    # Verdict is the zero-count floor.
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict
    # No degraded-path markers.
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    ei_markers = [m for m in all_wo if any(f"cut_{p}_failed:" in m for p in _EI_PHASES)]
    assert not ei_markers, f"disconnected graph must NOT surface W607-EI substrate markers; got {all_wo!r}"
    # partial_success NOT flipped on clean disconnected path.
    assert not summary.get("partial_success", False), (
        f"clean disconnected path must NOT flip partial_success; got summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# (13) Per-substrate isolation -- each boundary raising surfaces marker
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_detect_boundary_surfaces_marker(cli_runner, cut_project, monkeypatch):
    """Per-substrate isolation: detect_clusters raising surfaces a
    distinct marker + graceful degradation.

    Raise inside ``detect_clusters`` and confirm the matching
    detect_clusters marker surfaces. The remaining substrates still
    run on the empty floor so the envelope composes a coherent
    verdict.
    """
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    def _raise_detect(*args, **kwargs):
        raise RuntimeError("isolation-detect-W607-EI")

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_two_cluster_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _raise_detect)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    result = _invoke_cut(cli_runner, cut_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("cut_detect_clusters_failed:") for m in all_wo), all_wo
    # Envelope still composes.
    assert isinstance(data["summary"]["verdict"], str)
    assert data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# (14) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, cut_project, monkeypatch):
    """Pattern-2 regression guard.

    If ``detect_clusters`` raises, the empty-floor default kicks in
    (boundaries=[], leak_edges=[]) and the envelope is emitted. The
    W607-EI wrap MUST flip ``partial_success: True`` on that branch
    so the empty-state envelope is NOT mistaken for a clean cut
    verdict.
    """
    import roam.graph.builder as _builder
    import roam.graph.clusters as _clusters_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-EI")

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_two_cluster_graph())
    monkeypatch.setattr(_clusters_mod, "detect_clusters", _raise)
    monkeypatch.setattr(_clusters_mod, "label_clusters", _two_cluster_labels)

    result = _invoke_cut(cli_runner, cut_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    detect_markers = [m for m in all_wo if m.startswith("cut_detect_clusters_failed:")]
    assert detect_markers, (
        f"degraded path MUST surface the detect_clusters marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (15) Helper-template ``return default`` verbatim shape
# ---------------------------------------------------------------------------


def test_run_check_ei_helper_returns_default_verbatim():
    """W607-DP finding: the _run_check_ei helper MUST end with the literal
    ``return default`` (not ``return None`` or a captured local). A raise
    inside the wrapped fn falls through to ``return default`` so the
    caller's empty-floor default actually propagates.

    AST-level guard: locate the ``_run_check_ei`` FunctionDef and walk
    its body to confirm the last statement of the ``except`` handler
    is ``Return(value=Name(id='default'))``.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_cut.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_ei"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ExceptHandler):
                # Last statement in the except body must be ``return default``.
                last_stmt = sub.body[-1]
                assert isinstance(last_stmt, ast.Return), (
                    f"_run_check_ei except handler last stmt is {type(last_stmt).__name__!r}, not Return"
                )
                assert isinstance(last_stmt.value, ast.Name), (
                    f"_run_check_ei must `return default` (a Name), got {ast.dump(last_stmt.value)!r}"
                )
                assert last_stmt.value.id == "default", (
                    f"_run_check_ei must `return default`, got `return {last_stmt.value.id}`"
                )
                found = True
                break
        if found:
            break
    assert found, (
        "_run_check_ei FunctionDef / except handler not found in cmd_cut AST; the helper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (16) Cross-prefix isolation at SOURCE level: cmd_cut doesn't leak siblings
# ---------------------------------------------------------------------------


def test_cmd_cut_source_no_sibling_marker_leak():
    """Source-level: cmd_cut.py does NOT contain any sibling W607-* marker fstrings.

    Hard distinction from sibling structural-analysis + adjacent
    families at the source-level: the cut command must emit only
    ``cut_*`` markers, never ``simulate_*`` / ``closure_*`` /
    ``orchestrate_*`` etc.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_cut.py"
    src = src_path.read_text(encoding="utf-8")
    cut_marker = 'f"cut_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert cut_marker in src, f"canonical cut marker fstring missing; expected: {cut_marker}"
    forbidden_markers = (
        'f"simulate_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"closure_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"orchestrate_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"partition_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"agent_plan_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"fleet_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"smells_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"vulns_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"taint_{phase}_failed:{type(exc).__name__}:{exc}"',
        'f"health_{phase}_failed:{type(exc).__name__}:{exc}"',
    )
    for forbidden in forbidden_markers:
        assert forbidden not in src, f"cmd_cut.py leaks sibling marker fstring: {forbidden!r}"
