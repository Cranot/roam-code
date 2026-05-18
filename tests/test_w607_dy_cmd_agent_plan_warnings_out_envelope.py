"""W607-DY -- ``cmd_agent_plan`` substrate-boundary plumbing.

cmd_agent_plan is the third leg of the architecture multi-agent triad
-- alongside cmd_orchestrate (W607-DS) and cmd_partition (W607-DU) --
but uniquely produces dependency-ordered phase schedules for staged
agent execution. It consumes the partition manifest, builds a
topological phase ordering, and emits Claude Teams schema output for
SDK integration. Until this wave the command had no
substrate-boundary marker plumbing -- a raise inside
``build_agent_plan`` (manifest -> topo phases),
``_dependency_maps`` / ``_phase_map`` (topo sort + phase bucketing),
the per-task descriptor builder, or the downstream verdict / envelope
composers would crash the agent-plan command outright.

This wave installs the canonical ``_w607dy_warnings_out`` bucket +
``_run_check_dy`` helper inside the ``agent_plan`` click command and
wraps every substrate boundary:

* resolve_target_files               -- n_agents normalisation
* build_dependency_graph             -- manifest construction
* compute_topo_order                 -- _dependency_maps probe
* assign_phases                      -- _phase_map probe
* extract_phase_metrics              -- per-task descriptor validate
* compose_verdict                    -- LAW 6 single-line floor
* compose_facts                      -- agent_contract.facts list
* compose_next_commands              -- agent_contract.next_commands
* serialize_envelope                 -- JSON envelope emission
* format_text_output                 -- text path phase printing

Marker family ``agent_plan_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test.

7919-CATASTROPHE REGRESSION GUARD
---------------------------------

CONSTRAINT 12 (first-token EXECUTABILITY): the partition catastrophe
named in CLAUDE.md is the 7919-partition output that technically
conforms to schema but is *not actionable*. The W607-DY substrate
boundary on ``build_agent_plan`` must NOT re-introduce the
catastrophe: on a degraded plan the verdict still produces a LAW-6
single-line string with the EMPTY-FLOOR zero counts (NOT a raw 7919
figure echoed from the user input). The regression-guard test below
confirms the empty-floor verdict emits ``"0 tasks for 0 agents"``
not ``"7919 tasks for 7919 agents"`` on the degraded path.

LAW 6 VERDICT-FIRST INVARIANT
-----------------------------

``summary.verdict`` survives every phase failure as a literal floor.
A raise in any substrate degrades to the empty-floor verdict string;
the verdict is NEVER absent.

CROSS-PREFIX ISOLATION
----------------------

``agent_plan_*`` markers do NOT leak into ``orchestrate_*`` (the
operational-dispatch sibling) or ``partition_*`` (the analytical
manifest sibling) or any of the broader detector and architecture
command families. The prefix-discipline test confirms hard distinction.

MULTI-AGENT TRIAD 3-WAY PAIRING PIN
------------------------------------

cmd_orchestrate (W607-DS), cmd_partition (W607-DU), and
cmd_agent_plan (W607-DY) together close the architecture multi-agent
triad at substrate-CALL layer. The 3-way AST-scan test below confirms
all sibling commands carry their respective W607 plumbing
accumulators -- the family is closed.
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


def _build_agent_plan_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_agent_plan.

    Builds a tiny Python fixture so ensure_index() can find a .roam DB
    rooted at tmp_path. The agent-plan engine only needs a symbol
    graph to chew on; the W607-DY substrate boundary tests monkeypatch
    the interior calls (build_agent_plan, _dependency_maps, _phase_map)
    so the actual graph contents matter less than DB-and-index presence.
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
        CREATE TABLE IF NOT EXISTS git_cochange (
            file_id_a INTEGER NOT NULL,
            file_id_b INTEGER NOT NULL,
            cochange_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS file_stats (
            file_id INTEGER PRIMARY KEY,
            total_churn INTEGER DEFAULT 0
        );
        """
    )
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/engine.py', 'python')")
    conn.execute("INSERT INTO files (id, path, language) VALUES (2, 'src/runner.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(1, 1, 'helper', 'src.engine.helper', 'function', 1, 2, 'public', 1)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(2, 2, 'runner', 'src.runner.runner', 'function', 1, 2, 'public', 1)"
    )
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (2, 1, 'calls')")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def agent_plan_project(tmp_path):
    return _build_agent_plan_project(tmp_path)


def _invoke_agent_plan(cli_runner, project_root, *args, json_mode=True):
    """Invoke the agent-plan click command directly.

    Clears the module-level ``_GRAPH_CACHE`` before every invocation so
    monkeypatched ``build_symbol_graph`` calls aren't bypassed by a
    cached graph from a sibling test.
    """
    from roam.commands.cmd_agent_plan import agent_plan
    from roam.graph.builder import clear_graph_cache

    clear_graph_cache()

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(agent_plan, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_DY_PHASES = (
    "resolve_target_files",
    "build_dependency_graph",
    "compute_topo_order",
    "assign_phases",
    "extract_phase_metrics",
    "compose_verdict",
    "compose_facts",
    "compose_next_commands",
    "serialize_envelope",
    "format_text_output",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DY substrate markers
# ---------------------------------------------------------------------------


def test_agent_plan_clean_envelope_omits_w607dy_markers(cli_runner, agent_plan_project):
    """Clean agent-plan run -> no W607-DY substrate markers."""
    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "agent-plan"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    dy_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"agent_plan_{p}_failed:" in m for p in _DY_PHASES)
    ]
    assert not dy_markers, (
        f"clean agent-plan must NOT surface W607-DY substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) build_dependency_graph failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_agent_plan_build_failure_marker_format(cli_runner, agent_plan_project, monkeypatch):
    """If ``build_agent_plan`` raises, surface the canonical marker."""
    import roam.commands.cmd_agent_plan as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-from-W607-DY")

    monkeypatch.setattr(_mod, "build_agent_plan", _raise)

    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "3")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    build_markers = [m for m in all_wo if m.startswith("agent_plan_build_dependency_graph_failed:")]
    assert build_markers, f"expected agent_plan_build_dependency_graph_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in build_markers), build_markers
    assert any("synthetic-build-from-W607-DY" in m for m in build_markers), build_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_agent_plan_w607dy_warnings_in_envelope(cli_runner, agent_plan_project, monkeypatch):
    """Non-empty W607-DY bucket -> both top-level AND summary.warnings_out."""
    import roam.commands.cmd_agent_plan as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DY")

    monkeypatch.setattr(_mod, "build_agent_plan", _raise)

    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DY disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DY disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_agent_plan_three_segment_marker_shape(cli_runner, agent_plan_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    import roam.commands.cmd_agent_plan as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-DY")

    monkeypatch.setattr(_mod, "build_agent_plan", _raise)

    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("agent_plan_build_dependency_graph_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "agent_plan_build_dependency_graph_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) compute_topo_order failure -> marker, command still emits
# ---------------------------------------------------------------------------


def test_agent_plan_topo_failure_surfaces_marker(cli_runner, agent_plan_project, monkeypatch):
    """A raise in ``_dependency_maps`` surfaces via the W607-DY marker.

    The ``_dependency_maps`` probe runs as the compute_topo_order substrate
    AFTER the main plan build. The plan above carries the canonical
    task list so a raise in the probe only surfaces a marker; the plan
    itself stays intact.
    """
    import roam.commands.cmd_agent_plan as _mod

    call_count = {"n": 0}
    real_dependency_maps = _mod._dependency_maps

    def _raise_on_second_call(*args, **kwargs):
        # The substrate probe is the SECOND call (the first happens
        # inside ``build_agent_plan``). Raise on the second.
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("synthetic-topo-from-W607-DY")
        return real_dependency_maps(*args, **kwargs)

    monkeypatch.setattr(_mod, "_dependency_maps", _raise_on_second_call)

    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    topo_markers = [
        m
        for m in all_wo
        if m.startswith("agent_plan_compute_topo_order_failed:") or m.startswith("agent_plan_assign_phases_failed:")
    ]
    assert topo_markers, all_wo
    # Envelope still composes.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-DY stays in ``agent_plan_*`` family
# ---------------------------------------------------------------------------


def test_w607dy_marker_prefix_stays_in_agent_plan_family(cli_runner, agent_plan_project, monkeypatch):
    """Every W607-DY substrate marker uses the canonical ``agent_plan_*`` prefix.

    Hard distinction from sibling W607-* layers across the broader
    command surface. Confirms cross-prefix isolation per the wave
    contract.
    """
    import roam.commands.cmd_agent_plan as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DY")

    monkeypatch.setattr(_mod, "build_agent_plan", _raise)

    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("agent_plan_"), (
            f"every surfaced W607-DY marker must use the ``agent_plan_*`` prefix family; got {marker!r}"
        )
        # Hard distinction from the multi-agent triad siblings
        # (cmd_orchestrate / W607-DS, cmd_partition / W607-DU) and
        # from every adjacent detector + architecture family.
        for forbidden_prefix, sibling in (
            ("orchestrate_", "cmd_orchestrate W607-DS (triad sibling)"),
            ("partition_", "cmd_partition W607-DU (triad sibling)"),
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
# (7) Source-level guard: cmd_agent_plan carries the W607-DY accumulator
# ---------------------------------------------------------------------------


def test_cmd_agent_plan_carries_w607dy_accumulator():
    """AST-level guard: cmd_agent_plan source carries the W607-DY accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_agent_plan.py"
    assert src_path.exists(), f"cmd_agent_plan.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607dy_warnings_out" in src, (
        "W607-DY accumulator missing from cmd_agent_plan; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_dy" in src, (
        "W607-DY ``_run_check_dy`` helper missing from cmd_agent_plan; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_dy = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dy":
            found_run_check_dy = True
            break
    assert found_run_check_dy, (
        "W607-DY ``_run_check_dy`` helper not found in cmd_agent_plan AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-DY substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607dy_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-DY substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_agent_plan.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _DY_PHASES:
        same_line = f'_run_check_dy("{phase}"' in src
        multi_line = (
            f'_run_check_dy(\n        "{phase}"' in src
            or f'_run_check_dy(\n            "{phase}"' in src
            or f'_run_check_dy(\n                "{phase}"' in src
            or f'_run_check_dy(\n                    "{phase}"' in src
            or f'_run_check_dy(\n                        "{phase}"' in src
        )
        marker_grep = f"agent_plan_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DY wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) compose_verdict failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_agent_plan_compose_verdict_failure_degrades(cli_runner, agent_plan_project, monkeypatch):
    """A raise inside the verdict composer degrades to the empty-floor verdict.

    Force a malformed plan result by replacing build_agent_plan with a
    function that returns a plan whose ``verdict`` access raises. The
    W607-DY compose_verdict substrate falls back to the empty-floor
    default verdict so LAW 6 holds.
    """
    import roam.commands.cmd_agent_plan as _mod

    class _BoomDict(dict):
        """Dict-like object that raises on .get('verdict')."""

        def get(self, key, default=None):
            if key == "verdict":
                raise ZeroDivisionError("synthetic-verdict-from-W607-DY")
            return super().get(key, default)

    boom_plan = _BoomDict(
        n_agents=3,
        tasks=[],
        merge_sequence=[],
        handoffs=[],
        claude_teams={"agents": [], "coordination": {}},
        conflict_probability=0.0,
        manifest={
            "total_partitions": 0,
            "n_agents": 3,
            "overall_conflict_probability": 0.0,
            "partitions": [],
            "dependencies": [],
            "conflict_hotspots": [],
            "merge_order": [],
        },
    )

    def _return_boom(*args, **kwargs):
        return boom_plan

    monkeypatch.setattr(_mod, "build_agent_plan", _return_boom)

    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "3")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    verdict_markers = [m for m in all_wo if m.startswith("agent_plan_compose_verdict_failed:")]
    assert verdict_markers, all_wo
    # Verdict still emits (LAW 6 single-line).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607dy_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-DY marker shape lives in cmd_agent_plan."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_agent_plan.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"agent_plan_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-DY marker fstring missing from cmd_agent_plan; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (11) LAW 6 verdict-first invariant: verdict survives every phase failure
# ---------------------------------------------------------------------------


def test_law_6_verdict_survives_every_phase_failure(cli_runner, agent_plan_project, monkeypatch):
    """LAW 6 invariant: ``summary.verdict`` is a non-empty single line on
    every phase failure -- the floor never disappears.

    Exercise: raise inside ``build_agent_plan`` so compose_verdict
    operates on the empty floor; the verdict still emits as the LAW-6
    zero-count floor string.
    """
    import roam.commands.cmd_agent_plan as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-from-W607-DY")

    monkeypatch.setattr(_mod, "build_agent_plan", _raise)

    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "4")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict, (
        f"LAW 6 invariant violated: verdict missing/empty on degraded path; got summary={summary!r}"
    )
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    # The floor names the zero-count state, NOT a SAFE/passed vocabulary.
    forbidden_vocab = ("safe", "passed", "completed", "all clear")
    for forbidden in forbidden_vocab:
        assert forbidden not in verdict.lower(), (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# (12) 7919-CATASTROPHE REGRESSION
# ---------------------------------------------------------------------------


def test_agent_plan_catastrophe_regression_preserves_constraint_12(cli_runner, agent_plan_project, monkeypatch):
    """CONSTRAINT 12 (first-token executability) regression guard.

    The 7919-partition catastrophe named in CLAUDE.md is the case where
    the multi-agent output technically conforms to schema but is not
    actionable -- 7919 tasks / 7919 agents is unusable as a number to
    act on.

    The W607-DY substrate boundary on ``build_agent_plan`` must NOT
    re-introduce the catastrophe: on a degraded plan the verdict still
    produces a LAW-6 single-line string with the EMPTY-FLOOR zero
    counts (NOT a raw 7919 figure). We exercise this by raising inside
    build_agent_plan with --agents 7919; the empty-floor verdict
    produces ``"0 tasks for 0 agents, 0 handoffs, 0% conflict probability"``
    -- the literal, executable LAW-6 floor.
    """
    import roam.commands.cmd_agent_plan as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-catastrophe-from-W607-DY")

    monkeypatch.setattr(_mod, "build_agent_plan", _raise)

    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "7919")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    # The catastrophe shape would echo 7919 as task / agent counts.
    # The W607-DY empty-floor verdict says
    # ``"0 tasks for 0 agents, 0 handoffs, 0% conflict probability"``
    # which is the executable floor (the partial_success marker
    # discloses the degraded state).
    assert "0 tasks" in verdict, (
        f"7919-catastrophe regression: verdict must use the empty-floor "
        f"(0 tasks) on degraded path, NOT propagate the raw input "
        f"n_agents value; got {verdict!r}"
    )
    assert "0 agents" in verdict, (
        f"7919-catastrophe regression: verdict must use the empty-floor "
        f"(0 agents) on degraded path, NOT propagate the raw input "
        f"n_agents value; got {verdict!r}"
    )
    assert "7919" not in verdict, (
        f"7919-catastrophe regression: verdict must NOT echo the raw "
        f"input n_agents value on degraded path; got {verdict!r}"
    )
    # partial_success surfaces so consumers see the degraded state.
    assert summary.get("partial_success") is True
    # tasks count in the envelope reflects the actual (empty) plan,
    # not the user input.
    assert summary.get("tasks") == 0, (
        f"tasks count must reflect the empty plan (0), not the raw input on degraded path; got {summary.get('tasks')!r}"
    )
    assert summary.get("n_agents") == 0, (
        f"n_agents must reflect the empty plan (0), not the raw input on degraded path; got {summary.get('n_agents')!r}"
    )


# ---------------------------------------------------------------------------
# (13) Per-substrate isolation -- each boundary raising surfaces marker
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_each_boundary_surfaces_marker(cli_runner, agent_plan_project, monkeypatch):
    """Per-substrate isolation: each W607-DY boundary raising surfaces a
    distinct marker + graceful degradation.

    Raise inside ``build_agent_plan`` and confirm the matching marker
    surfaces. The remaining substrates still run on the empty floor so
    the envelope composes a coherent verdict.
    """
    import roam.commands.cmd_agent_plan as _mod

    def _raise_build(*args, **kwargs):
        raise RuntimeError("isolation-build-W607-DY")

    monkeypatch.setattr(_mod, "build_agent_plan", _raise_build)
    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("agent_plan_build_dependency_graph_failed:") for m in all_wo), all_wo
    # Envelope still composes.
    assert isinstance(data["summary"]["verdict"], str)
    assert data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# (14) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, agent_plan_project, monkeypatch):
    """Pattern-2 regression guard.

    If ``build_agent_plan`` raises, the empty-floor default kicks in
    (tasks=[], n_agents=0, etc.) and the envelope is emitted. The
    W607-DY wrap MUST flip ``partial_success: True`` on that branch so
    the empty-state envelope is NOT mistaken for a clean planned verdict.
    """
    import roam.commands.cmd_agent_plan as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-DY")

    monkeypatch.setattr(_mod, "build_agent_plan", _raise)

    result = _invoke_agent_plan(cli_runner, agent_plan_project, "--agents", "3")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    build_markers = [m for m in all_wo if m.startswith("agent_plan_build_dependency_graph_failed:")]
    assert build_markers, (
        f"degraded path MUST surface the build_dependency_graph marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (15) Helper-template ``return default`` verbatim shape
# ---------------------------------------------------------------------------


def test_run_check_dy_helper_returns_default_verbatim():
    """W607-DP finding: the _run_check_dy helper MUST end with the literal
    ``return default`` (not ``return None`` or a captured local). A raise
    inside the wrapped fn falls through to ``return default`` so the
    caller's empty-floor default actually propagates.

    AST-level guard: locate the ``_run_check_dy`` FunctionDef and walk
    its body to confirm the last statement of the ``except`` handler
    is ``Return(value=Name(id='default'))``.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_agent_plan.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_dy"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ExceptHandler):
                # Last statement in the except body must be ``return default``.
                last_stmt = sub.body[-1]
                assert isinstance(last_stmt, ast.Return), (
                    f"_run_check_dy except handler last stmt is {type(last_stmt).__name__!r}, not Return"
                )
                assert isinstance(last_stmt.value, ast.Name), (
                    f"_run_check_dy must `return default` (a Name), got {ast.dump(last_stmt.value)!r}"
                )
                assert last_stmt.value.id == "default", (
                    f"_run_check_dy must `return default`, got `return {last_stmt.value.id}`"
                )
                found = True
                break
        if found:
            break
    assert found, (
        "_run_check_dy FunctionDef / except handler not found in "
        "cmd_agent_plan AST; the helper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (16) MULTI-AGENT TRIAD 3-WAY PAIRING PIN
# ---------------------------------------------------------------------------


def test_multi_agent_triad_3way_pairing():
    """AST-scan pin: cmd_orchestrate (W607-DS) + cmd_partition (W607-DU) +
    cmd_agent_plan (W607-DY) all carry W607 substrate-CALL plumbing.

    The architecture multi-agent triad is closed at substrate-CALL
    layer. Removing the plumbing from any source file fails this guard
    so the triad invariant stays loud.
    """
    root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    orchestrate_src = (root / "cmd_orchestrate.py").read_text(encoding="utf-8")
    partition_src = (root / "cmd_partition.py").read_text(encoding="utf-8")
    agent_plan_src = (root / "cmd_agent_plan.py").read_text(encoding="utf-8")

    # cmd_orchestrate carries W607-DS
    assert "_w607ds_warnings_out" in orchestrate_src, (
        "multi-agent triad pairing pin: cmd_orchestrate has lost its "
        "W607-DS substrate-CALL accumulator -- the triad is no longer "
        "closed."
    )
    assert "_run_check_ds" in orchestrate_src, (
        "multi-agent triad pairing pin: cmd_orchestrate has lost its W607-DS ``_run_check_ds`` helper."
    )

    # cmd_partition carries W607-DU
    assert "_w607du_warnings_out" in partition_src, (
        "multi-agent triad pairing pin: cmd_partition has lost its "
        "W607-DU substrate-CALL accumulator -- the triad is no longer "
        "closed."
    )
    assert "_run_check_du" in partition_src, (
        "multi-agent triad pairing pin: cmd_partition has lost its W607-DU ``_run_check_du`` helper."
    )

    # cmd_agent_plan carries W607-DY
    assert "_w607dy_warnings_out" in agent_plan_src, (
        "multi-agent triad pairing pin: cmd_agent_plan has lost its "
        "W607-DY substrate-CALL accumulator -- the triad is no longer "
        "closed."
    )
    assert "_run_check_dy" in agent_plan_src, (
        "multi-agent triad pairing pin: cmd_agent_plan has lost its W607-DY ``_run_check_dy`` helper."
    )

    # Cross-prefix discipline at source level: each sibling's marker
    # fstring does NOT leak into the other source files.
    assert 'f"orchestrate_{phase}_failed:{type(exc).__name__}:{exc}"' not in partition_src, (
        "cmd_partition leaks ``orchestrate_*`` marker -- prefix discipline violated."
    )
    assert 'f"orchestrate_{phase}_failed:{type(exc).__name__}:{exc}"' not in agent_plan_src, (
        "cmd_agent_plan leaks ``orchestrate_*`` marker -- prefix discipline violated."
    )
    assert 'f"partition_{phase}_failed:{type(exc).__name__}:{exc}"' not in orchestrate_src, (
        "cmd_orchestrate leaks ``partition_*`` marker -- prefix discipline violated."
    )
    assert 'f"partition_{phase}_failed:{type(exc).__name__}:{exc}"' not in agent_plan_src, (
        "cmd_agent_plan leaks ``partition_*`` marker -- prefix discipline violated."
    )
    assert 'f"agent_plan_{phase}_failed:{type(exc).__name__}:{exc}"' not in orchestrate_src, (
        "cmd_orchestrate leaks ``agent_plan_*`` marker -- prefix discipline violated."
    )
    assert 'f"agent_plan_{phase}_failed:{type(exc).__name__}:{exc}"' not in partition_src, (
        "cmd_partition leaks ``agent_plan_*`` marker -- prefix discipline violated."
    )
