"""Incomplete scans route agents to recover evidence before applying fixes."""

from __future__ import annotations

import json

import pytest

from roam.commands import cmd_stale_refs as mod
from tests import test_w1527_stale_refs_unreadable_files as fixture_module
from tests.conftest import invoke_cli

refs_project = fixture_module.refs_project


@pytest.mark.parametrize("has_findings", [False, True])
@pytest.mark.parametrize("incomplete", [False, True])
def test_partial_guidance_recovers_evidence_first(cli_runner, refs_project, monkeypatch, has_findings, incomplete):
    (refs_project / "README.md").write_text(
        "[missing](docs/missing.md)\n" if has_findings else "# Readme\n", encoding="utf-8"
    )
    (refs_project / "docs/big.md").write_text("# Big\n", encoding="utf-8")
    original = mod._read_text_with_reason

    def read(path, *args, **kwargs):
        # Fixture-local source failure; no real permissions or timing dependency.
        if incomplete and path == refs_project / "docs/big.md":
            return None, "controlled source refusal"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(mod, "_read_text_with_reason", read)
    result = invoke_cli(cli_runner, ["--json", "--budget", "0", "stale-refs", "--gate"], cwd=refs_project)
    summary = json.loads(result.stdout)["summary"]
    assert summary["files_scanned"] > 0
    assert summary["partial_success"] is incomplete
    assert summary["stale_refs"] == int(has_findings)
    assert result.exit_code == (5 if incomplete or has_findings else 0)
    steps = summary["next_steps"]
    if incomplete:
        assert "scan gaps" in steps[0]
        assert not any("--fix apply" in step for step in steps)
        if has_findings:
            assert any("--fix preview" in step for step in steps)
    else:
        assert not any("scan gaps" in step for step in steps)
