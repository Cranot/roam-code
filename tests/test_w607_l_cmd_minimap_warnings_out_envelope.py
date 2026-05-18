"""W607-L — ``cmd_minimap`` threads ``warnings_out`` onto its envelope.

Twelfth-in-batch W607 consumer-layer arc. DB-shape continuation after
W607-K (cmd_describe). cmd_minimap per CLAUDE.md is a compact codebase
summary generator that consumes the ``graph_metrics`` /
``symbols`` / ``files`` / ``file_stats`` substrates via per-helper SQL
queries; a substrate failure silently degrades the rendered block into
an empty section while the JSON envelope claims success.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_minimap.py`` head-to-tail.
The dominant additional failure surface BEYOND W805-B Pattern-2
silent-SAFE (empty-corpus "minimap rendered (N chars)" verdict) is the
DB-shape silent path through the per-section helpers:

* ``_get_stack`` — direct SQL on ``files.language`` (no try/except).
* ``_get_file_annotations`` — JOIN on graph_metrics + symbols + files.
* ``_get_key_symbols`` — JOIN on graph_metrics + symbols + files.
* ``_get_touch_carefully`` — JOIN on graph_metrics + symbols + files.
* ``_get_hotspots`` — JOIN on files + file_stats.
* ``_get_conventions`` — delegates to ``conventions_helper``.
* ``SELECT path FROM files ORDER BY path`` — bare query in
  ``_render_minimap``.

Pre-W607-L any of those raising propagated out as an unhandled
exception → the envelope crashed. W607-L wraps each helper, surfaces
``minimap_<phase>_failed:<exc>:<detail>`` markers via ``warnings_out``,
and falls back to empty sections so the envelope still emits cleanly.

W805-B (Pattern-2 silent-SAFE on empty corpus) is COMPLEMENTARY: it
pins the empty-corpus verdict shape ("minimap rendered (148 chars)" on
0-symbol corpus). W607-L adds the substrate-failure disclosure axis to
the SAME envelope. On empty corpus the helpers return empty results
(NOT exceptions), so warnings_out stays empty and the envelope is
byte-identical — W805-B xfail-strict tests MUST remain xfailed after
W607-L lands.

Marker family is ``minimap_*`` — NOT ``describe_*`` (W607-K), NOT
``grep_*`` (W607-G), NOT ``history_*`` (W607-H), NOT ``refs_text_*``
(W607-I), NOT ``delete_check_*`` (W607-J), NOT ``search_*`` /
``complete_*`` / ``semantic_*`` (W607-E/F/A). The marker-prefix
discipline test pins this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. ``warnings_out`` is a
plain accumulator (mirrors W607-G's cmd_grep / W607-K's cmd_describe
idiom). Section helpers accept the bucket through ``_render_minimap``'s
keyword-only ``warnings_out`` parameter; the outer-guard wraps the
``open_db`` + ``_render_minimap`` scope so the bucket collects markers
consistently across both branches.

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
# Helpers — invoke minimap via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_minimap(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam minimap`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("minimap")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture — populated, indexed corpus with real symbols.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def minimap_project(tmp_path, monkeypatch):
    """Indexed corpus with multiple symbols, edges, and a populated
    graph_metrics table — used as the W607-L substrate-failure baseline.

    Distinct from the W805-B empty_corpus fixture (which deliberately
    contains 0 symbols / 0 edges to pin the silent-SAFE verdict bug);
    this corpus DOES have substrate to query, so the W607-L axis is
    "what happens when a substrate raises mid-query" rather than
    "what happens on empty corpus".
    """
    proj = tmp_path / "minimap_w607l_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n',
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


def test_clean_happy_path(cli_runner, minimap_project):
    """Clean minimap on populated corpus → envelope omits warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. The empty-bucket-no-keys discipline ensures
    consumers can't accidentally read a stale or always-present
    warnings_out field.
    """
    result = _invoke_minimap(cli_runner, minimap_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "minimap"
    # Real corpus → real verdict.
    assert "minimap rendered" in data["summary"]["verdict"].lower(), data["summary"]
    # Empty-bucket discipline: NO warnings_out keys.
    assert "warnings_out" not in data, (
        f"clean minimap must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean minimap must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) DB-phase failure marker fires when a substrate helper raises
# ---------------------------------------------------------------------------


def test_db_phase_failure_marker(cli_runner, minimap_project, monkeypatch):
    """If a substrate helper raises, the W607-L outer-guard surfaces a
    ``minimap_<phase>_failed:`` marker.

    Substrate-failure shape: patch ``_get_key_symbols`` to raise. The
    per-helper guard inside ``_render_minimap`` catches it and threads
    the marker.
    """
    from roam.commands import cmd_minimap

    def _boom_key_symbols(conn, limit=5):
        raise PermissionError("synthetic-key-symbols-from-W607-L")

    monkeypatch.setattr(cmd_minimap, "_get_key_symbols", _boom_key_symbols)

    result = _invoke_minimap(cli_runner, minimap_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"_get_key_symbols PermissionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("minimap_key_symbols_failed:") for m in top_wo), (
        f"expected ``minimap_key_symbols_failed:`` marker; got {top_wo!r}"
    )
    assert any("PermissionError" in m for m in top_wo), top_wo
    assert any("synthetic-key-symbols-from-W607-L" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (3) Helper consumption failure disclosed (section helper raises)
# ---------------------------------------------------------------------------


def test_helper_consumption_failure_disclosed(cli_runner, minimap_project, monkeypatch):
    """Section helper failure surfaces via ``minimap_<phase>_failed:``.

    Patch ``_get_hotspots`` to raise — this exercises the per-helper
    guard inside ``_render_minimap`` and the W607-L marker-thread.
    """
    from roam.commands import cmd_minimap

    def _boom_hotspots(conn, limit=5):
        raise RuntimeError("synthetic-hotspots-from-W607-L")

    monkeypatch.setattr(cmd_minimap, "_get_hotspots", _boom_hotspots)

    result = _invoke_minimap(cli_runner, minimap_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"_get_hotspots RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    hotspot_markers = [m for m in top_wo if m.startswith("minimap_hotspots_failed:")]
    assert hotspot_markers, f"expected ``minimap_hotspots_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in hotspot_markers), hotspot_markers


# ---------------------------------------------------------------------------
# (4) No-failure clean → byte-identical envelope (hash stability)
# ---------------------------------------------------------------------------


def test_no_match_byte_identical(cli_runner, minimap_project):
    """Clean envelope must NOT carry warnings_out keys when no markers fire.

    Empty-bucket discipline: the W607-L plumbing must NOT leak the
    empty bucket onto a clean envelope. The pre-W607-L envelope shape is
    preserved byte-for-byte when no markers fired. Hash-stable contract
    matches W607-A/B/C/D/E/F/G/H/I/J/K discipline.
    """
    result = _invoke_minimap(cli_runner, minimap_project, json_mode=True)
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


def test_three_segment_marker_shape(cli_runner, minimap_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A/B/C/D/E/F/G/H/I/J/K contracts.
    """
    from roam.commands import cmd_minimap

    def _boom_key_symbols(conn, limit=5):
        raise PermissionError("synthetic-shape-detail-from-W607-L")

    monkeypatch.setattr(cmd_minimap, "_get_key_symbols", _boom_key_symbols)

    result = _invoke_minimap(cli_runner, minimap_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "_get_key_symbols outer-guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("minimap_key_symbols_failed:")]
    assert failure_markers, f"expected minimap_key_symbols_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "minimap_key_symbols_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline — ``minimap_*`` not describe/subprocess/lexical
#     family prefixes (closed-enum discipline)
# ---------------------------------------------------------------------------


def test_marker_prefix_minimap_not_describe(cli_runner, minimap_project, monkeypatch):
    """Every surfaced marker uses the canonical ``minimap_*`` prefix.

    cmd_minimap is the DB-SHAPE-AGGREGATOR axis — distinct from:

    * cmd_describe         → ``describe_*`` (W607-K flagship aggregator)
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
    contributor mis-routing a marker into a subprocess family because
    cmd_minimap is also a CLI surface). Closes the closed-enum
    discipline at the cmd_minimap boundary.
    """
    from roam.commands import cmd_minimap

    def _boom_key_symbols(conn, limit=5):
        raise PermissionError("synthetic-prefix-discipline-from-W607-L")

    monkeypatch.setattr(cmd_minimap, "_get_key_symbols", _boom_key_symbols)

    result = _invoke_minimap(cli_runner, minimap_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("minimap_"), (
            f"every surfaced marker must use the W607-L ``minimap_*`` "
            f"prefix family (cmd_minimap DB-shape scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("describe_", "cmd_describe W607-K"),
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


def test_partial_success_flip_on_db_failure(cli_runner, minimap_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    minimap" from "minimap ran with substrate degradation" via
    summary.partial_success alone, independent of the existing
    markdown-blob content. (Complementary to W805-B — that axis pins
    the empty-corpus silent-SAFE verdict; W607-L pins the
    substrate-failure flip on the non-empty-corpus path.)
    """
    from roam.commands import cmd_minimap

    def _boom_key_symbols(conn, limit=5):
        raise PermissionError("synthetic-partial-success-from-W607-L")

    monkeypatch.setattr(cmd_minimap, "_get_key_symbols", _boom_key_symbols)

    result = _invoke_minimap(cli_runner, minimap_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) summary.warnings_out mirror — top-level AND summary populated
# ---------------------------------------------------------------------------


def test_summary_warnings_out_mirror(cli_runner, minimap_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror gives
    consumers reading only the summary block visibility too. Mirror
    parity with W607-A/B/C/D/E/F/G/H/I/J/K consumers.
    """
    from roam.commands import cmd_minimap

    def _boom_key_symbols(conn, limit=5):
        raise PermissionError("synthetic-mirror-from-W607-L")

    monkeypatch.setattr(cmd_minimap, "_get_key_symbols", _boom_key_symbols)

    result = _invoke_minimap(cli_runner, minimap_project, json_mode=True)
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
# (9) Top-level mirror explicitly checked (W607-A..K discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(cli_runner, minimap_project, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. W607-A through W607-K pinned the same
    discipline; W607-L extends it to cmd_minimap — second DB-shape
    consumer in the W607 arc.
    """
    from roam.commands import cmd_minimap

    def _boom_key_symbols(conn, limit=5):
        raise PermissionError("synthetic-top-level-from-W607-L")

    monkeypatch.setattr(cmd_minimap, "_get_key_symbols", _boom_key_symbols)

    result = _invoke_minimap(cli_runner, minimap_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (10) W805-B parity — strict-xfail Pattern-2 disclosure tests must remain
#      xfailed (W607-L does NOT fix the empty-corpus silent-SAFE bug).
# ---------------------------------------------------------------------------


def test_w805_b_xfail_still_strict():
    """W805-B strict-xfail Pattern-2 disclosure must remain xfailed.

    W805-B pins 4 strict-xfail tests on the empty-corpus
    silent-SAFE path for cmd_minimap:

    * ``test_empty_corpus_partial_success_set``
    * ``test_empty_corpus_explicit_state``
    * ``test_empty_corpus_no_silent_healthy_minimap``
    * ``test_no_symbols_emits_explicit_no_symbols_state``

    W607-L adds a COMPLEMENTARY disclosure axis (DB-shape substrate
    failure via ``warnings_out``), but does NOT address the
    empty-corpus Pattern-2 contract — state-on-empty-corpus is a
    separate fix. The W805-B tests must stay xfailed after W607-L
    lands — a drive-by graduation of ANY of those four to PASS would
    mean W607-L accidentally fixed something it wasn't scoped to fix.

    Verify the xfail-strict markers are still present in the W805-B
    test source. Source-text scan beats invoking pytest-on-pytest;
    if the strict markers were removed, this assertion catches it.
    """
    here = Path(__file__).parent
    w805_b = here / "test_w805_b_cmd_minimap_empty_corpus.py"
    assert w805_b.exists(), f"W805-B test file missing at {w805_b}"
    src = w805_b.read_text(encoding="utf-8")
    # Count strict-xfail markers — must remain at 4 (the original pin set).
    strict_count = src.count("strict=True")
    assert strict_count == 4, (
        f"W805-B strict-xfail marker count drift: expected 4, got "
        f"{strict_count}. W607-L must NOT graduate any W805-B bug; the "
        f"empty-corpus state disclosure is a separate Pattern-2 contract "
        f"orthogonal to the W607-L DB-shape substrate-degrade axis."
    )
    # Names of the 4 xfail-strict tests — pin so a future rename without
    # graduation doesn't slip past.
    for test_name in (
        "test_empty_corpus_partial_success_set",
        "test_empty_corpus_explicit_state",
        "test_empty_corpus_no_silent_healthy_minimap",
        "test_no_symbols_emits_explicit_no_symbols_state",
    ):
        assert test_name in src, (
            f"W805-B xfail-strict test {test_name!r} was renamed or "
            f"removed without graduation. W607-L scope is the DB-shape "
            f"substrate-degrade axis only — empty-corpus silent-SAFE "
            f"is a separate Pattern-2 fix."
        )
