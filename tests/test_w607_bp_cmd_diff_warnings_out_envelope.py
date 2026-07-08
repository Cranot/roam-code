"""W607-BP -- additive aggregation-phase plumbing for ``cmd_diff``.

cmd_diff is the POST-EDIT SIGNAL SOURCE feeding into the critique gate
per the CLAUDE.md ``roam diff | roam critique`` chain. With W607-BP
landed, the agent-OS edit loop is W607-plumbed end-to-end on BOTH
layers across all FIVE edit-loop commands:

  - substrate-CALL layer: W607-R + W607-T + W607-S + W607-Y + W607-Z
  - aggregation-phase layer: W607-AW + W607-BB + W607-BH + W607-BL +
                             W607-BP

Each command has dual-bucket plumbing; each marker family is prefix-
isolated (``preflight_*`` / ``impact_*`` / ``diagnose_*`` /
``critique_*`` / ``diff_*``).

Relation to W607-Z
------------------

cmd_diff already carries W607-Z substrate-CALL plumbing covering
seven substrate-helper boundaries: get_changed_files /
resolve_changed_to_db / build_symbol_graph / collect_affected_tests /
collect_coupling_warnings / collect_fitness_violations /
compute_risk_level. W607-BP is ADDITIVE on top of W607-Z, extending
marker coverage to the AGGREGATION-PHASE boundaries that W607-Z left
unguarded:

  - ``severity_classify``    -- per-affected-symbol severity
                                classification (the inner
                                ``_diff_risk_level`` walk; a closed-
                                vocabulary refactor or future inner
                                threshold helper can raise here)
  - ``severity_normalize``   -- canonical W631 risk-LEVEL projection
                                (``normalize_risk_level`` + ``risk_rank``)
                                mirror of cmd_impact W607-BB / cmd_critique
                                W607-BL pattern
  - ``compute_verdict``      -- augmented_verdict text build with the
                                canonical risk_level suffix (LAW 6)
  - ``auto_log``             -- active-run ledger write (silent no-op
                                if no run is active, but the underlying
                                ``auto_log`` can still raise on HMAC
                                chain misshape or filesystem failures)
  - ``serialize_envelope``   -- ``json_envelope("diff", ...)`` projection

Both layers share the canonical ``diff_*`` marker family and the
``diff_<phase>_failed:<exc_class>:<detail>`` shape contract. The three
buckets (``_diff_warnings_out`` unknown-severity + ``_w607z_warnings_out``
substrate-CALL + ``_w607bp_warnings_out`` aggregation-phase) are
combined at envelope-emit time so consumers see the full degradation
lineage in marker-emission order.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_diff's aggregation-phase boundaries (severity_classify /
severity_normalize / compute_verdict / auto_log / serialize_envelope)
had no guards beyond the W607-Z compute_risk_level call. A downstream
refactor that changes the risk-level projection contract, the
canonical W631 vocabulary, the verdict string composition, the HMAC
chain on the runs ledger, or the ``json_envelope`` shape would crash
the envelope post-compute -- after the substrate signals were already
gathered, the agent loses the result. W607-BP wraps each boundary with
``_run_check_bp`` so a raise becomes a marker via ``warnings_out`` and
the envelope still emits.

Severity-classify degradation discipline
----------------------------------------

When the inner severity_classify boundary raises (e.g. a refactored
``_diff_risk_level``), the wrap floors the classified tier to ``None``
and surfaces ``severity_classification: "unknown"`` in the envelope
summary alongside the canonical W631 ``"low"`` floor on
``risk_level_canonical``. Mirror of cmd_critique W607-BL /
cmd_diagnose W607-BH severity_classification sentinel.

AGENT-OS EDIT LOOP 5-FOLD closure milestone
-------------------------------------------

cmd_diff is the POST-EDIT signal source AFTER cmd_preflight /
cmd_impact / cmd_diagnose (pre-edit triangle) and BEFORE
cmd_critique (post-edit gate). The five W607-* additive layers use
distinct marker prefixes (``preflight_*`` / ``impact_*`` /
``diagnose_*`` / ``critique_*`` / ``diff_*``) which coexist when all
five commands run on the same change scope. The
``test_edit_loop_5fold_marker_families_coexist`` integration test
confirms the five families do NOT collide.

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
# Helpers -- invoke diff / critique / diagnose / impact / preflight via Click
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


def _invoke_critique(runner: CliRunner, cwd, *extra, json_mode: bool = True, stdin: str | None = None):
    """Invoke ``roam critique`` for the agent-OS edit-loop integration test."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("critique")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, input=stdin, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _invoke_preflight(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam preflight`` for the agent-OS edit-loop integration test."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("preflight")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _invoke_impact(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam impact`` for the agent-OS edit-loop integration test."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("impact")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _invoke_diagnose(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam diagnose`` for the agent-OS edit-loop integration test."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("diagnose")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


_DIFF_TEXT = (
    "diff --git a/src/auth.py b/src/auth.py\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/auth.py\n"
    "+++ b/src/auth.py\n"
    "@@ -1,5 +1,6 @@\n"
    " from src.models import User\n"
    " \n"
    " def verify_token(t):\n"
    "+    # tweak\n"
    "     return User('test')\n"
    " \n"
)


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with an uncommitted edit so diff finds changes
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def diff_project(tmp_path, monkeypatch):
    """Indexed corpus with an uncommitted edit so diff sees changes + a
    resolvable target for the agent-OS 5-fold edit-loop integration test.
    """
    proj = tmp_path / "diff_w607bp_project"
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
    # A resolvable target for the edit-loop integration (cmd_preflight /
    # cmd_impact / cmd_diagnose / cmd_critique all need a callable target
    # name independent of the diff text).
    (src / "main.py").write_text(
        "def main_entry():\n    return diff_target()\n\n"
        "def diff_target():\n    return helper_one() + helper_two()\n\n"
        "def helper_one():\n    return 1\n\n"
        "def helper_two():\n    return 2\n",
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
# (1) Happy path -- clean diff -> envelope omits W607-BP markers
# ---------------------------------------------------------------------------


def test_diff_happy_path_no_w607bp_markers(cli_runner, diff_project):
    """Clean diff on a healthy corpus -> no W607-BP aggregation markers.

    Hash-stable: an empty W607-BP bucket on the success path must
    produce an envelope without any
    ``diff_severity_classify_failed:`` /
    ``diff_severity_normalize_failed:`` /
    ``diff_compute_verdict_failed:`` /
    ``diff_auto_log_failed:`` /
    ``diff_serialize_envelope_failed:`` markers. Mirror of W607-BL
    contract.
    """
    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "diff"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607bp_phases = (
        "diff_severity_classify_failed:",
        "diff_severity_normalize_failed:",
        "diff_compute_verdict_failed:",
        "diff_auto_log_failed:",
        "diff_serialize_envelope_failed:",
    )
    for prefix in w607bp_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean diff must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_bp`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_diff_carries_w607bp_accumulator():
    """AST-level guard: cmd_diff source carries the W607-BP accumulator.

    Pins the canonical W607-BP anchors so a future refactor that
    removes the additive instrumentation (or merges it back into
    W607-Z) fails this guard rather than silently regressing the
    aggregation-phase marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diff.py"
    assert src_path.exists(), f"cmd_diff.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "w607bp_warnings_out" in src, (
        "W607-BP accumulator missing from cmd_diff; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_bp" in src, (
        "W607-BP helper ``_run_check_bp`` missing from cmd_diff; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_bp is defined inside diff_cmd().
    tree = ast.parse(src)
    found_run_check_bp = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bp":
            found_run_check_bp = True
            break
    assert found_run_check_bp, (
        "W607-BP ``_run_check_bp`` helper not found in cmd_diff AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-Z must still be present (additive layer does NOT replace it)
    assert "w607z_warnings_out" in src, (
        "W607-Z accumulator vanished alongside the W607-BP add; the "
        "additive plumbing must preserve the W607-Z substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_bp():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_bp(...)`` with the canonical phase name.

    The five phases must appear inside a ``_run_check_bp("<phase>", ...)``
    call inside cmd_diff. Multi-indent variants (8, 12, 16, 20, 24
    spaces) are all considered valid wrap call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diff.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "severity_classify",
        "severity_normalize",
        "compute_verdict",
        "auto_log",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_bp(\n        "{phase}"',
            f'_run_check_bp(\n            "{phase}"',
            f'_run_check_bp(\n                "{phase}"',
            f'_run_check_bp(\n                    "{phase}"',
            f'_run_check_bp(\n                        "{phase}"',
            f'_run_check_bp("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_bp(...); add the W607-BP guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) Marker shape -- ``diff_<phase>_failed:<exc>:<detail>``
# ---------------------------------------------------------------------------


def test_auto_log_failure_marker_format(cli_runner, diff_project, monkeypatch):
    """If ``auto_log`` raises, surface ``diff_auto_log_failed:`` and keep
    the diff envelope intact.

    Discipline mirror of the W607-BL auto_log-failure pattern in
    cmd_critique. The auto_log boundary writes to the active run
    ledger when one is open -- a raise here would otherwise crash the
    envelope AFTER the success envelope was already built.
    """
    from roam.commands import cmd_diff

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-auto-log-from-W607-BP")

    monkeypatch.setattr(cmd_diff, "auto_log", _raise_auto_log)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diff_auto_log_failed:")]
    assert markers, f"expected ``diff_auto_log_failed:`` marker; got {top_wo!r}"
    marker = markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-auto-log-from-W607-BP" in parts[2], parts

    # Envelope still emits the core diff fields
    for key in ("changed_files", "affected_symbols", "affected_files"):
        assert key in data, (
            f"envelope must still emit ``{key}`` when auto_log raises; got keys = {sorted(data.keys())!r}"
        )


# ---------------------------------------------------------------------------
# (5) SEVERITY CLASSIFY DEGRADATION discipline
# ---------------------------------------------------------------------------


def test_severity_classify_degradation_surfaces_unknown_sentinel(cli_runner, diff_project, monkeypatch):
    """When the severity_classify boundary raises:

    1. Marker ``diff_severity_classify_failed:`` appears
    2. Envelope still emits the core diff signal blocks
    3. Summary stamps ``severity_classification: "unknown"`` sentinel
    4. Summary still carries the canonical floor ``risk_level_canonical: "low"``

    The underlying action (emit the diff envelope) stays -- degraded
    outcomes are valid design. The LIE we prevent is a clean classified
    verdict when severity_classify actually raised. Mirror of
    cmd_critique's severity_classify degradation pattern.

    NOTE on patching: the W607-Z layer ALSO calls ``_diff_risk_level``
    (substrate-CALL boundary, ``compute_risk_level`` phase). Patching the
    helper raises on BOTH layers; both surface markers and the W607-Z
    wrap floors to ``"low"``. The W607-BP-driven sentinel
    (``severity_classification: "unknown"``) is the new behavior under
    test.
    """
    from roam.commands import cmd_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-severity-classify-from-W607-BP")

    monkeypatch.setattr(cmd_diff, "_diff_risk_level", _raise)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # (1) marker appears -- W607-BP severity_classify
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diff_severity_classify_failed:")]
    assert markers, f"expected ``diff_severity_classify_failed:`` marker; got {top_wo!r}"

    # (2) envelope still emits the diff signal blocks
    summary = data["summary"]
    assert "changed_files" in data, (
        f"envelope must still emit ``changed_files`` even when "
        f"severity_classify raises; got keys = {sorted(data.keys())!r}"
    )
    assert "affected_symbols" in data, (
        f"envelope must still emit ``affected_symbols`` even when "
        f"severity_classify raises; got keys = {sorted(data.keys())!r}"
    )

    # (3) severity_classification sentinel
    assert summary.get("severity_classification") == "unknown", (
        f'summary must stamp ``severity_classification: "unknown"`` when '
        f"severity_classify raises; got "
        f"{summary.get('severity_classification')!r}"
    )

    # (4) canonical floor still emitted
    assert summary.get("risk_level_canonical") == "low", (
        f'summary must floor ``risk_level_canonical`` to ``"low"`` on '
        f"severity_classify raise; got "
        f"{summary.get('risk_level_canonical')!r}"
    )


def test_severity_classify_clean_path_stamps_classified(cli_runner, diff_project):
    """Happy path: ``severity_classification`` summary field is ``"classified"``.

    Mirror of the W607-BL discipline that the sentinel disambiguates a
    real classified verdict from a degraded "unknown" floor. Mirror of
    cmd_impact's W607-BB / cmd_diagnose's W607-BH / cmd_critique's
    W607-BL ``"classified"`` contract.
    """
    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("severity_classification") == "classified", (
        f'clean path must stamp ``severity_classification: "classified"``; '
        f"got {data['summary'].get('severity_classification')!r}"
    )


# ---------------------------------------------------------------------------
# (6) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, diff_project, monkeypatch):
    """ANY W607-BP or W607-Z marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    diff" from "diff ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_diff

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-BP")

    monkeypatch.setattr(cmd_diff, "auto_log", _raise_auto_log)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-BP warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607bp_warnings_out_in_both_top_and_summary(cli_runner, diff_project, monkeypatch):
    """Non-empty W607-BP bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-Y / W607-BL contract: top-level is needed
    because the preserved-list field survives ``strip_list_payloads``
    in default-detail mode; summary mirror gives consumers reading
    only the summary block visibility too.
    """
    from roam.commands import cmd_diff

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BP")

    monkeypatch.setattr(cmd_diff, "auto_log", _raise_auto_log)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BP raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BP raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("diff_auto_log_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("diff_auto_log_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the auto_log marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-Z COEXISTENCE -- both buckets surface in combined envelope
# ---------------------------------------------------------------------------


def test_combined_w607z_and_w607bp_markers_both_surface(cli_runner, diff_project, monkeypatch):
    """W607-Z and W607-BP markers BOTH surface when raises occur on each
    layer simultaneously.

    The additive plumbing must not shadow the W607-Z bucket -- agents
    must see the full degradation lineage in marker-emission order.
    Mirror of cmd_critique's W607-Y + W607-BL combined test (regression
    guard ensuring the pre-existing W607-Z layer survives the additive
    W607-BP plumbing).
    """
    from roam.commands import cmd_diff

    def _raise_collect_coupling(*a, **kw):
        # W607-Z substrate boundary
        raise RuntimeError("synthetic-coupling-from-W607-BP-combined")

    def _raise_auto_log(*a, **kw):
        # W607-BP aggregation boundary
        raise RuntimeError("synthetic-auto-log-from-W607-BP-combined")

    monkeypatch.setattr(cmd_diff, "_collect_coupling_warnings", _raise_collect_coupling)
    monkeypatch.setattr(cmd_diff, "auto_log", _raise_auto_log)

    # --coupling flag triggers the W607-Z collect_coupling_warnings call.
    result = _invoke_diff(cli_runner, diff_project, "--coupling")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    z_markers = [m for m in top_wo if m.startswith("diff_collect_coupling_warnings_failed:")]
    bp_markers = [m for m in top_wo if m.startswith("diff_auto_log_failed:")]
    assert z_markers, f"W607-Z collect_coupling_warnings marker missing; got {top_wo!r}"
    assert bp_markers, f"W607-BP auto_log marker missing; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-BP uses the SAME ``diff_*`` family
# ---------------------------------------------------------------------------


def test_w607bp_marker_prefix_diff_family(cli_runner, diff_project, monkeypatch):
    """W607-BP markers use the canonical ``diff_*`` prefix (same family
    as W607-Z; W607-BP is ADDITIVE, not a separate prefix).

    Hard guard: any W607-BP marker that leaks into a sibling W607-*
    family (e.g. ``preflight_*`` / ``impact_*`` / ``diagnose_*`` /
    ``critique_*``) breaks the closed-enum marker-family contract
    pinned in the W607-Z test.
    """
    from roam.commands import cmd_diff

    def _raise_auto_log(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BP")

    monkeypatch.setattr(cmd_diff, "auto_log", _raise_auto_log)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-discipline check"
    for marker in top_wo:
        assert marker.startswith("diff_"), f"every W607-BP marker must use the ``diff_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (10) AGENT-OS EDIT LOOP 5-FOLD closure -- five marker families coexist
# ---------------------------------------------------------------------------


def test_edit_loop_5fold_marker_families_coexist(cli_runner, diff_project, monkeypatch):
    """cmd_preflight (W607-AW + W607-R), cmd_impact (W607-BB + W607-T),
    cmd_diagnose (W607-BH + W607-S), cmd_critique (W607-BL + W607-Y),
    and cmd_diff (W607-BP + W607-Z) use distinct marker families that
    coexist when all FIVE run on the same change scope.

    Integration: simulate a degraded auto_log in ALL FIVE commands
    (each command has its own ``from roam.runs.helpers import auto_log``
    import binding so monkeypatching one module's ``auto_log`` does NOT
    affect siblings). Run all five commands and assert each surfaces
    its own marker family with no cross-prefix leakage.

    This is the AGENT-OS EDIT LOOP 5-FOLD closure milestone: with
    W607-BP landed, all FIVE edit-loop commands (pre-edit triangle +
    diff signal source + post-edit gate) are W607-plumbed end-to-end on
    both the substrate-CALL layer (W607-R + W607-T + W607-S + W607-Y +
    W607-Z) AND the aggregation-phase layer (W607-AW + W607-BB +
    W607-BH + W607-BL + W607-BP).
    """
    from roam.commands import (
        cmd_critique,
        cmd_diagnose,
        cmd_diff,
        cmd_impact,
        cmd_preflight,
    )

    def _raise_diff_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-diff-auto-log-5fold")

    def _raise_critique_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-critique-auto-log-5fold")

    def _raise_diagnose_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-diagnose-auto-log-5fold")

    def _raise_impact_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-impact-auto-log-5fold")

    def _raise_preflight_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-preflight-auto-log-5fold")

    monkeypatch.setattr(cmd_diff, "auto_log", _raise_diff_auto_log)
    monkeypatch.setattr(cmd_critique, "auto_log", _raise_critique_auto_log)
    monkeypatch.setattr(cmd_diagnose, "auto_log", _raise_diagnose_auto_log)
    monkeypatch.setattr(cmd_impact, "auto_log", _raise_impact_auto_log)
    monkeypatch.setattr(cmd_preflight, "auto_log", _raise_preflight_auto_log)

    # Run diff -> expect ``diff_*`` markers, NO sibling-family leakage.
    diff_result = _invoke_diff(cli_runner, diff_project)
    assert diff_result.exit_code == 0, diff_result.output
    diff_data = _json.loads(diff_result.output)
    diff_wo = diff_data.get("warnings_out") or []
    diff_markers = [m for m in diff_wo if m.startswith("diff_auto_log_failed:")]
    assert diff_markers, f"cmd_diff must surface ``diff_auto_log_failed:`` markers; got {diff_wo!r}"
    for foreign_prefix in ("preflight_", "impact_", "diagnose_", "critique_"):
        leaked = [m for m in diff_wo if m.startswith(foreign_prefix)]
        assert not leaked, f"cmd_diff warnings_out must not contain {foreign_prefix}* markers; got {leaked!r}"

    # Run critique -> expect ``critique_*`` markers.
    critique_result = _invoke_critique(cli_runner, diff_project, stdin=_DIFF_TEXT)
    assert critique_result.exit_code == 0, critique_result.output
    critique_data = _json.loads(critique_result.output)
    critique_wo = critique_data.get("warnings_out") or []
    critique_markers = [m for m in critique_wo if m.startswith("critique_auto_log_failed:")]
    assert critique_markers, f"cmd_critique must surface ``critique_auto_log_failed:`` markers; got {critique_wo!r}"
    for foreign_prefix in ("preflight_", "impact_", "diagnose_", "diff_"):
        leaked = [m for m in critique_wo if m.startswith(foreign_prefix)]
        assert not leaked, f"cmd_critique warnings_out must not contain {foreign_prefix}* markers; got {leaked!r}"

    # Run diagnose -> expect ``diagnose_*`` markers.
    diagnose_result = _invoke_diagnose(cli_runner, diff_project, "diff_target")
    assert diagnose_result.exit_code == 0, diagnose_result.output
    diagnose_data = _json.loads(diagnose_result.output)
    diagnose_wo = diagnose_data.get("warnings_out") or []
    diagnose_markers = [m for m in diagnose_wo if m.startswith("diagnose_auto_log_failed:")]
    assert diagnose_markers, f"cmd_diagnose must surface ``diagnose_auto_log_failed:`` markers; got {diagnose_wo!r}"
    for foreign_prefix in ("preflight_", "impact_", "critique_", "diff_"):
        leaked = [m for m in diagnose_wo if m.startswith(foreign_prefix)]
        assert not leaked, f"cmd_diagnose warnings_out must not contain {foreign_prefix}* markers; got {leaked!r}"

    # Run impact -> expect ``impact_*`` markers.
    impact_result = _invoke_impact(cli_runner, diff_project, "diff_target")
    assert impact_result.exit_code == 0, impact_result.output
    impact_data = _json.loads(impact_result.output)
    impact_wo = impact_data.get("warnings_out") or []
    impact_markers = [m for m in impact_wo if m.startswith("impact_auto_log_failed:")]
    assert impact_markers, f"cmd_impact must surface ``impact_auto_log_failed:`` markers; got {impact_wo!r}"
    for foreign_prefix in ("preflight_", "diagnose_", "critique_", "diff_"):
        leaked = [m for m in impact_wo if m.startswith(foreign_prefix)]
        assert not leaked, f"cmd_impact warnings_out must not contain {foreign_prefix}* markers; got {leaked!r}"

    # Run preflight -> expect ``preflight_*`` markers.
    preflight_result = _invoke_preflight(cli_runner, diff_project, "diff_target")
    assert preflight_result.exit_code == 0, preflight_result.output
    preflight_data = _json.loads(preflight_result.output)
    preflight_wo = preflight_data.get("warnings_out") or []
    preflight_markers = [m for m in preflight_wo if m.startswith("preflight_auto_log_failed:")]
    assert preflight_markers, f"cmd_preflight must surface ``preflight_auto_log_failed:`` markers; got {preflight_wo!r}"
    for foreign_prefix in ("impact_", "diagnose_", "critique_", "diff_"):
        leaked = [m for m in preflight_wo if m.startswith(foreign_prefix)]
        assert not leaked, f"cmd_preflight warnings_out must not contain {foreign_prefix}* markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (11) Canonical risk-LEVEL emission -- top-level + summary mirror
# ---------------------------------------------------------------------------


def test_canonical_risk_level_emitted_on_success_path(cli_runner, diff_project):
    """Success path emits ``risk_level_canonical`` + ``risk_rank`` on
    BOTH top-level envelope AND summary.

    Mirror of cmd_impact's W641-followup-A + cmd_critique's W607-BL
    canonical-emit pattern. Cross-command consumers can call
    ``risk_rank(data["summary"]["risk_level_canonical"]) >= 3`` to
    gate on high-or-worse without re-deriving the threshold table at
    the call site (Pattern-3a).
    """
    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Summary mirror
    summary = data["summary"]
    assert "risk_level_canonical" in summary, (
        f"summary must emit ``risk_level_canonical``; got summary = {sorted(summary.keys())!r}"
    )
    assert "risk_rank" in summary, f"summary must emit ``risk_rank``; got summary = {sorted(summary.keys())!r}"
    assert summary["risk_level_canonical"] in (
        "critical",
        "high",
        "medium",
        "low",
    ), f"summary.risk_level_canonical must be in canonical W631 set; got {summary['risk_level_canonical']!r}"

    # Top-level mirror
    assert "risk_level_canonical" in data, (
        f"top-level envelope must emit ``risk_level_canonical``; got keys = {sorted(data.keys())!r}"
    )
    assert "risk_rank" in data, f"top-level envelope must emit ``risk_rank``; got keys = {sorted(data.keys())!r}"

    # Verdict suffix carries the canonical bucket per LAW 6
    assert f"risk_level {summary['risk_level_canonical']}" in summary["verdict"], (
        f"verdict must carry the canonical risk_level bucket per LAW 6; got verdict = {summary['verdict']!r}"
    )


# ---------------------------------------------------------------------------
# (12) Serialize envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607bp_serialize_envelope_floor_on_raise(cli_runner, diff_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``diff_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("diff", ...)`` would otherwise crash AFTER all
    substrate + aggregation signals were already gathered. The consumer
    must still receive a parseable JSON object with the marker attached
    + the canonical command name.
    """
    from roam.commands import cmd_diff

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-BP")

    monkeypatch.setattr(cmd_diff, "json_envelope", _raise_envelope)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "diff", f"envelope stub must carry the canonical command name on raise; got {data!r}"
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diff_serialize_envelope_failed:")]
    assert markers, f"expected ``diff_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (13) Compute-verdict guard -- raise floors to a stable verdict
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, diff_project, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    We force the compute_verdict closure to raise by patching
    ``normalize_risk_level`` to return an object whose ``__format__``
    raises -- the verdict f-string interpolation of risk_level_canonical
    then trips the wrap. Same approach as cmd_critique's
    test_compute_verdict_failure_marker_format (via aggregate patching),
    adapted to cmd_diff's call site.
    """
    from roam.commands import cmd_diff

    class _BadLevel:
        def __str__(self):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BP")

        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BP")

    # We need risk_level_canonical to be the _BadLevel instance.
    # cmd_diff calls ``normalize_risk_level(...) or "low"`` inside the
    # severity_normalize wrap, then f-strings it. Patch normalize_risk_level
    # to return _BadLevel(); ``or "low"`` keeps the bad instance because
    # _BadLevel() is truthy.
    def _bad_normalize(level):
        return _BadLevel()

    monkeypatch.setattr(cmd_diff, "normalize_risk_level", _bad_normalize)

    result = _invoke_diff(cli_runner, diff_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diff_compute_verdict_failed:")]
    assert markers, f"expected ``diff_compute_verdict_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
