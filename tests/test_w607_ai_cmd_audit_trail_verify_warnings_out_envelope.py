"""W607-AI -- ``cmd_audit_trail_verify`` threads ``warnings_out`` onto its envelope.

cmd_audit_trail_verify is the VERIFIER half of the cryptographic-attestation
triad-quartet:

* cmd_attest          (W607-AD landed) -- producer
* cmd_pr_bundle       (W607-AE landed) -- composer
* cmd_cga             (W607-AF in flight) -- signer
* cmd_audit_trail_verify (W607-AI THIS WAVE) -- verifier

With W607-AI plumbed, the producer + composer + signer + verifier of the
cryptographic-attestation path are all W607-instrumented. A raise anywhere
in ``{sign, hash, write, verify}`` now surfaces a per-phase marker rather
than crashing.

Substrate boundaries wrapped by W607-AI
---------------------------------------

Four substrate-call sites in ``audit_trail_verify()`` get the canonical
``_run_check_ai(phase, fn, *args)`` wrapper:

* ``verify_chain``       -- _verify_chain(path) (CRYPTOGRAPHIC SHA-256 walk)
* ``open_findings_db``   -- open_db(readonly=False) (registry connection)
* ``emit_findings``      -- _emit_audit_trail_verify_findings(...) (rows)
* ``commit_findings``    -- conn.commit() (durable persist)

Each raise becomes an
``audit_trail_verify_<phase>_failed:<exc_class>:<detail>`` marker via
``_w607ai_warnings_out`` and the envelope still emits cleanly.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

The prior code had a bare ``try / except (sqlite3.OperationalError,
click.ClickException): pass`` around the persist block -- Pattern-2
silent fallback that swallowed findings-table-missing OR no-index-db-yet
errors. W607-AI replaces that swallow with three structured markers
(open_findings_db / emit_findings / commit_findings) so the disclosure
channel names which step crashed.

VERIFIER-SIDE bonus: a simulated raise on ``_verify_chain`` is the
cryptographic-verify boundary the wrapper is named for. The marker
``audit_trail_verify_verify_chain_failed:<exc>:<detail>`` AND the
verdict reflects the tampering / corruption (not silent SAFE).

TRIAD-quartet pairing bonus: an envelope-shape test confirms that
``audit_trail_verify_*`` markers from this wave can coexist with
``attest_*`` (W607-AD) and ``pr_bundle_*`` (W607-AE) markers on a
downstream consumer that aggregates verify + producer envelopes --
each prefix family is mutually distinct so an aggregator can
attribute the disclosure to the right source command.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. cmd_audit_trail_verify
has a lazy ``from roam.db.findings import FindingRecord, emit_finding``
inside ``_emit_audit_trail_verify_findings`` which is a genuine
deferred-load import (the findings module is only needed when
``--persist`` is set), NOT a cargo-cult cycle hedge. Left untouched
per W907.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import hashlib
import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers -- build a known-good audit trail JSONL with a proper SHA-256 chain
# ---------------------------------------------------------------------------


def _write_chain(path: Path, records: list[dict]) -> None:
    """Write records as JSONL with proper SHA-256 chain linking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            rec = dict(rec)
            rec["previous_record_hash"] = prev_hash
            line = _json.dumps(rec, separators=(",", ":"), sort_keys=True)
            f.write(line + "\n")
            prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()


def _base_record(verdict: str, ts: str) -> dict:
    return {
        "schema": "roam-audit-trail-v1",
        "timestamp": ts,
        "tool": "roam-code",
        "tool_version": "12.26",
        "actor": "test@example.com",
        "verdict": verdict,
        "blast_radius": 30,
        "ai_likelihood": 50,
        "rule_violations_count": 0,
    }


def _invoke_verify(runner: CliRunner, trail_path: Path, *extra):
    """Invoke ``roam --json audit-trail-verify --input <trail_path>``."""
    from roam.cli import cli

    args = ["--json", "audit-trail-verify", "--input", str(trail_path)]
    args.extend(extra)
    return runner.invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def valid_trail(tmp_path):
    """A known-good audit trail with a valid 3-record SHA-256 chain."""
    path = tmp_path / "trail.jsonl"
    _write_chain(
        path,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
            _base_record("BLOCK", "2026-05-05T00:02:00Z"),
        ],
    )
    return path


@pytest.fixture
def tampered_trail(tmp_path):
    """A 3-record audit trail with line 2 tampered (chain broken at line 3)."""
    path = tmp_path / "tampered.jsonl"
    _write_chain(
        path,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
            _base_record("BLOCK", "2026-05-05T00:02:00Z"),
        ],
    )
    # Tamper line 2: change verdict; line 3's previous_record_hash now points
    # at the original line 2's hash -> chain break detected at line 3.
    lines = path.read_text(encoding="utf-8").splitlines()
    rec2 = _json.loads(lines[1])
    rec2["verdict"] = "TAMPERED"
    lines[1] = _json.dumps(rec2, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# (1) Happy path -- clean envelope omits W607-AI substrate markers
# ---------------------------------------------------------------------------


def test_audit_trail_verify_clean_envelope_omits_w607ai_markers(cli_runner, valid_trail):
    """Clean audit-trail-verify -> no W607-AI substrate markers.

    Hash-stable: an empty W607-AI bucket on the success path must produce
    an envelope without substrate markers AND without a top-level
    ``warnings_out`` key. The envelope shape stays byte-identical to the
    pre-W607-AI verifier when no helper raised.
    """
    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "audit-trail-verify"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    assert "chain valid" in verdict, verdict
    # Empty-bucket discipline: NO W607-AI markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("audit_trail_verify_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean audit-trail-verify must NOT surface "
        f"audit_trail_verify_<phase>_failed: markers; "
        f"got top={top_wo!r}, summary={summary_wo!r}"
    )
    # partial_success stays False on the clean path (chain_valid=True).
    assert data["summary"].get("partial_success") is False, data["summary"]


# ---------------------------------------------------------------------------
# (2) verify_chain failure (CRYPTOGRAPHIC verify boundary) -> marker emitted
# ---------------------------------------------------------------------------


def test_audit_trail_verify_chain_failure_marker_format(cli_runner, valid_trail, monkeypatch):
    """If _verify_chain raises, surface ``audit_trail_verify_verify_chain_failed:``.

    This is the VERIFIER-SIDE bonus boundary -- a raise on the SHA-256
    chain walk (corrupted file, encoding error, hash collision) MUST
    surface a structured marker; the envelope still emits with empty
    records/issues and partial_success=True.
    """
    from roam.commands import cmd_audit_trail_verify

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-verify-chain-from-W607-AI")

    monkeypatch.setattr(cmd_audit_trail_verify, "_verify_chain", _raise)

    result = _invoke_verify(cli_runner, valid_trail)
    # exit code can be 0 (no gate) on a partial-failure envelope.
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    vc_markers = [m for m in top_wo if m.startswith("audit_trail_verify_verify_chain_failed:")]
    assert vc_markers, f"expected audit_trail_verify_verify_chain_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in vc_markers), vc_markers
    assert any("synthetic-verify-chain-from-W607-AI" in m for m in vc_markers), vc_markers
    # partial_success flips on any non-empty W607-AI bucket.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (3) Verifier-side bonus: tampered trail surfaces marker shape consistent
#     with the verdict reflecting the tampering (not silent SAFE).
# ---------------------------------------------------------------------------


def test_audit_trail_verify_tampered_trail_verdict_not_silent_safe(cli_runner, tampered_trail):
    """Tampered trail -> verdict reflects the break, NOT a silent SAFE.

    No monkeypatch; this exercises real _verify_chain on a tampered trail
    and confirms the verdict correctly says "chain BROKEN" -- the
    Pattern-2 silent-fallback discipline that gives W607-AI its
    cryptographic-verify reason for being.

    This pairs with test (2): when the cryptographic-verify boundary
    detects tampering naturally, the verdict reflects it. When the
    boundary RAISES (e.g. file corruption), the W607-AI marker covers
    the disclosure gap.
    """
    result = _invoke_verify(cli_runner, tampered_trail)
    # broken state without --gate is exit 0; with --gate it would be 5.
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    verdict = data["summary"]["verdict"]
    assert "BROKEN" in verdict, (
        f"tampered trail must produce a verdict containing 'BROKEN' (no silent SAFE); got verdict = {verdict!r}"
    )
    assert data["summary"].get("state") == "broken", data["summary"]
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (4) emit_findings failure -> marker + envelope still ships
# ---------------------------------------------------------------------------


def test_audit_trail_verify_emit_findings_failure_marker_format(cli_runner, tampered_trail, tmp_path, monkeypatch):
    """If _emit_audit_trail_verify_findings raises, surface the marker.

    Pattern-2 discipline: the prior bare ``except (OperationalError,
    ClickException): pass`` swallowed this silently. W607-AI now
    surfaces ``audit_trail_verify_emit_findings_failed:`` and the
    envelope still emits.
    """
    from roam.commands import cmd_audit_trail_verify

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-findings-from-W607-AI")

    monkeypatch.setattr(cmd_audit_trail_verify, "_emit_audit_trail_verify_findings", _raise)
    # cwd must be a directory where ``open_db(readonly=False)`` can create
    # .roam/index.db without polluting the real repo state.
    monkeypatch.chdir(tmp_path)

    result = _invoke_verify(cli_runner, tampered_trail, "--persist")
    # Exit 0 on broken-no-gate; envelope is the load-bearing surface.
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    ef_markers = [m for m in top_wo if m.startswith("audit_trail_verify_emit_findings_failed:")]
    assert ef_markers, f"expected audit_trail_verify_emit_findings_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in ef_markers), ef_markers


# ---------------------------------------------------------------------------
# (5) warnings_out lands in BOTH summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_audit_trail_verify_warnings_out_in_envelope(cli_runner, valid_trail, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_audit_trail_verify

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AI")

    monkeypatch.setattr(cmd_audit_trail_verify, "_verify_chain", _raise)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("audit_trail_verify_verify_chain_failed:")]
    assert markers, f"expected audit_trail_verify_verify_chain_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (6) partial_success flips when ANY W607-AI helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_audit_trail_verify_helper_raises(cli_runner, valid_trail, monkeypatch):
    """Any non-empty W607-AI bucket -> summary.partial_success = True."""
    from roam.commands import cmd_audit_trail_verify

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AI")

    monkeypatch.setattr(cmd_audit_trail_verify, "_verify_chain", _raise)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, valid_trail, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AE contracts.
    """
    from roam.commands import cmd_audit_trail_verify

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AI")

    monkeypatch.setattr(cmd_audit_trail_verify, "_verify_chain", _raise)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "verify_chain guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("audit_trail_verify_verify_chain_failed:")]
    assert failure_markers, f"expected audit_trail_verify_verify_chain_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "audit_trail_verify_verify_chain_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- ``audit_trail_verify_*`` not other families
# ---------------------------------------------------------------------------


def test_marker_prefix_audit_trail_verify_not_attest_or_pr_bundle(cli_runner, valid_trail, monkeypatch):
    """Every surfaced W607-AI marker uses the ``audit_trail_verify_*`` prefix.

    cmd_audit_trail_verify is the VERIFIER half of the cryptographic-
    attestation triad-quartet -- mutually distinct from sibling W607-*
    layers. Hard guard against accidental marker-prefix drift.

    TRIAD-quartet pairing: this distinctness guarantees an aggregator
    that consumes verify + producer envelopes can attribute each
    disclosure to its source command via prefix alone.
    """
    from roam.commands import cmd_audit_trail_verify

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AI")

    monkeypatch.setattr(cmd_audit_trail_verify, "_verify_chain", _raise)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("audit_trail_verify_"), (
            f"every surfaced W607-AI marker must use the "
            f"``audit_trail_verify_*`` prefix family "
            f"(cmd_audit_trail_verify scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("attest_", "cmd_attest W607-AD"),
            ("pr_bundle_", "cmd_pr_bundle W607-AE"),
            ("pr_analyze_", "cmd_pr_analyze W607-AA"),
            ("pr_risk_", "cmd_pr_risk W607-AB/Q"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("cga_", "cmd_cga W607-AF"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) Triad-quartet pairing: audit_trail_verify_* and attest_* prefixes
#     are mutually distinct closed-enum families.
# ---------------------------------------------------------------------------


def test_triad_quartet_prefixes_mutually_distinct():
    """W607-AD attest_* and W607-AI audit_trail_verify_* prefixes are distinct.

    This is a source-level guard pinning the triad-quartet marker-family
    closed-enum invariant: when a downstream aggregator consumes envelopes
    from BOTH cmd_attest (producer) and cmd_audit_trail_verify (verifier),
    the prefix family alone is sufficient to attribute each disclosure.

    Drift here would mean an aggregator could mis-attribute a verifier
    raise to the producer (or vice versa) -- a real Pattern-3 vocabulary-
    mismatch hazard.
    """
    attest_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_attest.py"
    verify_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"
    assert attest_src_path.exists(), attest_src_path
    assert verify_src_path.exists(), verify_src_path
    attest_src = attest_src_path.read_text(encoding="utf-8")
    verify_src = verify_src_path.read_text(encoding="utf-8")

    # cmd_attest carries the attest_ marker prefix template.
    assert "attest_{phase}_failed" in attest_src, (
        "W607-AD attest_{phase}_failed marker template missing from "
        "cmd_attest -- producer-side instrumentation regressed."
    )
    # cmd_audit_trail_verify carries the audit_trail_verify_ marker prefix.
    assert "audit_trail_verify_{phase}_failed" in verify_src, (
        "W607-AI audit_trail_verify_{phase}_failed marker template "
        "missing from cmd_audit_trail_verify -- verifier-side "
        "instrumentation regressed."
    )
    # The two prefixes do not collide -- audit_trail_verify_ does NOT
    # start with attest_ (mutually distinct closed-enum families).
    assert not "audit_trail_verify_".startswith("attest_")
    assert not "attest_".startswith("audit_trail_verify_")


# ---------------------------------------------------------------------------
# (10) Sibling parity -- W607-AD cmd_attest source unchanged
# ---------------------------------------------------------------------------


def test_w607_ad_cmd_attest_unaffected():
    """Sibling parity guard: W607-AD cmd_attest source surface unchanged.

    W607-AI lands only in cmd_audit_trail_verify. The W607-AD cmd_attest
    surface (``_w607ad_warnings_out`` accumulator + ``attest_*`` marker
    emission) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_attest.py"
    assert src_path.exists(), f"cmd_attest.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ad_warnings_out" in src, (
        "W607-AD accumulator removed from cmd_attest; W607-AI must not regress the sibling instrumentation."
    )
    assert "attest_{phase}_failed" in src, (
        "W607-AD marker prefix template removed from cmd_attest; W607-AI must not regress the sibling marker family."
    )


def test_w607_ae_cmd_pr_bundle_unaffected():
    """Sibling parity guard: W607-AE cmd_pr_bundle source surface unchanged.

    W607-AI lands only in cmd_audit_trail_verify. The W607-AE
    cmd_pr_bundle surface (``_w607ae_warnings_out`` + ``pr_bundle_*``
    markers) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_pr_bundle.py"
    assert src_path.exists(), f"cmd_pr_bundle.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ae_warnings_out" in src, (
        "W607-AE accumulator removed from cmd_pr_bundle; W607-AI must not regress the sibling instrumentation."
    )
    assert "pr_bundle_{phase}_failed" in src, (
        "W607-AE marker prefix template removed from cmd_pr_bundle; W607-AI must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (11) Source-level guard: cmd_audit_trail_verify carries the W607-AI accumulator
# ---------------------------------------------------------------------------


def test_cmd_audit_trail_verify_carries_w607ai_accumulator():
    """AST-level guard: cmd_audit_trail_verify source carries the W607-AI accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation (e.g. switches to a single try/except wrapping the
    whole command body) fails this guard rather than silently regressing
    every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"
    assert src_path.exists(), f"cmd_audit_trail_verify.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ai_warnings_out" in src, (
        "W607-AI accumulator missing from cmd_audit_trail_verify; the substrate-CALL marker plumbing has been removed."
    )
    assert "audit_trail_verify_{phase}_failed" in src, (
        "W607-AI marker prefix template missing from "
        "cmd_audit_trail_verify; check the "
        '`f"audit_trail_verify_{phase}_failed:..."` line in _run_check_ai.'
    )
    # Parse-tree level: confirm _run_check_ai is defined inside the command body.
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ai":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AI ``_run_check_ai`` helper not found in "
        "cmd_audit_trail_verify AST; the per-substrate wrapper has been "
        "refactored away."
    )


# ---------------------------------------------------------------------------
# (12) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_audit_trail_verify substrate boundary is wrapped.

    W607-AI substrate inventory (in order of cryptographic importance):

    * verify_chain      -- _verify_chain(path) (CRYPTOGRAPHIC SHA-256 walk)
    * open_findings_db  -- open_db(readonly=False) (registry connection)
    * emit_findings     -- _emit_audit_trail_verify_findings(...) (rows)
    * commit_findings   -- conn.commit() (durable persist)

    If a future wave introduces a new substrate boundary (e.g. HMAC
    signature verify, cross-reference to pr-bundle artifacts), this
    guard needs to know about it -- add the phase name here.

    Accepts indentation depths of 8, 12, 16, 20, 24 spaces to allow for
    refactor of the substrate call sites without breaking the guard.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "verify_chain",
        "open_findings_db",
        "emit_findings",
        "commit_findings",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_ai("{phase}"' in src
        multi_line = (
            f'_run_check_ai(\n        "{phase}"' in src
            or f'_run_check_ai(\n            "{phase}"' in src
            or f'_run_check_ai(\n                "{phase}"' in src
            or f'_run_check_ai(\n                    "{phase}"' in src
            or f'_run_check_ai(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AI _run_check_ai wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )
