"""Draw Glancer's icon: assets/glancer.ico (16-256 px) and assets/glancer.png (256 px).

A dark rounded tile holding a blue lens with a white play triangle: a quick
look at anything, video, music or photo.

    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024  # drawn large, then scaled down per icon size
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
OUT = Path(__file__).resolve().parent.parent / "assets"


def _gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    column = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return column.resize((size, size))


def draw() -> Image.Image:
    s = SIZE
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Tile with a soft shadow.
    margin = round(s * 0.06)
    radius = round(s * 0.22)
    shadow = Image.new("L", (s, s), 0)
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin + s * 0.015, s - margin, s - margin + s * 0.015), radius, fill=110)
    icon.paste((0, 0, 0, 255), (0, 0), shadow.filter(ImageFilter.GaussianBlur(s * 0.02)))
    tile_mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle((margin, margin, s - margin, s - margin), radius, fill=255)
    icon.paste(_gradient(s, (52, 56, 64), (24, 26, 31)), (0, 0), tile_mask)

    # Lens: accent-blue disc with a lighter rim.
    c, r = s / 2, s * 0.30
    lens_mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(lens_mask).ellipse((c - r, c - r, c + r, c + r), fill=255)
    icon.paste(_gradient(s, (110, 214, 255), (30, 110, 235)), (0, 0), lens_mask)
    ImageDraw.Draw(icon).ellipse((c - r, c - r, c + r, c + r), outline=(170, 232, 255, 255),
                                 width=round(s * 0.018))

    # Play triangle, nudged right so it looks centred.
    h = r * 0.95
    left, right = c - h * 0.38, c + h * 0.55
    ImageDraw.Draw(icon).polygon([(left, c - h / 2), (left, c + h / 2), (right, c)],
                                 fill=(255, 255, 255, 255))
    return icon


def main() -> None:
    OUT.mkdir(exist_ok=True)
    big = draw()
    frames = [big.resize((n, n), Image.Resampling.LANCZOS) for n in SIZES]
    frames[-1].save(OUT / "glancer.ico", sizes=[(n, n) for n in SIZES], append_images=frames[:-1])
    frames[-1].save(OUT / "glancer.png")
    print("wrote", OUT / "glancer.ico", OUT / "glancer.png")


if __name__ == "__main__":
    main()
