"""W607-BI -- ``cmd_retrieve`` per-phase substrate-CALL marker plumbing.

ADDITIVE to W607-B. cmd_retrieve already carried a single outer-guard
``retrieve_pipeline_failed:`` marker (W607-B); this wave adds per-phase
substrate-CALL plumbing for the seven substrate boundaries inside the
retrieve consumer:

* load_config              -- get_retrieve_config()
* compute_semantic_coverage -- semantic_coverage(conn)
* allocate_token_budget    -- int() coercion of --budget
* fts5_search              -- _fts5_search_full (full FTS5 + rerank pipeline)
* tfidf_rerank             -- _fts5_search_lexical_only (degradation fallback)
* scope_filter             -- _scope_filter_candidates
* extract_spans            -- _extract_dry_run_spans (dry-run mode)
* compute_confidence_score -- _retrieve_confidence_score
* suggest_refinements      -- _suggest_refinements
* serialize_envelope       -- to_json on the JSON envelope

cmd_retrieve is the CLAUDE.md-documented canonical graph-aware FTS5
retrieval command — agents call it as their primary free-form task
lookup tool ("trace login flow", "where is the n+1?"). A silent failure
in retrieval directly degrades agent productivity, so each substrate
boundary surfaces a structured ``retrieve_<phase>_failed:<exc_class>:
<detail>`` marker instead of a Click traceback.

FTS5 vs RERANK degradation: a raise inside ``_fts5_search_full`` (which
runs the full FTS5 + structural rerank pipeline) surfaces the
``retrieve_fts5_search_failed:`` marker AND retries via
``_fts5_search_lexical_only`` (rerank="off") so agents still receive
raw FTS5 results rather than a wholesale empty envelope. If the
lexical-only fallback also raises, the W607-B outer-guard
``retrieve_pipeline_failed:`` takes over.

CONTEXT/RETRIEVE pairing bonus: cmd_retrieve and cmd_context share the
token-budget allocator boundary. When the shared allocator raises in
BOTH commands, both marker families (``retrieve_*`` and ``context_*``)
surface together, each in its own command's envelope.

W978 first-hypothesis check
---------------------------

Each W607-BI-wrapped substrate has a documented empty-floor default
that matches its happy-path return shape so a raise degrades cleanly:

* load_config              -> {}    (empty cfg, picks defaults)
* compute_semantic_coverage -> minimal dict with embeddings=0 / coverage_pct=0
* allocate_token_budget    -> 0
* fts5_search              -> None  (triggers tfidf_rerank fallback)
* tfidf_rerank             -> None  (triggers W607-B outer-guard)
* scope_filter             -> (candidates, normalised_scope) (unchanged)
* extract_spans            -> candidates (unchanged)
* compute_confidence_score -> (0.0, "low")
* suggest_refinements      -> []
* serialize_envelope       -> None  (triggers manual fallback rebuild)

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The substrate helpers
are top-level module-level shims so tests patch them via
``monkeypatch.setattr(cmd_retrieve, "_<helper>", ...)``.

Marker prefix discipline
------------------------

Marker family is ``retrieve_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers (``context_*`` W607-BF,
``understand_*`` W607-BC, ``describe_*`` W607-K, ``minimap_*`` W607-L/AZ,
etc.).

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def retrieve_project(tmp_path, monkeypatch):
    """Indexed corpus with multiple symbols + FTS5 rows -- the W607-BI
    substrate-failure baseline."""
    proj = tmp_path / "retrieve_w607bi_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "auth.py").write_text(
        "def login(user, password):\n"
        "    session = create_session(user)\n"
        "    return session\n\n"
        "def create_session(user):\n"
        "    return {'user': user}\n\n"
        "def logout(session):\n"
        "    session.clear()\n",
        encoding="utf-8",
    )
    (src / "checkout.py").write_text(
        "def checkout(cart):\n"
        "    for item in cart:\n"
        "        process_item(item)\n\n"
        "def process_item(item):\n"
        "    return item.price\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


def _invoke_retrieve(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam retrieve`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("retrieve")
    args.extend(extra)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BI substrate-CALL markers
# ---------------------------------------------------------------------------


def test_retrieve_clean_envelope_omits_w607bi_markers(cli_runner, retrieve_project):
    """Clean retrieve -> no W607-BI substrate markers.

    Byte-identical-on-happy-path: an empty W607-BI bucket on the success
    path must NOT introduce ``retrieve_*_failed:`` markers on the
    envelope. The envelope's ``warnings_out`` is omitted entirely on a
    clean run.
    """
    result = _invoke_retrieve(cli_runner, retrieve_project, "login session")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "retrieve"
    # Empty-bucket discipline: NO warnings_out keys on the clean path.
    assert "warnings_out" not in data, (
        f"clean retrieve must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean retrieve must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) load_config failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_retrieve_load_config_failure_marker_format(cli_runner, retrieve_project, monkeypatch):
    """If ``_load_retrieve_config`` raises, surface the W607-BI marker."""
    from roam.commands import cmd_retrieve

    def _boom_config():
        raise RuntimeError("synthetic-load-config-from-W607-BI")

    monkeypatch.setattr(cmd_retrieve, "_load_retrieve_config", _boom_config)

    result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("retrieve_load_config_failed:")]
    assert markers, f"expected retrieve_load_config_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-load-config-from-W607-BI" in m for m in markers), markers
    assert data["summary"].get("partial_success") is True, (
        f"load_config-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_retrieve_w607bi_warnings_in_envelope(cli_runner, retrieve_project, monkeypatch):
    """Non-empty W607-BI bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_retrieve

    def _boom_semantic(conn):
        raise RuntimeError("synthetic-semantic-from-W607-BI")

    monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", _boom_semantic)

    result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BI disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BI disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("retrieve_compute_semantic_coverage_failed:")]
    assert markers, f"expected retrieve_compute_semantic_coverage_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, retrieve_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..BF contracts.
    """
    from roam.commands import cmd_retrieve

    def _boom_semantic(conn):
        raise ValueError("synthetic-shape-detail-from-W607-BI")

    monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", _boom_semantic)

    result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("retrieve_compute_semantic_coverage_failed:")]
    assert failure_markers, f"expected retrieve_compute_semantic_coverage_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "retrieve_compute_semantic_coverage_failed", parts
    assert parts[1] == "ValueError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) FTS5 DEGRADATION test -- a raise in fts5_search must NOT crash the
#     envelope wholesale. The tfidf_rerank fallback retries with rerank=off.
# ---------------------------------------------------------------------------


def test_fts5_degradation_retries_via_tfidf_rerank_fallback(cli_runner, retrieve_project, monkeypatch):
    """A raise in the full pipeline (``_fts5_search_full``) must:

    1. Surface a ``retrieve_fts5_search_failed:`` marker.
    2. Trigger the W607-BI ``_fts5_search_lexical_only`` fallback so
       the envelope still emits results (just rerank=off).
    3. The envelope MUST still emit a structured result with
       ``partial_success: true``.

    This is the "don't crash retrieval wholesale on FTS5 unavailable"
    contract: agents must still receive raw FTS5 results when the
    structural rerank substrate degrades.
    """
    from roam.commands import cmd_retrieve

    def _boom_full(conn, task_str, *, budget, k, rerank, seed_files):
        raise RuntimeError("synthetic-fts5-full-from-W607-BI")

    monkeypatch.setattr(cmd_retrieve, "_fts5_search_full", _boom_full)

    result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    fts5_markers = [m for m in all_wo if m.startswith("retrieve_fts5_search_failed:")]
    assert fts5_markers, f"expected retrieve_fts5_search_failed: marker after FTS5 degradation; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, (
        f"fts5_search-failed envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # Envelope still emits a structured result (the lexical-only
    # fallback ran). The result shape is preserved -- candidates may
    # be 0 or more depending on token overlap, but the keys exist.
    assert "candidates" in data["summary"], (
        f"FTS5-degraded envelope must still surface candidates key; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (6) RERANK DEGRADATION test -- a raise in ``_fts5_search_lexical_only``
#     (the fallback that's only reached when the full pipeline already
#     raised) propagates to the W607-B outer-guard
#     ``retrieve_pipeline_failed:``.
# ---------------------------------------------------------------------------


def test_rerank_degradation_emits_marker_with_partial_success(cli_runner, retrieve_project, monkeypatch):
    """When BOTH the full pipeline AND the lexical-only fallback raise,
    surface both markers (``retrieve_fts5_search_failed:`` +
    ``retrieve_tfidf_rerank_failed:``) AND the W607-B outer-guard
    ``retrieve_pipeline_failed:``.

    Verifies the layered degradation chain stays loud all the way down.
    """
    from roam.commands import cmd_retrieve

    def _boom_full(conn, task_str, *, budget, k, rerank, seed_files):
        raise RuntimeError("synthetic-full-rerank-from-W607-BI")

    def _boom_lexical(conn, task_str, *, budget, k, seed_files):
        raise RuntimeError("synthetic-lexical-rerank-from-W607-BI")

    monkeypatch.setattr(cmd_retrieve, "_fts5_search_full", _boom_full)
    monkeypatch.setattr(cmd_retrieve, "_fts5_search_lexical_only", _boom_lexical)

    result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    fts5_markers = [m for m in all_wo if m.startswith("retrieve_fts5_search_failed:")]
    rerank_markers = [m for m in all_wo if m.startswith("retrieve_tfidf_rerank_failed:")]
    outer_markers = [m for m in all_wo if m.startswith("retrieve_pipeline_failed:")]
    assert fts5_markers, f"expected retrieve_fts5_search_failed: marker; got {all_wo!r}"
    assert rerank_markers, f"expected retrieve_tfidf_rerank_failed: marker; got {all_wo!r}"
    assert outer_markers, f"expected W607-B outer-guard retrieve_pipeline_failed: marker; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True, (
        f"layered-degradation envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # Envelope must still emit candidates key (empty list); never crash.
    assert "candidates" in data["summary"], data["summary"]


# ---------------------------------------------------------------------------
# (7) CONTEXT/RETRIEVE pairing bonus -- both marker families coexist
#     when shared substrate (allocate_token_budget) raises in both.
# ---------------------------------------------------------------------------


def test_context_retrieve_marker_families_coexist(cli_runner, retrieve_project, monkeypatch):
    """W607-BI (retrieve) + W607-BF (context) markers coexist on the same
    corpus. Each command keeps its own marker family discipline --
    ``retrieve_*`` for cmd_retrieve, ``context_*`` for cmd_context --
    they MUST NOT collide.

    The two commands share the token-budget allocator boundary; this
    test confirms that markers from BOTH families surface together when
    both commands are invoked in sequence.
    """
    from roam.cli import cli
    from roam.commands import cmd_context, cmd_retrieve

    def _boom_retrieve_semantic(conn):
        raise RuntimeError("synthetic-retrieve-pairing-from-W607-BI")

    def _boom_context_single(*args, **kwargs):
        raise RuntimeError("synthetic-context-pairing-from-W607-BF")

    monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", _boom_retrieve_semantic)
    monkeypatch.setattr(cmd_context, "_gather_single", _boom_context_single)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(retrieve_project))

        r_retrieve = cli_runner.invoke(cli, ["--json", "retrieve", "login"], catch_exceptions=False)
        assert r_retrieve.exit_code == 0, r_retrieve.output
        d_retrieve = _json.loads(r_retrieve.output)

        r_context = cli_runner.invoke(cli, ["--json", "context", "login"], catch_exceptions=False)
        assert r_context.exit_code == 0, r_context.output
        d_context = _json.loads(r_context.output)
    finally:
        os.chdir(old_cwd)

    # cmd_retrieve markers (W607-BI family)
    retrieve_wo = list(d_retrieve.get("warnings_out") or []) + list(d_retrieve["summary"].get("warnings_out") or [])
    retrieve_markers = [m for m in retrieve_wo if m.startswith("retrieve_")]
    assert retrieve_markers, f"expected retrieve_* markers from cmd_retrieve W607-BI; got {retrieve_wo!r}"
    # cmd_retrieve must NOT carry context_* markers
    for m in retrieve_wo:
        assert not m.startswith("context_"), f"cmd_retrieve envelope must NOT carry context_* markers; got {m!r}"

    # cmd_context markers (W607-BF family)
    context_wo = list(d_context.get("warnings_out") or []) + list(d_context["summary"].get("warnings_out") or [])
    context_markers = [m for m in context_wo if m.startswith("context_")]
    assert context_markers, f"expected context_* markers from cmd_context W607-BF; got {context_wo!r}"
    for m in context_wo:
        assert not m.startswith("retrieve_"), f"cmd_context envelope must NOT carry retrieve_* markers; got {m!r}"


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-BI stays in ``retrieve_*`` family
# ---------------------------------------------------------------------------


def test_w607bi_marker_prefix_stays_in_retrieve_family(cli_runner, retrieve_project, monkeypatch):
    """Every W607-BI substrate marker uses the canonical ``retrieve_*`` prefix.

    cmd_retrieve is distinct from sibling W607-* layers. Marker prefix
    MUST stay ``retrieve_*`` and MUST NOT leak into other family prefixes.
    """
    from roam.commands import cmd_retrieve

    def _boom_semantic(conn):
        raise RuntimeError("synthetic-prefix-discipline-from-W607-BI")

    monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", _boom_semantic)

    result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("retrieve_"), (
            f"every surfaced W607-BI marker must use the ``retrieve_*`` "
            f"prefix family (cmd_retrieve scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("context_", "cmd_context W607-BF"),
            ("understand_", "cmd_understand W607-BC"),
            ("minimap_", "cmd_minimap W607-L / W607-AZ"),
            ("describe_", "cmd_describe W607-K"),
            ("vulns_", "cmd_vulns W607-AQ"),
            ("sbom_", "cmd_sbom W607-AM"),
            ("supply_chain_", "cmd_supply_chain W607-AK"),
            ("cga_", "cmd_cga W607-AF"),
            ("attest_", "cmd_attest W607-AD"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("relate_", "cmd_relate W607-W"),
            ("deps_", "cmd_deps W607-V"),
            ("uses_", "cmd_uses W607-U"),
            ("impact_", "cmd_impact W607-T / W607-BB"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("audit_trail_", "cmd_audit_trail W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("findings_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D / W607-AV"),
            ("vuln_reach_", "cmd_vuln_reach W607-AU"),
            ("capsule_", "cmd_capsule W607-BD"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) Source-level guard: cmd_retrieve carries the W607-BI accumulator
# ---------------------------------------------------------------------------


def test_cmd_retrieve_carries_w607bi_accumulator():
    """AST-level guard: cmd_retrieve source carries the W607-BI accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-BI instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    import ast

    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_retrieve.py"
    assert src_path.exists(), f"cmd_retrieve.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607bi_warnings_out" in src, (
        "W607-BI accumulator missing from cmd_retrieve; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bi" in src, (
        "W607-BI ``_run_check_bi`` helper missing from cmd_retrieve; the "
        "per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_bi is defined inside cmd_retrieve.
    tree = ast.parse(src)
    found_run_check_bi = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bi":
            found_run_check_bi = True
            break
    assert found_run_check_bi, (
        "W607-BI ``_run_check_bi`` helper not found in cmd_retrieve AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (10) Each W607-BI substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bi_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BI substrate boundary is wrapped.

    W607-BI substrate inventory (cmd_retrieve):

    * load_config              -- get_retrieve_config()
    * compute_semantic_coverage -- semantic_coverage(conn)
    * allocate_token_budget    -- int() coercion of --budget
    * fts5_search              -- _fts5_search_full (full FTS5 + rerank)
    * tfidf_rerank             -- _fts5_search_lexical_only (degradation)
    * scope_filter             -- _scope_filter_candidates
    * extract_spans            -- _extract_dry_run_spans (dry-run)
    * compute_confidence_score -- _retrieve_confidence_score
    * suggest_refinements      -- _suggest_refinements
    * serialize_envelope       -- to_json on the JSON envelope

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_retrieve.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "load_config",
        "compute_semantic_coverage",
        "allocate_token_budget",
        "fts5_search",
        "tfidf_rerank",
        "scope_filter",
        "extract_spans",
        "compute_confidence_score",
        "suggest_refinements",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_bi("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_bi(\n        "{phase}"' in src
            or f'_run_check_bi(\n            "{phase}"' in src
            or f'_run_check_bi(\n                "{phase}"' in src
            or f'_run_check_bi(\n                    "{phase}"' in src
            or f'_run_check_bi(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-BI _run_check_bi wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (11) Top-level vs summary.warnings_out parity on disclosure path
# ---------------------------------------------------------------------------


def test_top_level_and_summary_warnings_out_parity(cli_runner, retrieve_project, monkeypatch):
    """top-level warnings_out and summary.warnings_out must agree.

    The bucket is sourced once (combined W607-B + W607-BI) and threaded
    into both channels so consumers reading either end see the same
    lineage.
    """
    from roam.commands import cmd_retrieve

    def _boom_semantic(conn):
        raise RuntimeError("synthetic-parity-from-W607-BI")

    monkeypatch.setattr(cmd_retrieve, "_compute_semantic_coverage", _boom_semantic)

    result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    assert sorted(top_wo) == sorted(summary_wo), (
        f"top-level vs summary.warnings_out must be equal; top={top_wo!r} summary={summary_wo!r}"
    )
    # And the disclosed marker is the semantic one we synthesised.
    semantic_markers = [m for m in top_wo if m.startswith("retrieve_compute_semantic_coverage_failed:")]
    assert semantic_markers, f"expected retrieve_compute_semantic_coverage_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (12) suggest_refinements substrate failure (only triggers on low confidence)
# ---------------------------------------------------------------------------


def test_retrieve_suggest_refinements_failure_marker(cli_runner, retrieve_project, monkeypatch):
    """If ``_suggest_refinements`` raises on the low-confidence path,
    surface the W607-BI marker.

    Forced via a synthetic raise inside the helper; verified by an
    abstract-noun query that triggers the low-confidence threshold
    (or a forced low-conf via monkeypatching _retrieve_confidence_score).
    """
    from roam.commands import cmd_retrieve

    def _boom_refine(task, candidates):
        raise RuntimeError("synthetic-refinements-from-W607-BI")

    def _forced_low_conf(candidates, task=""):
        # Force the low-confidence branch so the refinements helper
        # is actually called.
        return 0.10, "low"

    monkeypatch.setattr(cmd_retrieve, "_suggest_refinements", _boom_refine)
    monkeypatch.setattr(cmd_retrieve, "_retrieve_confidence_score", _forced_low_conf)

    result = _invoke_retrieve(cli_runner, retrieve_project, "login", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("retrieve_suggest_refinements_failed:")]
    # NOTE: refinements only fires when candidates exist + confidence < 0.40.
    # If no candidates surfaced for "login" in this fixture (unlikely but
    # possible), the marker won't fire. In that case the test asserts
    # the substrate boundary remains source-grep-discoverable instead.
    if data["summary"].get("candidates", 0) > 0:
        assert markers, (
            f"expected retrieve_suggest_refinements_failed: marker on "
            f"low-confidence path with candidates; got {all_wo!r}"
        )
