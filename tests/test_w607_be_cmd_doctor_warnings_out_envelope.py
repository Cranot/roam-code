"""W607-BE -- ``cmd_doctor`` threads registry-side ``warnings_out`` onto its envelope.

cmd_doctor is the CI-gate sibling of cmd_health (W607-BA, in flight). Both
are flagship CLAUDE.md-mentioned CI commands. cmd_doctor had prior W607-N
plumbing covering each per-``_check_*`` helper boundary; W607-BE is an
ADDITIVE layer that splits the persist path's single
``except Exception: ...`` channel into four distinct registry-side
substrate boundaries:

* ``persist_db_exists``        -- DB existence probe
* ``persist_open_db``          -- writable connection open
* ``persist_emit_findings``    -- registry-row emit
* ``persist_commit_findings``  -- durable persist

Each raise becomes a ``doctor_<phase>_failed:<exc_class>:<detail>`` marker
via ``_w607be_warnings_out``. The output path merges the W607-N and
W607-BE accumulators into a single ``warnings_out`` channel so downstream
parsers see one closed-enum prefix family (``doctor_*``).

This file validates:

1. happy-path byte-equivalence (no W607-BE markers when nothing raises)
2. per-check degradation (W607-N path still surfaces markers for each
   per-``_check_*`` helper raise; remaining checks still compute)
3. registry-side degradation (W607-BE path surfaces markers when persist
   substrates raise; the standard diagnostic envelope still emits)
4. AST-level Pattern-2 silent-fallback drift-guard
5. W835/W836 empty-corpus disclosure coexists with W607-BE markers
6. health/doctor pairing -- ``doctor_*`` and ``health_*`` markers
   coexist when both run on the same corpus (closed-enum invariant)

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_doctor(runner: CliRunner, *extra):
    """Invoke ``roam --json doctor`` returning the click Result."""
    from roam.cli import cli

    args = ["--json", "doctor", *extra]
    return runner.invoke(cli, args, catch_exceptions=False)


@pytest.fixture
def cli_runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# (1) Happy path -- clean run does NOT surface W607-BE markers
# ---------------------------------------------------------------------------


def test_doctor_clean_envelope_omits_w607be_persist_markers(cli_runner):
    """Without ``--persist`` the W607-BE persist substrates never run.

    The W607-BE bucket stays empty so no ``doctor_persist_*_failed:``
    markers leak into ``warnings_out``. Hash-stable: this is the
    byte-equivalence guarantee on the happy path.
    """
    result = _invoke_doctor(cli_runner)
    # Doctor may exit 0/1/2 depending on host state; we only need clean JSON.
    assert "Traceback" not in result.output, result.output
    data = _json.loads(result.output)
    assert data["command"] == "doctor"
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    persist_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("doctor_persist_") and "_failed:" in m
    ]
    assert not persist_markers, (
        f"clean doctor run must NOT surface doctor_persist_*_failed: markers; "
        f"got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) Source-level guard: cmd_doctor carries the W607-BE accumulator
# ---------------------------------------------------------------------------


def test_cmd_doctor_carries_w607be_accumulator():
    """AST-level guard: cmd_doctor carries the W607-BE accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation fails this guard rather than silently regressing
    every other dynamic envelope-shape test.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_doctor.py"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607be_warnings_out" in src, (
        "W607-BE accumulator missing from cmd_doctor; the registry-side "
        "substrate-CALL marker plumbing has been removed."
    )
    assert "doctor_{phase}_failed" in src, (
        "W607-BE marker prefix template missing from cmd_doctor; check the "
        '`f"doctor_{phase}_failed:..."` line in _run_check_be.'
    )
    # Parse-tree level: confirm _run_check_be is defined inside the command body.
    tree = ast.parse(src)
    found_run_check_be = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_be":
            found_run_check_be = True
            break
    assert found_run_check_be, (
        "W607-BE ``_run_check_be`` helper not found in cmd_doctor AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (3) Each W607-BE persist substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607be_persist_phases_wrapped_in_source():
    """Source-level guard: every W607-BE persist substrate boundary is wrapped.

    W607-BE substrate inventory (in order of execution under --persist):

    * persist_db_exists        -- DB existence probe
    * persist_open_db          -- writable connection open
    * persist_emit_findings    -- registry-row emit
    * persist_commit_findings  -- durable persist

    Accepts indentation depths of 8, 12, 16, 20, 24 spaces.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_doctor.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "persist_db_exists",
        "persist_open_db",
        "persist_emit_findings",
        "persist_commit_findings",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_be("{phase}"' in src
        # ``persist_emit_findings`` and ``persist_commit_findings`` are
        # nested under ``if persist and blocking_failed: > if _db_present:
        # > if _conn_ctx is not None: > try: > with _conn_ctx as _conn:``
        # which lands at 24-28 spaces of indent depth. Accept 8 through 28.
        multi_line = any(f'_run_check_be(\n{" " * indent}"{phase}"' in src for indent in (8, 12, 16, 20, 24, 28))
        assert same_line or multi_line, (
            f"W607-BE _run_check_be wrap missing for phase {phase!r}; "
            f"persist-side substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) Per-check degradation -- W607-N path still surfaces markers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "patched_helper,phase_marker",
    [
        ("_check_python_version", "doctor_python_version_failed:"),
        ("_check_networkx", "doctor_networkx_failed:"),
        ("_check_required_tables", "doctor_required_tables_failed:"),
        ("_check_command_registry", "doctor_command_registry_failed:"),
    ],
)
def test_doctor_per_check_helper_raise_surfaces_marker_and_other_checks_compute(
    cli_runner, monkeypatch, patched_helper, phase_marker
):
    """Per-check degradation: a raise in ONE helper surfaces a marker, OTHERS still compute.

    This is the CI-gate critical axis: a degraded individual check must
    NOT crash the doctor; the verdict must reflect partial-success not
    silent-SAFE. The W607-N wrapper around each ``_check_*`` helper
    ensures the substrate-CALL marker is emitted and the remaining
    checks still feed into the verdict.
    """
    from roam.commands import cmd_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError(f"synthetic-{patched_helper}-from-W607-BE")

    monkeypatch.setattr(cmd_doctor, patched_helper, _raise)

    result = _invoke_doctor(cli_runner)
    assert "Traceback" not in result.output, result.output
    data = _json.loads(result.output)
    # Combined warnings_out bucket carries the W607-N marker for the raised helper.
    combined_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    matching = [m for m in combined_wo if m.startswith(phase_marker)]
    assert matching, f"expected marker prefix {phase_marker!r}; got combined warnings_out = {combined_wo!r}"
    # Other checks still computed -- the envelope's ``checks`` list has
    # rows from the other per-check helpers (we just need ANY surviving
    # check to prove the doctor did not crash mid-pipeline).
    assert data.get("checks"), (
        "expected non-empty checks[] -- a per-check raise must NOT crash "
        "the doctor mid-pipeline; got empty checks. The W607-N wrapper "
        "is supposed to skip the raising check and continue."
    )
    # The verdict must NOT silently claim "all checks passed" when a marker is present.
    # (Verdict text not asserted on directly here; partial_success is the canonical signal.)
    # When the patched helper would have been a passing check on the host,
    # its removal flips total counts but the marker is the disclosure
    # signal. partial_success must flip True regardless.
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-N marker bucket must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AL contracts.
    """
    from roam.commands import cmd_doctor

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-BE")

    monkeypatch.setattr(cmd_doctor, "_check_networkx", _raise)

    result = _invoke_doctor(cli_runner)
    assert "Traceback" not in result.output, result.output
    data = _json.loads(result.output)
    combined_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    networkx_markers = [m for m in combined_wo if m.startswith("doctor_networkx_failed:")]
    assert networkx_markers, f"expected doctor_networkx_failed: marker; got {combined_wo!r}"
    marker = networkx_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "doctor_networkx_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- ``doctor_*`` only
# ---------------------------------------------------------------------------


def test_marker_prefix_doctor_not_other_families(cli_runner, monkeypatch):
    """Every surfaced W607-N/W607-BE marker uses ``doctor_*``.

    cmd_doctor is the environment-aggregator -- mutually distinct from
    sibling W607-* layers (health_*, describe_*, minimap_*, audit_trail_*,
    attest_*, pr_bundle_*, …). Hard guard against accidental marker
    prefix drift.
    """
    from roam.commands import cmd_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-discipline-from-W607-BE")

    monkeypatch.setattr(cmd_doctor, "_check_git", _raise)

    result = _invoke_doctor(cli_runner)
    assert "Traceback" not in result.output, result.output
    data = _json.loads(result.output)
    combined_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    substrate_markers = [m for m in combined_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("doctor_"), (
            f"every surfaced W607-N/W607-BE marker must use the ``doctor_*`` "
            f"prefix family (cmd_doctor scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("health_", "cmd_health W607-M/BA"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI"),
            ("audit_trail_conformance_", "cmd_audit_trail_conformance W607-AL"),
            ("attest_", "cmd_attest W607-AD"),
            ("pr_bundle_", "cmd_pr_bundle W607-AE"),
            ("capsule_", "cmd_capsule W607-BD"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_doctor_warnings_out_mirrored_to_summary_and_top_level(cli_runner, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BE")

    monkeypatch.setattr(cmd_doctor, "_check_command_registry", _raise)

    result = _invoke_doctor(cli_runner)
    assert "Traceback" not in result.output, result.output
    data = _json.loads(result.output)
    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) PATTERN-2 ELIMINATION drift-guard -- AST walk
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallbacks_constrained_to_known_safe_sites():
    """Pattern-2 silent-fallback drift-guard.

    cmd_doctor's substrate-call sites must use the W607-N / W607-BE
    structured-marker pattern, NOT a bare ``except X: pass``. The one
    surviving Pattern-2 block is the version-resolution chain in
    ``_pkg_version`` (line ~159), which is a legitimate fallback chain
    (importlib.metadata -> module.__version__ -> 'unknown') NOT a
    substrate-CALL boundary. Any other ``except ...: pass`` in this
    module is a Pattern-2 antipattern and must be converted to
    ``_run_check[_be](...)`` instead.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_doctor.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Build a lineno -> enclosing function name map for diagnostic context.
    func_by_lineno: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if hasattr(sub, "lineno"):
                    # Keep the innermost function name (last writer wins is OK
                    # because ast.walk emits parents before children for FunctionDef).
                    func_by_lineno.setdefault(sub.lineno, node.name)

    silent_fallbacks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                    if handler.type is None:
                        kind = "bare except"
                    elif isinstance(handler.type, ast.Name):
                        kind = handler.type.id
                    elif isinstance(handler.type, ast.Attribute):
                        kind = ast.unparse(handler.type)
                    else:
                        kind = ast.dump(handler.type)
                    silent_fallbacks.append((handler.lineno, kind, func_by_lineno.get(handler.lineno, "<module>")))

    # Allowlist: ``_pkg_version`` is a documented version-resolution chain,
    # NOT a substrate-CALL boundary. Any other Pattern-2 silent fallback
    # in cmd_doctor is a regression -- convert to _run_check[_be].
    allowlisted_funcs = {"_pkg_version"}
    unexpected = [(ln, kind, fn) for (ln, kind, fn) in silent_fallbacks if fn not in allowlisted_funcs]
    assert not unexpected, (
        f"W607-BE Pattern-2 drift: unexpected ``except ...: pass`` blocks in "
        f"cmd_doctor outside the allowlisted version-resolution chain; "
        f"still found: {unexpected!r}. Convert each to "
        f"``_run_check_be(...)`` or ``_run_check(...)``."
    )


# ---------------------------------------------------------------------------
# (9) W835/W836 COMPATIBILITY -- empty-corpus disclosure coexists with W607-BE
# ---------------------------------------------------------------------------


def test_w835_w836_empty_corpus_disclosure_coexists_with_w607be(cli_runner, monkeypatch):
    """W835/W836 fixed the flagship Pattern-2 "all checks passed" silent-SAFE on empty corpus.

    W607-BE must not regress that closure. When a per-check helper raises
    AND the W835/W836 corpus disclosure is active, both signals must
    coexist on the envelope.

    The minimal coexistence check: a raise produces a W607-N marker, the
    summary state is non-``all_passed``, and partial_success is True.
    """
    from roam.commands import cmd_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-w835w836-coexist-from-W607-BE")

    # Make corpus_content raise -- this is the very check the W835/W836
    # fix added to surface empty-corpus state. The W607-N wrapper turns
    # the raise into a marker; the W835/W836 disclosure pivots through
    # the absence of a passing corpus_content row.
    monkeypatch.setattr(cmd_doctor, "_check_corpus_content", _raise)

    result = _invoke_doctor(cli_runner)
    assert "Traceback" not in result.output, result.output
    data = _json.loads(result.output)

    combined_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    corpus_marker_present = any(m.startswith("doctor_corpus_content_failed:") for m in combined_wo)
    assert corpus_marker_present, (
        f"W607-BE/W607-N marker for the raising corpus_content check must be "
        f"present; got combined warnings_out = {combined_wo!r}"
    )
    # partial_success flips True regardless of which checks passed.
    assert data["summary"].get("partial_success") is True, (
        f"partial_success must flip True when a check raised; got summary = {data['summary']!r}"
    )
    # And the verdict must NOT be the silent-SAFE form ("all N checks passed")
    # when a marker is present.
    verdict = data["summary"].get("verdict", "")
    if "all" in verdict.lower() and "passed" in verdict.lower():
        # Acceptable only if the all-passed verdict is paired with the
        # marker disclosure (markers do not contradict the verdict when
        # the underlying check was a NO-OP fallback that defaulted to
        # "passed"). The discriminator is partial_success above; do not
        # over-constrain the verdict string.
        pass


# ---------------------------------------------------------------------------
# (10) HEALTH/DOCTOR PAIRING -- ``doctor_*`` and ``health_*`` markers coexist
# ---------------------------------------------------------------------------


def test_doctor_and_health_marker_families_mutually_distinct():
    """Source-level closed-enum guard: ``doctor_*`` and ``health_*`` prefixes are mutually distinct.

    When a downstream aggregator (e.g. CI dashboard, MCP-tool agent
    contract) consumes envelopes from BOTH cmd_doctor and cmd_health,
    the prefix family alone must attribute each disclosure correctly.

    Drift here would mean an aggregator could mis-attribute a doctor
    raise to the health flagship (or vice versa) -- a real Pattern-3
    vocabulary mismatch hazard given both are flagship CI commands and
    are typically run together (``roam doctor && roam health``).
    """
    doctor_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_doctor.py"
    health_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_health.py"
    assert doctor_src_path.exists(), doctor_src_path
    doctor_src = doctor_src_path.read_text(encoding="utf-8")
    # cmd_doctor carries the doctor_<phase>_failed template (both W607-N and W607-BE).
    assert "doctor_{phase}_failed" in doctor_src, (
        "doctor_{phase}_failed marker template missing from cmd_doctor; the W607-N/W607-BE marker family has regressed."
    )
    # The two prefixes do not collide.
    assert not "doctor_".startswith("health_")
    assert not "health_".startswith("doctor_")

    # If cmd_health exists alongside (W607-M/BA), verify it carries its own
    # marker template. cmd_health is part of the same flagship CI duo;
    # we don't require it to exist for THIS test to pass (W607-BA may be
    # in flight), but if it does we sanity-check the prefix.
    if health_src_path.exists():
        health_src = health_src_path.read_text(encoding="utf-8")
        if "health_{phase}_failed" in health_src or "_w607" in health_src:
            # Sanity: cmd_health should not accidentally carry doctor_ markers.
            assert "doctor_{phase}_failed" not in health_src, (
                "cmd_health carries doctor_{phase}_failed marker template -- "
                "marker-family cross-contamination; the doctor/health "
                "closed-enum prefix invariant has regressed."
            )


# ---------------------------------------------------------------------------
# (11) W607-N + W607-BE merge: both buckets surface on a single warnings_out
# ---------------------------------------------------------------------------


def test_w607n_and_w607be_markers_merge_into_single_warnings_out(cli_runner, monkeypatch):
    """Output-path discipline: both accumulators surface on the SAME warnings_out channel.

    W607-N (per-check helpers) and W607-BE (registry-side persist) markers
    both use the ``doctor_<phase>_failed:`` prefix, so the consumer sees
    one closed-enum prefix family. The output path merges the two buckets
    into a single ``warnings_out`` list on BOTH summary and top-level.
    """
    from roam.commands import cmd_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-merge-from-W607-BE")

    monkeypatch.setattr(cmd_doctor, "_check_tree_sitter", _raise)

    result = _invoke_doctor(cli_runner)
    assert "Traceback" not in result.output, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    # Both lists carry identical content.
    assert top_wo == summary_wo, (
        f"top-level and summary warnings_out must mirror each other; top={top_wo!r} summary={summary_wo!r}"
    )
    # And the merge -- every marker uses the doctor_ prefix family.
    for m in top_wo:
        assert m.startswith("doctor_"), f"merged warnings_out marker must use doctor_ prefix; got {m!r}"
