"""W607-AQ -- ``cmd_vulns`` substrate-boundary plumbing.

Thirty-fourth-in-batch W607 consumer-layer arc. Fresh-plumbing wave:
cmd_vulns had NO pre-existing ``warnings_out`` channel and NO ``_run_check`` /
substrate-CALL marker plumbing -- so the canonical fresh template applies
(one accumulator + one ``_run_check_aq`` helper) with the marker prefix
``vulns_*`` outright on a brand-new ``summary.warnings_out`` field.

cmd_vulns is the VEX projection leg + the upstream OSV / NVD / Trivy /
npm-audit / pip-audit / osv-scanner ingest substrate. It sits on the W805
cross-artifact family and closes the SBOM / VEX / supply-chain triad
together with cmd_sbom (W607-AM, SBOM EMIT producer) and cmd_supply_chain
(W607-AK, supply-chain consumer/projection). Each substrate boundary --
detect_format / load_npm_audit / load_pip_audit / load_trivy / load_osv /
load_generic / ingest_report / query_vulns / classify_findings /
vulns_to_sarif / serialize_envelope -- can raise; prior to W607-AQ a raise
crashed the whole vulns invocation wholesale.

W805 cross-artifact consistency family
--------------------------------------

cmd_vulns sits on the VEX leg of the W805 cross-artifact-consistency family.
The eventual VEX-with-content-hash-binding artifact would naturally extend
the W805 drift-table; W607-AQ gives the runtime-raise complement to that
future structural pin. The triad closure milestone: with W607-AK
(supply_chain) + W607-AM (sbom) + W607-AQ (vulns) the supply-chain
producer chain is W607-plumbed end-to-end. A raise anywhere in {ingest,
normalize, reach, sbom-build, vex-build, sign, write} surfaces a marker
rather than crashing.

W978 first-hypothesis check
---------------------------

Each W607-AQ-wrapped substrate has a documented empty-floor default
matching its happy-path return shape so a raise degrades cleanly. Dominant
raise axes are: filesystem refusal (``detect_format`` on truncated JSON),
parser refusal (``load_<format>`` on malformed scanner output), missing
DB column (``query_vulns`` on pre-W117 schema), and serialization
(``serialize_envelope``).

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The ingest dispatchers
are imported inside ``_ingest_report`` at the call-site, so patches go
via ``monkeypatch.setattr`` on ``cmd_vulns`` module-level helpers
(``_ingest_report`` / ``_detect_format`` / ``_query_vulns`` /
``_emit_vuln_findings`` / ``_vulns_to_sarif``).

Marker prefix discipline
------------------------

Marker family is ``vulns_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers (``sbom_*``,
``supply_chain_*``, ``cga_*``, ``attest_*``, etc.) preserved
by the prefix-discipline test.

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
def vulns_project(project_factory):
    """Tiny corpus with a few symbols so vulns has an index to query.

    The actual ingest reports are written by individual tests because the
    multi-ingest-format coverage parametrizes over the on-disk file shape.
    """
    return project_factory(
        {
            "service.py": "def process(): return 1\n",
            "api.py": "from service import process\ndef handle(): return process()\n",
        }
    )


@pytest.fixture
def generic_vuln_report(tmp_path):
    """A small generic-format vuln report consumed by the happy path."""
    report = [
        {
            "cve": "CVE-2099-0001",
            "package": "process",
            "severity": "high",
            "title": "test vuln",
        }
    ]
    p = tmp_path / "report.json"
    p.write_text(_json.dumps(report), encoding="utf-8")
    return str(p)


def _invoke_vulns(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam vulns`` against a project root via the top-level CLI.

    Using the top-level CLI rather than the click command directly so the
    ``--json`` flag wires into ``ctx.obj`` the same way the production
    invocation does.
    """
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("vulns")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-AQ substrate-CALL markers
# ---------------------------------------------------------------------------


def test_vulns_clean_envelope_omits_w607aq_markers(cli_runner, vulns_project):
    """Clean vulns -> no W607-AQ substrate markers.

    Byte-identical-on-happy-path: an empty W607-AQ bucket on the success
    path must NOT introduce ``vulns_<phase>_failed:`` markers on the
    envelope. cmd_vulns has no pre-existing warnings_out channel, so the
    field is absent entirely on the clean path.
    """
    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "vulns"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    aq_markers = [m for m in (list(top_wo) + list(summary_wo)) if "_failed:" in m and m.startswith("vulns_")]
    assert not aq_markers, (
        f"clean vulns must NOT surface W607-AQ substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) query_vulns failure -> structured marker + degraded envelope
# ---------------------------------------------------------------------------


def test_vulns_query_vulns_failure_marker_format(cli_runner, vulns_project, monkeypatch):
    """If ``_query_vulns`` raises, surface the W607-AQ marker.

    The DB-query is the inventory-read substrate boundary -- a raise here
    previously crashed the whole vulns invocation. W607-AQ surfaces it as
    a structured ``vulns_query_vulns_failed:<exc>:<detail>`` marker and
    emits a structured degraded envelope (zero vulns) rather than crashing.
    """
    from roam.commands import cmd_vulns

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-query-from-W607-AQ")

    monkeypatch.setattr(cmd_vulns, "_query_vulns", _raise)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("vulns_query_vulns_failed:")]
    assert markers, f"expected vulns_query_vulns_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-query-from-W607-AQ" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"query-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_vulns_w607aq_warnings_in_envelope(cli_runner, vulns_project, monkeypatch):
    """Non-empty W607-AQ bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_vulns

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AQ")

    monkeypatch.setattr(cmd_vulns, "_query_vulns", _raise)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AQ disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AQ disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("vulns_query_vulns_failed:")]
    assert markers, f"expected vulns_query_vulns_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) partial_success flips when W607-AQ substrate raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_w607aq_helper_raises(cli_runner, vulns_project, monkeypatch):
    """Any non-empty W607-AQ bucket -> summary.partial_success = True."""
    from roam.commands import cmd_vulns

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AQ")

    monkeypatch.setattr(cmd_vulns, "_query_vulns", _raise)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-AQ warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, vulns_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AM contracts.
    """
    from roam.commands import cmd_vulns

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AQ")

    monkeypatch.setattr(cmd_vulns, "_query_vulns", _raise)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("vulns_query_vulns_failed:")]
    assert failure_markers, f"expected vulns_query_vulns_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "vulns_query_vulns_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) classify_findings failure -> structured marker
# ---------------------------------------------------------------------------


def test_vulns_classify_findings_failure_marker(cli_runner, vulns_project, monkeypatch, generic_vuln_report):
    """If ``wrap_findings`` (the R22 classify boundary) raises, surface a marker.

    This is the JSON-mode-only classify substrate; the SARIF / text paths
    do not call it. Ensures the W607-AQ disclosure covers the
    confidence-bucketing leg.
    """
    from roam.commands import cmd_vulns

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-classify-from-W607-AQ")

    monkeypatch.setattr(cmd_vulns, "wrap_findings", _raise)

    result = _invoke_vulns(cli_runner, vulns_project, "--import-file", generic_vuln_report)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("vulns_classify_findings_failed:")]
    assert markers, f"expected vulns_classify_findings_failed: marker; got {all_wo!r}"


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- W607-AQ stays in ``vulns_*`` family
# ---------------------------------------------------------------------------


def test_w607aq_marker_prefix_stays_in_vulns_family(cli_runner, vulns_project, monkeypatch):
    """Every W607-AQ substrate marker uses the canonical ``vulns_*`` prefix.

    cmd_vulns is the VEX projection / vuln-ingest substrate -- distinct
    from sibling W607-* layers. Marker prefix MUST stay ``vulns_*`` and
    MUST NOT leak into other family prefixes (``sbom_*``,
    ``supply_chain_*``, ``cga_*``, ``attest_*``, etc.).
    """
    from roam.commands import cmd_vulns

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AQ")

    monkeypatch.setattr(cmd_vulns, "_query_vulns", _raise)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("vulns_"), (
            f"every surfaced W607-AQ marker must use the ``vulns_*`` prefix family (cmd_vulns scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
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
# (8) Source-level guard: cmd_vulns carries the W607-AQ accumulator
# ---------------------------------------------------------------------------


def test_cmd_vulns_carries_w607aq_accumulator():
    """AST-level guard: cmd_vulns source carries the W607-AQ accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-AQ instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vulns.py"
    assert src_path.exists(), f"cmd_vulns.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607aq_warnings_out" in src, (
        "W607-AQ accumulator missing from cmd_vulns; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_aq" in src, (
        "W607-AQ ``_run_check_aq`` helper missing from cmd_vulns; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_aq is defined inside cmd_vulns.
    tree = ast.parse(src)
    found_run_check_aq = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_aq":
            found_run_check_aq = True
            break
    assert found_run_check_aq, (
        "W607-AQ ``_run_check_aq`` helper not found in cmd_vulns AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (9) Each W607-AQ substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607aq_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-AQ substrate boundary is wrapped.

    W607-AQ substrate inventory (cmd_vulns -- single command):

    * detect_format             -- format auto-detection
    * load_npm_audit            -- npm-audit ingest (multi-ingest-format)
    * load_pip_audit            -- pip-audit ingest (multi-ingest-format)
    * load_trivy                -- trivy ingest (multi-ingest-format)
    * load_osv                  -- osv ingest (multi-ingest-format)
    * load_generic              -- generic JSON ingest (multi-ingest-format)
    * query_vulns               -- DB inventory read
    * emit_vuln_findings        -- the findings-registry write boundary
    * classify_findings         -- the R22 confidence-bucketing boundary
    * vulns_to_sarif            -- the SARIF projection boundary
    * serialize_envelope        -- the on-text JSON serialization boundary

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vulns.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "detect_format",
        "load_npm_audit",
        "load_pip_audit",
        "load_trivy",
        "load_osv",
        "load_generic",
        "query_vulns",
        "emit_vuln_findings",
        "classify_findings",
        "vulns_to_sarif",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_aq("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_aq(\n        "{phase}"' in src
            or f'_run_check_aq(\n            "{phase}"' in src
            or f'_run_check_aq(\n                "{phase}"' in src
            or f'_run_check_aq(\n                    "{phase}"' in src
            or f'_run_check_aq(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AQ _run_check_aq wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (10) Multi-ingest-format coverage -- one assertion per ingest substrate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ingest_format", "expected_phase", "sample_report"),
    [
        (
            "npm-audit",
            "load_npm_audit",
            {"vulnerabilities": {}},
        ),
        (
            "pip-audit",
            "load_pip_audit",
            [{"name": "x", "vulns": []}],
        ),
        (
            "trivy",
            "load_trivy",
            {"Results": []},
        ),
        (
            "osv",
            "load_osv",
            {"results": []},
        ),
        (
            "generic",
            "load_generic",
            [{"cve": "CVE-2099-0001", "package": "x", "severity": "low"}],
        ),
    ],
)
def test_per_ingest_format_marker(
    cli_runner,
    vulns_project,
    monkeypatch,
    tmp_path,
    ingest_format,
    expected_phase,
    sample_report,
):
    """Highest-signal multi-ingest-format coverage.

    Drives each ingest-format boundary (npm-audit / pip-audit / trivy /
    osv / generic) with a simulated raise inside ``_ingest_report`` and
    asserts the corresponding ``vulns_load_<format>_failed:`` marker
    appears. This is the canonical coverage for the ingest substrate.
    """
    from roam.commands import cmd_vulns

    # Write the report so click's exists=True path-validation passes.
    report_path = tmp_path / f"{ingest_format}.json"
    report_path.write_text(_json.dumps(sample_report), encoding="utf-8")

    def _raise(*args, **kwargs):
        raise RuntimeError(f"synthetic-{ingest_format}-from-W607-AQ")

    monkeypatch.setattr(cmd_vulns, "_ingest_report", _raise)

    result = _invoke_vulns(
        cli_runner,
        vulns_project,
        "--import-file",
        str(report_path),
        "--format",
        ingest_format,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith(f"vulns_{expected_phase}_failed:")]
    assert markers, f"expected vulns_{expected_phase}_failed: marker for {ingest_format} ingest; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert data["summary"].get("partial_success") is True, (
        f"per-ingest-format failure must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (11) SBOM / VEX / supply-chain triad pairing -- W607-AQ coexists with
#      W607-AM (sbom) and W607-AK (supply_chain) markers
# ---------------------------------------------------------------------------


def test_w607aq_and_w607am_and_w607ak_markers_coexist(cli_runner, vulns_project, monkeypatch, tmp_path):
    """SBOM/VEX/supply-chain triad closure bonus.

    cmd_vulns, cmd_sbom, and cmd_supply_chain all run on the same corpus
    and surface their respective markers without prefix collision. The
    marker families stay distinct: cmd_vulns -> ``vulns_*``,
    cmd_sbom -> ``sbom_*``, cmd_supply_chain -> ``supply_chain_*``.
    No mixing.
    """
    from unittest import mock

    from roam.commands import cmd_vulns as _cmd_vulns_mod
    from roam.commands.cmd_sbom import sbom
    from roam.commands.cmd_supply_chain import supply_chain

    def _raise_vulns(*args, **kwargs):
        raise RuntimeError("synthetic-triad-vulns-from-W607-AQ")

    monkeypatch.setattr(_cmd_vulns_mod, "_query_vulns", _raise_vulns)

    # 1) cmd_vulns -> vulns_* family
    vulns_result = _invoke_vulns(cli_runner, vulns_project)
    assert vulns_result.exit_code == 0, vulns_result.output
    vulns_data = _json.loads(vulns_result.output)
    vulns_wo = list(vulns_data.get("warnings_out") or []) + list(vulns_data["summary"].get("warnings_out") or [])
    vulns_markers = [m for m in vulns_wo if m.startswith("vulns_")]
    assert vulns_markers, f"expected vulns_* marker; got {vulns_wo!r}"
    assert not any(m.startswith("sbom_") for m in vulns_wo), (
        f"vulns output must NOT leak sbom_* prefix; got {vulns_wo!r}"
    )
    assert not any(m.startswith("supply_chain_") for m in vulns_wo), (
        f"vulns output must NOT leak supply_chain_* prefix; got {vulns_wo!r}"
    )

    # 2) cmd_sbom -> sbom_* family (raise inside discover_and_parse)
    def _raise_sbom(*args, **kwargs):
        raise RuntimeError("synthetic-triad-sbom-from-W607-AQ")

    monkeypatch.setattr("roam.commands.cmd_supply_chain.discover_and_parse", _raise_sbom)

    sbom_obj = {"json": True, "sarif": False, "budget": 0}
    with mock.patch(
        "roam.commands.cmd_sbom.find_project_root",
        return_value=Path(str(vulns_project)),
    ):
        sbom_result = cli_runner.invoke(sbom, ["--no-reachability"], obj=sbom_obj)
    assert sbom_result.exit_code == 0, sbom_result.output
    sbom_data = _json.loads(sbom_result.output)
    sbom_wo = list(sbom_data.get("warnings_out") or []) + list(sbom_data["summary"].get("warnings_out") or [])
    sbom_markers = [m for m in sbom_wo if m.startswith("sbom_")]
    assert sbom_markers, f"expected sbom_* marker; got {sbom_wo!r}"
    assert not any(m.startswith("vulns_") for m in sbom_wo), (
        f"sbom output must NOT leak vulns_* prefix; got {sbom_wo!r}"
    )

    # 3) cmd_supply_chain -> supply_chain_* family
    sc_obj = {"json": True, "sarif": False, "budget": 0}
    with mock.patch(
        "roam.commands.cmd_supply_chain.find_project_root",
        return_value=Path(str(vulns_project)),
    ):
        sc_result = cli_runner.invoke(supply_chain, [], obj=sc_obj)
    assert sc_result.exit_code == 0, sc_result.output
    sc_data = _json.loads(sc_result.output)
    sc_wo = list(sc_data.get("warnings_out") or []) + list(sc_data["summary"].get("warnings_out") or [])
    sc_markers = [m for m in sc_wo if m.startswith("supply_chain_")]
    assert sc_markers, f"expected supply_chain_* marker; got {sc_wo!r}"
    assert not any(m.startswith("vulns_") for m in sc_wo), (
        f"supply_chain output must NOT leak vulns_* prefix; got {sc_wo!r}"
    )
    assert not any(m.startswith("sbom_") for m in sc_wo), (
        f"supply_chain output must NOT leak sbom_* prefix; got {sc_wo!r}"
    )
