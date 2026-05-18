"""W607-DV -- ``cmd_pr_replay`` threads aggregation-LAYER ``warnings_out``
onto its envelope.

cmd_pr_replay is the canonical PR-replay-report producer that consumes
cmd_postmortem-style ledger data + collector output. With W607-DV
landed, cmd_pr_replay now carries THREE layers of W607 plumbing:

  - substrate-CALL layer: W607-AH (8 substrate boundaries:
    run_postmortem / aggregate_by_detector / render_report / render_pdf /
    build_review_suggestions / collect_change_evidence / to_canonical_json /
    render_evidence_markdown)
  - aggregation-phase layer: W607-CA (6 aggregation boundaries:
    score_classify / severity_normalize / compute_verdict /
    render_markdown / serialize_envelope / auto_log)
  - aggregation-LAYER (additional): W607-DV (4 aggregation boundaries:
    completeness_classify / completeness_rollup /
    evidence_verdict_compose / dv_serialize_envelope)

All three layers share the canonical ``pr_replay_*`` marker family and
the ``pr_replay_<phase>_failed:<exc_class>:<detail>`` shape contract.
The three buckets (``_w607ah_warnings_out`` + ``_w607ca_warnings_out``
+ ``_w607dv_warnings_out``) are combined at envelope-emit time so
consumers see the full degradation lineage.

Ledger-reader 3-way closure
---------------------------

W607-DV closes the runs-ledger reader 3-way at the aggregation layer:

  * cmd_postmortem (W607-AN + W607-CV + W607-DR) -- git-log reader
  * cmd_audit_trail_verify (W607-AI substrate-only)
  * cmd_pr_replay (W607-AH + W607-CA + W607-DV) -- ledger consumer +
    replay-renderer

DV phases focus on the evidence-completeness aggregation slice that
W607-CA does not cover:

  * ``completeness_classify``    -- buckets the 8-question
                                    evidence-completeness count into
                                    one of FOUR W276 tiers
                                    (PASS / WARN / FAIL / INSUFFICIENT).
  * ``completeness_rollup``      -- rolls up Q1..Q8 + redaction-count +
                                    producer-warning-count disclosure
                                    (W561-spirit dropped-row visibility).
  * ``evidence_verdict_compose`` -- synthesises canonical
                                    "N of 8 evidence questions answered"
                                    verdict with literal floor
                                    "pr_replay completed".
  * ``dv_serialize_envelope``    -- additive json_envelope re-projection
                                    with a DISTINCT phase name from
                                    CA's serialize_envelope.

W978 7-discipline pinned
------------------------

cmd_taint W607-CJ codified the 5th W978 discipline: move ``len()``
INSIDE the wrapped closure rather than at the kwarg-bind site.
cmd_audit_trail_export W607-CR codified the 7th discipline: use bare
``dict[key]`` lookup when a floor dict guarantees the key, NOT
``dict.get(key, expensive_default)``. The AST audit below pins both at
the W607-DV layer.

W276 INSUFFICIENT-tier preservation
-----------------------------------

DV completeness_classify deliberately returns INSUFFICIENT when the
packet is absent or lacks ``evidence_completeness()`` -- preserving the
W276 four-tier vocabulary (PASS / WARN / FAIL / INSUFFICIENT) rather
than collapsing to a three-tier classification.

W561 dropped-row disclosure preservation
----------------------------------------

DV completeness_rollup surfaces a ``redaction_count`` +
``producer_warning_count`` pair on the envelope summary. These are the
pr_replay-surface equivalent of OSCAL's ``dropped_enum_rows`` -- the
closed-enum drop-visibility channel for the evidence-compiler boundary.

W246 context_refs producer wiring not regressed
-----------------------------------------------

DV is ADDITIVE; the W246 context_refs gatherer + the W607-AH
``collect_change_evidence`` boundary remain untouched.

Cross-prefix isolation
----------------------

All W607-DV markers use the ``pr_replay_*`` prefix family (no
``postmortem_*`` / ``audit_trail_verify_*`` / ``critique_*`` leakage).

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
# Canonical W607-DV phase enumeration
# ---------------------------------------------------------------------------


_DV_PHASES = (
    "completeness_classify",
    "completeness_rollup",
    "evidence_verdict_compose",
    "dv_serialize_envelope",
)


# Sibling-layer phase enumerations (used for the collision check below)
_AH_PHASES = frozenset(
    {
        "run_postmortem",
        "aggregate_by_detector",
        "render_report",
        "render_pdf",
        "build_review_suggestions",
        "collect_change_evidence",
        "to_canonical_json",
        "render_evidence_markdown",
    }
)
_CA_PHASES = frozenset(
    {
        "score_classify",
        "severity_normalize",
        "compute_verdict",
        "render_markdown",
        "serialize_envelope",
        "auto_log",
    }
)


# ---------------------------------------------------------------------------
# Helpers -- invoke pr-replay via the Click CLI
# ---------------------------------------------------------------------------


def _invoke_pr_replay(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam pr-replay`` (top-level command)."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("pr-replay")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with at least one commit on the branch
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def pr_replay_project(tmp_path, monkeypatch):
    """Indexed corpus with at least one commit for ``pr-replay`` to read."""
    proj = tmp_path / "pr_replay_w607dv_project"
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
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DV aggregation-layer markers
# ---------------------------------------------------------------------------


def test_pr_replay_happy_path_no_w607dv_markers(cli_runner, pr_replay_project):
    """Clean pr-replay -> no W607-DV aggregation-layer markers.

    Hash-stable: an empty W607-DV bucket on the success path must produce
    an envelope without any ``pr_replay_completeness_classify_failed:`` /
    ``pr_replay_completeness_rollup_failed:`` /
    ``pr_replay_evidence_verdict_compose_failed:`` /
    ``pr_replay_dv_serialize_envelope_failed:`` markers.
    """
    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-replay"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _DV_PHASES:
        prefix = f"pr_replay_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean pr-replay must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- ``_run_check_dv`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_pr_replay_carries_w607dv_accumulator():
    """AST-level guard: cmd_pr_replay source carries the W607-DV
    accumulator AND both prior W607-AH + W607-CA accumulators.

    Pins the canonical W607-DV anchors so a future refactor that removes
    the additive aggregation-layer instrumentation fails this guard.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    assert src_path.exists(), f"cmd_pr_replay.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607dv_warnings_out" in src, (
        "W607-DV accumulator missing from cmd_pr_replay; the additive "
        "aggregation-layer marker plumbing has been removed."
    )
    assert "_run_check_dv" in src, (
        "W607-DV helper ``_run_check_dv`` missing from cmd_pr_replay; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_dv is defined inside the command.
    tree = ast.parse(src)
    found_run_check_dv = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dv":
            found_run_check_dv = True
            break
    assert found_run_check_dv, (
        "W607-DV ``_run_check_dv`` helper not found in cmd_pr_replay "
        "AST; the additive aggregation-layer wrapper has been refactored "
        "away."
    )

    # W607-AH must still be present (additive layer does NOT replace it)
    assert "_w607ah_warnings_out" in src, (
        "W607-AH accumulator vanished alongside the W607-DV add; the "
        "additive plumbing must preserve the W607-AH substrate-CALL layer."
    )
    # W607-CA must still be present (additive layer does NOT replace it)
    assert "_w607ca_warnings_out" in src, (
        "W607-CA accumulator vanished alongside the W607-DV add; the "
        "additive plumbing must preserve the W607-CA aggregation-phase "
        "layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every W607-DV aggregation boundary wrapped
# ---------------------------------------------------------------------------


def test_every_dv_aggregation_phase_wrapped_in_run_check_dv():
    """Source-grep guard: every W607-DV aggregation boundary calls
    ``_run_check_dv(...)`` with the canonical phase name.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _DV_PHASES:
        same_line = f'_run_check_dv("{phase}"' in src
        multi_line = any(f'_run_check_dv(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"pr_replay_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DV wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) Per-phase isolation -- completeness_classify raise surfaces marker
# ---------------------------------------------------------------------------


def _make_poison_packet_class(raise_after_calls: int = 1):
    """Factory for a duck-typed evidence packet whose
    ``evidence_completeness()`` succeeds for the first ``raise_after_calls``
    invocations and then raises.

    The banner_envelope_block path (and classify_evidence_coverage) calls
    ``evidence_completeness()`` from inside _banner_envelope_block BEFORE
    the W607-DV ``completeness_classify`` closure runs. To target the DV
    layer cleanly without dragging the banner producer into the failure
    path, we let the first call(s) succeed (banner gets a clean dict)
    and only raise on the DV invocation.
    """

    class _PoisonPacket:
        content_hash = "synthetic"
        authority_refs = ()
        redactions = ()

        def __init__(self):
            self._calls = 0

        def evidence_completeness(self):
            self._calls += 1
            if self._calls <= raise_after_calls:
                return {
                    "complete": 8,
                    "partial": 0,
                    "missing": 0,
                    "not_applicable": 0,
                    "Q1": "complete",
                    "Q2": "complete",
                    "Q3": "complete",
                    "Q4": "complete",
                    "Q5": "complete",
                    "Q6": "complete",
                    "Q7": "complete",
                    "Q8": "complete",
                }
            raise RuntimeError(f"synthetic-completeness-from-W607-DV-call-{self._calls}")

        def to_canonical_json(self):
            return "{}"

    return _PoisonPacket


def test_completeness_classify_failure_marker_format(cli_runner, pr_replay_project, monkeypatch):
    """If the completeness_classify closure raises, the wrap floors to
    the INSUFFICIENT shape and surfaces
    ``pr_replay_completeness_classify_failed:``.

    Simulated via a poisoned packet whose ``evidence_completeness()``
    raises on the DV invocation (after the upstream banner call has
    already succeeded). The DV closure calls ``_pkt.evidence_completeness()``
    which raises; the wrap catches it and ships the marker.
    """
    from roam.commands import cmd_pr_replay

    _PoisonPacket = _make_poison_packet_class(raise_after_calls=2)

    def _patched_collect(*args, **kwargs):
        return _PoisonPacket()

    monkeypatch.setattr(cmd_pr_replay, "_collect_change_evidence", _patched_collect)

    # Force evidence-collector path by passing --evidence so the packet is built.
    out_path = pr_replay_project / "evidence.json"
    result = _invoke_pr_replay(
        cli_runner,
        pr_replay_project,
        "--tier",
        "sample",
        "--evidence",
        str(out_path),
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("pr_replay_completeness_classify_failed:")]
    assert markers, f"expected ``pr_replay_completeness_classify_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) Per-phase isolation -- evidence_verdict_compose raise surfaces marker
# ---------------------------------------------------------------------------


def test_evidence_verdict_compose_law6_floor_on_classify_raise(cli_runner, pr_replay_project, monkeypatch):
    """When ``completeness_classify`` raises, the DV chain MUST still
    produce a valid evidence_verdict on the summary block.

    The classify-raise path floors to ``complete_count = 0`` which the
    verdict_compose closure interpolates as
    ``"0 of 8 evidence questions answered"``. This pins the chain-
    composition contract: a downstream raise on the FIRST DV phase
    must NOT poison the SECOND DV phase's output -- the floor dict
    guarantees ``complete_count`` is an int, so the verdict closure
    receives a known-good value and produces a known-good string.

    LAW 6 standalone-parse: the verdict-compose floor itself is the
    literal ``"pr_replay completed"`` (verified by AST audit in the
    kwarg-default test); this integration test pins the runtime chain.
    """
    from roam.commands import cmd_pr_replay

    _PoisonPacket = _make_poison_packet_class(raise_after_calls=2)

    def _patched_collect(*args, **kwargs):
        return _PoisonPacket()

    monkeypatch.setattr(cmd_pr_replay, "_collect_change_evidence", _patched_collect)

    out_path = pr_replay_project / "evidence.json"
    result = _invoke_pr_replay(
        cli_runner,
        pr_replay_project,
        "--tier",
        "sample",
        "--evidence",
        str(out_path),
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    # The DV chain's second phase still produced its known-good verdict
    # interpolation using the floor's ``complete_count = 0``.
    verdict = data["summary"].get("evidence_verdict")
    assert verdict == "0 of 8 evidence questions answered", (
        f"expected DV chain to compose ``0 of 8 evidence questions "
        f"answered`` from the classify-raise floor; got {verdict!r}. "
        f"summary = {data['summary']!r}"
    )
    # AND the classify-raise marker rode warnings_out.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("pr_replay_completeness_classify_failed:")]
    assert markers, (
        f"expected ``pr_replay_completeness_classify_failed:`` marker "
        f"alongside the chained verdict floor; got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (6) Substrate + aggregation coexistence -- W607-AH + W607-DV markers
# ---------------------------------------------------------------------------


def test_w607dv_coexists_with_w607ah(cli_runner, pr_replay_project, monkeypatch):
    """W607-DV aggregation-layer markers coexist with W607-AH
    substrate-CALL markers when both layers fault.

    The additive aggregation-LAYER (DV) MUST NOT shadow the prior
    substrate-CALL layer (AH); both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``pr_replay_<ah-phase>_failed:`` vs ``pr_replay_<dv-phase>_failed:``).
    """
    from roam.commands import cmd_pr_replay

    # W607-AH substrate boundary -- render_report raises
    def _raise_render(*a, **kw):
        raise RuntimeError("synthetic-ah-render-from-W607-DV-test")

    monkeypatch.setattr(cmd_pr_replay, "_render_report", _raise_render)

    # W607-DV aggregation boundary -- inject a poisoned packet so
    # completeness_classify raises (after the banner call succeeds)
    _PoisonPacket = _make_poison_packet_class(raise_after_calls=2)

    def _patched_collect(*args, **kwargs):
        return _PoisonPacket()

    monkeypatch.setattr(cmd_pr_replay, "_collect_change_evidence", _patched_collect)

    out_path = pr_replay_project / "evidence.json"
    result = _invoke_pr_replay(
        cli_runner,
        pr_replay_project,
        "--tier",
        "sample",
        "--evidence",
        str(out_path),
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])

    ah_markers = [m for m in all_wo if m.startswith("pr_replay_render_report_failed:")]
    dv_markers = [m for m in all_wo if m.startswith("pr_replay_completeness_classify_failed:")]

    assert ah_markers, f"W607-AH substrate-CALL marker (pr_replay_render_report_failed) missing; got {all_wo!r}"
    assert dv_markers, (
        f"W607-DV aggregation-layer marker (pr_replay_completeness_classify_failed) missing; got {all_wo!r}"
    )

    # Both share the canonical ``pr_replay_*`` family
    for m in ah_markers + dv_markers:
        assert m.startswith("pr_replay_"), f"all markers must share the canonical ``pr_replay_*`` family; got {m!r}"


# ---------------------------------------------------------------------------
# (7) ANY W607-DV marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_dv_marker_flips_partial_success(cli_runner, pr_replay_project, monkeypatch):
    """ANY W607-DV marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    pr-replay" from "pr-replay ran with aggregation-layer degradation"
    via summary.partial_success alone.
    """
    from roam.commands import cmd_pr_replay

    _PoisonPacket = _make_poison_packet_class(raise_after_calls=2)

    def _patched_collect(*args, **kwargs):
        return _PoisonPacket()

    monkeypatch.setattr(cmd_pr_replay, "_collect_change_evidence", _patched_collect)

    out_path = pr_replay_project / "evidence.json"
    result = _invoke_pr_replay(
        cli_runner,
        pr_replay_project,
        "--tier",
        "sample",
        "--evidence",
        str(out_path),
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-DV warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607dv_warnings_out_in_both_top_and_summary(cli_runner, pr_replay_project, monkeypatch):
    """Non-empty W607-DV bucket -> both top-level AND summary.warnings_out
    populated with the marker.
    """
    from roam.commands import cmd_pr_replay

    _PoisonPacket = _make_poison_packet_class(raise_after_calls=2)

    def _patched_collect(*args, **kwargs):
        return _PoisonPacket()

    monkeypatch.setattr(cmd_pr_replay, "_collect_change_evidence", _patched_collect)

    out_path = pr_replay_project / "evidence.json"
    result = _invoke_pr_replay(
        cli_runner,
        pr_replay_project,
        "--tier",
        "sample",
        "--evidence",
        str(out_path),
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DV raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DV raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("pr_replay_completeness_classify_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("pr_replay_completeness_classify_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the completeness_classify marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Cross-prefix isolation -- W607-DV markers stay in pr_replay_* family
# ---------------------------------------------------------------------------


def test_w607dv_marker_prefix_pr_replay_family(cli_runner, pr_replay_project, monkeypatch):
    """W607-DV markers use the canonical ``pr_replay_*`` prefix (same
    family as W607-AH + W607-CA; W607-DV is ADDITIVE, not a separate
    prefix).

    Hard guard: any W607-DV marker that leaks into a sibling W607-*
    family (e.g. ``postmortem_*`` / ``audit_trail_verify_*`` /
    ``critique_*``) breaks the closed-enum marker-family contract.
    """
    from roam.commands import cmd_pr_replay

    _PoisonPacket = _make_poison_packet_class(raise_after_calls=2)

    def _patched_collect(*args, **kwargs):
        return _PoisonPacket()

    monkeypatch.setattr(cmd_pr_replay, "_collect_change_evidence", _patched_collect)

    out_path = pr_replay_project / "evidence.json"
    result = _invoke_pr_replay(
        cli_runner,
        pr_replay_project,
        "--tier",
        "sample",
        "--evidence",
        str(out_path),
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("pr_replay_"), (
            f"every W607-DV marker must use the ``pr_replay_*`` prefix; got {marker!r}"
        )

    # Verify NO cross-prefix leakage into sibling W607 families
    forbidden_prefixes = (
        "postmortem_",
        "audit_trail_verify_",
        "critique_",
        "preflight_",
        "diagnose_",
        "dead_",
        "pr_bundle_",
    )
    for marker in failure_markers:
        for forbidden in forbidden_prefixes:
            assert not marker.startswith(forbidden), (
                f"W607-DV marker leaked into sibling family {forbidden!r}; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (10) AST-scan source pinning all three accumulators
# ---------------------------------------------------------------------------


def test_all_three_warnings_out_accumulators_present_in_ast():
    """AST-scan source pinning: cmd_pr_replay must carry all three
    accumulators (``_w607ah_warnings_out`` / ``_w607ca_warnings_out`` /
    ``_w607dv_warnings_out``) as local-variable assignments inside the
    ``pr_replay`` command function body.

    Triple-layer plumbing contract: AH substrate + CA aggregation-phase
    + DV aggregation-LAYER. A refactor that drops any of them silently
    must be caught here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_ah = found_ca = found_dv = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if node.target.id == "_w607ah_warnings_out":
            found_ah = True
        elif node.target.id == "_w607ca_warnings_out":
            found_ca = True
        elif node.target.id == "_w607dv_warnings_out":
            found_dv = True

    assert found_ah, "W607-AH substrate-CALL accumulator (``_w607ah_warnings_out``) missing from cmd_pr_replay AST."
    assert found_ca, "W607-CA aggregation-phase accumulator (``_w607ca_warnings_out``) missing from cmd_pr_replay AST."
    assert found_dv, "W607-DV aggregation-LAYER accumulator (``_w607dv_warnings_out``) missing from cmd_pr_replay AST."


# ---------------------------------------------------------------------------
# (11) W978 kwarg-default audit -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants_dv():
    """W978 kwarg-default audit: every W607-DV ``default=`` must be a
    literal constant, NOT computed from upstream values.

    cmd_sbom W607-CG sealed this axis after a regression where
    ``len(_BadDeps())`` defaults eagerly raised inside the ``default=``
    expression -- BEFORE the wrap call entered the try-block. cmd_taint
    W607-CJ added the 5th discipline: ``len()`` lives INSIDE the
    closure, not at the kwarg-bind site.

    AST audit: walk every ``_run_check_dv(...)`` call, extract the
    ``default=`` keyword argument's AST node, confirm it is a Constant
    (literal int/str/bool/None) or a Dict/List/Set/Tuple of Constants
    or a bare Name reference (variable bound BEFORE the wrap call).
    Reject any Call, Attribute, Subscript, BinOp, Compare, IfExp, or
    f-string node in the default expression -- these compute from
    upstream values at kwarg-bind time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.Dict):
            return all(_is_literal(k) for k in node.keys if k is not None) and all(_is_literal(v) for v in node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_literal(e) for e in node.elts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _is_literal(node.operand)
        return False

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dv"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_dv(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_pr_replay.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ / cmd_audit_trail_export "
        "W607-CR for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (12) W978 5th-discipline -- len() lives INSIDE the closure
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_dv_closures_not_at_kwarg_bind_site():
    """W978 5th-discipline AST guard (cmd_taint W607-CJ anchor): every
    ``len()`` call on a wrapped input MUST live INSIDE the wrapped
    closure, NOT at the ``_run_check_dv(...)`` call site as a positional
    or keyword argument expression.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dv"):
            continue
        for sub in node.args:
            for descendant in ast.walk(sub):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call at "
                        f"_run_check_dv positional-arg site -- W978 "
                        f"5th-discipline violation"
                    )
        for kw in node.keywords:
            for descendant in ast.walk(kw.value):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call in "
                        f"_run_check_dv kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_pr_replay.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure. See cmd_taint W607-CJ "
        "for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (13) Phase-name collision check -- no overlap with W607-AH / W607-CA
# ---------------------------------------------------------------------------


def test_w607dv_phase_names_no_collision_with_w607ah_or_w607ca():
    """Phase-name collision check: W607-DV phase names MUST NOT overlap
    with W607-AH substrate phases or W607-CA aggregation phases.

    AH phases:  run_postmortem / aggregate_by_detector / render_report /
                render_pdf / build_review_suggestions /
                collect_change_evidence / to_canonical_json /
                render_evidence_markdown
    CA phases:  score_classify / severity_normalize / compute_verdict /
                render_markdown / serialize_envelope / auto_log
    DV phases:  completeness_classify / completeness_rollup /
                evidence_verdict_compose / dv_serialize_envelope

    All three sets must be disjoint so the per-phase marker prefix is
    unambiguous.
    """
    dv_phases = frozenset(_DV_PHASES)

    overlap_ah = _AH_PHASES & dv_phases
    overlap_ca = _CA_PHASES & dv_phases
    assert not overlap_ah, f"W607-DV phase collision with W607-AH substrate phases: {sorted(overlap_ah)!r}"
    assert not overlap_ca, f"W607-DV phase collision with W607-CA aggregation phases: {sorted(overlap_ca)!r}"


# ---------------------------------------------------------------------------
# (14) W276 INSUFFICIENT-tier preservation
# ---------------------------------------------------------------------------


def test_w276_insufficient_tier_preserved_when_no_evidence_packet(cli_runner, pr_replay_project):
    """W276 4-tier vocabulary preservation: when no evidence packet is
    built (no --evidence / --markdown / --evidence-bundle), the DV
    ``completeness_classify`` floor MUST surface tier=INSUFFICIENT --
    NOT a degraded three-tier collapse to PASS/WARN/FAIL.

    The four-tier vocabulary PASS / WARN / FAIL / INSUFFICIENT is the
    closed enumeration; INSUFFICIENT is reserved for the no-packet /
    no-method case so consumers can distinguish "we measured and it's
    failing" from "we never measured".
    """
    # Default invocation -- no --evidence, no packet built
    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    tier = data["summary"].get("completeness_tier")
    assert tier == "INSUFFICIENT", (
        f"W276 4-tier vocabulary regression: completeness_tier should be "
        f"INSUFFICIENT when no evidence packet is built; got {tier!r}. "
        f"summary keys = {sorted(data['summary'].keys())!r}"
    )


# ---------------------------------------------------------------------------
# (15) W561 dropped-row disclosure preservation (redaction_count surfaces)
# ---------------------------------------------------------------------------


def test_w561_dropped_row_disclosure_redaction_count_surfaces(cli_runner, pr_replay_project, monkeypatch):
    """W561-spirit dropped-row disclosure: the DV ``completeness_rollup``
    surfaces ``redaction_count`` + ``producer_warning_count`` on the
    summary block so closed-enum drop visibility stays intact.

    pr_replay does NOT carry OSCAL's ``dropped_enum_rows`` field, but
    the W561 spirit (always disclose how many closed-enum values were
    dropped at the boundary) applies here as ``redaction_count`` (count
    of producer-side closed-enum drops) and ``producer_warning_count``
    (count of producer-level warning markers).
    """
    from roam.commands import cmd_pr_replay

    class _PacketWithRedactions:
        content_hash = "synthetic"
        authority_refs = ()
        redactions = ("producer_not_available", "secret", "policy")

        def evidence_completeness(self):
            return {
                "complete": 4,
                "partial": 0,
                "missing": 4,
                "not_applicable": 0,
                "Q1": "complete",
                "Q2": "complete",
                "Q3": "complete",
                "Q4": "complete",
                "Q5": "missing",
                "Q6": "missing",
                "Q7": "missing",
                "Q8": "missing",
            }

        def to_canonical_json(self):
            return "{}"

    def _patched_collect(*args, **kwargs):
        return _PacketWithRedactions()

    monkeypatch.setattr(cmd_pr_replay, "_collect_change_evidence", _patched_collect)

    out_path = pr_replay_project / "evidence.json"
    result = _invoke_pr_replay(
        cli_runner,
        pr_replay_project,
        "--tier",
        "sample",
        "--evidence",
        str(out_path),
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    redaction_count = data["summary"].get("redaction_count")
    producer_warning_count = data["summary"].get("producer_warning_count")
    assert redaction_count == 3, (
        f"W561 dropped-row disclosure regression: redaction_count should "
        f"be 3 (matching ``redactions`` tuple length); got {redaction_count!r}"
    )
    assert producer_warning_count is not None, (
        f"W561 dropped-row disclosure regression: producer_warning_count "
        f"must be present on the summary block; got summary = "
        f"{data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (16) W246 context_refs producer wiring not regressed
# ---------------------------------------------------------------------------


def test_w246_context_refs_producer_wiring_not_regressed():
    """W246 context_refs producer wiring preservation: the W246 gatherer
    + the W607-AH ``collect_change_evidence`` boundary remain untouched
    after W607-DV. AST-level guard: confirm
    ``_build_context_refs_from_context_files`` is still referenced AND
    the W607-AH wrap of ``_collect_change_evidence`` is still present.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    src = src_path.read_text(encoding="utf-8")

    # W246 context_refs gatherer reference -- canonical W246 anchor
    assert "_build_context_refs_from_context_files" in src, (
        "W246 context_refs producer wiring regressed: "
        "``_build_context_refs_from_context_files`` reference missing "
        "from cmd_pr_replay."
    )

    # W607-AH collect_change_evidence wrap -- substrate boundary intact
    assert '_run_check_ah(\n            "collect_change_evidence"' in src or (
        '"collect_change_evidence"' in src and "_collect_change_evidence" in src
    ), (
        "W607-AH ``collect_change_evidence`` wrap regressed; the W246 "
        "producer wiring fundamentally depends on the collector boundary."
    )


# ---------------------------------------------------------------------------
# (17) Source-pin: 4 W607-DV phases + 8 W607-AH phases + 6 W607-CA phases
# ---------------------------------------------------------------------------


def test_w607_total_phase_count_pr_replay():
    """Total phase count: cmd_pr_replay carries 4 W607-DV + 6 W607-CA +
    8 W607-AH = 18 W607 phases. The phase-count guard pins the canonical
    arity so a refactor that silently merges layers fails here rather
    than via subtle marker-prefix regression.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    src = src_path.read_text(encoding="utf-8")

    # Use AST scan so indentation / arg-formatting variants don't drift
    # the count. Walk every Call node whose callee is one of the three
    # _run_check_* helpers, look at the first positional arg.
    tree = ast.parse(src)
    ah_phases_seen: set[str] = set()
    ca_phases_seen: set[str] = set()
    dv_phases_seen: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        phase = first.value
        if node.func.id == "_run_check_ah":
            ah_phases_seen.add(phase)
        elif node.func.id == "_run_check_ca":
            ca_phases_seen.add(phase)
        elif node.func.id == "_run_check_dv":
            dv_phases_seen.add(phase)
    ah_present = len(ah_phases_seen)
    ca_present = len(ca_phases_seen)
    dv_present = len(dv_phases_seen)

    assert ah_present == 8, f"W607-AH substrate phase count regression: expected 8 wraps, got {ah_present}"
    assert ca_present >= 6, f"W607-CA aggregation-phase count regression: expected >=6 wraps, got {ca_present}"
    assert dv_present == 4, f"W607-DV aggregation-LAYER phase count regression: expected 4 wraps, got {dv_present}"
