"""Tests for ``roam drift --by-team`` ownership-realisation summary.

Validates the helper-level behaviour. End-to-end ``cli_runner`` cases
live in tests/test_drift.py.
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_drift import _build_team_summary, _matches_actual


class TestMatchesActual:
    @pytest.mark.parametrize(
        "declared,actual,expected",
        [
            ("@alice", "alice", True),
            ("@alice", "Alice Cooper", True),
            ("@org/backend", "backend-bot", True),
            ("@org/backend", "Backend Service", True),
            ("@alice", "bob", False),
            ("@org/backend", "frontend-bot", False),
            ("", "alice", False),
            ("@alice", "", False),
        ],
    )
    def test_matches(self, declared, actual, expected):
        assert _matches_actual(declared, actual) is expected


class TestBuildTeamSummary:
    def test_empty(self):
        assert _build_team_summary({}) == []

    def test_single_team_full_realisation(self):
        per_owner = {"@alice": {"owned": 5, "drifted": 0, "realised": 5}}
        rows = _build_team_summary(per_owner)
        assert len(rows) == 1
        row = rows[0]
        assert row["owner"] == "@alice"
        assert row["owned"] == 5
        assert row["drifted"] == 0
        assert row["realised"] == 5
        assert row["realisation_pct"] == 100.0

    def test_partial_realisation(self):
        per_owner = {
            "@alice": {"owned": 10, "drifted": 4, "realised": 6},
        }
        row = _build_team_summary(per_owner)[0]
        assert row["realised"] == 6
        assert row["realisation_pct"] == 60.0
        assert row["drift_pct"] == 40.0

    def test_sorted_by_files_owned_desc(self):
        per_owner = {
            "@alice": {"owned": 3, "drifted": 0, "realised": 3},
            "@bob": {"owned": 10, "drifted": 2, "realised": 7},
            "@carol": {"owned": 5, "drifted": 1, "realised": 4},
        }
        rows = _build_team_summary(per_owner)
        assert [r["owner"] for r in rows] == ["@bob", "@carol", "@alice"]

    def test_drops_zero_owned(self):
        per_owner = {
            "@alice": {"owned": 0, "drifted": 0, "realised": 0},
            "@bob": {"owned": 1, "drifted": 0, "realised": 1},
        }
        rows = _build_team_summary(per_owner)
        assert [r["owner"] for r in rows] == ["@bob"]
