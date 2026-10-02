"""Keep public measurement claims attached to their actual source records."""

from __future__ import annotations

import json
import re
import statistics

from tests._helpers.repo_root import repo_root
from tests.test_homepage_contract import SITE, HomepageParser, normalized

ROOT = repo_root()


def test_transfer_table_matches_the_published_record_and_full_corpus():
    page = HomepageParser((SITE / "measurements.html").read_text(encoding="utf-8"))
    section = next(node for node in page.root.find("section") if node.attrs.get("id") == "transfer")
    record = ROOT / "benchmarks/cross-repo-l1"
    prompts = [
        line
        for line in (record / "CORPUS_L1_TRANSFER_60.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(prompts) == 60
    results = (record / "RESULTS.md").read_text(encoding="utf-8")
    recorded = re.findall(r"\| (fastapi|gin|svelte|roam-code).*?\|\s*\*\*\d+\*\*\s*\|\s*(\d+)\s*\|", results)
    assert len(recorded) == 4, "An empty or changed source table is not a passing correspondence"
    body = next(section.find("tbody"))
    rows = list(body.find("tr"))
    assert len(rows) == len(recorded)
    for row, (repo, count) in zip(rows, recorded):
        assert normalized(next(row.find("th")).text()).lower().startswith(repo.removesuffix("-code"))
        assert normalized(next(row.find("td")).text()) == f"{count} / {len(prompts)}"
    text = normalized(section.text())
    assert "routing, not answer accuracy" in text
    assert "different corpus" in text and "not rerun" in text
    assert "13.10.0" in text and "b6a8e87f" in text


def test_retrieval_summary_preserves_the_real_baseline_and_protocol_gap():
    page = HomepageParser((SITE / "measurements.html").read_text(encoding="utf-8"))
    section = next(node for node in page.root.find("section") if node.attrs.get("id") == "history")
    text = normalized(section.text())
    results = json.loads((ROOT / "tests/data/1c_fourarm_results.json").read_text(encoding="utf-8"))
    assert len(results["cases"]) == 576
    for arm in ("T", "B0", "B2"):
        mean = statistics.fmean(float(case["case_metrics"][arm]["ndcg@10"]) for case in results["cases"])
        assert f"{mean:.4f}" in text
    assert "lexical baseline" in text and "worse than lexical" in text
    assert "protocol discrepancy" in text and "does not rerun candidate collection" in text
    targets = {node.attrs.get("href") for node in section.find("a")}
    assert all(
        f"https://github.com/Cranot/roam-code/blob/main/{path}" in targets
        for path in (
            "tests/data/1c_frozen.json",
            "tests/data/1c_fourarm_results.json",
            "tests/test_repair_intent_frozen.py",
        )
    )


def test_real_example_is_visible_before_atlas_without_changing_the_headline():
    source = (SITE / "index.html").read_text(encoding="utf-8")
    page = HomepageParser(source)
    assert normalized(next(page.root.find("h1")).text()) == "Code analysis your agent can query. Checks it can run."
    details = next(node for node in page.root.find("details") if node.attrs.get("class") == "home-output")
    assert "open" in details.attrs
    assert source.index("roam impact calculate_total") < source.index('id="hero-atlas-title"')
