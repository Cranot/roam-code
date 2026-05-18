"""W607-DZ -- aggregation-phase plumbing role for ``cmd_taint``.

WAVE-AXIS FINDING
-----------------

W607-DZ on cmd_taint is **closed-as-duplicate-of-W607-CJ**. cmd_taint
already carries the canonical 4-phase aggregation-layer plumbing under
the W607-CJ namespace (landed prior to this wave). The W607-CJ layer
wraps the same four canonical aggregation phases that the W607-DX
template for cmd_missing_index uses:

  * ``score_classify``
  * ``compute_predicate``
  * ``compute_verdict``
  * ``serialize_envelope``

Introducing an additional ``_w607dz_warnings_out`` / ``_run_check_dz``
layer on top of cmd_taint's existing W607-CJ would:

  1. Triple-stack the aggregation wrap (substrate W607-AY +
     aggregation W607-CJ + redundant W607-DZ) for zero behavioural
     gain on the canonical 4 phases.
  2. Violate W978's 4th discipline (phase-name collision): the W607-CJ
     phase names ``score_classify`` / ``compute_predicate`` /
     ``compute_verdict`` / ``serialize_envelope`` would collide 1:1
     with any W607-DZ phase set. An agent reading
     ``taint_compute_verdict_failed:`` could not tell which layer
     raised.
  3. Confuse the security-axis cluster naming (cmd_taint AY+CJ /
     cmd_vulns AQ+CH / cmd_auth_gaps CM+pending) -- each command's
     letter pair stays disjoint across the cluster, and W607-DZ
     belongs to the next free letter-pair for a DIFFERENT command
     (not a third aggregation layer on cmd_taint).

This test file therefore PINS the aggregation-layer invariants on the
W607-CJ layer (the role cmd_missing_index calls "DX") and documents
the W607-DZ-on-cmd_taint axis as **closed**. Future agents picking up
the W607-DZ letter pair should target a DIFFERENT command (e.g. the
next consumer in the security cluster: cmd_vulns aggregation if not
yet present, or cmd_auth_gaps aggregation).

ROLE-MAP TABLE
--------------

For the security-axis cluster, the substrate-CALL + aggregation-phase
letter-pair roles are:

  * cmd_taint        -> AY (substrate) + CJ (aggregation)
  * cmd_vulns        -> AQ (substrate) + CH (aggregation)
  * cmd_vuln_reach   -> AU (substrate); aggregation pending
  * cmd_auth_gaps    -> CM (substrate); aggregation pending
  * cmd_sbom         -> AM (substrate) + CG (aggregation)
  * cmd_supply_chain -> AK (substrate) + CD (aggregation)
  * cmd_cga          -> BL (substrate) + BZ (aggregation)

W607-DZ on cmd_taint -> closed-as-duplicate-of-CJ.

REGRESSION INVARIANTS PRESERVED
-------------------------------

  * W826 -- HIGH-SEV silent-SAFE-on-empty-corpus seal (security-critical).
    Empty corpus MUST flip ``partial_success`` and name the absent state
    explicitly. Never emit a clean SAFE verdict on an unanalyzed
    corpus.
  * W493 -- CRITICAL correctness fix: edge ``kind='calls'`` (with the
    trailing ``s``) regression check (don't reintroduce the bare
    ``kind='call'`` typo that drops call edges).
  * W454 -- ``qualified_only`` lint flag preserved on the JSON
    envelope's ``rules_lint`` summary block.
  * W492 -- ``owasp_top10`` field preserved on each finding-dump entry
    so OpenVEX / OSCAL projections downstream can read it.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Canonical W607-DZ-role aggregation phases (the role W607-CJ plays for
# cmd_taint -- same 4 phase names cmd_missing_index W607-DX wraps).
# ---------------------------------------------------------------------------


_DZ_ROLE_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)


_TAINT_SRC = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_taint.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def taint_project(project_factory):
    """Small Python project with a source -> sink reach pattern.

    Mirrors the dogfood SQLi shape: an HTTP-style input bound to a
    cursor.execute() call. The bundled python-sqli rule pack will
    detect this path.
    """
    return project_factory(
        {
            "app.py": (
                "import flask\n"
                "from db import run_query\n"
                "def handler():\n"
                "    user = flask.request.args.get('x')\n"
                "    return run_query(user)\n"
            ),
            "db.py": (
                "import sqlite3\n"
                "def run_query(q):\n"
                "    conn = sqlite3.connect('x.db')\n"
                "    return conn.execute(q).fetchall()\n"
            ),
        }
    )


def _invoke_taint(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam taint`` against a project root via the top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("taint")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def _extract_json(output: str) -> dict:
    """Strip leading indexing chatter and parse the trailing JSON envelope."""
    first_brace = output.find("{")
    if first_brace == -1:
        raise ValueError(f"no JSON envelope in output: {output!r}")
    return _json.loads(output[first_brace:])


# ---------------------------------------------------------------------------
# (1) WAVE-AXIS FINDING -- W607-DZ accumulator is INTENTIONALLY ABSENT
# from cmd_taint (closed-as-duplicate-of-W607-CJ).
# ---------------------------------------------------------------------------


def test_w607dz_accumulator_absent_from_cmd_taint():
    """W607-DZ on cmd_taint is closed-as-duplicate-of-CJ.

    cmd_taint already carries W607-CJ as the canonical aggregation
    layer. Stacking an additional W607-DZ layer would:
      * triple-stack aggregation wrapping (substrate AY + agg CJ +
        redundant DZ) for zero behavioural gain,
      * violate W978 4th discipline (phase-name collision: DZ phases
        would collide 1:1 with CJ phases).

    This guard pins the absence so a future agent who incorrectly
    introduces W607-DZ on cmd_taint sees the test fail with context
    pointing them at the W607-CJ layer.
    """
    assert _TAINT_SRC.exists(), f"cmd_taint.py missing at {_TAINT_SRC}"
    src = _TAINT_SRC.read_text(encoding="utf-8")

    assert "_w607dz_warnings_out" not in src, (
        "W607-DZ accumulator unexpectedly present in cmd_taint. "
        "cmd_taint's aggregation layer is W607-CJ "
        "(``_w607cj_warnings_out`` + ``_run_check_cj``); W607-DZ on "
        "cmd_taint is closed-as-duplicate-of-CJ. If you intended to "
        "add a third aggregation layer, you must rename one set of "
        "phases to avoid W978 4th-discipline collision -- but "
        "preferred path is NOT to add the layer (it adds plumbing "
        "with zero behavioural gain)."
    )
    assert "_run_check_dz" not in src, (
        "W607-DZ helper unexpectedly present in cmd_taint. "
        "cmd_taint's aggregation helper is ``_run_check_cj``; "
        "W607-DZ-on-cmd_taint is closed-as-duplicate-of-CJ."
    )


# ---------------------------------------------------------------------------
# (2) CANONICAL AGGREGATION LAYER -- W607-CJ plays the W607-DZ role
# for cmd_taint. Pin its presence.
# ---------------------------------------------------------------------------


def test_cmd_taint_aggregation_layer_is_w607cj():
    """The aggregation-layer role for cmd_taint is W607-CJ.

    Pins the structural anchor: ``_w607cj_warnings_out`` accumulator,
    ``_run_check_cj`` helper, and the W607-AY substrate-CALL layer
    coexisting below it. A regression that removes the CJ layer
    silently demotes cmd_taint to substrate-only coverage.
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")

    assert "_w607cj_warnings_out" in src, (
        "W607-CJ accumulator missing from cmd_taint; the aggregation-"
        "layer role for cmd_taint has regressed. The CJ layer is the "
        "canonical aggregation surface (the role cmd_missing_index "
        "calls 'DX'); removing it leaves cmd_taint with substrate-"
        "only (AY) coverage."
    )
    assert "_run_check_cj" in src, "W607-CJ helper ``_run_check_cj`` missing from cmd_taint."
    assert "_w607ay_warnings_out" in src, "W607-AY substrate-CALL accumulator missing from cmd_taint."
    assert "_run_check_ay" in src, "W607-AY helper ``_run_check_ay`` missing from cmd_taint."


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every canonical aggregation phase is wrapped
# (under the W607-CJ layer, since that plays the DZ role).
# ---------------------------------------------------------------------------


def test_every_dz_role_phase_wrapped_in_run_check_cj():
    """Every canonical W607-DZ-role aggregation phase calls
    ``_run_check_cj(...)`` with the canonical phase name.

    The 4 phases ``score_classify`` / ``compute_predicate`` /
    ``compute_verdict`` / ``serialize_envelope`` are the canonical
    aggregation boundaries that cmd_missing_index W607-DX wraps. In
    cmd_taint they are wrapped under W607-CJ (the layer that plays
    the W607-DZ role).
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")

    for phase in _DZ_ROLE_PHASES:
        same_line = f'_run_check_cj("{phase}"' in src
        multi_line = any(f'_run_check_cj(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"taint_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DZ-role wrap missing for phase {phase!r} on cmd_taint; "
            f"the canonical aggregation boundary is no longer caught. "
            f"cmd_taint's aggregation layer is W607-CJ."
        )


# ---------------------------------------------------------------------------
# (4) Per-phase isolation -- score_classify raise surfaces marker + floors
# ---------------------------------------------------------------------------


def test_score_classify_isolation_marker_and_floor(cli_runner, taint_project, monkeypatch):
    """Per-phase isolation: a raise inside the score_classify boundary
    surfaces ``taint_score_classify_failed:`` and floors to zero-count
    metrics rather than crashing the envelope.

    Strategy: patch ``run_taint`` to return a finding whose
    ``.severity`` getter raises. The score_classify closure trips on
    the first ``f.severity`` access; the W607-CJ wrap catches the
    raise and the floor ``{"high_count": 0, ...}`` lets the envelope
    finish composing.
    """
    from roam.commands import cmd_taint as _mod

    class _BadFinding:
        @property
        def severity(self):
            raise RuntimeError("synthetic-dz-role-score-classify")

        @property
        def sanitizer_in_path(self):
            return False

        path_truncated = False
        rule_id = "synthetic"
        cwe = None
        owasp_top10 = ""
        source_symbol = {}
        sink_symbol = {}
        path_symbols = []

    monkeypatch.setattr(_mod, "run_taint", lambda *a, **kw: [_BadFinding()])

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("taint_score_classify_failed:")]
    assert markers, (
        f"expected ``taint_score_classify_failed:`` marker after "
        f"poisoning ``run_taint`` to return a finding whose .severity "
        f"raises; got {all_wo!r}"
    )
    # Floor must surface zero counts so downstream verdict + envelope
    # don't crash.
    summary = data["summary"]
    assert summary.get("errors", -1) == 0, f"score_classify floor must zero ``errors``; got {summary!r}"
    assert summary.get("risk_score", -1) == 0, f"score_classify floor must zero ``risk_score``; got {summary!r}"


# ---------------------------------------------------------------------------
# (5) Per-phase isolation -- compute_predicate floor dict shape
# ---------------------------------------------------------------------------


def test_compute_predicate_floor_dict_shape():
    """W978 6th-discipline: compute_predicate floor MUST be a concrete
    dict carrying all 6 documented keys (rules / findings / errors /
    warnings / sanitized / risk_score), NOT a sentinel that may
    __len__-raise downstream.
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_predicate_floor = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cj"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "compute_predicate"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            assert isinstance(kw.value, ast.Dict), (
                f"compute_predicate default= must be a literal dict; got {type(kw.value).__name__!r}"
            )
            keys_present = set()
            for k in kw.value.keys:
                if isinstance(k, ast.Constant):
                    keys_present.add(k.value)
            expected_keys = {
                "rules",
                "findings",
                "errors",
                "warnings",
                "sanitized",
                "risk_score",
            }
            missing = expected_keys - keys_present
            assert not missing, (
                f"compute_predicate floor dict missing keys {missing!r}; "
                f"floor shape must mirror the happy-path return so "
                f"downstream consumers see a consistent envelope."
            )
            found_predicate_floor = True
            break

    assert found_predicate_floor, (
        "compute_predicate _run_check_cj call site not found in cmd_taint; "
        "the aggregation boundary has been refactored away."
    )


# ---------------------------------------------------------------------------
# (6) Per-phase isolation -- compute_verdict floor is the literal
# "Taint analysis completed" string (W978 first-hypothesis discipline)
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """W978 first-hypothesis discipline: compute_verdict floor must be
    a literal string, NOT an f-string re-interpolating the values that
    just raised. Canonical floor for cmd_taint is
    ``"Taint analysis completed"``.
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")

    assert 'default="Taint analysis completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-CJ "
        "discipline; the canonical floor literal 'Taint analysis "
        "completed' is missing from cmd_taint.py"
    )


# ---------------------------------------------------------------------------
# (7) Per-phase isolation -- serialize_envelope raise -> marker + stub
# ---------------------------------------------------------------------------


def test_serialize_envelope_isolation_marker_and_stub(cli_runner, taint_project, monkeypatch):
    """If the serialize_envelope boundary (json_envelope) raises, the
    wrap floors to a parseable stub document carrying the canonical
    command name and surfaces the ``taint_serialize_envelope_failed:``
    marker.
    """
    from roam.commands import cmd_taint as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-dz-role-serialize-envelope")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data.get("command") == "taint", f"envelope stub must carry the canonical command name on raise; got {data!r}"
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("taint_serialize_envelope_failed:")]
    assert markers, f"expected ``taint_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (8) Substrate (W607-AY) + aggregation (W607-CJ) coexistence -- BOTH
# layer markers surface when BOTH layers fault on the same invocation.
# ---------------------------------------------------------------------------


def test_w607ay_substrate_and_w607cj_aggregation_coexist(cli_runner, taint_project, monkeypatch):
    """When BOTH layers fault, BOTH layer markers surface.

    This is the security-axis 1st-leg closure check: cmd_taint is the
    dataflow-reach leg of the security-reachability triad. With both
    layers landed (AY substrate + CJ aggregation), a single invocation
    on a workspace that faults at both layers must surface markers
    from BOTH buckets in the same ``warnings_out`` channel.
    """
    from roam.commands import cmd_taint as _mod

    def _raise_run_taint(*a, **kw):
        raise RuntimeError("synthetic-ay-coexist-run-taint")

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-cj-coexist-envelope")

    monkeypatch.setattr(_mod, "run_taint", _raise_run_taint)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    ay_markers = [m for m in top_wo if m.startswith("taint_run_taint_failed:")]
    cj_markers = [m for m in top_wo if m.startswith("taint_serialize_envelope_failed:")]

    assert ay_markers, f"W607-AY substrate-CALL marker (taint_run_taint_failed) missing; got {top_wo!r}"
    assert cj_markers, f"W607-CJ aggregation-phase marker (taint_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``taint_*`` family
    assert all(m.startswith("taint_") for m in (ay_markers + cj_markers)), (
        f"all markers must share the canonical ``taint_*`` family; got ay = {ay_markers!r}, cj = {cj_markers!r}"
    )


# ---------------------------------------------------------------------------
# (9) ANY aggregation marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_aggregation_marker_flips_partial_success(cli_runner, taint_project, monkeypatch):
    """ANY W607-CJ aggregation marker must flip
    summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    taint" from "taint ran with aggregation degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_taint as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-dz-role-partial-success")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CJ warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (10) warnings_out mirrors -- both top-level AND summary populated
# ---------------------------------------------------------------------------


def test_warnings_out_in_both_top_and_summary(cli_runner, taint_project, monkeypatch):
    """Non-empty W607-CJ bucket -> both top-level AND summary.warnings_out
    populated.
    """
    from roam.commands import cmd_taint as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-dz-role-mirror")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), f"top-level warnings_out missing; keys = {sorted(data.keys())!r}"
    assert data["summary"].get("warnings_out"), f"summary.warnings_out missing; summary = {data['summary']!r}"

    top_markers = [m for m in data["warnings_out"] if m.startswith("taint_serialize_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("taint_serialize_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (11) Cross-prefix isolation -- taint_* markers do NOT leak into
# sibling W607-* families (vulns_* / auth_gaps_* / sbom_*).
# ---------------------------------------------------------------------------


def test_taint_markers_do_not_leak_into_sibling_families(cli_runner, taint_project, monkeypatch):
    """``taint_*`` markers must NOT appear with foreign prefixes when
    cmd_taint raises. Specifically validates the security-axis cluster
    cross-prefix isolation: cmd_taint vs cmd_vulns (AQ/CH) vs
    cmd_auth_gaps (CM) vs cmd_sbom (AM/CG) vs cmd_supply_chain (AK/CD).
    """
    from roam.commands import cmd_taint as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-cross-prefix-from-dz-role")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for cross-prefix check"

    foreign_prefixes = (
        # Security-axis siblings (the critical cluster to keep distinct)
        ("vulns_", "cmd_vulns W607-AQ/CH (catalog-ingestion sibling)"),
        ("vuln_reach_", "cmd_vuln_reach W607-AU (call-graph reach sibling)"),
        ("auth_gaps_", "cmd_auth_gaps W607-CM (auth detector sibling)"),
        ("sbom_", "cmd_sbom W607-AM/CG (SBOM emit sibling)"),
        ("supply_chain_", "cmd_supply_chain W607-AK/CD (supply-chain sibling)"),
        # Other adjacent commands
        ("cga_", "cmd_cga W607-BL/BZ (attestation sibling)"),
        ("attest_", "cmd_attest (attestation sibling)"),
        ("pr_bundle_", "cmd_pr_bundle"),
        ("preflight_", "cmd_preflight"),
        ("impact_", "cmd_impact"),
        ("diagnose_", "cmd_diagnose"),
        ("critique_", "cmd_critique"),
        ("diff_", "cmd_diff"),
        # ORM-detector cluster siblings (must not leak into taint either)
        ("n1_", "cmd_n1 W607-CB/DQ (sibling ORM detector)"),
        ("over_fetch_", "cmd_over_fetch W607-CE/DT (sibling ORM detector)"),
        ("missing_index_", "cmd_missing_index W607-CI/DX (sibling ORM detector)"),
    )
    for marker in failure_markers:
        # Every marker must use the canonical taint_* family.
        assert marker.startswith("taint_"), (
            f"every cmd_taint W607 marker must use the ``taint_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in foreign_prefixes:
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) W826 REGRESSION GUARD -- empty corpus is NOT silent SAFE
# (security-critical; the HIGH-SEV seal must not regress under any
# aggregation-layer plumbing).
# ---------------------------------------------------------------------------


def test_w826_empty_corpus_not_silent_safe_under_dz_role(cli_runner, tmp_path, monkeypatch):
    """W826 regression guard (security-critical).

    W826 sealed a HIGH-SEV bug: cmd_taint silent-SAFE on empty corpus.
    The aggregation-layer plumbing (W607-CJ playing the DZ role) MUST
    NOT re-introduce a Pattern-2 silent-SAFE bug -- a regression here
    is security-critical.

    Strategy: create a tmp project with NO source files. cmd_taint's
    ``query_symbol_count`` substrate returns 0; the empty-corpus
    branch fires. The verdict must name the absent state explicitly
    and either flip ``partial_success`` OR explicitly disclaim
    SAFE-ness in the verdict text.
    """
    import subprocess

    (tmp_path / ".gitignore").write_text(".roam/\n")
    subprocess.run(
        ["git", "init", "-q"],
        cwd=str(tmp_path),
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(tmp_path),
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(tmp_path),
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )

    result = _invoke_taint(cli_runner, tmp_path)
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    verdict = data["summary"]["verdict"]
    state = data["summary"].get("state", "")

    # Pattern-2 contract: name the absent state explicitly
    explicit_marker = "empty_corpus" in state or "no symbols" in verdict.lower() or "no rules" in verdict.lower()
    assert explicit_marker, (
        f"W826 regression: empty-corpus / no-rules verdict must name "
        f"the absent state explicitly (not silent-SAFE); got "
        f"verdict={verdict!r}, state={state!r}"
    )


# ---------------------------------------------------------------------------
# (13) W493 REGRESSION GUARD -- kind='calls' (plural) NOT kind='call'
# (CRITICAL correctness fix preserved)
# ---------------------------------------------------------------------------


def test_w493_kind_calls_typo_not_reintroduced():
    """W493 sealed a CRITICAL correctness bug: cmd_taint's flow-shape
    classifier originally queried the edges table with the bare
    ``kind='call'`` literal -- but the indexer writes both ``call``
    and ``calls`` kinds, and the W493 fix routed the query through
    ``call_or_ref_in_clause()`` which accepts the full vocabulary.

    A regression that reintroduces the bare ``kind='call'`` literal
    (or any naked equality predicate over edges.kind that doesn't
    consult the call-or-ref vocabulary helper) would silently drop
    forward-BFS path validation for half the edge inventory --
    falsely tagging real BFS paths as ``co_call`` flow shape.
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")

    # The canonical helper must be invoked from the flow-shape
    # classifier (replaces the bare equality literal).
    assert "call_or_ref_in_clause()" in src, (
        "W493 regression: cmd_taint must consult "
        "``call_or_ref_in_clause()`` for flow-shape edge probing -- the "
        "bare ``kind = 'call'`` literal silently drops ``calls`` (and "
        "``reference`` etc.) edges from the BFS path validation."
    )

    # Defensive grep: no bare ``kind = 'call'`` equality literal in
    # the SQL. Allow ``call_or_ref_in_clause`` (function call) and
    # ``# kind = 'call'`` (comment) shapes.
    forbidden_patterns = (
        "WHERE kind = 'call'",
        "WHERE kind='call'",
        'kind = "call"',
        "kind='call'\"",
    )
    for pat in forbidden_patterns:
        assert pat not in src, (
            f"W493 regression: bare equality literal {pat!r} reintroduced "
            f"in cmd_taint.py -- use call_or_ref_in_clause() instead."
        )


# ---------------------------------------------------------------------------
# (14) W454 REGRESSION GUARD -- qualified_only lint flag preserved
# ---------------------------------------------------------------------------


def test_w454_qualified_only_flag_preserved(cli_runner, taint_project):
    """W454 added the ``qualified_only`` lint -- bare-name violations
    in YAML rules surface on the envelope's ``rules_lint`` summary
    block AND as ``qualified_only_violations`` on the envelope body
    when non-empty.

    The aggregation-layer plumbing must not drop this disclosure.
    """
    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    rules_lint = summary.get("rules_lint")
    assert rules_lint is not None, (
        f"W454 regression: ``rules_lint`` summary block missing on the JSON envelope; got summary = {summary!r}"
    )
    # rules_lint must carry both keys symmetrically per W1101/W1006
    assert "qualified_only_violations" in rules_lint, (
        f"W454 regression: ``qualified_only_violations`` count missing from rules_lint; got rules_lint = {rules_lint!r}"
    )
    assert "total_rules" in rules_lint, (
        f"W454 regression: ``total_rules`` count missing from rules_lint; got rules_lint = {rules_lint!r}"
    )


def test_w454_qualified_only_helper_imported_in_source():
    """W454 source-level guard: cmd_taint must import the canonical
    ``capture_qualified_only_lint`` helper from the hoisted
    ``roam.security.taint_rules_lint`` module (W489-A hoist).
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")
    assert "capture_qualified_only_lint" in src, (
        "W454 regression: ``capture_qualified_only_lint`` helper not "
        "imported / referenced in cmd_taint.py -- the qualified_only "
        "lint disclosure path has been refactored away."
    )


# ---------------------------------------------------------------------------
# (15) W492 REGRESSION GUARD -- owasp_top10 field preserved on findings
# ---------------------------------------------------------------------------


def test_w492_owasp_top10_field_preserved_in_findings_dump():
    """W492 added an ``owasp_top10`` field to each finding-dump entry
    (OpenVEX / OSCAL projection consumes it downstream). The
    aggregation-layer plumbing must not drop the field from the
    findings_dump shape.

    Source-grep: the score_classify closure that builds findings_dump
    must reference ``f.owasp_top10`` when projecting the per-finding
    dict.
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")
    assert '"owasp_top10": f.owasp_top10' in src, (
        "W492 regression: ``owasp_top10`` field not projected into "
        "findings_dump; consumers reading OpenVEX / OSCAL projections "
        "downstream will see the field disappear."
    )


def test_w492_owasp_top10_emitted_to_registry_evidence():
    """W492 source-level guard: the registry-emit path must carry
    ``owasp_top10`` into the evidence JSON so cross-detector consumers
    (roam findings) can filter / project on it.
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")
    assert '"owasp_top10"' in src, (
        "W492 regression: ``owasp_top10`` evidence field not emitted "
        "to the central findings registry; OpenVEX / OSCAL projections "
        "downstream will read empty."
    )


# ---------------------------------------------------------------------------
# (16) W978 KWARG-DEFAULT EAGERNESS TRAP -- floors are literal constants
# (AST audit against _run_check_cj call sites since CJ plays the DZ role)
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants_under_cj():
    """W978 kwarg-default audit on the W607-CJ layer (which plays the
    W607-DZ role for cmd_taint).

    Every ``_run_check_cj(...)`` ``default=`` MUST be a literal
    constant, NOT computed from upstream values. A computed default
    evaluates BEFORE the wrap call enters its try-block, so a raise
    inside the expression escapes the wrap.
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.Dict):
            return all(_is_literal(k) for k in node.keys if k is not None) and all(_is_literal(v) for v in node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_literal(e) for e in node.elts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _is_literal(node.operand)
        return False

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cj"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_cj(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_taint.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants."
    )


# ---------------------------------------------------------------------------
# (17) W978 5th-discipline -- closures call len() INSIDE, not at kwarg-bind
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_closures_not_at_kwarg_bind():
    """W978 5th-discipline: every ``len()`` call on a wrapped input
    MUST live INSIDE the wrapped closure, NOT at the
    ``_run_check_cj(...)`` call site as a positional or keyword
    argument expression.

    A ``_BadFindingList`` whose ``__len__`` raises would otherwise
    escape the try-block at kwarg-bind time.
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cj"):
            continue
        for sub in node.args:
            for descendant in ast.walk(sub):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call at "
                        f"_run_check_cj positional-arg site -- W978 "
                        f"5th-discipline violation"
                    )
        for kw in node.keywords:
            for descendant in ast.walk(kw.value):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call in "
                        f"_run_check_cj kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, "W978 5th-discipline violations in cmd_taint.py:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# (18) W978 4TH-DISCIPLINE -- phase-name collision check.
# This is the structural reason W607-DZ on cmd_taint is closed-as-
# duplicate-of-CJ: if a literal _run_check_dz layer were introduced
# alongside _run_check_cj, the 4 aggregation phase names would collide
# 1:1 and an agent reading ``taint_compute_verdict_failed:`` could
# not tell which layer raised.
# ---------------------------------------------------------------------------


def test_w978_phase_name_collision_would_block_dz_layer():
    """W978 4th-discipline: if W607-DZ were introduced on cmd_taint
    alongside W607-CJ, the canonical 4 aggregation phase names
    (``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
    ``serialize_envelope``) would collide 1:1. Marker prefix would be
    ambiguous.

    This test makes the structural blocker explicit: the W607-CJ
    layer ALREADY uses these 4 phase names, so any future W607-DZ
    layer MUST either pick a different phase set OR not exist. The
    preferred path is the latter (W607-DZ-on-cmd_taint is closed-as-
    duplicate-of-CJ).
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")

    # Confirm all 4 canonical aggregation phases are wrapped under
    # W607-CJ. A NEW _run_check_dz layer wrapping the same phases
    # would collide. We check by scanning the _run_check_cj call
    # sites' first-positional-arg phase-name literal (the marker
    # itself is built via an f-string ``taint_{phase}_failed:...``
    # so the literal ``taint_score_classify_failed`` won't appear
    # in source -- the literal phase name is the anchor).
    tree = ast.parse(src)
    cj_phases_found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cj"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            cj_phases_found.add(first.value)
    for phase in _DZ_ROLE_PHASES:
        assert phase in cj_phases_found, (
            f"Canonical aggregation phase {phase!r} not wrapped under "
            f"any _run_check_cj(...) call site in cmd_taint -- the "
            f"W607-CJ aggregation layer has regressed. Found phases: "
            f"{sorted(cj_phases_found)!r}"
        )

    # And confirm no W607-DZ counterpart exists (would be a collision).
    # The accumulator/helper names are the structural anchors; if they
    # appear in the same module, the layers collide.
    assert "_w607dz_warnings_out" not in src, (
        "W978 4th-discipline collision risk: _w607dz_warnings_out "
        "present alongside _w607cj_warnings_out in cmd_taint. Either "
        "rename one set's phase names OR remove the duplicate layer. "
        "W607-DZ-on-cmd_taint should be closed-as-duplicate-of-CJ."
    )


# ---------------------------------------------------------------------------
# (19) Helper-template ``return default`` verbatim shape -- W607-DW pin
# ---------------------------------------------------------------------------


def test_run_check_cj_helper_returns_default_verbatim():
    """W607-DW regression guard: the ``_run_check_cj`` helper body must
    end with ``return default`` (verbatim) -- NOT
    ``return default if default is not None else {}``.

    The W607-DW finding identified that an "improved" default-coerce
    return shape silently masks the floor literal -- e.g., a ``None``
    floor for a phase that legitimately returns ``None`` on success
    would get coerced to ``{}`` on raise, breaking caller assumptions.
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_helper = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_run_check_cj"):
            continue
        found_helper = True
        try_stmt = None
        for stmt in node.body:
            if isinstance(stmt, ast.Try):
                try_stmt = stmt
                break
        assert try_stmt is not None, (
            f"_run_check_cj body must contain a try/except block; got {[type(s).__name__ for s in node.body]!r}"
        )
        assert try_stmt.handlers, "_run_check_cj try-block must have at least one except-handler"
        last_handler = try_stmt.handlers[-1]
        last_stmt = last_handler.body[-1]
        assert isinstance(last_stmt, ast.Return), (
            f"_run_check_cj except-handler must end with a Return statement; got {type(last_stmt).__name__!r}"
        )
        assert isinstance(last_stmt.value, ast.Name), (
            f"_run_check_cj return value must be a bare ``default`` Name "
            f"node (W607-DW verbatim shape); got "
            f"{type(last_stmt.value).__name__!r} -- a conditional/IfExp "
            f"return masks the floor literal."
        )
        assert last_stmt.value.id == "default", (
            f"_run_check_cj return value must reference the ``default`` parameter; got Name(id={last_stmt.value.id!r})"
        )
        break

    assert found_helper, "_run_check_cj helper not found in cmd_taint AST"


# ---------------------------------------------------------------------------
# (20) SECURITY-AXIS 1ST-LEG CLOSURE PIN -- cmd_taint carries BOTH
# substrate-CALL (AY) AND aggregation-phase (CJ) plumbing. This is the
# 1st leg of the security cluster (taint -> vulns -> auth_gaps) closed
# at agg-layer.
# ---------------------------------------------------------------------------


def test_security_axis_1st_leg_closure_substrate_plus_aggregation():
    """Security-axis 1st-leg closure pin: cmd_taint carries BOTH the
    substrate-CALL accumulator (W607-AY) AND the aggregation-phase
    accumulator (W607-CJ -- playing the DZ role).

    A regression here means cmd_taint silently lost a layer -- a
    Pattern-2 hazard that downgrades the security-axis cluster.
    cmd_taint is the DATAFLOW-REACH leg of the security-reachability
    triad; both layers are required for the full degradation lineage
    to surface on the envelope.
    """
    src = _TAINT_SRC.read_text(encoding="utf-8")
    # W607-AY substrate-CALL family
    assert "_w607ay_warnings_out" in src, (
        "Security-axis 1st-leg regression: cmd_taint lost W607-AY substrate-CALL accumulator."
    )
    assert "_run_check_ay" in src, "Security-axis 1st-leg regression: cmd_taint lost W607-AY substrate-CALL helper."
    # W607-CJ aggregation-phase family (plays the W607-DZ role)
    assert "_w607cj_warnings_out" in src, (
        "Security-axis 1st-leg regression: cmd_taint lost W607-CJ aggregation-phase accumulator."
    )
    assert "_run_check_cj" in src, "Security-axis 1st-leg regression: cmd_taint lost W607-CJ aggregation-phase helper."
