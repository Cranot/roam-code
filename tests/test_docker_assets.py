"""Tests for Docker packaging assets (backlog #22)."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_exists():
    dockerfile = ROOT / "Dockerfile"
    assert dockerfile.exists(), "Dockerfile should exist at repository root"


def test_dockerfile_is_alpine_based():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:" in text
    assert "alpine" in text.lower()


def test_dockerfile_runs_roam_cli():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["roam"]' in text


def test_dockerignore_excludes_heavy_dev_paths():
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".git" in text
    assert "tests" in text
    assert ".roam" in text
