"""W607-AM -- ``cmd_sbom`` substrate-boundary plumbing.

Thirty-second-in-batch W607 consumer-layer arc. Fresh-plumbing wave:
cmd_sbom had NO pre-existing ``warnings_out`` channel and NO ``_run_check`` /
substrate-CALL marker plumbing -- so the canonical fresh template applies
(one accumulator + one ``_run_check_am`` helper) with the marker prefix
``sbom_*`` outright on a brand-new ``summary.warnings_out`` field.

cmd_sbom is the SBOM EMIT producer leg of the W805 cross-artifact-consistency
family. Sibling of cmd_supply_chain (W607-AK) which is the consumer/projection
side -- cmd_sbom produces the CycloneDX/SPDX artifact downstream consumers
use. Each substrate boundary -- find_project_root / discover_and_parse /
compute_graph_reachability / compute_filesystem_reachability /
merge_reachability / generate_cyclonedx / generate_spdx / build_aibom_block /
serialize_sbom_json / write_sbom_to_disk -- can raise; prior to W607-AM a
raise crashed the whole SBOM emit wholesale.

W805 cross-artifact consistency family
--------------------------------------

cmd_sbom sits on the SBOM EMIT producer leg of the W805 6-artifact family
(CGA + VSA + Rekor + bundle + cosign + Fulcio + SBOM/VEX). The eventual
7th artifact (SBOM with content-hash binding) would naturally extend the
W805 drift-table; W607-AM gives the runtime-raise complement to that
future structural pin.

The W607-AM markers fire AT RUNTIME when an emission boundary raises,
complementing the W805 xfail-strict pins that catch structural inconsistency
at the dataclass level. Specifically the ``generate_cyclonedx`` /
``generate_spdx`` boundaries must not crash the SBOM emit wholesale -- the
envelope still emits with the remaining signal and a structured marker
disclosure (``sbom: null`` artifact slot when the generator raised).

W978 first-hypothesis check
---------------------------

Each W607-AM-wrapped substrate has a documented empty-floor default matching
its happy-path return shape so a raise degrades cleanly. Dominant raise axes
are: filesystem refusal (``discover_and_parse``), missing index
(``compute_graph_reachability`` -- ensure_index() stays outside the wrap so
the index-gate behaviour is preserved), and serialization (``serialize_sbom_json``).

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Every substrate is
referenced by its imported module-level name and patched via
``monkeypatch.setattr`` on ``cmd_sbom`` at test time. The
``compute_filesystem_reachability`` / ``merge_reachability`` symbols
are imported inside the click command body, so they are patched on
the source module (``roam.security.sbom_reachability``) at test time.

Marker prefix discipline
------------------------

Marker family is ``sbom_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers (``supply_chain_*``,
``cga_*``, ``attest_*``, ``diff_*``, ``critique_*``, etc.) preserved
by the prefix-discipline test.

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

from roam.commands.cmd_sbom import sbom

# ---------------------------------------------------------------------------
# Helpers -- invoke sbom through the click runner
# ---------------------------------------------------------------------------


def _invoke_sbom(
    runner: CliRunner,
    project_root: Path,
    *,
    json_mode: bool = True,
    fmt: str = "cyclonedx",
    no_reachability: bool = True,
    output_path: str | None = None,
    aibom: bool = False,
):
    """Invoke ``roam sbom`` against a project-root mock."""
    from unittest import mock

    args: list[str] = ["--format", fmt]
    if no_reachability:
        args.append("--no-reachability")
    if aibom:
        args.append("--aibom")
    if output_path is not None:
        args.extend(["--output", output_path])
    obj = {"json": json_mode, "budget": 0}
    with mock.patch("roam.commands.cmd_sbom.find_project_root", return_value=project_root):
        return runner.invoke(sbom, args, obj=obj)


# ---------------------------------------------------------------------------
# Fixture -- a small project with a clean requirements.txt manifest so
# discover_and_parse hits every parse path on the happy path.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def sbom_project(tmp_path):
    """Tiny corpus with a clean manifest so sbom exercises every
    W607-AM substrate boundary (discover_and_parse / generate_cyclonedx /
    serialize_sbom_json).
    """
    (tmp_path / "requirements.txt").write_text(
        "requests==2.28.0\nclick==8.1.0\nflask\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-AM substrate-CALL markers
# ---------------------------------------------------------------------------


def test_sbom_clean_envelope_omits_w607am_markers(cli_runner, sbom_project):
    """Clean sbom -> no W607-AM substrate markers.

    Byte-identical-on-happy-path: an empty W607-AM bucket on the success
    path must NOT introduce ``sbom_<phase>_failed:`` markers on the
    envelope. cmd_sbom has no pre-existing warnings_out channel, so the
    field is absent entirely on the clean path.
    """
    result = _invoke_sbom(cli_runner, sbom_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "sbom"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    am_markers = [m for m in (list(top_wo) + list(summary_wo)) if "_failed:" in m and m.startswith("sbom_")]
    assert not am_markers, (
        f"clean sbom must NOT surface W607-AM substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )
    # No partial_success flag on clean path either.
    assert data["summary"].get("partial_success") in (None, False), (
        f"clean sbom must NOT flip partial_success; got {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (2) discover_and_parse failure -> structured marker + clean degraded envelope
# ---------------------------------------------------------------------------


def test_sbom_discover_and_parse_failure_marker_format(cli_runner, sbom_project, monkeypatch):
    """If ``discover_and_parse`` raises, surface the W607-AM marker.

    The manifest-discovery is the SBOM-extraction core substrate boundary
    -- a raise here previously crashed the whole SBOM build. W607-AM
    surfaces it as a structured
    ``sbom_discover_and_parse_failed:<exc>:<detail>`` marker and emits
    a structured degraded envelope (zero deps / empty SBOM) rather than
    crashing.
    """

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-discover-from-W607-AM")

    monkeypatch.setattr("roam.commands.cmd_supply_chain.discover_and_parse", _raise)

    result = _invoke_sbom(cli_runner, sbom_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("sbom_discover_and_parse_failed:")]
    assert markers, f"expected sbom_discover_and_parse_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-discover-from-W607-AM" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"discover-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # W805 invariant: envelope still emits cleanly with an empty
    # dependency list -- the SBOM build does NOT crash wholesale.
    assert data["summary"].get("total_dependencies") == 0, data["summary"]
    # And the artifact slot is populated (default-floor SBOM with zero deps).
    assert "sbom" in data, list(data.keys())


# ---------------------------------------------------------------------------
# (3) generate_cyclonedx failure -> structured marker + null SBOM disclosure
# ---------------------------------------------------------------------------


def test_sbom_generate_cyclonedx_failure_marker_format(cli_runner, sbom_project, monkeypatch):
    """W805-family bonus: simulated ``_generate_cyclonedx`` raise.

    The SBOM-emit boundary is the producer-side W805 invariant: a raise
    here means the report data was computed but the SBOM document couldn't
    be rendered. W607-AM surfaces the marker AND the envelope completes
    with ``sbom: null`` disclosure rather than crashing the emit
    wholesale.
    """
    from roam.commands import cmd_sbom

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-cyclonedx-from-W607-AM")

    monkeypatch.setattr(cmd_sbom, "_generate_cyclonedx", _raise)

    result = _invoke_sbom(cli_runner, sbom_project, fmt="cyclonedx")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("sbom_generate_cyclonedx_failed:")]
    assert markers, f"expected sbom_generate_cyclonedx_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    # W805 invariant: envelope completes with sbom: null disclosure when
    # the generator raised. Don't crash the SBOM emit wholesale.
    assert data.get("sbom") is None, (
        f"generate_cyclonedx-failed envelope must disclose sbom=null; got sbom={data.get('sbom')!r}"
    )
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (4) generate_spdx failure -> structured marker
# ---------------------------------------------------------------------------


def test_sbom_generate_spdx_failure_marker_format(cli_runner, sbom_project, monkeypatch):
    """If ``_generate_spdx`` raises, surface the W607-AM marker.

    Sibling boundary to generate_cyclonedx -- the SPDX 2.3 emit path.
    """
    from roam.commands import cmd_sbom

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-spdx-from-W607-AM")

    monkeypatch.setattr(cmd_sbom, "_generate_spdx", _raise)

    result = _invoke_sbom(cli_runner, sbom_project, fmt="spdx")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("sbom_generate_spdx_failed:")]
    assert markers, f"expected sbom_generate_spdx_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_sbom_w607am_warnings_in_envelope(cli_runner, sbom_project, monkeypatch):
    """Non-empty W607-AM bucket -> both top-level AND summary.warnings_out."""

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AM")

    monkeypatch.setattr("roam.commands.cmd_supply_chain.discover_and_parse", _raise)

    result = _invoke_sbom(cli_runner, sbom_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AM disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AM disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("sbom_discover_and_parse_failed:")]
    assert markers, f"expected sbom_discover_and_parse_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (6) partial_success flips when W607-AM substrate raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_w607am_helper_raises(cli_runner, sbom_project, monkeypatch):
    """Any non-empty W607-AM bucket -> summary.partial_success = True."""

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AM")

    monkeypatch.setattr("roam.commands.cmd_supply_chain.discover_and_parse", _raise)

    result = _invoke_sbom(cli_runner, sbom_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-AM warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, sbom_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AL contracts.
    """

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AM")

    monkeypatch.setattr("roam.commands.cmd_supply_chain.discover_and_parse", _raise)

    result = _invoke_sbom(cli_runner, sbom_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("sbom_discover_and_parse_failed:")]
    assert failure_markers, f"expected sbom_discover_and_parse_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "sbom_discover_and_parse_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-AM stays in ``sbom_*`` family
# ---------------------------------------------------------------------------


def test_w607am_marker_prefix_stays_in_sbom_family(cli_runner, sbom_project, monkeypatch):
    """Every W607-AM substrate marker uses the canonical ``sbom_*`` prefix.

    cmd_sbom is the SBOM EMIT producer -- distinct from sibling W607-*
    layers. Marker prefix MUST stay ``sbom_*`` and MUST NOT leak into
    other family prefixes (``supply_chain_*``, ``cga_*``, ``attest_*``, etc.).
    """

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AM")

    monkeypatch.setattr("roam.commands.cmd_supply_chain.discover_and_parse", _raise)

    result = _invoke_sbom(cli_runner, sbom_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("sbom_"), (
            f"every surfaced W607-AM marker must use the ``sbom_*`` prefix family (cmd_sbom scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
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
# (9) Source-level guard: cmd_sbom carries the W607-AM accumulator
# ---------------------------------------------------------------------------


def test_cmd_sbom_carries_w607am_accumulator():
    """AST-level guard: cmd_sbom source carries the W607-AM accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-AM instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_sbom.py"
    assert src_path.exists(), f"cmd_sbom.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607am_warnings_out" in src, (
        "W607-AM accumulator missing from cmd_sbom; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_am" in src, (
        "W607-AM ``_run_check_am`` helper missing from cmd_sbom; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_am is defined inside cmd_sbom.
    tree = ast.parse(src)
    found_run_check_am = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_am":
            found_run_check_am = True
            break
    assert found_run_check_am, (
        "W607-AM ``_run_check_am`` helper not found in cmd_sbom AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (10) Each W607-AM substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607am_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-AM substrate boundary is wrapped.

    W607-AM substrate inventory (cmd_sbom -- single command):

    * find_project_root             -- project-root resolution (FS boundary)
    * discover_and_parse            -- manifest discovery (FS boundary)
    * compute_graph_reachability    -- the symbol-graph reachability boundary
    * compute_filesystem_reachability -- FS-heuristic reachability boundary
    * merge_reachability            -- merge of graph + FS reachability
    * generate_cyclonedx            -- CycloneDX 1.7 emit boundary (W805 bonus)
    * generate_spdx                 -- SPDX 2.3 emit boundary
    * build_aibom_block             -- AIBOM augmentation boundary
    * serialize_sbom_json           -- the on-text serialization boundary
    * write_sbom_to_disk            -- the disk-write boundary

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_sbom.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "find_project_root",
        "discover_and_parse",
        "compute_graph_reachability",
        "compute_filesystem_reachability",
        "merge_reachability",
        "generate_cyclonedx",
        "generate_spdx",
        "build_aibom_block",
        "serialize_sbom_json",
        "write_sbom_to_disk",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_am("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_am(\n        "{phase}"' in src
            or f'_run_check_am(\n            "{phase}"' in src
            or f'_run_check_am(\n                "{phase}"' in src
            or f'_run_check_am(\n                    "{phase}"' in src
            or f'_run_check_am(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AM _run_check_am wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (11) SBOM/VEX pairing bonus -- W607-AM coexists with W607-AK markers
# ---------------------------------------------------------------------------


def test_w607am_and_w607ak_markers_coexist(cli_runner, sbom_project, monkeypatch):
    """SBOM/VEX pairing bonus: cmd_sbom and cmd_supply_chain both run on
    the same corpus and surface their respective markers without prefix
    collision.

    Because cmd_sbom IMPORTS discover_and_parse from cmd_supply_chain, a
    monkeypatch on ``roam.commands.cmd_supply_chain.discover_and_parse``
    is observed by BOTH commands. The marker family stays distinct:
    cmd_sbom emits ``sbom_discover_and_parse_failed:*`` while
    cmd_supply_chain emits ``supply_chain_discover_and_parse_failed:*``.
    No mixing.
    """
    from unittest import mock

    from roam.commands.cmd_supply_chain import supply_chain

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-shared-corpus-from-W607-AM-pairing")

    monkeypatch.setattr("roam.commands.cmd_supply_chain.discover_and_parse", _raise)

    # 1) cmd_sbom -> sbom_* family
    sbom_result = _invoke_sbom(cli_runner, sbom_project)
    assert sbom_result.exit_code == 0, sbom_result.output
    sbom_data = _json.loads(sbom_result.output)
    sbom_wo = list(sbom_data.get("warnings_out") or [])
    sbom_markers = [m for m in sbom_wo if m.startswith("sbom_")]
    assert sbom_markers, f"expected sbom_* marker; got {sbom_wo!r}"
    assert not any(m.startswith("supply_chain_") for m in sbom_wo), (
        f"sbom output must NOT leak supply_chain_* prefix; got {sbom_wo!r}"
    )

    # 2) cmd_supply_chain -> supply_chain_* family
    sc_obj = {"json": True, "sarif": False, "budget": 0}
    with mock.patch(
        "roam.commands.cmd_supply_chain.find_project_root",
        return_value=sbom_project,
    ):
        sc_result = cli_runner.invoke(supply_chain, [], obj=sc_obj)
    assert sc_result.exit_code == 0, sc_result.output
    sc_data = _json.loads(sc_result.output)
    sc_wo = list(sc_data.get("warnings_out") or []) + list(sc_data["summary"].get("warnings_out") or [])
    sc_markers = [m for m in sc_wo if m.startswith("supply_chain_")]
    assert sc_markers, f"expected supply_chain_* marker; got {sc_wo!r}"
    assert not any(m.startswith("sbom_") for m in sc_wo), (
        f"supply_chain output must NOT leak sbom_* prefix; got {sc_wo!r}"
    )
