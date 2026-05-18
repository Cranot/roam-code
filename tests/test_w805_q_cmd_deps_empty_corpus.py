"""W805-Q - empty-corpus smoke for ``roam deps`` (W805 Pattern-2 sweep).

Seventeenth-in-batch of the W805 Pattern-2 audit. ``cmd_deps`` is a
peer-shape to ``cmd_file`` (W805-N): same ``FILE_BY_PATH`` lookup,
same ``file_not_found_hint``, similar exact-match -> substring-fallback
resolution chain, same ``SystemExit(1)`` on miss.

W978 first-hypothesis re-run BEFORE writing any test
============================================================

``cmd_deps`` IS DB-querying. It calls ``ensure_index()`` + ``open_db()``
and queries ``files``, ``edges``, ``symbols`` tables via the
``FILE_BY_PATH`` + ``FILE_IMPORTS`` + ``FILE_IMPORTED_BY`` queries
in ``src/roam/commands/cmd_deps.py``. The empty-corpus probe surfaces
THREE distinct bug shapes mirroring W805-N's three-bug pile-up:

1. **Pattern-1B/C REAL BUG** - ``cmd_deps.py:69-86``: when the path
   resolves to no row (after the substring fallback also misses), the
   command emits a structured JSON envelope in --json mode (good!)
   BUT calls ``raise SystemExit(1)`` at line 84. The MCP wrapper's
   ``_run_roam_*`` bridge with ``_success_codes = {0, EXIT_GATE_FAILURE}``
   converts the non-zero exit to a generic ``COMMAND_FAILED`` envelope,
   burying the structured ``file_not_found`` signal:

       $ roam --json deps does/not/exist.py
       {"command":"deps","summary":{"verdict":"file not found:...",
        "error":"file_not_found"}, ...}
       <exit 1>   <-- MCP collapse risk

   In NON-json mode, the command emits plain text via
   ``file_not_found_hint(path)`` + exit 1 - the canonical Pattern-1B/C
   double-bug. Fix template: same as W362 on cmd_owner / W805-N on
   cmd_file - exit 0 with structured envelope.

2. **Pattern-1 Variant D REAL BUG** - ``cmd_deps.py:64-68``: the
   resolver silently falls back to ``LIKE '%{path}'`` substring match
   when the exact ``FILE_BY_PATH`` query returns None.
   ``roam deps real.py`` matches ``src/real.py`` and reports the
   degraded match as a confident success:

       $ roam --json deps real.py    # 'real.py' is NOT the indexed path
       {"path":"src/real.py", "summary":{"verdict":"real.py: N imports..."},
        "partial_success":false}      <-- silent fuzzy fallback

   No ``resolution`` field, no ``partial_success=True``, no disclosure
   that the result came from a degraded substring match. Exactly the
   Pattern-1 Variant D "silent success on degraded resolution" shape.

3. **Pattern-2 REAL BUG** - ``cmd_deps.py:108-134``: when a file
   resolves cleanly but has 0 imports AND 0 importers (e.g. a leaf
   module that imports nothing and is imported by nothing), the
   command emits a confident verdict ``"isolated.py: 0 imports, 0
   importers"`` with ``partial_success: false`` and NO ``state``
   disclosure. The verdict reads identically to a "real isolated file
   by design". A consumer cannot distinguish "file with no deps by
   design" from "extractor failed silently" from "indexer missed
   imports".

Test split (mirrors W805-N's three-bug pile-up template):

1. SMOKE (always-on assertions):
   * Clean-corpus regression: real file emits real signal
   * LAW 6: verdict is standalone single-line ASCII
   * Envelope shape (``command``, ``summary.verdict``)

2. PATTERN-1B/C PIN (xfail-strict):
   * Unresolved file in --json mode exits 0 (NOT 1)
   * Unresolved file in non-json mode emits structured signal
   * ``state == "file_not_found"`` on unresolved file

3. PATTERN-2 PIN (xfail-strict):
   * 0-deps file discloses ``state`` (closed enum like ``no_deps``)
   * 0-deps file sets ``partial_success: true``

The W805-Q fix lives in a separate wave; this module is intentionally
test-only per the accumulate-only constraint.
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
def zero_deps_corpus(tmp_path, monkeypatch):
    """Indexed corpus with ``src/isolated.py`` having 0 imports AND 0 importers.

    The file row exists in the ``files`` table (so the path resolves
    cleanly) but contains a single self-contained function that imports
    nothing AND is imported by nothing. This is the canonical Pattern-2
    "file resolved, 0 deps either direction" silent-SAFE branch on
    ``cmd_deps.py:108-134``.
    """
    repo = tmp_path / "zero-deps-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    # No imports + not imported by anything. Filename intentionally
    # avoids 'empty' so the Pattern-2 verdict-keyword check is not
    # satisfied by the filename literal alone.
    (src / "isolated.py").write_text(
        "def standalone():\n    return 42\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def fuzzy_fallback_deps_corpus(tmp_path, monkeypatch):
    """Indexed corpus where ``src/real.py`` is the only real file.

    Probing ``roam deps real.py`` (no ``src/`` prefix) triggers the
    SUBSTRING fuzzy fallback at ``cmd_deps.py:65-68``: the primary
    exact ``FILE_BY_PATH`` query returns 0 rows, then the fallback
    ``LIKE '%real.py'`` matches ``src/real.py``. The Pattern-1
    Variant D silent-SAFE shape.
    """
    repo = tmp_path / "fuzzy-deps-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    (src / "real.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def real_deps_corpus(tmp_path, monkeypatch):
    """Indexed corpus with real imports in ``src/consumer.py``.

    ``consumer.py`` imports from ``helper.py`` (real edge) - both files
    have symbols, both participate in the file-edges graph. This is
    the happy-path positive-coverage baseline.
    """
    repo = tmp_path / "real-deps-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "helper.py").write_text(
        "def helper_fn():\n    return 'help'\n",
        encoding="utf-8",
    )
    (src / "consumer.py").write_text(
        "from src.helper import helper_fn\n\ndef use_it():\n    return helper_fn()\n",
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


def _invoke_deps(target: str, json_mode: bool = True):
    """Run ``roam [--json] deps <target>`` in-process and return result."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.extend(["deps", target])
    return runner.invoke(cli, args, catch_exceptions=False)


def _parse_envelope(result):
    """Parse the runner's stdout as a JSON envelope (tolerant of trailing prose)."""
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestDepsEmptyCorpusSmoke:
    """Pattern-2 always-emit baseline assertions for ``roam deps``."""

    def test_empty_corpus_no_crash(self, zero_deps_corpus):
        """``roam deps src/isolated.py`` on 0-deps file does not crash; exits 0."""
        result = _invoke_deps("src/isolated.py", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, zero_deps_corpus):
        """0-deps file emits ``command=deps`` + non-empty verdict."""
        result = _invoke_deps("src/isolated.py", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "deps"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_law6_verdict_standalone(self, zero_deps_corpus):
        """LAW 6: verdict is a single line of ASCII."""
        result = _invoke_deps("src/isolated.py", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.isascii(), f"verdict is not plain ASCII: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict"), f"verdict is a placeholder: {verdict!r}"

    def test_clean_corpus_emits_real_deps(self, real_deps_corpus):
        """Happy-path positive coverage: a real file emits real signal.

        ``src/consumer.py`` imports from ``src/helper.py`` so the deps
        envelope must surface at least one import edge.
        """
        result = _invoke_deps("src/consumer.py", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        verdict = summary["verdict"]
        # Real signal: imports count > 0 in verdict.
        assert "imports" in verdict.lower(), f"happy-path verdict missing imports keyword: {verdict!r}"
        # The imports count from the envelope summary must reflect at
        # least the helper edge. Some indexer paths may emit 1+ rows
        # for the same import; we only assert >= 1.
        imports_count = summary.get("imports")
        assert isinstance(imports_count, int) and imports_count >= 1, (
            f"happy-path should expose at least 1 import edge, got: "
            f"summary.imports={imports_count!r}; envelope={envelope!r}"
        )

    def test_partial_success_key_present(self, zero_deps_corpus):
        """``summary.partial_success`` key is auto-injected on every envelope."""
        result = _invoke_deps("src/isolated.py", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        assert "partial_success" in summary, (
            f"summary.partial_success key must be auto-injected; got summary keys = {sorted(summary.keys())}"
        )


# ---------------------------------------------------------------------------
# PATTERN-1B/C PIN: unresolved path in --json mode
# (cmd_deps.py:84 raises SystemExit(1) even after emitting structured envelope)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-Q REAL BUG 1 (Pattern-1B): cmd_deps.py:84 raises "
        "SystemExit(1) on unresolved path EVEN AFTER emitting a "
        "structured JSON envelope. The MCP wrapper's _run_roam_* bridge "
        "with _success_codes = {0, EXIT_GATE_FAILURE} converts the "
        "non-zero exit to a generic COMMAND_FAILED envelope - burying "
        "the structured 'file_not_found' signal. Fix template: same as "
        "W362 on cmd_owner / W805-N on cmd_file - exit 0 with "
        "structured envelope. Separate fix wave."
    ),
)
def test_nonexistent_file_exit_code_not_1_on_json(real_deps_corpus):
    """Pin: unresolved file in --json mode SHOULD exit 0 with structured envelope.

    Mirrors the W362 ``file_not_found`` discipline on cmd_owner.
    """
    result = _invoke_deps("does/not/exist.py", json_mode=True)
    assert result.exit_code == 0, (
        f"unresolved file should exit 0 with structured envelope; "
        f"got exit {result.exit_code}, output: {result.output!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-Q REAL BUG 1 (Pattern-1B/C): cmd_deps.py:85-86 emits "
        "plain text from file_not_found_hint() + exit 1 in non-json "
        "mode. The Pattern-1B/C fix template requires structured "
        "signal even on the error path; non-json mode should at "
        "minimum include a recognisable VERDICT: line for consistency "
        "with the rest of the roam surface. Separate fix wave."
    ),
)
def test_nonexistent_file_emits_json_envelope_not_plain_text(real_deps_corpus):
    """Pin: unresolved file in non-json mode should emit a structured signal.

    Today only --json mode emits the structured envelope; non-json mode
    drops to plain text via ``file_not_found_hint``. Pattern-1B/C
    discipline says both modes should be consistent (verdict-first).
    """
    result = _invoke_deps("does/not/exist.py", json_mode=False)
    assert "VERDICT:" in (result.output or ""), (
        f"non-json mode should emit a VERDICT: line on file-not-found; got plain text: {result.output!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-Q REAL BUG 1: cmd_deps.py:71-83 emits a JSON envelope "
        "but does not set summary.state to a closed-enum value. "
        "Pattern-2 requires closed-enum state disclosure "
        "('file_not_found' to mirror cmd_owner's W362 contract). "
        "summary.error='file_not_found' exists but state is not "
        "promoted to the canonical envelope.state field. "
        "Separate fix wave."
    ),
)
def test_nonexistent_file_explicit_state(real_deps_corpus):
    """Pin: unresolved file discloses ``state="file_not_found"`` (closed enum)."""
    result = _invoke_deps("does/not/exist.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {"file_not_found", "path_not_found", "not_indexed"}
    assert state in accepted, (
        f"summary.state should disclose file-not-found state; got {state!r}; expected one of {accepted}"
    )


# ---------------------------------------------------------------------------
# PATTERN-2 PIN: 0-deps file silent SAFE
# (cmd_deps.py:108-134 emits confident verdict for 0-imports/0-importers file)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-Q REAL BUG 2 (Pattern-2): cmd_deps.py:108-134 emits "
        "verdict '<file>: 0 imports, 0 importers' with "
        "summary.partial_success=False when the file resolved but has "
        "0 imports AND 0 importers. A consumer cannot distinguish "
        "'real isolated file by design' from 'extractor failed silently' "
        "from 'indexer missed imports'. Fix: set partial_success=True + "
        "state='no_deps' + verdict that names the isolated state. "
        "Separate fix wave."
    ),
)
def test_zero_deps_file_partial_success(zero_deps_corpus):
    """Pin: ``summary.partial_success`` should be True on 0-deps file."""
    result = _invoke_deps("src/isolated.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"0-deps file branch must set partial_success=True; got summary={summary!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-Q REAL BUG 2 (Pattern-2): cmd_deps.py:108-134 does not "
        "emit a summary.state field on the 0-deps branch. Pattern-2 "
        "requires closed-enum state disclosure. Acceptable: 'no_deps', "
        "'isolated', 'no_imports_no_importers'. Separate fix wave."
    ),
)
def test_zero_deps_file_explicit_state(zero_deps_corpus):
    """Pin: ``summary.state`` discloses the 0-deps state."""
    result = _invoke_deps("src/isolated.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {
        "no_deps",
        "isolated",
        "no_imports_no_importers",
        "no_dependencies",
        "leaf",
    }
    assert state in accepted, f"summary.state should disclose 0-deps state; got {state!r}; expected one of {accepted}"
