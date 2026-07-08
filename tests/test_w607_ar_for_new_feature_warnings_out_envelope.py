"""W607-AR -- ``for_new_feature`` compound recipe threads ``warnings_out``.

FOURTH and FINAL compound-recipe W607 wave. With this landing the
4-compound family is complete:

* W607-AG cmd_for_refactor          -- 5 phases (preflight / impact /
  complexity_report / clones / compound_envelope)
* W607-AJ cmd_for_security_review   -- 5 phases (vulns / vuln_reach /
  taint / auth_gaps / compound_envelope)
* W607-AO cmd_for_bug_fix           -- 5 phases (diagnose /
  affected_tests / diff / context / compound_envelope)
* W607-AR cmd_for_new_feature       -- THIS WAVE: 5 phases
  (understand / complexity_report / search / context /
  compound_envelope). ``search`` and ``context`` are CONDITIONAL on
  ``area`` being non-empty (and on search finding matches for
  ``context``); the W607-AR plumbing only wraps a substrate when the
  recipe actually invokes it.

``for_new_feature`` lives in ``src/roam/mcp_server.py`` as an
``@_tool(name="roam_for_new_feature")`` aggregator that dispatches via
``_safe_run([_cr(<key>), ...])``.

Marker-stack composition (this wave proves the same 3-deep pattern)
-------------------------------------------------------------------

* L1 (workflow boundary, W607-AA): ``pr_analyze_*`` markers on
  cmd_pr_analyze's outer envelope when an inner-CliRunner output
  collapse occurs.
* L2 (recipe boundary, W607-AC): ``pr_prep_*`` markers on cmd_pr_prep's
  envelope when one of its substrate helpers raises.
* L3a (compound recipe boundary, sibling W607-AG):
  ``for_refactor_*`` markers on the for_refactor compound envelope.
* L3b (compound recipe boundary, sibling W607-AJ):
  ``for_security_review_*`` markers on the for_security_review
  compound envelope.
* L3c (compound recipe boundary, sibling W607-AO):
  ``for_bug_fix_*`` markers on the for_bug_fix compound envelope.
* L3d (compound recipe boundary, THIS WAVE -- W607-AR):
  ``for_new_feature_*`` markers on the for_new_feature compound
  envelope when one of its ``_safe_run`` / ``_cr`` dispatches raises
  before producing a child payload.

Substrate boundaries wrapped by W607-AR
---------------------------------------

Five substrate-call sites in ``for_new_feature()`` get the canonical
``_run_check_ar(phase, fn, *args)`` wrapper:

* ``understand``        -- _safe_run([_cr("understand")], root)
* ``complexity_report`` -- _safe_run([_cr("complexity"), "--limit", "10"], root)
* ``search``            -- _safe_run([_cr("search"), area], root)  [conditional]
* ``context``           -- _safe_run([_cr("context"), anchor], root)  [conditional]
* ``compound_envelope`` -- _compound_envelope("for-new-feature", ...)

Each raise becomes a
``for_new_feature_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607ar_warnings_out`` and the envelope still emits
cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

for_new_feature's substrate-call sites are direct calls on module-level
helpers (``_safe_run`` + ``_compound_envelope``) in the same file plus
``_cr`` lookups against a static registry. The dominant raise axes
are: ``_cr`` KeyError if a registry key drifts (caught at recipe init
by ``_verify_compound_registry``), ``_safe_run`` itself wraps in
try/except so a raise typically lands in the child envelope's
``error`` key NOT a W607-AR marker (this is the data-shape channel),
and ``_compound_envelope`` raising on aggregator-internal bugs.

For the synthetic raise tests we monkeypatch ``_safe_run`` itself so
the raise bubbles up to ``_run_check_ar`` -- proving the marker
plumbing is engaged.

Marker prefix discipline
------------------------

Marker family is ``for_new_feature_*`` -- distinct from every other
W607-* layer including the sibling W607-AG (``for_refactor_*``),
W607-AJ (``for_security_review_*``), and W607-AO (``for_bug_fix_*``).
The marker-prefix discipline test pins this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The marker
accumulator + ``_run_check_ar`` helper live entirely inside the
for_new_feature function body; no extracted-helper module needed.
Mirrors the W607-AG / W607-AJ / W607-AO discipline.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.

W805-QQQQQ pre-existing pins
----------------------------

The Pass 3 W607 wrapper-bridge scanner already recognises any
``_run_check*`` family helper (added at W607-AJ landing), so W607-AR
inherits AST scanner coverage without modification. The W607-AR
plumbing wraps ``_safe_run([_cr(...)], root)`` the same way W607-AJ
and W607-AO did, so the scanner still finds whatever drifts exist in
for_new_feature's recipe.

4-COMPOUND FAMILY CLOSURE
-------------------------

With this wave the 4-compound family is COMPLETE. Test 15 below pins
the cross-recipe parity: all four ``_run_check_<suffix>`` accumulators
and marker prefix templates coexist in mcp_server.py source, proving
the AST scanner's family-prefix discovery handles the closed set
{ag, aj, ao, ar}.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# Import the compound directly. ``roam.mcp_server`` imports only the
# specific fastmcp submodules it needs (NOT the top-level ``fastmcp``
# package which has transitive import errors on some environments).
try:
    from roam import mcp_server as _srv  # noqa: E402
    from roam.mcp_server import for_new_feature  # noqa: E402
except Exception as _exc:  # pragma: no cover -- guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene: disable the large-response handle-off so envelope inspection
# reads the full compound dict directly (mirrors W607-AG / W607-AJ / W607-AO).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_handle_off(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    yield


# ---------------------------------------------------------------------------
# Fixtures -- minimal git-init + index for in-process compound dispatch
# ---------------------------------------------------------------------------


def _git_init_committed(repo: Path) -> None:
    """Init a git repo + commit current files. No further history."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(repo),
        capture_output=True,
        env=env,
        check=True,
    )


@pytest.fixture
def for_new_feature_project(tmp_path, monkeypatch):
    """A git repo with a real function for the happy-path compound."""
    repo = tmp_path / "w607ar-for-new-feature-project"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "auth.py").write_text(
        "def handle_login(user):\n    return user\n\ndef main():\n    return handle_login('alice')\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# (1) Happy path -- clean envelope omits W607-AR substrate markers
# ---------------------------------------------------------------------------


def test_for_new_feature_clean_envelope_omits_w607ar_markers(
    for_new_feature_project,
):
    """Clean for_new_feature on a healthy repo -> no W607-AR substrate markers.

    Byte-stable: an empty W607-AR bucket on the success path must
    produce an envelope without W607-AR substrate markers. The
    pre-existing ``failed_subcommands`` data-shape channel may still
    surface if any inner subcommand returns a top-level ``error`` key,
    but those are NOT W607-AR substrate-CALL markers.
    """
    r = for_new_feature(area="handle_login", root=".")
    assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"
    assert r.get("command") == "for-new-feature", r.get("command")
    summary = r.get("summary") or {}
    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-AR substrate markers on the clean envelope.
    top_wo = r.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("for_new_feature_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean for_new_feature must NOT surface "
        f"for_new_feature_<phase>_failed: markers; got top={top_wo!r}, "
        f"summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) understand phase failure -> for_new_feature_understand_failed marker
# ---------------------------------------------------------------------------


def test_for_new_feature_understand_failure_marker_format(for_new_feature_project, monkeypatch):
    """If _safe_run raises on the understand dispatch, surface
    ``for_new_feature_understand_failed:``.

    Marker shape: ``for_new_feature_understand_failed:<exc_class>:<detail>``.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "understand":
            raise RuntimeError("synthetic-understand-from-W607-AR")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_new_feature(area="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    un_markers = [m for m in top_wo if m.startswith("for_new_feature_understand_failed:")]
    assert un_markers, f"expected for_new_feature_understand_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in un_markers), un_markers
    assert any("synthetic-understand-from-W607-AR" in m for m in un_markers), un_markers


# ---------------------------------------------------------------------------
# (3) complexity_report phase failure -> marker emitted
# ---------------------------------------------------------------------------


def test_for_new_feature_complexity_report_failure_marker_format(for_new_feature_project, monkeypatch):
    """If _safe_run raises on the complexity dispatch, surface
    ``for_new_feature_complexity_report_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "complexity":
            raise RuntimeError("synthetic-complexity-from-W607-AR")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_new_feature(area="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    cr_markers = [m for m in top_wo if m.startswith("for_new_feature_complexity_report_failed:")]
    assert cr_markers, f"expected for_new_feature_complexity_report_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (4) search phase failure -> for_new_feature_search_failed marker
# ---------------------------------------------------------------------------


def test_for_new_feature_search_failure_marker_format(for_new_feature_project, monkeypatch):
    """If _safe_run raises on the search dispatch (only when area is
    non-empty), surface ``for_new_feature_search_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "search":
            raise RuntimeError("synthetic-search-from-W607-AR")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_new_feature(area="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    se_markers = [m for m in top_wo if m.startswith("for_new_feature_search_failed:")]
    assert se_markers, f"expected for_new_feature_search_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (5) context phase failure -> for_new_feature_context_failed marker
# ---------------------------------------------------------------------------


def test_for_new_feature_context_failure_marker_format(for_new_feature_project, monkeypatch):
    """If _safe_run raises on the context dispatch, surface
    ``for_new_feature_context_failed:``.

    Context only runs when search returned matches with a usable
    anchor. The monkeypatch routes search to a synthetic match
    structure so the recipe reaches the context dispatch.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "search":
            # Return a synthetic match so the recipe proceeds to context.
            return {"matches": [{"qualified_name": "handle_login", "name": "handle_login"}]}
        if args and args[0] == "context":
            raise RuntimeError("synthetic-context-from-W607-AR")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_new_feature(area="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    cx_markers = [m for m in top_wo if m.startswith("for_new_feature_context_failed:")]
    assert cx_markers, f"expected for_new_feature_context_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (6) warnings_out lands in both summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_for_new_feature_warnings_out_in_both_buckets(for_new_feature_project, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "understand":
            raise RuntimeError("synthetic-mirror-from-W607-AR")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_new_feature(area="handle_login", root=".")
    assert r.get("warnings_out"), f"top-level warnings_out missing on disclosure path; keys = {sorted(r.keys())!r}"
    summary = r.get("summary") or {}
    assert summary.get("warnings_out"), f"summary.warnings_out missing on disclosure path; got summary = {summary!r}"
    markers = [m for m in r["warnings_out"] if m.startswith("for_new_feature_understand_failed:")]
    assert markers, f"expected for_new_feature_understand_failed: marker; got {r['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-AR" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY for_new_feature helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_for_new_feature_helper_raises(for_new_feature_project, monkeypatch):
    """Any non-empty W607-AR bucket -> summary.partial_success = True."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "understand":
            raise RuntimeError("synthetic-partial-success-from-W607-AR")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_new_feature(area="handle_login", root=".")
    summary = r.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(for_new_feature_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AO contracts.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "understand":
            raise PermissionError("synthetic-shape-detail-from-W607-AR")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_new_feature(area="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    assert top_wo, "understand guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("for_new_feature_understand_failed:")]
    assert failure_markers, f"expected for_new_feature_understand_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "for_new_feature_understand_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``for_new_feature_*`` only
# ---------------------------------------------------------------------------


def test_marker_prefix_for_new_feature_not_sibling(for_new_feature_project, monkeypatch):
    """Every surfaced W607-AR marker uses the canonical
    ``for_new_feature_*`` prefix family.

    for_new_feature is distinct from every other sibling W607-* layer.
    Hard guard against accidental marker-prefix drift -- particularly
    important because the dispatched subcommands (understand,
    complexity, search, context) each have their own marker prefixes
    at the standalone-cmd layer; a leak would corrupt the 3-deep
    cross-recipe disclosure stack. Also distinct from the sibling
    compound recipes W607-AG (``for_refactor_*``), W607-AJ
    (``for_security_review_*``), and W607-AO (``for_bug_fix_*``).
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "understand":
            raise PermissionError("synthetic-prefix-discipline-from-W607-AR")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_new_feature(area="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    # Filter to substrate-CALL markers (have ``_failed:`` in the middle).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("for_new_feature_"), (
            f"every surfaced W607-AR marker must use the "
            f"``for_new_feature_*`` prefix family (compound recipe "
            f"scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("pr_analyze_", "cmd_pr_analyze W607-AA"),
            ("pr_prep_", "cmd_pr_prep W607-AC"),
            ("pr_risk_", "cmd_pr_risk W607-AB"),
            ("attest_", "cmd_attest W607-AD"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("relate_", "cmd_relate W607-W"),
            ("for_refactor_", "for_refactor W607-AG"),
            ("for_security_review_", "for_security_review W607-AJ"),
            ("for_bug_fix_", "for_bug_fix W607-AO"),
            # Subcommand-layer prefixes that must NOT leak into the
            # compound bucket (3-deep stack discipline).
            ("understand_", "cmd_understand"),
            ("complexity_", "cmd_complexity"),
            ("search_", "cmd_search"),
            ("context_", "cmd_context"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (10) Source-level guard: for_new_feature carries the canonical W607-AR accumulator
# ---------------------------------------------------------------------------


def test_for_new_feature_carries_w607ar_accumulator():
    """AST-level guard: for_new_feature source carries the W607-AR accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole recipe body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    assert src_path.exists(), f"mcp_server.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ar_warnings_out" in src, (
        "W607-AR accumulator missing from mcp_server.py; the "
        "substrate-CALL marker plumbing for for_new_feature has been "
        "removed."
    )
    assert "for_new_feature_{phase}_failed" in src, (
        "W607-AR marker prefix template missing from mcp_server.py; "
        'check the `f"for_new_feature_{phase}_failed:..."` line '
        "in for_new_feature's _run_check_ar."
    )
    # Parse-tree level: confirm _run_check_ar is defined inside
    # for_new_feature().
    tree = ast.parse(src)
    found_run_check_in_fnf = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "for_new_feature":
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name == "_run_check_ar":
                    found_run_check_in_fnf = True
                    break
            break
    assert found_run_check_in_fnf, (
        "W607-AR ``_run_check_ar`` helper not found inside "
        "for_new_feature AST; the per-substrate wrapper has been "
        "refactored away."
    )


# ---------------------------------------------------------------------------
# (11) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every for_new_feature substrate boundary is wrapped.

    W607-AR substrate inventory:

    * understand        -- _safe_run([_cr("understand")], root)
    * complexity_report -- _safe_run([_cr("complexity"), "--limit", "10"], root)
    * search            -- _safe_run([_cr("search"), area], root)
    * context           -- _safe_run([_cr("context"), anchor], root)
    * compound_envelope -- _compound_envelope("for-new-feature", ...)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "understand",
        "complexity_report",
        "search",
        "context",
        "compound_envelope",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check_ar("phase",`` or a
        # multi-line block where the phase string is the first argument
        # on the next line -- both are legitimate refactor shapes at ANY
        # nesting depth (a hardcoded indent list broke when the search
        # phase moved one level deeper inside
        # ``_materialize_compound_sections_preserving_recipe_order``).
        wrapped = re.search(rf'_run_check_ar\(\s*"{phase}"', src) is not None
        assert wrapped, (
            f"W607-AR _run_check_ar wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (12) 3-LAYER COMPOSITION -- compound + substrate dual marker stack
# ---------------------------------------------------------------------------


def test_three_layer_marker_composition_compound_plus_substrate(for_new_feature_project, monkeypatch):
    """3-deep marker stack composition pin.

    When a raise originates INSIDE a subcommand's substrate (e.g. the
    understand CLI substrate raises during execution), the
    for_new_feature aggregator's _safe_run wraps it as a child envelope
    with a top-level ``error`` key (the data-shape channel). That goes
    into failed_subcommands. ORTHOGONALLY, when _safe_run ITSELF raises
    (e.g. in _cr resolution, or in the network/import path), the
    W607-AR bucket fires.

    This test proves BOTH channels can coexist on the same envelope:
    * One substrate raises an exception via _safe_run wrapper itself
      (W607-AR _run_check_ar catches it -> marker on warnings_out).
    * Another substrate returns a clean error envelope (data-shape
      channel -> failed_subcommands).

    The 3-layer composition: standalone-cmd W607 (innermost, e.g.
    cmd_understand has its own W607 instrumentation that COULD have
    fired) -> _safe_run data-shape channel (middle) -> for_new_feature
    W607-AR substrate-CALL channel (outermost, THIS WAVE). Each layer
    owns its own disclosure bucket; consumers pick the layer of
    interest.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "understand":
            # Raise BEFORE _safe_run gets to wrap -- proves W607-AR fires.
            raise RuntimeError("synthetic-3layer-from-W607-AR")
        if args and args[0] == "complexity":
            # Return a clean error envelope -- proves the data-shape
            # channel (failed_subcommands) also fires in parallel.
            return {"error": "synthetic-data-shape-complexity-error"}
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_new_feature(area="handle_login", root=".")

    # Channel 1 (W607-AR substrate-CALL): for_new_feature_understand_failed marker
    top_wo = r.get("warnings_out") or []
    un_markers = [m for m in top_wo if m.startswith("for_new_feature_understand_failed:")]
    assert un_markers, f"W607-AR substrate-CALL channel missing understand marker; got warnings_out={top_wo!r}"

    # Channel 2 (data-shape): complexity_report in failed_subcommands
    summary = r.get("summary") or {}
    failed = summary.get("failed_subcommands") or []
    assert "complexity_report" in failed, (
        f"data-shape channel must name 'complexity_report' in failed_subcommands; got failed_subcommands={failed!r}"
    )

    # Both channels flip partial_success -- proving they coexist.
    assert summary.get("partial_success") is True, summary


# ---------------------------------------------------------------------------
# (13) Sibling parity -- W607-AG/AJ/AO surfaces unchanged
# ---------------------------------------------------------------------------


def test_sibling_w607_compound_recipes_unaffected():
    """Sibling parity guard: W607-AG / W607-AJ / W607-AO source unchanged.

    W607-AR lands only in for_new_feature (mcp_server.py). The three
    sibling surfaces (per-helper ``_run_check`` / ``_run_check_aj`` /
    ``_run_check_ao`` wrappers + ``_w607ag_warnings_out`` /
    ``_w607aj_warnings_out`` / ``_w607ao_warnings_out`` accumulators +
    marker prefix templates) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    assert src_path.exists(), f"mcp_server.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    # W607-AG sibling
    assert "_w607ag_warnings_out" in src, (
        "W607-AG accumulator removed from mcp_server.py; W607-AR must not regress the sibling instrumentation."
    )
    assert "for_refactor_{phase}_failed" in src, (
        "W607-AG marker prefix removed from mcp_server.py; W607-AR must not regress the sibling marker family."
    )
    # W607-AJ sibling
    assert "_w607aj_warnings_out" in src, (
        "W607-AJ accumulator removed from mcp_server.py; W607-AR must not regress the sibling instrumentation."
    )
    assert "for_security_review_{phase}_failed" in src, (
        "W607-AJ marker prefix removed from mcp_server.py; W607-AR must not regress the sibling marker family."
    )
    # W607-AO sibling
    assert "_w607ao_warnings_out" in src, (
        "W607-AO accumulator removed from mcp_server.py; W607-AR must not regress the sibling instrumentation."
    )
    assert "for_bug_fix_{phase}_failed" in src, (
        "W607-AO marker prefix removed from mcp_server.py; W607-AR must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (14) Empty-area happy path -- conditional substrates skipped cleanly
# ---------------------------------------------------------------------------


def test_empty_area_skips_conditional_substrates(for_new_feature_project):
    """Empty ``area`` -> recipe runs orientation + complexity only.

    Unlike for_bug_fix (which short-circuits to USAGE_ERROR on empty
    symbol), for_new_feature accepts empty ``area`` as a valid input:
    the recipe returns orientation + complexity baseline only. This
    test pins that the W607-AR plumbing handles the conditional-skip
    cleanly: no marker is emitted for a phase that the recipe chose
    not to invoke.
    """
    r = for_new_feature(area="", root=".")
    assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"
    assert r.get("command") == "for-new-feature", r.get("command")
    summary = r.get("summary") or {}
    # No W607-AR markers on the clean empty-area path.
    top_wo = r.get("warnings_out") or []
    substrate_markers = [m for m in top_wo if m.startswith("for_new_feature_") and "_failed:" in m]
    assert not substrate_markers, f"empty-area happy path must NOT carry W607-AR substrate markers; got {top_wo!r}"
    # The recipe must NOT have invoked search or context -- check the
    # sections list to confirm conditional-skip discipline.
    sections = summary.get("sections") or []
    section_names = [s.get("name") if isinstance(s, dict) else s for s in sections]
    if section_names:
        # When sections were emitted by name (the canonical
        # ``_compound_envelope`` shape), search/context must NOT be in
        # the list on the empty-area path.
        assert "search" not in section_names, f"empty-area path must NOT invoke search; got sections={section_names!r}"
        assert "context" not in section_names, (
            f"empty-area path must NOT invoke context; got sections={section_names!r}"
        )


# ---------------------------------------------------------------------------
# (15) 4-COMPOUND FAMILY CLOSURE -- AG + AJ + AO + AR all coexist
# ---------------------------------------------------------------------------


def test_four_compound_family_closure_all_accumulators_coexist():
    """4-COMPOUND FAMILY CLOSURE: all four compound recipes carry their
    W607 accumulators + marker prefix templates.

    With W607-AR landing, the 4-compound family is complete:

    * W607-AG cmd_for_refactor          -- ``_w607ag_warnings_out`` +
      ``for_refactor_{phase}_failed``
    * W607-AJ cmd_for_security_review   -- ``_w607aj_warnings_out`` +
      ``for_security_review_{phase}_failed``
    * W607-AO cmd_for_bug_fix           -- ``_w607ao_warnings_out`` +
      ``for_bug_fix_{phase}_failed``
    * W607-AR cmd_for_new_feature       -- ``_w607ar_warnings_out`` +
      ``for_new_feature_{phase}_failed``

    This guard pins the closed-enum membership: the four
    accumulators and the four marker prefix templates ALL appear in
    mcp_server.py source.

    Inheritance via W805-QQQQQ Pass 3 AST scanner: the scanner already
    recognises ``_run_check*`` family prefix (added at W607-AJ
    landing), so the family closure inherits scanner coverage. The
    parity test in W805-QQQQQ runs separately; this guard pins only
    the four marker accumulator + prefix anchors.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    src = src_path.read_text(encoding="utf-8")
    # The 4-member closed-enum membership pin.
    family_members = [
        ("_w607ag_warnings_out", "for_refactor_{phase}_failed", "W607-AG", "for_refactor"),
        ("_w607aj_warnings_out", "for_security_review_{phase}_failed", "W607-AJ", "for_security_review"),
        ("_w607ao_warnings_out", "for_bug_fix_{phase}_failed", "W607-AO", "for_bug_fix"),
        ("_w607ar_warnings_out", "for_new_feature_{phase}_failed", "W607-AR", "for_new_feature"),
    ]
    for acc, prefix_template, wave_tag, recipe_fn in family_members:
        assert acc in src, (
            f"{wave_tag} accumulator {acc!r} missing from mcp_server.py; the 4-compound family closure is broken."
        )
        assert prefix_template in src, (
            f"{wave_tag} marker prefix template {prefix_template!r} "
            f"missing from mcp_server.py; the 4-compound family closure "
            f"is broken."
        )
    # Closed-enum exhaustiveness: each accumulator helper must be
    # defined as a nested FunctionDef inside its compound recipe AST.
    tree = ast.parse(src)
    expected_helpers = {
        "for_refactor": "_run_check",
        "for_security_review": "_run_check_aj",
        "for_bug_fix": "_run_check_ao",
        "for_new_feature": "_run_check_ar",
    }
    found_helpers: dict[str, bool] = {fn: False for fn in expected_helpers}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in expected_helpers:
            target_helper = expected_helpers[node.name]
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name == target_helper:
                    found_helpers[node.name] = True
                    break
    for recipe_fn, helper_name in expected_helpers.items():
        assert found_helpers[recipe_fn], (
            f"4-COMPOUND FAMILY CLOSURE broken: helper "
            f"{helper_name!r} not found inside {recipe_fn!r} AST; "
            f"the family-prefix discovery for W805-QQQQQ Pass 3 will "
            f"miss this recipe."
        )
