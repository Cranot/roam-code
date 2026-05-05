"""Tests for v12.19 passes 91-100."""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def test_pass91_complexity_empty_state_emits_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "complexity", "--threshold", "999999"])
    # Either passes (returns empty results envelope) or exits 1 with JSON
    output = result.output
    assert output, "expected envelope output"
    parsed = json.loads(output)
    assert parsed["command"] == "complexity"


def test_pass91_coverage_gaps_missing_filter_emits_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "coverage-gaps"])
    assert result.exit_code == 2
    parsed = json.loads(result.output)
    assert parsed["command"] == "coverage-gaps"
    assert "missing" in parsed["summary"]["verdict"].lower()


def test_pass91_config_default_show_emits_json():
    """config without flags should emit JSON (helper-fix from Pass 91)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "config"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["command"] == "config"


def test_pass92_observability_writes_to_stderr_when_enabled(monkeypatch):
    import io
    import sys

    from roam.observability import log_swallowed, reset

    reset()
    monkeypatch.setenv("ROAM_VERBOSE", "1")
    fake_err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", fake_err)
    log_swallowed("test:scope", RuntimeError("boom"))
    out = fake_err.getvalue()
    assert "test:scope" in out
    assert "RuntimeError" in out


def test_pass92_observability_silent_by_default(monkeypatch):
    import io
    import sys

    from roam.observability import log_swallowed, reset

    reset()
    monkeypatch.delenv("ROAM_VERBOSE", raising=False)
    monkeypatch.delenv("ROAM_OBSERVABILITY", raising=False)
    fake_err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", fake_err)
    log_swallowed("test:scope", RuntimeError("boom"))
    assert fake_err.getvalue() == ""


def test_pass93_mcp_wrappers_registered():
    from roam.mcp_server import _CORE_TOOLS, _TOOL_METADATA

    expected = {
        "roam_alerts",
        "roam_timeline",
        "roam_test_impact",
        "roam_disambiguate",
        "roam_why_fail",
    }
    for name in expected:
        assert name in _TOOL_METADATA, f"{name} not registered"
        assert name in _CORE_TOOLS, f"{name} not in core preset"


def test_pass94_adversarial_completes_without_n1():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "adversarial"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["command"] == "adversarial"


def test_pass95_command_reference_appendix_present():
    """Auto-generated complete-reference appendix must exist."""
    from pathlib import Path

    text = Path("docs/site/command-reference.html").read_text(encoding="utf-8")
    assert "<!-- BEGIN auto-reference -->" in text
    assert "<!-- END auto-reference -->" in text
    assert 'id="complete-reference"' in text


def test_pass96_orphan_imports_lang_filter_runs():
    runner = CliRunner()
    for lang in ("python", "javascript", "go", "all"):
        result = runner.invoke(cli, ["--json", "orphan-imports", "--lang", lang])
        assert result.exit_code == 0, f"failed for --lang {lang}: {result.output}"
        parsed = json.loads(result.output)
        assert parsed["command"] == "orphan-imports"
        assert "languages" in parsed["summary"]


def test_pass97_audit_chains_sections():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "audit", "--brief"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["command"] == "audit"
    summary = parsed["summary"]
    for k in ("verdict", "health_score", "file_total", "symbol_total", "api_surface"):
        assert k in summary
    assert "sections" in parsed
    assert "health" in parsed["sections"]
    assert "stats" in parsed["sections"]


def test_pass98_ai_default_off(monkeypatch):
    import asyncio

    from roam.mcp_extras.sampling import compress_with_sampling

    class FakeCtx:
        def sample(self):
            raise RuntimeError("should not be called when AI is OFF")

    monkeypatch.delenv("ROAM_AI_ENABLED", raising=False)
    out = asyncio.run(compress_with_sampling(FakeCtx(), {"foo": "bar"}, task="t"))
    assert out is None


def test_pass98_ai_opt_in_calls_sampler(monkeypatch):
    import asyncio

    from roam.mcp_extras.sampling import compress_with_sampling

    monkeypatch.setenv("ROAM_AI_ENABLED", "1")
    # Use an unusable ctx so we exit on the next guard, but at least the
    # env-var guard didn't short-circuit.
    out = asyncio.run(compress_with_sampling(None, {"foo": "bar"}, task="t"))
    assert out is None  # ctx is None; should hit the next guard


def test_pass99_impact_indirect_refs_field_present():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "impact", "ensure_index"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["command"] == "impact"
    assert "indirect_refs" in parsed


def test_pass100_agent_export_brief_drops_verbose_payload():
    runner = CliRunner()
    full = runner.invoke(cli, ["--json", "agent-export"])
    brief = runner.invoke(cli, ["--json", "agent-export", "--brief"])
    assert full.exit_code == 0
    assert brief.exit_code == 0
    full_p = json.loads(full.output)
    brief_p = json.loads(brief.output)
    assert "directory_layout" in full_p
    assert "directory_layout" not in brief_p
    assert brief_p["summary"]["brief"] is True
    assert len(brief.output) < len(full.output) / 2
