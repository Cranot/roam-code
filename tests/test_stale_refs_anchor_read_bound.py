"""Anchor reads preserve multiline semantics without unbounded allocations."""

from __future__ import annotations

import pytest

from roam.commands import stale_refs_anchors as anchors


def test_anchor_growth_after_stat_is_unknown(tmp_path, monkeypatch):
    path = tmp_path / "growing.md"
    path.write_text("# Small\n", encoding="utf-8")
    real_open = open

    def grow_then_open(target, *args, **kwargs):
        # Fixture-local I/O at the real read boundary; no timing or threads.
        path.write_bytes(b"# Hidden\n" + b"x" * 2048)
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr(anchors, "open", grow_then_open, raising=False)
    assert anchors._read_anchors_for(path, max_bytes=1024) is None


@pytest.mark.parametrize(
    "raw",
    [
        b'# Heading\r\n# Heading\r\n<div\n id="multi-line"></div>\n',
        b"Title\n=====\n```\n# Excluded\n```\n# Included\n",
        '# Café\r<div id="café"></div>\n'.encode(),
        b"# Invalid \xff\n",
        b"",
    ],
)
def test_anchor_exact_byte_limit_preserves_parser(tmp_path, raw):
    # Compare actual bytes against the existing complete-text parser. This
    # covers duplicate slugs, fences, setext and multiline HTML IDs.
    path = tmp_path / "anchors.md"
    path.write_bytes(raw)
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    assert anchors._read_anchors_for(path, max_bytes=len(raw)) == anchors.extract_anchors(text)


def test_anchor_unreadable_remains_unknown(tmp_path):
    assert anchors._read_anchors_for(tmp_path / "missing.md") is None
