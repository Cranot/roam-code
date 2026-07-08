"""W607-Z -- ``cmd_diff`` threads ``warnings_out`` onto its envelope.

Twenty-sixth-in-batch W607 consumer-layer arc. Direct sibling of W607-Y
(cmd_critique diff-text-substrate axis). cmd_diff is the **diff-INPUT
pair complement** -- it consumes git REFS through ``get_changed_files``
(the shared-helper axis the W805-HHHH wave probed), where cmd_critique
consumes diff TEXT via stdin. Plumbing diff's substrate boundaries
closes the diff-input pair on the W607 family.

Substrate boundaries wrapped by W607-Z
--------------------------------------

Seven substrate-call sites in ``diff_cmd()`` get the canonical
``_run_check(phase, fn, *args)`` wrapper:

* ``get_changed_files``        -- get_changed_files(root, ...)
* ``resolve_changed_to_db``    -- resolve_changed_to_db(conn, changed)
* ``build_symbol_graph``       -- build_symbol_graph(conn)
* ``collect_affected_tests``   -- _collect_affected_tests(conn, sym_by_file)
* ``collect_coupling_warnings``-- _collect_coupling_warnings(conn, file_map)
* ``collect_fitness_violations``-- _collect_fitness_violations(conn, file_map, root)
* ``compute_risk_level``       -- _diff_risk_level(...)

Each raise becomes a ``diff_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607z_warnings_out`` and the envelope still emits cleanly.

W805-HHHH SHARED-HELPER axis -- doubly valuable
-----------------------------------------------

cmd_diff is the 12-strong SHARED-HELPER family's CANONICAL exemplar:
``get_changed_files`` returns ``[]`` silently on returncode!=0 /
FileNotFoundError / TimeoutExpired (root cause at
``src/roam/commands/changed_files.py:142,145``). The W607-Z plumbing
wraps the ``get_changed_files`` call AT THIS CALL SITE so a substrate
raise that escapes the helper's swallow path produces a structured
``diff_get_changed_files_failed:...`` marker on the envelope -- giving
this well-trafficked call site its own per-invocation visibility into
the helper's failure modes even before the root-cause helper fix lands.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_diff's substrate-call sites are direct function invocations on the
imported shared helpers (``get_changed_files``, ``resolve_changed_to_db``,
``build_symbol_graph``) and the module-level collector helpers
(``_collect_affected_tests``, ``_collect_coupling_warnings``,
``_collect_fitness_violations``, ``_diff_risk_level``). The dominant
raise axis is the helper-CALL boundary -- consistent with W607-N..Y.
Each helper can raise on a missing DB table (older indexes), a
fitness.yaml-shape change, a transient OperationalError, or a graph-
construction error. The previous code had silent ``try/except: pass``
around coupling + fitness collectors (the marker would never have
surfaced); W607-Z promotes these to ``_run_check`` so a raise produces
a structured marker.

Marker family is ``diff_*`` -- NOT ``critique_*`` (W607-Y), NOT
``relate_*`` (W607-W), etc. The marker-prefix discipline test pins
this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_diff has lazy
``from roam.commands.cmd_affected_tests import _gather_affected_tests``
inside ``_collect_affected_tests`` and lazy ``from roam.commands.cmd_fitness
import _load_rules`` inside ``_collect_fitness_violations`` -- these
are genuine deferred-load imports (the helpers pull heavy machinery
only needed on the --tests / --fitness paths), NOT cargo-cult cycle
hedges. Left untouched per W907.

Two warnings_out buckets, one channel
-------------------------------------

cmd_diff already carries a ``_diff_warnings_out`` bucket (W641-followup-
E unknown-severity / negative-count tracking — flips ``partial_success``
when ``_diff_risk_level`` couldn't map a count to a canonical bucket).
W607-Z adds a DISTINCT ``_w607z_warnings_out`` bucket (substrate-CALL
markers) so the two axes (unknown-severity data shape vs. helper-raised
substrate boundary) don't conflate at the call site. They MERGE into a
single ``warnings_out`` list on envelope emission; the marker PREFIX
disambiguates them downstream (``diff_unknown_severity:*`` vs.
``diff_<phase>_failed:*``). ``partial_success`` flips when EITHER
bucket is non-empty -- consumers reading ``partial_success`` alone
need not distinguish the two flavours.

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
# Helpers -- invoke diff via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_diff(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam diff`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("diff")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with an uncommitted edit so diff finds changes
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def diff_project(tmp_path, monkeypatch):
    """Indexed corpus with a small uncommitted edit so diff sees changes."""
    proj = tmp_path / "diff_w607z_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "models.py").write_text(
        "class User:\n    def __init__(self, name):\n        self.name = name\n",
        encoding="utf-8",
    )
    (src / "auth.py").write_text(
        "from src.models import User\n\ndef verify_token(t):\n    return User('test')\n\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    # Edit auth.py post-commit so `roam diff` (unstaged) sees a change.
    (src / "auth.py").write_text(
        "from src.models import User\n\ndef verify_token(t):\n    # tweak comment\n    return User('test')\n\n",
        encoding="utf-8",
    )
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-Z substrate markers
# ---------------------------------------------------------------------------


def test_diff_clean_envelope_omits_w607z_markers(cli_runner, diff_project):
    """Clean diff on a healthy corpus -> no W607-Z substrate markers.

    Hash-stable: an empty W607-Z bucket on the success path must produce
    an envelope without W607-Z substrate markers. The pre-existing
    ``_diff_warnings_out`` (unknown-severity) bucket may or may not
    emit independently; this test asserts that no marker carries the
    ``diff_<phase>_failed:`` substrate prefix on the clean path.
    """
    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "diff"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-Z substrate markers on the clean
    # envelope. Filter to substrate-CALL markers (have ``_failed:`` in the
    # middle) so unrelated ``diff_unknown_severity:*`` markers don't
    # accidentally trip this guard.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [m for m in (list(top_wo) + list(summary_wo)) if "_failed:" in m and m.startswith("diff_")]
    assert not substrate_markers, (
        f"clean diff must NOT surface diff_<phase>_failed: markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) get_changed_files failure -> diff_get_changed_files_failed marker
# ---------------------------------------------------------------------------


def test_diff_get_changed_files_failure_marker_format(cli_runner, diff_project, monkeypatch):
    """If get_changed_files raises, surface ``diff_get_changed_files_failed:``.

    Note: get_changed_files normally swallows OSError / TimeoutExpired
    internally and returns ``[]``. W607-Z catches escaping raises
    (e.g. an unexpected exception class) and produces a marker. This
    test simulates that by patching the symbol to raise a RuntimeError.
    """
    from roam.commands import cmd_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-get-changed-files-from-W607-Z")

    monkeypatch.setattr(cmd_diff, "get_changed_files", _raise)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    gcf_markers = [m for m in top_wo if m.startswith("diff_get_changed_files_failed:")]
    assert gcf_markers, f"expected diff_get_changed_files_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in gcf_markers), gcf_markers
    assert any("synthetic-get-changed-files-from-W607-Z" in m for m in gcf_markers), gcf_markers


# ---------------------------------------------------------------------------
# (3) resolve_changed_to_db failure -> diff_resolve_changed_to_db_failed marker
# ---------------------------------------------------------------------------


def test_diff_resolve_changed_to_db_failure_marker_format(cli_runner, diff_project, monkeypatch):
    """If resolve_changed_to_db raises, surface the substrate marker."""
    from roam.commands import cmd_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-resolve-from-W607-Z")

    monkeypatch.setattr(cmd_diff, "resolve_changed_to_db", _raise)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    rc_markers = [m for m in top_wo if m.startswith("diff_resolve_changed_to_db_failed:")]
    assert rc_markers, f"expected diff_resolve_changed_to_db_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (4) compute_risk_level failure -> diff_compute_risk_level_failed marker
# ---------------------------------------------------------------------------


def test_diff_compute_risk_level_failure_marker_format(cli_runner, diff_project, monkeypatch):
    """If _diff_risk_level raises, surface ``diff_compute_risk_level_failed:``."""
    from roam.commands import cmd_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-risk-level-from-W607-Z")

    monkeypatch.setattr(cmd_diff, "_diff_risk_level", _raise)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    rl_markers = [m for m in top_wo if m.startswith("diff_compute_risk_level_failed:")]
    assert rl_markers, f"expected diff_compute_risk_level_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (5) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_diff_warnings_out_in_envelope(cli_runner, diff_project, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-Z")

    monkeypatch.setattr(cmd_diff, "_diff_risk_level", _raise)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("diff_compute_risk_level_failed:")]
    assert markers, f"expected diff_compute_risk_level_failed: marker; got {data['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-Z" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (6) partial_success flips when ANY diff helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_diff_helper_raises(cli_runner, diff_project, monkeypatch):
    """Any non-empty W607-Z bucket -> summary.partial_success = True."""
    from roam.commands import cmd_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-Z")

    monkeypatch.setattr(cmd_diff, "_diff_risk_level", _raise)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, diff_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..Y contracts.
    """
    from roam.commands import cmd_diff

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-Z")

    monkeypatch.setattr(cmd_diff, "_diff_risk_level", _raise)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "compute_risk_level guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("diff_compute_risk_level_failed:")]
    assert failure_markers, f"expected diff_compute_risk_level_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "diff_compute_risk_level_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- ``diff_*`` not critique/relate/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_diff_not_critique_or_other(cli_runner, diff_project, monkeypatch):
    """Every surfaced W607-Z marker uses the canonical ``diff_*`` prefix.

    cmd_diff is the diff-INPUT pair complement -- distinct from sibling
    W607-* layers (critique / relate / deps / uses / impact / diagnose /
    preflight / pr_risk / audit / dashboard / doctor / health / describe /
    minimap / grep / history / refs_text / delete_check / search /
    complete / semantic / findings_query / dogfood / retrieve). Hard
    guard against accidental marker-prefix drift.
    """
    from roam.commands import cmd_diff

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-Z")

    monkeypatch.setattr(cmd_diff, "_diff_risk_level", _raise)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # Filter to substrate-CALL markers (skip the W641-followup-E
    # unknown-severity markers, which are a distinct axis on the same
    # warnings_out channel).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("diff_"), (
            f"every surfaced W607-Z marker must use the ``diff_*`` prefix family (cmd_diff scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("critique_", "cmd_critique W607-Y"),
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
# (9) Sibling parity -- W607-Y cmd_critique surface unchanged
# ---------------------------------------------------------------------------


def test_w607_y_cmd_critique_unaffected():
    """Sibling parity guard: W607-Y cmd_critique source surface unchanged.

    W607-Z lands only in cmd_diff. The W607-Y cmd_critique surface
    (per-helper ``_run_check`` wrapper + ``_w607y_warnings_out``
    accumulator + ``critique_*`` marker emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_critique.py"
    assert src_path.exists(), f"cmd_critique.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607y_warnings_out" in src, (
        "W607-Y accumulator removed from cmd_critique; W607-Z must not regress the sibling instrumentation."
    )
    assert "critique_{phase}_failed" in src, (
        "W607-Y marker prefix removed from cmd_critique; W607-Z must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (10) Source-level guard: cmd_diff carries the canonical W607-Z accumulator
# ---------------------------------------------------------------------------


def test_cmd_diff_carries_w607z_accumulator():
    """AST-level guard: cmd_diff source carries the W607-Z accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g., switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diff.py"
    assert src_path.exists(), f"cmd_diff.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607z_warnings_out" in src, (
        "W607-Z accumulator missing from cmd_diff; the substrate-CALL marker plumbing has been removed."
    )
    assert "diff_{phase}_failed" in src, (
        "W607-Z marker prefix template missing from cmd_diff; check the "
        '`f"diff_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside diff_cmd().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-Z ``_run_check`` helper not found in cmd_diff AST; the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (11) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_diff substrate boundary is wrapped.

    W607-Z substrate inventory (top boundaries):

    * get_changed_files        -- get_changed_files(root, ...)
    * resolve_changed_to_db    -- resolve_changed_to_db(conn, changed)
    * build_symbol_graph       -- build_symbol_graph(conn)
    * collect_affected_tests   -- _collect_affected_tests(conn, sym_by_file)
    * collect_coupling_warnings-- _collect_coupling_warnings(conn, file_map)
    * collect_fitness_violations -- _collect_fitness_violations(conn, file_map, root)
    * compute_risk_level       -- _diff_risk_level(...)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diff.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "get_changed_files",
        "resolve_changed_to_db",
        "build_symbol_graph",
        "collect_affected_tests",
        "collect_coupling_warnings",
        "collect_fitness_violations",
        "compute_risk_level",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes. The actual file
        # indentation depth varies (8/12/16/20/24 spaces) depending on
        # the site's nesting; accept any of the canonical depths.
        # The 8-space variant is included from the start per the W607-Y
        # follow-up lesson (one indent-variant gap was caught during
        # W607-Y's run on ``load_overrides``).
        same_line = f'_run_check("{phase}"' in src
        multi_line = (
            f'_run_check(\n        "{phase}"' in src
            or f'_run_check(\n            "{phase}"' in src
            or f'_run_check(\n                "{phase}"' in src
            or f'_run_check(\n                    "{phase}"' in src
            or f'_run_check(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-Z _run_check wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
