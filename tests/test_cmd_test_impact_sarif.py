"""W1203: SARIF projection for ``roam test-impact`` output.

cmd_test_impact ranks tests by how many changed symbols transitively
reach them (BFS over the reverse call graph). The SARIF projection
exposes that ranking to CI consumers (GitHub Code Scanning, etc.) using
a single closed-enum rule ``test-impact/affected-test`` whose severity
band reflects the test's ``reach_count``:

- ``reach_count >= 20`` -> SARIF ``warning`` (high-impact test).
- ``reach_count < 20``  -> SARIF ``note`` (moderate / low ranking band;
  informational only — no error band because test-impact is a ranker,
  not a gate).

Mirrors the test design from ``test_cmd_affected_tests_sarif.py``:
every band the command can emit must round-trip through SARIF without
dropping its severity / message / anchor, and the rule catalogue is
always emitted even on empty input.
"""

from __future__ import annotations

# Import-alias dance: pytest collects any callable starting with ``test_``
# inside a test module's namespace, even ones imported from another
# module. The producer function below is the SUT, not a test case, so
# we rename at import to keep pytest from treating it as a fixture-less
# test (which would surface as ``fixture 'data' not found``).
from roam.output.sarif import test_impact_to_sarif as _test_impact_to_sarif


def test_empty_tests_produces_valid_sarif_with_zero_results() -> None:
    """An empty ``tests[]`` envelope emits a valid SARIF doc with 0 results.

    Mirrors the cmd_affected_tests / cmd_impact "no findings" path: the
    rules array is always populated (so consumers can introspect the
    rule catalogue even when nothing fired), but ``results`` is empty.
    """
    empty_envelope = {
        "command": "test-impact",
        "summary": {"verdict": "no tests reach the 0 changed file(s)", "count": 0},
        "changed_files": [],
        "tests": [],
    }

    doc = _test_impact_to_sarif(empty_envelope)

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 1 rule).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {"test-impact/affected-test"}
    # defaultLevel is ``note`` — informational ranker, no gate. The
    # SARIF builder projects ``defaultLevel`` onto
    # ``defaultConfiguration.level`` per the 2.1.0 schema.
    rule = next(r for r in rules if r["id"] == "test-impact/affected-test")
    assert rule.get("defaultConfiguration", {}).get("level") == "note"


def test_single_test_low_reach_count_projects_to_note() -> None:
    """A single test with low ``reach_count`` projects to SARIF ``note``.

    reach_count < 20 is the informational band — the test is reachable
    from the changeset but not a hotspot. SARIF level is ``note`` so CI
    consumers can surface it without escalation.

    Anchor is file-level (no region) because cmd_test_impact does not
    carry per-test line numbers in its envelope.
    """
    envelope = {
        "command": "test-impact",
        "summary": {
            "verdict": "1 test file(s) reachable from 2 changed file(s)",
            "count": 1,
        },
        "changed_files": ["src/auth.py", "src/session.py"],
        "tests": [
            {"file": "tests/test_auth.py", "reach_count": 3},
        ],
    }

    doc = _test_impact_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 1

    r = results[0]
    assert r["ruleId"] == "test-impact/affected-test"
    assert r["level"] == "note"
    # File-level anchor — no region/line.
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "tests/test_auth.py"
    assert "region" not in phys
    # Message mentions reach_count + changed-file count so SARIF
    # consumers correlate findings to the triggering changeset.
    text = r["message"]["text"]
    assert "tests/test_auth.py" in text
    assert "3" in text  # reach_count
    assert "2" in text  # changed file count


def test_multiple_tests_severity_bands_scale_with_reach_count() -> None:
    """High reach_count tests project to ``warning``; low/moderate to ``note``.

    Verifies the closed-enum band mapping in
    :func:`_test_impact_reach_level`:

    - reach_count >= 20 -> ``warning`` (high-impact)
    - 5 <= reach_count < 20 -> ``note`` (moderate)
    - reach_count < 5 -> ``note`` (low)

    All three rows project onto the SAME rule id
    (``test-impact/affected-test``) — only the per-result ``level``
    differs. No SARIF ``error`` band is ever emitted because
    test-impact is a ranker, not a gate-failing finding family.
    """
    envelope = {
        "command": "test-impact",
        "summary": {
            "verdict": "3 test file(s) reachable from 5 changed file(s)",
            "count": 3,
        },
        "changed_files": ["a.py", "b.py", "c.py", "d.py", "e.py"],
        "tests": [
            {"file": "tests/test_hot.py", "reach_count": 25},  # warning
            {"file": "tests/test_mid.py", "reach_count": 10},  # note
            {"file": "tests/test_cold.py", "reach_count": 2},  # note
        ],
    }

    doc = _test_impact_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 3

    # Every result uses the single closed-enum rule id.
    rule_ids = {r["ruleId"] for r in results}
    assert rule_ids == {"test-impact/affected-test"}

    # Per-result level scales with reach_count.
    by_file = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r["level"] for r in results}
    assert by_file["tests/test_hot.py"] == "warning"
    assert by_file["tests/test_mid.py"] == "note"
    assert by_file["tests/test_cold.py"] == "note"

    # No SARIF ``error`` level — test-impact is a ranker, not a gate.
    levels = {r["level"] for r in results}
    assert "error" not in levels


def test_malformed_entries_are_skipped_without_crash() -> None:
    """Non-dict entries / empty file paths skip silently (no crash).

    Defensive parsing per Pattern 1 family discipline — the SARIF emitter
    must not crash on a malformed entry, since the producer envelope
    can carry partial data when the underlying BFS hits an exception.
    """
    envelope = {
        "command": "test-impact",
        "summary": {"verdict": "partial", "count": 1},
        "changed_files": ["src/a.py"],
        "tests": [
            "not a dict",  # skipped
            {"file": "", "reach_count": 10},  # skipped (empty path)
            {"file": "tests/test_ok.py", "reach_count": 7},  # kept
            {"file": "tests/test_bad_count.py", "reach_count": "garbage"},  # kept, treated as 0
        ],
    }

    doc = _test_impact_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    # Two valid entries survived (the dict + the dict-with-garbage-count).
    assert len(results) == 2
    paths = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in results}
    assert paths == {"tests/test_ok.py", "tests/test_bad_count.py"}
    # Garbage reach_count fell back to 0 -> note band.
    by_file = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r["level"] for r in results}
    assert by_file["tests/test_bad_count.py"] == "note"
