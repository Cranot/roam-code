"""W1172: SARIF projection for ``roam clones`` AST-clone-detection output.

The killer signal for clones is *which pairs of functions duplicate each
other's structure and which clusters of 3+ functions share a skeleton*.
Two finding families project onto SARIF, each on its own closed-enum
rule id:

- ``clones/pair`` (defaultLevel ``note``): per-pair clone finding scaled
  by similarity score. >= 0.95 -> ``warning`` (near-identical, almost
  certainly an unintentional duplicate); lower bands collapse to
  ``note`` (structural skeleton match). Two-sided anchor: PRIMARY =
  ``file_a:line_a``, SECONDARY = ``file_b:line_b``.
- ``clones/cluster`` (defaultLevel ``warning``): per-cluster finding
  (3+ members at high similarity). PRIMARY = first member's file:line;
  up to 10 additional members attach as SECONDARY locations.

Mirrors the test design from ``test_cmd_partition_sarif.py`` (W1159)
and ``test_cmd_impact_sarif.py`` (W1165): every finding family the
command emits must round-trip through SARIF without losing its
severity / message / anchor.
"""

from __future__ import annotations

from roam.output.sarif import clones_to_sarif


def test_empty_clones_envelope_produces_valid_sarif_with_zero_results() -> None:
    """A zero-finding envelope emits a valid SARIF doc with 0 results.

    Mirrors the cmd_impact / cmd_affected_tests / cmd_partition "no
    findings" path: the rules array is always populated (so consumers
    can introspect the rule catalogue even when nothing fired), but
    ``results`` is empty.
    """
    empty_envelope = {
        "command": "clones",
        "summary": {
            "verdict": "No structural clones detected",
            "clusters": 0,
            "clone_pairs": 0,
        },
        "clusters": [],
        "pairs": [],
    }

    doc = clones_to_sarif(empty_envelope)

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 2 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {"clones/pair", "clones/cluster"}


def test_clones_pair_severity_bands_map_to_warning_and_note() -> None:
    """Similarity score scales the SARIF level via :func:`_clones_pair_level`.

    >= 0.95 -> ``warning`` (near-identical duplicate); lower bands ->
    ``note`` (structural skeleton match). Pair severity NEVER
    escalates to ``error`` — clones are refactor opportunities, not
    defects that should block CI. Also exercises the two-sided
    anchor: PRIMARY = ``file_a:line_a``, SECONDARY = ``file_b:line_b``.
    """
    envelope = {
        "command": "clones",
        "summary": {"verdict": "2 clone pairs"},
        "clusters": [],
        "pairs": [
            {
                "value": {
                    "file_a": "src/foo/a.py",
                    "func_a": "do_thing",
                    "line_a": 12,
                    "file_b": "src/foo/b.py",
                    "func_b": "do_thing_v2",
                    "line_b": 45,
                    "similarity": 0.97,
                    "role_bucket": "production",
                },
                "confidence": "high",
                "reason": "similarity 0.97 >= 0.90 — near-identical clone",
            },
            {
                "value": {
                    "file_a": "src/bar/x.py",
                    "func_a": "small_helper",
                    "line_a": 5,
                    "file_b": "tests/test_bar.py",
                    "func_b": "test_small_helper_shape",
                    "line_b": 88,
                    "similarity": 0.78,
                    "role_bucket": "mixed",
                },
                "confidence": "medium",
                "reason": "similarity 0.78 in [0.70, 0.90)",
            },
        ],
    }

    doc = clones_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 2

    by_anchor = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r for r in results}

    # >= 0.95 -> "warning"
    high = by_anchor["src/foo/a.py"]
    assert high["ruleId"] == "clones/pair"
    assert high["level"] == "warning"
    # Two-sided anchor: PRIMARY + SECONDARY (file_b).
    assert len(high["locations"]) == 2
    assert high["locations"][0]["physicalLocation"]["region"]["startLine"] == 12
    assert high["locations"][1]["physicalLocation"]["artifactLocation"]["uri"] == "src/foo/b.py"
    assert high["locations"][1]["physicalLocation"]["region"]["startLine"] == 45
    # Message carries function names + similarity + role_bucket.
    msg = high["message"]["text"]
    assert "do_thing" in msg
    assert "do_thing_v2" in msg
    assert "97%" in msg
    assert "production" in msg

    # < 0.95 -> "note"
    low = by_anchor["src/bar/x.py"]
    assert low["level"] == "note"
    assert "mixed" in low["message"]["text"]


def test_clones_cluster_anchors_first_member_with_secondary_cap() -> None:
    """Each cluster emits one ``clones/cluster`` finding (warning level).

    PRIMARY = first member's file:line; SECONDARY = up to 10 additional
    members. Cluster level is uniformly ``warning`` (cluster of 3+
    duplicates is a stronger signal than a single pair, but still a
    refactor opportunity not a defect). The 11-member cluster in this
    test exercises the secondary-location cap.
    """
    # Build an 11-member cluster so the secondary cap (1 PRIMARY + 10
    # SECONDARY = 11) is exhaustive but does not overflow.
    members = [
        {
            "file": f"src/lang/{name}_lang.py",
            "function": f"extract_{name}",
            "line_start": 100 + i,
            "line_end": 150 + i,
            "ast_nodes": 42,
        }
        for i, name in enumerate(
            [
                "python",
                "javascript",
                "typescript",
                "java",
                "go",
                "rust",
                "ruby",
                "kotlin",
                "swift",
                "scala",
                "php",
            ]
        )
    ]
    envelope = {
        "command": "clones",
        "summary": {"verdict": "1 clone cluster"},
        "clusters": [
            {
                "value": {
                    "cluster_id": 7,
                    "avg_similarity": 0.92,
                    "size": 11,
                    "members": members,
                    "pattern": "parallel language extractors",
                    "suggestion": "extract common base",
                    "role_bucket": "production",
                },
                "confidence": "high",
                "reason": "similarity 0.92 >= 0.90 — near-identical clone",
            },
        ],
        "pairs": [],
    }

    doc = clones_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 1

    cluster_result = results[0]
    assert cluster_result["ruleId"] == "clones/cluster"
    assert cluster_result["level"] == "warning"

    # PRIMARY (1) + SECONDARY (up to 10) = 11 total locations.
    locs = cluster_result["locations"]
    assert len(locs) == 11
    # PRIMARY anchor is the first member.
    assert locs[0]["physicalLocation"]["artifactLocation"]["uri"] == "src/lang/python_lang.py"
    assert locs[0]["physicalLocation"]["region"]["startLine"] == 100

    # Message carries cluster id + size + avg similarity + role bucket +
    # pattern hint.
    msg = cluster_result["message"]["text"]
    assert "#7" in msg
    assert "11 functions" in msg
    assert "92%" in msg
    assert "production" in msg
    assert "parallel language extractors" in msg


def test_clones_cluster_truncates_oversized_secondary_locations() -> None:
    """A 15-member cluster collapses to 1 PRIMARY + 10 SECONDARY.

    Larger-than-cap clusters must NOT overflow the SARIF document — the
    secondary cap (``_CLONES_MAX_SECONDARY_LOCS = 10``) is a hard limit
    so a pathological clone cluster cannot inflate the document beyond
    what GitHub Code Scanning can render.
    """
    members = [
        {
            "file": f"src/big/file_{i}.py",
            "function": f"fn_{i}",
            "line_start": 10 + i,
            "line_end": 50 + i,
            "ast_nodes": 30,
        }
        for i in range(15)
    ]
    envelope = {
        "command": "clones",
        "clusters": [
            {
                "value": {
                    "cluster_id": 1,
                    "avg_similarity": 0.88,
                    "size": 15,
                    "members": members,
                    "pattern": "",
                    "suggestion": "",
                    "role_bucket": "production",
                },
            }
        ],
        "pairs": [],
    }

    doc = clones_to_sarif(envelope)
    cluster_result = doc["runs"][0]["results"][0]
    # 15 members capped to 11 locations (1 PRIMARY + 10 SECONDARY).
    assert len(cluster_result["locations"]) == 11


def test_clones_accepts_raw_unwrapped_shapes() -> None:
    """The converter unwraps ``{value, confidence}`` triples but also
    accepts raw entries.

    Tests can feed minimal fixtures without round-tripping through
    :func:`wrap_findings`. This keeps the converter's contract honest:
    structural shape > triple presence.
    """
    envelope = {
        "command": "clones",
        "clusters": [],
        "pairs": [
            # Raw pair (no {value} wrapper).
            {
                "file_a": "src/a.py",
                "func_a": "fn_a",
                "line_a": 1,
                "file_b": "src/b.py",
                "func_b": "fn_b",
                "line_b": 2,
                "similarity": 0.99,
                "role_bucket": "production",
            },
        ],
    }

    doc = clones_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "clones/pair"
    assert r["level"] == "warning"  # 0.99 >= 0.95
    assert len(r["locations"]) == 2
