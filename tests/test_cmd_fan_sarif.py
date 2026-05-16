"""W1209: SARIF projection for ``roam fan`` cross-file fan-in/out findings.

cmd_fan emits per-symbol or per-file architectural findings via two
detector surfaces (``fan-symbol`` / ``fan-file``) that both feed the
same SARIF projection. Three closed-enum architectural flags
(``HIGH-RISK`` / ``hub`` / ``spreader``) project to three closed-enum
rule ids with distinct defaultLevels reflecting the underlying
blast-radius risk:

- ``fan/hub`` -> defaultLevel ``note`` (high cross-file fan-in only —
  absorbs change pressure but does not propagate outward).
- ``fan/spreader`` -> defaultLevel ``warning`` (high cross-file
  fan-out only — changes here propagate outward).
- ``fan/high-risk`` -> defaultLevel ``error`` (both directions over
  threshold — amplifies blast radius in both directions, highest
  architectural-risk band).

Per-result level matches the rule defaultLevel (the flag IS the
severity band — a "hub" finding cannot escalate to "error" without
becoming a HIGH-RISK row by definition). Mirrors the closed-enum
design from ``test_cmd_laws_sarif.py`` (W1216) and
``test_cmd_bus_factor_sarif.py`` (W1215).

Per-finding anchor: ``file_path`` + (optional) ``line_start`` for
symbol-mode findings; ``file_path`` only for file-mode findings (no
line — the metric applies to the whole file). Local-only flags
(``local-hub`` / ``local-spreader``) and empty flags are skipped —
the W150 audit classifies them as non-architectural.
"""

from __future__ import annotations

from roam.output.sarif import fan_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Each rule's ``defaultLevel`` reflects the
    architectural-risk band: fan/hub -> note, fan/spreader ->
    warning, fan/high-risk -> error. Mirrors the cmd_laws /
    cmd_bus_factor "no findings" path.
    """
    doc = fan_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum: 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {"fan/hub", "fan/spreader", "fan/high-risk"}
    # defaultLevel reflects the architectural-risk band per rule —
    # hub (absorbs change pressure) -> note, spreader (propagates
    # outward) -> warning, high-risk (both directions) -> error.
    level_by_id = {r["id"]: r["defaultConfiguration"]["level"] for r in rules}
    assert level_by_id == {
        "fan/hub": "note",
        "fan/spreader": "warning",
        "fan/high-risk": "error",
    }


def test_symbol_and_file_findings_map_to_flag_rules_and_levels() -> None:
    """Per-finding flags map onto distinct rules; level matches rule default.

    Verifies the flag -> rule routing for symbol-mode (``hub`` /
    ``spreader`` / ``HIGH-RISK``) AND file-mode findings on the same
    SARIF projection. Anchor verification: symbol-mode carries a
    non-zero ``region.startLine``; file-mode drops the ``region`` key
    entirely (the metric applies to the whole file, not a specific
    line).
    """
    findings = [
        # Symbol-mode hub finding -> fan/hub + note.
        {
            "flag": "hub",
            "name": "handle_save",
            "kind": "function",
            "fan_in": 42,
            "fan_out": 3,
            "location": "src/app/handlers.py:120",
        },
        # Symbol-mode spreader finding -> fan/spreader + warning.
        {
            "flag": "spreader",
            "name": "render_view",
            "kind": "function",
            "fan_in": 4,
            "fan_out": 28,
            "location": "src/app/views.py:55",
        },
        # Symbol-mode HIGH-RISK finding -> fan/high-risk + error.
        {
            "flag": "HIGH-RISK",
            "name": "Dispatcher",
            "kind": "class",
            "fan_in": 30,
            "fan_out": 25,
            "location": "src/app/dispatch.py:10",
        },
        # File-mode hub finding -> fan/hub + note. file-mode uses
        # ``path`` (the cmd_fan file-items shape) and has no line.
        {
            "flag": "hub",
            "path": "src/app/utils.py",
            "fan_in": 14,
            "fan_out": 0,
            "total": 14,
        },
    ]

    doc = fan_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 4

    by_rule_and_name: dict[tuple[str, str], dict] = {}
    for r in results:
        # Index by (ruleId, anchor_path) to disambiguate the two
        # ``fan/hub`` rows (symbol-mode handle_save vs file-mode
        # utils.py).
        uri = r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        by_rule_and_name[(r["ruleId"], uri)] = r

    # hub (symbol) -> fan/hub + note, anchored at line 120.
    hub_sym = by_rule_and_name[("fan/hub", "src/app/handlers.py")]
    assert hub_sym["level"] == "note"
    assert hub_sym["locations"][0]["physicalLocation"]["region"]["startLine"] == 120
    text = hub_sym["message"]["text"]
    assert "[hub]" in text
    assert "handle_save" in text
    assert "fan_in=42" in text
    assert "fan_out=3" in text

    # spreader (symbol) -> fan/spreader + warning, anchored at line 55.
    spr = by_rule_and_name[("fan/spreader", "src/app/views.py")]
    assert spr["level"] == "warning"
    assert spr["locations"][0]["physicalLocation"]["region"]["startLine"] == 55
    assert "[spreader]" in spr["message"]["text"]

    # HIGH-RISK (symbol) -> fan/high-risk + error, anchored at line 10.
    hr = by_rule_and_name[("fan/high-risk", "src/app/dispatch.py")]
    assert hr["level"] == "error"
    assert hr["locations"][0]["physicalLocation"]["region"]["startLine"] == 10
    assert "[HIGH-RISK]" in hr["message"]["text"]

    # hub (file) -> fan/hub + note, NO region (file-level metric).
    hub_file = by_rule_and_name[("fan/hub", "src/app/utils.py")]
    assert hub_file["level"] == "note"
    phys = hub_file["locations"][0]["physicalLocation"]
    # File-mode rows have no line — region key omitted entirely so
    # SARIF consumers don't anchor to a synthetic ``startLine: 0``.
    assert "region" not in phys
    assert "[hub]" in hub_file["message"]["text"]
    assert "src/app/utils.py" in hub_file["message"]["text"]


def test_malformed_entries_and_local_flags_are_skipped() -> None:
    """Non-dict / anchorless / local-only / unknown-flag rows are skipped.

    Defensive parsing per Pattern 1 family discipline — the SARIF
    emitter must not crash on a malformed entry. Local-only flags
    (``local-hub`` / ``local-spreader``) are skipped per the W150
    audit (single-file by design — non-architectural). Anchor-less
    findings (no ``file_path`` / no parseable ``location``) cannot be
    surfaced meaningfully so we skip rather than emit an anchorless
    row (matches LAW 6 disclosure rules). Future flag values outside
    the closed enumeration are also skipped (LAW 8 closed-enum
    discipline).
    """
    findings = [
        "not a dict",  # skipped (defensive parse).
        # Empty flag -> skipped (non-architectural).
        {
            "flag": "",
            "name": "no_flag_symbol",
            "location": "src/x.py:1",
        },
        # local-hub -> skipped (W150: non-architectural single-file).
        {
            "flag": "local-hub",
            "name": "intra_file_helper",
            "location": "src/big_sfc.py:120",
        },
        # local-spreader -> skipped (W150: non-architectural).
        {
            "flag": "local-spreader",
            "name": "intra_file_dispatcher",
            "location": "src/big_sfc.py:200",
        },
        # Future / unknown flag -> skipped (LAW 8 closed-enum).
        {
            "flag": "MEGA-RISK",
            "name": "future_flag_symbol",
            "location": "src/y.py:5",
        },
        # Missing anchor -> skipped (no file_path, no location).
        {
            "flag": "hub",
            "name": "no_anchor",
            "fan_in": 20,
            "fan_out": 1,
        },
        # Well-formed hub finding -> survives, anchored at line 7.
        {
            "flag": "hub",
            "name": "real_hub",
            "fan_in": 42,
            "fan_out": 3,
            "location": "src/real.py:7",
        },
    ]

    doc = fan_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Only the well-formed hub finding survives.
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "fan/hub"
    assert r["level"] == "note"
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/real.py"
    assert phys["region"]["startLine"] == 7
    assert "real_hub" in r["message"]["text"]
