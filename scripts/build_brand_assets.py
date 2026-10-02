"""Export the existing navigation identity as font-independent SVG assets.

Optional authoring dependency: fonttools[woff]. No website runtime dependency.
Run --write after changing the nav mark, brand tokens or bundled typeface.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "templates/distribution/landing-page"


def build() -> dict[str, str]:
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    css = (SITE / "landing.css").read_text(encoding="utf-8")
    colors = {key: re.search(rf"--{key}:\s*(#[0-9a-fA-F]{{6}})", css)[1] for key in ("ink", "accent")}
    logo_css = re.search(r"\.site-logo\s*\{([^}]+)\}", css)[1]
    weight = int(re.search(r"font-weight:\s*(\d+)", logo_css)[1])
    spacing = float(re.search(r"letter-spacing:\s*(-?[\d.]+)em", logo_css)[1])
    source = (SITE / "index.html").read_text(encoding="utf-8")
    mark = re.search(r'<svg class="site-logo-mark".*?</svg>', source, re.S)[0]
    mark_tree = ET.fromstring(mark)
    # Preserve the live mark's geometry; only its surrounding layout changes.
    mark_body = "\n".join(ET.tostring(child, encoding="unicode").strip() for child in mark_tree)
    font_path = SITE / "fonts/space-grotesk-variable.woff2"
    font_hash = hashlib.sha256(font_path.read_bytes()).hexdigest()
    font = instantiateVariableFont(TTFont(font_path), {"wght": weight}, inplace=False)
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    scale = 64 / font["head"].unitsPerEm
    outlines = []
    cursor = 0.0
    bounds = []
    for character in "roam.":
        glyph = glyphs[cmap[ord(character)]]
        pen = SVGPathPen(glyphs)
        glyph.draw(TransformPen(pen, (scale, 0, 0, -scale, cursor, 0)))
        outlines.append((character, pen.getCommands()))
        box = BoundsPen(glyphs)
        glyph.draw(box)
        bounds.append(box.bounds)
        cursor += glyph.width * scale + 64 * spacing
    height = 96
    top = min(-box[3] * scale for box in bounds)
    bottom = max(-box[1] * scale for box in bounds)
    baseline = (height - bottom - top) / 2
    width = math.ceil(16 + 44 + 16 + cursor + 16)
    result = {}
    for name, ink, accent in (
        ("roam-logo", colors["ink"], colors["accent"]),
        ("roam-logo-mono", colors["ink"], colors["ink"]),
        ("roam-logo-white", "#ffffff", "#ffffff"),
    ):
        paths = "\n".join(f'<path fill="{accent if char == "." else ink}" d="{path}"/>' for char, path in outlines)
        result[name] = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">\n'
            '<title id="title">Roam</title>\n'
            f"<!-- Space Grotesk {weight}; font-sha256: {font_hash}; outlined, no font download -->\n"
            f'<g transform="translate(16 26) scale(1.375)" fill="none" stroke="{accent}" color="{accent}">\n{mark_body}\n</g>\n'
            f'<g transform="translate(76 {baseline:.6f})">\n{paths}\n</g>\n</svg>\n'
        )
    result["roam-mark"] = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" role="img" aria-labelledby="title">\n'
        '<title id="title">Roam mark</title>\n'
        f'<g transform="translate(8 8)" fill="none" stroke="{colors["accent"]}" color="{colors["accent"]}">\n{mark_body}\n</g>\n</svg>\n'
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    output = SITE / "brand"
    changes = []
    for name, content in build().items():
        path = output / f"{name}.svg"
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            changes.append(name)
            if args.write:
                output.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
    print(f"4 SVG assets checked; {len(changes)} {'updated' if args.write else 'with drift'}: {changes}")
    return 0 if args.write or not changes else 1


if __name__ == "__main__":
    raise SystemExit(main())
