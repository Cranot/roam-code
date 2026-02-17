"""Tests for the `roam visualize` command."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import roam, git_init, index_in_process


# ============================================================================
# Shared fixture: small Python project with known dependency structure
# ============================================================================

@pytest.fixture(scope="module")
def indexed_project(tmp_path_factory):
    """Create a temp project with cross-file dependencies, git init, and index."""
    proj = tmp_path_factory.mktemp("visualize")

    (proj / "models.py").write_text(
        'class User:\n'
        '    """A user model."""\n'
        '    def __init__(self, name: str):\n'
        '        self.name = name\n'
        '\n'
        'class Role:\n'
        '    """A role model."""\n'
        '    def __init__(self, title: str):\n'
        '        self.title = title\n'
    )

    (proj / "utils.py").write_text(
        'def validate(value: str) -> bool:\n'
        '    return len(value) > 0\n'
        '\n'
        'def format_name(name: str) -> str:\n'
        '    return name.strip()\n'
    )

    (proj / "service.py").write_text(
        'from models import User, Role\n'
        'from utils import validate, format_name\n'
        '\n'
        'def create_user(name: str) -> User:\n'
        '    if not validate(name):\n'
        '        raise ValueError("bad")\n'
        '    return User(format_name(name))\n'
        '\n'
        'def assign_role(user: User) -> Role:\n'
        '    return Role("member")\n'
    )

    (proj / "main.py").write_text(
        'from service import create_user, assign_role\n'
        '\n'
        'def run():\n'
        '    u = create_user("Alice")\n'
        '    r = assign_role(u)\n'
        '    print(u.name, r.title)\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    return proj


# ============================================================================
# Tests
# ============================================================================

class TestVisualizeMermaid:
    """Test Mermaid output mode."""

    def test_basic(self, indexed_project):
        """Basic mermaid output contains graph directive and edges."""
        out, rc = roam("visualize", cwd=indexed_project)
        assert rc == 0, f"visualize failed: {out}"
        assert "VERDICT: OK" in out
        assert "graph TD" in out
        assert "-->" in out

    def test_lr_direction(self, indexed_project):
        """--direction LR changes graph orientation."""
        out, rc = roam("visualize", "--direction", "LR", cwd=indexed_project)
        assert rc == 0, f"failed: {out}"
        assert "graph LR" in out

    def test_classdef_styles(self, indexed_project):
        """Style definitions are present in mermaid output."""
        out, rc = roam("visualize", cwd=indexed_project)
        assert rc == 0
        assert "classDef classNode" in out
        assert "classDef funcNode" in out
        assert "classDef fileNode" in out


class TestVisualizeDot:
    """Test DOT output mode."""

    def test_dot_format(self, indexed_project):
        """--format dot produces DOT syntax."""
        out, rc = roam("visualize", "--format", "dot", cwd=indexed_project)
        assert rc == 0, f"failed: {out}"
        assert "digraph G {" in out
        assert "}" in out
        # Should have edges
        assert "->" in out


class TestVisualizeFocus:
    """Test focus mode (BFS neighborhood)."""

    def test_focus_mode(self, indexed_project):
        """--focus limits output to a symbol neighborhood."""
        out, rc = roam("visualize", "--focus", "create_user", cwd=indexed_project)
        assert rc == 0, f"failed: {out}"
        assert "VERDICT: OK" in out
        assert "graph TD" in out
        # Focus should produce fewer nodes than full graph
        assert "focus=create_user" in out

    def test_focus_not_found(self, indexed_project):
        """--focus with unknown symbol gives an error."""
        out, rc = roam("visualize", "--focus", "nonexistent_xyz_99", cwd=indexed_project)
        assert rc != 0 or "not found" in out.lower() or "error" in out.lower()


class TestVisualizeOptions:
    """Test various filtering options."""

    def test_limit(self, indexed_project):
        """--limit caps the number of nodes."""
        out, rc = roam("visualize", "--limit", "5", cwd=indexed_project)
        assert rc == 0, f"failed: {out}"
        assert "VERDICT: OK" in out
        # Count node definitions (lines matching n<digits>)
        import re
        node_defs = re.findall(r'\bn\d+[\[(""]', out)
        assert len(node_defs) <= 10  # generous upper bound (5 nodes, some may appear in edges)

    def test_no_clusters(self, indexed_project):
        """--no-clusters omits subgraph blocks."""
        out, rc = roam("visualize", "--no-clusters", cwd=indexed_project)
        assert rc == 0, f"failed: {out}"
        assert "subgraph" not in out

    def test_file_level(self, indexed_project):
        """--file-level uses file graph."""
        out, rc = roam("visualize", "--file-level", cwd=indexed_project)
        assert rc == 0, f"failed: {out}"
        assert "VERDICT: OK" in out
        assert "graph TD" in out


class TestVisualizeJson:
    """Test JSON output mode."""

    def test_json_envelope(self, indexed_project):
        """--json wraps output in standard envelope."""
        out, rc = roam("--json", "visualize", cwd=indexed_project)
        assert rc == 0, f"failed: {out}"
        data = json.loads(out)
        assert data["command"] == "visualize"
        assert "summary" in data
        assert data["summary"]["verdict"] == "OK"
        assert data["summary"]["nodes"] > 0
        assert "diagram" in data
        assert "graph TD" in data["diagram"] or "graph LR" in data["diagram"]

    def test_json_dot(self, indexed_project):
        """--json with --format dot includes DOT in diagram field."""
        out, rc = roam("--json", "visualize", "--format", "dot", cwd=indexed_project)
        assert rc == 0, f"failed: {out}"
        data = json.loads(out)
        assert "digraph G {" in data["diagram"]
        assert data["summary"]["format"] == "dot"


class TestVisualizeEmpty:
    """Test graceful handling of empty index."""

    def test_empty_index(self, tmp_path):
        """Empty project produces EMPTY verdict."""
        # Create an empty project with just a non-code file
        (tmp_path / "README.txt").write_text("hello")
        git_init(tmp_path)
        out, rc = index_in_process(tmp_path)
        # visualize should handle the empty graph gracefully
        out, rc = roam("visualize", cwd=tmp_path)
        assert rc == 0
        assert "EMPTY" in out

    def test_empty_json(self, tmp_path):
        """Empty project in JSON mode returns EMPTY verdict."""
        (tmp_path / "README.txt").write_text("hello")
        git_init(tmp_path)
        index_in_process(tmp_path)
        out, rc = roam("--json", "visualize", cwd=tmp_path)
        assert rc == 0
        data = json.loads(out)
        assert data["summary"]["verdict"] == "EMPTY"
        assert data["summary"]["nodes"] == 0
