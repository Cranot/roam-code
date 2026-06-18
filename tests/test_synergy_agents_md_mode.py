"""W14.2 Synergy 3 — ``roam agents-md`` includes a "Current mode" section.

The generator must surface the active mode (resolved via
:func:`roam.modes.policy.resolve_mode`), the count of allowed commands,
representative highlights, and representative commands that the
NEXT-higher mode would unlock. The section sits between "Workflow
gates" and "Test conventions" — modes are gate-related context.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

from roam.modes import set_active_mode  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def indexed_repo(tmp_path, monkeypatch):
    """Tiny indexed Python project, monkeypatched cwd."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "service.py").write_text(
        "def greet(name):\n"
        "    return f'hi {name}'\n"
        "\n"
        "class Greeter:\n"
        "    def say(self):\n"
        "        return greet('world')\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"roam index failed: {out}"
    # Make sure no env mode leaks in.
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    return proj


# ---------------------------------------------------------------------------
# 1. AGENTS.md includes a Current-mode section with the active mode name
# ---------------------------------------------------------------------------


def test_agents_md_includes_current_mode_section(indexed_repo):
    """``generate_agents_md`` populates ``current_mode`` and renders it.

    With the active mode persisted to ``.roam/active_mode``, the
    AgentsMd dataclass surfaces the right name + count, and the
    rendered markdown contains a "## Current mode" header and a
    verbatim mention of the active mode.
    """
    from roam.agents_md.generator import generate_agents_md, render_agents_markdown
    from roam.db.connection import open_db

    # Persist a specific mode so resolution is deterministic.
    set_active_mode(indexed_repo, "safe_edit")

    with open_db(readonly=True, project_root=indexed_repo) as conn:
        am = generate_agents_md(indexed_repo, conn)

    # Structured data
    assert am.current_mode, "current_mode section should be populated"
    assert am.current_mode["name"] == "safe_edit", am.current_mode
    assert am.current_mode["allowed_count"] > 0, am.current_mode
    assert "Current mode" in am.section_names(), am.section_names()
    assert "current_mode" in am.sources, am.sources

    # Section ordering: Current mode sits AFTER Workflow gates (if
    # present) and BEFORE Test conventions.
    sections = am.section_names()
    cm_idx = sections.index("Current mode")
    if "Workflow gates" in sections:
        assert sections.index("Workflow gates") < cm_idx, sections
    if "Test conventions" in sections:
        assert cm_idx < sections.index("Test conventions"), sections

    # Rendered markdown
    md = render_agents_markdown(am)
    assert "## Current mode" in md, md
    assert "safe_edit" in md, md
    # The "switch with" hint enumerates valid modes.
    assert "roam mode" in md, md


# ---------------------------------------------------------------------------
# 2. Current-mode section lists commands the upgrade tier unlocks
# ---------------------------------------------------------------------------


def test_agents_md_mode_section_lists_blocked_command_examples(indexed_repo):
    """The section calls out what the NEXT-higher mode would unlock.

    For ``safe_edit``, the upgrade target is ``migration``; that tier
    adds commands like ``migration-plan`` / ``apply-plan``. The section
    surfaces these as backtick-quoted examples so an agent reading the
    doc immediately sees what the upgrade buys them (LAW 7 / positive
    vocabulary -- name what works at the upgrade tier, not what's
    forbidden today).
    """
    from roam.agents_md.generator import generate_agents_md, render_agents_markdown
    from roam.db.connection import open_db

    set_active_mode(indexed_repo, "safe_edit")

    with open_db(readonly=True, project_root=indexed_repo) as conn:
        am = generate_agents_md(indexed_repo, conn)

    assert am.current_mode["upgrade_to"] == "migration", am.current_mode
    blocked = am.current_mode["blocked_examples"]
    assert blocked, "expected at least one blocked-example command for safe_edit"
    assert isinstance(blocked, list)

    md = render_agents_markdown(am)
    # The blocked section calls out the upgrade target by name.
    assert "roam mode migration" in md, md
    # And at least one of the example commands lands in the rendered
    # text. We don't pin a specific name (the migration tier list can
    # evolve) but at least one of the dataclass-reported examples must
    # appear verbatim.
    assert any(f"`{cmd}`" in md for cmd in blocked), f"none of {blocked!r} appear in rendered section:\n{md}"
