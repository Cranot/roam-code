"""W607-AW -- additive aggregation-phase plumbing for ``cmd_preflight``.

cmd_preflight is the AGENT-OS PRE-EDIT SAFETY GATE per CLAUDE.md LAW 1:
agents are instructed to run ``roam preflight <symbol>`` BEFORE every
code change. A silent failure or partial-success in preflight is the
highest-blast-radius bug class in the entire roam surface, because it
directly defeats the change-safety gate that the assurance pipeline
depends on.

Relation to W607-R
------------------

cmd_preflight already carries W607-R substrate-CALL plumbing covering
seven helper boundaries: resolve_targets / blast_radius / affected_tests
/ complexity / coupling / conventions / fitness. W607-AW is ADDITIVE on
top of W607-R, extending marker coverage to the AGGREGATION-PHASE
boundaries that W607-R left unguarded:

  - ``overall_risk``       -- ``_overall_risk(...)`` rollup compute
  - ``risk_driver``        -- ``_risk_driver(...)`` row-picker
  - ``fitness_violations`` -- flat list build for summary contract
  - ``auto_log``           -- active-run ledger write

Both layers share the canonical ``preflight_*`` marker family and the
``preflight_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two buckets (``_w607r_warnings_out`` + ``_w607aw_warnings_out``) are
combined at envelope-emit time so consumers see the full degradation
lineage in marker-emission order.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

cmd_preflight's aggregation-phase boundaries (overall_risk / risk_driver
/ fitness_violations / auto_log) had no guards. A downstream
refactor that changes the severity dict shape, the rule_details
producer contract, or the HMAC chain on the runs ledger would crash
the envelope post-compute -- after all six signals were successfully
gathered, the agent loses the result. W607-AW wraps each boundary
with ``_run_check_aw`` so a raise becomes a marker via
``warnings_out`` and the envelope still emits.

LAW 1 / LAW 6 discipline (CLAUDE.md)
------------------------------------

The 5-signal degradation discipline test below is the agent-OS-critical
axis: a degraded 4-of-5 envelope is still actionable; a crashed
envelope is unactionable. The test simulates a raise in EACH of the 5
signal-compute boundaries (blast/complexity/conventions/coupling/
fitness) and asserts (a) the corresponding marker appears, (b) the
envelope still emits the OTHER 4 signals, (c) ``partial_success`` is
True, (d) the verdict is NOT a clean "SAFE" line.
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
# Helpers -- invoke preflight via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_preflight(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam preflight`` through the group so ``--json`` is honoured."""
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
def preflight_project(tmp_path, monkeypatch):
    """Indexed corpus with a unique resolvable symbol (``unique_target``).

    Two-file fixture so blast-radius / coupling / tests / complexity /
    conventions / fitness all have real inputs to chew on. Mirror of
    the W607-R fixture shape so the two test files stay byte-comparable.
    """
    proj = tmp_path / "preflight_w607aw_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def unique_target():\n    return helper()\n\n"
        "def helper():\n    return inner()\n\n"
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
    return proj


def _patch_helper(monkeypatch, attr_name: str, exc):
    """Patch ``cmd_preflight.<attr_name>`` to raise ``exc`` unconditionally."""
    from roam.commands import cmd_preflight

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cmd_preflight, attr_name, _raise)


# ---------------------------------------------------------------------------
# (1) Happy path -- byte-identical envelope, no W607-AW markers
# ---------------------------------------------------------------------------


def test_preflight_happy_path_no_w607aw_markers(cli_runner, preflight_project):
    """Clean preflight on a healthy corpus -> no W607-AW markers.

    Hash-stable: an empty W607-AW bucket on the success path must
    produce an envelope without any ``preflight_overall_risk_failed:`` /
    ``preflight_risk_driver_failed:`` / ``preflight_fitness_violations_failed:``
    / ``preflight_auto_log_failed:`` markers. Mirrors W607-R contract.
    """
    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "preflight"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607aw_phases = (
        "preflight_overall_risk_failed:",
        "preflight_risk_driver_failed:",
        "preflight_fitness_violations_failed:",
        "preflight_auto_log_failed:",
    )
    for prefix in w607aw_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean preflight must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_aw`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_preflight_carries_w607aw_accumulator():
    """AST-level guard: cmd_preflight source carries the W607-AW accumulator.

    Pins the canonical W607-AW anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-R)
    fails this guard rather than silently regressing the aggregation-
    phase marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_preflight.py"
    assert src_path.exists(), f"cmd_preflight.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "w607aw_warnings_out" in src, (
        "W607-AW accumulator missing from cmd_preflight; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_aw" in src, (
        "W607-AW helper ``_run_check_aw`` missing from cmd_preflight; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_aw is defined inside preflight().
    tree = ast.parse(src)
    found_run_check_aw = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_aw":
            found_run_check_aw = True
            break
    assert found_run_check_aw, (
        "W607-AW ``_run_check_aw`` helper not found in cmd_preflight AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-R must still be present (additive layer does NOT replace it)
    assert "w607r_warnings_out" in src, (
        "W607-R accumulator vanished alongside the W607-AW add; the "
        "additive plumbing must preserve the W607-R substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_aw():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_aw(...)`` with the canonical phase name.

    The four phases must appear inside a ``_run_check_aw("<phase>", ...)``
    call inside cmd_preflight. Multi-indent variants (8, 12, 16, 20, 24
    spaces) are all considered valid wrap call-sites.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_preflight.py"
    src = src_path.read_text(encoding="utf-8")

    # Each phase must appear inside a _run_check_aw("<phase>", ...) call.
    canonical_phases = (
        "overall_risk",
        "risk_driver",
        "fitness_violations",
        "auto_log",
    )
    for phase in canonical_phases:
        # Match _run_check_aw( + optional newline + indent + "phase"
        markers = [
            f'_run_check_aw(\n            "{phase}"',
            f'_run_check_aw(\n                "{phase}"',
            f'_run_check_aw(\n                    "{phase}"',
            f'_run_check_aw("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_aw(...); add the W607-AW guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) Marker shape -- ``preflight_<phase>_failed:<exc>:<detail>``
# ---------------------------------------------------------------------------


def test_overall_risk_failure_marker_format(cli_runner, preflight_project, monkeypatch):
    """If ``_overall_risk`` raises, surface
    ``preflight_overall_risk_failed:<exc_class>:<detail>`` and the
    envelope still emits the 5 signal sections."""
    _patch_helper(
        monkeypatch,
        "_overall_risk",
        RuntimeError("synthetic-overall-risk-from-W607-AW"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("preflight_overall_risk_failed:")]
    assert markers, f"expected ``preflight_overall_risk_failed:`` marker; got {top_wo!r}"
    marker = markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments; got {marker!r}"
    assert parts[1] == "RuntimeError", parts
    assert "synthetic-overall-risk-from-W607-AW" in parts[2], parts

    # 5-signal sections still emitted alongside the marker
    for key in ("blast_radius", "complexity", "conventions", "coupling", "fitness"):
        assert key in data, (
            f"envelope must still emit ``{key}`` even when overall_risk raises; got keys = {sorted(data.keys())!r}"
        )


def test_fitness_violations_failure_marker_format(cli_runner, preflight_project, monkeypatch):
    """If the fitness_violations builder raises, surface the marker.

    Patches ``_check_fitness`` to return a malformed dict whose
    ``rule_details`` contains a non-dict row -- the comprehension
    inside ``_build_fitness_violations_list`` will raise ``AttributeError``
    on ``detail.get(...)``.
    """
    from roam.commands import cmd_preflight

    def _malformed_fitness(*args, **kwargs):
        return {
            "rules_checked": 1,
            "rules_failed": 1,
            "rules_currently_failing": 1,
            "rules_failing_on_target": 1,
            "rules_failing_on_siblings": 0,
            "total_violations": 1,
            "failed_rules": ["bad"],
            # Non-dict row triggers AttributeError on detail.get(...)
            "rule_details": [object()],
            "severity": "WARNING",
        }

    monkeypatch.setattr(cmd_preflight, "_check_fitness", _malformed_fitness)

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("preflight_fitness_violations_failed:")]
    assert markers, f"expected ``preflight_fitness_violations_failed:`` marker; got {top_wo!r}"
    # Envelope still emits the 5 signal sections
    for key in ("blast_radius", "complexity", "conventions", "coupling", "fitness"):
        assert key in data


def test_auto_log_failure_marker_format(cli_runner, preflight_project, monkeypatch):
    """If ``auto_log`` raises, surface ``preflight_auto_log_failed:`` and
    keep the full 5-signal envelope intact.

    Discipline mirror of the W607-AS HMAC-failure-aborts-write pattern.
    The auto_log boundary writes to the active run ledger when one is
    open -- a raise here would otherwise crash the envelope AFTER the
    five signals were successfully computed.
    """
    from roam.commands import cmd_preflight

    def _raise_auto_log(*args, **kwargs):
        raise RuntimeError("synthetic-auto-log-from-W607-AW")

    monkeypatch.setattr(cmd_preflight, "auto_log", _raise_auto_log)

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("preflight_auto_log_failed:")]
    assert markers, f"expected ``preflight_auto_log_failed:`` marker; got {top_wo!r}"
    for key in ("blast_radius", "complexity", "conventions", "coupling", "fitness"):
        assert key in data


# ---------------------------------------------------------------------------
# (5) 5-SIGNAL DEGRADATION discipline -- the agent-OS-critical axis
# ---------------------------------------------------------------------------


_SIGNAL_BOUNDARIES = [
    # (attribute_to_patch, marker_prefix, envelope_section_for_this_signal,
    #  other_4_envelope_sections_that_MUST_still_emit)
    (
        "_check_blast_radius",
        "preflight_blast_radius_failed:",
        "blast_radius",
        ("complexity", "conventions", "coupling", "fitness"),
    ),
    (
        "_check_complexity",
        "preflight_complexity_failed:",
        "complexity",
        ("blast_radius", "conventions", "coupling", "fitness"),
    ),
    (
        "_check_conventions",
        "preflight_conventions_failed:",
        "conventions",
        ("blast_radius", "complexity", "coupling", "fitness"),
    ),
    (
        "_check_coupling",
        "preflight_coupling_failed:",
        "coupling",
        ("blast_radius", "complexity", "conventions", "fitness"),
    ),
    (
        "_check_fitness",
        "preflight_fitness_failed:",
        "fitness",
        ("blast_radius", "complexity", "conventions", "coupling"),
    ),
]


@pytest.mark.parametrize(
    "attr,prefix,this_signal,other_signals",
    _SIGNAL_BOUNDARIES,
    ids=[b[0] for b in _SIGNAL_BOUNDARIES],
)
def test_5_signal_degradation_discipline(
    cli_runner, preflight_project, monkeypatch, attr, prefix, this_signal, other_signals
):
    """LAW 1: a degraded 4-of-5 envelope is still actionable.

    For each of the 5 signal-compute boundaries
    (blast/complexity/conventions/coupling/fitness), simulate a raise
    and assert:

    1. The corresponding marker appears in warnings_out
    2. The envelope still emits the OTHER 4 signal sections
       (partial-batch resilience)
    3. ``summary.partial_success`` is True
    4. The verdict is NOT a clean "Safe to proceed" line -- the gate
       must NOT say SAFE when any signal degraded

    The 5-signal envelope (blast / complexity / conventions / coupling /
    fitness) is THE dominant variable per LAW 1 of CLAUDE.md. A
    degraded 4-of-5 envelope is still actionable for the agent; a
    crashed envelope is not.
    """
    _patch_helper(
        monkeypatch,
        attr,
        RuntimeError(f"synthetic-{attr}-from-W607-AW"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # (1) marker appears
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith(prefix)]
    assert markers, f"5-signal degradation discipline: {attr} raise must surface ``{prefix}`` marker; got {top_wo!r}"

    # (2) OTHER 4 signal sections still emit (partial-batch resilience)
    for other in other_signals:
        assert other in data, (
            f"5-signal degradation discipline: {attr} raise must NOT "
            f"crash the envelope -- section ``{other}`` is missing; "
            f"got keys = {sorted(data.keys())!r}"
        )

    # (3) partial_success flips
    assert data["summary"].get("partial_success") is True, (
        f"5-signal degradation discipline: {attr} raise must flip "
        f"summary.partial_success=True; got summary = {data['summary']!r}"
    )

    # (4) PRE-EDIT GATE bonus: verdict is NOT a clean SAFE line. The
    # gate must NOT say "Safe to proceed" when any signal degraded --
    # otherwise the agent reads "SAFE" and the substrate-degradation
    # disclosure is silently lost.
    verdict = data["summary"].get("verdict", "")
    assert "Safe to proceed" not in verdict, (
        f"PRE-EDIT GATE discipline: when any of the 5 signals "
        f"degraded, the verdict must NOT say ``Safe to proceed``; "
        f"got verdict = {verdict!r}"
    )


# ---------------------------------------------------------------------------
# (6) PRE-EDIT GATE bonus -- ANY marker flips partial_success + downgrades verdict
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, preflight_project, monkeypatch):
    """ANY W607-AW or W607-R marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    preflight" from "preflight ran with substrate degradation" via
    summary.partial_success alone.
    """
    _patch_helper(
        monkeypatch,
        "_overall_risk",
        RuntimeError("synthetic-partial-success-from-W607-AW"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-AW warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607aw_warnings_out_in_both_top_and_summary(cli_runner, preflight_project, monkeypatch):
    """Non-empty W607-AW bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-R contract: top-level is needed because
    the preserved-list field survives ``strip_list_payloads`` in
    default-detail mode; summary mirror gives consumers reading only
    the summary block visibility too.
    """
    _patch_helper(
        monkeypatch,
        "_overall_risk",
        RuntimeError("synthetic-mirror-from-W607-AW"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AW raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AW raise path; got summary = {data['summary']!r}"
    )

    # Both mirrors carry the same marker
    top_markers = [m for m in data["warnings_out"] if m.startswith("preflight_overall_risk_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("preflight_overall_risk_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the overall_risk marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) Combined bucket -- W607-R + W607-AW markers both surface
# ---------------------------------------------------------------------------


def test_combined_w607r_and_w607aw_markers_both_surface(cli_runner, preflight_project, monkeypatch):
    """W607-R and W607-AW markers BOTH surface when raises occur on
    each layer simultaneously.

    The additive plumbing must not shadow the W607-R bucket -- agents
    must see the full degradation lineage in marker-emission order.
    """
    from roam.commands import cmd_preflight

    def _raise_blast(*a, **kw):
        # W607-R substrate boundary
        raise RuntimeError("synthetic-blast-from-W607-AW-combined")

    def _raise_overall(*a, **kw):
        # W607-AW aggregation boundary
        raise RuntimeError("synthetic-overall-from-W607-AW-combined")

    monkeypatch.setattr(cmd_preflight, "_check_blast_radius", _raise_blast)
    monkeypatch.setattr(cmd_preflight, "_overall_risk", _raise_overall)

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    blast_markers = [m for m in top_wo if m.startswith("preflight_blast_radius_failed:")]
    overall_markers = [m for m in top_wo if m.startswith("preflight_overall_risk_failed:")]
    assert blast_markers, f"W607-R blast marker missing; got {top_wo!r}"
    assert overall_markers, f"W607-AW overall_risk marker missing; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-AW uses the SAME ``preflight_*`` family
# ---------------------------------------------------------------------------


def test_w607aw_marker_prefix_preflight_family(cli_runner, preflight_project, monkeypatch):
    """W607-AW markers use the canonical ``preflight_*`` prefix
    (same family as W607-R; W607-AW is ADDITIVE, not a separate prefix).

    Hard guard: any W607-AW marker that leaks into a sibling W607-*
    family (e.g. ``audit_*`` / ``health_*`` / ``pr_risk_*``) breaks the
    closed-enum marker-family contract pinned in the W607-R test.
    """
    _patch_helper(
        monkeypatch,
        "_overall_risk",
        PermissionError("synthetic-prefix-discipline-from-W607-AW"),
    )

    result = _invoke_preflight(cli_runner, preflight_project, "unique_target")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-discipline check"
    for marker in top_wo:
        assert marker.startswith("preflight_"), (
            f"every W607-AW marker must use the ``preflight_*`` prefix; got {marker!r}"
        )
