from __future__ import annotations

import yaml

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
CONFIG = ROOT / ".github" / "dependabot.yml"


def _updates_by_ecosystem() -> dict[str, dict]:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    updates = payload["updates"]
    return {row["package-ecosystem"]: row for row in updates}


def test_dependabot_cooldowns_cover_pip_and_github_actions() -> None:
    updates = _updates_by_ecosystem()
    assert set(updates) == {"pip", "github-actions"}

    assert updates["pip"]["cooldown"] == {
        "default-days": 7,
        "semver-major-days": 30,
        "semver-minor-days": 14,
        "semver-patch-days": 7,
        "include": ["*"],
    }
    assert updates["github-actions"]["cooldown"] == {
        "default-days": 7,
        "include": ["*"],
    }


def test_cooldown_policy_keeps_security_updates_immediate_and_sha_bumps_reviewable() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    updates = _updates_by_ecosystem()

    assert "security updates continue immediately" in text
    assert "day-zero" in text
    assert updates["github-actions"]["schedule"]["interval"] == "weekly"
    assert updates["github-actions"]["commit-message"]["prefix"] == "ci"
    assert "exclude" not in updates["pip"]["cooldown"]
    assert "exclude" not in updates["github-actions"]["cooldown"]
