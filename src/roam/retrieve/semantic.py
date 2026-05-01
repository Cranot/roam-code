"""Semantic similarity backend for the v12.2 ζ retrieve signal.

Activation chain:

1. Optional ``[semantic]`` extras: ``onnxruntime`` + ``tokenizers`` + a
   tiny code embedding model (bge-small-en-v1.5 INT8 ONNX, ~50MB).
2. Optional ``[graph-fast]`` / ``[semantic]`` adjacent: ``sqlite-vec``
   loadable extension for ANN over the persisted embedding table.
3. Indexer-populated ``symbol_embeddings`` table (BLOB column with
   384-d float32 vectors).

When any link in the chain is missing, ``semantic_score`` returns an
empty dict — the reranker's ζ contribution is then 0 for every
candidate and the v12.0/12.1 blend is preserved exactly.

This file ships the **wiring + interface** so the weight, query plan, and
fallback path are stable. Populating the embeddings table happens in a
follow-up indexer pass (`roam index --semantic`) which lands in v12.3.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable


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

    # Pull candidate vectors. The table schema is:
    #   symbol_embeddings(symbol_id INTEGER PRIMARY KEY, vec BLOB, dim INTEGER)
    placeholders = ",".join("?" * len(candidate_set))
    try:
        rows = conn.execute(
            f"SELECT symbol_id, vec, dim FROM symbol_embeddings WHERE symbol_id IN ({placeholders})",
            candidate_set,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    out: dict[int, float] = {}
    import math
    import struct

    q_norm = math.sqrt(sum(v * v for v in query_vec)) or 1.0
    for row in rows:
        sym_id, blob, dim = int(row[0]), row[1], int(row[2] or 0)
        if not blob or dim <= 0:
            continue
        try:
            vec = struct.unpack(f"{dim}f", blob[: 4 * dim])
        except struct.error:
            continue
        if len(vec) != len(query_vec):
            continue
        dot = sum(a * b for a, b in zip(query_vec, vec))
        v_norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        cos = dot / (q_norm * v_norm)
        # Cosine in [-1, 1] → clip to [0, 1] then linear-rescale.
        score = max(0.0, (cos + 1.0) / 2.0)
        if score > 0:
            out[sym_id] = score
    return out


# ---------------------------------------------------------------------------
# Encoder loader — module-level singleton, lazy on first use
# ---------------------------------------------------------------------------


_ENCODER_CACHE: object | None = None
_ENCODER_LOAD_FAILED = False


def _load_text_encoder():
    """Return a callable ``str -> list[float]`` or ``None`` when unavailable.

    Production path uses bge-small-en-v1.5 INT8 ONNX (~50MB) via
    onnxruntime + tokenizers. The model + tokenizer files are looked up
    in ``$ROAM_SEMANTIC_MODEL_DIR`` (default ``~/.cache/roam/bge-small-en-v1.5-int8``).

    Returns ``None`` when:
    * The optional packages aren't installed.
    * The model files aren't present.
    * Loading fails for any reason.

    This module never raises — the reranker always sees an empty score
    dict on failure and the original blend stays consistent.
    """
    global _ENCODER_CACHE, _ENCODER_LOAD_FAILED
    if _ENCODER_CACHE is not None:
        return _ENCODER_CACHE
    if _ENCODER_LOAD_FAILED:
        return None
    try:
        import os
        from pathlib import Path as _Path

        import numpy as np  # noqa: F401  required by onnxruntime
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
    except Exception:
        _ENCODER_LOAD_FAILED = True
        return None

    def _encode(text: str) -> list[float] | None:
        import numpy as _np

        enc = tokenizer.encode(text)
        ids = _np.array([enc.ids], dtype=_np.int64)
        mask = _np.array([enc.attention_mask], dtype=_np.int64)
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
        m = mask[0].astype(_np.float32)[..., None]
        pooled = (last_hidden * m).sum(axis=0) / max(m.sum(), 1.0)
        norm = float(_np.linalg.norm(pooled)) or 1.0
        return [float(x) / norm for x in pooled.tolist()]

    _ENCODER_CACHE = _encode
    return _encode
