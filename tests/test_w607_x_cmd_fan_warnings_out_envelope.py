"""W607-X -- ``cmd_fan`` threads ``warnings_out`` onto its envelope.

Twenty-fourth-in-batch W607 consumer-layer arc. Direct sibling of W607-W
(cmd_relate multi-target axis). cmd_fan is the **dual-mode aggregator**
variant -- a single Click command with two top-level branches
(``mode == "symbol"`` vs ``mode == "file"``) that each fan out across
several substrate boundaries. Each branch threads its own warnings_out
into its success-path envelope, but the accumulator is shared at the
function scope so a substrate raise in one branch surfaces on the
emitted envelope for that same invocation.

Substrate boundaries wrapped by W607-X
--------------------------------------

Six substrate-call sites in ``fan()`` get the canonical
``_run_check(phase, fn, *args)`` wrapper:

* ``fetch_symbol_rows``     -- symbol-mode graph_metrics + symbols + files
                              JOIN (sqlite3.Connection.execute().fetchall())
* ``filter_tooling``        -- _filter_tooling_rows (file_role_hints check)
* ``file_scope_metrics``    -- _file_scope_metrics (intra/inter file
                              edge breakdown via batched_in)
* ``emit_findings_symbol``  -- _emit_fan_findings(mode='symbol') +
                              conn.commit() (W152 registry mirror)
* ``fetch_file_rows``       -- file-mode files + file_edges JOIN
                              (sqlite3.Connection.execute().fetchall())
* ``emit_findings_file``    -- _emit_fan_findings(mode='file') +
                              conn.commit() (W152 registry mirror)

Each raise becomes a ``fan_<phase>_failed:<exc_class>:<detail>`` marker
via ``_w607x_warnings_out`` and the envelope still emits cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_fan's substrate-call sites are a mix of inline SQL (the two big
graph_metrics + file_edges queries) and helper-call boundaries
(_filter_tooling_rows, _file_scope_metrics, _emit_fan_findings). Two
of the pre-existing try/except blocks (the symbol/file persist branches)
already swallowed sqlite3.OperationalError silently to handle the pre-W89
schema case. W607-X PRESERVES that local degradation -- OperationalError
on a missing findings table is the expected pre-W89 path and does NOT
surface as a marker. All OTHER raise classes flow through _run_check
and surface as structured ``fan_*`` markers.

Marker family is ``fan_*`` -- NOT ``relate_*`` (W607-W), NOT ``deps_*``
(W607-V), etc. The marker-prefix discipline test pins this closed-enum
distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_fan has a lazy
``from roam.db.findings import FindingRecord, emit_finding`` inside
``_emit_fan_findings`` -- this is a genuine deferred-load to keep the
read-only path cost-free, NOT a cargo-cult cycle hedge. Left untouched
per W907.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers -- invoke fan via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_fan(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam fan`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("fan")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with multi-symbol call structure for fan analysis
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def fan_project(tmp_path, monkeypatch):
    """Indexed corpus with cross-file edges for fan analysis.

    Three-file fixture so cmd_fan substrates have signal: graph_metrics
    populates with non-zero degrees, file_edges has rows for the
    file-mode path, and _file_scope_metrics has cross-file targets.
    """
    proj = tmp_path / "fan_w607x_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        "def shared_helper():\n    return 1\n\ndef secondary_helper():\n    return shared_helper()\n",
        encoding="utf-8",
    )
    (src / "consumer_a.py").write_text(
        "from src.core import shared_helper, secondary_helper\n\n"
        "def use_a():\n"
        "    shared_helper()\n"
        "    return secondary_helper()\n",
        encoding="utf-8",
    )
    (src / "consumer_b.py").write_text(
        "from src.core import shared_helper\n\ndef use_b():\n    return shared_helper()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- clean fan -> envelope omits warnings_out (byte-identical)
# ---------------------------------------------------------------------------


def test_fan_symbol_clean_envelope_no_warnings_out(cli_runner, fan_project):
    """Clean fan symbol-mode -> no W607-X warnings_out.

    Hash-stable: an empty W607-X bucket on the success path must produce
    an envelope WITHOUT top-level ``warnings_out`` (only added when a
    substrate raises). Mirrors W607-W contract.
    """
    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "fan"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-X markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    w607x_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("fan_")]
    assert not w607x_markers, f"clean fan must NOT surface fan_* markers; got top={top_wo!r}, summary={summary_wo!r}"
    # partial_success must NOT flip on the clean path.
    assert data["summary"].get("partial_success") is not True, (
        f"clean fan must NOT flip partial_success; got summary = {data['summary']!r}"
    )


def test_fan_file_clean_envelope_no_warnings_out(cli_runner, fan_project):
    """Clean fan file-mode -> no W607-X warnings_out (file-mode parity)."""
    result = _invoke_fan(cli_runner, fan_project, "file")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "fan"
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    w607x_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("fan_")]
    assert not w607x_markers, (
        f"clean fan file-mode must NOT surface fan_* markers; got top={top_wo!r}, summary={summary_wo!r}"
    )
    assert data["summary"].get("partial_success") is not True, data["summary"]


# ---------------------------------------------------------------------------
# (2) Each substrate failure marker fires when that helper raises
# ---------------------------------------------------------------------------


def test_fan_file_scope_metrics_failure_marker_format(cli_runner, fan_project, monkeypatch):
    """If _file_scope_metrics raises, surface ``fan_file_scope_metrics_failed:``.

    Driven via monkeypatching the helper on the cmd_fan module.
    """
    from roam.commands import cmd_fan

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-scope-from-W607-X")

    monkeypatch.setattr(cmd_fan, "_file_scope_metrics", _raise)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    scope_markers = [m for m in top_wo if m.startswith("fan_file_scope_metrics_failed:")]
    assert scope_markers, f"expected fan_file_scope_metrics_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in scope_markers), scope_markers
    assert any("synthetic-scope-from-W607-X" in m for m in scope_markers), scope_markers


def test_fan_filter_tooling_failure_marker_format(cli_runner, fan_project, monkeypatch):
    """If _filter_tooling_rows raises, surface ``fan_filter_tooling_failed:``."""
    from roam.commands import cmd_fan

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-tooling-from-W607-X")

    monkeypatch.setattr(cmd_fan, "_filter_tooling_rows", _raise)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    tooling_markers = [m for m in top_wo if m.startswith("fan_filter_tooling_failed:")]
    assert tooling_markers, f"expected fan_filter_tooling_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in tooling_markers), tooling_markers


def test_fan_emit_findings_symbol_failure_marker_format(cli_runner, fan_project, monkeypatch):
    """If _emit_fan_findings raises (non-OperationalError) with --persist,
    surface ``fan_emit_findings_symbol_failed:``.

    sqlite3.OperationalError is the expected pre-W89-schema path and is
    swallowed locally inside _persist_symbol (NOT surfaced as a marker).
    Other raises flow through _run_check.
    """
    from roam.commands import cmd_fan

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-persist-symbol-from-W607-X")

    monkeypatch.setattr(cmd_fan, "_emit_fan_findings", _raise)

    result = _invoke_fan(cli_runner, fan_project, "symbol", "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    persist_markers = [m for m in top_wo if m.startswith("fan_emit_findings_symbol_failed:")]
    assert persist_markers, f"expected fan_emit_findings_symbol_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in persist_markers), persist_markers


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_fan_warnings_out_top_level_and_summary_mirror(cli_runner, fan_project, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated.

    Drive a substrate raise on _file_scope_metrics and verify the envelope
    surfaces the marker in BOTH the top-level (``warnings_out`` key on the
    envelope dict) AND the summary mirror (``summary.warnings_out``).
    Mirror parity with W607-A..W consumers.
    """
    from roam.commands import cmd_fan

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-X")

    monkeypatch.setattr(cmd_fan, "_file_scope_metrics", _raise)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("fan_file_scope_metrics_failed:")]
    assert markers, f"expected fan_file_scope_metrics_failed: marker; got {data['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-X" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (4) partial_success flips when ANY fan helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_fan_helper_raises(cli_runner, fan_project, monkeypatch):
    """Any non-empty W607-X bucket -> summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    fan" from "fan ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    """
    from roam.commands import cmd_fan

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-X")

    monkeypatch.setattr(cmd_fan, "_file_scope_metrics", _raise)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) File-mode -- dual-mode aggregator parity
# ---------------------------------------------------------------------------


def test_fan_file_mode_substrate_raise_surfaces_marker(cli_runner, fan_project, monkeypatch):
    """File-mode substrate raise -> file-mode envelope carries the marker.

    Dual-mode aggregator axis: cmd_fan has TWO top-level branches that
    each emit their own JSON envelope. A raise inside the file-mode
    persist branch must surface on the file-mode envelope (NOT the
    symbol-mode envelope, which never executes on this invocation).
    """
    from roam.commands import cmd_fan

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-file-persist-from-W607-X")

    monkeypatch.setattr(cmd_fan, "_emit_fan_findings", _raise)

    result = _invoke_fan(cli_runner, fan_project, "file", "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    file_markers = [m for m in top_wo if m.startswith("fan_emit_findings_file_failed:")]
    assert file_markers, f"expected fan_emit_findings_file_failed: marker; got {top_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (6) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, fan_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..W contracts.
    """
    from roam.commands import cmd_fan

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-X")

    monkeypatch.setattr(cmd_fan, "_file_scope_metrics", _raise)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "file_scope_metrics guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("fan_file_scope_metrics_failed:")]
    assert failure_markers, f"expected fan_file_scope_metrics_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "fan_file_scope_metrics_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- ``fan_*`` not relate/deps/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_fan_not_relate_or_other(cli_runner, fan_project, monkeypatch):
    """Every surfaced marker uses the canonical ``fan_*`` prefix.

    cmd_fan is the dual-mode aggregator variant -- distinct from:

    * cmd_relate          -> ``relate_*`` (W607-W multi-target)
    * cmd_deps            -> ``deps_*`` (W607-V file-deps standalone)
    * cmd_uses            -> ``uses_*`` (W607-U direct-callers standalone)
    * cmd_impact          -> ``impact_*`` (W607-T blast-radius standalone)
    * cmd_diagnose        -> ``diagnose_*`` (W607-S root-cause ranking)
    * cmd_preflight       -> ``preflight_*`` (W607-R pre-change safety gate)
    * cmd_pr_risk         -> ``pr_risk_*`` (W607-Q PR-time risk aggregator)
    * cmd_audit           -> ``audit_*`` (W607-P one-shot architecture audit)
    * cmd_dashboard       -> ``dashboard_*`` (W607-O unified status)
    * cmd_doctor          -> ``doctor_*`` (W607-N environment aggregator)
    * cmd_health          -> ``health_*`` (W607-M CI-gate flagship)
    * cmd_describe        -> ``describe_*`` (W607-K flagship aggregator)
    * cmd_minimap         -> ``minimap_*`` (W607-L DB-shape aggregator)
    * cmd_grep            -> ``grep_*`` (W607-G ripgrep/git-grep fan-out)

    Hard guard against accidental marker-prefix drift.
    """
    from roam.commands import cmd_fan

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-X")

    monkeypatch.setattr(cmd_fan, "_file_scope_metrics", _raise)

    result = _invoke_fan(cli_runner, fan_project, "symbol")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("fan_"), (
            f"every surfaced W607-X marker must use the ``fan_*`` prefix "
            f"family (cmd_fan dual-mode aggregator scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("relate_", "cmd_relate W607-W"),
            ("deps_", "cmd_deps W607-V"),
            ("uses_", "cmd_uses W607-U"),
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("pr_risk_", "cmd_pr_risk W607-Q"),
            ("audit_", "cmd_audit W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("grep_", "cmd_grep W607-G"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (8) Source-level guard: cmd_fan carries the canonical W607-X accumulator
# ---------------------------------------------------------------------------


def test_cmd_fan_carries_w607x_accumulator():
    """AST-level guard: cmd_fan source carries the W607-X accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g., switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fan.py"
    assert src_path.exists(), f"cmd_fan.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607x_warnings_out" in src, (
        "W607-X accumulator missing from cmd_fan; the substrate-CALL marker plumbing has been removed."
    )
    assert "fan_{phase}_failed" in src, (
        'W607-X marker prefix template missing from cmd_fan; check the `f"fan_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside fan().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-X ``_run_check`` helper not found in cmd_fan AST; the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (9) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_fan substrate boundary is wrapped.

    W607-X substrate inventory:

    * fetch_symbol_rows     -- inline SQL fetchall (symbol-mode)
    * filter_tooling        -- _filter_tooling_rows
    * file_scope_metrics    -- _file_scope_metrics
    * emit_findings_symbol  -- _emit_fan_findings(mode='symbol') + commit
    * fetch_file_rows       -- inline SQL fetchall (file-mode)
    * emit_findings_file    -- _emit_fan_findings(mode='file') + commit

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_fan.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "fetch_symbol_rows",
        "filter_tooling",
        "file_scope_metrics",
        "emit_findings_symbol",
        "fetch_file_rows",
        "emit_findings_file",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes.
        same_line = f'_run_check("{phase}"' in src
        multi_line = (
            f'_run_check(\n            "{phase}"' in src
            or f'_run_check(\n                "{phase}"' in src
            or f'_run_check(\n                    "{phase}"' in src
            or f'_run_check(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-X _run_check wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (10) Sibling parity -- W607-W cmd_relate surface unchanged
# ---------------------------------------------------------------------------


def test_w607_w_cmd_relate_surface_unaffected():
    """Sibling parity guard: W607-W cmd_relate source surface unchanged.

    W607-X lands only in cmd_fan. The W607-W cmd_relate surface
    (``_w607w_warnings_out`` accumulator + ``relate_{phase}_failed``
    template) MUST stay identical. If a future refactor wave touches
    cmd_relate while editing fan, the canonical anchors below catch
    the drift before sibling tests fail downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_relate.py"
    assert src_path.exists(), f"cmd_relate.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607w_warnings_out" in src, (
        "W607-W accumulator removed from cmd_relate; W607-X must not regress the sibling instrumentation."
    )
    assert "relate_{phase}_failed" in src, (
        "W607-W marker prefix removed from cmd_relate; W607-X must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (11) Pre-W89 schema graceful-degradation preserved (OperationalError)
# ---------------------------------------------------------------------------


def test_operational_error_on_persist_is_swallowed_not_marked(cli_runner, fan_project, monkeypatch):
    """sqlite3.OperationalError on --persist must NOT surface as a marker.

    The pre-W89 schema path (no ``findings`` table) raises
    sqlite3.OperationalError inside _emit_fan_findings. The local
    try/except inside _persist_symbol catches that specific class so the
    command degrades silently -- this is the EXPECTED graceful-degradation
    path, not a substrate failure that should pollute warnings_out.

    Other raise classes (RuntimeError, PermissionError, etc.) flow
    through _run_check and surface as ``fan_emit_findings_symbol_failed:``.
    This test pins the OperationalError vs other-exception distinction.
    """
    import sqlite3

    from roam.commands import cmd_fan

    def _raise_operational(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic-no-findings-table")

    monkeypatch.setattr(cmd_fan, "_emit_fan_findings", _raise_operational)

    result = _invoke_fan(cli_runner, fan_project, "symbol", "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # OperationalError must NOT surface as a marker (it's the expected
    # graceful-degradation path, not a substrate failure).
    persist_markers = [m for m in top_wo if m.startswith("fan_emit_findings_symbol_failed:")]
    assert not persist_markers, (
        f"sqlite3.OperationalError on --persist must NOT surface as a "
        f"marker (graceful pre-W89 schema degradation); got {persist_markers!r}"
    )
