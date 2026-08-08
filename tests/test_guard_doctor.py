"""Tests for `roam guard-doctor` preflight."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture
def repo_with_roam(tmp_path, monkeypatch):
    """Bare project with .roam/ dir but no bundles."""
    (tmp_path / ".roam" / "pr-bundles").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_doctor_runs_all_checks_text_mode(repo_with_roam):
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-doctor"])
    # Exit may be 0/1/2 depending on env; just verify output shape.
    assert "VERDICT:" in result.output
    for check_name in (
        "dot_roam",
        "bundles_dir",
        "rule_pack",
        "command_graph",
        "git",
        "github_token",
        "verdict_log",
        "yaml_lib",
    ):
        assert check_name in result.output


def test_doctor_json_envelope_shape(repo_with_roam):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor"])
    payload = json.loads(result.output)
    assert payload["command"] == "guard-doctor"
    assert "checks" in payload
    assert len(payload["checks"]) == 9
    for c in payload["checks"]:
        assert c["status"] in ("pass", "warn", "fail")


def test_doctor_passes_with_healthy_setup(repo_with_roam):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor"])
    payload = json.loads(result.output)
    # No blocking failures expected in a fresh repo with .roam/ present.
    assert payload["summary"]["blocking_failures"] == []
    assert result.exit_code in (0, 1)  # 0 if all-pass, 1 if some warns


def test_doctor_with_invalid_rule_pack_fails_blocking(repo_with_roam):
    bad_yaml = repo_with_roam / "bad.yml"
    bad_yaml.write_text("name: x\nfile_patterns: [not-a-mapping]")
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor", "--rules", str(bad_yaml)])
    payload = json.loads(result.output)
    assert result.exit_code == 2  # blocking failure
    assert "rule_pack" in payload["summary"]["blocking_failures"]


def test_doctor_summary_verdict_terms(repo_with_roam):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor"])
    payload = json.loads(result.output)
    assert payload["summary"]["verdict"] in ("healthy", "warnings", "blocked")


def test_doctor_text_mode_shows_fix_hints_for_failures(repo_with_roam):
    """When a check fails, its `fix:` hint surfaces below the row."""
    bad_yaml = repo_with_roam / "bad.yml"
    bad_yaml.write_text("not yaml at all: [unclosed")
    runner = CliRunner()
    result = runner.invoke(cli, ["guard-doctor", "--rules", str(bad_yaml)])
    # Fix hint surfaces with `fix:` prefix in text mode.
    assert "fix:" in result.output


def test_doctor_smoke_compose_runs_when_bundle_exists(repo_with_roam):
    """`smoke_compose` check passes when the compose pipeline succeeds."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor"])
    payload = json.loads(result.output)
    smoke = next((c for c in payload["checks"] if c["name"] == "smoke_compose"), None)
    assert smoke is not None
    # repo_with_roam fixture supplies at least one valid pr-bundle.
    assert smoke["status"] in ("pass", "warn")


def test_doctor_smoke_compose_skips_when_no_bundles(tmp_path, monkeypatch):
    """`smoke_compose` warns (doesn't fail) when no bundles are present."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "guard-doctor"])
    payload = json.loads(result.output)
    smoke = next((c for c in payload["checks"] if c["name"] == "smoke_compose"), None)
    assert smoke is not None
    assert smoke["status"] == "warn"
    assert smoke["blocking"] is False


def test_a_warning_on_screen_is_never_summarised_as_healthy(tmp_path, monkeypatch):
    """`VERDICT: healthy` may not be printed above a ⚠ row.

    `summary_verdict`'s "warnings" rung was gated on `has_any_failure`, and
    every `status == "fail"` site in cmd_guard_doctor passes `blocking=True`,
    so that rung was true if and only if `has_blocking_failure` was. Every
    non-blocking problem is emitted as `"warn"`, which neither property could
    see. Measured before the fix: a run printed `VERDICT: healthy` on the line
    above `⚠ github_token — GITHUB_TOKEN not set` — the table naming a
    degradation and the summary denying it, on one screen.

    Same shape as the exit-code defect this file's siblings cover: the tool
    detects a condition, names it in its own output, and then does not encode
    it in the value a caller reads.
    """
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    payload = json.loads(runner.invoke(cli, ["--json", "guard-doctor"]).output)

    warned = [c["name"] for c in payload["checks"] if c["status"] == "warn"]
    assert warned, "fixture produced no warn rows, so this test proves nothing"

    verdict = payload["summary"]["verdict"]
    assert verdict != "healthy", (
        f"guard-doctor reported verdict {verdict!r} while these checks were "
        f"degraded: {warned}. A summary that contradicts its own table is the "
        "defect, not the presentation."
    )
    assert verdict == "warnings"


def test_documented_exit_code_rungs_match_the_ones_exit_code_can_return(tmp_path):
    """The module docstring may not advertise a rung the code cannot produce.

    guard-doctor's docstring used to promise `1 = at least one ADVISORY check
    failed`. A pipeline written as `if rc == 1: warn; elif rc == 2: fail`
    would collapse every advisory degradation into the success branch, because
    no input can produce 1. This pins the docstring to the implementation so
    the two cannot drift apart again in the direction of over-promising.

    Reachability is computed from the ``Check(...)`` shapes this module
    ACTUALLY constructs, not from feeding ``exit_code()`` every shape the
    dataclass permits. That distinction is the whole test: an isolated
    ``exit_code()`` probe can build a non-blocking ``"fail"`` and conclude 1 is
    reachable, while no check site in the module ever builds one. Testing the
    function in isolation is exactly how this stayed green.
    """
    import ast
    import inspect
    import itertools
    import re

    from roam.commands import cmd_guard_doctor as mod
    from roam.commands.cmd_guard_doctor import Check, DoctorReport

    documented = set(re.findall(r"^\s*(\d+) = ", mod.__doc__ or "", re.M))
    assert documented, (
        "no `N = ...` exit-code rungs found in cmd_guard_doctor's docstring. "
        "Either the contract was deleted or this test stopped parsing it; a "
        "silently vacuous guard is worse than no guard."
    )

    # Every (status, blocking) pair the module really constructs.
    shapes: set[tuple[str, bool]] = set()
    for node in ast.walk(ast.parse(inspect.getsource(mod))):
        if not isinstance(node, ast.Call) or getattr(node.func, "id", None) != "Check":
            continue
        if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
            continue
        status = node.args[1].value
        blocking = False
        for kw in node.keywords:
            if kw.arg == "blocking" and isinstance(kw.value, ast.Constant):
                blocking = bool(kw.value.value)
        shapes.add((str(status), blocking))
    assert shapes, "found no Check(...) construction sites -- the scan broke"

    # Any subset of the constructed shapes could co-occur in one run; the
    # exit code depends only on which shapes are present, so powersets of the
    # distinct shapes cover every reachable code.
    reachable: set[str] = set()
    for size in range(len(shapes) + 1):
        for combo in itertools.combinations(sorted(shapes), size):
            report = DoctorReport()
            for i, (status, blocking) in enumerate(combo):
                report.add(Check(f"probe{i}", status, "probe", blocking=blocking))
            reachable.add(str(report.exit_code()))

    assert documented <= reachable, (
        f"cmd_guard_doctor's docstring documents exit code(s) "
        f"{sorted(documented - reachable)} that no combination of the checks "
        f"this module builds can produce (reachable: {sorted(reachable)}; "
        f"constructed shapes: {sorted(shapes)}). Either make the rung "
        "reachable or stop advertising it — never leave a CI author branching "
        "on a dead code."
    )
