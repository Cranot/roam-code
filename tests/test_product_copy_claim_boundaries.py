"""Regression guards for specific first-party claims, not a prose-quality score.

These local-source checks neither verify third-party comparisons nor establish
publication, conversion, or browser accessibility. Historical benchmark prose
and generated command/changelog bodies are outside this copy-editing gate.
"""

from __future__ import annotations

import json

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 uses the declared tomli dependency.
    import tomli as tomllib

from tests.test_homepage_contract import SITE, HomepageParser, normalized


def main_text(name):
    page = HomepageParser((SITE / name).read_text(encoding="utf-8"))
    return normalized(next(page.root.find("main")).text()), page


def test_press_does_not_rewrite_license_history_or_sell_unavailable_hosting():
    text, page = main_text("press.html")
    assert "Apache 2.0 from day one" not in text
    assert "early access / planned hosted product" not in text
    assert "planned, not available to subscribe to" in text
    assert "free DIY 5-PR sample" not in text
    assert "HEAD~5..HEAD" in text
    cta = next(node for node in page.elements if node.attrs.get("class") == "page-cta-strip")
    primary = next(link for link in cta.find("a") if link.attrs.get("class") == "btn-primary")
    assert primary.attrs["href"] == "/setup"
    assert "book a PR Replay audit" not in cta.text()


def test_getting_started_does_not_make_evidence_automatic_or_sample_paid():
    text, _ = main_text("docs/getting-started.html")
    assert "Every AI-assisted change compiles into one portable evidence packet" not in text
    assert "when configured" in text
    assert "paid PR Replay — we replay your last 5 PRs" not in text
    assert "HEAD~5..HEAD" in text
    assert "Free local sample" in text
    assert "paid report" in text


def test_agent_summary_does_not_invent_adoption_or_unqualified_offline_operation():
    text = (SITE / "llms.txt").read_text(encoding="utf-8")
    assert "most teams use both" not in text
    assert "running entirely on the user's machine" not in text
    assert "every analysis can emit" not in text
    assert "Configured evidence workflows" in text
    assert "model usage" in text
    assert "network-boundary" in text


def test_homepage_model_free_heading_names_the_static_scope():
    # The prominent claim needs its own scope: optional model-assisted paths
    # exist. A nearby eyebrow or a distant FAQ cannot qualify the heading alone.
    _, page = main_text("index.html")
    section = next(node for node in page.elements if node.attrs.get("id") == "what-you-get")
    headline = normalized(next(section.find("h2")).text()).lower()
    assert "static" in headline, "Bound the no-model-call headline to static checks"
    assert "model usage" in section.text().lower(), "Keep the connected agent's usage separate"


def test_compare_current_invitation_keeps_future_subscriptions_unavailable():
    text, page = main_text("compare.html")
    assert "not available to subscribe to" in text
    cta = next(node for node in page.elements if node.attrs.get("class") == "audit-upsell")
    cta_text = normalized(cta.text())
    assert "free 5-PR DIY sample" not in cta_text
    assert "HEAD~5..HEAD" in cta_text
    assert "after Roam Review reaches general availability" in cta_text


def test_security_describes_opt_in_records_and_the_actual_site_scripts():
    text, _ = main_text("security.html")
    assert "HMAC-chained run ledger on every analysis" not in text
    assert "No JavaScript except Cloudflare's email-obfuscation helper" not in text
    assert "when configured" in text
    assert "code atlas" in text


def test_next_steps_sample_is_a_commit_range_not_five_pr_identities():
    text, _ = main_text("thank-you.html")
    assert "The 5-PR sample" not in text
    assert "HEAD~5..HEAD" in text
    assert "does not rerun tests" in text


def test_readme_faq_and_demo_keep_availability_and_network_boundaries():
    text = (SITE.parents[2] / "README.md").read_text(encoding="utf-8")
    assert "Roam Review is a hosted PR bot" not in text
    assert "five commands, no laptop egress" not in text
    faq = normalized(text.split("## FAQ", 1)[1].split("## Limitations", 1)[0])
    assert "planned" in faq and "not available to subscribe to" in faq


def test_equivalent_agent_install_alternatives_include_optional_mcp_dependencies():
    readme = (SITE.parents[2] / "README.md").read_text(encoding="utf-8")
    guide = (SITE / "docs/getting-started.html").read_text(encoding="utf-8")
    for text in (readme, guide):
        assert 'pipx install "roam-code[mcp]"' in text
        assert 'uv tool install "roam-code[mcp]"' in text


def test_compare_separates_duplicate_candidates_and_local_defaults_from_guarantees():
    text, _ = main_text("compare.html")
    assert "Catches the clone-not-edited bug AI keeps shipping" not in text
    assert "Runs locally — source never uploaded" not in text
    assert "persisted duplicate-code results" in text
    assert "not proof of a bug" in text


def test_package_descriptions_share_the_bounded_network_claim():
    root = SITE.parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    codemeta = json.loads((root / "codemeta.json").read_text(encoding="utf-8"))
    assert project["description"] == codemeta["description"]
    assert "no source-code egress" not in project["description"]
    assert "no automatic source-code upload" in project["description"]


def test_advertised_homepage_platform_guides_exist_at_the_destination():
    # Named-guide promises, not an exhaustive natural-language classifier.
    page = HomepageParser((SITE / "index.html").read_text(encoding="utf-8"))
    guide_line = next(normalized(node.text()) for node in page.root.find("p") if "Setup guides for" in node.text())
    tutorials = HomepageParser((SITE / "docs/integration-tutorials.html").read_text(encoding="utf-8"))
    anchors = {node.attrs.get("id") for node in tutorials.elements}
    for name, anchor in {
        "Claude Code": "claude-code",
        "Cursor": "cursor",
        "Codex": "codex-cli",
        "Gemini CLI": "gemini-cli",
        "Amp": "amp",
        "VS Code": "vscode",
        "Windsurf": "windsurf",
    }.items():
        if name in guide_line:
            assert anchor in anchors, f"{name} has no guide at the advertised destination"


def test_setup_describes_tour_as_repository_onboarding_not_command_onboarding():
    text, _ = main_text("setup.html")
    assert "guided walk through the five core verbs" not in text
    tour_description = text.split("roam tour", 1)[1].split("Full reference", 1)[0]
    assert "project" in tour_description and "reading order" in tour_description


def test_setup_reaches_a_first_result_not_only_configuration():
    # A working connection is not evidence that it targets the intended repo.
    # Keep the local step and its detailed validation destination reachable.
    _, page = main_text("setup.html")
    step = next((node for node in page.elements if node.attrs.get("id") == "first-result"), None)
    assert step is not None, "Setup needs a first-result validation step"
    assert any(link.attrs.get("href") == "/docs/integration-tutorials#validate-connection" for link in step.find("a"))
    text = normalized(step.text()).lower()
    assert "project" in text and "source locations" in text and "incomplete" in text


def test_compare_demo_invitation_does_not_imply_installation_is_offline():
    _, page = main_text("compare.html")
    categories = next(node for node in page.elements if node.attrs.get("id") == "categories")
    invitation = normalized(
        next(node for node in categories.find("p") if node.attrs.get("class") == "review-layers-foot").text()
    )
    assert "without leaving the laptop" not in invitation
    assert "Installation and parser downloads" in invitation
    assert "network access" in invitation


def test_homepage_does_not_promise_hooks_at_an_mcp_setup_only_destination():
    page = HomepageParser((SITE / "index.html").read_text(encoding="utf-8"))
    tutorials = HomepageParser((SITE / "docs/integration-tutorials.html").read_text(encoding="utf-8"))
    for link in page.root.find("a"):
        if link.attrs.get("href", "").split("#", 1)[0] == "/docs/integration-tutorials":
            if "hooks" in link.text().lower():
                assert "hooks" in tutorials.root.text().lower(), "Linked guide does not document hooks"


def test_active_writing_guidance_does_not_teach_an_absolute_no_egress_claim():
    text = (SITE.parents[2] / "docs/understanding-roam.md").read_text(encoding="utf-8")
    assert '"Your source never leaves the machine"' not in text
