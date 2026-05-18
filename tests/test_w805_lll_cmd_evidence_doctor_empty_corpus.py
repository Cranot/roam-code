"""W805-LLL: Empty-corpus Pattern-2 + Pattern-1-V-C smoke test on ``cmd_evidence_doctor``.

Sixty-fourth-in-batch W805 sweep. Single-packet consumer twin of
cmd_evidence_diff (W805-III -- 4 REAL BUGS, two-packet consumer). Where
cmd_evidence_diff CONSUMES two ``ChangeEvidence`` packets, cmd_evidence_doctor
CONSUMES one. Same evidence-compiler axis (predicate-consumer family), same
loader idiom (``_load_raw_packet`` reads from disk via ``Path.read_text``),
same Pattern-1-V-D / Pattern-2 audit candidates.

W978 first-hypothesis discipline: empirical probe across (truly empty packet
= ``{}``) and (missing packet = nonexistent path) and (malformed JSON) and
(JSON array) shows cmd_evidence_doctor has one remaining pinned bug:

  BUG #1 (FIXED -- Pattern-1-V-C "Empty/missing-input crash"). The missing
  packet path now binds ``source_label`` before ``Path.read_text()`` and emits
  a structured FAIL envelope instead of leaking ``UnboundLocalError``. The
  positive test below keeps that regression closed.

  BUG #2 (REMAINING -- Pattern-1-V-B "Structured signal vs. process signal
  disagree"). An empty ``{}`` packet emits ``level: "FAIL"`` +
  ``verdict: "FAIL: packet shape invalid"`` -- BUT the process exits with
  code 0. An MCP/CI consumer relying on exit code would treat the FAIL as
  success. The structured signal disagrees with the process signal. By
  contrast, malformed-JSON and JSON-array inputs exit 2 (correct).

This test keeps positive regression pins on the fixed disclosure-shape
invariants and xfail-strict only on the remaining exit-code disagreement.

W805 sweep tally update (incl. this entry):
  * Predicate-consumer family: cmd_evidence_diff (W805-III, two-packet
    consumer) + cmd_evidence_doctor (W805-LLL, single-packet consumer) --
    NEW. Both ends of the single/double-input consumer axis are now pinned.
  * Predicate family count: producer (cmd_cga / W805-FFF) + consumer
    (cmd_evidence_diff / W805-III) + consumer (cmd_evidence_doctor /
    W805-LLL) = 3 predicate-family entries with REAL BUGS.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

# Local conftest helpers
sys.path.insert(0, str(Path(__file__).parent))

_CMD_PATH = Path(__file__).resolve().parents[1] / "src" / "roam" / "commands" / "cmd_evidence_doctor.py"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _invoke_doctor(packet_path: Path, json_mode: bool = True):
    """Invoke ``roam evidence-doctor <packet>`` via CliRunner."""
    from roam.cli import cli

    runner = CliRunner()
    args = (["--json"] if json_mode else []) + [
        "evidence-doctor",
        str(packet_path),
    ]
    return runner.invoke(cli, args, catch_exceptions=False)


def _parse_envelope(result, command: str = "evidence-doctor") -> dict:
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
        "content_hash": None,
        "signature_ref": None,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Existence guard (W978 + W907 discipline)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """If cmd_evidence_doctor.py vanishes, this whole module skips."""
    if not _CMD_PATH.is_file():
        pytest.skip(f"cmd_evidence_doctor.py absent at {_CMD_PATH}")
    assert _CMD_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# 2. Empty packet must not crash (positive regression -- already holds)
# ---------------------------------------------------------------------------


def test_empty_packet_no_crash(tmp_path):
    """A ``{}`` packet on disk -- evidence-doctor must NOT traceback. The
    current code tolerates absent fields via ``.get(...)`` fallbacks, so the
    diagnostic completes cleanly. We pin this so a future refactor cannot
    accidentally introduce a KeyError on degenerate input."""
    p = _write_empty(tmp_path / "empty.json")
    result = _invoke_doctor(p)
    assert "Traceback" not in result.output, result.output
    # exit_code 0 is the current (buggy) behaviour; we pin to "not 1" so the
    # process signal can be tightened later (the disagreement is pinned
    # separately as xfail-strict in test_empty_packet_exit_code_matches_fail).
    assert result.exit_code in (0, 2), f"unexpected exit code {result.exit_code}: {result.output[:400]}"


# ---------------------------------------------------------------------------
# 3. Envelope always has a verdict (LAW 6 baseline)
# ---------------------------------------------------------------------------


def test_empty_packet_envelope_has_verdict(tmp_path):
    """LAW 6: ``summary.verdict`` must be a non-empty string regardless of
    input-packet state. Positive regression pin."""
    p = _write_empty(tmp_path / "empty.json")
    result = _invoke_doctor(p)
    data = _parse_envelope(result)
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), f"summary.verdict must be non-empty, got {verdict!r}"


# ---------------------------------------------------------------------------
# 4. State / level is explicit on empty packets (positive regression)
# ---------------------------------------------------------------------------


def test_empty_packet_state_explicit(tmp_path):
    """Empty packets -> ``summary.level`` MUST be explicitly set to FAIL (not
    a default green level). The doctor already does this correctly via the
    schema-version-missing path; pin so it does not regress."""
    p = _write_empty(tmp_path / "empty.json")
    result = _invoke_doctor(p)
    data = _parse_envelope(result)
    level = data["summary"].get("level")
    assert level == "FAIL", f"empty packet must surface level=FAIL, got {level!r}"


# ---------------------------------------------------------------------------
# 5. partial_success must be True on empty packets (positive regression)
# ---------------------------------------------------------------------------


def test_empty_packet_partial_success_set(tmp_path):
    """Pattern-2: degenerate input flips ``partial_success``."""
    p = _write_empty(tmp_path / "empty.json")
    result = _invoke_doctor(p)
    data = _parse_envelope(result)
    assert data["summary"].get("partial_success") is True, (
        f"empty packet must set partial_success=true, got {data['summary'].get('partial_success')!r}"
    )


# ---------------------------------------------------------------------------
# 6. LAW 6: verdict on empty packets names the FAIL state
# ---------------------------------------------------------------------------


def test_empty_packet_law6_verdict_standalone(tmp_path):
    """LAW 6 (compression forces domain neutrality): the verdict alone must
    surface FAIL semantics on degenerate input. Positive regression pin --
    today's verdict ``"FAIL: packet shape invalid"`` satisfies this."""
    p = _write_empty(tmp_path / "empty.json")
    result = _invoke_doctor(p)
    data = _parse_envelope(result)
    verdict = data["summary"]["verdict"].lower()
    assert verdict.startswith("fail"), f"empty-packet verdict must start with FAIL (LAW 6 standalone), got {verdict!r}"


# ---------------------------------------------------------------------------
# 7. Missing-packet resolution disclosure (Pattern-1-V-D)
#
#     The doctor's _load_raw_packet builds its own structured FAIL envelope
#     for missing files. This used to crash with UnboundLocalError because
#     ``source_label`` was referenced in the ``except OSError`` handler before
#     it was bound. It is now a positive regression pin.
# ---------------------------------------------------------------------------


def test_missing_packet_resolution_disclosed(tmp_path):
    """A nonexistent packet path must produce a structured FAIL envelope
    instead of a Click-level traceback or variable-name leak."""
    missing = tmp_path / "does_not_exist.json"
    result = _invoke_doctor(missing)
    # No UnboundLocalError leaking to the user
    assert "UnboundLocalError" not in result.output, (
        f"OSError handler crashed with UnboundLocalError: {result.output[:400]}"
    )
    assert "source_label" not in result.output, f"variable-name leak in error: {result.output[:400]}"
    data = _parse_envelope(result)
    assert data["summary"]["verdict"].startswith("FAIL:")
    assert data["summary"]["partial_success"] is True
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# 8. No silent PASS on empty packets (W805-III empty-input family)
# ---------------------------------------------------------------------------


def test_no_silent_pass_on_empty(tmp_path):
    """The verdict MUST NOT collapse to a green PASS/SAFE on an empty packet.
    Positive regression pin -- the doctor already does this correctly via
    schema_ok=False on missing schema_version."""
    p = _write_empty(tmp_path / "empty.json")
    result = _invoke_doctor(p)
    data = _parse_envelope(result)
    verdict = data["summary"]["verdict"].lower()
    forbidden = ("pass:", "safe", "completed", "no drift", "healthy")
    for f in forbidden:
        assert f not in verdict, (
            f"Pattern-2 silent SAFE: empty packet emitted forbidden verdict signal {f!r}: {verdict!r}"
        )


# ---------------------------------------------------------------------------
# 9. Empty-packet exit code disagrees with FAIL verdict (Pattern-1-V-B)
#
#     The doctor sets ``summary.level = "FAIL"`` AND
#     ``summary.verdict = "FAIL: packet shape invalid"`` on an empty packet,
#     yet exits with code 0. An MCP/CI consumer routing on exit code would
#     treat the FAIL as success. Pinned via xfail-strict.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-LLL Pattern-1-V-B REAL BUG: empty `{}` packet emits "
        "level=FAIL + verdict='FAIL: packet shape invalid' but exits with "
        "process exit code 0. Malformed JSON and JSON-array inputs correctly "
        "exit 2 via the hard-load-failure path. The valid-but-empty packet "
        "path emits FAIL in the envelope but reaches the normal `return` at "
        "the end of the JSON branch (no sys.exit). An MCP/CI consumer routing "
        "on exit code reads success; an agent reading the envelope reads FAIL. "
        "Fix: when level=='FAIL' in the success-path branch, exit with a "
        "non-zero code (matching the hard-load FAIL path which exits 2)."
    ),
)
def test_empty_packet_exit_code_matches_fail(tmp_path):
    """The process exit code must agree with the structured FAIL verdict."""
    p = _write_empty(tmp_path / "empty.json")
    result = _invoke_doctor(p)
    data = _parse_envelope(result)
    assert data["summary"]["level"] == "FAIL"
    # FAIL verdict must exit non-zero (Pattern-1-V-B disagreement).
    assert result.exit_code != 0, f"FAIL verdict with exit-code 0 disagreement: exit={result.exit_code}"


# ---------------------------------------------------------------------------
# 10. Clean corpus: realistic packet emits a real diagnosis (positive regression)
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_diagnosis(tmp_path):
    """With a realistic ChangeEvidence packet that has a valid
    ``schema_version``, the doctor must produce a real diagnosis -- NOT the
    'packet shape invalid' empty-input verdict. Positive regression pin so
    a future fix to the empty-packet bug cannot regress real diagnostics."""
    p = _write_real(tmp_path / "real.json")
    result = _invoke_doctor(p)
    data = _parse_envelope(result)
    summary = data["summary"]

    # A real packet has a schema_version -> schema_ok semantics apply.
    assert summary.get("schema_version") == "1.0.0"
    # Banner should be insufficient (no findings, no completeness) but not
    # 'packet shape invalid'.
    assert summary.get("banner_tier") in ("insufficient", "partial"), (
        f"real packet should reach the banner path, got tier={summary.get('banner_tier')!r}"
    )
    verdict = summary["verdict"].lower()
    assert "packet shape invalid" not in verdict, f"real packet verdict regressed to empty-input string: {verdict!r}"
