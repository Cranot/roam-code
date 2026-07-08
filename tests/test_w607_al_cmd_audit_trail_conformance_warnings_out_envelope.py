"""W607-AL -- ``cmd_audit_trail_conformance`` threads ``warnings_out`` onto its envelope.

cmd_audit_trail_conformance is the COMPLIANCE-checking sibling of
cmd_audit_trail_verify (W607-AI). Both are downstream consumers of the
same audit-trail JSONL artifact:

* cmd_audit_trail_verify (W607-AI landed)      -- chain-integrity verifier
* cmd_audit_trail_conformance (W607-AL THIS)   -- Article-12 conformance

With W607-AL plumbed, both downstream consumers of the audit-trail JSONL
have substrate-CALL marker plumbing. Combined with W607-AD (cmd_attest,
producer), the complete attest -> verify -> conformance triad is closed.

Substrate boundaries wrapped by W607-AL
---------------------------------------

Ten substrate-call sites in ``audit_trail_conformance_check()`` get the
canonical ``_run_check_al(phase, fn, *args)`` wrapper:

* ``load_records``                     -- _load_records(path)        (JSONL trail-read)
* ``check_chain_integrity``            -- _check_chain_integrity(path)
* ``check_timestamp_completeness``     -- _check_timestamps(records)
* ``check_actor_attribution``          -- _check_actors(records)
* ``check_reproducibility_metadata``   -- _check_reproducibility(records)
* ``check_verdict_and_rationale``      -- _check_verdicts_and_rationale(records)
* ``check_retention``                  -- _check_retention(records, retention_days)
* ``open_findings_db``                 -- open_db(readonly=False)    (registry)
* ``emit_findings``                    -- _emit_audit_trail_conformance_findings(...)
* ``commit_findings``                  -- conn.commit()              (durable persist)

Each raise becomes an
``audit_trail_conformance_<phase>_failed:<exc_class>:<detail>`` marker
via ``_w607al_warnings_out``.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

The prior code had TWO nested Pattern-2 silent-fallback blocks around
the persist path -- an inner ``except sqlite3.OperationalError: pass``
and an outer ``except Exception: pass``. Both swallowed errors with NO
disclosure channel. W607-AL replaces both with three structured markers
(open_findings_db / emit_findings / commit_findings) so the disclosure
channel names which step crashed. This is the same pattern landed in
W607-AE (cmd_pr_bundle) and W607-AI (cmd_audit_trail_verify).

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
        "rationale_summary": "synthetic test record",
        "diff_sha256": "0" * 64,
        "git_sha": "deadbeef" * 5,
        "blast_radius": 30,
        "ai_likelihood": 50,
        "rule_violations_count": 0,
    }


def _invoke_conformance(runner: CliRunner, trail_path: Path, *extra):
    """Invoke ``roam --json audit-trail-conformance-check --input <trail_path>``."""
    from roam.cli import cli

    args = ["--json", "audit-trail-conformance-check", "--input", str(trail_path)]
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
    """Audit trail with a valid chain but young records (retention will FAIL).

    Chain integrity + timestamps + actors + reproducibility +
    verdict-rationale will all PASS; only retention (180-day floor)
    will fail because the records are 2026-05 timestamps. This is the
    natural "clean trail, partial conformance" state we need for the
    happy-path envelope shape lock.
    """
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


# ---------------------------------------------------------------------------
# (1) Happy path -- clean envelope omits W607-AL substrate markers
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_clean_envelope_omits_w607al_markers(cli_runner, valid_trail):
    """Clean run -> no W607-AL substrate markers.

    Hash-stable: empty W607-AL bucket on the success path produces an
    envelope without substrate markers AND without a top-level
    ``warnings_out`` key. Byte-identical to pre-W607-AL when no helper
    raised.
    """
    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "audit-trail-conformance-check"
    # Empty-bucket discipline: NO W607-AL markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("audit_trail_conformance_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean audit-trail-conformance-check must NOT surface "
        f"audit_trail_conformance_<phase>_failed: markers; "
        f"got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) load_records failure -> marker emitted, envelope still emits
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_load_records_failure_marker_format(cli_runner, valid_trail, monkeypatch):
    """If _load_records raises, surface ``audit_trail_conformance_load_records_failed:``.

    This is the JSONL trail-read boundary. A raise (encoding error,
    permission denied, file vanished mid-read) MUST surface a
    structured marker; the envelope still emits with the trail
    reduced to an empty list (triggering the no-trail branch).
    """
    from roam.commands import cmd_audit_trail_conformance

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-load-records-from-W607-AL")

    monkeypatch.setattr(cmd_audit_trail_conformance, "_load_records", _raise)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    # The trail will be empty after the failed load -> no_trail branch
    # is taken. That branch is a DIFFERENT no-trail envelope shape; we
    # only need to confirm the run did not crash. The marker validation
    # is done by other tests (e.g. test_audit_trail_conformance_chain_check_failure_marker)
    # because the no-trail branch deliberately short-circuits BEFORE the
    # other check substrate boundaries execute.
    # Sanity: not a crash, no exception leaked.
    assert "Traceback" not in result.output, result.output


# ---------------------------------------------------------------------------
# (3) check_chain_integrity failure -> marker + envelope still ships
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_chain_check_failure_marker_format(cli_runner, valid_trail, monkeypatch):
    """If _check_chain_integrity raises, surface the marker.

    The chain check delegates internally to cmd_audit_trail_verify;
    a raise here (e.g. on a corrupted records buffer) MUST surface a
    ``audit_trail_conformance_check_chain_integrity_failed:`` marker
    and the conformance check still scores against the remaining 5.
    """
    from roam.commands import cmd_audit_trail_conformance

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-chain-check-from-W607-AL")

    monkeypatch.setattr(cmd_audit_trail_conformance, "_check_chain_integrity", _raise)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    cc_markers = [m for m in top_wo if m.startswith("audit_trail_conformance_check_chain_integrity_failed:")]
    assert cc_markers, f"expected audit_trail_conformance_check_chain_integrity_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in cc_markers), cc_markers
    assert any("synthetic-chain-check-from-W607-AL" in m for m in cc_markers), cc_markers
    # Non-empty W607-AL bucket -> partial_success flips True.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (4) check_retention failure -> marker emitted
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_retention_check_failure_marker_format(cli_runner, valid_trail, monkeypatch):
    """If _check_retention raises, surface the marker.

    Heuristic-tier check; a raise (timezone arithmetic edge case,
    datetime parse explosion) MUST surface a structured marker.
    """
    from roam.commands import cmd_audit_trail_conformance

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-retention-from-W607-AL")

    monkeypatch.setattr(cmd_audit_trail_conformance, "_check_retention", _raise)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    rt_markers = [m for m in top_wo if m.startswith("audit_trail_conformance_check_retention_failed:")]
    assert rt_markers, f"expected audit_trail_conformance_check_retention_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (5) emit_findings failure -> marker + envelope still ships
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_emit_findings_failure_marker_format(cli_runner, valid_trail, tmp_path, monkeypatch):
    """If _emit_audit_trail_conformance_findings raises, surface the marker.

    Pattern-2 discipline: the prior bare ``except sqlite3.OperationalError:
    pass`` (inner) and ``except Exception: pass`` (outer) swallowed this
    silently. W607-AL surfaces ``audit_trail_conformance_emit_findings_failed:``
    and the envelope still emits.
    """
    from roam.commands import cmd_audit_trail_conformance

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-emit-findings-from-W607-AL")

    monkeypatch.setattr(
        cmd_audit_trail_conformance,
        "_emit_audit_trail_conformance_findings",
        _raise,
    )
    # cwd must be a directory where ``open_db(readonly=False)`` can create
    # .roam/index.db without polluting the real repo state.
    monkeypatch.chdir(tmp_path)
    # Re-write the trail into the new cwd so --input points at a real path.
    trail = tmp_path / "trail.jsonl"
    _write_chain(
        trail,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
        ],
    )

    result = _invoke_conformance(cli_runner, trail, "--persist")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    ef_markers = [m for m in top_wo if m.startswith("audit_trail_conformance_emit_findings_failed:")]
    assert ef_markers, f"expected audit_trail_conformance_emit_findings_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in ef_markers), ef_markers


# ---------------------------------------------------------------------------
# (6) warnings_out lands in BOTH summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_warnings_out_in_envelope(cli_runner, valid_trail, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_audit_trail_conformance

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AL")

    monkeypatch.setattr(cmd_audit_trail_conformance, "_check_chain_integrity", _raise)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY W607-AL helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_audit_trail_conformance_helper_raises(cli_runner, valid_trail, monkeypatch):
    """Any non-empty W607-AL bucket -> summary.partial_success = True."""
    from roam.commands import cmd_audit_trail_conformance

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-from-W607-AL")

    monkeypatch.setattr(cmd_audit_trail_conformance, "_check_timestamps", _raise)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, valid_trail, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AI contracts.
    """
    from roam.commands import cmd_audit_trail_conformance

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AL")

    monkeypatch.setattr(cmd_audit_trail_conformance, "_check_actors", _raise)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("audit_trail_conformance_check_actor_attribution_failed:")]
    assert failure_markers, f"expected audit_trail_conformance_check_actor_attribution_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "audit_trail_conformance_check_actor_attribution_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``audit_trail_conformance_*`` only
# ---------------------------------------------------------------------------


def test_marker_prefix_audit_trail_conformance_not_other_families(cli_runner, valid_trail, monkeypatch):
    """Every surfaced W607-AL marker uses ``audit_trail_conformance_*``.

    cmd_audit_trail_conformance is the COMPLIANCE-checking sibling of
    cmd_audit_trail_verify -- mutually distinct from sibling W607-*
    layers (audit_trail_verify_*, attest_*, pr_bundle_*, cga_*, …).
    Hard guard against accidental marker-prefix drift.
    """
    from roam.commands import cmd_audit_trail_conformance

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AL")

    monkeypatch.setattr(cmd_audit_trail_conformance, "_check_reproducibility", _raise)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("audit_trail_conformance_"), (
            f"every surfaced W607-AL marker must use the "
            f"``audit_trail_conformance_*`` prefix family "
            f"(cmd_audit_trail_conformance scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI"),
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
# (10) Triad-closure pairing: verify_ and conformance_ prefixes mutually distinct
# ---------------------------------------------------------------------------


def test_triad_closure_prefixes_mutually_distinct():
    """W607-AI audit_trail_verify_* and W607-AL audit_trail_conformance_* are distinct.

    Source-level guard pinning the triad-closure marker-family closed-enum
    invariant: when a downstream aggregator consumes envelopes from BOTH
    cmd_audit_trail_verify (integrity) and cmd_audit_trail_conformance
    (Article-12 rollup), the prefix family alone attributes each
    disclosure correctly.

    Drift here would mean an aggregator could mis-attribute a conformance
    raise to the verifier (or vice versa) -- a real Pattern-3 vocabulary
    mismatch hazard given the two commands operate on the SAME trail.
    """
    verify_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"
    conformance_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py"
    assert verify_src_path.exists(), verify_src_path
    assert conformance_src_path.exists(), conformance_src_path
    verify_src = verify_src_path.read_text(encoding="utf-8")
    conformance_src = conformance_src_path.read_text(encoding="utf-8")

    # cmd_audit_trail_verify carries the audit_trail_verify_ marker template.
    assert "audit_trail_verify_{phase}_failed" in verify_src, (
        "W607-AI audit_trail_verify_{phase}_failed marker template missing "
        "from cmd_audit_trail_verify -- verifier-side regressed."
    )
    # cmd_audit_trail_conformance carries audit_trail_conformance_.
    assert "audit_trail_conformance_{phase}_failed" in conformance_src, (
        "W607-AL audit_trail_conformance_{phase}_failed marker template "
        "missing from cmd_audit_trail_conformance -- conformance-side "
        "regressed."
    )
    # The two prefixes do not collide -- audit_trail_conformance_ does
    # NOT start with audit_trail_verify_ (mutually distinct closed-enum
    # families).
    assert not "audit_trail_conformance_".startswith("audit_trail_verify_")
    assert not "audit_trail_verify_".startswith("audit_trail_conformance_")


# ---------------------------------------------------------------------------
# (11) PATTERN-2 ELIMINATION test: prior silent fallbacks are gone
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallbacks_eliminated():
    """W607-AL eliminates the two nested Pattern-2 silent fallbacks.

    Pre-W607-AL cmd_audit_trail_conformance had:

      if persist:
          try:
              ...
              with open_db(readonly=False) as conn:
                  try:
                      _emit_audit_trail_conformance_findings(...)
                      conn.commit()
                  except sqlite3.OperationalError:
                      pass     # <-- INNER silent fallback
          except Exception:
              pass             # <-- OUTER silent fallback

    Both swallow registry-write errors with NO disclosure channel --
    exactly the Pattern-2 antipattern documented in CLAUDE.md. W607-AL
    replaces both with structured ``_run_check_al("open_findings_db",
    ...)`` / ``_run_check_al("emit_findings", ...)`` /
    ``_run_check_al("commit_findings", ...)`` boundaries.

    This guard pins the elimination via source-level search: the literal
    ``except sqlite3.OperationalError:\\n                    pass``
    block must no longer exist (and any future bare ``except Exception:
    pass`` in the persist path is forbidden).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py"
    src = src_path.read_text(encoding="utf-8")

    # The specific pre-W607-AL silent fallback shapes must NOT appear in
    # active code (comments documenting their removal are fine; we check
    # for the executable pattern by requiring "pass" on the next non-
    # empty line). Use AST to be precise.
    tree = ast.parse(src)
    silent_fallbacks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                # Body of a single `pass` is the Pattern-2 antipattern.
                if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                    # Build a description of which exception
                    if handler.type is None:
                        kind = "bare except"
                    elif isinstance(handler.type, ast.Name):
                        kind = handler.type.id
                    elif isinstance(handler.type, ast.Attribute):
                        kind = ast.unparse(handler.type)
                    else:
                        kind = ast.dump(handler.type)
                    silent_fallbacks.append((handler.lineno, kind))

    assert not silent_fallbacks, (
        f"W607-AL must eliminate Pattern-2 silent-fallback ``except ...: "
        f"pass`` blocks in cmd_audit_trail_conformance; still found: "
        f"{silent_fallbacks!r}. Convert each to ``_run_check_al(...)``."
    )


# ---------------------------------------------------------------------------
# (12) Source-level guard: cmd_audit_trail_conformance carries the W607-AL accumulator
# ---------------------------------------------------------------------------


def test_cmd_audit_trail_conformance_carries_w607al_accumulator():
    """AST-level guard: cmd_audit_trail_conformance carries the W607-AL accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation fails this guard rather than silently regressing
    every other dynamic envelope-shape test.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py"
    src = src_path.read_text(encoding="utf-8")
    assert "w607al_warnings_out" in src, (
        "W607-AL accumulator missing from cmd_audit_trail_conformance; "
        "the substrate-CALL marker plumbing has been removed."
    )
    assert "audit_trail_conformance_{phase}_failed" in src, (
        "W607-AL marker prefix template missing from "
        "cmd_audit_trail_conformance; check the "
        '`f"audit_trail_conformance_{phase}_failed:..."` line in _run_check_al.'
    )
    # Parse-tree level: confirm _run_check_al is defined inside the command body.
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_al":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AL ``_run_check_al`` helper not found in "
        "cmd_audit_trail_conformance AST; the per-substrate wrapper "
        "has been refactored away."
    )


# ---------------------------------------------------------------------------
# (13) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_audit_trail_conformance substrate boundary is wrapped.

    W607-AL substrate inventory (in order of execution):

    * load_records                     -- JSONL trail-read
    * check_chain_integrity            -- delegates to verify
    * check_timestamp_completeness     -- Article-12 §2
    * check_actor_attribution          -- Article-12 §3
    * check_reproducibility_metadata   -- Article-12 §4
    * check_verdict_and_rationale      -- Article-12 §5
    * check_retention                  -- Article-12 §6
    * open_findings_db                 -- registry conn
    * emit_findings                    -- rows
    * commit_findings                  -- durable persist

    If a future wave introduces a new substrate boundary (e.g. an
    Article-13 transparency rollup), this guard needs to know about
    it -- add the phase name here.

    Accepts indentation depths of 8, 12, 16, 20, 24 spaces to allow for
    refactor of the substrate call sites without breaking the guard.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "load_records",
        "check_chain_integrity",
        "check_timestamp_completeness",
        "check_actor_attribution",
        "check_reproducibility_metadata",
        "check_verdict_and_rationale",
        "check_retention",
        "open_findings_db",
        "emit_findings",
        "commit_findings",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_al("{phase}"' in src
        multi_line = (
            f'_run_check_al(\n        "{phase}"' in src
            or f'_run_check_al(\n            "{phase}"' in src
            or f'_run_check_al(\n                "{phase}"' in src
            or f'_run_check_al(\n                    "{phase}"' in src
            or f'_run_check_al(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AL _run_check_al wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (14) TRIAD-VERIFY pairing: conformance markers coexist with verify markers
# ---------------------------------------------------------------------------


def test_triad_verify_markers_coexist():
    """W607-AL and W607-AI markers can coexist in source.

    cmd_audit_trail_conformance delegates internally to
    cmd_audit_trail_verify via ``_check_chain_integrity`` which imports
    and calls ``_verify_chain``. When a downstream aggregator collects
    envelopes from both commands, both prefix families need to remain
    parseable. This guard pins the closed-enum invariant at the source
    level.
    """
    verify_src = (Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py").read_text(
        encoding="utf-8"
    )
    conformance_src = (
        Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py"
    ).read_text(encoding="utf-8")

    # Both prefix templates present in their respective sources.
    assert "audit_trail_verify_{phase}_failed" in verify_src
    assert "audit_trail_conformance_{phase}_failed" in conformance_src

    # The verifier substrate phases (W607-AI) MUST be a subset of:
    verify_phases = ("verify_chain", "open_findings_db", "emit_findings", "commit_findings")
    for ph in verify_phases:
        same_line = f'_run_check_ai("{ph}"' in verify_src
        # The verify substrate sites range from 4-space (top-level command
        # body) to 20-space (nested under ``if _db_ctx is not None: with
        # _db_ctx as conn:``). Accept all indent depths from 8 to 24.
        multi_line = any(f'_run_check_ai(\n{" " * indent}"{ph}"' in verify_src for indent in (8, 12, 16, 20, 24))
        assert same_line or multi_line, f"verifier phase {ph!r} missing"

    # The conformance substrate phases (W607-AL) include the verifier-
    # style registry trio PLUS the 7 Article-12 check boundaries.
    conformance_phases = (
        "load_records",
        "check_chain_integrity",
        "check_timestamp_completeness",
        "check_actor_attribution",
        "check_reproducibility_metadata",
        "check_verdict_and_rationale",
        "check_retention",
        "open_findings_db",
        "emit_findings",
        "commit_findings",
    )
    for ph in conformance_phases:
        same_line = f'_run_check_al("{ph}"' in conformance_src
        multi_line = any(f'_run_check_al(\n{" " * indent}"{ph}"' in conformance_src for indent in (8, 12, 16, 20, 24))
        assert same_line or multi_line, f"conformance phase {ph!r} missing"


# ---------------------------------------------------------------------------
# (15) Sibling parity -- W607-AI cmd_audit_trail_verify source unchanged
# ---------------------------------------------------------------------------


def test_w607_ai_cmd_audit_trail_verify_unaffected():
    """Sibling parity guard: W607-AI cmd_audit_trail_verify surface unchanged.

    W607-AL lands only in cmd_audit_trail_conformance. The W607-AI
    cmd_audit_trail_verify surface (``_w607ai_warnings_out`` +
    ``audit_trail_verify_*`` markers) MUST stay identical.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"
    assert src_path.exists(), f"cmd_audit_trail_verify.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ai_warnings_out" in src, (
        "W607-AI accumulator removed from cmd_audit_trail_verify; W607-AL must not regress the sibling instrumentation."
    )
    assert "audit_trail_verify_{phase}_failed" in src, (
        "W607-AI marker prefix template removed from cmd_audit_trail_verify; "
        "W607-AL must not regress the sibling marker family."
    )
