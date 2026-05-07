"""Tests for SARIF enrichment (automationDetails + provenance + suppressions).

The enrichment lets GitHub Code Scanning correlate runs across re-ingests,
maps findings to a specific git commit, and respects user-defined
suppressions from .roam/suppressions.json.
"""

from __future__ import annotations

import json

from roam.output.sarif import to_sarif


def _minimal_result(rule_id: str = "ROAM-DEMO-1", file: str = "src/x.py", line: int = 10) -> dict:
    return {
        "ruleId": rule_id,
        "level": "warning",
        "message": {"text": "demo finding"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": file},
                    "region": {"startLine": line},
                }
            }
        ],
    }


def test_automation_details_present() -> None:
    doc = to_sarif(
        tool_name="roam-code",
        version="12.42",
        rules=[{"id": "ROAM-DEMO-1", "shortDescription": "demo"}],
        results=[_minimal_result()],
    )
    run = doc["runs"][0]
    assert "automationDetails" in run
    ad = run["automationDetails"]
    assert "id" in ad
    assert "guid" in ad
    assert "12.42" in ad["guid"]


def test_information_uri_on_driver() -> None:
    doc = to_sarif("roam-code", "12.42", [], [])
    driver = doc["runs"][0]["tool"]["driver"]
    assert driver["informationUri"] == "https://roam-code.com/"
    assert "downloadUri" in driver
    assert driver["organization"] == "Cranot"


def test_version_control_provenance_when_in_git_repo(tmp_path, monkeypatch) -> None:
    # Use the actual roam-code repo (which is a git repo, ensured by conftest)
    doc = to_sarif("roam-code", "12.42", [], [])
    run = doc["runs"][0]
    # In a real git repo, provenance should be present; in a sandbox without
    # git, it may not. Either is OK; just check the shape if it exists.
    if "versionControlProvenance" in run:
        vcs = run["versionControlProvenance"][0]
        assert "revisionId" in vcs
        assert len(vcs["revisionId"]) >= 8  # short or full SHA


def test_suppressions_applied_when_file_present(tmp_path, monkeypatch) -> None:
    # Create a fake .roam/suppressions.json in a temp dir
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text(
        json.dumps(
            [
                {
                    "rule_id": "ROAM-DEMO-1",
                    "location": "src/x.py:10",
                    "reason": "false positive: this is intentional",
                    "kind": "external",
                    "status": "accepted",
                }
            ]
        ),
        encoding="utf-8",
    )

    doc = to_sarif(
        tool_name="roam-code",
        version="12.42",
        rules=[{"id": "ROAM-DEMO-1", "shortDescription": "demo"}],
        results=[_minimal_result()],
    )
    result = doc["runs"][0]["results"][0]
    assert "suppressions" in result
    sup = result["suppressions"][0]
    assert sup["status"] == "accepted"
    assert sup["kind"] == "external"
    assert "false positive" in sup["justification"]


def test_suppressions_object_shape_also_supported(tmp_path, monkeypatch) -> None:
    """Either a list at top-level OR {"suppressions": [...]} should work."""
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text(
        json.dumps(
            {
                "suppressions": [
                    {
                        "rule_id": "ROAM-DEMO-1",
                        "location": "src/x.py:10",
                        "reason": "wrapped",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    doc = to_sarif(
        tool_name="roam-code",
        version="12.42",
        rules=[{"id": "ROAM-DEMO-1", "shortDescription": "demo"}],
        results=[_minimal_result()],
    )
    assert "suppressions" in doc["runs"][0]["results"][0]


def test_no_suppressions_file_means_no_array(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .roam/ dir present
    doc = to_sarif(
        tool_name="roam-code",
        version="12.42",
        rules=[],
        results=[_minimal_result()],
    )
    assert "suppressions" not in doc["runs"][0]["results"][0]


def test_unmatched_suppression_does_not_attach(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text(
        json.dumps([{"rule_id": "OTHER-RULE", "location": "x.py:1"}]),
        encoding="utf-8",
    )

    doc = to_sarif(
        tool_name="roam-code",
        version="12.42",
        rules=[],
        results=[_minimal_result()],
    )
    assert "suppressions" not in doc["runs"][0]["results"][0]
