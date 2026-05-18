"""W805-K - empty-corpus smoke for ``roam module`` (W805 Pattern 2 sweep).

Eleventh-in-batch of the W805 Pattern-2 audit. Prior cohort:

- A (cmd_owner)            REAL BUG (silent "top owner: ?")
- B (cmd_minimap)          REAL BUG (silent "minimap rendered (148 chars)")
- C (cmd_oracle)           REAL BUG (verdict/metadata mismatch)
- D (cmd_workflow)         NO REAL BUG (static-metadata inspector)
- E (cmd_path_coverage)    NO REAL BUG (W807-hardened)
- F (cmd_for_bug_fix)      REAL BUG (_compound_envelope)
- G (cmd_pr_prep)          REAL BUG (silent READY + ASCII)
- H (cmd_explain_command)  NO REAL BUG (static-metadata) + 3 milder gaps
- I (cmd_describe)         in flight
- J (cmd_understand)       in flight
- K (cmd_module, this wave)

W978 first-hypothesis re-run BEFORE writing any test
============================================================

``cmd_module`` IS DB-querying. It calls ``ensure_index()`` + ``open_db()``
and queries the ``files``, ``symbols``, ``file_edges``, ``edges`` tables
to compute per-directory cohesion, API surface, and external coupling.
The empty-corpus probe is the right axis — but the W978 re-run surfaced
THREE distinct bug shapes, not one, across the resolution branches in
``src/roam/commands/cmd_module.py:135-151``:

1. **Pattern-1B/C REAL BUG** — `cmd_module.py:150-151`: when the path
   matches no indexed file, the command emits the plain-text line
   ``"No files found under: <path>/"`` and calls ``raise SystemExit(1)``
   EVEN IN ``--json`` MODE. This is the canonical Pattern-1B (structured
   signal lost) + Pattern-1C (empty/non-JSON stdout) double-bug:

       $ roam --json module does/not/exist
       No files found under: does/not/exist/   <-- not JSON
       <exit 1>

   The MCP wrapper's ``_run_roam_*`` bridge will try-parse this as JSON,
   fail, and collapse to a generic ``COMMAND_FAILED`` envelope — burying
   the actual "path not found" signal. The fix template is the same as
   W362 on ``cmd_owner``: emit a structured ``state="path_not_found"``
   envelope and exit 0.

2. **Pattern-1 Variant D REAL BUG** — `cmd_module.py:147-148, 164-165`:
   when the primary pattern ``"{path}/%"`` returns 0 rows, the command
   silently re-queries with the FUZZY pattern ``"%{path}/%"`` (substring
   match anywhere in the path). The verdict reports ``path: <input>``
   as if the original target resolved, even though files come from a
   different directory:

       $ roam --json module nested      # 'nested' is NOT a real dir
       {"path": "nested",
        "files": [{"path": "deep/nested/x.py", ...}],   <-- different dir!
        "summary": {"verdict": "nested/: 1 files, ...",
                    "partial_success": false}}          <-- silent SAFE

   No ``resolution`` field, no ``partial_success=True``, no disclosure
   that the result came from a degraded fuzzy fallback. Exactly the
   Pattern-1 Variant D "silent success on degraded resolution" shape.

3. **Pattern-2 REAL BUG** — `cmd_module.py:176-208`: when a directory
   resolves cleanly but contains 0 symbols (e.g. only empty files or
   non-source assets), the command emits a confident verdict
   ``"src/: 1 files, 0 symbols, 0 importers"`` with
   ``partial_success: false``, ``cohesion_pct: 0``, ``api_surface_pct: 0``
   and NO ``state`` disclosure. The verdict reads identically to a
   "real module with intentionally 0 exports". A consumer cannot
   distinguish "module empty because uncoded" from "module empty by
   design" from "module empty because indexing failed".

Test split (mirrors W805-A / W805-B baseline-plus-xfail-pin discipline):

1. SMOKE (always-on assertions):
   * Clean-corpus regression: real module emits real signal
   * LAW 6: verdict is standalone single-line ASCII
   * Envelope shape (``command``, ``summary.verdict``)

2. PATTERN-1B/C PIN (xfail-strict):
   * Unresolved path emits JSON envelope in --json mode (NOT plain text)
   * Unresolved path exits 0 (NOT 1)
   * ``state == "path_not_found"`` on unresolved path

3. PATTERN-1 VARIANT D PIN (xfail-strict):
   * Fuzzy-fallback discloses ``resolution`` field
   * Fuzzy-fallback sets ``partial_success: true``

4. PATTERN-2 PIN (xfail-strict):
   * Empty module discloses ``state`` (closed enum like ``no_symbols``)
   * Empty module sets ``partial_success: true``
   * Verdict does NOT read as a confident module description when
     symbols == 0

The W805-K fix lives in a separate wave; this module is intentionally
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
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(repo),
        capture_output=True,
        env=env,
        check=True,
    )


@pytest.fixture
def empty_module_corpus(tmp_path, monkeypatch):
    """Indexed corpus with one empty file in ``src/``.

    The ``src/`` directory exists in the ``files`` table (so the path
    resolves) but contains 0 symbols. This is the canonical Pattern-2
    "module resolved cleanly, 0 symbols" silent-SAFE branch on
    ``cmd_module.py:176-208``.
    """
    repo = tmp_path / "empty-module-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    (src / "empty.py").write_text("", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def fuzzy_fallback_corpus(tmp_path, monkeypatch):
    """Indexed corpus where ``deep/nested/`` is the only real dir.

    Probing ``roam module nested`` triggers the SUBSTRING fuzzy fallback
    at ``cmd_module.py:147-148``: the primary ``"nested/%"`` pattern
    returns 0 rows, then the fallback ``"%nested/%"`` matches files
    under ``deep/nested/``. The Pattern-1 Variant D silent-SAFE shape.
    """
    repo = tmp_path / "fuzzy-module-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    nested = repo / "deep" / "nested"
    nested.mkdir(parents=True)
    (nested / "x.py").write_text("def x():\n    return 1\n", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def real_module_corpus(tmp_path, monkeypatch):
    """Indexed corpus with real symbols under ``src/`` for the
    happy-path positive-coverage baseline.
    """
    repo = tmp_path / "real-module-repo"
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


def _invoke_module(target: str, json_mode: bool = True):
    """Run ``roam [--json] module <target>`` in-process and return result."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.extend(["module", target])
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


class TestModuleEmptyCorpusSmoke:
    """Pattern-2 always-emit baseline assertions for ``roam module``."""

    def test_empty_corpus_no_crash(self, empty_module_corpus):
        """``roam module src`` on empty corpus does not crash; exits 0."""
        result = _invoke_module("src", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, empty_module_corpus):
        """Empty module emits ``command=module`` + non-empty verdict."""
        result = _invoke_module("src", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "module"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_law6_verdict_standalone(self, empty_module_corpus):
        """LAW 6: verdict is a single line of ASCII."""
        result = _invoke_module("src", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.isascii(), f"verdict is not plain ASCII: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict"), f"verdict is a placeholder: {verdict!r}"

    def test_clean_corpus_emits_real_module_info(self, real_module_corpus):
        """Happy-path positive coverage: a real module emits real signal.

        ``src/`` with 2 functions in ``main.py`` produces:
        - 1 file
        - 2 symbols (``main`` + ``helper``)
        - cohesion > 0 (one internal edge: ``main -> helper``)
        """
        result = _invoke_module("src", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        assert summary["file_count"] == 1
        # Verdict mentions the symbols count > 0.
        verdict = summary["verdict"]
        assert "2 symbols" in verdict, f"happy-path verdict missing real symbol count: {verdict!r}"
        # At least one internal symbol present.
        symbols = envelope.get("symbols") or []
        assert len(symbols) >= 1, f"happy-path should expose real symbols, got: {symbols!r}"
        sym_names = {s["name"] for s in symbols}
        assert "main" in sym_names or "helper" in sym_names, (
            f"happy-path expected 'main' or 'helper' in symbols, got {sym_names!r}"
        )

    def test_empty_corpus_partial_success_key_present(self, empty_module_corpus):
        """``summary.partial_success`` key is auto-injected on every envelope.

        Note: this asserts the *key* is present (sealed today by
        ``json_envelope``). The Pattern-2 pin below asserts the *value*
        should be ``True`` on the empty branch.
        """
        result = _invoke_module("src", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        assert "partial_success" in summary, (
            f"summary.partial_success key must be auto-injected; got summary keys = {sorted(summary.keys())}"
        )


# ---------------------------------------------------------------------------
# PATTERN-1B/C PIN: unresolved path in --json mode
# (cmd_module.py:150-151 emits plain text + SystemExit(1))
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-K REAL BUG 1 (Pattern-1B+1C): cmd_module.py:150-151 emits "
        "plain text 'No files found under: <path>/' and calls "
        "raise SystemExit(1) EVEN IN --json mode. The MCP wrapper's "
        "_run_roam_* bridge will try-parse this as JSON, fail, and "
        "collapse to a generic COMMAND_FAILED envelope - burying the "
        "structured 'path not found' signal. Fix template: same as W362 "
        "on cmd_owner - emit state='path_not_found' envelope + exit 0. "
        "Separate fix wave."
    ),
)
def test_unresolved_path_emits_json_envelope_in_json_mode(empty_module_corpus):
    """Pin: unresolved path MUST emit a JSON envelope in --json mode.

    Pattern-1C says: always emit a structured envelope from the CLI
    even on no-results. Pattern-1B says: non-zero exit + JSON stdout is
    survivable; non-zero exit + non-JSON stdout is not.
    """
    result = _invoke_module("does/not/exist", json_mode=True)
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), (
        f"--json mode must emit a JSON envelope on unresolved path; got plain text: {result.output!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-K REAL BUG 1 (Pattern-1B): cmd_module.py:151 raises "
        "SystemExit(1) on unresolved path. The W362 / W805-A precedent "
        "on cmd_owner is to exit 0 with state='path_not_found'. "
        "Separate fix wave."
    ),
)
def test_unresolved_path_exits_zero(empty_module_corpus):
    """Pin: unresolved path SHOULD exit 0 with structured envelope.

    Mirrors the W362 ``path_not_found`` discipline on cmd_owner.
    """
    result = _invoke_module("does/not/exist", json_mode=True)
    assert result.exit_code == 0, (
        f"unresolved path should exit 0 with structured envelope; "
        f"got exit {result.exit_code}, output: {result.output!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-K REAL BUG 1: cmd_module.py:150-151 does not emit a "
        "summary.state field on the path-not-found branch. Pattern-2 "
        "requires closed-enum state disclosure ('path_not_found' to "
        "mirror cmd_owner's W362 contract). Separate fix wave."
    ),
)
def test_unresolved_path_explicit_state(empty_module_corpus):
    """Pin: unresolved path discloses ``state="path_not_found"``."""
    result = _invoke_module("does/not/exist", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {"path_not_found", "directory_not_found", "module_not_found"}
    assert state in accepted, (
        f"summary.state should disclose path-not-found state; got {state!r}; expected one of {accepted}"
    )


# ---------------------------------------------------------------------------
# PATTERN-1 VARIANT D PIN: silent fuzzy substring fallback
# (cmd_module.py:147-148 silently re-queries with '%{path}/%' on miss)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-K REAL BUG 2 (Pattern-1 Variant D): cmd_module.py:147-148 "
        "silently re-queries with the SUBSTRING fuzzy pattern "
        "'%{path}/%' when the primary '{path}/%' returns 0 rows. The "
        "verdict reports the requested input as 'path' even though the "
        "files come from a different directory. Canonical degraded-"
        "resolution silent SAFE: success verdict indistinguishable "
        "from a fully-resolved success. Fix: disclose via "
        "summary.resolution='fuzzy_substring' + partial_success=True + "
        "verdict that names the degraded match. Separate fix wave."
    ),
)
def test_fuzzy_fallback_discloses_resolution(fuzzy_fallback_corpus):
    """Pin: fuzzy substring fallback should disclose ``resolution``.

    Variant D requires explicit disclosure when a target resolves
    partially through a fallback chain.
    """
    result = _invoke_module("nested", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    resolution = summary.get("resolution") or envelope.get("resolution")
    accepted = {
        "fuzzy",
        "fuzzy_substring",
        "fuzzy_match",
        "substring_fallback",
        "degraded",
    }
    assert resolution in accepted, (
        f"summary.resolution should disclose the fuzzy fallback; got {resolution!r}; expected one of {accepted}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-K REAL BUG 2 (Pattern-1 Variant D): cmd_module.py:147-148 "
        "fuzzy substring fallback emits partial_success=False, masking "
        "a degraded-resolution branch. Fix: partial_success=True when "
        "the fuzzy fallback matched. Separate fix wave."
    ),
)
def test_fuzzy_fallback_partial_success_set(fuzzy_fallback_corpus):
    """Pin: fuzzy fallback must set ``partial_success: True``."""
    result = _invoke_module("nested", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"fuzzy-substring fallback must set partial_success=True; got summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# PATTERN-2 PIN: empty module silent SAFE
# (cmd_module.py:176-208 emits confident verdict for 0-symbol module)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-K REAL BUG 3 (Pattern-2): cmd_module.py:176-208 emits "
        "verdict 'src/: 1 files, 0 symbols, 0 importers' with "
        "summary.partial_success=False when the module resolved but "
        "contains 0 symbols. A consumer cannot distinguish 'real empty "
        "module' from 'indexing failed' from 'module uncoded'. Fix: "
        "set partial_success=True + state='no_symbols' + verdict that "
        "names the empty state. Separate fix wave."
    ),
)
def test_empty_module_partial_success_set(empty_module_corpus):
    """Pin: ``summary.partial_success`` should be True on 0-symbol module."""
    result = _invoke_module("src", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"empty-module branch must set partial_success=True; got summary={summary!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-K REAL BUG 3 (Pattern-2): cmd_module.py:176-208 does not "
        "emit a summary.state field on the 0-symbol branch. Pattern-2 "
        "requires closed-enum state disclosure. Acceptable: 'no_symbols', "
        "'empty_module', 'no_exports'. Separate fix wave."
    ),
)
def test_empty_module_explicit_state(empty_module_corpus):
    """Pin: ``summary.state`` discloses the 0-symbol state."""
    result = _invoke_module("src", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {"no_symbols", "empty_module", "no_exports", "empty_corpus"}
    assert state in accepted, f"summary.state should disclose 0-symbol state; got {state!r}; expected one of {accepted}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-K REAL BUG 3 (Pattern-2): cmd_module.py:177 emits verdict "
        "'<path>/: N files, 0 symbols, 0 importers' which reads as a "
        "confident module description when 0 symbols indicates a "
        "degraded state. The verdict should explicitly name the empty "
        "state, e.g. 'module src/ empty: 0 symbols indexed across 1 "
        "files'. Separate fix wave."
    ),
)
def test_no_silent_module_success(empty_module_corpus):
    """Pin: verdict must NOT read as confident module success on 0 symbols.

    Anti-shape: a verdict that reports symbol/file/importer counts in
    the form ``"<path>/: N files, 0 symbols, 0 importers"`` without
    ANY indicator of the empty state.
    """
    result = _invoke_module("src", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    verdict = (summary.get("verdict") or "").lower()
    # Silent-SAFE shape: verdict reports zero symbols without flagging
    # the empty state explicitly.
    is_zero_symbol = "0 symbols" in verdict
    flags_empty = any(token in verdict for token in ("empty", "no symbols", "no-symbols", "no exports", "uncoded"))
    if is_zero_symbol:
        assert flags_empty, (
            f"silent-SAFE Pattern-2 shape: verdict reports 0 symbols "
            f"without naming the empty state. verdict={summary.get('verdict')!r}; "
            "verdict should mention 'empty' / 'no symbols' / 'no exports'."
        )
