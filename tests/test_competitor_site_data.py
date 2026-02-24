import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roam.competitor_site_data import (
    CRITERIA_DATA,
    SCORING_RUBRIC,
    build_site_payload,
    compute_scores,
    default_output_path,
)
from roam.surface_counts import collect_surface_counts


def test_site_payload_has_expected_shape_and_count():
    payload = build_site_payload()

    assert payload["tracker_file"] == "reports/competitor_tracker.md"
    assert payload["tracker_updated"]
    assert "competitors" in payload
    assert "methodology" in payload
    assert "rubric" in payload
    assert len(payload["competitors"]) >= 9

    names = {entry["name"] for entry in payload["competitors"]}
    required = {
        "roam-code",
        "CKB/CodeMCP",
        "SonarQube",
        "CodeQL",
    }
    assert required <= names


def test_competitor_rows_include_evidence_metadata():
    payload = build_site_payload()
    sample = next(entry for entry in payload["competitors"] if entry["name"] == "roam-code")
    assert sample["confidence"] in {"High", "Medium", "Low"}
    assert sample["claim_type"] in {"measured", "mixed", "estimated"}
    assert sample["source_count"] >= 1
    assert re.match(r"\d{4}-\d{2}-\d{2}", sample["last_verified"])


def test_generated_landscape_json_is_in_sync():
    expected = build_site_payload()
    output_path = default_output_path()
    actual = json.loads(output_path.read_text(encoding="utf-8"))
    assert actual == expected


def test_roam_counts_match_surface_source_of_truth():
    payload = build_site_payload()
    surface = collect_surface_counts()
    roam = next(entry for entry in payload["competitors"] if entry["name"] == "roam-code")

    assert roam["mcp"] == str(surface["mcp"]["registered_tools"])
    assert str(surface["cli"]["canonical_commands"]) in roam["cli_commands"]
    assert re.search(r"\+1 alias", roam["cli_commands"])


# ---------------------------------------------------------------------------
# Rubric-based scoring tests
# ---------------------------------------------------------------------------

def test_every_competitor_has_scores_with_all_7_categories():
    payload = build_site_payload()
    cat_ids = {cat["id"] for cat in SCORING_RUBRIC}
    assert len(cat_ids) == 7

    for entry in payload["competitors"]:
        assert "scores" in entry, f"{entry['name']} missing scores"
        scores = entry["scores"]
        assert "total" in scores
        assert "map_x" in scores
        assert "map_y" in scores
        assert "categories" in scores
        entry_cat_ids = {c["id"] for c in scores["categories"]}
        assert entry_cat_ids == cat_ids, f"{entry['name']} missing categories: {cat_ids - entry_cat_ids}"


def test_every_criterion_in_rubric_appears_in_every_competitors_data():
    all_crit_ids = set()
    for cat in SCORING_RUBRIC:
        for crit in cat["criteria"]:
            all_crit_ids.add(crit["id"])

    payload = build_site_payload()
    for entry in payload["competitors"]:
        scores = entry["scores"]
        found_ids = set()
        for cat in scores["categories"]:
            for crit in cat["criteria"]:
                found_ids.add(crit["id"])
        assert found_ids == all_crit_ids, (
            f"{entry['name']} missing criteria in scores: {all_crit_ids - found_ids}"
        )


def test_total_equals_sum_of_category_totals():
    payload = build_site_payload()
    for entry in payload["competitors"]:
        scores = entry["scores"]
        cat_sum = sum(c["score"] for c in scores["categories"])
        assert scores["total"] == cat_sum, (
            f"{entry['name']}: total {scores['total']} != cat sum {cat_sum}"
        )


def test_map_coordinates_in_range():
    payload = build_site_payload()
    for entry in payload["competitors"]:
        scores = entry["scores"]
        assert 0 <= scores["map_x"] <= 100, f"{entry['name']} map_x={scores['map_x']}"
        assert 0 <= scores["map_y"] <= 100, f"{entry['name']} map_y={scores['map_y']}"


def test_roam_dataflow_taint_is_intra():
    """Self-assessment check: roam ships basic intra-procedural dataflow."""
    assert CRITERIA_DATA["roam-code"]["dataflow_taint"] == "intra"


def test_backward_compat_arch_and_agent_fields():
    payload = build_site_payload()
    for entry in payload["competitors"]:
        assert "arch" in entry, f"{entry['name']} missing arch"
        assert "agent" in entry, f"{entry['name']} missing agent"
        assert isinstance(entry["arch"], int)
        assert isinstance(entry["agent"], int)
        # arch/agent should match map_y/map_x
        assert entry["arch"] == entry["scores"]["map_y"]
        assert entry["agent"] == entry["scores"]["map_x"]


def test_rubric_in_payload():
    payload = build_site_payload()
    assert "rubric" in payload
    assert len(payload["rubric"]) == 7
    for cat in payload["rubric"]:
        assert "id" in cat
        assert "label" in cat
        assert "max_points" in cat
        assert "default_weight" in cat
        assert "criteria" in cat
        assert len(cat["criteria"]) > 0


def test_rubric_max_points_sum_to_100():
    total = sum(cat["max_points"] for cat in SCORING_RUBRIC)
    assert total == 100, f"Rubric max points sum to {total}, expected 100"


def test_compute_scores_function_directly():
    scores = compute_scores(CRITERIA_DATA["roam-code"])
    assert scores["total"] > 0
    assert 0 <= scores["map_x"] <= 100
    assert 0 <= scores["map_y"] <= 100
    assert scores["total_criteria"] == 45
    assert scores["subjective_count"] == 1


def test_criteria_data_covers_all_competitors():
    """Every competitor in MAP_METADATA should have CRITERIA_DATA."""
    from roam.competitor_site_data import MAP_METADATA
    for name in MAP_METADATA:
        assert name in CRITERIA_DATA, f"Missing CRITERIA_DATA for {name}"


def test_every_landscape_tool_has_version_and_repo():
    """Every tool on the landscape map must track which version was evaluated."""
    payload = build_site_payload()
    for entry in payload["competitors"]:
        assert entry.get("version_evaluated"), (
            f"{entry['name']} missing version_evaluated"
        )
        assert entry.get("repo_url"), (
            f"{entry['name']} missing repo_url"
        )
