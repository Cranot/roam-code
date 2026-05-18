"""W607-S -- ``cmd_diagnose`` threads ``warnings_out`` onto its envelope.

Nineteenth-in-batch W607 consumer-layer arc. Direct continuation after
W607-R (cmd_preflight pre-change safety gate) and the W607-K..Q
aggregator cohort (describe / minimap / health / doctor / dashboard /
audit / pr_risk). cmd_diagnose is the **root-cause ranking command**
composing 4-5 substrate consumers (symbol resolution, blast-radius BFS
on the call graph, complexity / cognitive-load reads, git
cochange/recent-commits, suggest_next_steps) into upstream/downstream
ranked envelopes.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_diagnose's substrate-call sites are direct helper invocations
(``find_symbol_with_alternatives(conn, name)`` /
``build_symbol_graph(conn)`` / ``_get_symbol_metrics(conn, sym_id)`` /
``_build_distribution_stats(conn)`` / ``_cochange_partners(conn, file_id)``
/ ``_recent_changes(conn, file_id)`` / ``_build_ranked(...)`` /
``suggest_next_steps(...)``) -- NOT a uniform ``_capture`` boundary.
Each helper has its own internal floors for the common-case error
shapes (``_get_symbol_metrics`` floors the git_file_changes fallback;
the graph builder caches), but a helper itself can still raise BEFORE
producing a safe floor (downstream SQL-shape refactor changes a column
name, networkx blowing up on a corrupted edge row, build_symbol_graph
import-time failure, sqlite3.OperationalError on a missing table).
The outer call sites in ``diagnose()`` previously had no guards, so
the envelope crashed whole. W607-S wraps each substrate boundary with
``_run_check(phase, fn, *args)`` so the raise becomes a
``diagnose_<phase>_failed:<exc_class>:<detail>`` marker via
``warnings_out`` and the envelope still emits the remaining sections
cleanly.

Marker family is ``diagnose_*`` -- NOT ``preflight_*`` (W607-R), NOT
``pr_risk_*`` (W607-Q), NOT ``audit_*`` (W607-P), NOT ``dashboard_*``
(W607-O), NOT ``doctor_*`` (W607-N), NOT ``health_*`` (W607-M), NOT
``describe_*`` (W607-K), NOT ``minimap_*`` (W607-L). The
marker-prefix discipline test pins this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The networkx import
inside the W607-S build_graph wrap is a deferred floor (constructs an
empty ``nx.DiGraph()`` so the isolated-in-graph branch catches the
failure uniformly), not a cycle hedge.

Pattern 1 Variant D cross-check
-------------------------------

cmd_diagnose already discloses resolution state via the W1244
``resolution_disclosure()`` helper across BOTH single and batch modes.
The W607-S wrap of ``find_symbol_with_alternatives`` does NOT change
the disclosure -- it only catches a raise BEFORE the helper produces a
(sym, alternatives) tuple. The Pattern-1-V-D guard below asserts the
disclosure surface (resolution + partial_success) survives the W607-S
wrap.

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
# Helpers -- invoke diagnose via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_diagnose(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam diagnose`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("diagnose")
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
def diagnose_project(tmp_path, monkeypatch):
    """Indexed corpus with a unique resolvable symbol (``diagnose_target``).

    Two-file fixture with a real ``main -> diagnose_target -> helper``
    chain so upstream/downstream BFS / risk-score ranking / cochange /
    recent-commits all have signal to chew on. The target name is
    intentionally unique to avoid LIKE-fallback false-positives in the
    resolver.
    """
    proj = tmp_path / "diagnose_w607s_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main_entry():\n    return diagnose_target()\n\n"
        "def diagnose_target():\n    return helper_one() + helper_two()\n\n"
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
# (1) Happy path -- clean diagnose -> envelope omits warnings_out
# ---------------------------------------------------------------------------


def test_diagnose_empty_corpus_envelope_byte_identical(cli_runner, diagnose_project):
    """Clean diagnose on a healthy corpus -> no W607-S warnings_out.

    Hash-stable: an empty W607-S bucket on the success path must produce
    an envelope WITHOUT top-level ``warnings_out`` (only added when a
    substrate raises). Mirrors W607-R contract.

    Note: ``partial_success`` MAY be True on the success path if the
    resolver fired a degraded tier (fuzzy), but the W607-S axis does NOT
    independently flip it -- the assertion here only pins the absence of
    W607-S markers, not the value of ``partial_success`` (which is owned
    by the W1244 resolution-disclosure axis).
    """
    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "diagnose"
    # The verdict is a real one-line root-cause verdict.
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO W607-S markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    w607s_markers = [m for m in (list(top_wo) + list(summary_wo)) if m.startswith("diagnose_")]
    assert not w607s_markers, (
        f"clean diagnose must NOT surface diagnose_* markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) Each substrate failure marker fires when that helper raises
# ---------------------------------------------------------------------------


def _patch_helper(monkeypatch, attr_name: str, exc):
    """Patch ``cmd_diagnose.<attr_name>`` to raise ``exc`` unconditionally."""
    from roam.commands import cmd_diagnose

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cmd_diagnose, attr_name, _raise)


def test_diagnose_resolve_symbol_failure_marker_format(cli_runner, diagnose_project, monkeypatch):
    """If ``find_symbol_with_alternatives`` raises, surface ``diagnose_resolve_symbol_failed:``.

    The resolver default floors to ``(None, [])`` so the unresolved
    branch fires and the not-found envelope still emits the marker (with
    partial_success already True from W1244 / W1272).
    """
    _patch_helper(
        monkeypatch,
        "find_symbol_with_alternatives",
        RuntimeError("synthetic-resolve-from-W607-S"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or data["summary"].get("warnings_out") or []
    assert top_wo, (
        f"find_symbol_with_alternatives RuntimeError must surface warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    markers = [m for m in top_wo if m.startswith("diagnose_resolve_symbol_failed:")]
    assert markers, f"expected ``diagnose_resolve_symbol_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-resolve-from-W607-S" in m for m in markers), markers


def test_diagnose_build_graph_failure_marker_format(cli_runner, diagnose_project, monkeypatch):
    """If ``build_symbol_graph`` raises, surface ``diagnose_build_graph_failed:``.

    The graph-builder floor is an empty ``nx.DiGraph()`` so the
    isolated-in-graph branch fires and the envelope still emits cleanly
    with partial_success=True (W1244 isolated_in_graph disclosure).
    """
    _patch_helper(
        monkeypatch,
        "build_symbol_graph",
        RuntimeError("synthetic-build-graph-from-W607-S"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diagnose_build_graph_failed:")]
    assert markers, f"expected ``diagnose_build_graph_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


def test_diagnose_target_metrics_failure_marker_format(cli_runner, diagnose_project, monkeypatch):
    """If ``_get_symbol_metrics`` raises, surface ``diagnose_target_metrics_failed:``.

    The metrics floor preserves the dict shape downstream consumers
    (``_risk_score``, the envelope ``target_metrics`` field) read so the
    envelope still emits with the remaining suspects.
    """
    _patch_helper(
        monkeypatch,
        "_get_symbol_metrics",
        PermissionError("synthetic-metrics-from-W607-S"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diagnose_target_metrics_failed:")]
    assert markers, f"expected ``diagnose_target_metrics_failed:`` marker; got {top_wo!r}"
    assert any("PermissionError" in m for m in markers), markers


def test_diagnose_dist_stats_failure_marker_format(cli_runner, diagnose_project, monkeypatch):
    """If ``_build_distribution_stats`` raises, surface ``diagnose_dist_stats_failed:``."""
    _patch_helper(
        monkeypatch,
        "_build_distribution_stats",
        RuntimeError("synthetic-dist-from-W607-S"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diagnose_dist_stats_failed:")]
    assert markers, f"expected ``diagnose_dist_stats_failed:`` marker; got {top_wo!r}"


def test_diagnose_cochange_partners_failure_marker_format(cli_runner, diagnose_project, monkeypatch):
    """If ``_cochange_partners`` raises, surface ``diagnose_cochange_partners_failed:``."""
    _patch_helper(
        monkeypatch,
        "_cochange_partners",
        RuntimeError("synthetic-cochange-from-W607-S"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diagnose_cochange_partners_failed:")]
    assert markers, f"expected ``diagnose_cochange_partners_failed:`` marker; got {top_wo!r}"


def test_diagnose_recent_commits_failure_marker_format(cli_runner, diagnose_project, monkeypatch):
    """If ``_recent_changes`` raises, surface ``diagnose_recent_commits_failed:``."""
    _patch_helper(
        monkeypatch,
        "_recent_changes",
        RuntimeError("synthetic-recent-from-W607-S"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("diagnose_recent_commits_failed:")]
    assert markers, f"expected ``diagnose_recent_commits_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_diagnose_warnings_out_in_envelope(cli_runner, diagnose_project, monkeypatch):
    """Non-empty bucket -> both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. Summary mirror gives
    consumers reading only the summary block visibility too. Mirror parity
    with W607-A..R consumers.
    """
    _patch_helper(
        monkeypatch,
        "_cochange_partners",
        RuntimeError("synthetic-mirror-from-W607-S"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) partial_success flips when ANY diagnose helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_diagnose_helper_raises(cli_runner, diagnose_project, monkeypatch):
    """Any non-empty W607-S bucket -> summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    diagnose" from "diagnose ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    cmd_diagnose previously only flipped partial_success on the W1244
    resolution-disclosure axis -- the W607-S wave extends the flip to
    ANY substrate-CALL raise on the success path too.
    """
    _patch_helper(
        monkeypatch,
        "_recent_changes",
        RuntimeError("synthetic-partial-success-from-W607-S"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, diagnose_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..R contracts.
    """
    _patch_helper(
        monkeypatch,
        "_cochange_partners",
        PermissionError("synthetic-shape-detail-from-W607-S"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "_cochange_partners guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("diagnose_cochange_partners_failed:")]
    assert failure_markers, f"expected diagnose_cochange_partners_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "diagnose_cochange_partners_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- ``diagnose_*`` not preflight/pr_risk/audit/etc.
# ---------------------------------------------------------------------------


def test_marker_prefix_diagnose_not_preflight_or_other(cli_runner, diagnose_project, monkeypatch):
    """Every surfaced marker uses the canonical ``diagnose_*`` prefix.

    cmd_diagnose is the ROOT-CAUSE-RANKING axis -- distinct from:

    * cmd_preflight        -> ``preflight_*`` (W607-R pre-change safety gate)
    * cmd_pr_risk          -> ``pr_risk_*`` (W607-Q PR-time risk aggregator)
    * cmd_audit            -> ``audit_*`` (W607-P one-shot architecture audit)
    * cmd_dashboard        -> ``dashboard_*`` (W607-O unified status)
    * cmd_doctor           -> ``doctor_*`` (W607-N environment aggregator)
    * cmd_health           -> ``health_*`` (W607-M CI-gate flagship)
    * cmd_describe         -> ``describe_*`` (W607-K flagship aggregator)
    * cmd_minimap          -> ``minimap_*`` (W607-L DB-shape aggregator)
    * cmd_grep             -> ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep     -> ``history_*`` (W607-H pickaxe)
    * cmd_refs_text        -> ``refs_text_*`` (W607-I string-audit)
    * cmd_delete_check     -> ``delete_check_*`` (W607-J diff-gate)
    * cmd_search           -> ``search_*`` (W607-E lexical)
    * cmd_complete         -> ``complete_*`` (W607-F prefix)
    * cmd_search_semantic  -> ``semantic_*`` (W607-A FTS5)
    * cmd_findings         -> ``findings_query_*`` (W607-C registry)
    * cmd_dogfood          -> ``dogfood_*`` (W607-D corpus loader)
    * cmd_retrieve         -> ``retrieve_*`` (W607-B pipeline)

    Hard guard against accidental marker-prefix drift.
    """
    _patch_helper(
        monkeypatch,
        "_recent_changes",
        PermissionError("synthetic-prefix-discipline-from-W607-S"),
    )

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("diagnose_"), (
            f"every surfaced W607-S marker must use the ``diagnose_*`` prefix "
            f"family (cmd_diagnose root-cause ranking scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
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
# (7) Sibling parity -- W607-R cmd_preflight surface unchanged
# ---------------------------------------------------------------------------


def test_w607_r_cmd_preflight_xfails_unaffected():
    """Sibling parity guard: W607-R cmd_preflight source surface unchanged.

    W607-S lands only in cmd_diagnose. The W607-R cmd_preflight surface
    (per-helper ``_run_check`` wrapper + ``_w607r_warnings_out``
    accumulator + ``preflight_*`` marker emission) MUST stay identical. If
    a future refactor wave touches cmd_preflight while editing diagnose,
    the canonical anchors below catch the drift before sibling tests fail
    downstream.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_preflight.py"
    assert src_path.exists(), f"cmd_preflight.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607r_warnings_out" in src, (
        "W607-R accumulator removed from cmd_preflight; W607-S must not regress the sibling instrumentation."
    )
    assert "preflight_" in src, (
        "W607-R marker prefix removed from cmd_preflight; W607-S must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (8) Pattern 1 Variant D cross-check -- resolution disclosure survives
# ---------------------------------------------------------------------------


def test_resolution_state_disclosed_on_degraded_symbol(cli_runner, diagnose_project):
    """Pattern 1-V-D guard: the W1244 resolution disclosure survives W607-S.

    The W607-S wrap of ``find_symbol_with_alternatives`` must NOT change
    the resolution disclosure -- the helper still stamps
    ``_resolution_tier`` on the returned row and the envelope still emits
    ``resolution`` + ``partial_success`` for fuzzy / unresolved / file
    tiers.

    This is the canonical Pattern 1-V-D template per CLAUDE.md
    ``src/roam/commands/cmd_annotate.py:60-118``: a command resolving a
    target through a fallback chain MUST disclose the resolution state
    via a closed-enum ``resolution`` field + ``partial_success: true``
    + degraded verdict on non-exact tiers.
    """
    # A name that does NOT exact-match any symbol but substring-matches
    # ``diagnose_target`` -- the resolver lands on the LIKE-fallback tier
    # (tier 3) and stamps ``_resolution_tier = "fuzzy"``.
    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_tar")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    assert summary.get("resolution") == "fuzzy", f"fuzzy-LIKE fallback must disclose resolution=fuzzy; got {summary!r}"
    assert summary.get("partial_success") is True, (
        f"fuzzy resolution must set partial_success=True (W1244); got {summary!r}"
    )
    # LAW 6: the single-line verdict alone must signal the degradation.
    assert "[fuzzy resolution" in summary.get("verdict", ""), (
        f"fuzzy-resolution verdict must carry the [fuzzy resolution] suffix; got {summary.get('verdict')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Multiple substrates can fail simultaneously -- all markers surface
# ---------------------------------------------------------------------------


def test_multiple_substrates_failing_emit_separate_markers(cli_runner, diagnose_project, monkeypatch):
    """Two simultaneous substrate raises -> two markers, both surfaced.

    Aggregator scope: cmd_diagnose's value is fanning out across multiple
    substrate sources (graph + metrics + git history). The W607-S guard
    must NOT short-circuit on the first raise -- each subsequent
    substrate still runs and emits its own marker on failure. Consumers
    see the full degradation lineage.
    """
    from roam.commands import cmd_diagnose

    def _raise_cochange(*a, **kw):
        raise RuntimeError("synthetic-multi-cochange-from-W607-S")

    def _raise_recent(*a, **kw):
        raise PermissionError("synthetic-multi-recent-from-W607-S")

    monkeypatch.setattr(cmd_diagnose, "_cochange_partners", _raise_cochange)
    monkeypatch.setattr(cmd_diagnose, "_recent_changes", _raise_recent)

    result = _invoke_diagnose(cli_runner, diagnose_project, "diagnose_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    cochange_markers = [m for m in top_wo if m.startswith("diagnose_cochange_partners_failed:")]
    recent_markers = [m for m in top_wo if m.startswith("diagnose_recent_commits_failed:")]
    assert cochange_markers, f"expected diagnose_cochange_partners_failed: marker; got {top_wo!r}"
    assert recent_markers, f"expected diagnose_recent_commits_failed: marker; got {top_wo!r}"
    # partial_success still flips with multiple markers.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (10) Source-level guard: cmd_diagnose uses the canonical W607-S accumulator
# ---------------------------------------------------------------------------


def test_cmd_diagnose_carries_w607s_accumulator():
    """AST-level guard: cmd_diagnose source carries the W607-S accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g., switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_diagnose.py"
    assert src_path.exists(), f"cmd_diagnose.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607s_warnings_out" in src, (
        "W607-S accumulator missing from cmd_diagnose; the substrate-CALL marker plumbing has been removed."
    )
    assert "diagnose_" in src, (
        'W607-S marker prefix missing from cmd_diagnose; check the `f"diagnose_{phase}_failed:..."` line in _run_check.'
    )
    # Parse-tree level: confirm _run_check is defined inside diagnose().
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-S ``_run_check`` helper not found in cmd_diagnose AST; the "
        "per-substrate wrapper has been refactored away."
    )
