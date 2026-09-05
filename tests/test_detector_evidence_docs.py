"""Keep maintained entrypoints aligned with detector evidence limitations."""

from __future__ import annotations

import ast

import pytest

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
SITE = ROOT / "templates" / "distribution" / "landing-page" / "docs"


@pytest.mark.parametrize("filename", ["README.md", "AGENTS.md", "CONTRIBUTING.md", "llms-install.md"])
def test_maintainer_entrypoints_link_detector_evidence(filename):
    assert "docs/concepts/detector-evidence.md" in (ROOT / filename).read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", ["agent-contract.html", "demos.html", "mcp-usage.html"])
def test_web_guides_link_shared_evidence_limits(filename):
    text = (SITE / filename).read_text(encoding="utf-8")
    assert "/docs/command-reference#evidence-limits" in text
    assert "binary verdict" not in text


def test_partition_docs_distinguish_cli_and_mcp_defaults():
    guide = (ROOT / "docs/concepts/detector-evidence.md").read_text(encoding="utf-8")
    assert "two to eight agents" in guide
    assert "MCP wrapper defaults to an explicit four partitions" in guide
    assert "one entry per cluster" not in (SITE / "mcp-usage.html").read_text(encoding="utf-8")


def test_mcp_authorship_description_and_generated_reference_agree():
    tree = ast.parse((ROOT / "src/roam/mcp_server.py").read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "roam_ai_ratio")
    decorator = next(
        node
        for node in fn.decorator_list
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_tool"
    )
    description = ast.literal_eval(next(kw.value for kw in decorator.keywords if kw.arg == "description"))
    assert "uncalibrated score" in description
    assert "not an authorship estimate" in description
    assert description in (ROOT / "docs/mcp-tools.md").read_text(encoding="utf-8")


def test_web_reference_discloses_partial_static_analysis():
    text = (SITE / "command-reference.html").read_text(encoding="utf-8")
    assert 'id="evidence-limits"' in text
    for phrase in ("partial_success", "not runtime coverage", "not exploitability proof", "SAFE / REVIEW / UNSAFE"):
        assert phrase in text


def test_deletion_demo_does_not_invent_runtime_telemetry():
    text = (SITE / "demos.html").read_text(encoding="utf-8")
    assert "runtime hits:" not in text
    assert "VERDICT: REVIEW" in text
