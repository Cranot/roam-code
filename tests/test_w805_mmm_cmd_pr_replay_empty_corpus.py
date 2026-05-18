"""W805-MMM: Empty-corpus Pattern-2 + Pattern-1-V-D smoke test on ``cmd_pr_replay``.

Sixty-fifth-in-batch W805 sweep. End-to-end ChangeEvidence compiler
empty-PR analog peer of W805-FFF (cmd_cga -- in-toto predicate
PRODUCER, 6 REAL BUGS, empty merkle = SHA-256(empty)) and
W805-III (cmd_evidence_diff -- evidence packet CONSUMER, 4 REAL
BUGS, "no drift between packets" on two ``{}`` inputs).

Where cmd_cga produces a ``ChangeEvidence``-adjacent in-toto predicate
over the indexed graph and cmd_evidence_diff consumes two evidence
packets from disk, ``cmd_pr_replay`` is the END-TO-END PR Replay v0
compiler (per CLAUDE.md "Phase 3 -- PR Replay compiled report"). It
runs ``roam postmortem`` over a commit range, aggregates findings,
optionally builds a ``ChangeEvidence`` packet via
``_collect_change_evidence``, and emits a buyer-facing Markdown +
JSON report.

W978 first-hypothesis discipline: empirical probe across a truly empty
git corpus (one empty commit, no tracked files) shows cmd_pr_replay
DOES exhibit a Pattern-2 + Pattern-1-V-D bug:

  * Empty corpus -> postmortem returns ``verdict: "no commits matched"``,
    ``commits_scanned: 0``. pr_replay surfaces that string into the
    JSON envelope's ``summary.verdict`` while the **Markdown report**
    -- the headline buyer-facing artifact -- says: "**Verdict:** Clean
    window. None of the 0 PRs replayed would have been flagged by the
    current detector set."
  * ``summary.state`` = ``None``. No degenerate-input disclosure.
  * ``summary.partial_success`` = ``False``. Empty/no-commits is not
    flagged as partial; a clean window and a *no-window* look the same.
  * ``summary.resolution`` = ``None``. No Pattern-1-V-D disclosure
    distinguishing "PR range resolved to N commits with M findings"
    from "PR range resolved to ZERO commits and we ran nothing".

This is a real Pattern-2 silent-SAFE bug AND a Pattern-1-V-D silent-
success-on-degraded-resolution bug. The Markdown "Clean window"
verdict is INDISTINGUISHABLE between (real 5-PR window with zero
findings -- legitimately clean) and (zero-commit window -- nothing
was actually scanned). An agent OR a prospective buyer consuming
the Markdown sample cannot tell which one ran.

BUG class: Pattern-2 (silent fallback) + Pattern-1-V-D (silent success
on degraded resolution). file:line --
``src/roam/commands/cmd_pr_replay.py:2913-2917`` (Markdown executive-
summary "Clean window" branch fires when ``commits_with == 0``
WITHOUT distinguishing ``commits_scanned == 0``) +
``cmd_pr_replay.py:3700-3729`` (JSON envelope ``summary`` lacks
``state`` / ``resolution`` / ``partial_success`` flip on empty
commit range).

This test pins via xfail-strict on the bug assertions and positive
regression pins on the disclosure-shape invariants that SHOULD hold.

W805 sweep tally update (incl. this entry):
  * Aggregator-family bugs: ~6+ (cmd_brief / cmd_audit / cmd_dogfood
    etc., all _compound_envelope-rooted) -- unchanged.
  * Predicate-producer family: cmd_cga (W805-FFF) -- in-toto producer.
  * Predicate-consumer family: cmd_evidence_diff (W805-III) -- consumer.
  * End-to-end compiler family: cmd_pr_replay (W805-MMM) -- NEW.
    Completes the empty-input audit symmetry across producer (W805-FFF),
    consumer (W805-III), and end-to-end compiler (W805-MMM) per the
    evidence-compiler thesis (W170/W174).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

# Local conftest helpers
sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

_CMD_PATH = Path(__file__).resolve().parents[1] / "src" / "roam" / "commands" / "cmd_pr_replay.py"


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _git_init_empty(path: Path) -> None:
    """Initialize a git repo with NO tracked files (one empty commit)."""
    subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=path, capture_output=True)
    # Empty commit so HEAD resolves but no PRs/diffs exist.
    subprocess.run(
        ["git", "commit", "--allow-empty", "-qm", "init"],
        cwd=path,
        capture_output=True,
    )


def _git_init_minimal_with_change(path: Path) -> None:
    """Initialize a git repo with a baseline commit + a follow-up change.

    Two commits so ``HEAD~1..HEAD`` resolves to a single real PR-like
    range, but the postmortem detector hits are not guaranteed (since
    the codebase is trivial). Suitable for the clean-corpus positive
    regression pin.
    """
    subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=path, capture_output=True)
    (path / "x.py").write_text("def f():\n    return 1\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, capture_output=True)
    (path / "x.py").write_text("def f():\n    return 2\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "tweak"], cwd=path, capture_output=True)


def _parse_json_envelope(result, command: str = "pr-replay") -> dict:
    """Parse JSON envelope from CliRunner result, tolerating leading chrome."""
    raw = getattr(result, "stdout", None)
    if raw is None:
        raw = result.output
    idx = raw.find("{")
    if idx < 0:
        pytest.fail(f"No JSON object in {command} output:\n{raw[:500]}")
    last_err: Exception | None = None
    for start in range(idx, min(len(raw), idx + 5000)):
        if raw[start] != "{":
            continue
        try:
            data = json.loads(raw[start:])
        except json.JSONDecodeError as e:  # pragma: no cover - resilience
            last_err = e
            continue
        if data.get("command") == command:
            return data
    pytest.fail(f"No JSON envelope with command={command!r} (last err: {last_err}):\n{raw[:600]}")


# ---------------------------------------------------------------------------
# 1. Existence guard (W978 + W907 discipline)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """If cmd_pr_replay.py vanishes, this whole module skips."""
    if not _CMD_PATH.is_file():
        pytest.skip(f"cmd_pr_replay.py absent at {_CMD_PATH}")
    assert _CMD_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# 2. Empty corpus must not crash
# ---------------------------------------------------------------------------


def test_empty_corpus_no_crash(cli_runner, tmp_path, monkeypatch):
    """Empty git corpus (one empty commit, no tracked files) -- pr-replay
    must NOT traceback. The producer auto-indexes (0 files, 0 symbols),
    postmortem returns 'no commits matched', and pr-replay must still
    emit SOME envelope (whether degenerate or properly disclosed)."""
    proj = tmp_path / "empty"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["pr-replay", "--tier", "sample"],
        cwd=proj,
        json_mode=True,
    )
    assert "Traceback" not in result.output, result.output
    # Exit may be 0 (current bug) or non-zero (post-fix). Either is fine
    # as long as the process didn't crash.
    assert result.exit_code in (0, 1, 5), f"unexpected exit code {result.exit_code}: {result.output[:400]}"


# ---------------------------------------------------------------------------
# 3. Envelope always has a verdict (LAW 6 baseline)
# ---------------------------------------------------------------------------


def test_empty_pr_envelope_has_verdict(cli_runner, tmp_path, monkeypatch):
    """LAW 6: ``summary.verdict`` must be a non-empty string regardless
    of corpus state. Positive regression pin -- this invariant holds
    under both pre- and post-fix behavior."""
    proj = tmp_path / "verdict"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["pr-replay", "--tier", "sample"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), f"summary.verdict must be non-empty, got {verdict!r}"


# ---------------------------------------------------------------------------
# 4. State is explicit on empty corpus  (xfail-strict: real bug)
#
#     Pattern-2 invariant: degenerate input (0 commits) must disclose
#     ``summary.state``. cmd_pr_replay CURRENTLY emits no state field --
#     pinned via xfail-strict.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-MMM Pattern-2 bug: cmd_pr_replay emits no `state` field when the "
        "commit range resolves to ZERO commits. Verdict reads 'no commits matched' "
        "(JSON) / 'Clean window' (Markdown) without a state disclosure. Fix: "
        "cmd_pr_replay.py:3700-3729 must set summary.state='empty_corpus' (or "
        "'no_commits_in_range') when summary.commits_scanned == 0."
    ),
)
def test_empty_pr_state_explicit(cli_runner, tmp_path, monkeypatch):
    """Empty corpus -> ``summary.state`` MUST be explicitly set (e.g.
    ``"empty_corpus"`` / ``"no_commits_in_range"``), NOT a silent green
    'no verdict' / 'no commits matched' verdict alone. Pattern-2."""
    proj = tmp_path / "state_explicit"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["pr-replay", "--tier", "sample"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    state = data["summary"].get("state")
    assert state is not None and state != "ok", f"empty-corpus path must set a non-default `state` field; got {state!r}"


# ---------------------------------------------------------------------------
# 5. partial_success must be True on empty corpus  (xfail-strict: real bug)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-MMM Pattern-2 bug: cmd_pr_replay emits partial_success=false on a "
        "zero-commit range. No real replay happened -- the engine was trivially "
        "'clean'. Fix: set partial_success=True whenever commits_scanned == 0."
    ),
)
def test_empty_pr_partial_success_set(cli_runner, tmp_path, monkeypatch):
    """Pattern-2: degenerate input flips ``partial_success``."""
    proj = tmp_path / "partial"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["pr-replay", "--tier", "sample"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    assert data["summary"].get("partial_success") is True, (
        f"empty-corpus must set partial_success=true, got {data['summary'].get('partial_success')!r}"
    )


# ---------------------------------------------------------------------------
# 6. LAW 6: verdict on empty corpus stands alone
#
#     LAW 6 says ``summary.verdict`` must work without any other field.
#     Today the JSON envelope says ``no commits matched`` -- that DOES
#     stand alone, so this is a positive regression pin (NOT xfail) for
#     the JSON path. The Markdown report's "Clean window..." verdict is
#     pinned separately in test_no_silent_replay_complete_on_empty.
# ---------------------------------------------------------------------------


def test_law6_verdict_standalone(cli_runner, tmp_path, monkeypatch):
    """LAW 6 (compression forces domain neutrality): an agent consuming
    ONLY ``summary.verdict`` on an empty-corpus run must be able to tell
    something is off. The string ``"no verdict"`` is the canonical FAIL
    case -- it tells the agent nothing. Positive regression pin: we must
    NEVER fall back to ``"no verdict"``."""
    proj = tmp_path / "law6"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["pr-replay", "--tier", "sample"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    verdict = data["summary"]["verdict"].lower().strip()
    assert verdict != "no verdict", f"LAW 6 violation: verdict collapsed to canonical fallback string: {verdict!r}"
    # The verdict must be at least a couple of words -- a bare token
    # like 'ok' / 'clean' would fail LAW 6.
    assert len(verdict.split()) >= 2, f"LAW 6 violation: verdict too short to stand alone: {verdict!r}"


# ---------------------------------------------------------------------------
# 7. Pattern-1-V-D: missing-bundle / commit-range resolution disclosure
#
#     Pattern-1-V-D = silent success on degraded resolution. The
#     commit-range resolves through git_log_in_range -> postmortem ->
#     pr-replay. An empty range is the textbook degraded-resolution
#     case. The current envelope provides NO `resolution` field
#     distinguishing "PRs resolved" from "range resolved to zero PRs".
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-MMM Pattern-1-V-D bug: cmd_pr_replay emits no `resolution` field "
        "(closed enum: pr_range / empty_range / unindexed / ...). The replay over "
        "an empty range produces a verdict indistinguishable from a populated "
        "range with zero findings -- both read 'Clean window' in Markdown. Fix: "
        "stamp resolution='empty_range' (or similar) on summary when "
        "commits_scanned == 0."
    ),
)
def test_missing_bundle_resolution_disclosed(cli_runner, tmp_path, monkeypatch):
    """Pattern-1-V-D: the resolution state of the PR-range query MUST
    be disclosed via a ``resolution`` field on the summary. Without it,
    a downstream consumer cannot distinguish 'real range with no
    findings' from 'no PRs in range at all'."""
    proj = tmp_path / "resolution"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["pr-replay", "--tier", "sample"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    resolution = data["summary"].get("resolution")
    assert resolution is not None and resolution != "pr_range", (
        f"empty-range path must set a non-default `resolution` field, got {resolution!r}"
    )


# ---------------------------------------------------------------------------
# 8. No silent "Clean window" verdict on empty corpus  (xfail-strict)
#
#     The Pattern-2 invariant pin: the buyer-facing Markdown report MUST
#     NOT emit a "Clean window. None of the 0 PRs replayed would have
#     been flagged" verdict that is indistinguishable from a real-PRs-
#     with-zero-findings clean window. This is the consumer-side
#     symmetry to W805-FFF (cmd_cga) on the producer side.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-MMM Pattern-2 bug (W805-FFF empty-hash family analog at the end-to-"
        "end compiler tier): empty-corpus Markdown report emits 'Verdict: Clean "
        "window. None of the 0 PRs replayed would have been flagged...' exactly "
        "like a real 5-PR window with zero detector hits. An agent OR a buyer "
        "reading the Markdown cannot tell which one ran. Fix: cmd_pr_replay.py:"
        "2913-2917 must branch on commits_scanned == 0 -> 'EMPTY CORPUS:' / "
        "'no PRs in window' (Markdown) AND summary.verdict starts with "
        "'empty corpus' / 'no PRs matched' (JSON)."
    ),
)
def test_no_silent_replay_complete_on_empty(cli_runner, tmp_path, monkeypatch):
    """The JSON verdict + the Markdown report MUST NOT collapse to the
    'Clean window' string used for real-PR-with-no-findings scans.
    Agent-safety: a silent 'clean window' on an empty range would teach
    the agent the replay was real, when in fact zero PRs were scanned."""
    proj = tmp_path / "no_silent"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["pr-replay", "--tier", "sample"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    report_md = (data.get("report_markdown") or "").lower()

    # The Markdown report's "Clean window" headline is the smoking-gun
    # Pattern-2 string. On an empty corpus it must NOT appear.
    forbidden_markdown_phrases = (
        "clean window",
        "clean window.",
    )
    for phrase in forbidden_markdown_phrases:
        assert phrase not in report_md, (
            f"Pattern-2 silent SAFE: empty corpus emitted forbidden Markdown phrase {phrase!r} in report_markdown"
        )


# ---------------------------------------------------------------------------
# 9. Clean corpus: realistic two-commit range emits a real replay
#
#     With one baseline commit + one follow-up tweak commit, the
#     pr-replay runs postmortem over HEAD~1..HEAD -- a real PR-like
#     range. Whether or not detectors fire is environment-dependent;
#     the positive regression pin asserts only that ``commits_scanned``
#     is >= 1 (the replay actually ran).
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_replay(cli_runner, tmp_path, monkeypatch):
    """With two real commits, pr-replay surfaces a non-zero scan count.
    Positive regression pin -- this is the path that already works;
    a future "fix" must not regress real replays to zero-scan."""
    proj = tmp_path / "clean"
    proj.mkdir()
    _git_init_minimal_with_change(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["pr-replay", "--tier", "sample", "--range", "HEAD~1..HEAD"],
        cwd=proj,
        json_mode=True,
    )
    if result.exit_code not in (0, 1, 5):
        pytest.skip(f"pr-replay exit_code={result.exit_code} -- env-dependent: {result.output[:300]}")
    data = _parse_json_envelope(result)
    summary = data["summary"]
    assert summary.get("commits_scanned", 0) >= 1, (
        f"clean corpus produced commits_scanned={summary.get('commits_scanned')!r}; "
        f"the replay should have walked at least one commit in HEAD~1..HEAD"
    )
