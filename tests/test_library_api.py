"""Tests for the programmatic Python API (`roam.api`)."""

from __future__ import annotations

import pytest


def test_run_json_health(project_factory):
    from roam.api import run_json

    proj = project_factory({
        "src/app.py": (
            "def process(x):\n"
            "    return x + 1\n"
        ),
    })

    data = run_json("health", project_root=proj)
    assert data["command"] == "health"
    assert "summary" in data
    assert "health_score" in data["summary"]


def test_client_metrics(project_factory):
    from roam.api import RoamClient

    proj = project_factory({
        "src/app.py": (
            "def process(x):\n"
            "    y = x + 1\n"
            "    return y\n"
        ),
    })

    client = RoamClient(project_root=proj)
    data = client.metrics("src/app.py")
    assert data["command"] == "metrics"
    assert data["summary"]["target_type"] == "file"
    assert "metrics" in data


def test_invalid_command_raises(project_factory):
    from roam.api import RoamAPIError, run_json

    proj = project_factory({
        "src/app.py": "def process(x):\n    return x + 1\n",
    })

    with pytest.raises(RoamAPIError):
        run_json("not-a-real-command", project_root=proj)


def test_gate_failure_behavior(project_factory):
    from roam.api import RoamAPIError, run_json

    proj = project_factory({
        "src/app.py": (
            "def process(x):\n"
            "    return x + 1\n"
        ),
        ".roam-gates.yml": (
            "health:\n"
            "  health_min: 101\n"
        ),
    })

    gate_data = run_json("health", "--gate", project_root=proj, allow_gate_failure=True)
    assert gate_data["command"] == "health"
    assert gate_data.get("gate_failure") is True or gate_data.get("exit_code") == 5

    with pytest.raises(RoamAPIError):
        run_json("health", "--gate", project_root=proj, allow_gate_failure=False)
