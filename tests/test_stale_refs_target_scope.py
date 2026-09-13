"""Explicit target scope is applied before anchor reads and target stat probes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roam.commands import cmd_stale_refs as mod
from roam.commands import stale_refs_anchors as anchors
from tests import test_w1527_stale_refs_unreadable_files as fixture_module
from tests.conftest import invoke_cli

refs_project = fixture_module.refs_project


@pytest.mark.parametrize("in_page", [False, True])
@pytest.mark.parametrize("scope", ["none", "file", "fragment"])
def test_ignored_anchor_does_not_attempt_unavailable_read(cli_runner, refs_project, monkeypatch, in_page, scope):
    # Deterministic parser-boundary refusal, not permissions or clock timing.
    # Source text stays readable; target exclusion does not exclude a source.
    (refs_project / "docs/big.md").write_text("# Big\n", encoding="utf-8")
    target = "README.md" if in_page else "docs/big.md"
    url = "#missing" if in_page else "docs/big.md#missing"
    (refs_project / "README.md").write_text(f"# Readme\n[x]({url})\n", encoding="utf-8")
    attempts = []
    original = anchors._read_anchors_for

    def unavailable(path, *args, **kwargs):
        if path == refs_project / target:
            attempts.append(path)
            return None
        return original(path, *args, **kwargs)

    monkeypatch.setattr(anchors, "_read_anchors_for", unavailable)
    args = ["--json", "--budget", "0", "stale-refs", "--gate"]
    if scope != "none":
        args += ["--ignore-target", target + ("#missing" if scope == "fragment" else "")]
    result = invoke_cli(cli_runner, args, cwd=refs_project)
    data = json.loads(result.stdout)
    assert data["summary"]["refs_checked"] == 1
    assert data["summary"]["files_scanned"] > 0
    assert data["summary"]["partial_success"] is (scope == "none")
    assert result.exit_code == (5 if scope == "none" else 0), result.output
    assert len(attempts) == (1 if scope == "none" else 0)


@pytest.mark.parametrize("in_page", [False, True])
def test_ignored_fragment_does_not_hide_other_missing_fragment(cli_runner, refs_project, in_page):
    (refs_project / "docs/big.md").write_text("# Big\n", encoding="utf-8")
    target = "README.md" if in_page else "docs/big.md"
    prefix = "" if in_page else target
    (refs_project / "README.md").write_text(f"[ignored]({prefix}#ignored) [real]({prefix}#missing)\n", encoding="utf-8")
    result = invoke_cli(
        cli_runner,
        ["--json", "--budget", "0", "stale-refs", "--gate", "--ignore-target", target + "#ignored"],
        cwd=refs_project,
    )
    data = json.loads(result.stdout)
    assert result.exit_code == 5
    assert data["summary"]["stale_refs"] == 1
    assert data["summary"]["refs_checked"] == 2
    assert data["summary"]["partial_success"] is False


@pytest.mark.parametrize("scope", ["none", "file", "fragment"])
def test_whole_target_scope_precedes_stat_but_fragment_scope_does_not(cli_runner, refs_project, monkeypatch, scope):
    target = refs_project / "docs/big.md"
    target.write_text("# Big\n", encoding="utf-8")
    (refs_project / "README.md").write_text("[target](docs/big.md#big)\n", encoding="utf-8")
    original_resolve, original_stat = mod._resolve_ref_target, Path.stat
    active = False
    attempts = []

    def resolve(*args, **kwargs):
        nonlocal active
        result = original_resolve(*args, **kwargs)
        active = True
        return result

    def stat(path, *args, **kwargs):
        if active and path == target:
            attempts.append(path)
            raise PermissionError("fixture-local stat failure")
        return original_stat(path, *args, **kwargs)

    # Freeze discovery/source processing order to isolate target observation;
    # source-read failures are a separate contract tested below.
    monkeypatch.setattr(mod, "discover_files_with_skips", lambda *a, **k: (["README.md"], {}))
    monkeypatch.setattr(mod, "_resolve_ref_target", resolve)
    monkeypatch.setattr(Path, "stat", stat)
    args = ["--json", "--budget", "0", "stale-refs", "--gate"]
    if scope != "none":
        args += ["--ignore-target", "docs/big.md" + ("#big" if scope == "fragment" else "")]
    result = invoke_cli(cli_runner, args, cwd=refs_project)
    data = json.loads(result.stdout)
    assert data["summary"]["refs_checked"] == 1
    assert data["summary"]["targets_unstatable"] == (0 if scope == "file" else 1)
    assert data["summary"]["partial_success"] is (scope != "file")
    assert result.exit_code == (0 if scope == "file" else 5)
    assert len(attempts) == (0 if scope == "file" else 1)


def test_target_exclusion_does_not_hide_source_read_failure(cli_runner, refs_project, monkeypatch):
    original = mod._read_text_with_reason

    def read(path, *args, **kwargs):
        if path == refs_project / "docs/big.md":
            return None, "controlled source read failure"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(mod, "_read_text_with_reason", read)
    result = invoke_cli(
        cli_runner,
        ["--json", "--budget", "0", "stale-refs", "--gate", "--ignore-target", "docs/big.md"],
        cwd=refs_project,
    )
    data = json.loads(result.stdout)
    assert result.exit_code == 5
    assert data["summary"]["partial_success"] is True
    assert any(item["file"] == "docs/big.md" for item in data["unreadable_files"])
