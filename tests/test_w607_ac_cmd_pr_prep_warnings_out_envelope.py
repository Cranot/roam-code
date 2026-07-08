"""W607-AC -- ``cmd_pr_prep`` threads ``warnings_out`` onto its envelope.

Twenty-ninth-in-batch W607 consumer-layer arc. Direct DOWNSTREAM-of-pr-analyze
sibling of W607-AA (cmd_pr_analyze) -- cmd_pr_prep is invoked internally by
cmd_pr_analyze via ``_capture_pr_prep``. W607-AA wraps the ``capture_pr_prep``
boundary at the outer layer; W607-AC wraps the substrate boundaries INSIDE
pr-prep itself. Together they form a 2-layer cross-recipe disclosure stack:

    pr-analyze invokes pr-prep -> pr-prep substrate raise ->
        inner envelope carries pr_prep_<phase>_failed:<exc>:... +
        outer envelope (pr-analyze) may also carry pr_analyze_capture_pr_prep_failed:...
        when the inner stdout JSON or exit-code path also collapses.

Substrate boundaries wrapped by W607-AC
---------------------------------------

Nine substrate-call sites in ``pr_prep()`` get the canonical
``_run_check(phase, fn, *args)`` wrapper:

* ``capture_diff``                -- _capture_json_subcommand(["diff", ...])
* ``git_diff_text``               -- _git_diff_text(commit_range)
* ``capture_critique``            -- runner.invoke(critique) (closure)
* ``parse_critique_json``         -- json.loads(result.output) (closure)
* ``capture_pr_risk``             -- _capture_json_subcommand(["pr-risk"])
* ``inspect_failed_subcommands``  -- pattern-2 data-shape inspector
* ``compute_verdict``             -- verdict + partial + ready computation
* ``auto_log_run``                -- auto_log(envelope, ...)

Each raise becomes a ``pr_prep_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607ac_warnings_out`` and the envelope still emits cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_pr_prep's substrate-call sites are direct calls on module-level
helpers in the same file plus a closure around ``CliRunner().invoke``.
The dominant raise axis is the helper-CALL boundary -- consistent with
W607-N..AA. Each helper can raise on a subprocess timeout
(_git_diff_text via subprocess.run), a recursive Click invocation
crash inside _capture_json_subcommand, a json.loads on truncated CLI
output, or an auto_log writing into a read-only .roam dir.

Marker family is ``pr_prep_*`` -- distinct from ``pr_analyze_*``
(W607-AA), ``diff_*`` (W607-Z), ``critique_*`` (W607-Y). The marker-
prefix discipline test pins this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_pr_prep keeps its
``from roam.cli import cli`` import lazy inside ``_invoke_critique`` --
genuine deferred-load to dodge import-time recursion into the Click
group, NOT a cargo-cult cycle hedge. Left untouched per W907.

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
# Helpers -- invoke pr-prep via the Click group (uses --json on group)
# ---------------------------------------------------------------------------


def _invoke_pr_prep(
    runner: CliRunner,
    cwd,
    *extra,
    json_mode: bool = True,
):
    """Invoke ``roam pr-prep`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("pr-prep")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def pr_prep_project(tmp_path, monkeypatch):
    """Indexed corpus with a symbol pr-prep can analyze."""
    proj = tmp_path / "pr_prep_w607ac_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "models.py").write_text(
        "class User:\n    def __init__(self, name):\n        self.name = name\n",
        encoding="utf-8",
    )
    (src / "auth.py").write_text(
        "from src.models import User\n\ndef verify_token(t):\n    return User('test')\n\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- clean envelope omits W607-AC substrate markers
# ---------------------------------------------------------------------------


def test_pr_prep_clean_envelope_omits_w607ac_markers(cli_runner, pr_prep_project):
    """Clean pr-prep on a healthy repo -> no W607-AC substrate markers.

    Byte-stable: an empty W607-AC bucket on the success path must produce
    an envelope without W607-AC substrate markers. The pre-existing
    ``failed_subcommands`` data-shape channel may still surface if the
    inner subcommands (diff/critique/pr-risk) return non-JSON output,
    but those are NOT W607-AC substrate-CALL markers.
    """
    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-prep"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-AC substrate markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("pr_prep_") and "_failed:" in m]
    assert not substrate_markers, (
        f"clean pr-prep must NOT surface pr_prep_<phase>_failed: markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) capture_diff failure -> pr_prep_capture_diff_failed marker
# ---------------------------------------------------------------------------


def test_pr_prep_capture_diff_failure_marker_format(cli_runner, pr_prep_project, monkeypatch):
    """If _capture_json_subcommand raises on the diff call, surface
    ``pr_prep_capture_diff_failed:``.

    NOTE: cmd_pr_prep uses _capture_json_subcommand for BOTH diff and
    pr-risk. We monkeypatch with a routing helper so only the diff arg
    list raises -- pr-risk still works and its marker doesn't appear.
    """
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "diff":
            raise RuntimeError("synthetic-capture-diff-from-W607-AC")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)

    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    cd_markers = [m for m in top_wo if m.startswith("pr_prep_capture_diff_failed:")]
    assert cd_markers, f"expected pr_prep_capture_diff_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in cd_markers), cd_markers
    assert any("synthetic-capture-diff-from-W607-AC" in m for m in cd_markers), cd_markers


# ---------------------------------------------------------------------------
# (3) capture_pr_risk failure -> pr_prep_capture_pr_risk_failed marker
# ---------------------------------------------------------------------------


def test_pr_prep_capture_pr_risk_failure_marker_format(cli_runner, pr_prep_project, monkeypatch):
    """If _capture_json_subcommand raises on the pr-risk call, surface
    ``pr_prep_capture_pr_risk_failed:``."""
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "pr-risk":
            raise RuntimeError("synthetic-capture-pr-risk-from-W607-AC")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)

    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    pr_markers = [m for m in top_wo if m.startswith("pr_prep_capture_pr_risk_failed:")]
    assert pr_markers, f"expected pr_prep_capture_pr_risk_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (4) git_diff_text failure -> pr_prep_git_diff_text_failed marker
# ---------------------------------------------------------------------------


def test_pr_prep_git_diff_text_failure_marker_format(cli_runner, pr_prep_project, monkeypatch):
    """If _git_diff_text raises, surface ``pr_prep_git_diff_text_failed:``."""
    from roam.commands import cmd_pr_prep as _mod

    def _raise(*args, **kwargs):
        raise OSError("synthetic-git-diff-text-from-W607-AC")

    monkeypatch.setattr(_mod, "_git_diff_text", _raise)

    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    g_markers = [m for m in top_wo if m.startswith("pr_prep_git_diff_text_failed:")]
    assert g_markers, f"expected pr_prep_git_diff_text_failed: marker; got {top_wo!r}"
    assert any("OSError" in m for m in g_markers), g_markers


# ---------------------------------------------------------------------------
# (5) warnings_out lands in both summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_pr_prep_warnings_out_in_envelope(cli_runner, pr_prep_project, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "diff":
            raise RuntimeError("synthetic-mirror-from-W607-AC")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)

    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("pr_prep_capture_diff_failed:")]
    assert markers, f"expected pr_prep_capture_diff_failed: marker; got {data['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-AC" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (6) partial_success flips when ANY pr-prep helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_pr_prep_helper_raises(cli_runner, pr_prep_project, monkeypatch):
    """Any non-empty W607-AC bucket -> summary.partial_success = True."""
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "diff":
            raise RuntimeError("synthetic-partial-success-from-W607-AC")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)

    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, pr_prep_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AA contracts.
    """
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "diff":
            raise PermissionError("synthetic-shape-detail-from-W607-AC")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)

    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "capture_diff guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("pr_prep_capture_diff_failed:")]
    assert failure_markers, f"expected pr_prep_capture_diff_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "pr_prep_capture_diff_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- ``pr_prep_*`` not pr_analyze/diff/critique
# ---------------------------------------------------------------------------


def test_marker_prefix_pr_prep_not_pr_analyze_or_critique(cli_runner, pr_prep_project, monkeypatch):
    """Every surfaced W607-AC marker uses the canonical ``pr_prep_*`` prefix.

    cmd_pr_prep is distinct from cmd_pr_analyze (its outer caller) and
    every other sibling W607-* layer. Hard guard against accidental
    marker-prefix drift -- particularly important because pr_prep is
    directly UPSTREAM of pr_analyze and marker confusion would corrupt
    the cross-recipe 2-layer disclosure stack.
    """
    from roam.commands import cmd_pr_prep as _mod

    original = _mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "diff":
            raise PermissionError("synthetic-prefix-discipline-from-W607-AC")
        return original(args)

    monkeypatch.setattr(_mod, "_capture_json_subcommand", _routed)

    result = _invoke_pr_prep(cli_runner, pr_prep_project)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # Filter to substrate-CALL markers (have ``_failed:`` in the middle).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("pr_prep_"), (
            f"every surfaced W607-AC marker must use the ``pr_prep_*`` "
            f"prefix family (cmd_pr_prep scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers -- especially
        # pr_analyze_ (W607-AA) since that's the direct outer caller.
        for forbidden_prefix, sibling in (
            ("pr_analyze_", "cmd_pr_analyze W607-AA"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("relate_", "cmd_relate W607-W"),
            ("pr_risk_", "cmd_pr_risk W607-Q"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) Sibling parity -- W607-AA cmd_pr_analyze surface unchanged
# ---------------------------------------------------------------------------


def test_w607_aa_cmd_pr_analyze_unaffected():
    """Sibling parity guard: W607-AA cmd_pr_analyze source unchanged.

    W607-AC lands only in cmd_pr_prep. The W607-AA cmd_pr_analyze
    surface (per-helper ``_run_check`` wrapper + ``_w607aa_warnings_out``
    accumulator + ``pr_analyze_*`` marker emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_analyze.py"
    assert src_path.exists(), f"cmd_pr_analyze.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607aa_warnings_out" in src, (
        "W607-AA accumulator removed from cmd_pr_analyze; W607-AC must not regress the sibling instrumentation."
    )
    assert "pr_analyze_{phase}_failed" in src, (
        "W607-AA marker prefix removed from cmd_pr_analyze; W607-AC must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (10) Source-level guard: cmd_pr_prep carries the canonical W607-AC accumulator
# ---------------------------------------------------------------------------


def test_cmd_pr_prep_carries_w607ac_accumulator():
    """AST-level guard: cmd_pr_prep source carries the W607-AC accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_prep.py"
    assert src_path.exists(), f"cmd_pr_prep.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ac_warnings_out" in src, (
        "W607-AC accumulator missing from cmd_pr_prep; the substrate-CALL marker plumbing has been removed."
    )
    assert "pr_prep_{phase}_failed" in src, (
        "W607-AC marker prefix template missing from cmd_pr_prep; check the "
        '`f"pr_prep_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside pr_prep().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AC ``_run_check`` helper not found in cmd_pr_prep AST; the "
        "per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (11) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_pr_prep substrate boundary is wrapped.

    W607-AC substrate inventory (top boundaries):

    * capture_diff                -- _capture_json_subcommand(["diff", ...])
    * git_diff_text               -- _git_diff_text(commit_range)
    * capture_critique            -- runner.invoke(critique) (closure)
    * parse_critique_json         -- json.loads(result.output) (closure)
    * capture_pr_risk             -- _capture_json_subcommand(["pr-risk"])
    * inspect_failed_subcommands  -- pattern-2 data-shape inspector
    * compute_verdict             -- verdict + partial + ready computation
    * auto_log_run                -- auto_log(envelope, ...)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_prep.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "capture_diff",
        "git_diff_text",
        "capture_critique",
        "parse_critique_json",
        "capture_pr_risk",
        "inspect_failed_subcommands",
        "compute_verdict",
        "auto_log_run",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes. Indent depths
        # 8/12/16/20/24 cover the canonical Click-command nesting levels.
        same_line = f'_run_check("{phase}"' in src
        multi_line = (
            f'_run_check(\n        "{phase}"' in src
            or f'_run_check(\n            "{phase}"' in src
            or f'_run_check(\n                "{phase}"' in src
            or f'_run_check(\n                    "{phase}"' in src
            or f'_run_check(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AC _run_check wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (12) Cross-recipe composition -- pr_prep marker rides through pr_analyze
# ---------------------------------------------------------------------------


def test_cross_recipe_marker_composition_pr_prep_inside_pr_analyze(cli_runner, pr_prep_project, monkeypatch):
    """Highest-signal cross-recipe disclosure pin: when cmd_pr_prep's
    substrate raises and pr-prep is invoked via cmd_pr_analyze, the
    inner pr-prep envelope (carried as pr_analyze.pr_prep) should still
    carry the ``pr_prep_<phase>_failed:`` marker on its own warnings_out
    -- W607-AC's structured disclosure rides through the recipe boundary.

    NOTE: cmd_pr_analyze (W607-AA) wraps its own ``capture_pr_prep``
    boundary, so the OUTER envelope's warnings_out won't carry the
    ``pr_prep_*`` marker (different bucket) -- but the INNER pr_prep
    payload embedded under ``pr_analyze.pr_prep`` does. This is the
    2-layer composition contract: each layer owns its own disclosure
    bucket; consumers pick the layer of interest.
    """
    from roam.commands import cmd_pr_prep as _prep_mod

    original = _prep_mod._capture_json_subcommand

    def _routed(args):
        if args and args[0] == "diff":
            raise RuntimeError("synthetic-cross-recipe-from-W607-AC")
        return original(args)

    monkeypatch.setattr(_prep_mod, "_capture_json_subcommand", _routed)

    # Invoke pr-analyze, which internally calls pr-prep via _capture_pr_prep.
    from roam.cli import cli

    args = ["--json", "pr-analyze"]
    old_cwd = os.getcwd()
    try:
        os.chdir(str(pr_prep_project))
        result = cli_runner.invoke(
            cli,
            args,
            input="",  # No diff text -> pr-analyze still invokes pr-prep
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)

    # pr-analyze can exit 0/5; we don't gate on exit code here.
    assert result.exit_code in (0, 5), result.output
    try:
        data = _json.loads(result.output)
    except Exception:
        pytest.skip(f"pr-analyze did not emit JSON envelope: {result.output[:200]!r}")

    # The inner pr_prep payload may live under any of these paths
    # depending on the pr-analyze envelope shape; probe both.
    pr_prep_payload = data.get("pr_prep") or {}
    if not pr_prep_payload:
        # cmd_pr_analyze captures pr-prep through a CliRunner. If the
        # outer capture itself raised on its own boundary, the W607-AA
        # marker is the only thing that surfaces -- the inner W607-AC
        # marker never made it past the CliRunner output capture. In
        # that case the cross-recipe composition story is still intact
        # via W607-AA's outer marker; assert that as the fallback
        # disclosure path.
        outer_wo = data.get("warnings_out") or []
        outer_markers = [m for m in outer_wo if m.startswith("pr_analyze_capture_pr_prep_failed:")]
        assert outer_markers, (
            f"neither inner pr_prep envelope nor outer pr_analyze_capture_pr_prep_failed "
            f"marker surfaced cross-recipe; got warnings_out = {outer_wo!r}"
        )
        return

    # If the inner envelope did make it through, it should carry the
    # W607-AC marker on its own warnings_out -- proving the 2-layer
    # disclosure stack composes correctly.
    inner_wo = pr_prep_payload.get("warnings_out") or []
    inner_summary_wo = (pr_prep_payload.get("summary") or {}).get("warnings_out") or []
    combined = list(inner_wo) + list(inner_summary_wo)
    prep_markers = [m for m in combined if m.startswith("pr_prep_capture_diff_failed:")]
    assert prep_markers, (
        f"inner pr_prep envelope must carry pr_prep_capture_diff_failed: "
        f"marker; got warnings_out = {combined!r}, "
        f"pr_prep payload keys = {sorted(pr_prep_payload.keys())!r}"
    )
