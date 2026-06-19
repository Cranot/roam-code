"""Contract tests for optional-dependency fallback paths.

The bug class these tests guard: an algorithm passes the happy path
(real library installed) but its ImportError fallback silently violates
the contract documented in the docstring (sum-to-1, return shape,
monotonicity, etc.). The CI session 2026-05-01 caught one such drift —
``compute_pagerank`` and ``personalized_pagerank`` fallbacks returned
unnormalised scores, breaking downstream tests that mixed real and
fallback paths. These tests force the fallback by monkey-patching the
import to raise ImportError, then assert the contract.

Each test runs the fallback once explicitly (so a passing test means
the fallback was actually exercised, not skipped because the optional
lib happened to be installed in the test env).
"""

from __future__ import annotations

import sys
import types
from unittest.mock import patch

import networkx as nx
import pytest

from roam.graph.pagerank import compute_pagerank, personalized_pagerank


def _star(center: int, leaves: list[int]) -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_node(center)
    for leaf in leaves:
        G.add_node(leaf)
        G.add_edge(leaf, center)
    return G


def _line(nodes: list[int]) -> nx.DiGraph:
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n)
    for a, b in zip(nodes, nodes[1:]):
        G.add_edge(a, b)
    return G


class TestComputePagerankFallback:
    """``compute_pagerank`` fallback when scipy isn't installed."""

    def _force_fallback(self):
        """Force the degree-based fallback by making ``_pagerank_core``
        (the roam-owned power iteration both pagerank entry points route
        through) raise ImportError — i.e. simulate numpy/scipy missing."""
        return patch("roam.graph.pagerank._pagerank_core", side_effect=ImportError("scipy missing"))

    def test_fallback_returns_one_score_per_node(self):
        G = _star(0, [1, 2, 3, 4])
        with self._force_fallback():
            scores = compute_pagerank(G)
        assert set(scores.keys()) == set(G.nodes)
        for n, s in scores.items():
            assert isinstance(s, float)
            assert 0.0 <= s <= 1.0, f"node {n} score {s} out of [0,1]"

    def test_fallback_scores_sum_to_one(self):
        """Documented contract: scores ~= 1. The bug we shipped: scores
        summed to len(G) before normalisation."""
        G = _star(0, [1, 2, 3, 4, 5])
        with self._force_fallback():
            scores = compute_pagerank(G)
        assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)

    def test_fallback_preserves_relative_ordering(self):
        """A high-degree node must outrank a leaf even on the fallback."""
        G = _star(0, [1, 2, 3, 4, 5])
        with self._force_fallback():
            scores = compute_pagerank(G)
        center_score = scores[0]
        for leaf in (1, 2, 3, 4, 5):
            assert center_score > scores[leaf], f"center (deg=5) should outrank leaf {leaf} (deg=1)"


class TestPersonalizedPagerankFallback:
    def _force_fallback(self):
        return patch("roam.graph.pagerank._pagerank_core", side_effect=ImportError("scipy missing"))

    def test_fallback_returns_one_score_per_node(self):
        G = _star(0, [1, 2, 3, 4])
        with self._force_fallback():
            scores = personalized_pagerank(G, {1: 1.0})
        assert set(scores.keys()) == set(G.nodes)

    def test_fallback_scores_sum_to_one(self):
        G = _star(0, [1, 2, 3, 4, 5])
        with self._force_fallback():
            scores = personalized_pagerank(G, {1: 1.0})
        assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)

    def test_fallback_seeded_node_outranks_unseeded_peers(self):
        """The whole point of the seed boost — the fallback better honour it."""
        G = _star(0, [1, 2, 3, 4])
        with self._force_fallback():
            scores = personalized_pagerank(G, {1: 1.0})
        # Leaf 1 is seeded, leaves 2/3/4 are structurally identical but
        # unseeded. Seeded must win.
        assert scores[1] > scores[2]
        assert scores[1] > scores[3]
        assert scores[1] > scores[4]

    def test_fallback_consistent_with_compute_pagerank_fallback(self):
        """Both fallbacks must be on the same scale (sum to 1) so callers
        can compare them. The bug we shipped: ``compute_pagerank``
        unnormalised, ``personalized_pagerank`` normalised — chain
        comparisons in tests gave nonsense."""
        unseeded = _line([10, 20, 30, 40, 50])
        seeded = _line([1, 2, 3, 4, 5])
        with self._force_fallback():
            global_pr = compute_pagerank(unseeded)
            seeded_pr = personalized_pagerank(seeded, {1: 1.0})
        # Both sum to 1
        assert sum(global_pr.values()) == pytest.approx(1.0, abs=1e-6)
        assert sum(seeded_pr.values()) == pytest.approx(1.0, abs=1e-6)
        # Seeded head outranks unseeded head — this is the documented
        # value of personalisation.
        assert seeded_pr[1] > global_pr[10]


class TestPersonalizedPagerankAlphaIgnoredInFallback:
    """Document the limitation: alpha doesn't influence the degree-based
    fallback. Tests assert that callers who depend on alpha differentiation
    *must* have scipy installed."""

    def test_different_alphas_can_collide_in_fallback(self):
        G = _star(0, [1, 2, 3, 4])
        # personalized_pagerank routes through _pagerank_core (the roam-owned
        # bounded power iteration), NOT nx.pagerank — so the degree fallback now
        # triggers when _pagerank_core raises ImportError (numpy/scipy genuinely
        # missing). Patch that to exercise the surviving fallback branch.
        with patch("roam.graph.pagerank._pagerank_core", side_effect=ImportError):
            a = personalized_pagerank(G, {1: 1.0}, alpha=0.5)
            b = personalized_pagerank(G, {1: 1.0}, alpha=0.95)
        # In the fallback, alpha is ignored — so the two distributions
        # are identical. This documents the constraint, not a bug.
        # If/when the fallback grows alpha-awareness this test should
        # be inverted.
        assert a == b


class TestSemanticEncoderFallback:
    """``_load_text_encoder`` returns None when optional deps missing."""

    def _reset_encoder_cache(self, semantic):
        semantic._ENCODER_CACHE = None
        semantic._ENCODER_LOAD_FAILED = False

    def _install_fake_semantic_stack(self, monkeypatch, session_factory):
        np_mod = types.ModuleType("numpy")
        ort_mod = types.ModuleType("onnxruntime")
        tokenizers_mod = types.ModuleType("tokenizers")

        ort_mod.InferenceSession = session_factory

        class FakeTokenizer:
            @staticmethod
            def from_file(_path):
                return object()

        tokenizers_mod.Tokenizer = FakeTokenizer

        monkeypatch.setitem(sys.modules, "numpy", np_mod)
        monkeypatch.setitem(sys.modules, "onnxruntime", ort_mod)
        monkeypatch.setitem(sys.modules, "tokenizers", tokenizers_mod)

    def _install_model_files(self, monkeypatch, tmp_path):
        model_dir = tmp_path / "semantic-model"
        model_dir.mkdir()
        (model_dir / "model.onnx").write_bytes(b"fake model")
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("ROAM_SEMANTIC_MODEL_DIR", str(model_dir))

    def test_returns_none_without_onnxruntime(self):
        """Force ImportError on numpy import (the first one) — caller
        must get ``None``, not a crash."""
        from roam.retrieve import semantic

        # Reset the module-level cache so the import gate is re-evaluated.
        self._reset_encoder_cache(semantic)

        original_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name in ("numpy", "onnxruntime", "tokenizers"):
                raise ImportError(f"forced: {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            encoder = semantic._load_text_encoder()
        assert encoder is None

        # Reset for downstream tests
        self._reset_encoder_cache(semantic)

    def test_expected_model_load_error_returns_none(self, monkeypatch, tmp_path):
        from roam.retrieve import semantic

        self._reset_encoder_cache(semantic)
        self._install_model_files(monkeypatch, tmp_path)

        def _bad_session(*_args, **_kwargs):
            raise ValueError("bad ONNX model")

        self._install_fake_semantic_stack(monkeypatch, _bad_session)

        try:
            assert semantic._load_text_encoder() is None
            assert semantic._ENCODER_LOAD_FAILED is True
        finally:
            self._reset_encoder_cache(semantic)

    def test_unexpected_model_load_error_propagates(self, monkeypatch, tmp_path):
        from roam.retrieve import semantic

        self._reset_encoder_cache(semantic)
        self._install_model_files(monkeypatch, tmp_path)

        def _buggy_session(*_args, **_kwargs):
            raise AssertionError("programmer bug")

        self._install_fake_semantic_stack(monkeypatch, _buggy_session)

        try:
            with pytest.raises(AssertionError, match="programmer bug"):
                semantic._load_text_encoder()
            assert semantic._ENCODER_LOAD_FAILED is False
        finally:
            self._reset_encoder_cache(semantic)


class TestLearnedRankerFallback:
    """``learned_ranker.is_available()`` returns False without lightgbm."""

    def test_is_available_false_without_lightgbm(self):
        from roam.retrieve import learned_ranker

        original_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name == "lightgbm":
                raise ImportError("forced")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            assert learned_ranker.is_available() is False

    def test_score_returns_empty_dict_when_unavailable(self):
        """The reranker calls ``score()`` defensively — when lightgbm
        is missing it must return an empty dict so the structural
        blend stays unchanged."""
        from roam.retrieve import learned_ranker

        original_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name == "lightgbm":
                raise ImportError("forced")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            result = learned_ranker.score([{"symbol_id": 1, "fts_score": 1.0}], "task")
        assert result == {} or result is None

    def test_is_available_false_when_model_file_is_invalid(self, tmp_path, monkeypatch):
        """A LightGBM model-load failure is an expected unavailable-model path."""
        from roam.retrieve import learned_ranker

        class FakeLightGBMError(Exception):
            pass

        fake_lightgbm = types.ModuleType("lightgbm")
        fake_lightgbm.basic = types.SimpleNamespace(LightGBMError=FakeLightGBMError)

        def _raise_invalid_model(*args, **kwargs):
            raise FakeLightGBMError("invalid model")

        fake_lightgbm.Booster = _raise_invalid_model
        model_path = tmp_path / "bad-model.lgbm"
        model_path.write_text("not a LightGBM model", encoding="utf-8")

        monkeypatch.setenv("ROAM_LEARNED_MODEL", str(model_path))
        monkeypatch.setitem(sys.modules, "lightgbm", fake_lightgbm)

        assert learned_ranker.is_available() is False

    def test_model_loader_does_not_swallow_unexpected_booster_errors(self, tmp_path, monkeypatch):
        """Unexpected Booster failures should not be hidden as fallback state."""
        from roam.retrieve import learned_ranker

        class FakeLightGBMError(Exception):
            pass

        fake_lightgbm = types.ModuleType("lightgbm")
        fake_lightgbm.basic = types.SimpleNamespace(LightGBMError=FakeLightGBMError)

        def _raise_unexpected_error(*args, **kwargs):
            raise RuntimeError("unexpected booster failure")

        fake_lightgbm.Booster = _raise_unexpected_error
        model_path = tmp_path / "bad-model.lgbm"
        model_path.write_text("not a LightGBM model", encoding="utf-8")

        monkeypatch.setenv("ROAM_LEARNED_MODEL", str(model_path))
        monkeypatch.setitem(sys.modules, "lightgbm", fake_lightgbm)

        with pytest.raises(RuntimeError, match="unexpected booster failure"):
            learned_ranker.is_available()


class TestClustersLeidenFallback:
    """``detect_clusters`` falls back to Louvain when leiden isn't available."""

    def test_returns_partition_for_all_nodes_without_leiden(self):
        """Even without the optional ``leidenalg`` import, every node
        must end up in some cluster."""
        import os

        from roam.graph.clusters import detect_clusters

        G = nx.DiGraph()
        for i in range(8):
            G.add_node(i)
        for i in range(7):
            G.add_edge(i, i + 1)

        # Force the leiden gate off via env var (the documented disable).
        with patch.dict(os.environ, {"ROAM_LEIDEN": "0"}):
            result = detect_clusters(G)
        assert set(result.keys()) == set(G.nodes)
        for n, cid in result.items():
            assert isinstance(cid, int), f"cluster id for {n} not int: {cid!r}"

    def test_leiden_backend_runtime_error_falls_back(self, monkeypatch):
        """Expected optional-backend runtime failures return False."""
        from roam.graph.clusters import _try_leiden_communities

        def _raise_runtime_error(*_args, **_kwargs):
            raise RuntimeError("backend failed")

        fake_igraph = types.SimpleNamespace(Graph=lambda **_kwargs: object(), InternalError=RuntimeError)
        fake_leidenalg = types.SimpleNamespace(
            ModularityVertexPartition=object(),
            find_partition=_raise_runtime_error,
        )
        monkeypatch.delenv("ROAM_LEIDEN", raising=False)
        monkeypatch.setitem(sys.modules, "igraph", fake_igraph)
        monkeypatch.setitem(sys.modules, "leidenalg", fake_leidenalg)

        out = [{99}]
        assert _try_leiden_communities(nx.Graph([(1, 2)]), out) is False
        assert out == []

    def test_leiden_backend_unexpected_exception_propagates(self, monkeypatch):
        """Unexpected non-backend exceptions are not swallowed."""
        from roam.graph.clusters import _try_leiden_communities

        def _raise_key_error(*_args, **_kwargs):
            raise KeyError("logic bug")

        fake_igraph = types.SimpleNamespace(Graph=lambda **_kwargs: object(), InternalError=RuntimeError)
        fake_leidenalg = types.SimpleNamespace(
            ModularityVertexPartition=object(),
            find_partition=_raise_key_error,
        )
        monkeypatch.delenv("ROAM_LEIDEN", raising=False)
        monkeypatch.setitem(sys.modules, "igraph", fake_igraph)
        monkeypatch.setitem(sys.modules, "leidenalg", fake_leidenalg)

        with pytest.raises(KeyError, match="logic bug"):
            _try_leiden_communities(nx.Graph([(1, 2)]), [])
