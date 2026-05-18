"""W805-MM -- empty-corpus Pattern-2 smoke test on ``roam pr-analyze``.

Thirty-ninth-in-batch W805 sweep. PR-aggregator family -- distinct from
compound recipes (cmd_for_*) and resolver-bearing commands. Composes
``pr-prep`` (diff + critique + pr-risk) with AI-likelihood scoring +
``.roam/rules.yml`` enforcement into an INTENTIONAL / SAFE / REVIEW /
BLOCK verdict.

Scope
-----

cmd_pr_analyze (src/roam/commands/cmd_pr_analyze.py) acquires a diff
from one of six sources (priority: ``--diff-from-pr URL`` > ``--input``
file > stdin > ``--staged`` > ``COMMIT_RANGE`` > unstaged ``git diff``)
and emits an aggregated verdict with three relevant empty-input paths:

1. ``commit_range=None`` + no piped stdin + clean working tree (the
   pure empty-corpus path) -- _acquire_diff returns ``""``,
   ``_inspect_prep_subcommand_failures`` detects ``no_changes`` from the
   inner ``diff`` step, and the verdict is overridden to ``"NOCHANGES"``
   with ``state="no_changes"`` + ``partial_success=True`` +
   ``reasons=["no changes to analyze"]`` (line 2124-2126). **No bug.**
   W807-axis Pattern-2 SAFE fabrication is correctly avoided here.

2. ``--input <empty-file>`` -- same downstream effect: pr-prep's inner
   diff step reports no_changes; verdict overrides to NOCHANGES with
   partial_success=True. **No bug.**

3. ``--diff-from-pr <malformed-or-unreachable-URL>`` --
   ``_fetch_diff_from_pr_url`` silently returns ``""`` on TWO failure
   modes (line 278-297):

   * URL doesn't match ``_GITHUB_PR_URL_RE`` (regex fails) -> ``return ""``
   * ``gh pr diff`` returns non-zero / raises OSError -> ``return ""``

   Neither path stamps any disclosure. The CALLER then sees an empty
   diff_text identical to "no changes in working tree", and the
   downstream NOCHANGES override applies the same SAFE-adjacent verdict.
   An agent reading the structured envelope cannot tell apart:
     (a) the working tree is genuinely clean (NOCHANGES, safe to merge)
     (b) the requested ``--diff-from-pr <url>`` failed to fetch (we have
         NO information about the PR -- merging would be reckless)

   **REAL BUG**: silent fetch failure on an explicit user request is
   the Pattern-1 Variant D agent-safety class -- silent success on
   degraded resolution. The verdict says ``"no changes to analyze"``
   when the truth is ``"the URL you gave us couldn't be parsed /
   fetched and we silently fell through to local git diff which is
   also empty"``.

W978 first-hypothesis discipline
--------------------------------

First hypothesis: cmd_pr_analyze is a compound aggregator likely to
fabricate SAFE on missing subcommand state (Pattern-2 silent-SAFE).
Probe result on the live tree:

* Empty corpus + no diff -> envelope CORRECTLY emits
  ``verdict="NOCHANGES"``, ``state="no_changes"``,
  ``partial_success=True``. Fix B (Pattern-2) is fully in force on the
  pure empty-corpus path. **No bug on this branch.**

* ``--input <empty>`` -> same as above; CORRECTLY discloses no_changes.
  **No bug.**

* ``--diff-from-pr <not-a-github-url>`` -> envelope emits
  ``verdict="NOCHANGES"``, ``reasons=["no changes to analyze"]``. The
  fetch failure is INVISIBLE -- no ``state="fetch_failed"``, no
  ``diff_source`` field, no warning that the user-requested URL was
  malformed. **REAL BUG**: an agent treating ``verdict="NOCHANGES"``
  as "PR is clean, merge" would merge a PR whose diff was never
  fetched. Pattern-1 Variant D agent-safety class.

W978 re-run check: probed three times (no args, --input empty, malformed
--diff-from-pr URL). Output byte-identical across all three on the
discriminating fields. The malformed-URL probe is genuinely
indistinguishable from the legitimately-clean tree probe. Hypothesis
stands.

Conclusion
----------

* **Pure empty-corpus path passes the W1272-canonical Pattern-2
  contract**. cmd_pr_analyze's Fix B is well-architected -- positive
  regression tests preserve it.

* **REAL BUG pinned: Pattern-1-V-D agent-safety class on the
  --diff-from-pr failure paths** (cmd_pr_analyze.py:278-297). Silent
  fetch failure indistinguishable from legitimate-clean. Pinned
  xfail-strict so a future fix that adds ``state="fetch_failed"`` /
  ``diff_source_error`` / ``partial_success=True`` graduates to PASS.

* **Bug class**: agent-safety -- silent "PR clean" on missing data
  could cause an agent to merge a PR it never analysed. This is the
  exact failure mode the CLAUDE.md Pattern-1 Variant D guidance was
  written to prevent.

Sweep brief: W805-MM (Wave805-MM, thirty-ninth-in-batch).
"""

from __future__ import annotations

import json
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
# Existence gate (test_command_exists_or_skip)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """W805-MM bail gate: cmd_pr_analyze.py must exist + register a CLI command."""
    cmd_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_analyze.py"
    if not cmd_path.exists():
        pytest.skip(f"cmd_pr_analyze missing at {cmd_path} -- W805-MM cannot probe")

    from roam.cli import _COMMANDS

    assert "pr-analyze" in _COMMANDS, (
        "W805-MM: 'pr-analyze' not registered in cli._COMMANDS; the module exists but the wiring is broken"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path):
    """Project with only a README -- no source code to analyse.

    Exercises the pure empty-corpus path: _acquire_diff returns ``""``
    (no piped stdin, clean working tree), pr-prep's inner diff step
    reports no_changes, and the verdict overrides to NOCHANGES.
    """
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("Empty corpus project.\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path):
    """Project with one indexable function + a real staged diff.

    Exercises the full pipeline: pr-prep gets real content, pr-analyze
    runs the AI-likelihood scoring, rules pass-through, verdict
    composition. Sanity check that the empty-corpus disclosure does
    NOT trigger on real input.
    """
    proj = tmp_path / "clean_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text("def hello():\n    return 42\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash / no empty stdout on the empty path.
# ---------------------------------------------------------------------------


class TestEmptyCorpusNoCrash:
    """The empty-corpus path must always emit a structured envelope, never
    crash and never emit empty stdout (Pattern-1 Variant C)."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus, monkeypatch):
        """No exception / non-empty stdout on the empty-corpus path."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["pr-analyze"],
            cwd=empty_corpus,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"pr-analyze empty-corpus path must exit 0; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on empty-corpus path"


# ---------------------------------------------------------------------------
# Pattern-2 disclosure -- empty corpus emits explicit state/partial_success.
# ---------------------------------------------------------------------------


class TestEmptyCorpusEnvelopeShape:
    """The empty-corpus path is already W1272-canonical via the Fix B
    Pattern-2 logic -- positive regression tests so a future cleanup
    doesn't strip the disclosure."""

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus, monkeypatch):
        """Envelope carries a non-empty verdict per LAW 6."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["pr-analyze"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "pr-analyze")
        assert "summary" in data, f"envelope missing summary: {data}"
        assert "verdict" in data["summary"], f"summary missing verdict: {data['summary']}"
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict.strip()

    def test_empty_corpus_state_explicit(self, cli_runner, empty_corpus, monkeypatch):
        """Fix B (Pattern-2) regression: empty corpus sets ``state='no_changes'``."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["pr-analyze"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "pr-analyze")
        summary = data["summary"]
        assert summary.get("state") == "no_changes", (
            f"W805-MM regression: cmd_pr_analyze empty-corpus must keep "
            f"state='no_changes' (Fix B Pattern-2 contract); "
            f"got {summary.get('state')!r}"
        )

    def test_empty_corpus_partial_success_set(self, cli_runner, empty_corpus, monkeypatch):
        """Fix B (Pattern-2) regression: empty corpus sets ``partial_success=True``."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["pr-analyze"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "pr-analyze")
        summary = data["summary"]
        assert summary.get("partial_success") is True, (
            f"W805-MM regression: cmd_pr_analyze empty-corpus must keep "
            f"partial_success=True (Fix B Pattern-2 contract); "
            f"got {summary.get('partial_success')!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus, monkeypatch):
        """LAW 6: verdict works without any other field.

        Empty-corpus verdict is ``"NOCHANGES"`` which standalone declares
        the absence-state. An agent reading only the verdict knows there
        is nothing to act on.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["pr-analyze"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "pr-analyze")
        verdict = data["summary"]["verdict"]
        # NOCHANGES verdict is self-describing -- names the absence-state.
        upper = verdict.upper()
        assert "NOCHANGES" in upper or "NO CHANGES" in upper or "NO_CHANGES" in upper, (
            f"LAW 6: empty-corpus verdict must declare the absence-state standalone; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# Missing-PR-ref disclosure -- REAL BUG pinned strict.
# ---------------------------------------------------------------------------


class TestMissingPrRefDisclosure:
    """W805-MM REAL BUG: ``--diff-from-pr <malformed-url>`` silently falls
    through to empty diff text. The downstream NOCHANGES override fires
    with NO disclosure that the user-requested fetch failed.

    This is the Pattern-1 Variant D agent-safety class -- silent success
    on degraded resolution. The fix template per CLAUDE.md Pattern-1
    Variant D: disclose the resolution state via a field on the envelope
    + ``partial_success=true`` + a distinct verdict reflecting the
    degradation (NOT NOCHANGES which masquerades as clean tree).
    """

    def test_missing_pr_ref_envelope_emits(self, cli_runner, empty_corpus, monkeypatch):
        """Sanity: pr-analyze with malformed URL doesn't crash; emits envelope."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["pr-analyze", "--diff-from-pr", "not-a-github-url"],
            cwd=empty_corpus,
            json_mode=True,
        )
        assert result.exit_code == 0
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on bad-URL path"
        data = json.loads(out)
        assert "summary" in data

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-MM REAL BUG (agent-safety class): "
            "src/roam/commands/cmd_pr_analyze.py:278-297 "
            "(_fetch_diff_from_pr_url) silently returns empty string on "
            "TWO distinct failure modes: (1) URL doesn't match "
            "_GITHUB_PR_URL_RE -- regex fails -> return ''; (2) gh pr "
            "diff returns non-zero / raises -> return ''. Neither path "
            "stamps any disclosure on the envelope. The CALLER then sees "
            "an empty diff_text identical to 'clean working tree', and "
            "the downstream NOCHANGES override emits "
            "verdict='NOCHANGES', state='no_changes', "
            "reasons=['no changes to analyze'] -- structurally "
            "indistinguishable from a legitimately-clean tree. An agent "
            "reading verdict=NOCHANGES would merge a PR whose diff was "
            "never fetched. Pinned strict so a future fix that adds "
            "state='fetch_failed' / state='url_invalid' / "
            "diff_source_error / partial_success=True (NOT masquerading "
            "as NOCHANGES) graduates to PASS."
        ),
    )
    def test_missing_pr_ref_disclosure(self, cli_runner, empty_corpus, monkeypatch):
        """W805-MM REAL BUG sentinel: malformed --diff-from-pr is silently
        equivalent to clean working tree.

        The bug: user requests ``--diff-from-pr not-a-github-url``;
        ``_fetch_diff_from_pr_url`` regex-fails and returns ``""``;
        the empty diff propagates through pr-prep as no_changes; the
        verdict is emitted as ``"NOCHANGES"`` with no signal that the
        user-requested fetch failed.

        The fix MUST distinguish "we have no PR data because the URL
        was bad" from "we have no PR data because the tree is clean".
        Acceptable shapes (any one suffices):

          summary.state in {"fetch_failed", "url_invalid", "diff_source_error"}
          summary.diff_source_error truthy
          summary.diff_source == "diff-from-pr" + state != "no_changes"
          top-level fetch_error / diff_acquisition_error field
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["pr-analyze", "--diff-from-pr", "not-a-github-url"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "pr-analyze")
        summary = data["summary"]
        # ASSERT THE FIX HAS BEEN APPLIED: malformed-URL envelope MUST
        # carry a disclosure distinguishing it from a legitimate-clean
        # envelope. Any one of these signals suffices.
        has_disclosure = (
            summary.get("state") in ("fetch_failed", "url_invalid", "diff_source_error")
            or bool(summary.get("diff_source_error"))
            or (summary.get("diff_source") == "diff-from-pr" and summary.get("state") != "no_changes")
            or bool(data.get("fetch_error"))
            or bool(data.get("diff_acquisition_error"))
        )
        assert has_disclosure, (
            f"W805-MM REAL BUG (agent-safety): cmd_pr_analyze.py:278-297 "
            f"silently maps bad --diff-from-pr URL to empty diff, "
            f"producing verdict='NOCHANGES' indistinguishable from a "
            f"legitimately-clean tree. Expected one of: "
            f"state in {{fetch_failed, url_invalid, diff_source_error}} / "
            f"diff_source_error truthy / "
            f"top-level fetch_error or diff_acquisition_error. "
            f"Got summary={summary!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-MM REAL BUG (agent-safety): _fetch_diff_from_pr_url "
            "silent-fall-through to '' on bad URL is indistinguishable "
            "from clean working tree on the verdict axis. A NOCHANGES "
            "verdict from a failed fetch is the dangerous case -- an "
            "agent reading verdict=NOCHANGES could merge a PR whose diff "
            "was never fetched. The fix MUST emit a distinct verdict "
            "(e.g. 'FETCH_FAILED', 'URL_INVALID', 'CANNOT_ANALYZE') "
            "rather than NOCHANGES on the bad-URL path."
        ),
    )
    def test_no_silent_pr_clean_on_empty(self, cli_runner, empty_corpus, monkeypatch):
        """Agent-safety pin: malformed --diff-from-pr MUST NOT emit a
        verdict that an agent could read as 'PR is clean, merge it'.

        Today, the verdict is ``"NOCHANGES"`` -- semantically
        identical to a legitimately-clean tree. The fix MUST emit a
        distinct verdict (e.g. ``"FETCH_FAILED"``, ``"URL_INVALID"``,
        ``"CANNOT_ANALYZE"``) on the bad-URL path so an agent reading
        only the verdict cannot conclude 'safe to merge'.
        """
        # Run on empty-corpus with malformed URL.
        monkeypatch.chdir(empty_corpus)
        bad_url_result = invoke_cli(
            cli_runner,
            ["pr-analyze", "--diff-from-pr", "not-a-github-url"],
            cwd=empty_corpus,
            json_mode=True,
        )
        bad_url_summary = parse_json_output(bad_url_result, "pr-analyze")["summary"]

        # Run on empty-corpus with no diff at all.
        clean_result = invoke_cli(
            cli_runner,
            ["pr-analyze"],
            cwd=empty_corpus,
            json_mode=True,
        )
        clean_summary = parse_json_output(clean_result, "pr-analyze")["summary"]

        # Restrict to discriminating fields (drop nondeterministic ones).
        keep_keys = (
            "verdict",
            "state",
            "partial_success",
            "failed_subcommands",
            "diff_source",
            "diff_source_error",
            "reasons",
        )
        bad_url_keep = {k: bad_url_summary.get(k) for k in keep_keys}
        clean_keep = {k: clean_summary.get(k) for k in keep_keys}

        assert bad_url_keep != clean_keep, (
            f"W805-MM agent-safety pin: bad-URL envelope is "
            f"byte-indistinguishable from legitimately-clean envelope on "
            f"the discriminating fields. An agent cannot tell 'fetch "
            f"failed' from 'tree is clean'. "
            f"bad_url={bad_url_keep!r}, clean={clean_keep!r}"
        )


# ---------------------------------------------------------------------------
# Clean-corpus regression -- non-empty pipeline still emits real analysis.
# ---------------------------------------------------------------------------


class TestCleanCorpusFullSuccess:
    """Sanity: a corpus with real source emits a real pr-analyze envelope.

    Guards against accidental over-zealous Pattern-2 disclosure that
    flags legitimate-empty diffs as 'fetch failed'. The pure empty-
    corpus path (no --diff-from-pr) SHOULD emit NOCHANGES -- that's
    correct. Only the explicit-URL-failure path should escalate.
    """

    def test_clean_corpus_emits_real_analysis(self, cli_runner, clean_corpus, monkeypatch):
        """Clean corpus with no diff -> still NOCHANGES (correct), but the
        envelope is fully-formed with all aggregator fields present."""
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(
            cli_runner,
            ["pr-analyze"],
            cwd=clean_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "pr-analyze")
        # Aggregator architecture: pr_prep + ai_likelihood + rule_violations
        # must be present even when the diff is empty.
        assert "pr_prep" in data, f"missing pr_prep aggregator output: {list(data.keys())}"
        assert "ai_likelihood" in data, f"missing ai_likelihood scoring: {list(data.keys())}"
        assert "rule_violations" in data, f"missing rule_violations: {list(data.keys())}"
        # ai_likelihood should be present with score=0 on empty diff.
        ai = data["ai_likelihood"]
        assert ai.get("score") == 0
        assert ai.get("reason") == "empty diff", f"expected reason='empty diff'; got {ai.get('reason')!r}"
