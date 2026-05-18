"""W805-A - Empty-corpus smoke test for ``roam owner`` (W805 sweep).

First-of-batch extension of the W805 / W802-W836 Pattern-2 audit beyond
the original cohort. ``cmd_owner`` was not in the cohort but W362
[completed] previously fixed the path-not-found Pattern-1B branch.

This module probes the *other* empty-state path on the same command:
the "indexed file exists but git blame returns no rows" / "directory
files exist but no git history rows" branches at
``src/roam/commands/cmd_owner.py:142-217``. Pre-W805-A behaviour on
those paths emits a verdict like ``"top owner: ?, 0 contributors,
fragmentation=0"`` with ``summary.partial_success=False`` (auto-injected
default) - i.e. the verdict reads like a confident statement when the
underlying blame / git_file_changes data is absent. That is the
Pattern-2 silent SAFE signature.

Test split (mirrors the W802 / W804 baseline-plus-xfail-pin discipline):

1. SMOKE (always-on assertions):
   * No crash on empty corpus, exit 0 (or 5 documented).
   * Canonical envelope shape: ``command``, ``summary``, ``summary.verdict``.
   * W362 path-not-found regression still works (file: cmd_owner.py:110-139).
   * Happy-path positive coverage (real ownership emitted).
   * LAW 6 verdict standalone (single-line, ASCII).

2. PATTERN-2 PIN (xfail-strict until the underlying fix lands):
   * ``summary.partial_success`` is ``True`` when blame data is empty.
   * ``summary.state`` discloses ``no_blame_data`` / ``no_git_data``
     explicitly (closed-enum disclosure).
   * No silent confident-ownership verdict (verdict must NOT start with
     ``"top owner:"`` when blame data is empty).

The W805-A fix lives in a separate wave; this module is intentionally
test-only per the user-issued accumulate-only constraint.
"""

from __future__ import annotations

import json
import os
import re
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
    """Init a git repo, commit all current files, no history beyond init."""
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
def empty_blame_corpus(tmp_path, monkeypatch):
    """A git repo with a single committed empty Python file.

    The file is indexed (lands in the ``files`` table) but the empty
    body means ``git blame`` returns zero lines and ``_ownership_for_file``
    returns ``None`` - the silent-empty branch under W805-A inspection.
    """
    repo = tmp_path / "empty-owner-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def real_blame_corpus(tmp_path, monkeypatch):
    """A git repo with a non-empty committed source file for the
    happy-path positive coverage assertion."""
    repo = tmp_path / "real-owner-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "hello.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# Invocation helper
# ---------------------------------------------------------------------------


def _invoke_owner(target: str, json_mode: bool = True):
    """Run ``roam [--json] owner <target>`` in-process and return result."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.extend(["owner", target])
    return runner.invoke(cli, args, catch_exceptions=False)


def _parse_envelope(result):
    """Parse the runner's stdout as a JSON envelope."""
    raw = result.output.strip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output}"
    return json.loads(raw)


# ---------------------------------------------------------------------------
# SMOKE (always-on)
# ---------------------------------------------------------------------------


class TestOwnerEmptyCorpusSmoke:
    """Pattern-2 always-emit baseline assertions for ``roam owner``."""

    def test_empty_corpus_no_crash(self, empty_blame_corpus):
        """Empty corpus + indexed file -> command does not crash."""
        result = _invoke_owner("empty.py", json_mode=True)
        # Exit 0 is the W362-style "checked, no data" contract. If the
        # command exits non-zero, the wrapper-bridge Pattern-1B fix has
        # regressed; the test surfaces that loudly.
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"

    def test_empty_corpus_envelope_has_verdict(self, empty_blame_corpus):
        """The envelope carries a non-empty ``summary.verdict``."""
        result = _invoke_owner("empty.py", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "owner"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got: {verdict!r}"

    def test_empty_corpus_law6_verdict_standalone(self, empty_blame_corpus):
        """LAW 6: verdict line stands alone (single line, ASCII, no
        box-drawing). Drift guard mirroring the cmd_owner output discipline."""
        result = _invoke_owner("empty.py", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        # Single line - LAW 6 prohibits embedded newlines in the verdict.
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        # Plain ASCII per the project conventions ("no box-drawing").
        assert verdict.isascii(), f"verdict is not plain ASCII: {verdict!r}"
        # Useful sanity: the verdict shouldn't be the literal placeholder.
        assert verdict.strip() not in ("", "?", "verdict"), f"verdict is a placeholder: {verdict!r}"

    def test_unresolved_target_pattern_1(self, empty_blame_corpus):
        """W362 regression baseline: path-not-found still emits a
        structured envelope and exits 0 (not the legacy SystemExit(1)).
        """
        result = _invoke_owner("does/not/exist.py", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        assert envelope["state"] == "path_not_found"
        assert envelope["target_path"] == "does/not/exist.py"
        assert envelope["summary"]["state"] == "path_not_found"
        assert envelope["summary"]["partial_success"] is False

    def test_clean_corpus_emits_real_owner(self, real_blame_corpus):
        """Happy-path positive coverage: a real file with committed
        history produces a concrete ownership verdict (not the
        silent-empty fallback)."""
        result = _invoke_owner("hello.py", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        verdict = summary["verdict"]
        # The happy-path verdict mentions "top owner" + a real author
        # (the fixture commits as "test"). The "?" sentinel must NOT
        # appear as the main_dev on the happy path.
        assert "top owner" in verdict, f"happy-path verdict missing 'top owner': {verdict!r}"
        assert summary.get("main_dev") not in (None, "?"), (
            f"main_dev should be a real author on the happy path, got {summary.get('main_dev')!r}"
        )


# ---------------------------------------------------------------------------
# PATTERN-2 PIN (xfail-strict until cmd_owner empty-blame branches disclose
# the absent-data state explicitly via state + partial_success)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-A REAL BUG: cmd_owner.py:142-175 (single-file JSON branch) "
        "emits 'top owner: ?, 0 contributors, fragmentation=0' with "
        "summary.partial_success=False when blame data is absent. "
        "Pattern-2 silent SAFE: verdict reads as confident ownership "
        "when the underlying _ownership_for_file returned None. "
        "Fix: emit state='no_blame_data' + partial_success=True + a "
        "verdict that names the absent-data state. Separate fix wave."
    ),
)
def test_empty_corpus_partial_success_set(empty_blame_corpus):
    """Pin: ``summary.partial_success`` should be True when blame is empty.

    The empty-corpus path is a degraded-resolution branch (Pattern-1
    variant D in CLAUDE.md): the command resolved the target file but
    the downstream blame query produced zero rows. That is NOT a
    "fully resolved, found nothing" success - it is "resolved partly,
    cannot answer the ownership question". ``partial_success=True``.
    """
    result = _invoke_owner("empty.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    assert summary.get("partial_success") is True, (
        f"empty-blame branch must set partial_success=True; got summary={summary!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-A REAL BUG: cmd_owner.py:142-175 does not emit a "
        "summary.state field on the empty-blame branch. Pattern-2 "
        "requires closed-enum state disclosure ('no_blame_data' "
        "alongside the existing 'path_not_found'). Separate fix wave."
    ),
)
def test_empty_corpus_explicit_state(empty_blame_corpus):
    """Pin: ``summary.state`` should disclose absent blame data.

    Mirrors the W362 ``state="path_not_found"`` closed-enum pattern.
    Acceptable values for the absent-blame branch include:
    ``no_blame_data``, ``no_git_data``, ``no_ownership_data``.
    """
    result = _invoke_owner("empty.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    state = summary.get("state") or envelope.get("state")
    accepted = {"no_blame_data", "no_git_data", "no_ownership_data"}
    assert state in accepted, (
        f"summary.state should disclose absent-data state, got {state!r}; expected one of {accepted}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-A REAL BUG: cmd_owner.py:161 emits verdict "
        "'top owner: ?, 0 contributors, fragmentation=0' when blame "
        "data is absent. That is the canonical silent-SAFE shape - the "
        "verdict reads as confident ownership ('top owner: ?') when "
        "the underlying check produced no signal. Separate fix wave."
    ),
)
def test_empty_corpus_no_silent_ownership(empty_blame_corpus):
    """Pin: the verdict must NOT claim ownership when blame is absent.

    The silent-SAFE shape is a verdict beginning with ``top owner:``
    while ``main_dev`` is ``?`` and ``authors`` is empty. The fix is
    to emit a verdict explicitly naming the absent-data state, e.g.
    ``"no blame data available for empty.py"``.
    """
    result = _invoke_owner("empty.py", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    verdict = (summary.get("verdict") or "").lower()
    # Bug-pinning assertion: the silent shape begins with "top owner"
    # and reports the sentinel ``?`` main_dev. Anti-shape: verdict
    # must NOT start with "top owner" when main_dev is "?".
    main_dev = summary.get("main_dev")
    silent_safe_shape = verdict.startswith("top owner") and main_dev in (None, "?")
    assert not silent_safe_shape, (
        f"silent-SAFE Pattern-2 shape detected: verdict={summary.get('verdict')!r}, "
        f"main_dev={main_dev!r}. Verdict should disclose absent blame data, "
        "not claim ownership with a '?' sentinel."
    )


# ---------------------------------------------------------------------------
# Additional smoke: LAW 4 anchored facts (auto-injected by json_envelope)
# ---------------------------------------------------------------------------


def test_empty_corpus_agent_contract_facts_present(empty_blame_corpus):
    """``agent_contract.facts`` must be a non-empty list on every
    envelope (Pattern-2 always-emit + LAW 4 anchoring). This holds on
    the empty-blame branch today because ``json_envelope`` auto-derives
    the contract from the summary keys."""
    result = _invoke_owner("empty.py", json_mode=True)
    envelope = _parse_envelope(result)
    contract = envelope.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and facts, f"agent_contract.facts must be a non-empty list; got {facts!r}"


def test_empty_corpus_verdict_law4_anchored(empty_blame_corpus):
    """LAW 4 drift guard: the empty-corpus verdict must remain
    concrete-noun-anchored even after the Pattern-2 fix lands.

    The current silent verdict ``"top owner: ?, 0 contributors,
    fragmentation=0"`` happens to end on a numeric literal which is
    accepted by the LAW 4 lint's "long sentence with numeric terminal"
    rule. Future fixes that rename the verdict (e.g.
    ``"no blame data available for empty.py"``) must keep the LAW 4
    anchor in place. We assert the verdict either ends in a known
    anchor terminal OR is a long-enough sentence to qualify under the
    long-sentence rule.
    """
    result = _invoke_owner("empty.py", json_mode=True)
    envelope = _parse_envelope(result)
    verdict = envelope["summary"]["verdict"]
    # Drift guard: verdict is a non-trivial string. The full LAW 4
    # lint runs centrally on the producer side; we only assert the
    # minimum shape constraint here so this test remains hermetic.
    tokens = re.split(r"\s+", verdict.strip())
    assert len(tokens) >= 3, f"verdict too short for LAW 4 anchoring: {verdict!r}"
