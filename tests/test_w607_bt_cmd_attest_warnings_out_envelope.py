"""W607-BT -- additive aggregation-phase plumbing for ``cmd_attest``.

cmd_attest is the proof-carrying PR attestation aggregator and the only
edit-loop command in the W607-* family that legitimately reaches
``risk_level "critical"`` (the composite-risk score >75/100 tier of
``_collect_risk``). With W607-BT landed, the full W631 risk-LEVEL
vocabulary range (``critical``/``high``/``medium``/``low``) is now
dual-bucket plumbed via:

  - substrate-CALL layer: W607-AD (11 phases)
  - aggregation-phase layer: W607-BT (5 phases)

Both layers share the canonical ``attest_*`` marker family and the
``attest_<phase>_failed:<exc_class>:<detail>`` shape contract. The
three buckets (``_attest_warnings_out`` unknown-status +
``_w607ad_warnings_out`` substrate-CALL + ``_w607bt_warnings_out``
aggregation-phase) are combined at envelope-emit time so consumers see
the full degradation lineage in marker-emission order.

Relation to W607-AD
-------------------

cmd_attest already carries W607-AD substrate-CALL plumbing covering 11
substrate-helper boundaries (get_changed_files / resolve_changed_to_db /
collect_blast_radius / collect_risk / collect_breaking / collect_fitness /
collect_budget / collect_tests / collect_effects / content_hash /
compute_verdict). W607-BT is ADDITIVE on top of W607-AD, extending
marker coverage to the AGGREGATION-PHASE boundaries that W607-AD left
unguarded:

  - ``score_classify``       -- per-factor classification of the internal
                                attest risk-LEVEL set (``LOW`` /
                                ``MODERATE`` / ``HIGH`` / ``CRITICAL``)
                                via ``_attest_risk_level``
  - ``severity_normalize``   -- canonical W631 risk-LEVEL projection
                                (``normalize_risk_level`` + ``risk_rank``)
                                -- CRITICAL-PATH instrumentation
  - ``compute_verdict``      -- augmented_verdict text build with the
                                canonical risk_level suffix (LAW 6)
                                via ``_make_verdict_str``
  - ``auto_log``             -- active-run ledger write
  - ``serialize_envelope``   -- ``json_envelope("attest", ...)`` projection

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_attest's aggregation-phase boundaries had no guards beyond the
W607-AD compute_verdict call. A downstream refactor that changes the
risk-level projection contract, the canonical W631 vocabulary, the
verdict string composition, the HMAC chain on the runs ledger, or the
``json_envelope`` shape would crash the envelope post-compute -- after
the substrate signals were already gathered, the agent loses the
result. W607-BT wraps each boundary with ``_run_check_bt`` so a raise
becomes a marker via ``warnings_out`` and the envelope still emits.

Score-classify degradation discipline
-------------------------------------

When the inner score_classify boundary raises (e.g. a refactored
``_attest_risk_level``), the wrap floors the classified tier to ``None``
and surfaces ``score_classification: "unknown"`` in the envelope summary
alongside the canonical W631 ``"low"`` floor on
``risk_level_canonical``. Mirror of cmd_diff W607-BP /
cmd_critique W607-BL severity_classification sentinel.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers -- invoke attest via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_attest(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam attest`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("attest")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture -- indexed corpus with an unstaged edit so attest reaches the
# collectors AND the aggregation-phase layer.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def attest_project(tmp_path, monkeypatch):
    """Indexed corpus with an unstaged modification so attest exercises every
    W607-BT aggregation-phase boundary (score_classify / severity_normalize /
    compute_verdict / auto_log / serialize_envelope).
    """
    proj = tmp_path / "attest_w607bt_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\n"
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n\ndef shout(msg):\n    return msg.upper()\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    # Unstaged edit so `roam attest` reaches the collector + aggregation path.
    (proj / "src" / "main.py").write_text(
        "def main():\n    helper()\n    return 2\n\n"
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    return proj


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BT aggregation markers
# ---------------------------------------------------------------------------


def test_attest_happy_path_no_w607bt_markers(cli_runner, attest_project):
    """Clean attest on a healthy corpus -> no W607-BT aggregation markers.

    Hash-stable: an empty W607-BT bucket on the success path must
    produce an envelope without any
    ``attest_score_classify_failed:`` /
    ``attest_severity_normalize_failed:`` /
    ``attest_compute_verdict_failed:`` /
    ``attest_auto_log_failed:`` /
    ``attest_serialize_envelope_failed:`` markers. Mirror of cmd_diff
    W607-BP discipline.
    """
    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "attest"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607bt_phases = (
        "attest_score_classify_failed:",
        "attest_severity_normalize_failed:",
        "attest_compute_verdict_failed:",
        "attest_auto_log_failed:",
        "attest_serialize_envelope_failed:",
    )
    for prefix in w607bt_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean attest must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_bt`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_attest_carries_w607bt_accumulator():
    """AST-level guard: cmd_attest source carries the W607-BT accumulator.

    Pins the canonical W607-BT anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AD) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_attest.py"
    assert src_path.exists(), f"cmd_attest.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607bt_warnings_out" in src, (
        "W607-BT accumulator missing from cmd_attest; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_bt" in src, (
        "W607-BT helper ``_run_check_bt`` missing from cmd_attest; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_bt is defined inside attest().
    tree = ast.parse(src)
    found_run_check_bt = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bt":
            found_run_check_bt = True
            break
    assert found_run_check_bt, (
        "W607-BT ``_run_check_bt`` helper not found in cmd_attest AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-AD must still be present (additive layer does NOT replace it)
    assert "_w607ad_warnings_out" in src, (
        "W607-AD accumulator vanished alongside the W607-BT add; the "
        "additive plumbing must preserve the W607-AD substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_bt():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_bt(...)`` with the canonical phase name.

    The five phases must appear inside a ``_run_check_bt("<phase>", ...)``
    call inside cmd_attest. Multi-indent variants are all considered
    valid wrap call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_attest.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "score_classify",
        "severity_normalize",
        "compute_verdict",
        "auto_log",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_bt(\n        "{phase}"',
            f'_run_check_bt(\n            "{phase}"',
            f'_run_check_bt(\n                "{phase}"',
            f'_run_check_bt(\n                    "{phase}"',
            f'_run_check_bt(\n                        "{phase}"',
            f'_run_check_bt("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_bt(...); add the W607-BT guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) Marker shape -- ``attest_<phase>_failed:<exc>:<detail>``
# ---------------------------------------------------------------------------


def test_auto_log_failure_marker_format(cli_runner, attest_project, monkeypatch):
    """If ``auto_log`` raises, surface ``attest_auto_log_failed:`` and keep
    the attest envelope intact.

    Discipline mirror of the W607-BP auto_log-failure pattern in
    cmd_diff. The auto_log boundary writes to the active run ledger
    when one is open -- a raise here would otherwise crash the envelope
    AFTER the success envelope was already built.
    """
    from roam.commands import cmd_attest

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-auto-log-from-W607-BT")

    monkeypatch.setattr(cmd_attest, "auto_log", _raise_auto_log)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("attest_auto_log_failed:")]
    assert markers, f"expected ``attest_auto_log_failed:`` marker; got {top_wo!r}"
    marker = markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-auto-log-from-W607-BT" in parts[2], parts

    # Envelope still emits the core attest fields
    for key in ("attestation", "evidence", "verdict"):
        assert key in data, (
            f"envelope must still emit ``{key}`` when auto_log raises; got keys = {sorted(data.keys())!r}"
        )


# ---------------------------------------------------------------------------
# (5) SCORE CLASSIFY DEGRADATION discipline
# ---------------------------------------------------------------------------


def test_score_classify_degradation_surfaces_unknown_sentinel(cli_runner, attest_project, monkeypatch):
    """When the score_classify boundary raises:

    1. Marker ``attest_score_classify_failed:`` appears
    2. Envelope still emits the core attest signal blocks
    3. Summary stamps ``score_classification: "unknown"`` sentinel
    4. Summary still carries the canonical floor ``risk_level_canonical: "low"``

    The underlying action (emit the attest envelope) stays -- degraded
    outcomes are valid design. The LIE we prevent is a clean classified
    verdict when score_classify actually raised. Mirror of cmd_diff's
    severity_classify degradation pattern (W607-BP).
    """
    from roam.commands import cmd_attest

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-score-classify-from-W607-BT")

    monkeypatch.setattr(cmd_attest, "_attest_risk_level", _raise)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # (1) marker appears -- W607-BT score_classify
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("attest_score_classify_failed:")]
    assert markers, f"expected ``attest_score_classify_failed:`` marker; got {top_wo!r}"

    # (2) envelope still emits the attest signal blocks
    summary = data["summary"]
    for key in ("attestation", "evidence", "verdict"):
        assert key in data, (
            f"envelope must still emit ``{key}`` even when score_classify raises; got keys = {sorted(data.keys())!r}"
        )

    # (3) score_classification sentinel
    assert summary.get("score_classification") == "unknown", (
        f'summary must stamp ``score_classification: "unknown"`` when '
        f"score_classify raises; got "
        f"{summary.get('score_classification')!r}"
    )

    # (4) canonical floor still emitted
    assert summary.get("risk_level_canonical") == "low", (
        f'summary must floor ``risk_level_canonical`` to ``"low"`` on '
        f"score_classify raise; got {summary.get('risk_level_canonical')!r}"
    )


def test_score_classify_clean_path_stamps_classified(cli_runner, attest_project):
    """Happy path: ``score_classification`` summary field is ``"classified"``.

    Mirror of the W607-BP discipline that the sentinel disambiguates a
    real classified verdict from a degraded "unknown" floor.
    """
    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("score_classification") == "classified", (
        f'clean path must stamp ``score_classification: "classified"``; '
        f"got {data['summary'].get('score_classification')!r}"
    )


# ---------------------------------------------------------------------------
# (6) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, attest_project, monkeypatch):
    """ANY W607-BT or W607-AD marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    attest" from "attest ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_attest

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-BT")

    monkeypatch.setattr(cmd_attest, "auto_log", _raise_auto_log)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-BT warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607bt_warnings_out_in_both_top_and_summary(cli_runner, attest_project, monkeypatch):
    """Non-empty W607-BT bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-BP / W607-BL contract: top-level is needed
    because the preserved-list field survives ``strip_list_payloads`` in
    default-detail mode; summary mirror gives consumers reading only the
    summary block visibility too.
    """
    from roam.commands import cmd_attest

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BT")

    monkeypatch.setattr(cmd_attest, "auto_log", _raise_auto_log)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BT raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BT raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("attest_auto_log_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("attest_auto_log_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the auto_log marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) W607-AD COEXISTENCE -- both buckets surface in combined envelope
# ---------------------------------------------------------------------------


def test_combined_w607ad_and_w607bt_markers_both_surface(cli_runner, attest_project, monkeypatch):
    """W607-AD and W607-BT markers BOTH surface when raises occur on each
    layer simultaneously.

    The additive plumbing must not shadow the W607-AD bucket -- agents
    must see the full degradation lineage in marker-emission order.
    Mirror of cmd_diff's W607-Z + W607-BP combined test (regression
    guard ensuring the pre-existing W607-AD layer survives the additive
    W607-BT plumbing).
    """
    from roam.commands import cmd_attest

    def _raise_collect_blast(*a, **kw):
        # W607-AD substrate boundary
        raise RuntimeError("synthetic-blast-from-W607-BT-combined")

    def _raise_auto_log(*a, **kw):
        # W607-BT aggregation boundary
        raise RuntimeError("synthetic-auto-log-from-W607-BT-combined")

    monkeypatch.setattr(cmd_attest, "_collect_blast_radius", _raise_collect_blast)
    monkeypatch.setattr(cmd_attest, "auto_log", _raise_auto_log)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    ad_markers = [m for m in top_wo if m.startswith("attest_collect_blast_radius_failed:")]
    bt_markers = [m for m in top_wo if m.startswith("attest_auto_log_failed:")]
    assert ad_markers, f"W607-AD collect_blast_radius marker missing; got {top_wo!r}"
    assert bt_markers, f"W607-BT auto_log marker missing; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-BT uses the SAME ``attest_*`` family
# ---------------------------------------------------------------------------


def test_w607bt_marker_prefix_attest_family(cli_runner, attest_project, monkeypatch):
    """W607-BT markers use the canonical ``attest_*`` prefix (same family
    as W607-AD; W607-BT is ADDITIVE, not a separate prefix).

    Hard guard: any W607-BT marker that leaks into a sibling W607-*
    family (e.g. ``preflight_*`` / ``impact_*`` / ``diagnose_*`` /
    ``critique_*`` / ``diff_*``) breaks the closed-enum marker-family
    contract pinned in the W607-AD test.
    """
    from roam.commands import cmd_attest

    def _raise_auto_log(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BT")

    monkeypatch.setattr(cmd_attest, "auto_log", _raise_auto_log)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-discipline check"
    for marker in top_wo:
        assert marker.startswith("attest_"), f"every W607-BT marker must use the ``attest_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (10) CROSS-PREFIX ISOLATION -- attest_* markers DO NOT leak into sibling
# commands (cmd_cga, cmd_pr_bundle)
# ---------------------------------------------------------------------------


def test_attest_markers_do_not_leak_into_adjacent_commands(cli_runner, attest_project, monkeypatch):
    """``attest_*`` markers must NOT appear in ``cmd_cga`` /
    ``cmd_pr_bundle`` envelopes when those commands raise.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels. Mirror of cmd_diff's W607-BP /
    cmd_critique's W607-BL prefix-isolation discipline.
    """
    from roam.commands import cmd_attest

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation-from-W607-BT")

    monkeypatch.setattr(cmd_attest, "auto_log", _raise_auto_log)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    assert all_markers, "expected non-empty warnings_out for prefix-isolation check"

    # Every marker must start with attest_ -- foreign-family leakage is a bug
    foreign_prefixes = (
        "cga_",
        "pr_bundle_",
        "preflight_",
        "impact_",
        "diagnose_",
        "critique_",
        "diff_",
    )
    for marker in all_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_attest warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (11) Canonical risk-LEVEL emission -- top-level + summary mirror
# ---------------------------------------------------------------------------


def test_canonical_risk_level_emitted_on_success_path(cli_runner, attest_project):
    """Success path emits ``risk_level_canonical`` + ``risk_rank`` on
    BOTH top-level envelope AND summary.

    Mirror of cmd_diff's W607-BP / cmd_critique's W607-BL canonical-emit
    pattern. Cross-command consumers can call
    ``risk_rank(data["summary"]["risk_level_canonical"]) >= 3`` to gate
    on high-or-worse without re-deriving the threshold table at the
    call site (Pattern-3a).
    """
    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Summary mirror
    summary = data["summary"]
    assert "risk_level_canonical" in summary, (
        f"summary must emit ``risk_level_canonical``; got summary = {sorted(summary.keys())!r}"
    )
    assert "risk_rank" in summary, f"summary must emit ``risk_rank``; got summary = {sorted(summary.keys())!r}"
    assert summary["risk_level_canonical"] in (
        "critical",
        "high",
        "medium",
        "low",
    ), f"summary.risk_level_canonical must be in canonical W631 set; got {summary['risk_level_canonical']!r}"

    # Top-level mirror
    assert "risk_level_canonical" in data, (
        f"top-level envelope must emit ``risk_level_canonical``; got keys = {sorted(data.keys())!r}"
    )
    assert "risk_rank" in data, f"top-level envelope must emit ``risk_rank``; got keys = {sorted(data.keys())!r}"

    # Verdict suffix carries the canonical bucket per LAW 6
    assert f"risk_level {summary['risk_level_canonical']}" in summary["verdict"], (
        f"verdict must carry the canonical risk_level bucket per LAW 6; got verdict = {summary['verdict']!r}"
    )


# ---------------------------------------------------------------------------
# (12) Serialize envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607bt_serialize_envelope_floor_on_raise(cli_runner, attest_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``attest_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("attest", ...)`` would otherwise crash AFTER all
    substrate + aggregation signals were already gathered. The consumer
    must still receive a parseable JSON object with the marker attached
    + the canonical command name.
    """
    from roam.commands import cmd_attest

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-BT")

    monkeypatch.setattr(cmd_attest, "json_envelope", _raise_envelope)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "attest", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("attest_serialize_envelope_failed:")]
    assert markers, f"expected ``attest_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (13) Compute-verdict guard -- raise floors to a stable verdict
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, attest_project, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    We force the compute_verdict closure to raise by patching
    ``normalize_risk_level`` to return an object whose ``__format__``
    raises -- the verdict f-string interpolation of risk_level_canonical
    then trips the wrap inside ``_make_verdict_str``. Same approach as
    cmd_diff's test_compute_verdict_failure_marker_format adapted to
    cmd_attest's call site.

    W978 first-hypothesis check: the canonical floor MUST NOT
    re-interpolate the same value that raised on the BadLevel sentinel
    test -- the floor is a literal string.
    """
    from roam.commands import cmd_attest

    class _BadLevel:
        def __str__(self):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BT")

        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-from-W607-BT")

    def _bad_normalize(level):
        return _BadLevel()

    monkeypatch.setattr(cmd_attest, "normalize_risk_level", _bad_normalize)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("attest_compute_verdict_failed:")]
    assert markers, f"expected ``attest_compute_verdict_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (14) CRITICAL-LEVEL path exercise -- the only command in the W607-* family
# that legitimately reaches ``risk_level "critical"``
# ---------------------------------------------------------------------------


def test_critical_level_path_emits_canonical_critical(cli_runner, attest_project, monkeypatch):
    """When ``_collect_risk`` returns a HIGH-tier composite-risk score
    (>75/100), the W631 projection emits ``risk_level_canonical: "critical"``.

    cmd_attest is the only command in the W607-* family that legitimately
    reaches ``risk_level "critical"`` (the composite-risk score >75/100
    tier of ``_collect_risk``). Per the W641-followup-D
    conservative-on-critical discipline: unlike critique / impact which
    saturate at ``high``, attest's composite IS allowed to reach
    ``critical``. The W607-BT severity_normalize boundary must preserve
    that escalation through the projection.

    This validates the FULL W631 risk-LEVEL vocabulary range
    (``critical``/``high``/``medium``/``low``) is now dual-bucket plumbed
    through cmd_attest's aggregation-phase layer.
    """
    from roam.commands import cmd_attest

    # Patch ``_collect_risk`` to return a CRITICAL-tier dict directly.
    # ``_attest_risk_level`` will normalize "CRITICAL" -> canonical
    # "critical" via the RISK_ALIASES table.
    def _critical_risk(*args, **kwargs):
        return {"score": 92, "level": "CRITICAL"}

    monkeypatch.setattr(cmd_attest, "_collect_risk", _critical_risk)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]

    # The W631 projection must NOT saturate at "high" -- attest's
    # composite legitimately reaches "critical".
    assert summary.get("risk_level_canonical") == "critical", (
        f'CRITICAL-LEVEL path must emit ``risk_level_canonical: "critical"``; '
        f"got {summary.get('risk_level_canonical')!r} "
        f'(saturation at "high" would be a regression of the '
        f"conservative-on-critical discipline)"
    )
    # risk_rank for "critical" is 4 (the top of the W631 vocabulary)
    assert summary.get("risk_rank") == 4, (
        f"CRITICAL-LEVEL path must emit ``risk_rank: 4`` (top of W631); got {summary.get('risk_rank')!r}"
    )

    # The domain-level risk_level (the internal 4-tier) must also reach
    # ``CRITICAL`` -- this is what the W631 projection preserves.
    assert summary.get("risk_level") == "CRITICAL", (
        f"domain risk_level must reach ``CRITICAL`` on this path; got {summary.get('risk_level')!r}"
    )

    # Verdict suffix carries the canonical "critical" bucket per LAW 6
    assert "risk_level critical" in summary["verdict"], (
        f"verdict must carry the canonical critical bucket per LAW 6; got verdict = {summary['verdict']!r}"
    )

    # Top-level mirror also escalates to critical
    assert data.get("risk_level_canonical") == "critical", (
        f"top-level envelope must mirror critical bucket; got {data.get('risk_level_canonical')!r}"
    )

    # Score_classification must still be "classified" (the boundary
    # didn't raise; CRITICAL is a valid classification)
    assert summary.get("score_classification") == "classified", (
        f"CRITICAL-LEVEL path must still stamp ``score_classification: "
        f'"classified"`` (the boundary didn\'t raise); '
        f"got {summary.get('score_classification')!r}"
    )


# ---------------------------------------------------------------------------
# (15) W607-AD COEXISTENCE GUARD -- substrate-CALL + aggregation-phase
# markers coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607ad_substrate_markers_coexist_with_w607bt_aggregation(cli_runner, attest_project, monkeypatch):
    """Confirm ``attest_<substrate-phase>_failed:`` markers (W607-AD
    layer) coexist with ``attest_<agg-phase>_failed:`` markers (W607-BT
    layer) -- both in same family, but threaded through different buckets
    at envelope-emit.

    This is the explicit guard requested by the W607-BT brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``attest_<substrate-phase>_failed:`` vs.
    ``attest_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_attest

    # W607-AD substrate boundary -- collect_breaking (one of 11 wrapped)
    def _raise_breaking(*a, **kw):
        raise RuntimeError("synthetic-ad-coexist-breaking")

    # W607-BT aggregation boundary -- auto_log
    def _raise_auto_log(*a, **kw):
        raise RuntimeError("synthetic-bt-coexist-auto-log")

    monkeypatch.setattr(cmd_attest, "_collect_breaking", _raise_breaking)
    monkeypatch.setattr(cmd_attest, "auto_log", _raise_auto_log)

    result = _invoke_attest(cli_runner, attest_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-AD
    ad_markers = [m for m in top_wo if m.startswith("attest_collect_breaking_failed:")]
    # Aggregation-phase from W607-BT
    bt_markers = [m for m in top_wo if m.startswith("attest_auto_log_failed:")]

    assert ad_markers, f"W607-AD substrate-CALL marker (attest_collect_breaking_failed) missing; got {top_wo!r}"
    assert bt_markers, f"W607-BT aggregation-phase marker (attest_auto_log_failed) missing; got {top_wo!r}"

    # Both share the canonical ``attest_*`` family
    assert all(m.startswith("attest_") for m in (ad_markers + bt_markers)), (
        f"all markers must share the canonical ``attest_*`` family; got ad = {ad_markers!r}, bt = {bt_markers!r}"
    )

    # Both surface in summary mirror too
    summary_wo = data["summary"].get("warnings_out") or []
    assert any(m.startswith("attest_collect_breaking_failed:") for m in summary_wo), (
        f"W607-AD marker missing from summary mirror; got {summary_wo!r}"
    )
    assert any(m.startswith("attest_auto_log_failed:") for m in summary_wo), (
        f"W607-BT marker missing from summary mirror; got {summary_wo!r}"
    )
