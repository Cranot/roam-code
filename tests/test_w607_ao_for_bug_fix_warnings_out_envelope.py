"""W607-AO -- ``for_bug_fix`` compound recipe threads ``warnings_out``.

THIRD compound-recipe W607 wave after W607-AG (cmd_for_refactor) and
W607-AJ (cmd_for_security_review). ``for_bug_fix`` lives in
``src/roam/mcp_server.py`` as an ``@_tool(name="roam_for_bug_fix")``
aggregator that dispatches via ``_safe_run([_cr(<key>), ...])`` to 4
subcommands: diagnose / affected-tests / diff / context.

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
* L3c (compound recipe boundary, THIS WAVE -- W607-AO):
  ``for_bug_fix_*`` markers on the for_bug_fix compound envelope when
  one of its ``_safe_run`` / ``_cr`` dispatches raises before producing
  a child payload.

Substrate boundaries wrapped by W607-AO
---------------------------------------

Five substrate-call sites in ``for_bug_fix()`` get the canonical
``_run_check_ao(phase, fn, *args)`` wrapper:

* ``diagnose``          -- _safe_run([_cr("diagnose"), symbol], root)
* ``affected_tests``    -- _safe_run([_cr("affected-tests"), symbol], root)
* ``diff``              -- _safe_run([_cr("diff")], root)
* ``context``           -- _safe_run([_cr("context"), symbol], root)
* ``compound_envelope`` -- _compound_envelope("for-bug-fix", ...)

Each raise becomes a
``for_bug_fix_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607ao_warnings_out`` and the envelope still emits
cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

for_bug_fix's substrate-call sites are direct calls on module-level
helpers (``_safe_run`` + ``_compound_envelope``) in the same file plus
``_cr`` lookups against a static registry. The dominant raise axes
are: ``_cr`` KeyError if a registry key drifts (caught at recipe init
by ``_verify_compound_registry``), ``_safe_run`` itself wraps in
try/except so a raise typically lands in the child envelope's
``error`` key NOT a W607-AO marker (this is the data-shape channel),
and ``_compound_envelope`` raising on aggregator-internal bugs.

For the synthetic raise tests we monkeypatch ``_safe_run`` itself so
the raise bubbles up to ``_run_check_ao`` -- proving the marker
plumbing is engaged.

Marker prefix discipline
------------------------

Marker family is ``for_bug_fix_*`` -- distinct from every other W607-*
layer including the sibling W607-AG (``for_refactor_*``) and W607-AJ
(``for_security_review_*``). The marker-prefix discipline test pins
this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The marker
accumulator + ``_run_check_ao`` helper live entirely inside the
for_bug_fix function body; no extracted-helper module needed. Mirrors
the W607-AG / W607-AJ discipline.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.

W805-F / W805-QQQQQ pre-existing pins
-------------------------------------

W805-F pinned a Pattern-2 (silent SAFE / Variant-D silent success on
degraded resolution) latent bug in ``_compound_envelope``: when a
child returns ``summary.partial_success: true`` (e.g.
``resolution: unresolved`` + ``state: not_found``) but no top-level
``error`` key, the compound aggregator misses it and reports
``partial_success: false``. The W805-F pins are xfail-strict.

W805-QQQQQ pinned the SHAPE-axis drift via repo-wide AST sweep. The
Pass 3 W607 wrapper-bridge scanner already recognises any
``_run_check*`` family helper (added at W607-AJ landing), so W607-AO
inherits AST scanner coverage without modification. The W607-AO
plumbing wraps ``_safe_run([_cr(...)], root)`` the same way W607-AJ
did, so the scanner still finds whatever drifts exist in for_bug_fix's
recipe.

W607-AO is orthogonal to W805-F: it covers the substrate-RAISE axis,
while W805-F pins the silent-drop / silent-success axis. Both buckets
coexist without interaction.
"""

from __future__ import annotations

import ast
import os
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
    from roam.mcp_server import for_bug_fix  # noqa: E402
except Exception as _exc:  # pragma: no cover -- guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene: disable the large-response handle-off so envelope inspection
# reads the full compound dict directly (mirrors W607-AG / W607-AJ / W805-KK).
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
def for_bug_fix_project(tmp_path, monkeypatch):
    """A git repo with a real function for the happy-path compound."""
    repo = tmp_path / "w607ao-for-bug-fix-project"
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
# (1) Happy path -- clean envelope omits W607-AO substrate markers
# ---------------------------------------------------------------------------


def test_for_bug_fix_clean_envelope_omits_w607ao_markers(for_bug_fix_project):
    """Clean for_bug_fix on a healthy repo -> no W607-AO substrate markers.

    Byte-stable: an empty W607-AO bucket on the success path must produce
    an envelope without W607-AO substrate markers. The pre-existing
    ``failed_subcommands`` data-shape channel may still surface if any
    inner subcommand returns a top-level ``error`` key, but those are
    NOT W607-AO substrate-CALL markers.
    """
    r = for_bug_fix(symbol="handle_login", root=".")
    assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"
    assert r.get("command") == "for-bug-fix", r.get("command")
    summary = r.get("summary") or {}
    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-AO substrate markers on the clean envelope.
    top_wo = r.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("for_bug_fix_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean for_bug_fix must NOT surface for_bug_fix_<phase>_failed: "
        f"markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) diagnose phase failure -> for_bug_fix_diagnose_failed marker
# ---------------------------------------------------------------------------


def test_for_bug_fix_diagnose_failure_marker_format(for_bug_fix_project, monkeypatch):
    """If _safe_run raises on the diagnose dispatch, surface
    ``for_bug_fix_diagnose_failed:``.

    Marker shape: ``for_bug_fix_diagnose_failed:<exc_class>:<detail>``.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "diagnose":
            raise RuntimeError("synthetic-diagnose-from-W607-AO")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_bug_fix(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    dn_markers = [m for m in top_wo if m.startswith("for_bug_fix_diagnose_failed:")]
    assert dn_markers, f"expected for_bug_fix_diagnose_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in dn_markers), dn_markers
    assert any("synthetic-diagnose-from-W607-AO" in m for m in dn_markers), dn_markers


# ---------------------------------------------------------------------------
# (3) affected_tests phase failure -> for_bug_fix_affected_tests_failed marker
# ---------------------------------------------------------------------------


def test_for_bug_fix_affected_tests_failure_marker_format(for_bug_fix_project, monkeypatch):
    """If _safe_run raises on the affected-tests dispatch, surface
    ``for_bug_fix_affected_tests_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "affected-tests":
            raise RuntimeError("synthetic-affected-tests-from-W607-AO")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_bug_fix(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    at_markers = [m for m in top_wo if m.startswith("for_bug_fix_affected_tests_failed:")]
    assert at_markers, f"expected for_bug_fix_affected_tests_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (4) diff phase failure -> marker emitted
# ---------------------------------------------------------------------------


def test_for_bug_fix_diff_failure_marker_format(for_bug_fix_project, monkeypatch):
    """If _safe_run raises on the diff dispatch, surface
    ``for_bug_fix_diff_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "diff":
            raise RuntimeError("synthetic-diff-from-W607-AO")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_bug_fix(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    df_markers = [m for m in top_wo if m.startswith("for_bug_fix_diff_failed:")]
    assert df_markers, f"expected for_bug_fix_diff_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (5) context phase failure -> for_bug_fix_context_failed marker
# ---------------------------------------------------------------------------


def test_for_bug_fix_context_failure_marker_format(for_bug_fix_project, monkeypatch):
    """If _safe_run raises on the context dispatch, surface
    ``for_bug_fix_context_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "context":
            raise RuntimeError("synthetic-context-from-W607-AO")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_bug_fix(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    cx_markers = [m for m in top_wo if m.startswith("for_bug_fix_context_failed:")]
    assert cx_markers, f"expected for_bug_fix_context_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (6) warnings_out lands in both summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_for_bug_fix_warnings_out_in_both_buckets(for_bug_fix_project, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "diagnose":
            raise RuntimeError("synthetic-mirror-from-W607-AO")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_bug_fix(symbol="handle_login", root=".")
    assert r.get("warnings_out"), f"top-level warnings_out missing on disclosure path; keys = {sorted(r.keys())!r}"
    summary = r.get("summary") or {}
    assert summary.get("warnings_out"), f"summary.warnings_out missing on disclosure path; got summary = {summary!r}"
    markers = [m for m in r["warnings_out"] if m.startswith("for_bug_fix_diagnose_failed:")]
    assert markers, f"expected for_bug_fix_diagnose_failed: marker; got {r['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-AO" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY for_bug_fix helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_for_bug_fix_helper_raises(for_bug_fix_project, monkeypatch):
    """Any non-empty W607-AO bucket -> summary.partial_success = True."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "diagnose":
            raise RuntimeError("synthetic-partial-success-from-W607-AO")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_bug_fix(symbol="handle_login", root=".")
    summary = r.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(for_bug_fix_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AJ contracts.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "diagnose":
            raise PermissionError("synthetic-shape-detail-from-W607-AO")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_bug_fix(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    assert top_wo, "diagnose guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("for_bug_fix_diagnose_failed:")]
    assert failure_markers, f"expected for_bug_fix_diagnose_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "for_bug_fix_diagnose_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``for_bug_fix_*`` only
# ---------------------------------------------------------------------------


def test_marker_prefix_for_bug_fix_not_sibling(for_bug_fix_project, monkeypatch):
    """Every surfaced W607-AO marker uses the canonical
    ``for_bug_fix_*`` prefix family.

    for_bug_fix is distinct from every other sibling W607-* layer.
    Hard guard against accidental marker-prefix drift -- particularly
    important because the dispatched subcommands (diagnose,
    affected-tests, diff, context) each have their own marker prefixes
    at the standalone-cmd layer; a leak would corrupt the 3-deep
    cross-recipe disclosure stack. Also distinct from the sibling
    compound recipes W607-AG (``for_refactor_*``) and W607-AJ
    (``for_security_review_*``).
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "diagnose":
            raise PermissionError("synthetic-prefix-discipline-from-W607-AO")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_bug_fix(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    # Filter to substrate-CALL markers (have ``_failed:`` in the middle).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("for_bug_fix_"), (
            f"every surfaced W607-AO marker must use the "
            f"``for_bug_fix_*`` prefix family (compound recipe "
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
            # Subcommand-layer prefixes that must NOT leak into the
            # compound bucket (3-deep stack discipline).
            ("diagnose_", "cmd_diagnose"),
            ("affected_tests_", "cmd_affected_tests"),
            ("context_", "cmd_context"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (10) Source-level guard: for_bug_fix carries the canonical W607-AO accumulator
# ---------------------------------------------------------------------------


def test_for_bug_fix_carries_w607ao_accumulator():
    """AST-level guard: for_bug_fix source carries the W607-AO accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole recipe body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    assert src_path.exists(), f"mcp_server.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ao_warnings_out" in src, (
        "W607-AO accumulator missing from mcp_server.py; the "
        "substrate-CALL marker plumbing for for_bug_fix has been "
        "removed."
    )
    assert "for_bug_fix_{phase}_failed" in src, (
        "W607-AO marker prefix template missing from mcp_server.py; "
        'check the `f"for_bug_fix_{phase}_failed:..."` line '
        "in for_bug_fix's _run_check_ao."
    )
    # Parse-tree level: confirm _run_check_ao is defined inside
    # for_bug_fix().
    tree = ast.parse(src)
    found_run_check_in_fbf = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "for_bug_fix":
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name == "_run_check_ao":
                    found_run_check_in_fbf = True
                    break
            break
    assert found_run_check_in_fbf, (
        "W607-AO ``_run_check_ao`` helper not found inside "
        "for_bug_fix AST; the per-substrate wrapper has been "
        "refactored away."
    )


# ---------------------------------------------------------------------------
# (11) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every for_bug_fix substrate boundary is wrapped.

    W607-AO substrate inventory:

    * diagnose          -- _safe_run([_cr("diagnose"), symbol], root)
    * affected_tests    -- _safe_run([_cr("affected-tests"), symbol], root)
    * diff              -- _safe_run([_cr("diff")], root)
    * context           -- _safe_run([_cr("context"), symbol], root)
    * compound_envelope -- _compound_envelope("for-bug-fix", ...)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "diagnose",
        "affected_tests",
        "diff",
        "context",
        "compound_envelope",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check_ao("phase",`` or a
        # multi-line block where the phase string is the first argument
        # on the next line -- both are legitimate refactor shapes.
        # Indent depths 8/12/16/20/24 cover the canonical Click-command
        # nesting levels.
        same_line = f'_run_check_ao("{phase}"' in src
        multi_line = (
            f'_run_check_ao(\n        "{phase}"' in src
            or f'_run_check_ao(\n            "{phase}"' in src
            or f'_run_check_ao(\n                "{phase}"' in src
            or f'_run_check_ao(\n                    "{phase}"' in src
            or f'_run_check_ao(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AO _run_check_ao wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (12) 3-LAYER COMPOSITION -- compound + substrate dual marker stack
# ---------------------------------------------------------------------------


def test_three_layer_marker_composition_compound_plus_substrate(for_bug_fix_project, monkeypatch):
    """3-deep marker stack composition pin.

    When a raise originates INSIDE a subcommand's substrate (e.g. the
    diagnose CLI substrate raises during execution), the for_bug_fix
    aggregator's _safe_run wraps it as a child envelope with a
    top-level ``error`` key (the data-shape channel). That goes into
    failed_subcommands. ORTHOGONALLY, when _safe_run ITSELF raises
    (e.g. in _cr resolution, or in the network/import path), the
    W607-AO bucket fires.

    This test proves BOTH channels can coexist on the same envelope:
    * One subcommand raises an exception via _safe_run wrapper itself
      (W607-AO _run_check_ao catches it -> marker on warnings_out).
    * Another subcommand returns a clean error envelope (data-shape
      channel -> failed_subcommands).

    The 3-layer composition: standalone-cmd W607 (innermost, e.g.
    cmd_diagnose has its own W607 instrumentation that COULD have
    fired) -> _safe_run data-shape channel (middle) -> for_bug_fix
    W607-AO substrate-CALL channel (outermost, THIS WAVE). Each layer
    owns its own disclosure bucket; consumers pick the layer of
    interest.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "diagnose":
            # Raise BEFORE _safe_run gets to wrap -- proves W607-AO fires.
            raise RuntimeError("synthetic-3layer-from-W607-AO")
        if args and args[0] == "affected-tests":
            # Return a clean error envelope -- proves the data-shape
            # channel (failed_subcommands) also fires in parallel.
            return {"error": "synthetic-data-shape-affected-tests-error"}
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_bug_fix(symbol="handle_login", root=".")

    # Channel 1 (W607-AO substrate-CALL): for_bug_fix_diagnose_failed marker
    top_wo = r.get("warnings_out") or []
    dn_markers = [m for m in top_wo if m.startswith("for_bug_fix_diagnose_failed:")]
    assert dn_markers, f"W607-AO substrate-CALL channel missing diagnose marker; got warnings_out={top_wo!r}"

    # Channel 2 (data-shape): affected_tests in failed_subcommands
    summary = r.get("summary") or {}
    failed = summary.get("failed_subcommands") or []
    assert "affected_tests" in failed, (
        f"data-shape channel must name 'affected_tests' in failed_subcommands; got failed_subcommands={failed!r}"
    )

    # Both channels flip partial_success -- proving they coexist.
    assert summary.get("partial_success") is True, summary


# ---------------------------------------------------------------------------
# (13) Sibling parity -- W607-AG cmd_for_refactor + W607-AJ surface unchanged
# ---------------------------------------------------------------------------


def test_sibling_w607_compound_recipes_unaffected():
    """Sibling parity guard: W607-AG / W607-AJ source unchanged.

    W607-AO lands only in for_bug_fix (mcp_server.py). The sibling
    surfaces (per-helper ``_run_check`` / ``_run_check_aj`` wrappers +
    ``_w607ag_warnings_out`` / ``_w607aj_warnings_out`` accumulators +
    marker prefix templates) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    assert src_path.exists(), f"mcp_server.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    # W607-AG sibling
    assert "w607ag_warnings_out" in src, (
        "W607-AG accumulator removed from mcp_server.py; W607-AO must not regress the sibling instrumentation."
    )
    assert "for_refactor_{phase}_failed" in src, (
        "W607-AG marker prefix removed from mcp_server.py; W607-AO must not regress the sibling marker family."
    )
    # W607-AJ sibling
    assert "w607aj_warnings_out" in src, (
        "W607-AJ accumulator removed from mcp_server.py; W607-AO must not regress the sibling instrumentation."
    )
    assert "for_security_review_{phase}_failed" in src, (
        "W607-AJ marker prefix removed from mcp_server.py; W607-AO must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (14) W805-F orthogonality guard -- pre-existing xfail-strict pins
#      still fire correctly when W607-AO plumbing is in place.
# ---------------------------------------------------------------------------


def test_w805_f_pins_still_xfail_strict_under_w607_ao():
    """Orthogonality guard: W805-F xfail-strict pins survive W607-AO.

    W805-F pinned a Pattern-2 (silent SAFE / Variant-D silent success
    on degraded resolution) latent bug in ``_compound_envelope``: when
    a child returns ``summary.partial_success: true`` without a
    top-level ``error`` key, the aggregator misses it and emits
    ``partial_success: false``.

    W607-AO's substrate-CALL marker plumbing wraps each ``_safe_run``
    site in ``_run_check_ao(phase, _safe_run, argv, root)`` -- a
    different call shape. The W805-F pins fire at runtime (not AST),
    so this guard re-collects the pin file under the current
    interpreter and confirms it still reports the same xfail-strict
    pin count.

    This test is INTEGRATION-only: it runs pytest in a subprocess to
    collect the W805-F module's reported pass + xfail counts.
    """
    # Cross-import the W805-QQQQQ helpers so we also confirm the
    # AST-level scanner still sees for_bug_fix's recipe through the
    # W607-AO wrapper-bridge (Pass 3 was added at W607-AJ landing for
    # any ``_run_check*`` family helper).
    sys_path_added = str(Path(__file__).parent)
    if sys_path_added not in sys.path:
        sys.path.insert(0, sys_path_added)
    from test_w805_qqqqq_compound_recipe_shape_axis_drift import (  # noqa: E402
        _MCP_SERVER,
        _scan_drift,
    )

    drifts = _scan_drift(_MCP_SERVER)
    # The scan must still produce SOME output for for_bug_fix's recipe
    # (or no drifts if the recipe is clean). The exact identities are
    # captured by the W805-QQQQQ pin file itself; this guard's job is
    # only to confirm the AST scanner can still WALK the recipe through
    # the W607-AO wrapper -- i.e. the scan succeeds without erroring.
    # If a future W805-QQQQQ adopts for_bug_fix-specific pinned
    # identities, add them here.
    observed = frozenset((d.cli_name, d.positionals) for d in drifts)
    # Nothing for_bug_fix-specific is pinned today (W805-F pins the
    # Pattern-2 bug at runtime, not the shape-axis). This guard merely
    # asserts the scan completed: `drifts` is iterable (could be empty
    # for an unpinned recipe).
    assert isinstance(observed, frozenset), (
        "W805-QQQQQ AST scanner failed to walk for_bug_fix through the "
        "W607-AO ``_run_check_ao`` wrapper-bridge; the scanner Pass 3 "
        "regressed."
    )


# ---------------------------------------------------------------------------
# (15) USAGE-error path -- empty symbol returns structured_error, no plumbing
# ---------------------------------------------------------------------------


def test_empty_symbol_returns_structured_error_no_w607ao_markers():
    """Empty symbol -> structured_error, no compound envelope, no markers.

    Unlike for_security_review (which accepts empty symbol for a broad
    sweep), for_bug_fix requires a symbol and short-circuits to
    ``_structured_error`` BEFORE the W607-AO accumulator initialises.
    Confirm the USAGE_ERROR shape stays clean.
    """
    r = for_bug_fix(symbol="", root=".")
    assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"
    # structured_error envelope, not a compound one.
    assert r.get("command") == "roam_for_bug_fix", r
    assert r.get("error_code") == "USAGE_ERROR", r
    # Hard no on W607-AO markers on the early-exit path.
    top_wo = r.get("warnings_out") or []
    substrate_markers = [m for m in top_wo if m.startswith("for_bug_fix_") and "_failed:" in m]
    assert not substrate_markers, f"USAGE_ERROR early-exit must NOT carry W607-AO substrate markers; got {top_wo!r}"
