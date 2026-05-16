"""W1171: SARIF projection for ``roam smells`` detector output.

The killer signal for smells is *which symbols carry which structural
anti-pattern at which severity*. ~24 smell kinds project onto closed-enum
SARIF rule ids of the form ``smells/<smell_id>``; per-finding severity
drives the SARIF level so a CI consumer (GitHub Code Scanning) can triage
critical smells (brain-method, god-class, large-class) ahead of info-only
exploratory smells (data-clumps, message-chain).

Severity -> SARIF level (closed mapping via
:func:`roam.output._severity.to_sarif_level`):

    critical  -> "error"
    warning   -> "warning"
    info      -> "note"

Mirrors the test design from ``test_cmd_impact_sarif.py`` and
``test_cmd_affected_tests_sarif.py``: every detector kind the command
can emit must round-trip through SARIF without dropping its severity /
message / file-line anchor.
"""

from __future__ import annotations

from roam.output.sarif import smells_to_sarif


def test_empty_findings_produces_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    Mirrors the cmd_complexity / cmd_dead / cmd_impact "no findings"
    path: the rules array is always populated (so consumers can
    introspect the rule catalogue even when nothing fired), but
    ``results`` is empty. The rule catalogue is derived from
    :mod:`roam.catalog.registry` — one rule per registered smell kind.
    """
    doc = smells_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present and reflects the registry.
    rules = run["tool"]["driver"]["rules"]
    assert len(rules) > 0, "rules catalogue must include all registered smell kinds"
    # Every rule id is namespaced under smells/...
    for r in rules:
        assert r["id"].startswith("smells/"), r["id"]
        # SARIF 2.1.0 renders ``defaultLevel`` as
        # ``defaultConfiguration.level`` (see _build_rule).
        assert r["defaultConfiguration"]["level"] == "warning"
    # Rules are sorted alphabetically (SARIF-stable output per W896).
    rule_ids = [r["id"] for r in rules]
    assert rule_ids == sorted(rule_ids), f"rules must be sorted alphabetically for stable SARIF output: {rule_ids}"
    # Spot-check a few well-known smell kinds — they MUST be in the
    # catalogue because the registry-import is what populates it.
    rule_id_set = set(rule_ids)
    assert "smells/brain-method" in rule_id_set
    assert "smells/god-class" in rule_id_set
    assert "smells/deep-nesting" in rule_id_set


def test_single_critical_finding_round_trips_with_error_level() -> None:
    """A critical-severity smell (e.g. brain-method) maps to SARIF level=error.

    Closed mapping: critical -> "error", warning -> "warning", info -> "note".
    The finding's file:line anchor parses out of the ``location`` string
    via :func:`_parse_loc_string`; the symbol_name + description appear
    in the message body so SARIF consumers can triage without parsing
    a JSON envelope.
    """
    findings = [
        {
            "smell_id": "brain-method",
            "severity": "critical",
            "symbol_name": "process_request",
            "kind": "function",
            "location": "src/server/handler.py:142",
            "metric_value": 87,
            "threshold": 60,
            "description": "Brain method: complexity 87, 142 LOC",
        }
    ]

    doc = smells_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "smells/brain-method"
    assert r["level"] == "error"
    # Anchor: file + line parsed from "src/server/handler.py:142".
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/server/handler.py"
    assert phys["region"]["startLine"] == 142
    # Message carries symbol_name + description for triage.
    text = r["message"]["text"]
    assert "process_request" in text
    assert "brain-method" in text


def test_multi_kind_findings_round_trip_with_distinct_levels() -> None:
    """A mixed bag of severities round-trips each onto its SARIF level.

    The same SARIF document carries findings spanning all three
    severity bands (critical/warning/info), each anchored on its own
    file:line, each on its own ``smells/<kind>`` rule id. This is the
    realistic CI case — `roam --sarif smells` on a 20k-symbol codebase
    typically emits 100s of findings across 10+ kinds.

    Per W896 (sorted output), the rule catalogue stays alphabetical
    regardless of finding-encounter order.
    """
    findings = [
        {
            "smell_id": "god-class",
            "severity": "critical",
            "symbol_name": "UserManager",
            "kind": "class",
            "location": "src/auth/user_manager.py:12",
            "metric_value": 85,
            "threshold": 50,
            "description": "God class: 85 methods + state span",
        },
        {
            "smell_id": "deep-nesting",
            "severity": "warning",
            "symbol_name": "validate_payload",
            "kind": "function",
            "location": "src/api/validators.py:88",
            "metric_value": 6,
            "threshold": 4,
            "description": "Nesting depth 6 exceeds threshold 4",
        },
        {
            "smell_id": "data-clumps",
            "severity": "info",
            "symbol_name": "address_fields",
            "kind": "parameter_group",
            "location": "src/models/address.py:34",
            "metric_value": 5,
            "threshold": 3,
            "description": "5 parameters appear together in 4 sites",
        },
    ]

    doc = smells_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 3

    by_rule = {r["ruleId"]: r for r in results}
    assert by_rule["smells/god-class"]["level"] == "error"
    assert by_rule["smells/deep-nesting"]["level"] == "warning"
    assert by_rule["smells/data-clumps"]["level"] == "note"

    # File anchors round-trip cleanly.
    god = by_rule["smells/god-class"]
    god_phys = god["locations"][0]["physicalLocation"]
    assert god_phys["artifactLocation"]["uri"] == "src/auth/user_manager.py"
    assert god_phys["region"]["startLine"] == 12

    # Symbol names appear in messages so consumers can correlate
    # without a JSON-envelope round-trip.
    assert "UserManager" in by_rule["smells/god-class"]["message"]["text"]
    assert "validate_payload" in by_rule["smells/deep-nesting"]["message"]["text"]
    assert "address_fields" in by_rule["smells/data-clumps"]["message"]["text"]


def test_unknown_smell_id_is_skipped_not_minted_on_the_fly() -> None:
    """Unknown smell_id values are skipped — closed-enum discipline (LAW 8).

    A plugin-registered detector that hasn't landed in
    :mod:`roam.catalog.registry` yet must not crash the SARIF
    projection NOR mint a fresh rule on the fly. The SARIF rule
    catalogue is closed-by-construction over the registry; extending
    the vocabulary is a deliberate registry edit. This guards against
    the W1159/W1160 mismatch pattern (string-composed rule ids).
    """
    findings = [
        {
            "smell_id": "future-plugin-detector",  # not in registry
            "severity": "warning",
            "symbol_name": "some_symbol",
            "kind": "function",
            "location": "src/plugin/code.py:1",
            "metric_value": 1,
            "threshold": 0,
            "description": "fictional plugin detector",
        },
        # Mixed-with-known: known finding still round-trips.
        {
            "smell_id": "brain-method",
            "severity": "critical",
            "symbol_name": "real_brain_method",
            "kind": "function",
            "location": "src/real.py:5",
            "metric_value": 87,
            "threshold": 60,
            "description": "real brain method",
        },
    ]

    doc = smells_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # The unknown smell_id is dropped; the known one survives.
    assert len(results) == 1
    assert results[0]["ruleId"] == "smells/brain-method"
    # The rule catalogue never grew an entry for the unknown kind.
    rule_ids = {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]}
    assert "smells/future-plugin-detector" not in rule_ids


def test_finding_without_location_emits_zero_locations() -> None:
    """A finding with empty location parses cleanly to an empty anchor list.

    Empty ``locations`` is valid per SARIF 2.1.0 — signals "applies to
    the whole artifact set / run" (matches ``critique_to_sarif``'s
    handling of the ``intent`` check). This guards the edge case where
    a smell detector emits a file-less finding (e.g. cross-cutting
    architectural smell).
    """
    findings = [
        {
            "smell_id": "shotgun-surgery",
            "severity": "warning",
            "symbol_name": "log_event",
            "kind": "function",
            "location": "",  # empty — no anchor
            "metric_value": 12,
            "threshold": 5,
            "description": "Symbol changed in 12 files in last 90 days",
        }
    ]

    doc = smells_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["locations"] == []
    # The finding still carries severity + message — only the anchor
    # is degraded.
    assert results[0]["level"] == "warning"
    assert "log_event" in results[0]["message"]["text"]
