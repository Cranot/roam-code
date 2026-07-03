"""Config-by-name grep matches must respect compiler forbidden paths."""

from __future__ import annotations

import json

import roam.plan.compiler as compiler


def test_config_probe_drops_forbidden_grep_matches_before_embedding(tmp_path, monkeypatch):
    public_match = tmp_path / "src" / "settings.py"

    def fake_run_roam(args, cwd, timeout=8.0, detail=False):
        return {
            "matches": [
                {
                    "path": "internal/planning/secrets.env",
                    "line": 1,
                    "content": "APP_SECRET=internal-secret-value",
                },
                {
                    "path": str(tmp_path / ".env"),
                    "line": 2,
                    "content": "APP_SECRET=dotenv-secret-value",
                },
                {
                    "path": str(public_match),
                    "line": 3,
                    "content": "APP_SECRET = os.getenv('APP_SECRET')",
                },
            ]
        }

    monkeypatch.setattr(compiler, "_run_roam", fake_run_roam)

    out = compiler._probe_config_for_task(
        "where is the APP_SECRET env var configured",
        str(tmp_path),
    )

    assert out is not None
    assert out["config_matches"] == [
        {
            "location": "src/settings.py:3",
            "snippet": "APP_SECRET = os.getenv('APP_SECRET')",
            "trust": "untrusted_grep_output",
        }
    ]
    assert out["config_matches_dropped_forbidden_count"] == 2
    wire = json.dumps(out)
    assert "internal-secret-value" not in wire
    assert "dotenv-secret-value" not in wire
    assert "internal/planning/secrets.env" not in wire
    assert ".env:2" not in wire


def test_config_probe_degrades_when_all_grep_matches_are_forbidden(tmp_path, monkeypatch):
    def fake_run_roam(args, cwd, timeout=8.0, detail=False):
        return {
            "matches": [
                {
                    "path": "internal/planning/secrets.env",
                    "line": 1,
                    "content": "APP_SECRET=internal-secret-value",
                },
                {
                    "path": ".env.local",
                    "line": 2,
                    "content": "APP_SECRET=dotenv-secret-value",
                },
            ]
        }

    monkeypatch.setattr(compiler, "_run_roam", fake_run_roam)

    out = compiler._probe_config_for_task(
        "where is the APP_SECRET env var configured",
        str(tmp_path),
    )

    assert out is not None
    assert "config_matches" not in out
    assert out["config_matches_dropped_forbidden_count"] == 2
    assert "forbidden_paths" in out["config_matches_unavailable"]
    wire = json.dumps(out)
    assert "internal-secret-value" not in wire
    assert "dotenv-secret-value" not in wire
