"""W1213: SARIF projection for ``roam duplicates`` semantic-duplicate output.

The killer signal for duplicates is *which clusters of 2+ semantically
similar functions can be consolidated through refactoring*. One finding
family projects onto SARIF on a single closed-enum rule id:

- ``duplicates/cluster`` (defaultLevel ``note``): per-cluster duplicate
  finding scaled by similarity score via
  :func:`_duplicates_cluster_level`. >= 0.95 -> ``warning`` (near-
  identical cluster); lower bands collapse to ``note`` (structural-
  pattern match). Cluster severity NEVER escalates to ``error`` —
  duplicates are refactor opportunities, not defects. PRIMARY = first
  member's file:line; up to 10 additional members attach as SECONDARY
  locations.

Mirrors the test design from ``test_cmd_clones_sarif.py`` (W1172):
every finding family the command emits must round-trip through SARIF
without losing its severity / message / anchor. Where ``clones``
compares AST subtree hashes, ``duplicates`` clusters by weighted
similarity of AST-derived metrics — so the SARIF surfaces stay on
distinct rule prefixes for filter / triage clarity.
"""

from __future__ import annotations

from roam.output.sarif import duplicates_to_sarif


def test_empty_duplicates_envelope_produces_valid_sarif_with_zero_results() -> None:
    """A zero-finding envelope emits a valid SARIF doc with 0 results.

    Mirrors the cmd_clones / cmd_partition / cmd_affected_tests "no
    findings" path: the rules array is always populated (so consumers
    can introspect the rule catalogue even when nothing fired), but
    ``results`` is empty. The closed-enum rule vocabulary is fixed at
    1 entry (``duplicates/cluster``).
    """
    empty_envelope = {
        "command": "duplicates",
        "summary": {
            "verdict": "No semantic duplicates detected",
            "total_clusters": 0,
            "total_functions": 0,
            "estimated_reducible_lines": 0,
        },
        "clusters": [],
    }

    doc = duplicates_to_sarif(empty_envelope)

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 1 rule).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {"duplicates/cluster"}


def test_duplicates_cluster_severity_bands_map_to_warning_and_note() -> None:
    """Similarity score scales the SARIF level via :func:`_duplicates_cluster_level`.

    >= 0.95 -> ``warning`` (near-identical duplicate cluster); lower
    bands -> ``note`` (structural-pattern match). Cluster severity
    NEVER escalates to ``error`` — duplicates are refactor
    opportunities, not defects that should block CI. Also exercises
    the multi-member anchor: PRIMARY = first member's file:line,
    SECONDARY = remaining members' file:line.
    """
    envelope = {
        "command": "duplicates",
        "summary": {"verdict": "2 duplicate clusters found"},
        "clusters": [
            {
                "id": 1,
                "similarity": 0.97,
                "size": 2,
                "functions": [
                    {
                        "name": "handle_save",
                        "qualified_name": "src.foo.a.handle_save",
                        "kind": "function",
                        "file": "src/foo/a.py",
                        "line": 12,
                        "lines": 18,
                        "pagerank": 0.04,
                    },
                    {
                        "name": "handle_save_v2",
                        "qualified_name": "src.foo.b.handle_save_v2",
                        "kind": "function",
                        "file": "src/foo/b.py",
                        "line": 45,
                        "lines": 19,
                        "pagerank": 0.02,
                    },
                ],
                "pattern": "shared save logic with v2 variant",
                "suggestion": "Extract common logic into a generic save_handler() helper",
                "role_bucket": "production",
            },
            {
                "id": 2,
                "similarity": 0.78,
                "size": 2,
                "functions": [
                    {
                        "name": "small_helper",
                        "qualified_name": "src.bar.x.small_helper",
                        "kind": "function",
                        "file": "src/bar/x.py",
                        "line": 5,
                        "lines": 6,
                        "pagerank": 0.001,
                    },
                    {
                        "name": "test_small_helper_shape",
                        "qualified_name": "tests.test_bar.test_small_helper_shape",
                        "kind": "function",
                        "file": "tests/test_bar.py",
                        "line": 88,
                        "lines": 7,
                        "pagerank": 0.0,
                    },
                ],
                "pattern": "similar control flow structure",
                "suggestion": "Extract shared logic into a parameterized helper function",
                "role_bucket": "mixed",
            },
        ],
    }

    doc = duplicates_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 2

    by_anchor = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r for r in results}

    # >= 0.95 -> "warning"
    high = by_anchor["src/foo/a.py"]
    assert high["ruleId"] == "duplicates/cluster"
    assert high["level"] == "warning"
    # Multi-member anchor: PRIMARY + SECONDARY (file_b).
    assert len(high["locations"]) == 2
    assert high["locations"][0]["physicalLocation"]["region"]["startLine"] == 12
    assert high["locations"][1]["physicalLocation"]["artifactLocation"]["uri"] == "src/foo/b.py"
    assert high["locations"][1]["physicalLocation"]["region"]["startLine"] == 45
    # Message carries cluster size + similarity + role bucket + anchor
    # name + pattern + suggestion.
    msg = high["message"]["text"]
    assert "2 functions" in msg
    assert "97%" in msg
    assert "production" in msg
    assert "handle_save" in msg
    assert "shared save logic" in msg
    assert "Suggestion" in msg

    # < 0.95 -> "note"
    low = by_anchor["src/bar/x.py"]
    assert low["level"] == "note"
    assert "mixed" in low["message"]["text"]


def test_duplicates_cluster_truncates_oversized_secondary_locations() -> None:
    """A 15-member cluster collapses to 1 PRIMARY + 10 SECONDARY.

    Larger-than-cap clusters must NOT overflow the SARIF document — the
    secondary cap (``_DUPLICATES_MAX_SECONDARY_LOCS = 10``) is a hard
    limit so a pathological duplicate cluster (e.g. parametrize-heavy
    test corpus) cannot inflate the document beyond what GitHub Code
    Scanning can render. Mirrors the W1172 ``_CLONES_MAX_SECONDARY_LOCS``
    discipline.
    """
    functions = [
        {
            "name": f"fn_{i}",
            "qualified_name": f"src.big.file_{i}.fn_{i}",
            "kind": "function",
            "file": f"src/big/file_{i}.py",
            "line": 10 + i,
            "lines": 40,
            "pagerank": 0.01,
        }
        for i in range(15)
    ]
    envelope = {
        "command": "duplicates",
        "clusters": [
            {
                "id": 1,
                "similarity": 0.88,
                "size": 15,
                "functions": functions,
                "pattern": "shared process logic",
                "suggestion": "",
                "role_bucket": "production",
            },
        ],
    }

    doc = duplicates_to_sarif(envelope)
    cluster_result = doc["runs"][0]["results"][0]
    # 15 members capped to 11 locations (1 PRIMARY + 10 SECONDARY).
    assert len(cluster_result["locations"]) == 11
    assert cluster_result["ruleId"] == "duplicates/cluster"
    # 0.88 < 0.95 -> note (structural-pattern match band, not near-identical).
    assert cluster_result["level"] == "note"
    # PRIMARY anchor is the first member.
    assert cluster_result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/big/file_0.py"
    assert cluster_result["locations"][0]["physicalLocation"]["region"]["startLine"] == 10


# ---------------------------------------------------------------------------
# W1529: the token budget must not empty a SARIF projection silently
# ---------------------------------------------------------------------------


def _cluster(i: int) -> dict:
    """One realistic duplicates cluster, big enough that N of them blow the cap."""
    return {
        "size": 2,
        "similarity": 0.91,
        "pattern": f"structural pattern number {i} repeated across modules",
        "suggestion": f"extract a shared helper for cluster {i} to remove the repetition",
        "role_bucket": "production",
        "functions": [
            {"name": f"handler_{i}_a", "file": f"src/pkg/module_{i}_alpha.py", "line": 10 + i},
            {"name": f"handler_{i}_b", "file": f"src/pkg/module_{i}_beta.py", "line": 40 + i},
        ],
    }


def _duplicates_envelope(n: int, *, uncapped: bool) -> dict:
    from roam.output.formatter import json_envelope

    clusters = [_cluster(i) for i in range(n)]
    kwargs = {"uncapped": True} if uncapped else {}
    return json_envelope(
        "duplicates",
        summary={
            "verdict": f"{n} duplicate clusters found",
            "total_clusters": n,
            "total_functions": n * 2,
            "estimated_reducible_lines": n * 12,
        },
        clusters=clusters,
        **kwargs,
    )


def _notifications(doc: dict) -> list[dict]:
    invocations = doc["runs"][0].get("invocations") or []
    if not invocations:
        return []
    return invocations[0].get("toolExecutionNotifications") or []


# --- must fire -------------------------------------------------------------


def test_default_budget_empties_a_large_duplicates_envelope() -> None:
    """The mechanism itself: without the opt-out the payload is stripped.

    This is the pre-existing behaviour the SARIF projector was reading. It
    is asserted directly so the regression test below cannot go vacuously
    green on a corpus that was never large enough to be truncated.
    """
    env = _duplicates_envelope(400, uncapped=False)
    assert len(env["clusters"]) < 400, "fixture is too small to exercise the default budget"
    assert env["summary"].get("truncated") is True, f"expected budget truncation, got {env['summary']}"


def test_sarif_projection_keeps_every_cluster_the_run_measured() -> None:
    """A SARIF document must not report fewer findings than the run counted.

    Measured before the fix on this repo: `roam --sarif duplicates` emitted
    0 results, rc 0, while the same run's own verdict said 99 clusters.
    """
    env = _duplicates_envelope(400, uncapped=True)
    assert len(env["clusters"]) == 400, "SARIF envelopes must not be budget-capped"

    doc = duplicates_to_sarif(env)
    assert len(doc["runs"][0]["results"]) == env["summary"]["total_clusters"]


def test_a_truncated_envelope_reaching_the_projector_is_disclosed() -> None:
    """Belt and braces: the opt-out is one keyword away from regressing.

    If a truncated envelope ever reaches the projector again, the document
    itself says so instead of presenting a short result set as the whole
    measurement.
    """
    env = _duplicates_envelope(400, uncapped=False)
    doc = duplicates_to_sarif(env)

    notes = _notifications(doc)
    assert notes, f"a truncated envelope must produce a toolExecutionNotification; got {doc['runs'][0]}"
    text = " ".join(n["message"]["text"] for n in notes)
    assert "truncated" in text.lower(), text
    assert "not a clean-scan result" in text.lower(), text


def test_partition_projector_discloses_a_truncated_envelope_too() -> None:
    """Same envelope->SARIF shape at cmd_partition.py; latent, guarded anyway."""
    from roam.output.sarif import partition_to_sarif

    doc = partition_to_sarif(
        {
            "command": "partition",
            "summary": {
                "verdict": "2 partitions",
                "truncated": True,
                "truncation_reason": "budget",
                "emitted_counts": {"partitions": 0},
            },
            "partitions": [],
        }
    )
    assert _notifications(doc), "truncated partition envelope must be disclosed in SARIF"


# --- must NOT fire ---------------------------------------------------------


def test_complete_envelope_carries_no_truncation_notification() -> None:
    """A run that emitted everything must not claim degradation.

    This is the assertion that keeps the notification meaningful: if every
    document carried it, a Code-Scanning consumer would learn nothing from
    seeing it.
    """
    env = _duplicates_envelope(3, uncapped=False)
    assert env["summary"].get("truncated") is not True, "fixture unexpectedly truncated"

    doc = duplicates_to_sarif(env)
    assert len(doc["runs"][0]["results"]) == 3
    assert _notifications(doc) == [], f"complete run must not be flagged: {_notifications(doc)}"


def test_empty_envelope_is_not_reported_as_truncated() -> None:
    """A genuinely clean repo stays a clean zero-result document.

    Guards the over-eager fix that would treat every zero-result document as
    a truncation and make a clean run look degraded.
    """
    doc = duplicates_to_sarif(
        {
            "command": "duplicates",
            "summary": {"verdict": "No semantic duplicates detected", "total_clusters": 0},
            "clusters": [],
        }
    )
    assert doc["runs"][0]["results"] == []
    assert _notifications(doc) == []


def test_uncapped_does_not_change_a_small_envelope() -> None:
    """Below the cap the opt-out is a no-op, so ordinary output is unchanged."""
    capped = _duplicates_envelope(3, uncapped=False)
    uncapped = _duplicates_envelope(3, uncapped=True)
    for env in (capped, uncapped):
        env.pop("_meta", None)
    assert capped == uncapped


def test_json_mode_envelope_is_still_budget_bounded() -> None:
    """The Pattern-6 default cap on ordinary --json envelopes is untouched.

    Scope guard: the fix is two envelope->SARIF call sites, NOT a widening
    of _default_json_budget or _apply_envelope_budget for everyone.
    """
    from roam.output.formatter import json_envelope

    env = json_envelope("uses", summary={"verdict": "x"}, refs=[_cluster(i) for i in range(400)])
    assert env["summary"].get("truncated") is True, "the default budget must still bound --json payloads"
