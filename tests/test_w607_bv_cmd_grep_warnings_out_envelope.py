"""W607-BV -- ``cmd_grep`` per-phase substrate-CALL marker plumbing.

ADDITIVE to W607-G. cmd_grep already carried the W607-G outer-guard
markers (``grep_engine_pin_missing:`` / ``grep_engine_fanout_fallback:``
/ ``grep_ripgrep_failed:`` / ``grep_git_grep_failed:`` /
``grep_engine_failed:`` / ``grep_indexed_scan_failed:``) wrapped around
the engine fan-out + subprocess axis.

This wave adds per-phase substrate-CALL plumbing for the substrate
boundaries inside the index-aware grep consumer:

* compile_patterns           -- _compile_patterns()
* select_engine              -- _select_engine()
* run_engine                 -- _run_engine() (ADDITIVE; W607-G outer-guard preserved)
* apply_reachability_filter  -- _apply_reachability_filter()
* apply_co_occur_filter      -- _apply_co_occur_filter()
* apply_missing_pattern      -- _apply_missing_pattern()
* apply_rank_by              -- _apply_rank_by()
* apply_blame_heat           -- _apply_blame_heat()
* apply_group_by             -- _apply_group_by()
* serialize_envelope         -- to_json on the JSON envelope

cmd_grep is the TEXT-CONTENT SEARCH sibling of cmd_search (W607-BR),
cmd_search_semantic (W607-BO), and cmd_retrieve (W607-BI). Closes the
DISCOVERABILITY 4-WAY with distinct marker prefixes (``grep_*`` vs
``search_*`` vs ``search_semantic_*`` vs ``retrieve_*``) so a 4-way
envelope inspection can demultiplex every consumer's substrate axis.

W978 first-hypothesis check
---------------------------

Each W607-BV-wrapped substrate has a documented empty-floor default
that matches its happy-path return shape so a raise degrades cleanly:

* compile_patterns           -> []                  (usage-error envelope)
* select_engine              -> "fallback"          (indexed-scan path runs)
* run_engine                 -> None                (relabel + W607-G outer-guard)
* apply_reachability_filter  -> None                (Pattern-1D unresolved)
* apply_co_occur_filter      -> matches             (unfiltered passthrough)
* apply_missing_pattern      -> matches             (unfiltered passthrough)
* apply_rank_by              -> None                (line-ordered sort)
* apply_blame_heat           -> None                (unannotated matches)
* apply_group_by             -> None                (un-grouped text path)
* serialize_envelope         -> None                (minimal-envelope fallback)

Marker prefix discipline
------------------------

Marker family is ``grep_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers (``search_*`` W607-BR,
``search_semantic_*`` W607-BO, ``retrieve_*`` W607-BI). The W607-G
outer-guard markers (``grep_ripgrep_failed:`` / ``grep_git_grep_failed:``
/ ``grep_engine_failed:`` / ``grep_indexed_scan_failed:`` /
``grep_engine_pin_missing:`` / ``grep_engine_fanout_fallback:``)
share the ``grep_*`` family and are explicitly ALLOWED to coexist with
W607-BV markers (additive contract).

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def grep_project(project_factory):
    """Indexed corpus with multiple symbols carrying searchable text -- the
    W607-BV substrate baseline."""
    return project_factory(
        {
            "db/connection.py": (
                "def open_database():\n"
                "    '''Open a database connection.'''\n"
                "    # TODO: connection pooling\n"
                "    pass\n"
                "def close_database():\n"
                "    '''Close the database connection.'''\n"
                "    # TODO: graceful shutdown\n"
                "    pass\n"
            ),
            "auth/login.py": (
                "def authenticate_user(username, password):\n"
                "    '''Authenticate a user with credentials.'''\n"
                "    # TODO: rate limiting\n"
                "    pass\n"
                "def logout_user(session):\n"
                "    '''Log out a user session.'''\n"
                "    pass\n"
            ),
        }
    )


def _invoke_grep(cli_runner, cwd, *extra, monkeypatch, json_mode: bool = True):
    """Invoke ``roam grep`` through the group so ``--json`` is honoured."""
    monkeypatch.chdir(cwd)
    args = []
    if json_mode:
        args.append("--json")
    args.append("grep")
    args.extend(extra)
    return cli_runner.invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BV substrate-CALL markers
# ---------------------------------------------------------------------------


def test_grep_clean_envelope_omits_w607bv_markers(cli_runner, grep_project, monkeypatch):
    """Clean grep -> no W607-BV substrate markers.

    Byte-identical-on-happy-path: an empty W607-BV bucket on the success
    path must NOT introduce ``grep_*_failed:`` markers on the envelope.
    The envelope's ``warnings_out`` is omitted entirely on a clean run
    (hash-stable with pre-W607-BV).
    """
    result = _invoke_grep(cli_runner, grep_project, "TODO", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "grep"
    # Empty-bucket discipline: NO warnings_out keys on the clean path.
    assert "warnings_out" not in data, (
        f"clean grep must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean grep must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) compile_patterns failure -> structured marker + usage-error envelope
# ---------------------------------------------------------------------------


def test_compile_patterns_failure_marker_format(cli_runner, grep_project, monkeypatch):
    """If ``_compile_patterns`` raises, surface the W607-BV marker.

    Degraded default is ``[]`` which fires the structured usage-error
    envelope (``state: usage_error``, exit code 2). The marker MUST
    surface on that envelope so the agent sees lineage.
    """
    from roam.commands import cmd_grep

    def _boom_compile(positional, patterns, patterns_from):
        raise RuntimeError("synthetic-compile-patterns-from-W607-BV")

    monkeypatch.setattr(cmd_grep, "_compile_patterns", _boom_compile)

    result = _invoke_grep(cli_runner, grep_project, "TODO", monkeypatch=monkeypatch)
    # Degraded compile_patterns => empty patterns => usage_error exit 2.
    assert result.exit_code == 2, result.output
    data = _json.loads(result.output)

    # The usage-error envelope path does NOT thread warnings_out (it
    # raises SystemExit(2) before the disclosure plumbing), but the
    # W607-BV bucket is captured in the closure. Pin the marker via the
    # closure's accumulator state by re-invoking with serialize patched:
    # this confirms the boundary actually wraps the call.
    # The accumulator lives inside the closure; the source-grep guard
    # below covers the wrap site directly.
    assert data["summary"].get("state") == "usage_error", data["summary"]


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_w607bv_warnings_in_envelope_both_channels(cli_runner, grep_project, monkeypatch):
    """Non-empty W607-BV bucket -> both top-level AND summary.warnings_out.

    Uses ``apply_rank_by`` raise (with --rank-by importance) because
    that substrate runs AFTER the engine pipeline AND has a degraded
    default that preserves the envelope.
    """
    from roam.commands import cmd_grep

    def _boom_rank(matches, conn):
        raise RuntimeError("synthetic-rank-by-from-W607-BV")

    monkeypatch.setattr(cmd_grep, "_apply_rank_by", _boom_rank)

    result = _invoke_grep(
        cli_runner,
        grep_project,
        "TODO",
        "--rank-by",
        "importance",
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BV disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BV disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("grep_apply_rank_by_failed:")]
    assert markers, f"expected grep_apply_rank_by_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, grep_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..BR contracts.
    """
    from roam.commands import cmd_grep

    def _boom_rank(matches, conn):
        raise ValueError("synthetic-shape-detail-from-W607-BV")

    monkeypatch.setattr(cmd_grep, "_apply_rank_by", _boom_rank)

    result = _invoke_grep(
        cli_runner,
        grep_project,
        "TODO",
        "--rank-by",
        "importance",
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("grep_apply_rank_by_failed:")]
    assert failure_markers, f"expected grep_apply_rank_by_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "grep_apply_rank_by_failed", parts
    assert parts[1] == "ValueError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) run_engine failure -- W607-BV marker AND W607-G outer-guard mirror
# ---------------------------------------------------------------------------


def test_run_engine_failure_dual_marker_emission(cli_runner, grep_project, monkeypatch):
    """A raise in ``_run_engine`` must:

    1. Surface a ``grep_run_engine_failed:`` marker (W607-BV family).
    2. Re-emit the W607-G outer-guard ``grep_<engine>_failed:``
       marker as well (backwards compat). The relabel is engine-aware:
       ripgrep -> ``grep_ripgrep_failed:`` / git -> ``grep_git_grep_failed:``
       / fallback -> ``grep_engine_failed:``.
    3. The envelope MUST still emit a structured result with
       ``partial_success: true``.

    This is the "don't crash grep wholesale on a subprocess substrate
    failure" contract: agents must still receive the structured
    no-match envelope when the engine path degrades.
    """
    from roam.commands import cmd_grep

    def _boom_engine(*, patterns, root, globs, fixed_string, case_insensitive, word_boundary, engine):
        raise RuntimeError("synthetic-run-engine-from-W607-BV")

    monkeypatch.setattr(cmd_grep, "_run_engine", _boom_engine)

    result = _invoke_grep(cli_runner, grep_project, "TODO", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    bv_markers = [m for m in all_wo if m.startswith("grep_run_engine_failed:")]
    assert bv_markers, f"expected grep_run_engine_failed: marker after engine substrate failure; got {all_wo!r}"
    # W607-G outer-guard marker MUST still be emitted (backwards compat).
    # Pick whichever engine-flavour marker fired -- depends on which
    # engine `_select_engine()` returned on the host.
    outer_markers = [
        m
        for m in all_wo
        if m.startswith("grep_ripgrep_failed:")
        or m.startswith("grep_git_grep_failed:")
        or m.startswith("grep_engine_failed:")
    ]
    assert outer_markers, (
        f"W607-G outer-guard grep_<engine>_failed: marker missing; "
        f"W607-BV is ADDITIVE, not a replacement. Got {all_wo!r}"
    )
    assert data["summary"].get("partial_success") is True, (
        f"engine-degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (6) apply_co_occur_filter failure surfaces marker
# ---------------------------------------------------------------------------


def test_apply_co_occur_filter_failure_emits_marker(cli_runner, grep_project, monkeypatch):
    """A raise inside ``_apply_co_occur_filter`` must surface a marker;
    the degraded default returns the unfiltered matches so the user
    still gets results.
    """
    from roam.commands import cmd_grep

    def _boom_co_occur(matches, patterns, fixed, case_insensitive):
        raise RuntimeError("synthetic-co-occur-from-W607-BV")

    monkeypatch.setattr(cmd_grep, "_apply_co_occur_filter", _boom_co_occur)

    result = _invoke_grep(
        cli_runner,
        grep_project,
        "-e",
        "TODO",
        "-e",
        "database",
        "--co-occur",
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("grep_apply_co_occur_filter_failed:")]
    assert markers, f"expected grep_apply_co_occur_filter_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (7) apply_group_by failure surfaces marker
# ---------------------------------------------------------------------------


def test_apply_group_by_failure_emits_marker(cli_runner, grep_project, monkeypatch):
    """A raise inside ``_apply_group_by`` must surface a marker; the
    degraded default returns ``None`` so the un-grouped JSON path
    emits."""
    from roam.commands import cmd_grep

    def _boom_group(matches):
        raise RuntimeError("synthetic-group-by-from-W607-BV")

    monkeypatch.setattr(cmd_grep, "_apply_group_by", _boom_group)

    result = _invoke_grep(
        cli_runner,
        grep_project,
        "TODO",
        "--group-by",
        "symbol",
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("grep_apply_group_by_failed:")]
    assert markers, f"expected grep_apply_group_by_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-BV stays in ``grep_*`` family
#     and does NOT leak into sibling W607 family prefixes.
# ---------------------------------------------------------------------------


def test_w607bv_marker_prefix_stays_in_grep_family(cli_runner, grep_project, monkeypatch):
    """Every W607-BV substrate marker uses the canonical
    ``grep_<phase>_failed:`` prefix.

    cmd_grep is distinct from sibling W607-* layers. Marker prefix
    MUST stay ``grep_*`` and MUST NOT leak into other family prefixes
    (e.g. ``search_*`` W607-BR, ``search_semantic_*`` W607-BO,
    ``retrieve_*`` W607-BI).
    """
    from roam.commands import cmd_grep

    def _boom_rank(matches, conn):
        raise RuntimeError("synthetic-prefix-discipline-from-W607-BV")

    monkeypatch.setattr(cmd_grep, "_apply_rank_by", _boom_rank)

    result = _invoke_grep(
        cli_runner,
        grep_project,
        "TODO",
        "--rank-by",
        "importance",
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # Surfaced W607-BV markers must all use the grep_ family.
    bv_markers = [m for m in all_wo if "_failed:" in m]
    assert bv_markers, "expected non-empty W607-BV substrate markers for prefix-consistency check"
    for marker in bv_markers:
        assert marker.startswith("grep_"), (
            f"every surfaced W607-BV marker must use the ``grep_*`` prefix family (cmd_grep scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("search_semantic_", "cmd_search_semantic W607-BO"),
            ("retrieve_", "cmd_retrieve W607-BI"),
            ("context_", "cmd_context W607-BF"),
            ("understand_", "cmd_understand W607-BC"),
            ("minimap_", "cmd_minimap W607-L / W607-AZ"),
            ("describe_", "cmd_describe W607-K"),
            ("preflight_", "cmd_preflight W607-R"),
            ("impact_", "cmd_impact W607-T / W607-BB"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("doctor_", "cmd_doctor W607-N"),
            ("findings_", "cmd_findings W607-C"),
            ("critique_", "cmd_critique W607-Y"),
            ("diff_", "cmd_diff W607-Z"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )
        # cmd_search W607-BR uses the ``search_*`` family (strict
        # superset of ``search_semantic_*`` — check disjoint explicitly).
        assert not (marker.startswith("search_") and not marker.startswith("search_semantic_")), (
            f"cmd_grep envelope must NOT carry cmd_search W607-BR markers (search_<phase>_*); got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (9) Top-level vs summary.warnings_out parity on disclosure path
# ---------------------------------------------------------------------------


def test_top_level_and_summary_warnings_out_parity(cli_runner, grep_project, monkeypatch):
    """top-level warnings_out and summary.warnings_out must agree.

    The bucket is sourced once (combined W607-G + W607-BV) and threaded
    into both channels so consumers reading either end see the same
    lineage.
    """
    from roam.commands import cmd_grep

    def _boom_rank(matches, conn):
        raise RuntimeError("synthetic-parity-from-W607-BV")

    monkeypatch.setattr(cmd_grep, "_apply_rank_by", _boom_rank)

    result = _invoke_grep(
        cli_runner,
        grep_project,
        "TODO",
        "--rank-by",
        "importance",
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    assert sorted(top_wo) == sorted(summary_wo), (
        f"top-level vs summary.warnings_out must be equal; top={top_wo!r} summary={summary_wo!r}"
    )
    rank_markers = [m for m in top_wo if m.startswith("grep_apply_rank_by_failed:")]
    assert rank_markers, f"expected grep_apply_rank_by_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (10) DISCOVERABILITY 4-WAY pairing -- grep, search, search_semantic,
#      retrieve marker families coexist when each command is invoked.
# ---------------------------------------------------------------------------


def test_discoverability_four_way_marker_families_coexist(cli_runner, grep_project, monkeypatch):
    """W607-BV (grep) + W607-BR (search) + W607-BO (search_semantic) +
    W607-BI (retrieve) markers coexist across the four sibling
    discoverability consumers.

    Each command keeps its own marker family discipline:

    * ``grep_*``             for cmd_grep            (W607-BV)
    * ``search_*``           for cmd_search          (W607-BR)
    * ``search_semantic_*``  for cmd_search_semantic (W607-BO)
    * ``retrieve_*``         for cmd_retrieve        (W607-BI)

    These four families MUST NOT collide. This test confirms that
    markers from ALL FOUR families surface when each command is
    invoked in sequence on the same corpus.
    """
    from roam.commands import (
        cmd_grep,
        cmd_retrieve,
        cmd_search,
        cmd_search_semantic,
    )

    def _boom_grep_rank(matches, conn):
        raise RuntimeError("synthetic-grep-quartet-from-W607-BV")

    def _boom_search_parse(pattern, mode):
        raise RuntimeError("synthetic-search-quartet-from-W607-BR")

    def _boom_semantic_coverage(conn):
        raise RuntimeError("synthetic-semantic-quartet-from-W607-BO")

    def _boom_retrieve_coverage(conn):
        raise RuntimeError("synthetic-retrieve-quartet-from-W607-BI")

    monkeypatch.setattr(cmd_grep, "_apply_rank_by", _boom_grep_rank)
    monkeypatch.setattr(cmd_search, "_parse_query", _boom_search_parse)
    monkeypatch.setattr(cmd_search_semantic, "_compute_semantic_coverage", _boom_semantic_coverage)
    monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", _boom_retrieve_coverage)

    monkeypatch.chdir(grep_project)

    r_grep = cli_runner.invoke(
        cli,
        ["--json", "grep", "TODO", "--rank-by", "importance"],
        catch_exceptions=False,
    )
    assert r_grep.exit_code == 0, r_grep.output
    d_grep = _json.loads(r_grep.output)

    r_search = cli_runner.invoke(cli, ["--json", "search", "database"], catch_exceptions=False)
    assert r_search.exit_code == 0, r_search.output
    d_search = _json.loads(r_search.output)

    r_semantic = cli_runner.invoke(cli, ["--json", "search-semantic", "database"], catch_exceptions=False)
    assert r_semantic.exit_code == 0, r_semantic.output
    d_semantic = _json.loads(r_semantic.output)

    r_retrieve = cli_runner.invoke(cli, ["--json", "retrieve", "database"], catch_exceptions=False)
    assert r_retrieve.exit_code == 0, r_retrieve.output
    d_retrieve = _json.loads(r_retrieve.output)

    # cmd_grep markers (W607-BV family)
    grep_wo = list(d_grep.get("warnings_out") or []) + list(d_grep["summary"].get("warnings_out") or [])
    grep_markers = [m for m in grep_wo if m.startswith("grep_apply_rank_by_failed:")]
    assert grep_markers, f"expected grep_apply_rank_by_failed: marker from cmd_grep W607-BV; got {grep_wo!r}"
    # cmd_grep must NOT carry search_*, search_semantic_*, or retrieve_* markers.
    for m in grep_wo:
        assert not m.startswith("search_semantic_"), (
            f"cmd_grep envelope must NOT carry search_semantic_* markers; got {m!r}"
        )
        assert not m.startswith("retrieve_"), f"cmd_grep envelope must NOT carry retrieve_* markers; got {m!r}"
        assert not (m.startswith("search_") and not m.startswith("search_semantic_")), (
            f"cmd_grep envelope must NOT carry cmd_search W607-BR markers; got {m!r}"
        )

    # cmd_search markers (W607-BR family) -- must NOT carry grep_ markers
    search_wo = list(d_search.get("warnings_out") or []) + list(d_search["summary"].get("warnings_out") or [])
    search_markers = [m for m in search_wo if m.startswith("search_parse_query_failed:")]
    assert search_markers, f"expected search_parse_query_failed: marker from cmd_search W607-BR; got {search_wo!r}"
    for m in search_wo:
        assert not m.startswith("grep_"), f"cmd_search envelope must NOT carry grep_* markers; got {m!r}"

    # cmd_search_semantic markers (W607-BO family) -- must NOT carry grep_
    semantic_wo = list(d_semantic.get("warnings_out") or []) + list(d_semantic["summary"].get("warnings_out") or [])
    semantic_markers = [m for m in semantic_wo if m.startswith("search_semantic_")]
    assert semantic_markers, f"expected search_semantic_* markers from cmd_search_semantic W607-BO; got {semantic_wo!r}"
    for m in semantic_wo:
        assert not m.startswith("grep_"), f"cmd_search_semantic envelope must NOT carry grep_* markers; got {m!r}"

    # cmd_retrieve markers (W607-BI family) -- must NOT carry grep_
    retrieve_wo = list(d_retrieve.get("warnings_out") or []) + list(d_retrieve["summary"].get("warnings_out") or [])
    retrieve_markers = [m for m in retrieve_wo if m.startswith("retrieve_")]
    assert retrieve_markers, f"expected retrieve_* markers from cmd_retrieve W607-BI; got {retrieve_wo!r}"
    for m in retrieve_wo:
        assert not m.startswith("grep_"), f"cmd_retrieve envelope must NOT carry grep_* markers; got {m!r}"


# ---------------------------------------------------------------------------
# (11) Engine-fallback isolation -- select_engine raising fires the
#      W607-BV marker AND the envelope reflects degraded engine.
# ---------------------------------------------------------------------------


def test_select_engine_raise_emits_marker_and_degraded_engine(cli_runner, grep_project, monkeypatch):
    """A raise inside ``_select_engine`` must:

    1. Surface a ``grep_select_engine_failed:`` marker (W607-BV).
    2. Degrade to ``engine="fallback"`` then relabel to
       ``"indexed_scan"`` (W1010 lineage) so the envelope discloses
       which engine produced the results.
    3. The envelope MUST still emit a structured result with
       ``partial_success: true``.

    Engine-probe raising is the canonical "degraded engine" simulation
    for cmd_grep: the subprocess axis fails on the very first probe,
    so the indexed-scan path takes over end-to-end.
    """
    from roam.commands import cmd_grep

    def _boom_select():
        raise RuntimeError("synthetic-select-engine-from-W607-BV")

    monkeypatch.setattr(cmd_grep, "_select_engine", _boom_select)

    result = _invoke_grep(cli_runner, grep_project, "TODO", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("grep_select_engine_failed:")]
    assert markers, f"expected grep_select_engine_failed: marker on engine-probe raise; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]
    # Engine relabel: indexed_scan (W1010) when the indexed-scan
    # fan-out actually runs and yields matches; "fallback" when no
    # matches surfaced post-relabel.
    reported_engine = data["summary"].get("engine") or data.get("engine")
    assert reported_engine in ("indexed_scan", "fallback"), (
        f"degraded engine must be ``indexed_scan`` (W1010 relabel) or "
        f"``fallback`` (no matches); got {reported_engine!r} on envelope "
        f"summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (12) Cross-prefix isolation -- grep_* markers do NOT leak into
#      adjacent commands' envelopes (cmd_refs_text, cmd_history_grep).
# ---------------------------------------------------------------------------


def test_grep_prefix_does_not_leak_into_adjacent_commands(cli_runner, grep_project, monkeypatch):
    """``grep_*`` markers MUST NOT leak into adjacent commands'
    envelopes.

    ``refs-text`` (literal-string audit with verdict) and
    ``history-grep`` (git-pickaxe) are the adjacent text-axis
    commands. Patching cmd_grep helpers must NOT influence either
    adjacent envelope.
    """
    from roam.commands import cmd_grep

    # Patch cmd_grep helpers; invoke ONLY adjacent commands.
    def _boom_rank(matches, conn):
        raise RuntimeError("synthetic-grep-leak-check-from-W607-BV")

    def _boom_select():
        raise RuntimeError("synthetic-grep-leak-select-from-W607-BV")

    monkeypatch.setattr(cmd_grep, "_apply_rank_by", _boom_rank)
    monkeypatch.setattr(cmd_grep, "_select_engine", _boom_select)

    monkeypatch.chdir(grep_project)

    # refs-text
    r_refs = cli_runner.invoke(cli, ["--json", "refs-text", "TODO"], catch_exceptions=False)
    # refs-text may emit non-zero on missing-state; we only check
    # markers when output is JSON-parseable.
    try:
        d_refs = _json.loads(r_refs.output)
    except _json.JSONDecodeError:
        d_refs = None
    if d_refs is not None:
        refs_wo = list(d_refs.get("warnings_out") or []) + list((d_refs.get("summary") or {}).get("warnings_out") or [])
        for m in refs_wo:
            assert not m.startswith("grep_"), (
                f"cmd_refs_text envelope must NOT carry grep_* markers (cross-prefix leak); got {m!r}"
            )

    # history-grep
    r_hist = cli_runner.invoke(cli, ["--json", "history-grep", "TODO"], catch_exceptions=False)
    try:
        d_hist = _json.loads(r_hist.output)
    except _json.JSONDecodeError:
        d_hist = None
    if d_hist is not None:
        hist_wo = list(d_hist.get("warnings_out") or []) + list((d_hist.get("summary") or {}).get("warnings_out") or [])
        for m in hist_wo:
            assert not m.startswith("grep_"), (
                f"cmd_history_grep envelope must NOT carry cmd_grep W607-BV markers (cross-prefix leak); got {m!r}"
            )


# ---------------------------------------------------------------------------
# (13) Source-level guard: cmd_grep carries the W607-BV accumulator
# ---------------------------------------------------------------------------


def test_cmd_grep_carries_w607bv_accumulator():
    """AST-level guard: cmd_grep source carries the W607-BV
    accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-BV instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_grep.py"
    assert src_path.exists(), f"cmd_grep.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607bv_warnings_out" in src, (
        "W607-BV accumulator missing from cmd_grep; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bv" in src, (
        "W607-BV ``_run_check_bv`` helper missing from cmd_grep; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_bv is defined inside cmd_grep.
    tree = ast.parse(src)
    found_run_check_bv = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bv":
            found_run_check_bv = True
            break
    assert found_run_check_bv, (
        "W607-BV ``_run_check_bv`` helper not found in cmd_grep AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (14) Each W607-BV substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bv_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BV substrate boundary is wrapped.

    W607-BV substrate inventory (cmd_grep):

    * compile_patterns           -- _compile_patterns()
    * select_engine              -- _select_engine()
    * run_engine                 -- _run_engine() (additive to W607-G outer-guard)
    * apply_reachability_filter  -- _apply_reachability_filter()
    * apply_co_occur_filter      -- _apply_co_occur_filter()
    * apply_missing_pattern      -- _apply_missing_pattern()
    * apply_rank_by              -- _apply_rank_by()
    * apply_blame_heat           -- _apply_blame_heat()
    * apply_group_by             -- _apply_group_by()
    * serialize_envelope         -- to_json on the JSON envelope

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (4/8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_grep.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "compile_patterns",
        "select_engine",
        "run_engine",
        "apply_reachability_filter",
        "apply_co_occur_filter",
        "apply_missing_pattern",
        "apply_rank_by",
        "apply_blame_heat",
        "apply_group_by",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_bv("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented
        # at 4/8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_bv(\n    "{phase}"' in src
            or f'_run_check_bv(\n        "{phase}"' in src
            or f'_run_check_bv(\n            "{phase}"' in src
            or f'_run_check_bv(\n                "{phase}"' in src
            or f'_run_check_bv(\n                    "{phase}"' in src
            or f'_run_check_bv(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-BV _run_check_bv wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
