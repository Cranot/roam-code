from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
import yaml

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
ACTION_PATH = ROOT / "action.yml"
PIN_SCRIPT = ROOT / "dev" / "pin_github_actions.sh"
CI_DOC = ROOT / "docs" / "ci-integration.md"
GITHUB_GUARD_TEMPLATE = ROOT / "templates" / "examples" / "roam-guard-pr.github-actions.yml"

SETUP_PYTHON_REF = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
CACHE_REF = "actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830"
CODEQL_REF = "github/codeql-action/upload-sarif@03e4368ac7daa2bd82b3e85262f3bf87ee112f57"
GITHUB_SCRIPT_REF = "actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b"


def _action() -> dict:
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _action()["runs"]["steps"]


def _step(*, step_id: str | None = None, name: str | None = None) -> dict:
    for step in _steps():
        if step_id is not None and step.get("id") == step_id:
            return step
        if name is not None and step.get("name") == name:
            return step
    raise AssertionError(f"missing composite step: id={step_id!r}, name={name!r}")


def _bash_executable() -> str:
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    bash = shutil.which("bash")
    assert bash, "the composite action requires bash, but no bash executable was found"
    return bash


def _validator_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    values = {
        "INPUT_VERSION": "13.10.0",
        "INPUT_ALLOW_LATEST": "false",
        "INPUT_COMMANDS": "health pr-risk",
        "INPUT_CHANGED_ONLY": "false",
        "INPUT_CHANGED_DEPTH": "3",
        "INPUT_BASE_REF": "origin/main",
        "INPUT_SARIF": "true",
        "INPUT_SARIF_COMMANDS": "auto",
        "INPUT_SARIF_CATEGORY": "roam-code",
        "INPUT_SARIF_MAX_RUNS": "20",
        "INPUT_SARIF_MAX_RESULTS": "25000",
        "INPUT_SARIF_MAX_BYTES": "10000000",
        "INPUT_COMMENT": "true",
        "INPUT_GATE": "health_score>=60, velocity(risk_score)<=0",
        "INPUT_CACHE": "true",
        "INPUT_PYTHON_VERSION": "3.11",
        "ACTION_PATH_INPUT": ROOT.as_posix(),
        "EVENT_NAME_INPUT": "pull_request",
        "PR_BASE_SHA_INPUT": "a" * 40,
        "PUSH_BEFORE_SHA_INPUT": "",
    }
    values.update(overrides)
    return os.environ.copy() | values | {"GITHUB_OUTPUT": (tmp_path / "github-output.txt").as_posix()}


def _run_validator(tmp_path: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash_executable(), "-c", _step(step_id="validate-inputs")["run"]],
        cwd=ROOT,
        env=_validator_env(tmp_path, **overrides),
        text=True,
        capture_output=True,
        check=False,
    )


def test_all_composite_action_dependencies_are_reviewed_commit_pins() -> None:
    refs = [step["uses"] for step in _steps() if "uses" in step and not step["uses"].startswith("./")]

    assert Counter(refs) == Counter(
        {
            SETUP_PYTHON_REF: 1,
            CACHE_REF: 2,
            CODEQL_REF: 1,
            GITHUB_SCRIPT_REF: 1,
        }
    )
    assert all(re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", ref) for ref in refs)

    text = ACTION_PATH.read_text(encoding="utf-8")
    for version_comment in ("# v5.6.0", "# v4.3.0", "# v3.36.0", "# v7.1.0"):
        assert version_comment in text


def test_no_composite_shell_or_javascript_source_interpolates_expressions() -> None:
    for step in _steps():
        run = step.get("run")
        if isinstance(run, str):
            assert "${{" not in run, step.get("name")

        script = (step.get("with") or {}).get("script")
        if isinstance(script, str):
            assert "${{" not in script, step.get("name")

        if step.get("id") != "validate-inputs":
            serialized_env = "\n".join(str(value) for value in (step.get("env") or {}).values())
            assert "${{ inputs." not in serialized_env, step.get("name")
            assert "${{ github.event." not in serialized_env, step.get("name")
            assert "${{ github.action_path" not in serialized_env, step.get("name")

        serialized_with = "\n".join(str(value) for value in (step.get("with") or {}).values())
        assert "${{ inputs." not in serialized_with, step.get("name")
        assert "${{ github.event." not in serialized_with, step.get("name")
        assert "${{ github.action_path" not in serialized_with, step.get("name")


def test_validator_is_the_single_raw_input_boundary() -> None:
    action = _action()
    validator = _step(step_id="validate-inputs")
    env_values = set(validator["env"].values())

    for input_name in action["inputs"]:
        assert f"${{{{ inputs.{input_name} }}}}" in env_values
    for context_expr in (
        "${{ github.action_path }}",
        "${{ github.event_name }}",
        "${{ github.event.pull_request.base.sha }}",
        "${{ github.event.before }}",
    ):
        assert context_expr in env_values

    body = validator["run"]
    for required_guard in (
        "validate_bool",
        "validate_uint",
        "version_re=",
        "latest requires allow-latest=true",
        "command names must match",
        "forbidden Git ref component",
        "unsupported expression grammar",
        "expected Python 3.10 through 3.13",
    ):
        assert required_guard in body


def test_safe_defaults_are_exact_and_mutable_latest_is_explicit() -> None:
    action = _action()
    assert action["inputs"]["version"]["default"] == "13.10.0"
    assert action["inputs"]["allow-latest"]["default"] == "false"

    installer = _step(name="Install roam-code")
    assert installer["env"]["ROAM_VERSION"] == "${{ steps.validate-inputs.outputs.version }}"
    assert installer["env"]["ALLOW_LATEST"] == "${{ steps.validate-inputs.outputs.allow-latest }}"
    for required in (
        '"roam-code==${ROAM_VERSION}"',
        "Mutable latest install was not explicitly authorized",
        "INSTALLED_VERSION=",
        "python -m pip check",
    ):
        assert required in installer["run"]

    docs = CI_DOC.read_text(encoding="utf-8")
    assert "| `version` | `13.10.0` |" in docs
    assert "| `allow-latest` | `false` |" in docs
    assert "transitive dependencies still follow" in docs
    assert "`uv.lock`" in docs


def test_ci_guide_examples_avoid_mutable_dependency_and_runner_defaults() -> None:
    docs = CI_DOC.read_text(encoding="utf-8")

    assert "runs-on: ubuntu-latest" not in docs
    assert "Cranot/roam-code@main" not in docs
    assert 'pip install --disable-pip-version-check "roam-code==13.10.0"' in docs
    assert "Cranot/roam-code@v13.10.0" in docs
    assert "replace the tag with its reviewed 40-character SHA" in docs
    for ref in (
        "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5",
        SETUP_PYTHON_REF,
        CODEQL_REF,
    ):
        assert ref in docs

    action_refs = re.findall(r"^\s*- uses:\s*([^\s#]+)", docs, flags=re.MULTILINE)
    assert action_refs
    for ref in action_refs:
        if ref.startswith("Cranot/roam-code@"):
            assert ref == "Cranot/roam-code@v13.10.0"
        else:
            assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", ref), ref


def test_github_guard_template_has_closed_verdict_and_required_evidence() -> None:
    text = GITHUB_GUARD_TEMPLATE.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    steps = payload["jobs"]["roam-guard"]["steps"]

    assert payload["jobs"]["roam-guard"]["runs-on"] == "ubuntu-24.04"
    assert "'roam-code==13.10.0'" in text
    assert "python -I -m venv .roam-guard-venv" in text
    assert 'case "${status}" in' in text
    assert "0|4|5" in text
    assert "failed before producing a verdict" in text
    assert "did not produce required non-empty guard.md evidence" in text
    assert "text.startswith('## ') and 'Roam Guard verdict:' in text" in text
    assert "if-no-files-found: error" in text
    assert "|| true" not in text
    assert "continue-on-error" not in text

    refs = [step["uses"] for step in steps if "uses" in step]
    assert refs
    for ref in refs:
        assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", ref), ref


def test_github_script_reads_action_path_from_process_env() -> None:
    step = _step(name="Post PR comment")
    script = step["with"]["script"]

    assert step["uses"] == GITHUB_SCRIPT_REF
    assert step["if"] == (
        "steps.validate-inputs.outputs.comment == 'true' && steps.validate-inputs.outputs.event-name == 'pull_request'"
    )
    assert step["env"]["ACTION_PATH"] == "${{ steps.validate-inputs.outputs.action-path }}"
    assert "process.env.ACTION_PATH" in script
    assert "path.join(actionPath" in script
    assert "${{" not in script


def test_validator_accepts_safe_exact_and_explicit_latest_modes(tmp_path: Path) -> None:
    exact = _run_validator(tmp_path)
    assert exact.returncode == 0, exact.stderr
    output = (tmp_path / "github-output.txt").read_text(encoding="utf-8")
    assert "version=13.10.0\n" in output
    assert "commands=health pr-risk\n" in output

    latest_dir = tmp_path / "latest"
    latest_dir.mkdir()
    latest = _run_validator(latest_dir, INPUT_VERSION="latest", INPUT_ALLOW_LATEST="true")
    assert latest.returncode == 0, latest.stderr
    latest_output = (latest_dir / "github-output.txt").read_text(encoding="utf-8")
    assert "version=latest\n" in latest_output
    assert "allow-latest=true\n" in latest_output


@pytest.mark.parametrize(
    "version",
    [
        "0.1",
        "1.2.3",
        "1.2.3.4",
        "13.10rc1",
        "13.10.0.post1",
        "13.10.0.dev1",
        "13.10.0rc1.post2.dev3",
    ],
)
def test_validator_accepts_closed_pep440_style_release_forms(tmp_path: Path, version: str) -> None:
    result = _run_validator(tmp_path, INPUT_VERSION=version)

    assert result.returncode == 0, result.stderr
    output = (tmp_path / "github-output.txt").read_text(encoding="utf-8")
    assert f"version={version}\n" in output


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"INPUT_VERSION": "latest"}, "latest requires allow-latest=true"),
        ({"INPUT_VERSION": "--index-url=https://evil.invalid"}, "closed PEP 440-style"),
        ({"INPUT_VERSION": "https://evil.invalid/pkg.whl"}, "closed PEP 440-style"),
        ({"INPUT_VERSION": "git+https://evil.invalid/repo.git"}, "closed PEP 440-style"),
        ({"INPUT_VERSION": "@requirements.txt"}, "closed PEP 440-style"),
        ({"INPUT_VERSION": "13.10.0 --no-deps"}, "closed PEP 440-style"),
        ({"INPUT_VERSION": "13.10.0+local"}, "closed PEP 440-style"),
        ({"INPUT_VERSION": "13.10.0\nallow-latest=true"}, "line breaks are not allowed"),
        ({"INPUT_ALLOW_LATEST": "yes"}, "expected true or false"),
        ({"INPUT_COMMANDS": "health;id"}, "command names must match"),
        ({"INPUT_COMMANDS": "health --json"}, "command names must match"),
        ({"INPUT_SARIF_COMMANDS": "health $(id)"}, "command names must match"),
        ({"INPUT_CHANGED_DEPTH": "33"}, "expected 0..32"),
        ({"INPUT_CHANGED_DEPTH": "1+1"}, "unsigned decimal integer"),
        ({"INPUT_BASE_REF": "--upload-pack=evil"}, "expected a ref/SHA"),
        ({"INPUT_BASE_REF": "main..evil"}, "forbidden Git ref component"),
        ({"INPUT_SARIF_CATEGORY": "roam code"}, "bounded path-like category"),
        ({"INPUT_SARIF_MAX_RUNS": "101"}, "expected 1..100"),
        ({"INPUT_SARIF_MAX_RESULTS": "0"}, "expected 1..100000"),
        ({"INPUT_SARIF_MAX_BYTES": "999999999"}, "expected 1024..100000000"),
        ({"INPUT_GATE": "health_score>=60; id"}, "unsupported expression grammar"),
        ({"INPUT_GATE": "$(id)>=0"}, "unsupported expression grammar"),
        ({"INPUT_PYTHON_VERSION": "3.x"}, "expected Python 3.10 through 3.13"),
        ({"INPUT_PYTHON_VERSION": "3.14"}, "expected Python 3.10 through 3.13"),
        ({"PR_BASE_SHA_INPUT": "main"}, "expected an empty, SHA-1, or SHA-256"),
    ],
)
def test_validator_rejects_injection_and_out_of_bounds_values(
    tmp_path: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    result = _run_validator(tmp_path, **overrides)

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert message in result.stderr


def test_command_lists_are_consumed_as_validated_arrays() -> None:
    analysis = _step(step_id="run-analysis")["run"]
    sarif = _step(step_id="generate-sarif")["run"]

    assert 'for CMD in "${COMMAND_LIST[@]}"' in analysis
    assert "for CMD in ${COMMANDS}" not in analysis
    assert 'for CMD in "${ANALYSIS_COMMAND_LIST[@]}"' in sarif
    assert 'for CMD in "${REQUESTED_SARIF_COMMAND_LIST[@]}"' in sarif
    assert "for CMD in ${ANALYSIS_COMMANDS}" not in sarif
    assert "for CMD in ${REQUESTED_SARIF_COMMANDS}" not in sarif


def test_generated_step_outputs_are_closed_before_github_output_emission() -> None:
    analysis = _step(step_id="run-analysis")["run"]
    gate = _step(step_id="quality-gate")["run"]

    assert "health score is not a bounded decimal" in analysis
    assert "health score is outside 0..100" in analysis
    assert '[[ ! "${GATE_RESULT}" =~ ^(true|false)$ ]]' in gate
    assert "Gate evaluator returned an unexpected result" in gate


def test_soft_failure_sites_are_closed_and_explicitly_advisory() -> None:
    text = ACTION_PATH.read_text(encoding="utf-8")
    assert text.count("set +e") == 4
    for captured_status in ("PLAN_EXIT=$?", "CMD_EXIT=$?", "GUARD_EXIT=$?"):
        assert captured_status in text

    upload = _step(name="Upload SARIF")
    assert upload["continue-on-error"] is True
    assert text.count("continue-on-error: true") == 1
    assert "fork PRs and repositories without Code Scanning" in text


def test_pin_helper_covers_composite_actions_without_cross_repo_replacement() -> None:
    text = PIN_SCRIPT.read_text(encoding="utf-8")
    executable = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))

    assert ".github/workflows/*.yml .github/workflows/*.yaml action.yml" in text
    assert "awk '{print $2}'" not in executable
    assert "escaped_ref=" in text
    assert "s|${escaped_ref}|${pinned}|g" in text
    assert "Replacing a bare ``@v4``" in text
    assert "globally can pin unrelated actions" in text
