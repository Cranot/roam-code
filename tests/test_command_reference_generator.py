"""Website command summaries retain source text without importing commands."""

from __future__ import annotations

import re
import sys
from html import unescape
from html.parser import HTMLParser

import pytest

from dev import build_command_reference as generator
from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS


class ReferenceRows(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.rows = []
        self.ids = []
        self.links = []
        self._row = None
        self._cell = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "tr":
            self._row = {"id": attrs.get("id"), "cells": [], "links": []}
        elif tag == "td":
            self._cell = ""
        elif tag == "a":
            self.links.append(attrs.get("href"))
            if self._row is not None:
                self._row["links"].append(attrs.get("href"))

    def handle_data(self, data):
        if self._cell is not None:
            self._cell += data

    def handle_endtag(self, tag):
        if tag == "td" and self._row is not None:
            self._row["cells"].append(self._cell.strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row["cells"]:
                self.rows.append(self._row)
            self._row = None


@pytest.fixture(scope="module")
def reference():
    return ReferenceRows(generator._build_appendix())


def test_every_registered_command_and_alias_appears_exactly_once(reference):
    names = [row["cells"][0].removeprefix("roam ") for row in reference.rows]
    assert len(names) == len(set(names)) == len(_COMMANDS)
    assert set(names) == set(_COMMANDS)


def test_every_registered_command_has_a_description(reference):
    empty = [row["cells"][0] for row in reference.rows if not row["cells"][1]]
    assert empty == []


def test_long_real_summary_is_complete(reference):
    rows = {row["cells"][0]: row["cells"][1] for row in reference.rows}
    assert rows["roam workflow"] == "Inspect a workflow recipe DAG, review lenses, and next commands."


def test_registered_commands_have_stable_unique_self_links(reference):
    for row in reference.rows:
        expected = "command-" + row["cells"][0].removeprefix("roam ")
        assert row["id"] == expected
        assert "#" + expected in row["links"]
    assert len(reference.ids) == len(set(reference.ids))


def test_legacy_aliases_keep_rows_and_link_to_their_replacements(reference):
    rows = {row["cells"][0]: row for row in reference.rows}
    for alias, notice in _DEPRECATED_COMMANDS.items():
        replacement = notice["replacement"] if isinstance(notice, dict) else notice
        row = rows["roam " + alias]
        assert "Legacy alias" in row["cells"][1]
        assert "#command-" + replacement in row["links"]


def _source_fixture(tmp_path, monkeypatch, source, commands=None, categories=None):
    module = tmp_path / "src" / "reference_fixture.py"
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "_COMMANDS", commands or {"sample": ("reference_fixture", "sample")})
    monkeypatch.setattr(generator, "_CATEGORIES", categories or {"Fixtures": ["sample"]})
    monkeypatch.setattr(generator, "_DEPRECATED_COMMANDS", {}, raising=False)
    return module


@pytest.mark.parametrize("alias_binding", ["sample = middle", "sample: object = middle", "unused = sample = middle"])
def test_ast_aliases_preserve_complete_first_paragraph_without_imports(tmp_path, monkeypatch, alias_binding):
    summary = "A deliberately long description with <tags> & values that extends beyond the CLI help cutoff."
    source = (
        'raise AssertionError("command modules must never be imported")\n'
        "async def actual():\n"
        '    """A deliberately long description with <tags> & values\n'
        "    that extends beyond the CLI help cutoff.\n\n"
        '    A separate details paragraph should not be copied.\n    """\n'
        "middle = actual\n" + alias_binding + "\n"
    )
    _source_fixture(tmp_path, monkeypatch, source)
    appendix = generator._build_appendix()
    rows = ReferenceRows(appendix).rows
    assert rows[0]["cells"][1] == summary
    assert "&lt;tags&gt; &amp; values" in appendix
    assert "separate details" not in appendix
    assert "reference_fixture" not in sys.modules


def test_categories_deduplicate_rows_and_retain_uncategorized_commands(tmp_path, monkeypatch):
    _source_fixture(
        tmp_path,
        monkeypatch,
        'def actual():\n    """Complete description."""\n',
        commands={name: ("reference_fixture", "actual") for name in ("sample", "leftover")},
        categories={"First": ["sample", "sample", "not-registered"], "Second": ["sample"]},
    )
    appendix = generator._build_appendix()
    assert [row["cells"][0] for row in ReferenceRows(appendix).rows] == ["roam sample", "roam leftover"]
    assert "<h3>Other</h3>" in appendix
    assert "<h3>Second</h3>" not in appendix


@pytest.mark.parametrize(
    "source",
    [
        "def sample():\n    pass\n",
        'def another():\n    """Not the registered function."""\n',
        "sample = other\nother = sample\n",
        "sample = command_factory()\n",
        "this is not valid Python !\n",
    ],
)
def test_unresolved_or_undocumented_commands_fail_explicitly(tmp_path, monkeypatch, source):
    _source_fixture(tmp_path, monkeypatch, source)
    with pytest.raises(ValueError, match="sample"):
        generator._build_appendix()


def _reference_target(tmp_path, text):
    target = tmp_path / "templates" / "distribution" / "landing-page" / "docs" / "command-reference.html"
    target.parent.mkdir(parents=True)
    target.write_text(text, encoding="utf-8")
    return target


def test_missing_source_leaves_existing_reference_untouched(tmp_path, monkeypatch, capsys):
    module = _source_fixture(tmp_path, monkeypatch, 'def sample():\n    """Description."""\n')
    module.unlink()
    original = "<main><!-- BEGIN auto-reference -->keep me<!-- END auto-reference --></main>"
    target = _reference_target(tmp_path, original)
    assert generator.main() == 1
    assert target.read_text(encoding="utf-8") == original
    assert "sample" in capsys.readouterr().err


def test_generation_preserves_curated_anchors_and_is_idempotent(tmp_path, monkeypatch):
    _source_fixture(tmp_path, monkeypatch, 'def sample():\n    """Description."""\n')
    prefix = '<main><h1 id="sample">Curated heading</h1><!-- BEGIN auto-reference -->'
    suffix = '<!-- END auto-reference --><a href="#sample">Curated link</a></main>'
    target = _reference_target(tmp_path, prefix + "old generated rows" + suffix)
    assert generator.main() == 0
    result = target.read_text(encoding="utf-8")
    assert result.startswith(prefix)
    assert result.endswith(suffix)
    assert generator._build_appendix() in result
    assert len(ReferenceRows(result).ids) == len(set(ReferenceRows(result).ids))
    first_bytes = target.read_bytes()
    assert generator.main() == 0
    assert target.read_bytes() == first_bytes


def test_replacement_keeps_literal_backslashes(tmp_path, monkeypatch):
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    appendix = r"<section>Describe literal \b and \1 sequences.</section>"
    monkeypatch.setattr(generator, "_build_appendix", lambda: appendix)
    target = _reference_target(tmp_path, "<main><!-- BEGIN auto-reference -->old<!-- END auto-reference --></main>")
    assert generator.main() == 0
    assert appendix in target.read_text(encoding="utf-8")


def test_regeneration_preserves_mixed_newlines_outside_owned_markers(tmp_path, monkeypatch):
    _source_fixture(tmp_path, monkeypatch, 'def sample():\n    """Description."""\n')
    prefix = b'<main>\r\n<h1 id="sample">Curated heading</h1>\n<!-- BEGIN auto-reference -->'
    suffix = b'<!-- END auto-reference -->\n<a href="#sample">Curated link</a>\r\n</main>'
    target = _reference_target(tmp_path, "")
    target.write_bytes(prefix + b"old generated rows" + suffix)
    assert generator.main() == 0
    result = target.read_bytes()
    assert result.startswith(prefix)
    assert result.endswith(suffix)


@pytest.mark.parametrize(
    "markers",
    [
        "<!-- END auto-reference -->",
        "<!-- BEGIN auto-reference -->",
        "<!-- END auto-reference --><!-- BEGIN auto-reference -->",
        "<!-- BEGIN auto-reference --><!-- END auto-reference -->"
        "<!-- BEGIN auto-reference --><!-- END auto-reference -->",
        "<!-- BEGIN auto-reference --><!-- BEGIN auto-reference --><!-- END auto-reference -->",
        "<!-- BEGIN auto-reference --><!-- END auto-reference --><!-- END auto-reference -->",
    ],
    ids=["missing-begin", "missing-end", "reversed", "duplicate-pairs", "duplicate-begin", "duplicate-end"],
)
def test_malformed_markers_refuse_without_changing_the_reference(tmp_path, monkeypatch, capsys, markers):
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "_build_appendix", lambda: "<section>Replacement</section>")
    target = _reference_target(tmp_path, "<main>\r\nCurated content\n" + markers + "\r\n</main>")
    original = target.read_bytes()
    assert generator.main() == 1
    assert target.read_bytes() == original
    assert "marker" in capsys.readouterr().err.lower()


def test_first_generation_with_no_markers_still_inserts_before_main_end(tmp_path, monkeypatch):
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    appendix = "<section>Generated</section>"
    monkeypatch.setattr(generator, "_build_appendix", lambda: appendix)
    target = _reference_target(tmp_path, '<main><h1 id="curated">Curated</h1></main><footer>Retained</footer>')
    assert generator.main() == 0
    result = target.read_text(encoding="utf-8")
    assert result.startswith('<main><h1 id="curated">Curated</h1>')
    assert result.endswith("</main><footer>Retained</footer>")
    assert result.count("<!-- BEGIN auto-reference -->") == result.count("<!-- END auto-reference -->") == 1
    assert appendix in result


def test_generated_html_does_not_embed_unescaped_description_markup(reference):
    # The real registry remains text, not a second source of HTML elements.
    for row in reference.rows:
        assert len(row["cells"]) == 2
        assert unescape(row["cells"][0]).startswith("roam ")
    assert all(re.fullmatch(r"command-[a-z0-9-]+", row["id"] or "") for row in reference.rows)
