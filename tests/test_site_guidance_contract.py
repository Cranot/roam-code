"""Known website instruction and offer regressions, not a prose-quality score."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest

from tests.test_homepage_contract import SITE, HomepageParser, normalized


@pytest.mark.parametrize("name", ["sow-pr-replay.md", "README.md"])
def test_review_credit_waits_for_general_availability(name):
    text = (SITE.parents[1] / "legal" / name).read_text(encoding="utf-8")
    assert "general availability" in text
    assert not re.search(r"60\s+(?:calendar\s+)?days\s+(?:after|of)\s+(?:report\s+)?delivery", text)
    assert all(amount in text for amount in ("$2,500", "$6,000", "$1,250", "$3,000"))


@pytest.mark.parametrize(
    "path",
    [
        SITE / "refund.html",
        SITE.parents[1] / "legal" / "sow-pr-replay.md",
        SITE.parents[1] / "legal" / "nda-mutual.md",
    ],
)
def test_consumer_dispute_link_does_not_offer_the_closed_odr_platform(path):
    text = path.read_text(encoding="utf-8")
    assert "https://ec.europa.eu/consumers/odr" not in text
    assert "https://consumer-redress.ec.europa.eu/site-relocation_en" in text
    assert "20 July 2025" in text


@pytest.mark.parametrize("name", ["docs/mcp-usage.html", "docs/integration-tutorials.html"])
def test_config_templates_are_not_claimed_as_connected_tool_discovery(name):
    page = HomepageParser((SITE / name).read_text(encoding="utf-8"))
    text = normalized(next(page.root.find("main")).text())
    assert "reads the live registry" not in text
    assert "reads the live tool registry" not in text
    assert "connected" in text and "preset" in text and "restart" in text


def test_cold_start_example_retains_the_producer_error_signal():
    import json

    page = HomepageParser((SITE / "docs/mcp-usage.html").read_text(encoding="utf-8"))
    examples = [
        json.loads(node.text())
        for node in page.root.find("code")
        if node.text().lstrip().startswith("{") and '"index_not_built"' in node.text()
    ]
    assert len(examples) == 1
    assert examples[0]["isError"] is True
    assert examples[0]["status"] == "index_not_built"


@pytest.mark.parametrize("name", ["docs/mcp-usage.html", "docs/integration-tutorials.html"])
def test_paid_report_footer_does_not_sell_the_five_commit_sample(name):
    page = HomepageParser((SITE / name).read_text(encoding="utf-8"))
    text = normalized(next(page.root.find("main")).text())
    assert "last 5 PRs" not in text
    assert "5-PR replay" not in text
    assert "HEAD~5..HEAD" in text


def _code_blocks(name):
    page = HomepageParser((SITE / name).read_text(encoding="utf-8"))
    return "\n".join(node.text() for node in page.root.find("pre"))


def test_worked_example_binds_run_and_collects_before_review():
    code = _code_blocks("docs/canonical-demo.html")
    ordered = [
        "roam --json runs start",
        "$env:ROAM_RUN_ID",
        "roam --json pr-bundle init",
        "roam --json context",
        "roam --json preflight",
        "roam --json impact",
        "roam --json clones --persist",
        "roam --json critique --working-tree",
        "roam --json pr-bundle emit --strict",
        "roam --json runs end --run-id",
        "roam --json runs verify",
    ]
    positions = [code.index(command) for command in ordered]
    assert positions == sorted(positions)
    assert "--status failed" in code


def test_integration_run_verification_requires_explicit_scope():
    code = _code_blocks("docs/integration-tutorials.html")
    lines = [line.split("#", 1)[0].strip() for line in code.splitlines() if "runs verify" in line]
    assert lines
    assert all(re.search(r"runs verify\s+(?:<run_id>|--all)$", line) for line in lines)


def test_receipt_export_uses_the_installed_module():
    code = _code_blocks("docs/integration-tutorials.html")
    assert "python -m roam.evidence.mcp_receipt_schema --out mcp-receipt.schema.json" in code
    assert "scripts/export_mcp_receipt_schema.py" not in code


def test_oscal_setup_names_the_actual_generated_assessment_plan():
    text = (SITE / "docs/integration-tutorials.html").read_text(encoding="utf-8")
    assert ".roam/oscal/stub-assessment-plan.json" in text


@pytest.mark.parametrize("name", ["docs/architecture.html", "docs/canonical-demo.html"])
def test_impact_report_is_not_piped_as_a_git_patch(name):
    assert "roam diff | roam critique" not in _code_blocks(name)


def test_customer_email_credit_reminders_wait_for_review_launch():
    text = (SITE.parents[1] / "email" / "customer-journey.md").read_text(encoding="utf-8")
    assert "general availability" in text
    # Preserve an explicit correction such as "not 60 days after report
    # delivery". It states the opposite of the commercial regression.
    compact = " ".join(text.split())
    assert not re.search(r"(?<!not )\b60 (?:calendar )?days (?:after|of) (?:report )?delivery", compact, re.IGNORECASE)


def test_status_routes_to_scoped_reports_without_implying_payment_or_live_monitoring():
    page = HomepageParser((SITE / "status.html").read_text(encoding="utf-8"))
    main = next(page.root.find("main"))
    text = normalized(main.text())
    assert "paid reports over an agreed PR scope" in text
    assert "runs against a single PR" not in text
    assert "ask about a paid report" in text
    assert "This is not a live status feed" in text
    assert "does not run uptime checks" in text
    assert any(
        link.attrs.get("href") == "/setup" and "Set up your agent for free" in normalized(link.text())
        for link in main.find("a")
    )


def _reference():
    return HomepageParser((SITE / "docs/command-reference.html").read_text(encoding="utf-8"))


@pytest.mark.parametrize("command", ["roam algo", "roam complexity"])
def test_homepage_command_action_reaches_specific_guidance(command):
    home = HomepageParser((SITE / "index.html").read_text(encoding="utf-8"))
    actions = [
        node for node in home.root.find("a") if any(normalized(code.text()) == command for code in node.find("code"))
    ]
    assert len(actions) == 1
    target = urlsplit(actions[0].attrs["href"])
    assert target.path == "/docs/command-reference"
    assert target.fragment and target.fragment != "complete-reference"
    matches = [node for node in _reference().elements if node.attrs.get("id") == target.fragment]
    assert len(matches) == 1
    assert command in normalized(matches[0].text())
    if command == "roam algo":
        text = normalized(matches[0].text()).lower()
        assert "candidate" in text and "test" in text and "measure" in text
        assert "preset" in text and "restart" in text


def test_mutate_reference_keeps_preview_and_apply_separate():
    section = next(node for node in _reference().elements if node.attrs.get("id") == "mutate")
    text = normalized(section.text()).lower()
    assert "preview" in text and "default" in text and "--apply" in text
    assert "guarantees" not in text
    assert "zero dangling" not in text
    assert "natural-language refactor request" not in text
    assert "test" in text
    # The example is an explicit supported preview, not natural-language input
    # to a claimed compiler. Read-only help verifies its syntax, not the edit.
    from click.testing import CliRunner

    from roam.cli import cli

    result = CliRunner().invoke(cli, ["mutate", "move", "--help"])
    assert result.exit_code == 0
    assert "--apply" in result.output and "dry-run preview" in result.output


def test_simulate_reference_describes_one_requested_graph_operation():
    section = next(node for node in _reference().elements if node.attrs.get("id") == "simulate")
    text = normalized(section.text()).lower()
    assert "requested" in text and "graph" in text and "metrics" in text
    assert "source files" in text
    assert "gradient descent" not in text and "rank by metric delta" not in text
    assert "best move" not in text and "complexity=84" not in text
    from click.testing import CliRunner

    from roam.cli import cli

    result = CliRunner().invoke(cli, ["simulate", "move", "--help"])
    assert result.exit_code == 0
    assert "SYMBOL TARGET_FILE" in result.output


def test_reference_does_not_claim_unmeasured_workflow_coverage_or_breakage():
    text = (SITE / "docs/command-reference.html").read_text(encoding="utf-8")
    assert "80% of agent workflows" not in text
    assert "Most agents need only these" not in text
    assert "what breaks if" not in text
    assert "without breaking callers" not in text
    assert "Symbols that no test covers" not in text
    assert "OpenVEX-correct" not in text


def test_generated_command_reference_matches_its_current_owner():
    from dev.build_command_reference import _build_appendix

    text = (SITE / "docs/command-reference.html").read_text(encoding="utf-8")
    generated = text.split("<!-- BEGIN auto-reference -->", 1)[1].split("<!-- END auto-reference -->", 1)[0]
    assert generated.strip() == _build_appendix().strip()


def test_reference_metadata_does_not_restore_the_rejected_usage_claim():
    page = _reference()
    descriptions = [
        node.attrs.get("content", "").lower()
        for node in page.root.find("meta")
        if node.attrs.get("name") == "description" or node.attrs.get("property") == "og:description"
    ]
    assert len(descriptions) == 2
    assert all("most-used" not in text and "core verbs" not in text for text in descriptions)


def test_architecture_overview_does_not_claim_universal_observation():
    text = (SITE / "docs/architecture.html").read_text(encoding="utf-8")
    assert "All downstream consumers" not in text
    assert "every <code>--json</code> error path" not in text
    assert "Every sellable Roam report answers" not in text
    assert "for every ledger and bundle file" not in text


def test_getting_started_ci_does_not_use_unstaged_changes_as_pr_scope():
    page = HomepageParser((SITE / "docs/getting-started.html").read_text(encoding="utf-8"))
    section = next(node for node in page.elements if node.attrs.get("id") == "ci-example")
    code = "\n".join(node.text() for node in section.find("pre"))
    assert "test-gaps --changed" not in code
    text = normalized(section.text()).lower()
    assert "whole-repository" in text
    assert "base" in text and "head" in text and "unstaged" in text


def test_first_use_rules_do_not_assume_roams_development_checkout():
    code = _code_blocks("docs/getting-started.html")
    assert "roam rules --rules-dir rules/community" not in code
    assert "roam check-rules" in code


def test_watcher_recovery_names_dependency_and_confirms_startup():
    page = HomepageParser((SITE / "docs/mcp-usage.html").read_text(encoding="utf-8"))
    text = normalized(next(page.root.find("main")).text())
    assert "watchdog" in text
    assert "same Python environment" in text
    assert "file watcher started" in text
    assert "roam index" in text


def test_test_gaps_changed_excludes_committed_changes_in_clean_ci(tmp_path, monkeypatch):
    """Exercise the scope behind the rejected example, plus its valid local case."""
    import json
    import os
    import subprocess

    from click.testing import CliRunner

    from roam.cli import cli
    from roam.commands import cmd_test_gaps
    from roam.commands.changed_files import get_changed_files

    # Use only fixture-owned configuration. The hostile settings exercise
    # the overrides without ever discovering or invoking the user's hooks.
    empty_hooks = tmp_path / "empty-hooks"
    empty_hooks.mkdir()
    hostile_hooks = tmp_path / "hostile-hooks"
    hostile_hooks.mkdir()
    hostile_hook = hostile_hooks / "pre-commit"
    hostile_hook.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    hostile_hook.chmod(0o755)
    fixture_config = tmp_path / "fixture.gitconfig"
    fixture_config.write_text(
        "[commit]\n\tgpgsign = true\n"
        "[gpg]\n\tprogram = deliberately-missing-scope-test-signer\n"
        f'[core]\n\thooksPath = "{hostile_hooks.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fixture_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Environment-injected configuration outranks files, and inherited Git
    # directory/index variables could redirect even a cwd-bound subprocess.
    for name in list(os.environ):
        if name in {
            "GIT_CONFIG",
            "GIT_CONFIG_PARAMETERS",
            "GIT_CONFIG_COUNT",
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_TEMPLATE_DIR",
        } or name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            monkeypatch.delenv(name, raising=False)

    def git(*args):
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={empty_hooks}", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )

    assert git("config", "--global", "--get", "commit.gpgsign").stdout.strip() == "true"
    assert git("config", "--global", "--get", "core.hooksPath").stdout.strip() == hostile_hooks.as_posix()
    git("init")
    source = tmp_path / "service.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    git("add", "service.py")
    git("-c", "user.name=Scope Test", "-c", "user.email=scope@example.invalid", "commit", "-m", "base")
    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    git("add", "service.py")
    git("-c", "user.name=Scope Test", "-c", "user.email=scope@example.invalid", "commit", "-m", "change")
    assert get_changed_files(tmp_path) == []
    assert get_changed_files(tmp_path, commit_range="HEAD~1..HEAD") == ["service.py"]

    monkeypatch.chdir(tmp_path)
    # Scope selection happens before any DB read. Avoid parser acquisition;
    # this fixture establishes the selection boundary, not detector coverage.
    monkeypatch.setattr(cmd_test_gaps, "ensure_index", lambda: None)
    result = CliRunner().invoke(cli, ["--json", "test-gaps", "--changed"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["verdict"] == "No changed files to analyze"
    source.write_text("def value():\n    return 3\n", encoding="utf-8")
    assert get_changed_files(tmp_path) == ["service.py"]
