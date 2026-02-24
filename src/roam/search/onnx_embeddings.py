"""Optional local ONNX embedding backend for semantic search (#56)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from roam.db.connection import _load_project_config, find_project_root

_VALID_BACKENDS = {"auto", "tfidf", "onnx", "hybrid"}
_EMBEDDER_CACHE: dict[tuple[str, str, int], "OnnxEmbedder"] = {}


def _parse_max_length(value: Any, default: int = 256) -> int:
    """Parse and clamp max sequence length."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(16, min(parsed, 1024))


def load_semantic_settings(project_root: Path | None = None) -> dict[str, Any]:
    """Load semantic backend settings from config + env vars (env wins)."""
    if project_root is None:
        project_root = find_project_root()

    config = _load_project_config(project_root)
    backend = str(config.get("semantic_backend", "auto") or "auto").strip().lower()
    if backend not in _VALID_BACKENDS:
        backend = "auto"

    model_path = config.get("onnx_model_path")
    tokenizer_path = config.get("onnx_tokenizer_path")
    max_length = _parse_max_length(config.get("onnx_max_length", 256))

    env_backend = os.getenv("ROAM_SEMANTIC_BACKEND")
    if env_backend:
        env_backend = env_backend.strip().lower()
        if env_backend in _VALID_BACKENDS:
            backend = env_backend

    env_model = os.getenv("ROAM_ONNX_MODEL_PATH")
    if env_model:
        model_path = env_model

    env_tokenizer = os.getenv("ROAM_ONNX_TOKENIZER_PATH")
    if env_tokenizer:
        tokenizer_path = env_tokenizer

    env_max_len = os.getenv("ROAM_ONNX_MAX_LENGTH")
    if env_max_len:
        max_length = _parse_max_length(env_max_len, default=max_length)

    return {
        "semantic_backend": backend,
        "onnx_model_path": str(model_path) if model_path else "",
        "onnx_tokenizer_path": str(tokenizer_path) if tokenizer_path else "",
        "onnx_max_length": max_length,
    }


def _import_onnx_stack():
    """Import optional ONNX dependencies lazily."""
    try:
        import numpy as np  # type: ignore
        import onnxruntime as ort  # type: ignore
        from tokenizers import Tokenizer  # type: ignore
    except Exception as exc:
        return None, None, None, str(exc)
    return np, ort, Tokenizer, ""


def onnx_dependencies_available() -> tuple[bool, str]:
    """Return whether ONNX stack is importable."""
    np_mod, ort_mod, tok_cls, err = _import_onnx_stack()
    if np_mod is None or ort_mod is None or tok_cls is None:
        return False, err or "missing ONNX dependencies"
    return True, "ok"


def onnx_ready(
    project_root: Path | None = None,
    settings: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Return readiness status for ONNX embedding backend."""
    if settings is None:
        settings = load_semantic_settings(project_root=project_root)

    backend = settings.get("semantic_backend", "auto")
    if backend not in {"auto", "onnx", "hybrid"}:
        return False, "backend-disabled", settings

    model_path = Path(settings.get("onnx_model_path") or "")
    tokenizer_path = Path(settings.get("onnx_tokenizer_path") or "")
    if not model_path or not tokenizer_path:
        return False, "missing-model-or-tokenizer", settings
    if not model_path.exists():
        return False, f"model-not-found:{model_path}", settings
    if not tokenizer_path.exists():
        return False, f"tokenizer-not-found:{tokenizer_path}", settings

    ok, reason = onnx_dependencies_available()
    if not ok:
        return False, reason, settings

    return True, "ok", settings


class OnnxEmbedder:
    """Text embedder backed by local ONNX model + tokenizer.json."""

    def __init__(self, model_path: str, tokenizer_path: str, max_length: int = 256):
        np_mod, ort_mod, tok_cls, err = _import_onnx_stack()
        if np_mod is None or ort_mod is None or tok_cls is None:
            raise RuntimeError(f"ONNX backend unavailable: {err}")

        self.np = np_mod
        self.ort = ort_mod
        self.tokenizer = tok_cls.from_file(str(tokenizer_path))
        self.max_length = _parse_max_length(max_length)
        self.model_path = str(model_path)
        self.tokenizer_path = str(tokenizer_path)
        self.model_id = Path(model_path).stem
        self.session = self.ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        self.input_names = [item.name for item in self.session.get_inputs()]

    def _prepare_inputs(self, texts: list[str]):
        encoded = self.tokenizer.encode_batch(texts)
        ids_rows: list[list[int]] = []
        mask_rows: list[list[int]] = []
        type_rows: list[list[int]] = []

        for row in encoded:
            ids = list(row.ids[: self.max_length])
            mask = [1] * len(ids)
            type_ids = list(row.type_ids[: self.max_length]) if row.type_ids else []
            if not type_ids:
                type_ids = [0] * len(ids)
            if len(type_ids) < len(ids):
                type_ids.extend([0] * (len(ids) - len(type_ids)))

            pad = self.max_length - len(ids)
            if pad > 0:
                ids.extend([0] * pad)
                mask.extend([0] * pad)
                type_ids.extend([0] * pad)

            ids_rows.append(ids)
            mask_rows.append(mask)
            type_rows.append(type_ids)

        ids_arr = self.np.asarray(ids_rows, dtype=self.np.int64)
        mask_arr = self.np.asarray(mask_rows, dtype=self.np.int64)
        type_arr = self.np.asarray(type_rows, dtype=self.np.int64)

        feeds: dict[str, Any] = {}
        for name in self.input_names:
            lname = name.lower()
            if "input" in lname and "id" in lname:
                feeds[name] = ids_arr
            elif "attention" in lname and "mask" in lname:
                feeds[name] = mask_arr
            elif "token_type" in lname or "segment" in lname:
                feeds[name] = type_arr

        fallback = [ids_arr, mask_arr, type_arr]
        for idx, name in enumerate(self.input_names):
            if name not in feeds:
                feeds[name] = fallback[min(idx, len(fallback) - 1)]

        return feeds, mask_arr

    def _pool(self, raw_output, attention_mask):
        arr = self.np.asarray(raw_output, dtype=self.np.float32)
        if arr.ndim == 3:
            mask = attention_mask.astype(self.np.float32)[..., None]
            summed = (arr * mask).sum(axis=1)
            denom = mask.sum(axis=1)
            denom[denom == 0] = 1.0
            arr = summed / denom
        elif arr.ndim != 2:
            arr = arr.reshape(arr.shape[0], -1)

        norms = self.np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms

    def embed_texts(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """Embed text batch and return L2-normalized vectors."""
        if not texts:
            return []

        vectors: list[list[float]] = []
        for i in range(0, len(texts), max(batch_size, 1)):
            batch = [str(t or "") for t in texts[i : i + max(batch_size, 1)]]
            feeds, mask = self._prepare_inputs(batch)
            outputs = self.session.run(None, feeds)
            pooled = self._pool(outputs[0], mask)
            for row in pooled:
                vectors.append([float(x) for x in row.tolist()])
        return vectors


def get_onnx_embedder(
    project_root: Path | None = None,
    settings: dict[str, Any] | None = None,
) -> OnnxEmbedder | None:
    """Return cached embedder instance when ONNX backend is ready."""
    ready, _, settings = onnx_ready(project_root=project_root, settings=settings)
    if not ready:
        return None

    model_path = settings.get("onnx_model_path", "")
    tokenizer_path = settings.get("onnx_tokenizer_path", "")
    max_length = _parse_max_length(settings.get("onnx_max_length", 256))

    key = (model_path, tokenizer_path, max_length)
    cached = _EMBEDDER_CACHE.get(key)
    if cached is not None:
        return cached

    embedder = OnnxEmbedder(
        model_path=model_path,
        tokenizer_path=tokenizer_path,
        max_length=max_length,
    )
    _EMBEDDER_CACHE[key] = embedder
    return embedder

