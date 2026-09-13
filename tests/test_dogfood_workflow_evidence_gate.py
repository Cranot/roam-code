"""Execute the actual workflow's summary gate with controlled artifacts."""

from __future__ import annotations

import json

import pytest
import yaml

from tests._helpers.repo_root import repo_root


def test_workflow_collects_uncapped_artifact_without_weakening_gate():
    workflow = yaml.safe_load((repo_root() / ".github/workflows/dogfood.yml").read_text(encoding="utf-8"))
    script = next(
        step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "dogfood execution degraded" in step.get("run", "")
    )
    assert ".venv/bin/roam --json --budget 0 dogfood > dogfood.json" in script
    assert 's.get("partial_success") is not False' in script


@pytest.mark.parametrize(
    "state",
    ["complete", "incomplete", "failed", "missing_flag", "null_flag", "numeric_flag", "contradictory", "root_partial"],
)
def test_workflow_gate_names_degraded_sections(tmp_path, monkeypatch, state):
    workflow = yaml.safe_load((repo_root() / ".github/workflows/dogfood.yml").read_text(encoding="utf-8"))
    script = next(
        step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "dogfood execution degraded" in step.get("run", "")
    )
    code = script.split(".venv/bin/python - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
    summary = {"sections_run": ["audit", "pr_analyze"], "partial_success": state != "complete"}
    if state in {"incomplete", "failed"}:
        summary["incomplete_sections" if state == "incomplete" else "failed_sections"] = ["audit"]
    if state == "missing_flag":
        summary.pop("partial_success")
    if state == "null_flag":
        summary["partial_success"] = None
    if state == "numeric_flag":
        summary["partial_success"] = 0
    if state == "contradictory":
        summary.update(partial_success=False, failed_sections=["audit"])
    if state == "root_partial":
        summary["partial_success"] = False
    envelope = {"command": "dogfood", "summary": summary}
    if state == "root_partial":
        envelope["partial_success"] = True
    # Intentional fixture I/O and execution of the real workflow gate: no
    # subprocess/network call; tmp_path and monkeypatch contain every write.
    (tmp_path / "dogfood.json").write_text(json.dumps(envelope), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    if state == "complete":
        exec(compile(code, "dogfood-workflow-gate", "exec"), {})
    else:
        with pytest.raises(SystemExit, match="dogfood execution degraded"):
            exec(compile(code, "dogfood-workflow-gate", "exec"), {})
