#!/usr/bin/env python3
"""Generate the README terminal demo GIF.

Usage:
  python dev/generate_terminal_demo_gif.py
  python dev/generate_terminal_demo_gif.py --output docs/assets/roam-terminal-demo.gif
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1120
HEIGHT = 640
PADDING_X = 48
PADDING_Y = 46
HEADER_H = 40
LINE_SPACING = 10

BG = (7, 11, 20)
CARD = (14, 22, 36)
CARD_BORDER = (36, 51, 78)
HEADER_BG = (18, 30, 48)
TEXT = (226, 237, 255)
SUBTLE = (151, 168, 196)
PROMPT = (120, 210, 144)
ACCENT = (104, 185, 255)


SCENES: list[tuple[str, list[str], int]] = [
    (
        "Initialize graph index",
        [
            "$ roam init",
            "Indexing 1268 files...",
            "Done: 8421 symbols, 16234 edges",
        ],
        4,
    ),
    (
        "Ask for architecture context",
        [
            "$ roam context OrderService",
            "Callers: 19  Callees: 7  Affected tests: 11",
            "Read first: src/orders/service.py:43-312",
            "Then: src/orders/repo.py, src/payments/client.py",
        ],
        4,
    ),
    (
        "Run a hard quality gate",
        [
            "$ roam health --gate",
            "Health score: 93/100  PASS",
            "Cycles: 0  Tangle: 0.03  Critical: 0",
        ],
        5,
    ),
    (
        "Get refactoring guidance",
        [
            "$ roam suggest-refactoring --top 3",
            "1) build_plan(): split branch planner and validation",
            "2) apply_rules(): extract strategy map",
            "3) _render(): isolate side effects from transforms",
        ],
        5,
    ),
    (
        "Keep architecture healthy continuously",
        [
            "$ roam watch --guardian --guardian-report .roam/reports/guardian.jsonl",
            "[watch] reindex complete in 2.1s",
            "[guardian] health PASS | drift 0.21 stable | trend improving",
        ],
        7,
    ),
]


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/consolab.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/Library/Fonts/Menlo.ttc",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_terminal(lines: list[str], subtitle: str, show_cursor: bool) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    box = (36, 36, WIDTH - 36, HEIGHT - 36)
    draw.rounded_rectangle(box, radius=24, fill=CARD, outline=CARD_BORDER, width=2)

    header = (box[0], box[1], box[2], box[1] + HEADER_H)
    draw.rounded_rectangle(header, radius=24, fill=HEADER_BG)
    # Square off the lower corners of header so body joins smoothly.
    draw.rectangle((box[0], header[3] - 24, box[2], header[3]), fill=HEADER_BG)

    for i, color in enumerate(((251, 95, 92), (251, 189, 64), (52, 200, 89))):
        cx = box[0] + 22 + i * 18
        cy = box[1] + 20
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=color)

    title_font = _load_font(22)
    label_font = _load_font(16)
    body_font = _load_font(24)
    text_x = box[0] + PADDING_X
    text_y = box[1] + HEADER_H + PADDING_Y

    draw.text((box[0] + 82, box[1] + 10), "roam terminal demo", font=title_font, fill=TEXT)
    draw.text((box[2] - 360, box[1] + 13), subtitle, font=label_font, fill=ACCENT)

    line_h = body_font.getbbox("Ag")[3] + LINE_SPACING
    for line in lines:
        color = PROMPT if line.startswith("$ ") else TEXT
        draw.text((text_x, text_y), line, font=body_font, fill=color)
        text_y += line_h

    if show_cursor and lines:
        last = lines[-1]
        last_y = text_y - line_h
        cursor_x = text_x + body_font.getlength(last) + 6
        draw.rectangle((cursor_x, last_y + 5, cursor_x + 10, last_y + 30), fill=SUBTLE)

    return image


def build_frames() -> tuple[list[Image.Image], list[int]]:
    frames: list[Image.Image] = []
    durations: list[int] = []
    visible: list[str] = []

    for subtitle, lines, hold_count in SCENES:
        for line in lines:
            visible.append(line)
            frames.append(_draw_terminal(visible, subtitle, show_cursor=True))
            durations.append(250)

        for i in range(hold_count):
            frames.append(_draw_terminal(visible, subtitle, show_cursor=(i % 2 == 0)))
            durations.append(170)

        if len(visible) > 11:
            visible = visible[-11:]

    return frames, durations


def generate(output: Path) -> None:
    frames, durations = build_frames()
    output.parent.mkdir(parents=True, exist_ok=True)

    palette_frames = [frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=192) for frame in frames]
    palette_frames[0].save(
        output,
        save_all=True,
        append_images=palette_frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Generate README terminal demo GIF")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "docs" / "assets" / "roam-terminal-demo.gif",
        help="Output GIF path",
    )
    args = parser.parse_args()

    output = args.output if args.output.is_absolute() else (root / args.output)
    generate(output)
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
