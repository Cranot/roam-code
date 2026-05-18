"""W805-SSS - Empty-corpus Pattern-2 smoke for ``roam triage``.

Seventy-first-in-batch W805 sweep. W805-PPP framed cmd_triage as a
"finding-prioritization aggregator; novel axis (severity ranking on
empty findings)" -- the W978 first-hypothesis probe contradicts that
framing.

W978 first-hypothesis verification (run BEFORE writing pins):

``cmd_triage`` is NOT a finding-prioritization aggregator. It is a
suppression-state manager -- pure CRUD over ``.roam-suppressions.yml``::

  roam triage list  -> reads YAML, emits {verdict, total, suppressions}
  roam triage stats -> counts by_status/by_rule/by_file
  roam triage add   -> writes one suppression entry
  roam triage check -> queries suppression match for a finding

There is no DB query, no symbol resolution, no compound aggregation, no
severity ranking. The "empty corpus" axis collapses to "no suppressions
in YAML" -- which each subcommand handles EXPLICITLY:

  triage list:  verdict="no suppressions", total=0, partial_success=False,
                suppressions=[]
  triage stats: same shape + by_status/by_rule/by_file = {}
  triage check: verdict="not suppressed: <rule> at <file>",
                result.suppressed=False
                (semantically correct -- a finding with no recorded
                suppression genuinely is NOT suppressed; no fallback
                chain, no inference, no degraded-resolution path)

**There is no Pattern-2 silent-SAFE bug here.** Unlike compound
aggregators (W805-OOO/KKK/MMM etc.) which silently swallow degraded
children, cmd_triage is a substrate state manager. ``verdict:
"no suppressions"`` is the canonical Pattern-2 explicit-empty-state
disclosure applied correctly, NOT a silent SAFE.

The W805-PPP "novel axis" framing was a misreading of cmd_triage's
purpose. cmd_triage neither ranks severity nor aggregates findings; it
records human review decisions about findings produced by OTHER
commands.

PIN STRATEGY (W978 + accumulate-only constraint):

- NO xfail-strict pin -- no bug to pin.
- SMOKE: no crash + LAW 6 verdict + explicit empty-state disclosure
  across all three read paths (list / stats / check).
- POSITIVE BASELINE: add a suppression, confirm list / stats / check
  reflect it (regression guard against the explicit-state disclosure
  silently collapsing into a default SAFE in a future refactor).
- W805 SWEEP CONTRIBUTION: confirm the suppression-substrate command
  family is Pattern-2 CLEAN, so the sweep doesn't re-investigate it.

Run isolation: ``python -m pytest tests/test_w805_sss_cmd_triage_empty_corpus.py -x -n 0``
Regression: ``python -m pytest tests/test_triage.py -x -n 0``
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git_init_committed(repo: Path) -> None:
    """Init a git repo + commit current files. No further history."""
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(repo),
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), capture_output=True)


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """A git repo with a single empty .py file and NO .roam-suppressions.yml.

    The triage substrate operates entirely on disk state under the project
    root; no indexer run required. The absence of .roam-suppressions.yml
    is the canonical 'empty' shape for this command family."""
    repo = tmp_path / "empty-triage-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def populated_corpus(tmp_path, monkeypatch):
    """A git repo with one suppression already recorded via `triage add`.

    Positive baseline for regression guards: confirms the empty-state
    verdict is NOT a default-SAFE fallback -- it transitions to a real
    'N suppression(s)' verdict when state is non-empty."""
    repo = tmp_path / "populated-triage-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "main.py").write_text("password = 'hunter2'\n", encoding="utf-8")
    _git_init_committed(repo)
    monkeypatch.chdir(repo)

    # Record one suppression so list/stats/check exercise the populated
    # path. Use --path (not the deprecated --file alias).
    runner = CliRunner()
    r = runner.invoke(
        cli,
        [
            "triage",
            "add",
            "--rule",
            "hardcoded-secret",
            "--path",
            "main.py",
            "--reason",
            "test fixture",
            "--status",
            "safe",
        ],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, f"triage add failed:\n{r.output}"
    return repo


# ---------------------------------------------------------------------------
# Existence check (W978 + W907 - verify before pinning)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``triage`` is registered as a click group with the four subcommands."""
    from roam.commands.cmd_triage import (  # noqa: F401
        triage,
        triage_add,
        triage_check,
        triage_list,
        triage_stats,
    )

    # triage is a click.Group, subcommands are click.Commands.
    assert hasattr(triage, "commands"), type(triage)
    for sub in ("list", "add", "stats", "check"):
        assert sub in triage.commands, list(triage.commands.keys())


def _invoke_json(args: list[str]) -> dict:
    """Run a roam CLI subcommand in --json mode + return the parsed envelope."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json"] + args, catch_exceptions=False)
    assert result.exit_code == 0, f"command {args!r} failed (exit {result.exit_code}):\n{result.output}"
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        raise AssertionError(f"command {args!r} produced non-JSON output:\n{result.output[:500]}") from e


# ---------------------------------------------------------------------------
# SMOKE (always-on) -- triage list on empty corpus
# ---------------------------------------------------------------------------


class TestTriageListEmptyCorpusSmoke:
    """Pattern-2 baseline on the empty-corpus envelope shape for
    ``roam triage list``."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        """``triage list`` must return exit 0 + a dict envelope, never raise."""
        data = _invoke_json(["triage", "list"])
        assert isinstance(data, dict)

    def test_empty_corpus_envelope_has_verdict(self, empty_corpus):
        """``summary.verdict`` is a non-empty string (Pattern-2 always-emit)."""
        data = _invoke_json(["triage", "list"])
        verdict = (data.get("summary") or {}).get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be non-empty; got {verdict!r}"

    def test_empty_corpus_command_field_set(self, empty_corpus):
        """Envelope identifies itself as ``triage-list``."""
        data = _invoke_json(["triage", "list"])
        assert data.get("command") == "triage-list", data.get("command")

    def test_empty_corpus_state_explicit(self, empty_corpus):
        """Pattern-2 canonical: empty state is named explicitly in the
        verdict (``"no suppressions"``), NOT inferred from a default SAFE
        verdict + zero total."""
        data = _invoke_json(["triage", "list"])
        s = data["summary"]
        verdict = s.get("verdict", "")
        # Explicit empty-state disclosure: 'no suppressions' must appear
        # in the verdict text (closed-enum vocabulary).
        assert "no suppressions" in verdict.lower(), (
            f"empty-state verdict must explicitly say 'no suppressions'; got {verdict!r}"
        )
        assert s.get("total") == 0, s

    def test_empty_corpus_partial_success_set(self, empty_corpus):
        """``summary.partial_success`` is False on a CLEAN empty state.

        For cmd_triage the empty case is genuinely clean -- 'no
        suppressions recorded' is a valid steady state, not a degraded
        execution. This pins partial_success=False so a future refactor
        that flips it to True (incorrectly conflating 'empty' with
        'degraded') is caught."""
        data = _invoke_json(["triage", "list"])
        s = data["summary"]
        assert s.get("partial_success") is False, f"partial_success must be False on clean-empty state; got {s!r}"

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        """LAW 6: verdict is single-line + readable without other fields."""
        data = _invoke_json(["triage", "list"])
        verdict = data["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        # LAW 6: verdict communicates state in one line.
        assert len(verdict) >= 5, f"verdict too short: {verdict!r}"

    def test_empty_corpus_suppressions_list_present(self, empty_corpus):
        """``suppressions`` key is present + empty (always emit, never absent)."""
        data = _invoke_json(["triage", "list"])
        assert "suppressions" in data, list(data.keys())
        assert data["suppressions"] == [], data["suppressions"]


# ---------------------------------------------------------------------------
# SMOKE -- triage stats on empty corpus
# ---------------------------------------------------------------------------


class TestTriageStatsEmptyCorpusSmoke:
    """Pattern-2 baseline on ``roam triage stats`` -- the closest cmd_triage
    has to the W805-PPP 'severity ranking' framing. Confirms there is no
    silent-SAFE on the count axis."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        data = _invoke_json(["triage", "stats"])
        assert isinstance(data, dict)

    def test_empty_corpus_command_field_set(self, empty_corpus):
        data = _invoke_json(["triage", "stats"])
        assert data.get("command") == "triage-stats", data.get("command")

    def test_empty_corpus_state_explicit(self, empty_corpus):
        """Stats discloses 'no suppressions' explicitly + zero by_status
        / by_rule / by_file maps. There is no silent ALL CLEAR collapsing
        an empty registry into a SAFE verdict + missing field."""
        data = _invoke_json(["triage", "stats"])
        s = data["summary"]
        verdict = s.get("verdict", "")
        assert "no suppressions" in verdict.lower(), f"empty-state verdict must say 'no suppressions'; got {verdict!r}"
        assert s.get("total") == 0
        # Per-axis breakdowns are always-emit empty dicts (NOT absent
        # keys -- key absence is the Pattern-1C class of bug).
        for axis in ("by_status", "by_rule", "by_file"):
            assert axis in data, f"missing axis {axis!r} in {list(data.keys())}"
            assert data[axis] == {}, data[axis]

    def test_empty_corpus_partial_success_set(self, empty_corpus):
        """Clean-empty registry -> partial_success is False."""
        data = _invoke_json(["triage", "stats"])
        assert data["summary"].get("partial_success") is False, data["summary"]

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        verdict = _invoke_json(["triage", "stats"])["summary"]["verdict"]
        assert "\n" not in verdict, f"newline in verdict: {verdict!r}"
        assert len(verdict) >= 5

    def test_severity_ranking_disclosure_on_empty(self, empty_corpus):
        """W805-PPP 'severity ranking on empty findings' axis-check.

        cmd_triage does NOT do severity ranking -- it counts by status
        (safe / acknowledged / wont-fix), NOT by severity. This test
        documents that the W805-PPP framing was a misread. The closed-
        enum 'status' field is the only axis the stats subcommand
        ranks on. On empty corpus, every status count is zero AND the
        verdict explicitly says 'no suppressions' -- no silent
        ALL-CLEAR fallback."""
        data = _invoke_json(["triage", "stats"])
        # No silent default-status entries -- by_status is genuinely
        # empty, not pre-populated with zero counts that an agent could
        # mistake for 'all-clear after review'.
        assert data["by_status"] == {}, (
            f"by_status pre-populated with default-status entries: "
            f"{data['by_status']!r} -- this would let an agent read a "
            f"'safe: 0' entry and assume reviewed-clear status"
        )


# ---------------------------------------------------------------------------
# SMOKE -- triage check on empty corpus
# ---------------------------------------------------------------------------


class TestTriageCheckEmptyCorpusSmoke:
    """Pattern-2 baseline on ``roam triage check`` -- the closest
    cmd_triage has to 'finding-prioritization aggregator' framing.
    Confirms no silent-SUPPRESSED fallback on an empty registry."""

    def test_empty_corpus_no_crash(self, empty_corpus):
        data = _invoke_json(["triage", "check", "some-rule", "some/file.py"])
        assert isinstance(data, dict)

    def test_empty_corpus_command_field_set(self, empty_corpus):
        data = _invoke_json(["triage", "check", "some-rule", "some/file.py"])
        assert data.get("command") == "triage-check"

    def test_no_silent_suppressed_on_empty(self, empty_corpus):
        """Critical Pattern-2 check: on an empty registry, ``check``
        must report ``suppressed=False`` -- never silently fall back to
        True (which would let an agent skip a real security finding by
        treating an empty registry as 'pre-approved')."""
        data = _invoke_json(["triage", "check", "hardcoded-secret", "main.py"])
        s = data["summary"]
        assert s.get("suppressed") is False, f"empty registry must NOT report suppressed=True; got {s!r}"
        verdict = s.get("verdict", "")
        assert "not suppressed" in verdict.lower(), (
            f"empty-registry check verdict must say 'not suppressed'; got {verdict!r}"
        )

    def test_empty_corpus_result_field_explicit(self, empty_corpus):
        """The ``result`` payload always carries the queried rule / file
        / line + suppressed=False -- never absent fields that an agent
        could interpret as ambiguous-pass."""
        data = _invoke_json(["triage", "check", "hardcoded-secret", "main.py"])
        assert "result" in data
        result = data["result"]
        assert result.get("rule") == "hardcoded-secret"
        assert result.get("file") == "main.py"
        assert result.get("suppressed") is False
        # 'suppression' key is intentionally ABSENT on a not-suppressed
        # finding -- presence implies a matched record.
        assert "suppression" not in result, (
            f"result.suppression must be absent on not-suppressed finding; got {result.get('suppression')!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, empty_corpus):
        verdict = _invoke_json(["triage", "check", "some-rule", "some/file.py"])["summary"]["verdict"]
        assert "\n" not in verdict
        # Verdict identifies the queried finding by name in standalone
        # form (LAW 4 concrete-noun anchor + LAW 6 standalone reading).
        assert "some-rule" in verdict


# ---------------------------------------------------------------------------
# POSITIVE BASELINE -- regression guard against silent-empty SAFE fallback
# ---------------------------------------------------------------------------


class TestTriagePopulatedCorpusBaseline:
    """Populated baseline: confirm the empty-state verdicts are NOT a
    default-SAFE fallback. When state is non-empty, list/stats/check
    transition to real ``N suppression(s)`` / ``suppressed`` verdicts.

    This guards against a future refactor that hardcodes 'no
    suppressions' as the verdict regardless of state."""

    def test_clean_corpus_emits_real_triage_list(self, populated_corpus):
        data = _invoke_json(["triage", "list"])
        s = data["summary"]
        assert s.get("total") == 1, s
        verdict = s.get("verdict", "")
        # Real verdict mentions the count, NOT 'no suppressions'.
        assert "no suppressions" not in verdict.lower(), (
            f"populated registry still emits empty-state verdict: {verdict!r}"
        )
        assert "1" in verdict, f"verdict must mention count; got {verdict!r}"
        # Suppressions list contains the recorded entry.
        assert len(data.get("suppressions") or []) == 1, data.get("suppressions")
        sup = data["suppressions"][0]
        assert sup.get("rule") == "hardcoded-secret"

    def test_clean_corpus_emits_real_triage_stats(self, populated_corpus):
        data = _invoke_json(["triage", "stats"])
        s = data["summary"]
        assert s.get("total") == 1, s
        # by_status carries the real status of the one recorded entry.
        assert data["by_status"] == {"safe": 1}, data["by_status"]
        assert data["by_rule"] == {"hardcoded-secret": 1}, data["by_rule"]

    def test_clean_corpus_check_finds_recorded_suppression(self, populated_corpus):
        data = _invoke_json(["triage", "check", "hardcoded-secret", "main.py"])
        s = data["summary"]
        assert s.get("suppressed") is True, s
        verdict = s.get("verdict", "")
        assert "suppressed" in verdict.lower(), verdict
        # The matched record is surfaced under result.suppression.
        result = data.get("result") or {}
        assert "suppression" in result, list(result.keys())
        assert result["suppression"].get("status") == "safe"

    def test_clean_corpus_check_unrelated_rule_not_suppressed(self, populated_corpus):
        """A different rule against the same file: NOT suppressed.

        This is the key 'no fallback chain' regression guard -- check
        does not infer 'file has any suppression, so all findings on it
        are suppressed'. Each (rule, file, line) tuple is independent."""
        data = _invoke_json(["triage", "check", "sql-injection", "main.py"])
        s = data["summary"]
        assert s.get("suppressed") is False, f"check incorrectly reports unrelated rule as suppressed: {s!r}"
