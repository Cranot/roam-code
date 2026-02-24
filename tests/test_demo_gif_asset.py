from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_references_demo_gif():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "![roam terminal demo](docs/assets/roam-terminal-demo.gif)" in text
    assert "- [x] Terminal demo GIF in README (`#26`)." in text


def test_demo_gif_asset_exists_and_non_empty():
    gif = ROOT / "docs" / "assets" / "roam-terminal-demo.gif"
    assert gif.is_file()
    assert gif.stat().st_size > 100_000
