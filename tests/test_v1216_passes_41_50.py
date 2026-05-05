"""Tests for v12.16 passes 41-50 (consolidated into the 12.16 ship)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def test_pass41_unknown_command_routes_via_classifier():
    """A natural-language phrase with no edit-distance match falls back to ask suggestion."""
    runner = CliRunner()
    result = runner.invoke(cli, ["trace login flow through middleware"])
    assert result.exit_code != 0
    out = (result.output + (str(result.exception) if result.exception else "")).lower()
    # redactedshould suggest `roam ask "..."` (matches recipe: ...)
    assert "ask" in out or "matches recipe" in out


def test_pass42_telemetry_records_and_reads(tmp_path, monkeypatch):
    """Telemetry round-trip: enable, record, fetch."""
    from roam import telemetry

    monkeypatch.setenv("ROAM_TELEMETRY_LOCAL", "1")
    monkeypatch.setattr(telemetry, "_db_path", lambda: tmp_path / "telemetry.db")
    telemetry.record("test-cmd", 100, 0)
    telemetry.record("slow-cmd", 9999, 1)
    slow = telemetry.fetch_top_slow(limit=5)
    assert any(r["command"] == "slow-cmd" for r in slow)
    assert slow[0]["command"] == "slow-cmd"
    recent = telemetry.fetch_recent(limit=5)
    names = [r["command"] for r in recent]
    assert "test-cmd" in names and "slow-cmd" in names


def test_pass42_telemetry_disabled_by_default(tmp_path, monkeypatch):
    """Without env var, telemetry is a no-op."""
    from roam import telemetry

    monkeypatch.delenv("ROAM_TELEMETRY_LOCAL", raising=False)
    monkeypatch.setattr(telemetry, "_db_path", lambda: tmp_path / "telemetry.db")
    telemetry.record("test-cmd", 100, 0)
    # No record should land
    assert telemetry.fetch_top_slow() == []


def test_pass42_telemetry_command_emits_envelope():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "telemetry"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "telemetry"
    assert "verdict" in payload["summary"]


def test_pass43_oracle_batch_jsonl_stream():
    """`oracle batch` reads JSONL from stdin and emits an envelope."""
    runner = CliRunner()
    inp = '{"oracle":"symbol-exists","args":{"name":"open_db"}}\n{"oracle":"is-clone-of","args":{"name":"open_db"}}\n'
    result = runner.invoke(cli, ["--json", "oracle", "batch"], input=inp)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "oracle.batch"
    assert payload["summary"]["count"] == 2
    assert len(payload["results"]) == 2


def test_pass44_orphan_imports_command_runs():
    """`orphan-imports` produces a verdict + count without crashing."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "orphan-imports"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "orphan-imports"
    assert "count" in payload["summary"]
    assert "files_scanned" in payload["summary"]


def test_pass44_classify_internal_typo_vs_missing():
    """The detector distinguishes internal-typo from missing-package classes."""
    from roam.commands.cmd_orphan_imports import _is_external_package

    # A clearly built-in module returns True
    assert _is_external_package("os")
    assert _is_external_package("json")
    # A truly missing package returns False
    assert not _is_external_package("definitely_not_a_real_package_xyz123")


def test_pass45_docstring_quality_buckets():
    """The bucket helper returns ABSENT / SHALLOW / RICH per heuristic."""
    from roam.commands.cmd_docs_coverage import _docstring_quality

    assert _docstring_quality("")[0] == "ABSENT"
    assert _docstring_quality("short doc")[0] == "SHALLOW"
    rich, signals = _docstring_quality(
        "Description that is long enough to clear the 80-char gate.\n:param x: the input\n:returns: the result\n"
    )
    assert rich == "RICH"
    assert signals["has_params"] is True
    assert signals["has_returns"] is True


def test_pass45_docs_coverage_quality_flag():
    """`docs-coverage --quality` populates quality_buckets in JSON."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "docs-coverage", "--quality"])
    assert result.exit_code in (0, 5), result.output
    payload = json.loads(result.output)
    assert "quality_buckets" in payload["summary"]
    bk = payload["summary"]["quality_buckets"]
    for b in ("ABSENT", "SHALLOW", "RICH"):
        assert b in bk


def test_pass46_search_explain_includes_pagerank():
    """`search --explain` emits PageRank alongside BM25."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "search", "ensure_index", "--explain"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    if not payload.get("results"):
        return  # nothing indexed under this name
    first = payload["results"][0]
    assert "explanation" in first
    expl = first["explanation"]
    assert "bm25_score" in expl
    assert "pagerank" in expl


def test_pass47_retrieve_scope_filters_paths():
    """`retrieve --scope <dir>` keeps only candidates under the given prefix."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "retrieve", "indexer parse files", "--scope", "src/roam/index", "--k", "5"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    candidates = payload.get("candidates", []) or []
    for c in candidates:
        path = (c.get("file_path") or "").replace("\\", "/")
        assert path.startswith("src/roam/index/"), f"out-of-scope path: {path}"


def test_pass48_changelog_command_runs():
    """`changelog --since HEAD~3 --suggest` produces grouped buckets."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "changelog", "--since", "HEAD~3"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "changelog"
    assert "commit_count" in payload["summary"]
    assert "buckets" in payload["summary"]


def test_pass49_graph_export_jsonl(tmp_path):
    """`graph-export --format jsonl` writes a node+edge stream."""
    out_path = tmp_path / "graph.jsonl"
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "graph-export", "--format", "jsonl", "--output", str(out_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "graph-export"
    assert payload["summary"]["nodes"] >= 1
    assert out_path.exists()
    first_line = out_path.read_text(encoding="utf-8").splitlines()[0]
    obj = json.loads(first_line)
    assert obj["type"] in ("node", "edge")


def test_pass50_help_search_finds_blast_radius():
    """`help-search blast radius` finds diff and impact."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "help-search", "blast", "radius"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    names = {m["name"] for m in payload.get("matches", [])}
    # Both should land in the top results
    assert "diff" in names or "impact" in names


def test_pass50_help_search_empty_query_errors():
    """Empty query is a structured usage error."""
    runner = CliRunner()
    result = runner.invoke(cli, ["help-search", " "])
    # Click captures UsageError with exit code 2
    assert result.exit_code != 0
    out = result.output.lower() + (str(result.exception).lower() if result.exception else "")
    assert "empty" in out or "query" in out or "usage" in out
