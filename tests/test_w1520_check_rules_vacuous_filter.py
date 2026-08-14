"""W1520 -- `check-rules` with a filter that matched nothing reported "no rules matched" and exited 0.

The defect. ``roam check-rules --rule circular-imprts`` (a one-character typo
of ``no-circular-imports``) printed ``VERDICT: no rules matched`` and exited 0.
So did ``--severity critical``, which is vacuous by construction: measured
against every shipped profile, ``severity_rank("critical")`` is 5 and the
highest severity any builtin rule emits is ``error`` at rank 4, so the rank-5
floor selects zero rules under default / strict-security / ai-code-review /
legacy-maintenance / minimal alike. A pipeline running either form measured
nothing and was told it was clean.

This is the same shape ``taint --ci --rule no-such-rule-id`` already refuses
on -- "Nothing was analysed, so nothing is proven clean", exit 5 -- and the
generalisation to its sibling never happened.

The scope that matters. The refusal is conditioned on a FILTER having emptied
the rule set, not on the set being empty. A user who deliberately disabled
every rule in ``.roam-rules.yml``, or whose ``--profile`` resolves to none, has
made an explicit opt-out; that is a measured decision, not an absent
measurement, and turning it red would be the outage. Both directions are
asserted below.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli


def _make_project(tmp_path: Path) -> Path:
    """Minimal indexed project -- enough rows for the builtin rules to run."""
    (tmp_path / ".roam").mkdir()
    conn = sqlite3.connect(tmp_path / ".roam" / "index.db")
    conn.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, loc INTEGER);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT,
            cognitive_complexity INTEGER
        );
        CREATE TABLE file_edges (src_file_id INTEGER, dst_file_id INTEGER);
        CREATE TABLE edges (src_id INTEGER, dst_id INTEGER);
        """
    )
    from roam.db.connection import USER_VERSION

    conn.execute(f"PRAGMA user_version = {USER_VERSION}")  # task #147: pass the open_db version gate
    conn.execute("INSERT INTO files (id, path, loc) VALUES (1, 'src/foo.py', 50)")
    conn.execute("INSERT INTO files (id, path, loc) VALUES (2, 'tests/test_foo.py', 20)")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, cognitive_complexity) VALUES (1, 1, 'my_fn', 'function', 5)"
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def project(tmp_path):
    return _make_project(tmp_path)


# ---------------------------------------------------------------------------
# MUST FIRE -- a filter that emptied the rule set measured nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
def test_typo_rule_filter_refuses_in_every_channel(project, monkeypatch, mode_args):
    monkeypatch.chdir(project)
    result = CliRunner().invoke(
        cli,
        [*mode_args, "check-rules", "--rule", "circular-imprts"],
        catch_exceptions=False,
    )
    assert result.exit_code == 5, f"mode {mode_args or ['text']}: {result.output[:400]}"


@pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
def test_vacuous_severity_filter_refuses_in_every_channel(project, monkeypatch, mode_args):
    """`--severity critical` selects zero rules under every shipped profile.

    No builtin rule emits `critical`; the option's own docstring used to claim
    critical collapses onto a floor matching the emitted `error` rank, which
    is true for `high` (rank 4 == rank 4) and false for `critical` (rank 5).
    """
    monkeypatch.chdir(project)
    result = CliRunner().invoke(
        cli,
        [*mode_args, "check-rules", "--severity", "critical"],
        catch_exceptions=False,
    )
    assert result.exit_code == 5, f"mode {mode_args or ['text']}: {result.output[:400]}"


def test_verdict_names_the_filter_that_emptied_the_set(project, monkeypatch):
    """ "no rules matched" alone does not tell the reader WHICH filter did it."""
    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli, ["check-rules", "--rule", "circular-imprts"], catch_exceptions=False)
    assert "--rule circular-imprts" in result.output, result.output
    assert "nothing is proven clean" in result.output, result.output


def test_json_summary_carries_scan_incomplete_and_state(project, monkeypatch):
    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli, ["--json", "check-rules", "--rule", "circular-imprts"], catch_exceptions=False)
    summary = json.loads(result.output)["summary"]
    assert summary["scan_incomplete"] is True, summary
    assert summary["partial_success"] is True, summary
    assert summary["state"] == "no_rules_matched", summary
    assert summary["filters_applied"] == ["--rule circular-imprts"], summary
    # config_state is a separate, pre-existing axis and must survive.
    assert "config_state" in summary, summary


def test_sarif_channel_emits_sarif_not_text(project, monkeypatch):
    """`--sarif` on this branch used to print `VERDICT: no rules matched`.

    The empty branch had no `if sarif_mode:` arm at all, so the SARIF channel
    silently degraded to text -- unparseable for the CI consumer that asked
    for SARIF.
    """
    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli, ["--sarif", "check-rules", "--rule", "circular-imprts"], catch_exceptions=False)
    doc = json.loads(result.output)
    assert doc["version"] == "2.1.0", doc
    run = doc["runs"][0]
    assert run["results"] == [], run["results"]
    notes = [
        n.get("message", {}).get("text", "")
        for inv in run.get("invocations", [])
        for n in inv.get("toolExecutionNotifications", [])
    ]
    assert any("nothing is proven clean" in n for n in notes), notes


# ---------------------------------------------------------------------------
# MUST NOT FIRE -- an explicit opt-out is a measured decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
def test_no_filter_with_every_rule_disabled_still_exits_zero(project, monkeypatch, mode_args):
    """The outage this guards against.

    Refusing whenever `results` is empty -- rather than whenever a FILTER
    emptied it -- newly fails a user who deliberately disabled every rule in
    .roam-rules.yml. That is an explicit opt-out, not an unmeasured gate.
    """
    from roam.rules.builtin import BUILTIN_RULES

    lines = ["rules:"]
    for rule in BUILTIN_RULES:
        lines.append(f"  - id: {rule.id}")
        lines.append("    enabled: false")
    (project / ".roam-rules.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli, [*mode_args, "check-rules"], catch_exceptions=False)
    assert result.exit_code == 0, f"mode {mode_args or ['text']}: {result.output[:400]}"


def test_no_filter_with_every_rule_disabled_reports_configured_not_incomplete(project, monkeypatch):
    from roam.rules.builtin import BUILTIN_RULES

    lines = ["rules:"]
    for rule in BUILTIN_RULES:
        lines.append(f"  - id: {rule.id}")
        lines.append("    enabled: false")
    (project / ".roam-rules.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli, ["--json", "check-rules"], catch_exceptions=False)
    summary = json.loads(result.output)["summary"]
    assert summary["scan_incomplete"] is False, summary
    assert summary["state"] == "no_rules_configured", summary
    # `_calculate_verdict`'s `total == 0` arm was written for this state and
    # was unreachable from the CLI, because this branch returned first.
    assert summary["verdict"] == "PASS - no rules configured", summary


@pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
def test_filter_that_selects_a_real_rule_still_exits_zero(project, monkeypatch, mode_args):
    """A filter that legitimately selects rules is unaffected."""
    monkeypatch.chdir(project)
    result = CliRunner().invoke(
        cli,
        [*mode_args, "check-rules", "--rule", "no-circular-imports"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"mode {mode_args or ['text']}: {result.output[:400]}"


@pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
def test_severity_high_still_selects_error_rules(project, monkeypatch, mode_args):
    """`--severity high` is NOT vacuous -- rank 4 == the emitted `error` rank.

    Only `critical` (rank 5) sits above everything roam emits. Refusing on
    `high` too would break the documented alias.
    """
    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli, [*mode_args, "check-rules", "--severity", "high"], catch_exceptions=False)
    assert result.exit_code == 0, f"mode {mode_args or ['text']}: {result.output[:400]}"


def test_gate_failure_exit_code_stays_inside_mcp_success_codes():
    """5, not 1 and not 2.

    docs/ci-integration.md defines 5 as "quality gate check failed, OR the
    check could not run at all", and MCP's `_SUCCESS_EXIT_CODES` is {0, 5}.
    Exits 1 or 2 would flip roam_check_rules to isError:true for a state that
    is a gate refusal, not a tool error.
    """
    from roam.exit_codes import EXIT_GATE_FAILURE
    from roam.mcp_server import _SUCCESS_EXIT_CODES

    assert EXIT_GATE_FAILURE == 5
    assert EXIT_GATE_FAILURE in _SUCCESS_EXIT_CODES
