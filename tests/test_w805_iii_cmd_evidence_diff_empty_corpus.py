"""W805-III: Empty-corpus Pattern-2 + Pattern-1-V-D smoke test on ``cmd_evidence_diff``.

Sixty-first-in-batch W805 sweep. Predicate-producer-family peer of
cmd_cga (W805-FFF -- 6 REAL BUGS, empty merkle equals SHA-256(empty)).
Where cmd_cga produces a ``ChangeEvidence``-adjacent in-toto predicate
over the indexed graph, cmd_evidence_diff CONSUMES two
``ChangeEvidence`` packets from disk and diffs them. Same evidence-
compiler axis, opposite verb (produce vs. compare).

W978 first-hypothesis discipline: empirical probe across (truly empty
packets = ``{}`` on both sides) and (clean packet pair = realistic
``ChangeEvidence`` fixtures) shows cmd_evidence_diff DOES exhibit a
Pattern-2 + Pattern-1-V-D bug:

  * Two ``{}`` empty packets -> ``verdict: "no drift between packets"``
    with ``partial_success: false``, NO ``state`` field, NO
    ``resolution`` field. The verdict is BYTE-IDENTICAL to the verdict
    emitted when the user diffs two valid, identical packets (e.g. a
    re-run of the same PR). An agent consuming the envelope cannot tell:
      (a) both packets are valid + identical (real "no drift"), vs.
      (b) both packets are empty/degenerate stubs (no real comparison
          was performed -- the empty packets agree on having no
          structured evidence at all, which is trivially "no drift").

This is a real Pattern-2 silent-SAFE bug AND a Pattern-1-V-D silent-
success-on-degraded-resolution bug. The "no drift" verdict masks
degenerate input -- a reviewer reading the envelope is told the two
packets are equivalent when in fact neither packet carried any
structured evidence to begin with.

BUG class: Pattern-2 (silent fallback) + Pattern-1-V-D (silent success
on degraded resolution). file:line --
``src/roam/commands/cmd_evidence_diff.py:280-304`` (``_build_verdict``
returns "no drift between packets" without checking input-packet
degeneracy) + ``cmd_evidence_diff.py:434-454`` (summary lacks
``state`` / ``resolution`` / ``partial_success: True`` when both
packets are degenerate).

This test pins via xfail-strict on the bug assertions and positive
regression pins on the disclosure-shape invariants that SHOULD hold.

W805 sweep tally update (incl. this entry):
  * Aggregator-family bugs: ~6+ (cmd_brief / cmd_audit / cmd_dogfood
    etc., all _compound_envelope-rooted) -- unchanged.
  * Predicate-producer family: cmd_cga (W805-FFF) -- producer.
  * Predicate-consumer family: cmd_evidence_diff (W805-III) -- NEW.
    First evidence-CONSUMER bug. Producer + consumer are now BOTH
    pinned, completing the empty-input audit symmetry across the
    evidence-compiler thesis (W170/W174).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

# Local conftest helpers
sys.path.insert(0, str(Path(__file__).parent))

_CMD_PATH = Path(__file__).resolve().parents[1] / "src" / "roam" / "commands" / "cmd_evidence_diff.py"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _invoke_diff(old_path: Path, new_path: Path, json_mode: bool = True):
    """Invoke ``roam evidence-diff old new`` via CliRunner."""
    from roam.cli import cli

    runner = CliRunner()
    args = (["--json"] if json_mode else []) + [
        "evidence-diff",
        str(old_path),
        str(new_path),
    ]
    return runner.invoke(cli, args, catch_exceptions=False)


def _parse_envelope(result, command: str = "evidence-diff") -> dict:
    """Parse JSON envelope from CliRunner result."""
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


def _write_empty(path: Path) -> Path:
    """Write a degenerate ``{}`` packet to disk."""
    path.write_text("{}", encoding="utf-8")
    return path


def _write_real(path: Path, **overrides) -> Path:
    """Write a realistic ChangeEvidence-shaped packet to disk."""
    payload = {
        "evidence_id": "ev_test_real",
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "git_range": "abc..def",
        "commit_sha": "d" * 40,
        "diff_hash": "h" * 64,
        "run_ids": ["run_1"],
        "agent_id": "agent:claude-opus-4.7",
        "mode": "safe_edit",
        "verdict": "SAFE",
        "risk_level": None,
        "context_refs": [],
        "changed_subjects": [
            {"kind": "symbol", "qualified_name": "src/foo.py::bar"},
        ],
        "findings": [],
        "policy_decisions": [],
        "tests_required": [],
        "tests_run": [],
        "approvals": [],
        "accepted_risks": [],
        "artifacts": [],
        "actor_refs": [],
        "authority_refs": [],
        "environment_refs": [],
        "redactions": [],
        "content_hash": "0" * 64,
        "signature_ref": None,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Existence guard (W978 + W907 discipline)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """If cmd_evidence_diff.py vanishes, this whole module skips."""
    if not _CMD_PATH.is_file():
        pytest.skip(f"cmd_evidence_diff.py absent at {_CMD_PATH}")
    assert _CMD_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# 2. Empty packets must not crash
# ---------------------------------------------------------------------------


def test_empty_corpus_no_crash(tmp_path):
    """Two ``{}`` packets on disk -- evidence-diff must NOT traceback.
    The current code path tolerates absent fields via ``.get(...)``
    fallbacks, so the diff completes cleanly. We pin this so a future
    refactor cannot accidentally introduce a KeyError on degenerate
    input."""
    p1 = _write_empty(tmp_path / "old.json")
    p2 = _write_empty(tmp_path / "new.json")
    result = _invoke_diff(p1, p2)
    assert "Traceback" not in result.output, result.output
    assert result.exit_code in (0, 1, 5), f"unexpected exit code {result.exit_code}: {result.output[:400]}"


# ---------------------------------------------------------------------------
# 3. Envelope always has a verdict (LAW 6 baseline)
# ---------------------------------------------------------------------------


def test_empty_corpus_envelope_has_verdict(tmp_path):
    """LAW 6: ``summary.verdict`` must be a non-empty string regardless
    of input-packet state. Positive regression pin -- holds under both
    pre- and post-fix behaviour."""
    p1 = _write_empty(tmp_path / "old.json")
    p2 = _write_empty(tmp_path / "new.json")
    result = _invoke_diff(p1, p2)
    data = _parse_envelope(result)
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), f"summary.verdict must be non-empty, got {verdict!r}"


# ---------------------------------------------------------------------------
# 4. State is explicit on empty packets  (xfail-strict: real bug)
#
#     Pattern-2 invariant: degenerate input (both packets are ``{}``)
#     must disclose ``summary.state``. cmd_evidence_diff CURRENTLY
#     emits no state field -- pinned via xfail-strict.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-III Pattern-2 bug: cmd_evidence_diff emits no `state` field when "
        "both input packets are degenerate (empty `{}`). Verdict reads 'no drift "
        "between packets' indistinguishable from a valid identical-packet "
        "comparison. Fix: cmd_evidence_diff.py:280-304 must set "
        "summary.state='empty_packets' (or similar) when both packets are "
        "structurally empty."
    ),
)
def test_empty_corpus_state_explicit(tmp_path):
    """Empty packets -> ``summary.state`` MUST be explicitly set (e.g.
    ``"empty_packets"`` / ``"degenerate_input"``), NOT a silent green
    "no drift" verdict. Pattern-2 invariant."""
    p1 = _write_empty(tmp_path / "old.json")
    p2 = _write_empty(tmp_path / "new.json")
    result = _invoke_diff(p1, p2)
    data = _parse_envelope(result)
    state = data["summary"].get("state")
    assert state is not None and state != "ok", f"empty-packet path must set a non-default `state` field; got {state!r}"


# ---------------------------------------------------------------------------
# 5. partial_success must be True on empty packets  (xfail-strict: real bug)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-III Pattern-2 bug: cmd_evidence_diff emits partial_success=false on "
        "two empty `{}` packets. The diff compared nothing against nothing -- "
        "trivially 'no drift'. Fix: set partial_success=True whenever both packets "
        "have no structured evidence (no schema_version, no content_hash, no refs, "
        "no findings, no completeness data)."
    ),
)
def test_empty_corpus_partial_success_set(tmp_path):
    """Pattern-2: degenerate input flips ``partial_success``."""
    p1 = _write_empty(tmp_path / "old.json")
    p2 = _write_empty(tmp_path / "new.json")
    result = _invoke_diff(p1, p2)
    data = _parse_envelope(result)
    assert data["summary"].get("partial_success") is True, (
        f"empty packets must set partial_success=true, got {data['summary'].get('partial_success')!r}"
    )


# ---------------------------------------------------------------------------
# 6. LAW 6: verdict on empty packets names the degenerate state
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-III Pattern-2 bug: empty-packet verdict reads 'no drift between "
        "packets'. LAW 6 requires the verdict to stand alone; an agent reading "
        "just this string cannot tell the inputs were empty stubs. Fix: include "
        "'empty packets' / 'degenerate' / '(no evidence)' in the verdict when "
        "both packets carry no structured evidence."
    ),
)
def test_empty_corpus_law6_verdict_standalone(tmp_path):
    """LAW 6 (compression forces domain neutrality): the verdict alone
    must signal degeneracy on empty input."""
    p1 = _write_empty(tmp_path / "old.json")
    p2 = _write_empty(tmp_path / "new.json")
    result = _invoke_diff(p1, p2)
    data = _parse_envelope(result)
    verdict = data["summary"]["verdict"].lower()
    degeneracy_signals = (
        "empty",
        "degenerate",
        "no evidence",
        "no packets",
        "uninitialized",
        "stub",
    )
    assert any(sig in verdict for sig in degeneracy_signals), (
        f"verdict {verdict!r} contains no empty-packet signal (any of {degeneracy_signals})"
    )


# ---------------------------------------------------------------------------
# 7. Missing-packet disclosure (Click-level guard -- positive regression)
#
#     A non-existent path should exit non-zero with a usage error, NOT
#     a green "no drift" envelope. This is already enforced by Click's
#     ``Path(exists=True)`` argument validator -- we pin the contract.
# ---------------------------------------------------------------------------


def test_missing_packet_disclosure(tmp_path):
    """Non-existent NEW_PATH must fail loudly via Click validator (exit 2
    + usage error), never silently succeed with 'no drift'. Positive
    regression pin -- this guard is provided by ``click.Path(exists=True)``
    and we MUST NOT regress it."""
    p1 = _write_empty(tmp_path / "old.json")
    missing = tmp_path / "does_not_exist.json"
    result = _invoke_diff(p1, missing)
    assert result.exit_code != 0, f"missing path produced silent exit-0: {result.output[:300]}"
    # Standard Click usage error: exit 2, message names the missing path.
    assert "does not exist" in result.output.lower() or result.exit_code == 2, (
        f"missing-path failure must surface the missing file: {result.output[:300]}"
    )


# ---------------------------------------------------------------------------
# 8. No silent "no drift" verdict on empty packets  (xfail-strict)
#
#     The Pattern-2 invariant pin: empty-packet input MUST NOT emit a
#     verdict indistinguishable from a real valid-identical-packets
#     comparison. This is the W805-FFF empty-hash family on the
#     consumer side -- two empty packets agree trivially on having no
#     evidence, but the diff currently treats that as success.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-III Pattern-2 bug (W805-FFF empty-hash family analog on consumer "
        "side): empty-packet verdict reads 'no drift between packets' exactly "
        "like a valid identical-packet diff. An agent reading the verdict cannot "
        "tell which one ran. Fix: prefix with 'EMPTY PACKETS:' / 'degenerate "
        "input' / 'no evidence to diff' when both packets have no structured "
        "evidence."
    ),
)
def test_no_silent_no_diff_on_empty(tmp_path):
    """The verdict MUST NOT collapse to the same green 'no drift' string
    used for valid identical packets. Agent-safety: a silent 'no drift'
    on two empty packets would teach the agent the comparison was real,
    when in fact neither packet contained any evidence."""
    p1 = _write_empty(tmp_path / "old.json")
    p2 = _write_empty(tmp_path / "new.json")
    result = _invoke_diff(p1, p2)
    data = _parse_envelope(result)
    verdict = data["summary"]["verdict"].lower()
    forbidden_verdicts = (
        "no drift between packets",
        "no drift detected",
    )
    for forbidden in forbidden_verdicts:
        assert forbidden not in verdict, (
            f"Pattern-2 silent SAFE: empty packets emitted forbidden verdict {forbidden!r}: {verdict!r}"
        )


# ---------------------------------------------------------------------------
# 9. Clean corpus: realistic packets emit a real diff (positive regression)
#
#     With two genuine ChangeEvidence-shaped packets that differ in a
#     concrete field (verdict), the diff must report the delta. This is
#     the path that already works -- positive regression pin so a future
#     "fix" cannot accidentally regress real comparisons.
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_diff(tmp_path):
    """With two realistic ChangeEvidence packets that differ on
    ``verdict``, the diff surfaces a changed-verdict delta. Positive
    regression pin."""
    p1 = _write_real(tmp_path / "old.json", verdict="SAFE", content_hash="a" * 64)
    p2 = _write_real(tmp_path / "new.json", verdict="REVIEW", content_hash="b" * 64)
    result = _invoke_diff(p1, p2)
    data = _parse_envelope(result)
    summary = data["summary"]

    # The real diff should report changed verdicts AND hash drift.
    assert summary.get("changed_verdicts", 0) >= 1, f"clean diff failed to surface changed_verdicts: {summary!r}"
    assert summary.get("hash_drift") is True, f"clean diff failed to surface hash_drift: {summary!r}"
    # And the verdict must NOT be the empty-input "no drift" string.
    verdict = summary["verdict"].lower()
    assert "no drift" not in verdict, f"real-diff verdict regressed to empty-input string: {verdict!r}"
