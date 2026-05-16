"""W1165: SARIF projection for ``roam impact`` blast-radius output.

The killer signal for impact is *which downstream files / symbols can break*
when a target changes. Four finding families project onto SARIF, each on
its own closed-enum rule id:

- ``impact/affected-file`` (defaultLevel ``warning``): file-level anchor;
  severity scaled by PageRank importance (the file's max-dependent score).
- ``impact/direct-dependent`` (defaultLevel ``note``): per-dependent-symbol
  result anchored on the dependent's file (line where parseable).
- ``impact/sf-convention-test`` (defaultLevel ``note``): Salesforce
  convention test file covering the changed class.
- ``impact/indirect-ref`` (defaultLevel ``note``): string-literal
  reference site (registry / dispatch pattern) — anchored on
  file + line.

Mirrors the test design from ``test_cmd_critique_sarif.py``: every check
family the command emits must round-trip through SARIF without losing
its severity / message / anchor.
"""

from __future__ import annotations

from roam.output.sarif import impact_to_sarif


def test_empty_impact_produces_valid_sarif_with_zero_results() -> None:
    """A leaf-symbol / no-dependents envelope emits a valid SARIF doc with 0 results.

    Mirrors the cmd_complexity / cmd_dead "no findings" path: the rules
    array is always populated (so consumers can introspect the rule
    catalogue even when nothing fired), but ``results`` is empty.
    """
    empty_envelope = {
        "command": "impact",
        "symbol": "leaf_symbol",
        "summary": {"verdict": "no dependents", "affected_symbols": 0},
        "affected_file_list": [],
        "direct_dependents": {},
        "sf_convention_tests": [],
        "indirect_refs": [],
    }

    doc = impact_to_sarif(empty_envelope)

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 4 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "impact/affected-file",
        "impact/direct-dependent",
        "impact/sf-convention-test",
        "impact/indirect-ref",
    }


def test_affected_file_finding_has_valid_location_and_scaled_level() -> None:
    """An affected_file entry anchors on the file with severity scaled by importance.

    Importance is the per-file max-dependent PageRank score (typical
    range 1e-5..1e-3 on a 20k-symbol graph). The closed mapping is:

        >= 0.01   -> "error"
        >= 0.001  -> "warning"
        < 0.001   -> "note"

    We test a high-importance file (0.05 -> "error") and a low-importance
    file (0.0001 -> "note") in one envelope so both bands are exercised.
    """
    envelope = {
        "command": "impact",
        "symbol": "useThemeClasses",
        "summary": {"verdict": "Large blast radius"},
        "affected_file_list": [
            {"path": "src/hot/path.py", "importance": 0.05},
            {"path": "src/cold/path.py", "importance": 0.0001},
        ],
        "direct_dependents": {},
        "sf_convention_tests": [],
        "indirect_refs": [],
    }

    doc = impact_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    # Two affected_file findings; nothing else fired.
    assert len(results) == 2

    # First (importance 0.05) maps to "error".
    hot = next(
        r for r in results if r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/hot/path.py"
    )
    assert hot["ruleId"] == "impact/affected-file"
    assert hot["level"] == "error"
    # File-level anchor — no region/line.
    phys = hot["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/hot/path.py"
    assert "region" not in phys
    # Message mentions the target symbol so consumers can correlate.
    assert "useThemeClasses" in hot["message"]["text"]

    # Second (importance 0.0001) maps to "note".
    cold = next(
        r for r in results if r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/cold/path.py"
    )
    assert cold["level"] == "note"


def test_direct_dependent_finding_carries_secondary_location() -> None:
    """A direct_dependents[edge_kind][] entry emits one result per dependent.

    Each result anchors on the dependent's file; the edge kind appears
    in the message so a SARIF consumer (GitHub Code Scanning) can
    surface the relationship (calls / imports) without parsing the
    free-text body.
    """
    envelope = {
        "command": "impact",
        "symbol": "handleSave",
        "summary": {"verdict": "Moderate blast radius"},
        "affected_file_list": [],
        "direct_dependents": {
            "call": [
                {"name": "submitForm", "kind": "function", "file": "src/forms.py:42"},
            ],
            "import": [
                {"name": "FormView", "kind": "class", "file": "src/views.py"},
            ],
        },
        "sf_convention_tests": [],
        "indirect_refs": [],
    }

    doc = impact_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    # Two direct-dependent findings (one per dependent across both edge kinds).
    assert len(results) == 2

    by_name = {r["message"]["text"]: r for r in results}
    call_msg = next(t for t in by_name if "submitForm" in t)
    import_msg = next(t for t in by_name if "FormView" in t)

    # Call-edge dependent anchored at src/forms.py with line 42.
    call_r = by_name[call_msg]
    assert call_r["ruleId"] == "impact/direct-dependent"
    assert call_r["level"] == "note"
    phys = call_r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/forms.py"
    assert phys["region"]["startLine"] == 42
    # Edge kind surfaces in the message body.
    assert "call" in call_msg
    assert "handleSave" in call_msg

    # Import-edge dependent has no line (parse falls through cleanly).
    import_r = by_name[import_msg]
    phys = import_r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/views.py"
    assert "region" not in phys
    assert "import" in import_msg


def test_indirect_ref_finding_carries_file_and_line() -> None:
    """An indirect_refs entry (registry / string-dispatch) anchors on file + line.

    The _find_indirect_refs helper in cmd_impact emits dicts shaped
    ``{file, line, match}``. The SARIF projection should preserve all
    three: file + line on the location, match string inside the message.
    """
    envelope = {
        "command": "impact",
        "symbol": "cmd_search",
        "summary": {"verdict": "Moderate blast radius"},
        "affected_file_list": [],
        "direct_dependents": {},
        "sf_convention_tests": [],
        "indirect_refs": [
            {"file": "src/roam/cli.py", "line": 140, "match": "'cmd_search'"},
        ],
    }

    doc = impact_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "impact/indirect-ref"
    assert r["level"] == "note"
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/roam/cli.py"
    assert phys["region"]["startLine"] == 140
    # The matched literal surfaces in the message body so the SARIF
    # consumer can show "what we found" without an envelope round-trip.
    assert "cmd_search" in r["message"]["text"]
