"""W805-S - empty-corpus smoke for ``roam fan`` (W805 Pattern-2 sweep).

Nineteenth-in-batch of the W805 Pattern-2 audit. ``cmd_fan`` is a
peer-shape file-resolution command listed alongside ``cmd_file``
(W805-N 3-bug), ``cmd_module`` (W805-K 3-bug), ``cmd_deps`` (W805-Q
2-bug). The W805-S probe is a DEFENSIVE pin: zero new bugs in scope,
all four anticipated bug-shapes already sealed by prior waves.

W978 first-hypothesis re-run BEFORE writing any test
============================================================

``cmd_fan`` argument shape differs from its peer commands:

* ``cmd_file <PATH>`` / ``cmd_module <PATH>`` / ``cmd_deps <PATH>`` --
  positional ``PATH`` argument resolved via ``FILE_BY_PATH`` (exact)
  -> ``LIKE '%path'`` fallback. THIS resolver chain is the source of
  Pattern-1B (SystemExit on miss) and Pattern-1 Variant D (silent
  fuzzy substring match) on those peers.

* ``cmd_fan [MODE]`` -- positional ``MODE`` is a Click
  ``Choice(["symbol", "file"])`` with default ``"symbol"``. There is
  NO path resolution, NO symbol lookup, NO substring fallback. The
  command operates on the WHOLE corpus (``graph_metrics`` for symbol
  mode, ``file_edges`` for file mode) and ranks the top-N.

What this means for the four bug-shapes in the brief:

1. **Pattern-1B/C (SystemExit on nonexistent)** -- N/A by design.
   Click's ``Choice`` validator rejects unknown modes with exit 2 +
   plain text BEFORE ``cmd_fan`` runs. That is Click's standard
   usage-error contract, not a roam Pattern-1B bug. Across the
   W805 sweep, Click-Choice usage errors are systemically tolerated
   (no peer command pins this shape).

2. **Pattern-1 Variant D (silent fuzzy fallback)** -- N/A by design.
   No resolver, no fuzzy match, nothing to disclose.

3. **Pattern-2 (silent SAFE on 0-fan)** -- ALREADY SEALED by
   W805-followup-C (see ``cmd_fan.py:445-519`` symbol mode +
   ``671-702`` file mode). Three closed-enum states are emitted:

   * ``"no_symbols"`` -- ``graph_metrics`` query returned zero rows
     (genuinely empty corpus).
   * ``"all_filtered_tooling"`` -- raw rows existed but the
     ``_filter_tooling_rows`` excluder wiped them all.
   * ``"all_filtered_framework"`` -- rows survived tooling
     exclusion but ``--no-framework`` wiped them all.

   File mode emits ``"no_file_edges"`` when ``file_edges`` is empty.
   All four branches set ``partial_success: True`` and emit a
   verdict that names the absent state explicitly.

4. **Empty registry / persist path** -- ``--persist`` writes are
   wrapped in ``try/except sqlite3.OperationalError`` for pre-W89
   schemas. Defensive degradation; never crashes.

REAL BUGs found in scope: **0**

The W805-S test file therefore captures positive-coverage regression
pins for the already-sealed contract. It serves the W805 sweep audit
trail (peer-shape coverage uniform across cmd_file / cmd_module /
cmd_deps / cmd_fan) and prevents silent regression of the four
empty-state branches that W805-followup-C made explicit.

The existing ``test_w805_fan_empty_corpus.py`` covers the two corpus-
level empty paths (``no_symbols`` + ``no_file_edges``). This W805-S
module adds the two filter-induced paths (``all_filtered_tooling``
+ ``all_filtered_framework``), LAW 6 standalone-verdict, and a
clean-corpus positive baseline. No xfails -- all assertions pass on
HEAD.
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git_init_committed(repo: Path) -> None:
    """Init a git repo + commit all current files. Quiet."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(repo),
        capture_output=True,
        env=env,
        check=True,
    )


@pytest.fixture
def empty_corpus_repo(tmp_path, monkeypatch):
    """Indexed corpus with a single empty .py -- no symbols, no edges."""
    repo = tmp_path / "w805s-empty"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo)
    assert rc == 0, f"roam init failed:\n{out}"
    return repo


@pytest.fixture
def tooling_only_corpus(tmp_path, monkeypatch):
    """Indexed corpus where every symbol lives in a tooling-excluded path.

    Drives ``cmd_fan`` into the ``all_filtered_tooling`` branch: raw rows
    exist (``_raw_row_count > 0``) but ``_filter_tooling_rows`` wipes
    them all (``_after_tooling == 0``).
    """
    repo = tmp_path / "w805s-tooling"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    dev = repo / "dev"
    dev.mkdir()
    # ``dev/`` is in the default tooling-exclusion set. Both symbols
    # mutually reference each other so ``graph_metrics`` records
    # in_degree + out_degree > 0 for both.
    (dev / "script.py").write_text(
        "def helper():\n    return other()\n\ndef other():\n    return helper()\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def framework_only_corpus(tmp_path, monkeypatch):
    """Indexed corpus where every symbol name is in ``FRAMEWORK_PRIMITIVE_NAMES``.

    Drives ``cmd_fan --no-framework`` into the ``all_filtered_framework``
    branch: rows survive tooling exclusion (``_after_tooling > 0``) but
    the ``--no-framework`` predicate wipes them all.
    """
    repo = tmp_path / "w805s-framework"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    # Names from FRAMEWORK_PRIMITIVE_NAMES (Vue Composition API).
    # All four symbols form a connected cycle so each has in+out > 0.
    (src / "framework_only.py").write_text(
        "def computed():\n    ref()\n    reactive()\n\n"
        "def ref():\n    computed()\n    watch()\n\n"
        "def reactive():\n    ref()\n    computed()\n\n"
        "def watch():\n    ref()\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def real_fan_corpus(tmp_path, monkeypatch):
    """Indexed corpus with a real cross-file fan-in hub.

    ``src/hub_mod.py::hub`` is called from two distinct files
    (``b.py`` + ``c.py``), creating real fan-in. Drives the
    happy-path verdict ("top fan-in: hub(...)").
    """
    repo = tmp_path / "w805s-real"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "hub_mod.py").write_text("def hub():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text(
        "from src.hub_mod import hub\n\ndef caller_b():\n    return hub()\n",
        encoding="utf-8",
    )
    (src / "c.py").write_text(
        "from src.hub_mod import hub\n\ndef caller_c():\n    return hub()\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# Invocation helpers
# ---------------------------------------------------------------------------


def _invoke_fan(*extra, json_mode: bool = True):
    """Run ``roam [--json] fan [extra...]`` in-process."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("fan")
    args.extend(extra)
    return runner.invoke(cli, args, catch_exceptions=False)


def _parse_envelope(result):
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    return _json.JSONDecoder().raw_decode(raw)[0]


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestFanEmptyCorpusSmoke:
    """Pattern-2 always-emit baseline assertions for ``roam fan``.

    Mirrors the W805-N / W805-K / W805-Q smoke layer. All assertions pass
    on HEAD -- ``cmd_fan`` already discloses the four empty-state branches
    via the W805-followup-C work.
    """

    def test_empty_corpus_no_crash(self, empty_corpus_repo):
        """``roam fan`` on empty corpus exits 0; stdout is non-empty JSON."""
        result = _invoke_fan(json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus_repo):
        """``roam fan`` empty-corpus envelope has command=fan + non-empty verdict."""
        result = _invoke_fan(json_mode=True)
        env = _parse_envelope(result)
        assert env["command"] == "fan"
        summary = env.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_law6_verdict_standalone(self, empty_corpus_repo):
        """LAW 6: verdict is a single line that works without any other field.

        ASCII not required (``cmd_fan`` uses an em-dash); only "no embedded
        newline + not a placeholder" is asserted.
        """
        result = _invoke_fan(json_mode=True)
        env = _parse_envelope(result)
        verdict = env["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict"), f"verdict is a placeholder: {verdict!r}"

    def test_empty_corpus_partial_success_disclosed(self, empty_corpus_repo):
        """Pattern-2: empty corpus emits ``partial_success: True`` + state."""
        result = _invoke_fan(json_mode=True)
        env = _parse_envelope(result)
        summary = env.get("summary") or {}
        assert summary.get("partial_success") is True, (
            f"empty-corpus branch must set partial_success=True; got summary={summary!r}"
        )
        assert summary.get("state") == "no_symbols", (
            f"empty-corpus symbol-mode state must be 'no_symbols'; got {summary.get('state')!r}"
        )

    def test_empty_corpus_file_mode_state(self, empty_corpus_repo):
        """File mode on empty corpus emits ``state: "no_file_edges"``."""
        result = _invoke_fan("file", json_mode=True)
        env = _parse_envelope(result)
        summary = env.get("summary") or {}
        assert summary.get("partial_success") is True
        assert summary.get("state") == "no_file_edges", (
            f"empty-corpus file-mode state must be 'no_file_edges'; got {summary.get('state')!r}"
        )


# ---------------------------------------------------------------------------
# FILTER-INDUCED EMPTY STATES
# (Pattern-2 lineage-disclosure regression pins)
# ---------------------------------------------------------------------------


class TestFanFilterInducedEmptyStates:
    """Filter-induced empty states must disclose the FILTER cause.

    Pre-W805-followup-C, ``cmd_fan`` collapsed three distinct causes into
    one misleading "corpus empty" verdict. These two tests pin the
    closed-enum distinction so a future refactor cannot silently regress
    back to the pre-fix uniform message.
    """

    def test_all_filtered_tooling_state_disclosed(self, tooling_only_corpus):
        """When every symbol is tooling-excluded, state == 'all_filtered_tooling'."""
        result = _invoke_fan(json_mode=True)
        env = _parse_envelope(result)
        summary = env.get("summary") or {}
        assert summary.get("partial_success") is True
        assert summary.get("state") == "all_filtered_tooling", (
            f"tooling-only corpus must emit state='all_filtered_tooling'; got {summary.get('state')!r}"
        )
        # Verdict must mention the FILTER cause, not the absent-corpus cause.
        verdict = (summary.get("verdict") or "").lower()
        assert "tooling" in verdict or "include-tooling" in verdict, (
            f"tooling-only verdict must name the filter cause; got {verdict!r}"
        )

    def test_all_filtered_framework_state_disclosed(self, framework_only_corpus):
        """When every symbol is framework-named, state == 'all_filtered_framework'."""
        result = _invoke_fan("--no-framework", json_mode=True)
        env = _parse_envelope(result)
        summary = env.get("summary") or {}
        assert summary.get("partial_success") is True
        assert summary.get("state") == "all_filtered_framework", (
            f"framework-only corpus must emit state='all_filtered_framework'; got {summary.get('state')!r}"
        )
        verdict = (summary.get("verdict") or "").lower()
        assert "framework" in verdict or "no-framework" in verdict, (
            f"framework-only verdict must name the filter cause; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# CLEAN-CORPUS POSITIVE BASELINE
# ---------------------------------------------------------------------------


class TestFanCleanCorpusBaseline:
    """Happy-path positive coverage: a real corpus produces real fan signal."""

    def test_clean_corpus_emits_real_fan(self, real_fan_corpus):
        """Real fan-in hub surfaces in the verdict.

        ``src/hub_mod.py::hub`` is called from two distinct files. The
        happy-path verdict reads ``"top fan-in: hub(N), top fan-out: ..."``
        on a fan run.
        """
        result = _invoke_fan(json_mode=True)
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        summary = env["summary"]
        verdict = summary["verdict"]
        # Real signal: hub name surfaces in the top fan-in slot.
        assert "fan-in" in verdict.lower(), f"happy-path verdict missing fan-in keyword: {verdict!r}"
        # Non-empty items list.
        items = env.get("items") or []
        assert isinstance(items, list) and len(items) >= 1, f"happy-path items must be non-empty list; got {items!r}"

    def test_clean_corpus_no_partial_success_state(self, real_fan_corpus):
        """Happy path does NOT set partial_success or state.

        Asserts the inverse of the Pattern-2 pins: on a real corpus where
        the detector ran cleanly, ``partial_success`` is False (or absent)
        and no empty-state ``state`` enum is set.
        """
        result = _invoke_fan(json_mode=True)
        env = _parse_envelope(result)
        summary = env.get("summary") or {}
        # partial_success key is auto-injected; on the happy path it
        # MUST be the default ``False`` rather than carrying an
        # empty-state True from a regressed branch.
        assert summary.get("partial_success") in (False, None), (
            f"happy-path partial_success must be False/None; got {summary.get('partial_success')!r}"
        )
        # No empty-state ``state`` enum on the happy path.
        assert summary.get("state") in (None, "", "ok"), f"happy-path state must be unset; got {summary.get('state')!r}"
