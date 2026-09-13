"""Example scope is local to a claim, not an exemption for later sentences."""

from __future__ import annotations

import pytest

from roam.commands import cmd_doc_drift as mod
from tests.test_doc_drift import _invoke_json, _make_project


@pytest.mark.parametrize(
    "text",
    [
        "99 functions in this example.",
        "99 functions in the sample output.",
        "99 functions (illustrative).",
        "For example, 99 functions.",
        "An illustration, e.g. a project has 99 functions.",
    ],
)
def test_local_example_population_is_unknown(text):
    claim = mod._extract_count_claims(text, doc="guide.md", line_number=1)[0]
    result = mod._evaluate_count_claim(claim, {"functions": mod._Metric(1, "indexed functions")})
    assert result["status"] == "unverifiable"
    assert result["actual"] is None


@pytest.mark.parametrize("separator", [". ", "; ", " | "])
@pytest.mark.parametrize("example_first", [False, True])
def test_example_scope_does_not_cross_sentence_or_cell(separator, example_first):
    parts = ["For example, 3 functions", "the index has 99 functions"]
    if not example_first:
        parts.reverse()
    claims = mod._extract_count_claims(separator.join(parts), doc="guide.md", line_number=1)
    statuses = {
        claim["_number"]: mod._evaluate_count_claim(claim, {"functions": mod._Metric(1, "indexed functions")})["status"]
        for claim in claims
    }
    assert statuses == {3: "unverifiable", 99: "drifted"}


def test_real_cli_preserves_example_denominator_and_current_drift(tmp_path, cli_runner):
    # Existing fixture uses a disposable local Git repo and a real index.
    project = _make_project(
        tmp_path,
        docs={
            "README.md": (
                "99 functions in this example. the index has 98 functions.\n"
                "For example, 3 functions. the index has 1 functions.\n"
            )
        },
    )
    result, payload = _invoke_json(cli_runner, project, "--ci")
    assert result.exit_code == 5
    summary = payload["summary"]
    assert summary["docs_scanned"] == 1 and summary["claims_total"] == 4
    assert summary["unverifiable"] == 2
    assert summary["verified"] == 1
    assert summary["drifted"] == 1
    assert summary["partial_success"] is True
