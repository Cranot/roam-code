"""Known onboarding path contracts, not live-client or reader-success evidence."""

from __future__ import annotations

import pytest

from tests.test_homepage_contract import SITE, HomepageParser, normalized

CLIENTS = ("claude-code", "cursor", "gemini-cli", "codex-cli", "amp", "vscode", "windsurf")
OPTIONAL = ("ci-tutorial", "mcp-gateway-tutorial", "oscal-audit-tutorial")
ROUTINE = (
    "Use Roam to find relevant code and inspect indexed callers before a non-trivial edit. "
    "Read the returned source locations and limitations. After editing, refresh the index "
    "and run the project's required tests and review checks. Keep missing evidence explicit."
)


def _page(name):
    return HomepageParser((SITE / "docs" / name).read_text(encoding="utf-8"))


def _section(page, heading_id):
    return next(
        section
        for section in page.root.find("section")
        if any(heading.attrs.get("id") == heading_id for heading in section.find("h2"))
    )


def test_early_agent_setup_route_reaches_preview_apply_and_connection_guidance():
    page = _page("getting-started.html")
    next_steps = next(node for node in page.elements if node.attrs.get("id") == "whats-next")
    item = next(node for node in next_steps.find("li") if normalized(node.text()).startswith("Agent integration:"))
    links = list(item.find("a"))
    assert any(link.attrs.get("href") == "/docs/integration-tutorials#mcp-host-tutorial" for link in links)
    assert not list(item.find("code")), "Route to the complete setup instead of an unlabelled preview shortcut"
    destination = _section(_page("integration-tutorials.html"), "mcp-host-tutorial")
    text = normalized(destination.text()).lower()
    assert all(term in text for term in ("preview", "--write", "restart", "query", "connected"))


def test_preflight_step_reports_risk_without_claiming_enforcement():
    page = _page("getting-started.html")
    step = next(
        node
        for node in page.root.find("article")
        if any(normalized(heading.text()).startswith("Step 4:") for heading in node.find("h3"))
    )
    heading = normalized(next(step.find("h3")).text()).lower()
    assert "check before" in heading and "gate" not in heading
    text = normalized(step.text()).lower()
    assert "does not block" in text and "by itself" in text
    assert "roam preflight <symbol>" in normalized(next(step.find("pre")).text())
    examples = "\n".join(node.text() for node in page.root.find("pre"))
    assert "pre-edit gate:" not in examples


def test_real_configured_gate_example_is_retained():
    """Do not turn an informational-preflight correction into a global gate ban."""
    page = _page("getting-started.html")
    examples = "\n".join(node.text() for node in page.root.find("pre"))
    assert "roam health --gate" in examples
    assert "configured quality gates" in normalized(next(page.root.find("main")).text())


def test_integration_contents_has_native_named_routes_before_client_setup():
    page = _page("integration-tutorials.html")
    contents = [
        node for node in page.root.find("nav") if node.attrs.get("aria-labelledby") == "integration-contents-title"
    ]
    assert len(contents) == 1, "Give the long integration guide a named native contents navigation"
    contents = contents[0]
    label = next(node for node in contents.find("p") if node.attrs.get("id") == "integration-contents-title")
    assert normalized(label.text())
    assert page.elements.index(contents) < page.elements.index(_section(page, "mcp-host-tutorial"))
    expected = ("mcp-host-tutorial", *CLIENTS, "validate-connection", *OPTIONAL)
    links = list(contents.find("a"))
    assert [link.attrs.get("href") for link in links] == [f"#{target}" for target in expected]
    ids = [node.attrs["id"] for node in page.elements if "id" in node.attrs]
    assert len(ids) == len(set(ids))
    for target, link in zip(expected, links):
        assert ids.count(target) == 1 and normalized(link.text())
        assert not {"role", "tabindex", "onclick", "hidden"} & link.attrs.keys()
    for node in (contents, label, *contents.find("ul"), *contents.find("li"), *links):
        assert "hidden" not in node.attrs and node.attrs.get("aria-hidden") != "true"


def test_connection_routine_follows_clients_and_precedes_optional_integrations():
    page = _page("integration-tutorials.html")
    routine_position = page.elements.index(_section(page, "validate-connection"))
    assert all(page.elements.index(_section(page, client)) < routine_position for client in CLIENTS)
    assert all(routine_position < page.elements.index(_section(page, optional)) for optional in OPTIONAL)


def test_setup_matrix_routes_direct_arrivals_to_each_client_guide():
    """The early tutorial link lands below contents, so its matrix needs routes too."""
    section = _section(_page("integration-tutorials.html"), "mcp-host-tutorial")
    table = next(section.find("table"))
    destinations = {node.attrs.get("href") for node in table.find("a")}
    assert {f"#{client}" for client in CLIENTS} <= destinations


@pytest.mark.parametrize("client", CLIENTS)
def test_each_client_can_reach_the_shared_connection_and_routine_check(client):
    section = _section(_page("integration-tutorials.html"), client)
    links = [node for node in section.find("a") if node.attrs.get("href") == "#validate-connection"]
    assert links, f"{client} must not end at configuration without a route to the shared first-result check"
    assert all(normalized(link.text()) for link in links)


def test_existing_shared_routine_and_unknown_result_guidance_are_retained():
    section = _section(_page("integration-tutorials.html"), "validate-connection")
    assert normalized(next(section.find("pre")).text()) == ROUTINE
    text = normalized(section.text())
    assert "an empty list alone is unknown" in text
    assert "roam_search_symbol" in text and "roam_uses" in text
    assert "does not ensure the agent uses them" in text
