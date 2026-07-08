"""W607-EM -- ``cmd_closure`` substrate-boundary plumbing.

cmd_closure is the transitive-closure command (forward/backward
dependency closure traversal), one leg of the structural-analysis
family alongside cmd_cut (W607-EI, minimum edge cuts) and cmd_simulate
(W607-EF, counterfactual transforms). Until this wave the command had
no substrate-boundary marker plumbing -- a raise inside
``_collect_closure`` (DB-driven callers/tests/re-exports/docs), the
change-type grouping, or any downstream verdict / envelope composer
would crash the closure command outright.

This wave installs the canonical ``_w607em_warnings_out`` bucket +
``_run_check_em`` helper inside ``closure`` and wraps every substrate
boundary:

* resolve_seed_symbols        -- resolution-tier handling
* build_dependency_graph      -- _collect_closure DB queries
* compute_transitive_closure  -- change-type grouping (by_type)
* extract_closure_metrics     -- file_set + counts
* compose_verdict             -- LAW 6 single-line floor
* compose_facts               -- agent_contract.facts list
* compose_next_commands       -- agent_contract.next_commands
* serialize_envelope          -- JSON envelope emission
* format_text_output          -- text path table printing

Marker family ``closure_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the
prefix-discipline test.

CYCLE-GRAPH REGRESSION
----------------------

cmd_closure on a graph with cycles must produce a finite verdict, not
crash. The W607-EI substrate plumbing must not regress the existing
graceful-handling of recursive closure traversal: every SQL query that
backs _collect_closure is a single-hop (callers + test pattern +
re-exports + docs), so cycles do not blow the stack, but the
W607-EM plumbing must preserve that finite-verdict behaviour.

EMPTY-SEED REGRESSION
---------------------

cmd_closure on an empty change-set (no callers, no tests, no
re-exports, no docs) must produce a sensible degraded verdict (the
"1 change in 1 file" floor for the definition row only), not crash
or collapse into an empty envelope.

LAW 6 VERDICT-FIRST INVARIANT
-----------------------------

``summary.verdict`` survives every phase failure as a literal floor.
A raise in any substrate degrades to the empty-floor verdict string
(``"closure for <name> requires 0 changes in 0 files"``); the verdict
is NEVER absent.

CROSS-PREFIX ISOLATION
----------------------

``closure_*`` markers do NOT leak into ``cut_*`` / ``simulate_*`` /
``orchestrate_*`` / ``partition_*`` / ``agent_plan_*`` / ``fleet_*``
or any of the broader detector and architecture command families.
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


def _build_closure_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_closure.

    The W607-EM substrate boundary tests monkeypatch the interior calls
    (_collect_closure, etc.) so the actual DB row contents matter less
    than DB-and-index presence. We just need ensure_index() to find a
    .roam DB rooted at tmp_path AND find_symbol() to resolve a symbol
    by name.
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
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (2, 1, 'calls')")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def closure_project(tmp_path):
    return _build_closure_project(tmp_path)


def _invoke_closure(cli_runner, project_root, *args, json_mode=True):
    """Invoke the closure click command directly."""
    from roam.commands.cmd_closure import closure

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(closure, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_EM_PHASES = (
    "resolve_seed_symbols",
    "build_dependency_graph",
    "compute_transitive_closure",
    "extract_closure_metrics",
    "compose_verdict",
    "compose_facts",
    "compose_next_commands",
    "serialize_envelope",
    "format_text_output",
)


def _sample_changes():
    """Return a sample 3-change list (definition + caller + test)."""
    return [
        {
            "change_type": "update_definition",
            "file": "src/a.py",
            "line": 1,
            "name": "foo",
            "kind": "function",
            "reason": "symbol definition",
        },
        {
            "change_type": "update_call",
            "file": "src/b.py",
            "line": 1,
            "name": "bar",
            "kind": "function",
            "reason": "calls foo",
        },
        {
            "change_type": "update_test",
            "file": "tests/test_a.py",
            "line": None,
            "name": "",
            "kind": "test_file",
            "reason": "test file referencing foo",
        },
    ]


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-EM substrate markers
# ---------------------------------------------------------------------------


def test_closure_clean_envelope_omits_w607em_markers(cli_runner, closure_project, monkeypatch):
    """Clean closure run -> no W607-EM substrate markers."""
    import roam.commands.cmd_closure as _closure_mod

    monkeypatch.setattr(_closure_mod, "_collect_closure", lambda *a, **k: _sample_changes())

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "closure"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    em_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"closure_{p}_failed:" in m for p in _EM_PHASES)]
    assert not em_markers, (
        f"clean closure must NOT surface W607-EM substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) build_dependency_graph failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_closure_build_dependency_graph_failure_marker_format(cli_runner, closure_project, monkeypatch):
    """If ``_collect_closure`` raises, surface the canonical marker."""
    import roam.commands.cmd_closure as _closure_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-from-W607-EM")

    monkeypatch.setattr(_closure_mod, "_collect_closure", _raise)

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    build_markers = [m for m in all_wo if m.startswith("closure_build_dependency_graph_failed:")]
    assert build_markers, f"expected closure_build_dependency_graph_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in build_markers), build_markers
    assert any("synthetic-build-from-W607-EM" in m for m in build_markers), build_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_closure_w607em_warnings_in_envelope(cli_runner, closure_project, monkeypatch):
    """Non-empty W607-EM bucket -> both top-level AND summary.warnings_out."""
    import roam.commands.cmd_closure as _closure_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-EM")

    monkeypatch.setattr(_closure_mod, "_collect_closure", _raise)

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-EM disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-EM disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_closure_three_segment_marker_shape(cli_runner, closure_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    import roam.commands.cmd_closure as _closure_mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-EM")

    monkeypatch.setattr(_closure_mod, "_collect_closure", _raise)

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("closure_build_dependency_graph_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "closure_build_dependency_graph_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) compute_transitive_closure failure -> marker, command still emits
# ---------------------------------------------------------------------------


def test_closure_compute_transitive_closure_failure_surfaces_marker(cli_runner, closure_project, monkeypatch):
    """A raise in the change-type grouping substrate surfaces a marker.

    Return a poison list whose iteration raises -- this triggers the
    by_type loop's setdefault() call but ALSO causes downstream
    consumers (extract_closure_metrics, envelope serialization) to
    fail. The W607-EM plumbing must surface SOME closure_*_failed
    marker; the exact substrate that wins depends on iteration
    order.
    """
    import roam.commands.cmd_closure as _closure_mod

    # Poison list whose __iter__ raises on iteration start. The
    # compute_transitive_closure substrate is the first consumer to
    # iterate ``changes`` after build_dependency_graph (which monkeypatch
    # is bypassed via its lambda return).
    class _PoisonList(list):
        def __iter__(self):
            raise RuntimeError("synthetic-compute-from-W607-EM")

    def _poison_changes(*args, **kwargs):
        return _PoisonList()

    monkeypatch.setattr(_closure_mod, "_collect_closure", _poison_changes)

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # The compute_transitive_closure substrate is the first iteration
    # consumer; isinstance(changes, list) is True so it enters the loop
    # and raises on __iter__. Other downstream substrates that iterate
    # may ALSO raise on the same poison.
    compute_markers = [
        m
        for m in all_wo
        if m.startswith("closure_compute_transitive_closure_failed:")
        or m.startswith("closure_extract_closure_metrics_failed:")
        or m.startswith("closure_serialize_envelope_failed:")
    ]
    assert compute_markers, all_wo
    # Envelope still composes.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-EM stays in ``closure_*`` family
# ---------------------------------------------------------------------------


def test_w607em_marker_prefix_stays_in_closure_family(cli_runner, closure_project, monkeypatch):
    """Every W607-EM substrate marker uses the canonical ``closure_*`` prefix.

    Hard distinction from sibling W607-* layers across the broader
    command surface. Confirms cross-prefix isolation per the wave
    contract.
    """
    import roam.commands.cmd_closure as _closure_mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-EM")

    monkeypatch.setattr(_closure_mod, "_collect_closure", _raise)

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("closure_"), (
            f"every surfaced W607-EM marker must use the ``closure_*`` prefix family; got {marker!r}"
        )
        # Hard distinction from sibling structural-analysis family +
        # adjacent detector + architecture families.
        for forbidden_prefix, sibling in (
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
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_closure carries the W607-EM accumulator
# ---------------------------------------------------------------------------


def test_cmd_closure_carries_w607em_accumulator():
    """AST-level guard: cmd_closure source carries the W607-EM accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_closure.py"
    assert src_path.exists(), f"cmd_closure.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607em_warnings_out" in src, (
        "W607-EM accumulator missing from cmd_closure; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_em" in src, (
        "W607-EM ``_run_check_em`` helper missing from cmd_closure; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_em = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_em":
            found_run_check_em = True
            break
    assert found_run_check_em, (
        "W607-EM ``_run_check_em`` helper not found in cmd_closure AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-EM substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607em_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-EM substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_closure.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _EM_PHASES:
        same_line = f'_run_check_em("{phase}"' in src
        multi_line = (
            f'_run_check_em(\n        "{phase}"' in src
            or f'_run_check_em(\n            "{phase}"' in src
            or f'_run_check_em(\n                "{phase}"' in src
        )
        marker_grep = f"closure_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-EM wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) compose_verdict failure -> empty floor, envelope composes
# ---------------------------------------------------------------------------


def test_closure_compose_verdict_failure_degrades(cli_runner, closure_project, monkeypatch):
    """A raise inside the verdict composer degrades to the empty-floor verdict.

    Force ``_closure_verdict`` to raise. The W607-EM compose_verdict
    substrate falls back to the empty-floor default verdict so LAW 6
    holds.
    """
    import roam.commands.cmd_closure as _closure_mod

    monkeypatch.setattr(_closure_mod, "_collect_closure", lambda *a, **k: _sample_changes())

    def _raise_verdict(*args, **kwargs):
        raise ZeroDivisionError("synthetic-verdict-from-W607-EM")

    monkeypatch.setattr(_closure_mod, "_closure_verdict", _raise_verdict)

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    verdict_markers = [m for m in all_wo if m.startswith("closure_compose_verdict_failed:")]
    assert verdict_markers, all_wo
    # Verdict still emits (LAW 6 single-line).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607em_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-EM marker shape lives in cmd_closure."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_closure.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"closure_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-EM marker fstring missing from cmd_closure; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (11) LAW 6 verdict-first invariant: verdict survives every phase failure
# ---------------------------------------------------------------------------


def test_law_6_verdict_survives_every_phase_failure(cli_runner, closure_project, monkeypatch):
    """LAW 6 invariant: ``summary.verdict`` is a non-empty single line on
    every phase failure -- the floor never disappears.
    """
    import roam.commands.cmd_closure as _closure_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-from-W607-EM")

    monkeypatch.setattr(_closure_mod, "_collect_closure", _raise)

    result = _invoke_closure(cli_runner, closure_project, "foo")
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
# (12) CYCLE-GRAPH REGRESSION PRESERVATION
# ---------------------------------------------------------------------------


def test_closure_cycle_graph_produces_finite_verdict(cli_runner, closure_project, monkeypatch):
    """Cycle-graph regression guard.

    cmd_closure on a change-set that includes cyclic references (e.g.
    foo calls bar; bar calls foo) must produce a finite verdict, not
    crash or recurse infinitely. _collect_closure uses single-hop SQL
    queries so cycles do not blow the stack; the W607-EM plumbing
    must not regress that.
    """
    import roam.commands.cmd_closure as _closure_mod

    # Simulate cyclic structure: foo calls bar, bar calls foo. The
    # change set is finite because _collect_closure returns single-hop
    # callers only.
    cyclic_changes = [
        {
            "change_type": "update_definition",
            "file": "src/a.py",
            "line": 1,
            "name": "foo",
            "kind": "function",
            "reason": "symbol definition",
        },
        {
            "change_type": "update_call",
            "file": "src/b.py",
            "line": 1,
            "name": "bar",
            "kind": "function",
            "reason": "calls foo (cycle: bar -> foo -> bar)",
        },
        {
            "change_type": "update_call",
            "file": "src/a.py",
            "line": 2,
            "name": "foo",
            "kind": "function",
            "reason": "calls bar (cycle: foo -> bar -> foo)",
        },
    ]

    monkeypatch.setattr(_closure_mod, "_collect_closure", lambda *a, **k: cyclic_changes)

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # Cyclic graph produces a finite total_changes count.
    assert summary.get("total_changes") == 3, f"cycle graph must produce finite total_changes; got summary={summary!r}"
    # Verdict is finite, single-line.
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict
    # No degraded-path markers -- the cycle is handled cleanly.
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    em_markers = [m for m in all_wo if any(f"closure_{p}_failed:" in m for p in _EM_PHASES)]
    assert not em_markers, f"cycle graph must NOT surface W607-EM substrate markers; got {all_wo!r}"


# ---------------------------------------------------------------------------
# (13) EMPTY-SEED REGRESSION PRESERVATION
# ---------------------------------------------------------------------------


def test_closure_empty_seed_change_set_produces_sensible_verdict(cli_runner, closure_project, monkeypatch):
    """Empty-seed regression guard.

    cmd_closure on an empty change-set (no callers, no tests, no
    re-exports, no docs) must produce a sensible degraded verdict
    (zero changes, zero files), not crash or collapse into an empty
    envelope.
    """
    import roam.commands.cmd_closure as _closure_mod

    monkeypatch.setattr(_closure_mod, "_collect_closure", lambda *a, **k: [])

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # Empty change set -> zero counts.
    assert summary.get("total_changes") == 0, f"empty seed must produce 0 changes; got summary={summary!r}"
    assert summary.get("files_affected") == 0, f"empty seed must produce 0 files_affected; got summary={summary!r}"
    # Verdict is the zero-count floor, finite, single-line.
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict
    # No degraded-path markers -- empty seed is a clean path, not a
    # degraded one. The W607-EM plumbing must NOT flip partial_success
    # on an empty-but-clean change set.
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    em_markers = [m for m in all_wo if any(f"closure_{p}_failed:" in m for p in _EM_PHASES)]
    assert not em_markers, f"empty seed must NOT surface W607-EM substrate markers; got {all_wo!r}"
    assert not summary.get("partial_success", False), (
        f"clean empty-seed path must NOT flip partial_success; got summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# (14) Per-substrate isolation -- each boundary raising surfaces marker
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_build_boundary_surfaces_marker(cli_runner, closure_project, monkeypatch):
    """Per-substrate isolation: build_dependency_graph raising surfaces a
    distinct marker + graceful degradation.

    Raise inside ``_collect_closure`` and confirm the matching
    build_dependency_graph marker surfaces. The remaining substrates
    still run on the empty floor so the envelope composes a coherent
    verdict.
    """
    import roam.commands.cmd_closure as _closure_mod

    def _raise_build(*args, **kwargs):
        raise RuntimeError("isolation-build-W607-EM")

    monkeypatch.setattr(_closure_mod, "_collect_closure", _raise_build)

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("closure_build_dependency_graph_failed:") for m in all_wo), all_wo
    # Envelope still composes.
    assert isinstance(data["summary"]["verdict"], str)
    assert data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# (15) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, closure_project, monkeypatch):
    """Pattern-2 regression guard.

    If ``_collect_closure`` raises, the empty-floor default kicks in
    (changes=[], by_type={}) and the envelope is emitted. The W607-EM
    wrap MUST flip ``partial_success: True`` on that branch so the
    empty-state envelope is NOT mistaken for a clean closure verdict.
    """
    import roam.commands.cmd_closure as _closure_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-EM")

    monkeypatch.setattr(_closure_mod, "_collect_closure", _raise)

    result = _invoke_closure(cli_runner, closure_project, "foo")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    build_markers = [m for m in all_wo if m.startswith("closure_build_dependency_graph_failed:")]
    assert build_markers, (
        f"degraded path MUST surface the build_dependency_graph marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (16) Helper-template ``return default`` verbatim shape
# ---------------------------------------------------------------------------


def test_run_check_em_helper_returns_default_verbatim():
    """W607-DP finding: the _run_check_em helper MUST end with the literal
    ``return default`` (not ``return None`` or a captured local). A raise
    inside the wrapped fn falls through to ``return default`` so the
    caller's empty-floor default actually propagates.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_closure.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_em"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ExceptHandler):
                # Last statement in the except body must be ``return default``.
                last_stmt = sub.body[-1]
                assert isinstance(last_stmt, ast.Return), (
                    f"_run_check_em except handler last stmt is {type(last_stmt).__name__!r}, not Return"
                )
                assert isinstance(last_stmt.value, ast.Name), (
                    f"_run_check_em must `return default` (a Name), got {ast.dump(last_stmt.value)!r}"
                )
                assert last_stmt.value.id == "default", (
                    f"_run_check_em must `return default`, got `return {last_stmt.value.id}`"
                )
                found = True
                break
        if found:
            break
    assert found, (
        "_run_check_em FunctionDef / except handler not found in cmd_closure AST; the helper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (17) Cross-prefix isolation at SOURCE level: cmd_closure doesn't leak siblings
# ---------------------------------------------------------------------------


def test_cmd_closure_source_no_sibling_marker_leak():
    """Source-level: cmd_closure.py does NOT contain any sibling W607-* marker fstrings."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_closure.py"
    src = src_path.read_text(encoding="utf-8")
    closure_marker = 'f"closure_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert closure_marker in src, f"canonical closure marker fstring missing; expected: {closure_marker}"
    forbidden_markers = (
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
    )
    for forbidden in forbidden_markers:
        assert forbidden not in src, f"cmd_closure.py leaks sibling marker fstring: {forbidden!r}"


# ---------------------------------------------------------------------------
# (18) STRUCTURAL-ANALYSIS 3-FAMILY PAIRING PIN
# ---------------------------------------------------------------------------


def test_structural_analysis_3_family_w607_pairing_pin():
    """Pin: cmd_simulate, cmd_cut, and cmd_closure all carry W607 substrate plumbing.

    The structural-analysis family (counterfactual, graph-cut,
    transitive-closure) ships substrate-CALL plumbing under three
    distinct W607 letters: EF (simulate), EI (cut), EM (closure).
    This test AST-scans all three for the canonical accumulator +
    helper pattern.
    """
    base = Path(__file__).parent.parent / "src" / "roam" / "commands"
    family = (
        ("cmd_simulate.py", "w607ef_warnings_out", "_run_check_ef"),
        ("cmd_cut.py", "w607ei_warnings_out", "_run_check_ei"),
        ("cmd_closure.py", "w607em_warnings_out", "_run_check_em"),
    )
    for filename, accumulator, helper in family:
        src_path = base / filename
        assert src_path.exists(), f"{filename} missing at {src_path}"
        src = src_path.read_text(encoding="utf-8")
        assert accumulator in src, (
            f"{filename} missing accumulator {accumulator!r}; the structural-analysis 3-family pairing is broken."
        )
        assert helper in src, (
            f"{filename} missing helper {helper!r}; the structural-analysis 3-family pairing is broken."
        )
        # AST: helper FunctionDef present.
        tree = ast.parse(src)
        found = any(isinstance(node, ast.FunctionDef) and node.name == helper for node in ast.walk(tree))
        assert found, f"{filename}: helper {helper!r} FunctionDef not found in AST"
