"""W1207: SARIF projection for ``roam llm-smells`` detector output.

The killer signal for llm-smells is *which LLM-API anti-pattern fires at
which file:line at which severity*. Ten closed-enum pattern kinds (W415
baseline + W415b cheap-pattern wave) project onto SARIF rule ids under
the ``llm-smells/`` namespace; per-finding severity drives the SARIF
level so a CI consumer (GitHub Code Scanning) can gate prompt-injection
findings (``llm-smells/direct-user-input-concatenation`` — OWASP
LLM01:2025) ahead of advisory smells (``llm-smells/temperature-not-set``).

Severity -> SARIF level (closed mapping via
:func:`roam.output._severity.to_sarif_level`):

    critical  -> "error"
    warning   -> "warning"
    info      -> "note"

Mirrors the test design from ``test_cmd_smells_sarif.py`` (W1171) and
``test_cmd_laws_sarif.py`` (W1216): every detector kind the command can
emit must round-trip through SARIF without dropping its severity /
message / file-line anchor.
"""

from __future__ import annotations

from roam.output.sarif import llm_smells_to_sarif


def test_empty_findings_produces_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    Mirrors the cmd_laws / cmd_smells "no findings" path: the rules
    array is always populated (10 closed-enum rules so consumers can
    introspect the kind vocabulary even when nothing fired), but
    ``results`` is empty. The rule catalogue mirrors
    :data:`roam.output.sarif._LLM_SMELLS_KIND_TO_RULE` exactly.
    """
    doc = llm_smells_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present — 10 closed-enum rules.
    rules = run["tool"]["driver"]["rules"]
    assert len(rules) == 10, f"rules catalogue must include all 10 W415 / W415b kinds; got {len(rules)}"
    # Every rule id is namespaced under llm-smells/...
    for r in rules:
        assert r["id"].startswith("llm-smells/"), r["id"]
        # SARIF 2.1.0 renders ``defaultLevel`` as
        # ``defaultConfiguration.level`` (see _build_rule).
        assert "level" in r["defaultConfiguration"]
    # Rules are sorted alphabetically (SARIF-stable output per W896).
    rule_ids = [r["id"] for r in rules]
    assert rule_ids == sorted(rule_ids), f"rules must be sorted alphabetically for stable SARIF output: {rule_ids}"
    # Spot-check the closed-enum vocabulary — every W415 / W415b kind
    # MUST be in the catalogue (10 rules total).
    rule_id_set = set(rule_ids)
    expected = {
        "llm-smells/no-model-version-pinning",
        "llm-smells/missing-max-tokens",
        "llm-smells/direct-user-input-concatenation",
        "llm-smells/no-structured-output-validation",
        "llm-smells/temperature-not-set",
        "llm-smells/missing-timeout",
        "llm-smells/missing-max-retries",
        "llm-smells/no-system-message",
        "llm-smells/no-retry-on-rate-limit",
        "llm-smells/call-in-loop",
    }
    assert rule_id_set == expected, (
        f"closed-enum rule catalogue mismatch: missing {expected - rule_id_set}, extra {rule_id_set - expected}"
    )
    # The critical-band kind (direct-user-input-concatenation) carries
    # defaultLevel=error per the W415 severity table; the rest are
    # warning or note.
    by_id = {r["id"]: r for r in rules}
    assert by_id["llm-smells/direct-user-input-concatenation"]["defaultConfiguration"]["level"] == "error"


def test_single_critical_finding_round_trips_with_error_level() -> None:
    """A critical-severity llm-smell (prompt-injection vector — OWASP
    LLM01:2025) maps to SARIF level=error.

    Closed mapping: critical -> "error", warning -> "warning",
    info -> "note". The finding's file:line anchor parses directly out
    of the envelope-shape ``file`` + ``line`` fields; the snippet
    appears in the message body so SARIF consumers can triage without
    parsing a JSON envelope.
    """
    findings = [
        {
            "kind": "llm_api_direct_user_input_concatenation",
            "file": "src/server/handler.py",
            "line": 142,
            "severity": "critical",
            "confidence": "heuristic",
            "snippet": ("def chat_endpoint(request):  # uses request.json"),
        }
    ]

    doc = llm_smells_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "llm-smells/direct-user-input-concatenation"
    assert r["level"] == "error"
    # Anchor: file + line round-trip cleanly.
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/server/handler.py"
    assert phys["region"]["startLine"] == 142
    # Message carries the rule short name + snippet for triage.
    text = r["message"]["text"]
    assert "direct-user-input-concatenation" in text


def test_multi_kind_findings_round_trip_with_distinct_levels() -> None:
    """A mixed bag of severities round-trips each onto its SARIF level.

    The same SARIF document carries findings spanning all three
    severity bands (critical/warning/info), each anchored on its own
    file:line, each on its own ``llm-smells/<kind>`` rule id. This is
    the realistic CI case — ``roam --sarif llm-smells`` on a 20k-symbol
    codebase typically emits dozens of findings across 5+ kinds.

    Per W896 (sorted output), the rule catalogue stays alphabetical
    regardless of finding-encounter order.
    """
    findings = [
        # critical -> error
        {
            "kind": "llm_api_direct_user_input_concatenation",
            "file": "src/api/chat.py",
            "line": 23,
            "severity": "critical",
            "confidence": "heuristic",
            "snippet": "f'You are an assistant. User says: {user_input}'",
        },
        # warning -> warning
        {
            "kind": "llm_api_missing_max_tokens",
            "file": "src/api/chat.py",
            "line": 45,
            "severity": "warning",
            "confidence": "heuristic",
            "snippet": "client.chat.completions.create(model='gpt-4o',",
        },
        # info -> note
        {
            "kind": "llm_api_temperature_not_set",
            "file": "src/api/chat.py",
            "line": 67,
            "severity": "info",
            "confidence": "heuristic",
            "snippet": "client.chat.completions.create(model='gpt-4o',",
        },
        # warning -> warning (different rule kind)
        {
            "kind": "llm_api_call_in_loop",
            "file": "src/batch/processor.py",
            "line": 88,
            "severity": "warning",
            "confidence": "heuristic",
            "snippet": "client.chat.completions.create(",
        },
    ]

    doc = llm_smells_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 4

    # Index by (rule_id, line) since two findings share rule kinds
    # would be possible in larger fixtures (one rule, many sites).
    levels_by_anchor = {
        (r["ruleId"], r["locations"][0]["physicalLocation"]["region"]["startLine"]): r["level"] for r in results
    }
    assert levels_by_anchor[("llm-smells/direct-user-input-concatenation", 23)] == "error"
    assert levels_by_anchor[("llm-smells/missing-max-tokens", 45)] == "warning"
    assert levels_by_anchor[("llm-smells/temperature-not-set", 67)] == "note"
    assert levels_by_anchor[("llm-smells/call-in-loop", 88)] == "warning"

    # File anchors round-trip cleanly across multiple files.
    files = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in results}
    assert files == {"src/api/chat.py", "src/batch/processor.py"}

    # Rule catalogue stays at 10 entries regardless of which kinds fired.
    rules = doc["runs"][0]["tool"]["driver"]["rules"]
    assert len(rules) == 10


def test_unknown_kind_is_skipped_not_minted_on_the_fly() -> None:
    """Unknown ``kind`` values are skipped — closed-enum discipline (LAW 8).

    A future LLM-API anti-pattern that hasn't landed in
    :data:`roam.output.sarif._LLM_SMELLS_KIND_TO_RULE` yet must not
    crash the SARIF projection NOR mint a fresh rule on the fly. The
    SARIF rule catalogue is closed-by-construction over the registry;
    extending the vocabulary is a deliberate edit. This guards against
    the W1159/W1160 mismatch pattern (string-composed rule ids).
    """
    findings = [
        {
            "kind": "llm_api_future_pattern_not_in_registry",  # unknown
            "file": "src/api/code.py",
            "line": 1,
            "severity": "warning",
            "confidence": "heuristic",
            "snippet": "fictional future pattern",
        },
        # Mixed-with-known: known finding still round-trips.
        {
            "kind": "llm_api_no_system_message",
            "file": "src/api/real.py",
            "line": 5,
            "severity": "warning",
            "confidence": "heuristic",
            "snippet": "client.chat.completions.create(messages=[",
        },
    ]

    doc = llm_smells_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # The unknown kind is dropped; the known one survives.
    assert len(results) == 1
    assert results[0]["ruleId"] == "llm-smells/no-system-message"
    # The rule catalogue never grew an entry for the unknown kind.
    rule_ids = {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]}
    assert "llm-smells/future-pattern-not-in-registry" not in rule_ids


def test_finding_without_file_anchor_is_skipped() -> None:
    """A finding with empty ``file`` is skipped (no anchor = unsurfaceable).

    Mirrors :func:`laws_to_sarif` and :func:`orphan_imports_to_sarif`:
    without a file anchor the finding cannot be surfaced meaningfully
    in any SARIF viewer (no file:line target to highlight), so it is
    skipped rather than emitted as an anchorless row. This contrasts
    with :func:`smells_to_sarif`, which emits anchorless rows because
    structural smells can be cross-cutting; llm-smells findings are
    always per-call-site / per-file by construction.
    """
    findings = [
        {
            "kind": "llm_api_missing_max_tokens",
            "file": "",  # no anchor — skip
            "line": 0,
            "severity": "warning",
            "confidence": "heuristic",
            "snippet": "",
        },
        # Sibling with anchor — survives.
        {
            "kind": "llm_api_missing_max_tokens",
            "file": "src/api/real.py",
            "line": 10,
            "severity": "warning",
            "confidence": "heuristic",
            "snippet": "client.chat.completions.create(model='gpt-4o',",
        },
    ]

    doc = llm_smells_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/api/real.py"
    assert results[0]["level"] == "warning"


def test_per_rule_default_level_reflects_severity_table() -> None:
    """The rule catalogue's per-rule ``defaultLevel`` mirrors the W415 /
    W415b severity table.

    The defaultLevel is what SARIF consumers see when a result row's
    ``level`` is omitted (it never is in our projection, but the rule
    catalogue is the canonical advertisement of "expected severity").
    Mapping (W415 / W415b severity -> SARIF level via _to_level):

        critical -> error  (only direct-user-input-concatenation —
                            OWASP LLM01:2025 prompt-injection vector)
        warning  -> warning (the common case — 7 of 10 kinds)
        info     -> note   (temperature-not-set, missing-max-retries)
    """
    doc = llm_smells_to_sarif([])
    rules = {r["id"]: r for r in doc["runs"][0]["tool"]["driver"]["rules"]}

    # Critical band.
    assert rules["llm-smells/direct-user-input-concatenation"]["defaultConfiguration"]["level"] == "error"
    # Info band.
    assert rules["llm-smells/temperature-not-set"]["defaultConfiguration"]["level"] == "note"
    assert rules["llm-smells/missing-max-retries"]["defaultConfiguration"]["level"] == "note"
    # Warning band — spot-check three.
    for rule_id in (
        "llm-smells/no-model-version-pinning",
        "llm-smells/missing-max-tokens",
        "llm-smells/no-retry-on-rate-limit",
    ):
        assert rules[rule_id]["defaultConfiguration"]["level"] == "warning", rule_id
