"""Tests for the R19 repo-local agent memory substrate.

Covers:
  - add_memory appends a parseable JSONL entry
  - list_memory streams entries
  - relevant_memory ranks by overlap + respects --top
  - empty-memory state is a clean envelope, not an error
  - JSON envelope shape (verdict / partial_success / schema)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    assert_json_envelope,
    git_init,
    invoke_cli,
    parse_json_output,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_project(tmp_path):
    """A minimal git-initialised project with no memory yet."""
    proj = tmp_path / "memproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    return proj


def _memory_file(proj: Path) -> Path:
    return proj / ".roam" / "memory.jsonl"


# ---------------------------------------------------------------------------
# 1. add appends to JSONL
# ---------------------------------------------------------------------------


def test_add_memory_appends_to_jsonl(cli_runner, memory_project, monkeypatch):
    monkeypatch.chdir(memory_project)
    result = invoke_cli(
        cli_runner,
        [
            "memory",
            "add",
            "--kind",
            "fact",
            "--subject",
            "auth/login.py",
            "--body",
            "Always check session in middleware first.",
            "--tags",
            "auth,security",
            "--confidence",
            "high",
        ],
        cwd=memory_project,
    )
    assert result.exit_code == 0, result.output

    path = _memory_file(memory_project)
    assert path.exists(), "memory file was not created"

    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one line, got {len(lines)}"

    entry = json.loads(lines[0])
    assert entry["kind"] == "fact"
    assert entry["subject"] == "auth/login.py"
    assert entry["body"] == "Always check session in middleware first."
    assert entry["confidence"] == "high"
    assert "auth" in entry["tags"]
    assert "security" in entry["tags"]
    assert entry["id"].startswith("mem_")
    assert "ts" in entry and entry["ts"]


# ---------------------------------------------------------------------------
# 2. add returns entry id in the envelope
# ---------------------------------------------------------------------------


def test_add_returns_entry_id(cli_runner, memory_project, monkeypatch):
    monkeypatch.chdir(memory_project)
    result = invoke_cli(
        cli_runner,
        [
            "memory",
            "add",
            "--kind",
            "decision",
            "--subject",
            "build_system",
            "--body",
            "Use pyproject.toml as the single source of truth for version.",
        ],
        cwd=memory_project,
        json_mode=True,
    )
    data = parse_json_output(result, "memory-add")
    assert_json_envelope(data, "memory-add")
    assert data["summary"]["added"] is True
    assert data["summary"]["partial_success"] is False
    assert "id" in data["summary"]
    assert data["summary"]["id"].startswith("mem_")
    # And the entry payload should also carry the id.
    assert data["entry"]["id"] == data["summary"]["id"]


# ---------------------------------------------------------------------------
# 3. list on empty -> no_memory state
# ---------------------------------------------------------------------------


def test_list_memory_empty_returns_no_memory_state(cli_runner, memory_project, monkeypatch):
    monkeypatch.chdir(memory_project)
    result = invoke_cli(cli_runner, ["memory", "list"], cwd=memory_project, json_mode=True)
    data = parse_json_output(result, "memory-list")
    assert_json_envelope(data, "memory-list")
    assert data["summary"]["state"] == "no_memory"
    assert data["summary"]["partial_success"] is False
    assert data["summary"]["total"] == 0
    assert data["entries"] == []


# ---------------------------------------------------------------------------
# 4. list streams entries
# ---------------------------------------------------------------------------


def test_list_memory_streams_entries(cli_runner, memory_project, monkeypatch):
    monkeypatch.chdir(memory_project)
    for i, kind in enumerate(("fact", "convention", "warning")):
        result = invoke_cli(
            cli_runner,
            [
                "memory",
                "add",
                "--kind",
                kind,
                "--subject",
                f"topic_{i}",
                "--body",
                f"entry number {i}",
            ],
            cwd=memory_project,
        )
        assert result.exit_code == 0, result.output

    result = invoke_cli(cli_runner, ["memory", "list"], cwd=memory_project, json_mode=True)
    data = parse_json_output(result, "memory-list")
    assert_json_envelope(data, "memory-list")
    assert data["summary"]["total"] == 3
    assert data["summary"]["state"] == "ok"
    kinds_seen = {e["kind"] for e in data["entries"]}
    assert kinds_seen == {"fact", "convention", "warning"}


# ---------------------------------------------------------------------------
# 5. relevant ranks by overlap
# ---------------------------------------------------------------------------


def test_relevant_memory_ranks_by_overlap(cli_runner, memory_project, monkeypatch):
    monkeypatch.chdir(memory_project)

    # Entry A — strong overlap with "login auth"
    invoke_cli(
        cli_runner,
        [
            "memory",
            "add",
            "--kind",
            "fact",
            "--subject",
            "auth/login.py",
            "--body",
            "Login flow requires session middleware.",
            "--tags",
            "auth,login",
        ],
        cwd=memory_project,
    )
    # Entry B — about something unrelated
    invoke_cli(
        cli_runner,
        [
            "memory",
            "add",
            "--kind",
            "fact",
            "--subject",
            "build_system",
            "--body",
            "Use ruff for linting.",
            "--tags",
            "tooling",
        ],
        cwd=memory_project,
    )
    # Entry C — partial overlap (auth, no login)
    invoke_cli(
        cli_runner,
        [
            "memory",
            "add",
            "--kind",
            "convention",
            "--subject",
            "auth/policy.md",
            "--body",
            "Auth policies live in docs/auth/.",
            "--tags",
            "auth",
        ],
        cwd=memory_project,
    )

    result = invoke_cli(
        cli_runner,
        ["memory", "relevant", "--query", "login auth flow", "--top", "5"],
        cwd=memory_project,
        json_mode=True,
    )
    data = parse_json_output(result, "memory-relevant")
    assert_json_envelope(data, "memory-relevant")
    results = data["results"]
    assert len(results) >= 2, f"expected at least 2 matches, got {results}"
    # Top result should be the login/auth entry (highest overlap).
    top = results[0]
    assert top["entry"]["subject"] == "auth/login.py"
    assert top["score"] > 0.0
    # Scores must be sorted desc.
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 6. relevant respects --top
# ---------------------------------------------------------------------------


def test_relevant_memory_top_n_truncation(cli_runner, memory_project, monkeypatch):
    monkeypatch.chdir(memory_project)
    # Add 10 entries that all match the query.
    for i in range(10):
        invoke_cli(
            cli_runner,
            [
                "memory",
                "add",
                "--kind",
                "fact",
                "--subject",
                f"alpha_{i}",
                "--body",
                f"alpha topic entry number {i}",
                "--tags",
                "alpha",
            ],
            cwd=memory_project,
        )
    result = invoke_cli(
        cli_runner,
        ["memory", "relevant", "--query", "alpha", "--top", "3"],
        cwd=memory_project,
        json_mode=True,
    )
    data = parse_json_output(result, "memory-relevant")
    assert_json_envelope(data, "memory-relevant")
    assert data["summary"]["total"] == 3
    assert len(data["results"]) == 3


# ---------------------------------------------------------------------------
# 7. envelope shape across all three subcommands
# ---------------------------------------------------------------------------


def test_memory_json_envelope_shape(cli_runner, memory_project, monkeypatch):
    monkeypatch.chdir(memory_project)

    # add envelope
    result = invoke_cli(
        cli_runner,
        ["memory", "add", "--kind", "fact", "--subject", "x", "--body", "y"],
        cwd=memory_project,
        json_mode=True,
    )
    data = parse_json_output(result, "memory-add")
    assert_json_envelope(data, "memory-add")
    assert data["schema"] == "roam-envelope-v1"
    assert "schema_version" in data
    assert "verdict" in data["summary"] and isinstance(data["summary"]["verdict"], str)
    assert "partial_success" in data["summary"]

    # list envelope
    result = invoke_cli(cli_runner, ["memory", "list"], cwd=memory_project, json_mode=True)
    data = parse_json_output(result, "memory-list")
    assert_json_envelope(data, "memory-list")
    assert data["schema"] == "roam-envelope-v1"
    assert "verdict" in data["summary"]
    assert "partial_success" in data["summary"]

    # relevant envelope
    result = invoke_cli(
        cli_runner,
        ["memory", "relevant", "--query", "x"],
        cwd=memory_project,
        json_mode=True,
    )
    data = parse_json_output(result, "memory-relevant")
    assert_json_envelope(data, "memory-relevant")
    assert data["schema"] == "roam-envelope-v1"
    assert "verdict" in data["summary"]
    assert "partial_success" in data["summary"]


# ---------------------------------------------------------------------------
# Bonus: direct store API smoke
# ---------------------------------------------------------------------------


def test_store_api_direct(tmp_path):
    """Direct programmatic use of the store API (no CLI)."""
    from roam.memory.store import MemoryEntry, add_memory, list_memory, relevant_memory

    root = tmp_path / "rootdir"
    root.mkdir()
    entry = MemoryEntry(
        kind="warning",
        subject="middleware.py",
        body="Never call session.save inside a transaction.",
        agent="claude",
        confidence="high",
        tags=["session", "transaction"],
        relevance_signals={"symbols": ["save_session"], "files": ["middleware.py"], "topics": ["concurrency"]},
    )
    new_id = add_memory(root, entry)
    assert new_id.startswith("mem_")

    items = list(list_memory(root))
    assert len(items) == 1
    assert items[0].id == new_id

    ranked = relevant_memory(root, query="session save concurrency", top=5)
    assert len(ranked) == 1
    assert ranked[0][1] > 0.0
