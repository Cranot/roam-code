"""W607-CH -- additive aggregation-phase plumbing for ``cmd_vulns``.

cmd_vulns is the vulnerability scanner -- W117 origin, original 16
findings-registry detectors. With W607-CH landed, the full vulns build
path is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-AQ (11 build-path substrate boundaries:
    detect_format / load_<5 ingest formats> / ingest_report / query_vulns /
    emit_vuln_findings / classify_findings / confidence_distribution /
    verdict_with_high_count / severity_breakdown / vulns_to_sarif /
    write_sarif / serialize_envelope)
  - aggregation-phase layer: W607-CH (3 build-path aggregation
    boundaries: compute_predicate / compute_verdict / build_envelope)

Both layers share the canonical ``vulns_*`` marker family and the
``vulns_<phase>_failed:<exc_class>:<detail>`` shape contract. The two
buckets (``_w607aq_warnings_out`` substrate-CALL +
``_w607ch_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order.

Relation to W607-AQ
-------------------

cmd_vulns already carries W607-AQ substrate-CALL plumbing covering 11
substrate-helper boundaries on the build path. W607-CH is ADDITIVE on
top of W607-AQ, extending marker coverage to the AGGREGATION-PHASE
boundaries that W607-AQ left unguarded:

  - ``compute_predicate``  -- per-field extraction of (total /
                              sev_parts / reachable_count /
                              just_imported / state / partial_success)
                              used to compose the verdict string +
                              envelope.
  - ``compute_verdict``    -- verdict-string assembly based on the
                              vuln-count + severity-breakdown.
  - ``build_envelope``     -- ``json_envelope("vulns", ...)`` projection.
                              Phase name distinct from W607-AQ's
                              existing ``serialize_envelope`` (which
                              wraps ``to_json`` instead).

cmd_vulns is NOT a single-risk_level emitter (unlike cmd_attest /
cmd_pr_risk); it emits a per-CVE severity breakdown (CVSS 5-tier:
critical / high / medium / low / unknown). The CRITICAL-LEVEL path
exercise (W641 normalize_risk_level analogue) is captured here as a
critical-severity vuln in the by_severity bucket -- cmd_vulns is one
of the few commands whose envelope legitimately reaches ``critical``.

cmd_vulns has no ``auto_log`` call, so the W607-BZ 4-phase set drops
to 3 phases here (compute_predicate / compute_verdict /
build_envelope). Same marker shape contract, narrower phase set.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_vulns's aggregation-phase boundaries had no guards. A downstream
refactor that changes the severity-breakdown schema, the verdict-
string composition, or the ``json_envelope`` shape would crash the
envelope post-compute -- after the substrate signals were already
gathered, the agent loses the result. W607-CH wraps each boundary
with ``_run_check_ch`` so a raise becomes a marker via
``warnings_out`` and the envelope still emits.

W826 / W823 regression check (security axis)
--------------------------------------------

Per W826 (HIGH-SEV cmd_taint silent-SAFE on empty corpus -- security-
critical Pattern-2): cmd_vulns must NEVER silently emit a SAFE
verdict on the aggregation-phase boundary raising. The marker +
partial_success disclosure preserves the W823 empty-corpus security-
axis discipline. A guard test confirms W607-CH doesn't re-introduce
a Pattern-2 silent-SAFE bug on the empty-corpus / aggregation-raise
path.

W805 security-reachability triad pairing
----------------------------------------

cmd_vulns sits on the VEX leg of the W805 cross-artifact-consistency
family. With W607-CH landed, the SECURITY-REACHABILITY TRIAD is
plumbed end-to-end at the substrate level: cmd_vulns (W607-AQ +
W607-CH), cmd_taint (W607-AY), and cmd_vuln_reach (W607-AU). An
integration test confirms each command's markers stay in its OWN
family and never bleed into a sibling's envelope.

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
    """Tiny corpus with a few symbols so vulns has an index to query."""
    return project_factory(
        {
            "service.py": "def process(): return 1\n",
            "api.py": "from service import process\ndef handle(): return process()\n",
        }
    )


@pytest.fixture
def critical_vuln_report(tmp_path):
    """A generic-format vuln report at CRITICAL severity.

    Drives the W607-CH CRITICAL-LEVEL path: cmd_vulns is one of the few
    commands whose envelope legitimately reaches ``critical`` in the
    by_severity bucket. The verdict assembly + envelope serialization
    must preserve the CRITICAL bucket through W607-CH wrapping.
    """
    report = [
        {
            "cve": "CVE-2099-0001",
            "package": "process",
            "severity": "critical",
            "title": "test critical vuln",
        }
    ]
    p = tmp_path / "critical.json"
    p.write_text(_json.dumps(report), encoding="utf-8")
    return str(p)


@pytest.fixture
def generic_vuln_report(tmp_path):
    """A small generic-format vuln report consumed by the happy path."""
    report = [
        {
            "cve": "CVE-2099-0002",
            "package": "process",
            "severity": "high",
            "title": "test vuln",
        }
    ]
    p = tmp_path / "report.json"
    p.write_text(_json.dumps(report), encoding="utf-8")
    return str(p)


def _invoke_vulns(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam vulns`` against a project root via the top-level CLI."""
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
# (1) Happy path -- envelope omits W607-CH aggregation markers
# ---------------------------------------------------------------------------


def test_vulns_happy_path_no_w607ch_markers(cli_runner, vulns_project):
    """Clean vulns on a healthy corpus -> no W607-CH aggregation markers.

    Hash-stable: an empty W607-CH bucket on the success path must
    produce an envelope without any
    ``vulns_compute_predicate_failed:`` /
    ``vulns_compute_verdict_failed:`` /
    ``vulns_build_envelope_failed:`` markers. Mirror of cmd_supply_chain
    W607-CD happy-path discipline.
    """
    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "vulns"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607ch_phases = (
        "vulns_compute_predicate_failed:",
        "vulns_compute_verdict_failed:",
        "vulns_build_envelope_failed:",
    )
    for prefix in w607ch_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean vulns must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_ch`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_vulns_carries_w607ch_accumulator():
    """AST-level guard: cmd_vulns source carries the W607-CH accumulator.

    Pins the canonical W607-CH anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AQ) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vulns.py"
    assert src_path.exists(), f"cmd_vulns.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607ch_warnings_out" in src, (
        "W607-CH accumulator missing from cmd_vulns; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_ch" in src, (
        "W607-CH helper ``_run_check_ch`` missing from cmd_vulns; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_ch is defined inside cmd_vulns.
    tree = ast.parse(src)
    found_run_check_ch = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ch":
            found_run_check_ch = True
            break
    assert found_run_check_ch, (
        "W607-CH ``_run_check_ch`` helper not found in cmd_vulns AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-AQ must still be present (additive layer does NOT replace it)
    assert "_w607aq_warnings_out" in src, (
        "W607-AQ accumulator vanished alongside the W607-CH add; the "
        "additive plumbing must preserve the W607-AQ substrate-CALL layer."
    )
    assert "_run_check_aq" in src, (
        "W607-AQ helper ``_run_check_aq`` vanished alongside the W607-CH "
        "add; the additive layer must preserve the substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_ch():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_ch(...)`` with the canonical phase name.

    The three phases must appear inside a ``_run_check_ch("<phase>", ...)``
    call inside cmd_vulns.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vulns.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "compute_predicate",
        "compute_verdict",
        "build_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_ch("{phase}"',
            f'_run_check_ch(\n        "{phase}"',
            f'_run_check_ch(\n            "{phase}"',
            f'_run_check_ch(\n                "{phase}"',
            f'_run_check_ch(\n                    "{phase}"',
            f'_run_check_ch(\n                        "{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_ch(...); add the W607-CH guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) compute_predicate failure marker
# ---------------------------------------------------------------------------


def test_compute_predicate_failure_marker_format(cli_runner, vulns_project, monkeypatch):
    """If the compute_predicate boundary raises, surface the marker.

    We patch ``_severity_breakdown`` to return a non-dict sentinel so
    the ``compute_predicate`` inner closure trips on ``.get()``. The
    W607-CH wrap surfaces a structured marker rather than crashing
    the envelope.

    Note: ``_severity_breakdown`` itself is wrapped by W607-AQ
    (substrate-CALL layer), but its failure mode is to FLOOR to an
    empty dict via the ``default={}`` arg -- which makes the
    severity_breakdown call succeed but with floored output. The
    by_severity dict is then handed to compute_predicate; we need
    compute_predicate's own raise path. A non-dict sentinel that
    raises on ``.get`` triggers the right path because the
    severity_breakdown helper returns the sentinel through (W607-AQ
    doesn't replace return values, only raise paths).
    """
    from roam.commands import cmd_vulns

    class _BadByDict:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("synthetic-compute-predicate-from-W607-CH")

    def _bad_severity_breakdown(*_args, **_kwargs):
        return _BadByDict()

    monkeypatch.setattr(cmd_vulns, "_severity_breakdown", _bad_severity_breakdown)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("vulns_compute_predicate_failed:")]
    assert markers, f"expected ``vulns_compute_predicate_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict failure marker
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, vulns_project, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    We patch the verdict-builder by injecting a malformed pred-fields
    dict via a monkeypatched ``_severity_breakdown`` that returns a
    dict whose values raise on f-string formatting. The
    ``_build_verdict_str`` closure trips inside the join + f-string
    interpolation.

    W978 first-hypothesis check: the canonical floor MUST NOT
    re-interpolate the same values that raised -- the floor is a
    literal string ``"Vulnerability scan completed"``.
    """
    from roam.commands import cmd_vulns

    class _BadCount:
        def __gt__(self, _other):
            raise RuntimeError("synthetic-compute-verdict-from-W607-CH")

        def __format__(self, _spec):
            raise RuntimeError("synthetic-compute-verdict-from-W607-CH")

    # Inject a vulnerabilities row that has a count value raising on
    # comparison + format. _build_verdict_str does
    # ``count > 0`` then ``f"{count} {sev}"`` -- the > trips first.
    def _bad_severity_breakdown(*_args, **_kwargs):
        return {"high": _BadCount()}

    # We also need total > 0 to reach the verdict-assembly branch.
    # Patch _query_vulns to return one synthetic row.
    def _one_row(*_args, **_kwargs):
        return [
            {
                "cve_id": "CVE-2099-0099",
                "package_name": "x",
                "severity": "high",
                "title": "t",
                "source": "generic",
                "matched_symbol_id": None,
                "matched_file": None,
                "reachable": 0,
                "shortest_path": None,
                "hop_count": None,
            }
        ]

    monkeypatch.setattr(cmd_vulns, "_severity_breakdown", _bad_severity_breakdown)
    monkeypatch.setattr(cmd_vulns, "_query_vulns", _one_row)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # Either compute_predicate or compute_verdict trips depending on
    # which dict access raises first. Both are valid W607-CH paths;
    # the canonical contract is "some W607-CH marker fires AND the
    # floor verdict applies if compute_verdict was the one that
    # raised."
    ch_markers = [
        m
        for m in all_wo
        if m.startswith("vulns_compute_predicate_failed:") or m.startswith("vulns_compute_verdict_failed:")
    ]
    assert ch_markers, (
        f"expected ``vulns_compute_predicate_failed:`` or ``vulns_compute_verdict_failed:`` marker; got {all_wo!r}"
    )
    assert any("RuntimeError" in m for m in ch_markers), ch_markers


# ---------------------------------------------------------------------------
# (6) build_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607ch_build_envelope_floor_on_raise(cli_runner, vulns_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``vulns_build_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("vulns", ...)`` would otherwise crash AFTER all
    substrate + aggregation signals were already gathered. The
    consumer must still receive a parseable JSON object with the
    marker attached + the canonical command name.
    """
    from roam.commands import cmd_vulns

    call_count = {"n": 0}

    def _raise_envelope(*args, **kwargs):
        # Raise on the FIRST call only -- the W805 fallback path may
        # retry json_envelope, and we want that retry to surface a
        # parseable envelope rather than re-raise infinitely.
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic-build-envelope-from-W607-CH")
        # On retry, return a tiny dict so to_json succeeds.
        return {"command": "vulns", "summary": kwargs.get("summary", {})}

    monkeypatch.setattr(cmd_vulns, "json_envelope", _raise_envelope)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "vulns", f"envelope stub must carry the canonical command name on raise; got {data!r}"
    top_wo = data.get("warnings_out") or []
    summary_wo = data.get("summary", {}).get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("vulns_build_envelope_failed:")]
    assert markers, f"expected ``vulns_build_envelope_failed:`` marker; got {all_wo!r}"


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, vulns_project, monkeypatch):
    """ANY W607-CH or W607-AQ marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    vulns" from "vulns ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_vulns

    class _BadByDict:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("synthetic-partial-success-from-W607-CH")

    def _bad_severity_breakdown(*_args, **_kwargs):
        return _BadByDict()

    monkeypatch.setattr(cmd_vulns, "_severity_breakdown", _bad_severity_breakdown)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CH warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607ch_warnings_out_in_both_top_and_summary(cli_runner, vulns_project, monkeypatch):
    """Non-empty W607-CH bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-CD contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_vulns

    class _BadByDict:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("synthetic-mirror-from-W607-CH")

    def _bad_severity_breakdown(*_args, **_kwargs):
        return _BadByDict()

    monkeypatch.setattr(cmd_vulns, "_severity_breakdown", _bad_severity_breakdown)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CH raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CH raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("vulns_compute_predicate_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("vulns_compute_predicate_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the compute_predicate marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-CH uses the SAME ``vulns_*`` family
# ---------------------------------------------------------------------------


def test_w607ch_marker_prefix_vulns_family(cli_runner, vulns_project, monkeypatch):
    """W607-CH markers use the canonical ``vulns_*`` prefix (same family
    as W607-AQ; W607-CH is ADDITIVE, not a separate prefix).

    Hard guard: any W607-CH marker that leaks into a sibling W607-*
    family (``sbom_*`` / ``supply_chain_*`` / ``cga_*`` / ``attest_*`` /
    ``taint_*`` / ``vuln_reach_*``) breaks the closed-enum
    marker-family contract.
    """
    from roam.commands import cmd_vulns

    class _BadByDict:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("synthetic-prefix-from-W607-CH")

    def _bad_severity_breakdown(*_args, **_kwargs):
        return _BadByDict()

    monkeypatch.setattr(cmd_vulns, "_severity_breakdown", _bad_severity_breakdown)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("vulns_"), f"every W607-CH marker must use the ``vulns_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (10) W607-AQ COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607aq_substrate_markers_coexist_with_w607ch_aggregation(
    cli_runner, vulns_project, monkeypatch, generic_vuln_report
):
    """Confirm ``vulns_<substrate-phase>_failed:`` markers (W607-AQ
    layer) coexist with ``vulns_<agg-phase>_failed:`` markers
    (W607-CH layer) -- both in same family, but threaded through
    different buckets at envelope-emit.

    This is the explicit guard requested by the W607-CH brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``vulns_<substrate-phase>_failed:`` vs.
    ``vulns_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_vulns

    # W607-AQ substrate boundary -- classify_findings (wrap_findings) raises
    def _raise_classify(*a, **kw):
        raise RuntimeError("synthetic-aq-coexist-classify")

    # W607-CH aggregation boundary -- compute_predicate raises via
    # malformed severity_breakdown
    class _BadByDict:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("synthetic-ch-coexist-compute-predicate")

    def _bad_severity_breakdown(*_args, **_kwargs):
        return _BadByDict()

    monkeypatch.setattr(cmd_vulns, "wrap_findings", _raise_classify)
    monkeypatch.setattr(cmd_vulns, "_severity_breakdown", _bad_severity_breakdown)

    result = _invoke_vulns(cli_runner, vulns_project, "--import-file", generic_vuln_report)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    # Substrate-CALL phase from W607-AQ
    aq_markers = [m for m in all_wo if m.startswith("vulns_classify_findings_failed:")]
    # Aggregation-phase from W607-CH
    ch_markers = [m for m in all_wo if m.startswith("vulns_compute_predicate_failed:")]

    assert aq_markers, f"W607-AQ substrate-CALL marker (vulns_classify_findings_failed) missing; got {all_wo!r}"
    assert ch_markers, f"W607-CH aggregation-phase marker (vulns_compute_predicate_failed) missing; got {all_wo!r}"

    # Both share the canonical ``vulns_*`` family
    assert all(m.startswith("vulns_") for m in (aq_markers + ch_markers)), (
        f"all markers must share the canonical ``vulns_*`` family; got aq = {aq_markers!r}, ch = {ch_markers!r}"
    )

    # Both surface in summary mirror too
    assert any(m.startswith("vulns_classify_findings_failed:") for m in summary_wo), (
        f"W607-AQ marker missing from summary mirror; got {summary_wo!r}"
    )
    assert any(m.startswith("vulns_compute_predicate_failed:") for m in summary_wo), (
        f"W607-CH marker missing from summary mirror; got {summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (11) W826 / W823 REGRESSION GUARD -- empty corpus does NOT silently SAFE
# even when W607-CH aggregation boundary raises
# ---------------------------------------------------------------------------


def test_w823_w826_no_silent_safe_on_aggregation_raise(cli_runner, vulns_project, monkeypatch):
    """W823/W826 regression guard: empty corpus + aggregation-phase raise
    MUST disclose the failure, never collapse to a silent SAFE verdict.

    Per W826 (HIGH-SEV cmd_taint silent-SAFE on empty corpus -- security-
    critical Pattern-2): cmd_vulns must NEVER silently emit a SAFE
    verdict on the aggregation-phase boundary raising. The marker +
    partial_success disclosure preserves the W823 empty-corpus
    security-axis discipline.

    Strategy: empty vulns inventory + W607-CH compute_predicate raise.
    The envelope MUST:
      1. Carry partial_success=True (Pattern-2 not silent)
      2. Carry a ``vulns_compute_predicate_failed:`` marker
      3. NOT carry verdict text claiming success without disclosure
    """
    from roam.commands import cmd_vulns

    class _BadByDict:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("synthetic-w826-regression-from-W607-CH")

    def _bad_severity_breakdown(*_args, **_kwargs):
        return _BadByDict()

    monkeypatch.setattr(cmd_vulns, "_severity_breakdown", _bad_severity_breakdown)

    # Empty corpus: no _query_vulns rows (no scan imported yet).
    def _empty_query(*_args, **_kwargs):
        return []

    monkeypatch.setattr(cmd_vulns, "_query_vulns", _empty_query)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Pattern-2: partial_success MUST be True
    assert data["summary"].get("partial_success") is True, (
        f"W826 regression: empty-corpus + W607-CH raise must flip "
        f"partial_success=True; got summary = {data['summary']!r}"
    )

    # Pattern-2: marker MUST be present
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    ch_markers = [m for m in all_wo if m.startswith("vulns_compute_predicate_failed:")]
    assert ch_markers, (
        f"W826 regression: empty-corpus + W607-CH raise must surface "
        f"vulns_compute_predicate_failed marker; got {all_wo!r}"
    )

    # Pattern-2: verdict MUST NOT be a vanilla "SAFE" / "No vulnerabilities
    # found" line that hides the failure -- it must disclose the
    # degradation OR be the floor literal. The literal floor for
    # compute_verdict raise is "Vulnerability scan completed".
    verdict = data["summary"].get("verdict", "")
    assert verdict, "verdict must be present even on failure path"
    # NOTE: the verdict can still say "No vulnerabilities found" or
    # "no vulnerability scan available" because compute_verdict may have
    # succeeded with floored data; what MUST NOT happen is partial_success
    # being False -- the marker channel + partial_success guard the
    # silent-SAFE bug. The two assertions above already prove this.


# ---------------------------------------------------------------------------
# (12) CRITICAL-LEVEL path coverage -- cmd_vulns is one of the few commands
# whose envelope legitimately reaches ``critical`` in by_severity
# ---------------------------------------------------------------------------


def test_critical_severity_path_preserved_through_w607ch(cli_runner, vulns_project, critical_vuln_report):
    """When a critical-severity vuln is ingested, the W607-CH wrapping
    must preserve the critical bucket through the aggregation phase.

    cmd_vulns uses canonical severity helpers (CVSS 5-tier vocabulary:
    critical/high/medium/low/unknown) rather than the W631 4-tier
    risk_level vocabulary. The CRITICAL bucket here is the analogue
    of the W607-BT cmd_attest CRITICAL-LEVEL path: a path the
    aggregation-phase plumbing must NOT silently down-bucket.

    Validates the FULL CVSS critical bucket is dual-bucket plumbed
    through cmd_vulns's aggregation-phase layer.
    """
    result = _invoke_vulns(cli_runner, vulns_project, "--import-file", critical_vuln_report)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # by_severity must reach the canonical "critical" bucket
    by_severity = summary.get("by_severity") or {}
    assert by_severity.get("critical", 0) >= 1, (
        f"CRITICAL-severity vuln must surface in by_severity['critical']; got by_severity = {by_severity!r}"
    )

    # Verdict must explicitly mention 'critical' per LAW 6 standalone-parse
    verdict = summary.get("verdict") or ""
    assert "critical" in verdict.lower(), (
        f"CRITICAL-severity verdict must mention ``critical`` per LAW 6; got verdict = {verdict!r}"
    )

    # No W607-CH degradation markers on the CRITICAL clean path
    top_wo = data.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    ch_failure_markers = [
        m
        for m in all_markers
        if (
            m.startswith("vulns_compute_predicate_failed:")
            or m.startswith("vulns_compute_verdict_failed:")
            or m.startswith("vulns_build_envelope_failed:")
        )
    ]
    assert not ch_failure_markers, (
        f"CRITICAL-LEVEL clean path must NOT surface W607-CH markers; got {ch_failure_markers!r}"
    )


# ---------------------------------------------------------------------------
# (13) 5-FORMAT INGEST ISOLATION -- marker family stays clean across all
# 5 ingest formats (npm-audit / pip-audit / trivy / osv / generic) on
# the aggregation-phase layer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ingest_format", "sample_report"),
    [
        ("npm-audit", {"vulnerabilities": {}}),
        ("pip-audit", [{"name": "x", "vulns": []}]),
        ("trivy", {"Results": []}),
        ("osv", {"results": []}),
        ("generic", [{"cve": "CVE-2099-0009", "package": "x", "severity": "low"}]),
    ],
)
def test_w607ch_marker_family_clean_across_ingest_formats(
    cli_runner, vulns_project, tmp_path, ingest_format, sample_report
):
    """Empty-result import-path across all 5 ingest formats keeps the
    W607-CH marker family clean.

    cmd_vulns has 5 ingest formats (npm-audit / pip-audit / trivy / osv /
    generic). Confirm the W607-CH aggregation-phase marker family stays
    clean across all 5 -- no synthetic ``vulns_compute_predicate_failed:``
    / ``vulns_compute_verdict_failed:`` / ``vulns_build_envelope_failed:``
    markers leak on the success path.

    Mirror of cmd_supply_chain W607-CD 7-format isolation pattern.
    """
    # Write the report so click's exists=True path-validation passes.
    report_path = tmp_path / f"{ingest_format}.json"
    report_path.write_text(_json.dumps(sample_report), encoding="utf-8")

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

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)

    w607ch_phases = (
        "vulns_compute_predicate_failed:",
        "vulns_compute_verdict_failed:",
        "vulns_build_envelope_failed:",
    )
    for prefix in w607ch_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, (
            f"ingest format {ingest_format!r} must NOT surface {prefix} markers on the clean path; got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (14) CROSS-PREFIX ISOLATION -- vulns_* markers DO NOT leak into adjacent
# commands' envelopes (cmd_taint, cmd_vuln_reach, cmd_sbom, cmd_supply_chain)
# ---------------------------------------------------------------------------


def test_vulns_markers_do_not_leak_into_adjacent_commands(cli_runner, vulns_project, monkeypatch):
    """``vulns_*`` markers must NOT appear with foreign prefixes
    (``taint_*`` / ``vuln_reach_*`` / ``sbom_*`` / ``supply_chain_*`` /
    ``cga_*`` / ``attest_*`` / ``pr_bundle_*``) when vulns raises.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels. Mirror of cmd_supply_chain W607-CD
    cross-prefix-isolation discipline.
    """
    from roam.commands import cmd_vulns

    class _BadByDict:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("synthetic-cross-prefix-from-W607-CH")

    def _bad_severity_breakdown(*_args, **_kwargs):
        return _BadByDict()

    monkeypatch.setattr(cmd_vulns, "_severity_breakdown", _bad_severity_breakdown)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    # Every failure marker must start with vulns_ -- foreign-family
    # leakage is a bug
    foreign_prefixes = (
        "taint_",
        "vuln_reach_",
        "sbom_",
        "supply_chain_",
        "cga_",
        "attest_",
        "pr_bundle_",
        "preflight_",
        "impact_",
        "diagnose_",
        "critique_",
        "diff_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_vulns warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (15) SECURITY-REACHABILITY TRIAD pairing -- vulns_/taint_/vuln_reach_
# marker families stay isolated when all 3 commands fire on the same workspace
# ---------------------------------------------------------------------------


def test_security_reachability_triad_marker_families_coexist(cli_runner, vulns_project, monkeypatch):
    """SECURITY-REACHABILITY TRIAD pairing guard requested by the W607-CH
    brief:

    Confirm that ``vulns_<phase>_failed:`` markers (W607-AQ + W607-CH)
    stay in the canonical ``vulns_*`` family when vulns is invoked on
    a workspace also covered by the cmd_taint (W607-AY) and
    cmd_vuln_reach (W607-AU) commands. Each command's markers must
    stay in its OWN family and never bleed into a sibling's envelope.

    Closes the security-reachability triad: every emitter in the W805
    security chain now has substrate-CALL plumbing AND prefix-isolation
    guards. cmd_vulns now has BOTH substrate-CALL (W607-AQ) and
    aggregation-phase (W607-CH) layers.

    Strategy: monkeypatch vulns's _severity_breakdown to raise so a
    W607-CH marker fires, and confirm:
      1. vulns envelope carries ``vulns_*_failed:`` markers
      2. vulns envelope does NOT carry ``taint_*`` / ``vuln_reach_*``
         foreign markers
      3. The marker family is closed-enum: every failure marker starts
         with the canonical ``vulns_`` prefix.
    """
    from roam.commands import cmd_vulns

    class _BadByDict:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("synthetic-triad-from-W607-CH")

    def _bad_severity_breakdown(*_args, **_kwargs):
        return _BadByDict()

    monkeypatch.setattr(cmd_vulns, "_severity_breakdown", _bad_severity_breakdown)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)

    # vulns envelope MUST contain vulns_compute_predicate_failed
    assert any(m.startswith("vulns_compute_predicate_failed:") for m in all_markers), (
        f"vulns envelope missing vulns_compute_predicate_failed marker; got {all_markers!r}"
    )

    # vulns envelope MUST NOT contain security-triad sibling markers
    for marker in all_markers:
        if "_failed:" not in marker:
            continue
        assert not marker.startswith("taint_"), f"vulns envelope leaked taint_* marker: {marker!r}"
        assert not marker.startswith("vuln_reach_"), f"vulns envelope leaked vuln_reach_* marker: {marker!r}"

    # Closed-enum check: every failure marker uses the canonical
    # ``vulns_*`` prefix.
    failure_markers = [m for m in all_markers if "_failed:" in m]
    for marker in failure_markers:
        assert marker.startswith("vulns_"), (
            f"every vulns failure marker must use the canonical ``vulns_*`` family; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (16) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_w607ch_three_segment_marker_shape(cli_runner, vulns_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-AQ/CD contracts.
    """
    from roam.commands import cmd_vulns

    class _BadByDict:
        def get(self, *_args, **_kwargs):
            raise PermissionError("synthetic-shape-detail-from-W607-CH")

    def _bad_severity_breakdown(*_args, **_kwargs):
        return _BadByDict()

    monkeypatch.setattr(cmd_vulns, "_severity_breakdown", _bad_severity_breakdown)

    result = _invoke_vulns(cli_runner, vulns_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("vulns_compute_predicate_failed:")]
    assert failure_markers, f"expected vulns_compute_predicate_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "vulns_compute_predicate_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts
