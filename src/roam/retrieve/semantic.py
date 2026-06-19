"""Semantic similarity backend for the v12.2 ζ retrieve signal.

Activation chain:

1. Optional ``[semantic]`` extras: ``onnxruntime`` + ``tokenizers`` + a
   tiny code embedding model (bge-small-en-v1.5 INT8 ONNX, ~50MB).
2. Optional ``[graph-fast]`` / ``[semantic]`` adjacent: ``sqlite-vec``
   loadable extension for ANN over the persisted embedding table.
3. Indexer-populated ``symbol_embeddings`` table (JSON ``vector`` +
   ``dims`` columns, shared with ``roam search-semantic``).

When any link in the chain is missing, ``semantic_score`` returns an
empty dict — the reranker's ζ contribution is then 0 for every
candidate and the v12.0/12.1 blend is preserved exactly.

This file ships the retrieve-side reader for the same embedding table used
by ``roam search-semantic``. Missing optional dependencies or vectors still
degrade to an empty score map so the structural blend remains deterministic.
"""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Iterable, Sequence


def has_symbol_embeddings(conn: sqlite3.Connection) -> bool:
    """Return ``True`` when the embeddings table exists and is non-empty."""
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_embeddings'").fetchone()
        if not row:
            return False
        row = conn.execute("SELECT 1 FROM symbol_embeddings LIMIT 1").fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def semantic_coverage(conn: sqlite3.Connection) -> dict[str, int | float | str | bool]:
    """Return dense-embedding coverage diagnostics for retrieve/rerank.

    The ζ rerank signal only contributes when candidate symbols have dense
    vectors. This helper makes that state explicit for CLI/MCP consumers.
    """
    try:
        symbol_count = int(conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0])
    except sqlite3.OperationalError:
        symbol_count = 0

    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN provider = 'onnx' THEN 1 ELSE 0 END) AS onnx "
            "FROM symbol_embeddings"
        ).fetchone()
    except sqlite3.OperationalError:
        return {
            "status": "missing-table",
            "ready": False,
            "symbols": symbol_count,
            "embeddings": 0,
            "onnx_embeddings": 0,
            "coverage_pct": 0.0,
        }

    if isinstance(row, sqlite3.Row):
        embedding_count = int(row["total"] or 0)
        onnx_count = int(row["onnx"] or 0)
    else:
        embedding_count = int(row[0] or 0)
        onnx_count = int(row[1] or 0)
    coverage_pct = round((embedding_count * 100.0 / symbol_count), 1) if symbol_count else 0.0
    if embedding_count == 0:
        status = "empty"
    elif embedding_count < symbol_count:
        status = "partial"
    else:
        status = "ready"
    return {
        "status": status,
        "ready": embedding_count > 0,
        "symbols": symbol_count,
        "embeddings": embedding_count,
        "onnx_embeddings": onnx_count,
        "coverage_pct": coverage_pct,
    }


def semantic_score(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[int],
    task: str,
) -> dict[int, float]:
    """Per-candidate semantic-similarity score in [0, 1].

    Returns an empty dict when:

    * The ``symbol_embeddings`` table is absent or empty.
    * The optional ``[semantic]`` extras (onnxruntime + tokenizers) aren't
      importable.
    * The task is empty.

    When all three preconditions hold, computes cosine similarity between
    the task embedding and each candidate's stored vector. Skips the ANN
    layer (sqlite-vec) for the v12.2 MVP since the candidate set is
    already bounded by the FTS5 first stage — brute-force cosine over
    ≤200 candidates is sub-millisecond on CPU.
    """
    if not task or not task.strip():
        return {}
    candidate_set = list(candidate_ids)
    if not candidate_set:
        return {}
    if not has_symbol_embeddings(conn):
        return {}

    encoder = _load_text_encoder()
    if encoder is None:
        return {}

    try:
        query_vec = encoder(task)
    except Exception:
        return {}
    if query_vec is None:
        return {}

    out: dict[int, float] = {}
    for sym_id, raw_vector, dims in _candidate_embedding_rows(conn, candidate_set):
        vec = _decode_vector(raw_vector, dims)
        score = _cosine_score(query_vec, vec)
        if score > 0:
            out[sym_id] = score
    return out


def _candidate_embedding_rows(
    conn: sqlite3.Connection,
    candidate_ids: Sequence[int],
) -> list[tuple[int, str, int]]:
    """Return stored vectors for candidate symbols using the canonical schema."""
    if not candidate_ids:
        return []
    # The canonical schema is shared with ``roam.search.index_embeddings``:
    #   symbol_embeddings(symbol_id INTEGER PRIMARY KEY, vector TEXT, dims INTEGER, provider TEXT, ...)
    placeholders = ",".join("?" * len(candidate_ids))
    try:
        rows = conn.execute(
            f"SELECT symbol_id, vector, dims FROM symbol_embeddings WHERE symbol_id IN ({placeholders})",
            list(candidate_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(int(row[0]), row[1], int(row[2] or 0)) for row in rows]


def _decode_vector(raw_vector: str, dims: int) -> list[float] | None:
    """Decode the JSON vector persisted by ``search.index_embeddings``."""
    if not raw_vector or dims <= 0:
        return None
    try:
        decoded = json.loads(raw_vector)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, list):
        return None
    try:
        return [float(v) for v in decoded[:dims]]
    except (TypeError, ValueError):
        return None


def _cosine_score(query_vec: Sequence[float], vec: Sequence[float] | None) -> float:
    """Cosine similarity rescaled from [-1, 1] to [0, 1]."""
    if vec is None or len(vec) != len(query_vec):
        return 0.0
    q_norm = math.sqrt(sum(v * v for v in query_vec)) or 1.0
    v_norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    dot = sum(a * b for a, b in zip(query_vec, vec))
    cos = dot / (q_norm * v_norm)
    return max(0.0, (cos + 1.0) / 2.0)


# ---------------------------------------------------------------------------
# Encoder loader — module-level singleton, lazy on first use
# ---------------------------------------------------------------------------


_ENCODER_CACHE: object | None = None
_ENCODER_LOAD_FAILED = False

_ONNX_RUNTIME_ERROR_NAMES = (
    "Fail",
    "InvalidArgument",
    "InvalidGraph",
    "InvalidProtobuf",
    "NoSuchFile",
    "RuntimeException",
    "EPFail",
    "NotImplemented",
)


def _encoder_load_error_types(ort: object) -> tuple[type[BaseException], ...]:
    """Return expected optional-backend failures for model/tokenizer loading."""
    errors: list[type[BaseException]] = [OSError, ValueError, RuntimeError]
    state = getattr(getattr(ort, "capi", None), "onnxruntime_pybind11_state", None)
    for name in _ONNX_RUNTIME_ERROR_NAMES:
        exc_type = getattr(state, name, None)
        if isinstance(exc_type, type) and issubclass(exc_type, BaseException):
            errors.append(exc_type)
    return tuple(errors)


def _load_text_encoder():
    """Return a callable ``str -> list[float]`` or ``None`` when unavailable.

    Production path uses bge-small-en-v1.5 INT8 ONNX (~50MB) via
    onnxruntime + tokenizers. The model + tokenizer files are looked up
    in ``$ROAM_SEMANTIC_MODEL_DIR`` (default ``~/.cache/roam/bge-small-en-v1.5-int8``).

    Returns ``None`` when:
    * The optional packages aren't installed.
    * The model files aren't present.
    * The model/session/tokenizer load hits an expected backend error.

    Unexpected defects still raise. Documented optional-backend failures
    return ``None`` so the reranker sees an empty score dict and the
    original blend stays consistent.
    """
    global _ENCODER_CACHE, _ENCODER_LOAD_FAILED
    if _ENCODER_CACHE is not None:
        return _ENCODER_CACHE
    if _ENCODER_LOAD_FAILED:
        return None
    try:
        import os
        from pathlib import Path as _Path

        import numpy as np
        import onnxruntime as ort  # type: ignore
        from tokenizers import Tokenizer  # type: ignore
    except ImportError:
        _ENCODER_LOAD_FAILED = True
        return None

    model_dir = os.environ.get("ROAM_SEMANTIC_MODEL_DIR")
    if model_dir:
        path = _Path(model_dir)
    else:
        path = _Path.home() / ".cache" / "roam" / "bge-small-en-v1.5-int8"
    model_path = path / "model.onnx"
    tokenizer_path = path / "tokenizer.json"
    if not model_path.is_file() or not tokenizer_path.is_file():
        _ENCODER_LOAD_FAILED = True
        return None

    try:
        sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
    except _encoder_load_error_types(ort):
        _ENCODER_LOAD_FAILED = True
        return None

    def _encode(text: str) -> list[float] | None:
        enc = tokenizer.encode(text)
        ids = np.array([enc.ids], dtype=np.int64)
        mask = np.array([enc.attention_mask], dtype=np.int64)
        try:
            outputs = sess.run(
                None,
                {"input_ids": ids, "attention_mask": mask},
            )
        except Exception:
            return None
        # First output is typically the last_hidden_state; mean-pool.
        last_hidden = outputs[0][0]
        # Mask-aware mean pooling
        m = mask[0].astype(np.float32)[..., None]
        pooled = (last_hidden * m).sum(axis=0) / max(m.sum(), 1.0)
        norm = float(np.linalg.norm(pooled)) or 1.0
        return [float(x) / norm for x in pooled.tolist()]

    _ENCODER_CACHE = _encode
    return _encode
