"""
Square logo images for Google Business Profile, LinkedIn and similar.

    python make_profile_images.py

Writes 1024x1024 PNGs to static/images/profile/. Profiles often crop logos to a
circle, so the mark sits centred with a wide margin and the background is
solid: transparent PNGs show up badly on those services.
"""

import os

from PIL import Image, ImageDraw

from make_images import ACCENT, INK, PAPER, draw_mark, font

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "static", "images", "profile")
SIZE = 1024


def square(background, mark_size, filename, wordmark=False):
    img = Image.new("RGBA", (SIZE, SIZE), background + (255,))
    y = (SIZE - mark_size) // 2 - (SIZE // 10 if wordmark else 0)
    draw_mark(img, (SIZE - mark_size) // 2, y, mark_size)
    if wordmark:
        d = ImageDraw.Draw(img)
        f = font(150)
        text = "dooster"
        w = d.textbbox((0, 0), text, font=f)[2]
        colour = PAPER if background == INK else INK
        d.text(((SIZE - w) // 2, y + mark_size + 60), text, font=f, fill=colour)
    img.convert("RGB").save(os.path.join(OUT, filename), optimize=True)
    return filename


def main():
    os.makedirs(OUT, exist_ok=True)
    made = [
        # the mark on its own: safest for circular crops
        square(INK, 520, "dooster-logo-dark.png"),
        square(PAPER, 520, "dooster-logo-light.png"),
        # mark plus name: for profiles that show the logo as a square
        square(INK, 380, "dooster-logo-wordmark-dark.png", wordmark=True),
        square(PAPER, 380, "dooster-logo-wordmark-light.png", wordmark=True),
    ]
    for name in made:
        path = os.path.join(OUT, name)
        print(f"  {name}  {SIZE}x{SIZE}  {os.path.getsize(path) // 1024} KB")


if __name__ == "__main__":
    main()
