"""Keep copied CI examples aligned with the executable Action contract."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from roam.cli import _SARIF_CONSUMERS
from scripts.sync_surface_counts import _published_version
from tests._helpers.repo_root import repo_root
from tests.test_composite_action_security import _bash_executable


def _doc() -> str:
    return (repo_root() / "docs/ci-integration.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("heading,field", [("Inputs", "inputs"), ("Outputs", "outputs")])
def test_ci_reference_names_match_public_action_contract(heading, field):
    action = yaml.safe_load((repo_root() / "action.yml").read_text(encoding="utf-8"))
    section = _doc().split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    names = re.findall(r"^\| `([^`]+)` \|", section, re.MULTILINE)
    assert len(names) == len(set(names)), "duplicate reference rows"
    assert set(names) == set(action[field])


def _action_steps(value):
    if isinstance(value, list):
        for item in value:
            yield from _action_steps(item)
    elif isinstance(value, dict):
        if str(value.get("uses", "")).startswith("Cranot/roam-code@"):
            yield value
        for item in value.values():
            yield from _action_steps(item)


@pytest.mark.parametrize("document", ["README.md", "docs/ci-integration.md"])
def test_every_copied_action_example_pins_the_package_independently(document):
    content = (repo_root() / document).read_text(encoding="utf-8")
    examples = re.findall(r"```ya?ml\n(.*?)\n```", content, re.DOTALL)
    steps = [step for example in examples for step in _action_steps(yaml.safe_load(example))]
    assert steps, "the guide must include a runnable Action example"
    for step in steps:
        assert str(step.get("with", {}).get("version", "")) == _published_version(), step


@pytest.mark.parametrize("generator", ["ci-setup", "init"])
def test_generated_ci_example_pins_package_independently(generator):
    from roam.commands.cmd_ci_setup import _load_template
    from roam.commands.cmd_init import _GITHUB_WORKFLOW

    template = _load_template("github") if generator == "ci-setup" else _GITHUB_WORKFLOW
    steps = list(_action_steps(yaml.safe_load(template)))
    assert len(steps) == 1
    assert str(steps[0].get("with", {}).get("version", "")) == _published_version()


def test_documented_cli_sarif_list_matches_the_cli_registry():
    section = _doc().split("### Commands that emit SARIF\n", 1)[1].split("### Upload", 1)[0]
    match = re.search(r"The current set \(alphabetical\):\s*```\s*(.*?)\s*```", section, re.DOTALL)
    assert match, "keep the CLI list discoverable separately from the Action's auto-upload subset"
    commands = [value.strip() for value in match.group(1).replace("\n", " ").split(",")]
    assert commands == list(_SARIF_CONSUMERS)


@pytest.mark.parametrize("state", ["clean", "modified", "untracked", "hidden_untracked", "git_failure"])
def test_site_deploy_requires_a_clean_identified_checkout(tmp_path, state):
    """Execute the real recipe with a harmless npx stub; never publish a site."""
    text = (repo_root() / "Makefile").read_text(encoding="utf-8")
    block = text.split("site-deploy:\n", 1)[1].split("\n\n", 1)[0]
    recipe = "\n".join(line.removeprefix("\t").removeprefix("@") for line in block.splitlines())
    # Make expands $$ into a literal shell dollar before executing a recipe.
    recipe = recipe.replace("$$", "$")
    project = tmp_path / "project"
    project.mkdir()
    (project / "source.txt").write_text("initial\n", encoding="utf-8")
    for args in (
        ["init", "--quiet"],
        ["add", "source.txt"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "Fixture"],
    ):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    if state == "modified":
        (project / "source.txt").write_text("changed\n", encoding="utf-8")
    elif state in {"untracked", "hidden_untracked"}:
        (project / "new.txt").write_text("new\n", encoding="utf-8")
        if state == "hidden_untracked":
            subprocess.run(["git", "config", "status.showUntrackedFiles", "no"], cwd=project, check=True)
    capture = tmp_path / "publish-arguments.txt"
    stub = 'npx() { printf "%s\\n" "$*" > "$SITE_DEPLOY_CAPTURE"; }\n'
    if state == "git_failure":
        stub += "git() { return 7; }\n"
    process = subprocess.run(
        [_bash_executable(), "-c", stub + recipe],
        cwd=project,
        env={**os.environ, "SITE_DEPLOY_CAPTURE": Path(capture).as_posix()},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if state != "clean":
        assert process.returncode == (7 if state == "git_failure" else 1), process.stderr
        assert not capture.exists(), "a refused deployment must never call the publisher"
    else:
        assert process.returncode == 0, process.stderr
        arguments = capture.read_text(encoding="utf-8")
        assert "--commit-dirty=false" in arguments
        assert f"--commit-hash={sha}" in arguments
