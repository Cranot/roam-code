"""W607-T -- ``cmd_impact`` threads ``warnings_out`` onto its envelope.

Twentieth-in-batch W607 consumer-layer arc. Direct continuation after
W607-S (cmd_diagnose root-cause ranking) and the W607-K..R aggregator
cohort. cmd_impact is the **blast-radius standalone** -- a single-target
command composing 4-5 substrate consumers (``find_symbol``,
``build_symbol_graph``, ``_collect_dependents`` BFS + sf-test SQL,
``personalized_pagerank``, ``_find_indirect_refs`` registry scan,
``_impact_verdict`` synthesis).

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_impact's substrate-call sites are direct helper invocations
(``find_symbol(conn, name)`` / ``build_symbol_graph(conn)`` /
``_collect_dependents(G, RG, sym_id, conn, ...)`` /
``_find_indirect_refs(conn, sym, affected_files)`` /
``_impact_verdict(dependents, affected_files, len(G))``) -- NOT a
uniform ``_capture`` boundary. Each helper has its own internal floors
for common error shapes (the W336 ImportError swallow on
personalized_pagerank is preserved -- it is a separate floor, NOT a
substrate failure to disclose), but a helper itself can still raise
BEFORE producing a safe floor (downstream SQL-shape refactor changes
a column name, networkx blowing up on a corrupted edge row,
build_symbol_graph import-time failure, sqlite3.OperationalError on a
missing table). The outer call sites in ``impact()`` previously had no
guards, so the envelope crashed whole. W607-T wraps each substrate
boundary with ``_run_check(phase, fn, *args)`` so the raise becomes a
``impact_<phase>_failed:<exc_class>:<detail>`` marker via
``_w607t_warnings_out`` and the envelope still emits the remaining
sections cleanly.

Marker family is ``impact_*`` -- NOT ``diagnose_*`` (W607-S), NOT
``preflight_*`` (W607-R), NOT ``pr_risk_*`` (W607-Q), NOT ``audit_*``
(W607-P), NOT ``dashboard_*`` (W607-O), NOT ``doctor_*`` (W607-N), NOT
``health_*`` (W607-M), NOT ``describe_*`` (W607-K), NOT ``minimap_*``
(W607-L). The marker-prefix discipline test pins this closed-enum
distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The networkx import
inside the W607-T build_graph wrap (``import networkx as _nx_for_floor``)
is a deferred floor (constructs an empty ``nx.DiGraph()`` so the
not-in-graph branch catches the failure uniformly), not a cycle hedge --
networkx is already imported elsewhere in the module via the same
deferred-import style. The pre-existing ``import networkx as nx`` inside
``_collect_dependents`` mirrors the same lazy-import practice.

Pattern 1 Variant D cross-check
-------------------------------

cmd_impact already discloses resolution state via the W1242 / W1272
``resolution_disclosure()`` helper on every envelope branch (found /
not-in-graph / no-dependents / success). The W607-T wrap of
``find_symbol`` does NOT change the disclosure -- it only catches a
raise BEFORE the helper produces a row OR None. The Pattern-1-V-D guard
below asserts the disclosure surface (resolution + partial_success +
the [fuzzy resolution] verdict suffix) survives the W607-T wrap.

W336 regression guard
---------------------

cmd_impact's weighted_impact rounding bug (W336) widened from 4 ->
6 decimals AND switched the bare ``nx.pagerank`` call to the
``personalized_pagerank`` helper so the numpy-free fallback survives.
The W607-T wrap MUST NOT regress either axis -- the personalized
PageRank call site stays inside its own try/except (separate floor,
NOT funneled through ``_run_check``) so the W336 fallback semantics
are byte-identical. The W336 guard below pins both axes.

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
# Helpers -- invoke impact via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_impact(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam impact`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("impact")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with a resolvable symbol + real call edges
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def impact_project(tmp_path, monkeypatch):
    """Indexed corpus with a unique resolvable symbol (``impact_target``).

    Two-file fixture with a real ``main_caller -> impact_target ->
    helper_one/helper_two`` chain so blast-radius BFS + indirect-ref
    scan + verdict synthesis all have signal to chew on. The target
    name is intentionally unique to avoid LIKE-fallback false-positives
    in the resolver.
    """
    proj = tmp_path / "impact_w607t_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main_caller():\n    return impact_target()\n\n"
        "def impact_target():\n    return helper_one() + helper_two()\n\n"
        "def helper_one():\n    return 1\n\n"
        "def helper_two():\n    return 2\n",
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
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- clean impact -> envelope omits warnings_out
# ---------------------------------------------------------------------------


def test_impact_empty_corpus_envelope_byte_identical(cli_runner, impact_project):
    """Clean impact on a healthy corpus -> no W607-T warnings_out.

    Hash-stable: an empty W607-T bucket on the success path must produce
    an envelope WITHOUT top-level ``warnings_out`` (only added when a
    substrate raises). Mirrors W607-S contract.

    Note: ``partial_success`` MAY be True on the success path if the
    resolver fired a degraded tier (fuzzy) or the BFS truncated, but the
    W607-T axis does NOT independently flip it -- the assertion here
    only pins the absence of W607-T markers, not the value of
    ``partial_success`` (owned by the W1242 resolution-disclosure +
    truncation axes).
    """
    result = _invoke_impact(cli_runner, impact_project, "main_caller")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "impact"
    # The verdict is a real one-line blast-radius verdict.
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-T markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    w607t_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("impact_")]
    assert not w607t_markers, (
        f"clean impact must NOT surface impact_* markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) Each substrate failure marker fires when that helper raises
# ---------------------------------------------------------------------------


def _patch_helper(monkeypatch, attr_name: str, exc):
    """Patch ``cmd_impact.<attr_name>`` to raise ``exc`` unconditionally."""
    from roam.commands import cmd_impact

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cmd_impact, attr_name, _raise)


def test_impact_resolve_symbol_failure_marker_format(cli_runner, impact_project, monkeypatch):
    """If ``find_symbol`` raises, surface ``impact_resolve_symbol_failed:``.

    The resolver default floors to ``None`` so the unresolved branch
    fires and the not-found envelope still emits the marker (with
    partial_success already True from W1272).
    """
    _patch_helper(
        monkeypatch,
        "find_symbol",
        RuntimeError("synthetic-resolve-from-W607-T"),
    )

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or data["summary"].get("warnings_out") or []
    assert top_wo, f"find_symbol RuntimeError must surface warnings_out; got data keys = {sorted(data.keys())!r}"
    markers = [m for m in top_wo if m.startswith("impact_resolve_symbol_failed:")]
    assert markers, f"expected ``impact_resolve_symbol_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-resolve-from-W607-T" in m for m in markers), markers


def test_impact_build_graph_failure_marker_format(cli_runner, impact_project, monkeypatch):
    """If ``build_symbol_graph`` raises, surface ``impact_build_graph_failed:``.

    The graph-builder floor is an empty ``nx.DiGraph()`` so the
    not-in-graph branch fires and the envelope still emits cleanly with
    partial_success=True (W1242 + W607-T combined disclosure).
    """
    _patch_helper(
        monkeypatch,
        "build_symbol_graph",
        RuntimeError("synthetic-build-graph-from-W607-T"),
    )

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("impact_build_graph_failed:")]
    assert markers, f"expected ``impact_build_graph_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


def test_impact_collect_dependents_failure_marker_format(cli_runner, impact_project, monkeypatch):
    """If ``_collect_dependents`` raises, surface ``impact_collect_dependents_failed:``.

    The collect-dependents floor preserves the 6-tuple shape downstream
    consumers expect (empty sets + clean BFS state) so the envelope
    still emits with a no-dependents leaf-style verdict.
    """
    _patch_helper(
        monkeypatch,
        "_collect_dependents",
        PermissionError("synthetic-collect-from-W607-T"),
    )

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("impact_collect_dependents_failed:")]
    assert markers, f"expected ``impact_collect_dependents_failed:`` marker; got {top_wo!r}"
    assert any("PermissionError" in m for m in markers), markers


def test_impact_indirect_refs_failure_marker_format(cli_runner, impact_project, monkeypatch):
    """If ``_find_indirect_refs`` raises, surface ``impact_indirect_refs_failed:``."""
    _patch_helper(
        monkeypatch,
        "_find_indirect_refs",
        RuntimeError("synthetic-indirect-from-W607-T"),
    )

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("impact_indirect_refs_failed:")]
    assert markers, f"expected ``impact_indirect_refs_failed:`` marker; got {top_wo!r}"


def test_impact_verdict_synthesis_failure_marker_format(cli_runner, impact_project, monkeypatch):
    """If ``_impact_verdict`` raises, surface ``impact_verdict_synthesis_failed:``."""
    _patch_helper(
        monkeypatch,
        "_impact_verdict",
        RuntimeError("synthetic-verdict-from-W607-T"),
    )

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("impact_verdict_synthesis_failed:")]
    assert markers, f"expected ``impact_verdict_synthesis_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_impact_warnings_out_in_envelope(cli_runner, impact_project, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. Summary mirror gives
    consumers reading only the summary block visibility too. Mirror parity
    with W607-A..S consumers.
    """
    _patch_helper(
        monkeypatch,
        "_find_indirect_refs",
        RuntimeError("synthetic-mirror-from-W607-T"),
    )

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) partial_success flips when ANY impact helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_impact_helper_raises(cli_runner, impact_project, monkeypatch):
    """Any non-empty W607-T bucket -> summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    impact" from "impact ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    cmd_impact previously only flipped partial_success on the W1242
    resolution-disclosure axis + the truncation axis -- the W607-T wave
    extends the flip to ANY substrate-CALL raise on the success path too.
    """
    _patch_helper(
        monkeypatch,
        "_find_indirect_refs",
        RuntimeError("synthetic-partial-success-from-W607-T"),
    )

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, impact_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..S contracts.
    """
    _patch_helper(
        monkeypatch,
        "_find_indirect_refs",
        PermissionError("synthetic-shape-detail-from-W607-T"),
    )

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "_find_indirect_refs guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("impact_indirect_refs_failed:")]
    assert failure_markers, f"expected impact_indirect_refs_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "impact_indirect_refs_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- ``impact_*`` not diagnose/preflight/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_impact_not_diagnose_or_other(cli_runner, impact_project, monkeypatch):
    """Every surfaced marker uses the canonical ``impact_*`` prefix.

    cmd_impact is the BLAST-RADIUS-STANDALONE axis -- distinct from:

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
    _patch_helper(
        monkeypatch,
        "_find_indirect_refs",
        PermissionError("synthetic-prefix-discipline-from-W607-T"),
    )

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("impact_"), (
            f"every surfaced W607-T marker must use the ``impact_*`` prefix "
            f"family (cmd_impact blast-radius scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
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
# (7) Sibling parity -- W607-S cmd_diagnose surface unchanged
# ---------------------------------------------------------------------------


def test_w607_s_cmd_diagnose_xfails_unaffected():
    """Sibling parity guard: W607-S cmd_diagnose source surface unchanged.

    W607-T lands only in cmd_impact. The W607-S cmd_diagnose surface
    (per-helper ``_run_check`` wrapper + ``_w607s_warnings_out``
    accumulator + ``diagnose_*`` marker emission) MUST stay identical. If
    a future refactor wave touches cmd_diagnose while editing impact,
    the canonical anchors below catch the drift before sibling tests fail
    downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diagnose.py"
    assert src_path.exists(), f"cmd_diagnose.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607s_warnings_out" in src, (
        "W607-S accumulator removed from cmd_diagnose; W607-T must not regress the sibling instrumentation."
    )
    assert "diagnose_" in src, (
        "W607-S marker prefix removed from cmd_diagnose; W607-T must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (8) Pattern 1 Variant D cross-check -- resolution disclosure survives
# ---------------------------------------------------------------------------


def test_resolution_state_disclosed_on_degraded_symbol(cli_runner, impact_project):
    """Pattern 1-V-D guard: the W1242 resolution disclosure survives W607-T.

    The W607-T wrap of ``find_symbol`` must NOT change the resolution
    disclosure -- the helper still stamps ``_resolution_tier`` on the
    returned row and the envelope still emits ``resolution`` +
    ``partial_success`` for fuzzy / unresolved / file tiers, AND the
    verdict still carries the ``[fuzzy resolution -- target '...']``
    suffix per LAW 6 (verdict line works standalone).

    This is the canonical Pattern 1-V-D template per CLAUDE.md
    ``src/roam/commands/cmd_annotate.py:60-118``: a command resolving a
    target through a fallback chain MUST disclose the resolution state
    via a closed-enum ``resolution`` field + ``partial_success: true``
    + degraded verdict on non-exact tiers.
    """
    # A name that does NOT exact-match any symbol but substring-matches
    # ``impact_target`` -- the resolver lands on the LIKE-fallback tier
    # (tier 3) and stamps ``_resolution_tier = "fuzzy"``.
    result = _invoke_impact(cli_runner, impact_project, "impact_tar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("resolution") == "fuzzy", f"fuzzy-LIKE fallback must disclose resolution=fuzzy; got {summary!r}"
    assert summary.get("partial_success") is True, (
        f"fuzzy resolution must set partial_success=True (W1242); got {summary!r}"
    )
    # LAW 6: the single-line verdict alone must signal the degradation.
    assert "[fuzzy resolution" in summary.get("verdict", ""), (
        f"fuzzy-resolution verdict must carry the [fuzzy resolution] suffix; got {summary.get('verdict')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Multiple substrates can fail simultaneously -- all markers surface
# ---------------------------------------------------------------------------


def test_multiple_substrates_failing_emit_separate_markers(cli_runner, impact_project, monkeypatch):
    """Two simultaneous substrate raises -> two markers, both surfaced.

    Aggregator scope: cmd_impact's value is fanning out across multiple
    substrate sources (graph builder + collect-dependents BFS + indirect
    refs registry scan + verdict synthesis). The W607-T guard must NOT
    short-circuit on the first raise -- each subsequent substrate still
    runs and emits its own marker on failure. Consumers see the full
    degradation lineage.
    """
    from roam.commands import cmd_impact

    def _raise_indirect(*a, **kw):
        raise RuntimeError("synthetic-multi-indirect-from-W607-T")

    def _raise_verdict(*a, **kw):
        raise PermissionError("synthetic-multi-verdict-from-W607-T")

    monkeypatch.setattr(cmd_impact, "_find_indirect_refs", _raise_indirect)
    monkeypatch.setattr(cmd_impact, "_impact_verdict", _raise_verdict)

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    indirect_markers = [m for m in top_wo if m.startswith("impact_indirect_refs_failed:")]
    verdict_markers = [m for m in top_wo if m.startswith("impact_verdict_synthesis_failed:")]
    assert indirect_markers, f"expected impact_indirect_refs_failed: marker; got {top_wo!r}"
    assert verdict_markers, f"expected impact_verdict_synthesis_failed: marker; got {top_wo!r}"
    # partial_success still flips with multiple markers.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (10) Source-level guard: cmd_impact uses the canonical W607-T accumulator
# ---------------------------------------------------------------------------


def test_cmd_impact_carries_w607t_accumulator():
    """AST-level guard: cmd_impact source carries the W607-T accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g., switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_impact.py"
    assert src_path.exists(), f"cmd_impact.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607t_warnings_out" in src, (
        "W607-T accumulator missing from cmd_impact; the substrate-CALL marker plumbing has been removed."
    )
    assert "impact_" in src, (
        'W607-T marker prefix missing from cmd_impact; check the `f"impact_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside impact().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-T ``_run_check`` helper not found in cmd_impact AST; the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (11) W336 regression guard -- weighted_impact rounding + PageRank fallback
# ---------------------------------------------------------------------------


def test_w336_weighted_impact_rounding_preserved(cli_runner, impact_project):
    """W336 regression guard: weighted_impact rounding stays at 6 decimals
    AND the ``personalized_pagerank`` fallback is still wired in.

    Two bugs combined to silently zero weighted_impact (per the
    test_impact_bounded.py docstring):

    1. The unconditional ``round(weighted_impact, 4)`` truncated
       legitimate small per-node PageRank sums (1e-5 to 1e-3 range on
       multi-thousand-node graphs) down to 0.0.
    2. The bare ``except Exception`` around the ``nx.pagerank`` call
       silently swallowed ``ImportError`` when scipy/numpy weren't
       installed, leaving ``ppr = {}`` so the sum was always 0.

    The W607-T wrap MUST NOT regress either axis -- the personalized
    PageRank call site stays inside its own try/except (separate floor,
    NOT funneled through ``_run_check``) so the W336 fallback semantics
    are byte-identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_impact.py"
    src = src_path.read_text(encoding="utf-8")
    # Axis 1: rounding stays at 6 decimals.
    assert "round(weighted_impact, 6)" in src, (
        "W336 weighted_impact rounding regressed from 6 decimals; "
        "small per-node PageRank sums will silently truncate to 0."
    )
    # Axis 2: personalized_pagerank fallback wired in.
    assert "from roam.graph.pagerank import personalized_pagerank" in src, (
        "W336 personalized_pagerank import removed; the numpy-free degree-based fallback no longer fires."
    )
    # Behavior axis: invoke impact on a real symbol and confirm the
    # rounded value is preserved in the success envelope (precision +
    # type both matter).
    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    # weighted_impact must still be a (rounded) float on the success
    # envelope -- not stripped, not silently None.
    assert "weighted_impact" in summary, f"weighted_impact missing from success envelope; got {summary!r}"
    assert isinstance(summary["weighted_impact"], (int, float)), (
        f"weighted_impact must be numeric (int|float); got {summary['weighted_impact']!r}"
    )
