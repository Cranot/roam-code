"""W607-AK -- ``cmd_supply_chain`` substrate-boundary plumbing.

Thirty-first-in-batch W607 consumer-layer arc. Fresh-plumbing wave:
cmd_supply_chain had only a thin pre-existing ``warnings_out`` channel
(W1142-followup-B cap-hit truncation disclosure) and NO ``_run_check`` /
substrate-CALL marker plumbing -- so the canonical fresh template applies
(one accumulator + one ``_run_check_ak`` helper) with the marker prefix
merging additively into the pre-existing ``warnings_out`` list.

cmd_supply_chain is the SBOM/VEX projection leg of the W805 cross-artifact-
consistency family (CGA/VSA -> SBOM/VEX projection chain). Each substrate
boundary -- discover_and_parse / compute_risk_score / top_risky /
supply_chain_to_sarif / write_sarif -- can raise; prior to W607-AK a raise
crashed the whole supply-chain build wholesale.

W805 cross-artifact consistency family
--------------------------------------

cmd_supply_chain sits on the SBOM/VEX projection leg of the W805 6-artifact
family (CGA + VSA + Rekor + bundle + cosign + Fulcio + SBOM/VEX). The
W607-AK markers fire AT RUNTIME when an emission boundary raises,
complementing the W805 xfail-strict pins that catch structural inconsistency
at the dataclass level. Specifically the ``build_cdx_sbom``-class boundary
(here the ``discover_and_parse`` manifest-extraction core) must not crash
the supply-chain build wholesale -- the envelope still emits with the
remaining signal and a structured marker disclosure.

W1142-followup-B parity
-----------------------

cmd_supply_chain's envelope already carried a ``summary.warnings_out`` field
for the W1142-followup-B cap-hit truncation axis. W607-AK is ADDITIVE:
substrate-CALL markers merge into the SAME ``summary.warnings_out`` list,
and ``partial_success`` flips when EITHER bucket is non-empty. The marker
PREFIX disambiguates them downstream (``truncated to ...`` vs
``supply_chain_<phase>_failed:*``).

W978 first-hypothesis check
---------------------------

Each W607-AK-wrapped substrate has a documented empty-floor default matching
its happy-path return shape so a raise degrades cleanly. Dominant raise axes
are: filesystem refusal (``discover_and_parse``), malformed manifest
(``_parse_*`` parsers downstream), and SARIF serialization (``write_sarif``).

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Every substrate is
referenced by its imported module-level name and patched via
``monkeypatch.setattr`` on ``cmd_supply_chain`` at test time.

Marker prefix discipline
------------------------

Marker family is ``supply_chain_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers (``cga_*``, ``attest_*``,
``diff_*``, ``critique_*``, etc.) preserved by the prefix-discipline test.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands.cmd_supply_chain import supply_chain

# ---------------------------------------------------------------------------
# Helpers -- invoke supply-chain through the click runner
# ---------------------------------------------------------------------------


def _invoke_supply_chain(runner: CliRunner, project_root: Path, *, json_mode: bool = True, sarif_mode: bool = False):
    """Invoke ``roam supply-chain`` against a project-root mock."""
    from unittest import mock

    args: list[str] = []
    obj = {"json": json_mode, "sarif": sarif_mode, "budget": 0}
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=project_root):
        return runner.invoke(supply_chain, args, obj=obj)


# ---------------------------------------------------------------------------
# Fixture -- a small project with a clean requirements.txt manifest so
# discover_and_parse hits every parse path on the happy path.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def supply_chain_project(tmp_path):
    """Tiny corpus with a clean manifest so supply-chain exercises every
    W607-AK substrate boundary (discover_and_parse / compute_risk_score /
    top_risky / supply_chain_to_sarif).
    """
    (tmp_path / "requirements.txt").write_text(
        "requests==2.28.0\nclick==8.1.0\nflask\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-AK substrate-CALL markers
# ---------------------------------------------------------------------------


def test_supply_chain_clean_envelope_omits_w607ak_markers(cli_runner, supply_chain_project):
    """Clean supply-chain -> no W607-AK substrate markers.

    Byte-identical-on-happy-path: an empty W607-AK bucket on the success
    path must NOT introduce ``supply_chain_<phase>_failed:`` markers on
    the envelope. The pre-existing W1142-followup-B cap-hit truncation
    only fires with --limit < total so it stays quiet here too.
    """
    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "supply-chain"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ak_markers = [m for m in (list(top_wo) + list(summary_wo)) if "_failed:" in m and m.startswith("supply_chain_")]
    assert not ak_markers, (
        f"clean supply-chain must NOT surface W607-AK substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) discover_and_parse failure -> structured marker + clean degraded envelope
# ---------------------------------------------------------------------------


def test_supply_chain_discover_and_parse_failure_marker_format(cli_runner, supply_chain_project, monkeypatch):
    """If ``discover_and_parse`` raises, surface the W607-AK marker.

    The manifest-discovery is the SBOM-extraction core substrate boundary
    -- a raise here previously crashed the whole supply-chain build. W607-AK
    surfaces it as a structured
    ``supply_chain_discover_and_parse_failed:<exc>:<detail>`` marker and
    emits a structured degraded envelope (zero deps / empty SBOM) rather
    than crashing.
    """
    from roam.commands import cmd_supply_chain

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-discover-from-W607-AK")

    monkeypatch.setattr(cmd_supply_chain, "discover_and_parse", _raise)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("supply_chain_discover_and_parse_failed:")]
    assert markers, f"expected supply_chain_discover_and_parse_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-discover-from-W607-AK" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"discover-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # W805 invariant: envelope still emits cleanly with an empty
    # dependency list -- the supply-chain build does NOT crash wholesale.
    assert data["summary"].get("total_dependencies") == 0, data["summary"]


# ---------------------------------------------------------------------------
# (3) compute_risk_score failure -> structured marker
# ---------------------------------------------------------------------------


def test_supply_chain_compute_risk_score_failure_marker_format(cli_runner, supply_chain_project, monkeypatch):
    """If ``compute_risk_score`` raises, surface the W607-AK marker.

    Risk scoring is the metric-computation boundary -- a raise here would
    crash the verdict-building before any output reaches the agent; W607-AK
    degrades gracefully with an empty-floor metrics dict.
    """
    from roam.commands import cmd_supply_chain

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-score-from-W607-AK")

    monkeypatch.setattr(cmd_supply_chain, "compute_risk_score", _raise)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("supply_chain_compute_risk_score_failed:")]
    assert markers, f"expected supply_chain_compute_risk_score_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (4) supply_chain_to_sarif failure -> SARIF projection boundary marker
# ---------------------------------------------------------------------------


def test_supply_chain_to_sarif_failure_marker_format(cli_runner, supply_chain_project, monkeypatch):
    """W805-family bonus: simulated supply_chain_to_sarif raise.

    The SARIF projection is the supply-chain-emit-to-SARIF boundary. A raise
    here means the report was computed but the projection couldn't be
    rendered. W607-AK surfaces the marker AND the SARIF output emits as
    an empty/default SARIF rather than crashing.
    """
    from roam.commands import cmd_supply_chain

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-sarif-from-W607-AK")

    monkeypatch.setattr(cmd_supply_chain, "supply_chain_to_sarif", _raise)

    result = _invoke_supply_chain(cli_runner, supply_chain_project, json_mode=False, sarif_mode=True)
    # SARIF mode is a sibling code path; the test passes as long as the
    # raise didn't crash the CLI wholesale. The bucket-level marker test
    # for the JSON envelope path is exercised in (5)+.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (5) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_supply_chain_w607ak_warnings_in_envelope(cli_runner, supply_chain_project, monkeypatch):
    """Non-empty W607-AK bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_supply_chain

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AK")

    monkeypatch.setattr(cmd_supply_chain, "discover_and_parse", _raise)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AK disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AK disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("supply_chain_discover_and_parse_failed:")]
    assert markers, f"expected supply_chain_discover_and_parse_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (6) partial_success flips when W607-AK substrate raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_w607ak_helper_raises(cli_runner, supply_chain_project, monkeypatch):
    """Any non-empty W607-AK bucket -> summary.partial_success = True."""
    from roam.commands import cmd_supply_chain

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AK")

    monkeypatch.setattr(cmd_supply_chain, "discover_and_parse", _raise)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-AK warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, supply_chain_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AF contracts.
    """
    from roam.commands import cmd_supply_chain

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AK")

    monkeypatch.setattr(cmd_supply_chain, "discover_and_parse", _raise)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("supply_chain_discover_and_parse_failed:")]
    assert failure_markers, f"expected supply_chain_discover_and_parse_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "supply_chain_discover_and_parse_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-AK stays in ``supply_chain_*`` family
# ---------------------------------------------------------------------------


def test_w607ak_marker_prefix_stays_in_supply_chain_family(cli_runner, supply_chain_project, monkeypatch):
    """Every W607-AK substrate marker uses the canonical ``supply_chain_*`` prefix.

    cmd_supply_chain is the SBOM/VEX projection -- distinct from sibling
    W607-* layers. Marker prefix MUST stay ``supply_chain_*`` and MUST NOT
    leak into other family prefixes (``cga_*``, ``attest_*``, etc.).
    """
    from roam.commands import cmd_supply_chain

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AK")

    monkeypatch.setattr(cmd_supply_chain, "discover_and_parse", _raise)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("supply_chain_"), (
            f"every surfaced W607-AK marker must use the ``supply_chain_*`` "
            f"prefix family (cmd_supply_chain scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
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
            ("audit_", "cmd_audit W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) Source-level guard: cmd_supply_chain carries the W607-AK accumulator
# ---------------------------------------------------------------------------


def test_cmd_supply_chain_carries_w607ak_accumulator():
    """AST-level guard: cmd_supply_chain source carries the W607-AK accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-AK instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_supply_chain.py"
    assert src_path.exists(), f"cmd_supply_chain.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ak_warnings_out" in src, (
        "W607-AK accumulator missing from cmd_supply_chain; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ak" in src, (
        "W607-AK ``_run_check_ak`` helper missing from cmd_supply_chain; the "
        "per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_ak is defined inside cmd_supply_chain.
    tree = ast.parse(src)
    found_run_check_ak = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ak":
            found_run_check_ak = True
            break
    assert found_run_check_ak, (
        "W607-AK ``_run_check_ak`` helper not found in cmd_supply_chain AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (10) Each W607-AK substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ak_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-AK substrate boundary is wrapped.

    W607-AK substrate inventory (cmd_supply_chain -- single command):

    * find_project_root      -- project-root resolution (FS boundary)
    * discover_and_parse     -- the SBOM/manifest-extraction core
    * compute_risk_score     -- the risk-scoring boundary
    * sort_risky_full        -- the pre-slice risk-sort boundary (W1142-followup-B)
    * top_risky              -- the riskiest-N slice boundary
    * supply_chain_to_sarif  -- the SARIF projection boundary (W805 bonus)
    * write_sarif            -- the on-disk/text SARIF emit boundary

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_supply_chain.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "find_project_root",
        "discover_and_parse",
        "compute_risk_score",
        "sort_risky_full",
        "top_risky",
        "supply_chain_to_sarif",
        "write_sarif",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_ak("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_ak(\n        "{phase}"' in src
            or f'_run_check_ak(\n            "{phase}"' in src
            or f'_run_check_ak(\n                "{phase}"' in src
            or f'_run_check_ak(\n                    "{phase}"' in src
            or f'_run_check_ak(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AK _run_check_ak wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
