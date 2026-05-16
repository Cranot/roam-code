"""W1215: SARIF projection for ``roam bus-factor`` knowledge-loss findings.

cmd_bus_factor ranks every directory by a risk score combining Shannon
entropy of contribution shares, primary-author churn concentration, and
primary-author inactivity (staleness factor). It also persists findings
into the central registry under detector ``bus-factor`` with three
sub-kinds.

SARIF projection emits three closed-enum rule ids with distinct
``defaultLevel`` so a CI gate keyed off SARIF ``level: warning`` only
blocks on the actionable risk bands:

- ``bus-factor/author-concentration`` (defaultLevel ``warning``):
  directory ownership concentrated in a single author (>70%).
- ``bus-factor/stale-ownership`` (defaultLevel ``warning``): primary
  author inactive beyond stale-months threshold.
- ``bus-factor/solo-author-summary`` (defaultLevel ``note``): repo-level
  summary on solo-author repos (W164 collapse).

Per-finding level mapping via ``_bus_factor_risk_level``:

- HIGH -> ``warning``
- MEDIUM / LOW / unknown -> ``note``

Per-finding anchor: the directory path itself (no line number — risk
applies to the directory as a whole, not a specific symbol). SARIF
supports directory-style ``artifactLocation.uri`` entries with no
``region`` key.

Mirrors the closed-enum design from ``test_cmd_auth_gaps_sarif.py``
(W1195) and ``test_cmd_over_fetch_sarif.py`` (W1219), adapted for
directory-level anchors.
"""

from __future__ import annotations

from roam.output.sarif import bus_factor_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_auth_gaps / cmd_over_fetch
    "no findings" path.
    """
    doc = bus_factor_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum: 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "bus-factor/author-concentration",
        "bus-factor/stale-ownership",
        "bus-factor/solo-author-summary",
    }
    # Each rule carries its closed-enum defaultLevel — surfaced via
    # the SARIF builder onto ``defaultConfiguration.level``.
    level_by_id = {r["id"]: r["defaultConfiguration"]["level"] for r in rules}
    assert level_by_id == {
        "bus-factor/author-concentration": "warning",
        "bus-factor/stale-ownership": "warning",
        "bus-factor/solo-author-summary": "note",
    }


def test_concentrated_and_stale_findings_map_to_distinct_rules() -> None:
    """A concentrated+stale directory emits TWO results (one per kind).

    A directory that is BOTH ``concentrated`` AND ``stale_primary``
    emits one ``author-concentration`` row AND one ``stale-ownership``
    row so a SARIF consumer filtering by rule id can isolate just the
    stale set when triaging. Anchor verification: the directory path
    itself (no line — region key absent on physicalLocation).

    Level mapping: HIGH risk -> ``warning``; MEDIUM / LOW -> ``note``.
    """
    findings = [
        # Concentrated + stale + HIGH -> two results, both warning.
        {
            "directory": "src/critical/",
            "bus_factor": 1,
            "entropy": 0.0,
            "risk": "HIGH",
            "risk_score": 2.8,
            "primary_author": "alice",
            "primary_actor": "alice",
            "primary_share_pct": 92,
            "concentrated": True,
            "stale_primary": True,
            "staleness_factor": 2.5,
            "total_commits": 200,
            "total_churn": 10000,
            "top_authors": [
                {"name": "alice", "share_pct": 92, "share": 0.92, "churn": 9200, "commits": 184},
            ],
        },
        # Concentrated only + MEDIUM -> one result, note.
        {
            "directory": "src/helpers/",
            "bus_factor": 1,
            "entropy": 0.15,
            "risk": "MEDIUM",
            "risk_score": 1.0,
            "primary_author": "bob",
            "primary_actor": "bob",
            "primary_share_pct": 80,
            "concentrated": True,
            "stale_primary": False,
            "staleness_factor": 1.0,
            "total_commits": 50,
            "total_churn": 2000,
            "top_authors": [{"name": "bob", "share_pct": 80, "share": 0.80, "churn": 1600, "commits": 40}],
        },
        # Below threshold (neither concentrated nor stale) -> skipped.
        {
            "directory": "src/wellspread/",
            "bus_factor": 4,
            "entropy": 0.85,
            "risk": "LOW",
            "risk_score": 0.3,
            "primary_author": "alice",
            "primary_share_pct": 30,
            "concentrated": False,
            "stale_primary": False,
            "staleness_factor": 1.0,
            "total_commits": 100,
            "total_churn": 4000,
            "top_authors": [],
        },
    ]

    doc = bus_factor_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Two results from the concentrated+stale entry, one from the
    # concentrated-only entry — three total (the below-threshold row
    # is skipped).
    assert len(results) == 3

    # The concentrated+stale directory contributes two results, one
    # per rule kind.
    critical_results = [r for r in results if "src/critical/" in r["message"]["text"]]
    assert len(critical_results) == 2
    critical_rule_ids = {r["ruleId"] for r in critical_results}
    assert critical_rule_ids == {
        "bus-factor/author-concentration",
        "bus-factor/stale-ownership",
    }
    # Both critical/ rows are HIGH risk -> ``warning``.
    assert all(r["level"] == "warning" for r in critical_results)
    # Anchor = directory path (no line region present).
    for r in critical_results:
        phys = r["locations"][0]["physicalLocation"]
        assert phys["artifactLocation"]["uri"] == "src/critical/"
        assert "region" not in phys

    # The MEDIUM-risk concentrated-only directory -> one result, note.
    helpers_results = [r for r in results if "src/helpers/" in r["message"]["text"]]
    assert len(helpers_results) == 1
    hr = helpers_results[0]
    assert hr["ruleId"] == "bus-factor/author-concentration"
    assert hr["level"] == "note"
    # Message body surfaces the directory, owner, share, and risk band.
    text = hr["message"]["text"]
    assert "src/helpers/" in text
    assert "bob" in text
    assert "80%" in text
    assert "risk=MEDIUM" in text


def test_malformed_entries_skipped_and_solo_summary_emits() -> None:
    """Non-dict / missing-directory entries skipped; summary_only emits.

    Defensive parsing per Pattern 1 family discipline — the SARIF
    emitter must not crash on a malformed entry, since the producer
    envelope can carry partial data when the underlying analyzer hits
    an exception. Also exercises the solo-author summary row (W164
    collapse) which carries summary_only=True and projects onto the
    dedicated solo-author-summary rule at level=note.
    """
    findings = [
        "not a dict",  # skipped
        # Empty directory -> skipped (no anchor).
        {
            "directory": "",
            "concentrated": True,
            "stale_primary": False,
            "risk": "HIGH",
        },
        # Solo-author summary entry (W164 collapse).
        {
            "summary_only": True,
            "repo": "https://github.com/example/repo.git",
            "total_directories_analyzed": 42,
            "unique_authors_count": 1,
            "dominant_author": "alice",
            "dominant_actor": "alice",
            "dominant_author_share_pct": 100,
        },
        # Stale-only directory with unknown risk -> defaults to note.
        {
            "directory": "src/forgotten/",
            "bus_factor": 2,
            "entropy": 0.5,
            "risk": "UNKNOWN_BAND",
            "risk_score": 1.2,
            "primary_author": "charlie",
            "primary_share_pct": 55,
            "concentrated": False,
            "stale_primary": True,
            "staleness_factor": 3.0,
            "total_commits": 30,
            "total_churn": 1500,
            "top_authors": [],
        },
    ]

    doc = bus_factor_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Two well-formed entries survive: solo-author-summary + stale-only.
    assert len(results) == 2

    # The summary row projects onto solo-author-summary at note.
    summary_results = [r for r in results if r["ruleId"] == "bus-factor/solo-author-summary"]
    assert len(summary_results) == 1
    sr = summary_results[0]
    assert sr["level"] == "note"
    phys = sr["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "https://github.com/example/repo.git"
    assert "region" not in phys
    text = sr["message"]["text"]
    assert "Solo-author repo summary" in text
    assert "alice" in text
    assert "42 directories" in text

    # The stale-only entry projects onto stale-ownership; unknown risk
    # band defaults to ``note`` via the level mapper (LAW 6).
    stale_results = [r for r in results if r["ruleId"] == "bus-factor/stale-ownership"]
    assert len(stale_results) == 1
    st = stale_results[0]
    assert st["level"] == "note"  # UNKNOWN_BAND -> note
    phys = st["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/forgotten/"
    assert "region" not in phys
    text = st["message"]["text"]
    assert "Stale ownership" in text
    assert "src/forgotten/" in text
    assert "charlie" in text
    assert "staleness factor 3.00" in text
