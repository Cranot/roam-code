"""W607-CA -- additive aggregation-phase plumbing for ``cmd_pr_replay``.

cmd_pr_replay is the consumer at the heart of the W805-OOOOO 3-artifact
family: it reads the bundle JSON from disk (artifact 1), reconstructs
the ChangeEvidence packet via W534 ``to_canonical_json`` (artifact 2),
and reads the run-ledger root (artifact 3). It is the producer/consumer
pair of cmd_pr_bundle. With W607-CA landed, the full W631 risk-LEVEL
vocabulary range is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-AH (8 phases)
  - aggregation-phase layer: W607-CA (6 phases incl. the pr-replay-
    specific ``render_markdown`` re-probe)

Both layers share the canonical ``pr_replay_*`` marker family and the
``pr_replay_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two buckets (``_w607ah_warnings_out`` substrate-CALL +
``_w607ca_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order. Closes the PR-bundle ecosystem (emit + analyze + replay)
pairing with cmd_pr_bundle's W607-AE + BW.

Relation to W607-AH
-------------------

cmd_pr_replay already carries W607-AH substrate-CALL plumbing covering
8 substrate-helper boundaries (run_postmortem / aggregate_by_detector /
render_report / render_pdf / build_review_suggestions /
collect_change_evidence / to_canonical_json / render_evidence_markdown).
W607-CA is ADDITIVE on top of W607-AH, extending marker coverage to the
AGGREGATION-PHASE boundaries that W607-AH left unguarded:

  - ``score_classify``      -- per-replay risk classification re-probe
  - ``severity_normalize``  -- canonical W631 risk-LEVEL projection
  - ``compute_verdict``     -- augmented verdict-floor build
  - ``render_markdown``     -- pr-replay-specific report_md re-probe
  - ``serialize_envelope``  -- ``json_envelope("pr-replay", ...)`` probe
  - ``auto_log``            -- active-run ledger write

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_pr_replay's aggregation-phase boundaries had no guards beyond the
W607-AH compose call. A downstream refactor that changes the risk-level
projection contract, the canonical W631 vocabulary, the verdict-string
composition, the HMAC chain on the runs ledger, or the ``json_envelope``
shape would crash the envelope post-compose -- after the substrate
signals were already gathered, the agent loses the result. W607-CA wraps
each boundary with ``_run_check_ca`` so a raise becomes a marker via
``warnings_out`` and the envelope still emits.

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
# Fixture -- indexed corpus with at least one commit on the branch
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def pr_replay_project(tmp_path, monkeypatch):
    """Indexed corpus with at least one commit for ``pr-replay`` to read."""
    proj = tmp_path / "pr_replay_w607ca_project"
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
# (1) Happy path -- envelope omits W607-CA aggregation markers
# ---------------------------------------------------------------------------


def test_pr_replay_happy_path_no_w607ca_markers(cli_runner, pr_replay_project):
    """Clean pr-replay -> no W607-CA aggregation markers.

    Hash-stable: an empty W607-CA bucket on the success path must produce
    an envelope without any
    ``pr_replay_score_classify_failed:`` /
    ``pr_replay_severity_normalize_failed:`` /
    ``pr_replay_compute_verdict_failed:`` /
    ``pr_replay_render_markdown_failed:`` /
    ``pr_replay_serialize_envelope_failed:`` /
    ``pr_replay_auto_log_failed:`` markers. Mirror of cmd_pr_bundle
    W607-BW discipline.
    """
    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-replay"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607ca_phases = (
        "pr_replay_score_classify_failed:",
        "pr_replay_severity_normalize_failed:",
        "pr_replay_compute_verdict_failed:",
        "pr_replay_render_markdown_failed:",
        "pr_replay_serialize_envelope_failed:",
        "pr_replay_auto_log_failed:",
    )
    for prefix in w607ca_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean pr-replay must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_ca`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_pr_replay_carries_w607ca_accumulator():
    """AST-level guard: cmd_pr_replay source carries the W607-CA accumulator.

    Pins the canonical W607-CA anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AH) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    assert src_path.exists(), f"cmd_pr_replay.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607ca_warnings_out" in src, (
        "W607-CA accumulator missing from cmd_pr_replay; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_ca" in src, (
        "W607-CA helper ``_run_check_ca`` missing from cmd_pr_replay; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_ca is defined inside pr_replay_cmd().
    tree = ast.parse(src)
    found_run_check_ca = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ca":
            found_run_check_ca = True
            break
    assert found_run_check_ca, (
        "W607-CA ``_run_check_ca`` helper not found in cmd_pr_replay AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-AH must still be present (additive layer does NOT replace it)
    assert "_w607ah_warnings_out" in src, (
        "W607-AH accumulator vanished alongside the W607-CA add; the "
        "additive plumbing must preserve the W607-AH substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_ca():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_ca(...)`` with the canonical phase name.

    The six phases must appear inside a ``_run_check_ca("<phase>", ...)``
    call inside cmd_pr_replay. Multi-indent variants are all considered
    valid wrap call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "score_classify",
        "severity_normalize",
        "compute_verdict",
        "render_markdown",
        "serialize_envelope",
        "auto_log",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_ca(\n        "{phase}"',
            f'_run_check_ca(\n            "{phase}"',
            f'_run_check_ca(\n                "{phase}"',
            f'_run_check_ca(\n                    "{phase}"',
            f'_run_check_ca(\n                        "{phase}"',
            f'_run_check_ca("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_ca(...); add the W607-CA guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) Marker shape -- ``pr_replay_<phase>_failed:<exc>:<detail>``
# ---------------------------------------------------------------------------


def test_auto_log_failure_marker_format(cli_runner, pr_replay_project, monkeypatch):
    """If ``auto_log`` raises, surface ``pr_replay_auto_log_failed:`` and
    keep the pr-replay envelope intact.

    Discipline mirror of the W607-BW auto_log-failure pattern in
    cmd_pr_bundle.
    """
    from roam.commands import cmd_pr_replay

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-auto-log-from-W607-CA")

    monkeypatch.setattr(cmd_pr_replay, "auto_log", _raise_auto_log)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    markers = [m for m in all_markers if m.startswith("pr_replay_auto_log_failed:")]
    assert markers, f"expected ``pr_replay_auto_log_failed:`` marker; got top={top_wo!r}, summary={summary_wo!r}"
    marker = markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-auto-log-from-W607-CA" in parts[2], parts

    # Envelope still emits the core pr-replay signal block
    assert data.get("command") == "pr-replay", data


# ---------------------------------------------------------------------------
# (5) SCORE CLASSIFY DEGRADATION discipline
# ---------------------------------------------------------------------------


def test_score_classify_degradation_surfaces_unknown_sentinel(cli_runner, pr_replay_project, monkeypatch):
    """When the score_classify boundary raises:

    1. Marker ``pr_replay_score_classify_failed:`` appears
    2. Envelope still completes with a parseable summary
    3. Summary stamps ``score_classification: "unknown"`` sentinel

    The underlying action (emit the replay envelope) stays -- degraded
    outcomes are valid design. The LIE we prevent is a clean classified
    verdict when score_classify actually raised. Mirror of cmd_pr_bundle's
    W607-BW score_classify pattern.
    """
    from roam.commands import cmd_pr_replay

    # Force the score_classify probe to raise by making normalize_risk_level
    # raise on the FIRST call. Score-classify uses the summary's commit
    # ratio; we instead patch the closure dependency.
    # The simplest approach: patch the postmortem run to return a summary
    # whose ``commits_scanned`` field raises on int() coercion.
    original_run_postmortem = cmd_pr_replay._run_postmortem

    class _BadInt:
        def __int__(self):
            raise RuntimeError("synthetic-score-classify-from-W607-CA")

    def _patched_run_postmortem(*args, **kwargs):
        result = original_run_postmortem(*args, **kwargs)
        if isinstance(result, dict):
            summary = result.get("summary") or {}
            summary["commits_scanned"] = _BadInt()
            # Drop the risk_level so the fallback path is taken.
            summary.pop("risk_level", None)
            result["summary"] = summary
        return result

    monkeypatch.setattr(cmd_pr_replay, "_run_postmortem", _patched_run_postmortem)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    # (1) marker appears -- W607-CA score_classify
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    markers = [m for m in all_markers if m.startswith("pr_replay_score_classify_failed:")]
    assert markers, f"expected ``pr_replay_score_classify_failed:`` marker; got top={top_wo!r}, summary={summary_wo!r}"

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


def test_score_classify_clean_path_stamps_classified(cli_runner, pr_replay_project):
    """Happy path: ``score_classification`` summary field is ``"classified"``.

    Mirror of the W607-BW discipline that the sentinel disambiguates a
    real classified verdict from a degraded "unknown" floor.
    """
    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("score_classification") == "classified", (
        f'clean path must stamp ``score_classification: "classified"``; '
        f"got {data['summary'].get('score_classification')!r}"
    )


# ---------------------------------------------------------------------------
# (6) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, pr_replay_project, monkeypatch):
    """ANY W607-CA or W607-AH marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    pr-replay" from "pr-replay ran with aggregation degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_pr_replay

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-CA")

    monkeypatch.setattr(cmd_pr_replay, "auto_log", _raise_auto_log)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CA warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607ca_warnings_out_in_both_top_and_summary(cli_runner, pr_replay_project, monkeypatch):
    """Non-empty W607-CA bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-BW contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-detail
    mode; summary mirror gives consumers reading only the summary block
    visibility too.
    """
    from roam.commands import cmd_pr_replay

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CA")

    monkeypatch.setattr(cmd_pr_replay, "auto_log", _raise_auto_log)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CA raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CA raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("pr_replay_auto_log_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("pr_replay_auto_log_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the auto_log marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-AH COEXISTENCE -- substrate-CALL + aggregation-phase markers
#     coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607ah_substrate_markers_coexist_with_w607ca_aggregation(cli_runner, pr_replay_project, monkeypatch):
    """Confirm ``pr_replay_<substrate-phase>_failed:`` markers (W607-AH
    layer) coexist with ``pr_replay_<agg-phase>_failed:`` markers
    (W607-CA layer) -- both in same family, threaded through different
    buckets at envelope-emit.

    This is the explicit guard requested by the W607-CA brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``pr_replay_<substrate-phase>_failed:`` vs.
    ``pr_replay_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_pr_replay

    # W607-AH substrate boundary -- _aggregate_by_detector
    def _raise_aggregate(*a, **kw):
        raise RuntimeError("synthetic-ah-coexist-aggregate")

    # W607-CA aggregation boundary -- auto_log
    def _raise_auto_log(*a, **kw):
        raise RuntimeError("synthetic-ca-coexist-auto-log")

    monkeypatch.setattr(cmd_pr_replay, "_aggregate_by_detector", _raise_aggregate)
    monkeypatch.setattr(cmd_pr_replay, "auto_log", _raise_auto_log)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)

    # Substrate-CALL phase from W607-AH
    ah_markers = [m for m in all_markers if m.startswith("pr_replay_aggregate_by_detector_failed:")]
    # Aggregation-phase from W607-CA
    ca_markers = [m for m in all_markers if m.startswith("pr_replay_auto_log_failed:")]

    assert ah_markers, (
        f"W607-AH substrate-CALL marker (pr_replay_aggregate_by_detector_failed) missing; got {all_markers!r}"
    )
    assert ca_markers, f"W607-CA aggregation-phase marker (pr_replay_auto_log_failed) missing; got {all_markers!r}"

    # Both share the canonical ``pr_replay_*`` family
    assert all(m.startswith("pr_replay_") for m in (ah_markers + ca_markers)), (
        f"all markers must share the canonical ``pr_replay_*`` family; got ah = {ah_markers!r}, ca = {ca_markers!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-CA uses the SAME ``pr_replay_*`` family
# ---------------------------------------------------------------------------


def test_w607ca_marker_prefix_pr_replay_family(cli_runner, pr_replay_project, monkeypatch):
    """W607-CA markers use the canonical ``pr_replay_*`` prefix (same family
    as W607-AH; W607-CA is ADDITIVE, not a separate prefix).

    Hard guard: any W607-CA marker that leaks into a sibling W607-*
    family (e.g. ``pr_bundle_*`` / ``attest_*`` / ``preflight_*``) breaks
    the closed-enum marker-family contract.
    """
    from roam.commands import cmd_pr_replay

    def _raise_auto_log(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-CA")

    monkeypatch.setattr(cmd_pr_replay, "auto_log", _raise_auto_log)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-discipline check"
    # Filter to substrate-/aggregation-CALL markers (have ``_failed:`` in the middle).
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure markers"
    for marker in failure_markers:
        assert marker.startswith("pr_replay_"), (
            f"every W607-CA marker must use the ``pr_replay_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (10) CROSS-PREFIX ISOLATION -- pr_replay_* markers DO NOT leak into siblings
# ---------------------------------------------------------------------------


def test_pr_replay_markers_do_not_leak_into_adjacent_commands(cli_runner, pr_replay_project, monkeypatch):
    """``pr_replay_*`` markers must NOT appear in ``cmd_pr_bundle`` /
    ``cmd_pr_analyze`` envelopes.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels.
    """
    from roam.commands import cmd_pr_replay

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation-from-W607-CA")

    monkeypatch.setattr(cmd_pr_replay, "auto_log", _raise_auto_log)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    assert all_markers, "expected non-empty warnings_out for prefix-isolation check"

    # Foreign-family leakage check
    foreign_prefixes = (
        "pr_bundle_",
        "pr_analyze_",
        "attest_",
        "cga_",
        "preflight_",
        "impact_",
        "diagnose_",
        "critique_",
        "diff_",
        "pr_risk_",
    )
    # Filter to failure markers (the ones we own)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_pr_replay warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (11) Compute-verdict guard -- raise floors to a stable verdict
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, pr_replay_project, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    We force the verdict-floor closure to raise by patching
    ``normalize_risk_level`` to return an object whose ``__format__``
    raises -- the verdict f-string interpolation of risk_level_canonical
    then trips the wrap inside ``_make_pr_replay_verdict_floor``.

    W978 first-hypothesis check: the canonical floor MUST NOT
    re-interpolate the same value that raised on the BadLevel sentinel
    test -- the floor is a literal string.
    """
    from roam.commands import cmd_pr_replay

    # A ``str`` subclass with ``__str__`` that returns ``self`` (so the
    # defensive ``str()`` coercion in severity_normalize preserves the
    # subclass) and ``__format__`` that raises (so the verdict-floor
    # f-string interpolation trips the W607-CA compute_verdict wrap).
    class _BadLevel(str):
        def __new__(cls):
            return super().__new__(cls, "bad-level")

        def __str__(self):
            # str() returns self -- defeats the defensive str() coerce
            # that severity_normalize uses to drop hostile subclasses.
            return self

        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-from-W607-CA")

    _bad_instance = _BadLevel()

    def _bad_normalize(level):
        return _bad_instance

    monkeypatch.setattr(cmd_pr_replay, "normalize_risk_level", _bad_normalize)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    markers = [m for m in all_markers if m.startswith("pr_replay_compute_verdict_failed:")]
    assert markers, f"expected ``pr_replay_compute_verdict_failed:`` marker; got top={top_wo!r}, summary={summary_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (12) RENDER MARKDOWN re-probe -- pr-replay-specific aggregation phase
# ---------------------------------------------------------------------------


def test_render_markdown_clean_path_stamps_rendered(cli_runner, pr_replay_project):
    """Happy path: ``render_markdown_state`` summary field is ``"rendered"``.

    Mirror of the W607-BW score_classify sentinel discipline applied to
    the pr-replay-specific render_markdown re-probe.
    """
    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("render_markdown_state") == "rendered", (
        f'clean path must stamp ``render_markdown_state: "rendered"``; '
        f"got {data['summary'].get('render_markdown_state')!r}"
    )


# ---------------------------------------------------------------------------
# (13) W805 CROSS-ARTIFACT PR-BUNDLE ECOSYSTEM PAIRING
# ---------------------------------------------------------------------------


def test_w805_pr_replay_and_pr_bundle_marker_families_coexist(tmp_path, monkeypatch):
    """W805 cross-artifact pair: ``pr_replay_<phase>_failed:`` markers
    (W607-AH + CA) coexist with ``pr_bundle_<phase>_failed:`` markers
    (W607-AE + BW) when both commands are invoked on the same workspace.

    Closes the PR-bundle ecosystem (emit + replay). cmd_pr_bundle is the
    producer; cmd_pr_replay is the consumer. Both commands must thread
    their respective marker families WITHOUT cross-contamination
    (pr_replay_* never appears in pr_bundle_* and vice versa).
    """
    proj = tmp_path / "pr_replay_bundle_w805_project"
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

    runner = CliRunner()

    # Initialise bundle (so emit has something to load)
    init_result = _invoke_pr_bundle(runner, proj, "init", "--intent", "W805 ecosystem")
    assert init_result.exit_code == 0, init_result.output

    # Patch BOTH the pr_bundle auto_log AND the pr_replay auto_log to force
    # an aggregation-phase marker on each command's envelope.
    from roam.commands import cmd_pr_bundle, cmd_pr_replay

    def _raise_pb_auto_log(*a, **kw):
        raise RuntimeError("synthetic-W805-pair-pr-bundle-auto-log")

    def _raise_pr_replay_auto_log(*a, **kw):
        raise RuntimeError("synthetic-W805-pair-pr-replay-auto-log")

    monkeypatch.setattr(cmd_pr_bundle, "auto_log", _raise_pb_auto_log)
    monkeypatch.setattr(cmd_pr_replay, "auto_log", _raise_pr_replay_auto_log)

    # Invoke pr-bundle emit
    pb_result = _invoke_pr_bundle(runner, proj, "emit", "--no-auto-collect")
    assert pb_result.exit_code in (0, 5), pb_result.output
    pb_data = _json.loads(pb_result.output)
    pb_wo = pb_data.get("warnings_out") or []
    pb_markers = [m for m in pb_wo if m.startswith("pr_bundle_auto_log_failed:")]
    assert pb_markers, f"W805 pair: pr_bundle_auto_log_failed: missing from pr-bundle envelope; got {pb_wo!r}"
    # pr_bundle envelope must NOT contain pr_replay_* failure markers
    pb_replay_leak = [m for m in pb_wo if m.startswith("pr_replay_") and "_failed:" in m]
    assert not pb_replay_leak, f"W805 pair: pr_replay_* markers leaked into pr-bundle envelope; got {pb_replay_leak!r}"

    # Invoke pr-replay
    pr_result = _invoke_pr_replay(runner, proj, "--tier", "sample")
    assert pr_result.exit_code in (0, 5), pr_result.output
    pr_data = _json.loads(pr_result.output)
    pr_wo = pr_data.get("warnings_out") or []
    pr_summary_wo = pr_data["summary"].get("warnings_out") or []
    pr_all = list(pr_wo) + list(pr_summary_wo)
    pr_markers = [m for m in pr_all if m.startswith("pr_replay_auto_log_failed:")]
    assert pr_markers, (
        f"W805 pair: pr_replay_auto_log_failed: missing from pr-replay envelope; "
        f"got top={pr_wo!r}, summary={pr_summary_wo!r}"
    )
    # pr_replay envelope must NOT contain pr_bundle_* failure markers
    pr_bundle_leak = [m for m in pr_all if m.startswith("pr_bundle_") and "_failed:" in m]
    assert not pr_bundle_leak, f"W805 pair: pr_bundle_* markers leaked into pr-replay envelope; got {pr_bundle_leak!r}"


# ---------------------------------------------------------------------------
# (14) Combined W607-AH + W607-CA markers in summary mirror
# ---------------------------------------------------------------------------


def test_combined_ah_and_ca_markers_in_summary_mirror(cli_runner, pr_replay_project, monkeypatch):
    """Combined raise: both substrate-CALL and aggregation-phase markers
    flow into BOTH top-level + summary.warnings_out mirrors.

    Confirms both buckets surface to consumers reading either mirror.
    """
    from roam.commands import cmd_pr_replay

    def _raise_aggregate(*a, **kw):
        raise RuntimeError("synthetic-ah-combined-aggregate")

    def _raise_auto_log(*a, **kw):
        raise RuntimeError("synthetic-ca-combined-auto-log")

    monkeypatch.setattr(cmd_pr_replay, "_aggregate_by_detector", _raise_aggregate)
    monkeypatch.setattr(cmd_pr_replay, "auto_log", _raise_auto_log)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []

    # Both markers in summary mirror
    assert any(m.startswith("pr_replay_aggregate_by_detector_failed:") for m in summary_wo), (
        f"W607-AH marker missing from summary mirror; got {summary_wo!r}"
    )
    assert any(m.startswith("pr_replay_auto_log_failed:") for m in summary_wo), (
        f"W607-CA marker missing from summary mirror; got {summary_wo!r}"
    )

    # Both markers in top-level mirror
    assert any(m.startswith("pr_replay_aggregate_by_detector_failed:") for m in top_wo), (
        f"W607-AH marker missing from top-level mirror; got {top_wo!r}"
    )
    assert any(m.startswith("pr_replay_auto_log_failed:") for m in top_wo), (
        f"W607-CA marker missing from top-level mirror; got {top_wo!r}"
    )
