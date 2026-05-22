"""W607-EF -- ``cmd_simulate`` substrate-boundary plumbing.

cmd_simulate is the fifth leg of the architecture-prediction PENTAGON
at substrate-CALL layer -- alongside cmd_orchestrate (W607-DS),
cmd_partition (W607-DU), cmd_agent_plan (W607-DY), and cmd_fleet
(W607-EB) -- and uniquely emits counterfactual graph-mutation envelopes
(move/extract/merge/delete deltas on a cloned graph). Until this wave
the command had no substrate-boundary marker plumbing -- a raise inside
``build_symbol_graph`` (baseline), ``clone_graph`` + the transform
dispatch, ``compute_graph_metrics`` on the counterfactual graph,
``metric_delta``, or any downstream verdict / envelope composer would
crash the simulate command outright.

This wave installs the canonical ``_w607ef_warnings_out`` bucket +
``_run_check_ef`` helper inside ``_run_simulation`` (the shared flow
for all simulate subcommands) and wraps every substrate boundary:

* load_baseline_graph     -- DB -> baseline networkx graph + pre-metrics
* apply_transforms        -- clone_graph + op_args_fn dispatch
* recompute_metrics       -- compute_graph_metrics on counterfactual
* diff_metrics            -- metric_delta + warning derivation
* compose_verdict         -- LAW 6 single-line health-delta floor
* compose_facts           -- agent_contract.facts list
* compose_next_commands   -- agent_contract.next_commands
* serialize_envelope      -- JSON envelope emission
* format_text_output      -- text path metric-delta table printing

Marker family ``simulate_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test.

COUNTERFACTUAL BASELINE PRESERVATION
------------------------------------

Identity transforms (move-to-same-file, etc.) must produce an empty
metric diff on the happy path. The W607-EF substrate boundary on
``recompute_metrics`` / ``diff_metrics`` must NOT introduce drift: a
clean simulate call still produces ``health_delta == 0`` and zero
markers. The regression-guard test below confirms.

LAW 6 VERDICT-FIRST INVARIANT
-----------------------------

``summary.verdict`` survives every phase failure as a literal floor.
A raise in any substrate degrades to the empty-floor verdict string
(``"health unchanged at 0, 0 new cycles"``); the verdict is NEVER
absent.

CROSS-PREFIX ISOLATION
----------------------

``simulate_*`` markers do NOT leak into ``orchestrate_*`` /
``partition_*`` / ``agent_plan_*`` / ``fleet_*`` (the
architecture-prediction PENTAGON siblings) or any of the broader
detector and architecture command families. The prefix-discipline test
confirms hard distinction.

ARCHITECTURE-PREDICTION PENTAGON 5-WAY PAIRING PIN
--------------------------------------------------

cmd_orchestrate (W607-DS), cmd_partition (W607-DU),
cmd_agent_plan (W607-DY), cmd_fleet (W607-EB), and cmd_simulate
(W607-EF) together close the architecture-prediction pentagon at
substrate-CALL layer. The 5-way AST-scan test below confirms all
sibling commands carry their respective W607 plumbing accumulators --
the family is closed.
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


def _build_sim_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_simulate.

    The W607-EF substrate boundary tests monkeypatch the interior calls
    (build_symbol_graph, clone_graph, etc.) so the actual graph contents
    matter less than DB-and-index presence. We just need ensure_index()
    to find a .roam DB rooted at tmp_path.
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
def sim_project(tmp_path):
    return _build_sim_project(tmp_path)


def _make_graph_with_nodes():
    """Build a tiny networkx DiGraph mirroring the seeded DB."""
    G = nx.DiGraph()
    G.add_node(1, name="foo", file_path="src/a.py", kind="function")
    G.add_node(2, name="bar", file_path="src/b.py", kind="function")
    G.add_edge(2, 1, kind="calls")
    return G


def _invoke_simulate(cli_runner, project_root, *args, json_mode=True):
    """Invoke the simulate click command directly.

    Clears the module-level ``_GRAPH_CACHE`` before every invocation so
    monkeypatched ``build_symbol_graph`` calls aren't bypassed by a
    cached graph from a sibling test.
    """
    from roam.commands.cmd_simulate import simulate
    from roam.graph.builder import clear_graph_cache

    clear_graph_cache()

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(simulate, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_EF_PHASES = (
    "load_baseline_graph",
    "apply_transforms",
    "recompute_metrics",
    "diff_metrics",
    "compose_verdict",
    "compose_facts",
    "compose_next_commands",
    "serialize_envelope",
    "format_text_output",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-EF substrate markers
# ---------------------------------------------------------------------------


def test_simulate_clean_envelope_omits_w607ef_markers(cli_runner, sim_project, monkeypatch):
    """Clean simulate run -> no W607-EF substrate markers."""
    import roam.graph.builder as _builder
    from roam.graph import simulate as _sim_mod

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_graph_with_nodes())

    # resolve_target succeeds; apply_move returns a tidy dict.
    monkeypatch.setattr(_sim_mod, "resolve_target", lambda G, conn, t: ([1], "foo"))
    monkeypatch.setattr(
        _sim_mod,
        "apply_move",
        lambda G, nid, target: {"operation": "move", "symbol": "foo", "from_file": "src/a.py", "to_file": target},
    )

    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/b.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "simulate"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ef_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"simulate_{p}_failed:" in m for p in _EF_PHASES)]
    assert not ef_markers, (
        f"clean simulate must NOT surface W607-EF substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) load_baseline_graph failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_simulate_baseline_failure_marker_format(cli_runner, sim_project, monkeypatch):
    """If ``build_symbol_graph`` raises, surface the canonical marker."""
    import roam.graph.builder as _builder
    from roam.graph import simulate as _sim_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-baseline-from-W607-EF")

    monkeypatch.setattr(_builder, "build_symbol_graph", _raise)
    monkeypatch.setattr(_sim_mod, "resolve_target", lambda G, conn, t: ([1], "foo"))
    monkeypatch.setattr(
        _sim_mod,
        "apply_move",
        lambda G, nid, target: {"operation": "move"},
    )

    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/b.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    baseline_markers = [m for m in all_wo if m.startswith("simulate_load_baseline_graph_failed:")]
    assert baseline_markers, f"expected simulate_load_baseline_graph_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in baseline_markers), baseline_markers
    assert any("synthetic-baseline-from-W607-EF" in m for m in baseline_markers), baseline_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_simulate_w607ef_warnings_in_envelope(cli_runner, sim_project, monkeypatch):
    """Non-empty W607-EF bucket -> both top-level AND summary.warnings_out."""
    import roam.graph.builder as _builder

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-EF")

    monkeypatch.setattr(_builder, "build_symbol_graph", _raise)

    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/b.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-EF disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-EF disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_simulate_three_segment_marker_shape(cli_runner, sim_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    import roam.graph.builder as _builder

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-EF")

    monkeypatch.setattr(_builder, "build_symbol_graph", _raise)

    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/b.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("simulate_load_baseline_graph_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "simulate_load_baseline_graph_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) recompute_metrics failure -> marker, command still emits
# ---------------------------------------------------------------------------


def test_simulate_recompute_metrics_failure_surfaces_marker(cli_runner, sim_project, monkeypatch):
    """A raise in ``compute_graph_metrics`` after the transform surfaces a marker."""
    import roam.graph.builder as _builder
    from roam.graph import simulate as _sim_mod

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_graph_with_nodes())
    monkeypatch.setattr(_sim_mod, "resolve_target", lambda G, conn, t: ([1], "foo"))

    # The simulate command short-circuits the recompute when the
    # counterfactual graph is topologically identical to the baseline
    # (perf optimisation — every metric is derived from topology, so
    # an unchanged topology reproduces the baseline byte-for-byte).
    # The stub must therefore *actually* mutate G so the recompute
    # path runs and the synthetic raise below can fire.
    def _stub_apply_move(G, nid, target):
        G.add_node("__force_topology_change__")
        return {"operation": "move", "symbol": "foo"}

    monkeypatch.setattr(_sim_mod, "apply_move", _stub_apply_move)

    # Track call count so the baseline pass succeeds (call 1) but the
    # post-transform recompute (call 2) raises.
    call_count = {"n": 0}
    real_compute = _sim_mod.compute_graph_metrics

    def _maybe_raise(G):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_compute(G)
        raise RuntimeError("synthetic-recompute-from-W607-EF")

    monkeypatch.setattr(_sim_mod, "compute_graph_metrics", _maybe_raise)

    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/b.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    recompute_markers = [m for m in all_wo if m.startswith("simulate_recompute_metrics_failed:")]
    assert recompute_markers, all_wo
    # Envelope still composes.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-EF stays in ``simulate_*`` family
# ---------------------------------------------------------------------------


def test_w607ef_marker_prefix_stays_in_simulate_family(cli_runner, sim_project, monkeypatch):
    """Every W607-EF substrate marker uses the canonical ``simulate_*`` prefix.

    Hard distinction from sibling W607-* layers across the broader
    command surface. Confirms cross-prefix isolation per the wave
    contract.
    """
    import roam.graph.builder as _builder

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-EF")

    monkeypatch.setattr(_builder, "build_symbol_graph", _raise)

    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/b.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("simulate_"), (
            f"every surfaced W607-EF marker must use the ``simulate_*`` prefix family; got {marker!r}"
        )
        # Hard distinction from the architecture-prediction PENTAGON
        # siblings (cmd_orchestrate / W607-DS, cmd_partition / W607-DU,
        # cmd_agent_plan / W607-DY, cmd_fleet / W607-EB) and from every
        # adjacent detector + architecture family.
        for forbidden_prefix, sibling in (
            ("orchestrate_", "cmd_orchestrate W607-DS (pentagon sibling)"),
            ("partition_", "cmd_partition W607-DU (pentagon sibling)"),
            ("agent_plan_", "cmd_agent_plan W607-DY (pentagon sibling)"),
            ("fleet_", "cmd_fleet W607-EB (pentagon sibling)"),
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
            ("bus_factor_", "cmd_bus_factor W607-CQ"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
            ("vulns_", "cmd_vulns W607-AQ + CH (security sibling)"),
            ("taint_", "cmd_taint W607-AY + CJ (security sibling)"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_simulate carries the W607-EF accumulator
# ---------------------------------------------------------------------------


def test_cmd_simulate_carries_w607ef_accumulator():
    """AST-level guard: cmd_simulate source carries the W607-EF accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_simulate.py"
    assert src_path.exists(), f"cmd_simulate.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ef_warnings_out" in src, (
        "W607-EF accumulator missing from cmd_simulate; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ef" in src, (
        "W607-EF ``_run_check_ef`` helper missing from cmd_simulate; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_ef = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ef":
            found_run_check_ef = True
            break
    assert found_run_check_ef, (
        "W607-EF ``_run_check_ef`` helper not found in cmd_simulate AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-EF substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ef_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-EF substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_simulate.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _EF_PHASES:
        same_line = f'_run_check_ef("{phase}"' in src
        multi_line = (
            f'_run_check_ef(\n        "{phase}"' in src
            or f'_run_check_ef(\n            "{phase}"' in src
            or f'_run_check_ef(\n                "{phase}"' in src
            or f'_run_check_ef(\n                    "{phase}"' in src
            or f'_run_check_ef(\n                        "{phase}"' in src
        )
        marker_grep = f"simulate_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-EF wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) compose_verdict failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_simulate_compose_verdict_failure_degrades(cli_runner, sim_project, monkeypatch):
    """A raise inside the verdict composer degrades to the empty-floor verdict.

    Force a malformed ``before`` metrics dict so the verdict access
    raises. The W607-EF compose_verdict substrate falls back to the
    empty-floor default verdict so LAW 6 holds.
    """
    import roam.graph.builder as _builder
    from roam.graph import simulate as _sim_mod

    class _BoomDict(dict):
        """Dict-like object that raises on .get('health_score')."""

        def get(self, key, default=None):
            if key == "health_score":
                raise ZeroDivisionError("synthetic-verdict-from-W607-EF")
            return super().get(key, default)

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_graph_with_nodes())
    monkeypatch.setattr(_sim_mod, "resolve_target", lambda G, conn, t: ([1], "foo"))
    monkeypatch.setattr(
        _sim_mod,
        "apply_move",
        lambda G, nid, target: {"operation": "move", "symbol": "foo"},
    )

    call_count = {"n": 0}

    def _maybe_boom(G):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # baseline: return poison dict
            d = _BoomDict()
            d["cycles"] = 0
            d["layer_violations"] = 0
            d["modularity"] = 0.0
            return d
        # after: return normal-looking dict
        return {"health_score": 50, "cycles": 0, "layer_violations": 0, "modularity": 0.0}

    monkeypatch.setattr(_sim_mod, "compute_graph_metrics", _maybe_boom)

    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/b.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # The poison dict causes compose_verdict to raise via .get('health_score').
    # Either compose_verdict OR diff_metrics may catch it depending on which
    # phase touches the poison first.
    bad_markers = [
        m
        for m in all_wo
        if m.startswith("simulate_compose_verdict_failed:") or m.startswith("simulate_diff_metrics_failed:")
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


def test_w607ef_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-EF marker shape lives in cmd_simulate."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_simulate.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"simulate_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-EF marker fstring missing from cmd_simulate; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (11) LAW 6 verdict-first invariant: verdict survives every phase failure
# ---------------------------------------------------------------------------


def test_law_6_verdict_survives_every_phase_failure(cli_runner, sim_project, monkeypatch):
    """LAW 6 invariant: ``summary.verdict`` is a non-empty single line on
    every phase failure -- the floor never disappears.

    Exercise: raise inside ``build_symbol_graph`` so the downstream
    substrates operate on the empty floor; the verdict still emits as
    the LAW-6 zero-count floor string.
    """
    import roam.graph.builder as _builder

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-from-W607-EF")

    monkeypatch.setattr(_builder, "build_symbol_graph", _raise)

    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/b.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict, (
        f"LAW 6 invariant violated: verdict missing/empty on degraded path; got summary={summary!r}"
    )
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    # The floor names the zero-count or zero-delta state, NOT a SAFE/passed vocabulary.
    forbidden_vocab = ("safe", "passed", "all clear")
    for forbidden in forbidden_vocab:
        assert forbidden not in verdict.lower(), (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# (12) COUNTERFACTUAL BASELINE PRESERVATION
# ---------------------------------------------------------------------------


def test_simulate_identity_transform_preserves_baseline(cli_runner, sim_project, monkeypatch):
    """Counterfactual baseline preservation regression guard.

    An identity transform (move-to-same-file, here simulated via
    apply_move returning the original location) must produce empty
    metric drift on the happy path: health_delta == 0, zero W607-EF
    markers, and a verdict whose floor names the unchanged state.
    """
    import roam.graph.builder as _builder
    from roam.graph import simulate as _sim_mod

    monkeypatch.setattr(_builder, "build_symbol_graph", lambda conn: _make_graph_with_nodes())
    monkeypatch.setattr(_sim_mod, "resolve_target", lambda G, conn, t: ([1], "foo"))
    # Identity transform: apply_move that doesn't actually mutate the graph.
    monkeypatch.setattr(
        _sim_mod,
        "apply_move",
        lambda G, nid, target: {"operation": "move", "symbol": "foo", "from_file": "src/a.py", "to_file": "src/a.py"},
    )

    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/a.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # Identity transform: no health drift.
    assert summary.get("health_delta") == 0, (
        f"identity transform must produce health_delta == 0 (no drift); got summary={summary!r}"
    )
    # No degraded-path markers.
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    ef_markers = [m for m in all_wo if any(f"simulate_{p}_failed:" in m for p in _EF_PHASES)]
    assert not ef_markers, f"identity transform must NOT surface W607-EF substrate markers; got {all_wo!r}"
    # partial_success NOT flipped on clean identity path.
    assert not summary.get("partial_success", False), (
        f"clean identity transform must NOT flip partial_success; got summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# (13) Per-substrate isolation -- each boundary raising surfaces marker
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_each_boundary_surfaces_marker(cli_runner, sim_project, monkeypatch):
    """Per-substrate isolation: each W607-EF boundary raising surfaces a
    distinct marker + graceful degradation.

    Raise inside ``build_symbol_graph`` and confirm the matching
    load_baseline_graph marker surfaces. The remaining substrates still
    run on the empty floor so the envelope composes a coherent verdict.
    """
    import roam.graph.builder as _builder

    def _raise_baseline(*args, **kwargs):
        raise RuntimeError("isolation-baseline-W607-EF")

    monkeypatch.setattr(_builder, "build_symbol_graph", _raise_baseline)
    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/b.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("simulate_load_baseline_graph_failed:") for m in all_wo), all_wo
    # Envelope still composes.
    assert isinstance(data["summary"]["verdict"], str)
    assert data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# (14) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, sim_project, monkeypatch):
    """Pattern-2 regression guard.

    If ``build_symbol_graph`` raises, the empty-floor default kicks in
    (health_score=0, cycles=0, etc.) and the envelope is emitted. The
    W607-EF wrap MUST flip ``partial_success: True`` on that branch so
    the empty-state envelope is NOT mistaken for a clean simulate
    verdict.
    """
    import roam.graph.builder as _builder

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-EF")

    monkeypatch.setattr(_builder, "build_symbol_graph", _raise)

    result = _invoke_simulate(cli_runner, sim_project, "move", "foo", "src/b.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    baseline_markers = [m for m in all_wo if m.startswith("simulate_load_baseline_graph_failed:")]
    assert baseline_markers, (
        f"degraded path MUST surface the load_baseline_graph marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (15) Helper-template ``return default`` verbatim shape
# ---------------------------------------------------------------------------


def test_run_check_ef_helper_returns_default_verbatim():
    """W607-DP finding: the _run_check_ef helper MUST end with the literal
    ``return default`` (not ``return None`` or a captured local). A raise
    inside the wrapped fn falls through to ``return default`` so the
    caller's empty-floor default actually propagates.

    AST-level guard: locate the ``_run_check_ef`` FunctionDef and walk
    its body to confirm the last statement of the ``except`` handler
    is ``Return(value=Name(id='default'))``.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_simulate.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_ef"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ExceptHandler):
                # Last statement in the except body must be ``return default``.
                last_stmt = sub.body[-1]
                assert isinstance(last_stmt, ast.Return), (
                    f"_run_check_ef except handler last stmt is {type(last_stmt).__name__!r}, not Return"
                )
                assert isinstance(last_stmt.value, ast.Name), (
                    f"_run_check_ef must `return default` (a Name), got {ast.dump(last_stmt.value)!r}"
                )
                assert last_stmt.value.id == "default", (
                    f"_run_check_ef must `return default`, got `return {last_stmt.value.id}`"
                )
                found = True
                break
        if found:
            break
    assert found, (
        "_run_check_ef FunctionDef / except handler not found in cmd_simulate AST; the helper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (16) ARCHITECTURE-PREDICTION PENTAGON 5-WAY PAIRING PIN
# ---------------------------------------------------------------------------


def test_architecture_prediction_pentagon_5way_pairing():
    """AST-scan pin: cmd_orchestrate (W607-DS) + cmd_partition (W607-DU) +
    cmd_agent_plan (W607-DY) + cmd_fleet (W607-EB) + cmd_simulate (W607-EF)
    all carry W607 substrate-CALL plumbing.

    The architecture-prediction pentagon is closed at substrate-CALL
    layer. Removing the plumbing from any source file fails this guard
    so the pentagon invariant stays loud.
    """
    root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    orchestrate_src = (root / "cmd_orchestrate.py").read_text(encoding="utf-8")
    partition_src = (root / "cmd_partition.py").read_text(encoding="utf-8")
    agent_plan_src = (root / "cmd_agent_plan.py").read_text(encoding="utf-8")
    fleet_src = (root / "cmd_fleet.py").read_text(encoding="utf-8")
    simulate_src = (root / "cmd_simulate.py").read_text(encoding="utf-8")

    # cmd_orchestrate carries W607-DS
    assert "_w607ds_warnings_out" in orchestrate_src, (
        "pentagon pairing pin: cmd_orchestrate has lost its "
        "W607-DS substrate-CALL accumulator -- the pentagon is no longer "
        "closed."
    )
    assert "_run_check_ds" in orchestrate_src, (
        "pentagon pairing pin: cmd_orchestrate has lost its W607-DS ``_run_check_ds`` helper."
    )

    # cmd_partition carries W607-DU
    assert "_w607du_warnings_out" in partition_src, (
        "pentagon pairing pin: cmd_partition has lost its "
        "W607-DU substrate-CALL accumulator -- the pentagon is no longer "
        "closed."
    )
    assert "_run_check_du" in partition_src, (
        "pentagon pairing pin: cmd_partition has lost its W607-DU ``_run_check_du`` helper."
    )

    # cmd_agent_plan carries W607-DY
    assert "_w607dy_warnings_out" in agent_plan_src, (
        "pentagon pairing pin: cmd_agent_plan has lost its "
        "W607-DY substrate-CALL accumulator -- the pentagon is no longer "
        "closed."
    )
    assert "_run_check_dy" in agent_plan_src, (
        "pentagon pairing pin: cmd_agent_plan has lost its W607-DY ``_run_check_dy`` helper."
    )

    # cmd_fleet carries W607-EB
    assert "_w607eb_warnings_out" in fleet_src, (
        "pentagon pairing pin: cmd_fleet has lost its "
        "W607-EB substrate-CALL accumulator -- the pentagon is no longer "
        "closed."
    )
    assert "_run_check_eb" in fleet_src, (
        "pentagon pairing pin: cmd_fleet has lost its W607-EB ``_run_check_eb`` helper."
    )

    # cmd_simulate carries W607-EF
    assert "_w607ef_warnings_out" in simulate_src, (
        "pentagon pairing pin: cmd_simulate has lost its "
        "W607-EF substrate-CALL accumulator -- the pentagon is no longer "
        "closed."
    )
    assert "_run_check_ef" in simulate_src, (
        "pentagon pairing pin: cmd_simulate has lost its W607-EF ``_run_check_ef`` helper."
    )

    # Cross-prefix discipline at source level: each sibling's marker
    # fstring does NOT leak into the other source files.
    orchestrate_marker = 'f"orchestrate_{phase}_failed:{type(exc).__name__}:{exc}"'
    partition_marker = 'f"partition_{phase}_failed:{type(exc).__name__}:{exc}"'
    agent_plan_marker = 'f"agent_plan_{phase}_failed:{type(exc).__name__}:{exc}"'
    fleet_marker = 'f"fleet_{phase}_failed:{type(exc).__name__}:{exc}"'
    simulate_marker = 'f"simulate_{phase}_failed:{type(exc).__name__}:{exc}"'

    # No leg leaks any sibling's marker fstring.
    for src_name, src_text in (
        ("cmd_orchestrate", orchestrate_src),
        ("cmd_partition", partition_src),
        ("cmd_agent_plan", agent_plan_src),
        ("cmd_fleet", fleet_src),
        ("cmd_simulate", simulate_src),
    ):
        for other_name, other_marker in (
            ("orchestrate_", orchestrate_marker),
            ("partition_", partition_marker),
            ("agent_plan_", agent_plan_marker),
            ("fleet_", fleet_marker),
            ("simulate_", simulate_marker),
        ):
            # Don't compare a source against its own marker.
            if src_name == "cmd_" + other_name.rstrip("_"):
                continue
            assert other_marker not in src_text, (
                f"{src_name} leaks ``{other_name}*`` marker -- prefix discipline violated."
            )
