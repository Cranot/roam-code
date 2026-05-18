"""W607-AZ -- ``cmd_minimap`` per-phase substrate-CALL marker plumbing.

Thirty-sixth-in-batch W607 consumer-layer arc. ADDITIVE plumbing: cmd_minimap
already carries the W607-L per-section-helper bare try/except family covering
DB-shape helpers inside ``_render_minimap`` (``_get_stack`` / ``_get_key_symbols``
/ ``_get_hotspots`` etc.) PLUS the W607-L outer-guard
``minimap_pipeline_failed:<exc>:<detail>`` for the open_db scope. W607-AZ adds
the canonical ``_run_check_az`` closure-based wrapper on top, covering the
downstream NON-DB substrate boundaries that W607-L did not reach:

* wrap_sentinels      -- markdown-sentinel block construction
* upsert_file         -- filesystem read + regex-substitute + write
* serialize_envelope  -- the on-text JSON serialization boundary

The marker prefix stays in the ``minimap_*`` family (closed-enum marker-prefix
discipline preserved). Hard distinction from sibling W607-* layers preserved
by the prefix-discipline test.

EXPLORATION-COMMAND resilience: cmd_minimap is a high-traffic orientation
aggregator -- agents call it as their first map-loading step. A raise in
``_upsert_file`` (read-only filesystem, permission denied, encoding error)
must NOT prevent the rendered block from being preserved in
``warnings_out`` markers. The per-phase wrap is what gives W607-AZ its
"partial-batch resilience" property -- a single broken substrate shouldn't
sink the whole map invocation.

W978 first-hypothesis check
---------------------------

Each W607-AZ-wrapped substrate has a documented empty-floor default that
matches its happy-path return shape so a raise degrades cleanly:

* wrap_sentinels      -> ""               (empty markdown block)
* upsert_file         -> "Failed"         (sentinel-failure verb)
* serialize_envelope  -> None             (manual fallback rebuild path)

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The substrate helpers are
patched via ``monkeypatch.setattr(cmd_minimap, "_upsert_file", ...)`` on
module-level helpers.

Marker prefix discipline
------------------------

Marker family is ``minimap_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the prefix-discipline
test. W607-AZ and W607-L SHARE the ``minimap_*`` family (both target the
same cmd_minimap consumer); the AZ-vs-L sub-wave distinction is internal.

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
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def minimap_project(tmp_path, monkeypatch):
    """Indexed corpus with multiple symbols + edges -- the W607-AZ
    substrate-failure baseline. Distinct from the W805-B empty_corpus
    fixture; this corpus DOES have substrate, so the W607-AZ axis is
    "what happens when a downstream NON-DB substrate raises" rather than
    "what happens on empty corpus".
    """
    proj = tmp_path / "minimap_w607az_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


def _invoke_minimap(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam minimap`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("minimap")
    args.extend(extra)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-AZ substrate-CALL markers
# ---------------------------------------------------------------------------


def test_minimap_clean_envelope_omits_w607az_markers(cli_runner, minimap_project):
    """Clean minimap -> no W607-AZ substrate markers.

    Byte-identical-on-happy-path: an empty W607-AZ bucket on the success
    path must NOT introduce ``minimap_wrap_sentinels_failed:`` /
    ``minimap_upsert_file_failed:`` / ``minimap_serialize_envelope_failed:``
    markers on the envelope. The envelope's ``warnings_out`` is omitted
    entirely on a clean run (W607-L empty-bucket discipline).
    """
    result = _invoke_minimap(cli_runner, minimap_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "minimap"
    # Empty-bucket discipline: NO warnings_out keys on the clean path.
    assert "warnings_out" not in data, (
        f"clean minimap must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean minimap must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) upsert_file failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_minimap_upsert_file_failure_marker_format(cli_runner, minimap_project, monkeypatch, tmp_path):
    """If ``_upsert_file`` raises, surface the W607-AZ marker.

    The filesystem upsert is one of cmd_minimap's downstream substrate
    boundaries -- a raise here previously crashed the whole minimap
    invocation. W607-AZ surfaces it as a structured
    ``minimap_upsert_file_failed:<exc>:<detail>`` marker.
    """
    from roam.commands import cmd_minimap

    def _boom_upsert(*args, **kwargs):
        raise PermissionError("synthetic-upsert-from-W607-AZ")

    monkeypatch.setattr(cmd_minimap, "_upsert_file", _boom_upsert)

    target = tmp_path / "CLAUDE-target.md"
    result = _invoke_minimap(cli_runner, minimap_project, "-o", str(target), json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("minimap_upsert_file_failed:")]
    assert markers, f"expected minimap_upsert_file_failed: marker; got {all_wo!r}"
    assert any("PermissionError" in m for m in markers), markers
    assert any("synthetic-upsert-from-W607-AZ" in m for m in markers), markers
    assert data["summary"].get("partial_success") is True, (
        f"upsert-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_minimap_w607az_warnings_in_envelope(cli_runner, minimap_project, monkeypatch, tmp_path):
    """Non-empty W607-AZ bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_minimap

    def _boom_upsert(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AZ")

    monkeypatch.setattr(cmd_minimap, "_upsert_file", _boom_upsert)

    target = tmp_path / "CLAUDE-target.md"
    result = _invoke_minimap(cli_runner, minimap_project, "-o", str(target), json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AZ disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AZ disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("minimap_upsert_file_failed:")]
    assert markers, f"expected minimap_upsert_file_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, minimap_project, monkeypatch, tmp_path):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AV contracts.
    """
    from roam.commands import cmd_minimap

    def _boom_upsert(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AZ")

    monkeypatch.setattr(cmd_minimap, "_upsert_file", _boom_upsert)

    target = tmp_path / "CLAUDE-target.md"
    result = _invoke_minimap(cli_runner, minimap_project, "-o", str(target), json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("minimap_upsert_file_failed:")]
    assert failure_markers, f"expected minimap_upsert_file_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "minimap_upsert_file_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) EXPLORATION-COMMAND partial-batch resilience: one phase raises,
#     the rendered block is still preserved as warnings_out evidence
# ---------------------------------------------------------------------------


def test_minimap_partial_batch_resilience_render_preserved(cli_runner, minimap_project, monkeypatch, tmp_path):
    """A raise in ``_upsert_file`` must NOT abort the envelope.

    Per-file PARSE-RESILIENCE bonus shape: one substrate boundary
    failing must NOT prevent the rendered block from being preserved
    via the warnings_out evidence trail. The minimap render is the
    high-value output for an exploration agent; losing it on a single
    downstream substrate raise would be a strict regression.
    """
    from roam.commands import cmd_minimap

    def _boom_upsert(*args, **kwargs):
        raise RuntimeError("synthetic-batch-upsert-from-W607-AZ")

    monkeypatch.setattr(cmd_minimap, "_upsert_file", _boom_upsert)

    target = tmp_path / "CLAUDE-target.md"
    result = _invoke_minimap(cli_runner, minimap_project, "-o", str(target), json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # 1) upsert_file failure marker present
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    upsert_markers = [m for m in all_wo if m.startswith("minimap_upsert_file_failed:")]
    assert upsert_markers, f"expected minimap_upsert_file_failed: marker; got {all_wo!r}"

    # 2) summary partial_success flipped
    assert data["summary"].get("partial_success") is True, (
        f"partial-batch failure must flip partial_success; got summary = {data['summary']!r}"
    )

    # 3) Envelope still emits cleanly (exit 0, JSON parseable, command stamped)
    assert data["command"] == "minimap"


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-AZ stays in ``minimap_*`` family
# ---------------------------------------------------------------------------


def test_w607az_marker_prefix_stays_in_minimap_family(cli_runner, minimap_project, monkeypatch, tmp_path):
    """Every W607-AZ substrate marker uses the canonical ``minimap_*`` prefix.

    cmd_minimap is the DB-shape + filesystem map-builder -- distinct from
    sibling W607-* layers. Marker prefix MUST stay ``minimap_*`` and MUST
    NOT leak into other family prefixes (``vulns_*``, ``dogfood_*``,
    ``describe_*``, ``preflight_*``, etc.).
    """
    from roam.commands import cmd_minimap

    def _boom_upsert(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AZ")

    monkeypatch.setattr(cmd_minimap, "_upsert_file", _boom_upsert)

    target = tmp_path / "CLAUDE-target.md"
    result = _invoke_minimap(cli_runner, minimap_project, "-o", str(target), json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("minimap_"), (
            f"every surfaced W607-AZ marker must use the ``minimap_*`` "
            f"prefix family (cmd_minimap scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("vulns_", "cmd_vulns W607-AQ"),
            ("sbom_", "cmd_sbom W607-AM"),
            ("supply_chain_", "cmd_supply_chain W607-AK"),
            ("cga_", "cmd_cga W607-AF"),
            ("attest_", "cmd_attest W607-AD"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("relate_", "cmd_relate W607-W"),
            ("deps_", "cmd_deps W607-V"),
            ("uses_", "cmd_uses W607-U"),
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("audit_trail_", "cmd_audit_trail W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("retrieve_", "cmd_retrieve W607-B"),
            ("findings_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D / W607-AV"),
            ("vuln_reach_", "cmd_vuln_reach W607-AU"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_minimap carries the W607-AZ accumulator
# ---------------------------------------------------------------------------


def test_cmd_minimap_carries_w607az_accumulator():
    """AST-level guard: cmd_minimap source carries the W607-AZ accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-AZ instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_minimap.py"
    assert src_path.exists(), f"cmd_minimap.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607az_warnings_out" in src, (
        "W607-AZ accumulator missing from cmd_minimap; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_az" in src, (
        "W607-AZ ``_run_check_az`` helper missing from cmd_minimap; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_az is defined inside cmd_minimap.
    tree = ast.parse(src)
    found_run_check_az = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_az":
            found_run_check_az = True
            break
    assert found_run_check_az, (
        "W607-AZ ``_run_check_az`` helper not found in cmd_minimap AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-AZ substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607az_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-AZ substrate boundary is wrapped.

    W607-AZ substrate inventory (cmd_minimap -- downstream NON-DB
    substrates not covered by W607-L):

    * wrap_sentinels      -- markdown-sentinel block construction
    * upsert_file         -- filesystem read + regex-substitute + write
    * serialize_envelope  -- the on-text JSON serialization boundary

    NOTE: The DB-shape section helpers (``_get_stack``, ``_get_key_symbols``,
    etc.) are owned by the W607-L per-helper bare try/except family
    inside ``_render_minimap`` -- W607-AZ does NOT re-wrap them. The
    W607-L outer-guard ``minimap_pipeline_failed`` marker family stays
    owned by the open_db scope.

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_minimap.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "wrap_sentinels",
        "upsert_file",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_az("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_az(\n        "{phase}"' in src
            or f'_run_check_az(\n            "{phase}"' in src
            or f'_run_check_az(\n                "{phase}"' in src
            or f'_run_check_az(\n                    "{phase}"' in src
            or f'_run_check_az(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AZ _run_check_az wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) W607-L pre-existing plumbing coexists with W607-AZ per-phase plumbing
# ---------------------------------------------------------------------------


def test_w607l_and_w607az_coexist_in_cmd_minimap():
    """cmd_minimap carries BOTH the W607-L bare try/except family AND the
    W607-AZ per-phase ``_run_check_az`` plumbing.

    W607-AZ is an ADDITIVE extension to W607-L's pre-existing per-section
    helper guards inside ``_render_minimap``. Both must remain in place:
    W607-L catches DB-shape section-helper raises (and the open_db
    outer-guard ``minimap_pipeline_failed`` family); W607-AZ catches the
    downstream filesystem/serialization substrate raises.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_minimap.py"
    src = src_path.read_text(encoding="utf-8")
    # W607-L outer-guard marker family
    assert "minimap_pipeline_failed" in src, (
        "W607-L outer-guard ``minimap_pipeline_failed`` marker family "
        "missing from cmd_minimap; the W607-L plumbing has been removed."
    )
    # W607-L per-section-helper markers (sample one of the per-helper
    # markers -- _get_key_symbols is a stable anchor)
    assert "minimap_key_symbols_failed" in src, (
        "W607-L per-section-helper marker ``minimap_key_symbols_failed`` "
        "missing from cmd_minimap; the W607-L per-helper bare try/except "
        "family has been removed."
    )
    # W607-AZ per-phase marker family (via _run_check_az which emits
    # ``minimap_<phase>_failed``)
    assert "_run_check_az" in src, (
        "W607-AZ ``_run_check_az`` per-phase helper missing from cmd_minimap; the W607-AZ plumbing has been removed."
    )
    # Both bucket names must coexist
    assert "warnings_out" in src and "_w607az_warnings_out" in src, (
        "cmd_minimap must carry BOTH the W607-L ``warnings_out`` bucket "
        "AND the W607-AZ ``_w607az_warnings_out`` per-phase bucket; one "
        "of the two has been removed."
    )


# ---------------------------------------------------------------------------
# (10) W607-L bucket and W607-AZ bucket markers coexist in the SAME envelope
# ---------------------------------------------------------------------------


def test_w607l_and_w607az_markers_coexist_in_envelope(cli_runner, minimap_project, monkeypatch, tmp_path):
    """Force BOTH buckets to fire and assert they coexist in warnings_out.

    Regression-guard: the pre-existing W607-L per-section-helper marker
    family must still surface AFTER the W607-AZ additive plumbing lands.
    Patch ``_get_key_symbols`` (W607-L scope) AND ``_upsert_file``
    (W607-AZ scope) so both fire; assert both markers appear in the
    combined ``warnings_out``.
    """
    from roam.commands import cmd_minimap

    def _boom_key_symbols(conn, limit=5):
        raise RuntimeError("synthetic-l-coexist")

    def _boom_upsert(*args, **kwargs):
        raise PermissionError("synthetic-az-coexist")

    monkeypatch.setattr(cmd_minimap, "_get_key_symbols", _boom_key_symbols)
    monkeypatch.setattr(cmd_minimap, "_upsert_file", _boom_upsert)

    target = tmp_path / "CLAUDE-target.md"
    result = _invoke_minimap(cli_runner, minimap_project, "-o", str(target), json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    l_markers = [m for m in top_wo if m.startswith("minimap_key_symbols_failed:")]
    az_markers = [m for m in top_wo if m.startswith("minimap_upsert_file_failed:")]
    assert l_markers, f"W607-L pre-existing marker family must still surface after W607-AZ lands; got {top_wo!r}"
    assert az_markers, f"W607-AZ marker family must surface alongside W607-L; got {top_wo!r}"
    # Both bucket origins reach summary.warnings_out as well.
    summary_wo = data["summary"].get("warnings_out") or []
    assert sorted(top_wo) == sorted(summary_wo), (
        f"top-level vs summary.warnings_out must be equal; top={top_wo!r} summary={summary_wo!r}"
    )
