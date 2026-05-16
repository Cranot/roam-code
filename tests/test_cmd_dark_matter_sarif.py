"""W1211: SARIF projection for ``roam dark-matter`` hidden-coupling output.

The killer signal for dark-matter is *which file pairs co-change frequently
despite having no structural dependency*. One closed-enum rule projects
onto SARIF:

- ``dark-matter/hidden-coupling`` (defaultLevel ``note``): one result
  per detected pair. Per-pair severity is mapped from the W154
  confidence tier (structural -> ``warning``; heuristic -> ``note``).
  PRIMARY = ``file_a``; SECONDARY = ``file_b`` (no line number — the
  signal is file-pair-level).

Mirrors the test design from ``test_cmd_clones_sarif.py`` (W1172) and
``test_cmd_n1_sarif.py`` (W1208): every finding family the command emits
must round-trip through SARIF without losing its severity / message /
anchor.
"""

from __future__ import annotations

from roam.output.sarif import dark_matter_to_sarif


def test_empty_dark_matter_envelope_produces_valid_sarif_with_zero_results() -> None:
    """A zero-finding list emits a valid SARIF doc with 0 results.

    Mirrors the cmd_clones / cmd_n1 "no findings" path: the rules array
    is always populated (so consumers can introspect the rule catalogue
    even when nothing fired), but ``results`` is empty.
    """
    doc = dark_matter_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 1 rule).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {"dark-matter/hidden-coupling"}
    # defaultLevel is "note" — dark-matter is a refactor / observability
    # signal, not a defect that should block CI on its own. Note that
    # ``_build_rule`` projects the input ``defaultLevel`` onto the SARIF
    # ``defaultConfiguration.level`` key.
    by_id = {r["id"]: r for r in rules}
    assert by_id["dark-matter/hidden-coupling"]["defaultConfiguration"]["level"] == "note"


def test_dark_matter_confidence_tier_bands_map_to_warning_and_note() -> None:
    """W154 confidence tier scales the SARIF level.

    Typed hypothesis category (``SHARED_DB`` / ``EVENT_BUS`` /
    ``SHARED_CONFIG`` / ``SHARED_API`` / ``TEXT_SIMILARITY`` /
    ``COPY_PASTE`` / ``NAMING``) -> ``structural`` -> SARIF
    ``warning`` (the engine resolved a concrete cause beyond raw
    NPMI correlation). ``UNKNOWN`` / missing -> ``heuristic`` ->
    SARIF ``note`` (pure statistical correlation; higher false-
    positive risk). Also exercises the two-sided anchor: PRIMARY =
    ``file_a``, SECONDARY = ``file_b``, no line number.
    """
    findings = [
        # Typed hypothesis -> structural -> warning.
        {
            "file_a": "src/api/auth.py",
            "file_b": "src/db/users.sql",
            "npmi": 0.82,
            "lift": 4.3,
            "strength": 0.71,
            "cochange_count": 12,
            "hypothesis": {
                "category": "SHARED_DB",
                "detail": "both reference users table",
                "confidence": "high",
            },
        },
        # UNKNOWN -> heuristic -> note.
        {
            "file_a": "src/web/routes.py",
            "file_b": "templates/index.html",
            "npmi": 0.45,
            "lift": 2.1,
            "strength": 0.38,
            "cochange_count": 5,
            "hypothesis": {
                "category": "UNKNOWN",
                "detail": "",
                "confidence": "low",
            },
        },
    ]

    doc = dark_matter_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 2

    by_anchor = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r for r in results}

    # Typed (SHARED_DB) -> structural -> warning.
    typed = by_anchor["src/api/auth.py"]
    assert typed["ruleId"] == "dark-matter/hidden-coupling"
    assert typed["level"] == "warning"
    # Two-sided anchor: PRIMARY (file_a) + SECONDARY (file_b). No
    # region (dark-matter is a file-pair-level signal).
    assert len(typed["locations"]) == 2
    assert typed["locations"][1]["physicalLocation"]["artifactLocation"]["uri"] == "src/db/users.sql"
    assert "region" not in typed["locations"][0]["physicalLocation"]
    assert "region" not in typed["locations"][1]["physicalLocation"]
    # Message carries NPMI, co-change count, hypothesis category +
    # detail so consumers can triage without a JSON-envelope round-trip.
    msg = typed["message"]["text"]
    assert "src/api/auth.py" in msg
    assert "src/db/users.sql" in msg
    assert "0.82" in msg
    assert "12" in msg
    assert "SHARED_DB" in msg
    assert "users table" in msg

    # UNKNOWN -> heuristic -> note.
    unknown = by_anchor["src/web/routes.py"]
    assert unknown["level"] == "note"
    # Message omits hypothesis suffix on UNKNOWN (no resolved cause to
    # surface).
    unknown_msg = unknown["message"]["text"]
    assert "Hypothesis" not in unknown_msg


def test_dark_matter_skips_pairs_without_primary_anchor_and_handles_missing_hypothesis() -> None:
    """Pairs missing ``file_a`` are skipped (no PRIMARY anchor).

    Mirrors the ``clones_to_sarif`` pair-without-file_a discipline:
    without a PRIMARY anchor a SARIF consumer cannot surface the row
    meaningfully, so we skip rather than emit an anchorless result.

    Also exercises the missing-hypothesis fallback: a pair with no
    ``hypothesis`` key (legacy / text-mode fixtures) still emits
    correctly as a heuristic-tier note. Pairs with non-canonical
    ordering (file_a > file_b lexicographically) are accepted as-is —
    the canonical pair ordering is enforced at the producer side
    (cmd_dark_matter._canonical_pair); the SARIF emitter does not
    re-canonicalise.
    """
    findings = [
        # Skipped: no file_a anchor.
        {
            "file_a": "",
            "file_b": "src/orphan.py",
            "npmi": 0.5,
            "cochange_count": 4,
        },
        # Skipped: not a dict.
        "not-a-dict",  # type: ignore[list-item]
        # Accepted: no hypothesis key (legacy fixture) -> heuristic -> note.
        {
            "file_a": "src/a.py",
            "file_b": "src/b.py",
            "npmi": 0.6,
            "lift": 3.2,
            "strength": 0.55,
            "cochange_count": 8,
        },
        # Accepted: file_b missing -> single-location result (PRIMARY only).
        {
            "file_a": "src/solo.py",
            "file_b": "",
            "npmi": 0.55,
            "lift": 2.8,
            "cochange_count": 6,
        },
    ]

    doc = dark_matter_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # 2 emitted (anchorless / non-dict skipped).
    assert len(results) == 2

    by_anchor = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r for r in results}

    # Missing hypothesis -> heuristic -> note.
    no_hyp = by_anchor["src/a.py"]
    assert no_hyp["ruleId"] == "dark-matter/hidden-coupling"
    assert no_hyp["level"] == "note"
    assert len(no_hyp["locations"]) == 2  # file_a + file_b
    # No hypothesis suffix on the message.
    assert "Hypothesis" not in no_hyp["message"]["text"]

    # Missing file_b -> single-location result.
    solo = by_anchor["src/solo.py"]
    assert len(solo["locations"]) == 1
    assert solo["level"] == "note"
