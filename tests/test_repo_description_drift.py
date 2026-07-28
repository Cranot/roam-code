"""The GitHub *description* is a count surface too — gate it like one.

``dev/build_readme_counts.py`` and ``scripts/sync_surface_counts.py`` keep
every count-bearing file in the tree in sync, and the pre-commit hook makes
that a blocking local gate. Neither can see the repository description on
GitHub, which is rendered above the file list, in search results and in every
listing that scrapes it. It drifted 43 commands (``238 commands / 224 MCP
tools`` against a real ``281 / 244``) with every in-tree gate green.

``dev/repo_description_drift.py`` closes that class. These tests pin the
behaviour that makes it trustworthy, entirely offline (the network path is
exercised by the scheduled workflow, not by the suite):

* claims are extracted with their unit, so "244 MCP tools" is checked against
  the MCP-tool count and not against something else that happens to be 244;
* a wrong number FAILS — the historical drift is the fixture;
* a claim with no computed truth is REPORTED, not failed, because a
  description is prose and demanding total coverage would make the gate lie;
* a description with no numeric claim passes silently;
* version strings are not counts;
* this repo's provider derives its numbers from the same
  ``collect_counts()`` the README gate uses, so the two surfaces cannot
  disagree about what a number means;
* the checker is wired into a workflow that actually runs it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
DRIFT_SCRIPT = REPO_ROOT / "dev" / "repo_description_drift.py"
TRUTH_PROVIDER = REPO_ROOT / "dev" / "description_truth.py"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def drift():
    return _load(DRIFT_SCRIPT, "_test_repo_description_drift")


@pytest.fixture(scope="module")
def provider():
    return _load(TRUTH_PROVIDER, "_test_description_truth")


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------


def test_extracts_number_and_unit_together(drift) -> None:
    claims = drift.extract_claims("SQLite code graph, 28 languages, 281 commands, 244 MCP tools, zero API keys.")
    assert [(c.value, c.unit) for c in claims] == [
        (28, "languages"),
        (281, "commands"),
        (244, "mcp tools"),
    ]


def test_word_form_numerals_are_not_claims(drift) -> None:
    """ "zero API keys" is rhetoric, not a derived count — extracting it would
    make every description fail for a number nothing computes."""
    assert drift.extract_claims("zero API keys, no telemetry") == []


def test_version_strings_are_not_counts(drift) -> None:
    """A dotted version must never be read as "10 point releases"."""
    claims = drift.extract_claims("Requires roam-code 13.10.0 on Python 3.12; ships 281 commands.")
    assert [(c.value, c.unit) for c in claims] == [(281, "commands")]


def test_thousands_separated_numbers_stay_whole(drift) -> None:
    claims = drift.extract_claims("indexes 1,024 files")
    assert [(c.value, c.unit) for c in claims] == [(1024, "files")]


def test_unit_stops_at_the_first_function_word(drift) -> None:
    """The unit is a noun phrase; "5 verbs and no counts" is about verbs."""
    claims = drift.extract_claims("5 verbs and no counts")
    assert [(c.value, c.unit) for c in claims] == [(5, "verbs")]


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def test_matching_claim_verifies(drift) -> None:
    findings = drift.compare(drift.extract_claims("281 commands"), {"commands": 281})
    assert [f.status for f in findings] == ["verified"]


def test_the_historical_drift_fails(drift) -> None:
    """The exact stale description that reached production, as the fixture."""
    stale = (
        "Local codebase intelligence CLI + MCP server for AI coding agents: SQLite code graph, "
        "28 languages, 238 commands, 224 MCP tools, change-safety gates, audit evidence, zero API keys."
    )
    truth = {"languages": 28, "commands": 281, "mcp tools": 244}
    findings = drift.compare(drift.extract_claims(stale), truth)
    assert [f.status for f in findings] == ["verified", "mismatch", "mismatch"]
    assert drift.run(repo="owner/name", description=stale, truth=truth) == 1


def test_longest_unit_phrase_wins(drift) -> None:
    """ "244 MCP tools" must resolve to ``mcp tools``, not to a bare ``tools``
    key that happens to hold a different number."""
    findings = drift.compare(
        drift.extract_claims("244 MCP tools"),
        {"tools": 999, "mcp tools": 244},
    )
    assert findings[0].truth_key == "mcp tools"
    assert findings[0].status == "verified"


def test_number_agreement_is_tolerated(drift) -> None:
    findings = drift.compare(drift.extract_claims("1 command"), {"commands": 1})
    assert findings[0].status == "verified"


def test_unknown_unit_is_reported_not_failed(drift) -> None:
    """A description is prose. Claims we cannot compute must be visible and
    non-blocking, or the gate gets muted and stops catching real drift."""
    description = "281 commands and 12 undocumented widgets"
    truth = {"commands": 281}
    findings = drift.compare(drift.extract_claims(description), truth)
    assert [f.status for f in findings] == ["verified", "unverified"]
    assert drift.run(repo="owner/name", description=description, truth=truth) == 0
    assert drift.run(repo="owner/name", description=description, truth=truth, fail_on_unverified=True) == 3


def test_description_without_numbers_passes_silently(drift) -> None:
    assert drift.run(repo="owner/name", description="A local code intelligence CLI.", truth={"commands": 281}) == 0


def test_empty_description_passes(drift) -> None:
    """An unset GitHub description makes no claim, so it cannot be wrong."""
    assert drift.extract_claims("") == []
    assert drift.run(repo="owner/name", description="", truth={"commands": 281}) == 0


# ---------------------------------------------------------------------------
# This repository's provider
# ---------------------------------------------------------------------------


def test_provider_reuses_the_readme_gate_counts(provider) -> None:
    """The description gate and the README gate must read the same source.

    If this provider ever grew its own counting logic, the two surfaces could
    drift in opposite directions and both gates would still pass.
    """
    counts = provider._load_build_readme_counts().collect_counts(REPO_ROOT)
    truth = provider.truth()
    assert truth["commands"] == counts.command_names
    assert truth["mcp tools"] == counts.mcp_full
    assert truth["canonical commands"] == counts.canonical_commands
    assert truth["categories"] == counts.category_count


def test_provider_derives_the_language_count_from_the_registry(provider) -> None:
    """The 28 in the description must come from ``_SUPPORTED_LANGUAGES``, not
    from a second hand-typed literal."""
    registry = (REPO_ROOT / "src" / "roam" / "languages" / "registry.py").read_text(encoding="utf-8")
    assert "_SUPPORTED_LANGUAGES" in registry
    assert provider.truth()["languages"] == provider.language_count()
    assert provider.language_count() > 0


def test_live_description_claims_resolve_against_this_repos_truth(drift, provider) -> None:
    """Offline stand-in for the live check: the description shape this repo
    publishes must be fully resolvable by the truth map, so the scheduled job
    reports VERIFIED/MISMATCH rather than a wall of UNVERIFIED."""
    published_shape = (
        "Local codebase intelligence CLI + MCP server for AI coding agents: SQLite code graph, "
        "28 languages, {commands} commands, {tools} MCP tools, change-safety gates, audit evidence, "
        "zero API keys."
    )
    truth = provider.truth()
    description = published_shape.format(commands=truth["commands"], tools=truth["mcp tools"])
    findings = drift.compare(drift.extract_claims(description), truth)
    assert findings and all(f.status == "verified" for f in findings)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_some_workflow_runs_the_description_drift_check() -> None:
    """A gate nobody runs is a comment. It must live in CI, not in the
    pre-commit hook — the hook has to work offline and without a token."""
    texts = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    }
    runners = [name for name, text in texts.items() if "dev/repo_description_drift.py" in text]
    assert runners, (
        "No workflow runs dev/repo_description_drift.py. The GitHub description "
        "is the one count surface no in-tree gate can see; it needs a CI job."
    )
    for name in runners:
        assert "schedule:" in texts[name], (
            f"{name} runs the description gate but not on a schedule. The description "
            "can be edited on GitHub without any commit, so a push-only trigger "
            "would never notice."
        )


def test_pre_commit_hook_does_not_require_the_network() -> None:
    """Guard the placement decision: the local hook must stay offline."""
    hook = (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    assert "repo_description_drift.py" not in hook, (
        "The description gate needs network + a GitHub token; wiring it into "
        "pre-commit would break committing on a plane and in any sandbox."
    )
