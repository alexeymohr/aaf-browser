#!/usr/bin/env python3
"""
Generate the placeholder app icon (`icon.png`, 1024x1024).

Run once at setup time, OR re-run after editing the design constants
below. The PNG is checked in; build.sh slices it into an .iconset and
runs `iconutil -c icns` to produce the final icon.icns referenced by
the PyInstaller spec.

Design intent: subdued dark background, two-letter "AB" mark in a
neutral monospace, rounded-square frame. Replace with a real logo
later by running this script (or by replacing icon.png directly).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SIZE = 1024
BG = (15, 17, 22, 255)         # near-black, matches GUI bg
ACCENT = (88, 166, 255, 255)   # GUI accent blue
RADIUS = 224                   # rounded-corner radius — close to macOS Tahoe
LETTER_FILL = ACCENT


def _find_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except (OSError, FileNotFoundError):
            continue
    # Fallback to the bundled default; not as nice but always works.
    return ImageFont.load_default()


def render(out_path: Path) -> None:
    # Compose on RGBA, mask the rounded square, then flatten.
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, SIZE - 1, SIZE - 1), radius=RADIUS, fill=255
    )
    bg_layer = Image.new("RGBA", (SIZE, SIZE), BG)
    canvas = Image.composite(bg_layer, canvas, mask)

    draw = ImageDraw.Draw(canvas)
    font = _find_font(
        [
            "/System/Library/Fonts/SFNSMono.ttf",
            "/System/Library/Fonts/Menlo.ttc",
            "/Library/Fonts/Monaco.ttf",
        ],
        size=int(SIZE * 0.45),
    )
    text = "AB"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pos = ((SIZE - tw) / 2 - bbox[0], (SIZE - th) / 2 - bbox[1] - SIZE * 0.02)
    draw.text(pos, text, fill=LETTER_FILL, font=font)

    # Subtle inner stroke for crispness on small sizes
    ImageDraw.Draw(canvas).rounded_rectangle(
        (8, 8, SIZE - 9, SIZE - 9),
        radius=RADIUS - 8,
        outline=(255, 255, 255, 24),
        width=2,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    print(f"wrote {out_path}  ({SIZE}x{SIZE})")


if __name__ == "__main__":
    here = Path(__file__).parent
    render(here / "icon.png")
