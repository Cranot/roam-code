"""Tests for expanded SNA metrics vector and debt score (backlog #70)."""

from __future__ import annotations

import os

import pytest


def test_graph_metrics_schema_has_sna_v2_columns(indexed_project):
    from roam.db.connection import open_db

    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_project))
        with open_db(readonly=True) as conn:
            rows = conn.execute("PRAGMA table_info(graph_metrics)").fetchall()
            cols = {r["name"] for r in rows}
            expected = {
                "closeness",
                "eigenvector",
                "clustering_coefficient",
                "debt_score",
            }
            assert expected.issubset(cols), f"Missing columns: {expected - cols}"
    finally:
        os.chdir(old_cwd)


def test_graph_metrics_populates_sna_v2_values(indexed_project):
    from roam.db.connection import open_db

    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_project))
        with open_db(readonly=True) as conn:
            row = conn.execute(
                "SELECT closeness, eigenvector, clustering_coefficient, debt_score "
                "FROM graph_metrics ORDER BY pagerank DESC LIMIT 1"
            ).fetchone()
            if row is None:
                pytest.skip("graph_metrics empty")

            assert isinstance(row["closeness"] or 0.0, (int, float))
            assert isinstance(row["eigenvector"] or 0.0, (int, float))
            cc = float(row["clustering_coefficient"] or 0.0)
            debt = float(row["debt_score"] or 0.0)
            assert 0.0 <= cc <= 1.0
            assert 0.0 <= debt <= 100.0
    finally:
        os.chdir(old_cwd)


def test_context_helpers_exposes_sna_v2_fields(indexed_project):
    from roam.db.connection import open_db
    from roam.commands.context_helpers import get_graph_metrics

    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_project))
        with open_db(readonly=True) as conn:
            row = conn.execute("SELECT symbol_id FROM graph_metrics LIMIT 1").fetchone()
            if row is None:
                pytest.skip("graph_metrics empty")
            metrics = get_graph_metrics(conn, row["symbol_id"])
            assert metrics is not None
            assert "closeness" in metrics
            assert "eigenvector" in metrics
            assert "clustering_coefficient" in metrics
            assert "debt_score" in metrics
    finally:
        os.chdir(old_cwd)
