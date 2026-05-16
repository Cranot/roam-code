"""W1146: SARIF projection for ``roam critique`` patch-review findings.

The killer signal for critique is *clones-not-edited* (high severity), with
*impact* (blast radius, medium/high) and *intent* (diff-wide note) as
secondary checks. Two of those three carry file/line evidence; the third
(intent) is diff-wide and must emit an empty ``locations[]`` array per the
SARIF 2.1.0 spec (locations is documented as optional — a result without
one signals "applies to the whole run").

The dogfood-corpus rule that pinned this test design: every check kind a
critique invocation can emit must round-trip through the SARIF projection
without dropping its severity / message / anchor. The three assertions
below correspond exactly to the three closed-enum rule ids declared by
:func:`roam.output.sarif.critique_to_sarif`.
"""

from __future__ import annotations

from roam.output.sarif import critique_to_sarif


def test_empty_findings_produces_valid_sarif_with_zero_results() -> None:
    """Empty input emits a valid SARIF 2.1.0 envelope with 0 results.

    Mirrors the cmd_complexity / cmd_dead "no findings" path: the rules
    array is always populated (so consumers can introspect the rule
    catalogue even when nothing fired), but ``results`` is empty.
    """
    doc = critique_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "critique/clone-not-edited",
        "critique/blast-radius",
        "critique/intent-mismatch",
    }


def test_clones_not_edited_finding_has_valid_location() -> None:
    """A clones-not-edited finding anchors on the changed symbol's file.

    Evidence shape matches roam.critique.checks.check_clones_not_edited:
    ``evidence.changed_symbol.file`` is the SARIF anchor; line is not
    recorded (the diff-side "region" is the absence of an analogous
    edit, not a specific span).
    """
    finding = {
        "check": "clones-not-edited",
        "severity": "high",
        "title": "handleSave has 2 clone siblings that may need the same change",
        "detail": "Unedited clone siblings:\n  src/other.py:42",
        "evidence": {
            "changed_symbol": {
                "id": 1234,
                "name": "handleSave",
                "file": "src/main.py",
            },
            "siblings": [
                {
                    "sibling_file": "src/other.py",
                    "sibling_line": 42,
                    "sibling_func": "handleSave",
                    "sibling_qname": "src/other.py:handleSave",
                    "similarity": 0.95,
                }
            ],
        },
    }

    doc = critique_to_sarif([finding])
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "critique/clone-not-edited"
    # severity "high" → SARIF level "warning" via _legacy_level_map.
    assert r["level"] == "warning"
    # Location anchored on the changed symbol's file; no region (line)
    # because clones-not-edited reports an absent edit.
    assert len(r["locations"]) == 1
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/main.py"
    # No region/startLine — _location omits "region" when line is None.
    assert "region" not in phys


def test_intent_finding_has_empty_locations_diff_wide() -> None:
    """An intent finding is diff-wide → emits empty locations[] per SARIF spec.

    SARIF 2.1.0 makes ``result.locations`` optional precisely so tools
    can express findings that aren't pinned to a single artifact. The
    critique intent check compares PR title verbs against the diff's net
    add/delete count — the finding describes the whole patch, not any
    one file.
    """
    finding = {
        "check": "intent",
        "severity": "medium",
        "title": "PR title says 'add' but the diff has no additions",
        "detail": "The stated intent mentions adding something...",
        "evidence": {
            "intent_label": "add",
            "summary": {
                "symbols_touched": 3,
                "additions": 0,
                "deletions": 12,
            },
        },
    }

    doc = critique_to_sarif([finding])
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "critique/intent-mismatch"
    # Intent findings are diff-wide — empty locations array.
    assert r["locations"] == []
