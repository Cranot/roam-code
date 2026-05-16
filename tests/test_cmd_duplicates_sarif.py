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
