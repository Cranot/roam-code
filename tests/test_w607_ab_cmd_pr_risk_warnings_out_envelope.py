"""W607-AB -- ``cmd_pr_risk`` extends ``warnings_out`` to findings-emission.

Twenty-eighth-in-batch W607 consumer-layer arc. Direct extension of
W607-Q (which wrapped get_changed_files / resolve_changed_to_db /
build_symbol_graph / _compute_surprise / detect_layers /
_author_familiarity / _minor_contributor_risk). W607-AB closes the two
remaining substrate boundaries inside ``pr_risk()``:

* ``_build_pr_risk_finding_rows`` -- the SINGLE SOURCE OF TRUTH row
  builder used by BOTH the envelope ``findings[]`` array AND the
  ``--persist`` registry write. A raise here previously crashed the
  whole envelope.
* ``_emit_pr_risk_findings`` -- the registry-write boundary. The
  pre-existing ``try/except sqlite3.OperationalError`` at the call
  site preserves the pre-W89-schema silent-floor; W607-AB wraps the
  wider Exception axis so e.g. a malformed FindingRecord construction
  surfaces a marker rather than crashing the read path.

Each W607-AB raise becomes a
``pr_risk_<phase>_failed:<exc_class>:<detail>`` marker via
``_w607ab_warnings_out`` and the envelope still emits cleanly.

W805-EEEE SHARED-HELPER axis
----------------------------

cmd_pr_risk is the canonical SHARED-HELPER family sibling of cmd_diff
(W607-Z) on the ``get_changed_files`` axis (root cause at
``src/roam/commands/changed_files.py:142,145``). W607-Q wraps the
upstream substrates; W607-AB wraps the downstream findings-emission
boundaries. Together the two waves give cmd_pr_risk full per-
invocation visibility into the substrate degradation lineage even
before the root-cause helper fix lands.

W978 first-hypothesis check
---------------------------

The findings-emission boundary is the dominant remaining raise axis.
``_build_pr_risk_finding_rows`` accepts an arbitrary dict and builds
a list of FindingRecord dicts; a downstream refactor changing the
``data`` shape or the ``_PR_RISK_LEVEL_TO_SEVERITY`` table would
raise here. ``_emit_pr_risk_findings`` writes through
``emit_finding`` from ``src/roam/db/findings.py``; a malformed
``FindingRecord`` (e.g. a kind outside the closed enum) raises
``TypeError`` / ``ValueError`` before reaching the DB.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The
``_build_pr_risk_finding_rows`` and ``_emit_pr_risk_findings``
helpers are module-local and already shared one call site each.

Marker prefix discipline
------------------------

Marker family remains ``pr_risk_*`` -- W607-AB extends the SAME
marker family as W607-Q (the bucket separation is for source-grep
auditability, not for consumer-side demux). Consumers continue to
demux by marker shape: W989 canonical-level warnings have the
``"Config field 'level' value ..."`` prefix; W607-Q + W607-AB
substrate-CALL markers have the three-segment
``pr_risk_<phase>_failed:<exc_class>:<detail>`` shape.

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
# Helpers -- invoke pr-risk via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_pr_risk(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam pr-risk`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("pr-risk")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with unstaged changes so pr-risk reaches the
# findings-emission boundary (which only fires on the populated-diff path).
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def pr_risk_project_with_changes(tmp_path, monkeypatch):
    """Indexed corpus with an unstaged modification so pr-risk reaches the
    findings-emission boundary (build/emit rows path).
    """
    proj = tmp_path / "pr_risk_w607ab_project"
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
    # Make an unstaged edit so `roam pr-risk` reaches the findings path.
    (proj / "src" / "main.py").write_text(
        "def main():\n    helper()\n    return 2\n\n"  # changed return
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-AB substrate-CALL markers
# ---------------------------------------------------------------------------


def test_pr_risk_clean_envelope_omits_w607ab_markers(cli_runner, pr_risk_project_with_changes):
    """Clean pr-risk on a healthy corpus -> no W607-AB substrate markers.

    Hash-stable: an empty W607-AB bucket on the success path must NOT
    introduce ``pr_risk_build_pr_risk_finding_rows_failed:`` or
    ``pr_risk_emit_pr_risk_findings_failed:`` markers on the envelope.
    Other pr_risk_* markers (W607-Q substrates) may or may not surface
    independently; this test asserts only that the W607-AB boundaries
    don't false-fire on the clean path.
    """
    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "pr-risk"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ab_markers = [
        m
        for m in (list(top_wo) + list(summary_wo))
        if m.startswith("pr_risk_build_pr_risk_finding_rows_failed:")
        or m.startswith("pr_risk_emit_pr_risk_findings_failed:")
    ]
    assert not ab_markers, (
        f"clean pr-risk must NOT surface W607-AB substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) _build_pr_risk_finding_rows failure -> structured marker
# ---------------------------------------------------------------------------


def test_pr_risk_build_finding_rows_failure_marker_format(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """If ``_build_pr_risk_finding_rows`` raises, surface the W607-AB marker.

    The row builder is the SINGLE SOURCE OF TRUTH used by both the envelope
    ``findings[]`` array AND the ``--persist`` registry write. A raise here
    must NOT crash the whole envelope -- W607-AB surfaces it as a structured
    ``pr_risk_build_pr_risk_finding_rows_failed:<exc>:<detail>`` marker and
    degrades ``findings[]`` to ``[]`` so the composite score still emits.
    """
    from roam.commands import cmd_pr_risk

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-rows-from-W607-AB")

    monkeypatch.setattr(cmd_pr_risk, "_build_pr_risk_finding_rows", _raise)

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_risk_build_pr_risk_finding_rows_failed:")]
    assert markers, f"expected pr_risk_build_pr_risk_finding_rows_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-build-rows-from-W607-AB" in m for m in markers), markers
    # findings[] degrades to [] when builder raises -- envelope still emits.
    assert data.get("findings") == [] or "findings" not in data, (
        f"finding_rows default=[] should degrade findings[] cleanly; got findings={data.get('findings')!r}"
    )


# ---------------------------------------------------------------------------
# (3) _emit_pr_risk_findings failure -> structured marker (--persist path)
# ---------------------------------------------------------------------------


def test_pr_risk_emit_findings_failure_marker_format(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """If ``_emit_pr_risk_findings`` raises non-sqlite, surface the marker.

    The registry-write boundary is only exercised on the ``--persist``
    branch. ``sqlite3.OperationalError`` stays handled by the pre-existing
    outer try/except (pre-W89-schema silent floor); W607-AB catches any
    OTHER raise (e.g. malformed FindingRecord -> TypeError/ValueError).
    """
    from roam.commands import cmd_pr_risk

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-from-W607-AB")

    monkeypatch.setattr(cmd_pr_risk, "_emit_pr_risk_findings", _raise)

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("pr_risk_emit_pr_risk_findings_failed:")]
    assert markers, f"expected pr_risk_emit_pr_risk_findings_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (4) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_pr_risk_w607ab_warnings_in_envelope(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """Non-empty W607-AB bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_pr_risk

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AB")

    monkeypatch.setattr(cmd_pr_risk, "_build_pr_risk_finding_rows", _raise)

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AB disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AB disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("pr_risk_build_pr_risk_finding_rows_failed:")]
    assert markers, f"expected pr_risk_build_pr_risk_finding_rows_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (5) partial_success flips when W607-AB substrate raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_w607ab_helper_raises(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """Any non-empty W607-AB bucket -> summary.partial_success = True."""
    from roam.commands import cmd_pr_risk

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AB")

    monkeypatch.setattr(cmd_pr_risk, "_build_pr_risk_finding_rows", _raise)

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-AB warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (6) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..Z contracts.
    """
    from roam.commands import cmd_pr_risk

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AB")

    monkeypatch.setattr(cmd_pr_risk, "_build_pr_risk_finding_rows", _raise)

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("pr_risk_build_pr_risk_finding_rows_failed:")]
    assert failure_markers, f"expected pr_risk_build_pr_risk_finding_rows_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "pr_risk_build_pr_risk_finding_rows_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- W607-AB stays in ``pr_risk_*`` family
# ---------------------------------------------------------------------------


def test_w607ab_marker_prefix_stays_in_pr_risk_family(cli_runner, pr_risk_project_with_changes, monkeypatch):
    """Every W607-AB substrate marker uses the canonical ``pr_risk_*`` prefix.

    cmd_pr_risk is the PR-time risk aggregator -- distinct from sibling
    W607-* layers. W607-AB is an EXTENSION of the W607-Q wave (same cmd,
    additional substrate boundaries) -- marker prefix MUST stay
    ``pr_risk_*`` and MUST NOT leak into other family prefixes.
    """
    from roam.commands import cmd_pr_risk

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AB")

    monkeypatch.setattr(cmd_pr_risk, "_build_pr_risk_finding_rows", _raise)

    result = _invoke_pr_risk(cli_runner, pr_risk_project_with_changes, True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # Filter to substrate-CALL markers (skip W989 canonical-level warnings
    # which have the "Config field 'level' value..." prefix).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("pr_risk_"), (
            f"every surfaced W607-AB marker must use the ``pr_risk_*`` prefix "
            f"family (cmd_pr_risk scope); got {marker!r}"
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
# (8) Source-level guard: cmd_pr_risk carries the W607-AB accumulator
# ---------------------------------------------------------------------------


def test_cmd_pr_risk_carries_w607ab_accumulator():
    """AST-level guard: cmd_pr_risk source carries the W607-AB accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-AB instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_risk.py"
    assert src_path.exists(), f"cmd_pr_risk.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ab_warnings_out" in src, (
        "W607-AB accumulator missing from cmd_pr_risk; the substrate-CALL "
        "marker plumbing for findings-emission has been removed."
    )
    assert "_run_check_ab" in src, (
        "W607-AB ``_run_check_ab`` helper missing from cmd_pr_risk; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_ab is defined inside pr_risk().
    tree = ast.parse(src)
    found_run_check_ab = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ab":
            found_run_check_ab = True
            break
    assert found_run_check_ab, (
        "W607-AB ``_run_check_ab`` helper not found in cmd_pr_risk AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (9) Each W607-AB substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ab_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-AB substrate boundary is wrapped.

    W607-AB substrate inventory (additive on top of W607-Q):

    * build_pr_risk_finding_rows   -- _build_pr_risk_finding_rows(...)
    * emit_pr_risk_findings        -- _emit_pr_risk_findings(...)

    If a future wave introduces a new findings-emission substrate
    boundary, this guard needs to know about it -- add the phase name
    here. Accepts multiple indent depths because the call sites are
    inside a ``with open_db(...)`` block and possibly an ``if persist:``
    block.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_risk.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "build_pr_risk_finding_rows",
        "emit_pr_risk_findings",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_ab("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_ab(\n        "{phase}"' in src
            or f'_run_check_ab(\n            "{phase}"' in src
            or f'_run_check_ab(\n                "{phase}"' in src
            or f'_run_check_ab(\n                    "{phase}"' in src
            or f'_run_check_ab(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AB _run_check_ab wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (10) Sibling parity -- W607-Q cmd_pr_risk W607-Q surface unchanged
# ---------------------------------------------------------------------------


def test_w607_q_accumulator_still_present():
    """Sibling parity guard: W607-Q ``_w607q_warnings_out`` still present.

    W607-AB is ADDITIVE on top of W607-Q (same cmd, additional substrate
    boundaries). The pre-existing W607-Q wrapper / accumulator / marker
    family MUST stay identical -- W607-AB only extends the wrap, never
    replaces the existing W607-Q instrumentation.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_risk.py"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607q_warnings_out" in src, (
        "W607-Q accumulator removed from cmd_pr_risk; W607-AB must NOT regress the prior W607-Q instrumentation."
    )
    assert "pr_risk_" in src, (
        "W607-Q marker prefix removed from cmd_pr_risk; W607-AB must NOT regress the prior W607-Q marker family."
    )
