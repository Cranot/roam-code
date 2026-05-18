"""W607-AH -- ``cmd_pr_replay`` threads ``warnings_out`` onto its envelope.

Thirty-fourth-in-batch W607 consumer-layer arc. Direct sibling of W607-AE
(cmd_pr_bundle producer-side substrate-CALL plumbing). cmd_pr_replay is
the **reader at the heart of the W805-OOOOO 3-artifact family**: it
reads the bundle JSON from disk (artifact 1), reconstructs the
``ChangeEvidence`` packet via the W534 ``to_canonical_json`` boundary
(artifact 2), and reads the run-ledger root (artifact 3). It is the
producer/consumer pair of cmd_pr_bundle on the evidence-compiler PR
Replay recipe path -- pr-bundle emits, pr-replay reads + renders.

Substrate boundaries wrapped by W607-AH
---------------------------------------

Eight substrate-call sites in ``pr_replay_cmd()`` get the canonical
``_run_check_ah(phase, fn, *args)`` wrapper:

* ``run_postmortem``           -- _run_postmortem(...) (the source of commits)
* ``aggregate_by_detector``    -- _aggregate_by_detector(commits) (aggregation)
* ``render_report``            -- _render_report(...) (markdown projection)
* ``render_pdf``               -- _render_pdf(...) (PDF projection)
* ``build_review_suggestions`` -- _build_review_suggestions(...)
* ``collect_change_evidence``  -- _collect_change_evidence(...) (W534 + W805)
* ``to_canonical_json``        -- evidence_packet.to_canonical_json() (W534)
* ``render_evidence_markdown`` -- _render_evidence_markdown(...) (projection)

Each raise becomes a ``pr_replay_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607ah_warnings_out`` and the envelope still emits cleanly.

W805 cross-artifact READER bridge
---------------------------------

cmd_pr_replay is THE reader on the W805-OOOOO 3-artifact family + W534
ChangeEvidence path. Pattern-2 disclosure on the reader side has even
HIGHER consequence than the writer side: if the reader silently falls
back when an artifact is missing or malformed, downstream consumers
(audit-report, GRC export) treat the partial replay as complete. W607-AH
markers on each read boundary lift this from silent to disclosed.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_pr_replay's substrate-call sites are direct invocations on
module-level helpers (and one method-call on the ChangeEvidence packet,
``packet.to_canonical_json()``). The dominant raise axis is the
helper-CALL boundary -- consistent with W607-N..AE. Each helper can
raise on a corrupted bundle JSON, a missing run-ledger entry, a YAML
schema drift, a network failure during ``gh api`` review harvest, or a
hostile char inside the markdown renderer's table escaper.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_pr_replay has
genuine deferred-load imports inside ``_collect_change_evidence``
(``from roam.evidence import ...`` etc.) which are legitimate
lazy-load patterns, NOT cargo-cult cycle hedges. Left untouched per
W907.

W607-AE pairing bonus
---------------------

cmd_pr_replay reads bundles emitted by cmd_pr_bundle. When pr-bundle
itself emitted with W607-AE markers in its warnings_out, the replay
processing that bundle should not collide with its own W607-AH
markers -- the two marker prefixes (``pr_bundle_*`` and
``pr_replay_*``) are closed-enum distinct.

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


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with at least one commit on the branch
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def pr_replay_project(tmp_path, monkeypatch):
    """Indexed corpus with at least one commit for ``pr-replay`` to read."""
    proj = tmp_path / "pr_replay_w607ah_project"
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
# (1) Happy path -- clean envelope omits W607-AH substrate markers
# ---------------------------------------------------------------------------


def test_pr_replay_clean_envelope_omits_w607ah_markers(cli_runner, pr_replay_project):
    """Clean pr-replay -> no W607-AH substrate markers on the envelope.

    Hash-stable: an empty W607-AH bucket on the success path must produce
    an envelope without substrate markers. The envelope shape stays
    byte-identical to the pre-W607-AH consumer when no helper raised.
    """
    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-replay"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-AH substrate markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("pr_replay_") and "_failed:" in m]
    assert not substrate_markers, (
        f"clean pr-replay must NOT surface pr_replay_<phase>_failed: "
        f"markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) run_postmortem failure -> pr_replay_run_postmortem_failed marker
# ---------------------------------------------------------------------------


def test_pr_replay_run_postmortem_failure_marker_format(cli_runner, pr_replay_project, monkeypatch):
    """If _run_postmortem raises, surface ``pr_replay_run_postmortem_failed:``."""
    from roam.commands import cmd_pr_replay

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-postmortem-from-W607-AH")

    monkeypatch.setattr(cmd_pr_replay, "_run_postmortem", _raise)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    pm_markers = [m for m in top_wo if m.startswith("pr_replay_run_postmortem_failed:")]
    assert pm_markers, f"expected pr_replay_run_postmortem_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in pm_markers), pm_markers
    assert any("synthetic-postmortem-from-W607-AH" in m for m in pm_markers), pm_markers


# ---------------------------------------------------------------------------
# (3) render_report failure -> pr_replay_render_report_failed marker
# ---------------------------------------------------------------------------


def test_pr_replay_render_report_failure_marker_format(cli_runner, pr_replay_project, monkeypatch):
    """If _render_report raises, marker surfaces AND envelope completes."""
    from roam.commands import cmd_pr_replay

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-render-from-W607-AH")

    monkeypatch.setattr(cmd_pr_replay, "_render_report", _raise)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    rr_markers = [m for m in top_wo if m.startswith("pr_replay_render_report_failed:")]
    assert rr_markers, f"expected pr_replay_render_report_failed: marker; got {top_wo!r}"
    # Even on the render-failure path, the envelope completes with a verdict.
    assert isinstance(data["summary"].get("verdict"), str)


# ---------------------------------------------------------------------------
# (4) W805 READER bridge: collect_change_evidence raise -> marker + replay
#     completes with empty evidence packet
# ---------------------------------------------------------------------------


def test_pr_replay_collect_change_evidence_failure_w805_reader_bridge(
    cli_runner, pr_replay_project, monkeypatch, tmp_path
):
    """The W805 reader bridge: collect_change_evidence raise -> marker + replay continues.

    cmd_pr_replay is the canonical reader on the W805-OOOOO 3-artifact
    family. A raise inside the collector previously crashed the entire
    replay -- the worst possible Pattern-2 outcome on the reader side
    because downstream consumers (audit-report, GRC export) would never
    learn the replay was incomplete. W607-AH wraps the collector so a
    raise surfaces a ``pr_replay_collect_change_evidence_failed:`` marker
    on ``warnings_out`` AND the replay envelope still completes (with
    ``evidence_path: null`` / ``evidence_content_hash: null`` as the
    absent-artifact disclosure).

    This is the producer/consumer twin of W607-AE's W805 6-artifact
    bridge test on cmd_pr_bundle.
    """
    from roam.commands import cmd_pr_replay

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-collect-evidence-from-W607-AH")

    monkeypatch.setattr(cmd_pr_replay, "_collect_change_evidence", _raise)

    evidence_target = tmp_path / "evidence_out" / "evidence.json"
    result = _invoke_pr_replay(
        cli_runner,
        pr_replay_project,
        "--tier",
        "sample",
        "--evidence",
        str(evidence_target),
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    cce_markers = [m for m in top_wo if m.startswith("pr_replay_collect_change_evidence_failed:")]
    assert cce_markers, f"expected pr_replay_collect_change_evidence_failed: marker; got {top_wo!r}"
    # Absent-artifact disclosure: evidence_path / evidence_content_hash
    # are None when collection failed (Pattern 2: explicit absence
    # beats silence -- a downstream audit-report consumer reading
    # these fields sees the failure rather than a fabricated success).
    assert data["summary"].get("evidence_path") is None, (
        f"collect_change_evidence raised; expected evidence_path=None on "
        f"the envelope, got {data['summary'].get('evidence_path')!r}"
    )
    assert data["summary"].get("evidence_content_hash") is None, (
        f"collect_change_evidence raised; expected evidence_content_hash=None "
        f"on the envelope, got {data['summary'].get('evidence_content_hash')!r}"
    )
    assert data["summary"].get("partial_success") is True, (
        f"collect_change_evidence raise must flip partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) warnings_out lands in both summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_pr_replay_warnings_out_in_envelope(cli_runner, pr_replay_project, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_pr_replay

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AH")

    monkeypatch.setattr(cmd_pr_replay, "_run_postmortem", _raise)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("pr_replay_run_postmortem_failed:")]
    assert markers, f"expected pr_replay_run_postmortem_failed: marker; got {data['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-AH" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (6) partial_success flips when ANY helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_pr_replay_helper_raises(cli_runner, pr_replay_project, monkeypatch):
    """Any non-empty W607-AH bucket -> summary.partial_success = True."""
    from roam.commands import cmd_pr_replay

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AH")

    monkeypatch.setattr(cmd_pr_replay, "_run_postmortem", _raise)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, pr_replay_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AE contracts.
    """
    from roam.commands import cmd_pr_replay

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AH")

    monkeypatch.setattr(cmd_pr_replay, "_run_postmortem", _raise)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "run_postmortem guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("pr_replay_run_postmortem_failed:")]
    assert failure_markers, f"expected pr_replay_run_postmortem_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "pr_replay_run_postmortem_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- ``pr_replay_*`` not pr_bundle/pr_analyze/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_pr_replay_not_pr_bundle_or_pr_analyze(cli_runner, pr_replay_project, monkeypatch):
    """Every surfaced W607-AH marker uses the canonical ``pr_replay_*`` prefix.

    cmd_pr_replay is the consumer at the heart of the W805 reader family --
    distinct from sibling W607-* layers. Hard guard against accidental
    marker-prefix drift, particularly against the sibling W607-AE
    ``pr_bundle_*`` family it consumes.
    """
    from roam.commands import cmd_pr_replay

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AH")

    monkeypatch.setattr(cmd_pr_replay, "_run_postmortem", _raise)

    result = _invoke_pr_replay(cli_runner, pr_replay_project, "--tier", "sample")
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # Filter to substrate-CALL markers (have ``_failed:`` in the middle).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("pr_replay_"), (
            f"every surfaced W607-AH marker must use the ``pr_replay_*`` "
            f"prefix family (cmd_pr_replay scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers, particularly the
        # cmd_pr_bundle (W607-AE) producer this consumer reads from.
        for forbidden_prefix, sibling in (
            ("pr_bundle_", "cmd_pr_bundle W607-AE"),
            ("pr_analyze_", "cmd_pr_analyze W607-AA"),
            ("pr_risk_", "cmd_pr_risk W607-AB/Q"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("relate_", "cmd_relate W607-W"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) W607-AE pairing -- pr_replay markers can coexist with pr_bundle markers
# ---------------------------------------------------------------------------


def test_pr_replay_markers_coexist_with_pr_bundle_markers():
    """pr_replay_* markers do NOT collide with pr_bundle_* markers (closed enum).

    pr-bundle (W607-AE producer) emits markers with ``pr_bundle_*``
    prefix. pr-replay (W607-AH consumer) reads bundles emitted by
    pr-bundle and emits markers with ``pr_replay_*`` prefix. A replay
    processing a bundle that itself has W607-AE markers in its
    warnings_out should not cause prefix collisions -- the two are
    closed-enum distinct prefix families.
    """
    # Simulate a synthetic merged warnings_out array containing both
    # families. Each marker MUST be assignable to exactly one family by
    # prefix-matching alone.
    pr_bundle_marker = "pr_bundle_emit_slsa_l3_failed:RuntimeError:synthetic"
    pr_replay_marker = "pr_replay_collect_change_evidence_failed:RuntimeError:synthetic"

    merged = [pr_bundle_marker, pr_replay_marker]
    bundle_family = [m for m in merged if m.startswith("pr_bundle_") and "_failed:" in m]
    replay_family = [m for m in merged if m.startswith("pr_replay_") and "_failed:" in m]

    assert bundle_family == [pr_bundle_marker], bundle_family
    assert replay_family == [pr_replay_marker], replay_family
    # Cross-check: a pr_bundle_ marker MUST NOT start with pr_replay_
    # and vice versa (closed-enum prefix discipline). The accidental
    # ``pr_*`` shared root is the failure case W607-AH must prevent.
    assert not pr_bundle_marker.startswith("pr_replay_"), pr_bundle_marker
    assert not pr_replay_marker.startswith("pr_bundle_"), pr_replay_marker


# ---------------------------------------------------------------------------
# (10) Sibling parity -- W607-AE cmd_pr_bundle surface unchanged
# ---------------------------------------------------------------------------


def test_w607_ae_cmd_pr_bundle_unaffected():
    """Sibling parity guard: W607-AE cmd_pr_bundle source surface unchanged.

    W607-AH lands only in cmd_pr_replay. The W607-AE cmd_pr_bundle
    surface (``_w607ae_warnings_out`` accumulator + ``pr_bundle_*``
    marker emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_bundle.py"
    assert src_path.exists(), f"cmd_pr_bundle.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ae_warnings_out" in src, (
        "W607-AE accumulator removed from cmd_pr_bundle; W607-AH must not regress the sibling instrumentation."
    )
    assert "pr_bundle_{phase}_failed" in src, (
        "W607-AE marker prefix template removed from cmd_pr_bundle; W607-AH must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (11) Source-level guard: cmd_pr_replay carries the canonical W607-AH accumulator
# ---------------------------------------------------------------------------


def test_cmd_pr_replay_carries_w607ah_accumulator():
    """AST-level guard: cmd_pr_replay source carries the W607-AH accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    assert src_path.exists(), f"cmd_pr_replay.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ah_warnings_out" in src, (
        "W607-AH accumulator missing from cmd_pr_replay; the substrate-CALL marker plumbing has been removed."
    )
    assert "pr_replay_{phase}_failed" in src, (
        "W607-AH marker prefix template missing from cmd_pr_replay; check the "
        '`f"pr_replay_{phase}_failed:..."` line in _run_check_ah.'
    )
    # Parse-tree level: confirm _run_check_ah is defined inside pr_replay_cmd().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ah":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AH ``_run_check_ah`` helper not found in cmd_pr_replay AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (12) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_pr_replay substrate boundary is wrapped.

    W607-AH substrate inventory (in canonical order for the W805
    reader family):

    * run_postmortem            -- _run_postmortem(...)           (commits source)
    * aggregate_by_detector     -- _aggregate_by_detector(commits) (aggregation)
    * render_report             -- _render_report(...)            (markdown projection)
    * render_pdf                -- _render_pdf(...)               (PDF projection)
    * build_review_suggestions  -- _build_review_suggestions(...) (suggestion builder)
    * collect_change_evidence   -- _collect_change_evidence(...)  (W534 + W805 reader)
    * to_canonical_json         -- packet.to_canonical_json()     (W534 serialize)
    * render_evidence_markdown  -- _render_evidence_markdown(...) (companion projection)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_replay.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "run_postmortem",
        "aggregate_by_detector",
        "render_report",
        "render_pdf",
        "build_review_suggestions",
        "collect_change_evidence",
        "to_canonical_json",
        "render_evidence_markdown",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check_ah("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes. The actual file
        # indentation depth varies (8/12/16/20/24 spaces) depending on the
        # site's nesting; accept any of the canonical depths.
        same_line = f'_run_check_ah("{phase}"' in src
        multi_line = (
            f'_run_check_ah(\n        "{phase}"' in src
            or f'_run_check_ah(\n            "{phase}"' in src
            or f'_run_check_ah(\n                "{phase}"' in src
            or f'_run_check_ah(\n                    "{phase}"' in src
            or f'_run_check_ah(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AH _run_check_ah wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
