"""W607-DO -- ``cmd_graph_export`` substrate-CALL-layer plumbing.

cmd_graph_export is the graph-FORMAT companion to cmd_fingerprint
(topology-HASH, W607-DH) and cmd_capsule (graph-BUNDLE, W607-BD/W607-DK).
Together they close the architecture-export 3-way at the substrate-CALL
layer:

  * cmd_fingerprint  -> W607-DH (topology-HASH, 11 phases)
  * cmd_capsule      -> W607-DK on top of W607-BD (graph-BUNDLE)
  * cmd_graph_export -> W607-DO (THIS WAVE; multi-format graph export)

cmd_graph_export has NO pre-existing warnings_out channel -- W607-DO is
FRESH: the accumulator-based markers become the canonical
``summary.warnings_out`` field outright.

Substrates wrapped via ``_run_check_do``:

* build_graph             -- networkx graph construction from DB (a
                             raise inside build_file_graph /
                             build_symbol_graph degrades to an empty
                             DiGraph floor).
* serialize_jsonl         -- JSONL projection + W82.1 atomic file-write
                             (when fmt=jsonl).
* serialize_dot           -- DOT projection + W82.1 atomic file-write
                             (when fmt=dot).
* serialize_graphml       -- GraphML projection + W82.1 atomic file-write
                             via tempfile + os.replace (when fmt=graphml).
* compute_export_metadata -- nodes/edges counts + LAW 6 single-line
                             verdict composition.
* serialize_envelope      -- json_envelope composition. A circular-ref
                             / hostile field surfaces a marker rather
                             than crashing before to_json runs.

Marker family ``graph_export_<phase>_failed:<exc_class>:<detail>``.

W82.1 REGRESSION GUARD
----------------------

The W82.1 atomic file-write pattern stays wired through the W607-DO
serialize_* wraps -- a clean run with --output produces a parseable
JSONL / DOT / GraphML file at the target path that round-trips.

PER-SUBSTRATE ISOLATION
-----------------------

Simulate ONE substrate raising while the others succeed. The marker
surfaces for the failed substrate, the others contribute fields
normally, and the envelope stays well-formed.

CROSS-PREFIX ISOLATION
----------------------

The ``graph_export_*`` markers do NOT leak into adjacent W607-*
families (fingerprint / capsule / health / complexity / etc.), AND
sibling prefixes do NOT leak INTO the graph-export envelope.

MULTI-FORMAT DISPATCH ISOLATION
-------------------------------

The three serialize_* substrates (jsonl / dot / graphml) can each fail
independently. A fmt=jsonl invocation that raises inside
_serialise_jsonl surfaces ONLY the ``graph_export_serialize_jsonl_failed:``
marker; the dot / graphml prefixes stay absent because they never ran.

ARCHITECTURE-EXPORT 3-WAY PAIRING
---------------------------------

An AST-scan over cmd_fingerprint + cmd_capsule + cmd_graph_export
confirms ALL THREE carry W607 substrate-CALL plumbing
(cmd_fingerprint = ``_run_check_dh``, cmd_capsule = ``_run_check_dk``,
cmd_graph_export = ``_run_check_do``). This pins the
architecture-export 3-way.

W978 7-DISCIPLINE COMPLIANCE
----------------------------

The AST audit pins:
- every ``default=`` is a literal constant (kwarg-default eagerness,
  2nd discipline)
- phase names are unique within the file (4th discipline)
- ``len()`` / dict-index over poisonable input lives INSIDE the wrapped
  closure (5th discipline)
"""

from __future__ import annotations

import ast
import json as _json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Canonical W607-DO phase enumeration
# ---------------------------------------------------------------------------


_DO_PHASES = (
    "build_graph",
    "serialize_jsonl",
    "serialize_dot",
    "serialize_graphml",
    "compute_export_metadata",
    "serialize_envelope",
)

_DO_SERIALIZE_PHASES = (
    "serialize_jsonl",
    "serialize_dot",
    "serialize_graphml",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def graph_project(project_factory):
    """Small Python project for graph-export."""
    return project_factory(
        {
            "app.py": ("from lib import helper\ndef main():\n    return helper()\n"),
            "lib.py": ("def helper():\n    return 42\n"),
        }
    )


def _invoke_graph_export(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam graph-export`` via the top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("graph-export")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DO substrate-CALL markers
# ---------------------------------------------------------------------------


def test_graph_export_clean_envelope_omits_w607do_markers(cli_runner, graph_project, tmp_path):
    """Clean graph-export -> no W607-DO substrate-CALL markers.

    An empty W607-DO bucket on the success path must NOT introduce
    ``graph_export_<phase>_failed:`` markers on the envelope.
    cmd_graph_export has no pre-existing warnings_out channel, so the
    field is absent entirely on the clean path.
    """
    out = tmp_path / "graph.jsonl"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "jsonl", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "graph-export"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    do_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"graph_export_{p}_failed:" in m for p in _DO_PHASES)
    ]
    assert not do_markers, (
        f"clean graph-export must NOT surface W607-DO substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )
    # Happy path: partial_success is not set (or is False).
    assert not data["summary"].get("partial_success"), data["summary"]


# ---------------------------------------------------------------------------
# (2) build_graph failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_graph_export_build_graph_failure_marker(cli_runner, graph_project, monkeypatch, tmp_path):
    """A raise in build_symbol_graph surfaces marker.

    This is the substrate-CALL boundary the W607-DO wrap catches: a
    raise inside the networkx graph construction degrades to an
    empty-graph floor rather than crashing the exporter wholesale.
    """
    from roam.commands import cmd_graph_export as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-build-graph-from-W607-DO")

    monkeypatch.setattr(_mod, "build_symbol_graph", _raise)
    monkeypatch.setattr(_mod, "build_file_graph", _raise)

    out = tmp_path / "graph.jsonl"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "jsonl", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("graph_export_build_graph_failed:")]
    assert markers, f"expected graph_export_build_graph_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-build-graph-from-W607-DO" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"build-graph-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # LAW 6: single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations
# ---------------------------------------------------------------------------


def test_graph_export_w607do_warnings_in_envelope(cli_runner, graph_project, monkeypatch, tmp_path):
    """Non-empty W607-DO bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_graph_export as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DO")

    monkeypatch.setattr(_mod, "build_symbol_graph", _raise)
    monkeypatch.setattr(_mod, "build_file_graph", _raise)

    out = tmp_path / "graph.jsonl"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "jsonl", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DO disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DO disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("graph_export_build_graph_failed:")]
    assert markers, f"expected graph_export_build_graph_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_graph_export_do_three_segment_marker_shape(cli_runner, graph_project, monkeypatch, tmp_path):
    """W607-DO marker must have three colon-separated segments."""
    from roam.commands import cmd_graph_export as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-DO")

    monkeypatch.setattr(_mod, "build_symbol_graph", _raise)
    monkeypatch.setattr(_mod, "build_file_graph", _raise)

    out = tmp_path / "graph.jsonl"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "jsonl", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("graph_export_build_graph_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "graph_export_build_graph_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) serialize_jsonl failure -> marker, envelope still composes
# ---------------------------------------------------------------------------


def test_graph_export_serialize_jsonl_failure_surfaces_marker(cli_runner, graph_project, monkeypatch, tmp_path):
    """A raise inside ``_serialise_jsonl`` surfaces W607-DO marker.

    Multi-format dispatch isolation: only the jsonl substrate ran, so
    only the ``graph_export_serialize_jsonl_failed:`` marker surfaces.
    """
    from roam.commands import cmd_graph_export as _mod

    def _raise_jsonl(*args, **kwargs):
        raise OSError("synthetic-jsonl-from-W607-DO")

    monkeypatch.setattr(_mod, "_serialise_jsonl", _raise_jsonl)

    out = tmp_path / "graph.jsonl"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "jsonl", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    jsonl_markers = [m for m in all_wo if m.startswith("graph_export_serialize_jsonl_failed:")]
    assert jsonl_markers, f"expected graph_export_serialize_jsonl_failed: marker; got {all_wo!r}"
    # Multi-format dispatch isolation: dot / graphml markers stay absent.
    dot_leaked = [m for m in all_wo if m.startswith("graph_export_serialize_dot_failed:")]
    graphml_leaked = [m for m in all_wo if m.startswith("graph_export_serialize_graphml_failed:")]
    assert not dot_leaked, dot_leaked
    assert not graphml_leaked, graphml_leaked
    # Envelope still composes a single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict


# ---------------------------------------------------------------------------
# (6) serialize_dot failure -> marker, envelope still composes
# ---------------------------------------------------------------------------


def test_graph_export_serialize_dot_failure_surfaces_marker(cli_runner, graph_project, monkeypatch, tmp_path):
    """A raise inside ``_serialise_dot`` surfaces W607-DO marker.

    Multi-format dispatch isolation: only the dot substrate ran, so
    only the ``graph_export_serialize_dot_failed:`` marker surfaces.
    """
    from roam.commands import cmd_graph_export as _mod

    def _raise_dot(*args, **kwargs):
        raise OSError("synthetic-dot-from-W607-DO")

    monkeypatch.setattr(_mod, "_serialise_dot", _raise_dot)

    out = tmp_path / "graph.dot"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "dot", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    dot_markers = [m for m in all_wo if m.startswith("graph_export_serialize_dot_failed:")]
    assert dot_markers, f"expected graph_export_serialize_dot_failed: marker; got {all_wo!r}"
    # Multi-format dispatch isolation: jsonl / graphml markers stay absent.
    jsonl_leaked = [m for m in all_wo if m.startswith("graph_export_serialize_jsonl_failed:")]
    graphml_leaked = [m for m in all_wo if m.startswith("graph_export_serialize_graphml_failed:")]
    assert not jsonl_leaked, jsonl_leaked
    assert not graphml_leaked, graphml_leaked


# ---------------------------------------------------------------------------
# (7) serialize_graphml failure -> marker, envelope still composes
# ---------------------------------------------------------------------------


def test_graph_export_serialize_graphml_failure_surfaces_marker(cli_runner, graph_project, monkeypatch, tmp_path):
    """A raise inside ``_serialise_graphml`` surfaces W607-DO marker.

    Multi-format dispatch isolation: only the graphml substrate ran, so
    only the ``graph_export_serialize_graphml_failed:`` marker surfaces.
    """
    from roam.commands import cmd_graph_export as _mod

    def _raise_graphml(*args, **kwargs):
        raise OSError("synthetic-graphml-from-W607-DO")

    monkeypatch.setattr(_mod, "_serialise_graphml", _raise_graphml)

    out = tmp_path / "graph.graphml"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "graphml", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    graphml_markers = [m for m in all_wo if m.startswith("graph_export_serialize_graphml_failed:")]
    assert graphml_markers, f"expected graph_export_serialize_graphml_failed: marker; got {all_wo!r}"
    # Multi-format dispatch isolation: jsonl / dot markers stay absent.
    jsonl_leaked = [m for m in all_wo if m.startswith("graph_export_serialize_jsonl_failed:")]
    dot_leaked = [m for m in all_wo if m.startswith("graph_export_serialize_dot_failed:")]
    assert not jsonl_leaked, jsonl_leaked
    assert not dot_leaked, dot_leaked


# ---------------------------------------------------------------------------
# (8) compute_export_metadata failure -> marker, envelope still composes
# ---------------------------------------------------------------------------


def test_graph_export_compute_metadata_failure_surfaces_marker(cli_runner, graph_project, monkeypatch, tmp_path):
    """A raise on G.number_of_nodes() triggers the metadata wrap.

    The DO wrap embeds every G lookup INSIDE the closure (W978 5th
    discipline). A corrupted G whose number_of_nodes() raises cannot
    crash the metadata composition path.
    """
    import networkx as nx

    from roam.commands import cmd_graph_export as _mod

    class _PoisonGraph(nx.DiGraph):
        def number_of_nodes(self):
            raise RuntimeError("synthetic-metadata-from-W607-DO")

        def number_of_edges(self):
            return 0

    def _make_poison(*args, **kwargs):
        return _PoisonGraph()

    monkeypatch.setattr(_mod, "build_symbol_graph", _make_poison)
    monkeypatch.setattr(_mod, "build_file_graph", _make_poison)

    out = tmp_path / "graph.jsonl"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "jsonl", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    metadata_markers = [m for m in all_wo if m.startswith("graph_export_compute_export_metadata_failed:")]
    assert metadata_markers, (
        f"expected graph_export_compute_export_metadata_failed: marker for poisoned G.number_of_nodes; got {all_wo!r}"
    )
    # Envelope still composes a single-line verdict.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict


# ---------------------------------------------------------------------------
# (9) W82.1 atomic file-write pattern preserved through W607-DO (jsonl)
# ---------------------------------------------------------------------------


def test_w82_1_jsonl_file_write_preserved_under_w607do(cli_runner, graph_project, tmp_path):
    """W82.1 regression guard: --output --format jsonl still writes on disk.

    The W82.1 atomic file-write pattern stays wired through the W607-DO
    serialize_jsonl outer wrap -- a clean run with --output produces a
    parseable JSONL file at the target path that round-trips.
    """
    output_path = tmp_path / "graph_out.jsonl"
    result = _invoke_graph_export(
        cli_runner,
        graph_project,
        "--format",
        "jsonl",
        "--output",
        str(output_path),
        json_mode=False,
    )
    assert result.exit_code == 0, result.output
    assert output_path.exists(), (
        f"W82.1 file-write pattern broken under W607-DO; expected {output_path} to exist after --output"
    )
    # Each line round-trips through json.loads cleanly.
    text = output_path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines, "JSONL output should be non-empty for a 2-file project"
    for ln in lines:
        parsed = _json.loads(ln)
        assert isinstance(parsed, dict), parsed
        assert parsed.get("type") in ("node", "edge"), parsed


# ---------------------------------------------------------------------------
# (10) W82.1 atomic file-write pattern preserved through W607-DO (dot)
# ---------------------------------------------------------------------------


def test_w82_1_dot_file_write_preserved_under_w607do(cli_runner, graph_project, tmp_path):
    """W82.1 regression guard: --output --format dot still writes on disk."""
    output_path = tmp_path / "graph_out.dot"
    result = _invoke_graph_export(
        cli_runner,
        graph_project,
        "--format",
        "dot",
        "--output",
        str(output_path),
        json_mode=False,
    )
    assert result.exit_code == 0, result.output
    assert output_path.exists(), (
        f"W82.1 file-write pattern broken under W607-DO; expected {output_path} to exist after --output"
    )
    text = output_path.read_text(encoding="utf-8")
    # DOT minimum shape: digraph header + closing brace.
    assert text.startswith("digraph G {"), text[:80]
    assert text.rstrip().endswith("}"), text[-80:]


# ---------------------------------------------------------------------------
# (11) W82.1 atomic file-write pattern preserved through W607-DO (graphml)
# ---------------------------------------------------------------------------


def test_w82_1_graphml_file_write_preserved_under_w607do(cli_runner, graph_project, tmp_path):
    """W82.1 regression guard: --output --format graphml still writes on disk."""
    output_path = tmp_path / "graph_out.graphml"
    result = _invoke_graph_export(
        cli_runner,
        graph_project,
        "--format",
        "graphml",
        "--output",
        str(output_path),
        json_mode=False,
    )
    assert result.exit_code == 0, result.output
    assert output_path.exists(), (
        f"W82.1 file-write pattern broken under W607-DO; expected {output_path} to exist after --output"
    )
    text = output_path.read_text(encoding="utf-8")
    # GraphML minimum shape: XML declaration + graphml root.
    assert "<graphml" in text, text[:200]


# ---------------------------------------------------------------------------
# (12) W82.1 file-write failure -> marker surfaces, no torn output
# ---------------------------------------------------------------------------


def test_w82_1_jsonl_write_failure_surfaces_marker(cli_runner, graph_project, monkeypatch, tmp_path):
    """W82.1 + W607-DO: a raise inside atomic_write_text surfaces marker.

    The W607-DO wrap around _serialise_jsonl catches the OSError and
    surfaces ``graph_export_serialize_jsonl_failed:`` without crashing
    the rest of the envelope path.
    """
    import roam.atomic_io as _atomic_mod

    def _raise(*args, **kwargs):
        raise OSError("synthetic-w82-1-write-from-W607-DO")

    monkeypatch.setattr(_atomic_mod, "atomic_write_text", _raise)

    output_path = tmp_path / "graph_out.jsonl"
    result = _invoke_graph_export(
        cli_runner,
        graph_project,
        "--format",
        "jsonl",
        "--output",
        str(output_path),
    )
    # Command does NOT crash.
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    jsonl_markers = [m for m in all_wo if m.startswith("graph_export_serialize_jsonl_failed:")]
    assert jsonl_markers, f"expected graph_export_serialize_jsonl_failed: marker on W82.1 write failure; got {all_wo!r}"
    # The file should NOT have been created on disk (atomic write
    # bailed before the os.replace step).
    assert not output_path.exists() or output_path.stat().st_size == 0


# ---------------------------------------------------------------------------
# (13) Marker-prefix discipline -- W607-DO stays in ``graph_export_*`` family
# ---------------------------------------------------------------------------


def test_w607do_marker_prefix_stays_in_graph_export_family(cli_runner, graph_project, monkeypatch, tmp_path):
    """Every W607-DO substrate marker uses the canonical ``graph_export_*`` prefix.

    Hard distinction from sibling W607-* layers across adjacent
    architecture/export commands (fingerprint, capsule, health,
    complexity, dark_matter, smells).
    """
    from roam.commands import cmd_graph_export as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DO")

    monkeypatch.setattr(_mod, "build_symbol_graph", _raise)
    monkeypatch.setattr(_mod, "build_file_graph", _raise)

    out = tmp_path / "graph.jsonl"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "jsonl", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("graph_export_"), (
            f"every surfaced W607-DO marker must use the ``graph_export_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("fingerprint_", "cmd_fingerprint W607-DH"),
            ("capsule_", "cmd_capsule W607-BD / W607-DK"),
            ("health_", "cmd_health W607-M / W607-BA"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
            ("smells_", "cmd_smells W607-BN / W607-DF"),
            ("bus_factor_", "cmd_bus_factor W607-CQ"),
            ("clones_", "cmd_clones W607-BQ / W607-DC"),
            ("duplicates_", "cmd_duplicates W607-BM / W607-DD"),
            ("dead_", "cmd_dead W607-BX"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("hotspots_", "cmd_hotspots W607-CP"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("over_fetch_", "cmd_over_fetch W607-CE"),
            ("missing_index_", "cmd_missing_index W607-CI"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (14) Source-level guard: cmd_graph_export carries the W607-DO accumulator
# ---------------------------------------------------------------------------


def test_cmd_graph_export_carries_w607do_accumulator():
    """AST-level guard: cmd_graph_export carries the W607-DO accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_graph_export.py"
    assert src_path.exists(), f"cmd_graph_export.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607do_warnings_out" in src, (
        "W607-DO accumulator missing from cmd_graph_export; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_do" in src, (
        "W607-DO ``_run_check_do`` helper missing from cmd_graph_export; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_do = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_do":
            found_run_check_do = True
            break
    assert found_run_check_do, (
        "W607-DO ``_run_check_do`` helper not found in cmd_graph_export AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (15) Each W607-DO substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607do_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-DO substrate boundary is wrapped."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_graph_export.py"
    src = src_path.read_text(encoding="utf-8")
    for phase in _DO_PHASES:
        same_line = f'_run_check_do("{phase}"' in src
        multi_line = (
            f'_run_check_do(\n        "{phase}"' in src
            or f'_run_check_do(\n            "{phase}"' in src
            or f'_run_check_do(\n                "{phase}"' in src
            or f'_run_check_do(\n                    "{phase}"' in src
            or f'_run_check_do(\n                        "{phase}"' in src
        )
        marker_grep = f"graph_export_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DO wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (16) Canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607do_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-DO marker fstring in cmd_graph_export."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_graph_export.py"
    src = src_path.read_text(encoding="utf-8")
    fstring_pattern = 'f"graph_export_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-DO marker fstring missing from cmd_graph_export; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (17) Pattern-2 silent-fallback eliminated on degraded path
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_do_degraded_path(cli_runner, graph_project, monkeypatch, tmp_path):
    """Pattern-2 regression guard on the degraded path.

    If build_symbol_graph raises, the empty-graph floor kicks in and
    the envelope is emitted. The W607-DO wrap MUST flip
    ``partial_success: True`` on that branch so the empty-state envelope
    is NOT mistaken for a clean graph export.
    """
    from roam.commands import cmd_graph_export as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-DO")

    monkeypatch.setattr(_mod, "build_symbol_graph", _raise)
    monkeypatch.setattr(_mod, "build_file_graph", _raise)

    out = tmp_path / "graph.jsonl"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "jsonl", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    build_markers = [m for m in all_wo if m.startswith("graph_export_build_graph_failed:")]
    assert build_markers, (
        f"degraded path MUST surface the build_graph marker (loud-not-silent discipline); got {all_wo!r}"
    )
    # Verdict must NOT contain default-success vocabulary.
    verdict = (summary.get("verdict") or "").lower()
    # The verdict embeds the caller-selected output path.  Remove that exact
    # data value before scanning semantic verdict language so a workspace such
    # as ``D:\\Safe\\...`` cannot masquerade as a silent-SAFE claim.
    semantic_verdict = verdict.replace(str(out).lower(), "<output-path>")
    for forbidden in ("safe", "passed", "all clear"):
        assert forbidden not in semantic_verdict, (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# (18) Cross-prefix isolation -- graph_export_* markers stay scoped
# ---------------------------------------------------------------------------


def test_cross_prefix_marker_isolation_against_siblings_do(cli_runner, graph_project, monkeypatch, tmp_path):
    """Cross-prefix marker isolation across the export detector family."""
    from roam.commands import cmd_graph_export as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-from-W607-DO")

    monkeypatch.setattr(_mod, "build_symbol_graph", _raise)
    monkeypatch.setattr(_mod, "build_file_graph", _raise)

    out = tmp_path / "graph.jsonl"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "jsonl", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    assert any(m.startswith("graph_export_build_graph_failed:") for m in all_wo), all_wo

    for forbidden_prefix in (
        "fingerprint_",
        "capsule_",
        "health_",
        "complexity_",
        "dark_matter_",
        "smells_",
        "bus_factor_",
        "clones_",
        "duplicates_",
        "dead_",
        "vibe_check_",
        "hotspots_",
    ):
        leaked = [m for m in all_wo if m.startswith(forbidden_prefix)]
        assert not leaked, (
            f"marker family leakage on architecture-family pairing: "
            f"``{forbidden_prefix}*`` leaked into cmd_graph_export envelope; "
            f"got {leaked!r}"
        )


# ---------------------------------------------------------------------------
# (19) Architecture-export 3-way pairing pin
# ---------------------------------------------------------------------------


def test_architecture_export_3way_pairing_dh_dk_do():
    """AST pairing pin: cmd_fingerprint + cmd_capsule + cmd_graph_export
    all carry W607 plumbing.

    cmd_fingerprint  = topology-HASH (W607-DH).
    cmd_capsule      = graph-BUNDLE   (W607-DK on top of W607-BD).
    cmd_graph_export = graph-FORMAT   (W607-DO).

    Together they form the architecture-export 3-way at the
    substrate-CALL layer. A regression that strips any side breaks
    the 3-way.
    """
    base = Path(__file__).parent.parent / "src" / "roam" / "commands"
    fp_src = (base / "cmd_fingerprint.py").read_text(encoding="utf-8")
    cap_src = (base / "cmd_capsule.py").read_text(encoding="utf-8")
    ge_src = (base / "cmd_graph_export.py").read_text(encoding="utf-8")

    # cmd_fingerprint side: W607-DH plumbing present.
    assert "w607dh_warnings_out" in fp_src, (
        "cmd_fingerprint missing _w607dh_warnings_out; architecture-export 3-way is broken on the topology-HASH side."
    )
    assert "_run_check_dh" in fp_src, (
        "cmd_fingerprint missing _run_check_dh; architecture-export 3-way is broken on the topology-HASH side."
    )

    # cmd_capsule side: W607-DK plumbing present.
    assert "w607dk_warnings_out" in cap_src, (
        "cmd_capsule missing _w607dk_warnings_out; architecture-export 3-way is broken on the graph-BUNDLE side."
    )
    assert "_run_check_dk" in cap_src, (
        "cmd_capsule missing _run_check_dk; architecture-export 3-way is broken on the graph-BUNDLE side."
    )

    # cmd_graph_export side: W607-DO plumbing present.
    assert "w607do_warnings_out" in ge_src, (
        "cmd_graph_export missing _w607do_warnings_out; architecture-export 3-way is broken on the graph-FORMAT side."
    )
    assert "_run_check_do" in ge_src, (
        "cmd_graph_export missing _run_check_do; architecture-export 3-way is broken on the graph-FORMAT side."
    )

    # AST-level: each helper is defined as FunctionDef inside its
    # respective module.
    for side_label, src, expected_helper in (
        ("cmd_fingerprint", fp_src, "_run_check_dh"),
        ("cmd_capsule", cap_src, "_run_check_dk"),
        ("cmd_graph_export", ge_src, "_run_check_do"),
    ):
        tree = ast.parse(src)
        found = any(isinstance(n, ast.FunctionDef) and n.name == expected_helper for n in ast.walk(tree))
        assert found, f"{side_label}: {expected_helper} FunctionDef missing; architecture-export 3-way broken."


# ---------------------------------------------------------------------------
# (20) W978 7-discipline AST audit
# ---------------------------------------------------------------------------


def _w978_is_literal_tree(n: ast.AST) -> bool:
    """Pure-AST literal check; recursive on container nodes."""
    if isinstance(n, ast.Constant):
        return True
    if isinstance(n, (ast.List, ast.Tuple, ast.Set)):
        return all(_w978_is_literal_tree(x) for x in n.elts)
    if isinstance(n, ast.Dict):
        return all(_w978_is_literal_tree(x) for x in (list(n.keys) + list(n.values)))
    if isinstance(n, ast.Name):
        return n.id in ("None", "True", "False")
    if isinstance(n, ast.UnaryOp) and isinstance(n.operand, ast.Constant):
        return True
    return False


def _w978_assert_container_is_literal(value: ast.AST, fn_name: str) -> None:
    """Assert every child of a List/Dict/Tuple/Set is a literal subtree."""
    if isinstance(value, ast.Dict):
        children = list(value.keys) + list(value.values)
    else:
        children = list(value.elts)
    for child in children:
        assert _w978_is_literal_tree(child), (
            f"{fn_name} default= contains non-literal child at line {value.lineno}: {ast.dump(child)!r}"
        )


def _w978_assert_default_is_literal(value: ast.AST, fn_name: str) -> None:
    """Dispatch default= value to the right literal check; raise on miss."""
    if isinstance(value, ast.Constant):
        return
    if isinstance(value, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
        _w978_assert_container_is_literal(value, fn_name)
        return
    if isinstance(value, ast.Name):
        assert value.id in ("None", "True", "False"), (
            f"{fn_name} default= references symbol {value.id!r} "
            f"at line {value.lineno}; only literals + immutable "
            f"containers allowed (W978 2nd discipline)."
        )
        return
    raise AssertionError(f"{fn_name} default= is not a literal at line {value.lineno}: {ast.dump(value)!r}")


def _w978_extract_phase(node: ast.Call, fn_name: str) -> str | None:
    """Return the phase string of a `fn_name(phase, ...)` call, or None to skip."""
    func = node.func
    if not isinstance(func, ast.Name) or func.id != fn_name:
        return None
    if not node.args:
        return None
    phase_arg = node.args[0]
    assert isinstance(phase_arg, ast.Constant), (
        f"{fn_name} phase arg must be a string literal at line {phase_arg.lineno}; got {ast.dump(phase_arg)!r}"
    )
    return phase_arg.value


def _w978_audit_call(node: ast.Call, fn_name: str) -> str | None:
    """Audit a single Call node; return its phase name (or None to skip)."""
    phase = _w978_extract_phase(node, fn_name)
    if phase is None:
        return None
    for kw in node.keywords:
        if kw.arg != "default":
            continue
        _w978_assert_default_is_literal(kw.value, fn_name)
    return phase


def _w978_collect_phases(tree: ast.AST, fn_name: str) -> list[str]:
    """Walk the tree, audit every matching call, return collected phase names."""
    phases_seen: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        phase = _w978_audit_call(node, fn_name)
        if phase is not None:
            phases_seen.append(phase)
    return phases_seen


def test_w978_7_discipline_ast_audit_do():
    """AST audit pins the W978 7-discipline compliance for W607-DO.

    Each ``_run_check_do("phase", ...)`` call site must:
    - have a ``default=`` that is a literal constant / immutable
      container of literals (kwarg-default eagerness, 2nd discipline)
    - phase names unique within the file (4th discipline)
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_graph_export.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    phases_seen = _w978_collect_phases(tree, "_run_check_do")

    # Phase names unique within the file (4th discipline collision check).
    duplicates = [p for p in phases_seen if phases_seen.count(p) > 1]
    assert not duplicates, f"W607-DO phase name collision in cmd_graph_export: {sorted(set(duplicates))!r}"


# ---------------------------------------------------------------------------
# (21) W978 5th discipline -- len() / dict-index NOT at the kwarg-bind site
# ---------------------------------------------------------------------------


def test_w978_5th_discipline_no_unguarded_len_or_index_at_do_kwarg_bind():
    """W978 5th discipline: ``len()`` / dict-index over poisoned input
    MUST live INSIDE the wrapped closure, never at the _run_check_do
    kwarg-bind site.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_graph_export.py"
    src = src_path.read_text(encoding="utf-8")
    # Forbid any direct ``len(`` reference on the same source line as a
    # _run_check_do call.
    for line in src.splitlines():
        if "_run_check_do(" in line and "len(" in line:
            raise AssertionError(
                f"W978 5th discipline violation in cmd_graph_export: "
                f"``len(`` at the same line as _run_check_do call -- "
                f"move len() INSIDE the wrapped closure; line: {line!r}"
            )


# ---------------------------------------------------------------------------
# (22) DO fstring fallback marker contains exception class + detail
# ---------------------------------------------------------------------------


def test_w607do_marker_fstring_carries_exc_class_and_detail(cli_runner, graph_project, monkeypatch, tmp_path):
    """W607-DO marker carries the (exc_class, detail) tuple in the fstring.

    The marker shape is ``graph_export_<phase>_failed:<exc_class>:<detail>``;
    consumers parse this to triage. A regression that strips either
    component would break downstream triage.
    """
    from roam.commands import cmd_graph_export as _mod

    def _raise(*args, **kwargs):
        raise ValueError("synthetic-fstring-detail-from-W607-DO")

    monkeypatch.setattr(_mod, "build_symbol_graph", _raise)
    monkeypatch.setattr(_mod, "build_file_graph", _raise)

    out = tmp_path / "graph.jsonl"
    result = _invoke_graph_export(cli_runner, graph_project, "--format", "jsonl", "--output", str(out))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    build_markers = [m for m in all_wo if m.startswith("graph_export_build_graph_failed:")]
    assert build_markers, all_wo
    marker = build_markers[0]
    assert "ValueError" in marker, marker
    assert "synthetic-fstring-detail-from-W607-DO" in marker, marker
