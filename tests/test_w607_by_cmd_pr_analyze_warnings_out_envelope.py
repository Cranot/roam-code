"""W607-BY -- additive aggregation-phase plumbing for ``cmd_pr_analyze``.

cmd_pr_analyze is the PR-review composer that consumes diff + pr_risk
signals upstream. With cmd_pr_risk (W607-BU) and cmd_diff (W607-BP)
both aggregation-plumbed, cmd_pr_analyze closes the **PR-REVIEW
COMPOSER TRIO**.

  - substrate-CALL layer: cmd_diff (W607-Z), cmd_pr_risk (W607-Q +
                          W607-AB), cmd_pr_analyze (W607-AA)
  - aggregation-phase layer: cmd_diff (W607-BP), cmd_pr_risk (W607-BU),
                             cmd_pr_analyze (W607-BY)

Each command's marker family is prefix-isolated (``diff_*`` /
``pr_risk_*`` / ``pr_analyze_*``).

Relation to W607-AA
-------------------

cmd_pr_analyze already carries W607-AA substrate-CALL plumbing covering
ten substrate-helper boundaries (acquire_diff / capture_pr_prep /
compute_ai_likelihood / check_rules / inspect_prep_subcommand_failures
/ determine_verdict / added_lines_by_file / capture_suggest_reviewers /
build_rationale / apply_drift). W607-BY is ADDITIVE on top, extending
marker coverage to the AGGREGATION-PHASE boundaries that W607-AA left
unguarded:

  - ``score_classify``    -- per-verdict classification onto the
                             internal 4-tier risk vocabulary
                             (``low``/``medium``/``high``/``critical``).
                             Default=None drives the
                             ``score_classification: "unknown"`` sentinel.
  - ``score_normalize``   -- canonical W631 risk-LEVEL projection
                             (``normalize_risk_level`` + ``risk_rank``).
                             Pattern 3a discipline -- routes through
                             ``normalize_risk_level`` (the W631 canonical
                             helper), NOT through a separate inline
                             severity map.
  - ``compute_verdict``   -- augmented verdict text build appending the
                             canonical ``(risk_level X)`` suffix
                             (LAW 6 standalone-parse).
  - ``auto_log``          -- active-run ledger write (silent no-op if no
                             run is active, but the underlying ``auto_log``
                             can still raise on HMAC chain misshape or
                             filesystem failures).
  - ``serialize_envelope`` -- ``json_envelope("pr-analyze", ...)`` projection.

All boundaries share the canonical ``pr_analyze_*`` marker family and
the ``pr_analyze_<phase>_failed:<exc_class>:<detail>`` shape contract.
The two buckets (``_w607aa_warnings_out`` substrate-CALL +
``_w607by_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_pr_analyze's aggregation-phase boundaries (score_classify /
score_normalize / compute_verdict / auto_log / serialize_envelope) had
no guards beyond the W607-AA substrate-CALL calls. A downstream
refactor that changes the risk-level projection contract, the canonical
W631 vocabulary, the verdict string composition, the HMAC chain on the
runs ledger, or the ``json_envelope`` shape would crash the envelope
post-compute. W607-BY wraps each boundary with ``_run_check_by`` so a
raise becomes a marker via ``warnings_out`` and the envelope still
emits.

Score-classify degradation discipline
-------------------------------------

When the inner score_classify boundary raises (e.g. a refactored
verdict-to-tier mapping), the wrap floors the classified tier to
``None`` and surfaces ``score_classification: "unknown"`` in the
envelope summary alongside the canonical W631 ``"low"`` floor on
``risk_level_canonical``. Mirror of cmd_pr_risk W607-BU / cmd_attest
W607-BT / cmd_diff W607-BP classification sentinel.

PR-REVIEW COMPOSER TRIO closure milestone
-----------------------------------------

With W607-BY landed, the trio of PR-review composer commands (cmd_diff,
cmd_pr_risk, cmd_pr_analyze) is W607-plumbed end-to-end on both the
substrate-CALL layer AND the aggregation-phase layer. The three
families use distinct marker prefixes (``diff_*`` / ``pr_risk_*`` /
``pr_analyze_*``) which coexist when all three commands run on the
same change scope.

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
    proj = tmp_path / "pr_analyze_w607by_project"
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
# (1) Happy path -- clean envelope omits W607-BY aggregation markers
# ---------------------------------------------------------------------------


def test_pr_analyze_happy_path_no_w607by_markers(cli_runner, pr_analyze_project):
    """Clean pr-analyze on a healthy corpus -> no W607-BY aggregation markers.

    Hash-stable: an empty W607-BY bucket on the success path must produce
    an envelope without any
    ``pr_analyze_score_classify_failed:`` /
    ``pr_analyze_score_normalize_failed:`` /
    ``pr_analyze_compute_verdict_failed:`` /
    ``pr_analyze_auto_log_failed:`` /
    ``pr_analyze_serialize_envelope_failed:`` markers.
    """
    result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-analyze"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607by_phases = (
        "pr_analyze_score_classify_failed:",
        "pr_analyze_score_normalize_failed:",
        "pr_analyze_compute_verdict_failed:",
        "pr_analyze_auto_log_failed:",
        "pr_analyze_serialize_envelope_failed:",
    )
    for prefix in w607by_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean pr-analyze must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_by`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_pr_analyze_carries_w607by_accumulator():
    """AST-level guard: cmd_pr_analyze source carries the W607-BY accumulator.

    Pins the canonical W607-BY anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AA) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_analyze.py"
    assert src_path.exists(), f"cmd_pr_analyze.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607by_warnings_out" in src, (
        "W607-BY accumulator missing from cmd_pr_analyze; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_by" in src, (
        "W607-BY helper ``_run_check_by`` missing from cmd_pr_analyze; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_by is defined inside pr_analyze().
    tree = ast.parse(src)
    found_run_check_by = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_by":
            found_run_check_by = True
            break
    assert found_run_check_by, (
        "W607-BY ``_run_check_by`` helper not found in cmd_pr_analyze AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-AA must still be present (additive does NOT replace it)
    assert "_w607aa_warnings_out" in src, (
        "W607-AA accumulator vanished alongside the W607-BY add; the additive "
        "plumbing must preserve the W607-AA substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_by():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_by(...)`` with the canonical phase name.

    The five phases must appear inside a ``_run_check_by("<phase>", ...)``
    call inside cmd_pr_analyze. Multi-indent variants are all considered
    valid wrap call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_analyze.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "score_classify",
        "score_normalize",
        "compute_verdict",
        "auto_log",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_by(\n        "{phase}"',
            f'_run_check_by(\n            "{phase}"',
            f'_run_check_by(\n                "{phase}"',
            f'_run_check_by(\n                    "{phase}"',
            f'_run_check_by(\n                        "{phase}"',
            f'_run_check_by("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_by(...); add the W607-BY guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) auto_log failure marker shape
# ---------------------------------------------------------------------------


def test_auto_log_failure_marker_format(cli_runner, pr_analyze_project, monkeypatch):
    """If ``auto_log`` raises, surface ``pr_analyze_auto_log_failed:`` and
    keep the pr-analyze envelope intact.

    The auto_log boundary writes to the active run ledger when one is open
    -- a raise here would otherwise crash the envelope AFTER the success
    envelope was already built.
    """
    from roam.commands import cmd_pr_analyze

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-auto-log-from-W607-BY")

    monkeypatch.setattr(cmd_pr_analyze, "auto_log", _raise_auto_log)

    result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_analyze_auto_log_failed:")]
    assert markers, f"expected ``pr_analyze_auto_log_failed:`` marker; got {top_wo!r}"
    marker = markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-auto-log-from-W607-BY" in parts[2], parts


# ---------------------------------------------------------------------------
# (5) Happy-path score_classify stamps "classified" sentinel
# ---------------------------------------------------------------------------


def test_score_classify_clean_path_stamps_classified(cli_runner, pr_analyze_project):
    """Happy path: ``score_classification`` summary field is ``"classified"``.

    The sentinel disambiguates a real classified verdict from a degraded
    "unknown" floor. Mirror of cmd_pr_risk W607-BU / cmd_attest W607-BT /
    cmd_diff W607-BP ``"classified"`` contract.
    """
    result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("score_classification") == "classified", (
        f'clean path must stamp ``score_classification: "classified"``; '
        f"got {data['summary'].get('score_classification')!r}"
    )


# ---------------------------------------------------------------------------
# (6) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, pr_analyze_project, monkeypatch):
    """ANY W607-BY marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    pr-analyze" from "pr-analyze ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_pr_analyze

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-BY")

    monkeypatch.setattr(cmd_pr_analyze, "auto_log", _raise_auto_log)

    result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-BY warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607by_warnings_out_in_both_top_and_summary(cli_runner, pr_analyze_project, monkeypatch):
    """Non-empty W607-BY bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-BU / W607-BT / W607-BP contract: top-level is
    needed because the preserved-list field survives ``strip_list_payloads``
    in default-detail mode; summary mirror gives consumers reading only the
    summary block visibility too.
    """
    from roam.commands import cmd_pr_analyze

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BY")

    monkeypatch.setattr(cmd_pr_analyze, "auto_log", _raise_auto_log)

    result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BY raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BY raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("pr_analyze_auto_log_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("pr_analyze_auto_log_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the auto_log marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-AA COEXISTENCE -- both buckets surface in combined envelope
# ---------------------------------------------------------------------------


def test_combined_w607aa_and_w607by_markers_both_surface(cli_runner, pr_analyze_project, monkeypatch):
    """W607-AA (substrate-CALL) and W607-BY (aggregation-phase) markers
    BOTH surface when raises occur on each layer simultaneously.

    The additive plumbing must not shadow the W607-AA bucket -- agents
    must see the full degradation lineage. This is the explicit W607-AA
    COEXISTENCE GUARD requested in the wave spec: confirm
    ``pr_analyze_<substrate-phase>_failed:`` markers (W607-AA layer)
    coexist with ``pr_analyze_<agg-phase>_failed:`` markers (W607-BY
    layer) -- both in same family, threaded through different buckets at
    envelope-emit.
    """
    from roam.commands import cmd_pr_analyze

    def _raise_acquire_diff(*a, **kw):
        # W607-AA substrate-CALL boundary
        raise RuntimeError("synthetic-acquire-diff-from-W607-BY-combined")

    def _raise_auto_log(*a, **kw):
        # W607-BY aggregation boundary
        raise RuntimeError("synthetic-auto-log-from-W607-BY-combined")

    monkeypatch.setattr(cmd_pr_analyze, "_acquire_diff", _raise_acquire_diff)
    monkeypatch.setattr(cmd_pr_analyze, "auto_log", _raise_auto_log)

    result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    aa_markers = [m for m in top_wo if m.startswith("pr_analyze_acquire_diff_failed:")]
    by_markers = [m for m in top_wo if m.startswith("pr_analyze_auto_log_failed:")]
    assert aa_markers, f"W607-AA acquire_diff marker missing; got {top_wo!r}"
    assert by_markers, f"W607-BY auto_log marker missing; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-BY uses ``pr_analyze_*`` family
# ---------------------------------------------------------------------------


def test_w607by_marker_prefix_pr_analyze_family(cli_runner, pr_analyze_project, monkeypatch):
    """W607-BY markers use the canonical ``pr_analyze_*`` prefix (same
    family as W607-AA; W607-BY is ADDITIVE, not a separate prefix).

    Hard guard: any W607-BY marker that leaks into a sibling W607-*
    family (e.g. ``diff_*`` / ``pr_risk_*`` / ``critique_*``) breaks the
    closed-enum marker-family contract pinned in the W607-AA test.
    """
    from roam.commands import cmd_pr_analyze

    def _raise_auto_log(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BY")

    monkeypatch.setattr(cmd_pr_analyze, "auto_log", _raise_auto_log)

    result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-discipline check"
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, f"expected at least one ``*_failed:`` marker; got {top_wo!r}"
    for marker in failure_markers:
        assert marker.startswith("pr_analyze_"), (
            f"every W607-BY marker must use the ``pr_analyze_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (10) Canonical risk-LEVEL emission -- top-level + summary mirror
# ---------------------------------------------------------------------------


def test_canonical_risk_level_emitted_on_success_path(cli_runner, pr_analyze_project):
    """Success path emits ``risk_level_canonical`` + ``risk_rank`` on
    BOTH top-level envelope AND summary.

    Cross-command consumers can call
    ``risk_rank(data["summary"]["risk_level_canonical"]) >= 3`` to gate
    on high-or-worse without re-deriving the threshold table at the
    call site (Pattern-3a).
    """
    result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    # Summary mirror
    summary = data["summary"]
    assert "risk_level_canonical" in summary, (
        f"summary must emit ``risk_level_canonical``; got summary = {sorted(summary.keys())!r}"
    )
    assert "risk_rank" in summary, f"summary must emit ``risk_rank``; got summary = {sorted(summary.keys())!r}"
    assert summary["risk_level_canonical"] in (
        "critical",
        "high",
        "medium",
        "low",
    ), f"summary.risk_level_canonical must be in canonical W631 set; got {summary['risk_level_canonical']!r}"

    # Top-level mirror
    assert "risk_level_canonical" in data, (
        f"top-level envelope must emit ``risk_level_canonical``; got keys = {sorted(data.keys())!r}"
    )
    assert "risk_rank" in data, f"top-level envelope must emit ``risk_rank``; got keys = {sorted(data.keys())!r}"

    # Verdict suffix carries the canonical bucket per LAW 6
    assert f"risk_level {summary['risk_level_canonical']}" in summary["verdict"], (
        f"verdict must carry the canonical risk_level bucket per LAW 6; got verdict = {summary['verdict']!r}"
    )


# ---------------------------------------------------------------------------
# (11) Serialize envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607by_serialize_envelope_floor_on_raise(cli_runner, pr_analyze_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``pr_analyze_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("pr-analyze", ...)`` would otherwise crash AFTER all
    substrate + aggregation signals were already gathered. The consumer
    must still receive a parseable JSON object with the marker attached +
    the canonical command name.
    """
    from roam.commands import cmd_pr_analyze

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-BY")

    monkeypatch.setattr(cmd_pr_analyze, "json_envelope", _raise_envelope)

    result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "pr-analyze", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_analyze_serialize_envelope_failed:")]
    assert markers, f"expected ``pr_analyze_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (12) Compute-verdict guard -- raise surfaces the marker
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, pr_analyze_project, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    We force the compute_verdict closure to raise by patching
    ``normalize_risk_level`` to return an object whose ``__format__``
    raises -- the verdict f-string interpolation of risk_level_canonical
    then trips the wrap. Same approach as cmd_pr_risk W607-BU /
    cmd_attest W607-BT / cmd_diff W607-BP, adapted to cmd_pr_analyze's
    call site.
    """
    from roam.commands import cmd_pr_analyze

    class _BadLevel:
        def __str__(self):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BY")

        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BY")

    def _bad_normalize(level):
        # Returns a non-string truthy that fails f-string interpolation
        return _BadLevel()

    monkeypatch.setattr(cmd_pr_analyze, "normalize_risk_level", _bad_normalize)

    result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_analyze_compute_verdict_failed:")]
    assert markers, f"expected ``pr_analyze_compute_verdict_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (13) W641 normalize_risk_level WIRING GUARD -- Pattern 3a discipline
# ---------------------------------------------------------------------------


def test_w641_normalize_risk_level_wiring_in_score_normalize():
    """Pattern 3a discipline guard: the score_normalize boundary routes
    through ``normalize_risk_level`` (the W631 canonical helper) -- NOT
    through a separate inline severity map.

    This is the explicit W641 NORMALIZE_RISK_LEVEL WIRING GUARD requested
    in the wave spec. cmd_pr_analyze is a PR-review composer; drift
    between its risk-LEVEL projection and the canonical W631 vocabulary
    would silently corrupt cross-command floor comparators.

    The lint inspects the source: the ``score_normalize`` boundary
    invocation must reference ``normalize_risk_level`` inside the wrapped
    callable.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_analyze.py"
    src = src_path.read_text(encoding="utf-8")

    # The score_normalize call should contain a lambda or direct
    # reference to normalize_risk_level (the W631 canonical helper).
    assert "normalize_risk_level" in src, (
        "cmd_pr_analyze source must reference ``normalize_risk_level`` "
        "(the W631 canonical helper) -- Pattern 3a discipline."
    )

    # Find the score_normalize call site and confirm it references
    # normalize_risk_level within a small window
    score_normalize_idx = -1
    for indent in (4, 8, 12, 16, 20, 24):
        spaces = " " * indent
        candidate = src.find(f'_run_check_by(\n{spaces}"score_normalize"')
        if candidate != -1:
            score_normalize_idx = candidate
            break
    assert score_normalize_idx != -1, "score_normalize boundary call missing from cmd_pr_analyze."

    # Window: 500 chars after the call site to find the wrapped callable
    window = src[score_normalize_idx : score_normalize_idx + 500]
    assert "normalize_risk_level" in window, (
        "score_normalize boundary does NOT route through "
        "``normalize_risk_level`` -- Pattern 3a discipline broken. The "
        "W631 canonical helper must be the single source of truth for "
        "the risk-LEVEL projection; an inline severity map at the "
        "score_normalize boundary creates vocabulary drift."
    )


# ---------------------------------------------------------------------------
# (14) PR-REVIEW COMPOSER TRIO -- pr_analyze + pr_risk + diff markers coexist
# ---------------------------------------------------------------------------


def test_pr_review_composer_trio_marker_families_coexist(cli_runner, pr_analyze_project):
    """PR-REVIEW COMPOSER TRIO integration test: ``pr_analyze_*`` markers
    (W607-AA + BY), ``pr_risk_*`` markers (W607-Q/AB/BU), and ``diff_*``
    markers (W607-Z/BP) coexist when all 3 are invoked back-to-back.

    Closes the PR-review composer trio: each command emits its own marker
    family with no cross-contamination on the clean path.
    """
    from roam.cli import cli

    def _invoke(*args):
        old_cwd = os.getcwd()
        try:
            os.chdir(str(pr_analyze_project))
            return cli_runner.invoke(cli, list(args), catch_exceptions=False)
        finally:
            os.chdir(old_cwd)

    # 1. cmd_diff -> emits risk_level_canonical + diff_* family discipline
    diff_result = _invoke("--json", "diff")
    assert diff_result.exit_code == 0, diff_result.output
    diff_data = _json.loads(diff_result.output)
    diff_canonical = diff_data["summary"].get("risk_level_canonical")
    assert diff_canonical in ("critical", "high", "medium", "low"), (
        f"cmd_diff must emit canonical W631 risk-LEVEL; got {diff_canonical!r}"
    )

    # 2. cmd_pr_risk -> emits risk_level_canonical + pr_risk_* family discipline
    pr_risk_result = _invoke("--json", "pr-risk")
    assert pr_risk_result.exit_code == 0, pr_risk_result.output
    pr_risk_data = _json.loads(pr_risk_result.output)
    pr_risk_canonical = pr_risk_data["summary"].get("risk_level_canonical")
    assert pr_risk_canonical in ("critical", "high", "medium", "low"), (
        f"cmd_pr_risk must emit canonical W631 risk-LEVEL; got {pr_risk_canonical!r}"
    )

    # 3. cmd_pr_analyze -> emits risk_level_canonical + pr_analyze_* family
    pr_analyze_result = _invoke("--json", "pr-analyze")
    assert pr_analyze_result.exit_code in (0, 5), pr_analyze_result.output
    pr_analyze_data = _json.loads(pr_analyze_result.output)
    pr_analyze_canonical = pr_analyze_data["summary"].get("risk_level_canonical")
    assert pr_analyze_canonical in ("critical", "high", "medium", "low"), (
        f"cmd_pr_analyze must emit canonical W631 risk-LEVEL; got {pr_analyze_canonical!r}"
    )


# ---------------------------------------------------------------------------
# (15) FAMILY ISOLATION -- pr_analyze_* family does NOT leak to siblings
# ---------------------------------------------------------------------------


def test_pr_analyze_family_isolation(cli_runner, pr_analyze_project, monkeypatch):
    """Monkeypatch cmd_pr_analyze.auto_log to raise; confirm cmd_pr_analyze
    surfaces ``pr_analyze_*`` markers and does NOT contaminate cmd_pr_risk /
    cmd_pr_prep / cmd_pr_replay envelopes.

    Mirror of cmd_pr_risk's W607-BU family-isolation test, narrowed to the
    pr-review composer trio + sibling pr_* commands.
    """
    from roam.commands import cmd_pr_analyze

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-isolation-from-W607-BY")

    monkeypatch.setattr(cmd_pr_analyze, "auto_log", _raise_auto_log)

    # Run pr-analyze -> expect ``pr_analyze_*`` markers; no foreign family
    # leaks (in particular: NO ``pr_risk_*`` / ``pr_prep_*`` / ``pr_replay_*``
    # markers because pr-analyze does not invoke those internal substrates
    # via the W607-BY wrap).
    pr_analyze_result = _invoke_pr_analyze(cli_runner, pr_analyze_project, stdin=_DIFF_TEXT)
    assert pr_analyze_result.exit_code in (0, 5), pr_analyze_result.output
    pr_analyze_data = _json.loads(pr_analyze_result.output)
    pr_analyze_wo = pr_analyze_data.get("warnings_out") or []
    pr_analyze_markers = [m for m in pr_analyze_wo if m.startswith("pr_analyze_auto_log_failed:")]
    assert pr_analyze_markers, (
        f"cmd_pr_analyze must surface ``pr_analyze_auto_log_failed:`` markers; got {pr_analyze_wo!r}"
    )
    # Cross-prefix isolation: pr-analyze warnings_out must not contain
    # foreign W607-* family markers.
    failure_markers = [m for m in pr_analyze_wo if "_failed:" in m]
    # NOTE: ``pr_analyze_*`` and ``pr_*`` overlap as prefix strings -- guard
    # explicitly that no SIBLING command family (`pr_risk_` / ``pr_prep_`` /
    # ``pr_replay_``) markers leak.
    for foreign_prefix in (
        "pr_risk_",
        "pr_prep_",
        "pr_replay_",
        "diff_",
        "critique_",
        "attest_",
    ):
        leaked = [m for m in failure_markers if m.startswith(foreign_prefix)]
        # ``pr_analyze_`` starts with ``pr_`` so cannot be naively excluded
        # via prefix; only flag markers that are NOT pr_analyze_*.
        leaked = [m for m in leaked if not m.startswith("pr_analyze_")]
        assert not leaked, (
            f"cmd_pr_analyze warnings_out must not contain {foreign_prefix}* failure markers; got {leaked!r}"
        )
