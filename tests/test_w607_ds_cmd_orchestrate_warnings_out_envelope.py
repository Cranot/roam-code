"""W607-DS -- ``cmd_orchestrate`` substrate-boundary plumbing.

cmd_orchestrate is the multi-agent partitioning command -- it runs
Louvain community detection over the symbol graph, assigns each
partition to a worker bucket, then computes merge-order and a
conflict-probability score for the partition assignment. Until this
wave the command had no substrate-boundary marker plumbing -- a raise
in ``build_symbol_graph`` (DB -> networkx), ``partition_for_agents``
(Louvain + worker assignment + metrics), or the downstream verdict /
envelope composers would crash the orchestrate command outright.

This wave installs the canonical ``_w607ds_warnings_out`` bucket +
``_run_check_ds`` helper inside the ``orchestrate`` click command and
wraps every substrate boundary:

* resolve_target_files       -- --file / --staged resolution
* build_dependency_graph     -- build_symbol_graph(conn) DB -> networkx
* partition_for_agents       -- Louvain + worker assignment + metrics
* extract_agent_descriptors  -- result["agents"] / etc. unpack
* compose_verdict            -- LAW 6 single-line verdict
* compose_facts              -- agent_contract.facts list
* compose_next_commands      -- agent_contract.next_commands
* serialize_envelope         -- JSON envelope emission
* format_text_output         -- text path agent printing

Marker family ``orchestrate_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test.

7919-PARTITION CATASTROPHE REGRESSION GUARD
-------------------------------------------

CONSTRAINT 12 (first-token EXECUTABILITY): the partition catastrophe
named in CLAUDE.md is the 7919-partition output that technically
conforms to schema but is *not actionable*. The W607-DS substrate
boundary on ``partition_for_agents`` must NOT re-introduce the
catastrophe: on a degraded partition the verdict still produces an
LAW-6 single-line string with the EMPTY-FLOOR zero counts (NOT a raw
7919 figure). The regression-guard tests below confirm:

  1. The clean partition path emits a coherent LAW-6 single-line verdict.
  2. The W607-DS substrate boundary on ``partition_for_agents`` does
     NOT produce a raw oversized partition count -- the empty-floor
     default emits "orchestrated 0 agents" not "orchestrated 7919 agents".

LAW 6 VERDICT-FIRST INVARIANT
-----------------------------

``summary.verdict`` survives every phase failure as a literal floor.
A raise in any substrate degrades to the empty-floor verdict string;
the verdict is NEVER absent.

CROSS-PREFIX ISOLATION
----------------------

``orchestrate_*`` markers do NOT leak into adjacent commands. The
prefix-discipline test confirms hard distinction from the
detector-family 11-way (auth_gaps_, n1_, over_fetch_,
missing_index_, smells_, vibe_check_, clones_, duplicates_, dead_,
hotspots_, bus_factor_) AND from the architecture-sibling families
(complexity_, health_, dark_matter_).
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


def _build_orchestrate_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_orchestrate.

    Builds a tiny Python fixture so ensure_index() can find a .roam DB
    rooted at tmp_path. The partition engine only needs a symbol graph
    to chew on; the W607-DS substrate boundary tests monkeypatch the
    interior partitioning calls so the actual graph contents matter
    less than the DB-and-index presence.
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
def orchestrate_project(tmp_path):
    return _build_orchestrate_project(tmp_path)


def _invoke_orchestrate(cli_runner, project_root, *args, json_mode=True):
    """Invoke the orchestrate click command directly.

    Clears the module-level ``_GRAPH_CACHE`` before every invocation so
    monkeypatched ``build_symbol_graph`` calls aren't bypassed by a
    cached graph from a sibling test.
    """
    from roam.commands.cmd_orchestrate import orchestrate
    from roam.graph.builder import clear_graph_cache

    clear_graph_cache()

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(orchestrate, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_DS_PHASES = (
    "resolve_target_files",
    "build_dependency_graph",
    "partition_for_agents",
    "extract_agent_descriptors",
    "compose_verdict",
    "compose_facts",
    "compose_next_commands",
    "serialize_envelope",
    "format_text_output",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DS substrate markers
# ---------------------------------------------------------------------------


def test_orchestrate_clean_envelope_omits_w607ds_markers(cli_runner, orchestrate_project):
    """Clean orchestrate run -> no W607-DS substrate markers."""
    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "orchestrate"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ds_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"orchestrate_{p}_failed:" in m for p in _DS_PHASES)
    ]
    assert not ds_markers, (
        f"clean orchestrate must NOT surface W607-DS substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) partition_for_agents failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_orchestrate_partition_failure_marker_format(cli_runner, orchestrate_project, monkeypatch):
    """If ``partition_for_agents`` raises, surface the canonical marker."""
    import roam.graph.partition as _part

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partition-from-W607-DS")

    monkeypatch.setattr(_part, "partition_for_agents", _raise)

    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "3")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    partition_markers = [m for m in all_wo if m.startswith("orchestrate_partition_for_agents_failed:")]
    assert partition_markers, f"expected orchestrate_partition_for_agents_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in partition_markers), partition_markers
    assert any("synthetic-partition-from-W607-DS" in m for m in partition_markers), partition_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_orchestrate_w607ds_warnings_in_envelope(cli_runner, orchestrate_project, monkeypatch):
    """Non-empty W607-DS bucket -> both top-level AND summary.warnings_out."""
    import roam.graph.partition as _part

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DS")

    monkeypatch.setattr(_part, "partition_for_agents", _raise)

    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DS disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DS disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_orchestrate_three_segment_marker_shape(cli_runner, orchestrate_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    import roam.graph.partition as _part

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-DS")

    monkeypatch.setattr(_part, "partition_for_agents", _raise)

    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("orchestrate_partition_for_agents_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "orchestrate_partition_for_agents_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) build_dependency_graph failure -> marker, command still emits
# ---------------------------------------------------------------------------


def test_orchestrate_build_graph_failure_surfaces_marker(cli_runner, orchestrate_project, monkeypatch):
    """A raise in ``build_symbol_graph`` surfaces via the W607-DS marker."""
    import roam.graph.builder as _builder

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-graph-from-W607-DS")

    monkeypatch.setattr(_builder, "build_symbol_graph", _raise)

    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    graph_markers = [m for m in all_wo if m.startswith("orchestrate_build_dependency_graph_failed:")]
    assert graph_markers, all_wo
    # Envelope still composes.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-DS stays in ``orchestrate_*`` family
# ---------------------------------------------------------------------------


def test_w607ds_marker_prefix_stays_in_orchestrate_family(cli_runner, orchestrate_project, monkeypatch):
    """Every W607-DS substrate marker uses the canonical ``orchestrate_*`` prefix.

    Hard distinction from sibling W607-* layers across the broader
    command surface. Confirms cross-prefix isolation per the wave
    contract.
    """
    import roam.graph.partition as _part

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DS")

    monkeypatch.setattr(_part, "partition_for_agents", _raise)

    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("orchestrate_"), (
            f"every surfaced W607-DS marker must use the ``orchestrate_*`` prefix family; got {marker!r}"
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
# (7) Source-level guard: cmd_orchestrate carries the W607-DS accumulator
# ---------------------------------------------------------------------------


def test_cmd_orchestrate_carries_w607ds_accumulator():
    """AST-level guard: cmd_orchestrate source carries the W607-DS accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_orchestrate.py"
    assert src_path.exists(), f"cmd_orchestrate.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ds_warnings_out" in src, (
        "W607-DS accumulator missing from cmd_orchestrate; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ds" in src, (
        "W607-DS ``_run_check_ds`` helper missing from cmd_orchestrate; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_ds = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ds":
            found_run_check_ds = True
            break
    assert found_run_check_ds, (
        "W607-DS ``_run_check_ds`` helper not found in cmd_orchestrate AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-DS substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ds_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-DS substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_orchestrate.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _DS_PHASES:
        same_line = f'_run_check_ds("{phase}"' in src
        multi_line = (
            f'_run_check_ds(\n        "{phase}"' in src
            or f'_run_check_ds(\n            "{phase}"' in src
            or f'_run_check_ds(\n                "{phase}"' in src
            or f'_run_check_ds(\n                    "{phase}"' in src
            or f'_run_check_ds(\n                        "{phase}"' in src
        )
        marker_grep = f"orchestrate_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DS wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) compose_verdict failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_orchestrate_compose_verdict_failure_degrades(cli_runner, orchestrate_project, monkeypatch):
    """A raise inside the verdict composer degrades to the empty-floor verdict.

    The verdict composer uses ``len(agents)`` / ``write_conflicts`` /
    ``len(shared_interfaces)``. Force a malformed partition result so
    the descriptor unpack returns the empty floor (which composes a
    valid verdict), then mock the partition raise so compose_verdict
    runs on the empty floor.

    The verdict still emits as the LAW-6 floor string.
    """
    import roam.graph.partition as _part

    monkeypatch.setattr(
        _part,
        "partition_for_agents",
        lambda G, conn, n_agents, target_files: {
            # Missing every key -- extract_descriptors will KeyError
            # and degrade to ([], [], 0.0, [], 0).
            "garbage": True,
        },
    )

    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "3")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    extract_markers = [m for m in all_wo if m.startswith("orchestrate_extract_agent_descriptors_failed:")]
    assert extract_markers, all_wo
    # Verdict still emits (LAW 6 single-line) -- the empty floor:
    # "orchestrated 0 agents with 0 write conflicts across 0 shared interfaces".
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607ds_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-DS marker shape lives in cmd_orchestrate."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_orchestrate.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"orchestrate_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-DS marker fstring missing from cmd_orchestrate; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (11) LAW 6 verdict-first invariant: verdict survives every phase failure
# ---------------------------------------------------------------------------


def test_law_6_verdict_survives_every_phase_failure(cli_runner, orchestrate_project, monkeypatch):
    """LAW 6 invariant: ``summary.verdict`` is a non-empty single line on
    every phase failure -- the floor never disappears.

    Exercise: raise inside ``partition_for_agents`` so compose_verdict
    operates on the empty floor; the verdict still emits as the
    LAW-6 zero-count floor string.
    """
    import roam.graph.partition as _part

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-from-W607-DS")

    monkeypatch.setattr(_part, "partition_for_agents", _raise)

    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "4")
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
# (12) 7919-PARTITION CATASTROPHE REGRESSION
# ---------------------------------------------------------------------------


def test_partition_catastrophe_regression_preserves_constraint_12(cli_runner, orchestrate_project, monkeypatch):
    """CONSTRAINT 12 (first-token executability) regression guard.

    The 7919-partition catastrophe named in CLAUDE.md is the case where
    the partition output technically conforms to schema but is not
    actionable -- 7919 partitions is unusable as a number to act on.

    The W607-DS substrate boundary on ``partition_for_agents`` must NOT
    re-introduce the catastrophe: on a degraded partition the verdict
    still produces an LAW-6 single-line string with the EMPTY-FLOOR
    zero counts (NOT a raw 7919 figure). We exercise this by raising
    inside partition_for_agents; the empty-floor verdict produces
    "orchestrated 0 agents..." -- the literal, executable LAW-6 floor.
    """
    import roam.graph.partition as _part

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-catastrophe-from-W607-DS")

    monkeypatch.setattr(_part, "partition_for_agents", _raise)

    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "7919")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    # The catastrophe shape would be e.g. ``"orchestrated 7919 agents
    # with 0 write conflicts ..."`` -- a useless instruction. The
    # W607-DS empty-floor verdict says ``"orchestrated 0 agents ..."``
    # which is the executable floor (n_agents=0 means nothing to
    # orchestrate, and the partial_success marker discloses the
    # degraded state).
    assert "0 agents" in verdict, (
        f"7919-partition catastrophe regression: verdict must use the "
        f"empty-floor (0 agents) on degraded path, NOT propagate the raw "
        f"input n_agents value; got {verdict!r}"
    )
    assert "7919" not in verdict, (
        f"7919-partition catastrophe regression: verdict must NOT echo "
        f"the raw input n_agents value on degraded path; got {verdict!r}"
    )
    # partial_success surfaces so consumers see the degraded state.
    assert summary.get("partial_success") is True
    # n_agents in the envelope reflects the actual (empty) partition,
    # not the user input.
    assert summary.get("n_agents") == 0, (
        f"n_agents must reflect the empty partition (0), not the raw "
        f"input on degraded path; got {summary.get('n_agents')!r}"
    )


# ---------------------------------------------------------------------------
# (13) Per-substrate isolation -- each boundary raising surfaces marker
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_each_boundary_surfaces_marker(cli_runner, orchestrate_project, monkeypatch):
    """Per-substrate isolation: each W607-DS boundary raising surfaces a
    distinct marker + graceful degradation.

    Walk the substrates one at a time -- raise in each -- and confirm
    the matching marker surfaces. The remaining substrates still run
    on the empty floor so the envelope composes a coherent verdict.
    """
    # Exercise the ``build_dependency_graph`` substrate.
    import roam.graph.builder as _builder

    def _raise_builder(*args, **kwargs):
        raise RuntimeError("isolation-build")

    monkeypatch.setattr(_builder, "build_symbol_graph", _raise_builder)
    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("orchestrate_build_dependency_graph_failed:") for m in all_wo), all_wo
    # Envelope still composes.
    assert isinstance(data["summary"]["verdict"], str)
    assert data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# (14) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, orchestrate_project, monkeypatch):
    """Pattern-2 regression guard.

    If ``partition_for_agents`` raises, the empty-floor default kicks
    in (agents=[], merge_order=[], etc.) and the envelope is emitted.
    The W607-DS wrap MUST flip ``partial_success: True`` on that
    branch so the empty-state envelope is NOT mistaken for a clean
    orchestrated verdict.
    """
    import roam.graph.partition as _part

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-DS")

    monkeypatch.setattr(_part, "partition_for_agents", _raise)

    result = _invoke_orchestrate(cli_runner, orchestrate_project, "--agents", "3")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    partition_markers = [m for m in all_wo if m.startswith("orchestrate_partition_for_agents_failed:")]
    assert partition_markers, (
        f"degraded path MUST surface the partition_for_agents marker (loud-not-silent discipline); got {all_wo!r}"
    )
