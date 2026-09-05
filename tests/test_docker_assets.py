"""Tests for Docker packaging assets (backlog #22)."""

from __future__ import annotations

import re

from tests._helpers.repo_root import repo_root

ROOT = repo_root()


def test_dockerfile_exists():
    dockerfile = ROOT / "Dockerfile"
    assert dockerfile.exists(), "Dockerfile should exist at repository root"


def test_dockerfile_pins_a_glibc_base_by_digest():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"^FROM python:3[.]12-slim-trixie@sha256:[0-9a-f]{64}$", text, re.MULTILINE)


def test_dockerfile_runs_roam_cli():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["roam"]' in text
    assert "uv sync --locked --no-default-groups --extra mcp --no-editable" in text
    assert "COPY pyproject.toml uv.lock README.md LICENSE" in text
    assert "ROAM_TREE_SITTER_CACHE_SEALED=1" in text
    assert "SEALED_PRODUCTION_GRAMMARS, get_parser" in text
    assert "USER roam" in text
    assert "chown roam:roam /workspace" in text
    assert "apt-get upgrade -y" in text
    assert "pip uninstall -y pip uv" in text
    assert "rm -rf /root/.cache/uv" in text


def test_container_bootstrap_matches_reviewed_uv_version():
    from scripts.verify_uv_runtime import EXPECTED_UV_VERSION

    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"pip install 'uv=={EXPECTED_UV_VERSION}'" in text


def test_dockerignore_excludes_heavy_dev_paths():
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".git" in text
    assert "tests" in text
    assert ".roam" in text
    assert "internal" in text.splitlines()
    assert ".env" in text.splitlines()


def test_container_publication_is_release_bound_and_tested():
    text = (ROOT / ".github/workflows/container.yml").read_text(encoding="utf-8")
    assert "workflow_call:" in text
    assert "workflow_dispatch:" not in text
    assert "environment: pypi" in text
    assert "packages: write" in text
    assert "persist-credentials: false" in text
    assert "ref: ${{ inputs.sha }}" in text
    assert "platforms: linux/amd64" in text
    assert "provenance: mode=max" in text and "sbom: true" in text
    assert "cosign sign --yes" in text and "cosign verify" in text
    assert 'docker --config "$public_config" pull' in text
    assert text.index("Test the exact candidate digest") < text.index("Sign and verify the tested digest")
    assert text.index("Require an anonymous pull") < text.index("Promote the verified digest")
    assert '"$latest" == "$TAG"' in text
    assert "Existing image has a different source commit" in text


def test_container_publication_requires_explicit_repository_opt_in():
    gate = "    if: ${{ vars.ROAM_CONTAINER_PUBLISH == 'true' }}"
    caller = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    callee = (ROOT / ".github/workflows/container.yml").read_text(encoding="utf-8")
    assert gate in caller.split("\n  container:\n", 1)[1]
    assert gate in callee.split("\n  publish:\n", 1)[1].split("    steps:", 1)[0]
    # The hold controls container publication only, never package verification.
    assert "ROAM_CONTAINER_PUBLISH" not in caller.split("\n  container:\n", 1)[0]
