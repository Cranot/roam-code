"""W1192: SARIF projection for ``roam delete-check`` deletion-safety gate.

The killer signal for delete-check is *which deletions in the diff have
surviving references that would break the build*. Three rule ids project
onto SARIF — one per verdict, each with a distinct ``defaultLevel`` so
a CI gate keyed off SARIF ``level: error`` blocks on BREAK-RISK without
surfacing the advisory bands:

- ``delete-check/break-risk`` (defaultLevel ``error``): surviving
  reachable code references remain — deleting the target will break
  the build. Mirrors the cmd_delete_check exit-5 gate.
- ``delete-check/likely-safe`` (defaultLevel ``warning``): only test /
  docs / unreachable references survive — review recommended but not
  blocking.
- ``delete-check/safe`` (defaultLevel ``note``): no surviving
  references — informational, safe to delete.

Per-deletion anchor: PRIMARY = ``from_file:from_line`` (the deletion
site itself). SECONDARY = up to 10 survivors[] entries (each with
``path`` + ``line``). Mirrors the test design from
``test_cmd_clones_sarif.py`` (W1172) and ``test_cmd_partition_sarif.py``
(W1159).
"""

from __future__ import annotations

from roam.output.sarif import delete_check_to_sarif


def test_empty_delete_check_envelope_produces_valid_sarif_with_zero_results() -> None:
    """A zero-deletion envelope emits a valid SARIF doc with 0 results.

    Mirrors the cmd_clones / cmd_partition / cmd_impact "no findings"
    path: the rules array is always populated (so consumers can
    introspect the rule catalogue even when nothing fired), but
    ``results`` is empty.
    """
    empty_envelope = {"command": "delete-check", "deletions": []}

    doc = delete_check_to_sarif(empty_envelope)

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "delete-check/break-risk",
        "delete-check/likely-safe",
        "delete-check/safe",
    }
    # Each rule carries its closed-enum defaultLevel (projected onto
    # SARIF's ``defaultConfiguration.level`` by ``_build_rule``).
    by_id = {r["id"]: r for r in rules}
    assert by_id["delete-check/break-risk"]["defaultConfiguration"]["level"] == "error"
    assert by_id["delete-check/likely-safe"]["defaultConfiguration"]["level"] == "warning"
    assert by_id["delete-check/safe"]["defaultConfiguration"]["level"] == "note"


def test_break_risk_deletion_maps_to_error_level_with_survivor_secondary_locations() -> None:
    """A BREAK-RISK deletion projects onto ``delete-check/break-risk``.

    Per-result level = ``error`` (gate-blocking). PRIMARY anchor =
    ``from_file:from_line`` (the deletion site itself); SECONDARY =
    survivors[] entries. Exercises the two-sided anchor pattern:
    consumers can navigate from the deletion site directly to surviving
    callers without a JSON-envelope round-trip.
    """
    envelope = {
        "command": "delete-check",
        "deletions": [
            {
                "kind": "symbol",
                "name": "handleSave",
                "from_file": "src/ui/form.py",
                "from_line": 42,
                "verdict": "BREAK-RISK",
                "reason": "3 surviving reachable code reference(s)",
                "survivors": [
                    {
                        "path": "src/ui/main.py",
                        "line": 17,
                        "enclosing_symbol": "App.submit",
                        "reachable": True,
                        "surface": "code",
                    },
                    {
                        "path": "src/ui/dialogs.py",
                        "line": 88,
                        "enclosing_symbol": "Dialog.confirm",
                        "reachable": True,
                        "surface": "code",
                    },
                ],
            }
        ],
    }

    doc = delete_check_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "delete-check/break-risk"
    assert r["level"] == "error"

    # PRIMARY anchor = the deletion site (from_file:from_line).
    locs = r["locations"]
    assert len(locs) == 3  # 1 PRIMARY + 2 SECONDARY survivors
    assert locs[0]["physicalLocation"]["artifactLocation"]["uri"] == "src/ui/form.py"
    assert locs[0]["physicalLocation"]["region"]["startLine"] == 42

    # SECONDARY locations follow in survivor order.
    assert locs[1]["physicalLocation"]["artifactLocation"]["uri"] == "src/ui/main.py"
    assert locs[1]["physicalLocation"]["region"]["startLine"] == 17
    assert locs[2]["physicalLocation"]["artifactLocation"]["uri"] == "src/ui/dialogs.py"
    assert locs[2]["physicalLocation"]["region"]["startLine"] == 88

    # Message carries verdict + kind + name + reason.
    msg = r["message"]["text"]
    assert "BREAK-RISK" in msg
    assert "symbol" in msg
    assert "handleSave" in msg
    assert "3 surviving reachable" in msg


def test_likely_safe_and_safe_verdicts_map_to_distinct_rule_ids_and_levels() -> None:
    """LIKELY-SAFE -> ``delete-check/likely-safe`` (warning); SAFE ->
    ``delete-check/safe`` (note).

    Both verdicts produce results in the same SARIF document. A CI gate
    keyed off ``level: error`` will NOT block on either — they are
    advisory signals (review-recommended for LIKELY-SAFE; informational
    for SAFE).
    """
    envelope = {
        "command": "delete-check",
        "deletions": [
            {
                "kind": "symbol",
                "name": "old_helper",
                "from_file": "src/legacy/util.py",
                "from_line": 100,
                "verdict": "LIKELY-SAFE",
                "reason": "2 test / 1 doc reference(s)",
                "survivors": [
                    {
                        "path": "tests/test_util.py",
                        "line": 5,
                        "surface": "test",
                    }
                ],
            },
            {
                "kind": "file",
                "name": "src/legacy/dead.py",
                "from_file": "src/legacy/dead.py",
                "from_line": 0,
                "verdict": "SAFE",
                "reason": "no surviving references",
                "survivors": [],
            },
        ],
    }

    doc = delete_check_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 2

    by_rule = {r["ruleId"]: r for r in results}

    likely = by_rule["delete-check/likely-safe"]
    assert likely["level"] == "warning"
    assert "LIKELY-SAFE" in likely["message"]["text"]
    assert "old_helper" in likely["message"]["text"]
    # One survivor + the PRIMARY anchor = 2 locations.
    assert len(likely["locations"]) == 2

    safe = by_rule["delete-check/safe"]
    assert safe["level"] == "note"
    assert "SAFE" in safe["message"]["text"]
    assert "dead.py" in safe["message"]["text"]
    # Full-file deletion: from_line == 0; only the PRIMARY anchor
    # location is emitted (no survivors), and the SARIF region is
    # omitted (line <= 0).
    assert len(safe["locations"]) == 1
    safe_loc = safe["locations"][0]["physicalLocation"]
    assert safe_loc["artifactLocation"]["uri"] == "src/legacy/dead.py"
    assert "region" not in safe_loc  # from_line=0 -> no region


def test_break_risk_truncates_oversized_survivors_to_secondary_cap() -> None:
    """A BREAK-RISK deletion with 15 survivors collapses to 1 PRIMARY +
    10 SECONDARY locations.

    Larger-than-cap survivor lists must NOT overflow the SARIF
    document — the secondary cap
    (``_DELETE_CHECK_MAX_SECONDARY_LOCS = 10``) is a hard limit so a
    god-class deletion with hundreds of surviving callers cannot
    inflate the document beyond what GitHub Code Scanning can render.
    """
    survivors = [
        {
            "path": f"src/caller_{i}.py",
            "line": 10 + i,
            "surface": "code",
            "reachable": True,
        }
        for i in range(15)
    ]
    envelope = {
        "command": "delete-check",
        "deletions": [
            {
                "kind": "symbol",
                "name": "god_method",
                "from_file": "src/core/god.py",
                "from_line": 1,
                "verdict": "BREAK-RISK",
                "reason": "15 surviving reachable code reference(s)",
                "survivors": survivors,
            }
        ],
    }

    doc = delete_check_to_sarif(envelope)
    result = doc["runs"][0]["results"][0]
    # 15 survivors capped to 10 SECONDARY + 1 PRIMARY = 11 locations.
    assert len(result["locations"]) == 11


def test_unknown_verdict_and_missing_from_file_are_skipped() -> None:
    """Defensive: rows without a recognised verdict OR without a
    ``from_file`` PRIMARY anchor are dropped.

    Closed enumeration over free string composition (LAW 8): a future
    verdict literal that hasn't landed in :data:`_VERDICT_TO_RULE_LEVEL`
    is skipped rather than minting a rule on the fly. Anchorless rows
    are skipped to match the ``clones_to_sarif`` pair-without-file_a
    discipline.
    """
    envelope = {
        "command": "delete-check",
        "deletions": [
            {
                "kind": "symbol",
                "name": "future_verdict",
                "from_file": "src/a.py",
                "from_line": 1,
                "verdict": "WIP-UNKNOWN",  # not in the closed enum
                "reason": "n/a",
                "survivors": [],
            },
            {
                "kind": "symbol",
                "name": "no_anchor",
                "from_file": "",  # missing PRIMARY anchor
                "from_line": 0,
                "verdict": "BREAK-RISK",
                "reason": "n/a",
                "survivors": [],
            },
            {
                "kind": "symbol",
                "name": "valid",
                "from_file": "src/b.py",
                "from_line": 5,
                "verdict": "SAFE",
                "reason": "no surviving references",
                "survivors": [],
            },
        ],
    }

    doc = delete_check_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    # Only the third row survives — the first has an unknown verdict,
    # the second has no PRIMARY anchor.
    assert len(results) == 1
    assert results[0]["ruleId"] == "delete-check/safe"
    assert "valid" in results[0]["message"]["text"]
