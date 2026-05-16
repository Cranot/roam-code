"""W1160: SARIF projection for ``roam affected-tests`` output.

The killer signal for affected-tests is *which tests cover the changed
symbol/file* and *how directly* they cover it. Three kinds project onto
three closed-enum SARIF rule ids, each with a distinct severity so a CI
consumer (GitHub Code Scanning, code-scanning APIs) can triage by
distance from the change:

- ``affected-tests/direct`` (defaultLevel ``error``): test calls the
  changed symbol with no indirection.
- ``affected-tests/transitive`` (defaultLevel ``warning``): test reaches
  the changed symbol through intermediate callers.
- ``affected-tests/colocated`` (defaultLevel ``note``): test file shares
  a directory with a changed source file (filename convention only).

Mirrors the test design from ``test_cmd_impact_sarif.py``: every kind
the command can emit must round-trip through SARIF without dropping
its severity / message / anchor.
"""

from __future__ import annotations

from roam.output.sarif import affected_tests_to_sarif


def test_empty_tests_produces_valid_sarif_with_zero_results() -> None:
    """An empty ``tests[]`` envelope emits a valid SARIF doc with 0 results.

    Mirrors the cmd_complexity / cmd_dead / cmd_impact "no findings"
    path: the rules array is always populated (so consumers can
    introspect the rule catalogue even when nothing fired), but
    ``results`` is empty.
    """
    empty_envelope = {
        "command": "affected-tests",
        "summary": {"target": "leaf_symbol"},
        "tests": [],
    }

    doc = affected_tests_to_sarif(empty_envelope)

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "affected-tests/direct",
        "affected-tests/transitive",
        "affected-tests/colocated",
    }


def test_direct_test_finding_has_error_severity_and_file_anchor() -> None:
    """A DIRECT test entry projects to ``error`` severity + file anchor.

    DIRECT is the strongest coverage signal — the test calls the
    changed symbol with no indirection (hops == 1). The CI severity
    is ``error`` so blocking-on-failed-affected-tests works out of
    the box.

    Anchor is file-level (no region) because the cmd_affected_tests
    envelope does not carry per-test line numbers as of W1160.
    """
    envelope = {
        "command": "affected-tests",
        "summary": {"target": "handle_login (fn, src/auth.py:42)"},
        "tests": [
            {
                "file": "tests/test_auth.py",
                "symbol": "test_handle_login_happy_path",
                "kind": "DIRECT",
                "hops": 1,
                "via": None,
            },
        ],
    }

    doc = affected_tests_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 1

    r = results[0]
    assert r["ruleId"] == "affected-tests/direct"
    assert r["level"] == "error"
    # File-level anchor — no region/line.
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "tests/test_auth.py"
    assert "region" not in phys
    # Message mentions the target symbol AND the covering test symbol
    # so SARIF consumers can correlate findings to the triggering
    # change without parsing the envelope.
    text = r["message"]["text"]
    assert "handle_login" in text
    assert "test_handle_login_happy_path" in text


def test_colocated_test_finding_has_note_severity_and_no_symbol_in_message() -> None:
    """A COLOCATED test entry projects to ``note`` severity (weakest signal).

    COLOCATED tests come from filename convention only — there is no
    graph edge linking them to the changed symbol, only a shared
    parent directory. The SARIF level is ``note`` so CI consumers can
    still surface them without blocking.

    The colocated entry has no associated ``symbol`` (it's a
    file-level discovery), so the message body must not try to
    reference a None symbol.
    """
    envelope = {
        "command": "affected-tests",
        "summary": {"target": "staged changes (3 files)"},
        "tests": [
            {
                "file": "tests/test_auth.py",
                "symbol": None,
                "kind": "COLOCATED",
                "hops": None,
                "via": None,
            },
        ],
    }

    doc = affected_tests_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 1

    r = results[0]
    assert r["ruleId"] == "affected-tests/colocated"
    assert r["level"] == "note"
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "tests/test_auth.py"
    # Message mentions the target so SARIF consumers correlate
    # findings to the triggering change.
    text = r["message"]["text"]
    assert "staged changes" in text
    assert "same directory" in text
    # No "None" should leak into the message body when symbol is absent
    # (an early bug in the projection would interpolate the literal).
    assert "None" not in text
