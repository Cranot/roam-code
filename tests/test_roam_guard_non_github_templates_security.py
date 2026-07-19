from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
TEMPLATE_DIR = ROOT / "templates" / "examples"

CIRCLECI = TEMPLATE_DIR / "roam-guard-pr.circleci.yml"
GITLAB = TEMPLATE_DIR / "roam-guard-pr.gitlab-ci.yml"
BITBUCKET = TEMPLATE_DIR / "roam-guard-pr.bitbucket-pipelines.yml"

EXPECTED_IMAGES = {
    CIRCLECI: ("cimg/python:3.12.13@sha256:9c796c23c84e84a66a964acb508d39dc5433c81a47e07efd56dccbbc2427e07c"),
    GITLAB: ("python:3.12.13-bookworm@sha256:9bed8554e926c07c6f908841d5ee88c33e8df9236b191526bbce81a9062ab43a"),
    BITBUCKET: ("python:3.12.13-bookworm@sha256:9bed8554e926c07c6f908841d5ee88c33e8df9236b191526bbce81a9062ab43a"),
}


def _load(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    node = yaml.compose(text)
    assert node is not None, path
    _assert_unique_yaml_keys(node, path=path)
    payload = yaml.safe_load(text)
    assert isinstance(payload, dict), path
    return payload


def _assert_unique_yaml_keys(node: yaml.Node, *, path: Path) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[tuple[str, str]] = set()
        for key, value in node.value:
            assert isinstance(key, yaml.ScalarNode), (path, "complex mapping key")
            identity = (key.tag, key.value)
            assert identity not in seen, (path, f"duplicate YAML key: {key.value}")
            seen.add(identity)
            _assert_unique_yaml_keys(value, path=path)
    elif isinstance(node, yaml.SequenceNode):
        for value in node.value:
            _assert_unique_yaml_keys(value, path=path)


def _image(path: Path, payload: dict) -> str:
    if path == CIRCLECI:
        return payload["jobs"]["roam-guard"]["docker"][0]["image"]
    if path == GITLAB:
        return payload["roam-guard"]["image"]
    return payload["image"]


def _shell(path: Path, payload: dict) -> str:
    if path == CIRCLECI:
        steps = payload["jobs"]["roam-guard"]["steps"]
        return "\n".join(step["run"]["command"] for step in steps if isinstance(step, dict) and "run" in step)
    if path == GITLAB:
        job = payload["roam-guard"]
        return "\n".join([*job["before_script"], *job["script"]])
    step = payload["pipelines"]["pull-requests"]["**"][0]["step"]
    return "\n".join(step["script"])


def _artifacts(path: Path, payload: dict) -> str:
    if path == CIRCLECI:
        steps = payload["jobs"]["roam-guard"]["steps"]
        stores = [step["store_artifacts"]["path"] for step in steps if "store_artifacts" in step]
        return "\n".join(stores)
    if path == GITLAB:
        return "\n".join(payload["roam-guard"]["artifacts"]["paths"])
    step = payload["pipelines"]["pull-requests"]["**"][0]["step"]
    return "\n".join(step["artifacts"])


def _templates() -> list[tuple[Path, dict, str]]:
    return [(path, payload := _load(path), _shell(path, payload)) for path in EXPECTED_IMAGES]


def test_templates_parse_and_pin_verified_multiarch_oci_indexes() -> None:
    for path, expected in EXPECTED_IMAGES.items():
        payload = _load(path)
        image = _image(path, payload)

        assert image == expected
        assert re.fullmatch(r"[^\s@:]+(?:/[^\s@:]+)*:\d+\.\d+\.\d+(?:-bookworm)?@sha256:[0-9a-f]{64}", image)

        text = path.read_text(encoding="utf-8")
        assert "The image tag is retained for readability" in text
        assert "recomputed" in text
        assert "2026-07-18" in text


def test_templates_install_only_exact_roam_release_in_an_isolated_venv() -> None:
    for path, _payload, shell in _templates():
        install_lines = [line.strip() for line in shell.splitlines() if " pip " in line and " install" in line]

        assert len(install_lines) == 1, path
        assert "--isolated --disable-pip-version-check install" in install_lines[0]
        assert "--no-input --no-cache-dir --only-binary=:all: 'roam-code==13.10.0'" in shell
        assert shell.count("roam-code==13.10.0") == 1
        assert "pip check" in shell
        assert "actual == '13.10.0'" in shell
        assert "python -I -m venv .roam-guard-venv" in shell
        python_invocations = [line for line in shell.splitlines() if re.search(r"(?:^|/)python\s", line)]
        assert python_invocations
        assert all(re.search(r"(?:^|/)python -I ", line) for line in python_invocations), path
        roam_invocations = [line for line in shell.splitlines() if ".roam-guard-venv/bin/roam " in line]
        assert len(roam_invocations) == 3
        assert all("bin/python -I .roam-guard-venv/bin/roam" in line for line in roam_invocations)
        assert "PATH='/usr/local/bin:/usr/bin:/bin'" in shell
        assert "export PATH" in shell
        expected_path_boundaries = 2 if path == CIRCLECI else 1
        assert shell.count("PATH='/usr/local/bin:/usr/bin:/bin'") == expected_path_boundaries
        assert shell.count("export PATH") == expected_path_boundaries
        assert re.search(r"pip[^\n]*install[^\n]*\broam-code(?:\s|$)", shell) is None
        assert re.search(r"roam-code\s*(?:>=|~=|>|<|\*)", shell) is None


def test_required_guard_evidence_fails_closed_and_preserves_verdict_status() -> None:
    for path, payload, shell in _templates():
        init_at = shell.index(".roam-guard-venv/bin/roam init --quiet")
        guard_at = shell.index(".roam-guard-venv/bin/roam guard-pr --ci")
        evidence_at = shell.index("if [ ! -s roam-guard-artifacts/guard.md ]; then")
        sarif_at = shell.index(".roam-guard-venv/bin/roam proof-bundle")
        exit_at = shell.rindex('exit "$guard_status"')

        assert init_at < guard_at < evidence_at < sarif_at < exit_at, path
        assert "guard_status=$?" in shell
        assert "ERROR: Roam Guard did not produce required non-empty guard.md evidence" in shell
        assert "text.startswith('## ') and 'Roam Guard verdict:' in text" in shell
        assert "ERROR: guard.md is not a structurally valid Roam Guard verdict" in shell
        assert "set +e" in shell
        assert "set -e" in shell
        assert "allow_failure" not in shell
        assert "roam-guard-artifacts" in _artifacts(path, payload)

        if path == GITLAB:
            job = payload["roam-guard"]
            assert job["allow_failure"] is False
            assert job["artifacts"]["when"] == "always"


def test_sarif_is_validated_and_explicitly_advisory_without_silent_success() -> None:
    for path, _payload, shell in _templates():
        assert "ADVISORY: SARIF is supplemental and does not affect the Guard gate" in shell
        assert "sarif_status=$?" in shell
        assert "payload.get('version') == '2.1.0'" in shell
        assert "driver.get('name') == 'roam-guard'" in shell
        assert "isinstance(first.get('results'), list)" in shell
        assert "SARIF generation or validation failed with status $sarif_status" in shell
        assert "rm -f -- roam-guard-artifacts/guard.sarif" in shell
        assert re.search(r"\|\|\s*(?:true|:)(?:\s|$)", shell) is None, path
        assert 'exit "$sarif_status"' not in shell


def test_shell_blocks_do_not_interpolate_ci_templates_or_execute_dynamic_code() -> None:
    forbidden = (
        "${{",
        "<< parameters.",
        "$CI_",
        "${CI_",
        "$BITBUCKET_",
        "${BITBUCKET_",
        "$(",
        "`",
        "eval ",
        "sh -c",
        "bash -c",
        "envsubst",
    )
    allowed_variables = {"guard_status", "sarif_status"}

    for path, _payload, shell in _templates():
        for token in forbidden:
            assert token not in shell, (path, token)

        referenced_variables = set(re.findall(r"\$(?!\?)([A-Za-z_][A-Za-z0-9_]*)", shell))
        assert referenced_variables <= allowed_variables, (path, referenced_variables)


def test_templates_reserve_output_paths_and_do_not_reuse_dependency_caches() -> None:
    for path, _payload, shell in _templates():
        assert "[ -e .roam-guard-venv ] || [ -L .roam-guard-venv ]" in shell
        assert "[ -e roam-guard-artifacts ] || [ -L roam-guard-artifacts ]" in shell
        assert "reserved path .roam-guard-venv already exists" in shell
        assert "reserved path roam-guard-artifacts already exists" in shell
        assert "--no-cache-dir" in shell

        text = path.read_text(encoding="utf-8")
        assert re.search(r"^\s*caches:\s*$", text, flags=re.MULTILINE) is None
