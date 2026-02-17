"""Tests for the 4 root-cause fixes:
1. Generic supplement for inheritance refs
2. Incremental edge re-resolution
3. Java signature fixes (no doubled keywords, no double parens)
4. .roam/ exclusion from indexing
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import roam, git_init, git_commit, index_in_process


@pytest.fixture
def java_project(tmp_path):
    """Create a Java test project with inheritance."""
    proj = tmp_path / "java_proj"
    proj.mkdir()

    (proj / "Animal.java").write_text(
        'public class Animal {\n'
        '    public String speak() { return "..."; }\n'
        '}\n'
    )
    (proj / "Pet.java").write_text(
        'public interface Pet {\n'
        '    String speak();\n'
        '}\n'
    )
    (proj / "Dog.java").write_text(
        'public class Dog extends Animal implements Pet {\n'
        '    private String breed;\n'
        '    public String speak() { return "Woof"; }\n'
        '}\n'
    )
    (proj / "Cat.java").write_text(
        'public class Cat extends Animal implements Pet {\n'
        '    private int lives = 9;\n'
        '    public String speak() { return "Meow"; }\n'
        '}\n'
    )
    (proj / "GuideDog.java").write_text(
        'public class GuideDog extends Dog {\n'
        '    private String handler;\n'
        '    public GuideDog(String name, String handler) {\n'
        '        this.handler = handler;\n'
        '    }\n'
        '}\n'
    )

    git_init(proj)
    return proj


@pytest.fixture
def ts_project(tmp_path):
    """Create a TypeScript test project with inheritance."""
    proj = tmp_path / "ts_proj"
    proj.mkdir()

    (proj / "base.ts").write_text(
        'export interface Serializable {\n'
        '    serialize(): string;\n'
        '}\n'
        '\n'
        'export class BaseEntity {\n'
        '    id: number = 0;\n'
        '    createdAt: Date = new Date();\n'
        '}\n'
    )
    (proj / "user.ts").write_text(
        'import { BaseEntity, Serializable } from "./base";\n'
        '\n'
        'export class User extends BaseEntity implements Serializable {\n'
        '    name: string = "";\n'
        '    email: string = "";\n'
        '    serialize(): string { return JSON.stringify(this); }\n'
        '}\n'
    )
    (proj / "admin.ts").write_text(
        'import { User } from "./user";\n'
        '\n'
        'export class AdminUser extends User {\n'
        '    role: string = "admin";\n'
        '}\n'
    )

    git_init(proj)
    return proj


@pytest.fixture
def go_project(tmp_path):
    """Create a Go test project with embedded structs."""
    proj = tmp_path / "go_proj"
    proj.mkdir()

    (proj / "config.go").write_text(
        'package store\n'
        '\n'
        'type Config struct {\n'
        '    MaxSize int\n'
        '    Timeout int\n'
        '}\n'
    )
    (proj / "store.go").write_text(
        'package store\n'
        '\n'
        'type MemoryStore struct {\n'
        '    Config\n'
        '    data map[string]string\n'
        '}\n'
        '\n'
        'type RedisStore struct {\n'
        '    Config\n'
        '    pool string\n'
        '}\n'
    )

    git_init(proj)
    return proj


@pytest.fixture
def python_project(tmp_path):
    """Create a Python test project for incremental indexing test."""
    proj = tmp_path / "py_proj"
    proj.mkdir()

    (proj / "base.py").write_text(
        'class Base:\n'
        '    def hello(self):\n'
        '        return "hi"\n'
    )
    (proj / "child.py").write_text(
        'from base import Base\n'
        '\n'
        'class Child(Base):\n'
        '    def greet(self):\n'
        '        return self.hello()\n'
    )

    git_init(proj)
    return proj


# ---- Fix 3: Java signature fixes ----

class TestJavaSignatures:
    def test_no_doubled_extends(self, java_project):
        """Java class signatures should not have 'extends extends'."""
        out, rc = index_in_process(java_project, "--force")
        assert rc == 0

        out, rc = roam("symbol", "Dog", cwd=java_project)
        assert "extends extends" not in out, f"Doubled 'extends' in: {out}"
        assert "extends Animal" in out, f"Missing 'extends Animal' in: {out}"

    def test_no_doubled_implements(self, java_project):
        """Java class signatures should not have 'implements implements'."""
        out, _ = index_in_process(java_project, "--force")
        out, _ = roam("symbol", "Dog", cwd=java_project)
        assert "implements implements" not in out, f"Doubled 'implements' in: {out}"
        assert "implements Pet" in out, f"Missing 'implements Pet' in: {out}"

    def test_no_double_parens(self, java_project):
        """Java method signatures should not have double parentheses."""
        index_in_process(java_project, "--force")
        out, _ = roam("file", "Dog.java", cwd=java_project)
        assert "((" not in out, f"Double parens in: {out}"

    def test_constructor_no_double_parens(self, java_project):
        """Java constructor signatures should not have double parentheses."""
        index_in_process(java_project, "--force")
        out, _ = roam("file", "GuideDog.java", cwd=java_project)
        assert "((" not in out, f"Double parens in constructor: {out}"


# ---- Fix 1: Generic supplement + inheritance edges ----

class TestInheritanceEdges:
    def test_java_extends_edges(self, java_project):
        """Java extends should create inherits edges."""
        index_in_process(java_project, "--force")
        out, _ = roam("uses", "Animal", cwd=java_project)
        # Dog and Cat extend Animal
        assert "Dog" in out or "Cat" in out, f"Missing inheritance in: {out}"

    def test_java_implements_edges(self, java_project):
        """Java implements should create implements edges."""
        index_in_process(java_project, "--force")
        out, _ = roam("uses", "Pet", cwd=java_project)
        # Dog and Cat implement Pet
        assert "Dog" in out or "Cat" in out, f"Missing implements in: {out}"

    def test_ts_extends_edges(self, ts_project):
        """TypeScript extends should create inherits edges."""
        index_in_process(ts_project, "--force")
        out, _ = roam("uses", "BaseEntity", cwd=ts_project)
        assert "User" in out, f"Missing TS extends in: {out}"

    def test_ts_implements_edges(self, ts_project):
        """TypeScript implements should create implements edges."""
        index_in_process(ts_project, "--force")
        out, _ = roam("uses", "Serializable", cwd=ts_project)
        assert "User" in out, f"Missing TS implements in: {out}"

    def test_go_embedded_edges(self, go_project):
        """Go embedded structs should create inherits edges."""
        index_in_process(go_project, "--force")
        out, _ = roam("uses", "Config", cwd=go_project)
        assert "MemoryStore" in out or "RedisStore" in out, \
            f"Missing Go embed in: {out}"

    def test_edge_count_nonzero(self, java_project):
        """Indexing should produce edges."""
        out, _ = index_in_process(java_project, "--force")
        # Parse edge count from "Done. X files, Y symbols, Z edges."
        assert "0 edges" not in out, f"Zero edges: {out}"


# ---- Fix 2: Incremental edge re-resolution ----

class TestIncrementalEdges:
    def test_edges_survive_modification(self, python_project):
        """Cross-file edges should survive when target file is modified."""
        # Initial index
        out, _ = index_in_process(python_project, "--force")
        assert "edges" in out

        # Verify initial edge exists
        out, _ = roam("uses", "Base", cwd=python_project)
        assert "Child" in out, f"Initial edge missing: {out}"

        # Modify base.py (the target file)
        (python_project / "base.py").write_text(
            'class Base:\n'
            '    def hello(self):\n'
            '        return "hello world"\n'
            '    def goodbye(self):\n'
            '        return "bye"\n'
        )
        git_commit(python_project, "update base")

        # Incremental re-index
        out, _ = index_in_process(python_project)
        assert "Re-extracting references" in out, \
            f"Should re-extract refs from unchanged files: {out}"

        # Verify edge still exists
        out, _ = roam("uses", "Base", cwd=python_project)
        assert "Child" in out, f"Edge lost after incremental re-index: {out}"


# ---- Fix 4: .roam/ exclusion ----

class TestRoamExclusion:
    def test_roam_dir_excluded(self, python_project):
        """Files inside .roam/ should not be indexed."""
        # Create .roam directory with a file
        roam_dir = python_project / ".roam"
        roam_dir.mkdir(exist_ok=True)
        (roam_dir / "test.py").write_text('x = 1\n')
        git_commit(python_project, "add roam dir")

        index_in_process(python_project, "--force")
        out, _ = roam("file", ".roam/test.py", cwd=python_project)
        # Should NOT find the file
        assert "not found" in out.lower() or "No file" in out, \
            f".roam/ file should be excluded: {out}"

    def test_discovery_skips_roam(self):
        """The _is_skippable function should skip .roam paths."""
        from roam.index.discovery import _is_skippable
        assert _is_skippable(".roam/index.db")
        assert _is_skippable(".roam/test.py")
        assert not _is_skippable("src/main.py")
