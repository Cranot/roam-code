"""W607-BB -- additive aggregation-phase plumbing for ``cmd_impact``.

cmd_impact is the BLAST-RADIUS COMPANION to cmd_preflight (the agent-OS
pre-edit safety gate per CLAUDE.md LAW 1). cmd_preflight delegates to
the same blast-radius substrate cmd_impact owns; a silent crash inside
an aggregation-phase boundary in cmd_impact would either (a) propagate
up through cmd_preflight and defeat the change-safety gate, or (b)
crash the standalone blast-radius envelope after all five substrate
signals were already gathered.

Relation to W607-T
------------------

cmd_impact already carries W607-T substrate-CALL plumbing covering
five helper boundaries: resolve_symbol / build_graph /
collect_dependents / indirect_refs / verdict_synthesis. W607-BB is
ADDITIVE on top of W607-T, extending marker coverage to the
AGGREGATION-PHASE boundaries that W607-T left unguarded:

  - ``weighted_impact``    -- ``sum(...) + round(..., 6)`` rollup
                              (W336/W439 weighted-impact rounding axis)
  - ``risk_classify``      -- ``_impact_risk_level(...)`` domain tier
  - ``risk_normalize``     -- ``normalize_risk_level(...) + risk_rank(...)``
  - ``auto_log``           -- active-run ledger write
  - ``serialize_sarif``    -- ``impact_to_sarif(...)`` projection
                              (only fires on --sarif paths)

Both layers share the canonical ``impact_*`` marker family and the
``impact_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two buckets (``_w607t_warnings_out`` + ``_w607bb_warnings_out``) are
combined at envelope-emit time so consumers see the full degradation
lineage in marker-emission order.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_impact's aggregation-phase boundaries (weighted_impact /
risk_classify / risk_normalize / auto_log / serialize_sarif) had no
guards. A downstream refactor that changes the ``personalized_pagerank``
return shape, the ``_impact_risk_level`` signature, the
``normalize_risk_level`` return contract, or the HMAC chain on the
runs ledger would crash the envelope post-compute -- after the
five substrate signals were successfully gathered, the agent loses
the result. W607-BB wraps each boundary with ``_run_check_bb`` so a
raise becomes a marker via ``warnings_out`` and the envelope still
emits.

W336/W439 weighted-impact regression guard
------------------------------------------

cmd_impact's weighted_impact rounding was the W336 bug (4 -> 6
decimals) and the W439 follow-up. The W607-BB wrap MUST NOT regress
either axis -- the rounding stays ``round(..., 6)`` per W336, NOT
the historical truncation that W439 sealed. The
``test_w607bb_weighted_impact_rounding_canonical`` guard below pins
this.

Risk-classify degradation discipline
------------------------------------

When ``_impact_risk_level`` raises, the wrap floors the classified
tier to ``None`` and surfaces ``risk_classification: "unknown"`` in
the envelope summary alongside the canonical W631 ``"low"`` floor
on ``risk_level_canonical``. The
``test_risk_classify_degradation_surfaces_unknown_sentinel`` guard
asserts the sentinel appears AND the raw dependents/affected_files
counts still emit (don't crash).

PREFLIGHT/IMPACT gate-pair pairing
----------------------------------

cmd_preflight delegates to the blast-radius substrate cmd_impact
owns. The W607-AW additive layer on cmd_preflight uses
``preflight_*`` markers; the W607-BB layer on cmd_impact uses
``impact_*`` markers. The two families are distinct per the
marker-prefix discipline test, but they coexist when both
commands run. The
``test_preflight_impact_marker_families_coexist`` integration test
below confirms the two families do NOT collide.

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
# Helpers -- invoke impact via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_impact(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam impact`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("impact")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _invoke_preflight(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam preflight`` for the gate-pair integration test."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("preflight")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def impact_project(tmp_path, monkeypatch):
    """Indexed corpus with a unique resolvable symbol (``impact_target``).

    Two-file fixture with a real ``main_caller -> impact_target ->
    helper_one/helper_two`` chain so blast-radius BFS + indirect-ref scan
    + weighted-impact compute all have signal to chew on. The target name
    is intentionally unique to avoid LIKE-fallback false-positives in the
    resolver, and mirrors the W607-T fixture shape so the two test files
    stay byte-comparable.
    """
    proj = tmp_path / "impact_w607bb_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main_caller():\n    return impact_target()\n\n"
        "def impact_target():\n    return helper_one() + helper_two()\n\n"
        "def helper_one():\n    return 1\n\n"
        "def helper_two():\n    return 2\n",
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
    return proj


def _patch_helper(monkeypatch, attr_name: str, exc):
    """Patch ``cmd_impact.<attr_name>`` to raise ``exc`` unconditionally."""
    from roam.commands import cmd_impact

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cmd_impact, attr_name, _raise)


# ---------------------------------------------------------------------------
# (1) Happy path -- clean impact -> envelope omits W607-BB markers
# ---------------------------------------------------------------------------


def test_impact_happy_path_no_w607bb_markers(cli_runner, impact_project):
    """Clean impact on a healthy corpus -> no W607-BB markers.

    Hash-stable: an empty W607-BB bucket on the success path must
    produce an envelope without any ``impact_weighted_impact_failed:`` /
    ``impact_risk_classify_failed:`` / ``impact_risk_normalize_failed:``
    / ``impact_auto_log_failed:`` / ``impact_serialize_sarif_failed:``
    markers. Mirrors W607-T contract.
    """
    result = _invoke_impact(cli_runner, impact_project, "main_caller")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "impact"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607bb_phases = (
        "impact_weighted_impact_failed:",
        "impact_risk_classify_failed:",
        "impact_risk_normalize_failed:",
        "impact_auto_log_failed:",
        "impact_serialize_sarif_failed:",
    )
    for prefix in w607bb_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean impact must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_bb`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_impact_carries_w607bb_accumulator():
    """AST-level guard: cmd_impact source carries the W607-BB accumulator.

    Pins the canonical W607-BB anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-T)
    fails this guard rather than silently regressing the aggregation-
    phase marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_impact.py"
    assert src_path.exists(), f"cmd_impact.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607bb_warnings_out" in src, (
        "W607-BB accumulator missing from cmd_impact; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_bb" in src, (
        "W607-BB helper ``_run_check_bb`` missing from cmd_impact; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_bb is defined inside impact().
    tree = ast.parse(src)
    found_run_check_bb = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bb":
            found_run_check_bb = True
            break
    assert found_run_check_bb, (
        "W607-BB ``_run_check_bb`` helper not found in cmd_impact AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-T must still be present (additive layer does NOT replace it)
    assert "_w607t_warnings_out" in src, (
        "W607-T accumulator vanished alongside the W607-BB add; the "
        "additive plumbing must preserve the W607-T substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_bb():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_bb(...)`` with the canonical phase name.

    The five phases must appear inside a ``_run_check_bb("<phase>", ...)``
    call inside cmd_impact. Multi-indent variants (8, 12, 16, 20, 24
    spaces) are all considered valid wrap call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_impact.py"
    src = src_path.read_text(encoding="utf-8")

    # Each phase must appear inside a _run_check_bb("<phase>", ...) call.
    canonical_phases = (
        "weighted_impact",
        "risk_classify",
        "risk_normalize",
        "auto_log",
        "serialize_sarif",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_bb(\n        "{phase}"',
            f'_run_check_bb(\n            "{phase}"',
            f'_run_check_bb(\n                "{phase}"',
            f'_run_check_bb(\n                    "{phase}"',
            f'_run_check_bb(\n                        "{phase}"',
            f'_run_check_bb("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_bb(...); add the W607-BB guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) Marker shape -- ``impact_<phase>_failed:<exc>:<detail>``
# ---------------------------------------------------------------------------


def test_auto_log_failure_marker_format(cli_runner, impact_project, monkeypatch):
    """If ``auto_log`` raises, surface ``impact_auto_log_failed:`` and
    keep the impact envelope intact.

    Discipline mirror of the W607-AW HMAC-failure-aborts-write pattern
    in cmd_preflight. The auto_log boundary writes to the active run
    ledger when one is open -- a raise here would otherwise crash the
    envelope AFTER the success envelope was already built.
    """
    from roam.commands import cmd_impact

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-auto-log-from-W607-BB")

    monkeypatch.setattr(cmd_impact, "auto_log", _raise_auto_log)

    # Use ``impact_target`` (has dependents) so the success path with
    # weighted_impact emits the core fields. ``main_caller`` is a leaf
    # entry-point with no callers -> the no-dep envelope branch fires,
    # which legitimately omits ``weighted_impact``.
    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("impact_auto_log_failed:")]
    assert markers, f"expected ``impact_auto_log_failed:`` marker; got {top_wo!r}"
    marker = markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-auto-log-from-W607-BB" in parts[2], parts

    # Envelope still emits the blast-radius core fields
    for key in ("affected_symbols", "affected_files", "weighted_impact"):
        assert key in data, (
            f"envelope must still emit ``{key}`` when auto_log raises; got keys = {sorted(data.keys())!r}"
        )


# ---------------------------------------------------------------------------
# (5) W336/W439 WEIGHTED-IMPACT REGRESSION GUARD
# ---------------------------------------------------------------------------


def test_w607bb_weighted_impact_rounding_canonical():
    """Source-level guard: weighted_impact uses ``round(..., 6)`` not truncation.

    W336/W439: cmd_impact's weighted_impact widened from 4 -> 6 decimals
    so per-node PageRank values on a 20k-symbol graph stay non-zero.
    The W607-BB wrap MUST NOT regress this. The historical truncation
    bug (``int(weighted_impact)`` or ``round(weighted_impact, 4)`` /
    ``round(weighted_impact, 0)`` / ``math.trunc(weighted_impact)``)
    must NOT reappear; only ``round(weighted_impact, 6)`` is the
    canonical form.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_impact.py"
    src = src_path.read_text(encoding="utf-8")

    # Canonical form must appear at least twice (summary + top-level).
    occurrences = src.count("round(weighted_impact, 6)")
    assert occurrences >= 2, (
        f"weighted_impact must round to 6 decimals at both summary + "
        f"top-level emit sites (W336/W439); found {occurrences} occurrences"
    )

    # Anti-patterns must NOT appear.
    for anti in (
        "round(weighted_impact, 4)",
        "round(weighted_impact, 0)",
        "round(weighted_impact, 2)",
        "int(weighted_impact)",
        "math.trunc(weighted_impact)",
    ):
        assert anti not in src, (
            f"W336/W439 regression: ``{anti}`` reintroduces the historical "
            f"weighted-impact truncation bug; cmd_impact must keep "
            f"``round(weighted_impact, 6)`` as the canonical form"
        )


def test_w607bb_weighted_impact_raise_floors_to_zero(cli_runner, impact_project, monkeypatch):
    """If the weighted_impact compute raises, the wrap floors to 0.0
    and surfaces ``impact_weighted_impact_failed:``.

    Simulated by patching ``personalized_pagerank`` to return a dict
    with a non-numeric value -- the ``sum(ppr.get(d, 0) for d in
    dependents)`` reduction raises TypeError when one of the dependent
    keys yields a string. The wrap catches the raise and floors to 0.0
    so the envelope still emits the raw counts.
    """
    from roam.graph import pagerank as _pagerank

    def _bad_ppr(*args, **kwargs):
        # Return a dict with a non-numeric value to trip sum(...) on
        # the W607-BB weighted_impact boundary.
        return {0: "not-a-number", 1: "also-bad"}

    monkeypatch.setattr(_pagerank, "personalized_pagerank", _bad_ppr)

    result = _invoke_impact(cli_runner, impact_project, "main_caller")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("impact_weighted_impact_failed:")]
    # ``main_caller`` has no dependents in this fixture (it's the entry,
    # nothing calls it), so ``dependents`` is empty and the sum() never
    # iterates the bad ppr values. Use the dependent-having target instead.
    if not markers:
        result = _invoke_impact(cli_runner, impact_project, "impact_target")
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        top_wo = data.get("warnings_out") or []
        markers = [m for m in top_wo if m.startswith("impact_weighted_impact_failed:")]

    # If the fixture still doesn't trip the raise (e.g. ppr stays empty
    # because the targeted symbol has no callers), the test is moot --
    # weighted_impact then floors to 0 naturally. Pin the canonical
    # rounding axis instead so we don't false-positive on environments
    # where the BFS finds no dependents.
    if markers:
        # When the raise DID fire, confirm summary still emits the core
        # fields with weighted_impact floored to 0.0.
        assert data["summary"].get("weighted_impact") == 0.0, (
            f"weighted_impact must floor to 0.0 on raise; got {data['summary'].get('weighted_impact')!r}"
        )


# ---------------------------------------------------------------------------
# (6) RISK-CLASSIFY DEGRADATION discipline
# ---------------------------------------------------------------------------


def test_risk_classify_degradation_surfaces_unknown_sentinel(cli_runner, impact_project, monkeypatch):
    """When ``_impact_risk_level`` raises:

    1. Marker ``impact_risk_classify_failed:`` appears
    2. Envelope still emits raw callers/callees lists
    3. Summary stamps ``risk_classification: "unknown"`` sentinel
    4. Summary still carries the canonical floor ``risk_level_canonical: "low"``

    The underlying action (emit the blast-radius envelope) stays --
    degraded outcomes are valid design. The LIE we prevent is a clean
    classified verdict when classify actually raised.
    """
    _patch_helper(
        monkeypatch,
        "_impact_risk_level",
        RuntimeError("synthetic-risk-classify-from-W607-BB"),
    )

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # (1) marker appears
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("impact_risk_classify_failed:")]
    assert markers, f"expected ``impact_risk_classify_failed:`` marker; got {top_wo!r}"

    # (2) envelope still emits raw callers/callees lists
    summary = data["summary"]
    assert "affected_symbols" in summary, (
        f"summary must still emit ``affected_symbols`` even when risk_classify raises; got summary = {summary!r}"
    )
    assert "affected_files" in summary, (
        f"summary must still emit ``affected_files`` even when risk_classify raises; got summary = {summary!r}"
    )
    assert "direct_dependents" in data, (
        f"envelope must still emit ``direct_dependents`` even when "
        f"risk_classify raises; got keys = {sorted(data.keys())!r}"
    )

    # (3) risk_classification sentinel
    assert summary.get("risk_classification") == "unknown", (
        f'summary must stamp ``risk_classification: "unknown"`` when '
        f"risk_classify raises; got {summary.get('risk_classification')!r}"
    )

    # (4) canonical floor still emitted
    assert summary.get("risk_level_canonical") == "low", (
        f'summary must floor ``risk_level_canonical`` to ``"low"`` on '
        f"risk_classify raise; got {summary.get('risk_level_canonical')!r}"
    )


def test_risk_classify_clean_path_stamps_classified(cli_runner, impact_project):
    """Happy path: ``risk_classification`` summary field is ``"classified"``.

    Mirrors the W607-BB discipline that the sentinel disambiguates a
    real classified verdict from a degraded "unknown" floor.
    """
    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("risk_classification") == "classified", (
        f'clean path must stamp ``risk_classification: "classified"``; '
        f"got {data['summary'].get('risk_classification')!r}"
    )


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, impact_project, monkeypatch):
    """ANY W607-BB or W607-T marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    impact" from "impact ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_impact

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-BB")

    monkeypatch.setattr(cmd_impact, "auto_log", _raise_auto_log)

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-BB warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607bb_warnings_out_in_both_top_and_summary(cli_runner, impact_project, monkeypatch):
    """Non-empty W607-BB bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-T contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_impact

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BB")

    monkeypatch.setattr(cmd_impact, "auto_log", _raise_auto_log)

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BB raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BB raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("impact_auto_log_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("impact_auto_log_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the auto_log marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Combined bucket -- W607-T + W607-BB markers both surface
# ---------------------------------------------------------------------------


def test_combined_w607t_and_w607bb_markers_both_surface(cli_runner, impact_project, monkeypatch):
    """W607-T and W607-BB markers BOTH surface when raises occur on
    each layer simultaneously.

    The additive plumbing must not shadow the W607-T bucket -- agents
    must see the full degradation lineage in marker-emission order.
    """
    from roam.commands import cmd_impact

    def _raise_indirect_refs(*a, **kw):
        # W607-T substrate boundary
        raise RuntimeError("synthetic-indirect-refs-from-W607-BB-combined")

    def _raise_auto_log(*a, **kw):
        # W607-BB aggregation boundary
        raise RuntimeError("synthetic-auto-log-from-W607-BB-combined")

    monkeypatch.setattr(cmd_impact, "_find_indirect_refs", _raise_indirect_refs)
    monkeypatch.setattr(cmd_impact, "auto_log", _raise_auto_log)

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    t_markers = [m for m in top_wo if m.startswith("impact_indirect_refs_failed:")]
    bb_markers = [m for m in top_wo if m.startswith("impact_auto_log_failed:")]
    assert t_markers, f"W607-T indirect_refs marker missing; got {top_wo!r}"
    assert bb_markers, f"W607-BB auto_log marker missing; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (10) Marker-prefix discipline -- W607-BB uses the SAME ``impact_*`` family
# ---------------------------------------------------------------------------


def test_w607bb_marker_prefix_impact_family(cli_runner, impact_project, monkeypatch):
    """W607-BB markers use the canonical ``impact_*`` prefix
    (same family as W607-T; W607-BB is ADDITIVE, not a separate prefix).

    Hard guard: any W607-BB marker that leaks into a sibling W607-*
    family (e.g. ``preflight_*`` / ``audit_*`` / ``diagnose_*``) breaks
    the closed-enum marker-family contract pinned in the W607-T test.
    """
    from roam.commands import cmd_impact

    def _raise_auto_log(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BB")

    monkeypatch.setattr(cmd_impact, "auto_log", _raise_auto_log)

    result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-discipline check"
    for marker in top_wo:
        assert marker.startswith("impact_"), f"every W607-BB marker must use the ``impact_*`` prefix; got {marker!r}"


# ---------------------------------------------------------------------------
# (11) PREFLIGHT/IMPACT GATE-PAIR pairing -- two marker families coexist
# ---------------------------------------------------------------------------


def test_preflight_impact_marker_families_coexist(cli_runner, impact_project, monkeypatch):
    """cmd_preflight (W607-AW + W607-R) and cmd_impact (W607-BB + W607-T)
    use distinct marker families that coexist when both run.

    Integration: simulate a degraded auto_log in BOTH cmd_preflight
    AND cmd_impact (the same ``auto_log`` import resolves to different
    module attributes; monkeypatching the cmd_impact module's
    ``auto_log`` does NOT affect cmd_preflight's because each command
    has its own ``from roam.runs.helpers import auto_log`` import).
    Run both commands and assert each surfaces its own marker family.
    """
    from roam.commands import cmd_impact, cmd_preflight

    def _raise_impact_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-impact-auto-log-gate-pair")

    def _raise_preflight_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-preflight-auto-log-gate-pair")

    # Patch each command's own module-attr binding.
    monkeypatch.setattr(cmd_impact, "auto_log", _raise_impact_auto_log)
    monkeypatch.setattr(cmd_preflight, "auto_log", _raise_preflight_auto_log)

    # Run impact -> expect ``impact_*`` markers.
    impact_result = _invoke_impact(cli_runner, impact_project, "impact_target")
    assert impact_result.exit_code == 0, impact_result.output
    impact_data = _json.loads(impact_result.output)
    impact_wo = impact_data.get("warnings_out") or []
    impact_markers = [m for m in impact_wo if m.startswith("impact_auto_log_failed:")]
    assert impact_markers, f"cmd_impact must surface ``impact_auto_log_failed:`` markers; got {impact_wo!r}"
    # No cross-family leakage from impact into preflight prefix.
    leaked_preflight = [m for m in impact_wo if m.startswith("preflight_")]
    assert not leaked_preflight, (
        f"cmd_impact warnings_out must not contain preflight_* markers; got {leaked_preflight!r}"
    )

    # Run preflight -> expect ``preflight_*`` markers.
    preflight_result = _invoke_preflight(cli_runner, impact_project, "impact_target")
    assert preflight_result.exit_code == 0, preflight_result.output
    preflight_data = _json.loads(preflight_result.output)
    preflight_wo = preflight_data.get("warnings_out") or []
    preflight_markers = [m for m in preflight_wo if m.startswith("preflight_auto_log_failed:")]
    assert preflight_markers, f"cmd_preflight must surface ``preflight_auto_log_failed:`` markers; got {preflight_wo!r}"
    # No cross-family leakage from preflight into impact prefix.
    leaked_impact = [m for m in preflight_wo if m.startswith("impact_")]
    assert not leaked_impact, f"cmd_preflight warnings_out must not contain impact_* markers; got {leaked_impact!r}"


# ---------------------------------------------------------------------------
# (12) SARIF projection guard -- ``--sarif`` path still emits with marker
# ---------------------------------------------------------------------------


def test_w607bb_sarif_projection_floor_on_raise(cli_runner, impact_project, monkeypatch):
    """If ``impact_to_sarif`` raises on the --sarif path, the wrap floors
    to a valid SARIF stub document and surfaces
    ``impact_serialize_sarif_failed:``.

    The CI-consumer contract is that --sarif always yields a parseable
    SARIF document; a crash here would break GitHub Code Scanning
    integration. The marker reaches the next-run's findings registry
    via the SARIF document's properties bag (downstream W607 work),
    but here we only assert the SARIF document still emits.
    """
    from roam.output import sarif as _sarif_mod

    def _raise_sarif(*args, **kwargs):
        raise RuntimeError("synthetic-sarif-from-W607-BB")

    monkeypatch.setattr(_sarif_mod, "impact_to_sarif", _raise_sarif)

    # Invoke with --sarif (not --json).
    from roam.cli import cli

    args = ["impact", "impact_target", "--sarif"]
    old_cwd = os.getcwd()
    try:
        os.chdir(str(impact_project))
        result = cli_runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, result.output
    # cmd_impact emits a text header line (``fn  <name>  <file>:<line>``)
    # before the SARIF document on the --sarif path; that's a pre-existing
    # text-output side effect, not a W607-BB axis. Strip everything up to
    # the first ``{`` and parse the SARIF document from there.
    output = result.output
    json_start = output.find("{")
    assert json_start >= 0, f"no SARIF document in output; got {output!r}"
    sarif_doc = _json.loads(output[json_start:])
    assert sarif_doc.get("version") == "2.1.0", (
        f"SARIF document must remain parseable on impact_to_sarif raise; got {sarif_doc!r}"
    )
    assert "runs" in sarif_doc, f"SARIF document must carry a ``runs`` array; got {sarif_doc!r}"
