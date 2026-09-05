from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.verify_uv_runtime import EXPECTED_UV_VERSION
from tests._helpers.repo_root import repo_root

ROOT = repo_root()
WORKFLOW_DIR = ROOT / ".github" / "workflows"

UV_ACTION_SHA = "11f9893b081a58869d3b5fccaea48c9e9e46f990"
UV_VERSION = EXPECTED_UV_VERSION
UV_RUNTIME_GUARD = "python -I -S -B scripts/verify_uv_runtime.py"
EXAMPLE_ACTION_SHA = "decb938c1f114aeff90f8cf5edd9dbbc0224093e"
EXAMPLE_ROAM_VERSION = "14.0.3"

EXPECTED_WORKFLOWS = {
    "actions-pinning.yml",
    "architecture-guardian.yml",
    "cga-attestation.yml",
    "container.yml",
    "dogfood.yml",
    "publish.yml",
    "repo-description-drift.yml",
    "roam-ci.yml",
    "roam.yml",
    "secret-scan.yml",
}

# These workflows execute the source checkout and therefore can consume this
# repository's uv.lock directly. The count is the number of independently
# materialized Python environments in each file.
LOCKED_SOURCE_WORKFLOWS = {
    "architecture-guardian.yml": 1,
    "cga-attestation.yml": 2,
    "dogfood.yml": 1,
    "secret-scan.yml": 1,
}


def _workflow_paths() -> list[Path]:
    return sorted((*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")))


def _text(name: str) -> str:
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def _executable_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _normalized_executable_text(text: str) -> str:
    executable = "\n".join(_executable_lines(text))
    return re.sub(r"\\\s*\n\s*", " ", executable)


def test_workflow_inventory_is_closed_for_dependency_policy_review() -> None:
    assert {path.name for path in _workflow_paths()} == EXPECTED_WORKFLOWS


def test_all_workflows_pin_runner_images_and_remote_actions() -> None:
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        runners = re.findall(r"^\s*runs-on:\s*([^\s#]+)", text, flags=re.MULTILINE)
        assert runners, path.name
        assert set(runners) == {"ubuntu-24.04"}, (path.name, runners)

        refs = re.findall(r"^\s*(?:-\s+)?uses:\s*([^\s#]+)", text, flags=re.MULTILINE)
        assert refs, path.name
        for ref in refs:
            if ref.startswith("./"):
                continue
            assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", ref), (path.name, ref)


def test_source_checkout_workflows_materialize_only_from_uv_lock() -> None:
    setup_uv = f"astral-sh/setup-uv@{UV_ACTION_SHA}"
    for name, environment_count in LOCKED_SOURCE_WORKFLOWS.items():
        text = _text(name)
        assert text.count(setup_uv) == environment_count, name
        assert text.count(f'version: "{UV_VERSION}"') == environment_count, name
        assert text.count(UV_RUNTIME_GUARD) == environment_count, name
        assert "$(uv --version)" not in text, name
        assert text.count("cache-dependency-glob: uv.lock") == environment_count, name
        assert text.count("uv sync --locked --no-default-groups") == environment_count, name
        assert text.count("uv pip check --python .venv/bin/python") == environment_count, name
        assert text.count('UV_LOCKED: "1"') == 1, name
        assert text.count('UV_NO_PROGRESS: "1"') == 1, name
        assert text.count('UV_PYTHON_DOWNLOADS: "never"') == 1, name
        assert "cache: pip" not in text, name
        assert ".venv/bin/roam" in text, name

        bare_runtime_calls = [
            line
            for line in _executable_lines(text)
            if line != UV_RUNTIME_GUARD and re.match(r"^(?:python(?:\d+(?:\.\d+)?)?|roam)(?:\s|$)", line)
        ]
        assert not bare_runtime_calls, (name, bare_runtime_calls)

    assert "--extra dev" in _text("secret-scan.yml")
    for name in LOCKED_SOURCE_WORKFLOWS.keys() - {"secret-scan.yml"}:
        assert "--extra dev" not in _text(name), name


def test_no_workflow_bypasses_the_lock_with_a_direct_pip_install() -> None:
    for path in _workflow_paths():
        if path.name == "publish.yml":
            continue
        executable = _normalized_executable_text(path.read_text(encoding="utf-8"))
        installs = re.findall(r"\bpip\s+install\b", executable)
        if path.name == "roam-ci.yml":
            assert len(installs) == 1
            assert 'uv pip install --python "${environment}/bin/python" --no-deps "${wheels[0]}"' in executable
        else:
            assert not installs, path.name

    # publish.yml is deliberately outside the source-checkout uv path: its two
    # isolated release-tool installs consume pre-downloaded, hash-checked
    # wheelhouses. Existing provenance tests cover the full recovery protocol.
    publish = _text("publish.yml")
    direct_install_positions = [
        index
        for index, line in enumerate(publish.splitlines())
        if "pip install" in line and "uv pip install" not in line and not line.lstrip().startswith("#")
    ]
    assert len(direct_install_positions) == 2
    publish_lines = publish.splitlines()
    for index in direct_install_positions:
        install_block = "\n".join(publish_lines[index : index + 10])
        assert "--require-hashes" in install_block
        assert "--no-index" in install_block
        assert "--requirement" in install_block


def test_non_release_workflows_have_no_blanket_soft_failure_constructs() -> None:
    for path in _workflow_paths():
        if path.name == "publish.yml":
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in ("continue-on-error: true", "|| true", "set +e"):
            assert forbidden not in text, (path.name, forbidden)

    expected_always = {
        "architecture-guardian.yml": 1,
        "cga-attestation.yml": 1,
        "dogfood.yml": 1,
    }
    for path in _workflow_paths():
        if path.name == "publish.yml":
            continue
        assert path.read_text(encoding="utf-8").count("if: always()") == expected_always.get(path.name, 0)


def test_advisory_findings_and_expected_failures_are_narrowly_encoded() -> None:
    guardian = _text("architecture-guardian.yml")
    assert "findings remain advisory because `roam report` runs without `--strict`" in guardian
    assert 'report.get("command") == "report"' in guardian
    assert "isinstance(failed, int)" in guardian

    dogfood = _text("dogfood.yml")
    assert 'if s.get("partial_success")' in dogfood
    assert "--gate is deliberately omitted" in dogfood
    assert 'report.get("command") == "pr-analyze"' in dogfood
    assert "if-no-files-found: error" in dogfood

    cga = _text("cga-attestation.yml")
    tamper = cga.split("- name: Tamper-detection sanity check", 1)[1].split("- name: Upload offline statement", 1)[0]
    assert "if .venv/bin/roam cga verify" in tamper
    assert "else\n            rc=$?" in tamper
    assert 'if [ "$rc" != "5" ]; then' in tamper
    assert "set +e" not in tamper
    assert "partially emitted statement for incident diagnosis" in cga


def test_downstream_example_pins_the_portable_action_and_package_pair() -> None:
    example = _text("roam.yml")

    assert f"Cranot/roam-code@{EXAMPLE_ACTION_SHA}" in example
    assert f"version: '{EXAMPLE_ROAM_VERSION}'" in example
    assert "cannot consume this repository's" in example
    assert "uv.lock" in example
    assert "pip install" not in "\n".join(_executable_lines(example))


def test_pr_self_analysis_explicitly_installs_the_local_action_source() -> None:
    workflow_text = _text("roam-ci.yml")
    workflow = yaml.safe_load(workflow_text)
    local_action_steps = [
        step for job in workflow["jobs"].values() for step in job.get("steps", []) if step.get("uses") == "./"
    ]

    assert len(local_action_steps) == 1
    assert local_action_steps[0].get("with", {}).get("version") == "source"
    assert "no PyPI fallback" in workflow_text


def _jobs(document: object) -> dict[str, dict]:
    assert isinstance(document, dict)
    return {name: job for name, job in (document.get("jobs") or {}).items() if isinstance(job, dict)}


def _steps(job: dict) -> list[dict]:
    return [step for step in (job.get("steps") or []) if isinstance(step, dict)]


def test_every_workflow_job_declares_least_privilege_permissions() -> None:
    """A job with no `permissions:` block inherits the workflow default, and
    with no workflow default it inherits the repository default -- which on
    some configurations is a read/write GITHUB_TOKEN for every job in the
    repo. Least privilege has to be written down to be true, and the token
    scope a job runs with should be reviewable in the diff that adds the job
    rather than inferred from repository settings.

    This is the sibling of the SHA-pinning assertion above: the pin controls
    WHAT third-party code runs, this controls WHAT THAT CODE CAN DO.
    """
    for path in _workflow_paths():
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        workflow_default = document.get("permissions")
        for job_name, job in _jobs(document).items():
            assert job.get("permissions") is not None or workflow_default is not None, (
                f"{path.name} job '{job_name}' declares no `permissions:` and the workflow sets "
                f"no default. Add the least privilege the job actually needs, e.g. "
                f"`permissions:\n  contents: read`."
            )


def test_no_expression_is_interpolated_into_a_run_script() -> None:
    """`${{ }}` is substituted into the script text BEFORE the shell parses
    it, so an expression carrying attacker-controlled content -- a PR title,
    a branch name, an issue body -- is executed as shell source, with this
    repository's token in the environment. Mapping the value through the
    step's `env:` and referencing it as "$VAR" makes the shell treat it as
    data instead.

    action.yml is already held to this rule by
    tests/test_composite_action_security.py; the repository's own workflows
    were not covered by any gate until this one.
    """
    for path in _workflow_paths():
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in _jobs(document).items():
            for step in _steps(job):
                script = step.get("run")
                if not isinstance(script, str):
                    continue
                expressions = re.findall(r"\$\{\{.*?\}\}", script, flags=re.DOTALL)
                assert not expressions, (
                    f"{path.name} job '{job_name}' step {step.get('name', '<unnamed>')!r} interpolates "
                    f"{expressions} directly into a `run:` block. Map each through the step's `env:` "
                    f'and reference it as "$VAR".'
                )
