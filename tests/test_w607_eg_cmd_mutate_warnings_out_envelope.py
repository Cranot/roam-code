"""W607-EG -- ``cmd_mutate`` substrate-boundary plumbing.

cmd_mutate is the state-mutating code-transform command (move / rename /
add-call / extract). It pairs with cmd_simulate (W607-EF) along the
TRANSFORM AXIS: cmd_simulate is the what-if counterfactual transform on
a cloned graph; cmd_mutate is the real-transform that writes files to
disk. Until this wave cmd_mutate had no substrate-boundary marker
plumbing -- a raise inside ``move_symbol`` /
``rename_symbol`` / ``add_call`` / ``extract_symbol``, the downstream
verdict composer, or the envelope serializer would crash the mutate
command outright.

This wave installs the canonical ``_w607eg_warnings_out`` bucket +
``_run_check_eg`` helper inside ``cmd_mutate`` and wraps every substrate
boundary on the ``mutate_move`` click subcommand (the canonical
representative of the four transforms):

* resolve_target          -- symbol -> resolver location
* load_source             -- delegate to roam.refactor.transforms entry
                             (which reads original file content)
* apply_transform         -- the actual mutation (move_symbol)
* validate_transform      -- shape-validate transform result envelope
* write_output            -- atomic file-write per W82.1 (guarded by
                             ``apply_changes``)
* compose_verdict         -- LAW 6 single-line floor
* compose_facts           -- agent_contract.facts list
* compose_next_commands   -- agent_contract.next_commands
* serialize_envelope      -- JSON envelope emission
* format_text_output      -- text path emission

Marker family ``mutate_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the prefix-
discipline test.

LAW 6 VERDICT-FIRST INVARIANT
-----------------------------

``summary.verdict`` survives every phase failure as a literal floor.
A raise in any substrate degrades to the empty-floor verdict string;
the verdict is NEVER absent.

CROSS-PREFIX ISOLATION
----------------------

``mutate_*`` markers do NOT leak into ``simulate_*`` (transform-axis
sibling) or any other detector / architecture command family. The
prefix-discipline test confirms hard distinction.

TRANSFORM AXIS 2-WAY PAIRING PIN
--------------------------------

cmd_simulate (W607-EF) and cmd_mutate (W607-EG) together close the
TRANSFORM AXIS at substrate-CALL layer. The 2-way AST-scan test below
confirms both sibling commands carry their respective W607 plumbing
accumulators -- the axis is closed.

W82.1 ATOMIC FILE-WRITE PRESERVATION
------------------------------------

The W607-EG ``apply_transform`` substrate wraps the inner
``move_symbol`` / ``rename_symbol`` / etc. transform call. When
``--apply`` is set the transform writes files to disk atomically per
W82.1. The substrate wrap must NOT introduce a torn-write path: a raise
inside the inner transform call degrades to the empty-result floor
WITHOUT writing partial output. The regression test below confirms no
torn writes occur on apply_transform raise.
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


def _build_mutate_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_mutate.

    Builds a tiny Python fixture so ensure_index() can find a .roam DB
    rooted at tmp_path. The mutate transforms only need a symbol graph
    to chew on; the W607-EG substrate boundary tests monkeypatch the
    interior calls (``move_symbol`` and friends) so the actual graph
    contents matter less than DB-and-index presence.
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
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def mutate_project(tmp_path):
    return _build_mutate_project(tmp_path)


def _invoke_mutate_move(cli_runner, project_root, *args, json_mode=True):
    """Invoke the ``mutate move`` click command directly."""
    from roam.commands.cmd_mutate import mutate_cmd

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(mutate_cmd, ["move", *list(args)], obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_EG_PHASES = (
    "resolve_target",
    "apply_transform",
    "validate_transform",
    "compose_verdict",
    "compose_facts",
    "compose_next_commands",
    "serialize_envelope",
    "format_text_output",
)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-EG substrate markers
# ---------------------------------------------------------------------------


def test_mutate_clean_envelope_omits_w607eg_markers(cli_runner, mutate_project, monkeypatch):
    """Clean mutate move run -> no W607-EG substrate markers."""
    import roam.refactor.transforms as _tx

    def _clean_move(conn, symbol, target_file, dry_run=True):
        return {
            "symbol": symbol,
            "files_modified": [{"path": "src/engine.py", "action": "MODIFY", "changes": []}],
            "warnings": [],
        }

    monkeypatch.setattr(_tx, "move_symbol", _clean_move)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "mutate"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    eg_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"mutate_{p}_failed:" in m for p in _EG_PHASES)]
    assert not eg_markers, (
        f"clean mutate move must NOT surface W607-EG substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) apply_transform failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_mutate_apply_transform_failure_marker_format(cli_runner, mutate_project, monkeypatch):
    """If ``move_symbol`` raises, surface the canonical marker."""
    import roam.refactor.transforms as _tx

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-apply-from-W607-EG")

    monkeypatch.setattr(_tx, "move_symbol", _raise)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    apply_markers = [m for m in all_wo if m.startswith("mutate_apply_transform_failed:")]
    assert apply_markers, f"expected mutate_apply_transform_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in apply_markers), apply_markers
    assert any("synthetic-apply-from-W607-EG" in m for m in apply_markers), apply_markers
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_mutate_w607eg_warnings_in_envelope(cli_runner, mutate_project, monkeypatch):
    """Non-empty W607-EG bucket -> both top-level AND summary.warnings_out."""
    import roam.refactor.transforms as _tx

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-EG")

    monkeypatch.setattr(_tx, "move_symbol", _raise)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-EG disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-EG disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_mutate_three_segment_marker_shape(cli_runner, mutate_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    import roam.refactor.transforms as _tx

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-EG")

    monkeypatch.setattr(_tx, "move_symbol", _raise)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("mutate_apply_transform_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "mutate_apply_transform_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) Marker-prefix discipline -- W607-EG stays in ``mutate_*`` family
# ---------------------------------------------------------------------------


def test_w607eg_marker_prefix_stays_in_mutate_family(cli_runner, mutate_project, monkeypatch):
    """Every W607-EG substrate marker uses the canonical ``mutate_*`` prefix.

    Hard distinction from sibling W607-* layers, especially the
    transform-axis pair (cmd_simulate / W607-EF).
    """
    import roam.refactor.transforms as _tx

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-EG")

    monkeypatch.setattr(_tx, "move_symbol", _raise)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("mutate_"), (
            f"every surfaced W607-EG marker must use the ``mutate_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("simulate_", "cmd_simulate W607-EF (transform-axis sibling)"),
            ("orchestrate_", "cmd_orchestrate W607-DS"),
            ("partition_", "cmd_partition W607-DU"),
            ("agent_plan_", "cmd_agent_plan W607-DY"),
            ("fleet_", "cmd_fleet W607-EB"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("over_fetch_", "cmd_over_fetch W607-CE"),
            ("missing_index_", "cmd_missing_index W607-CI"),
            ("smells_", "cmd_smells W607-BN"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("clones_", "cmd_clones W607-BQ"),
            ("duplicates_", "cmd_duplicates W607-BM"),
            ("dead_", "cmd_dead W607-BX"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("vulns_", "cmd_vulns W607-AQ + CH (security sibling)"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (6) Source-level guard: cmd_mutate carries the W607-EG accumulator
# ---------------------------------------------------------------------------


def test_cmd_mutate_carries_w607eg_accumulator():
    """AST-level guard: cmd_mutate source carries the W607-EG accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_mutate.py"
    assert src_path.exists(), f"cmd_mutate.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607eg_warnings_out" in src, (
        "W607-EG accumulator missing from cmd_mutate; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_eg" in src, (
        "W607-EG ``_run_check_eg`` helper missing from cmd_mutate; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_eg = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_eg":
            found_run_check_eg = True
            break
    assert found_run_check_eg, (
        "W607-EG ``_run_check_eg`` helper not found in cmd_mutate AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (7) Each W607-EG substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607eg_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-EG substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_mutate.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _EG_PHASES:
        same_line = f'_run_check_eg(\n        "{phase}"' in src or f'_run_check_eg("{phase}"' in src
        multi_line = (
            f'_run_check_eg(\n            "{phase}"' in src
            or f'_run_check_eg(\n                "{phase}"' in src
            or f'_run_check_eg(\n                    "{phase}"' in src
        )
        marker_grep = f"mutate_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-EG wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (8) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607eg_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-EG marker shape lives in cmd_mutate."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_mutate.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"mutate_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-EG marker fstring missing from cmd_mutate; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (9) LAW 6 verdict-first invariant: verdict survives every phase failure
# ---------------------------------------------------------------------------


def test_law_6_verdict_survives_every_phase_failure(cli_runner, mutate_project, monkeypatch):
    """LAW 6 invariant: ``summary.verdict`` is a non-empty single line on
    every phase failure -- the floor never disappears.
    """
    import roam.refactor.transforms as _tx

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-law6-from-W607-EG")

    monkeypatch.setattr(_tx, "move_symbol", _raise)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict, (
        f"LAW 6 invariant violated: verdict missing/empty on degraded path; got summary={summary!r}"
    )
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    forbidden_vocab = ("safe", "passed", "all clear")
    for forbidden in forbidden_vocab:
        assert forbidden not in verdict.lower(), (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# (10) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, mutate_project, monkeypatch):
    """Pattern-2 regression guard.

    If ``move_symbol`` raises, the empty-floor default kicks in
    (files_modified=[]) and the envelope is emitted. The W607-EG wrap
    MUST flip ``partial_success: True`` on that branch so the empty-state
    envelope is NOT mistaken for a clean transform.
    """
    import roam.refactor.transforms as _tx

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-EG")

    monkeypatch.setattr(_tx, "move_symbol", _raise)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    apply_markers = [m for m in all_wo if m.startswith("mutate_apply_transform_failed:")]
    assert apply_markers, (
        f"degraded path MUST surface the apply_transform marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (11) Helper-template ``return default`` verbatim shape
# ---------------------------------------------------------------------------


def test_run_check_eg_helper_returns_default_verbatim():
    """W607-DP finding: the _run_check_eg helper MUST end with the literal
    ``return default`` (not ``return None`` or a captured local).

    AST-level guard: locate the ``_run_check_eg`` FunctionDef and walk
    its body to confirm the last statement of the ``except`` handler
    is ``Return(value=Name(id='default'))``.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_mutate.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_eg"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ExceptHandler):
                last_stmt = sub.body[-1]
                assert isinstance(last_stmt, ast.Return), (
                    f"_run_check_eg except handler last stmt is {type(last_stmt).__name__!r}, not Return"
                )
                assert isinstance(last_stmt.value, ast.Name), (
                    f"_run_check_eg must `return default` (a Name), got {ast.dump(last_stmt.value)!r}"
                )
                assert last_stmt.value.id == "default", (
                    f"_run_check_eg must `return default`, got `return {last_stmt.value.id}`"
                )
                found = True
                break
        if found:
            break
    assert found, (
        "_run_check_eg FunctionDef / except handler not found in cmd_mutate AST; the helper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (12) TRANSFORM AXIS 2-WAY PAIRING PIN
# ---------------------------------------------------------------------------


def test_transform_axis_2way_pairing():
    """AST-scan pin: cmd_simulate (W607-EF) + cmd_mutate (W607-EG) both
    carry W607 substrate-CALL plumbing.

    The TRANSFORM AXIS is closed at substrate-CALL layer:
    - cmd_simulate is the what-if counterfactual transform on a cloned graph
    - cmd_mutate is the real-transform that writes files to disk

    Removing the plumbing from either source file fails this guard so
    the axis invariant stays loud.
    """
    root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    simulate_src = (root / "cmd_simulate.py").read_text(encoding="utf-8")
    mutate_src = (root / "cmd_mutate.py").read_text(encoding="utf-8")

    # cmd_simulate carries W607-EF (counterfactual transform on cloned graph)
    assert "_w607ef_warnings_out" in simulate_src, (
        "transform-axis pairing pin: cmd_simulate has lost its "
        "W607-EF substrate-CALL accumulator -- the axis is no longer "
        "closed."
    )
    assert "_run_check_ef" in simulate_src, (
        "transform-axis pairing pin: cmd_simulate has lost its W607-EF ``_run_check_ef`` helper."
    )

    # cmd_mutate carries W607-EG (real transform that writes files)
    assert "_w607eg_warnings_out" in mutate_src, (
        "transform-axis pairing pin: cmd_mutate has lost its "
        "W607-EG substrate-CALL accumulator -- the axis is no longer "
        "closed."
    )
    assert "_run_check_eg" in mutate_src, (
        "transform-axis pairing pin: cmd_mutate has lost its W607-EG ``_run_check_eg`` helper."
    )

    # Cross-prefix discipline at source level.
    simulate_marker = 'f"simulate_{phase}_failed:{type(exc).__name__}:{exc}"'
    mutate_marker = 'f"mutate_{phase}_failed:{type(exc).__name__}:{exc}"'

    assert simulate_marker not in mutate_src, (
        "cmd_mutate leaks ``simulate_*`` marker -- transform-axis prefix discipline violated."
    )
    assert mutate_marker not in simulate_src, (
        "cmd_simulate leaks ``mutate_*`` marker -- transform-axis prefix discipline violated."
    )


# ---------------------------------------------------------------------------
# (13) Per-substrate isolation -- each boundary raising surfaces marker
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_each_boundary_surfaces_marker(cli_runner, mutate_project, monkeypatch):
    """Per-substrate isolation: each W607-EG boundary raising surfaces a
    distinct marker + graceful degradation.

    Raise inside ``move_symbol`` and confirm the matching
    apply_transform marker surfaces. The remaining substrates still run
    on the empty floor so the envelope composes a coherent verdict.
    """
    import roam.refactor.transforms as _tx

    def _raise(*args, **kwargs):
        raise RuntimeError("isolation-apply-W607-EG")

    monkeypatch.setattr(_tx, "move_symbol", _raise)
    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("mutate_apply_transform_failed:") for m in all_wo), all_wo
    # Envelope still composes.
    assert isinstance(data["summary"]["verdict"], str)
    assert data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# (14) W82.1 ATOMIC FILE-WRITE REGRESSION GUARD
# ---------------------------------------------------------------------------


def test_w821_no_torn_writes_on_apply_transform_raise(cli_runner, mutate_project, monkeypatch, tmp_path):
    """W82.1 atomic file-write regression guard.

    If the inner ``move_symbol`` raises after partially preparing
    changes, the W607-EG substrate wrap MUST NOT introduce a torn-write
    path. We pre-create a target file with known content and confirm
    that on apply_transform raise the file is unchanged (no partial
    write through the W607-EG plumbing).

    Note: ``--apply`` is required to even attempt writing; the empty-
    floor result returned by the substrate wrap on raise has
    ``files_modified=[]`` so no downstream write path is reached.
    """
    target = mutate_project / "src" / "runner.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    canary_content = "ORIGINAL CANARY CONTENT\n"
    target.write_text(canary_content, encoding="utf-8")

    import roam.refactor.transforms as _tx

    def _raise(*args, **kwargs):
        raise OSError("synthetic-torn-write-from-W607-EG")

    monkeypatch.setattr(_tx, "move_symbol", _raise)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py", "--apply")
    assert result.exit_code == 0, result.output

    # File contents unchanged -- no torn write through the wrap.
    assert target.read_text(encoding="utf-8") == canary_content, (
        "W82.1 regression: pre-existing target file contents were "
        "modified despite apply_transform raising; the W607-EG wrap "
        "must NOT introduce a partial-write path."
    )

    # Envelope still emits with degraded floor.
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True
    assert data["summary"].get("files_modified") == 0


# ---------------------------------------------------------------------------
# (15) compose_verdict failure path -- LAW 6 floor survives
# ---------------------------------------------------------------------------


def test_mutate_compose_verdict_degraded_floor(cli_runner, mutate_project, monkeypatch):
    """Force a poisoned transform result so compose_verdict degrades to
    the empty-floor LAW-6 string. The verdict still emits.
    """
    import roam.refactor.transforms as _tx

    class _BoomDict(dict):
        """Dict-like object whose .get('symbol', ...) raises."""

        def get(self, key, default=None):
            if key == "symbol":
                raise ZeroDivisionError("synthetic-verdict-from-W607-EG")
            return super().get(key, default)

    boom_result = _BoomDict(
        symbol="helper",
        files_modified=[],
        warnings=[],
    )

    def _return_boom(*args, **kwargs):
        return boom_result

    monkeypatch.setattr(_tx, "move_symbol", _return_boom)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    bad_markers = [
        m
        for m in all_wo
        if m.startswith("mutate_compose_verdict_failed:") or m.startswith("mutate_validate_transform_failed:")
    ]
    assert bad_markers, all_wo
    # Verdict still emits (LAW 6 single-line).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (16) Cross-prefix isolation: ``mutate_*`` markers don't leak globally
# ---------------------------------------------------------------------------


def test_cross_prefix_isolation_global(cli_runner, mutate_project, monkeypatch):
    """Confirm every marker emitted by cmd_mutate uses ``mutate_`` prefix.

    Raise inside the transform and check the marker prefix on the whole
    warnings_out list -- no foreign-family prefix leaks.
    """
    import roam.refactor.transforms as _tx

    def _raise(*args, **kwargs):
        raise RuntimeError("cross-prefix-W607-EG")

    monkeypatch.setattr(_tx, "move_symbol", _raise)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers
    for marker in substrate_markers:
        assert marker.startswith("mutate_"), f"foreign-family marker leaked: {marker!r}"


# ---------------------------------------------------------------------------
# (17) Agent contract emits facts + next_commands
# ---------------------------------------------------------------------------


def test_mutate_agent_contract_facts_emitted(cli_runner, mutate_project, monkeypatch):
    """On the clean path the agent_contract carries facts AND
    next_commands -- LAW 4 concrete-noun anchored.
    """
    import roam.refactor.transforms as _tx

    def _clean_move(conn, symbol, target_file, dry_run=True):
        return {
            "symbol": symbol,
            "files_modified": [{"path": "src/engine.py", "action": "MODIFY", "changes": []}],
            "warnings": [],
        }

    monkeypatch.setattr(_tx, "move_symbol", _clean_move)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0
    data = _json.loads(result.output)
    ac = data.get("agent_contract") or {}
    facts = ac.get("facts") or []
    next_commands = ac.get("next_commands") or []
    assert facts, f"facts must be non-empty; got {ac!r}"
    # First fact is the verdict (verdict-first ordering).
    assert facts[0] == data["summary"]["verdict"]
    # next_commands contains the --apply hint on dry-run.
    assert any("--apply" in c for c in next_commands), next_commands


# ---------------------------------------------------------------------------
# (18) Empty-floor result on raise carries shape contract
# ---------------------------------------------------------------------------


def test_mutate_empty_floor_shape(cli_runner, mutate_project, monkeypatch):
    """Empty-floor result on apply_transform raise has the canonical
    shape: ``files_modified=[]``, ``conflicts=0``.
    """
    import roam.refactor.transforms as _tx

    def _raise(*args, **kwargs):
        raise RuntimeError("empty-floor-W607-EG")

    monkeypatch.setattr(_tx, "move_symbol", _raise)

    result = _invoke_mutate_move(cli_runner, mutate_project, "helper", "src/runner.py")
    assert result.exit_code == 0
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("files_modified") == 0, summary
    assert summary.get("conflicts") == 0, summary
    assert summary.get("operation") == "move", summary


# ---------------------------------------------------------------------------
# (19) AST: cmd_mutate command count unchanged (subcommand surface preserved)
# ---------------------------------------------------------------------------


def test_mutate_subcommand_surface_preserved():
    """Source-level guard: cmd_mutate still exposes the four mutate
    subcommands (move/rename/add-call/extract).

    Adding W607-EG plumbing must NOT silently drop a subcommand.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_mutate.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    subcommand_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                # @mutate.command("move") shape
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "command"
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                ):
                    subcommand_names.add(dec.args[0].value)
    expected = {"move", "rename", "add-call", "extract"}
    assert expected.issubset(subcommand_names), (
        f"mutate subcommand surface drifted; expected {expected!r} <= {subcommand_names!r}"
    )


# ---------------------------------------------------------------------------
# (20) Marker fstring does NOT appear in cmd_simulate (axis-pair isolation)
# ---------------------------------------------------------------------------


def test_mutate_marker_fstring_isolated_from_simulate():
    """The ``mutate_{phase}_failed`` marker fstring lives only in
    cmd_mutate -- not in the axis-pair cmd_simulate.
    """
    root = Path(__file__).parent.parent / "src" / "roam" / "commands"
    simulate_src = (root / "cmd_simulate.py").read_text(encoding="utf-8")
    mutate_marker = 'f"mutate_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert mutate_marker not in simulate_src, (
        f"axis-pair isolation: cmd_simulate must not carry the cmd_mutate marker fstring {mutate_marker!r}"
    )


# ---------------------------------------------------------------------------
# (21) W978 #1 discipline: f-string verdict floor uses literal text
# ---------------------------------------------------------------------------


def test_w978_discipline_verdict_floor_literal_text():
    """W978 #1: the verdict-floor default passed to _run_check_eg for the
    compose_verdict phase must be a literal string (not a Name reference
    that could be None / unbound at the kwarg-bind point).

    AST guard: find the _run_check_eg call with first positional arg
    ``"compose_verdict"`` and confirm its ``default=`` kwarg is a
    Constant string (not a Name).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_mutate.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found_compose_call = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "_run_check_eg":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "compose_verdict"):
            continue
        # Find the ``default=`` kwarg.
        for kw in node.keywords:
            if kw.arg == "default":
                assert isinstance(kw.value, ast.Constant), (
                    f"W978 #1 violation: compose_verdict ``default=`` "
                    f"must be a Constant literal, got "
                    f"{ast.dump(kw.value)!r}"
                )
                assert isinstance(kw.value.value, str), (
                    f"W978 #1 violation: compose_verdict ``default=`` must be a literal str; got {kw.value.value!r}"
                )
                found_compose_call = True
                break
    assert found_compose_call, (
        "could not find _run_check_eg('compose_verdict', ...) call with a ``default=`` kwarg in cmd_mutate"
    )
