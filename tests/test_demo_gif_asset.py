from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_references_demo_gif():
    """The README must embed the terminal demo GIF (the functional ref).

    The original test also asserted a `- [x] Terminal demo GIF in README.`
    checklist line, but that lived in a pre-launch checklist section that
    was removed in v13.2's README cleanup. The load-bearing assertion is
    the actual image embed — keep that.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "![roam terminal demo](docs/assets/roam-terminal-demo.gif)" in text


def test_demo_gif_asset_exists_and_non_empty():
    gif = ROOT / "docs" / "assets" / "roam-terminal-demo.gif"
    assert gif.is_file()
    assert gif.stat().st_size > 100_000
