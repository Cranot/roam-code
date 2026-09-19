"""Keep copied CI examples aligned with the executable Action contract."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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


@pytest.mark.parametrize(
    "state",
    [
        "clean",
        "modified",
        "untracked",
        "hidden_untracked",
        "git_failure",
        "ignored_cache",
        "publish_failure",
        "acceptance_failure",
    ],
)
def test_site_deploy_requires_a_clean_identified_checkout(tmp_path, state):
    """Exercise real Git/staging with publisher and HTTP-check stubs; no network."""
    text = (repo_root() / "Makefile").read_text(encoding="utf-8")
    block = text.split("site-deploy:\n", 1)[1].split("\n\n", 1)[0]
    recipe = "\n".join(line.removeprefix("\t").removeprefix("@") for line in block.splitlines())
    # Make expands $$ into a literal shell dollar before executing a recipe.
    recipe = recipe.replace("$$", "$")
    project = tmp_path / "project"
    project.mkdir()
    (project / "source.txt").write_text("initial\n", encoding="utf-8")
    site = project / "templates/distribution/landing-page"
    site.mkdir(parents=True)
    (site / "index.html").write_text("Public site", encoding="utf-8")
    (project / ".gitignore").write_text(".roam/\ninternal/\n", encoding="utf-8")
    (project / "scripts").mkdir()
    (project / "scripts/stage_site.py").write_bytes((repo_root() / "scripts/stage_site.py").read_bytes())
    # HTTP behavior has its own positive/negative controls. Here test the real
    # recipe's argument wiring, ordering and propagation, without public requests.
    (project / "scripts/verify_site_deployment.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "Path(os.environ['SITE_ACCEPTANCE_CAPTURE']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "sys.exit(int(os.environ['SITE_ACCEPTANCE_EXIT']))\n",
        encoding="utf-8",
    )
    for args in (
        ["init", "--quiet"],
        ["add", "source.txt", ".gitignore", "templates", "scripts"],
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
    elif state == "ignored_cache":
        (site / ".roam").mkdir()
        (site / ".roam/private.json").write_text("local analysis only", encoding="utf-8")
    capture = tmp_path / "publish-arguments.txt"
    acceptance = tmp_path / "acceptance-arguments.json"
    stub = (
        'npx() { printf "%s\\n" "$*" > "$SITE_DEPLOY_CAPTURE"; '
        'find "$4" -type f >> "$SITE_DEPLOY_CAPTURE"; return "$SITE_PUBLISH_EXIT"; }\n'
    )
    stub += f'python() {{ "{Path(sys.executable).as_posix()}" "$@"; }}\n'
    if state == "git_failure":
        stub += "git() { return 7; }\n"
    process = subprocess.run(
        [_bash_executable(), "-c", stub + recipe],
        cwd=project,
        env={
            **os.environ,
            "SITE_DEPLOY_CAPTURE": capture.as_posix(),
            "SITE_ACCEPTANCE_CAPTURE": acceptance.as_posix(),
            "SITE_ACCEPTANCE_EXIT": "2" if state == "acceptance_failure" else "0",
            "SITE_PUBLISH_EXIT": "7" if state == "publish_failure" else "0",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    if state not in {"clean", "ignored_cache", "publish_failure", "acceptance_failure"}:
        assert process.returncode == (7 if state == "git_failure" else 1), process.stderr
        assert not capture.exists(), "a refused deployment must never call the publisher"
        assert not acceptance.exists(), "a refused deployment must not claim post-deploy verification"
    else:
        expected_exit = 7 if state == "publish_failure" else 2 if state == "acceptance_failure" else 0
        assert process.returncode == expected_exit, process.stderr
        arguments = capture.read_text(encoding="utf-8")
        assert "--commit-dirty=false" in arguments
        assert f"--commit-hash={sha}" in arguments
        assert "/index.html" in arguments, "the publisher must receive a nonempty public site"
        assert "/internal/site-deploy/" in arguments, "upload the isolated export, not the working site"
        assert "private.json" not in arguments, "ignored local analysis must never enter the upload directory"
        if state == "publish_failure":
            assert not acceptance.exists(), "failed publication must stop before acceptance"
        else:
            checked = json.loads(acceptance.read_text(encoding="utf-8"))
            assert checked[:4] == ["--commit", sha, "--base-url", "https://roam-code.com"]
            assert checked[4] == "--output-dir" and len(checked) == 6
            receipt = Path(checked[5]).resolve()
            assert receipt.name == "acceptance"
            assert receipt.parent.parent == project / "internal/site-deploy"
            assert receipt.parent.name.startswith(sha[:12] + "-")
