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


def test_uv_lockfile_uses_the_lock_aware_dependency_ecosystem() -> None:
    """Manifest-only updates cannot reach tests behind `uv sync --locked`."""
    assert (ROOT / "uv.lock").is_file()
    updates = _updates_by_ecosystem()
    assert "uv" in updates, "Use the uv ecosystem so dependency PRs update uv.lock with pyproject.toml"
    assert "pip" not in updates, "Avoid duplicate manifest-only Python update jobs"
    assert updates["uv"]["directory"] == "/"


def test_dependabot_covers_uv_with_a_graduated_cooldown() -> None:
    updates = _updates_by_ecosystem()

    assert updates["uv"]["cooldown"] == {
        "default-days": 7,
        "semver-major-days": 30,
        "semver-minor-days": 14,
        "semver-patch-days": 7,
        "include": ["*"],
    }


def test_github_actions_is_deliberately_absent_with_the_reason_recorded() -> None:
    """A pinned action SHA is a PRODUCT surface here, not just CI config: the
    same value appears in .github/workflows/, action.yml, the templates
    `roam init` emits, the documented examples, and four tests that assert it
    as a literal. Dependabot's github-actions ecosystem can edit only the
    first of those, so any bump it proposes desynchronises this repo's CI
    from the templates it ships, the asserting tests correctly refuse it, and
    the PR is red by construction.

    That is a reason to keep Dependabot out of this ecosystem, NOT a reason to
    loosen the assertions -- they are what keeps the shipped templates honest.
    Bumps go through dev/pin_github_actions.sh, which repins every surface in
    one pass. Dependabot security updates are repository-level and still
    reach pinned actions without an entry here.

    This test exists so re-adding the ecosystem is a deliberate act that has
    to delete a stated rationale, rather than a plausible-looking one-line
    "parity" edit that silently reintroduces permanently-red PRs.
    """
    updates = _updates_by_ecosystem()
    assert set(updates) == {"uv"}

    text = CONFIG.read_text(encoding="utf-8")
    assert "NO github-actions ecosystem here, deliberately" in text
    assert "dev/pin_github_actions.sh" in text
    assert "Permanent red PRs are worse than none" in text


def test_cooldown_policy_keeps_security_updates_immediate() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    updates = _updates_by_ecosystem()

    assert "security updates continue immediately" in text
    assert "SECURITY updates are enabled at the" in text
    assert updates["uv"]["schedule"] == {"interval": "weekly", "day": "monday"}
    assert updates["uv"]["commit-message"]["prefix"] == "deps"
    assert updates["uv"]["open-pull-requests-limit"] == 5
    assert "exclude" not in updates["uv"]["cooldown"]
    assert updates["uv"]["ignore"] == [
        {"dependency-name": name, "update-types": ["version-update:semver-major"]}
        for name in ("networkx", "leidenalg", "onnxruntime")
    ]
