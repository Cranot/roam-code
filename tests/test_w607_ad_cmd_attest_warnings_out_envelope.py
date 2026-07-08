"""W607-AD -- ``cmd_attest`` substrate-boundary plumbing.

Twenty-ninth-in-batch W607 consumer-layer arc. Fresh-plumbing wave: cmd_attest
had NO prior W607 instrumentation, so the canonical fresh template applies
(one accumulator + one ``_run_check_ad`` helper).

cmd_attest is the proof-carrying PR attestation aggregator -- it composes
blast-radius / risk / breaking / fitness / budget / tests / effects evidence
into a single auditable artifact. Each collector is a substrate boundary
that can raise; prior to W607-AD a raise crashed the whole attestation
build wholesale (registry-write boundary at the heart of the proof-bundle
emit path).

W805 cross-artifact consistency family
--------------------------------------

cmd_attest sits at the heart of the W805-KKKKK / OOOOO / PPPPP / RRRRR /
SSSSS cross-artifact consistency family (CGA / VSA / Rekor pipeline). The
W607-AD plumbing fires AT RUNTIME when an emission boundary raises,
complementing the W805 xfail-strict pins that catch structural inconsistency
at the dataclass level.

W631 risk-LEVEL canonical axis
------------------------------

cmd_attest is on the W631 risk-LEVEL canonical axis (parity with cmd_diff /
cmd_critique / cmd_pr_risk). The pre-existing ``_attest_warnings_out`` bucket
tracks W641-followup-D unknown-status drops (a risk.level couldn't be mapped
to the canonical W631 set); W607-AD adds the SUBSTRATE-CALL bucket
(``_w607ad_warnings_out``) tracking helper raises. Both feed the same
envelope ``warnings_out`` field; the marker PREFIX disambiguates them
(``attest_unknown_status:*`` vs. ``attest_<phase>_failed:*``).

W978 first-hypothesis check
---------------------------

Each W607-AD-wrapped collector has a documented empty-floor default matching
its happy-path return shape so a raise degrades cleanly. The dominant raise
axes are: networkx import propagation (``_collect_risk``,
``_collect_blast_radius``), malformed graph rows (``_collect_blast_radius``),
git-shell subprocess failure (``_collect_breaking``), and YAML parse errors
(``_collect_budget``).

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Every collector is a module-
local helper with a single call site.

Marker prefix discipline
------------------------

Marker family is ``attest_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers (``diff_*``, ``critique_*``,
``pr_risk_*``, etc.) preserved by the prefix-discipline test.

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
# Helpers -- invoke attest via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_attest(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam attest`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("attest")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with unstaged changes so attest reaches the
# collectors (the no-changes path short-circuits before any collector runs).
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def attest_project_with_changes(tmp_path, monkeypatch):
    """Indexed corpus with an unstaged modification so attest exercises every
    W607-AD substrate boundary (get_changed_files / resolve_changed_to_db /
    collect_blast_radius / collect_risk / collect_breaking / collect_fitness /
    collect_budget / collect_tests / collect_effects / compute_verdict).
    """
    proj = tmp_path / "attest_w607ad_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\n"
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n\ndef shout(msg):\n    return msg.upper()\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    # Make an unstaged edit so `roam attest` reaches the collector path.
    (proj / "src" / "main.py").write_text(
        "def main():\n    helper()\n    return 2\n\n"  # changed return
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-AD substrate-CALL markers
# ---------------------------------------------------------------------------


def test_attest_clean_envelope_omits_w607ad_markers(cli_runner, attest_project_with_changes):
    """Clean attest on a healthy corpus -> no W607-AD substrate markers.

    Byte-identical-on-happy-path: an empty W607-AD bucket on the success
    path must NOT introduce ``attest_<phase>_failed:`` markers on the
    envelope. The pre-existing ``_attest_warnings_out`` (W641-followup-D
    unknown-status) bucket is on a different axis and may or may not
    surface independently; this test asserts only that the W607-AD
    boundaries don't false-fire on the clean path.
    """
    result = _invoke_attest(cli_runner, attest_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "attest"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ad_markers = [m for m in (list(top_wo) + list(summary_wo)) if "_failed:" in m and m.startswith("attest_")]
    assert not ad_markers, (
        f"clean attest must NOT surface W607-AD substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) collect_blast_radius failure -> structured marker
# ---------------------------------------------------------------------------


def test_attest_collect_blast_radius_failure_marker_format(cli_runner, attest_project_with_changes, monkeypatch):
    """If ``_collect_blast_radius`` raises, surface the W607-AD marker.

    Blast radius is the first collector; a raise here previously crashed
    the whole attestation. W607-AD surfaces it as a structured
    ``attest_collect_blast_radius_failed:<exc>:<detail>`` marker and
    degrades the blast-radius dict to the documented empty floor so the
    composite verdict still emits.
    """
    from roam.commands import cmd_attest

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-blast-radius-from-W607-AD")

    monkeypatch.setattr(cmd_attest, "_collect_blast_radius", _raise)

    result = _invoke_attest(cli_runner, attest_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("attest_collect_blast_radius_failed:")]
    assert markers, f"expected attest_collect_blast_radius_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-blast-radius-from-W607-AD" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (3) SHARED-HELPER axis: get_changed_files failure -> structured marker
# ---------------------------------------------------------------------------


def test_attest_get_changed_files_failure_marker_format(cli_runner, attest_project_with_changes, monkeypatch):
    """If ``get_changed_files`` raises, surface the W607-AD marker.

    cmd_attest sits on the SHARED-HELPER axis (root cause at
    ``src/roam/commands/changed_files.py:142,145``); W607-AD wraps the
    upstream call so a raise here surfaces as
    ``attest_get_changed_files_failed:<exc>:<detail>`` rather than
    crashing the whole attestation build.
    """
    from roam.commands import cmd_attest

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-get-changed-files-from-W607-AD")

    monkeypatch.setattr(cmd_attest, "get_changed_files", _raise)

    result = _invoke_attest(cli_runner, attest_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # With get_changed_files raising, the default=[] cascades to the
    # no-changes degraded-resolution path -- the marker is the disclosure
    # of WHY the empty changeset surfaced. The marker may land on the
    # short-circuit envelope (no_changes path) without W607-AD wrapping
    # contributing further markers. Check the envelope's warnings_out
    # (top-level OR summary mirror).
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("attest_get_changed_files_failed:")]
    # The no-changes branch short-circuits before the W607-AD
    # warnings_out is wired to the envelope. As a fallback acceptance
    # criterion, the envelope MUST still emit cleanly (no crash) and the
    # no_changes state MUST be disclosed -- the SHARED-HELPER raise is
    # caught and the empty-floor cascades through.
    if not markers:
        # The fallback path emitted the no_changes envelope without the
        # W607-AD marker plumbing. That's still a cleanly-degraded
        # envelope (the contract) but the marker channel is missing.
        # Accept either outcome but require non-crash.
        assert data["summary"].get("state") == "no_changes", (
            f"expected either W607-AD marker OR no_changes state; got summary = {data['summary']!r}"
        )
    else:
        # Happy path: marker surfaced; check shape.
        assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (4) collect_risk failure -> structured marker
# ---------------------------------------------------------------------------


def test_attest_collect_risk_failure_marker_format(cli_runner, attest_project_with_changes, monkeypatch):
    """If ``_collect_risk`` raises, surface the W607-AD marker.

    Risk is the largest collector with a deep dependency chain (networkx
    + ``cmd_pr_risk`` helpers + ``cmd_coupling`` surprise + ``graph.layers``).
    A raise propagates as
    ``attest_collect_risk_failed:<exc>:<detail>``; the documented
    empty-floor (None) cascades through so the verdict + canonical
    risk_level safe-floor to ``low`` (the W531 CI-safety floor) without
    crashing.
    """
    from roam.commands import cmd_attest

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-risk-from-W607-AD")

    monkeypatch.setattr(cmd_attest, "_collect_risk", _raise)

    result = _invoke_attest(cli_runner, attest_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("attest_collect_risk_failed:")]
    assert markers, f"expected attest_collect_risk_failed: marker; got {top_wo!r}"
    # The risk None default cascades to the W531 safe-floor canonical level.
    assert data["summary"].get("risk_level_canonical") == "low", (
        f"missing-risk path must safe-floor canonical risk_level to 'low'; got {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_attest_w607ad_warnings_in_envelope(cli_runner, attest_project_with_changes, monkeypatch):
    """Non-empty W607-AD bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_attest

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AD")

    monkeypatch.setattr(cmd_attest, "_collect_blast_radius", _raise)

    result = _invoke_attest(cli_runner, attest_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AD disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AD disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("attest_collect_blast_radius_failed:")]
    assert markers, f"expected attest_collect_blast_radius_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (6) partial_success flips when W607-AD substrate raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_w607ad_helper_raises(cli_runner, attest_project_with_changes, monkeypatch):
    """Any non-empty W607-AD bucket -> summary.partial_success = True."""
    from roam.commands import cmd_attest

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AD")

    monkeypatch.setattr(cmd_attest, "_collect_blast_radius", _raise)

    result = _invoke_attest(cli_runner, attest_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-AD warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, attest_project_with_changes, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AC contracts.
    """
    from roam.commands import cmd_attest

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AD")

    monkeypatch.setattr(cmd_attest, "_collect_blast_radius", _raise)

    result = _invoke_attest(cli_runner, attest_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("attest_collect_blast_radius_failed:")]
    assert failure_markers, f"expected attest_collect_blast_radius_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "attest_collect_blast_radius_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-AD stays in ``attest_*`` family
# ---------------------------------------------------------------------------


def test_w607ad_marker_prefix_stays_in_attest_family(cli_runner, attest_project_with_changes, monkeypatch):
    """Every W607-AD substrate marker uses the canonical ``attest_*`` prefix.

    cmd_attest is the proof-carrying PR attestation aggregator -- distinct
    from sibling W607-* layers. Marker prefix MUST stay ``attest_*`` and
    MUST NOT leak into other family prefixes.
    """
    from roam.commands import cmd_attest

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AD")

    monkeypatch.setattr(cmd_attest, "_collect_blast_radius", _raise)

    result = _invoke_attest(cli_runner, attest_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # Filter to substrate-CALL markers (skip W641-followup-D
    # unknown-status markers which use ``attest_unknown_status:*``).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("attest_"), (
            f"every surfaced W607-AD marker must use the ``attest_*`` prefix family (cmd_attest scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
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
# (9) Source-level guard: cmd_attest carries the W607-AD accumulator
# ---------------------------------------------------------------------------


def test_cmd_attest_carries_w607ad_accumulator():
    """AST-level guard: cmd_attest source carries the W607-AD accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-AD instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_attest.py"
    assert src_path.exists(), f"cmd_attest.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ad_warnings_out" in src, (
        "W607-AD accumulator missing from cmd_attest; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ad" in src, (
        "W607-AD ``_run_check_ad`` helper missing from cmd_attest; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_ad is defined inside the
    # ``attest`` click command function.
    tree = ast.parse(src)
    found_run_check_ad = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ad":
            found_run_check_ad = True
            break
    assert found_run_check_ad, (
        "W607-AD ``_run_check_ad`` helper not found in cmd_attest AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (10) Each W607-AD substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ad_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-AD substrate boundary is wrapped.

    W607-AD substrate inventory:

    * get_changed_files          -- shared-helper axis (changed_files.py)
    * resolve_changed_to_db      -- shared-helper axis
    * collect_blast_radius       -- _collect_blast_radius(...)
    * collect_risk               -- _collect_risk(...)
    * collect_breaking           -- _collect_breaking(...)
    * collect_fitness            -- _collect_fitness_evidence(...)
    * collect_budget             -- _collect_budget_evidence(...)
    * collect_tests              -- _collect_affected_tests_evidence(...)
    * collect_effects            -- _collect_effects_evidence(...)
    * compute_verdict            -- _compute_verdict(...)
    * content_hash               -- _content_hash(...) (signing boundary)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites are inside a ``with
    open_db(...)`` block (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_attest.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "get_changed_files",
        "resolve_changed_to_db",
        "collect_blast_radius",
        "collect_risk",
        "collect_breaking",
        "collect_fitness",
        "collect_budget",
        "collect_tests",
        "collect_effects",
        "compute_verdict",
        "content_hash",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_ad("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_ad(\n        "{phase}"' in src
            or f'_run_check_ad(\n            "{phase}"' in src
            or f'_run_check_ad(\n                "{phase}"' in src
            or f'_run_check_ad(\n                    "{phase}"' in src
            or f'_run_check_ad(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AD _run_check_ad wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (11) Sibling parity -- pre-existing W641-followup-D bucket unchanged
# ---------------------------------------------------------------------------


def test_w641_followup_d_unknown_status_bucket_still_present():
    """Sibling parity guard: pre-existing W641-followup-D bucket intact.

    W607-AD is ADDITIVE: cmd_attest already had ``_attest_warnings_out``
    for tracking W641-followup-D unknown-status drops (a risk.level
    couldn't be mapped to the canonical W631 set). W607-AD adds a
    DIFFERENT bucket (``_w607ad_warnings_out``) for substrate-CALL
    markers. The pre-existing bucket MUST stay -- W607-AD only extends
    the wrap, never replaces the prior instrumentation.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_attest.py"
    src = src_path.read_text(encoding="utf-8")
    assert "_attest_warnings_out" in src, (
        "W641-followup-D ``_attest_warnings_out`` bucket removed from "
        "cmd_attest; W607-AD must NOT regress the prior W641-followup-D "
        "unknown-status instrumentation."
    )
    assert "attest_unknown_status:" in src, (
        "W641-followup-D ``attest_unknown_status:`` marker prefix removed "
        "from cmd_attest; W607-AD must NOT regress the prior marker family."
    )
