"""roam retrieve — graph-aware context server (A.1).

Public API:
    pipeline.RetrieveOptions(...)      # tuning knobs for the pipeline
    pipeline.run_retrieve(...)         # the full pipeline
    rerank.structural_score(...)       # the structural reranker
    seeds.infer_seeds(conn, query)     # seed inference
"""

from __future__ import annotations
