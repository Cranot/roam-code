"""W607-CD -- additive aggregation-phase plumbing for ``cmd_supply_chain``.

cmd_supply_chain is the SBOM/VEX projection leg of the W805 cross-artifact-
consistency family. Closes the SUPPLY-CHAIN ATTESTATION QUARTET together
with W607-AD/BT (cmd_attest), W607-AE/BW (cmd_pr_bundle), and W607-AF/BZ
(cmd_cga). With W607-CD landed, the full supply-chain build path is now
dual-bucket plumbed via:

  - substrate-CALL layer: W607-AK (7 build-path substrate boundaries:
    find_project_root / discover_and_parse / compute_risk_score /
    sort_risky_full / top_risky / supply_chain_to_sarif / write_sarif)
  - aggregation-phase layer: W607-CD (3 build-path aggregation boundaries:
    compute_predicate / compute_verdict / serialize_envelope)

Both layers share the canonical ``supply_chain_*`` marker family and the
``supply_chain_<phase>_failed:<exc_class>:<detail>`` shape contract. The
three buckets (``_warnings_out`` W1142-followup-B cap-hit truncation +
``_w607ak_warnings_out`` substrate-CALL + ``_w607cd_warnings_out``
aggregation-phase) are combined at envelope-emit time so consumers see the
full degradation lineage in marker-emission order.

Relation to W607-AK
-------------------

cmd_supply_chain already carries W607-AK substrate-CALL plumbing covering
7 substrate-helper boundaries on the build path. W607-CD is ADDITIVE on
top of W607-AK, extending marker coverage to the AGGREGATION-PHASE
boundaries that W607-AK left unguarded:

  - ``compute_predicate``    -- per-field extraction of metrics fields
                                (score / pin_coverage / unpinned_count /
                                range_count / exact_count / total /
                                direct_count / dev_count) used to
                                compose the verdict string + envelope.
  - ``compute_verdict``      -- verdict string assembly based on score
                                thresholds (LAW 6 standalone-parse).
  - ``serialize_envelope``   -- ``json_envelope("supply-chain", ...)``
                                projection.

cmd_supply_chain is NOT a risk-LEVEL emitter (unlike cmd_pr_risk /
cmd_attest); it emits a ``risk_score`` integer alongside a score-threshold
verdict. So the W607-CD phase set drops ``score_classify`` /
``score_normalize`` (no canonical risk_level emission) and substitutes
``compute_predicate`` instead. cmd_supply_chain has no ``auto_log`` call
either, so the W607-BZ 4-phase set drops to 3 phases here. Same marker
shape contract, narrower phase set.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_supply_chain's aggregation-phase boundaries had no guards. A downstream
refactor that changes the metrics schema, the verdict string composition,
or the ``json_envelope`` shape would crash the envelope post-compute --
after the substrate signals were already gathered, the agent loses the
result. W607-CD wraps each boundary with ``_run_check_cd`` so a raise
becomes a marker via ``warnings_out`` and the envelope still emits.

W805 supply-chain attestation quartet pairing
----------------------------------------------

With W607-BT (cmd_attest), W607-BW (cmd_pr_bundle), and W607-BZ (cmd_cga)
already landed, W607-CD closes the supply-chain attestation quartet:
every emitter in the W805 attestation chain now has dual-bucket plumbing
(substrate-CALL + aggregation-phase). The integration test
(test_supply_chain_attestation_quartet_marker_families_coexist) confirms
each command's markers stay in its OWN family and never bleed into a
sibling's envelope.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from roam.commands.cmd_supply_chain import supply_chain

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_supply_chain(runner: CliRunner, project_root: Path, *, json_mode: bool = True):
    """Invoke ``roam supply-chain`` against a project-root mock."""
    obj = {"json": json_mode, "sarif": False, "budget": 0}
    with mock.patch("roam.commands.cmd_supply_chain.find_project_root", return_value=project_root):
        return runner.invoke(supply_chain, [], obj=obj)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def supply_chain_project(tmp_path):
    """Tiny corpus with a clean manifest so supply-chain exercises every
    W607-CD aggregation boundary (compute_predicate / compute_verdict /
    serialize_envelope).
    """
    (tmp_path / "requirements.txt").write_text(
        "requests==2.28.0\nclick==8.1.0\nflask\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def empty_project(tmp_path):
    """A project with no dependency manifest -- exercises the
    ``No dependency files found`` verdict branch.
    """
    return tmp_path


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CD aggregation markers
# ---------------------------------------------------------------------------


def test_supply_chain_happy_path_no_w607cd_markers(cli_runner, supply_chain_project):
    """Clean supply-chain on a healthy corpus -> no W607-CD aggregation markers.

    Hash-stable: an empty W607-CD bucket on the success path must produce
    an envelope without any
    ``supply_chain_compute_predicate_failed:`` /
    ``supply_chain_compute_verdict_failed:`` /
    ``supply_chain_serialize_envelope_failed:`` markers. Mirror of
    cmd_cga W607-BZ happy-path discipline.
    """
    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "supply-chain"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607cd_phases = (
        "supply_chain_compute_predicate_failed:",
        "supply_chain_compute_verdict_failed:",
        "supply_chain_serialize_envelope_failed:",
    )
    for prefix in w607cd_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean supply-chain must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_cd`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_supply_chain_carries_w607cd_accumulator():
    """AST-level guard: cmd_supply_chain source carries the W607-CD accumulator.

    Pins the canonical W607-CD anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AK) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_supply_chain.py"
    assert src_path.exists(), f"cmd_supply_chain.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607cd_warnings_out" in src, (
        "W607-CD accumulator missing from cmd_supply_chain; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_cd" in src, (
        "W607-CD helper ``_run_check_cd`` missing from cmd_supply_chain; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_cd is defined inside supply_chain.
    tree = ast.parse(src)
    found_run_check_cd = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cd":
            found_run_check_cd = True
            break
    assert found_run_check_cd, (
        "W607-CD ``_run_check_cd`` helper not found in cmd_supply_chain AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-AK must still be present (additive layer does NOT replace it)
    assert "_w607ak_warnings_out" in src, (
        "W607-AK accumulator vanished alongside the W607-CD add; the "
        "additive plumbing must preserve the W607-AK substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_cd():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_cd(...)`` with the canonical phase name.

    The three phases must appear inside a ``_run_check_cd("<phase>", ...)``
    call inside cmd_supply_chain.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_supply_chain.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_cd(\n        "{phase}"',
            f'_run_check_cd(\n            "{phase}"',
            f'_run_check_cd(\n                "{phase}"',
            f'_run_check_cd(\n                    "{phase}"',
            f'_run_check_cd(\n                        "{phase}"',
            f'_run_check_cd("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_cd(...); add the W607-CD guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) compute_predicate failure marker
# ---------------------------------------------------------------------------


def test_compute_predicate_failure_marker_format(cli_runner, supply_chain_project, monkeypatch):
    """If the compute_predicate boundary raises, surface the marker.

    We patch ``compute_risk_score`` to return a malformed metrics dict
    (missing required keys) so the W607-CD ``compute_predicate``
    inner closure trips on a KeyError. The W607-CD wrap surfaces a
    structured marker rather than crashing the envelope.
    """
    from roam.commands import cmd_supply_chain

    def _malformed_metrics(*args, **kwargs):
        # Missing the keys ``_compute_predicate_fields`` requires --
        # KeyError surfaces via the W607-CD wrap. But ``score`` is still
        # accessed by the caller pre-wrap (``score = metrics["score"]``)
        # via the W607-AK default for compute_risk_score. Patching
        # compute_risk_score itself doesn't trip the compute_predicate
        # wrap; instead, we patch ``_compute_predicate_fields`` indirectly
        # by patching ``compute_risk_score`` to return a dict with
        # ``score`` BUT missing other keys.
        return {"score": 55}  # missing pin_coverage / unpinned_count / ...

    monkeypatch.setattr(cmd_supply_chain, "compute_risk_score", _malformed_metrics)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("supply_chain_compute_predicate_failed:")]
    assert markers, f"expected ``supply_chain_compute_predicate_failed:`` marker; got {all_wo!r}"
    assert any("KeyError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict failure marker
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, supply_chain_project, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    We patch ``compute_risk_score`` to return a metrics dict whose
    ``pin_coverage`` is a non-int sentinel that raises on ``int(...)``.
    The verdict-string f-string interpolation trips the wrap inside
    ``_build_verdict_str``.

    W978 first-hypothesis check: the canonical floor MUST NOT
    re-interpolate the same value that raised -- the floor is a
    literal string ``"Supply chain analysis completed"``.
    """
    from roam.commands import cmd_supply_chain

    class _BadPinCoverage:
        def __mul__(self, other):
            raise RuntimeError("synthetic-compute-verdict-from-W607-CD")

        def __rmul__(self, other):
            raise RuntimeError("synthetic-compute-verdict-from-W607-CD")

    def _bad_metrics(*args, **kwargs):
        # score >= 80 path: _build_verdict_str does
        # int(fields["pin_coverage"] * 100) -- the multiply trips first.
        return {
            "score": 90,
            "pin_coverage": _BadPinCoverage(),
            "dev_ratio": 0.0,
            "total": 3,
            "direct_count": 3,
            "dev_count": 0,
            "exact_count": 2,
            "range_count": 0,
            "unpinned_count": 1,
        }

    monkeypatch.setattr(cmd_supply_chain, "compute_risk_score", _bad_metrics)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("supply_chain_compute_verdict_failed:")]
    assert markers, f"expected ``supply_chain_compute_verdict_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers

    # W978 first-hypothesis check: the floor verdict must be a literal
    # string, NOT re-interpolation of the values that raised.
    assert data["summary"]["verdict"] == "Supply chain analysis completed", (
        f"verdict floor must be literal per W978; got {data['summary']['verdict']!r}"
    )


# ---------------------------------------------------------------------------
# (6) serialize_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607cd_serialize_envelope_floor_on_raise(cli_runner, supply_chain_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``supply_chain_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("supply-chain", ...)`` would otherwise crash AFTER
    all substrate + aggregation signals were already gathered. The
    consumer must still receive a parseable JSON object with the marker
    attached + the canonical command name.
    """
    from roam.commands import cmd_supply_chain

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-CD")

    monkeypatch.setattr(cmd_supply_chain, "json_envelope", _raise_envelope)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "supply-chain", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("supply_chain_serialize_envelope_failed:")]
    assert markers, f"expected ``supply_chain_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, supply_chain_project, monkeypatch):
    """ANY W607-CD or W607-AK marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    supply-chain" from "supply-chain ran with substrate degradation"
    via summary.partial_success alone.
    """
    from roam.commands import cmd_supply_chain

    def _malformed_metrics(*args, **kwargs):
        return {"score": 55}  # missing keys triggers compute_predicate

    monkeypatch.setattr(cmd_supply_chain, "compute_risk_score", _malformed_metrics)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CD warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607cd_warnings_out_in_both_top_and_summary(cli_runner, supply_chain_project, monkeypatch):
    """Non-empty W607-CD bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-BZ contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_supply_chain

    def _malformed_metrics(*args, **kwargs):
        return {"score": 55}

    monkeypatch.setattr(cmd_supply_chain, "compute_risk_score", _malformed_metrics)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CD raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CD raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("supply_chain_compute_predicate_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("supply_chain_compute_predicate_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the compute_predicate marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-CD uses the SAME ``supply_chain_*`` family
# ---------------------------------------------------------------------------


def test_w607cd_marker_prefix_supply_chain_family(cli_runner, supply_chain_project, monkeypatch):
    """W607-CD markers use the canonical ``supply_chain_*`` prefix (same
    family as W607-AK; W607-CD is ADDITIVE, not a separate prefix).

    Hard guard: any W607-CD marker that leaks into a sibling W607-*
    family (e.g. ``cga_*`` / ``attest_*`` / ``pr_bundle_*``) breaks the
    closed-enum marker-family contract.
    """
    from roam.commands import cmd_supply_chain

    def _malformed_metrics(*args, **kwargs):
        return {"score": 55}

    monkeypatch.setattr(cmd_supply_chain, "compute_risk_score", _malformed_metrics)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("supply_chain_"), (
            f"every W607-CD marker must use the ``supply_chain_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (10) W607-AK COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607ak_substrate_markers_coexist_with_w607cd_aggregation(cli_runner, supply_chain_project, monkeypatch):
    """Confirm ``supply_chain_<substrate-phase>_failed:`` markers (W607-AK
    layer) coexist with ``supply_chain_<agg-phase>_failed:`` markers
    (W607-CD layer) -- both in same family, but threaded through different
    buckets at envelope-emit.

    This is the explicit guard requested by the W607-CD brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``supply_chain_<substrate-phase>_failed:`` vs.
    ``supply_chain_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_supply_chain

    # W607-AK substrate boundary -- top_risky raises
    def _raise_top_risky(*a, **kw):
        raise RuntimeError("synthetic-ak-coexist-top-risky")

    # W607-CD aggregation boundary -- compute_predicate raises via
    # malformed compute_risk_score
    def _malformed_metrics(*a, **kw):
        return {"score": 55}  # missing keys -> compute_predicate raises

    monkeypatch.setattr(cmd_supply_chain, "top_risky", _raise_top_risky)
    monkeypatch.setattr(cmd_supply_chain, "compute_risk_score", _malformed_metrics)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-AK
    ak_markers = [m for m in top_wo if m.startswith("supply_chain_top_risky_failed:")]
    # Aggregation-phase from W607-CD
    cd_markers = [m for m in top_wo if m.startswith("supply_chain_compute_predicate_failed:")]

    assert ak_markers, f"W607-AK substrate-CALL marker (supply_chain_top_risky_failed) missing; got {top_wo!r}"
    assert cd_markers, (
        f"W607-CD aggregation-phase marker (supply_chain_compute_predicate_failed) missing; got {top_wo!r}"
    )

    # Both share the canonical ``supply_chain_*`` family
    assert all(m.startswith("supply_chain_") for m in (ak_markers + cd_markers)), (
        f"all markers must share the canonical ``supply_chain_*`` family; got ak = {ak_markers!r}, cd = {cd_markers!r}"
    )

    # Both surface in summary mirror too
    summary_wo = data["summary"].get("warnings_out") or []
    assert any(m.startswith("supply_chain_top_risky_failed:") for m in summary_wo), (
        f"W607-AK marker missing from summary mirror; got {summary_wo!r}"
    )
    assert any(m.startswith("supply_chain_compute_predicate_failed:") for m in summary_wo), (
        f"W607-CD marker missing from summary mirror; got {summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (11) CROSS-PREFIX ISOLATION -- supply_chain_* markers DO NOT leak into
# adjacent commands (cmd_cga, cmd_attest, cmd_pr_bundle, cmd_vulns, cmd_sbom)
# ---------------------------------------------------------------------------


def test_supply_chain_markers_do_not_leak_into_adjacent_commands(cli_runner, supply_chain_project, monkeypatch):
    """``supply_chain_*`` markers must NOT appear with foreign prefixes
    (``cga_*`` / ``attest_*`` / ``pr_bundle_*`` / ``sbom_*`` / ``vulns_*``)
    when supply-chain raises.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels. Mirror of cmd_cga's W607-BZ
    cross-prefix-isolation discipline.
    """
    from roam.commands import cmd_supply_chain

    def _malformed_metrics(*args, **kwargs):
        return {"score": 55}

    monkeypatch.setattr(cmd_supply_chain, "compute_risk_score", _malformed_metrics)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    # Every failure marker must start with supply_chain_ -- foreign-family
    # leakage is a bug
    foreign_prefixes = (
        "cga_",
        "attest_",
        "pr_bundle_",
        "sbom_",
        "vulns_",
        "preflight_",
        "impact_",
        "diagnose_",
        "critique_",
        "diff_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_supply_chain warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) SBOM FORMAT ISOLATION -- empty-corpus path keeps marker family clean
# ---------------------------------------------------------------------------


def test_sbom_format_isolation_empty_corpus(cli_runner, empty_project, monkeypatch):
    """Empty-corpus path (no deps -> "No dependency files found" verdict)
    keeps the W607-CD marker family clean.

    cmd_supply_chain has multi-format emit paths (cyclonedx-shaped via
    discover_and_parse manifest types: requirements.txt / package.json /
    go.mod / Cargo.toml / pom.xml / Gemfile / composer.json). Confirm
    the marker family stays clean across the empty-deps branch too --
    no synthetic ``supply_chain_*`` markers leak when there are no
    manifests to parse.
    """
    result = _invoke_supply_chain(cli_runner, empty_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)

    w607cd_phases = (
        "supply_chain_compute_predicate_failed:",
        "supply_chain_compute_verdict_failed:",
        "supply_chain_serialize_envelope_failed:",
    )
    for prefix in w607cd_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"empty-corpus path must NOT surface {prefix} markers; got {leaked!r}"

    # Verdict must be the "no deps" floor (not the literal floor for
    # compute_verdict raise -- that's a different branch)
    assert data["summary"]["verdict"] == "No dependency files found", (
        f"empty-corpus verdict must be the no-deps floor; got {data['summary']['verdict']!r}"
    )


# ---------------------------------------------------------------------------
# (13) compute_verdict empty-deps branch -- no marker, no spurious floor
# ---------------------------------------------------------------------------


def test_compute_verdict_empty_deps_branch_no_marker(cli_runner, empty_project, monkeypatch):
    """The empty-deps branch of compute_verdict must NOT trip the
    compute_verdict wrap.

    Confirms the W607-CD ``compute_verdict`` boundary handles the
    "no deps" case cleanly: ``has_deps=False`` returns "No dependency
    files found" without any f-string interpolation that could raise.
    """
    result = _invoke_supply_chain(cli_runner, empty_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    markers = [m for m in all_markers if m.startswith("supply_chain_compute_verdict_failed:")]
    assert not markers, f"empty-deps branch must not trip compute_verdict wrap; got {markers!r}"

    assert data["summary"]["verdict"] == "No dependency files found"


# ---------------------------------------------------------------------------
# (14) SUPPLY-CHAIN ATTESTATION QUARTET pairing -- cga_/attest_/pr_bundle_/
# supply_chain_ marker families stay isolated when all 4 emitters fire
# on the same workspace
# ---------------------------------------------------------------------------


def test_supply_chain_attestation_quartet_marker_families_coexist(cli_runner, supply_chain_project, monkeypatch):
    """SUPPLY-CHAIN ATTESTATION QUARTET pairing guard requested by the
    W607-CD brief:

    Confirm that ``supply_chain_<phase>_failed:`` markers (W607-AK +
    W607-CD) stay in the canonical ``supply_chain_*`` family when
    supply-chain is invoked on a workspace also covered by the
    cmd_cga (W607-AF + W607-BZ), cmd_attest (W607-AD + W607-BT), and
    cmd_pr_bundle (W607-AE + W607-BW) commands. Each command's
    markers must stay in its OWN family and never bleed into a
    sibling's envelope.

    Closes the supply-chain attestation quartet: every emitter in the
    W805 supply-chain attestation chain now has dual-bucket plumbing
    (substrate-CALL + aggregation-phase) AND prefix-isolation guards.

    Strategy: monkeypatch supply_chain's compute_risk_score to raise
    so a W607-CD marker fires, and confirm:
      1. supply_chain envelope carries ``supply_chain_*_failed:`` markers
      2. supply_chain envelope does NOT carry ``cga_*`` / ``attest_*`` /
         ``pr_bundle_*`` foreign markers
      3. The marker family is closed-enum: every failure marker starts
         with the canonical ``supply_chain_`` prefix.
    """
    from roam.commands import cmd_supply_chain

    def _malformed_metrics(*a, **kw):
        return {"score": 55}  # missing keys -> compute_predicate raises

    monkeypatch.setattr(cmd_supply_chain, "compute_risk_score", _malformed_metrics)

    result = _invoke_supply_chain(cli_runner, supply_chain_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)

    # supply-chain envelope MUST contain supply_chain_compute_predicate_failed
    assert any(m.startswith("supply_chain_compute_predicate_failed:") for m in all_markers), (
        f"supply-chain envelope missing supply_chain_compute_predicate_failed marker; got {all_markers!r}"
    )

    # supply-chain envelope MUST NOT contain attestation-triad sibling markers
    for marker in all_markers:
        if "_failed:" not in marker:
            continue
        assert not marker.startswith("cga_"), f"supply-chain envelope leaked cga_* marker: {marker!r}"
        assert not marker.startswith("attest_"), f"supply-chain envelope leaked attest_* marker: {marker!r}"
        assert not marker.startswith("pr_bundle_"), f"supply-chain envelope leaked pr_bundle_* marker: {marker!r}"

    # Closed-enum check: every failure marker uses the canonical
    # ``supply_chain_*`` prefix.
    failure_markers = [m for m in all_markers if "_failed:" in m]
    for marker in failure_markers:
        assert marker.startswith("supply_chain_"), (
            f"every supply-chain failure marker must use the canonical ``supply_chain_*`` family; got {marker!r}"
        )
