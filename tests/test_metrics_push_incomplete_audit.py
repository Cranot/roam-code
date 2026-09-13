"""Unavailable Audit evidence must not become successful published counters."""

from __future__ import annotations

import copy
import json

import click
import pytest
from click.testing import CliRunner

from roam.commands import cmd_audit, cmd_dogfood, cmd_metrics_push
from tests.test_metrics_push import _FAKE_AUDIT_ENVELOPE


@pytest.mark.parametrize("has_tests", [True, False])
def test_real_indexed_audit_reaches_metrics_dry_run_without_a_fake_envelope(tmp_path, monkeypatch, has_tests):
    """Real Git/index/CLI in tmp_path; outbound posting is forbidden by a stub."""
    from roam.cli import cli
    from tests.conftest import git_init, index_in_process

    (tmp_path / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def identity(value):\n    return value\n", encoding="utf-8")
    if has_tests:
        (tmp_path / "test_app.py").write_text(
            "from app import identity\n\ndef test_identity():\n    assert identity(2) == 2\n", encoding="utf-8"
        )
    git_init(tmp_path)
    monkeypatch.chdir(tmp_path)
    output, code = index_in_process(tmp_path)
    assert code == 0, output

    def forbidden_post(*args, **kwargs):
        pytest.fail("Dry-run must never contact a metrics endpoint")

    monkeypatch.setattr(cmd_metrics_push, "_post_metrics", forbidden_post)
    result = CliRunner().invoke(cli, ["--json", "--budget", "0", "metrics-push", "--dry-run"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["summary"]["audit_status"] == ("ok" if has_tests else "failed")
    assert data["summary"]["partial_success"] is (not has_tests)
    if has_tests:
        assert data["payload"]["metrics"]["test_pyramid"]["total"] == 1
        assert data["payload"]["metrics"]["dead_test_only"] == 1
    else:
        assert data["payload"]["metrics"] == {}


@pytest.mark.parametrize("budget,explicit,complete", [(0, False, False), (0, True, True), (200000, True, True)])
def test_metrics_audit_budget_survives_real_cli_serialization(tmp_path, monkeypatch, budget, explicit, complete):
    from roam.cli import cli
    from roam.output.formatter import json_envelope, to_json

    # Controlled producer data, real Click dispatch and formatter/JSON consumer.
    # No network or indexing: tmp_path contains any incidental response state.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROAM_DEFAULT_JSON_BUDGET", "20000")
    source = copy.deepcopy(_FAKE_AUDIT_ENVELOPE)
    source["summary"]["partial_success"] = False
    for section in source["sections"].values():
        section["summary"]["partial_success"] = False
    source["details"] = [{"source": "x" * 160} for _ in range(2000)]
    observed = []

    @click.command("audit")
    @click.pass_context
    def producer(ctx):
        observed.append((ctx.obj["budget"], ctx.obj["budget_explicit"]))
        click.echo(
            to_json(
                json_envelope(
                    "audit", budget=ctx.obj["budget"], persist_response=False, include_index_metadata=False, **source
                )
            )
        )

    original = cli.get_command
    monkeypatch.setattr(cli, "get_command", lambda ctx, name: producer if name == "audit" else original(ctx, name))
    with click.Context(click.Command("metrics-parent"), obj={"budget": budget, "budget_explicit": explicit}):
        envelope = cmd_metrics_push._capture_audit()
    assert observed == [(budget, explicit)]
    assert (cmd_metrics_push._audit_evidence_error(envelope) is None) is complete
    if complete:
        assert len(envelope["details"]) == 2000
    else:
        assert envelope["summary"]["truncation_reason"] == "budget"


@pytest.mark.parametrize("consumer", [cmd_audit._capture, cmd_dogfood._run_subcommand])
@pytest.mark.parametrize("explicit", [False, True])
def test_zero_budget_preserves_explicitness(monkeypatch, consumer, explicit):
    captured = []

    class Result:
        stdout = output = '{"summary": {"verdict": "observed"}}'
        exit_code = 0

    def invoke(self, cli, args):
        captured.extend(args)
        return Result()

    monkeypatch.setattr(CliRunner, "invoke", invoke)
    with click.Context(click.Command("parent"), obj={"budget": 0, "budget_explicit": explicit}):
        consumer(["health"])
    assert ("--budget" in captured) is explicit


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "fault",
    ["partial", "truncated", "missing_sections", "missing_summary", "non_object", "missing_counter", "zero", "clean"],
)
def test_incomplete_audit_never_posts_clean_counters(monkeypatch, dry_run, fault):
    # Entire HTTP boundary is mocked. No credentials, endpoint or network used.
    audit = copy.deepcopy(_FAKE_AUDIT_ENVELOPE)
    audit["summary"]["partial_success"] = False
    for section in audit["sections"].values():
        section["summary"]["partial_success"] = False
    if fault == "partial":
        audit["summary"]["partial_success"] = True
    elif fault == "truncated":
        audit["summary"].update(truncated=True, truncation_reason="budget")
    elif fault == "missing_sections":
        audit.pop("sections")
    elif fault == "missing_summary":
        audit.pop("summary")
    elif fault == "non_object":
        audit = []
    elif fault == "missing_counter":
        audit["sections"]["dead"]["summary"].pop("safe")
    elif fault == "zero":
        audit["sections"]["health"]["summary"]["health_score"] = 0
        audit["sections"]["debt"]["summary"]["total_remediation_minutes"] = 0
        audit["sections"]["dead"]["summary"]["safe"] = 0
    posted = []

    def post(*args, **kwargs):
        posted.append(args[2])
        return True, 200, "accepted"

    monkeypatch.setattr(cmd_metrics_push, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_metrics_push, "git_metadata", lambda: {})
    monkeypatch.setattr(cmd_metrics_push, "_capture_audit", lambda: audit)
    monkeypatch.setattr(cmd_metrics_push, "_post_metrics", post)
    args = ["--dry-run"] if dry_run else ["--token", "fixture-token"]
    result = CliRunner().invoke(cmd_metrics_push.metrics_push, args, obj={"json": True})
    expected_ok = fault in ("clean", "zero")
    assert result.exit_code == (0 if dry_run or expected_ok else 1), result.output
    data = json.loads(result.stdout)
    assert data["summary"]["audit_status"] == ("ok" if expected_ok else "failed")
    assert data["summary"]["partial_success"] is (not expected_ok)
    assert bool(posted) is (expected_ok and not dry_run)
    if not expected_ok:
        assert data["payload"]["metrics"] == {}
        assert data["payload"]["audit_error"]
    if fault == "zero":
        assert data["payload"]["metrics"]["health_score"] == 0
        assert data["payload"]["metrics"]["debt_total_minutes"] == 0
        assert data["payload"]["metrics"]["dead_safe"] == 0


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("fault", ["raise", "none", "empty", "list", "clean"])
def test_payload_failure_never_reaches_http(monkeypatch, dry_run, fault):
    # A valid Audit isolates the later assembly failure. All network and Git
    # metadata boundaries are mocked; injected failures are deterministic.
    audit = copy.deepcopy(_FAKE_AUDIT_ENVELOPE)
    audit["summary"]["partial_success"] = False
    for section in audit["sections"].values():
        section["summary"]["partial_success"] = False
    original = cmd_metrics_push._build_payload

    def build(*args, **kwargs):
        if fault == "raise":
            raise RuntimeError("controlled metrics assembly failure")
        if fault != "clean":
            return {"none": None, "empty": {"metrics": {}}, "list": []}[fault]
        return original(*args, **kwargs)

    posts = []

    def post(*args, **kwargs):
        posts.append(args)
        return True, 200, "accepted"

    monkeypatch.setattr(cmd_metrics_push, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_metrics_push, "git_metadata", lambda: {})
    monkeypatch.setattr(cmd_metrics_push, "_capture_audit", lambda: audit)
    monkeypatch.setattr(cmd_metrics_push, "_build_payload", build)
    monkeypatch.setattr(cmd_metrics_push, "_post_metrics", post)
    args = ["--dry-run"] if dry_run else ["--token", "fixture-token"]
    result = CliRunner().invoke(cmd_metrics_push.metrics_push, args, obj={"json": True})
    clean = fault == "clean"
    assert result.exit_code == (0 if dry_run or clean else 1), result.output
    data = json.loads(result.stdout)
    assert data["summary"]["audit_status"] == "ok"
    assert data["summary"]["partial_success"] is (not clean)
    assert data["summary"]["payload_status"] == ("ready" if clean else "failed")
    assert bool(posts) is (clean and not dry_run)
