"""W1229: SARIF projection for ``roam verify-imports`` hallucination-firewall findings.

The killer signal for verify-imports is *which import statements name
symbols that don't exist anywhere in the indexed codebase* —
hallucinated imports are the canonical LLM-era failure mode (an AI-
generated file references a module / function it invented). The
:func:`roam.commands.cmd_verify_imports.verify_imports` producer walks
every source file, runs the appropriate per-language import regex, and
validates each name against the ``symbols`` / ``files`` tables. Each
row carries ``file`` / ``line`` / ``name`` / ``status`` (closed enum:
``resolved`` / ``unresolved``) and an optional ``suggestions`` list
(FTS5 fuzzy matches against the indexed symbol table).

The SARIF projection splits the unresolved population on the only
available producer-side signal — whether FTS5 surfaced any nearby
candidate — and projects onto two closed-enum rule ids:

- ``invalid-import`` (defaultLevel ``warning``): the imported name did
  not resolve, but FTS5 surfaced at least one fuzzy candidate. Likely
  a typo, rename, or stale import; the suggestions list gives a
  remediation path.
- ``hallucination-import`` (defaultLevel ``error``): the imported name
  did not resolve AND FTS5 found no nearby candidate. The symbol
  genuinely doesn't exist anywhere in the indexed graph — the
  canonical LLM-hallucination signal. This is the only verify-imports
  rule that escalates to ``error`` so a CI gate keyed off
  ``level: error`` blocks only on irrecoverable imports.

Mirrors the closed-enum test design from ``test_cmd_flag_dead_sarif.py``
(W1226) and ``test_cmd_orphan_routes_sarif.py`` (W1227), adapted for the
per-import anchor (file:line of the import statement) and the
two-rule warning / error split.
"""

from __future__ import annotations

from roam.output.sarif import verify_imports_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_flag_dead / cmd_orphan_routes
    "no findings" path. verify-imports specifically hits this branch
    on any repo where every import statement resolves cleanly (the
    ``resolved`` bucket is filtered upstream so SARIF consumers
    never see non-actionable rows).
    """
    doc = verify_imports_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum: 2 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {"invalid-import", "hallucination-import"}
    # Closed-enum default-level verification — the SARIF severity
    # contract is encoded in the rule descriptor itself so consumers
    # can introspect it without firing a finding. The SARIF schema
    # nests the default under defaultConfiguration.level (the
    # ``_build_rule`` helper normalises the emitter-side
    # ``defaultLevel`` shortcut into the schema-conformant nested key).
    by_id = {r["id"]: r for r in rules}
    assert by_id["invalid-import"]["defaultConfiguration"]["level"] == "warning"
    # hallucination-import escalates to error — the canonical
    # LLM-era CI-gate signal (an import referencing a name that
    # genuinely isn't in the indexed graph has no remediation path
    # through fuzzy match).
    assert by_id["hallucination-import"]["defaultConfiguration"]["level"] == "error"


def test_classification_bands_map_to_warning_and_error() -> None:
    """Each unresolved import projects onto its distinct SARIF level.

    invalid-import -> ``warning`` (unresolved with FTS5 fuzzy
        candidates — likely typo / rename, the suggestions list gives
        a remediation path).
    hallucination-import -> ``error`` (unresolved with no fuzzy
        candidates — the symbol genuinely doesn't exist anywhere in
        the indexed graph; the canonical LLM-hallucination signal).

    Also exercises the per-import anchor (file:line of the import
    statement) and the message body shape: language prefix, imported
    name (LAW 4 concrete-noun anchor on the import identifier),
    classification, and the joined suggestions list so consumers can
    triage without a JSON-envelope round-trip.
    """
    findings = [
        # Hallucination: no suggestions → error band.
        {
            "file": "src/broken.py",
            "line": 1,
            "name": "totally_made_up_module",
            "status": "unresolved",
            "language": "python",
            "suggestions": [],
        },
        # Invalid (typo): FTS5 found a candidate → warning band.
        {
            "file": "src/typo.py",
            "line": 3,
            "name": "Usre",
            "status": "unresolved",
            "language": "python",
            "suggestions": ["User", "users"],
        },
        # Hallucination on a different language — no suggestions.
        {
            "file": "src/client.ts",
            "line": 12,
            "name": "imaginaryLib",
            "status": "unresolved",
            "language": "typescript",
            "suggestions": [],
        },
    ]

    doc = verify_imports_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 3

    # All three rule ids drawn from the closed enum.
    assert {r["ruleId"] for r in results} <= {
        "invalid-import",
        "hallucination-import",
    }

    # --- hallucination on Python (no suggestions) -> error -------------
    halluc_py = next(r for r in results if "totally_made_up_module" in r["message"]["text"])
    assert halluc_py["ruleId"] == "hallucination-import"
    assert halluc_py["level"] == "error"
    primary = halluc_py["locations"][0]["physicalLocation"]
    assert primary["artifactLocation"]["uri"] == "src/broken.py"
    assert primary["region"]["startLine"] == 1
    msg_halluc = halluc_py["message"]["text"]
    assert "python" in msg_halluc.lower()
    assert "hallucination-import" in msg_halluc
    # Concrete-noun anchor on the imported name (LAW 4).
    assert "totally_made_up_module" in msg_halluc

    # --- invalid-import (typo with suggestions) -> warning -------------
    invalid = next(r for r in results if "Usre" in r["message"]["text"])
    assert invalid["ruleId"] == "invalid-import"
    assert invalid["level"] == "warning"
    inv_loc = invalid["locations"][0]["physicalLocation"]
    assert inv_loc["artifactLocation"]["uri"] == "src/typo.py"
    assert inv_loc["region"]["startLine"] == 3
    msg_invalid = invalid["message"]["text"]
    assert "invalid-import" in msg_invalid
    # Suggestions list surfaces in the message body so SARIF
    # consumers see the remediation path without a JSON-envelope
    # round-trip.
    assert "User" in msg_invalid
    assert "users" in msg_invalid

    # --- hallucination on TypeScript (cross-language coverage) ----------
    halluc_ts = next(r for r in results if "imaginaryLib" in r["message"]["text"])
    assert halluc_ts["ruleId"] == "hallucination-import"
    assert halluc_ts["level"] == "error"
    ts_loc = halluc_ts["locations"][0]["physicalLocation"]
    assert ts_loc["artifactLocation"]["uri"] == "src/client.ts"
    assert ts_loc["region"]["startLine"] == 12
    msg_ts = halluc_ts["message"]["text"]
    assert "typescript" in msg_ts.lower()


def test_unresolved_and_anchorless_findings_are_skipped() -> None:
    """Findings without ``name``, without a ``file`` anchor, with
    ``status: "resolved"``, or with an unknown status are dropped per
    the disclosure discipline.

    Specifically:
      - missing ``name`` (no subject) -> skip
      - missing ``file`` (no anchor) -> skip
      - ``status == "resolved"`` (not actionable — filtered upstream) -> skip
      - unknown / empty ``status`` outside the closed enum -> skip
      - non-dict items (defensive — pathological producer input) -> skip
      - well-formed unresolved row WITHOUT a ``language`` column -> kept
        (the producer keeps language on the file row, not the per-import
        row; the SARIF wrapper backfills it but the helper accepts an
        empty language and just omits the prefix from the message body)

    Mirrors the producer-side disclosure discipline (Pattern 1 / LAW 6):
    a SARIF result without a stable subject cannot be surfaced
    meaningfully.
    """
    findings = [
        # Skipped: missing name (no subject).
        {
            "file": "src/a.py",
            "line": 1,
            "name": "",
            "status": "unresolved",
            "language": "python",
            "suggestions": [],
        },
        # Skipped: ``resolved`` status (filtered upstream of per-result loop).
        {
            "file": "src/b.py",
            "line": 2,
            "name": "models",
            "status": "resolved",
            "language": "python",
            "suggestions": [],
        },
        # Skipped: unknown status (closed-enum discipline).
        {
            "file": "src/c.py",
            "line": 3,
            "name": "mystery",
            "status": "MAYBE_RESOLVED",
            "language": "python",
            "suggestions": [],
        },
        # Skipped: no file anchor.
        {
            "file": "",
            "line": 4,
            "name": "anchorless_import",
            "status": "unresolved",
            "language": "python",
            "suggestions": [],
        },
        # Skipped: not a dict (defensive — pathological producer input).
        "not-a-dict",
        # Kept: well-formed unresolved row WITHOUT language column.
        # The producer envelope doesn't always stamp language on the
        # per-import row, and the SARIF wrapper has to handle the
        # empty case without crashing — the message body just elides
        # the language prefix.
        {
            "file": "src/d.py",
            "line": 99,
            "name": "missingName",
            "status": "unresolved",
            "suggestions": [],
        },
    ]

    doc = verify_imports_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Only the well-formed language-less entry survives the filter.
    assert len(results) == 1
    surviving = results[0]
    # No FTS5 suggestions → hallucination-import / error band.
    assert surviving["ruleId"] == "hallucination-import"
    assert surviving["level"] == "error"
    assert surviving["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/d.py"
    assert surviving["locations"][0]["physicalLocation"]["region"]["startLine"] == 99
    msg = surviving["message"]["text"]
    # Imported name surfaces in the message body (LAW 4 anchor).
    assert "missingName" in msg
    # Language prefix is omitted when the producer didn't stamp one
    # — no leading ``: `` or similar artefact.
    assert not msg.startswith(": ")
    assert not msg.startswith(" :")
