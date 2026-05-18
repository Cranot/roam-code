"""W805-NNN: Empty-corpus Pattern-2 smoke test on ``cmd_evidence_oscal``.

Sixty-sixth-in-batch W805 sweep. Closes the predicate-family triangle started
by:

  * W805-FFF (cmd_cga / 6 REAL BUGS) -- evidence PRODUCER.
  * W805-III (cmd_evidence_diff / 4 REAL BUGS) -- two-packet CONSUMER.
  * W805-LLL (cmd_evidence_doctor / 2 REAL BUGS) -- single-packet CONSUMER.
  * W805-NNN (this) -- ChangeEvidence -> OSCAL v1.2 PROJECTION.

``cmd_evidence_oscal`` projects one ``ChangeEvidence`` packet (``--kind
assessment-results``) into an OSCAL v1.2 Assessment Results document, or
projects the wheel-bundled control-mapping YAML into an OSCAL Control
Mapping document. We probe the AR projection because that is the
ChangeEvidence-consuming branch; the CM branch is YAML-driven and already
gated by ``map_path.exists()``.

W978 first-hypothesis discipline: empirical probe across
  - empty packet = ``{}``                       (P1-V-B: TypeError leak)
  - minimal valid packet = ``{evidence_id, schema_version}`` (P2: vacuous)
  - missing ``--evidence`` path                 (gated; ok)
  - non-existent ``--evidence`` path            (gated; ok)
shows TWO real bugs:

  BUG #1 (PRIMARY -- Pattern-2 silent-success on vacuous projection). A
  minimal-but-valid packet (``{"evidence_id": "...", "schema_version":
  "1.0.0", "producer": {}}``) at ``cmd_evidence_oscal.py:368-397`` produces
  ``partial_success: False`` and verdict ``"emitted OSCAL v1.2
  assessment-results with 1 results, 0 findings, 0 observations"``. This is
  the projection-family analog of W805-LLL's silent-success bug: the
  projection emitted a "successful" OSCAL document that contains ZERO
  findings + ZERO observations -- a vacuous AR -- but the envelope reports
  it as a clean success indistinguishable from a fully-populated AR. Per
  the Pattern-2 rule ("never emit success when underlying check did not
  run/did not produce signal"), an AR projection with 0 findings + 0
  observations MUST surface ``partial_success: True`` so consumers can
  branch on real-vs-vacuous projections.

  BUG #2 (SECONDARY -- Pattern-1-V-B "Structured signal lost by intermediate
  layer"). At ``cmd_evidence_oscal.py:318-328`` the AR branch wraps
  ``ChangeEvidence.from_canonical_json_with_drops`` in
  ``try/except ValueError``. An empty ``{}`` packet, however, raises
  ``TypeError("ChangeEvidence.__init__() missing 1 required positional
  argument: 'evidence_id'")`` -- which is NOT caught. The result is a raw
  Click error string ``Error: ChangeEvidence.__init__()...`` on stdout +
  exit 1, BYPASSING the JSON envelope path entirely even under ``--json``.
  This is the same shape as W805-LLL's ``UnboundLocalError`` leak: a
  load-time exception class the explicit ``except`` clause does not cover,
  so the structured FAIL envelope never reaches the consumer.

The disclosure-shape invariants that SHOULD hold today (and pass
unxfailed):
  * The command exists.
  * Non-existent ``--evidence`` path produces a clean ClickException
    (gated upstream of the loader).
  * Missing ``--evidence`` flag produces a clean ClickException.
  * Clean fixture (``v1_with_refs.json``) produces a real AR document with
    nonzero findings/observations.

W805 sweep tally update (incl. this entry):
  * Predicate-family triangle CLOSED: producer (cmd_cga / W805-FFF) +
    two-packet consumer (cmd_evidence_diff / W805-III) + single-packet
    consumer (cmd_evidence_doctor / W805-LLL) + projection
    (cmd_evidence_oscal / W805-NNN) = 4 predicate-family entries with
    REAL BUGS across producer / consumer / projection roles.
  * Bugs pinned this entry: 2 (1 Pattern-2 vacuous + 1 Pattern-1-V-B
    TypeError leak).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

# Repo root anchor.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CMD_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_evidence_oscal.py"
_CLEAN_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "evidence" / "v1_with_refs.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_oscal_ar(evidence_path: Path | None, json_mode: bool = True):
    """Invoke ``roam evidence-oscal --kind assessment-results --evidence <p>``."""
    from roam.cli import cli

    runner = CliRunner()
    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.extend(["evidence-oscal", "--kind", "assessment-results"])
    if evidence_path is not None:
        args.extend(["--evidence", str(evidence_path)])
    return runner.invoke(cli, args, catch_exceptions=False)


def _parse_envelope(result) -> dict:
    """Parse the JSON envelope from a CliRunner result; raise if not JSON."""
    raw = result.output
    idx = raw.find("{")
    if idx < 0:
        raise ValueError(f"no JSON envelope in output: {raw!r}")
    return json.loads(raw[idx:])


def _write_packet(tmp_path: Path, payload) -> Path:
    """Write payload (dict or raw string) as a JSON packet under tmp_path."""
    p = tmp_path / "packet.json"
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Existence / sanity-shape pins (regression invariants that hold today)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``cmd_evidence_oscal.py`` must exist (W805 sweep precondition)."""
    if not _CMD_PATH.exists():
        pytest.skip(f"cmd_evidence_oscal not present at {_CMD_PATH}")
    assert _CMD_PATH.is_file()


def test_law6_verdict_standalone(tmp_path):
    """LAW 6 -- the verdict line must be readable without other fields.

    Even on the vacuous-projection path, the verdict line stands alone
    (Pattern-2 violation is the wording, not the verdict's structural
    standalone-readability). Holds today; pins LAW 6 compliance for the
    happy/vacuous branch.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_evidence_oscal not present")

    minimal = {"evidence_id": "law6-id", "schema_version": "1.0.0", "producer": {}}
    pkt = _write_packet(tmp_path, minimal)
    result = _invoke_oscal_ar(pkt, json_mode=True)
    assert result.exit_code == 0, result.output
    env = _parse_envelope(result)
    verdict = env["summary"]["verdict"]
    # Verdict is one line, nonempty, names the document kind explicitly.
    assert isinstance(verdict, str) and verdict.strip()
    assert "\n" not in verdict
    assert "assessment-results" in verdict.lower()


def test_clean_corpus_emits_real_oscal():
    """Clean fixture -> a real AR document with nonzero findings/observations.

    Regression pin: the AR projection actually works on a populated packet.
    This is the positive control for the vacuous-projection bug below.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_evidence_oscal not present")
    if not _CLEAN_FIXTURE.exists():
        pytest.skip(f"clean fixture not present at {_CLEAN_FIXTURE}")

    result = _invoke_oscal_ar(_CLEAN_FIXTURE, json_mode=True)
    assert result.exit_code == 0, result.output
    env = _parse_envelope(result)
    summary = env["summary"]
    # A real packet projects into a real AR with findings + observations.
    finding_count = int(summary.get("finding_count") or 0)
    observation_count = int(summary.get("observation_count") or 0)
    assert finding_count > 0 or observation_count > 0, (
        f"clean fixture produced vacuous AR: findings={finding_count}, observations={observation_count}"
    )


def test_missing_evidence_path_clean_error():
    """Non-existent ``--evidence`` path produces a clean ClickException.

    Gated upstream of the loader at line 307; surfaces as exit 1 with a
    helpful message rather than a Python traceback.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_evidence_oscal not present")
    fake = Path("/nonexistent/roam/w805_nnn_does_not_exist.json")
    result = _invoke_oscal_ar(fake, json_mode=True)
    # Click rejects nonexistent --evidence path with exit code 2 (Click
    # validation) before our handler runs.
    assert result.exit_code != 0
    # No Python traceback class names leak out.
    assert "Traceback" not in result.output


def test_missing_evidence_flag_clean_error():
    """``--kind assessment-results`` without ``--evidence`` errors cleanly."""
    if not _CMD_PATH.exists():
        pytest.skip("cmd_evidence_oscal not present")
    result = _invoke_oscal_ar(evidence_path=None, json_mode=True)
    assert result.exit_code != 0
    # Click renders this as "Error: ..." -- no Python traceback class names.
    assert "Traceback" not in result.output
    assert "--evidence" in result.output


# ---------------------------------------------------------------------------
# Bug pins (Pattern-2 + Pattern-1-V-B) -- xfail-strict
# ---------------------------------------------------------------------------


def test_empty_packet_no_crash(tmp_path):
    """Pattern-1-V-C analog: empty ``{}`` should not crash the loader.

    Today: ``ChangeEvidence(__init__)`` raises ``TypeError`` for the
    missing ``evidence_id`` arg, and the AR branch only catches
    ``ValueError`` at line 324. The exception bubbles up and Click prints
    a raw error string with exit 1 (not the structured FAIL envelope).
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_evidence_oscal not present")
    pkt = _write_packet(tmp_path, {})
    result = _invoke_oscal_ar(pkt, json_mode=True)
    # The process should not surface a raw Python error class name. This
    # asserts the CURRENT behavior pins the BUG: a raw error leaks, so the
    # output starts with the Click-rendered ``Error: ...`` string and is
    # NOT a JSON envelope.
    raw_error_leaked = "Error:" in result.output and "ChangeEvidence.__init__" in result.output
    # Today we expect the leak; this is the bug pin.
    assert raw_error_leaked, f"expected raw TypeError leak (W805-NNN bug #2), got: {result.output!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-NNN BUG #2 (Pattern-1-V-B): empty {} packet bypasses the "
        "click.ClickException handler at cmd_evidence_oscal.py:324 because "
        "ChangeEvidence.__init__ raises TypeError for missing evidence_id, "
        "not ValueError. The raw error leaks; no JSON envelope is emitted "
        "even under --json mode. Fix template: expand the except clause to "
        "(ValueError, TypeError) so the structured error envelope reaches "
        "the consumer."
    ),
)
def test_empty_packet_envelope_has_verdict(tmp_path):
    """Empty packet SHOULD still emit a structured envelope with a verdict.

    xfail-strict pin: today the TypeError leak prevents any envelope from
    reaching stdout. When BUG #2 is fixed, this test passes naturally.
    """
    pkt = _write_packet(tmp_path, {})
    result = _invoke_oscal_ar(pkt, json_mode=True)
    env = _parse_envelope(result)  # raises today (no JSON in stdout)
    assert isinstance(env.get("summary", {}).get("verdict"), str)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-NNN BUG #2 follow-on: structured envelope on empty packet "
        "should name the failure state explicitly (e.g. state: "
        "'invalid_packet' or 'missing_evidence_id'). Today the envelope "
        "doesn't reach stdout at all (TypeError leak)."
    ),
)
def test_empty_packet_state_explicit(tmp_path):
    """Empty packet failure state SHOULD be explicit, not 'broken'."""
    pkt = _write_packet(tmp_path, {})
    result = _invoke_oscal_ar(pkt, json_mode=True)
    env = _parse_envelope(result)
    summary = env.get("summary") or {}
    # Pattern-2: name absent state explicitly.
    state = summary.get("state") or summary.get("status") or ""
    assert isinstance(state, str) and state, f"empty packet did not surface explicit state field: {summary!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-NNN BUG #2 follow-on: structured envelope on empty packet "
        "should set partial_success=True so consumers can branch on the "
        "load failure. Today: TypeError leak; no envelope at all."
    ),
)
def test_empty_packet_partial_success_set(tmp_path):
    """Empty packet SHOULD surface partial_success: True."""
    pkt = _write_packet(tmp_path, {})
    result = _invoke_oscal_ar(pkt, json_mode=True)
    env = _parse_envelope(result)
    assert env["summary"]["partial_success"] is True


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-NNN BUG #1 (Pattern-2 silent-success on vacuous projection): "
        "a minimal-but-valid packet produces partial_success: False + a "
        "success verdict despite the OSCAL AR document containing ZERO "
        "findings and ZERO observations. Per the Pattern-2 rule, a vacuous "
        "projection MUST surface partial_success: True so consumers can "
        "distinguish a real AR from one projected off an empty packet. "
        "Source: cmd_evidence_oscal.py:368-397, ar_counts['partial_success']"
        " is bound to bool(dropped_count) -- which is False when no enum "
        "rows dropped, regardless of finding/observation count."
    ),
)
def test_no_silent_vacuous_oscal_on_empty(tmp_path):
    """Vacuous AR (0 findings + 0 observations) MUST surface partial_success.

    Projection-family analog of W805-LLL's silent-success bug. The
    projection succeeded structurally but produced no signal -- the
    envelope must say so.
    """
    minimal = {"evidence_id": "vacuous-id", "schema_version": "1.0.0", "producer": {}}
    pkt = _write_packet(tmp_path, minimal)
    result = _invoke_oscal_ar(pkt, json_mode=True)
    assert result.exit_code == 0, result.output
    env = _parse_envelope(result)
    summary = env["summary"]
    finding_count = int(summary.get("finding_count") or 0)
    observation_count = int(summary.get("observation_count") or 0)
    # Sanity: probe really did produce a vacuous AR.
    assert finding_count == 0 and observation_count == 0, (
        f"probe expected vacuous AR but got findings={finding_count}, observations={observation_count}"
    )
    # The Pattern-2 invariant: vacuous projection must NOT report
    # partial_success: False.
    assert summary["partial_success"] is True, "vacuous AR projection emitted partial_success: False (Pattern-2)"
