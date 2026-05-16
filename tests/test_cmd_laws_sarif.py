"""W1216: SARIF projection for ``roam laws check`` mined-law violations.

cmd_laws runs a mined ``roam-laws.yml`` against a diff and reports per-
violation file:line findings via :class:`roam.laws.miner.Violation`.

SARIF projection emits five closed-enum rule ids (one per law kind:
naming / import-layering / test-coverage / error-handling / co-change)
with uniform ``defaultLevel`` ``note`` — mined laws describe emergent
conventions, not invariants, so the advisory band is the safer
default. Per-result level via ``_laws_severity_level``:

- blocker  -> ``error``
- warning  -> ``warning``
- advisory / unknown -> ``note``

Per-finding anchor: ``file`` + ``line`` (the diff hunk line that
introduced the violation). Mirrors the closed-enum design from
``test_cmd_bus_factor_sarif.py`` (W1215) and
``test_cmd_orphan_imports_sarif.py`` (W1218).
"""

from __future__ import annotations

from roam.output.sarif import laws_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty violations list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_bus_factor / cmd_orphan_imports
    "no findings" path.
    """
    doc = laws_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum: 5 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "laws/naming",
        "laws/import-layering",
        "laws/test-coverage",
        "laws/error-handling",
        "laws/co-change",
    }
    # Every law-rule defaults to ``note`` — mined laws describe
    # conventions, not invariants, so the advisory band is the safer
    # default. Per-result level still varies via _laws_severity_level
    # when a rule override raises a violation to warning / blocker.
    level_by_id = {r["id"]: r["defaultConfiguration"]["level"] for r in rules}
    assert level_by_id == {
        "laws/naming": "note",
        "laws/import-layering": "note",
        "laws/test-coverage": "note",
        "laws/error-handling": "note",
        "laws/co-change": "note",
    }


def test_violations_map_to_kind_rules_and_severity_levels() -> None:
    """Per-violation kinds map onto distinct rules; severity drives level.

    Verifies the kind -> rule routing for naming / import / testing
    plus the severity -> level translation (advisory -> note, warning
    -> warning, blocker -> error). Anchor verification: ``file`` +
    ``line`` projects onto a physicalLocation with both
    ``artifactLocation.uri`` and a non-zero ``region.startLine``.
    """
    findings = [
        # naming kind + advisory -> laws/naming + note.
        {
            "law_id": "snake_case_functions",
            "kind": "naming",
            "severity": "advisory",
            "confidence": "high",
            "message": "Function 'MyFunc' does not follow snake_case",
            "file": "src/app/handlers.py",
            "line": 42,
            "evidence": {},
        },
        # import kind + warning -> laws/import-layering + warning.
        {
            "law_id": "handlers_no_db",
            "kind": "import",
            "severity": "warning",
            "confidence": "medium",
            "message": "src/app/handlers imports from src/app/db",
            "file": "src/app/handlers.py",
            "line": 7,
            "evidence": {},
        },
        # testing kind + blocker -> laws/test-coverage + error.
        {
            "law_id": "public_functions_must_be_tested",
            "kind": "testing",
            "severity": "blocker",
            "confidence": "medium",
            "message": "new public function 'process' has no matching test",
            "file": "src/app/process.py",
            "line": 1,
            "evidence": {},
        },
    ]

    doc = laws_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 3

    by_rule = {r["ruleId"]: r for r in results}
    assert set(by_rule) == {
        "laws/naming",
        "laws/import-layering",
        "laws/test-coverage",
    }

    # naming + advisory -> note
    naming = by_rule["laws/naming"]
    assert naming["level"] == "note"
    naming_phys = naming["locations"][0]["physicalLocation"]
    assert naming_phys["artifactLocation"]["uri"] == "src/app/handlers.py"
    assert naming_phys["region"]["startLine"] == 42
    text = naming["message"]["text"]
    assert "snake_case_functions" in text
    assert "MyFunc" in text
    assert "severity=advisory" in text

    # import + warning -> warning
    imp = by_rule["laws/import-layering"]
    assert imp["level"] == "warning"
    imp_phys = imp["locations"][0]["physicalLocation"]
    assert imp_phys["artifactLocation"]["uri"] == "src/app/handlers.py"
    assert imp_phys["region"]["startLine"] == 7

    # testing + blocker -> error
    tst = by_rule["laws/test-coverage"]
    assert tst["level"] == "error"
    tst_phys = tst["locations"][0]["physicalLocation"]
    assert tst_phys["artifactLocation"]["uri"] == "src/app/process.py"
    assert tst_phys["region"]["startLine"] == 1


def test_malformed_entries_and_unknown_kinds_are_skipped() -> None:
    """Non-dict / missing-file / unknown-kind entries are skipped.

    Defensive parsing per Pattern 1 family discipline — the SARIF
    emitter must not crash on a malformed entry. Anchor-less findings
    (no ``file``) cannot be surfaced meaningfully so we skip rather
    than emit an anchorless row (matches LAW 6 disclosure rules).
    Future kinds outside the closed enumeration are also skipped
    (LAW 8 closed-enum discipline). Line=0 / missing line drops the
    ``region`` key entirely so SARIF consumers don't anchor to the
    synthetic ``startLine: 0``.
    """
    findings = [
        "not a dict",  # skipped (defensive parse)
        # Missing file -> skipped (no anchor).
        {
            "law_id": "x",
            "kind": "naming",
            "severity": "advisory",
            "message": "no anchor here",
            "file": "",
            "line": 12,
        },
        # Future / unknown kind -> skipped (LAW 8).
        {
            "law_id": "future_law",
            "kind": "unmapped_kind",
            "severity": "warning",
            "message": "future kind",
            "file": "src/x.py",
            "line": 5,
        },
        # Well-formed but line=0 -> region key omitted entirely.
        {
            "law_id": "snake_case_functions",
            "kind": "naming",
            "severity": "advisory",
            "message": "no line info",
            "file": "src/orphan.py",
            "line": 0,
        },
    ]

    doc = laws_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Only the line=0 well-formed entry survives.
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "laws/naming"
    assert r["level"] == "note"
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/orphan.py"
    # line <= 0 drops the region key entirely (no synthetic
    # ``startLine: 0`` anchor).
    assert "region" not in phys
