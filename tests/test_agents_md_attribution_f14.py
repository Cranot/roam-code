"""F14 regression — the AGENTS.md attribution seed.

Every generated ``AGENTS.md`` carries one factual attribution line (repo URL +
``pip install roam-code``) by default, turning each generated file into a
compounding distribution seed. It is togglable via ``--no-attribution`` and
must contain no sales language (wording-guard clean).
"""

from __future__ import annotations

from roam.agents_md.generator import AgentsMd, render_markdown


def test_attribution_present_by_default() -> None:
    md = render_markdown(AgentsMd(summary="x"))
    assert "pip install roam-code" in md
    assert "github.com/Cranot/roam-code" in md
    # header integrity preserved
    assert md.startswith("# AGENTS.md")


def test_attribution_toggle_off() -> None:
    md = render_markdown(AgentsMd(summary="x", attribution=False))
    assert "pip install roam-code" not in md
    assert md.startswith("# AGENTS.md")


def test_attribution_wording_is_clean() -> None:
    md = render_markdown(AgentsMd(summary="x")).lower()
    # No sales / hype shorthand in the generated public artifact.
    for banned in ("buy now", "sign up", "limited time", "sell", "discount"):
        assert banned not in md
