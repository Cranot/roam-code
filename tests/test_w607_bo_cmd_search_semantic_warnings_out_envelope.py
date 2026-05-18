"""W607-BO -- ``cmd_search_semantic`` per-phase substrate-CALL marker plumbing.

ADDITIVE to W607-A. cmd_search_semantic already carried a single
outer-guard ``semantic_search_stored_failed:`` marker (W607-A); this
wave adds per-phase substrate-CALL plumbing for the seven substrate
boundaries inside the semantic-search consumer:

* load_config                -- _load_search_semantic_config()
* compute_semantic_coverage  -- semantic_coverage(conn) diagnostic
* compute_embedding          -- _compute_embedding_search (search_stored)
* cosine_rank                -- _cosine_rank_tfidf (TF-IDF fallback)
* apply_threshold            -- _apply_threshold (score floor filter)
* extract_spans              -- _extract_spans (JSON span build + pack count)
* serialize_envelope         -- to_json on the JSON envelope

cmd_search_semantic is the embedding-based sibling of cmd_retrieve
(W607-BI). Agents call it for natural-language symbol lookup; a silent
failure in any substrate boundary degrades agent productivity
identically to a cmd_retrieve degradation. W607-BO surfaces a
structured ``search_semantic_<phase>_failed:<exc_class>:<detail>``
marker instead of a Click traceback.

EMBEDDING DEGRADATION: a raise inside ``_compute_embedding_search``
(which runs the full hybrid BM25 + ONNX vector + packs pipeline)
surfaces BOTH a ``search_semantic_compute_embedding_failed:`` marker
AND the W607-A outer-guard ``semantic_search_stored_failed:`` marker
(preserved for backwards compat). The wrapper then degrades to the
``_cosine_rank_tfidf`` lexical fallback so agents still receive
results rather than a wholesale empty envelope.

RETRIEVE/SEMANTIC pairing bonus: cmd_retrieve and cmd_search_semantic
share the semantic-coverage compute path. When the shared substrate
raises in BOTH commands, both marker families (``retrieve_*`` and
``search_semantic_*``) surface together, each in its own command's
envelope.

W978 first-hypothesis check
---------------------------

Each W607-BO-wrapped substrate has a documented empty-floor default
that matches its happy-path return shape so a raise degrades cleanly:

* load_config                 -> {}    (empty cfg)
* compute_semantic_coverage   -> minimal dict with embeddings=0 / coverage_pct=0
* compute_embedding           -> None  (triggers cosine_rank fallback)
* cosine_rank                 -> []    (empty TF-IDF results)
* apply_threshold             -> results (unchanged on filter raise)
* extract_spans               -> ([], 0)
* serialize_envelope          -> None  (triggers manual fallback rebuild)

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The substrate helpers
are top-level module-level shims so tests patch them via
``monkeypatch.setattr(cmd_search_semantic, "_<helper>", ...)``.

Marker prefix discipline
------------------------

Marker family is ``search_semantic_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers (``retrieve_*`` W607-BI,
``context_*`` W607-BF, ``semantic_*`` W607-A producer floor).

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
def semantic_project(project_factory):
    """Indexed corpus with multiple symbols + FTS5 rows — the W607-BO
    substrate-failure baseline."""
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


def _invoke_semantic(cli_runner, cwd, *extra, monkeypatch, json_mode: bool = True):
    """Invoke ``roam search-semantic`` through the group so ``--json`` is honoured."""
    monkeypatch.chdir(cwd)
    args = []
    if json_mode:
        args.append("--json")
    args.append("search-semantic")
    args.extend(extra)
    return cli_runner.invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BO substrate-CALL markers
# ---------------------------------------------------------------------------


def test_search_semantic_clean_envelope_omits_w607bo_markers(cli_runner, semantic_project, monkeypatch):
    """Clean query -> no W607-BO substrate markers.

    Byte-identical-on-happy-path: an empty W607-BO bucket on the success
    path must NOT introduce ``search_semantic_*_failed:`` markers on the
    envelope. The envelope's ``warnings_out`` is omitted entirely on a
    clean run (hash-stable with pre-W607-BO).
    """
    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "search-semantic"
    # Empty-bucket discipline: NO warnings_out keys on the clean path.
    assert "warnings_out" not in data, (
        f"clean search-semantic must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean search-semantic must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) load_config failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_load_config_failure_marker_format(cli_runner, semantic_project, monkeypatch):
    """If ``_load_search_semantic_config`` raises, surface the W607-BO marker."""
    from roam.commands import cmd_search_semantic

    def _boom_config():
        raise RuntimeError("synthetic-load-config-from-W607-BO")

    monkeypatch.setattr(cmd_search_semantic, "_load_search_semantic_config", _boom_config)

    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("search_semantic_load_config_failed:")]
    assert markers, f"expected search_semantic_load_config_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-load-config-from-W607-BO" in m for m in markers), markers
    assert data["summary"].get("partial_success") is True, (
        f"load_config-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_w607bo_warnings_in_envelope_both_channels(cli_runner, semantic_project, monkeypatch):
    """Non-empty W607-BO bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_search_semantic

    def _boom_semantic(conn):
        raise RuntimeError("synthetic-semantic-from-W607-BO")

    monkeypatch.setattr(cmd_search_semantic, "_compute_semantic_coverage", _boom_semantic)

    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BO disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BO disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("search_semantic_compute_semantic_coverage_failed:")]
    assert markers, f"expected search_semantic_compute_semantic_coverage_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, semantic_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..BI contracts.
    """
    from roam.commands import cmd_search_semantic

    def _boom_semantic(conn):
        raise ValueError("synthetic-shape-detail-from-W607-BO")

    monkeypatch.setattr(cmd_search_semantic, "_compute_semantic_coverage", _boom_semantic)

    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("search_semantic_compute_semantic_coverage_failed:")]
    assert failure_markers, f"expected search_semantic_compute_semantic_coverage_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "search_semantic_compute_semantic_coverage_failed", parts
    assert parts[1] == "ValueError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) EMBEDDING DEGRADATION test -- a raise in compute_embedding must NOT
#     crash the envelope wholesale. The cosine_rank fallback retries via
#     the TF-IDF lexical path.
# ---------------------------------------------------------------------------


def test_embedding_degradation_falls_back_to_cosine_rank(cli_runner, semantic_project, monkeypatch):
    """A raise in ``_compute_embedding_search`` must:

    1. Surface a ``search_semantic_compute_embedding_failed:`` marker.
    2. Preserve the W607-A outer-guard ``semantic_search_stored_failed:``
       marker as well (backwards compat).
    3. Trigger the W607-BO ``_cosine_rank_tfidf`` fallback so the
       envelope still emits results (lexical-only).
    4. The envelope MUST still emit a structured result with
       ``partial_success: true``.

    This is the "don't crash semantic search wholesale on ONNX runtime
    unavailable" contract: agents must still receive lexical results
    when the embedding substrate degrades.
    """
    from roam.commands import cmd_search_semantic

    def _boom_embedding(conn, query, *, top_k, semantic_backend, warnings_out):
        raise RuntimeError("synthetic-embedding-from-W607-BO")

    monkeypatch.setattr(cmd_search_semantic, "_compute_embedding_search", _boom_embedding)

    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    embedding_markers = [m for m in all_wo if m.startswith("search_semantic_compute_embedding_failed:")]
    assert embedding_markers, (
        f"expected search_semantic_compute_embedding_failed: marker after embedding degradation; got {all_wo!r}"
    )
    # W607-A outer-guard marker MUST still be emitted (backwards compat).
    outer_markers = [m for m in all_wo if m.startswith("semantic_search_stored_failed:")]
    assert outer_markers, f"W607-A outer-guard semantic_search_stored_failed: marker missing; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, (
        f"embedding-degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # Envelope still emits a structured result (the cosine_rank fallback
    # ran). The result shape is preserved -- total_matches may be 0 or
    # more depending on token overlap, but the keys exist.
    assert "total_matches" in data["summary"], (
        f"embedding-degraded envelope must still surface total_matches key; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (6) cosine_rank fallback failure -- second-stage degradation stays loud
# ---------------------------------------------------------------------------


def test_cosine_rank_failure_emits_marker(cli_runner, semantic_project, monkeypatch):
    """When the embedding pipeline returns no results AND the TF-IDF
    fallback raises, surface a ``search_semantic_cosine_rank_failed:``
    marker AND keep the envelope intact (empty results, partial_success).
    """
    from roam.commands import cmd_search_semantic

    def _empty_embedding(conn, query, *, top_k, semantic_backend, warnings_out):
        return []  # no results — triggers cosine_rank fallback

    def _boom_cosine(conn, query, *, top_k):
        raise RuntimeError("synthetic-cosine-rank-from-W607-BO")

    monkeypatch.setattr(cmd_search_semantic, "_compute_embedding_search", _empty_embedding)
    monkeypatch.setattr(cmd_search_semantic, "_cosine_rank_tfidf", _boom_cosine)

    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("search_semantic_cosine_rank_failed:")]
    assert markers, f"expected search_semantic_cosine_rank_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]
    # Envelope still emits a structured result (empty list).
    assert data["summary"].get("total_matches") == 0, data["summary"]


# ---------------------------------------------------------------------------
# (7) apply_threshold failure surfaces marker
# ---------------------------------------------------------------------------


def test_apply_threshold_failure_emits_marker(cli_runner, semantic_project, monkeypatch):
    """A raise inside ``_apply_threshold`` must surface a marker; the
    degraded default returns the unfiltered list so the envelope still
    emits."""
    from roam.commands import cmd_search_semantic

    def _boom_threshold(results, threshold):
        raise RuntimeError("synthetic-threshold-from-W607-BO")

    monkeypatch.setattr(cmd_search_semantic, "_apply_threshold", _boom_threshold)

    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("search_semantic_apply_threshold_failed:")]
    assert markers, f"expected search_semantic_apply_threshold_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (8) extract_spans failure surfaces marker
# ---------------------------------------------------------------------------


def test_extract_spans_failure_emits_marker(cli_runner, semantic_project, monkeypatch):
    """A raise inside ``_extract_spans`` must surface a marker; the
    degraded default returns ``([], 0)`` so the envelope still emits."""
    from roam.commands import cmd_search_semantic

    def _boom_extract(results):
        raise RuntimeError("synthetic-extract-spans-from-W607-BO")

    monkeypatch.setattr(cmd_search_semantic, "_extract_spans", _boom_extract)

    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("search_semantic_extract_spans_failed:")]
    assert markers, f"expected search_semantic_extract_spans_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, data["summary"]
    # Envelope still emits a structured result (empty span list).
    assert data.get("results") == [], data


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-BO stays in ``search_semantic_*`` family
# ---------------------------------------------------------------------------


def test_w607bo_marker_prefix_stays_in_search_semantic_family(cli_runner, semantic_project, monkeypatch):
    """Every W607-BO substrate marker uses the canonical
    ``search_semantic_*`` prefix.

    cmd_search_semantic is distinct from sibling W607-* layers. Marker
    prefix MUST stay ``search_semantic_*`` and MUST NOT leak into other
    family prefixes (e.g. ``retrieve_*`` W607-BI, ``context_*`` W607-BF).
    """
    from roam.commands import cmd_search_semantic

    def _boom_semantic(conn):
        raise RuntimeError("synthetic-prefix-discipline-from-W607-BO")

    monkeypatch.setattr(cmd_search_semantic, "_compute_semantic_coverage", _boom_semantic)

    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # W607-BO markers must all use the search_semantic_ family. The
    # W607-A producer-floor markers (semantic_*) are a separate family
    # and are allowed to coexist; this test only enforces the W607-BO
    # consumer-layer family scope.
    bo_markers = [m for m in all_wo if "_failed:" in m and not m.startswith("semantic_")]
    assert bo_markers, "expected non-empty W607-BO substrate markers for prefix-consistency check"
    for marker in bo_markers:
        assert marker.startswith("search_semantic_"), (
            f"every surfaced W607-BO marker must use the ``search_semantic_*`` "
            f"prefix family (cmd_search_semantic scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
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
# (10) Top-level vs summary.warnings_out parity on disclosure path
# ---------------------------------------------------------------------------


def test_top_level_and_summary_warnings_out_parity(cli_runner, semantic_project, monkeypatch):
    """top-level warnings_out and summary.warnings_out must agree.

    The bucket is sourced once (combined W607-A + W607-BO) and threaded
    into both channels so consumers reading either end see the same
    lineage.
    """
    from roam.commands import cmd_search_semantic

    def _boom_semantic(conn):
        raise RuntimeError("synthetic-parity-from-W607-BO")

    monkeypatch.setattr(cmd_search_semantic, "_compute_semantic_coverage", _boom_semantic)

    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    assert sorted(top_wo) == sorted(summary_wo), (
        f"top-level vs summary.warnings_out must be equal; top={top_wo!r} summary={summary_wo!r}"
    )
    semantic_markers = [m for m in top_wo if m.startswith("search_semantic_compute_semantic_coverage_failed:")]
    assert semantic_markers, f"expected search_semantic_compute_semantic_coverage_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (11) RETRIEVE/SEMANTIC pairing bonus -- both marker families coexist
#      when shared substrate (semantic_coverage) raises in both.
# ---------------------------------------------------------------------------


def test_retrieve_semantic_marker_families_coexist(cli_runner, semantic_project, monkeypatch):
    """W607-BI (retrieve) + W607-BO (search_semantic) markers coexist on
    the same corpus. Each command keeps its own marker family discipline
    -- ``retrieve_*`` for cmd_retrieve, ``search_semantic_*`` for
    cmd_search_semantic -- they MUST NOT collide.

    Both commands share the semantic-coverage compute path; this test
    confirms that markers from BOTH families surface together when both
    commands are invoked in sequence.
    """
    from roam.commands import cmd_retrieve, cmd_search_semantic

    def _boom_retrieve_semantic(conn):
        raise RuntimeError("synthetic-retrieve-pairing-from-W607-BI")

    def _boom_search_semantic(conn):
        raise RuntimeError("synthetic-search-semantic-pairing-from-W607-BO")

    monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", _boom_retrieve_semantic)
    monkeypatch.setattr(cmd_search_semantic, "_compute_semantic_coverage", _boom_search_semantic)

    monkeypatch.chdir(semantic_project)

    r_retrieve = cli_runner.invoke(cli, ["--json", "retrieve", "database"], catch_exceptions=False)
    assert r_retrieve.exit_code == 0, r_retrieve.output
    d_retrieve = _json.loads(r_retrieve.output)

    r_semantic = cli_runner.invoke(cli, ["--json", "search-semantic", "database"], catch_exceptions=False)
    assert r_semantic.exit_code == 0, r_semantic.output
    d_semantic = _json.loads(r_semantic.output)

    # cmd_retrieve markers (W607-BI family)
    retrieve_wo = list(d_retrieve.get("warnings_out") or []) + list(d_retrieve["summary"].get("warnings_out") or [])
    retrieve_markers = [m for m in retrieve_wo if m.startswith("retrieve_")]
    assert retrieve_markers, f"expected retrieve_* markers from cmd_retrieve W607-BI; got {retrieve_wo!r}"
    # cmd_retrieve must NOT carry search_semantic_* markers
    for m in retrieve_wo:
        assert not m.startswith("search_semantic_"), (
            f"cmd_retrieve envelope must NOT carry search_semantic_* markers; got {m!r}"
        )

    # cmd_search_semantic markers (W607-BO family)
    semantic_wo = list(d_semantic.get("warnings_out") or []) + list(d_semantic["summary"].get("warnings_out") or [])
    semantic_markers = [m for m in semantic_wo if m.startswith("search_semantic_")]
    assert semantic_markers, f"expected search_semantic_* markers from cmd_search_semantic W607-BO; got {semantic_wo!r}"
    for m in semantic_wo:
        assert not m.startswith("retrieve_"), (
            f"cmd_search_semantic envelope must NOT carry retrieve_* markers; got {m!r}"
        )


# ---------------------------------------------------------------------------
# (12) Source-level guard: cmd_search_semantic carries the W607-BO accumulator
# ---------------------------------------------------------------------------


def test_cmd_search_semantic_carries_w607bo_accumulator():
    """AST-level guard: cmd_search_semantic source carries the W607-BO
    accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-BO instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_search_semantic.py"
    assert src_path.exists(), f"cmd_search_semantic.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607bo_warnings_out" in src, (
        "W607-BO accumulator missing from cmd_search_semantic; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bo" in src, (
        "W607-BO ``_run_check_bo`` helper missing from cmd_search_semantic; "
        "the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_bo is defined inside cmd_search_semantic.
    tree = ast.parse(src)
    found_run_check_bo = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bo":
            found_run_check_bo = True
            break
    assert found_run_check_bo, (
        "W607-BO ``_run_check_bo`` helper not found in cmd_search_semantic AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (13) Each W607-BO substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bo_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BO substrate boundary is wrapped.

    W607-BO substrate inventory (cmd_search_semantic):

    * load_config                -- _load_search_semantic_config
    * compute_semantic_coverage  -- _compute_semantic_coverage
    * compute_embedding          -- _compute_embedding_search (search_stored)
    * cosine_rank                -- _cosine_rank_tfidf (TF-IDF fallback)
    * apply_threshold            -- _apply_threshold
    * extract_spans              -- _extract_spans
    * serialize_envelope         -- to_json on the JSON envelope

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_search_semantic.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "load_config",
        "compute_semantic_coverage",
        "compute_embedding",
        "cosine_rank",
        "apply_threshold",
        "extract_spans",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_bo("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_bo(\n        "{phase}"' in src
            or f'_run_check_bo(\n            "{phase}"' in src
            or f'_run_check_bo(\n                "{phase}"' in src
            or f'_run_check_bo(\n                    "{phase}"' in src
            or f'_run_check_bo(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-BO _run_check_bo wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (14) W607-A backwards-compat: outer-guard marker still fires
# ---------------------------------------------------------------------------


def test_w607a_outer_guard_marker_preserved(cli_runner, semantic_project, monkeypatch):
    """When ``_compute_embedding_search`` raises, the W607-A outer-guard
    ``semantic_search_stored_failed:`` marker MUST still be emitted.

    Pins the backwards-compat contract: W607-BO is ADDITIVE, never a
    replacement. The W607-A bucket (``warnings_out``) and the W607-BO
    bucket (``_w607bo_warnings_out``) accumulate independently and
    merge at envelope-emit time. A consumer parsing the prior
    ``semantic_search_stored_failed:`` marker must continue to find it.
    """
    from roam.commands import cmd_search_semantic

    def _boom_embedding(conn, query, *, top_k, semantic_backend, warnings_out):
        raise RuntimeError("synthetic-w607a-preserve-from-W607-BO")

    monkeypatch.setattr(cmd_search_semantic, "_compute_embedding_search", _boom_embedding)

    result = _invoke_semantic(cli_runner, semantic_project, "database connection", monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # W607-A outer-guard marker must still be present.
    outer_markers = [m for m in all_wo if m.startswith("semantic_search_stored_failed:")]
    assert outer_markers, (
        f"W607-A outer-guard semantic_search_stored_failed: marker missing; "
        f"W607-BO is ADDITIVE, not a replacement. Got {all_wo!r}"
    )
    # AND the W607-BO per-substrate marker must also be present.
    bo_markers = [m for m in all_wo if m.startswith("search_semantic_compute_embedding_failed:")]
    assert bo_markers, f"W607-BO compute_embedding marker missing; got {all_wo!r}"
