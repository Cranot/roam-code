"""W805-N - empty-corpus smoke for ``roam file`` (W805 Pattern 2 sweep).

Fourteenth-in-batch of the W805 Pattern-2 audit. Prior cohort:

- A (cmd_owner)            REAL BUG (silent "top owner: ?")
- B (cmd_minimap)          REAL BUG (silent "minimap rendered (148 chars)")
- C (cmd_oracle)           REAL BUG (verdict/metadata mismatch)
- D (cmd_workflow)         NO REAL BUG
- E (cmd_path_coverage)    NO REAL BUG (W807-hardened)
- F (cmd_for_bug_fix)      REAL BUG (_compound_envelope)
- G (cmd_pr_prep)          REAL BUG (silent READY)
- H (cmd_explain_command)  NO REAL BUG
- I (cmd_describe)         REAL BUG (flagship silent SAFE)
- J (cmd_understand)       REAL BUG (flagship silent Healthy)
- K (cmd_module)           3 REAL BUGs (Pattern-1B/C + Variant D + Pattern-2)
- L (cmd_preflight)        in flight
- M (cmd_diagnose)         in flight
- N (cmd_file, this wave)

W978 first-hypothesis re-run BEFORE writing any test
============================================================

``cmd_file`` IS DB-querying. It calls ``ensure_index()`` + ``open_db()``
and queries ``files``, ``symbols``, ``file_stats``, ``file_edges`` tables
via ``_resolve_file()`` + ``_build_file_skeleton()`` in
``src/roam/commands/cmd_file.py``. As K's peer-shape, the empty-corpus
probe surfaces THREE distinct bug shapes mirroring W805-K:

1. **Pattern-1B REAL BUG** - ``cmd_file.py:270-287``: when the path
   resolves to no row (after the fuzzy fallback also misses), the
   command emits a structured JSON envelope in --json mode (good!)
   BUT calls ``raise SystemExit(1)`` at line 285. The MCP wrapper's
   ``_run_roam_*`` bridge with ``_success_codes = {0, EXIT_GATE_FAILURE}``
   converts the non-zero exit to a generic ``COMMAND_FAILED`` envelope,
   burying the structured ``file_not_found`` signal:

       $ roam --json file does/not/exist.py
       {"command":"file","summary":{"verdict":"file not found:...",
        "error":"file_not_found"}, ...}
       <exit 1>   <-- MCP collapse risk

   In NON-json mode, the command emits plain text + exit 1 - the
   canonical Pattern-1B/C double-bug. Fix template: same as W362 on
   cmd_owner - exit 0 with structured envelope.

2. **Pattern-1 Variant D REAL BUG** - ``cmd_file.py:28-37``:
   ``_resolve_file`` silently falls back to ``LIKE '%{path}'``
   substring match when the exact ``FILE_BY_PATH`` query returns None.
   ``roam file real.py`` matches ``src/real.py`` and reports the
   degraded match as a confident success:

       $ roam --json file real.py    # 'real.py' is NOT the indexed path
       {"path":"src/real.py", "summary":{"verdict":"real.py: 1 symbols..."},
        "partial_success":false}      <-- silent fuzzy fallback

   No ``resolution`` field, no ``partial_success=True``, no disclosure
   that the result came from a degraded substring match. Exactly the
   Pattern-1 Variant D "silent success on degraded resolution" shape.

3. **Pattern-2 REAL BUG** - ``cmd_file.py:289-323``: when a file
   resolves cleanly but contains 0 symbols (e.g. an empty source
   file), the command emits a confident verdict
   ``"empty.py: 0 symbols (), 0 LOC"`` with ``partial_success: false``
   and NO ``state`` disclosure. The verdict reads identically to a
   "real file with intentionally 0 definitions". A consumer cannot
   distinguish "file empty because uncoded" from "file empty by
   design" from "extractor failed silently".

Test split (mirrors W805-K's three-bug pile-up template):

1. SMOKE (always-on assertions):
   * Clean-corpus regression: real file emits real signal
   * LAW 6: verdict is standalone single-line ASCII
   * Envelope shape (``command``, ``summary.verdict``)

2. PATTERN-1B/C PIN (xfail-strict):
   * Unresolved file in --json mode exits 0 (NOT 1)
   * Unresolved file in non-json mode emits structured signal
   * ``state == "file_not_found"`` on unresolved file

3. PATTERN-1 VARIANT D PIN (xfail-strict):
   * Fuzzy-fallback discloses ``resolution`` field
   * Fuzzy-fallback sets ``partial_success: true``

4. PATTERN-2 PIN (xfail-strict):
   * Empty-symbol file discloses ``state`` (closed enum like ``no_symbols``)
   * Empty-symbol file sets ``partial_success: true``
   * Verdict does NOT read as confident file description when symbols == 0

The W805-N fix lives in a separate wave; this module is intentionally
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
def empty_symbol_file_corpus(tmp_path, monkeypatch):
    """Indexed corpus with ``src/empty.py`` containing 0 symbols.

    The file row exists in the ``files`` table (so the path resolves
    cleanly) but contains no symbols. This is the canonical Pattern-2
    "file resolved, 0 symbols" silent-SAFE branch on
    ``cmd_file.py:289-323``.
    """
    repo = tmp_path / "empty-file-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    # Filename intentionally avoids the substring "empty" so the
    # Pattern-2 verdict-keyword check at test_no_silent_file_success
    # is not satisfied by the filename literal alone.
    (src / "blank.py").write_text("", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def fuzzy_fallback_file_corpus(tmp_path, monkeypatch):
    """Indexed corpus where ``src/real.py`` is the only real file.

    Probing ``roam file real.py`` (no ``src/`` prefix) triggers the
    SUBSTRING fuzzy fallback at ``cmd_file.py:33-36``: the primary
    exact ``FILE_BY_PATH`` query returns 0 rows, then the fallback
    ``LIKE '%real.py'`` matches ``src/real.py``. The Pattern-1
    Variant D silent-SAFE shape.
    """
    repo = tmp_path / "fuzzy-file-repo"
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
def real_file_corpus(tmp_path, monkeypatch):
    """Indexed corpus with real symbols in ``src/main.py`` for the
    happy-path positive-coverage baseline.
    """
    repo = tmp_path / "real-file-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n\ndef helper():\n    return 42\n",
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


def _invoke_file(target: str, json_mode: bool = True):
    """Run ``roam [--json] file <target>`` in-process and return result."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.extend(["file", target])
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


class TestFileEmptyCorpusSmoke:
    """Pattern-2 always-emit baseline assertions for ``roam file``."""

    def test_empty_corpus_no_crash(self, empty_symbol_file_corpus):
        """``roam file src/blank.py`` on 0-symbol file does not crash; exits 0."""
        result = _invoke_file("src/blank.py", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, empty_symbol_file_corpus):
        """0-symbol file emits ``command=file`` + non-empty verdict."""
        result = _invoke_file("src/blank.py", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "file"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_law6_verdict_standalone(self, empty_symbol_file_corpus):
        """LAW 6: verdict is a single line of ASCII."""
        result = _invoke_file("src/blank.py", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.isascii(), f"verdict is not plain ASCII: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict"), f"verdict is a placeholder: {verdict!r}"

    def test_clean_corpus_emits_real_file_info(self, real_file_corpus):
        """Happy-path positive coverage: a real file emits real signal.

        ``src/main.py`` with 2 functions (``main`` + ``helper``) produces:
        - path: src/main.py
        - symbols >= 1 (main + helper)
        - verdict mentions symbol count > 0
        """
        result = _invoke_file("src/main.py", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        # Verdict mentions symbols > 0
        verdict = summary["verdict"]
        assert "2 symbols" in verdict, f"happy-path verdict missing real symbol count: {verdict!r}"
        symbols = envelope.get("symbols") or []
        assert len(symbols) >= 1, f"happy-path should expose real symbols, got: {symbols!r}"
        sym_names = {s["name"] for s in symbols}
        assert "main" in sym_names or "helper" in sym_names, (
            f"happy-path expected 'main' or 'helper' in symbols, got {sym_names!r}"
        )

    def test_partial_success_key_present(self, empty_symbol_file_corpus):
        """``summary.partial_success`` key is auto-injected on every envelope.

        Note: this asserts the *key* is present (sealed today by
        ``json_envelope``). The Pattern-2 pin below asserts the *value*
        should be ``True`` on the empty branch.
        """
        result = _invoke_file("src/blank.py", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        assert "partial_success" in summary, (
            f"summary.partial_success key must be auto-injected; got summary keys = {sorted(summary.keys())}"
        )


# ---------------------------------------------------------------------------
# PATTERN-1B/C PIN: unresolved path in --json mode
# (cmd_file.py:285 raises SystemExit(1) even after emitting structured envelope)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-N REAL BUG 1 (Pattern-1B): cmd_file.py:285 raises "
        "SystemExit(1) on unresolved path EVEN AFTER emitting a "
        "structured JSON envelope. The MCP wrapper's _run_roam_* bridge "
        "with _success_codes = {0, EXIT_GATE_FAILURE} converts the "
        "non-zero exit to a generic COMMAND_FAILED envelope - burying "
        "the structured 'file_not_found' signal. Fix template: same as "
        "W362 on cmd_owner - exit 0 with structured envelope. "
        "Separate fix wave."
    ),
)
def test_nonexistent_file_exit_code_not_1_on_json(real_file_corpus):
    """Pin: unresolved file in --json mode SHOULD exit 0 with structured envelope.

    Mirrors the W362 ``file_not_found`` discipline on cmd_owner.
    """
    result = _invoke_file("does/not/exist.py", json_mode=True)
    assert result.exit_code == 0, (
        f"unresolved file should exit 0 with structured envelope; "
        f"got exit {result.exit_code}, output: {result.output!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-N REAL BUG 1 (Pattern-1B/C): cmd_file.py:286-287 emits "
        "plain text 'File not found in index: ...' and exit 1 in "
        "non-json mode. The Pattern-1B/C fix template requires "
        "structured signal even on the error path; non-json mode "
        "should at minimum include a recognisable VERDICT: line for "
        "consistency with the rest of the roam surface. "
        "Separate fix wave."
    ),
)
def test_nonexistent_file_emits_json_envelope_not_plain_text(real_file_corpus):
    """Pin: unresolved file in non-json mode should emit a structured signal.

    Today only --json mode emits the structured envelope; non-json mode
    drops to plain text. The Pattern-1B/C discipline says both modes
    should be consistent (verdict-first in non-json).
    """
    result = _invoke_file("does/not/exist.py", json_mode=False)
    # Non-json mode should still surface a recognisable verdict for
    # consistency with the rest of the roam surface.
    assert "VERDICT:" in (result.output or ""), (
        f"non-json mode should emit a VERDICT: line on file-not-found; got plain text: {result.output!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-N REAL BUG 1: cmd_file.py:271-284 emits a JSON envelope "
        "but does not set summary.state to a closed-enum value. "
        "Pattern-2 requires closed-enum state disclosure "
        "('file_not_found' to mirror cmd_owner's W362 contract). "
        "summary.error='file_not_found' exists but state is not "
        "promoted to the canonical envelope.state field. "
        "Separate fix wave."
    ),
)
def test_nonexistent_file_explicit_state(real_file_corpus):
    """Pin: unresolved file discloses ``state="file_not_found"`` (closed enum)."""
    result = _invoke_file("does/not/exist.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {"file_not_found", "path_not_found", "not_indexed"}
    assert state in accepted, (
        f"summary.state should disclose file-not-found state; got {state!r}; expected one of {accepted}"
    )


# ---------------------------------------------------------------------------
# PATTERN-1 VARIANT D PIN: silent fuzzy substring fallback
# (cmd_file.py:33-36 silently re-queries with 'LIKE %{path}' on miss)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-N REAL BUG 2 (Pattern-1 Variant D): cmd_file.py:33-36 "
        "(_resolve_file) silently re-queries with the SUBSTRING fuzzy "
        "pattern 'LIKE %{path}' when the primary FILE_BY_PATH exact "
        "match returns None. The verdict reports the matched path as a "
        "confident success even though the input did not match exactly. "
        "Canonical degraded-resolution silent SAFE: success verdict "
        "indistinguishable from a fully-resolved success. Fix: disclose "
        "via summary.resolution='fuzzy_substring' + partial_success=True + "
        "verdict that names the degraded match. Separate fix wave."
    ),
)
def test_fuzzy_match_discloses_resolution(fuzzy_fallback_file_corpus):
    """Pin: fuzzy substring fallback should disclose ``resolution``.

    Variant D requires explicit disclosure when a target resolves
    partially through a fallback chain.
    """
    # 'real.py' is the user's literal input; the indexed path is
    # 'src/real.py' - this is a degraded substring resolution.
    result = _invoke_file("real.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    resolution = summary.get("resolution") or envelope.get("resolution")
    accepted = {
        "fuzzy",
        "fuzzy_substring",
        "fuzzy_match",
        "substring_fallback",
        "degraded",
        "suffix_match",
    }
    assert resolution in accepted, (
        f"summary.resolution should disclose the fuzzy fallback; got {resolution!r}; expected one of {accepted}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-N REAL BUG 2 (Pattern-1 Variant D): cmd_file.py:33-36 "
        "fuzzy substring fallback emits partial_success=False, masking "
        "a degraded-resolution branch. Fix: partial_success=True when "
        "the suffix fallback matched. Separate fix wave."
    ),
)
def test_fuzzy_match_partial_success_set(fuzzy_fallback_file_corpus):
    """Pin: fuzzy fallback must set ``partial_success: True``."""
    result = _invoke_file("real.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"fuzzy-substring fallback must set partial_success=True; got summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# PATTERN-2 PIN: 0-symbol file silent SAFE
# (cmd_file.py:289-323 emits confident verdict for 0-symbol file)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-N REAL BUG 3 (Pattern-2): cmd_file.py:289-323 emits "
        "verdict 'empty.py: 0 symbols (), 0 LOC' with "
        "summary.partial_success=False when the file resolved but "
        "contains 0 symbols. A consumer cannot distinguish 'real empty "
        "file' from 'extractor failed silently' from 'file uncoded'. "
        "Fix: set partial_success=True + state='no_symbols' + verdict "
        "that names the empty state. Separate fix wave."
    ),
)
def test_zero_symbol_file_partial_success(empty_symbol_file_corpus):
    """Pin: ``summary.partial_success`` should be True on 0-symbol file."""
    result = _invoke_file("src/blank.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"0-symbol file branch must set partial_success=True; got summary={summary!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-N REAL BUG 3 (Pattern-2): cmd_file.py:289-323 does not "
        "emit a summary.state field on the 0-symbol branch. Pattern-2 "
        "requires closed-enum state disclosure. Acceptable: 'no_symbols', "
        "'empty_file', 'no_definitions'. Separate fix wave."
    ),
)
def test_zero_symbol_file_explicit_state(empty_symbol_file_corpus):
    """Pin: ``summary.state`` discloses the 0-symbol state."""
    result = _invoke_file("src/blank.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {"no_symbols", "empty_file", "no_definitions", "empty"}
    assert state in accepted, f"summary.state should disclose 0-symbol state; got {state!r}; expected one of {accepted}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-N REAL BUG 3 (Pattern-2): cmd_file.py:319 emits verdict "
        "'<file>: N symbols (<kinds>), N LOC' which reads as a "
        "confident file description when 0 symbols indicates a "
        "degraded state. The verdict should explicitly name the empty "
        "state, e.g. 'file src/empty.py empty: 0 symbols indexed'. "
        "Separate fix wave."
    ),
)
def test_no_silent_file_success(empty_symbol_file_corpus):
    """Pin: verdict must NOT read as confident file success on 0 symbols.

    Anti-shape: a verdict that reports symbol/LOC counts in the form
    ``"<file>: N symbols (), N LOC"`` without ANY indicator of the
    empty state.
    """
    result = _invoke_file("src/blank.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    verdict = (summary.get("verdict") or "").lower()
    is_zero_symbol = "0 symbols" in verdict
    flags_empty = any(token in verdict for token in ("empty", "no symbols", "no-symbols", "no definitions", "uncoded"))
    if is_zero_symbol:
        assert flags_empty, (
            f"silent-SAFE Pattern-2 shape: verdict reports 0 symbols "
            f"without naming the empty state. verdict={summary.get('verdict')!r}; "
            "verdict should mention 'empty' / 'no symbols' / 'no definitions'."
        )
