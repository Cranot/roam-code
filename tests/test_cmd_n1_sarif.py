"""W1208: SARIF projection for ``roam n1`` implicit N+1 findings.

cmd_n1 detects implicit N+1 I/O patterns across ORM frameworks (Laravel
$appends accessors, Django @property + manager access, Rails association
calls, SQLAlchemy @hybrid_property, JPA @Transient). Each finding maps
onto one of three closed-enum rule ids by confidence label:

- ``n1/high-confidence`` (defaultLevel ``error``): the model is used in
  a collection / pagination context — the accessor's I/O will fire
  per-item on serialization. Confidence label ``high``.
- ``n1/medium-confidence`` (defaultLevel ``warning``): relationship
  lazy-load I/O type, no strong collection-context evidence.
  Confidence label ``medium``.
- ``n1/low-confidence`` (defaultLevel ``note``): heuristic match
  without supporting collection-context evidence. Confidence label
  ``low``.

Per-finding anchor: ``accessor_location`` (parsed as ``path:line``) —
the line that fires the per-item query. Mirrors the closed-enum design
from ``test_cmd_auth_gaps_sarif.py`` (W1195) and
``test_cmd_smells_sarif.py`` (W1171).
"""

from __future__ import annotations

from roam.output.sarif import n1_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_auth_gaps / cmd_smells
    "no findings" path.
    """
    doc = n1_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "n1/high-confidence",
        "n1/medium-confidence",
        "n1/low-confidence",
    }
    # Each rule carries its closed-enum defaultLevel — surfaced via the
    # SARIF builder onto ``defaultConfiguration.level``.
    by_id = {r["id"]: r for r in rules}
    assert by_id["n1/high-confidence"]["defaultConfiguration"]["level"] == "error"
    assert by_id["n1/medium-confidence"]["defaultConfiguration"]["level"] == "warning"
    assert by_id["n1/low-confidence"]["defaultConfiguration"]["level"] == "note"


def test_high_confidence_finding_maps_to_error_band() -> None:
    """A high-confidence finding projects onto ``n1/high-confidence``
    at ``level: error``.

    The model is used in a collection / pagination context — the
    accessor's I/O will fire per-item on serialization. SARIF level is
    ``error`` so a CI gate keyed off ``level: error`` blocks the change.
    """
    findings = [
        {
            "model_name": "App\\Models\\User",
            "model_location": "app/Models/User.php:12",
            "accessor_name": "getProfileAttribute",
            "accessor_location": "app/Models/User.php:42",
            "appended_attribute": "profile",
            "relationship": "profile",
            "io_type": "belongsTo",
            "eager_loaded": False,
            "confidence": "high",
            "severity": "per-item query on serialization",
            "collection_contexts": [
                {"type": "controller", "location": "app/Http/Controllers/UserController.php:55"},
            ],
            "suggestion": "Add ::with('profile') in the controller query",
        }
    ]

    doc = n1_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "n1/high-confidence"
    assert r["level"] == "error"

    # Anchor is accessor_location parsed as path:line.
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "app/Models/User.php"
    assert phys["region"]["startLine"] == 42

    # Message body surfaces model.accessor + appended + relationship +
    # I/O type + fix suggestion so SARIF consumers can triage without a
    # JSON-envelope round-trip.
    text = r["message"]["text"]
    assert "App\\Models\\User.getProfileAttribute" in text
    assert "$profile" in text  # appended attribute
    assert "profile" in text  # relationship
    assert "belongsTo" in text  # io_type
    assert "with('profile')" in text  # suggestion fragment


def test_medium_and_low_confidence_findings_scale_severity_bands() -> None:
    """Medium-confidence -> ``warning``, low-confidence -> ``note``.

    Verifies the closed-enum band mapping in
    :func:`_n1_confidence_level`: each confidence label projects onto a
    distinct rule id AND distinct level, so a CI gate keyed off SARIF
    ``level: error`` blocks ONLY on high-confidence findings (where the
    detector has collection-context evidence).
    """
    findings = [
        {
            "model_name": "Post",
            "model_location": "models.py:8",
            "accessor_name": "comment_count",
            "accessor_location": "models.py:25",
            "appended_attribute": "comment_count",
            "relationship": "comments",
            "io_type": "all",
            "eager_loaded": False,
            "confidence": "medium",
            "severity": "per-item query on serialization",
            "collection_contexts": [],
            "suggestion": "Add .prefetch_related('comments') to the QuerySet",
        },
        {
            "model_name": "Tag",
            "model_location": "models.py:50",
            "accessor_name": "slug",
            "accessor_location": "models.py:60",
            "appended_attribute": "slug",
            "relationship": "name",
            "io_type": "get",
            "eager_loaded": False,
            "confidence": "low",
            "severity": "per-item query on serialization",
            "collection_contexts": [],
            "suggestion": "Pre-load 'name' data before iterating",
        },
    ]

    doc = n1_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 2

    by_rule = {r["ruleId"]: r for r in results}
    assert "n1/medium-confidence" in by_rule
    assert "n1/low-confidence" in by_rule

    assert by_rule["n1/medium-confidence"]["level"] == "warning"
    assert by_rule["n1/low-confidence"]["level"] == "note"

    # No error band is emitted (no high-confidence findings).
    levels = {r["level"] for r in results}
    assert "error" not in levels


def test_malformed_entries_are_skipped_without_crash() -> None:
    """Non-dict entries / empty accessor_location / unknown confidence
    are skipped silently (no crash).

    Defensive parsing per Pattern 1 family discipline — the SARIF emitter
    must not crash on a malformed entry, since the producer envelope
    can carry partial data when the underlying analyzer hits an
    exception.
    """
    findings = [
        "not a dict",  # skipped
        {
            "model_name": "X",
            "accessor_name": "y",
            "accessor_location": "",  # empty anchor — skipped
            "confidence": "high",
        },
        {
            "model_name": "Y",
            "accessor_name": "z",
            "accessor_location": "foo.py:10",
            "confidence": "bogus",  # unknown label — skipped (closed enum)
        },
        {
            "model_name": "OK",
            "accessor_name": "ok",
            "accessor_location": "ok.py:5",
            "appended_attribute": "ok",
            "relationship": "ok",
            "io_type": "find",
            "confidence": "medium",
            "suggestion": "ok fix",
        },  # kept
    ]

    doc = n1_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Only the well-formed medium-confidence entry survived.
    assert len(results) == 1
    assert results[0]["ruleId"] == "n1/medium-confidence"
    assert results[0]["level"] == "warning"
    phys = results[0]["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "ok.py"
    assert phys["region"]["startLine"] == 5
