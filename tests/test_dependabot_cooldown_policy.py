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


def test_dependabot_covers_pip_with_a_graduated_cooldown() -> None:
    updates = _updates_by_ecosystem()

    assert updates["pip"]["cooldown"] == {
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
    assert set(updates) == {"pip"}

    text = CONFIG.read_text(encoding="utf-8")
    assert "NO github-actions ecosystem here, deliberately" in text
    assert "dev/pin_github_actions.sh" in text
    assert "Permanent red PRs are worse than none" in text


def test_cooldown_policy_keeps_security_updates_immediate() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    updates = _updates_by_ecosystem()

    assert "security updates continue immediately" in text
    assert "SECURITY updates are enabled at the" in text
    assert updates["pip"]["schedule"]["interval"] == "weekly"
    assert updates["pip"]["commit-message"]["prefix"] == "deps"
    assert "exclude" not in updates["pip"]["cooldown"]
