"""Regression tests for the validated T-prime repair-intent scorer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from roam.retrieve.repair_intent import (
    ScoredCandidate,
    ScorerCandidate,
    derive_repair_intent,
    score_pool_repair_intent,
)

DATA_DIR = Path(__file__).parent / "data"
FROZEN_PATH = DATA_DIR / "1c_frozen.json"
RESULTS_PATH = DATA_DIR / "1c_fourarm_results.json"
EXPECTED_FROZEN_SHA256 = "dc30d31aed6d52baef703531417e52016c2b698694944f9cea5b3f9abf578eb5"
EXPECTED_T_NDCG10 = 0.6045


def _frozen_cases(corpus: dict) -> list[dict]:
    return [case for report in corpus["reports"] for case in report["cases"]]


def _candidate_for_site(site: dict, ordinal: int) -> ScoredCandidate:
    body = "\n".join(
        value[1:]
        for value in site.get("changes", [])
        if isinstance(value, str) and value[:1] in {"+", "-"}
    )
    candidate = ScorerCandidate.from_body(
        {
            "id": ordinal,
            "file": site["file"],
            "symbol": site["symbol"],
            "kind": site.get("kind", "function"),
            "line_start": site.get("line", 1),
            "line_end": site.get("line", 1),
        },
        body,
    )
    return ScoredCandidate(candidate=candidate, lexical=1.0 / (ordinal + 1))


def test_frozen_corpus_replay_uses_ported_scorer() -> None:
    """Replay all frozen repair intents and pin the recorded T-arm metric."""
    corpus = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))

    assert hashlib.sha256(FROZEN_PATH.read_bytes()).hexdigest() == EXPECTED_FROZEN_SHA256
    assert corpus["case_count"] == 576
    cases = _frozen_cases(corpus)
    assert len(cases) == corpus["case_count"]
    assert results["frozen_cases_sha256"] == EXPECTED_FROZEN_SHA256

    replayed = 0
    score_digest = hashlib.sha256()
    for case in cases:
        sites = case["symbols"]
        for anchor in sites:
            intent = derive_repair_intent(anchor["changes"])
            pool = [_candidate_for_site(site, index) for index, site in enumerate(sites) if site is not anchor]
            scored = score_pool_repair_intent(pool, intent)
            replayed += 1
            score_digest.update(
                json.dumps(
                    [round(item.repair_score, 12) for item in scored],
                    separators=(",", ":"),
                ).encode("ascii")
            )

    assert replayed == 1530
    assert score_digest.hexdigest() == "8c81b43903dfb7f2e9a9a7474baf93e944a914ec5d46990d1bacd335b96d264b"

    measured_ndcg10 = sum(
        float(case["case_metrics"]["T"]["ndcg@10"])
        for case in results["cases"]
    ) / len(results["cases"])
    assert abs(measured_ndcg10 - EXPECTED_T_NDCG10) <= 0.005


def test_repair_intent_empty_pool_is_empty() -> None:
    intent = derive_repair_intent(["- return value", "+ return value.get()"])
    assert score_pool_repair_intent([], intent) == []


def test_repair_intent_single_candidate_preserves_pool_and_scores() -> None:
    intent = derive_repair_intent(["- return value", "+ return value.get()"])
    candidate = ScoredCandidate(
        candidate=ScorerCandidate.from_body(
            {"id": 1, "file": "mod.py", "symbol": "read", "line_start": 1},
            "def read(value):\n    return value\n",
        ),
        lexical=0.75,
        graph_score=0.3,
    )

    scored = score_pool_repair_intent([candidate], intent)

    assert len(scored) == 1
    assert scored[0].candidate is candidate.candidate
    assert scored[0].lexical == 0.75
    assert scored[0].graph_score == 0.3
    assert scored[0].repair_score == 0.65
