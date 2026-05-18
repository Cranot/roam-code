"""W805-DD -- empty-corpus Pattern-2 smoke test on ``roam history-grep``.

Thirtieth-in-batch W805 sweep. Git-pickaxe / "did this exist?" sibling of
the W805-W (refs-text) and W805-Z (delete-check) empty-corpus probes.
Per CLAUDE.md, ``roam history-grep`` is "git pickaxe (-S/-G) with
author/date and introduced/removed annotation" -- it answers "when did
this string first appear?" / "when was it removed?".

CRITICAL agent-safety class
---------------------------

The dangerous verdict shape in cmd_history_grep is the zero-commits
path: ``"0 commit(s) across 0/N pattern(s)"``. An agent reading this
on a string like ``DATABASE_URL`` or ``deprecated_api`` would
reasonably conclude "this string never existed in git history" --
which, in a corpus that's actually a fresh / shallow / non-git clone
or an environment with git unreachable per-pattern AFTER the
``_GIT_*`` sentinel fires only when ``returncode != 0`` (NOT on
genuine zero-match), is observationally indistinguishable from
"git couldn't scan it".

Existing partial loud-disclosure work (CP45/CP46, see source comments
at lines 30-37 + 260-275): the ``git_errors`` field DOES disclose when
git itself is missing / timed out / errored. That's good. The gap that
remains is the **silent zero-commits-on-empty-history path**, where
the git invocation SUCCEEDS (returncode 0, empty stdout) but the
underlying signal is "no commits exist in this repo yet" -- which an
agent cannot distinguish from "string is genuinely absent across a
rich history".

Scope
-----

cmd_history_grep has three zero-commits emission paths:

1. Genuine no-history (1-commit fresh repo, pattern absent): git
   succeeds, returns zero rows. Envelope:
   ``verdict: "0 commit(s) across 0/1 pattern(s)"``,
   ``partial_success: false``, ``git_errors: null``. **Pattern-2
   silent verdict candidate.**

2. Git invocation failed (rc != 0, FileNotFoundError, timeout). The
   ``_GIT_*`` sentinels propagate to ``git_errors{}``; verdict is
   correctly lifted to ``"history search unavailable: <kind>"``
   (lines 265-274). This path is loud and correct -- not a bug.

3. ``--polarity`` annotation on the zero-commits path: silently
   omitted because the inner ``for c in commits`` loop has no
   iterations. An agent that passed ``--polarity`` and got zero
   commits cannot tell "no commits to annotate" from "polarity
   feature degraded silently". The envelope has no
   ``polarity_requested`` / ``polarity_applied`` field.

W978 first-hypothesis check
---------------------------

First hypothesis: ``history_grep_cmd`` emits silent
"0 commit(s) across 0/N pattern(s)" verdict on the genuine
zero-matches path with no ``state`` / ``partial_success`` disclosure.

Probe result on the live tree (this commit, isolation run):

* Empty corpus (only README.md, 1 git commit) +
  ``--json history-grep NONEXISTENT``: exit 0,
  ``summary.partial_success: false``, no ``state`` field,
  ``git_errors: null``, ``results[0].commits: []``,
  ``verdict: "0 commit(s) across 0/1 pattern(s)"``. Pattern-2 silent
  verdict confirmed.

* Same corpus + ``--polarity NONEXISTENT``: identical envelope --
  no ``polarity`` field on any commit (because there are no commits),
  no disclosure that ``--polarity`` was requested. Silent degraded
  feature flag.

* Clean corpus (DATABASE_URL referenced + committed): pickaxe
  correctly returns 1 commit with sha + author + date + summary.
  Verdict "1 commit(s) across 1/1 pattern(s)". Positive-path
  regression remains green.

Severity comparison vs W805-W / W805-Z
---------------------------------------

* **W805-W refs-text** emits ``SAFE-TO-REMOVE`` on empty corpus --
  the verdict is directly destructive ("you can delete this").
  CRITICAL.

* **W805-Z delete-check** emits ``SAFE`` overall + admits the
  deletion through the CI gate (exit 0 instead of exit 5) on empty
  corpus. CRITICAL -- gates a destructive action.

* **W805-DD history-grep** emits "0 commit(s)" -- the agent-safety
  severity is **MEDIUM**, not CRITICAL. ``history-grep`` doesn't
  gate any action; it's a postmortem / provenance tool. But an
  agent could still draw the wrong conclusion from "0 commits" on
  a shallow / empty repo: "this string never existed, so removing
  it is fine" -- which is exactly the kind of inference Pattern-2
  is supposed to block.

Conclusion
----------

* **REAL BUG pinned: Pattern-2 silent zero-commits verdict on
  empty/shallow history** (src/roam/commands/cmd_history_grep.py:
  258-294 ``history_grep_cmd`` verdict + envelope assembly). The
  genuine zero-matches path emits a verdict observationally
  indistinguishable from a productive scan that found nothing. An
  agent reading the envelope cannot tell "we scanned a 5000-commit
  history" from "we scanned a 1-commit fresh repo". Pinned strict
  so a future cleanup that adds ``state: "empty_history"`` /
  ``commits_scanned: N`` graduates this to PASS.

* **Shape parity (mild)**: ``--polarity`` is requested but produces
  no observable signal on the zero-commits path. The envelope has
  no ``polarity_requested`` field that an agent could use to detect
  "we asked for annotation but couldn't apply it". Pinned strict
  for symmetry.

Sweep brief: W805-DD (Wave805-DD, thirtieth-in-batch).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 -- relative-to-tests-dir import after sys.path mutation
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_history(tmp_path):
    """Fresh git repo with a single init commit -- no relevant history.

    Exercises the genuine zero-matches path: git succeeds, returns zero
    rows. The kind of corpus an agent encounters on a fresh CI clone or
    a sparse / shallow checkout.
    """
    proj = tmp_path / "empty_history"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("Empty history project.\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def clean_history(tmp_path):
    """Repo with a real commit that introduces a target string.

    Exercises the positive-path: pickaxe should return >= 1 commit
    for the introduced string, with sha + author + date populated.
    """
    proj = tmp_path / "clean_history"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text("DATABASE_URL = 'postgresql://localhost'\n\ndef get_db():\n    return DATABASE_URL\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash / no empty stdout on the empty path.
# ---------------------------------------------------------------------------


class TestEmptyCorpusNoCrash:
    """The zero-commits envelope must always be structured, never crash
    and never emit empty stdout (Pattern-1 Variant C)."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_history, monkeypatch):
        """No exception / non-empty stdout on the zero-matches path."""
        monkeypatch.chdir(empty_history)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_history,
            json_mode=True,
        )
        # history-grep exits 0 on zero-commits (it's a successful pickaxe
        # that concluded the string has no history).
        assert result.exit_code == 0, (
            f"history-grep must exit 0 on zero-matches; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on zero-matches path"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_history, monkeypatch):
        """Envelope carries a non-empty summary verdict per LAW 6."""
        monkeypatch.chdir(empty_history)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_history,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        assert "summary" in data, f"envelope missing summary: {data}"
        assert "verdict" in data["summary"], f"summary missing verdict: {data['summary']}"
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict.strip()
        # Existing shape: "N commit(s) across M/P pattern(s)".
        assert "commit" in verdict.lower() and "pattern" in verdict.lower(), (
            f"summary verdict must mention commits + patterns; got {verdict!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_history, monkeypatch):
        """LAW 6: summary verdict works without any other field.

        Positive shape lint: the existing verdict "N commit(s) across M/P
        pattern(s)" is concrete-noun-anchored on ``pattern(s)`` and works
        standalone. Pin so a future refactor that drops the count from
        the verdict text breaks this.
        """
        monkeypatch.chdir(empty_history)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_history,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        verdict = data["summary"].get("verdict", "")
        assert verdict.strip(), "verdict empty"
        # Must name the pattern count (LAW 6: useful without other fields).
        assert "1" in verdict, f"verdict must name the pattern count; got {verdict!r}"
        # Concrete-noun anchor: ends on "pattern(s)".
        assert "pattern" in verdict.lower(), f"verdict must anchor on 'pattern' (LAW 4 anchor); got {verdict!r}"


# ---------------------------------------------------------------------------
# Pattern-2 silent verdict on the zero-commits path.
# REAL BUG pinned strict -- MEDIUM agent-safety class peer of W805-W/Z.
# ---------------------------------------------------------------------------


class TestEmptyCorpusSilentVerdict:
    """Pattern-2 silent zero-commits verdict on empty/shallow history.

    Distinct from the W805-W refs-text / W805-Z delete-check CRITICAL
    cases (those gate destructive action); history-grep is a postmortem
    tool, so severity is MEDIUM. But the same Pattern-2 contract
    applies: the envelope must distinguish "genuinely no history" from
    "couldn't scan".
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-DD REAL BUG: src/roam/commands/cmd_history_grep.py:258-294 "
            '(``history_grep_cmd``) emits ``verdict: "0 commit(s) across '
            '0/N pattern(s)"`` on the genuine zero-matches path with no '
            "``summary.state`` disclosure. An agent switching on "
            "machine-readable state cannot tell 'pattern truly absent "
            "across rich history' from 'shallow / fresh repo with nothing "
            "to scan'. MEDIUM agent-safety class -- not directly "
            "destructive but invites incorrect provenance inference. "
            "Pinned strict so a future cleanup that adds "
            '``state: "empty_history"`` (or equivalent) on the '
            "zero-matches path graduates this to PASS."
        ),
    )
    def test_empty_corpus_state_explicit(self, cli_runner, empty_history, monkeypatch):
        """Empty-history zero-matches discloses ``state`` explicitly."""
        monkeypatch.chdir(empty_history)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_history,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        summary = data["summary"]
        state = summary.get("state")
        # Accept any explicit non-empty state -- the contract is "explicit",
        # not "named X". Today this field is absent entirely.
        assert state is not None and isinstance(state, str) and state.strip(), (
            f"W805-DD Pattern-2 silent verdict: empty-history zero-matches must "
            f"emit summary.state to distinguish 'truly absent' from 'no history "
            f"to scan'; got {state!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-DD REAL BUG: src/roam/commands/cmd_history_grep.py:258-294 "
            "emits ``partial_success: false`` on the zero-commits / "
            "no-history path. When the underlying outcome is 'no commits "
            "exist in this repo' the canonical Pattern-2 contract sets "
            "partial_success=True so an agent can detect degradation. "
            "Today the genuine no-history path looks identical to a "
            "successful scan that found nothing. Pinned strict."
        ),
    )
    def test_empty_corpus_partial_success_set(self, cli_runner, empty_history, monkeypatch):
        """Pattern-2 guard: zero-commits empty-history sets partial_success=True."""
        monkeypatch.chdir(empty_history)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_history,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        summary = data["summary"]
        assert summary.get("partial_success") is True, (
            f"W805-DD Pattern-2: zero-commits empty-history must set "
            f"partial_success=True; got {summary.get('partial_success')!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-DD REAL BUG -- MEDIUM agent-safety class, peer of "
            "W805-W (refs-text SAFE-TO-REMOVE) and W805-Z (delete-check "
            "SAFE): src/roam/commands/cmd_history_grep.py:272 emits "
            '``verdict: "0 commit(s) across 0/N pattern(s)"`` '
            "unconditionally on the zero-matches branch. An agent "
            "reading this against an unindexed / shallow / fresh corpus "
            "could draw the wrong provenance conclusion: 'this string "
            "never appeared in history, so removing/migrating it is "
            "fine'. The canonical contract on a zero-matches path with "
            "no scannable history should explicitly disclose the "
            "no-history condition (state='empty_history' or equivalent) "
            "so agents can switch on it. Pinned strict so the fix "
            "graduates to PASS."
        ),
    )
    def test_no_silent_no_matches_on_empty(self, cli_runner, empty_history, monkeypatch):
        """MEDIUM: '0 commit(s)' on unscannable history is agent-misleading.

        Either the verdict gains an explicit no-history qualifier OR
        the envelope discloses ``state: "empty_history"`` (or
        equivalent) that an agent can switch on. Today neither is true.
        """
        monkeypatch.chdir(empty_history)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_history,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        summary = data["summary"]
        verdict = summary.get("verdict", "")
        summary_state = summary.get("state")
        # Fix is EITHER a qualified verdict ("0 commit(s); empty history")
        # OR a state disclosure that names the empty-history condition.
        verdict_qualified = any(
            qual in verdict.lower() for qual in ("empty history", "no history", "shallow", "no_history")
        )
        state_discloses_empty = summary_state is not None and any(
            s in str(summary_state).lower() for s in ("empty", "no_history", "shallow")
        )
        assert verdict_qualified or state_discloses_empty, (
            f"W805-DD MEDIUM agent-safety: '0 commit(s)' on zero-matches "
            f"path MUST be accompanied by a verdict qualifier or state "
            f"disclosure that an agent can use to detect 'no history to "
            f"scan'. Got verdict={verdict!r}, state={summary_state!r}."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-DD shape parity bug: ``--polarity`` is requested but "
            "produces no observable signal on the zero-commits path. The "
            "envelope has no ``polarity_requested`` / ``polarity_applied`` "
            "field, so an agent that passed ``--polarity`` cannot tell "
            "'no commits to annotate' from 'polarity feature silently "
            "degraded'. Pinned strict for symmetry with the main "
            "zero-matches bug -- the underlying contract should require "
            "feature-flag disclosure on ANY zero-results path."
        ),
    )
    def test_polarity_disclosure_on_empty(self, cli_runner, empty_history, monkeypatch):
        """--polarity request must surface on the envelope even on empty."""
        monkeypatch.chdir(empty_history)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "--polarity", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_history,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        # The envelope must surface that --polarity was requested -- via
        # a top-level field OR a per-result field -- so an agent can
        # detect "feature flag honored, just had nothing to annotate".
        polarity_requested = (
            "polarity_requested" in data
            or "polarity_requested" in data.get("summary", {})
            or any("polarity_requested" in r for r in data.get("results", []))
        )
        assert polarity_requested, (
            f"W805-DD: --polarity flag had no envelope-visible disclosure on "
            f"the zero-matches path. Agent cannot tell 'no commits to annotate' "
            f"from 'polarity silently degraded'. Envelope keys: {list(data.keys())}, "
            f"summary keys: {list(data.get('summary', {}).keys())}"
        )


# ---------------------------------------------------------------------------
# git_errors lineage -- positive shape lint, NOT a bug.
# ---------------------------------------------------------------------------


class TestGitErrorsLineagePresent:
    """Positive lint: ``git_errors`` field is present (null or dict) on
    the envelope so an agent can switch on it. CP45/CP46 fail-loud
    sentinel pattern already covers git-missing / git-timeout / git-error
    cases (cmd_history_grep.py lines 30-37, 260-275)."""

    def test_git_errors_field_present(self, cli_runner, empty_history, monkeypatch):
        """``git_errors`` key is in the envelope (null on the happy path)."""
        monkeypatch.chdir(empty_history)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "NONEXISTENT_STRING_XYZ"],
            cwd=empty_history,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        assert "git_errors" in data, (
            f"envelope must always carry git_errors key (CP45/CP46 fail-loud "
            f"sentinel disclosure); got keys={list(data.keys())}"
        )
        # On a successful git invocation (zero matches != git failure)
        # git_errors should be null.
        assert data["git_errors"] is None, (
            f"successful git pickaxe must emit git_errors=null on the zero-matches path; got {data['git_errors']!r}"
        )


# ---------------------------------------------------------------------------
# Clean-corpus regression -- pickaxe must still produce real commits.
# ---------------------------------------------------------------------------


class TestCleanCorpusFullAudit:
    """Sanity: a real introduced string produces a real pickaxe envelope."""

    def test_clean_corpus_emits_real_pickaxe(self, cli_runner, clean_history, monkeypatch):
        """DATABASE_URL introduced in init commit -> >= 1 commit row."""
        monkeypatch.chdir(clean_history)
        result = invoke_cli(
            cli_runner,
            ["history-grep", "DATABASE_URL"],
            cwd=clean_history,
            json_mode=True,
        )
        data = parse_json_output(result, "history-grep")
        # Pickaxe must report at least 1 commit (the init).
        per_result = data["results"][0]
        commits = per_result["commits"]
        assert len(commits) >= 1, f"clean corpus must have >=1 DATABASE_URL commit; got {len(commits)}"
        # Each commit row has sha + author + date + summary.
        c0 = commits[0]
        for key in ("sha", "short_sha", "author", "date", "summary"):
            assert key in c0 and c0[key], f"commit row missing/empty {key!r}: {c0}"
        # Summary verdict mentions commits + patterns.
        sv = data["summary"].get("verdict", "")
        assert "commit" in sv.lower() and "pattern" in sv.lower(), f"summary verdict shape regression; got {sv!r}"
        # git_errors null on the happy path.
        assert data["git_errors"] is None, f"clean-corpus pickaxe must emit git_errors=null; got {data['git_errors']!r}"
        # Total commits matches per-pattern len.
        assert data["summary"]["total_commits"] == len(commits)
