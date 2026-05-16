"""W1159: SARIF projection for ``roam partition`` multi-agent manifest output.

The killer signal for partition is *which work zones the fleet planner
identified and how risky each is to dispatch in parallel*. Two finding
families project onto SARIF, each on its own closed-enum rule id:

- ``partition/conflict-risk`` (defaultLevel ``warning``): per-partition
  conflict label scaled to a SARIF level. HIGH conflict risk escalates
  to ``error`` so a CI gate keyed off SARIF ``level: error`` can refuse
  to dispatch a partition that would almost certainly collide with
  another agent's work. PRIMARY anchor: the partition's first file;
  SECONDARY locations: up to 10 additional files.
- ``partition/key-symbol`` (defaultLevel ``note``): per-key-symbol
  finding inside each partition. PageRank-ranked anchors — letting a
  SARIF consumer link directly to the highest-leverage symbol in each
  work zone.

Mirrors the test design from ``test_cmd_impact_sarif.py`` (W1165): every
finding family the command emits must round-trip through SARIF without
losing its severity / message / anchor.
"""

from __future__ import annotations

from roam.output.sarif import partition_to_sarif


def test_empty_partition_produces_valid_sarif_with_zero_results() -> None:
    """A zero-partition envelope emits a valid SARIF doc with 0 results.

    Mirrors the cmd_impact / cmd_affected_tests "no findings" path: the
    rules array is always populated (so consumers can introspect the
    rule catalogue even when nothing fired), but ``results`` is empty.
    """
    empty_envelope = {
        "command": "partition",
        "summary": {
            "verdict": "0 partitions for 2 agents, conflict probability 0%",
            "total_partitions": 0,
            "n_agents": 2,
            "overall_conflict_probability": 0.0,
        },
        "partitions": [],
        "dependencies": [],
        "conflict_hotspots": [],
        "merge_order": [],
    }

    doc = partition_to_sarif(empty_envelope)

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 2 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {"partition/conflict-risk", "partition/key-symbol"}


def test_partition_conflict_risk_levels_map_correctly() -> None:
    """HIGH/MEDIUM/LOW conflict_risk maps to error/warning/note.

    Mirrors :func:`roam.commands.cmd_partition._classify_conflict_risk`
    — HIGH escalates to ``error`` so a CI gate can refuse to dispatch
    a colliding partition. Tests all three bands in one envelope so the
    closed enumeration is exercised end-to-end.
    """
    envelope = {
        "command": "partition",
        "summary": {"verdict": "3 partitions for 3 agents"},
        "partitions": [
            {
                "id": 1,
                "label": "api",
                "role": "API Layer",
                "agent": "Worker-1",
                "files": ["src/api/routes.py", "src/api/views.py"],
                "key_symbols": [],
                "conflict_risk": "HIGH",
                "cross_partition_edges": 25,
                "cochange_score": 12,
            },
            {
                "id": 2,
                "label": "db",
                "role": "Database Layer",
                "agent": "Worker-2",
                "files": ["src/db/models.py"],
                "key_symbols": [],
                "conflict_risk": "MEDIUM",
                "cross_partition_edges": 8,
                "cochange_score": 4,
            },
            {
                "id": 3,
                "label": "utils",
                "role": "Utility Layer",
                "agent": "Worker-3",
                "files": ["src/utils/helpers.py"],
                "key_symbols": [],
                "conflict_risk": "LOW",
                "cross_partition_edges": 2,
                "cochange_score": 1,
            },
        ],
        "dependencies": [],
        "conflict_hotspots": [],
        "merge_order": [],
    }

    doc = partition_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    # 3 partition-level conflict-risk findings; no key-symbol findings.
    assert len(results) == 3

    by_id = {}
    for r in results:
        assert r["ruleId"] == "partition/conflict-risk"
        # Anchor on PRIMARY location's file.
        phys = r["locations"][0]["physicalLocation"]
        by_id[phys["artifactLocation"]["uri"]] = r

    # HIGH -> error
    high = by_id["src/api/routes.py"]
    assert high["level"] == "error"
    # First file is PRIMARY anchor; second file attaches as a SECONDARY
    # location (two-file partition).
    assert len(high["locations"]) == 2
    assert high["locations"][1]["physicalLocation"]["artifactLocation"]["uri"] == "src/api/views.py"
    # Message carries the role + agent + conflict label.
    assert "API Layer" in high["message"]["text"]
    assert "Worker-1" in high["message"]["text"]
    assert "HIGH" in high["message"]["text"]

    # MEDIUM -> warning
    assert by_id["src/db/models.py"]["level"] == "warning"
    # LOW -> note
    assert by_id["src/utils/helpers.py"]["level"] == "note"


def test_partition_key_symbol_findings_anchor_on_symbol_file() -> None:
    """Each key_symbols[] entry emits one note-level finding per partition.

    The key symbol's file is the SARIF location anchor; name + kind +
    PageRank score appear in the message body so SARIF consumers can
    surface the highest-leverage symbols without round-tripping to the
    JSON envelope. Also covers the SECONDARY-location cap (11 files in
    a single partition collapses to 1 PRIMARY + 10 SECONDARY).
    """
    # Build 11 files so the secondary-location cap kicks in.
    many_files = [f"src/big/file_{i}.py" for i in range(11)]
    envelope = {
        "command": "partition",
        "summary": {"verdict": "1 partition"},
        "partitions": [
            {
                "id": 1,
                "label": "graph",
                "role": "Graph/Analysis Layer",
                "agent": "Worker-1",
                "files": many_files,
                "key_symbols": [
                    {
                        "name": "build_symbol_graph",
                        "kind": "fn",
                        "pagerank": 0.0421,
                        "file": "src/big/file_0.py",
                    },
                    {
                        "name": "PartitionEngine",
                        "kind": "cls",
                        "pagerank": 0.0317,
                        "file": "src/big/file_3.py",
                    },
                ],
                "conflict_risk": "MEDIUM",
                "cross_partition_edges": 7,
                "cochange_score": 3,
            },
        ],
        "dependencies": [],
        "conflict_hotspots": [],
        "merge_order": [],
    }

    doc = partition_to_sarif(envelope)
    results = doc["runs"][0]["results"]
    # 1 conflict-risk finding + 2 key-symbol findings.
    assert len(results) == 3

    # Conflict-risk PRIMARY + SECONDARY count: PRIMARY (1) + at most 10
    # SECONDARY = 11 total locations (cap exercised; remaining 0 files
    # dropped — there are exactly 11 so the slice is exhaustive).
    conflict = next(r for r in results if r["ruleId"] == "partition/conflict-risk")
    assert len(conflict["locations"]) == 11
    # PRIMARY anchor.
    assert conflict["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/big/file_0.py"

    # Key-symbol findings (per-symbol anchor + message).
    sym_findings = [r for r in results if r["ruleId"] == "partition/key-symbol"]
    assert len(sym_findings) == 2

    by_anchor = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r for r in sym_findings}

    bsg = by_anchor["src/big/file_0.py"]
    assert bsg["level"] == "note"
    assert "build_symbol_graph" in bsg["message"]["text"]
    assert "fn" in bsg["message"]["text"]
    # PageRank score surfaces in the message.
    assert "0.0421" in bsg["message"]["text"]

    pe = by_anchor["src/big/file_3.py"]
    assert "PartitionEngine" in pe["message"]["text"]
    assert "cls" in pe["message"]["text"]
