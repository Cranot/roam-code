"""Guard maintained documentation routing and evidence boundaries."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

import pytest

from roam.testing.ci_xdist import xdist_args_to_inject
from tests._helpers.repo_root import repo_root

ROOT = repo_root()
MAINTAINED = ["CONTRIBUTING.md"] + [
    path.relative_to(ROOT).as_posix()
    for path in sorted((ROOT / "docs").rglob("*.md"))
    if path.name not in {"COMMANDS.md", "mcp-tools.md"}
]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def _prose(text):
    return re.sub(r"^```[^\n]*\n.*?^```[^\n]*$", "", text, flags=re.MULTILINE | re.DOTALL)


def _anchors(text):
    anchors = set(re.findall(r"\b(?:id|name)=[\"\']([^\"\']+)[\"\']", text))
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", _prose(text), re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        anchors.add(slug)
    return anchors


@pytest.mark.parametrize("relative", MAINTAINED)
def test_maintained_relative_links_and_fragments_resolve(relative):
    """Check these handwritten guides, including the preserved contributor anchors.

    External availability and generated references have separate checks. This
    deliberately does not infer executable examples or rendered accessibility.
    """
    document = ROOT / relative
    links = re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", _prose(_read(relative)))
    for href in links:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc:
            continue
        target = (document.parent / unquote(parsed.path)).resolve() if parsed.path else document
        assert target.exists(), f"{relative}: missing {href}"
        if parsed.fragment and target.is_file():
            assert unquote(parsed.fragment) in _anchors(target.read_text(encoding="utf-8")), (
                f"{relative}: missing fragment in {href}"
            )


def test_release_and_website_procedures_have_one_home():
    contributors = _read("CONTRIBUTING.md")
    assert "## Version + release cadence" in contributors
    assert "## Deploys" in contributors
    assert "docs/releases.md#publish-the-verified-package" in contributors
    assert "docs/website-maintenance.md#publishing" in contributors
    assert "wrangler pages deploy" not in contributors
    assert "git tag -a" not in contributors
    release = _read("docs/releases.md")
    assert 'test -z "$release_status"' in release
    assert 'test "$(git rev-parse origin/main)" = "$release_sha"' in release
    assert 'git tag -a "v${version}" "$release_sha"' in release
    assert "--release" in release and "--network" in release
    website = _read("docs/website-maintenance.md")
    assert 'test -z "$site_status"' in website
    assert '--commit-dirty=false --commit-hash="$site_sha"' in website


def test_contributor_parallelism_matches_plugin_default():
    args = xdist_args_to_inject([], {"CI": "true"}, True)
    default = {"1": "one", "2": "two", "3": "three", "4": "four"}[args[1]]
    assert f"selects {default} workers by default" in _read("CONTRIBUTING.md")
    assert "enables `-n auto" not in _read("CONTRIBUTING.md")
    assert "ROAM_XDIST_WORKERS" in _read("CONTRIBUTING.md")


def test_caller_guide_does_not_order_different_populations():
    guide = _read("docs/concepts/caller-metrics.md")
    assert "no universal numeric ordering" in guide
    assert "historical example" in guide
    assert "Always biggest" not in guide


def test_experimental_replay_discloses_execution_and_claim_limits():
    guide = _read("docs/sibling-patch-network-v1.md")
    assert "not independent authentication" in guide
    assert "not a kernel sandbox" in guide
    assert "execute code" in guide
    assert "no push, no write, no commit" not in guide


def test_algorithm_research_routes_to_existing_discovery():
    guide = _read("docs/algo-polish-research.md")
    assert "Historical priority queue" in guide
    assert "`roam algo --list-tasks` is implemented" in guide
    assert "concepts/detector-evidence.md" in guide
