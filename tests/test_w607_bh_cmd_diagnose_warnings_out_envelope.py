"""W607-BH -- additive aggregation-phase plumbing for ``cmd_diagnose``.

cmd_diagnose is the ROOT-CAUSE COMPANION to cmd_preflight (the agent-OS
pre-edit safety gate per CLAUDE.md LAW 1) and cmd_impact (the
blast-radius companion). Together the three commands form the
**agent-OS pre-edit triangle**: an agent reading top-to-bottom on the
canonical workflow runs ``roam preflight <sym>`` (W607-R + W607-AW),
``roam impact <sym>`` (W607-T + W607-BB), and ``roam diagnose <sym>``
(W607-S + W607-BH) before editing. A silent crash inside an
aggregation-phase boundary in cmd_diagnose would either (a) propagate
up through compound recipes (for_bug_fix, diagnose_issue) and defeat
the change-safety gate, or (b) crash the standalone root-cause
envelope after all six substrate signals were already gathered.

Relation to W607-S
------------------

cmd_diagnose already carries W607-S substrate-CALL plumbing covering
the substrate-helper boundaries: resolve_symbol / build_graph /
target_metrics / dist_stats / ranked_upstream / ranked_downstream /
cochange_partners / recent_commits / next_steps / index_status.
W607-BH is ADDITIVE on top of W607-S, extending marker coverage to
the AGGREGATION-PHASE boundaries that W607-S left unguarded:

  - ``verdict_synthesis``    -- top-suspect verdict text build
  - ``severity_normalize``   -- canonical W631 risk-LEVEL projection +
                                integer rank cluster (NEW field axis
                                on cmd_diagnose; mirror of cmd_impact's
                                W641-followup-A emit pattern)
  - ``auto_log``             -- active-run ledger write (NEW boundary
                                on cmd_diagnose; agent-OS pre-edit
                                triangle parity with cmd_preflight +
                                cmd_impact)
  - ``serialize_envelope``   -- ``json_envelope("diagnose", ...)``
                                projection

Both layers share the canonical ``diagnose_*`` marker family and the
``diagnose_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two buckets (``_w607s_warnings_out`` + ``_w607bh_warnings_out``) are
combined at envelope-emit time so consumers see the full degradation
lineage in marker-emission order.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_diagnose's aggregation-phase boundaries (verdict_synthesis /
severity_normalize / auto_log / serialize_envelope) had no guards. A
downstream refactor that changes the ranked-suspect row schema, the
``_diagnose_risk_level`` signature, the ``normalize_risk_level``
return contract, the HMAC chain on the runs ledger, or the
``json_envelope`` shape would crash the envelope post-compute -- after
the substrate signals were successfully gathered, the agent loses the
result. W607-BH wraps each boundary with ``_run_check_bh`` so a raise
becomes a marker via ``warnings_out`` and the envelope still emits.

Severity-normalize degradation discipline
-----------------------------------------

When ``_diagnose_risk_level`` raises, the wrap floors the classified
tier to ``None`` and surfaces ``severity_classification: "unknown"``
in the envelope summary alongside the canonical W631 ``"low"`` floor
on ``risk_level_canonical``. The
``test_severity_normalize_degradation_surfaces_unknown_sentinel``
guard asserts the sentinel appears AND the raw upstream/downstream
suspect lists still emit (don't crash).

AGENT-OS PRE-EDIT TRIANGLE pairing bonus
----------------------------------------

cmd_preflight delegates to substrate cmd_impact and cmd_diagnose
share. The W607-AW additive layer on cmd_preflight uses
``preflight_*`` markers; the W607-BB layer on cmd_impact uses
``impact_*`` markers; the W607-BH layer on cmd_diagnose uses
``diagnose_*`` markers. The three families are distinct per the
marker-prefix discipline test, but they coexist when all three
commands run on the same target. The
``test_pre_edit_triangle_marker_families_coexist`` integration test
confirms the three families do NOT collide.

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
# Helpers -- invoke diagnose / impact / preflight via the Click group
# ---------------------------------------------------------------------------


def _invoke_diagnose(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam diagnose`` through the group so ``--json`` is honoured."""
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


def _invoke_impact(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam impact`` for the agent-OS triangle integration test."""
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


def _invoke_preflight(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam preflight`` for the agent-OS triangle integration test."""
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


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with a resolvable symbol + real call edges
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def diagnose_project(tmp_path, monkeypatch):
    """Indexed corpus with a unique resolvable symbol (``diagnose_target``).

    Two-file fixture with a real ``main_entry -> diagnose_target ->
    helper_one/helper_two`` chain so upstream/downstream BFS / risk-score
    ranking / cochange / recent-commits all have signal to chew on. The
    target name is intentionally unique to avoid LIKE-fallback false-
    positives in the resolver, and mirrors the W607-S + W607-BB fixture
    shape so the three test files stay byte-comparable.
    """
    proj = tmp_path / "diagnose_w607bh_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main_entry():\n    return diagnose_target()\n\n"
        "def diagnose_target():\n    return helper_one() + helper_two()\n\n"
        "def helper_one():\n    return 1\n\n"
        "def helper_two():\n    return 2\n",
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


def _patch_helper(monkeypatch, attr_name: str, exc):
    """Patch ``cmd_diagnose.<attr_name>`` to raise ``exc`` unconditionally."""
    from roam.commands import cmd_diagnose

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cmd_diagnose, attr_name, _raise)


# ---------------------------------------------------------------------------
# (1) Happy path -- clean diagnose -> envelope omits W607-BH markers
# ---------------------------------------------------------------------------


def test_diagnose_happy_path_no_w607bh_markers(cli_runner, diagnose_project):
    """Clean diagnose on a healthy corpus -> no W607-BH markers.

    Hash-stable: an empty W607-BH bucket on the success path must
    produce an envelope without any
    ``diagnose_verdict_synthesis_failed:`` /
    ``diagnose_severity_normalize_failed:`` /
    ``diagnose_auto_log_failed:`` /
    ``diagnose_serialize_envelope_failed:`` markers. Mirrors W607-BB
    contract.
    """
    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "diagnose"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607bh_phases = (
        "diagnose_verdict_synthesis_failed:",
        "diagnose_severity_normalize_failed:",
        "diagnose_auto_log_failed:",
        "diagnose_serialize_envelope_failed:",
    )
    for prefix in w607bh_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean diagnose must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_bh`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_diagnose_carries_w607bh_accumulator():
    """AST-level guard: cmd_diagnose source carries the W607-BH accumulator.

    Pins the canonical W607-BH anchors so a future refactor that
    removes the additive instrumentation (or merges it back into
    W607-S) fails this guard rather than silently regressing the
    aggregation-phase marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diagnose.py"
    assert src_path.exists(), f"cmd_diagnose.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607bh_warnings_out" in src, (
        "W607-BH accumulator missing from cmd_diagnose; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_bh" in src, (
        "W607-BH helper ``_run_check_bh`` missing from cmd_diagnose; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_bh is defined inside diagnose().
    tree = ast.parse(src)
    found_run_check_bh = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bh":
            found_run_check_bh = True
            break
    assert found_run_check_bh, (
        "W607-BH ``_run_check_bh`` helper not found in cmd_diagnose AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-S must still be present (additive layer does NOT replace it)
    assert "_w607s_warnings_out" in src, (
        "W607-S accumulator vanished alongside the W607-BH add; the "
        "additive plumbing must preserve the W607-S substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_bh():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_bh(...)`` with the canonical phase name.

    The four phases must appear inside a ``_run_check_bh("<phase>", ...)``
    call inside cmd_diagnose. Multi-indent variants (8, 12, 16, 20, 24
    spaces) are all considered valid wrap call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diagnose.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "verdict_synthesis",
        "severity_normalize",
        "auto_log",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_bh(\n        "{phase}"',
            f'_run_check_bh(\n            "{phase}"',
            f'_run_check_bh(\n                "{phase}"',
            f'_run_check_bh(\n                    "{phase}"',
            f'_run_check_bh(\n                        "{phase}"',
            f'_run_check_bh("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_bh(...); add the W607-BH guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) Marker shape -- ``diagnose_<phase>_failed:<exc>:<detail>``
# ---------------------------------------------------------------------------


def test_auto_log_failure_marker_format(cli_runner, diagnose_project, monkeypatch):
    """If ``auto_log`` raises, surface ``diagnose_auto_log_failed:`` and
    keep the diagnose envelope intact.

    Discipline mirror of the W607-BB auto_log-failure pattern in
    cmd_impact. The auto_log boundary writes to the active run ledger
    when one is open -- a raise here would otherwise crash the
    envelope AFTER the success envelope was already built.
    """
    from roam.commands import cmd_diagnose

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-auto-log-from-W607-BH")

    monkeypatch.setattr(cmd_diagnose, "auto_log", _raise_auto_log)

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diagnose_auto_log_failed:")]
    assert markers, f"expected ``diagnose_auto_log_failed:`` marker; got {top_wo!r}"
    marker = markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-auto-log-from-W607-BH" in parts[2], parts

    # Envelope still emits the core root-cause fields
    for key in ("upstream", "downstream", "target_metrics"):
        assert key in data, (
            f"envelope must still emit ``{key}`` when auto_log raises; got keys = {sorted(data.keys())!r}"
        )


def test_verdict_synthesis_failure_marker_format(cli_runner, diagnose_project, monkeypatch):
    """If the verdict_synthesis boundary raises, surface the marker.

    Simulated by patching ``_diagnose_risk_level`` -- no, that's the
    severity_normalize boundary. For verdict_synthesis we need to
    force the inner closure (build_verdict) to raise. The simplest
    way: corrupt the ranked-suspect rows after they're built but
    before _build_verdict reads them by patching the
    ``_diagnose_risk_level`` AFTER verdict_synthesis. Actually the
    cleanest approach: patch a helper inside the verdict text-build
    path. We do not have one -- the closure reads dict keys directly.
    So we approach via patching ``_risk_score`` to return a non-format-
    able object that breaks ``{:.2f}`` formatting.
    """
    from roam.commands import cmd_diagnose

    class _UnformattableRisk:
        """Object that breaks the ``{:.2f}`` format spec inside
        _build_verdict's f-string."""

        def __format__(self, spec):
            raise RuntimeError("synthetic-verdict-from-W607-BH")

    _original_risk_score = cmd_diagnose._risk_score

    def _unformattable_risk(*args, **kwargs):
        # Return a regular score but with an unformattable risk-score
        # value: we cannot just hand back a non-float as the dict
        # value because _build_ranked sorts by ``-x["risk_score"]``;
        # instead we wrap the returned float to break only during
        # format.
        score = _original_risk_score(*args, **kwargs)

        # Wrap so __format__ raises but ordering still works via
        # __lt__ on the float comparison in sort key -- we override
        # __format__ ONLY (sort uses ``-score`` which uses __neg__).
        # Easier: make the score a float subclass.
        class _BadFloat(float):
            def __format__(self, spec):
                raise RuntimeError("synthetic-verdict-from-W607-BH")

        return _BadFloat(score)

    monkeypatch.setattr(cmd_diagnose, "_risk_score", _unformattable_risk)

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diagnose_verdict_synthesis_failed:")]
    assert markers, f"expected ``diagnose_verdict_synthesis_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) SEVERITY NORMALIZE DEGRADATION discipline
# ---------------------------------------------------------------------------


def test_severity_normalize_degradation_surfaces_unknown_sentinel(cli_runner, diagnose_project, monkeypatch):
    """When ``_diagnose_risk_level`` raises:

    1. Marker ``diagnose_severity_normalize_failed:`` appears
    2. Envelope still emits the upstream/downstream suspect lists
    3. Summary stamps ``severity_classification: "unknown"`` sentinel
    4. Summary still carries the canonical floor ``risk_level_canonical: "low"``

    The underlying action (emit the root-cause ranking envelope) stays
    -- degraded outcomes are valid design. The LIE we prevent is a
    clean classified verdict when severity_normalize actually raised.
    Mirror of cmd_impact's risk_classify degradation pattern.
    """
    _patch_helper(
        monkeypatch,
        "_diagnose_risk_level",
        RuntimeError("synthetic-severity-normalize-from-W607-BH"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # (1) marker appears
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diagnose_severity_normalize_failed:")]
    assert markers, f"expected ``diagnose_severity_normalize_failed:`` marker; got {top_wo!r}"

    # (2) envelope still emits the root-cause lists
    summary = data["summary"]
    assert "upstream" in data, (
        f"envelope must still emit ``upstream`` even when severity_normalize raises; got keys = {sorted(data.keys())!r}"
    )
    assert "downstream" in data, (
        f"envelope must still emit ``downstream`` even when "
        f"severity_normalize raises; got keys = {sorted(data.keys())!r}"
    )
    assert "target_metrics" in data, (
        f"envelope must still emit ``target_metrics`` even when "
        f"severity_normalize raises; got keys = {sorted(data.keys())!r}"
    )

    # (3) severity_classification sentinel
    assert summary.get("severity_classification") == "unknown", (
        f'summary must stamp ``severity_classification: "unknown"`` when '
        f"severity_normalize raises; got "
        f"{summary.get('severity_classification')!r}"
    )

    # (4) canonical floor still emitted
    assert summary.get("risk_level_canonical") == "low", (
        f'summary must floor ``risk_level_canonical`` to ``"low"`` on '
        f"severity_normalize raise; got "
        f"{summary.get('risk_level_canonical')!r}"
    )


def test_severity_normalize_clean_path_stamps_classified(cli_runner, diagnose_project):
    """Happy path: ``severity_classification`` summary field is ``"classified"``.

    Mirrors the W607-BH discipline that the sentinel disambiguates a
    real classified verdict from a degraded "unknown" floor. Mirror
    of cmd_impact's W607-BB ``risk_classification: "classified"`` test.
    """
    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("severity_classification") == "classified", (
        f'clean path must stamp ``severity_classification: "classified"``; '
        f"got {data['summary'].get('severity_classification')!r}"
    )


# ---------------------------------------------------------------------------
# (6) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, diagnose_project, monkeypatch):
    """ANY W607-BH or W607-S marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    diagnose" from "diagnose ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_diagnose

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-BH")

    monkeypatch.setattr(cmd_diagnose, "auto_log", _raise_auto_log)

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-BH warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607bh_warnings_out_in_both_top_and_summary(cli_runner, diagnose_project, monkeypatch):
    """Non-empty W607-BH bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-S contract: top-level is needed because
    the preserved-list field survives ``strip_list_payloads`` in
    default-detail mode; summary mirror gives consumers reading only
    the summary block visibility too.
    """
    from roam.commands import cmd_diagnose

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BH")

    monkeypatch.setattr(cmd_diagnose, "auto_log", _raise_auto_log)

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BH raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BH raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("diagnose_auto_log_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("diagnose_auto_log_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the auto_log marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-S COEXISTENCE -- both buckets surface in combined envelope
# ---------------------------------------------------------------------------


def test_combined_w607s_and_w607bh_markers_both_surface(cli_runner, diagnose_project, monkeypatch):
    """W607-S and W607-BH markers BOTH surface when raises occur on
    each layer simultaneously.

    The additive plumbing must not shadow the W607-S bucket -- agents
    must see the full degradation lineage in marker-emission order.
    Mirror of cmd_impact's W607-T + W607-BB combined test.
    """
    from roam.commands import cmd_diagnose

    def _raise_cochange(*a, **kw):
        # W607-S substrate boundary
        raise RuntimeError("synthetic-cochange-from-W607-BH-combined")

    def _raise_auto_log(*a, **kw):
        # W607-BH aggregation boundary
        raise RuntimeError("synthetic-auto-log-from-W607-BH-combined")

    monkeypatch.setattr(cmd_diagnose, "_cochange_partners", _raise_cochange)
    monkeypatch.setattr(cmd_diagnose, "auto_log", _raise_auto_log)

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    s_markers = [m for m in top_wo if m.startswith("diagnose_cochange_partners_failed:")]
    bh_markers = [m for m in top_wo if m.startswith("diagnose_auto_log_failed:")]
    assert s_markers, f"W607-S cochange_partners marker missing; got {top_wo!r}"
    assert bh_markers, f"W607-BH auto_log marker missing; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-BH uses the SAME ``diagnose_*`` family
# ---------------------------------------------------------------------------


def test_w607bh_marker_prefix_diagnose_family(cli_runner, diagnose_project, monkeypatch):
    """W607-BH markers use the canonical ``diagnose_*`` prefix
    (same family as W607-S; W607-BH is ADDITIVE, not a separate prefix).

    Hard guard: any W607-BH marker that leaks into a sibling W607-*
    family (e.g. ``preflight_*`` / ``impact_*`` / ``audit_*``) breaks
    the closed-enum marker-family contract pinned in the W607-S test.
    """
    from roam.commands import cmd_diagnose

    def _raise_auto_log(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BH")

    monkeypatch.setattr(cmd_diagnose, "auto_log", _raise_auto_log)

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-discipline check"
    for marker in top_wo:
        assert marker.startswith("diagnose_"), (
            f"every W607-BH marker must use the ``diagnose_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (10) AGENT-OS PRE-EDIT TRIANGLE pairing -- three marker families coexist
# ---------------------------------------------------------------------------


def test_pre_edit_triangle_marker_families_coexist(cli_runner, diagnose_project, monkeypatch):
    """cmd_preflight (W607-AW + W607-R), cmd_impact (W607-BB + W607-T),
    and cmd_diagnose (W607-BH + W607-S) use distinct marker families
    that coexist when all three run on the same target.

    Integration: simulate a degraded auto_log in ALL THREE commands
    (the same ``auto_log`` import resolves to different module
    attributes; monkeypatching the cmd_diagnose module's ``auto_log``
    does NOT affect cmd_impact's or cmd_preflight's because each
    command has its own ``from roam.runs.helpers import auto_log``
    import). Run all three commands and assert each surfaces its own
    marker family with no cross-prefix leakage.

    This is the AGENT-OS PRE-EDIT TRIANGLE closure milestone: with
    W607-BH landed, all three pre-edit commands are W607-plumbed end-
    to-end on both the substrate-CALL layer (W607-R + W607-T +
    W607-S) AND the aggregation-phase layer (W607-AW + W607-BB +
    W607-BH).
    """
    from roam.commands import cmd_diagnose, cmd_impact, cmd_preflight

    def _raise_diagnose_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-diagnose-auto-log-triangle")

    def _raise_impact_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-impact-auto-log-triangle")

    def _raise_preflight_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-preflight-auto-log-triangle")

    # Patch each command's own module-attr binding.
    monkeypatch.setattr(cmd_diagnose, "auto_log", _raise_diagnose_auto_log)
    monkeypatch.setattr(cmd_impact, "auto_log", _raise_impact_auto_log)
    monkeypatch.setattr(cmd_preflight, "auto_log", _raise_preflight_auto_log)

    # Run diagnose -> expect ``diagnose_*`` markers.
    diagnose_result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert diagnose_result.exit_code == 0, diagnose_result.output
    diagnose_data = _json.loads(diagnose_result.output)
    diagnose_wo = diagnose_data.get("warnings_out") or []
    diagnose_markers = [m for m in diagnose_wo if m.startswith("diagnose_auto_log_failed:")]
    assert diagnose_markers, f"cmd_diagnose must surface ``diagnose_auto_log_failed:`` markers; got {diagnose_wo!r}"
    # No cross-family leakage from diagnose into impact / preflight prefixes.
    leaked_impact_into_diagnose = [m for m in diagnose_wo if m.startswith("impact_")]
    leaked_preflight_into_diagnose = [m for m in diagnose_wo if m.startswith("preflight_")]
    assert not leaked_impact_into_diagnose, (
        f"cmd_diagnose warnings_out must not contain impact_* markers; got {leaked_impact_into_diagnose!r}"
    )
    assert not leaked_preflight_into_diagnose, (
        f"cmd_diagnose warnings_out must not contain preflight_* markers; got {leaked_preflight_into_diagnose!r}"
    )

    # Run impact -> expect ``impact_*`` markers.
    impact_result = _invoke_impact(cli_runner, diagnose_project, "diagnose_target")
    assert impact_result.exit_code == 0, impact_result.output
    impact_data = _json.loads(impact_result.output)
    impact_wo = impact_data.get("warnings_out") or []
    impact_markers = [m for m in impact_wo if m.startswith("impact_auto_log_failed:")]
    assert impact_markers, f"cmd_impact must surface ``impact_auto_log_failed:`` markers; got {impact_wo!r}"
    # No cross-family leakage from impact into diagnose / preflight prefixes.
    leaked_diagnose_into_impact = [m for m in impact_wo if m.startswith("diagnose_")]
    leaked_preflight_into_impact = [m for m in impact_wo if m.startswith("preflight_")]
    assert not leaked_diagnose_into_impact, (
        f"cmd_impact warnings_out must not contain diagnose_* markers; got {leaked_diagnose_into_impact!r}"
    )
    assert not leaked_preflight_into_impact, (
        f"cmd_impact warnings_out must not contain preflight_* markers; got {leaked_preflight_into_impact!r}"
    )

    # Run preflight -> expect ``preflight_*`` markers.
    preflight_result = _invoke_preflight(cli_runner, diagnose_project, "diagnose_target")
    assert preflight_result.exit_code == 0, preflight_result.output
    preflight_data = _json.loads(preflight_result.output)
    preflight_wo = preflight_data.get("warnings_out") or []
    preflight_markers = [m for m in preflight_wo if m.startswith("preflight_auto_log_failed:")]
    assert preflight_markers, f"cmd_preflight must surface ``preflight_auto_log_failed:`` markers; got {preflight_wo!r}"
    # No cross-family leakage from preflight into diagnose / impact prefixes.
    leaked_diagnose_into_preflight = [m for m in preflight_wo if m.startswith("diagnose_")]
    leaked_impact_into_preflight = [m for m in preflight_wo if m.startswith("impact_")]
    assert not leaked_diagnose_into_preflight, (
        f"cmd_preflight warnings_out must not contain diagnose_* markers; got {leaked_diagnose_into_preflight!r}"
    )
    assert not leaked_impact_into_preflight, (
        f"cmd_preflight warnings_out must not contain impact_* markers; got {leaked_impact_into_preflight!r}"
    )


# ---------------------------------------------------------------------------
# (11) Canonical risk-LEVEL emission -- top-level + summary mirror
# ---------------------------------------------------------------------------


def test_canonical_risk_level_emitted_on_success_path(cli_runner, diagnose_project):
    """Success path emits ``risk_level_canonical`` + ``risk_rank`` on
    BOTH top-level envelope AND summary.

    Mirror of cmd_impact's W641-followup-A + cmd_pr_risk's W641 emit
    pattern. Cross-command consumers can call
    ``risk_rank(data["summary"]["risk_level_canonical"]) >= 3`` to
    gate on high-or-worse without re-deriving the threshold table at
    the call site (Pattern-3a).
    """
    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
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


def test_w607bh_serialize_envelope_floor_on_raise(cli_runner, diagnose_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``diagnose_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("diagnose", ...)`` would otherwise crash AFTER
    all substrate + aggregation signals were already gathered. The
    consumer must still receive a parseable JSON object with the
    marker attached + the canonical command name.
    """
    from roam.commands import cmd_diagnose

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-BH")

    monkeypatch.setattr(cmd_diagnose, "json_envelope", _raise_envelope)

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "diagnose", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diagnose_serialize_envelope_failed:")]
    assert markers, f"expected ``diagnose_serialize_envelope_failed:`` marker; got {top_wo!r}"
