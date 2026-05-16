"""Tests for the world-model causal-graph detector (R28 sub-feature 3 / W15.3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

# ---------------------------------------------------------------------------
# A. param_to_effect — `def write_log(path): open(path, 'w').write('x')`
# ---------------------------------------------------------------------------


def test_param_to_effect_high_confidence(project_factory, monkeypatch):
    """Param appearing in side-effect call args → high-confidence param_to_effect."""
    proj = project_factory(
        {
            "src/writer.py": ("def write_log(path):\n    with open(path, 'w') as f:\n        f.write('x')\n"),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.causal_graph import classify_causal_graph

    with open_db(readonly=True) as conn:
        results = classify_causal_graph(conn, symbol_name="write_log")

    assert results, "Expected to classify write_log"
    g = results[0]
    p2e_edges = [e for e in g.edges if e.kind == "param_to_effect"]
    assert p2e_edges, f"Expected param_to_effect edges, got {[e.kind for e in g.edges]}"
    matching = [e for e in p2e_edges if e.source == "param:path"]
    assert matching, f"Expected source=param:path, edges were {[(e.source, e.sink) for e in p2e_edges]}"
    # At least one edge should be high-confidence (param appears inside open() args)
    assert any(e.confidence == "high" for e in matching), (
        f"Expected at least one high-confidence edge, got {[e.confidence for e in matching]}"
    )
    # sink should mention io_read or io_write (open() defaults to io_read, mode='w' → io_write)
    assert any("io_" in e.sink for e in matching), f"Sinks: {[e.sink for e in matching]}"


# ---------------------------------------------------------------------------
# B. param_to_return — `def echo(x): return x`
# ---------------------------------------------------------------------------


def test_param_to_return(project_factory, monkeypatch):
    """Param appearing in return expression → param_to_return."""
    proj = project_factory(
        {
            "src/echo.py": ("def echo(x):\n    return x\n"),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.causal_graph import classify_causal_graph

    with open_db(readonly=True) as conn:
        results = classify_causal_graph(conn, symbol_name="echo")

    assert results
    g = results[0]
    p2r = [e for e in g.edges if e.kind == "param_to_return"]
    assert p2r, f"Expected param_to_return, got {[e.kind for e in g.edges]}"
    assert any(e.source == "param:x" and e.sink == "return" for e in p2r), (
        f"Edges were {[(e.source, e.sink) for e in p2r]}"
    )


# ---------------------------------------------------------------------------
# C. global_to_effect — `LOG = open(...); def log(): LOG.write('x')`
# ---------------------------------------------------------------------------


def test_global_to_effect(project_factory, monkeypatch):
    """Global identifier flowing into a side-effecting call → global_to_effect."""
    proj = project_factory(
        {
            "src/logger.py": (
                "import subprocess\nLOG = open('out.log', 'w')\ndef log_event():\n    subprocess.run(['echo', LOG])\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.causal_graph import classify_causal_graph

    with open_db(readonly=True) as conn:
        results = classify_causal_graph(conn, symbol_name="log_event")

    assert results
    g = results[0]
    g2e = [e for e in g.edges if e.kind == "global_to_effect"]
    assert g2e, f"Expected global_to_effect edges, got {[e.kind for e in g.edges]}, inputs={g.inputs}"
    assert any(e.source == "global:LOG" for e in g2e), f"Sources: {[e.source for e in g2e]}"


# ---------------------------------------------------------------------------
# D. env_to_effect — `def f(): subprocess.run(os.environ.get('CMD'))`
# ---------------------------------------------------------------------------


def test_env_to_effect(project_factory, monkeypatch):
    """env read followed by side-effect → env_to_effect edge."""
    proj = project_factory(
        {
            "src/runner.py": (
                "import os\nimport subprocess\ndef run():\n    cmd = os.environ.get('CMD')\n    subprocess.run([cmd])\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.causal_graph import classify_causal_graph

    with open_db(readonly=True) as conn:
        results = classify_causal_graph(conn, symbol_name="run")

    assert results
    g = results[0]
    e2e = [e for e in g.edges if e.kind == "env_to_effect"]
    assert e2e, f"Expected env_to_effect edges, got {[e.kind for e in g.edges]}, inputs={g.inputs}"
    assert any(e.source == "env:CMD" for e in e2e), f"Sources: {[e.source for e in e2e]}"
    assert any("process" in e.sink for e in e2e), f"Sinks: {[e.sink for e in e2e]}"


# ---------------------------------------------------------------------------
# Cap at MAX_EDGES_PER_SYMBOL
# ---------------------------------------------------------------------------


def test_truncation_caps_edges_at_50(project_factory, monkeypatch):
    """A function with many param→effect lines is truncated at the cap."""
    from roam.world_model.causal_graph import MAX_EDGES_PER_SYMBOL

    # Build a function with 80 io_write lines, all reading param `p`.
    body_lines = ["def overflow(p):"] + [f"    open(p, 'w').write(str({i}))" for i in range(80)]
    proj = project_factory(
        {
            "src/over.py": "\n".join(body_lines) + "\n",
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.causal_graph import classify_causal_graph

    with open_db(readonly=True) as conn:
        results = classify_causal_graph(conn, symbol_name="overflow")

    assert results
    g = results[0]
    assert g.truncated is True, "Expected truncated=True with 80 generating lines"
    assert len(g.edges) <= MAX_EDGES_PER_SYMBOL, f"Edges {len(g.edges)} should be <= cap {MAX_EDGES_PER_SYMBOL}"


# ---------------------------------------------------------------------------
# Pure function — only param_to_return, no side-effect edges
# ---------------------------------------------------------------------------


def test_pure_function_has_no_side_effect_causal_edges(project_factory, monkeypatch):
    """`def add(a, b): return a + b` → only param_to_return edges."""
    proj = project_factory(
        {
            "src/pure.py": ("def add(a, b):\n    return a + b\n"),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.causal_graph import classify_causal_graph

    with open_db(readonly=True) as conn:
        results = classify_causal_graph(conn, symbol_name="add")

    assert results
    g = results[0]
    # No side-effect / env / mutation / raise edges.
    forbidden = {"param_to_effect", "global_to_effect", "env_to_effect", "global_to_mutation", "param_to_raise"}
    bad = [e for e in g.edges if e.kind in forbidden]
    assert not bad, f"Pure function should not have side-effect causal edges, got {[(e.source, e.sink) for e in bad]}"
    # Both params should appear in return expression.
    p2r = [e for e in g.edges if e.kind == "param_to_return"]
    sources = {e.source for e in p2r}
    assert "param:a" in sources, f"Expected param:a in return edges, got {sources}"
    assert "param:b" in sources, f"Expected param:b in return edges, got {sources}"


# ---------------------------------------------------------------------------
# Envelope includes by_kind distribution + causal_kind_definition
# ---------------------------------------------------------------------------


def test_envelope_includes_by_kind_distribution(project_factory, monkeypatch, cli_runner):
    """``roam --json causal-graph`` envelope: by_kind + causal_kind_definition + facts."""
    proj = project_factory(
        {
            "src/mixed.py": (
                "import subprocess\n"
                "import os\n"
                "\n"
                "def writer(path):\n"
                "    with open(path, 'w') as f:\n"
                "        f.write('x')\n"
                "\n"
                "def echo(x):\n"
                "    return x\n"
                "\n"
                "def runner(cmd_arg):\n"
                "    cmd = os.environ.get('CMD')\n"
                "    subprocess.run([cmd_arg, cmd])\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["causal-graph", "--top", "10"], json_mode=True)
    assert result.exit_code == 0, f"causal-graph failed: {result.output}"
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)

    assert data["command"] == "causal-graph"
    summary = data["summary"]
    assert "by_kind" in summary
    assert "total_edges" in summary
    assert "causal_kind_definition" in summary  # Pattern 3: metric definition
    assert "detector" in summary
    assert summary["state"] == "ok"
    assert summary["partial_success"] is False

    by_kind = summary["by_kind"]
    # At least param_to_effect, param_to_return, env_to_effect should be present
    assert by_kind.get("param_to_effect", 0) >= 1
    assert by_kind.get("param_to_return", 0) >= 1
    assert by_kind.get("env_to_effect", 0) >= 1

    # graphs surfaced
    assert isinstance(data["graphs"], list)
    assert len(data["graphs"]) > 0

    # agent_contract facts: concrete-noun anchored (LAW 4)
    ac = data["agent_contract"]
    assert isinstance(ac["facts"], list)
    assert all(isinstance(f, str) for f in ac["facts"])
    # First fact should mention a concrete symbol when edges exist.
    facts_blob = "\n".join(ac["facts"])
    assert "causes" in facts_blob or "edges" in facts_blob, f"Expected concrete fact, got {ac['facts']}"
    # next_commands must be imperative roam strings
    assert all(nc.startswith("roam ") for nc in ac["next_commands"])


# ---------------------------------------------------------------------------
# Unit tests for _extract_params_from_signature None-safety (W1034)
# ---------------------------------------------------------------------------


def test_extract_params_from_signature_none_returns_empty():
    """``signature is None`` (NULL signature column) short-circuits to []."""
    from roam.world_model.causal_graph import _extract_params_from_signature

    assert _extract_params_from_signature(None) == []


def test_extract_params_from_signature_empty_string_returns_empty():
    """Empty signature string short-circuits to [] same as None."""
    from roam.world_model.causal_graph import _extract_params_from_signature

    assert _extract_params_from_signature("") == []


def test_extract_params_from_signature_normal_signature_returns_params():
    """Normal signature extracts params (defaults + type hints stripped)."""
    from roam.world_model.causal_graph import _extract_params_from_signature

    assert _extract_params_from_signature("(self, path: str, mode='w')") == [
        "path",
        "mode",
    ]
    assert _extract_params_from_signature("(name, email)") == ["name", "email"]
