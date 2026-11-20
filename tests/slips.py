"""Render sportsbook-style bet slips for OCR tests and the Discord fixture page (no real sportsbook branding)."""
from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw, ImageFont

FONTS = ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Helvetica.ttc"]
BOLD = ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"]


def _font(paths, size):
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


SLIPS = {
    # name: (lines as (text, size, bold, dim), dark mode)
    "straight_ml": ([
        ("Bet Placed", 30, True, False), ("STRAIGHT", 20, True, True), ("", 10, False, False),
        ("Detroit Pistons", 34, True, False), ("Moneyline", 24, False, True), ("-280", 30, True, False),
        ("Toronto Raptors @ Detroit Pistons", 22, False, True), ("Today 7:30 PM", 20, False, True), ("", 10, False, False),
        ("Wager $50.00", 24, False, False), ("To Pay $67.86", 24, False, False), ("Bet ID: 2611200193", 18, False, True),
    ], True),
    "light_ml": ([
        ("Single", 22, True, True), ("Calgary Flames", 32, True, False), ("+135", 28, True, False),
        ("Money Line", 22, False, True), ("Minnesota Wild @ Calgary Flames", 20, False, True),
        ("Stake $20.00   Payout $47.00", 22, False, False),
    ], False),
    "parlay": ([
        ("3 Leg Parlay", 30, True, False), ("+596", 28, True, False), ("Detroit Pistons", 26, False, False),
        ("Moneyline", 20, False, True), ("Boston Bruins", 26, False, False), ("Moneyline", 20, False, True),
        ("Dallas Stars", 26, False, False), ("Moneyline", 20, False, True), ("Wager $10.00", 22, False, False),
    ], True),
    "spread": ([
        ("STRAIGHT", 20, True, True), ("Detroit Pistons -7.5", 32, True, False), ("Spread", 22, False, True),
        ("-110", 28, True, False), ("Wager $55.00", 22, False, False),
    ], True),
}


def render(name: str) -> bytes:
    lines, dark = SLIPS[name]
    w = 720
    h = 60 + sum(size + 18 for _, size, _, _ in lines)
    bg, fg, dim = ((24, 26, 31), (240, 241, 245), (150, 155, 165)) if dark else ((250, 250, 252), (20, 22, 28), (95, 100, 110))
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    y = 30
    for text, size, bold, is_dim in lines:
        if text:
            d.text((36, y), text, fill=dim if is_dim else fg, font=_font(BOLD if bold else FONTS, size))
        y += size + 18
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    for n in SLIPS:
        with open(os.path.join(out, f"{n}.png"), "wb") as f:
            f.write(render(n))
    print("wrote", ", ".join(SLIPS))
