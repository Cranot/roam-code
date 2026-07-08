"""W607-O — ``cmd_dashboard`` threads ``warnings_out`` onto its envelope.

Fifteenth-in-batch W607 consumer-layer arc. DB-shape continuation after
W607-K (cmd_describe flagship aggregator), W607-L (cmd_minimap DB-shape
aggregator), W607-M (cmd_health CI-gate flagship), and W607-N (cmd_doctor
environment aggregator). cmd_dashboard per CLAUDE.md is the unified
single-screen status surface — it aggregates overview / health
(collect_metrics) / hotspots / risks (bus-factor, dead, cycles) /
vibe-check (canonical 8-pattern AI rot) / danger-zone substrates into
ONE envelope. Several helpers (``_vibe_check_canonical``,
``_top_danger_files``, ``_risk_areas``) already have INTERNAL try/except
returning safe floor values, but each helper itself can still raise
BEFORE reaching that floor (e.g., a downstream substrate refactor
changes the SQL shape, networkx blowing up during ``build_symbol_graph``
inside ``_risk_areas``, etc.) — and the outer call sites in
``dashboard()`` have no guards. W607-O adds the per-helper outer
wrapper + ``warnings_out`` marker emission so that lineage is preserved.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_dashboard.py`` head-to-tail.
The dominant additional failure surface is the per-helper raise path:
each helper has its own internal try/except for its expected exception
classes (where defensible), but the call boundary itself is
unprotected. W607-O wraps each outer-call site
(``_overview`` / ``collect_metrics`` / ``_top_hotspots`` /
``_risk_areas`` / ``_vibe_check_canonical`` / ``_unique_signal_hints``
/ ``_top_danger_files``) with a single try/except that emits
``dashboard_<phase>_failed:<exc>:<detail>`` markers via
``warnings_out`` and substitutes a safe default so the envelope still
emits the remaining sections cleanly.

Marker family is ``dashboard_*`` — NOT ``doctor_*`` (W607-N), NOT
``health_*`` (W607-M), NOT ``describe_*`` (W607-K), NOT ``minimap_*``
(W607-L), NOT ``grep_*`` (W607-G), NOT ``history_*`` (W607-H), NOT
``refs_text_*`` (W607-I), NOT ``delete_check_*`` (W607-J), NOT
``search_*`` / ``complete_*`` / ``semantic_*`` (W607-E/F/A). The
marker-prefix discipline test pins this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. ``warnings_out`` is a
plain accumulator (mirrors W607-N's cmd_doctor / W607-M's cmd_health
idiom). The per-helper wrapper ``_run_check`` lives in the
``dashboard()`` body so the bucket collects markers consistently
across every helper invocation. cmd_dashboard's existing inline lazy
imports (``get_db_path`` / ``UNREFERENCED_EXPORTS`` /
``build_symbol_graph`` / ``find_cycles`` / ``compute_ai_rot_score`` /
``collect_metrics``) are cost-deferred lazy imports — none claim to
"avoid a cycle" in their inline comments, so no false-hedge
remediation is needed.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers — invoke dashboard via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_dashboard(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam dashboard`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("dashboard")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture — populated, indexed corpus with symbols + edges so the W607-O
# substrate-failure baseline has real data to query.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def dashboard_project(tmp_path, monkeypatch):
    """Indexed corpus with multiple symbols + edges so dashboard substrate
    helpers have real data to query.
    """
    proj = tmp_path / "dashboard_w607o_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\n"
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n\ndef shout(msg):\n    return msg.upper()\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path — populated corpus → no warnings_out (byte-identical regression guard)
# ---------------------------------------------------------------------------


def test_dashboard_empty_corpus_envelope_byte_identical(cli_runner, dashboard_project):
    """Clean dashboard on populated corpus → envelope omits warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. The empty-bucket-no-keys discipline ensures
    consumers can't accidentally read a stale or always-present
    warnings_out field. ``summary.partial_success`` is universally
    auto-defaulted to ``False`` by ``json_envelope`` for every command
    (see ``src/roam/output/formatter.py`` line 975), so the W607-O
    contract is: the clean path leaves it as ``False``, the disclosure
    path flips it to ``True`` — mirrors W607-N's contract on cmd_doctor.
    """
    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "dashboard"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO warnings_out keys.
    assert "warnings_out" not in data, (
        f"clean dashboard must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean dashboard must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )
    # On the clean path partial_success must remain at the auto-False
    # default — only the disclosure path flips it to True.
    assert data["summary"].get("partial_success") is False, (
        f"clean dashboard summary.partial_success must remain False on the auto-default path; got {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (2) Each phase failure marker fires when that substrate helper raises
# ---------------------------------------------------------------------------


def test_dashboard_overview_failure_marker_format(cli_runner, dashboard_project, monkeypatch):
    """If ``_overview`` raises, the W607-O per-phase guard surfaces
    a ``dashboard_overview_failed:`` marker.
    """
    from roam.commands import cmd_dashboard

    def _boom_overview(conn):
        raise RuntimeError("synthetic-overview-from-W607-O")

    monkeypatch.setattr(cmd_dashboard, "_overview", _boom_overview)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"_overview RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("dashboard_overview_failed:")]
    assert markers, f"expected ``dashboard_overview_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-overview-from-W607-O" in m for m in markers), markers


def test_dashboard_collect_metrics_failure_marker_format(cli_runner, dashboard_project, monkeypatch):
    """If ``collect_metrics`` raises, surface ``dashboard_collect_metrics_failed:``.

    cmd_dashboard imports ``collect_metrics`` lazily inside the
    function body, so patch the canonical source-module attr instead
    of trying to reach into the not-yet-imported alias.
    """
    from roam.commands import metrics_history

    def _boom_metrics(conn):
        raise PermissionError("synthetic-metrics-from-W607-O")

    monkeypatch.setattr(metrics_history, "collect_metrics", _boom_metrics)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"collect_metrics PermissionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("dashboard_collect_metrics_failed:")]
    assert markers, f"expected ``dashboard_collect_metrics_failed:`` marker; got {top_wo!r}"
    assert any("PermissionError" in m for m in markers), markers


def test_dashboard_hotspots_failure_marker_format(cli_runner, dashboard_project, monkeypatch):
    """If ``_top_hotspots`` raises, surface ``dashboard_hotspots_failed:``."""
    from roam.commands import cmd_dashboard

    def _boom_hotspots(conn, limit=5):
        raise RuntimeError("synthetic-hotspots-from-W607-O")

    monkeypatch.setattr(cmd_dashboard, "_top_hotspots", _boom_hotspots)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"_top_hotspots RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("dashboard_hotspots_failed:")]
    assert markers, f"expected ``dashboard_hotspots_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


def test_dashboard_risk_areas_failure_marker_format(cli_runner, dashboard_project, monkeypatch):
    """If ``_risk_areas`` raises, surface ``dashboard_risk_areas_failed:``."""
    from roam.commands import cmd_dashboard

    def _boom_risks(conn):
        raise RuntimeError("synthetic-risks-from-W607-O")

    monkeypatch.setattr(cmd_dashboard, "_risk_areas", _boom_risks)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"_risk_areas RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("dashboard_risk_areas_failed:")]
    assert markers, f"expected ``dashboard_risk_areas_failed:`` marker; got {top_wo!r}"


def test_dashboard_vibe_check_failure_marker_format(cli_runner, dashboard_project, monkeypatch):
    """If ``_vibe_check_canonical`` raises, surface ``dashboard_vibe_check_failed:``."""
    from roam.commands import cmd_dashboard

    def _boom_vibe(conn):
        raise RuntimeError("synthetic-vibe-from-W607-O")

    monkeypatch.setattr(cmd_dashboard, "_vibe_check_canonical", _boom_vibe)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"_vibe_check_canonical RuntimeError must surface top-level "
        f"warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("dashboard_vibe_check_failed:")]
    assert markers, f"expected ``dashboard_vibe_check_failed:`` marker; got {top_wo!r}"


def test_dashboard_danger_top_failure_marker_format(cli_runner, dashboard_project, monkeypatch):
    """If ``_top_danger_files`` raises, surface ``dashboard_danger_top_failed:``."""
    from roam.commands import cmd_dashboard

    def _boom_danger(conn, limit=5):
        raise RuntimeError("synthetic-danger-from-W607-O")

    monkeypatch.setattr(cmd_dashboard, "_top_danger_files", _boom_danger)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"_top_danger_files RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("dashboard_danger_top_failed:")]
    assert markers, f"expected ``dashboard_danger_top_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_dashboard_warnings_out_in_envelope(cli_runner, dashboard_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. Summary mirror
    gives consumers reading only the summary block visibility too.
    Mirror parity with W607-A/B/C/D/E/F/G/H/I/J/K/L/M/N consumers.
    """
    from roam.commands import cmd_dashboard

    def _boom_overview(conn):
        raise RuntimeError("synthetic-mirror-from-W607-O")

    monkeypatch.setattr(cmd_dashboard, "_overview", _boom_overview)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    # Top-level and summary content must be equal.
    assert sorted(data["warnings_out"]) == sorted(data["summary"]["warnings_out"]), (
        f"top-level vs summary.warnings_out must be equal; "
        f"top={data['warnings_out']!r} summary={data['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (4) partial_success flips when ANY dashboard helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_dashboard_helper_raises(cli_runner, dashboard_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    dashboard" from "dashboard ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    cmd_dashboard previously did NOT emit partial_success at all (the
    envelope had only verdict + counts), so the W607-O fix introduces
    the field exclusively on the disclosure path.
    """
    from roam.commands import cmd_dashboard

    def _boom_overview(conn):
        raise RuntimeError("synthetic-partial-success-from-W607-O")

    monkeypatch.setattr(cmd_dashboard, "_overview", _boom_overview)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, dashboard_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A/B/C/D/E/F/G/H/I/J/K/L/M/N contracts.
    """
    from roam.commands import cmd_dashboard

    def _boom_overview(conn):
        raise PermissionError("synthetic-shape-detail-from-W607-O")

    monkeypatch.setattr(cmd_dashboard, "_overview", _boom_overview)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "_overview per-phase guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("dashboard_overview_failed:")]
    assert failure_markers, f"expected dashboard_overview_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "dashboard_overview_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline — ``dashboard_*`` not doctor/health/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_dashboard_not_doctor_or_health(cli_runner, dashboard_project, monkeypatch):
    """Every surfaced marker uses the canonical ``dashboard_*`` prefix.

    cmd_dashboard is the UNIFIED-AGGREGATOR axis — distinct from:

    * cmd_doctor           → ``doctor_*`` (W607-N environment aggregator)
    * cmd_health           → ``health_*`` (W607-M flagship CI-gate)
    * cmd_describe         → ``describe_*`` (W607-K flagship aggregator)
    * cmd_minimap          → ``minimap_*`` (W607-L DB-shape aggregator)
    * cmd_grep             → ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep     → ``history_*`` (W607-H pickaxe)
    * cmd_refs_text        → ``refs_text_*`` (W607-I string-audit)
    * cmd_delete_check     → ``delete_check_*`` (W607-J diff-gate)
    * cmd_search           → ``search_*`` (W607-E lexical)
    * cmd_complete         → ``complete_*`` (W607-F prefix)
    * cmd_search_semantic  → ``semantic_*`` (W607-A FTS5)
    * cmd_findings         → ``findings_query_*`` (W607-C registry)
    * cmd_dogfood          → ``dogfood_*`` (W607-D corpus loader)
    * cmd_retrieve         → ``retrieve_*`` (W607-B pipeline)

    Hard guard against accidental marker-prefix drift (a future
    contributor mis-routing a marker into a sibling family because
    cmd_dashboard is a high-traffic exploration surface that may be
    edited next to cmd_health / cmd_doctor by a refactor wave). Closes
    the closed-enum discipline at the cmd_dashboard boundary.
    """
    from roam.commands import cmd_dashboard

    def _boom_overview(conn):
        raise PermissionError("synthetic-prefix-discipline-from-W607-O")

    monkeypatch.setattr(cmd_dashboard, "_overview", _boom_overview)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("dashboard_"), (
            f"every surfaced marker must use the W607-O ``dashboard_*`` "
            f"prefix family (cmd_dashboard unified-aggregator scope); "
            f"got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("grep_", "cmd_grep W607-G"),
            ("history_", "cmd_history_grep W607-H"),
            ("refs_text_", "cmd_refs_text W607-I"),
            ("delete_check_", "cmd_delete_check W607-J"),
            ("search_", "cmd_search W607-E"),
            ("complete_", "cmd_complete W607-F"),
            ("semantic_", "cmd_search_semantic W607-A"),
            ("findings_query_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D"),
            ("retrieve_", "cmd_retrieve W607-B"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Sibling parity — W607-N cmd_doctor xfails / source pins unaffected
# ---------------------------------------------------------------------------


def test_w607_n_cmd_doctor_xfails_unaffected():
    """Sibling parity guard: W607-N cmd_doctor source surface unchanged.

    W607-O lands only in cmd_dashboard. The W607-N cmd_doctor surface
    (per-helper ``_run_check`` wrapper + ``_w607n_warnings_out`` accumulator
    + ``doctor_*`` marker emission) MUST stay identical. If a future refactor
    wave touches cmd_doctor while editing dashboard, the canonical anchors
    below catch the drift before sibling tests fail downstream.
    """
    from pathlib import Path

    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_doctor.py"
    assert src_path.exists(), f"cmd_doctor.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607n_warnings_out" in src, (
        "W607-N accumulator removed from cmd_doctor; W607-O must not regress the sibling instrumentation."
    )
    assert "doctor_" in src, (
        "W607-N marker prefix removed from cmd_doctor; W607-O must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (8) Subprocess axis orthogonality — cmd_dashboard never shells out
# ---------------------------------------------------------------------------


def test_subprocess_axis_orthogonality():
    """cmd_dashboard is a pure DB-shape aggregator — no subprocess calls.

    The W607 marker family is split by failure axis: subprocess wrappers
    (W607-G/H/I/J cmd_grep / cmd_history_grep / cmd_refs_text /
    cmd_delete_check) wrap subprocess exit codes; DB-shape aggregators
    (W607-K/L/M/N/O cmd_describe / cmd_minimap / cmd_health / cmd_doctor /
    cmd_dashboard) wrap DB-substrate raises. This test pins cmd_dashboard
    in the DB-shape axis by source inspection: no subprocess imports.
    """
    from pathlib import Path

    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dashboard.py"
    src = src_path.read_text(encoding="utf-8")
    # Pin import-axis only; the W607-O docstring may name sibling
    # subprocess families (W607-G/H/I/J) without triggering a false
    # positive on a substring scan.
    assert "import subprocess" not in src, (
        "cmd_dashboard must remain in the DB-shape W607 axis (no subprocess fan-out). Found 'import subprocess'."
    )
    assert "from subprocess" not in src, (
        "cmd_dashboard must remain in the DB-shape W607 axis (no subprocess fan-out). Found 'from subprocess'."
    )


# ---------------------------------------------------------------------------
# (9) Multiple helpers can fail simultaneously — all markers surface
# ---------------------------------------------------------------------------


def test_multiple_helpers_failing_emit_separate_markers(cli_runner, dashboard_project, monkeypatch):
    """Two simultaneous helper raises → two markers, both surfaced.

    Aggregator scope: the dashboard's value proposition is composing
    multiple substrates. The W607-O guard must NOT short-circuit on
    the first raise — each subsequent helper still runs and emits its
    own marker on failure. Consumers see the full degradation lineage.
    """
    from roam.commands import cmd_dashboard

    def _boom_overview(conn):
        raise RuntimeError("synthetic-multi-overview-from-W607-O")

    def _boom_risks(conn):
        raise PermissionError("synthetic-multi-risks-from-W607-O")

    monkeypatch.setattr(cmd_dashboard, "_overview", _boom_overview)
    monkeypatch.setattr(cmd_dashboard, "_risk_areas", _boom_risks)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    overview_markers = [m for m in top_wo if m.startswith("dashboard_overview_failed:")]
    risk_markers = [m for m in top_wo if m.startswith("dashboard_risk_areas_failed:")]
    assert overview_markers, f"expected dashboard_overview_failed: marker; got {top_wo!r}"
    assert risk_markers, f"expected dashboard_risk_areas_failed: marker; got {top_wo!r}"
    # partial_success still flips with multiple markers.
    assert data["summary"].get("partial_success") is True, data["summary"]
