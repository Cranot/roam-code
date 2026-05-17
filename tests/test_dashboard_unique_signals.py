"""Tests for unique-signal discovery promotion in dashboard/understand/audit.

Six commands produce signal not surfaced anywhere else (per
``internal/dogfood/SYNTHESIS-2026-05-12.md`` section "NEW in v3"):
``metrics-push --dry-run`` (danger_score), ``algo``/``math``
(anti-pattern counts), ``ai-ratio`` (ai_generated_percentage),
``ai-readiness`` (ai_readiness_score), ``vibe-check`` (ai_rot_score),
``module`` (cohesion_pct), ``forecast`` (health_score_30d_projection).

Agents never discover them by name. Fix: the discovery-tier commands
(``dashboard``, ``understand``, ``audit``) surface a ``discoverable_via``
hint dict naming each metric -> the roam command that emits it
(LAW 11: server-side hints teaching better tools).

This module exercises that promotion in JSON envelope shape — the
fields agents will read.  Each test is small and targets one
unique-signal command name, so a regression points straight at the
missing hint.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output


# W414b: module-scoped indexed_project override — every test in this file
# is a read-only `roam dashboard`/`understand`/`audit --json` invocation.
# The conftest `indexed_project` is function-scoped (python_project ->
# git_repo -> tmp_path), so without this override we re-index ~17 times.
# Mirrors the W414 pattern in test_dashboard.py.
@pytest.fixture(scope="module")
def indexed_project(tmp_path_factory):
    proj = tmp_path_factory.mktemp("unique_signals_proj")
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()

    (src / "models.py").write_text(
        textwrap.dedent(
            '''\
            class User:
                """A user model."""
                def __init__(self, name, email):
                    self.name = name
                    self.email = email

                def display_name(self):
                    return self.name.title()


            class Admin(User):
                """An admin user."""
                def __init__(self, name, email, role="admin"):
                    super().__init__(name, email)
                    self.role = role
            '''
        ),
        encoding="utf-8",
    )
    (src / "service.py").write_text(
        textwrap.dedent(
            '''\
            from models import User, Admin


            def create_user(name, email):
                user = User(name, email)
                return user


            def unused_helper():
                return 42
            '''
        ),
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(proj), capture_output=True, env=env)

    from roam.cli import cli

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, f"roam index failed:\n{result.output}"
    return proj


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


class TestDashboardUniqueSignals:
    """``roam --json dashboard`` must surface unique-signal hints."""

    def test_dashboard_envelope_has_unique_signals_block(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        assert "unique_signals" in data, (
            f"dashboard envelope must include a unique_signals block; got keys: {sorted(data.keys())}"
        )

    def test_dashboard_envelope_lists_danger_score_hint(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        hints = data["unique_signals"]["discoverable_via"]
        assert hints["danger_score"] == "roam metrics-push --dry-run", (
            f"expected danger_score -> 'roam metrics-push --dry-run', got: {hints.get('danger_score')!r}"
        )

    def test_dashboard_envelope_lists_algo_hint(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        hints = data["unique_signals"]["discoverable_via"]
        assert hints["algo_anti_patterns"] == "roam algo"

    def test_dashboard_envelope_lists_ai_ratio_hint(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        hints = data["unique_signals"]["discoverable_via"]
        assert hints["ai_generated_percentage"] == "roam ai-ratio"
        assert hints["ai_readiness_score"] == "roam ai-readiness"
        assert hints["ai_rot_score"] == "roam vibe-check"

    def test_dashboard_envelope_lists_module_and_forecast_hints(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        hints = data["unique_signals"]["discoverable_via"]
        # Module cohesion takes an argument; the hint should preserve the
        # placeholder so an agent knows to substitute a module name.
        assert hints["module_cohesion_pct"] == "roam module <module>"
        assert hints["health_30d_forecast"] == "roam forecast"

    def test_dashboard_envelope_has_danger_score_top_5(self, cli_runner, indexed_project, monkeypatch):
        """The inline-cheap headline list lives under unique_signals.

        Empty list is a valid signal (no danger-zone files) — the list
        must exist regardless.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        assert "danger_score_top_5" in data["unique_signals"]
        top = data["unique_signals"]["danger_score_top_5"]
        assert isinstance(top, list)
        assert len(top) <= 5
        # Each entry, when present, has the contracted shape.
        for row in top:
            assert "path" in row
            assert "danger_score" in row
            assert isinstance(row["danger_score"], (int, float))

    def test_dashboard_next_steps_lists_unique_signal_commands(self, cli_runner, indexed_project, monkeypatch):
        """``next_steps`` surfaces as ``agent_contract.next_commands``."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        steps = data.get("next_steps") or []
        assert isinstance(steps, list)
        # At least two unique-signal commands must appear so a Haiku-class
        # agent reading the bounded agent_contract block sees them.
        joined = " ".join(steps)
        unique_cmds = ["vibe-check", "ai-readiness", "ai-ratio", "algo", "forecast"]
        hits = [c for c in unique_cmds if c in joined]
        assert len(hits) >= 2, f"expected at least 2 unique-signal commands in next_steps, found {hits}; steps={steps}"

    def test_dashboard_agent_contract_includes_unique_signal_command(self, cli_runner, indexed_project, monkeypatch):
        """The derived ``agent_contract.next_commands`` must surface the hints.

        Verifies the LAW-11 pipeline end-to-end: dashboard emits
        ``next_steps``, the envelope formatter folds it into
        ``agent_contract.next_commands``, agents on tight context budget
        consume it.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dashboard"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "dashboard")
        agent = data.get("agent_contract") or {}
        next_cmds = agent.get("next_commands") or []
        joined = " ".join(next_cmds)
        unique_cmds = ["vibe-check", "ai-readiness", "ai-ratio", "algo", "forecast"]
        hits = [c for c in unique_cmds if c in joined]
        assert len(hits) >= 2, (
            f"agent_contract.next_commands should include >=2 unique-signal hints, "
            f"got hits={hits}; next_commands={next_cmds}"
        )


# ---------------------------------------------------------------------------
# understand
# ---------------------------------------------------------------------------


class TestUnderstandUniqueSignals:
    """``roam --json understand`` must surface unique-signal hints."""

    def test_understand_envelope_has_discoverable_via(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["understand"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "understand")
        assert "discoverable_via" in data, (
            f"understand envelope must include discoverable_via; keys: {sorted(data.keys())}"
        )
        hints = data["discoverable_via"]
        assert hints["danger_score"] == "roam metrics-push --dry-run"
        assert hints["algo_anti_patterns"] == "roam algo"
        assert hints["ai_rot_score"] == "roam vibe-check"

    def test_understand_facts_mention_unique_signal_commands(self, cli_runner, indexed_project, monkeypatch):
        """At least 2 unique-signal commands must reach the agent_contract.

        This is the canonical agent-discovery channel — an agent that
        only reads the bounded ``agent_contract`` block (Haiku, tight
        context) must still find the names.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["understand"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "understand")
        agent = data.get("agent_contract") or {}
        next_cmds = agent.get("next_commands") or []
        joined = " ".join(next_cmds)
        unique_cmds = ["vibe-check", "ai-readiness", "ai-ratio", "algo", "forecast"]
        hits = [c for c in unique_cmds if c in joined]
        assert len(hits) >= 2, (
            f"understand agent_contract.next_commands must include >=2 unique-signal hints; "
            f"hits={hits}; next_commands={next_cmds}"
        )


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


class TestAuditUniqueSignals:
    """``roam --json audit`` must surface unique-signal hints.

    Audit aggregates 7+ signals already; the unique-signal block is
    additive (server-side hints, no replicated output).
    """

    def test_audit_envelope_has_discoverable_via(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["audit"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "audit")
        assert "discoverable_via" in data, f"audit envelope must include discoverable_via; keys: {sorted(data.keys())}"
        hints = data["discoverable_via"]
        assert hints["danger_score"] == "roam metrics-push --dry-run"
        assert hints["module_cohesion_pct"] == "roam module <module>"
        assert hints["health_30d_forecast"] == "roam forecast"

    def test_audit_next_steps_includes_unique_signal_commands(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["audit"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "audit")
        steps = data.get("next_steps") or []
        joined = " ".join(steps)
        unique_cmds = ["vibe-check", "ai-readiness", "ai-ratio", "algo", "forecast"]
        hits = [c for c in unique_cmds if c in joined]
        assert len(hits) >= 2, f"audit next_steps should include >=2 unique-signal commands; hits={hits}; steps={steps}"
