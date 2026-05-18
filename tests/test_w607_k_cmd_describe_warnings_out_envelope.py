"""W607-K — ``cmd_describe`` threads ``warnings_out`` onto its envelope.

Eleventh-in-batch W607 consumer-layer arc. DB-shape PIVOT — distinct
from the W607-G/H/I/J subprocess quartet (cmd_grep / cmd_history_grep /
cmd_refs_text / cmd_delete_check). First DB-shape consumer in the W607
arc. cmd_describe is a flagship aggregator (CLAUDE.md "Codebase
navigation with roam" tutorial cites it) that consumes the
``graph_metrics`` / ``symbol_metrics`` / ``file_edges`` /
``build_symbol_graph`` / ``cycles_summary`` substrates; a substrate
failure silently degrades the markdown blob into "No X available"
sentinel lines while the JSON envelope claims success.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_describe.py`` head-to-tail.
Per CLAUDE.md L935: "Auto-generate a project description for AI coding
agents." The dominant additional failure surface BEYOND W805-I
Pattern-2 silent SAFE (empty-corpus verdict + missing state) is the
DB-shape silent path through the section helpers:

* ``_section_key_abstractions`` — bare ``except Exception`` around the
  graph_metrics JOIN → emits "Graph metrics not available." silently.
* ``_section_architecture`` — bare ``except Exception`` around
  ``build_symbol_graph`` + ``detect_layers`` + ``cycles_summary`` →
  silently emits "Architecture analysis not available".
* ``_section_complexity_guide`` — ``log_swallowed`` around
  ``symbol_metrics`` count/critical query (only fires when
  ROAM_VERBOSE=1).
* ``_section_dependencies`` — bare ``except Exception`` around
  ``file_edges`` JOIN → silently emits "File edge data not available."
* ``_agent_prompt_data`` — ``log_swallowed`` around 4 silent paths
  (project_root, key_abstractions, hotspots, cycle_health) AND a bare
  ``except: pass`` on ``detect_project_shape``. All sentinel-collapse
  to ``"unknown"`` / ``[]`` / ``"N/A"`` in the agent-prompt data
  without surfacing the substrate failure lineage.

W805-I (Pattern-2 silent-SAFE on empty corpus) is COMPLEMENTARY: it
pins the empty-corpus verdict shape (verdict reads "python project, 2
files, 1 languages" on 0-symbol corpus). W607-K is COMPLEMENTARY: it
adds the substrate-failure disclosure axis to the SAME envelope. The
W805-I xfail-strict tests MUST remain xfailed after W607-K lands
(W607-K does NOT graduate the empty-corpus state bug; it adds a
distinct disclosure axis).

Marker family is ``describe_*`` — NOT ``grep_*`` (W607-G), NOT
``history_*`` (W607-H), NOT ``refs_text_*`` (W607-I), NOT
``delete_check_*`` (W607-J), NOT ``search_*``/``complete_*``/
``semantic_*`` (W607-E/F/A). The marker-prefix discipline test pins
this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. ``warnings_out``
is a plain accumulator (mirrors W607-G's cmd_grep / W607-C's
cmd_findings idiom). Section helpers accept a keyword-only
``warnings_out`` parameter; the outer-guard wraps the
``_agent_prompt_data`` and section-pipeline scopes so the bucket
collects markers consistently across both branches.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture — populated, indexed corpus with real symbols.
# ---------------------------------------------------------------------------


@pytest.fixture
def describe_project(tmp_path, monkeypatch):
    """Indexed corpus with multiple symbols, edges, and a populated
    graph_metrics table — used as the W607-K substrate-failure baseline.

    Distinct from the W805-I empty_corpus fixture (which deliberately
    contains 0 symbols / 0 edges to pin the silent-SAFE verdict bug);
    this corpus DOES have substrate to query, so the W607-K axis is
    "what happens when a substrate raises mid-query" rather than
    "what happens on empty corpus".
    """
    proj = tmp_path / "describe_w607k_project"
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


def test_clean_happy_path(describe_project):
    """Clean describe on populated corpus → envelope omits warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. The empty-bucket-no-keys discipline ensures
    consumers can't accidentally read a stale or always-present
    warnings_out field.
    """
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "describe"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["command"] == "describe"
    # Real corpus → real verdict.
    assert "python" in data["summary"]["verdict"].lower(), data["summary"]
    # Empty-bucket discipline: NO warnings_out keys.
    assert "warnings_out" not in data, (
        f"clean describe must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean describe must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) DB-phase failure marker fires when a substrate helper raises
# ---------------------------------------------------------------------------


def test_db_phase_failure_marker(describe_project, monkeypatch):
    """If a section helper raises, the W607-K outer-guard surfaces a
    ``describe_<phase>_failed:`` marker.

    Substrate-failure shape: simulate ``cycles_summary`` raising at the
    JSON-tail call site (which would otherwise propagate uncaught and
    crash the envelope before W607-K). The outer-guard catches it and
    threads the marker.
    """
    from roam.commands import cmd_describe

    def _boom_cycles_summary(conn):
        raise PermissionError("synthetic-cycles-summary-from-W607-K")

    # Patch the imported reference inside cmd_describe — the JSON-tail
    # path re-imports ``cycles_summary as _cs`` lazily, so we have to
    # patch the source module.
    import roam.quality.cycles as _qc

    monkeypatch.setattr(_qc, "cycles_summary", _boom_cycles_summary)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "describe"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"cycles_summary PermissionError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("describe_cycles_summary_failed:") for m in top_wo), (
        f"expected ``describe_cycles_summary_failed:`` marker; got {top_wo!r}"
    )
    assert any("PermissionError" in m for m in top_wo), top_wo
    assert any("synthetic-cycles-summary-from-W607-K" in m for m in top_wo), top_wo
    _ = cmd_describe  # silence unused-import lint


# ---------------------------------------------------------------------------
# (3) Helper consumption failure disclosed (section helper raises)
# ---------------------------------------------------------------------------


def test_helper_consumption_failure_disclosed(describe_project, monkeypatch):
    """Section helper failure surfaces via ``describe_<phase>_failed:``.

    Patch ``build_symbol_graph`` to raise — this exercises
    ``_section_architecture``'s try/except branch and the W607-K
    marker-thread inside it.
    """
    import roam.graph.builder as _builder

    def _boom_build(conn):
        raise RuntimeError("synthetic-builder-from-W607-K")

    monkeypatch.setattr(_builder, "build_symbol_graph", _boom_build)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "describe"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"build_symbol_graph RuntimeError must surface top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    # The architecture section uses ``build_symbol_graph`` → ``detect_layers``
    # → ``cycles_summary``. ANY of the markers from that phase is acceptable.
    arch_markers = [m for m in top_wo if m.startswith("describe_architecture_failed:")]
    assert arch_markers, f"expected ``describe_architecture_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in arch_markers), arch_markers


# ---------------------------------------------------------------------------
# (4) No-failure clean → byte-identical envelope (hash stability)
# ---------------------------------------------------------------------------


def test_no_match_byte_identical(describe_project):
    """Clean envelope must NOT carry warnings_out keys when no markers fire.

    Empty-bucket discipline: the W607-K plumbing must NOT leak the
    empty bucket onto a clean envelope. The pre-W607-K envelope shape is
    preserved byte-for-byte when no markers fired. Hash-stable contract
    matches W607-A/B/C/D/E/F/G/H/I/J discipline.
    """
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "describe"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert "warnings_out" not in data, (
        f"clean envelope must omit top-level warnings_out; got data['warnings_out']={data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean envelope must omit summary.warnings_out; got summary={data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(describe_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A/B/C/D/E/F/G/H/I/J contracts.
    """
    import roam.quality.cycles as _qc

    def _boom_cycles_summary(conn):
        raise PermissionError("synthetic-shape-detail-from-W607-K")

    monkeypatch.setattr(_qc, "cycles_summary", _boom_cycles_summary)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "describe"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "cycles_summary outer-guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("describe_cycles_summary_failed:")]
    assert failure_markers, f"expected describe_cycles_summary_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "describe_cycles_summary_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline — ``describe_*`` not subprocess/lexical
#     family prefixes (closed-enum discipline)
# ---------------------------------------------------------------------------


def test_marker_prefix_describe_not_grep_or_search(describe_project, monkeypatch):
    """Every surfaced marker uses the canonical ``describe_*`` prefix.

    cmd_describe is the FLAGSHIP-AGGREGATOR axis (DB-shape consumer) —
    distinct from:

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
    cmd_describe is also a CLI surface). Closes the closed-enum
    discipline at the cmd_describe boundary.
    """
    import roam.quality.cycles as _qc

    def _boom_cycles_summary(conn):
        raise PermissionError("synthetic-prefix-discipline-from-W607-K")

    monkeypatch.setattr(_qc, "cycles_summary", _boom_cycles_summary)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "describe"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("describe_"), (
            f"every surfaced marker must use the W607-K ``describe_*`` "
            f"prefix family (cmd_describe DB-shape scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
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


def test_partial_success_flip_on_db_failure(describe_project, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    describe" from "describe ran with substrate degradation" via
    summary.partial_success alone, independent of the existing
    markdown-blob content. (Complementary to W805-I — that axis pins
    the empty-corpus silent-SAFE verdict; W607-K pins the
    substrate-failure flip.)
    """
    import roam.quality.cycles as _qc

    def _boom_cycles_summary(conn):
        raise PermissionError("synthetic-partial-success-from-W607-K")

    monkeypatch.setattr(_qc, "cycles_summary", _boom_cycles_summary)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "describe"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) summary.warnings_out mirror — top-level AND summary populated
# ---------------------------------------------------------------------------


def test_summary_warnings_out_mirror(describe_project, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror gives
    consumers reading only the summary block visibility too. Mirror
    parity with W607-A/B/C/D/E/F/G/H/I/J consumers.
    """
    import roam.quality.cycles as _qc

    def _boom_cycles_summary(conn):
        raise PermissionError("synthetic-mirror-from-W607-K")

    monkeypatch.setattr(_qc, "cycles_summary", _boom_cycles_summary)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "describe"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

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
# (9) Top-level mirror explicitly checked (W607-A..J discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(describe_project, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. W607-A through W607-J pinned the same
    discipline; W607-K extends it to cmd_describe — first DB-shape
    consumer in the W607 arc.
    """
    import roam.quality.cycles as _qc

    def _boom_cycles_summary(conn):
        raise PermissionError("synthetic-top-level-from-W607-K")

    monkeypatch.setattr(_qc, "cycles_summary", _boom_cycles_summary)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "describe"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (10) W805-I parity — strict-xfail Pattern-2 disclosure tests must remain
#      xfailed (W607-K does NOT fix the empty-corpus silent-SAFE bug).
# ---------------------------------------------------------------------------


def test_w805_i_xfail_still_strict():
    """W805-I strict-xfail Pattern-2 disclosure must remain xfailed.

    W805-I pins 4 strict-xfail tests on the empty-corpus / no-source
    silent-SAFE path:

    * ``test_empty_corpus_partial_success_set``
    * ``test_empty_corpus_explicit_state``
    * ``test_empty_corpus_verdict_discloses_empty``
    * ``test_no_source_corpus_unknown_project_partial_success``
    * ``test_agent_prompt_empty_corpus_partial_success_coupled_to_na``

    W607-K adds a COMPLEMENTARY disclosure axis (DB-shape substrate
    failure via ``warnings_out``), but does NOT address the
    empty-corpus Pattern-2 contract — state-on-empty-corpus is a
    separate fix. The W805-I tests must stay xfailed after W607-K
    lands — a drive-by graduation of ANY of those five to PASS would
    mean W607-K accidentally fixed something it wasn't scoped to fix.

    Verify the xfail-strict markers are still present in the W805-I
    test source. Source-text scan beats invoking pytest-on-pytest;
    if the strict markers were removed, this assertion catches it.
    """
    here = Path(__file__).parent
    w805_i = here / "test_w805_i_cmd_describe_empty_corpus.py"
    assert w805_i.exists(), f"W805-I test file missing at {w805_i}"
    src = w805_i.read_text(encoding="utf-8")
    # Count strict-xfail markers — must remain at 5 (the original pin set).
    strict_count = src.count("strict=True")
    assert strict_count == 5, (
        f"W805-I strict-xfail marker count drift: expected 5, got "
        f"{strict_count}. W607-K must NOT graduate any W805-I bug; the "
        f"empty-corpus state disclosure is a separate Pattern-2 contract "
        f"orthogonal to the W607-K DB-shape substrate-degrade axis."
    )
    # Names of the 5 xfail-strict tests — pin so a future rename without
    # graduation doesn't slip past.
    for test_name in (
        "test_empty_corpus_partial_success_set",
        "test_empty_corpus_explicit_state",
        "test_empty_corpus_verdict_discloses_empty",
        "test_no_source_corpus_unknown_project_partial_success",
        "test_agent_prompt_empty_corpus_partial_success_coupled_to_na",
    ):
        assert test_name in src, (
            f"W805-I xfail-strict test {test_name!r} was renamed or "
            f"removed without graduation. W607-K scope is the DB-shape "
            f"substrate-degrade axis only — empty-corpus silent-SAFE "
            f"is a separate Pattern-2 fix."
        )
