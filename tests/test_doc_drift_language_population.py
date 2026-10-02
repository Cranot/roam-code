"""A product's language capability is not the language census of its own code."""

from __future__ import annotations

import pytest

from roam.commands import cmd_doc_drift as mod
from tests.test_doc_drift import _invoke_json, _make_project


@pytest.mark.parametrize(
    "text",
    [
        "28 languages",
        "287 commands · 246 MCP tools · 28 languages · local analysis",
        "Works across 28 languages.",
        "The product supports 28 languages.",
        "The index has 13 languages. The product has 28 languages.",
        "The index has 13 languages | 28 languages",
    ],
)
@pytest.mark.parametrize("actual", [13, 28])
def test_unestablished_language_population_is_unknown(text, actual):
    claim = next(c for c in mod._extract_count_claims(text, doc="guide.md", line_number=1) if c["_number"] == 28)
    result = mod._evaluate_count_claim(claim, {"languages": mod._Metric(actual, "indexed languages")})
    assert result["status"] == "unverifiable"
    assert result["actual"] is None
    assert result["_authority_unavailable"] is True


@pytest.mark.parametrize(
    "text",
    [
        "The index has 13 languages.",
        "There are 13 indexed languages.",
        "13 languages in the index.",
        "13 languages present in the index.",
    ],
)
@pytest.mark.parametrize("actual,status", [(13, "verified"), (12, "drifted")])
def test_explicit_index_census_keeps_agreement_and_drift(text, actual, status):
    claim = mod._extract_count_claims(text, doc="guide.md", line_number=1)[0]
    result = mod._evaluate_count_claim(claim, {"languages": mod._Metric(actual, "indexed languages")})
    assert result["status"] == status
    assert result["actual"] == actual


@pytest.mark.parametrize("indexed_claim", [False, True])
def test_real_cli_separates_capability_from_index_drift(tmp_path, cli_runner, indexed_claim):
    # Existing fixture indexes a disposable local Git repository. No network,
    # external repository, wall-clock dependency or fabricated index metrics.
    text = "28 languages\n"
    if indexed_claim:
        text += "The index has 999999 languages.\n"
    project = _make_project(tmp_path, docs={"README.md": text})
    result, payload = _invoke_json(cli_runner, project, "--ci")
    assert payload["summary"]["docs_scanned"] == 1
    assert payload["summary"]["claims_total"] == (2 if indexed_claim else 1)
    assert payload["summary"]["unverifiable"] == 1
    assert payload["summary"]["drifted"] == int(indexed_claim)
    assert payload["summary"]["partial_success"] is True
    assert result.exit_code == (5 if indexed_claim else 0)
