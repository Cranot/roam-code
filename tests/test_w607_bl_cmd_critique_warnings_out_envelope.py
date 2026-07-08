"""W607-BL -- additive aggregation-phase plumbing for ``cmd_critique``.

cmd_critique is the POST-EDIT GATE completing the agent-OS edit loop:

    pre-edit triangle (preflight / impact / diagnose) -> EDIT -> critique

With W607-BL landed, the agent-OS edit loop is W607-plumbed end-to-end
on BOTH layers:

  - substrate-CALL layer: W607-R + W607-T + W607-S + W607-Y
  - aggregation-phase layer: W607-AW + W607-BB + W607-BH + W607-BL

Each command has dual-bucket plumbing; each marker family is prefix-
isolated (``preflight_*`` / ``impact_*`` / ``diagnose_*`` /
``critique_*``).

Relation to W607-Y
------------------

cmd_critique already carries W607-Y substrate-CALL plumbing covering
eight substrate-helper boundaries: parse_diff / find_changed_symbols /
run_checks / aggregate / emit_findings / load_overrides /
bench_relevance_hint / compute_risk_level. W607-BL is ADDITIVE on top
of W607-Y, extending marker coverage to the AGGREGATION-PHASE
boundaries that W607-Y left unguarded:

  - ``severity_classify``    -- per-finding severity classification
                                (the inner ``_critique_risk_level``
                                walk; a closed-vocabulary refactor or
                                future per-finding inspection helper
                                can raise here)
  - ``severity_normalize``   -- canonical W631 risk-LEVEL projection
                                (``normalize_risk_level`` + ``risk_rank``)
                                mirror of cmd_impact W607-BB pattern
  - ``compute_verdict``      -- augmented_verdict text build with the
                                canonical risk_level suffix (LAW 6)
  - ``auto_log``             -- active-run ledger write (silent no-op
                                if no run is active, but the underlying
                                ``auto_log`` can still raise on HMAC
                                chain misshape or filesystem failures)
  - ``serialize_envelope``   -- ``json_envelope("critique", ...)``
                                projection

Both layers share the canonical ``critique_*`` marker family and the
``critique_<phase>_failed:<exc_class>:<detail>`` shape contract. The
three buckets (``_critique_warnings_out`` unknown-severity +
``_w607y_warnings_out`` substrate-CALL + ``_w607bl_warnings_out``
aggregation-phase) are combined at envelope-emit time so consumers see
the full degradation lineage in marker-emission order.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_critique's aggregation-phase boundaries (severity_classify /
severity_normalize / compute_verdict / auto_log / serialize_envelope)
had no guards beyond the W607-Y compute_risk_level call. A downstream
refactor that changes the risk-level projection contract, the
canonical W631 vocabulary, the verdict string composition, the HMAC
chain on the runs ledger, or the ``json_envelope`` shape would crash
the envelope post-compute -- after the substrate signals were already
gathered, the agent loses the result. W607-BL wraps each boundary with
``_run_check_bl`` so a raise becomes a marker via ``warnings_out`` and
the envelope still emits.

Severity-classify degradation discipline
----------------------------------------

When the inner severity_classify boundary raises (e.g. a refactored
``_critique_risk_level``), the wrap floors the classified tier to
``None`` and surfaces ``severity_classification: "unknown"`` in the
envelope summary alongside the canonical W631 ``"low"`` floor on
``risk_level_canonical``. The
``test_severity_classify_degradation_surfaces_unknown_sentinel`` guard
asserts the sentinel appears AND the raw findings still emit (don't
crash).

AGENT-OS EDIT LOOP closure milestone
------------------------------------

cmd_critique runs AFTER cmd_preflight / cmd_impact / cmd_diagnose. The
W607-AW additive layer on cmd_preflight uses ``preflight_*`` markers;
W607-BB on cmd_impact uses ``impact_*``; W607-BH on cmd_diagnose uses
``diagnose_*``; W607-BL on cmd_critique uses ``critique_*``. The four
families are distinct per the marker-prefix discipline test, but they
coexist when all four commands run on the same change scope. The
``test_edit_loop_marker_families_coexist`` integration test confirms
the four families do NOT collide.

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
# Helpers -- invoke critique / diagnose / impact / preflight via the Click group
# ---------------------------------------------------------------------------


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


def _invoke_critique(runner: CliRunner, cwd, *extra, json_mode: bool = True, stdin: str | None = None):
    """Invoke ``roam critique`` through the group so ``--json`` is honoured."""
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


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with diff target file + resolvable symbol
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def critique_project(tmp_path, monkeypatch):
    """Indexed corpus with a symbol the diff modifies + a resolvable
    target for the agent-OS edit-loop integration test.
    """
    proj = tmp_path / "critique_w607bl_project"
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
    # An additional resolvable symbol for the edit-loop integration
    # (cmd_preflight / cmd_impact / cmd_diagnose all need a callable
    # target name independent of the diff text).
    (src / "main.py").write_text(
        "def main_entry():\n    return critique_target()\n\n"
        "def critique_target():\n    return helper_one() + helper_two()\n\n"
        "def helper_one():\n    return 1\n\n"
        "def helper_two():\n    return 2\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- clean critique -> envelope omits W607-BL markers
# ---------------------------------------------------------------------------


def test_critique_happy_path_no_w607bl_markers(cli_runner, critique_project):
    """Clean critique on a healthy diff -> no W607-BL aggregation markers.

    Hash-stable: an empty W607-BL bucket on the success path must
    produce an envelope without any
    ``critique_severity_classify_failed:`` /
    ``critique_severity_normalize_failed:`` /
    ``critique_compute_verdict_failed:`` /
    ``critique_auto_log_failed:`` /
    ``critique_serialize_envelope_failed:`` markers. Mirrors W607-BH
    contract.
    """
    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "critique"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607bl_phases = (
        "critique_severity_classify_failed:",
        "critique_severity_normalize_failed:",
        "critique_compute_verdict_failed:",
        "critique_auto_log_failed:",
        "critique_serialize_envelope_failed:",
    )
    for prefix in w607bl_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean critique must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_bl`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_critique_carries_w607bl_accumulator():
    """AST-level guard: cmd_critique source carries the W607-BL accumulator.

    Pins the canonical W607-BL anchors so a future refactor that
    removes the additive instrumentation (or merges it back into
    W607-Y) fails this guard rather than silently regressing the
    aggregation-phase marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_critique.py"
    assert src_path.exists(), f"cmd_critique.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "w607bl_warnings_out" in src, (
        "W607-BL accumulator missing from cmd_critique; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_bl" in src, (
        "W607-BL helper ``_run_check_bl`` missing from cmd_critique; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_bl is defined inside critique().
    tree = ast.parse(src)
    found_run_check_bl = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bl":
            found_run_check_bl = True
            break
    assert found_run_check_bl, (
        "W607-BL ``_run_check_bl`` helper not found in cmd_critique AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-Y must still be present (additive layer does NOT replace it)
    assert "w607y_warnings_out" in src, (
        "W607-Y accumulator vanished alongside the W607-BL add; the "
        "additive plumbing must preserve the W607-Y substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_bl():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_bl(...)`` with the canonical phase name.

    The five phases must appear inside a ``_run_check_bl("<phase>", ...)``
    call inside cmd_critique. Multi-indent variants (8, 12, 16, 20, 24
    spaces) are all considered valid wrap call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_critique.py"
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
            f'_run_check_bl(\n        "{phase}"',
            f'_run_check_bl(\n            "{phase}"',
            f'_run_check_bl(\n                "{phase}"',
            f'_run_check_bl(\n                    "{phase}"',
            f'_run_check_bl(\n                        "{phase}"',
            f'_run_check_bl("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_bl(...); add the W607-BL guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) Marker shape -- ``critique_<phase>_failed:<exc>:<detail>``
# ---------------------------------------------------------------------------


def test_auto_log_failure_marker_format(cli_runner, critique_project, monkeypatch):
    """If ``auto_log`` raises, surface ``critique_auto_log_failed:`` and
    keep the critique envelope intact.

    Discipline mirror of the W607-BH auto_log-failure pattern in
    cmd_diagnose. The auto_log boundary writes to the active run ledger
    when one is open -- a raise here would otherwise crash the envelope
    AFTER the success envelope was already built.
    """
    from roam.commands import cmd_critique

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-auto-log-from-W607-BL")

    monkeypatch.setattr(cmd_critique, "auto_log", _raise_auto_log)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("critique_auto_log_failed:")]
    assert markers, f"expected ``critique_auto_log_failed:`` marker; got {top_wo!r}"
    marker = markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-auto-log-from-W607-BL" in parts[2], parts

    # Envelope still emits the core critique fields
    for key in ("findings", "severity_breakdown", "changed_symbols"):
        assert key in data, (
            f"envelope must still emit ``{key}`` when auto_log raises; got keys = {sorted(data.keys())!r}"
        )


# ---------------------------------------------------------------------------
# (5) SEVERITY CLASSIFY DEGRADATION discipline
# ---------------------------------------------------------------------------


def test_severity_classify_degradation_surfaces_unknown_sentinel(cli_runner, critique_project, monkeypatch):
    """When the severity_classify boundary raises:

    1. Marker ``critique_severity_classify_failed:`` appears
    2. Envelope still emits the findings + severity_breakdown
    3. Summary stamps ``severity_classification: "unknown"`` sentinel
    4. Summary still carries the canonical floor ``risk_level_canonical: "low"``

    The underlying action (emit the critique envelope) stays --
    degraded outcomes are valid design. The LIE we prevent is a clean
    classified verdict when severity_classify actually raised.
    Mirror of cmd_diagnose's severity_normalize degradation pattern.
    """
    from roam.commands import cmd_critique

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-severity-classify-from-W607-BL")

    monkeypatch.setattr(cmd_critique, "_critique_risk_level", _raise)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # (1) marker appears
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("critique_severity_classify_failed:")]
    assert markers, f"expected ``critique_severity_classify_failed:`` marker; got {top_wo!r}"

    # (2) envelope still emits the critique signal blocks
    summary = data["summary"]
    assert "findings" in data, (
        f"envelope must still emit ``findings`` even when severity_classify raises; got keys = {sorted(data.keys())!r}"
    )
    assert "severity_breakdown" in data, (
        f"envelope must still emit ``severity_breakdown`` even when "
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


def test_severity_classify_clean_path_stamps_classified(cli_runner, critique_project):
    """Happy path: ``severity_classification`` summary field is ``"classified"``.

    Mirrors the W607-BL discipline that the sentinel disambiguates a
    real classified verdict from a degraded "unknown" floor. Mirror of
    cmd_impact's W607-BB and cmd_diagnose's W607-BH ``"classified"``
    contract.
    """
    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("severity_classification") == "classified", (
        f'clean path must stamp ``severity_classification: "classified"``; '
        f"got {data['summary'].get('severity_classification')!r}"
    )


# ---------------------------------------------------------------------------
# (6) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, critique_project, monkeypatch):
    """ANY W607-BL or W607-Y marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    critique" from "critique ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_critique

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-BL")

    monkeypatch.setattr(cmd_critique, "auto_log", _raise_auto_log)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-BL warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607bl_warnings_out_in_both_top_and_summary(cli_runner, critique_project, monkeypatch):
    """Non-empty W607-BL bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-Y / W607-BH contract: top-level is needed
    because the preserved-list field survives ``strip_list_payloads``
    in default-detail mode; summary mirror gives consumers reading
    only the summary block visibility too.
    """
    from roam.commands import cmd_critique

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BL")

    monkeypatch.setattr(cmd_critique, "auto_log", _raise_auto_log)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BL raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BL raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("critique_auto_log_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("critique_auto_log_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the auto_log marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-Y COEXISTENCE -- both buckets surface in combined envelope
# ---------------------------------------------------------------------------


def test_combined_w607y_and_w607bl_markers_both_surface(cli_runner, critique_project, monkeypatch):
    """W607-Y and W607-BL markers BOTH surface when raises occur on
    each layer simultaneously.

    The additive plumbing must not shadow the W607-Y bucket -- agents
    must see the full degradation lineage in marker-emission order.
    Mirror of cmd_diagnose's W607-S + W607-BH combined test.
    """
    from roam.commands import cmd_critique

    def _raise_parse(*a, **kw):
        # W607-Y substrate boundary
        raise RuntimeError("synthetic-parse-from-W607-BL-combined")

    def _raise_auto_log(*a, **kw):
        # W607-BL aggregation boundary
        raise RuntimeError("synthetic-auto-log-from-W607-BL-combined")

    monkeypatch.setattr(cmd_critique, "parse_diff", _raise_parse)
    monkeypatch.setattr(cmd_critique, "auto_log", _raise_auto_log)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    y_markers = [m for m in top_wo if m.startswith("critique_parse_diff_failed:")]
    bl_markers = [m for m in top_wo if m.startswith("critique_auto_log_failed:")]
    assert y_markers, f"W607-Y parse_diff marker missing; got {top_wo!r}"
    assert bl_markers, f"W607-BL auto_log marker missing; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-BL uses the SAME ``critique_*`` family
# ---------------------------------------------------------------------------


def test_w607bl_marker_prefix_critique_family(cli_runner, critique_project, monkeypatch):
    """W607-BL markers use the canonical ``critique_*`` prefix
    (same family as W607-Y; W607-BL is ADDITIVE, not a separate prefix).

    Hard guard: any W607-BL marker that leaks into a sibling W607-*
    family (e.g. ``preflight_*`` / ``impact_*`` / ``diagnose_*``)
    breaks the closed-enum marker-family contract pinned in the W607-Y
    test.
    """
    from roam.commands import cmd_critique

    def _raise_auto_log(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BL")

    monkeypatch.setattr(cmd_critique, "auto_log", _raise_auto_log)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-discipline check"
    for marker in top_wo:
        assert marker.startswith("critique_"), (
            f"every W607-BL marker must use the ``critique_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (10) AGENT-OS EDIT LOOP closure -- four marker families coexist
# ---------------------------------------------------------------------------


def test_edit_loop_marker_families_coexist(cli_runner, critique_project, monkeypatch):
    """cmd_preflight (W607-AW + W607-R), cmd_impact (W607-BB + W607-T),
    cmd_diagnose (W607-BH + W607-S), and cmd_critique (W607-BL + W607-Y)
    use distinct marker families that coexist when all four run on
    the same change scope.

    Integration: simulate a degraded auto_log in ALL FOUR commands
    (the same ``auto_log`` import resolves to different module
    attributes; monkeypatching the cmd_critique module's ``auto_log``
    does NOT affect cmd_diagnose's or cmd_impact's or cmd_preflight's
    because each command has its own ``from roam.runs.helpers import
    auto_log`` import). Run all four commands and assert each surfaces
    its own marker family with no cross-prefix leakage.

    This is the AGENT-OS EDIT LOOP closure milestone: with W607-BL
    landed, all four edit-loop commands (pre-edit triangle + post-edit
    gate) are W607-plumbed end-to-end on both the substrate-CALL layer
    (W607-R + W607-T + W607-S + W607-Y) AND the aggregation-phase
    layer (W607-AW + W607-BB + W607-BH + W607-BL).
    """
    from roam.commands import cmd_critique, cmd_diagnose, cmd_impact, cmd_preflight

    def _raise_critique_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-critique-auto-log-loop")

    def _raise_diagnose_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-diagnose-auto-log-loop")

    def _raise_impact_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-impact-auto-log-loop")

    def _raise_preflight_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-preflight-auto-log-loop")

    monkeypatch.setattr(cmd_critique, "auto_log", _raise_critique_auto_log)
    monkeypatch.setattr(cmd_diagnose, "auto_log", _raise_diagnose_auto_log)
    monkeypatch.setattr(cmd_impact, "auto_log", _raise_impact_auto_log)
    monkeypatch.setattr(cmd_preflight, "auto_log", _raise_preflight_auto_log)

    # Run critique -> expect ``critique_*`` markers.
    critique_result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert critique_result.exit_code == 0, critique_result.output
    critique_data = _json.loads(critique_result.output)
    critique_wo = critique_data.get("warnings_out") or []
    critique_markers = [m for m in critique_wo if m.startswith("critique_auto_log_failed:")]
    assert critique_markers, f"cmd_critique must surface ``critique_auto_log_failed:`` markers; got {critique_wo!r}"
    # No cross-family leakage from critique into preflight/impact/diagnose.
    for foreign_prefix in ("preflight_", "impact_", "diagnose_"):
        leaked = [m for m in critique_wo if m.startswith(foreign_prefix)]
        assert not leaked, f"cmd_critique warnings_out must not contain {foreign_prefix}* markers; got {leaked!r}"

    # Run diagnose -> expect ``diagnose_*`` markers.
    diagnose_result = _invoke_diagnose(cli_runner, critique_project, "critique_target")
    assert diagnose_result.exit_code == 0, diagnose_result.output
    diagnose_data = _json.loads(diagnose_result.output)
    diagnose_wo = diagnose_data.get("warnings_out") or []
    diagnose_markers = [m for m in diagnose_wo if m.startswith("diagnose_auto_log_failed:")]
    assert diagnose_markers, f"cmd_diagnose must surface ``diagnose_auto_log_failed:`` markers; got {diagnose_wo!r}"
    for foreign_prefix in ("preflight_", "impact_", "critique_"):
        leaked = [m for m in diagnose_wo if m.startswith(foreign_prefix)]
        assert not leaked, f"cmd_diagnose warnings_out must not contain {foreign_prefix}* markers; got {leaked!r}"

    # Run impact -> expect ``impact_*`` markers.
    impact_result = _invoke_impact(cli_runner, critique_project, "critique_target")
    assert impact_result.exit_code == 0, impact_result.output
    impact_data = _json.loads(impact_result.output)
    impact_wo = impact_data.get("warnings_out") or []
    impact_markers = [m for m in impact_wo if m.startswith("impact_auto_log_failed:")]
    assert impact_markers, f"cmd_impact must surface ``impact_auto_log_failed:`` markers; got {impact_wo!r}"
    for foreign_prefix in ("preflight_", "diagnose_", "critique_"):
        leaked = [m for m in impact_wo if m.startswith(foreign_prefix)]
        assert not leaked, f"cmd_impact warnings_out must not contain {foreign_prefix}* markers; got {leaked!r}"

    # Run preflight -> expect ``preflight_*`` markers.
    preflight_result = _invoke_preflight(cli_runner, critique_project, "critique_target")
    assert preflight_result.exit_code == 0, preflight_result.output
    preflight_data = _json.loads(preflight_result.output)
    preflight_wo = preflight_data.get("warnings_out") or []
    preflight_markers = [m for m in preflight_wo if m.startswith("preflight_auto_log_failed:")]
    assert preflight_markers, f"cmd_preflight must surface ``preflight_auto_log_failed:`` markers; got {preflight_wo!r}"
    for foreign_prefix in ("impact_", "diagnose_", "critique_"):
        leaked = [m for m in preflight_wo if m.startswith(foreign_prefix)]
        assert not leaked, f"cmd_preflight warnings_out must not contain {foreign_prefix}* markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (11) Canonical risk-LEVEL emission -- top-level + summary mirror
# ---------------------------------------------------------------------------


def test_canonical_risk_level_emitted_on_success_path(cli_runner, critique_project):
    """Success path emits ``risk_level_canonical`` + ``risk_rank`` on
    BOTH top-level envelope AND summary.

    Mirror of cmd_impact's W641-followup-A + cmd_diagnose's W607-BH
    canonical-emit pattern. Cross-command consumers can call
    ``risk_rank(data["summary"]["risk_level_canonical"]) >= 3`` to
    gate on high-or-worse without re-deriving the threshold table at
    the call site (Pattern-3a).
    """
    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
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


def test_w607bl_serialize_envelope_floor_on_raise(cli_runner, critique_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``critique_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("critique", ...)`` would otherwise crash AFTER
    all substrate + aggregation signals were already gathered. The
    consumer must still receive a parseable JSON object with the
    marker attached + the canonical command name.
    """
    from roam.commands import cmd_critique

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-BL")

    monkeypatch.setattr(cmd_critique, "json_envelope", _raise_envelope)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "critique", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("critique_serialize_envelope_failed:")]
    assert markers, f"expected ``critique_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (13) Compute-verdict guard -- raise floors to a stable verdict
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, critique_project, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    Simulated by patching ``normalize_risk_level`` so the inner verdict
    builder ALSO works on a degraded path BUT the format-spec on the
    risk_level_canonical string still works. To force the
    compute_verdict closure to raise, we patch ``normalize_risk_level``
    to return a value with a broken ``__format__``. The compute_verdict
    f-string uses ``{risk_level_canonical}`` (str interpolation, no
    format spec) so we patch the f-string subject by replacing
    ``risk_level_canonical`` via a sentinel that raises on __str__.

    Cleanest approach: patch the inner builder by patching
    ``result["verdict"]`` indirectly via patching ``aggregate`` to
    return a dict whose ``verdict`` raises on string interpolation.
    """
    from roam.commands import cmd_critique

    class _BadVerdict:
        def __str__(self):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BL")

        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BL")

    _original_aggregate = cmd_critique.aggregate

    def _bad_aggregate(*args, **kwargs):
        res = _original_aggregate(*args, **kwargs)
        res["verdict"] = _BadVerdict()
        return res

    monkeypatch.setattr(cmd_critique, "aggregate", _bad_aggregate)

    result = _invoke_critique(cli_runner, critique_project, stdin=_DIFF_TEXT)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("critique_compute_verdict_failed:")]
    assert markers, f"expected ``critique_compute_verdict_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
