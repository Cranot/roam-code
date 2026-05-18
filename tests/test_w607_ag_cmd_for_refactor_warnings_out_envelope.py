"""W607-AG -- ``for_refactor`` compound recipe threads ``warnings_out``.

Thirtieth-in-batch W607 consumer-layer arc. This is the FIRST compound-recipe
W607 wave -- prior W607 waves all targeted standalone Click commands or
CLI-side helpers. ``for_refactor`` lives in ``src/roam/mcp_server.py``
(NOT in ``src/roam/commands/cmd_for_refactor.py``) as an
``@_tool(name="roam_for_refactor")`` aggregator that dispatches via
``_safe_run([_cr(<key>), ...])``.

Marker-stack composition (this wave proves 3-deep)
--------------------------------------------------

* L1 (workflow boundary, W607-AA): ``pr_analyze_*`` markers on
  cmd_pr_analyze's outer envelope when an inner-CliRunner output
  collapse occurs.
* L2 (recipe boundary, W607-AC): ``pr_prep_*`` markers on cmd_pr_prep's
  envelope when one of its substrate helpers raises.
* L3 (compound recipe boundary, THIS WAVE -- W607-AG):
  ``for_refactor_*`` markers on the for_refactor compound envelope when
  one of its ``_safe_run`` / ``_cr`` dispatches raises before producing
  a child payload.

Substrate boundaries wrapped by W607-AG
---------------------------------------

Five substrate-call sites in ``for_refactor()`` get the canonical
``_run_check(phase, fn, *args)`` wrapper:

* ``preflight``           -- _safe_run([_cr("preflight"), symbol], root)
* ``impact``              -- _safe_run([_cr("impact"), symbol], root)
* ``complexity_report``   -- _safe_run([_cr("complexity"), "--limit", "5"], root)
* ``clones``              -- _safe_run([_cr("clones"), "--top", "20"], root)
* ``compound_envelope``   -- _compound_envelope("for-refactor", sections, ...)

Each raise becomes a ``for_refactor_<phase>_failed:<exc_class>:<detail>``
marker via ``_w607ag_warnings_out`` and the envelope still emits cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

for_refactor's substrate-call sites are direct calls on module-level
helpers (``_safe_run`` + ``_compound_envelope``) in the same file plus
``_cr`` lookups against a static registry. The dominant raise axes are:
``_cr`` KeyError if a registry key drifts (caught at recipe init by
``_verify_compound_registry`` -- so unreachable at runtime in practice
but defended in depth), ``_safe_run`` itself wraps in try/except so a
raise typically lands in the child envelope's ``error`` key NOT a
W607-AG marker (this is the data-shape channel), and
``_compound_envelope`` raising on aggregator-internal bugs.

For the synthetic raise tests we monkeypatch ``_safe_run`` itself so
the raise bubbles up to ``_run_check`` -- proving the marker plumbing
is engaged.

Marker prefix discipline
------------------------

Marker family is ``for_refactor_*`` -- distinct from every other
W607-* layer. The marker-prefix discipline test pins this closed-enum
distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The marker accumulator
+ _run_check helper live entirely inside the for_refactor function;
no extracted-helper module needed.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.

W805-NNNNN cautionary note
--------------------------

W805-NNNNN's audit on cmd_for_security_review surfaced 3 latent bugs
(missing click.argument, wrong vulns shape, trimmed-isError aggregator
leak). The same audit risk applies here. While plumbing W607-AG, no
NEW latent bugs were discovered in for_refactor itself -- the pre-
existing W805-KK xfail-strict pins (in
``tests/test_w805_kk_cmd_for_refactor_empty_corpus.py``) already cover
the Pattern-2 silent-SAFE / Variant-D bug class on the empty-corpus +
unresolved-symbol axis. W607-AG is orthogonal: it covers the
substrate-RAISE axis. Both buckets coexist without interaction.
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
    from roam.mcp_server import for_refactor  # noqa: E402
except Exception as _exc:  # pragma: no cover -- guarded environments only
    pytest.skip(
        f"roam.mcp_server import failed: {_exc!r}; MCP compound tests require the MCP server module to be importable.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test hygiene: disable the large-response handle-off so envelope inspection
# reads the full compound dict directly (mirrors W805-KK / test_situation_compounds).
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
def for_refactor_project(tmp_path, monkeypatch):
    """A git repo with a real function for the happy-path compound."""
    repo = tmp_path / "w607ag-for-refactor-project"
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
# (1) Happy path -- clean envelope omits W607-AG substrate markers
# ---------------------------------------------------------------------------


def test_for_refactor_clean_envelope_omits_w607ag_markers(for_refactor_project):
    """Clean for_refactor on a healthy repo -> no W607-AG substrate markers.

    Byte-stable: an empty W607-AG bucket on the success path must produce
    an envelope without W607-AG substrate markers. The pre-existing
    ``failed_subcommands`` data-shape channel may still surface if any
    inner subcommand returns a top-level ``error`` key, but those are
    NOT W607-AG substrate-CALL markers.
    """
    r = for_refactor(symbol="handle_login", root=".")
    assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"
    assert r.get("command") == "for-refactor", r.get("command")
    summary = r.get("summary") or {}
    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-AG substrate markers on the clean envelope.
    top_wo = r.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("for_refactor_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean for_refactor must NOT surface for_refactor_<phase>_failed: markers; "
        f"got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) preflight phase failure -> for_refactor_preflight_failed marker
# ---------------------------------------------------------------------------


def test_for_refactor_preflight_failure_marker_format(for_refactor_project, monkeypatch):
    """If _safe_run raises on the preflight dispatch, surface
    ``for_refactor_preflight_failed:``.

    Marker shape: ``for_refactor_preflight_failed:<exc_class>:<detail>``.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "preflight":
            raise RuntimeError("synthetic-preflight-from-W607-AG")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    pf_markers = [m for m in top_wo if m.startswith("for_refactor_preflight_failed:")]
    assert pf_markers, f"expected for_refactor_preflight_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in pf_markers), pf_markers
    assert any("synthetic-preflight-from-W607-AG" in m for m in pf_markers), pf_markers


# ---------------------------------------------------------------------------
# (3) impact phase failure -> for_refactor_impact_failed marker
# ---------------------------------------------------------------------------


def test_for_refactor_impact_failure_marker_format(for_refactor_project, monkeypatch):
    """If _safe_run raises on the impact dispatch, surface
    ``for_refactor_impact_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "impact":
            raise RuntimeError("synthetic-impact-from-W607-AG")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    im_markers = [m for m in top_wo if m.startswith("for_refactor_impact_failed:")]
    assert im_markers, f"expected for_refactor_impact_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (4) complexity_report phase failure -> marker emitted
# ---------------------------------------------------------------------------


def test_for_refactor_complexity_report_failure_marker_format(for_refactor_project, monkeypatch):
    """If _safe_run raises on the complexity dispatch, surface
    ``for_refactor_complexity_report_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "complexity":
            raise RuntimeError("synthetic-complexity-from-W607-AG")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    cx_markers = [m for m in top_wo if m.startswith("for_refactor_complexity_report_failed:")]
    assert cx_markers, f"expected for_refactor_complexity_report_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (5) clones phase failure -> for_refactor_clones_failed marker
# ---------------------------------------------------------------------------


def test_for_refactor_clones_failure_marker_format(for_refactor_project, monkeypatch):
    """If _safe_run raises on the clones dispatch, surface
    ``for_refactor_clones_failed:``."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "clones":
            raise RuntimeError("synthetic-clones-from-W607-AG")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    cl_markers = [m for m in top_wo if m.startswith("for_refactor_clones_failed:")]
    assert cl_markers, f"expected for_refactor_clones_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (6) warnings_out lands in both summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_for_refactor_warnings_out_in_both_buckets(for_refactor_project, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "preflight":
            raise RuntimeError("synthetic-mirror-from-W607-AG")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    assert r.get("warnings_out"), f"top-level warnings_out missing on disclosure path; keys = {sorted(r.keys())!r}"
    summary = r.get("summary") or {}
    assert summary.get("warnings_out"), f"summary.warnings_out missing on disclosure path; got summary = {summary!r}"
    markers = [m for m in r["warnings_out"] if m.startswith("for_refactor_preflight_failed:")]
    assert markers, f"expected for_refactor_preflight_failed: marker; got {r['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-AG" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY for_refactor helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_for_refactor_helper_raises(for_refactor_project, monkeypatch):
    """Any non-empty W607-AG bucket -> summary.partial_success = True."""
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "preflight":
            raise RuntimeError("synthetic-partial-success-from-W607-AG")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    summary = r.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {summary!r}"
    )


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(for_refactor_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AD contracts.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "preflight":
            raise PermissionError("synthetic-shape-detail-from-W607-AG")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    assert top_wo, "preflight guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("for_refactor_preflight_failed:")]
    assert failure_markers, f"expected for_refactor_preflight_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "for_refactor_preflight_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``for_refactor_*`` only
# ---------------------------------------------------------------------------


def test_marker_prefix_for_refactor_not_sibling(for_refactor_project, monkeypatch):
    """Every surfaced W607-AG marker uses the canonical
    ``for_refactor_*`` prefix family.

    cmd_for_refactor is distinct from every other sibling W607-* layer.
    Hard guard against accidental marker-prefix drift -- particularly
    important because the dispatched subcommands (preflight, impact,
    complexity, clones) each have their own marker prefixes at the
    standalone-cmd layer; a leak would corrupt the 3-deep cross-recipe
    disclosure stack.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "preflight":
            raise PermissionError("synthetic-prefix-discipline-from-W607-AG")
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")
    top_wo = r.get("warnings_out") or []
    # Filter to substrate-CALL markers (have ``_failed:`` in the middle).
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("for_refactor_"), (
            f"every surfaced W607-AG marker must use the ``for_refactor_*`` "
            f"prefix family (compound recipe scope); got {marker!r}"
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
            # Subcommand-layer prefixes that must NOT leak into the
            # compound bucket (3-deep stack discipline).
            ("preflight_", "cmd_preflight"),
            ("impact_", "cmd_impact"),
            ("complexity_", "cmd_complexity"),
            ("clones_", "cmd_clones"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (10) Source-level guard: for_refactor carries the canonical W607-AG accumulator
# ---------------------------------------------------------------------------


def test_for_refactor_carries_w607ag_accumulator():
    """AST-level guard: for_refactor source carries the W607-AG accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole recipe body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    assert src_path.exists(), f"mcp_server.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ag_warnings_out" in src, (
        "W607-AG accumulator missing from mcp_server.py; the "
        "substrate-CALL marker plumbing for for_refactor has been removed."
    )
    assert "for_refactor_{phase}_failed" in src, (
        "W607-AG marker prefix template missing from mcp_server.py; check the "
        '`f"for_refactor_{phase}_failed:..."` line in for_refactor\'s _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside for_refactor().
    tree = ast.parse(src)
    found_run_check_in_for_refactor = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "for_refactor":
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name == "_run_check":
                    found_run_check_in_for_refactor = True
                    break
            break
    assert found_run_check_in_for_refactor, (
        "W607-AG ``_run_check`` helper not found inside for_refactor AST; the "
        "per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (11) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every for_refactor substrate boundary is wrapped.

    W607-AG substrate inventory:

    * preflight           -- _safe_run([_cr("preflight"), symbol], root)
    * impact              -- _safe_run([_cr("impact"), symbol], root)
    * complexity_report   -- _safe_run([_cr("complexity"), "--limit", "5"], root)
    * clones              -- _safe_run([_cr("clones"), "--top", "20"], root)
    * compound_envelope   -- _compound_envelope("for-refactor", sections, ...)

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "mcp_server.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "preflight",
        "impact",
        "complexity_report",
        "clones",
        "compound_envelope",
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
            f"W607-AG _run_check wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (12) 3-LAYER COMPOSITION -- compound + substrate dual marker stack
# ---------------------------------------------------------------------------


def test_three_layer_marker_composition_compound_plus_substrate(for_refactor_project, monkeypatch):
    """3-deep marker stack composition pin.

    When a raise originates INSIDE a subcommand's substrate (e.g. the
    preflight CLI substrate raises during execution), the for_refactor
    aggregator's _safe_run wraps it as a child envelope with a top-level
    ``error`` key (the data-shape channel). That goes into
    failed_subcommands. ORTHOGONALLY, when _safe_run ITSELF raises
    (e.g. in _cr resolution, or in the network/import path), the
    W607-AG bucket fires.

    This test proves BOTH channels can coexist on the same envelope:
    * One subcommand raises an exception via _safe_run wrapper itself
      (W607-AG _run_check catches it -> marker on warnings_out).
    * Another subcommand returns a clean error envelope (data-shape
      channel -> failed_subcommands).

    The 3-layer composition: standalone-cmd W607 (innermost, e.g.
    cmd_preflight has its own W607 instrumentation that COULD have
    fired) -> _safe_run data-shape channel (middle) ->
    for_refactor W607-AG substrate-CALL channel (outermost, THIS WAVE).
    Each layer owns its own disclosure bucket; consumers pick the layer
    of interest.
    """
    original = _srv._safe_run

    def _routed(args, root):
        if args and args[0] == "preflight":
            # Raise BEFORE _safe_run gets to wrap -- proves W607-AG fires.
            raise RuntimeError("synthetic-3layer-from-W607-AG")
        if args and args[0] == "impact":
            # Return a clean error envelope -- proves the data-shape
            # channel (failed_subcommands) also fires in parallel.
            return {"error": "synthetic-data-shape-impact-error"}
        return original(args, root)

    monkeypatch.setattr(_srv, "_safe_run", _routed)

    r = for_refactor(symbol="handle_login", root=".")

    # Channel 1 (W607-AG substrate-CALL): for_refactor_preflight_failed marker
    top_wo = r.get("warnings_out") or []
    pf_markers = [m for m in top_wo if m.startswith("for_refactor_preflight_failed:")]
    assert pf_markers, f"W607-AG substrate-CALL channel missing preflight marker; got warnings_out={top_wo!r}"

    # Channel 2 (data-shape): impact in failed_subcommands
    summary = r.get("summary") or {}
    failed = summary.get("failed_subcommands") or []
    assert "impact" in failed, (
        f"data-shape channel must name 'impact' in failed_subcommands; got failed_subcommands={failed!r}"
    )

    # Both channels flip partial_success -- proving they coexist.
    assert summary.get("partial_success") is True, summary


# ---------------------------------------------------------------------------
# (13) USAGE_ERROR path still bypasses W607-AG plumbing (no markers)
# ---------------------------------------------------------------------------


def test_usage_error_path_omits_w607ag_markers(for_refactor_project):
    """The empty-symbol USAGE_ERROR path returns a structured-error envelope
    before any substrate dispatch -- so no W607-AG markers should appear."""
    r = for_refactor(symbol="", root=".")
    # Structured-error shape, not a compound envelope.
    assert r.get("isError") is True, r
    assert "USAGE_ERROR" in (r.get("error_code") or ""), r.get("error_code")
    # No W607-AG markers on the structured-error path (substrate not dispatched).
    top_wo = r.get("warnings_out") or []
    substrate_markers = [m for m in top_wo if m.startswith("for_refactor_") and "_failed:" in m]
    assert not substrate_markers, f"USAGE_ERROR path must NOT carry W607-AG substrate markers; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (14) Sibling parity -- W607-AC cmd_pr_prep surface unchanged
# ---------------------------------------------------------------------------


def test_w607_ac_cmd_pr_prep_unaffected():
    """Sibling parity guard: W607-AC cmd_pr_prep source unchanged.

    W607-AG lands only in for_refactor (mcp_server.py). The W607-AC
    cmd_pr_prep surface (per-helper ``_run_check`` wrapper +
    ``_w607ac_warnings_out`` accumulator + ``pr_prep_*`` marker emission)
    MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_prep.py"
    assert src_path.exists(), f"cmd_pr_prep.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ac_warnings_out" in src, (
        "W607-AC accumulator removed from cmd_pr_prep; W607-AG must not regress the sibling instrumentation."
    )
    assert "pr_prep_{phase}_failed" in src, (
        "W607-AC marker prefix removed from cmd_pr_prep; W607-AG must not regress the sibling marker family."
    )
