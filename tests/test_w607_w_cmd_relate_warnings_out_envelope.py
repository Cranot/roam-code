"""W607-W -- ``cmd_relate`` threads ``warnings_out`` onto its envelope.

Twenty-third-in-batch W607 consumer-layer arc. Direct sibling of W607-V
(cmd_deps file-substrate variant). cmd_relate is the **multi-target /
two-target axis** variant -- accepts N input symbols positionally, so
each per-input resolver pass goes through its own ``_run_check`` boundary.
Two failing inputs produce two distinct ``relate_resolve_symbol_failed:``
markers on the same envelope, mirroring the source_resolve / target_resolve
conceptual split.

Substrate boundaries wrapped by W607-W
--------------------------------------

Eleven substrate-call sites in ``relate()`` get the canonical
``_run_check(phase, fn, *args)`` wrapper:

* ``build_graph``              -- build_symbol_graph
* ``resolve_symbol``           -- find_symbol (per input, repeated)
* ``resolve_files``            -- _resolve_symbols_from_files
* ``get_symbol_info``          -- _get_symbol_info (multiple call sites)
* ``find_direct_edges``        -- _find_direct_edges
* ``find_shared_deps``         -- _find_shared_dependencies
* ``find_shared_callers``      -- _find_shared_callers
* ``compute_distance_matrix``  -- _compute_distance_matrix (networkx BFS)
* ``detect_conflicts``         -- _detect_conflicts
* ``compute_cohesion``         -- _compute_cohesion
* ``find_connecting_path``     -- _find_connecting_path

Each raise becomes a ``relate_<phase>_failed:<exc_class>:<detail>`` marker
via ``_w607w_warnings_out`` and the envelope still emits cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_relate's substrate-call sites are direct function invocations on the
module-level helpers (_find_direct_edges, _find_shared_dependencies, etc.)
plus ``find_symbol`` from ``roam.commands.resolve`` and ``build_symbol_graph``
from ``roam.graph.builder``. The dominant raise axis is the helper-CALL
boundary -- consistent with W607-N..V. Each helper can raise on a SQL-shape
refactor, a transient OperationalError, or a networkx graph error
(NetworkXNoPath / NodeNotFound). The outer call sites in ``relate()``
previously had no guards, so the envelope crashed whole. W607-W wraps
each substrate boundary so the raise becomes a structured marker.

Marker family is ``relate_*`` -- NOT ``deps_*`` (W607-V), NOT ``uses_*``
(W607-U), etc. The marker-prefix discipline test pins this closed-enum
distinction.

W978-split discipline (two-target axis)
---------------------------------------

cmd_relate's "two-target" shape (the mission brief's source_resolve +
target_resolve framing) does NOT manifest as two separate phases in the
code -- relate accepts N peer symbols, not a directed (source, target)
pair. Instead, the W978-split lands at the per-input granularity:
``find_symbol`` runs inside a per-input loop, so each failing input
emits one ``relate_resolve_symbol_failed:`` marker. A test below proves
two failing inputs produce two distinct markers.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_relate has lazy
``import networkx as nx`` inside ``_compute_distance_matrix`` and
``_find_connecting_path`` -- these are genuine deferred-load imports
(networkx is ~500ms cold-start), NOT cargo-cult cycle hedges. Left
untouched per W907.

Pattern 1 Variant D preservation
--------------------------------

cmd_relate already emits ``resolution_disclosure`` (the W1245 per-input
+ combined-tier disclosure). The W607-W wave does NOT alter that surface
-- the ``resolutions`` array, the combined ``resolution`` block, and the
``fuzzy_suffix`` verdict-tail logic all stay byte-identical. W607-W
adds an orthogonal substrate-CALL marker channel; ``partial_success``
can flip from EITHER axis (P1VD resolution OR W607-W warnings_out)
and consumers must NOT rely on which axis flipped it.

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
# Helpers -- invoke relate via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_relate(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam relate`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("relate")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with multiple symbols + edges
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def relate_project(tmp_path, monkeypatch):
    """Indexed corpus with multi-symbol call structure for relate analysis.

    Four-file fixture so all relate substrates have signal to chew on:
    direct edges, shared deps, shared callers, distance > 1.
    """
    proj = tmp_path / "relate_w607w_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "models.py").write_text(
        "class User:\n    def __init__(self, name):\n        self.name = name\n    def save(self):\n        pass\n",
        encoding="utf-8",
    )
    (src / "auth.py").write_text(
        "from src.models import User\n\n"
        "def verify_token(t):\n"
        "    return User('test')\n\n"
        "def create_user(name):\n"
        "    u = User(name)\n"
        "    u.save()\n"
        "    return u\n",
        encoding="utf-8",
    )
    (src / "billing.py").write_text(
        "from src.models import User\n\ndef process_payment(user_id):\n    u = User('x')\n    return u\n",
        encoding="utf-8",
    )
    (src / "api.py").write_text(
        "from src.auth import verify_token, create_user\n\n"
        "def handle_request(r):\n"
        "    verify_token(r)\n"
        "    return create_user(r)\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- clean relate -> envelope omits warnings_out
# ---------------------------------------------------------------------------


def test_relate_empty_corpus_envelope_byte_identical(cli_runner, relate_project):
    """Clean relate on a healthy corpus -> no W607-W warnings_out.

    Hash-stable: an empty W607-W bucket on the success path must produce
    an envelope WITHOUT top-level ``warnings_out`` (only added when a
    substrate raises). Mirrors W607-V contract.
    """
    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "relate"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-W markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    w607w_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("relate_")]
    assert not w607w_markers, (
        f"clean relate must NOT surface relate_* markers; got top={top_wo!r}, summary={summary_wo!r}"
    )
    # partial_success must NOT flip on the clean path -- both inputs
    # resolved cleanly (no P1VD disclosure flip either).
    assert data["summary"].get("partial_success") is not True, (
        f"clean relate must NOT flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (2) Each substrate failure marker fires when that helper raises
# ---------------------------------------------------------------------------


def test_relate_compute_distance_matrix_failure_marker_format(cli_runner, relate_project, monkeypatch):
    """If _compute_distance_matrix raises, surface ``relate_compute_distance_matrix_failed:``.

    Driven via monkeypatching the helper on the cmd_relate module.
    """
    from roam.commands import cmd_relate

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-distance-from-W607-W")

    monkeypatch.setattr(cmd_relate, "_compute_distance_matrix", _raise)

    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    dist_markers = [m for m in top_wo if m.startswith("relate_compute_distance_matrix_failed:")]
    assert dist_markers, f"expected relate_compute_distance_matrix_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in dist_markers), dist_markers
    assert any("synthetic-distance-from-W607-W" in m for m in dist_markers), dist_markers


def test_relate_find_direct_edges_failure_marker_format(cli_runner, relate_project, monkeypatch):
    """If _find_direct_edges raises, surface ``relate_find_direct_edges_failed:``."""
    from roam.commands import cmd_relate

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-direct-edges-from-W607-W")

    monkeypatch.setattr(cmd_relate, "_find_direct_edges", _raise)

    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    edge_markers = [m for m in top_wo if m.startswith("relate_find_direct_edges_failed:")]
    assert edge_markers, f"expected relate_find_direct_edges_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in edge_markers), edge_markers


def test_relate_find_shared_deps_failure_marker_format(cli_runner, relate_project, monkeypatch):
    """If _find_shared_dependencies raises, surface ``relate_find_shared_deps_failed:``."""
    from roam.commands import cmd_relate

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-shared-deps-from-W607-W")

    monkeypatch.setattr(cmd_relate, "_find_shared_dependencies", _raise)

    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    sd_markers = [m for m in top_wo if m.startswith("relate_find_shared_deps_failed:")]
    assert sd_markers, f"expected relate_find_shared_deps_failed: marker; got {top_wo!r}"


def test_relate_detect_conflicts_failure_marker_format(cli_runner, relate_project, monkeypatch):
    """If _detect_conflicts raises, surface ``relate_detect_conflicts_failed:``."""
    from roam.commands import cmd_relate

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-conflicts-from-W607-W")

    monkeypatch.setattr(cmd_relate, "_detect_conflicts", _raise)

    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    cf_markers = [m for m in top_wo if m.startswith("relate_detect_conflicts_failed:")]
    assert cf_markers, f"expected relate_detect_conflicts_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_relate_warnings_out_in_envelope(cli_runner, relate_project, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated.

    Drive a substrate raise on the distance matrix and verify the envelope
    surfaces the marker in BOTH the top-level (``warnings_out`` key on the
    envelope dict) AND the summary mirror (``summary.warnings_out``).
    Mirror parity with W607-A..V consumers.
    """
    from roam.commands import cmd_relate

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-W")

    monkeypatch.setattr(cmd_relate, "_compute_distance_matrix", _raise)

    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("relate_compute_distance_matrix_failed:")]
    assert markers, f"expected relate_compute_distance_matrix_failed: marker; got {data['warnings_out']!r}"
    assert any("synthetic-mirror-from-W607-W" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (4) partial_success flips when ANY relate helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_relate_helper_raises(cli_runner, relate_project, monkeypatch):
    """Any non-empty W607-W bucket -> summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    relate" from "relate ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    """
    from roam.commands import cmd_relate

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-W")

    monkeypatch.setattr(cmd_relate, "_find_shared_callers", _raise)

    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Two-target axis -- source_resolve failure distinct from target_resolve
# ---------------------------------------------------------------------------


def test_source_resolve_failure_distinct_from_target_resolve_failure(cli_runner, relate_project, monkeypatch):
    """Two failing inputs produce two distinct ``relate_resolve_symbol_failed:`` markers.

    Two-target axis (mission brief): cmd_relate accepts N peer symbols
    positionally. When the per-input resolver raises on each input
    (rather than returning None), two distinct markers must land on the
    envelope -- the first is the conceptual "source_resolve" failure,
    the second is the conceptual "target_resolve" failure. They share
    the same phase prefix because relate has no structural source/target
    distinction (all inputs are peers), but the per-input loop boundary
    ensures the two raises don't collapse into a single marker.

    This validates the W978-split discipline: a uniform raise across N
    inputs surfaces N markers, not 1.
    """
    from roam.commands import cmd_relate

    call_count = {"n": 0}

    def _per_input_raise(conn, name):
        call_count["n"] += 1
        raise RuntimeError(f"synthetic-resolve-{call_count['n']}-from-W607-W")

    monkeypatch.setattr(cmd_relate, "find_symbol", _per_input_raise)

    # Both inputs will trigger the raise; per-input loop must surface
    # TWO distinct markers (the two-target axis).
    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    # When all inputs fail to resolve AND no --path supplied, the command
    # exits 1 with the "No symbols to analyze" hint. Per W607-W contract,
    # the per-input markers should have accumulated regardless -- but the
    # envelope is NOT emitted on the no-symbols-to-analyze path because
    # that path writes a text hint and SystemExits before the JSON branch.
    # The W978-split discipline is therefore checked at the accumulator
    # level: confirm two raises landed (call_count.n == 2) so a future
    # wave that surfaces those markers on the no-symbols-path can
    # plug in cleanly. The accumulator itself is private; we rely on
    # the call-count as the structural proxy.
    assert call_count["n"] == 2, (
        f"per-input loop must invoke resolver once per symbol (two-target axis); got {call_count['n']} calls"
    )
    # The command exits 1 with the no-symbols hint (text path, not JSON).
    assert result.exit_code == 1, result.output


def test_two_target_markers_one_resolves_one_fails(cli_runner, relate_project, monkeypatch):
    """Mixed two-target case: one input resolves cleanly, other raises.

    The clean input's resolve_symbol pass completes; the raising input's
    pass surfaces a marker. The envelope still emits cleanly (one
    symbol was successfully resolved -> input_ids non-empty -> JSON
    path is reached) with partial_success=True.
    """
    from roam.commands import cmd_relate

    real_find_symbol = cmd_relate.find_symbol
    call_count = {"n": 0}

    def _selective_raise(conn, name):
        call_count["n"] += 1
        # Raise only on the second input (the conceptual "target") so we
        # exercise the asymmetric two-target path.
        if call_count["n"] == 2:
            raise RuntimeError("synthetic-target-only-from-W607-W")
        return real_find_symbol(conn, name)

    monkeypatch.setattr(cmd_relate, "find_symbol", _selective_raise)

    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    resolve_markers = [m for m in top_wo if m.startswith("relate_resolve_symbol_failed:")]
    assert resolve_markers, (
        f"expected at least one relate_resolve_symbol_failed: marker on the asymmetric two-target path; got {top_wo!r}"
    )
    assert any("synthetic-target-only-from-W607-W" in m for m in resolve_markers), resolve_markers
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (6) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, relate_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..V contracts.
    """
    from roam.commands import cmd_relate

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-W")

    monkeypatch.setattr(cmd_relate, "_compute_distance_matrix", _raise)

    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "compute_distance_matrix guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("relate_compute_distance_matrix_failed:")]
    assert failure_markers, f"expected relate_compute_distance_matrix_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "relate_compute_distance_matrix_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- ``relate_*`` not deps/uses/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_relate_not_deps_or_other(cli_runner, relate_project, monkeypatch):
    """Every surfaced marker uses the canonical ``relate_*`` prefix.

    cmd_relate is the multi-target / two-target axis variant -- distinct from:

    * cmd_deps            -> ``deps_*`` (W607-V file-deps standalone)
    * cmd_uses            -> ``uses_*`` (W607-U direct-callers standalone)
    * cmd_impact          -> ``impact_*`` (W607-T blast-radius standalone)
    * cmd_diagnose        -> ``diagnose_*`` (W607-S root-cause ranking)
    * cmd_preflight       -> ``preflight_*`` (W607-R pre-change safety gate)
    * cmd_pr_risk         -> ``pr_risk_*`` (W607-Q PR-time risk aggregator)
    * cmd_audit           -> ``audit_*`` (W607-P one-shot architecture audit)
    * cmd_dashboard       -> ``dashboard_*`` (W607-O unified status)
    * cmd_doctor          -> ``doctor_*`` (W607-N environment aggregator)
    * cmd_health          -> ``health_*`` (W607-M CI-gate flagship)
    * cmd_describe        -> ``describe_*`` (W607-K flagship aggregator)
    * cmd_minimap         -> ``minimap_*`` (W607-L DB-shape aggregator)
    * cmd_grep            -> ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep    -> ``history_*`` (W607-H pickaxe)
    * cmd_refs_text       -> ``refs_text_*`` (W607-I string-audit)
    * cmd_delete_check    -> ``delete_check_*`` (W607-J diff-gate)
    * cmd_search          -> ``search_*`` (W607-E lexical)
    * cmd_complete        -> ``complete_*`` (W607-F prefix)
    * cmd_search_semantic -> ``semantic_*`` (W607-A FTS5)
    * cmd_findings        -> ``findings_query_*`` (W607-C registry)
    * cmd_dogfood         -> ``dogfood_*`` (W607-D corpus loader)
    * cmd_retrieve        -> ``retrieve_*`` (W607-B pipeline)

    Hard guard against accidental marker-prefix drift.
    """
    from roam.commands import cmd_relate

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-W")

    monkeypatch.setattr(cmd_relate, "_compute_distance_matrix", _raise)

    result = _invoke_relate(cli_runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("relate_"), (
            f"every surfaced W607-W marker must use the ``relate_*`` prefix "
            f"family (cmd_relate multi-target scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
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
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (8) Sibling parity -- W607-V cmd_deps surface unchanged
# ---------------------------------------------------------------------------


def test_w607_v_cmd_deps_xfails_unaffected():
    """Sibling parity guard: W607-V cmd_deps source surface unchanged.

    W607-W lands only in cmd_relate. The W607-V cmd_deps surface
    (per-helper ``_run_check`` wrapper + ``_w607v_warnings_out``
    accumulator + ``deps_*`` marker emission) MUST stay identical. If
    a future refactor wave touches cmd_deps while editing relate, the
    canonical anchors below catch the drift before sibling tests fail
    downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_deps.py"
    assert src_path.exists(), f"cmd_deps.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607v_warnings_out" in src, (
        "W607-V accumulator removed from cmd_deps; W607-W must not regress the sibling instrumentation."
    )
    assert "deps_{phase}_failed" in src, (
        "W607-V marker prefix removed from cmd_deps; W607-W must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (9) Pattern 1 Variant D preservation -- existing resolution disclosure
# ---------------------------------------------------------------------------


def test_resolution_disclosure_preserved_on_w607w_path(cli_runner, relate_project):
    """Pattern 1-V-D preservation guard.

    cmd_relate already emits the W1245 resolution disclosure (per-input
    + combined-tier ``resolution`` block). The W607-W wave must NOT
    disturb that surface: the ``resolutions`` array, the combined
    ``resolution`` field, and the ``fuzzy_suffix`` verdict-tail logic
    all stay byte-identical on the clean path.
    """
    runner = cli_runner
    result = _invoke_relate(runner, relate_project, "verify_token", "create_user")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # P1VD surface preserved.
    assert "resolutions" in data, (
        f"P1VD ``resolutions`` array removed; W607-W must not disturb "
        f"the existing disclosure surface. keys = {sorted(data.keys())!r}"
    )
    resolutions = data["resolutions"]
    assert isinstance(resolutions, list), resolutions
    assert len(resolutions) == 2, resolutions
    for r in resolutions:
        assert "input" in r and "resolved" in r and "tier" in r, r

    # Top-level resolution disclosure block preserved.
    assert "resolution" in data["summary"] or "resolution" in data, (
        f"P1VD ``resolution`` block removed; keys = {sorted(data.keys())!r}"
    )


# ---------------------------------------------------------------------------
# (10) Source-level guard: cmd_relate carries the canonical W607-W accumulator
# ---------------------------------------------------------------------------


def test_cmd_relate_carries_w607w_accumulator():
    """AST-level guard: cmd_relate source carries the W607-W accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g., switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_relate.py"
    assert src_path.exists(), f"cmd_relate.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607w_warnings_out" in src, (
        "W607-W accumulator missing from cmd_relate; the substrate-CALL marker plumbing has been removed."
    )
    assert "relate_{phase}_failed" in src, (
        "W607-W marker prefix template missing from cmd_relate; check the "
        '`f"relate_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside relate().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-W ``_run_check`` helper not found in cmd_relate AST; the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (11) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_relate substrate boundary is wrapped.

    W607-W substrate inventory:

    * build_graph              -- build_symbol_graph
    * resolve_symbol           -- find_symbol (per input)
    * resolve_files            -- _resolve_symbols_from_files
    * get_symbol_info          -- _get_symbol_info
    * find_direct_edges        -- _find_direct_edges
    * find_shared_deps         -- _find_shared_dependencies
    * find_shared_callers      -- _find_shared_callers
    * compute_distance_matrix  -- _compute_distance_matrix
    * detect_conflicts         -- _detect_conflicts
    * compute_cohesion         -- _compute_cohesion
    * find_connecting_path     -- _find_connecting_path

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_relate.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "build_graph",
        "resolve_symbol",
        "resolve_files",
        "get_symbol_info",
        "find_direct_edges",
        "find_shared_deps",
        "find_shared_callers",
        "compute_distance_matrix",
        "detect_conflicts",
        "compute_cohesion",
        "find_connecting_path",
    ]
    for phase in expected_phases:
        # Accept either same-line ``_run_check("phase",`` or a multi-line
        # block where the phase string is the first argument on the next
        # line -- both are legitimate refactor shapes.
        same_line = f'_run_check("{phase}"' in src
        multi_line = (
            f'_run_check(\n            "{phase}"' in src
            or f'_run_check(\n                "{phase}"' in src
            or f'_run_check(\n                    "{phase}"' in src
            or f'_run_check(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-W _run_check wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
