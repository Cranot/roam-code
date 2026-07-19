"""Systemic supply-chain and failure-boundary guards for bundled CI templates."""

from __future__ import annotations

import re
from typing import Any

import yaml

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
CI_DIR = REPO_ROOT / "src" / "roam" / "templates" / "ci"

TEMPLATE_FILES = {
    "agent-review.yml",
    "azure-pipelines.yml",
    "bitbucket-pipelines.yml",
    "gitlab-ci.yml",
    "Jenkinsfile",
    "roam-sarif-with-codeql.yml",
    "slsa-src-l3.yml",
}
PACKAGE_FILES = {"__init__.py"}
GITHUB_TEMPLATES = {
    "agent-review.yml",
    "roam-sarif-with-codeql.yml",
    "slsa-src-l3.yml",
}

ACTION_PINS = {
    "actions/checkout": "34e114876b0b11c390a56381ad16ebd13914f8d5",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "github/codeql-action/upload-sarif": "03e4368ac7daa2bd82b3e85262f3bf87ee112f57",
    "marocchino/sticky-pull-request-comment": "773744901bac0e8cbb5a0dc842800d45e9b2b405",
    "sigstore/cosign-installer": "398d4b0eeef1380460a10c8013a76f728fb906ac",
}

PYTHON_IMAGE = "python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b"


def _read(name: str) -> str:
    return (CI_DIR / name).read_text(encoding="utf-8")


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _github_payload(name: str) -> dict[str, Any]:
    payload = yaml.safe_load(_read(name))
    assert isinstance(payload, dict), f"{name} must parse as a YAML mapping"
    return payload


def test_ci_template_family_inventory_is_closed() -> None:
    """Every shipped file is intentionally classified and policy-covered."""

    actual = {path.name for path in CI_DIR.iterdir() if path.is_file()}
    assert actual == TEMPLATE_FILES | PACKAGE_FILES
    assert _read("__init__.py").strip() == '"""CI/CD pipeline templates for roam-code integration."""'


def test_every_install_is_exactly_roam_code_13_10_0() -> None:
    install_pattern = re.compile(r"(?m)^.*(?:python -m )?pip install[^\n]*roam-code[^\n]*$")

    for name in sorted(TEMPLATE_FILES):
        text = _read(name)
        installs = install_pattern.findall(text)
        assert installs, f"{name} does not install roam-code"
        for install in installs:
            assert re.search(r"roam-code(?:\[mcp\])?==13\.10\.0(?:[\"']|$)", install), (
                f"{name} has a non-exact roam-code install: {install.strip()}"
            )
        assert "pip install --upgrade pip" not in text
        assert "pip install roam-code" not in text


def test_github_actions_runners_and_checkout_credentials_are_immutable() -> None:
    for name in sorted(GITHUB_TEMPLATES):
        payload = _github_payload(name)
        jobs = payload.get("jobs")
        assert isinstance(jobs, dict) and jobs
        for job in jobs.values():
            assert job["runs-on"] == "ubuntu-24.04"

        uses_steps = [node for node in _walk(payload) if isinstance(node, dict) and "uses" in node]
        assert uses_steps, f"{name} has no action steps"
        for step in uses_steps:
            uses = step["uses"]
            assert isinstance(uses, str) and "@" in uses
            action, ref = uses.rsplit("@", 1)
            assert action in ACTION_PINS, f"{name} uses an unreviewed action: {action}"
            assert ref == ACTION_PINS[action], f"{name} has the wrong pin for {action}: {ref}"
            assert re.fullmatch(r"[0-9a-f]{40}", ref)
            if action == "actions/checkout":
                with_values = step.get("with", {})
                assert with_values.get("persist-credentials") is False


def test_github_shells_receive_expressions_only_through_env() -> None:
    for name in sorted(GITHUB_TEMPLATES):
        payload = _github_payload(name)
        for node in _walk(payload):
            if isinstance(node, dict) and isinstance(node.get("run"), str):
                assert "${{" not in node["run"], f"{name} interpolates a GitHub expression in shell"


def test_vm_and_container_runtime_pins_are_explicit() -> None:
    all_text = "\n".join(_read(name) for name in sorted(TEMPLATE_FILES))
    assert not re.search(r"(?m)^\s*runs-on:\s*ubuntu-latest\s*$", all_text)
    assert not re.search(r"(?m)^\s*vmImage:\s*['\"]?ubuntu-latest", all_text)
    assert "vmImage: 'ubuntu-24.04'" in _read("azure-pipelines.yml")

    for name in ("gitlab-ci.yml", "bitbucket-pipelines.yml"):
        text = _read(name)
        assert PYTHON_IMAGE in text
        assert "digest, not the tag, is the runtime trust anchor" in text
        image_values = re.findall(r"(?m)^\s*image:\s*[\"']?([^\"'\s]+)", text)
        assert image_values == [PYTHON_IMAGE]

    jenkins = _read("Jenkinsfile")
    assert "executor-image mutability as a residual platform risk" in jenkins


def test_blanket_failure_masking_is_absent_across_the_family() -> None:
    for name in sorted(TEMPLATE_FILES):
        text = _read(name)
        assert "|| true" not in text
        assert "continue-on-error" not in text
        assert "2>/dev/null" not in text
        assert "allowEmptyArchive: true" not in text


def test_advisory_commands_have_paired_capture_and_disclosure() -> None:
    for name in ("azure-pipelines.yml", "bitbucket-pipelines.yml", "gitlab-ci.yml", "Jenkinsfile"):
        text = _read(name)
        set_plus = len(re.findall(r"(?m)^\s*set \+e\s*$", text))
        set_minus = len(re.findall(r"(?m)^\s*set -e\s*$", text))
        assert set_plus > 0, f"{name} lacks an advisory capture boundary"
        assert set_plus == set_minus, f"{name} has an unpaired set +e boundary"
        assert "exit_code=%s" in text
        assert "state=%s" in text
        assert "state=failed" in text
        assert ".status" in text
        assert "inspect captured" in text


def test_required_evidence_paths_fail_closed() -> None:
    agent_review = _read("agent-review.yml")
    assert "pr-analyze did not emit the required audit trail" in agent_review
    assert "if-no-files-found: error" in agent_review
    assert "invalid or missing pr-analyze verdict" in agent_review

    sarif = _read("roam-sarif-with-codeql.yml")
    assert "roam --sarif health > roam-health.sarif" in sarif
    assert "upload-sarif@03e4368ac7daa2bd82b3e85262f3bf87ee112f57" in sarif

    slsa = _read("slsa-src-l3.yml")
    assert "roam pr-bundle emit" in slsa and "--strict" in slsa
    assert "roam runs verify" in slsa
    assert "if-no-files-found: error" in slsa

    for name in ("azure-pipelines.yml", "bitbucket-pipelines.yml", "gitlab-ci.yml", "Jenkinsfile"):
        text = _read(name)
        assert re.search(r"roam --json health\s*>[^\n]+", text)
        assert re.search(r"roam --sarif health\s*>[^\n]+", text)
        assert "invalid or missing health_score" in text
        assert "print(0)" not in text


def test_advisory_rule_outputs_disclose_command_status() -> None:
    for name in ("azure-pipelines.yml", "bitbucket-pipelines.yml", "gitlab-ci.yml", "Jenkinsfile"):
        text = _read(name)
        assert "rules-json" in text
        assert "rules-json.status" in text or "${label}.status" in text
        assert "rules-sarif" in text
        assert "rules-sarif.status" in text or '"roam-advisory/${label}.status"' in text
        assert "advisory exit" in text or "ADVISORY rules" in text
        assert "violations = 0" not in text
        assert "rules_verdict = 'not available'" not in text


def test_azure_macros_enter_shell_via_environment_boundary() -> None:
    payload = yaml.safe_load(_read("azure-pipelines.yml"))
    assert isinstance(payload, dict)
    for node in _walk(payload):
        if isinstance(node, dict) and isinstance(node.get("script"), str):
            script = node["script"]
            assert not re.search(r"\$\((?:Build|System)\.", script), (
                "Azure predefined variables must enter scripts through env"
            )


def test_gitlab_extra_commands_are_bounded_before_dispatch_and_filenames() -> None:
    text = _read("gitlab-ci.yml")
    assert "ROAM_EXTRA_COMMANDS must contain only space-separated roam command names" in text
    validation = "^([a-z][a-z0-9-]{0,63})( [a-z][a-z0-9-]{0,63})*$"
    assert validation in text
    assert 'roam --json "$command_name"' in text
    assert '"roam-extra-${command_name}.json"' in text


def test_jenkins_parameters_are_bounded_before_shell_dispatch() -> None:
    text = _read("Jenkinsfile")
    assert 'case "${ROAM_PYTHON}" in' in text
    assert '"${ROAM_PYTHON}" -m venv' in text
    assert 'case "${ROAM_HEALTH_GATE}" in' in text
    assert "Refusing to clean a non-directory or symlinked .roam-venv" in text
