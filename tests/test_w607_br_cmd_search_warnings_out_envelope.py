"""W607-BR -- ``cmd_search`` per-phase substrate-CALL marker plumbing.

ADDITIVE to W607-E. cmd_search already carried:

* An outer-guard ``search_pipeline_failed:`` marker around the main SQL
  query (W607-E outer).
* Three inner ``search_explain_<phase>_failed:`` markers around the
  FTS5 explain helper (W607-E inner: ``bm25`` / ``highlight`` /
  ``term_counts``).

This wave adds per-phase substrate-CALL plumbing for the seven substrate
boundaries inside the exact-match search consumer:

* load_config              -- _load_search_config()
* parse_query              -- _parse_query (mode + LIKE-pattern normalize)
* validate_kind_filter     -- _validate_kind_filter (W1068 Pattern-1D)
* fts_search               -- _fts_search (main SQL with PageRank ORDER BY)
* fallback_like_match      -- _fallback_like_match (text "50 of <N>" count)
* apply_kind_filter        -- _apply_kind_filter (abbrev -> full kind)
* extract_spans            -- _extract_spans (JSON results dict-list)
* serialize_envelope       -- to_json on the JSON envelope

cmd_search is the EXACT-MATCH sibling of cmd_search_semantic
(W607-BO) and cmd_retrieve (W607-BI). Closes the SEARCH TRIO with
distinct marker prefixes (``search_*`` vs ``search_semantic_*`` vs
``retrieve_*``) so a 3-way envelope inspection can demultiplex every
consumer's substrate axis.

W1068 PATTERN-1D regression guard: an ``--kind unknown-name`` invocation
must still produce the closest-match suggestion verdict-suffix AND a
W607-BR ``search_validate_kind_filter`` boundary marker surfaces if
the underlying validator raises.

W978 first-hypothesis check
---------------------------

Each W607-BR-wrapped substrate has a documented empty-floor default
that matches its happy-path return shape so a raise degrades cleanly:

* load_config              -> {}                  (empty cfg)
* parse_query              -> (mode_lower, like)  (raw normalization)
* validate_kind_filter     -> (True, [], None)    (treat kind as valid)
* fts_search               -> None / []           (W607-E pipeline rethrow)
* fallback_like_match      -> 0                   (loud-zero hint)
* apply_kind_filter        -> kind_filter         (unchanged)
* extract_spans            -> []                  (empty span list)
* serialize_envelope       -> None                (manual fallback rebuild)

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The substrate helpers
are top-level module-level shims so tests patch them via
``monkeypatch.setattr(cmd_search, "_<helper>", ...)``.

Marker prefix discipline
------------------------

Marker family is ``search_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers (``search_semantic_*``
W607-BO, ``retrieve_*`` W607-BI, ``context_*`` W607-BF). The W607-E
``search_pipeline_failed:`` and ``search_explain_*_failed:`` markers
share the ``search_*`` family and are explicitly ALLOWED to coexist
with W607-BR markers (additive contract).

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
def search_project(project_factory):
    """Indexed corpus with multiple symbols — the W607-BR substrate
    baseline."""
    return project_factory(
        {
            "db/connection.py": (
                "def open_database():\n"
                "    '''Open a database connection.'''\n"
                "    pass\n"
                "def close_database():\n"
                "    '''Close the database connection.'''\n"
                "    pass\n"
            ),
            "auth/login.py": (
                "def authenticate_user(username, password):\n"
                "    '''Authenticate a user with credentials.'''\n"
                "    pass\n"
                "def logout_user(session):\n"
                "    '''Log out a user session.'''\n"
                "    pass\n"
            ),
        }
    )


def _invoke_search(cli_runner, cwd, *extra, monkeypatch, json_mode: bool = True):
    """Invoke ``roam search`` through the group so ``--json`` is honoured."""
    monkeypatch.chdir(cwd)
    args = []
    if json_mode:
        args.append("--json")
    args.append("search")
    args.extend(extra)
    return cli_runner.invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BR substrate-CALL markers
# ---------------------------------------------------------------------------


def test_search_clean_envelope_omits_w607br_markers(cli_runner, search_project, monkeypatch):
    """Clean query -> no W607-BR substrate markers.

    Byte-identical-on-happy-path: an empty W607-BR bucket on the success
    path must NOT introduce ``search_*_failed:`` markers on the
    envelope. The envelope's ``warnings_out`` is omitted entirely on a
    clean run (hash-stable with pre-W607-BR).
    """
    result = _invoke_search(cli_runner, search_project, "authenticate", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "search"
    # Empty-bucket discipline: NO warnings_out keys on the clean path.
    assert "warnings_out" not in data, (
        f"clean search must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean search must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) load_config failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_load_config_failure_marker_format(cli_runner, search_project, monkeypatch):
    """If ``_load_search_config`` raises, surface the W607-BR marker."""
    from roam.commands import cmd_search

    def _boom_config():
        raise RuntimeError("synthetic-load-config-from-W607-BR")

    monkeypatch.setattr(cmd_search, "_load_search_config", _boom_config)

    result = _invoke_search(cli_runner, search_project, "authenticate", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("search_load_config_failed:")]
    assert markers, f"expected search_load_config_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-load-config-from-W607-BR" in m for m in markers), markers
    assert data["summary"].get("partial_success") is True, (
        f"load_config-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_w607br_warnings_in_envelope_both_channels(cli_runner, search_project, monkeypatch):
    """Non-empty W607-BR bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_search

    def _boom_parse(pattern, mode):
        raise RuntimeError("synthetic-parse-from-W607-BR")

    monkeypatch.setattr(cmd_search, "_parse_query", _boom_parse)

    result = _invoke_search(cli_runner, search_project, "authenticate", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BR disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BR disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("search_parse_query_failed:")]
    assert markers, f"expected search_parse_query_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, search_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..BO contracts.
    """
    from roam.commands import cmd_search

    def _boom_parse(pattern, mode):
        raise ValueError("synthetic-shape-detail-from-W607-BR")

    monkeypatch.setattr(cmd_search, "_parse_query", _boom_parse)

    result = _invoke_search(cli_runner, search_project, "authenticate", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("search_parse_query_failed:")]
    assert failure_markers, f"expected search_parse_query_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "search_parse_query_failed", parts
    assert parts[1] == "ValueError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) fts_search failure -- marker AND W607-E outer-guard mirror preserved
# ---------------------------------------------------------------------------


def test_fts_search_failure_dual_marker_emission(cli_runner, search_project, monkeypatch):
    """A raise in ``_fts_search`` must:

    1. Surface a ``search_fts_search_failed:`` marker (W607-BR family).
    2. Re-emit the W607-E outer-guard ``search_pipeline_failed:``
       marker as well (backwards compat).
    3. The envelope MUST still emit a structured result with
       ``partial_success: true``.

    This is the "don't crash search wholesale on a SQL substrate
    failure" contract: agents must still receive the structured
    no-match envelope when the main SQL path degrades.
    """
    from roam.commands import cmd_search

    def _boom_fts(conn, where_sql, params, *, recent_days):
        raise RuntimeError("synthetic-fts-from-W607-BR")

    monkeypatch.setattr(cmd_search, "_fts_search", _boom_fts)

    result = _invoke_search(cli_runner, search_project, "authenticate", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    fts_markers = [m for m in all_wo if m.startswith("search_fts_search_failed:")]
    assert fts_markers, f"expected search_fts_search_failed: marker after fts substrate failure; got {all_wo!r}"
    # W607-E outer-guard marker MUST still be emitted (backwards compat).
    outer_markers = [m for m in all_wo if m.startswith("search_pipeline_failed:")]
    assert outer_markers, (
        f"W607-E outer-guard search_pipeline_failed: marker missing; "
        f"W607-BR is ADDITIVE, not a replacement. Got {all_wo!r}"
    )
    assert data["summary"].get("partial_success") is True, (
        f"fts-degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # Envelope still emits a structured result (empty hits — degraded
    # path).
    assert data["summary"].get("total") == 0, data["summary"]


# ---------------------------------------------------------------------------
# (6) apply_kind_filter failure surfaces marker
# ---------------------------------------------------------------------------


def test_apply_kind_filter_failure_emits_marker(cli_runner, search_project, monkeypatch):
    """A raise inside ``_apply_kind_filter`` must surface a marker; the
    degraded default returns the kind unchanged so the SQL path still
    runs.

    Note: ``apply_kind_filter`` is only invoked when ``--kind`` is
    supplied AND the kind passed validation. Pass a valid kind so the
    SQL filter step is reached.
    """
    from roam.commands import cmd_search

    def _boom_apply(kind_filter):
        raise RuntimeError("synthetic-apply-kind-from-W607-BR")

    monkeypatch.setattr(cmd_search, "_apply_kind_filter", _boom_apply)

    result = _invoke_search(
        cli_runner,
        search_project,
        "authenticate",
        "--kind",
        "function",
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("search_apply_kind_filter_failed:")]
    assert markers, f"expected search_apply_kind_filter_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (7) extract_spans failure surfaces marker
# ---------------------------------------------------------------------------


def test_extract_spans_failure_emits_marker(cli_runner, search_project, monkeypatch):
    """A raise inside ``_extract_spans`` must surface a marker; the
    degraded default returns ``[]`` so the envelope still emits."""
    from roam.commands import cmd_search

    def _boom_extract(rows, *, ref_counts, explanations, explain):
        raise RuntimeError("synthetic-extract-spans-from-W607-BR")

    monkeypatch.setattr(cmd_search, "_extract_spans", _boom_extract)

    result = _invoke_search(cli_runner, search_project, "authenticate", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("search_extract_spans_failed:")]
    assert markers, f"expected search_extract_spans_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]
    # Envelope still emits a structured result (empty span list).
    assert data.get("results") == [], data


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-BR stays in ``search_*`` family
#     and does NOT leak into sibling W607 family prefixes.
# ---------------------------------------------------------------------------


def test_w607br_marker_prefix_stays_in_search_family(cli_runner, search_project, monkeypatch):
    """Every W607-BR substrate marker uses the canonical
    ``search_<phase>_failed:`` prefix.

    cmd_search is distinct from sibling W607-* layers. Marker prefix
    MUST stay ``search_*`` and MUST NOT leak into other family prefixes
    (e.g. ``search_semantic_*`` W607-BO, ``retrieve_*`` W607-BI,
    ``context_*`` W607-BF).
    """
    from roam.commands import cmd_search

    def _boom_parse(pattern, mode):
        raise RuntimeError("synthetic-prefix-discipline-from-W607-BR")

    monkeypatch.setattr(cmd_search, "_parse_query", _boom_parse)

    result = _invoke_search(cli_runner, search_project, "authenticate", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # Surfaced W607-BR markers must all use the search_ family.
    br_markers = [m for m in all_wo if "_failed:" in m]
    assert br_markers, "expected non-empty W607-BR substrate markers for prefix-consistency check"
    for marker in br_markers:
        assert marker.startswith("search_"), (
            f"every surfaced W607-BR marker must use the ``search_*`` prefix family (cmd_search scope); got {marker!r}"
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


# ---------------------------------------------------------------------------
# (9) Top-level vs summary.warnings_out parity on disclosure path
# ---------------------------------------------------------------------------


def test_top_level_and_summary_warnings_out_parity(cli_runner, search_project, monkeypatch):
    """top-level warnings_out and summary.warnings_out must agree.

    The bucket is sourced once (combined W607-E + W607-BR) and threaded
    into both channels so consumers reading either end see the same
    lineage.
    """
    from roam.commands import cmd_search

    def _boom_parse(pattern, mode):
        raise RuntimeError("synthetic-parity-from-W607-BR")

    monkeypatch.setattr(cmd_search, "_parse_query", _boom_parse)

    result = _invoke_search(cli_runner, search_project, "authenticate", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    assert sorted(top_wo) == sorted(summary_wo), (
        f"top-level vs summary.warnings_out must be equal; top={top_wo!r} summary={summary_wo!r}"
    )
    parse_markers = [m for m in top_wo if m.startswith("search_parse_query_failed:")]
    assert parse_markers, f"expected search_parse_query_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (10) W607-E backwards-compat: explain inner-fallback markers still fire
# ---------------------------------------------------------------------------


def test_w607e_explain_inner_fallback_preserved(cli_runner, search_project, monkeypatch):
    """When the FTS5 explain helper's inner ``except: pass`` paths
    raise, the W607-E ``search_explain_<phase>_failed:`` markers MUST
    still be emitted.

    Pins the backwards-compat contract: W607-BR is ADDITIVE, never a
    replacement. The W607-E inner-fallback bucket (``warnings_out``)
    and the W607-BR bucket (``_w607br_warnings_out``) accumulate
    independently and merge at envelope-emit time. A consumer parsing
    the prior ``search_explain_*_failed:`` marker must continue to
    find it.
    """
    from roam.commands import cmd_search

    # _get_explain_data_batch is invoked (once, batched) when --explain is
    # set. Patch it to record warnings_out usage and then raise inside one
    # inner phase to force the W607-E marker.
    def _bm25_boom_explain(conn, symbol_ids, pattern, *, warnings_out=None):
        # Inject a W607-E explain marker directly via the bucket so the
        # disclosure path is exercised even though we did not actually
        # patch the FTS5 internals.
        if warnings_out is not None:
            warnings_out.append("search_explain_bm25_failed:RuntimeError:synthetic-explain-from-W607-BR")
        return {
            sid: {
                "bm25_score": None,
                "matched_fields": [],
                "highlights": {},
                "term_counts": {},
            }
            for sid in symbol_ids
        }

    monkeypatch.setattr(cmd_search, "_get_explain_data_batch", _bm25_boom_explain)

    result = _invoke_search(
        cli_runner,
        search_project,
        "authenticate",
        "--explain",
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # W607-E inner-fallback marker must still be present.
    inner_markers = [m for m in all_wo if m.startswith("search_explain_bm25_failed:")]
    assert inner_markers, (
        f"W607-E search_explain_bm25_failed: marker missing; W607-BR is ADDITIVE, not a replacement. Got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (11) SEARCH TRIO 3-WAY pairing -- search, search_semantic, retrieve
#      marker families coexist when each command is invoked.
# ---------------------------------------------------------------------------


def test_search_trio_marker_families_coexist(cli_runner, search_project, monkeypatch):
    """W607-BR (search) + W607-BO (search_semantic) + W607-BI (retrieve)
    markers coexist across the three sibling consumers.

    Each command keeps its own marker family discipline:

    * ``search_*``           for cmd_search          (W607-BR)
    * ``search_semantic_*``  for cmd_search_semantic (W607-BO)
    * ``retrieve_*``         for cmd_retrieve        (W607-BI)

    These three families MUST NOT collide. This test confirms that
    markers from ALL THREE families surface when each command is
    invoked in sequence on the same corpus.
    """
    from roam.commands import cmd_retrieve, cmd_search, cmd_search_semantic

    def _boom_search_parse(pattern, mode):
        raise RuntimeError("synthetic-search-trio-from-W607-BR")

    def _boom_semantic_coverage(conn):
        raise RuntimeError("synthetic-semantic-trio-from-W607-BO")

    def _boom_retrieve_coverage(conn):
        raise RuntimeError("synthetic-retrieve-trio-from-W607-BI")

    monkeypatch.setattr(cmd_search, "_parse_query", _boom_search_parse)
    monkeypatch.setattr(cmd_search_semantic, "_compute_semantic_coverage", _boom_semantic_coverage)
    monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", _boom_retrieve_coverage)

    monkeypatch.chdir(search_project)

    r_search = cli_runner.invoke(cli, ["--json", "search", "database"], catch_exceptions=False)
    assert r_search.exit_code == 0, r_search.output
    d_search = _json.loads(r_search.output)

    r_semantic = cli_runner.invoke(cli, ["--json", "search-semantic", "database"], catch_exceptions=False)
    assert r_semantic.exit_code == 0, r_semantic.output
    d_semantic = _json.loads(r_semantic.output)

    r_retrieve = cli_runner.invoke(cli, ["--json", "retrieve", "database"], catch_exceptions=False)
    assert r_retrieve.exit_code == 0, r_retrieve.output
    d_retrieve = _json.loads(r_retrieve.output)

    # cmd_search markers (W607-BR family)
    search_wo = list(d_search.get("warnings_out") or []) + list(d_search["summary"].get("warnings_out") or [])
    search_markers = [m for m in search_wo if m.startswith("search_parse_query_failed:")]
    assert search_markers, f"expected search_parse_query_failed: marker from cmd_search W607-BR; got {search_wo!r}"
    # cmd_search must NOT carry search_semantic_* or retrieve_* markers.
    for m in search_wo:
        assert not m.startswith("search_semantic_"), (
            f"cmd_search envelope must NOT carry search_semantic_* markers; got {m!r}"
        )
        assert not m.startswith("retrieve_"), f"cmd_search envelope must NOT carry retrieve_* markers; got {m!r}"

    # cmd_search_semantic markers (W607-BO family)
    semantic_wo = list(d_semantic.get("warnings_out") or []) + list(d_semantic["summary"].get("warnings_out") or [])
    semantic_markers = [m for m in semantic_wo if m.startswith("search_semantic_")]
    assert semantic_markers, f"expected search_semantic_* markers from cmd_search_semantic W607-BO; got {semantic_wo!r}"
    for m in semantic_wo:
        assert not m.startswith("retrieve_"), (
            f"cmd_search_semantic envelope must NOT carry retrieve_* markers; got {m!r}"
        )

    # cmd_retrieve markers (W607-BI family)
    retrieve_wo = list(d_retrieve.get("warnings_out") or []) + list(d_retrieve["summary"].get("warnings_out") or [])
    retrieve_markers = [m for m in retrieve_wo if m.startswith("retrieve_")]
    assert retrieve_markers, f"expected retrieve_* markers from cmd_retrieve W607-BI; got {retrieve_wo!r}"
    for m in retrieve_wo:
        assert not m.startswith("search_semantic_"), (
            f"cmd_retrieve envelope must NOT carry search_semantic_* markers; got {m!r}"
        )
        # cmd_retrieve's own outer-guard uses ``retrieve_*`` exclusively.
        # ``search_*`` markers (W607-BR cmd_search scope) must NOT leak in.
        # Note: ``search_*`` prefix is a strict superset that contains
        # ``search_semantic_*``; check the disjoint prefix explicitly.
        assert not (m.startswith("search_") and not m.startswith("search_semantic_")), (
            f"cmd_retrieve envelope must NOT carry cmd_search W607-BR markers (search_<phase>_*); got {m!r}"
        )


# ---------------------------------------------------------------------------
# (12) W1068 PATTERN-1D regression guard
#      Unknown --kind must produce closest-match verdict-suffix +
#      W607-BR boundary surfaces marker on validator raise.
# ---------------------------------------------------------------------------


def test_w1068_pattern_1d_unknown_kind_closest_match_intact(cli_runner, search_project, monkeypatch):
    """Unknown ``--kind`` still produces the W1068 Pattern-1D closest-
    match suggestion verdict-suffix.

    Pins the W1068 regression contract: introducing the W607-BR
    ``_validate_kind_filter`` substrate boundary must NOT silently
    swallow the unknown-kind disclosure. A passed-but-unknown kind
    must:

    1. Produce a ``"unknown kind '<name>'"`` verdict prefix.
    2. Land a closest-match did-you-mean suffix (``frag.verdict_suffix``).
    3. Return total=0 + results=[] (degraded-path success shape).
    """
    result = _invoke_search(
        cli_runner,
        search_project,
        "authenticate",
        "--kind",
        "fn-typo-xyz",
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    verdict = data["summary"].get("verdict", "")
    assert "unknown kind" in verdict, f"W1068 Pattern-1D regression: unknown-kind verdict missing; got {verdict!r}"
    assert "fn-typo-xyz" in verdict, (
        f"W1068 Pattern-1D regression: requested kind missing from verdict; got {verdict!r}"
    )
    assert data["summary"].get("total") == 0, data["summary"]
    assert data.get("results") == [], data


def test_w607br_validate_kind_filter_surfaces_marker_on_raise(cli_runner, search_project, monkeypatch):
    """W607-BR validate_kind_filter boundary surfaces a marker if the
    underlying validator raises.

    Pins the W1068 PATTERN-1D regression guard's W607-BR axis: a raise
    inside ``_validate_kind_filter`` MUST emit a
    ``search_validate_kind_filter_failed:`` marker AND the degraded
    default (treat the kind as valid) keeps the SQL path running so
    the envelope still emits.
    """
    from roam.commands import cmd_search

    def _boom_validate(kind_filter):
        raise RuntimeError("synthetic-validate-kind-from-W607-BR")

    monkeypatch.setattr(cmd_search, "_validate_kind_filter", _boom_validate)

    result = _invoke_search(
        cli_runner,
        search_project,
        "authenticate",
        "--kind",
        "function",
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("search_validate_kind_filter_failed:")]
    assert markers, f"expected search_validate_kind_filter_failed: marker on validator raise; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (13) Source-level guard: cmd_search carries the W607-BR accumulator
# ---------------------------------------------------------------------------


def test_cmd_search_carries_w607br_accumulator():
    """AST-level guard: cmd_search source carries the W607-BR
    accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-BR instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_search.py"
    assert src_path.exists(), f"cmd_search.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607br_warnings_out" in src, (
        "W607-BR accumulator missing from cmd_search; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_br" in src, (
        "W607-BR ``_run_check_br`` helper missing from cmd_search; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_br is defined inside cmd_search.
    tree = ast.parse(src)
    found_run_check_br = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_br":
            found_run_check_br = True
            break
    assert found_run_check_br, (
        "W607-BR ``_run_check_br`` helper not found in cmd_search AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (14) Each W607-BR substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607br_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BR substrate boundary is wrapped.

    W607-BR substrate inventory (cmd_search):

    * load_config              -- _load_search_config
    * parse_query              -- _parse_query
    * validate_kind_filter     -- _validate_kind_filter
    * fts_search               -- _fts_search
    * fallback_like_match      -- _fallback_like_match
    * apply_kind_filter        -- _apply_kind_filter
    * extract_spans            -- _extract_spans
    * serialize_envelope       -- to_json on the JSON envelope

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (4/8/12/16/20 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_search.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "load_config",
        "parse_query",
        "validate_kind_filter",
        "fts_search",
        "fallback_like_match",
        "apply_kind_filter",
        "extract_spans",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_br("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented
        # at 4/8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_br(\n    "{phase}"' in src
            or f'_run_check_br(\n        "{phase}"' in src
            or f'_run_check_br(\n            "{phase}"' in src
            or f'_run_check_br(\n                "{phase}"' in src
            or f'_run_check_br(\n                    "{phase}"' in src
            or f'_run_check_br(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-BR _run_check_br wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
