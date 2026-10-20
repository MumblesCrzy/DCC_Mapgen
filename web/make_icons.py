#!/usr/bin/env python3
"""Draw the web page's icons: the maps' stair glyph, in the maps' colours.

    python web/make_icons.py      # needs Pillow; writes web/icon-*.png

Run once and commit the PNGs; the build only copies them. The maskable icon
keeps the glyph inside the middle 60%, where every platform's mask leaves it.
"""

import os

from PIL import Image, ImageDraw

WEB = os.path.dirname(os.path.abspath(__file__))
INK, PAPER, RED = "#222222", "#f6f2ea", "#c0392b"


def icon(size: int, glyph: float, rounded: bool) -> Image.Image:
    """glyph is the share of the icon's width the stairs take up."""
    s = 4                                     # draw large, then shrink: smooth edges
    big = size * s
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, big - 1, big - 1], radius=big // 5, fill=INK)
    else:
        d.rectangle([0, 0, big, big], fill=INK)
    # Three steps up to the right, as the maps' stairwell marker draws them.
    w = big * glyph
    x0, y0 = (big - w) / 2, (big - w) / 2 + w * 0.05
    step = w / 3
    pts = [(x0, y0 + w), (x0, y0 + 2 * step), (x0 + step, y0 + 2 * step),
           (x0 + step, y0 + step), (x0 + 2 * step, y0 + step), (x0 + 2 * step, y0),
           (x0 + w, y0), (x0 + w, y0 + w)]
    d.polygon(pts, fill=PAPER)
    # The red of a crawler's spawn point, on the bottom step.
    r = step * 0.28
    cx, cy = x0 + step / 2, y0 + 2 * step - r * 1.6
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=RED)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    for name, size, glyph, rounded in (("icon-180.png", 180, 0.62, False),
                                       ("icon-192.png", 192, 0.62, True),
                                       ("icon-512.png", 512, 0.62, True),
                                       ("icon-maskable-512.png", 512, 0.5, False)):
        icon(size, glyph, rounded).save(os.path.join(WEB, name))
        print("wrote", name)
