"""Tests for `roam fleet plan` + `roam fleet verify` (C.1).

Two surfaces:

* :mod:`roam.fleet.manifest` — the planner that wraps a partition into
  the canonical fleet envelope.
* :mod:`roam.fleet.adapters` — render the envelope for external
  orchestrators (Composio, Copilot CLI, raw).

CLI smoke tests run end-to-end against the standard `python_project`
fixture.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.fleet.adapters import ADAPTERS, to_composio, to_copilot_cli, to_raw
from roam.fleet.manifest import _slugify, build_fleet_manifest
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Manifest builder — pure unit tests
# ---------------------------------------------------------------------------


def _stub_partition_manifest() -> dict:
    """Hand-built partition manifest matching the cmd_partition output shape."""
    return {
        "verdict": "3 partitions; 2 hotspots",
        "agent_count": 3,
        "partitions": [
            {
                "id": 1,
                "agent": "Worker-1",
                "role": "Auth Layer",
                "files": ["src/auth/login.py", "src/auth/session.py"],
                "file_count": 2,
                "complexity": 12.0,
                "test_coverage": 0.85,
                "conflict_risk": "LOW",
                "key_symbols": [
                    {"name": "UserSession", "kind": "class", "file": "src/auth/session.py"},
                    {"name": "handle_login", "kind": "function", "file": "src/auth/login.py"},
                ],
            },
            {
                "id": 2,
                "agent": "Worker-2",
                "role": "Billing",
                "files": ["src/billing/invoice.py"],
                "file_count": 1,
                "complexity": 6.0,
                "test_coverage": 0.60,
                "conflict_risk": "MEDIUM",
                "key_symbols": [
                    {"name": "Invoice", "kind": "class", "file": "src/billing/invoice.py"},
                ],
            },
            {
                "id": 3,
                "agent": "Worker-3",
                "role": "Tests",
                "files": ["tests/test_auth.py"],
                "file_count": 1,
                "complexity": 3.0,
                "test_coverage": 1.0,
                "conflict_risk": "LOW",
                "key_symbols": [],
            },
        ],
        "merge_order": [3, 1, 2],
        "conflict_hotspots": [{"file": "src/auth/login.py", "conflict_score": 0.4}],
        "overall_conflict_probability": 0.18,
        "dependencies": [{"from": 2, "to": 1}],
    }


class TestBuildFleetManifest:
    def test_envelope_shape(self):
        env = build_fleet_manifest(_stub_partition_manifest(), goal="refactor auth")
        assert env["schema"] == "roam-fleet/v1"
        assert env["goal"] == "refactor auth"
        assert env["agent_count"] == 3
        assert len(env["tasks"]) == 3
        for t in env["tasks"]:
            assert "task_id" in t
            assert "title" in t
            assert "description" in t
            assert "file_scope" in t
            assert "conflict_risk" in t
            assert t["suggested_branch"]

    def test_branch_prefix_is_slugged(self):
        env = build_fleet_manifest(_stub_partition_manifest(), goal="x", branch_prefix="myfleet")
        slugs = {t["suggested_branch"] for t in env["tasks"]}
        assert "myfleet/1-auth-layer" in slugs
        assert "myfleet/2-billing" in slugs
        assert "myfleet/3-tests" in slugs

    def test_empty_goal_falls_back(self):
        env = build_fleet_manifest(_stub_partition_manifest(), goal="")
        assert "no goal supplied" in env["goal"].lower()

    def test_no_partitions_yields_no_tasks(self):
        manifest = {**_stub_partition_manifest(), "partitions": []}
        env = build_fleet_manifest(manifest, goal="x")
        assert env["tasks"] == []
        assert env["agent_count"] == 0

    def test_pagerank_anchors_capped_at_five(self):
        manifest = _stub_partition_manifest()
        manifest["partitions"][0]["key_symbols"] = [
            {"name": f"sym{i}", "kind": "fn", "file": "x.py"} for i in range(20)
        ]
        env = build_fleet_manifest(manifest, goal="x")
        assert len(env["tasks"][0]["pagerank_anchors"]) == 5


class TestSlugify:
    def test_lowercases_and_replaces_spaces(self):
        assert _slugify("Auth Layer") == "auth-layer"

    def test_strips_punctuation(self):
        assert _slugify("Re-Factor!! Auth // Layer") == "re-factor-auth-layer"

    def test_empty_falls_back_to_worker(self):
        assert _slugify("") == "worker"
        assert _slugify("!!!") == "worker"


# ---------------------------------------------------------------------------
# Adapter unit tests
# ---------------------------------------------------------------------------


class TestAdapters:
    def setup_method(self):
        self.envelope = build_fleet_manifest(_stub_partition_manifest(), goal="ship feature")

    def test_to_raw_is_pass_through(self):
        out = to_raw(self.envelope)
        assert out == self.envelope
        assert out is not self.envelope  # defensive copy

    def test_to_composio_shape(self):
        out = to_composio(self.envelope)
        assert out["version"] == "composio.v1"
        assert out["workspace_goal"] == "ship feature"
        assert isinstance(out["agents"], list)
        for a in out["agents"]:
            for key in ("name", "goal", "allowed_paths", "depends_on", "constraints"):
                assert key in a

    def test_composio_dependencies_follow_merge_order(self):
        """In our stub, merge_order is [3, 1, 2] — task-2 depends on
        task-3 and task-1."""
        out = to_composio(self.envelope)
        by_name = {a["name"]: a for a in out["agents"]}
        assert by_name["task-3"]["depends_on"] == []
        assert by_name["task-1"]["depends_on"] == ["task-3"]
        assert sorted(by_name["task-2"]["depends_on"]) == ["task-1", "task-3"]

    def test_to_copilot_cli_shape(self):
        out = to_copilot_cli(self.envelope)
        assert out["version"] == "copilot-cli.fleet.v1"
        assert out["goal"] == "ship feature"
        assert len(out["worktrees"]) == 3
        for w in out["worktrees"]:
            assert "description" in w
            assert "worktree_branch" in w
            assert "files" in w
            assert "labels" in w

    def test_adapter_registry_complete(self):
        for key in ("raw", "composio", "copilot"):
            assert key in ADAPTERS, f"adapter {key} missing"
            assert callable(ADAPTERS[key])


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def fleet_project(tmp_path):
    proj = _make_project(
        tmp_path,
        {
            "auth.py": """
                class UserSession:
                    def refresh(self):
                        return self.token
                def handle_login(user):
                    return UserSession()
            """,
            "billing.py": """
                class Invoice:
                    def total(self):
                        return self.amount
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


class TestFleetCLI:
    def test_plan_smoke(self, fleet_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "plan", "split auth", "--n-agents", "2"])
        assert result.exit_code == 0, result.output
        assert "VERDICT:" in result.output
        assert "task-" in result.output

    def test_plan_json_envelope(self, fleet_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "fleet", "plan", "split auth", "--n-agents", "2"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "fleet-plan"
        assert "summary" in data
        assert "fleet" in data
        assert data["fleet"]["schema"] == "roam-fleet/v1"

    def test_plan_writes_to_output(self, fleet_project, tmp_path):
        out = tmp_path / "fleet.json"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "fleet",
                "plan",
                "split auth",
                "--n-agents",
                "2",
                "--output",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["schema"] == "roam-fleet/v1"
        assert loaded["agent_count"] >= 1

    def test_plan_composio_adapter(self, fleet_project, tmp_path):
        out = tmp_path / "composio.json"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "fleet",
                "plan",
                "x",
                "--n-agents",
                "2",
                "--adapter",
                "composio",
                "--output",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["version"] == "composio.v1"
        assert "agents" in loaded

    def test_plan_copilot_adapter(self, fleet_project, tmp_path):
        out = tmp_path / "copilot.json"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "fleet",
                "plan",
                "x",
                "--n-agents",
                "2",
                "--adapter",
                "copilot",
                "--output",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["version"] == "copilot-cli.fleet.v1"
        assert "worktrees" in loaded

    def test_verify_finds_overlap(self, fleet_project, tmp_path):
        # Build a manifest with overlapping file_scope on purpose.
        manifest = {
            "schema": "roam-fleet/v1",
            "tasks": [
                {"task_id": "task-1", "file_scope": ["src/a.py", "src/b.py"]},
                {"task_id": "task-2", "file_scope": ["src/b.py", "src/c.py"]},
            ],
        }
        path = tmp_path / "fleet.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "verify", str(path)])
        assert result.exit_code == 0, result.output
        assert "1 cross-task overlap" in result.output
        assert "src/b.py" in result.output

    def test_verify_clean_manifest(self, fleet_project, tmp_path):
        manifest = {
            "schema": "roam-fleet/v1",
            "tasks": [
                {"task_id": "task-1", "file_scope": ["src/a.py"]},
                {"task_id": "task-2", "file_scope": ["src/b.py"]},
            ],
        }
        path = tmp_path / "fleet.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "verify", str(path)])
        assert result.exit_code == 0, result.output
        assert "0 cross-task overlap" in result.output

    def test_verify_rejects_invalid_json(self, fleet_project, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("not json", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "verify", str(path)])
        assert result.exit_code != 0
