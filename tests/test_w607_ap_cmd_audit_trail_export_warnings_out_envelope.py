"""W607-AP -- ``cmd_audit_trail_export`` threads ``warnings_out`` onto its envelope.

cmd_audit_trail_export is the EXPORT leg of the audit-trail family quartet:

* cmd_attest (W607-AD landed)                  -- produces (writes to ledger)
* cmd_audit_trail_verify (W607-AI landed)      -- verifies chain integrity
* cmd_audit_trail_conformance (W607-AL landed) -- conforms (6-check Article-12)
* cmd_audit_trail_export (W607-AP THIS)        -- exports (projection + I/O)

With W607-AP plumbed, the complete attest -> verify -> conformance -> export
quartet is now W607-plumbed end-to-end. A raise anywhere surfaces a marker
rather than crashing.

Substrate boundaries wrapped by W607-AP
---------------------------------------

Ten substrate-call sites in ``audit_trail_export()`` get the canonical
``_run_check_ap(phase, fn, *args)`` wrapper:

* ``load_records_finalize``     -- _load_records(path)               (--finalize read)
* ``compute_chain_head``        -- _compute_chain_head(path)         (tail-read I/O)
* ``build_integrity_summary``   -- _build_integrity_summary(...)     (closing record)
* ``append_integrity_summary``  -- file.write(...)                   (--finalize append I/O)
* ``load_records``              -- _load_records(path)               (JSONL trail-read)
* ``filter_records``            -- _filter_records(...)              (since/until/verdict)
* ``aggregate_records``         -- _aggregate_records(...)           (bucketing)
* ``build_top_actors``          -- _build_top_actors(...)            (ranking)
* ``render_output``             -- _render_markdown/_csv/_json       (projection)
* ``atomic_write_text``         -- atomic_write_text(...)            (I/O boundary)

Each raise becomes an
``audit_trail_export_<phase>_failed:<exc_class>:<detail>`` marker via
``_w607ap_warnings_out``.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

The prior code had ONE Pattern-2 silent-fallback block (``except OSError:
pass`` in ``_build_integrity_summary``'s tail-read for chain_head). It is
replaced by extracting the tail-read into ``_compute_chain_head`` and
routing it through ``_run_check_ap("compute_chain_head", ...)`` so the
disclosure channel names the I/O failure instead of silently degrading to
an empty chain_head. This is the same Pattern-2 elimination shape landed
in W607-AE / W607-AI / W607-AL.

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


def _invoke_export(runner: CliRunner, trail_path: Path, *extra):
    """Invoke ``roam --json audit-trail-export --input <trail_path>``."""
    from roam.cli import cli

    args = ["--json", "audit-trail-export", "--input", str(trail_path)]
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
    """Audit trail with a valid chain for happy-path envelope shape tests."""
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
# (1) Happy path -- clean envelope omits W607-AP substrate markers
# ---------------------------------------------------------------------------


def test_audit_trail_export_clean_envelope_omits_w607ap_markers(cli_runner, valid_trail):
    """Clean run -> no W607-AP substrate markers.

    Hash-stable: empty W607-AP bucket on the success path produces an
    envelope without substrate markers AND without a top-level
    ``warnings_out`` key. Byte-identical to pre-W607-AP when no helper
    raised.
    """
    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "audit-trail-export"
    # Empty-bucket discipline: NO W607-AP markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("audit_trail_export_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean audit-trail-export must NOT surface "
        f"audit_trail_export_<phase>_failed: markers; "
        f"got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) load_records failure -> marker emitted, envelope still emits
# ---------------------------------------------------------------------------


def test_audit_trail_export_load_records_failure_marker_format(cli_runner, valid_trail, monkeypatch):
    """If _load_records raises, surface ``audit_trail_export_load_records_failed:``.

    This is the JSONL trail-read boundary. A raise (encoding error,
    permission denied, file vanished mid-read) MUST surface a structured
    marker; the envelope still emits with the trail reduced to an empty
    list and the renderer ships an empty-records markdown table.
    """
    from roam.commands import cmd_audit_trail_export

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-load-records-from-W607-AP")

    monkeypatch.setattr(cmd_audit_trail_export, "_load_records", _raise)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    lr_markers = [m for m in top_wo if m.startswith("audit_trail_export_load_records_failed:")]
    assert lr_markers, f"expected audit_trail_export_load_records_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in lr_markers), lr_markers
    assert any("synthetic-load-records-from-W607-AP" in m for m in lr_markers), lr_markers
    # Non-empty W607-AP bucket -> partial_success flips True.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (3) render_output failure -> marker + envelope still ships
# ---------------------------------------------------------------------------


def test_audit_trail_export_render_failure_marker_format(cli_runner, valid_trail, monkeypatch):
    """If the markdown renderer raises, surface ``audit_trail_export_render_output_failed:``.

    The projection boundary -- a raise here (memory pressure, locale
    surprise, downstream template explosion) MUST surface a structured
    marker. The envelope still emits with empty rendered content.
    """
    from roam.commands import cmd_audit_trail_export

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-render-from-W607-AP")

    monkeypatch.setattr(cmd_audit_trail_export, "_render_markdown", _raise)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    rd_markers = [m for m in top_wo if m.startswith("audit_trail_export_render_output_failed:")]
    assert rd_markers, f"expected audit_trail_export_render_output_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in rd_markers), rd_markers


# ---------------------------------------------------------------------------
# (4) atomic_write_text failure -> marker emitted
# ---------------------------------------------------------------------------


def test_audit_trail_export_atomic_write_text_failure_marker_format(cli_runner, valid_trail, tmp_path, monkeypatch):
    """If atomic_write_text raises, surface ``audit_trail_export_atomic_write_text_failed:``.

    I/O boundary -- a raise (disk full, permission denied, EBADF mid-flush)
    MUST surface a structured marker; the envelope still emits.
    """
    import roam.atomic_io as atomic_io_mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-atomic-write-from-W607-AP")

    monkeypatch.setattr(atomic_io_mod, "atomic_write_text", _raise)

    output_path = tmp_path / "out.md"
    result = _invoke_export(cli_runner, valid_trail, "--output", str(output_path))
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    aw_markers = [m for m in top_wo if m.startswith("audit_trail_export_atomic_write_text_failed:")]
    assert aw_markers, f"expected audit_trail_export_atomic_write_text_failed: marker; got {top_wo!r}"
    assert any("PermissionError" in m for m in aw_markers), aw_markers


# ---------------------------------------------------------------------------
# (5) compute_chain_head failure -> marker emitted (Pattern-2 elimination proof)
# ---------------------------------------------------------------------------


def test_audit_trail_export_compute_chain_head_failure_marker_format(cli_runner, valid_trail, monkeypatch):
    """If _compute_chain_head raises, surface the marker.

    Pattern-2 elimination proof: pre-W607-AP, the tail-read was guarded
    by a bare ``except OSError: pass`` inside _build_integrity_summary --
    an OSError silently degraded to an empty chain_head. W607-AP routes
    the tail-read through _compute_chain_head and wraps it via
    _run_check_ap, so the disclosure channel names the failure.
    """
    from roam.commands import cmd_audit_trail_export

    def _raise(*args, **kwargs):
        raise OSError("synthetic-chain-head-from-W607-AP")

    monkeypatch.setattr(cmd_audit_trail_export, "_compute_chain_head", _raise)

    result = _invoke_export(cli_runner, valid_trail, "--finalize")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    ch_markers = [m for m in top_wo if m.startswith("audit_trail_export_compute_chain_head_failed:")]
    assert ch_markers, f"expected audit_trail_export_compute_chain_head_failed: marker; got {top_wo!r}"
    assert any("OSError" in m for m in ch_markers), ch_markers


# ---------------------------------------------------------------------------
# (6) warnings_out lands in BOTH summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_audit_trail_export_warnings_out_in_envelope(cli_runner, valid_trail, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_audit_trail_export

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AP")

    monkeypatch.setattr(cmd_audit_trail_export, "_load_records", _raise)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY W607-AP helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_audit_trail_export_helper_raises(cli_runner, valid_trail, monkeypatch):
    """Any non-empty W607-AP bucket -> summary.partial_success = True."""
    from roam.commands import cmd_audit_trail_export

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-from-W607-AP")

    monkeypatch.setattr(cmd_audit_trail_export, "_filter_records", _raise)

    result = _invoke_export(cli_runner, valid_trail)
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
    Mirrors W607-A..AL contracts.
    """
    from roam.commands import cmd_audit_trail_export

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AP")

    monkeypatch.setattr(cmd_audit_trail_export, "_aggregate_records", _raise)

    result = _invoke_export(cli_runner, valid_trail, "--aggregate")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("audit_trail_export_aggregate_records_failed:")]
    assert failure_markers, f"expected audit_trail_export_aggregate_records_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "audit_trail_export_aggregate_records_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``audit_trail_export_*`` only
# ---------------------------------------------------------------------------


def test_marker_prefix_audit_trail_export_not_other_families(cli_runner, valid_trail, monkeypatch):
    """Every surfaced W607-AP marker uses ``audit_trail_export_*``.

    cmd_audit_trail_export is the EXPORT leg of the quartet -- mutually
    distinct from sibling W607-* layers (audit_trail_verify_*,
    audit_trail_conformance_*, attest_*, pr_bundle_*, cga_*, …). Hard
    guard against accidental marker-prefix drift.
    """
    from roam.commands import cmd_audit_trail_export

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AP")

    monkeypatch.setattr(cmd_audit_trail_export, "_build_top_actors", _raise)

    result = _invoke_export(cli_runner, valid_trail, "--top-actors", "5")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("audit_trail_export_"), (
            f"every surfaced W607-AP marker must use the "
            f"``audit_trail_export_*`` prefix family "
            f"(cmd_audit_trail_export scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers. Note: ``audit_trail_``
        # is a common prefix shared with verify/conformance, so we check
        # that the marker does NOT start with the sibling triplet exactly.
        for forbidden_prefix, sibling in (
            ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI"),
            ("audit_trail_conformance_", "cmd_audit_trail_conformance W607-AL"),
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
# (10) Quartet-closure pairing: all 4 prefixes mutually distinct in source
# ---------------------------------------------------------------------------


def test_quartet_closure_prefixes_mutually_distinct():
    """W607-AD/AI/AL/AP marker families coexist in their respective sources.

    Source-level guard pinning the quartet-closure marker-family closed-
    enum invariant: when a downstream aggregator consumes envelopes from
    all 4 audit-trail-family commands, the prefix family alone attributes
    each disclosure correctly.

    The 4 prefix templates (one per family member):
    * audit_trail_export_{phase}_failed       (W607-AP, this wave)
    * audit_trail_conformance_{phase}_failed  (W607-AL)
    * audit_trail_verify_{phase}_failed       (W607-AI)
    * attest_{phase}_failed                   (W607-AD producer)

    Drift here would mean an aggregator could mis-attribute an export
    raise to the verifier (or vice versa) -- a real Pattern-3 vocabulary
    mismatch hazard given all 4 commands operate on the SAME trail.
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"
    export_src_path = src_root / "cmd_audit_trail_export.py"
    conformance_src_path = src_root / "cmd_audit_trail_conformance.py"
    verify_src_path = src_root / "cmd_audit_trail_verify.py"

    assert export_src_path.exists(), export_src_path
    assert conformance_src_path.exists(), conformance_src_path
    assert verify_src_path.exists(), verify_src_path

    export_src = export_src_path.read_text(encoding="utf-8")
    conformance_src = conformance_src_path.read_text(encoding="utf-8")
    verify_src = verify_src_path.read_text(encoding="utf-8")

    # Each family carries its OWN marker template in its OWN source.
    assert "audit_trail_export_{phase}_failed" in export_src, (
        "W607-AP audit_trail_export_{phase}_failed marker template missing "
        "from cmd_audit_trail_export -- exporter-side regressed."
    )
    assert "audit_trail_conformance_{phase}_failed" in conformance_src, (
        "W607-AL audit_trail_conformance_{phase}_failed marker template "
        "missing from cmd_audit_trail_conformance -- conformance-side "
        "regressed."
    )
    assert "audit_trail_verify_{phase}_failed" in verify_src, (
        "W607-AI audit_trail_verify_{phase}_failed marker template missing "
        "from cmd_audit_trail_verify -- verifier-side regressed."
    )

    # The three audit_trail_* prefixes are mutually distinct on full-string
    # equality (a substring relationship is OK because each marker emission
    # appends a phase + failed suffix that disambiguates).
    prefixes = (
        "audit_trail_export_",
        "audit_trail_conformance_",
        "audit_trail_verify_",
    )
    for i, p1 in enumerate(prefixes):
        for j, p2 in enumerate(prefixes):
            if i == j:
                continue
            assert p1 != p2, (p1, p2)


# ---------------------------------------------------------------------------
# (11) PATTERN-2 ELIMINATION drift-guard: no `except ...: pass` blocks
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallbacks_eliminated():
    """W607-AP eliminates the Pattern-2 silent fallback in cmd_audit_trail_export.

    Pre-W607-AP cmd_audit_trail_export had:

      def _build_integrity_summary(records, path):
          ...
          if path.exists():
              try:
                  with path.open("rb") as f:
                      ...
                  for line in tail.strip().split("\\n"):
                      ...
              except OSError:
                  pass     # <-- Pattern-2 silent fallback

    The OSError swallow had no disclosure channel: a disk read failure
    silently degraded chain_head to "" with no warning at all. W607-AP
    extracts the tail-read into ``_compute_chain_head`` and routes it
    through ``_run_check_ap("compute_chain_head", ...)`` so the failure
    surfaces as a structured marker.

    This AST-walk guard pins the elimination: any new ``except ...:
    pass`` in this module fails the test. Mirrors W607-AL test 11.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_export.py"
    src = src_path.read_text(encoding="utf-8")

    tree = ast.parse(src)
    silent_fallbacks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                # Body of a single `pass` is the Pattern-2 antipattern.
                if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
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
        f"W607-AP must eliminate Pattern-2 silent-fallback ``except ...: "
        f"pass`` blocks in cmd_audit_trail_export; still found: "
        f"{silent_fallbacks!r}. Convert each to ``_run_check_ap(...)``."
    )


# ---------------------------------------------------------------------------
# (12) Source-level guard: cmd_audit_trail_export carries the W607-AP accumulator
# ---------------------------------------------------------------------------


def test_cmd_audit_trail_export_carries_w607ap_accumulator():
    """AST-level guard: cmd_audit_trail_export carries the W607-AP accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation fails this guard rather than silently regressing
    every other dynamic envelope-shape test.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_export.py"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607ap_warnings_out" in src, (
        "W607-AP accumulator missing from cmd_audit_trail_export; the substrate-CALL marker plumbing has been removed."
    )
    assert "audit_trail_export_{phase}_failed" in src, (
        "W607-AP marker prefix template missing from cmd_audit_trail_export; "
        'check the `f"audit_trail_export_{phase}_failed:..."` line in '
        "_run_check_ap."
    )
    # Parse-tree level: confirm _run_check_ap is defined inside the command body.
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ap":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AP ``_run_check_ap`` helper not found in "
        "cmd_audit_trail_export AST; the per-substrate wrapper "
        "has been refactored away."
    )


# ---------------------------------------------------------------------------
# (13) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_audit_trail_export substrate boundary is wrapped.

    W607-AP substrate inventory (in order of execution):

    * load_records_finalize     -- --finalize read
    * compute_chain_head        -- tail-read I/O
    * build_integrity_summary   -- closing record
    * append_integrity_summary  -- --finalize append I/O
    * load_records              -- JSONL trail-read
    * filter_records            -- since/until/verdict
    * aggregate_records         -- bucketing
    * build_top_actors          -- ranking
    * render_output             -- projection
    * atomic_write_text         -- I/O boundary

    Accepts indentation depths of 8, 12, 16, 20, 24 spaces to allow for
    refactor of the substrate call sites without breaking the guard.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_export.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "load_records_finalize",
        "compute_chain_head",
        "build_integrity_summary",
        "append_integrity_summary",
        "load_records",
        "filter_records",
        "aggregate_records",
        "build_top_actors",
        "render_output",
        "atomic_write_text",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_ap("{phase}"' in src
        multi_line = any(f'_run_check_ap(\n{" " * indent}"{phase}"' in src for indent in (8, 12, 16, 20, 24))
        assert same_line or multi_line, (
            f"W607-AP _run_check_ap wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (14) QUARTET-CLOSURE pairing: 4 prefix families present across all 4 sources
# ---------------------------------------------------------------------------


def test_quartet_closure_marker_templates_present():
    """W607-AD/AI/AL/AP marker templates each live in their respective source.

    Quartet-closure milestone: the complete audit-trail family is now
    W607-plumbed. This guard pins the closed-enum invariant at the source
    level -- a raise in any of {produce, verify, conform, export} surfaces
    via a different prefix family, never collides.
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    export_src = (src_root / "cmd_audit_trail_export.py").read_text(encoding="utf-8")
    conformance_src = (src_root / "cmd_audit_trail_conformance.py").read_text(encoding="utf-8")
    verify_src = (src_root / "cmd_audit_trail_verify.py").read_text(encoding="utf-8")

    # All three audit_trail_* prefix templates present in their respective
    # sources.
    assert "audit_trail_export_{phase}_failed" in export_src
    assert "audit_trail_conformance_{phase}_failed" in conformance_src
    assert "audit_trail_verify_{phase}_failed" in verify_src

    # The export substrate phases (W607-AP).
    export_phases = (
        "load_records_finalize",
        "compute_chain_head",
        "build_integrity_summary",
        "append_integrity_summary",
        "load_records",
        "filter_records",
        "aggregate_records",
        "build_top_actors",
        "render_output",
        "atomic_write_text",
    )
    for ph in export_phases:
        same_line = f'_run_check_ap("{ph}"' in export_src
        multi_line = any(f'_run_check_ap(\n{" " * indent}"{ph}"' in export_src for indent in (8, 12, 16, 20, 24))
        assert same_line or multi_line, f"export phase {ph!r} missing"

    # Sibling parity guards: conformance and verify accumulators unchanged.
    assert "_w607al_warnings_out" in conformance_src, (
        "W607-AL accumulator removed from cmd_audit_trail_conformance; "
        "W607-AP must not regress the sibling instrumentation."
    )
    assert "_w607ai_warnings_out" in verify_src, (
        "W607-AI accumulator removed from cmd_audit_trail_verify; W607-AP must not regress the sibling instrumentation."
    )


# ---------------------------------------------------------------------------
# (15) Sibling parity -- W607-AI / W607-AL sources unchanged by W607-AP
# ---------------------------------------------------------------------------


def test_w607_ai_and_al_sources_unaffected():
    """Sibling parity guard: W607-AI cmd_audit_trail_verify and W607-AL
    cmd_audit_trail_conformance surfaces unchanged.

    W607-AP lands only in cmd_audit_trail_export. Both sibling source
    surfaces MUST stay identical -- accumulator + marker template present.
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    verify_src_path = src_root / "cmd_audit_trail_verify.py"
    conformance_src_path = src_root / "cmd_audit_trail_conformance.py"
    assert verify_src_path.exists()
    assert conformance_src_path.exists()

    verify_src = verify_src_path.read_text(encoding="utf-8")
    conformance_src = conformance_src_path.read_text(encoding="utf-8")

    # W607-AI sibling: verify
    assert "_w607ai_warnings_out" in verify_src, (
        "W607-AI accumulator removed from cmd_audit_trail_verify; W607-AP must not regress the sibling instrumentation."
    )
    assert "audit_trail_verify_{phase}_failed" in verify_src, (
        "W607-AI marker prefix template removed from cmd_audit_trail_verify; "
        "W607-AP must not regress the sibling marker family."
    )

    # W607-AL sibling: conformance
    assert "_w607al_warnings_out" in conformance_src, (
        "W607-AL accumulator removed from cmd_audit_trail_conformance; "
        "W607-AP must not regress the sibling instrumentation."
    )
    assert "audit_trail_conformance_{phase}_failed" in conformance_src, (
        "W607-AL marker prefix template removed from cmd_audit_trail_conformance; "
        "W607-AP must not regress the sibling marker family."
    )


# ---------------------------------------------------------------------------
# (16) Quartet-coexistence: AP markers can coexist with AI + AL markers on same trail
# ---------------------------------------------------------------------------


def test_quartet_coexistence_export_and_conformance_markers_on_same_trail(cli_runner, valid_trail, monkeypatch):
    """W607-AP audit_trail_export_* markers coexist with W607-AI / W607-AL.

    All 4 audit-trail-family commands process the SAME JSONL trail. When a
    downstream aggregator collects envelopes from {export, conform, verify,
    attest}, each disclosure must be attributable to the right command via
    the prefix family alone.

    This test fires the export pipeline and confirms the marker carries
    the audit_trail_export_ prefix (NOT audit_trail_verify_ or
    audit_trail_conformance_) even though the underlying trail is the
    same one verify/conform would process.
    """
    from roam.commands import cmd_audit_trail_export

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-quartet-coexist-from-W607-AP")

    monkeypatch.setattr(cmd_audit_trail_export, "_load_records", _raise)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # The export marker fires...
    export_markers = [m for m in top_wo if m.startswith("audit_trail_export_")]
    assert export_markers, f"expected audit_trail_export_ markers for quartet-coexistence smoke test; got {top_wo!r}"
    # ...and NO sibling-family markers leak from the same envelope.
    sibling_leaks = [
        m
        for m in top_wo
        if (m.startswith("audit_trail_verify_") or m.startswith("audit_trail_conformance_") or m.startswith("attest_"))
    ]
    assert not sibling_leaks, f"sibling W607-* marker families leaked into the export envelope; got {sibling_leaks!r}"
