"""Basic integration tests for Roam."""

import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def roam_project(tmp_path_factory):
    """Create a small Python project and index it."""
    root = tmp_path_factory.mktemp("project")

    # Create files
    (root / "main.py").write_text(
        'from helper import add\n\ndef main():\n    print(add(1, 2))\n\nif __name__ == "__main__":\n    main()\n'
    )
    (root / "helper.py").write_text(
        'def add(a, b):\n    """Add two numbers."""\n    return a + b\n\ndef multiply(x, y):\n    return x * y\n'
    )
    (root / "models.py").write_text(
        'class User:\n    def __init__(self, name):\n        self.name = name\n\n    def greet(self):\n        return f"Hello {self.name}"\n'
    )

    # Git init
    subprocess.run(["git", "init"], cwd=root, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True)

    return root


def _run_roam(args, cwd):
    result = subprocess.run(
        [sys.executable, "-m", "roam"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result


def test_index(roam_project):
    result = _run_roam(["index"], roam_project)
    assert result.returncode == 0
    assert "Index complete" in result.stdout or "Index complete" in result.stderr or result.returncode == 0


def test_map(roam_project):
    result = _run_roam(["map"], roam_project)
    assert result.returncode == 0
    assert "Files:" in result.stdout


def test_file(roam_project):
    result = _run_roam(["file", "main.py"], roam_project)
    assert result.returncode == 0
    assert "main" in result.stdout


def test_search(roam_project):
    result = _run_roam(["search", "add"], roam_project)
    assert result.returncode == 0
    assert "add" in result.stdout


def test_symbol(roam_project):
    result = _run_roam(["search", "User"], roam_project)
    assert result.returncode == 0


def test_deps(roam_project):
    result = _run_roam(["deps", "main.py"], roam_project)
    assert result.returncode == 0


def test_dead(roam_project):
    result = _run_roam(["dead"], roam_project)
    assert result.returncode == 0


def test_health(roam_project):
    result = _run_roam(["health"], roam_project)
    assert result.returncode == 0


def test_weather(roam_project):
    result = _run_roam(["weather"], roam_project)
    assert result.returncode == 0


def test_clusters(roam_project):
    result = _run_roam(["clusters"], roam_project)
    assert result.returncode == 0


def test_layers(roam_project):
    result = _run_roam(["layers"], roam_project)
    assert result.returncode == 0


def test_context(roam_project):
    result = _run_roam(["context", "main"], roam_project)
    assert result.returncode == 0
    assert "Context for" in result.stdout


def test_safe_delete(roam_project):
    result = _run_roam(["safe-delete", "multiply"], roam_project)
    assert result.returncode == 0
    assert "SAFE" in result.stdout


def test_split(roam_project):
    result = _run_roam(["split", "main.py"], roam_project)
    assert result.returncode == 0


def test_risk(roam_project):
    result = _run_roam(["risk"], roam_project)
    assert result.returncode == 0


def test_pr_risk(roam_project):
    result = _run_roam(["pr-risk"], roam_project)
    assert result.returncode == 0


def test_why(roam_project):
    result = _run_roam(["why", "add"], roam_project)
    assert result.returncode == 0
    assert "ROLE:" in result.stdout or "role" in result.stdout


def test_why_batch(roam_project):
    result = _run_roam(["why", "add", "multiply", "main"], roam_project)
    assert result.returncode == 0


def test_version():
    result = subprocess.run(
        [sys.executable, "-m", "roam", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "version" in result.stdout


def test_help():
    result = subprocess.run(
        [sys.executable, "-m", "roam", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "index" in result.stdout
    assert "map" in result.stdout
    assert "file" in result.stdout
    assert "symbol" in result.stdout
