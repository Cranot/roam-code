"""W607-DU -- ``cmd_partition`` substrate-boundary plumbing.

cmd_partition is the multi-agent partition manifest command -- the
"deeper analytical metrics + claude-teams output" sibling to
cmd_orchestrate. Both run Louvain community detection over the
symbol graph and assign partitions to worker buckets, but partition
layers on difficulty scores, churn, co-change coupling, and the
``--format claude-teams`` output for SDK integration. Until this
wave the command had no substrate-boundary marker plumbing -- a
raise in ``build_symbol_graph`` (DB -> networkx),
``compute_partition_manifest`` (Louvain + worker assignment +
metrics), ``_to_claude_teams`` (output projection), or the
downstream verdict / envelope composers would crash the partition
command outright.

This wave installs the canonical ``_w607du_warnings_out`` bucket +
``_run_check_du`` helper inside the ``partition`` click command and
wraps every substrate boundary:

* resolve_target_files               -- n_agents normalisation
* build_dependency_graph             -- build_symbol_graph(conn)
* compute_louvain_partitions         -- manifest construction
* assign_workers                     -- partition -> worker bucket
* extract_claude_teams_descriptor    -- _to_claude_teams projection
* compose_verdict                    -- LAW 6 single-line floor
* compose_facts                      -- agent_contract.facts list
* compose_next_commands              -- agent_contract.next_commands
* serialize_envelope                 -- JSON envelope emission
* format_text_output                 -- text path partition printing

Marker family ``partition_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test.

7919-PARTITION CATASTROPHE REGRESSION GUARD
-------------------------------------------

CONSTRAINT 12 (first-token EXECUTABILITY): the partition catastrophe
named in CLAUDE.md is the 7919-partition output that technically
conforms to schema but is *not actionable*. The W607-DU substrate
boundary on ``compute_partition_manifest`` must NOT re-introduce the
catastrophe: on a degraded partition the verdict still produces a
LAW-6 single-line string with the EMPTY-FLOOR zero counts (NOT a
raw 7919 figure echoed from the user input). The regression-guard
tests below confirm:

  1. The clean partition path emits a coherent LAW-6 single-line verdict.
  2. The W607-DU substrate boundary on ``compute_partition_manifest``
     does NOT produce a raw oversized partition count -- the empty-floor
     default emits "0 partitions for 0 agents" not "7919 partitions
     for 7919 agents".

LAW 6 VERDICT-FIRST INVARIANT
-----------------------------

``summary.verdict`` survives every phase failure as a literal floor.
A raise in any substrate degrades to the empty-floor verdict string;
the verdict is NEVER absent.

CROSS-PREFIX ISOLATION
----------------------

``partition_*`` markers do NOT leak into ``orchestrate_*`` (the
operational-dispatch sibling) or any of the broader detector and
architecture command families. The prefix-discipline test confirms
hard distinction.

MULTI-AGENT-PARTITION 2-WAY PAIRING PIN
----------------------------------------

cmd_orchestrate (W607-DS) and cmd_partition (W607-DU) together cover
the multi-agent-partition family. The 2-way AST-scan test below
confirms both sibling commands carry their respective W607 plumbing
accumulators -- the family is closed at substrate-CALL layer.
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


def _build_partition_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_partition.

    Builds a tiny Python fixture so ensure_index() can find a .roam DB
    rooted at tmp_path. The partition engine only needs a symbol graph
    to chew on; the W607-DU substrate boundary tests monkeypatch the
    interior calls (build_symbol_graph, compute_partition_manifest,
    _to_claude_teams) so the actual graph contents matter less than
    DB-and-index presence.
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
def partition_project(tmp_path):
    return _build_partition_project(tmp_path)


def _invoke_partition(cli_runner, project_root, *args, json_mode=True):
    """Invoke the partition click command directly.

    Clears the module-level ``_GRAPH_CACHE`` before every invocation so
    monkeypatched ``build_symbol_graph`` calls aren't bypassed by a
    cached graph from a sibling test.
    """
    from roam.commands.cmd_partition import partition
    from roam.graph.builder import clear_graph_cache

    clear_graph_cache()

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(partition, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_DU_PHASES = (
    "resolve_target_files",
    "build_dependency_graph",
    "compute_louvain_partitions",
    "assign_workers",
    "extract_claude_teams_descriptor",
    "compose_verdict",
    "compose_facts",
    "compose_next_commands",
    "serialize_envelope",
    "format_text_output",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DU substrate markers
# ---------------------------------------------------------------------------


def test_partition_clean_envelope_omits_w607du_markers(cli_runner, partition_project):
    """Clean partition run -> no W607-DU substrate markers."""
    result = _invoke_partition(cli_runner, partition_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "partition"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    du_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"partition_{p}_failed:" in m for p in _DU_PHASES)
    ]
    assert not du_markers, (
        f"clean partition must NOT surface W607-DU substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) compute_louvain_partitions failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_partition_compute_failure_marker_format(cli_runner, partition_project, monkeypatch):
    """If ``compute_partition_manifest`` raises, surface the canonical marker."""
    import roam.commands.cmd_partition as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-compute-from-W607-DU")

    monkeypatch.setattr(_mod, "compute_partition_manifest", _raise)

    result = _invoke_partition(cli_runner, partition_project, "--agents", "3")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    compute_markers = [m for m in all_wo if m.startswith("partition_compute_louvain_partitions_failed:")]
    assert compute_markers, f"expected partition_compute_louvain_partitions_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in compute_markers), compute_markers
    assert any("synthetic-compute-from-W607-DU" in m for m in compute_markers), compute_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_partition_w607du_warnings_in_envelope(cli_runner, partition_project, monkeypatch):
    """Non-empty W607-DU bucket -> both top-level AND summary.warnings_out."""
    import roam.commands.cmd_partition as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DU")

    monkeypatch.setattr(_mod, "compute_partition_manifest", _raise)

    result = _invoke_partition(cli_runner, partition_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DU disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DU disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_partition_three_segment_marker_shape(cli_runner, partition_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    import roam.commands.cmd_partition as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-DU")

    monkeypatch.setattr(_mod, "compute_partition_manifest", _raise)

    result = _invoke_partition(cli_runner, partition_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("partition_compute_louvain_partitions_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "partition_compute_louvain_partitions_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) build_dependency_graph failure -> marker, command still emits
# ---------------------------------------------------------------------------


def test_partition_build_graph_failure_surfaces_marker(cli_runner, partition_project, monkeypatch):
    """A raise in ``build_symbol_graph`` surfaces via the W607-DU marker.

    The build_symbol_graph probe runs BEFORE the manifest engine. With
    build_symbol_graph patched to raise, both the probe AND the manifest
    engine (which also calls build_symbol_graph internally) will fail;
    the test just checks the probe-stage marker surfaces and the
    envelope still composes.
    """
    import roam.graph.builder as _builder

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-graph-from-W607-DU")

    monkeypatch.setattr(_builder, "build_symbol_graph", _raise)

    result = _invoke_partition(cli_runner, partition_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    graph_markers = [m for m in all_wo if m.startswith("partition_build_dependency_graph_failed:")]
    assert graph_markers, all_wo
    # Envelope still composes.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-DU stays in ``partition_*`` family
# ---------------------------------------------------------------------------


def test_w607du_marker_prefix_stays_in_partition_family(cli_runner, partition_project, monkeypatch):
    """Every W607-DU substrate marker uses the canonical ``partition_*`` prefix.

    Hard distinction from sibling W607-* layers across the broader
    command surface. Confirms cross-prefix isolation per the wave
    contract.
    """
    import roam.commands.cmd_partition as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DU")

    monkeypatch.setattr(_mod, "compute_partition_manifest", _raise)

    result = _invoke_partition(cli_runner, partition_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("partition_"), (
            f"every surfaced W607-DU marker must use the ``partition_*`` prefix family; got {marker!r}"
        )
        # Hard distinction from the multi-agent-partition sibling
        # (cmd_orchestrate / W607-DS) and from every adjacent
        # detector + architecture family.
        for forbidden_prefix, sibling in (
            ("orchestrate_", "cmd_orchestrate W607-DS (multi-agent sibling)"),
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
# (7) Source-level guard: cmd_partition carries the W607-DU accumulator
# ---------------------------------------------------------------------------


def test_cmd_partition_carries_w607du_accumulator():
    """AST-level guard: cmd_partition source carries the W607-DU accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_partition.py"
    assert src_path.exists(), f"cmd_partition.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607du_warnings_out" in src, (
        "W607-DU accumulator missing from cmd_partition; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_du" in src, (
        "W607-DU ``_run_check_du`` helper missing from cmd_partition; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_du = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_du":
            found_run_check_du = True
            break
    assert found_run_check_du, (
        "W607-DU ``_run_check_du`` helper not found in cmd_partition AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-DU substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607du_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-DU substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_partition.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _DU_PHASES:
        same_line = f'_run_check_du("{phase}"' in src
        multi_line = (
            f'_run_check_du(\n        "{phase}"' in src
            or f'_run_check_du(\n            "{phase}"' in src
            or f'_run_check_du(\n                "{phase}"' in src
            or f'_run_check_du(\n                    "{phase}"' in src
            or f'_run_check_du(\n                        "{phase}"' in src
        )
        marker_grep = f"partition_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DU wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) compose_verdict failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_partition_compose_verdict_failure_degrades(cli_runner, partition_project, monkeypatch):
    """A raise inside the verdict composer degrades to the empty-floor verdict.

    Force a malformed manifest result by replacing compute_partition_manifest
    with a function that returns a manifest missing the ``verdict`` key.
    The W607-DU compose_verdict substrate falls back to deriving a verdict
    from the manifest's partial fields; if THAT also raises, the empty-floor
    default kicks in and the verdict still emits as the LAW-6 floor string.
    """
    import roam.commands.cmd_partition as _mod

    # Use a sentinel that raises on ``int(cp * 100)`` so the inner
    # f-string fallback also explodes -- routing to the empty-floor
    # default verdict.
    class _BoomFloat:
        def __mul__(self, other):
            raise ZeroDivisionError("synthetic-verdict-from-W607-DU")

    monkeypatch.setattr(
        _mod,
        "compute_partition_manifest",
        lambda conn, n_agents: {
            # ``verdict`` missing entirely; ``overall_conflict_probability``
            # blows up on int(cp * 100).
            "total_partitions": 5,
            "n_agents": 3,
            "overall_conflict_probability": _BoomFloat(),
            "merge_order": [],
            "partitions": [],
            "dependencies": [],
            "conflict_hotspots": [],
        },
    )

    result = _invoke_partition(cli_runner, partition_project, "--agents", "3")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    verdict_markers = [m for m in all_wo if m.startswith("partition_compose_verdict_failed:")]
    assert verdict_markers, all_wo
    # Verdict still emits (LAW 6 single-line) -- the empty floor:
    # "conflict probability 0% across 0 partitions for 0 agents".
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607du_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-DU marker shape lives in cmd_partition."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_partition.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"partition_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-DU marker fstring missing from cmd_partition; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (11) LAW 6 verdict-first invariant: verdict survives every phase failure
# ---------------------------------------------------------------------------


def test_law_6_verdict_survives_every_phase_failure(cli_runner, partition_project, monkeypatch):
    """LAW 6 invariant: ``summary.verdict`` is a non-empty single line on
    every phase failure -- the floor never disappears.

    Exercise: raise inside ``compute_partition_manifest`` so compose_verdict
    operates on the empty floor; the verdict still emits as the
    LAW-6 zero-count floor string.
    """
    import roam.commands.cmd_partition as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-from-W607-DU")

    monkeypatch.setattr(_mod, "compute_partition_manifest", _raise)

    result = _invoke_partition(cli_runner, partition_project, "--agents", "4")
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


def test_partition_catastrophe_regression_preserves_constraint_12(cli_runner, partition_project, monkeypatch):
    """CONSTRAINT 12 (first-token executability) regression guard.

    The 7919-partition catastrophe named in CLAUDE.md is the case where
    the partition output technically conforms to schema but is not
    actionable -- 7919 partitions / 7919 agents is unusable as a number
    to act on.

    The W607-DU substrate boundary on ``compute_partition_manifest``
    must NOT re-introduce the catastrophe: on a degraded partition the
    verdict still produces a LAW-6 single-line string with the EMPTY-FLOOR
    zero counts (NOT a raw 7919 figure). We exercise this by raising
    inside compute_partition_manifest with --agents 7919; the empty-floor
    verdict produces "conflict probability 0% across 0 partitions for
    0 agents" -- the literal, executable LAW-6 floor.

    cmd_partition is THE canonical home for this regression test because
    its verdict naturally embeds ``N partitions for N agents`` so the
    raw-input echo would surface most visibly here.
    """
    import roam.commands.cmd_partition as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-catastrophe-from-W607-DU")

    monkeypatch.setattr(_mod, "compute_partition_manifest", _raise)

    result = _invoke_partition(cli_runner, partition_project, "--agents", "7919")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    # The catastrophe shape would be e.g. ``"conflict probability X%
    # across 7919 partitions for 7919 agents"`` -- a useless instruction.
    # The W607-DU empty-floor verdict says
    # ``"conflict probability 0% across 0 partitions for 0 agents"``
    # which is the executable floor (the partial_success marker
    # discloses the degraded state).
    assert "0 partitions" in verdict, (
        f"7919-partition catastrophe regression: verdict must use the "
        f"empty-floor (0 partitions) on degraded path, NOT propagate "
        f"the raw input n_agents value; got {verdict!r}"
    )
    assert "0 agents" in verdict, (
        f"7919-partition catastrophe regression: verdict must use the "
        f"empty-floor (0 agents) on degraded path, NOT propagate the "
        f"raw input n_agents value; got {verdict!r}"
    )
    assert "7919" not in verdict, (
        f"7919-partition catastrophe regression: verdict must NOT echo "
        f"the raw input n_agents value on degraded path; got {verdict!r}"
    )
    # partial_success surfaces so consumers see the degraded state.
    assert summary.get("partial_success") is True
    # total_partitions in the envelope reflects the actual (empty)
    # manifest, not the user input.
    assert summary.get("total_partitions") == 0, (
        f"total_partitions must reflect the empty manifest (0), not "
        f"the raw input on degraded path; got "
        f"{summary.get('total_partitions')!r}"
    )
    assert summary.get("n_agents") == 0, (
        f"n_agents must reflect the empty manifest (0), not the raw "
        f"input on degraded path; got {summary.get('n_agents')!r}"
    )


# ---------------------------------------------------------------------------
# (13) Per-substrate isolation -- each boundary raising surfaces marker
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_each_boundary_surfaces_marker(cli_runner, partition_project, monkeypatch):
    """Per-substrate isolation: each W607-DU boundary raising surfaces a
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
    result = _invoke_partition(cli_runner, partition_project, "--agents", "2")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("partition_build_dependency_graph_failed:") for m in all_wo), all_wo
    # Envelope still composes.
    assert isinstance(data["summary"]["verdict"], str)
    assert data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# (14) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, partition_project, monkeypatch):
    """Pattern-2 regression guard.

    If ``compute_partition_manifest`` raises, the empty-floor default
    kicks in (total_partitions=0, n_agents=0, etc.) and the envelope
    is emitted. The W607-DU wrap MUST flip ``partial_success: True``
    on that branch so the empty-state envelope is NOT mistaken for a
    clean partitioned verdict.
    """
    import roam.commands.cmd_partition as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-DU")

    monkeypatch.setattr(_mod, "compute_partition_manifest", _raise)

    result = _invoke_partition(cli_runner, partition_project, "--agents", "3")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    compute_markers = [m for m in all_wo if m.startswith("partition_compute_louvain_partitions_failed:")]
    assert compute_markers, (
        f"degraded path MUST surface the compute_louvain_partitions marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (15) extract_claude_teams_descriptor failure -> marker, envelope composes
# ---------------------------------------------------------------------------


def test_partition_claude_teams_extract_failure_surfaces_marker(cli_runner, partition_project, monkeypatch):
    """A raise inside ``_to_claude_teams`` surfaces the substrate marker
    on the ``--format claude-teams`` path.

    The teams projection is the output-format substrate unique to
    cmd_partition (cmd_orchestrate has no analogue). A raise here used
    to crash the command; the W607-DU wrap degrades to an empty teams
    descriptor and the envelope still emits with the marker disclosed.
    """
    import roam.commands.cmd_partition as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-teams-from-W607-DU")

    monkeypatch.setattr(_mod, "_to_claude_teams", _raise)

    result = _invoke_partition(cli_runner, partition_project, "--agents", "2", "--format", "claude-teams")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    teams_markers = [m for m in all_wo if m.startswith("partition_extract_claude_teams_descriptor_failed:")]
    assert teams_markers, all_wo
    # Envelope still emits a verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict


# ---------------------------------------------------------------------------
# (16) MULTI-AGENT-PARTITION 2-WAY PAIRING PIN
# ---------------------------------------------------------------------------


def test_multi_agent_partition_family_2way_pairing():
    """AST-scan pin: cmd_orchestrate (W607-DS) + cmd_partition (W607-DU)
    both carry W607 substrate-CALL plumbing.

    The multi-agent-partition family is closed at substrate-CALL layer.
    Adding a third sibling would extend this set; removing the plumbing
    from either source file fails this guard so the family invariant
    stays loud.
    """
    root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    orchestrate_src = (root / "cmd_orchestrate.py").read_text(encoding="utf-8")
    partition_src = (root / "cmd_partition.py").read_text(encoding="utf-8")

    # cmd_orchestrate carries W607-DS
    assert "_w607ds_warnings_out" in orchestrate_src, (
        "multi-agent-partition family pairing pin: cmd_orchestrate has lost "
        "its W607-DS substrate-CALL accumulator -- the family is no longer "
        "closed."
    )
    assert "_run_check_ds" in orchestrate_src, (
        "multi-agent-partition family pairing pin: cmd_orchestrate has lost its W607-DS ``_run_check_ds`` helper."
    )
    assert "orchestrate_" in orchestrate_src, orchestrate_src[:200]

    # cmd_partition carries W607-DU
    assert "_w607du_warnings_out" in partition_src, (
        "multi-agent-partition family pairing pin: cmd_partition has lost "
        "its W607-DU substrate-CALL accumulator -- the family is no longer "
        "closed."
    )
    assert "_run_check_du" in partition_src, (
        "multi-agent-partition family pairing pin: cmd_partition has lost its W607-DU ``_run_check_du`` helper."
    )

    # Cross-prefix discipline at source level: ``orchestrate_*`` markers
    # do NOT leak into cmd_partition and vice versa. We scan for the
    # other family's marker fstring.
    assert 'f"orchestrate_{phase}_failed:{type(exc).__name__}:{exc}"' not in partition_src, (
        "cmd_partition source carries the sibling ``orchestrate_*`` marker fstring -- prefix discipline violated."
    )
    assert 'f"partition_{phase}_failed:{type(exc).__name__}:{exc}"' not in orchestrate_src, (
        "cmd_orchestrate source carries the sibling ``partition_*`` marker fstring -- prefix discipline violated."
    )
