"""W827 — Empty-corpus smoke for ``roam audit-trail-conformance-check``.

Part of the W805 empty-corpus sweep. Verifies Pattern 2 (silent-fallback)
discipline: on a brand-new index with no audit trail yet, the command must
emit an explicit ``no_trail`` state rather than silently scoring 0/100 and
calling the trail NON-conformant. The conformance check has never run when
the trail does not exist; the envelope must say so.

LAW 4 anchor terminals reachable here: ``audits``, ``events``, ``findings``,
``markers``. The test grounds on ``markers`` (the closed-enum state tokens
the envelope publishes) and ``events`` (audit-trail records).
"""

from __future__ import annotations

import json as _json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner


def _git_init(path: Path) -> None:
    """Initialise a minimum git repo so ``roam init`` can discover files."""
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)


def test_audit_trail_conformance_empty_corpus_no_trail(tmp_path: Path) -> None:
    """Empty corpus (init runs, no audit trail yet) → explicit ``no_trail`` state.

    Asserts the canonical Pattern 2 empty-state envelope:
        * exit code 0 (no gate flag — non-error)
        * structured envelope with ``summary.verdict`` mentioning ``empty``
          or ``no audit trail``
        * ``summary.partial_success`` is True (per Pattern 1-D / Pattern 2)
        * ``summary.state == "no_trail"``
        * ``summary.score`` is None (not silent zero)
        * ``checks`` list is non-empty and every entry carries
          ``state == "not_run"``
        * ``agent_contract.facts`` non-empty (concrete-noun anchored)
    """
    from roam.cli import cli

    # Empty corpus: one empty .py file is enough for `roam init` to succeed.
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    _git_init(tmp_path)

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))

        # 1. Build the index. No audit trail is touched here.
        init_result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert init_result.exit_code == 0, (
            f"`roam init` failed on empty corpus; output:\n{init_result.output}"
        )

        # 2. Run the conformance check on the (non-existent) audit trail.
        result = runner.invoke(
            cli,
            ["--json", "audit-trail-conformance-check"],
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, (
        f"empty-corpus conformance check should exit 0 (no --gate flag); "
        f"got exit={result.exit_code}, output:\n{result.output}"
    )

    env = _json.loads(result.output)
    summary = env["summary"]

    # Pattern 2: NO silent "conformant" / "SAFE" verdict on an absent trail.
    verdict = summary["verdict"].lower()
    assert "conformant" not in verdict, f"silent conformant verdict on empty trail: {verdict!r}"
    assert "safe" not in verdict, f"silent SAFE verdict on empty trail: {verdict!r}"
    # Verdict mentions the empty-state explicitly (either "empty" or the
    # canonical "no audit trail" phrase used by the command).
    assert "no audit trail" in verdict or "empty" in verdict, (
        f"verdict should disclose empty state; got: {verdict!r}"
    )

    # Pattern 1-D / Pattern 2: explicit partial-success marker on degraded state.
    assert "partial_success" in summary, "summary must publish partial_success marker"
    assert summary["partial_success"] is True

    # Closed-enum state token must read "no_trail".
    assert summary["state"] == "no_trail"

    # Score is None, NOT silent zero (Pattern 2 explicit-absent-state rule).
    assert summary["score"] is None
    assert summary["chain_compliance_score"] is None
    assert summary["total_records"] == 0
    assert summary["checks_passed"] == 0

    # Checks list is present, non-empty, and every entry is marked not_run.
    checks = env["checks"]
    assert isinstance(checks, list)
    assert len(checks) == 6, f"expected 6 Article 12 checks, got {len(checks)}"
    for c in checks:
        assert c["state"] == "not_run", (
            f"empty-trail check entry should be marked not_run; got: {c!r}"
        )
        assert c["passed"] is False

    # agent_contract.facts (when present) must be non-empty + concrete-noun anchored.
    # The conformance command does not currently stamp agent_contract, but if a
    # future revision adds one this assertion guards LAW 4 compliance.
    contract = env.get("agent_contract")
    if contract is not None:
        facts = contract.get("facts") or []
        assert facts, "agent_contract.facts must be non-empty when contract is published"
