"""HTML IDs are global attributes, not a fixed list of Markdown tags."""

from __future__ import annotations

import json

import pytest

from roam.commands.stale_refs_anchors import _read_anchors_for, extract_anchors
from tests import test_w1527_stale_refs_unreadable_files as fixture_module
from tests.conftest import invoke_cli

refs_project = fixture_module.refs_project


@pytest.mark.parametrize("tag", ["main", "nav", "header", "footer", "article", "input", "button", "x-panel"])
def test_global_html_ids_are_recognized(tag):
    assert "target" in extract_anchors(f'<{tag} title="a > b" id=target>')


@pytest.mark.parametrize(
    "content",
    [
        '<!-- <div id="phantom"> -->',
        "<script>const sample = '<div id=\"phantom\">';</script>",
        '```html\n<div id="phantom">\n```',
        '````html\n```\n<div id="phantom">\n````',
        '<div name="phantom">',
    ],
)
def test_inert_or_non_anchor_names_do_not_hide_missing_targets(content):
    assert "phantom" not in extract_anchors(content)


def test_multiline_entities_and_legacy_named_anchor():
    assert {"a&b", "legacy", "heading"} <= extract_anchors('# Heading\n<main\n id="a&amp;b">\n<a name="legacy"></a>')


def test_duplicate_attribute_keeps_first_value():
    assert extract_anchors('<main id="first" id="phantom">') == {"first"}


@pytest.mark.parametrize("failure", [ValueError, AssertionError])
def test_parser_failure_is_unknown_in_reader(tmp_path, monkeypatch, failure):
    from roam.commands import stale_refs_anchors as anchors

    # HTMLParser tolerates many malformed declarations. Inject an actual
    # parser failure rather than assuming malformed-looking HTML must raise.
    def fail(*args, **kwargs):
        raise failure("controlled parser refusal")

    monkeypatch.setattr(anchors._HTMLAnchorParser, "feed", fail)
    path = tmp_path / "broken.html"
    path.write_text('<main id="main">', encoding="utf-8")
    assert _read_anchors_for(path) is None


@pytest.mark.parametrize("present", [False, True])
def test_real_cli_accepts_main_id_but_retains_missing_anchor(cli_runner, refs_project, present):
    (refs_project / "docs/big.md").write_text("No links.\n", encoding="utf-8")
    (refs_project / "README.md").write_text("[content](page.html#main)\n", encoding="utf-8")
    page = '<main id="main">Real content</main>' if present else "<main>Missing anchor</main>"
    (refs_project / "page.html").write_text(page, encoding="utf-8")
    result = invoke_cli(cli_runner, ["--json", "--budget", "0", "stale-refs", "--gate"], cwd=refs_project)
    assert result.exit_code == (0 if present else 5), result.output
    summary = json.loads(result.stdout)["summary"]
    assert summary["refs_checked"] == 1
    assert summary["stale_refs"] == (0 if present else 1)
    assert summary["partial_success"] is False
