"""W607-AV -- ``cmd_dogfood`` substrate-boundary plumbing.

Thirty-fifth-in-batch W607 consumer-layer arc. ADDITIVE plumbing: cmd_dogfood
already carries the W607-D ``warnings_out`` channel + outer-guard
``dogfood_aggregation_failed:<exc>:<detail>`` marker (a single try/except
wrapping the aggregation block). W607-AV adds per-phase ``_run_check_av``
substrate-CALL marker plumbing on top, so a raise in ONE phase no longer
aborts the remaining phases -- partial-batch resilience for the
high-traffic dogfood-eval invocation surface.

The marker prefix stays in the ``dogfood_*`` family. Hard distinction
from sibling W607-* layers preserved by the prefix-discipline test
(``vulns_*`` / ``sbom_*`` / ``supply_chain_*`` / ``critique_*`` /
``preflight_*`` etc.).

Phases inventoried for cmd_dogfood (one substrate boundary each):

* git_metadata             -- the git-SHA/branch probe
* audit_subcommand         -- ``roam audit`` dispatch
* pr_analyze_subcommand    -- ``roam pr-analyze`` dispatch
* conformance_subcommand   -- ``roam audit-trail-conformance-check`` dispatch
* compose_summary          -- verdict + sections assembly
* serialize_envelope       -- on-text JSON serialization

EVAL-INVOCATION resilience: a raise in audit_subcommand must NOT prevent
pr_analyze_subcommand and conformance_subcommand from still running. The
per-phase wrap is what gives W607-AV its "partial-batch resilience"
property -- a single broken eval shouldn't sink the rest of the batch.

W978 first-hypothesis check
---------------------------

Each W607-AV-wrapped substrate has a documented empty-floor default that
matches its happy-path return shape so a raise degrades cleanly.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The subcommand
dispatch is patched via ``monkeypatch.setattr(cmd_dogfood, "_run_subcommand", ...)``
on the module-level helper.

Marker prefix discipline
------------------------

Marker family is ``dogfood_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers preserved by the
prefix-discipline test.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def dogfood_project(project_factory):
    """Small indexed corpus -- enough for the v2 stack to compose an envelope."""
    return project_factory(
        {
            "service.py": "def process(): return 1\n",
            "api.py": "from service import process\ndef handle(): return process()\n",
        }
    )


def _invoke_dogfood(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam dogfood`` against a project root via the top-level CLI.

    Using the top-level CLI rather than the click command directly so the
    ``--json`` flag wires into ``ctx.obj`` the same way the production
    invocation does.
    """
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("dogfood")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-AV substrate-CALL markers
# ---------------------------------------------------------------------------


def test_dogfood_clean_envelope_omits_w607av_markers(cli_runner, dogfood_project):
    """Clean dogfood -> no W607-AV substrate markers.

    Byte-identical-on-happy-path: an empty W607-AV bucket on the success
    path must NOT introduce ``dogfood_<phase>_failed:`` markers on the
    envelope. The envelope's `warnings_out` may still be omitted entirely
    on a clean run.
    """
    result = _invoke_dogfood(cli_runner, dogfood_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "dogfood"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    av_markers = [
        m
        for m in (list(top_wo) + list(summary_wo))
        if "_failed:" in m and m.startswith("dogfood_") and "_aggregation_failed" not in m
    ]
    assert not av_markers, (
        f"clean dogfood must NOT surface W607-AV substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) audit_subcommand failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_dogfood_audit_subcommand_failure_marker_format(cli_runner, dogfood_project, monkeypatch):
    """If ``audit`` dispatch raises, surface the W607-AV marker.

    The audit dispatch is one of cmd_dogfood's three subcommand-invocation
    substrate boundaries -- a raise here previously crashed the whole
    dogfood invocation (or got swallowed by the W607-D outer-guard
    wholesale). W607-AV surfaces it as a structured
    ``dogfood_audit_subcommand_failed:<exc>:<detail>`` marker.
    """
    from roam.commands import cmd_dogfood

    original = cmd_dogfood._run_subcommand
    call_log: list[tuple] = []

    def _selective_raise(args):
        call_log.append(tuple(args))
        if len(args) >= 2 and args[1] == "audit":
            raise RuntimeError("synthetic-audit-from-W607-AV")
        return original(args)

    monkeypatch.setattr(cmd_dogfood, "_run_subcommand", _selective_raise)

    result = _invoke_dogfood(cli_runner, dogfood_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("dogfood_audit_subcommand_failed:")]
    assert markers, f"expected dogfood_audit_subcommand_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-audit-from-W607-AV" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"audit-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_dogfood_w607av_warnings_in_envelope(cli_runner, dogfood_project, monkeypatch):
    """Non-empty W607-AV bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_dogfood

    def _raise(args):
        raise RuntimeError("synthetic-mirror-from-W607-AV")

    monkeypatch.setattr(cmd_dogfood, "_run_subcommand", _raise)

    result = _invoke_dogfood(cli_runner, dogfood_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AV disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AV disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("dogfood_") and "_subcommand_failed" in m]
    assert markers, f"expected dogfood_*_subcommand_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, dogfood_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AQ contracts.
    """
    from roam.commands import cmd_dogfood

    def _raise(args):
        raise PermissionError("synthetic-shape-detail-from-W607-AV")

    monkeypatch.setattr(cmd_dogfood, "_run_subcommand", _raise)

    result = _invoke_dogfood(cli_runner, dogfood_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("dogfood_audit_subcommand_failed:")]
    assert failure_markers, f"expected dogfood_audit_subcommand_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "dogfood_audit_subcommand_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) EVAL-INVOCATION partial-batch resilience: one phase raises,
#     the rest still complete
# ---------------------------------------------------------------------------


def test_dogfood_partial_batch_resilience_other_phases_complete(cli_runner, dogfood_project, monkeypatch):
    """Highest-signal bonus for cmd_dogfood: a raise in ONE phase must NOT
    prevent the OTHER phases from running.

    Simulates: ``audit`` dispatch raises, but ``pr-analyze`` still gets
    dispatched. Asserts that:
    1. The ``dogfood_audit_subcommand_failed:`` marker appears.
    2. The ``pr_analyze`` section is still populated (the call_log records it).

    This is the partial-batch resilience property that makes per-phase
    wrapping more valuable than the outer-guard alone -- the outer-guard
    would short-circuit after the first raise, losing all downstream
    sections.
    """
    from roam.commands import cmd_dogfood

    original = cmd_dogfood._run_subcommand
    invocation_log: list[str] = []

    def _selective_raise(args):
        # Track which subcommand was invoked.
        sub = args[1] if len(args) >= 2 else "?"
        invocation_log.append(sub)
        if sub == "audit":
            raise RuntimeError("synthetic-batch-audit-from-W607-AV")
        return original(args)

    monkeypatch.setattr(cmd_dogfood, "_run_subcommand", _selective_raise)

    result = _invoke_dogfood(cli_runner, dogfood_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # 1) audit_subcommand failure marker present
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    audit_markers = [m for m in all_wo if m.startswith("dogfood_audit_subcommand_failed:")]
    assert audit_markers, f"expected dogfood_audit_subcommand_failed: marker; got {all_wo!r}"

    # 2) pr_analyze was still invoked even though audit raised
    assert "pr-analyze" in invocation_log, (
        f"pr-analyze substrate must still be dispatched after audit raised "
        f"(partial-batch resilience); invocation_log={invocation_log!r}"
    )

    # 3) summary partial_success flipped
    assert data["summary"].get("partial_success") is True, (
        f"partial-batch failure must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-AV stays in ``dogfood_*`` family
# ---------------------------------------------------------------------------


def test_w607av_marker_prefix_stays_in_dogfood_family(cli_runner, dogfood_project, monkeypatch):
    """Every W607-AV substrate marker uses the canonical ``dogfood_*`` prefix.

    cmd_dogfood is the v2-stack aggregator -- distinct from sibling W607-*
    layers. Marker prefix MUST stay ``dogfood_*`` and MUST NOT leak into
    other family prefixes (``vulns_*``, ``sbom_*``, ``supply_chain_*``,
    ``preflight_*``, etc.).
    """
    from roam.commands import cmd_dogfood

    def _raise(args):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AV")

    monkeypatch.setattr(cmd_dogfood, "_run_subcommand", _raise)

    result = _invoke_dogfood(cli_runner, dogfood_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("dogfood_"), (
            f"every surfaced W607-AV marker must use the ``dogfood_*`` "
            f"prefix family (cmd_dogfood scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("vulns_", "cmd_vulns W607-AQ"),
            ("sbom_", "cmd_sbom W607-AM"),
            ("supply_chain_", "cmd_supply_chain W607-AK"),
            ("cga_", "cmd_cga W607-AF"),
            ("attest_", "cmd_attest W607-AD"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("relate_", "cmd_relate W607-W"),
            ("deps_", "cmd_deps W607-V"),
            ("uses_", "cmd_uses W607-U"),
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("audit_trail_", "cmd_audit_trail W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("retrieve_", "cmd_retrieve W607-B"),
            ("findings_", "cmd_findings W607-C"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_dogfood carries the W607-AV accumulator
# ---------------------------------------------------------------------------


def test_cmd_dogfood_carries_w607av_accumulator():
    """AST-level guard: cmd_dogfood source carries the W607-AV accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-AV instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dogfood.py"
    assert src_path.exists(), f"cmd_dogfood.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607av_warnings_out" in src, (
        "W607-AV accumulator missing from cmd_dogfood; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_av" in src, (
        "W607-AV ``_run_check_av`` helper missing from cmd_dogfood; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_av is defined inside cmd_dogfood.
    tree = ast.parse(src)
    found_run_check_av = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_av":
            found_run_check_av = True
            break
    assert found_run_check_av, (
        "W607-AV ``_run_check_av`` helper not found in cmd_dogfood AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-AV substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607av_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-AV substrate boundary is wrapped.

    W607-AV substrate inventory (cmd_dogfood -- single command):

    * audit_subcommand         -- ``roam audit`` dispatch
    * pr_analyze_subcommand    -- ``roam pr-analyze`` dispatch
    * conformance_subcommand   -- ``roam audit-trail-conformance-check`` dispatch
    * compose_summary          -- verdict + sections assembly
    * serialize_envelope       -- the on-text JSON serialization boundary

    NOTE: ``git_metadata`` is intentionally NOT wrapped in ``_run_check_av``
    -- it is owned by the W607-D outer-guard's
    ``dogfood_aggregation_failed:`` marker family for parity with
    cmd_findings W607-C / cmd_retrieve W607-B outer-guard idioms.

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dogfood.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "audit_subcommand",
        "pr_analyze_subcommand",
        "conformance_subcommand",
        "compose_summary",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_av("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_av(\n        "{phase}"' in src
            or f'_run_check_av(\n            "{phase}"' in src
            or f'_run_check_av(\n                "{phase}"' in src
            or f'_run_check_av(\n                    "{phase}"' in src
            or f'_run_check_av(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AV _run_check_av wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) W607-D outer-guard coexists with W607-AV per-phase plumbing
# ---------------------------------------------------------------------------


def test_w607d_and_w607av_coexist_in_cmd_dogfood():
    """cmd_dogfood carries BOTH the W607-D outer-guard AND the W607-AV
    per-phase plumbing.

    W607-AV is an ADDITIVE extension to W607-D's pre-existing outer-guard.
    Both must remain in place: W607-D catches aggregation-block-wholesale
    raises (e.g. an exception thrown by the try-block scaffolding itself
    that's not inside a per-phase wrap), and W607-AV catches per-substrate
    raises with partial-batch resilience.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dogfood.py"
    src = src_path.read_text(encoding="utf-8")
    # W607-D outer-guard marker family
    assert "dogfood_aggregation_failed" in src, (
        "W607-D outer-guard ``dogfood_aggregation_failed`` marker family "
        "missing from cmd_dogfood; the W607-D plumbing has been removed."
    )
    # W607-AV per-phase marker family (via _run_check_av which emits
    # ``dogfood_<phase>_failed``)
    assert "_run_check_av" in src, (
        "W607-AV ``_run_check_av`` per-phase helper missing from cmd_dogfood; the W607-AV plumbing has been removed."
    )
    # Both bucket names must coexist
    assert "warnings_out" in src and "w607av_warnings_out" in src, (
        "cmd_dogfood must carry BOTH the W607-D ``warnings_out`` bucket "
        "AND the W607-AV ``_w607av_warnings_out`` per-phase bucket; one "
        "of the two has been removed."
    )
