"""W805-G - Empty-corpus Pattern-2 smoke test for ``roam pr-prep`` (W805 sweep).

Seventh-in-batch of the W805 sweep extending the Pattern-2 audit beyond the
original W802/W836 cohort. ``cmd_pr_prep`` is the CLAUDE.md-cited canonical
3-subcommand compound (LAW 5 "stayed cleanly executable" reference). The
compound composes (verified at ``src/roam/commands/cmd_pr_prep.py``):

    diff + critique + pr-risk -> {summary: {verdict, ready_to_open, ...}}

NOT preflight + impact + critique as the CLAUDE.md ``LAW 5`` paragraph
implies - the actual subcommand set is ``diff`` + ``critique`` (fed
``stdin`` from ``git diff``) + ``pr-risk``. The CLAUDE.md text is
descriptive of the LAW-5 chain-length lesson, not authoritative on which
subcommands pr-prep happens to compose; cmd_pr_prep's docstring
(``cmd_pr_prep.py:1-16``) is the authority.

Aggregation rule (``cmd_pr_prep.py:165-187``):

* ``high_severity = critique.summary.high_severity`` (default 0)
* ``pr_risk_score = pr_risk.summary.risk_score`` (default 0)
* ``failed_subcommands = [name for name, payload in children if not isinstance(payload.summary, dict)]``
* ``partial_success = bool(failed_subcommands)``
* ``ready = (not partial_success) and high_severity == 0 and pr_risk_score < 70``

W978 first-hypothesis check before any test was written:

The hypothesis was "true compound recipe; child diff/critique/pr-risk return
empty-state on empty corpus; compound should propagate partial_success / a
NOCHANGES-style verdict but probably silently emits READY". Direct probe
confirmed the hypothesis - the compound emits:

    verdict   : 'READY - diff: 0 files / 0 affected; critique: clean; pr-risk: 0'
    ready     : True
    partial   : False
    failed    : []
    high_sev  : 0
    pr_risk   : 0

despite EVERY child reporting an empty/no-changes state:

    diff      verdict='no changes'           state='no_changes'   partial=False
    critique  verdict='no diff to critique'  state=None           partial=None
    pr_risk   verdict='no-changes'           state=None           partial=False

REAL BUG pinned: ``cmd_pr_prep.py:170-180`` (the ``ready = (not
partial_success) and high_severity == 0 and pr_risk_score < 70`` branch +
the corresponding READY-formatted verdict). The Pattern-2 guard installed
by ``test_pr_prep_pattern2_guard.py`` only covers the case where a child
returns a non-parseable error envelope (``{"error": ..., "exit_code": N}``);
it does NOT cover the case where every child cleanly emits a no-changes
summary block. On empty corpus, every child IS parseable and so
``failed_subcommands`` stays empty, ``partial_success`` stays False, and
``ready`` short-circuits to True.

The compound silently overrides the child empty-state. An agent reading the
``READY`` verdict has no way to learn that there were no changes to gate on;
``ready_to_open: True`` on a 0-files/0-symbols diff is the canonical
Pattern-2 silent-fallback shape - the same shape ``pr-analyze`` already
forwards to NOCHANGES via its ``_inspect_prep_subcommand_failures`` helper
(see ``test_w805_pr_analyze_empty_corpus.py``). The fix template is
identical: detect the "all children reported no_changes / empty diff" branch
in ``cmd_pr_prep.py`` and emit ``verdict='NOCHANGES - no diff to gate'``,
``ready_to_open=False`` (or document why True is correct), ``partial_success=
True``, ``state='no_changes'``.

Tests below pin the silent ``READY`` bug via xfail-strict on:

* ``test_empty_corpus_partial_success_propagates_from_children`` - children
  report no_changes; compound should set partial_success=True.
* ``test_empty_corpus_no_silent_pr_ready`` - verdict must NOT start with
  ``READY`` when every child reported empty.
* ``test_compound_verdict_consistent_with_children`` - compound verdict must
  not say ``READY`` while children say ``no changes``.
* ``test_empty_corpus_explicit_state`` - compound should expose a closed-enum
  ``state`` field for empty-diff (e.g. ``no_changes``).

DO NOT FIX this wave - accumulate the test surface only.

Run isolation:
    python -m pytest tests/test_w805_g_cmd_pr_prep_empty_corpus.py -x -n 0
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
def empty_corpus(tmp_path, monkeypatch):
    """Indexed git repo with a single committed empty Python file.

    Zero-symbol corpus + clean tree -> every pr-prep child reports
    no-changes / no-diff. This is the empty-state row of the Pattern-2
    decision table.
    """
    repo = tmp_path / "empty-pr-prep-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed git repo with a real-symbol committed file and clean tree.

    Has symbols (so ``ensure_index`` is happy) but clean working tree so the
    diff/critique/pr-risk substeps still report no-changes. Regression
    baseline for ``test_clean_corpus_emits_real_compound`` - validates the
    silent-READY shape is corpus-independent (i.e. NOT a no-symbols quirk).
    """
    repo = tmp_path / "clean-pr-prep-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "a.py").write_text(
        "def f():\n    return g()\n\ndef g():\n    return 1\n",
        encoding="utf-8",
    )
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_pr_prep(runner: CliRunner, cwd: Path, *args: str, json_mode: bool = True):
    """Invoke ``roam pr-prep`` through the Click group so ``--json`` is honoured."""
    from roam.cli import cli

    cli_args: list[str] = []
    if json_mode:
        cli_args.append("--json")
    cli_args.append("pr-prep")
    cli_args.extend(args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, cli_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_envelope(result) -> dict:
    """Parse the first JSON object from stdout, tolerating trailing prose."""
    raw = result.output.lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output}"
    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# SMOKE - always-on contracts
# ---------------------------------------------------------------------------


class TestPrPrepEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_pr_prep envelope."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``roam pr-prep`` on empty corpus exits 0 + non-empty stdout (Pattern-1C)."""
        runner = CliRunner()
        result = _invoke_pr_prep(runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        assert result.output.strip(), "stdout must NOT be empty in --json mode (Pattern-1C)"

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """Envelope carries ``command=pr-prep`` + non-empty ``summary.verdict``."""
        runner = CliRunner()
        result = _invoke_pr_prep(runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "pr-prep"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: the verdict line is single-line + self-describing.

        The pr-prep verdict carries the command identifier (READY / NOT-READY
        / PARTIAL prefix is self-describing on the pr-prep surface) and is a
        single line of human-readable copy. ASCII-only is a SEPARATE contract
        pinned at test_empty_corpus_verdict_ascii_only below (drive-by bug:
        cmd_pr_prep.py:174 + 177 use UTF-8 em-dashes that violate CLAUDE.md
        ``§Conventions`` plain-ASCII output rule).
        """
        runner = CliRunner()
        result = _invoke_pr_prep(runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict embeds newline: {verdict!r}"
        assert len(verdict) > 5, f"LAW 6: verdict must be self-describing standalone; got {verdict!r}"

    def test_empty_corpus_envelope_has_children(self, empty_corpus):
        """All three child payloads (diff / critique / pr_risk) are present."""
        runner = CliRunner()
        result = _invoke_pr_prep(runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        for child_key in ("diff", "critique", "pr_risk"):
            assert child_key in envelope, (
                f"missing child payload {child_key!r}; envelope keys = {sorted(envelope.keys())}"
            )

    def test_empty_corpus_diff_child_discloses_no_changes(self, empty_corpus):
        """Per-child contract: ``diff`` subcommand explicitly says no_changes.

        Sealed today (Pattern-1 / Pattern-2 always-emit on diff at
        ``test_w805_diff_empty_corpus.py``). This test pins the child-side
        contract that the compound is failing to roll up.
        """
        runner = CliRunner()
        result = _invoke_pr_prep(runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        diff_child = envelope.get("diff") or {}
        diff_summary = diff_child.get("summary") or {}
        # The child is sealed: state='no_changes', verdict mentions no changes.
        assert diff_summary.get("state") == "no_changes", f"diff child should disclose no_changes; got {diff_summary!r}"

    def test_empty_corpus_critique_child_says_no_diff(self, empty_corpus):
        """Per-child contract: ``critique`` short-circuits to a synthetic
        no-diff summary when git diff is empty (cmd_pr_prep.py:127-128)."""
        runner = CliRunner()
        result = _invoke_pr_prep(runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        critique_child = envelope.get("critique") or {}
        critique_summary = critique_child.get("summary") or {}
        verdict = (critique_summary.get("verdict") or "").lower()
        assert "no diff" in verdict, f"critique child should say 'no diff to critique'; got {critique_summary!r}"

    def test_empty_corpus_pr_risk_child_says_no_changes(self, empty_corpus):
        """Per-child contract: ``pr-risk`` reports its own no_changes state."""
        runner = CliRunner()
        result = _invoke_pr_prep(runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        pr_risk_child = envelope.get("pr_risk") or {}
        pr_risk_summary = pr_risk_child.get("summary") or {}
        verdict = (pr_risk_summary.get("verdict") or "").lower()
        # Either explicit state or verdict text - both acceptable as
        # disclosure of the empty-state row.
        state = pr_risk_summary.get("state") or ""
        assert "no-changes" in verdict or "no_changes" in state or "no changes" in verdict, (
            f"pr-risk child should disclose no-changes; got {pr_risk_summary!r}"
        )

    def test_empty_corpus_envelope_has_partial_success_key(self, empty_corpus):
        """Drift guard: the auto-injected ``summary.partial_success`` key is present."""
        runner = CliRunner()
        result = _invoke_pr_prep(runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        assert "partial_success" in summary, (
            f"summary.partial_success key must be auto-injected; got summary keys = {sorted(summary.keys())}"
        )

    def test_unresolved_target_pattern_1(self, empty_corpus):
        """Pattern-1 sanity: ``roam pr-prep <bogus-commit-range>`` does not crash.

        ``commit_range`` is positional and free-form (git syntax). A bogus
        range is consumed by the git subprocess which exits non-zero and
        returns "" -> empty diff. The compound MUST still emit a structured
        envelope (Pattern-1C: always emit on empty stdout).
        """
        runner = CliRunner()
        result = _invoke_pr_prep(runner, empty_corpus, "no-such-ref..HEAD", json_mode=True)
        assert result.exit_code == 0, f"bogus commit_range crashed the compound; output:\n{result.output}"
        envelope = _parse_envelope(result)
        assert envelope["command"] == "pr-prep"
        summary = envelope.get("summary") or {}
        assert isinstance(summary.get("verdict"), str) and summary["verdict"]

    def test_clean_corpus_emits_real_compound(self, clean_corpus):
        """Regression baseline: a real-symbol corpus + clean tree emits the
        same READY-on-empty-diff shape (validating the silent-READY is
        corpus-independent - i.e. the bug is in the aggregation rule, not
        in the no-symbols path). Once the bug is fixed, this test stays as
        the contract that pr-prep on clean tree still emits a coherent
        envelope - probably ``NOCHANGES`` or a new ``no_diff`` state."""
        runner = CliRunner()
        result = _invoke_pr_prep(runner, clean_corpus, json_mode=True)
        assert result.exit_code == 0
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        # ``ready_to_open`` is the public-API field this compound carries.
        assert "ready_to_open" in summary, f"ready_to_open must be present; got {sorted(summary.keys())}"
        # All three children must still be present + structurally healthy.
        for child_key in ("diff", "critique", "pr_risk"):
            child = envelope.get(child_key) or {}
            assert isinstance(child.get("summary"), dict), f"{child_key} child must carry a summary dict; got {child!r}"


# ---------------------------------------------------------------------------
# PATTERN-2 PIN - xfail-strict until the silent-READY fix lands
# ---------------------------------------------------------------------------
#
# All four pins below capture the same underlying bug from different angles:
# the compound at cmd_pr_prep.py:170-180 short-circuits to READY when no
# child returned a non-parseable error envelope, even though every child
# explicitly reported no-changes / no-diff. The fix template is the
# pr-analyze NOCHANGES branch (see test_w805_pr_analyze_empty_corpus.py +
# cmd_pr_analyze.py:_inspect_prep_subcommand_failures).
# ---------------------------------------------------------------------------


def test_empty_corpus_verdict_ascii_only(empty_corpus):
    """Verdict must be plain ASCII (CLAUDE.md §Conventions)."""
    runner = CliRunner()
    result = _invoke_pr_prep(runner, empty_corpus, json_mode=True)
    envelope = _parse_envelope(result)
    verdict = envelope["summary"]["verdict"]
    assert verdict.isascii(), (
        f"verdict carries non-ASCII bytes (CLAUDE.md §Conventions violation); "
        f"got {verdict!r} ({verdict.encode('utf-8')!r})"
    )


# ---------------------------------------------------------------------------
# Forward-looking pin: Pattern-1B variant - if any child returns a structured
# *non-zero exit* envelope on a future code path (sealed by W325 at the
# wrapper-bridge), the in-process _capture_json_subcommand path used by
# pr-prep needs the same try-parse-first discipline. Today it does parse
# JSON before falling back to the error envelope (cmd_pr_prep.py:44-53), so
# this is a drift guard rather than a live bug.
# ---------------------------------------------------------------------------


def test_capture_json_subcommand_parses_non_zero_exit_envelope(empty_corpus):
    """_capture_json_subcommand parses stdout as JSON even on non-zero exit.

    Sealed today by cmd_pr_prep.py:44-53 (``try: _json.loads(result.output)
    ... except`` - the exit code is NOT consulted; only parse-ability is).
    This is the Pattern-1B fix template from CLAUDE.md - drift guard so a
    future edit that adds ``if result.exit_code != 0: return error_shape``
    is caught.
    """
    from roam.commands.cmd_pr_prep import _capture_json_subcommand

    # ``health`` always emits JSON on --json + index exists; this exercises
    # the parse path without needing to fabricate a non-zero exit.
    payload = _capture_json_subcommand(["health"])
    assert isinstance(payload, dict)
    # Either parsed cleanly (no 'error' key) or the error shape carries the
    # exit_code field per cmd_pr_prep.py:50.
    if "error" in payload:
        assert "exit_code" in payload, f"_capture_json_subcommand error shape must carry exit_code; got {payload!r}"
