"""W607-CN -- additive aggregation-phase plumbing for ``cmd_audit_trail_verify``.

cmd_audit_trail_verify is the HMAC chain-verify READER (W146/W829/W830).
With W607-CN landed, the verifier's full build path is now dual-bucket
plumbed via:

  - substrate-CALL layer: W607-AI (4 substrate boundaries:
    verify_chain / open_findings_db / emit_findings / commit_findings)
  - aggregation-phase layer: W607-CN (3 aggregation boundaries:
    compute_predicate / compute_verdict / serialize_envelope)

Both layers share the canonical ``audit_trail_verify_*`` marker family
and the ``audit_trail_verify_<phase>_failed:<exc_class>:<detail>`` shape
contract. The two buckets (``_w607ai_warnings_out`` substrate-CALL +
``_w607cn_warnings_out`` aggregation-phase) are combined at envelope-emit
time so consumers see the full degradation lineage in marker-emission
order.

AUDIT-TRAIL FAMILY pairing
--------------------------

cmd_audit_trail_verify (W607-AI + CN), cmd_audit_trail_conformance
(W607-AL), and cmd_audit_trail_export (W607-AP) form the audit-trail
verb family. The pairing test below confirms each command's markers
stay in its OWN family and never bleed into a sibling's envelope.

W829 + W830 regression guard
----------------------------

W829 + W830 sealed two critical contracts:
  - Pattern-2 + 3-state matrix (valid / broken / uninitialized) on the
    structured envelope (W829)
  - ``--gate`` fail-closed on BOTH ``broken`` AND ``uninitialized``
    (W830)

W607-CN MUST NOT re-introduce a Pattern-2 silent-SAFE regression. The
compute_predicate floor lands on broken (chain_valid=False) -- NEVER a
clean SAFE -- so a poisoned predicate still trips the broken-branch
verdict. The compute_verdict floor names the absent state via a literal
"Audit-trail verification completed" string (LAW 6 standalone-parse
discipline). The --gate behaviour test below exercises the
chain-uninitialised exit-5 + chain-broken exit-5 contract.

W978 first-hypothesis check (kwarg-default eagerness trap)
----------------------------------------------------------

cmd_sbom W607-CG sealed a recurring W978 axis: ``_run_check_X("phase",
fn, default={"x": len(records) if ...})`` -- Python evaluates the
``default=`` kwarg BEFORE the wrap call. cmd_taint W607-CJ added the
follow-on discipline of MOVING ``len()`` calls INSIDE the wrapped
closure (not at kwarg-bind time). Every W607-CN ``default=`` MUST be a
literal constant, not computed from upstream values. The defensive
test below exercises the floor on a corrupt-input sentinel (mirrors
cmd_sbom's ``_BadDeps(list)`` shape).

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
# (matches the W607-AI test fixture shape)
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
    lines = path.read_text(encoding="utf-8").splitlines()
    rec2 = _json.loads(lines[1])
    rec2["verdict"] = "TAMPERED"
    lines[1] = _json.dumps(rec2, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CN aggregation markers
# ---------------------------------------------------------------------------


def test_audit_trail_verify_happy_path_no_w607cn_markers(cli_runner, valid_trail):
    """Clean verify on a valid trail -> no W607-CN aggregation markers.

    Hash-stable: an empty W607-CN bucket on the success path must produce
    an envelope without any
    ``audit_trail_verify_compute_predicate_failed:`` /
    ``audit_trail_verify_compute_verdict_failed:`` /
    ``audit_trail_verify_serialize_envelope_failed:`` markers (from the
    CN layer).
    """
    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "audit-trail-verify"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607cn_phases = (
        "audit_trail_verify_compute_predicate_failed:",
        "audit_trail_verify_compute_verdict_failed:",
        "audit_trail_verify_serialize_envelope_failed:",
    )
    for prefix in w607cn_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean audit-trail-verify must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_cn`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_audit_trail_verify_carries_w607cn_accumulator():
    """AST-level guard: cmd_audit_trail_verify source carries the W607-CN
    accumulator.

    Pins the canonical W607-CN anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AI) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"
    assert src_path.exists(), f"cmd_audit_trail_verify.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607cn_warnings_out" in src, (
        "W607-CN accumulator missing from cmd_audit_trail_verify; the "
        "additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_cn" in src, (
        "W607-CN helper ``_run_check_cn`` missing from "
        "cmd_audit_trail_verify; the additive wrapper has been refactored "
        "away."
    )

    # Parse-tree level: confirm _run_check_cn is defined inside the command.
    tree = ast.parse(src)
    found_run_check_cn = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cn":
            found_run_check_cn = True
            break
    assert found_run_check_cn, (
        "W607-CN ``_run_check_cn`` helper not found in "
        "cmd_audit_trail_verify AST; the additive aggregation-phase "
        "wrapper has been refactored away."
    )

    # W607-AI must still be present (additive layer does NOT replace it)
    assert "_w607ai_warnings_out" in src, (
        "W607-AI accumulator vanished alongside the W607-CN add; the "
        "additive plumbing must preserve the W607-AI substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_cn():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_cn(...)`` with the canonical phase name.

    The three phases must appear inside a ``_run_check_cn("<phase>", ...)``
    call inside cmd_audit_trail_verify.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_cn(\n        "{phase}"',
            f'_run_check_cn(\n            "{phase}"',
            f'_run_check_cn(\n                "{phase}"',
            f'_run_check_cn(\n                    "{phase}"',
            f'_run_check_cn(\n                        "{phase}"',
            f'_run_check_cn("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_cn(...); add the W607-CN guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) compute_predicate failure marker -- floors land on BROKEN, not SAFE
# ---------------------------------------------------------------------------


def test_compute_predicate_failure_marker_and_broken_floor(cli_runner, valid_trail, monkeypatch):
    """If compute_predicate raises, surface marker AND floor lands on broken.

    Pattern-2 / W826 silent-fallback discipline: a poisoned predicate
    floor MUST land on a non-SAFE state (chain_valid=False), NEVER on a
    clean SAFE that pretends the chain was verified.

    Strategy: monkeypatch ``Path.exists`` on the local Path instance via
    a ``_BadIssueIterable`` that raises when the predicate logic walks
    ``issues`` -- the closure body trips, the wrap surfaces a marker,
    and the floor lands on broken.
    """
    from roam.commands import cmd_audit_trail_verify

    class _BadIssueList(list):
        # mimic the list shape but raise on iteration (the predicate
        # closure uses ``any(... for i in _issues)`` which forces
        # iteration)
        def __iter__(self):
            raise RuntimeError("synthetic-compute-predicate-from-W607-CN")

    def _bad_verify_chain(*args, **kwargs):
        # _verify_chain returns (records, issues). Return a healthy
        # records list paired with a poisoned issues list so the
        # predicate closure trips on the ``has_real_issues`` line.
        return ([{"timestamp": "x"}], _BadIssueList())

    monkeypatch.setattr(cmd_audit_trail_verify, "_verify_chain", _bad_verify_chain)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("audit_trail_verify_compute_predicate_failed:")]
    assert markers, f"expected ``audit_trail_verify_compute_predicate_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers

    # Pattern-2 / W826 silent-fallback: floor MUST land on broken
    # (chain_valid=False), NOT a clean SAFE.
    assert data["summary"].get("chain_valid") is False, (
        f"W826 regression: compute_predicate floor allowed chain_valid=True; got summary={data['summary']!r}"
    )
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (5) compute_verdict failure marker -- W978 first-hypothesis check
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_string():
    """W978 first-hypothesis check: the compute_verdict floor must be a
    literal string -- not an f-string re-interpolating the values that
    just raised.

    The canonical floor literal for cmd_audit_trail_verify is
    "Audit-trail verification completed" (LAW 6 standalone-parse).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"
    src = src_path.read_text(encoding="utf-8")

    # W978: the canonical floor for compute_verdict must be a literal
    # string -- not an f-string re-interpolating values that just raised.
    assert '"verdict": "Audit-trail verification completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-CN "
        "discipline; the canonical floor literal 'Audit-trail verification "
        "completed' is missing from cmd_audit_trail_verify.py"
    )


# ---------------------------------------------------------------------------
# (6) serialize_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607cn_serialize_envelope_floor_on_raise(cli_runner, valid_trail, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``audit_trail_verify_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("audit-trail-verify", ...)`` would otherwise crash
    AFTER all substrate + aggregation signals were already gathered. The
    consumer must still receive a parseable JSON object with the marker
    attached + the canonical command name.
    """
    from roam.commands import cmd_audit_trail_verify as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-CN")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "audit-trail-verify", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("audit_trail_verify_serialize_envelope_failed:")]
    assert markers, f"expected ``audit_trail_verify_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, valid_trail, monkeypatch):
    """ANY W607-CN or W607-AI marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    chain" from "chain verify ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_audit_trail_verify as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-CN")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CN warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607cn_warnings_out_in_both_top_and_summary(cli_runner, valid_trail, monkeypatch):
    """Non-empty W607-CN bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-AI contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_audit_trail_verify as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CN")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CN raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CN raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("audit_trail_verify_serialize_envelope_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("audit_trail_verify_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-CN uses ``audit_trail_verify_*`` family
# ---------------------------------------------------------------------------


def test_w607cn_marker_prefix_audit_trail_verify_family(cli_runner, valid_trail, monkeypatch):
    """W607-CN markers use the canonical ``audit_trail_verify_*`` prefix
    (same family as W607-AI; W607-CN is ADDITIVE, not a separate prefix).

    Hard guard: any W607-CN marker that leaks into a sibling W607-*
    family (``audit_trail_conformance_*`` / ``audit_trail_export_*`` /
    ``runs_*``) breaks the closed-enum marker-family contract.
    """
    from roam.commands import cmd_audit_trail_verify as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-CN")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("audit_trail_verify_"), (
            f"every W607-CN marker must use the ``audit_trail_verify_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (10) W607-AI COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607ai_substrate_markers_coexist_with_w607cn_aggregation(cli_runner, valid_trail, monkeypatch):
    """Confirm ``audit_trail_verify_<substrate-phase>_failed:`` markers
    (W607-AI layer) coexist with
    ``audit_trail_verify_<agg-phase>_failed:`` markers (W607-CN layer) --
    both in same family, but threaded through different buckets at
    envelope-emit.

    This is the explicit guard requested by the W607-CN brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-phase disambiguation
    (``audit_trail_verify_<substrate-phase>_failed:`` vs.
    ``audit_trail_verify_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_audit_trail_verify as _mod

    # W607-AI substrate boundary -- _verify_chain raises
    def _raise_verify_chain(*a, **kw):
        raise RuntimeError("synthetic-ai-coexist-verify-chain")

    # W607-CN aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-cn-coexist-envelope")

    monkeypatch.setattr(_mod, "_verify_chain", _raise_verify_chain)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-AI
    ai_markers = [m for m in top_wo if m.startswith("audit_trail_verify_verify_chain_failed:")]
    # Aggregation-phase from W607-CN
    cn_markers = [m for m in top_wo if m.startswith("audit_trail_verify_serialize_envelope_failed:")]

    assert ai_markers, f"W607-AI substrate-CALL marker (audit_trail_verify_verify_chain_failed) missing; got {top_wo!r}"
    assert cn_markers, (
        f"W607-CN aggregation-phase marker (audit_trail_verify_serialize_envelope_failed) missing; got {top_wo!r}"
    )

    # Both share the canonical ``audit_trail_verify_*`` family
    assert all(m.startswith("audit_trail_verify_") for m in (ai_markers + cn_markers)), (
        f"all markers must share the canonical ``audit_trail_verify_*`` "
        f"family; got ai = {ai_markers!r}, cn = {cn_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) CROSS-PREFIX ISOLATION -- audit_trail_verify_* markers DO NOT leak
# into adjacent commands' marker families
# ---------------------------------------------------------------------------


def test_audit_trail_verify_markers_do_not_leak_into_adjacent_commands(cli_runner, valid_trail, monkeypatch):
    """``audit_trail_verify_*`` markers must NOT appear with foreign
    prefixes (``audit_trail_conformance_*`` / ``audit_trail_export_*`` /
    ``runs_*`` / ``attest_*``) when verify raises.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels.
    """
    from roam.commands import cmd_audit_trail_verify as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-isolation-from-W607-CN")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    # Every failure marker must start with audit_trail_verify_ -- foreign-
    # family leakage is a bug. The adjacent commands' prefixes shape the
    # forbidden set.
    foreign_prefixes = (
        "audit_trail_conformance_",
        "audit_trail_export_",
        "runs_",
        "attest_",
        "pr_bundle_",
        "cga_",
        "pr_analyze_",
        "diff_",
        "critique_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_audit_trail_verify warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) AUDIT-TRAIL FAMILY pairing -- markers coexist with sibling
# audit_trail_conformance_* + audit_trail_export_* families
# ---------------------------------------------------------------------------


def test_audit_trail_family_marker_families_coexist():
    """AUDIT-TRAIL FAMILY pairing guard requested by the W607-CN brief.

    Confirm that ``audit_trail_verify_<phase>_failed:`` markers (W607-AI
    + W607-CN) stay in the canonical ``audit_trail_verify_*`` family
    while sibling commands carry their own distinct prefixes:

      - cmd_audit_trail_verify     -> ``audit_trail_verify_*``       (W607-AI + CN)
      - cmd_audit_trail_conformance -> ``audit_trail_conformance_*``  (W607-AL)
      - cmd_audit_trail_export     -> ``audit_trail_export_*``       (W607-AP)

    Source-level guard pinning the closed-enum invariant: an aggregator
    consuming envelopes from all 3 commands can attribute each disclosure
    via prefix alone, with NO ambiguity between siblings.
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    verify_src = (src_root / "cmd_audit_trail_verify.py").read_text(encoding="utf-8")
    conformance_src = (src_root / "cmd_audit_trail_conformance.py").read_text(encoding="utf-8")
    export_src = (src_root / "cmd_audit_trail_export.py").read_text(encoding="utf-8")

    # cmd_audit_trail_verify carries the audit_trail_verify_ marker prefix.
    assert "audit_trail_verify_{phase}_failed" in verify_src, (
        "W607-AI/CN audit_trail_verify_{phase}_failed marker template "
        "missing from cmd_audit_trail_verify -- verifier-side "
        "instrumentation regressed."
    )

    # cmd_audit_trail_conformance carries its own marker prefix
    # (W607-AL). Allow for either the template string or the substring.
    assert (
        "audit_trail_conformance_{phase}_failed" in conformance_src or "audit_trail_conformance_" in conformance_src
    ), (
        "W607-AL audit_trail_conformance_* marker family missing from "
        "cmd_audit_trail_conformance -- sibling instrumentation regressed."
    )

    # cmd_audit_trail_export carries its own marker prefix (W607-AP).
    assert "audit_trail_export_{phase}_failed" in export_src or "audit_trail_export_" in export_src, (
        "W607-AP audit_trail_export_* marker family missing from "
        "cmd_audit_trail_export -- sibling instrumentation regressed."
    )

    # The three prefixes do not collide -- audit_trail_verify_ is NOT a
    # prefix of any sibling, and no sibling is a prefix of it.
    for sibling_prefix in ("audit_trail_conformance_", "audit_trail_export_"):
        assert not "audit_trail_verify_".startswith(sibling_prefix)
        assert not sibling_prefix.startswith("audit_trail_verify_")


# ---------------------------------------------------------------------------
# (13) W829 + W830 regression guard -- 3-state matrix + --gate fail-closed
# ---------------------------------------------------------------------------


def test_w829_three_state_matrix_preserved_through_w607cn(cli_runner, tmp_path):
    """W829 regression guard: the 3-state (valid / broken / uninitialized)
    classification must survive the W607-CN aggregation plumbing.

    A missing-trail invocation hits the compute_predicate +
    compute_verdict path naturally (no monkeypatching) and confirms:

      - state = "uninitialized"
      - partial_success = True
      - verdict names the absent state (not silent SAFE)
    """
    missing_trail = tmp_path / "definitely-not-there.jsonl"
    result = _invoke_verify(cli_runner, missing_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    assert summary.get("state") == "uninitialized", (
        f"W829 regression: missing-trail must yield state='uninitialized'; got summary = {summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"W829 regression: uninitialized state must flip partial_success=True; got summary = {summary!r}"
    )
    verdict_lower = summary.get("verdict", "").lower()
    # Pattern-2: name the absent state, NEVER emit a silent SAFE.
    assert (
        "not initialized" in verdict_lower or "no audit trail" in verdict_lower or "uninitialized" in verdict_lower
    ), f"W829 regression: verdict must name the absent state explicitly; got verdict = {summary.get('verdict')!r}"


def test_w830_gate_fail_closed_on_uninitialized_through_w607cn(cli_runner, tmp_path):
    """W830 regression guard: ``--gate`` exits 5 on uninitialized state.

    Confirms the W607-CN aggregation plumbing has NOT re-broken the
    W830 fail-closed contract: a missing audit trail with ``--gate``
    still exits 5, NOT 0 (silent pass on no-evidence-chain).
    """
    missing_trail = tmp_path / "definitely-not-there.jsonl"
    result = _invoke_verify(cli_runner, missing_trail, "--gate")
    # W830: --gate fail-closed on uninitialized -> exit 5
    assert result.exit_code == 5, (
        f"W830 regression: --gate must exit 5 on uninitialized chain; "
        f"got exit_code={result.exit_code!r}, output={result.output!r}"
    )


def test_w830_gate_fail_closed_on_broken_through_w607cn(cli_runner, tampered_trail):
    """W830 regression guard: ``--gate`` exits 5 on broken state.

    Confirms the W607-CN aggregation plumbing has NOT re-broken the
    W830 fail-closed contract: a tampered audit trail with ``--gate``
    still exits 5 -- the broken branch of compute_verdict produces
    chain_valid=False which the gate consumes.
    """
    runner = CliRunner()
    from roam.cli import cli

    args = ["--json", "audit-trail-verify", "--input", str(tampered_trail), "--gate"]
    result = runner.invoke(cli, args, catch_exceptions=False)
    # W830: --gate fail-closed on broken -> exit 5
    assert result.exit_code == 5, (
        f"W830 regression: --gate must exit 5 on broken chain; "
        f"got exit_code={result.exit_code!r}, output={result.output!r}"
    )


# ---------------------------------------------------------------------------
# (14) Chain-broken path coverage -- aggregation-layer produces correct verdict
# ---------------------------------------------------------------------------


def test_chain_broken_path_through_aggregation_layer(cli_runner, tampered_trail):
    """Tampered trail -> compute_predicate produces chain_valid=False ->
    compute_verdict produces the broken-branch verdict.

    Exercises the full aggregation path through W607-CN on a real
    tampered chain (no monkeypatching). Confirms the aggregation layer
    correctly relays the broken-chain state from the substrate-layer
    output to the envelope.
    """
    result = _invoke_verify(cli_runner, tampered_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    # compute_predicate output reflects broken chain
    assert summary.get("chain_valid") is False, summary
    # compute_verdict produces the broken-branch verdict
    assert "BROKEN" in summary.get("verdict", ""), (
        f"compute_verdict must emit BROKEN on tampered trail; got verdict = {summary.get('verdict')!r}"
    )
    assert summary.get("state") == "broken", summary
    assert summary.get("partial_success") is True, summary
    # No W607 markers fired -- this is a CLEAN run on a tampered chain;
    # the broken-chain detection is the natural envelope output, NOT
    # marker-degraded output.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    assert not any(m.startswith("audit_trail_verify_") and "_failed:" in m for m in all_markers), (
        f"clean tampered-trail run must NOT surface any markers; got markers = {all_markers!r}"
    )


# ---------------------------------------------------------------------------
# (15) W978 KWARG-DEFAULT EAGERNESS TRAP -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """W978 kwarg-default audit: every W607-CN ``default=`` must be a
    literal constant, NOT computed from upstream values.

    cmd_sbom W607-CG sealed this axis after a regression where
    ``len(_BadDeps())`` defaults eagerly raised inside the ``default=``
    expression -- BEFORE the wrap call entered the try-block. Floor
    expressions in ``default=`` MUST be literal constants.

    AST audit: walk every ``_run_check_cn(...)`` call, extract the
    ``default=`` keyword argument's AST node, confirm it is a Constant
    (literal int/str/bool/None) or a Dict/List/Set/Tuple of Constants.
    Reject any Call, Attribute, BinOp, UnaryOp, Compare, or f-string node
    in the default expression -- these compute from upstream values at
    kwarg-bind time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        """True iff ``node`` is a fully-literal AST subtree.

        Allows: Constant, Dict/List/Tuple/Set of literals, unary +/- of
        a constant, and bare Name references (variables bound BEFORE the
        wrap call, e.g. ``default=_envelope_floor``). Rejects Call,
        Attribute, Subscript, BinOp, Compare, IfExp, f-string, etc. --
        these can compute over potentially-poisoned upstream values at
        kwarg-bind time and raise BEFORE the wrap's try-block enters.

        Note: bare ``Name`` references are safe because the underlying
        variable was constructed at an earlier statement; the kwarg-bind
        only reads the already-built value. The W978 trap fires on
        expressions, not on name lookups.
        """
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.Dict):
            return all(_is_literal(k) for k in node.keys if k is not None) and all(_is_literal(v) for v in node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_literal(e) for e in node.elts)
        # ast.UnaryOp with USub on a constant (e.g. -1) is acceptable
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _is_literal(node.operand)
        return False

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match _run_check_cn(...)
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cn"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_cn(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in "
        "cmd_audit_trail_verify.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ for the canonical fix "
        "pattern."
    )


def test_w978_kwarg_default_does_not_eagerly_raise_on_bad_input(cli_runner, valid_trail, monkeypatch):
    """W978 defensive test: exercise the floor on a corrupt-input
    sentinel (mirrors cmd_sbom's ``_BadDeps(list)`` shape +
    cmd_taint's ``_BadFindingList(list)`` follow-on).

    Patches ``_verify_chain`` to return lists with a ``__len__`` that
    raises. If ANY W607-CN ``default=`` kwarg eagerly computed ``len()``
    over this input, the raise would escape the try-block and crash the
    envelope. The literal-constant floors below catch the raise inside
    the wrapped call and surface a marker.
    """
    from roam.commands import cmd_audit_trail_verify as _mod

    class _BadChainState(list):
        # Mimics cmd_sbom's _BadDeps(list) regression sentinel: a
        # list-like whose ``__len__`` raises.
        def __len__(self):
            raise RuntimeError("synthetic-w978-bad-chain-state-from-W607-CN")

    def _bad_verify_chain(*args, **kwargs):
        # Return (records, issues) where ``records`` is a bad-length
        # list. compute_predicate's ``bool(_records)`` walks records
        # (safe) but the verdict closure calls ``len(_records)`` --
        # which would raise inside the closure body if W607-CN's wrap
        # didn't catch.
        return (_BadChainState(), [])

    monkeypatch.setattr(_mod, "_verify_chain", _bad_verify_chain)

    result = _invoke_verify(cli_runner, valid_trail)
    # The command MUST NOT crash -- a marker must be on the envelope
    # rather than the raise escaping the wrap.
    assert result.exit_code == 0, f"W978 violation: bad-list sentinel caused crash; output={result.output!r}"
    data = _json.loads(result.output)
    # Envelope must be parseable and carry SOMETHING in warnings_out
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # Either W607-AI or W607-CN family must carry a marker
    audit_markers = [m for m in all_wo if m.startswith("audit_trail_verify_") and "_failed:" in m]
    assert audit_markers, (
        f"W978 regression: bad-list sentinel produced no marker on the "
        f"envelope; the bad input either bypassed the wraps or eagerly "
        f"raised in default=; got all_wo={all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (16) compute_predicate floor lands on broken-shape (Pattern-2 silent-SAFE
# discipline)
# ---------------------------------------------------------------------------


def test_compute_predicate_floor_lands_on_broken_not_safe(cli_runner, valid_trail, monkeypatch):
    """If compute_predicate raises, the floor MUST land on broken-shape
    predicates (chain_valid=False, has_real_issues=True), NEVER on a
    clean SAFE that pretends the chain was verified.

    Pattern-2 + W826 silent-fallback discipline carried into the W607-CN
    aggregation-phase plumbing. The downstream verdict assembly then
    names the absent state via the broken branch.

    W978 first-hypothesis: the floor MUST be a literal dict with explicit
    False/True values, NOT a computed expression that re-walks the
    (potentially poisoned) inputs.
    """
    from roam.commands import cmd_audit_trail_verify as _mod

    class _BadIssueIter(list):
        def __iter__(self):
            raise RuntimeError("synthetic-compute-predicate-floor-from-W607-CN")

    def _bad_verify_chain(*args, **kwargs):
        return ([{"timestamp": "x"}], _BadIssueIter())

    monkeypatch.setattr(_mod, "_verify_chain", _bad_verify_chain)

    result = _invoke_verify(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Floor produces chain_valid=False per W826 silent-fallback
    # discipline.
    assert data["summary"].get("chain_valid") is False, data["summary"]
    # partial_success flips on any non-empty bucket
    assert data["summary"].get("partial_success") is True, data["summary"]
    # And the marker is on the bucket
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    assert any(m.startswith("audit_trail_verify_compute_predicate_failed:") for m in all_wo), (
        f"expected audit_trail_verify_compute_predicate_failed: marker after compute_predicate raise; got {all_wo!r}"
    )
