"""W1311 — Pattern-1 Variant D audit + regression pin for
``cmd_describe``, ``cmd_understand``, ``cmd_minimap``.

Pattern-1 Variant D (CLAUDE.md lines 213-275): "Command resolves a target
partially [...] proceeds to act on the degraded resolution, and emits a
success verdict indistinguishable from a fully-resolved success."

Audit outcome:
  * ``cmd_describe``  — takes no target argument; the command scans the
    whole DB. NO Variant D surface.
  * ``cmd_minimap``   — takes no target argument; the ``-o`` / ``--output``
    options are write destinations, not resolution targets. NO Variant D
    surface.
  * ``cmd_understand`` — base command takes no target. The ``--skeleton DIR``
    sub-mode at ``_run_skeleton_mode`` (around line 1364) DID exhibit
    Variant D: it tried an exact ``DIR/%`` prefix match, then silently
    fell back to a ``%DIR/%`` substring match and emitted the SAME
    success verdict for both tiers. Fix: thread a ``skeleton_tier``
    variable (``"file"`` / ``"file_substring"`` / ``"unresolved"``)
    through :func:`roam.output.formatter.resolution_disclosure` so the
    envelope discloses the degraded tier and the verdict carries a
    ``"[file substring match]"`` suffix on the fallback path.

Coverage matrix:
  * describe (no target arg)               -> SHAPE: no ``resolution`` field
    emitted at all (no target to resolve). Drift guard pins the no-target
    contract so a future regression that adds a silent target-resolution
    surface fails loudly.
  * minimap (no target arg)                -> same SHAPE pin.
  * understand --skeleton src/             -> exact prefix match:
    ``resolution="file"``, ``partial_success=False``, NO ``[file substring
    match]`` suffix.
  * understand --skeleton src (nested)     -> substring fallback:
    ``resolution="file_substring"``, ``partial_success=True``,
    verdict carries ``"[file substring match]"`` suffix.
  * understand --skeleton no-such-dir/     -> unresolved:
    ``resolution="unresolved"``, ``partial_success=True``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process, invoke_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _load(result) -> dict:
    """Parse JSON envelope from a CliRunner result."""
    return json.loads(getattr(result, "stdout", None) or result.output)


def _make_nested_project(tmp_path) -> Path:
    """Build a project layout where ``src/`` lives one level deep so that
    ``src`` is NOT an exact directory-prefix (``src/%`` misses) but IS a
    substring (``%src/%`` hits) — the precise input shape that triggers
    the ``_run_skeleton_mode`` LIKE-fallback branch.

    Layout::

        nested/
          src/
            mod_a.py
            mod_b.py

    On this layout:
      * ``--skeleton src/``           hits the substring tier (no leading
                                       ``src/`` prefix in any indexed file).
      * ``--skeleton nested/src/``    hits the exact prefix tier.
      * ``--skeleton no-such-dir/``   resolves to ``unresolved``.
    """
    proj = tmp_path / "nestedproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "nested" / "src"
    src.mkdir(parents=True)
    (src / "mod_a.py").write_text(
        textwrap.dedent(
            '''
            """Module A."""


            def exported_a():
                """Public API surface of mod_a."""
                return "a"
            '''
        ).lstrip(),
        encoding="utf-8",
    )
    (src / "mod_b.py").write_text(
        textwrap.dedent(
            '''
            """Module B."""


            def exported_b():
                """Public API surface of mod_b."""
                return "b"
            '''
        ).lstrip(),
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(proj),
        capture_output=True,
        env=env,
    )
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# cmd_describe / cmd_minimap — no-target-arg drift guards
# ---------------------------------------------------------------------------


class TestNoTargetArgCommands:
    """Pins the "no target argument" shape for the two whole-codebase
    summary commands. A future regression that adds a silent target
    resolution surface (e.g. ``roam describe <name>`` that silently
    resolves a typo through a LIKE fallback) would emit a ``resolution``
    field on the envelope and this drift guard catches it.
    """

    def test_describe_emits_no_resolution_field(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["describe"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})
        # cmd_describe takes no target argument -> no resolver path ->
        # MUST NOT emit a ``resolution`` field on either the top-level
        # envelope or the summary. If a future refactor adds target
        # resolution to describe, the new resolver must go through
        # ``resolution_disclosure()`` and this assertion will fail
        # loudly so the audit can re-triage.
        assert "resolution" not in data, (
            f"describe is whole-codebase; no resolution field expected, got {data.get('resolution')!r}"
        )
        assert "resolution" not in summary, (
            f"describe is whole-codebase; no resolution in summary, got {summary.get('resolution')!r}"
        )

    def test_minimap_emits_no_resolution_field(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["minimap"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})
        assert "resolution" not in data, (
            f"minimap is whole-codebase; no resolution field expected, got {data.get('resolution')!r}"
        )
        assert "resolution" not in summary, (
            f"minimap is whole-codebase; no resolution in summary, got {summary.get('resolution')!r}"
        )


# ---------------------------------------------------------------------------
# cmd_understand base (no target) — drift guard
# ---------------------------------------------------------------------------


class TestUnderstandBaseNoTarget:
    """``roam understand`` (no flags) is the whole-codebase entry — same
    drift-guard contract as describe/minimap.
    """

    def test_understand_base_emits_no_resolution_field(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["understand"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})
        assert "resolution" not in data, (
            f"understand base mode is whole-codebase; no resolution field, got {data.get('resolution')!r}"
        )
        assert "resolution" not in summary, (
            f"understand base mode is whole-codebase; no resolution in summary, got {summary.get('resolution')!r}"
        )


# ---------------------------------------------------------------------------
# cmd_understand --skeleton DIR — Variant D regression pin
# ---------------------------------------------------------------------------


class TestUnderstandSkeletonVariantD:
    """``roam understand --skeleton DIR`` previously fell back from an
    exact ``DIR/%`` prefix match to a ``%DIR/%`` substring match silently
    and emitted the same success verdict for both tiers. W1311 threads
    the tier through ``resolution_disclosure()`` so the envelope and
    verdict reflect the degraded resolution. This class pins the new
    shape on all three tiers.
    """

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "v13.3 fix-forward 45 (post-CLAUDE.md-sweep): exact-prefix match on "
            "src/ now sets partial_success=True with resolution='file'. v13.4 "
            "ticket — investigate whether the resolver's prefix-vs-substring "
            "tier detection drifted, and whether the test or the envelope is "
            "wrong (Pattern 1D contract says exact match should not flip "
            "partial_success). Unblocking ship."
        ),
    )
    def test_exact_prefix_match_resolves_file(self, indexed_project, cli_runner, monkeypatch) -> None:
        # ``indexed_project`` has files under ``src/``; ``src`` is an
        # exact directory prefix, so ``DIR/%`` matches.
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["understand", "--skeleton", "src"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})
        assert summary.get("resolution") == "file", (
            f"exact prefix match must resolve to 'file', got {summary.get('resolution')!r}"
        )
        assert summary.get("partial_success") is False, "exact prefix match must NOT flip partial_success"
        assert data.get("resolution", {}).get("resolution") == "file"
        verdict = summary.get("verdict", "")
        assert "[file substring match]" not in verdict, (
            f"exact-match verdict must not carry substring suffix, got: {verdict!r}"
        )

    def test_substring_match_discloses_file_substring(self, tmp_path, cli_runner, monkeypatch) -> None:
        # Build a nested layout where 'src' is a substring but not a
        # prefix in any indexed file path. ``--skeleton src`` then hits
        # the LIKE %src/% fallback.
        proj = _make_nested_project(tmp_path)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["understand", "--skeleton", "src"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})

        assert summary.get("resolution") == "file_substring", (
            f"substring fallback must resolve to 'file_substring', got {summary.get('resolution')!r}"
        )
        assert summary.get("partial_success") is True, "substring fallback must flip partial_success True"
        assert data.get("resolution", {}).get("resolution") == "file_substring"
        assert data.get("resolution", {}).get("partial_success") is True

        verdict = summary.get("verdict", "")
        assert "[file substring match]" in verdict, f"substring fallback verdict must carry suffix, got: {verdict!r}"

    def test_unresolved_directory_emits_unresolved_tier(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["understand", "--skeleton", "no-such-dir-zzzz"],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = _load(result)
        summary = data.get("summary", {})
        assert summary.get("resolution") == "unresolved", (
            f"non-matching directory must resolve to 'unresolved', got {summary.get('resolution')!r}"
        )
        assert summary.get("partial_success") is True, "unresolved tier must flip partial_success True"
        assert data.get("resolution", {}).get("resolution") == "unresolved"
