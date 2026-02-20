"""Tests for the roam capsule command."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    parse_json_output,
    assert_json_envelope,
    git_init,
    git_commit,
    index_in_process,
)


# ---------------------------------------------------------------------------
# Local invoke helper (capsule is not yet registered in cli.py)
# ---------------------------------------------------------------------------

def _invoke_capsule(runner, args=None, cwd=None, json_mode=False):
    """Invoke the capsule command directly (bypasses LazyGroup registration).

    The command is invoked as a standalone Click command so tests work even
    before 'capsule' is registered in cli.py's _COMMANDS dict.
    """
    from roam.commands.cmd_capsule import capsule

    full_args = []
    if json_mode:
        # Simulate the --json global flag by setting ctx.obj manually.
        # We do this by wrapping capsule in a minimal group context.
        pass  # handled via obj= below
    if args:
        full_args.extend(args)

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(
            capsule,
            full_args,
            obj={"json": json_mode},
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)

    return result


def _parse_capsule_json(result):
    """Parse JSON from a capsule CliRunner result."""
    assert result.exit_code == 0, (
        f"capsule command failed (exit {result.exit_code}):\n{result.output}"
    )
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"Invalid JSON from capsule: {e}\nOutput was:\n{result.output[:500]}"
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()


@pytest.fixture
def capsule_project(tmp_path, monkeypatch):
    """Small indexed project used by all capsule tests."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        'def hello():\n'
        '    return "world"\n\n'
        'def greet(name):\n'
        '    return hello() + name\n'
    )
    (proj / "utils.py").write_text(
        'def helper():\n'
        '    return 42\n'
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCapsuleCommand:

    def test_capsule_runs(self, capsule_project, cli_runner):
        """Command exits 0 in default text mode."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project)
        assert result.exit_code == 0, f"capsule failed:\n{result.output}"

    def test_capsule_json_envelope(self, capsule_project, cli_runner):
        """JSON output follows the standard roam envelope contract."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project, json_mode=True)
        data = _parse_capsule_json(result)
        assert_json_envelope(data, "capsule")

    def test_capsule_verdict_line(self, capsule_project, cli_runner):
        """Text output starts with a VERDICT: line."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project)
        assert result.exit_code == 0
        assert result.output.strip().startswith("VERDICT:"), (
            f"Expected output to start with VERDICT:, got:\n{result.output[:200]}"
        )

    def test_capsule_has_topology(self, capsule_project, cli_runner):
        """JSON capsule contains a topology section with expected keys."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project, json_mode=True)
        data = _parse_capsule_json(result)
        assert "topology" in data, f"Missing 'topology' key in: {list(data.keys())}"
        topo = data["topology"]
        assert "files" in topo, "topology missing 'files'"
        assert "symbols" in topo, "topology missing 'symbols'"
        assert "edges" in topo, "topology missing 'edges'"
        assert isinstance(topo["files"], int)
        assert isinstance(topo["symbols"], int)
        assert isinstance(topo["edges"], int)
        assert topo["files"] >= 1
        assert topo["symbols"] >= 1

    def test_capsule_has_symbols(self, capsule_project, cli_runner):
        """JSON capsule contains a symbols list with expected fields."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project, json_mode=True)
        data = _parse_capsule_json(result)
        assert "symbols" in data, f"Missing 'symbols' key in: {list(data.keys())}"
        symbols = data["symbols"]
        assert isinstance(symbols, list)
        assert len(symbols) >= 1, "Expected at least one symbol"

        # Validate shape of first symbol
        sym = symbols[0]
        assert "id" in sym, f"Symbol missing 'id': {sym}"
        assert "name" in sym, f"Symbol missing 'name': {sym}"
        assert "kind" in sym, f"Symbol missing 'kind': {sym}"
        assert "file" in sym, f"Symbol missing 'file': {sym}"
        assert "metrics" in sym, f"Symbol missing 'metrics': {sym}"

        m = sym["metrics"]
        assert "fan_in" in m, f"metrics missing 'fan_in': {m}"
        assert "fan_out" in m, f"metrics missing 'fan_out': {m}"

        # Verify we can find our fixture symbols by name
        names = {s["name"] for s in symbols}
        assert "hello" in names or "greet" in names or "helper" in names, (
            f"Expected hello/greet/helper in symbol names, got: {names}"
        )

    def test_capsule_has_edges(self, capsule_project, cli_runner):
        """JSON capsule contains an edges list (may be empty for tiny projects)."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project, json_mode=True)
        data = _parse_capsule_json(result)
        assert "edges" in data, f"Missing 'edges' key in: {list(data.keys())}"
        edges = data["edges"]
        assert isinstance(edges, list)

        # If there are edges, validate their shape
        if edges:
            edge = edges[0]
            assert "source" in edge, f"Edge missing 'source': {edge}"
            assert "target" in edge, f"Edge missing 'target': {edge}"
            assert "kind" in edge, f"Edge missing 'kind': {edge}"

    def test_capsule_has_health(self, capsule_project, cli_runner):
        """JSON capsule contains a health section with a score."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project, json_mode=True)
        data = _parse_capsule_json(result)
        assert "health" in data, f"Missing 'health' key in: {list(data.keys())}"
        health = data["health"]
        assert "score" in health, f"health missing 'score': {health}"
        assert isinstance(health["score"], (int, float))
        assert 0 <= health["score"] <= 100, (
            f"health score out of range: {health['score']}"
        )
        assert "cycles" in health, f"health missing 'cycles': {health}"
        assert "god_components" in health, f"health missing 'god_components': {health}"

    def test_capsule_output_file(self, capsule_project, cli_runner, tmp_path):
        """--output writes a valid JSON capsule to the specified file."""
        out_file = tmp_path / "capsule.json"
        result = _invoke_capsule(
            cli_runner,
            args=["--output", str(out_file)],
            cwd=capsule_project,
        )
        assert result.exit_code == 0, f"capsule --output failed:\n{result.output}"

        # File should exist and be valid JSON
        assert out_file.exists(), f"Output file not created: {out_file}"
        raw = out_file.read_text(encoding="utf-8")
        data = json.loads(raw)

        # Raw capsule JSON (not an envelope) should have a 'capsule' meta section
        assert "capsule" in data, (
            f"Missing 'capsule' meta in file output: {list(data.keys())}"
        )
        assert "topology" in data
        assert "symbols" in data
        assert "edges" in data
        assert "health" in data

        # Text summary should still print to stdout
        assert "VERDICT:" in result.output

    def test_capsule_redact_paths(self, capsule_project, cli_runner):
        """--redact-paths replaces real file paths with hashed components."""
        result = _invoke_capsule(
            cli_runner,
            args=["--redact-paths"],
            cwd=capsule_project,
            json_mode=True,
        )
        data = _parse_capsule_json(result)
        symbols = data.get("symbols", [])
        assert len(symbols) >= 1

        # No symbol should have a path containing a real filename
        real_names = {"app.py", "utils.py"}
        for sym in symbols:
            file_path = sym.get("file", "")
            path_parts = set(file_path.replace("\\", "/").split("/"))
            overlap = path_parts & real_names
            assert not overlap, (
                f"Real filename found in redacted path '{file_path}': {overlap}"
            )

        # The capsule meta should flag redaction
        capsule_meta = data.get("capsule", {})
        assert capsule_meta.get("redacted") is True

    def test_capsule_no_signatures(self, capsule_project, cli_runner):
        """--no-signatures omits the signature field from all symbol entries."""
        result = _invoke_capsule(
            cli_runner,
            args=["--no-signatures"],
            cwd=capsule_project,
            json_mode=True,
        )
        data = _parse_capsule_json(result)
        symbols = data.get("symbols", [])
        assert len(symbols) >= 1

        for sym in symbols:
            assert "signature" not in sym, (
                f"Found 'signature' in symbol despite --no-signatures: {sym['name']}"
            )

        # Meta flag should be set
        capsule_meta = data.get("capsule", {})
        assert capsule_meta.get("no_signatures") is True

    def test_capsule_no_function_bodies(self, capsule_project, cli_runner):
        """The capsule must never contain function body source text."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project, json_mode=True)
        data = _parse_capsule_json(result)

        # Known body text from our fixture files
        body_snippets = [
            'return "world"',
            'return hello() + name',
            'return 42',
        ]

        raw = json.dumps(data)
        for snippet in body_snippets:
            assert snippet not in raw, (
                f"Function body text found in capsule: {snippet!r}"
            )

    def test_capsule_has_clusters(self, capsule_project, cli_runner):
        """JSON capsule contains a clusters list (may be empty for tiny projects)."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project, json_mode=True)
        data = _parse_capsule_json(result)
        assert "clusters" in data, f"Missing 'clusters' key in: {list(data.keys())}"
        clusters = data["clusters"]
        assert isinstance(clusters, list)

        # If clusters exist, validate their shape
        if clusters:
            c = clusters[0]
            assert "id" in c, f"Cluster missing 'id': {c}"
            assert "size" in c, f"Cluster missing 'size': {c}"

    def test_capsule_capsule_meta_section(self, capsule_project, cli_runner):
        """JSON output contains the capsule meta section with version and timestamp."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project, json_mode=True)
        data = _parse_capsule_json(result)
        assert "capsule" in data, f"Missing 'capsule' meta key in: {list(data.keys())}"
        meta = data["capsule"]
        assert meta.get("version") == "1.0", (
            f"Expected version=1.0, got: {meta.get('version')}"
        )
        assert "generated" in meta, "capsule meta missing 'generated' timestamp"
        assert "tool_version" in meta, "capsule meta missing 'tool_version'"

    def test_capsule_summary_has_verdict(self, capsule_project, cli_runner):
        """The JSON envelope summary contains a verdict string."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project, json_mode=True)
        data = _parse_capsule_json(result)
        summary = data.get("summary", {})
        assert "verdict" in summary, f"summary missing 'verdict': {summary}"
        assert isinstance(summary["verdict"], str)
        assert len(summary["verdict"]) > 0

    def test_capsule_text_topology_section(self, capsule_project, cli_runner):
        """Text output includes a Topology section with numeric counts."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project)
        assert result.exit_code == 0
        output = result.output
        assert "Topology:" in output, f"Expected 'Topology:' in output:\n{output}"
        assert "Files:" in output
        assert "Symbols:" in output
        assert "Edges:" in output

    def test_capsule_text_health_section(self, capsule_project, cli_runner):
        """Text output includes a Health section."""
        result = _invoke_capsule(cli_runner, cwd=capsule_project)
        assert result.exit_code == 0
        output = result.output
        assert "Health:" in output, f"Expected 'Health:' in output:\n{output}"
        assert "Score:" in output
