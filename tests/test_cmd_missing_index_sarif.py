"""W1217: SARIF projection for ``roam missing-index`` unindexed-query findings.

cmd_missing_index detects database queries that filter or sort on columns
lacking indexes (Laravel migration cross-referenced against
``->where()`` / ``->orderBy()`` call sites). Each finding maps onto one
of three closed-enum rule ids by confidence label:

- ``missing-index/high-confidence`` (defaultLevel ``error``): WHERE on
  an unindexed column in a paginated query — bounded result set means
  filtering is happening; missing index means a guaranteed table scan.
- ``missing-index/medium-confidence`` (defaultLevel ``warning``):
  orderBy on a non-indexed column, OR a paginated WHERE without
  composite coverage.
- ``missing-index/low-confidence`` (defaultLevel ``note``): the column
  has an individual index but no composite covering filter + sort
  (orderby_with_where heuristic).

Per-finding anchor: ``query_location`` (parsed as ``path:line``) — the
line that runs the unindexed query. Mirrors the closed-enum design
from ``test_cmd_n1_sarif.py`` (W1208) and ``test_cmd_auth_gaps_sarif.py``
(W1195).
"""

from __future__ import annotations

from roam.output.sarif import missing_index_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_n1 / cmd_auth_gaps /
    cmd_smells "no findings" path.
    """
    doc = missing_index_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "missing-index/high-confidence",
        "missing-index/medium-confidence",
        "missing-index/low-confidence",
    }
    # Each rule carries its closed-enum defaultLevel — surfaced via the
    # SARIF builder onto ``defaultConfiguration.level``.
    by_id = {r["id"]: r for r in rules}
    assert by_id["missing-index/high-confidence"]["defaultConfiguration"]["level"] == "error"
    assert by_id["missing-index/medium-confidence"]["defaultConfiguration"]["level"] == "warning"
    assert by_id["missing-index/low-confidence"]["defaultConfiguration"]["level"] == "note"


def test_high_confidence_finding_maps_to_error_band() -> None:
    """A high-confidence finding projects onto ``missing-index/high-confidence``
    at ``level: error``.

    WHERE on an unindexed column inside a paginated query — guaranteed
    table scan. SARIF level is ``error`` so a CI gate keyed off
    ``level: error`` blocks the change.
    """
    findings = [
        {
            "confidence": "high",
            "table": "orders",
            "columns": ["customer_id"],
            "issue": "no index on customer_id",
            "query_location": "app/Services/OrderService.php:120",
            "query_kind": "service",
            "has_paginate": True,
            "pattern_type": "single_where",
            "suggestion": ("Add index on customer_id, or a composite index starting with customer_id"),
            "missing_individual": ["customer_id"],
        }
    ]

    doc = missing_index_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "missing-index/high-confidence"
    assert r["level"] == "error"

    # Anchor is query_location parsed as path:line.
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "app/Services/OrderService.php"
    assert phys["region"]["startLine"] == 120

    # Message body surfaces table.cols + pattern_type + paginate flag
    # + issue + fix suggestion so SARIF consumers can triage without a
    # JSON-envelope round-trip.
    text = r["message"]["text"]
    assert "orders.customer_id" in text
    assert "single_where" in text
    assert "paginated" in text
    assert "no index on customer_id" in text
    assert "Add index on customer_id" in text


def test_medium_and_low_confidence_findings_scale_severity_bands() -> None:
    """Medium-confidence -> ``warning``, low-confidence -> ``note``.

    Verifies the closed-enum band mapping in
    :func:`_missing_index_confidence_level`: each confidence label
    projects onto a distinct rule id AND distinct level, so a CI gate
    keyed off SARIF ``level: error`` blocks ONLY on high-confidence
    findings (paginated query on an unindexed column).
    """
    findings = [
        {
            "confidence": "medium",
            "table": "posts",
            "columns": ["created_at"],
            "issue": "orderBy on non-indexed column created_at",
            "query_location": "app/Models/Post.php:45",
            "query_kind": "model",
            "has_paginate": False,
            "pattern_type": "orderby",
            "suggestion": "Add index on created_at",
            "missing_individual": ["created_at"],
        },
        {
            "confidence": "low",
            "table": "comments",
            "columns": ["post_id"],
            "issue": ("post_id has an index but no composite index covering filter+sort (post_id, created_at)"),
            "query_location": "app/Models/Comment.php:80",
            "query_kind": "model",
            "has_paginate": True,
            "pattern_type": "orderby_with_where",
            "suggestion": "Consider a composite index on (post_id, created_at)",
            "missing_individual": [],
        },
    ]

    doc = missing_index_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 2

    by_rule = {r["ruleId"]: r for r in results}
    assert "missing-index/medium-confidence" in by_rule
    assert "missing-index/low-confidence" in by_rule

    assert by_rule["missing-index/medium-confidence"]["level"] == "warning"
    assert by_rule["missing-index/low-confidence"]["level"] == "note"

    # No error band is emitted (no high-confidence findings).
    levels = {r["level"] for r in results}
    assert "error" not in levels


def test_malformed_entries_are_skipped_without_crash() -> None:
    """Non-dict entries / empty query_location / unknown confidence
    are skipped silently (no crash).

    Defensive parsing per Pattern 1 family discipline — the SARIF
    emitter must not crash on a malformed entry, since the producer
    envelope can carry partial data when the underlying analyzer hits
    an exception.
    """
    findings = [
        "not a dict",  # skipped
        {
            "confidence": "high",
            "table": "x",
            "columns": ["y"],
            "query_location": "",  # empty anchor — skipped
            "pattern_type": "single_where",
        },
        {
            "confidence": "bogus",  # unknown label — skipped (closed enum)
            "table": "x",
            "columns": ["y"],
            "query_location": "foo.php:10",
            "pattern_type": "single_where",
        },
        {
            "confidence": "medium",
            "table": "ok_table",
            "columns": ["ok_col"],
            "issue": "orderBy on non-indexed column ok_col",
            "query_location": "ok.php:5",
            "query_kind": "service",
            "has_paginate": False,
            "pattern_type": "orderby",
            "suggestion": "Add index on ok_col",
        },  # kept
    ]

    doc = missing_index_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Only the well-formed medium-confidence entry survived.
    assert len(results) == 1
    assert results[0]["ruleId"] == "missing-index/medium-confidence"
    assert results[0]["level"] == "warning"
    phys = results[0]["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "ok.php"
    assert phys["region"]["startLine"] == 5
