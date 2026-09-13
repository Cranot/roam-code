"""Keep reviewed accessible SVG text while retaining metadata leak controls."""

from __future__ import annotations

import subprocess
import sys

from scripts.strip_metadata import _scan_svg, _strip_svg
from tests._helpers.repo_root import repo_root

TITLE = b'<title id="title">Inside Roam: Python import connections</title>'
DESC = b'<desc id="desc">Source-derived import map grouped by area. Larger circles contain more Python files. Not a runtime call graph.</desc>'


def svg(tmp_path, content):
    path = tmp_path / "atlas.svg"
    path.write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc">' + content + b"</svg>"
    )
    return path


def test_reviewed_product_accessibility_text_is_not_identifying_metadata(tmp_path):
    path = svg(tmp_path, TITLE + DESC)
    assert _scan_svg(path) == {}
    assert _strip_svg(path) is False
    assert TITLE in path.read_bytes() and DESC in path.read_bytes()


def test_benign_first_element_does_not_hide_later_private_metadata(tmp_path):
    private = b"<title>Private author name</title><desc>Private workstation path</desc>"
    path = svg(tmp_path, TITLE + DESC + private)
    assert set(_scan_svg(path)) == {"pattern_1", "pattern_2"}
    assert _strip_svg(path) is True
    assert TITLE in path.read_bytes() and DESC in path.read_bytes()
    assert b"Private" not in path.read_bytes()
    assert _scan_svg(path) == {}


def test_near_match_with_added_content_is_still_flagged(tmp_path):
    path = svg(tmp_path, TITLE.replace(b"connections</title>", b"connections by Private Author</title>"))
    assert "pattern_1" in _scan_svg(path)


def test_metadata_container_is_not_exempted_by_a_benign_child(tmp_path):
    path = svg(tmp_path, b"<metadata>" + TITLE + b"private provenance</metadata>" + TITLE + DESC)
    assert "pattern_0" in _scan_svg(path)
    assert _strip_svg(path) is True
    assert b"metadata" not in path.read_bytes()
    assert TITLE in path.read_bytes() and DESC in path.read_bytes()


def test_real_cli_accepts_reviewed_text_and_rejects_extra_metadata(tmp_path):
    """Real local CLI against a temporary file; no network or shared filesystem state."""
    path = svg(tmp_path, TITLE + DESC)
    script = repo_root() / "scripts/strip_metadata.py"
    argv = [sys.executable, str(script), "--files", path.name]
    clean = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    path.write_bytes(path.read_bytes().replace(b"</svg>", b"<title>Private author</title></svg>"))
    private = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert private.returncode == 1, private.stdout + private.stderr
