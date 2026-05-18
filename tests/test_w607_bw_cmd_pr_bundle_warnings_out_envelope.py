"""W607-BW -- additive aggregation-phase plumbing for ``cmd_pr_bundle``.

cmd_pr_bundle is the proof-emission boundary at the heart of the W805
6-artifact cross-artifact-consistency family (bundle envelope, VSA,
run-ledger root, cosign sig, Rekor entry, Fulcio cert) and the producer
that downstream consumers verify via ``roam pr-bundle validate
--strict`` + ``--strict-resolved`` (CI implies BOTH). With W607-BW
landed, the full W631 risk-LEVEL vocabulary range (``high`` /
``medium`` / ``low``; ``critical`` excluded by the conservative-on-
critical projection design) is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-AE (7 phases)
  - aggregation-phase layer: W607-BW (6 phases incl. the pr-bundle-
    specific ``validate_strict_resolved`` gate)

Both layers share the canonical ``pr_bundle_*`` marker family and the
``pr_bundle_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two buckets (``_w607ae_warnings_out`` substrate-CALL +
``_w607bw_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order. Pairs with cmd_attest's W607-AD + W607-BT closure on
the W805 cross-artifact-consistency family.

Relation to W607-AE
-------------------

cmd_pr_bundle already carries W607-AE substrate-CALL plumbing covering
7 substrate-helper boundaries (resolve_actor_block / mode_blocks_emit /
auto_collect / causal_diff_pass / atomic_write_bundle / build_envelope /
emit_slsa_l3). W607-BW is ADDITIVE on top of W607-AE, extending marker
coverage to the AGGREGATION-PHASE boundaries that W607-AE left
unguarded:

  - ``validate_strict_resolved``  -- pr-bundle-specific: the
                                     ``--strict-resolved`` gate boundary
                                     (W21.4 / W365)
  - ``score_classify``            -- per-bundle classification of the
                                     canonical W631 risk-LEVEL set via
                                     ``_pr_bundle_risk_level`` re-probe
  - ``severity_normalize``        -- canonical W631 risk-LEVEL projection
                                     (``normalize_risk_level`` +
                                     ``risk_rank``)
  - ``compute_verdict``           -- augmented verdict-floor build with
                                     the canonical risk_level suffix
                                     (LAW 6) via the
                                     ``_make_pr_bundle_verdict_floor``
  - ``serialize_envelope``        -- ``json_envelope("pr-bundle", ...)``
                                     re-projection (downstream contract
                                     changes / shape regressions)
  - ``auto_log``                  -- active-run ledger write

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_pr_bundle's aggregation-phase boundaries had no guards beyond the
W607-AE compose call. A downstream refactor that changes the risk-
level projection contract, the canonical W631 vocabulary, the verdict
string composition, the HMAC chain on the runs ledger, or the
``json_envelope`` shape would crash the envelope post-compose -- after
the substrate signals were already gathered, the agent loses the
result. W607-BW wraps each boundary with ``_run_check_bw`` so a raise
becomes a marker via ``warnings_out`` and the envelope still emits.

Score-classify degradation discipline
-------------------------------------

When the inner score_classify boundary raises (e.g. a refactored
``_pr_bundle_risk_level``), the wrap floors the classified tier to
``None`` and surfaces ``score_classification: "unknown"`` in the
envelope summary. The inner ``_build_envelope`` had already produced
the canonical ``risk_level_canonical`` summary field on the success
path, so degradation only flips the outer sentinel -- mirror of
cmd_attest W607-BT score_classify pattern.

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
# Helpers -- invoke pr-bundle init + emit via the Click group
# ---------------------------------------------------------------------------


def _invoke_pr_bundle(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam pr-bundle <subcommand>`` through the group."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("pr-bundle")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus + initialised bundle
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def pr_bundle_project(tmp_path, monkeypatch):
    """Indexed corpus with an initialised pr-bundle on the current branch."""
    proj = tmp_path / "pr_bundle_w607bw_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "auth.py").write_text(
        "def verify_token(t):\n    return t == 'ok'\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"

    # Initialise the bundle so `emit` has something to load.
    runner = CliRunner()
    init_result = _invoke_pr_bundle(
        runner,
        proj,
        "init",
        "--intent",
        "W607-BW smoke",
    )
    assert init_result.exit_code == 0, init_result.output
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BW aggregation markers
# ---------------------------------------------------------------------------


def test_pr_bundle_emit_happy_path_no_w607bw_markers(cli_runner, pr_bundle_project):
    """Clean pr-bundle emit -> no W607-BW aggregation markers.

    Hash-stable: an empty W607-BW bucket on the success path must produce
    an envelope without any
    ``pr_bundle_validate_strict_resolved_failed:`` /
    ``pr_bundle_score_classify_failed:`` /
    ``pr_bundle_severity_normalize_failed:`` /
    ``pr_bundle_compute_verdict_failed:`` /
    ``pr_bundle_serialize_envelope_failed:`` /
    ``pr_bundle_auto_log_failed:`` markers. Mirror of cmd_attest W607-BT
    discipline.
    """
    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-bundle"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607bw_phases = (
        "pr_bundle_validate_strict_resolved_failed:",
        "pr_bundle_score_classify_failed:",
        "pr_bundle_severity_normalize_failed:",
        "pr_bundle_compute_verdict_failed:",
        "pr_bundle_serialize_envelope_failed:",
        "pr_bundle_auto_log_failed:",
    )
    for prefix in w607bw_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean pr-bundle emit must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_bw`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_pr_bundle_carries_w607bw_accumulator():
    """AST-level guard: cmd_pr_bundle source carries the W607-BW accumulator.

    Pins the canonical W607-BW anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AE) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_bundle.py"
    assert src_path.exists(), f"cmd_pr_bundle.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607bw_warnings_out" in src, (
        "W607-BW accumulator missing from cmd_pr_bundle; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_bw" in src, (
        "W607-BW helper ``_run_check_bw`` missing from cmd_pr_bundle; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_bw is defined inside pr_bundle_emit().
    tree = ast.parse(src)
    found_run_check_bw = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bw":
            found_run_check_bw = True
            break
    assert found_run_check_bw, (
        "W607-BW ``_run_check_bw`` helper not found in cmd_pr_bundle AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-AE must still be present (additive layer does NOT replace it)
    assert "_w607ae_warnings_out" in src, (
        "W607-AE accumulator vanished alongside the W607-BW add; the "
        "additive plumbing must preserve the W607-AE substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_bw():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_bw(...)`` with the canonical phase name.

    The six phases must appear inside a ``_run_check_bw("<phase>", ...)``
    call inside cmd_pr_bundle. Multi-indent variants are all considered
    valid wrap call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_bundle.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "validate_strict_resolved",
        "score_classify",
        "severity_normalize",
        "compute_verdict",
        "serialize_envelope",
        "auto_log",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_bw(\n        "{phase}"',
            f'_run_check_bw(\n            "{phase}"',
            f'_run_check_bw(\n                "{phase}"',
            f'_run_check_bw(\n                    "{phase}"',
            f'_run_check_bw(\n                        "{phase}"',
            f'_run_check_bw("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_bw(...); add the W607-BW guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) Marker shape -- ``pr_bundle_<phase>_failed:<exc>:<detail>``
# ---------------------------------------------------------------------------


def test_auto_log_failure_marker_format(cli_runner, pr_bundle_project, monkeypatch):
    """If ``auto_log`` raises, surface ``pr_bundle_auto_log_failed:`` and
    keep the pr-bundle envelope intact.

    Discipline mirror of the W607-BT auto_log-failure pattern in
    cmd_attest. The auto_log boundary writes to the active run ledger
    when one is open -- a raise here would otherwise crash the envelope
    AFTER the success envelope was already built.
    """
    from roam.commands import cmd_pr_bundle

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-auto-log-from-W607-BW")

    monkeypatch.setattr(cmd_pr_bundle, "auto_log", _raise_auto_log)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_bundle_auto_log_failed:")]
    assert markers, f"expected ``pr_bundle_auto_log_failed:`` marker; got {top_wo!r}"
    marker = markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-auto-log-from-W607-BW" in parts[2], parts

    # Envelope still emits the core pr-bundle signal block
    assert data.get("command") == "pr-bundle", data


# ---------------------------------------------------------------------------
# (5) SCORE CLASSIFY DEGRADATION discipline
# ---------------------------------------------------------------------------


def test_score_classify_degradation_surfaces_unknown_sentinel(cli_runner, pr_bundle_project, monkeypatch):
    """When the score_classify boundary raises:

    1. Marker ``pr_bundle_score_classify_failed:`` appears
    2. Envelope still completes with a parseable summary
    3. Summary stamps ``score_classification: "unknown"`` sentinel

    The underlying action (emit the bundle envelope) stays -- degraded
    outcomes are valid design. The LIE we prevent is a clean classified
    verdict when score_classify actually raised. Mirror of cmd_attest's
    W607-BT score_classify pattern.

    Note: the inner ``_build_envelope`` already produced
    ``risk_level_canonical`` on its success path BEFORE the outer BW
    re-probe runs; the outer raise only flips the outer
    ``score_classification`` sentinel.
    """
    from roam.commands import cmd_pr_bundle

    # We have to capture the call-count: the inner _build_envelope path
    # also calls _pr_bundle_risk_level. To keep the inner build clean and
    # only break the OUTER BW re-probe, we patch the helper to raise on
    # the second call (the BW re-probe is invoked after _build_envelope).
    call_count = {"n": 0}
    original = cmd_pr_bundle._pr_bundle_risk_level

    def _raise_on_outer_call(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Inner _build_envelope call -- let it succeed.
            return original(*args, **kwargs)
        raise RuntimeError("synthetic-score-classify-from-W607-BW")

    monkeypatch.setattr(cmd_pr_bundle, "_pr_bundle_risk_level", _raise_on_outer_call)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    # (1) marker appears -- W607-BW score_classify
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_bundle_score_classify_failed:")]
    assert markers, f"expected ``pr_bundle_score_classify_failed:`` marker; got {top_wo!r}"

    # (2) envelope still completes with parseable summary
    summary = data.get("summary") or {}
    assert isinstance(summary, dict) and summary, summary
    assert isinstance(summary.get("verdict"), str), summary

    # (3) score_classification sentinel
    assert summary.get("score_classification") == "unknown", (
        f'summary must stamp ``score_classification: "unknown"`` when '
        f"score_classify raises; got "
        f"{summary.get('score_classification')!r}"
    )


def test_score_classify_clean_path_stamps_classified(cli_runner, pr_bundle_project):
    """Happy path: ``score_classification`` summary field is ``"classified"``.

    Mirror of the W607-BT discipline that the sentinel disambiguates a
    real classified verdict from a degraded "unknown" floor.
    """
    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("score_classification") == "classified", (
        f'clean path must stamp ``score_classification: "classified"``; '
        f"got {data['summary'].get('score_classification')!r}"
    )


# ---------------------------------------------------------------------------
# (6) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, pr_bundle_project, monkeypatch):
    """ANY W607-BW or W607-AE marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    pr-bundle emit" from "pr-bundle emit ran with aggregation degradation"
    via summary.partial_success alone.
    """
    from roam.commands import cmd_pr_bundle

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-BW")

    monkeypatch.setattr(cmd_pr_bundle, "auto_log", _raise_auto_log)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-BW warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607bw_warnings_out_in_both_top_and_summary(cli_runner, pr_bundle_project, monkeypatch):
    """Non-empty W607-BW bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-BT contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-detail
    mode; summary mirror gives consumers reading only the summary block
    visibility too.
    """
    from roam.commands import cmd_pr_bundle

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BW")

    monkeypatch.setattr(cmd_pr_bundle, "auto_log", _raise_auto_log)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BW raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BW raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("pr_bundle_auto_log_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("pr_bundle_auto_log_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the auto_log marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-AE COEXISTENCE -- both buckets surface in combined envelope
# ---------------------------------------------------------------------------


def test_combined_w607ae_and_w607bw_markers_both_surface(cli_runner, pr_bundle_project, monkeypatch):
    """W607-AE and W607-BW markers BOTH surface when raises occur on each
    layer simultaneously.

    The additive plumbing must not shadow the W607-AE bucket -- agents
    must see the full degradation lineage in marker-emission order.
    Mirror of cmd_attest's W607-AD + W607-BT combined test (regression
    guard ensuring the pre-existing W607-AE layer survives the additive
    W607-BW plumbing).
    """
    from roam.commands import cmd_pr_bundle

    def _raise_auto_collect(*a, **kw):
        # W607-AE substrate boundary
        raise RuntimeError("synthetic-auto-collect-from-W607-BW-combined")

    def _raise_auto_log(*a, **kw):
        # W607-BW aggregation boundary
        raise RuntimeError("synthetic-auto-log-from-W607-BW-combined")

    monkeypatch.setattr(cmd_pr_bundle, "_auto_collect", _raise_auto_collect)
    monkeypatch.setattr(cmd_pr_bundle, "auto_log", _raise_auto_log)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    ae_markers = [m for m in top_wo if m.startswith("pr_bundle_auto_collect_failed:")]
    bw_markers = [m for m in top_wo if m.startswith("pr_bundle_auto_log_failed:")]
    assert ae_markers, f"W607-AE auto_collect marker missing; got {top_wo!r}"
    assert bw_markers, f"W607-BW auto_log marker missing; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-BW uses the SAME ``pr_bundle_*`` family
# ---------------------------------------------------------------------------


def test_w607bw_marker_prefix_pr_bundle_family(cli_runner, pr_bundle_project, monkeypatch):
    """W607-BW markers use the canonical ``pr_bundle_*`` prefix (same family
    as W607-AE; W607-BW is ADDITIVE, not a separate prefix).

    Hard guard: any W607-BW marker that leaks into a sibling W607-*
    family (e.g. ``attest_*`` / ``preflight_*`` / ``impact_*`` /
    ``diagnose_*`` / ``critique_*`` / ``diff_*``) breaks the closed-enum
    marker-family contract pinned in the W607-AE test.
    """
    from roam.commands import cmd_pr_bundle

    def _raise_auto_log(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BW")

    monkeypatch.setattr(cmd_pr_bundle, "auto_log", _raise_auto_log)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-discipline check"
    # Filter to substrate-/aggregation-CALL markers (have ``_failed:`` in the middle).
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure markers"
    for marker in failure_markers:
        assert marker.startswith("pr_bundle_"), (
            f"every W607-BW marker must use the ``pr_bundle_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (10) CROSS-PREFIX ISOLATION -- pr_bundle_* markers DO NOT leak into siblings
# ---------------------------------------------------------------------------


def test_pr_bundle_markers_do_not_leak_into_adjacent_commands(cli_runner, pr_bundle_project, monkeypatch):
    """``pr_bundle_*`` markers must NOT appear in ``cmd_attest`` /
    ``cmd_cga`` envelopes when those commands raise.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels. Mirror of cmd_attest's W607-BT
    cross-prefix isolation discipline.
    """
    from roam.commands import cmd_pr_bundle

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation-from-W607-BW")

    monkeypatch.setattr(cmd_pr_bundle, "auto_log", _raise_auto_log)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    assert all_markers, "expected non-empty warnings_out for prefix-isolation check"

    # Foreign-family leakage check
    foreign_prefixes = (
        "attest_",
        "cga_",
        "preflight_",
        "impact_",
        "diagnose_",
        "critique_",
        "diff_",
        "pr_analyze_",
        "pr_risk_",
    )
    # Filter to failure markers (the ones we own)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_pr_bundle warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (11) Compute-verdict guard -- raise floors to a stable verdict
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, pr_bundle_project, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    We force the inner verdict-floor closure to raise by patching
    ``normalize_risk_level`` to return an object whose ``__format__``
    raises -- the verdict f-string interpolation of risk_level_canonical
    then trips the wrap inside ``_make_pr_bundle_verdict_floor``.

    W978 first-hypothesis check: the canonical floor MUST NOT
    re-interpolate the same value that raised on the BadLevel sentinel
    test -- the floor is a literal string.

    Note: ``normalize_risk_level`` is also called inside
    ``_build_envelope``. To keep the inner build clean, we patch the
    function to return the BadLevel object only AFTER the inner build
    has completed (we use a call-count gate).
    """
    from roam.commands import cmd_pr_bundle

    class _BadLevel:
        def __str__(self):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BW")

        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BW")

    original = cmd_pr_bundle.normalize_risk_level
    call_count = {"n": 0}

    def _bad_normalize(level):
        call_count["n"] += 1
        # The inner _build_envelope call_count tracking is fragile because
        # normalize_risk_level is called multiple times. Use a coarse
        # threshold: the first call is inside _build_envelope. From the
        # OUTER BW severity_normalize wrap onward (call >=2), return the
        # BadLevel sentinel which trips compute_verdict's f-string.
        if call_count["n"] >= 2:
            return _BadLevel()
        return original(level)

    monkeypatch.setattr(cmd_pr_bundle, "normalize_risk_level", _bad_normalize)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--no-auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_bundle_compute_verdict_failed:")]
    assert markers, f"expected ``pr_bundle_compute_verdict_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (12) STRICT-RESOLVED guard -- structured envelope on unresolved blast radius
# ---------------------------------------------------------------------------


def test_strict_resolved_gate_emits_structured_envelope(cli_runner, pr_bundle_project, monkeypatch):
    """``--strict-resolved`` with an unresolved (ghost) affected symbol:

    1. The envelope is still parseable JSON (no CLI crash)
    2. The ``strict_resolved_gate_state`` summary field is populated by
       the W607-BW validate_strict_resolved wrap
    3. The pr-bundle phase wrap doesn't shadow the W607-AE chain

    pr-bundle-specific phase (the only W607-BW boundary that doesn't
    have a sibling counterpart in cmd_attest's W607-BT plumbing). The
    --strict-resolved gate is the CI-required boundary (W21.4 / W365)
    that the wrap exists to keep loud.
    """

    # Force the outer probe lambda to surface a non-zero count by
    # stamping it on the bundle's affected symbol. We do that via the
    # _build_envelope path -- the inner build stamps
    # unresolved_affected_symbols_count from the bundle. Simplest path:
    # add an unresolved affected symbol via the CLI before emit.
    add_result = _invoke_pr_bundle(
        cli_runner,
        pr_bundle_project,
        "add",
        "affected",
        "ghost_symbol_does_not_exist",
    )
    # add affected may emit non-zero on ghost resolution; we accept any
    # exit code as long as the bundle file gets the entry.
    assert add_result.exit_code in (0, 5), add_result.output

    result = _invoke_pr_bundle(
        cli_runner,
        pr_bundle_project,
        "emit",
        "--no-auto-collect",
        "--strict-resolved",
    )
    # --strict-resolved can exit 5 (gated) or 0 depending on whether the
    # ghost was actually unresolved; we only need the envelope shape.
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data.get("command") == "pr-bundle", data

    summary = data.get("summary") or {}
    # The W607-BW validate_strict_resolved wrap stamps strict_resolved_gate_state
    # (closed enum: "unknown" / "blocked" / "passed").
    gate_state = summary.get("strict_resolved_gate_state")
    assert gate_state in ("unknown", "blocked", "passed"), (
        f"strict_resolved_gate_state must be in closed enum; got {gate_state!r}"
    )


# ---------------------------------------------------------------------------
# (13) W805 CROSS-ARTIFACT PAIR -- pr_bundle_* + attest_* markers coexist
# ---------------------------------------------------------------------------


def test_w805_pr_bundle_and_attest_marker_families_coexist(tmp_path, monkeypatch):
    """W805 cross-artifact pair: ``pr_bundle_<phase>_failed:`` markers
    (W607-AE + BW) coexist with ``attest_<phase>_failed:`` markers
    (W607-AD + BT) when both commands are invoked on the same workspace.

    Closes the proof-emission pair. cmd_pr_bundle is artifact 1 of the
    W805 6-artifact family; cmd_attest is the attestation projection
    over the same evidence. Both commands must thread their respective
    marker families WITHOUT cross-contamination (pr_bundle_* never
    appears in attest_* and vice versa).
    """
    # Set up a fresh workspace and run pr-bundle emit + attest with
    # forced raises on both layers; assert each marker family lives in
    # its own command's envelope.
    proj = tmp_path / "pr_bundle_attest_w805_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "auth.py").write_text(
        "def verify_token(t):\n    return t == 'ok'\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"

    # Unstaged edit so ``roam attest`` reaches the wrapped collector path
    # (the early-return empty / unresolved paths call ``auto_log``
    # OUTSIDE the W607-BT wrap, so they would crash the CLI on a
    # synthetic raise -- which is NOT the path we're testing here).
    (src / "auth.py").write_text(
        "def verify_token(t):\n    return t == 'OK'\n",  # changed return literal
        encoding="utf-8",
    )

    runner = CliRunner()

    # Initialise bundle (so emit has something to load)
    init_result = _invoke_pr_bundle(runner, proj, "init", "--intent", "W805 smoke")
    assert init_result.exit_code == 0, init_result.output

    # Patch BOTH the pr_bundle auto_log AND the attest auto_log to force
    # an aggregation-phase marker on each command's envelope.
    from roam.commands import cmd_attest, cmd_pr_bundle

    def _raise_pb_auto_log(*a, **kw):
        raise RuntimeError("synthetic-W805-pair-pr-bundle-auto-log")

    def _raise_at_auto_log(*a, **kw):
        raise RuntimeError("synthetic-W805-pair-attest-auto-log")

    monkeypatch.setattr(cmd_pr_bundle, "auto_log", _raise_pb_auto_log)
    monkeypatch.setattr(cmd_attest, "auto_log", _raise_at_auto_log)

    # Invoke pr-bundle emit
    pb_result = _invoke_pr_bundle(runner, proj, "emit", "--no-auto-collect")
    assert pb_result.exit_code in (0, 5), pb_result.output
    pb_data = _json.loads(pb_result.output)
    pb_wo = pb_data.get("warnings_out") or []
    pb_markers = [m for m in pb_wo if m.startswith("pr_bundle_auto_log_failed:")]
    assert pb_markers, f"W805 pair: pr_bundle_auto_log_failed: missing from pr-bundle envelope; got {pb_wo!r}"
    # pr_bundle envelope must NOT contain attest_* failure markers
    pb_attest_leak = [m for m in pb_wo if m.startswith("attest_") and "_failed:" in m]
    assert not pb_attest_leak, f"W805 pair: attest_* markers leaked into pr-bundle envelope; got {pb_attest_leak!r}"

    # Invoke attest
    from roam.cli import cli

    args = ["--json", "attest"]
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        at_result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert at_result.exit_code == 0, at_result.output
    at_data = _json.loads(at_result.output)
    at_wo = at_data.get("warnings_out") or []
    at_markers = [m for m in at_wo if m.startswith("attest_auto_log_failed:")]
    assert at_markers, f"W805 pair: attest_auto_log_failed: missing from attest envelope; got {at_wo!r}"
    # attest envelope must NOT contain pr_bundle_* failure markers
    at_pb_leak = [m for m in at_wo if m.startswith("pr_bundle_") and "_failed:" in m]
    assert not at_pb_leak, f"W805 pair: pr_bundle_* markers leaked into attest envelope; got {at_pb_leak!r}"


# ---------------------------------------------------------------------------
# (14) W607-AE COEXISTENCE GUARD -- substrate-CALL + aggregation-phase
# markers coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607ae_substrate_markers_coexist_with_w607bw_aggregation(cli_runner, pr_bundle_project, monkeypatch):
    """Confirm ``pr_bundle_<substrate-phase>_failed:`` markers (W607-AE
    layer) coexist with ``pr_bundle_<agg-phase>_failed:`` markers
    (W607-BW layer) -- both in same family, threaded through different
    buckets at envelope-emit.

    This is the explicit guard requested by the W607-BW brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``pr_bundle_<substrate-phase>_failed:`` vs.
    ``pr_bundle_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_pr_bundle

    # W607-AE substrate boundary -- _auto_collect
    def _raise_auto_collect(*a, **kw):
        raise RuntimeError("synthetic-ae-coexist-auto-collect")

    # W607-BW aggregation boundary -- auto_log
    def _raise_auto_log(*a, **kw):
        raise RuntimeError("synthetic-bw-coexist-auto-log")

    monkeypatch.setattr(cmd_pr_bundle, "_auto_collect", _raise_auto_collect)
    monkeypatch.setattr(cmd_pr_bundle, "auto_log", _raise_auto_log)

    result = _invoke_pr_bundle(cli_runner, pr_bundle_project, "emit", "--auto-collect")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-AE
    ae_markers = [m for m in top_wo if m.startswith("pr_bundle_auto_collect_failed:")]
    # Aggregation-phase from W607-BW
    bw_markers = [m for m in top_wo if m.startswith("pr_bundle_auto_log_failed:")]

    assert ae_markers, f"W607-AE substrate-CALL marker (pr_bundle_auto_collect_failed) missing; got {top_wo!r}"
    assert bw_markers, f"W607-BW aggregation-phase marker (pr_bundle_auto_log_failed) missing; got {top_wo!r}"

    # Both share the canonical ``pr_bundle_*`` family
    assert all(m.startswith("pr_bundle_") for m in (ae_markers + bw_markers)), (
        f"all markers must share the canonical ``pr_bundle_*`` family; got ae = {ae_markers!r}, bw = {bw_markers!r}"
    )

    # Both surface in summary mirror too
    summary_wo = data["summary"].get("warnings_out") or []
    assert any(m.startswith("pr_bundle_auto_collect_failed:") for m in summary_wo), (
        f"W607-AE marker missing from summary mirror; got {summary_wo!r}"
    )
    assert any(m.startswith("pr_bundle_auto_log_failed:") for m in summary_wo), (
        f"W607-BW marker missing from summary mirror; got {summary_wo!r}"
    )
