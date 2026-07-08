"""W607-AJ -- ``for_security_review`` compound recipe threads ``warnings_out``.

Thirty-first-in-batch W607 consumer-layer arc; the SECOND compound-recipe
W607 wave after W607-AG (cmd_for_refactor). ``for_security_review`` lives
in ``src/roam/mcp_server.py`` as an
``@_tool(name="roam_for_security_review")`` aggregator that dispatches
via ``_safe_run([_cr(<key>), ...])`` to 4 subcommands:
taint / vulns / critique / adversarial.

Marker-stack composition (this wave proves 3-deep cross-recipe)
---------------------------------------------------------------

* L1 (workflow boundary, W607-AA): ``pr_analyze_*`` markers on
  cmd_pr_analyze's outer envelope when an inner-CliRunner output
  collapse occurs.
* L2 (recipe boundary, W607-AC): ``pr_prep_*`` markers on cmd_pr_prep's
  envelope when one of its substrate helpers raises.
* L3a (compound recipe boundary, sibling W607-AG):
  ``for_refactor_*`` markers on the for_refactor compound envelope.
* L3b (compound recipe boundary, THIS WAVE -- W607-AJ):
  ``for_security_review_*`` markers on the for_security_review compound
  envelope when one of its ``_safe_run`` / ``_cr`` dispatches raises
  before producing a child payload.

Substrate boundaries wrapped by W607-AJ
---------------------------------------

Five substrate-call sites in ``for_security_review()`` get the canonical
``_run_check_aj(phase, fn, *args)`` wrapper:

* ``taint``               -- _safe_run([_cr("taint")], root)
* ``vulns``               -- _safe_run([_cr("vulns"), "list"], root)
* ``critique``            -- _safe_run([_cr("critique")], root)
* ``adversarial``         -- _safe_run([_cr("adversarial"), symbol?], root)
* ``compound_envelope``   -- _compound_envelope("for-security-review", ...)

Each raise becomes a
``for_security_review_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607aj_warnings_out`` and the envelope still emits cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

for_security_review's substrate-call sites are direct calls on
module-level helpers (``_safe_run`` + ``_compound_envelope``) in the
same file plus ``_cr`` lookups against a static registry. The dominant
raise axes are: ``_cr`` KeyError if a registry key drifts (caught at
recipe init by ``_verify_compound_registry``), ``_safe_run`` itself
wraps in try/except so a raise typically lands in the child envelope's
``error`` key NOT a W607-AJ marker (this is the data-shape channel),
and ``_compound_envelope`` raising on aggregator-internal bugs.

For the synthetic raise tests we monkeypatch ``_safe_run`` itself so
the raise bubbles up to ``_run_check_aj`` -- proving the marker
plumbing is engaged.

Marker prefix discipline
------------------------

Marker family is ``for_security_review_*`` -- distinct from every other
W607-* layer including the sibling W607-AG (``for_refactor_*``). The
marker-prefix discipline test pins this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The marker accumulator
+ ``_run_check_aj`` helper live entirely inside the for_security_review
function body; no extracted-helper module needed. Mirrors the W607-AG
discipline.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.

W805-NNNNN / W805-QQQQQ pre-existing pins
-----------------------------------------

The W805-NNNNN audit on cmd_for_security_review surfaced 3 latent bugs:
(A) cmd_adversarial has no click.argument so the recipe's ``symbol``
positional is silently dropped; (B) ``vulns`` is a single
@click.command, not a group, so the ``"list"`` positional is rejected;
(C) the error-storm coalescer trims repeat USAGE_ERROR envelopes to
bare ``isError:True`` shape, which _compound_envelope's failed-detector
misses (silent success on degraded resolution / Pattern-1D).

W805-QQQQQ pinned the SHAPE-axis drift via repo-wide AST sweep. W607-AJ
preserves these pins -- the AST scanner was upgraded in lockstep to
look through the ``_run_check_aj(phase, _safe_run, [_cr(...)], root)``
wrapper-bridge pattern so the drift detection survives the W607
substrate plumbing. The plumbing does NOT fix the underlying bug.

W607-AJ is orthogonal to W805-NNNNN: it covers the substrate-RAISE
axis, while W805-NNNNN pins the silent-drop / silent-success axis.
Both buckets coexist without interaction.
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
    from roam.mcp_server import for_security_review  # noqa: E402
except Exception as _exc:  # pragma: no cover -- guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene: disable the large-response handle-off so envelope inspection
# reads the full compound dict directly (mirrors W607-AG / W805-KK).
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
def for_security_review_project(tmp_path, monkeypatch):
    """A git repo with a real function for the happy-path compound."""
    repo = tmp_path / "w607aj-for-security-review-project"
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
# (1) Happy path -- clean envelope omits W607-AJ substrate markers
# ---------------------------------------------------------------------------


def test_for_security_review_clean_envelope_omits_w607aj_markers(
    for_security_review_project,
):
    """Clean for_security_review on a healthy repo -> no W607-AJ substrate markers.

    Byte-stable: an empty W607-AJ bucket on the success path must produce
    an envelope without W607-AJ substrate markers. The pre-existing
    ``failed_subcommands`` data-shape channel may still surface if any
    inner subcommand returns a top-level ``error`` key, but those are
    NOT W607-AJ substrate-CALL markers.
    """
    r = for_security_review(symbol="handle_login", root=".")
    assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"
    assert r.get("command") == "for-security-review", r.get("command")
    summary = r.get("summary") or {}
    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-AJ substrate markers on the clean envelope.
    top_wo = r.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("for_security_review_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean for_security_review must NOT surface for_security_review_<phase>_failed: "
        f"markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) taint phase failure -> for_security_review_taint_failed marker
# ---------------------------------------------------------------------------


def test_for_security_review_taint_failure_marker_format(for_security_review_project, monkeypatch):
    """If _safe_run raises on the taint dispatch, surface
    ``for_security_review_taint_failed:``.

    Marker shape: ``for_security_review_taint_failed:<exc_class>:<detail>``.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "taint":
            raise RuntimeError("synthetic-taint-from-W607-AJ")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_security_review(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    tn_markers = [m for m in top_wo if m.startswith("for_security_review_taint_failed:")]
    assert tn_markers, f"expected for_security_review_taint_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in tn_markers), tn_markers
    assert any("synthetic-taint-from-W607-AJ" in m for m in tn_markers), tn_markers


# ---------------------------------------------------------------------------
# (3) vulns phase failure -> for_security_review_vulns_failed marker
# ---------------------------------------------------------------------------


def test_for_security_review_vulns_failure_marker_format(for_security_review_project, monkeypatch):
    """If _safe_run raises on the vulns dispatch, surface
    ``for_security_review_vulns_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "vulns":
            raise RuntimeError("synthetic-vulns-from-W607-AJ")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_security_review(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    vn_markers = [m for m in top_wo if m.startswith("for_security_review_vulns_failed:")]
    assert vn_markers, f"expected for_security_review_vulns_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (4) critique phase failure -> marker emitted
# ---------------------------------------------------------------------------


def test_for_security_review_critique_failure_marker_format(for_security_review_project, monkeypatch):
    """If _safe_run raises on the critique dispatch, surface
    ``for_security_review_critique_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "critique":
            raise RuntimeError("synthetic-critique-from-W607-AJ")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_security_review(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    cr_markers = [m for m in top_wo if m.startswith("for_security_review_critique_failed:")]
    assert cr_markers, f"expected for_security_review_critique_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (5) adversarial phase failure -> for_security_review_adversarial_failed marker
# ---------------------------------------------------------------------------


def test_for_security_review_adversarial_failure_marker_format(for_security_review_project, monkeypatch):
    """If _safe_run raises on the adversarial dispatch, surface
    ``for_security_review_adversarial_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "adversarial":
            raise RuntimeError("synthetic-adversarial-from-W607-AJ")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_security_review(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    av_markers = [m for m in top_wo if m.startswith("for_security_review_adversarial_failed:")]
    assert av_markers, f"expected for_security_review_adversarial_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (6) warnings_out lands in both summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_for_security_review_warnings_out_in_both_buckets(for_security_review_project, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "taint":
            raise RuntimeError("synthetic-mirror-from-W607-AJ")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_security_review(symbol="handle_login", root=".")
    assert r.get("warnings_out"), f"top-level warnings_out missing on disclosure path; keys = {sorted(r.keys())!r}"
    summary = r.get("summary") or {}
    assert summary.get("warnings_out"), f"summary.warnings_out missing on disclosure path; got summary = {summary!r}"
    markers = [m for m in r["warnings_out"] if m.startswith("for_security_review_taint_failed:")]
    assert markers, f"expected for_security_review_taint_failed: marker; got {r['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-AJ" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY for_security_review helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_for_security_review_helper_raises(for_security_review_project, monkeypatch):
    """Any non-empty W607-AJ bucket -> summary.partial_success = True."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "taint":
            raise RuntimeError("synthetic-partial-success-from-W607-AJ")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_security_review(symbol="handle_login", root=".")
    summary = r.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(for_security_review_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AG contracts.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "taint":
            raise PermissionError("synthetic-shape-detail-from-W607-AJ")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_security_review(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    assert top_wo, "taint guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("for_security_review_taint_failed:")]
    assert failure_markers, f"expected for_security_review_taint_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "for_security_review_taint_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``for_security_review_*`` only
# ---------------------------------------------------------------------------


def test_marker_prefix_for_security_review_not_sibling(for_security_review_project, monkeypatch):
    """Every surfaced W607-AJ marker uses the canonical
    ``for_security_review_*`` prefix family.

    for_security_review is distinct from every other sibling W607-*
    layer. Hard guard against accidental marker-prefix drift --
    particularly important because the dispatched subcommands (taint,
    vulns, critique, adversarial) each have their own marker prefixes
    at the standalone-cmd layer; a leak would corrupt the 3-deep
    cross-recipe disclosure stack. Also distinct from the sibling
    compound recipe W607-AG (``for_refactor_*``).
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "taint":
            raise PermissionError("synthetic-prefix-discipline-from-W607-AJ")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_security_review(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    # Filter to substrate-CALL markers (have ``_failed:`` in the middle).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("for_security_review_"), (
            f"every surfaced W607-AJ marker must use the "
            f"``for_security_review_*`` prefix family (compound recipe "
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
            # Subcommand-layer prefixes that must NOT leak into the
            # compound bucket (3-deep stack discipline).
            ("taint_", "cmd_taint"),
            ("vulns_", "cmd_vulns"),
            ("adversarial_", "cmd_adversarial"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (10) Source-level guard: for_security_review carries the canonical W607-AJ accumulator
# ---------------------------------------------------------------------------


def test_for_security_review_carries_w607aj_accumulator():
    """AST-level guard: for_security_review source carries the W607-AJ accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole recipe body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    assert src_path.exists(), f"mcp_server.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607aj_warnings_out" in src, (
        "W607-AJ accumulator missing from mcp_server.py; the "
        "substrate-CALL marker plumbing for for_security_review has been "
        "removed."
    )
    assert "for_security_review_{phase}_failed" in src, (
        "W607-AJ marker prefix template missing from mcp_server.py; "
        'check the `f"for_security_review_{phase}_failed:..."` line '
        "in for_security_review's _run_check_aj."
    )
    # Parse-tree level: confirm _run_check_aj is defined inside
    # for_security_review().
    tree = ast.parse(src)
    found_run_check_in_fsr = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "for_security_review":
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name == "_run_check_aj":
                    found_run_check_in_fsr = True
                    break
            break
    assert found_run_check_in_fsr, (
        "W607-AJ ``_run_check_aj`` helper not found inside "
        "for_security_review AST; the per-substrate wrapper has been "
        "refactored away."
    )


# ---------------------------------------------------------------------------
# (11) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every for_security_review substrate boundary is wrapped.

    W607-AJ substrate inventory:

    * taint               -- _safe_run([_cr("taint")], root)
    * vulns               -- _safe_run([_cr("vulns"), "list"], root)
    * critique            -- _safe_run([_cr("critique")], root)
    * adversarial         -- _safe_run([_cr("adversarial"), symbol?], root)
    * compound_envelope   -- _compound_envelope("for-security-review", ...)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "taint",
        "vulns",
        "critique",
        "adversarial",
        "compound_envelope",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check_aj("phase",`` or a
        # multi-line block where the phase string is the first argument
        # on the next line -- both are legitimate refactor shapes.
        # Indent depths 8/12/16/20/24 cover the canonical Click-command
        # nesting levels.
        same_line = f'_run_check_aj("{phase}"' in src
        multi_line = (
            f'_run_check_aj(\n        "{phase}"' in src
            or f'_run_check_aj(\n            "{phase}"' in src
            or f'_run_check_aj(\n                "{phase}"' in src
            or f'_run_check_aj(\n                    "{phase}"' in src
            or f'_run_check_aj(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AJ _run_check_aj wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (12) 3-LAYER COMPOSITION -- compound + substrate dual marker stack
# ---------------------------------------------------------------------------


def test_three_layer_marker_composition_compound_plus_substrate(for_security_review_project, monkeypatch):
    """3-deep marker stack composition pin.

    When a raise originates INSIDE a subcommand's substrate (e.g. the
    taint CLI substrate raises during execution), the
    for_security_review aggregator's _safe_run wraps it as a child
    envelope with a top-level ``error`` key (the data-shape channel).
    That goes into failed_subcommands. ORTHOGONALLY, when _safe_run
    ITSELF raises (e.g. in _cr resolution, or in the network/import
    path), the W607-AJ bucket fires.

    This test proves BOTH channels can coexist on the same envelope:
    * One subcommand raises an exception via _safe_run wrapper itself
      (W607-AJ _run_check_aj catches it -> marker on warnings_out).
    * Another subcommand returns a clean error envelope (data-shape
      channel -> failed_subcommands).

    The 3-layer composition: standalone-cmd W607 (innermost, e.g.
    cmd_taint has its own W607 instrumentation that COULD have
    fired) -> _safe_run data-shape channel (middle) ->
    for_security_review W607-AJ substrate-CALL channel (outermost,
    THIS WAVE). Each layer owns its own disclosure bucket; consumers
    pick the layer of interest.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "taint":
            # Raise BEFORE _safe_run gets to wrap -- proves W607-AJ fires.
            raise RuntimeError("synthetic-3layer-from-W607-AJ")
        if args and args[0] == "vulns":
            # Return a clean error envelope -- proves the data-shape
            # channel (failed_subcommands) also fires in parallel.
            return {"error": "synthetic-data-shape-vulns-error"}
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_security_review(symbol="handle_login", root=".")

    # Channel 1 (W607-AJ substrate-CALL): for_security_review_taint_failed marker
    top_wo = r.get("warnings_out") or []
    tn_markers = [m for m in top_wo if m.startswith("for_security_review_taint_failed:")]
    assert tn_markers, f"W607-AJ substrate-CALL channel missing taint marker; got warnings_out={top_wo!r}"

    # Channel 2 (data-shape): vulns in failed_subcommands
    summary = r.get("summary") or {}
    failed = summary.get("failed_subcommands") or []
    assert "vulns" in failed, (
        f"data-shape channel must name 'vulns' in failed_subcommands; got failed_subcommands={failed!r}"
    )

    # Both channels flip partial_success -- proving they coexist.
    assert summary.get("partial_success") is True, summary


# ---------------------------------------------------------------------------
# (13) Sibling parity -- W607-AG cmd_for_refactor surface unchanged
# ---------------------------------------------------------------------------


def test_w607_ag_for_refactor_unaffected():
    """Sibling parity guard: W607-AG for_refactor source unchanged.

    W607-AJ lands only in for_security_review (mcp_server.py). The
    W607-AG for_refactor surface (per-helper ``_run_check`` wrapper +
    ``_w607ag_warnings_out`` accumulator + ``for_refactor_*`` marker
    emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    assert src_path.exists(), f"mcp_server.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ag_warnings_out" in src, (
        "W607-AG accumulator removed from mcp_server.py; W607-AJ must not regress the sibling instrumentation."
    )
    assert "for_refactor_{phase}_failed" in src, (
        "W607-AG marker prefix removed from mcp_server.py; W607-AJ must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (14) W805-NNNNN orthogonality guard -- pre-existing xfail-strict pins
#      still fire correctly when W607-AJ plumbing is in place.
# ---------------------------------------------------------------------------


def test_w805_nnnnn_pins_still_xfail_strict_under_w607_aj():
    """Orthogonality guard: W805-NNNNN xfail-strict pins survive W607-AJ.

    W805-NNNNN pinned 3 latent bugs in for_security_review's recipe:
    (A) cmd_adversarial silent-drop of symbol positional, (B) vulns
    treated as a group with 'list' subcommand, (C) Pattern-1D
    aggregator leak through error-storm coalescer.

    W607-AJ's substrate-CALL marker plumbing wraps each ``_safe_run``
    site in ``_run_check_aj(phase, _safe_run, argv, root)`` -- a
    different call shape. If the W805-NNNNN pin tests are AST-based
    and don't look through the wrapper, the plumbing would
    accidentally HIDE the bugs from the lint. The W805-QQQQQ AST
    scanner was upgraded in lockstep to look through
    ``_run_check_aj``; this test pins that orthogonality discipline.

    Concretely: invoking the W805-NNNNN sweep should produce the same
    drift identities (adversarial,1) and (vulns,1) as before W607-AJ,
    proving the W805 lint still sees the underlying recipe shape.
    """
    # Cross-import the W805-QQQQQ helpers so we run the SAME AST scan
    # the pin file uses. If those imports break, the lint surface
    # changed -- which is exactly what this guard is here to catch.
    sys_path_added = str(Path(__file__).parent)
    if sys_path_added not in sys.path:
        sys.path.insert(0, sys_path_added)
    from test_w805_qqqqq_compound_recipe_shape_axis_drift import (  # noqa: E402
        _MCP_SERVER,
        _scan_drift,
    )

    drifts = _scan_drift(_MCP_SERVER)
    observed = frozenset((d.cli_name, d.positionals) for d in drifts)
    expected_drift_identities = frozenset(
        {
            ("adversarial", 1),
            ("vulns", 1),
        }
    )
    missing = expected_drift_identities - observed
    assert not missing, (
        "W805-NNNNN pins regressed: W607-AJ plumbing accidentally hid "
        "the recipe-shape drifts from the AST scanner. Missing drift "
        f"identities: {sorted(missing)!r}. The W805-QQQQQ AST scanner "
        "must look through ``_run_check_aj(phase, _safe_run, [_cr(...)"
        ", ...], root)`` wrappers (Pass 3) -- otherwise W607 plumbing "
        "would mask every shape-axis pin."
    )


# ---------------------------------------------------------------------------
# (15) USAGE-error / structured-error: empty symbol still succeeds
#
# Unlike for_refactor, for_security_review accepts an empty symbol
# (broad sweep) -- so there's no USAGE_ERROR path to guard against.
# The empty-symbol path should produce a normal compound envelope, NOT
# a structured-error one, and still carry no W607-AJ markers.
# ---------------------------------------------------------------------------


def test_empty_symbol_path_omits_w607aj_markers(for_security_review_project):
    """Empty symbol (broad sweep) -> normal envelope, no W607-AJ markers.

    for_security_review intentionally accepts empty symbol for a
    broad-sweep review. The adversarial dispatch falls back to a
    no-positional call. On the clean path no markers should fire.
    """
    r = for_security_review(symbol="", root=".")
    # Should produce a real compound envelope, not a structured-error.
    assert r.get("command") == "for-security-review", r
    top_wo = r.get("warnings_out") or []
    substrate_markers = [m for m in top_wo if m.startswith("for_security_review_") and "_failed:" in m]
    assert not substrate_markers, (
        f"empty-symbol broad-sweep path must NOT carry W607-AJ substrate markers on a healthy repo; got {top_wo!r}"
    )
