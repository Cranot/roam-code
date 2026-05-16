"""Tests for the world-model side-effects detector (R28 sub-feature 1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Pure-function fixture
# ---------------------------------------------------------------------------


def test_classify_pure_function_returns_none(project_factory, monkeypatch):
    """A function that does no I/O / spawn / mutation classifies as 'none'."""
    proj = project_factory(
        {
            "src/pure.py": ("def add(a, b):\n    return a + b\n"),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.side_effects import classify_side_effects

    with open_db(readonly=True) as conn:
        results = classify_side_effects(conn, symbol_name="add")

    assert results, "Expected to classify the 'add' function"
    c = results[0]
    assert c.kinds == ["none"], f"Expected ['none'], got {c.kinds}"
    assert c.confidence in ("high", "medium")


# ---------------------------------------------------------------------------
# Open-file fixture — io_write / io_read
# ---------------------------------------------------------------------------


def test_classify_function_with_open_call_returns_io_write_or_read(project_factory, monkeypatch):
    """`with open(path, 'w'): f.write(...)` → io_write."""
    proj = project_factory(
        {
            "src/writer.py": (
                "def write_log(line):\n"
                "    with open('out.log', 'w') as f:\n"
                "        f.write(line)\n"
                "\n"
                "def read_log():\n"
                "    with open('out.log') as f:\n"
                "        return f.read()\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.side_effects import classify_side_effects

    with open_db(readonly=True) as conn:
        all_results = classify_side_effects(conn)

    by_name = {c.symbol.rsplit(".", 1)[-1]: c for c in all_results}
    assert "write_log" in by_name, f"Got symbols: {list(by_name)}"
    assert "io_write" in by_name["write_log"].kinds
    # read_log should classify as io_read (1-arg open defaults to 'r')
    assert "read_log" in by_name
    assert "io_read" in by_name["read_log"].kinds


# ---------------------------------------------------------------------------
# requests → io_read / io_write
# ---------------------------------------------------------------------------


def test_classify_function_calling_requests_returns_io_read(project_factory, monkeypatch):
    """`requests.get(url)` → io_read."""
    proj = project_factory(
        {
            "src/api.py": (
                "import requests\n"
                "\n"
                "def fetch_user(uid):\n"
                "    resp = requests.get(f'https://api.example.com/users/{uid}')\n"
                "    return resp.json()\n"
                "\n"
                "def create_user(payload):\n"
                "    resp = requests.post('https://api.example.com/users', json=payload)\n"
                "    return resp.json()\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.side_effects import classify_side_effects

    with open_db(readonly=True) as conn:
        all_results = classify_side_effects(conn)

    by_name = {c.symbol.rsplit(".", 1)[-1]: c for c in all_results}
    assert "fetch_user" in by_name
    assert "io_read" in by_name["fetch_user"].kinds
    assert "create_user" in by_name
    assert "io_write" in by_name["create_user"].kinds


# ---------------------------------------------------------------------------
# subprocess → process
# ---------------------------------------------------------------------------


def test_classify_function_with_subprocess_returns_process(project_factory, monkeypatch):
    """`subprocess.run([...])` → process."""
    proj = project_factory(
        {
            "src/runner.py": (
                "import subprocess\n"
                "\n"
                "def run_git():\n"
                "    return subprocess.run(['git', 'status'], capture_output=True)\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.side_effects import classify_side_effects

    with open_db(readonly=True) as conn:
        results = classify_side_effects(conn, symbol_name="run_git")

    assert results, "Expected to classify run_git"
    assert "process" in results[0].kinds


# ---------------------------------------------------------------------------
# Envelope shape — top-offender list ordering
# ---------------------------------------------------------------------------


def test_envelope_lists_top_offenders(project_factory, monkeypatch, cli_runner):
    """``roam --json side-effects`` envelope: by_kind counts + sorted classifications."""
    proj = project_factory(
        {
            "src/mixed.py": (
                "import subprocess\n"
                "import requests\n"
                "\n"
                "def pure_add(a, b):\n"
                "    return a + b\n"
                "\n"
                "def writer():\n"
                "    with open('x.log', 'w') as f:\n"
                "        f.write('hello')\n"
                "\n"
                "def spawner():\n"
                "    return subprocess.run(['ls'])\n"
                "\n"
                "def fetcher():\n"
                "    return requests.get('http://example.com')\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["side-effects", "--top", "10"], json_mode=True)
    assert result.exit_code == 0, f"side-effects failed: {result.output}"
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)

    assert data["command"] == "side-effects"
    summary = data["summary"]
    assert "by_kind" in summary
    assert summary["state"] == "ok"
    assert summary["partial_success"] is False
    # detector identity must be in the envelope (Pattern 3: metric definition)
    assert "kind_definition" in summary
    assert "detector" in summary

    by_kind = summary["by_kind"]
    # Pure function present
    assert by_kind.get("none", 0) >= 1
    # io_write must be reported
    assert by_kind.get("io_write", 0) >= 1
    # process must be reported
    assert by_kind.get("process", 0) >= 1

    # classifications surfaced should be ranked — first item should be high-interest
    classifications = data["classifications"]
    assert len(classifications) > 0
    first = classifications[0]
    # interest order: process > io_write > mutation > io_read > unknown > none
    interesting_kinds = {"process", "io_write", "mutation", "io_read"}
    assert set(first["kinds"]) & interesting_kinds, (
        f"Top classification should be a high-interest kind, got {first['kinds']}"
    )

    # agent_contract is present and facts are flat strings
    ac = data["agent_contract"]
    assert isinstance(ac["facts"], list)
    assert all(isinstance(f, str) for f in ac["facts"])
    assert any("roam idempotency" in nc for nc in ac["next_commands"])


# ---------------------------------------------------------------------------
# Symbol filter
# ---------------------------------------------------------------------------


def test_symbol_filter_returns_only_one(project_factory, monkeypatch):
    """`classify_side_effects(conn, symbol_name='X')` scopes to that one symbol."""
    proj = project_factory(
        {
            "src/two.py": ("def alpha():\n    return 1\n\ndef beta():\n    return 2\n"),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.side_effects import classify_side_effects

    with open_db(readonly=True) as conn:
        results = classify_side_effects(conn, symbol_name="alpha")

    assert len(results) == 1
    assert results[0].symbol.rsplit(".", 1)[-1] == "alpha"
