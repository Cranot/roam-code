"""W1226: SARIF projection for ``roam flag-dead`` staleness findings.

The killer signal for flag-dead is *which feature flags are stale and
safe to delete* vs *which are still load-bearing*. cmd_flag_dead scans
source files for feature-flag API calls (LaunchDarkly / Unleash / Split
/ generic / env-var) and groups call sites by flag name, then tags each
flag with one of four staleness states (closed enum: ``stale`` /
``likely_stale`` / ``suspect`` / ``ok``). The SARIF projection mirrors
the three actionable classifications onto three rule ids with distinct
``defaultLevel``:

- ``flag-staleness`` (defaultLevel ``warning``): flag listed in
  ``--config`` known-stale file — operator has already confirmed the
  flag should be removed.
- ``flag-single-reference`` (defaultLevel ``note``): flag has a single
  call site — likely leftover code, advisory band only.
- ``flag-suspect`` (defaultLevel ``warning``): flag is suspect — called
  with the same constant default at every site OR all references
  concentrate in a single file. Both ``suspect`` sub-causes share this
  rule id (named after the envelope's 4-value ``staleness`` vocabulary:
  ``stale`` / ``likely_stale`` / ``suspect`` / ``ok``); the message body
  surfaces the precise reason.

The ``ok`` bucket (no staleness indicators) is filtered upstream so
SARIF consumers never see non-actionable rows. Flag-dead deliberately
does NOT escalate to ``error``: the detector is heuristic (regex-based
scan, no dashboard cross-check) so even the strongest signal stays in
the warning band — mirrors the W1213 ``duplicates`` severity ceiling.

Mirrors the closed-enum test design from ``test_cmd_hotspots_sarif.py``
(W1210) and ``test_cmd_dark_matter_sarif.py`` (W1211), adapted for the
per-flag anchor (PRIMARY = first call site's file:line; SECONDARY = up
to 10 additional call sites).
"""

from __future__ import annotations

from roam.output.sarif import flag_dead_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_hotspots / cmd_dark_matter
    "no findings" path. Flag-dead specifically hits this branch on any
    repo without feature-flag usage — the default for a freshly-indexed
    codebase that doesn't use LaunchDarkly / Unleash / Split.
    """
    doc = flag_dead_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum: 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "flag-staleness",
        "flag-single-reference",
        "flag-suspect",
    }
    # Closed-enum default-level verification — the SARIF severity
    # contract is encoded in the rule descriptors themselves so
    # consumers can introspect it without firing a finding. The
    # SARIF schema nests the default under defaultConfiguration.level
    # (the ``_build_rule`` helper normalises the emitter-side
    # ``defaultLevel`` shortcut into the schema-conformant nested key).
    by_id = {r["id"]: r for r in rules}
    assert by_id["flag-staleness"]["defaultConfiguration"]["level"] == "warning"
    assert by_id["flag-single-reference"]["defaultConfiguration"]["level"] == "note"
    assert by_id["flag-suspect"]["defaultConfiguration"]["level"] == "warning"


def test_classification_bands_map_to_warning_and_note() -> None:
    """Each actionable classification projects onto its distinct SARIF level.

    stale -> ``warning`` (known-stale: operator-confirmed for removal).
    likely_stale -> ``note`` (single call site — advisory band).
    suspect -> ``warning`` (constant default OR all-in-single-file —
    review pressure justified). Also exercises the per-flag anchor:
    PRIMARY = first call site's file:line; SECONDARY = additional
    call sites surfaced when present. Each result message carries the
    flag name (LAW 4 concrete-noun anchor), classification, provider,
    reference count, and joined reasons so consumers can triage
    without a JSON-envelope round-trip.
    """
    findings = [
        {
            "flag_name": "legacy-toggle",
            "provider": "LaunchDarkly",
            "count": 4,
            "staleness": "stale",
            "reasons": ["listed in known-stale config"],
            "default_values": ["false"],
            "is_known_stale": True,
            "locations": [
                {"file": "src/api/handlers.py", "line": 42},
                {"file": "src/api/handlers.py", "line": 87},
                {"file": "src/worker/jobs.py", "line": 113},
                {"file": "src/worker/jobs.py", "line": 200},
            ],
        },
        {
            "flag_name": "beta-button",
            "provider": "Unleash",
            "count": 1,
            "staleness": "likely_stale",
            "reasons": ["only referenced in 1 location"],
            "default_values": [],
            "is_known_stale": False,
            "locations": [
                {"file": "src/ui/buttons.tsx", "line": 17},
            ],
        },
        {
            "flag_name": "feature-x-suspect",
            "provider": "generic",
            "count": 3,
            "staleness": "suspect",
            "reasons": [
                "always checked with same default (false)",
                "all references in single file",
            ],
            "default_values": ["false"],
            "is_known_stale": False,
            "locations": [
                {"file": "src/features/x.py", "line": 10},
                {"file": "src/features/x.py", "line": 25},
                {"file": "src/features/x.py", "line": 88},
            ],
        },
    ]

    doc = flag_dead_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 3

    by_rule = {r["ruleId"]: r for r in results}
    assert set(by_rule.keys()) == {
        "flag-staleness",
        "flag-single-reference",
        "flag-suspect",
    }

    # --- stale -> flag-staleness / warning -----------------------------
    stale = by_rule["flag-staleness"]
    assert stale["level"] == "warning"
    # PRIMARY anchor = first call site (file + line).
    primary = stale["locations"][0]["physicalLocation"]
    assert primary["artifactLocation"]["uri"] == "src/api/handlers.py"
    assert primary["region"]["startLine"] == 42
    # SECONDARY locations exist (4 call sites total — under the 11-cap).
    assert len(stale["locations"]) == 4
    # Message carries the flag name (LAW 4 anchor), classification,
    # provider, ref count, and reasons.
    msg_stale = stale["message"]["text"]
    assert "legacy-toggle" in msg_stale
    assert "stale" in msg_stale
    assert "LaunchDarkly" in msg_stale
    assert "refs=4" in msg_stale
    assert "listed in known-stale config" in msg_stale

    # --- likely_stale -> flag-single-reference / note ------------------
    likely = by_rule["flag-single-reference"]
    assert likely["level"] == "note"
    likely_loc = likely["locations"][0]["physicalLocation"]
    assert likely_loc["artifactLocation"]["uri"] == "src/ui/buttons.tsx"
    assert likely_loc["region"]["startLine"] == 17
    # Single call site -> only 1 location entry.
    assert len(likely["locations"]) == 1
    msg_likely = likely["message"]["text"]
    assert "beta-button" in msg_likely
    assert "likely_stale" in msg_likely
    assert "refs=1" in msg_likely

    # --- suspect -> flag-suspect / warning -----------------------------
    # The two ``suspect`` sub-causes (constant default vs all-in-single-
    # file) share the rule id; the reasons text in the message body
    # distinguishes them.
    suspect = by_rule["flag-suspect"]
    assert suspect["level"] == "warning"
    msg_suspect = suspect["message"]["text"]
    assert "feature-x-suspect" in msg_suspect
    assert "suspect" in msg_suspect
    # Both reasons surfaced — disambiguates the shared rule id.
    assert "same default (false)" in msg_suspect
    assert "all references in single file" in msg_suspect
    # 3 call sites all in same file.
    assert len(suspect["locations"]) == 3
    for loc in suspect["locations"]:
        assert loc["physicalLocation"]["artifactLocation"]["uri"] == "src/features/x.py"


def test_unresolved_and_anchorless_findings_are_skipped() -> None:
    """Findings without a flag_name, with an ``ok`` / unknown
    classification, or without any usable location anchor are skipped.

    Mirrors the producer-side disclosure discipline (Pattern 1 / LAW 6):
    a SARIF result without a stable subject cannot be surfaced
    meaningfully. The ``ok`` bucket is also dropped — no staleness
    indicators means no actionable signal, so SARIF consumers never
    see those rows. Unknown classifications outside the closed
    enumeration drop per LAW 8 / CLAUDE.md Constraint 8.
    """
    findings = [
        # Skipped: missing flag_name (no subject).
        {
            "flag_name": "",
            "provider": "LaunchDarkly",
            "count": 2,
            "staleness": "stale",
            "reasons": ["listed in known-stale config"],
            "locations": [
                {"file": "src/foo.py", "line": 10},
            ],
        },
        # Skipped: ``ok`` classification (no staleness indicators —
        # not actionable, filtered upstream of per-result loop).
        {
            "flag_name": "still-active-flag",
            "provider": "Unleash",
            "count": 12,
            "staleness": "ok",
            "reasons": [],
            "locations": [
                {"file": "src/api.py", "line": 5},
            ],
        },
        # Skipped: unknown classification (closed-enum discipline).
        {
            "flag_name": "weird-flag",
            "provider": "generic",
            "count": 2,
            "staleness": "MYSTERY",
            "reasons": ["unknown reason"],
            "locations": [
                {"file": "src/weird.py", "line": 1},
            ],
        },
        # Skipped: no usable location anchor (empty file path on
        # every loc entry).
        {
            "flag_name": "anchorless-flag",
            "provider": "LaunchDarkly",
            "count": 1,
            "staleness": "likely_stale",
            "reasons": ["only referenced in 1 location"],
            "locations": [
                {"file": "", "line": 0},
            ],
        },
        # Skipped: locations field is the wrong shape (not a list).
        {
            "flag_name": "bad-locs-flag",
            "provider": "Unleash",
            "count": 1,
            "staleness": "stale",
            "reasons": ["listed in known-stale config"],
            "locations": "not-a-list",
        },
        # Skipped: not a dict (defensive — pathological producer input).
        "not-a-dict",
        # Kept: well-formed likely_stale entry survives the filter.
        {
            "flag_name": "real-stale-flag",
            "provider": "LaunchDarkly",
            "count": 1,
            "staleness": "likely_stale",
            "reasons": ["only referenced in 1 location"],
            "locations": [
                {"file": "src/real.py", "line": 50},
            ],
        },
    ]

    doc = flag_dead_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Only the well-formed entry survives the filter.
    assert len(results) == 1
    surviving = results[0]
    assert surviving["ruleId"] == "flag-single-reference"
    assert surviving["level"] == "note"
    assert surviving["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/real.py"
    assert surviving["locations"][0]["physicalLocation"]["region"]["startLine"] == 50
    assert "real-stale-flag" in surviving["message"]["text"]
