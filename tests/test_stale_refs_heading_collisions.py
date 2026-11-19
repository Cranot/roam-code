"""Heading anchors must be unique even when literal suffixes collide."""

import pytest

from roam.commands.stale_refs_anchors import extract_anchors


@pytest.mark.parametrize(
    ("headings", "expected"),
    [
        (
            ["Setup", "Setup", "Setup 1"],
            {"setup", "setup-1", "setup-1-1"},
        ),
        (
            ["Setup", "Setup 1", "Setup"],
            {"setup", "setup-1", "setup-2"},
        ),
        (
            ["Setup", "Setup 1", "Setup 2", "Setup", "Setup"],
            {"setup", "setup-1", "setup-2", "setup-3", "setup-4"},
        ),
    ],
)
@pytest.mark.parametrize("style", ["atx", "setext"])
def test_heading_suffix_collisions(headings, expected, style):
    if style == "atx":
        content = "\n\n".join(f"# {heading}" for heading in headings)
    else:
        content = "\n\n".join(f"{heading}\n---" for heading in headings)
    assert extract_anchors(content) == expected


def test_ordinary_duplicate_headings():
    assert extract_anchors("# Setup\n\n## Setup\n\n### SETUP\n") == {"setup", "setup-1", "setup-2"}


def test_explicit_html_ids_remain_author_controlled():
    assert extract_anchors('# Setup\n<a id="setup"></a>\n<a id="custom"></a>') == {"setup", "custom"}
