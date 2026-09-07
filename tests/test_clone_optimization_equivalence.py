"""Exact legacy-output and allocation controls for clone optimizations."""

from __future__ import annotations

import itertools
import json
import math
import random
import sqlite3
import sys
import tracemalloc
from collections import Counter, defaultdict
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.db.connection import ensure_schema
from roam.graph import clone_detect as engine
from tests.conftest import make_src_project


def _function(idx, size, file=None, start=1, end=10, bag=None):
    return engine._FuncInfo(
        idx=idx,
        file_path=file or f"file_{idx}.py",
        name=f"function_{idx}",
        qname=f"file_{idx}.py:function_{idx}",
        line_start=start,
        line_end=end,
        node_count=size,
        hash_bag=Counter({size: size}) if bag is None else bag,
    )


def _legacy_candidates(funcs):
    """Frozen pre-optimization enumeration, including first-seen ordering."""
    buckets = defaultdict(list)
    for function in funcs:
        size = function.node_count
        buckets[int(math.log2(size)) if size > 0 else 0].append(function)
    seen = set()
    pairs = []
    for bucket, members in buckets.items():
        candidates = list(members)
        for delta in (-1, 1):
            candidates.extend(buckets.get(bucket + delta, []))
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a, b = candidates[i], candidates[j]
                key = (min(a.idx, b.idx), max(a.idx, b.idx))
                if key in seen:
                    continue
                seen.add(key)
                if a.file_path == b.file_path and a.line_start <= b.line_end and b.line_start <= a.line_end:
                    continue
                pairs.append(key)
    return pairs


def _legacy_cluster_ids(pairs, clusters):
    """The last containing cluster wins, including duplicate qualified names."""
    mapping = {}
    for cluster in clusters:
        names = [member.get("qualified_name") for member in cluster.members]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                mapping[tuple(sorted((names[i], names[j])))] = cluster.cluster_id
    return [mapping.get(tuple(sorted((pair.qname_a, pair.qname_b)))) for pair in pairs]


def _cluster(identity, names):
    return engine.CloneCluster(identity, [{"qualified_name": name} for name in names], 0.9)


def _pair(a, b):
    return engine.ClonePair("a.py", "a", a, 1, 10, "b.py", "b", b, 1, 10, 0.9)


@pytest.fixture
def clone_db():
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        yield conn


@pytest.mark.parametrize("seed", range(40))
def test_candidate_sequence_matches_frozen_enumerator(seed):
    rng = random.Random(seed)
    indices = rng.sample(range(10000), 75)
    funcs = [
        _function(
            idx,
            rng.choice([0, 1, 7, 8, 15, 16, 31, 32, 63, 64, 127, 128, 1024]),
            f"file_{rng.randrange(8)}.py",
            start := rng.randrange(1, 150),
            start + rng.randrange(1, 35),
        )
        for idx in indices
    ]
    assert engine._enumerate_candidate_pairs(funcs) == _legacy_candidates(funcs)


@pytest.mark.parametrize("order", list(itertools.permutations([8, 16, 32, 64])))
def test_bucket_insertion_order_and_two_band_candidates_are_preserved(order):
    funcs = [_function(100 - i, size) for i, size in enumerate(order)]
    assert engine._enumerate_candidate_pairs(funcs) == _legacy_candidates(funcs)


@pytest.mark.parametrize("sizes", [[], [8], [8, 8], [8, 64], [16, 32, 64]])
def test_empty_single_and_gapped_bands(sizes):
    funcs = [_function(i, size) for i, size in enumerate(sizes)]
    assert engine._enumerate_candidate_pairs(funcs) == _legacy_candidates(funcs)


@pytest.mark.parametrize("seed", range(20))
def test_detection_preserves_pair_and_cluster_output(monkeypatch, seed):
    rng = random.Random(seed)
    funcs = [_function(i, rng.choice([8, 16, 32, 64, 128])) for i in range(45)]
    rng.shuffle(funcs)
    monkeypatch.setenv("ROAM_NO_PARALLEL", "1")
    monkeypatch.setattr(engine, "_fetch_candidate_files", lambda *_: [])
    monkeypatch.setattr(engine, "_parallel_extract_func_infos", lambda *_: list(funcs))
    monkeypatch.setattr(engine, "build_clone_fix_hint", lambda *_, **__: None)
    actual = engine.detect_clones(None, min_similarity=0.5)
    monkeypatch.setattr(engine, "_enumerate_candidate_pairs", _legacy_candidates)
    expected = engine.detect_clones(None, min_similarity=0.5)
    assert [[asdict(item) for item in group] for group in actual] == [
        [asdict(item) for item in group] for group in expected
    ]


@pytest.mark.parametrize("seed", range(20))
def test_persisted_cluster_assignment_matches_frozen_mapping(clone_db, seed):
    rng = random.Random(seed)
    names = ["same", "other", "unicode_λ", "", "file:anonymous"]
    clusters = [_cluster(i + 10, rng.choices(names, k=rng.randrange(9))) for i in range(12)]
    pairs = [_pair(a, b) for a in names + ["missing"] for b in names + ["missing"]]
    engine.store_clones(clone_db, pairs, clusters)
    rows = clone_db.execute("SELECT cluster_id FROM clone_pairs ORDER BY id").fetchall()
    assert [row[0] for row in rows] == _legacy_cluster_ids(pairs, clusters)


def test_repeated_names_require_two_occurrences_and_last_matching_cluster_wins(clone_db):
    clusters = [_cluster(20, ["a", "a", "b"]), _cluster(10, ["a", "b"]), _cluster(30, ["a"])]
    pairs = [_pair("a", "a"), _pair("a", "b"), _pair("b", "a"), _pair("b", "b")]
    engine.store_clones(clone_db, pairs, clusters)
    assert [row[0] for row in clone_db.execute("SELECT cluster_id FROM clone_pairs ORDER BY id")] == [20, 10, 10, None]


def test_candidate_bookkeeping_does_not_duplicate_the_quadratic_output():
    funcs = [_function(i, 32) for i in range(600)]
    tracemalloc.start()
    try:
        pairs = engine._enumerate_candidate_pairs(funcs)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    retained = sys.getsizeof(pairs) + sum(sys.getsizeof(pair) for pair in pairs)
    assert len(pairs) == 600 * 599 // 2
    assert peak < retained * 1.25 + 1024 * 1024, (peak, retained)


def test_sparse_persistence_does_not_expand_all_cluster_combinations(clone_db):
    cluster = _cluster(1, [f"name_{i}" for i in range(600)])
    pairs = [_pair("name_0", "name_599"), _pair("missing", "name_1")]
    tracemalloc.start()
    try:
        engine.store_clones(clone_db, pairs, [cluster])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert [row[0] for row in clone_db.execute("SELECT cluster_id FROM clone_pairs ORDER BY id")] == [1, None]
    assert peak < 3 * 1024 * 1024, peak


@pytest.fixture
def same_file_clones(tmp_path, monkeypatch):
    body = (
        "def transform_{i}(values):\n"
        "    total_{i} = 0\n"
        "    for value in values:\n"
        "        if value > {i}:\n"
        "            total_{i} += value\n"
        "    return total_{i}\n"
    )
    project = make_src_project(tmp_path, {"same.py": "\n".join(body.format(i=i) for i in range(5))})
    monkeypatch.chdir(project)
    monkeypatch.setenv("ROAM_NO_PARALLEL", "1")
    result = CliRunner().invoke(cli, ["index"])
    assert result.exit_code == 0, result.output
    return project


def test_fix_hints_walk_each_parsed_file_once(same_file_clones, monkeypatch):
    original = engine._find_function_nodes
    walks = []

    def track(tree):
        walks.append(tree)
        return original(tree)

    monkeypatch.setattr(engine, "_find_function_nodes", track)
    result = CliRunner().invoke(cli, ["--json", "clones"])
    assert result.exit_code == 0, result.output
    # One extraction walk, then one walk of the parsed fix-hint snapshot.
    assert len(walks) == 2, f"Repeated full-file AST walks: {len(walks)}"


def test_cli_envelope_matches_uncached_and_original_enumeration(same_file_clones, monkeypatch):
    def run():
        result = CliRunner().invoke(cli, ["--json", "clones", "--persist"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        payload.pop("_meta", None)
        payload["summary"]["scan"].pop("scanned_at")
        return payload

    optimized = run()
    member_tokens = engine._member_tokens
    monkeypatch.setattr(engine, "_enumerate_candidate_pairs", _legacy_candidates)
    monkeypatch.setattr(engine, "_member_tokens", lambda member, cache, nodes=None: member_tokens(member, cache))
    assert run() == optimized
    assert optimized["summary"]["scan"]["complete"] is True
    assert optimized["summary"]["clone_pairs"] == 10
    assert optimized["clusters"][0]["value"]["fix_hint"]["varying_slots"]


def test_node_lookup_preserves_preorder_ties_and_rebuilds_for_a_replaced_tree(monkeypatch):
    first = SimpleNamespace(start_point=(11, 0), end_point=(19, 0), label="preorder-first")
    second = SimpleNamespace(start_point=(9, 0), end_point=(19, 0), label="lower-line-second")
    tree = SimpleNamespace(nodes=[first, second])
    monkeypatch.setattr(engine, "_find_function_nodes", lambda value: value.nodes)
    monkeypatch.setattr(engine, "_get_function_body", lambda node: node)
    monkeypatch.setattr(engine, "_parameterizable_tokens", lambda node, _: {0: ("identifier", node.label)})
    parse_cache = {"sample.py": (tree, b"sample")}
    nodes = {}
    member = {"file": "sample.py", "line_start": 11, "line_end": 20}
    assert engine._member_tokens(member, parse_cache, nodes) == engine._member_tokens(member, parse_cache)
    assert engine._member_tokens(member, parse_cache, nodes)[0][1] == "preorder-first"
    parse_cache["sample.py"] = (SimpleNamespace(nodes=[second]), b"replacement")
    assert engine._member_tokens(member, parse_cache, nodes)[0][1] == "lower-line-second"
    parse_cache["sample.py"] = (None, None)
    assert engine._member_tokens(member, parse_cache, nodes) is None


def test_node_lookup_preserves_no_match_and_missing_file(monkeypatch):
    tree = SimpleNamespace(nodes=[])
    monkeypatch.setattr(engine, "_find_function_nodes", lambda value: value.nodes)
    cache = {"sample.py": (tree, b"sample")}
    assert engine._member_tokens({"file": "sample.py", "line_start": 7}, cache, {}) is None
    assert engine._member_tokens({"file": ""}, cache, {}) is None
