"""Tests for `roam why-slow` — runtime hotspot finder."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli


def _make_repo(tmp: Path):
    """Initialize a tiny git repo + run `roam init` so why-slow has an index."""
    (tmp / "app.py").write_text(
        "def slow_fn():\n    pass\n\ndef fast_fn():\n    pass\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."], cwd=tmp, check=True
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init", "-q"],
        cwd=tmp,
        check=True,
    )
    # Run roam init so .roam/index.sqlite exists
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(cwd)


def _seed_runtime_stats(tmp: Path, rows: list[dict]):
    """Manually seed runtime_stats so we don't need a real trace ingest."""
    from roam.db.connection import open_db

    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        with open_db() as conn:
            for r in rows:
                conn.execute(
                    """INSERT INTO runtime_stats
                       (symbol_name, file_path, call_count, p50_latency_ms,
                        p99_latency_ms, error_rate, trace_source)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        r["name"],
                        r.get("file_path", "app.py"),
                        r["call_count"],
                        r.get("p50_ms", 1.0),
                        r.get("p99_ms", 10.0),
                        r.get("error_rate", 0.0),
                        r.get("source", "test"),
                    ),
                )
            conn.commit()
    finally:
        os.chdir(cwd)


def test_why_slow_no_data(tmp_path):
    _make_repo(tmp_path)
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(cli, ["why-slow"])
        assert result.exit_code == 0
        assert "NO RUNTIME DATA" in result.output
    finally:
        os.chdir(cwd)


def test_why_slow_no_data_json(tmp_path):
    _make_repo(tmp_path)
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(cli, ["--json", "why-slow"])
        assert result.exit_code == 0, result.output
        # Extract the JSON object from output (may have preamble noise)
        out = result.output
        start = out.find("{")
        assert start >= 0, f"No JSON in output: {out!r}"
        data = json.loads(out[start:])
        assert data["summary"]["verdict"] == "NO RUNTIME DATA"
        assert data["hotspots"] == []
    finally:
        os.chdir(cwd)


def test_why_slow_finds_hotspots(tmp_path):
    _make_repo(tmp_path)
    _seed_runtime_stats(
        tmp_path,
        [
            {"name": "slow_fn", "call_count": 1_000_000, "p99_ms": 250.0},
            {"name": "fast_fn", "call_count": 10, "p99_ms": 0.5},
            {"name": "medium_fn", "call_count": 5_000, "p99_ms": 25.0},
        ],
    )
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(cli, ["--json", "why-slow"])
        assert result.exit_code == 0, result.output
        out = result.output
        start = out.find("{")
        assert start >= 0, f"No JSON in output: {out!r}"
        data = json.loads(out[start:])
        assert "HOTSPOT" in data["summary"]["verdict"]
        names = [h["name"] for h in data["hotspots"]]
        assert "slow_fn" in names
        # slow_fn should rank first (highest call_count * latency)
        assert data["hotspots"][0]["name"] == "slow_fn"
    finally:
        os.chdir(cwd)


def test_why_slow_min_calls_filter(tmp_path):
    _make_repo(tmp_path)
    _seed_runtime_stats(
        tmp_path,
        [
            {"name": "slow_fn", "call_count": 1_000_000, "p99_ms": 250.0},
            {"name": "fast_fn", "call_count": 10, "p99_ms": 0.5},
        ],
    )
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(cli, ["--json", "why-slow", "--min-calls", "1000"])
        assert result.exit_code == 0
        out = result.output
        start = out.find("{")
        assert start >= 0
        data = json.loads(out[start:])
        names = [h["name"] for h in data["hotspots"]]
        assert "slow_fn" in names
        assert "fast_fn" not in names
    finally:
        os.chdir(cwd)


def test_why_slow_top_limit(tmp_path):
    _make_repo(tmp_path)
    _seed_runtime_stats(
        tmp_path,
        [{"name": f"fn_{i}", "call_count": 100 * (i + 1), "p99_ms": 5.0} for i in range(10)],
    )
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(cli, ["--json", "why-slow", "--top", "3"])
        assert result.exit_code == 0
        out = result.output
        start = out.find("{")
        assert start >= 0
        data = json.loads(out[start:])
        assert len(data["hotspots"]) == 3
    finally:
        os.chdir(cwd)
