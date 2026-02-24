"""Docs site quality checks for tutorial/reference/architecture pages."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_docs_site_required_pages_exist():
    root = _repo_root() / "docs" / "site"
    required = [
        "getting-started.html",
        "integration-tutorials.html",
        "command-reference.html",
        "architecture.html",
        "docs.css",
    ]
    for rel in required:
        assert (root / rel).is_file(), f"missing docs page: {rel}"


def test_getting_started_has_tutorial_flow():
    text = _read(_repo_root() / "docs" / "site" / "getting-started.html")
    assert "Getting Started with roam" in text
    assert "Step 1: Install" in text
    assert "roam init" in text
    assert "roam mcp-setup" in text
    assert "roam health --gate" in text


def test_command_reference_has_examples():
    text = _read(_repo_root() / "docs" / "site" / "command-reference.html")
    assert "Command Reference with Examples" in text
    assert "Core Daily Commands" in text
    assert "roam context" in text
    assert "roam check-rules" in text
    assert "roam mcp --list-tools" in text


def test_integration_tutorials_cover_five_platforms():
    text = _read(_repo_root() / "docs" / "site" / "integration-tutorials.html")
    assert "Integration Tutorials (5 Platforms)" in text
    assert "Claude Code" in text
    assert "Cursor" in text
    assert "Gemini CLI" in text
    assert "Codex CLI" in text
    assert "Amp (Sourcegraph)" in text
    assert "roam mcp-setup claude-code" in text
    assert "roam mcp-setup cursor" in text
    assert "roam mcp-setup gemini-cli" in text
    assert "roam mcp-setup codex-cli" in text


def test_architecture_page_has_diagram_and_pipeline():
    text = _read(_repo_root() / "docs" / "site" / "architecture.html")
    assert "roam-code Architecture" in text
    assert "Architecture Diagram" in text
    assert "<svg" in text
    assert "Index Pipeline Stages" in text
    assert ".roam/index.db" in text


def test_site_pages_linked_from_main_pages():
    index_text = _read(_repo_root() / "docs" / "site" / "index.html")
    landscape_text = _read(_repo_root() / "docs" / "site" / "landscape.html")

    for link in [
        "./getting-started.html",
        "./integration-tutorials.html",
        "./command-reference.html",
        "./architecture.html",
    ]:
        assert link in index_text
        assert link in landscape_text
