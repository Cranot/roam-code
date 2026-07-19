from __future__ import annotations

import re

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
WORKFLOW = ROOT / ".github" / "workflows" / "architecture-guardian.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_guardian_retries_fresh_runner_parser_downloads_but_fails_closed() -> None:
    text = _workflow_text()
    build_step = text.split("- name: Build index", 1)[1].split("- name: Generate guardian markdown report", 1)[0]

    assert "for attempt in 1 2 3; do" in build_step
    assert "if .venv/bin/roam init --yes; then" in build_step
    assert 'if [[ "$attempt" == 3 ]]; then' in build_step
    assert "exit 1" in build_step
    assert "continue-on-error" not in build_step


def test_guardian_actions_are_commit_pinned_and_checkout_drops_credentials() -> None:
    text = _workflow_text()
    action_refs = re.findall(r"^\s*uses:\s*[^\s@]+@([^\s#]+)", text, flags=re.MULTILINE)

    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
    assert "persist-credentials: false" in text


def test_guardian_keeps_findings_advisory_without_masking_runtime_failures() -> None:
    text = _workflow_text()
    json_step = text.split("- name: Run and validate guardian report (JSON)", 1)[1].split(
        "- name: Upload guardian artifacts", 1
    )[0]

    assert "continue-on-error" not in text
    assert "|| true" not in text
    assert "report guardian" in json_step
    assert "--strict" not in json_step
    assert 'report.get("command") == "report"' in json_step
    assert "isinstance(failed, int)" in json_step
