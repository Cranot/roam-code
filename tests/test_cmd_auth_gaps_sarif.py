"""W1195: SARIF projection for ``roam auth-gaps`` PHP / Laravel
endpoint authentication & authorization gaps.

The killer signal for auth-gaps is *which routes / controller methods
in the codebase are accessible without an authentication or
authorization check*. Three rule ids project onto SARIF — one per
confidence tier returned by
:func:`roam.commands.cmd_auth_gaps._auth_gap_confidence_tier` — each
with a distinct ``defaultLevel`` so a CI gate keyed off SARIF
``level: error`` only blocks on deterministic findings, not heuristic
name-matching:

- ``auth-gaps/direct-unauthenticated-handler`` (defaultLevel
  ``error``): a route sits outside every auth middleware group AND
  has no inline auth middleware. Confidence tier ``static_analysis``
  — deterministic brace-depth analysis of the routes file.
- ``auth-gaps/helper-indirection`` (defaultLevel ``warning``): a
  controller method without a literal ``$this->authorize`` call,
  where same-class / ancestor-class helper descent was attempted but
  did NOT clear the gap. Confidence tier ``structural`` — the
  detector ran a graph traversal.
- ``auth-gaps/name-based`` (defaultLevel ``note``): weaker signals
  — non-auth-guard routes (throttle / signed / verified) and read
  methods. Confidence tier ``heuristic``.

Per-finding anchor: ``file`` + ``line`` (route definition line or
controller method declaration line). Mirrors the test design from
``test_cmd_delete_check_sarif.py`` (W1192) and
``test_cmd_smells_sarif.py`` (W1171).
"""

from __future__ import annotations

from roam.output.sarif import auth_gaps_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_delete_check / cmd_clones /
    cmd_partition "no findings" path.
    """
    doc = auth_gaps_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "auth-gaps/direct-unauthenticated-handler",
        "auth-gaps/helper-indirection",
        "auth-gaps/name-based",
    }
    # Each rule carries its closed-enum defaultLevel.
    by_id = {r["id"]: r for r in rules}
    assert by_id["auth-gaps/direct-unauthenticated-handler"]["defaultConfiguration"]["level"] == "error"
    assert by_id["auth-gaps/helper-indirection"]["defaultConfiguration"]["level"] == "warning"
    assert by_id["auth-gaps/name-based"]["defaultConfiguration"]["level"] == "note"


def test_route_high_confidence_finding_maps_to_direct_unauthenticated_handler_error() -> None:
    """A high-confidence route finding projects onto
    ``auth-gaps/direct-unauthenticated-handler`` at ``level: error``.

    The route sits outside every auth middleware group and has no
    inline auth middleware — the cmd_auth_gaps detector reaches this
    finding via deterministic brace-depth analysis, so the SARIF
    confidence tier is ``static_analysis`` -> SARIF ``error``.
    """
    findings = [
        {
            "type": "route",
            "confidence": "high",
            "verb": "POST",
            "path": "/api/admin/users",
            "file": "routes/api.php",
            "line": 42,
            "fix": "Add ->middleware('auth:sanctum') or move inside auth group",
        }
    ]

    doc = auth_gaps_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "auth-gaps/direct-unauthenticated-handler"
    assert r["level"] == "error"

    # Anchor = the route definition file:line.
    locs = r["locations"]
    assert len(locs) == 1
    assert locs[0]["physicalLocation"]["artifactLocation"]["uri"] == "routes/api.php"
    assert locs[0]["physicalLocation"]["region"]["startLine"] == 42

    # Message carries verb + path + confidence + tier + fix hint.
    msg = r["message"]["text"]
    assert "POST" in msg
    assert "/api/admin/users" in msg
    assert "confidence=high" in msg
    assert "tier=static_analysis" in msg
    assert "middleware" in msg  # fix-hint suffix


def test_controller_high_confidence_finding_maps_to_helper_indirection_warning() -> None:
    """A high-confidence controller finding projects onto
    ``auth-gaps/helper-indirection`` at ``level: warning``.

    The detector ran same-class + ancestor-helper descent and didn't
    find an authorize call — structural-confidence tier -> SARIF
    ``warning``. Mirrors the dogfood #6 helper-indirection pattern.
    """
    findings = [
        {
            "type": "controller",
            "confidence": "high",
            "controller": "AdminController",
            "method": "destroy",
            "file": "app/Http/Controllers/AdminController.php",
            "line": 78,
            "reason": "CRUD method with no auth middleware and no authorization call",
            "fix": "Add $this->authorize() or Gate::allows() inside the method",
            "matched_patterns": [
                "CRUD action",
                "no $this->authorize() / Gate / Policy call in method body",
            ],
        }
    ]

    doc = auth_gaps_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "auth-gaps/helper-indirection"
    assert r["level"] == "warning"

    # Anchor = the controller method declaration line.
    locs = r["locations"]
    assert len(locs) == 1
    assert locs[0]["physicalLocation"]["artifactLocation"]["uri"] == "app/Http/Controllers/AdminController.php"
    assert locs[0]["physicalLocation"]["region"]["startLine"] == 78

    msg = r["message"]["text"]
    assert "AdminController::destroy" in msg
    assert "confidence=high" in msg
    assert "tier=structural" in msg
    assert "CRUD method" in msg  # reason suffix


def test_low_confidence_findings_map_to_name_based_note_level() -> None:
    """Low-confidence findings — both route and controller — project
    onto ``auth-gaps/name-based`` at ``level: note``.

    Route low-confidence variant: ``non_auth_guard_present=True``
    (throttle / signed / verified guard, no auth). Controller
    low-confidence variant: read method without authorization. Both
    are heuristic name-matches -> SARIF ``note``.
    """
    findings = [
        {
            "type": "route",
            "confidence": "low",
            "verb": "GET",
            "path": "/api/public/feed",
            "file": "routes/api.php",
            "line": 12,
            "fix": "Verify intent: this route has non-auth guards but no auth:*",
            "non_auth_guard_present": True,
        },
        {
            "type": "controller",
            "confidence": "low",
            "controller": "PostController",
            "method": "show",
            "file": "app/Http/Controllers/PostController.php",
            "line": 50,
            "reason": "Read method without authorization (may be intentionally public)",
            "fix": "Add $this->authorize('view', $model) if access should be restricted",
            "matched_patterns": ["read action"],
        },
    ]

    doc = auth_gaps_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 2

    # Both findings collapse to name-based / note.
    for r in results:
        assert r["ruleId"] == "auth-gaps/name-based"
        assert r["level"] == "note"

    by_msg = sorted(r["message"]["text"] for r in results)
    assert any("/api/public/feed" in m for m in by_msg)
    assert any("PostController::show" in m for m in by_msg)
    # tier=heuristic appears on both.
    assert all("tier=heuristic" in m for m in by_msg)


def test_medium_confidence_controller_finding_maps_to_helper_indirection() -> None:
    """A medium-confidence controller finding (route or constructor
    auth exists, but no object-level authorize call) projects onto
    ``auth-gaps/helper-indirection`` at ``level: warning``.

    Mirrors the structural-tier mapping in
    :func:`roam.commands.cmd_auth_gaps._auth_gap_finding_kind` for the
    medium-confidence controller branch.
    """
    findings = [
        {
            "type": "controller",
            "confidence": "medium",
            "controller": "OrderController",
            "method": "update",
            "file": "app/Http/Controllers/OrderController.php",
            "line": 33,
            "reason": "CRUD method without object-level authorization (route auth exists)",
            "fix": "Add $this->authorize('action', $model) for object-level authorization",
            "matched_patterns": [
                "CRUD action",
                "auth middleware at route level",
                "no $this->authorize() / Gate / Policy call in method body",
            ],
        }
    ]

    doc = auth_gaps_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "auth-gaps/helper-indirection"
    assert r["level"] == "warning"
    assert "OrderController::update" in r["message"]["text"]
    assert "tier=structural" in r["message"]["text"]


def test_unknown_type_and_missing_file_anchor_are_skipped() -> None:
    """Defensive: rows without a recognised ``type`` OR without a
    ``file`` anchor are dropped.

    Closed enumeration over free string composition (LAW 8): a future
    finding-type label that hasn't landed in the closed branch above
    is skipped rather than minting a rule on the fly. Anchorless rows
    are skipped to match the ``delete_check_to_sarif`` /
    ``clones_to_sarif`` discipline.
    """
    findings = [
        {
            # Unknown type — skipped.
            "type": "websocket",
            "confidence": "high",
            "file": "routes/ws.php",
            "line": 1,
        },
        {
            # Missing file anchor — skipped.
            "type": "route",
            "confidence": "high",
            "verb": "GET",
            "path": "/anonymous",
            "file": "",
            "line": 5,
        },
        {
            # Valid row — survives.
            "type": "route",
            "confidence": "high",
            "verb": "DELETE",
            "path": "/api/users/{id}",
            "file": "routes/api.php",
            "line": 99,
            "fix": "Add ->middleware('auth:sanctum')",
        },
    ]

    doc = auth_gaps_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "auth-gaps/direct-unauthenticated-handler"
    assert "DELETE" in r["message"]["text"]
    assert "/api/users/{id}" in r["message"]["text"]
