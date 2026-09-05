"""Tests for roam init -- project initialization command."""

from __future__ import annotations

import json
import subprocess

import pytest

from scripts import sync_surface_counts as sync
from tests.conftest import (
    assert_json_envelope,
    git_init,
    invoke_cli,
    parse_json_output,
)

# The release version the generated-CI assertions below are DERIVED from
# (W1501). ``roam init --with-ci=github`` writes a workflow into the USER'S
# repository, so the action ref and ``version:`` input it emits are INSTALL
# instructions: they must name a release that exists, which is the last
# PUBLISHED one and not the declared one. Asserting pyproject here made the
# test agree with a workflow that pinned an untagged version -- green in this
# repo, `Unable to resolve action` in the user's. A frozen literal would be
# worse still. ``scripts/sync_surface_counts.py`` owns the templates.
ROAM_VERSION = sync._published_version()

# ---------------------------------------------------------------------------
# Fixture: a git-init'd project that has NOT been indexed yet.
# init calls ensure_index() internally, so we must NOT pre-index here.
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_project(tmp_path):
    """A git-init'd project with a .gitignore and a Python file.

    Deliberately NOT indexed -- init is responsible for running the index.
    """
    proj = tmp_path / "init_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def main():\n"
        '    """Entry point."""\n'
        '    print("hello")\n'
        "\n\n"
        "class Config:\n"
        '    """Application configuration."""\n'
        "    DEBUG = False\n"
        '    VERSION = "1.0.0"\n'
    )
    (proj / "utils.py").write_text('def helper(x):\n    """A helper function."""\n    return x * 2\n')
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestInitSmoke:
    def test_exits_zero_on_fresh_project(self, cli_runner, fresh_project, monkeypatch):
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0, f"init exited non-zero:\n{result.output}"

    def test_exits_zero_on_already_indexed_project(self, cli_runner, fresh_project, monkeypatch):
        """Running init twice on the same project should still exit 0."""
        monkeypatch.chdir(fresh_project)
        # First run
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0
        # Second run (index already exists, files already created)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# JSON output tests
# ---------------------------------------------------------------------------


class TestInitJSON:
    def test_json_envelope(self, cli_runner, fresh_project, monkeypatch):
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "init")
        assert_json_envelope(data, "init")

    def test_json_summary_has_verdict(self, cli_runner, fresh_project, monkeypatch):
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "init")
        summary = data["summary"]
        assert "verdict" in summary, f"summary missing 'verdict' key: {summary}"
        assert isinstance(summary["verdict"], str)
        assert summary["verdict"]  # non-empty

    def test_json_summary_has_created_and_skipped(self, cli_runner, fresh_project, monkeypatch):
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "init")
        summary = data["summary"]
        assert "created" in summary
        assert "skipped" in summary
        assert isinstance(summary["created"], list)
        assert isinstance(summary["skipped"], list)

    def test_json_command_field_is_init(self, cli_runner, fresh_project, monkeypatch):
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "init")
        assert data["command"] == "init"


# ---------------------------------------------------------------------------
# Text output tests
# ---------------------------------------------------------------------------


class TestInitText:
    def test_verdict_line_present(self, cli_runner, fresh_project, monkeypatch):
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert "VERDICT:" in result.output, f"Expected 'VERDICT:' in output, got:\n{result.output}"

    def test_output_mentions_indexed_or_index(self, cli_runner, fresh_project, monkeypatch):
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        lower = result.output.lower()
        assert "index" in lower or "indexed" in lower, f"Expected 'index' or 'indexed' in output, got:\n{result.output}"

    def test_output_mentions_roam_commands(self, cli_runner, fresh_project, monkeypatch):
        """The welcome text should reference next steps (roam commands)."""
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        # Welcome message lists several roam sub-commands
        assert "roam" in result.output.lower()


# ---------------------------------------------------------------------------
# Side-effect tests: .roam/ directory creation
# ---------------------------------------------------------------------------


class TestInitSideEffects:
    def test_roam_directory_created(self, cli_runner, fresh_project, monkeypatch):
        monkeypatch.chdir(fresh_project)
        roam_dir = fresh_project / ".roam"
        assert not roam_dir.exists() or not (roam_dir / "index.db").exists(), (
            "Precondition: project should not be indexed before init"
        )
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0
        assert roam_dir.exists(), ".roam/ directory not created after init"
        assert roam_dir.is_dir()

    def test_index_db_created(self, cli_runner, fresh_project, monkeypatch):
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0
        db_path = fresh_project / ".roam" / "index.db"
        assert db_path.exists(), f"index.db not created at {db_path}"
        assert db_path.stat().st_size > 0, "index.db is empty"

    def test_fitness_yaml_created(self, cli_runner, fresh_project, monkeypatch):
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0
        fitness = fresh_project / ".roam" / "fitness.yaml"
        assert fitness.exists(), ".roam/fitness.yaml not created by init"

    def test_github_workflow_NOT_created_by_default(self, cli_runner, fresh_project, monkeypatch):
        """Default ``roam init`` must NOT drop CI config in the repo.

        Audit R1: writing ``.github/workflows/roam.yml`` unsolicited
        on first init was the single biggest churn driver — users
        evaluating roam in a private repo got "what is this YAML
        file?" before they'd seen a single useful output. CI generation
        must be explicit opt-in via ``--with-ci=github`` or the
        existing ``roam ci-setup``.
        """
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0
        workflow = fresh_project / ".github" / "workflows" / "roam.yml"
        assert not workflow.exists(), (
            ".github/workflows/roam.yml should NOT be created by default. "
            "It's an unsolicited side-effect — use --with-ci=github to opt in."
        )

    def test_github_workflow_with_explicit_opt_in(self, cli_runner, fresh_project, monkeypatch):
        """--with-ci=github creates the workflow file."""
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init", "--with-ci=github"], cwd=fresh_project)
        assert result.exit_code == 0
        workflow = fresh_project / ".github" / "workflows" / "roam.yml"
        assert workflow.exists(), "--with-ci=github should create the workflow file"
        content = workflow.read_text(encoding="utf-8")
        assert "runs-on: ubuntu-24.04" in content
        assert "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5" in content
        assert "persist-credentials: false" in content
        assert f"Cranot/roam-code@v{ROAM_VERSION}" in content
        assert f'version: "{ROAM_VERSION}"' in content
        assert "@main" not in content
        assert "ubuntu-latest" not in content
        assert "pip install" not in content

    def test_roamignore_template_created(self, cli_runner, fresh_project, monkeypatch):
        """G14: starter .roamignore template is written when absent.
        Every line commented out so the user opts in to what applies.
        """
        monkeypatch.chdir(fresh_project)
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        assert result.exit_code == 0
        roamignore = fresh_project / ".roamignore"
        assert roamignore.exists(), ".roamignore template not created by init"
        content = roamignore.read_text(encoding="utf-8")
        # Every entry commented — user opts in.
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            assert stripped.startswith("#"), f"non-commented entry in template: {line!r}"

    def test_second_init_skips_existing_files(self, cli_runner, fresh_project, monkeypatch):
        """On the second run, already-created config files are skipped."""
        monkeypatch.chdir(fresh_project)
        # First init
        invoke_cli(cli_runner, ["init"], cwd=fresh_project)
        # Second init in JSON mode to inspect skipped list
        result = invoke_cli(cli_runner, ["init"], cwd=fresh_project, json_mode=True)
        data = parse_json_output(result, "init")
        skipped = data["summary"]["skipped"]
        # At least one file should be reported as skipped on the second run
        assert len(skipped) >= 1, f"Expected skipped files on second init, got: {skipped}"


class TestInitGuards:
    """Guards added to prevent accidental misuse on first install."""

    def test_init_refuses_outside_git_repo(self, cli_runner, tmp_path, monkeypatch):
        """G6: ``roam init`` must fail-fast outside a git repository.

        Pre-fix, running init in ``~/Downloads`` (no .git) would walk
        the filesystem from there, drop ``.roam/`` in the user's home,
        and create a confusing "wrong directory" loop. Post-fix:
        actionable refusal before bootstrap writes.
        """
        non_git = tmp_path / "not_a_repo"
        non_git.mkdir()
        (non_git / "app.py").write_text("def x(): pass\n")
        # Deliberately no git_init.
        monkeypatch.chdir(non_git)
        result = invoke_cli(cli_runner, ["init"], cwd=non_git)
        assert result.exit_code != 0
        # Friendly enough that the user knows what to do.
        out = result.output.lower()
        assert "git" in out
        assert "git init" in out or "outside" in out or ".git" in out

    @pytest.mark.parametrize("json_mode", [False, True])
    @pytest.mark.parametrize("marker_kind", ["empty_dir", "empty_file", "garbage_file"])
    @pytest.mark.parametrize("marker_location", ["self", "ancestor"])
    def test_init_refuses_stray_git_markers_without_writes(
        self, cli_runner, tmp_path, monkeypatch, marker_location, marker_kind, json_mode
    ):
        """A rejected root marker cannot authorize bootstrap writes afterward."""
        parent = tmp_path / "stray"
        project = parent / "project"
        project.mkdir(parents=True)
        (project / "app.py").write_text("def entry(): return 1\n", encoding="utf-8")
        marker = (project if marker_location == "self" else parent) / ".git"
        if marker_kind == "empty_dir":
            marker.mkdir()
        else:
            marker.write_text("" if marker_kind == "empty_file" else "not a Git pointer\n", encoding="utf-8")
        monkeypatch.chdir(project)

        # An inherited agent run must not turn a refusal into a sidecar write.
        monkeypatch.setenv("ROAM_RUN_ID", "init-refusal-no-writes")

        result = invoke_cli(cli_runner, ["init"], cwd=project, json_mode=json_mode)

        assert result.exit_code == 2, result.output
        assert "git" in result.output.lower()
        if json_mode:
            data = json.loads(result.stdout)
            assert_json_envelope(data, "init")
            assert data["error_code"] == "FILE_NOT_FOUND"
            assert data["summary"]["state"] == "not_initialized"
            assert data["summary"]["partial_success"] is True
            assert data["created"] == []
        for root in (parent, project):
            assert not (root / ".roam").exists()
            assert not (root / ".roamignore").exists()
            assert not (root / ".github").exists()

    def test_init_refuses_an_unvalidated_child_after_root_resolution(self, cli_runner, tmp_path, monkeypatch):
        """A newly visible ancestor cannot authorize writes at a stale child root."""
        from roam.commands import cmd_init

        parent = tmp_path / "repo"
        project = parent / "child"
        project.mkdir(parents=True)
        (project / "app.py").write_text("def entry(): return 1\n", encoding="utf-8")
        git_init(parent)
        # Model the result of resolving the child before an ancestor became a
        # repository. Bootstrap must validate its actual write destination.
        monkeypatch.setattr(cmd_init, "find_project_root", lambda start: project)
        monkeypatch.chdir(project)

        result = invoke_cli(cli_runner, ["init"], cwd=project)

        assert result.exit_code != 0, result.output
        assert not (project / ".roam").exists()
        assert not (project / ".roamignore").exists()

    @pytest.mark.parametrize("root_kind", ["directory", "worktree"])
    def test_init_from_nested_directory_uses_validated_root(
        self, cli_runner, fresh_project, tmp_path, monkeypatch, root_kind
    ):
        project = fresh_project
        if root_kind == "worktree":
            project = tmp_path / "linked"
            subprocess.run(
                ["git", "-C", str(fresh_project), "worktree", "add", "--detach", str(project), "HEAD"],
                check=True,
                capture_output=True,
            )
            assert (project / ".git").is_file()
        child = project / "nested"
        child.mkdir()
        monkeypatch.chdir(child)

        result = invoke_cli(cli_runner, ["init"], cwd=child, json_mode=True)

        assert result.exit_code == 0, result.output
        assert_json_envelope(parse_json_output(result, "init"), "init")
        assert (project / ".roam" / "index.db").exists()
        assert not (child / ".roam").exists()
