"""W607-AA -- ``cmd_pr_analyze`` threads ``warnings_out`` onto its envelope.

Twenty-seventh-in-batch W607 consumer-layer arc. Direct sibling of W607-Y
(cmd_critique diff-text-substrate axis) and W607-Z (cmd_diff shared-helper
axis). cmd_pr_analyze is the **high-impact PR recipe-composer / evidence-
compiler-relevant** variant -- consumes a diff (via ``--input`` / stdin /
``--staged`` / commit range / ``--diff-from-pr``), fans out to
``pr-prep`` (which composes diff + critique + pr-risk), scores AI-
likelihood, enforces ``.roam/rules.yml``, and aggregates into a single
INTENTIONAL / SAFE / REVIEW / BLOCK verdict suitable for posting as a
sticky PR comment. The CLI engine behind Roam Agent Review.

Substrate boundaries wrapped by W607-AA
---------------------------------------

Ten substrate-call sites in ``pr_analyze()`` get the canonical
``_run_check(phase, fn, *args)`` wrapper:

* ``acquire_diff``                    -- _acquire_diff(input_file, commit_range, staged, ...)
* ``capture_pr_prep``                 -- _capture_pr_prep(commit_range, high_callers)
* ``compute_ai_likelihood``           -- _compute_ai_likelihood(diff_text, ...)
* ``check_rules``                     -- _check_rules(diff_text, rules)
* ``inspect_prep_subcommand_failures``-- _inspect_prep_subcommand_failures(prep_payload)
* ``determine_verdict``               -- _determine_verdict(...)
* ``added_lines_by_file``             -- _added_lines_by_file(diff_text)
* ``capture_suggest_reviewers``       -- _capture_suggest_reviewers(touched_files, top)
* ``build_rationale``                 -- _build_rationale(...)
* ``apply_drift``                     -- _apply_drift(bundle, base_path, verdict, reasons)

Each raise becomes a ``pr_analyze_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607aa_warnings_out`` and the envelope still emits cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_pr_analyze's substrate-call sites are direct function invocations on
the module-level helpers extracted into ``roam.commands.pr_analyze.*``
sub-packages plus the local ``_capture_pr_prep`` / ``_compute_ai_likelihood``
/ ``_inspect_prep_subcommand_failures`` / ``_determine_verdict`` /
``_build_rationale`` / ``_apply_drift`` helpers. The dominant raise axis
is the helper-CALL boundary -- consistent with W607-N..Z. Each helper
can raise on a malformed diff, a YAML-shape change in rules.yml, a sub-
process timeout in the pr-prep capture, a fitness.yaml refactor, or a
schema drift in the prep envelope. The pre-existing ``_load_rules_yaml``
``ValueError`` branch is already structurally handled (the CLI exits
``EXIT_GATE_BLOCK`` on ``--rules-strict`` failure); W607-AA wraps the
remaining substrate boundaries so an unhandled raise becomes a
structured marker.

Marker family is ``pr_analyze_*`` -- NOT ``diff_*`` (W607-Z), NOT
``critique_*`` (W607-Y), NOT ``relate_*`` (W607-W), etc. The marker-
prefix discipline test pins this closed-enum distinction.

W805-HHHH SHARED-HELPER axis (pre-existing disconfirmation)
-----------------------------------------------------------

cmd_pr_analyze does NOT call ``get_changed_files`` directly -- it
consumes diff TEXT via ``_acquire_diff`` and bridges to ``pr-prep``,
which in turn invokes ``diff`` (which IS in the SHARED-HELPER family).
The W805-HHHH ``get_changed_files`` silent-empty fallback would
therefore surface UPSTREAM via the prep_payload (already handled by the
``inspect_prep_subcommand_failures`` boundary above). The W607-AA
plumbing on cmd_pr_analyze still wraps ``_acquire_diff`` so a raise in
the diff-acquisition path (subprocess timeout, file-not-found,
encoding error) is disclosed locally.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_pr_analyze has
several lazy local imports (e.g. ``from roam.output.errors import ...``
on usage-error branches, ``from roam.exit_codes import GateFailureError``)
which are genuine deferred-load imports (heavy machinery only needed
on bad-input / gate-failure paths), NOT cargo-cult cycle hedges. Left
untouched per W907.

Evidence-compiler note
----------------------

cmd_pr_analyze emits a ``ChangeEvidence``-relevant envelope (the PR
risk verdict + rationale + audit trail). A W607-AA marker surfaces
THROUGH any evidence collector that downstream consumes the envelope
because the marker rides ``warnings_out`` on the same JSON document --
no special collector wiring required. The pre-existing
``partial_success`` flag is already canonical in the evidence layer.

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
# Helpers -- invoke pr-analyze via the Click group (uses --json on group)
# ---------------------------------------------------------------------------


_DIFF_TEXT = (
    "diff --git a/src/auth.py b/src/auth.py\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/auth.py\n"
    "+++ b/src/auth.py\n"
    "@@ -1,5 +1,6 @@\n"
    " from src.models import User\n"
    " \n"
    " def verify_token(t):\n"
    "+    # tweak\n"
    "     return User('test')\n"
    " \n"
)


def _invoke_pr_analyze(
    runner: CliRunner,
    cwd,
    *extra,
    json_mode: bool = True,
    stdin: str | None = None,
):
    """Invoke ``roam pr-analyze`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("pr-analyze")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, input=stdin, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus + a diff target file
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def pr_analyze_project(tmp_path, monkeypatch):
    """Indexed corpus with a symbol the diff modifies."""
    proj = tmp_path / "pr_analyze_w607aa_project"
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
# (1) Happy path -- clean envelope omits W607-AA substrate markers
# ---------------------------------------------------------------------------


def test_pr_analyze_clean_envelope_omits_w607aa_markers(cli_runner, pr_analyze_project):
    """Clean pr-analyze on a healthy diff -> no W607-AA substrate markers.

    Hash-stable: an empty W607-AA bucket on the success path must produce
    an envelope without W607-AA substrate markers.
    """
    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    # pr-analyze can exit 0 (clean) or 5 (gated BLOCK); both are fine for
    # this test. We only care about marker shape.
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-analyze"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-AA substrate markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("pr_analyze_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean pr-analyze must NOT surface pr_analyze_<phase>_failed: markers; "
        f"got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) acquire_diff failure -> pr_analyze_acquire_diff_failed marker
# ---------------------------------------------------------------------------


def test_pr_analyze_acquire_diff_failure_marker_format(cli_runner, pr_analyze_project, monkeypatch):
    """If _acquire_diff raises, surface ``pr_analyze_acquire_diff_failed:``."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-acquire-diff-from-W607-AA")

    monkeypatch.setattr(cmd_pr_analyze, "_acquire_diff", _raise)

    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    aq_markers = [m for m in top_wo if m.startswith("pr_analyze_acquire_diff_failed:")]
    assert aq_markers, f"expected pr_analyze_acquire_diff_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in aq_markers), aq_markers
    assert any("synthetic-acquire-diff-from-W607-AA" in m for m in aq_markers), aq_markers


# ---------------------------------------------------------------------------
# (3) capture_pr_prep failure -> pr_analyze_capture_pr_prep_failed marker
# ---------------------------------------------------------------------------


def test_pr_analyze_capture_pr_prep_failure_marker_format(cli_runner, pr_analyze_project, monkeypatch):
    """If _capture_pr_prep raises, surface ``pr_analyze_capture_pr_prep_failed:``."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-prep-from-W607-AA")

    monkeypatch.setattr(cmd_pr_analyze, "_capture_pr_prep", _raise)

    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    prep_markers = [m for m in top_wo if m.startswith("pr_analyze_capture_pr_prep_failed:")]
    assert prep_markers, f"expected pr_analyze_capture_pr_prep_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (4) compute_ai_likelihood failure -> marker
# ---------------------------------------------------------------------------


def test_pr_analyze_compute_ai_likelihood_failure_marker_format(cli_runner, pr_analyze_project, monkeypatch):
    """If _compute_ai_likelihood raises, surface ``pr_analyze_compute_ai_likelihood_failed:``."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-ai-from-W607-AA")

    monkeypatch.setattr(cmd_pr_analyze, "_compute_ai_likelihood", _raise)

    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    ai_markers = [m for m in top_wo if m.startswith("pr_analyze_compute_ai_likelihood_failed:")]
    assert ai_markers, f"expected pr_analyze_compute_ai_likelihood_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (5) determine_verdict failure -> marker
# ---------------------------------------------------------------------------


def test_pr_analyze_determine_verdict_failure_marker_format(cli_runner, pr_analyze_project, monkeypatch):
    """If _determine_verdict raises, surface ``pr_analyze_determine_verdict_failed:``."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-verdict-from-W607-AA")

    monkeypatch.setattr(cmd_pr_analyze, "_determine_verdict", _raise)

    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    v_markers = [m for m in top_wo if m.startswith("pr_analyze_determine_verdict_failed:")]
    assert v_markers, f"expected pr_analyze_determine_verdict_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (6) warnings_out lands in both summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_pr_analyze_warnings_out_in_envelope(cli_runner, pr_analyze_project, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AA")

    monkeypatch.setattr(cmd_pr_analyze, "_compute_ai_likelihood", _raise)

    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("pr_analyze_compute_ai_likelihood_failed:")]
    assert markers, f"expected pr_analyze_compute_ai_likelihood_failed: marker; got {data['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-AA" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY pr-analyze helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_pr_analyze_helper_raises(cli_runner, pr_analyze_project, monkeypatch):
    """Any non-empty W607-AA bucket -> summary.partial_success = True."""
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AA")

    monkeypatch.setattr(cmd_pr_analyze, "_compute_ai_likelihood", _raise)

    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, pr_analyze_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..Z contracts.
    """
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AA")

    monkeypatch.setattr(cmd_pr_analyze, "_compute_ai_likelihood", _raise)

    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "compute_ai_likelihood guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("pr_analyze_compute_ai_likelihood_failed:")]
    assert failure_markers, f"expected pr_analyze_compute_ai_likelihood_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "pr_analyze_compute_ai_likelihood_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``pr_analyze_*`` not diff/critique/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_pr_analyze_not_diff_or_critique(cli_runner, pr_analyze_project, monkeypatch):
    """Every surfaced W607-AA marker uses the canonical ``pr_analyze_*`` prefix.

    cmd_pr_analyze is the high-impact PR recipe-composer variant --
    distinct from sibling W607-* layers. Hard guard against accidental
    marker-prefix drift.
    """
    from roam.commands import cmd_pr_analyze

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AA")

    monkeypatch.setattr(cmd_pr_analyze, "_compute_ai_likelihood", _raise)

    result = _invoke_pr_analyze(
        cli_runner,
        pr_analyze_project,
        stdin=_DIFF_TEXT,
    )
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # Filter to substrate-CALL markers (have ``_failed:`` in the middle).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("pr_analyze_"), (
            f"every surfaced W607-AA marker must use the ``pr_analyze_*`` "
            f"prefix family (cmd_pr_analyze scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("relate_", "cmd_relate W607-W"),
            ("deps_", "cmd_deps W607-V"),
            ("uses_", "cmd_uses W607-U"),
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("pr_risk_", "cmd_pr_risk W607-Q"),
            ("audit_", "cmd_audit W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("grep_", "cmd_grep W607-G"),
            ("history_", "cmd_history_grep W607-H"),
            ("refs_text_", "cmd_refs_text W607-I"),
            ("delete_check_", "cmd_delete_check W607-J"),
            ("search_", "cmd_search W607-E"),
            ("complete_", "cmd_complete W607-F"),
            ("semantic_", "cmd_search_semantic W607-A"),
            ("findings_query_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D"),
            ("retrieve_", "cmd_retrieve W607-B"),
            ("fan_", "cmd_fan W607-X"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (10) Sibling parity -- W607-Z cmd_diff surface unchanged
# ---------------------------------------------------------------------------


def test_w607_z_cmd_diff_unaffected():
    """Sibling parity guard: W607-Z cmd_diff source surface unchanged.

    W607-AA lands only in cmd_pr_analyze. The W607-Z cmd_diff surface
    (per-helper ``_run_check`` wrapper + ``_w607z_warnings_out``
    accumulator + ``diff_*`` marker emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diff.py"
    assert src_path.exists(), f"cmd_diff.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607z_warnings_out" in src, (
        "W607-Z accumulator removed from cmd_diff; W607-AA must not regress the sibling instrumentation."
    )
    assert "diff_{phase}_failed" in src, (
        "W607-Z marker prefix removed from cmd_diff; W607-AA must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (11) Source-level guard: cmd_pr_analyze carries the canonical W607-AA accumulator
# ---------------------------------------------------------------------------


def test_cmd_pr_analyze_carries_w607aa_accumulator():
    """AST-level guard: cmd_pr_analyze source carries the W607-AA accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_analyze.py"
    assert src_path.exists(), f"cmd_pr_analyze.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607aa_warnings_out" in src, (
        "W607-AA accumulator missing from cmd_pr_analyze; the substrate-CALL marker plumbing has been removed."
    )
    assert "pr_analyze_{phase}_failed" in src, (
        "W607-AA marker prefix template missing from cmd_pr_analyze; check the "
        '`f"pr_analyze_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside pr_analyze().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            # cmd_pr_analyze has a single _run_check defined inside pr_analyze;
            # other helper modules may define namesake helpers, but this AST
            # walk only covers cmd_pr_analyze.py.
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AA ``_run_check`` helper not found in cmd_pr_analyze AST; the "
        "per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (12) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_pr_analyze substrate boundary is wrapped.

    W607-AA substrate inventory (top boundaries):

    * acquire_diff                    -- _acquire_diff(...)
    * capture_pr_prep                 -- _capture_pr_prep(...)
    * compute_ai_likelihood           -- _compute_ai_likelihood(...)
    * check_rules                     -- _check_rules(...)
    * inspect_prep_subcommand_failures-- _inspect_prep_subcommand_failures(...)
    * determine_verdict               -- _determine_verdict(...)
    * added_lines_by_file             -- _added_lines_by_file(...)
    * capture_suggest_reviewers       -- _capture_suggest_reviewers(...)
    * build_rationale                 -- _build_rationale(...)
    * apply_drift                     -- _apply_drift(...)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_analyze.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "acquire_diff",
        "capture_pr_prep",
        "compute_ai_likelihood",
        "check_rules",
        "inspect_prep_subcommand_failures",
        "determine_verdict",
        "added_lines_by_file",
        "capture_suggest_reviewers",
        "build_rationale",
        "apply_drift",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes. The actual file
        # indentation depth varies (8/12/16/20/24 spaces) depending on the
        # site's nesting; accept any of the canonical depths.
        same_line = f'_run_check("{phase}"' in src
        multi_line = (
            f'_run_check(\n        "{phase}"' in src
            or f'_run_check(\n            "{phase}"' in src
            or f'_run_check(\n                "{phase}"' in src
            or f'_run_check(\n                    "{phase}"' in src
            or f'_run_check(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AA _run_check wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
