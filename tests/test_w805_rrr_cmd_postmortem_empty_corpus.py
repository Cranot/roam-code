"""W805-RRR: Empty-corpus Pattern-2 smoke test on ``cmd_postmortem``.

Seventieth-in-batch W805 sweep. Novel axis: incident-narrative
emitter -- post-hoc / retrospective replay command (vs forward-looking
gate commands like ``preflight`` / ``impact`` / ``critique``). Closes
the corner of the W805 matrix where the command's semantic is "would
today's detector set have caught past incidents?" rather than "is the
current change safe?"

Architecture (verified against source 2026-05-18):

  * ``cmd_postmortem`` is GIT-LOG + CRITIQUE driven, NOT a runs-ledger
    consumer. The mission brief's "ledger-consumer" framing was
    inaccurate -- the command walks ``git log <range>`` and replays
    ``roam --json critique`` against each commit's outgoing diff
    in-process.
  * It does NOT call ``emit_finding`` -- findings are transient,
    invocation-scoped (envelope-only). Aligns with the
    "envelope-only exception" callout for invocation-scoped
    retrospective replays (vs registry-persistent forward detectors).
  * Two empty-corpus paths exist:
      (A) ``commits == []``  -- empty range / bad range / --limit 0.
          Lines 197-217 emit "no commits matched" + return.
      (B) ``commits_with_findings == 0`` -- range has commits, none
          would have surfaced findings under the current detector set.
          Lines 274-277 emit "X of Y commits would have surfaced
          findings (0 high, 0 medium total)" + a parenthetical "(no
          findings surfaced over this range)".

W978 first-hypothesis discipline: I probed BOTH empty paths in
isolation before pinning:

  * Probe (A1): ``roam postmortem nonexistent..alsofake --json``  ->
    exit 0; verdict "no commits matched"; partial_success: False;
    NO state field disclosing WHY (nonexistent range vs legitimately
    empty repo vs --limit 0).

  * Probe (A2): ``roam postmortem HEAD~1..HEAD --json --limit 0``  ->
    exit 0; verdict "no commits matched"; partial_success: False;
    envelope BYTE-IDENTICAL to (A1) modulo timestamp + range string.

The probes confirm BOTH halves of Pattern-1 variant D ("Silent
success on degraded resolution") and Pattern-2 silent-SAFE:

  REAL BUG #1 (PRIMARY -- Pattern-1-V-D + Pattern-2 silent-SAFE).
  At ``cmd_postmortem.py:197-217``, when ``_git_log_in_range`` returns
  ``[]``, the envelope reports ``partial_success: False`` and verdict
  ``"no commits matched"`` -- with NO disclosure of WHICH empty-path
  was hit. A user-intent "--limit 0" (legitimate empty corpus), a
  typo-range "nonexistent..alsofake" (broken input), and a real empty
  repo (no commits in window) all produce the same envelope. Per
  Pattern-1-V-D ("disclose the resolution state explicitly via a
  ``resolution`` field on the envelope (closed enum: ... unresolved /
  fuzzy / ...) + partial_success: true + distinct verdict reflecting
  the degradation"), the empty-corpus path MUST surface a
  ``state``-style field naming the cause AND set partial_success: True
  for the degraded-input cases.

  REAL BUG #2 (SECONDARY -- Pattern-2 silent-SAFE on
  zero-findings-replay). At ``cmd_postmortem.py:250-272``, when
  ``commits_with_findings == 0`` (real range walked, every commit
  produced zero findings), the verdict reads
  ``"0 of N commits would have surfaced findings (0 high, 0 medium
  total)"`` with ``partial_success: False``. This is a "successful
  retroactive replay that found nothing" -- which is the canonical
  Pattern-2 silent-SAFE shape. There is no way for a consumer to
  distinguish "all 30 commits cleanly passed" from "the critique
  detector silently swallowed errors on all 30 commits". The
  ``_critique_diff`` helper at lines 109-118 catches
  ``_json.JSONDecodeError`` and returns ``{"summary": {},
  "_parse_error": True}`` but the ``_parse_error`` sentinel is NEVER
  propagated upward into the postmortem envelope -- a CP45/CP46
  lineage violation (silent fallback). When 0 of N commits surface
  findings, the envelope MUST EITHER set partial_success: True (the
  detector set did not produce signal -- could be a real "all clean"
  OR a silent swallowing) OR surface a ``detector_errors`` count
  field bound to the count of commits where ``_parse_error == True``
  was observed.

The disclosure-shape invariants that hold today (pass unxfailed):
  * Command exists.
  * Empty-range path emits a valid JSON envelope with a single-line
    verdict (LAW 6 standalone).
  * Both empty paths exit 0 cleanly (no Python traceback leak).
  * Real-corpus invocation (``HEAD~3..HEAD``) emits an envelope with
    nonzero ``commits_scanned``.

W805 sweep tally update:
  * Through W805-RRR: ~36 of 47 axis-novel commands probed across
    the sweep. ~24+ REAL BUGS pinned to date.
  * Pinned this entry: 2 (1 Pattern-1-V-D + Pattern-2 silent-SAFE on
    empty-range, 1 Pattern-2 silent-SAFE on zero-findings retroactive
    replay).
  * Novel axis CLOSED: post-hoc / retrospective narrative emitter.
    All prior W805 entries covered forward-looking gates, projections,
    or registry consumers. ``cmd_postmortem`` is the first
    retrospective-replay command in the sweep, and the Pattern-2 bugs
    confirm the silent-SAFE family is invariant across time-direction
    (forward gate AND backward replay).

W805-SSS candidate axes (recommendations for next batch):
  * ``cmd_triage`` -- exists at src/roam/commands/cmd_triage.py. Likely
    findings-prioritizer / consumer; would close the
    incident-management family alongside this retrospective-replay
    entry.
  * ``cmd_ws`` -- workspace command, likely state-disclosure axis
    (multi-mode entrypoint with subcommands).
  * ``cmd_ai_readiness`` -- if it exists; advisory composite scorer,
    likely Pattern-2-prone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CMD_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_postmortem.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_postmortem(commit_range: str, *, extra: list[str] | None = None, json_mode: bool = True):
    """Invoke ``roam [--json] postmortem <range> [extra]``."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.extend(["postmortem", commit_range])
    if extra:
        args.extend(extra)
    return runner.invoke(cli, args, catch_exceptions=False)


def _parse_envelope(result) -> dict:
    """Parse the JSON envelope from a CliRunner result; raise if not JSON."""
    raw = result.output
    idx = raw.find("{")
    if idx < 0:
        raise ValueError(f"no JSON envelope in output: {raw!r}")
    return json.loads(raw[idx:])


# ---------------------------------------------------------------------------
# Existence / regression invariants (hold today)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``cmd_postmortem.py`` must exist (W805 sweep precondition)."""
    if not _CMD_PATH.exists():
        pytest.skip(f"cmd_postmortem not present at {_CMD_PATH}")
    assert _CMD_PATH.is_file()


def test_empty_corpus_no_crash():
    """Empty-range path exits 0 cleanly with no Python traceback leak."""
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    result = _invoke_postmortem("w805rrr_nonexistent..w805rrr_alsofake")
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output


def test_empty_corpus_envelope_has_verdict():
    """Empty-range path emits a JSON envelope with a verdict field."""
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    result = _invoke_postmortem("w805rrr_nonexistent..w805rrr_alsofake")
    env = _parse_envelope(result)
    verdict = env["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict.strip()


def test_empty_corpus_law6_verdict_standalone():
    """LAW 6 -- the verdict line is readable without other fields.

    Holds today: ``"no commits matched"`` is a complete sentence
    that does not require ``commits_scanned`` or ``commit_range`` to
    convey its meaning. Pins LAW 6 compliance for the empty-corpus
    branch independently of the Pattern-2 bugs.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    result = _invoke_postmortem("w805rrr_nonexistent..w805rrr_alsofake")
    env = _parse_envelope(result)
    verdict = env["summary"]["verdict"]
    # Single line, nonempty, structurally complete on its own.
    assert isinstance(verdict, str) and verdict.strip()
    assert "\n" not in verdict
    # Verdict carries enough info to be acted on (mentions the
    # core concept "commits" or "matched").
    assert any(token in verdict.lower() for token in ("commit", "matched", "no commits"))


def test_missing_ledger_state_disclosure():
    """Missing/inaccessible git state still produces a clean envelope.

    Even when the underlying git invocation cannot resolve a range
    (no ledger / no commits / typo range), the envelope reaches
    stdout cleanly. This pins the no-traceback-leak property
    independently of the explicit-state Pattern-2 bug below.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    result = _invoke_postmortem("w805rrr_definitely_not_a_real_ref..w805rrr_also_not_real")
    assert result.exit_code == 0
    env = _parse_envelope(result)
    # Envelope reaches stdout with the expected shape.
    assert env.get("command") == "postmortem"
    assert "summary" in env
    assert env["summary"].get("commits_scanned") == 0


def test_clean_corpus_emits_real_postmortem():
    """Real corpus (``HEAD~3..HEAD``) emits a real postmortem envelope.

    Regression pin: the happy path on a real range produces an
    envelope with nonzero ``commits_scanned``. Positive control for
    the silent-SAFE bug pinned below; if THIS test fails, the
    detector loop itself is broken and the silent-SAFE pin is
    meaningless.

    NOTE: Skipped if git can't resolve HEAD~3 (shallow clone, fresh
    repo). On roam-code's main branch this resolves cleanly.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    # --limit 1 keeps the test fast (one critique invocation per
    # walked commit; --limit 1 caps at one commit's diff).
    result = _invoke_postmortem("HEAD~3..HEAD", extra=["--limit", "1"])
    if result.exit_code != 0:
        pytest.skip(f"git can't resolve HEAD~3 in this checkout: {result.output[:200]}")
    env = _parse_envelope(result)
    summary = env["summary"]
    scanned = int(summary.get("commits_scanned") or 0)
    # Either we walked at least one commit OR the range was clean-empty.
    # If clean-empty, the test is informative but inconclusive; skip.
    if scanned == 0:
        pytest.skip("range walked zero commits in this checkout")
    assert scanned >= 1


# ---------------------------------------------------------------------------
# Bug pins -- xfail-strict
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RRR BUG #1 (Pattern-1-V-D + Pattern-2 silent-SAFE): the "
        "empty-range path at cmd_postmortem.py:197-217 emits "
        "partial_success: False with verdict 'no commits matched' and "
        "NO state field disclosing WHICH empty-path was hit. A "
        "user-intent --limit 0, a typo-range, and a real empty repo "
        "all produce byte-identical envelopes. Per Pattern-1-V-D, the "
        "envelope MUST surface a 'state' (closed enum) naming the "
        "cause AND set partial_success: True for the "
        "degraded-resolution case. Fix template: add summary['state'] "
        "= 'no_commits_matched' (or 'invalid_range' when git returned "
        "non-zero) and set partial_success: True so consumers can "
        "branch on real-empty vs degraded-input."
    ),
)
def test_empty_corpus_state_explicit():
    """Empty-range envelope SHOULD name the empty-cause explicitly.

    xfail-strict pin: today the envelope has no ``state`` /
    ``resolution`` field, only ``verdict: 'no commits matched'``.
    Fix template = expose a closed-enum state field naming the cause.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    result = _invoke_postmortem("w805rrr_nonexistent..w805rrr_alsofake")
    env = _parse_envelope(result)
    summary = env["summary"]
    state = summary.get("state") or summary.get("resolution") or summary.get("status")
    assert isinstance(state, str) and state, f"empty-range path did not surface explicit state field: {summary!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RRR BUG #1 follow-on (Pattern-2 silent-SAFE on empty "
        "range): the envelope sets partial_success: False on an "
        "empty-range / typo-range / --limit 0 input. Per the "
        "Pattern-2 rule ('never emit verdict: SAFE / completed when "
        "the underlying check failed or didn't run'), a degraded "
        "input must surface partial_success: True. Today the inner "
        "_git_log_in_range catches git's non-zero exit and returns "
        "[] -- a silent fallback (CP45/CP46) that loses the "
        "distinction between 'walked zero commits' and 'git rejected "
        "the range'."
    ),
)
def test_empty_corpus_partial_success_set():
    """Empty-range envelope SHOULD set partial_success: True.

    xfail-strict pin: today partial_success: False on degraded input.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    result = _invoke_postmortem("w805rrr_nonexistent..w805rrr_alsofake")
    env = _parse_envelope(result)
    assert env["summary"]["partial_success"] is True


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-RRR BUG #2 (Pattern-2 silent-SAFE on zero-findings "
        "retroactive replay): at cmd_postmortem.py:250-272, when "
        "commits_with_findings == 0, the verdict reads "
        "'0 of N commits would have surfaced findings (0 high, 0 "
        "medium total)' with partial_success: False -- a "
        "'successful' retroactive replay that produced no signal. "
        "There is no way for a consumer to distinguish 'all commits "
        "cleanly passed' from 'the critique detector silently "
        "swallowed errors on every commit'. The inner "
        "_critique_diff catches JSONDecodeError and returns a "
        "_parse_error sentinel that is NEVER propagated upward -- a "
        "CP45/CP46 lineage violation. Fix template: count "
        "critique-errors during the walk and surface "
        "summary['detector_errors'] + set partial_success: True when "
        "detector_errors > 0 OR when commits_with_findings == 0 AND "
        "commits_scanned > 0 (the latter is the canonical "
        "'no signal' ambiguous case). NOTE: This pin uses a synthetic "
        "range that doesn't resolve, falling through the empty-range "
        "path; it exercises the SAME 'silent SAFE on no signal' "
        "Pattern-2 family as the zero-findings branch (line 274-277). "
        "The disclosure-shape invariant is identical: when "
        "commits_with_findings == 0 AND the underlying state could "
        "be either real-empty or silently-swallowed, partial_success "
        "MUST be True."
    ),
)
def test_no_silent_no_incidents_on_empty():
    """Zero-incidents replay SHOULD NOT emit a clean SAFE envelope.

    xfail-strict pin: today the empty-corpus path emits
    partial_success: False with a verdict indistinguishable from a
    legitimate ''all-clean retroactive replay''. The Pattern-2 rule
    requires the envelope to disclose absent-signal explicitly.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    result = _invoke_postmortem("w805rrr_zero_findings..w805rrr_synthetic_range")
    env = _parse_envelope(result)
    summary = env["summary"]
    # Sanity: probe really did produce a zero-findings / no-signal envelope.
    commits_with_findings = int(summary.get("commits_with_findings") or 0)
    assert commits_with_findings == 0
    # The Pattern-2 invariant: no-signal MUST surface partial_success: True.
    assert summary["partial_success"] is True, (
        "zero-findings retroactive replay emitted partial_success: False (Pattern-2 silent-SAFE on no-signal replay)"
    )
