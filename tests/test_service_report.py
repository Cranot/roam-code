"""Tests for ``roam service-report`` — the productised service-engagement command.

``service-report`` is the sibling of ``roam pr-replay``: it turns the four
services-report templates (due-diligence, AI-readiness, reachability-triage,
post-incident) into one-command deliverables. Each ``--type`` runs the right
existing Roam primitives, aggregates their JSON envelopes, and emits a
buyer-facing Markdown narrative.

Tests focus on:
* Each ``--type`` renders a distinct, well-formed report (fast, via stubbed
  primitives — the real subprocess path is exercised by one slow test).
* Every report carries the honest disclaimer banner and is wording-guard
  clean (no ``certifies`` / ``guaranteed`` / ``compliant`` outside a
  negation window — the W184 / W203 discipline).
* The JSON envelope is parseable and carries the expected keys.
* ``--output`` writes the markdown; ``--type`` is required.
* The engagement ledger appends, not overwrites.

The primitive calls (``_run_roam_json`` / ``_run_postmortem``) are stubbed
with canned envelopes shaped like the real command output captured from
roam-code, so the render/dispatch/envelope path is tested deterministically
and fast. One ``@pytest.mark.slow`` test runs the real end-to-end path.
"""

from __future__ import annotations

import json as _json

import pytest
from click.testing import CliRunner

from tests._helpers.wording_lint import scan_for_overclaims

# ---------------------------------------------------------------------------
# Canned envelopes — shaped like the real ``roam --json <cmd>`` output.
# ---------------------------------------------------------------------------

_CANNED: dict[str, dict] = {
    "health": {
        "summary": {
            "verdict": "Fair codebase (75/100) — 47 critical, 9 warnings",
            "health_score": 75,
            "cycles_total": 10,
            "cycles_actionable": 2,
            "god_components": 50,
            "tangle_ratio": 0.0,
        }
    },
    "bus-factor": {
        "summary": {
            "verdict": "bus factor 1 (min), 58 high-risk, 63 single-owner modules",
            "high_risk": 58,
            "solo_authored_count": 63,
            "directories_analyzed": 63,
        }
    },
    "complexity": {
        "summary": {
            "verdict": "avg complexity 3.7, 867 critical, 781 high",
            "average_complexity": 3.7,
            "p90_complexity": 9.0,
            "critical_count": 867,
            "total_analyzed": 25537,
        }
    },
    "dead": {
        "summary": {
            "verdict": "579 dead export(s): 31 safe, 384 review, 164 intentional",
            "files_affected": 162,
            "total_effort_hours": 1225.6,
        }
    },
    "clones": {
        "summary": {
            "verdict": "115 clone clusters found (646 functions, 79% avg similarity)",
            "estimated_reducible_lines": 36681,
        }
    },
    "smells": {
        "summary": {
            "verdict": "Needs refactoring: 4367 smells (113 critical, 2508 warning) in 957 files",
            "total_smells": 4367,
            "files_affected": 957,
        }
    },
    "test-pyramid": {
        "summary": {
            "verdict": "MOSTLY-UNSTRUCTURED — 1130 of 1145 test files have no kind hint",
            "total": 1145,
            "unit": 0,
            "integration": 5,
            "e2e": 4,
        }
    },
    "sbom": {
        "summary": {
            "verdict": "4 reachable (4 direct, 0 heuristic), 19 phantom",
            "total_dependencies": 23,
            "reachable_count": 4,
            "reachable_direct_count": 4,
            "phantom_count": 19,
        }
    },
    "supply-chain": {
        "summary": {
            "verdict": "Supply chain risky (36/100) -- 0 unpinned dependencies",
            "risk_score": 36,
            "pin_coverage_pct": 0.0,
            "unpinned_count": 0,
            "total_dependencies": 23,
        }
    },
    "vulns": {
        "summary": {
            "verdict": "no vulnerability scan available (vulnerabilities table is empty)",
            "total": 0,
        }
    },
    "vuln-reach": {
        "summary": {
            "verdict": "No vulnerabilities ingested. Run vuln-map first.",
            "reachable_count": 0,
            "total_vulns": 0,
        }
    },
    "taint": {
        "summary": {
            "verdict": "No taint findings across 22 rule(s)",
            "findings": 0,
            "rules": 22,
            "risk_score": 0,
        }
    },
    "secrets": {
        "summary": {
            "verdict": "No secrets found",
            "total_findings": 0,
        }
    },
    "architecture-drift": {
        "summary": {
            "verdict": "Need at least 2 snapshots within window — found 1",
            "state": "insufficient_snapshots",
        }
    },
    "ai-readiness": {
        "summary": {
            "verdict": "AI Readiness 57/100 -- FAIR",
            "score": 57,
            "label": "FAIR",
        },
        "dimensions": [
            {
                "label": "Naming consistency",
                "name": "naming_consistency",
                "score": 100,
                "weight": 15,
                "contribution": 15.0,
            },
            {"label": "Module coupling", "name": "module_coupling", "score": 100, "weight": 20, "contribution": 20.0},
            {"label": "Dead code noise", "name": "dead_code_noise", "score": 0, "weight": 15, "contribution": 0.0},
            {
                "label": "Test signal strength",
                "name": "test_signal_strength",
                "score": 9,
                "weight": 20,
                "contribution": 1.8,
            },
        ],
        "recommendations": [
            "Remove 469 dead exports to reduce agent confusion",
            "Increase test coverage mapping (currently 9%)",
            "Break 10 dependency cycles",
        ],
    },
    "ai-ratio": {
        "summary": {
            "verdict": "~59% estimated AI-generated code (confidence: HIGH)",
            "ai_ratio": 0.59,
            "confidence": "HIGH",
            "commits_analyzed": 406,
        }
    },
    "agent-score": {
        "summary": {
            "verdict": "Scored 16 agents; top: recheck 99.6/100 over 2 runs",
            "agents_scored": 16,
            "count": 16,
        }
    },
    "mode": {
        "summary": {
            "verdict": "active mode: safe_edit (13 allowed commands)",
            "active_mode": "safe_edit",
            "allowed_count": 13,
            "policy_source": "default+constitution",
            "persisted": False,
        }
    },
    "audit-trail-verify": {
        "summary": {
            "verdict": "chain valid (9 records)",
            "chain_valid": True,
            "chain_tier": "CHAIN_VERIFIED",
            "total_records": 9,
            "unsigned_events": 0,
        }
    },
}

_CANNED_POSTMORTEM = {
    "summary": {
        "verdict": "11 of 20 commits would have surfaced findings",
        "commits_scanned": 20,
        "commits_with_findings": 11,
    },
    "commits": [
        {
            "date": "2026-05-21",
            "short_sha": "4b8c61c9",
            "subject": "refactor: make fallback chains loud",
            "high": 0,
            "medium": 12,
            "kinds": ["impact x12"],
        },
        {
            "date": "2026-05-22",
            "short_sha": "83f5c44c",
            "subject": "fix: dogfood-v2 defects",
            "high": 1,
            "medium": 3,
            "kinds": ["impact x2", "intent x1"],
        },
        {
            "date": "2026-05-21",
            "short_sha": "2852c675",
            "subject": "chore(release): v13.4",
            "high": 0,
            "medium": 0,
            "kinds": [],
        },
    ],
}


@pytest.fixture
def stub_primitives(monkeypatch):
    """Stub the primitive-invocation helpers with canned envelopes.

    Makes the render/dispatch/envelope path deterministic and fast — no
    subprocess, no index build. ``ensure_index`` is neutralised so the
    command doesn't try to (re)build an index during the render test.
    """
    from roam.commands import cmd_service_report as mod

    def _fake_run_roam_json(args, **_kwargs):
        return _CANNED.get(args[0], {})

    def _fake_run_postmortem(commit_range, *, limit=100):
        return _CANNED_POSTMORTEM

    monkeypatch.setattr(mod, "_run_roam_json", _fake_run_roam_json)
    monkeypatch.setattr(mod, "_run_postmortem", _fake_run_postmortem)
    monkeypatch.setattr(mod, "ensure_index", lambda *a, **k: None)
    return mod


def _invoke(*args: str, json_mode: bool = False) -> tuple[int, str]:
    from roam.cli import cli

    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + ["service-report", *args]
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


ALL_TYPES = ["due-diligence", "ai-readiness", "reachability-triage", "post-incident"]


# ---------------------------------------------------------------------------
# Smoke tests — every type produces a valid, banner-carrying report.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rtype", ALL_TYPES)
def test_each_type_renders_a_report(stub_primitives, rtype):
    code, out = _invoke("--type", rtype)
    assert code == 0, f"{rtype} exited non-zero: {out[:300]}"
    # Title line
    assert out.startswith("# "), f"{rtype}: report must start with an H1 title"
    # Honest disclaimer banner
    assert "Engineering evidence, not an attestation." in out
    assert "does not certify" in out
    # Tool attribution line
    assert f"roam service-report --type {rtype}" in out
    # "What this does not cover" section present
    assert "## What this report does not cover" in out
    # Paid framing present
    assert "About this engagement" in out


@pytest.mark.parametrize("rtype", ALL_TYPES)
def test_each_type_is_wording_guard_clean(stub_primitives, rtype):
    """No compliance-overclaim wording outside a negation window (W184/W203)."""
    code, out = _invoke("--type", rtype)
    assert code == 0
    violations = scan_for_overclaims(out)
    assert not violations, f"{rtype} report has wording-guard violations: {violations}"


def test_due_diligence_surfaces_health_verdict(stub_primitives):
    code, out = _invoke("--type", "due-diligence")
    assert code == 0
    assert "Codebase Due Diligence Report" in out
    assert "health 75/100" in out
    assert "roam health" in out
    assert "roam bus-factor" in out
    # Real numbers from the canned health/clones envelopes land in the report
    assert "36681" in out  # estimated reducible lines from clones


def test_reachability_triage_leads_with_the_wedge(stub_primitives):
    code, out = _invoke("--type", "reachability-triage")
    assert code == 0
    assert "Security Reachability Triage" in out
    assert "reachable from scanner noise" in out
    # Dependency reachability signal (4 of 23) present
    assert "4 of 23" in out or ("Reachable | 4" in out and "Total dependencies | 23" in out)


def test_ai_readiness_renders_dimension_table(stub_primitives):
    code, out = _invoke("--type", "ai-readiness")
    assert code == 0
    assert "AI Adoption Readiness Audit" in out
    assert "57/100" in out
    # Dimension rows land
    assert "Naming consistency" in out
    assert "Module coupling" in out
    # Recommendations land
    assert "Break 10 dependency cycles" in out


def test_post_incident_replays_the_range(stub_primitives):
    code, out = _invoke("--type", "post-incident", "--range", "HEAD~20..HEAD")
    assert code == 0
    assert "Post-Incident Replay Report" in out
    assert "HEAD~20..HEAD" in out
    assert "Commits replayed: **20**" in out
    # A flagged commit from the canned postmortem lands in the table
    assert "4b8c61c9" in out
    # Audit-trail integrity section present
    assert "chain valid (9 records)" in out
    assert "CHAIN_VERIFIED" in out


# ---------------------------------------------------------------------------
# JSON envelope
# ---------------------------------------------------------------------------


def test_json_envelope_is_well_formed(stub_primitives):
    code, out = _invoke("--type", "reachability-triage", json_mode=True)
    assert code == 0
    envelope = _json.loads(out[out.find("{") :])
    assert envelope["command"] == "service-report"
    assert "summary" in envelope
    summary = envelope["summary"]
    assert summary["report_type"] == "reachability-triage"
    assert "verdict" in summary
    assert "generated_at" in summary
    assert "sections_present" in summary
    assert summary["sections_failed"] == []
    assert summary["sections_degraded"] == []
    assert summary["state"] == "complete"
    assert summary["partial_success"] is False
    # Body
    assert isinstance(envelope.get("sections"), dict)
    assert isinstance(envelope.get("report_markdown"), str)
    assert envelope["report_markdown"].startswith("# ")


def test_json_envelope_sections_present_lists_run_commands(stub_primitives):
    code, out = _invoke("--type", "due-diligence", json_mode=True)
    assert code == 0
    envelope = _json.loads(out[out.find("{") :])
    present = envelope["summary"]["sections_present"]
    assert "health" in present
    assert "clones" in present


def test_component_failure_is_visible_in_text_and_json(stub_primitives, monkeypatch):
    """One absent primitive cannot collapse into a successful-looking report."""
    mod = stub_primitives

    def _partially_failed(args, **_kwargs):
        if args[0] == "health":
            return mod._component_failure("health", "component_timeout", "timed out after 180s")
        return _CANNED.get(args[0], {})

    monkeypatch.setattr(mod, "_run_roam_json", _partially_failed)
    code, text_report = _invoke("--type", "due-diligence")
    assert code == 0
    assert "**Partial report:**" in text_report
    assert "`health`" in text_report

    code, output = _invoke("--type", "due-diligence", json_mode=True)
    assert code == 0
    envelope = _json.loads(output[output.find("{") :])
    summary = envelope["summary"]
    assert summary["partial_success"] is True
    assert summary["state"] == "component_failure"
    assert summary["sections_failed"] == ["health"]
    assert "partial report" in summary["verdict"]
    assert envelope["sections"]["health"]["isError"] is True


# ---------------------------------------------------------------------------
# Component process boundary
# ---------------------------------------------------------------------------


def test_component_runner_uses_literal_isolated_argv(monkeypatch):
    from roam.commands import cmd_service_report as mod

    seen = {}

    class _Process:
        returncode = 0

        def communicate(self, timeout):
            seen["timeout"] = timeout
            return b'progress\n{"summary":{"verdict":"ok"}}', b""

    def _popen(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _Process()

    monkeypatch.setattr(mod._subprocess, "Popen", _popen)
    result = mod._run_roam_json(["version"])

    assert seen["argv"][1:] == ["-m", "roam", "--json", "version"]
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["stdin"] is mod._subprocess.DEVNULL
    assert seen["timeout"] == mod._COMPONENT_TIMEOUT_SECONDS
    assert result["summary"]["verdict"] == "ok"


def test_component_runner_terminates_process_tree_on_timeout(monkeypatch):
    from roam.commands import cmd_service_report as mod

    class _TimedOutProcess:
        returncode = None

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise mod._subprocess.TimeoutExpired(cmd="roam", timeout=timeout)
            return b"", b""

    proc = _TimedOutProcess()
    monkeypatch.setattr(mod._subprocess, "Popen", lambda *a, **k: proc)
    terminated = []
    monkeypatch.setattr(mod, "_terminate_component_process_tree", lambda value: terminated.append(value) or True)

    result = mod._run_roam_json(["clones"])

    assert terminated == [proc]
    assert result["isError"] is True
    assert result["summary"]["state"] == "component_timeout"
    assert result["summary"]["partial_success"] is True


def test_component_runner_refuses_launch_after_report_deadline(monkeypatch):
    from roam.commands import cmd_service_report as mod

    monkeypatch.setattr(mod._time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        mod._subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("expired report component must not launch"),
    )

    result = mod._run_roam_json(["smells"], deadline=110.0)

    assert result["isError"] is True
    assert result["summary"]["state"] == "report_deadline_exhausted"
    assert "before component launch" in result["summary"]["verdict"]


def test_component_runner_uses_remaining_report_budget_and_cleans_tree(monkeypatch):
    from roam.commands import cmd_service_report as mod

    seen = {}

    class _TimedOutProcess:
        returncode = None

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout):
            self.calls += 1
            if self.calls == 1:
                seen["timeout"] = timeout
                raise mod._subprocess.TimeoutExpired(cmd="roam", timeout=timeout)
            return b"", b""

    proc = _TimedOutProcess()
    monkeypatch.setattr(mod._time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(mod._subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(mod, "_terminate_component_process_tree", lambda value: value is proc)

    result = mod._run_roam_json(["sbom"], deadline=150.0)

    assert seen["timeout"] == 50.0 - mod._DEADLINE_CLEANUP_RESERVE_SECONDS
    assert result["isError"] is True
    assert result["summary"]["state"] == "report_deadline_exhausted"
    assert "process tree terminated" in result["summary"]["verdict"]


def test_due_diligence_uses_one_bounded_report_deadline(monkeypatch):
    from roam.commands import cmd_service_report as mod

    deadlines = []

    def _fake_gather(components, *, max_workers=mod._COMPONENT_MAX_WORKERS, deadline=None):
        del max_workers
        deadlines.append(deadline)
        return {key: {"command": args[0], "summary": {"verdict": f"{key} evidence"}} for key, args in components}

    monkeypatch.setattr(mod._time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(mod, "_gather_components", _fake_gather)

    result = mod._gather_due_diligence()

    assert tuple(result) == tuple(key for key, _args in mod._DUE_DILIGENCE_COMPONENTS)
    assert deadlines == [100.0 + mod._DUE_DILIGENCE_BUDGET_SECONDS] * 5


@pytest.mark.parametrize(
    ("payload", "state"),
    [
        (b'{"summary":{"verdict":"first"},"summary":{"verdict":"last"}}', "component_malformed_output"),
        (b"[]", "component_empty_output"),
        (b"no json here", "component_empty_output"),
    ],
)
def test_component_runner_rejects_ambiguous_or_invalid_envelopes(monkeypatch, payload, state):
    from roam.commands import cmd_service_report as mod

    class _Process:
        returncode = 0

        def communicate(self, timeout):
            return payload, b""

    monkeypatch.setattr(mod._subprocess, "Popen", lambda *a, **k: _Process())
    result = mod._run_roam_json(["health"])
    assert result["isError"] is True
    assert result["summary"]["state"] == state


# ---------------------------------------------------------------------------
# --output / --type required
# ---------------------------------------------------------------------------


def test_output_writes_markdown_to_file(stub_primitives, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "triage.md"
    code, out = _invoke("--type", "reachability-triage", "--output", str(target))
    assert code == 0
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert body.startswith("# Security Reachability Triage")
    assert "Wrote" in out


def test_type_is_required():
    """Missing --type is a clean Click usage error (exit 2), not a traceback."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["service-report"], catch_exceptions=False)
    assert result.exit_code == 2
    assert "Traceback (most recent call last)" not in (result.output or "")


def test_post_incident_rejects_argv_injection_range(stub_primitives):
    """A --range beginning with '-' is rejected at the CLI boundary."""
    code, out = _invoke("--type", "post-incident", "--range", "--upload-pack=evil")
    assert code != 0
    assert "must not start with" in out


# ---------------------------------------------------------------------------
# Engagement ledger
# ---------------------------------------------------------------------------


def test_engagement_ledger_records_service_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_service_report import _record_engagement

    rec = _record_engagement(
        report_type="due-diligence",
        client="Acme Inc",
        subject="Acme Inc",
        headline="Fair codebase (75/100)",
        output_path=str(tmp_path / "dd.md"),
        generated_at="2026-07-07 03:31 UTC",
    )
    assert rec is not None
    ledger = tmp_path / ".roam" / "engagements.jsonl"
    assert ledger.exists()
    record = _json.loads(ledger.read_text(encoding="utf-8").strip())
    assert record["kind"] == "service-report"
    assert record["report_type"] == "due-diligence"
    assert record["client"] == "Acme Inc"
    assert record["ledger_schema"] == 1


def test_engagement_ledger_appends_not_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_service_report import _record_engagement

    _record_engagement(
        report_type="due-diligence",
        client="Acme Inc",
        subject="Acme",
        headline="h1",
        output_path="a.md",
        generated_at="2026-07-07 10:00 UTC",
    )
    _record_engagement(
        report_type="reachability-triage",
        client="Beta Corp",
        subject="Beta",
        headline="h2",
        output_path="b.md",
        generated_at="2026-07-07 11:00 UTC",
    )
    ledger = tmp_path / ".roam" / "engagements.jsonl"
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert _json.loads(lines[0])["client"] == "Acme Inc"
    assert _json.loads(lines[1])["report_type"] == "reachability-triage"


def test_no_track_engagement_skips_ledger(stub_primitives, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "r.md"
    code, out = _invoke(
        "--type",
        "reachability-triage",
        "--output",
        str(output),
        "--no-track-engagement",
    )
    assert code == 0
    ledger = tmp_path / ".roam" / "engagements.jsonl"
    if ledger.exists():
        assert ledger.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------------------
# Report-type registry contract
# ---------------------------------------------------------------------------


def test_report_types_registry_contract():
    from roam.commands.cmd_service_report import _REPORT_TYPES

    assert set(_REPORT_TYPES.keys()) == {
        "due-diligence",
        "ai-readiness",
        "reachability-triage",
        "post-incident",
    }
    required = {"label", "title", "purpose_line", "engagement_price", "lead_commands"}
    for rtype, meta in _REPORT_TYPES.items():
        missing = required - meta.keys()
        assert not missing, f"type '{rtype}' missing keys: {missing}"
        assert isinstance(meta["lead_commands"], list) and meta["lead_commands"]


# ---------------------------------------------------------------------------
# Pure-render unit tests — no CLI, no stubbing needed.
# ---------------------------------------------------------------------------


def test_render_survives_empty_envelopes():
    """A gather that returns {} for every section still renders a report."""
    from roam.commands.cmd_service_report import _REPORT_TYPES, _render

    for rtype in ALL_TYPES:
        meta = {
            "type_meta": _REPORT_TYPES[rtype],
            "report_type": rtype,
            "client": None,
            "index_sha": None,
            "generated_at": "2026-07-07 00:00 UTC",
            "subject": "target repository",
        }
        md = _render(rtype, env={}, meta=meta, commit_range="HEAD~20..HEAD")
        assert md.startswith("# ")
        assert "does not certify" in md
        # Even the empty-data path is wording-guard clean.
        assert not scan_for_overclaims(md)


# ---------------------------------------------------------------------------
# Slow: real end-to-end path (actual primitive subprocesses).
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_reachability_triage_real_end_to_end():
    """The real path: actual `roam` primitives run against the repo.

    reachability-triage is the fastest real type (no heavy graph walk),
    so it's the one we exercise end-to-end. Asserts a well-formed,
    wording-clean report — proof the stubbed tests match reality.
    """
    code, out = _invoke("--type", "reachability-triage")
    assert code == 0, out[:400]
    assert out.startswith("# Security Reachability Triage")
    assert "Dependency reachability" in out
    assert not scan_for_overclaims(out)
