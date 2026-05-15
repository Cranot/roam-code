"""v12.2 learned-ranker hook for ``roam retrieve --rerank learned``.

Per the v12.2 ML review (agent #5): a 22-feature LambdaMART trained on
the 30-task self-bench (+ mined PR pairs) yields a 200KB-2MB serialised
LightGBM model with microsecond inference. Optional install:

    pip install "roam-code[learned]"

The model file lives at ``$ROAM_LEARNED_MODEL`` (default
``~/.cache/roam/learned-ranker.lgbm``). When the file or the LightGBM
package isn't available, ``score`` returns an empty dict and the
reranker falls back to the v12.0/v12.1 blend untouched.

What this file ships:

* The **inference path** — feature extraction + LightGBM predict.
* The **training entry point** — ``train_from_bench(bench_path,
  model_out)`` so users can train their own with their own bench.

Defaults are conservative — no default model file ships with the
package; users opt-in by training one.
"""

from __future__ import annotations

import os
from pathlib import Path

from roam.eval.harness import load_tasks
from roam.retrieve.pipeline import run_retrieve

# Optional dependency: LightGBM. Used by ``train_from_bench`` (training)
# and ``_load_model`` (inference). Hoisted to module scope so tests can
# monkeypatch ``lgb = None`` to exercise the missing-dep install-hint
# path without uninstalling the real package. ``_load_model`` keeps its
# own try/except because it ALSO catches sklearn/numpy ABI errors that
# bubble up via ``lightgbm``'s transitive imports — those are not pure
# ``ImportError``s, so a module-level catch would miss them.
try:
    import lightgbm as lgb  # type: ignore
except ImportError as _lgb_import_exc:  # pragma: no cover - exercised via test monkeypatch
    lgb = None  # type: ignore[assignment]
    _LIGHTGBM_IMPORT_ERROR: ImportError | None = _lgb_import_exc
else:
    _LIGHTGBM_IMPORT_ERROR = None

# 22-feature vector — all derivable from rerank.py's existing per-candidate
# scores plus a couple of structural extras. Order is fixed so model
# training and inference align.
FEATURE_NAMES: tuple[str, ...] = (
    "fts_score",
    "pr_norm",
    "cochange_norm",
    "runtime_norm",
    "semantic_norm",
    "clone_boost",
    "kind_function",
    "kind_method",
    "kind_class",
    "kind_other",
    "file_role_source",
    "file_role_test",
    "file_role_other",
    "depth_in_path",
    "fan_in",
    "fan_out",
    "line_count",
    "is_entry",
    "has_docstring",
    "name_token_overlap",
    "qname_token_overlap",
    "exact_name_match",
)


def _resolve_model_path() -> Path:
    return Path(os.environ.get("ROAM_LEARNED_MODEL") or (Path.home() / ".cache" / "roam" / "learned-ranker.lgbm"))


def _load_model():
    """Return a loaded LightGBM Booster or ``None`` when unavailable.

    Catches the broader ``Exception`` not just ``ImportError`` because
    LightGBM transitively imports sklearn, which can fail with a
    ``ValueError`` on numpy/sklearn ABI mismatches (common in conda
    environments where conda + pip versions diverge).
    """
    try:
        import lightgbm as lgb  # type: ignore
    except Exception:
        return None
    path = _resolve_model_path()
    if not path.is_file():
        return None
    try:
        return lgb.Booster(model_file=str(path))
    except Exception:
        return None


def _extract_features(c: dict) -> list[float]:
    """Project a candidate dict onto the 22-feature vector.

    Falls back to 0 for missing fields so a candidate from any earlier
    pipeline stage works without the reranker re-computing.
    """
    kind = (c.get("kind") or "").lower()
    file_role = (c.get("file_role") or "").lower()
    name = (c.get("name") or "").lower()
    qname = (c.get("qualified_name") or "").lower()
    task_lower = (c.get("_task") or "").lower()
    line_start = int(c.get("line_start") or 0)
    line_end = int(c.get("line_end") or line_start)
    fan_in = float(c.get("fan_in") or 0)
    fan_out = float(c.get("fan_out") or 0)
    just = c.get("justifications") or {}
    return [
        float(c.get("fts_score") or 0),
        float(just.get("pagerank") or 0),
        float(just.get("co_change") or 0),
        float(just.get("runtime_hot") or 0),
        float(just.get("semantic") or 0),
        1.0 if just.get("clone_cluster") else 0.0,
        1.0 if kind == "function" else 0.0,
        1.0 if kind == "method" else 0.0,
        1.0 if kind == "class" else 0.0,
        1.0 if kind not in ("function", "method", "class") else 0.0,
        1.0 if file_role == "source" else 0.0,
        1.0 if file_role == "test" else 0.0,
        1.0 if file_role not in ("source", "test") else 0.0,
        float(c.get("depth_in_path") or 0),
        fan_in,
        fan_out,
        float(line_end - line_start + 1),
        1.0 if c.get("is_entry") else 0.0,
        1.0 if c.get("has_docstring") else 0.0,
        _token_overlap(name, task_lower),
        _token_overlap(qname, task_lower),
        1.0 if name and name == task_lower.strip() else 0.0,
    ]


def _token_overlap(a: str, b: str) -> float:
    """Jaccard over alphanum tokens, used for name/qname × task overlap."""
    import re

    if not a or not b:
        return 0.0
    ta = set(re.findall(r"[a-z0-9]+", a))
    tb = set(re.findall(r"[a-z0-9]+", b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


def score(candidates: list[dict], task: str) -> dict[int, float]:
    """Return ``{symbol_id: model_score}`` for *candidates*.

    Empty dict on any unavailability — caller falls back to the existing
    structural blend. The reranker's mainline path stays unchanged.
    """
    if not candidates or not task:
        return {}
    model = _load_model()
    if model is None:
        return {}
    out: dict[int, float] = {}
    feats: list[list[float]] = []
    sids: list[int] = []
    for c in candidates:
        c_with_task = {**c, "_task": task}
        feats.append(_extract_features(c_with_task))
        sids.append(int(c["symbol_id"]))
    try:
        preds = model.predict(feats)  # type: ignore[union-attr]
    except Exception:
        return {}
    for sid, p in zip(sids, preds):
        out[sid] = float(p)
    return out


# ---------------------------------------------------------------------------
# Training entry point — called by ``roam train-ranker`` (lands v12.3)
# ---------------------------------------------------------------------------


def train_from_bench(bench_path: Path, model_out: Path, *, n_estimators: int = 200) -> dict:
    """Train a LambdaMART ranker from a JSONL bench + the live index.

    Each task expands to N candidates × 22 features × {1 if expected_files
    contain candidate's file else 0}. LightGBM's ``LGBMRanker`` does the
    pairwise NDCG work. Output is a single ``.lgbm`` model file.

    Returns a summary dict ``{tasks, candidates, ndcg@10, model_size}``.
    Raises ``ImportError`` with an install hint when LightGBM isn't
    installed — install with ``pip install 'roam-code[learned]'``.
    """
    if lgb is None:
        raise ImportError(
            "train_from_bench() requires LightGBM. "
            "Install with: pip install 'roam-code[learned]' (or: pip install lightgbm). "
            f"Original error: {_LIGHTGBM_IMPORT_ERROR!r}"
        )

    from roam.db.connection import open_db

    tasks = load_tasks(bench_path)
    X: list[list[float]] = []
    y: list[int] = []
    groups: list[int] = []

    with open_db(readonly=True) as conn:
        for t in tasks:
            result = run_retrieve(conn, t.task, budget=100_000, k=50, rerank="fast")
            cands = result.get("candidates") or []
            if not cands:
                continue
            expected = set(t.expected_files)
            group_size = 0
            for c in cands:
                feats = _extract_features({**c, "_task": t.task})
                rel = 1 if (c.get("file_path") or "") in expected else 0
                X.append(feats)
                y.append(rel)
                group_size += 1
            if group_size:
                groups.append(group_size)

    if not X:
        return {"tasks": 0, "candidates": 0, "error": "empty bench"}

    ranker = lgb.LGBMRanker(
        n_estimators=n_estimators,
        learning_rate=0.05,
        num_leaves=15,
        min_child_samples=5,
        objective="lambdarank",
        metric="ndcg",
        ndcg_at=[5, 10, 20],
        verbose=-1,
    )
    ranker.fit(X, y, group=groups)
    model_out.parent.mkdir(parents=True, exist_ok=True)
    ranker.booster_.save_model(str(model_out))
    return {
        "tasks": len(groups),
        "candidates": len(X),
        "model_path": str(model_out),
        "model_size": model_out.stat().st_size,
    }


def is_available() -> bool:
    """``True`` when LightGBM + a model file are both present."""
    return _load_model() is not None


def feature_names() -> tuple[str, ...]:
    """Return the 22-feature vector in canonical order."""
    return FEATURE_NAMES
