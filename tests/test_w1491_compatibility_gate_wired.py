"""W1491: the surface-compatibility gate is wired into CI and cannot decay.

``roam compatibility`` shipped at W1293 complete: a committed baseline, a
closed-enum verdict, a ``--ci`` exit-5 contract, and its own test file. It
was then never run by anything. ``grep -rn compatibility
.github/workflows/*.yml`` returned nothing, so for the whole life of the
command the surface it guards was unguarded, and the baseline drifted 43
commands / 38 flags / 20 MCP tools behind the shipped surface with nothing
red anywhere.

Two distinct failures produced that, and this file covers both:

  W1491  the gate was never executed  -> assert the workflow runs it, so
         deleting the job breaks a test rather than silently restoring the
         original condition.
  W1492  the baseline silently decayed -> assert ``--require-coverage``
         fails on an incomplete baseline, because removal detection is
         ``baseline - current`` and an entry the baseline never recorded
         can never be reported as removed.
  W1493  the remedy could erase the gate -> assert ``--write-baseline``
         refuses to drop entries the existing baseline records. Regenerating
         is what someone reaches for when the gate is red, which is exactly
         when blessing the current surface is wrong.

The scenarios build synthetic snapshots rather than asserting against the
committed baseline. Baseline exactness is an environment-derived claim and
belongs to the single pinned CI job that makes it; what belongs in the test
suite, on every interpreter, is that the mechanism behaves.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
from pathlib import Path

import yaml
from click.testing import CliRunner

import roam.commands.cmd_compatibility as compat_cmd
from roam.cli import cli
from roam.commands.cmd_compatibility import _build_snapshot, _diff, _verdict_for
from roam.exit_codes import EXIT_GATE_FAILURE
from tests._helpers.repo_root import repo_root

WORKFLOW = repo_root() / ".github" / "workflows" / "roam-ci.yml"
BASELINE_PATH = "dev/compatibility-baseline.json"


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _gate_steps() -> list[dict]:
    """Every ``run:`` step in roam-ci.yml that invokes ``roam compatibility``."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict) and re.search(r"\broam compatibility\b", str(step.get("run", "")))
    ]


# ---------------------------------------------------------------------------
# W1491 -- the gate is actually executed by CI
# ---------------------------------------------------------------------------


def test_w1491_ci_runs_the_compatibility_gate() -> None:
    """A gate no workflow invokes is decoration. This is the assertion whose
    absence let ``roam compatibility`` exist un-run since W1293."""
    steps = _gate_steps()
    assert steps, (
        "no job in roam-ci.yml runs `roam compatibility`. The command has a "
        "committed baseline and an exit-5 contract that nothing exercises; "
        "that is the exact state W1491 fixed."
    )


def test_w1491_both_gate_modes_are_enforced_against_the_committed_baseline() -> None:
    """Both failure modes must be gated, and against the committed file.

    ``--ci`` catches a removal; ``--require-coverage`` catches the baseline
    falling behind the surface, which is what makes a *later* removal
    invisible. Gating only the first leaves the decay path open, which is
    how the baseline got 43 commands behind.
    """
    scripts = [str(step["run"]) for step in _gate_steps()]
    assert any("--ci" in script for script in scripts), scripts
    assert any("--require-coverage" in script for script in scripts), scripts
    for script in scripts:
        assert f"--baseline {BASELINE_PATH}" in script, (
            f"the gate step must name the baseline it gates on, not rely on the command's repo-root search: {script!r}"
        )


def test_w1491_gate_runs_on_the_same_events_as_the_blocking_jobs() -> None:
    """Triggers are a property of the workflow, so the gate inherits the
    blocking merge gate's events only by living in that file. Assert the
    events are the ones that gate a merge, so moving the job to a schedule-
    only or dispatch-only workflow fails here."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML resolves an unquoted `on:` key to the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) >= {"push", "pull_request"}, triggers
    assert triggers["pull_request"]["branches"] == ["main"], triggers


# ---------------------------------------------------------------------------
# W1492 -- an incomplete baseline is a hard failure
# ---------------------------------------------------------------------------


def test_w1492_require_coverage_fails_on_a_baseline_missing_a_shipped_command(tmp_path) -> None:
    """Drop one command from an otherwise-current baseline: the gate must
    exit 5. This is the shape the committed baseline was in for 43 commands."""
    snapshot = _build_snapshot()
    dropped = sorted(snapshot["commands"])[0]
    del snapshot["commands"][dropped]
    baseline = _write(tmp_path / "baseline.json", snapshot)

    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--baseline", str(baseline), "--require-coverage"])
    assert result.exit_code == EXIT_GATE_FAILURE, (result.exit_code, result.output)
    assert BASELINE_PATH.rsplit("/", 1)[-1] in result.output or "write-baseline" in result.output


def test_w1492_require_coverage_names_the_refresh_command_as_the_remedy(tmp_path) -> None:
    """A gate that fails without naming its fix gets bypassed. The remedy is
    one offline command, and the output has to say so."""
    snapshot = _build_snapshot()
    del snapshot["commands"][sorted(snapshot["commands"])[0]]
    baseline = _write(tmp_path / "baseline.json", snapshot)

    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--baseline", str(baseline), "--require-coverage"])
    assert f"--write-baseline {baseline}" in result.output, result.output


def test_w1492_require_coverage_passes_against_a_current_baseline(tmp_path) -> None:
    """Positive control: a baseline that records the whole surface is clean.
    Without this, a gate that failed unconditionally would look correct."""
    baseline = _write(tmp_path / "baseline.json", _build_snapshot())

    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--baseline", str(baseline), "--ci", "--require-coverage"])
    assert result.exit_code == 0, result.output
    assert "no regressions" in result.output


def test_w1492_a_stale_baseline_reports_blocker_not_info(tmp_path) -> None:
    """The envelope must not claim ``info`` while the process exits 5.

    Under ``--require-coverage`` a stale baseline IS the failure, so the
    verdict and level track the caller's contract instead of describing the
    delta as a neutral addition.
    """
    snapshot = _build_snapshot()
    del snapshot["commands"][sorted(snapshot["commands"])[0]]
    baseline = _write(tmp_path / "baseline.json", snapshot)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "compatibility", "--baseline", str(baseline), "--require-coverage"])
    assert result.exit_code == EXIT_GATE_FAILURE, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] == "baseline stale"
    assert payload["summary"]["level"] == "blocker"
    assert payload["summary"]["coverage_gap"] >= 1


def test_w1492_coverage_gap_is_orthogonal_to_breaking() -> None:
    """An addition is never breaking, but it is always lost reach. The two
    counters have to move independently or one hides the other."""
    empty = {
        "schema_version": "1.0.0",
        "commands": {},
        "deprecated_aliases": {},
        "mcp_tools": [],
        "mcp_preset_counts": {},
        "categories": [],
        "envelope_summary_keys": [],
    }
    added = {**empty, "commands": {"brand-new": {"module": "x", "function": "y", "flags": []}}}

    delta = _diff(empty, added)
    assert delta["breaking_count"] == 0
    assert delta["coverage_gap_count"] == 1
    assert _verdict_for(delta) == ("surface additions", "info")
    assert _verdict_for(delta, require_coverage=True) == ("baseline stale", "blocker")


def test_w1492_missing_baseline_fails_the_coverage_gate(tmp_path) -> None:
    """No baseline is total loss of reach, not a partial one: nothing at all
    can be reported as removed. It must fail the coverage gate even though
    ``--ci`` was not passed."""
    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--baseline", str(tmp_path / "absent.json"), "--require-coverage"])
    assert result.exit_code == EXIT_GATE_FAILURE, (result.exit_code, result.output)


# ---------------------------------------------------------------------------
# W1493 -- --write-baseline cannot silently erase removal-detection coverage
# ---------------------------------------------------------------------------


def test_w1493_write_baseline_refuses_to_erase_a_recorded_command(tmp_path) -> None:
    """The footgun: regenerate after an accidental deletion and the deletion
    is blessed forever, because the next run compares against a baseline that
    no longer contains it. The refusal must name the entry, not just fail."""
    snapshot = _build_snapshot()
    snapshot["commands"]["_w1493_command_that_is_no_longer_shipped"] = {
        "module": "roam.commands.cmd_gone",
        "function": "gone",
        "flags": [],
    }
    baseline = _write(tmp_path / "baseline.json", snapshot)
    before = baseline.read_text(encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--write-baseline", str(baseline)])
    assert result.exit_code == EXIT_GATE_FAILURE, (result.exit_code, result.output)
    assert "_w1493_command_that_is_no_longer_shipped" in result.output
    assert baseline.read_text(encoding="utf-8") == before, "refused write still mutated the baseline"


def test_w1493_refusal_is_overridable_but_only_deliberately(tmp_path) -> None:
    """A guard with no escape hatch gets bypassed with ``--no-verify``-shaped
    workarounds. A deliberate removal is legitimate; it just has to be
    spelled out, so it shows up in the diff that performs it."""
    snapshot = _build_snapshot()
    snapshot["commands"]["_w1493_deliberately_retired"] = {
        "module": "roam.commands.cmd_gone",
        "function": "gone",
        "flags": [],
    }
    baseline = _write(tmp_path / "baseline.json", snapshot)

    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--write-baseline", str(baseline), "--accept-removals"])
    assert result.exit_code == 0, result.output
    rewritten = json.loads(baseline.read_text(encoding="utf-8"))
    assert "_w1493_deliberately_retired" not in rewritten["commands"]


def test_w1493_a_graceful_rename_never_trips_the_refusal() -> None:
    """The guard inherits ``_diff``'s rename handling, so a deprecation cycle
    regenerates its baseline without an override while a true deletion cannot.
    Asserting the delegation directly keeps that property from being lost to a
    future re-derivation of the set subtraction."""
    baseline = {
        "schema_version": "1.0.0",
        "commands": {"oldname": {"module": "x", "function": "y", "flags": []}},
        "deprecated_aliases": {},
        "mcp_tools": [],
        "mcp_preset_counts": {},
        "categories": [],
        "envelope_summary_keys": [],
    }
    renamed = {
        **baseline,
        "commands": {"newname": {"module": "x", "function": "y", "flags": []}},
        "deprecated_aliases": {"oldname": {"replacement": "newname", "reason": "renamed"}},
    }
    assert _diff(baseline, renamed)["breaking_count"] == 0


def test_w1493_write_baseline_still_creates_an_absent_baseline(tmp_path) -> None:
    """Positive control: the guard only protects an EXISTING baseline. First
    capture must stay a one-command operation."""
    baseline = tmp_path / "fresh.json"
    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--write-baseline", str(baseline)])
    assert result.exit_code == 0, result.output
    assert baseline.exists()


def test_w1493_an_unreadable_baseline_is_not_silently_blessed(tmp_path) -> None:
    """If the existing baseline will not parse, the write cannot be shown to
    preserve coverage. Fail closed rather than convert an unreadable file into
    a blessed one."""
    baseline = tmp_path / "corrupt.json"
    baseline.write_text("{ this is not json", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["compatibility", "--write-baseline", str(baseline)])
    assert result.exit_code == EXIT_GATE_FAILURE, (result.exit_code, result.output)
    assert baseline.read_text(encoding="utf-8") == "{ this is not json"


def test_w1493_written_baselines_are_byte_identical_across_platforms(tmp_path) -> None:
    """The baseline is a committed, reviewed artifact. Whoever refreshed it
    must not show up in the diff as a line-ending change on every line."""
    baseline = tmp_path / "eol.json"
    runner = CliRunner()
    assert runner.invoke(cli, ["compatibility", "--write-baseline", str(baseline)]).exit_code == 0
    assert b"\r" not in baseline.read_bytes()


# ---------------------------------------------------------------------------
# W1494 -- partial_success discloses the BLIND SPOT, not the findings
# ---------------------------------------------------------------------------
#
# In this codebase ``partial_success`` means "the result is incomplete
# relative to what was requested" -- the W1327 / Pattern-2c semantic that
# ``roam cycles`` violated by dropping its payload at the token budget and
# still reporting ``partial_success: false``. This command wired the field to
# ``breaking > 0``, which is a different claim ("problems were found") and is
# wrong in both directions. Measured on this repo at HEAD against the
# v13.10.0-era baseline, before the fix:
#
#   breaking=0 coverage_gap=110 -> partial_success False   <- false clean
#   breaking=1 coverage_gap=0   -> partial_success True    <- false degraded
#
# The first is the serious one, and it lives inside a gate that is now
# blocking in CI: the envelope asserted a complete result over 110 surface
# entries that were never evaluated.


def _summary(baseline: Path, *args: str) -> dict:
    result = CliRunner().invoke(cli, ["--json", "compatibility", "--baseline", str(baseline), *args])
    return json.loads(result.output)["summary"]


def test_w1494_partial_success_is_true_when_surface_went_unevaluated(tmp_path) -> None:
    """NEGATIVE CONTROL for the false-clean direction: fails pre-fix.

    A baseline missing a shipped command cannot report that command's later
    removal. The run is therefore incomplete, and the envelope must say so
    even though nothing breaking was found.
    """
    snapshot = _build_snapshot()
    del snapshot["commands"][sorted(snapshot["commands"])[0]]
    summary = _summary(_write(tmp_path / "baseline.json", snapshot))

    assert summary["breaking"] == 0, summary
    assert summary["coverage_gap"] >= 1, summary
    assert summary["partial_success"] is True, (
        f"the envelope asserted a COMPLETE result while surface entries went unevaluated: {summary}"
    )


def test_w1494_partial_success_is_false_when_a_complete_run_finds_a_regression(tmp_path) -> None:
    """NEGATIVE CONTROL for the false-degraded direction: fails pre-fix.

    Finding a breaking change is a successful, complete run. Degradation and
    findings are different claims and must not share one flag.
    """
    snapshot = _build_snapshot()
    snapshot["commands"]["_w1494_removed_command"] = {"module": "x", "function": "y", "flags": []}
    summary = _summary(_write(tmp_path / "baseline.json", snapshot))

    assert summary["breaking"] >= 1, summary
    assert summary["coverage_gap"] == 0, summary
    assert summary["partial_success"] is False, (
        f"a complete analysis that found a real regression reported itself degraded: {summary}"
    )


def test_w1494_surface_coverage_names_the_state_without_inference(tmp_path) -> None:
    """``partial_success`` alone cannot distinguish "checked everything and it
    was clean" from "checked what I could see". The closed-enum
    ``surface_coverage`` field names it directly, the same way
    ``truncation_reason`` disambiguates ``truncated``."""
    complete = _summary(_write(tmp_path / "complete.json", _build_snapshot()))
    assert complete["surface_coverage"] == "complete", complete
    assert complete["partial_success"] is False, complete
    assert complete["coverage_gap"] == 0, complete

    snapshot = _build_snapshot()
    del snapshot["commands"][sorted(snapshot["commands"])[0]]
    partial = _summary(_write(tmp_path / "partial.json", snapshot))
    assert partial["surface_coverage"] == "partial", partial
    assert partial["unevaluated_surface_entries"] == partial["coverage_gap"], partial


def test_w1494_surface_coverage_is_a_closed_enum_that_implies_partial_success(tmp_path) -> None:
    """``partial`` must never appear without ``partial_success: true``, and the
    enum must not drift -- the invariant ``_TRUNCATION_REASONS`` holds for
    ``truncated``."""
    assert compat_cmd._SURFACE_COVERAGE_KINDS == frozenset({"complete", "partial"})

    for mutate in (lambda s: None, lambda s: s["commands"].pop(sorted(s["commands"])[0])):
        snapshot = _build_snapshot()
        mutate(snapshot)
        summary = _summary(_write(tmp_path / "probe.json", snapshot))
        assert summary["surface_coverage"] in compat_cmd._SURFACE_COVERAGE_KINDS, summary
        assert (summary["surface_coverage"] == "partial") is (summary["partial_success"] is True), summary


# ---------------------------------------------------------------------------
# W1495 -- the documented breaking tally matches the computed one
# ---------------------------------------------------------------------------


def test_w1495_diff_docstring_names_every_breaking_contributor() -> None:
    """NEGATIVE CONTROL: fails pre-fix, where the docstring listed four of the
    five contributors and omitted preset shrinkage.

    That omission is not cosmetic. On this repo all four removed-* lists are
    empty and ``breaking`` is 1, which reads as a contradiction until the
    reader finds the undocumented fifth term -- and that single number is what
    makes the next release a major one, because ``core`` dropped 57 -> 17 MCP
    tools. Derived from the AST rather than hardcoded, so adding a sixth term
    without documenting it fails here too.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(_diff)))
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)

    summands: list[str] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "breaking" for t in node.targets):
            continue
        for call in ast.walk(node.value):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "len"
                and call.args
                and isinstance(call.args[0], ast.Name)
            ):
                summands.append(call.args[0].id)

    assert len(summands) == 5, summands
    docstring = ast.get_docstring(function) or ""
    undocumented = [name for name in summands if name not in docstring]
    assert not undocumented, (
        f"`_diff` adds {undocumented} to the breaking tally without naming them in its "
        f"docstring. A reader seeing breaking=1 with every removed-* list empty has no "
        f"way to find the term responsible."
    )
