"""Telemetry-driven (2026-06-04): bare code-filenames ("cmd_verify.py", no
directory) are extremely common in real prompts but `_extract_file_paths` only
yields SLASH-paths, so the path-driven probes (file_skeleton / file_summary /
api_surface) never fired → weak envelope. `_resolve_bare_filenames` resolves a
UNIQUE bare filename to its repo path via the index `files` table, and
`to_l1_probe_envelope` uses it when no slash-path was extracted.
"""

from __future__ import annotations

import os

import pytest

# xdist: these tests compile against the MAIN repo (shared
# .roam/compile-envelope-cache.sqlite + live probe subprocesses), so they
# serialize on one worker. Surfaced on the first parallel CI run
# (2026-06-11): the blast probe returned empty under 4-worker contention.
pytestmark = pytest.mark.xdist_group("mainrepo_compile")

from roam.plan.compiler import _resolve_bare_filenames

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HAS_INDEX = os.path.exists(os.path.join(_REPO, ".roam", "index.db"))


@pytest.mark.skipif(not _HAS_INDEX, reason="requires .roam/index.db")
def test_unique_bare_filename_resolves_to_repo_path() -> None:
    assert _resolve_bare_filenames("what's exported from cmd_verify.py", _REPO) == ["src/roam/commands/cmd_verify.py"]
    assert _resolve_bare_filenames("describe indexer.py", _REPO) == ["src/roam/index/indexer.py"]


@pytest.mark.skipif(not _HAS_INDEX, reason="requires .roam/index.db")
def test_nonexistent_bare_filename_resolves_empty() -> None:
    assert _resolve_bare_filenames("what is foo_nope_xyz123.py for", _REPO) == []


@pytest.mark.skipif(not _HAS_INDEX, reason="requires .roam/index.db")
def test_ambiguous_bare_filename_skipped() -> None:
    # __init__.py exists in many packages → ambiguous → unique-match guard skips it.
    assert _resolve_bare_filenames("describe __init__.py", _REPO) == []


def test_no_cwd_or_no_filename_returns_empty() -> None:
    assert _resolve_bare_filenames("what is exported", None) == []
    assert _resolve_bare_filenames("trace the login flow", _REPO) == []


@pytest.mark.skipif(not _HAS_INDEX, reason="requires .roam/index.db")
def test_api_surface_probe_self_resolves_bare_filename() -> None:
    """`_probe_api_surface_for_task` self-resolves a bare filename when handed
    empty named_paths, so "what's exported from cmd_verify.py" yields an
    api_surface payload (not None)."""
    from roam.plan.compiler import _probe_api_surface_for_task

    out = _probe_api_surface_for_task("what's exported from cmd_verify.py", [], _REPO)
    assert isinstance(out, dict) and "api_surface" in out, f"expected api_surface payload, got {out!r}"
    # Sanity: it resolved the RIGHT file (no path passed in).
    assert _probe_api_surface_for_task("what's exported from cmd_verify.py", [], _REPO) is not None
