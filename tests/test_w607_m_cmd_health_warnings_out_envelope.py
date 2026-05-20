"""W607-M — ``cmd_health`` threads ``warnings_out`` onto its envelope.

Thirteenth-in-batch W607 consumer-layer arc. DB-shape continuation after
W607-K (cmd_describe flagship aggregator) and W607-L (cmd_minimap
DB-shape aggregator). cmd_health per CLAUDE.md is THE flagship CI-gate
command — the canonical 0-100 score that downstream CI consumers
key on. It consumes graph_metrics / symbols / files / file_stats /
graph (build_symbol_graph) / find_cycles / detect_layers /
propagation_cost / algebraic_connectivity / imported_coverage_overview
substrates via direct SQL queries + helper calls; any of those raising
silently degrades the pipeline while the JSON envelope claims success.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_health.py`` head-to-tail.
The dominant additional failure surface BEYOND W805-833 / W833 Pattern-2
silent-Healthy on empty corpus is the DB-shape silent path through:

* ``build_symbol_graph(conn)`` — graph build from edges table.
* ``find_cycles(G)`` / ``format_cycles(cycles, conn)`` — SCC traversal.
* ``TOP_BY_DEGREE`` SQL on ``graph_metrics`` — god-component query.
* ``SELECT betweenness FROM graph_metrics WHERE betweenness > 0`` —
  bottleneck percentile substrate.
* ``TOP_BY_BETWEENNESS`` SQL — bottleneck enumeration.
* ``detect_layers(G)`` / ``find_violations(G, layer_map)`` /
  ``batched_in(SELECT s.id, s.name, f.path ...)`` — layer-violation
  triple.
* ``SELECT COUNT(*) FROM symbols`` — total_symbols + tangle ratio.
* ``propagation_cost(G)`` — transitive-closure-based metric.
* ``algebraic_connectivity(G)`` — Fiedler eigenvalue.
* ``imported_coverage_overview(conn)`` — imported coverage helper.
* ``SELECT AVG(health_score) FROM file_stats`` — file-health factor
  (already had a try/except floor; W607-M adds the disclosure marker).

Pre-W607-M any of those raising propagated out as an unhandled
exception → the envelope crashed. W607-M wraps each phase, surfaces
``health_<phase>_failed:<exc>:<detail>`` markers via ``warnings_out``,
and falls back to floor values so the envelope still emits cleanly.

W833 / W805-833 (Pattern-2 silent-100/100-Healthy on empty corpus)
is COMPLEMENTARY: it pins the empty-corpus verdict shape. W607-M
adds the substrate-failure disclosure axis to the SAME envelope. On
empty corpus the early-return at ``_early_symbol_count == 0`` fires
BEFORE the W607-M-instrumented phases, so warnings_out stays empty
and the envelope is byte-identical — W833 must remain at the same
pass/skip status after W607-M lands.

Marker family is ``health_*`` — NOT ``describe_*`` (W607-K), NOT
``minimap_*`` (W607-L), NOT ``grep_*`` (W607-G), NOT ``history_*``
(W607-H), NOT ``refs_text_*`` (W607-I), NOT ``delete_check_*``
(W607-J), NOT ``search_*`` / ``complete_*`` / ``semantic_*``
(W607-E/F/A). The marker-prefix discipline test pins this closed-enum
distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. ``warnings_out`` is a
plain accumulator (mirrors W607-G's cmd_grep / W607-K's cmd_describe
/ W607-L's cmd_minimap idiom). Per-phase try/except blocks wrap each
substrate-touching call inside the ``with open_db(...) as conn:`` body;
the bucket lives in the enclosing function scope.

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
# Helpers — invoke health via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_health(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam health`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("health")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture — populated, indexed corpus with real symbols, edges, metrics.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def health_project(tmp_path, monkeypatch):
    """Indexed corpus with multiple symbols + edges (call graph) so
    the W607-M substrate-failure baseline has real data to query.

    Distinct from the W833 empty_corpus fixture (which deliberately
    contains 0 symbols to pin the silent-Healthy-100/100 verdict bug);
    this corpus DOES have substrate, so the W607-M axis is "what
    happens when a substrate raises mid-query" rather than "what
    happens on empty corpus".
    """
    proj = tmp_path / "health_w607m_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\n"
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
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
# (1) Happy path — populated corpus → no warnings_out
# ---------------------------------------------------------------------------


def test_clean_happy_path(cli_runner, health_project):
    """Clean health on populated corpus → envelope omits warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. The empty-bucket-no-keys discipline ensures
    consumers can't accidentally read a stale or always-present
    warnings_out field.
    """
    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "health"
    # Real corpus → real verdict; should not start with a Pattern-2
    # empty-corpus disclosure phrase.
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Empty-bucket discipline: NO warnings_out keys.
    assert "warnings_out" not in data, (
        f"clean health must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean health must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) DB-phase failure marker fires when a substrate helper raises
# ---------------------------------------------------------------------------


def test_db_phase_failure_marker(cli_runner, health_project, monkeypatch):
    """If a substrate helper raises, the W607-M per-phase guard surfaces a
    ``health_<phase>_failed:`` marker.

    Substrate-failure shape: patch ``find_cycles`` to raise. The
    per-phase guard inside ``health()`` catches it and threads the
    marker.
    """
    from roam.commands import cmd_health

    def _boom_cycles(G):
        raise PermissionError("synthetic-cycles-from-W607-M")

    monkeypatch.setattr(cmd_health, "find_cycles", _boom_cycles)

    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"find_cycles PermissionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("health_cycles_failed:") for m in top_wo), (
        f"expected ``health_cycles_failed:`` marker; got {top_wo!r}"
    )
    assert any("PermissionError" in m for m in top_wo), top_wo
    assert any("synthetic-cycles-from-W607-M" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (2b) Algebraic-connectivity honesty: null export + availability flag when
#      the numpy+scipy substrate is missing (the real production case). The
#      pre-fix bug exported a fabricated 0.0 indistinguishable from a
#      legitimate disconnected-graph reading; a JSON consumer couldn't tell.
# ---------------------------------------------------------------------------


def test_algebraic_connectivity_unavailable_exports_null(cli_runner, health_project, monkeypatch):
    """When ``algebraic_connectivity`` raises (no numpy/scipy), the JSON
    export must be ``null`` with ``algebraic_connectivity_available: false``
    — NOT a fake 0.0 — at both the summary and top-level sites."""
    from roam.commands import cmd_health

    def _boom_fiedler(G):
        raise ModuleNotFoundError("No module named 'scipy'")

    monkeypatch.setattr(cmd_health, "algebraic_connectivity", _boom_fiedler)

    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data.get("summary") or {}
    assert summary.get("algebraic_connectivity") is None, (
        f"missing-substrate must export null, not a fake 0.0; got {summary.get('algebraic_connectivity')!r}"
    )
    assert summary.get("algebraic_connectivity_available") is False, summary
    # Top-level mirror must agree.
    assert data.get("algebraic_connectivity") is None, data.get("algebraic_connectivity")
    assert data.get("algebraic_connectivity_available") is False
    # And the failure is still disclosed in warnings_out (loud lineage).
    top_wo = data.get("warnings_out") or []
    assert any(m.startswith("health_algebraic_connectivity_failed:") for m in top_wo), top_wo


def test_algebraic_connectivity_available_exports_value(cli_runner, health_project, monkeypatch):
    """When the Fiedler value computes, it is exported verbatim with
    ``algebraic_connectivity_available: true`` (no false n/a)."""
    from roam.commands import cmd_health

    monkeypatch.setattr(cmd_health, "algebraic_connectivity", lambda G: 0.0916)

    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data.get("summary") or {}
    assert summary.get("algebraic_connectivity") == 0.0916, summary
    assert summary.get("algebraic_connectivity_available") is True, summary
    assert data.get("algebraic_connectivity") == 0.0916
    assert data.get("algebraic_connectivity_available") is True


# ---------------------------------------------------------------------------
# (3) Helper consumption failure disclosed (substrate helper raises)
# ---------------------------------------------------------------------------


def test_helper_consumption_failure_disclosed(cli_runner, health_project, monkeypatch):
    """Substrate-helper failure surfaces via ``health_<phase>_failed:``.

    Patch ``imported_coverage_overview`` to raise — this exercises the
    coverage-import per-phase guard inside ``health()`` and the W607-M
    marker-thread.
    """
    from roam.commands import cmd_health

    def _boom_coverage(conn):
        raise RuntimeError("synthetic-coverage-from-W607-M")

    monkeypatch.setattr(cmd_health, "imported_coverage_overview", _boom_coverage)

    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"imported_coverage_overview RuntimeError must surface "
        f"top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    cov_markers = [m for m in top_wo if m.startswith("health_imported_coverage_failed:")]
    assert cov_markers, f"expected ``health_imported_coverage_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in cov_markers), cov_markers


# ---------------------------------------------------------------------------
# (4) No-failure clean → byte-identical envelope (hash stability)
# ---------------------------------------------------------------------------


def test_no_match_byte_identical(cli_runner, health_project):
    """Clean envelope must NOT carry warnings_out keys when no markers fire.

    Empty-bucket discipline: the W607-M plumbing must NOT leak the
    empty bucket onto a clean envelope. The pre-W607-M envelope shape
    is preserved byte-for-byte when no markers fired. Hash-stable
    contract matches W607-A/B/C/D/E/F/G/H/I/J/K/L discipline.
    """
    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert "warnings_out" not in data, (
        f"clean envelope must omit top-level warnings_out; got data['warnings_out']={data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean envelope must omit summary.warnings_out; got summary={data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, health_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A/B/C/D/E/F/G/H/I/J/K/L contracts.
    """
    from roam.commands import cmd_health

    def _boom_cycles(G):
        raise PermissionError("synthetic-shape-detail-from-W607-M")

    monkeypatch.setattr(cmd_health, "find_cycles", _boom_cycles)

    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "find_cycles per-phase guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("health_cycles_failed:")]
    assert failure_markers, f"expected health_cycles_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "health_cycles_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline — ``health_*`` not describe/minimap/etc.
#     family prefixes (closed-enum discipline)
# ---------------------------------------------------------------------------


def test_marker_prefix_health_not_describe_or_minimap(cli_runner, health_project, monkeypatch):
    """Every surfaced marker uses the canonical ``health_*`` prefix.

    cmd_health is the FLAGSHIP CI-GATE axis — distinct from:

    * cmd_describe         → ``describe_*`` (W607-K flagship aggregator)
    * cmd_minimap          → ``minimap_*`` (W607-L DB-shape aggregator)
    * cmd_grep             → ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep     → ``history_*`` (W607-H pickaxe)
    * cmd_refs_text        → ``refs_text_*`` (W607-I string-audit)
    * cmd_delete_check     → ``delete_check_*`` (W607-J diff-gate)
    * cmd_search           → ``search_*`` (W607-E lexical)
    * cmd_complete         → ``complete_*`` (W607-F prefix)
    * cmd_search_semantic  → ``semantic_*`` (W607-A FTS5)
    * cmd_findings         → ``findings_query_*`` (W607-C registry)
    * cmd_dogfood          → ``dogfood_*`` (W607-D corpus loader)
    * cmd_retrieve         → ``retrieve_*`` (W607-B pipeline)

    Hard guard against accidental marker-prefix drift (a future
    contributor mis-routing a marker into a sibling family because
    cmd_health is a high-traffic CI surface that may be edited next to
    cmd_describe / cmd_minimap by a refactor wave). Closes the
    closed-enum discipline at the cmd_health boundary.
    """
    from roam.commands import cmd_health

    def _boom_cycles(G):
        raise PermissionError("synthetic-prefix-discipline-from-W607-M")

    monkeypatch.setattr(cmd_health, "find_cycles", _boom_cycles)

    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("health_"), (
            f"every surfaced marker must use the W607-M ``health_*`` "
            f"prefix family (cmd_health flagship CI-gate scope); "
            f"got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
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
# (7) partial_success flips on DB failure
# ---------------------------------------------------------------------------


def test_partial_success_flip_on_db_failure(cli_runner, health_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    health" from "health ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    (Complementary to W833 — that axis pins the empty-corpus
    silent-100/100-Healthy verdict; W607-M pins the substrate-failure
    flip on the non-empty-corpus path.)
    """
    from roam.commands import cmd_health

    def _boom_cycles(G):
        raise PermissionError("synthetic-partial-success-from-W607-M")

    monkeypatch.setattr(cmd_health, "find_cycles", _boom_cycles)

    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) summary.warnings_out mirror — top-level AND summary populated
# ---------------------------------------------------------------------------


def test_summary_warnings_out_mirror(cli_runner, health_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror
    gives consumers reading only the summary block visibility too.
    Mirror parity with W607-A/B/C/D/E/F/G/H/I/J/K/L consumers.
    """
    from roam.commands import cmd_health

    def _boom_cycles(G):
        raise PermissionError("synthetic-mirror-from-W607-M")

    monkeypatch.setattr(cmd_health, "find_cycles", _boom_cycles)

    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    # Top-level and summary content must be equal.
    assert sorted(data["warnings_out"]) == sorted(data["summary"]["warnings_out"]), (
        f"top-level vs summary.warnings_out must be equal; "
        f"top={data['warnings_out']!r} summary={data['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (9) Top-level mirror explicitly checked (W607-A..L discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(cli_runner, health_project, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. W607-A through W607-L pinned the same
    discipline; W607-M extends it to cmd_health — third DB-shape
    consumer in the W607 arc (after K=describe, L=minimap).
    """
    from roam.commands import cmd_health

    def _boom_cycles(G):
        raise PermissionError("synthetic-top-level-from-W607-M")

    monkeypatch.setattr(cmd_health, "find_cycles", _boom_cycles)

    result = _invoke_health(cli_runner, health_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (10) W833 / W805-833 parity — empty-corpus silent-Healthy disclosure
#      must NOT be graduated by W607-M (separate Pattern-2 fix wave).
# ---------------------------------------------------------------------------


def test_w805_833_xfail_still_strict():
    """W833 strict-xfail Pattern-2 disclosure must remain at status quo.

    W833 / W805-833 pins the empty-corpus silent-100/100-Healthy
    verdict for cmd_health (Pattern 2 silent fallback): on a
    zero-symbol corpus, the pre-fix code returned health_score=100
    via the empty-product geometric-mean. W834 / W1030 / W1052 etc.
    layered the empty-corpus carve-out (early-return at
    ``_early_symbol_count == 0``), so the verdict on empty corpus
    now correctly discloses "no symbols to analyze" with
    ``partial_success=True``.

    W607-M adds a COMPLEMENTARY disclosure axis (DB-shape substrate
    failure via ``warnings_out`` on the non-empty-corpus path), but
    does NOT address the empty-corpus Pattern-2 contract — state-on-
    empty-corpus is handled by the early-return BEFORE the W607-M
    instrumented phases run. The W833 test must remain at its
    pre-W607-M pass/skip status — a drive-by graduation would mean
    W607-M accidentally fixed something it wasn't scoped to fix.

    Verify the W833 test source still contains its canonical
    Pattern-2 forbidden-fragment blacklist and required-empty-fragment
    allowlist. Source-text scan beats invoking pytest-on-pytest; if
    the blacklist/allowlist were removed, this assertion catches it.
    """
    here = Path(__file__).parent
    w833 = here / "test_w833_health_empty_corpus.py"
    assert w833.exists(), f"W833 test file missing at {w833}"
    src = w833.read_text(encoding="utf-8")
    # Pin the forbidden-fragment blacklist names — these encode the
    # Pattern-2 silent-fallback discipline that W833 owns and W607-M
    # does NOT touch.
    assert "_FORBIDDEN_VERDICT_FRAGMENTS" in src, (
        "W833 forbidden-fragment blacklist renamed or removed; "
        "W607-M scope is the DB-shape substrate-degrade axis only — "
        "empty-corpus silent-Healthy is a separate Pattern-2 fix."
    )
    assert "_REQUIRED_EMPTY_FRAGMENTS" in src, (
        "W833 required-empty-fragment allowlist renamed or removed; "
        "W607-M does not change the empty-corpus verdict contract."
    )
    # Pin the canonical test name so a rename without graduation
    # doesn't slip past.
    assert "test_envelope_shape_and_no_silent_healthy" in src, (
        "W833 canonical test renamed or removed; W607-M scope is the DB-shape substrate-degrade axis only."
    )
