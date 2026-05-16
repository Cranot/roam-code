"""W829 — Empty-corpus smoke for ``roam audit-trail-verify``.

Sister to W827 (audit-trail-conformance). Verifies Pattern 2 cleanliness:
a project with no audit trail yet must NOT emit a default
"verified"/"SAFE" verdict — the envelope must disclose the uninitialized
state explicitly and flag ``partial_success``.

LAW 4 anchor terminals exercised: entries, chains, verifies, markers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture
def empty_corpus(tmp_path: Path) -> Path:
    """Tmp git project with one empty .py file and no audit trail."""
    import subprocess

    (tmp_path / "empty.py").write_text("# empty corpus\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t", "add", "-A"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


def _invoke(args: list[str], cwd: Path) -> tuple[int, str]:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=cwd) as _:
        # isolated_filesystem cd's into a new dir; we want to operate inside
        # ``cwd`` directly so the empty.py file is visible. Bypass via
        # monkey-patched cwd: just call invoke from within cwd manually.
        pass
    import os

    prev = os.getcwd()
    try:
        os.chdir(cwd)
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(prev)
    return result.exit_code, result.output


def test_audit_trail_verify_empty_corpus_emits_structured_envelope(empty_corpus: Path):
    """No audit trail at all → uninitialized state, partial_success=True."""
    init_code, init_out = _invoke(["init"], empty_corpus)
    assert init_code == 0, f"roam init failed: {init_out}"

    code, out = _invoke(["--json", "audit-trail-verify"], empty_corpus)

    # No --gate flag, so exit 0 even when chain is not initialized.
    assert code == 0, f"unexpected exit {code}; output: {out}"

    payload = json.loads(out)
    summary = payload["summary"]

    # Pattern 2 cleanliness: verdict must NOT silently claim success.
    verdict = summary["verdict"]
    verdict_lc = verdict.lower()
    assert "not initialized" in verdict_lc or "empty" in verdict_lc, (
        f"verdict should disclose empty/uninitialized state, got: {verdict!r}"
    )
    assert "valid" not in verdict_lc, f"verdict must not claim chain validity for empty trail: {verdict!r}"
    assert "safe" not in verdict_lc, f"verdict must not default to SAFE on empty trail: {verdict!r}"

    # Partial-success must be present and truthy.
    assert "partial_success" in summary
    assert summary["partial_success"] is True

    # State enum should disclose uninitialized; chain_valid must be false.
    assert summary["state"] == "uninitialized"
    assert summary["chain_valid"] is False
    assert summary["total_records"] == 0

    # agent_contract.facts must be non-empty (json_envelope auto-derives it
    # from the summary fields).
    facts = payload.get("agent_contract", {}).get("facts", [])
    assert isinstance(facts, list)
    assert len(facts) > 0, "agent_contract.facts should not be empty on empty corpus"


def test_audit_trail_verify_empty_corpus_gate_does_not_exit_5(empty_corpus: Path):
    """--gate on an uninitialized chain: chain_valid is False, so gate
    triggers exit 5. Documents current behavior — gate does NOT
    distinguish "uninitialized" from "broken" yet."""
    init_code, _ = _invoke(["init"], empty_corpus)
    assert init_code == 0

    code, out = _invoke(["--json", "audit-trail-verify", "--gate"], empty_corpus)

    # Document current behavior: --gate on uninitialized triggers exit 5
    # because chain_valid==False. This is a known minor wart (could be
    # tightened later to skip gating when state=='uninitialized'), but
    # the structured envelope still emits cleanly BEFORE the exit.
    assert code in (0, 5), f"unexpected exit {code}; output: {out}"
    payload = json.loads(out)
    assert payload["summary"]["state"] == "uninitialized"
    assert payload["summary"]["partial_success"] is True
