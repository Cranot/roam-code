"""W1219: SARIF projection for ``roam over-fetch`` over-fetch findings.

cmd_over_fetch detects Laravel/Eloquent over-fetch patterns: large models
without $hidden / $visible filtering, controllers that return models
directly without API Resources, and queries missing ->select() to limit
columns. It also classifies controller methods into 3 endpoint states
(BARE / UNGUARDED_RELATION / GUARDED_RELATION).

SARIF projection emits a single closed-enum rule
``over-fetch/select-star-or-wide-query`` (defaultLevel ``warning``) —
over-fetch is one failure mode (returning more columns than necessary),
surfaced through several heuristics. Per-finding level mapping:

- H severity / high confidence -> ``warning`` (confirmed leak)
- L severity / medium / low confidence -> ``note`` (advisory)

Per-finding anchor:

- Endpoint findings: ``file:line`` of the controller method (the
  query/load site itself).
- Model findings: ``model_location`` (class declaration line) — the
  canonical edit site when the fix is to add ``$hidden`` / ``$visible``
  / an API Resource scaffold.

Mirrors the closed-enum design from ``test_cmd_auth_gaps_sarif.py``
(W1195) and ``test_cmd_n1_sarif.py`` (W1208), but with a single rule
because over-fetch is one failure mode.
"""

from __future__ import annotations

from roam.output.sarif import over_fetch_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_auth_gaps / cmd_n1 /
    cmd_smells "no findings" path.
    """
    doc = over_fetch_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum: 1 rule).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {"over-fetch/select-star-or-wide-query"}
    # The single rule carries its closed-enum defaultLevel — surfaced
    # via the SARIF builder onto ``defaultConfiguration.level``.
    rule = rules[0]
    assert rule["defaultConfiguration"]["level"] == "warning"


def test_endpoint_and_model_findings_map_to_distinct_levels() -> None:
    """An endpoint BARE finding -> warning; a model low-confidence -> note.

    Endpoint findings carry ``state`` + ``severity`` (H/L); model
    findings carry ``confidence`` (high/medium/low). Both project onto
    the same closed-enum rule, but per-result ``level`` reflects the
    severity ladder:

    - H severity / high confidence -> ``warning``
    - L severity / medium / low confidence -> ``note``

    Anchor verification:
    - Endpoint -> ``file:line`` of the method.
    - Model -> ``model_location`` parsed as ``path:line``.
    """
    findings = [
        # Endpoint-level finding: BARE (severity H -> warning).
        {
            "endpoint": "UserController@index",
            "controller": "UserController",
            "method": "index",
            "file": "app/Http/Controllers/UserController.php",
            "line": 42,
            "state": "BARE",
            "severity": "H",
            "evidence": "paginate()/get()/all() without ->select() or Resource",
            "recommendation": "Add ->select(['col1','col2']) or wrap in a Resource.",
            "details": {"guarded": [], "unguarded": [], "has_select": False, "bare_main_model": True},
        },
        # Model-level finding: low confidence (-> note).
        {
            "model_name": "Account",
            "model_path": "app/Models/Account.php",
            "model_location": "app/Models/Account.php:15",
            "fillable_count": 18,
            "hidden_count": 0,
            "exposed_count": 18,
            "has_visible": False,
            "has_resource": False,
            "resource_path": None,
            "confidence": "low",
            "reasons": ["18 fillable fields without select() optimization"],
            "matched_patterns": ["exposed_fields=18"],
            "suggestions": ["Use ->select(...)"],
            "direct_returns": [],
            "missing_selects": [],
        },
    ]

    doc = over_fetch_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 2

    # Every result projects onto the single closed-enum rule.
    rule_ids = {r["ruleId"] for r in results}
    assert rule_ids == {"over-fetch/select-star-or-wide-query"}

    # Endpoint result -> level=warning, anchored at method file:line.
    endpoint_results = [r for r in results if "Over-fetch endpoint" in r["message"]["text"]]
    assert len(endpoint_results) == 1
    ep = endpoint_results[0]
    assert ep["level"] == "warning"
    phys = ep["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "app/Http/Controllers/UserController.php"
    assert phys["region"]["startLine"] == 42
    # Message body surfaces the endpoint, state, evidence, and fix.
    text = ep["message"]["text"]
    assert "UserController@index" in text
    assert "state=BARE" in text
    assert "Fix:" in text

    # Model result -> level=note, anchored at model_location.
    model_results = [r for r in results if "Over-fetch model" in r["message"]["text"]]
    assert len(model_results) == 1
    mr = model_results[0]
    assert mr["level"] == "note"
    phys = mr["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "app/Models/Account.php"
    assert phys["region"]["startLine"] == 15
    # Message body surfaces the model, confidence, and counts.
    text = mr["message"]["text"]
    assert "Account" in text
    assert "confidence=low" in text
    assert "18 fillable" in text


def test_malformed_entries_are_skipped_without_crash() -> None:
    """Non-dict entries / missing anchors / unknown confidence are
    skipped silently (no crash).

    Defensive parsing per Pattern 1 family discipline — the SARIF
    emitter must not crash on a malformed entry, since the producer
    envelope can carry partial data when the underlying analyzer hits
    an exception. Also exercises the high-confidence model anchor
    (model -> warning level) to confirm the H mapping in the same
    test (kept the function-count at 3 per the W1219 plan).
    """
    findings = [
        "not a dict",  # skipped
        # Endpoint missing file -> skipped (no anchor).
        {
            "endpoint": "X@y",
            "state": "BARE",
            "severity": "H",
            "file": "",
            "line": 10,
        },
        # Model with unknown confidence -> skipped (closed enum).
        {
            "model_name": "X",
            "model_path": "x.php",
            "model_location": "x.php:1",
            "confidence": "bogus",
            "fillable_count": 30,
            "hidden_count": 0,
            "exposed_count": 30,
            "reasons": [],
        },
        # Model with no path AND no model_location -> skipped.
        {
            "model_name": "Y",
            "model_path": "",
            "model_location": "",
            "confidence": "high",
            "fillable_count": 50,
            "hidden_count": 0,
            "exposed_count": 50,
            "reasons": ["Serializes 50 fields per item"],
        },
        # Well-formed high-confidence model finding -> kept.
        # Tests the H mapping (high confidence -> warning).
        {
            "model_name": "BigModel",
            "model_path": "app/Models/BigModel.php",
            "model_location": "app/Models/BigModel.php:8",
            "confidence": "high",
            "fillable_count": 50,
            "hidden_count": 0,
            "exposed_count": 50,
            "reasons": ["Serializes 50 fields per item in list APIs"],
            "has_resource": False,
        },
        # Well-formed UNGUARDED_RELATION endpoint -> kept (severity H -> warning).
        {
            "endpoint": "PostController@list",
            "controller": "PostController",
            "method": "list",
            "file": "app/Http/Controllers/PostController.php",
            "line": 20,
            "state": "UNGUARDED_RELATION",
            "severity": "H",
            "evidence": "with('comments')",
            "recommendation": "Add column selection.",
        },
        # Well-formed GUARDED_RELATION endpoint -> kept (severity L -> note).
        {
            "endpoint": "TagController@show",
            "controller": "TagController",
            "method": "show",
            "file": "app/Http/Controllers/TagController.php",
            "line": 30,
            "state": "GUARDED_RELATION",
            "severity": "L",
            "evidence": "with('user:id,name')",
            "recommendation": "Already partially guarded.",
        },
    ]

    doc = over_fetch_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Three well-formed entries survived (high-confidence model,
    # UNGUARDED_RELATION endpoint, GUARDED_RELATION endpoint).
    assert len(results) == 3
    # Every survivor projects onto the single closed-enum rule.
    assert {r["ruleId"] for r in results} == {"over-fetch/select-star-or-wide-query"}

    # Level mapping: H severity / high confidence -> warning; L -> note.
    levels = sorted(r["level"] for r in results)
    assert levels == ["note", "warning", "warning"]

    # The GUARDED_RELATION entry maps to ``note``.
    guarded = [r for r in results if "state=GUARDED_RELATION" in r["message"]["text"]]
    assert len(guarded) == 1
    assert guarded[0]["level"] == "note"

    # The high-confidence model maps to ``warning``.
    big_model = [r for r in results if "BigModel" in r["message"]["text"]]
    assert len(big_model) == 1
    assert big_model[0]["level"] == "warning"
    phys = big_model[0]["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "app/Models/BigModel.php"
    assert phys["region"]["startLine"] == 8
