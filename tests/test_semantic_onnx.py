"""Tests for optional ONNX semantic backend (#56)."""

from __future__ import annotations

import json
import os

import pytest

from roam.search.index_embeddings import (
    build_and_store_onnx_embeddings,
    build_and_store_tfidf,
    search_stored,
)


class _StubEmbedder:
    model_id = "stub-onnx"

    def embed_texts(self, texts, batch_size=32):  # noqa: ARG002 - shape-only stub
        vectors = []
        for text in texts:
            lower = (text or "").lower()
            vectors.append([
                1.0 if "database" in lower else 0.0,
                1.0 if "connection" in lower else 0.0,
                1.0 if "auth" in lower else 0.0,
                1.0,
            ])
        return vectors


@pytest.fixture
def onnx_project(project_factory):
    return project_factory({
        "db/connection.py": (
            "def open_database():\n"
            "    '''Open a database connection.'''\n"
            "    pass\n"
            "def close_database():\n"
            "    '''Close a database connection.'''\n"
            "    pass\n"
        ),
        "auth/login.py": (
            "def authenticate_user(username, password):\n"
            "    pass\n"
        ),
    })


def _patch_stub_backend(monkeypatch):
    import roam.search.index_embeddings as ie

    monkeypatch.setattr(
        ie,
        "_load_semantic_settings",
        lambda project_root=None: {
            "semantic_backend": "onnx",
            "onnx_model_path": "stub.onnx",
            "onnx_tokenizer_path": "tokenizer.json",
            "onnx_max_length": 256,
        },
    )
    monkeypatch.setattr(
        ie,
        "_onnx_ready",
        lambda project_root=None, settings=None: (True, "ok", settings or {}),
    )
    monkeypatch.setattr(
        ie,
        "_get_onnx_embedder",
        lambda project_root=None, settings=None: _StubEmbedder(),
    )


def test_build_and_store_onnx_embeddings(monkeypatch, onnx_project):
    """ONNX vector builder should persist dense vectors to symbol_embeddings."""
    from roam.db.connection import open_db

    _patch_stub_backend(monkeypatch)

    with open_db(readonly=False, project_root=onnx_project) as conn:
        stats = build_and_store_onnx_embeddings(conn, project_root=onnx_project)
        count = conn.execute(
            "SELECT COUNT(*) FROM symbol_embeddings WHERE provider='onnx'"
        ).fetchone()[0]

    assert stats.get("enabled") is True
    assert stats.get("stored", 0) > 0
    assert count > 0


def test_search_stored_with_onnx_backend(monkeypatch, onnx_project):
    """Explicit ONNX backend should return dense-similarity matches."""
    from roam.db.connection import open_db

    _patch_stub_backend(monkeypatch)

    with open_db(readonly=False, project_root=onnx_project) as conn:
        build_and_store_onnx_embeddings(conn, project_root=onnx_project)
        conn.commit()

    with open_db(readonly=True, project_root=onnx_project) as conn:
        results = search_stored(
            conn,
            "database connection",
            top_k=5,
            include_packs=False,
            semantic_backend="onnx",
            project_root=onnx_project,
        )

    assert results
    assert results[0]["name"] in {"open_database", "close_database"}
    assert all(0.0 < r["score"] <= 1.0 for r in results)


def test_search_stored_auto_falls_back_to_tfidf(monkeypatch, onnx_project):
    """When ONNX is unavailable, auto backend should still use TF-IDF."""
    from roam.db.connection import open_db
    import roam.search.index_embeddings as ie

    monkeypatch.setattr(
        ie,
        "_onnx_ready",
        lambda project_root=None, settings=None: (False, "not-configured", settings or {}),
    )

    with open_db(readonly=False, project_root=onnx_project) as conn:
        build_and_store_tfidf(conn)
        conn.commit()

    with open_db(readonly=True, project_root=onnx_project) as conn:
        results = search_stored(
            conn,
            "database connection",
            top_k=5,
            include_packs=False,
            semantic_backend="auto",
            project_root=onnx_project,
        )

    assert results
    assert any(r["name"] in {"open_database", "close_database"} for r in results[:3])


def test_config_semantic_options(cli_runner, tmp_path):
    """`roam config` should persist ONNX semantic settings."""
    from roam.cli import cli

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    (repo / "app.py").write_text("def main():\n    return 1\n")

    old_cwd = os.getcwd()
    try:
        os.chdir(repo)
        result = cli_runner.invoke(
            cli,
            [
                "config",
                "--semantic-backend",
                "onnx",
                "--set-onnx-model",
                "models/model.onnx",
                "--set-onnx-tokenizer",
                "models/tokenizer.json",
                "--set-onnx-max-length",
                "4096",
            ],
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0
    cfg = json.loads((repo / ".roam" / "config.json").read_text(encoding="utf-8"))
    assert cfg["semantic_backend"] == "onnx"
    assert cfg["onnx_model_path"] == "models/model.onnx"
    assert cfg["onnx_tokenizer_path"] == "models/tokenizer.json"
    assert cfg["onnx_max_length"] == 1024  # clamped
