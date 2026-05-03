"""Smoke tests for v12.2 additions.

One file per feature would be ideal but for shipping speed we batch the
contracts into a single suite. Each test class targets one v12.2 module:

* ``TestZetaWiring``           — retrieve/semantic.py, rerank.py
* ``TestAibomExtension``       — security/aibom_extension.py
* ``TestCgaAibomPredicate``    — attest/cga.py CodeGraph-AIBOM/v1 promote
* ``TestLockMgrSemantics``     — runtime/lockmgr.py
* ``TestDaemonScaffold``       — runtime/daemon.py status_summary
* ``TestLearnedRankerHook``    — retrieve/learned_ranker.py degrade path
* ``TestLeidenFallback``       — graph/clusters.py algorithm preference
* ``TestGraphBackendDispatch`` — runtime/graph_backend.py active_backend
"""

from __future__ import annotations

import json
import os
import threading
import time

import networkx as nx

# ---------------------------------------------------------------------------
# ζ semantic signal — wired but dormant without [semantic] extras
# ---------------------------------------------------------------------------


class TestZetaWiring:
    def test_default_weights_include_zeta(self):
        from roam.config import DEFAULT_RETRIEVE_WEIGHTS

        assert "zeta" in DEFAULT_RETRIEVE_WEIGHTS
        assert 0.0 < DEFAULT_RETRIEVE_WEIGHTS["zeta"] <= 0.5

    def test_semantic_score_returns_empty_without_table(self, tmp_path):
        import sqlite3

        from roam.retrieve.semantic import has_symbol_embeddings, semantic_score

        db = tmp_path / "blank.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE foo (x INTEGER)")
        conn.commit()
        assert has_symbol_embeddings(conn) is False
        assert semantic_score(conn, [1, 2, 3], "any task") == {}

    def test_semantic_score_empty_query_returns_empty(self, tmp_path):
        import sqlite3

        from roam.retrieve.semantic import semantic_score

        conn = sqlite3.connect(":memory:")
        assert semantic_score(conn, [1, 2], "") == {}
        assert semantic_score(conn, [1, 2], "   ") == {}

    def test_semantic_score_reads_canonical_embedding_schema(self, monkeypatch, tmp_path):
        import sqlite3

        from roam.retrieve import semantic

        conn = sqlite3.connect(str(tmp_path / "semantic.db"))
        conn.execute(
            "CREATE TABLE symbol_embeddings ("
            "symbol_id INTEGER PRIMARY KEY, "
            "vector TEXT NOT NULL, "
            "dims INTEGER NOT NULL, "
            "provider TEXT NOT NULL DEFAULT 'onnx')"
        )
        conn.execute(
            "INSERT INTO symbol_embeddings(symbol_id, vector, dims, provider) VALUES (?, ?, ?, ?)",
            (1, json.dumps([1.0, 0.0]), 2, "onnx"),
        )
        conn.execute(
            "INSERT INTO symbol_embeddings(symbol_id, vector, dims, provider) VALUES (?, ?, ?, ?)",
            (2, json.dumps([0.0, 1.0]), 2, "onnx"),
        )
        conn.commit()

        monkeypatch.setattr(semantic, "_load_text_encoder", lambda: (lambda _text: [1.0, 0.0]))

        scores = semantic.semantic_score(conn, [1, 2], "database connection")
        assert scores[1] > scores[2]
        assert scores[1] == 1.0

    def test_semantic_coverage_reports_empty_and_partial_states(self, tmp_path):
        import sqlite3

        from roam.retrieve.semantic import semantic_coverage

        conn = sqlite3.connect(str(tmp_path / "coverage.db"))
        conn.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE symbol_embeddings ("
            "symbol_id INTEGER PRIMARY KEY, "
            "vector TEXT NOT NULL, "
            "dims INTEGER NOT NULL, "
            "provider TEXT NOT NULL DEFAULT 'onnx')"
        )
        conn.executemany("INSERT INTO symbols(id) VALUES (?)", [(1,), (2,)])

        empty = semantic_coverage(conn)
        assert empty["status"] == "empty"
        assert empty["coverage_pct"] == 0.0

        conn.execute(
            "INSERT INTO symbol_embeddings(symbol_id, vector, dims, provider) VALUES (?, ?, ?, ?)",
            (1, json.dumps([1.0, 0.0]), 2, "onnx"),
        )
        partial = semantic_coverage(conn)
        assert partial["status"] == "partial"
        assert partial["embeddings"] == 1
        assert partial["coverage_pct"] == 50.0


# ---------------------------------------------------------------------------
# AIBOM extension — committer mining
# ---------------------------------------------------------------------------


class TestAibomExtension:
    def test_email_pattern_detects_anthropic(self):
        from roam.security.aibom_extension import _looks_like_ai_committer

        assert _looks_like_ai_committer("noreply@anthropic.com", "")
        assert _looks_like_ai_committer("user@example.com", "Co-authored-by Claude")
        assert not _looks_like_ai_committer("alice@example.com", "fix typo")

    def test_trailer_extraction(self):
        from roam.security.aibom_extension import _extract_ai_trailers

        message = (
            "Add new feature\n\n"
            "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>\n"
            "Co-Authored-By: Alice <alice@example.com>\n"
        )
        out = _extract_ai_trailers(message)
        # Only the AI trailer should match; Alice is human.
        assert len(out) == 1
        assert out[0]["email"] == "noreply@anthropic.com"

    def test_vendor_inference(self):
        from roam.security.aibom_extension import _vendor_for_email

        assert _vendor_for_email("user@anthropic.com") == "Anthropic"
        assert _vendor_for_email("user@openai.com") == "OpenAI"
        assert _vendor_for_email("alice@example.com") == "Unknown"


# ---------------------------------------------------------------------------
# CGA + AIBOM predicate fusion
# ---------------------------------------------------------------------------


class TestCgaAibomPredicate:
    def test_predicate_type_constant(self):
        from roam.attest.cga import PREDICATE_TYPE, PREDICATE_TYPE_AIBOM

        assert PREDICATE_TYPE == "https://roam-code.dev/CodeGraph/v1"
        assert PREDICATE_TYPE_AIBOM == "https://roam-code.dev/CodeGraph-AIBOM/v1"

    def test_verify_accepts_both_predicate_types(self):
        """The verifier must accept either predicate type. Smoke-check
        the predicate-type guard logic by reading the source — full
        round-trip is exercised in tests/test_cga.py against an indexed
        DB. The contract asserted here: both constants are wired in.
        """
        import inspect

        from roam.attest import cga

        src = inspect.getsource(cga.verify_cga_statement)
        # The verifier must reference the AIBOM predicate type by name.
        assert "PREDICATE_TYPE_AIBOM" in src
        assert "predicateType" in src


# ---------------------------------------------------------------------------
# LockMgr — read/write/exclusive semantics
# ---------------------------------------------------------------------------


class TestLockMgrSemantics:
    def test_concurrent_readers_pass_through(self):
        from roam.runtime.lockmgr import LockMgr

        mgr = LockMgr()
        readers_seen = []

        def reader(idx):
            with mgr.acquire("read", timeout=2):
                readers_seen.append(idx)
                time.sleep(0.05)

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # All 5 readers should have made it through within 2 seconds.
        assert len(readers_seen) == 5

    def test_write_blocks_other_writes(self):
        from roam.runtime.lockmgr import LockMgr

        mgr = LockMgr()
        events = []

        def writer(name, hold_s):
            with mgr.acquire("write", timeout=5):
                events.append(("start", name))
                time.sleep(hold_s)
                events.append(("end", name))

        a = threading.Thread(target=writer, args=("A", 0.1))
        b = threading.Thread(target=writer, args=("B", 0.1))
        a.start()
        time.sleep(0.02)  # give A a head start
        b.start()
        a.join()
        b.join()
        # The events should be ordered: A start, A end, then B start, B end.
        assert events.index(("end", "A")) < events.index(("start", "B"))

    def test_default_lockmgr_is_singleton(self):
        from roam.runtime.lockmgr import default_lockmgr

        assert default_lockmgr() is default_lockmgr()


# ---------------------------------------------------------------------------
# Daemon scaffold — Phase 1 surfaces
# ---------------------------------------------------------------------------


class TestDaemonScaffold:
    def test_status_summary_when_not_running(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from roam.runtime.daemon import status_summary

        out = status_summary()
        assert out["running"] is False
        assert "phase" in out
        assert "scaffold" in out["phase"].lower()
        assert "pid_file" in out

    def test_lookup_mode_default_is_exclusive(self):
        from roam.runtime.lock_modes import DEFAULT_MODE, lookup_mode

        # Unknown commands escalate to exclusive — safe-by-default.
        assert lookup_mode("definitely-not-a-command") == DEFAULT_MODE
        assert DEFAULT_MODE == "exclusive"

    def test_pilots_registered_for_phase_1(self):
        from roam.runtime.lock_modes import lookup_mode

        # 5 read pilots
        for cmd in ("preflight", "retrieve", "critique", "context", "uses"):
            assert lookup_mode(cmd) == "read"
        # 5 write/exclusive pilots
        assert lookup_mode("index") == "exclusive"
        assert lookup_mode("init") == "exclusive"
        assert lookup_mode("clones") == "write"

    def test_acquire_lock_for_command_falls_back_to_local(self, tmp_path, monkeypatch):
        """Without a running daemon, the helper still returns a working
        context manager via the process-local LockMgr."""
        monkeypatch.chdir(tmp_path)
        from roam.runtime.daemon import acquire_lock_for_command

        with acquire_lock_for_command("preflight"):
            pass  # smoke — no exception is the test


# ---------------------------------------------------------------------------
# Learned ranker hook — degrades cleanly without LightGBM / model file
# ---------------------------------------------------------------------------


class TestLearnedRankerHook:
    def test_score_empty_when_no_model(self):
        from roam.retrieve.learned_ranker import score

        # Without ROAM_LEARNED_MODEL or LightGBM, score is empty.
        candidates = [{"symbol_id": 1, "name": "foo"}]
        os.environ.pop("ROAM_LEARNED_MODEL", None)
        assert score(candidates, "find foo") == {}

    def test_is_available_returns_false_by_default(self):
        from roam.retrieve.learned_ranker import is_available

        assert is_available() is False

    def test_feature_names_count_is_22(self):
        from roam.retrieve.learned_ranker import feature_names

        # The agent's recommendation is a 22-feature vector; this test
        # locks the contract so adding/removing features without bumping
        # the model fails CI loudly.
        assert len(feature_names()) == 22


# ---------------------------------------------------------------------------
# Leiden algorithm — preference order is correct, fallback works offline
# ---------------------------------------------------------------------------


class TestLeidenFallback:
    def test_clusters_detected_without_leiden_extras(self, monkeypatch):
        """When [leiden] extras aren't installed, NetworkX Louvain takes
        over and still returns a valid clustering."""
        monkeypatch.setenv("ROAM_LEIDEN", "0")  # forced disable
        from roam.graph.clusters import detect_clusters

        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (2, 3), (3, 1), (4, 5), (5, 6), (6, 4), (2, 5)])
        clusters = detect_clusters(G)
        # Two triangles + a bridge — at least 1 cluster, all 6 nodes mapped.
        assert set(clusters.keys()) == {1, 2, 3, 4, 5, 6}
        assert len(set(clusters.values())) >= 1

    def test_try_leiden_returns_false_when_disabled(self, monkeypatch):
        from roam.graph.clusters import _try_leiden_communities

        monkeypatch.setenv("ROAM_LEIDEN", "0")
        out: list = []
        assert _try_leiden_communities(nx.Graph(), out) is False
        assert out == []


# ---------------------------------------------------------------------------
# Graph backend dispatcher — env switch + fallback
# ---------------------------------------------------------------------------


class TestGraphBackendDispatch:
    def test_active_backend_default_is_networkx(self, monkeypatch):
        # Force NetworkX to test the default path even if rustworkx is around.
        monkeypatch.setenv("ROAM_GRAPH_BACKEND", "networkx")
        from roam.runtime.graph_backend import active_backend

        assert active_backend() == "networkx"

    def test_pagerank_dispatches(self, monkeypatch):
        """Both backends must produce a {node: float} dict with all nodes."""
        monkeypatch.setenv("ROAM_GRAPH_BACKEND", "networkx")
        from roam.runtime.graph_backend import pagerank

        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (2, 3), (3, 1)])
        scores = pagerank(G, alpha=0.85)
        assert set(scores.keys()) == {1, 2, 3}
        assert all(isinstance(v, float) for v in scores.values())

    def test_pagerank_empty_graph_returns_empty(self):
        from roam.runtime.graph_backend import pagerank

        assert pagerank(nx.DiGraph()) == {}
