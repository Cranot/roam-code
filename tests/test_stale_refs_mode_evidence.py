"""Execute fix, attestation and watch consumers with controlled scan gaps."""

from __future__ import annotations

import json
import time as stdlib_time
from types import SimpleNamespace

import pytest

from roam.commands import cmd_stale_refs as mod
from roam.index.discovery import SKIP_UNENUMERABLE
from tests import test_w1527_stale_refs_unreadable_files as fixture_module
from tests.conftest import invoke_cli

refs_project = fixture_module.refs_project


@pytest.mark.parametrize("mode", ["preview", "apply"])
@pytest.mark.parametrize("gate", [False, True])
def test_fix_gate_uses_findings_before_successful_rewrite(cli_runner, refs_project, mode, gate):
    # Real fixture-local scanning and writes are required to distinguish an
    # applied repair from a subsequent verification; no clock or network use.
    (refs_project / "docs/big.md").write_text("# MCP Servers\n", encoding="utf-8")
    readme = refs_project / "README.md"
    before = "[servers](docs/big.md#mcp-server)\n"
    after = "[servers](docs/big.md#mcp-servers)\n"
    readme.write_text(before, encoding="utf-8")
    flags = ["stale-refs", "--fix", mode, *(["--gate"] if gate else [])]
    result = invoke_cli(cli_runner, flags, cwd=refs_project, json_mode=True)
    assert result.exit_code == (5 if gate else 0), result.output
    summary = json.loads(result.output)["summary"]
    assert summary["partial_success"] is False
    assert readme.read_text(encoding="utf-8") == (after if mode == "apply" else before)
    rescanned = invoke_cli(cli_runner, ["stale-refs", "--gate"], cwd=refs_project)
    assert rescanned.exit_code == (0 if mode == "apply" else 5), rescanned.output


def _gap(monkeypatch, enabled=True):
    original = mod.discover_files_with_skips

    def discover(*args, **kwargs):
        paths, skips = original(*args, **kwargs)
        if enabled:
            skips[SKIP_UNENUMERABLE] = ["unseen.bin"]
        return paths, skips

    monkeypatch.setattr(mod, "discover_files_with_skips", discover)


@pytest.mark.parametrize("mode", ["preview", "apply", "attest"])
@pytest.mark.parametrize("incomplete", [False, True])
@pytest.mark.parametrize("gate", [False, True])
def test_modes_preserve_scan_completeness(cli_runner, refs_project, monkeypatch, mode, incomplete, gate):
    # Real fixture-local Git/filesystem and CLI serialization. Apply has no
    # proposed edits; an injected boundary records whether writes were attempted.
    (refs_project / "docs/big.md").write_text("No references.\n", encoding="utf-8")
    _gap(monkeypatch, incomplete)
    applied = []
    real_apply = mod._apply_fixes_in_place

    def apply(*args, **kwargs):
        applied.append(True)
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(mod, "_apply_fixes_in_place", apply)
    artifact = refs_project.parent / "attestation.json"
    flags = ["--attest", str(artifact)] if mode == "attest" else ["--fix", mode]
    result = invoke_cli(cli_runner, ["stale-refs", "--json", *flags, *(["--gate"] if gate else [])], cwd=refs_project)
    assert result.exit_code == (5 if gate and incomplete else 0), result.output
    summary = json.loads(result.output)["summary"]
    assert summary["partial_success"] is incomplete
    assert summary["scan_incomplete"] is incomplete
    assert summary["directories_unenumerable"] == int(incomplete)
    if mode == "apply" and gate and incomplete:
        assert not applied
    if mode == "attest":
        recorded = json.loads(artifact.read_text())["predicate"]["scan_summary"]
        assert recorded["partial_success"] is incomplete
        assert recorded["directories_unenumerable"] == int(incomplete)


@pytest.mark.parametrize("external_sleep_calls", [0, 3])
def test_watch_reports_gap_and_recovery_without_findings_change(
    cli_runner, refs_project, monkeypatch, external_sleep_calls
):
    (refs_project / "docs/big.md").write_text("No references.\n", encoding="utf-8")
    original = mod.discover_files_with_skips
    scans = []

    def discover(*args, **kwargs):
        # Discovery performs real fixture-local Git subprocesses. Model unrelated
        # polling deterministically: POSIX subprocess waiting may also use sleep.
        # These zero-duration calls must never consume the watch clock's ticks.
        if not scans:
            for _ in range(external_sleep_calls):
                stdlib_time.sleep(0)
        paths, skips = original(*args, **kwargs)
        if not scans:
            skips[SKIP_UNENUMERABLE] = ["unseen.bin"]
        scans.append(True)
        return paths, skips

    ticks = iter([None, None])

    def sleep(_seconds):
        # Exercise one poll/debounce/rescan, then stop deterministically.
        try:
            next(ticks)
        except StopIteration:
            raise KeyboardInterrupt

    mtimes = iter([{}, {"changed.md": 1}])
    monkeypatch.setattr(mod, "discover_files_with_skips", discover)
    monkeypatch.setattr(mod, "_collect_mtimes", lambda *a, **k: next(mtimes, {"changed.md": 1}))
    # Rebind only the command's clock: patching time.sleep mutates the shared
    # stdlib module and can interrupt subprocess polling before the first scan.
    monkeypatch.setattr(
        mod, "time", SimpleNamespace(sleep=sleep, time=stdlib_time.time, perf_counter=stdlib_time.perf_counter)
    )
    result = invoke_cli(cli_runner, ["stale-refs", "--watch"], cwd=refs_project, json_mode=False)
    assert result.exit_code == 0, result.output
    assert "initial: clean" not in result.output
    assert "SCAN INCOMPLETE" in result.output
    assert "unseen.bin" in result.output
    assert "SCAN COVERAGE RESTORED" in result.output
    assert len(scans) == 2


def test_watch_rejects_gate_instead_of_ignoring_it(cli_runner, refs_project, monkeypatch):
    monkeypatch.setattr(mod, "_run_watch_loop", lambda *a, **k: None)
    result = invoke_cli(cli_runner, ["stale-refs", "--watch", "--gate"], cwd=refs_project, json_mode=False)
    assert result.exit_code == 2, result.output
    assert "--gate" in result.output


def test_watch_rejects_bare_diff(cli_runner, refs_project, monkeypatch):
    monkeypatch.setattr(mod, "_run_watch_loop", lambda *a, **k: None)
    result = invoke_cli(cli_runner, ["stale-refs", "--watch", "--diff"], cwd=refs_project, json_mode=False)
    assert result.exit_code == 2, result.output
    assert "--diff" in result.output


@pytest.mark.parametrize(
    "mode, option",
    [
        ("watch", "--attest"),
        ("watch", "--baseline-save"),
        ("watch", "--github-summary"),
        ("watch", "--diff"),
        ("fix", "--attest"),
        ("fix", "--github-summary"),
        ("fix", "--sarif"),
    ],
)
def test_short_circuit_modes_reject_ignored_obligations(cli_runner, refs_project, monkeypatch, mode, option):
    monkeypatch.setattr(mod, "_run_watch_loop", lambda *a, **k: None)
    flags = ["--watch"] if mode == "watch" else ["--fix", "preview"]
    flags.append(option)
    if option != "--sarif":
        flags.append("HEAD" if option == "--diff" else str(refs_project.parent / "requested-artifact.json"))
    result = invoke_cli(cli_runner, ["stale-refs", *flags], cwd=refs_project, json_mode=False)
    assert result.exit_code == 2, result.output
    assert option in result.output


@pytest.mark.parametrize("failed", [False, True])
def test_anchor_lookup_failure_is_separate_from_source_scan(cli_runner, refs_project, monkeypatch, failed):
    from roam.commands import stale_refs_anchors

    (refs_project / "docs/big.md").write_text("# Target\n", encoding="utf-8")
    (refs_project / "README.md").write_text("[target](docs/big.md#target)\n", encoding="utf-8")
    if failed:
        # Source scanning remains readable; only the required anchor read fails.
        monkeypatch.setattr(stale_refs_anchors, "_read_anchors_for", lambda path, **kw: None)
    result = invoke_cli(cli_runner, ["stale-refs", "--json", "--gate"], cwd=refs_project)
    assert result.exit_code == (5 if failed else 0), result.output
    summary = json.loads(result.output)["summary"]
    assert summary["partial_success"] is failed
    assert summary["files_unreadable"] == 0
    assert summary["anchor_targets_unreadable"] == int(failed)
    assert summary["files_scanned"] > 0
