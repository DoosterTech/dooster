"""
Regenerate the logo files, favicon and social preview image in static/images/.

    python make_images.py

Run locally when the brand changes; the output is committed, so the Amplify
build never needs Pillow or fonts.
"""

import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "static", "images")

INK = (13, 15, 20)
PAPER = (246, 245, 240)
ACCENT = (255, 91, 46)
LIME = (214, 244, 91)
MUTED = (164, 167, 175)

# The mark, on a 40-unit grid: rounded square, ring, rising dot.
MARK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">
  <rect width="40" height="40" rx="11" fill="#0D0F14"/>
  <circle cx="17" cy="23" r="8.5" fill="none" stroke="#F6F5F0" stroke-width="4"/>
  <circle cx="29" cy="11" r="4.5" fill="#FF5B2E"/>
</svg>
"""

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 196 40" role="img" aria-label="Dooster">
  <rect width="40" height="40" rx="11" fill="#0D0F14"/>
  <circle cx="17" cy="23" r="8.5" fill="none" stroke="#F6F5F0" stroke-width="4"/>
  <circle cx="29" cy="11" r="4.5" fill="#FF5B2E"/>
  <text x="52" y="30" font-family="'Space Grotesk', 'Segoe UI', Arial, sans-serif" font-weight="700" font-size="30" letter-spacing="-1.3" fill="#0D0F14">dooster</text>
</svg>
"""


def font(size, bold=True):
    candidates = (["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"] if bold
                  else ["segoeui.ttf", "arial.ttf"])
    for name in candidates:
        for base in (r"C:\Windows\Fonts", "/usr/share/fonts/truetype/dejavu"):
            path = os.path.join(base, name)
            if os.path.isfile(path):
                return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_mark(img, x, y, size):
    """Draw the mark at 4x then downsample, for smooth edges."""
    s = 4
    big = Image.new("RGBA", (size * s, size * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    u = size * s / 40
    d.rounded_rectangle([0, 0, size * s - 1, size * s - 1], radius=11 * u, fill=INK)
    r, w = 8.5 * u, 4 * u
    d.ellipse([17 * u - r - w / 2, 23 * u - r - w / 2, 17 * u + r + w / 2, 23 * u + r + w / 2], fill=PAPER)
    d.ellipse([17 * u - r + w / 2, 23 * u - r + w / 2, 17 * u + r - w / 2, 23 * u + r - w / 2], fill=INK)
    d.ellipse([29 * u - 4.5 * u, 11 * u - 4.5 * u, 29 * u + 4.5 * u, 11 * u + 4.5 * u], fill=ACCENT)
    img.alpha_composite(big.resize((size, size), Image.LANCZOS), (x, y))


def og_image():
    W, H = 1200, 630
    img = Image.new("RGBA", (W, H), INK + (255,))
    d = ImageDraw.Draw(img)
    # faint grid
    for gx in range(0, W, 60):
        d.line([(gx, 0), (gx, H)], fill=(26, 29, 37), width=1)
    for gy in range(0, H, 60):
        d.line([(0, gy), (W, gy)], fill=(26, 29, 37), width=1)
    # accent glow
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i in range(40, 0, -1):
        a = int(5 * (40 - i) / 40 * 1.2)
        gd.ellipse([900 - i * 12, 520 - i * 12, 900 + i * 12, 520 + i * 12], fill=ACCENT + (a,))
    img.alpha_composite(glow)

    # logo tile on light
    mark_bg = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    ImageDraw.Draw(mark_bg).rounded_rectangle([0, 0, 95, 95], radius=26, fill=PAPER)
    img.alpha_composite(mark_bg, (80, 80))
    m = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    draw_mark(m, 0, 0, 96)
    img.alpha_composite(m, (80, 80))
    d.text((200, 88), "dooster", font=font(76), fill=(255, 255, 255))

    d.text((80, 270), "Websites, marketing & AI,", font=font(72), fill=(255, 255, 255))
    d.text((80, 360), "engineered for growth.", font=font(72), fill=ACCENT)
    d.text((80, 500), "Web development  ·  Digital marketing  ·  AI search  —  UK",
           font=font(30, bold=False), fill=MUTED)
    img.convert("RGB").save(os.path.join(OUT, "og-default.png"), optimize=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "favicon.svg"), "w", encoding="utf-8") as f:
        f.write(MARK_SVG)
    with open(os.path.join(OUT, "logo.svg"), "w", encoding="utf-8") as f:
        f.write(LOGO_SVG)

    for name, size, pad in (("apple-touch-icon.png", 180, 0), ("logo.png", 512, 0)):
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw_mark(img, pad, pad, size - pad * 2)
        img.save(os.path.join(OUT, name), optimize=True)

    og_image()
    print("Images written to static/images/")


if __name__ == "__main__":
    main()
