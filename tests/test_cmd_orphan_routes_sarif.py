"""W1227: SARIF projection for ``roam orphan-routes`` dead-endpoint findings.

The killer signal for orphan-routes is *which Laravel API routes have no
frontend consumer* — dead endpoints are real bugs (operational cost +
attack surface), not just hygiene. cmd_orphan_routes parses
``routes/api.php`` / ``routes/web.php``, extracts route definitions, and
greps the codebase for references to each route's path segments. Each
finding carries a ``confidence`` band (closed enum: ``high`` / ``medium``
/ ``low`` — the ``used`` bucket is filtered upstream so SARIF consumers
never see non-actionable rows).

The SARIF projection maps the three confidence bands onto a single
closed-enum rule (``orphan-route``) with per-result level banded by
confidence:

- high + medium -> ``warning`` (no frontend consumer detected — strong
  dead-endpoint signal; the medium band still has backend test /
  seeder references but those don't count as consumers from the API
  surface perspective)
- low -> ``note`` (referenced only in docs / comments — advisory band,
  the doc may still be load-bearing for downstream consumers)

Orphan-routes deliberately does NOT escalate to ``error``: the detector
is heuristic (path-segment grep + Laravel-route regex parse, no full
PHP AST analysis) so even the strongest signal stays in the warning
band — mirrors the W1226 ``flag-dead`` and W1213 ``duplicates``
severity ceilings.

Mirrors the closed-enum test design from ``test_cmd_flag_dead_sarif.py``
(W1226) and ``test_cmd_dark_matter_sarif.py`` (W1211), adapted for the
per-route anchor (file:line of the route definition).
"""

from __future__ import annotations

from roam.output.sarif import orphan_routes_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_flag_dead / cmd_hotspots
    "no findings" path. Orphan-routes specifically hits this branch on
    any repo without Laravel route files OR with every route having a
    frontend consumer (the ``used`` bucket is filtered upstream).
    """
    doc = orphan_routes_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum: 1 rule).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {"orphan-route"}
    # Closed-enum default-level verification — the SARIF severity
    # contract is encoded in the rule descriptor itself so consumers
    # can introspect it without firing a finding. The SARIF schema
    # nests the default under defaultConfiguration.level (the
    # ``_build_rule`` helper normalises the emitter-side
    # ``defaultLevel`` shortcut into the schema-conformant nested key).
    by_id = {r["id"]: r for r in rules}
    assert by_id["orphan-route"]["defaultConfiguration"]["level"] == "warning"


def test_confidence_bands_map_to_warning_and_note() -> None:
    """Each actionable confidence band projects onto its distinct SARIF level.

    high -> ``warning`` (no references anywhere outside route file —
        strongest dead-endpoint signal).
    medium -> ``warning`` (backend tests / seeders only — still no
        frontend consumer).
    low -> ``note`` (docs / comments only — advisory band).

    Also exercises the per-route anchor (file:line of the route
    definition) and the message body shape: confidence band, method +
    path (LAW 4 concrete-noun anchor on the route identifier), and
    controller::action when present so consumers can triage without a
    JSON-envelope round-trip.
    """
    findings = [
        {
            "confidence": "high",
            "method": "DELETE",
            "path": "/api/legacy/wipe",
            "controller": "LegacyController",
            "action": "wipe",
            "file": "routes/api.php",
            "line": 42,
        },
        {
            "confidence": "medium",
            "method": "POST",
            "path": "/api/internal/sync",
            "controller": "SyncController",
            "action": "trigger",
            "file": "routes/api.php",
            "line": 87,
        },
        {
            "confidence": "low",
            "method": "GET",
            "path": "/api/docs/example",
            "controller": "ExampleController",
            "action": "show",
            "file": "routes/api.php",
            "line": 113,
        },
    ]

    doc = orphan_routes_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 3

    # All three results share the single closed-enum rule id.
    assert {r["ruleId"] for r in results} == {"orphan-route"}

    by_path = {
        r["message"]["text"].split()[2]: r for r in results
    }  # split on space, third token is method-then-path; key by path

    # --- high -> orphan-route / warning ---------------------------------
    high_result = next(r for r in results if "/api/legacy/wipe" in r["message"]["text"])
    assert high_result["level"] == "warning"
    primary = high_result["locations"][0]["physicalLocation"]
    assert primary["artifactLocation"]["uri"] == "routes/api.php"
    assert primary["region"]["startLine"] == 42
    msg_high = high_result["message"]["text"]
    assert "high" in msg_high
    assert "DELETE" in msg_high
    assert "/api/legacy/wipe" in msg_high
    # Controller::action surfaced.
    assert "LegacyController::wipe" in msg_high

    # --- medium -> orphan-route / warning -------------------------------
    medium_result = next(r for r in results if "/api/internal/sync" in r["message"]["text"])
    assert medium_result["level"] == "warning"
    med_loc = medium_result["locations"][0]["physicalLocation"]
    assert med_loc["region"]["startLine"] == 87
    msg_med = medium_result["message"]["text"]
    assert "medium" in msg_med
    assert "POST" in msg_med
    assert "SyncController::trigger" in msg_med

    # --- low -> orphan-route / note -------------------------------------
    low_result = next(r for r in results if "/api/docs/example" in r["message"]["text"])
    assert low_result["level"] == "note"
    low_loc = low_result["locations"][0]["physicalLocation"]
    assert low_loc["region"]["startLine"] == 113
    msg_low = low_result["message"]["text"]
    assert "low" in msg_low
    assert "GET" in msg_low
    assert "ExampleController::show" in msg_low

    # Silence "unused variable" warning while still exercising the
    # path-based lookup pattern (documents the per-route anchor design).
    _ = by_path


def test_unresolved_and_anchorless_findings_are_skipped() -> None:
    """Findings without method/path, without a file anchor, with a
    ``used`` / unknown classification, or missing both controller +
    action are handled per the disclosure discipline.

    Specifically:
      - missing ``method`` or ``path`` (no subject) -> skip
      - missing ``file`` (no anchor) -> skip
      - ``used`` (has a frontend consumer — not an orphan) -> skip
      - unknown confidence outside the closed enum -> skip
      - missing ``controller`` + ``action`` (closure-based route) ->
        keep, but the message body omits the controller suffix
        (a well-formed orphan can exist without a controller — Laravel
        supports closure-based routes)

    Mirrors the producer-side disclosure discipline (Pattern 1 / LAW 6):
    a SARIF result without a stable subject cannot be surfaced
    meaningfully. The ``used`` bucket is also dropped — it has a
    frontend consumer (not an orphan), so SARIF consumers never see
    those rows. Unknown classifications outside the closed enumeration
    drop per LAW 8 / CLAUDE.md Constraint 8.
    """
    findings = [
        # Skipped: missing path (no subject).
        {
            "confidence": "high",
            "method": "GET",
            "path": "",
            "controller": "X",
            "action": "y",
            "file": "routes/api.php",
            "line": 10,
        },
        # Skipped: missing method (no subject).
        {
            "confidence": "high",
            "method": "",
            "path": "/api/foo",
            "controller": "X",
            "action": "y",
            "file": "routes/api.php",
            "line": 11,
        },
        # Skipped: ``used`` classification (filtered upstream of
        # per-result loop — has a frontend consumer).
        {
            "confidence": "used",
            "method": "GET",
            "path": "/api/active",
            "controller": "ActiveController",
            "action": "index",
            "file": "routes/api.php",
            "line": 5,
        },
        # Skipped: unknown classification (closed-enum discipline).
        {
            "confidence": "MYSTERY",
            "method": "GET",
            "path": "/api/weird",
            "controller": "Weird",
            "action": "show",
            "file": "routes/api.php",
            "line": 1,
        },
        # Skipped: no file anchor.
        {
            "confidence": "high",
            "method": "POST",
            "path": "/api/anchorless",
            "controller": "Anchor",
            "action": "store",
            "file": "",
            "line": 0,
        },
        # Skipped: not a dict (defensive — pathological producer input).
        "not-a-dict",
        # Kept: closure-based route (no controller / action).
        # The orphan-route rule applies; the message body just omits
        # the controller suffix.
        {
            "confidence": "high",
            "method": "GET",
            "path": "/api/closure-orphan",
            "controller": None,
            "action": None,
            "file": "routes/api.php",
            "line": 50,
        },
    ]

    doc = orphan_routes_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Only the well-formed closure-route entry survives the filter.
    assert len(results) == 1
    surviving = results[0]
    assert surviving["ruleId"] == "orphan-route"
    assert surviving["level"] == "warning"
    assert surviving["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "routes/api.php"
    assert surviving["locations"][0]["physicalLocation"]["region"]["startLine"] == 50
    msg = surviving["message"]["text"]
    assert "high" in msg
    assert "GET" in msg
    assert "/api/closure-orphan" in msg
    # Controller suffix is omitted for closure-based routes — no "::" delimiter.
    assert "::" not in msg
