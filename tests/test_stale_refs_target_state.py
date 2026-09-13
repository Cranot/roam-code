"""Discovery membership is not proof that a reference target still exists."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roam.commands import cmd_stale_refs as mod
from tests import test_w1527_stale_refs_unreadable_files as fixture_module
from tests.conftest import invoke_cli

refs_project = fixture_module.refs_project


@pytest.mark.parametrize("fragment", ["", "#big"])
@pytest.mark.parametrize("timing", ["live", "before_discovery", "after_discovery"])
def test_deleted_target_is_not_certified_by_discovery(cli_runner, refs_project, monkeypatch, fragment, timing):
    # Fixture-owned deletion, injected at a deterministic boundary. No clock,
    # thread race, external filesystem target or network dependency.
    target = refs_project / "docs/big.md"
    target.write_text("# Big\n", encoding="utf-8")
    (refs_project / "README.md").write_text(f"[target](docs/big.md{fragment})\n", encoding="utf-8")
    if timing == "before_discovery":
        target.unlink()
    elif timing == "after_discovery":
        original = mod.discover_files_with_skips

        def discover(*args, **kwargs):
            result = original(*args, **kwargs)
            target.unlink(missing_ok=True)
            return result

        monkeypatch.setattr(mod, "discover_files_with_skips", discover)
    scan = mod._scan_project(refs_project, include_excluded=False)
    findings, files_scanned, refs_seen = scan[:3]
    assert files_scanned > 0 and refs_seen == 1
    assert ("docs/big.md" in findings) is (timing != "live")
    assert "docs/big.md#big" not in findings  # missing file is not an anchor mismatch
    # Re-run through actual CLI serialization/gate. Restore after-discovery
    # premise because the direct scan deliberately removed our fixture target.
    if timing == "after_discovery":
        target.write_text("# Big\n", encoding="utf-8")
    result = invoke_cli(cli_runner, ["--json", "--budget", "0", "stale-refs", "--gate"], cwd=refs_project)
    assert result.exit_code == (0 if timing == "live" else 5), result.output
    assert json.loads(result.stdout)["summary"]["stale_refs"] == (0 if timing == "live" else 1)


@pytest.mark.parametrize("output", ["json", "sarif", "preview", "attest"])
def test_unavailable_target_state_is_partial_not_missing(cli_runner, refs_project, monkeypatch, output):
    target = refs_project / "docs/big.md"
    target.write_text("# Big\n", encoding="utf-8")
    (refs_project / "README.md").write_text("[first](docs/big.md) [second](docs/big.md#big)\n", encoding="utf-8")
    original_resolve, original_stat = mod._resolve_ref_target, Path.stat
    probing = False

    def resolve(*args, **kwargs):
        nonlocal probing
        result = original_resolve(*args, **kwargs)
        probing = True
        return result

    def stat(path, *args, **kwargs):
        if probing and path == target:
            raise PermissionError("controlled target observation failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(mod, "_resolve_ref_target", resolve)
    monkeypatch.setattr(Path, "stat", stat)
    artifact = refs_project.parent / "target-attestation.json"
    args = ["--sarif"] if output == "sarif" else ["--json", "--budget", "0"]
    args += ["stale-refs", "--gate"]
    if output == "preview":
        args += ["--fix", "preview"]
    if output == "attest":
        args += ["--attest", str(artifact)]
    result = invoke_cli(cli_runner, args, cwd=refs_project)
    assert result.exit_code == 5, result.output
    data = json.loads(result.stdout)
    if output == "sarif":
        messages = [
            note["message"]["text"]
            for invocation in data["runs"][0].get("invocations", [])
            for note in invocation.get("toolExecutionNotifications", [])
        ]
        assert any("target_states_unavailable" in message for message in messages)
    else:
        assert data["summary"]["partial_success"] is True
        assert data["summary"]["targets_unstatable"] == 1
        if output != "preview":
            assert data["summary"]["stale_refs"] == 0
        if output == "attest":
            recorded = json.loads(artifact.read_text())["predicate"]["scan_summary"]
            assert recorded["targets_unstatable"] == 1
            assert recorded["partial_success"] is True
