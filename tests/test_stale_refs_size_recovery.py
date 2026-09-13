"""Explicit finite source budgets recover evidence, never suppress scan gaps."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_stale_refs as mod
from roam.index.discovery import MAX_FILE_SIZE, SKIP_OVERSIZED, discover_files_with_skips


@pytest.fixture
def large_reference_project(tmp_path, monkeypatch):
    # Real fixture-local filesystem and CLI scan are the boundary under test;
    # no index, network, scheduler race or user repository mutation is needed.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_STALE_REFS_MAX_BYTES", raising=False)
    monkeypatch.setenv("ROAM_NO_AUTO_INDEX", "1")
    (tmp_path / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    padding = "plain text without references\n" * 36000
    assert len(padding.encode()) > MAX_FILE_SIZE
    (tmp_path / "large.md").write_text(padding + "\n[late](target.md#late)\n", encoding="utf-8")
    (tmp_path / "target.md").write_text(padding + "\n# Late\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("[target](target.md#late)\n", encoding="utf-8")
    return tmp_path


def _scan(*extra):
    result = CliRunner().invoke(cli, ["--json", "--budget", "0", "stale-refs", "--no-rename-hint", *extra])
    return result, json.loads(result.stdout)


@pytest.mark.parametrize("broken", [False, True])
def test_opt_in_observes_late_reference_and_anchor_without_changing_default(
    large_reference_project, monkeypatch, broken
):
    if broken:
        with (large_reference_project / "large.md").open("a", encoding="utf-8") as stream:
            stream.write("[genuine missing](missing.md)\n")
    before, missing = _scan("--gate")
    assert before.exit_code == 5, before.output
    assert missing["summary"]["files_unreadable"] == 2
    assert missing["summary"]["partial_success"] is True
    monkeypatch.setenv("ROAM_STALE_REFS_MAX_BYTES", "2000000")
    after, observed = _scan("--gate")
    assert observed["summary"]["files_unreadable"] == 0
    assert observed["summary"]["anchor_targets_unreadable"] == 0
    assert observed["summary"]["files_scanned"] > missing["summary"]["files_scanned"]
    assert observed["summary"]["refs_checked"] == (3 if broken else 2)
    assert observed["summary"]["stale_refs"] == int(broken)
    assert observed["summary"]["partial_success"] is False
    assert observed["summary"]["max_file_bytes"] == 2000000
    assert after.exit_code == (5 if broken else 0), after.output
    # The override is invocation-local, not a global mutation or cached setting.
    monkeypatch.delenv("ROAM_STALE_REFS_MAX_BYTES")
    again, reset = _scan("--gate")
    assert again.exit_code == 5
    assert reset["summary"]["files_unreadable"] == 2
    assert MAX_FILE_SIZE == 1000000
    assert not (large_reference_project / ".roam/index.db").exists()


@pytest.mark.parametrize("raw", ["", "0", "-1", "16000001", "1.5", "NaN", "true", "2000000oops"])
def test_invalid_override_refuses_before_scanning(tmp_path, monkeypatch, raw):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_STALE_REFS_MAX_BYTES", raw)
    monkeypatch.setattr(mod, "_scan_project", lambda *a, **k: pytest.fail("invalid limit reached the scanner"))
    result = CliRunner().invoke(cli, ["--json", "stale-refs"])
    assert result.exit_code == 2, result.output
    assert "ROAM_STALE_REFS_MAX_BYTES" in result.output


def test_above_selected_bound_stays_partial(large_reference_project, monkeypatch):
    monkeypatch.setenv("ROAM_STALE_REFS_MAX_BYTES", "1010000")
    result, report = _scan("--gate")
    assert result.exit_code == 5
    assert report["summary"]["partial_success"] is True
    assert report["summary"]["files_unreadable"] == 2
    assert report["summary"]["max_file_bytes"] == 1010000


def test_discovery_override_preserves_default_and_scope(large_reference_project):
    default_paths, default_skips = discover_files_with_skips(large_reference_project)
    assert "large.md" not in default_paths
    assert "large.md" in default_skips[SKIP_OVERSIZED]
    paths, skips = discover_files_with_skips(large_reference_project, max_file_size=2000000)
    assert {"large.md", "target.md"} <= set(paths)
    assert SKIP_OVERSIZED not in skips
    again, _ = discover_files_with_skips(large_reference_project)
    assert again == default_paths


def test_selected_read_budget_still_checks_growth_after_stat(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_STALE_REFS_MAX_BYTES", "64")
    source = tmp_path / "README.md"
    source.write_text("[target](missing.md)\n", encoding="utf-8")
    original = open
    reads = []

    def grow(target, *args, **kwargs):
        if target == source:
            source.write_bytes(b"x" * 65)
            reads.append(target)
        return original(target, *args, **kwargs)

    monkeypatch.setattr(mod, "open", grow, raising=False)
    result, report = _scan("--gate")
    assert reads
    assert result.exit_code == 5
    assert report["summary"]["partial_success"] is True
    assert report["summary"]["files_scanned"] == 0
    assert report["summary"]["files_unreadable"] == 1


@pytest.mark.parametrize("mode", [["--watch"], ["--check-external"], ["--fix", "preview"]])
def test_expanded_scope_refuses_unsupported_secondary_readers(tmp_path, monkeypatch, mode):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_STALE_REFS_MAX_BYTES", "2000000")
    monkeypatch.setattr(mod, "_scan_project", lambda *a, **k: pytest.fail("unsupported mode reached scanner"))
    monkeypatch.setattr(mod, "_run_watch_loop", lambda *a, **k: pytest.fail("unsupported watch reached loop"))
    result = CliRunner().invoke(cli, ["stale-refs", *mode])
    assert result.exit_code == 2, result.output
    assert "ROAM_STALE_REFS_MAX_BYTES" in result.output


@pytest.mark.parametrize("expanded", [False, True])
@pytest.mark.parametrize("incomplete", [False, True])
def test_guidance_respects_selected_size_and_keeps_scan_gap_first(tmp_path, monkeypatch, expanded, incomplete):
    # Real CLI scan of fixture-owned files. Isolate the generic recommendation
    # at its helper boundary; final scope-aware guidance is the consumer tested.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROAM_STALE_REFS_MAX_BYTES", raising=False)
    monkeypatch.setenv("ROAM_NO_AUTO_INDEX", "1")
    if expanded:
        monkeypatch.setenv("ROAM_STALE_REFS_MAX_BYTES", "2000000")
    (tmp_path / "README.md").write_text("[target](target.md#missing)\n", encoding="utf-8")
    (tmp_path / "target.md").write_text("# Existing\n", encoding="utf-8")
    if incomplete:
        (tmp_path / "oversized.md").write_bytes(b"x" * 2100000)

    def generic_steps(command, context):
        assert command == "stale-refs" and context["missing_targets"] == 1
        return ["Run `roam stale-refs --fix preview`, then `--fix apply`."]

    monkeypatch.setattr(mod, "suggest_next_steps", generic_steps)
    result, report = _scan()
    assert result.exit_code == 0, result.output
    assert report["summary"]["stale_refs"] == 1
    assert report["summary"]["partial_success"] is incomplete
    steps = report["summary"]["next_steps"]
    if incomplete:
        assert "scan gaps" in steps[0]
    if expanded:
        assert not any("--fix" in step for step in steps), steps
        assert any("manually" in step for step in steps), steps
        assert any("same" in step and "2000000" in step for step in steps), steps
    elif incomplete:
        assert any("--fix preview" in step for step in steps), steps
    else:
        assert steps == ["Run `roam stale-refs --fix preview`, then `--fix apply`."], steps
