"""W1210: SARIF projection for ``roam hotspots`` runtime-vs-static findings.

The killer signal for hotspots is *which symbols are genuinely hot in
production* vs *which symbols static analysis incorrectly flagged as
important*. cmd_hotspots compares the static ranking (PageRank +
complexity + churn) against the runtime ranking (call_count + p99
latency + error rate) from ingested traces and tags each symbol with
one of three classifications. The SARIF projection mirrors that closed
enumeration onto three rule ids with distinct ``defaultLevel``:

- ``hotspots/confirmed`` (defaultLevel ``error``): static + runtime
  agree on importance — confirmed via real trace data.
- ``hotspots/upgrade`` (defaultLevel ``warning``): runtime-critical
  but statically safe — hidden hotspot static analysis missed.
- ``hotspots/downgrade`` (defaultLevel ``note``): statically risky but
  low traffic — informational by design.

All three carry confidence tier ``runtime`` in the findings registry —
every emitted finding required ingested ``runtime_stats`` rows. The
SARIF level split lets a CI gate keyed off ``level: error`` block only
on the band with the strongest operator signal (CONFIRMED) without
surfacing the long advisory tail.

Mirrors the closed-enum test design from ``test_cmd_bus_factor_sarif.py``
(W1215) and ``test_cmd_auth_gaps_sarif.py`` (W1195), adapted for the
file-level anchor (compute_hotspots returns symbol_name + file_path but
no specific line, so the symbol is located at file granularity).
"""

from __future__ import annotations

from roam.output.sarif import hotspots_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_bus_factor / cmd_over_fetch
    "no findings" path. Hotspots specifically hits this branch on any
    repo without ingested traces — the default for a freshly-indexed
    codebase.
    """
    doc = hotspots_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum: 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "hotspots/confirmed",
        "hotspots/upgrade",
        "hotspots/downgrade",
    }
    # Closed-enum default-level verification — the CI gate band split
    # is encoded in the rule descriptors themselves so consumers can
    # introspect the severity contract without firing a finding. The
    # SARIF schema nests the default under defaultConfiguration.level
    # (the ``_build_rule`` helper normalises the emitter-side
    # ``defaultLevel`` shortcut into the schema-conformant nested key).
    by_id = {r["id"]: r for r in rules}
    assert by_id["hotspots/confirmed"]["defaultConfiguration"]["level"] == "error"
    assert by_id["hotspots/upgrade"]["defaultConfiguration"]["level"] == "warning"
    assert by_id["hotspots/downgrade"]["defaultConfiguration"]["level"] == "note"


def test_classification_bands_map_to_error_warning_and_note() -> None:
    """Each classification projects onto its distinct SARIF level.

    CONFIRMED -> ``error`` (static + runtime agree — currently-hot
    symbol). UPGRADE -> ``warning`` (runtime-critical but statically
    safe — hidden hotspot). DOWNGRADE -> ``note`` (statically risky
    but low traffic — informational). Also exercises the file-level
    anchor: hotspots are symbol-granularity but compute_hotspots
    surfaces only file_path (no line), so the SARIF location has an
    ``artifactLocation.uri`` with no ``region`` key.
    """
    findings = [
        {
            "symbol_id": 101,
            "symbol_name": "handle_request",
            "file_path": "src/api/handlers.py",
            "classification": "CONFIRMED",
            "static_rank": 3,
            "runtime_rank": 2,
            "runtime_stats": {
                "call_count": 145000,
                "p50_latency_ms": 12.0,
                "p99_latency_ms": 85.0,
                "error_rate": 0.02,
            },
            "static_stats": {
                "pagerank": 0.041,
                "complexity": 18.0,
                "churn": 42,
            },
        },
        {
            "symbol_id": 202,
            "symbol_name": "process_batch",
            "file_path": "src/worker/batch.py",
            "classification": "UPGRADE",
            "static_rank": 89,
            "runtime_rank": 4,
            "runtime_stats": {
                "call_count": 95000,
                "p50_latency_ms": 8.0,
                "p99_latency_ms": 220.0,
                "error_rate": 0.0,
            },
            "static_stats": {
                "pagerank": 0.001,
                "complexity": 3.0,
                "churn": 1,
            },
        },
        {
            "symbol_id": 303,
            "symbol_name": "legacy_validate",
            "file_path": "src/legacy/validate.py",
            "classification": "DOWNGRADE",
            "static_rank": 2,
            "runtime_rank": 87,
            "runtime_stats": {
                "call_count": 12,
                "p50_latency_ms": 1.0,
                "p99_latency_ms": 5.0,
                "error_rate": 0.0,
            },
            "static_stats": {
                "pagerank": 0.038,
                "complexity": 22.0,
                "churn": 65,
            },
        },
    ]

    doc = hotspots_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 3

    by_rule = {r["ruleId"]: r for r in results}
    assert set(by_rule.keys()) == {
        "hotspots/confirmed",
        "hotspots/upgrade",
        "hotspots/downgrade",
    }

    confirmed = by_rule["hotspots/confirmed"]
    assert confirmed["level"] == "error"
    # File-level anchor — no region key when no line supplied.
    loc = confirmed["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/api/handlers.py"
    assert "region" not in loc
    # Message carries classification + symbol name + ranks + call count
    # + p99 latency + error rate so SARIF consumers can triage without
    # a JSON-envelope round-trip.
    msg = confirmed["message"]["text"]
    assert "CONFIRMED" in msg
    assert "handle_request" in msg
    assert "runtime_rank=2" in msg
    assert "static_rank=3" in msg
    assert "calls=145000" in msg
    assert "p99=85ms" in msg
    assert "err=2.0%" in msg

    upgrade = by_rule["hotspots/upgrade"]
    assert upgrade["level"] == "warning"
    assert upgrade["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/worker/batch.py"
    msg_up = upgrade["message"]["text"]
    assert "UPGRADE" in msg_up
    assert "process_batch" in msg_up
    # Zero error rate — err suffix elided.
    assert "err=" not in msg_up

    downgrade = by_rule["hotspots/downgrade"]
    assert downgrade["level"] == "note"
    assert downgrade["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/legacy/validate.py"
    msg_dn = downgrade["message"]["text"]
    assert "DOWNGRADE" in msg_dn
    assert "legacy_validate" in msg_dn


def test_unresolved_and_anchorless_findings_are_skipped() -> None:
    """Findings without an indexed symbol_id or file_path are skipped.

    Mirrors the producer-side discipline in ``_emit_hotspots_findings``
    (cmd_hotspots.py): trace spans that didn't resolve to a known
    symbol have no stable subject to attach the finding to, so the
    SARIF projection drops them rather than emit subject-less rows.
    Unknown classification labels also drop (closed-enum discipline
    per CLAUDE.md Constraint 8 — never synthesise a bucket the
    consumer can't reason about).
    """
    findings = [
        # Skipped: no symbol_id (trace span didn't resolve).
        {
            "symbol_id": None,
            "symbol_name": "<external_span>",
            "file_path": "src/foo.py",
            "classification": "CONFIRMED",
            "static_rank": 0,
            "runtime_rank": 1,
            "runtime_stats": {"call_count": 5000},
            "static_stats": {},
        },
        # Skipped: no file_path anchor.
        {
            "symbol_id": 7,
            "symbol_name": "anchorless_fn",
            "file_path": "",
            "classification": "UPGRADE",
            "static_rank": 50,
            "runtime_rank": 3,
            "runtime_stats": {"call_count": 80000},
            "static_stats": {},
        },
        # Skipped: unknown classification (closed-enum discipline).
        {
            "symbol_id": 8,
            "symbol_name": "weird_fn",
            "file_path": "src/weird.py",
            "classification": "MYSTERY",
            "static_rank": 1,
            "runtime_rank": 1,
            "runtime_stats": {"call_count": 100},
            "static_stats": {},
        },
        # Skipped: not a dict (defensive — pathological producer input).
        "not-a-dict",
        # Kept: well-formed CONFIRMED.
        {
            "symbol_id": 9,
            "symbol_name": "real_handler",
            "file_path": "src/real.py",
            "classification": "CONFIRMED",
            "static_rank": 1,
            "runtime_rank": 1,
            "runtime_stats": {
                "call_count": 50000,
                "p99_latency_ms": 12.0,
                "error_rate": 0.0,
            },
            "static_stats": {},
        },
    ]

    doc = hotspots_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Only the well-formed entry survives the filter.
    assert len(results) == 1
    surviving = results[0]
    assert surviving["ruleId"] == "hotspots/confirmed"
    assert surviving["level"] == "error"
    assert surviving["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/real.py"
    assert "real_handler" in surviving["message"]["text"]
