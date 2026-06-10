"""W-REPO / W-ENTRY / W-CFG procedures (2026-06-09).

Telemetry-driven: repo-level structure ("what are the layers of this
codebase"), entry-point ("what's the entry point for the CLI") and env-var
("where is the ROAM_X env var configured") prompts compiled to EMPTY
freeform envelopes — the W67/W49 probes existed but only ran on the L1
path, which target-less freeform prompts never reached.
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import (
    _classify,
    _extract_repo_structure,
    _probe_config_where,
    _probe_entry_point_where,
)


class TestRepoStructureClassification:
    @pytest.mark.parametrize(
        "task,dim",
        [
            ("what are the layers of this codebase", "layers"),
            ("what are the clusters", "clusters"),
            ("what is the health score of this repo", "health"),
            ("how healthy is this codebase", "health"),
            ("layers of the project", "layers"),
        ],
    )
    def test_repo_structure_routes_with_dimension(self, task, dim):
        assert _classify(task)[0] == "repo_structure"
        assert _extract_repo_structure(task) == dim

    @pytest.mark.parametrize(
        "task,expected",
        [
            # symbol/file-scoped structural prompts keep their subtypes
            ("what depends on cli.py", "structural_coupling"),
            ("blast radius of compile_plan", "structural_blast"),
            # roam-subcommand perf phrasing must not be swallowed by "health"
            ("why is roam health slow", "cli_verb_why_slow"),
            # ranking phrasing keeps W12 precedence
            ("top 5 clusters by size", "top_n_ranking"),
        ],
    )
    def test_precedence_preserved(self, task, expected):
        assert _classify(task)[0] == expected


class TestEntryConfigClassification:
    @pytest.mark.parametrize(
        "task",
        [
            "what's the entry point for the CLI",
            "what is the main entry point of roam",
            "how does the cli start",
        ],
    )
    def test_entry_point_routes(self, task):
        assert _classify(task)[0] == "entry_point_where"

    @pytest.mark.parametrize(
        "task",
        [
            "where is the ROAM_GREP_ENGINE env var configured",
            "where is the ROAM_BYPASS environment variable configured",
        ],
    )
    def test_config_where_routes(self, task):
        assert _classify(task)[0] == "config_where"


class TestDegradedAnswers:
    def test_entry_point_unavailable_is_explicit(self, tmp_path):
        # No index in an empty dir → probe returns the degraded answer,
        # never None (Pattern 2: absent state must be explicit).
        facts = _probe_entry_point_where([], str(tmp_path), task="what's the entry point")
        assert facts and "entry_points_unavailable" in facts
        assert "roam --json entry-points" in facts["entry_points_unavailable"]

    def test_config_unavailable_names_the_var_and_command(self, tmp_path):
        facts = _probe_config_where([], str(tmp_path), task="where is the ZZGHOST_VAR env var configured")
        assert facts and "config_matches_unavailable" in facts
        assert "roam grep ZZGHOST_VAR" in facts["config_matches_unavailable"]
